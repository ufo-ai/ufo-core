//! End-to-end sessions against a scripted gateway: real HTTP, the real binary, every mode.

use std::io::{Read, Write};
use std::net::TcpListener;
use std::process::{Command, Stdio};
#[cfg(unix)]
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
#[cfg(unix)]
use std::time::Duration;

struct Exchange {
    reply_lines: &'static [&'static str],
    delay_ms: u64,
    status: u16,
}

struct Served {
    url: String,
    handle: JoinHandle<Vec<Request>>,
    arrived: std::sync::mpsc::Receiver<()>,
}

#[derive(Debug)]
struct Request {
    body: String,
    op_header: Option<String>,
    slot_header: Option<String>,
    stop_header: Option<String>,
    send_header: Option<String>,
    send_id: Option<String>,
    unsend_header: Option<String>,
    timezone_header: Option<String>,
}

fn serve(script: Vec<Exchange>) -> Served {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    listener.set_nonblocking(true).expect("nonblocking");
    let url = format!("http://{}", listener.local_addr().unwrap());
    let (arrival, arrived) = std::sync::mpsc::channel();
    let handle = thread::spawn(move || {
        let mut seen = Vec::new();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(20);
        for exchange in script {
            let mut stream = loop {
                match listener.accept() {
                    Ok((stream, _)) => break stream,
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        if std::time::Instant::now() > deadline {
                            return seen;
                        }
                        thread::sleep(std::time::Duration::from_millis(10));
                    }
                    Err(error) => panic!("accept: {error}"),
                }
            };
            stream.set_nonblocking(false).expect("blocking stream");
            let request = read_request(&mut stream);
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
        seen
    });
    Served {
        url,
        handle,
        arrived,
    }
}

fn read_request(stream: &mut std::net::TcpStream) -> Request {
    let mut raw = Vec::new();
    let mut buffer = [0u8; 4096];
    let (
        headers_end,
        mut content_length,
        mut op_header,
        mut slot_header,
        mut stop_header,
        mut send_header,
        mut send_id,
        mut unsend_header,
        mut timezone_header,
    ) = loop {
        let read = stream.read(&mut buffer).expect("read");
        raw.extend_from_slice(&buffer[..read]);
        let Some(end) = raw.windows(4).position(|window| window == b"\r\n\r\n") else {
            continue;
        };
        let head = String::from_utf8_lossy(&raw[..end]).to_string();
        let mut length = 0usize;
        let mut op = None;
        let mut slot = None;
        let mut stop = None;
        let mut send = None;
        let mut send_key = None;
        let mut unsend = None;
        let mut timezone = None;
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
        }
        break (
            end + 4,
            length,
            op,
            slot,
            stop,
            send,
            send_key,
            unsend,
            timezone,
        );
    };
    while raw.len() < headers_end + content_length {
        let read = stream.read(&mut buffer).expect("read body");
        if read == 0 {
            break;
        }
        raw.extend_from_slice(&buffer[..read]);
    }
    let _ = &mut content_length;
    Request {
        body: String::from_utf8_lossy(&raw[headers_end..]).to_string(),
        op_header: op_header.take(),
        slot_header: slot_header.take(),
        stop_header: stop_header.take(),
        send_header: send_header.take(),
        send_id: send_id.take(),
        unsend_header: unsend_header.take(),
        timezone_header: timezone_header.take(),
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
        child.wait_with_output().expect("client exits")
    };
    (
        String::from_utf8_lossy(&output.stdout).to_string(),
        output.status.code().unwrap_or(-1),
    )
}

/// The client on a real terminal: the fullscreen loop only runs on a tty, and only a tty delivers
/// a key. The child is its own session, so the terminal queries it makes of `/dev/tty` reach
/// nothing and answer instantly.
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

/// `workspace` is the surface a signed-in client posts to; `None` leaves the client signed out, so
/// it drives the gateway's onboarding prompts instead.
#[cfg(unix)]
fn run_client_on_pty(
    url: &str,
    args: &[&str],
    home: &std::path::Path,
    workspace: Option<&str>,
) -> OnPty {
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

    let keys = unsafe { std::fs::File::from_raw_fd(leader) };
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
    let requests = served.handle.join().unwrap();
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
    let requests = served.handle.join().unwrap();
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
    let _ = served.handle.join().unwrap();
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
    let _ = served.handle.join().unwrap();
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
    let requests = served.handle.join().unwrap();
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
            arrived.recv().expect("the first post arrives");
            thread::sleep(std::time::Duration::from_millis(200));
            let _ = stdin.write_all(b"{\"type\":\"send\",\"text\":\"queued while the op ran\"}\n");
        });
        let output = child.wait_with_output().expect("client exits");
        writer.join().unwrap();
        (
            String::from_utf8_lossy(&output.stdout).to_string(),
            output.status.code().unwrap_or(-1),
        )
    };
    let requests = served.handle.join().unwrap();
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
            arrived.recv().expect("the first post arrives");
            thread::sleep(std::time::Duration::from_millis(200));
            let _ = stdin.write_all(b"{\"type\":\"send\",\"text\":\"while the turn ran\"}\n");
        });
        let output = child.wait_with_output().expect("client exits");
        writer.join().unwrap();
        (
            String::from_utf8_lossy(&output.stdout).to_string(),
            output.status.code().unwrap_or(-1),
        )
    };
    let requests = served.handle.join().unwrap();
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
        .recv_timeout(std::time::Duration::from_secs(15))
        .expect("the turn's own post reaches the gateway");
    session
        .keys
        .write_all(b"\x1b")
        .expect("Esc reaches the pty");
    let requests = served.handle.join().unwrap();
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
        .recv_timeout(std::time::Duration::from_secs(15))
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
    let requests = served.handle.join().unwrap();
    let _ = session.child.wait();
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
        .recv_timeout(std::time::Duration::from_secs(15))
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
    let requests = served.handle.join().unwrap();
    let _ = session.child.wait();
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
        .recv_timeout(std::time::Duration::from_secs(15))
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
    let _ = served.handle.join();
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
        .recv_timeout(std::time::Duration::from_secs(15))
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
    let _ = served.handle.join();
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
        .recv_timeout(std::time::Duration::from_secs(15))
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
    let requests = served.handle.join().unwrap();
    let _ = session.child.wait();
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
        .recv_timeout(std::time::Duration::from_secs(15))
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
    let requests = served.handle.join().unwrap();
    let _ = session.child.wait();
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
        .recv_timeout(std::time::Duration::from_secs(15))
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
    let _ = served.handle.join();
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
        .recv_timeout(std::time::Duration::from_secs(15))
        .expect("the first post reaches the gateway");
    thread::sleep(Duration::from_millis(800));
    session.keys.write_all(b"\x16").expect("Ctrl+V");
    session
        .keys
        .write_all(b"still alive\r")
        .expect("typing continues while the tool hangs");
    let requests = served.handle.join().unwrap();
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
        .recv_timeout(std::time::Duration::from_secs(15))
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
    let requests = served.handle.join().unwrap();
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
        .recv_timeout(std::time::Duration::from_secs(15))
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
    let _ = served.handle.join();
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
        .recv_timeout(std::time::Duration::from_secs(15))
        .expect("the turn's own post reaches the gateway");
    std::thread::sleep(std::time::Duration::from_millis(300));
    session
        .keys
        .write_all(b"later thought\r")
        .expect("the mid-turn message reaches the pty");
    served
        .arrived
        .recv_timeout(std::time::Duration::from_secs(15))
        .expect("the send reaches the gateway");
    std::thread::sleep(std::time::Duration::from_millis(400));
    session
        .keys
        .write_all(b"\x1b[A")
        .expect("Up reaches the pty");
    served
        .arrived
        .recv_timeout(std::time::Duration::from_secs(15))
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
    let requests = served.handle.join().unwrap();
    let _ = session.child.wait();
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
        .recv_timeout(std::time::Duration::from_secs(15))
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
    let requests = served.handle.join().unwrap();
    let _ = session.child.wait();
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
        .recv_timeout(Duration::from_secs(15))
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
        .recv_timeout(Duration::from_secs(15))
        .expect("one Enter answers the email prompt");

    thread::sleep(Duration::from_millis(800));
    session.press(b"123456\r");
    let entered = session.screen();
    assert!(
        !entered.contains("Enter the code:"),
        "the answered question leaves the composer while the code is in flight: {entered}"
    );

    let requests = served.handle.join().unwrap();
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
        .recv_timeout(Duration::from_secs(15))
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
        .recv_timeout(Duration::from_secs(15))
        .expect("Enter sends the entry the popup could not complete");

    thread::sleep(Duration::from_millis(800));
    session.press(b"say @");
    session.press(b"\x03");
    assert!(
        session.ended(),
        "Ctrl-C ends the session while the popup holds input"
    );

    let requests = served.handle.join().unwrap();
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
        .recv_timeout(Duration::from_secs(15))
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
        .recv_timeout(Duration::from_secs(15))
        .expect("the pasted key answers the secret prompt");
    thread::sleep(Duration::from_millis(800));

    session.press(b"read @");
    session.press(b"\x1b[200~Cargo.to\x1b[201~");
    session.press(b"\r");
    session.press(b"\r");
    served
        .arrived
        .recv_timeout(Duration::from_secs(15))
        .expect("the mention the pasted filter completed posts");

    let requests = served.handle.join().unwrap();
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
        .recv_timeout(Duration::from_secs(15))
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
        .recv_timeout(Duration::from_secs(15))
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
    let _ = served.handle.join();
    let _ = session.child.wait();
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
        .recv_timeout(Duration::from_secs(15))
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
    let _ = served.handle.join();
    let _ = session.child.wait();
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
    let _ = served.handle.join().unwrap();
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
    let _ = served.handle.join().unwrap();
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
    let _ = served.handle.join().unwrap();
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
