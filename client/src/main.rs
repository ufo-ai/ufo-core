mod config;
mod jsonio;
mod ops;
mod ui;
mod wire;

use std::collections::VecDeque;
use std::env;
use std::io::{BufRead, IsTerminal};
use std::process;
use std::sync::mpsc::{channel, Receiver, RecvTimeoutError, Sender};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crossterm::event::{Event as TermEvent, KeyEventKind};

use crate::ops::OpRuntime;
use crate::ui::history::{list_conversations, record_conversation, PastConversation};
use crate::ui::picker::{PickOutcome, Picker};
use crate::ui::plain::Plain;
use crate::ui::{App, Reply};
use crate::wire::{Directive, OpRequest, PostBody, Session, Stop};

const GATEWAY_URL_DEFAULT: &str = "https://flyingobject.ai";
const ONBOARDING_CHANNEL: &str = "onboard";
const RECONNECT_ATTEMPTS: u32 = 3;
const RECONNECT_PAUSE: Duration = Duration::from_secs(1);
const TICK: Duration = Duration::from_millis(80);
const RESUME_ROWS: usize = 12;

fn main() {
    #[cfg(unix)]
    adopt_tty_stdin();
    let args: Vec<String> = env::args().skip(1).collect();
    let home = config::Home::resolve();
    let mut rest: &[String] = &args;
    let mut resumed: Option<String> = None;
    let mut login = false;
    let mut json = false;
    loop {
        match rest.first().map(String::as_str) {
            Some("logout") => {
                home.clear_signin();
                println!("Signed out.");
                return;
            }
            Some("login") => {
                login = true;
                home.clear_signin();
                rest = &rest[1..];
            }
            Some("--json") => {
                json = true;
                rest = &rest[1..];
            }
            Some("--resume") => match rest.get(1) {
                Some(id) if !id.starts_with("--") => {
                    resumed = Some(id.clone());
                    rest = &rest[2..];
                }
                _ => {
                    resumed = Some(pick_resume(&home));
                    rest = &rest[1..];
                }
            },
            _ => break,
        }
    }
    let message = rest.join(" ");
    let workspace_url = if login {
        None
    } else {
        env_nonempty("WORKSPACE_URL").or_else(|| home.workspace())
    };
    let token = if login { None } else { home.credentials() };
    let session_id = home.session().unwrap_or_else(|| {
        let minted = format!("{}.{}.{}", hostname(), process::id(), epoch_seconds());
        home.store_session(&minted);
        minted
    });
    let channel_name = env_nonempty("UFO_CHANNEL").or(resumed).unwrap_or_else(|| {
        if workspace_url.is_some() {
            random_channel()
        } else {
            ONBOARDING_CHANNEL.to_string()
        }
    });
    let launch_dir = env::current_dir()
        .and_then(|dir| dir.canonicalize())
        .unwrap_or_else(|error| die(&format!("could not resolve the working directory: {error}")));
    let cwd_header = launch_dir
        .to_str()
        .filter(|path| path.starts_with('/'))
        .map(String::from);
    let installed = config::installed(&home);
    let tty = std::io::stdout().is_terminal();
    let workdir = private_workdir().unwrap_or_else(|error| die(&error));
    let session = Session::new(
        resolve_gateway(env_nonempty("UFO_URL"), home.gateway()),
        workspace_url,
        channel_name,
        token,
        session_id,
        cwd_header,
        installed,
        tty && !json,
    );
    #[cfg(unix)]
    interrupt::install();
    update_resume(&session, tty && !json);
    config::sweep_retired(&home);
    let scratch = workdir.clone();
    let runtime = OpRuntime {
        workdir,
        cwd: launch_dir,
    };
    let code = if json {
        run_json(session, runtime, home, message)
    } else if tty && ui::wants_fx() {
        run_tty(session, runtime, home, message)
    } else {
        run_plain(session, runtime, home, message)
    };
    let _ = std::fs::remove_dir_all(&scratch);
    process::exit(code);
}

fn die(message: &str) -> ! {
    eprintln!("ufo: {message}");
    process::exit(1);
}

/// A `curl | sh` install leaves the exec'd binary with the exhausted pipe as stdin. The member is
/// still at a terminal, so input comes from the terminal itself — the shell client read every
/// prompt from `/dev/tty`. The terminal is adopted by its real device name (`ttyname` of stdout
/// or stderr) because macOS refuses to register the `/dev/tty` alias with kqueue, which is what
/// the event reader polls; the alias remains the fallback for the fully redirected case, whose
/// plain-mode reads are blocking and never poll. A process with no terminal keeps its pipe.
#[cfg(unix)]
fn adopt_tty_stdin() {
    use std::io::IsTerminal;
    use std::os::fd::AsRawFd;
    if std::io::stdin().is_terminal() {
        return;
    }
    let named = [libc::STDOUT_FILENO, libc::STDERR_FILENO]
        .into_iter()
        .find(|&fd| unsafe { libc::isatty(fd) } == 1)
        .and_then(|fd| {
            let name = unsafe { libc::ttyname(fd) };
            if name.is_null() {
                return None;
            }
            Some(
                unsafe { std::ffi::CStr::from_ptr(name) }
                    .to_string_lossy()
                    .into_owned(),
            )
        });
    let path = named.unwrap_or_else(|| "/dev/tty".to_string());
    let Ok(tty) = std::fs::File::open(&path) else {
        return;
    };
    unsafe {
        libc::dup2(tty.as_raw_fd(), libc::STDIN_FILENO);
    }
}

fn env_nonempty(name: &str) -> Option<String> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

fn epoch_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn hostname() -> String {
    #[cfg(unix)]
    {
        let mut buffer = [0u8; 256];
        let outcome =
            unsafe { libc::gethostname(buffer.as_mut_ptr() as *mut libc::c_char, buffer.len()) };
        if outcome == 0 {
            let end = buffer
                .iter()
                .position(|&byte| byte == 0)
                .unwrap_or(buffer.len());
            if let Ok(name) = std::str::from_utf8(&buffer[..end]) {
                if !name.is_empty() {
                    return name.to_string();
                }
            }
        }
        "host".to_string()
    }
    #[cfg(not(unix))]
    {
        env::var("COMPUTERNAME").unwrap_or_else(|_| "host".to_string())
    }
}

fn random_channel() -> String {
    random_hex::<16>()
}

fn random_hex<const N: usize>() -> String {
    let mut bytes = [0u8; N];
    getrandom::fill(&mut bytes).expect("os randomness is available");
    let mut out = String::with_capacity(N * 2);
    for byte in bytes {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

fn private_workdir() -> Result<std::path::PathBuf, String> {
    let base = env::temp_dir();
    for _ in 0..16 {
        let candidate = base.join(format!("ufo.{}", random_hex::<8>()));
        let mut builder = std::fs::DirBuilder::new();
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt;
            builder.mode(0o700);
        }
        match builder.create(&candidate) {
            Ok(()) => return Ok(candidate),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(format!("could not create {}: {error}", candidate.display())),
        }
    }
    Err(format!(
        "could not create a private working directory under {}",
        base.display()
    ))
}

fn resolve_gateway(env: Option<String>, stored: Option<String>) -> String {
    env.or(stored)
        .unwrap_or_else(|| GATEWAY_URL_DEFAULT.to_string())
}

fn resume_command(workspace_url: Option<&str>, channel: &str) -> Option<String> {
    workspace_url?;
    Some(format!("ufo --resume {channel}"))
}

fn wants_style(tty: bool) -> bool {
    let term = env::var("TERM").unwrap_or_default();
    tty && !term.is_empty()
        && term != "dumb"
        && env::var_os("UFO_PLAIN").is_none()
        && env::var_os("NO_COLOR").is_none()
}

fn update_resume(session: &Session, tty: bool) {
    #[cfg(unix)]
    {
        if !tty {
            interrupt::set_resume("");
            return;
        }
        match resume_command(session.workspace_url.as_deref(), &session.channel) {
            Some(command) if wants_style(tty) => interrupt::set_resume(&format!(
                "\x1b[2mResume this conversation: \x1b[0m\x1b[1m{command}\x1b[0m"
            )),
            Some(command) => interrupt::set_resume(&format!("Resume this conversation: {command}")),
            None => interrupt::set_resume(""),
        }
    }
    #[cfg(not(unix))]
    {
        let _ = (session, tty);
    }
}

fn print_resume(workspace_url: Option<&str>, channel: &str, tty: bool) {
    if !tty {
        return;
    }
    let Some(command) = resume_command(workspace_url, channel) else {
        return;
    };
    println!();
    if wants_style(tty) {
        println!("\x1b[2mResume this conversation: \x1b[0m\x1b[1m{command}\x1b[0m");
    } else {
        println!("Resume this conversation: {command}");
    }
}

/// Bare `--resume`: pick from the conversations this machine opened.
fn pick_resume(home: &config::Home) -> String {
    if !std::io::stdout().is_terminal() || !ui::wants_fx() {
        die("--resume needs a conversation id when there is no terminal to pick in.");
    }
    let items = list_conversations(&home.root);
    if items.is_empty() {
        die("No conversation on this machine to resume.");
    }
    let rows = ui::history::conversation_rows(&items);
    let theme = ui::theme::Theme::detect(false);
    let raw = ui::RawGuard::new();
    let mut dock = ui::term::DockTerm::new(std::io::stdout(), theme.mode);
    let mut picker = Picker::new(rows);
    picker.set_page(RESUME_ROWS);
    let picked = loop {
        let mut lines = vec![ratatui::text::Line::styled(
            "Resume a conversation".to_string(),
            theme.heading,
        )];
        lines.extend(picker.render(&theme, 80, RESUME_ROWS));
        let _ = dock.frame(&[], &lines, None);
        let event = match crossterm::event::read() {
            Ok(event) => event,
            Err(_) => break None,
        };
        let TermEvent::Key(key) = event else { continue };
        if key.kind == KeyEventKind::Release {
            continue;
        }
        let Some(pick) = ui::pick_key(key) else {
            continue;
        };
        match picker.apply_key(pick) {
            PickOutcome::Picked(_) => break picker.current_index(),
            PickOutcome::Cancelled => break None,
            PickOutcome::Continue => {}
        }
    };
    let _ = dock.close();
    drop(raw);
    match picked {
        Some(index) => items[index].channel.clone(),
        None => process::exit(0),
    }
}

// ── wire thread ─────────────────────────────────────────────────────────────────────────────────

enum WireEvent {
    Dir(Directive),
    OpStarted(OpRequest),
    OpFinished(OpRequest, Result<Vec<u8>, String>),
    MemberEcho(String),
    Reconnecting { attempt: u32, retry_in_s: u64 },
    WorkspaceChanged { url: String, channel: String },
    Stoppable(Stop),
    StreamEnd { continues: bool },
    Fatal(String),
}

enum WireCmd {
    Say(String),
    SecretValue {
        sealed: String,
        slot: String,
        value: String,
    },
    Detach,
    Shutdown,
}

struct Wire {
    session: Session,
    runtime: OpRuntime,
    home: config::Home,
    evt: Sender<WireEvent>,
    cmd: Receiver<WireCmd>,
    queue: VecDeque<String>,
    op_reply: Option<PostBody>,
    poll: Option<f64>,
    detached: bool,
    recorded: bool,
    opened: bool,
    install: bool,
    installed_this_run: bool,
}

impl Wire {
    /// Post, read the stream it opens, then post whatever that read left to send. Each post's stop
    /// goes to the loop before the post itself, which blocks until the reply's headers arrive —
    /// a member ends a turn while it is still thinking, and this thread cannot hear the key.
    fn run(mut self, first: String) {
        let mut body = if first.is_empty() {
            Some(PostBody::Empty)
        } else {
            // The opening message posts alone. A line that lands in the same instant is the next
            // turn's, and joining it here would leave the turn it belongs to nothing to post.
            self.queue.push_back(first);
            Some(self.take_queue())
        };
        let mut attempts = 0u32;
        loop {
            let Some(post) = body.take() else { return };
            if let Some(stop) = self.session.stop() {
                let _ = self.evt.send(WireEvent::Stoppable(stop));
            }
            let stream = match self.session.post(post.clone()) {
                Ok(stream) => stream,
                Err(error) => {
                    if !self.reconnect(&mut attempts, &error) {
                        return;
                    }
                    body = Some(post);
                    continue;
                }
            };
            let mut got = false;
            let mut severed = None;
            for item in stream {
                match item {
                    Ok(directive) => {
                        got = true;
                        self.opened = true;
                        if self.handle(directive) {
                            break;
                        }
                    }
                    Err(error) => {
                        severed = Some(error);
                        break;
                    }
                }
            }
            if let Some(error) = severed {
                let resend = if got { None } else { Some(post) };
                if !self.reconnect(&mut attempts, &error) {
                    return;
                }
                body = resend.or(Some(PostBody::Empty));
                continue;
            }
            attempts = 0;
            if self.install {
                self.ensure_installed();
            }
            if !got && !self.opened {
                let _ = self.evt.send(WireEvent::Fatal(format!(
                    "No response from {}",
                    self.session.base()
                )));
                return;
            }
            let continues =
                self.op_reply.is_some() || self.poll.is_some() || !self.queue.is_empty();
            if self.evt.send(WireEvent::StreamEnd { continues }).is_err() {
                return;
            }
            body = self.next_body();
        }
    }

    /// True when the directive ends this stream (an op rendezvous).
    fn handle(&mut self, directive: Directive) -> bool {
        match directive {
            Directive::Run(op) => {
                let _ = self.evt.send(WireEvent::OpStarted(op.clone()));
                let reply = ops::run_op(&self.runtime, &self.session, &op);
                let _ = self
                    .evt
                    .send(WireEvent::OpFinished(op.clone(), reply.clone()));
                self.op_reply = Some(PostBody::OpReply {
                    op_id: op.op_id,
                    reply,
                });
                true
            }
            Directive::Since(cursor) => {
                self.session.since = Some(cursor);
                false
            }
            Directive::Poll(seconds) => {
                self.poll = Some(seconds);
                false
            }
            Directive::Token(token) => {
                self.home.store_credentials(&token);
                self.session.token = Some(token);
                false
            }
            Directive::Workspace(url) => {
                self.home.store_workspace(&url);
                self.home.store_gateway(&self.session.gateway_url);
                self.session.workspace_url = Some(url.clone());
                if self.session.channel == ONBOARDING_CHANNEL {
                    self.session.channel = random_channel();
                }
                update_resume(&self.session, self.session.tty);
                let _ = self.evt.send(WireEvent::WorkspaceChanged {
                    url,
                    channel: self.session.channel.clone(),
                });
                false
            }
            Directive::Install => {
                self.install = true;
                false
            }
            Directive::Unknown => false,
            other => {
                let _ = self.evt.send(WireEvent::Dir(other));
                false
            }
        }
    }

    fn ensure_installed(&mut self) {
        if self.installed_this_run {
            return;
        }
        self.installed_this_run = true;
        self.install = false;
        let session = &self.session;
        let outcome = config::install_self(&self.home, |target, dest| {
            session.fetch_client_binary(target, dest)
        });
        match outcome {
            Ok(installed) => {
                for line in installed.lines() {
                    let _ = self
                        .evt
                        .send(WireEvent::Dir(Directive::Note(line.to_string())));
                }
                self.session.installed = true;
            }
            Err(error) => {
                let _ = self.evt.send(WireEvent::Dir(Directive::Note(format!(
                    "Install failed: {error}"
                ))));
            }
        }
    }

    fn reconnect(&mut self, attempts: &mut u32, error: &str) -> bool {
        *attempts += 1;
        if !self.opened || *attempts > RECONNECT_ATTEMPTS {
            let said = if self.opened {
                error.to_string()
            } else {
                format!("No response from {} ({error})", self.session.base())
            };
            let _ = self.evt.send(WireEvent::Fatal(said));
            return false;
        }
        let _ = self.evt.send(WireEvent::Reconnecting {
            attempt: *attempts,
            retry_in_s: RECONNECT_PAUSE.as_secs(),
        });
        thread::sleep(RECONNECT_PAUSE);
        true
    }

    fn next_body(&mut self) -> Option<PostBody> {
        loop {
            self.drain_cmds();
            if let Some(reply) = self.op_reply.take() {
                self.poll = None;
                return Some(reply);
            }
            if !self.queue.is_empty() {
                return Some(self.take_queue());
            }
            if let Some(seconds) = self.poll.take() {
                if !self.detached {
                    thread::sleep(Duration::from_secs_f64(seconds.max(0.0)));
                    if self.queue.is_empty() {
                        return Some(PostBody::Empty);
                    }
                    continue;
                }
            }
            match self.cmd.recv() {
                Ok(command) => self.apply_cmd(command),
                Err(_) => return None,
            }
        }
    }

    /// Everything queued goes out as one post, each message recorded and echoed to the member.
    fn take_queue(&mut self) -> PostBody {
        let joined: Vec<String> = self.queue.drain(..).collect();
        for message in &joined {
            self.record(message);
            let _ = self.evt.send(WireEvent::MemberEcho(message.clone()));
        }
        self.detached = false;
        self.poll = None;
        PostBody::Message(joined.join("\n\n"))
    }

    fn drain_cmds(&mut self) {
        while let Ok(command) = self.cmd.try_recv() {
            self.apply_cmd(command);
        }
    }

    fn apply_cmd(&mut self, command: WireCmd) {
        match command {
            WireCmd::Say(text) => self.queue.push_back(text),
            WireCmd::SecretValue {
                sealed,
                slot,
                value,
            } => self.fulfill_secret(&sealed, &slot, &value),
            WireCmd::Detach => self.detached = true,
            WireCmd::Shutdown => {
                self.queue.clear();
                self.op_reply = None;
                self.poll = None;
            }
        }
    }

    fn fulfill_secret(&mut self, sealed: &str, slot: &str, value: &str) {
        if value.is_empty() {
            let _ = self
                .evt
                .send(WireEvent::Dir(Directive::Note(format!("Skipped {slot}"))));
            return;
        }
        let outcome = self.session.post_secret(sealed, slot, value);
        let note = match outcome {
            Ok(lines) if !lines.is_empty() => {
                for line in lines {
                    let _ = self.evt.send(WireEvent::Dir(Directive::Note(line)));
                }
                return;
            }
            Ok(_) => "No confirmation — ask the assistant to check.".to_string(),
            Err(error) => format!("No confirmation ({error}) — ask the assistant to check."),
        };
        let _ = self.evt.send(WireEvent::Dir(Directive::Note(note)));
    }

    fn record(&mut self, first_message: &str) {
        if self.recorded || self.session.workspace_url.is_none() {
            return;
        }
        self.recorded = true;
        record_conversation(
            &self.home.root,
            &PastConversation {
                channel: self.session.channel.clone(),
                opened_epoch: epoch_seconds(),
                first_message: first_message.to_string(),
            },
        );
    }
}

// ── tty mode ────────────────────────────────────────────────────────────────────────────────────

enum LoopEvent {
    Term(TermEvent),
    Wire(WireEvent),
    StdinClosed,
}

#[derive(Default)]
struct Gate {
    prompt: String,
    questions: VecDeque<(String, Vec<String>)>,
    answers: Vec<String>,
    many: bool,
    secrets: VecDeque<(String, String, String)>,
    asked: bool,
    exit: Option<i32>,
}

fn run_tty(session: Session, runtime: OpRuntime, home: config::Home, first: String) -> i32 {
    let host = session
        .workspace_url
        .clone()
        .unwrap_or_else(|| session.gateway_url.clone());
    let host = host
        .trim_start_matches("https://")
        .trim_start_matches("http://")
        .trim_end_matches('/')
        .to_string();
    let channel_name = session.channel.clone();
    let cwd = runtime.cwd.clone();
    let workspace_url = session.workspace_url.clone();

    let raw = ui::RawGuard::new();
    let mut app = App::new(&home.root, host, channel_name.clone(), cwd);
    let (evt_tx, evt_rx) = channel::<LoopEvent>();
    let (cmd_tx, cmd_rx) = channel::<WireCmd>();

    let term_tx = evt_tx.clone();
    thread::spawn(move || loop {
        match crossterm::event::read() {
            Ok(event) => {
                if term_tx.send(LoopEvent::Term(event)).is_err() {
                    return;
                }
            }
            Err(_) => return,
        }
    });

    let stop_evt = evt_tx.clone();
    let wire_evt = evt_tx;
    let wire = Wire {
        session,
        runtime,
        home,
        evt: wire_sender(wire_evt),
        cmd: cmd_rx,
        queue: VecDeque::new(),
        op_reply: None,
        poll: None,
        detached: false,
        recorded: false,
        opened: false,
        install: false,
        installed_this_run: false,
    };
    let first_for_wire = first.clone();
    thread::spawn(move || wire.run(first_for_wire));

    if !first.is_empty() {
        app.begin_turn();
    }
    let mut gate = Gate::default();
    let mut stop: Option<Stop> = None;
    let mut latest_workspace = workspace_url;
    let mut latest_channel = channel_name;
    let code = loop {
        let event = match evt_rx.recv_timeout(TICK) {
            Ok(event) => event,
            Err(RecvTimeoutError::Timeout) => {
                app.tick();
                app.paint();
                continue;
            }
            Err(RecvTimeoutError::Disconnected) => break 0,
        };
        match event {
            LoopEvent::Term(TermEvent::Key(key)) if key.kind != KeyEventKind::Release => {
                match app.on_key(key) {
                    Reply::None => {}
                    Reply::Send(text) => {
                        if app.is_working() {
                            app.push_queued(&text);
                        } else {
                            app.begin_turn();
                        }
                        let _ = cmd_tx.send(WireCmd::Say(text));
                    }
                    Reply::Choice(choice) => {
                        let Some((prompt, _)) = gate.questions.pop_front() else {
                            continue;
                        };
                        gate.answers.push(if gate.many {
                            format!("{prompt}: {choice}")
                        } else {
                            choice
                        });
                        if let Some((next_prompt, options)) = gate.questions.front() {
                            if options.is_empty() {
                                app.ask_prompt(&next_prompt.clone());
                            } else {
                                app.choose(&next_prompt.clone(), &options.clone());
                            }
                        } else {
                            let reply = gate.answers.join("\n");
                            gate.answers.clear();
                            app.begin_turn();
                            let _ = cmd_tx.send(WireCmd::Say(reply));
                        }
                    }
                    Reply::ChoiceCancelled => break 0,
                    Reply::Secret(value) => {
                        if let Some((sealed, slot, _)) = gate.secrets.pop_front() {
                            let _ = cmd_tx.send(WireCmd::SecretValue {
                                sealed,
                                slot,
                                value,
                            });
                        }
                        if let Some((_, _, prompt)) = gate.secrets.front() {
                            app.secret_begin(&prompt.clone());
                        } else {
                            settle(&mut app, &mut gate);
                        }
                    }
                    Reply::Stop => {
                        if let Some(prepared) = stop.take() {
                            let notify = stop_evt.clone();
                            thread::spawn(move || {
                                if let Err(error) = prepared.send() {
                                    let _ = notify.send(LoopEvent::Wire(WireEvent::Dir(
                                        Directive::Note(format!("Not stopped: {error}")),
                                    )));
                                }
                            });
                        }
                    }
                    Reply::Detach => {
                        let _ = cmd_tx.send(WireCmd::Detach);
                        app.end_turn(false);
                        app.note("Detached; the turn continues, and a new message rejoins it.");
                        app.ask_prompt("");
                    }
                    Reply::Exit => break 0,
                }
                app.paint();
            }
            LoopEvent::Term(TermEvent::Paste(text)) => {
                app.on_paste(text);
                app.paint();
            }
            LoopEvent::Term(TermEvent::Resize(..)) => {
                app.resize();
                app.paint();
            }
            LoopEvent::Term(TermEvent::Mouse(mouse)) => {
                app.on_mouse(mouse);
                app.paint();
            }
            LoopEvent::Term(_) => {}
            LoopEvent::StdinClosed => {}
            LoopEvent::Wire(wire_event) => match wire_event {
                WireEvent::Dir(directive) => {
                    apply_directive(&mut app, &mut gate, directive);
                    app.paint();
                }
                WireEvent::OpStarted(op) => {
                    app.op_started(&op);
                    app.paint();
                }
                WireEvent::OpFinished(op, result) => {
                    app.op_finished(&op, &result);
                    app.paint();
                }
                WireEvent::MemberEcho(text) => {
                    if !app.is_working() {
                        app.begin_turn();
                    }
                    app.queued_sent(&text);
                    app.paint();
                }
                WireEvent::Reconnecting {
                    attempt,
                    retry_in_s,
                } => {
                    app.reconnecting(attempt, RECONNECT_ATTEMPTS, retry_in_s);
                    app.paint();
                }
                WireEvent::WorkspaceChanged { url, channel } => {
                    latest_workspace = Some(url.clone());
                    latest_channel = channel.clone();
                    let host = url
                        .trim_start_matches("https://")
                        .trim_start_matches("http://")
                        .trim_end_matches('/')
                        .to_string();
                    app.set_endpoint(host, channel);
                }
                WireEvent::Stoppable(prepared) => stop = Some(prepared),
                WireEvent::StreamEnd { continues } => {
                    if let Some(code) = gate.exit.take() {
                        break code;
                    }
                    if continues {
                        app.paint();
                        continue;
                    }
                    if !gate.secrets.is_empty() {
                        app.end_turn(false);
                        let prompt = gate.secrets.front().map(|(_, _, p)| p.clone());
                        if let Some(prompt) = prompt {
                            app.secret_begin(&prompt);
                        }
                    } else if !settle(&mut app, &mut gate) {
                        break 0;
                    }
                    app.paint();
                }
                WireEvent::Fatal(message) => {
                    app.close();
                    drop(raw);
                    die(&message);
                }
            },
        }
    };
    let _ = cmd_tx.send(WireCmd::Shutdown);
    app.close();
    drop(raw);
    print_resume(latest_workspace.as_deref(), &latest_channel, true);
    code
}

/// End-of-stream disposition once secrets are done: the next question, the prompt, or — with
/// nothing left to do — the session's end (`false`).
fn settle(app: &mut App, gate: &mut Gate) -> bool {
    if let Some((prompt, options)) = gate.questions.front() {
        app.end_turn(true);
        if options.is_empty() {
            app.ask_prompt(&prompt.clone());
        } else {
            app.choose(&prompt.clone(), &options.clone());
        }
        return true;
    }
    if gate.asked {
        gate.asked = false;
        app.end_turn(true);
        let prompt = std::mem::take(&mut gate.prompt);
        app.ask_prompt(&prompt);
        return true;
    }
    app.end_turn(false);
    false
}

fn apply_directive(app: &mut App, gate: &mut Gate, directive: Directive) {
    match directive {
        Directive::Say(text) => app.say(&text),
        Directive::You(text) => app.member_replay(&text),
        Directive::Note(text) => app.note(&text),
        Directive::Txt(chunk) => app.txt(&chunk),
        Directive::Status(text) => app.status_text(&text),
        Directive::File { name, size, url } => app.file(&name, &size, &url),
        Directive::Ask(prompt) => {
            gate.asked = true;
            gate.prompt = prompt;
        }
        Directive::Choose { prompt, options } => {
            gate.questions.push_back((prompt, options));
            gate.many = gate.questions.len() > 1;
        }
        Directive::Secret {
            sealed,
            slot,
            prompt,
        } => gate.secrets.push_back((sealed, slot, prompt)),
        Directive::Exit(code) => gate.exit = Some(code),
        _ => {}
    }
}

fn wire_sender(tx: Sender<LoopEvent>) -> Sender<WireEvent> {
    let (wire_tx, wire_rx) = channel::<WireEvent>();
    thread::spawn(move || {
        for event in wire_rx {
            if tx.send(LoopEvent::Wire(event)).is_err() {
                return;
            }
        }
    });
    wire_tx
}

// ── plain mode ──────────────────────────────────────────────────────────────────────────────────

fn run_plain(session: Session, runtime: OpRuntime, home: config::Home, first: String) -> i32 {
    let tty = std::io::stdout().is_terminal();
    let (evt_tx, evt_rx) = channel::<LoopEvent>();
    let (cmd_tx, cmd_rx) = channel::<WireCmd>();
    let workspace_url = session.workspace_url.clone();
    let mut latest_channel = session.channel.clone();
    let wire = Wire {
        session,
        runtime,
        home,
        evt: wire_sender(evt_tx),
        cmd: cmd_rx,
        queue: VecDeque::new(),
        op_reply: None,
        poll: None,
        detached: false,
        recorded: false,
        opened: false,
        install: false,
        installed_this_run: false,
    };
    thread::spawn(move || wire.run(first));

    let mut out = Plain::new();
    let mut gate = Gate::default();
    let mut latest_workspace = workspace_url;
    let code = loop {
        let event = match evt_rx.recv() {
            Ok(LoopEvent::Wire(event)) => event,
            Ok(LoopEvent::Term(_)) | Ok(LoopEvent::StdinClosed) => continue,
            Err(_) => break 0,
        };
        match event {
            WireEvent::Dir(directive) => match directive {
                Directive::Say(text) => out.say(&text),
                Directive::You(text) => out.member(&text),
                Directive::Note(text) => out.note(&text),
                Directive::Txt(chunk) => out.txt(&chunk),
                Directive::Status(text) => out.status(&text),
                Directive::File { name, size, url } => out.file(&name, &size, &url),
                Directive::Ask(prompt) => {
                    gate.asked = true;
                    gate.prompt = prompt;
                }
                Directive::Choose { prompt, options } => {
                    gate.questions.push_back((prompt, options));
                    gate.many = gate.questions.len() > 1;
                }
                Directive::Secret {
                    sealed,
                    slot,
                    prompt,
                } => gate.secrets.push_back((sealed, slot, prompt)),
                Directive::Exit(code) => gate.exit = Some(code),
                _ => {}
            },
            WireEvent::OpStarted(_) | WireEvent::OpFinished(..) => {}
            WireEvent::MemberEcho(_) => {}
            WireEvent::Reconnecting { .. } => {}
            WireEvent::WorkspaceChanged { url, channel } => {
                latest_workspace = Some(url);
                latest_channel = channel;
            }
            WireEvent::Stoppable(_) => {}
            WireEvent::StreamEnd { continues } => {
                if let Some(code) = gate.exit.take() {
                    break code;
                }
                if continues {
                    continue;
                }
                out.end_stream();
                while let Some((sealed, slot, prompt)) = gate.secrets.pop_front() {
                    let value = out.secret(&prompt).unwrap_or_default();
                    let _ = cmd_tx.send(WireCmd::SecretValue {
                        sealed,
                        slot,
                        value,
                    });
                }
                if !gate.questions.is_empty() {
                    let mut collected = Vec::new();
                    let mut cancelled = false;
                    while let Some((prompt, options)) = gate.questions.pop_front() {
                        let answer = if options.is_empty() {
                            out.ask(&prompt)
                        } else {
                            out.menu(&prompt, &options)
                        };
                        let Some(answer) = answer else {
                            cancelled = true;
                            break;
                        };
                        collected.push(if gate.many {
                            format!("{prompt}: {answer}")
                        } else {
                            answer
                        });
                    }
                    if cancelled {
                        break 0;
                    }
                    let _ = cmd_tx.send(WireCmd::Say(collected.join("\n")));
                } else if gate.asked {
                    gate.asked = false;
                    match out.ask(&std::mem::take(&mut gate.prompt)) {
                        Some(reply) => {
                            let _ = cmd_tx.send(WireCmd::Say(reply));
                        }
                        None => break 0,
                    }
                } else {
                    break 0;
                }
            }
            WireEvent::Fatal(message) => {
                die(&message);
            }
        }
    };
    let _ = cmd_tx.send(WireCmd::Shutdown);
    print_resume(latest_workspace.as_deref(), &latest_channel, tty);
    code
}

// ── json mode ───────────────────────────────────────────────────────────────────────────────────

fn run_json(session: Session, runtime: OpRuntime, home: config::Home, first: String) -> i32 {
    let channel_name = session.channel.clone();
    let workspace_url = session.workspace_url.clone();
    let (evt_tx, evt_rx) = channel::<LoopEvent>();
    let (cmd_tx, cmd_rx) = channel::<WireCmd>();

    let stdin_tx = evt_tx.clone();
    thread::spawn(move || {
        let stdin = std::io::stdin();
        for line in stdin.lock().lines() {
            let Ok(line) = line else { break };
            if stdin_tx
                .send(LoopEvent::Term(TermEvent::Paste(line)))
                .is_err()
            {
                return;
            }
        }
        let _ = stdin_tx.send(LoopEvent::StdinClosed);
    });

    let wire = Wire {
        session,
        runtime,
        home,
        evt: wire_sender(evt_tx),
        cmd: cmd_rx,
        queue: VecDeque::new(),
        op_reply: None,
        poll: None,
        detached: false,
        recorded: false,
        opened: false,
        install: false,
        installed_this_run: false,
    };
    let first_nonempty = !first.is_empty();
    thread::spawn(move || wire.run(first));

    let mut driver = jsonio::Driver::new();
    let mut exit_code: Option<i32> = None;
    let mut in_turn = false;
    let mut stdin_open = true;
    emit_json(&driver.session_start(&channel_name, workspace_url.as_deref()));
    if first_nonempty {
        emit_json(&driver.on_turn_start());
        in_turn = true;
    }
    loop {
        let event = match evt_rx.recv() {
            Ok(event) => event,
            Err(_) => return exit_code.unwrap_or(0),
        };
        match event {
            LoopEvent::Term(TermEvent::Paste(line)) => {
                if line.trim().is_empty() {
                    continue;
                }
                match jsonio::parse_command(&line) {
                    Ok(command) => match driver.answer_body(command) {
                        Ok(jsonio::AnswerRouting::Post(text)) => {
                            if !in_turn {
                                emit_json(&driver.on_turn_start());
                                in_turn = true;
                            }
                            let _ = cmd_tx.send(WireCmd::Say(text));
                        }
                        Ok(jsonio::AnswerRouting::Secret {
                            sealed,
                            slot,
                            value,
                        }) => {
                            let _ = cmd_tx.send(WireCmd::SecretValue {
                                sealed,
                                slot,
                                value,
                            });
                        }
                        Ok(jsonio::AnswerRouting::Detach) => {
                            let _ = cmd_tx.send(WireCmd::Detach);
                            return exit_code.unwrap_or(0);
                        }
                        Ok(jsonio::AnswerRouting::Shutdown) => {
                            let _ = cmd_tx.send(WireCmd::Shutdown);
                            return exit_code.unwrap_or(0);
                        }
                        Err(error_event) => emit_json(&error_event),
                    },
                    Err(event) => emit_json(&event),
                }
            }
            LoopEvent::Term(_) => {}
            LoopEvent::StdinClosed => {
                stdin_open = false;
                if !in_turn {
                    return exit_code.unwrap_or(0);
                }
            }
            LoopEvent::Wire(wire_event) => match wire_event {
                WireEvent::Dir(directive) => {
                    if let Directive::Exit(code) = &directive {
                        exit_code = Some(*code);
                    }
                    for out in driver.on_directive(&directive) {
                        emit_json(&out);
                    }
                }
                WireEvent::OpStarted(op) => emit_json(&driver.on_op_started(&op)),
                WireEvent::OpFinished(op, result) => {
                    emit_json(&driver.on_op_finished(&op.op_id, &result));
                }
                WireEvent::MemberEcho(_) => {}
                WireEvent::Reconnecting { .. } => {}
                WireEvent::WorkspaceChanged { .. } => {}
                WireEvent::Stoppable(_) => {}
                WireEvent::StreamEnd { continues } => {
                    if continues {
                        continue;
                    }
                    if in_turn {
                        emit_json(&driver.on_turn_end());
                        in_turn = false;
                    }
                    if let Some(code) = exit_code {
                        return code;
                    }
                    if !stdin_open {
                        return 0;
                    }
                }
                WireEvent::Fatal(message) => {
                    emit_json(&driver.on_stream_error(&message, true));
                    return 1;
                }
            },
        }
    }
}

fn emit_json(event: &jsonio::Event) {
    use std::io::Write;
    let mut out = std::io::stdout();
    let _ = out.write_all(jsonio::emit(event).as_bytes());
    let _ = out.flush();
}

#[cfg(unix)]
mod interrupt {
    use std::ffi::CString;
    use std::sync::atomic::{AtomicBool, AtomicPtr, AtomicUsize, Ordering};

    static RESUME: AtomicUsize = AtomicUsize::new(0);
    static MODES: AtomicPtr<(libc::c_int, libc::termios)> = AtomicPtr::new(std::ptr::null_mut());
    static ALT: AtomicBool = AtomicBool::new(false);

    /// Hold the terminal's modes as they are, before raw mode replaces them: the handler exits
    /// through `_exit`, which runs no destructor, so it puts these back itself or the member's
    /// shell is left without echo.
    pub fn hold_modes() {
        if unsafe { libc::isatty(libc::STDIN_FILENO) } != 1 {
            return;
        }
        let mut modes: libc::termios = unsafe { std::mem::zeroed() };
        if unsafe { libc::tcgetattr(libc::STDIN_FILENO, &mut modes) } != 0 {
            return;
        }
        let held = Box::into_raw(Box::new((libc::STDIN_FILENO, modes)));
        drop_held(MODES.swap(held, Ordering::SeqCst));
    }

    pub fn release_modes() {
        drop_held(MODES.swap(std::ptr::null_mut(), Ordering::SeqCst));
    }

    /// Whether the alternate screen is up, and so whether the handler leaves it.
    pub fn hold_alt(entered: bool) {
        ALT.store(entered, Ordering::SeqCst);
    }

    fn drop_held(held: *mut (libc::c_int, libc::termios)) {
        if !held.is_null() {
            drop(unsafe { Box::from_raw(held) });
        }
    }

    /// Put the terminal back from inside the handler. Async-signal-safe: an atomic load, `write`,
    /// and `tcsetattr`, all on the POSIX safe list — crossterm's own calls are not.
    fn restore_terminal() {
        if ALT.load(Ordering::SeqCst) {
            let leave = crate::ui::term::ALT_LEAVE.as_bytes();
            unsafe {
                libc::write(1, leave.as_ptr() as *const libc::c_void, leave.len());
            }
        }
        let held = MODES.load(Ordering::SeqCst);
        if !held.is_null() {
            unsafe { libc::tcsetattr((*held).0, libc::TCSANOW, &(*held).1) };
        }
    }

    pub fn set_resume(line: &str) {
        let rendered = if line.is_empty() {
            "\x1b[?25h\n".to_string()
        } else {
            format!("\x1b[?25h\n{line}\n")
        };
        let owned = CString::new(rendered).expect("resume line has no NUL");
        RESUME.store(owned.into_raw() as usize, Ordering::SeqCst);
    }

    extern "C" fn on_sigint(_signal: libc::c_int) {
        restore_terminal();
        let pointer = RESUME.load(Ordering::SeqCst);
        unsafe {
            if pointer != 0 {
                let length = libc::strlen(pointer as *const libc::c_char);
                libc::write(2, pointer as *const libc::c_void, length);
            } else {
                let fallback = b"\x1b[?25h\n";
                libc::write(2, fallback.as_ptr() as *const libc::c_void, fallback.len());
            }
            libc::_exit(130);
        }
    }

    pub fn install() {
        let handler = on_sigint as extern "C" fn(libc::c_int);
        unsafe {
            libc::signal(libc::SIGINT, handler as libc::sighandler_t);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gateway_resolution_prefers_env_then_stored_then_default() {
        let env = Some("https://testing.example".to_string());
        let stored = Some("https://stored.example".to_string());
        assert_eq!(
            resolve_gateway(env.clone(), stored.clone()),
            "https://testing.example"
        );
        assert_eq!(resolve_gateway(None, stored), "https://stored.example");
        assert_eq!(resolve_gateway(None, None), GATEWAY_URL_DEFAULT);
    }

    #[test]
    fn private_workdirs_are_unique_and_private() {
        let first = private_workdir().unwrap();
        let second = private_workdir().unwrap();
        assert_ne!(first, second);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&first).unwrap().permissions().mode();
            assert_eq!(mode & 0o777, 0o700);
        }
        let _ = std::fs::remove_dir_all(&first);
        let _ = std::fs::remove_dir_all(&second);
    }

    #[test]
    fn private_workdir_never_adopts_an_existing_path() {
        let taken = private_workdir().unwrap();
        assert!(std::fs::DirBuilder::new().create(&taken).is_err());
        let _ = std::fs::remove_dir_all(&taken);
    }

    #[test]
    fn resume_command_needs_a_workspace() {
        assert_eq!(resume_command(None, "abc"), None);
        assert_eq!(
            resume_command(Some("https://w"), "abc").as_deref(),
            Some("ufo --resume abc")
        );
    }
}
