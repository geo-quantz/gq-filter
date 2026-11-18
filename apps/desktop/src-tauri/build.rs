// The commands the window may call. tauri-build generates an `allow-<name>`
// permission for each; capabilities/default.json grants them to the window.
const COMMANDS: &[&str] = &[
    "cli_status",
    "inspect_input",
    "prepare_output",
    "run_filter",
    "cancel_run",
];

fn main() {
    let attributes = tauri_build::Attributes::new()
        .app_manifest(tauri_build::AppManifest::new().commands(COMMANDS));
    tauri_build::try_build(attributes).expect("tauri-build failed");
}
