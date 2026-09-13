use std::collections::VecDeque;
use std::env;
use std::io::{BufRead, IsTerminal};
use std::process;
use std::sync::mpsc::{channel, Receiver, RecvTimeoutError, Sender};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crossterm::event::{Event as TermEvent, KeyEventKind};

use ufo::clipboard::{self, Clip};
use ufo::cmd::{cp, fscli, llm, run, tools};
#[cfg(unix)]
use ufo::interrupt;
use ufo::ops::{self, OpRuntime};
use ufo::ui::conversations::{surface_word, Fetch};
use ufo::ui::plain::Plain;
use ufo::ui::{self, App, ClipEntry, Reply};
use ufo::wire::{
    AuthorizationAnswer, AuthorizationOption, ConversationRow, Directive, Lister, OpRequest,
    PostBody, SendLane, SentAck, Session, Stop, Target,
};
use ufo::{config, jsonio, pr};

const HELP: &str = "\
Opens a conversation with your workspace assistant.

Usage: ufo [--resume ID] [--remote] [--model MODEL] [--no-internet] [--environment FILE]
           [--json] [message...]
       ufo login | logout
       ufo cp SRC DST  (one side is CHANNEL:PATH; directories sync)
       ufo fs {read|write|edit|grep|glob|changes} <json>
       ufo llm [--model MODEL] [--max-tokens N] PROMPT
       ufo run [--task PATH] [--detach] -- COMMAND [ARG...]
       ufo tool TOOL [--describe]

Commands:
  login          Sign in again.
  logout         Sign out.
  cp             Copy files or sync a folder with a conversation's workspace.
  fs             Run one file op inside a sandbox and print its JSON result.
  llm            Ask a model one question through the sandbox's egress proxy.
  run            Run a command with task state and sandbox egress.
  tool           Describe or call an object or connector tool with JSON.

Options:
  --resume ID    Open a conversation by its channel or id. With no message and no id,
                 ufo opens on the list of your chats.
  --remote       Run in the workspace's sandbox instead of the current directory.
  --model MODEL  Run each turn on this model.
  --no-internet  Run each turn without public internet access.
  --environment FILE  Pin this environment document for each turn: a YAML or JSON
                 overrides file uploaded once, or its sha256: digest.
  --json         Read and write JSON events on stdin and stdout.
  -h, --help     Show this help.
";

const GATEWAY_URL_DEFAULT: &str = "https://ufo.ai";
const ONBOARDING_CHANNEL: &str = "onboard";
const RECONNECT_PAUSE: Duration = Duration::from_secs(1);
const RECONNECT_ATTEMPTS: u32 =
    (ufo::wire::CONNECT_TIMEOUT.as_secs() / RECONNECT_PAUSE.as_secs()) as u32;
const TICK: Duration = Duration::from_millis(80);
const SEND_ATTEMPTS: usize = 3;
const SEND_RETRY: Duration = Duration::from_millis(500);
const CONVERSATION_ID_LEN: usize = 36;
const CONVERSATION_ID_DASHES: [usize; 4] = [8, 13, 18, 23];

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("cp") => process::exit(cp::main(&args[1..])),
        Some("fs") => process::exit(fscli::main(&args[1..])),
        Some("llm") => process::exit(llm::main(&args[1..])),
        Some("run") => process::exit(run::main(&args[1..])),
        Some("tool") => process::exit(tools::main(&args[1..])),
        _ => {}
    }
    let home = config::Home::resolve();
    let mut rest: &[String] = &args;
    let mut resumed: Option<String> = None;
    let mut login = false;
    let mut remote = false;
    let mut json = false;
    let mut model: Option<String> = None;
    let mut no_internet = false;
    let mut environment: Option<String> = None;
    loop {
        match rest.first().map(String::as_str) {
            Some("--help") | Some("-h") => {
                print!("{HELP}");
                return;
            }
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
            Some("--remote") => {
                remote = true;
                rest = &rest[1..];
            }
            Some("--model") => {
                let id = rest
                    .get(1)
                    .filter(|id| !id.starts_with("--"))
                    .unwrap_or_else(|| die("--model requires a model id"));
                model = Some(id.clone());
                rest = &rest[2..];
            }
            Some("--no-internet") => {
                no_internet = true;
                rest = &rest[1..];
            }
            Some("--environment") => {
                let url = rest
                    .get(1)
                    .filter(|url| !url.starts_with("--"))
                    .unwrap_or_else(|| die("--environment requires a file or digest"));
                environment = Some(url.clone());
                rest = &rest[2..];
            }
            Some("--resume") => match rest.get(1) {
                Some(id) if !id.starts_with("--") => {
                    resumed = Some(id.clone());
                    rest = &rest[2..];
                }
                _ => die("--resume needs a conversation id or channel."),
            },
            _ => break,
        }
    }
    #[cfg(unix)]
    if !json {
        adopt_tty_stdin();
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
    let named = match (env_nonempty("UFO_CHANNEL"), resumed) {
        (Some(channel), _) => Some(Target::Channel(channel)),
        (None, Some(id)) if is_conversation_id(&id) => Some(Target::Conversation(id)),
        (None, Some(channel)) => Some(Target::Channel(channel)),
        (None, None) => None,
    };
    if workspace_url.is_none()
        && named
            .as_ref()
            .is_some_and(|target| target.channel().is_none())
    {
        die("Sign in first: run ufo, then ufo --resume <id>.");
    }
    let tty = std::io::stdout().is_terminal();
    let pick = named.is_none()
        && message.is_empty()
        && workspace_url.is_some()
        && !json
        && tty
        && ui::wants_fx();
    let target = named.unwrap_or_else(|| {
        if workspace_url.is_some() {
            Target::Channel(random_channel())
        } else {
            Target::Channel(ONBOARDING_CHANNEL.to_string())
        }
    });
    let launch_dir = env::current_dir()
        .and_then(|dir| dir.canonicalize())
        .unwrap_or_else(|error| die(&format!("could not resolve the working directory: {error}")));
    let cwd_header = if remote {
        None
    } else {
        launch_dir
            .to_str()
            .filter(|path| path.starts_with('/'))
            .map(String::from)
    };
    let installed = config::installed(&home);
    let workdir = private_workdir().unwrap_or_else(|error| die(&error));
    let mut session = Session::new(
        resolve_gateway(env_nonempty("UFO_URL"), home.gateway()),
        workspace_url,
        target,
        token,
        session_id,
        cwd_header,
        installed,
        tty && !json,
    )
    .with_runtime_config(model, no_internet, environment);
    session
        .resolve_environment()
        .unwrap_or_else(|error| die(&error));
    #[cfg(unix)]
    interrupt::install();
    update_resume(
        session.workspace_url.as_deref(),
        session.target.label(),
        tty && !json,
    );
    config::sweep_retired(&home);
    let scratch = workdir.clone();
    let stash_home = home.root.clone();
    let runtime = OpRuntime {
        workdir,
        cwd: launch_dir,
        home: home.clone(),
    };
    let code = if json {
        run_json(session, runtime, home, message)
    } else if tty && ui::wants_fx() {
        run_tty(session, runtime, home, message, pick)
    } else {
        run_plain(session, runtime, home, message)
    };
    let _ = std::fs::remove_dir_all(&scratch);
    clipboard::sweep_stash(&stash_home);
    process::exit(code);
}

fn die(message: &str) -> ! {
    eprintln!("ufo: {message}");
    process::exit(1);
}

/// The terminal is adopted by its real device name, because macOS refuses to register the `/dev/tty`
/// alias with kqueue, which is what the event reader polls. The alias stays the fully-redirected fallback.
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

fn retract_instant(lane: SendLane, text: String, arrival_id: String, evt: Sender<WireEvent>) {
    thread::spawn(move || {
        let retracted = lane.retract(&arrival_id).unwrap_or(false);
        let _ = evt.send(WireEvent::Retracted {
            text,
            arrival_id,
            retracted,
        });
    });
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum MemberPost {
    Message(String),
    Authorization(AuthorizationAnswer),
}

impl MemberPost {
    fn text(&self) -> &str {
        match self {
            MemberPost::Message(text) => text,
            MemberPost::Authorization(answer) => &answer.text,
        }
    }

    fn command(self) -> WireCmd {
        match self {
            MemberPost::Message(text) => WireCmd::Say(text),
            MemberPost::Authorization(answer) => WireCmd::Authorize(answer),
        }
    }
}

fn send_instant(lane: SendLane, post: MemberPost, evt: Sender<WireEvent>, cmd: Sender<WireCmd>) {
    thread::spawn(move || {
        let send_id = random_hex::<16>();
        for attempt in 0..SEND_ATTEMPTS {
            let sent = match &post {
                MemberPost::Message(text) => lane.send(&send_id, text),
                MemberPost::Authorization(answer) => lane.authorize(&send_id, answer),
            };
            match sent {
                Ok(ack) => {
                    let _ = evt.send(WireEvent::Sent {
                        text: post.text().to_string(),
                        ack,
                    });
                    return;
                }
                Err(_) if attempt + 1 < SEND_ATTEMPTS => thread::sleep(SEND_RETRY),
                Err(_) => {}
            }
        }
        let _ = cmd.send(post.command());
    });
}

fn stop_instant(prepared: Stop, evt: Sender<WireEvent>) {
    thread::spawn(move || {
        if let Err(error) = prepared.send() {
            let _ = evt.send(WireEvent::Dir(Directive::Note(format!(
                "Not stopped: {error}"
            ))));
        }
    });
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
        let builder = std::fs::DirBuilder::new();
        #[cfg(unix)]
        let mut builder = builder;
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

fn update_resume(workspace_url: Option<&str>, label: &str, tty: bool) {
    #[cfg(unix)]
    {
        if !tty {
            interrupt::set_resume("");
            return;
        }
        match resume_command(workspace_url, label) {
            Some(command) if wants_style(tty) => interrupt::set_resume(&format!(
                "\x1b[2mResume this conversation: \x1b[0m\x1b[1m{command}\x1b[0m"
            )),
            Some(command) => interrupt::set_resume(&format!("Resume this conversation: {command}")),
            None => interrupt::set_resume(""),
        }
    }
    #[cfg(not(unix))]
    {
        let _ = (workspace_url, label, tty);
    }
}

fn is_conversation_id(text: &str) -> bool {
    text.len() == CONVERSATION_ID_LEN
        && text.char_indices().all(|(at, ch)| {
            if CONVERSATION_ID_DASHES.contains(&at) {
                ch == '-'
            } else {
                ch.is_ascii_hexdigit()
            }
        })
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

enum WireEvent {
    Dir(Directive),
    OpStarted(OpRequest),
    OpFinished(OpRequest, Result<Vec<u8>, String>),
    MemberEcho(String),
    Reconnecting {
        attempt: u32,
        retry_in_s: u64,
    },
    WorkspaceChanged {
        url: String,
        channel: String,
        seed: Box<Session>,
    },
    Stoppable(Stop),
    Sendable(SendLane),
    Sent {
        text: String,
        ack: SentAck,
    },
    Retracted {
        text: String,
        arrival_id: String,
        retracted: bool,
    },
    StreamEnd {
        continues: bool,
        listening: bool,
    },
    Fatal(String),
}

enum WireCmd {
    Say(String),
    Authorize(AuthorizationAnswer),
    SecretValue {
        sealed: String,
        slot: String,
        value: String,
    },
    Detach,
    Wake,
    Shutdown,
}

struct Wire {
    session: Session,
    runtime: OpRuntime,
    home: config::Home,
    evt: Sender<WireEvent>,
    cmd: Receiver<WireCmd>,
    queue: VecDeque<MemberPost>,
    op_reply: Option<PostBody>,
    poll: Option<f64>,
    listen: Option<f64>,
    heed_listen: bool,
    detached: bool,
    opened: bool,
    install: bool,
    installed_this_run: bool,
    pause: Duration,
}

struct Reconnect {
    attempt: u32,
    remaining: Duration,
    failed_at: Instant,
}

impl Wire {
    /// Each post's stop goes to the loop before the post itself, which blocks until the reply's headers
    /// arrive — a member ends a turn while it is still thinking, and this thread cannot hear the key.
    fn run(mut self, first: String) {
        let _ = ufo::system_skills::sync(&self.home, &self.session);
        let mut body = if first.is_empty() {
            Some(PostBody::Empty)
        } else {
            self.queue.push_back(MemberPost::Message(first));
            Some(self.take_queue())
        };
        let mut reconnect = None;
        loop {
            let Some(post) = body.take() else { return };
            if let Some(stop) = self.session.stop() {
                let _ = self.evt.send(WireEvent::Stoppable(stop));
            }
            if let Some(lane) = self.session.send_lane() {
                let _ = self.evt.send(WireEvent::Sendable(lane));
            }
            let stream = match self.session.post(post.clone()) {
                Ok(stream) => stream,
                Err(error) => {
                    if matches!(post, PostBody::Listen) {
                        body = self.next_body();
                        continue;
                    }
                    if !self.reconnect(&mut reconnect, &error, None) {
                        return;
                    }
                    body = Some(post);
                    continue;
                }
            };
            self.listen = None;
            let opened_at = Instant::now();
            let mut got = false;
            let mut severed = None;
            for item in stream {
                match item {
                    Ok(directive) => {
                        reconnect = None;
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
                if !got && matches!(post, PostBody::Listen) {
                    body = self.next_body();
                    continue;
                }
                let resend = if got { None } else { Some(post) };
                if !self.reconnect(&mut reconnect, &error, Some(opened_at)) {
                    return;
                }
                body = resend.or(Some(PostBody::Empty));
                continue;
            }
            reconnect = None;
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
            let listening = self.listen.is_some();
            if self
                .evt
                .send(WireEvent::StreamEnd {
                    continues,
                    listening,
                })
                .is_err()
            {
                return;
            }
            body = self.next_body();
        }
    }

    fn handle(&mut self, directive: Directive) -> bool {
        match directive {
            Directive::Run(op) => {
                let visible = op.kind != ops::OP_SKILLS;
                if visible {
                    let _ = self.evt.send(WireEvent::OpStarted(op.clone()));
                }
                let reply = ops::run_op(&self.runtime, &self.session, &op);
                if visible {
                    let _ = self
                        .evt
                        .send(WireEvent::OpFinished(op.clone(), reply.clone()));
                }
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
            Directive::Listen(seconds) => {
                if self.heed_listen {
                    self.listen = Some(seconds);
                }
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
                if self.session.target.channel() == Some(ONBOARDING_CHANNEL) {
                    self.session.target = Target::Channel(random_channel());
                }
                update_resume(
                    self.session.workspace_url.as_deref(),
                    self.session.target.label(),
                    self.session.tty,
                );
                let _ = self.evt.send(WireEvent::WorkspaceChanged {
                    url,
                    channel: self.session.target.label().to_string(),
                    seed: Box::new(self.session.retarget(self.session.target.clone())),
                });
                let _ = ufo::system_skills::sync(&self.home, &self.session);
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

    fn reconnect(
        &mut self,
        reconnect: &mut Option<Reconnect>,
        error: &str,
        opened_at: Option<Instant>,
    ) -> bool {
        self.reconnect_at(reconnect, error, opened_at, Instant::now())
    }

    fn reconnect_at(
        &mut self,
        reconnect: &mut Option<Reconnect>,
        error: &str,
        opened_at: Option<Instant>,
        now: Instant,
    ) -> bool {
        let recovery = reconnect.get_or_insert(Reconnect {
            attempt: 0,
            remaining: ufo::wire::CONNECT_TIMEOUT,
            failed_at: now,
        });
        let offline_until = opened_at.unwrap_or(now);
        recovery.remaining = recovery
            .remaining
            .saturating_sub(offline_until.saturating_duration_since(recovery.failed_at));
        recovery.failed_at = now;
        if !self.opened || recovery.remaining.is_zero() {
            let said = if self.opened {
                error.to_string()
            } else {
                format!("No response from {} ({error})", self.session.base())
            };
            let _ = self.evt.send(WireEvent::Fatal(said));
            return false;
        }
        recovery.attempt += 1;
        let _ = self.evt.send(WireEvent::Reconnecting {
            attempt: recovery.attempt,
            retry_in_s: RECONNECT_PAUSE.as_secs(),
        });
        thread::sleep(self.pause.min(recovery.remaining));
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
            if let Some(seconds) = self.listen.filter(|_| !self.detached) {
                match self
                    .cmd
                    .recv_timeout(Duration::from_secs_f64(seconds.max(0.0)))
                {
                    Ok(command) => self.apply_cmd(command),
                    Err(RecvTimeoutError::Timeout) => {
                        self.listen = None;
                        return Some(PostBody::Listen);
                    }
                    Err(RecvTimeoutError::Disconnected) => return None,
                }
                continue;
            }
            match self.cmd.recv() {
                Ok(command) => self.apply_cmd(command),
                Err(_) => return None,
            }
        }
    }

    fn take_queue(&mut self) -> PostBody {
        let first = self.queue.pop_front().expect("a queued post exists");
        self.detached = false;
        self.poll = None;
        match first {
            MemberPost::Authorization(answer) => {
                let _ = self.evt.send(WireEvent::MemberEcho(answer.text.clone()));
                PostBody::Authorization(answer)
            }
            MemberPost::Message(first) => {
                let mut joined = vec![first];
                while matches!(self.queue.front(), Some(MemberPost::Message(_))) {
                    let Some(MemberPost::Message(message)) = self.queue.pop_front() else {
                        unreachable!()
                    };
                    joined.push(message);
                }
                for message in &joined {
                    let _ = self.evt.send(WireEvent::MemberEcho(message.clone()));
                }
                PostBody::Message(joined.join("\n\n"))
            }
        }
    }

    fn drain_cmds(&mut self) {
        while let Ok(command) = self.cmd.try_recv() {
            self.apply_cmd(command);
        }
    }

    fn apply_cmd(&mut self, command: WireCmd) {
        match command {
            WireCmd::Say(text) => self.queue.push_back(MemberPost::Message(text)),
            WireCmd::Authorize(answer) => self.queue.push_back(MemberPost::Authorization(answer)),
            WireCmd::SecretValue {
                sealed,
                slot,
                value,
            } => self.fulfill_secret(&sealed, &slot, &value),
            WireCmd::Detach => self.detached = true,
            WireCmd::Wake => {
                self.detached = false;
                self.poll = Some(0.0);
            }
            WireCmd::Shutdown => {
                self.queue.clear();
                self.op_reply = None;
                self.poll = None;
                self.listen = None;
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
}

enum LoopEvent {
    Term(TermEvent),
    Wire(u32, WireEvent),
    Clip(ClipEntry, Result<Clip, String>),
    Pr(Option<pr::Pr>),
    Conversations(u32, Result<Vec<ConversationRow>, String>),
    StdinClosed,
}

#[derive(Default)]
struct Gate {
    prompt: String,
    questions: VecDeque<GateQuestion>,
    answers: Vec<String>,
    many: bool,
    secrets: VecDeque<(String, String, String)>,
    asked: bool,
    exit: Option<i32>,
}

struct GateQuestion {
    prompt: String,
    options: Vec<String>,
    multiple: bool,
    authorization: Option<(String, Vec<AuthorizationOption>)>,
}

fn authorization_labels(options: &[AuthorizationOption]) -> Vec<String> {
    options
        .iter()
        .map(|option| {
            if option.description.is_empty() {
                option.label.clone()
            } else {
                format!("{}: {}", option.label, option.description)
            }
        })
        .collect()
}

fn attach_dropped(app: &mut App, source: &std::path::Path, home: &std::path::Path, channel: &str) {
    match clipboard::stash_copy(source, home, channel) {
        Ok(path) => app.paste_image(&path),
        Err(error) => app.note(&error),
    }
}

struct Live {
    generation: u32,
    wired: bool,
    cmd: Sender<WireCmd>,
    evt: Sender<WireEvent>,
    gate: Gate,
    stop: Option<Stop>,
    stop_requested: bool,
    sends: Option<SendLane>,
    listening: bool,
    label: String,
}

impl Live {
    fn unwired(evt_tx: &Sender<LoopEvent>) -> Live {
        let (cmd, _) = channel::<WireCmd>();
        Live {
            generation: 0,
            wired: false,
            cmd,
            evt: wire_sender(evt_tx.clone(), 0),
            gate: Gate::default(),
            stop: None,
            stop_requested: false,
            sends: None,
            listening: false,
            label: String::new(),
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn start(
        generation: u32,
        seed: &Session,
        target: Target,
        runtime: &OpRuntime,
        home: &config::Home,
        evt_tx: &Sender<LoopEvent>,
        first: String,
        label: String,
    ) -> Live {
        let evt = wire_sender(evt_tx.clone(), generation);
        let (cmd, cmd_rx) = channel::<WireCmd>();
        let wire = Wire {
            session: seed.retarget(target),
            runtime: runtime.clone(),
            home: home.clone(),
            evt: evt.clone(),
            cmd: cmd_rx,
            queue: VecDeque::new(),
            op_reply: None,
            poll: None,
            listen: None,
            heed_listen: true,
            detached: false,
            opened: false,
            install: false,
            installed_this_run: false,
            pause: RECONNECT_PAUSE,
        };
        thread::spawn(move || wire.run(first));
        Live {
            generation,
            wired: true,
            cmd,
            evt,
            gate: Gate::default(),
            stop: None,
            stop_requested: false,
            sends: None,
            listening: false,
            label,
        }
    }
}

fn show_page(app: &mut App, lister: Option<&Lister>, back: bool, evt: &Sender<LoopEvent>) {
    match lister {
        Some(lister) => {
            let fetch = app.open_conversations(back);
            fetch_conversations(lister.clone(), fetch, evt.clone());
        }
        None => app.note("Sign in to list conversations."),
    }
}

fn fetch_conversations(lister: Lister, fetch: Fetch, evt: Sender<LoopEvent>) {
    thread::spawn(move || {
        let result = lister.list(&fetch.search);
        let _ = evt.send(LoopEvent::Conversations(fetch.generation, result));
    });
}

struct Opening {
    target: Target,
    label: String,
    read_only: Option<String>,
}

impl Opening {
    fn listed(row: &ConversationRow) -> Opening {
        Opening {
            target: match &row.channel {
                Some(channel) => Target::Channel(channel.clone()),
                None => Target::Conversation(row.id.clone()),
            },
            label: row.channel.clone().unwrap_or_else(|| row.title.clone()),
            read_only: (!row.postable).then(|| surface_word(&row.surface)),
        }
    }

    fn fresh() -> Opening {
        let channel = random_channel();
        Opening {
            target: Target::Channel(channel.clone()),
            label: channel,
            read_only: None,
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn open_conversation(
    app: &mut App,
    opening: Opening,
    live: Live,
    seed: &Session,
    runtime: &OpRuntime,
    home: &config::Home,
    evt_tx: &Sender<LoopEvent>,
    first: String,
    workspace_url: Option<&str>,
) -> Live {
    let _ = live.cmd.send(WireCmd::Shutdown);
    app.reset_conversation(&opening.target, opening.label.clone(), opening.read_only);
    if !first.is_empty() {
        app.begin_turn();
    }
    update_resume(workspace_url, opening.target.label(), true);
    Live::start(
        live.generation + 1,
        seed,
        opening.target,
        runtime,
        home,
        evt_tx,
        first,
        opening.label,
    )
}

fn run_tty(
    seed: Session,
    runtime: OpRuntime,
    home: config::Home,
    first: String,
    pick: bool,
) -> i32 {
    let host = seed
        .workspace_url
        .clone()
        .unwrap_or_else(|| seed.gateway_url.clone());
    let host = host
        .trim_start_matches("https://")
        .trim_start_matches("http://")
        .trim_end_matches('/')
        .to_string();
    let label = seed.target.label().to_string();
    let cwd = runtime.cwd.clone();
    let stash_home = home.root.clone();
    let pr_cwd = runtime.cwd.clone();
    let mut latest_workspace = seed.workspace_url.clone();
    let mut lister = seed.lister();
    let mut seed = seed;

    let (raw, probe) = ui::RawGuard::enter();
    let theme = ui::theme::Theme::detect(false, probe.scheme);
    let mut app = App::new(
        std::io::stdout(),
        &home.root,
        &seed.session_id,
        theme,
        host,
        label.clone(),
        cwd,
    );
    let (evt_tx, evt_rx) = channel::<LoopEvent>();

    for key in probe.typeahead {
        let _ = evt_tx.send(LoopEvent::Term(TermEvent::Key(key)));
    }
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

    let pr_tx = evt_tx.clone();
    thread::spawn(move || pr::watch(&pr_cwd, |found| pr_tx.send(LoopEvent::Pr(found)).is_ok()));

    let mut pending_first = first;
    let mut live = if pick {
        Live::unwired(&evt_tx)
    } else {
        let first = std::mem::take(&mut pending_first);
        app.set_live_target(seed.target.clone());
        app.masthead();
        if !first.is_empty() {
            app.begin_turn();
        }
        Live::start(
            1,
            &seed,
            seed.target.clone(),
            &runtime,
            &home,
            &evt_tx,
            first,
            label,
        )
    };
    if pick {
        let fetch = app.open_conversations(false);
        if let Some(lister) = lister.clone() {
            fetch_conversations(lister, fetch, evt_tx.clone());
        }
    }
    app.paint();
    let mut clip_pending = false;
    let code = loop {
        let event = match evt_rx.recv_timeout(TICK) {
            Ok(event) => event,
            Err(RecvTimeoutError::Timeout) => {
                app.tick();
                if let (Some(lister), Some(fetch)) =
                    (lister.clone(), app.conversations_due_fetch(Instant::now()))
                {
                    fetch_conversations(lister, fetch, evt_tx.clone());
                }
                app.paint();
                continue;
            }
            Err(RecvTimeoutError::Disconnected) => break 0,
        };
        match event {
            LoopEvent::Term(TermEvent::Key(key)) if key.kind != KeyEventKind::Release => {
                match app.on_key(key) {
                    Reply::None => {}
                    Reply::Attach(source) => {
                        attach_dropped(&mut app, &source, &stash_home, &live.label)
                    }
                    Reply::Clipboard(entry) => {
                        if !clip_pending {
                            clip_pending = true;
                            let notify = evt_tx.clone();
                            thread::spawn(move || {
                                let _ = notify.send(LoopEvent::Clip(entry, clipboard::read()));
                            });
                        }
                    }
                    Reply::Send(text)
                        if live
                            .gate
                            .questions
                            .front()
                            .is_some_and(|question| question.options.is_empty()) =>
                    {
                        if let Some(reply) = collect_answer(&mut app, &mut live.gate, text, &[]) {
                            app.begin_turn();
                            let _ = live.cmd.send(reply.command());
                        }
                    }
                    Reply::Send(text) => {
                        if app.is_working() {
                            app.push_queued(&text);
                            match live.sends.clone() {
                                Some(lane) => send_instant(
                                    lane,
                                    MemberPost::Message(text),
                                    live.evt.clone(),
                                    live.cmd.clone(),
                                ),
                                None => {
                                    let _ = live.cmd.send(WireCmd::Say(text));
                                }
                            }
                        } else if live.listening {
                            app.begin_turn();
                            match live.sends.clone() {
                                Some(lane) => {
                                    app.push_queued(&text);
                                    send_instant(
                                        lane,
                                        MemberPost::Message(text),
                                        live.evt.clone(),
                                        live.cmd.clone(),
                                    )
                                }
                                None => {
                                    let _ = live.cmd.send(WireCmd::Say(text));
                                }
                            }
                        } else {
                            app.begin_turn();
                            let _ = live.cmd.send(WireCmd::Say(text));
                        }
                    }
                    Reply::Choice { text, selected } => {
                        if let Some(reply) =
                            collect_answer(&mut app, &mut live.gate, text, &selected)
                        {
                            app.begin_turn();
                            let _ = live.cmd.send(reply.command());
                        }
                    }
                    Reply::ChoiceCancelled => break 0,
                    Reply::Secret(value) => {
                        if let Some((sealed, slot, _)) = live.gate.secrets.pop_front() {
                            let _ = live.cmd.send(WireCmd::SecretValue {
                                sealed,
                                slot,
                                value,
                            });
                        }
                        if let Some((_, _, prompt)) = live.gate.secrets.front() {
                            app.secret_begin(&prompt.clone());
                        } else {
                            settle(&mut app, &mut live.gate);
                        }
                    }
                    Reply::Stop => {
                        if let Some(prepared) = live.stop.take() {
                            stop_instant(prepared, live.evt.clone());
                        } else {
                            live.stop_requested = true;
                        }
                    }
                    Reply::Recall { text, arrival_id } => match live.sends.clone() {
                        Some(lane) => retract_instant(lane, text, arrival_id, live.evt.clone()),
                        None => app.retracted(&text, &arrival_id, false),
                    },
                    Reply::Detach => {
                        live.listening = false;
                        live.stop_requested = false;
                        let _ = live.cmd.send(WireCmd::Detach);
                        app.end_turn(false);
                        app.note("Detached; the turn continues, and a new message rejoins it.");
                        app.ask_prompt("");
                    }
                    Reply::OpenConversations => {
                        show_page(&mut app, lister.as_ref(), live.wired, &evt_tx)
                    }
                    Reply::CloseConversations => {}
                    Reply::Open(row) => {
                        app.mark_seen(&row);
                        live = open_conversation(
                            &mut app,
                            Opening::listed(&row),
                            live,
                            &seed,
                            &runtime,
                            &home,
                            &evt_tx,
                            std::mem::take(&mut pending_first),
                            latest_workspace.as_deref(),
                        );
                        clip_pending = false;
                    }
                    Reply::NewChat(text) => {
                        live = open_conversation(
                            &mut app,
                            Opening::fresh(),
                            live,
                            &seed,
                            &runtime,
                            &home,
                            &evt_tx,
                            text,
                            latest_workspace.as_deref(),
                        );
                        clip_pending = false;
                    }
                    Reply::Exit => break 0,
                }
                app.paint();
            }
            LoopEvent::Term(TermEvent::Paste(text)) => {
                if let Reply::Attach(source) = app.on_paste(text) {
                    attach_dropped(&mut app, &source, &stash_home, &live.label);
                }
                app.paint();
            }
            LoopEvent::Clip(entry, result) => {
                clip_pending = false;
                match result {
                    Err(error) => app.note(&error),
                    Ok(_) if !app.entry_still(entry) => {
                        app.note(
                            "The entry changed while the clipboard was read; press Ctrl+V again.",
                        );
                    }
                    Ok(Clip::Image(bytes)) => {
                        if entry == ClipEntry::Compose {
                            match clipboard::stash_image(&bytes, &stash_home, &live.label) {
                                Ok(path) => app.paste_image(&path),
                                Err(error) => app.note(&error),
                            }
                        } else {
                            app.note("An image pastes into the message entry only.");
                        }
                    }
                    Ok(Clip::Text(text)) => {
                        if let Reply::Attach(source) = app.on_paste(text) {
                            attach_dropped(&mut app, &source, &stash_home, &live.label);
                        }
                    }
                    Ok(Clip::Empty) => app.note("The clipboard holds nothing to paste."),
                }
                app.paint();
            }
            LoopEvent::Term(TermEvent::Resize(..)) => {
                app.resize();
                app.paint();
            }
            LoopEvent::Term(TermEvent::Mouse(mouse)) => {
                let opening = match app.on_mouse(mouse) {
                    Reply::Open(row) => {
                        app.mark_seen(&row);
                        Some(Opening::listed(&row))
                    }
                    Reply::OpenConversations => {
                        show_page(&mut app, lister.as_ref(), live.wired, &evt_tx);
                        None
                    }
                    _ => None,
                };
                if let Some(opening) = opening {
                    live = open_conversation(
                        &mut app,
                        opening,
                        live,
                        &seed,
                        &runtime,
                        &home,
                        &evt_tx,
                        std::mem::take(&mut pending_first),
                        latest_workspace.as_deref(),
                    );
                    clip_pending = false;
                }
                app.paint();
            }
            LoopEvent::Term(TermEvent::FocusGained) => app.set_focus(true),
            LoopEvent::Term(TermEvent::FocusLost) => app.set_focus(false),
            LoopEvent::Term(_) => {}
            LoopEvent::Pr(found) => {
                app.set_pr(found);
                app.paint();
            }
            LoopEvent::StdinClosed => {}
            LoopEvent::Conversations(generation, result) => {
                app.conversations_loaded(generation, result);
                app.paint();
            }
            LoopEvent::Wire(generation, _) if generation != live.generation => {}
            LoopEvent::Wire(_, wire_event) => match wire_event {
                WireEvent::Dir(directive) => {
                    if live.listening && !app.is_working() && wakes_display(&directive) {
                        app.begin_turn();
                    }
                    apply_directive(&mut app, &mut live.gate, directive);
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
                WireEvent::WorkspaceChanged {
                    url,
                    channel,
                    seed: signed_in,
                } => {
                    latest_workspace = Some(url.clone());
                    live.label = channel.clone();
                    lister = signed_in.lister();
                    seed = *signed_in;
                    let host = url
                        .trim_start_matches("https://")
                        .trim_start_matches("http://")
                        .trim_end_matches('/')
                        .to_string();
                    app.set_endpoint(host, channel);
                }
                WireEvent::Stoppable(prepared) => {
                    if live.stop_requested {
                        live.stop_requested = false;
                        stop_instant(prepared, live.evt.clone());
                    } else {
                        live.stop = Some(prepared);
                    }
                }
                WireEvent::Sendable(lane) => live.sends = Some(lane),
                WireEvent::Sent { text, ack } => {
                    if ack.opened {
                        if !app.is_working() {
                            app.begin_turn();
                        }
                        app.queued_sent(&text);
                        let _ = live.cmd.send(WireCmd::Wake);
                    } else if ack.arrival_id.is_empty() {
                        app.settle_queued(&text);
                    } else {
                        app.sent_ack(&text, &ack.arrival_id);
                    }
                    app.paint();
                }
                WireEvent::Retracted {
                    text,
                    arrival_id,
                    retracted,
                } => {
                    app.retracted(&text, &arrival_id, retracted);
                    app.paint();
                }
                WireEvent::StreamEnd {
                    continues,
                    listening: armed,
                } => {
                    live.listening = armed;
                    if let Some(code) = live.gate.exit.take() {
                        break code;
                    }
                    if continues {
                        app.paint();
                        continue;
                    }
                    live.stop_requested = false;
                    if !live.gate.secrets.is_empty() {
                        if !app.collecting_secret() {
                            app.end_turn(false);
                            let prompt = live.gate.secrets.front().map(|(_, _, p)| p.clone());
                            if let Some(prompt) = prompt {
                                app.secret_begin(&prompt);
                            }
                        }
                    } else if armed && !live.gate.asked && live.gate.questions.is_empty() {
                        if app.is_working() {
                            app.end_turn(false);
                        }
                    } else if !settle(&mut app, &mut live.gate) {
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
    let _ = live.cmd.send(WireCmd::Shutdown);
    app.close();
    drop(raw);
    if live.wired {
        print_resume(latest_workspace.as_deref(), &live.label, true);
    }
    code
}

fn settle(app: &mut App, gate: &mut Gate) -> bool {
    if let Some(question) = gate.questions.front() {
        app.end_turn(true);
        if question.options.is_empty() {
            app.ask_prompt(&question.prompt);
        } else {
            app.choose(&question.prompt, &question.options, question.multiple);
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

fn wakes_display(directive: &Directive) -> bool {
    matches!(
        directive,
        Directive::Say(_)
            | Directive::Txt(_)
            | Directive::Note(_)
            | Directive::Activity { .. }
            | Directive::Status(_)
            | Directive::File { .. }
    )
}

fn collect_answer<W: std::io::Write>(
    app: &mut App<W>,
    gate: &mut Gate,
    answer: String,
    selected: &[usize],
) -> Option<MemberPost> {
    let question = gate.questions.pop_front()?;
    if let Some((authorization_id, options)) = question.authorization {
        let option = selected.first().and_then(|index| options.get(*index))?;
        return Some(MemberPost::Authorization(AuthorizationAnswer {
            authorization_id,
            choice: option.choice,
            text: option.label.clone(),
        }));
    }
    gate.answers.push(if gate.many {
        format!("{}: {answer}", question.prompt)
    } else {
        answer
    });
    if let Some(next) = gate.questions.front() {
        if next.options.is_empty() {
            app.ask_prompt(&next.prompt);
        } else {
            app.choose(&next.prompt, &next.options, next.multiple);
        }
        return None;
    }
    Some(MemberPost::Message(
        std::mem::take(&mut gate.answers).join("\n"),
    ))
}

fn apply_directive(app: &mut App, gate: &mut Gate, directive: Directive) {
    match directive {
        Directive::Say(text) => app.say(&text),
        Directive::You(text) => app.member_replay(&text),
        Directive::Absorbed(arrival_ids) => app.absorbed(&arrival_ids),
        Directive::Note(text) => app.note(&text),
        Directive::Activity { text, run } => app.activity(&text, run.as_deref()),
        Directive::Txt(chunk) => app.txt(&chunk),
        Directive::Status(text) => app.status_text(&text),
        Directive::File { name, size, url } => app.file(&name, &size, &url),
        Directive::Ask(prompt) => {
            gate.asked = true;
            gate.prompt = prompt;
        }
        Directive::Choose {
            prompt,
            options,
            multiple,
        } => {
            gate.questions.push_back(GateQuestion {
                prompt,
                options,
                multiple,
                authorization: None,
            });
            gate.many = gate.questions.len() > 1;
        }
        Directive::Authorize {
            authorization_id,
            prompt,
            options,
        } => {
            gate.questions.push_back(GateQuestion {
                prompt,
                options: authorization_labels(&options),
                multiple: false,
                authorization: Some((authorization_id, options)),
            });
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

/// Each event is tagged with the generation of the conversation the wire serves, so a wire left behind
/// for another conversation is heard by nobody.
fn wire_sender(tx: Sender<LoopEvent>, generation: u32) -> Sender<WireEvent> {
    let (wire_tx, wire_rx) = channel::<WireEvent>();
    thread::spawn(move || {
        for event in wire_rx {
            if tx.send(LoopEvent::Wire(generation, event)).is_err() {
                return;
            }
        }
    });
    wire_tx
}

fn run_plain(session: Session, runtime: OpRuntime, home: config::Home, first: String) -> i32 {
    let tty = std::io::stdout().is_terminal();
    let (evt_tx, evt_rx) = channel::<LoopEvent>();
    let (cmd_tx, cmd_rx) = channel::<WireCmd>();
    let workspace_url = session.workspace_url.clone();
    let mut latest_channel = session.target.label().to_string();
    let wire = Wire {
        session,
        runtime,
        home,
        evt: wire_sender(evt_tx, 0),
        cmd: cmd_rx,
        queue: VecDeque::new(),
        op_reply: None,
        poll: None,
        listen: None,
        heed_listen: false,
        detached: false,
        opened: false,
        install: false,
        installed_this_run: false,
        pause: RECONNECT_PAUSE,
    };
    thread::spawn(move || wire.run(first));

    let mut out = Plain::new();
    let mut gate = Gate::default();
    let mut latest_workspace = workspace_url;
    let code = loop {
        let event = match evt_rx.recv() {
            Ok(LoopEvent::Wire(_, event)) => event,
            Ok(LoopEvent::Term(_))
            | Ok(LoopEvent::Clip(..))
            | Ok(LoopEvent::Pr(_))
            | Ok(LoopEvent::Conversations(..))
            | Ok(LoopEvent::StdinClosed) => continue,
            Err(_) => break 0,
        };
        match event {
            WireEvent::Dir(directive) => match directive {
                Directive::Say(text) => out.say(&text),
                Directive::You(text) => out.member(&text),
                Directive::Absorbed(_) | Directive::Sent { .. } => {}
                Directive::Note(text) => out.note(&text),
                Directive::Activity { text, run } => out.activity(&text, run.as_deref()),
                Directive::Txt(chunk) => out.txt(&chunk),
                Directive::Status(text) => out.status(&text),
                Directive::File { name, size, url } => out.file(&name, &size, &url),
                Directive::Ask(prompt) => {
                    gate.asked = true;
                    gate.prompt = prompt;
                }
                Directive::Choose {
                    prompt,
                    options,
                    multiple,
                } => {
                    gate.questions.push_back(GateQuestion {
                        prompt,
                        options,
                        multiple,
                        authorization: None,
                    });
                    gate.many = gate.questions.len() > 1;
                }
                Directive::Authorize {
                    authorization_id,
                    prompt,
                    options,
                } => {
                    gate.questions.push_back(GateQuestion {
                        prompt,
                        options: authorization_labels(&options),
                        multiple: false,
                        authorization: Some((authorization_id, options)),
                    });
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
            WireEvent::WorkspaceChanged { url, channel, .. } => {
                latest_workspace = Some(url);
                latest_channel = channel;
            }
            WireEvent::Stoppable(_) => {}
            WireEvent::Sendable(_) | WireEvent::Sent { .. } | WireEvent::Retracted { .. } => {}
            WireEvent::StreamEnd { continues, .. } => {
                if let Some(code) = gate.exit.take() {
                    out.end_stream();
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
                    let mut authorization = None;
                    let mut cancelled = false;
                    while let Some(question) = gate.questions.pop_front() {
                        let (answer, selected) = if question.options.is_empty() {
                            (out.ask(&question.prompt), None)
                        } else if question.authorization.is_some() {
                            match out.menu_selected(&question.prompt, &question.options) {
                                Some((answer, selected)) => (Some(answer), Some(selected)),
                                None => (None, None),
                            }
                        } else if question.multiple {
                            (out.menu_many(&question.prompt, &question.options), None)
                        } else {
                            (out.menu(&question.prompt, &question.options), None)
                        };
                        let Some(answer) = answer else {
                            cancelled = true;
                            break;
                        };
                        if let Some((authorization_id, options)) = question.authorization {
                            let Some(option) = selected.and_then(|index| options.get(index)) else {
                                cancelled = true;
                                break;
                            };
                            authorization = Some(AuthorizationAnswer {
                                authorization_id,
                                choice: option.choice,
                                text: option.label.clone(),
                            });
                            continue;
                        }
                        collected.push(if gate.many {
                            format!("{}: {answer}", question.prompt)
                        } else {
                            answer
                        });
                    }
                    if cancelled {
                        break 0;
                    }
                    let command = match authorization {
                        Some(answer) => WireCmd::Authorize(answer),
                        None => WireCmd::Say(collected.join("\n")),
                    };
                    let _ = cmd_tx.send(command);
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

fn run_json(session: Session, runtime: OpRuntime, home: config::Home, first: String) -> i32 {
    let channel_name = session.target.label().to_string();
    let workspace_url = session.workspace_url.clone();
    let (evt_tx, evt_rx) = channel::<LoopEvent>();
    let (cmd_tx, cmd_rx) = channel::<WireCmd>();

    let stdin_tx = evt_tx.clone();
    let wire_evt = wire_sender(evt_tx, 0);
    let send_evt = wire_evt.clone();
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
        evt: wire_evt,
        cmd: cmd_rx,
        queue: VecDeque::new(),
        op_reply: None,
        poll: None,
        listen: None,
        heed_listen: false,
        detached: false,
        opened: false,
        install: false,
        installed_this_run: false,
        pause: RECONNECT_PAUSE,
    };
    let first_nonempty = !first.is_empty();
    thread::spawn(move || wire.run(first));

    let mut driver = jsonio::Driver::new();
    let mut exit_code: Option<i32> = None;
    let mut in_turn = false;
    let mut stdin_open = true;
    emit_json(&driver.session_start(&channel_name, workspace_url.as_deref()));
    let mut sends: Option<SendLane> = None;
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
                                let _ = cmd_tx.send(WireCmd::Say(text));
                            } else {
                                match sends.clone() {
                                    Some(lane) => send_instant(
                                        lane,
                                        MemberPost::Message(text),
                                        send_evt.clone(),
                                        cmd_tx.clone(),
                                    ),
                                    None => {
                                        let _ = cmd_tx.send(WireCmd::Say(text));
                                    }
                                }
                            }
                        }
                        Ok(jsonio::AnswerRouting::Authorization(answer)) => {
                            if !in_turn {
                                emit_json(&driver.on_turn_start());
                                in_turn = true;
                                let _ = cmd_tx.send(WireCmd::Authorize(answer));
                            } else {
                                match sends.clone() {
                                    Some(lane) => send_instant(
                                        lane,
                                        MemberPost::Authorization(answer),
                                        send_evt.clone(),
                                        cmd_tx.clone(),
                                    ),
                                    None => {
                                        let _ = cmd_tx.send(WireCmd::Authorize(answer));
                                    }
                                }
                            }
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
            LoopEvent::Term(_)
            | LoopEvent::Clip(..)
            | LoopEvent::Pr(_)
            | LoopEvent::Conversations(..) => {}
            LoopEvent::StdinClosed => {
                stdin_open = false;
                if !in_turn {
                    return exit_code.unwrap_or(0);
                }
            }
            LoopEvent::Wire(_, wire_event) => match wire_event {
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
                WireEvent::WorkspaceChanged { url, channel, .. } => {
                    emit_json(&driver.signed_in(&url, &channel));
                }
                WireEvent::Stoppable(_) => {}
                WireEvent::Sendable(lane) => sends = Some(lane),
                WireEvent::Sent { ack, .. } => {
                    emit_json(&driver.message_sent(&ack));
                }
                WireEvent::Retracted { .. } => {}
                WireEvent::StreamEnd { continues, .. } => {
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

#[cfg(test)]
mod tests {
    use super::*;

    fn question_app() -> (App<Vec<u8>>, std::path::PathBuf) {
        let home = env::temp_dir().join(format!("ufo-question-test-{}", process::id()));
        let _ = std::fs::remove_dir_all(&home);
        std::fs::create_dir_all(&home).unwrap();
        let app = App::new(
            Vec::new(),
            &home,
            "question-test",
            ui::theme::Theme::for_mode(ui::theme::ColorMode::Plain, ui::theme::Scheme::Dark),
            "ufo.test".into(),
            "question-test".into(),
            home.clone(),
        );
        (app, home)
    }

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
    fn free_text_advances_a_multi_question_gate_before_submission() {
        let (mut app, home) = question_app();
        let mut gate = Gate {
            questions: VecDeque::from([
                GateQuestion {
                    prompt: "Explain access".into(),
                    options: Vec::new(),
                    multiple: false,
                    authorization: None,
                },
                GateQuestion {
                    prompt: "Select services".into(),
                    options: vec!["Mail".into(), "Calendar".into()],
                    multiple: true,
                    authorization: None,
                },
            ]),
            many: true,
            ..Gate::default()
        };

        assert_eq!(
            collect_answer(&mut app, &mut gate, "because".into(), &[]),
            None
        );
        assert_eq!(gate.questions.len(), 1);
        assert_eq!(
            collect_answer(&mut app, &mut gate, "Mail, Calendar".into(), &[0, 1]),
            Some(MemberPost::Message(
                "Explain access: because\nSelect services: Mail, Calendar".into()
            ))
        );
        assert!(gate.questions.is_empty());
        assert!(gate.answers.is_empty());
        app.close();
        let _ = std::fs::remove_dir_all(home);
    }

    #[test]
    fn a_tui_selection_keeps_authorization_identity_and_code_out_of_its_label() {
        let (mut app, home) = question_app();
        let authorization_id = "92fc2a7b-d3fe-4fb7-8096-e78f658dbda6";
        let options = vec![
            AuthorizationOption {
                label: "Allow once".into(),
                description: "This action only.".into(),
                choice: ufo::wire::AuthorizationChoice::Allow,
            },
            AuthorizationOption {
                label: "Deny".into(),
                description: "Do not allow this action.".into(),
                choice: ufo::wire::AuthorizationChoice::Deny,
            },
            AuthorizationOption {
                label: "Always allow for GitHub".into(),
                description: "Future repository reads from this account.".into(),
                choice: ufo::wire::AuthorizationChoice::Always,
            },
        ];
        let displayed = authorization_labels(&options);
        let mut gate = Gate {
            questions: VecDeque::from([GateQuestion {
                prompt: "Proceed?".into(),
                options: displayed.clone(),
                multiple: false,
                authorization: Some((authorization_id.into(), options)),
            }]),
            ..Gate::default()
        };
        assert_eq!(
            displayed,
            [
                "Allow once: This action only.",
                "Deny: Do not allow this action.",
                "Always allow for GitHub: Future repository reads from this account.",
            ]
        );
        assert_eq!(
            collect_answer(
                &mut app,
                &mut gate,
                "Always allow for GitHub: Future repository reads from this account.".into(),
                &[2],
            ),
            Some(MemberPost::Authorization(AuthorizationAnswer {
                authorization_id: authorization_id.into(),
                choice: ufo::wire::AuthorizationChoice::Always,
                text: "Always allow for GitHub".into(),
            }))
        );
        app.close();
        let _ = std::fs::remove_dir_all(home);
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

    fn listening_wire(listen: Option<f64>) -> (Wire, Sender<WireCmd>, Receiver<WireEvent>) {
        let (evt_tx, evt_rx) = channel::<WireEvent>();
        let (cmd_tx, cmd_rx) = channel::<WireCmd>();
        let dir = env::temp_dir();
        let wire = Wire {
            session: Session::new(
                "https://gw".into(),
                None,
                Target::Channel("abc".into()),
                None,
                "sid".into(),
                None,
                false,
                false,
            ),
            runtime: OpRuntime {
                workdir: dir.clone(),
                cwd: dir.clone(),
                home: config::Home { root: dir.clone() },
            },
            home: config::Home { root: dir },
            evt: evt_tx,
            cmd: cmd_rx,
            queue: VecDeque::new(),
            op_reply: None,
            poll: None,
            listen,
            heed_listen: true,
            detached: false,
            opened: false,
            install: false,
            installed_this_run: false,
            pause: Duration::ZERO,
        };
        (wire, cmd_tx, evt_rx)
    }

    fn said(evt: &Receiver<WireEvent>) -> Vec<String> {
        let mut said = Vec::new();
        while let Ok(event) = evt.try_recv() {
            match event {
                WireEvent::MemberEcho(text) => said.push(format!("echo:{text}")),
                WireEvent::Dir(Directive::Note(text)) => said.push(format!("note:{text}")),
                WireEvent::Fatal(text) => said.push(format!("fatal:{text}")),
                WireEvent::Reconnecting {
                    attempt,
                    retry_in_s,
                } => said.push(format!("retry:{attempt}:{retry_in_s}")),
                _ => {}
            }
        }
        said
    }

    #[test]
    fn the_system_skill_cache_operation_is_invisible() {
        let (mut wire, _cmd, evt) = listening_wire(None);
        let ended = wire.handle(Directive::Run(OpRequest {
            op_id: "skills".into(),
            kind: ops::OP_SKILLS.into(),
            name: String::new(),
            timeout_s: 30,
            arg: String::new(),
            params: r#"{"system":{},"user":{}}"#.into(),
        }));

        assert!(ended);
        assert!(evt.try_recv().is_err());
        let Some(PostBody::OpReply { op_id, reply }) = wire.op_reply.take() else {
            panic!("the cache operation must answer the server");
        };
        assert_eq!(op_id, "skills");
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&reply.unwrap()).unwrap(),
            serde_json::json!({"roots": {}})
        );
    }

    #[test]
    fn a_finished_op_outranks_everything_queued() {
        let (mut wire, cmd, _evt) = listening_wire(None);
        cmd.send(WireCmd::Say("typed while the op ran".into()))
            .expect("the wire holds its receiver");
        wire.op_reply = Some(PostBody::OpReply {
            op_id: "op1".into(),
            reply: Ok(b"done".to_vec()),
        });
        wire.poll = Some(5.0);
        let body = wire.next_body();
        assert!(matches!(body, Some(PostBody::OpReply { op_id, .. }) if op_id == "op1"));
        assert!(wire.poll.is_none(), "the op reply cancels a pending poll");
        assert_eq!(
            wire.queue.len(),
            1,
            "the message stays queued for the next post"
        );
    }

    #[test]
    fn a_burst_of_messages_posts_as_one_turn() {
        let (mut wire, cmd, evt) = listening_wire(None);
        for text in ["first", "second", "third"] {
            cmd.send(WireCmd::Say(text.into()))
                .expect("the wire holds its receiver");
        }
        wire.detached = true;
        let body = wire.next_body();
        assert!(
            matches!(&body, Some(PostBody::Message(text)) if text == "first\n\nsecond\n\nthird")
        );
        assert!(!wire.detached, "a member's message reattaches the wire");
        assert!(wire.queue.is_empty());
        assert_eq!(
            said(&evt),
            vec!["echo:first", "echo:second", "echo:third"],
            "every message is echoed to the member in order"
        );
    }

    #[test]
    fn an_authorization_answer_is_not_coalesced_with_ordinary_text() {
        let (mut wire, cmd, evt) = listening_wire(None);
        cmd.send(WireCmd::Say("before".into())).unwrap();
        cmd.send(WireCmd::Authorize(AuthorizationAnswer {
            authorization_id: "92fc2a7b-d3fe-4fb7-8096-e78f658dbda6".into(),
            choice: ufo::wire::AuthorizationChoice::Allow,
            text: "Proceed once".into(),
        }))
        .unwrap();
        cmd.send(WireCmd::Say("after".into())).unwrap();
        wire.detached = true;
        assert!(matches!(
            wire.next_body(),
            Some(PostBody::Message(text)) if text == "before"
        ));
        assert!(matches!(
            wire.next_body(),
            Some(PostBody::Authorization(AuthorizationAnswer {
                authorization_id,
                choice: ufo::wire::AuthorizationChoice::Allow,
                text,
            })) if authorization_id == "92fc2a7b-d3fe-4fb7-8096-e78f658dbda6"
                && text == "Proceed once"
        ));
        assert!(matches!(
            wire.next_body(),
            Some(PostBody::Message(text)) if text == "after"
        ));
        assert_eq!(
            said(&evt),
            vec!["echo:before", "echo:Proceed once", "echo:after"]
        );
    }

    #[test]
    fn a_shutdown_drops_the_work_the_wire_was_holding() {
        let (mut wire, cmd, _evt) = listening_wire(Some(30.0));
        wire.queue.push_back(MemberPost::Message("unsent".into()));
        wire.poll = Some(1.0);
        wire.op_reply = Some(PostBody::Empty);
        cmd.send(WireCmd::Shutdown)
            .expect("the wire holds its receiver");
        drop(cmd);
        assert!(wire.next_body().is_none(), "a shut wire posts nothing more");
        assert!(wire.queue.is_empty());
        assert!(wire.poll.is_none());
        assert!(wire.op_reply.is_none());
        assert!(wire.listen.is_none());
    }

    #[test]
    fn a_detached_poll_waits_for_the_member_instead_of_firing() {
        let (mut wire, cmd, _evt) = listening_wire(None);
        wire.detached = true;
        wire.poll = Some(0.0);
        cmd.send(WireCmd::Say("back".into()))
            .expect("the wire holds its receiver");
        let body = wire.next_body();
        assert!(
            matches!(&body, Some(PostBody::Message(text)) if text == "back"),
            "a detached wire lets its poll go and posts what the member said"
        );
    }

    #[test]
    fn an_attached_poll_fires_on_its_own() {
        let (mut wire, cmd, _evt) = listening_wire(None);
        wire.poll = Some(0.0);
        drop(cmd);
        assert!(
            matches!(wire.next_body(), Some(PostBody::Empty)),
            "the interval elapsing asks the conversation whether it spoke"
        );
        assert!(wire.poll.is_none(), "the poll fires once, not forever");
    }

    #[test]
    fn a_wake_reattaches_and_asks_at_once() {
        let (mut wire, cmd, _evt) = listening_wire(Some(30.0));
        wire.detached = true;
        cmd.send(WireCmd::Wake)
            .expect("the wire holds its receiver");
        drop(cmd);
        assert!(matches!(wire.next_body(), Some(PostBody::Empty)));
        assert!(!wire.detached, "waking reattaches the wire");
    }

    #[test]
    fn a_poll_the_member_beat_posts_the_message_instead() {
        let (mut wire, cmd, _evt) = listening_wire(None);
        wire.poll = Some(0.0);
        cmd.send(WireCmd::Say("beat the poll".into()))
            .expect("the wire holds its receiver");
        let body = wire.next_body();
        assert!(matches!(&body, Some(PostBody::Message(text)) if text == "beat the poll"));
    }

    #[test]
    fn a_first_failure_before_any_stream_names_the_gateway() {
        let (mut wire, _cmd, evt) = listening_wire(None);
        let mut reconnect = None;
        assert!(!wire.reconnect(&mut reconnect, "connection refused", None));
        assert_eq!(
            said(&evt),
            vec!["fatal:No response from https://gw (connection refused)"]
        );
    }

    #[test]
    fn an_opened_stream_recovers_across_four_failures_within_the_connect_window() {
        let (mut wire, _cmd, evt) = listening_wire(None);
        wire.opened = true;
        let mut reconnect = None;
        let started = Instant::now();
        for second in 0..4 {
            assert!(wire.reconnect_at(
                &mut reconnect,
                "lost connection",
                None,
                started + Duration::from_secs(second),
            ));
        }
        let expected: Vec<String> = (1..=4)
            .map(|attempt| format!("retry:{attempt}:{}", RECONNECT_PAUSE.as_secs()))
            .collect();
        assert_eq!(said(&evt), expected);
    }

    #[test]
    fn an_opened_stream_stops_recovery_at_the_connect_deadline() {
        let (mut wire, _cmd, evt) = listening_wire(None);
        wire.opened = true;
        let mut reconnect = None;
        let started = Instant::now();
        assert!(wire.reconnect_at(&mut reconnect, "lost connection", None, started));
        assert!(!wire.reconnect_at(
            &mut reconnect,
            "lost connection",
            None,
            started + ufo::wire::CONNECT_TIMEOUT,
        ));
        assert_eq!(said(&evt), vec!["retry:1:1", "fatal:lost connection"],);
    }

    #[test]
    fn a_quiet_85_second_stream_preserves_remaining_recovery_time() {
        let (mut wire, _cmd, evt) = listening_wire(None);
        wire.opened = true;
        let mut reconnect = None;
        let started = Instant::now();
        assert!(wire.reconnect_at(&mut reconnect, "lost connection", None, started));
        assert!(wire.reconnect_at(
            &mut reconnect,
            "lost connection",
            Some(started + RECONNECT_PAUSE),
            started + RECONNECT_PAUSE + Duration::from_secs(85),
        ));
        assert_eq!(said(&evt), vec!["retry:1:1", "retry:2:1"]);
    }

    #[test]
    fn repeated_header_only_streams_exhaust_the_recovery_window() {
        let (mut wire, _cmd, evt) = listening_wire(None);
        wire.opened = true;
        let mut reconnect = None;
        let started = Instant::now();
        assert!(wire.reconnect_at(&mut reconnect, "lost connection", None, started));
        for second in 1..ufo::wire::CONNECT_TIMEOUT.as_secs() {
            let opened_at = started + Duration::from_secs(second);
            assert!(wire.reconnect_at(
                &mut reconnect,
                "lost connection",
                Some(opened_at),
                opened_at,
            ));
        }
        let exhausted = started + ufo::wire::CONNECT_TIMEOUT;
        assert!(!wire.reconnect_at(
            &mut reconnect,
            "lost connection",
            Some(exhausted),
            exhausted,
        ));
        let events = said(&evt);
        assert_eq!(
            events.len(),
            ufo::wire::CONNECT_TIMEOUT.as_secs() as usize + 1
        );
        assert_eq!(events.first().map(String::as_str), Some("retry:1:1"));
        assert_eq!(
            events.last().map(String::as_str),
            Some("fatal:lost connection")
        );
    }

    #[test]
    fn an_empty_secret_is_never_posted() {
        let (mut wire, _cmd, evt) = listening_wire(None);
        wire.fulfill_secret("sealed1", "s1", "");
        assert_eq!(
            said(&evt),
            vec!["note:Skipped s1"],
            "an empty secret names the slot it skipped instead of reaching the wire"
        );
    }

    #[test]
    fn an_armed_listen_times_out_into_a_marked_bounce() {
        let (mut wire, _cmd, _evt) = listening_wire(Some(0.01));
        let body = wire.next_body();
        assert!(matches!(body, Some(PostBody::Listen)));
        assert!(wire.listen.is_none());
    }

    #[test]
    fn a_member_message_wins_the_listen_wait() {
        let (mut wire, cmd, _evt) = listening_wire(Some(30.0));
        let typing = thread::spawn(move || {
            thread::sleep(Duration::from_millis(20));
            let _ = cmd.send(WireCmd::Say("hello".into()));
        });
        let started = std::time::Instant::now();
        let body = wire.next_body();
        typing.join().expect("the sender thread ends");
        assert!(matches!(body, Some(PostBody::Message(text)) if text == "hello"));
        assert!(started.elapsed() < Duration::from_secs(5));
    }

    #[test]
    fn a_detached_wire_ignores_the_listen() {
        let (mut wire, cmd, _evt) = listening_wire(Some(0.01));
        wire.detached = true;
        drop(cmd);
        assert!(wire.next_body().is_none());
    }

    #[test]
    fn a_wake_reattaches_and_polls_at_once() {
        let (mut wire, cmd, _evt) = listening_wire(None);
        wire.detached = true;
        cmd.send(WireCmd::Wake)
            .expect("the wire holds its receiver");
        let body = wire.next_body();
        assert!(matches!(body, Some(PostBody::Empty)));
        assert!(!wire.detached);
    }
}
