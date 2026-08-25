//! End-to-end sessions against a scripted gateway: real HTTP, the real binary, every mode.

use std::io::{Read, Write};
use std::net::TcpListener;
use std::process::{Child, Command, Output, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
#[cfg(unix)]
use std::sync::Mutex;
use std::thread::{self, JoinHandle};
use std::time::Duration;

/// How long the gateway waits for a request on a connection before calling it abandoned.
const REQUEST_WAIT: Duration = Duration::from_secs(5);

/// How long a scripted session may run before the client is killed and its output reported.
const CLIENT_WAIT: Duration = Duration::from_secs(30);

/// How long a test waits for the post it is timing itself against.
const ARRIVAL_WAIT: Duration = Duration::from_secs(15);

/// How long a test waits for the gateway to serve its script out.
const SCRIPT_WAIT: Duration = Duration::from_secs(10);

struct Exchange {
    reply_lines: &'static [&'static str],
    delay_ms: u64,
    status: u16,
}

struct Served {
    url: String,
    gateway: Gateway,
    arrived: std::sync::mpsc::Receiver<()>,
    skills_arrived: std::sync::mpsc::Receiver<()>,
}

/// The scripted gateway, still serving.
struct Gateway {
    stop: Arc<AtomicBool>,
    served_out: std::sync::mpsc::Receiver<()>,
    handle: JoinHandle<Vec<Request>>,
}

impl Gateway {
    /// Every request of a script served out. A client that leaves the script unfinished costs
    /// [`SCRIPT_WAIT`] once and fails the assertion that asked, rather than hanging the suite.
    fn requests(self) -> Vec<Request> {
        let _ = self.served_out.recv_timeout(SCRIPT_WAIT);
        self.done()
    }

    /// Everything that arrived, now — for a session that ends with its script part-served, whose
    /// tail nothing will ever ask for.
    fn done(self) -> Vec<Request> {
        self.stop.store(true, Ordering::Relaxed);
        self.handle.join().expect("the gateway thread")
    }
}

#[derive(Debug)]
struct Request {
    system_skills: bool,
    body: String,
    op_header: Option<String>,
    slot_header: Option<String>,
    stop_header: Option<String>,
    send_header: Option<String>,
    send_id: Option<String>,
    unsend_header: Option<String>,
    timezone_header: Option<String>,
    since_header: Option<String>,
    listen_header: Option<String>,
}

fn serve(script: Vec<Exchange>) -> Served {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    listener.set_nonblocking(true).expect("nonblocking");
    let url = format!("http://{}", listener.local_addr().unwrap());
    let (arrival, arrived) = std::sync::mpsc::channel();
    let (skill_arrival, skills_arrived) = std::sync::mpsc::channel();
    let (last, served_out) = std::sync::mpsc::channel();
    let stop = Arc::new(AtomicBool::new(false));
    let stopped = stop.clone();
    let handle = thread::spawn(move || {
        let mut seen = Vec::new();
        'script: for exchange in script {
            // A connection the client abandoned without speaking — a lane it opened as it left —
            // is no exchange: this one still belongs to whichever request arrives next.
            let (mut stream, request) = loop {
                if stopped.load(Ordering::Relaxed) {
                    break 'script;
                }
                let mut stream = match listener.accept() {
                    Ok((stream, _)) => stream,
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(5));
                        continue;
                    }
                    Err(error) => panic!("accept: {error}"),
                };
                stream.set_nonblocking(false).expect("blocking stream");
                stream
                    .set_read_timeout(Some(REQUEST_WAIT))
                    .expect("request deadline");
                if let Some(request) = read_request(&mut stream) {
                    if request.system_skills {
                        let _ = skill_arrival.send(());
                        stream
                            .write_all(
                                b"HTTP/1.1 304 Not Modified\r\ncontent-length: 0\r\nconnection: close\r\n\r\n",
                            )
                            .expect("respond to system skills");
                        continue;
                    }
                    break (stream, request);
                }
            };
            seen.push(request);
            let _ = arrival.send(());
            if exchange.delay_ms > 0 {
                thread::sleep(std::time::Duration::from_millis(exchange.delay_ms));
            }
            let body: String = exchange
                .reply_lines
                .iter()
                .map(|line| format!("{line}\n"))
                .collect();
            // 599 severs the stream mid-body: the declared length outruns what is sent, and the
            // reset makes the client's next read an error rather than a clean end.
            if exchange.status == 599 {
                let head = format!(
                    "HTTP/1.1 200 OK\r\ncontent-type: text/plain\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                    body.len() + 64
                );
                stream.write_all(head.as_bytes()).expect("respond");
                stream.flush().expect("flush");
                thread::sleep(std::time::Duration::from_millis(100));
                rst_close(stream);
                continue 'script;
            }
            let status_line = match exchange.status {
                200 => "200 OK",
                500 => "500 Internal Server Error",
                other => panic!("unscripted status {other}"),
            };
            let response = format!(
                "HTTP/1.1 {status_line}\r\ncontent-type: text/plain\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len()
            );
            stream.write_all(response.as_bytes()).expect("respond");
        }
        let _ = last.send(());
        seen
    });
    Served {
        url,
        gateway: Gateway {
            stop,
            served_out,
            handle,
        },
        arrived,
        skills_arrived,
    }
}

#[cfg(unix)]
fn rst_close(stream: std::net::TcpStream) {
    use std::os::fd::AsRawFd;
    let linger = libc::linger {
        l_onoff: 1,
        l_linger: 0,
    };
    unsafe {
        libc::setsockopt(
            stream.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_LINGER,
            &linger as *const _ as *const libc::c_void,
            std::mem::size_of::<libc::linger>() as libc::socklen_t,
        );
    }
    drop(stream);
}

#[cfg(not(unix))]
fn rst_close(stream: std::net::TcpStream) {
    drop(stream);
}

/// The request one accepted connection carries, or None when it carries none: a client that went
/// away before writing, or one that said nothing before the deadline.
fn read_request(stream: &mut std::net::TcpStream) -> Option<Request> {
    let mut raw = Vec::new();
    let mut buffer = [0u8; 4096];
    let (
        headers_end,
        system_skills,
        content_length,
        op_header,
        slot_header,
        stop_header,
        send_header,
        send_id,
        unsend_header,
        timezone_header,
        since_header,
        listen_header,
    ) = loop {
        let read = match stream.read(&mut buffer) {
            Ok(0) | Err(_) => return None,
            Ok(read) => read,
        };
        raw.extend_from_slice(&buffer[..read]);
        let Some(end) = raw.windows(4).position(|window| window == b"\r\n\r\n") else {
            continue;
        };
        let head = String::from_utf8_lossy(&raw[..end]).to_string();
        let system_skills = head.lines().next().is_some_and(|line| {
            let mut fields = line.split_ascii_whitespace();
            fields.next() == Some("GET")
                && fields.next().is_some_and(|path| path.ends_with("/skills"))
        });
        let mut length = 0usize;
        let mut op = None;
        let mut slot = None;
        let mut stop = None;
        let mut send = None;
        let mut send_key = None;
        let mut unsend = None;
        let mut timezone = None;
        let mut since = None;
        let mut listen = None;
        for line in head.lines() {
            let lower = line.to_ascii_lowercase();
            if let Some(value) = lower.strip_prefix("content-length:") {
                length = value.trim().parse().unwrap_or(0);
            }
            if lower.starts_with("x-ufo-op:") {
                op = Some(line.split_once(':').unwrap().1.trim().to_string());
            }
            if lower.starts_with("x-ufo-slot:") {
                slot = Some(line.split_once(':').unwrap().1.trim().to_string());
            }
            if lower.starts_with("x-ufo-stop:") {
                stop = Some(line.split_once(':').unwrap().1.trim().to_string());
            }
            if lower.starts_with("x-ufo-send:") {
                send = Some(line.split_once(':').unwrap().1.trim().to_string());
            }
            if lower.starts_with("x-ufo-send-id:") {
                send_key = Some(line.split_once(':').unwrap().1.trim().to_string());
            }
            if lower.starts_with("x-ufo-unsend:") {
                unsend = Some(line.split_once(':').unwrap().1.trim().to_string());
            }
            if lower.starts_with("x-ufo-timezone:") {
                timezone = Some(line.split_once(':').unwrap().1.trim().to_string());
            }
            if lower.starts_with("x-ufo-since:") {
                since = Some(line.split_once(':').unwrap().1.trim().to_string());
            }
            if lower.starts_with("x-ufo-listen:") {
                listen = Some(line.split_once(':').unwrap().1.trim().to_string());
            }
        }
        break (
            end + 4,
            system_skills,
            length,
            op,
            slot,
            stop,
            send,
            send_key,
            unsend,
            timezone,
            since,
            listen,
        );
    };
    while raw.len() < headers_end + content_length {
        let read = stream.read(&mut buffer).expect("read body");
        if read == 0 {
            break;
        }
        raw.extend_from_slice(&buffer[..read]);
    }
    Some(Request {
        system_skills,
        body: String::from_utf8_lossy(&raw[headers_end..]).to_string(),
        op_header,
        slot_header,
        stop_header,
        send_header,
        send_id,
        unsend_header,
        timezone_header,
        since_header,
        listen_header,
    })
}

/// Wait out the scripted session. A client that outlives it is killed and everything it said is
/// reported: a session that will not end is a failure to read, never a suite that hangs.
fn wait_for_client(mut child: Child) -> Output {
    let mut out_pipe = child.stdout.take().expect("piped stdout");
    let mut err_pipe = child.stderr.take().expect("piped stderr");
    let out = thread::spawn(move || {
        let mut bytes = Vec::new();
        let _ = out_pipe.read_to_end(&mut bytes);
        bytes
    });
    let err = thread::spawn(move || {
        let mut bytes = Vec::new();
        let _ = err_pipe.read_to_end(&mut bytes);
        bytes
    });
    let deadline = std::time::Instant::now() + CLIENT_WAIT;
    let status = loop {
        match child.try_wait().expect("wait on the client") {
            Some(status) => break status,
            None if std::time::Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                panic!(
                    "the client never exited within {}s\nstdout:\n{}\nstderr:\n{}",
                    CLIENT_WAIT.as_secs(),
                    String::from_utf8_lossy(&out.join().expect("stdout reader")),
                    String::from_utf8_lossy(&err.join().expect("stderr reader")),
                );
            }
            None => thread::sleep(Duration::from_millis(10)),
        }
    };
    Output {
        status,
        stdout: out.join().expect("stdout reader"),
        stderr: err.join().expect("stderr reader"),
    }
}

fn run_client(url: &str, args: &[&str], stdin: &str, home: &std::path::Path) -> (String, i32) {
    let scratch_tmp = home.join("tmp");
    std::fs::create_dir_all(&scratch_tmp).expect("scratch tmp");
    let output = {
        let mut child = Command::new(env!("CARGO_BIN_EXE_ufo"))
            .args(args)
            .env("WORKSPACE_URL", url)
            .env("UFO_URL", url)
            .env("UFO_HOME", home)
            .env("UFO_CHANNEL", "e2e-test")
            .env("TMPDIR", &scratch_tmp)
            .env_remove("NO_COLOR")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("spawn client");
        child
            .stdin
            .take()
            .unwrap()
            .write_all(stdin.as_bytes())
            .expect("feed stdin");
        wait_for_client(child)
    };
    (
        String::from_utf8_lossy(&output.stdout).to_string(),
        output.status.code().unwrap_or(-1),
    )
}

/// The client on a real terminal: the fullscreen loop only runs on a tty, and only a tty delivers
/// a key. Nothing here answers the startup probe, so the client pays its deadline once and reads
/// the terminal's defaults — [`play_the_terminal`] is the rig that answers.
#[cfg(unix)]
struct OnPty {
    keys: std::fs::File,
    child: std::process::Child,
    painted: Arc<Mutex<Vec<u8>>>,
}

#[cfg(unix)]
impl OnPty {
    /// Type `keys` and let the dock repaint.
    fn press(&mut self, keys: &[u8]) {
        self.keys.write_all(keys).expect("keys reach the pty");
        thread::sleep(Duration::from_millis(600));
    }

    /// The dock as it stands, replayed through a terminal emulator.
    fn screen(&self) -> String {
        let mut parser = vt100::Parser::new(24, 100, 0);
        parser.process(&self.painted.lock().unwrap());
        parser.screen().contents()
    }

    /// Everything ever painted, so a popup that came and went is still evidence.
    fn painted(&self) -> String {
        String::from_utf8_lossy(&self.painted.lock().unwrap()).to_string()
    }

    /// Reap the client. A session the script ended has already exited; one still running is
    /// killed and the screen it was holding is reported.
    fn reaped(&mut self) -> std::process::ExitStatus {
        let deadline = std::time::Instant::now() + CLIENT_WAIT;
        loop {
            if let Some(status) = self.child.try_wait().expect("wait on the client") {
                return status;
            }
            if std::time::Instant::now() >= deadline {
                let _ = self.child.kill();
                let _ = self.child.wait();
                panic!(
                    "the client never exited within {}s:\n{}",
                    CLIENT_WAIT.as_secs(),
                    self.screen()
                );
            }
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn ended(&mut self) -> bool {
        for _ in 0..40 {
            if self.child.try_wait().expect("wait on the client").is_some() {
                return true;
            }
            thread::sleep(Duration::from_millis(100));
        }
        false
    }
}

/// The client on a pty, with the leader handed back: whoever holds it types the keys and plays
/// the terminal. `workspace` is the surface a signed-in client posts to; `None` leaves the client
/// signed out, so it drives the gateway's onboarding prompts instead.
#[cfg(unix)]
fn spawn_on_a_pty(
    url: &str,
    args: &[&str],
    home: &std::path::Path,
    workspace: Option<&str>,
    truecolor: bool,
) -> (std::fs::File, std::process::Child) {
    use std::os::fd::FromRawFd;
    use std::os::unix::process::CommandExt;

    let leader = unsafe { libc::posix_openpt(libc::O_RDWR | libc::O_NOCTTY) };
    assert!(leader >= 0, "a pty is available");
    assert_eq!(unsafe { libc::grantpt(leader) }, 0, "the pty is granted");
    assert_eq!(unsafe { libc::unlockpt(leader) }, 0, "the pty is unlocked");
    let name = unsafe { std::ffi::CStr::from_ptr(libc::ptsname(leader)) }.to_owned();
    let follower = unsafe { libc::open(name.as_ptr(), libc::O_RDWR | libc::O_NOCTTY) };
    assert!(follower >= 0, "the pty follower opens");

    let scratch_tmp = home.join("tmp");
    std::fs::create_dir_all(&scratch_tmp).expect("scratch tmp");
    let mut command = Command::new(env!("CARGO_BIN_EXE_ufo"));
    let mut path = home.join("bin").display().to_string();
    if let Ok(inherited) = std::env::var("PATH") {
        path.push(':');
        path.push_str(&inherited);
    }
    command
        .args(args)
        .current_dir(home)
        .env("UFO_URL", url)
        .env("UFO_HOME", home)
        .env("UFO_CHANNEL", "e2e-tty")
        .env("TERM", "xterm-256color")
        .env("TMPDIR", &scratch_tmp)
        .env("PATH", path)
        .env_remove("NO_COLOR")
        .env_remove("UFO_PLAIN");
    if truecolor {
        command.env("COLORTERM", "truecolor");
    }
    match workspace {
        Some(surface) => command.env("WORKSPACE_URL", surface),
        None => command.env_remove("WORKSPACE_URL"),
    };
    for slot in [libc::STDIN_FILENO, libc::STDOUT_FILENO, libc::STDERR_FILENO] {
        let held = unsafe { libc::dup(follower) };
        assert!(held >= 0, "the follower duplicates");
        let stdio = unsafe { std::process::Stdio::from_raw_fd(held) };
        match slot {
            libc::STDIN_FILENO => command.stdin(stdio),
            libc::STDOUT_FILENO => command.stdout(stdio),
            _ => command.stderr(stdio),
        };
    }
    unsafe {
        command.pre_exec(|| {
            libc::setsid();
            Ok(())
        });
    }
    let child = command.spawn().expect("spawn client on a pty");
    unsafe { libc::close(follower) };
    (unsafe { std::fs::File::from_raw_fd(leader) }, child)
}

/// The terminal the client starts on, played from the pty leader: which of the startup queries get
/// answered, how long the terminal takes to answer, and what the member types into the window the
/// probe is reading in.
#[cfg(unix)]
struct Terminal {
    replies: Vec<&'static str>,
    after: Duration,
    typeahead: &'static str,
}

#[cfg(unix)]
const KITTY_REPLY: &str = "\x1b[?0u";
#[cfg(unix)]
const LIGHT_REPLY: &str = "\x1b]11;rgb:f5f5/f5f5/f4f4\x1b\\";
#[cfg(unix)]
const ATTRIBUTES_REPLY: &str = "\x1b[?62;1;6c";
#[cfg(unix)]
const PROBE_QUERY: &str = "\x1b]11;?";
#[cfg(unix)]
const MODES_ON: &str = "\x1b[?2004h";
#[cfg(unix)]
const FLAGS_PUSH: &str = "\x1b[>1u";
#[cfg(unix)]
const FLAGS_POP: &str = "\x1b[<1u";
#[cfg(unix)]
const DARK_PROMPT: &str = "38;2;255;135;255";
#[cfg(unix)]
const LIGHT_PROMPT: &str = "38;2;162;28;175";
#[cfg(unix)]
const PROMPT_GLYPH: &str = "\u{203a}";
#[cfg(unix)]
const SPINNER_FIRST: &str = "\u{280b}";
#[cfg(unix)]
const SPINNER_SECOND: &str = "\u{2819}";

#[cfg(unix)]
#[derive(Default)]
struct Tape {
    painted: Vec<u8>,
    asked_at: Option<Duration>,
    modes_at: Option<Duration>,
    frame_at: Option<Duration>,
}

#[cfg(unix)]
struct Played {
    child: std::process::Child,
    keys: std::fs::File,
    tape: Arc<Mutex<Tape>>,
}

#[cfg(unix)]
fn play_the_terminal(url: &str, home: &std::path::Path, terminal: Terminal) -> Played {
    let (keys, child) = spawn_on_a_pty(url, &["go"], home, Some(url), true);
    let mut reader = keys.try_clone().expect("the leader duplicates");
    let mut writer = keys.try_clone().expect("the leader duplicates");
    let tape = Arc::new(Mutex::new(Tape::default()));
    let played = Arc::clone(&tape);
    let start = std::time::Instant::now();
    thread::spawn(move || {
        let mut buffer = [0u8; 8192];
        let mut answered = false;
        while let Ok(read) = reader.read(&mut buffer) {
            if read == 0 {
                return;
            }
            let at = start.elapsed();
            let asked = {
                let mut tape = played.lock().unwrap();
                tape.painted.extend_from_slice(&buffer[..read]);
                if tape.asked_at.is_none() && written(&tape.painted, PROBE_QUERY) {
                    tape.asked_at = Some(at);
                }
                if tape.modes_at.is_none() && written(&tape.painted, MODES_ON) {
                    tape.modes_at = Some(at);
                }
                if tape.frame_at.is_none() && written(&tape.painted, PROMPT_GLYPH) {
                    tape.frame_at = Some(at);
                }
                tape.asked_at.is_some()
            };
            if asked && !answered {
                answered = true;
                let _ = writer.write_all(terminal.typeahead.as_bytes());
                thread::sleep(terminal.after);
                for reply in &terminal.replies {
                    let _ = writer.write_all(reply.as_bytes());
                }
            }
        }
    });
    Played { child, keys, tape }
}

#[cfg(unix)]
fn written(painted: &[u8], marker: &str) -> bool {
    painted
        .windows(marker.len())
        .any(|window| window == marker.as_bytes())
}

#[cfg(unix)]
impl Played {
    /// Wait for the composer to paint, and answer when it did.
    fn framed(&self, within: Duration) -> Duration {
        let deadline = std::time::Instant::now() + within;
        loop {
            if let Some(at) = self.tape.lock().unwrap().frame_at {
                return at;
            }
            assert!(
                std::time::Instant::now() < deadline,
                "the composer never painted:\n{}",
                self.screen()
            );
            thread::sleep(Duration::from_millis(20));
        }
    }

    /// What the client waited on the terminal: the probe goes out, and the modes it holds until the
    /// read is done go out after.
    fn probe_wait(&self) -> Duration {
        let tape = self.tape.lock().unwrap();
        let asked = tape.asked_at.expect("the client asked the terminal");
        let done = tape.modes_at.expect("the client finished its read");
        done - asked
    }

    fn painted(&self) -> String {
        String::from_utf8_lossy(&self.tape.lock().unwrap().painted).to_string()
    }

    fn screen(&self) -> String {
        let mut parser = vt100::Parser::new(24, 100, 0);
        parser.process(&self.tape.lock().unwrap().painted);
        parser.screen().contents()
    }

    fn press(&mut self, keys: &[u8]) {
        self.keys.write_all(keys).expect("keys reach the pty");
    }

    fn ended(&mut self, within: Duration) -> bool {
        let deadline = std::time::Instant::now() + within;
        while std::time::Instant::now() < deadline {
            if self.child.try_wait().expect("wait on the client").is_some() {
                return true;
            }
            thread::sleep(Duration::from_millis(50));
        }
        false
    }
}

#[cfg(unix)]
fn run_client_on_pty(
    url: &str,
    args: &[&str],
    home: &std::path::Path,
    workspace: Option<&str>,
) -> OnPty {
    let (keys, child) = spawn_on_a_pty(url, args, home, workspace, false);
    let mut reader = keys.try_clone().expect("the leader duplicates");
    let painted = Arc::new(Mutex::new(Vec::new()));
    let sink = Arc::clone(&painted);
    thread::spawn(move || {
        let mut buffer = [0u8; 8192];
        while let Ok(read) = reader.read(&mut buffer) {
            if read == 0 {
                return;
            }
            sink.lock().unwrap().extend_from_slice(&buffer[..read]);
        }
    });
    OnPty {
        keys,
        child,
        painted,
    }
}

fn scratch_home(name: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("ufo-e2e-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("credentials"), "test-token").unwrap();
    std::fs::write(dir.join("Cargo.toml"), "").unwrap();
    dir
}

#[test]
fn help_prints_usage_and_never_touches_the_wire() {
    let home = scratch_home("help");
    for flag in ["--help", "-h"] {
        let (stdout, code) = run_client("http://127.0.0.1:9", &[flag], "", &home);
        assert_eq!(code, 0, "{flag} exits 0");
        assert!(
            stdout.contains("Usage: ufo"),
            "{flag} prints usage:\n{stdout}"
        );
        for named in ["login", "logout", "--resume", "--json", "--help"] {
            assert!(stdout.contains(named), "{flag} names {named}:\n{stdout}");
        }
    }
}

#[test]
fn plain_session_round_trips_ask_and_exit() {
    let served = serve(vec![
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\thello there", "ask\t>"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["txt\tThe answer.", "exit\t0"],
        },
    ]);
    let home = scratch_home("plain");
    let (stdout, code) = run_client(&served.url, &[], "hi\n", &home);
    served
        .skills_arrived
        .recv_timeout(SCRIPT_WAIT)
        .expect("startup fetches the system skill bundle");
    let requests = served.gateway.requests();
    assert_eq!(code, 0);
    assert!(stdout.contains("hello there"), "stdout: {stdout}");
    assert!(stdout.contains("The answer."), "stdout: {stdout}");
    assert_eq!(requests.len(), 2);
    assert_eq!(requests[1].body, "hi");
    assert_eq!(
        requests[1].timezone_header,
        iana_time_zone::get_timezone().ok()
    );
    assert!(requests[1].timezone_header.is_some(), "{requests:?}");
    let leftovers: Vec<_> = std::fs::read_dir(home.join("tmp"))
        .expect("scratch tmp readable")
        .flatten()
        .map(|entry| entry.file_name())
        .collect();
    assert!(
        leftovers.is_empty(),
        "the private workdir is removed when the session ends: {leftovers:?}"
    );
    let _ = std::fs::remove_dir_all(&home);
}

/// A lane the client opened and abandoned — one it was still connecting as it left — carries no
/// request, so the gateway holds its script for whoever speaks next.
#[test]
fn an_abandoned_connection_is_no_exchange() {
    let served = serve(vec![Exchange {
        delay_ms: 0,
        status: 200,
        reply_lines: &["say\thello", "exit\t0"],
    }]);
    let address = served
        .url
        .strip_prefix("http://")
        .expect("a host:port url")
        .to_string();
    drop(std::net::TcpStream::connect(&address).expect("connect"));
    let home = scratch_home("abandoned");
    let (stdout, code) = run_client(&served.url, &["hi"], "", &home);
    let requests = served.gateway.requests();
    assert_eq!(code, 0, "stdout: {stdout}");
    assert!(stdout.contains("hello"), "stdout: {stdout}");
    assert_eq!(requests.len(), 1, "{requests:?}");
    assert_eq!(requests[0].body, "hi");
    let _ = std::fs::remove_dir_all(&home);
}

#[test]
fn exec_op_runs_and_replies_on_the_op_channel() {
    let served = serve(vec![
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["run\top-1\texec\texec\t30\t\t{\"argv\":[\"echo\",\"proof\"]}"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\tdone", "exit\t0"],
        },
    ]);
    let home = scratch_home("ops");
    let (stdout, code) = run_client(&served.url, &["start"], "", &home);
    let requests = served.gateway.requests();
    assert_eq!(code, 0, "stdout: {stdout}");
    assert!(stdout.contains("done"));
    assert_eq!(requests.len(), 2);
    assert_eq!(requests[0].body, "start");
    assert_eq!(requests[1].op_header.as_deref(), Some("op-1"));
    let reply: serde_json::Value = serde_json::from_str(&requests[1].body).expect("op reply JSON");
    let stdout_b64 = reply["stdout_b64"].as_str().expect("stdout_b64 field");
    use base64::Engine as _;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(stdout_b64)
        .expect("stdout_b64 decodes");
    assert_eq!(String::from_utf8_lossy(&bytes), "proof\n");
    let _ = std::fs::remove_dir_all(&home);
}

#[test]
fn json_mode_speaks_the_event_protocol() {
    let served = serve(vec![Exchange {
        delay_ms: 0,
        status: 200,
        reply_lines: &[
            "token\ttok-1",
            "workspace\thttp://workspace.example",
            "txt\tpartial ",
            "txt\treply",
            "say\twhole",
            "exit\t0",
        ],
    }]);
    let home = scratch_home("json");
    let (stdout, code) = run_client(&served.url, &["--json", "go"], "", &home);
    served.gateway.done();
    assert_eq!(code, 0, "stdout: {stdout}");
    let events: Vec<serde_json::Value> = stdout
        .lines()
        .map(|line| serde_json::from_str(line).expect("event line is JSON"))
        .collect();
    let kinds: Vec<&str> = events
        .iter()
        .map(|event| event["type"].as_str().unwrap())
        .collect();
    assert_eq!(kinds[0], "session_start");
    assert!(events[0]["v"].as_u64().is_some());
    assert_eq!(kinds[1], "turn_start");
    assert!(kinds.contains(&"text_delta"));
    assert!(kinds.contains(&"message"));
    assert!(kinds.contains(&"exit"));
    let signed_in = events
        .iter()
        .find(|event| event["type"] == "signed_in")
        .expect("sign-in is observable");
    assert_eq!(signed_in["workspace_url"], "http://workspace.example");
    assert_eq!(signed_in["channel"], "e2e-test");
    let _ = std::fs::remove_dir_all(&home);
}

#[test]
fn resume_replays_history_before_the_tail() {
    let served = serve(vec![Exchange {
        delay_ms: 0,
        status: 200,
        reply_lines: &[
            "you\tearlier question",
            "say\tearlier answer",
            "txt\tthe latest reply",
            "exit\t0",
        ],
    }]);
    let home = scratch_home("resume");
    let (stdout, code) = run_client(&served.url, &[], "", &home);
    served.gateway.done();
    assert_eq!(code, 0, "stdout: {stdout}");
    let member = stdout
        .find("\u{203a} earlier question")
        .expect("member line");
    let reply = stdout.find("earlier answer").expect("reply line");
    let latest = stdout.find("the latest reply").expect("tail line");
    assert!(member < reply && reply < latest, "stdout: {stdout}");
}

#[test]
fn a_command_burst_while_the_wire_waits_loses_nothing() {
    let served = serve(vec![
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &[
                "say\thello",
                "secret\tsealed1\ts1\tFirst secret",
                "secret\tsealed2\ts2\tSecond secret",
                "ask\t>",
            ],
        },
        Exchange {
            delay_ms: 300,
            status: 200,
            reply_lines: &["say\tstored"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\tstored"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["exit\t0"],
        },
    ]);
    let home = scratch_home("burst");
    let (stdout, code) = run_client(&served.url, &[], "alpha\nbeta\ngamma\n", &home);
    let requests = served.gateway.requests();
    assert_eq!(code, 0, "stdout: {stdout}");
    assert_eq!(requests[1].slot_header.as_deref(), Some("s1"));
    assert_eq!(
        requests[2].slot_header.as_deref(),
        Some("s2"),
        "the second secret must not be swallowed by the wire's idle wait"
    );
    assert_eq!(requests[3].body, "gamma");
}

#[test]
fn a_finished_ops_reply_outranks_a_queued_message() {
    let served = serve(vec![
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["run\top-slow\texec\texec\t30\t\t{\"argv\":[\"sleep\",\"2\"]}"],
        },
        Exchange {
            delay_ms: 0,
            status: 500,
            reply_lines: &["no lane"],
        },
        Exchange {
            delay_ms: 0,
            status: 500,
            reply_lines: &["still none"],
        },
        Exchange {
            delay_ms: 0,
            status: 500,
            reply_lines: &["and none"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\tok", "ask\t>"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["exit\t0"],
        },
    ]);
    let home = scratch_home("op-priority");
    let (stdout, code) = {
        let mut child = Command::new(env!("CARGO_BIN_EXE_ufo"))
            .args(["--json", "go"])
            .env("WORKSPACE_URL", &served.url)
            .env("UFO_URL", &served.url)
            .env("UFO_HOME", &home)
            .env("UFO_CHANNEL", "e2e-test")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("spawn client");
        let mut stdin = child.stdin.take().unwrap();
        let arrived = served.arrived;
        let writer = thread::spawn(move || {
            arrived
                .recv_timeout(ARRIVAL_WAIT)
                .expect("the first post arrives");
            thread::sleep(std::time::Duration::from_millis(200));
            let _ = stdin.write_all(b"{\"type\":\"send\",\"text\":\"queued while the op ran\"}\n");
        });
        let output = wait_for_client(child);
        writer.join().unwrap();
        (
            String::from_utf8_lossy(&output.stdout).to_string(),
            output.status.code().unwrap_or(-1),
        )
    };
    let requests = served.gateway.requests();
    assert_eq!(code, 0, "stdout: {stdout}");
    for attempt in 1..=3 {
        assert_eq!(
            requests[attempt].send_header.as_deref(),
            Some("1"),
            "the mid-turn message travels its own request: {requests:?}"
        );
        assert_eq!(requests[attempt].body, "queued while the op ran");
        assert_eq!(
            requests[attempt].send_id, requests[1].send_id,
            "every retry keeps the delivery's one idempotency key: {requests:?}"
        );
    }
    assert_eq!(
        requests[4].op_header.as_deref(),
        Some("op-slow"),
        "the turn is blocked on the op reply, so it posts before the fallback: {requests:?}"
    );
    assert_eq!(requests[5].send_header, None);
    assert_eq!(
        requests[5].body, "queued while the op ran",
        "a send that never acks falls back to the boundary queue: {requests:?}"
    );
}

#[test]
fn a_mid_turn_send_posts_instantly_and_settles_on_absorption() {
    let served = serve(vec![
        Exchange {
            delay_ms: 1500,
            status: 200,
            reply_lines: &["say\tfirst", "poll\t1"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["sent\tturn-1\t0\tarr-9"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["absorbed\tarr-9", "say\tsecond", "exit\t0"],
        },
    ]);
    let home = scratch_home("instant-send");
    let (stdout, code) = {
        let mut child = Command::new(env!("CARGO_BIN_EXE_ufo"))
            .args(["--json", "go"])
            .env("WORKSPACE_URL", &served.url)
            .env("UFO_URL", &served.url)
            .env("UFO_HOME", &home)
            .env("UFO_CHANNEL", "e2e-test")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("spawn client");
        let mut stdin = child.stdin.take().unwrap();
        let arrived = served.arrived;
        let writer = thread::spawn(move || {
            arrived
                .recv_timeout(ARRIVAL_WAIT)
                .expect("the first post arrives");
            thread::sleep(std::time::Duration::from_millis(200));
            let _ = stdin.write_all(b"{\"type\":\"send\",\"text\":\"while the turn ran\"}\n");
        });
        let output = wait_for_client(child);
        writer.join().unwrap();
        (
            String::from_utf8_lossy(&output.stdout).to_string(),
            output.status.code().unwrap_or(-1),
        )
    };
    let requests = served.gateway.requests();
    assert_eq!(code, 0, "stdout: {stdout}");
    assert_eq!(requests.len(), 3, "{requests:?}");
    assert_eq!(requests[1].send_header.as_deref(), Some("1"));
    assert_eq!(requests[1].body, "while the turn ran");
    assert!(requests[1].send_id.is_some());
    assert_eq!(
        requests[1].timezone_header,
        iana_time_zone::get_timezone().ok()
    );
    assert!(requests[1].timezone_header.is_some(), "{requests:?}");
    assert_eq!(
        requests[2].body, "",
        "the scheduled poll still fires: {requests:?}"
    );
    let sent = stdout
        .lines()
        .find(|line| line.contains("\"type\":\"message_sent\""))
        .expect("the ack is observable");
    assert!(sent.contains("\"turn_id\":\"turn-1\""), "{sent}");
    assert!(sent.contains("\"arrival_id\":\"arr-9\""), "{sent}");
    assert!(sent.contains("\"opened_run\":false"), "{sent}");
    let absorbed = stdout
        .lines()
        .find(|line| line.contains("\"type\":\"message_absorbed\""))
        .expect("the fold is observable");
    assert!(absorbed.contains("arr-9"), "{absorbed}");
}

#[cfg(unix)]
#[test]
fn the_first_frame_is_painted_before_the_loop_waits_on_anything() {
    let served = serve(vec![Exchange {
        delay_ms: 900,
        status: 200,
        reply_lines: &["say\thello", "exit\t0"],
    }]);
    let home = scratch_home("tty-frame-zero");
    let mut session = run_client_on_pty(&served.url, &["echoed"], &home, Some(&served.url));
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the launch message reaches the gateway");
    let deadline = std::time::Instant::now() + Duration::from_secs(10);
    let first_frame = loop {
        let painted = session.painted();
        if let Some(at) = painted.find(PROMPT_GLYPH) {
            break painted[..at].to_string();
        }
        assert!(
            std::time::Instant::now() < deadline,
            "the composer never painted: {painted:?}"
        );
        thread::sleep(Duration::from_millis(20));
    };
    let _ = session.child.kill();
    let _ = session.child.wait();
    served.gateway.done();
    assert!(
        first_frame.contains(SPINNER_FIRST) && !first_frame.contains(SPINNER_SECOND),
        "a tick advances the spinner before painting, and the turn is open before the frame: {first_frame:?}"
    );
    assert!(
        !first_frame.contains("echoed"),
        "the wire's echo repaints, and the frame is painted before it is read: {first_frame:?}"
    );
    let _ = std::fs::remove_dir_all(&home);
}

#[cfg(unix)]
#[test]
fn esc_on_a_running_turn_posts_the_stop() {
    let served = serve(vec![
        Exchange {
            delay_ms: 2000,
            status: 200,
            reply_lines: &["txt\tthinking", "ask\t>"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\tcancelled", "ask\t>"],
        },
    ]);
    let home = scratch_home("stop");
    let mut session = run_client_on_pty(&served.url, &["go"], &home, Some(&served.url));
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the turn's own post reaches the gateway");
    session
        .keys
        .write_all(b"\x1b")
        .expect("Esc reaches the pty");
    let requests = served.gateway.requests();
    let _ = session.child.kill();
    let _ = session.child.wait();
    assert_eq!(requests.len(), 2, "{requests:?}");
    assert_eq!(requests[0].body, "go");
    assert_eq!(
        requests[1].stop_header.as_deref(),
        Some("1"),
        "Esc ends the turn on its own connection: {requests:?}"
    );
    assert_eq!(requests[1].body, "", "a stop admits no message");
    let _ = std::fs::remove_dir_all(&home);
}

#[cfg(unix)]
#[test]
fn an_ack_naming_no_arrival_settles_the_row_at_once() {
    let served = serve(vec![
        Exchange {
            delay_ms: 2500,
            status: 200,
            reply_lines: &["txt\tworking", "poll\t1"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["sent\tturn-1\t0\t"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\tdone", "exit\t0"],
        },
    ]);
    let home = scratch_home("tty-settle");
    let mut session = run_client_on_pty(&served.url, &["go"], &home, Some(&served.url));
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the turn's own post reaches the gateway");
    std::thread::sleep(std::time::Duration::from_millis(300));
    session
        .keys
        .write_all(b"later thought\r")
        .expect("the mid-turn message reaches the pty");
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(20);
    while !session.ended() {
        assert!(std::time::Instant::now() < deadline, "client never exited");
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    let requests = served.gateway.requests();
    session.reaped();
    assert_eq!(
        requests.len(),
        3,
        "no unsend and no resend fire: {requests:?}"
    );
    assert_eq!(requests[1].send_header.as_deref(), Some("1"));
    let printed = session.painted();
    let leave = printed
        .rfind("\x1b[?1049l")
        .expect("the session leaves the alternate screen");
    let mut parser = vt100::Parser::new(24, 100, 0);
    parser.process(&printed.as_bytes()[leave..]);
    let document = parser.screen().contents();
    assert_eq!(
        document.matches("\u{203a} later thought").count(),
        1,
        "the message the turn already holds settles once: {document}"
    );
}

#[cfg(unix)]
fn stub_clipboard(home: &std::path::Path, scripts: &[(&str, &str)]) {
    use std::os::unix::fs::PermissionsExt;

    let bin = home.join("bin");
    std::fs::create_dir_all(&bin).unwrap();
    for (name, script) in scripts {
        let stub = bin.join(name);
        std::fs::write(&stub, script).unwrap();
        std::fs::set_permissions(&stub, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
}

#[cfg(unix)]
fn stub_clipboard_image(home: &std::path::Path) {
    let raw_png = "#!/bin/sh\nprintf '\\211PNG\\r\\n\\032\\n\\000\\001'\n";
    let hex_png = "#!/bin/sh\nprintf '\\302\\253data PNGf89504E470D0A1A0A0001\\302\\273\\n'\n";
    stub_clipboard(
        home,
        &[
            ("osascript", hex_png),
            ("wl-paste", raw_png),
            ("xclip", raw_png),
        ],
    );
}

#[cfg(unix)]
fn stub_clipboard_text(home: &std::path::Path, value: &str) {
    let no_image = "#!/bin/sh\nexit 1\n";
    let text = format!("#!/bin/sh\ncase \"$*\" in *image/png*) exit 1;; esac\nprintf '{value}'\n");
    let plain = format!("#!/bin/sh\nprintf '{value}'\n");
    stub_clipboard(
        home,
        &[
            ("osascript", no_image),
            ("pbpaste", &plain),
            ("wl-paste", &text),
            ("xclip", &text),
        ],
    );
}

#[cfg(unix)]
fn stub_clipboard_hang(home: &std::path::Path) {
    let hang = "#!/bin/sh\nsleep 30\n";
    stub_clipboard(
        home,
        &[
            ("osascript", hang),
            ("pbpaste", hang),
            ("wl-paste", hang),
            ("xclip", hang),
        ],
    );
}

#[cfg(unix)]
#[test]
fn a_pasted_image_marks_the_entry_and_sends_its_path() {
    let served = serve(vec![
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\thello", "ask\t>"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\tseen", "exit\t0"],
        },
    ]);
    let home = scratch_home("tty-image");
    stub_clipboard_image(&home);
    let mut session = run_client_on_pty(&served.url, &["hi"], &home, Some(&served.url));
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the first post reaches the gateway");
    session
        .keys
        .write_all(b"\x16")
        .expect("Ctrl+V reaches the pty");
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
    while !session.painted().contains("[Image #1]") {
        assert!(
            std::time::Instant::now() < deadline,
            "the marker never painted: {}",
            session.painted()
        );
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    let relative = format!(".ufo/images/image-{}-1.png", session.child.id());
    let stashed = home.join(&relative);
    assert_eq!(
        std::fs::read(&stashed).expect("the image is stashed under the workspace"),
        [0x89, b'P', b'N', b'G', b'\r', b'\n', 0x1a, b'\n', 0, 1]
    );
    session.keys.write_all(b"\r").expect("Enter sends");
    let requests = served.gateway.requests();
    session.reaped();
    assert_eq!(requests.len(), 2, "{requests:?}");
    assert_eq!(
        requests[1].body,
        format!("[Image #1: {relative}]"),
        "the send names the workspace-relative path"
    );
    assert!(!stashed.exists(), "the client sweeps its images on exit");
    assert!(
        !home.join(".ufo").exists(),
        "an emptied stash directory leaves nothing behind"
    );
    let _ = std::fs::remove_dir_all(&home);
}

#[cfg(unix)]
#[test]
fn a_clipboard_read_lands_only_in_the_entry_that_asked() {
    let served = serve(vec![
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["secret\tsealed1\ts1\tPaste the key", "ask\t>"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\tstored", "ask\t>"],
        },
    ]);
    let home = scratch_home("tty-cliprace");
    let no_image = "#!/bin/sh\nexit 1\n";
    let slow =
        "#!/bin/sh\ncase \"$*\" in *image/png*) exit 1;; esac\nsleep 0.6\nprintf 'sk-live-abc123'\n";
    stub_clipboard(
        &home,
        &[
            ("osascript", no_image),
            ("pbpaste", slow),
            ("wl-paste", slow),
            ("xclip", slow),
        ],
    );
    let mut session = run_client_on_pty(&served.url, &["go"], &home, Some(&served.url));
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the opening post reaches the gateway");
    thread::sleep(Duration::from_millis(800));
    session
        .keys
        .write_all(b"\x16\r")
        .expect("Ctrl+V then Enter, without waiting for the read");
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
    while !session.screen().contains("press Ctrl+V again") {
        assert!(
            std::time::Instant::now() < deadline,
            "the dropped paste never named itself: {}",
            session.screen()
        );
        thread::sleep(Duration::from_millis(50));
    }
    let screen = session.screen();
    let _ = session.child.kill();
    let _ = session.child.wait();
    served.gateway.done();
    assert!(
        !screen.contains("sk-live-abc123"),
        "the masked value never reaches the composer: {screen}"
    );
    let _ = std::fs::remove_dir_all(&home);
}

#[cfg(unix)]
#[test]
fn a_clipboard_deadline_frees_ctrl_v() {
    let served = serve(vec![Exchange {
        delay_ms: 0,
        status: 200,
        reply_lines: &["say\thello", "ask\t>"],
    }]);
    let home = scratch_home("tty-clipwedge");
    stub_clipboard_hang(&home);
    let mut session = run_client_on_pty(&served.url, &["hi"], &home, Some(&served.url));
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the first post reaches the gateway");
    thread::sleep(Duration::from_millis(800));
    session.keys.write_all(b"\x16").expect("the first Ctrl+V");
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(15);
    while !session.screen().contains("did not answer within") {
        assert!(
            std::time::Instant::now() < deadline,
            "the deadline never fired: {}",
            session.screen()
        );
        thread::sleep(Duration::from_millis(100));
    }
    stub_clipboard_text(&home, "second-paste");
    session.keys.write_all(b"\x16").expect("the second Ctrl+V");
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
    while !session.screen().contains("second-paste") {
        assert!(
            std::time::Instant::now() < deadline,
            "Ctrl+V stayed wedged after the deadline: {}",
            session.screen()
        );
        thread::sleep(Duration::from_millis(50));
    }
    let _ = session.child.kill();
    let _ = session.child.wait();
    served.gateway.done();
    let _ = std::fs::remove_dir_all(&home);
}

#[cfg(unix)]
#[test]
fn a_dropped_image_path_attaches_as_an_image() {
    let served = serve(vec![
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\thello", "ask\t>"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\tseen", "exit\t0"],
        },
    ]);
    let home = scratch_home("tty-drop");
    let source = home.join("shot one.png");
    let png = [0x89u8, b'P', b'N', b'G', b'\r', b'\n', 0x1a, b'\n', 7, 7];
    std::fs::write(&source, png).unwrap();
    let mut session = run_client_on_pty(&served.url, &["hi"], &home, Some(&served.url));
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the first post reaches the gateway");
    thread::sleep(Duration::from_millis(800));
    let dropped = format!(
        "\x1b[200~{} \x1b[201~",
        source.display().to_string().replace(' ', "\\ ")
    );
    session
        .keys
        .write_all(dropped.as_bytes())
        .expect("the dropped path reaches the pty");
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
    while !session.screen().contains("[Image #1]") {
        assert!(
            std::time::Instant::now() < deadline,
            "the dropped path never attached: {}",
            session.screen()
        );
        thread::sleep(Duration::from_millis(50));
    }
    let relative = format!(".ufo/images/image-{}-1.png", session.child.id());
    assert_eq!(
        std::fs::read(home.join(&relative)).expect("the dropped image is copied into the stash"),
        png
    );
    session.keys.write_all(b"\r").expect("Enter sends");
    let requests = served.gateway.requests();
    session.reaped();
    assert_eq!(requests.len(), 2, "{requests:?}");
    assert_eq!(
        requests[1].body,
        format!("[Image #1: {relative}]"),
        "the send names the stashed copy, never the dropped path"
    );
    assert!(
        source.exists(),
        "the member's own file is copied, never moved"
    );
    let _ = std::fs::remove_dir_all(&home);
}

#[cfg(unix)]
#[test]
fn ctrl_v_with_a_copied_image_file_attaches_it() {
    let served = serve(vec![
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\thello", "ask\t>"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\tseen", "exit\t0"],
        },
    ]);
    let home = scratch_home("tty-filecopy");
    let source = home.join("copied.png");
    let png = [0x89u8, b'P', b'N', b'G', b'\r', b'\n', 0x1a, b'\n', 3, 3];
    std::fs::write(&source, png).unwrap();
    let file_clipboard = format!(
        "#!/bin/sh\ncase \"$*\" in *PNGf*) exit 1;; *furl*) printf '{}\\n';; *image/png*) exit 1;; \
         *uri-list*) printf 'file://{}\\n';; *) exit 1;; esac\n",
        source.display(),
        source.display()
    );
    stub_clipboard(
        &home,
        &[
            ("osascript", &file_clipboard),
            ("pbpaste", "#!/bin/sh\nexit 0\n"),
            ("wl-paste", &file_clipboard),
            ("xclip", &file_clipboard),
        ],
    );
    let mut session = run_client_on_pty(&served.url, &["hi"], &home, Some(&served.url));
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the first post reaches the gateway");
    thread::sleep(Duration::from_millis(800));
    session.keys.write_all(b"\x16").expect("Ctrl+V");
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
    while !session.screen().contains("[Image #1]") {
        assert!(
            std::time::Instant::now() < deadline,
            "the copied file never attached: {}",
            session.screen()
        );
        thread::sleep(Duration::from_millis(50));
    }
    let relative = format!(".ufo/images/image-{}-1.png", session.child.id());
    assert_eq!(
        std::fs::read(home.join(&relative)).expect("the copied file lands in the stash"),
        png
    );
    session.keys.write_all(b"\r").expect("Enter sends");
    let requests = served.gateway.requests();
    session.reaped();
    assert_eq!(
        requests[1].body,
        format!("[Image #1: {relative}]"),
        "{requests:?}"
    );
    let _ = std::fs::remove_dir_all(&home);
}

#[cfg(unix)]
#[test]
fn an_image_at_the_path_popup_names_the_drop() {
    let served = serve(vec![Exchange {
        delay_ms: 0,
        status: 200,
        reply_lines: &["say\thello", "ask\t>"],
    }]);
    let home = scratch_home("tty-clippath");
    stub_clipboard_image(&home);
    let mut session = run_client_on_pty(&served.url, &["hi"], &home, Some(&served.url));
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the first post reaches the gateway");
    thread::sleep(Duration::from_millis(800));
    session.press(b"read @");
    session.keys.write_all(b"\x16").expect("Ctrl+V");
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
    while !session
        .screen()
        .contains("An image pastes into the message entry only.")
    {
        assert!(
            std::time::Instant::now() < deadline,
            "the dropped image never named itself: {}",
            session.screen()
        );
        thread::sleep(Duration::from_millis(50));
    }
    let painted = session.painted();
    let _ = session.child.kill();
    let _ = session.child.wait();
    served.gateway.done();
    assert!(
        !painted.contains("[Image #"),
        "no marker lands outside the composer: {painted}"
    );
    assert!(
        !home.join(".ufo").exists(),
        "a dropped image writes nothing: {painted}"
    );
    let _ = std::fs::remove_dir_all(&home);
}

#[cfg(unix)]
#[test]
fn a_hung_clipboard_tool_never_wedges_the_session() {
    let served = serve(vec![
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\thello", "ask\t>"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\tseen", "exit\t0"],
        },
    ]);
    let home = scratch_home("tty-cliphang");
    stub_clipboard_hang(&home);
    let mut session = run_client_on_pty(&served.url, &["hi"], &home, Some(&served.url));
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the first post reaches the gateway");
    thread::sleep(Duration::from_millis(800));
    session.keys.write_all(b"\x16").expect("Ctrl+V");
    session
        .keys
        .write_all(b"still alive\r")
        .expect("typing continues while the tool hangs");
    let requests = served.gateway.requests();
    let _ = session.child.kill();
    let _ = session.child.wait();
    assert_eq!(requests.len(), 2, "{requests:?}");
    assert_eq!(
        requests[1].body, "still alive",
        "the session sends while the clipboard read blocks: {requests:?}"
    );
    let _ = std::fs::remove_dir_all(&home);
}

#[cfg(unix)]
#[test]
fn ctrl_v_pastes_text_into_the_masked_entry() {
    let served = serve(vec![
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["secret\tsealed1\ts1\tPaste the key", "ask\t>"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\tstored", "exit\t0"],
        },
    ]);
    let home = scratch_home("tty-clipsecret");
    stub_clipboard_text(&home, "sk-live-abc123");
    let mut session = run_client_on_pty(&served.url, &["go"], &home, Some(&served.url));
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the opening post reaches the gateway");
    thread::sleep(Duration::from_millis(800));
    session.keys.write_all(b"\x16").expect("Ctrl+V");
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
    while !session.screen().contains(&"\u{2022}".repeat(14)) {
        assert!(
            std::time::Instant::now() < deadline,
            "the pasted key never filled the masked entry: {}",
            session.screen()
        );
        thread::sleep(Duration::from_millis(50));
    }
    session.press(b"\r");
    let requests = served.gateway.requests();
    let _ = session.child.kill();
    let _ = session.child.wait();
    assert_eq!(
        requests[1].slot_header.as_deref(),
        Some("s1"),
        "{requests:?}"
    );
    assert_eq!(requests[1].body, "sk-live-abc123", "{requests:?}");
    let _ = std::fs::remove_dir_all(&home);
}

#[cfg(unix)]
#[test]
fn an_empty_clipboard_names_itself() {
    let served = serve(vec![Exchange {
        delay_ms: 0,
        status: 200,
        reply_lines: &["say\thello", "ask\t>"],
    }]);
    let home = scratch_home("tty-clipempty");
    stub_clipboard_text(&home, "");
    let mut session = run_client_on_pty(&served.url, &["hi"], &home, Some(&served.url));
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the first post reaches the gateway");
    thread::sleep(Duration::from_millis(800));
    session.keys.write_all(b"\x16").expect("Ctrl+V");
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
    while !session
        .screen()
        .contains("The clipboard holds nothing to paste.")
    {
        assert!(
            std::time::Instant::now() < deadline,
            "the empty clipboard never named itself: {}",
            session.screen()
        );
        thread::sleep(Duration::from_millis(50));
    }
    let _ = session.child.kill();
    let _ = session.child.wait();
    served.gateway.done();
    let _ = std::fs::remove_dir_all(&home);
}

#[cfg(unix)]
#[test]
fn up_recalls_the_queued_send_and_enter_sends_it_again() {
    let served = serve(vec![
        Exchange {
            delay_ms: 4000,
            status: 200,
            reply_lines: &["txt\tworking", "poll\t1"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["sent\tturn-1\t0\tarr-1"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &[],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["sent\tturn-1\t0\tarr-2"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["absorbed\tarr-2", "say\tdone", "exit\t0"],
        },
    ]);
    let home = scratch_home("tty-recall");
    let mut session = run_client_on_pty(&served.url, &["go"], &home, Some(&served.url));
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the turn's own post reaches the gateway");
    std::thread::sleep(std::time::Duration::from_millis(300));
    session
        .keys
        .write_all(b"later thought\r")
        .expect("the mid-turn message reaches the pty");
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the send reaches the gateway");
    std::thread::sleep(std::time::Duration::from_millis(400));
    session
        .keys
        .write_all(b"\x1b[A")
        .expect("Up reaches the pty");
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the unsend reaches the gateway");
    std::thread::sleep(std::time::Duration::from_millis(400));
    session
        .keys
        .write_all(b"\r")
        .expect("Enter resends the recalled text");
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(20);
    while !session.ended() {
        assert!(std::time::Instant::now() < deadline, "client never exited");
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    let requests = served.gateway.requests();
    session.reaped();
    assert_eq!(requests.len(), 5, "{requests:?}");
    assert_eq!(requests[1].send_header.as_deref(), Some("1"));
    assert_eq!(requests[1].body, "later thought");
    assert_eq!(
        requests[2].unsend_header.as_deref(),
        Some("arr-1"),
        "Up retracts the acknowledged send: {requests:?}"
    );
    assert_eq!(requests[2].body, "", "an unsend admits no message");
    assert_eq!(requests[3].send_header.as_deref(), Some("1"));
    assert_eq!(
        requests[3].body, "later thought",
        "the recalled text sends again from the composer: {requests:?}"
    );
    assert_ne!(
        requests[3].send_id, requests[1].send_id,
        "a re-send is a new delivery: {requests:?}"
    );
    let printed = session.painted();
    let leave = printed
        .rfind("\x1b[?1049l")
        .expect("the session leaves the alternate screen");
    let mut parser = vt100::Parser::new(24, 100, 0);
    parser.process(&printed.as_bytes()[leave..]);
    let document = parser.screen().contents();
    assert_eq!(
        document.matches("\u{203a} later thought").count(),
        1,
        "the message prints once, never doubled by the recall: {document}"
    );
    let member = document
        .find("\u{203a} later thought")
        .expect("member line");
    let reply = document.find("done").expect("the reply follows");
    assert!(member < reply, "{document}");
    let _ = std::fs::remove_dir_all(&home);
}

#[cfg(unix)]
#[test]
fn a_tty_send_settles_into_the_transcript_when_the_turn_absorbs_it() {
    let served = serve(vec![
        Exchange {
            delay_ms: 2500,
            status: 200,
            reply_lines: &["txt\tworking", "poll\t1"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["sent\tturn-1\t0\tarr-1"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["absorbed\tarr-1", "say\tdone", "exit\t0"],
        },
    ]);
    let home = scratch_home("tty-send");
    let mut session = run_client_on_pty(&served.url, &["go"], &home, Some(&served.url));
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the turn's own post reaches the gateway");
    std::thread::sleep(std::time::Duration::from_millis(300));
    session
        .keys
        .write_all(b"later thought\r")
        .expect("the mid-turn message reaches the pty");
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(15);
    while !session.ended() {
        assert!(std::time::Instant::now() < deadline, "client never exited");
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    let requests = served.gateway.requests();
    session.reaped();
    assert_eq!(requests.len(), 3, "{requests:?}");
    assert_eq!(
        requests[1].send_header.as_deref(),
        Some("1"),
        "{requests:?}"
    );
    assert_eq!(requests[1].body, "later thought");
    assert_eq!(
        requests[2].body, "",
        "the poll resumes the tail: {requests:?}"
    );
    let printed = session.painted();
    let member = printed
        .find("\u{203a} later thought")
        .expect("the absorbed message joins the transcript as the member's own");
    let reply = printed.find("done").expect("the reply follows");
    assert!(member < reply, "{printed}");
    let _ = std::fs::remove_dir_all(&home);
}

/// A turn's end arms the idle listen: the client reconnects empty on the interval carrying its
/// cursor, a quiet bounce leaves the prompt (and the session) standing, and the turn the
/// conversation wakes on prints without a keystroke.
#[cfg(unix)]
#[test]
fn an_idle_tty_listens_and_prints_the_turn_that_wakes_the_conversation() {
    let served = serve(vec![
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &[
                "txt\tanswer one",
                "ask\t>",
                "since\tturn-1\t5",
                "listen\t0.05",
            ],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["since\tturn-1\t5", "listen\t0.05"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\tthe job finished", "exit\t0"],
        },
    ]);
    let home = scratch_home("tty-listen");
    let mut session = run_client_on_pty(&served.url, &["go"], &home, Some(&served.url));
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(15);
    while !session.ended() {
        assert!(std::time::Instant::now() < deadline, "client never exited");
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    let requests = served.gateway.requests();
    session.reaped();
    assert_eq!(requests.len(), 3, "{requests:?}");
    assert_eq!(requests[0].body, "go");
    assert_eq!(
        requests[1].body, "",
        "a listen reconnect admits nothing: {requests:?}"
    );
    assert_eq!(
        requests[1].since_header.as_deref(),
        Some("turn-1:5"),
        "the bounce carries the cursor it was given: {requests:?}"
    );
    assert_eq!(
        requests[1].listen_header.as_deref(),
        Some("1"),
        "the bounce marks itself an idle listen: {requests:?}"
    );
    assert_eq!(requests[2].body, "");
    assert_eq!(requests[2].listen_header.as_deref(), Some("1"));
    let printed = session.painted();
    let answer = printed
        .find("answer one")
        .expect("the turn's answer prints");
    let woken = printed
        .find("the job finished")
        .expect("the wakeup prints with nobody typing");
    assert!(answer < woken, "{printed}");
    let _ = std::fs::remove_dir_all(&home);
}

/// Ctrl+B on a turn founded from the listening prompt stays a detach: the turn's later frames
/// must not re-open the presentation, and the member's next message takes the queueing lane that
/// rejoins the turn — never the send lane a working session uses.
#[cfg(unix)]
#[test]
fn a_detach_holds_while_the_listen_armed_turn_keeps_streaming() {
    let served = serve(vec![
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["ask\t>", "since\tturn-1\t1", "listen\t30"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["sent\tturn-2\t1\t"],
        },
        Exchange {
            delay_ms: 3000,
            status: 200,
            reply_lines: &["txt\tpartial", "poll\t9"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\tdone", "exit\t0"],
        },
    ]);
    let home = scratch_home("tty-detach-listen");
    let mut session = run_client_on_pty(&served.url, &["go"], &home, Some(&served.url));
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the opening turn posts");
    std::thread::sleep(std::time::Duration::from_millis(300));
    session
        .keys
        .write_all(b"next\r")
        .expect("the prompt message types");
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the send lane posts");
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the woken wire resumes the tail");
    std::thread::sleep(std::time::Duration::from_millis(300));
    session.keys.write_all(b"\x02").expect("Ctrl+B detaches");
    std::thread::sleep(std::time::Duration::from_millis(3300));
    session
        .keys
        .write_all(b"again\r")
        .expect("the post-detach message types");
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(15);
    while !session.ended() {
        assert!(std::time::Instant::now() < deadline, "client never exited");
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    let requests = served.gateway.requests();
    session.reaped();
    assert_eq!(requests.len(), 4, "{requests:?}");
    assert_eq!(requests[1].send_header.as_deref(), Some("1"));
    assert_eq!(requests[1].body, "next");
    assert_eq!(
        requests[3].send_header, None,
        "a detached member's message rejoins through the queue: {requests:?}"
    );
    assert_eq!(requests[3].body, "again");
    let _ = std::fs::remove_dir_all(&home);
}

/// A listen bounce that already streamed a woken turn's frames is that turn's live stream: when
/// it severs and the resume keeps failing, the reconnect ladder must end the wait with its error
/// rather than parking a working presentation forever.
#[cfg(unix)]
#[test]
fn a_severed_bounce_runs_the_reconnect_ladder_to_its_end() {
    let served = serve(vec![
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["ask\t>", "since\tturn-1\t1", "listen\t0.2"],
        },
        Exchange {
            delay_ms: 0,
            status: 599,
            reply_lines: &["txt\twoken"],
        },
        Exchange {
            delay_ms: 0,
            status: 500,
            reply_lines: &["boom"],
        },
        Exchange {
            delay_ms: 0,
            status: 500,
            reply_lines: &["boom"],
        },
        Exchange {
            delay_ms: 0,
            status: 500,
            reply_lines: &["boom"],
        },
    ]);
    let home = scratch_home("tty-severed-bounce");
    let mut session = run_client_on_pty(&served.url, &["go"], &home, Some(&served.url));
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(20);
    while !session.ended() {
        assert!(
            std::time::Instant::now() < deadline,
            "the failed resume parked instead of ending the session"
        );
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    let requests = served.gateway.requests();
    let status = session.reaped();
    assert!(!status.success(), "a dead link ends with the error, not 0");
    assert_eq!(requests.len(), 5, "{requests:?}");
    assert_eq!(requests[1].listen_header.as_deref(), Some("1"));
    assert_eq!(
        requests[2].listen_header, None,
        "the ladder's resume is no idle bounce: {requests:?}"
    );
    let _ = std::fs::remove_dir_all(&home);
}

/// An idle bounce that ends while the member is typing a credential must not reset the entry:
/// the value posted is everything they typed, on both sides of the bounce.
#[cfg(unix)]
#[test]
fn a_listen_bounce_does_not_clear_the_secret_being_typed() {
    let served = serve(vec![
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &[
                "secret\tsealed-1\tapi_key\tPaste the key",
                "ask\t>",
                "since\tturn-1\t3",
                "listen\t1",
            ],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["since\tturn-1\t3", "listen\t30"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\tstored api_key"],
        },
    ]);
    let home = scratch_home("tty-secret-listen");
    let mut session = run_client_on_pty(&served.url, &["go"], &home, Some(&served.url));
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the turn's own post reaches the gateway");
    std::thread::sleep(std::time::Duration::from_millis(300));
    session
        .keys
        .write_all(b"ab")
        .expect("the first half types before the bounce");
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the idle bounce reaches the gateway");
    std::thread::sleep(std::time::Duration::from_millis(300));
    session
        .keys
        .write_all(b"c\r")
        .expect("the second half types after the bounce");
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the secret posts");
    let _ = session.child.kill();
    let requests = served.gateway.requests();
    session.reaped();
    assert_eq!(requests.len(), 3, "{requests:?}");
    assert_eq!(requests[1].listen_header.as_deref(), Some("1"));
    assert_eq!(requests[2].slot_header.as_deref(), Some("api_key"));
    assert_eq!(
        requests[2].body, "abc",
        "the bounce must not clear what was already typed: {requests:?}"
    );
    let _ = std::fs::remove_dir_all(&home);
}

/// The signed-out client at the gateway's own prompts: the address types through untouched, one
/// Enter answers it, and the answered question leaves the composer while the turn runs.
#[cfg(unix)]
#[test]
fn the_sign_in_prompts_take_one_enter_and_list_no_paths() {
    let served = serve(vec![
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\tufo", "ask\tEnter your work email:"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &[
                "say\tWe emailed a code to member@metalcraft.ai",
                "ask\tEnter the code:",
            ],
        },
        Exchange {
            delay_ms: 2500,
            status: 200,
            reply_lines: &["say\tSigned in: member@metalcraft.ai", "ask\t>"],
        },
    ]);
    let home = scratch_home("signin");
    let mut session = run_client_on_pty(&served.url, &[], &home, None);
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the opening post reaches the gateway");
    thread::sleep(Duration::from_millis(800));

    session.press(b"member@metalcraft.ai\r");
    assert!(
        !session.painted().contains("Cargo.toml"),
        "the address is no path mention, so nothing lists the directory: {}",
        session.screen()
    );
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("one Enter answers the email prompt");

    thread::sleep(Duration::from_millis(800));
    session.press(b"123456\r");
    let entered = session.screen();
    assert!(
        !entered.contains("Enter the code:"),
        "the answered question leaves the composer while the code is in flight: {entered}"
    );

    let requests = served.gateway.requests();
    thread::sleep(Duration::from_millis(600));
    let signed_in = session.screen();
    let _ = session.child.kill();
    let _ = session.child.wait();
    assert_eq!(requests.len(), 3, "{requests:?}");
    assert_eq!(requests[1].body, "member@metalcraft.ai");
    assert_eq!(requests[2].body, "123456");
    assert!(
        signed_in.contains("Signed in: member@metalcraft.ai"),
        "the flow advances past the code prompt: {signed_in}"
    );
    let _ = std::fs::remove_dir_all(&home);
}

/// The path popup an `@` mention opens holds input, so it answers the keys that leave it: Enter
/// sends the entry when the filter matches nothing, and an interrupt ends the session.
#[cfg(unix)]
#[test]
fn the_path_popup_sends_on_enter_and_answers_an_interrupt() {
    let served = serve(vec![
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\thello", "ask\t>"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\tread it", "ask\t>"],
        },
    ]);
    let home = scratch_home("mention");
    let mut session = run_client_on_pty(&served.url, &["go"], &home, Some(&served.url));
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the opening post reaches the gateway");
    thread::sleep(Duration::from_millis(800));

    session.press(b"read @");
    let opened = session.screen();
    assert!(
        opened.contains("Cargo.toml"),
        "an `@` after a space still completes a path: {opened}"
    );

    session.press(b"zzzznothing\r");
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("Enter sends the entry the popup could not complete");

    thread::sleep(Duration::from_millis(800));
    session.press(b"say @");
    session.press(b"\x03");
    assert!(
        session.ended(),
        "Ctrl-C ends the session while the popup holds input"
    );

    let requests = served.gateway.requests();
    assert_eq!(requests[1].body, "read @zzzznothing", "{requests:?}");
    let _ = std::fs::remove_dir_all(&home);
}

/// A terminal that brackets a paste sends one paste event instead of the characters, so every
/// entry that holds input must take it: the masked entry a `secret` opens, and the composer under
/// the path popup.
#[cfg(unix)]
#[test]
fn a_bracketed_paste_reaches_the_masked_entry_and_the_path_popup() {
    let served = serve(vec![
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\thello", "secret\tsealed1\ts1\tPaste the key", "ask\t>"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\tstored", "ask\t>"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\tread it", "ask\t>"],
        },
    ]);
    let home = scratch_home("paste");
    let mut session = run_client_on_pty(&served.url, &["go"], &home, Some(&served.url));
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the opening post reaches the gateway");
    thread::sleep(Duration::from_millis(800));

    session.press(b"\x1b[200~sk-live-abc123\n\x1b[201~");
    let masked = session.screen();
    assert!(
        masked.contains(&"\u{2022}".repeat(14)),
        "the pasted key fills the masked entry, and its newline is no character: {masked}"
    );

    session.press(b"\r");
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the pasted key answers the secret prompt");
    thread::sleep(Duration::from_millis(800));

    session.press(b"read @");
    session.press(b"\x1b[200~Cargo.to\x1b[201~");
    session.press(b"\r");
    session.press(b"\r");
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the mention the pasted filter completed posts");

    let requests = served.gateway.requests();
    let _ = session.child.kill();
    let _ = session.child.wait();
    assert_eq!(
        requests[1].slot_header.as_deref(),
        Some("s1"),
        "{requests:?}"
    );
    assert_eq!(
        requests[1].body, "sk-live-abc123",
        "the masked entry posts the pasted key, never an empty secret: {requests:?}"
    );
    assert_eq!(
        requests[2].body, "read @Cargo.toml",
        "the paste filtered the popup, so Enter completed that path: {requests:?}"
    );
    let _ = std::fs::remove_dir_all(&home);
}

/// The turn's steps stand among the calls that made them while it runs, roll up behind one line
/// when the answer lands, and open again on the member's own key.
#[cfg(unix)]
#[test]
fn a_turns_thoughts_stand_among_its_calls_and_roll_up_on_the_answer() {
    let served = serve(vec![
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &[
                "txt\tReading the notes first.",
                "note\trunning read: the notes",
                "txt\tNow the calendar.",
                "note\trunning read: the calendar",
                "poll\t1",
            ],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["txt\tThe meeting is at four.", "ask\t>"],
        },
    ]);
    let home = scratch_home("tty-rollup");
    let mut session = run_client_on_pty(&served.url, &["go"], &home, Some(&served.url));
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the turn's own post reaches the gateway");
    thread::sleep(Duration::from_millis(800));

    let live = session.screen();
    let mut at = 0;
    for step in [
        "Reading the notes first.",
        "running read: the notes",
        "Now the calendar.",
        "running read: the calendar",
    ] {
        let found = live[at..]
            .find(step)
            .unwrap_or_else(|| panic!("the running turn shows {step} in its place: {live}"));
        at += found + step.len();
    }
    assert!(
        !live.contains("Completed"),
        "a running turn rolls nothing up: {live}"
    );

    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the poll reconnects for the answer");
    thread::sleep(Duration::from_millis(800));

    let settled = session.screen();
    assert!(
        settled.contains("Completed 4 steps \u{25b8}"),
        "the answer rolls two thoughts and two calls up: {settled}"
    );
    let line = settled.find("Completed 4 steps").expect("the rollup line");
    let answer = settled
        .find("The meeting is at four.")
        .expect("the answer stands under it");
    assert!(line < answer, "{settled}");
    assert!(
        !settled.contains("running read: the notes"),
        "the steps stand behind the line, not beside it: {settled}"
    );

    session.press(b"\x14");
    let opened = session.screen();
    assert!(
        opened.contains("Completed 4 steps \u{25be}"),
        "Ctrl+T opens the rollup: {opened}"
    );
    assert!(
        opened.contains("  the notes") && opened.contains("  the calendar"),
        "the opened rollup states each call's work, indented: {opened}"
    );

    session.press(b"\x03");
    assert!(session.ended(), "Ctrl+C ends the session");
    served.gateway.done();
    session.reaped();
    let _ = std::fs::remove_dir_all(&home);
}

/// A background run narrates while the parent writes its closing answer. The answer is the
/// reply — the run's row never takes it into the rollup — and the rollup line and the run's own
/// row open and close on a click, the run's rows standing behind its fold the way the web's do.
#[cfg(unix)]
#[test]
fn a_background_runs_call_leaves_the_answer_the_turn_already_wrote() {
    let served = serve(vec![Exchange {
        delay_ms: 0,
        status: 200,
        reply_lines: &[
            "note\trunning spawn: reviewer",
            "txt\tThe reviewer is on it.",
            "note\treviewer: running read: the diff",
            "note\treviewer: running bash: cargo test",
            "ask\t>",
        ],
    }]);
    let home = scratch_home("tty-background-run");
    let mut session = run_client_on_pty(&served.url, &["go"], &home, Some(&served.url));
    served
        .arrived
        .recv_timeout(ARRIVAL_WAIT)
        .expect("the turn's own post reaches the gateway");
    thread::sleep(Duration::from_millis(800));

    let settled = session.screen();
    assert!(
        settled.contains("Completed 2 steps \u{25b8}"),
        "the spawn and the run are the two steps: {settled}"
    );
    let line = settled.find("Completed 2 steps").expect("the rollup line");
    let answer = settled
        .find("The reviewer is on it.")
        .expect("the answer the turn streamed stays on the transcript");
    assert!(line < answer, "{settled}");

    let (row, col) = locate(&settled, "Completed 2 steps \u{25b8}");
    assert!(
        !cell_underlined(&session, row, col),
        "an unhovered rollup line carries no underline"
    );
    session.press(hover(row, col).as_bytes());
    assert!(
        cell_underlined(&session, row, col),
        "hovering the rollup line underlines it: {}",
        session.screen()
    );
    let (answer_row, answer_col) = locate(&settled, "The reviewer is on it.");
    session.press(hover(answer_row, answer_col).as_bytes());
    assert!(
        !cell_underlined(&session, row, col),
        "the affordance follows the pointer off the line"
    );

    session.press(click(row, col).as_bytes());
    let opened = session.screen();
    assert!(
        opened.contains("Completed 2 steps \u{25be}"),
        "a click opens the rollup: {opened}"
    );
    assert!(
        opened.lines().any(|line| line.trim_end() == "  reviewer")
            && opened.contains("reviewer \u{25b8}"),
        "the run stands as one closed row among the steps: {opened}"
    );
    assert!(
        !opened.contains("the diff"),
        "the run's rows stand behind its own fold: {opened}"
    );

    let (run_row, run_col) = locate(&opened, "reviewer \u{25b8}");
    session.press(click(run_row, run_col).as_bytes());
    let run_opened = session.screen();
    assert!(
        run_opened.contains("reviewer \u{25be}")
            && run_opened.contains("    the diff")
            && run_opened.contains("    cargo test"),
        "the run's row opens to everything it narrated: {run_opened}"
    );
    assert!(
        run_opened.contains("The reviewer is on it."),
        "the answer is no row of the run: {run_opened}"
    );

    session.press(click(run_row, run_col).as_bytes());
    assert!(
        !session.screen().contains("the diff"),
        "a second click closes the run: {}",
        session.screen()
    );
    session.press(click(row, col).as_bytes());
    let closed = session.screen();
    assert!(
        closed.contains("Completed 2 steps \u{25b8}") && !closed.contains("reviewer \u{25b8}"),
        "a click on the opened rollup line rolls the turn back up: {closed}"
    );

    session.press(b"\x03");
    assert!(session.ended(), "Ctrl+C ends the session");
    served.gateway.done();
    session.reaped();
    let _ = std::fs::remove_dir_all(&home);
}

/// The 1-based screen cell a line of text starts at, for aiming a mouse report.
#[cfg(unix)]
fn locate(screen: &str, needle: &str) -> (u16, u16) {
    for (row, line) in screen.lines().enumerate() {
        if let Some(byte) = line.find(needle) {
            let col = line[..byte].chars().count();
            return ((row + 1) as u16, (col + 1) as u16);
        }
    }
    panic!("{needle} is not on the screen: {screen}");
}

#[cfg(unix)]
fn click(row: u16, col: u16) -> String {
    format!("\x1b[<0;{col};{row}M\x1b[<0;{col};{row}m")
}

#[cfg(unix)]
fn hover(row: u16, col: u16) -> String {
    format!("\x1b[<35;{col};{row}M")
}

#[cfg(unix)]
fn cell_underlined(session: &OnPty, row: u16, col: u16) -> bool {
    let mut parser = vt100::Parser::new(24, 100, 0);
    parser.process(&session.painted.lock().unwrap());
    parser
        .screen()
        .cell(row - 1, col - 1)
        .is_some_and(|cell| cell.underline())
}

#[test]
fn a_piped_session_logs_every_step_and_counts_them() {
    let served = serve(vec![Exchange {
        delay_ms: 0,
        status: 200,
        reply_lines: &[
            "txt\tReading the notes first.\n",
            "note\trunning read: the notes",
            "note\tloading skill: office/pptx",
            "txt\tThe meeting is at four.\n",
            "exit\t0",
        ],
    }]);
    let home = scratch_home("plain-rollup");
    let (stdout, code) = run_client(&served.url, &["go"], "", &home);
    served.gateway.done();
    assert_eq!(code, 0, "stdout: {stdout}");
    let thought = stdout
        .find("Reading the notes first.")
        .expect("a piped session keeps the narration");
    let call = stdout.find("running read: the notes").expect("the call");
    let skill = stdout
        .find("loading skill: office/pptx")
        .expect("the skill");
    let answer = stdout.find("The meeting is at four.").expect("the answer");
    let rollup = stdout
        .find("Completed 3 steps")
        .expect("the turn's end counts the steps");
    assert!(
        thought < call && call < skill && skill < answer && answer < rollup,
        "a log stays in order and states the count at the end: {stdout}"
    );
    let _ = std::fs::remove_dir_all(&home);
}

/// A background run's call reaches a piped session after the parent wrote its answer. The answer
/// is the reply, so the count states the work and not the words.
#[test]
fn a_piped_session_counts_no_step_for_an_answer_a_run_narrated_over() {
    let served = serve(vec![Exchange {
        delay_ms: 0,
        status: 200,
        reply_lines: &[
            "note\trunning spawn: reviewer",
            "txt\tThe reviewer is on it.\n",
            "note\treviewer: running read: the diff",
            "exit\t0",
        ],
    }]);
    let home = scratch_home("plain-background-run");
    let (stdout, code) = run_client(&served.url, &["go"], "", &home);
    served.gateway.done();
    assert_eq!(code, 0, "stdout: {stdout}");
    assert!(
        stdout.contains("Completed 2 steps"),
        "the spawn and the run are the two steps: {stdout}"
    );
    let _ = std::fs::remove_dir_all(&home);
}

/// A run states every call it made, and counts as the one step the web counts it as.
#[test]
fn a_piped_session_counts_a_run_once_however_much_it_did() {
    let served = serve(vec![Exchange {
        delay_ms: 0,
        status: 200,
        reply_lines: &[
            "note\trunning spawn: reviewer",
            "note\treviewer: running read: the diff",
            "note\treviewer: running bash: cargo test",
            "txt\tThe reviewer found nothing.\n",
            "exit\t0",
        ],
    }]);
    let home = scratch_home("plain-run-rollup");
    let (stdout, code) = run_client(&served.url, &["go"], "", &home);
    served.gateway.done();
    assert_eq!(code, 0, "stdout: {stdout}");
    for step in [
        "running spawn: reviewer",
        "reviewer: running read: the diff",
        "reviewer: running bash: cargo test",
    ] {
        assert!(stdout.contains(step), "a log keeps {step}: {stdout}");
    }
    assert!(
        stdout.contains("Completed 2 steps"),
        "the spawn and the run it opened are two steps: {stdout}"
    );
    let _ = std::fs::remove_dir_all(&home);
}

#[cfg(unix)]
#[test]
fn a_terminal_that_answers_sets_the_scheme_and_takes_the_kitty_flags() {
    let served = serve(vec![Exchange {
        delay_ms: 400,
        status: 200,
        reply_lines: &["ask\t>"],
    }]);
    let home = scratch_home("probe-answers");
    let mut played = play_the_terminal(
        &served.url,
        &home,
        Terminal {
            replies: vec![KITTY_REPLY, LIGHT_REPLY, ATTRIBUTES_REPLY],
            after: Duration::from_millis(1),
            typeahead: "",
        },
    );
    played.framed(Duration::from_secs(10));
    let painted = played.painted();
    assert!(
        painted.contains(LIGHT_PROMPT) && !painted.contains(DARK_PROMPT),
        "the color reply picks the light palette:\n{}",
        played.screen()
    );
    assert!(painted.contains(FLAGS_PUSH), "the kitty flags are pushed");
    played.press(b"\x03");
    assert!(
        played.ended(Duration::from_secs(10)),
        "Ctrl+C ends the client"
    );
    assert!(
        played.painted().contains(FLAGS_POP),
        "the kitty flags are popped on the way out"
    );
    let _ = std::fs::remove_dir_all(&home);
}

#[cfg(unix)]
#[test]
fn a_terminal_that_answers_late_is_still_read() {
    let served = serve(vec![Exchange {
        delay_ms: 400,
        status: 200,
        reply_lines: &["ask\t>"],
    }]);
    let home = scratch_home("probe-late");
    let mut played = play_the_terminal(
        &served.url,
        &home,
        Terminal {
            replies: vec![LIGHT_REPLY, ATTRIBUTES_REPLY],
            after: Duration::from_millis(60),
            typeahead: "",
        },
    );
    played.framed(Duration::from_secs(10));
    let wait = played.probe_wait();
    assert!(
        wait >= Duration::from_millis(60) && wait < Duration::from_millis(900),
        "the read waited for the late answer and no longer: {wait:?}"
    );
    let painted = played.painted();
    assert!(
        painted.contains(LIGHT_PROMPT) && !painted.contains(DARK_PROMPT),
        "the late color reply picks the light palette:\n{}",
        played.screen()
    );
    assert!(
        !painted.contains(FLAGS_PUSH),
        "a terminal that named no flags gets none pushed"
    );
    played.press(b"\x03");
    assert!(
        played.ended(Duration::from_secs(10)),
        "Ctrl+C ends the client"
    );
    assert!(
        !played.painted().contains(FLAGS_POP),
        "flags that were never pushed are never popped"
    );
    let _ = std::fs::remove_dir_all(&home);
}

#[cfg(unix)]
#[test]
fn a_terminal_that_answers_nothing_pays_one_deadline() {
    let served = serve(vec![Exchange {
        delay_ms: 400,
        status: 200,
        reply_lines: &["ask\t>"],
    }]);
    let home = scratch_home("probe-mute");
    let mut played = play_the_terminal(
        &served.url,
        &home,
        Terminal {
            replies: Vec::new(),
            after: Duration::from_millis(1),
            typeahead: "",
        },
    );
    played.framed(Duration::from_secs(10));
    let wait = played.probe_wait();
    assert!(
        wait > Duration::from_millis(140) && wait < Duration::from_millis(900),
        "a mute terminal waits its deadline out, and no hardcoded two seconds: {wait:?}"
    );
    let painted = played.painted();
    assert!(
        painted.contains(DARK_PROMPT) && !painted.contains(LIGHT_PROMPT),
        "an unread background reads as dark:\n{}",
        played.screen()
    );
    assert!(!painted.contains(FLAGS_PUSH), "no reply, no flags");
    let _ = played.child.kill();
    let _ = played.child.wait();
    let _ = std::fs::remove_dir_all(&home);
}

#[cfg(unix)]
#[test]
fn typeahead_typed_into_the_probe_reaches_the_composer() {
    for (name, replies) in [
        (
            "probe-typed-answers",
            vec![KITTY_REPLY, LIGHT_REPLY, ATTRIBUTES_REPLY],
        ),
        ("probe-typed-mute", Vec::new()),
    ] {
        let served = serve(vec![Exchange {
            delay_ms: 400,
            status: 200,
            reply_lines: &["ask\t>"],
        }]);
        let home = scratch_home(name);
        let mut played = play_the_terminal(
            &served.url,
            &home,
            Terminal {
                replies,
                after: Duration::from_millis(1),
                typeahead: "hello",
            },
        );
        played.framed(Duration::from_secs(10));
        thread::sleep(Duration::from_millis(400));
        assert!(
            played.screen().contains("hello"),
            "{name}: what was typed during the probe reaches the composer:\n{}",
            played.screen()
        );
        let _ = played.child.kill();
        let _ = played.child.wait();
        let _ = std::fs::remove_dir_all(&home);
    }
}

#[cfg(unix)]
#[test]
fn a_replayed_enter_sends_what_was_typed_into_the_probe() {
    let served = serve(vec![
        Exchange {
            delay_ms: 400,
            status: 200,
            reply_lines: &["say\there", "ask\t>"],
        },
        Exchange {
            delay_ms: 0,
            status: 200,
            reply_lines: &["say\tgot it", "exit\t0"],
        },
    ]);
    let home = scratch_home("probe-typed-enter");
    let mut played = play_the_terminal(
        &served.url,
        &home,
        Terminal {
            replies: vec![KITTY_REPLY, LIGHT_REPLY, ATTRIBUTES_REPLY],
            after: Duration::from_millis(1),
            typeahead: "hi\r",
        },
    );
    played.framed(Duration::from_secs(10));
    assert!(played.ended(Duration::from_secs(20)), "the client exits");
    let requests = served.gateway.requests();
    assert_eq!(requests.len(), 2, "{requests:?}");
    assert_eq!(requests[0].body, "go");
    assert_eq!(
        requests[1].body, "hi",
        "the Enter typed into the probe sends its line: {requests:?}"
    );
    let _ = std::fs::remove_dir_all(&home);
}
