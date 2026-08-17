use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use axum::body::{Body, Bytes};
use axum::http::{HeaderMap, Method, StatusCode};
use axum::response::{IntoResponse, Response};
use base64::Engine;
use filetime::{set_file_mtime, FileTime};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::process::{Child, Command};
use tokio::sync::Mutex;

use crate::cgi::{read_head, response_from_cgi, CgiHead, READ_CHUNK};
use crate::config::Config;
use crate::creds::{CredentialClient, Resolved};
use crate::durable::Durable;
use crate::inuse::InUse;

/// A warm mirror is re-snapshotted to the durable tier at most this often.
const SNAPSHOT_INTERVAL: Duration = Duration::from_secs(600);
/// The mirror tree is swept back under its ceiling at most this often.
const MIRROR_SWEEP_INTERVAL: Duration = Duration::from_secs(120);
/// The pack cache tree is swept back under its ceiling at most this often.
const PACK_SWEEP_INTERVAL: Duration = Duration::from_secs(120);
const MAX_UPLOAD_PACK_BYTES: usize = 128 * 1024 * 1024;
/// Bound on a relayed git-lfs API body: batch and lock JSON, never object content — content moves
/// on the hrefs the batch response names, straight between the client and the origin's storage.
const MAX_LFS_BODY_BYTES: usize = 16 * 1024 * 1024;
/// A single cached upload-pack response never exceeds this, whatever the ceiling: one outsized pack
/// must not be able to evict every other entry to fit.
const MAX_CACHED_PACK_BYTES: u64 = 512 * 1024 * 1024;
/// A single cached LFS object never exceeds this, whatever the ceiling; content past it streams
/// through uncached.
const MAX_CACHED_LFS_BYTES: u64 = 512 * 1024 * 1024;
/// The daemon-owned segment a rewritten batch href lands on. The LFS API defines nothing under it,
/// so shadowing it costs no origin surface.
const LFS_CONTENT_ROUTE: &str = "objects/content/";
/// How many redirects a content fetch follows, each hop re-vetted against the href host guard.
const LFS_CONTENT_HOPS: usize = 4;
/// A `.writing` temp older than this had no writer for an hour — far past any pack generation — so the
/// sweep reclaims it as a crashed capture's orphan. Mirrors the package cache's grace.
const PACK_WRITING_ORPHAN_GRACE_SECS: u64 = 3600;

/// A temp path unique to this capture: `<path>.<pid>.<seq>.writing`, so two concurrent captures of
/// one key never share a file. Same convention as `pkg::writing_temp` and `durable::restore_temp`.
static WRITE_SEQ: AtomicU64 = AtomicU64::new(0);

fn writing_temp(path: &Path) -> PathBuf {
    let seq = WRITE_SEQ.fetch_add(1, Ordering::Relaxed);
    let mut name = path.as_os_str().to_owned();
    name.push(format!(".{}.{}.writing", std::process::id(), seq));
    PathBuf::from(name)
}

#[derive(Clone, Copy)]
enum Endpoint {
    Info,
    /// A protocol-v2 `ls-refs` POST. It arrives on the `git-upload-pack` path but it is the ref
    /// advertisement, which protocol v2 moves off the `info/refs` GET.
    LsRefs,
    UploadPack,
}

#[derive(Default)]
struct MirrorState {
    last_snapshot: Option<Instant>,
    /// When this mirror last completed a successful upstream fetch (or clone). The bounded-freshness
    /// window is measured from here, per mirror path — and the path carries the principal, so one
    /// principal's fetch never marks another's mirror fresh.
    last_fetch: Option<Instant>,
}

/// Serves the git smart-HTTP fetch protocol from per-principal bare mirrors. All wire-protocol work
/// is done by the `git` binary (`clone --mirror`, `fetch`, `http-backend`, `bundle`); this type owns
/// mirror freshness, isolation, credential injection, durable snapshot/restore, and bounding the
/// mirror tree's disk.
pub struct GitStrategy {
    state_root: PathBuf,
    pack_root: PathBuf,
    lfs_root: PathBuf,
    creds: Arc<CredentialClient>,
    scheme: String,
    /// Relays git-lfs API calls and fetches LFS content from the origin. Redirects are never
    /// followed silently: an API 3xx passes through to the client, and a content 3xx is re-checked
    /// against the same host guard as the href it came from.
    http: reqwest::Client,
    allowed_hosts: Vec<String>,
    durable: Arc<Durable>,
    mirror_limit: u64,
    pack_limit: u64,
    lfs_limit: u64,
    fresh_ttl: Duration,
    mirrors: Mutex<HashMap<PathBuf, Arc<Mutex<MirrorState>>>>,
    last_sweep: Mutex<Option<Instant>>,
    last_pack_sweep: Mutex<Option<Instant>>,
    last_lfs_sweep: Mutex<Option<Instant>>,
    in_use: InUse,
}

impl GitStrategy {
    /// The mirror tree and the pack cache are sibling roots under the state root, bounded and swept
    /// separately: a mirror is re-clonable and a cached pack is re-generable, but a pack replays whole
    /// while a mirror serves many different requests, so packs must not charge against the mirror
    /// ceiling (nor the reverse).
    pub fn new(config: &Config, creds: Arc<CredentialClient>, durable: Arc<Durable>) -> Self {
        Self {
            state_root: config.state_root.join("git"),
            pack_root: config.state_root.join("pack"),
            lfs_root: config.state_root.join("lfs"),
            creds,
            scheme: config.upstream_scheme.clone(),
            http: reqwest::Client::builder()
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .expect("build lfs relay client"),
            allowed_hosts: config.allowed_git_hosts.clone(),
            durable,
            mirror_limit: config.disk_limit_bytes,
            pack_limit: config.pack_cache_bytes,
            lfs_limit: config.lfs_cache_bytes,
            fresh_ttl: Duration::from_secs(config.git_fresh_ttl_secs),
            mirrors: Mutex::new(HashMap::new()),
            last_sweep: Mutex::new(None),
            last_pack_sweep: Mutex::new(None),
            last_lfs_sweep: Mutex::new(None),
            in_use: InUse::default(),
        }
    }

    /// `tail` is the request path after `/git/<host>/`, e.g. `octocat/hello/info/refs`.
    #[allow(clippy::too_many_arguments)]
    pub async fn handle(
        &self,
        method: &Method,
        host: &str,
        tail: &str,
        query: Option<&str>,
        headers: &HeaderMap,
        body: Bytes,
        workspace: &str,
        user: &str,
    ) -> Response {
        // The git-lfs API rides the same remote URL as the wire protocol, so its calls arrive
        // here: `<repo>.git/info/lfs/...`, JSON both ways. The daemon relays them with the
        // principal's credential — and when the LFS tier is on, it rewrites each batch answer's
        // download href onto its own content route, so the objects it fetches once (verified
        // against their oid) serve every later pull from disk. Everything else — uploads, locks,
        // verify — relays untouched, and object content is never read from a client-named URL:
        // a miss re-batches against the allowlisted origin itself.
        if let Some((repo, lfs_path)) = split_lfs(tail) {
            let resolved = match self.creds.resolve(workspace, user, host, &repo).await {
                Ok(r) => r,
                Err(e) => {
                    tracing::warn!(error = %e, host, repo, "credential resolve failed");
                    return (StatusCode::BAD_GATEWAY, "credential resolve failed").into_response();
                }
            };
            if let Some(oid) = lfs_path.strip_prefix(LFS_CONTENT_ROUTE) {
                return self
                    .serve_lfs_content(host, &repo, oid, query, &resolved)
                    .await;
            }
            if lfs_path == "objects/batch" && self.lfs_limit > 0 {
                return self
                    .serve_lfs_batch(method, host, &repo, headers, body, &resolved)
                    .await;
            }
            return self
                .forward_lfs(
                    method, host, &repo, lfs_path, query, headers, body, &resolved,
                )
                .await;
        }

        let Some((endpoint, repo)) = classify(tail, &body) else {
            return (StatusCode::NOT_FOUND, "unsupported git path").into_response();
        };
        let resolved = match self.creds.resolve(workspace, user, host, &repo).await {
            Ok(r) => r,
            Err(e) => {
                tracing::warn!(error = %e, host, repo, "credential resolve failed");
                return (StatusCode::BAD_GATEWAY, "credential resolve failed").into_response();
            }
        };

        let host_root = self
            .state_root
            .join(sanitize(&resolved.principal))
            .join(sanitize(host));
        let mirror = host_root.join(format!("{repo}.git"));

        // Hold the mirror in use across both the fetch and the serve below, so the LRU sweep cannot
        // evict the directory this request is reading. Dropped when the response is built.
        let _in_use = self.in_use.guard(&mirror);

        // Ref discovery always refreshes the mirror from origin — the `info/refs` GET and the
        // protocol-v2 `ls-refs` POST alike; only the negotiation POSTs that follow it may be served
        // inside the freshness window, and only when the mirror already holds every object they
        // want. So a clone that starts after a push sees the new head, and the negotiation it then
        // sends costs no second upstream round trip. Across replicas the requests are separate
        // connections, so a negotiation can reach a replica whose window is open on an older mirror
        // than the advertising replica's: the want that mirror cannot back forces its fetch, and the
        // clone completes there too.
        let wants = match endpoint {
            Endpoint::UploadPack => negotiation_wants(headers, &body),
            Endpoint::Info | Endpoint::LsRefs => None,
        };
        if let Err(e) = self
            .ensure_fresh(endpoint, wants.as_deref(), &mirror, host, &repo, &resolved)
            .await
        {
            tracing::warn!(error = %e, host, repo, "mirror ensure failed");
            return (StatusCode::BAD_GATEWAY, "upstream unavailable").into_response();
        }

        let path_info = match endpoint {
            Endpoint::Info => format!("/{repo}.git/info/refs"),
            Endpoint::LsRefs | Endpoint::UploadPack => format!("/{repo}.git/git-upload-pack"),
        };
        // Only the `git-upload-pack` endpoint is cached. The `info/refs` GET is a few hundred bytes
        // the backend builds from the refs it just refreshed, so replaying it would save nothing worth
        // a lookup. (Under protocol v2 a client's `ls-refs` arrives as an upload-pack POST, so that
        // one is cached — keyed by the same ref-state fingerprint, so moved refs miss.)
        let served = match endpoint {
            Endpoint::LsRefs | Endpoint::UploadPack if self.pack_limit > 0 => {
                self.serve_pack_cached(
                    &host_root, &mirror, &path_info, method, query, headers, body, &resolved, host,
                    &repo,
                )
                .await
            }
            _ => match self
                .spawn_backend(&host_root, &path_info, method, query, headers, &body)
                .await
            {
                Ok(child) => response_from_cgi(child).await,
                Err(e) => Err(e),
            },
        };
        match served {
            Ok(resp) => resp,
            Err(e) => {
                tracing::warn!(error = %e, "http-backend failed");
                (StatusCode::BAD_GATEWAY, "git backend failed").into_response()
            }
        }
    }

    /// Relay one git-lfs API call to the origin — the principal's credential injected on the way
    /// out, the origin's status, headers, and body streamed back untouched. The host was
    /// allowlisted and the repo made safe before this is reached, so the URL built here can only
    /// name an approved origin.
    #[allow(clippy::too_many_arguments)]
    async fn forward_lfs(
        &self,
        method: &Method,
        host: &str,
        repo: &str,
        lfs_path: &str,
        query: Option<&str>,
        headers: &HeaderMap,
        body: Bytes,
        resolved: &Resolved,
    ) -> Response {
        if body.len() > MAX_LFS_BODY_BYTES {
            return (StatusCode::PAYLOAD_TOO_LARGE, "lfs body too large").into_response();
        }
        let mut url = format!("{}://{host}/{repo}.git/info/lfs/{lfs_path}", self.scheme);
        if let Some(q) = query {
            url.push('?');
            url.push_str(q);
        }
        let mut request = self.http.request(method.clone(), &url).body(body);
        for name in ["content-type", "accept"] {
            if let Some(value) = header(headers, name) {
                request = request.header(name, value);
            }
        }
        let upstream = match with_lfs_auth(request, resolved).send().await {
            Ok(r) => r,
            Err(e) => {
                tracing::warn!(error = %e, host, repo, "lfs relay failed");
                return (StatusCode::BAD_GATEWAY, "lfs upstream unavailable").into_response();
            }
        };
        let mut builder = Response::builder().status(upstream.status());
        for (name, value) in upstream.headers() {
            if !matches!(
                name.as_str(),
                "connection" | "keep-alive" | "transfer-encoding" | "content-length"
            ) {
                builder = builder.header(name, value);
            }
        }
        builder
            .body(Body::from_stream(upstream.bytes_stream()))
            .unwrap_or_else(|_| {
                (StatusCode::BAD_GATEWAY, "lfs relay response failed").into_response()
            })
    }

    /// Relay a batch call like `forward_lfs`, but rewrite each download href in the answer onto
    /// this daemon's content route, so the client pulls objects through the cache. An answer that
    /// cannot be rewritten — an error status, a non-basic transfer, no Host to build a base from —
    /// relays untouched: correctness never rides on the rewrite, only the caching does.
    async fn serve_lfs_batch(
        &self,
        method: &Method,
        host: &str,
        repo: &str,
        headers: &HeaderMap,
        body: Bytes,
        resolved: &Resolved,
    ) -> Response {
        if body.len() > MAX_LFS_BODY_BYTES {
            return (StatusCode::PAYLOAD_TOO_LARGE, "lfs body too large").into_response();
        }
        let url = format!("{}://{host}/{repo}.git/info/lfs/objects/batch", self.scheme);
        let mut request = self.http.request(method.clone(), &url).body(body);
        for name in ["content-type", "accept"] {
            if let Some(value) = header(headers, name) {
                request = request.header(name, value);
            }
        }
        let upstream = match with_lfs_auth(request, resolved).send().await {
            Ok(r) => r,
            Err(e) => {
                tracing::warn!(error = %e, host, repo, "lfs relay failed");
                return (StatusCode::BAD_GATEWAY, "lfs upstream unavailable").into_response();
            }
        };
        let status = upstream.status();
        let kept: Vec<(String, String)> = upstream
            .headers()
            .iter()
            .filter(|(name, _)| {
                !matches!(
                    name.as_str(),
                    "connection" | "keep-alive" | "transfer-encoding" | "content-length"
                )
            })
            .filter_map(|(name, value)| {
                value
                    .to_str()
                    .ok()
                    .map(|v| (name.as_str().to_string(), v.to_string()))
            })
            .collect();
        let answer = match read_capped(upstream, MAX_LFS_BODY_BYTES).await {
            Ok(bytes) => bytes,
            Err(e) => {
                tracing::warn!(error = %e, host, repo, "lfs batch read failed");
                return (StatusCode::BAD_GATEWAY, "lfs batch response too large").into_response();
            }
        };
        let rewritten = (status == StatusCode::OK)
            .then(|| public_base(headers))
            .flatten()
            .and_then(|base| rewrite_batch(&answer, &base, host, repo));
        let mut builder = Response::builder().status(status);
        for (name, value) in kept {
            builder = builder.header(name, value);
        }
        builder
            .body(Body::from(rewritten.map(Bytes::from).unwrap_or(answer)))
            .unwrap_or_else(|_| {
                (StatusCode::BAD_GATEWAY, "lfs relay response failed").into_response()
            })
    }

    /// Serve one LFS object from the per-principal content tier, restoring it from the durable
    /// tier or fetching it from the origin on a miss. The client reached this route through a
    /// rewritten batch answer, but nothing here trusts what it names beyond the oid and size: the
    /// fetch re-batches against the allowlisted origin with the principal's own credential, and
    /// bytes must hash to the oid before they are committed or served — restored bytes held to
    /// the same proof as fetched ones. What the origin supplies is snapshotted to the durable
    /// tier off the response path, so a rolled pod restores instead of re-downloading.
    async fn serve_lfs_content(
        &self,
        host: &str,
        repo: &str,
        oid: &str,
        query: Option<&str>,
        resolved: &Resolved,
    ) -> Response {
        if !is_oid(oid) {
            return (StatusCode::NOT_FOUND, "not an lfs oid").into_response();
        }
        let Some(size) = lfs_size(query) else {
            return (StatusCode::BAD_REQUEST, "missing size").into_response();
        };
        let dir = self.lfs_root.join(sanitize(&resolved.principal));
        let body_path = dir.join(format!("{oid}.body"));
        let _in_use = self.in_use.guard(&body_path);
        let meta = PackMeta {
            status: StatusCode::OK.as_u16(),
            headers: vec![(
                "content-type".to_string(),
                "application/octet-stream".to_string(),
            )],
        };
        if let Some(resp) = serve_pack_file(&meta, &body_path, "HIT").await {
            return resp;
        }
        if self
            .restore_lfs(&dir, &body_path, oid, size, resolved)
            .await
        {
            if let Some(resp) = serve_pack_file(&meta, &body_path, "HIT").await {
                return resp;
            }
        }
        let upstream = match self.fetch_lfs_object(host, repo, oid, size, resolved).await {
            Ok(r) => r,
            Err(e) => {
                tracing::warn!(error = %e, host, repo, oid, "lfs content fetch failed");
                return (StatusCode::BAD_GATEWAY, "lfs content unavailable").into_response();
            }
        };
        // Content the tier could never keep streams through uncached: correct for the client, no
        // disk for the daemon. The client verifies the oid itself, as it does against any server.
        if size > MAX_CACHED_LFS_BYTES.min(self.lfs_limit) {
            return Response::builder()
                .status(StatusCode::OK)
                .header("content-type", "application/octet-stream")
                .header("content-length", size)
                .header("x-ufo-cache", "MISS")
                .body(Body::from_stream(upstream.bytes_stream()))
                .unwrap_or_else(|_| {
                    (StatusCode::BAD_GATEWAY, "lfs relay response failed").into_response()
                });
        }
        // Reclaim space before a write may add to the tree.
        self.maybe_sweep_lfs().await;
        if let Err(e) = tokio::fs::create_dir_all(&dir).await {
            tracing::warn!(error = %e, "create lfs cache dir failed");
            return (StatusCode::BAD_GATEWAY, "lfs cache unavailable").into_response();
        }
        let tmp = writing_temp(&body_path);
        if let Err(e) = capture_lfs(upstream, &tmp, oid, size).await {
            let _ = tokio::fs::remove_file(&tmp).await;
            tracing::warn!(error = %e, host, repo, oid, "lfs content capture failed");
            return (StatusCode::BAD_GATEWAY, "lfs content mismatch").into_response();
        }
        let serve_path = if tokio::fs::rename(&tmp, &body_path).await.is_ok() {
            let durable = self.durable.clone();
            let key = lfs_key(&resolved.principal, oid);
            let snapshot = body_path.clone();
            tokio::spawn(async move {
                durable.put_file(&key, &snapshot).await;
            });
            body_path
        } else {
            tmp.clone()
        };
        let resp = serve_pack_file(&meta, &serve_path, "MISS").await;
        if serve_path == tmp {
            let _ = tokio::fs::remove_file(&tmp).await;
        }
        resp.unwrap_or_else(|| (StatusCode::BAD_GATEWAY, "lfs serve failed").into_response())
    }

    /// Rebuild a missing object from the durable tier instead of the origin. The restored bytes
    /// must hash to the oid and total the declared size before they are trusted: a corrupt durable
    /// object is discarded and the caller fetches the origin as if it were never there — and the
    /// snapshot that fetch spawns overwrites the bad copy.
    async fn restore_lfs(
        &self,
        dir: &Path,
        body_path: &Path,
        oid: &str,
        size: u64,
        resolved: &Resolved,
    ) -> bool {
        if matches!(*self.durable, Durable::Off) {
            return false;
        }
        // Reclaim space before a write may add to the tree: restore-only traffic on a rolled pod
        // must bound the tier exactly as origin fetches do.
        self.maybe_sweep_lfs().await;
        if tokio::fs::create_dir_all(dir).await.is_err() {
            return false;
        }
        let tmp = writing_temp(body_path);
        if !self
            .durable
            .get_file(&lfs_key(&resolved.principal, oid), &tmp)
            .await
        {
            return false;
        }
        if !lfs_file_matches(&tmp, oid, size).await {
            tracing::warn!(oid, "durable lfs object failed verification");
            let _ = tokio::fs::remove_file(&tmp).await;
            return false;
        }
        tokio::fs::rename(&tmp, body_path).await.is_ok()
    }

    /// One streaming response for an LFS object, negotiated fresh with the origin: this daemon's
    /// own batch call, then the download href it names. Without the cache the client fetches the
    /// href through egress rules that stop at private addresses; the daemon takes that fetch over,
    /// so it applies the same bar — every hop's host allowlisted or globally routable — and an
    /// origin's batch answer can never steer the daemon's network position at the cluster.
    async fn fetch_lfs_object(
        &self,
        host: &str,
        repo: &str,
        oid: &str,
        size: u64,
        resolved: &Resolved,
    ) -> Result<reqwest::Response, String> {
        let url = format!("{}://{host}/{repo}.git/info/lfs/objects/batch", self.scheme);
        let batch = serde_json::json!({
            "operation": "download",
            "transfers": ["basic"],
            "objects": [{"oid": oid, "size": size}],
        });
        let request = self
            .http
            .post(&url)
            .header("content-type", "application/vnd.git-lfs+json")
            .header("accept", "application/vnd.git-lfs+json")
            .body(batch.to_string());
        let response = with_lfs_auth(request, resolved)
            .send()
            .await
            .map_err(|e| format!("lfs batch: {e}"))?;
        if response.status() != StatusCode::OK {
            return Err(format!("lfs batch answered {}", response.status()));
        }
        let answer = read_capped(response, MAX_LFS_BODY_BYTES).await?;
        let doc: serde_json::Value =
            serde_json::from_slice(&answer).map_err(|e| format!("lfs batch json: {e}"))?;
        let object = doc
            .get("objects")
            .and_then(|o| o.as_array())
            .and_then(|objects| {
                objects
                    .iter()
                    .find(|o| o.get("oid").and_then(|v| v.as_str()) == Some(oid))
            })
            .ok_or("lfs batch names no such object")?;
        if let Some(error) = object.get("error") {
            return Err(format!("lfs batch refused the object: {error}"));
        }
        let action = object
            .get("actions")
            .and_then(|a| a.get("download"))
            .ok_or("lfs batch offers no download")?;
        let mut target = action
            .get("href")
            .and_then(|h| h.as_str())
            .ok_or("lfs download has no href")?
            .to_string();
        let mut extra: Vec<(String, String)> = action
            .get("header")
            .and_then(|h| h.as_object())
            .map(|map| {
                map.iter()
                    .filter_map(|(name, value)| {
                        value.as_str().map(|v| (name.clone(), v.to_string()))
                    })
                    .collect()
            })
            .unwrap_or_default();
        for _ in 0..LFS_CONTENT_HOPS {
            let (client, url) = self.lfs_content_client(&target).await?;
            let mut request = client.get(url.clone());
            for (name, value) in &extra {
                request = request.header(name, value);
            }
            let response = request
                .send()
                .await
                .map_err(|e| format!("lfs content: {e}"))?;
            if response.status().is_redirection() {
                let location = response
                    .headers()
                    .get("location")
                    .and_then(|v| v.to_str().ok())
                    .ok_or("lfs redirect without location")?;
                let next = url
                    .join(location)
                    .map_err(|e| format!("lfs redirect target: {e}"))?;
                // The batch's headers are the first hop's grant; they must not leak to a host the
                // redirect chose.
                if next.host_str() != url.host_str() {
                    extra.clear();
                }
                target = next.to_string();
                continue;
            }
            if response.status() != StatusCode::OK {
                return Err(format!("lfs content answered {}", response.status()));
            }
            return Ok(response);
        }
        Err("lfs content redirected too many times".into())
    }

    /// A client pinned to the href host's own vetted address. The host passes when it is an
    /// allowlisted git host, or when every address it resolves to is globally routable — so a
    /// batch answer cannot point the daemon at loopback, a private range, or the cluster, and the
    /// pinned resolution is the one that was checked.
    async fn lfs_content_client(
        &self,
        target: &str,
    ) -> Result<(reqwest::Client, reqwest::Url), String> {
        let url = reqwest::Url::parse(target).map_err(|e| format!("lfs href: {e}"))?;
        if !matches!(url.scheme(), "http" | "https") {
            return Err(format!("lfs href scheme {}", url.scheme()));
        }
        let host = url.host_str().ok_or("lfs href has no host")?.to_string();
        let port = url.port_or_known_default().ok_or("lfs href has no port")?;
        let named = match url.port() {
            Some(port) => format!("{host}:{port}"),
            None => host.clone(),
        };
        let addrs: Vec<std::net::SocketAddr> = tokio::net::lookup_host((host.as_str(), port))
            .await
            .map_err(|e| format!("resolve {host}: {e}"))?
            .collect();
        let allowlisted = self.allowed_hosts.iter().any(|h| *h == named || *h == host);
        let addr = if allowlisted {
            *addrs
                .first()
                .ok_or_else(|| format!("{host} resolves to nothing"))?
        } else {
            if addrs.is_empty() || !addrs.iter().all(|a| globally_routable(&a.ip())) {
                return Err(format!("{host} does not resolve to public addresses"));
            }
            addrs[0]
        };
        let client = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .resolve(&host, addr)
            .build()
            .map_err(|e| format!("build lfs content client: {e}"))?;
        Ok((client, url))
    }

    async fn maybe_sweep_lfs(&self) {
        {
            let mut last = self.last_lfs_sweep.lock().await;
            if last.is_some_and(|t| t.elapsed() < PACK_SWEEP_INTERVAL) {
                return;
            }
            *last = Some(Instant::now());
        }
        let root = self.lfs_root.clone();
        let limit = self.lfs_limit;
        let in_use = self.in_use.clone();
        tokio::task::spawn_blocking(move || sweep_packs(&root, limit, &in_use));
    }

    async fn ensure_fresh(
        &self,
        endpoint: Endpoint,
        wants: Option<&[String]>,
        mirror: &Path,
        host: &str,
        repo: &str,
        resolved: &Resolved,
    ) -> Result<(), String> {
        // An authenticated mirror must never be served without a successful fetch inside the
        // freshness window: a token the org has since revoked would otherwise keep reading a cached
        // private history indefinitely. An anonymous mirror (public repo) may serve stale through an
        // upstream blip.
        //
        // What the window bounds is the *object refresh*, not the authorization decision: every
        // request still resolves its credential through the control plane
        // (`/internal/git-credential`), which is what decides whether this principal may read this
        // repo at all. Residual risk: an upstream revocation that the control plane keeps granting is
        // honoured up to `fresh_ttl` seconds late on the upload-pack POSTs of a clone whose ref
        // discovery was still authorized. `UFO_CACHE_GIT_FRESH_TTL_SECS=0` removes the window.
        let authenticated = resolved.token.is_some();
        let entry = {
            let mut map = self.mirrors.lock().await;
            map.entry(mirror.to_path_buf())
                .or_insert_with(|| Arc::new(Mutex::new(MirrorState::default())))
                .clone()
        };
        let mut state = entry.lock().await;
        let now = Instant::now();
        // Reclaim space before a write may add to the tree.
        self.maybe_sweep_mirrors().await;
        if !mirror.join("HEAD").exists() {
            // A cold or evicted mirror is never served from the window: it must clone, or restore from
            // the durable tier and then fetch, before this request reads a single object from it.
            if self.restore_mirror(mirror, host, repo, resolved).await? {
                // A restore trusts a bundle taken under a past authorization; re-confirm access.
                match self.fetch_mirror(mirror, resolved).await {
                    Ok(()) => state.last_fetch = Some(now),
                    Err(e) => {
                        if authenticated {
                            let _ = tokio::fs::remove_dir_all(mirror).await;
                            return Err(format!("restore re-auth failed: {e}"));
                        }
                        tracing::warn!(error = %e, "restored mirror serves stale (anonymous)");
                    }
                }
            } else {
                self.clone_mirror(mirror, host, repo, resolved).await?;
                state.last_fetch = Some(now);
                self.spawn_snapshot(mirror, resolved.principal.clone(), host, repo);
            }
            state.last_snapshot = Some(now);
        } else if self.is_fresh(endpoint, &state) && mirror_holds(mirror, wants).await {
            tracing::debug!(
                host,
                repo,
                "mirror inside the freshness window holds the wants; skipping fetch"
            );
        } else {
            match self.fetch_mirror(mirror, resolved).await {
                Ok(()) => {
                    state.last_fetch = Some(now);
                    if state
                        .last_snapshot
                        .is_none_or(|t| t.elapsed() >= SNAPSHOT_INTERVAL)
                    {
                        self.spawn_snapshot(mirror, resolved.principal.clone(), host, repo);
                        state.last_snapshot = Some(now);
                    }
                }
                Err(e) if authenticated => {
                    return Err(format!("mirror refresh failed (authenticated): {e}"));
                }
                Err(e) => tracing::warn!(error = %e, "mirror refresh failed; serving existing"),
            }
        }
        Ok(())
    }

    /// True when the freshness window applies to this request: a negotiation POST whose mirror
    /// completed a successful fetch inside the window. The caller still requires the mirror to hold
    /// every object the negotiation wants (`mirror_holds`) before it skips the fetch — the window
    /// alone proves the mirror is recent, not that it is the mirror whose advertisement this
    /// negotiation answers, and across replicas those differ.
    ///
    /// Both forms of ref discovery are excluded — the `info/refs` GET and the protocol-v2 `ls-refs`
    /// POST — because that is where a client learns which head to ask for: a window that covered
    /// either would advertise the previous head to a clone that started after the push, and the client
    /// would check that head out with no error. Skipping only the negotiation still collapses the
    /// refreshes of one clone into one. A zero TTL is never fresh, so every request fetches — the
    /// behaviour before the window existed.
    fn is_fresh(&self, endpoint: Endpoint, state: &MirrorState) -> bool {
        matches!(endpoint, Endpoint::UploadPack)
            && !self.fresh_ttl.is_zero()
            && state
                .last_fetch
                .is_some_and(|t| t.elapsed() < self.fresh_ttl)
    }

    /// Rate-limited, off the request path: evict least-recently-used bare mirrors when the mirror
    /// tree exceeds its ceiling. Mirrors are re-clonable (and restorable from the durable snapshot),
    /// so eviction is safe; evicting the oldest first spares whatever a live turn just fetched, and
    /// the sweep skips any mirror a request holds in use so it never deletes one mid-fetch.
    async fn maybe_sweep_mirrors(&self) {
        {
            let mut last = self.last_sweep.lock().await;
            if last.is_some_and(|t| t.elapsed() < MIRROR_SWEEP_INTERVAL) {
                return;
            }
            *last = Some(Instant::now());
        }
        let root = self.state_root.clone();
        let limit = self.mirror_limit;
        let in_use = self.in_use.clone();
        tokio::task::spawn_blocking(move || sweep_mirrors(&root, limit, &in_use));
    }

    /// Rebuild a missing mirror from a durable snapshot instead of re-cloning origin. Returns false
    /// when no snapshot exists (caller falls back to a full clone).
    async fn restore_mirror(
        &self,
        mirror: &Path,
        host: &str,
        repo: &str,
        resolved: &Resolved,
    ) -> Result<bool, String> {
        let key = snapshot_key(&resolved.principal, host, repo);
        let parent = mirror.parent().ok_or("mirror has no parent")?;
        tokio::fs::create_dir_all(parent)
            .await
            .map_err(|e| format!("create mirror parent: {e}"))?;
        let bundle = parent.join(format!("{}.bundle", basename(mirror)));
        if !self.durable.get_file(&key, &bundle).await {
            return Ok(false);
        }
        let tmp = parent.join(format!("{}.tmp", basename(mirror)));
        let _ = tokio::fs::remove_dir_all(&tmp).await;
        let restore = run_git(
            &[
                "clone".into(),
                "--mirror".into(),
                path_arg(&bundle),
                path_arg(&tmp),
            ],
            None,
        )
        .await;
        let _ = tokio::fs::remove_file(&bundle).await;
        if let Err(e) = restore {
            tracing::warn!(error = %e, "durable restore failed; falling back to clone");
            let _ = tokio::fs::remove_dir_all(&tmp).await;
            return Ok(false);
        }
        let url = format!("{}://{host}/{repo}", self.scheme);
        run_git(
            &["remote".into(), "set-url".into(), "origin".into(), url],
            Some(&tmp),
        )
        .await?;
        tokio::fs::rename(&tmp, mirror)
            .await
            .map_err(|e| format!("promote mirror: {e}"))?;
        allow_any_sha(mirror).await?;
        // The caller re-fetches to confirm current access and catch commits pushed since the
        // snapshot; a restored mirror is never served on its bundle alone.
        Ok(true)
    }

    /// Bundle the mirror and upload it, off the request path.
    fn spawn_snapshot(&self, mirror: &Path, principal: String, host: &str, repo: &str) {
        if matches!(*self.durable, Durable::Off) {
            return;
        }
        let durable = self.durable.clone();
        let key = snapshot_key(&principal, host, repo);
        let mirror = mirror.to_path_buf();
        let bundle = mirror.with_extension("snapshot.tmp");
        tokio::spawn(async move {
            let made = run_git(
                &[
                    "bundle".into(),
                    "create".into(),
                    path_arg(&bundle),
                    "--all".into(),
                    "HEAD".into(),
                ],
                Some(&mirror),
            )
            .await;
            match made {
                Ok(()) => {
                    durable.put_file(&key, &bundle).await;
                    let _ = tokio::fs::remove_file(&bundle).await;
                }
                Err(e) => tracing::warn!(error = %e, key, "snapshot bundle failed"),
            }
        });
    }

    async fn clone_mirror(
        &self,
        mirror: &Path,
        host: &str,
        repo: &str,
        resolved: &Resolved,
    ) -> Result<(), String> {
        let parent = mirror.parent().ok_or("mirror has no parent")?;
        tokio::fs::create_dir_all(parent)
            .await
            .map_err(|e| format!("create mirror parent: {e}"))?;
        let tmp = parent.join(format!(
            "{}.tmp",
            mirror
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("repo")
        ));
        let _ = tokio::fs::remove_dir_all(&tmp).await;
        let url = format!("{}://{host}/{repo}", self.scheme);
        let mut args = git_auth_args(resolved);
        args.extend(["clone".into(), "--mirror".into(), url, path_arg(&tmp)]);
        run_git(&args, None).await?;
        tokio::fs::rename(&tmp, mirror)
            .await
            .map_err(|e| format!("promote mirror: {e}"))?;
        allow_any_sha(mirror).await
    }

    async fn fetch_mirror(&self, mirror: &Path, resolved: &Resolved) -> Result<(), String> {
        let mut args = git_auth_args(resolved);
        args.extend([
            "fetch".into(),
            "--prune".into(),
            "origin".into(),
            "+refs/*:refs/*".into(),
        ]);
        run_git(&args, Some(mirror)).await
    }

    /// Cache the `git-upload-pack` response bytes and replay them for an identical request against an
    /// unmoved mirror. Generating a pack is the expensive half of a clone the mirror cannot make
    /// cheaper; a repeat clone of the same head — a subagent fan-out, a retried turn, a rebuilt
    /// sandbox — is the same wants and haves against the same refs, so the bytes are reusable.
    ///
    /// A miss captures the response to disk before answering, rather than teeing a stream, so a
    /// dropped client never commits a truncated pack. The captured file is what gets served, so the
    /// miss pays one disk write and every hit pays one read. The capture stops at the cap the tier can
    /// hold: a larger response is served from the bytes already captured plus the rest of the pipe, so
    /// the volume never takes a full copy of a response no entry could keep.
    #[allow(clippy::too_many_arguments)]
    async fn serve_pack_cached(
        &self,
        host_root: &Path,
        mirror: &Path,
        path_info: &str,
        method: &Method,
        query: Option<&str>,
        headers: &HeaderMap,
        body: Bytes,
        resolved: &Resolved,
        host: &str,
        repo: &str,
    ) -> Result<Response, String> {
        let fingerprint = ref_fingerprint(mirror).await?;
        let key = pack_key(
            &resolved.principal,
            host,
            repo,
            &fingerprint,
            &body,
            headers,
        );
        // The entry also lives under the principal's own directory, so isolation does not rest on the
        // key alone: one principal's packs are a subtree another principal's requests never address.
        let dir = self.pack_root.join(sanitize(&resolved.principal));
        let body_path = dir.join(format!("{key}.body"));
        let meta_path = dir.join(format!("{key}.meta"));
        // Held across the replay and the capture, so the pack sweep cannot evict the entry this
        // request is reading or has just written.
        let _in_use = self.in_use.guard(&body_path);

        if let Some(resp) = replay_pack(&meta_path, &body_path).await {
            return Ok(resp);
        }

        // Reclaim space before a write may add to the tree.
        self.maybe_sweep_packs().await;
        let mut child = self
            .spawn_backend(host_root, path_info, method, query, headers, &body)
            .await?;
        let mut stdout = child.stdout.take().ok_or("cgi child has no stdout")?;
        let head = read_head(&mut stdout).await?;
        tokio::fs::create_dir_all(&dir)
            .await
            .map_err(|e| format!("create pack cache dir: {e}"))?;
        let tmp = writing_temp(&body_path);
        // The cap bounds the capture itself, not just what is committed: a response the tier cannot
        // hold must never sit on the volume in full, or one clone of an outsized repo takes the cache
        // volume past its `sizeLimit` and the kubelet evicts the pod that carries all sandbox egress.
        let cap = MAX_CACHED_PACK_BYTES.min(self.pack_limit);
        let captured = capture_body(&mut stdout, &head.leftover, &tmp, cap).await;
        match captured {
            Ok(Capture::Complete) => {}
            Ok(Capture::PastTheCap) => return stream_past_the_cap(head, &tmp, stdout, child).await,
            Err(e) => {
                let _ = tokio::fs::remove_file(&tmp).await;
                return Err(e);
            }
        }
        let exited_clean = child.wait().await.map(|s| s.success()).unwrap_or(false);

        let meta = PackMeta::from_head(&head);
        // Never cache a failed backend run or a non-200 — a hit must never replay an error.
        let cacheable = exited_clean && head.status == StatusCode::OK;
        let serve_path = if cacheable && commit_pack(&meta, &tmp, &meta_path, &body_path).await {
            body_path
        } else {
            tmp.clone()
        };
        let resp = serve_pack_file(&meta, &serve_path, "MISS").await;
        if serve_path == tmp {
            let _ = tokio::fs::remove_file(&tmp).await;
        }
        resp.ok_or_else(|| "pack serve failed".to_string())
    }

    async fn maybe_sweep_packs(&self) {
        {
            let mut last = self.last_pack_sweep.lock().await;
            if last.is_some_and(|t| t.elapsed() < PACK_SWEEP_INTERVAL) {
                return;
            }
            *last = Some(Instant::now());
        }
        let root = self.pack_root.clone();
        let limit = self.pack_limit;
        let in_use = self.in_use.clone();
        tokio::task::spawn_blocking(move || sweep_packs(&root, limit, &in_use));
    }

    /// Spawn `git http-backend` over the principal's mirror tree with the request's CGI environment,
    /// its negotiation body written to stdin. The caller decides what to do with the response: stream
    /// it, or capture it for the pack cache.
    async fn spawn_backend(
        &self,
        host_root: &Path,
        path_info: &str,
        method: &Method,
        query: Option<&str>,
        headers: &HeaderMap,
        body: &Bytes,
    ) -> Result<Child, String> {
        if body.len() > MAX_UPLOAD_PACK_BYTES {
            return Err("upload-pack request too large".into());
        }
        let mut cmd = Command::new("git");
        cmd.arg("http-backend")
            .env_clear()
            .env("PATH", std::env::var("PATH").unwrap_or_default())
            .env("GIT_HTTP_EXPORT_ALL", "1")
            .env("GIT_PROJECT_ROOT", host_root)
            .env("PATH_INFO", path_info)
            .env("REQUEST_METHOD", method.as_str())
            .env("QUERY_STRING", query.unwrap_or(""))
            .env("GIT_TERMINAL_PROMPT", "0")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        if let Some(ct) = header(headers, "content-type") {
            cmd.env("CONTENT_TYPE", ct);
        }
        if let Some(ce) = header(headers, "content-encoding") {
            cmd.env("HTTP_CONTENT_ENCODING", ce);
        }
        if let Some(proto) = header(headers, "git-protocol") {
            cmd.env("GIT_PROTOCOL", proto);
        }
        if method == Method::POST {
            cmd.env("CONTENT_LENGTH", body.len().to_string());
        }
        let mut child = cmd
            .spawn()
            .map_err(|e| format!("spawn http-backend: {e}"))?;
        if let Some(mut stdin) = child.stdin.take() {
            stdin
                .write_all(body)
                .await
                .map_err(|e| format!("write cgi stdin: {e}"))?;
        }
        Ok(child)
    }
}

/// `acme/widget.git/info/lfs/objects/batch` → (safe repo, `objects/batch`). git-lfs derives its
/// endpoint as `<remote>.git/info/lfs` whether or not the remote URL spells the `.git`, so the
/// suffix is always present and nothing else on this path carries it.
fn split_lfs(tail: &str) -> Option<(String, &str)> {
    let (repo, rest) = tail.split_once(".git/info/lfs/")?;
    Some((safe_repo(repo)?, rest))
}

/// The principal's credential on an outbound LFS call, Basic exactly as the git fetch sends it.
fn with_lfs_auth(request: reqwest::RequestBuilder, resolved: &Resolved) -> reqwest::RequestBuilder {
    match &resolved.token {
        Some(token) => {
            let user = resolved.username.as_deref().unwrap_or("x-access-token");
            request.basic_auth(user, Some(token))
        }
        None => request,
    }
}

/// Read a response body whole, refusing past `cap`: the callers parse the bytes, so an unbounded
/// upstream answer must not become unbounded memory.
async fn read_capped(response: reqwest::Response, cap: usize) -> Result<Bytes, String> {
    use futures_util::StreamExt;
    let mut stream = response.bytes_stream();
    let mut buf = Vec::new();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|e| format!("read lfs response: {e}"))?;
        if buf.len() + chunk.len() > cap {
            return Err("lfs response too large".into());
        }
        buf.extend_from_slice(&chunk);
    }
    Ok(Bytes::from(buf))
}

/// Rewrite a batch answer so each download lands on this daemon's content route, carrying the oid
/// and declared size the content serve needs. Uploads and verifies keep their origin actions. None
/// when the answer is not one this daemon can re-route — not JSON, or a negotiated transfer other
/// than basic — and the caller then relays the origin's bytes untouched.
fn rewrite_batch(answer: &[u8], base: &str, host: &str, repo: &str) -> Option<Vec<u8>> {
    let mut doc: serde_json::Value = serde_json::from_slice(answer).ok()?;
    if doc
        .get("transfer")
        .and_then(|t| t.as_str())
        .is_some_and(|t| t != "basic")
    {
        return None;
    }
    for object in doc.get_mut("objects")?.as_array_mut()? {
        let oid = object
            .get("oid")
            .and_then(|o| o.as_str())
            .map(str::to_string);
        let size = object.get("size").and_then(|s| s.as_u64());
        let (Some(oid), Some(size)) = (oid, size) else {
            continue;
        };
        if !is_oid(&oid) {
            continue;
        }
        let Some(download) = object
            .get_mut("actions")
            .and_then(|actions| actions.get_mut("download"))
        else {
            continue;
        };
        // The origin's href and its pre-signed headers stay with the daemon's own re-batch on a
        // miss; the client gets a grant-free URL it reaches with its identity alone.
        *download = serde_json::json!({
            "href": format!("{base}/git/{host}/{repo}.git/info/lfs/{LFS_CONTENT_ROUTE}{oid}?size={size}")
        });
    }
    serde_json::to_vec(&doc).ok()
}

fn is_oid(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// The `size=<n>` a rewritten href carries: the batch's declared object size, which decides
/// cacheability up front and pins the byte count the capture must verify.
/// Durable key for one principal's object, isolated in the store as it is on disk.
fn lfs_key(principal: &str, oid: &str) -> String {
    format!("lfs/{}/{oid}", sanitize(principal))
}

/// True when the file on disk hashes to the oid and totals `size` bytes.
async fn lfs_file_matches(path: &Path, oid: &str, size: u64) -> bool {
    let Ok(mut file) = tokio::fs::File::open(path).await else {
        return false;
    };
    let mut hasher = Sha256::new();
    let mut total = 0u64;
    let mut chunk = vec![0u8; READ_CHUNK];
    loop {
        match file.read(&mut chunk).await {
            Ok(0) => break,
            Ok(n) => {
                total += n as u64;
                hasher.update(&chunk[..n]);
            }
            Err(_) => return false,
        }
    }
    total == size && hex::encode(hasher.finalize()) == oid
}

fn lfs_size(query: Option<&str>) -> Option<u64> {
    query?
        .split('&')
        .find_map(|p| p.strip_prefix("size="))?
        .parse()
        .ok()
}

/// The absolute base the client reaches this daemon at, rebuilt from its request: the Host it
/// addressed and the scheme the fronting proxy stamped (`x-forwarded-proto`; plain http when the
/// daemon is dialed directly). None without a Host — then no href can be rewritten.
fn public_base(headers: &HeaderMap) -> Option<String> {
    let host = header(headers, "host")?;
    let scheme = header(headers, "x-forwarded-proto").unwrap_or("http");
    Some(format!("{scheme}://{host}"))
}

/// The bar the sandbox's own egress applies to a public CONNECT: IPv4 that is not loopback,
/// private, link-local, carrier-grade NAT, multicast, broadcast, unspecified, documentation,
/// benchmarking, or reserved. IPv6 is refused outright, as the egress proxy refuses it.
fn globally_routable(ip: &std::net::IpAddr) -> bool {
    let std::net::IpAddr::V4(v4) = ip else {
        return false;
    };
    let octets = v4.octets();
    !(v4.is_loopback()
        || v4.is_private()
        || v4.is_link_local()
        || v4.is_multicast()
        || v4.is_broadcast()
        || v4.is_unspecified()
        || v4.is_documentation()
        || (octets[0] == 100 && (octets[1] & 0b1100_0000) == 64)
        || (octets[0] == 192 && octets[1] == 0 && octets[2] == 0)
        || (octets[0] == 198 && (octets[1] & 0b1111_1110) == 18)
        || octets[0] >= 240)
}

/// Drain an LFS content response to `path`, verifying as it writes: the bytes must hash to the
/// oid and total the declared size, or nothing is committed — a truncated or substituted object
/// never enters the tier and never reaches the client.
async fn capture_lfs(
    response: reqwest::Response,
    path: &Path,
    oid: &str,
    size: u64,
) -> Result<(), String> {
    use futures_util::StreamExt;
    let mut file = tokio::fs::File::create(path)
        .await
        .map_err(|e| format!("create {path:?}: {e}"))?;
    let mut hasher = Sha256::new();
    let mut written = 0u64;
    let mut stream = response.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|e| format!("read lfs content: {e}"))?;
        written += chunk.len() as u64;
        if written > size {
            return Err("lfs content past its declared size".into());
        }
        hasher.update(&chunk);
        file.write_all(&chunk)
            .await
            .map_err(|e| format!("write lfs content: {e}"))?;
    }
    file.flush()
        .await
        .map_err(|e| format!("flush lfs content: {e}"))?;
    if written != size {
        return Err(format!("lfs content is {written} bytes, not {size}"));
    }
    if hex::encode(hasher.finalize()) != oid {
        return Err("lfs content does not hash to its oid".into());
    }
    Ok(())
}

fn classify(tail: &str, body: &[u8]) -> Option<(Endpoint, String)> {
    if let Some(repo) = tail.strip_suffix("/info/refs") {
        return Some((Endpoint::Info, safe_repo(repo)?));
    }
    if let Some(repo) = tail.strip_suffix("/git-upload-pack") {
        let endpoint = if is_ls_refs(body) {
            Endpoint::LsRefs
        } else {
            Endpoint::UploadPack
        };
        return Some((endpoint, safe_repo(repo)?));
    }
    None
}

/// True when a `git-upload-pack` body is a protocol-v2 `ls-refs` request, read from the first
/// `command=` pkt-line. A protocol-v0 body carries no command line, so it is negotiation.
fn is_ls_refs(body: &[u8]) -> bool {
    let mut rest = body;
    while rest.len() >= 4 {
        let Ok(hex) = std::str::from_utf8(&rest[..4]) else {
            return false;
        };
        let Ok(len) = usize::from_str_radix(hex, 16) else {
            return false;
        };
        // `0000`, `0001` and `0002` are the flush, delimiter and response-end markers: four bytes and
        // no payload.
        if len < 4 {
            rest = &rest[4..];
            continue;
        }
        if len > rest.len() {
            return false;
        }
        let payload = &rest[4..len];
        let line = std::str::from_utf8(payload).unwrap_or("").trim_end();
        if let Some(command) = line.strip_prefix("command=") {
            return command == "ls-refs";
        }
        rest = &rest[len..];
    }
    false
}

/// The object ids a `git-upload-pack` negotiation asks for — `want <oid>` pkt-lines, protocol v0
/// and v2 alike — or None when they cannot be read positively: a compressed body, a `want-ref`, a
/// malformed pkt-line, or no `want` at all. None means the freshness window cannot prove the mirror
/// can back the request, so the caller fetches.
fn negotiation_wants(headers: &HeaderMap, body: &[u8]) -> Option<Vec<String>> {
    if header(headers, "content-encoding").is_some() {
        return None;
    }
    let mut wants = Vec::new();
    let mut rest = body;
    while rest.len() >= 4 {
        let hex = std::str::from_utf8(&rest[..4]).ok()?;
        let len = usize::from_str_radix(hex, 16).ok()?;
        if len < 4 {
            rest = &rest[4..];
            continue;
        }
        let payload = rest.get(4..len)?;
        rest = &rest[len..];
        let line = std::str::from_utf8(payload).ok()?.trim_end();
        if line.starts_with("want-ref ") {
            return None;
        }
        if let Some(want) = line.strip_prefix("want ") {
            let oid = want.split_whitespace().next()?;
            if !matches!(oid.len(), 40 | 64) || !oid.bytes().all(|b| b.is_ascii_hexdigit()) {
                return None;
            }
            wants.push(oid.to_string());
        }
    }
    if wants.is_empty() {
        return None;
    }
    Some(wants)
}

/// True when the mirror already holds every object the negotiation wants — the condition under
/// which `upload-pack` can answer without a fetch, and exactly what it accepts under
/// `allowAnySHA1InWant`. One `cat-file --batch-check` run answers all wants; unreadable wants or
/// any failure to run the check reads as not held, so the caller fetches.
async fn mirror_holds(mirror: &Path, wants: Option<&[String]>) -> bool {
    let Some(wants) = wants else {
        return false;
    };
    let mut cmd = Command::new("git");
    cmd.arg("--git-dir")
        .arg(mirror)
        .args(["cat-file", "--batch-check"])
        .env("GIT_TERMINAL_PROMPT", "0")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let Ok(mut child) = cmd.spawn() else {
        return false;
    };
    let Some(mut stdin) = child.stdin.take() else {
        return false;
    };
    // Written concurrently with the read: a want list large enough to fill the pipe while
    // `batch-check` is still answering earlier lines must not deadlock the request.
    let feed: String = wants.iter().map(|w| format!("{w}\n")).collect();
    let writer = tokio::spawn(async move {
        let _ = stdin.write_all(feed.as_bytes()).await;
    });
    let output = child.wait_with_output().await;
    let _ = writer.await;
    let Ok(output) = output else {
        return false;
    };
    output.status.success()
        && output
            .stdout
            .split(|&b| b == b'\n')
            .all(|line| !line.ends_with(b" missing"))
}

/// The canonical `org/repo` path, or None when it could escape its principal/host directory. A
/// `..`, `.`, or empty segment is refused rather than sanitized: the repo joins the mirror path, the
/// clone URL, and the http-backend `PATH_INFO`, and traversal there crosses into another workspace's
/// mirrors or outside the state root entirely.
fn safe_repo(repo: &str) -> Option<String> {
    let trimmed = repo.trim_matches('/').trim_end_matches(".git");
    if trimmed.is_empty()
        || trimmed
            .split('/')
            .any(|segment| matches!(segment, "" | "." | ".."))
    {
        return None;
    }
    Some(trimmed.to_string())
}

/// `-c http.extraHeader=...` injects auth per-invocation so the token never lands in the mirror's
/// on-disk config. Anonymous when the control plane returned no token.
fn git_auth_args(resolved: &Resolved) -> Vec<String> {
    let mut args = vec!["-c".into(), "credential.helper=".into()];
    if let Some(token) = &resolved.token {
        let user = resolved.username.as_deref().unwrap_or("x-access-token");
        let basic = base64::engine::general_purpose::STANDARD.encode(format!("{user}:{token}"));
        args.push("-c".into());
        args.push(format!("http.extraHeader=Authorization: Basic {basic}"));
    }
    args
}

async fn run_git(args: &[String], git_dir: Option<&Path>) -> Result<(), String> {
    git_output(args, git_dir).await.map(|_| ())
}

async fn git_output(args: &[String], git_dir: Option<&Path>) -> Result<Vec<u8>, String> {
    let mut cmd = Command::new("git");
    if let Some(dir) = git_dir {
        cmd.arg("--git-dir").arg(dir);
    }
    cmd.args(args)
        .env("GIT_TERMINAL_PROMPT", "0")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let output = cmd.output().await.map_err(|e| format!("spawn git: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "git {}: {}",
            args.first().map(String::as_str).unwrap_or(""),
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(output.stdout)
}

/// Let a fetch ask for an exact commit that is not a ref tip (e.g. `git fetch origin <base_sha>`);
/// `git upload-pack` refuses an unadvertised want without this. Set on every mirror we serve.
async fn allow_any_sha(mirror: &Path) -> Result<(), String> {
    run_git(
        &[
            "config".into(),
            "uploadpack.allowAnySHA1InWant".into(),
            "true".into(),
        ],
        Some(mirror),
    )
    .await
}

fn path_arg(p: &Path) -> String {
    p.to_string_lossy().into_owned()
}

/// Durable snapshot key, namespaced by principal so a private mirror's bundle is isolated in the
/// object store exactly as it is on disk.
fn snapshot_key(principal: &str, host: &str, repo: &str) -> String {
    format!(
        "git/{}/{}/{repo}.bundle",
        sanitize(principal),
        sanitize(host)
    )
}

fn basename(mirror: &Path) -> &str {
    mirror
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("repo")
}

/// The response head a cached pack replays. Byte-identical: the status and the headers `http-backend`
/// itself produced, so a hit is indistinguishable from a fresh run to the git client.
#[derive(Serialize, Deserialize)]
struct PackMeta {
    status: u16,
    headers: Vec<(String, String)>,
}

impl PackMeta {
    fn from_head(head: &CgiHead) -> Self {
        Self {
            status: head.status.as_u16(),
            headers: head
                .headers
                .iter()
                .filter(|(name, _)| name.as_str() != "content-length")
                .filter_map(|(name, value)| {
                    value
                        .to_str()
                        .ok()
                        .map(|v| (name.as_str().to_string(), v.to_string()))
                })
                .collect(),
        }
    }
}

/// Everything that decides the response bytes, hashed into one entry name: the principal (so no
/// principal can address another's entry), the repo it came from, the mirror's ref state, the
/// negotiation body (the wants and haves), and the request headers the backend reads.
/// `accept-encoding` is keyed although the backend is not given it — keying it now means a future
/// change that does forward it cannot replay a body in the wrong encoding.
fn pack_key(
    principal: &str,
    host: &str,
    repo: &str,
    fingerprint: &str,
    body: &[u8],
    headers: &HeaderMap,
) -> String {
    let mut hasher = Sha256::new();
    for part in [
        principal,
        host,
        repo,
        fingerprint,
        header(headers, "git-protocol").unwrap_or(""),
        header(headers, "content-type").unwrap_or(""),
        header(headers, "content-encoding").unwrap_or(""),
        header(headers, "accept-encoding").unwrap_or(""),
    ] {
        hasher.update(part.as_bytes());
        hasher.update(b"\n");
    }
    hasher.update(body);
    hex::encode(hasher.finalize())
}

/// An exact fingerprint of what the mirror can serve: every ref's target object, plus `HEAD`. Any ref
/// movement — a new commit, a force-push, a deleted branch — changes it, so a cached pack is a miss
/// rather than a replay of history the mirror no longer has. Never an mtime: a fetch that changes
/// nothing still rewrites files, and a repack changes files without changing what is servable.
async fn ref_fingerprint(mirror: &Path) -> Result<String, String> {
    let refs = git_output(
        &[
            "for-each-ref".into(),
            "--format=%(objectname) %(refname)".into(),
        ],
        Some(mirror),
    )
    .await?;
    let head = tokio::fs::read(mirror.join("HEAD"))
        .await
        .unwrap_or_default();
    let mut hasher = Sha256::new();
    hasher.update(&refs);
    hasher.update(b"\n");
    hasher.update(&head);
    Ok(hex::encode(hasher.finalize()))
}

/// How a capture ended: with the whole response on disk, or at the cap with the rest still on the
/// pipe.
enum Capture {
    Complete,
    PastTheCap,
}

/// Drain the backend's remaining stdout into `path`. Fails loud on a read or write error so a truncated
/// pack is never committed. Stops on the first chunk that takes the file past `cap`, so a response the
/// tier could never hold is never written whole and the capture holds at most `cap` plus one chunk.
async fn capture_body(
    stdout: &mut tokio::process::ChildStdout,
    leftover: &Bytes,
    path: &Path,
    cap: u64,
) -> Result<Capture, String> {
    let mut file = tokio::fs::File::create(path)
        .await
        .map_err(|e| format!("create {path:?}: {e}"))?;
    let mut size = 0u64;
    file.write_all(leftover)
        .await
        .map_err(|e| format!("write pack: {e}"))?;
    size += leftover.len() as u64;
    let mut chunk = vec![0u8; READ_CHUNK];
    let mut ended = false;
    while size <= cap {
        let n = stdout
            .read(&mut chunk)
            .await
            .map_err(|e| format!("read backend stdout: {e}"))?;
        if n == 0 {
            ended = true;
            break;
        }
        file.write_all(&chunk[..n])
            .await
            .map_err(|e| format!("write pack: {e}"))?;
        size += n as u64;
    }
    file.flush().await.map_err(|e| format!("flush pack: {e}"))?;
    if ended {
        Ok(Capture::Complete)
    } else {
        Ok(Capture::PastTheCap)
    }
}

/// Serve a response the pack tier cannot hold: the bytes already captured, then the rest straight from
/// the backend's pipe. The capture is unlinked before the first byte goes out, so its disk is reclaimed
/// when the response ends — including when the client drops mid-stream.
async fn stream_past_the_cap(
    head: CgiHead,
    capture: &Path,
    stdout: tokio::process::ChildStdout,
    mut child: Child,
) -> Result<Response, String> {
    let file = tokio::fs::File::open(capture)
        .await
        .map_err(|e| format!("open {capture:?}: {e}"))?;
    tokio::fs::remove_file(capture)
        .await
        .map_err(|e| format!("unlink {capture:?}: {e}"))?;
    let mut source = file.chain(stdout);
    let stream = async_stream::stream! {
        let mut chunk = vec![0u8; READ_CHUNK];
        loop {
            match source.read(&mut chunk).await {
                Ok(0) => break,
                Ok(n) => yield Ok::<Bytes, std::io::Error>(Bytes::copy_from_slice(&chunk[..n])),
                Err(e) => {
                    yield Err(e);
                    break;
                }
            }
        }
        let _ = child.wait().await;
    };
    let mut builder = Response::builder().status(head.status);
    for (name, value) in head.headers {
        builder = builder.header(name, value);
    }
    builder
        .header("x-ufo-cache", "MISS")
        .body(Body::from_stream(stream))
        .map_err(|e| format!("build response: {e}"))
}

/// Rename the captured pack and its head into place. The body lands first and the meta commits the
/// entry, so a crash between the two leaves a body no lookup can find rather than a meta pointing at
/// nothing. False on any failure — the caller then serves the capture uncommitted.
async fn commit_pack(meta: &PackMeta, tmp: &Path, meta_path: &Path, body_path: &Path) -> bool {
    let Ok(meta_bytes) = serde_json::to_vec(meta) else {
        return false;
    };
    let tmp_meta = writing_temp(meta_path);
    if tokio::fs::write(&tmp_meta, &meta_bytes).await.is_err()
        || tokio::fs::rename(tmp, body_path).await.is_err()
    {
        let _ = tokio::fs::remove_file(&tmp_meta).await;
        return false;
    }
    tokio::fs::rename(&tmp_meta, meta_path).await.is_ok()
}

/// Replay a cached pack, or None when this request has no entry — including a meta whose body the
/// sweep has since evicted, which the caller treats as a plain miss.
async fn replay_pack(meta_path: &Path, body_path: &Path) -> Option<Response> {
    let bytes = tokio::fs::read(meta_path).await.ok()?;
    let meta: PackMeta = serde_json::from_slice(&bytes).ok()?;
    serve_pack_file(&meta, body_path, "HIT").await
}

async fn serve_pack_file(
    meta: &PackMeta,
    body_path: &Path,
    cache_status: &str,
) -> Option<Response> {
    let file = tokio::fs::File::open(body_path).await.ok()?;
    let len = file.metadata().await.ok()?.len();
    // The sweep ranks entries by mtime, so a replay is a use that must postpone eviction.
    let _ = set_file_mtime(body_path, FileTime::now());
    let stream = tokio_util::io::ReaderStream::with_capacity(file, READ_CHUNK);
    let mut builder =
        Response::builder().status(StatusCode::from_u16(meta.status).unwrap_or(StatusCode::OK));
    for (name, value) in &meta.headers {
        builder = builder.header(name, value);
    }
    builder
        .header("content-length", len)
        .header("x-ufo-cache", cache_status)
        .body(Body::from_stream(stream))
        .ok()
}

/// Evict least-recently-used cached packs until the pack tree is under `limit`. The LFS content
/// tier keeps the same entry shape (`<key>.body`), so its sweep is this same function over its own
/// root and ceiling. Mirrors the mirror
/// sweep and the package sweep: reserve each victim with an atomic rename-aside taken only while
/// `in_use` shows no holder, then delete the body and its meta outside the lock, so an entry a live
/// request is replaying is never removed mid-serve. A cached pack is re-generable from the mirror, so
/// eviction only costs one backend run.
fn sweep_packs(root: &Path, limit: u64, in_use: &InUse) {
    let mut entries: Vec<(PathBuf, u64, u64)> = Vec::new();
    let mut total = 0u64;
    collect_packs(root, &mut entries, &mut total);
    if total <= limit {
        return;
    }
    entries.sort_by_key(|(_, _, mtime)| *mtime);
    let mut remaining = total;
    for (body_path, size, _) in entries {
        if remaining <= limit {
            break;
        }
        let evicting = body_path.with_extension("evicting");
        let _ = std::fs::remove_file(&evicting);
        let reserved = in_use.reserve_if_free(&body_path, || {
            std::fs::rename(&body_path, &evicting).is_ok()
        });
        if reserved {
            remaining = remaining.saturating_sub(size);
            let _ = std::fs::remove_file(&evicting);
            let _ = std::fs::remove_file(body_path.with_extension("meta"));
        }
    }
}

/// Total the pack tree and list its evictable bodies. A capture underway (`.writing`) is charged
/// against the ceiling but never evicted; one left by a crash is reclaimed after its grace.
fn collect_packs(dir: &Path, out: &mut Vec<(PathBuf, u64, u64)>, total: &mut u64) {
    let Ok(read) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in read.flatten() {
        let path = entry.path();
        let Ok(meta) = entry.metadata() else {
            continue;
        };
        if meta.is_dir() {
            collect_packs(&path, out, total);
            continue;
        }
        let mtime = meta
            .modified()
            .ok()
            .and_then(|m| m.duration_since(UNIX_EPOCH).ok())
            .map(|d| d.as_secs())
            .unwrap_or(0);
        match path.extension().and_then(|e| e.to_str()) {
            Some("body") => {
                *total += meta.len();
                out.push((path, meta.len(), mtime));
            }
            Some("meta") => *total += meta.len(),
            Some("writing")
                if now_secs().saturating_sub(mtime) > PACK_WRITING_ORPHAN_GRACE_SECS =>
            {
                let _ = std::fs::remove_file(&path);
            }
            Some("writing") => *total += meta.len(),
            _ => {}
        }
    }
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Evict oldest bare mirrors until the whole tree is under `limit`. The size counted is the entire
/// state tree — finished `*.git` mirrors *and* in-progress clones/bundles (`.tmp`, `.bundle`) — so a
/// clone underway is charged against the ceiling and finished mirrors are evicted to make room for
/// it. A mirror a live request holds in use is skipped; a single clone larger than the volume is
/// inherent and cannot be swept.
///
/// Eviction reserves the mirror with an atomic rename-aside taken only while `in_use` shows no
/// holder, under the same lock a request bumps to claim one. So a request that races the sweep
/// either wins the lock first (the sweep sees the claim and skips) or loses it (the mirror is
/// already renamed away, and the request finds it missing and re-clones) — it never fetches a
/// directory being deleted underneath it. The recursive delete runs outside the lock, on the
/// renamed-aside path, so the lock never covers I/O and the event loop never stalls behind it.
fn sweep_mirrors(root: &Path, limit: u64, in_use: &InUse) {
    let (mut remaining, _) = dir_size_and_mtime(root);
    if remaining <= limit {
        return;
    }
    let mut mirrors: Vec<(PathBuf, u64, u64)> = Vec::new();
    collect_mirrors(root, &mut mirrors);
    mirrors.sort_by_key(|(_, _, mtime)| *mtime);
    for (path, size, _) in mirrors {
        if remaining <= limit {
            break;
        }
        let evicting = path.with_file_name(format!("{}.evicting", basename(&path)));
        // No concurrent sweep runs (the interval gate serialises them), so a stale tomb can only be
        // a prior crash's; clear it before the lock so no I/O runs under the lock.
        let _ = std::fs::remove_dir_all(&evicting);
        let reserved = in_use.reserve_if_free(&path, || std::fs::rename(&path, &evicting).is_ok());
        if reserved {
            remaining = remaining.saturating_sub(size);
            let _ = std::fs::remove_dir_all(&evicting);
        }
    }
}

fn collect_mirrors(dir: &Path, out: &mut Vec<(PathBuf, u64, u64)>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        if path.extension().and_then(|e| e.to_str()) == Some("git") && path.join("HEAD").exists() {
            let (size, mtime) = dir_size_and_mtime(&path);
            out.push((path, size, mtime));
        } else {
            collect_mirrors(&path, out);
        }
    }
}

/// A mirror's total bytes and its most recent file mtime (its recency: a fetch rewrites packs).
fn dir_size_and_mtime(dir: &Path) -> (u64, u64) {
    let mut size = 0u64;
    let mut newest = 0u64;
    let mut stack = vec![dir.to_path_buf()];
    while let Some(current) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&current) else {
            continue;
        };
        for entry in entries.flatten() {
            let Ok(meta) = entry.metadata() else {
                continue;
            };
            if meta.is_dir() {
                stack.push(entry.path());
            } else {
                size += meta.len();
                if let Ok(secs) = meta
                    .modified()
                    .and_then(|m| m.duration_since(UNIX_EPOCH).map_err(std::io::Error::other))
                {
                    newest = newest.max(secs.as_secs());
                }
            }
        }
    }
    (size, newest)
}

fn header<'a>(headers: &'a HeaderMap, name: &str) -> Option<&'a str> {
    headers.get(name).and_then(|v| v.to_str().ok())
}

/// A principal or host is an opaque path segment; keep it to safe characters so it cannot escape the
/// state root. `/` and `..` collapse to `_`.
pub(crate) fn sanitize(segment: &str) -> String {
    segment
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.') {
                c
            } else {
                '_'
            }
        })
        .collect::<String>()
        .replace("..", "__")
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::Path;

    use filetime::{set_file_mtime, FileTime};

    use axum::http::HeaderMap;

    use super::{
        globally_routable, is_ls_refs, is_oid, lfs_size, negotiation_wants, pack_key,
        rewrite_batch, safe_repo, split_lfs, sweep_mirrors, sweep_packs,
    };
    use crate::inuse::InUse;

    fn pkt(line: &str) -> String {
        format!("{:04x}{line}", line.len() + 4)
    }

    #[test]
    fn a_protocol_v2_ls_refs_body_is_ref_discovery_and_nothing_else_is() {
        let ls_refs = format!(
            "{}{}0001{}0000",
            pkt("command=ls-refs\n"),
            pkt("agent=git/2.47.3\n"),
            pkt("peel\n")
        );
        assert!(is_ls_refs(ls_refs.as_bytes()));

        // The other v2 command on this path is the negotiation, which the freshness window may serve.
        let fetch = format!(
            "{}{}0001{}0000",
            pkt("command=fetch\n"),
            pkt("agent=git/2.47.3\n"),
            pkt("want 0123456789012345678901234567890123456789\n")
        );
        assert!(!is_ls_refs(fetch.as_bytes()));

        // Protocol v0 carries no command line: the wants arrive straight away, and its advertisement was
        // the `info/refs` GET.
        let v0 = format!(
            "{}0000{}",
            pkt("want 0123456789012345678901234567890123456789\n"),
            pkt("done\n")
        );
        assert!(!is_ls_refs(v0.as_bytes()));
        assert!(!is_ls_refs(b""));
        assert!(!is_ls_refs(b"not a pkt-line at all"));
    }

    #[test]
    fn split_lfs_takes_only_the_lfs_api_and_keeps_the_repo_safe() {
        assert_eq!(
            split_lfs("acme/widget.git/info/lfs/objects/batch"),
            Some(("acme/widget".to_string(), "objects/batch"))
        );
        assert_eq!(
            split_lfs("acme/widget.git/info/lfs/locks/verify"),
            Some(("acme/widget".to_string(), "locks/verify"))
        );
        // A repo whose own path ends in the marker still splits at the api boundary.
        assert_eq!(
            split_lfs("acme/info/lfs.git/info/lfs/objects/batch"),
            Some(("acme/info/lfs".to_string(), "objects/batch"))
        );
        assert_eq!(split_lfs("acme/widget/info/refs"), None);
        assert_eq!(split_lfs("acme/widget.git/git-upload-pack"), None);
        assert_eq!(split_lfs("info/lfs/git-upload-pack"), None);
        assert_eq!(split_lfs("../evil.git/info/lfs/objects/batch"), None);
    }

    #[test]
    fn rewrite_batch_points_downloads_at_the_daemon_and_leaves_the_rest() {
        let oid = "a".repeat(64);
        let answer = serde_json::json!({
            "transfer": "basic",
            "objects": [
                {
                    "oid": oid,
                    "size": 11,
                    "actions": {
                        "download": {
                            "href": "https://storage.example/x",
                            "header": {"Authorization": "presigned"}
                        },
                        "upload": {"href": "https://storage.example/up"}
                    }
                },
                {"oid": oid, "size": 11, "error": {"code": 404, "message": "gone"}}
            ]
        });
        let rewritten: serde_json::Value = serde_json::from_slice(
            &rewrite_batch(
                &serde_json::to_vec(&answer).unwrap(),
                "https://cache.ufo.internal",
                "github.com",
                "acme/widget",
            )
            .unwrap(),
        )
        .unwrap();
        let download = &rewritten["objects"][0]["actions"]["download"];
        assert_eq!(
            download["href"],
            format!(
                "https://cache.ufo.internal/git/github.com/acme/widget.git/info/lfs/objects/content/{oid}?size=11"
            )
        );
        assert!(
            download.get("header").is_none(),
            "the origin's pre-signed grant must not reach the client"
        );
        assert_eq!(
            rewritten["objects"][0]["actions"]["upload"]["href"], "https://storage.example/up",
            "uploads keep the origin's own action"
        );
        assert_eq!(rewritten["objects"][1]["error"]["code"], 404);

        // A transfer the daemon does not understand relays untouched, and so does a non-answer.
        let multipart = br#"{"transfer":"multipart","objects":[]}"#;
        assert_eq!(rewrite_batch(multipart, "b", "h", "r"), None);
        assert_eq!(rewrite_batch(b"not json", "b", "h", "r"), None);

        assert!(is_oid(&oid));
        assert!(!is_oid("ABCDEF"));
        assert_eq!(lfs_size(Some("size=11")), Some(11));
        assert_eq!(lfs_size(Some("other=1")), None);
        assert_eq!(lfs_size(None), None);
    }

    #[test]
    fn only_public_addresses_pass_the_content_guard() {
        let public = ["140.82.112.3", "185.199.108.133", "1.1.1.1"];
        for ip in public {
            assert!(globally_routable(&ip.parse().unwrap()), "{ip}");
        }
        let refused = [
            "127.0.0.1",
            "10.255.255.1",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",
            "100.64.0.1",
            "192.0.0.1",
            "198.18.0.1",
            "224.0.0.1",
            "255.255.255.255",
            "0.0.0.0",
            "240.0.0.1",
            "::1",
            "2606:4700::1111",
        ];
        for ip in refused {
            assert!(!globally_routable(&ip.parse().unwrap()), "{ip}");
        }
    }

    #[test]
    fn negotiation_wants_reads_both_protocols_and_refuses_what_it_cannot_prove() {
        let h = HeaderMap::new();
        let sha = "0123456789012345678901234567890123456789";
        let other = "a".repeat(40);

        // Protocol v0: the first want carries the capability list after the oid.
        let v0 = format!(
            "{}{}0000{}",
            pkt(&format!("want {sha} multi_ack_detailed side-band-64k\n")),
            pkt(&format!("want {other}\n")),
            pkt("done\n")
        );
        assert_eq!(
            negotiation_wants(&h, v0.as_bytes()).as_deref(),
            Some(&[sha.to_string(), other.clone()][..])
        );

        let v2 = format!(
            "{}{}0001{}{}{}0000",
            pkt("command=fetch\n"),
            pkt("agent=git/2.47.3\n"),
            pkt("thin-pack\n"),
            pkt(&format!("want {sha}\n")),
            pkt("done\n")
        );
        assert_eq!(
            negotiation_wants(&h, v2.as_bytes()).as_deref(),
            Some(&[sha.to_string()][..])
        );

        // A `want-ref` names a ref, not an object: nothing in the mirror proves where the ref points
        // upstream, so the whole body is unprovable.
        let want_ref = format!(
            "{}0001{}{}0000",
            pkt("command=fetch\n"),
            pkt(&format!("want {sha}\n")),
            pkt("want-ref refs/heads/main\n")
        );
        assert_eq!(negotiation_wants(&h, want_ref.as_bytes()), None);

        // A compressed body cannot be read here; `http-backend` decodes it, the window must not.
        let mut gzip = HeaderMap::new();
        gzip.insert("content-encoding", "gzip".parse().unwrap());
        assert_eq!(negotiation_wants(&gzip, v0.as_bytes()), None);

        let ls_refs = format!("{}0001{}0000", pkt("command=ls-refs\n"), pkt("peel\n"));
        assert_eq!(negotiation_wants(&h, ls_refs.as_bytes()), None);
        assert_eq!(negotiation_wants(&h, b""), None);
        assert_eq!(negotiation_wants(&h, b"not a pkt-line at all"), None);
        assert_eq!(
            negotiation_wants(&h, pkt("want notanoid\n").as_bytes()),
            None
        );
    }

    #[test]
    fn accepts_a_normal_repo_and_strips_trailing_git_and_slashes() {
        assert_eq!(safe_repo("acme/widget").as_deref(), Some("acme/widget"));
        assert_eq!(
            safe_repo("/acme/widget.git").as_deref(),
            Some("acme/widget")
        );
    }

    #[test]
    fn refuses_traversal_and_empty_segments() {
        assert_eq!(safe_repo("../github.com/victim/repo"), None);
        assert_eq!(safe_repo("acme/../widget"), None);
        assert_eq!(safe_repo("acme//widget"), None);
        assert_eq!(safe_repo("."), None);
        assert_eq!(safe_repo(""), None);
    }

    fn plant_mirror(root: &Path, name: &str, bytes: usize, mtime_secs: i64) {
        let dir = root.join(format!("host/{name}.git"));
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("HEAD"), "ref: refs/heads/main\n").unwrap();
        fs::write(dir.join("pack"), vec![0u8; bytes]).unwrap();
        // The sweep ranks a mirror by its newest file, so every file must carry the intended age.
        let stamp = FileTime::from_unix_time(mtime_secs, 0);
        set_file_mtime(dir.join("HEAD"), stamp).unwrap();
        set_file_mtime(dir.join("pack"), stamp).unwrap();
    }

    #[test]
    fn sweep_evicts_the_oldest_mirror_until_under_the_limit() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        plant_mirror(root, "old", 4096, 1_000);
        plant_mirror(root, "new", 4096, 2_000);

        // Limit fits one mirror; the older one is evicted, the newer kept.
        let idle = InUse::default();
        sweep_mirrors(root, 5000, &idle);

        assert!(
            !root.join("host/old.git").exists(),
            "oldest mirror should be evicted"
        );
        assert!(
            root.join("host/new.git").exists(),
            "newest mirror should survive"
        );
    }

    #[test]
    fn sweep_counts_only_its_own_root_not_a_sibling_package_tree() {
        // git mirrors live under `<state>/git` and the package cache under `<state>/pkg`. The git
        // sweep must total only its own root — a package tree fat enough to blow the git ceiling must
        // never make the sweep evict a mirror that fits under it.
        let tmp = tempfile::tempdir().unwrap();
        let state = tmp.path();
        plant_mirror(&state.join("git"), "repo", 4096, 1_000);
        let pkg = state.join("pkg/registry.npmjs.org");
        fs::create_dir_all(&pkg).unwrap();
        fs::write(pkg.join("big.body"), vec![0u8; 1_000_000]).unwrap();

        sweep_mirrors(&state.join("git"), 100_000, &InUse::default());

        assert!(
            state.join("git/host/repo.git").join("HEAD").exists(),
            "the git sweep must ignore the sibling package tree's bytes"
        );
    }

    fn plant_pack(root: &Path, principal: &str, key: &str, bytes: usize, mtime_secs: i64) {
        let dir = root.join(principal);
        fs::create_dir_all(&dir).unwrap();
        let body = dir.join(format!("{key}.body"));
        let meta = dir.join(format!("{key}.meta"));
        fs::write(&body, vec![0u8; bytes]).unwrap();
        fs::write(&meta, br#"{"status":200,"headers":[]}"#).unwrap();
        let stamp = FileTime::from_unix_time(mtime_secs, 0);
        set_file_mtime(&body, stamp).unwrap();
        set_file_mtime(&meta, stamp).unwrap();
    }

    #[test]
    fn the_pack_key_separates_principals_repos_ref_states_and_negotiations() {
        let h = HeaderMap::new();
        let base = pack_key(
            "w1-u-alice",
            "github.com",
            "acme/widget",
            "reffp",
            b"want a",
            &h,
        );

        // The principal is in the key, so one principal cannot address another's cached pack even if
        // it asks for the identical repo, refs, and wants.
        assert_ne!(
            base,
            pack_key(
                "w1-u-bob",
                "github.com",
                "acme/widget",
                "reffp",
                b"want a",
                &h
            )
        );
        assert_ne!(
            base,
            pack_key(
                "w1-u-alice",
                "gitlab.com",
                "acme/widget",
                "reffp",
                b"want a",
                &h
            )
        );
        assert_ne!(
            base,
            pack_key(
                "w1-u-alice",
                "github.com",
                "acme/other",
                "reffp",
                b"want a",
                &h
            )
        );
        // A ref that moved is a different fingerprint, so the entry is a miss.
        assert_ne!(
            base,
            pack_key(
                "w1-u-alice",
                "github.com",
                "acme/widget",
                "moved",
                b"want a",
                &h
            )
        );
        assert_ne!(
            base,
            pack_key(
                "w1-u-alice",
                "github.com",
                "acme/widget",
                "reffp",
                b"want b",
                &h
            )
        );

        let mut protocol = HeaderMap::new();
        protocol.insert("git-protocol", "version=2".parse().unwrap());
        assert_ne!(
            base,
            pack_key(
                "w1-u-alice",
                "github.com",
                "acme/widget",
                "reffp",
                b"want a",
                &protocol
            )
        );

        assert_eq!(
            base,
            pack_key(
                "w1-u-alice",
                "github.com",
                "acme/widget",
                "reffp",
                b"want a",
                &h
            ),
            "the same request against the same ref state must be one key"
        );
    }

    #[test]
    fn the_pack_sweep_evicts_the_oldest_entry_until_under_the_ceiling() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        plant_pack(root, "w1-u-alice", "old", 4096, 1_000);
        plant_pack(root, "w1-u-alice", "new", 4096, 2_000);

        sweep_packs(root, 5000, &InUse::default());

        assert!(
            !root.join("w1-u-alice/old.body").exists(),
            "the oldest cached pack should be evicted"
        );
        assert!(
            !root.join("w1-u-alice/old.meta").exists(),
            "an evicted body must take its meta with it, or a lookup finds a head with no pack"
        );
        assert!(
            root.join("w1-u-alice/new.body").exists(),
            "the newest cached pack should survive"
        );
    }

    #[test]
    fn the_pack_sweep_spares_an_entry_a_request_is_replaying() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        plant_pack(root, "w1-u-alice", "old", 4096, 1_000);
        plant_pack(root, "w1-u-alice", "new", 4096, 2_000);

        let in_use = InUse::default();
        let _held = in_use.guard(&root.join("w1-u-alice/old.body"));

        sweep_packs(root, 5000, &in_use);

        assert!(
            root.join("w1-u-alice/old.body").exists(),
            "an entry being replayed must survive even as the LRU"
        );
        assert!(
            !root.join("w1-u-alice/new.body").exists(),
            "an idle entry is evicted to reclaim space instead"
        );
    }

    #[test]
    fn the_pack_sweep_leaves_a_tree_under_its_ceiling_alone() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        plant_pack(root, "w1-u-alice", "one", 4096, 1_000);

        sweep_packs(root, 1 << 20, &InUse::default());

        assert!(root.join("w1-u-alice/one.body").exists());
    }

    #[test]
    fn sweep_spares_a_mirror_held_in_use_even_when_it_is_the_oldest() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        plant_mirror(root, "old", 4096, 1_000);
        plant_mirror(root, "new", 4096, 2_000);

        // The oldest mirror is the one a live request holds; the sweep must skip it and reclaim from
        // the idle newer mirror instead, never deleting the directory being served.
        let in_use = InUse::default();
        let _held = in_use.guard(&root.join("host/old.git"));

        sweep_mirrors(root, 5000, &in_use);

        assert!(
            root.join("host/old.git").exists(),
            "an in-use mirror must survive even as the LRU"
        );
        assert!(
            !root.join("host/new.git").exists(),
            "an idle mirror is evicted to reclaim space instead"
        );
    }
}
