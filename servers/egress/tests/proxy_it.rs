//! End-to-end wire tests for the egress proxy. Each spins a fake control RPC server and (where the
//! path needs one) a local origin, all on 127.0.0.1 with no Postgres, then drives a real CONNECT
//! through `EgressProxy` and asserts the refusal code, the tunnelled/MITM'd bytes, the injected
//! secret, and the metering the proxy posts back.

use std::collections::HashMap;
use std::net::{Ipv4Addr, SocketAddr};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use base64::Engine;
use rustls::pki_types::ServerName;
use rustls::{ClientConfig, RootCertStore};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio_rustls::{TlsAcceptor, TlsConnector};
use uuid::Uuid;

use ufo_egress::control::Control;
use ufo_egress::meter::Meter;
use ufo_egress::server::{Dns, EgressProxy, ServiceDaemons};
use ufo_egress::tls::{generate_ca, LeafStore};
use ufo_egress::token::RunTokenCodec;
use ufo_egress::types::{MeterRecord, RunToken};

const SECRET: &[u8] = b"proxy-it-signing-secret";
const WORKSPACE: u128 = 0x1111;
const TURN: u128 = 0x2222;
/// An address the DNS pin vets as globally routable that no test origin serves, so a test can tell
/// the pinned address from the private one the same name answers. No test waits on that connect.
const VETTED_PUBLIC_ADDRESS: Ipv4Addr = Ipv4Addr::new(93, 184, 216, 34);

// --- fake control plane -----------------------------------------------------------------------

struct ControlState {
    authorized: bool,
    authorize_status: u16,
    generation: i64,
    rules_json: String,
    bridge_json: String,
    bridge_requests: Mutex<Vec<serde_json::Value>>,
    meter_records: Mutex<Vec<serde_json::Value>>,
}

impl ControlState {
    fn new(rules_json: &str) -> Arc<ControlState> {
        Arc::new(ControlState {
            authorized: true,
            authorize_status: 200,
            generation: 0,
            rules_json: rules_json.to_string(),
            bridge_json: String::new(),
            bridge_requests: Mutex::new(Vec::new()),
            meter_records: Mutex::new(Vec::new()),
        })
    }
}

async fn spawn_control(state: Arc<ControlState>) -> String {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        loop {
            let (mut sock, _) = match listener.accept().await {
                Ok(pair) => pair,
                Err(_) => break,
            };
            let state = state.clone();
            tokio::spawn(async move {
                let (path, body) = match read_message(&mut sock).await {
                    Some(msg) => msg,
                    None => return,
                };
                let (status, payload) = if path.ends_with("/authorize") {
                    if state.authorize_status != 200 {
                        (state.authorize_status, "{}".to_string())
                    } else {
                        (
                            200,
                            format!(
                                "{{\"authorized\":{},\"generation\":{}}}",
                                state.authorized, state.generation
                            ),
                        )
                    }
                } else if path.ends_with("/resolve") {
                    (200, format!("{{\"rules\":{}}}", state.rules_json))
                } else if path.ends_with("/meter") {
                    if let Ok(value) = serde_json::from_slice::<serde_json::Value>(&body) {
                        if let Some(records) = value.get("records").and_then(|r| r.as_array()) {
                            state
                                .meter_records
                                .lock()
                                .unwrap()
                                .extend(records.iter().cloned());
                        }
                    }
                    (200, "{}".to_string())
                } else if path.ends_with("/tool-bridge") {
                    if let Ok(value) = serde_json::from_slice::<serde_json::Value>(&body) {
                        state.bridge_requests.lock().unwrap().push(value);
                    }
                    (200, state.bridge_json.clone())
                } else {
                    (404, "{}".to_string())
                };
                let response = format!(
                    "HTTP/1.1 {status} X\r\ncontent-type: application/json\r\ncontent-length: {}\r\n\
                     connection: close\r\n\r\n{payload}",
                    payload.len()
                );
                let _ = sock.write_all(response.as_bytes()).await;
                let _ = sock.shutdown().await;
            });
        }
    });
    format!("http://{addr}")
}

async fn read_message(sock: &mut TcpStream) -> Option<(String, Vec<u8>)> {
    let mut buf = Vec::new();
    let mut chunk = [0u8; 4096];
    let head_end = loop {
        if let Some(pos) = find(&buf, b"\r\n\r\n") {
            break pos;
        }
        let n = sock.read(&mut chunk).await.ok()?;
        if n == 0 {
            return None;
        }
        buf.extend_from_slice(&chunk[..n]);
    };
    let head = String::from_utf8_lossy(&buf[..head_end]).to_string();
    let path = head.lines().next()?.split_whitespace().nth(1)?.to_string();
    let content_length = head
        .lines()
        .find_map(|line| {
            let (name, value) = line.split_once(':')?;
            name.trim()
                .eq_ignore_ascii_case("content-length")
                .then(|| value.trim().parse::<usize>().ok())
                .flatten()
        })
        .unwrap_or(0);
    let mut body = buf[head_end + 4..].to_vec();
    while body.len() < content_length {
        let n = sock.read(&mut chunk).await.ok()?;
        if n == 0 {
            break;
        }
        body.extend_from_slice(&chunk[..n]);
    }
    Some((path, body))
}

// --- proxy under test -------------------------------------------------------------------------

struct Proxy {
    addr: SocketAddr,
    ca_pem: String,
    control: Arc<ControlState>,
}

async fn start_proxy(state: Arc<ControlState>) -> Proxy {
    start_proxy_trusting(state, None, ServiceDaemons::default()).await
}

async fn start_proxy_trusting(
    state: Arc<ControlState>,
    upstream_ca_pem: Option<String>,
    daemons: ServiceDaemons,
) -> Proxy {
    start_proxy_pinning(state, upstream_ca_pem, daemons, None).await
}

/// `pinned` answers the DNS pin from a fixed map instead of the box's resolver, so a name can
/// answer one address to the pin and another to the system resolver a connect-by-name would use.
async fn start_proxy_pinning(
    state: Arc<ControlState>,
    upstream_ca_pem: Option<String>,
    daemons: ServiceDaemons,
    pinned: Option<(&str, Ipv4Addr)>,
) -> Proxy {
    let control_url = spawn_control(state.clone()).await;
    let (ca_pem, ca_key) = generate_ca().unwrap();
    let leaves = Arc::new(LeafStore::new(&ca_pem, &ca_key).unwrap());
    let control = Arc::new(Control::new(&control_url, "control-token"));
    let meter = Meter::start(control.clone());
    let mut proxy = EgressProxy::new(
        control,
        leaves,
        meter.sink(),
        SECRET.to_vec(),
        daemons,
        None,
        Duration::from_secs(10),
    );
    if let Some(pem) = upstream_ca_pem {
        proxy = proxy.trust_upstream(client_config_trusting(&pem));
    }
    if let Some((host, address)) = pinned {
        proxy = proxy.resolve_with(Dns::Fixed(HashMap::from([(
            host.to_string(),
            vec![address],
        )])));
    }
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let proxy = Arc::new(proxy);
    tokio::spawn(async move {
        let _ = proxy
            .serve_listener(listener, std::future::pending::<()>())
            .await;
    });
    Proxy {
        addr,
        ca_pem,
        control: state,
    }
}

// --- CONNECT client ---------------------------------------------------------------------------

fn run_token(member: Option<Uuid>) -> String {
    RunTokenCodec {
        secret: SECRET.to_vec(),
    }
    .encode(&RunToken {
        workspace_id: Uuid::from_u128(WORKSPACE),
        turn_id: Uuid::from_u128(TURN),
        acting_member_id: member,
    })
}

fn basic(token: &str) -> String {
    format!(
        "Basic {}",
        base64::engine::general_purpose::STANDARD.encode(format!("{token}:"))
    )
}

/// Open a CONNECT to `target` through the proxy and return the socket plus the response head.
async fn connect(proxy: &Proxy, target: &str, auth: Option<&str>) -> (TcpStream, String) {
    let mut sock = TcpStream::connect(proxy.addr).await.unwrap();
    let mut request = format!("CONNECT {target} HTTP/1.1\r\nhost: {target}\r\n");
    if let Some(header) = auth {
        request.push_str(&format!("proxy-authorization: {header}\r\n"));
    }
    request.push_str("\r\n");
    sock.write_all(request.as_bytes()).await.unwrap();
    let head = read_head_text(&mut sock).await;
    (sock, head)
}

async fn read_head_text(sock: &mut TcpStream) -> String {
    let mut buf = Vec::new();
    let mut chunk = [0u8; 1024];
    loop {
        if find(&buf, b"\r\n\r\n").is_some() {
            break;
        }
        let n = sock.read(&mut chunk).await.unwrap_or(0);
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&chunk[..n]);
    }
    String::from_utf8_lossy(&buf).to_string()
}

fn status_of(head: &str) -> u16 {
    head.lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|code| code.parse().ok())
        .unwrap_or(0)
}

// --- TLS helpers ------------------------------------------------------------------------------

fn client_config_trusting(ca_pem: &str) -> Arc<ClientConfig> {
    let mut roots = RootCertStore::empty();
    let mut reader = ca_pem.as_bytes();
    for cert in rustls_pemfile::certs(&mut reader) {
        roots.add(cert.unwrap()).unwrap();
    }
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let mut config = ClientConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_root_certificates(roots)
        .with_no_client_auth();
    config.alpn_protocols = vec![b"http/1.1".to_vec()];
    Arc::new(config)
}

/// A local TLS origin for `host`, minted from its own CA. Returns (addr, ca_pem, seen-auth cell).
async fn spawn_tls_origin(host: &str) -> (SocketAddr, String, Arc<Mutex<Option<String>>>) {
    let (ca_pem, ca_key) = generate_ca().unwrap();
    let leaves = LeafStore::new(&ca_pem, &ca_key).unwrap();
    let server_config = leaves.server_config(host).await.unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let seen = Arc::new(Mutex::new(None));
    let seen_task = seen.clone();
    tokio::spawn(async move {
        let (sock, _) = listener.accept().await.unwrap();
        let acceptor = TlsAcceptor::from(server_config);
        let mut tls = match acceptor.accept(sock).await {
            Ok(tls) => tls,
            Err(_) => return,
        };
        let mut buf = Vec::new();
        let mut chunk = [0u8; 1024];
        loop {
            if find(&buf, b"\r\n\r\n").is_some() {
                break;
            }
            match tls.read(&mut chunk).await {
                Ok(0) | Err(_) => break,
                Ok(n) => buf.extend_from_slice(&chunk[..n]),
            }
        }
        let head = String::from_utf8_lossy(&buf).to_string();
        let auth = head.lines().find_map(|line| {
            let (name, value) = line.split_once(':')?;
            name.trim()
                .eq_ignore_ascii_case("authorization")
                .then(|| value.trim().to_string())
        });
        *seen_task.lock().unwrap() = auth;
        let _ = tls
            .write_all(b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\nconnection: close\r\n\r\nok")
            .await;
        let _ = tls.shutdown().await;
    });
    (addr, ca_pem, seen)
}

/// Complete a TLS handshake to the proxy's MITM leaf and exchange one inner HTTP request.
async fn mitm_request(proxy: &Proxy, sock: TcpStream, host: &str, request: &[u8]) -> String {
    let connector = TlsConnector::from(client_config_trusting(&proxy.ca_pem));
    let name = ServerName::try_from(host.to_string()).unwrap();
    let mut tls = connector.connect(name, sock).await.unwrap();
    tls.write_all(request).await.unwrap();
    let mut response = Vec::new();
    let mut chunk = [0u8; 1024];
    loop {
        match tls.read(&mut chunk).await {
            Ok(0) => break,
            Ok(n) => response.extend_from_slice(&chunk[..n]),
            Err(error) => panic!("MITM response ended with a TLS error: {error}"),
        }
    }
    String::from_utf8_lossy(&response).to_string()
}

/// A plaintext local daemon standing in for the cache or the preview service: it records the head of
/// every request handed to it and answers `body`. Returns its `host:port` and the recorded heads.
async fn spawn_daemon(body: &'static str) -> (String, Arc<Mutex<Vec<String>>>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let seen = Arc::new(Mutex::new(Vec::new()));
    let seen_task = seen.clone();
    tokio::spawn(async move {
        loop {
            let (mut sock, _) = match listener.accept().await {
                Ok(pair) => pair,
                Err(_) => break,
            };
            let seen_conn = seen_task.clone();
            tokio::spawn(async move {
                let mut buf = Vec::new();
                let mut chunk = [0u8; 1024];
                loop {
                    if find(&buf, b"\r\n\r\n").is_some() {
                        break;
                    }
                    match sock.read(&mut chunk).await {
                        Ok(0) | Err(_) => break,
                        Ok(n) => buf.extend_from_slice(&chunk[..n]),
                    }
                }
                seen_conn
                    .lock()
                    .unwrap()
                    .push(String::from_utf8_lossy(&buf).to_string());
                let _ = sock
                    .write_all(
                        format!(
                            "HTTP/1.1 200 OK\r\ncontent-length: {}\r\nconnection: close\r\n\r\n\
                             {body}",
                            body.len()
                        )
                        .as_bytes(),
                    )
                    .await;
            });
        }
    });
    (addr.to_string(), seen)
}

/// An address nothing listens on: bound to learn a free port, then released.
async fn closed_port() -> String {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    drop(listener);
    addr.to_string()
}

/// Complete the MITM handshake, send one request, and read the response head alone — a refusal the
/// proxy answers while it drains the rest of the request is read as soon as it lands.
async fn mitm_response_head(proxy: &Proxy, sock: TcpStream, host: &str, request: &[u8]) -> String {
    let connector = TlsConnector::from(client_config_trusting(&proxy.ca_pem));
    let name = ServerName::try_from(host.to_string()).unwrap();
    let mut tls = connector.connect(name, sock).await.unwrap();
    tls.write_all(request).await.unwrap();
    let mut head = Vec::new();
    let mut chunk = [0u8; 1024];
    while find(&head, b"\r\n\r\n").is_none() {
        match tls.read(&mut chunk).await {
            Ok(0) | Err(_) => break,
            Ok(n) => head.extend_from_slice(&chunk[..n]),
        }
    }
    String::from_utf8_lossy(&head).to_string()
}

async fn spawn_echo() -> SocketAddr {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        loop {
            let (mut sock, _) = match listener.accept().await {
                Ok(pair) => pair,
                Err(_) => break,
            };
            tokio::spawn(async move {
                let mut chunk = [0u8; 1024];
                loop {
                    match sock.read(&mut chunk).await {
                        Ok(0) | Err(_) => break,
                        Ok(n) => {
                            if sock.write_all(&chunk[..n]).await.is_err() {
                                break;
                            }
                        }
                    }
                }
            });
        }
    });
    addr
}

async fn meter_records(state: &Arc<ControlState>) -> Vec<serde_json::Value> {
    for _ in 0..40 {
        {
            let records = state.meter_records.lock().unwrap();
            if !records.is_empty() {
                return records.clone();
            }
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    state.meter_records.lock().unwrap().clone()
}

fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack.windows(needle.len()).position(|w| w == needle)
}

// --- tests ------------------------------------------------------------------------------------

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn connect_without_a_token_is_refused() {
    let proxy = start_proxy(ControlState::new("[]")).await;
    let (_sock, head) = connect(&proxy, "api.example.com:443", None).await;
    assert_eq!(status_of(&head), 403, "no token was not refused: {head}");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn connect_with_a_garbage_token_is_refused() {
    let proxy = start_proxy(ControlState::new("[]")).await;
    let garbage = basic("not-a-real.token");
    let (_sock, head) = connect(&proxy, "api.example.com:443", Some(&garbage)).await;
    assert_eq!(
        status_of(&head),
        403,
        "garbage token was not refused: {head}"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn authorize_denial_refuses_the_connect() {
    let denied = Arc::new(ControlState {
        authorized: false,
        ..control_defaults("[]")
    });
    let proxy = start_proxy(denied).await;
    let auth = basic(&run_token(None));
    let (_sock, head) = connect(&proxy, "api.example.com:443", Some(&auth)).await;
    assert_eq!(
        status_of(&head),
        403,
        "authorize denial was not 403: {head}"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn authorize_rpc_error_answers_503() {
    let faulty = Arc::new(ControlState {
        authorize_status: 500,
        ..control_defaults("[]")
    });
    let proxy = start_proxy(faulty).await;
    let auth = basic(&run_token(None));
    let (_sock, head) = connect(&proxy, "api.example.com:443", Some(&auth)).await;
    assert_eq!(status_of(&head), 503, "authorize fault was not 503: {head}");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_scoped_host_tunnels_and_meters_one_egress_request() {
    let origin = spawn_echo().await;
    let target = format!("127.0.0.1:{}", origin.port());
    let rules = r#"[{"kind":"scope","hosts":["127.0.0.1"]},{"kind":"meter","host":"127.0.0.1","dimension":"requests"}]"#;
    let proxy = start_proxy(ControlState::new(rules)).await;
    let auth = basic(&run_token(None));
    let (mut sock, head) = connect(&proxy, &target, Some(&auth)).await;
    assert_eq!(
        status_of(&head),
        200,
        "scoped CONNECT was not tunnelled: {head}"
    );

    sock.write_all(b"ping").await.unwrap();
    let mut echoed = [0u8; 4];
    sock.read_exact(&mut echoed).await.unwrap();
    assert_eq!(&echoed, b"ping", "tunnel did not relay opaquely");

    // The wire posts the `sandbox_egress_total{host, dimension}` counter and the egress ledger
    // charge — the counter carrying host and dimension, the charge workspace and turn.
    let records = meter_records(&proxy.control).await;
    let metric = records
        .iter()
        .find(|r| r["kind"] == "metric")
        .unwrap_or_else(|| panic!("no metric record among {records:?}"));
    assert_eq!(metric["host"], "127.0.0.1");
    assert_eq!(metric["dimension"], "requests");
    let egress = records
        .iter()
        .find(|r| r["kind"] == "egress")
        .unwrap_or_else(|| panic!("no egress record among {records:?}"));
    assert_eq!(
        egress["workspace_id"],
        Uuid::from_u128(WORKSPACE).to_string()
    );
    assert_eq!(egress["turn_id"], Uuid::from_u128(TURN).to_string());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_pinned_scope_refuses_a_host_that_answers_a_private_address() {
    // An exact scope is otherwise the one path around the private-address check, so a pinned
    // scope's host is resolved here and refused.
    let origin = spawn_echo().await;
    let target = format!("127.0.0.1:{}", origin.port());
    let pinned = r#"[{"kind":"scope","hosts":["127.0.0.1"],"pinned":true}]"#;
    let proxy = start_proxy(ControlState::new(pinned)).await;
    let auth = basic(&run_token(None));
    let (_sock, head) = connect(&proxy, &target, Some(&auth)).await;
    assert_eq!(
        status_of(&head),
        403,
        "a pinned scope admitted a private address: {head}"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_pinned_scope_with_an_injection_rule_mitms_the_vetted_address_alone() {
    // A pinned scope plus an injection rule takes the MITM path. `localhost` answers the pin a
    // public address and the system resolver loopback, where a TLS origin records any secret.
    let (origin, origin_ca, seen) = spawn_tls_origin("localhost").await;
    let target = format!("localhost:{}", origin.port());
    let rules = r#"[{"kind":"scope","hosts":["localhost"],"pinned":true},{"kind":"injection","host":"localhost","header":"authorization","sentinel":"SENTINEL","real":"real-secret"}]"#;
    let proxy = start_proxy_pinning(
        ControlState::new(rules),
        Some(origin_ca),
        ServiceDaemons::default(),
        Some(("localhost", VETTED_PUBLIC_ADDRESS)),
    )
    .await;
    let auth = basic(&run_token(None));
    let (sock, head) = connect(&proxy, &target, Some(&auth)).await;
    assert_eq!(
        status_of(&head),
        200,
        "a pinned scope with an injection rule was not accepted: {head}"
    );

    // The vetted address answers nothing here, so the re-originated request never completes — the
    // test waits only long enough to see that no response came back from the private address.
    let request = b"GET / HTTP/1.1\r\nhost: localhost\r\nauthorization: Bearer SENTINEL\r\n\r\n";
    let answered = tokio::time::timeout(
        Duration::from_secs(3),
        mitm_response_head(&proxy, sock, "localhost", request),
    )
    .await
    .unwrap_or_default();
    assert!(
        !answered.contains("200 OK"),
        "the MITM answered from an address the pin never vetted: {answered}"
    );
    let handed = seen.lock().unwrap().clone();
    assert_eq!(
        handed, None,
        "the injected secret reached the private address the name also answers"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn an_internet_rule_refuses_a_private_destination() {
    let rules = r#"[{"kind":"internet"}]"#;
    let proxy = start_proxy(ControlState::new(rules)).await;
    let auth = basic(&run_token(None));
    let (_a, private) = connect(&proxy, "10.1.2.3:443", Some(&auth)).await;
    assert_eq!(
        status_of(&private),
        403,
        "private literal was admitted: {private}"
    );
    let (_b, loopback) = connect(&proxy, "127.0.0.1:443", Some(&auth)).await;
    assert_eq!(
        status_of(&loopback),
        403,
        "loopback literal was admitted: {loopback}"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn an_injection_host_mitms_and_swaps_the_real_secret_upstream() {
    let (origin, origin_ca, seen) = spawn_tls_origin("localhost").await;
    let target = format!("localhost:{}", origin.port());
    let rules = r#"[{"kind":"scope","hosts":["localhost"]},{"kind":"injection","host":"localhost","header":"authorization","sentinel":"SENTINEL","real":"real-secret"}]"#;
    let proxy = start_proxy_trusting(
        ControlState::new(rules),
        Some(origin_ca),
        ServiceDaemons::default(),
    )
    .await;
    let auth = basic(&run_token(None));
    let (sock, head) = connect(&proxy, &target, Some(&auth)).await;
    assert_eq!(
        status_of(&head),
        200,
        "MITM CONNECT was not accepted: {head}"
    );

    let request = b"GET / HTTP/1.1\r\nhost: localhost\r\nauthorization: Bearer SENTINEL\r\n\r\n";
    let response = mitm_request(&proxy, sock, "localhost", request).await;
    assert!(
        response.contains("200 OK"),
        "origin did not answer: {response}"
    );

    // Give the origin task a beat to record the header it saw.
    for _ in 0..40 {
        if seen.lock().unwrap().is_some() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
    assert_eq!(
        seen.lock().unwrap().clone(),
        Some("Bearer real-secret".to_string()),
        "the sentinel was not swapped for the real secret upstream"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_tool_bridge_request_reaches_core_with_the_proxy_run_authority() {
    let state = Arc::new(ControlState {
        bridge_json: r#"{"ok":true,"result":{"objects":[]}}"#.to_string(),
        ..control_defaults(
            r#"[{"kind":"service","host":"tools.ufo.internal","daemon_prefix":null}]"#,
        )
    });
    let proxy = start_proxy(state.clone()).await;
    let auth = basic(&run_token(Some(Uuid::from_u128(0x3333))));
    let (sock, head) = connect(&proxy, "tools.ufo.internal:443", Some(&auth)).await;
    assert_eq!(status_of(&head), 200, "bridge CONNECT was refused: {head}");
    let body = r#"{"request_id":"00000000-0000-0000-0000-000000004444","action":"get_schema","tool_name":"object_list","arguments":{}}"#;
    let request = format!(
        "POST /request HTTP/1.1\r\nhost: tools.ufo.internal\r\ncontent-type: application/json\r\ncontent-length: {}\r\n\r\n{body}",
        body.len()
    );
    let response = mitm_request(&proxy, sock, "tools.ufo.internal", request.as_bytes()).await;
    assert!(
        response.ends_with(r#"{"ok":true,"result":{"objects":[]}}"#),
        "bridge response missing: {response}"
    );
    let requests = state.bridge_requests.lock().unwrap();
    assert_eq!(requests.len(), 1);
    assert_eq!(requests[0]["proxy_auth"], auth);
    assert_eq!(requests[0]["request"]["tool_name"], "object_list");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn each_service_rule_relays_to_the_daemon_that_owns_its_host() {
    let (cache, cached) = spawn_daemon("packed refs").await;
    let (preview, rendered) = spawn_daemon("png bytes").await;
    let rules = r#"[{"kind":"service","host":"cache.ufo.internal","daemon_prefix":null},{"kind":"service","host":"preview.ufo.internal","daemon_prefix":null},{"kind":"injection","host":"preview.ufo.internal","header":"authorization","sentinel":"ufo-preview-token-sentinel","real":"preview-real"}]"#;
    let proxy = start_proxy_trusting(
        ControlState::new(rules),
        None,
        ServiceDaemons {
            cache: Some(cache),
            preview: Some(preview),
        },
    )
    .await;
    let auth = basic(&run_token(None));

    let (sock, head) = connect(&proxy, "cache.ufo.internal:443", Some(&auth)).await;
    assert_eq!(status_of(&head), 200, "cache CONNECT was refused: {head}");
    let answer = mitm_request(
        &proxy,
        sock,
        "cache.ufo.internal",
        b"GET /git/github.com/o/r/info/refs HTTP/1.1\r\nhost: cache.ufo.internal\r\nauthorization: Bearer ufo-preview-token-sentinel\r\n\r\n",
    )
    .await;
    assert!(answer.contains("packed refs"), "cache answer: {answer}");

    let (sock, head) = connect(&proxy, "preview.ufo.internal:443", Some(&auth)).await;
    assert_eq!(status_of(&head), 200, "preview CONNECT was refused: {head}");
    let answer = mitm_request(
        &proxy,
        sock,
        "preview.ufo.internal",
        b"POST /render HTTP/1.1\r\nhost: preview.ufo.internal\r\nauthorization: Bearer ufo-preview-token-sentinel\r\ncontent-length: 0\r\n\r\n",
    )
    .await;
    assert!(answer.contains("png bytes"), "preview answer: {answer}");

    let cached = cached.lock().unwrap().clone();
    let rendered = rendered.lock().unwrap().clone();
    assert_eq!(cached.len(), 1, "cache daemon saw {cached:?}");
    assert!(
        cached[0].starts_with("GET /git/github.com/o/r/info/refs"),
        "cache daemon saw {cached:?}"
    );
    assert!(cached[0].contains("ufo-preview-token-sentinel"));
    assert!(!cached[0].contains("preview-real"));
    assert_eq!(rendered.len(), 1, "preview daemon saw {rendered:?}");
    assert!(
        rendered[0].starts_with("POST /render"),
        "preview daemon saw {rendered:?}"
    );
    assert!(
        rendered[0].contains("authorization: Bearer preview-real"),
        "preview daemon saw {rendered:?}"
    );
    assert!(!rendered[0].contains("ufo-preview-token-sentinel"));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_preview_request_fails_in_the_tunnel_when_its_daemon_is_gone() {
    // Nothing else serves the preview host, so a render call is answered 502 inside the tunnel
    // whether no daemon is configured or the configured one is down.
    let rules = r#"[{"kind":"service","host":"preview.ufo.internal","daemon_prefix":null}]"#;
    let render =
        b"POST /render HTTP/1.1\r\nhost: preview.ufo.internal\r\ncontent-length: 0\r\n\r\n";
    for daemons in [
        ServiceDaemons::default(),
        ServiceDaemons {
            cache: None,
            preview: Some(closed_port().await),
        },
    ] {
        let proxy = start_proxy_trusting(ControlState::new(rules), None, daemons.clone()).await;
        let (sock, head) = connect(
            &proxy,
            "preview.ufo.internal:443",
            Some(&basic(&run_token(None))),
        )
        .await;
        assert_eq!(status_of(&head), 200, "preview CONNECT was refused: {head}");
        let answer = mitm_response_head(&proxy, sock, "preview.ufo.internal", render).await;
        assert_eq!(
            status_of(&answer),
            502,
            "preview daemon {:?} did not answer 502: {answer}",
            daemons.preview
        );
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_git_path_through_the_preview_host_never_reaches_an_origin() {
    // The preview host must not inherit the cache's path-based re-origination: a path naming
    // `github.com` is answered 502, never relayed to it.
    let rules = r#"[{"kind":"service","host":"preview.ufo.internal","daemon_prefix":null}]"#;
    let git =
        b"GET /git/github.com/owner/repo/info/refs HTTP/1.1\r\nhost: preview.ufo.internal\r\n\r\n";
    let proxy =
        start_proxy_trusting(ControlState::new(rules), None, ServiceDaemons::default()).await;
    let (sock, head) = connect(
        &proxy,
        "preview.ufo.internal:443",
        Some(&basic(&run_token(None))),
    )
    .await;
    assert_eq!(status_of(&head), 200, "preview CONNECT was refused: {head}");
    let answer = mitm_response_head(&proxy, sock, "preview.ufo.internal", git).await;
    assert_eq!(
        status_of(&answer),
        502,
        "a git-path request through the preview host was not answered 502: {answer}"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn shutdown_flushes_the_queued_meter_batch() {
    // A record enqueued just before a SIGTERM must still reach the control RPC: Meter::shutdown
    // closes the sink and awaits the batcher so its final window posts rather than being discarded.
    let state = ControlState::new("[]");
    let control_url = spawn_control(state.clone()).await;
    let control = Arc::new(Control::new(&control_url, "control-token"));
    let meter = Meter::start(control);
    meter
        .sink()
        .enqueue(MeterRecord::Egress {
            workspace_id: Uuid::from_u128(WORKSPACE),
            turn_id: Some(Uuid::from_u128(TURN)),
        })
        .await;
    meter.shutdown().await;
    let records = state.meter_records.lock().unwrap();
    assert_eq!(
        records.len(),
        1,
        "shutdown discarded the queued record: {records:?}"
    );
    assert_eq!(records[0]["kind"], "egress");
}

fn control_defaults(rules_json: &str) -> ControlState {
    ControlState {
        authorized: true,
        authorize_status: 200,
        generation: 0,
        rules_json: rules_json.to_string(),
        bridge_json: String::new(),
        bridge_requests: Mutex::new(Vec::new()),
        meter_records: Mutex::new(Vec::new()),
    }
}
