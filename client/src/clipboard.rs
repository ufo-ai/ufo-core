//! The OS clipboard behind Ctrl+V: an image wins, text otherwise, read through the platform's
//! own tool so the static binary carries no clipboard stack.

use std::path::Path;
use std::process::{Command, Output, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Duration, Instant};

use crate::ops::fileops::fs_read::IMAGE_MAX_BYTES;

const STASH_DIR: &str = ".ufo/images";
const TOOL_DEADLINE: Duration = Duration::from_secs(5);

/// What the clipboard held.
pub enum Clip {
    Image(Vec<u8>),
    Text(String),
    Empty,
}

/// Read the clipboard: image bytes when one is held, text otherwise.
pub fn read() -> Result<Clip, String> {
    if let Some(bytes) = read_image()? {
        return Ok(Clip::Image(bytes));
    }
    let text = read_text()?;
    if text.is_empty() {
        return Ok(Clip::Empty);
    }
    Ok(Clip::Text(text))
}

/// Save pasted image bytes under the workspace's stash directory, where the agent's
/// workspace-scoped read reaches them; answers the workspace-relative path the message names.
pub fn stash_image(bytes: &[u8], cwd: &Path) -> Result<String, String> {
    static STASHED: AtomicUsize = AtomicUsize::new(1);
    if bytes.len() > IMAGE_MAX_BYTES {
        return Err(format!(
            "The pasted image is {:.1} MB, over the {} MB cap.",
            bytes.len() as f64 / (1024.0 * 1024.0),
            IMAGE_MAX_BYTES / (1024 * 1024)
        ));
    }
    let at = STASHED.fetch_add(1, Ordering::Relaxed);
    let relative = format!("{STASH_DIR}/image-{}-{at}.png", std::process::id());
    let path = cwd.join(&relative);
    let parent = path.parent().expect("the stash path names a directory");
    std::fs::create_dir_all(parent)
        .and_then(|()| std::fs::write(&path, bytes))
        .map_err(|error| format!("could not save the pasted image: {error}"))?;
    Ok(relative)
}

/// Remove this session's stashed images, and the stash directories once nothing else is in them.
pub fn sweep_stash(cwd: &Path) {
    let dir = cwd.join(STASH_DIR);
    let own = format!("image-{}-", std::process::id());
    let Ok(entries) = std::fs::read_dir(&dir) else {
        return;
    };
    for entry in entries.flatten() {
        if entry.file_name().to_string_lossy().starts_with(&own) {
            let _ = std::fs::remove_file(entry.path());
        }
    }
    let _ = std::fs::remove_dir(&dir);
    let _ = std::fs::remove_dir(dir.parent().expect("the stash sits inside .ufo"));
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
    fn stash_answers_a_workspace_relative_path_and_sweep_clears_it() {
        let cwd = std::env::temp_dir().join(format!("ufo-stash-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&cwd);
        std::fs::create_dir_all(&cwd).unwrap();
        let relative = stash_image(&[1, 2, 3], &cwd).unwrap();
        assert!(
            relative.starts_with(".ufo/images/image-") && relative.ends_with(".png"),
            "{relative}"
        );
        assert_eq!(std::fs::read(cwd.join(&relative)).unwrap(), [1, 2, 3]);
        sweep_stash(&cwd);
        assert!(!cwd.join(".ufo").exists());
        let _ = std::fs::remove_dir_all(&cwd);
    }

    #[test]
    fn stash_refuses_an_image_over_the_read_cap() {
        let cwd = std::env::temp_dir().join(format!("ufo-stash-cap-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&cwd);
        std::fs::create_dir_all(&cwd).unwrap();
        let refused = stash_image(&vec![0u8; IMAGE_MAX_BYTES + 1], &cwd).unwrap_err();
        assert_eq!(refused, "The pasted image is 5.0 MB, over the 5 MB cap.");
        assert!(!cwd.join(".ufo").exists());
        let _ = std::fs::remove_dir_all(&cwd);
    }
}
