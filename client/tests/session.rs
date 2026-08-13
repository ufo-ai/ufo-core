//! End-to-end sessions against a scripted gateway: real HTTP, the real binary, every mode.

use std::io::{Read, Write};
use std::net::TcpListener;
use std::process::{Command, Stdio};
use std::thread::{self, JoinHandle};

struct Exchange {
    reply_lines: &'static [&'static str],
    delay_ms: u64,
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
            let response = format!(
                "HTTP/1.1 200 OK\r\ncontent-type: text/plain\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
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
    let (headers_end, mut content_length, mut op_header, mut slot_header, mut stop_header) = loop {
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
        }
        break (end + 4, length, op, slot, stop);
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
}

#[cfg(unix)]
fn run_client_on_pty(url: &str, args: &[&str], home: &std::path::Path) -> OnPty {
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
    command
        .args(args)
        .env("WORKSPACE_URL", url)
        .env("UFO_URL", url)
        .env("UFO_HOME", home)
        .env("UFO_CHANNEL", "e2e-tty")
        .env("TERM", "xterm-256color")
        .env("TMPDIR", &scratch_tmp)
        .env_remove("NO_COLOR")
        .env_remove("UFO_PLAIN");
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
    let mut painted = keys.try_clone().expect("the leader duplicates");
    thread::spawn(move || {
        let mut buffer = [0u8; 8192];
        while painted.read(&mut buffer).is_ok_and(|read| read > 0) {}
    });
    OnPty { keys, child }
}

fn scratch_home(name: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("ufo-e2e-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("credentials"), "test-token").unwrap();
    dir
}

#[test]
fn plain_session_round_trips_ask_and_exit() {
    let served = serve(vec![
        Exchange {
            delay_ms: 0,
            reply_lines: &["say\thello there", "ask\t>"],
        },
        Exchange {
            delay_ms: 0,
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
            reply_lines: &["run\top-1\texec\texec\t30\t\t{\"argv\":[\"echo\",\"proof\"]}"],
        },
        Exchange {
            delay_ms: 0,
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
        reply_lines: &["txt\tpartial ", "txt\treply", "say\twhole", "exit\t0"],
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
    let _ = std::fs::remove_dir_all(&home);
}

#[test]
fn resume_replays_history_before_the_tail() {
    let served = serve(vec![Exchange {
        delay_ms: 0,
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
            reply_lines: &[
                "say\thello",
                "secret\tsealed1\ts1\tFirst secret",
                "secret\tsealed2\ts2\tSecond secret",
                "ask\t>",
            ],
        },
        Exchange {
            delay_ms: 300,
            reply_lines: &["say\tstored"],
        },
        Exchange {
            delay_ms: 0,
            reply_lines: &["say\tstored"],
        },
        Exchange {
            delay_ms: 0,
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
            reply_lines: &["run\top-slow\texec\texec\t30\t\t{\"argv\":[\"sleep\",\"1\"]}"],
        },
        Exchange {
            delay_ms: 0,
            reply_lines: &["say\tok", "ask\t>"],
        },
        Exchange {
            delay_ms: 0,
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
        let writer = thread::spawn(move || {
            thread::sleep(std::time::Duration::from_millis(500));
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
    assert_eq!(
        requests[1].op_header.as_deref(),
        Some("op-slow"),
        "the turn is blocked on the op reply, so it posts first: {requests:?}"
    );
    assert_eq!(requests[2].body, "queued while the op ran");
}

#[test]
fn a_message_that_supersedes_a_poll_clears_it() {
    let served = serve(vec![
        Exchange {
            delay_ms: 700,
            reply_lines: &["say\tfirst", "poll\t1"],
        },
        Exchange {
            delay_ms: 0,
            reply_lines: &["say\tsecond", "ask\t>"],
        },
    ]);
    let home = scratch_home("poll-supersede");
    let (stdout, code) = run_client(
        &served.url,
        &["--json", "go"],
        "{\"type\":\"send\",\"text\":\"while polling\"}\n",
        &home,
    );
    let requests = served.handle.join().unwrap();
    assert_eq!(code, 0, "stdout: {stdout}");
    let seconds = stdout
        .lines()
        .filter(|line| line.contains("\"type\":\"message\"") && line.contains("second"))
        .count();
    assert_eq!(
        seconds, 1,
        "the reply prints once, never re-tailed: {stdout}"
    );
    assert_eq!(
        requests.len(),
        2,
        "no stale poll posts a third time: {requests:?}"
    );
    assert_eq!(requests[1].body, "while polling");
}

#[cfg(unix)]
#[test]
fn esc_on_a_running_turn_posts_the_stop() {
    let served = serve(vec![
        Exchange {
            delay_ms: 2000,
            reply_lines: &["txt\tthinking", "ask\t>"],
        },
        Exchange {
            delay_ms: 0,
            reply_lines: &["say\tcancelled", "ask\t>"],
        },
    ]);
    let home = scratch_home("stop");
    let mut session = run_client_on_pty(&served.url, &["go"], &home);
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
