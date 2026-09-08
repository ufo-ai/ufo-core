
use std::fs;
use std::path::{Path, PathBuf};

pub const HISTORY_LIMIT: usize = 500;

pub struct History {
    path: PathBuf,
    pub entries: Vec<String>,
}

impl History {
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

pub fn hotkeys() -> Vec<(&'static str, &'static str)> {
    vec![
        ("Enter", "Send"),
        ("Shift+Enter / Alt+Enter", "New line"),
        (
            "Up / Down",
            "History (filtered by what you typed) or cursor; Up recalls the newest queued message",
        ),
        ("Ctrl+Up / Ctrl+Down", "Jump between your messages"),
        ("Ctrl+T", "Open or close the steps behind the last reply"),
        ("Ctrl+A / Ctrl+E", "Line start / end"),
        ("Ctrl+U / Ctrl+W", "Kill to start / word"),
        ("Ctrl+Y", "Yank"),
        ("Ctrl+Z", "Undo"),
        (
            "Ctrl+V",
            "Paste from the clipboard; an image attaches as [Image #N]",
        ),
        ("@", "Path completion"),
        ("PgUp / PgDn", "Scroll the transcript"),
        ("End", "Follow the live end, when scrolled"),
        ("Mouse wheel", "Scroll the transcript"),
        ("Drag", "Select; releasing copies"),
        ("Double / triple click", "Select the word / line"),
        ("Click", "Open the URL under it, or clear the selection"),
        ("Esc", "Stop the turn / cancel picker"),
        ("Ctrl+K", "List conversations; Enter or a click opens one"),
        ("Ctrl+B", "Detach from the turn, leaving it running"),
        ("Ctrl+C", "Exit"),
        ("?", "Hotkeys, on an empty composer"),
    ]
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
    fn hotkeys_are_listed_once_each() {
        let listed = hotkeys();
        let keys: Vec<&str> = listed.iter().map(|(key, _)| *key).collect();
        assert_eq!(listed[0], ("Enter", "Send"));
        assert!(keys.contains(&"?"));
        assert!(keys.contains(&"Esc"));
        assert!(keys.contains(&"Ctrl+K"));
        let mut sorted = keys.clone();
        sorted.sort_unstable();
        sorted.dedup();
        assert_eq!(sorted.len(), keys.len());
        assert!(listed
            .iter()
            .all(|(key, action)| !key.is_empty() && action.starts_with(char::is_uppercase)));
    }
}
