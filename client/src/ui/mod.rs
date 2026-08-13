//! Terminal rendering: finalized transcript lines scroll natively; a repainted dynamic region
//! sits at the bottom — [activity row] [rule] [entry rows, craft at the right] [rule].

mod editor;
mod wrap;

use std::io::{self, BufRead, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::Duration;

use crossterm::event::{
    read, DisableBracketedPaste, EnableBracketedPaste, Event, KeyCode, KeyEvent, KeyEventKind,
    KeyModifiers,
};
use crossterm::terminal;
use crossterm::tty::IsTty;

use editor::{AskState, Key, Outcome};

const PROMPT_IDLE: &str = "›";
const MARKER: &str = "❯";
const CRAFT_WIDTH_DEFAULT: usize = 15;
const CRAFT_ROWS: usize = 4;
const SPINNER_FRAMES: [&str; 10] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
const SPINNER_TICK: Duration = Duration::from_millis(80);
const MIN_ROWS: u16 = 14;
const MIN_COLS: u16 = 48;
const BOLD: &str = "\x1b[1m";
const DIM: &str = "\x1b[2m";
const RESET: &str = "\x1b[0m";
const CYAN: &str = "\x1b[36m";
const MAGENTA: &str = "\x1b[35m";
const CYAN_256: &str = "\x1b[38;5;87m";
const MAGENTA_256: &str = "\x1b[38;5;213m";

#[derive(Clone)]
struct Palette {
    bold: &'static str,
    dim: &'static str,
    cyan: &'static str,
    magenta: &'static str,
    reset: &'static str,
}

impl Palette {
    fn for_term(term: &str) -> Palette {
        let colorterm = std::env::var("COLORTERM").unwrap_or_default();
        let rich = colorterm.contains("truecolor") || term.contains("256color");
        Palette {
            bold: BOLD,
            dim: DIM,
            cyan: if rich { CYAN_256 } else { CYAN },
            magenta: if rich { MAGENTA_256 } else { MAGENTA },
            reset: RESET,
        }
    }

    fn empty() -> Palette {
        Palette {
            bold: "",
            dim: "",
            cyan: "",
            magenta: "",
            reset: "",
        }
    }
}

struct Spinner {
    running: Arc<AtomicBool>,
    handle: JoinHandle<()>,
}

struct RawGuard;

impl RawGuard {
    fn new() -> RawGuard {
        let _ = terminal::enable_raw_mode();
        let _ = crossterm::execute!(io::stdout(), EnableBracketedPaste);
        RawGuard
    }
}

impl Drop for RawGuard {
    fn drop(&mut self) {
        let _ = crossterm::execute!(io::stdout(), DisableBracketedPaste);
        let _ = terminal::disable_raw_mode();
    }
}

/// The renderer. Falls back to plain line output when stdout is not a TTY, `TERM=dumb`, or
/// `UFO_PLAIN` is set.
pub struct Ui {
    fx: bool,
    p: Palette,
    craft_w: usize,
    craft: Vec<String>,
    open: String,
    status: String,
    dyn_rows: u16,
    history: Vec<String>,
    spinner: Option<Spinner>,
    plain_open: bool,
}

impl Ui {
    pub fn new() -> Ui {
        let tty = io::stdout().is_tty();
        let term = std::env::var("TERM").unwrap_or_default();
        let plain = std::env::var_os("UFO_PLAIN").is_some();
        let fx = tty && !term.is_empty() && term != "dumb" && !plain;
        #[cfg(windows)]
        let fx = fx && crossterm::ansi_support::supports_ansi();
        let colored = fx && std::env::var_os("NO_COLOR").is_none();
        Ui {
            fx,
            p: if colored {
                Palette::for_term(&term)
            } else {
                Palette::empty()
            },
            craft_w: CRAFT_WIDTH_DEFAULT,
            craft: Vec::new(),
            open: String::new(),
            status: String::new(),
            dyn_rows: 0,
            history: Vec::new(),
            spinner: None,
            plain_open: false,
        }
    }

    /// A finalized transcript line.
    pub fn say(&mut self, text: &str) {
        self.transcript_line(&format!("{text}\n"));
    }

    /// A dim activity line.
    pub fn note(&mut self, text: &str) {
        let p = self.p.clone();
        self.transcript_line(&format!("{}{text}{}\n", p.dim, p.reset));
    }

    /// A streamed text delta: completed lines flow into the transcript, the open tail shows in
    /// the activity row.
    pub fn txt(&mut self, chunk: &str) {
        self.status.clear();
        if !self.fx {
            self.emit(chunk);
            if !chunk.is_empty() {
                self.plain_open = !chunk.ends_with('\n');
            }
            return;
        }
        self.open.push_str(chunk);
        let Some((cols, _)) = self.fx_size() else {
            let tail = std::mem::take(&mut self.open);
            self.plain_open = !tail.is_empty() && !tail.ends_with('\n');
            self.emit(&tail);
            return;
        };
        let wrap_at = cols.saturating_sub(2);
        if self.open.contains('\n') || wrap::width(&self.open) >= wrap_at {
            self.erase_region();
            self.drain_open(wrap_at);
            self.paint_region(None);
        } else {
            self.preview_paint();
        }
    }

    /// A transient status shown in the activity row until the next transcript line.
    pub fn status(&mut self, text: &str) {
        if !self.fx {
            self.emit(&format!("  {text}\n"));
            return;
        }
        let Some((cols, _)) = self.fx_size() else {
            self.emit(&format!("  {text}\n"));
            return;
        };
        self.status = wrap::clip(text, cols.saturating_sub(4)).to_string();
        self.preview_paint();
    }

    /// A shared-file line.
    pub fn file(&mut self, name: &str, size: &str, url: &str) {
        let line = if url.is_empty() {
            format!("shared {name} ({size} bytes)")
        } else {
            format!("shared {name} ({size} bytes) {url}")
        };
        self.note(&line);
    }

    /// The craft frame drawn at the right of the dynamic region.
    pub fn set_craft(&mut self, width: usize, frame: &str) {
        self.craft_w = width.max(1);
        self.craft = frame
            .split('\n')
            .take(CRAFT_ROWS)
            .map(str::to_string)
            .collect();
        if self.fx {
            self.paint_region(None);
        }
    }

    /// Spinner while a request is in flight and nothing has arrived yet.
    pub fn spinner_start(&mut self) {
        if !self.fx || self.spinner.is_some() {
            return;
        }
        self.emit("\x1b[?25l");
        let running = Arc::new(AtomicBool::new(true));
        let flag = running.clone();
        let rows = self.dyn_rows;
        let cyan = self.p.cyan;
        let reset = self.p.reset;
        let handle = thread::spawn(move || {
            let mut at = 0;
            while flag.load(Ordering::Relaxed) {
                let frame = SPINNER_FRAMES[at % SPINNER_FRAMES.len()];
                at += 1;
                let paint = if rows > 1 {
                    format!("\x1b7\r\x1b[{}A\x1b[2K {cyan}{frame}{reset}\x1b8", rows - 1)
                } else {
                    format!("\r\x1b[K {cyan}{frame}{reset}")
                };
                let mut out = io::stdout();
                let _ = out.write_all(paint.as_bytes());
                let _ = out.flush();
                thread::sleep(SPINNER_TICK);
            }
        });
        self.spinner = Some(Spinner { running, handle });
    }

    pub fn spinner_stop(&mut self) {
        let Some(spinner) = self.spinner.take() else {
            return;
        };
        spinner.running.store(false, Ordering::Relaxed);
        let _ = spinner.handle.join();
        if self.dyn_rows > 1 {
            self.emit(&format!("\x1b7\r\x1b[{}A\x1b[2K\x1b8", self.dyn_rows - 1));
        } else {
            self.emit("\r\x1b[K");
        }
    }

    /// Read one line of member input under `prompt` with full line editing; `None` on EOF/cancel.
    pub fn ask(&mut self, prompt: &str) -> Option<String> {
        if !self.fx {
            return self.ask_plain(prompt);
        }
        self.status.clear();
        let raw = RawGuard::new();
        self.emit("\x1b[?25h");
        let mut state = AskState::new();
        self.paint_ask(prompt, &state, true);
        let submitted = loop {
            let event = match read() {
                Ok(event) => event,
                Err(_) => break None,
            };
            match event {
                Event::Key(key) if key.kind != KeyEventKind::Release => {
                    let Some(key) = decode_key(key) else { continue };
                    match state.apply(key, &self.history) {
                        Outcome::Continue => self.paint_ask(prompt, &state, false),
                        Outcome::Submit => break Some(std::mem::take(&mut state.text)),
                        Outcome::Cancel => break None,
                    }
                }
                Event::Paste(text) => {
                    state.apply(Key::Paste(text), &self.history);
                    self.paint_ask(prompt, &state, false);
                }
                Event::Resize(..) => self.paint_ask(prompt, &state, false),
                _ => {}
            }
        };
        self.emit("\x1b8");
        drop(raw);
        self.erase_region();
        let text = submitted?;
        let p = self.p.clone();
        self.emit(&format!("{}{PROMPT_IDLE}{} {text}\n", p.magenta, p.reset));
        if !text.is_empty() && self.history.last() != Some(&text) {
            self.history.push(text.clone());
        }
        self.paint_region(None);
        Some(text)
    }

    /// Pick one option with arrow keys (numbered read in plain mode); `None` on cancel.
    pub fn menu(&mut self, prompt: &str, options: &[String]) -> Option<String> {
        if options.is_empty() {
            return None;
        }
        if !self.fx {
            return self.menu_plain(prompt, options);
        }
        self.erase_region();
        let raw = RawGuard::new();
        self.emit("\x1b[?25l");
        let mut selected = 0;
        self.paint_menu(prompt, options, selected, true);
        let picked = loop {
            let event = match read() {
                Ok(event) => event,
                Err(_) => break None,
            };
            let Event::Key(key) = event else {
                if let Event::Resize(..) = event {
                    self.paint_menu(prompt, options, selected, false);
                }
                continue;
            };
            if key.kind == KeyEventKind::Release {
                continue;
            }
            let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
            match key.code {
                KeyCode::Up | KeyCode::Char('k') if !ctrl => {
                    selected = (selected + options.len() - 1) % options.len();
                    self.paint_menu(prompt, options, selected, false);
                }
                KeyCode::Down | KeyCode::Char('j') if !ctrl => {
                    selected = (selected + 1) % options.len();
                    self.paint_menu(prompt, options, selected, false);
                }
                KeyCode::Enter => break Some(options[selected].clone()),
                KeyCode::Esc => break None,
                KeyCode::Char('c') | KeyCode::Char('d') if ctrl => break None,
                _ => {}
            }
        };
        self.emit("\x1b[?25h");
        drop(raw);
        picked
    }

    /// Read one secret without echo; `None` on EOF, `Some("")` when skipped. A terminal on stdin
    /// always goes through the raw-mode reader — plain mode included — so the typed value never
    /// echoes; piped stdin reads a line, which no terminal echoes.
    pub fn secret(&mut self, prompt: &str) -> Option<String> {
        if !io::stdin().is_tty() {
            let mut line = String::new();
            eprint!("{prompt} (hidden): ");
            let _ = io::stderr().flush();
            return match io::stdin().lock().read_line(&mut line) {
                Ok(0) | Err(_) => None,
                Ok(_) => Some(line.trim_end_matches(['\r', '\n']).to_string()),
            };
        }
        let raw = RawGuard::new();
        let p = self.p.clone();
        let to_stderr = !io::stdout().is_tty();
        let shown = format!("\r{}{prompt}{} (hidden): ", p.magenta, p.reset);
        if to_stderr {
            eprint!("{shown}");
            let _ = io::stderr().flush();
        } else {
            self.emit(&shown);
        }
        let mut value = String::new();
        let entered = loop {
            let event = match read() {
                Ok(event) => event,
                Err(_) => break None,
            };
            let Event::Key(key) = event else { continue };
            if key.kind == KeyEventKind::Release {
                continue;
            }
            let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
            match key.code {
                KeyCode::Enter => break Some(std::mem::take(&mut value)),
                KeyCode::Backspace => {
                    value.pop();
                }
                KeyCode::Char('u') if ctrl => value.clear(),
                KeyCode::Char('c') | KeyCode::Char('d') if ctrl => break None,
                KeyCode::Char(ch) if !ctrl => value.push(ch),
                _ => {}
            }
        };
        if to_stderr {
            eprint!("\r\n");
            let _ = io::stderr().flush();
        } else {
            self.emit("\r\n");
        }
        drop(raw);
        entered
    }

    /// Flush any open streamed text into the transcript at end of stream.
    pub fn end_stream(&mut self) {
        self.status.clear();
        if !self.fx {
            self.line_break();
            return;
        }
        if !self.open.is_empty() {
            self.erase_region();
            self.flush_open();
        }
        self.paint_region(None);
    }

    /// Erase the dynamic region and restore the terminal.
    pub fn close(&mut self) {
        if let Some(spinner) = self.spinner.take() {
            spinner.running.store(false, Ordering::Relaxed);
            let _ = spinner.handle.join();
        }
        if self.fx {
            self.erase_region();
            self.emit("\x1b[?25h");
        } else {
            self.line_break();
        }
    }

    fn transcript_line(&mut self, rendered: &str) {
        self.status.clear();
        if self.fx {
            self.erase_region();
            self.flush_open();
            self.emit(rendered);
            self.paint_region(None);
        } else {
            self.line_break();
            self.emit(rendered);
        }
    }

    fn flush_open(&mut self) {
        if self.open.is_empty() {
            return;
        }
        let tail = std::mem::take(&mut self.open);
        self.emit(&format!("{tail}\n"));
    }

    fn drain_open(&mut self, wrap_at: usize) {
        loop {
            let newline = self.open.find('\n');
            let line = match newline {
                Some(at) => &self.open[..at],
                None => self.open.as_str(),
            };
            if wrap::width(line) < wrap_at {
                match newline {
                    Some(at) => {
                        let line = self.open[..at].to_string();
                        self.emit(&format!("{line}\n"));
                        self.open.replace_range(..=at, "");
                        continue;
                    }
                    None => break,
                }
            }
            let (head, rest) = wrap::wrap_head(&self.open, wrap_at);
            let line = self.open[..head].to_string();
            self.emit(&format!("{line}\n"));
            self.open.replace_range(..rest, "");
        }
    }

    fn size(&self) -> (usize, usize) {
        let (cols, rows) = terminal::size().unwrap_or((80, 24));
        (cols as usize, rows as usize)
    }

    fn fx_size(&mut self) -> Option<(usize, usize)> {
        let (cols, rows) = self.size();
        if rows >= MIN_ROWS as usize && cols >= MIN_COLS as usize {
            return Some((cols, rows));
        }
        self.erase_region();
        self.fx = false;
        None
    }

    fn region_top(&self, buffer: &mut String) {
        if self.dyn_rows > 1 {
            buffer.push_str(&format!("\r\x1b[{}A", self.dyn_rows - 1));
        } else {
            buffer.push('\r');
        }
    }

    fn activity_row(&self, buffer: &mut String, cols: usize) {
        buffer.push_str("\x1b[2K");
        if !self.open.is_empty() {
            buffer.push_str(wrap::clip(&self.open, cols.saturating_sub(1)));
        } else if !self.status.is_empty() {
            buffer.push_str(&format!("  {}{}{}", self.p.dim, self.status, self.p.reset));
        }
    }

    fn ship_suffix(&self, buffer: &mut String, cols: usize, at: &mut usize) {
        if !self.craft.is_empty() && *at <= CRAFT_ROWS {
            let line = self.craft.get(*at - 1).map(String::as_str).unwrap_or("");
            buffer.push_str(&format!("\x1b[{}G{line}\x1b[0m", cols - self.craft_w + 1));
        }
        *at += 1;
    }

    fn paint_region(&mut self, edit: Option<(&str, &AskState)>) -> Option<(usize, usize, usize)> {
        if !self.fx {
            return None;
        }
        if self.craft.is_empty() && edit.is_none() {
            return None;
        }
        let (cols, rows) = self.size();
        if rows < MIN_ROWS as usize || cols < MIN_COLS as usize {
            self.erase_region();
            return None;
        }
        let prompt = edit.map(|(prompt, _)| prompt).unwrap_or(PROMPT_IDLE);
        let prompt_w = wrap::width(prompt);
        let rule = "─".repeat(cols.saturating_sub(self.craft_w + 1));
        let cap1 = cols.saturating_sub(self.craft_w + 3 + prompt_w);
        let cap = cols.saturating_sub(self.craft_w + 3);
        let text = edit.map(|(_, state)| state.text.as_str()).unwrap_or("");
        let entry_rows = wrap::hard_rows(text, cap1, cap);
        let mut buffer = String::new();
        self.region_top(&mut buffer);
        self.activity_row(&mut buffer, cols);
        let mut ship = 1;
        buffer.push_str(&format!("\r\n\x1b[2K{}{rule}{}", self.p.dim, self.p.reset));
        self.ship_suffix(&mut buffer, cols, &mut ship);
        for (index, (start, end)) in entry_rows.iter().enumerate() {
            buffer.push_str("\r\n\x1b[2K");
            let part = &text[*start..*end];
            if index == 0 {
                if edit.is_some() {
                    buffer.push_str(&format!(
                        "{}{prompt}{} {part}",
                        self.p.magenta, self.p.reset
                    ));
                } else {
                    buffer.push_str(&format!("{}{PROMPT_IDLE}{}", self.p.dim, self.p.reset));
                }
            } else if !part.is_empty() {
                buffer.push_str(&format!("  {part}"));
            }
            self.ship_suffix(&mut buffer, cols, &mut ship);
        }
        buffer.push_str(&format!("\r\n\x1b[2K{}{rule}{}", self.p.dim, self.p.reset));
        self.ship_suffix(&mut buffer, cols, &mut ship);
        buffer.push_str("\r\n\x1b[2K");
        self.ship_suffix(&mut buffer, cols, &mut ship);
        buffer.push_str("\x1b[J\r");
        self.dyn_rows = (entry_rows.len() + 4) as u16;
        self.emit(&buffer);
        edit.map(|(_, state)| {
            let (row, col) = wrap::cursor_pos(text, &entry_rows, state.cursor);
            (
                entry_rows.len(),
                row,
                if row == 0 {
                    prompt_w + 2 + col
                } else {
                    3 + col
                },
            )
        })
    }

    fn paint_ask(&mut self, prompt: &str, state: &AskState, first: bool) {
        if !first {
            self.emit("\x1b8");
        }
        if let Some((total, row, col)) = self.paint_region(Some((prompt, state))) {
            let up = 2 + (total - 1 - row);
            self.emit(&format!("\x1b7\x1b[{up}A\x1b[{col}G"));
        }
    }

    fn preview_paint(&mut self) {
        if !self.fx {
            return;
        }
        if self.dyn_rows == 0 {
            self.paint_region(None);
            return;
        }
        let (cols, _) = self.size();
        let mut buffer = String::new();
        self.region_top(&mut buffer);
        self.activity_row(&mut buffer, cols);
        buffer.push_str(&format!("\x1b[{}B\r", self.dyn_rows - 1));
        self.emit(&buffer);
    }

    fn erase_region(&mut self) {
        if self.dyn_rows == 0 {
            return;
        }
        let mut buffer = String::new();
        self.region_top(&mut buffer);
        buffer.push_str("\x1b[J");
        self.dyn_rows = 0;
        self.emit(&buffer);
    }

    fn paint_menu(&mut self, prompt: &str, options: &[String], selected: usize, first: bool) {
        let mut buffer = String::new();
        if !first {
            buffer.push_str(&format!("\x1b[{}A", options.len() + 1));
        }
        buffer.push_str(&format!(
            "\r\x1b[2K{}{prompt}{}\r\n",
            self.p.bold, self.p.reset
        ));
        for (index, option) in options.iter().enumerate() {
            if index == selected {
                buffer.push_str(&format!(
                    "\r\x1b[2K{}{MARKER} {option}{}\r\n",
                    self.p.magenta, self.p.reset
                ));
            } else {
                buffer.push_str(&format!("\r\x1b[2K  {option}\r\n"));
            }
        }
        self.emit(&buffer);
    }

    fn ask_plain(&mut self, prompt: &str) -> Option<String> {
        eprint!("{prompt} ");
        let _ = io::stderr().flush();
        let mut line = String::new();
        match io::stdin().lock().read_line(&mut line) {
            Ok(0) | Err(_) => None,
            Ok(_) => Some(line.trim_end_matches(['\r', '\n']).to_string()),
        }
    }

    fn menu_plain(&mut self, prompt: &str, options: &[String]) -> Option<String> {
        self.emit(&format!("{prompt}\n"));
        for (index, option) in options.iter().enumerate() {
            self.emit(&format!("  {}) {option}\n", index + 1));
        }
        let answer = self.ask_plain("›")?;
        if answer.is_empty() {
            return None;
        }
        match answer.parse::<usize>() {
            Ok(number) if (1..=options.len()).contains(&number) => {
                Some(options[number - 1].clone())
            }
            _ => Some(answer),
        }
    }

    fn line_break(&mut self) {
        if self.plain_open {
            self.emit("\n");
            self.plain_open = false;
        }
    }

    fn emit(&self, text: &str) {
        let mut out = io::stdout();
        let _ = out.write_all(text.as_bytes());
        let _ = out.flush();
    }
}

impl Default for Ui {
    fn default() -> Ui {
        Ui::new()
    }
}

impl Drop for Ui {
    fn drop(&mut self) {
        if let Some(spinner) = self.spinner.take() {
            spinner.running.store(false, Ordering::Relaxed);
            let _ = spinner.handle.join();
        }
        if self.fx {
            let _ = terminal::disable_raw_mode();
            self.emit("\x1b[?25h");
        }
    }
}

fn decode_key(key: KeyEvent) -> Option<Key> {
    let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
    let alt = key.modifiers.contains(KeyModifiers::ALT);
    Some(match key.code {
        KeyCode::Enter if alt => Key::InsertNewline,
        KeyCode::Enter => Key::Enter,
        KeyCode::Backspace if alt => Key::KillWord,
        KeyCode::Backspace => Key::Backspace,
        KeyCode::Delete => Key::Delete,
        KeyCode::Left if ctrl || alt => Key::WordLeft,
        KeyCode::Left => Key::Left,
        KeyCode::Right if ctrl || alt => Key::WordRight,
        KeyCode::Right => Key::Right,
        KeyCode::Home => Key::Home,
        KeyCode::End => Key::End,
        KeyCode::Up => Key::HistPrev,
        KeyCode::Down => Key::HistNext,
        KeyCode::Char('a') if ctrl => Key::Home,
        KeyCode::Char('e') if ctrl => Key::End,
        KeyCode::Char('u') if ctrl => Key::KillLine,
        KeyCode::Char('w') if ctrl => Key::KillWord,
        KeyCode::Char('j') if ctrl => Key::InsertNewline,
        KeyCode::Char('c') if ctrl => Key::Cancel,
        KeyCode::Char('d') if ctrl => Key::Eof,
        KeyCode::Char('b') if alt => Key::WordLeft,
        KeyCode::Char('f') if alt => Key::WordRight,
        KeyCode::Char(ch) if !ctrl && !alt => Key::Char(ch),
        _ => return None,
    })
}
