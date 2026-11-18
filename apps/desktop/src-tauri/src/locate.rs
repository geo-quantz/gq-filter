//! Finds the `gqfilter` executable (the PyInstaller build from the releases).
//!
//! Order: the path set in the app → `GQFILTER_PATH` → `PATH` → usual install
//! places (the Windows MSI installs to `%ProgramFiles%\gqfilter`; on macOS and
//! Linux an app started from the desktop does not see the shell's `PATH`).
//! A folder is accepted too: the unpacked release folder holds the executable.
//! Pure logic: file checks go through `Fs` so every platform can be tested.

use std::ffi::OsString;
use std::path::{Path, PathBuf};

pub const ENV_VAR: &str = "GQFILTER_PATH";

#[derive(Debug, Clone, Default)]
pub struct LocateInput {
    pub windows: bool,
    pub setting: Option<PathBuf>,
    pub env: Option<OsString>,
    pub path_var: Option<OsString>,
    pub home: Option<PathBuf>,
    pub program_files: Option<OsString>,
}

impl LocateInput {
    /// The real environment of this process.
    pub fn from_env(setting: Option<PathBuf>) -> Self {
        Self {
            windows: cfg!(windows),
            setting,
            env: std::env::var_os(ENV_VAR),
            path_var: std::env::var_os("PATH"),
            home: std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
                .map(PathBuf::from),
            program_files: std::env::var_os("ProgramFiles"),
        }
    }
}

pub trait Fs {
    fn is_file(&self, path: &Path) -> bool;
    fn is_dir(&self, path: &Path) -> bool;
}

pub struct RealFs;

impl Fs for RealFs {
    fn is_file(&self, path: &Path) -> bool {
        path.is_file()
    }
    fn is_dir(&self, path: &Path) -> bool {
        path.is_dir()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Source {
    Setting,
    Env,
    Path,
    InstallDir,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Found {
    pub path: PathBuf,
    pub source: Source,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LocateError {
    /// The path set in the app has no gqfilter.
    SettingMissing(PathBuf),
    /// `GQFILTER_PATH` has no gqfilter.
    EnvMissing(PathBuf),
    /// A `.bat` / `.cmd` would run through cmd.exe, which re-reads the arguments.
    Script(PathBuf),
    NotFound,
}

impl LocateError {
    /// A message for the window (Japanese, like the rest of the UI).
    pub fn message(&self) -> String {
        match self {
            Self::SettingMissing(p) => format!(
                "設定した場所に gqfilter がありません: {}。gqfilter の実行ファイルか、それが入ったフォルダを選んでください。",
                p.display()
            ),
            Self::EnvMissing(p) => {
                format!("{ENV_VAR} に書かれた場所に gqfilter がありません: {}", p.display())
            }
            Self::Script(p) => format!(
                ".bat / .cmd は使えません。gqfilter の実行ファイルを選んでください: {}",
                p.display()
            ),
            Self::NotFound => "gqfilter が見つかりません。リリースの gqfilter を入れて、「gqfilter の場所」で実行ファイルを選んでください。".to_string(),
        }
    }
}

pub fn exe_name(windows: bool) -> &'static str {
    if windows {
        "gqfilter.exe"
    } else {
        "gqfilter"
    }
}

pub fn locate(input: &LocateInput, fs: &dyn Fs) -> Result<Found, LocateError> {
    if let Some(setting) = input.setting.as_ref().filter(|p| !p.as_os_str().is_empty()) {
        return explicit(setting, input.windows, fs, Source::Setting)
            .ok_or_else(|| missing_or_script(setting, input.windows, LocateError::SettingMissing));
    }
    if let Some(env) = input.env.as_ref().filter(|v| !v.is_empty()) {
        let path = PathBuf::from(env);
        return explicit(&path, input.windows, fs, Source::Env)
            .ok_or_else(|| missing_or_script(&path, input.windows, LocateError::EnvMissing));
    }
    let name = exe_name(input.windows);
    let on_path = input
        .path_var
        .as_ref()
        .map(|p| std::env::split_paths(p).collect::<Vec<_>>())
        .unwrap_or_default();
    if let Some(path) = first_file(&on_path, name, fs) {
        return Ok(Found {
            path,
            source: Source::Path,
        });
    }
    if let Some(path) = first_file(&install_dirs(input), name, fs) {
        return Ok(Found {
            path,
            source: Source::InstallDir,
        });
    }
    Err(LocateError::NotFound)
}

fn install_dirs(input: &LocateInput) -> Vec<PathBuf> {
    if input.windows {
        return input
            .program_files
            .iter()
            .map(|p| PathBuf::from(p).join("gqfilter"))
            .collect();
    }
    let mut dirs = vec![
        PathBuf::from("/usr/local/bin"),
        PathBuf::from("/opt/homebrew/bin"),
    ];
    if let Some(home) = &input.home {
        dirs.push(home.join(".local").join("bin"));
    }
    dirs
}

fn first_file(dirs: &[PathBuf], name: &str, fs: &dyn Fs) -> Option<PathBuf> {
    dirs.iter()
        .filter(|d| !d.as_os_str().is_empty())
        .map(|d| d.join(name))
        .find(|p| fs.is_file(p))
}

fn is_script(path: &Path) -> bool {
    path.extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| e.eq_ignore_ascii_case("bat") || e.eq_ignore_ascii_case("cmd"))
}

fn explicit(path: &Path, windows: bool, fs: &dyn Fs, source: Source) -> Option<Found> {
    let candidate = if fs.is_dir(path) {
        path.join(exe_name(windows))
    } else {
        path.to_path_buf()
    };
    if (windows && is_script(&candidate)) || !fs.is_file(&candidate) {
        return None;
    }
    Some(Found {
        path: candidate,
        source,
    })
}

fn missing_or_script(
    path: &Path,
    windows: bool,
    missing: fn(PathBuf) -> LocateError,
) -> LocateError {
    if windows && is_script(path) {
        LocateError::Script(path.to_path_buf())
    } else {
        missing(path.to_path_buf())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    struct FakeFs {
        files: HashSet<PathBuf>,
        dirs: HashSet<PathBuf>,
    }

    impl FakeFs {
        fn new(files: &[&str], dirs: &[&str]) -> Self {
            Self {
                files: files.iter().map(PathBuf::from).collect(),
                dirs: dirs.iter().map(PathBuf::from).collect(),
            }
        }
    }

    impl Fs for FakeFs {
        fn is_file(&self, path: &Path) -> bool {
            self.files.contains(path)
        }
        fn is_dir(&self, path: &Path) -> bool {
            self.dirs.contains(path)
        }
    }

    fn unix(setting: Option<&str>, env: Option<&str>, path_var: Option<&str>) -> LocateInput {
        LocateInput {
            windows: false,
            setting: setting.map(PathBuf::from),
            env: env.map(OsString::from),
            path_var: path_var.map(OsString::from),
            home: Some(PathBuf::from("/home/u")),
            program_files: None,
        }
    }

    #[test]
    fn setting_file_wins() {
        let fs = FakeFs::new(&["/opt/g/gqfilter", "/usr/bin/gqfilter"], &[]);
        let found = locate(&unix(Some("/opt/g/gqfilter"), None, Some("/usr/bin")), &fs).unwrap();
        assert_eq!(found.path, PathBuf::from("/opt/g/gqfilter"));
        assert_eq!(found.source, Source::Setting);
    }

    #[test]
    fn setting_folder_means_the_release_folder() {
        let fs = FakeFs::new(&["/opt/g/gqfilter"], &["/opt/g"]);
        let found = locate(&unix(Some("/opt/g"), None, None), &fs).unwrap();
        assert_eq!(found.path, PathBuf::from("/opt/g/gqfilter"));
    }

    #[test]
    fn missing_setting_does_not_fall_back() {
        let fs = FakeFs::new(&["/usr/bin/gqfilter"], &[]);
        let err = locate(&unix(Some("/nope"), None, Some("/usr/bin")), &fs).unwrap_err();
        assert_eq!(err, LocateError::SettingMissing(PathBuf::from("/nope")));
        assert!(err.message().contains("/nope"));
    }

    #[test]
    fn empty_setting_is_ignored() {
        let fs = FakeFs::new(&["/usr/bin/gqfilter"], &[]);
        let found = locate(&unix(Some(""), None, Some("/usr/bin")), &fs).unwrap();
        assert_eq!(found.source, Source::Path);
    }

    #[test]
    fn env_var_before_path() {
        let fs = FakeFs::new(&["/e/gqfilter", "/usr/bin/gqfilter"], &[]);
        let found = locate(&unix(None, Some("/e/gqfilter"), Some("/usr/bin")), &fs).unwrap();
        assert_eq!(found.source, Source::Env);
        let err = locate(&unix(None, Some("/x"), Some("/usr/bin")), &fs).unwrap_err();
        assert_eq!(err, LocateError::EnvMissing(PathBuf::from("/x")));
    }

    #[test]
    fn path_then_install_dirs() {
        let fs = FakeFs::new(&["/b/gqfilter"], &[]);
        let found = locate(&unix(None, None, Some("/a:/b")), &fs).unwrap();
        assert_eq!(found.path, PathBuf::from("/b/gqfilter"));

        let fs = FakeFs::new(&["/home/u/.local/bin/gqfilter"], &[]);
        let found = locate(&unix(None, None, Some("/a")), &fs).unwrap();
        assert_eq!(found.source, Source::InstallDir);
    }

    #[test]
    fn not_found() {
        let fs = FakeFs::new(&[], &[]);
        assert_eq!(
            locate(&unix(None, None, Some("/a")), &fs),
            Err(LocateError::NotFound)
        );
    }

    #[test]
    fn windows_program_files_and_no_scripts() {
        let input = LocateInput {
            windows: true,
            program_files: Some(OsString::from("C:/PF")),
            ..Default::default()
        };
        let fs = FakeFs::new(&["C:/PF/gqfilter/gqfilter.exe"], &[]);
        let found = locate(&input, &fs).unwrap();
        assert_eq!(found.path, PathBuf::from("C:/PF/gqfilter/gqfilter.exe"));

        let script = LocateInput {
            windows: true,
            setting: Some(PathBuf::from("C:/x/run.bat")),
            ..Default::default()
        };
        let fs = FakeFs::new(&["C:/x/run.bat"], &[]);
        assert_eq!(
            locate(&script, &fs),
            Err(LocateError::Script(PathBuf::from("C:/x/run.bat")))
        );
    }
}
