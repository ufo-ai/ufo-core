//! What persists across sessions under `$UFO_HOME`: the input history the editor walks, and the
//! conversation log the resume picker lists. The hotkey reference the overlay renders lives here
//! too, beside the keys the editor binds.

use std::cmp::Reverse;
use std::collections::HashMap;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

pub const HISTORY_LIMIT: usize = 500;

const CONVERSATIONS_FILE: &str = "conversations";
const COMPACT_SUFFIX: &str = "compacting";
const FIRST_MESSAGE_LIMIT: usize = 120;
const CONVERSATION_LIMIT: usize = 200;
const COMPACT_ABOVE: usize = 400;
const ROW_CHANNEL_WIDTH: usize = 8;
const ROW_AGE_WIDTH: usize = 3;
const ROW_MESSAGE_WIDTH: usize = 60;
const MINUTE: u64 = 60;
const HOUR: u64 = 60 * MINUTE;
const DAY: u64 = 24 * HOUR;
const WEEK: u64 = 7 * DAY;

/// Input history persisted one entry per line, newlines escaped as `\n`.
pub struct History {
    path: PathBuf,
    pub entries: Vec<String>,
}

impl History {
    /// Load history from `home/history`, tolerating absence.
    pub fn load(home: &Path) -> History {
        let path = home.join("history");
        let entries = fs::read_to_string(&path)
            .map(|text| {
                text.lines()
                    .filter(|line| !line.is_empty())
                    .map(|line| line.replace("\\n", "\n"))
                    .collect()
            })
            .unwrap_or_default();
        History { path, entries }
    }

    /// Append one submitted entry and persist, deduplicating the immediate repeat and holding
    /// the file to `HISTORY_LIMIT` entries.
    pub fn push(&mut self, entry: &str) {
        if entry.is_empty() || self.entries.last().map(String::as_str) == Some(entry) {
            return;
        }
        self.entries.push(entry.to_string());
        if self.entries.len() > HISTORY_LIMIT {
            let drop = self.entries.len() - HISTORY_LIMIT;
            self.entries.drain(..drop);
        }
        let rendered: String = self
            .entries
            .iter()
            .map(|line| format!("{}\n", line.replace('\n', "\\n")))
            .collect();
        if let Some(parent) = self.path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        let _ = fs::write(&self.path, rendered);
    }
}

/// One conversation this machine opened, for the resume picker.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PastConversation {
    pub channel: String,
    pub opened_epoch: u64,
    pub first_message: String,
}

/// Record that a conversation opened, as one JSON line under `home/conversations`.
///
/// The log is a convenience, not part of the session: a write that fails is dropped.
pub fn record_conversation(home: &Path, row: &PastConversation) {
    let stored = PastConversation {
        channel: row.channel.clone(),
        opened_epoch: row.opened_epoch,
        first_message: clip(&row.first_message, FIRST_MESSAGE_LIMIT).to_string(),
    };
    let Ok(line) = serde_json::to_string(&stored) else {
        return;
    };
    let _ = fs::create_dir_all(home);
    let Ok(mut file) = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(home.join(CONVERSATIONS_FILE))
    else {
        return;
    };
    let _ = writeln!(file, "{line}");
}

/// The conversations this machine opened, newest first, one row per channel.
///
/// The last line written for a channel is the one kept. A log grown past `COMPACT_ABOVE` lines is
/// rewritten to the rows returned, so the file stays bounded without a separate sweep.
pub fn list_conversations(home: &Path) -> Vec<PastConversation> {
    let path = home.join(CONVERSATIONS_FILE);
    let Ok(text) = fs::read_to_string(&path) else {
        return Vec::new();
    };
    let mut raw = 0;
    let mut at: HashMap<String, usize> = HashMap::new();
    let mut rows: Vec<PastConversation> = Vec::new();
    for line in text.lines().filter(|line| !line.trim().is_empty()) {
        raw += 1;
        let Ok(row) = serde_json::from_str::<PastConversation>(line) else {
            continue;
        };
        match at.get(&row.channel) {
            Some(&index) => rows[index] = row,
            None => {
                at.insert(row.channel.clone(), rows.len());
                rows.push(row);
            }
        }
    }
    rows.sort_by_key(|row| Reverse(row.opened_epoch));
    rows.truncate(CONVERSATION_LIMIT);
    if raw > COMPACT_ABOVE {
        compact(&path, &rows);
    }
    rows
}

/// The resume picker's rows: channel, age, and the first message on one line.
pub fn conversation_rows(items: &[PastConversation]) -> Vec<String> {
    let now = now_seconds();
    items
        .iter()
        .map(|item| {
            let channel = clip(&item.channel, ROW_CHANNEL_WIDTH);
            let age = age(item.opened_epoch, now);
            let message = clip(&one_line(&item.first_message), ROW_MESSAGE_WIDTH).to_string();
            format!("{channel:<ROW_CHANNEL_WIDTH$}  {age:>ROW_AGE_WIDTH$}  {message}")
                .trim_end()
                .to_string()
        })
        .collect()
}

/// How long ago `epoch_seconds` was, in the largest unit that reaches 1.
pub fn age(epoch_seconds: u64, now_seconds: u64) -> String {
    let elapsed = now_seconds.saturating_sub(epoch_seconds);
    match elapsed {
        seconds if seconds < MINUTE => format!("{seconds}s"),
        seconds if seconds < HOUR => format!("{}m", seconds / MINUTE),
        seconds if seconds < DAY => format!("{}h", seconds / HOUR),
        seconds if seconds < WEEK => format!("{}d", seconds / DAY),
        seconds => format!("{}w", seconds / WEEK),
    }
}

/// Key and action for every binding, in the order the `?` overlay lists them.
pub fn hotkeys() -> Vec<(&'static str, &'static str)> {
    vec![
        ("Enter", "Send"),
        ("Shift+Enter / Alt+Enter", "New line"),
        ("Up / Down", "History or cursor"),
        ("Ctrl+A / Ctrl+E", "Line start / end"),
        ("Ctrl+U / Ctrl+K / Ctrl+W", "Kill to start / end / word"),
        ("Ctrl+Y", "Yank"),
        ("Ctrl+Z", "Undo"),
        ("@", "Path completion"),
        ("PgUp / PgDn", "Scroll the transcript"),
        ("Mouse wheel", "Scroll the transcript"),
        ("Drag", "Select; releasing copies"),
        ("Click", "Clear the selection"),
        ("Esc", "Stop the turn / cancel picker"),
        ("Ctrl+B", "Detach from the turn, leaving it running"),
        ("Ctrl+C", "Exit"),
        ("?", "Hotkeys, on an empty composer"),
    ]
}

fn compact(path: &Path, rows: &[PastConversation]) {
    let rendered: String = rows
        .iter()
        .filter_map(|row| serde_json::to_string(row).ok())
        .map(|line| format!("{line}\n"))
        .collect();
    let staged = path.with_extension(COMPACT_SUFFIX);
    if fs::write(&staged, rendered).is_ok() && fs::rename(&staged, path).is_ok() {
        return;
    }
    let _ = fs::remove_file(&staged);
}

fn clip(text: &str, limit: usize) -> &str {
    match text.char_indices().nth(limit) {
        Some((at, _)) => &text[..at],
        None => text,
    }
}

fn one_line(text: &str) -> String {
    let mut flat = String::with_capacity(text.len());
    for character in text.chars() {
        let character = if character.is_control() {
            ' '
        } else {
            character
        };
        if character == ' ' && flat.ends_with(' ') {
            continue;
        }
        flat.push(character);
    }
    flat.trim().to_string()
}

fn now_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|since| since.as_secs())
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("ufo-history-test-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn opened(channel: &str, epoch: u64, first: &str) -> PastConversation {
        PastConversation {
            channel: channel.to_string(),
            opened_epoch: epoch,
            first_message: first.to_string(),
        }
    }

    fn lines(home: &Path) -> usize {
        fs::read_to_string(home.join(CONVERSATIONS_FILE))
            .unwrap()
            .lines()
            .count()
    }

    #[test]
    fn history_round_trips_multiline_entries() {
        let home = scratch("history");
        let mut history = History::load(&home);
        history.push("first");
        history.push("two\nlines");
        history.push("two\nlines");
        let reloaded = History::load(&home);
        assert_eq!(reloaded.entries, vec!["first", "two\nlines"]);
        let _ = fs::remove_dir_all(&home);
    }

    #[test]
    fn conversations_round_trip_newest_first() {
        let home = scratch("round-trip");
        assert!(list_conversations(&home).is_empty());
        record_conversation(&home, &opened("aaaa", 100, "oldest"));
        record_conversation(&home, &opened("bbbb", 300, "newest"));
        record_conversation(&home, &opened("cccc", 200, "middle"));
        let listed = list_conversations(&home);
        assert_eq!(
            listed
                .iter()
                .map(|row| row.channel.as_str())
                .collect::<Vec<_>>(),
            vec!["bbbb", "cccc", "aaaa"]
        );
        assert_eq!(listed[0].first_message, "newest");
        let _ = fs::remove_dir_all(&home);
    }

    #[test]
    fn later_line_replaces_the_channel() {
        let home = scratch("dedupe");
        record_conversation(&home, &opened("aaaa", 100, "first open"));
        record_conversation(&home, &opened("bbbb", 200, "other"));
        record_conversation(&home, &opened("aaaa", 400, "reopened"));
        let listed = list_conversations(&home);
        assert_eq!(listed.len(), 2);
        assert_eq!(listed[0], opened("aaaa", 400, "reopened"));
        let _ = fs::remove_dir_all(&home);
    }

    #[test]
    fn first_message_is_stored_clipped_at_a_char_boundary() {
        let home = scratch("clip");
        let long = "é".repeat(FIRST_MESSAGE_LIMIT + 10);
        record_conversation(&home, &opened("aaaa", 100, &long));
        let listed = list_conversations(&home);
        assert_eq!(listed[0].first_message.chars().count(), FIRST_MESSAGE_LIMIT);
        let _ = fs::remove_dir_all(&home);
    }

    #[test]
    fn malformed_lines_are_skipped() {
        let home = scratch("malformed");
        record_conversation(&home, &opened("aaaa", 100, "kept"));
        let path = home.join(CONVERSATIONS_FILE);
        let mut file = fs::OpenOptions::new().append(true).open(&path).unwrap();
        writeln!(file, "not json").unwrap();
        writeln!(file, "{{\"channel\":\"bbbb\"}}").unwrap();
        writeln!(file).unwrap();
        drop(file);
        record_conversation(&home, &opened("cccc", 200, "also kept"));
        let listed = list_conversations(&home);
        assert_eq!(
            listed
                .iter()
                .map(|row| row.channel.as_str())
                .collect::<Vec<_>>(),
            vec!["cccc", "aaaa"]
        );
        let _ = fs::remove_dir_all(&home);
    }

    #[test]
    fn a_short_log_is_left_as_written() {
        let home = scratch("no-compact");
        record_conversation(&home, &opened("aaaa", 100, "one"));
        record_conversation(&home, &opened("aaaa", 200, "two"));
        record_conversation(&home, &opened("bbbb", 300, "three"));
        assert_eq!(list_conversations(&home).len(), 2);
        assert_eq!(lines(&home), 3);
        let _ = fs::remove_dir_all(&home);
    }

    #[test]
    fn a_long_log_is_compacted_on_read() {
        let home = scratch("compact");
        let channels = CONVERSATION_LIMIT + 10;
        for index in 0..channels {
            let channel = format!("channel-{index:04}");
            record_conversation(&home, &opened(&channel, 1_000 + index as u64, "opened"));
            record_conversation(&home, &opened(&channel, 2_000 + index as u64, "reopened"));
        }
        assert_eq!(lines(&home), channels * 2);
        assert!(channels * 2 > COMPACT_ABOVE);
        let listed = list_conversations(&home);
        assert_eq!(listed.len(), CONVERSATION_LIMIT);
        assert_eq!(listed[0].channel, format!("channel-{:04}", channels - 1));
        assert_eq!(listed[0].first_message, "reopened");
        assert_eq!(lines(&home), CONVERSATION_LIMIT);
        assert_eq!(list_conversations(&home), listed);
        assert!(!home
            .join(CONVERSATIONS_FILE)
            .with_extension(COMPACT_SUFFIX)
            .exists());
        let _ = fs::remove_dir_all(&home);
    }

    #[test]
    fn age_names_the_largest_unit_that_reaches_one() {
        let now = 1_800_000_000;
        assert_eq!(age(now, now), "0s");
        assert_eq!(age(now + 5, now), "0s");
        assert_eq!(age(now - 59, now), "59s");
        assert_eq!(age(now - MINUTE, now), "1m");
        assert_eq!(age(now - HOUR + 1, now), "59m");
        assert_eq!(age(now - HOUR, now), "1h");
        assert_eq!(age(now - 2 * HOUR, now), "2h");
        assert_eq!(age(now - DAY + 1, now), "23h");
        assert_eq!(age(now - DAY, now), "1d");
        assert_eq!(age(now - 3 * DAY, now), "3d");
        assert_eq!(age(now - WEEK + 1, now), "6d");
        assert_eq!(age(now - WEEK, now), "1w");
        assert_eq!(age(now - 52 * WEEK, now), "52w");
    }

    #[test]
    fn rows_state_channel_age_and_one_clipped_line() {
        let now = now_seconds();
        let rows = conversation_rows(&[
            opened(
                "0123456789abcdef",
                now - 2 * HOUR,
                &format!("read\nthe\tlog\n\n{}", "x".repeat(ROW_MESSAGE_WIDTH)),
            ),
            opened("short", now - 3 * DAY, ""),
        ]);
        let gap = " ".repeat(2);
        assert_eq!(
            rows[0],
            format!(
                "01234567{gap} 2h{gap}read the log {}",
                "x".repeat(ROW_MESSAGE_WIDTH - "read the log ".len())
            )
        );
        assert_eq!(rows[1], format!("short   {gap} 3d"));
        assert_eq!(rows[0].find("2h"), rows[1].find("3d"));
    }

    #[test]
    fn hotkeys_are_listed_once_each() {
        let listed = hotkeys();
        let keys: Vec<&str> = listed.iter().map(|(key, _)| *key).collect();
        assert_eq!(listed[0], ("Enter", "Send"));
        assert!(keys.contains(&"?"));
        assert!(keys.contains(&"Esc"));
        let mut sorted = keys.clone();
        sorted.sort_unstable();
        sorted.dedup();
        assert_eq!(sorted.len(), keys.len());
        assert!(listed
            .iter()
            .all(|(key, action)| !key.is_empty() && action.starts_with(char::is_uppercase)));
    }
}
