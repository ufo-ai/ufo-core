use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use std::time::{Duration, Instant, UNIX_EPOCH};

use axum::body::Bytes;
use axum::http::{HeaderMap, Method, StatusCode};
use axum::response::{IntoResponse, Response};
use base64::Engine;
use tokio::io::AsyncWriteExt;
use tokio::process::Command;
use tokio::sync::Mutex;

use crate::cgi::response_from_cgi;
use crate::creds::{CredentialClient, Resolved};
use crate::durable::Durable;
use crate::inuse::InUse;

/// A warm mirror is re-snapshotted to the durable tier at most this often.
const SNAPSHOT_INTERVAL: Duration = Duration::from_secs(600);
/// The mirror tree is swept back under its ceiling at most this often.
const MIRROR_SWEEP_INTERVAL: Duration = Duration::from_secs(120);
const MAX_UPLOAD_PACK_BYTES: usize = 128 * 1024 * 1024;

#[derive(Clone, Copy)]
enum Endpoint {
    Info,
    UploadPack,
}

#[derive(Default)]
struct MirrorState {
    last_snapshot: Option<Instant>,
}

/// Serves the git smart-HTTP fetch protocol from per-principal bare mirrors. All wire-protocol work
/// is done by the `git` binary (`clone --mirror`, `fetch`, `http-backend`, `bundle`); this type owns
/// mirror freshness, isolation, credential injection, durable snapshot/restore, and bounding the
/// mirror tree's disk.
pub struct GitStrategy {
    state_root: PathBuf,
    creds: Arc<CredentialClient>,
    scheme: String,
    durable: Arc<Durable>,
    mirror_limit: u64,
    mirrors: Mutex<HashMap<PathBuf, Arc<Mutex<MirrorState>>>>,
    last_sweep: Mutex<Option<Instant>>,
    in_use: InUse,
}

impl GitStrategy {
    pub fn new(
        state_root: PathBuf,
        creds: Arc<CredentialClient>,
        scheme: String,
        durable: Arc<Durable>,
        mirror_limit: u64,
    ) -> Self {
        Self {
            state_root,
            creds,
            scheme,
            durable,
            mirror_limit,
            mirrors: Mutex::new(HashMap::new()),
            last_sweep: Mutex::new(None),
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
        let Some((endpoint, repo)) = classify(tail) else {
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

        // Every request refreshes the mirror: a read right after a push is never stale, and — since
        // `info/refs` and `git-upload-pack` are separate connections that a load balancer may route
        // to different replicas — the pod serving upload-pack must fetch the refs it will serve
        // rather than trust a peer's mirror.
        if let Err(e) = self.ensure_fresh(&mirror, host, &repo, &resolved).await {
            tracing::warn!(error = %e, host, repo, "mirror ensure failed");
            return (StatusCode::BAD_GATEWAY, "upstream unavailable").into_response();
        }

        let path_info = match endpoint {
            Endpoint::Info => format!("/{repo}.git/info/refs"),
            Endpoint::UploadPack => format!("/{repo}.git/git-upload-pack"),
        };
        match self
            .serve(&host_root, &path_info, method, query, headers, body)
            .await
        {
            Ok(resp) => resp,
            Err(e) => {
                tracing::warn!(error = %e, "http-backend failed");
                (StatusCode::BAD_GATEWAY, "git backend failed").into_response()
            }
        }
    }

    async fn ensure_fresh(
        &self,
        mirror: &Path,
        host: &str,
        repo: &str,
        resolved: &Resolved,
    ) -> Result<(), String> {
        // An authenticated mirror must never be served without a current successful fetch: a token
        // the org has since revoked would otherwise keep reading a cached private history. An
        // anonymous mirror (public repo) may serve stale through an upstream blip.
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
            if self.restore_mirror(mirror, host, repo, resolved).await? {
                // A restore trusts a bundle taken under a past authorization; re-confirm access.
                if let Err(e) = self.fetch_mirror(mirror, resolved).await {
                    if authenticated {
                        let _ = tokio::fs::remove_dir_all(mirror).await;
                        return Err(format!("restore re-auth failed: {e}"));
                    }
                    tracing::warn!(error = %e, "restored mirror serves stale (anonymous)");
                }
            } else {
                self.clone_mirror(mirror, host, repo, resolved).await?;
                self.spawn_snapshot(mirror, resolved.principal.clone(), host, repo);
            }
            state.last_snapshot = Some(now);
        } else {
            match self.fetch_mirror(mirror, resolved).await {
                Ok(()) => {
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

    async fn serve(
        &self,
        host_root: &Path,
        path_info: &str,
        method: &Method,
        query: Option<&str>,
        headers: &HeaderMap,
        body: Bytes,
    ) -> Result<Response, String> {
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
                .write_all(&body)
                .await
                .map_err(|e| format!("write cgi stdin: {e}"))?;
        }
        response_from_cgi(child).await
    }
}

fn classify(tail: &str) -> Option<(Endpoint, String)> {
    if let Some(repo) = tail.strip_suffix("/info/refs") {
        return Some((Endpoint::Info, safe_repo(repo)?));
    }
    if let Some(repo) = tail.strip_suffix("/git-upload-pack") {
        return Some((Endpoint::UploadPack, safe_repo(repo)?));
    }
    None
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
    let mut cmd = Command::new("git");
    if let Some(dir) = git_dir {
        cmd.arg("--git-dir").arg(dir);
    }
    cmd.args(args)
        .env("GIT_TERMINAL_PROMPT", "0")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped());
    let output = cmd.output().await.map_err(|e| format!("spawn git: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "git {}: {}",
            args.first().map(String::as_str).unwrap_or(""),
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(())
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

    use super::{safe_repo, sweep_mirrors};
    use crate::inuse::InUse;

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
