//! The founder-email operator surface. It is served on the workspace host beside core's other
//! operator surfaces, so the `ufo_debug` session core binds is the session this reads.

use axum::extract::{Path, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Redirect, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use hmac::{Hmac, Mac};
use serde::Deserialize;
use sha2::Sha256;
use uuid::Uuid;

use crate::campaign::{CampaignError, Campaigns, Draft};
use crate::gateway::{GatewayState, OPERATOR_COOKIE, OPERATOR_EMAIL_DOMAIN};
use crate::token;
use crate::workos::constant_time_eq;

pub const EMAIL_SURFACE_PATH: &str = "/surface/email";
pub const CSRF_HEADER: &str = "x-ufo-csrf";
pub const OPERATOR_LOGIN_PATH: &str = "/login?debug=1";

const CSRF_CONTEXT: &str = "email-hud:";
const HUD_PAGE: &str = include_str!("email_hud.html");

pub fn routes() -> Router<GatewayState> {
    Router::new()
        .route(EMAIL_SURFACE_PATH, get(page))
        .route("/surface/email/campaigns", get(listed).post(created))
        .route("/surface/email/campaigns/{id}", get(previewed))
        .route("/surface/email/campaigns/{id}/content", post(revised))
        .route("/surface/email/campaigns/{id}/test", post(tested))
        .route("/surface/email/campaigns/{id}/prepare", post(prepared))
        .route("/surface/email/campaigns/{id}/approve", post(approved))
        .route("/surface/email/campaigns/{id}/send", post(scheduled))
        .route("/surface/email/campaigns/{id}/cancel", post(cancelled))
}

/// A GET with no session bounces to the one sign-in page, where the operator mints a bearer and
/// binds the cookie. Every other route refuses, so a fetch never reads a redirect as data.
async fn page(State(state): State<GatewayState>, headers: HeaderMap) -> Response {
    match operator(&state, &headers) {
        Some(_) => (
            [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
            HUD_PAGE,
        )
            .into_response(),
        None => Redirect::to(OPERATOR_LOGIN_PATH).into_response(),
    }
}

async fn listed(State(state): State<GatewayState>, headers: HeaderMap) -> Response {
    let Some(operator) = operator(&state, &headers) else {
        return unauthorized();
    };
    let Some(campaigns) = state.campaigns.as_ref() else {
        return unavailable();
    };
    match campaigns.list().await {
        Ok(listed) => Json(serde_json::json!({
            "operator": operator,
            "senders": campaigns.sender.senders,
            "csrf": csrf_token(&state.onboarding.token_secret, &session(&headers).unwrap_or_default()),
            "campaigns": listed,
        }))
        .into_response(),
        Err(error) => refused(error),
    }
}

async fn previewed(
    State(state): State<GatewayState>,
    headers: HeaderMap,
    Path(id): Path<Uuid>,
) -> Response {
    let Some(campaigns) = read_guard(&state, &headers) else {
        return unauthorized();
    };
    answered(campaigns.preview(id).await)
}

#[derive(Deserialize)]
struct Composed {
    #[serde(flatten)]
    draft: Draft,
}

async fn created(
    State(state): State<GatewayState>,
    headers: HeaderMap,
    Json(composed): Json<Composed>,
) -> Response {
    let (campaigns, operator) = match write_guard(&state, &headers) {
        Ok(guarded) => guarded,
        Err(refusal) => return *refusal,
    };
    answered(campaigns.create(&operator, composed.draft).await)
}

#[derive(Deserialize)]
struct Revised {
    revision: i32,
    #[serde(flatten)]
    draft: Draft,
}

async fn revised(
    State(state): State<GatewayState>,
    headers: HeaderMap,
    Path(id): Path<Uuid>,
    Json(revised): Json<Revised>,
) -> Response {
    let (campaigns, _) = match write_guard(&state, &headers) {
        Ok(guarded) => guarded,
        Err(refusal) => return *refusal,
    };
    answered(campaigns.revise(id, revised.revision, revised.draft).await)
}

#[derive(Deserialize)]
struct AtRevision {
    revision: i32,
}

async fn tested(
    State(state): State<GatewayState>,
    headers: HeaderMap,
    Path(id): Path<Uuid>,
    Json(at): Json<AtRevision>,
) -> Response {
    let (campaigns, operator) = match write_guard(&state, &headers) {
        Ok(guarded) => guarded,
        Err(refusal) => return *refusal,
    };
    answered(campaigns.test(id, at.revision, &operator).await)
}

async fn prepared(
    State(state): State<GatewayState>,
    headers: HeaderMap,
    Path(id): Path<Uuid>,
    Json(at): Json<AtRevision>,
) -> Response {
    let (campaigns, _) = match write_guard(&state, &headers) {
        Ok(guarded) => guarded,
        Err(refusal) => return *refusal,
    };
    answered(campaigns.prepare(id, at.revision).await)
}

#[derive(Deserialize)]
struct Approval {
    revision: i32,
    count: i64,
}

async fn approved(
    State(state): State<GatewayState>,
    headers: HeaderMap,
    Path(id): Path<Uuid>,
    Json(approval): Json<Approval>,
) -> Response {
    let (campaigns, operator) = match write_guard(&state, &headers) {
        Ok(guarded) => guarded,
        Err(refusal) => return *refusal,
    };
    answered(
        campaigns
            .approve(id, approval.revision, approval.count, &operator)
            .await,
    )
}

#[derive(Deserialize)]
struct Schedule {
    revision: i32,
    at: Option<DateTime<Utc>>,
}

async fn scheduled(
    State(state): State<GatewayState>,
    headers: HeaderMap,
    Path(id): Path<Uuid>,
    Json(schedule): Json<Schedule>,
) -> Response {
    let (campaigns, _) = match write_guard(&state, &headers) {
        Ok(guarded) => guarded,
        Err(refusal) => return *refusal,
    };
    let at = schedule.at.unwrap_or_else(Utc::now);
    answered(campaigns.schedule(id, schedule.revision, at).await)
}

async fn cancelled(
    State(state): State<GatewayState>,
    headers: HeaderMap,
    Path(id): Path<Uuid>,
    Json(at): Json<AtRevision>,
) -> Response {
    let (campaigns, _) = match write_guard(&state, &headers) {
        Ok(guarded) => guarded,
        Err(refusal) => return *refusal,
    };
    answered(campaigns.cancel(id, at.revision).await)
}

fn read_guard<'a>(state: &'a GatewayState, headers: &HeaderMap) -> Option<&'a Campaigns> {
    operator(state, headers)?;
    state.campaigns.as_ref()
}

/// Four proofs before the ledger is touched: the cookie resolves, the address it names is an
/// operator's, the request came from this host, and its token is bound to that session.
fn write_guard<'a>(
    state: &'a GatewayState,
    headers: &HeaderMap,
) -> Result<(&'a Campaigns, String), Box<Response>> {
    let operator = operator_write(state, headers)?;
    let Some(campaigns) = state.campaigns.as_ref() else {
        return Err(Box::new(unavailable()));
    };
    Ok((campaigns, operator))
}

/// The same four proofs, for an operator surface with no campaign ledger behind it.
pub(crate) fn operator_write(
    state: &GatewayState,
    headers: &HeaderMap,
) -> Result<String, Box<Response>> {
    let Some(operator) = operator(state, headers) else {
        return Err(Box::new(unauthorized()));
    };
    if !same_origin(headers) {
        return Err(Box::new(
            (
                StatusCode::FORBIDDEN,
                Json(serde_json::json!({"detail": "the request must come from this page"})),
            )
                .into_response(),
        ));
    }
    let session = session(headers).unwrap_or_default();
    let presented = headers
        .get(CSRF_HEADER)
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default();
    if !constant_time_eq(
        presented,
        &csrf_token(&state.onboarding.token_secret, &session),
    ) {
        return Err(Box::new(
            (
                StatusCode::FORBIDDEN,
                Json(
                    serde_json::json!({"detail": "the request token does not match this session"}),
                ),
            )
                .into_response(),
        ));
    }
    Ok(operator)
}

pub(crate) fn operator(state: &GatewayState, headers: &HeaderMap) -> Option<String> {
    let session = session(headers)?;
    let email = token::verified_email(&state.onboarding.token_secret, &session, Utc::now())?;
    let domain = email.rsplit_once('@').map(|(_, domain)| domain)?;
    (domain == OPERATOR_EMAIL_DOMAIN).then_some(email)
}

pub(crate) fn session(headers: &HeaderMap) -> Option<String> {
    let cookies = headers.get(header::COOKIE)?.to_str().ok()?;
    cookies.split(';').find_map(|part| {
        let (name, value) = part.trim().split_once('=')?;
        (name.trim() == OPERATOR_COOKIE && !value.is_empty()).then(|| value.to_string())
    })
}

/// A browser sends `Origin` on every state-changing request, so a missing one is refused rather than
/// waved through: the page this serves always sends it.
fn same_origin(headers: &HeaderMap) -> bool {
    let Some(host) = headers.get(header::HOST).and_then(|v| v.to_str().ok()) else {
        return false;
    };
    headers
        .get(header::ORIGIN)
        .and_then(|value| value.to_str().ok())
        .and_then(|origin| origin.split_once("://").map(|(_, rest)| rest.to_string()))
        .is_some_and(|origin| origin == host)
}

/// Bound to the session it was minted for, and derived rather than stored: a token that outlives the
/// cookie it names proves nothing.
pub fn csrf_token(secret: &str, session: &str) -> String {
    let mut mac = <Hmac<Sha256>>::new_from_slice(secret.as_bytes())
        .expect("hmac-sha256 accepts a key of any length");
    mac.update(CSRF_CONTEXT.as_bytes());
    mac.update(session.as_bytes());
    format!("{:x}", mac.finalize().into_bytes())
}

fn answered<T: serde::Serialize>(result: Result<T, CampaignError>) -> Response {
    match result {
        Ok(value) => Json(value).into_response(),
        Err(error) => refused(error),
    }
}

fn refused(error: CampaignError) -> Response {
    let status = match &error {
        CampaignError::Absent(_) => StatusCode::NOT_FOUND,
        CampaignError::Refused(_) | CampaignError::Stale { .. } | CampaignError::State { .. } => {
            StatusCode::CONFLICT
        }
        _ => {
            tracing::error!(target: "ufo_control::hud", "hud.failed {error}");
            StatusCode::SERVICE_UNAVAILABLE
        }
    };
    (
        status,
        Json(serde_json::json!({"detail": error.to_string()})),
    )
        .into_response()
}

pub(crate) fn unauthorized() -> Response {
    (
        StatusCode::UNAUTHORIZED,
        Json(serde_json::json!({"detail": "an operator session is required"})),
    )
        .into_response()
}

fn unavailable() -> Response {
    (
        StatusCode::SERVICE_UNAVAILABLE,
        Json(serde_json::json!({"detail": "this deploy sends no campaigns"})),
    )
        .into_response()
}
