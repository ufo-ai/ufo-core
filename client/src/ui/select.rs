use std::time::{Duration, Instant};

use unicode_width::UnicodeWidthChar;

pub const MULTI_CLICK_WINDOW: Duration = Duration::from_millis(500);
pub const EDGE_SCROLL_LINES: isize = 3;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Grain {
    Char,
    Word,
    Line,
}

pub struct Selection {
    pub anchor: (usize, usize),
    pub head: (usize, usize),
    pub grain: Grain,
    pub dragged: bool,
}

impl Selection {
    pub fn begin(line: usize, col: usize, grain: Grain) -> Selection {
        Selection {
            anchor: (line, col),
            head: (line, col),
            grain,
            dragged: false,
        }
    }

    pub fn drag_to(&mut self, line: usize, col: usize) {
        self.head = (line, col);
        self.dragged = true;
    }

    pub fn cols_for(&self, line: usize, text: &str) -> Option<(usize, usize)> {
        let (start, end) = self.ordered();
        if line < start.0 || line > end.0 {
            return None;
        }
        let span = line_width(text).max(1);
        if self.grain == Grain::Line {
            return Some((0, span));
        }
        let mut from = if line == start.0 { start.1 } else { 0 };
        let mut to = if line == end.0 {
            end.1.saturating_add(1).min(span)
        } else {
            span
        };
        if self.grain == Grain::Word {
            if line == start.0 {
                if let Some((word, _)) = word_bounds(text, from) {
                    from = word;
                }
            }
            if line == end.0 {
                if let Some((_, word)) = word_bounds(text, end.1) {
                    to = to.max(word);
                }
            }
        }
        (from < to).then_some((from, to))
    }

    pub fn extract(&self, text_of: &mut dyn FnMut(usize) -> String) -> String {
        let (start, end) = self.ordered();
        let mut rows: Vec<String> = Vec::new();
        for line in start.0..=end.0 {
            let text = text_of(line);
            let taken = match self.cols_for(line, &text) {
                Some((from, to)) => slice_cols(&text, from, to),
                None => "",
            };
            rows.push(taken.trim_end().to_string());
        }
        rows.join("\n")
    }

    fn ordered(&self) -> ((usize, usize), (usize, usize)) {
        if self.anchor <= self.head {
            (self.anchor, self.head)
        } else {
            (self.head, self.anchor)
        }
    }
}

fn word_bounds(text: &str, col: usize) -> Option<(usize, usize)> {
    let cells: Vec<(usize, usize, char)> = cells(text);
    let at = cells
        .iter()
        .position(|&(start, width, _)| col >= start && col < start + width)?;
    if cells[at].2.is_whitespace() {
        return None;
    }
    let mut first = at;
    while first > 0 && !cells[first - 1].2.is_whitespace() {
        first -= 1;
    }
    let mut last = at;
    while last + 1 < cells.len() && !cells[last + 1].2.is_whitespace() {
        last += 1;
    }
    Some((cells[first].0, cells[last].0 + cells[last].1))
}

fn slice_cols(text: &str, from: usize, to: usize) -> &str {
    let mut col = 0;
    let mut span: Option<(usize, usize)> = None;
    let mut carried = false;
    for (index, ch) in text.char_indices() {
        let width = UnicodeWidthChar::width(ch).unwrap_or(0);
        let inside = if width == 0 {
            carried
        } else {
            col < to && col + width > from
        };
        if inside {
            let end = index + ch.len_utf8();
            span = Some(match span {
                Some((start, _)) => (start, end),
                None => (index, end),
            });
        }
        carried = inside;
        col += width;
    }
    match span {
        Some((start, end)) => &text[start..end],
        None => "",
    }
}

fn cells(text: &str) -> Vec<(usize, usize, char)> {
    let mut col = 0;
    text.chars()
        .map(|ch| {
            let width = UnicodeWidthChar::width(ch).unwrap_or(0);
            let cell = (col, width, ch);
            col += width;
            cell
        })
        .collect()
}

fn line_width(text: &str) -> usize {
    text.chars()
        .map(|ch| UnicodeWidthChar::width(ch).unwrap_or(0))
        .sum()
}

pub struct ClickTracker {
    last: Option<(Instant, (usize, usize), u8)>,
}

impl ClickTracker {
    pub fn new() -> ClickTracker {
        ClickTracker { last: None }
    }

    pub fn press(&mut self, at: (usize, usize)) -> Grain {
        self.press_at(at, Instant::now())
    }

    pub fn press_at(&mut self, at: (usize, usize), now: Instant) -> Grain {
        let count = match self.last {
            Some((when, spot, count))
                if spot == at && now.duration_since(when) < MULTI_CLICK_WINDOW =>
            {
                count % 3 + 1
            }
            _ => 1,
        };
        self.last = Some((now, at, count));
        match count {
            1 => Grain::Char,
            2 => Grain::Word,
            _ => Grain::Line,
        }
    }
}

impl Default for ClickTracker {
    fn default() -> ClickTracker {
        ClickTracker::new()
    }
}

pub fn edge_scroll(mouse_row: u16, view_rows: usize) -> isize {
    if mouse_row == 0 {
        return -EDGE_SCROLL_LINES;
    }
    if usize::from(mouse_row) + 1 >= view_rows {
        return EDGE_SCROLL_LINES;
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn drag(from: (usize, usize), to: (usize, usize), grain: Grain) -> Selection {
        let mut selection = Selection::begin(from.0, from.1, grain);
        selection.drag_to(to.0, to.1);
        selection
    }

    #[test]
    fn a_press_starts_a_character_selection() {
        let selection = Selection::begin(3, 7, Grain::Char);
        assert_eq!(selection.anchor, (3, 7));
        assert!(!selection.dragged);
    }

    #[test]
    fn a_char_drag_spans_the_pressed_columns() {
        let selection = drag((0, 2), (0, 5), Grain::Char);
        assert_eq!(selection.cols_for(0, "abcdefgh"), Some((2, 6)));
        assert_eq!(selection.cols_for(1, "abcdefgh"), None);
    }

    #[test]
    fn a_reversed_drag_orders_its_ends() {
        let selection = drag((2, 5), (2, 1), Grain::Char);
        assert_eq!(selection.cols_for(2, "abcdefgh"), Some((1, 6)));
    }

    #[test]
    fn a_char_span_stops_at_the_end_of_the_line() {
        let selection = drag((0, 0), (0, 40), Grain::Char);
        assert_eq!(selection.cols_for(0, "ab"), Some((0, 2)));
    }

    #[test]
    fn word_grain_widens_to_whole_words() {
        let text = "one two three";
        let selection = drag((0, 5), (0, 9), Grain::Word);
        assert_eq!(selection.cols_for(0, text), Some((4, 13)));
        assert_eq!(slice_cols(text, 4, 13), "two three");
    }

    #[test]
    fn word_grain_counts_wide_chars() {
        let text = "日本語 word";
        let selection = Selection::begin(0, 3, Grain::Word);
        assert_eq!(selection.cols_for(0, text), Some((0, 6)));
        assert_eq!(slice_cols(text, 0, 6), "日本語");

        let selection = Selection::begin(0, 8, Grain::Word);
        assert_eq!(selection.cols_for(0, text), Some((7, 11)));
    }

    #[test]
    fn word_grain_leaves_whitespace_exact() {
        let selection = Selection::begin(0, 3, Grain::Word);
        assert_eq!(selection.cols_for(0, "one two"), Some((3, 4)));
    }

    #[test]
    fn line_grain_takes_the_whole_line() {
        let selection = Selection::begin(1, 4, Grain::Line);
        assert_eq!(selection.cols_for(1, "one two"), Some((0, 7)));
        assert_eq!(selection.cols_for(1, ""), Some((0, 1)));
    }

    #[test]
    fn interior_lines_span_their_full_width() {
        let selection = drag((0, 3), (3, 1), Grain::Char);
        assert_eq!(selection.cols_for(0, "abcdef"), Some((3, 6)));
        assert_eq!(selection.cols_for(1, "日本語"), Some((0, 6)));
        assert_eq!(selection.cols_for(2, ""), Some((0, 1)));
        assert_eq!(selection.cols_for(3, "abcdef"), Some((0, 2)));
    }

    #[test]
    fn extract_clips_the_boundary_lines() {
        let lines = ["one two", "", "three four"];
        let selection = drag((0, 4), (2, 4), Grain::Char);
        let text = selection.extract(&mut |line| lines[line].to_string());
        assert_eq!(text, "two\n\nthree");
    }

    #[test]
    fn extract_trims_each_line() {
        let lines = ["one   ", "two"];
        let selection = drag((0, 0), (1, 2), Grain::Char);
        let text = selection.extract(&mut |line| lines[line].to_string());
        assert_eq!(text, "one\ntwo");
    }

    #[test]
    fn extract_keeps_wide_chars_whole() {
        let selection = drag((0, 1), (0, 4), Grain::Char);
        let text = selection.extract(&mut |_| "日本語".to_string());
        assert_eq!(text, "日本語");
    }

    #[test]
    fn presses_at_one_spot_cycle_the_grain() {
        let mut tracker = ClickTracker::new();
        let now = Instant::now();
        assert_eq!(tracker.press_at((2, 4), now), Grain::Char);
        assert_eq!(
            tracker.press_at((2, 4), now + Duration::from_millis(100)),
            Grain::Word
        );
        assert_eq!(
            tracker.press_at((2, 4), now + Duration::from_millis(200)),
            Grain::Line
        );
        assert_eq!(
            tracker.press_at((2, 4), now + Duration::from_millis(300)),
            Grain::Char
        );
    }

    #[test]
    fn a_press_elsewhere_restarts_the_count() {
        let mut tracker = ClickTracker::new();
        let now = Instant::now();
        assert_eq!(tracker.press_at((2, 4), now), Grain::Char);
        assert_eq!(
            tracker.press_at((2, 5), now + Duration::from_millis(50)),
            Grain::Char
        );
        assert_eq!(
            tracker.press_at((2, 5), now + Duration::from_millis(100)),
            Grain::Word
        );
    }

    #[test]
    fn a_late_press_restarts_the_count() {
        let mut tracker = ClickTracker::new();
        let now = Instant::now();
        assert_eq!(tracker.press_at((2, 4), now), Grain::Char);
        assert_eq!(
            tracker.press_at((2, 4), now + MULTI_CLICK_WINDOW),
            Grain::Char
        );
    }

    #[test]
    fn the_edges_scroll_and_the_middle_does_not() {
        assert_eq!(edge_scroll(0, 10), -EDGE_SCROLL_LINES);
        assert_eq!(edge_scroll(9, 10), EDGE_SCROLL_LINES);
        assert_eq!(edge_scroll(12, 10), EDGE_SCROLL_LINES);
        assert_eq!(edge_scroll(5, 10), 0);
    }
}
