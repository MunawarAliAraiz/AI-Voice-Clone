use rand::RngCore;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_shell::{process::CommandChild, ShellExt};

struct Sidecar(Arc<Mutex<Option<CommandChild>>>);

fn free_port() -> Result<u16, Box<dyn std::error::Error>> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    Ok(listener.local_addr()?.port())
}

fn wait_for_api(port: u16) -> bool {
    let deadline = Instant::now() + Duration::from_secs(60);
    while Instant::now() < deadline {
        if let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) {
            let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
            let _ = stream.write_all(b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n");
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
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let port = free_port()?;
            let mut key = [0u8; 32];
            rand::rngs::OsRng.fill_bytes(&mut key);
            let session_key: String = key.iter().map(|byte| format!("{byte:02x}")).collect();
            let data_dir = app.path().app_data_dir()?;
            std::fs::create_dir_all(&data_dir)?;
            let (_, child) = app
                .shell()
                .sidecar("voice-clone-api")?
                .env("VCS_DESKTOP_PORT", port.to_string())
                .env("VCS_API_KEY", session_key)
                .env("VCS_DATA_DIR", data_dir.to_string_lossy().to_string())
                .spawn()?;
            app.manage(Sidecar(Arc::new(Mutex::new(Some(child)))));
            if !wait_for_api(port) {
                return Err("The local Voice Clone Studio service did not start".into());
            }
            let url = format!("http://127.0.0.1:{port}/").parse()?;
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
                .title("AI Voice Clone Studio")
                .inner_size(1200.0, 800.0)
                .build()?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(sidecar) = window.app_handle().try_state::<Sidecar>() {
                    if let Ok(mut child) = sidecar.0.lock() {
                        if let Some(child) = child.take() {
                            let _ = child.kill();
                        }
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("failed to run desktop app");
}
