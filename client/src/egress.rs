use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::sync::Arc;
use std::time::Duration;

use base64::engine::general_purpose::STANDARD;
use base64::Engine as _;

const RESPONSE_HEAD_MAX_BYTES: usize = 64 * 1024;
const RESPONSE_HEAD_DELIMITER_BYTES: usize = 4;

#[derive(Debug, PartialEq)]
struct Wiring {
    proxy: Option<Proxy>,
    ca_file: Option<String>,
}

#[derive(Debug, PartialEq)]
struct Proxy {
    tls: bool,
    host: String,
    port: u16,
    authorization: Option<String>,
}

trait Io: Read + Write {}
impl<T: Read + Write> Io for T {}

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

    #[test]
    fn the_proxy_token_rides_a_header_and_leaves_the_url() {
        let wired = wiring(Some("https://run-token:@sandbox-proxy.test"), None).unwrap();
        let proxy = wired.proxy.unwrap();
        assert_eq!(proxy.host, "sandbox-proxy.test");
        assert_eq!(proxy.port, 443);
        assert!(proxy.tls);
        assert_eq!(
            proxy.authorization.as_deref(),
            Some(format!("Basic {}", STANDARD.encode("run-token:")).as_str())
        );
        let request = connect_request(&proxy, "preview.ufo.internal", 443);
        assert!(
            request.starts_with("CONNECT preview.ufo.internal:443 HTTP/1.1\r\n"),
            "{request}"
        );
        assert!(request.contains("Proxy-Authorization: Basic cnVuLXRva2VuOg==\r\n"));
        assert!(!request.contains("run-token:@"));
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
