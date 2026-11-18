//! Starts gqfilter, passes each output line on, and reads the result.
//! No Tauri here, so it can be run against a real gqfilter in a test.

use std::collections::VecDeque;
use std::ffi::OsString;
use std::path::Path;
use std::process::Stdio;
use std::sync::{Arc, Mutex};

use serde::Serialize;
use tokio::io::{AsyncBufReadExt, AsyncRead, BufReader};
use tokio::process::Command;
use tokio::sync::oneshot;

use crate::run_args;

/// Keeps the last lines of each stream for the result (the window gets them all).
const KEEP_LINES: usize = 2000;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RunResult {
    pub success: bool,
    pub cancelled: bool,
    pub exit_code: Option<i32>,
    pub output_points: Option<u64>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LogLine {
    pub stream: &'static str,
    pub line: String,
}

pub type LineSink = Arc<dyn Fn(LogLine) + Send + Sync>;
type Kept = Arc<Mutex<VecDeque<String>>>;

/// Runs `cli argv...` to the end, or until `cancel` fires.
pub async fn run_cli(
    cli: &Path,
    argv: &[OsString],
    cancel: oneshot::Receiver<()>,
    on_line: LineSink,
) -> Result<RunResult, String> {
    let mut command = Command::new(cli);
    command
        .args(argv)
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    #[cfg(windows)]
    {
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = command
        .spawn()
        .map_err(|e| format!("gqfilter を起動できません（{}）: {e}", cli.display()))?;

    let stdout = child.stdout.take().ok_or("gqfilter の出力を読めません。")?;
    let stderr = child.stderr.take().ok_or("gqfilter の出力を読めません。")?;
    let out_lines: Kept = Arc::default();
    let err_lines: Kept = Arc::default();
    let out_task = tokio::spawn(forward(
        stdout,
        "stdout",
        on_line.clone(),
        out_lines.clone(),
    ));
    let err_task = tokio::spawn(forward(stderr, "stderr", on_line, err_lines.clone()));

    let (status, cancelled) = tokio::select! {
        status = child.wait() => (status.map_err(|e| e.to_string())?, false),
        _ = cancel => {
            let _ = child.kill().await;
            (child.wait().await.map_err(|e| e.to_string())?, true)
        }
    };
    let _ = out_task.await;
    let _ = err_task.await;

    let out = snapshot(&out_lines);
    let err = snapshot(&err_lines);
    Ok(result_of(
        status.success(),
        status.code(),
        cancelled,
        &out,
        &err,
    ))
}

fn snapshot(kept: &Kept) -> Vec<String> {
    kept.lock()
        .map(|v| v.iter().cloned().collect())
        .unwrap_or_default()
}

/// The result from the exit status and the lines gqfilter printed.
pub fn result_of(
    exited_ok: bool,
    exit_code: Option<i32>,
    cancelled: bool,
    stdout: &[String],
    stderr: &[String],
) -> RunResult {
    let success = exited_ok && !cancelled;
    let error = if success {
        None
    } else if cancelled {
        Some("中止しました。途中までの出力ファイルが残っていることがあります。".to_string())
    } else {
        Some(run_args::failure_message(stderr, exit_code))
    };
    RunResult {
        success,
        cancelled,
        exit_code,
        output_points: success
            .then(|| {
                stdout
                    .iter()
                    .rev()
                    .find_map(|l| run_args::parse_points_processed(l))
            })
            .flatten(),
        error,
    }
}

/// Passes each line on and keeps the last ones.
async fn forward<R: AsyncRead + Unpin>(
    reader: R,
    stream: &'static str,
    on_line: LineSink,
    keep: Kept,
) {
    let mut reader = BufReader::new(reader);
    let mut buf = Vec::new();
    loop {
        buf.clear();
        match reader.read_until(b'\n', &mut buf).await {
            Ok(0) | Err(_) => break,
            Ok(_) => {
                let line = String::from_utf8_lossy(&buf)
                    .trim_end_matches(['\r', '\n'])
                    .to_string();
                on_line(LogLine {
                    stream,
                    line: line.clone(),
                });
                if let Ok(mut lines) = keep.lock() {
                    if lines.len() >= KEEP_LINES {
                        lines.pop_front();
                    }
                    lines.push_back(line);
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn s(v: &[&str]) -> Vec<String> {
        v.iter().map(|x| x.to_string()).collect()
    }

    #[test]
    fn result_reads_points_only_on_success() {
        let out = s(&["Executing pipeline: a -> b", "Points processed: 42"]);
        let ok = result_of(true, Some(0), false, &out, &[]);
        assert!(ok.success);
        assert_eq!(ok.output_points, Some(42));
        assert_eq!(ok.error, None);

        let failed = result_of(false, Some(1), false, &out, &s(&["Error: boom"]));
        assert!(!failed.success);
        assert_eq!(failed.output_points, None);
        assert_eq!(failed.error.as_deref(), Some("Error: boom"));

        let cancelled = result_of(false, None, true, &out, &[]);
        assert!(cancelled.cancelled);
        assert!(cancelled.error.unwrap().contains("中止"));
    }

    fn runtime() -> tokio::runtime::Runtime {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
    }

    fn collect() -> (LineSink, Arc<Mutex<Vec<LogLine>>>) {
        let seen: Arc<Mutex<Vec<LogLine>>> = Arc::default();
        let sink_seen = seen.clone();
        let sink: LineSink = Arc::new(move |l| sink_seen.lock().unwrap().push(l));
        (sink, seen)
    }

    #[cfg(unix)]
    #[test]
    fn streams_lines_and_exit_code_of_a_real_process() {
        let dir = tempfile::tempdir().unwrap();
        let script = dir.path().join("fake-gqfilter");
        std::fs::write(
            &script,
            "#!/bin/sh\necho \"Executing pipeline: $1\"\necho 'Points processed: 5'\necho 'Error: nope' >&2\nexit ${FAKE_EXIT:-0}\n",
        )
        .unwrap();
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755)).unwrap();

        let (sink, seen) = collect();
        let (_tx, rx) = oneshot::channel();
        let argv = vec![OsString::from("--input=/x.las")];
        let result = runtime()
            .block_on(run_cli(&script, &argv, rx, sink))
            .unwrap();
        assert!(result.success);
        assert_eq!(result.output_points, Some(5));
        let lines = seen.lock().unwrap();
        assert!(lines
            .iter()
            .any(|l| l.stream == "stdout" && l.line == "Executing pipeline: --input=/x.las"));
        assert!(lines
            .iter()
            .any(|l| l.stream == "stderr" && l.line == "Error: nope"));
    }

    #[cfg(unix)]
    #[test]
    fn cancel_stops_the_process() {
        let dir = tempfile::tempdir().unwrap();
        let script = dir.path().join("slow");
        std::fs::write(&script, "#!/bin/sh\necho started\nexec sleep 30\n").unwrap();
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755)).unwrap();

        let (sink, _) = collect();
        let (tx, rx) = oneshot::channel();
        let result = runtime().block_on(async {
            let run = run_cli(&script, &[], rx, sink);
            let stop = async {
                tokio::time::sleep(std::time::Duration::from_millis(200)).await;
                let _ = tx.send(());
            };
            let (result, ()) = tokio::join!(run, stop);
            result
        });
        let result = result.unwrap();
        assert!(result.cancelled);
        assert!(!result.success);
    }

    #[test]
    fn missing_program_is_a_clear_error() {
        let (sink, _) = collect();
        let (_tx, rx) = oneshot::channel();
        let err = runtime()
            .block_on(run_cli(Path::new("/no/such/gqfilter"), &[], rx, sink))
            .unwrap_err();
        assert!(err.contains("起動できません"));
    }

    /// End to end with the released gqfilter on the repository fixture.
    /// Runs only when GQFILTER_PATH points at a gqfilter executable.
    #[test]
    fn real_gqfilter_on_the_fixture() {
        let Some(cli) = std::env::var_os("GQFILTER_PATH").map(PathBuf::from) else {
            eprintln!("GQFILTER_PATH not set, skipped");
            return;
        };
        let fixture =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../tests/fixtures/autzen-small.laz");
        let dir = tempfile::tempdir().unwrap();
        let output = run_args::output_path(&fixture, dir.path(), "laz").unwrap();
        let filter_args = s(&[
            "--outlier-statistical",
            "--outlier-mean-k",
            "8",
            "--outlier-multiplier",
            "2",
        ]);
        run_args::validate_filter_args(&filter_args).unwrap();
        let argv = run_args::build_argv(&fixture, &output, &filter_args);

        let (sink, seen) = collect();
        let (_tx, rx) = oneshot::channel();
        let result = runtime().block_on(run_cli(&cli, &argv, rx, sink)).unwrap();
        for l in seen.lock().unwrap().iter() {
            eprintln!("[{}] {}", l.stream, l.line);
        }
        assert!(result.success, "{:?}", result.error);
        assert_eq!(result.output_points, Some(7262));
        let written = crate::point_count::read_header(&output).unwrap();
        assert_eq!(written.points, 7262);
        assert!(written.format.starts_with("LAZ"));
    }
}
