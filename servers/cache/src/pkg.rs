use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use axum::body::{Body, Bytes};
use axum::http::{HeaderMap, Method, StatusCode};
use axum::response::{IntoResponse, Response};
use filetime::{set_file_mtime, FileTime};
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tokio::io::AsyncWriteExt;

use crate::cgi::READ_CHUNK;
use crate::durable::Durable;
use crate::git::sanitize;
use crate::inuse::InUse;

/// A response marked `immutable` but carrying no `max-age` is still treated as fresh this long — a
/// package file never changes under its versioned URL.
const IMMUTABLE_SECS: u64 = 365 * 24 * 3600;
/// The pkg tree is swept back under its ceiling at most this often.
const PKG_SWEEP_INTERVAL: Duration = Duration::from_secs(120);
/// The response headers replayed from a cached entry; everything else the origin sent is dropped.
const REPLAY_HEADERS: [&str; 7] = [
    "content-type",
    "content-encoding",
    "content-language",
    "cache-control",
    "etag",
    "last-modified",
    "vary",
];
/// A request carrying either of these is authenticated: never served from — nor written to — a cache
/// shared across every workspace, so a private package on a shared host cannot leak.
const PRIVATE_REQUEST_HEADERS: [&str; 2] = ["authorization", "cookie"];
/// A `.writing` temp older than this had no writer for an hour — far past any real download — so the
/// sweep reclaims it as a crashed download's orphan. A live download rewrites its temp continuously,
/// keeping the mtime fresh.
const WRITING_ORPHAN_GRACE_SECS: u64 = 3600;

/// A temp path unique to this write: `<path>.<pid>.<seq>.writing`, so two concurrent downloads of one
/// key never share a file (one `File::create` would truncate the other's in-flight body). Mirrors
/// `durable::restore_temp`.
static WRITE_SEQ: AtomicU64 = AtomicU64::new(0);

fn writing_temp(path: &Path) -> PathBuf {
    let seq = WRITE_SEQ.fetch_add(1, Ordering::Relaxed);
    let mut name = path.as_os_str().to_owned();
    name.push(format!(".{}.{}.writing", std::process::id(), seq));
    PathBuf::from(name)
}

/// Serves a transparent, shared HTTP forward-cache for public package registries and their download
/// CDNs. The proxy MITMs each allowlisted host and relays its requests here; this type honours the
/// origin's `Cache-Control` to cache immutable downloads while passing mutable metadata through,
/// revalidates stale entries conditionally, and bounds the tree's disk. Entries are namespaced by
/// host but not by tenant: the artifacts are public and immutable, so one shared cache is both safe
/// and the whole point.
pub struct PkgCache {
    root: PathBuf,
    client: reqwest::Client,
    durable: Arc<Durable>,
    scheme: String,
    disk_limit: u64,
    in_use: InUse,
    last_sweep: tokio::sync::Mutex<Option<Instant>>,
}

impl PkgCache {
    pub fn new(root: PathBuf, durable: Arc<Durable>, scheme: String, disk_limit: u64) -> Self {
        let client = reqwest::Client::builder()
            .no_proxy()
            .connect_timeout(Duration::from_secs(10))
            .build()
            .expect("reqwest client");
        Self {
            root,
            client,
            durable,
            scheme,
            disk_limit,
            in_use: InUse::default(),
            last_sweep: tokio::sync::Mutex::new(None),
        }
    }

    /// `tail` is the request path after `/pkg/<host>/`; `query` is its query string.
    pub async fn handle(
        &self,
        method: &Method,
        host: &str,
        tail: &str,
        query: Option<&str>,
        headers: &HeaderMap,
        body: Bytes,
    ) -> Response {
        let path_q = match query {
            Some(q) if !q.is_empty() => format!("{tail}?{q}"),
            _ => tail.to_string(),
        };
        let url = format!("{}://{host}/{path_q}", self.scheme);

        // Only an anonymous, whole-object GET is cacheable. A non-GET mutates; an authenticated
        // request may carry a private artifact that must never enter a shared cache; a ranged request
        // wants a slice this cache does not store. All three pass straight through to origin, headers
        // and body intact, so authenticated publish and resumable downloads still work transparently.
        let authenticated = PRIVATE_REQUEST_HEADERS
            .iter()
            .any(|name| headers.contains_key(*name));
        if method != Method::GET || authenticated || headers.contains_key("range") {
            return self.passthrough(method, &url, headers, body).await;
        }

        self.maybe_sweep().await;
        let key = cache_key(
            host,
            &path_q,
            header(headers, "accept-encoding").unwrap_or(""),
        );
        let dir = self.root.join(sanitize(host));
        let meta_path = dir.join(format!("{key}.meta"));
        let body_path = dir.join(format!("{key}.body"));
        let _held = self.in_use.guard(&body_path);

        if let Some(meta) = self.load_meta(&meta_path, &body_path, host, &key).await {
            let age = now().saturating_sub(meta.stored_at);
            if age < meta.fresh_for {
                if let Some(resp) = serve_file(&meta, &body_path, "HIT").await {
                    return resp;
                }
            } else if meta.etag.is_some() || meta.last_modified.is_some() {
                match self.revalidate(&url, headers, &meta).await {
                    Revalidation::Fresh => {
                        self.refresh_meta(&meta_path, &meta).await;
                        if let Some(resp) = serve_file(&meta, &body_path, "REVALIDATED").await {
                            return resp;
                        }
                    }
                    Revalidation::Changed(resp) => {
                        return self
                            .store_or_relay(resp, &dir, &meta_path, &body_path)
                            .await;
                    }
                    Revalidation::Unreachable => {
                        return (StatusCode::BAD_GATEWAY, "origin unreachable").into_response();
                    }
                }
            }
        }

        match self.origin_get(&url, headers).await {
            Ok(resp) => {
                self.store_or_relay(resp, &dir, &meta_path, &body_path)
                    .await
            }
            Err(_) => (StatusCode::BAD_GATEWAY, "origin unreachable").into_response(),
        }
    }

    /// Read the local entry, or restore it from the durable tier on a cold miss so a rolled pod does
    /// not re-fetch what a peer already cached. Returns None when neither has it.
    async fn load_meta(
        &self,
        meta_path: &Path,
        body_path: &Path,
        host: &str,
        key: &str,
    ) -> Option<Meta> {
        if let Some(meta) = read_meta(meta_path).await {
            return Some(meta);
        }
        if self
            .durable
            .get_file(&durable_key(host, key, "meta"), meta_path)
            .await
            && self
                .durable
                .get_file(&durable_key(host, key, "body"), body_path)
                .await
        {
            return read_meta(meta_path).await;
        }
        let _ = tokio::fs::remove_file(meta_path).await;
        None
    }

    async fn revalidate(&self, url: &str, headers: &HeaderMap, meta: &Meta) -> Revalidation {
        let mut req = self.client.get(url);
        req = forward_headers(req, headers);
        if let Some(etag) = &meta.etag {
            req = req.header("if-none-match", etag);
        }
        if let Some(lm) = &meta.last_modified {
            req = req.header("if-modified-since", lm);
        }
        match req.send().await {
            Ok(resp) if resp.status() == StatusCode::NOT_MODIFIED => Revalidation::Fresh,
            Ok(resp) => Revalidation::Changed(resp),
            Err(e) => {
                tracing::warn!(error = %e, url, "pkg revalidation failed");
                Revalidation::Unreachable
            }
        }
    }

    async fn origin_get(
        &self,
        url: &str,
        headers: &HeaderMap,
    ) -> reqwest::Result<reqwest::Response> {
        forward_headers(self.client.get(url), headers).send().await
    }

    /// Cache the response if the origin marks it storable, then serve it. A storable body is written
    /// to a temp file and committed — renamed into place with its meta — before the response returns,
    /// so the very next request is a hit and a dropped download never leaves a half-written entry. A
    /// non-storable body streams straight through, uncached. The cold miss pays a full download before
    /// its first byte; every warm request streams from disk. The commit snapshots to the durable tier
    /// off the response path.
    async fn store_or_relay(
        &self,
        resp: reqwest::Response,
        dir: &Path,
        meta_path: &Path,
        body_path: &Path,
    ) -> Response {
        let status = resp.status();
        let Some(fresh_for) = storable(status, resp.headers()) else {
            return relay(resp, "MISS");
        };
        if tokio::fs::create_dir_all(dir).await.is_err() {
            return relay(resp, "MISS");
        }
        let meta = Meta::from_response(status.as_u16(), fresh_for, resp.headers());
        let tmp_body = writing_temp(body_path);
        if let Err(e) = download_to_file(resp, &tmp_body).await {
            tracing::warn!(error = %e, "pkg cache download failed");
            let _ = tokio::fs::remove_file(&tmp_body).await;
            return (StatusCode::BAD_GATEWAY, "origin unreachable").into_response();
        }
        let serve_path = if self.commit(&meta, &tmp_body, meta_path, body_path).await {
            body_path.to_path_buf()
        } else {
            tmp_body.clone()
        };
        let resp = serve_file(&meta, &serve_path, "MISS").await;
        if serve_path == tmp_body {
            let _ = tokio::fs::remove_file(&tmp_body).await;
        }
        resp.unwrap_or_else(|| (StatusCode::BAD_GATEWAY, "cache serve failed").into_response())
    }

    /// Rename the downloaded body and its meta into place, then snapshot both to the durable tier off
    /// the response path. False on any rename failure — the caller serves the download uncommitted.
    async fn commit(
        &self,
        meta: &Meta,
        tmp_body: &Path,
        meta_path: &Path,
        body_path: &Path,
    ) -> bool {
        let Ok(meta_bytes) = serde_json::to_vec(meta) else {
            return false;
        };
        let tmp_meta = writing_temp(meta_path);
        if tokio::fs::write(&tmp_meta, &meta_bytes).await.is_err()
            || tokio::fs::rename(tmp_body, body_path).await.is_err()
        {
            let _ = tokio::fs::remove_file(&tmp_meta).await;
            return false;
        }
        if tokio::fs::rename(&tmp_meta, meta_path).await.is_err() {
            return false;
        }
        let durable = self.durable.clone();
        let dkey_body = durable_key_from(body_path, "body");
        let dkey_meta = durable_key_from(body_path, "meta");
        let body_path = body_path.to_path_buf();
        let meta_path = meta_path.to_path_buf();
        tokio::spawn(async move {
            durable.put_file(&dkey_body, &body_path).await;
            durable.put_file(&dkey_meta, &meta_path).await;
        });
        true
    }

    async fn passthrough(
        &self,
        method: &Method,
        url: &str,
        headers: &HeaderMap,
        body: Bytes,
    ) -> Response {
        let mut req = self.client.request(reqwest_method(method), url);
        req = forward_headers(req, headers);
        if !body.is_empty() {
            req = req.body(body.to_vec());
        }
        match req.send().await {
            Ok(resp) => relay(resp, "PASS"),
            Err(e) => {
                tracing::warn!(error = %e, url, "pkg passthrough failed");
                (StatusCode::BAD_GATEWAY, "origin unreachable").into_response()
            }
        }
    }

    async fn refresh_meta(&self, meta_path: &Path, meta: &Meta) {
        let refreshed = Meta {
            stored_at: now(),
            ..meta.clone()
        };
        if let Ok(bytes) = serde_json::to_vec(&refreshed) {
            let tmp = writing_temp(meta_path);
            if tokio::fs::write(&tmp, &bytes).await.is_ok() {
                let _ = tokio::fs::rename(&tmp, meta_path).await;
            }
        }
    }

    async fn maybe_sweep(&self) {
        {
            let mut last = self.last_sweep.lock().await;
            if last.is_some_and(|t| t.elapsed() < PKG_SWEEP_INTERVAL) {
                return;
            }
            *last = Some(Instant::now());
        }
        let root = self.root.clone();
        let limit = self.disk_limit;
        let in_use = self.in_use.clone();
        tokio::task::spawn_blocking(move || sweep_pkg(&root, limit, &in_use));
    }
}

enum Revalidation {
    Fresh,
    Changed(reqwest::Response),
    Unreachable,
}

#[derive(Clone, Serialize, Deserialize)]
struct Meta {
    status: u16,
    stored_at: u64,
    fresh_for: u64,
    etag: Option<String>,
    last_modified: Option<String>,
    headers: Vec<(String, String)>,
}

impl Meta {
    fn from_response(status: u16, fresh_for: u64, headers: &reqwest::header::HeaderMap) -> Self {
        let kept: Vec<(String, String)> = REPLAY_HEADERS
            .iter()
            .filter_map(|name| {
                headers
                    .get(*name)
                    .and_then(|v| v.to_str().ok())
                    .map(|v| ((*name).to_string(), v.to_string()))
            })
            .collect();
        Self {
            status,
            stored_at: now(),
            fresh_for,
            etag: reqwest_header(headers, "etag"),
            last_modified: reqwest_header(headers, "last-modified"),
            headers: kept,
        }
    }

    fn replay_headers(&self) -> Vec<(String, String)> {
        self.headers.clone()
    }
}

/// Stream the origin body to a temp file, failing loud on a read or write error so a truncated
/// download is never committed.
async fn download_to_file(resp: reqwest::Response, path: &Path) -> Result<(), String> {
    let mut file = tokio::fs::File::create(path)
        .await
        .map_err(|e| format!("create {path:?}: {e}"))?;
    let mut stream = resp.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let bytes = chunk.map_err(|e| format!("origin stream: {e}"))?;
        file.write_all(&bytes)
            .await
            .map_err(|e| format!("write body: {e}"))?;
    }
    file.flush().await.map_err(|e| format!("flush body: {e}"))?;
    Ok(())
}

async fn serve_file(meta: &Meta, body_path: &Path, cache_status: &str) -> Option<Response> {
    let file = tokio::fs::File::open(body_path).await.ok()?;
    let len = file.metadata().await.ok()?.len();
    let _ = set_file_mtime(body_path, FileTime::now());
    let stream = tokio_util::io::ReaderStream::with_capacity(file, READ_CHUNK);
    Some(build_response(
        StatusCode::from_u16(meta.status).unwrap_or(StatusCode::OK),
        &meta.replay_headers(),
        Some(len),
        cache_status,
        Body::from_stream(stream),
    ))
}

fn relay(resp: reqwest::Response, cache_status: &str) -> Response {
    let status = resp.status();
    let replay: Vec<(String, String)> = resp
        .headers()
        .iter()
        .filter_map(|(k, v)| {
            v.to_str()
                .ok()
                .map(|v| (k.as_str().to_string(), v.to_string()))
        })
        .filter(|(k, _)| !is_hop_by_hop(k))
        .collect();
    let body = Body::from_stream(resp.bytes_stream());
    build_response(status, &replay, None, cache_status, body)
}

fn build_response(
    status: StatusCode,
    replay: &[(String, String)],
    content_length: Option<u64>,
    cache_status: &str,
    body: Body,
) -> Response {
    let mut builder = Response::builder().status(status);
    for (name, value) in replay {
        if !is_hop_by_hop(name) {
            builder = builder.header(name, value);
        }
    }
    if let Some(len) = content_length {
        builder = builder.header("content-length", len);
    }
    builder
        .header("x-ufo-cache", cache_status)
        .body(body)
        .unwrap_or_else(|_| {
            (StatusCode::BAD_GATEWAY, "cache response build failed").into_response()
        })
}

/// The freshness lifetime the origin grants a shared cache, or None when the response must not be
/// stored. Only a `200` with a positive lifetime (`s-maxage`/`max-age`) or `immutable` is stored;
/// `no-store`/`private`, a `Vary` on anything but encoding, and a body with no freshness signal are
/// all passed through instead of guessed at.
fn storable(status: StatusCode, headers: &reqwest::header::HeaderMap) -> Option<u64> {
    if status != StatusCode::OK {
        return None;
    }
    if let Some(vary) = reqwest_header(headers, "vary") {
        let varies_beyond_encoding = vary
            .split(',')
            .map(|t| t.trim().to_ascii_lowercase())
            .any(|t| !t.is_empty() && t != "accept-encoding");
        if vary.trim() == "*" || varies_beyond_encoding {
            return None;
        }
    }
    let cc = reqwest_header(headers, "cache-control").unwrap_or_default();
    let mut max_age = None;
    let mut s_maxage = None;
    let mut immutable = false;
    for directive in cc.split(',') {
        let directive = directive.trim().to_ascii_lowercase();
        let (name, value) = match directive.split_once('=') {
            Some((n, v)) => (n.trim(), Some(v.trim().trim_matches('"').to_string())),
            None => (directive.as_str(), None),
        };
        match name {
            "no-store" | "private" => return None,
            "immutable" => immutable = true,
            "s-maxage" => s_maxage = value.and_then(|v| v.parse::<u64>().ok()),
            "max-age" => max_age = value.and_then(|v| v.parse::<u64>().ok()),
            _ => {}
        }
    }
    let fresh_for = s_maxage.or(max_age).unwrap_or(0);
    match (fresh_for, immutable) {
        (0, false) => None,
        (0, true) => Some(IMMUTABLE_SECS),
        (n, _) => Some(n),
    }
}

/// Relay the sandbox's own request headers to origin, so an authenticated publish or a bespoke
/// user-agent reaches the registry unchanged. Dropped: hop-by-hop controls, the `Host` (reqwest sets
/// it from the URL), the proxy-stamped `x-ufo-*` identity, and the client's own cache validators —
/// this cache manages revalidation itself.
fn forward_headers(req: reqwest::RequestBuilder, headers: &HeaderMap) -> reqwest::RequestBuilder {
    let mut req = req;
    for (name, value) in headers {
        let name = name.as_str();
        if is_hop_by_hop(name)
            || name == "host"
            || name.starts_with("x-ufo-")
            || matches!(
                name,
                "if-none-match"
                    | "if-modified-since"
                    | "if-match"
                    | "if-unmodified-since"
                    | "if-range"
            )
        {
            continue;
        }
        if let Ok(value) = value.to_str() {
            req = req.header(name, value);
        }
    }
    req
}

fn cache_key(host: &str, path_q: &str, accept_encoding: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(host.as_bytes());
    hasher.update(b"\n");
    hasher.update(path_q.as_bytes());
    hasher.update(b"\n");
    hasher.update(accept_encoding.as_bytes());
    hex::encode(hasher.finalize())
}

fn durable_key(host: &str, key: &str, ext: &str) -> String {
    format!("pkg/{}/{key}.{ext}", sanitize(host))
}

/// The durable key for `.../pkg/<host>/<key>.body` (or `.meta`), rebuilt from the on-disk path so the
/// tee task carries no extra host/key state.
fn durable_key_from(body_path: &Path, ext: &str) -> String {
    let key = body_path.file_stem().and_then(|s| s.to_str()).unwrap_or("");
    let host = body_path
        .parent()
        .and_then(|p| p.file_name())
        .and_then(|s| s.to_str())
        .unwrap_or("");
    format!("pkg/{host}/{key}.{ext}")
}

fn with_suffix(path: &Path, suffix: &str) -> PathBuf {
    let mut name = path.as_os_str().to_owned();
    name.push(suffix);
    PathBuf::from(name)
}

async fn read_meta(meta_path: &Path) -> Option<Meta> {
    let bytes = tokio::fs::read(meta_path).await.ok()?;
    serde_json::from_slice(&bytes).ok()
}

fn header<'a>(headers: &'a HeaderMap, name: &str) -> Option<&'a str> {
    headers.get(name).and_then(|v| v.to_str().ok())
}

fn reqwest_header(headers: &reqwest::header::HeaderMap, name: &str) -> Option<String> {
    headers
        .get(name)
        .and_then(|v| v.to_str().ok())
        .map(str::to_string)
}

fn reqwest_method(method: &Method) -> reqwest::Method {
    reqwest::Method::from_bytes(method.as_str().as_bytes()).unwrap_or(reqwest::Method::GET)
}

fn is_hop_by_hop(name: &str) -> bool {
    matches!(
        name.to_ascii_lowercase().as_str(),
        "connection"
            | "keep-alive"
            | "proxy-authenticate"
            | "proxy-authorization"
            | "te"
            | "trailer"
            | "transfer-encoding"
            | "upgrade"
            | "content-length"
    )
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Evict least-recently-used cache entries until the pkg tree is under `limit`. Mirrors the git
/// sweep: reserve each victim with an atomic rename-aside taken only while no request holds it, then
/// delete the body and its meta outside the lock, so an in-use entry is never removed mid-serve.
fn sweep_pkg(root: &Path, limit: u64, in_use: &InUse) {
    let mut entries = Vec::new();
    let mut total = 0u64;
    collect_entries(root, &mut entries, &mut total);
    if total <= limit {
        return;
    }
    entries.sort_by_key(|(_, _, mtime)| *mtime);
    let mut remaining = total;
    for (body_path, size, _) in entries {
        if remaining <= limit {
            break;
        }
        let evicting = with_suffix(&body_path, ".evicting");
        let _ = std::fs::remove_file(&evicting);
        let reserved = in_use.reserve_if_free(&body_path, || {
            std::fs::rename(&body_path, &evicting).is_ok()
        });
        if reserved {
            remaining = remaining.saturating_sub(size);
            let _ = std::fs::remove_file(&evicting);
            let meta = body_path.with_extension("meta");
            let _ = std::fs::remove_file(&meta);
        }
    }
}

fn collect_entries(dir: &Path, out: &mut Vec<(PathBuf, u64, u64)>, total: &mut u64) {
    let Ok(read) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in read.flatten() {
        let path = entry.path();
        let Ok(meta) = entry.metadata() else {
            continue;
        };
        let mtime = meta
            .modified()
            .ok()
            .and_then(|m| m.duration_since(UNIX_EPOCH).ok())
            .map(|d| d.as_secs())
            .unwrap_or(0);
        match path.extension().and_then(|e| e.to_str()) {
            _ if meta.is_dir() => collect_entries(&path, out, total),
            Some("body") => {
                *total += meta.len();
                out.push((path, meta.len(), mtime));
            }
            Some("meta") => *total += meta.len(),
            Some("writing") if now().saturating_sub(mtime) > WRITING_ORPHAN_GRACE_SECS => {
                // A crashed or dropped download's orphan: no writer has touched it in an hour.
                let _ = std::fs::remove_file(&path);
            }
            Some("writing") => *total += meta.len(),
            _ => {}
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cc(value: &str) -> reqwest::header::HeaderMap {
        let mut h = reqwest::header::HeaderMap::new();
        h.insert("cache-control", value.parse().unwrap());
        h
    }

    #[test]
    fn immutable_and_long_max_age_are_stored() {
        assert_eq!(
            storable(StatusCode::OK, &cc("public, max-age=600")),
            Some(600)
        );
        assert_eq!(
            storable(StatusCode::OK, &cc("public, immutable")),
            Some(IMMUTABLE_SECS)
        );
        assert_eq!(
            storable(StatusCode::OK, &cc("public, max-age=31536000, immutable")),
            Some(31536000)
        );
    }

    #[test]
    fn s_maxage_wins_for_a_shared_cache() {
        assert_eq!(
            storable(StatusCode::OK, &cc("max-age=60, s-maxage=600")),
            Some(600)
        );
    }

    #[test]
    fn uncacheable_responses_are_not_stored() {
        assert_eq!(storable(StatusCode::OK, &cc("no-store")), None);
        assert_eq!(storable(StatusCode::OK, &cc("private, max-age=600")), None);
        assert_eq!(storable(StatusCode::OK, &cc("no-cache")), None);
        assert_eq!(storable(StatusCode::OK, &cc("max-age=0")), None);
        assert_eq!(
            storable(StatusCode::OK, &reqwest::header::HeaderMap::new()),
            None
        );
        assert_eq!(storable(StatusCode::NOT_FOUND, &cc("max-age=600")), None);
    }

    #[test]
    fn a_vary_beyond_encoding_is_not_stored() {
        let mut h = cc("max-age=600");
        h.insert("vary", "accept-encoding".parse().unwrap());
        assert_eq!(storable(StatusCode::OK, &h), Some(600));
        h.insert("vary", "accept-encoding, authorization".parse().unwrap());
        assert_eq!(storable(StatusCode::OK, &h), None);
    }

    #[test]
    fn the_key_separates_hosts_paths_and_encodings() {
        let a = cache_key("registry.npmjs.org", "lodash", "gzip");
        assert_ne!(a, cache_key("pypi.org", "lodash", "gzip"));
        assert_ne!(a, cache_key("registry.npmjs.org", "left-pad", "gzip"));
        assert_ne!(a, cache_key("registry.npmjs.org", "lodash", "identity"));
        assert_eq!(a, cache_key("registry.npmjs.org", "lodash", "gzip"));
    }

    #[test]
    fn durable_key_from_path_matches_the_write_key() {
        let body = Path::new("/state/pkg/registry.npmjs.org/abc123.body");
        assert_eq!(
            durable_key_from(body, "body"),
            durable_key("registry.npmjs.org", "abc123", "body")
        );
        assert_eq!(
            durable_key_from(body, "meta"),
            durable_key("registry.npmjs.org", "abc123", "meta")
        );
    }
}
