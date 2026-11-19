//! The gqfilter command line and what it prints. Pure logic, no process here.
//!
//! The window builds the filter arguments (src/args/build.ts); this side checks
//! them again against the same flag list before anything is started, adds the
//! input and output, and reads the result lines that cli.py prints.

use std::ffi::OsString;
use std::path::{Path, PathBuf};

/// Same list as `CLI_FLAGS` in src/args/build.ts (and the options of cli.py).
pub const ALLOWED_FLAGS: &[&str] = &[
    "--incidence-angle-max",
    "--intensity-min",
    "--intensity-max",
    "--range-min",
    "--range-max",
    "--deduplicate",
    "--outlier-statistical",
    "--outlier-mean-k",
    "--outlier-multiplier",
    "--outlier-radius",
    "--outlier-radius-value",
    "--outlier-min-k",
    "--keep-classes",
    "--keep-returns",
    "--clip-xmin",
    "--clip-xmax",
    "--clip-ymin",
    "--clip-ymax",
    "--clip-zmin",
    "--clip-zmax",
    "--z-min",
    "--z-max",
    "--sample-radius",
    "--ground-classify",
    "--ground-cell",
    "--ground-window",
    "--ground-threshold",
    "--ground-only",
    "--ground-keep-classes",
];

pub const GROUND_METHODS: &[&str] = &["smrf", "pmf", "csf"];
pub const INPUT_EXTENSIONS: &[&str] = &["las", "laz", "e57"];
pub const OUTPUT_FORMATS: &[&str] = &["las", "laz"];
const OUTPUT_SUFFIX: &str = "_filtered";
const POINTS_LINE: &str = "Points processed:";

/// Checks the filter arguments: known flags, finite numbers, ground method names.
pub fn validate_filter_args(args: &[String]) -> Result<(), String> {
    if args.is_empty() {
        return Err("フィルターを 1 つ以上選んでください。".to_string());
    }
    let mut previous: Option<&str> = None;
    for token in args {
        let ok = if token.starts_with("--") {
            ALLOWED_FLAGS.contains(&token.as_str())
        } else if previous == Some("--ground-classify") {
            GROUND_METHODS.contains(&token.as_str())
        } else {
            previous.is_some() && token.parse::<f64>().is_ok_and(f64::is_finite)
        };
        if !ok {
            return Err(format!("使えない引数です: {token}"));
        }
        previous = Some(token.as_str());
    }
    Ok(())
}

fn extension_of(path: &Path) -> Option<String> {
    path.extension()
        .and_then(|e| e.to_str())
        .map(|e| e.to_ascii_lowercase())
}

/// The input must be an existing LAS / LAZ / E57 file.
pub fn check_input(path: &Path) -> Result<(), String> {
    let ext = extension_of(path).unwrap_or_default();
    if !INPUT_EXTENSIONS.contains(&ext.as_str()) {
        return Err("入力は .las / .laz / .e57 のファイルを選んでください。".to_string());
    }
    if !path.is_file() {
        return Err(format!("入力ファイルがありません: {}", path.display()));
    }
    Ok(())
}

/// `<folder>/<input name>_filtered.<format>`.
pub fn output_path(input: &Path, folder: &Path, format: &str) -> Result<PathBuf, String> {
    if !OUTPUT_FORMATS.contains(&format) {
        return Err(format!("出力形式は las か laz です（{format}）。"));
    }
    let stem = input
        .file_stem()
        .ok_or_else(|| "入力ファイルの名前を読めません。".to_string())?;
    let mut name = OsString::from(stem);
    name.push(OUTPUT_SUFFIX);
    name.push(".");
    name.push(format);
    Ok(folder.join(name))
}

/// The output folder must exist and the output must not be the input itself.
pub fn check_output(input: &Path, output: &Path) -> Result<(), String> {
    let folder = output
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .ok_or_else(|| "出力フォルダを選んでください。".to_string())?;
    if !folder.is_dir() {
        return Err(format!("出力フォルダがありません: {}", folder.display()));
    }
    let ext = extension_of(output).unwrap_or_default();
    if !OUTPUT_FORMATS.contains(&ext.as_str()) {
        return Err("出力は .las か .laz にしてください。".to_string());
    }
    let same = match (
        input.canonicalize(),
        folder.canonicalize(),
        output.file_name(),
    ) {
        (Ok(i), Ok(f), Some(name)) => i == f.join(name),
        _ => input == output,
    };
    if same {
        return Err(
            "出力が入力と同じファイルです。別の名前か別のフォルダにしてください。".to_string(),
        );
    }
    Ok(())
}

/// The full argument list. `--name=value` keeps a path that starts with `-`
/// from being read as an option.
pub fn build_argv(input: &Path, output: &Path, filter_args: &[String]) -> Vec<OsString> {
    let with = |flag: &str, path: &Path| {
        let mut arg = OsString::from(flag);
        arg.push(path.as_os_str());
        arg
    };
    let mut argv = vec![with("--input=", input), with("--output=", output)];
    argv.extend(filter_args.iter().map(OsString::from));
    argv
}

/// cli.py prints `Points processed: N` after a successful run.
pub fn parse_points_processed(line: &str) -> Option<u64> {
    line.trim().strip_prefix(POINTS_LINE)?.trim().parse().ok()
}

/// What cli.py puts in front of its own error lines.
const PIPELINE_MARKER: &str = "Pipeline execution failed:";
const ERROR_MARKER: &str = "Error:";

/// Words that mark the harmless messages PDAL prints when an optional plugin
/// (for example the HDF5 reader) cannot be loaded. Same list as
/// `PLUGIN_NOISE_WORDS` in src/logLine.ts.
const PLUGIN_NOISE_WORDS: &[&str] = &["dlopen", "unable to load", "failed to load", "library not loaded"];

/// Where cli.py's markers start in `line`. `Error:` that ends a Python
/// exception name (`RuntimeError:`) is not cli.py's own line, so it does not count.
fn marker_positions(line: &str) -> impl Iterator<Item = usize> + '_ {
    let pipeline = line.match_indices(PIPELINE_MARKER).map(|(i, _)| i);
    let error = line
        .match_indices(ERROR_MARKER)
        .map(|(i, _)| i)
        .filter(move |&i| !ends_exception_name(&line[..i]));
    pipeline.chain(error)
}

/// True when the text ends with a word that starts with a capital letter.
fn ends_exception_name(before: &str) -> bool {
    before
        .rsplit(|c: char| !c.is_ascii_alphanumeric())
        .next()
        .and_then(|word| word.chars().next())
        .is_some_and(|c| c.is_ascii_uppercase())
}

/// The text from cli.py's error marker on, when the line has one. The marker
/// can sit in the middle of a line: on macOS a PDAL plugin message is often
/// printed without a newline and the real error is glued onto its end.
pub fn error_text(line: &str) -> Option<&str> {
    let line = line.trim();
    marker_positions(line).min().map(|i| &line[i..])
}

/// A PDAL plugin-load message with no error of gqfilter's own in it.
pub fn is_plugin_noise(line: &str) -> bool {
    let lower = line.to_ascii_lowercase();
    lower.contains("plugin")
        && PLUGIN_NOISE_WORDS.iter().any(|w| lower.contains(w))
        && error_text(line).is_none()
}

/// The message to show when gqfilter failed: its own error lines when it
/// printed any (cli.py prints `Error: ...` or `Pipeline execution failed: ...`),
/// otherwise the last line that is not plugin noise.
pub fn failure_message(stderr: &[String], exit_code: Option<i32>) -> String {
    let marked: Vec<&str> = stderr.iter().filter_map(|l| error_text(l)).collect();
    if !marked.is_empty() {
        return marked.join("\n");
    }
    let mut lines = stderr.iter().rev().map(|l| l.trim()).filter(|l| !l.is_empty());
    let last = lines.clone().find(|l| !is_plugin_noise(l)).or_else(|| lines.next());
    if let Some(last) = last {
        return last.to_string();
    }
    match exit_code {
        Some(code) => format!("gqfilter が終了コード {code} で終わりました。"),
        None => "gqfilter が途中で止められました。".to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs::File;

    fn s(v: &[&str]) -> Vec<String> {
        v.iter().map(|x| x.to_string()).collect()
    }

    #[test]
    fn allowed_flags_match_the_shared_cases() {
        let cases: serde_json::Value =
            serde_json::from_str(include_str!("../../src/args/cases.json")).unwrap();
        let mut used: Vec<String> = cases["cases"]
            .as_array()
            .unwrap()
            .iter()
            .flat_map(|c| c["argv"].as_array().unwrap().iter())
            .filter_map(|t| t.as_str())
            .filter(|t| t.starts_with("--"))
            .map(str::to_string)
            .collect();
        used.sort();
        used.dedup();
        let mut allowed: Vec<String> = ALLOWED_FLAGS.iter().map(|f| f.to_string()).collect();
        allowed.sort();
        assert_eq!(allowed, used);
        for case in cases["cases"].as_array().unwrap() {
            let argv: Vec<String> = case["argv"]
                .as_array()
                .unwrap()
                .iter()
                .map(|t| t.as_str().unwrap().to_string())
                .collect();
            assert_eq!(validate_filter_args(&argv), Ok(()), "{}", case["name"]);
        }
    }

    #[test]
    fn accepts_what_the_window_builds() {
        let args = s(&[
            "--outlier-statistical",
            "--outlier-mean-k",
            "8",
            "--outlier-multiplier",
            "2",
            "--clip-xmin",
            "-10.5",
            "--keep-classes",
            "2",
            "6",
            "--ground-classify",
            "csf",
            "--ground-cell",
            "3",
            "--ground-only",
        ]);
        assert_eq!(validate_filter_args(&args), Ok(()));
    }

    #[test]
    fn rejects_unknown_flags_and_values() {
        for bad in [
            s(&[]),
            s(&["--input", "x.las"]),
            s(&["--output=x.las"]),
            s(&["--dry-run"]),
            s(&["8"]),
            s(&["--z-min", "abc"]),
            s(&["--z-min", "inf"]),
            s(&["--z-min", "NaN"]),
            s(&["--ground-classify", "rm"]),
            s(&["--sample-radius", "1;", "rm"]),
        ] {
            assert!(validate_filter_args(&bad).is_err(), "{bad:?}");
        }
    }

    #[test]
    fn output_name_comes_from_the_input() {
        let out = output_path(Path::new("/d/in/scan.e57"), Path::new("/d/out"), "laz").unwrap();
        assert_eq!(out, PathBuf::from("/d/out/scan_filtered.laz"));
        assert!(output_path(Path::new("/d/a.las"), Path::new("/d"), "e57").is_err());
    }

    #[test]
    fn input_and_output_checks() {
        let dir = tempfile::tempdir().unwrap();
        let input = dir.path().join("a.las");
        File::create(&input).unwrap();
        assert_eq!(check_input(&input), Ok(()));
        assert!(check_input(&dir.path().join("missing.las")).is_err());
        assert!(check_input(&dir.path().join("a.txt")).is_err());

        let out = dir.path().join("a_filtered.laz");
        assert_eq!(check_output(&input, &out), Ok(()));
        assert!(check_output(&input, &input).is_err(), "same file");
        assert!(check_output(&input, &dir.path().join("no/x.las")).is_err());
        assert!(check_output(&input, &dir.path().join("x.e57")).is_err());
    }

    #[test]
    fn argv_keeps_paths_as_one_argument() {
        let argv = build_argv(
            Path::new("/d/-odd name.las"),
            Path::new("/o/out.laz"),
            &s(&["--deduplicate"]),
        );
        assert_eq!(
            argv,
            vec![
                OsString::from("--input=/d/-odd name.las"),
                OsString::from("--output=/o/out.laz"),
                OsString::from("--deduplicate"),
            ]
        );
    }

    #[test]
    fn reads_cli_output() {
        assert_eq!(parse_points_processed("Points processed: 7262"), Some(7262));
        assert_eq!(parse_points_processed("  Points processed: 0\r"), Some(0));
        assert_eq!(parse_points_processed("Executing pipeline: a -> b"), None);
    }

    #[test]
    fn failure_message_prefers_cli_error_lines() {
        let err = s(&[
            "Traceback...",
            "Error: --ground-* options require --ground-classify",
        ]);
        assert_eq!(
            failure_message(&err, Some(1)),
            "Error: --ground-* options require --ground-classify"
        );
        assert_eq!(failure_message(&s(&["", "boom", " "]), Some(1)), "boom");
        assert!(failure_message(&[], Some(3)).contains('3'));
        assert!(failure_message(&[], None).contains("止められ"));
    }

    const NOISE: &str = "PDAL: Unable to load plugin /opt/pdal/lib/libpdal_plugin_reader_hdf.dylib: \
dlopen(/opt/pdal/lib/libpdal_plugin_reader_hdf.dylib, 0x0001): Library not loaded: @rpath/libhdf5.dylib";

    #[test]
    fn failure_message_finds_the_error_glued_onto_plugin_noise() {
        let err = s(&[
            NOISE,
            &format!("{NOISE}Pipeline execution failed: readers.las: Unable to open file"),
        ]);
        assert_eq!(
            failure_message(&err, Some(1)),
            "Pipeline execution failed: readers.las: Unable to open file"
        );
        let err = s(&[&format!("{NOISE} Error: input file not found")]);
        assert_eq!(failure_message(&err, Some(1)), "Error: input file not found");
    }

    #[test]
    fn failure_message_skips_plugin_noise_in_the_fallback() {
        let err = s(&["writers.las: disk full", NOISE, ""]);
        assert_eq!(failure_message(&err, Some(1)), "writers.las: disk full");
        // Only noise: still show something rather than nothing.
        assert_eq!(failure_message(&s(&[NOISE]), Some(1)), NOISE);
    }

    #[test]
    fn failure_message_ignores_python_exception_names() {
        let err = s(&["RuntimeError: something deep", "Error: the real cause"]);
        assert_eq!(failure_message(&err, Some(1)), "Error: the real cause");
        let err = s(&["libHDF5Pipeline execution failed: x"]);
        assert_eq!(failure_message(&err, Some(1)), "Pipeline execution failed: x");
        let err = s(&["Pipeline execution failed: bad Error: inside"]);
        assert_eq!(failure_message(&err, Some(1)), "Pipeline execution failed: bad Error: inside");
    }

    #[test]
    fn plugin_noise_is_recognised() {
        assert!(is_plugin_noise(NOISE));
        assert!(!is_plugin_noise(&format!("{NOISE}Error: x")));
        assert!(!is_plugin_noise("Error: plugin option is wrong"));
        assert!(!is_plugin_noise("Points processed: 10"));
    }
}
