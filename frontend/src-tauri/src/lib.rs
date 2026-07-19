use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandEvent;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  tauri::Builder::default()
    .plugin(tauri_plugin_shell::init())
    .setup(|app| {
      if cfg!(debug_assertions) {
        app.handle().plugin(
          tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build(),
        )?;
      }

      // Start the Python backend sidecar
      let shell = app.handle().shell();
      let (mut rx, _child) = shell.sidecar("backend")
        .expect("failed to setup `backend` sidecar")
        .spawn()
        .expect("Failed to spawn sidecar");

      tauri::async_runtime::spawn(async move {
        // Log stdout from backend for debugging
        while let Some(event) = rx.recv().await {
          if let CommandEvent::Stdout(line) = event {
            log::info!("backend: {}", String::from_utf8_lossy(&line));
          }
        }
      });

      Ok(())
    })
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}
