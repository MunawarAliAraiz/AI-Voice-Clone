use futures_util::future::{AbortHandle, Abortable};
use serde::Serialize;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{ipc::Channel, Manager, State};
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_updater::{Update, UpdaterExt};
#[path = "update_cache.rs"]
mod cache;

#[derive(Default)]
pub struct UpdateState {
    pending: tauri::async_runtime::Mutex<PendingUpdate>,
    downloading: AtomicBool,
    cancel: AtomicBool,
    abort: Mutex<Option<AbortHandle>>,
}

#[derive(Default)]
struct PendingUpdate {
    update: Option<Update>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateInfo {
    pub version: String,
    pub notes: Option<String>,
    #[serde(flatten)]
    pub cache: cache::CacheStatus,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DownloadProgress {
    pub downloaded: u64,
    pub total: Option<u64>,
}

#[tauri::command]
pub fn desktop_version(app: tauri::AppHandle) -> String {
    app.package_info().version.to_string()
}

#[tauri::command]
pub async fn updater_check(
    app: tauri::AppHandle,
    state: State<'_, UpdateState>,
) -> Result<Option<UpdateInfo>, String> {
    let mut pending = state.pending.lock().await;
    if state.downloading.load(Ordering::Acquire) {
        return Err("Wait for the update download to pause or finish before checking again".into());
    }
    let exit_handle = app.clone();
    let updater = app
        .updater_builder()
        .timeout(Duration::from_secs(30))
        .on_before_exit(move || {
            super::stop_owned_sidecar(&exit_handle);
            // Keep the window/runtime alive until ShellExecute succeeds. If
            // launch fails, updater_install restores the API and admission.
        })
        .build()
        .map_err(|_| "App update configuration is unavailable".to_string())?;
    let mut update = updater.check().await.map_err(|_| {
        "Cannot check app updates. The release feed may not be published yet, or the connection failed. Try again later.".to_string()
    })?;
    if let Some(value) = update.as_mut() {
        value.timeout = Some(Duration::from_secs(900));
    }
    let root = app
        .state::<super::BackendContext>()
        .data_dir
        .join("update-cache");
    let info = update.as_ref().map(|u| UpdateInfo {
        version: u.version.clone(),
        notes: u.body.clone(),
        cache: cache::status(&root, u),
    });
    *pending = PendingUpdate { update };
    Ok(info)
}

#[tauri::command]
pub async fn updater_download(
    app: tauri::AppHandle,
    state: State<'_, UpdateState>,
    progress: Channel<DownloadProgress>,
) -> Result<cache::CacheStatus, String> {
    // Serialize admission with updater_check without holding the lock across
    // network reads: cancel is a separate command and must remain callable.
    let pending = state.pending.lock().await;
    let update = pending
        .update
        .as_ref()
        .ok_or("Check for an app update first")?
        .clone();
    if state.downloading.swap(true, Ordering::AcqRel) {
        return Err("An update download is already running".into());
    }
    state.cancel.store(false, Ordering::Release);
    struct DownloadGuard<'a>(&'a AtomicBool, &'a Mutex<Option<AbortHandle>>);
    impl Drop for DownloadGuard<'_> {
        fn drop(&mut self) {
            if let Ok(mut abort) = self.1.lock() {
                abort.take();
            }
            self.0.store(false, Ordering::Release);
        }
    }
    let _guard = DownloadGuard(&state.downloading, &state.abort);
    let (abort, registration) = AbortHandle::new_pair();
    if state.cancel.load(Ordering::Acquire) {
        abort.abort();
    }
    *state
        .abort
        .lock()
        .map_err(|_| "Update cancellation state is unavailable")? = Some(abort);
    // Cancel may race admission before the handle is registered.
    if state.cancel.load(Ordering::Acquire) {
        if let Ok(abort) = state.abort.lock() {
            if let Some(abort) = abort.as_ref() {
                abort.abort();
            }
        }
    }
    drop(pending);
    let root = app
        .state::<super::BackendContext>()
        .data_dir
        .join("update-cache");
    let result = Abortable::new(
        cache::download(root.clone(), update.clone(), &state.cancel, progress),
        registration,
    )
    .await;
    if state.cancel.load(Ordering::Acquire) {
        return Ok(cache::status(&root, &update));
    }
    result.map_err(|_| "Update download was stopped unexpectedly".to_string())?
}

#[tauri::command]
pub fn updater_cancel_download(state: State<'_, UpdateState>) -> bool {
    let running = state.downloading.load(Ordering::Acquire);
    if running {
        state.cancel.store(true, Ordering::Release);
        if let Ok(abort) = state.abort.lock() {
            if let Some(abort) = abort.as_ref() {
                abort.abort();
            }
        }
    }
    running
}

#[tauri::command]
pub async fn updater_install(
    app: tauri::AppHandle,
    state: State<'_, UpdateState>,
) -> Result<(), String> {
    let pending = state.pending.lock().await;
    if state.downloading.load(Ordering::Acquire) {
        return Err("Pause or finish the update download before installing".into());
    }
    let update = pending
        .update
        .as_ref()
        .ok_or("Check for an app update first")?
        .clone();
    let context = app.state::<super::BackendContext>().inner().clone();
    // Reverify cached bytes, including the signed version, immediately before
    // use. A persisted completion flag alone never grants installation.
    let bytes = cache::checked_bytes(&context.data_dir.join("update-cache"), &update)?;
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(60))
        .build()
        .map_err(|_| "Could not prepare update")?;
    let base = format!("http://127.0.0.1:{}/api/desktop/updates", context.port);
    let response = client
        .post(format!("{base}/prepare"))
        .header("X-API-Key", &context.key)
        .send()
        .await
        .map_err(|_| "Could not verify that the app is idle; try again")?;
    if !response.status().is_success() {
        return Err(response
            .json::<serde_json::Value>()
            .await
            .ok()
            .and_then(|value| value["detail"].as_str().map(str::to_string))
            .unwrap_or_else(|| "Finish current work before restarting".into()));
    }
    // Windows starts the per-user NSIS updater, exits this process, and restarts
    // the updated app. on_before_exit stops the owned onefile API process tree.
    let result = tauri::async_runtime::spawn_blocking(move || update.install(bytes)).await;
    if !matches!(result, Ok(Ok(()))) {
        let restore = app.clone();
        tauri::async_runtime::spawn_blocking(move || super::restore_owned_sidecar(&restore))
            .await
            .map_err(|_| "Could not restore the app; close and reopen it")??;
        let _ = client
            .post(format!("{base}/cancel"))
            .header("X-API-Key", &context.key)
            .send()
            .await;
        return Err(
            "The installer could not start. The app has been restored; you can retry.".into(),
        );
    }
    Ok(())
}

#[tauri::command]
pub fn desktop_notify(app: tauri::AppHandle, title: String, body: String) -> Result<(), String> {
    app.notification()
        .builder()
        .title(title.chars().take(80).collect::<String>())
        .body(body.chars().take(240).collect::<String>())
        .show()
        .map_err(|_| "Windows notifications are unavailable".into())
}
