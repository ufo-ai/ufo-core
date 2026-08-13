//! Terminal integrations spoken in OSC: hyperlinks, clipboard, and inline images — each gated
//! by a detected capability, because an escape a terminal swallows is content the member never
//! sees.

use std::io::Read;
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use base64::engine::general_purpose::STANDARD;
use base64::Engine;

const KITTY_CHUNK: usize = 4096;
const KITTY_ID_CEILING: u32 = 0xffff_fffe;
const TMUX_PROBE_WAIT: Duration = Duration::from_millis(250);
const TMUX_PROBE_POLL: Duration = Duration::from_millis(5);
const TMUX_HYPERLINK_FEATURE: &str = "hyperlinks";
const PNG_SIGNATURE: [u8; 8] = [0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a];
const PNG_HEADER_LEN: usize = 24;
const JPEG_FILL: u8 = 0xff;
const JPEG_SOI: u8 = 0xd8;
const JPEG_EOI: u8 = 0xd9;
const JPEG_SOS: u8 = 0xda;
const JPEG_TEM: u8 = 0x01;
const JPEG_RST_FIRST: u8 = 0xd0;
const JPEG_RST_LAST: u8 = 0xd7;
const JPEG_SOF_FIRST: u8 = 0xc0;
const JPEG_SOF_LAST: u8 = 0xc2;

/// What the running terminal is known to honor, sniffed from the environment once.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Caps {
    pub hyperlinks: bool,
    pub osc52: bool,
    pub images: ImageProtocol,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ImageProtocol {
    None,
    Kitty,
    Iterm2,
}

/// The terminal-identifying variables the capability matrix reads, taken once so the matrix is a
/// pure function of them.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct TermEnv {
    pub term: String,
    pub term_program: String,
    pub tmux: bool,
    pub wt_session: bool,
    pub kitty_window_id: bool,
    pub alacritty: bool,
}

impl TermEnv {
    /// Read the running process's terminal environment.
    pub fn snapshot() -> TermEnv {
        TermEnv {
            term: std::env::var("TERM").unwrap_or_default(),
            term_program: std::env::var("TERM_PROGRAM").unwrap_or_default(),
            tmux: std::env::var_os("TMUX").is_some(),
            wt_session: std::env::var_os("WT_SESSION").is_some(),
            kitty_window_id: std::env::var_os("KITTY_WINDOW_ID").is_some(),
            alacritty: std::env::var_os("ALACRITTY_WINDOW_ID").is_some()
                || std::env::var_os("ALACRITTY_SOCKET").is_some(),
        }
    }
}

impl Caps {
    /// Sniff the environment: known terminals get their known features, unknown terminals get
    /// none — a swallowed OSC 8 renders the URL invisible, so the default is off. Under tmux the
    /// hyperlink answer comes from tmux itself, which alone knows what the outer terminal spoke.
    pub fn detect() -> Caps {
        let env = TermEnv::snapshot();
        let mut caps = caps_for(&env);
        if env.tmux {
            caps.hyperlinks = tmux_passes_hyperlinks();
        }
        caps
    }
}

/// The capability matrix, pure over a snapshot. Under tmux no image protocol reaches the outer
/// terminal and hyperlinks wait on the probe [`Caps::detect`] runs; under screen nothing passes
/// through at all.
pub fn caps_for(env: &TermEnv) -> Caps {
    let none = Caps {
        hyperlinks: false,
        osc52: false,
        images: ImageProtocol::None,
    };
    if env.term.starts_with("screen") && !env.tmux {
        return none;
    }
    let caps = match identify(env) {
        Terminal::Kitty | Terminal::Ghostty | Terminal::WezTerm | Terminal::Warp => Caps {
            hyperlinks: true,
            osc52: true,
            images: ImageProtocol::Kitty,
        },
        Terminal::Iterm2 => Caps {
            hyperlinks: true,
            osc52: true,
            images: ImageProtocol::Iterm2,
        },
        Terminal::Vscode | Terminal::Windows | Terminal::Alacritty => Caps {
            hyperlinks: true,
            osc52: true,
            images: ImageProtocol::None,
        },
        Terminal::Unknown => none,
    };
    if env.tmux {
        return Caps {
            hyperlinks: false,
            images: ImageProtocol::None,
            ..caps
        };
    }
    caps
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Terminal {
    Kitty,
    Ghostty,
    WezTerm,
    Warp,
    Iterm2,
    Vscode,
    Windows,
    Alacritty,
    Unknown,
}

fn identify(env: &TermEnv) -> Terminal {
    let program = env.term_program.to_ascii_lowercase();
    let term = env.term.to_ascii_lowercase();
    match program.as_str() {
        "kitty" => Terminal::Kitty,
        "ghostty" => Terminal::Ghostty,
        "wezterm" => Terminal::WezTerm,
        "warpterminal" => Terminal::Warp,
        "iterm.app" => Terminal::Iterm2,
        "vscode" => Terminal::Vscode,
        _ if env.kitty_window_id || term.contains("kitty") => Terminal::Kitty,
        _ if term.contains("ghostty") => Terminal::Ghostty,
        _ if term.contains("wezterm") => Terminal::WezTerm,
        _ if env.wt_session => Terminal::Windows,
        _ if env.alacritty || term.contains("alacritty") => Terminal::Alacritty,
        _ => Terminal::Unknown,
    }
}

fn tmux_passes_hyperlinks() -> bool {
    let spawned = Command::new("tmux")
        .args(["display-message", "-p", "#{client_termfeatures}"])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn();
    let Ok(mut child) = spawned else {
        return false;
    };
    let deadline = Instant::now() + TMUX_PROBE_WAIT;
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if Instant::now() < deadline => thread::sleep(TMUX_PROBE_POLL),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return false;
            }
        }
    }
    let mut features = String::new();
    if let Some(mut pipe) = child.stdout.take() {
        let _ = pipe.read_to_string(&mut features);
    }
    features.contains(TMUX_HYPERLINK_FEATURE)
}

/// An OSC 8 hyperlink, BEL-terminated — some terminals only make BEL-terminated links
/// clickable. Answers the plain text when hyperlinks are off.
pub fn hyperlink(caps: Caps, url: &str, text: &str) -> String {
    if !caps.hyperlinks || url.is_empty() {
        return text.to_string();
    }
    format!("\x1b]8;;{url}\x07{text}\x1b]8;;\x07")
}

/// OSC 52: place `text` on the clipboard. Empty when the terminal is not known to honor it.
pub fn copy_to_clipboard(caps: Caps, text: &str) -> String {
    if !caps.osc52 {
        return String::new();
    }
    let encoded = STANDARD.encode(text.as_bytes());
    format!("\x1b]52;c;{encoded}\x07")
}

/// Emit one image inline at up to `max_width_cells` columns — zero leaves the sizing to the
/// terminal — or nothing when no protocol is spoken or the protocol does not carry this mime.
/// Kitty carries PNG alone; iTerm2 carries any image. The cursor stays where it was, so the
/// caller reserves the rows the image covers from [`png_dimensions`] or [`jpeg_dimensions`].
pub fn inline_image(caps: Caps, bytes: &[u8], mime: &str, max_width_cells: u16) -> String {
    if bytes.is_empty() {
        return String::new();
    }
    match caps.images {
        ImageProtocol::Kitty if mime.eq_ignore_ascii_case("image/png") => {
            kitty_image(bytes, max_width_cells)
        }
        ImageProtocol::Iterm2 if is_image_mime(mime) => iterm2_image(bytes, max_width_cells),
        _ => String::new(),
    }
}

fn is_image_mime(mime: &str) -> bool {
    mime.get(..6)
        .is_some_and(|head| head.eq_ignore_ascii_case("image/"))
}

fn kitty_image(bytes: &[u8], max_width_cells: u16) -> String {
    let payload = STANDARD.encode(bytes);
    let id = image_id();
    let columns = match max_width_cells {
        0 => String::new(),
        cells => format!(",c={cells}"),
    };
    let mut out = String::with_capacity(payload.len() + payload.len() / KITTY_CHUNK * 16 + 64);
    let mut at = 0;
    while at < payload.len() {
        let end = (at + KITTY_CHUNK).min(payload.len());
        let more = u8::from(end < payload.len());
        let chunk = &payload[at..end];
        if at == 0 {
            out.push_str(&format!(
                "\x1b_Ga=T,f=100,q=2,C=1,i={id}{columns},m={more};{chunk}\x1b\\"
            ));
        } else {
            out.push_str(&format!("\x1b_Gm={more};{chunk}\x1b\\"));
        }
        at = end;
    }
    out.push('\n');
    out
}

fn iterm2_image(bytes: &[u8], max_width_cells: u16) -> String {
    let payload = STANDARD.encode(bytes);
    let size = bytes.len();
    let columns = match max_width_cells {
        0 => String::new(),
        cells => format!("width={cells};"),
    };
    format!("\x1b]1337;File=inline=1;size={size};{columns}preserveAspectRatio=1:{payload}\x07\n")
}

fn image_id() -> u32 {
    let mut raw = [0u8; 4];
    getrandom::fill(&mut raw).expect("os randomness is available");
    u32::from_be_bytes(raw) % KITTY_ID_CEILING + 1
}

/// Pixel width and height read from a PNG's IHDR, or `None` when the bytes are not a PNG whose
/// header is intact.
pub fn png_dimensions(bytes: &[u8]) -> Option<(u32, u32)> {
    if bytes.len() < PNG_HEADER_LEN || bytes[..8] != PNG_SIGNATURE || &bytes[12..16] != b"IHDR" {
        return None;
    }
    let width = be_u32(bytes, 16);
    let height = be_u32(bytes, 20);
    (width > 0 && height > 0).then_some((width, height))
}

/// Pixel width and height read from a JPEG's first frame header, or `None` when the bytes are not
/// a JPEG whose markers lead to one intact.
pub fn jpeg_dimensions(bytes: &[u8]) -> Option<(u32, u32)> {
    if bytes.len() < 4 || bytes[0] != JPEG_FILL || bytes[1] != JPEG_SOI {
        return None;
    }
    let mut at = 2;
    while at + 1 < bytes.len() {
        if bytes[at] != JPEG_FILL {
            return None;
        }
        let marker = bytes[at + 1];
        at += 2;
        match marker {
            JPEG_FILL => at -= 1,
            JPEG_TEM | JPEG_SOI | JPEG_RST_FIRST..=JPEG_RST_LAST => {}
            JPEG_EOI | JPEG_SOS => return None,
            JPEG_SOF_FIRST..=JPEG_SOF_LAST => {
                if at + 7 > bytes.len() {
                    return None;
                }
                let height = be_u16(bytes, at + 3);
                let width = be_u16(bytes, at + 5);
                return (width > 0 && height > 0).then_some((width, height));
            }
            _ => {
                if at + 2 > bytes.len() {
                    return None;
                }
                let length = be_u16(bytes, at) as usize;
                if length < 2 {
                    return None;
                }
                at += length;
            }
        }
    }
    None
}

fn be_u32(bytes: &[u8], at: usize) -> u32 {
    u32::from_be_bytes([bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]])
}

fn be_u16(bytes: &[u8], at: usize) -> u32 {
    u32::from(bytes[at]) << 8 | u32::from(bytes[at + 1])
}

#[cfg(test)]
mod tests {
    use super::*;

    const OFF: Caps = Caps {
        hyperlinks: false,
        osc52: false,
        images: ImageProtocol::None,
    };

    fn env(term: &str, program: &str) -> TermEnv {
        TermEnv {
            term: term.to_string(),
            term_program: program.to_string(),
            ..TermEnv::default()
        }
    }

    fn png(width: u32, height: u32) -> Vec<u8> {
        let mut bytes = PNG_SIGNATURE.to_vec();
        bytes.extend_from_slice(&13u32.to_be_bytes());
        bytes.extend_from_slice(b"IHDR");
        bytes.extend_from_slice(&width.to_be_bytes());
        bytes.extend_from_slice(&height.to_be_bytes());
        bytes.extend_from_slice(&[8, 6, 0, 0, 0]);
        bytes
    }

    fn jpeg(width: u16, height: u16) -> Vec<u8> {
        let mut bytes = vec![0xff, JPEG_SOI, 0xff, 0xe0, 0x00, 0x04, 0x00, 0x00];
        bytes.extend_from_slice(&[0xff, JPEG_SOF_FIRST, 0x00, 0x11, 0x08]);
        bytes.extend_from_slice(&height.to_be_bytes());
        bytes.extend_from_slice(&width.to_be_bytes());
        bytes.extend_from_slice(&[0x03, 0x01, 0x22, 0x00]);
        bytes
    }

    fn kitty_escapes(out: &str) -> Vec<&str> {
        out.trim_end_matches('\n')
            .split("\x1b\\")
            .filter(|part| !part.is_empty())
            .map(|part| part.trim_start_matches("\x1b_G"))
            .collect()
    }

    #[test]
    fn hyperlink_falls_back_to_text() {
        assert_eq!(hyperlink(OFF, "https://x", "label"), "label");
        let on = Caps {
            hyperlinks: true,
            ..OFF
        };
        let linked = hyperlink(on, "https://x", "label");
        assert!(linked.starts_with("\x1b]8;;https://x\x07"));
        assert!(linked.ends_with("\x1b]8;;\x07"));
    }

    #[test]
    fn osc52_is_silent_when_unsupported() {
        assert!(copy_to_clipboard(OFF, "text").is_empty());
    }

    #[test]
    fn detect_answers_for_the_running_terminal() {
        let env = TermEnv::snapshot();
        let caps = Caps::detect();
        let matrix = caps_for(&env);
        assert_eq!(caps.osc52, matrix.osc52);
        assert_eq!(caps.images, matrix.images);
        if env.tmux {
            assert_eq!(caps.images, ImageProtocol::None);
        } else {
            assert_eq!(caps.hyperlinks, matrix.hyperlinks);
        }
    }

    #[test]
    fn kitty_terminals_speak_every_feature() {
        let expected = Caps {
            hyperlinks: true,
            osc52: true,
            images: ImageProtocol::Kitty,
        };
        assert_eq!(caps_for(&env("xterm-kitty", "")), expected);
        assert_eq!(caps_for(&env("xterm-ghostty", "ghostty")), expected);
        assert_eq!(caps_for(&env("xterm-256color", "WezTerm")), expected);
        assert_eq!(caps_for(&env("xterm-256color", "WarpTerminal")), expected);
        let marked = TermEnv {
            kitty_window_id: true,
            ..env("xterm-256color", "")
        };
        assert_eq!(caps_for(&marked), expected);
    }

    #[test]
    fn iterm_speaks_its_own_protocol() {
        assert_eq!(
            caps_for(&env("xterm-256color", "iTerm.app")),
            Caps {
                hyperlinks: true,
                osc52: true,
                images: ImageProtocol::Iterm2,
            }
        );
    }

    #[test]
    fn hyperlink_only_terminals_carry_no_images() {
        let expected = Caps {
            hyperlinks: true,
            osc52: true,
            images: ImageProtocol::None,
        };
        assert_eq!(caps_for(&env("xterm-256color", "vscode")), expected);
        assert_eq!(caps_for(&env("alacritty", "")), expected);
        let windows = TermEnv {
            wt_session: true,
            ..env("xterm-256color", "")
        };
        assert_eq!(caps_for(&windows), expected);
        let socketed = TermEnv {
            alacritty: true,
            ..env("xterm-256color", "")
        };
        assert_eq!(caps_for(&socketed), expected);
    }

    #[test]
    fn unknown_terminals_get_nothing() {
        assert_eq!(caps_for(&env("xterm-256color", "")), OFF);
        assert_eq!(caps_for(&env("", "")), OFF);
        assert_eq!(caps_for(&env("vt100", "Apple_Terminal")), OFF);
    }

    #[test]
    fn screen_gets_nothing() {
        assert_eq!(caps_for(&env("screen.xterm-256color", "iTerm.app")), OFF);
        assert_eq!(caps_for(&env("screen-256color", "")), OFF);
    }

    #[test]
    fn tmux_drops_images_and_defers_hyperlinks() {
        let under_tmux = TermEnv {
            tmux: true,
            ..env("screen-256color", "iTerm.app")
        };
        assert_eq!(
            caps_for(&under_tmux),
            Caps {
                hyperlinks: false,
                osc52: true,
                images: ImageProtocol::None,
            }
        );
        let kitty = TermEnv {
            tmux: true,
            ..env("xterm-kitty", "")
        };
        assert_eq!(caps_for(&kitty).images, ImageProtocol::None);
    }

    #[test]
    fn kitty_chunks_the_payload() {
        let caps = Caps {
            images: ImageProtocol::Kitty,
            ..OFF
        };
        let mut bytes = png(64, 64);
        bytes.resize(10_000, 0x5a);
        let out = inline_image(caps, &bytes, "image/png", 40);
        assert!(out.ends_with('\n'));
        let escapes = kitty_escapes(&out);
        assert!(escapes.len() > 2);
        let (keys, payload) = escapes[0].split_once(';').unwrap();
        assert!(keys.starts_with("a=T,f=100,q=2,C=1,i="));
        assert!(keys.contains(",c=40"));
        assert!(keys.ends_with(",m=1"));
        assert!(payload.len() <= KITTY_CHUNK);
        let id: u32 = keys
            .split(',')
            .find_map(|key| key.strip_prefix("i="))
            .unwrap()
            .parse()
            .unwrap();
        assert!((1..=KITTY_ID_CEILING).contains(&id));
        for escape in &escapes[1..escapes.len() - 1] {
            let (keys, payload) = escape.split_once(';').unwrap();
            assert_eq!(keys, "m=1");
            assert_eq!(payload.len(), KITTY_CHUNK);
        }
        let (keys, payload) = escapes.last().unwrap().split_once(';').unwrap();
        assert_eq!(keys, "m=0");
        assert!(!payload.is_empty() && payload.len() <= KITTY_CHUNK);
        let rejoined: String = escapes
            .iter()
            .map(|escape| escape.split_once(';').unwrap().1)
            .collect();
        assert_eq!(STANDARD.decode(rejoined).unwrap(), bytes);
    }

    #[test]
    fn kitty_sends_one_escape_for_a_small_image() {
        let caps = Caps {
            images: ImageProtocol::Kitty,
            ..OFF
        };
        let out = inline_image(caps, &png(8, 8), "image/png", 0);
        let escapes = kitty_escapes(&out);
        assert_eq!(escapes.len(), 1);
        let (keys, _) = escapes[0].split_once(';').unwrap();
        assert!(!keys.contains("c="));
        assert!(keys.ends_with(",m=0"));
    }

    #[test]
    fn kitty_carries_png_alone() {
        let caps = Caps {
            images: ImageProtocol::Kitty,
            ..OFF
        };
        assert!(inline_image(caps, b"\xff\xd8\xff\xe0", "image/jpeg", 40).is_empty());
        assert!(inline_image(caps, b"", "image/png", 40).is_empty());
        assert!(inline_image(OFF, &png(8, 8), "image/png", 40).is_empty());
    }

    #[test]
    fn iterm_carries_any_image() {
        let caps = Caps {
            images: ImageProtocol::Iterm2,
            ..OFF
        };
        let bytes = jpeg(3, 2);
        let out = inline_image(caps, &bytes, "image/jpeg", 12);
        let expected = format!(
            "\x1b]1337;File=inline=1;size={};width=12;preserveAspectRatio=1:{}\x07\n",
            bytes.len(),
            STANDARD.encode(&bytes)
        );
        assert_eq!(out, expected);
        assert!(inline_image(caps, &bytes, "application/pdf", 12).is_empty());
        assert!(inline_image(caps, &bytes, "image/gif", 0)
            .contains(&format!(";size={};preserveAspectRatio=1:", bytes.len())));
    }

    #[test]
    fn png_header_gives_dimensions() {
        assert_eq!(png_dimensions(&png(640, 480)), Some((640, 480)));
        assert_eq!(png_dimensions(&png(0, 480)), None);
        assert_eq!(png_dimensions(&png(640, 480)[..20]), None);
        let mut wrong_chunk = png(640, 480);
        wrong_chunk[13] = b'X';
        assert_eq!(png_dimensions(&wrong_chunk), None);
        assert_eq!(png_dimensions(b"not a png at all, truly"), None);
        assert_eq!(png_dimensions(b""), None);
    }

    #[test]
    fn jpeg_frame_header_gives_dimensions() {
        assert_eq!(jpeg_dimensions(&jpeg(1920, 1080)), Some((1920, 1080)));
        let mut progressive = jpeg(320, 240);
        progressive[9] = 0xc2;
        assert_eq!(jpeg_dimensions(&progressive), Some((320, 240)));
        assert_eq!(jpeg_dimensions(&jpeg(0, 240)), None);
        assert_eq!(jpeg_dimensions(&jpeg(64, 64)[..12]), None);
        assert_eq!(jpeg_dimensions(&[0xff, JPEG_SOI, 0xff, JPEG_SOS]), None);
        assert_eq!(jpeg_dimensions(&png(8, 8)), None);
        assert_eq!(jpeg_dimensions(b""), None);
    }
}
