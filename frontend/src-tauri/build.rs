fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "updater_check",
            "updater_download",
            "updater_cancel_download",
            "updater_install",
            "desktop_version",
            "desktop_notify",
            "desktop_exit_retry",
            "desktop_exit_cancel",
        ]),
    ))
    .expect("desktop permission generation failed")
}
