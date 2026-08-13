//! `$UFO_HOME` state: credentials, onboarding session id, workspace URL — file-compatible with
//! what the shell client wrote.

use std::env;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process;

const CREDENTIALS_FILE: &str = "credentials";
const SESSION_FILE: &str = "session";
const WORKSPACE_FILE: &str = "workspace";
const GATEWAY_FILE: &str = "gateway";
#[cfg(windows)]
const BIN_NAME: &str = "ufo.exe";
#[cfg(not(windows))]
const BIN_NAME: &str = "ufo";

pub struct Home {
    pub root: PathBuf,
}

impl Home {
    /// `$UFO_HOME`, defaulting to `~/.ufo`.
    pub fn resolve() -> Home {
        let root = env::var_os("UFO_HOME")
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| home_dir().join(".ufo"));
        Home { root }
    }

    pub fn bin(&self) -> PathBuf {
        self.root.join("bin")
    }

    pub fn credentials(&self) -> Option<String> {
        self.read(CREDENTIALS_FILE)
    }

    pub fn session(&self) -> Option<String> {
        self.read(SESSION_FILE)
    }

    pub fn workspace(&self) -> Option<String> {
        self.read(WORKSPACE_FILE)
    }

    pub fn store_credentials(&self, token: &str) {
        self.write(CREDENTIALS_FILE, token, true);
    }

    pub fn store_session(&self, session_id: &str) {
        self.write(SESSION_FILE, session_id, false);
    }

    pub fn store_workspace(&self, url: &str) {
        self.write(WORKSPACE_FILE, url, false);
    }

    pub fn gateway(&self) -> Option<String> {
        self.read(GATEWAY_FILE)
    }

    pub fn store_gateway(&self, url: &str) {
        self.write(GATEWAY_FILE, url, false);
    }

    pub fn clear_signin(&self) {
        for name in [CREDENTIALS_FILE, SESSION_FILE, WORKSPACE_FILE] {
            let _ = fs::remove_file(self.root.join(name));
        }
    }

    fn read(&self, name: &str) -> Option<String> {
        fs::read_to_string(self.root.join(name))
            .ok()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
    }

    fn write(&self, name: &str, value: &str, secret: bool) {
        fs::create_dir_all(&self.root)
            .unwrap_or_else(|error| panic!("could not create {}: {error}", self.root.display()));
        let path = self.root.join(name);
        let mut options = fs::OpenOptions::new();
        options.write(true).create(true).truncate(true);
        #[cfg(unix)]
        if secret {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        #[cfg(not(unix))]
        let _ = secret;
        let mut file = options
            .open(&path)
            .unwrap_or_else(|error| panic!("could not write {}: {error}", path.display()));
        writeln!(file, "{value}")
            .unwrap_or_else(|error| panic!("could not write {}: {error}", path.display()));
        #[cfg(unix)]
        if secret {
            use std::os::unix::fs::PermissionsExt;
            let _ = fs::set_permissions(&path, fs::Permissions::from_mode(0o600));
        }
    }
}

fn home_dir() -> PathBuf {
    #[cfg(windows)]
    let name = "USERPROFILE";
    #[cfg(not(windows))]
    let name = "HOME";
    PathBuf::from(env::var_os(name).unwrap_or_else(|| panic!("{name} is unset")))
}

/// The release target this binary was built for, for the platforms the deploy serves.
pub fn client_target() -> Option<&'static str> {
    target_for(env::consts::OS, env::consts::ARCH)
}

fn target_for(os: &str, arch: &str) -> Option<&'static str> {
    match (os, arch) {
        ("macos", "aarch64") => Some("aarch64-apple-darwin"),
        ("macos", "x86_64") => Some("x86_64-apple-darwin"),
        ("linux", "x86_64") => Some("x86_64-unknown-linux-musl"),
        ("linux", "aarch64") => Some("aarch64-unknown-linux-musl"),
        ("windows", "x86_64") => Some("x86_64-pc-windows-msvc"),
        _ => None,
    }
}

/// Ensure `$UFO_HOME/bin/ufo` is current and on PATH: download the served binary for this build's
/// target through `download`, then wire PATH via the shell profile the way the installer script
/// did. A failed download falls back to copying the running binary only where that copy installs
/// something — a binary already running from the bin directory has nothing to copy, so its failed
/// update surfaces instead of reporting an install that never happened.
pub fn install_self(
    home: &Home,
    download: impl FnOnce(&str, &Path) -> Result<(), String>,
) -> Result<String, String> {
    let bin_dir = home.bin();
    fs::create_dir_all(&bin_dir)
        .map_err(|error| format!("could not create {}: {error}", bin_dir.display()))?;
    let staged = bin_dir.join(format!(".ufo.tmp.{}", process::id()));
    let downloaded = match client_target().map(|target| download(target, &staged)) {
        Some(Ok(())) => Some(land(&bin_dir, &staged)?),
        outcome => {
            let _ = fs::remove_file(&staged);
            if running_from(&bin_dir) {
                let reason = match outcome {
                    Some(Err(error)) => error,
                    _ => "no binary is built for this platform".to_string(),
                };
                return Err(format!("the update did not land: {reason}"));
            }
            None
        }
    };
    let target = match downloaded {
        Some(landed) => landed,
        None => copy_self(&bin_dir, &staged)?,
    };
    let mut lines = vec![format!("✓ Installed ufo ({})", target.display())];
    #[cfg(unix)]
    if let Some(line) = add_to_path(&bin_dir, &target) {
        lines.push(line);
    }
    #[cfg(windows)]
    lines.push(format!(
        "Add {} to your PATH to run ufo anywhere",
        bin_dir.display()
    ));
    Ok(lines.join("\n"))
}

fn land(bin_dir: &Path, staged: &Path) -> Result<PathBuf, String> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(staged, fs::Permissions::from_mode(0o755))
            .map_err(|error| format!("could not mark {} executable: {error}", staged.display()))?;
    }
    let destination = bin_dir.join(BIN_NAME);
    #[cfg(windows)]
    if destination.exists() {
        let retired = bin_dir.join(format!("ufo.exe.old.{}", process::id()));
        fs::rename(&destination, &retired)
            .map_err(|error| format!("could not retire {}: {error}", destination.display()))?;
    }
    fs::rename(staged, &destination)
        .map_err(|error| format!("could not install {}: {error}", destination.display()))?;
    Ok(destination)
}

fn running_from(bin_dir: &Path) -> bool {
    let exe = env::current_exe()
        .ok()
        .and_then(|exe| exe.canonicalize().ok());
    match (exe, bin_dir.canonicalize()) {
        (Some(exe), Ok(bin)) => exe.starts_with(bin),
        _ => false,
    }
}

/// Whether this process runs the installed binary, resolved through symlinks on both sides.
pub fn installed(home: &Home) -> bool {
    running_from(&home.bin())
}

/// Sweep the retired binaries Windows updates left beside the live one — each deletable once no
/// old process runs it; best effort until then.
pub fn sweep_retired(home: &Home) {
    let Ok(entries) = fs::read_dir(home.bin()) else {
        return;
    };
    for entry in entries.flatten() {
        if entry
            .file_name()
            .to_string_lossy()
            .starts_with("ufo.exe.old")
        {
            let _ = fs::remove_file(entry.path());
        }
    }
}

fn copy_self(bin_dir: &Path, staged: &Path) -> Result<PathBuf, String> {
    let exe = env::current_exe()
        .map_err(|error| format!("could not locate the running binary: {error}"))?;
    let target = bin_dir.join(BIN_NAME);
    if exe.canonicalize().ok() != target.canonicalize().ok() || !target.exists() {
        fs::copy(&exe, staged)
            .map_err(|error| format!("could not copy into {}: {error}", staged.display()))?;
        return land(bin_dir, staged);
    }
    Ok(target)
}

#[cfg(unix)]
fn add_to_path(bin_dir: &Path, target: &Path) -> Option<String> {
    let path_var = env::var("PATH").unwrap_or_default();
    let bin = bin_dir.to_string_lossy().to_string();
    if path_var.split(':').any(|entry| entry == bin) {
        return None;
    }
    let home_dir = env::var("HOME").unwrap_or_default();
    if !home_dir.is_empty() {
        let local_bin = format!("{home_dir}/.local/bin");
        if path_var.split(':').any(|entry| entry == local_bin) {
            fs::create_dir_all(&local_bin).ok()?;
            let link = Path::new(&local_bin).join("ufo");
            let _ = fs::remove_file(&link);
            std::os::unix::fs::symlink(target, &link).ok()?;
            return Some("✓ Linked ufo into ~/.local/bin".into());
        }
    }
    let bin_ref = if !home_dir.is_empty() && bin.starts_with(&home_dir) {
        format!("$HOME{}", &bin[home_dir.len()..])
    } else {
        bin.clone()
    };
    let profile = profile_file(&home_dir);
    let line = if profile
        .extension()
        .is_some_and(|extension| extension == "fish")
    {
        format!("fish_add_path -g \"{bin_ref}\"")
    } else {
        format!("export PATH=\"{bin_ref}:$PATH\"")
    };
    if fs::read_to_string(&profile).is_ok_and(|existing| existing.contains(&bin_ref)) {
        return None;
    }
    if let Some(parent) = profile.parent() {
        fs::create_dir_all(parent).ok()?;
    }
    let mut file = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&profile)
        .ok()?;
    writeln!(file, "\n# added by ufo installer\n{line}").ok()?;
    Some(format!(
        "✓ Added ufo to PATH in {}\n  For this shell: export PATH=\"{bin}:$PATH\"",
        profile.display()
    ))
}

#[cfg(unix)]
fn profile_file(home_dir: &str) -> PathBuf {
    let shell = env::var("SHELL").unwrap_or_else(|_| "/bin/sh".into());
    if shell.ends_with("/zsh") {
        let base = env::var("ZDOTDIR")
            .ok()
            .filter(|z| !z.is_empty())
            .unwrap_or_else(|| home_dir.into());
        return Path::new(&base).join(".zshrc");
    }
    if shell.ends_with("/bash") {
        if cfg!(target_os = "macos") {
            return Path::new(home_dir).join(".bash_profile");
        }
        return Path::new(home_dir).join(".bashrc");
    }
    if shell.ends_with("/fish") {
        return Path::new(home_dir).join(".config/fish/conf.d/ufo.fish");
    }
    Path::new(home_dir).join(".profile")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let root = env::temp_dir().join(format!("ufo-home-test-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        root
    }

    #[test]
    fn state_round_trips() {
        let home = Home {
            root: scratch("roundtrip"),
        };
        assert!(home.credentials().is_none());
        assert!(home.session().is_none());
        assert!(home.workspace().is_none());
        home.store_credentials("tok");
        home.store_session("host.1.2");
        home.store_workspace("https://w.example");
        home.store_gateway("https://g.example");
        assert_eq!(home.credentials().as_deref(), Some("tok"));
        assert_eq!(home.session().as_deref(), Some("host.1.2"));
        assert_eq!(home.workspace().as_deref(), Some("https://w.example"));
        assert_eq!(home.gateway().as_deref(), Some("https://g.example"));
        home.clear_signin();
        assert!(home.credentials().is_none());
        assert!(home.session().is_none());
        assert!(home.workspace().is_none());
        assert_eq!(home.gateway().as_deref(), Some("https://g.example"));
        let _ = fs::remove_dir_all(&home.root);
    }

    #[cfg(unix)]
    #[test]
    fn credentials_are_private() {
        use std::os::unix::fs::PermissionsExt;
        let home = Home {
            root: scratch("private"),
        };
        home.store_credentials("tok");
        let mode = fs::metadata(home.root.join(CREDENTIALS_FILE))
            .unwrap()
            .permissions()
            .mode();
        assert_eq!(mode & 0o777, 0o600);
        let _ = fs::remove_dir_all(&home.root);
    }

    #[test]
    fn targets_cover_the_served_platforms() {
        assert_eq!(target_for("macos", "aarch64"), Some("aarch64-apple-darwin"));
        assert_eq!(target_for("macos", "x86_64"), Some("x86_64-apple-darwin"));
        assert_eq!(
            target_for("linux", "x86_64"),
            Some("x86_64-unknown-linux-musl")
        );
        assert_eq!(
            target_for("linux", "aarch64"),
            Some("aarch64-unknown-linux-musl")
        );
        assert_eq!(
            target_for("windows", "x86_64"),
            Some("x86_64-pc-windows-msvc")
        );
        assert_eq!(target_for("freebsd", "x86_64"), None);
        assert_eq!(target_for("windows", "aarch64"), None);
    }

    fn path_holding(bin_dir: &Path) {
        let joined = format!(
            "{}:{}",
            env::var("PATH").unwrap_or_default(),
            bin_dir.display()
        );
        env::set_var("PATH", joined);
    }

    #[test]
    fn install_lands_the_downloaded_binary() {
        let home = Home {
            root: scratch("download"),
        };
        path_holding(&home.bin());
        let report = install_self(&home, |target, dest| {
            assert_eq!(Some(target), client_target());
            fs::write(dest, b"SERVED BINARY").map_err(|error| error.to_string())
        })
        .unwrap();
        assert!(report.contains("Installed ufo"));
        assert_eq!(
            fs::read(home.bin().join(BIN_NAME)).unwrap(),
            b"SERVED BINARY"
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = fs::metadata(home.bin().join(BIN_NAME))
                .unwrap()
                .permissions()
                .mode();
            assert_eq!(mode & 0o111, 0o111);
        }
        let _ = fs::remove_dir_all(&home.root);
    }

    #[test]
    fn install_falls_back_to_the_running_binary() {
        let home = Home {
            root: scratch("fallback"),
        };
        path_holding(&home.bin());
        let report = install_self(&home, |_, _| Err("404".into())).unwrap();
        assert!(report.contains("Installed ufo"));
        let landed = fs::metadata(home.bin().join(BIN_NAME)).unwrap().len();
        let exe = fs::metadata(env::current_exe().unwrap()).unwrap().len();
        assert_eq!(landed, exe);
        assert!(!home.bin().join(".ufo.tmp").exists());
        let _ = fs::remove_dir_all(&home.root);
    }

    #[test]
    fn reads_trim_whitespace() {
        let home = Home {
            root: scratch("trim"),
        };
        fs::create_dir_all(&home.root).unwrap();
        fs::write(home.root.join(SESSION_FILE), "  sid  \n").unwrap();
        assert_eq!(home.session().as_deref(), Some("sid"));
        fs::write(home.root.join(WORKSPACE_FILE), "\n").unwrap();
        assert!(home.workspace().is_none());
        let _ = fs::remove_dir_all(&home.root);
    }

    #[test]
    fn sweep_removes_the_retired_binary() {
        let home = Home {
            root: scratch("sweep"),
        };
        fs::create_dir_all(home.bin()).unwrap();
        let retired = home.bin().join("ufo.exe.old");
        fs::write(&retired, b"old").unwrap();
        sweep_retired(&home);
        assert!(!retired.exists());
        let _ = fs::remove_dir_all(&home.root);
    }
}
