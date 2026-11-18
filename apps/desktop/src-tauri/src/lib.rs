//! gq-filter desktop app (Tauri 2). The window picks files and options; the
//! filtering itself is done by the gqfilter CLI, started as a child process.
pub mod commands;
pub mod locate;
pub mod point_count;
pub mod run_args;
pub mod runner;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(commands::RunState::default())
        .invoke_handler(tauri::generate_handler![
            commands::cli_status,
            commands::inspect_input,
            commands::prepare_output,
            commands::run_filter,
            commands::cancel_run,
        ])
        .run(tauri::generate_context!())
        .expect("error while running the gq-filter app");
}
