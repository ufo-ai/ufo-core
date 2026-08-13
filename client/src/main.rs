mod config;
mod ops;
mod ui;
mod wire;

use std::env;
use std::io::IsTerminal;
use std::process;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::ops::OpRuntime;
use crate::wire::{Directive, OpRequest, PostBody, Session};

const GATEWAY_URL_DEFAULT: &str = "https://flyingobject.ai";
const ONBOARDING_CHANNEL: &str = "onboard";
const RECONNECT_ATTEMPTS: u32 = 3;
const RECONNECT_PAUSE: Duration = Duration::from_secs(1);

fn main() {
    #[cfg(unix)]
    adopt_tty_stdin();
    let args: Vec<String> = env::args().skip(1).collect();
    let home = config::Home::resolve();
    let mut rest: &[String] = &args;
    let mut resumed: Option<String> = None;
    let mut login = false;
    match args.first().map(String::as_str) {
        Some("logout") => {
            home.clear_signin();
            println!("Signed out.");
            return;
        }
        Some("login") => {
            login = true;
            home.clear_signin();
            rest = &args[1..];
        }
        Some("--resume") => {
            let Some(id) = args.get(1) else {
                die("--resume needs a conversation id.")
            };
            resumed = Some(id.clone());
            rest = &args[2..];
        }
        _ => {}
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
    let channel = env_nonempty("UFO_CHANNEL").or(resumed).unwrap_or_else(|| {
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
        channel,
        token,
        session_id,
        cwd_header,
        installed,
        tty,
    );
    #[cfg(unix)]
    interrupt::install();
    update_resume(&session, tty);
    config::sweep_retired(&home);
    let mut client = Client {
        home,
        runtime: OpRuntime {
            workdir,
            cwd: launch_dir,
        },
        ui: ui::Ui::new(),
        session,
        tty,
        opened: false,
        installed_this_run: false,
    };
    let code = client.run(message);
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

fn resume_command(session: &Session) -> Option<String> {
    session.workspace_url.as_ref()?;
    Some(format!("ufo --resume {}", session.channel))
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
        match resume_command(session) {
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

#[cfg(unix)]
mod interrupt {
    use std::ffi::CString;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static RESUME: AtomicUsize = AtomicUsize::new(0);

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

#[derive(Default)]
enum Next {
    #[default]
    End,
    Ask,
    Choose,
    Poll,
    Run,
    Exit,
}

struct DrainFailure {
    rendered: bool,
    error: String,
}

struct Turn {
    next: Next,
    got: bool,
    prompt: String,
    questions: Vec<(String, Vec<String>)>,
    secrets: Vec<(String, String, String)>,
    poll_seconds: f64,
    op: Option<OpRequest>,
    exit_code: i32,
    install: bool,
}

impl Default for Turn {
    fn default() -> Turn {
        Turn {
            next: Next::End,
            got: false,
            prompt: String::new(),
            questions: Vec::new(),
            secrets: Vec::new(),
            poll_seconds: 1.0,
            op: None,
            exit_code: 0,
            install: false,
        }
    }
}

struct Client {
    home: config::Home,
    runtime: OpRuntime,
    ui: ui::Ui,
    session: Session,
    tty: bool,
    opened: bool,
    installed_this_run: bool,
}

impl Client {
    fn run(&mut self, first: String) -> i32 {
        let mut body = if first.is_empty() {
            PostBody::Empty
        } else {
            PostBody::Message(first)
        };
        let mut attempts = 0u32;
        loop {
            let turn = match self.drain(body.clone()) {
                Ok(turn) => turn,
                Err(failure) => {
                    if !self.opened {
                        self.ui.close();
                        die(&format!(
                            "No response from {} ({})",
                            self.session.base(),
                            failure.error
                        ));
                    }
                    attempts += 1;
                    if attempts > RECONNECT_ATTEMPTS {
                        self.ui.close();
                        die(&failure.error);
                    }
                    thread::sleep(RECONNECT_PAUSE);
                    if failure.rendered {
                        body = PostBody::Empty;
                    }
                    continue;
                }
            };
            if !turn.got {
                self.ui.close();
                die(&format!("No response from {}", self.session.base()));
            }
            attempts = 0;
            self.fulfill_secrets(&turn.secrets);
            if turn.install {
                self.ensure_installed();
            }
            body = match turn.next {
                Next::End => return self.finish(0),
                Next::Exit => return self.finish(turn.exit_code),
                Next::Ask => match self.ui.ask(&turn.prompt) {
                    Some(reply) => PostBody::Message(reply),
                    None => return self.finish(0),
                },
                Next::Choose => match self.answers(&turn.questions) {
                    Some(reply) => PostBody::Message(reply),
                    None => return self.finish(0),
                },
                Next::Poll => {
                    thread::sleep(Duration::from_secs_f64(turn.poll_seconds.max(0.0)));
                    PostBody::Empty
                }
                Next::Run => {
                    let op = turn.op.expect("a run directive carries its op");
                    let reply = ops::run_op(&self.runtime, &self.session, &op);
                    PostBody::OpReply {
                        op_id: op.op_id,
                        reply,
                    }
                }
            };
        }
    }

    fn drain(&mut self, body: PostBody) -> Result<Turn, DrainFailure> {
        self.ui.spinner_start();
        let stream = match self.session.post(body) {
            Ok(stream) => stream,
            Err(error) => {
                self.ui.spinner_stop();
                return Err(DrainFailure {
                    rendered: false,
                    error,
                });
            }
        };
        let mut turn = Turn::default();
        let mut waiting = true;
        for item in stream {
            let directive = match item {
                Ok(directive) => directive,
                Err(error) => {
                    if waiting {
                        self.ui.spinner_stop();
                    }
                    self.ui.end_stream();
                    return Err(DrainFailure {
                        rendered: turn.got,
                        error,
                    });
                }
            };
            if waiting {
                self.ui.spinner_stop();
                waiting = false;
            }
            turn.got = true;
            self.opened = true;
            self.render(directive, &mut turn);
        }
        if waiting {
            self.ui.spinner_stop();
        }
        self.ui.end_stream();
        Ok(turn)
    }

    fn render(&mut self, directive: Directive, turn: &mut Turn) {
        match directive {
            Directive::Say(text) => self.ui.say(&text),
            Directive::Note(text) => self.ui.note(&text),
            Directive::Txt(chunk) => self.ui.txt(&chunk),
            Directive::Status(text) => self.ui.status(&text),
            Directive::File { name, size, url } => self.ui.file(&name, &size, &url),
            Directive::Ufo { width, frame } => self.ui.set_craft(width, &frame),
            Directive::Ask(prompt) => {
                turn.next = Next::Ask;
                turn.prompt = prompt;
            }
            Directive::Choose { prompt, options } => {
                turn.next = Next::Choose;
                turn.questions.push((prompt, options));
            }
            Directive::Secret {
                sealed,
                slot,
                prompt,
            } => turn.secrets.push((sealed, slot, prompt)),
            Directive::Since(cursor) => self.session.since = Some(cursor),
            Directive::Poll(seconds) => {
                turn.next = Next::Poll;
                turn.poll_seconds = seconds;
            }
            Directive::Run(op) => {
                turn.next = Next::Run;
                turn.op = Some(op);
            }
            Directive::Token(token) => {
                self.home.store_credentials(&token);
                self.session.token = Some(token);
            }
            Directive::Workspace(url) => {
                self.home.store_workspace(&url);
                self.home.store_gateway(&self.session.gateway_url);
                self.session.workspace_url = Some(url);
                if self.session.channel == ONBOARDING_CHANNEL {
                    self.session.channel = random_channel();
                }
                update_resume(&self.session, self.tty);
            }
            Directive::Install => turn.install = true,
            Directive::Logout => {
                self.home.clear_signin();
                self.session.token = None;
                self.session.workspace_url = None;
            }
            Directive::Exit(code) => {
                turn.next = Next::Exit;
                turn.exit_code = code;
            }
            Directive::Unknown => {}
        }
    }

    fn ensure_installed(&mut self) {
        if self.installed_this_run {
            return;
        }
        self.installed_this_run = true;
        let session = &self.session;
        let outcome = config::install_self(&self.home, |target, dest| {
            session.fetch_client_binary(target, dest)
        });
        match outcome {
            Ok(installed) => {
                for line in installed.lines() {
                    self.ui.note(line);
                }
                self.session.installed = true;
            }
            Err(error) => self.ui.note(&format!("Install failed: {error}")),
        }
    }

    fn fulfill_secrets(&mut self, secrets: &[(String, String, String)]) {
        for (sealed, slot, prompt) in secrets {
            let value = self.ui.secret(prompt).unwrap_or_default();
            if value.is_empty() {
                self.ui.note(&format!("Skipped {slot}"));
                continue;
            }
            match self.session.post_secret(sealed, slot, &value) {
                Ok(lines) if !lines.is_empty() => {
                    for line in lines {
                        self.ui.note(&line);
                    }
                }
                Ok(_) => self
                    .ui
                    .note("No confirmation — ask the assistant to check."),
                Err(error) => self.ui.note(&format!(
                    "No confirmation ({error}) — ask the assistant to check."
                )),
            }
        }
    }

    fn answers(&mut self, questions: &[(String, Vec<String>)]) -> Option<String> {
        let mut collected = Vec::new();
        for (prompt, options) in questions {
            let answer = if options.is_empty() {
                self.ui.ask(prompt)?
            } else {
                self.ui.menu(prompt, options)?
            };
            collected.push(if questions.len() > 1 {
                format!("{prompt}: {answer}")
            } else {
                answer
            });
        }
        Some(collected.join("\n"))
    }

    fn finish(&mut self, code: i32) -> i32 {
        self.ui.close();
        self.print_resume();
        let _ = std::fs::remove_dir_all(&self.runtime.workdir);
        code
    }

    fn print_resume(&self) {
        if !self.tty {
            return;
        }
        let Some(command) = resume_command(&self.session) else {
            return;
        };
        println!();
        if wants_style(self.tty) {
            println!("\x1b[2mResume this conversation: \x1b[0m\x1b[1m{command}\x1b[0m");
        } else {
            println!("Resume this conversation: {command}");
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
}
