use std::net::{IpAddr, SocketAddr};
use std::time::Duration;

use futures_util::StreamExt;

use crate::refusal::Refusal;

/// Resolve and admit a caller-supplied URL: https only (unless `allow_local`), no private,
/// loopback, or link-local target. Returns the one vetted address the connection must use.
///
/// `Url::host_str` brackets an IPv6 literal (`"[::1]"`), which is neither valid `Ipv6Addr`
/// syntax nor a resolvable hostname — matching on `Url::host` instead reads a literal address
/// straight out of the URL with no DNS round trip, so it can't be refused for the wrong reason.
pub(crate) async fn vet(url: &url::Url, allow_local: bool) -> Result<SocketAddr, Refusal> {
    match url.scheme() {
        "https" => {}
        "http" if allow_local => {}
        s => return Err(Refusal::FetchRefused(format!("scheme {s} refused"))),
    }
    let port = url.port_or_known_default().unwrap_or(443);
    let ip = match url
        .host()
        .ok_or_else(|| Refusal::FetchRefused("url has no host".into()))?
    {
        url::Host::Ipv4(v4) => IpAddr::V4(v4),
        url::Host::Ipv6(v6) => IpAddr::V6(v6),
        url::Host::Domain(host) => tokio::net::lookup_host((host, port))
            .await
            .map_err(|e| Refusal::FetchRefused(format!("resolve {host}: {e}")))?
            .next()
            .ok_or_else(|| Refusal::FetchRefused(format!("{host} resolves to nothing")))?
            .ip(),
    };
    if !allow_local && !is_public(ip) {
        return Err(Refusal::FetchRefused(
            "target resolves to a non-public address".into(),
        ));
    }
    Ok(SocketAddr::new(ip, port))
}

fn is_public(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(v4) => {
            !(v4.is_loopback()
                || v4.is_private()
                || v4.is_link_local()
                || v4.is_unspecified()
                || v4.is_broadcast()
                || v4.is_multicast()
                || (v4.octets()[0] == 100 && (v4.octets()[1] & 0xc0) == 64))
        }
        IpAddr::V6(v6) => {
            if v6.is_loopback() || v6.is_unspecified() {
                return false;
            }
            if let Some(v4) = v6.to_ipv4() {
                return is_public(IpAddr::V4(v4));
            }
            !(v6.is_multicast()
                || (v6.segments()[0] & 0xfe00) == 0xfc00
                || (v6.segments()[0] & 0xffc0) == 0xfe80)
        }
    }
}

fn client(url: &url::Url, addr: SocketAddr, timeout: Duration) -> Result<reqwest::Client, Refusal> {
    let host = url.host_str().unwrap_or_default();
    reqwest::Client::builder()
        .resolve(host, addr)
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(timeout)
        .build()
        .map_err(|e| Refusal::FetchRefused(format!("client: {e}")))
}

/// Fetch a caller-supplied URL, streaming into memory while enforcing `cap` bytes; never buffers
/// past the cap. The address is vetted once and pinned for the one connection made against it.
pub async fn fetch_source(
    url: &str,
    cap: u64,
    allow_local: bool,
    timeout: Duration,
) -> Result<Vec<u8>, Refusal> {
    let parsed = url::Url::parse(url).map_err(|e| Refusal::FetchRefused(format!("url: {e}")))?;
    let addr = vet(&parsed, allow_local).await?;
    let resp = client(&parsed, addr, timeout)?
        .get(parsed.clone())
        .send()
        .await
        .map_err(|e| Refusal::FetchRefused(format!("fetch: {e}")))?;
    if resp.status().is_redirection() {
        return Err(Refusal::FetchRefused("redirect refused".into()));
    }
    if !resp.status().is_success() {
        return Err(Refusal::FetchRefused(format!(
            "source answered {}",
            resp.status()
        )));
    }
    let mut body = Vec::new();
    let mut stream = resp.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|e| Refusal::FetchRefused(format!("read: {e}")))?;
        if body.len() as u64 + chunk.len() as u64 > cap {
            return Err(Refusal::TooLarge(format!("source exceeds {cap} bytes")));
        }
        body.extend_from_slice(&chunk);
    }
    Ok(body)
}

/// Put a render result to a caller-supplied URL, vetted the same way as `fetch_source`. The URL is
/// the caller's own presigned PUT — it signs whatever it signs; the service adds only `content-type`.
pub async fn put_result(
    url: &str,
    body: Vec<u8>,
    media_type: &str,
    allow_local: bool,
    timeout: Duration,
) -> Result<(), Refusal> {
    let parsed = url::Url::parse(url).map_err(|e| Refusal::FetchRefused(format!("url: {e}")))?;
    let addr = vet(&parsed, allow_local).await?;
    let resp = client(&parsed, addr, timeout)?
        .put(parsed.clone())
        .header("content-type", media_type)
        .body(body)
        .send()
        .await
        .map_err(|e| Refusal::FetchRefused(format!("put: {e}")))?;
    if !resp.status().is_success() {
        return Err(Refusal::FetchRefused(format!(
            "sink answered {}",
            resp.status()
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    async fn refused(u: &str) -> bool {
        vet(&url::Url::parse(u).unwrap(), false).await.is_err()
    }

    #[tokio::test]
    async fn refuses_http_without_allow_local() {
        assert!(refused("http://example.com/x").await);
    }

    #[tokio::test]
    async fn refuses_private_and_loopback_literals() {
        for u in [
            "https://127.0.0.1/x",
            "https://10.1.2.3/x",
            "https://192.168.1.1/x",
            "https://169.254.169.254/latest/meta-data",
            "https://172.16.0.1/x",
            "https://100.64.0.1/x",
            "https://[::1]/x",
            "https://[fd00::1]/x",
            "https://[fe80::1]/x",
            "https://[::ffff:127.0.0.1]/x",
            "https://[::ffff:10.1.2.3]/x",
            "https://[::127.0.0.1]/x",
        ] {
            assert!(refused(u).await, "{u}");
        }
    }

    #[tokio::test]
    async fn allow_local_admits_loopback_http() {
        let ok = vet(&url::Url::parse("http://127.0.0.1:9/x").unwrap(), true).await;
        assert!(ok.is_ok());
    }

    #[tokio::test]
    async fn refuses_domain_that_resolves_locally() {
        assert!(refused("https://localhost/x").await);
    }

    #[tokio::test]
    async fn refusal_of_bracketed_ipv6_reaches_is_public_not_a_resolve_failure() {
        let err = vet(&url::Url::parse("https://[fe80::1]/x").unwrap(), false)
            .await
            .unwrap_err();
        let Refusal::FetchRefused(msg) = err else {
            panic!("expected FetchRefused")
        };
        assert_eq!(msg, "target resolves to a non-public address");
    }
}
