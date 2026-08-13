//! Role-named colors resolved once at startup: truecolor where the terminal speaks it, the
//! 256-cube otherwise, and no color at all under `NO_COLOR`/plain mode.
//!
//! Prose keeps the terminal's own foreground — body text, headings, fenced code — so a member
//! whose background was read wrong, or never read at all, still reads every word at the contrast
//! their terminal was configured for. Only the signal roles carry a hue, and every hue is chosen
//! twice: bright on a dark background, dark and saturated on a light one.

#[cfg(unix)]
use std::time::{Duration, Instant};

use ratatui::style::{Color, Modifier, Style};

/// How much color the terminal takes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ColorMode {
    TrueColor,
    Ansi256,
    Plain,
}

/// Which scheme the terminal background reads as.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Scheme {
    Dark,
    Light,
}

/// Every color a screen may use, named by role. A new color is a new field here, never an
/// inline value at a call site.
#[derive(Debug, Clone)]
pub struct Theme {
    pub mode: ColorMode,
    pub scheme: Scheme,
    pub text: Style,
    pub muted: Style,
    pub accent: Style,
    pub prompt: Style,
    pub member: Style,
    pub error: Style,
    pub warning: Style,
    pub heading: Style,
    pub code: Style,
    pub code_block: Style,
    pub link: Style,
    pub quote: Style,
    pub list_bullet: Style,
    pub rule: Style,
    pub tool_title: Style,
    pub tool_output: Style,
    pub diff_added: Style,
    pub diff_removed: Style,
    pub diff_context: Style,
    pub selected: Style,
    pub queued: Style,
}

type Rgb = (u8, u8, u8);

#[derive(Debug, Clone, Copy)]
struct Palette {
    muted: Rgb,
    accent: Rgb,
    prompt: Rgb,
    error: Rgb,
    warning: Rgb,
    code: Rgb,
    link: Rgb,
    quote: Rgb,
    rule: Rgb,
    tool_output: Rgb,
    diff_added: Rgb,
    diff_removed: Rgb,
    diff_context: Rgb,
}

const DARK: Palette = Palette {
    muted: (0x8a, 0x8a, 0x99),
    accent: (0x5f, 0xd7, 0xff),
    prompt: (0xff, 0x87, 0xff),
    error: (0xff, 0x6b, 0x6b),
    warning: (0xff, 0xd7, 0x5f),
    code: (0xff, 0xb8, 0x6c),
    link: (0x82, 0xaa, 0xff),
    quote: (0xa0, 0xa0, 0xb4),
    rule: (0x45, 0x45, 0x52),
    tool_output: (0x9a, 0x9a, 0xa8),
    diff_added: (0x7e, 0xe7, 0x87),
    diff_removed: (0xff, 0x7b, 0x72),
    diff_context: (0x6f, 0x6f, 0x7d),
};

const LIGHT: Palette = Palette {
    muted: (0x5c, 0x63, 0x70),
    accent: (0x0e, 0x74, 0x90),
    prompt: (0xa2, 0x1c, 0xaf),
    error: (0xb9, 0x1c, 0x1c),
    warning: (0xa1, 0x62, 0x07),
    code: (0x9a, 0x34, 0x12),
    link: (0x1d, 0x4e, 0xd8),
    quote: (0x52, 0x52, 0x5b),
    rule: (0xb4, 0xb4, 0xbe),
    tool_output: (0x4b, 0x55, 0x63),
    diff_added: (0x11, 0x63, 0x29),
    diff_removed: (0xa4, 0x0e, 0x26),
    diff_context: (0x71, 0x71, 0x7a),
};

impl Theme {
    /// Resolve the palette for the running terminal: mode from `COLORTERM`/`TERM`, scheme from
    /// [`detect_background`], plain under `NO_COLOR`.
    ///
    /// Inherits that function's constraint — call this once, at startup, before the input event
    /// loop exists. A terminal that names no background gets the dark palette.
    pub fn detect(plain: bool) -> Theme {
        let mode = if plain || std::env::var_os("NO_COLOR").is_some() {
            ColorMode::Plain
        } else {
            let colorterm = std::env::var("COLORTERM").unwrap_or_default();
            let term = std::env::var("TERM").unwrap_or_default();
            if colorterm.contains("truecolor") || colorterm.contains("24bit") {
                ColorMode::TrueColor
            } else if term.contains("256color") {
                ColorMode::Ansi256
            } else {
                ColorMode::Plain
            }
        };
        let scheme = match mode {
            ColorMode::Plain => Scheme::Dark,
            _ => detect_background().unwrap_or(Scheme::Dark),
        };
        Theme::for_mode(mode, scheme)
    }

    /// The palette for one mode and scheme.
    pub fn for_mode(mode: ColorMode, scheme: Scheme) -> Theme {
        let palette = match scheme {
            Scheme::Dark => DARK,
            Scheme::Light => LIGHT,
        };
        let plain = Style::new();
        let dim = plain.add_modifier(Modifier::DIM);
        let hue = |rgb: Rgb| match mode {
            ColorMode::TrueColor => plain.fg(Color::Rgb(rgb.0, rgb.1, rgb.2)),
            ColorMode::Ansi256 => plain.fg(Color::Indexed(rgb_to_256(rgb.0, rgb.1, rgb.2))),
            ColorMode::Plain => plain,
        };
        let recede = |rgb: Rgb| match mode {
            ColorMode::Plain => dim,
            _ => hue(rgb),
        };
        Theme {
            mode,
            scheme,
            text: plain,
            muted: recede(palette.muted),
            accent: hue(palette.accent),
            prompt: hue(palette.prompt),
            member: plain.add_modifier(Modifier::BOLD),
            error: match mode {
                ColorMode::Plain => plain.add_modifier(Modifier::BOLD),
                _ => hue(palette.error),
            },
            warning: hue(palette.warning),
            heading: plain.add_modifier(Modifier::BOLD),
            code: hue(palette.code),
            code_block: plain,
            link: hue(palette.link).add_modifier(Modifier::UNDERLINED),
            quote: recede(palette.quote).add_modifier(Modifier::ITALIC),
            list_bullet: hue(palette.accent),
            rule: recede(palette.rule),
            tool_title: recede(palette.tool_output),
            tool_output: recede(palette.tool_output),
            diff_added: hue(palette.diff_added),
            diff_removed: hue(palette.diff_removed),
            diff_context: recede(palette.diff_context),
            selected: match mode {
                ColorMode::Plain => plain.add_modifier(Modifier::REVERSED),
                _ => hue(palette.prompt).add_modifier(Modifier::BOLD),
            },
            queued: dim.add_modifier(Modifier::ITALIC),
        }
    }
}

const CUBE_LEVELS: [u8; 6] = [0, 95, 135, 175, 215, 255];
const CUBE_FIRST: u8 = 16;
const GRAY_FIRST: u8 = 232;
const GRAY_BASE: i32 = 8;
const GRAY_STEP: i32 = 10;
const GRAY_STEPS: i32 = 24;
const NEUTRAL_SPREAD: u8 = 12;

/// The xterm-256 index nearest an RGB value: the 6×6×6 cube (16..=231) or the 24-step gray ramp
/// (232..=255), whichever sits closer under a green-weighted distance. A near-neutral value takes
/// the ramp where the two tie, since the ramp is ten times finer there than the cube's six levels.
/// Indices 0..=15 are never answered — a terminal remaps those, so the same index is a different
/// color on the next member's screen.
pub fn rgb_to_256(r: u8, g: u8, b: u8) -> u8 {
    let level = |value: u8| {
        CUBE_LEVELS
            .iter()
            .enumerate()
            .fold(0usize, |best, (index, level)| {
                if value.abs_diff(*level) < value.abs_diff(CUBE_LEVELS[best]) {
                    index
                } else {
                    best
                }
            })
    };
    let (red, green, blue) = (level(r), level(g), level(b));
    let cube_index = CUBE_FIRST as usize + 36 * red + 6 * green + blue;
    let cube_distance = weighted_distance(
        (r, g, b),
        (CUBE_LEVELS[red], CUBE_LEVELS[green], CUBE_LEVELS[blue]),
    );

    let mean = (2 * r as i32 + 4 * g as i32 + 3 * b as i32) / 9;
    let step = ((mean - GRAY_BASE + GRAY_STEP / 2) / GRAY_STEP).clamp(0, GRAY_STEPS - 1);
    let tone = (GRAY_BASE + GRAY_STEP * step) as u8;
    let gray_distance = weighted_distance((r, g, b), (tone, tone, tone));

    let spread = r.max(g).max(b) - r.min(g).min(b);
    let take_gray = if spread <= NEUTRAL_SPREAD {
        gray_distance <= cube_distance
    } else {
        gray_distance < cube_distance
    };
    if take_gray {
        GRAY_FIRST + step as u8
    } else {
        cube_index as u8
    }
}

fn weighted_distance(left: Rgb, right: Rgb) -> u32 {
    let red = left.0.abs_diff(right.0) as u32;
    let green = left.1.abs_diff(right.1) as u32;
    let blue = left.2.abs_diff(right.2) as u32;
    2 * red * red + 4 * green * green + 3 * blue * blue
}

const LIGHT_LUMA: f32 = 0.5;

fn luma(rgb: Rgb) -> f32 {
    (0.2126 * rgb.0 as f32 + 0.7152 * rgb.1 as f32 + 0.0722 * rgb.2 as f32) / 255.0
}

/// Ask the terminal what its background is and answer the scheme that implies, falling back to
/// what `COLORFGBG` states.
///
/// Call this exactly once, at startup, **before the input event loop exists**: it puts the tty in
/// raw mode for the length of the query, writes OSC 11, and reads the answer straight off the
/// tty — a reader running alongside would swallow the reply, and a reply arriving after this
/// returns would land in the member's first keystrokes. The query carries a primary device
/// attributes request behind it, which every terminal answers, so a terminal that ignores OSC 11
/// is known to have ignored it instead of being waited on, and nothing is left in the buffer.
///
/// Answers `None` when there is no tty, when nothing replies within 150ms, or on any I/O
/// failure; raw mode is restored to what it was either way. The wait is `select` over a
/// non-blocking descriptor of its own: Darwin's `poll` answers `POLLNVAL` for a tty the instant
/// it is asked, so a client that trusted it would read a terminal that never spoke and hang
/// there forever.
pub fn detect_background() -> Option<Scheme> {
    query_background().or_else(|| scheme_from_colorfgbg(&std::env::var("COLORFGBG").ok()?))
}

#[cfg(unix)]
const BACKGROUND_QUERY: &[u8] = b"\x1b]11;?\x1b\\\x1b[c";
#[cfg(unix)]
const BACKGROUND_REPLY_TIMEOUT: Duration = Duration::from_millis(150);

#[cfg(unix)]
fn query_background() -> Option<Scheme> {
    use crossterm::terminal;

    let mut tty = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open("/dev/tty")
        .ok()?;
    let was_raw = terminal::is_raw_mode_enabled().ok()?;
    if !was_raw {
        terminal::enable_raw_mode().ok()?;
    }
    let reply = read_background_reply(&mut tty);
    if !was_raw {
        let _ = terminal::disable_raw_mode();
    }
    scheme_from_osc11(&reply?)
}

#[cfg(unix)]
fn read_background_reply(tty: &mut std::fs::File) -> Option<String> {
    use std::io::{Read, Write};
    use std::os::fd::AsRawFd;

    let fd = tty.as_raw_fd();
    let flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
    if flags < 0 || unsafe { libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0 {
        return None;
    }
    tty.write_all(BACKGROUND_QUERY).ok()?;
    tty.flush().ok()?;
    let deadline = Instant::now() + BACKGROUND_REPLY_TIMEOUT;
    let mut reply = Vec::new();
    while !attributes_answered(&reply) {
        let left = deadline.saturating_duration_since(Instant::now());
        if left.is_zero() || !readable_within(fd, left) {
            break;
        }
        let mut chunk = [0u8; 64];
        match tty.read(&mut chunk) {
            Ok(0) => break,
            Ok(count) => reply.extend_from_slice(&chunk[..count]),
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => continue,
            Err(_) => break,
        }
    }
    Some(String::from_utf8_lossy(&reply).into_owned())
}

#[cfg(unix)]
fn readable_within(fd: std::os::fd::RawFd, window: Duration) -> bool {
    if fd < 0 || fd as usize >= libc::FD_SETSIZE {
        return false;
    }
    let mut readable: libc::fd_set = unsafe { std::mem::zeroed() };
    unsafe { libc::FD_SET(fd, &mut readable) };
    let mut left = libc::timeval {
        tv_sec: window.as_secs() as libc::time_t,
        tv_usec: window.subsec_micros() as libc::suseconds_t,
    };
    let ready = unsafe {
        libc::select(
            fd + 1,
            &mut readable,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            &mut left,
        )
    };
    ready > 0 && unsafe { libc::FD_ISSET(fd, &readable) }
}

#[cfg(unix)]
fn attributes_answered(reply: &[u8]) -> bool {
    reply
        .windows(2)
        .rposition(|pair| pair == b"\x1b[")
        .is_some_and(|start| reply[start..].contains(&b'c'))
}

#[cfg(not(unix))]
fn query_background() -> Option<Scheme> {
    None
}

/// The scheme an OSC 11 reply states: `\x1b]11;rgb:1e1e/1e1e/2e2e\x1b\\`, `#1e1e2e`, and every
/// channel width X11 spells those in. `None` when the text carries no color.
pub fn scheme_from_osc11(reply: &str) -> Option<Scheme> {
    let color = parse_osc11_color(reply)?;
    Some(if luma(color) > LIGHT_LUMA {
        Scheme::Light
    } else {
        Scheme::Dark
    })
}

fn parse_osc11_color(reply: &str) -> Option<Rgb> {
    if let Some(rest) = reply.split("rgb").nth(1) {
        let rest = rest.strip_prefix('a').unwrap_or(rest);
        if let Some(channels) = rest.strip_prefix(':') {
            let mut fields = channels.split('/');
            return Some((
                scale_channel(fields.next()?)?,
                scale_channel(fields.next()?)?,
                scale_channel(fields.next()?)?,
            ));
        }
    }
    let digits: String = reply
        .split('#')
        .nth(1)?
        .chars()
        .take_while(char::is_ascii_hexdigit)
        .collect();
    if digits.is_empty() || !digits.len().is_multiple_of(3) || digits.len() > 12 {
        return None;
    }
    let width = digits.len() / 3;
    Some((
        scale_channel(&digits[..width])?,
        scale_channel(&digits[width..2 * width])?,
        scale_channel(&digits[2 * width..])?,
    ))
}

fn scale_channel(field: &str) -> Option<u8> {
    let digits: String = field.chars().take_while(char::is_ascii_hexdigit).collect();
    if digits.is_empty() || digits.len() > 4 {
        return None;
    }
    let value = u32::from_str_radix(&digits, 16).ok()?;
    let full = (1u32 << (4 * digits.len())) - 1;
    Some((value * 255 / full) as u8)
}

/// The scheme `COLORFGBG` states, read off its background field — the last one, so `15;0` and
/// rxvt's `0;default;15` both answer. `None` where the value names no background color index.
pub fn scheme_from_colorfgbg(value: &str) -> Option<Scheme> {
    let background: u8 = value.rsplit(';').next()?.trim().parse().ok()?;
    match background {
        0..=6 | 8 => Some(Scheme::Dark),
        7 | 9..=15 => Some(Scheme::Light),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const TEXT_ROLES: [fn(&Palette) -> Rgb; 12] = [
        |p| p.muted,
        |p| p.accent,
        |p| p.prompt,
        |p| p.error,
        |p| p.warning,
        |p| p.code,
        |p| p.link,
        |p| p.quote,
        |p| p.tool_output,
        |p| p.diff_added,
        |p| p.diff_removed,
        |p| p.diff_context,
    ];

    #[test]
    fn rgb_to_256_maps_primaries_onto_the_cube() {
        assert_eq!(rgb_to_256(0, 0, 0), 16);
        assert_eq!(rgb_to_256(255, 0, 0), 196);
        assert_eq!(rgb_to_256(0, 255, 0), 46);
        assert_eq!(rgb_to_256(0, 0, 255), 21);
        assert_eq!(rgb_to_256(255, 255, 255), 231);
        assert_eq!(rgb_to_256(0x5f, 0xd7, 0xff), 81);
        assert_eq!(rgb_to_256(0xff, 0x87, 0xff), 213);
    }

    #[test]
    fn rgb_to_256_maps_gray_onto_the_ramp() {
        assert_eq!(rgb_to_256(128, 128, 128), 244);
        assert_eq!(rgb_to_256(18, 18, 18), 233);
        assert_eq!(rgb_to_256(238, 238, 238), 255);
        assert_eq!(rgb_to_256(126, 128, 130), 244);
        assert_eq!(rgb_to_256(95, 95, 95), 59);
    }

    #[test]
    fn ansi256_derives_from_the_truecolor_palette() {
        let truecolor = Theme::for_mode(ColorMode::TrueColor, Scheme::Dark);
        let indexed = Theme::for_mode(ColorMode::Ansi256, Scheme::Dark);
        assert_eq!(truecolor.accent.fg, Some(Color::Rgb(0x5f, 0xd7, 0xff)));
        assert_eq!(indexed.accent.fg, Some(Color::Indexed(81)));
        for role in TEXT_ROLES {
            let (r, g, b) = role(&DARK);
            assert!(rgb_to_256(r, g, b) >= CUBE_FIRST);
            let (r, g, b) = role(&LIGHT);
            assert!(rgb_to_256(r, g, b) >= CUBE_FIRST);
        }
    }

    #[test]
    fn schemes_carry_distinct_hues() {
        let dark = Theme::for_mode(ColorMode::TrueColor, Scheme::Dark);
        let light = Theme::for_mode(ColorMode::TrueColor, Scheme::Light);
        assert_ne!(dark.accent.fg, light.accent.fg);
        assert_ne!(dark.prompt.fg, light.prompt.fg);
        assert_ne!(dark.error.fg, light.error.fg);
        assert_ne!(dark.muted.fg, light.muted.fg);
        for role in TEXT_ROLES {
            assert_ne!(role(&DARK), role(&LIGHT));
        }
    }

    #[test]
    fn every_hue_reads_on_its_own_scheme() {
        for role in TEXT_ROLES {
            assert!(luma(role(&DARK)) > 0.35, "{:?} is too dark", role(&DARK));
            assert!(luma(role(&LIGHT)) < 0.55, "{:?} is too light", role(&LIGHT));
        }
        assert!(luma(LIGHT.rule) > luma(DARK.rule));
    }

    #[test]
    fn prose_rides_the_terminal_foreground() {
        for scheme in [Scheme::Dark, Scheme::Light] {
            let theme = Theme::for_mode(ColorMode::TrueColor, scheme);
            assert_eq!(theme.text.fg, None);
            assert_eq!(theme.heading.fg, None);
            assert_eq!(theme.code_block.fg, None);
            assert!(theme.heading.add_modifier.contains(Modifier::BOLD));
        }
    }

    #[test]
    fn plain_mode_styles_carry_no_color() {
        for scheme in [Scheme::Dark, Scheme::Light] {
            let theme = Theme::for_mode(ColorMode::Plain, scheme);
            for style in [
                theme.text,
                theme.muted,
                theme.accent,
                theme.prompt,
                theme.error,
                theme.warning,
                theme.heading,
                theme.code,
                theme.code_block,
                theme.link,
                theme.quote,
                theme.list_bullet,
                theme.rule,
                theme.tool_title,
                theme.tool_output,
                theme.diff_added,
                theme.diff_removed,
                theme.diff_context,
                theme.selected,
                theme.queued,
            ] {
                assert_eq!(style.fg, None);
                assert_eq!(style.bg, None);
            }
            assert!(theme.muted.add_modifier.contains(Modifier::DIM));
            assert!(theme.selected.add_modifier.contains(Modifier::REVERSED));
            assert!(theme.error.add_modifier.contains(Modifier::BOLD));
        }
    }

    #[test]
    fn osc11_reply_names_the_background() {
        assert_eq!(
            scheme_from_osc11("\x1b]11;rgb:ffff/ffff/ffff\x1b\\"),
            Some(Scheme::Light)
        );
        assert_eq!(
            scheme_from_osc11("\x1b]11;rgb:1e1e/1e1e/2e2e\x07"),
            Some(Scheme::Dark)
        );
        assert_eq!(scheme_from_osc11("#1e1e2e"), Some(Scheme::Dark));
        assert_eq!(scheme_from_osc11("#fdf6e3"), Some(Scheme::Light));
        assert_eq!(scheme_from_osc11("rgb:f/f/f"), Some(Scheme::Light));
        assert_eq!(
            scheme_from_osc11("rgba:0000/2b2b/3636/ffff"),
            Some(Scheme::Dark)
        );
        assert_eq!(scheme_from_osc11("\x1b[?62;c"), None);
        assert_eq!(scheme_from_osc11(""), None);
        assert_eq!(scheme_from_osc11("rgb:zz/zz/zz"), None);
    }

    #[test]
    fn osc11_channels_scale_to_eight_bits() {
        assert_eq!(parse_osc11_color("rgb:1e1e/1e1e/2e2e"), Some((30, 30, 46)));
        assert_eq!(parse_osc11_color("#1e1e2e"), Some((30, 30, 46)));
        assert_eq!(parse_osc11_color("#fff"), Some((255, 255, 255)));
        assert_eq!(parse_osc11_color("rgb:00/80/ff"), Some((0, 128, 255)));
        assert_eq!(parse_osc11_color("#12345"), None);
    }

    #[test]
    fn colorfgbg_names_the_background() {
        assert_eq!(scheme_from_colorfgbg("15;0"), Some(Scheme::Dark));
        assert_eq!(scheme_from_colorfgbg("0;15"), Some(Scheme::Light));
        assert_eq!(scheme_from_colorfgbg("0;default;15"), Some(Scheme::Light));
        assert_eq!(scheme_from_colorfgbg("15;default;0"), Some(Scheme::Dark));
        assert_eq!(scheme_from_colorfgbg("7;8"), Some(Scheme::Dark));
        assert_eq!(scheme_from_colorfgbg("0;7"), Some(Scheme::Light));
        assert_eq!(scheme_from_colorfgbg("default;default"), None);
        assert_eq!(scheme_from_colorfgbg("15;99"), None);
        assert_eq!(scheme_from_colorfgbg(""), None);
    }

    #[cfg(unix)]
    #[test]
    fn attributes_reply_ends_the_read() {
        assert!(!attributes_answered(b""));
        assert!(!attributes_answered(b"\x1b]11;rgb:1e1e/1e1e/2e2e\x1b\\"));
        assert!(!attributes_answered(
            b"\x1b]11;rgb:cccc/cccc/cccc\x1b\\\x1b[?6"
        ));
        assert!(attributes_answered(
            b"\x1b]11;rgb:cccc/cccc/cccc\x1b\\\x1b[?62;1;4c"
        ));
        assert!(attributes_answered(b"\x1b[?1;2c"));
    }
}
