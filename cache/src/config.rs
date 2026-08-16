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
    /// Soft ceiling for the git mirror tree (`state_root/git`); its eviction runs above it. The
    /// package tree is a sibling bounded separately by `pkg_disk_limit_bytes`.
    pub disk_limit_bytes: u64,
    /// Scheme used to reach upstreams. `https` in every real deploy; overridable so tests can point
    /// a strategy at a local plaintext origin.
    pub upstream_scheme: String,
    /// The hosts the daemon will mirror. Any other host in a request path is refused, so a sandbox
    /// cannot steer the cache at a private or in-cluster address. `github.com` in every real deploy.
    pub allowed_git_hosts: Vec<String>,
    /// The package registries and download CDNs the daemon will forward-cache. Any other host in a
    /// `/pkg/` request is refused, so the cache cannot be pointed at a private or in-cluster address.
    pub allowed_pkg_hosts: Vec<String>,
    /// Soft ceiling for the package cache tree; its eviction runs above it. Separate from the git
    /// ceiling because the package cache is shared across every workspace and grows differently.
    pub pkg_disk_limit_bytes: u64,
}

// Per-tier ceiling (the package cache and git mirrors are bounded separately), so the cache volume
// must hold both plus headroom.
const DEFAULT_DISK_LIMIT_BYTES: u64 = 4 * 1024 * 1024 * 1024;
const DEFAULT_PKG_DISK_LIMIT_BYTES: u64 = 8 * 1024 * 1024 * 1024;

// Public registries and their download CDNs for Node, Python, Rust, and Go — the build/test time
// sinks. More ecosystems (apt, apk, RubyGems, Maven) drop in through UFO_CACHE_PKG_HOSTS.
const DEFAULT_PKG_HOSTS: &[&str] = &[
    "registry.npmjs.org",
    "pypi.org",
    "files.pythonhosted.org",
    "crates.io",
    "static.crates.io",
    "index.crates.io",
    "proxy.golang.org",
    "sum.golang.org",
];

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
            allowed_pkg_hosts: std::env::var("UFO_CACHE_PKG_HOSTS")
                .map(|v| v.split(',').map(|h| h.trim().to_string()).collect())
                .unwrap_or_else(|_| DEFAULT_PKG_HOSTS.iter().map(|h| (*h).to_string()).collect()),
            pkg_disk_limit_bytes: match std::env::var("UFO_CACHE_PKG_DISK_LIMIT_BYTES") {
                Ok(v) => v
                    .parse()
                    .map_err(|e| format!("UFO_CACHE_PKG_DISK_LIMIT_BYTES: {e}"))?,
                Err(_) => DEFAULT_PKG_DISK_LIMIT_BYTES,
            },
        })
    }
}

fn req(key: &str) -> Result<String, String> {
    std::env::var(key).map_err(|_| format!("{key} is required"))
}
