//! Markdown rendered to styled transcript lines: headings, emphasis, lists, tables, quotes,
//! rules, and fenced code blocks highlighted through the theme's roles.

use std::sync::OnceLock;

use pulldown_cmark::{Alignment, CodeBlockKind, Event, Options, Parser, Tag, TagEnd};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use syntect::easy::HighlightLines;
use syntect::highlighting::{Color as InkColor, ThemeSet};
use syntect::parsing::SyntaxSet;

use crate::ui::osc;
use crate::ui::theme::{rgb_to_256, ColorMode, Scheme, Theme};
use crate::ui::wrap;

const BULLET: &str = "• ";
const QUOTE_BAR: &str = "▎ ";
const RULE: &str = "─";
const BAR: &str = "│";
const TOP: [&str; 3] = ["┌", "┬", "┐"];
const SPINE: [&str; 3] = ["├", "┼", "┤"];
const FOOT: [&str; 3] = ["└", "┴", "┘"];
const CODE_INDENT: &str = "  ";
const LIST_INDENT: usize = 2;
const CELL_FRAME: usize = 3;
const INK_DARK: &str = "base16-ocean.dark";
const INK_LIGHT: &str = "InspiredGitHub";

/// Render one block of finished markdown to styled lines wrapped at `width`.
pub fn render(text: &str, theme: &Theme, width: u16) -> Vec<Line<'static>> {
    Render::new(theme, width).run(text)
}

/// Accumulates a streamed reply and commits fully-arrived markdown blocks as source text,
/// holding back the open tail so a half-arrived construct is never committed mid-block. The
/// committed source keeps its own blank separators, so the transcript re-wraps it whole.
pub struct StreamRenderer {
    pending: String,
}

impl StreamRenderer {
    pub fn new() -> StreamRenderer {
        StreamRenderer {
            pending: String::new(),
        }
    }

    /// Feed one delta; returns the source now safe to commit to the transcript. A block still
    /// arriving — an open fence, a table, a list, a paragraph — is held back whole.
    pub fn push(&mut self, chunk: &str) -> String {
        self.pending.push_str(chunk);
        let closed = closed_block_end(&self.pending);
        self.pending.drain(..closed).collect()
    }

    /// The uncommitted tail, shown in the activity row while the reply streams.
    pub fn open_tail(&self) -> &str {
        &self.pending
    }

    /// End of stream: whatever remains commits now.
    pub fn finish(&mut self) -> String {
        std::mem::take(&mut self.pending)
    }
}

/// The end of the last block that has fully arrived: a closing fence, or a blank line that no open
/// fence or list is holding. A table ends at a blank line like any other block — the parser reads
/// the line after a row as another row, so a commit there would state something a re-read denies.
fn closed_block_end(text: &str) -> usize {
    let mut closed = 0;
    let mut at = 0;
    let mut fence: Option<(char, usize)> = None;
    let mut list = false;
    let mut blank = false;
    for raw in text.split_inclusive('\n') {
        let start = at;
        at += raw.len();
        let whole = raw.ends_with('\n');
        let line = raw.trim_end_matches(['\r', '\n']);
        let head = line.trim_start();
        if let Some((glyph, len)) = fence {
            if whole && closes_fence(head, glyph, len) {
                fence = None;
                closed = at;
            }
            continue;
        }
        if list && blank && !head.is_empty() && !is_item(head) && !line.starts_with([' ', '\t']) {
            list = false;
            closed = start;
        }
        blank = whole && head.is_empty();
        if let Some(open) = opens_fence(head) {
            fence = Some(open);
            continue;
        }
        if head.is_empty() {
            if whole && !list {
                closed = at;
            }
            continue;
        }
        if is_item(head) {
            list = true;
        }
    }
    closed
}

fn opens_fence(line: &str) -> Option<(char, usize)> {
    let glyph = line.chars().next()?;
    if glyph != '`' && glyph != '~' {
        return None;
    }
    let len = line.chars().take_while(|ch| *ch == glyph).count();
    (len >= 3).then_some((glyph, len))
}

fn closes_fence(line: &str, glyph: char, len: usize) -> bool {
    let run = line.chars().take_while(|ch| *ch == glyph).count();
    run >= len && line[run..].trim().is_empty()
}

fn is_item(line: &str) -> bool {
    let Some((marker, _)) = line.split_once(' ') else {
        return false;
    };
    matches!(marker, "-" | "*" | "+")
        || (marker.len() > 1
            && marker.ends_with(['.', ')'])
            && marker[..marker.len() - 1]
                .chars()
                .all(|ch| ch.is_ascii_digit()))
}

struct Level {
    next: Option<u64>,
    marker: usize,
}

struct Cells {
    aligns: Vec<Alignment>,
    head: Vec<Vec<Span<'static>>>,
    body: Vec<Vec<Vec<Span<'static>>>>,
    row: Vec<Vec<Span<'static>>>,
}

struct Render<'a> {
    theme: &'a Theme,
    width: usize,
    lines: Vec<Line<'static>>,
    spans: Vec<Span<'static>>,
    emphasis: Vec<Modifier>,
    heading: bool,
    quote: usize,
    lists: Vec<Level>,
    marker: Option<String>,
    fresh: bool,
    code: Option<(String, String)>,
    table: Option<Cells>,
    links: Vec<(usize, String)>,
    marks: Vec<(usize, usize, String)>,
}

impl<'a> Render<'a> {
    fn new(theme: &'a Theme, width: u16) -> Render<'a> {
        Render {
            theme,
            width: (width as usize).max(1),
            lines: Vec::new(),
            spans: Vec::new(),
            emphasis: Vec::new(),
            heading: false,
            quote: 0,
            lists: Vec::new(),
            marker: None,
            fresh: false,
            code: None,
            table: None,
            links: Vec::new(),
            marks: Vec::new(),
        }
    }

    fn run(mut self, text: &str) -> Vec<Line<'static>> {
        let options = Options::ENABLE_TABLES | Options::ENABLE_STRIKETHROUGH;
        for event in Parser::new_ext(text, options) {
            match event {
                Event::Start(tag) => self.start(tag),
                Event::End(tag) => self.end(tag),
                Event::Text(text) | Event::Html(text) | Event::InlineHtml(text) => self.text(&text),
                Event::Code(code) => {
                    let style = self.emphasized(self.theme.code);
                    self.spans.push(Span::styled(code.to_string(), style));
                }
                Event::SoftBreak => self.text(" "),
                Event::HardBreak => self.flush(),
                Event::Rule => {
                    self.flush();
                    self.gap();
                    let rule = Line::styled(RULE.repeat(self.width), self.theme.rule);
                    self.lines.push(rule);
                }
                _ => {}
            }
        }
        self.lines
    }

    fn start(&mut self, tag: Tag<'_>) {
        match tag {
            Tag::Paragraph => {
                self.flush();
                self.gap();
            }
            Tag::Heading { level, .. } => {
                self.flush();
                self.gap();
                self.heading = true;
                let hashes = format!("{} ", "#".repeat(level as usize));
                self.spans.push(Span::styled(hashes, self.theme.heading));
            }
            Tag::BlockQuote(_) => {
                self.flush();
                self.gap();
                self.quote += 1;
            }
            Tag::CodeBlock(kind) => {
                self.flush();
                self.gap();
                let lang = match kind {
                    CodeBlockKind::Fenced(info) => {
                        info.split_whitespace().next().unwrap_or("").to_string()
                    }
                    CodeBlockKind::Indented => String::new(),
                };
                self.code = Some((lang, String::new()));
            }
            Tag::List(first) => {
                self.flush();
                if self.lists.is_empty() {
                    self.gap();
                }
                self.lists.push(Level {
                    next: first,
                    marker: 0,
                });
            }
            Tag::Item => {
                let level = self.lists.last_mut().expect("list item outside a list");
                let marker = match level.next {
                    Some(number) => {
                        level.next = Some(number + 1);
                        format!("{number}. ")
                    }
                    None => BULLET.to_string(),
                };
                level.marker = wrap::width(&marker);
                self.marker = Some(marker);
                self.fresh = true;
            }
            Tag::Table(aligns) => {
                self.flush();
                self.gap();
                self.table = Some(Cells {
                    aligns,
                    head: Vec::new(),
                    body: Vec::new(),
                    row: Vec::new(),
                });
            }
            Tag::TableHead => self.heading = true,
            Tag::Emphasis => self.emphasis.push(Modifier::ITALIC),
            Tag::Strong => self.emphasis.push(Modifier::BOLD),
            Tag::Strikethrough => self.emphasis.push(Modifier::CROSSED_OUT),
            Tag::Link { dest_url, .. } | Tag::Image { dest_url, .. } => {
                self.links.push((self.spans.len(), dest_url.to_string()));
            }
            _ => {}
        }
    }

    fn end(&mut self, tag: TagEnd) {
        match tag {
            TagEnd::Paragraph => self.flush(),
            TagEnd::Heading(_) => {
                self.flush();
                self.heading = false;
            }
            TagEnd::BlockQuote(_) => {
                self.flush();
                self.quote -= 1;
            }
            TagEnd::CodeBlock => {
                let (lang, code) = self.code.take().expect("code block end without a start");
                self.draw_code(&lang, &code);
            }
            TagEnd::List(_) => {
                self.flush();
                self.lists.pop();
            }
            TagEnd::Item => {
                self.flush();
                self.fresh = false;
            }
            TagEnd::TableHead => {
                self.heading = false;
                let table = self.table.as_mut().expect("table head outside a table");
                table.head = std::mem::take(&mut table.row);
            }
            TagEnd::TableRow => {
                let table = self.table.as_mut().expect("table row outside a table");
                let row = std::mem::take(&mut table.row);
                table.body.push(row);
            }
            TagEnd::TableCell => {
                let cell = std::mem::take(&mut self.spans);
                let table = self.table.as_mut().expect("table cell outside a table");
                table.row.push(cell);
            }
            TagEnd::Table => {
                let table = self.table.take().expect("table end without a start");
                self.draw_table(table);
            }
            TagEnd::Emphasis | TagEnd::Strong | TagEnd::Strikethrough => {
                self.emphasis.pop();
            }
            TagEnd::Link | TagEnd::Image => self.close_link(),
            _ => {}
        }
    }

    fn text(&mut self, text: &str) {
        if let Some((_, code)) = self.code.as_mut() {
            code.push_str(text);
            return;
        }
        let style = self.inline();
        self.spans.push(Span::styled(text.to_string(), style));
    }

    /// A closed link becomes a mark over its label's spans; the label is all the member sees,
    /// and [`Render::flush`] wraps each rendered row's share of it in OSC 8 markers the client
    /// reads back on click. A table cell clips rather than wraps, so its markers embed directly
    /// and [`clip_spans`] carries them whole.
    fn close_link(&mut self) {
        let Some((at, dest)) = self.links.pop() else {
            return;
        };
        if dest.is_empty() || at >= self.spans.len() {
            return;
        }
        if self.table.is_some() {
            self.spans.insert(at, Span::raw(osc::link_open(&dest)));
            self.spans.push(Span::raw(osc::LINK_CLOSE));
            return;
        }
        self.marks.push((at, self.spans.len(), dest));
    }

    fn inline(&self) -> Style {
        let base = if !self.links.is_empty() {
            self.theme.link
        } else if self.heading {
            self.theme.heading
        } else if self.quote > 0 {
            self.theme.quote
        } else {
            self.theme.text
        };
        self.emphasized(base)
    }

    fn emphasized(&self, base: Style) -> Style {
        let mut style = base;
        for modifier in &self.emphasis {
            style = style.add_modifier(*modifier);
        }
        style
    }

    fn gap(&mut self) {
        if self.fresh {
            self.fresh = false;
            return;
        }
        if self.lines.last().is_some_and(|line| line.width() > 0) {
            self.lines.push(Line::raw(""));
        }
    }

    fn flush(&mut self) {
        if self.spans.is_empty() {
            return;
        }
        let (first, rest) = self.prefixes();
        let mut text = String::new();
        let mut runs = Vec::new();
        for span in self.spans.drain(..) {
            text.extend(
                span.content
                    .chars()
                    .map(|ch| if ch.is_whitespace() { ' ' } else { ch }),
            );
            runs.push((text.len(), span.style));
        }
        let mut links: Vec<(usize, usize, String)> = self
            .marks
            .drain(..)
            .rev()
            .map(|(at, to, url)| {
                let from = if at == 0 { 0 } else { runs[at - 1].0 };
                (from, runs[to - 1].0, url)
            })
            .collect();
        links.sort_by(|a, b| a.0.cmp(&b.0).then(b.1.cmp(&a.1)));
        let body = &text[..text.trim_end().len()];
        let mut prefix = first;
        let mut at = 0;
        loop {
            let cap = self.width.saturating_sub(span_width(&prefix)).max(1);
            let left = &body[at..];
            let (head, next) = wrap::wrap_head(left, cap);
            let row_end = at + head;
            let mut row = std::mem::take(&mut prefix);
            let mut cursor = at;
            for (from, to, url) in &links {
                let lo = (*from).max(cursor);
                let hi = (*to).min(row_end);
                if lo >= hi {
                    continue;
                }
                row.extend(styled_slice(body, &runs, cursor, lo));
                row.push(Span::raw(osc::link_open(url)));
                row.extend(styled_slice(body, &runs, lo, hi));
                row.push(Span::raw(osc::LINK_CLOSE));
                cursor = hi;
            }
            row.extend(styled_slice(body, &runs, cursor, row_end));
            self.lines.push(Line::from(row));
            at += next;
            if at >= body.len() {
                return;
            }
            prefix = rest.clone();
        }
    }

    fn prefixes(&mut self) -> (Vec<Span<'static>>, Vec<Span<'static>>) {
        let mut first = Vec::new();
        let mut rest = Vec::new();
        for _ in 0..self.quote {
            first.push(Span::styled(QUOTE_BAR, self.theme.quote));
            rest.push(Span::styled(QUOTE_BAR, self.theme.quote));
        }
        let Some(level) = self.lists.last() else {
            return (first, rest);
        };
        let marker_width = level.marker;
        let pad = LIST_INDENT * (self.lists.len() - 1);
        if pad > 0 {
            first.push(Span::raw(" ".repeat(pad)));
            rest.push(Span::raw(" ".repeat(pad)));
        }
        match self.marker.take() {
            Some(marker) => first.push(Span::styled(marker, self.theme.list_bullet)),
            None => first.push(Span::raw(" ".repeat(marker_width))),
        }
        rest.push(Span::raw(" ".repeat(marker_width)));
        (first, rest)
    }

    fn draw_code(&mut self, lang: &str, code: &str) {
        let cap = self.width.saturating_sub(wrap::width(CODE_INDENT));
        if self.theme.mode == ColorMode::Plain {
            for raw in code.split_inclusive('\n') {
                let text = wrap::clip(raw.trim_end_matches(['\r', '\n']), cap);
                let line = Line::styled(format!("{CODE_INDENT}{text}"), self.theme.code_block);
                self.lines.push(line);
            }
            return;
        }
        let (syntaxes, inks) = assets();
        let syntax = syntaxes
            .find_syntax_by_token(lang)
            .unwrap_or_else(|| syntaxes.find_syntax_plain_text());
        let name = match self.theme.scheme {
            Scheme::Dark => INK_DARK,
            Scheme::Light => INK_LIGHT,
        };
        let ink = inks.themes.get(name).expect("syntect default theme");
        let mut lit = HighlightLines::new(syntax, ink);
        for raw in code.split_inclusive('\n') {
            let mut row = vec![Span::styled(CODE_INDENT, self.theme.code_block)];
            match lit.highlight_line(raw, syntaxes) {
                Ok(pieces) => {
                    let mut used = 0;
                    for (style, piece) in pieces {
                        let text = wrap::clip(
                            piece.trim_end_matches(['\r', '\n']),
                            cap.saturating_sub(used),
                        );
                        if text.is_empty() {
                            continue;
                        }
                        used += wrap::width(text);
                        row.push(Span::styled(text.to_string(), self.fg(style.foreground)));
                    }
                }
                Err(_) => row.push(Span::styled(
                    wrap::clip(raw.trim_end_matches(['\r', '\n']), cap).to_string(),
                    self.theme.code_block,
                )),
            }
            self.lines.push(Line::from(row));
        }
    }

    fn fg(&self, color: InkColor) -> Style {
        match self.theme.mode {
            ColorMode::TrueColor => Style::new().fg(Color::Rgb(color.r, color.g, color.b)),
            ColorMode::Ansi256 => {
                Style::new().fg(Color::Indexed(rgb_to_256(color.r, color.g, color.b)))
            }
            ColorMode::Plain => self.theme.code_block,
        }
    }

    fn draw_table(&mut self, table: Cells) {
        let columns = table
            .head
            .len()
            .max(table.body.iter().map(Vec::len).max().unwrap_or(0));
        if columns == 0 {
            return;
        }
        let mut widths = vec![0; columns];
        for row in std::iter::once(&table.head).chain(table.body.iter()) {
            for (index, cell) in row.iter().enumerate() {
                widths[index] = widths[index].max(span_width(cell));
            }
        }
        while 1 + widths.iter().map(|width| width + CELL_FRAME).sum::<usize>() > self.width {
            let widest = widths
                .iter_mut()
                .max_by_key(|width| **width)
                .expect("columns");
            if *widest <= 1 {
                break;
            }
            *widest -= 1;
        }
        let top = self.border(&widths, TOP);
        self.lines.push(top);
        let head = self.cells(&table.head, &widths, &table.aligns);
        self.lines.push(head);
        let spine = self.border(&widths, SPINE);
        self.lines.push(spine);
        for row in &table.body {
            let row = self.cells(row, &widths, &table.aligns);
            self.lines.push(row);
        }
        let foot = self.border(&widths, FOOT);
        self.lines.push(foot);
    }

    fn border(&self, widths: &[usize], glyphs: [&str; 3]) -> Line<'static> {
        let bars: Vec<String> = widths.iter().map(|width| RULE.repeat(width + 2)).collect();
        let text = format!("{}{}{}", glyphs[0], bars.join(glyphs[1]), glyphs[2]);
        Line::styled(wrap::clip(&text, self.width).to_string(), self.theme.rule)
    }

    fn cells(
        &self,
        row: &[Vec<Span<'static>>],
        widths: &[usize],
        aligns: &[Alignment],
    ) -> Line<'static> {
        let mut spans = vec![Span::styled(BAR, self.theme.rule)];
        for (index, width) in widths.iter().enumerate() {
            let cell = row.get(index).map(Vec::as_slice).unwrap_or(&[]);
            let text = clip_spans(cell, *width);
            let pad = width - span_width(&text);
            let (lead, trail) = match aligns.get(index) {
                Some(Alignment::Right) => (pad, 0),
                Some(Alignment::Center) => (pad / 2, pad - pad / 2),
                _ => (0, pad),
            };
            spans.push(Span::raw(" ".repeat(lead + 1)));
            spans.extend(text);
            spans.push(Span::raw(" ".repeat(trail + 1)));
            spans.push(Span::styled(BAR, self.theme.rule));
        }
        Line::from(clip_spans(&spans, self.width))
    }
}

fn assets() -> &'static (SyntaxSet, ThemeSet) {
    static ASSETS: OnceLock<(SyntaxSet, ThemeSet)> = OnceLock::new();
    ASSETS.get_or_init(|| {
        (
            SyntaxSet::load_defaults_newlines(),
            ThemeSet::load_defaults(),
        )
    })
}

fn span_width(spans: &[Span<'static>]) -> usize {
    spans
        .iter()
        .flat_map(|span| wrap::units(&span.content))
        .map(|(_, step)| step)
        .sum()
}

/// The prefix of `spans` that fits `max` display columns. OSC markers take no columns and are
/// all kept, clipped or not, so a link's open always meets its close.
fn clip_spans(spans: &[Span<'static>], max: usize) -> Vec<Span<'static>> {
    let mut out = Vec::new();
    let mut used = 0;
    let mut full = false;
    for span in spans {
        let mut kept = String::new();
        for (unit, step) in wrap::units(&span.content) {
            if step == 0 {
                kept.push_str(unit);
                continue;
            }
            if full || used + step > max {
                full = true;
                continue;
            }
            used += step;
            kept.push_str(unit);
        }
        if !kept.is_empty() {
            out.push(Span::styled(kept, span.style));
        }
    }
    out
}

fn styled_slice(text: &str, runs: &[(usize, Style)], from: usize, to: usize) -> Vec<Span<'static>> {
    let mut out = Vec::new();
    let mut start = 0;
    for (end, style) in runs {
        let lo = start.max(from);
        let hi = (*end).min(to);
        if lo < hi {
            out.push(Span::styled(text[lo..hi].to_string(), *style));
        }
        start = *end;
        if start >= to {
            break;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ui::theme::{ColorMode, Scheme, Theme};

    fn plain() -> Theme {
        Theme::for_mode(ColorMode::Plain, Scheme::Dark)
    }

    fn lit() -> Theme {
        Theme::for_mode(ColorMode::TrueColor, Scheme::Dark)
    }

    #[test]
    fn heading_carries_the_heading_style() {
        let lines = render("# Title", &plain(), 40);
        assert_eq!(lines.len(), 1);
        assert_eq!(lines[0].to_string(), "# Title");
        assert!(lines[0]
            .spans
            .iter()
            .all(|span| span.style.add_modifier.contains(Modifier::BOLD)));
    }

    #[test]
    fn prose_wraps_at_the_given_width() {
        let lines = render(
            "alpha beta gamma delta epsilon zeta eta theta",
            &plain(),
            20,
        );
        assert!(lines.len() > 2);
        assert!(lines.iter().all(|line| line.width() <= 20));
        let whole: Vec<String> = lines.iter().map(Line::to_string).collect();
        assert_eq!(
            whole.join(" "),
            "alpha beta gamma delta epsilon zeta eta theta"
        );
    }

    #[test]
    fn emphasis_carries_its_modifier() {
        let lines = render("plain **bold** and ~~gone~~", &plain(), 40);
        let bold = lines[0]
            .spans
            .iter()
            .find(|span| span.content.as_ref() == "bold")
            .expect("the bold run");
        assert!(bold.style.add_modifier.contains(Modifier::BOLD));
        let gone = lines[0]
            .spans
            .iter()
            .find(|span| span.content.as_ref() == "gone")
            .expect("the struck run");
        assert!(gone.style.add_modifier.contains(Modifier::CROSSED_OUT));
    }

    #[test]
    fn fenced_code_is_colored_in_truecolor() {
        let lines = render("```rust\nfn main() {}\n```", &lit(), 40);
        assert_eq!(lines.len(), 1);
        assert_eq!(lines[0].to_string(), "  fn main() {}");
        assert!(lines[0]
            .spans
            .iter()
            .any(|span| matches!(span.style.fg, Some(Color::Rgb(..)))));
    }

    #[test]
    fn fenced_code_takes_no_color_in_plain_mode() {
        let lines = render("```rust\nfn main() {}\n```", &plain(), 40);
        assert_eq!(lines.len(), 1);
        assert_eq!(lines[0].to_string(), "  fn main() {}");
        assert!(lines
            .iter()
            .all(|line| line.spans.iter().all(|span| span.style.fg.is_none())));
    }

    #[test]
    fn code_is_clipped_never_wrapped() {
        let lines = render("```\nlet answer = 12345678901234567890;\n```", &plain(), 12);
        assert_eq!(lines.len(), 1);
        assert_eq!(lines[0].to_string(), "  let answer");
    }

    #[test]
    fn fenced_code_takes_cube_indices_in_ansi256() {
        let theme = Theme::for_mode(ColorMode::Ansi256, Scheme::Dark);
        let lines = render("```rust\nfn main() {}\n```", &theme, 40);
        assert!(lines[0]
            .spans
            .iter()
            .any(|span| matches!(span.style.fg, Some(Color::Indexed(index)) if index >= 16)));
    }

    #[test]
    fn list_items_take_the_bullet_and_nest() {
        let lines = render("- one\n  - two\n- three", &plain(), 40);
        assert_eq!(lines.len(), 3);
        assert_eq!(lines[0].to_string(), "• one");
        assert_eq!(lines[1].to_string(), "  • two");
        assert_eq!(lines[2].to_string(), "• three");
    }

    #[test]
    fn ordered_items_number_and_hang_their_wrap() {
        let lines = render("1. alpha beta gamma\n2. delta", &plain(), 14);
        assert_eq!(lines.len(), 3);
        assert_eq!(lines[0].to_string(), "1. alpha beta");
        assert_eq!(lines[1].to_string(), "   gamma");
        assert_eq!(lines[2].to_string(), "2. delta");
    }

    #[test]
    fn quote_takes_the_bar_on_every_wrapped_row() {
        let lines = render("> alpha beta gamma delta", &plain(), 14);
        assert!(lines.len() > 1);
        assert!(lines
            .iter()
            .all(|line| line.to_string().starts_with(QUOTE_BAR)));
    }

    #[test]
    fn rule_fills_the_width() {
        let lines = render("---", &plain(), 12);
        assert_eq!(lines[0].to_string(), RULE.repeat(12));
    }

    #[test]
    fn a_link_renders_its_label_alone() {
        let lines = render("see [docs](https://ufo.test)", &plain(), 60);
        assert_eq!(
            lines[0].to_string(),
            "see \u{1b}]8;;https://ufo.test\u{7}docs\u{1b}]8;;\u{7}"
        );
    }

    #[test]
    fn a_wrapped_link_reopens_on_every_row() {
        let lines = render("[alpha beta](https://u.fo)", &plain(), 5);
        assert_eq!(lines.len(), 2);
        for (line, label) in lines.iter().zip(["alpha", "beta"]) {
            assert_eq!(
                line.to_string(),
                format!("\u{1b}]8;;https://u.fo\u{7}{label}\u{1b}]8;;\u{7}")
            );
        }
    }

    #[test]
    fn a_table_link_clicks_by_its_label() {
        let table = "| Doc |\n| --- |\n| [x](https://u.fo) |\n";
        let lines = render(table, &plain(), 24);
        assert!(lines[3]
            .to_string()
            .contains("\u{1b}]8;;https://u.fo\u{7}x\u{1b}]8;;\u{7}"));
        let visible_widths: Vec<usize> = lines
            .iter()
            .map(|line| {
                line.spans
                    .iter()
                    .flat_map(|span| wrap::units(&span.content))
                    .map(|(_, step)| step)
                    .sum()
            })
            .collect();
        assert!(visible_widths
            .iter()
            .all(|width| *width == visible_widths[0]));
    }

    #[test]
    fn a_clipped_table_cell_keeps_its_markers_balanced() {
        let table = "| Doc |\n| --- |\n| [alpha beta gamma](https://u.fo) |\n";
        let lines = render(table, &plain(), 10);
        for line in &lines {
            let text = line.to_string();
            assert_eq!(
                text.matches('\u{1b}').count(),
                text.matches('\u{7}').count(),
                "markers torn: {text:?}"
            );
        }
    }

    #[test]
    fn blocks_are_parted_by_one_blank_line() {
        let lines = render("one\n\ntwo", &plain(), 40);
        assert_eq!(lines.len(), 3);
        assert_eq!(lines[1].width(), 0);
    }

    #[test]
    fn table_draws_box_rules_inside_the_width() {
        let table = "| Name | Size |\n| --- | ---: |\n| one | 12 |\n";
        let lines = render(table, &plain(), 24);
        assert_eq!(lines.len(), 5);
        assert!(lines.iter().all(|line| line.width() <= 24));
        assert_eq!(lines[0].to_string(), "┌──────┬──────┐");
        assert_eq!(lines[1].to_string(), "│ Name │ Size │");
        assert_eq!(lines[2].to_string(), "├──────┼──────┤");
        assert_eq!(lines[3].to_string(), "│ one  │   12 │");
        assert_eq!(lines[4].to_string(), "└──────┴──────┘");
    }

    #[test]
    fn table_clips_to_a_narrow_width() {
        let table = "| Name | Size |\n| --- | --- |\n| one | 12 |\n";
        let lines = render(table, &plain(), 13);
        assert!(lines.iter().all(|line| line.width() <= 13));
        assert!(lines[1].to_string().starts_with('│'));
    }

    #[test]
    fn stream_holds_an_open_fence_until_it_closes() {
        let mut stream = StreamRenderer::new();
        assert!(stream.push("```rust\n").is_empty());
        assert!(stream.push("fn main() {}\n").is_empty());
        assert_eq!(stream.open_tail(), "```rust\nfn main() {}\n");
        assert_eq!(stream.push("```\n"), "```rust\nfn main() {}\n```\n");
        assert_eq!(stream.open_tail(), "");
    }

    #[test]
    fn stream_holds_a_table_until_a_blank_line_ends_it() {
        let mut stream = StreamRenderer::new();
        assert!(stream.push("| a | b |\n| - | - |\n").is_empty());
        assert!(stream.push("| 1 | 2 |\n").is_empty());
        assert_eq!(
            stream.push("\nafter"),
            "| a | b |\n| - | - |\n| 1 | 2 |\n\n"
        );
        assert_eq!(stream.open_tail(), "after");
    }

    #[test]
    fn stream_commits_a_paragraph_on_its_blank_line() {
        let mut stream = StreamRenderer::new();
        assert!(stream.push("one two\nthree\n").is_empty());
        assert_eq!(stream.push("\nnext"), "one two\nthree\n\n");
        assert_eq!(stream.open_tail(), "next");
        assert_eq!(stream.finish(), "next");
        assert_eq!(stream.open_tail(), "");
    }

    #[test]
    fn streamed_deltas_commit_the_source_losslessly() {
        let source = "# Title\n\nA paragraph long enough that it has to wrap somewhere.\n\n\
             - one\n- two\n  - nested\n\n1. first\n2. second\n\n\
             ```rust\nfn main() {}\n```\n\n\
             | a | b |\n| - | - |\n| 1 | 2 |\n\n> quoted\n\n---\n\nlast word\n";
        let mut stream = StreamRenderer::new();
        let mut committed = String::new();
        let mut delta = String::new();
        for ch in source.chars() {
            delta.push(ch);
            if delta.chars().count() == 5 {
                committed.push_str(&stream.push(&delta));
                delta.clear();
            }
        }
        committed.push_str(&stream.push(&delta));
        committed.push_str(&stream.finish());
        assert_eq!(committed, source);
    }

    #[test]
    fn stream_holds_a_list_until_a_block_follows_it() {
        let mut stream = StreamRenderer::new();
        assert!(stream.push("1. one\n").is_empty());
        assert!(stream.push("2. two\n\n").is_empty());
        assert_eq!(stream.push("after"), "1. one\n2. two\n\n");
        assert_eq!(stream.open_tail(), "after");
    }
}

#[cfg(test)]
mod linked_image_tests {
    use super::*;
    use crate::ui::theme::{ColorMode, Scheme, Theme};

    #[test]
    fn a_linked_image_renders_without_panicking() {
        let theme = Theme::for_mode(ColorMode::Plain, Scheme::Dark);
        let lines = render(
            "[![build](https://img.example/badge.svg)](https://ci.example)",
            &theme,
            120,
        );
        let text: String = lines
            .iter()
            .flat_map(|line| line.spans.iter())
            .map(|span| span.content.as_ref())
            .collect();
        assert!(text.contains("build"), "alt text renders: {text}");
        assert!(
            text.contains("\u{1b}]8;;https://ci.example\u{7}"),
            "outer link carries the click target: {text}"
        );
    }

    #[test]
    fn an_unbalanced_link_end_is_ignored() {
        let theme = Theme::for_mode(ColorMode::Plain, Scheme::Dark);
        let lines = render("plain ![img](u) text [a](b)", &theme, 120);
        assert!(!lines.is_empty());
    }
}
