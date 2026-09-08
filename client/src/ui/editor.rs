use std::collections::BTreeMap;
use std::sync::Arc;

use super::wrap;

const UNDO_MAX: usize = 100;
const KILL_MAX: usize = 32;
const PASTE_LINES: usize = 10;
const PASTE_CHARS: usize = 1000;

#[derive(Debug, Clone, PartialEq)]
pub enum Key {
    Char(char),
    Enter,
    InsertNewline,
    ShiftEnter,
    Backspace,
    Delete,
    Left,
    Right,
    CursorUp,
    CursorDown,
    Home,
    End,
    BufferHome,
    BufferEnd,
    WordLeft,
    WordRight,
    KillLine,
    KillWord,
    Yank,
    Undo,
    HistPrev,
    HistNext,
    Paste(String),
    Image(String),
    Eof,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Outcome {
    Continue,
    Submit,
    Cancel,
}

#[derive(Debug, Clone, PartialEq)]
pub struct EditorLayout {
    pub rows: Vec<String>,
    pub cursor_row: usize,
    pub cursor_col: usize,
}

#[derive(Debug, Clone, Copy, Default, PartialEq)]
enum Unit {
    #[default]
    None,
    Insert,
    Delete,
}

#[derive(Clone)]
struct Snapshot {
    text: String,
    cursor: usize,
    pastes: BTreeMap<usize, Arc<str>>,
    images: BTreeMap<usize, Arc<str>>,
}

#[derive(Default)]
pub struct AskState {
    pub text: String,
    pub cursor: usize,
    hist_at: Option<usize>,
    draft: String,
    pastes: BTreeMap<usize, Arc<str>>,
    images: BTreeMap<usize, Arc<str>>,
    kills: Vec<String>,
    killing: bool,
    undos: Vec<Snapshot>,
    unit: Unit,
    sticky: Option<usize>,
}

impl AskState {
    pub fn apply(&mut self, key: Key, history: &[String], width: usize) -> Outcome {
        let unit = self.unit;
        self.unit = Unit::None;
        let killing = self.killing;
        self.killing = false;
        let sticky = self.sticky.take();
        match key {
            Key::Char(ch) => {
                self.open_insert(unit, !ch.is_whitespace());
                self.insert(&ch.to_string());
            }
            Key::Enter => return Outcome::Submit,
            Key::InsertNewline | Key::ShiftEnter => {
                self.open_insert(unit, false);
                self.insert("\n");
            }
            Key::Paste(text) => {
                self.snapshot();
                if text.lines().count() > PASTE_LINES || text.chars().count() > PASTE_CHARS {
                    self.collapse(text);
                } else {
                    self.insert(&text);
                }
            }
            Key::Image(path) => {
                self.snapshot();
                let at = self.images.len() + 1;
                self.images.insert(at, Arc::from(path.as_str()));
                self.insert(&image_marker(at));
            }
            Key::Backspace => {
                if let Some(prev) = self.prev_boundary() {
                    self.open_delete(unit);
                    self.cut(prev, self.cursor);
                }
            }
            Key::Delete => self.delete_at_cursor(unit),
            Key::Left => {
                if let Some(prev) = self.prev_boundary() {
                    self.cursor = prev;
                }
            }
            Key::Right => {
                if let Some(next) = self.next_boundary() {
                    self.cursor = next;
                }
            }
            Key::CursorUp => {
                let rows = self.rows(width);
                let (row, col) = wrap::cursor_pos(&self.text, &rows, self.cursor);
                if row == 0 {
                    self.hist_prev(history);
                } else {
                    let want = sticky.unwrap_or(col);
                    self.sticky = Some(want);
                    self.cursor = self.at_col(&rows, row - 1, want);
                }
            }
            Key::CursorDown => {
                let rows = self.rows(width);
                let (row, col) = wrap::cursor_pos(&self.text, &rows, self.cursor);
                if row + 1 == rows.len() {
                    self.hist_next(history);
                } else {
                    let want = sticky.unwrap_or(col);
                    self.sticky = Some(want);
                    self.cursor = self.at_col(&rows, row + 1, want);
                }
            }
            Key::Home => self.cursor = self.line_start(),
            Key::End => self.cursor = self.line_end(),
            Key::BufferHome => self.cursor = 0,
            Key::BufferEnd => self.cursor = self.text.len(),
            Key::WordLeft => self.cursor = self.snap(self.word_left(), false),
            Key::WordRight => self.cursor = self.snap(self.word_right(), true),
            Key::KillLine => {
                let from = self.line_start();
                self.kill(from, self.cursor, killing);
            }
            Key::KillWord => {
                let from = self.snap(self.word_left(), false);
                self.kill(from, self.cursor, killing);
            }
            Key::Yank => {
                if let Some(top) = self.kills.last().cloned() {
                    self.snapshot();
                    self.insert(&top);
                }
            }
            Key::Undo => {
                if let Some(snapshot) = self.undos.pop() {
                    self.text = snapshot.text;
                    self.cursor = snapshot.cursor;
                    self.pastes = snapshot.pastes;
                    self.images = snapshot.images;
                    self.hist_at = None;
                }
            }
            Key::HistPrev => self.hist_prev(history),
            Key::HistNext => self.hist_next(history),
            Key::Eof => {
                if self.text.is_empty() {
                    return Outcome::Cancel;
                }
                self.delete_at_cursor(unit);
            }
        }
        Outcome::Continue
    }

    pub fn render(&self, width: usize) -> EditorLayout {
        let rows = self.rows(width);
        let (cursor_row, cursor_col) = wrap::cursor_pos(&self.text, &rows, self.cursor);
        EditorLayout {
            rows: rows
                .iter()
                .map(|(start, end)| self.text[*start..*end].to_string())
                .collect(),
            cursor_row,
            cursor_col,
        }
    }

    pub fn expand(&self) -> String {
        self.expanded(&self.text)
    }

    fn open_insert(&mut self, unit: Unit, keep: bool) {
        if unit != Unit::Insert {
            self.snapshot();
        }
        if keep {
            self.unit = Unit::Insert;
        }
    }

    fn open_delete(&mut self, unit: Unit) {
        if unit != Unit::Delete {
            self.snapshot();
        }
        self.unit = Unit::Delete;
    }

    fn snapshot(&mut self) {
        if self.undos.len() == UNDO_MAX {
            self.undos.remove(0);
        }
        self.undos.push(Snapshot {
            text: self.text.clone(),
            cursor: self.cursor,
            pastes: self.pastes.clone(),
            images: self.images.clone(),
        });
    }

    fn insert(&mut self, text: &str) {
        self.text.insert_str(self.cursor, text);
        self.cursor += text.len();
        self.hist_at = None;
    }

    fn collapse(&mut self, text: String) {
        let at = self.pastes.len() + 1;
        let marker = marker(at, &text);
        self.pastes.insert(at, Arc::from(text.as_str()));
        self.insert(&marker);
    }

    fn delete_at_cursor(&mut self, unit: Unit) {
        if let Some(next) = self.next_boundary() {
            self.open_delete(unit);
            self.cut(self.cursor, next);
        }
    }

    fn kill(&mut self, from: usize, to: usize, merging: bool) {
        if from == to {
            return;
        }
        self.snapshot();
        let taken = self.cut(from, to);
        match self.kills.last_mut() {
            Some(top) if merging => top.insert_str(0, &taken),
            _ => {
                if self.kills.len() == KILL_MAX {
                    self.kills.remove(0);
                }
                self.kills.push(taken);
            }
        }
        self.killing = true;
    }

    fn cut(&mut self, from: usize, to: usize) -> String {
        let taken = self.expanded(&self.text[from..to]);
        self.text.replace_range(from..to, "");
        self.cursor = from;
        self.hist_at = None;
        self.renumber();
        taken
    }

    fn expanded(&self, text: &str) -> String {
        let mut out = text.to_string();
        for (at, contents) in &self.pastes {
            out = out.replace(&marker(*at, contents), contents);
        }
        for (at, path) in &self.images {
            out = out.replace(&image_marker(*at), &format!("[Image #{at}: {path}]"));
        }
        out
    }

    fn renumber(&mut self) {
        let kept: Vec<(usize, Arc<str>)> = self
            .pastes
            .iter()
            .filter(|(at, contents)| self.text.contains(&marker(**at, contents)))
            .map(|(at, contents)| (*at, contents.clone()))
            .collect();
        let kept_images: Vec<(usize, Arc<str>)> = self
            .images
            .iter()
            .filter(|(at, _)| self.text.contains(&image_marker(**at)))
            .map(|(at, path)| (*at, path.clone()))
            .collect();
        if kept.len() == self.pastes.len() && kept_images.len() == self.images.len() {
            return;
        }
        let mut spans: Vec<(usize, usize, String)> = Vec::new();
        let mut pastes = BTreeMap::new();
        for (index, (at, contents)) in kept.into_iter().enumerate() {
            let was = marker(at, &contents);
            let start = self.text.find(&was).unwrap();
            spans.push((start, start + was.len(), marker(index + 1, &contents)));
            pastes.insert(index + 1, contents);
        }
        let mut images = BTreeMap::new();
        for (index, (at, path)) in kept_images.into_iter().enumerate() {
            let was = image_marker(at);
            let start = self.text.find(&was).unwrap();
            spans.push((start, start + was.len(), image_marker(index + 1)));
            images.insert(index + 1, path);
        }
        spans.sort_by_key(|(start, _, _)| *start);
        let mut rebuilt = String::with_capacity(self.text.len());
        let mut at = 0;
        let mut cursor = self.cursor;
        for (start, end, now) in spans {
            rebuilt.push_str(&self.text[at..start]);
            if self.cursor >= end {
                cursor = cursor + now.len() - (end - start);
            } else if self.cursor > start {
                cursor = rebuilt.len() + now.len();
            }
            rebuilt.push_str(&now);
            at = end;
        }
        rebuilt.push_str(&self.text[at..]);
        self.text = rebuilt;
        self.cursor = cursor.min(self.text.len());
        self.pastes = pastes;
        self.images = images;
    }

    fn markers(&self) -> Vec<(usize, usize)> {
        let texts = self
            .pastes
            .iter()
            .map(|(at, contents)| marker(*at, contents))
            .chain(self.images.keys().map(|at| image_marker(*at)));
        texts
            .filter_map(|text| {
                self.text
                    .find(&text)
                    .map(|start| (start, start + text.len()))
            })
            .collect()
    }

    fn snap(&self, at: usize, forward: bool) -> usize {
        match self
            .markers()
            .into_iter()
            .find(|(start, end)| at > *start && at < *end)
        {
            Some((start, end)) => {
                if forward {
                    end
                } else {
                    start
                }
            }
            None => at,
        }
    }

    fn prev_boundary(&self) -> Option<usize> {
        if let Some((start, _)) = self
            .markers()
            .into_iter()
            .find(|(_, end)| *end == self.cursor)
        {
            return Some(start);
        }
        self.text[..self.cursor]
            .char_indices()
            .next_back()
            .map(|(index, _)| index)
    }

    fn next_boundary(&self) -> Option<usize> {
        if let Some((_, end)) = self
            .markers()
            .into_iter()
            .find(|(start, _)| *start == self.cursor)
        {
            return Some(end);
        }
        self.text[self.cursor..]
            .chars()
            .next()
            .map(|ch| self.cursor + ch.len_utf8())
    }

    fn rows(&self, width: usize) -> Vec<(usize, usize)> {
        wrap::hard_rows(&self.text, width, width)
    }

    fn at_col(&self, rows: &[(usize, usize)], row: usize, want: usize) -> usize {
        let (start, end) = rows[row];
        let line = &self.text[start..end];
        let mut at = start + wrap::clip(line, want).len();
        if at == end && end < self.text.len() && !self.text[end..].starts_with('\n') {
            if let Some((index, _)) = line.char_indices().next_back() {
                at = start + index;
            }
        }
        self.snap(at, false)
    }

    fn line_start(&self) -> usize {
        self.text[..self.cursor]
            .rfind('\n')
            .map(|at| at + 1)
            .unwrap_or(0)
    }

    fn line_end(&self) -> usize {
        self.text[self.cursor..]
            .find('\n')
            .map(|at| self.cursor + at)
            .unwrap_or(self.text.len())
    }

    fn word_left(&self) -> usize {
        let head = &self.text[..self.cursor];
        let skipped = head.trim_end_matches(char::is_whitespace);
        match skipped.rfind(char::is_whitespace) {
            Some(space) => space + self.text[space..].chars().next().unwrap().len_utf8(),
            None => 0,
        }
    }

    fn word_right(&self) -> usize {
        let tail = &self.text[self.cursor..];
        let after_space = tail.len() - tail.trim_start_matches(char::is_whitespace).len();
        let word = &tail[after_space..];
        let advanced = after_space
            + (word.len() - word.trim_start_matches(|c: char| !c.is_whitespace()).len());
        self.cursor + advanced
    }

    fn hist_prev(&mut self, history: &[String]) {
        if self.hist_at.is_none() {
            self.draft = self.text.clone();
        }
        let from = self.hist_at.unwrap_or(history.len());
        let found = history
            .iter()
            .enumerate()
            .rev()
            .find(|(at, entry)| *at < from && self.recalls(entry));
        if let Some((at, entry)) = found {
            self.hist_at = Some(at);
            self.text = entry.clone();
            self.cursor = self.text.len();
        }
    }

    fn hist_next(&mut self, history: &[String]) {
        let Some(from) = self.hist_at else {
            return;
        };
        let found = history
            .iter()
            .enumerate()
            .find(|(at, entry)| *at > from && self.recalls(entry));
        match found {
            Some((at, entry)) => {
                self.hist_at = Some(at);
                self.text = entry.clone();
            }
            None => {
                self.hist_at = None;
                self.text = self.draft.clone();
            }
        }
        self.cursor = self.text.len();
    }

    fn recalls(&self, entry: &str) -> bool {
        self.draft.is_empty() || (entry.starts_with(&self.draft) && entry != self.draft)
    }
}

fn marker(at: usize, contents: &str) -> String {
    format!("[paste #{at} +{} lines]", contents.lines().count())
}

fn image_marker(at: usize) -> String {
    format!("[Image #{at}]")
}

#[cfg(test)]
mod tests {
    use super::*;

    const WIDE: usize = 200;

    fn drive(state: &mut AskState, keys: &[Key], history: &[String]) -> Outcome {
        drive_at(state, WIDE, keys, history)
    }

    fn drive_at(state: &mut AskState, width: usize, keys: &[Key], history: &[String]) -> Outcome {
        let mut last = Outcome::Continue;
        for key in keys {
            last = state.apply(key.clone(), history, width);
        }
        last
    }

    fn typed(state: &mut AskState, text: &str) {
        let keys: Vec<Key> = text.chars().map(Key::Char).collect();
        drive(state, &keys, &[]);
    }

    fn big(lines: usize) -> String {
        (0..lines)
            .map(|at| format!("line {at}\n"))
            .collect::<String>()
    }

    #[test]
    fn inserts_at_cursor() {
        let mut state = AskState::default();
        drive(
            &mut state,
            &[Key::Char('a'), Key::Char('c'), Key::Left, Key::Char('b')],
            &[],
        );
        assert_eq!(state.text, "abc");
        assert_eq!(state.cursor, 2);
    }

    #[test]
    fn word_movement_and_kill() {
        let mut state = AskState::default();
        drive(
            &mut state,
            &[Key::Paste("one two three".into()), Key::KillWord],
            &[],
        );
        assert_eq!(state.text, "one two ");
        drive(&mut state, &[Key::WordLeft, Key::WordLeft], &[]);
        assert_eq!(state.cursor, 0);
        drive(&mut state, &[Key::WordRight], &[]);
        assert_eq!(state.cursor, 3);
    }

    #[test]
    fn kill_line_erases_before_cursor() {
        let mut state = AskState::default();
        drive(
            &mut state,
            &[Key::Paste("abcdef".into()), Key::Left, Key::KillLine],
            &[],
        );
        assert_eq!(state.text, "f");
        assert_eq!(state.cursor, 0);
    }

    #[test]
    fn history_walks_and_restores_draft() {
        let history = vec!["first".to_string(), "second".to_string()];
        let mut state = AskState::default();
        drive(&mut state, &[Key::HistPrev], &history);
        assert_eq!(state.text, "second");
        drive(&mut state, &[Key::HistPrev, Key::HistPrev], &history);
        assert_eq!(state.text, "first");
        assert_eq!(state.cursor, 5);
        drive(&mut state, &[Key::HistNext], &history);
        assert_eq!(state.text, "second");
        drive(&mut state, &[Key::HistNext], &history);
        assert_eq!(state.text, "");
        assert_eq!(state.cursor, 0);
    }

    #[test]
    fn history_recalls_only_entries_under_the_typed_prefix() {
        let history = vec![
            "git status".to_string(),
            "cargo test".to_string(),
            "git push".to_string(),
        ];
        let mut state = AskState::default();
        typed(&mut state, "git");
        drive(&mut state, &[Key::HistPrev], &history);
        assert_eq!(state.text, "git push");
        assert_eq!(state.cursor, state.text.len());
        drive(&mut state, &[Key::HistPrev], &history);
        assert_eq!(state.text, "git status");
        drive(&mut state, &[Key::HistPrev], &history);
        assert_eq!(state.text, "git status");
        drive(&mut state, &[Key::HistNext], &history);
        assert_eq!(state.text, "git push");
        drive(&mut state, &[Key::HistNext], &history);
        assert_eq!(state.text, "git");
        assert_eq!(state.cursor, 3);
    }

    #[test]
    fn a_prefix_skips_the_entry_it_equals() {
        let history = vec!["git push --force".to_string(), "git push".to_string()];
        let mut state = AskState::default();
        typed(&mut state, "git push");
        drive(&mut state, &[Key::HistPrev], &history);
        assert_eq!(state.text, "git push --force");
        drive(&mut state, &[Key::HistPrev], &history);
        assert_eq!(state.text, "git push --force");
    }

    #[test]
    fn a_prefix_matching_nothing_leaves_the_ask_alone() {
        let history = vec!["first".to_string(), "second".to_string()];
        let mut state = AskState::default();
        typed(&mut state, "draft");
        drive(&mut state, &[Key::Left, Key::HistPrev], &history);
        assert_eq!(state.text, "draft");
        assert_eq!(state.cursor, 4);
        drive(&mut state, &[Key::HistNext], &history);
        assert_eq!(state.text, "draft");
        assert_eq!(state.cursor, 4);
    }

    #[test]
    fn a_multiline_draft_filters_on_the_whole_text() {
        let history = vec!["one\nfour".to_string(), "one\ntwo three".to_string()];
        let mut state = AskState::default();
        drive(
            &mut state,
            &[Key::Paste("one\ntwo".into()), Key::HistPrev],
            &history,
        );
        assert_eq!(state.text, "one\ntwo three");
        drive(&mut state, &[Key::HistPrev], &history);
        assert_eq!(state.text, "one\ntwo three");
        drive(&mut state, &[Key::HistNext], &history);
        assert_eq!(state.text, "one\ntwo");
    }

    #[test]
    fn a_multibyte_prefix_is_boundary_safe() {
        let history = vec!["état".to_string(), "eau".to_string(), "élan".to_string()];
        let mut state = AskState::default();
        typed(&mut state, "é");
        drive(&mut state, &[Key::HistPrev], &history);
        assert_eq!(state.text, "élan");
        assert_eq!(state.cursor, state.text.len());
        drive(&mut state, &[Key::HistPrev], &history);
        assert_eq!(state.text, "état");
        assert_eq!(state.cursor, state.text.len());
        drive(&mut state, &[Key::HistNext, Key::HistNext], &history);
        assert_eq!(state.text, "é");
    }

    #[test]
    fn editing_a_history_entry_detaches_it() {
        let history = vec!["old news".to_string(), "old".to_string()];
        let mut state = AskState::default();
        drive(&mut state, &[Key::HistPrev, Key::Char(' ')], &history);
        assert_eq!(state.text, "old ");
        drive(&mut state, &[Key::HistPrev], &history);
        assert_eq!(state.text, "old news");
        drive(&mut state, &[Key::HistNext], &history);
        assert_eq!(state.text, "old ");
    }

    #[test]
    fn eof_on_empty_cancels_and_deletes_otherwise() {
        let mut state = AskState::default();
        assert_eq!(state.apply(Key::Eof, &[], WIDE), Outcome::Cancel);
        drive(&mut state, &[Key::Paste("ab".into()), Key::Home], &[]);
        assert_eq!(state.apply(Key::Eof, &[], WIDE), Outcome::Continue);
        assert_eq!(state.text, "b");
    }

    #[test]
    fn submit_passes_through() {
        let mut state = AskState::default();
        assert_eq!(state.apply(Key::Enter, &[], WIDE), Outcome::Submit);
    }

    #[test]
    fn multibyte_editing_is_boundary_safe() {
        let mut state = AskState::default();
        drive(
            &mut state,
            &[Key::Paste("héé".into()), Key::Left, Key::Backspace],
            &[],
        );
        assert_eq!(state.text, "hé");
        assert_eq!(state.cursor, 1);
    }

    #[test]
    fn newline_keys_insert_and_enter_submits() {
        let mut state = AskState::default();
        drive(&mut state, &[Key::Char('a'), Key::ShiftEnter], &[]);
        drive(&mut state, &[Key::Char('b'), Key::InsertNewline], &[]);
        assert_eq!(state.text, "a\nb\n");
        assert_eq!(state.apply(Key::Enter, &[], WIDE), Outcome::Submit);
    }

    #[test]
    fn home_and_end_are_line_scoped() {
        let mut state = AskState::default();
        drive(&mut state, &[Key::Paste("one\ntwo\nthree".into())], &[]);
        drive(&mut state, &[Key::Home], &[]);
        assert_eq!(state.cursor, 8);
        drive(&mut state, &[Key::End], &[]);
        assert_eq!(state.cursor, 13);
        drive(&mut state, &[Key::BufferHome], &[]);
        assert_eq!(state.cursor, 0);
        drive(&mut state, &[Key::End], &[]);
        assert_eq!(state.cursor, 3);
        drive(&mut state, &[Key::BufferEnd], &[]);
        assert_eq!(state.cursor, 13);
    }

    #[test]
    fn rows_and_cursor_cells_follow_the_wrap() {
        let mut state = AskState::default();
        drive(&mut state, &[Key::Paste("abcdefgh\nij".into())], &[]);
        let layout = state.render(3);
        assert_eq!(layout.rows, vec!["abc", "def", "gh", "ij"]);
        assert_eq!((layout.cursor_row, layout.cursor_col), (3, 2));
    }

    #[test]
    fn cursor_cells_count_wide_and_multibyte_chars() {
        let mut state = AskState::default();
        drive(&mut state, &[Key::Paste("héllo".into())], &[]);
        assert_eq!(state.render(WIDE).cursor_col, 5);
        let mut state = AskState::default();
        drive(&mut state, &[Key::Paste("日本語".into())], &[]);
        let layout = state.render(WIDE);
        assert_eq!(layout.cursor_col, 6);
        assert_eq!(state.render(4).rows, vec!["日本", "語"]);
    }

    #[test]
    fn vertical_motion_keeps_a_sticky_column() {
        let mut state = AskState::default();
        drive(
            &mut state,
            &[Key::Paste("aaaaaaaaaa\nbb\ncccccccccc".into())],
            &[],
        );
        drive_at(&mut state, 10, &[Key::CursorUp], &[]);
        assert_eq!(state.render(10).cursor_col, 2);
        drive_at(&mut state, 10, &[Key::CursorUp], &[]);
        let layout = state.render(10);
        assert_eq!((layout.cursor_row, layout.cursor_col), (0, 10));
        drive_at(&mut state, 10, &[Key::CursorDown, Key::CursorDown], &[]);
        let layout = state.render(10);
        assert_eq!((layout.cursor_row, layout.cursor_col), (2, 10));
    }

    #[test]
    fn vertical_motion_crosses_wrapped_rows_of_one_line() {
        let mut state = AskState::default();
        drive(&mut state, &[Key::Paste("abcdefghijklmno".into())], &[]);
        drive_at(&mut state, 5, &[Key::Left, Key::CursorUp], &[]);
        let layout = state.render(5);
        assert_eq!((layout.cursor_row, layout.cursor_col), (1, 4));
        drive_at(&mut state, 5, &[Key::CursorUp], &[]);
        assert_eq!(state.render(5).cursor_row, 0);
    }

    #[test]
    fn sticky_column_survives_a_wide_short_line() {
        let mut state = AskState::default();
        drive(
            &mut state,
            &[Key::Paste("日本語日本語\nx\n日本語日本語".into())],
            &[],
        );
        drive_at(&mut state, 12, &[Key::CursorUp, Key::CursorUp], &[]);
        let layout = state.render(12);
        assert_eq!((layout.cursor_row, layout.cursor_col), (0, 12));
    }

    #[test]
    fn vertical_motion_reaches_history_at_the_edges() {
        let history = vec!["one\ntwo three".to_string(), "earlier".to_string()];
        let mut state = AskState::default();
        drive(&mut state, &[Key::Paste("one\ntwo".into())], &[]);
        drive(&mut state, &[Key::CursorUp], &history);
        assert_eq!(state.text, "one\ntwo");
        drive(&mut state, &[Key::CursorUp], &history);
        assert_eq!(state.text, "one\ntwo three");
        drive(&mut state, &[Key::CursorDown], &history);
        assert_eq!(state.text, "one\ntwo");
    }

    #[test]
    fn kills_merge_then_yank_restores_them() {
        let mut state = AskState::default();
        drive(
            &mut state,
            &[
                Key::Paste("one two three".into()),
                Key::KillWord,
                Key::KillWord,
            ],
            &[],
        );
        assert_eq!(state.text, "one ");
        drive(&mut state, &[Key::BufferEnd, Key::Yank], &[]);
        assert_eq!(state.text, "one two three");
    }

    #[test]
    fn a_pause_between_kills_starts_a_new_entry() {
        let mut state = AskState::default();
        drive(
            &mut state,
            &[
                Key::Paste("one two three".into()),
                Key::KillWord,
                Key::End,
                Key::KillWord,
            ],
            &[],
        );
        assert_eq!(state.text, "one ");
        drive(&mut state, &[Key::Yank], &[]);
        assert_eq!(state.text, "one two ");
    }

    #[test]
    fn undo_coalesces_words_and_seals_at_a_space() {
        let mut state = AskState::default();
        typed(&mut state, "ab cd");
        drive(&mut state, &[Key::Undo], &[]);
        assert_eq!(state.text, "ab ");
        assert_eq!(state.cursor, 3);
        drive(&mut state, &[Key::Undo], &[]);
        assert_eq!(state.text, "");
        drive(&mut state, &[Key::Undo], &[]);
        assert_eq!(state.text, "");
    }

    #[test]
    fn undo_restores_a_kill_and_a_run_of_deletes() {
        let mut state = AskState::default();
        typed(&mut state, "hello");
        drive(&mut state, &[Key::KillWord], &[]);
        assert_eq!(state.text, "");
        drive(&mut state, &[Key::Undo], &[]);
        assert_eq!(state.text, "hello");
        drive(&mut state, &[Key::Backspace, Key::Backspace], &[]);
        assert_eq!(state.text, "hel");
        drive(&mut state, &[Key::Undo], &[]);
        assert_eq!(state.text, "hello");
    }

    #[test]
    fn undo_stops_at_the_cap() {
        let mut state = AskState::default();
        for _ in 0..UNDO_MAX + 20 {
            drive(&mut state, &[Key::Char('x'), Key::Char(' ')], &[]);
        }
        assert_eq!(state.undos.len(), UNDO_MAX);
        for _ in 0..UNDO_MAX {
            drive(&mut state, &[Key::Undo], &[]);
        }
        assert_eq!(state.text.len(), 40);
    }

    #[test]
    fn a_big_paste_collapses_to_one_marker() {
        let mut state = AskState::default();
        let pasted = big(12);
        drive(&mut state, &[Key::Paste(pasted.clone())], &[]);
        assert_eq!(state.text, "[paste #1 +12 lines]");
        assert_eq!(state.expand(), pasted);
        drive(&mut state, &[Key::Char('!')], &[]);
        assert_eq!(state.expand(), format!("{pasted}!"));
    }

    #[test]
    fn a_long_single_line_paste_collapses_too() {
        let mut state = AskState::default();
        let pasted = "x".repeat(PASTE_CHARS + 1);
        drive(&mut state, &[Key::Paste(pasted.clone())], &[]);
        assert_eq!(state.text, "[paste #1 +1 lines]");
        assert_eq!(state.expand(), pasted);
    }

    #[test]
    fn a_marker_moves_and_deletes_as_one() {
        let mut state = AskState::default();
        drive(&mut state, &[Key::Paste(big(12)), Key::Char('!')], &[]);
        drive(&mut state, &[Key::Left, Key::Left], &[]);
        assert_eq!(state.cursor, 0);
        drive(&mut state, &[Key::Right], &[]);
        assert_eq!(state.cursor, "[paste #1 +12 lines]".len());
        drive(&mut state, &[Key::Backspace], &[]);
        assert_eq!(state.text, "!");
        assert_eq!(state.expand(), "!");
    }

    #[test]
    fn deleting_a_marker_renumbers_the_higher_ones() {
        let mut state = AskState::default();
        let first = big(11);
        let second = big(12);
        let third = big(13);
        drive(
            &mut state,
            &[
                Key::Paste(first),
                Key::Paste(second.clone()),
                Key::Paste(third.clone()),
            ],
            &[],
        );
        assert_eq!(
            state.text,
            "[paste #1 +11 lines][paste #2 +12 lines][paste #3 +13 lines]"
        );
        drive(&mut state, &[Key::BufferHome, Key::Delete], &[]);
        assert_eq!(state.text, "[paste #1 +12 lines][paste #2 +13 lines]");
        assert_eq!(state.expand(), format!("{second}{third}"));
        assert_eq!(state.cursor, 0);
    }

    #[test]
    fn killing_a_marker_rings_its_contents() {
        let mut state = AskState::default();
        let pasted = big(12);
        drive(
            &mut state,
            &[Key::Paste(pasted.clone()), Key::KillLine],
            &[],
        );
        assert_eq!(state.text, "");
        assert!(state.pastes.is_empty());
        drive(&mut state, &[Key::Yank], &[]);
        assert_eq!(state.text, pasted);
        assert_eq!(state.expand(), pasted);
    }

    #[test]
    fn undo_brings_a_deleted_marker_back() {
        let mut state = AskState::default();
        let pasted = big(12);
        drive(
            &mut state,
            &[Key::Paste(pasted.clone()), Key::Backspace],
            &[],
        );
        assert_eq!(state.text, "");
        drive(&mut state, &[Key::Undo], &[]);
        assert_eq!(state.text, "[paste #1 +12 lines]");
        assert_eq!(state.expand(), pasted);
    }

    #[test]
    fn an_image_paste_shows_a_marker_and_expands_to_its_path() {
        let mut state = AskState::default();
        drive(&mut state, &[Key::Image("/tmp/shot.png".into())], &[]);
        assert_eq!(state.text, "[Image #1]");
        assert_eq!(state.expand(), "[Image #1: /tmp/shot.png]");
        drive(&mut state, &[Key::Char('?')], &[]);
        assert_eq!(state.expand(), "[Image #1: /tmp/shot.png]?");
    }

    #[test]
    fn images_and_text_pastes_number_independently() {
        let mut state = AskState::default();
        let pasted = big(12);
        drive(
            &mut state,
            &[Key::Paste(pasted.clone()), Key::Image("/tmp/a.png".into())],
            &[],
        );
        assert_eq!(state.text, "[paste #1 +12 lines][Image #1]");
        assert_eq!(state.expand(), format!("{pasted}[Image #1: /tmp/a.png]"));
    }

    #[test]
    fn an_image_marker_moves_and_deletes_as_one() {
        let mut state = AskState::default();
        drive(&mut state, &[Key::Image("/tmp/a.png".into())], &[]);
        drive(&mut state, &[Key::Left], &[]);
        assert_eq!(state.cursor, 0);
        drive(&mut state, &[Key::Right], &[]);
        assert_eq!(state.cursor, "[Image #1]".len());
        drive(&mut state, &[Key::Backspace], &[]);
        assert_eq!(state.text, "");
        assert_eq!(state.expand(), "");
    }

    #[test]
    fn deleting_an_image_marker_renumbers_the_rest() {
        let mut state = AskState::default();
        drive(
            &mut state,
            &[
                Key::Image("/tmp/a.png".into()),
                Key::Image("/tmp/b.png".into()),
                Key::Image("/tmp/c.png".into()),
            ],
            &[],
        );
        assert_eq!(state.text, "[Image #1][Image #2][Image #3]");
        drive(&mut state, &[Key::BufferHome, Key::Delete], &[]);
        assert_eq!(state.text, "[Image #1][Image #2]");
        assert_eq!(
            state.expand(),
            "[Image #1: /tmp/b.png][Image #2: /tmp/c.png]"
        );
        assert_eq!(state.cursor, 0);
    }

    #[test]
    fn undo_brings_a_deleted_image_back() {
        let mut state = AskState::default();
        drive(
            &mut state,
            &[Key::Image("/tmp/a.png".into()), Key::Backspace],
            &[],
        );
        assert_eq!(state.text, "");
        drive(&mut state, &[Key::Undo], &[]);
        assert_eq!(state.text, "[Image #1]");
        assert_eq!(state.expand(), "[Image #1: /tmp/a.png]");
    }

    #[test]
    fn killing_an_image_marker_rings_its_path() {
        let mut state = AskState::default();
        drive(
            &mut state,
            &[Key::Image("/tmp/a.png".into()), Key::KillLine],
            &[],
        );
        assert_eq!(state.text, "");
        assert!(state.images.is_empty());
        drive(&mut state, &[Key::Yank], &[]);
        assert_eq!(state.text, "[Image #1: /tmp/a.png]");
    }

    #[test]
    fn word_motion_steps_over_a_marker() {
        let mut state = AskState::default();
        drive(&mut state, &[Key::Paste(big(12)), Key::Char('x')], &[]);
        drive(&mut state, &[Key::WordLeft, Key::WordLeft], &[]);
        assert_eq!(state.cursor, 0);
        drive(&mut state, &[Key::WordRight], &[]);
        assert_eq!(state.cursor, "[paste #1 +12 lines]".len());
    }
}
