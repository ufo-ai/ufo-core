//! The one round-trip ufo makes of the terminal at startup: the kitty keyboard flags it takes and
//! the color it draws on, asked in a single write and read once, under a single deadline.
//!
//! The read owns the tty, so it also takes whatever the member typed while the terminal was
//! answering. Those bytes come back as the keys they were, for the loop to replay in order.

use crossterm::event::KeyEvent;
#[cfg(unix)]
use crossterm::event::{KeyCode, KeyModifiers};

use crate::ui::theme::Scheme;

/// What the terminal answered, and what the member typed while it answered.
pub struct Probe {
    pub kitty: bool,
    pub scheme: Option<Scheme>,
    pub typeahead: Vec<KeyEvent>,
}

#[cfg(unix)]
const ESC: u8 = 0x1b;
#[cfg(unix)]
const BEL: u8 = 0x07;

/// Kitty keyboard flags, then the background color, then primary device attributes — the sentinel
/// every terminal answers, so a terminal that ignores the first two is known to have ignored them
/// instead of being waited on.
#[cfg(unix)]
const QUERY: &[u8] = b"\x1b[?u\x1b]11;?\x1b\\\x1b[c";

#[cfg(unix)]
const DEADLINE: std::time::Duration = std::time::Duration::from_millis(150);

impl Probe {
    /// Ask the terminal and read what it says.
    ///
    /// Call this exactly once, at startup, with the tty already in raw mode and **before the
    /// input reader thread exists**: a reader running alongside would race this one for both the
    /// reply and the member's keystrokes. A terminal that says nothing costs the deadline and
    /// answers nothing — no flags, no scheme, no keys.
    #[cfg(unix)]
    pub fn query() -> Probe {
        use std::os::fd::AsRawFd;

        let held = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open("/dev/tty")
            .ok();
        let (read_fd, write_fd) = match &held {
            Some(tty) => (tty.as_raw_fd(), tty.as_raw_fd()),
            None if is_tty(libc::STDIN_FILENO) && is_tty(libc::STDOUT_FILENO) => {
                (libc::STDIN_FILENO, libc::STDOUT_FILENO)
            }
            None => return Probe::unanswered(),
        };
        let said = split(&read_reply(read_fd, write_fd));
        Probe {
            kitty: said.kitty,
            scheme: said.scheme,
            typeahead: keys(&said.typed),
        }
    }

    #[cfg(not(unix))]
    pub fn query() -> Probe {
        Probe::unanswered()
    }

    fn unanswered() -> Probe {
        Probe {
            kitty: false,
            scheme: None,
            typeahead: Vec::new(),
        }
    }
}

#[cfg(unix)]
fn is_tty(fd: std::os::fd::RawFd) -> bool {
    (unsafe { libc::isatty(fd) }) == 1
}

/// Write the query and read until the attributes reply closes it or the deadline runs out. The
/// wait is `select` over a non-blocking descriptor: Darwin's `poll` answers `POLLNVAL` for a tty
/// the instant it is asked, so a client that trusted it would read a terminal that never spoke
/// and hang there forever. The descriptor's flags are put back, since it may be the process's own
/// stdin.
#[cfg(unix)]
fn read_reply(read_fd: std::os::fd::RawFd, write_fd: std::os::fd::RawFd) -> Vec<u8> {
    use std::time::Instant;

    let mut sent = 0;
    while sent < QUERY.len() {
        let wrote =
            unsafe { libc::write(write_fd, QUERY[sent..].as_ptr().cast(), QUERY.len() - sent) };
        if wrote <= 0 {
            return Vec::new();
        }
        sent += wrote as usize;
    }

    let flags = unsafe { libc::fcntl(read_fd, libc::F_GETFL) };
    if flags < 0 || unsafe { libc::fcntl(read_fd, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0 {
        return Vec::new();
    }
    let deadline = Instant::now() + DEADLINE;
    let mut reply = Vec::new();
    loop {
        let left = deadline.saturating_duration_since(Instant::now());
        if left.is_zero() || !readable_within(read_fd, left) {
            break;
        }
        let mut chunk = [0u8; 256];
        let read = unsafe { libc::read(read_fd, chunk.as_mut_ptr().cast(), chunk.len()) };
        if read == 0 {
            break;
        }
        if read < 0 {
            match std::io::Error::last_os_error().kind() {
                std::io::ErrorKind::WouldBlock | std::io::ErrorKind::Interrupted => continue,
                _ => break,
            }
        }
        reply.extend_from_slice(&chunk[..read as usize]);
        if split(&reply).attributes {
            break;
        }
    }
    unsafe { libc::fcntl(read_fd, libc::F_SETFL, flags) };
    reply
}

#[cfg(unix)]
fn readable_within(fd: std::os::fd::RawFd, window: std::time::Duration) -> bool {
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

/// The three answers, and the bytes that were none of them.
#[cfg(unix)]
struct Split {
    kitty: bool,
    scheme: Option<Scheme>,
    attributes: bool,
    typed: Vec<u8>,
}

/// Take the answers out of what was read, leaving the member's own bytes.
///
/// An escape sequence that is not one of the three answers is dropped: an arrow key or a function
/// key would take crossterm's whole parser to name, and half a sequence is worse than none. A
/// sequence still arriving when the buffer ends — down to a bare trailing escape byte, which the
/// deadline or the sentinel cut off mid-sequence — goes the same way, so no escape byte ever
/// reaches the keys. An answer already read stands whatever follows it.
#[cfg(unix)]
fn split(bytes: &[u8]) -> Split {
    use crate::ui::theme::scheme_from_osc11;

    let mut split = Split {
        kitty: false,
        scheme: None,
        attributes: false,
        typed: Vec::new(),
    };
    let mut at = 0;
    while at < bytes.len() {
        if bytes[at] != ESC {
            split.typed.push(bytes[at]);
            at += 1;
            continue;
        }
        match bytes.get(at + 1).copied() {
            None => return split,
            Some(b'[') => {
                let Some(end) = bytes[at + 2..]
                    .iter()
                    .position(|byte| (0x40..=0x7e).contains(byte))
                else {
                    return split;
                };
                let params = &bytes[at + 2..at + 2 + end];
                match bytes[at + 2 + end] {
                    b'u' if params.first() == Some(&b'?') => split.kitty = true,
                    b'c' => split.attributes = true,
                    _ => {}
                }
                at += 3 + end;
            }
            Some(b']') => {
                let Some((payload, len)) = command_string(&bytes[at + 2..]) else {
                    return split;
                };
                if payload.starts_with(b"11;") {
                    split.scheme = scheme_from_osc11(&String::from_utf8_lossy(payload));
                }
                at += 2 + len;
            }
            Some(b'O') if at + 3 > bytes.len() => return split,
            Some(b'O') => at += 3,
            Some(ESC) => at += 1,
            Some(_) => at += 2,
        }
    }
    split
}

/// The payload of an OSC string and the length it occupies, terminated by `ST` or `BEL`.
#[cfg(unix)]
fn command_string(bytes: &[u8]) -> Option<(&[u8], usize)> {
    for (at, byte) in bytes.iter().enumerate() {
        match *byte {
            BEL => return Some((&bytes[..at], at + 1)),
            ESC if bytes.get(at + 1) == Some(&b'\\') => return Some((&bytes[..at], at + 2)),
            ESC => return None,
            _ => {}
        }
    }
    None
}

/// The keys the member's bytes were, decoded the way crossterm decodes them in raw mode: `\r` is
/// Enter, `\n` is Ctrl+J, a control byte is its Ctrl pair, and a character is itself. A truncated
/// multi-byte character is left behind rather than replayed as a replacement glyph, and [`split`]
/// has already taken every escape byte out.
#[cfg(unix)]
fn keys(typed: &[u8]) -> Vec<KeyEvent> {
    let mut keys = Vec::new();
    let mut at = 0;
    while at < typed.len() {
        let byte = typed[at];
        if byte < 0x80 {
            keys.push(ascii_key(byte));
            at += 1;
            continue;
        }
        let width = match byte {
            0xc0..=0xdf => 2,
            0xe0..=0xef => 3,
            0xf0..=0xf7 => 4,
            _ => return keys,
        };
        let Some(text) = typed
            .get(at..at + width)
            .and_then(|character| std::str::from_utf8(character).ok())
        else {
            return keys;
        };
        keys.extend(text.chars().map(char_key));
        at += width;
    }
    keys
}

#[cfg(unix)]
fn ascii_key(byte: u8) -> KeyEvent {
    match byte {
        b'\r' => KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE),
        b'\t' => KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE),
        0x7f => KeyEvent::new(KeyCode::Backspace, KeyModifiers::NONE),
        0x00 => KeyEvent::new(KeyCode::Char(' '), KeyModifiers::CONTROL),
        0x01..=0x1a => KeyEvent::new(
            KeyCode::Char((byte - 0x01 + b'a') as char),
            KeyModifiers::CONTROL,
        ),
        0x1c..=0x1f => KeyEvent::new(
            KeyCode::Char((byte - 0x1c + b'4') as char),
            KeyModifiers::CONTROL,
        ),
        _ => char_key(byte as char),
    }
}

#[cfg(unix)]
fn char_key(character: char) -> KeyEvent {
    let modifiers = if character.is_uppercase() {
        KeyModifiers::SHIFT
    } else {
        KeyModifiers::NONE
    };
    KeyEvent::new(KeyCode::Char(character), modifiers)
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    const KITTY_REPLY: &[u8] = b"\x1b[?0u";
    const COLOR_REPLY: &[u8] = b"\x1b]11;rgb:1e1e/1e1e/2e2e\x1b\\";
    const LIGHT_REPLY: &[u8] = b"\x1b]11;rgb:f5f5/f5f5/f4f4\x07";
    const ATTRIBUTES_REPLY: &[u8] = b"\x1b[?62;1;6c";

    fn typed(bytes: &[u8]) -> String {
        keys(&split(bytes).typed)
            .into_iter()
            .filter_map(|key| match key.code {
                KeyCode::Char(character) => Some(character),
                _ => None,
            })
            .collect()
    }

    #[test]
    fn every_answer_is_read_out_of_one_reply() {
        let mut reply = Vec::new();
        reply.extend_from_slice(KITTY_REPLY);
        reply.extend_from_slice(COLOR_REPLY);
        reply.extend_from_slice(ATTRIBUTES_REPLY);
        let said = split(&reply);
        assert!(said.kitty);
        assert!(said.attributes);
        assert_eq!(said.scheme, Some(Scheme::Dark));
        assert!(said.typed.is_empty(), "no answer reads as a keystroke");
    }

    #[test]
    fn a_light_color_reads_as_the_light_scheme() {
        let said = split(LIGHT_REPLY);
        assert_eq!(said.scheme, Some(Scheme::Light));
        assert!(!said.attributes);
    }

    #[test]
    fn a_terminal_that_answers_nothing_answers_nothing() {
        let said = split(b"");
        assert!(!said.kitty);
        assert!(!said.attributes);
        assert_eq!(said.scheme, None);
    }

    #[test]
    fn only_the_attributes_reply_closes_the_read() {
        let said = split(ATTRIBUTES_REPLY);
        assert!(said.attributes);
        assert!(!said.kitty);
        assert_eq!(said.scheme, None);
        assert!(!split(COLOR_REPLY).attributes);
        assert!(!split(b"\x1b]11;rgb:cccc/cccc/cccc\x1b\\\x1b[?6").attributes);
        assert!(split(b"\x1b]11;rgb:cccc/cccc/cccc\x1b\\\x1b[?62;1;4c").attributes);
    }

    #[test]
    fn typeahead_between_the_answers_replays_in_order() {
        let mut reply = b"he".to_vec();
        reply.extend_from_slice(KITTY_REPLY);
        reply.extend_from_slice(b"ll");
        reply.extend_from_slice(COLOR_REPLY);
        reply.extend_from_slice(b"o");
        reply.extend_from_slice(ATTRIBUTES_REPLY);
        let said = split(&reply);
        assert!(said.kitty);
        assert_eq!(said.scheme, Some(Scheme::Dark));
        assert_eq!(typed(&reply), "hello");
    }

    #[test]
    fn typeahead_carrying_a_c_does_not_close_the_read() {
        let mut reply = KITTY_REPLY.to_vec();
        reply.extend_from_slice(b"abc");
        assert!(!split(&reply).attributes);
    }

    #[test]
    fn an_incomplete_trailing_sequence_is_not_replayed_as_a_fragment() {
        assert_eq!(typed(b"hi\x1b"), "hi");
        assert_eq!(typed(b"hi\x1b["), "hi");
        assert_eq!(typed(b"hi\x1b[1;2"), "hi");
        assert_eq!(typed(b"hi\x1b]11;rgb:1e1e"), "hi");
        assert_eq!(typed(b"hi\x1bO"), "hi");
        assert_eq!(split(b"hi\x1b]11;rgb:1e1e").scheme, None);
    }

    #[test]
    fn a_trailing_escape_is_dropped_with_the_sequence_it_began() {
        let sent = keys(&split(b"hi\x1b").typed);
        assert_eq!(sent.len(), 2, "{sent:?}");
        assert_eq!(typed(b"hi\x1b"), "hi");
        assert!(
            !sent.iter().any(|key| key.code == KeyCode::Esc),
            "a cut-off sequence never lands as an Esc that would stop a turn: {sent:?}"
        );
    }

    #[test]
    fn a_sequence_that_is_not_an_answer_is_dropped() {
        assert_eq!(typed(b"a\x1b[Ab"), "ab");
        assert_eq!(typed(b"a\x1bOPb"), "ab");
        assert_eq!(typed(b"a\x1b[<0;1;1Mb"), "ab");
        assert_eq!(typed(b"a\x1b]0;a title\x07b"), "ab");
        assert_eq!(typed(b"a\x1bxb"), "ab");
    }

    #[test]
    fn control_bytes_replay_as_the_keys_they_are() {
        let sent = keys(&split(b"a\r\t\x7f\x01\x00").typed);
        assert_eq!(
            sent[0],
            KeyEvent::new(KeyCode::Char('a'), KeyModifiers::NONE)
        );
        assert_eq!(sent[1].code, KeyCode::Enter);
        assert_eq!(sent[2].code, KeyCode::Tab);
        assert_eq!(sent[3].code, KeyCode::Backspace);
        assert_eq!(
            sent[4],
            KeyEvent::new(KeyCode::Char('a'), KeyModifiers::CONTROL)
        );
        assert_eq!(
            sent[5],
            KeyEvent::new(KeyCode::Char(' '), KeyModifiers::CONTROL)
        );
    }

    #[test]
    fn a_capital_carries_the_shift_it_was_typed_with() {
        let sent = keys(&split(b"Hi").typed);
        assert_eq!(
            sent[0],
            KeyEvent::new(KeyCode::Char('H'), KeyModifiers::SHIFT)
        );
        assert_eq!(
            sent[1],
            KeyEvent::new(KeyCode::Char('i'), KeyModifiers::NONE)
        );
    }

    #[test]
    fn a_multi_byte_character_replays_as_one_key() {
        assert_eq!(typed("é☃".as_bytes()), "é☃");
        let truncated = &"é".as_bytes()[..1];
        assert!(keys(&split(truncated).typed).is_empty());
    }
}
