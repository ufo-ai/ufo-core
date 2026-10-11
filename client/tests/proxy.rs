use std::fs;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::{mpsc, Arc};
use std::thread;
use std::time::Duration;

const TOKEN: &str = "c2Vzc2lvbg.c2lnbmF0dXJl";
const CONNECT_HEAD: &[u8] =
    b"CONNECT example.com:443 HTTP/1.1\r\nProxy-Authorization: Basic YzJWemMybHZiZy5jMmxuYm1GMGRYSmw6dWZv\r\n\r\n";
const REFUSAL: &str = "The session token names no session.";

struct StandIn {
    port: u16,
    ca: PathBuf,
    connects: mpsc::Receiver<Vec<u8>>,
}

fn binary() -> &'static str {
    env!("CARGO_BIN_EXE_ufo")
}

fn scratch(name: &str) -> PathBuf {
    let path = std::env::temp_dir().join(format!("ufo-proxy-{name}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&path);
    fs::create_dir_all(&path).unwrap();
    path
}

fn stand_in(root: &Path, refuse: bool) -> StandIn {
    let rcgen::CertifiedKey { cert, key_pair } =
        rcgen::generate_simple_self_signed(vec!["127.0.0.1".to_string()]).unwrap();
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let key = rustls::pki_types::PrivatePkcs8KeyDer::from(key_pair.serialize_der());
    let config = Arc::new(
        rustls::ServerConfig::builder_with_provider(provider)
            .with_safe_default_protocol_versions()
            .unwrap()
            .with_no_client_auth()
            .with_single_cert(vec![cert.der().clone()], key.into())
            .unwrap(),
    );
    let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
    let port = listener.local_addr().unwrap().port();
    let ca = root.join(format!("stand-in-ca-{port}.pem"));
    fs::write(&ca, cert.pem()).unwrap();
    let ca_pem = cert.pem();
    let (sent, connects) = mpsc::channel();
    thread::spawn(move || {
        for socket in listener.incoming().flatten() {
            let config = config.clone();
            let sent = sent.clone();
            let ca_pem = ca_pem.clone();
            thread::spawn(move || answer(socket, config, &sent, &ca_pem, refuse));
        }
    });
    StandIn { port, ca, connects }
}

fn answer(
    socket: TcpStream,
    config: Arc<rustls::ServerConfig>,
    sent: &mpsc::Sender<Vec<u8>>,
    ca_pem: &str,
    refuse: bool,
) {
    socket
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let connection = rustls::ServerConnection::new(config).unwrap();
    let mut stream = rustls::StreamOwned::new(connection, socket);
    let mut head = Vec::new();
    let mut byte = [0u8; 1];
    while !head.ends_with(b"\r\n\r\n") {
        if stream.read_exact(&mut byte).is_err() {
            return;
        }
        head.push(byte[0]);
    }
    let text = String::from_utf8_lossy(&head).into_owned();
    if text.starts_with("CONNECT ") {
        sent.send(head).unwrap();
        stream
            .write_all(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            .unwrap();
        stream.flush().unwrap();
        return;
    }
    let bearer = format!("authorization: bearer {}\r\n", TOKEN.to_ascii_lowercase());
    let (status, body) = if text.starts_with("GET /v1/proxy/ca ") {
        ("200 OK", serde_json::json!({ "ca_pem": ca_pem }))
    } else if text.starts_with("GET /v1/sessions/self ")
        && !refuse
        && text.to_ascii_lowercase().contains(&bearer)
    {
        (
            "200 OK",
            serde_json::json!({
                "id": "3e6f1a2b-7c8d-4e9f-a0b1-c2d3e4f5a6b7",
                "proxy_url": "https://proxy.test",
                "env": {
                    "HTTPS_PROXY": format!("https://{TOKEN}:ufo@proxy.test"),
                    "HTTP_PROXY": format!("https://{TOKEN}:ufo@proxy.test"),
                    "https_proxy": format!("https://{TOKEN}:ufo@proxy.test"),
                    "http_proxy": format!("https://{TOKEN}:ufo@proxy.test"),
                    "NO_PROXY": "localhost,127.0.0.1,::1",
                    "no_proxy": "localhost,127.0.0.1,::1",
                    "GH_TOKEN": "ufo-sentinel-x"
                }
            }),
        )
    } else {
        (
            "401 Unauthorized",
            serde_json::json!({ "error": { "code": "unauthorized", "message": REFUSAL } }),
        )
    };
    let body = body.to_string();
    let response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    stream.write_all(response.as_bytes()).unwrap();
    stream.flush().unwrap();
}

fn ufo_proxy(home: &Path, stand_in: &StandIn, args: &[&str]) -> Output {
    Command::new(binary())
        .arg("proxy")
        .args(args)
        .env_clear()
        .env("UFO_HOME", home)
        .env(
            "UFO_PROXY_URL",
            format!("https://127.0.0.1:{}", stand_in.port),
        )
        .env("SSL_CERT_FILE", &stand_in.ca)
        .output()
        .unwrap()
}

fn relayed(port: u16, stand_in: &StandIn) {
    let mut local = TcpStream::connect(("127.0.0.1", port)).unwrap();
    local
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    local.write_all(CONNECT_HEAD).unwrap();
    let mut answer = [0u8; 12];
    local.read_exact(&mut answer).unwrap();
    assert_eq!(&answer, b"HTTP/1.1 200");
    assert_eq!(
        stand_in
            .connects
            .recv_timeout(Duration::from_secs(5))
            .unwrap(),
        CONNECT_HEAD
    );
}

fn printed_port(output: &Output) -> u16 {
    let stdout = String::from_utf8(output.stdout.clone()).unwrap();
    let prefix = format!("HTTPS_PROXY=http://{TOKEN}:ufo@127.0.0.1:");
    stdout
        .lines()
        .find_map(|line| line.strip_prefix(&prefix))
        .unwrap_or_else(|| panic!("{stdout}"))
        .parse()
        .unwrap()
}

#[test]
fn start_prints_the_environment_and_leaves_a_daemon_that_relays_connect() {
    let root = scratch("start");
    let home = root.join("home");
    let stand_in = stand_in(&root, false);

    let output = ufo_proxy(&home, &stand_in, &["--session", TOKEN]);

    assert_eq!(
        output.status.code(),
        Some(0),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let port = printed_port(&output);
    let bundle = home.join("proxy/ca.pem");
    let stdout = String::from_utf8(output.stdout.clone()).unwrap();
    assert!(stdout.contains("\nGH_TOKEN=ufo-sentinel-x\n"), "{stdout}");
    assert!(stdout.contains(&format!("\nSSL_CERT_FILE={}\n", bundle.display())));
    assert!(fs::read_to_string(&bundle)
        .unwrap()
        .ends_with(&fs::read_to_string(&stand_in.ca).unwrap()));
    relayed(port, &stand_in);
    assert!(ufo_proxy(&home, &stand_in, &["--stop"]).status.success());
}

#[test]
fn a_second_start_reuses_the_daemon() {
    let root = scratch("reuse");
    let home = root.join("home");
    let stand_in = stand_in(&root, false);

    let first = printed_port(&ufo_proxy(&home, &stand_in, &["--session", TOKEN]));
    let second = ufo_proxy(&home, &stand_in, &["--session", TOKEN, "--env"]);

    let stdout = String::from_utf8(second.stdout.clone()).unwrap();
    assert!(
        stdout.starts_with(&format!(
            "export HTTPS_PROXY='http://{TOKEN}:ufo@127.0.0.1:{first}'\n"
        )),
        "{stdout}"
    );
    assert!(ufo_proxy(&home, &stand_in, &["--stop"]).status.success());
}

#[test]
fn a_start_against_another_proxy_service_relays_there() {
    let root = scratch("switch");
    let home = root.join("home");
    let first = stand_in(&root, false);
    let second = stand_in(&root, false);
    let old = printed_port(&ufo_proxy(&home, &first, &["--session", TOKEN]));

    let output = ufo_proxy(&home, &second, &["--session", TOKEN]);

    assert_eq!(
        output.status.code(),
        Some(0),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(TcpStream::connect(("127.0.0.1", old)).is_err());
    relayed(printed_port(&output), &second);
    assert!(first.connects.try_recv().is_err());
    assert!(ufo_proxy(&home, &second, &["--stop"]).status.success());
}

#[test]
fn stop_ends_the_daemon() {
    let root = scratch("stop");
    let home = root.join("home");
    let stand_in = stand_in(&root, false);
    let port = printed_port(&ufo_proxy(&home, &stand_in, &["--session", TOKEN]));

    let stopped = ufo_proxy(&home, &stand_in, &["--stop"]);

    assert!(
        stopped.status.success(),
        "{}",
        String::from_utf8_lossy(&stopped.stderr)
    );
    assert!(TcpStream::connect(("127.0.0.1", port)).is_err());
    assert!(!home.join("proxy/daemon.pid").exists());
    assert!(!home.join("proxy/daemon.port").exists());
    assert!(!home.join("proxy/daemon.upstream").exists());
}

#[test]
fn a_refused_token_prints_the_proxys_sentence() {
    let root = scratch("refused");
    let home = root.join("home");
    let stand_in = stand_in(&root, true);

    let output = ufo_proxy(&home, &stand_in, &["--session", TOKEN]);

    assert_eq!(output.status.code(), Some(1));
    assert!(output.stdout.is_empty());
    assert_eq!(
        String::from_utf8(output.stderr).unwrap(),
        format!("ufo proxy: the proxy service answered 401: {REFUSAL}\n")
    );
    assert!(!home.join("proxy/daemon.pid").exists());
}
