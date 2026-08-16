// Each integration-test binary links only the helpers it uses; the rest look dead per-binary.
#![allow(dead_code)]

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::{Request, State};
use axum::http::{HeaderMap, Method, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{any, post};
use axum::{Json, Router};
use tokio::io::AsyncWriteExt;
use tokio::net::TcpListener;
use ufo_cache::cgi::response_from_cgi;

/// Bind an ephemeral loopback port and serve `router`; returns the bound address.
pub async fn spawn(router: Router) -> std::net::SocketAddr {
    spawn_abortable(router).await.0
}

/// Like `spawn`, but also returns the task handle so a test can `.abort()` it to make the port dead
/// (e.g. to prove a cold daemon restores from the durable tier without reaching origin).
pub async fn spawn_abortable(
    router: Router,
) -> (std::net::SocketAddr, tokio::task::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let handle = tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    (addr, handle)
}

/// A control plane that answers every credential callback with the given JSON body and counts calls.
pub fn control_plane(body: serde_json::Value) -> (Router, Arc<AtomicUsize>) {
    let hits = Arc::new(AtomicUsize::new(0));
    let state = (Arc::new(body), hits.clone());
    let router = Router::new()
        .route(
            "/internal/git-credential",
            post(
                |State((body, hits)): State<(Arc<serde_json::Value>, Arc<AtomicUsize>)>| async move {
                    hits.fetch_add(1, Ordering::SeqCst);
                    Json((*body).clone())
                },
            ),
        )
        .with_state(state);
    (router, hits)
}

/// A registry-like host: an immutable artifact (long max-age) and a no-store metadata document,
/// counting every upstream fetch so a test can prove the artifact was fetched once.
pub fn registry() -> (Router, Arc<AtomicUsize>) {
    let hits = Arc::new(AtomicUsize::new(0));
    let state = hits.clone();
    let router = Router::new()
        .route(
            "/pkg/thing.tgz",
            any(|State(hits): State<Arc<AtomicUsize>>| async move {
                hits.fetch_add(1, Ordering::SeqCst);
                (
                    [
                        ("content-type", "application/octet-stream"),
                        ("cache-control", "public, max-age=31557600, immutable"),
                    ],
                    "TARBALL",
                )
            }),
        )
        .route(
            "/meta.json",
            any(|State(hits): State<Arc<AtomicUsize>>| async move {
                hits.fetch_add(1, Ordering::SeqCst);
                ([("cache-control", "no-store")], "META")
            }),
        )
        .with_state(state);
    (router, hits)
}

/// A real git smart-HTTP upstream serving bare repos under `project_root` via `git http-backend`,
/// counting requests so a test can prove the cache fetched only once.
pub fn git_upstream(project_root: PathBuf) -> (Router, Arc<AtomicUsize>) {
    let hits = Arc::new(AtomicUsize::new(0));
    let state = (Arc::new(project_root), hits.clone());
    let router = Router::new().fallback(any(git_backend)).with_state(state);
    (router, hits)
}

async fn git_backend(
    State((root, hits)): State<(Arc<PathBuf>, Arc<AtomicUsize>)>,
    req: Request,
) -> Response {
    hits.fetch_add(1, Ordering::SeqCst);
    let (parts, body) = req.into_parts();
    let bytes = axum::body::to_bytes(body, 64 * 1024 * 1024).await.unwrap();
    match run_backend(
        root.as_path(),
        &parts.method,
        parts.uri.path(),
        parts.uri.query(),
        &parts.headers,
        bytes,
    )
    .await
    {
        Ok(r) => r,
        Err(e) => (StatusCode::BAD_GATEWAY, e).into_response(),
    }
}

async fn run_backend(
    root: &Path,
    method: &Method,
    path: &str,
    query: Option<&str>,
    headers: &HeaderMap,
    body: Bytes,
) -> Result<Response, String> {
    let mut cmd = tokio::process::Command::new("git");
    cmd.arg("http-backend")
        .env_clear()
        .env("PATH", std::env::var("PATH").unwrap_or_default())
        .env("GIT_HTTP_EXPORT_ALL", "1")
        .env("GIT_PROJECT_ROOT", root)
        .env("PATH_INFO", path)
        .env("REQUEST_METHOD", method.as_str())
        .env("QUERY_STRING", query.unwrap_or(""))
        .env("GIT_TERMINAL_PROMPT", "0")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    if let Some(ct) = headers.get("content-type").and_then(|v| v.to_str().ok()) {
        cmd.env("CONTENT_TYPE", ct);
    }
    if let Some(proto) = headers.get("git-protocol").and_then(|v| v.to_str().ok()) {
        cmd.env("GIT_PROTOCOL", proto);
    }
    if method == Method::POST {
        cmd.env("CONTENT_LENGTH", body.len().to_string());
    }
    let mut child = cmd.spawn().map_err(|e| e.to_string())?;
    if let Some(mut stdin) = child.stdin.take() {
        stdin.write_all(&body).await.map_err(|e| e.to_string())?;
    }
    response_from_cgi(child).await
}
