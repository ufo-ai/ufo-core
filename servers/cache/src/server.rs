use std::sync::Arc;

use axum::extract::{Request, State};
use axum::http::{HeaderMap, StatusCode, Uri};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::Router;

use crate::config::Config;
use crate::creds::CredentialClient;
use crate::durable::Durable;
use crate::git::GitStrategy;
use crate::pkg::PkgCache;

const MAX_BODY_BYTES: usize = 128 * 1024 * 1024;

#[derive(Clone)]
pub struct AppState {
    git: Arc<GitStrategy>,
    pkg: Arc<PkgCache>,
    allowed_git_hosts: Arc<Vec<String>>,
    allowed_pkg_hosts: Arc<Vec<String>>,
}

pub fn app(config: &Config, durable: Durable) -> Router {
    let creds = Arc::new(CredentialClient::new(
        &config.control_url,
        config.control_token.clone(),
    ));
    let durable = Arc::new(durable);
    let state = AppState {
        // Sibling roots, swept independently: the git strategy totals its mirror tree and its pack
        // cache separately, and the package sweep totals its own, so no tier's bytes charge against
        // another tier's ceiling.
        git: Arc::new(GitStrategy::new(config, creds, durable.clone())),
        pkg: Arc::new(PkgCache::new(
            config.state_root.join("pkg"),
            durable,
            config.upstream_scheme.clone(),
            config.pkg_disk_limit_bytes,
        )),
        allowed_git_hosts: Arc::new(config.allowed_git_hosts.clone()),
        allowed_pkg_hosts: Arc::new(config.allowed_pkg_hosts.clone()),
    };
    Router::new()
        .route("/_health", get(|| async { "ok" }))
        .fallback(dispatch)
        .with_state(state)
}

async fn dispatch(State(state): State<AppState>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let method = parts.method;
    let uri = parts.uri;
    let headers = parts.headers;

    let Some((kind, host, tail)) = split_target(&uri) else {
        return (StatusCode::NOT_FOUND, "no route").into_response();
    };

    // The daemon fetches only an allowlisted set of hosts. Anything else is refused before a URL is
    // built from it, so a sandbox cannot reach a private or in-cluster address through the cache.
    let allowed = match kind {
        "git" => state.allowed_git_hosts.iter().any(|h| h == host),
        "pkg" => state.allowed_pkg_hosts.iter().any(|h| h == host),
        _ => false,
    };
    if !allowed {
        return (StatusCode::NOT_FOUND, "host not cached").into_response();
    }

    let bytes = match axum::body::to_bytes(body, MAX_BODY_BYTES).await {
        Ok(b) => b,
        Err(_) => return (StatusCode::PAYLOAD_TOO_LARGE, "body too large").into_response(),
    };

    if kind == "pkg" {
        return state
            .pkg
            .handle(&method, host, tail, uri.query(), &headers, bytes)
            .await;
    }

    // A git request carries the identity the proxy stamped — workspace, user, and the run or probe
    // token the sandbox presented as `Proxy-Authorization` — which the daemon resolves its upstream
    // credential by; the package cache is anonymous and needs none.
    let workspace = trusted(&headers, "x-ufo-workspace");
    if workspace.is_empty() {
        return (StatusCode::BAD_REQUEST, "missing workspace").into_response();
    }
    let user = trusted(&headers, "x-ufo-user");
    let proxy_auth = trusted(&headers, "x-ufo-proxy-auth");
    state
        .git
        .handle(
            &method,
            host,
            tail,
            uri.query(),
            &headers,
            bytes,
            workspace,
            user,
            proxy_auth,
        )
        .await
}

/// `/git/<host>/<tail>` → (kind, host, tail). `tail` keeps its inner slashes.
fn split_target(uri: &Uri) -> Option<(&str, &str, &str)> {
    let mut it = uri.path().trim_start_matches('/').splitn(3, '/');
    let kind = it.next()?;
    let host = it.next()?;
    let tail = it.next()?;
    if kind.is_empty() || host.is_empty() || tail.is_empty() {
        return None;
    }
    Some((kind, host, tail))
}

fn trusted<'a>(headers: &'a HeaderMap, name: &str) -> &'a str {
    headers
        .get(name)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
}
