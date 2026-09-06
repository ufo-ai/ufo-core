//! The terminal app: a scrollback transcript over a repainted dock — activity row, rule, queued
//! sends, the composer (or a picker, a masked secret entry, or the hotkey sheet), rule, footer.
//! The dock states what is happening now; what happened is the transcript's.
//! Every member-visible string renders through the theme's roles.

pub mod conversations;
pub mod editor;
pub mod history;
pub mod markdown;
pub mod masthead;
pub mod osc;
pub mod picker;
pub mod plain;
pub mod probe;
pub mod retained;
pub mod select;
pub mod status;
pub mod term;
pub mod theme;
pub mod toolrender;
mod wrap;

use std::collections::{HashMap, HashSet, VecDeque};
use std::io::{self, Write};
use std::ops::Range;
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

use crate::pr::Pr;
use crate::ui::conversations::{labeled, Cache, Conversations, Fetch, Pick, Slot, NEW_CHAT_LABEL};
use crate::ui::editor::{AskState, Key, Outcome};
use crate::ui::history::History;
use crate::ui::osc::{Caps, ImageProtocol};
use crate::ui::picker::{PickKey, PickOutcome, Picker};
use crate::ui::probe::Probe;
use crate::ui::retained::{Entry, Retained, Step};
use crate::ui::select::{ClickTracker, Grain, Selection};
use crate::ui::status::{Activity, Progress, Signals, StatusRow};
use crate::ui::term::AltScreen;
use crate::ui::theme::{ColorMode, Theme};
use crate::ui::toolrender::OpView;
use crate::wire::{ConversationRow, OpRequest, Target};

pub const PROMPT_IDLE: &str = "›";
/// The caret on the input that holds the cursor — the composer, a list row, the page's search or
/// entry line; `PROMPT_IDLE` marks the same lines when the cursor is elsewhere.
pub const FOCUS_CARET: &str = "❯";
const SERVER_PROMPT: &str = ">";
const READ_ONLY_MESSAGE: &str =
    "This conversation is read-only here. Reply in {surface} to continue it.";
const QUEUE_SHOWN: usize = 3;
const ENTRY_ROWS_MAX: usize = 8;
const PICKER_ROWS: usize = 8;
const ECHO_INDENT: &str = "  ";
const IMAGE_COLS_MAX: u16 = 60;
const IMAGE_BYTES_MAX: usize = 2 * 1024 * 1024;
const KEY_COL: usize = 26;
const FLASH_SECONDS: u64 = 2;
const EARLY_ABSORBED_MAX: usize = 64;

/// Whether a server note narrates the agent's work: a call or a skill load, the agent's own or one
/// a subagent made under its label. Such a note is a step of the turn; a note the client writes
/// about itself, and the line a rolled-up turn states, are not.
pub fn narrates_activity(text: &str) -> bool {
    let under_a_label = text.split_once(": ").map_or(text, |(_, rest)| rest);
    [text, under_a_label]
        .iter()
        .any(|said| said.starts_with("running ") || said.starts_with("loading skill"))
}

/// The run a note narrates under: a subagent's dispatch states the run's name before the call it
/// made, and the agent's own dispatch states no name.
pub fn run_label(text: &str) -> Option<&str> {
    let (label, made) = text.split_once(": ")?;
    narrates_activity(made).then_some(label)
}

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
    /// Take the terminal, and ask it what it is while nothing else is reading it: raw mode first,
    /// or the reply is line-buffered; the probe next, holding the tty alone; then the modes whose
    /// own events would otherwise land in the probe's read, and the flags the probe asked about.
    ///
    /// The probe's scheme and the keys the member typed into it come back to the caller, which is
    /// the only reader of either.
    pub fn enter() -> (RawGuard, Probe) {
        #[cfg(unix)]
        crate::interrupt::hold_modes();
        let _ = terminal::enable_raw_mode();
        let probe = Probe::query();
        let _ = crossterm::execute!(
            io::stdout(),
            EnableBracketedPaste,
            EnableMouseCapture,
            EnableFocusChange
        );
        if probe.kitty {
            let _ = crossterm::execute!(
                io::stdout(),
                PushKeyboardEnhancementFlags(KeyboardEnhancementFlags::DISAMBIGUATE_ESCAPE_CODES)
            );
        }
        (RawGuard { kitty: probe.kitty }, probe)
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

/// The entry a Ctrl+V was pressed in. The read runs off the loop, so the result names the entry
/// it was meant for and lands only there — never in whichever entry holds focus when it returns.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ClipEntry {
    Compose,
    Secret,
    Path,
}

/// What a key did, for the loop to route.
#[derive(Debug, Clone, PartialEq)]
pub enum Reply {
    None,
    Send(String),
    Clipboard(ClipEntry),
    Attach(std::path::PathBuf),
    Recall {
        text: String,
        arrival_id: String,
    },
    Choice(String),
    ChoiceCancelled,
    Secret(String),
    Stop,
    Detach,
    /// The member asked for the conversation page.
    OpenConversations,
    /// The member left the page for the conversation underneath it.
    CloseConversations,
    /// The member picked a conversation to open.
    Open(ConversationRow),
    /// The member typed into the page's entry bar: a fresh terminal conversation opening with
    /// these words.
    NewChat(String),
    Exit,
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum Focus {
    Compose,
    Choose,
    Secret,
    Path,
    Keys,
    Conversations,
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
pub struct App<W: Write = io::Stdout> {
    pub theme: Theme,
    pub caps: Caps,
    signals: Signals,
    progress: Progress,
    screen: AltScreen<W>,
    status: StatusRow,
    stream: markdown::StreamRenderer,
    ask: AskState,
    prompt: String,
    history: History,
    queued: VecDeque<QueuedSend>,
    early_absorbed: Vec<String>,
    focus: Focus,
    behind: Focus,
    chooser: Option<Chooser>,
    secret: Option<SecretEntry>,
    path_pick: Option<PathPick>,
    conversations: Option<Conversations>,
    cache: Cache,
    cached: Vec<ConversationRow>,
    page_draft: AskState,
    page_hit: Option<(u16, Range<u16>)>,
    seen: HashMap<String, f64>,
    live_target: Option<Target>,
    read_only: Option<String>,
    retained: Retained,
    reply_open: bool,
    view_rows: usize,
    window_start: usize,
    hover: Option<(u16, u16)>,
    exit_images: Vec<String>,
    selection: Option<Selection>,
    clicks: ClickTracker,
    focused: bool,
    flash: Option<(String, Instant)>,
    running_op: Option<OpView>,
    running_desc: Option<String>,
    narration: Option<(String, String)>,
    runs_counted: HashSet<String>,
    last_reply: String,
    host: String,
    channel: String,
    pr: Option<Pr>,
    pr_hit: Option<(u16, Range<usize>)>,
    list_hit: Option<(u16, Range<usize>)>,
    cwd: PathBuf,
    working: bool,
    cols: u16,
    rows: u16,
}

impl<W: Write> App<W> {
    pub fn new(
        out: W,
        home_root: &std::path::Path,
        session_id: &str,
        theme: Theme,
        host: String,
        channel: String,
        cwd: PathBuf,
    ) -> App<W> {
        let caps = Caps::detect();
        let (cols, rows) = sane_size();
        let mut screen = AltScreen::new(out, theme.mode);
        let _ = screen.enter();
        let cache = Cache::at(home_root, session_id);
        let cached = cache.load();
        App {
            signals: Signals {
                enabled: theme.mode != ColorMode::Plain,
            },
            progress: Progress::new(),
            screen,
            status: StatusRow::new(),
            stream: markdown::StreamRenderer::default(),
            ask: AskState::default(),
            prompt: PROMPT_IDLE.to_string(),
            history: History::load(home_root),
            queued: VecDeque::new(),
            early_absorbed: Vec::new(),
            focus: Focus::Compose,
            behind: Focus::Compose,
            chooser: None,
            secret: None,
            path_pick: None,
            conversations: None,
            cache,
            cached,
            page_draft: AskState::default(),
            page_hit: None,
            seen: HashMap::new(),
            live_target: None,
            read_only: None,
            retained: Retained::new(cols),
            reply_open: false,
            view_rows: 1,
            window_start: 0,
            hover: None,
            exit_images: Vec::new(),
            selection: None,
            clicks: ClickTracker::new(),
            focused: true,
            flash: None,
            running_op: None,
            running_desc: None,
            narration: None,
            runs_counted: HashSet::new(),
            last_reply: String::new(),
            host,
            channel,
            pr: None,
            pr_hit: None,
            list_hit: None,
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
        self.live_target = Some(Target::Channel(channel.clone()));
        self.channel = channel;
    }

    /// Name the conversation on the wire — what the page tells apart from the rest, so a reply
    /// landing in it while the page is up is drawn as news.
    pub fn set_live_target(&mut self, target: Target) {
        self.live_target = Some(target);
    }

    /// The member opened `row` from the page: it stands seen where it is.
    pub fn mark_seen(&mut self, row: &ConversationRow) {
        self.seen.insert(row.id.clone(), row.last_at);
    }

    /// Start over on another conversation: the transcript, the composer, and every mark of the
    /// turn that was running are dropped, and the mark heads the new transcript. `read_only` names
    /// the surface to reply in where this one takes no message from here.
    pub fn reset_conversation(
        &mut self,
        target: &Target,
        channel: String,
        read_only: Option<String>,
    ) {
        self.stream = markdown::StreamRenderer::default();
        self.ask = AskState::default();
        self.prompt = PROMPT_IDLE.to_string();
        self.queued.clear();
        self.early_absorbed.clear();
        self.focus = Focus::Compose;
        self.behind = Focus::Compose;
        self.chooser = None;
        self.secret = None;
        self.path_pick = None;
        self.conversations = None;
        self.page_draft = AskState::default();
        self.page_hit = None;
        self.live_target = Some(target.clone());
        self.read_only = read_only;
        self.retained = Retained::new(self.cols);
        self.reply_open = false;
        self.window_start = 0;
        self.hover = None;
        self.exit_images.clear();
        self.selection = None;
        self.flash = None;
        self.running_op = None;
        self.running_desc = None;
        self.narration = None;
        self.runs_counted.clear();
        self.last_reply.clear();
        self.channel = channel;
        self.working = false;
        self.status = StatusRow::new();
        let off = self.progress.off(&self.signals);
        self.splice_raw(&off);
        self.masthead();
        self.screen.invalidate();
    }

    /// Open the conversation page over whatever is showing — on the list as it last stood, so
    /// it reads at once — and name the fetch it opens with. `back` says whether Esc has a
    /// conversation to return to.
    pub fn open_conversations(&mut self, back: bool) -> Fetch {
        let page = Conversations::new(back, self.cached.clone(), &self.seen);
        let fetch = page.first_fetch();
        self.conversations = Some(page);
        if self.focus != Focus::Conversations {
            self.behind = self.focus;
            self.page_draft = std::mem::take(&mut self.ask);
        }
        self.focus = Focus::Conversations;
        self.screen.invalidate();
        fetch
    }

    /// Leave the page for the conversation underneath it, in whatever state its stream left it
    /// while the page was up — the prompt, a chooser, a secret entry — with the draft the member
    /// had been typing there back in the entry.
    pub fn close_conversations(&mut self) {
        self.conversations = None;
        self.page_hit = None;
        if self.focus == Focus::Conversations {
            self.focus = self.behind;
            self.ask = std::mem::take(&mut self.page_draft);
        }
        self.behind = Focus::Compose;
        self.screen.invalidate();
    }

    /// Where the stream puts the member's input next. While the page is up it waits behind the
    /// page rather than taking it down: a reply landing in the conversation is news the page
    /// draws, never a reason to leave it.
    fn take_focus(&mut self, focus: Focus) {
        if self.focus == Focus::Conversations {
            self.behind = focus;
        } else {
            self.focus = focus;
        }
    }

    /// One conversation fetch answered — dropped when the page has since closed. The whole list,
    /// when that is what landed and it changed, is what the page opens on next time, here and in
    /// the next process of this sign-in.
    pub fn conversations_loaded(
        &mut self,
        generation: u32,
        result: Result<Vec<ConversationRow>, String>,
    ) {
        let Some(page) = self.conversations.as_mut() else {
            return;
        };
        let whole = page.loaded(
            generation,
            result,
            &mut self.seen,
            self.live_target.as_ref(),
        );
        if whole && page.rows() != self.cached.as_slice() {
            self.cached = page.rows().to_vec();
            self.cache.store(&self.cached);
        }
    }

    /// The fetch the page's typed words call for now, if any.
    pub fn conversations_due_fetch(&mut self, now: Instant) -> Option<Fetch> {
        self.conversations.as_mut()?.due_fetch(now)
    }

    /// One key on the page. Esc and Ctrl+K close it; Up, Down, PageUp and PageDown walk the
    /// column; Enter opens the highlighted row from the search line or the list and starts a new
    /// chat from the entry bar; every other key writes where the cursor stands.
    fn conversations_key(&mut self, key: KeyEvent) -> Reply {
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        let Some(page) = self.conversations.as_mut() else {
            self.focus = Focus::Compose;
            return Reply::None;
        };
        let pick = match key.code {
            KeyCode::Char('k') if ctrl => Pick::Close,
            KeyCode::Esc => Pick::Close,
            KeyCode::Up | KeyCode::Down | KeyCode::PageUp | KeyCode::PageDown => {
                match pick_key(key) {
                    Some(pick) => page.key(pick),
                    None => Pick::None,
                }
            }
            KeyCode::Tab => {
                page.cycle(true);
                Pick::None
            }
            KeyCode::BackTab => {
                page.cycle(false);
                Pick::None
            }
            KeyCode::End => {
                page.set_slot(Slot::Entry);
                Pick::None
            }
            KeyCode::Home => {
                page.home();
                Pick::None
            }
            KeyCode::Enter if page.slot() == Slot::Entry => {
                let text = self.ask.expand();
                self.ask = AskState::default();
                if text.trim().is_empty() {
                    return Reply::None;
                }
                return Reply::NewChat(text);
            }
            KeyCode::Char('v') if ctrl && page.slot() == Slot::Entry => {
                return Reply::Clipboard(ClipEntry::Compose);
            }
            _ if page.slot() == Slot::Entry => {
                let Some(decoded) = decode_key(key) else {
                    return Reply::None;
                };
                let width = self.entry_width();
                return match self.ask.apply(decoded, &self.history.entries, width) {
                    Outcome::Cancel => Reply::Exit,
                    Outcome::Continue | Outcome::Submit => Reply::None,
                };
            }
            _ => match pick_key(key) {
                Some(pick) => page.key(pick),
                None => Pick::None,
            },
        };
        self.conversations_step(pick)
    }

    fn conversations_step(&mut self, pick: Pick) -> Reply {
        match pick {
            Pick::None => Reply::None,
            Pick::Open(row) => Reply::Open(row),
            Pick::Close => {
                let back = self.conversations.as_ref().is_some_and(|page| page.back());
                if back {
                    self.close_conversations();
                    Reply::CloseConversations
                } else {
                    Reply::Exit
                }
            }
        }
    }

    pub fn set_pr(&mut self, pr: Option<Pr>) {
        self.pr = pr;
    }

    /// The mark, at the head of the transcript.
    pub fn masthead(&mut self) {
        self.retained.push(Entry::Masthead);
    }

    // ── directives ────────────────────────────────────────────────────────────────────────────

    pub fn say(&mut self, text: &str) {
        self.flush_stream();
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
        if self.reply_open {
            self.retained.extend_markdown(&source);
        } else if !source.trim().is_empty() {
            self.retained.push(Entry::Markdown(source));
            self.reply_open = true;
        }
    }

    /// A server note. Tool narration — the activity the client also states for its own ops —
    /// states the current step in the activity row and is a step of the turn's rollup, ranked with
    /// the thoughts written between the calls. A run the turn already counted narrates its further
    /// dispatches as rows of that one step, so a run counts once however much it did. A note the
    /// client writes about itself is no step of the agent's work and joins the transcript on its
    /// own.
    pub fn note(&mut self, text: &str) {
        if text.starts_with("running ") {
            self.narrate(text);
        } else if text.starts_with("loading skill") {
            self.status_text(text);
        }
        if narrates_activity(text) {
            let Some(label) = run_label(text) else {
                self.step(Step::Note(text.to_string()), true);
                return;
            };
            let row = text[label.len() + ": ".len()..].to_string();
            match self.runs_counted.insert(label.to_string()) {
                true => self.step(
                    Step::Run {
                        label: label.to_string(),
                        rows: vec![row],
                        opened: false,
                    },
                    false,
                ),
                false => {
                    self.flush_stream();
                    self.retained.push_under(label, row);
                }
            }
            return;
        }
        self.flush_stream();
        self.retained.push(Entry::Note(text.to_string()));
    }

    pub fn activity(&mut self, text: &str, run: Option<&str>) {
        let Some(label) = run else {
            self.narrate(text);
            self.step(Step::Note(text.to_string()), true);
            return;
        };
        self.status_text(text);
        let prefix = format!("{label}: ");
        let row = text.strip_prefix(&prefix).unwrap_or(text).to_string();
        match self.runs_counted.insert(label.to_string()) {
            true => self.step(
                Step::Run {
                    label: label.to_string(),
                    rows: vec![row],
                    opened: false,
                },
                false,
            ),
            false => {
                self.flush_stream();
                self.retained.push_under(label, row);
            }
        }
    }

    /// The call a narration names, held for the op that answers it: the client states one call
    /// once, under the words the agent wrote for it, whichever directive carried them.
    fn narrate(&mut self, text: &str) {
        if let Some((tool, detail)) = text
            .strip_prefix("running ")
            .and_then(|rest| rest.split_once(": "))
        {
            self.narration = Some((tool.to_string(), detail.to_string()));
        }
        self.status_text(text);
    }

    /// One step of the running turn, `own` for a dispatch the turn made itself. Its own dispatch
    /// stands the open reply before it as the thought it is — text a round wrote before it
    /// dispatched work is intermediate by definition — so the reply the turn closes on is the
    /// answer, and the steps behind it are what its end rolls up. A run narrates at its own pace: a
    /// background run states its calls while the parent writes that closing answer, so a row of the
    /// run says nothing about where the parent's words end and takes none of them.
    fn step(&mut self, step: Step, own: bool) {
        self.flush_stream();
        if own && self.reply_open {
            if let Some(thought) = self.retained.take_reply() {
                self.retained.push_step(Step::Thought(thought));
                self.last_reply.clear();
            }
            self.reply_open = false;
        }
        self.retained.push_step(step);
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

    /// The op's header throbs in the activity row while it runs.
    pub fn op_started(&mut self, op: &OpRequest) {
        self.running_desc = match self.narration.take() {
            Some((tool, detail)) if tool == op.name || tool == op.kind => Some(detail),
            _ => None,
        };
        self.running_op = Some(OpView::from_request(op));
    }

    /// The answered op joins the turn's steps, its header over the rows its result showed —
    /// the work stands in the transcript where it happened, and the turn's end rolls it up with
    /// every other step. A call the agent narrated restates that narration's row rather than
    /// adding one of its own.
    pub fn op_finished(&mut self, op: &OpRequest, result: &Result<Vec<u8>, String>) {
        self.running_op = None;
        let narrated = self.running_desc.take();
        let view = OpView::from_request(op);
        let reply = match result {
            Ok(bytes) => Ok(bytes.as_slice()),
            Err(failure) => Err(failure.as_str()),
        };
        let step = Step::Op {
            header: view.header(narrated.as_deref(), &self.theme),
            body: view.body(reply, &self.theme),
        };
        match narrated {
            Some(said) => {
                self.flush_stream();
                self.retained.restate_step(&said, step);
            }
            None => self.step(step, true),
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
        self.retained.begin_turn();
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
        self.running_op = None;
        self.flush_stream();
        self.reply_open = false;
        self.retained.roll_up_steps();
        self.runs_counted.clear();
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
        if !focused {
            self.hover = None;
        }
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
        self.prompt = if prompt.is_empty() || prompt == SERVER_PROMPT {
            PROMPT_IDLE.to_string()
        } else {
            prompt.to_string()
        };
        self.take_focus(Focus::Compose);
    }

    pub fn choose(&mut self, prompt: &str, options: &[String]) {
        let mut picker = Picker::new(options.to_vec());
        picker.set_page(PICKER_ROWS);
        self.chooser = Some(Chooser {
            prompt: prompt.to_string(),
            picker,
        });
        self.take_focus(Focus::Choose);
    }

    pub fn secret_begin(&mut self, prompt: &str) {
        self.secret = Some(SecretEntry {
            prompt: prompt.to_string(),
            value: String::new(),
        });
        self.take_focus(Focus::Secret);
    }

    pub fn collecting_secret(&self) -> bool {
        self.secret.is_some()
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
            KeyCode::Char('t') if ctrl => {
                self.retained.toggle_steps();
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
            Focus::Conversations => self.conversations_key(key),
        }
    }

    /// Whether the entry a Ctrl+V was pressed in still holds input, so its clipboard result may
    /// land.
    pub fn entry_still(&self, entry: ClipEntry) -> bool {
        match entry {
            ClipEntry::Compose => {
                self.focus == Focus::Compose
                    || self
                        .conversations
                        .as_ref()
                        .is_some_and(|page| page.slot() == Slot::Entry)
            }
            ClipEntry::Secret => self.focus == Focus::Secret,
            ClipEntry::Path => self.focus == Focus::Path,
        }
    }

    /// A clipboard image lands in the ask as an `[Image #N]` marker; the send expands it to the
    /// stashed workspace-relative path.
    pub fn paste_image(&mut self, path: &str) {
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
                if let Some(source) = crate::clipboard::dropped_image(&text) {
                    return Reply::Attach(source);
                }
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
            Focus::Conversations => {
                if self
                    .conversations
                    .as_ref()
                    .is_some_and(|page| page.slot() == Slot::Entry)
                {
                    let width = self.entry_width();
                    self.ask
                        .apply(Key::Paste(text), &self.history.entries, width);
                }
            }
            Focus::Choose | Focus::Keys => {}
        }
        Reply::None
    }

    fn compose_key(&mut self, key: KeyEvent) -> Reply {
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        match key.code {
            KeyCode::Char('k') if ctrl => return Reply::OpenConversations,
            KeyCode::Char('?') if self.read_only.is_some() => {
                self.focus = Focus::Keys;
                return Reply::None;
            }
            KeyCode::Esc if self.read_only.is_some() && self.working => return Reply::Stop,
            _ if self.read_only.is_some() => return Reply::None,
            KeyCode::Up if !ctrl && self.ask.text.is_empty() && !self.queued.is_empty() => {
                return self.recall_queued();
            }
            KeyCode::Char('o') if ctrl => {
                self.copy_last_reply();
                return Reply::None;
            }
            KeyCode::Char('v') if ctrl => return Reply::Clipboard(ClipEntry::Compose),
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
                self.ask = AskState::default();
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
                self.ask = AskState::default();
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
            KeyCode::Char('v') if ctrl => Reply::Clipboard(ClipEntry::Secret),
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
                return Reply::Clipboard(ClipEntry::Path);
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

    /// Every mouse event: the wheel scrolls; a press flips the fold it lands on, or anchors a
    /// selection at the grain repeated clicks cycle to; a drag extends it, scrolling at the
    /// window's edges; releasing a drag or a widened grain copies it. A plain click opens the URL
    /// under it, or clears the selection. Motion is held for the fold affordance the paint draws.
    /// On the conversation page the wheel moves the selection and a click opens the row under it.
    pub fn on_mouse(&mut self, mouse: MouseEvent) -> Reply {
        if let (MouseEventKind::Down(MouseButton::Left), Some((row, columns))) =
            (mouse.kind, self.list_hit.as_ref())
        {
            if mouse.row == *row && columns.contains(&(mouse.column as usize)) {
                return if self.conversations.is_some() {
                    self.conversations_step(Pick::Close)
                } else {
                    Reply::OpenConversations
                };
            }
        }
        if let Some(page) = self.conversations.as_mut() {
            let pick = match mouse.kind {
                MouseEventKind::ScrollUp => {
                    page.scroll(-1);
                    Pick::None
                }
                MouseEventKind::ScrollDown => {
                    page.scroll(1);
                    Pick::None
                }
                MouseEventKind::Down(MouseButton::Left) => match self.page_hit.as_ref() {
                    Some((search, _)) if mouse.row == *search => {
                        page.set_slot(Slot::Search);
                        Pick::None
                    }
                    Some((_, entry)) if entry.contains(&mouse.row) => {
                        page.set_slot(Slot::Entry);
                        Pick::None
                    }
                    _ => page.click(mouse.row),
                },
                _ => Pick::None,
            };
            return self.conversations_step(pick);
        }
        match mouse.kind {
            MouseEventKind::ScrollUp => self.scroll(3),
            MouseEventKind::ScrollDown => self.scroll(-3),
            MouseEventKind::Moved => self.hover = Some((mouse.row, mouse.column)),
            MouseEventKind::Down(MouseButton::Left) => {
                if let (Some((row, columns)), Some(pr)) = (self.pr_hit.as_ref(), self.pr.as_ref()) {
                    if mouse.row == *row && columns.contains(&(mouse.column as usize)) {
                        osc::open_url(&pr.url);
                        self.flash = Some((format!("Opened {}", pr.url), Instant::now()));
                        self.selection = None;
                        return Reply::None;
                    }
                }
                if (mouse.row as usize) >= self.view_rows {
                    self.selection = None;
                    return Reply::None;
                }
                let at = (
                    self.window_start + mouse.row as usize,
                    mouse.column as usize,
                );
                if self.retained.toggle(at.0, at.1, &self.theme) {
                    self.selection = None;
                    return Reply::None;
                }
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
        Reply::None
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
        if self.focus == Focus::Conversations && self.conversations.is_some() {
            self.paint_conversations();
            return;
        }
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
        let rule = || Line::styled("─".repeat(cols.saturating_sub(1)), self.theme.prompt);
        dock.push(rule());
        self.queued_rows(&mut dock, cols);
        let (entry, cursor_in_entry) = self.entry_rows(cols);
        let entry_at = dock.len();
        dock.extend(entry);
        dock.push(rule());
        let (footer, hits) = status::footer(
            &self.theme,
            self.cols,
            &self.host,
            &self.channel,
            self.pr.as_ref(),
            Some(status::LIST_HINT),
        );
        dock.push(footer);

        let avail = (self.rows as usize).saturating_sub(dock.len()).max(1);
        self.view_rows = avail;
        let footer_row = (avail + dock.len() - 1) as u16;
        self.pr_hit = hits.pr.map(|columns| (footer_row, columns));
        self.list_hit = hits.list.map(|columns| (footer_row, columns));
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
        if let Some((row, col)) = self.hover {
            let row = row as usize;
            if row < avail && retained.is_toggle(window.start + row, col as usize, theme) {
                frame[row] = underline_line(frame[row].clone());
            }
        }
        frame.extend(dock);
        let cursor =
            cursor_in_entry.map(|(row, col)| ((avail + entry_at + row) as u16, col as u16));
        self.present(frame, cursor);
    }

    /// Hand the frame to the screen with every row cut at the screen's edge: the terminal
    /// soft-wraps a wider row and shifts every row below it, the entry bar included. Width is
    /// counted in painted units, so a link's escape costs nothing.
    fn present(&mut self, mut frame: Vec<Line<'static>>, cursor: Option<(u16, u16)>) {
        let cols = self.cols as usize;
        for line in frame.iter_mut() {
            if painted_width(line) > cols {
                *line = clip_line(line, cols);
            }
        }
        let _ = self.screen.frame(&frame, cursor);
    }

    /// The reply as it stands, rendered live while it streams — a block does not wait for its
    /// close to be readable. Scrolled away from the end, the live tail yields to history.
    fn live_tail(&self) -> Vec<Line<'static>> {
        let tail = self.stream.open_tail();
        if tail.trim().is_empty() || self.retained.scrolled() > 0 {
            return Vec::new();
        }
        markdown::render_live(
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

    /// The page: the mark, the heading and the list in the transcript area, and the dock below
    /// with the search line over the entry bar — the activity line and the footer still those of
    /// the conversation behind. The cursor stands where the page's column puts it.
    fn paint_conversations(&mut self) {
        let cols = self.cols as usize;
        let rule = || Line::styled("─".repeat(cols.saturating_sub(1)), self.theme.prompt);
        let mark = masthead::masthead(&self.theme, self.cols);
        let mut dock: Vec<Line> = vec![self.activity_line(cols), rule()];
        let page = self.conversations.as_ref().expect("the page is up");
        let (search, search_col) = page.search_line(&self.theme, self.cols);
        let search_at = dock.len();
        dock.push(search);
        dock.push(rule());
        let entry_prompt = labeled(NEW_CHAT_LABEL, page.slot() == Slot::Entry);
        let (entry, cursor_in_entry) = self.compose_rows_with(&entry_prompt);
        let entry_at = dock.len();
        let entry_rows = entry.len();
        dock.extend(entry);
        dock.push(rule());
        let (footer, hits) = status::footer(
            &self.theme,
            self.cols,
            &self.host,
            &self.channel,
            self.pr.as_ref(),
            None,
        );
        dock.push(footer);

        let avail = (self.rows as usize)
            .saturating_sub(dock.len() + mark.len())
            .max(1);
        let window = avail + mark.len();
        self.view_rows = 0;
        let footer_row = (window + dock.len() - 1) as u16;
        self.pr_hit = hits.pr.map(|columns| (footer_row, columns));
        self.list_hit = hits.list.map(|columns| (footer_row, columns));
        self.page_hit = Some((
            (window + search_at) as u16,
            (window + entry_at) as u16..(window + entry_at + entry_rows) as u16,
        ));
        let page = self.conversations.as_mut().expect("the page is up");
        page.set_layout(mark.len(), avail);
        let cursor = match page.slot() {
            Slot::Entry => {
                cursor_in_entry.map(|(row, col)| ((window + entry_at + row) as u16, col as u16))
            }
            Slot::Search => Some(((window + search_at) as u16, search_col)),
            Slot::List => None,
        };
        let mut frame = mark;
        frame.extend(page.render(&self.theme, self.cols, avail));
        frame.extend(dock);
        self.present(frame, cursor);
    }

    fn entry_rows(&self, width: usize) -> (Vec<Line<'static>>, Option<(usize, usize)>) {
        match self.focus {
            Focus::Compose => self.compose_rows(),
            Focus::Choose => (self.choose_rows(width), None),
            Focus::Secret => self.secret_rows(width),
            Focus::Path => self.path_rows(width),
            Focus::Keys => (self.keys_rows(width), None),
            Focus::Conversations => (Vec::new(), None),
        }
    }

    fn compose_rows(&self) -> (Vec<Line<'static>>, Option<(usize, usize)>) {
        if let Some(surface) = &self.read_only {
            let said = READ_ONLY_MESSAGE.replace("{surface}", surface);
            let row = Line::styled(
                wrap::clip(&said, self.cols as usize).to_string(),
                self.theme.muted,
            );
            return (vec![row], None);
        }
        self.compose_rows_with(&self.prompt)
    }

    /// The entry bar under `prompt`: the draft's rows, at most `ENTRY_ROWS_MAX`, with the cursor.
    fn compose_rows_with(&self, prompt: &str) -> (Vec<Line<'static>>, Option<(usize, usize)>) {
        let prompt = if prompt == PROMPT_IDLE {
            FOCUS_CARET
        } else {
            prompt
        };
        let width = (self.cols as usize)
            .saturating_sub(3 + wrap::width(prompt))
            .max(8);
        let layout = self.ask.render(width);
        let prompt_w = wrap::width(prompt);
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
                Span::styled(format!("{prompt} "), self.theme.prompt)
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
                prompt.to_string(),
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
        self.screen.splice(bytes);
    }

    /// Leave the alternate screen and print the whole conversation into the terminal's own
    /// scrollback, images last — the session ends, the transcript stays at its final width. The
    /// session's end caps its last turn, so a rollup the member never saw settle rolls up here.
    pub fn close(&mut self) {
        self.flush_stream();
        self.retained.roll_up_steps();
        let document = self.retained.document(&self.theme);
        let _ = self.screen.leave();
        let _ = self.screen.print_document(&document);
        for blob in std::mem::take(&mut self.exit_images) {
            self.splice_raw(&blob);
            self.splice_raw("\r\n");
        }
    }
}

/// `line` underlined whole — the affordance a hovered fold row takes.
/// The columns a row paints: an OSC escape is a zero-width unit, every other char at least one.
fn painted_width(line: &Line<'static>) -> usize {
    line.spans
        .iter()
        .flat_map(|span| wrap::units(&span.content))
        .map(|(_, step)| step)
        .sum()
}

/// A row cut to `width` painted columns. Visible units stop at the first that does not fit; every
/// zero-width unit still ships, so a link opened before the cut is closed after it.
fn clip_line(line: &Line<'static>, width: usize) -> Line<'static> {
    let mut left = width;
    let mut full = false;
    let mut spans = Vec::new();
    for span in &line.spans {
        let mut kept = String::new();
        for (unit, step) in wrap::units(&span.content) {
            if step == 0 {
                kept.push_str(unit);
            } else if !full && step <= left {
                left -= step;
                kept.push_str(unit);
            } else {
                full = true;
            }
        }
        if !kept.is_empty() {
            spans.push(Span::styled(kept, span.style));
        }
    }
    let mut cut = Line::from(spans).style(line.style);
    cut.alignment = line.alignment;
    cut
}

fn underline_line(line: Line<'static>) -> Line<'static> {
    let spans = line
        .spans
        .into_iter()
        .map(|span| {
            let style = span.style.add_modifier(Modifier::UNDERLINED);
            Span::styled(span.content, style)
        })
        .collect::<Vec<_>>();
    Line::from(spans).style(line.style.add_modifier(Modifier::UNDERLINED))
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

    /// An app whose screen writes into memory: no terminal is touched, and what it would have
    /// painted is readable. Each one gets a home of its own, because the composer's history is a
    /// file — a shared one would let a recall in a later test read what an earlier test typed.
    fn app_on_memory() -> App<Vec<u8>> {
        app_at(scratch_home(), "ufo.test", "host.1")
    }

    /// A home no other test writes into.
    fn scratch_home() -> PathBuf {
        static NEXT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let home = std::env::temp_dir().join(format!(
            "ufo-ui-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        let _ = std::fs::remove_dir_all(&home);
        std::fs::create_dir_all(&home).expect("a scratch home");
        home
    }

    /// An app on `home` whose screen writes into memory, signed in at `host` under `session`.
    fn app_at(home: PathBuf, host: &str, session: &str) -> App<Vec<u8>> {
        App::new(
            Vec::new(),
            &home.clone(),
            session,
            Theme::for_mode(ColorMode::TrueColor, theme::Scheme::Dark),
            host.to_string(),
            "host.1".to_string(),
            home,
        )
    }

    fn asked(op: &str, kind: &str, params: &str) -> OpRequest {
        OpRequest {
            op_id: "op1".to_string(),
            kind: kind.to_string(),
            name: op.to_string(),
            timeout_s: 30,
            arg: String::new(),
            params: params.to_string(),
        }
    }

    /// The whole transcript as one string, for asserting what a member can read back.
    fn transcript(app: &mut App<Vec<u8>>) -> String {
        let theme = app.theme.clone();
        app.retained
            .document(&theme)
            .iter()
            .map(Line::to_string)
            .collect::<Vec<String>>()
            .join("\n")
    }

    fn exec_reply(exit_code: i32, stdout: &str) -> String {
        format!(
            r#"{{"exit_code":{exit_code},"stdout_b64":"{}","stderr_b64":""}}"#,
            base64::Engine::encode(
                &base64::engine::general_purpose::STANDARD,
                stdout.as_bytes()
            )
        )
    }

    /// Every member message the transcript holds, in order — the caret marks them.
    fn members(app: &mut App<Vec<u8>>) -> Vec<String> {
        let theme = app.theme.clone();
        app.retained
            .document(&theme)
            .iter()
            .map(Line::to_string)
            .filter(|line| line.starts_with('›'))
            .map(|line| line.trim_start_matches('›').trim().to_string())
            .collect()
    }

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }

    fn ctrl(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::CONTROL)
    }

    fn typed(app: &mut App<Vec<u8>>, text: &str) {
        for character in text.chars() {
            app.on_key(key(KeyCode::Char(character)));
        }
    }

    #[test]
    fn enter_sends_what_was_typed_and_leaves_the_composer_empty() {
        let mut app = app_on_memory();
        typed(&mut app, "hello there");
        let reply = app.on_key(key(KeyCode::Enter));
        assert!(matches!(&reply, Reply::Send(text) if text == "hello there"));
        assert!(app.ask.text.is_empty());
    }

    #[test]
    fn escape_stops_a_running_turn_but_first_clears_a_draft() {
        let mut app = app_on_memory();
        app.begin_turn();
        typed(&mut app, "a draft");
        assert!(
            matches!(app.on_key(key(KeyCode::Esc)), Reply::None),
            "escape with a draft clears it rather than stopping the turn"
        );
        assert!(app.ask.text.is_empty());
        assert!(matches!(app.on_key(key(KeyCode::Esc)), Reply::Stop));
    }

    #[test]
    fn detach_is_offered_only_while_a_turn_runs() {
        let mut app = app_on_memory();
        assert!(matches!(app.on_key(ctrl(KeyCode::Char('b'))), Reply::None));
        app.begin_turn();
        assert!(matches!(
            app.on_key(ctrl(KeyCode::Char('b'))),
            Reply::Detach
        ));
    }

    #[test]
    fn the_hotkey_sheet_opens_on_an_empty_composer_and_any_key_closes_it() {
        let mut app = app_on_memory();
        typed(&mut app, "?");
        assert!(
            matches!(app.focus, Focus::Keys),
            "a lone question mark opens the sheet"
        );
        app.on_key(key(KeyCode::Char('x')));
        assert!(matches!(app.focus, Focus::Compose));
        typed(&mut app, "draft?");
        assert!(
            matches!(app.focus, Focus::Compose),
            "a question mark inside a draft is just a character"
        );
        assert_eq!(app.ask.text, "draft?");
    }

    #[test]
    fn ctrl_v_asks_for_the_clipboard_and_ctrl_o_copies_the_last_reply() {
        let mut app = app_on_memory();
        assert!(matches!(
            app.on_key(ctrl(KeyCode::Char('v'))),
            Reply::Clipboard(ClipEntry::Compose)
        ));
        app.last_reply = "the answer".to_string();
        app.caps.osc52 = false;
        app.on_key(ctrl(KeyCode::Char('o')));
        assert!(
            !String::from_utf8_lossy(app.screen.written()).contains("52;c;"),
            "a terminal that cannot take a clipboard sequence is never sent one"
        );
        app.caps.osc52 = true;
        app.on_key(ctrl(KeyCode::Char('o')));
        let painted = String::from_utf8_lossy(app.screen.written()).into_owned();
        assert!(
            painted.contains("52;c;"),
            "the reply is copied through the terminal's own clipboard: {painted:?}"
        );
    }

    #[test]
    fn a_chooser_answers_a_pick_and_a_cancel() {
        let mut app = app_on_memory();
        app.choose("which?", &["first".to_string(), "second".to_string()]);
        app.on_key(key(KeyCode::Down));
        let reply = app.on_key(key(KeyCode::Enter));
        assert!(matches!(&reply, Reply::Choice(choice) if choice == "second"));
        assert!(app.chooser.is_none());
        assert!(matches!(app.focus, Focus::Compose));
        app.choose("again?", &["only".to_string()]);
        assert!(matches!(
            app.on_key(key(KeyCode::Esc)),
            Reply::ChoiceCancelled
        ));
        assert!(app.chooser.is_none());
    }

    #[test]
    fn a_secret_entry_hides_what_is_typed_and_answers_on_enter() {
        let mut app = app_on_memory();
        app.secret_begin("paste the key");
        typed(&mut app, "sk-live-abc");
        assert!(app.collecting_secret());
        app.paint();
        let painted = String::from_utf8_lossy(app.screen.written()).into_owned();
        assert!(
            !painted.contains("sk-live-abc"),
            "a secret never reaches the screen: {painted:?}"
        );
        assert!(
            painted.contains('•'),
            "what the member typed is drawn masked: {painted:?}"
        );
        let reply = app.on_key(key(KeyCode::Enter));
        assert!(matches!(&reply, Reply::Secret(value) if value == "sk-live-abc"));
        assert!(!app.collecting_secret());
        assert!(matches!(app.focus, Focus::Compose));
    }

    #[test]
    fn a_pasted_secret_loses_the_newline_that_came_with_it() {
        let mut app = app_on_memory();
        app.secret_begin("paste the key");
        app.on_paste("sk-live-abc\n".to_string());
        let reply = app.on_key(key(KeyCode::Enter));
        assert!(
            matches!(&reply, Reply::Secret(value) if value == "sk-live-abc"),
            "a key copied with its trailing newline still posts clean: {reply:?}"
        );
    }

    #[test]
    fn a_secret_entry_can_be_abandoned() {
        let mut app = app_on_memory();
        app.secret_begin("paste the key");
        typed(&mut app, "half");
        assert!(matches!(app.on_key(key(KeyCode::Esc)), Reply::Secret(value) if value.is_empty()));
        assert!(!app.collecting_secret());
    }

    #[test]
    fn a_choose_key_with_no_chooser_falls_back_to_the_composer() {
        let mut app = app_on_memory();
        app.focus = Focus::Choose;
        assert!(matches!(app.on_key(key(KeyCode::Enter)), Reply::None));
        assert!(matches!(app.focus, Focus::Compose));
    }

    #[test]
    fn up_on_an_empty_composer_recalls_only_when_something_is_queued() {
        let mut app = app_on_memory();
        assert!(
            matches!(app.on_key(key(KeyCode::Up)), Reply::None),
            "nothing queued, nothing recalled"
        );
        app.push_queued("queued words");
        app.sent_ack("queued words", "arr-1");
        let reply = app.on_key(key(KeyCode::Up));
        assert!(matches!(&reply, Reply::Recall { text, .. } if text == "queued words"));
    }

    #[test]
    fn a_painted_frame_states_the_transcript_and_the_endpoint() {
        let mut app = app_on_memory();
        app.say("the assistant spoke");
        app.member_echo("the member answered");
        app.paint();
        let painted = String::from_utf8_lossy(app.screen.written()).into_owned();
        assert!(painted.contains("the assistant spoke"), "{painted}");
        assert!(painted.contains("the member answered"));
        assert!(
            painted.contains("host.1"),
            "the footer names the conversation"
        );
    }

    /// The dock draws the open tail on every delta and on every tick. Whatever it draws there is a
    /// different string each time, so the renderer it reaches for must not be the holding one.
    #[test]
    fn a_streaming_reply_leaves_nothing_held_until_it_commits() {
        let mut app = app_on_memory();
        let before = markdown::held_blocks();
        for delta in [
            "```rust\n",
            "fn parse(line",
            ": &str) -> usize {\n",
            "    line.len()\n",
            "}\n",
        ] {
            app.txt(delta);
            app.paint();
        }
        assert_eq!(
            markdown::held_blocks(),
            before,
            "the open tail is drawn, never held"
        );
        app.txt("```\n\n");
        app.paint();
        assert!(
            markdown::held_blocks() > before,
            "the block holds its colours once it settles into the transcript"
        );
    }

    #[test]
    fn a_narration_names_the_call_it_belongs_to() {
        let mut app = app_on_memory();
        app.narration = Some(("exec".to_string(), "counting the rows".to_string()));
        app.op_started(&asked("exec", "exec", "{}"));
        assert_eq!(app.running_desc.as_deref(), Some("counting the rows"));
        app.narration = Some(("read".to_string(), "a different call".to_string()));
        app.op_started(&asked("exec", "exec", "{}"));
        assert!(
            app.running_desc.is_none(),
            "a narration for another tool is not adopted"
        );
    }

    #[test]
    fn every_call_this_terminal_ran_stays_in_the_transcript() {
        let mut app = app_on_memory();
        app.begin_turn();
        for run in [
            "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
        ] {
            app.op_finished(
                &asked("exec", "exec", &format!(r#"{{"argv":["make","{run}"]}}"#)),
                &Ok(exec_reply(0, &format!("built {run}\n")).into_bytes()),
            );
        }
        let document = transcript(&mut app);
        for run in ["alpha", "theta"] {
            assert!(
                document.contains(&format!("⏺ exec make {run}")),
                "the call is a step of the turn: {document}"
            );
            assert!(
                document.contains(&format!("built {run}")),
                "and its output stands under it: {document}"
            );
        }
        assert!(
            app.running_op.is_none(),
            "a finished op is no longer running"
        );
        app.end_turn(false);
        assert!(
            transcript(&mut app).contains("Completed 8 steps"),
            "the turn's end rolls the calls up with its other steps"
        );
    }

    #[test]
    fn a_narrated_call_states_its_result_under_the_row_it_narrated() {
        let mut app = app_on_memory();
        app.begin_turn();
        app.note("running exec: counting the rows");
        let op = asked("exec", "exec", r#"{"argv":["wc","-l"]}"#);
        app.op_started(&op);
        app.op_finished(&op, &Ok(exec_reply(0, "42\n").into_bytes()));
        let document = transcript(&mut app);
        assert!(
            !document.contains("running exec: counting the rows"),
            "the narration's row became the call's own: {document}"
        );
        assert!(
            document.contains("⏺ counting the rows") && document.contains("42"),
            "which states the agent's words over the result: {document}"
        );
        app.end_turn(false);
        assert!(
            transcript(&mut app).contains("Completed 1 step"),
            "one call is one step, whichever end of the wire stated it"
        );
    }

    #[test]
    fn text_between_two_dispatches_is_the_thought_each_round_wrote() {
        let mut app = app_on_memory();
        app.begin_turn();
        app.txt("Template is in place. Now writing the schema.\n");
        app.activity("Build the content management system", None);
        app.txt("Now the routes with auth:\n");
        app.activity("Create the blog admin dashboard", None);
        app.txt("Now the frontend pages.\n");
        app.end_turn(false);
        let document = transcript(&mut app);
        assert!(
            !document.contains("schema.Now") && !document.contains("auth:Now"),
            "a round's words end where the next round's begin: {document}"
        );
        app.retained.toggle_steps();
        let opened = transcript(&mut app);
        let thought = opened
            .find("Now the routes with auth:")
            .expect("the thought");
        let after = opened
            .find("Create the blog admin dashboard")
            .expect("the call it wrote before");
        assert!(
            thought < after,
            "each round's words stand above the call it made: {opened}"
        );
    }

    #[test]
    fn a_call_the_activity_directive_narrated_states_itself_once() {
        let mut app = app_on_memory();
        app.begin_turn();
        app.activity("running exec: counting the rows", None);
        let op = asked("exec", "exec", r#"{"argv":["wc","-l"]}"#);
        app.op_started(&op);
        app.op_finished(&op, &Ok(exec_reply(0, "42\n").into_bytes()));
        app.end_turn(false);
        assert!(
            transcript(&mut app).contains("Completed 1 step"),
            "the narration and the call it named are one step, not two"
        );
        app.retained.toggle_steps();
        let opened = transcript(&mut app);
        assert!(
            opened.contains("⏺ counting the rows") && opened.contains("42"),
            "stated under the agent's own words, over its result: {opened}"
        );
        assert!(
            !opened.contains("exec wc -l"),
            "the command is not restated beside the words: {opened}"
        );
    }

    #[test]
    fn a_failed_op_states_its_failure() {
        let mut app = app_on_memory();
        app.op_finished(
            &asked("exec", "exec", r#"{"argv":["make"]}"#),
            &Err("ENOENT: no such tool".to_string()),
        );
        let document = transcript(&mut app);
        assert!(document.contains("ENOENT: no such tool"), "{document}");
    }

    #[test]
    fn a_call_row_clips_to_the_width() {
        let mut app = app_on_memory();
        app.retained.set_width(14);
        app.op_finished(
            &asked("exec", "exec", r#"{"argv":["make","test","--verbose"]}"#),
            &Ok(exec_reply(0, "").into_bytes()),
        );
        assert!(
            transcript(&mut app).contains("⏺ exec make"),
            "the header takes the room the transcript has"
        );
    }

    #[test]
    fn a_prompt_a_choice_and_a_secret_each_take_the_focus() {
        let mut app = app_on_memory();
        app.choose("which one?", &["first".to_string(), "second".to_string()]);
        assert!(matches!(app.focus, Focus::Choose));
        assert!(app.chooser.is_some());
        app.secret_begin("paste the key");
        assert!(matches!(app.focus, Focus::Secret));
        assert!(app.collecting_secret());
        app.ask_prompt("your turn");
        assert!(matches!(app.focus, Focus::Compose));
        assert_eq!(app.prompt, "your turn");
        app.ask_prompt("");
        assert_eq!(
            app.prompt, PROMPT_IDLE,
            "an empty ask restores the idle prompt"
        );
    }

    #[test]
    fn an_acknowledged_send_settles_when_the_turn_absorbs_it() {
        let mut app = app_on_memory();
        app.push_queued("while the turn ran");
        app.sent_ack("while the turn ran", "arr-9");
        assert_eq!(app.queued[0].arrival.as_deref(), Some("arr-9"));
        app.absorbed(&["arr-9".to_string()]);
        assert!(app.queued.is_empty(), "the absorbed row leaves the queue");
        assert_eq!(members(&mut app), vec!["while the turn ran"]);
    }

    #[test]
    fn an_absorb_that_outran_its_ack_settles_the_row_once() {
        let mut app = app_on_memory();
        app.push_queued("raced");
        app.absorbed(&["arr-9".to_string()]);
        assert_eq!(
            app.queued.len(),
            1,
            "nothing settles before its ack arrives"
        );
        assert_eq!(app.early_absorbed, vec!["arr-9".to_string()]);
        app.sent_ack("raced", "arr-9");
        assert!(
            app.queued.is_empty(),
            "the late ack settles the row at once"
        );
        assert!(app.early_absorbed.is_empty(), "the parked id is consumed");
        assert_eq!(members(&mut app), vec!["raced"]);
    }

    #[test]
    fn parked_arrivals_never_grow_without_bound() {
        let mut app = app_on_memory();
        let ids: Vec<String> = (0..EARLY_ABSORBED_MAX + 8)
            .map(|n| format!("arr-{n}"))
            .collect();
        app.absorbed(&ids);
        assert_eq!(app.early_absorbed.len(), EARLY_ABSORBED_MAX);
        assert_eq!(
            app.early_absorbed.first().map(String::as_str),
            Some("arr-8"),
            "the oldest parked ids are the ones dropped"
        );
    }

    #[test]
    fn a_recall_waits_for_the_send_to_be_acknowledged() {
        let mut app = app_on_memory();
        app.push_queued("not yet sent");
        assert!(matches!(app.recall_queued(), Reply::None));
        assert!(
            app.flash
                .as_ref()
                .is_some_and(|(said, _)| said.contains("Still sending")),
            "the member is told the send is still in flight"
        );
        app.sent_ack("not yet sent", "arr-1");
        let reply = app.recall_queued();
        assert!(
            matches!(&reply, Reply::Recall { text, arrival_id } if text == "not yet sent" && arrival_id == "arr-1")
        );
        assert!(app.queued[0].retracting);
        assert!(
            matches!(app.recall_queued(), Reply::None),
            "a row already being recalled is not recalled twice"
        );
    }

    #[test]
    fn a_granted_recall_puts_the_words_back_in_the_composer() {
        let mut app = app_on_memory();
        app.push_queued("take it back");
        app.sent_ack("take it back", "arr-1");
        app.retracted("take it back", "arr-1", true);
        assert!(app.queued.is_empty());
        assert_eq!(app.ask.text, "take it back");
        assert_eq!(app.ask.cursor, app.ask.text.len());
    }

    #[test]
    fn a_recall_the_turn_beat_leaves_the_row_where_it_was() {
        let mut app = app_on_memory();
        app.push_queued("too late");
        app.sent_ack("too late", "arr-1");
        app.recall_queued();
        app.retracted("too late", "arr-1", false);
        assert_eq!(
            app.queued.len(),
            1,
            "the row stays; the turn owns the words"
        );
        assert!(!app.queued[0].retracting, "and it can be recalled again");
        assert!(app
            .flash
            .as_ref()
            .is_some_and(|(said, _)| said.contains("Already picked up")));
        app.retracted("nothing here", "arr-unknown", true);
        assert_eq!(
            app.queued.len(),
            1,
            "an answer naming no queued row changes nothing"
        );
    }

    #[test]
    fn a_recall_joins_what_the_member_has_since_typed() {
        let mut app = app_on_memory();
        app.push_queued("first words");
        app.sent_ack("first words", "arr-1");
        app.ask.text = "second words".to_string();
        app.retracted("first words", "arr-1", true);
        assert_eq!(app.ask.text, "first words\n\nsecond words");
    }

    #[test]
    fn a_send_the_turn_already_holds_settles_exactly_once() {
        let mut app = app_on_memory();
        app.push_queued("retried delivery");
        app.settle_queued("retried delivery");
        assert!(app.queued.is_empty());
        assert_eq!(members(&mut app), vec!["retried delivery"]);
        app.settle_queued("retried delivery");
        assert_eq!(
            members(&mut app),
            vec!["retried delivery"],
            "a row an absorb already settled stays settled"
        );
    }

    #[test]
    fn an_ended_turn_takes_its_running_call_with_it() {
        let mut app = app_on_memory();
        app.begin_turn();
        app.op_started(&asked("exec", "exec", r#"{"argv":["make"]}"#));
        assert!(app.running_op.is_some());
        app.end_turn(false);
        assert!(!app.is_working());
        assert!(
            app.running_op.is_none(),
            "a turn that ended is running nothing"
        );
    }

    #[test]
    fn an_app_paints_into_the_sink_it_was_given() {
        let mut app = app_on_memory();
        app.say("hello there");
        app.begin_turn();
        assert!(app.is_working(), "a begun turn is working");
        app.end_turn(false);
        assert!(!app.is_working(), "an ended turn is not");
        app.splice_raw("\x1b]52;c;YWJj\x07");
        let painted = String::from_utf8_lossy(app.screen.written()).into_owned();
        assert!(
            painted.contains("\x1b]52;c;YWJj\x07"),
            "a spliced sequence reaches the given sink, never the process's own terminal"
        );
        assert!(
            painted.contains("\x1b[?1049h"),
            "the screen entered the alternate buffer of the sink it was handed: {painted:?}"
        );
    }

    #[test]
    fn only_the_agents_own_work_narrates_a_step() {
        assert!(narrates_activity("running bash: ls"));
        assert!(narrates_activity("loading skill: office/pptx"));
        assert!(narrates_activity("reviewer: running read: the diff"));
        assert!(narrates_activity("reviewer: loading skill: coding"));
        assert!(!narrates_activity("Completed 3 steps"));
        assert!(!narrates_activity("Copied the last reply."));
        assert!(!narrates_activity("Not stopped: the turn had ended"));
        assert!(!narrates_activity(
            "Detached; the turn continues, and a new message rejoins it."
        ));
    }

    #[test]
    fn a_run_names_itself_before_the_call_it_made() {
        assert_eq!(
            run_label("reviewer: running read: the diff"),
            Some("reviewer")
        );
        assert_eq!(
            run_label("reviewer: loading skill: coding"),
            Some("reviewer")
        );
        assert_eq!(run_label("running read: the diff"), None);
        assert_eq!(run_label("loading skill: office/pptx"), None);
        assert_eq!(run_label("Copied the last reply."), None);
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

    fn listed(id: &str, title: &str, postable: bool) -> ConversationRow {
        ConversationRow {
            id: id.to_string(),
            title: title.to_string(),
            surface: "slack".to_string(),
            surface_label: Some("#eng".to_string()),
            speaker: None,
            agent: "assistant".to_string(),
            main: true,
            last_at: 0.0,
            postable,
            channel: None,
        }
    }

    #[test]
    fn a_turn_ending_under_the_page_waits_behind_it() {
        let mut app = app_on_memory();
        app.set_live_target(Target::Channel("host.1".to_string()));
        app.begin_turn();
        app.open_conversations(true);
        app.conversations_loaded(1, Ok(vec![listed("c1", "Who owns the pager", true)]));
        app.txt("the reply");
        app.end_turn(true);
        app.ask_prompt(">");
        assert_eq!(
            app.focus,
            Focus::Conversations,
            "the reply is news, not a reason to leave"
        );
        app.paint();
        let painted = String::from_utf8_lossy(app.screen.written()).to_string();
        assert!(painted.contains("UFO Chats"), "{painted}");
        app.close_conversations();
        assert_eq!(app.focus, Focus::Compose);
        assert_eq!(
            app.prompt, PROMPT_IDLE,
            "the server's bare prompt is the idle one"
        );

        app.open_conversations(true);
        app.choose("Pick one", &["a".to_string(), "b".to_string()]);
        assert_eq!(app.focus, Focus::Conversations);
        app.close_conversations();
        assert_eq!(app.focus, Focus::Choose);
    }

    #[test]
    fn a_row_wider_than_the_screen_is_cut_at_its_edge() {
        let mut app = app_on_memory();
        let line = Line::from(vec![
            Span::styled("abc", app.theme.member),
            Span::raw("defgh"),
        ]);
        let cut = clip_line(&line, 5);
        assert_eq!(cut.to_string(), "abcde");
        assert_eq!(cut.spans[0].style, app.theme.member);
        let cols = app.cols as usize;
        app.note(&"n".repeat(cols + 200));
        app.paint();
        let painted = String::from_utf8_lossy(app.screen.written()).to_string();
        assert!(painted.contains(&"n".repeat(cols)), "the note is drawn");
        assert!(
            !painted.contains(&"n".repeat(cols + 1)),
            "and cut where the screen ends"
        );
    }

    #[test]
    fn a_link_costs_no_columns_and_stays_closed_past_the_cut() {
        let url = "https://github.com/acme/repo/pull/1892/with/a/tail/that/runs/on/and/on";
        let label = format!("{}PR #1892{}", osc::link_open(url), osc::LINK_CLOSE);
        let linked = Line::from(vec![Span::raw(label.clone()), Span::raw("x".repeat(10))]);
        assert_eq!(painted_width(&linked), 18);
        assert_eq!(clip_line(&linked, 10).to_string(), format!("{label}xx"));
        assert_eq!(
            clip_line(&linked, 3).to_string(),
            format!("{}PR {}", osc::link_open(url), osc::LINK_CLOSE)
        );
        let pr = Pr {
            number: 1892,
            url: url.to_string(),
        };
        let (footer, _) = status::footer(
            &Theme::for_mode(ColorMode::TrueColor, theme::Scheme::Dark),
            80,
            "acme.ufo.dev",
            "general",
            Some(&pr),
            Some(status::LIST_HINT),
        );
        assert_eq!(
            painted_width(&footer),
            80,
            "a linked footer fills its row whole"
        );
    }

    /// The page as it renders into the transcript area, one string.
    fn page_text(app: &App<Vec<u8>>) -> String {
        let page = app.conversations.as_ref().expect("the page is up");
        page.render(&app.theme, 80, 24)
            .iter()
            .map(|line| line.to_string())
            .collect::<Vec<String>>()
            .join("\n")
    }

    #[test]
    fn the_page_opens_on_the_list_it_last_showed_here_and_in_the_next_process() {
        let home = scratch_home();
        let mut app = app_at(home.clone(), "ufo.test", "host.1");
        app.open_conversations(false);
        assert!(page_text(&app).contains("Loading…"), "nothing to show yet");
        app.conversations_loaded(1, Ok(vec![listed("c1", "Who owns the pager", true)]));
        app.close_conversations();
        app.open_conversations(false);
        let shown = page_text(&app);
        assert!(shown.contains("Who owns the pager"), "{shown}");
        assert!(!shown.contains("Loading…"), "{shown}");
        drop(app);

        let mut next = app_at(home.clone(), "ufo.test", "host.1");
        next.open_conversations(false);
        assert!(
            page_text(&next).contains("Who owns the pager"),
            "the stored list opens the page"
        );
        let mut elsewhere = app_at(home, "ufo.test", "host.2");
        elsewhere.open_conversations(false);
        assert!(
            page_text(&elsewhere).contains("Loading…"),
            "another sign-in's list stays unread"
        );
    }

    /// Whether the first list row is drawn bold; the cursor rests in the entry, so no row carries
    /// the highlight's own bold.
    fn first_row_bold(app: &mut App<Vec<u8>>) -> bool {
        let page = app.conversations.as_ref().expect("the page is up");
        let lines = page.render(&app.theme, 80, 24);
        lines[1]
            .spans
            .iter()
            .any(|span| span.style.add_modifier.contains(Modifier::BOLD))
    }

    #[test]
    fn a_reply_landing_behind_the_page_makes_its_row_bold() {
        let mut app = app_on_memory();
        app.set_live_target(Target::Channel("abc".to_string()));
        app.open_conversations(true);
        let mut mine = listed("c1", "list files", true);
        mine.channel = Some("abc".to_string());
        mine.last_at = 100.0;
        let other = listed("c2", "Who owns the pager", true);
        app.conversations_loaded(1, Ok(vec![mine.clone(), other.clone()]));
        assert!(!first_row_bold(&mut app), "nothing bold at first sight");
        mine.last_at = 200.0;
        let fetch = app.conversations_due_fetch(Instant::now() + std::time::Duration::from_secs(6));
        assert_eq!(fetch.map(|fetch| fetch.generation), Some(2));
        app.conversations_loaded(2, Ok(vec![mine.clone(), other.clone()]));
        assert!(first_row_bold(&mut app), "the moved row is bold");
        app.mark_seen(&mine);
        app.close_conversations();
        app.open_conversations(true);
        app.conversations_loaded(1, Ok(vec![mine, other]));
        assert!(!first_row_bold(&mut app), "opened rows are seen");
    }

    #[test]
    fn ctrl_k_opens_the_conversation_page_and_a_pick_names_the_row() {
        let mut app = app_on_memory();
        typed(&mut app, "draft");
        assert_eq!(
            app.on_key(ctrl(KeyCode::Char('k'))),
            Reply::OpenConversations
        );
        let fetch = app.open_conversations(true);
        assert_eq!(fetch.generation, 1);
        assert_eq!(fetch.search, "");
        assert!(app.ask.text.is_empty(), "the page's entry starts empty");
        app.conversations_loaded(
            1,
            Ok(vec![
                listed("c1", "Who owns the pager", true),
                listed("c2", "Deploy plan", true),
            ]),
        );
        app.paint();
        let painted = String::from_utf8_lossy(app.screen.written()).to_string();
        assert!(painted.contains("UFO Chats"), "{painted}");
        assert!(painted.contains("Deploy plan"), "{painted}");
        assert!(painted.contains("Search \u{203a}"), "{painted}");
        assert!(painted.contains("New chat ❯"), "{painted}");
        assert!(painted.contains("ufo.test"), "the footer stays: {painted}");
        let version = format!("v{}", env!("CARGO_PKG_VERSION"));
        assert!(
            painted.contains(&version),
            "the mark heads the page: {painted}"
        );
        app.on_key(key(KeyCode::Up));
        typed(&mut app, "deploy");
        let picked = app.on_key(key(KeyCode::Enter));
        assert!(
            matches!(&picked, Reply::Open(row) if row.id == "c2"),
            "{picked:?}"
        );
    }

    #[test]
    fn typing_in_the_entry_bar_starts_a_new_chat_and_the_draft_waits_behind() {
        let mut app = app_on_memory();
        typed(&mut app, "half a thought");
        app.open_conversations(true);
        assert!(app.ask.text.is_empty());
        assert_eq!(
            app.on_key(key(KeyCode::Enter)),
            Reply::None,
            "an empty entry sends nothing"
        );
        typed(&mut app, "hello there");
        assert_eq!(
            app.on_key(key(KeyCode::Enter)),
            Reply::NewChat("hello there".to_string())
        );
        assert!(app.ask.text.is_empty());
        typed(&mut app, "kept");
        app.close_conversations();
        assert_eq!(
            app.ask.text, "half a thought",
            "the conversation's draft returns"
        );
        assert_eq!(app.focus, Focus::Compose);
    }

    #[test]
    fn a_click_on_the_page_opens_the_row_under_it() {
        let mut app = app_on_memory();
        app.open_conversations(false);
        app.conversations_loaded(1, Ok(vec![listed("c1", "Who owns the pager", true)]));
        app.paint();
        let top = masthead::masthead(&app.theme, app.cols).len() as u16;
        let press = MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 4,
            row: top + 1,
            modifiers: KeyModifiers::NONE,
        };
        assert!(matches!(app.on_mouse(press), Reply::Open(row) if row.id == "c1"));
        let above = MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 4,
            row: 0,
            modifiers: KeyModifiers::NONE,
        };
        assert_eq!(app.on_mouse(above), Reply::None);
        let (search_row, entry_rows) = app.page_hit.clone().expect("the dock was painted");
        let on_search = MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 2,
            row: search_row,
            modifiers: KeyModifiers::NONE,
        };
        assert_eq!(app.on_mouse(on_search), Reply::None);
        typed(&mut app, "pag");
        assert!(
            app.ask.text.is_empty(),
            "typing after a click on the search line searches"
        );
        let on_entry = MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 2,
            row: entry_rows.start,
            modifiers: KeyModifiers::NONE,
        };
        assert_eq!(app.on_mouse(on_entry), Reply::None);
        typed(&mut app, "hi");
        assert_eq!(app.ask.text, "hi");
    }

    #[test]
    fn a_click_on_the_footer_hint_opens_the_page_and_the_page_carries_none() {
        let mut app = app_on_memory();
        app.paint();
        let (row, columns) = app.list_hit.clone().expect("the footer carries the hint");
        let painted = String::from_utf8_lossy(app.screen.written()).to_string();
        assert!(painted.contains(status::LIST_HINT), "{painted}");
        let press = MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: columns.start as u16,
            row,
            modifiers: KeyModifiers::NONE,
        };
        assert_eq!(app.on_mouse(press), Reply::OpenConversations);
        app.open_conversations(true);
        app.paint();
        assert_eq!(app.list_hit, None, "the page's footer carries no hint");
    }

    #[test]
    fn esc_leaves_the_page_for_the_conversation_or_exits_without_one() {
        let mut app = app_on_memory();
        app.open_conversations(true);
        assert_eq!(app.on_key(key(KeyCode::Esc)), Reply::CloseConversations);
        assert_eq!(app.focus, Focus::Compose);
        app.open_conversations(true);
        assert_eq!(
            app.on_key(ctrl(KeyCode::Char('k'))),
            Reply::CloseConversations
        );
        app.open_conversations(false);
        assert_eq!(app.on_key(key(KeyCode::Esc)), Reply::Exit);
    }

    #[test]
    fn a_reset_starts_a_bare_transcript_and_a_read_only_one_takes_no_message() {
        let mut app = app_on_memory();
        app.begin_turn();
        app.txt("an old reply");
        app.end_turn(false);
        typed(&mut app, "half a thought");
        app.reset_conversation(
            &Target::Conversation("c1".to_string()),
            "#eng".to_string(),
            Some("Slack".to_string()),
        );
        assert_eq!(app.channel, "#eng");
        assert!(app.ask.text.is_empty());
        assert!(!transcript(&mut app).contains("an old reply"));
        typed(&mut app, "hello");
        assert_eq!(app.on_key(key(KeyCode::Enter)), Reply::None);
        assert!(
            app.ask.text.is_empty(),
            "a read-only conversation takes no draft"
        );
        app.paint();
        let painted = String::from_utf8_lossy(app.screen.written()).to_string();
        assert!(
            painted.contains("Reply in Slack to continue it."),
            "{painted}"
        );
        assert_eq!(
            app.on_key(ctrl(KeyCode::Char('k'))),
            Reply::OpenConversations
        );
        app.reset_conversation(&Target::Channel("abc".to_string()), "abc".to_string(), None);
        typed(&mut app, "hello");
        assert_eq!(
            app.on_key(key(KeyCode::Enter)),
            Reply::Send("hello".to_string())
        );
    }
}
