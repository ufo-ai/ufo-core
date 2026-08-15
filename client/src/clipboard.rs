//! The OS clipboard behind Ctrl+V: an image wins, text otherwise, read through the platform's
//! own tool so the static binary carries no clipboard stack.

use std::process::{Command, Output};
use std::sync::atomic::{AtomicUsize, Ordering};

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

/// Save pasted image bytes into the session's private workdir, where the agent's read op
/// reaches them until the session ends; answers the path.
pub fn stash_image(bytes: &[u8], dir: &std::path::Path) -> Result<String, String> {
    static STASHED: AtomicUsize = AtomicUsize::new(1);
    let at = STASHED.fetch_add(1, Ordering::Relaxed);
    let path = dir.join(format!("image-{at}.png"));
    std::fs::write(&path, bytes)
        .map_err(|error| format!("could not save the pasted image: {error}"))?;
    Ok(path.display().to_string())
}

fn run(name: &str, args: &[&str]) -> Result<Output, String> {
    Command::new(name).args(args).output().map_err(|error| {
        if cfg!(target_os = "linux") && error.kind() == std::io::ErrorKind::NotFound {
            return format!(
                "{name} is not installed; install wl-clipboard (Wayland) or xclip (X11) to \
                 paste from the clipboard"
            );
        }
        format!("could not run {name}: {error}")
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
    fn stash_lands_the_bytes_in_the_given_directory() {
        let dir = std::env::temp_dir().join(format!("ufo-stash-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = stash_image(&[1, 2, 3], &dir).unwrap();
        assert!(path.starts_with(dir.to_str().unwrap()), "{path}");
        assert!(path.ends_with(".png"), "{path}");
        assert_eq!(std::fs::read(&path).unwrap(), [1, 2, 3]);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
