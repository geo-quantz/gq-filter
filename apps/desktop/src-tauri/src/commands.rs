//! The commands the window calls. Checks live in `run_args`, `locate` and
//! `point_count`; this file only starts gqfilter and passes its lines on.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, State};
use tokio::sync::oneshot;

use crate::locate::{self, LocateInput, RealFs, Source};
use crate::point_count::{self, HeaderInfo};
use crate::run_args;
use crate::runner::{run_cli, LineSink, LogLine, RunResult};

/// One line of gqfilter output, sent to the window while it runs.
pub const LOG_EVENT: &str = "filter-log";

/// At most one run at a time; holds the way to stop it.
#[derive(Default)]
pub struct RunState {
    running: AtomicBool,
    cancel: Mutex<Option<oneshot::Sender<()>>>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CliStatus {
    pub found: bool,
    pub path: Option<String>,
    pub source: Option<&'static str>,
    pub message: Option<String>,
}

fn non_empty(setting: Option<String>) -> Option<PathBuf> {
    setting
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
}

fn find_cli(setting: Option<String>) -> Result<PathBuf, String> {
    let input = LocateInput::from_env(non_empty(setting));
    locate::locate(&input, &RealFs)
        .map(|found| found.path)
        .map_err(|e| e.message())
}

#[tauri::command]
pub fn cli_status(setting: Option<String>) -> CliStatus {
    let input = LocateInput::from_env(non_empty(setting));
    match locate::locate(&input, &RealFs) {
        Ok(found) => CliStatus {
            found: true,
            path: Some(found.path.display().to_string()),
            source: Some(match found.source {
                Source::Setting => "setting",
                Source::Env => "env",
                Source::Path => "path",
                Source::InstallDir => "install",
            }),
            message: None,
        },
        Err(e) => CliStatus {
            found: false,
            path: None,
            source: None,
            message: Some(e.message()),
        },
    }
}

#[tauri::command]
pub fn inspect_input(path: String) -> Result<HeaderInfo, String> {
    let path = PathBuf::from(path);
    run_args::check_input(&path)?;
    point_count::read_header(&path).map_err(|e| e.message())
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PlannedOutput {
    pub path: String,
    pub exists: bool,
}

#[tauri::command]
pub fn prepare_output(
    input: String,
    folder: String,
    format: String,
) -> Result<PlannedOutput, String> {
    let input = PathBuf::from(input);
    let output = run_args::output_path(&input, Path::new(&folder), &format)?;
    run_args::check_output(&input, &output)?;
    Ok(PlannedOutput {
        exists: output.exists(),
        path: output.display().to_string(),
    })
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RunRequest {
    pub cli_setting: Option<String>,
    pub input: String,
    pub output: String,
    pub filter_args: Vec<String>,
    pub overwrite: bool,
}

/// Clears the "running" mark when the run ends, however it ends.
struct RunGuard<'a>(&'a RunState);

impl Drop for RunGuard<'_> {
    fn drop(&mut self) {
        if let Ok(mut slot) = self.0.cancel.lock() {
            slot.take();
        }
        self.0.running.store(false, Ordering::SeqCst);
    }
}

fn start_guard(state: &RunState) -> Result<(RunGuard<'_>, oneshot::Receiver<()>), String> {
    if state
        .running
        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .is_err()
    {
        return Err("いま実行中です。終わるまで待つか、中止してください。".to_string());
    }
    let guard = RunGuard(state);
    let (tx, rx) = oneshot::channel();
    let mut slot = state
        .cancel
        .lock()
        .map_err(|_| "内部の状態を読めません。アプリを開き直してください。".to_string())?;
    *slot = Some(tx);
    Ok((guard, rx))
}

/// Asks the running gqfilter to stop. False when nothing is running.
fn request_cancel(state: &RunState) -> bool {
    let sender = state.cancel.lock().ok().and_then(|mut slot| slot.take());
    sender.is_some_and(|tx| tx.send(()).is_ok())
}

#[tauri::command]
pub async fn run_filter(
    app: AppHandle,
    state: State<'_, RunState>,
    request: RunRequest,
) -> Result<RunResult, String> {
    let input = PathBuf::from(&request.input);
    let output = PathBuf::from(&request.output);
    run_args::check_input(&input)?;
    run_args::check_output(&input, &output)?;
    run_args::validate_filter_args(&request.filter_args)?;
    if output.exists() && !request.overwrite {
        return Err(format!("出力ファイルがもうあります: {}", output.display()));
    }
    let cli = find_cli(request.cli_setting.clone())?;
    let (_guard, cancel) = start_guard(&state)?;

    let argv = run_args::build_argv(&input, &output, &request.filter_args);
    let on_line: LineSink = Arc::new(move |line: LogLine| {
        let _ = app.emit(LOG_EVENT, line);
    });
    run_cli(&cli, &argv, cancel, on_line).await
}

#[tauri::command]
pub fn cancel_run(state: State<'_, RunState>) -> bool {
    request_cancel(&state)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_run_at_a_time() {
        let state = RunState::default();
        let first = start_guard(&state).unwrap();
        assert!(start_guard(&state).is_err());
        drop(first);
        assert!(start_guard(&state).is_ok());
    }

    #[test]
    fn cancel_reaches_the_run_and_does_not_free_the_slot() {
        let state = RunState::default();
        assert!(!request_cancel(&state), "nothing to cancel");
        let (guard, mut rx) = start_guard(&state).unwrap();
        assert!(request_cancel(&state));
        assert!(rx.try_recv().is_ok());
        assert!(
            start_guard(&state).is_err(),
            "still running until the guard drops"
        );
        drop(guard);
        assert!(!request_cancel(&state));
    }

    #[test]
    fn setting_is_trimmed() {
        assert_eq!(non_empty(Some("  ".into())), None);
        assert_eq!(
            non_empty(Some(" /a/gqfilter ".into())),
            Some(PathBuf::from("/a/gqfilter"))
        );
        assert_eq!(non_empty(None), None);
    }
}
