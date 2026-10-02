#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use rand::RngCore;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_shell::{process::CommandChild, ShellExt};
mod updates;

struct Sidecar(Arc<Mutex<Option<CommandChild>>>);

#[derive(Default)]
struct ExitState {
    closing: AtomicBool,
    prepared: AtomicBool,
}

fn show_exit_status(window: &tauri::WebviewWindow, message: &str, failed: bool) {
    // This fixed native overlay is independent of the current wizard page.
    // Text is serialized, never interpreted as HTML; no session key enters JS.
    let text = serde_json::to_string(message).unwrap_or_else(|_| "\"Closing…\"".into());
    let script = format!(
        r#"(() => {{
        let panel = document.getElementById('vcs-native-closing');
        if (!panel) {{
            panel = document.createElement('div'); panel.id = 'vcs-native-closing';
            panel.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:#090d19ee;display:flex;align-items:center;justify-content:center;padding:24px;color:#f4f5fa;font:16px system-ui';
            const box = document.createElement('div'); box.style.cssText = 'max-width:460px;background:#171d2e;border:1px solid #333b51;border-radius:18px;padding:28px';
            const message = document.createElement('p'); message.id = 'vcs-native-closing-message'; message.setAttribute('role','status'); box.append(message);
            const buttons = document.createElement('div'); buttons.id = 'vcs-native-closing-buttons'; buttons.style.cssText = 'display:flex;gap:12px;flex-wrap:wrap';
            for (const [label, command] of [['Try again','desktop_exit_retry'],['Return to app','desktop_exit_cancel']]) {{
                const button = document.createElement('button'); button.textContent = label;
                button.style.cssText = 'padding:10px 16px;border-radius:10px;border:1px solid #555e75;background:#232b40;color:inherit;cursor:pointer';
                button.onclick = async () => {{
                    button.disabled = true;
                    try {{ await window.__TAURI_INTERNALS__.invoke(command); }}
                    catch (error) {{ document.getElementById('vcs-native-closing-message').textContent = String(error); }}
                    finally {{ button.disabled = false; }}
                }};
                buttons.append(button);
            }}
            box.append(buttons); panel.append(box); document.body.append(panel);
        }}
        document.getElementById('vcs-native-closing-message').textContent = {text};
        document.getElementById('vcs-native-closing-buttons').style.display = {failed} ? 'flex' : 'none';
    }})();"#
    );
    let _ = window.eval(&script);
}

async fn lifecycle_request(
    app: &tauri::AppHandle,
    path: &str,
) -> Result<serde_json::Value, String> {
    let context = app.state::<BackendContext>().inner().clone();
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(90))
        .build()
        .map_err(|_| "Could not finish closing. Try again or return to the app.")?;
    let response = client
        .post(format!(
            "http://127.0.0.1:{}/api/desktop/lifecycle/{path}",
            context.port
        ))
        .header("X-API-Key", &context.key)
        .send()
        .await
        .map_err(|_| {
            "Could not reach the app service. Try again. Cloud stop is not yet confirmed."
        })?;
    let success = response.status().is_success();
    let value: serde_json::Value = response
        .json()
        .await
        .map_err(|_| "Could not confirm closing. Try again or return to the app.")?;
    if !success {
        return Err(value["detail"]
            .as_str()
            .unwrap_or(
                "Could not confirm the cloud machine stopped. Try again or return to the app.",
            )
            .into());
    }
    Ok(value)
}

fn exit_is_confirmed(value: &serde_json::Value) -> bool {
    value["prepared"] == true && value["stop_confirmed"] == true
}

fn begin_exit(app: tauri::AppHandle) {
    let state = app.state::<ExitState>();
    if state.closing.swap(true, Ordering::AcqRel) {
        return;
    }
    if let Some(window) = app.get_webview_window("main") {
        show_exit_status(&window, "Closing… Stopping cloud work.", false);
    }
    tauri::async_runtime::spawn(async move {
        let result = lifecycle_request(&app, "prepare-exit").await;
        let confirmed = result.as_ref().map(exit_is_confirmed).unwrap_or(false);
        if confirmed {
            app.state::<ExitState>()
                .prepared
                .store(true, Ordering::Release);
            // Cloud cleanup is acknowledged before the exact owned process
            // tree is stopped. Never force-close on an uncertain response.
            stop_owned_sidecar(&app);
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.close();
            }
        } else {
            app.state::<ExitState>()
                .closing
                .store(false, Ordering::Release);
            if let Some(window) = app.get_webview_window("main") {
                let message = result.err().unwrap_or_else(||
                    "Could not confirm the cloud machine stopped. Try again or return to the app.".into());
                show_exit_status(&window, &message, true);
            }
        }
    });
}

#[cfg(test)]
mod exit_tests {
    use super::exit_is_confirmed;
    use serde_json::json;

    #[test]
    fn close_requires_both_true_acknowledgements() {
        assert!(exit_is_confirmed(
            &json!({"prepared": true, "stop_confirmed": true})
        ));
        for response in [
            json!({"prepared": true}),
            json!({"stop_confirmed": true}),
            json!({"prepared": true, "stop_confirmed": false}),
            json!({"prepared": "true", "stop_confirmed": "true"}),
            json!({"prepared": false, "stop_confirmed": true}),
            json!(null),
        ] {
            assert!(!exit_is_confirmed(&response));
        }
    }
}

#[tauri::command]
fn desktop_exit_retry(app: tauri::AppHandle) {
    begin_exit(app);
}

#[tauri::command]
async fn desktop_exit_cancel(app: tauri::AppHandle) -> Result<(), String> {
    if app.state::<ExitState>().closing.load(Ordering::Acquire) {
        return Err("The cloud machine is still stopping. Please wait, then try again.".into());
    }
    lifecycle_request(&app, "cancel-exit").await?;
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.eval("document.getElementById('vcs-native-closing')?.remove();");
    }
    Ok(())
}

#[derive(Clone)]
struct BackendContext {
    port: u16,
    key: String,
    data_dir: std::path::PathBuf,
    process_path: std::ffi::OsString,
}

fn restore_owned_sidecar(app: &tauri::AppHandle) -> Result<(), String> {
    let sidecar = app.state::<Sidecar>();
    if sidecar
        .0
        .lock()
        .map_err(|_| "Local service state unavailable")?
        .is_some()
    {
        return Ok(());
    }
    let context = app.state::<BackendContext>();
    let (mut events, child) = app
        .shell()
        .sidecar("voice-clone-api")
        .map_err(|_| "Cannot locate local service")?
        .env("VCS_DESKTOP_PORT", context.port.to_string())
        .env("VCS_API_KEY", context.key.clone())
        .env(
            "VCS_DATA_DIR",
            context.data_dir.to_string_lossy().to_string(),
        )
        .env("PATH", context.process_path.to_string_lossy().to_string())
        .spawn()
        .map_err(|_| "Could not restore local service")?;
    tauri::async_runtime::spawn(async move { while events.recv().await.is_some() {} });
    if !wait_for_api(context.port) {
        stop_sidecar(child);
        return Err("Local service restart failed; close and reopen the app".into());
    }
    *sidecar
        .0
        .lock()
        .map_err(|_| "Local service state unavailable")? = Some(child);
    Ok(())
}

fn stop_owned_sidecar(app: &tauri::AppHandle) {
    if let Some(sidecar) = app.try_state::<Sidecar>() {
        if let Ok(mut child) = sidecar.0.lock() {
            if let Some(child) = child.take() {
                stop_sidecar(child);
            }
        }
    }
}

fn stop_sidecar(child: CommandChild) {
    // PyInstaller's onefile bootloader starts another process. Killing only the
    // bootloader leaves its API child and pipe readers alive on Windows.
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let taskkill = std::path::PathBuf::from(
            std::env::var_os("SystemRoot").unwrap_or_else(|| "C:\\Windows".into()),
        )
        .join("System32\\taskkill.exe");
        let _ = std::process::Command::new(taskkill)
            .args(["/PID", &child.pid().to_string(), "/T", "/F"])
            .creation_flags(0x0800_0000)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
    }
    let _ = child.kill();
}

fn free_port() -> Result<u16, Box<dyn std::error::Error>> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    Ok(listener.local_addr()?.port())
}

fn wait_for_api(port: u16) -> bool {
    let deadline = Instant::now() + Duration::from_secs(60);
    while Instant::now() < deadline {
        if let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) {
            let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
            let _ = stream.write_all(
                b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n",
            );
            let mut response = [0u8; 64];
            if let Ok(count) = stream.read(&mut response) {
                if response[..count].starts_with(b"HTTP/1.1 200") {
                    return true;
                }
            }
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    false
}

fn main() {
    tauri::Builder::default()
        // A second process must not replace the first process's MCP session.
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                if std::env::var_os("VCS_DESKTOP_TEST_HIDE").is_none() {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_notification::init())
        .manage(updates::UpdateState::default())
        .manage(ExitState::default())
        .invoke_handler(tauri::generate_handler![
            updates::updater_check,
            updates::updater_download,
            updates::updater_cancel_download,
            updates::updater_install,
            updates::desktop_version,
            updates::desktop_notify,
            desktop_exit_retry,
            desktop_exit_cancel
        ])
        .setup(|app| {
            let port = free_port()?;
            let mut key = [0u8; 32];
            rand::rngs::OsRng.fill_bytes(&mut key);
            let session_key: String = key.iter().map(|byte| format!("{byte:02x}")).collect();
            let data_dir = match std::env::var_os("VCS_DESKTOP_DATA_DIR") {
                Some(value) => {
                    let path = std::path::PathBuf::from(value);
                    if !path.is_absolute() {
                        return Err("VCS_DESKTOP_DATA_DIR must be an absolute path".into());
                    }
                    path
                }
                None => app.path().app_data_dir()?,
            };
            std::fs::create_dir_all(&data_dir)?;
            let executable = std::env::current_exe()?;
            let install_dir = executable
                .parent()
                .ok_or("Cannot locate install directory")?;
            let mut search_paths = vec![
                data_dir.join("tools").join("ffmpeg"),
                install_dir.to_path_buf(),
            ];
            if let Some(existing) = std::env::var_os("PATH") {
                search_paths.extend(std::env::split_paths(&existing));
            }
            let process_path = std::env::join_paths(search_paths)?;
            let url = format!("http://127.0.0.1:{port}/").parse()?;
            // IPC is granted only to this session's exact loopback origin/window.
            // No updater command accepts a URL, path, key or signature from JS.
            app.add_capability(format!(
                r#"{{"identifier":"desktop-update-session",
                "windows":["main"],"local":false,
                "remote":{{"urls":["http://127.0.0.1:{port}"]}},
                "permissions":["allow-updater-check","allow-updater-download","allow-updater-cancel-download",
                    "allow-updater-install","allow-desktop-version","allow-desktop-notify",
                    "allow-desktop-exit-retry","allow-desktop-exit-cancel"]}}"#
            ))?;
            let (mut events, child) = app
                .shell()
                .sidecar("voice-clone-api")?
                .env("VCS_DESKTOP_PORT", port.to_string())
                .env("VCS_API_KEY", session_key.clone())
                .env("VCS_DATA_DIR", data_dir.to_string_lossy().to_string())
                .env("PATH", process_path.to_string_lossy().to_string())
                .spawn()?;
            // Drain stdout/stderr so pipe backpressure cannot stall the API.
            // Do not copy potentially sensitive diagnostics into UI logs.
            tauri::async_runtime::spawn(async move { while events.recv().await.is_some() {} });
            if !wait_for_api(port) {
                stop_sidecar(child);
                return Err("The local Voice Clone Studio service did not start".into());
            }
            app.manage(BackendContext {
                port,
                key: session_key,
                data_dir: data_dir.clone(),
                process_path,
            });
            if let Err(error) = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
                .title("AI Voice Clone Studio")
                .data_directory(data_dir.join("webview"))
                .visible(std::env::var_os("VCS_DESKTOP_TEST_HIDE").is_none())
                .inner_size(1200.0, 800.0)
                .build()
            {
                stop_sidecar(child);
                return Err(error.into());
            }
            app.manage(Sidecar(Arc::new(Mutex::new(Some(child)))));
            Ok(())
        })
        .on_window_event(|window, event| {
            match event {
                tauri::WindowEvent::CloseRequested { api, .. } => {
                    if !window.app_handle().state::<ExitState>().prepared.load(Ordering::Acquire) {
                        api.prevent_close();
                        begin_exit(window.app_handle().clone());
                    }
                }
                tauri::WindowEvent::Destroyed => {
                    if window.app_handle().state::<ExitState>().prepared.load(Ordering::Acquire) {
                        stop_owned_sidecar(window.app_handle());
                    }
                }
                _ => {}
            }
        })
        .run(tauri::generate_context!())
        .expect("failed to run desktop app");
}
