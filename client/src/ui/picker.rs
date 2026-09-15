use std::cmp::Reverse;
use std::collections::VecDeque;
use std::fs;
use std::path::Path;

use ratatui::style::Style;
use ratatui::text::{Line, Span};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

use crate::ui::theme::Theme;

const SCORE_MATCH: i32 = 16;
const SCORE_START: i32 = 32;
const SCORE_SEGMENT: i32 = 24;
const SCORE_CONSECUTIVE: i32 = 20;
const PENALTY_GAP: i32 = -2;
const PENALTY_LEADING: i32 = -1;
const UNREACHABLE: i32 = i32::MIN / 2;
const SEGMENT_MARKS: [char; 5] = [' ', '/', '-', '_', '.'];

const MARKER: &str = "❯ ";
const INDENT: &str = "  ";
const STATUS_GAP: &str = " ";
const STATUS_COLS: usize = 2;
const ELLIPSIS: &str = "…";
const PAGE_ROWS: usize = 10;

const MAX_DEPTH: usize = 5;
const MAX_ENTRIES: usize = 4000;
const SKIP_DIRS: [&str; 3] = ["node_modules", "target", "__pycache__"];

#[derive(Debug, Clone, PartialEq)]
pub enum PickOutcome {
    Continue,
    Picked(String),
    Cancelled,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PickKey {
    Up,
    Down,
    PageUp,
    PageDown,
    Char(char),
    Backspace,
    Enter,
    Esc,
}

pub struct Picker {
    items: Vec<String>,
    pub filter: String,
    pub selected: usize,
    visible: Vec<usize>,
    page: usize,
}

impl Picker {
    pub fn new(items: Vec<String>) -> Picker {
        let visible = (0..items.len()).collect();
        Picker {
            items,
            filter: String::new(),
            selected: 0,
            visible,
            page: PAGE_ROWS,
        }
    }

    pub fn apply_key(&mut self, key: PickKey) -> PickOutcome {
        match key {
            PickKey::Up => self.step(-1),
            PickKey::Down => self.step(1),
            PickKey::PageUp => self.jump(-(self.page as isize)),
            PickKey::PageDown => self.jump(self.page as isize),
            PickKey::Char(ch) => {
                self.filter.push(ch);
                self.rank();
            }
            PickKey::Backspace => {
                self.filter.pop();
                self.rank();
            }
            PickKey::Enter => {
                return match self.current() {
                    Some(item) => PickOutcome::Picked(item.to_string()),
                    None => PickOutcome::Continue,
                }
            }
            PickKey::Esc => return PickOutcome::Cancelled,
        }
        PickOutcome::Continue
    }

    pub fn step(&mut self, delta: isize) {
        if self.visible.is_empty() {
            return;
        }
        let len = self.visible.len() as isize;
        self.selected = ((self.selected as isize + delta).rem_euclid(len)) as usize;
    }

    pub fn set_page(&mut self, rows: usize) {
        self.page = rows.max(1);
    }

    pub fn set_filter(&mut self, filter: &str) {
        self.filter = filter.to_string();
        self.rank();
    }

    fn rank(&mut self) {
        let mut scored: Vec<(i32, usize)> = self
            .items
            .iter()
            .enumerate()
            .filter_map(|(index, item)| fuzzy_score(&self.filter, item).map(|score| (score, index)))
            .collect();
        scored.sort_by_key(|&(score, _)| Reverse(score));
        self.visible = scored.into_iter().map(|(_, index)| index).collect();
        self.selected = 0;
    }

    fn jump(&mut self, delta: isize) {
        if self.visible.is_empty() {
            return;
        }
        let last = (self.visible.len() - 1) as isize;
        self.selected = (self.selected as isize + delta).clamp(0, last) as usize;
    }

    pub fn current(&self) -> Option<&str> {
        self.visible
            .get(self.selected)
            .map(|&index| self.items[index].as_str())
    }

    pub fn visible_len(&self) -> usize {
        self.visible.len()
    }

    pub fn current_index(&self) -> Option<usize> {
        self.visible.get(self.selected).copied()
    }

    pub fn item(&self, index: usize) -> Option<&str> {
        self.items.get(index).map(String::as_str)
    }

    pub fn select_index(&mut self, index: usize) -> bool {
        match self.visible.iter().position(|&at| at == index) {
            Some(row) => {
                self.selected = row;
                true
            }
            None => false,
        }
    }

    fn window(&self, max_rows: usize) -> (usize, usize) {
        let overflow = self.visible.len() > max_rows;
        let rows = if overflow {
            max_rows.saturating_sub(1).max(1)
        } else {
            max_rows
        };
        let start = self
            .selected
            .saturating_sub(rows / 2)
            .min(self.visible.len().saturating_sub(rows));
        (start, rows)
    }

    pub fn index_at(&self, offset: usize, max_rows: usize) -> Option<usize> {
        if self.visible.is_empty() || max_rows == 0 {
            return None;
        }
        let (start, rows) = self.window(max_rows);
        if offset >= rows {
            return None;
        }
        self.visible.get(start + offset).copied()
    }

    pub fn render(&self, theme: &Theme, width: u16, max_rows: usize) -> Vec<Line<'static>> {
        self.draw(theme, width, max_rows, true, None)
    }

    pub fn render_unmarked(
        &self,
        theme: &Theme,
        width: u16,
        max_rows: usize,
    ) -> Vec<Line<'static>> {
        self.draw(theme, width, max_rows, false, None)
    }

    /// Every row leads with the one-cell status `status` draws for its item.
    pub fn render_status(
        &self,
        theme: &Theme,
        width: u16,
        max_rows: usize,
        marked: bool,
        status: &dyn Fn(usize) -> Span<'static>,
    ) -> Vec<Line<'static>> {
        self.draw(theme, width, max_rows, marked, Some(status))
    }

    fn draw(
        &self,
        theme: &Theme,
        width: u16,
        max_rows: usize,
        marked: bool,
        status: Option<&dyn Fn(usize) -> Span<'static>>,
    ) -> Vec<Line<'static>> {
        if self.visible.is_empty() || max_rows == 0 {
            return Vec::new();
        }
        let overflow = self.visible.len() > max_rows;
        let (start, rows) = self.window(max_rows);
        let budget =
            (width as usize).saturating_sub(MARKER.width() + status.map_or(0, |_| STATUS_COLS));
        let mut lines = Vec::new();
        for (row, &index) in self.visible.iter().enumerate().skip(start).take(rows) {
            let item = &self.items[index];
            let hits = fuzzy_match(&self.filter, item)
                .map(|(_, at)| at)
                .unwrap_or_default();
            let selected = marked && row == self.selected;
            let base = if selected { theme.selected } else { theme.text };
            let mut spans = vec![Span::styled(
                if selected { MARKER } else { INDENT }.to_string(),
                base,
            )];
            if let Some(status) = status {
                spans.push(status(index));
                spans.push(Span::styled(STATUS_GAP.to_string(), base));
            }
            spans.extend(row_spans(
                item,
                &hits,
                base,
                base.patch(theme.accent),
                budget,
            ));
            lines.push(Line::from(spans));
        }
        if overflow {
            lines.push(Line::styled(
                format!("{INDENT}({}/{})", self.selected + 1, self.visible.len()),
                theme.muted,
            ));
        }
        lines
    }
}

fn row_spans(
    item: &str,
    hits: &[usize],
    base: Style,
    hit: Style,
    budget: usize,
) -> Vec<Span<'static>> {
    let clipped = UnicodeWidthStr::width(item) > budget;
    let room = if clipped {
        budget.saturating_sub(ELLIPSIS.width())
    } else {
        budget
    };
    let mut spans = Vec::new();
    let mut run = String::new();
    let mut lit = false;
    let mut used = 0;
    for (at, ch) in item.char_indices() {
        let step = UnicodeWidthChar::width(ch).unwrap_or(0);
        if used + step > room {
            break;
        }
        used += step;
        let matched = hits.contains(&at);
        if matched != lit && !run.is_empty() {
            spans.push(Span::styled(
                std::mem::take(&mut run),
                if lit { hit } else { base },
            ));
        }
        lit = matched;
        run.push(ch);
    }
    if !run.is_empty() {
        spans.push(Span::styled(run, if lit { hit } else { base }));
    }
    if clipped {
        spans.push(Span::styled(ELLIPSIS.to_string(), base));
    }
    spans
}

pub fn fuzzy_score(needle: &str, haystack: &str) -> Option<i32> {
    fuzzy_match(needle, haystack).map(|(score, _)| score)
}

pub fn fuzzy_match(needle: &str, haystack: &str) -> Option<(i32, Vec<usize>)> {
    let want: Vec<char> = needle.chars().map(fold).collect();
    let hay: Vec<(usize, char)> = haystack.char_indices().collect();
    if want.is_empty() {
        return Some((0, Vec::new()));
    }
    if want.len() > hay.len() {
        return None;
    }
    let (span, best) = score_matrix(&want, &hay);
    let tail = (want.len() - 1) * span;
    let end = (0..span).rev().max_by_key(|&at| best[tail + at])?;
    if best[tail + end] <= UNREACHABLE {
        return None;
    }
    Some((best[tail + end], trace(&hay, &best, span, end)))
}

fn segment_bonus(hay: &[(usize, char)], at: usize) -> i32 {
    match at {
        0 => SCORE_START,
        _ if SEGMENT_MARKS.contains(&hay[at - 1].1) => SCORE_SEGMENT,
        _ => 0,
    }
}

fn score_matrix(want: &[char], hay: &[(usize, char)]) -> (usize, Vec<i32>) {
    let span = hay.len();
    let bonus: Vec<i32> = (0..span).map(|at| segment_bonus(hay, at)).collect();
    let mut best = vec![UNREACHABLE; want.len() * span];
    let mut runs = vec![UNREACHABLE; want.len() * span];
    for (row, &wanted) in want.iter().enumerate() {
        for at in 0..span {
            let mut score = UNREACHABLE;
            if wanted == fold(hay[at].1) {
                score = if row == 0 {
                    SCORE_MATCH + bonus[at] + PENALTY_LEADING * at as i32
                } else if at == 0 {
                    UNREACHABLE
                } else {
                    let through_gap = add(runs[(row - 1) * span + at - 1], SCORE_MATCH + bonus[at]);
                    let through_run = add(
                        best[(row - 1) * span + at - 1],
                        SCORE_MATCH + bonus[at].max(SCORE_CONSECUTIVE),
                    );
                    through_gap.max(through_run)
                };
            }
            best[row * span + at] = score;
            runs[row * span + at] = if at == 0 {
                score
            } else {
                score.max(add(runs[row * span + at - 1], PENALTY_GAP))
            };
        }
    }
    (span, best)
}

fn trace(hay: &[(usize, char)], best: &[i32], span: usize, end: usize) -> Vec<usize> {
    let rows = best.len() / span;
    let mut at = end;
    let mut hits = vec![hay[at].0];
    for row in (1..rows).rev() {
        let here = best[row * span + at];
        let bonus = segment_bonus(hay, at);
        let run = add(
            best[(row - 1) * span + at - 1],
            SCORE_MATCH + bonus.max(SCORE_CONSECUTIVE),
        );
        at = if run == here {
            at - 1
        } else {
            (0..at)
                .rev()
                .find(|&prev| {
                    add(
                        best[(row - 1) * span + prev],
                        SCORE_MATCH + bonus + PENALTY_GAP * (at - 1 - prev) as i32,
                    ) == here
                })
                .expect("scored alignment has a predecessor")
        };
        hits.push(hay[at].0);
    }
    hits.reverse();
    hits
}

fn add(base: i32, gain: i32) -> i32 {
    if base <= UNREACHABLE {
        UNREACHABLE
    } else {
        base + gain
    }
}

fn fold(ch: char) -> char {
    ch.to_lowercase().next().unwrap_or(ch)
}

pub fn path_candidates(cwd: &Path, token: &str, limit: usize) -> Vec<String> {
    let mut queue = VecDeque::from([(cwd.to_path_buf(), String::new(), 0usize)]);
    let mut seen = 0;
    let mut found: Vec<String> = Vec::new();
    while let Some((dir, prefix, depth)) = queue.pop_front() {
        let Ok(entries) = fs::read_dir(&dir) else {
            continue;
        };
        let mut names: Vec<(String, bool)> = Vec::new();
        for entry in entries.flatten() {
            if seen >= MAX_ENTRIES {
                break;
            }
            seen += 1;
            let Ok(kind) = entry.file_type() else {
                continue;
            };
            let name = entry.file_name().to_string_lossy().into_owned();
            if kind.is_dir() && (name.starts_with('.') || SKIP_DIRS.contains(&name.as_str())) {
                continue;
            }
            names.push((name, kind.is_dir()));
        }
        names.sort();
        for (name, is_dir) in names {
            if !is_dir {
                found.push(format!("{prefix}{name}"));
                continue;
            }
            let child = format!("{prefix}{name}/");
            if depth + 1 < MAX_DEPTH {
                queue.push_back((dir.join(&name), child.clone(), depth + 1));
            }
            found.push(child);
        }
        if seen >= MAX_ENTRIES {
            break;
        }
    }
    let mut scored: Vec<(i32, String)> = found
        .into_iter()
        .filter_map(|path| fuzzy_score(token, &path).map(|score| (score, path)))
        .collect();
    scored.sort_by_key(|(score, _)| Reverse(*score));
    scored
        .into_iter()
        .take(limit)
        .map(|(_, path)| path)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ui::theme::{ColorMode, Scheme};
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn theme() -> Theme {
        Theme::for_mode(ColorMode::TrueColor, Scheme::Dark)
    }

    fn rendered(lines: &[Line<'static>]) -> Vec<String> {
        lines
            .iter()
            .map(|line| {
                line.spans
                    .iter()
                    .map(|span| span.content.as_ref())
                    .collect()
            })
            .collect()
    }

    fn tree(tag: &str) -> PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("ufo-picker-{tag}-{stamp}"));
        fs::create_dir_all(&root).unwrap();
        root
    }

    #[test]
    fn selecting_by_item_index_follows_the_filter() {
        let mut picker = Picker::new(vec!["alpha".into(), "beta".into(), "gamma".into()]);
        assert!(picker.select_index(2));
        assert_eq!(picker.current(), Some("gamma"));
        picker.set_filter("a");
        assert!(picker.select_index(2));
        assert_eq!(picker.current(), Some("gamma"));
        assert!(!picker.select_index(9));
        assert_eq!(picker.current(), Some("gamma"));
        let unmarked = rendered(&picker.render_unmarked(&theme(), 40, 5));
        assert!(
            unmarked.iter().all(|row| row.starts_with(INDENT)),
            "{unmarked:?}"
        );
    }

    #[test]
    fn a_status_leads_each_row_wherever_the_window_puts_it_and_the_text_yields_its_cells() {
        let items: Vec<String> = (0..20)
            .map(|n| format!("item {n:02} {}", "x".repeat(40)))
            .collect();
        let mut picker = Picker::new(items);
        let status =
            |index: usize| Span::styled(if index == 9 { "●" } else { "·" }, Style::default());
        let leads = |lines: &[Line<'static>]| -> Vec<String> {
            lines
                .iter()
                .map(|line| line.spans[1].content.to_string())
                .collect()
        };
        let drawn = picker.render_status(&theme(), 20, 5, false, &status);
        assert_eq!(leads(&drawn[..4]), vec!["·"; 4]);
        assert!(drawn[0].to_string().starts_with("  · item 00"), "{drawn:?}");
        assert!(
            drawn.iter().all(|line| line.width() <= 20),
            "the status takes its cells from the text: {drawn:?}"
        );
        picker.selected = 9;
        let window = picker.render_status(&theme(), 20, 5, true, &status);
        assert_eq!(
            leads(&window[..4])
                .iter()
                .filter(|lead| *lead == "●")
                .count(),
            1
        );
        assert!(
            window
                .iter()
                .any(|line| line.to_string().starts_with("❯ ● item 09")),
            "{window:?}"
        );
    }

    #[test]
    fn a_row_offset_names_the_item_the_window_draws_there() {
        let items: Vec<String> = (0..20).map(|n| format!("item {n:02}")).collect();
        let mut picker = Picker::new(items);
        assert_eq!(picker.index_at(0, 5), Some(0));
        assert_eq!(picker.index_at(3, 5), Some(3));
        assert_eq!(
            picker.index_at(4, 5),
            None,
            "the last row is the (i/n) line"
        );
        picker.selected = 10;
        let drawn = rendered(&picker.render(&theme(), 40, 5));
        let first = drawn[0].trim_start_matches(['❯', ' ']).to_string();
        assert_eq!(
            picker.index_at(0, 5).map(|at| format!("item {at:02}")),
            Some(first)
        );
        picker.set_filter("item 1");
        assert_eq!(picker.index_at(0, 5), picker.current_index());
        picker.step(1);
        assert_eq!(picker.index_at(1, 5), picker.current_index());
        assert!(picker
            .current()
            .is_some_and(|item| item.starts_with("item 1")));
        assert_eq!(Picker::new(Vec::new()).index_at(0, 5), None);
    }

    #[test]
    fn filter_narrows_and_wraps() {
        let mut picker = Picker::new(vec!["alpha".into(), "beta".into(), "gamma".into()]);
        picker.set_filter("a");
        assert_eq!(picker.current(), Some("alpha"));
        picker.step(1);
        assert_eq!(picker.current(), Some("gamma"));
        picker.step(1);
        assert_eq!(picker.current(), Some("beta"));
        picker.step(1);
        assert_eq!(picker.current(), Some("alpha"));
        picker.set_filter("zz");
        assert_eq!(picker.current(), None);
    }

    #[test]
    fn word_start_outscores_a_buried_match() {
        assert!(
            fuzzy_score("rs", "run_server.py").unwrap() > fuzzy_score("rs", "resources").unwrap()
        );
        assert!(fuzzy_score("ap", "a/path.rs").unwrap() > fuzzy_score("ap", "apart.rs").unwrap());
        assert!(
            fuzzy_score("mts", "make-tests.sh").unwrap()
                > fuzzy_score("mts", "mktemp-shim").unwrap()
        );
    }

    #[test]
    fn consecutive_outscores_a_split_match() {
        assert!(
            fuzzy_score("conf", "config.rs").unwrap()
                > fuzzy_score("conf", "connect_first.rs").unwrap()
        );
        assert!(fuzzy_score("abc", "abcx").unwrap() > fuzzy_score("abc", "axbxc").unwrap());
        assert!(
            fuzzy_score("mkts", "mktemp-shim").unwrap()
                > fuzzy_score("mkts", "make-tests.sh").unwrap()
        );
    }

    #[test]
    fn non_matching_items_drop_out() {
        assert_eq!(fuzzy_score("zq", "alpha"), None);
        assert_eq!(fuzzy_score("abcd", "abc"), None);
        assert_eq!(fuzzy_score("ba", "abc"), None);
        assert_eq!(fuzzy_score("", "abc"), Some(0));
        assert!(fuzzy_score("ABC", "xxabc").is_some());
    }

    #[test]
    fn ranking_orders_by_score_and_keeps_ties() {
        let mut picker = Picker::new(vec![
            "vendor/pack.rs".into(),
            "alto".into(),
            "alps".into(),
            "alpha".into(),
        ]);
        picker.set_filter("al");
        assert_eq!(picker.visible, vec![1, 2, 3]);
        picker.set_filter("pk");
        assert_eq!(picker.current(), Some("vendor/pack.rs"));
    }

    #[test]
    fn empty_filter_keeps_every_item_in_order() {
        let mut picker = Picker::new(vec!["one".into(), "two".into(), "three".into()]);
        picker.set_filter("o");
        picker.set_filter("");
        assert_eq!(picker.visible_len(), 3);
        assert_eq!(picker.current(), Some("one"));
    }

    #[test]
    fn matched_positions_take_the_tightest_alignment() {
        let (_, hits) = fuzzy_match("pk", "picker").unwrap();
        assert_eq!(hits, vec![0, 3]);
        let (_, hits) = fuzzy_match("sr", "src/server.rs").unwrap();
        assert_eq!(hits, vec![0, 1]);
    }

    #[test]
    fn typing_narrows_and_enter_picks() {
        let mut picker = Picker::new(vec![
            "make-tests.sh".into(),
            "mktemp-shim".into(),
            "readme.md".into(),
        ]);
        assert_eq!(picker.apply_key(PickKey::Char('m')), PickOutcome::Continue);
        assert_eq!(picker.visible_len(), 3);
        picker.apply_key(PickKey::Char('k'));
        assert_eq!(picker.visible_len(), 2);
        picker.apply_key(PickKey::Char('t'));
        picker.apply_key(PickKey::Char('s'));
        assert_eq!(picker.visible_len(), 2);
        assert_eq!(
            picker.apply_key(PickKey::Enter),
            PickOutcome::Picked("mktemp-shim".into())
        );
        picker.apply_key(PickKey::Backspace);
        picker.apply_key(PickKey::Backspace);
        assert_eq!(picker.filter, "mk");
        assert_eq!(picker.visible_len(), 2);
    }

    #[test]
    fn typing_resets_the_selection_to_the_top() {
        let mut picker = Picker::new(vec!["alpha".into(), "alto".into(), "album".into()]);
        picker.apply_key(PickKey::Down);
        assert_eq!(picker.current(), Some("alto"));
        picker.apply_key(PickKey::Char('a'));
        assert_eq!(picker.selected, 0);
        assert_eq!(picker.current(), Some("alpha"));
    }

    #[test]
    fn enter_with_nothing_visible_continues_and_esc_cancels() {
        let mut picker = Picker::new(vec!["alpha".into()]);
        picker.set_filter("zq");
        assert_eq!(picker.apply_key(PickKey::Enter), PickOutcome::Continue);
        assert_eq!(picker.apply_key(PickKey::Esc), PickOutcome::Cancelled);
    }

    #[test]
    fn page_keys_move_by_the_window_and_stop_at_the_ends() {
        let items: Vec<String> = (0..30).map(|at| format!("item-{at:02}")).collect();
        let mut picker = Picker::new(items);
        picker.set_page(8);
        picker.apply_key(PickKey::PageDown);
        assert_eq!(picker.current(), Some("item-08"));
        picker.apply_key(PickKey::PageDown);
        picker.apply_key(PickKey::PageDown);
        picker.apply_key(PickKey::PageDown);
        assert_eq!(picker.current(), Some("item-29"));
        picker.apply_key(PickKey::PageUp);
        assert_eq!(picker.current(), Some("item-21"));
        picker.apply_key(PickKey::PageUp);
        picker.apply_key(PickKey::PageUp);
        picker.apply_key(PickKey::PageUp);
        assert_eq!(picker.current(), Some("item-00"));
    }

    #[test]
    fn render_marks_the_selection_and_accents_the_match() {
        let theme = theme();
        let mut picker = Picker::new(vec!["picker.rs".into(), "packer.rs".into()]);
        picker.set_filter("pk");
        let lines = picker.render(&theme, 40, 10);
        assert_eq!(rendered(&lines), vec!["❯ picker.rs", "  packer.rs"]);
        assert_eq!(lines[0].spans[0].content, "❯ ");
        assert_eq!(lines[0].spans[0].style.fg, theme.selected.fg);
        let accented: Vec<&str> = lines[1]
            .spans
            .iter()
            .filter(|span| span.style.fg == theme.accent.fg)
            .map(|span| span.content.as_ref())
            .collect();
        assert_eq!(accented, vec!["p", "k"]);
    }

    #[test]
    fn render_clips_a_row_to_the_width() {
        let theme = theme();
        let picker = Picker::new(vec!["a-very-long-candidate-name.txt".into()]);
        let lines = picker.render(&theme, 12, 4);
        assert_eq!(rendered(&lines), vec!["❯ a-very-lo…"]);
    }

    #[test]
    fn render_windows_around_the_selection_and_counts() {
        let theme = theme();
        let items: Vec<String> = (0..30).map(|at| format!("item-{at:02}")).collect();
        let mut picker = Picker::new(items);
        let lines = picker.render(&theme, 40, 6);
        assert_eq!(lines.len(), 6);
        assert_eq!(rendered(&lines)[0], "❯ item-00");
        assert_eq!(rendered(&lines)[5], "  (1/30)");
        for _ in 0..10 {
            picker.apply_key(PickKey::Down);
        }
        let lines = picker.render(&theme, 40, 6);
        assert_eq!(
            rendered(&lines),
            vec![
                "  item-08",
                "  item-09",
                "❯ item-10",
                "  item-11",
                "  item-12",
                "  (11/30)",
            ]
        );
        picker.apply_key(PickKey::Up);
        picker.apply_key(PickKey::Up);
        let lines = picker.render(&theme, 40, 40);
        assert_eq!(lines.len(), 30);
        assert_eq!(rendered(&lines)[8], "❯ item-08");
    }

    #[test]
    fn path_candidates_walk_shallowest_first_and_skip_vendored_trees() {
        let root = tree("walk");
        fs::create_dir_all(root.join("src/ui")).unwrap();
        fs::create_dir_all(root.join(".git")).unwrap();
        fs::create_dir_all(root.join("node_modules/left")).unwrap();
        fs::create_dir_all(root.join("target/debug")).unwrap();
        fs::create_dir_all(root.join("__pycache__")).unwrap();
        fs::write(root.join("README.md"), "").unwrap();
        fs::write(root.join("src/main.rs"), "").unwrap();
        fs::write(root.join("src/ui/picker.rs"), "").unwrap();
        fs::write(root.join(".git/config"), "").unwrap();
        fs::write(root.join("node_modules/left/index.js"), "").unwrap();
        fs::write(root.join("target/debug/ufo"), "").unwrap();

        let all = path_candidates(&root, "", 50);
        assert_eq!(
            all,
            vec![
                "README.md",
                "src/",
                "src/main.rs",
                "src/ui/",
                "src/ui/picker.rs"
            ]
        );
        assert_eq!(path_candidates(&root, "", 2), vec!["README.md", "src/"]);
        assert_eq!(path_candidates(&root, "picker", 5)[0], "src/ui/picker.rs");
        assert_eq!(path_candidates(&root, "sui", 5)[0], "src/ui/");
        assert!(path_candidates(&root, "index", 5).is_empty());

        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn path_candidates_stop_at_the_depth_bound() {
        let root = tree("depth");
        fs::create_dir_all(root.join("a/b/c/d/e/f")).unwrap();
        fs::write(root.join("a/b/c/d/e/f/deep.txt"), "").unwrap();

        let all = path_candidates(&root, "", 50);
        assert_eq!(all, vec!["a/", "a/b/", "a/b/c/", "a/b/c/d/", "a/b/c/d/e/"]);

        fs::remove_dir_all(&root).unwrap();
    }
}
