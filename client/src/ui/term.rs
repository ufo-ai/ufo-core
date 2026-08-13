//! The dock terminal: finalized transcript lines scroll natively above a repainted dynamic
//! region, every frame wrapped in one synchronized-output write. Styled lines arrive as ratatui
//! `Line`s and leave as ANSI; the dock repaints only rows whose rendering changed.

use std::io::Write;

use ratatui::style::{Color, Modifier, Style};
use ratatui::text::Line;

use crate::ui::theme::ColorMode;

const SYNC_BEGIN: &str = "\x1b[?2026h";
const SYNC_END: &str = "\x1b[?2026l";
const HIDE_CURSOR: &str = "\x1b[?25l";
const SHOW_CURSOR: &str = "\x1b[?25h";
const CLEAR_LINE: &str = "\x1b[2K";
const CLEAR_BELOW: &str = "\x1b[J";
const RESET: &str = "\x1b[0m";

/// Render one styled line to ANSI, the line's own style under each span's. Plain mode emits
/// the text alone — no SGR at all.
pub fn render_line(line: &Line, mode: ColorMode) -> String {
    let mut out = String::new();
    for span in &line.spans {
        if span.content.is_empty() {
            continue;
        }
        let sgr = sgr_for(line.style.patch(span.style), mode);
        if sgr.is_empty() {
            out.push_str(&span.content);
        } else {
            out.push_str(&sgr);
            out.push_str(&span.content);
            out.push_str(RESET);
        }
    }
    out
}

fn sgr_for(style: Style, mode: ColorMode) -> String {
    if mode == ColorMode::Plain {
        return String::new();
    }
    let mut codes: Vec<String> = Vec::new();
    let modifiers = style.add_modifier;
    for (flag, code) in [
        (Modifier::BOLD, "1"),
        (Modifier::DIM, "2"),
        (Modifier::ITALIC, "3"),
        (Modifier::UNDERLINED, "4"),
        (Modifier::REVERSED, "7"),
        (Modifier::CROSSED_OUT, "9"),
    ] {
        if modifiers.contains(flag) {
            codes.push(code.to_string());
        }
    }
    match style.fg {
        Some(Color::Rgb(r, g, b)) => codes.push(format!("38;2;{r};{g};{b}")),
        Some(Color::Indexed(index)) => codes.push(format!("38;5;{index}")),
        _ => {}
    }
    match style.bg {
        Some(Color::Rgb(r, g, b)) => codes.push(format!("48;2;{r};{g};{b}")),
        Some(Color::Indexed(index)) => codes.push(format!("48;5;{index}")),
        _ => {}
    }
    if codes.is_empty() {
        return String::new();
    }
    format!("\x1b[{}m", codes.join(";"))
}

/// The hardware cursor's place inside the dock, rows from the dock's top row.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DockCursor {
    pub row: u16,
    pub col: u16,
}

/// One dynamic region under the transcript. Between frames the terminal cursor rests on a known
/// dock row — the last row after a plain paint, the composer row while the hardware cursor shows —
/// and every frame begins by walking up from that row to the region's top, so terminal scrolling
/// between frames stays correct where absolute positions would drift.
pub struct DockTerm<W: Write> {
    out: W,
    mode: ColorMode,
    rows_painted: u16,
    parked_row: u16,
    prev: Vec<String>,
    cursor_shown: bool,
}

impl<W: Write> DockTerm<W> {
    pub fn new(out: W, mode: ColorMode) -> DockTerm<W> {
        DockTerm {
            out,
            mode,
            rows_painted: 0,
            parked_row: 0,
            prev: Vec::new(),
            cursor_shown: false,
        }
    }

    /// Paint one frame: transcript lines (if any) flow into scrollback above the dock, the dock
    /// repaints beneath them, and the hardware cursor lands at `cursor` (hidden when None). One
    /// buffered write, wrapped in synchronized output.
    pub fn frame(
        &mut self,
        transcript: &[Line],
        dock: &[Line],
        cursor: Option<DockCursor>,
    ) -> std::io::Result<()> {
        let rendered: Vec<String> = dock
            .iter()
            .map(|line| render_line(line, self.mode))
            .collect();
        let mut buffer = String::from(SYNC_BEGIN);
        if self.cursor_shown {
            buffer.push_str(HIDE_CURSOR);
        }
        self.walk_to_top(&mut buffer);
        if transcript.is_empty() && rendered.len() == self.prev.len() && !self.prev.is_empty() {
            self.paint_changed_rows(&mut buffer, &rendered);
        } else {
            if !transcript.is_empty() {
                buffer.push_str(CLEAR_BELOW);
                for line in transcript {
                    buffer.push_str(&render_line(line, self.mode));
                    buffer.push_str("\r\n");
                }
            }
            self.paint_all_rows(&mut buffer, &rendered);
        }
        self.prev = rendered;
        self.rows_painted = self.prev.len() as u16;
        self.parked_row = self.rows_painted.saturating_sub(1);
        self.place_cursor(&mut buffer, cursor);
        buffer.push_str(SYNC_END);
        self.out.write_all(buffer.as_bytes())?;
        self.out.flush()
    }

    /// Erase the dock and leave the cursor at its former top row.
    pub fn erase(&mut self) -> std::io::Result<()> {
        if self.rows_painted == 0 {
            return Ok(());
        }
        let mut buffer = String::new();
        self.walk_to_top(&mut buffer);
        buffer.push_str(CLEAR_BELOW);
        self.rows_painted = 0;
        self.parked_row = 0;
        self.prev.clear();
        self.out.write_all(buffer.as_bytes())?;
        self.out.flush()
    }

    /// Restore the cursor on the way out.
    pub fn close(&mut self) -> std::io::Result<()> {
        self.erase()?;
        self.out.write_all(SHOW_CURSOR.as_bytes())?;
        self.out.flush()
    }

    fn walk_to_top(&self, buffer: &mut String) {
        if self.parked_row > 0 {
            buffer.push_str(&format!("\r\x1b[{}A", self.parked_row));
        } else {
            buffer.push('\r');
        }
    }

    fn paint_all_rows(&self, buffer: &mut String, rendered: &[String]) {
        for (index, row) in rendered.iter().enumerate() {
            if index > 0 {
                buffer.push_str("\r\n");
            }
            buffer.push_str(CLEAR_LINE);
            buffer.push_str(row);
        }
        buffer.push_str("\r\n");
        buffer.push_str(CLEAR_BELOW);
        buffer.push_str("\x1b[1A\r");
    }

    fn paint_changed_rows(&self, buffer: &mut String, rendered: &[String]) {
        let mut at = 0usize;
        for (index, (row, previous)) in rendered.iter().zip(&self.prev).enumerate() {
            if row == previous {
                continue;
            }
            if index > at {
                buffer.push_str(&format!("\x1b[{}B", index - at));
            }
            at = index;
            buffer.push('\r');
            buffer.push_str(CLEAR_LINE);
            buffer.push_str(row);
            buffer.push('\r');
        }
        let last = rendered.len().saturating_sub(1);
        if last > at {
            buffer.push_str(&format!("\x1b[{}B", last - at));
        }
        buffer.push('\r');
    }

    fn place_cursor(&mut self, buffer: &mut String, cursor: Option<DockCursor>) {
        let Some(cursor) = cursor else {
            self.cursor_shown = false;
            return;
        };
        let up = self.parked_row.saturating_sub(cursor.row);
        if up > 0 {
            buffer.push_str(&format!("\x1b[{up}A"));
        }
        self.parked_row = cursor.row.min(self.parked_row);
        buffer.push_str(&format!("\x1b[{}G", cursor.col + 1));
        buffer.push_str(SHOW_CURSOR);
        self.cursor_shown = true;
    }
}

const ALT_ENTER: &str = "\x1b[?1049h\x1b[2J\x1b[H\x1b[?25l";
const ALT_LEAVE: &str = "\x1b[?1049l\x1b[?25h";

/// The whole terminal, owned: every frame paints exactly the screen's rows, diffing against the
/// previous frame so an unchanged row costs nothing. Rows position absolutely, so nothing the
/// dock does can shift the transcript and a resize is one clean repaint.
pub struct AltScreen<W: Write> {
    out: W,
    mode: ColorMode,
    prev: Vec<String>,
    cursor_shown: bool,
}

impl<W: Write> AltScreen<W> {
    pub fn new(out: W, mode: ColorMode) -> AltScreen<W> {
        AltScreen {
            out,
            mode,
            prev: Vec::new(),
            cursor_shown: false,
        }
    }

    pub fn enter(&mut self) -> std::io::Result<()> {
        self.prev.clear();
        self.out.write_all(ALT_ENTER.as_bytes())?;
        self.out.flush()
    }

    /// Back to the main screen; the caller prints the exit document there.
    pub fn leave(&mut self) -> std::io::Result<()> {
        self.prev.clear();
        self.out.write_all(ALT_LEAVE.as_bytes())?;
        self.out.flush()
    }

    /// Repaint after a size change: every row is unknown again.
    pub fn invalidate(&mut self) {
        self.prev.clear();
    }

    /// Paint one frame of exactly the screen's rows; `cursor` is (row, col) from the top left.
    pub fn frame(&mut self, rows: &[Line], cursor: Option<(u16, u16)>) -> std::io::Result<()> {
        let rendered: Vec<String> = rows
            .iter()
            .map(|line| render_line(line, self.mode))
            .collect();
        let mut buffer = String::from(SYNC_BEGIN);
        if self.cursor_shown {
            buffer.push_str(HIDE_CURSOR);
        }
        for (index, row) in rendered.iter().enumerate() {
            if self.prev.get(index) == Some(row) {
                continue;
            }
            buffer.push_str(&format!("\x1b[{};1H", index + 1));
            buffer.push_str(CLEAR_LINE);
            buffer.push_str(row);
        }
        if rendered.len() < self.prev.len() {
            for index in rendered.len()..self.prev.len() {
                buffer.push_str(&format!("\x1b[{};1H", index + 1));
                buffer.push_str(CLEAR_LINE);
            }
        }
        self.prev = rendered;
        match cursor {
            Some((row, col)) => {
                buffer.push_str(&format!("\x1b[{};{}H", row + 1, col + 1));
                buffer.push_str(SHOW_CURSOR);
                self.cursor_shown = true;
            }
            None => {
                self.cursor_shown = false;
            }
        }
        buffer.push_str(SYNC_END);
        self.out.write_all(buffer.as_bytes())?;
        self.out.flush()
    }

    /// Print retained lines onto the main screen, after `leave` — the conversation outlives the
    /// session in the terminal's own scrollback.
    pub fn print_document(&mut self, lines: &[Line]) -> std::io::Result<()> {
        let mut buffer = String::new();
        for line in lines {
            buffer.push_str(&render_line(line, self.mode));
            buffer.push_str("\r\n");
        }
        self.out.write_all(buffer.as_bytes())?;
        self.out.flush()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ratatui::text::Span;

    fn screen(bytes: &[u8], rows: u16, cols: u16, seed_row: u16) -> vt100::Parser {
        let mut parser = vt100::Parser::new(rows, cols, 0);
        parser.process(format!("\x1b[{};1H", seed_row + 1).as_bytes());
        parser.process(bytes);
        parser
    }

    fn styled(text: &str, style: Style) -> Line<'static> {
        Line::from(Span::styled(text.to_string(), style))
    }

    #[test]
    fn line_level_styles_reach_the_screen() {
        let line = Line::styled("rule", Style::new().fg(Color::Rgb(9, 8, 7)));
        assert!(render_line(&line, ColorMode::TrueColor).contains("38;2;9;8;7"));
    }

    #[test]
    fn plain_mode_emits_no_sgr() {
        let line = styled(
            "hello",
            Style::new()
                .fg(Color::Rgb(1, 2, 3))
                .add_modifier(Modifier::BOLD),
        );
        assert_eq!(render_line(&line, ColorMode::Plain), "hello");
        assert!(render_line(&line, ColorMode::TrueColor).contains("38;2;1;2;3"));
    }

    #[test]
    fn transcript_flows_above_the_dock() {
        let mut bytes = Vec::new();
        {
            let mut dock = DockTerm::new(&mut bytes, ColorMode::Plain);
            dock.frame(&[], &[Line::raw("rule"), Line::raw("entry")], None)
                .unwrap();
            dock.frame(
                &[Line::raw("first answer")],
                &[Line::raw("rule"), Line::raw("entry")],
                None,
            )
            .unwrap();
        }
        let parser = screen(&bytes, 12, 40, 0);
        let content = parser.screen().contents();
        let first = content
            .lines()
            .position(|line| line.starts_with("first answer"));
        let dock_rule = content.lines().position(|line| line.starts_with("rule"));
        assert!(first.is_some());
        assert!(dock_rule > first, "dock repaints under the transcript");
    }

    #[test]
    fn dock_only_updates_leave_transcript_untouched() {
        let mut bytes = Vec::new();
        {
            let mut dock = DockTerm::new(&mut bytes, ColorMode::Plain);
            dock.frame(
                &[Line::raw("kept")],
                &[Line::raw("a"), Line::raw("b")],
                None,
            )
            .unwrap();
            dock.frame(&[], &[Line::raw("a"), Line::raw("changed")], None)
                .unwrap();
        }
        let parser = screen(&bytes, 12, 40, 0);
        let content = parser.screen().contents();
        assert!(content.contains("kept"));
        assert!(content.contains("changed"));
        assert!(!content.contains("\nb"));
    }

    #[test]
    fn cursor_lands_inside_the_dock() {
        let mut bytes = Vec::new();
        {
            let mut dock = DockTerm::new(&mut bytes, ColorMode::Plain);
            dock.frame(
                &[],
                &[Line::raw("rule"), Line::raw("> hi"), Line::raw("rule")],
                Some(DockCursor { row: 1, col: 4 }),
            )
            .unwrap();
        }
        let parser = screen(&bytes, 12, 40, 0);
        assert_eq!(parser.screen().cursor_position(), (1, 4));
        assert!(!parser.screen().hide_cursor());
    }

    #[test]
    fn repeated_cursor_frames_do_not_drift() {
        let mut bytes = Vec::new();
        {
            let mut dock = DockTerm::new(&mut bytes, ColorMode::Plain);
            for (typed, text) in ["f", "fo", "foo"].iter().enumerate() {
                dock.frame(
                    &[],
                    &[
                        Line::raw("act"),
                        Line::raw(format!("> {text}")),
                        Line::raw("foot"),
                    ],
                    Some(DockCursor {
                        row: 1,
                        col: 3 + typed as u16,
                    }),
                )
                .unwrap();
            }
        }
        let parser = screen(&bytes, 24, 40, 10);
        let content = parser.screen().contents();
        assert_eq!(content.matches("act").count(), 1, "screen: {content}");
        assert_eq!(content.matches("foot").count(), 1, "screen: {content}");
        assert!(content.contains("> foo"));
        assert_eq!(parser.screen().cursor_position(), (11, 5));
    }

    #[test]
    fn transcript_after_a_cursor_frame_lands_at_the_dock_top() {
        let mut bytes = Vec::new();
        {
            let mut dock = DockTerm::new(&mut bytes, ColorMode::Plain);
            let rows = [Line::raw("act"), Line::raw("> hi"), Line::raw("foot")];
            dock.frame(&[], &rows, Some(DockCursor { row: 1, col: 4 }))
                .unwrap();
            dock.frame(
                &[Line::raw("the reply")],
                &rows,
                Some(DockCursor { row: 1, col: 4 }),
            )
            .unwrap();
        }
        let parser = screen(&bytes, 24, 40, 10);
        let content = parser.screen().contents();
        let lines: Vec<&str> = content.lines().collect();
        assert_eq!(lines[10], "the reply", "screen: {content}");
        assert_eq!(lines[11], "act");
        assert_eq!(content.matches("act").count(), 1);
        assert_eq!(parser.screen().cursor_position(), (12, 4));
    }

    #[test]
    fn erase_then_repaint_leaves_no_trail_after_a_width_change() {
        let mut bytes = Vec::new();
        {
            let mut dock = DockTerm::new(&mut bytes, ColorMode::Plain);
            let rows = [Line::raw("act"), Line::raw("> hi"), Line::raw("foot")];
            dock.frame(&[], &rows, Some(DockCursor { row: 1, col: 4 }))
                .unwrap();
            dock.erase().unwrap();
            for _ in 0..4 {
                dock.frame(&[], &rows, Some(DockCursor { row: 1, col: 4 }))
                    .unwrap();
            }
        }
        let parser = screen(&bytes, 24, 40, 10);
        let content = parser.screen().contents();
        assert!(
            content.matches("foot").count() <= 2,
            "one ghost at most, never a trail: {content}"
        );
        assert_eq!(parser.screen().cursor_position().1, 4);
    }

    #[test]
    fn alt_screen_paints_and_diffs_rows() {
        let mut bytes = Vec::new();
        {
            let mut alt = AltScreen::new(&mut bytes, ColorMode::Plain);
            alt.enter().unwrap();
            alt.frame(
                &[Line::raw("top"), Line::raw("> hi"), Line::raw("foot")],
                Some((1, 4)),
            )
            .unwrap();
            alt.frame(
                &[Line::raw("top"), Line::raw("> hiy"), Line::raw("foot")],
                Some((1, 5)),
            )
            .unwrap();
        }
        let text = String::from_utf8_lossy(&bytes);
        let mut parser = vt100::Parser::new(10, 40, 0);
        parser.process(&bytes);
        let screen = parser.screen();
        assert!(screen.alternate_screen());
        assert_eq!(screen.cursor_position(), (1, 5));
        let content = screen.contents();
        assert!(content.contains("> hiy"));
        assert_eq!(text.matches("top").count(), 1, "unchanged rows repaint");
    }

    #[test]
    fn alt_screen_leave_restores_and_prints_the_document() {
        let mut bytes = Vec::new();
        {
            let mut alt = AltScreen::new(&mut bytes, ColorMode::Plain);
            alt.enter().unwrap();
            alt.frame(&[Line::raw("ephemeral")], None).unwrap();
            alt.leave().unwrap();
            alt.print_document(&[Line::raw("kept one"), Line::raw("kept two")])
                .unwrap();
        }
        let mut parser = vt100::Parser::new(10, 40, 0);
        parser.process(&bytes);
        let screen = parser.screen();
        assert!(!screen.alternate_screen());
        let content = screen.contents();
        assert!(content.contains("kept one"));
        assert!(content.contains("kept two"));
        assert!(!content.contains("ephemeral"));
    }

    #[test]
    fn erase_clears_the_region() {
        let mut bytes = Vec::new();
        {
            let mut dock = DockTerm::new(&mut bytes, ColorMode::Plain);
            dock.frame(&[], &[Line::raw("visible")], None).unwrap();
            dock.erase().unwrap();
        }
        let parser = screen(&bytes, 12, 40, 0);
        assert!(!parser.screen().contents().contains("visible"));
    }
}
