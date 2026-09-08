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
    /// How long a mirror's last *successful* upstream fetch keeps it fresh: inside the window a
    /// negotiation POST whose wants the mirror already holds serves it without another origin round
    /// trip; a want the mirror does not hold fetches regardless. Ref discovery always fetches,
    /// window or not — the `info/refs` GET and the protocol-v2 `ls-refs` POST alike. `0` fetches on
    /// every request.
    pub git_fresh_ttl_secs: u64,
    /// Soft ceiling for the cached `git-upload-pack` responses (`state_root/pack`); its eviction runs
    /// above it. `0` disables the pack cache, so every request runs the backend as before.
    pub pack_cache_bytes: u64,
    /// Soft ceiling for the cached LFS objects (`state_root/lfs`); its eviction runs above it. `0`
    /// disables the tier: batch answers relay untouched and content moves client to origin.
    pub lfs_cache_bytes: u64,
}

// Per-tier ceiling (the package cache and git mirrors are bounded separately), so the cache volume
// must hold both plus headroom.
const DEFAULT_DISK_LIMIT_BYTES: u64 = 4 * 1024 * 1024 * 1024;
const DEFAULT_PKG_DISK_LIMIT_BYTES: u64 = 8 * 1024 * 1024 * 1024;
// Long enough that one clone's negotiation rides the fetch its own ref discovery did; short enough
// that a lone negotiation is not far behind origin.
const DEFAULT_GIT_FRESH_TTL_SECS: u64 = 15;
// The hosted cache volume is 24Gi against 4 GiB of mirrors and 8 GiB of packages: exceeding an
// `emptyDir` `sizeLimit` evicts the proxy pod.
const DEFAULT_PACK_CACHE_MB: u64 = 4096;
const DEFAULT_LFS_CACHE_MB: u64 = 4096;

// Public registries, distro archives, and artifact CDNs used by sandbox builds.
const DEFAULT_PKG_HOSTS: &[&str] = &[
    "registry.npmjs.org",
    "pypi.org",
    "files.pythonhosted.org",
    "crates.io",
    "static.crates.io",
    "index.crates.io",
    "proxy.golang.org",
    "sum.golang.org",
    "rubygems.org",
    "index.rubygems.org",
    "api.rubygems.org",
    "archive.ubuntu.com",
    "security.ubuntu.com",
    "ports.ubuntu.com",
    "deb.debian.org",
    "security.debian.org",
    "cdn-fastly.deb.debian.org",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "github-releases.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "codeload.github.com",
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
            git_fresh_ttl_secs: match std::env::var("UFO_CACHE_GIT_FRESH_TTL_SECS") {
                Ok(v) => v
                    .parse()
                    .map_err(|e| format!("UFO_CACHE_GIT_FRESH_TTL_SECS: {e}"))?,
                Err(_) => DEFAULT_GIT_FRESH_TTL_SECS,
            },
            pack_cache_bytes: match std::env::var("UFO_CACHE_PACK_CACHE_MB") {
                Ok(v) => v
                    .parse::<u64>()
                    .map_err(|e| format!("UFO_CACHE_PACK_CACHE_MB: {e}"))?
                    .saturating_mul(1024 * 1024),
                Err(_) => DEFAULT_PACK_CACHE_MB.saturating_mul(1024 * 1024),
            },
            lfs_cache_bytes: match std::env::var("UFO_CACHE_LFS_CACHE_MB") {
                Ok(v) => v
                    .parse::<u64>()
                    .map_err(|e| format!("UFO_CACHE_LFS_CACHE_MB: {e}"))?
                    .saturating_mul(1024 * 1024),
                Err(_) => DEFAULT_LFS_CACHE_MB.saturating_mul(1024 * 1024),
            },
        })
    }
}

fn req(key: &str) -> Result<String, String> {
    std::env::var(key).map_err(|_| format!("{key} is required"))
}
