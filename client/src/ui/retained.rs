//! The retained transcript: entries hold their sources, rendered lines are a per-width cache, so
//! a resize re-wraps every screen the member can reach and the exit document prints at the final
//! width. Scroll state, the member-message jump index, and the link under a click all live here,
//! because they are all questions about the same rendered rows.

use ratatui::text::{Line, Span};

use crate::ui::theme::Theme;
use crate::ui::{markdown, wrap, PROMPT_IDLE};

pub const ENTRY_MAX: usize = 2000;
const ECHO_INDENT: &str = "  ";
const KEEP_DRAWN: usize = 16;
const SCHEMES: [&str; 2] = ["https://", "http://"];
const FOLD_ROLLED: &str = "▸";
const FOLD_OPENED: &str = "▾";
const STEP_INDENT: &str = "  ";
const RUN_INDENT: &str = "  ";

/// One retained transcript element, held as its source.
pub enum Entry {
    /// A member message: caret + bold, in member coordinates for the jump index.
    Member(String),
    /// A committed markdown block of the agent's reply.
    Markdown(String),
    /// A muted single line.
    Note(String),
    /// One turn's steps, ranked as they happened.
    Steps { steps: Vec<Step>, fold: Fold },
    /// Pre-rendered lines that re-wrap by clipping only (image markers, raw spans).
    Raw(Vec<Line<'static>>),
}

/// One step of a turn: a thought the agent wrote between its calls, the one line a call or a
/// skill load narrated, or a subagent run.
pub enum Step {
    Thought(String),
    Note(String),
    /// One subagent run: its name, the rows it narrated, and whether the member opened it. A run
    /// is one step however much it did — the count the web states — and its rows stand behind
    /// its own fold, like the web's run row.
    Run {
        label: String,
        rows: Vec<String>,
        opened: bool,
    },
}

/// How a turn's steps stand: written into while the turn runs, rolled up behind
/// `Completed N steps` once the answer lands, or opened again by the member.
#[derive(Clone, Copy, PartialEq)]
pub enum Fold {
    Live,
    Rolled,
    Opened,
}

/// The line a rolled-up turn states. The web's fold states the same words.
pub fn rollup_line(steps: usize) -> String {
    match steps {
        1 => "Completed 1 step".to_string(),
        count => format!("Completed {count} steps"),
    }
}

/// What a click on one rendered row of a steps entry toggles.
#[derive(Clone, Copy)]
enum Toggle {
    Fold,
    Run(usize),
}

/// What one paint of the transcript region shows.
pub struct Window {
    /// Exactly the rows asked for, blank-padded at the bottom when content is short.
    pub lines: Vec<Line<'static>>,
    /// Absolute line index of `lines[0]`, for translating screen rows to content lines.
    pub start: usize,
}

/// One entry's rendered extent, kept for every entry so the line arithmetic never re-renders.
#[derive(Clone, Copy)]
struct Shape {
    lines: usize,
    starts_blank: bool,
    ends_blank: bool,
}

impl Shape {
    fn of(lines: &[Line<'static>]) -> Shape {
        Shape {
            lines: lines.len(),
            starts_blank: lines.first().is_some_and(is_blank),
            ends_blank: lines.last().is_some_and(is_blank),
        }
    }
}

/// Where one entry lands in the collapsed transcript: its first absolute line, and the offset of
/// the first of its own lines that survives — 1 where its leading blank met the entry above it.
#[derive(Clone, Copy)]
struct Place {
    start: usize,
    from: usize,
}

/// The transcript, its scroll state, and the caches that make a resize a re-render.
pub struct Retained {
    entries: Vec<Entry>,
    drawn: Vec<Option<Vec<Line<'static>>>>,
    shapes: Vec<Option<Shape>>,
    places: Vec<Place>,
    width: u16,
    total: usize,
    rows: usize,
    scroll_back: usize,
    grew: bool,
    dirty: bool,
    live_steps: Option<usize>,
}

impl Retained {
    pub fn new(width: u16) -> Retained {
        Retained {
            entries: Vec::new(),
            drawn: Vec::new(),
            shapes: Vec::new(),
            places: Vec::new(),
            width,
            total: 0,
            rows: 1,
            scroll_back: 0,
            grew: false,
            dirty: false,
            live_steps: None,
        }
    }

    pub fn push(&mut self, entry: Entry) {
        self.entries.push(entry);
        self.drawn.push(None);
        self.shapes.push(None);
        if self.entries.len() > ENTRY_MAX {
            self.entries.remove(0);
            self.drawn.remove(0);
            self.shapes.remove(0);
            self.live_steps = match self.live_steps {
                Some(at) if at > 0 => Some(at - 1),
                _ => None,
            };
        }
        self.grew = true;
        self.dirty = true;
    }

    /// One step of the running turn joins its rollup, opening one where the turn has none yet. A
    /// live rollup stands open, so the steps are read where they happened.
    pub fn push_step(&mut self, step: Step) {
        let Some(at) = self.live_steps else {
            self.push(Entry::Steps {
                steps: vec![step],
                fold: Fold::Live,
            });
            self.live_steps = Some(self.entries.len() - 1);
            return;
        };
        if let Entry::Steps { steps, .. } = &mut self.entries[at] {
            steps.push(step);
        }
        self.invalidate(at);
        self.grew = true;
    }

    /// A row a counted run narrated: it joins that run's step, or opens a fresh run where the
    /// rollup that counted it already settled.
    pub fn push_under(&mut self, label: &str, row: String) {
        if let Some(at) = self.live_steps {
            if let Entry::Steps { steps, .. } = &mut self.entries[at] {
                let run = steps.iter_mut().rev().find_map(|step| match step {
                    Step::Run {
                        label: held, rows, ..
                    } if held == label => Some(rows),
                    _ => None,
                });
                if let Some(rows) = run {
                    rows.push(row);
                    self.invalidate(at);
                    self.grew = true;
                    return;
                }
            }
        }
        self.push_step(Step::Run {
            label: label.to_string(),
            rows: vec![row],
            opened: false,
        });
    }

    /// The open reply's source, taken off the transcript with the entry it grew in — so words the
    /// turn wrote before a call can be held as the step they are.
    pub fn take_reply(&mut self) -> Option<String> {
        if !matches!(self.entries.last(), Some(Entry::Markdown(_))) {
            return None;
        }
        let Some(Entry::Markdown(source)) = self.entries.pop() else {
            return None;
        };
        self.drawn.pop();
        self.shapes.pop();
        self.dirty = true;
        Some(source)
    }

    /// The turn ended: its steps roll up behind the one line that counts them, and the next turn
    /// opens a rollup of its own.
    pub fn roll_up_steps(&mut self) {
        let Some(at) = self.live_steps.take() else {
            return;
        };
        if let Entry::Steps { fold, .. } = &mut self.entries[at] {
            *fold = Fold::Rolled;
        }
        self.invalidate(at);
    }

    /// Open or close the newest rolled-up turn — the member's own toggle, which is the only thing
    /// that opens a settled rollup. The turn still writing into its steps keeps them open.
    pub fn toggle_steps(&mut self) {
        let found = self
            .entries
            .iter()
            .rposition(|entry| matches!(entry, Entry::Steps { fold, .. } if *fold != Fold::Live));
        let Some(at) = found else {
            return;
        };
        if let Entry::Steps { fold, .. } = &mut self.entries[at] {
            *fold = match fold {
                Fold::Opened => Fold::Rolled,
                _ => Fold::Opened,
            };
        }
        self.invalidate(at);
    }

    /// Flip the fold a click landed on: the rollup line opens or closes its turn, a run's row
    /// opens or closes the run. True where the click was such a toggle.
    pub fn toggle(&mut self, line: usize, col: usize, theme: &Theme) -> bool {
        let Some((index, toggle)) = self.toggle_target(line, col, theme) else {
            return false;
        };
        if let Entry::Steps { steps, fold } = &mut self.entries[index] {
            match toggle {
                Toggle::Fold => {
                    *fold = match fold {
                        Fold::Opened => Fold::Rolled,
                        _ => Fold::Opened,
                    };
                }
                Toggle::Run(at) => {
                    if let Some(Step::Run { opened, .. }) = steps.get_mut(at) {
                        *opened = !*opened;
                    }
                }
            }
        }
        self.invalidate(index);
        true
    }

    /// Whether a toggle stands under this position — the hover query behind the affordance.
    pub fn is_toggle(&mut self, line: usize, col: usize, theme: &Theme) -> bool {
        self.toggle_target(line, col, theme).is_some()
    }

    /// The toggle one absolute rendered line carries at a display column: the rollup line and a
    /// run's row toggle, and only within their visible text.
    fn toggle_target(&mut self, line: usize, col: usize, theme: &Theme) -> Option<(usize, Toggle)> {
        self.layout(theme);
        if line >= self.total {
            return None;
        }
        let index = self.entry_at(line);
        let Entry::Steps { steps, fold } = &self.entries[index] else {
            return None;
        };
        let place = self.places[index];
        let at = place.from + (line - place.start);
        let rows = collapse(steps_rows(steps, *fold, theme, self.width), |(line, _)| {
            line
        });
        let (row, toggle) = rows.get(at)?;
        let toggle = (*toggle)?;
        (col < wrap::width(&plain_text(row))).then_some((index, toggle))
    }

    fn invalidate(&mut self, at: usize) {
        self.drawn[at] = None;
        self.shapes[at] = None;
        self.dirty = true;
    }

    /// Grow the reply the last entry holds, or start one. A streamed reply is one markdown
    /// document: its blocks arrive apart but re-wrap together, and the blank between two of them
    /// is the source's own rather than something a commit inserts.
    pub fn extend_markdown(&mut self, source: &str) {
        let Some(Entry::Markdown(held)) = self.entries.last_mut() else {
            self.push(Entry::Markdown(source.to_string()));
            return;
        };
        held.push_str(source);
        let at = self.entries.len() - 1;
        self.drawn[at] = None;
        self.shapes[at] = None;
        self.grew = true;
        self.dirty = true;
    }

    /// A width change: every cached rendering is invalid.
    pub fn set_width(&mut self, width: u16) {
        if width == self.width {
            return;
        }
        self.width = width;
        self.drawn.iter_mut().for_each(|drawn| *drawn = None);
        self.shapes.iter_mut().for_each(|shape| *shape = None);
        self.grew = false;
        self.dirty = true;
    }

    /// Scroll by `up` lines (positive is into history); clamped, and reaching the end resumes
    /// following.
    pub fn scroll(&mut self, up: isize) {
        let max = self.total.saturating_sub(1) as isize;
        self.scroll_back = (self.scroll_back as isize + up).clamp(0, max) as usize;
    }

    pub fn scroll_to_end(&mut self) {
        self.scroll_back = 0;
    }

    pub fn scrolled(&self) -> usize {
        self.scroll_back
    }

    /// Jump the window to the previous / next member message relative to the top of the window.
    pub fn jump_member(&mut self, theme: &Theme, back: bool) {
        self.layout(theme);
        let top = self.total.saturating_sub(self.scroll_back + self.rows);
        let mut target = None;
        for (index, entry) in self.entries.iter().enumerate() {
            if !matches!(entry, Entry::Member(_)) {
                continue;
            }
            let place = self.places[index];
            let said = place.start + usize::from(place.from == 0);
            if back && said < top {
                target = Some(said);
            }
            if !back && said > top && target.is_none() {
                target = Some(said);
            }
        }
        let Some(said) = target else {
            return;
        };
        self.scroll_back = self.total.saturating_sub(self.rows + said);
    }

    /// The visible rows at the current scroll, plus the live tail lines appended when following.
    pub fn window(&mut self, rows: usize, live: &[Line<'static>], theme: &Theme) -> Window {
        self.layout(theme);
        let rows = rows.max(1);
        self.rows = rows;
        let following = self.scroll_back == 0;
        let end = self.total.saturating_sub(self.scroll_back);
        let held = end + if following { live.len() } else { 0 };
        let start = held.saturating_sub(rows);
        let mut lines = self.between(start.min(end), end, theme);
        if following {
            lines.extend(live.iter().cloned());
        }
        let over = lines.len().saturating_sub(rows);
        lines.drain(..over);
        while lines.len() < rows {
            lines.push(Line::raw(""));
        }
        let first = self.entry_at(start.min(end));
        let last = self.entry_at(end.saturating_sub(1));
        self.forget_outside(first, last);
        Window { lines, start }
    }

    /// The plain text of one absolute rendered line, for selection extraction and word bounds.
    pub fn text_of(&mut self, line: usize, theme: &Theme) -> String {
        self.layout(theme);
        if line >= self.total {
            return String::new();
        }
        let index = self.entry_at(line);
        let place = self.places[index];
        let at = place.from + (line - place.start);
        self.draw(index, theme)
            .get(at)
            .map(plain_text)
            .unwrap_or_default()
    }

    /// The URL under a display column of one absolute line: an OSC 8 link's label, or a URL
    /// written in the line's visible text.
    pub fn link_at(&mut self, line: usize, col: usize, theme: &Theme) -> Option<String> {
        self.layout(theme);
        if line >= self.total {
            return None;
        }
        let index = self.entry_at(line);
        let place = self.places[index];
        let at = place.from + (line - place.start);
        let row = self.draw(index, theme).get(at)?;
        let mut found = osc_links(row);
        found.extend(urls(&plain_text(row)));
        found
            .into_iter()
            .find(|(from, to, _)| col >= *from && col < *to)
            .map(|(_, _, url)| url)
    }

    /// Every entry rendered at the current width, for the exit document.
    pub fn document(&mut self, theme: &Theme) -> Vec<Line<'static>> {
        self.layout(theme);
        self.between(0, self.total, theme)
    }

    /// Where every entry lands, from the cached shapes: one blank of two adjacent ones is dropped,
    /// and lines a push added below a reader carry that reader's window with them.
    fn layout(&mut self, theme: &Theme) {
        if !self.dirty {
            return;
        }
        let mut places = Vec::with_capacity(self.entries.len());
        let mut at = 0;
        let mut tail_blank = true;
        for index in 0..self.entries.len() {
            let shape = self.shape(index, theme);
            let from = usize::from(shape.starts_blank && tail_blank);
            let kept = shape.lines - from;
            places.push(Place { start: at, from });
            at += kept;
            if kept > 0 {
                tail_blank = shape.ends_blank;
            }
        }
        let added = match self.grew && self.scroll_back > 0 {
            true => at.saturating_sub(self.total),
            false => 0,
        };
        self.places = places;
        self.total = at;
        self.grew = false;
        self.dirty = false;
        self.scroll_back = (self.scroll_back + added).min(self.total.saturating_sub(1));
    }

    fn shape(&mut self, at: usize, theme: &Theme) -> Shape {
        if let Some(shape) = self.shapes[at] {
            return shape;
        }
        let lines = collapse(render(&self.entries[at], theme, self.width), |line| line);
        let shape = Shape::of(&lines);
        self.shapes[at] = Some(shape);
        shape
    }

    fn draw(&mut self, at: usize, theme: &Theme) -> &[Line<'static>] {
        if self.drawn[at].is_none() {
            let lines = collapse(render(&self.entries[at], theme, self.width), |line| line);
            self.shapes[at] = Some(Shape::of(&lines));
            self.drawn[at] = Some(lines);
        }
        self.drawn[at].as_deref().expect("the entry is drawn")
    }

    /// The entry holding an absolute line: the last one that starts at or before it, so an entry
    /// the collapse emptied is stepped over rather than answered with.
    fn entry_at(&self, line: usize) -> usize {
        self.places
            .partition_point(|place| place.start <= line)
            .saturating_sub(1)
    }

    fn between(&mut self, from: usize, to: usize, theme: &Theme) -> Vec<Line<'static>> {
        let mut out = Vec::new();
        if from >= to {
            return out;
        }
        let mut index = self.entry_at(from);
        while index < self.entries.len() {
            let place = self.places[index];
            if place.start >= to {
                break;
            }
            for (offset, line) in self.draw(index, theme)[place.from..].iter().enumerate() {
                let at = place.start + offset;
                if at >= to {
                    break;
                }
                if at >= from {
                    out.push(line.clone());
                }
            }
            index += 1;
        }
        out
    }

    /// Rendered lines are held only around the window; every entry keeps its shape, which is what
    /// the arithmetic reads.
    fn forget_outside(&mut self, first: usize, last: usize) {
        let low = first.saturating_sub(KEEP_DRAWN);
        let high = last + KEEP_DRAWN;
        for (index, drawn) in self.drawn.iter_mut().enumerate() {
            if index < low || index > high {
                *drawn = None;
            }
        }
    }
}

fn render(entry: &Entry, theme: &Theme, width: u16) -> Vec<Line<'static>> {
    match entry {
        Entry::Member(text) => member_lines(text, theme),
        Entry::Markdown(source) => markdown::render(source, theme, width),
        Entry::Note(text) => vec![Line::styled(text.clone(), theme.muted)],
        Entry::Steps { steps, fold } => steps_lines(steps, *fold, theme, width),
        Entry::Raw(lines) => lines.clone(),
    }
}

/// A turn's steps: the rows alone while the turn writes them, the count alone once it rolled up,
/// and the count over the rows the member opened again.
fn steps_lines(steps: &[Step], fold: Fold, theme: &Theme, width: u16) -> Vec<Line<'static>> {
    steps_rows(steps, fold, theme, width)
        .into_iter()
        .map(|(line, _)| line)
        .collect()
}

/// The same rows with the toggle each carries, so a click and the rendering read one traversal.
/// A live turn's rows read as the narration they were; the rows a member opened again are past
/// work, so they stand indented under the count and state each call's work without the
/// `running <tool>:` lead — the words the web's settled disclosure shows.
fn steps_rows(
    steps: &[Step],
    fold: Fold,
    theme: &Theme,
    width: u16,
) -> Vec<(Line<'static>, Option<Toggle>)> {
    let summary =
        |mark: &str| Line::styled(format!("{} {mark}", rollup_line(steps.len())), theme.muted);
    let mut rows = match fold {
        Fold::Live => Vec::new(),
        Fold::Rolled => return vec![(summary(FOLD_ROLLED), Some(Toggle::Fold))],
        Fold::Opened => vec![(summary(FOLD_OPENED), Some(Toggle::Fold))],
    };
    let disclosed = fold == Fold::Opened;
    let indent = if disclosed { STEP_INDENT } else { "" };
    let said = |text: &str| match disclosed {
        true => described(text).to_string(),
        false => text.to_string(),
    };
    for (index, step) in steps.iter().enumerate() {
        match step {
            Step::Thought(source) => rows.extend(
                markdown::render(source, theme, width.saturating_sub(indent.len() as u16))
                    .into_iter()
                    .map(|mut line| {
                        if disclosed {
                            line.spans.insert(0, Span::raw(STEP_INDENT));
                        }
                        (line, None)
                    }),
            ),
            Step::Note(text) => rows.push((
                Line::styled(format!("{indent}{}", said(text)), theme.muted),
                None,
            )),
            Step::Run {
                label,
                rows: narrated,
                opened,
            } => {
                let row = run_row(label, narrated, *opened, fold);
                rows.push((
                    Line::styled(format!("{indent}{row}"), theme.muted),
                    Some(Toggle::Run(index)),
                ));
                if *opened {
                    for held in narrated {
                        rows.push((
                            Line::styled(
                                format!("{indent}{RUN_INDENT}{}", said(held)),
                                theme.muted,
                            ),
                            None,
                        ));
                    }
                }
            }
        }
    }
    rows
}

/// The work a narrated call states, without the `running <tool>: ` the live line leads with.
fn described(text: &str) -> &str {
    text.strip_prefix("running ")
        .and_then(|rest| rest.split_once(": "))
        .map_or(text, |(_, description)| description)
}

/// A run's own row: its name before a fold mark and — closed while the turn still runs — the
/// latest thing it narrated, the way the web's run row states what it is doing.
fn run_row(label: &str, rows: &[String], opened: bool, fold: Fold) -> String {
    match (opened, fold, rows.last()) {
        (false, Fold::Live, Some(latest)) => format!("{label} · {latest} {FOLD_ROLLED}"),
        (false, ..) => format!("{label} {FOLD_ROLLED}"),
        (true, ..) => format!("{label} {FOLD_OPENED}"),
    }
}

fn member_lines(text: &str, theme: &Theme) -> Vec<Line<'static>> {
    let mut rows = text.lines();
    let first = rows.next().unwrap_or("").to_string();
    let mut lines = vec![
        Line::raw(""),
        Line::from(vec![
            Span::styled(format!("{PROMPT_IDLE} "), theme.prompt),
            Span::styled(first, theme.member),
        ]),
    ];
    for row in rows {
        lines.push(Line::from(Span::styled(
            format!("{ECHO_INDENT}{row}"),
            theme.member,
        )));
    }
    lines.push(Line::raw(""));
    lines
}

fn collapse<T>(rows: Vec<T>, line: impl Fn(&T) -> &Line<'static>) -> Vec<T> {
    let mut out: Vec<T> = Vec::with_capacity(rows.len());
    for row in rows {
        if is_blank(line(&row)) && out.last().is_some_and(|held| is_blank(line(held))) {
            continue;
        }
        out.push(row);
    }
    out
}

fn is_blank(line: &Line) -> bool {
    line.spans.iter().all(|span| span.content.trim().is_empty())
}

/// The visible text of a rendered line: escapes carried in span text take no columns and drop.
fn plain_text(line: &Line) -> String {
    line.spans
        .iter()
        .flat_map(|span| wrap::units(&span.content))
        .filter(|(_, step)| *step > 0)
        .map(|(unit, _)| unit)
        .collect()
}

/// Every OSC 8 link in one line as `(first column, column past the end, url)`, the columns those
/// of its visible label.
fn osc_links(line: &Line) -> Vec<(usize, usize, String)> {
    let mut found = Vec::new();
    let mut open: Option<(usize, String)> = None;
    let mut col = 0;
    for span in &line.spans {
        for (unit, step) in wrap::units(&span.content) {
            if let Some(rest) = unit.strip_prefix("\x1b]8;;") {
                let url = rest.trim_end_matches('\x07').trim_end_matches("\x1b\\");
                match (url.is_empty(), open.take()) {
                    (true, Some((from, url))) if col > from => found.push((from, col, url)),
                    (true, _) => {}
                    (false, _) => open = Some((col, url.to_string())),
                }
            }
            col += step;
        }
    }
    found
}

/// Every URL in one line's text as `(first column, column past the end, url)`. A token runs to the
/// next space; a closing paren ends it only where one was opened before it, which is how a
/// markdown link states its destination.
fn urls(text: &str) -> Vec<(usize, usize, String)> {
    let mut found = Vec::new();
    let mut at = 0;
    while at < text.len() {
        let Some(offset) = text[at..].find("http") else {
            break;
        };
        let start = at + offset;
        let Some(scheme) = SCHEMES
            .iter()
            .find(|scheme| text[start..].starts_with(**scheme))
        else {
            at = start + "http".len();
            continue;
        };
        let token = &text[start..];
        let mut end = start + token.find(char::is_whitespace).unwrap_or(token.len());
        let lead = &text[..start];
        if lead.matches('(').count() > lead.matches(')').count() {
            while end > start && text[start..end].ends_with(')') {
                end -= 1;
            }
        }
        if end > start + scheme.len() {
            found.push((
                wrap::width(lead),
                wrap::width(&text[..end]),
                text[start..end].to_string(),
            ));
        }
        at = end.max(start + 1);
    }
    found
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ui::osc;
    use crate::ui::theme::{ColorMode, Scheme, Theme};

    const PARAGRAPH: &str = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu";

    fn theme() -> Theme {
        Theme::for_mode(ColorMode::Plain, Scheme::Dark)
    }

    fn texts(lines: &[Line<'static>]) -> Vec<String> {
        lines.iter().map(plain_text).collect()
    }

    fn notes(count: usize) -> Retained {
        let mut retained = Retained::new(40);
        for index in 0..count {
            retained.push(Entry::Note(format!("line {index}")));
        }
        retained
    }

    #[test]
    fn a_resize_rewraps_every_entry() {
        let theme = theme();
        let mut retained = Retained::new(80);
        retained.push(Entry::Markdown(PARAGRAPH.into()));
        let wide = retained.document(&theme).len();
        retained.set_width(20);
        let narrow = retained.document(&theme).len();
        assert!(wide < narrow, "{wide} lines at 80, {narrow} at 20");
    }

    #[test]
    fn the_window_follows_the_end_and_pads() {
        let theme = theme();
        let mut retained = notes(2);
        let window = retained.window(6, &[], &theme);
        assert_eq!(window.lines.len(), 6);
        assert_eq!(texts(&window.lines)[..2], ["line 0", "line 1"]);
        assert!(texts(&window.lines)[2..].iter().all(String::is_empty));
        assert_eq!(window.start, 0);
        assert_eq!(retained.scrolled(), 0);
    }

    #[test]
    fn the_window_shows_the_last_rows_of_a_long_transcript() {
        let theme = theme();
        let mut retained = notes(30);
        let window = retained.window(10, &[], &theme);
        assert_eq!(window.start, 20);
        assert_eq!(plain_text(&window.lines[0]), "line 20");
        assert_eq!(plain_text(&window.lines[9]), "line 29");
    }

    #[test]
    fn a_scrolled_window_stays_anchored_across_pushes() {
        let theme = theme();
        let mut retained = notes(30);
        let _ = retained.window(10, &[], &theme);
        retained.scroll(5);
        let before = retained.window(10, &[], &theme);
        assert_eq!(before.start, 15);
        assert_eq!(retained.scrolled(), 5);
        retained.push(Entry::Note("fresh".into()));
        let after = retained.window(10, &[], &theme);
        assert_eq!(after.start, 15);
        assert_eq!(texts(&after.lines), texts(&before.lines));
        assert_eq!(retained.scrolled(), 6);
        retained.scroll_to_end();
        let end = retained.window(10, &[], &theme);
        assert_eq!(plain_text(&end.lines[9]), "fresh");
        assert_eq!(retained.scrolled(), 0);
    }

    #[test]
    fn the_live_tail_rides_the_end_only_while_following() {
        let theme = theme();
        let mut retained = notes(30);
        let live = vec![Line::raw("streaming")];
        let following = retained.window(10, &live, &theme);
        assert_eq!(plain_text(&following.lines[9]), "streaming");
        assert_eq!(following.start, 21);
        retained.scroll(3);
        let scrolled = retained.window(10, &live, &theme);
        assert_eq!(plain_text(&scrolled.lines[9]), "line 26");
    }

    #[test]
    fn jump_member_lands_a_member_line_at_the_window_top() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.push(Entry::Member("first".into()));
        for index in 0..20 {
            retained.push(Entry::Note(format!("note {index}")));
        }
        retained.push(Entry::Member("second".into()));
        for index in 0..20 {
            retained.push(Entry::Note(format!("more {index}")));
        }
        let _ = retained.window(8, &[], &theme);

        retained.jump_member(&theme, true);
        let window = retained.window(8, &[], &theme);
        assert_eq!(plain_text(&window.lines[0]), "› second");

        retained.jump_member(&theme, true);
        let window = retained.window(8, &[], &theme);
        assert_eq!(plain_text(&window.lines[0]), "› first");

        retained.jump_member(&theme, true);
        let window = retained.window(8, &[], &theme);
        assert_eq!(plain_text(&window.lines[0]), "› first");

        retained.jump_member(&theme, false);
        let window = retained.window(8, &[], &theme);
        assert_eq!(plain_text(&window.lines[0]), "› second");
    }

    #[test]
    fn a_member_message_keeps_its_caret_and_indent() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.push(Entry::Member("one\ntwo".into()));
        assert_eq!(texts(&retained.document(&theme)), ["› one", "  two", ""]);
    }

    #[test]
    fn a_streamed_reply_grows_one_entry() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.extend_markdown("one two\n\n");
        retained.extend_markdown("next\n");
        assert_eq!(texts(&retained.document(&theme)), ["one two", "", "next"]);
        retained.push(Entry::Note("done".into()));
        retained.extend_markdown("after");
        assert_eq!(
            texts(&retained.document(&theme)),
            ["one two", "", "next", "done", "after"]
        );
    }

    #[test]
    fn a_growing_reply_carries_a_scrolled_reader() {
        let theme = theme();
        let mut retained = notes(30);
        let _ = retained.window(10, &[], &theme);
        retained.scroll(5);
        let before = retained.window(10, &[], &theme);
        retained.extend_markdown("tail line\n\n");
        let opened = retained.window(10, &[], &theme);
        assert_eq!(opened.start, before.start);
        assert_eq!(retained.scrolled(), 6);
        retained.extend_markdown("more\n\n");
        let grown = retained.window(10, &[], &theme);
        assert_eq!(grown.start, before.start);
        assert_eq!(texts(&grown.lines), texts(&before.lines));
        assert_eq!(retained.scrolled(), 8);
    }

    #[test]
    fn blank_lines_collapse_between_entries() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.push(Entry::Member("hi".into()));
        retained.push(Entry::Member("again".into()));
        retained.push(Entry::Markdown("body".into()));
        assert_eq!(
            texts(&retained.document(&theme)),
            ["› hi", "", "› again", "", "body"]
        );
    }

    #[test]
    fn the_document_is_every_line_the_window_can_reach() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.push(Entry::Member("hi".into()));
        retained.push(Entry::Markdown(PARAGRAPH.into()));
        retained.push(Entry::Note("done".into()));
        let document = retained.document(&theme);
        let window = retained.window(document.len(), &[], &theme);
        assert_eq!(texts(&window.lines), texts(&document));
    }

    #[test]
    fn text_of_reads_back_one_line() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.push(Entry::Member("hi".into()));
        retained.push(Entry::Note("done".into()));
        assert_eq!(retained.text_of(0, &theme), "› hi");
        assert_eq!(retained.text_of(1, &theme), "");
        assert_eq!(retained.text_of(2, &theme), "done");
        assert_eq!(retained.text_of(3, &theme), "");
    }

    #[test]
    fn link_at_finds_the_url_under_the_column() {
        let theme = theme();
        let mut retained = Retained::new(60);
        retained.push(Entry::Note("see https://ufo.test/x now".into()));
        assert_eq!(
            retained.link_at(0, 6, &theme).as_deref(),
            Some("https://ufo.test/x")
        );
        assert_eq!(
            retained.link_at(0, 4, &theme).as_deref(),
            Some("https://ufo.test/x")
        );
        assert_eq!(retained.link_at(0, 2, &theme), None);
        assert_eq!(retained.link_at(0, 24, &theme), None);
        assert_eq!(retained.link_at(9, 0, &theme), None);
    }

    fn linked_line() -> Line<'static> {
        let open = osc::link_open("https://ufo.test/artifacts/abc?exp=1&sig=2");
        Line::raw(format!("shared {open}name{} (12 bytes)", osc::LINK_CLOSE))
    }

    #[test]
    fn text_of_reads_visible_text_through_a_hyperlink() {
        let theme = theme();
        let mut retained = Retained::new(60);
        retained.push(Entry::Raw(vec![linked_line()]));
        assert_eq!(retained.text_of(0, &theme), "shared name (12 bytes)");
    }

    #[test]
    fn link_at_finds_a_hyperlink_under_its_label() {
        let theme = theme();
        let mut retained = Retained::new(60);
        retained.push(Entry::Raw(vec![linked_line()]));
        let url = "https://ufo.test/artifacts/abc?exp=1&sig=2";
        assert_eq!(retained.link_at(0, 7, &theme).as_deref(), Some(url));
        assert_eq!(retained.link_at(0, 10, &theme).as_deref(), Some(url));
        assert_eq!(retained.link_at(0, 6, &theme), None);
        assert_eq!(retained.link_at(0, 11, &theme), None);
    }

    #[test]
    fn link_at_finds_a_markdown_link_by_its_label() {
        let theme = theme();
        let mut retained = Retained::new(60);
        retained.push(Entry::Markdown("see [docs](https://ufo.test)".into()));
        assert_eq!(retained.text_of(0, &theme), "see docs");
        assert_eq!(
            retained.link_at(0, 4, &theme).as_deref(),
            Some("https://ufo.test")
        );
        assert_eq!(retained.link_at(0, 3, &theme), None);
    }

    #[test]
    fn a_prose_urls_trailing_paren_stays_prose() {
        let theme = theme();
        let mut retained = Retained::new(60);
        retained.push(Entry::Note("docs (https://ufo.test) now".into()));
        assert_eq!(
            retained.link_at(0, 8, &theme).as_deref(),
            Some("https://ufo.test")
        );
        assert_eq!(retained.link_at(0, 22, &theme), None);
    }

    #[test]
    fn raw_lines_are_kept_verbatim() {
        let theme = theme();
        let mut retained = Retained::new(8);
        let held = vec![Line::raw("a line wider than the width")];
        retained.push(Entry::Raw(held));
        assert_eq!(
            texts(&retained.document(&theme)),
            ["a line wider than the width"]
        );
    }

    #[test]
    fn a_running_turns_steps_stand_open_in_the_order_they_happened() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.push_step(Step::Thought("reading the calendar next".into()));
        retained.push_step(Step::Note("running read: the calendar".into()));
        retained.push_step(Step::Note("loading skill: office/pptx".into()));
        assert_eq!(
            texts(&retained.document(&theme)),
            [
                "reading the calendar next",
                "running read: the calendar",
                "loading skill: office/pptx",
            ]
        );
    }

    #[test]
    fn the_turns_end_rolls_its_steps_up_and_the_member_opens_them_again() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.push_step(Step::Thought("first the calendar".into()));
        retained.push_step(Step::Note("running read: the calendar".into()));
        retained.push(Entry::Markdown("the answer".into()));
        retained.roll_up_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 2 steps ▸", "the answer"]
        );
        retained.toggle_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            [
                "Completed 2 steps ▾",
                "  first the calendar",
                "  the calendar",
                "the answer",
            ]
        );
        retained.toggle_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 2 steps ▸", "the answer"]
        );
    }

    #[test]
    fn one_step_is_said_in_the_singular_and_the_next_turn_rolls_up_on_its_own() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.push_step(Step::Note("running bash: ls".into()));
        retained.roll_up_steps();
        retained.push(Entry::Member("and again".into()));
        retained.push_step(Step::Note("running bash: ls".into()));
        retained.push_step(Step::Note("running read: notes".into()));
        retained.roll_up_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            [
                "Completed 1 step ▸",
                "",
                "› and again",
                "",
                "Completed 2 steps ▸",
            ]
        );
    }

    #[test]
    fn a_run_counts_as_one_step_however_many_calls_it_states() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.push_step(Step::Note("running spawn: reviewer".into()));
        retained.push_step(Step::Run {
            label: "reviewer".into(),
            rows: vec!["running read: the diff".into()],
            opened: false,
        });
        retained.push_under("reviewer", "running bash: cargo test".into());
        retained.roll_up_steps();
        assert_eq!(texts(&retained.document(&theme)), ["Completed 2 steps ▸"]);
        retained.toggle_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 2 steps ▾", "  reviewer", "  reviewer ▸"]
        );
        assert!(retained.toggle(2, 0, &theme));
        assert_eq!(
            texts(&retained.document(&theme)),
            [
                "Completed 2 steps ▾",
                "  reviewer",
                "  reviewer ▾",
                "    the diff",
                "    cargo test",
            ]
        );
        assert!(retained.toggle(2, 0, &theme));
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 2 steps ▾", "  reviewer", "  reviewer ▸"]
        );
    }

    #[test]
    fn a_live_runs_row_states_its_latest_call_and_opens_to_them_all() {
        let theme = theme();
        let mut retained = Retained::new(60);
        retained.push_step(Step::Run {
            label: "reviewer".into(),
            rows: vec!["running read: the diff".into()],
            opened: false,
        });
        retained.push_under("reviewer", "running bash: cargo test".into());
        assert_eq!(
            texts(&retained.document(&theme)),
            ["reviewer · running bash: cargo test ▸"]
        );
        assert!(retained.toggle(0, 2, &theme));
        assert_eq!(
            texts(&retained.document(&theme)),
            [
                "reviewer ▾",
                "  running read: the diff",
                "  running bash: cargo test",
            ]
        );
        assert!(retained.toggle(0, 2, &theme));
        assert_eq!(
            texts(&retained.document(&theme)),
            ["reviewer · running bash: cargo test ▸"]
        );
    }

    #[test]
    fn a_click_on_the_rollup_line_opens_and_closes_the_turn() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.push_step(Step::Note("running bash: ls".into()));
        retained.push(Entry::Markdown("the answer".into()));
        retained.roll_up_steps();
        assert!(retained.is_toggle(0, 3, &theme));
        assert!(!retained.is_toggle(0, 30, &theme));
        assert!(!retained.is_toggle(1, 0, &theme));
        assert!(retained.toggle(0, 0, &theme));
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 1 step ▾", "  ls", "the answer"]
        );
        assert!(retained.toggle(0, 5, &theme));
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 1 step ▸", "the answer"]
        );
        assert!(!retained.toggle(0, 30, &theme));
        assert!(!retained.toggle(1, 0, &theme));
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 1 step ▸", "the answer"]
        );
    }

    #[test]
    fn a_late_row_of_a_settled_run_opens_a_fresh_run() {
        let theme = theme();
        let mut retained = Retained::new(60);
        retained.push_step(Step::Run {
            label: "reviewer".into(),
            rows: vec!["running read: x".into()],
            opened: false,
        });
        retained.roll_up_steps();
        retained.push_under("reviewer", "running bash: late".into());
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 1 step ▸", "reviewer · running bash: late ▸"]
        );
    }

    #[test]
    fn the_toggle_leaves_the_turn_still_writing_its_steps_open() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.push_step(Step::Note("running read: notes".into()));
        retained.roll_up_steps();
        retained.push_step(Step::Note("running bash: ls".into()));
        retained.toggle_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 1 step ▾", "  notes", "running bash: ls"]
        );
    }

    #[test]
    fn the_open_reply_leaves_the_transcript_to_become_a_step() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.extend_markdown("a thought");
        let held = retained.take_reply();
        assert_eq!(held.as_deref(), Some("a thought"));
        assert_eq!(retained.take_reply(), None);
        retained.push_step(Step::Thought(held.expect("the reply was open")));
        retained.push_step(Step::Note("running bash: ls".into()));
        assert_eq!(
            texts(&retained.document(&theme)),
            ["a thought", "running bash: ls"]
        );
    }

    #[test]
    fn eviction_keeps_the_scroll_inside_the_transcript() {
        let theme = theme();
        let mut retained = notes(ENTRY_MAX + 5);
        assert_eq!(retained.document(&theme).len(), ENTRY_MAX);
        retained.scroll(ENTRY_MAX as isize * 2);
        assert_eq!(retained.scrolled(), ENTRY_MAX - 1);
        assert_eq!(
            plain_text(&retained.window(1, &[], &theme).lines[0]),
            "line 5"
        );
        retained.scroll_to_end();
        assert_eq!(retained.scrolled(), 0);
    }
}
