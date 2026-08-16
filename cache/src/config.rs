use std::net::SocketAddr;
use std::path::PathBuf;

/// Daemon configuration, read once from the environment at boot. Fail loud on anything missing or
/// unparseable — the daemon is launched by the deploy, not a human, so a bad value is a deploy bug.
#[derive(Clone, Debug)]
pub struct Config {
    /// Loopback address the proxy relays to.
    pub listen: SocketAddr,
    /// Root of the on-disk mirrors and cache, one subtree per principal.
    pub state_root: PathBuf,
    /// Control-plane base URL for the credential callback (e.g. http://127.0.0.1:8080).
    pub control_url: String,
    /// Shared secret presented to the control plane on every callback.
    pub control_token: String,
    /// Soft ceiling for the whole state tree; eviction runs above it.
    pub disk_limit_bytes: u64,
    /// Scheme used to reach upstreams. `https` in every real deploy; overridable so tests can point
    /// a strategy at a local plaintext origin.
    pub upstream_scheme: String,
    /// The hosts the daemon will mirror. Any other host in a request path is refused, so a sandbox
    /// cannot steer the cache at a private or in-cluster address. `github.com` in every real deploy.
    pub allowed_git_hosts: Vec<String>,
}

// Per-tier ceiling (host cache and git mirrors are bounded separately), so the cache volume must
// hold roughly twice this plus headroom.
const DEFAULT_DISK_LIMIT_BYTES: u64 = 4 * 1024 * 1024 * 1024;

impl Config {
    pub fn from_env() -> Result<Self, String> {
        Ok(Self {
            listen: req("UFO_CACHE_LISTEN")?
                .parse()
                .map_err(|e| format!("UFO_CACHE_LISTEN: {e}"))?,
            state_root: PathBuf::from(req("UFO_CACHE_STATE")?),
            control_url: req("UFO_CACHE_CONTROL_URL")?,
            control_token: req("UFO_CACHE_CONTROL_TOKEN")?,
            disk_limit_bytes: match std::env::var("UFO_CACHE_DISK_LIMIT_BYTES") {
                Ok(v) => v
                    .parse()
                    .map_err(|e| format!("UFO_CACHE_DISK_LIMIT_BYTES: {e}"))?,
                Err(_) => DEFAULT_DISK_LIMIT_BYTES,
            },
            upstream_scheme: std::env::var("UFO_CACHE_UPSTREAM_SCHEME")
                .unwrap_or_else(|_| "https".into()),
            allowed_git_hosts: std::env::var("UFO_CACHE_GIT_HOSTS")
                .map(|v| v.split(',').map(|h| h.trim().to_string()).collect())
                .unwrap_or_else(|_| vec!["github.com".into()]),
        })
    }
}

fn req(key: &str) -> Result<String, String> {
    std::env::var(key).map_err(|_| format!("{key} is required"))
}
