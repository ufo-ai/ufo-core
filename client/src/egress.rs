use std::collections::{HashMap, VecDeque};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream, ToSocketAddrs};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;
use std::time::Duration;

use base64::engine::general_purpose::STANDARD;
use base64::Engine as _;
use mio::{Events, Interest, Poll, Token};

const RESPONSE_HEAD_MAX_BYTES: usize = 64 * 1024;
const RESPONSE_HEAD_DELIMITER_BYTES: usize = 4;
const RELAY_BUFFER_BYTES: usize = 256 * 1024;
const RELAY_CHUNK_BYTES: usize = 16 * 1024;
const LOCAL: Token = Token(0);
const UPSTREAM: Token = Token(1);
const LOOPBACK_HOST: &str = "127.0.0.1";
static LOOPBACK_PROXIES: OnceLock<Mutex<HashMap<(String, u16), u16>>> = OnceLock::new();

#[derive(Debug, PartialEq)]
struct Wiring {
    proxy: Option<Proxy>,
    ca_file: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
struct Proxy {
    tls: bool,
    host: String,
    port: u16,
    authorization: Option<String>,
}

trait Io: Read + Write {}
impl<T: Read + Write> Io for T {}

pub(crate) fn loopback_proxy_url(raw: &str, ca_file: Option<&str>) -> Result<String, String> {
    let proxy = proxy_target(raw)?;
    if !proxy.tls {
        return Ok(raw.to_string());
    }
    let key = (proxy.host.clone(), proxy.port);
    let proxies = LOOPBACK_PROXIES.get_or_init(|| Mutex::new(HashMap::new()));
    let mut proxies = proxies
        .lock()
        .map_err(|_| "local proxy registry is unavailable".to_string())?;
    let port = match proxies.get(&key) {
        Some(port) => *port,
        None => {
            let port = start_loopback_proxy(proxy, tls_config(ca_file)?)?;
            proxies.insert(key, port);
            port
        }
    };
    let authority = raw
        .split_once("://")
        .map(|(_, rest)| rest)
        .unwrap_or_default()
        .split(['/', '?', '#'])
        .next()
        .unwrap_or_default();
    let userinfo = authority
        .rsplit_once('@')
        .map(|(userinfo, _)| format!("{userinfo}@"))
        .unwrap_or_default();
    Ok(format!("http://{userinfo}{LOOPBACK_HOST}:{port}"))
}

fn start_loopback_proxy(proxy: Proxy, config: Arc<rustls::ClientConfig>) -> Result<u16, String> {
    let listener = TcpListener::bind((LOOPBACK_HOST, 0))
        .map_err(|error| format!("could not bind the local egress proxy: {error}"))?;
    let port = listener
        .local_addr()
        .map_err(|error| format!("could not read the local egress proxy address: {error}"))?
        .port();
    thread::Builder::new()
        .name("ufo-egress-proxy".to_string())
        .spawn(move || {
            for local in listener.incoming().flatten() {
                let proxy = proxy.clone();
                let config = config.clone();
                let _ = thread::Builder::new()
                    .name("ufo-egress-relay".to_string())
                    .spawn(move || relay(local, &proxy, config));
            }
        })
        .map_err(|error| format!("could not start the local egress proxy: {error}"))?;
    Ok(port)
}

fn relay(local: TcpStream, proxy: &Proxy, config: Arc<rustls::ClientConfig>) -> Result<(), String> {
    let upstream = dialed(&proxy.host, proxy.port, Duration::from_secs(30))?;
    local
        .set_nonblocking(true)
        .and_then(|_| upstream.set_nonblocking(true))
        .map_err(|error| format!("could not prepare the local egress relay: {error}"))?;
    let mut local = mio::net::TcpStream::from_std(local);
    let mut upstream = mio::net::TcpStream::from_std(upstream);
    let mut poll =
        Poll::new().map_err(|error| format!("could not poll the egress relay: {error}"))?;
    poll.registry()
        .register(&mut local, LOCAL, Interest::READABLE | Interest::WRITABLE)
        .and_then(|_| {
            poll.registry().register(
                &mut upstream,
                UPSTREAM,
                Interest::READABLE | Interest::WRITABLE,
            )
        })
        .map_err(|error| format!("could not register the egress relay: {error}"))?;
    let name = rustls::pki_types::ServerName::try_from(proxy.host.clone())
        .map_err(|error| format!("{} is not a TLS server name: {error}", proxy.host))?;
    let mut tls = rustls::ClientConnection::new(config, name)
        .map_err(|error| format!("could not open TLS to {}: {error}", proxy.host))?;
    tls.set_buffer_limit(Some(RELAY_BUFFER_BYTES));
    let mut events = Events::with_capacity(8);
    let mut to_tls = VecDeque::new();
    let mut to_local = VecDeque::new();
    let mut local_closed = false;
    let mut upstream_closed = false;
    loop {
        let mut moved = false;
        while pump(
            &mut local,
            &mut upstream,
            &mut tls,
            &mut to_tls,
            &mut to_local,
        )? {
            moved = true;
        }
        if !local_closed && to_tls.len() < RELAY_BUFFER_BYTES {
            let (closed, read) = read_local(&mut local, &mut to_tls)?;
            local_closed = closed;
            moved |= read;
            if local_closed {
                tls.send_close_notify();
            }
        }
        while pump(
            &mut local,
            &mut upstream,
            &mut tls,
            &mut to_tls,
            &mut to_local,
        )? {
            moved = true;
        }
        if !upstream_closed && to_local.len() < RELAY_BUFFER_BYTES {
            let (closed, read) = read_upstream(&mut upstream, &mut tls, &proxy.host)?;
            upstream_closed = closed;
            moved |= read;
        }
        if local_closed && to_tls.is_empty() && !tls.wants_write() {
            return Ok(());
        }
        if upstream_closed && to_local.is_empty() {
            return Ok(());
        }
        if moved {
            continue;
        }
        poll.poll(&mut events, None)
            .map_err(|error| format!("could not poll the egress relay: {error}"))?;
    }
}

fn pump(
    local: &mut mio::net::TcpStream,
    upstream: &mut mio::net::TcpStream,
    tls: &mut rustls::ClientConnection,
    to_tls: &mut VecDeque<u8>,
    to_local: &mut VecDeque<u8>,
) -> Result<bool, String> {
    let mut moved = false;
    while !to_tls.is_empty() {
        let front = to_tls.as_slices().0;
        match tls.writer().write(front) {
            Ok(0) => break,
            Ok(written) => {
                to_tls.drain(..written);
                moved = true;
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => break,
            Err(error) => return Err(format!("could not write to the TLS relay: {error}")),
        }
    }
    while tls.wants_write() {
        match tls.write_tls(upstream) {
            Ok(0) => break,
            Ok(_) => moved = true,
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => break,
            Err(error) => return Err(format!("could not write to the upstream proxy: {error}")),
        }
    }
    while to_local.len() < RELAY_BUFFER_BYTES {
        let mut chunk = [0u8; RELAY_CHUNK_BYTES];
        let available = (RELAY_BUFFER_BYTES - to_local.len()).min(chunk.len());
        match tls.reader().read(&mut chunk[..available]) {
            Ok(0) => break,
            Ok(read) => {
                to_local.extend(&chunk[..read]);
                moved = true;
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => break,
            Err(error) => return Err(format!("could not read from the TLS relay: {error}")),
        }
    }
    while !to_local.is_empty() {
        let front = to_local.as_slices().0;
        match local.write(front) {
            Ok(0) => return Err("the local proxy connection closed".to_string()),
            Ok(written) => {
                to_local.drain(..written);
                moved = true;
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => break,
            Err(error)
                if matches!(
                    error.kind(),
                    std::io::ErrorKind::BrokenPipe | std::io::ErrorKind::ConnectionReset
                ) =>
            {
                return Err("the local proxy connection closed".to_string())
            }
            Err(error) => {
                return Err(format!(
                    "could not write to the local proxy client: {error}"
                ))
            }
        }
    }
    Ok(moved)
}

fn read_local(
    local: &mut mio::net::TcpStream,
    pending: &mut VecDeque<u8>,
) -> Result<(bool, bool), String> {
    let mut chunk = [0u8; RELAY_CHUNK_BYTES];
    let available = (RELAY_BUFFER_BYTES - pending.len()).min(chunk.len());
    match local.read(&mut chunk[..available]) {
        Ok(0) => Ok((true, false)),
        Ok(read) => {
            pending.extend(&chunk[..read]);
            Ok((false, true))
        }
        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => Ok((false, false)),
        Err(error)
            if matches!(
                error.kind(),
                std::io::ErrorKind::ConnectionReset | std::io::ErrorKind::BrokenPipe
            ) =>
        {
            Ok((true, false))
        }
        Err(error) => Err(format!("could not read the local proxy client: {error}")),
    }
}

fn read_upstream(
    upstream: &mut mio::net::TcpStream,
    tls: &mut rustls::ClientConnection,
    host: &str,
) -> Result<(bool, bool), String> {
    match tls.read_tls(upstream) {
        Ok(0) => Ok((true, false)),
        Ok(_) => {
            tls.process_new_packets()
                .map_err(|error| format!("{host} ended TLS: {error}"))?;
            Ok((false, true))
        }
        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => Ok((false, false)),
        Err(error)
            if matches!(
                error.kind(),
                std::io::ErrorKind::ConnectionReset | std::io::ErrorKind::BrokenPipe
            ) =>
        {
            Ok((true, false))
        }
        Err(error) => Err(format!("could not read {host}: {error}")),
    }
}

pub(crate) fn post(
    url: &str,
    host: &str,
    port: u16,
    head: &str,
    body: &[u8],
    timeout: Duration,
    max_body_bytes: usize,
) -> Result<(u16, Vec<u8>), String> {
    let wired = wiring(
        env_nonempty("HTTPS_PROXY")
            .or_else(|| env_nonempty("https_proxy"))
            .as_deref(),
        env_nonempty("SSL_CERT_FILE").as_deref(),
    )?;
    let config = tls_config(wired.ca_file.as_deref())?;
    let failed = |error: String| format!("{url} failed: {error}");
    let carrier = match &wired.proxy {
        Some(proxy) => tunnelled(proxy, host, port, config.clone(), timeout).map_err(failed)?,
        None => Box::new(dialed(host, port, timeout).map_err(failed)?),
    };
    let mut stream = tls_stream(config, host, carrier).map_err(failed)?;
    stream
        .write_all(head.as_bytes())
        .and_then(|_| stream.write_all(body))
        .and_then(|_| stream.flush())
        .map_err(|error| failed(error.to_string()))?;
    let max_response_bytes = max_body_bytes
        .checked_add(RESPONSE_HEAD_MAX_BYTES + RESPONSE_HEAD_DELIMITER_BYTES + 1)
        .ok_or_else(|| failed("response bound overflowed".to_string()))?;
    let mut answer = Vec::new();
    stream
        .take(max_response_bytes as u64)
        .read_to_end(&mut answer)
        .map_err(|error| failed(error.to_string()))?;
    if answer.len() == max_response_bytes {
        return Err(failed(format!(
            "response exceeds the {max_body_bytes}-byte body cap"
        )));
    }
    let (status, body) = parsed_response(url, &answer)?;
    if body.len() > max_body_bytes {
        return Err(failed(format!(
            "response exceeds the {max_body_bytes}-byte body cap"
        )));
    }
    Ok((status, body))
}

fn wiring(proxy: Option<&str>, ca_file: Option<&str>) -> Result<Wiring, String> {
    Ok(Wiring {
        proxy: match proxy {
            Some(raw) => Some(proxy_target(raw)?),
            None => None,
        },
        ca_file: ca_file.map(str::to_string),
    })
}

fn proxy_target(raw: &str) -> Result<Proxy, String> {
    let invalid = || format!("invalid HTTPS_PROXY URL: '{raw}'");
    let (scheme, rest) = raw.split_once("://").ok_or_else(invalid)?;
    let tls = match scheme {
        "https" => true,
        "http" => false,
        _ => return Err(invalid()),
    };
    let authority = rest.split(['/', '?', '#']).next().unwrap_or_default();
    let (userinfo, endpoint) = match authority.rsplit_once('@') {
        Some((userinfo, endpoint)) => (Some(userinfo), endpoint),
        None => (None, authority),
    };
    let (host, port) = match endpoint.rsplit_once(':') {
        Some((host, port)) => (host, port.parse::<u16>().map_err(|_| invalid())?),
        None => (endpoint, if tls { 443 } else { 80 }),
    };
    if host.is_empty() {
        return Err(invalid());
    }
    Ok(Proxy {
        tls,
        host: host.to_string(),
        port,
        authorization: userinfo.map(|creds| format!("Basic {}", STANDARD.encode(creds))),
    })
}

fn connect_request(proxy: &Proxy, host: &str, port: u16) -> String {
    let authorization = match &proxy.authorization {
        Some(header) => format!("Proxy-Authorization: {header}\r\n"),
        None => String::new(),
    };
    format!(
        "CONNECT {host}:{port} HTTP/1.1\r\n\
         Host: {host}:{port}\r\n\
         {authorization}\r\n"
    )
}

fn roots(ca_file: Option<&str>) -> Result<rustls::RootCertStore, String> {
    let mut store = rustls::RootCertStore::empty();
    let certificates = match ca_file {
        Some(path) => {
            let bytes = std::fs::read(path).map_err(|error| format!("{path}: {error}"))?;
            rustls_pemfile::certs(&mut std::io::BufReader::new(&bytes[..]))
                .collect::<Result<Vec<_>, _>>()
                .map_err(|error| format!("{path}: {error}"))?
        }
        None => rustls_native_certs::load_native_certs()
            .map_err(|error| format!("could not read this machine's trust store: {error}"))?,
    };
    for certificate in certificates {
        store
            .add(certificate)
            .map_err(|error| format!("could not trust a certificate: {error}"))?;
    }
    if store.is_empty() {
        return Err("no certificate authority is available to verify TLS".to_string());
    }
    Ok(store)
}

fn tls_config(ca_file: Option<&str>) -> Result<Arc<rustls::ClientConfig>, String> {
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let config = rustls::ClientConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .map_err(|error| format!("could not build a TLS client: {error}"))?
        .with_root_certificates(roots(ca_file)?)
        .with_no_client_auth();
    Ok(Arc::new(config))
}

fn tls_stream(
    config: Arc<rustls::ClientConfig>,
    host: &str,
    stream: Box<dyn Io>,
) -> Result<Box<dyn Io>, String> {
    let name = rustls::pki_types::ServerName::try_from(host.to_string())
        .map_err(|error| format!("{host} is not a TLS server name: {error}"))?;
    let connection = rustls::ClientConnection::new(config, name)
        .map_err(|error| format!("could not open TLS to {host}: {error}"))?;
    Ok(Box::new(rustls::StreamOwned::new(connection, stream)))
}

fn dialed(host: &str, port: u16, timeout: Duration) -> Result<TcpStream, String> {
    let address = (host, port)
        .to_socket_addrs()
        .map_err(|error| format!("{host}: {error}"))?
        .next()
        .ok_or_else(|| format!("{host} resolves to no address"))?;
    let stream = TcpStream::connect_timeout(&address, timeout)
        .map_err(|error| format!("{host}: {error}"))?;
    stream
        .set_read_timeout(Some(timeout))
        .and_then(|_| stream.set_write_timeout(Some(timeout)))
        .map_err(|error| format!("{host}: {error}"))?;
    Ok(stream)
}

fn tunnelled(
    proxy: &Proxy,
    host: &str,
    port: u16,
    config: Arc<rustls::ClientConfig>,
    timeout: Duration,
) -> Result<Box<dyn Io>, String> {
    let mut stream: Box<dyn Io> = Box::new(dialed(&proxy.host, proxy.port, timeout)?);
    if proxy.tls {
        stream = tls_stream(config, &proxy.host, stream)?;
    }
    stream
        .write_all(connect_request(proxy, host, port).as_bytes())
        .and_then(|_| stream.flush())
        .map_err(|error| format!("{}: {error}", proxy.host))?;
    let head = response_head(&mut stream, &proxy.host)?;
    let status = head
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .unwrap_or_default();
    if status != "200" {
        return Err(format!(
            "{} refused the tunnel to {host}: {}",
            proxy.host,
            head.lines().next().unwrap_or_default()
        ));
    }
    Ok(stream)
}

fn response_head(stream: &mut Box<dyn Io>, host: &str) -> Result<String, String> {
    let mut head = Vec::new();
    let mut byte = [0u8; 1];
    loop {
        let read = stream
            .read(&mut byte)
            .map_err(|error| format!("{host}: {error}"))?;
        if read == 0 {
            break;
        }
        head.push(byte[0]);
        if head.ends_with(b"\r\n\r\n") || head.ends_with(b"\n\n") {
            break;
        }
        if head.len() > RESPONSE_HEAD_MAX_BYTES {
            return Err(format!("{host} answered an oversized header"));
        }
    }
    Ok(String::from_utf8_lossy(&head).into_owned())
}

fn parsed_response(url: &str, bytes: &[u8]) -> Result<(u16, Vec<u8>), String> {
    let cut = bytes
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .ok_or_else(|| format!("{url} failed: the response has no header"))?;
    if cut > RESPONSE_HEAD_MAX_BYTES {
        return Err(format!("{url} failed: the response header is oversized"));
    }
    let head = String::from_utf8_lossy(&bytes[..cut]).into_owned();
    let body = &bytes[cut + 4..];
    let mut lines = head.lines();
    let status = lines
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|code| code.parse::<u16>().ok())
        .ok_or_else(|| format!("{url} failed: the response has no status"))?;
    let chunked = lines.any(|line| {
        let lowered = line.to_ascii_lowercase();
        lowered.starts_with("transfer-encoding:") && lowered.contains("chunked")
    });
    Ok((
        status,
        if chunked {
            dechunked(body)
        } else {
            body.to_vec()
        },
    ))
}

fn dechunked(body: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    let mut rest = body;
    loop {
        let Some(cut) = rest.windows(2).position(|window| window == b"\r\n") else {
            return out;
        };
        let size = String::from_utf8_lossy(&rest[..cut]);
        let size = size.split(';').next().unwrap_or_default().trim();
        let Ok(size) = usize::from_str_radix(size, 16) else {
            return out;
        };
        rest = &rest[cut + 2..];
        if size == 0 || size > rest.len() {
            return out;
        }
        out.extend_from_slice(&rest[..size]);
        rest = &rest[size..];
        if rest.starts_with(b"\r\n") {
            rest = &rest[2..];
        }
    }
}

fn env_nonempty(name: &str) -> Option<String> {
    std::env::var(name).ok().filter(|value| !value.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;

    fn relay_configs() -> (Arc<rustls::ClientConfig>, Arc<rustls::ServerConfig>) {
        let rcgen::CertifiedKey { cert, key_pair } =
            rcgen::generate_simple_self_signed(vec![LOOPBACK_HOST.to_string()]).unwrap();
        let mut roots = rustls::RootCertStore::empty();
        roots.add(cert.der().clone()).unwrap();
        let provider = Arc::new(rustls::crypto::ring::default_provider());
        let client = rustls::ClientConfig::builder_with_provider(provider.clone())
            .with_safe_default_protocol_versions()
            .unwrap()
            .with_root_certificates(roots)
            .with_no_client_auth();
        let key = rustls::pki_types::PrivatePkcs8KeyDer::from(key_pair.serialize_der());
        let server = rustls::ServerConfig::builder_with_provider(provider)
            .with_safe_default_protocol_versions()
            .unwrap()
            .with_no_client_auth()
            .with_single_cert(vec![cert.der().clone()], key.into())
            .unwrap();
        (Arc::new(client), Arc::new(server))
    }

    #[test]
    fn the_loopback_proxy_carries_plain_connect_over_tls() {
        let (client_config, server_config) = relay_configs();
        let listener = TcpListener::bind((LOOPBACK_HOST, 0)).unwrap();
        let upstream_port = listener.local_addr().unwrap().port();
        let (captured, received) = mpsc::channel();
        let payload = vec![b'x'; 1024 * 1024];
        let expected_payload = payload.clone();
        let request_head =
            b"CONNECT example.com:443 HTTP/1.1\r\nProxy-Authorization: Basic dG9rZW46dWZv\r\n\r\n";
        let request_bytes = request_head.len() + 1024 * 1024;
        thread::spawn(move || {
            let (socket, _) = listener.accept().unwrap();
            socket
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            socket
                .set_write_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let connection = rustls::ServerConnection::new(server_config).unwrap();
            let mut stream = rustls::StreamOwned::new(connection, socket);
            let mut request = vec![0u8; request_bytes];
            stream.read_exact(&mut request).unwrap();
            captured.send(request).unwrap();
            stream
                .write_all(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                .unwrap();
            stream.write_all(&payload).unwrap();
            stream.flush().unwrap();
        });
        let proxy = Proxy {
            tls: true,
            host: LOOPBACK_HOST.to_string(),
            port: upstream_port,
            authorization: None,
        };
        let loopback_port = start_loopback_proxy(proxy, client_config).unwrap();
        let mut stream = TcpStream::connect((LOOPBACK_HOST, loopback_port)).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        stream.write_all(request_head).unwrap();
        stream.write_all(&vec![b'y'; 1024 * 1024]).unwrap();
        let mut response = Vec::new();
        stream.read_to_end(&mut response).unwrap();
        assert!(response.starts_with(b"HTTP/1.1 200 Connection Established\r\n\r\n"));
        let body = &response[b"HTTP/1.1 200 Connection Established\r\n\r\n".len()..];
        assert_eq!(body.len(), expected_payload.len());
        assert!(body.iter().all(|byte| *byte == b'x'));
        let request = received.recv_timeout(Duration::from_secs(5)).unwrap();
        assert!(request.starts_with(request_head));
        assert_eq!(request.len(), request_head.len() + 1024 * 1024);
        assert!(request[request_head.len()..]
            .iter()
            .all(|byte| *byte == b'y'));
    }

    #[test]
    fn an_https_proxy_becomes_one_reused_authenticated_loopback() {
        let first = loopback_proxy_url("https://turn-a:ufo@proxy.invalid:8443", None).unwrap();
        let second = loopback_proxy_url("https://turn-b:ufo@proxy.invalid:8443", None).unwrap();
        assert!(first.starts_with("http://turn-a:ufo@127.0.0.1:"));
        assert!(second.starts_with("http://turn-b:ufo@127.0.0.1:"));
        assert_eq!(
            first.rsplit_once(':').unwrap().1,
            second.rsplit_once(':').unwrap().1
        );
        assert_eq!(
            loopback_proxy_url("http://turn-a:ufo@proxy.invalid:8080", None).unwrap(),
            "http://turn-a:ufo@proxy.invalid:8080"
        );
    }

    #[test]
    fn the_loopback_proxy_reads_the_named_ca_bundle() {
        let path = std::env::temp_dir().join(format!("ufo-run-ca-{}.pem", std::process::id()));
        std::fs::write(&path, b"not a certificate").unwrap();
        let error =
            loopback_proxy_url("https://turn:ufo@another-proxy.invalid:8443", path.to_str())
                .unwrap_err();
        std::fs::remove_file(path).unwrap();
        assert!(error.contains("certificate"), "{error}");
    }

    #[test]
    fn the_proxy_token_rides_a_header_and_leaves_the_url() {
        let wired = wiring(Some("https://run-token:ufo@sandbox-proxy.test"), None).unwrap();
        let proxy = wired.proxy.unwrap();
        assert_eq!(proxy.host, "sandbox-proxy.test");
        assert_eq!(proxy.port, 443);
        assert!(proxy.tls);
        assert_eq!(
            proxy.authorization.as_deref(),
            Some(format!("Basic {}", STANDARD.encode("run-token:ufo")).as_str())
        );
        let request = connect_request(&proxy, "preview.ufo.internal", 443);
        assert!(
            request.starts_with("CONNECT preview.ufo.internal:443 HTTP/1.1\r\n"),
            "{request}"
        );
        assert!(request.contains("Proxy-Authorization: Basic cnVuLXRva2VuOnVmbw==\r\n"));
        assert!(!request.contains("run-token:ufo@"));
    }

    #[test]
    fn a_plain_proxy_keeps_its_own_port_and_scheme() {
        let proxy = wiring(Some("http://proxy.test:3128/"), None)
            .unwrap()
            .proxy
            .unwrap();
        assert!(!proxy.tls);
        assert_eq!(proxy.port, 3128);
        assert_eq!(proxy.authorization, None);
        assert!(
            !connect_request(&proxy, "preview.ufo.internal", 443).contains("Proxy-Authorization")
        );
        let bare = wiring(Some("http://proxy.test"), None)
            .unwrap()
            .proxy
            .unwrap();
        assert_eq!(bare.port, 80);
    }

    #[test]
    fn an_unusable_proxy_url_is_refused() {
        for raw in ["socks5://proxy.test", "proxy.test:3128", "https://:8080"] {
            assert_eq!(
                wiring(Some(raw), None).unwrap_err(),
                format!("invalid HTTPS_PROXY URL: '{raw}'")
            );
        }
    }

    #[test]
    fn no_proxy_and_no_ca_file_wire_nothing() {
        assert_eq!(
            wiring(None, None).unwrap(),
            Wiring {
                proxy: None,
                ca_file: None
            }
        );
    }

    #[test]
    fn the_ca_file_is_what_a_request_trusts() {
        let Ok(native) = rustls_native_certs::load_native_certs() else {
            return;
        };
        let Some(first) = native.first() else {
            return;
        };
        let mut pem = String::from("-----BEGIN CERTIFICATE-----\n");
        for line in STANDARD.encode(first.as_ref()).as_bytes().chunks(64) {
            pem.push_str(std::str::from_utf8(line).unwrap());
            pem.push('\n');
        }
        pem.push_str("-----END CERTIFICATE-----\n");
        let path = std::env::temp_dir().join(format!("ufo-egress-ca-{}.pem", std::process::id()));
        std::fs::write(&path, pem).unwrap();
        let named = path.to_str().unwrap();
        assert_eq!(
            wiring(None, Some(named)).unwrap().ca_file.as_deref(),
            Some(named)
        );
        assert_eq!(roots(Some(named)).unwrap().len(), 1);
        assert!(!roots(None).unwrap().is_empty());
        assert!(tls_config(Some(named)).is_ok());
        let _ = std::fs::remove_file(&path);
        let missing = "/nonexistent/ca.pem";
        assert!(roots(Some(missing))
            .unwrap_err()
            .starts_with("/nonexistent/ca.pem: "));
    }

    #[test]
    fn response_parsing_reads_a_status_and_unwraps_chunks() {
        let plain = b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\n\r\n{}";
        assert_eq!(
            parsed_response("https://test", plain).unwrap(),
            (200, b"{}".to_vec())
        );
        let chunked =
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n\r\n" as &[u8];
        assert_eq!(
            parsed_response("https://test", chunked).unwrap(),
            (200, b"{}".to_vec())
        );
        let refused = b"HTTP/1.1 401 Unauthorized\r\n\r\nno";
        assert_eq!(
            parsed_response("https://test", refused).unwrap(),
            (401, b"no".to_vec())
        );
        assert!(parsed_response("https://test", b"nothing").is_err());
    }
}
