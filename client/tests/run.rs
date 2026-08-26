use std::fs;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{mpsc, Arc};
use std::thread;
use std::time::{Duration, Instant};

fn binary() -> &'static str {
    env!("CARGO_BIN_EXE_ufo")
}

fn scratch(name: &str) -> PathBuf {
    let path = std::env::temp_dir().join(format!("ufo-run-{name}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&path);
    fs::create_dir_all(&path).unwrap();
    path
}

fn suffixed(base: &Path, suffix: &str) -> PathBuf {
    PathBuf::from(format!("{}.{suffix}", base.display()))
}

#[test]
fn direct_run_returns_the_commands_output_exit_and_loopback_proxy() {
    let output = Command::new(binary())
        .args([
            "run",
            "--",
            "sh",
            "-c",
            "printf '%s\\n' \"$HTTP_PROXY\" \"$HTTPS_PROXY\" \"$http_proxy\" \"$https_proxy\"; exit 7",
        ])
        .env("HTTP_PROXY", "https://turn:ufo@proxy.invalid:8443")
        .env("HTTPS_PROXY", "https://turn:ufo@proxy.invalid:8443")
        .env("http_proxy", "https://turn:ufo@proxy.invalid:8443")
        .env("https_proxy", "https://turn:ufo@proxy.invalid:8443")
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(7));
    let stdout = String::from_utf8(output.stdout).unwrap();
    let proxies: Vec<_> = stdout.lines().collect();
    assert_eq!(proxies.len(), 4);
    assert!(proxies
        .iter()
        .all(|proxy| proxy.starts_with("http://turn:ufo@127.0.0.1:")));
    assert!(proxies.windows(2).all(|pair| pair[0] == pair[1]));
}

#[test]
fn urllib_reaches_a_tls_proxy_through_run() {
    let root = scratch("urllib-proxy");
    let rcgen::CertifiedKey { cert, key_pair } =
        rcgen::generate_simple_self_signed(vec!["127.0.0.1".to_string()]).unwrap();
    let ca = root.join("ca.pem");
    fs::write(&ca, cert.pem()).unwrap();
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let key = rustls::pki_types::PrivatePkcs8KeyDer::from(key_pair.serialize_der());
    let server_config = rustls::ServerConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_no_client_auth()
        .with_single_cert(vec![cert.der().clone()], key.into())
        .unwrap();
    let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
    let port = listener.local_addr().unwrap().port();
    let (sent, received) = mpsc::channel();
    thread::spawn(move || {
        let (socket, _) = listener.accept().unwrap();
        socket
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let connection = rustls::ServerConnection::new(Arc::new(server_config)).unwrap();
        let mut stream = rustls::StreamOwned::new(connection, socket);
        let mut request = Vec::new();
        let mut byte = [0u8; 1];
        while !request.ends_with(b"\r\n\r\n") {
            stream.read_exact(&mut byte).unwrap();
            request.push(byte[0]);
        }
        sent.send(request).unwrap();
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            .unwrap();
        stream.flush().unwrap();
    });
    let proxy = format!("https://run-token:ufo@127.0.0.1:{port}");
    let output = Command::new(binary())
        .args([
            "run",
            "--",
            "python3",
            "-c",
            "import urllib.request; print(urllib.request.urlopen('http://packages.example/file', timeout=5).read().decode())",
        ])
        .env_clear()
        .env("PATH", std::env::var("PATH").unwrap())
        .env("HTTP_PROXY", &proxy)
        .env("http_proxy", &proxy)
        .env("NO_PROXY", "")
        .env("no_proxy", "")
        .env("SSL_CERT_FILE", &ca)
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(0));
    assert_eq!(
        output.stdout,
        b"ok\n",
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let request = received.recv_timeout(Duration::from_secs(5)).unwrap();
    assert!(request.starts_with(b"GET http://packages.example/file HTTP/1.1\r\n"));
    assert!(request
        .windows(b"Proxy-Authorization: Basic cnVuLXRva2VuOnVmbw==\r\n".len())
        .any(|window| window == b"Proxy-Authorization: Basic cnVuLXRva2VuOnVmbw==\r\n"));
}

#[test]
fn a_task_is_journaled_and_reattached_without_running_twice() {
    let root = scratch("reattach");
    let base = root.join("tasks/task-a");
    let marker = root.join("marker");
    let command = format!(
        "printf x >> '{}'; printf task-output; exit 9",
        marker.display()
    );

    let first = Command::new(binary())
        .args([
            "run",
            "--task",
            base.to_str().unwrap(),
            "--",
            "bash",
            "-lc",
            &command,
        ])
        .output()
        .unwrap();
    let second = Command::new(binary())
        .args([
            "run",
            "--task",
            base.to_str().unwrap(),
            "--",
            "bash",
            "-lc",
            "printf should-not-run",
        ])
        .output()
        .unwrap();

    assert_eq!(first.status.code(), Some(9));
    assert_eq!(second.status.code(), Some(9));
    assert_eq!(first.stdout, b"task-output");
    assert_eq!(second.stdout, b"task-output");
    assert_eq!(fs::read(marker).unwrap(), b"x");
    assert_eq!(fs::read_to_string(suffixed(&base, "exit")).unwrap(), "9");
    assert!(!fs::read_to_string(suffixed(&base, "pid"))
        .unwrap()
        .trim()
        .is_empty());
}

#[test]
fn an_abandoned_task_lock_is_reclaimed() {
    let root = scratch("abandoned-lock");
    let base = root.join("tasks/task-a");
    fs::create_dir_all(base.parent().unwrap()).unwrap();
    fs::write(suffixed(&base, "lock"), i32::MAX.to_string()).unwrap();

    let output = Command::new(binary())
        .args([
            "run",
            "--task",
            base.to_str().unwrap(),
            "--",
            "sh",
            "-c",
            "printf recovered",
        ])
        .output()
        .unwrap();

    assert!(output.status.success());
    assert_eq!(output.stdout, b"recovered");
}

#[test]
fn a_task_journals_a_command_that_cannot_start() {
    let root = scratch("missing-command");
    let base = root.join("tasks/task-a");

    let output = Command::new(binary())
        .args([
            "run",
            "--task",
            base.to_str().unwrap(),
            "--",
            "ufo-command-that-does-not-exist",
        ])
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(127));
    assert!(String::from_utf8_lossy(&output.stdout).contains("command not found"));
    assert_eq!(fs::read_to_string(suffixed(&base, "exit")).unwrap(), "127");
}

#[cfg(unix)]
#[test]
fn a_detached_task_stops_through_its_supervisor_and_writes_exit() {
    let root = scratch("stop");
    let base = root.join("tasks/task-b");
    let output = Command::new(binary())
        .args([
            "run",
            "--task",
            base.to_str().unwrap(),
            "--detach",
            "--",
            "bash",
            "-lc",
            "while :; do sleep 1; done",
        ])
        .output()
        .unwrap();
    assert!(output.status.success());
    let pid = String::from_utf8(output.stdout)
        .unwrap()
        .trim()
        .parse::<i32>()
        .unwrap();

    assert_eq!(unsafe { libc::kill(pid, libc::SIGTERM) }, 0);
    let exit = suffixed(&base, "exit");
    let deadline = Instant::now() + Duration::from_secs(10);
    while !exit.exists() && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(20));
    }
    assert!(exit.exists());
    assert_ne!(fs::read_to_string(exit).unwrap(), "0");
}
