use axum::body::{Body, Bytes};
use axum::http::{HeaderName, HeaderValue, StatusCode};
use axum::response::Response;
use tokio::io::AsyncReadExt;
use tokio::process::Child;

const MAX_HEADER_BYTES: usize = 64 * 1024;
const READ_CHUNK: usize = 64 * 1024;

/// Translate a CGI child's stdout into an HTTP response: read the header block, then stream the
/// body straight from the pipe so a packfile is never buffered in memory. The child is owned by the
/// body stream and reaped at EOF.
pub async fn response_from_cgi(mut child: Child) -> Result<Response, String> {
    let mut stdout = child.stdout.take().ok_or("cgi child has no stdout")?;

    let mut buf: Vec<u8> = Vec::with_capacity(READ_CHUNK);
    let boundary = loop {
        if let Some(pos) = find_boundary(&buf) {
            break pos;
        }
        if buf.len() > MAX_HEADER_BYTES {
            return Err("cgi header block too large".into());
        }
        let mut chunk = [0u8; READ_CHUNK];
        let n = stdout
            .read(&mut chunk)
            .await
            .map_err(|e| format!("cgi read: {e}"))?;
        if n == 0 {
            return Err("cgi child closed before headers".into());
        }
        buf.extend_from_slice(&chunk[..n]);
    };

    let (header_bytes, sep_len) = boundary;
    let mut status = StatusCode::OK;
    let mut headers: Vec<(HeaderName, HeaderValue)> = Vec::new();
    for line in split_lines(&buf[..header_bytes]) {
        let Some((name, value)) = line.split_once(':') else {
            continue;
        };
        let name = name.trim();
        let value = value.trim();
        if name.eq_ignore_ascii_case("status") {
            if let Some(code) = value.split_whitespace().next() {
                if let Ok(parsed) = code.parse::<u16>() {
                    status = StatusCode::from_u16(parsed).unwrap_or(StatusCode::OK);
                }
            }
            continue;
        }
        if let (Ok(n), Ok(v)) = (
            HeaderName::from_bytes(name.as_bytes()),
            HeaderValue::from_str(value),
        ) {
            headers.push((n, v));
        }
    }

    let leftover = Bytes::copy_from_slice(&buf[header_bytes + sep_len..]);
    let stream = async_stream::stream! {
        if !leftover.is_empty() {
            yield Ok::<Bytes, std::io::Error>(leftover);
        }
        let mut chunk = vec![0u8; READ_CHUNK];
        loop {
            match stdout.read(&mut chunk).await {
                Ok(0) => break,
                Ok(n) => yield Ok(Bytes::copy_from_slice(&chunk[..n])),
                Err(e) => {
                    yield Err(e);
                    break;
                }
            }
        }
        let _ = child.wait().await;
    };

    let mut builder = Response::builder().status(status);
    for (name, value) in headers {
        builder = builder.header(name, value);
    }
    builder
        .body(Body::from_stream(stream))
        .map_err(|e| format!("build response: {e}"))
}

/// Position of the header/body separator and its length, supporting `\r\n\r\n` and `\n\n`.
fn find_boundary(buf: &[u8]) -> Option<(usize, usize)> {
    let crlf = find(buf, b"\r\n\r\n").map(|p| (p, 4));
    let lf = find(buf, b"\n\n").map(|p| (p, 2));
    match (crlf, lf) {
        (Some(a), Some(b)) => Some(if a.0 <= b.0 { a } else { b }),
        (Some(a), None) => Some(a),
        (None, Some(b)) => Some(b),
        (None, None) => None,
    }
}

fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack.windows(needle.len()).position(|w| w == needle)
}

fn split_lines(header: &[u8]) -> Vec<String> {
    String::from_utf8_lossy(header)
        .split('\n')
        .map(|l| l.trim_end_matches('\r').to_string())
        .filter(|l| !l.is_empty())
        .collect()
}
