//! The ask line editor as a pure state machine over decoded keys.

/// One decoded editing key.
#[derive(Debug, Clone, PartialEq)]
pub enum Key {
    Char(char),
    Enter,
    InsertNewline,
    Backspace,
    Delete,
    Left,
    Right,
    Home,
    End,
    WordLeft,
    WordRight,
    KillLine,
    KillWord,
    HistPrev,
    HistNext,
    Paste(String),
    Cancel,
    Eof,
}

/// What a key did to the ask.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Outcome {
    Continue,
    Submit,
    Cancel,
}

/// The in-flight ask: the text, the byte cursor, and where history browsing stands.
pub struct AskState {
    pub text: String,
    pub cursor: usize,
    hist_at: Option<usize>,
    draft: String,
}

impl AskState {
    pub fn new() -> AskState {
        AskState {
            text: String::new(),
            cursor: 0,
            hist_at: None,
            draft: String::new(),
        }
    }

    pub fn apply(&mut self, key: Key, history: &[String]) -> Outcome {
        match key {
            Key::Char(ch) => self.insert(&ch.to_string()),
            Key::Enter => return Outcome::Submit,
            Key::InsertNewline => self.insert("\n"),
            Key::Paste(text) => self.insert(&text),
            Key::Backspace => {
                if let Some(prev) = self.prev_boundary() {
                    self.text.replace_range(prev..self.cursor, "");
                    self.cursor = prev;
                    self.hist_at = None;
                }
            }
            Key::Delete => self.delete_at_cursor(),
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
            Key::Home => self.cursor = 0,
            Key::End => self.cursor = self.text.len(),
            Key::WordLeft => self.cursor = self.word_left(),
            Key::WordRight => self.cursor = self.word_right(),
            Key::KillLine => {
                self.text.replace_range(..self.cursor, "");
                self.cursor = 0;
                self.hist_at = None;
            }
            Key::KillWord => {
                let from = self.word_left();
                self.text.replace_range(from..self.cursor, "");
                self.cursor = from;
                self.hist_at = None;
            }
            Key::HistPrev => {
                if history.is_empty() {
                    return Outcome::Continue;
                }
                let at = match self.hist_at {
                    None => {
                        self.draft = self.text.clone();
                        history.len() - 1
                    }
                    Some(at) => at.saturating_sub(1),
                };
                self.hist_at = Some(at);
                self.text = history[at].clone();
                self.cursor = self.text.len();
            }
            Key::HistNext => match self.hist_at {
                None => {}
                Some(at) if at + 1 < history.len() => {
                    self.hist_at = Some(at + 1);
                    self.text = history[at + 1].clone();
                    self.cursor = self.text.len();
                }
                Some(_) => {
                    self.hist_at = None;
                    self.text = self.draft.clone();
                    self.cursor = self.text.len();
                }
            },
            Key::Cancel => return Outcome::Cancel,
            Key::Eof => {
                if self.text.is_empty() {
                    return Outcome::Cancel;
                }
                self.delete_at_cursor();
            }
        }
        Outcome::Continue
    }

    fn insert(&mut self, text: &str) {
        self.text.insert_str(self.cursor, text);
        self.cursor += text.len();
        self.hist_at = None;
    }

    fn delete_at_cursor(&mut self) {
        if let Some(next) = self.next_boundary() {
            self.text.replace_range(self.cursor..next, "");
            self.hist_at = None;
        }
    }

    fn prev_boundary(&self) -> Option<usize> {
        self.text[..self.cursor]
            .char_indices()
            .next_back()
            .map(|(index, _)| index)
    }

    fn next_boundary(&self) -> Option<usize> {
        self.text[self.cursor..]
            .chars()
            .next()
            .map(|ch| self.cursor + ch.len_utf8())
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
}

impl Default for AskState {
    fn default() -> AskState {
        AskState::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn drive(state: &mut AskState, keys: &[Key], history: &[String]) -> Outcome {
        let mut last = Outcome::Continue;
        for key in keys {
            last = state.apply(key.clone(), history);
        }
        last
    }

    #[test]
    fn inserts_at_cursor() {
        let mut state = AskState::new();
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
        let mut state = AskState::new();
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
        let mut state = AskState::new();
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
        let mut state = AskState::new();
        drive(
            &mut state,
            &[Key::Paste("draft".into()), Key::HistPrev],
            &history,
        );
        assert_eq!(state.text, "second");
        drive(&mut state, &[Key::HistPrev, Key::HistPrev], &history);
        assert_eq!(state.text, "first");
        drive(&mut state, &[Key::HistNext, Key::HistNext], &history);
        assert_eq!(state.text, "draft");
    }

    #[test]
    fn editing_a_history_entry_detaches_it() {
        let history = vec!["old".to_string()];
        let mut state = AskState::new();
        drive(&mut state, &[Key::HistPrev, Key::Char('!')], &history);
        assert_eq!(state.text, "old!");
        drive(&mut state, &[Key::HistPrev], &history);
        assert_eq!(state.text, "old");
        drive(&mut state, &[Key::HistNext], &history);
        assert_eq!(state.text, "old!");
    }

    #[test]
    fn eof_on_empty_cancels_and_deletes_otherwise() {
        let mut state = AskState::new();
        assert_eq!(state.apply(Key::Eof, &[]), Outcome::Cancel);
        drive(&mut state, &[Key::Paste("ab".into()), Key::Home], &[]);
        assert_eq!(state.apply(Key::Eof, &[]), Outcome::Continue);
        assert_eq!(state.text, "b");
    }

    #[test]
    fn submit_and_cancel_pass_through() {
        let mut state = AskState::new();
        assert_eq!(state.apply(Key::Enter, &[]), Outcome::Submit);
        assert_eq!(state.apply(Key::Cancel, &[]), Outcome::Cancel);
    }

    #[test]
    fn multibyte_editing_is_boundary_safe() {
        let mut state = AskState::new();
        drive(
            &mut state,
            &[Key::Paste("héé".into()), Key::Left, Key::Backspace],
            &[],
        );
        assert_eq!(state.text, "hé");
        assert_eq!(state.cursor, 1);
    }
}
