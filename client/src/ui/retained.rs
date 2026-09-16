use std::collections::{BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::time::Instant;

use ratatui::text::{Line, Span};

use crate::fold::{self, Frame, DONE};
use crate::ops::OP_EXEC;
use crate::record::{Step, SubagentRun, TurnEnd, TurnRecord};
use crate::ui::theme::Theme;
use crate::ui::toolrender::OpRow;
use crate::ui::{markdown, masthead, wrap, PROMPT_IDLE, RESUMED_NOTE, SENT_BY_UFO};
use crate::wire::OpRequest;

pub const ENTRY_MAX: usize = 2000;
const ECHO_INDENT: &str = "  ";
const KEEP_DRAWN: usize = 16;
const SCHEMES: [&str; 2] = ["https://", "http://"];
const FOLD_ROLLED: &str = "▸";
const FOLD_OPENED: &str = "▾";
const STEP_INDENT: &str = "  ";
const CALL_MARKER: &str = "⏺ ";
const RUN_INDENT: &str = "  ";

pub enum Entry {
    Member(String),
    Fired(String),
    Markdown(String),
    Note(String),
    Segment(Segment),
    Raw(Vec<Line<'static>>),
    Masthead,
}

impl Entry {
    fn keeps_its_blanks(&self) -> bool {
        matches!(self, Entry::Masthead)
    }

    fn keeps_its_leading_blank(&self) -> bool {
        matches!(self, Entry::Masthead)
    }
}

/// One stretch of a turn: `steps[from..to)` of its record and the ops that ran among them, drawn
/// from the record the turn folds. A turn is one segment until it takes up a member's message;
/// that message then stands between what came before it and what followed.
#[derive(Clone, Copy)]
pub struct Segment {
    turn: usize,
    ordinal: usize,
    from: usize,
    to: Option<usize>,
    fold: Fold,
}

struct TurnState {
    record: TurnRecord,
    ops: Vec<OpRow>,
    opened_runs: BTreeSet<String>,
    segments: usize,
}

impl TurnState {
    fn fresh() -> TurnState {
        TurnState {
            record: fold::empty(None),
            ops: Vec::new(),
            opened_runs: BTreeSet::new(),
            segments: 1,
        }
    }
}

#[derive(Clone, Copy, PartialEq)]
pub enum Fold {
    Live,
    Rolled,
    Opened,
}

fn flipped(fold: Fold) -> Fold {
    match fold {
        Fold::Opened => Fold::Rolled,
        _ => Fold::Opened,
    }
}

pub fn rollup_line(steps: usize) -> String {
    match steps {
        1 => "Completed 1 step".to_string(),
        count => format!("Completed {count} steps"),
    }
}

#[derive(Clone, PartialEq)]
enum Toggle {
    Fold,
    Run(String),
}

pub struct Window {
    pub lines: Vec<Line<'static>>,
    pub start: usize,
}

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

#[derive(Clone, Copy)]
struct Place {
    start: usize,
    from: usize,
}

pub struct Retained {
    entries: Vec<Entry>,
    drawn: Vec<Option<Vec<Line<'static>>>>,
    shapes: Vec<Option<Shape>>,
    places: Vec<Place>,
    turns: Vec<TurnState>,
    live: Option<usize>,
    phase: usize,
    cwd: PathBuf,
    width: u16,
    total: usize,
    rows: usize,
    scroll_back: usize,
    grew: bool,
    dirty: bool,
}

impl Retained {
    pub fn new(width: u16) -> Retained {
        Retained {
            entries: Vec::new(),
            drawn: Vec::new(),
            shapes: Vec::new(),
            places: Vec::new(),
            turns: Vec::new(),
            live: None,
            phase: 0,
            cwd: PathBuf::new(),
            width,
            total: 0,
            rows: 1,
            scroll_back: 0,
            grew: false,
            dirty: false,
        }
    }

    /// What arrives while the live turn's open segment still holds nothing stands above it, so
    /// the rows the turn draws follow it instead of carrying it down.
    pub fn push(&mut self, entry: Entry) {
        let at = match entry {
            Entry::Segment(_) => self.entries.len(),
            _ => self.empty_open_segment().unwrap_or(self.entries.len()),
        };
        self.entries.insert(at, entry);
        self.drawn.insert(at, None);
        self.shapes.insert(at, None);
        if self.entries.len() > ENTRY_MAX {
            let evicted = self.entries.remove(0);
            self.drawn.remove(0);
            self.shapes.remove(0);
            if let Entry::Segment(segment) = evicted {
                self.release_if_gone(segment.turn);
            }
        }
        self.grew = true;
        self.dirty = true;
    }

    /// A turn no segment shows any more gives up its record and its rows; the index it held stays,
    /// since later segments name their turns by index.
    fn release_if_gone(&mut self, turn: usize) {
        let shown = self
            .entries
            .iter()
            .any(|entry| matches!(entry, Entry::Segment(segment) if segment.turn == turn));
        if !shown && self.live != Some(turn) {
            self.turns[turn] = TurnState::fresh();
        }
    }

    /// A turn begins as one live segment over a fresh record. A turn still live when the next
    /// begins settles first, since no end ever reached it.
    pub fn begin_turn(&mut self) {
        self.roll_up_steps();
        self.turns.push(TurnState::fresh());
        let turn = self.turns.len() - 1;
        self.live = Some(turn);
        self.push(Entry::Segment(Segment {
            turn,
            ordinal: 0,
            from: 0,
            to: None,
            fold: Fold::Live,
        }));
    }

    /// The directory the terminal stands in: paths under it read relative, and a command's `cd`
    /// into it is not restated.
    pub fn set_cwd(&mut self, cwd: PathBuf) {
        self.cwd = cwd;
    }

    fn live_turn(&mut self) -> usize {
        let ended = self
            .live
            .is_some_and(|turn| self.turns[turn].record.end.is_some());
        if self.live.is_none() || ended {
            self.begin_turn();
        }
        self.live.expect("a turn is live")
    }

    /// One frame folded into the live turn's record, or false when the record refused it.
    pub fn fold(&mut self, frame: &Frame, at: &str) -> bool {
        let turn = self.live_turn();
        let folded = fold::fold(&mut self.turns[turn].record, frame, at);
        self.invalidate_open(turn);
        folded
    }

    pub fn record(&self) -> Option<&TurnRecord> {
        let turn = self.live.or_else(|| self.turns.len().checked_sub(1))?;
        Some(&self.turns[turn].record)
    }

    /// The live turn's words still open at its end, past the blocks the transcript already holds.
    /// Words an op started after are drawn whole inside the turn, so they have no live tail.
    pub fn open_answer_tail(&self) -> Option<&str> {
        let turn = &self.turns[self.live?];
        let steps = &turn.record.steps;
        let index = steps
            .iter()
            .rposition(|step| !matches!(step, Step::Reply { .. } | Step::Comment { .. }))?;
        let Step::Text { text, open: true } = &steps[index] else {
            return None;
        };
        if turn.ops.iter().any(|row| row.after_step > index) {
            return None;
        }
        Some(markdown::committed_split(text).1)
    }

    /// Called once a member's message stands in the transcript mid-turn: the turn's open words
    /// close, as a drain closes them, the live segment closes where the record stands, and the
    /// next opens under the message, so what the turn writes and does after the message stands
    /// below it. A segment nothing has been drawn in already stands under the message.
    pub fn split_segment(&mut self) {
        let Some(turn) = self.live else {
            return;
        };
        fold::close_open(&mut self.turns[turn].record.steps);
        if self.empty_open_segment().is_some() {
            return;
        }
        let at = self.turns[turn].record.steps.len();
        let mut ordinal = self.turns[turn].segments - 1;
        if let Some(index) = self.open_segment(turn) {
            if let Entry::Segment(segment) = &mut self.entries[index] {
                segment.to = Some(at);
            }
            self.invalidate(index);
            ordinal = self.turns[turn].segments;
            self.turns[turn].segments += 1;
        }
        self.push(Entry::Segment(Segment {
            turn,
            ordinal,
            from: at,
            to: None,
            fold: Fold::Live,
        }));
    }

    pub fn op_started(&mut self, op: &OpRequest) {
        let turn = self.live_turn();
        let after_step = self.turns[turn].record.steps.len();
        let segment = self.turns[turn].segments - 1;
        self.turns[turn]
            .ops
            .push(OpRow::started(op, segment, after_step));
        self.invalidate_open(turn);
    }

    /// The op's row settles on its reply, rendered once here. An op this transcript never saw
    /// start takes a row where the turn stands now.
    pub fn op_finished(&mut self, op: &OpRequest, reply: &Result<Vec<u8>, String>, theme: &Theme) {
        let found = self
            .turns
            .iter()
            .enumerate()
            .rev()
            .find_map(|(turn, held)| {
                held.ops
                    .iter()
                    .position(|row| row.op.op_id == op.op_id)
                    .map(|at| (turn, at))
            });
        let (turn, at) = match found {
            Some(place) => place,
            None => {
                self.op_started(op);
                let turn = self.live.expect("op_started left a turn live");
                (turn, self.turns[turn].ops.len() - 1)
            }
        };
        let segment = self.turns[turn].ops[at].segment;
        self.turns[turn].ops[at].finish(reply, theme);
        self.invalidate_segment(turn, segment);
    }

    /// The turn's end: every live segment of it rolls up behind its count, its words close, and
    /// an op still running is stopped where it stood.
    pub fn roll_up_steps(&mut self) {
        let Some(turn) = self.live.take() else {
            return;
        };
        fold::close_open(&mut self.turns[turn].record.steps);
        for row in &mut self.turns[turn].ops {
            row.stop();
        }
        for entry in &mut self.entries {
            if let Entry::Segment(segment) = entry {
                if segment.turn == turn && segment.fold == Fold::Live {
                    segment.fold = Fold::Rolled;
                }
            }
        }
        self.invalidate_turn(turn);
    }

    pub fn toggle_steps(&mut self) {
        let found = self.entries.iter().rposition(
            |entry| matches!(entry, Entry::Segment(segment) if segment.fold != Fold::Live),
        );
        let Some(at) = found else {
            return;
        };
        if let Entry::Segment(segment) = &mut self.entries[at] {
            segment.fold = flipped(segment.fold);
        }
        self.invalidate(at);
    }

    pub fn toggle(&mut self, line: usize, col: usize, theme: &Theme) -> bool {
        let Some((index, toggle)) = self.toggle_target(line, col, theme) else {
            return false;
        };
        let Entry::Segment(segment) = &self.entries[index] else {
            return false;
        };
        let (turn, fold) = (segment.turn, segment.fold);
        match toggle {
            Toggle::Fold => {
                if let Entry::Segment(segment) = &mut self.entries[index] {
                    segment.fold = flipped(fold);
                }
            }
            Toggle::Run(key) => {
                let opened = &mut self.turns[turn].opened_runs;
                if !opened.remove(&key) {
                    opened.insert(key);
                }
            }
        }
        self.invalidate_turn(turn);
        true
    }

    pub fn is_toggle(&mut self, line: usize, col: usize, theme: &Theme) -> bool {
        self.toggle_target(line, col, theme).is_some()
    }

    fn toggle_target(&mut self, line: usize, col: usize, theme: &Theme) -> Option<(usize, Toggle)> {
        self.layout(theme);
        if line >= self.total {
            return None;
        }
        let index = self.entry_at(line);
        let Entry::Segment(segment) = &self.entries[index] else {
            return None;
        };
        let segment = *segment;
        let place = self.places[index];
        let at = place.from + (line - place.start);
        let rows = collapse(self.segment_rows(segment, theme), |(line, _)| line);
        let (row, toggle) = rows.get(at)?;
        let toggle = toggle.clone()?;
        (col < wrap::width(&plain_text(row))).then_some((index, toggle))
    }

    fn open_segment(&self, turn: usize) -> Option<usize> {
        self.entries.iter().rposition(
            |entry| matches!(entry, Entry::Segment(segment) if segment.turn == turn && segment.to.is_none()),
        )
    }

    fn empty_open_segment(&self) -> Option<usize> {
        let turn = self.live?;
        let index = self.open_segment(turn)?;
        let Entry::Segment(open) = &self.entries[index] else {
            return None;
        };
        let held = &self.turns[turn];
        let empty = open.from == held.record.steps.len()
            && !held.ops.iter().any(|row| row.segment == open.ordinal);
        empty.then_some(index)
    }

    fn last_segment_of(&self, turn: usize) -> Option<usize> {
        self.entries
            .iter()
            .filter_map(|entry| match entry {
                Entry::Segment(segment) if segment.turn == turn => Some(segment.ordinal),
                _ => None,
            })
            .max()
    }

    fn invalidate(&mut self, at: usize) {
        self.drawn[at] = None;
        self.shapes[at] = None;
        self.dirty = true;
    }

    /// An op's reply and its clock reach the segment the op started in, which a member's message
    /// may have closed since.
    fn invalidate_segment(&mut self, turn: usize, ordinal: usize) {
        let found = self.entries.iter().rposition(|entry| {
            matches!(entry, Entry::Segment(segment) if segment.turn == turn && segment.ordinal == ordinal)
        });
        match found {
            Some(index) => self.invalidate(index),
            None => self.invalidate_turn(turn),
        }
        self.grew = true;
    }

    /// Frames and starting ops reach only the turn's open segment: steps and ops land there, and
    /// a label binds within one segment. The closed segments keep their drawn rows.
    fn invalidate_open(&mut self, turn: usize) {
        match self.open_segment(turn) {
            Some(index) => self.invalidate(index),
            None => self.invalidate_turn(turn),
        }
        self.grew = true;
    }

    fn invalidate_turn(&mut self, turn: usize) {
        for index in 0..self.entries.len() {
            if matches!(&self.entries[index], Entry::Segment(segment) if segment.turn == turn) {
                self.invalidate(index);
            }
        }
        self.grew = true;
    }

    /// A tick redraws the segments of a live turn that hold an op still running, so its clock
    /// and dot move.
    pub fn tick(&mut self, phase: usize) {
        self.phase = phase;
        let Some(turn) = self.live else {
            return;
        };
        let running: Vec<usize> = self.turns[turn]
            .ops
            .iter()
            .filter(|row| row.running())
            .map(|row| row.segment)
            .collect();
        for segment in running {
            self.invalidate_segment(turn, segment);
        }
    }

    fn segment_rows(
        &self,
        segment: Segment,
        theme: &Theme,
    ) -> Vec<(Line<'static>, Option<Toggle>)> {
        let last = self.last_segment_of(segment.turn) == Some(segment.ordinal);
        segment_lines(
            &self.turns[segment.turn],
            segment,
            last,
            &self.cwd,
            self.phase,
            theme,
            self.width,
        )
    }

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

    pub fn scroll(&mut self, up: isize) {
        let max = self.top() as isize;
        self.scroll_back = (self.scroll_back as isize + up).clamp(0, max) as usize;
    }

    fn top(&self) -> usize {
        self.total.saturating_sub(self.rows)
    }

    pub fn scroll_to_end(&mut self) {
        self.scroll_back = 0;
    }

    pub fn scrolled(&self) -> usize {
        self.scroll_back
    }

    pub fn jump_member(&mut self, theme: &Theme, back: bool) {
        self.layout(theme);
        let top = self.total.saturating_sub(self.scroll_back + self.rows);
        let mut target = None;
        for (index, entry) in self.entries.iter().enumerate() {
            if !matches!(entry, Entry::Member(_) | Entry::Fired(_)) {
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

    pub fn window(&mut self, rows: usize, live: &[Line<'static>], theme: &Theme) -> Window {
        let rows = rows.max(1);
        self.rows = rows;
        self.layout(theme);
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

    pub fn document(&mut self, theme: &Theme) -> Vec<Line<'static>> {
        self.layout(theme);
        self.between(0, self.total, theme)
    }

    fn layout(&mut self, theme: &Theme) {
        if !self.dirty {
            return;
        }
        let mut places = Vec::with_capacity(self.entries.len());
        let mut at = 0;
        let mut tail_blank = true;
        for index in 0..self.entries.len() {
            let shape = self.shape(index, theme);
            let held = self.entries[index].keeps_its_leading_blank();
            let from = usize::from(shape.starts_blank && tail_blank && !held);
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
        self.scroll_back = (self.scroll_back + added).min(self.top());
    }

    fn rendered(&self, at: usize, theme: &Theme) -> Vec<Line<'static>> {
        let entry = &self.entries[at];
        let lines = match entry {
            Entry::Segment(segment) => self
                .segment_rows(*segment, theme)
                .into_iter()
                .map(|(line, _)| line)
                .collect(),
            _ => render(entry, theme, self.width),
        };
        match entry.keeps_its_blanks() {
            true => lines,
            false => collapse(lines, |line| line),
        }
    }

    fn shape(&mut self, at: usize, theme: &Theme) -> Shape {
        if let Some(shape) = self.shapes[at] {
            return shape;
        }
        let lines = self.rendered(at, theme);
        let shape = Shape::of(&lines);
        self.shapes[at] = Some(shape);
        shape
    }

    fn draw(&mut self, at: usize, theme: &Theme) -> &[Line<'static>] {
        if self.drawn[at].is_none() {
            let lines = self.rendered(at, theme);
            self.shapes[at] = Some(Shape::of(&lines));
            self.drawn[at] = Some(lines);
        }
        self.drawn[at].as_deref().expect("the entry is drawn")
    }

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
        Entry::Member(text) => member_lines(text, theme, width),
        Entry::Fired(text) => fired_lines(text, theme, width),
        Entry::Markdown(source) => markdown::render(source, theme, width),
        Entry::Note(text) => vec![Line::styled(text.clone(), theme.muted)],
        Entry::Segment(_) => Vec::new(),
        Entry::Raw(lines) => lines.clone(),
        Entry::Masthead => masthead::masthead(theme, width),
    }
}

fn segment_lines(
    turn: &TurnState,
    segment: Segment,
    last: bool,
    cwd: &Path,
    phase: usize,
    theme: &Theme,
    width: u16,
) -> Vec<(Line<'static>, Option<Toggle>)> {
    let steps = &turn.record.steps;
    let to = segment.to.unwrap_or(steps.len()).min(steps.len());
    let from = segment.from.min(to);
    let ops: Vec<&OpRow> = turn
        .ops
        .iter()
        .filter(|row| row.segment == segment.ordinal)
        .collect();
    let ops_end = ops.iter().map(|row| row.after_step).max().unwrap_or(from);
    let mut answer_from = to;
    while answer_from > from && matches!(steps[answer_from - 1], Step::Drain { .. }) {
        answer_from -= 1;
    }
    while answer_from > from.max(ops_end)
        && matches!(
            steps[answer_from - 1],
            Step::Text { .. } | Step::Reply { .. } | Step::Comment { .. }
        )
    {
        answer_from -= 1;
    }
    let bound = bindings(steps, from, to, &ops);
    let disclosed = segment.fold == Fold::Opened;
    let indent = if disclosed { STEP_INDENT } else { "" };
    let room = width.saturating_sub(indent.len() as u16);
    let mut inside = Blocks::default();
    let mut after: Vec<Line<'static>> = Vec::new();
    let mut count = 0;
    let mut words = 0;
    let mut next_op = 0;
    let now = Instant::now();
    let draw_op = |row: &OpRow,
                   label: Option<&str>,
                   inside: &mut Blocks,
                   count: &mut usize,
                   words: &mut usize| {
        let bare = label.is_none_or(|said| said.trim().is_empty());
        let block = row
            .rows(label, cwd, phase, now, theme)
            .into_iter()
            .map(|line| (indented(clipped(&line, room), indent), None))
            .collect();
        inside.push(block, bare && row.op.kind == OP_EXEC);
        *count += *words + 1;
        *words = 0;
    };
    for index in from..to {
        while next_op < ops.len() && ops[next_op].after_step <= index {
            let row = ops[next_op];
            match bound.get(&row.op.call_id) {
                Some(&(step_index, bound_op)) if bound_op == next_op => {
                    if step_index >= row.after_step {
                        let label = label_of(&steps[step_index]);
                        draw_op(row, label, &mut inside, &mut count, &mut words);
                    }
                }
                _ => draw_op(row, None, &mut inside, &mut count, &mut words),
            }
            next_op += 1;
        }
        if index >= answer_from {
            break;
        }
        match &steps[index] {
            Step::Text { text, .. } => {
                words += 1;
                let block = markdown::render(text, theme, room)
                    .into_iter()
                    .map(|line| (indented(line, indent), None))
                    .collect();
                inside.push(block, false);
            }
            Step::Tool { label: None, .. } | Step::Drain { .. } => {}
            Step::Tool {
                label: Some(label),
                call_id,
                ..
            } => match bound.get(call_id) {
                Some(&(_, op_index)) if ops[op_index].after_step <= index => {}
                Some(&(_, op_index)) => draw_op(
                    ops[op_index],
                    Some(label),
                    &mut inside,
                    &mut count,
                    &mut words,
                ),
                None => {
                    inside.push(
                        vec![(
                            Line::styled(format!("{indent}{CALL_MARKER}{label}"), theme.tool_title),
                            None,
                        )],
                        false,
                    );
                    count += words + 1;
                    words = 0;
                }
            },
            Step::Reply { text, .. } | Step::Comment { text, .. } => {
                after.extend(markdown::render(text, theme, width));
            }
            Step::Resumed { .. } => inside.push(
                vec![(
                    Line::styled(format!("{indent}{RESUMED_NOTE}"), theme.muted),
                    None,
                )],
                false,
            ),
        }
    }
    for (op_index, row) in ops.iter().enumerate().skip(next_op) {
        match bound.get(&row.op.call_id) {
            Some(&(step_index, bound_op)) if bound_op == op_index => {
                if step_index >= row.after_step {
                    let label = label_of(&steps[step_index]);
                    draw_op(row, label, &mut inside, &mut count, &mut words);
                }
            }
            _ => draw_op(row, None, &mut inside, &mut count, &mut words),
        }
    }
    let stated = stated_answer(&turn.record);
    let closing = stated.and_then(|_| closing_step(steps));
    for (index, step) in steps.iter().enumerate().take(to).skip(answer_from) {
        match step {
            Step::Text { .. } if stated.is_some() && Some(index) == closing => {
                after.extend(markdown::render(stated.unwrap_or_default(), theme, width));
            }
            Step::Text { text, open } => {
                let shown = if *open && last {
                    markdown::committed_split(text).0
                } else {
                    text
                };
                after.extend(markdown::render(shown, theme, width));
            }
            Step::Reply { text, .. } | Step::Comment { text, .. } => {
                after.extend(markdown::render(text, theme, width));
            }
            _ => {}
        }
    }
    if let (true, Some(text), None) = (last, stated, closing) {
        after.extend(markdown::render(text, theme, width));
    }
    if last {
        if segment.fold != Fold::Live {
            for run in &turn.record.runs {
                inside.push(run_rows(run, turn, indent, theme), false);
            }
        }
        count += fold::runs_that_worked(&turn.record.runs);
    }
    let answers = !after.is_empty()
        || steps[answer_from..to].iter().any(|step| {
            matches!(
                step,
                Step::Text { .. } | Step::Reply { .. } | Step::Comment { .. }
            )
        });
    let summary = |mark: &str| Line::styled(format!("{} {mark}", rollup_line(count)), theme.muted);
    let mut rows = Vec::new();
    match segment.fold {
        Fold::Live => {
            rows.extend(inside.rows);
            if !rows.is_empty() && answers {
                rows.push((Line::raw(""), None));
            }
        }
        Fold::Rolled if count > 0 => {
            rows.push((summary(FOLD_ROLLED), Some(Toggle::Fold)));
            rows.push((Line::raw(""), None));
        }
        Fold::Opened if count > 0 => {
            rows.push((summary(FOLD_OPENED), Some(Toggle::Fold)));
            rows.extend(inside.rows);
            rows.push((Line::raw(""), None));
        }
        Fold::Rolled | Fold::Opened => {}
    }
    rows.extend(after.into_iter().map(|line| (line, None)));
    rows
}

/// The blocks a segment draws inside its fold, a blank row apart. A bare command — an exec op no
/// label heads — stands only until the next bare command follows it, which takes its place.
#[derive(Default)]
struct Blocks {
    rows: Vec<(Line<'static>, Option<Toggle>)>,
    command_at: Option<usize>,
}

impl Blocks {
    fn push(&mut self, block: Vec<(Line<'static>, Option<Toggle>)>, command: bool) {
        if let (true, Some(at)) = (command, self.command_at) {
            self.rows.truncate(at);
        }
        let at = self.rows.len();
        if at > 0 {
            self.rows.push((Line::raw(""), None));
        }
        self.rows.extend(block);
        self.command_at = command.then_some(at);
    }
}

/// The labelled tool step each op of the segment serves, by the call they share; the first op
/// of a call binds, a later one heads its own row.
fn bindings(
    steps: &[Step],
    from: usize,
    to: usize,
    ops: &[&OpRow],
) -> HashMap<String, (usize, usize)> {
    let mut bound = HashMap::new();
    for (op_index, row) in ops.iter().enumerate() {
        if row.op.call_id.is_empty() {
            continue;
        }
        let step_index = (from..to).find(|&index| {
            matches!(&steps[index], Step::Tool { label: Some(_), call_id, .. } if *call_id == row.op.call_id)
        });
        if let Some(step_index) = step_index {
            bound
                .entry(row.op.call_id.clone())
                .or_insert((step_index, op_index));
        }
    }
    bound
}

/// The turn's closing passage: the text step past its last tool step, wherever a member's message
/// split the segments; replies and drains after it do not move it.
fn closing_step(steps: &[Step]) -> Option<usize> {
    let index = steps.iter().rposition(|step| {
        !matches!(
            step,
            Step::Reply { .. } | Step::Comment { .. } | Step::Drain { .. }
        )
    })?;
    matches!(steps[index], Step::Text { .. }).then_some(index)
}

/// A done frame's words are the answer and stand where the closing passage streamed, as the
/// portal's `answerOf` has it: a resume that streamed nothing and a stream an op cut read once.
fn stated_answer(record: &TurnRecord) -> Option<&str> {
    match &record.end {
        Some(TurnEnd::Terminal { frame, .. }) if frame.status == DONE => {
            frame.text.as_deref().filter(|text| !text.is_empty())
        }
        _ => None,
    }
}

fn label_of(step: &Step) -> Option<&str> {
    match step {
        Step::Tool { label, .. } => label.as_deref(),
        _ => None,
    }
}

fn indented(mut line: Line<'static>, indent: &str) -> Line<'static> {
    if !indent.is_empty() {
        line.spans.insert(0, Span::raw(indent.to_string()));
    }
    line
}

fn run_key(run: &SubagentRun) -> String {
    run.turn_id
        .clone()
        .unwrap_or_else(|| run.conversation_id.clone())
}

fn run_label(run: &SubagentRun) -> &str {
    match run.name.as_deref() {
        Some(name) if !name.is_empty() => name,
        _ => &run.profile,
    }
}

fn run_rows(
    run: &SubagentRun,
    turn: &TurnState,
    indent: &str,
    theme: &Theme,
) -> Vec<(Line<'static>, Option<Toggle>)> {
    let key = run_key(run);
    let opened = turn.opened_runs.contains(&key);
    let mark = if opened { FOLD_OPENED } else { FOLD_ROLLED };
    let mut rows = vec![(
        Line::styled(format!("{indent}{} {mark}", run_label(run)), theme.muted),
        Some(Toggle::Run(key)),
    )];
    if !opened {
        return rows;
    }
    for event in &run.events {
        rows.push((
            Line::styled(
                format!("{indent}{RUN_INDENT}{CALL_MARKER}{}", event.text),
                theme.tool_title,
            ),
            None,
        ));
    }
    let deeper = format!("{indent}{RUN_INDENT}");
    for nested in &run.subagents {
        rows.extend(run_rows(nested, turn, &deeper, theme));
    }
    rows
}

fn clipped(line: &Line<'static>, width: u16) -> Line<'static> {
    let width = width as usize;
    if line
        .spans
        .iter()
        .map(|span| wrap::width(&span.content))
        .sum::<usize>()
        <= width
    {
        return line.clone();
    }
    let room = width.saturating_sub(1);
    let mut used = 0;
    let mut kept = Vec::new();
    let mut style = line.style;
    for span in &line.spans {
        let left = room.saturating_sub(used);
        if left == 0 {
            break;
        }
        let cut = wrap::clip(&span.content, left);
        used += wrap::width(cut);
        style = span.style;
        kept.push(Span::styled(cut.to_string(), span.style));
        if cut.len() < span.content.len() {
            break;
        }
    }
    kept.push(Span::styled("…", style));
    Line::from(kept)
}

fn member_lines(text: &str, theme: &Theme, width: u16) -> Vec<Line<'static>> {
    let caret = Span::styled(format!("{PROMPT_IDLE} "), theme.prompt);
    let mut lines = vec![Line::raw("")];
    lines.extend(said_lines(text, caret, theme, width));
    lines.push(Line::raw(""));
    lines
}

fn fired_lines(text: &str, theme: &Theme, width: u16) -> Vec<Line<'static>> {
    let mut lines = vec![
        Line::raw(""),
        Line::styled(SENT_BY_UFO.to_string(), theme.muted),
    ];
    lines.extend(said_lines(
        text,
        Span::raw(ECHO_INDENT.to_string()),
        theme,
        width,
    ));
    lines.push(Line::raw(""));
    lines
}

fn said_lines(text: &str, lead: Span<'static>, theme: &Theme, width: u16) -> Vec<Line<'static>> {
    let cap = (width as usize)
        .saturating_sub(wrap::width(ECHO_INDENT))
        .max(1);
    let mut rows: Vec<&str> = text.lines().collect();
    if rows.is_empty() {
        rows.push("");
    }
    let mut lines = Vec::new();
    for row in rows {
        let mut rest = row;
        loop {
            let (head, next) = wrap::wrap_head(rest, cap);
            let first = if lines.is_empty() {
                lead.clone()
            } else {
                Span::raw(ECHO_INDENT.to_string())
            };
            lines.push(Line::from(vec![
                first,
                Span::styled(rest[..head].to_string(), theme.member),
            ]));
            if next >= rest.len() {
                break;
            }
            rest = &rest[next..];
        }
    }
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

fn plain_text(line: &Line) -> String {
    line.spans
        .iter()
        .flat_map(|span| wrap::units(&span.content))
        .filter(|(_, step)| *step > 0)
        .map(|(unit, _)| unit)
        .collect()
}

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

    const AT: &str = "2026-09-16T00:00:00Z";

    fn words(retained: &mut Retained, text: &str) {
        words_fold(retained, text);
    }

    fn words_fold(retained: &mut Retained, text: &str) -> bool {
        retained.fold(
            &Frame::Message {
                text: text.to_string(),
            },
            AT,
        )
    }

    fn label(retained: &mut Retained, text: &str) {
        labelled(retained, text, "");
    }

    fn labelled(retained: &mut Retained, text: &str, call_id: &str) {
        retained.fold(
            &Frame::Activity {
                text: text.to_string(),
                call_id: call_id.to_string(),
            },
            AT,
        );
    }

    fn run(retained: &mut Retained, activity: &str) {
        retained.fold(
            &Frame::SubagentActivity(crate::fold::RunFrame {
                turn_id: "run-1".to_string(),
                parent_turn_id: "turn-1".to_string(),
                conversation_id: "conv-1".to_string(),
                profile: "reviewer".to_string(),
                name: String::new(),
                activity: activity.to_string(),
                status: String::new(),
            }),
            AT,
        );
    }

    fn op(op_id: &str, call_id: &str, command: &str) -> OpRequest {
        OpRequest {
            op_id: op_id.to_string(),
            kind: "exec".to_string(),
            name: "exec".to_string(),
            timeout_s: 30,
            arg: String::new(),
            params: format!(
                r#"{{"argv":["bash","-lc","{command}"],"safety_argv":["bash","-lc","{command}"]}}"#
            ),
            call_id: call_id.to_string(),
        }
    }

    fn exec_reply(exit_code: i32, stdout: &str) -> Result<Vec<u8>, String> {
        use base64::Engine as _;
        Ok(format!(
            r#"{{"exit_code":{exit_code},"stdout_b64":"{}","stderr_b64":""}}"#,
            base64::engine::general_purpose::STANDARD.encode(stdout.as_bytes())
        )
        .into_bytes())
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
    fn scrolling_past_the_top_holds_the_first_screen() {
        let theme = theme();
        let mut retained = notes(30);
        let _ = retained.window(10, &[], &theme);
        retained.scroll(100);
        let window = retained.window(10, &[], &theme);
        assert_eq!(window.start, 0);
        assert_eq!(retained.scrolled(), 20);
        let expected: Vec<String> = (0..10).map(|index| format!("line {index}")).collect();
        assert_eq!(texts(&window.lines), expected);
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
    fn a_fired_message_stands_under_the_line_saying_ufo_sent_it() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.push(Entry::Fired("github: Fix the build updated\nsecond".into()));
        assert_eq!(
            texts(&retained.document(&theme)),
            [
                "∵ Sent by UFO",
                "  github: Fix the build updated",
                "  second",
                ""
            ]
        );
    }

    #[test]
    fn a_long_member_message_wraps_under_its_caret() {
        let theme = theme();
        let mut retained = Retained::new(20);
        retained.push(Entry::Member("one two three four five six\nseven".into()));
        assert_eq!(
            texts(&retained.document(&theme)),
            ["› one two three four", "  five six", "  seven", ""]
        );
        let mut empty = Retained::new(20);
        empty.push(Entry::Member(String::new()));
        assert_eq!(texts(&empty.document(&theme)), ["› ", ""]);
    }

    #[test]
    fn a_streamed_reply_holds_its_closed_blocks_and_leaves_the_tail_live() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        words(&mut retained, "one two\n\n");
        words(&mut retained, "next\n");
        assert_eq!(texts(&retained.document(&theme)), ["one two"]);
        assert_eq!(retained.open_answer_tail(), Some("next\n"));
        label(&mut retained, "done");
        assert_eq!(
            texts(&retained.document(&theme)),
            ["one two", "", "next", "", "⏺ done"]
        );
        assert_eq!(retained.open_answer_tail(), None);
    }

    #[test]
    fn a_growing_reply_carries_a_scrolled_reader() {
        let theme = theme();
        let mut retained = notes(30);
        let _ = retained.window(10, &[], &theme);
        retained.scroll(5);
        let before = retained.window(10, &[], &theme);
        retained.begin_turn();
        words(&mut retained, "tail line\n\n");
        let opened = retained.window(10, &[], &theme);
        assert_eq!(opened.start, before.start);
        assert_eq!(retained.scrolled(), 6);
        words(&mut retained, "more\n\n");
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
            ["› hi", "", "› again", "", "body"],
            "one blank stands between entries, however many they wrote"
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
    fn the_mark_is_drawn_at_the_width_it_is_read_at() {
        let theme = theme();
        let mut retained = Retained::new(80);
        retained.push(Entry::Masthead);
        let wide = texts(&retained.document(&theme));
        assert!(
            wide.iter()
                .any(|row| row.contains(env!("CARGO_PKG_VERSION"))),
            "{wide:?}"
        );
        retained.set_width(70);
        let narrow = texts(&retained.document(&theme));
        assert_eq!(narrow.len(), wide.len());
        assert!(
            narrow
                .iter()
                .all(|row| !row.contains(env!("CARGO_PKG_VERSION"))),
            "a width with no room for the version drops it: {narrow:?}"
        );
        retained.set_width(30);
        assert!(
            texts(&retained.document(&theme)).is_empty(),
            "a width with no room for the mark drops it whole"
        );
        retained.set_width(80);
        assert_eq!(texts(&retained.document(&theme)), wide);
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
        retained.begin_turn();
        words(&mut retained, "reading the calendar next");
        label(&mut retained, "the calendar");
        label(&mut retained, "loading skill: office/pptx");
        assert_eq!(
            texts(&retained.document(&theme)),
            [
                "reading the calendar next",
                "",
                "⏺ the calendar",
                "",
                "⏺ loading skill: office/pptx",
            ]
        );
    }

    #[test]
    fn words_a_late_label_cut_stand_before_it_and_the_rest_answer() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        words(&mut retained, "The answer has started");
        label(&mut retained, "Checking the result.");
        words(&mut retained, "and now ends.");
        assert_eq!(
            texts(&retained.document(&theme)),
            ["The answer has started", "", "⏺ Checking the result.", ""],
            "the live answer stands a row under the last step"
        );
        assert_eq!(retained.open_answer_tail(), Some("and now ends."));
        retained.roll_up_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 2 steps ▸", "", "and now ends."]
        );
    }

    #[test]
    fn a_member_message_stands_clear_of_the_work_it_started() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.push(Entry::Member("draft the post".into()));
        retained.begin_turn();
        label(&mut retained, "the drafts");
        assert_eq!(
            texts(&retained.document(&theme)),
            ["\u{203a} draft the post", "", "⏺ the drafts"],
            "one row stands between the ask and the first thing the turn did"
        );
    }

    fn done(retained: &mut Retained, text: &str) {
        let frame = Frame::decode(
            "terminal",
            &format!(r#"{{"status":"done","text":{}}}"#, serde_json::json!(text)),
        )
        .expect("a done frame");
        retained.fold(&frame, AT);
    }

    #[test]
    fn a_done_frames_text_replaces_the_words_that_streamed() {
        let theme = theme();
        let mut retained = Retained::new(60);
        retained.begin_turn();
        label(&mut retained, "Sharing the note");
        words(&mut retained, "Sending the note.");
        done(&mut retained, "Sent: note.md");
        retained.roll_up_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 1 step ▸", "", "Sent: note.md"]
        );
    }

    #[test]
    fn a_done_frame_states_the_answer_a_resume_never_streamed() {
        let theme = theme();
        let mut retained = Retained::new(60);
        retained.begin_turn();
        done(&mut retained, "Here it is.");
        retained.roll_up_steps();
        assert_eq!(texts(&retained.document(&theme)), ["Here it is."]);
    }

    #[test]
    fn a_done_frame_replaces_a_closing_passage_a_member_message_split_off() {
        let theme = theme();
        let mut retained = Retained::new(60);
        retained.begin_turn();
        label(&mut retained, "Sharing the note");
        words(&mut retained, "Sent: the note.");
        retained.push(Entry::Member("thanks".into()));
        retained.split_segment();
        done(&mut retained, "Sent: note.md");
        retained.roll_up_steps();
        let drawn = texts(&retained.document(&theme));
        assert_eq!(
            drawn.iter().filter(|line| line.contains("Sent:")).count(),
            1,
            "{drawn:?}"
        );
        assert!(drawn.contains(&"Sent: note.md".to_string()), "{drawn:?}");
    }

    #[test]
    fn a_frame_after_the_turns_end_begins_the_next_turn() {
        let theme = theme();
        let mut retained = Retained::new(60);
        retained.begin_turn();
        done(&mut retained, "First answer.");
        assert!(words_fold(&mut retained, "Second answer."));
        retained.roll_up_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            ["First answer.", "Second answer."]
        );
    }

    #[test]
    fn a_new_turns_first_step_stays_below_the_previous_answer() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.push(Entry::Markdown("The previous answer.".into()));
        retained.begin_turn();
        label(&mut retained, "Checking the result.");
        words(&mut retained, "The new answer.");
        retained.roll_up_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            [
                "The previous answer.",
                "Completed 1 step ▸",
                "",
                "The new answer.",
            ]
        );
    }

    #[test]
    fn the_turns_end_rolls_its_steps_up_and_the_member_opens_them_again() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        words(&mut retained, "first the calendar");
        label(&mut retained, "the calendar");
        words(&mut retained, "the answer");
        retained.roll_up_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 2 steps ▸", "", "the answer"]
        );
        retained.toggle_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            [
                "Completed 2 steps ▾",
                "  first the calendar",
                "",
                "  ⏺ the calendar",
                "",
                "the answer",
            ]
        );
        retained.toggle_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 2 steps ▸", "", "the answer"]
        );
    }

    #[test]
    fn one_step_is_said_in_the_singular_and_the_next_turn_rolls_up_on_its_own() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        label(&mut retained, "ls");
        retained.roll_up_steps();
        retained.push(Entry::Member("and again".into()));
        retained.begin_turn();
        label(&mut retained, "ls");
        label(&mut retained, "notes");
        retained.roll_up_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            [
                "Completed 1 step ▸",
                "",
                "› and again",
                "",
                "Completed 2 steps ▸",
                "",
            ]
        );
    }

    #[test]
    fn a_run_counts_as_one_step_however_many_calls_it_states() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        label(&mut retained, "reviewer");
        run(&mut retained, "the diff");
        run(&mut retained, "cargo test");
        retained.roll_up_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 2 steps ▸", ""]
        );
        retained.toggle_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            [
                "Completed 2 steps ▾",
                "  ⏺ reviewer",
                "",
                "  reviewer ▸",
                ""
            ]
        );
        assert!(retained.toggle(3, 0, &theme));
        assert_eq!(
            texts(&retained.document(&theme)),
            [
                "Completed 2 steps ▾",
                "  ⏺ reviewer",
                "",
                "  reviewer ▾",
                "    ⏺ the diff",
                "    ⏺ cargo test",
                "",
            ]
        );
        assert!(retained.toggle(3, 0, &theme));
        assert_eq!(
            texts(&retained.document(&theme)),
            [
                "Completed 2 steps ▾",
                "  ⏺ reviewer",
                "",
                "  reviewer ▸",
                ""
            ]
        );
    }

    #[test]
    fn a_live_run_draws_no_row_until_the_turn_ends() {
        let theme = theme();
        let mut retained = Retained::new(60);
        retained.begin_turn();
        run(&mut retained, "the diff");
        run(&mut retained, "cargo test");
        assert_eq!(
            texts(&retained.document(&theme)),
            Vec::<String>::new(),
            "the bottom line alone carries a running run"
        );
        retained.roll_up_steps();
        retained.toggle_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 1 step ▾", "  reviewer ▸", ""]
        );
        assert!(retained.toggle(1, 2, &theme));
        assert_eq!(
            texts(&retained.document(&theme)),
            [
                "Completed 1 step ▾",
                "  reviewer ▾",
                "    ⏺ the diff",
                "    ⏺ cargo test",
                "",
            ]
        );
    }

    #[test]
    fn a_click_on_the_rollup_line_opens_and_closes_the_turn() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        label(&mut retained, "ls");
        words(&mut retained, "the answer");
        retained.roll_up_steps();
        assert!(retained.is_toggle(0, 3, &theme));
        assert!(!retained.is_toggle(0, 30, &theme));
        assert!(!retained.is_toggle(1, 0, &theme));
        assert!(retained.toggle(0, 0, &theme));
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 1 step ▾", "  ⏺ ls", "", "the answer"]
        );
        assert!(retained.toggle(0, 5, &theme));
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 1 step ▸", "", "the answer"]
        );
        assert!(!retained.toggle(0, 30, &theme));
        assert!(!retained.toggle(1, 0, &theme));
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 1 step ▸", "", "the answer"]
        );
    }

    #[test]
    fn a_late_row_of_a_settled_run_opens_a_fresh_run() {
        let theme = theme();
        let mut retained = Retained::new(60);
        retained.begin_turn();
        run(&mut retained, "x");
        retained.roll_up_steps();
        run(&mut retained, "late");
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 1 step ▸", ""]
        );
        retained.roll_up_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 1 step ▸", "", "Completed 1 step ▸", ""]
        );
    }

    #[test]
    fn the_toggle_leaves_the_turn_still_writing_its_steps_open() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        label(&mut retained, "notes");
        retained.roll_up_steps();
        retained.begin_turn();
        label(&mut retained, "ls");
        retained.toggle_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 1 step ▾", "  ⏺ notes", "", "⏺ ls"]
        );
    }

    #[test]
    fn the_open_reply_becomes_a_thought_when_a_step_follows_it() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        words(&mut retained, "a thought");
        assert_eq!(texts(&retained.document(&theme)), Vec::<String>::new());
        assert_eq!(retained.open_answer_tail(), Some("a thought"));
        label(&mut retained, "ls");
        assert_eq!(texts(&retained.document(&theme)), ["a thought", "", "⏺ ls"]);
    }

    #[test]
    fn an_op_binds_to_the_label_naming_its_call_whichever_lands_first() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        labelled(&mut retained, "counting the rows", "c1");
        retained.op_started(&op("op1", "c1", "wc"));
        retained.op_finished(&op("op1", "c1", "wc"), &exec_reply(0, "42\n"), &theme);
        assert_eq!(
            texts(&retained.document(&theme)),
            ["⏺ counting the rows", "  $ wc", "  42"]
        );
        retained.roll_up_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 1 step ▸", ""]
        );

        let mut retained = Retained::new(40);
        retained.begin_turn();
        retained.op_started(&op("op1", "c1", "wc"));
        assert_eq!(texts(&retained.document(&theme)), ["⏺ $ wc"]);
        retained.op_finished(&op("op1", "c1", "wc"), &exec_reply(0, "42\n"), &theme);
        labelled(&mut retained, "counting the rows", "c1");
        assert_eq!(
            texts(&retained.document(&theme)),
            ["⏺ counting the rows", "  $ wc", "  42"],
            "the label re-heads the row that already ran"
        );
        retained.roll_up_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 1 step ▸", ""]
        );
    }

    #[test]
    fn an_op_with_no_label_heads_its_own_row_and_counts_as_a_step() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        words(&mut retained, "building");
        retained.op_finished(&op("op1", "", "make"), &exec_reply(0, "built\n"), &theme);
        assert_eq!(
            texts(&retained.document(&theme)),
            ["building", "", "⏺ $ make", "  built"]
        );
        retained.roll_up_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 2 steps ▸", ""]
        );
    }

    #[test]
    fn contiguous_bare_commands_stand_as_the_latest_and_all_count() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        retained.op_finished(&op("op1", "", "ls"), &exec_reply(0, "a\n"), &theme);
        retained.op_finished(&op("op2", "", "cat a"), &exec_reply(0, "one\n"), &theme);
        retained.op_started(&op("op3", "", "grep one a"));
        assert_eq!(
            texts(&retained.document(&theme)),
            ["⏺ $ grep one a"],
            "a bare command takes the place of the bare command before it"
        );
        retained.op_finished(&op("op3", "", "grep one a"), &exec_reply(1, ""), &theme);
        assert_eq!(
            texts(&retained.document(&theme)),
            ["⏺ $ grep one a", "  exit 1"]
        );
        retained.roll_up_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 3 steps ▸", ""],
            "the count is the record's, not the rows'"
        );
    }

    #[test]
    fn a_bare_command_stays_when_another_block_stands_between() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        retained.op_finished(&op("op1", "", "ls"), &exec_reply(0, "a\n"), &theme);
        let read = OpRequest {
            op_id: "op2".to_string(),
            kind: "fileop".to_string(),
            name: "read".to_string(),
            timeout_s: 30,
            arg: String::new(),
            params: r#"{"path":"a"}"#.to_string(),
            call_id: "c2".to_string(),
        };
        retained.op_finished(&read, &Ok(b"{}".to_vec()), &theme);
        retained.op_finished(&op("op3", "", "cat a"), &exec_reply(0, "one\n"), &theme);
        label(&mut retained, "the notes");
        retained.op_finished(&op("op4", "", "wc a"), &exec_reply(0, "1\n"), &theme);
        assert_eq!(
            texts(&retained.document(&theme)),
            [
                "⏺ $ ls",
                "  a",
                "",
                "⏺ read a",
                "",
                "⏺ $ cat a",
                "  one",
                "",
                "⏺ the notes",
                "",
                "⏺ $ wc a",
                "  1",
            ]
        );
    }

    #[test]
    fn what_arrives_before_the_turn_draws_stands_above_its_rows() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        retained.push(Entry::Member("go".into()));
        retained.split_segment();
        retained.push(Entry::Note("Workspace: /w".into()));
        label(&mut retained, "first");
        retained.push(Entry::Note("late".into()));
        words(&mut retained, "the answer");
        retained.roll_up_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            [
                "› go",
                "",
                "Workspace: /w",
                "Completed 1 step ▸",
                "",
                "the answer",
                "late",
            ]
        );
    }

    #[test]
    fn a_drained_message_stands_between_the_segments_it_cut() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        label(&mut retained, "first");
        words(&mut retained, "before");
        retained.fold(
            &Frame::Absorbed {
                arrivals: vec!["a1".to_string()],
            },
            AT,
        );
        retained.push(Entry::Member("the message".into()));
        retained.split_segment();
        label(&mut retained, "second");
        words(&mut retained, "after");
        assert_eq!(
            texts(&retained.document(&theme)),
            [
                "⏺ first",
                "",
                "before",
                "",
                "› the message",
                "",
                "⏺ second",
                ""
            ]
        );
        assert_eq!(retained.open_answer_tail(), Some("after"));
        retained.roll_up_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            [
                "Completed 1 step ▸",
                "",
                "before",
                "",
                "› the message",
                "",
                "Completed 1 step ▸",
                "",
                "after",
            ]
        );
    }

    #[test]
    fn a_split_before_anything_was_drawn_moves_the_empty_segment_under_the_message() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        retained.push(Entry::Member("go".into()));
        retained.split_segment();
        label(&mut retained, "first");
        assert_eq!(texts(&retained.document(&theme)), ["› go", "", "⏺ first"]);
    }

    #[test]
    fn a_row_wider_than_the_transcript_is_cut_under_an_ellipsis() {
        let theme = theme();
        let mut retained = Retained::new(14);
        retained.begin_turn();
        retained.op_finished(
            &op("op1", "", "make test --verbose"),
            &exec_reply(0, "a line that runs past the edge\n"),
            &theme,
        );
        assert_eq!(
            texts(&retained.document(&theme)),
            ["⏺ $ make test…", "  a line that…"]
        );
    }

    #[test]
    fn a_frame_redraws_only_the_segment_it_lands_in() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        label(&mut retained, "first");
        retained.op_finished(&op("op1", "", "make"), &exec_reply(0, "built\n"), &theme);
        retained.push(Entry::Member("the message".into()));
        retained.split_segment();
        let _ = retained.document(&theme);
        let closed = retained
            .entries
            .iter()
            .position(|entry| matches!(entry, Entry::Segment(segment) if segment.to.is_some()))
            .expect("the closed segment");
        assert!(retained.drawn[closed].is_some());
        label(&mut retained, "second");
        assert!(
            retained.drawn[closed].is_some(),
            "a frame in the open segment leaves the closed one drawn"
        );
        retained.roll_up_steps();
        assert!(
            retained.drawn[closed].is_none(),
            "the roll-up redraws every segment of the turn"
        );
    }

    #[test]
    fn eviction_releases_the_turn_it_scrolled_out() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        label(&mut retained, "first");
        retained.op_finished(&op("op1", "", "make"), &exec_reply(0, "built\n"), &theme);
        retained.roll_up_steps();
        assert_eq!(retained.turns[0].ops.len(), 1);
        for index in 0..ENTRY_MAX {
            retained.push(Entry::Note(format!("line {index}")));
        }
        assert!(
            retained.turns[0].ops.is_empty() && retained.turns[0].record.steps.is_empty(),
            "a turn no segment shows gives up its record and rows"
        );
        assert_eq!(
            retained.turns.len(),
            1,
            "its index stays for later segments"
        );
    }

    #[test]
    fn an_op_settles_in_the_segment_it_started_in_after_a_message_closed_it() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        retained.op_started(&op("op1", "", "make"));
        retained.push(Entry::Member("the message".into()));
        retained.split_segment();
        let _ = retained.document(&theme);
        retained.op_finished(&op("op1", "", "make"), &exec_reply(0, "built\n"), &theme);
        let document = texts(&retained.document(&theme));
        let built = document.iter().position(|line| line == "  built");
        let echoed = document.iter().position(|line| line == "› the message");
        assert!(
            built.is_some() && built < echoed,
            "the finished op draws its output where it started, above the message: {document:?}"
        );
        assert!(
            !document.iter().any(|line| line.contains("stopped")),
            "the op ended on its own reply: {document:?}"
        );
    }

    #[test]
    fn words_an_op_follows_are_drawn_once_with_no_live_tail() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        words(&mut retained, "Let me check");
        assert_eq!(retained.open_answer_tail(), Some("Let me check"));
        retained.op_started(&op("op1", "", "wc"));
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Let me check", "", "⏺ $ wc"],
            "the words the op followed stand whole inside the turn"
        );
        assert_eq!(
            retained.open_answer_tail(),
            None,
            "and are not drawn again as a tail"
        );
    }

    #[test]
    fn a_calls_later_op_heads_its_own_row_whichever_order_the_label_came() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        labelled(&mut retained, "Starting the server", "c1");
        retained.op_finished(&op("op1", "c1", "serve"), &exec_reply(0, "up\n"), &theme);
        retained.op_finished(&op("op2", "c1", "stop"), &exec_reply(0, "down\n"), &theme);
        assert_eq!(
            texts(&retained.document(&theme)),
            [
                "⏺ Starting the server",
                "  $ serve",
                "  up",
                "",
                "⏺ $ stop",
                "  down"
            ]
        );
        retained.roll_up_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Completed 2 steps ▸", ""]
        );

        let mut retained = Retained::new(40);
        retained.begin_turn();
        retained.op_finished(&op("op1", "c1", "serve"), &exec_reply(0, "up\n"), &theme);
        retained.op_finished(&op("op2", "c1", "stop"), &exec_reply(0, "down\n"), &theme);
        labelled(&mut retained, "Starting the server", "c1");
        assert_eq!(
            texts(&retained.document(&theme)),
            [
                "⏺ Starting the server",
                "  $ serve",
                "  up",
                "",
                "⏺ $ stop",
                "  down"
            ]
        );
    }

    #[test]
    fn words_streamed_across_a_message_stand_once_on_each_side_of_it() {
        let theme = theme();
        let mut retained = Retained::new(40);
        retained.begin_turn();
        words(&mut retained, "Half");
        retained.push(Entry::Member("the message".into()));
        retained.split_segment();
        words(&mut retained, "way");
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Half", "", "› the message", ""],
            "the words before the message stand closed above it"
        );
        assert_eq!(
            retained.open_answer_tail(),
            Some("way"),
            "the words after it are the open answer, drawn live once"
        );
        retained.roll_up_steps();
        assert_eq!(
            texts(&retained.document(&theme)),
            ["Half", "", "› the message", "", "way"]
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
