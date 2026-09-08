use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use ratatui::text::{Line, Span};
use serde::{Deserialize, Serialize};

use crate::config::CONVERSATIONS_FILE;
use crate::ui::picker::{PickKey, PickOutcome, Picker};
use crate::ui::theme::Theme;
use crate::ui::wrap;
use crate::ui::{FOCUS_CARET, PROMPT_IDLE};
use crate::wire::{ConversationRow, Target};

pub const TITLE: &str = "UFO Chats";
const INDENT: &str = "  ";
pub const NEW_CHAT_LABEL: &str = "New chat";
pub const SEARCH_LABEL: &str = "Search";
const LOADING: &str = "Loading…";
const NOTHING_MATCHES: &str = "Nothing matches.";
const NOTHING_YET: &str = "No conversations yet.";
const ORIGIN_WIDTH: usize = 16;
const AGE_WIDTH: usize = 3;
const GAP: &str = "  ";
const HEAD_ROWS: usize = 1;
const REQUERY_AFTER: Duration = Duration::from_millis(250);
const REFRESH_EVERY: Duration = Duration::from_secs(5);
const MINUTE: u64 = 60;
const HOUR: u64 = 60 * MINUTE;
const DAY: u64 = 24 * HOUR;
const WEEK: u64 = 7 * DAY;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Slot {
    Entry,
    Search,
    List,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Pick {
    None,
    Open(ConversationRow),
    Close,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Fetch {
    pub generation: u32,
    pub search: String,
}

/// The file names the sign-in it was read under, so another member's sign-in on this machine leaves it
/// unread, and a sign-out deletes it with the credential.
pub struct Cache {
    path: PathBuf,
    session: String,
}

#[derive(Serialize, Deserialize)]
struct Stored {
    session: String,
    conversations: Vec<ConversationRow>,
}

impl Cache {
    pub fn at(home: &Path, session: &str) -> Cache {
        Cache {
            path: home.join(CONVERSATIONS_FILE),
            session: session.to_string(),
        }
    }

    pub fn load(&self) -> Vec<ConversationRow> {
        fs::read_to_string(&self.path)
            .ok()
            .and_then(|text| serde_json::from_str::<Stored>(&text).ok())
            .filter(|stored| stored.session == self.session)
            .map(|stored| stored.conversations)
            .unwrap_or_default()
    }

    pub fn store(&self, rows: &[ConversationRow]) {
        let stored = Stored {
            session: self.session.clone(),
            conversations: rows.to_vec(),
        };
        let text = serde_json::to_string(&stored).expect("rows serialize");
        let _ = fs::write(&self.path, text);
    }
}

pub struct Conversations {
    rows: Vec<ConversationRow>,
    picker: Picker,
    loading: bool,
    error: Option<String>,
    generation: u32,
    asked: String,
    typed_at: Option<Instant>,
    fetched_at: Instant,
    loads: u32,
    slot: Slot,
    top: usize,
    list: usize,
    back: bool,
}

impl Conversations {
    pub fn new(
        back: bool,
        rows: Vec<ConversationRow>,
        seen: &HashMap<String, f64>,
    ) -> Conversations {
        let now = now_seconds();
        let mut picker = Picker::new(rows.iter().map(|row| row_text(row, now)).collect());
        for (index, row) in rows.iter().enumerate() {
            let moved = seen.get(&row.id).is_some_and(|&stamp| row.last_at > stamp);
            picker.set_bold(index, moved);
        }
        Conversations {
            rows,
            picker,
            loading: true,
            error: None,
            generation: 1,
            asked: String::new(),
            typed_at: None,
            fetched_at: Instant::now(),
            loads: 0,
            slot: Slot::Entry,
            top: 0,
            list: 1,
            back,
        }
    }

    pub fn back(&self) -> bool {
        self.back
    }

    pub fn slot(&self) -> Slot {
        self.slot
    }

    pub fn set_slot(&mut self, slot: Slot) {
        self.slot = slot;
    }

    pub fn rows(&self) -> &[ConversationRow] {
        &self.rows
    }

    pub fn first_fetch(&self) -> Fetch {
        Fetch {
            generation: self.generation,
            search: String::new(),
        }
    }

    /// An answer to an earlier generation is dropped: a later fetch already speaks for what the member
    /// typed since.
    pub fn loaded(
        &mut self,
        generation: u32,
        result: Result<Vec<ConversationRow>, String>,
        seen: &mut HashMap<String, f64>,
        current: Option<&Target>,
    ) -> bool {
        if generation != self.generation {
            return false;
        }
        self.loading = false;
        match result {
            Ok(rows) => {
                self.error = None;
                let first = self.loads == 0;
                self.loads += 1;
                let filter = self.picker.filter.clone();
                let kept = self
                    .picker
                    .current_index()
                    .and_then(|at| self.rows.get(at))
                    .map(|row| row.id.clone());
                let now = now_seconds();
                let items = rows.iter().map(|row| row_text(row, now)).collect();
                self.rows = rows;
                self.picker = Picker::new(items);
                self.picker.set_page(self.list);
                self.picker.set_filter(&filter);
                for (index, row) in self.rows.iter().enumerate() {
                    let behind = current.is_some_and(|target| is_current(row, target));
                    let fresh = match seen.get(&row.id) {
                        Some(&stamp) if !(first && behind) => row.last_at > stamp,
                        Some(_) => false,
                        None => !first,
                    };
                    if first && !fresh {
                        seen.insert(row.id.clone(), row.last_at);
                    }
                    self.picker.set_bold(index, fresh);
                }
                if let Some(index) =
                    kept.and_then(|id| self.rows.iter().position(|row| row.id == id))
                {
                    self.picker.select_index(index);
                }
                self.asked.is_empty()
            }
            Err(error) => {
                self.error = Some(error);
                false
            }
        }
    }

    pub fn set_layout(&mut self, top: usize, rows: usize) {
        self.top = top;
        self.list = rows.saturating_sub(HEAD_ROWS).max(1);
        self.picker.set_page(self.list);
    }

    pub fn up(&mut self) {
        match self.slot {
            Slot::Entry => self.slot = Slot::Search,
            Slot::Search => {
                if self.picker.visible_len() > 0 {
                    self.slot = Slot::List;
                    self.picker.selected = 0;
                }
            }
            Slot::List => {
                if self.picker.selected > 0 {
                    self.picker.step(-1);
                } else {
                    self.slot = Slot::Entry;
                }
            }
        }
    }

    pub fn cycle(&mut self, forward: bool) {
        let rows = self.picker.visible_len() > 0;
        self.slot = match (self.slot, forward) {
            (Slot::Entry, true) | (Slot::Search, false) if rows => Slot::List,
            (Slot::Entry, true) | (Slot::Search, false) => Slot::Search,
            (Slot::List, true) | (Slot::Entry, false) => Slot::Search,
            (Slot::Search, true) | (Slot::List, false) => Slot::Entry,
        };
    }

    pub fn home(&mut self) {
        if self.picker.visible_len() > 0 {
            self.slot = Slot::List;
            self.picker.selected = 0;
        }
    }

    pub fn down(&mut self) {
        match self.slot {
            Slot::List => {
                if self.picker.selected + 1 >= self.picker.visible_len() {
                    self.slot = Slot::Search;
                } else {
                    self.picker.step(1);
                }
            }
            Slot::Search => self.slot = Slot::Entry,
            Slot::Entry => {
                if self.picker.visible_len() > 0 {
                    self.slot = Slot::List;
                    self.picker.selected = 0;
                } else {
                    self.slot = Slot::Search;
                }
            }
        }
    }

    pub fn key(&mut self, key: PickKey) -> Pick {
        match key {
            PickKey::Up => {
                self.up();
                Pick::None
            }
            PickKey::Down => {
                self.down();
                Pick::None
            }
            PickKey::Esc => Pick::Close,
            PickKey::Enter => self.open(self.picker.current_index()),
            PickKey::PageUp | PickKey::PageDown => {
                let last = self.picker.selected + 1 >= self.picker.visible_len();
                if self.slot == Slot::List && key == PickKey::PageDown && last {
                    self.down();
                } else if self.slot == Slot::List {
                    self.picker.apply_key(key);
                }
                Pick::None
            }
            PickKey::Char(_) | PickKey::Backspace => {
                self.slot = Slot::Search;
                let before = self.picker.filter.clone();
                if let PickOutcome::Cancelled = self.picker.apply_key(key) {
                    return Pick::Close;
                }
                if self.picker.filter != before {
                    self.typed_at = Some(Instant::now());
                }
                Pick::None
            }
        }
    }

    pub fn click(&mut self, row: u16) -> Pick {
        let Some(offset) = (row as usize).checked_sub(self.top + HEAD_ROWS) else {
            return Pick::None;
        };
        self.open(self.picker.index_at(offset, self.list))
    }

    pub fn scroll(&mut self, delta: isize) {
        if self.picker.visible_len() == 0 {
            return;
        }
        self.slot = Slot::List;
        self.picker.step(delta);
    }

    pub fn due_fetch(&mut self, now: Instant) -> Option<Fetch> {
        if let Some(typed_at) = self.typed_at {
            if now.duration_since(typed_at) < REQUERY_AFTER {
                return None;
            }
            self.typed_at = None;
            if self.picker.filter != self.asked {
                return Some(self.fetch(now));
            }
        }
        if now.duration_since(self.fetched_at) >= REFRESH_EVERY {
            return Some(self.fetch(now));
        }
        None
    }

    fn fetch(&mut self, now: Instant) -> Fetch {
        self.asked = self.picker.filter.clone();
        self.generation += 1;
        self.loading = true;
        self.fetched_at = now;
        Fetch {
            generation: self.generation,
            search: self.asked.clone(),
        }
    }

    pub fn render(&self, theme: &Theme, cols: u16, rows: usize) -> Vec<Line<'static>> {
        let mut lines = vec![Line::styled(format!("{INDENT}{TITLE}"), theme.heading)];
        let state = match (&self.error, self.loading, self.rows.is_empty()) {
            (Some(error), _, _) => Some(Line::styled(error.clone(), theme.error)),
            (None, true, true) => Some(Line::styled(LOADING.to_string(), theme.muted)),
            (None, _, true) => Some(Line::styled(NOTHING_YET.to_string(), theme.muted)),
            (None, _, false) if self.picker.visible_len() == 0 => {
                Some(Line::styled(NOTHING_MATCHES.to_string(), theme.muted))
            }
            _ => None,
        };
        match state {
            Some(line) => lines.push(line),
            None if self.slot == Slot::List => {
                lines.extend(self.picker.render(theme, cols, self.list))
            }
            None => lines.extend(self.picker.render_unmarked(theme, cols, self.list)),
        }
        lines.truncate(rows.max(1));
        while lines.len() < rows {
            lines.push(Line::raw(""));
        }
        lines
    }

    pub fn search_line(&self, theme: &Theme, cols: u16) -> (Line<'static>, u16) {
        let label = format!("{} ", labeled(SEARCH_LABEL, self.slot == Slot::Search));
        let budget = (cols as usize).saturating_sub(wrap::width(&label));
        let shown = wrap::clip(&self.picker.filter, budget).to_string();
        let col = (wrap::width(&label) + wrap::width(&shown)) as u16;
        (
            Line::from(vec![Span::styled(label, theme.prompt), Span::raw(shown)]),
            col,
        )
    }

    fn open(&self, index: Option<usize>) -> Pick {
        match index.and_then(|at| self.rows.get(at)) {
            Some(row) => Pick::Open(row.clone()),
            None => Pick::None,
        }
    }
}

pub fn labeled(label: &str, focused: bool) -> String {
    let caret = if focused { FOCUS_CARET } else { PROMPT_IDLE };
    format!("{label} {caret}")
}

fn is_current(row: &ConversationRow, target: &Target) -> bool {
    match target {
        Target::Channel(channel) => row.channel.as_deref() == Some(channel.as_str()),
        Target::Conversation(id) => &row.id == id,
    }
}

pub fn row_text(row: &ConversationRow, now_seconds: u64) -> String {
    let origin = wrap::clip(&origin(row), ORIGIN_WIDTH).to_string();
    let age = age(row.last_at as u64, now_seconds);
    let mut text = format!(
        "{origin:<ORIGIN_WIDTH$}{GAP}{age:>AGE_WIDTH$}{GAP}{}",
        row.title
    );
    if let Some(speaker) = &row.speaker {
        text.push_str(GAP);
        text.push_str(speaker);
    }
    if !row.main {
        text.push_str(GAP);
        text.push_str(&row.agent);
    }
    text
}

fn origin(row: &ConversationRow) -> String {
    match &row.surface_label {
        Some(label) => label.clone(),
        None => surface_word(&row.surface),
    }
}

pub fn surface_word(surface: &str) -> String {
    match surface {
        "ufo" => "Terminal".to_string(),
        "web" => "Web".to_string(),
        "slack" => "Slack".to_string(),
        "imessage" => "iMessage".to_string(),
        other => {
            let name = other.strip_prefix("extension:").unwrap_or(other);
            let mut chars = name.chars();
            match chars.next() {
                Some(first) => first.to_uppercase().chain(chars).collect(),
                None => String::new(),
            }
        }
    }
}

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

fn now_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs())
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use ratatui::style::Modifier;

    use super::*;
    use crate::ui::theme::{ColorMode, Scheme};

    const ROWS: usize = 12;

    fn row(id: &str, title: &str, surface: &str, agent: &str) -> ConversationRow {
        ConversationRow {
            id: id.to_string(),
            title: title.to_string(),
            surface: surface.to_string(),
            surface_label: None,
            speaker: None,
            agent: agent.to_string(),
            main: agent == "assistant",
            last_at: now_seconds() as f64 - 2.0 * HOUR as f64,
            postable: true,
            channel: None,
        }
    }

    fn theme() -> Theme {
        Theme::for_mode(ColorMode::TrueColor, Scheme::Dark)
    }

    fn fresh(back: bool) -> Conversations {
        Conversations::new(back, Vec::new(), &HashMap::new())
    }

    fn loaded(rows: Vec<ConversationRow>) -> Conversations {
        let mut page = fresh(true);
        page.set_layout(0, ROWS);
        load(&mut page, 1, Ok(rows));
        page
    }

    fn load(
        page: &mut Conversations,
        generation: u32,
        result: Result<Vec<ConversationRow>, String>,
    ) {
        let mut seen = HashMap::new();
        page.loaded(generation, result, &mut seen, None);
    }

    fn text(lines: &[Line<'static>]) -> Vec<String> {
        lines.iter().map(Line::to_string).collect()
    }

    fn bold_rows(page: &mut Conversations) -> Vec<bool> {
        let was = page.slot;
        page.slot = Slot::Entry;
        let lines = page.render(&theme(), 80, ROWS);
        page.slot = was;
        lines[HEAD_ROWS..HEAD_ROWS + page.rows.len()]
            .iter()
            .map(|line| {
                line.spans
                    .iter()
                    .any(|span| span.style.add_modifier.contains(Modifier::BOLD))
            })
            .collect()
    }

    #[test]
    fn a_row_states_origin_age_title_speaker_and_the_agent_when_not_the_main_one() {
        let now = now_seconds();
        let mut slack = row("c1", "Who owns the pager", "slack", "assistant");
        slack.surface_label = Some("#eng".to_string());
        slack.speaker = Some("Nate Ford".to_string());
        slack.last_at = (now - 3 * DAY) as f64;
        assert_eq!(
            row_text(&slack, now),
            "#eng               3d  Who owns the pager  Nate Ford"
        );
        let web = row("c2", "Deploy plan", "web", "notes");
        assert_eq!(
            row_text(&web, now),
            "Web                2h  Deploy plan  notes"
        );
        let app = row("c3", "Ping", "extension:notification", "notification");
        assert!(row_text(&app, now).starts_with("Notification    "));
        assert!(row_text(&app, now).ends_with("  notification"));
    }

    #[test]
    fn age_names_the_largest_unit_that_reaches_one() {
        assert_eq!(age(1000, 1030), "30s");
        assert_eq!(age(1000, 1000 + 5 * MINUTE), "5m");
        assert_eq!(age(1000, 1000 + 3 * HOUR), "3h");
        assert_eq!(age(1000, 1000 + 2 * DAY), "2d");
        assert_eq!(age(1000, 1000 + 3 * WEEK), "3w");
        assert_eq!(age(2000, 1000), "0s");
    }

    #[test]
    fn the_cursor_walks_entry_search_and_list_as_one_column() {
        let mut page = loaded(vec![
            row("c1", "Deploy plan", "web", "assistant"),
            row("c2", "list files", "ufo", "assistant"),
        ]);
        assert_eq!(page.slot(), Slot::Entry);
        page.down();
        assert_eq!(
            page.slot(),
            Slot::List,
            "Down from the entry wraps to the list"
        );
        assert_eq!(page.picker.current_index(), Some(0));
        page.up();
        assert_eq!(
            page.slot(),
            Slot::Entry,
            "Up from the top row wraps to the entry"
        );
        page.up();
        assert_eq!(page.slot(), Slot::Search);
        page.up();
        assert_eq!(page.slot(), Slot::List);
        assert_eq!(
            page.picker.current_index(),
            Some(0),
            "the list opens at its newest row"
        );
        page.down();
        assert_eq!(page.picker.current_index(), Some(1));
        page.down();
        assert_eq!(
            page.slot(),
            Slot::Search,
            "past the last row is the search line"
        );
        page.down();
        assert_eq!(page.slot(), Slot::Entry);

        let mut empty = loaded(Vec::new());
        empty.up();
        empty.up();
        assert_eq!(empty.slot(), Slot::Search, "an empty list is not entered");
    }

    #[test]
    fn a_long_list_is_one_key_from_the_entry_bar() {
        let mut page = loaded(
            (0..40)
                .map(|n| row(&format!("c{n}"), &format!("thread {n}"), "web", "assistant"))
                .collect(),
        );
        page.cycle(true);
        assert_eq!(
            page.slot(),
            Slot::List,
            "Tab from the entry enters the list"
        );
        page.key(PickKey::PageDown);
        assert_eq!(page.slot(), Slot::List);
        page.cycle(true);
        assert_eq!(page.slot(), Slot::Search);
        page.cycle(true);
        assert_eq!(page.slot(), Slot::Entry);
        page.cycle(false);
        assert_eq!(page.slot(), Slot::Search);
        page.cycle(false);
        assert_eq!(page.slot(), Slot::List);
        page.home();
        assert_eq!(page.picker.current_index(), Some(0));
        for _ in 0..4 {
            page.key(PickKey::PageDown);
        }
        assert_eq!(
            page.picker.current_index(),
            Some(39),
            "PageDown clamps at the last row"
        );
        page.key(PickKey::PageDown);
        assert_eq!(
            page.slot(),
            Slot::Search,
            "and once more falls out to the search line"
        );
        let mut empty = loaded(Vec::new());
        empty.cycle(true);
        assert_eq!(empty.slot(), Slot::Search, "an empty list is skipped");
        empty.home();
        assert_eq!(empty.slot(), Slot::Search);
    }

    #[test]
    fn enter_opens_the_highlighted_row_and_esc_closes() {
        let mut page = loaded(vec![
            row("c1", "Deploy plan", "web", "assistant"),
            row("c2", "list files", "ufo", "assistant"),
        ]);
        assert_eq!(page.key(PickKey::Up), Pick::None);
        assert_eq!(page.key(PickKey::Up), Pick::None);
        assert_eq!(page.key(PickKey::Down), Pick::None);
        assert!(matches!(page.key(PickKey::Enter), Pick::Open(opened) if opened.id == "c2"));
        assert_eq!(page.key(PickKey::Esc), Pick::Close);
    }

    #[test]
    fn typing_narrows_at_once_from_the_search_line_and_asks_the_workspace_after_a_pause() {
        let mut page = loaded(vec![
            row("c1", "Deploy plan", "web", "assistant"),
            row("c2", "list files", "ufo", "assistant"),
        ]);
        page.up();
        page.up();
        for ch in "files".chars() {
            page.key(PickKey::Char(ch));
        }
        assert_eq!(page.slot(), Slot::Search, "typing in the list is searching");
        assert!(matches!(page.key(PickKey::Enter), Pick::Open(opened) if opened.id == "c2"));
        let typed = Instant::now();
        assert_eq!(page.due_fetch(typed), None);
        assert_eq!(
            page.due_fetch(typed + REQUERY_AFTER),
            Some(Fetch {
                generation: 2,
                search: "files".to_string(),
            })
        );
        assert_eq!(page.due_fetch(typed + 2 * REQUERY_AFTER), None);
        load(
            &mut page,
            1,
            Ok(vec![row("c9", "stale answer", "web", "assistant")]),
        );
        assert_eq!(
            page.rows.len(),
            2,
            "an earlier generation's answer is dropped"
        );
        load(
            &mut page,
            2,
            Ok(vec![row("c3", "old files thread", "slack", "assistant")]),
        );
        assert_eq!(page.rows.len(), 1);
        assert_eq!(
            page.picker.filter, "files",
            "the typed words survive a reload"
        );
        assert!(matches!(page.key(PickKey::Enter), Pick::Open(opened) if opened.id == "c3"));
    }

    #[test]
    fn a_click_opens_the_row_under_it_and_nothing_above_the_list() {
        let mut page = loaded(vec![
            row("c1", "Deploy plan", "web", "assistant"),
            row("c2", "list files", "ufo", "assistant"),
        ]);
        page.set_layout(9, ROWS);
        assert_eq!(page.click(9), Pick::None, "the heading");
        assert_eq!(page.click(3), Pick::None, "the mark above the page");
        assert!(
            matches!(page.click(9 + HEAD_ROWS as u16 + 1), Pick::Open(opened) if opened.id == "c2")
        );
        assert_eq!(page.click(9 + HEAD_ROWS as u16 + 5), Pick::None);
    }

    #[test]
    fn the_list_refreshes_on_its_interval_and_keeps_the_selection() {
        let mut page = loaded(vec![
            row("c1", "Deploy plan", "web", "assistant"),
            row("c2", "list files", "ufo", "assistant"),
        ]);
        let opened = Instant::now();
        assert_eq!(page.due_fetch(opened), None);
        page.up();
        page.up();
        page.down();
        let refresh = page
            .due_fetch(opened + REFRESH_EVERY)
            .expect("the interval elapsed");
        assert_eq!(refresh.generation, 2);
        assert_eq!(refresh.search, "");
        assert_eq!(
            page.due_fetch(opened + REFRESH_EVERY + Duration::from_millis(100)),
            None,
            "one refresh per interval"
        );
        load(
            &mut page,
            2,
            Ok(vec![
                row("c3", "newest thread", "slack", "assistant"),
                row("c2", "list files", "ufo", "assistant"),
                row("c1", "Deploy plan", "web", "assistant"),
            ]),
        );
        assert!(matches!(page.key(PickKey::Enter), Pick::Open(kept) if kept.id == "c2"));
        for ch in "deploy".chars() {
            page.key(PickKey::Char(ch));
        }
        let typed = Instant::now();
        assert_eq!(
            page.due_fetch(typed + REQUERY_AFTER)
                .map(|fetch| fetch.search),
            Some("deploy".to_string())
        );
        let again = page
            .due_fetch(typed + REQUERY_AFTER + REFRESH_EVERY)
            .expect("the refresh carries the search");
        assert_eq!(again.search, "deploy");
    }

    #[test]
    fn a_page_opens_on_the_rows_it_is_given_bold_where_they_moved_since_seen() {
        let mut seen = HashMap::new();
        let moved = row("c1", "Who owns the pager", "slack", "assistant");
        seen.insert("c1".to_string(), moved.last_at - 60.0);
        let still = row("c2", "Deploy plan", "web", "assistant");
        seen.insert("c2".to_string(), still.last_at);
        let unmet = row("c3", "Ping", "web", "assistant");
        let rows = vec![moved, still, unmet];
        let mut page = Conversations::new(true, rows.clone(), &seen);
        page.set_layout(0, ROWS);
        let lines = text(&page.render(&theme(), 80, ROWS));
        assert!(lines[1].contains("Who owns the pager"), "{lines:?}");
        assert!(
            !lines.iter().any(|line| line.contains(LOADING)),
            "{lines:?}"
        );
        assert_eq!(bold_rows(&mut page), vec![true, false, false]);
        assert!(
            page.loaded(1, Ok(rows.clone()), &mut seen, None),
            "the whole list landed"
        );
        page.key(PickKey::Char('p'));
        let fetch = page
            .due_fetch(Instant::now() + REQUERY_AFTER)
            .expect("the typed word is asked");
        assert!(
            !page.loaded(fetch.generation, Ok(rows), &mut seen, None),
            "a search's answer is not the list"
        );
    }

    #[test]
    fn the_cache_keeps_the_list_for_the_sign_in_it_came_from() {
        let home =
            std::env::temp_dir().join(format!("ufo-conversations-cache-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&home);
        std::fs::create_dir_all(&home).expect("a scratch home");
        let cache = Cache::at(&home, "host.1");
        assert!(cache.load().is_empty(), "nothing stored yet");
        let rows = vec![row("c1", "Who owns the pager", "slack", "assistant")];
        cache.store(&rows);
        assert_eq!(Cache::at(&home, "host.1").load(), rows);
        assert!(
            Cache::at(&home, "host.2").load().is_empty(),
            "another sign-in's list"
        );
        std::fs::write(home.join(CONVERSATIONS_FILE), "{").expect("a torn file");
        assert!(cache.load().is_empty(), "a torn file is no list");
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn a_row_that_moved_since_the_member_saw_it_is_bold_until_opened() {
        let mut seen = HashMap::new();
        let current = Target::Channel("abc".to_string());
        let mut mine = row("c1", "list files", "ufo", "assistant");
        mine.channel = Some("abc".to_string());
        mine.last_at = 100.0;
        let mut theirs = row("c2", "Who owns the pager", "slack", "assistant");
        theirs.last_at = 200.0;
        seen.insert("c1".to_string(), 50.0);
        let mut page = fresh(true);
        page.set_layout(0, ROWS);
        page.loaded(
            1,
            Ok(vec![mine.clone(), theirs.clone()]),
            &mut seen,
            Some(&current),
        );
        assert_eq!(
            bold_rows(&mut page),
            vec![false, false],
            "first sight is seen as it stands"
        );
        assert_eq!(
            seen["c1"], 100.0,
            "the conversation behind the page is seen where it is"
        );
        assert_eq!(seen["c2"], 200.0);

        mine.last_at = 150.0;
        let arrived = row("c3", "new thread", "web", "assistant");
        page.due_fetch(Instant::now() + REFRESH_EVERY);
        page.loaded(
            2,
            Ok(vec![mine.clone(), arrived, theirs.clone()]),
            &mut seen,
            Some(&current),
        );
        assert_eq!(bold_rows(&mut page), vec![true, true, false]);
        assert!(
            !seen.contains_key("c3"),
            "a fresh row stays fresh until opened"
        );

        seen.insert("c1".to_string(), 150.0);
        let mut reopened = fresh(true);
        reopened.set_layout(0, ROWS);
        reopened.loaded(1, Ok(vec![mine, theirs]), &mut seen, Some(&current));
        assert_eq!(bold_rows(&mut reopened), vec![false, false]);

        let mut stale = HashMap::from([("c2".to_string(), 100.0)]);
        let mut later = fresh(true);
        later.set_layout(0, ROWS);
        later.loaded(
            1,
            Ok(vec![row("c2", "Who owns the pager", "slack", "assistant")]),
            &mut stale,
            None,
        );
        assert_eq!(
            bold_rows(&mut later),
            vec![true],
            "a thread that moved since it was last opened is bold on a later page too"
        );
    }

    #[test]
    fn the_page_states_the_list_and_the_search_line_carries_the_words() {
        let waiting = fresh(false);
        let drawn = text(&waiting.render(&theme(), 60, 6));
        assert_eq!(drawn.len(), 6);
        assert_eq!(drawn[0], "  UFO Chats");
        assert_eq!(drawn[1], LOADING);
        assert_eq!(drawn[5], "");

        let mut page = loaded(vec![row("c1", "Deploy plan", "web", "assistant")]);
        let drawn = text(&page.render(&theme(), 60, 6));
        assert!(drawn[1].contains("Deploy plan"), "{drawn:?}");
        assert!(
            drawn[1].starts_with("  "),
            "no highlight while the cursor is in the entry"
        );
        page.up();
        page.up();
        let drawn = text(&page.render(&theme(), 60, 6));
        assert!(drawn[1].starts_with("❯ "), "{drawn:?}");
        for ch in "zzz".chars() {
            page.key(PickKey::Char(ch));
        }
        assert_eq!(text(&page.render(&theme(), 60, 6))[1], NOTHING_MATCHES);
        let (search, col) = page.search_line(&theme(), 60);
        assert_eq!(
            search.to_string(),
            "Search ❯ zzz",
            "the search line holds the cursor"
        );
        assert_eq!(col, wrap::width("Search ❯ zzz") as u16);
        page.down();
        let (idle, _) = page.search_line(&theme(), 60);
        assert_eq!(
            idle.to_string(),
            "Search › zzz",
            "and reads idle once it does not"
        );

        let mut failed = fresh(true);
        load(&mut failed, 1, Err("lost connection".to_string()));
        assert_eq!(text(&failed.render(&theme(), 60, 6))[1], "lost connection");

        let empty = loaded(Vec::new());
        assert_eq!(text(&empty.render(&theme(), 60, 6))[1], NOTHING_YET);
    }
}
