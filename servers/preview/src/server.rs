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
const FORWARDED_WORKSPACE_HEADER: &str = "x-ufo-workspace";

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

async fn render_within_deadline(
    state: AppState,
    headers: HeaderMap,
    multipart: Multipart,
) -> Response {
    let _permit = state
        .render
        .semaphore()
        .clone()
        .acquire_owned()
        .await
        .expect("render semaphore remains open");
    let (request, file) = match parse_parts(multipart, state.cfg.max_input_bytes).await {
        Ok(p) => p,
        Err(r) => return r.into_response(),
    };
    if !admits(&request, &file, &headers, &state.cfg.token) {
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

fn admits(
    request: &RenderRequest,
    file: &Option<Vec<u8>>,
    headers: &HeaderMap,
    token: &str,
) -> bool {
    if request.kind == "site" {
        return !headers.contains_key(FORWARDED_WORKSPACE_HEADER)
            && file.is_none()
            && request.source_url.is_some()
            && !matches!(&request.sink, SinkSpec::Bundle { .. })
            && authorized(headers, token);
    }
    if !headers.contains_key(FORWARDED_WORKSPACE_HEADER) {
        return matches!(&request.sink, SinkSpec::PutUrl { .. }) || authorized(headers, token);
    }
    let direct_file = file.is_some() && request.source_url.is_none();
    match &request.sink {
        SinkSpec::PutUrl { .. } => direct_file,
        SinkSpec::Bundle { bundle: true } => {
            direct_file
                && authorized(headers, token)
                && matches!(request.kind.as_str(), "pdf" | "pptx" | "docx" | "xlsx")
        }
        SinkSpec::Inline { .. } | SinkSpec::Bundle { .. } => false,
    }
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
