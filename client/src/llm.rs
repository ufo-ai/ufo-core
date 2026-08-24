//! The `ufo llm` verb: the one credentialed egress a sandbox script gets.
//!
//! The Anthropic Messages API, reached through the egress proxy the carrier set. The sandbox holds
//! only the sentinel key (`ANTHROPIC_API_KEY`) and dials out through `HTTPS_PROXY`, trusting the
//! proxy CA (`SSL_CERT_FILE`, else this machine's own store); the proxy swaps the sentinel for the
//! real key on the wire, so the raw credential never enters the sandbox. The proxy's own userinfo
//! becomes a `Proxy-Authorization` header and leaves the request line, so the run token is never
//! written into a URL.
//!
//! The request is spoken directly over rustls rather than through the client's HTTP agent, because
//! the deploy's proxy terminates TLS itself: a `https://` proxy needs TLS to the proxy and then TLS
//! to the host inside the tunnel, which the agent's proxy support does not offer.

use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::sync::Arc;
use std::time::Duration;

use base64::engine::general_purpose::STANDARD;
use base64::Engine as _;

pub const USAGE: &str = "usage: ufo llm [--model MODEL] [--max-tokens N] PROMPT";
const DEFAULT_MODEL: &str = "claude-opus-4-8";
const DEFAULT_MAX_TOKENS: u64 = 8192;
const ANTHROPIC_HOST: &str = "api.anthropic.com";
const ANTHROPIC_PORT: u16 = 443;
const ANTHROPIC_PATH: &str = "/v1/messages";
const ANTHROPIC_URL: &str = "https://api.anthropic.com/v1/messages";
const ANTHROPIC_VERSION: &str = "2023-06-01";
const API_KEY_ENV: &str = "ANTHROPIC_API_KEY";
const REQUEST_TIMEOUT: Duration = Duration::from_secs(60);
const HEAD_MAX_BYTES: usize = 64 * 1024;

/// One call's argv, after the flags are read off it.
#[derive(Debug, PartialEq)]
pub struct Call {
    pub model: String,
    pub max_tokens: u64,
    pub prompt: String,
}

/// Where the request goes and what it trusts on the way — the sandbox's egress wiring, read once.
#[derive(Debug, PartialEq)]
pub struct Wiring {
    pub proxy: Option<Proxy>,
    pub ca_file: Option<String>,
}

/// The proxy to tunnel through, with its credential lifted out of the URL.
#[derive(Debug, PartialEq)]
pub struct Proxy {
    pub tls: bool,
    pub host: String,
    pub port: u16,
    pub authorization: Option<String>,
}

trait Io: Read + Write {}
impl<T: Read + Write> Io for T {}

/// Write the answer and answer the exit code: the response text plus one newline on stdout, a
/// handled failure as `ufo llm: {error}` on stderr and exit 1, a usage error exit 2.
pub fn main(args: &[String]) -> i32 {
    let call = match parse(args) {
        Ok(call) => call,
        Err(usage) => {
            eprintln!("{usage}");
            return 2;
        }
    };
    match answered(&call) {
        Ok(text) => {
            let mut out = std::io::stdout().lock();
            let _ = out.write_all(text.as_bytes());
            let _ = out.write_all(b"\n");
            let _ = out.flush();
            0
        }
        Err(error) => {
            eprintln!("ufo llm: {error}");
            1
        }
    }
}

fn answered(call: &Call) -> Result<String, String> {
    let key = require_env(API_KEY_ENV)?;
    let wired = wiring(
        env_nonempty("HTTPS_PROXY")
            .or_else(|| env_nonempty("https_proxy"))
            .as_deref(),
        env_nonempty("SSL_CERT_FILE").as_deref(),
    )?;
    anthropic_text(&posted(call, &wired, &key)?)
}

pub fn parse(args: &[String]) -> Result<Call, String> {
    let mut model = DEFAULT_MODEL.to_string();
    let mut max_tokens = DEFAULT_MAX_TOKENS;
    let mut prompt: Option<String> = None;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--model" => {
                model = args
                    .get(index + 1)
                    .ok_or_else(|| USAGE.to_string())?
                    .clone();
                index += 2;
            }
            "--max-tokens" => {
                max_tokens = args
                    .get(index + 1)
                    .and_then(|value| value.parse::<u64>().ok())
                    .ok_or_else(|| USAGE.to_string())?;
                index += 2;
            }
            other if prompt.is_none() && !other.starts_with("--") => {
                prompt = Some(other.to_string());
                index += 1;
            }
            _ => return Err(USAGE.to_string()),
        }
    }
    Ok(Call {
        model,
        max_tokens,
        prompt: prompt.ok_or_else(|| USAGE.to_string())?,
    })
}

/// The proxy and the trusted CA file this environment names. An unusable proxy URL is refused rather
/// than ignored: dialing out without the proxy would leave the sentinel key on an untunnelled
/// connection.
pub fn wiring(proxy: Option<&str>, ca_file: Option<&str>) -> Result<Wiring, String> {
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

/// The CONNECT request that opens the tunnel. The credential rides a header, never the request line.
pub fn connect_request(proxy: &Proxy) -> String {
    let authorization = match &proxy.authorization {
        Some(header) => format!("Proxy-Authorization: {header}\r\n"),
        None => String::new(),
    };
    format!(
        "CONNECT {ANTHROPIC_HOST}:{ANTHROPIC_PORT} HTTP/1.1\r\n\
         Host: {ANTHROPIC_HOST}:{ANTHROPIC_PORT}\r\n\
         {authorization}\r\n"
    )
}

pub fn request_body(call: &Call) -> Vec<u8> {
    serde_json::to_vec(&serde_json::json!({
        "model": call.model,
        "max_tokens": call.max_tokens,
        "messages": [{"role": "user", "content": call.prompt}],
    }))
    .expect("a call serializes")
}

pub fn request_head(key: &str, length: usize) -> String {
    format!(
        "POST {ANTHROPIC_PATH} HTTP/1.1\r\n\
         Host: {ANTHROPIC_HOST}\r\n\
         content-type: application/json\r\n\
         x-api-key: {key}\r\n\
         anthropic-version: {ANTHROPIC_VERSION}\r\n\
         content-length: {length}\r\n\
         connection: close\r\n\r\n"
    )
}

/// Every `text` block of the response, concatenated.
pub fn anthropic_text(response: &serde_json::Value) -> Result<String, String> {
    let Some(blocks) = response.get("content").and_then(|value| value.as_array()) else {
        return Err(format!(
            "anthropic response has no content array: {response}"
        ));
    };
    Ok(blocks
        .iter()
        .filter(|block| block.get("type").and_then(|kind| kind.as_str()) == Some("text"))
        .filter_map(|block| block.get("text").and_then(|text| text.as_str()))
        .collect())
}

/// One response's status and body, from the bytes the connection answered. `connection: close` is
/// what makes the body's end unambiguous; a chunked one is still unwrapped, because the framing is
/// the server's choice and not the request's.
pub fn parsed_response(bytes: &[u8]) -> Result<(u16, Vec<u8>), String> {
    let cut = bytes
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .ok_or_else(|| format!("{ANTHROPIC_URL} failed: the response has no header"))?;
    let head = String::from_utf8_lossy(&bytes[..cut]).into_owned();
    let body = &bytes[cut + 4..];
    let mut lines = head.lines();
    let status = lines
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|code| code.parse::<u16>().ok())
        .ok_or_else(|| format!("{ANTHROPIC_URL} failed: the response has no status"))?;
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

/// The roots a request verifies TLS against: the CA file the sandbox names, else this machine's own
/// trust store. The proxy mints the leaf for the host it terminates, so its CA is the only root that
/// can verify the tunnel's inner handshake.
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

fn dialed(host: &str, port: u16) -> Result<TcpStream, String> {
    let address = (host, port)
        .to_socket_addrs()
        .map_err(|error| format!("{host}: {error}"))?
        .next()
        .ok_or_else(|| format!("{host} resolves to no address"))?;
    let stream = TcpStream::connect_timeout(&address, REQUEST_TIMEOUT)
        .map_err(|error| format!("{host}: {error}"))?;
    stream
        .set_read_timeout(Some(REQUEST_TIMEOUT))
        .and_then(|_| stream.set_write_timeout(Some(REQUEST_TIMEOUT)))
        .map_err(|error| format!("{host}: {error}"))?;
    Ok(stream)
}

fn tunnelled(proxy: &Proxy, config: Arc<rustls::ClientConfig>) -> Result<Box<dyn Io>, String> {
    let mut stream: Box<dyn Io> = Box::new(dialed(&proxy.host, proxy.port)?);
    if proxy.tls {
        stream = tls_stream(config, &proxy.host, stream)?;
    }
    stream
        .write_all(connect_request(proxy).as_bytes())
        .and_then(|_| stream.flush())
        .map_err(|error| format!("{}: {error}", proxy.host))?;
    let head = tunnel_head(&mut stream, &proxy.host)?;
    let status = head
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .unwrap_or_default();
    if status != "200" {
        return Err(format!(
            "{} refused the tunnel to {ANTHROPIC_HOST}: {}",
            proxy.host,
            head.lines().next().unwrap_or_default()
        ));
    }
    Ok(stream)
}

/// The CONNECT response's header, read a byte at a time so the tunnelled bytes behind it stay in the
/// stream the request then writes to.
fn tunnel_head(stream: &mut Box<dyn Io>, host: &str) -> Result<String, String> {
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
        if head.len() > HEAD_MAX_BYTES {
            return Err(format!("{host} answered an oversized header"));
        }
    }
    Ok(String::from_utf8_lossy(&head).into_owned())
}

fn posted(call: &Call, wired: &Wiring, key: &str) -> Result<serde_json::Value, String> {
    let config = tls_config(wired.ca_file.as_deref())?;
    let failed = |error: String| format!("{ANTHROPIC_URL} failed: {error}");
    let carrier = match &wired.proxy {
        Some(proxy) => tunnelled(proxy, config.clone()).map_err(failed)?,
        None => Box::new(dialed(ANTHROPIC_HOST, ANTHROPIC_PORT).map_err(failed)?),
    };
    let mut stream = tls_stream(config, ANTHROPIC_HOST, carrier).map_err(failed)?;
    let body = request_body(call);
    let mut answer = Vec::new();
    stream
        .write_all(request_head(key, body.len()).as_bytes())
        .and_then(|_| stream.write_all(&body))
        .and_then(|_| stream.flush())
        .and_then(|_| stream.read_to_end(&mut answer))
        .map_err(|error| failed(error.to_string()))?;
    let (status, body) = parsed_response(&answer)?;
    if status != 200 {
        return Err(format!(
            "{ANTHROPIC_URL} -> {status}: {}",
            String::from_utf8_lossy(&body)
        ));
    }
    let decoded: serde_json::Value = serde_json::from_slice(&body)
        .map_err(|_| format!("{ANTHROPIC_URL} returned invalid JSON"))?;
    if !decoded.is_object() {
        return Err(format!(
            "{ANTHROPIC_URL} returned non-object JSON: {}",
            String::from_utf8_lossy(&body)
        ));
    }
    Ok(decoded)
}

fn require_env(name: &str) -> Result<String, String> {
    env_nonempty(name).ok_or_else(|| format!("{name} is required in the sandbox environment"))
}

fn env_nonempty(name: &str) -> Option<String> {
    std::env::var(name).ok().filter(|value| !value.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_defaults_and_flags() {
        assert_eq!(
            parse(&["hello".to_string()]).unwrap(),
            Call {
                model: DEFAULT_MODEL.to_string(),
                max_tokens: DEFAULT_MAX_TOKENS,
                prompt: "hello".to_string(),
            }
        );
        let flagged = parse(&[
            "--model".to_string(),
            "claude-3".to_string(),
            "--max-tokens".to_string(),
            "16".to_string(),
            "why".to_string(),
        ])
        .unwrap();
        assert_eq!(flagged.model, "claude-3");
        assert_eq!(flagged.max_tokens, 16);
        assert_eq!(flagged.prompt, "why");
    }

    #[test]
    fn parse_refuses_a_usage_error() {
        for args in [
            vec![],
            vec!["--model".to_string()],
            vec!["--max-tokens".to_string(), "many".to_string()],
            vec!["--unknown".to_string(), "x".to_string()],
            vec!["one".to_string(), "two".to_string()],
        ] {
            assert_eq!(parse(&args).unwrap_err(), USAGE);
        }
    }

    /// `test_sbx.py`'s proxy assertion: the token becomes a basic-auth header and leaves the URL.
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
        let request = connect_request(&proxy);
        assert!(
            request.starts_with("CONNECT api.anthropic.com:443 HTTP/1.1\r\n"),
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
        assert!(!connect_request(&proxy).contains("Proxy-Authorization"));
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

    /// The CA file is the trust anchor, not an addition to the machine's store: a bundle holding one
    /// certificate leaves exactly that one root, and an unreadable one fails the call rather than
    /// falling back to a store the proxy did not sign for.
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
        let path = std::env::temp_dir().join(format!("ufo-llm-ca-{}.pem", std::process::id()));
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
    fn the_request_carries_the_sentinel_key_and_the_api_version() {
        let call = Call {
            model: "claude-opus-4-8".to_string(),
            max_tokens: 8192,
            prompt: "hi".to_string(),
        };
        let body = request_body(&call);
        assert_eq!(
            String::from_utf8(body.clone()).unwrap(),
            r#"{"max_tokens":8192,"messages":[{"content":"hi","role":"user"}],"model":"claude-opus-4-8"}"#
        );
        let head = request_head("sentinel", body.len());
        assert!(head.starts_with("POST /v1/messages HTTP/1.1\r\n"), "{head}");
        assert!(head.contains("x-api-key: sentinel\r\n"));
        assert!(head.contains("anthropic-version: 2023-06-01\r\n"));
        assert!(head.contains(&format!("content-length: {}\r\n", body.len())));
    }

    #[test]
    fn response_parsing_reads_a_status_and_unwraps_chunks() {
        let plain = b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\n\r\n{}";
        assert_eq!(parsed_response(plain).unwrap(), (200, b"{}".to_vec()));
        let chunked =
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n\r\n" as &[u8];
        assert_eq!(parsed_response(chunked).unwrap(), (200, b"{}".to_vec()));
        let refused = b"HTTP/1.1 401 Unauthorized\r\n\r\nno";
        assert_eq!(parsed_response(refused).unwrap(), (401, b"no".to_vec()));
        assert!(parsed_response(b"nothing").is_err());
    }

    #[test]
    fn text_blocks_concatenate_and_a_missing_array_refuses() {
        let response = serde_json::json!({
            "content": [
                {"type": "text", "text": "one "},
                {"type": "thinking", "text": "skipped"},
                {"type": "text", "text": "two"},
            ]
        });
        assert_eq!(anthropic_text(&response).unwrap(), "one two");
        let empty = serde_json::json!({"content": []});
        assert_eq!(anthropic_text(&empty).unwrap(), "");
        let broken = serde_json::json!({"stop_reason": "end_turn"});
        assert_eq!(
            anthropic_text(&broken).unwrap_err(),
            "anthropic response has no content array: {\"stop_reason\":\"end_turn\"}"
        );
    }

    #[test]
    fn a_missing_api_key_names_the_variable() {
        assert_eq!(
            require_env("UFO_LLM_TEST_ABSENT").unwrap_err(),
            "UFO_LLM_TEST_ABSENT is required in the sandbox environment"
        );
    }
}
