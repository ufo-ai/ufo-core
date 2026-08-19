use std::sync::Arc;

use axum::extract::{DefaultBodyLimit, Multipart, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::Router;

use crate::config::Config;
use crate::refusal::Refusal;
use crate::render::{Render, RenderRequest, SinkSpec};
use crate::sink;

const MULTIPART_OVERHEAD_BYTES: u64 = 1024 * 1024;

#[derive(Clone)]
struct AppState {
    cfg: Arc<Config>,
    render: Render,
}

pub fn app(cfg: Arc<Config>) -> Router {
    let render = Render::new(cfg.clone());
    app_with(cfg, render)
}

pub fn app_with(cfg: Arc<Config>, render: Render) -> Router {
    let body_limit = (cfg.max_input_bytes + MULTIPART_OVERHEAD_BYTES) as usize;
    Router::new()
        .route("/_health", get(|| async { "ok" }))
        .route("/render", post(render_route))
        .layer(DefaultBodyLimit::max(body_limit))
        .with_state(AppState { cfg, render })
}

async fn render_route(
    State(state): State<AppState>,
    headers: HeaderMap,
    multipart: Multipart,
) -> Response {
    let deadline = state.cfg.request_timeout;
    match tokio::time::timeout(deadline, render_within_deadline(state, headers, multipart)).await {
        Ok(response) => response,
        Err(_) => Refusal::RenderTimeout(format!("request exceeded {deadline:?}")).into_response(),
    }
}

/// The route body proper, run under `render_route`'s deadline: the permit is acquired here, so a
/// caller trickling a body (or any other slow phase) is dropped with it on expiry, freeing the
/// permit for the next request rather than pinning it forever.
async fn render_within_deadline(
    state: AppState,
    headers: HeaderMap,
    multipart: Multipart,
) -> Response {
    let _permit = match state.render.semaphore().clone().try_acquire_owned() {
        Ok(permit) => permit,
        Err(_) => return Refusal::Busy.into_response(),
    };
    let (request, file) = match parse_parts(multipart, state.cfg.max_input_bytes).await {
        Ok(p) => p,
        Err(r) => return r.into_response(),
    };
    if !admits(&request.sink, &headers, &state.cfg.token) {
        return (StatusCode::UNAUTHORIZED, "bearer token required").into_response();
    }
    match state.render.handle(&request, file).await {
        Ok(rendered) => match sink::deliver(rendered, &request.sink, &state.cfg).await {
            Ok(resp) => resp,
            Err(r) => r.into_response(),
        },
        Err(r) => r.into_response(),
    }
}

/// A `put_url` sink is its own capability: the caller-minted presigned PUT is authority to store to
/// exactly that key, so the service admits that request without a bearer. `inline` returns bytes to
/// the caller directly and carries no such capability, so it still needs one.
fn admits(sink: &SinkSpec, headers: &HeaderMap, token: &str) -> bool {
    matches!(sink, SinkSpec::PutUrl { .. }) || authorized(headers, token)
}

fn authorized(headers: &HeaderMap, token: &str) -> bool {
    headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .is_some_and(|presented| presented == token)
}

async fn parse_parts(
    mut multipart: Multipart,
    max_input: u64,
) -> Result<(RenderRequest, Option<Vec<u8>>), Refusal> {
    let mut request: Option<RenderRequest> = None;
    let mut file: Option<Vec<u8>> = None;
    while let Some(part) = multipart
        .next_field()
        .await
        .map_err(|e| Refusal::UnsupportedType(format!("multipart: {e}")))?
    {
        match part.name() {
            Some("request") => {
                let text = part
                    .text()
                    .await
                    .map_err(|e| Refusal::UnsupportedType(format!("request part: {e}")))?;
                request = Some(
                    serde_json::from_str(&text)
                        .map_err(|e| Refusal::UnsupportedType(format!("request json: {e}")))?,
                );
            }
            Some("file") => {
                let bytes = part
                    .bytes()
                    .await
                    .map_err(|e| Refusal::TooLarge(format!("file part: {e}")))?;
                if bytes.len() as u64 > max_input {
                    return Err(Refusal::TooLarge(format!(
                        "input exceeds {max_input} bytes"
                    )));
                }
                file = Some(bytes.to_vec());
            }
            _ => {}
        }
    }
    let request =
        request.ok_or_else(|| Refusal::UnsupportedType("request part is required".into()))?;
    Ok((request, file))
}
