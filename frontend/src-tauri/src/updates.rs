use serde::Serialize;
use std::time::Duration;
use tauri::{ipc::Channel, Manager, State};
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_updater::{Update, UpdaterExt};

#[derive(Default)]
pub struct UpdateState(tauri::async_runtime::Mutex<PendingUpdate>);

#[derive(Default)]
struct PendingUpdate {
    update: Option<Update>,
    bytes: Option<Vec<u8>>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateInfo {
    pub version: String,
    pub notes: Option<String>,
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
    let mut pending = state.0.lock().await;
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
    let info = update.as_ref().map(|u| UpdateInfo {
        version: u.version.clone(),
        notes: u.body.clone(),
    });
    *pending = PendingUpdate {
        update,
        bytes: None,
    };
    Ok(info)
}

#[tauri::command]
pub async fn updater_download(
    state: State<'_, UpdateState>,
    progress: Channel<DownloadProgress>,
) -> Result<(), String> {
    let mut pending = state.0.lock().await;
    pending.bytes = None;
    let update = pending
        .update
        .as_ref()
        .ok_or("Check for an app update first")?;
    let mut downloaded = 0u64;
    // The plugin verifies the mandatory embedded-key signature before returning bytes.
    let bytes = update.download(|length, total| {
        downloaded += length as u64;
        let _ = progress.send(DownloadProgress { downloaded, total });
    }, || {}).await.map_err(|_| {
        "The update could not be downloaded or its signature could not be verified. The app has not changed.".to_string()
    })?;
    pending.bytes = Some(bytes);
    Ok(())
}

#[tauri::command]
pub async fn updater_install(
    app: tauri::AppHandle,
    state: State<'_, UpdateState>,
) -> Result<(), String> {
    let pending = state.0.lock().await;
    let bytes = pending
        .bytes
        .as_ref()
        .ok_or("Download and verify the update first")?
        .clone();
    let update = pending
        .update
        .as_ref()
        .ok_or("Check for an app update first")?
        .clone();
    let context = app.state::<super::BackendContext>().inner().clone();
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
