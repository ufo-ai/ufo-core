//! The terminal app: a scrollback transcript over a repainted dock — activity row, rule, queued
//! sends, the composer (or a picker, a masked secret entry, or the hotkey sheet), rule, footer.
//! Every member-visible string renders through the theme's roles.

mod editor;
pub mod history;
pub mod markdown;
pub mod osc;
pub mod picker;
pub mod plain;
pub mod retained;
pub mod select;
pub mod status;
pub mod term;
pub mod theme;
pub mod toolrender;
mod wrap;

use std::collections::VecDeque;
use std::io::{self, Write};
use std::path::PathBuf;
use std::time::Instant;

use crossterm::event::{
    DisableBracketedPaste, DisableFocusChange, DisableMouseCapture, EnableBracketedPaste,
    EnableFocusChange, EnableMouseCapture, KeyCode, KeyEvent, KeyModifiers,
    KeyboardEnhancementFlags, MouseButton, MouseEvent, MouseEventKind, PopKeyboardEnhancementFlags,
    PushKeyboardEnhancementFlags,
};
use crossterm::terminal;
use crossterm::tty::IsTty as _;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};

use crate::ui::editor::{AskState, Key, Outcome};
use crate::ui::history::History;
use crate::ui::osc::{Caps, ImageProtocol};
use crate::ui::picker::{PickKey, PickOutcome, Picker};
use crate::ui::retained::{Entry, Retained};
use crate::ui::select::{ClickTracker, Grain, Selection};
use crate::ui::status::{Activity, Progress, Signals, StatusRow};
use crate::ui::term::AltScreen;
use crate::ui::theme::{ColorMode, Theme};
use crate::ui::toolrender::OpView;
use crate::wire::OpRequest;

pub const PROMPT_IDLE: &str = "›";
const QUEUE_SHOWN: usize = 3;
const ENTRY_ROWS_MAX: usize = 8;
const PICKER_ROWS: usize = 8;
const OP_LOG_ROWS: usize = 6;
const ECHO_INDENT: &str = "  ";
const IMAGE_COLS_MAX: u16 = 60;
const IMAGE_BYTES_MAX: usize = 2 * 1024 * 1024;
const KEY_COL: usize = 26;
const FLASH_SECONDS: u64 = 2;
const EARLY_ABSORBED_MAX: usize = 64;

/// Whether the fancy renderer runs: a TTY, a real TERM, and no `UFO_PLAIN`.
pub fn wants_fx() -> bool {
    let term = std::env::var("TERM").unwrap_or_default();
    io::stdout().is_tty()
        && !term.is_empty()
        && term != "dumb"
        && std::env::var_os("UFO_PLAIN").is_none()
}

/// Raw mode plus bracketed paste and, where the terminal takes them, the kitty keyboard flags —
/// entered once for the whole session, before the input thread starts reading.
pub struct RawGuard {
    kitty: bool,
}

impl RawGuard {
    pub fn new() -> RawGuard {
        let kitty = terminal::supports_keyboard_enhancement().unwrap_or(false);
        #[cfg(unix)]
        crate::interrupt::hold_modes();
        let _ = terminal::enable_raw_mode();
        let _ = crossterm::execute!(
            io::stdout(),
            EnableBracketedPaste,
            EnableMouseCapture,
            EnableFocusChange
        );
        if kitty {
            let _ = crossterm::execute!(
                io::stdout(),
                PushKeyboardEnhancementFlags(KeyboardEnhancementFlags::DISAMBIGUATE_ESCAPE_CODES)
            );
        }
        RawGuard { kitty }
    }
}

impl Drop for RawGuard {
    fn drop(&mut self) {
        if self.kitty {
            let _ = crossterm::execute!(io::stdout(), PopKeyboardEnhancementFlags);
        }
        let _ = crossterm::execute!(
            io::stdout(),
            DisableFocusChange,
            DisableMouseCapture,
            DisableBracketedPaste
        );
        let _ = terminal::disable_raw_mode();
        #[cfg(unix)]
        crate::interrupt::release_modes();
    }
}

/// What a key did, for the loop to route.
#[derive(Debug, Clone, PartialEq)]
pub enum Reply {
    None,
    Send(String),
    Clipboard,
    Recall { text: String, arrival_id: String },
    Choice(String),
    ChoiceCancelled,
    Secret(String),
    Stop,
    Detach,
    Exit,
}

#[derive(PartialEq)]
enum Focus {
    Compose,
    Choose,
    Secret,
    Path,
    Keys,
}

struct Chooser {
    prompt: String,
    picker: Picker,
}

struct SecretEntry {
    prompt: String,
    value: String,
}

/// One message waiting under the composer: typed; named by the arrival id the turn will fold
/// once its instant send is acknowledged; and marked while a recall is in flight so a second Up
/// never retracts it twice.
struct QueuedSend {
    text: String,
    arrival: Option<String>,
    retracting: bool,
}

struct PathPick {
    picker: Picker,
    token_start: usize,
}

/// The dock and everything drawn in it.
pub struct App {
    pub theme: Theme,
    pub caps: Caps,
    signals: Signals,
    progress: Progress,
    screen: AltScreen<io::Stdout>,
    status: StatusRow,
    stream: markdown::StreamRenderer,
    ask: AskState,
    prompt: String,
    history: History,
    queued: VecDeque<QueuedSend>,
    early_absorbed: Vec<String>,
    focus: Focus,
    chooser: Option<Chooser>,
    secret: Option<SecretEntry>,
    path_pick: Option<PathPick>,
    retained: Retained,
    reply_open: bool,
    view_rows: usize,
    window_start: usize,
    exit_images: Vec<String>,
    selection: Option<Selection>,
    clicks: ClickTracker,
    focused: bool,
    flash: Option<(String, Instant)>,
    op_log: Vec<Line<'static>>,
    running_op: Option<OpView>,
    running_desc: Option<String>,
    narration: Option<(String, String)>,
    last_reply: String,
    host: String,
    channel: String,
    cwd: PathBuf,
    working: bool,
    cols: u16,
    rows: u16,
}

impl App {
    pub fn new(home_root: &std::path::Path, host: String, channel: String, cwd: PathBuf) -> App {
        let theme = Theme::detect(false);
        let caps = Caps::detect();
        let (cols, rows) = sane_size();
        let mut screen = AltScreen::new(io::stdout(), theme.mode);
        let _ = screen.enter();
        App {
            signals: Signals {
                enabled: theme.mode != ColorMode::Plain,
            },
            progress: Progress::new(),
            screen,
            status: StatusRow::new(),
            stream: markdown::StreamRenderer::new(),
            ask: AskState::new(),
            prompt: PROMPT_IDLE.to_string(),
            history: History::load(home_root),
            queued: VecDeque::new(),
            early_absorbed: Vec::new(),
            focus: Focus::Compose,
            chooser: None,
            secret: None,
            path_pick: None,
            retained: Retained::new(cols),
            reply_open: false,
            view_rows: 1,
            window_start: 0,
            exit_images: Vec::new(),
            selection: None,
            clicks: ClickTracker::new(),
            focused: true,
            flash: None,
            op_log: Vec::new(),
            running_op: None,
            running_desc: None,
            narration: None,
            last_reply: String::new(),
            host,
            channel,
            cwd,
            working: false,
            cols,
            rows,
            theme,
            caps,
        }
    }

    pub fn set_endpoint(&mut self, host: String, channel: String) {
        self.host = host;
        self.channel = channel;
    }

    // ── directives ────────────────────────────────────────────────────────────────────────────

    pub fn say(&mut self, text: &str) {
        self.flush_stream();
        self.op_log.clear();
        self.last_reply = text.to_string();
        self.retained.push(Entry::Markdown(text.to_string()));
        self.reply_open = false;
    }

    pub fn txt(&mut self, chunk: &str) {
        self.last_reply.push_str(chunk);
        let source = self.stream.push(chunk);
        self.commit_reply(source);
    }

    /// Committed reply source joins the transcript: growing the open reply entry, or opening one
    /// on the first non-blank commit — so one streamed reply re-wraps as one document.
    fn commit_reply(&mut self, source: String) {
        if source.is_empty() {
            return;
        }
        if !source.trim().is_empty() {
            self.op_log.clear();
        }
        if self.reply_open {
            self.retained.extend_markdown(&source);
        } else if !source.trim().is_empty() {
            self.retained.push(Entry::Markdown(source));
            self.reply_open = true;
        }
    }

    /// A server note. Tool narration — the activity the client also states for its own ops —
    /// stays in the activity row; everything else joins the transcript.
    pub fn note(&mut self, text: &str) {
        if let Some(rest) = text.strip_prefix("running ") {
            if let Some((tool, detail)) = rest.split_once(": ") {
                self.narration = Some((tool.to_string(), detail.to_string()));
            }
            self.status_text(text);
            return;
        }
        if text.starts_with("loading skill") {
            self.status_text(text);
            return;
        }
        self.flush_stream();
        self.retained.push(Entry::Note(text.to_string()));
    }

    pub fn status_text(&mut self, text: &str) {
        if let Activity::Working { since, .. } = self.status.activity {
            self.status.activity = Activity::Working {
                since,
                status: text.to_string(),
            };
        }
    }

    pub fn file(&mut self, name: &str, size: &str, url: &str) {
        self.flush_stream();
        let said = match url.is_empty() {
            true => name.to_string(),
            false => format!("{}{name}{}", osc::link_open(url), osc::LINK_CLOSE),
        };
        let line = format!("shared {said} ({size} bytes)");
        self.retained
            .push(Entry::Raw(vec![Line::styled(line, self.theme.muted)]));
    }

    /// The op's header throbs in the activity row while it runs; it joins the dock's op log
    /// when it answers, and the log clears the moment the reply starts streaming — tool activity
    /// is read while it happens and never crowds the transcript.
    pub fn op_started(&mut self, op: &OpRequest) {
        self.running_desc = match self.narration.take() {
            Some((tool, detail)) if tool == op.name || tool == op.kind => Some(detail),
            _ => None,
        };
        self.running_op = Some(OpView::from_request(op));
    }

    pub fn op_finished(&mut self, op: &OpRequest, result: &Result<Vec<u8>, String>) {
        self.running_op = None;
        let description = self.running_desc.take();
        let view = OpView::from_request(op);
        let reply = match result {
            Ok(bytes) => Ok(bytes.as_slice()),
            Err(failure) => Err(failure.as_str()),
        };
        let width = self.cols;
        self.op_log
            .push(view.header(description.as_deref(), &self.theme, width));
        self.op_log.extend(view.body(reply, &self.theme, width));
        let overflow = self.op_log.len().saturating_sub(OP_LOG_ROWS);
        if overflow > 0 {
            self.op_log.drain(..overflow);
        }
        if let Ok(bytes) = result {
            self.inline_read_image(op, bytes);
        }
    }

    fn inline_read_image(&mut self, op: &OpRequest, bytes: &[u8]) {
        if op.kind != crate::ops::OP_READ
            || self.caps.images == ImageProtocol::None
            || bytes.len() > IMAGE_BYTES_MAX
        {
            return;
        }
        let (mime, size) = match (osc::png_dimensions(bytes), osc::jpeg_dimensions(bytes)) {
            (Some(size), _) => ("image/png", size),
            (None, Some(size)) => ("image/jpeg", size),
            (None, None) => return,
        };
        let blob = osc::inline_image(self.caps, bytes, mime, IMAGE_COLS_MAX);
        if blob.is_empty() {
            return;
        }
        self.exit_images.push(blob);
        let said = format!(
            "read image {} ({}×{} px, printed when this session ends)",
            op.arg, size.0, size.1
        );
        self.retained.push(Entry::Note(said));
    }

    // ── turn state ────────────────────────────────────────────────────────────────────────────

    pub fn begin_turn(&mut self) {
        self.working = true;
        self.prompt = PROMPT_IDLE.to_string();
        self.last_reply.clear();
        self.status.activity = Activity::Working {
            since: Instant::now(),
            status: String::new(),
        };
        self.splice_raw(&self.signals.title(&format!("ufo — {}", self.channel)));
    }

    pub fn end_turn(&mut self, waiting: bool) {
        self.working = false;
        self.op_log.clear();
        self.running_op = None;
        self.flush_stream();
        self.reply_open = false;
        self.status.activity = if waiting {
            Activity::WaitingInput
        } else {
            Activity::Idle
        };
        let off = self.progress.off(&self.signals);
        self.splice_raw(&off);
        if waiting {
            self.splice_raw(&self.signals.bell());
        }
        if !self.focused {
            let body = if waiting {
                "ufo needs input"
            } else {
                "ufo replied"
            };
            let said = osc::notification(self.caps, body);
            self.splice_raw(&said);
        }
    }

    pub fn set_focus(&mut self, focused: bool) {
        self.focused = focused;
    }

    pub fn reconnecting(&mut self, attempt: u32, of: u32, retry_in_s: u64) {
        self.status.activity = Activity::Reconnecting {
            attempt,
            of,
            retry_in_s,
        };
    }

    pub fn is_working(&self) -> bool {
        self.working
    }

    pub fn tick(&mut self) {
        if self
            .flash
            .as_ref()
            .is_some_and(|(_, at)| at.elapsed().as_secs() >= FLASH_SECONDS)
        {
            self.flash = None;
        }
        self.status.on_tick();
        if self.working {
            let keepalive = self.progress.tick(&self.signals, Instant::now());
            self.splice_raw(&keepalive);
        }
    }

    // ── member input states ───────────────────────────────────────────────────────────────────

    pub fn ask_prompt(&mut self, prompt: &str) {
        self.prompt = if prompt.is_empty() {
            PROMPT_IDLE.to_string()
        } else {
            prompt.to_string()
        };
        self.focus = Focus::Compose;
    }

    pub fn choose(&mut self, prompt: &str, options: &[String]) {
        let mut picker = Picker::new(options.to_vec());
        picker.set_page(PICKER_ROWS);
        self.chooser = Some(Chooser {
            prompt: prompt.to_string(),
            picker,
        });
        self.focus = Focus::Choose;
    }

    pub fn secret_begin(&mut self, prompt: &str) {
        self.secret = Some(SecretEntry {
            prompt: prompt.to_string(),
            value: String::new(),
        });
        self.focus = Focus::Secret;
    }

    pub fn push_queued(&mut self, text: &str) {
        self.queued.push_back(QueuedSend {
            text: text.to_string(),
            arrival: None,
            retracting: false,
        });
    }

    /// Up on an empty composer recalls the newest queued message: the recall the reply names is
    /// posted to the server, and the row waits marked until the server answers whose the words
    /// are. A row still awaiting its send ack cannot be recalled yet.
    fn recall_queued(&mut self) -> Reply {
        let Some(row) = self.queued.iter_mut().rev().find(|row| !row.retracting) else {
            return Reply::None;
        };
        let Some(arrival_id) = row.arrival.clone() else {
            self.flash = Some(("Still sending — try again.".to_string(), Instant::now()));
            return Reply::None;
        };
        row.retracting = true;
        Reply::Recall {
            text: row.text.clone(),
            arrival_id,
        }
    }

    /// The server answered a recall: the words are the member's again (the row leaves the queue
    /// and fills the composer), or the turn already took them up (the row stays and settles the
    /// way every absorbed message does).
    pub fn retracted(&mut self, text: &str, arrival_id: &str, retracted: bool) {
        let Some(at) = self
            .queued
            .iter()
            .position(|row| row.arrival.as_deref() == Some(arrival_id))
        else {
            return;
        };
        if !retracted {
            self.queued[at].retracting = false;
            self.flash = Some(("Already picked up.".to_string(), Instant::now()));
            return;
        }
        self.queued.remove(at);
        let restored = if self.ask.text.is_empty() {
            text.to_string()
        } else {
            format!("{text}\n\n{}", self.ask.text)
        };
        self.ask.text = restored;
        self.ask.cursor = self.ask.text.len();
    }

    /// The server admitted an instant send into the running turn: the row it acknowledged now
    /// waits under its arrival id for the turn to fold it in. An `absorbed` that outran this ack
    /// settles the row at once.
    pub fn sent_ack(&mut self, text: &str, arrival_id: &str) {
        if let Some(at) = self.early_absorbed.iter().position(|id| id == arrival_id) {
            self.early_absorbed.remove(at);
            self.queued_sent(text);
            return;
        }
        if let Some(row) = self
            .queued
            .iter_mut()
            .find(|row| row.text == text && row.arrival.is_none())
        {
            row.arrival = Some(arrival_id.to_string());
        }
    }

    /// The turn took up these arrivals; the queued rows they name settle into the transcript. An
    /// id with no acknowledged row yet is held for the ack racing it.
    pub fn absorbed(&mut self, arrival_ids: &[String]) {
        for id in arrival_ids {
            match self
                .queued
                .iter()
                .position(|row| row.arrival.as_deref() == Some(id))
            {
                Some(at) => {
                    let row = self.queued.remove(at).expect("the row was just found");
                    self.member_echo(&row.text);
                }
                None => {
                    self.early_absorbed.push(id.clone());
                    let overflow = self.early_absorbed.len().saturating_sub(EARLY_ABSORBED_MAX);
                    if overflow > 0 {
                        self.early_absorbed.drain(..overflow);
                    }
                }
            }
        }
    }

    /// The ack named a turn but no pending arrival: the message already lives in that turn — a
    /// retried delivery whose first answer was lost, or a send a spend gate parked whole. The row
    /// settles now, and there is nothing left to recall; a row an absorb already settled stays
    /// settled.
    pub fn settle_queued(&mut self, text: &str) {
        if let Some(at) = self.queued.iter().position(|row| row.text == text) {
            self.queued.remove(at);
            self.member_echo(text);
        }
    }

    /// A queued message the wire has now posted: it leaves the queue and joins the transcript.
    pub fn queued_sent(&mut self, text: &str) {
        if let Some(at) = self.queued.iter().position(|row| row.text == text) {
            self.queued.remove(at);
        }
        self.member_echo(text);
    }

    /// A member message replayed from the durable transcript: drawn as the member's own, but
    /// never re-entered into the input history — it was typed once.
    pub fn member_replay(&mut self, text: &str) {
        self.draw_member(text);
    }

    pub fn member_echo(&mut self, text: &str) {
        self.draw_member(text);
        self.history.push(text);
    }

    fn draw_member(&mut self, text: &str) {
        self.flush_stream();
        self.retained.push(Entry::Member(text.to_string()));
        self.reply_open = false;
    }

    // ── keys ──────────────────────────────────────────────────────────────────────────────────

    pub fn on_key(&mut self, key: KeyEvent) -> Reply {
        if key.code == KeyCode::Char('c') && key.modifiers.contains(KeyModifiers::CONTROL) {
            return Reply::Exit;
        }
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        match key.code {
            KeyCode::PageUp => {
                self.scroll(self.page());
                return Reply::None;
            }
            KeyCode::PageDown => {
                self.scroll(-self.page());
                return Reply::None;
            }
            KeyCode::Up if ctrl => {
                self.retained.jump_member(&self.theme, true);
                return Reply::None;
            }
            KeyCode::Down if ctrl => {
                self.retained.jump_member(&self.theme, false);
                return Reply::None;
            }
            KeyCode::End if self.retained.scrolled() > 0 => {
                self.retained.scroll_to_end();
                return Reply::None;
            }
            _ => {}
        }
        match self.focus {
            Focus::Compose => self.compose_key(key),
            Focus::Choose => self.choose_key(key),
            Focus::Secret => self.secret_key(key),
            Focus::Path => self.path_key(key),
            Focus::Keys => {
                self.focus = Focus::Compose;
                Reply::None
            }
        }
    }

    /// A clipboard image lands in the ask as an `[Image #N]` marker; the send expands it to the
    /// saved path. Only the composer takes an image — a masked entry or path popup drops it, as
    /// they drop every paste that is not theirs.
    pub fn paste_image(&mut self, path: &str) {
        if self.focus != Focus::Compose {
            return;
        }
        let width = self.entry_width();
        self.ask
            .apply(Key::Image(path.to_string()), &self.history.entries, width);
    }

    /// Pasted text reaches whichever entry holds input. Where a terminal brackets a paste the
    /// member never types the characters, so an entry that ignored the event took nothing at all —
    /// a masked entry above all, whose prompt is what asks for a pasted value. That entry holds one
    /// value, so the newline a copied value carries, and every other control character, is dropped.
    pub fn on_paste(&mut self, text: String) -> Reply {
        match self.focus {
            Focus::Compose => {
                let width = self.entry_width();
                self.ask
                    .apply(Key::Paste(text), &self.history.entries, width);
            }
            Focus::Path => {
                let width = self.entry_width();
                self.ask
                    .apply(Key::Paste(text), &self.history.entries, width);
                self.refilter_paths();
            }
            Focus::Secret => {
                if let Some(entry) = self.secret.as_mut() {
                    entry
                        .value
                        .extend(text.chars().filter(|ch| !ch.is_control()));
                }
            }
            Focus::Choose | Focus::Keys => {}
        }
        Reply::None
    }

    fn compose_key(&mut self, key: KeyEvent) -> Reply {
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        match key.code {
            KeyCode::Up if !ctrl && self.ask.text.is_empty() && !self.queued.is_empty() => {
                return self.recall_queued();
            }
            KeyCode::Char('o') if ctrl => {
                self.copy_last_reply();
                return Reply::None;
            }
            KeyCode::Char('v') if ctrl => return Reply::Clipboard,
            KeyCode::Char('?') if self.ask.text.is_empty() => {
                self.focus = Focus::Keys;
                return Reply::None;
            }
            KeyCode::Char('@') if !ctrl && self.opens_a_mention() => {
                let width = self.entry_width();
                self.ask.apply(Key::Char('@'), &self.history.entries, width);
                self.open_path_pick();
                return Reply::None;
            }
            KeyCode::Char('b') if ctrl && self.working => return Reply::Detach,
            KeyCode::Esc => {
                if self.working && self.ask.text.is_empty() {
                    return Reply::Stop;
                }
                self.ask = AskState::new();
                return Reply::None;
            }
            _ => {}
        }
        let Some(decoded) = decode_key(key) else {
            return Reply::None;
        };
        let width = self.entry_width();
        match self.ask.apply(decoded, &self.history.entries, width) {
            Outcome::Continue => Reply::None,
            Outcome::Cancel => Reply::Exit,
            Outcome::Submit => {
                let text = self.ask.expand();
                self.ask = AskState::new();
                if text.trim().is_empty() {
                    return Reply::None;
                }
                Reply::Send(text)
            }
        }
    }

    fn choose_key(&mut self, key: KeyEvent) -> Reply {
        let Some(chooser) = self.chooser.as_mut() else {
            self.focus = Focus::Compose;
            return Reply::None;
        };
        let Some(pick) = pick_key(key) else {
            return Reply::None;
        };
        match chooser.picker.apply_key(pick) {
            PickOutcome::Continue => Reply::None,
            PickOutcome::Picked(choice) => {
                self.chooser = None;
                self.focus = Focus::Compose;
                Reply::Choice(choice)
            }
            PickOutcome::Cancelled => {
                self.chooser = None;
                self.focus = Focus::Compose;
                Reply::ChoiceCancelled
            }
        }
    }

    fn secret_key(&mut self, key: KeyEvent) -> Reply {
        let Some(entry) = self.secret.as_mut() else {
            self.focus = Focus::Compose;
            return Reply::None;
        };
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        match key.code {
            KeyCode::Enter => {
                let value = std::mem::take(&mut entry.value);
                self.secret = None;
                self.focus = Focus::Compose;
                Reply::Secret(value)
            }
            KeyCode::Esc => {
                self.secret = None;
                self.focus = Focus::Compose;
                Reply::Secret(String::new())
            }
            KeyCode::Backspace => {
                entry.value.pop();
                Reply::None
            }
            KeyCode::Char('u') if ctrl => {
                entry.value.clear();
                Reply::None
            }
            KeyCode::Char('v') if ctrl => Reply::Clipboard,
            KeyCode::Char(ch) if !ctrl => {
                entry.value.push(ch);
                Reply::None
            }
            _ => Reply::None,
        }
    }

    /// Whether an `@` typed here starts a path mention: only at the start of a word, so an address
    /// like `member@example.com` — what a sign-in prompt asks for — types straight through.
    fn opens_a_mention(&self) -> bool {
        self.ask.text[..self.ask.cursor]
            .chars()
            .next_back()
            .is_none_or(char::is_whitespace)
    }

    fn open_path_pick(&mut self) {
        let candidates = picker::path_candidates(&self.cwd, "", 200);
        if candidates.is_empty() {
            return;
        }
        let mut picker = Picker::new(candidates);
        picker.set_page(PICKER_ROWS);
        self.path_pick = Some(PathPick {
            picker,
            token_start: self.ask.cursor,
        });
        self.focus = Focus::Path;
    }

    fn path_key(&mut self, key: KeyEvent) -> Reply {
        let width = self.entry_width();
        let Some(pick) = self.path_pick.as_mut() else {
            self.focus = Focus::Compose;
            return Reply::None;
        };
        match key.code {
            KeyCode::Esc => {
                self.path_pick = None;
                self.focus = Focus::Compose;
            }
            KeyCode::Up => {
                pick.picker.step(-1);
            }
            KeyCode::Down => {
                pick.picker.step(1);
            }
            KeyCode::Enter | KeyCode::Tab => {
                let token_start = pick.token_start;
                let picked = pick.picker.current().map(str::to_string);
                self.path_pick = None;
                self.focus = Focus::Compose;
                match picked {
                    Some(path) => {
                        let end = self.ask.cursor;
                        self.ask.text.replace_range(token_start..end, &path);
                        self.ask.cursor = token_start + path.len();
                    }
                    // Nothing is left to complete, so the key is the composer's: Enter still sends.
                    None => return self.compose_key(key),
                }
            }
            KeyCode::Backspace => {
                if self.ask.cursor <= pick.token_start {
                    self.path_pick = None;
                    self.focus = Focus::Compose;
                    return Reply::None;
                }
                self.ask.apply(Key::Backspace, &self.history.entries, width);
                self.refilter_paths();
            }
            KeyCode::Char('v') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                return Reply::Clipboard;
            }
            KeyCode::Char(ch) if !key.modifiers.contains(KeyModifiers::CONTROL) => {
                self.ask.apply(Key::Char(ch), &self.history.entries, width);
                self.refilter_paths();
            }
            _ => {}
        }
        Reply::None
    }

    fn refilter_paths(&mut self) {
        let Some(pick) = self.path_pick.as_mut() else {
            return;
        };
        let token = self.ask.text[pick.token_start..self.ask.cursor].to_string();
        pick.picker.set_filter(&token);
    }

    fn copy_last_reply(&mut self) {
        if !self.caps.osc52 || self.last_reply.is_empty() {
            return;
        }
        let escape = osc::copy_to_clipboard(self.caps, &self.last_reply);
        self.splice_raw(&escape);
        self.note("Copied the last reply.");
    }

    // ── painting ──────────────────────────────────────────────────────────────────────────────

    /// The alternate screen owns every row, so a resize is one clean repaint at the new size,
    /// and every retained entry re-wraps to the new width.
    pub fn resize(&mut self) {
        let (cols, rows) = sane_size();
        self.cols = cols;
        self.rows = rows;
        self.retained.set_width(cols);
        self.screen.invalidate();
    }

    /// Every mouse event: the wheel scrolls; a press anchors a selection at the grain repeated
    /// clicks cycle to; a drag extends it, scrolling at the window's edges; releasing a drag or a
    /// widened grain copies it. A plain click opens the URL under it, or clears the selection.
    pub fn on_mouse(&mut self, mouse: MouseEvent) {
        match mouse.kind {
            MouseEventKind::ScrollUp => self.scroll(3),
            MouseEventKind::ScrollDown => self.scroll(-3),
            MouseEventKind::Down(MouseButton::Left) => {
                if (mouse.row as usize) >= self.view_rows {
                    self.selection = None;
                    return;
                }
                let at = (
                    self.window_start + mouse.row as usize,
                    mouse.column as usize,
                );
                let grain = self.clicks.press(at);
                self.selection = Some(Selection::begin(at.0, at.1, grain));
            }
            MouseEventKind::Drag(MouseButton::Left) => {
                let edge = select::edge_scroll(mouse.row, self.view_rows);
                if edge != 0 {
                    self.scroll(-edge);
                }
                if let Some(selection) = self.selection.as_mut() {
                    let row = (mouse.row as usize).min(self.view_rows.saturating_sub(1));
                    selection.drag_to(self.window_start + row, mouse.column as usize);
                }
            }
            MouseEventKind::Up(MouseButton::Left) => self.finish_press(),
            _ => {}
        }
    }

    fn finish_press(&mut self) {
        let Some(selection) = self.selection.take() else {
            return;
        };
        if selection.dragged || selection.grain != Grain::Char {
            let retained = &mut self.retained;
            let theme = &self.theme;
            let text = selection.extract(&mut |line| retained.text_of(line, theme));
            if !text.trim().is_empty() {
                let escape = osc::copy_to_clipboard(self.caps, &text);
                if !escape.is_empty() {
                    self.splice_raw(&escape);
                    self.flash = Some(("Copied.".to_string(), Instant::now()));
                }
            }
            self.selection = Some(selection);
            return;
        }
        let (line, col) = selection.anchor;
        if let Some(url) = self.retained.link_at(line, col, &self.theme) {
            osc::open_url(&url);
            self.flash = Some((format!("Opened {url}"), Instant::now()));
        }
    }

    /// Scroll the transcript: positive is up into history, negative back toward the live end.
    /// Reaching the end resumes following.
    pub fn scroll(&mut self, up: isize) {
        self.retained.scroll(up);
    }

    pub fn page(&self) -> isize {
        self.view_rows.saturating_sub(1).max(1) as isize
    }

    fn transcript_width(&self) -> u16 {
        self.cols
    }

    fn entry_width(&self) -> usize {
        (self.cols as usize)
            .saturating_sub(3 + wrap::width(&self.prompt))
            .max(8)
    }

    pub fn paint(&mut self) {
        let cols = self.cols as usize;
        let mut dock: Vec<Line> = Vec::new();
        let below = self.retained.scrolled();
        if below > 0 {
            let noun = if below == 1 { "line" } else { "lines" };
            dock.push(Line::styled(
                format!(" ↓ {below} {noun} below — End follows"),
                self.theme.accent,
            ));
        }
        dock.push(self.activity_line(cols));
        dock.extend(self.op_log.iter().cloned());
        let rule = || Line::styled("─".repeat(cols.saturating_sub(1)), self.theme.prompt);
        dock.push(rule());
        self.queued_rows(&mut dock, cols);
        let (entry, cursor_in_entry) = self.entry_rows(cols);
        let entry_at = dock.len();
        dock.extend(entry);
        dock.push(rule());
        dock.push(status::footer(
            &self.theme,
            self.cols,
            &self.host,
            &self.channel,
        ));

        let avail = (self.rows as usize).saturating_sub(dock.len()).max(1);
        self.view_rows = avail;
        let live = self.live_tail();
        let window = self.retained.window(avail, &live, &self.theme);
        self.window_start = window.start;
        let mut frame = window.lines;
        let retained = &mut self.retained;
        let theme = &self.theme;
        if let Some(selection) = self.selection.as_ref() {
            for (index, line) in frame.iter_mut().enumerate() {
                let at = window.start + index;
                let text = retained.text_of(at, theme);
                if let Some((from, to)) = selection.cols_for(at, &text) {
                    *line = highlight_columns(line.clone(), from, to);
                }
            }
        }
        frame.extend(dock);
        let cursor =
            cursor_in_entry.map(|(row, col)| ((avail + entry_at + row) as u16, col as u16));
        let _ = self.screen.frame(&frame, cursor);
    }

    /// The reply as it stands, rendered live while it streams — a block does not wait for its
    /// close to be readable. Scrolled away from the end, the live tail yields to history.
    fn live_tail(&self) -> Vec<Line<'static>> {
        let tail = self.stream.open_tail();
        if tail.trim().is_empty() || self.retained.scrolled() > 0 {
            return Vec::new();
        }
        markdown::render(
            tail.trim_end_matches('\n'),
            &self.theme,
            self.transcript_width(),
        )
    }

    fn activity_line(&self, width: usize) -> Line<'static> {
        if let Some((said, at)) = self.flash.as_ref() {
            if at.elapsed().as_secs() < FLASH_SECONDS {
                let said = format!(" {said}");
                return Line::styled(wrap::clip(&said, width).to_string(), self.theme.muted);
            }
        }
        if let Some(view) = self.running_op.as_ref() {
            let dot = if self.status.phase().is_multiple_of(2) {
                self.theme.accent
            } else {
                self.theme.muted
            };
            let said = view.title(self.running_desc.as_deref());
            let title = wrap::clip(&said, width.saturating_sub(4)).to_string();
            return Line::from(vec![
                Span::styled("  ⏺ ".to_string(), dot),
                Span::styled(title, self.theme.tool_title),
            ]);
        }
        self.status.render(&self.theme, width as u16)
    }

    fn queued_rows(&self, dock: &mut Vec<Line<'static>>, width: usize) {
        for held in self.queued.iter().take(QUEUE_SHOWN) {
            let first = held.text.lines().next().unwrap_or("");
            let row = format!(
                "{PROMPT_IDLE} {}",
                wrap::clip(first, width.saturating_sub(4))
            );
            dock.push(Line::styled(row, self.theme.queued));
        }
        let hidden = self.queued.len().saturating_sub(QUEUE_SHOWN);
        if hidden > 0 {
            dock.push(Line::styled(
                format!("… +{hidden} queued"),
                self.theme.queued,
            ));
        }
    }

    fn entry_rows(&self, width: usize) -> (Vec<Line<'static>>, Option<(usize, usize)>) {
        match self.focus {
            Focus::Compose => self.compose_rows(),
            Focus::Choose => (self.choose_rows(width), None),
            Focus::Secret => self.secret_rows(width),
            Focus::Path => self.path_rows(width),
            Focus::Keys => (self.keys_rows(width), None),
        }
    }

    fn compose_rows(&self) -> (Vec<Line<'static>>, Option<(usize, usize)>) {
        let width = self.entry_width();
        let layout = self.ask.render(width);
        let prompt_w = wrap::width(&self.prompt);
        let total = layout.rows.len();
        let window = ENTRY_ROWS_MAX.min(total.max(1));
        let first = layout
            .cursor_row
            .saturating_sub(window - 1)
            .min(total.saturating_sub(window));
        let mut rows = Vec::new();
        let mut cursor = None;
        for (index, row) in layout.rows.iter().enumerate().skip(first).take(window) {
            let lead = if index == 0 {
                Span::styled(format!("{} ", self.prompt), self.theme.prompt)
            } else {
                Span::raw(ECHO_INDENT.to_string())
            };
            let shown = rows.len();
            if index == layout.cursor_row {
                let lead_w = if index == 0 { prompt_w + 1 } else { 2 };
                cursor = Some((shown, lead_w + layout.cursor_col));
            }
            rows.push(Line::from(vec![lead, Span::raw(row.clone())]));
        }
        if rows.is_empty() {
            rows.push(Line::from(Span::styled(
                self.prompt.clone(),
                self.theme.prompt,
            )));
            cursor = Some((0, prompt_w + 1));
        }
        (rows, cursor)
    }

    fn choose_rows(&self, width: usize) -> Vec<Line<'static>> {
        let Some(chooser) = self.chooser.as_ref() else {
            return Vec::new();
        };
        let mut rows = vec![Line::styled(
            wrap::clip(&chooser.prompt, width).to_string(),
            self.theme.heading,
        )];
        if chooser.picker.visible_len() == 0 && !chooser.picker.filter.is_empty() {
            rows.push(Line::styled(
                "Nothing matches.".to_string(),
                self.theme.muted,
            ));
        } else {
            rows.extend(
                chooser
                    .picker
                    .render(&self.theme, width as u16, PICKER_ROWS),
            );
        }
        rows
    }

    fn secret_rows(&self, width: usize) -> (Vec<Line<'static>>, Option<(usize, usize)>) {
        let Some(entry) = self.secret.as_ref() else {
            return (Vec::new(), None);
        };
        let label = format!("{} (hidden): ", wrap::clip(&entry.prompt, width / 2));
        let mask = "•".repeat(entry.value.chars().count());
        let col = wrap::width(&label) + mask.chars().count();
        let row = Line::from(vec![
            Span::styled(label, self.theme.prompt),
            Span::raw(mask),
        ]);
        (vec![row], Some((0, col)))
    }

    fn path_rows(&self, width: usize) -> (Vec<Line<'static>>, Option<(usize, usize)>) {
        let (mut rows, cursor) = self.compose_rows();
        if let Some(pick) = self.path_pick.as_ref() {
            rows.extend(pick.picker.render(&self.theme, width as u16, PICKER_ROWS));
        }
        (rows, cursor)
    }

    fn keys_rows(&self, width: usize) -> Vec<Line<'static>> {
        let mut pairs = history::hotkeys();
        if self.caps.osc52 {
            pairs.push(("Ctrl+O", "Copy last reply"));
        }
        let mut rows = Vec::new();
        for (key, action) in pairs {
            let pad = " ".repeat(KEY_COL.saturating_sub(wrap::width(key)));
            rows.push(Line::from(vec![
                Span::styled(format!("{key}{pad}"), self.theme.accent),
                Span::styled(
                    wrap::clip(action, width.saturating_sub(KEY_COL)).to_string(),
                    self.theme.muted,
                ),
            ]));
        }
        rows
    }

    /// Anything the stream still holds commits now: a transcript element that is not part of the
    /// reply is about to land, and the held block stands before it.
    fn flush_stream(&mut self) {
        let source = self.stream.finish();
        self.commit_reply(source);
    }

    fn splice_raw(&mut self, bytes: &str) {
        if bytes.is_empty() {
            return;
        }
        let mut out = io::stdout();
        let _ = out.write_all(bytes.as_bytes());
        let _ = out.flush();
    }

    /// Leave the alternate screen and print the whole conversation into the terminal's own
    /// scrollback, images last — the session ends, the transcript stays at its final width.
    pub fn close(&mut self) {
        self.flush_stream();
        let document = self.retained.document(&self.theme);
        let _ = self.screen.leave();
        let _ = self.screen.print_document(&document);
        for blob in std::mem::take(&mut self.exit_images) {
            self.splice_raw(&blob);
            self.splice_raw("\r\n");
        }
    }
}

/// `line` with the span between two display columns drawn in reverse video.
fn highlight_columns(line: Line<'static>, from: usize, to: usize) -> Line<'static> {
    let base = line.style;
    let mut spans: Vec<Span<'static>> = Vec::new();
    let mut at = 0usize;
    for span in line.spans {
        let mut plain = String::new();
        let mut lit = false;
        let flush = |spans: &mut Vec<Span<'static>>, text: &mut String, lit: bool, style: Style| {
            if text.is_empty() {
                return;
            }
            let style = if lit {
                style.add_modifier(Modifier::REVERSED)
            } else {
                style
            };
            spans.push(Span::styled(std::mem::take(text), style));
        };
        let style = base.patch(span.style);
        for (unit, step) in wrap::units(&span.content) {
            if step == 0 {
                plain.push_str(unit);
                continue;
            }
            let inside = at >= from && at < to;
            if inside != lit {
                flush(&mut spans, &mut plain, lit, style);
                lit = inside;
            }
            plain.push_str(unit);
            at += step;
        }
        flush(&mut spans, &mut plain, lit, style);
    }
    let mut width = at;
    if width < to {
        let pad_from = width.max(from);
        if to > pad_from {
            while width < pad_from {
                spans.push(Span::raw(" "));
                width += 1;
            }
            spans.push(Span::styled(
                " ".repeat(to.min(pad_from + 200) - pad_from),
                Style::new().add_modifier(Modifier::REVERSED),
            ));
        }
    }
    Line::from(spans)
}

/// The terminal's size, floored to something drawable — a pty that reports no size gets the
/// classic 80×24.
fn sane_size() -> (u16, u16) {
    let (cols, rows) = terminal::size().unwrap_or((80, 24));
    (
        if cols >= 20 { cols } else { 80 },
        if rows >= 5 { rows } else { 24 },
    )
}

/// Decode one key event to a picker key.
pub fn pick_key(key: KeyEvent) -> Option<PickKey> {
    let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
    Some(match key.code {
        KeyCode::Up => PickKey::Up,
        KeyCode::Down => PickKey::Down,
        KeyCode::PageUp => PickKey::PageUp,
        KeyCode::PageDown => PickKey::PageDown,
        KeyCode::Enter => PickKey::Enter,
        KeyCode::Esc => PickKey::Esc,
        KeyCode::Backspace => PickKey::Backspace,
        KeyCode::Char('c') | KeyCode::Char('d') if ctrl => PickKey::Esc,
        KeyCode::Char('k') if ctrl => PickKey::Up,
        KeyCode::Char('j') if ctrl => PickKey::Down,
        KeyCode::Char(ch) if !ctrl => PickKey::Char(ch),
        _ => return None,
    })
}

/// Decode one key event to an editor key.
pub fn decode_key(key: KeyEvent) -> Option<Key> {
    let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
    let alt = key.modifiers.contains(KeyModifiers::ALT);
    let shift = key.modifiers.contains(KeyModifiers::SHIFT);
    Some(match key.code {
        KeyCode::Enter if shift => Key::ShiftEnter,
        KeyCode::Enter if alt => Key::InsertNewline,
        KeyCode::Enter => Key::Enter,
        KeyCode::Backspace if alt => Key::KillWord,
        KeyCode::Backspace => Key::Backspace,
        KeyCode::Delete => Key::Delete,
        KeyCode::Left if ctrl || alt => Key::WordLeft,
        KeyCode::Left => Key::Left,
        KeyCode::Right if ctrl || alt => Key::WordRight,
        KeyCode::Right => Key::Right,
        KeyCode::Up => Key::CursorUp,
        KeyCode::Down => Key::CursorDown,
        KeyCode::Home if ctrl => Key::BufferHome,
        KeyCode::End if ctrl => Key::BufferEnd,
        KeyCode::Home => Key::Home,
        KeyCode::End => Key::End,
        KeyCode::Char('a') if ctrl => Key::Home,
        KeyCode::Char('e') if ctrl => Key::End,
        KeyCode::Char('u') if ctrl => Key::KillLine,
        KeyCode::Char('k') if ctrl => Key::KillToEnd,
        KeyCode::Char('w') if ctrl => Key::KillWord,
        KeyCode::Char('y') if ctrl => Key::Yank,
        KeyCode::Char('z') if ctrl => Key::Undo,
        KeyCode::Char('j') if ctrl => Key::InsertNewline,
        KeyCode::Char('p') if ctrl => Key::HistPrev,
        KeyCode::Char('n') if ctrl => Key::HistNext,
        KeyCode::Char('d') if ctrl => Key::Eof,
        KeyCode::Char('b') if alt => Key::WordLeft,
        KeyCode::Char('f') if alt => Key::WordRight,
        KeyCode::Char(ch) if !ctrl && !alt => Key::Char(ch),
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shared_line() -> Line<'static> {
        let open = osc::link_open("https://ufo.test/artifacts/abc?exp=1&sig=2");
        let said = format!("shared {open}name{} (12 bytes)", osc::LINK_CLOSE);
        Line::styled(said, Style::new())
    }

    fn visible(text: &str) -> String {
        wrap::units(text)
            .into_iter()
            .filter(|(_, step)| *step > 0)
            .map(|(unit, _)| unit)
            .collect()
    }

    #[test]
    fn a_hyperlink_survives_highlight_whole() {
        let lit = highlight_columns(shared_line(), 8, 9);
        let joined: String = lit.spans.iter().map(|span| span.content.as_ref()).collect();
        let original: String = shared_line()
            .spans
            .iter()
            .map(|span| span.content.as_ref())
            .collect();
        assert_eq!(joined, original);
        for span in &lit.spans {
            let opens = span.content.matches('\x1b').count();
            let closes = span.content.matches('\x07').count();
            assert_eq!(
                opens, closes,
                "escape torn across spans: {:?}",
                span.content
            );
        }
    }

    #[test]
    fn highlight_lands_on_visible_columns() {
        let lit = highlight_columns(shared_line(), 7, 11);
        let reversed: String = lit
            .spans
            .iter()
            .filter(|span| span.style.add_modifier.contains(Modifier::REVERSED))
            .map(|span| visible(&span.content))
            .collect();
        assert_eq!(reversed, "name");
    }

    #[test]
    fn highlight_pads_past_the_visible_width() {
        let lit = highlight_columns(shared_line(), 0, 30);
        let width: usize = lit
            .spans
            .iter()
            .map(|span| wrap::width(&visible(&span.content)))
            .sum();
        assert_eq!(width, 30);
    }
}
