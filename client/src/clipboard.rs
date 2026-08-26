//! The OS clipboard behind Ctrl+V: an image wins, text otherwise, read through the platform's
//! own tool so the static binary carries no clipboard stack.

use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Duration, Instant};

use sha2::{Digest, Sha256};

use crate::ops::fileops::fs_read::{IMAGE_MAX_BYTES, IMAGE_MEDIA_TYPES};

const RUNS_DIR: &str = "runs";
const IMAGES_DIR: &str = "images";
const RUNTIME_ID_HEX_CHARS: usize = 32;
const TOOL_DEADLINE: Duration = Duration::from_secs(5);

/// What the clipboard held.
pub enum Clip {
    Image(Vec<u8>),
    Text(String),
    Empty,
}

/// Read the clipboard: image bytes when one is held, a copied file's path next, text otherwise.
pub fn read() -> Result<Clip, String> {
    if let Some(bytes) = read_image()? {
        return Ok(Clip::Image(bytes));
    }
    if let Some(path) = read_file_path()? {
        return Ok(Clip::Text(path));
    }
    let text = read_text()?;
    if text.is_empty() {
        return Ok(Clip::Empty);
    }
    Ok(Clip::Text(text))
}

/// Save pasted image bytes under this conversation's runtime directory; answers the
/// `$UFO_HOME` path the message names.
pub fn stash_image(bytes: &[u8], home: &Path, channel: &str) -> Result<String, String> {
    stash(bytes, "png", home, channel)
}

/// Copy a dropped image file into the runtime directory; answers its `$UFO_HOME` path.
pub fn stash_copy(source: &Path, home: &Path, channel: &str) -> Result<String, String> {
    let bytes = std::fs::read(source)
        .map_err(|error| format!("could not read {}: {error}", source.display()))?;
    let extension = source
        .extension()
        .and_then(|extension| extension.to_str())
        .expect("a dropped image path carries its extension")
        .to_ascii_lowercase();
    stash(&bytes, &extension, home, channel)
}

/// The image file a paste names, when the pasted text is exactly one existing image path —
/// what a drag-drop or a Finder copy delivers as text.
pub fn dropped_image(text: &str) -> Option<PathBuf> {
    let trimmed = text.trim();
    let unquoted = trimmed
        .strip_prefix('\'')
        .and_then(|rest| rest.strip_suffix('\''))
        .or_else(|| {
            trimmed
                .strip_prefix('"')
                .and_then(|rest| rest.strip_suffix('"'))
        })
        .unwrap_or(trimmed);
    let mut plain = String::with_capacity(unquoted.len());
    let mut chars = unquoted.chars();
    while let Some(character) = chars.next() {
        match character {
            '\\' => plain.push(chars.next()?),
            '\n' => return None,
            _ => plain.push(character),
        }
    }
    let path = PathBuf::from(&plain);
    let extension = format!(".{}", path.extension()?.to_str()?.to_ascii_lowercase());
    if !IMAGE_MEDIA_TYPES
        .iter()
        .any(|(known, _)| *known == extension)
    {
        return None;
    }
    if !path.is_file() {
        return None;
    }
    Some(path)
}

fn runtime_id(channel: &str) -> String {
    let digest = format!("{:x}", Sha256::digest(channel.as_bytes()));
    digest[..RUNTIME_ID_HEX_CHARS].to_string()
}

fn stash(bytes: &[u8], extension: &str, home: &Path, channel: &str) -> Result<String, String> {
    static STASHED: AtomicUsize = AtomicUsize::new(1);
    if bytes.len() > IMAGE_MAX_BYTES {
        return Err(format!(
            "The pasted image is {:.1} MB, over the {} MB cap.",
            bytes.len() as f64 / (1024.0 * 1024.0),
            IMAGE_MAX_BYTES / (1024 * 1024)
        ));
    }
    let at = STASHED.fetch_add(1, Ordering::Relaxed);
    let run_id = runtime_id(channel);
    let relative = format!(
        "{RUNS_DIR}/{run_id}/{IMAGES_DIR}/image-{}-{at}.{extension}",
        std::process::id()
    );
    let path = home.join(&relative);
    let parent = path.parent().expect("the stash path names a directory");
    std::fs::create_dir_all(parent)
        .and_then(|()| std::fs::write(&path, bytes))
        .map_err(|error| format!("could not save the pasted image: {error}"))?;
    Ok(format!("$UFO_HOME/{relative}"))
}

/// Remove this session's stashed images, and the stash directories once nothing else is in them.
pub fn sweep_stash(home: &Path) {
    let own = format!("image-{}-", std::process::id());
    let runs = home.join(RUNS_DIR);
    let Ok(entries) = std::fs::read_dir(&runs) else {
        return;
    };
    for run in entries.flatten() {
        let dir = run.path().join(IMAGES_DIR);
        let Ok(images) = std::fs::read_dir(&dir) else {
            continue;
        };
        for image in images.flatten() {
            if image.file_name().to_string_lossy().starts_with(&own) {
                let _ = std::fs::remove_file(image.path());
            }
        }
        let _ = std::fs::remove_dir(&dir);
        let _ = std::fs::remove_dir(run.path());
    }
    let _ = std::fs::remove_dir(&runs);
}

fn run(name: &str, args: &[&str]) -> Result<Output, String> {
    let mut child = Command::new(name)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| {
            if cfg!(target_os = "linux") && error.kind() == std::io::ErrorKind::NotFound {
                return format!(
                    "{name} is not installed; install wl-clipboard (Wayland) or xclip (X11) to \
                     paste from the clipboard"
                );
            }
            format!("could not run {name}: {error}")
        })?;
    let stdout = drain(child.stdout.take().expect("stdout is piped"));
    let stderr = drain(child.stderr.take().expect("stderr is piped"));
    let deadline = Instant::now() + TOOL_DEADLINE;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(25)),
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!(
                    "{name} did not answer within {} seconds.",
                    TOOL_DEADLINE.as_secs()
                ));
            }
            Err(error) => return Err(format!("could not run {name}: {error}")),
        }
    };
    Ok(Output {
        status,
        stdout: stdout.join().unwrap_or_default(),
        stderr: stderr.join().unwrap_or_default(),
    })
}

fn drain<R: std::io::Read + Send + 'static>(mut pipe: R) -> std::thread::JoinHandle<Vec<u8>> {
    std::thread::spawn(move || {
        let mut bytes = Vec::new();
        let _ = pipe.read_to_end(&mut bytes);
        bytes
    })
}

#[cfg(target_os = "macos")]
fn read_image() -> Result<Option<Vec<u8>>, String> {
    let output = run("osascript", &["-e", "the clipboard as «class PNGf»"])?;
    if !output.status.success() {
        return Ok(None);
    }
    Ok(osascript_png(&String::from_utf8_lossy(&output.stdout)))
}

#[cfg(target_os = "macos")]
fn read_text() -> Result<String, String> {
    let output = run("pbpaste", &[])?;
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

#[cfg(target_os = "macos")]
fn read_file_path() -> Result<Option<String>, String> {
    let output = run(
        "osascript",
        &["-e", "POSIX path of (the clipboard as «class furl»)"],
    )?;
    if !output.status.success() {
        return Ok(None);
    }
    let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
    Ok((!path.is_empty()).then_some(path))
}

#[cfg(any(target_os = "macos", test))]
fn osascript_png(stdout: &str) -> Option<Vec<u8>> {
    let hex = stdout
        .trim()
        .strip_prefix("«data PNGf")?
        .strip_suffix('»')?;
    if !hex.is_ascii() || hex.len() % 2 != 0 {
        return None;
    }
    (0..hex.len())
        .step_by(2)
        .map(|at| u8::from_str_radix(&hex[at..at + 2], 16).ok())
        .collect()
}

#[cfg(target_os = "linux")]
const PNG_MAGIC: &[u8] = &[0x89, b'P', b'N', b'G', b'\r', b'\n', 0x1a, b'\n'];

#[cfg(target_os = "linux")]
fn wayland() -> bool {
    std::env::var_os("WAYLAND_DISPLAY").is_some()
}

#[cfg(target_os = "linux")]
fn read_image() -> Result<Option<Vec<u8>>, String> {
    let output = if wayland() {
        run("wl-paste", &["-t", "image/png"])?
    } else {
        run(
            "xclip",
            &["-selection", "clipboard", "-t", "image/png", "-o"],
        )?
    };
    if !output.status.success() || !output.stdout.starts_with(PNG_MAGIC) {
        return Ok(None);
    }
    Ok(Some(output.stdout))
}

#[cfg(target_os = "linux")]
fn read_file_path() -> Result<Option<String>, String> {
    let output = if wayland() {
        run("wl-paste", &["-n", "-t", "text/uri-list"])?
    } else {
        run(
            "xclip",
            &["-selection", "clipboard", "-t", "text/uri-list", "-o"],
        )?
    };
    if !output.status.success() {
        return Ok(None);
    }
    let listed = String::from_utf8_lossy(&output.stdout);
    Ok(listed
        .lines()
        .find_map(|line| line.trim().strip_prefix("file://"))
        .map(percent_decoded))
}

#[cfg(target_os = "linux")]
fn read_text() -> Result<String, String> {
    let output = if wayland() {
        run("wl-paste", &["-n"])?
    } else {
        run("xclip", &["-selection", "clipboard", "-o"])?
    };
    if !output.status.success() {
        return Ok(String::new());
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

#[cfg(windows)]
const IMAGE_SCRIPT: &str = "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; \
    $image = [System.Windows.Forms.Clipboard]::GetImage(); \
    if ($image -ne $null) { \
    $stream = New-Object System.IO.MemoryStream; \
    $image.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png); \
    [System.Convert]::ToBase64String($stream.ToArray()) }";

#[cfg(windows)]
fn read_image() -> Result<Option<Vec<u8>>, String> {
    use base64::Engine as _;

    let output = run("powershell", &["-NoProfile", "-Command", IMAGE_SCRIPT])?;
    let encoded = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if !output.status.success() || encoded.is_empty() {
        return Ok(None);
    }
    Ok(base64::engine::general_purpose::STANDARD
        .decode(&encoded)
        .ok())
}

#[cfg(any(target_os = "linux", test))]
fn percent_decoded(encoded: &str) -> String {
    let raw = encoded.as_bytes();
    let mut bytes = Vec::with_capacity(raw.len());
    let mut at = 0;
    while at < raw.len() {
        let decoded = (raw[at] == b'%' && at + 3 <= raw.len())
            .then(|| &raw[at + 1..at + 3])
            .and_then(|hex| std::str::from_utf8(hex).ok())
            .and_then(|hex| u8::from_str_radix(hex, 16).ok());
        match decoded {
            Some(byte) => {
                bytes.push(byte);
                at += 3;
            }
            None => {
                bytes.push(raw[at]);
                at += 1;
            }
        }
    }
    String::from_utf8_lossy(&bytes).into_owned()
}

#[cfg(windows)]
fn read_file_path() -> Result<Option<String>, String> {
    let output = run(
        "powershell",
        &[
            "-NoProfile",
            "-Command",
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; \
             $files = Get-Clipboard -Format FileDropList; if ($files) { $files[0].FullName }",
        ],
    )?;
    if !output.status.success() {
        return Ok(None);
    }
    let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
    Ok((!path.is_empty()).then_some(path))
}

#[cfg(windows)]
fn read_text() -> Result<String, String> {
    let output = run(
        "powershell",
        &[
            "-NoProfile",
            "-Command",
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; Get-Clipboard -Raw",
        ],
    )?;
    if !output.status.success() {
        return Ok(String::new());
    }
    Ok(String::from_utf8_lossy(&output.stdout)
        .trim_end_matches(['\r', '\n'])
        .to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn osascript_hex_decodes_to_png_bytes() {
        let decoded = osascript_png("«data PNGf89504E470D0A1A0A0001»\n").unwrap();
        assert_eq!(
            decoded,
            [0x89, b'P', b'N', b'G', b'\r', b'\n', 0x1a, b'\n', 0, 1]
        );
    }

    #[test]
    fn osascript_junk_is_no_image() {
        assert_eq!(osascript_png("error: no clipboard"), None);
        assert_eq!(osascript_png("«data PNGf123»"), None);
        assert_eq!(osascript_png("«data PNGfZZ»"), None);
        assert_eq!(osascript_png("«data PNGf«»»"), None);
    }

    #[test]
    fn stash_answers_a_run_path_and_sweep_clears_it() {
        let home = std::env::temp_dir().join(format!("ufo-stash-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&home);
        std::fs::create_dir_all(&home).unwrap();
        let relative = stash_image(&[1, 2, 3], &home, "conversation").unwrap();
        assert!(
            relative.starts_with("$UFO_HOME/runs/") && relative.ends_with(".png"),
            "{relative}"
        );
        let path = home.join(relative.trim_start_matches("$UFO_HOME/"));
        assert_eq!(std::fs::read(path).unwrap(), [1, 2, 3]);
        sweep_stash(&home);
        assert!(!home.join(RUNS_DIR).exists());
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn runtime_id_matches_the_terminal_surface() {
        assert_eq!(
            runtime_id("conversation"),
            "8b34dbc2c05eb4d7e25d48efeace8245"
        );
    }

    #[test]
    fn a_dropped_path_is_recognized_through_escapes_and_quotes() {
        let dir = std::env::temp_dir().join(format!("ufo-drop-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let source = dir.join("shot one.JPG");
        std::fs::write(&source, b"x").unwrap();
        let plain = source.display().to_string();
        let escaped = plain.replace(' ', "\\ ");
        assert_eq!(
            dropped_image(&format!("{escaped} \n")),
            Some(source.clone())
        );
        assert_eq!(dropped_image(&format!("'{plain}'")), Some(source.clone()));
        assert_eq!(dropped_image(&format!("\"{plain}\"")), Some(source.clone()));
        assert_eq!(dropped_image("look at shot.png please"), None);
        assert_eq!(dropped_image(&format!("{escaped}\n{escaped}")), None);
        assert_eq!(
            dropped_image(&dir.join("absent.png").display().to_string()),
            None
        );
        let text = dir.join("notes.txt");
        std::fs::write(&text, b"x").unwrap();
        assert_eq!(dropped_image(&text.display().to_string()), None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn stash_copy_keeps_the_bytes_and_lowercases_the_extension() {
        let cwd = std::env::temp_dir().join(format!("ufo-copy-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&cwd);
        std::fs::create_dir_all(&cwd).unwrap();
        let source = cwd.join("shot.JPG");
        std::fs::write(&source, [1, 2, 3]).unwrap();
        let relative = stash_copy(&source, &cwd, "conversation").unwrap();
        assert!(relative.ends_with(".jpg"), "{relative}");
        assert_eq!(
            std::fs::read(cwd.join(relative.trim_start_matches("$UFO_HOME/"))).unwrap(),
            [1, 2, 3]
        );
        assert!(source.exists());
        let _ = std::fs::remove_dir_all(&cwd);
    }

    #[test]
    fn percent_decoding_restores_uri_list_paths() {
        assert_eq!(
            percent_decoded("/home/o/Screen%20Shot%201.png"),
            "/home/o/Screen Shot 1.png"
        );
        assert_eq!(percent_decoded("/caf%C3%A9.png"), "/café.png");
        assert_eq!(percent_decoded("/plain.png"), "/plain.png");
        assert_eq!(percent_decoded("100%"), "100%");
        assert_eq!(percent_decoded("50%zz"), "50%zz");
    }

    #[test]
    fn stash_refuses_an_image_over_the_read_cap() {
        let cwd = std::env::temp_dir().join(format!("ufo-stash-cap-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&cwd);
        std::fs::create_dir_all(&cwd).unwrap();
        let refused =
            stash_image(&vec![0u8; IMAGE_MAX_BYTES + 1], &cwd, "conversation").unwrap_err();
        assert_eq!(refused, "The pasted image is 5.0 MB, over the 5 MB cap.");
        assert!(!cwd.join(RUNS_DIR).exists());
        let _ = std::fs::remove_dir_all(&cwd);
    }
}
