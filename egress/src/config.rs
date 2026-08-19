//! Boot configuration, read once from the environment. Fail loud on anything required and missing —
//! the proxy is launched by the deploy, not a human, so a bad value is a deploy bug.

use std::net::SocketAddr;

#[derive(Clone, Debug)]
pub struct Config {
    /// Bind address the proxy listens on (`UFO_EGRESS_BIND`:`UFO_EGRESS_PORT`).
    pub bind: SocketAddr,
    /// Externally-reachable base an off-cluster sandbox dials, carried through to the endpoint.
    pub public_url: Option<String>,
    /// The deploy secret that signs run/probe tokens — verified locally to scope caps and the rule
    /// cache. Not a customer credential; core `serve` re-verifies authoritatively.
    pub token_secret: Vec<u8>,
    /// The shared egress CA (cert, key) PEM the proxy signs per-host leaves from. Both unset mints an
    /// ephemeral per-process CA (single-node/local).
    pub ca_cert: Option<String>,
    pub ca_key: Option<String>,
    /// Core `serve`'s internal egress-control base URL and the shared bearer the proxy presents.
    pub control_url: String,
    pub control_token: String,
    /// The cache daemon a `Service` rule for a cached host relays to; unset disables the cache path.
    pub cache_daemon: Option<String>,
    /// The preview service a `Service` rule for the preview host relays to; unset answers every
    /// render request 502, since nothing else serves that host.
    pub preview_daemon: Option<String>,
    /// How long a SIGTERM lets live tunnels drain before stragglers are aborted — the deploy's
    /// `[serve] graceful_shutdown_seconds`, passed as `UFO_EGRESS_GRACEFUL_SHUTDOWN_SECONDS`.
    pub graceful_shutdown: std::time::Duration,
}

const DEFAULT_BIND_HOST: &str = "0.0.0.0";
const DEFAULT_GRACEFUL_SHUTDOWN_SECONDS: u64 = 30;

impl Config {
    pub fn from_env() -> Result<Config, String> {
        let host = std::env::var("UFO_EGRESS_BIND").unwrap_or_else(|_| DEFAULT_BIND_HOST.into());
        let port: u16 = match std::env::var("UFO_EGRESS_PORT") {
            Ok(v) => v.parse().map_err(|e| format!("UFO_EGRESS_PORT: {e}"))?,
            Err(_) => 0,
        };
        let bind: SocketAddr = format!("{host}:{port}")
            .parse()
            .map_err(|e| format!("UFO_EGRESS_BIND/PORT: {e}"))?;
        let graceful_shutdown_seconds: u64 =
            match std::env::var("UFO_EGRESS_GRACEFUL_SHUTDOWN_SECONDS") {
                Ok(v) => v
                    .parse()
                    .map_err(|e| format!("UFO_EGRESS_GRACEFUL_SHUTDOWN_SECONDS: {e}"))?,
                Err(_) => DEFAULT_GRACEFUL_SHUTDOWN_SECONDS,
            };
        Ok(Config {
            bind,
            public_url: std::env::var("UFO_EGRESS_PUBLIC_URL").ok(),
            token_secret: req("UFO_TOKEN_SECRET")?.into_bytes(),
            ca_cert: pem_or_file("UFO_EGRESS_CA_CERT")?,
            ca_key: pem_or_file("UFO_EGRESS_CA_KEY")?,
            control_url: req("UFO_EGRESS_CONTROL_URL")?,
            control_token: req("UFO_EGRESS_CONTROL_TOKEN")?,
            cache_daemon: daemon_address("UFO_EGRESS_CACHE_DAEMON")?,
            preview_daemon: daemon_address("UFO_EGRESS_PREVIEW_DAEMON")?,
            graceful_shutdown: std::time::Duration::from_secs(graceful_shutdown_seconds),
        })
    }
}

/// A relay daemon's `host:port`, kept as a name and resolved at connect time: one daemon is a
/// co-located address and the other a cluster DNS name whose address the proxy must not pin for the
/// life of the process. Validated here, so a malformed value dies at boot rather than degrading
/// silently to an unreachable daemon.
fn daemon_address(key: &str) -> Result<Option<String>, String> {
    let value = match std::env::var(key) {
        Ok(value) => value,
        Err(_) => return Ok(None),
    };
    let shaped = match value.rsplit_once(':') {
        Some((host, port)) => !host.is_empty() && port.parse::<u16>().is_ok(),
        None => false,
    };
    if !shaped {
        return Err(format!("{key} must be host:port, got {value:?}"));
    }
    Ok(Some(value))
}

fn req(key: &str) -> Result<String, String> {
    std::env::var(key).map_err(|_| format!("{key} is required"))
}

/// The PEM behind `<key>`, or the contents of the file `<key>_FILE` names. Hosted passes the value
/// straight from a secret; the dev rig mounts one shared CA and points both serve and the proxy at
/// its path, so a multi-line PEM never has to ride an env value.
fn pem_or_file(key: &str) -> Result<Option<String>, String> {
    if let Ok(path) = std::env::var(format!("{key}_FILE")) {
        return std::fs::read_to_string(&path)
            .map(Some)
            .map_err(|e| format!("{key}_FILE {path}: {e}"));
    }
    Ok(std::env::var(key).ok())
}

#[cfg(test)]
mod tests {
    use super::*;

    const KEY: &str = "UFO_EGRESS_DAEMON_ADDRESS_TEST";

    #[test]
    fn a_daemon_address_takes_a_dns_name_or_a_literal_and_refuses_anything_else() {
        for accepted in [
            "127.0.0.1:9110",
            "ufo-preview.ufo.svc.cluster.local:8930",
            "[::1]:9110",
        ] {
            std::env::set_var(KEY, accepted);
            assert_eq!(
                daemon_address(KEY).unwrap().as_deref(),
                Some(accepted),
                "{accepted} was rejected"
            );
        }
        for malformed in [
            "9110",
            ":9110",
            "ufo-preview:",
            "ufo-preview:http",
            "host:70000",
        ] {
            std::env::set_var(KEY, malformed);
            assert!(
                daemon_address(KEY).is_err(),
                "{malformed} was admitted as an address"
            );
        }
        std::env::remove_var(KEY);
        assert!(daemon_address(KEY).unwrap().is_none());
    }
}
