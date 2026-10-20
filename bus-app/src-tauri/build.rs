fn main() {
    // The shell's IPC commands get generated permissions (allow-<command>) so the capability can
    // grant them to the console's local origin by name, and nothing else.
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "shell_info",
            "set_bus_config",
            "test_bus_connection",
            "pick_pack_folder",
            "update_check",
            "update_install",
            "update_restart",
        ]),
    ))
    .expect("failed to run tauri-build");
}
