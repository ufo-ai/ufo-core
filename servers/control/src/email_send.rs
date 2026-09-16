//! One message to one address, asked for by core. Every SES client, the suppression union, and the
//! feedback consumer live here, so an extension that has to reach a member by email asks this route
//! rather than opening a second sender in another language.
//!
//! The route is bearer-gated by the same token core's `/internal/onboard/*` routes require: one
//! shared secret for the one channel between the two services.
//!
//! Two suppressions, and they are not the same rule. A hard one — bounced, complained,
//! unsubscribed — bars every message this deploy sends, because reaching that address again costs
//! the sending domain its standing. A topic preference bars product news alone: a member who asked
//! to hear nothing more about the product is still told their balance ran out, because that is
//! what their workspace is doing with their money.

use std::sync::LazyLock;

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use deadpool_postgres::Pool;
use regex::Regex;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::campaign::{RECIPIENT_TABLE, UNREACHABLE};
use crate::email::{normalize_email, AwsError, EmailSender};
use crate::gateway::GatewayState;
use crate::message::{render, Words};
use crate::workos::constant_time_eq;

pub const TABLE: &str = "ufo_control.email_send";
pub const PREFERENCE_TABLE: &str = "ufo_control.email_preference";
pub const SEND_PATH: &str = "/internal/email/send";
pub const DELIVERY_PATH: &str = "/internal/email/send/{message_id}";
pub const PREFERENCE_PATH: &str = "/internal/email/preference";

/// Everything a workspace is doing with a member's money or access. Never silenced.
pub const TRANSACTIONAL: &str = "transactional";
/// Everything else this seam sends. A member silences it and keeps the rest.
pub const PRODUCT_NEWS: &str = "product_news";
/// What a founder campaign sends under. Nothing posts it here — a campaign has its own sender —
/// but a member silences it the same way, and one table holds both answers.
pub const FOUNDER_UPDATES: &str = "founder_updates";

/// What a send may name.
pub const TOPICS: &[&str] = &[TRANSACTIONAL, PRODUCT_NEWS];
/// What a member may silence. Transactional is absent and always will be: a workspace running out
/// of credit is not news, it is what is happening to their money.
pub const SILENCEABLE: &[&str] = &[PRODUCT_NEWS, FOUNDER_UPDATES];

/// What a member may set from chat. The founder list's opt-out is SES's, held on its contact list
/// and applied to the send, so lifting our row alone would report a resume that never happens.
pub const SETTABLE: &[&str] = &[PRODUCT_NEWS];

fn transactional() -> String {
    TRANSACTIONAL.to_string()
}

pub const MAX_KIND_CHARS: usize = 64;
pub const MAX_SUBJECT_CHARS: usize = 200;
pub const MAX_BODY_CHARS: usize = 20_000;
pub const MAX_ACTION_LABEL_CHARS: usize = 60;
pub const MAX_ACTION_URL_CHARS: usize = 2000;

/// Reserved by RFC 2606 and RFC 6761 so that they never resolve: mail to one is a hard bounce, and
/// the fleet's own eval workspaces seat a member at `swebench@<run>.eval.invalid`.
const UNROUTABLE_TLDS: &[&str] = &["example", "invalid", "local", "localhost", "test"];

static KIND_PATTERN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[a-z][a-z0-9_]*$").expect("the kind pattern compiles"));

pub const DDL: &[&str] = &[
    "create table if not exists ufo_control.email_send (\
       id uuid primary key,\
       kind text not null,\
       email text not null,\
       ses_message_id text not null unique,\
       delivery text,\
       sent_at timestamptz not null default now(),\
       updated_at timestamptz not null default now())",
    "create index if not exists email_send_address on ufo_control.email_send (email)",
    "create table if not exists ufo_control.email_preference (\
       email text not null,\
       topic text not null,\
       silenced_at timestamptz not null default now(),\
       primary key (email, topic))",
];

/// What core posts: words, never markup. The frame is this deploy's, so an extension cannot ship a
/// message that looks like it came from another product, and the one place that knows how a message
/// is drawn is the one place that draws it.
///
/// Every bound is checked here rather than at the column, so a refusal reaches the caller as a
/// sentence instead of a constraint violation.
#[derive(Debug, Clone, Deserialize)]
pub struct Asked {
    pub email: String,
    pub kind: String,
    /// A caller that names no topic is one from the image this gateway is replacing: the two roll
    /// as separate Deployments, so an old serve pod posts here for about a minute. It reads as
    /// transactional, which loses no notice — where refusing it would fail the attempt row for
    /// good, and a workspace that ran out of credit in that minute would never be told.
    #[serde(default = "transactional")]
    pub topic: String,
    pub subject: String,
    /// Paragraphs, separated by a blank line.
    pub body: String,
    pub action_label: Option<String>,
    pub action_url: Option<String>,
}

/// What a member asked for, carried here rather than kept by the extension that heard it, so the
/// one place that applies suppression applies this too.
#[derive(Debug, Clone, Deserialize)]
pub struct Preference {
    pub email: String,
    pub topic: String,
    pub silenced: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Silenced {
    pub topic: String,
    pub silenced: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Sent {
    pub message_id: String,
}

/// What the feedback consumer has recorded for a send, which is nothing until SES publishes its
/// first event for it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Delivered {
    pub delivery: Option<String>,
}

#[derive(Debug, thiserror::Error)]
pub enum SendError {
    #[error("{0}")]
    Refused(String),
    #[error("{0} has bounced, complained, or unsubscribed")]
    Suppressed(String),
    #[error("{address} asked to hear nothing more about {topic}")]
    Silenced { address: String, topic: String },
    #[error("there is no send {0}")]
    Absent(String),
    #[error(transparent)]
    Aws(#[from] AwsError),
    #[error(transparent)]
    Pool(#[from] deadpool_postgres::PoolError),
    #[error(transparent)]
    Query(#[from] tokio_postgres::Error),
}

#[derive(Clone)]
pub struct EmailSends {
    pub pool: Pool,
    pub sender: EmailSender,
    pub apex_host: String,
}

impl EmailSends {
    /// Send one message and record it. A hard suppression refuses before SES is called, whatever
    /// the kind: an address that bounced or complained is barred from every message this deploy
    /// sends, and reaching it again costs the sending domain its standing.
    pub async fn send(&self, asked: Asked) -> Result<Sent, SendError> {
        let address = self.checked(&asked)?;
        if self.suppressed(&address).await? {
            return Err(SendError::Suppressed(address));
        }
        if asked.topic != TRANSACTIONAL && self.silenced(&address, &asked.topic).await? {
            return Err(SendError::Silenced {
                address,
                topic: asked.topic.clone(),
            });
        }
        let message = render(
            &Words {
                subject: &asked.subject,
                body: &asked.body,
                action_label: asked.action_label.as_deref(),
                action_url: asked.action_url.as_deref(),
                unsubscribe: false,
            },
            &self.apex_host,
        );
        let message_id = self
            .sender
            .send(
                &address,
                &message.subject,
                &message.text,
                Some(&message.html),
            )
            .await?;
        self.record(&asked.kind, &address, &message_id).await?;
        tracing::info!(
            target: "ufo_control::email_send",
            "email_send.sent kind={} message={message_id}", asked.kind
        );
        Ok(Sent { message_id })
    }

    pub async fn delivery(&self, message_id: &str) -> Result<Delivered, SendError> {
        let connection = self.pool.get().await?;
        let row = connection
            .query_opt(
                &format!("select delivery from {TABLE} where ses_message_id = $1"),
                &[&message_id],
            )
            .await?
            .ok_or_else(|| SendError::Absent(message_id.to_string()))?;
        Ok(Delivered {
            delivery: row.get("delivery"),
        })
    }

    /// Record, or lift, what a member asked for. Idempotent both ways: a member who says it twice
    /// says it once.
    pub async fn prefer(&self, asked: Preference) -> Result<Silenced, SendError> {
        let (address, _) =
            normalize_email(&asked.email).map_err(|error| SendError::Refused(error.to_string()))?;
        if !SETTABLE.contains(&asked.topic.as_str()) {
            return Err(SendError::Refused(format!(
                "{:?} is not a topic a member can set (expected one of {SETTABLE:?})",
                asked.topic
            )));
        }
        let connection = self.pool.get().await?;
        let statement = match asked.silenced {
            true => format!(
                "insert into {PREFERENCE_TABLE} (email, topic) values ($1, $2) \
                 on conflict (email, topic) do nothing"
            ),
            false => format!("delete from {PREFERENCE_TABLE} where email = $1 and topic = $2"),
        };
        connection
            .execute(&statement, &[&address, &asked.topic])
            .await?;
        Ok(Silenced {
            topic: asked.topic,
            silenced: asked.silenced,
        })
    }

    async fn silenced(&self, address: &str, topic: &str) -> Result<bool, SendError> {
        let connection = self.pool.get().await?;
        let held = connection
            .query_opt(
                &format!("select 1 from {PREFERENCE_TABLE} where email = $1 and topic = $2"),
                &[&address, &topic],
            )
            .await?;
        Ok(held.is_some())
    }

    fn checked(&self, asked: &Asked) -> Result<String, SendError> {
        let (address, domain) =
            normalize_email(&asked.email).map_err(|error| SendError::Refused(error.to_string()))?;
        if let Some(tld) = domain.rsplit('.').next() {
            if UNROUTABLE_TLDS.contains(&tld) {
                return Err(SendError::Refused(format!(
                    "{address} is under the reserved top-level domain {tld:?}, which accepts no mail"
                )));
            }
        }
        if !TOPICS.contains(&asked.topic.as_str()) {
            return Err(SendError::Refused(format!(
                "{:?} is not a topic (expected one of {TOPICS:?})",
                asked.topic
            )));
        }
        if asked.kind.chars().count() > MAX_KIND_CHARS || !KIND_PATTERN.is_match(&asked.kind) {
            return Err(SendError::Refused(format!(
                "{:?} is not a send kind (lowercase letters, digits, and underscores)",
                asked.kind
            )));
        }
        if asked.subject.trim().is_empty() || asked.subject.chars().count() > MAX_SUBJECT_CHARS {
            return Err(SendError::Refused(format!(
                "a subject is required and holds at most {MAX_SUBJECT_CHARS} characters"
            )));
        }
        if asked.body.trim().is_empty() || asked.body.chars().count() > MAX_BODY_CHARS {
            return Err(SendError::Refused(format!(
                "a body is required and holds at most {MAX_BODY_CHARS} characters"
            )));
        }
        match (&asked.action_label, &asked.action_url) {
            (None, None) => {}
            (Some(label), Some(url))
                if !label.trim().is_empty()
                    && label.chars().count() <= MAX_ACTION_LABEL_CHARS
                    && url.starts_with("https://")
                    && url.chars().count() <= MAX_ACTION_URL_CHARS => {}
            _ => {
                return Err(SendError::Refused(format!(
                    "an act is a label of at most {MAX_ACTION_LABEL_CHARS} characters and an \
                     https URL of at most {MAX_ACTION_URL_CHARS}, or neither"
                )))
            }
        }
        Ok(address)
    }

    /// Whether this address is unreachable, not whether it has left a campaign — a member who
    /// unsubscribed from our news is still told what their workspace is doing with their money.
    async fn suppressed(&self, address: &str) -> Result<bool, SendError> {
        let states: Vec<String> = UNREACHABLE.iter().map(|state| state.to_string()).collect();
        let connection = self.pool.get().await?;
        let held = connection
            .query_opt(
                &format!(
                    "select 1 from {RECIPIENT_TABLE} \
                       where email = $1 and delivery = any($2::text[]) \
                     union all \
                     select 1 from {TABLE} where email = $1 and delivery = any($2::text[]) \
                     limit 1"
                ),
                &[&address, &states],
            )
            .await?;
        Ok(held.is_some())
    }

    async fn record(&self, kind: &str, address: &str, message_id: &str) -> Result<(), SendError> {
        let connection = self.pool.get().await?;
        connection
            .execute(
                &format!(
                    "insert into {TABLE} (id, kind, email, ses_message_id) values ($1, $2, $3, $4)"
                ),
                &[&Uuid::new_v4(), &kind, &address, &message_id],
            )
            .await?;
        Ok(())
    }
}

pub fn routes() -> Router<GatewayState> {
    Router::new()
        .route(SEND_PATH, post(sent))
        .route(DELIVERY_PATH, get(delivered))
        .route(PREFERENCE_PATH, post(preferred))
}

async fn preferred(State(state): State<GatewayState>, headers: HeaderMap, body: Bytes) -> Response {
    if !admitted(&state, &headers) {
        return unauthorized();
    }
    let asked: Preference = match serde_json::from_slice(&body) {
        Ok(asked) => asked,
        Err(error) => {
            return answered::<Silenced>(Err(SendError::Refused(format!(
                "the preference is unreadable: {error}"
            ))))
        }
    };
    answered(state.email_sends.prefer(asked).await)
}

/// A `Json<Asked>` argument would be rejected by axum before the handler runs, answering an
/// unauthorized caller 422 and telling them the shape. So the body is read after the token.
async fn sent(State(state): State<GatewayState>, headers: HeaderMap, body: Bytes) -> Response {
    if !admitted(&state, &headers) {
        return unauthorized();
    }
    let asked: Asked = match serde_json::from_slice(&body) {
        Ok(asked) => asked,
        Err(error) => {
            return answered::<Sent>(Err(SendError::Refused(format!(
                "the send body is unreadable: {error}"
            ))))
        }
    };
    answered(state.email_sends.send(asked).await)
}

async fn delivered(
    State(state): State<GatewayState>,
    headers: HeaderMap,
    Path(message_id): Path<String>,
) -> Response {
    if !admitted(&state, &headers) {
        return unauthorized();
    }
    answered(state.email_sends.delivery(&message_id).await)
}

fn admitted(state: &GatewayState, headers: &HeaderMap) -> bool {
    let presented = headers
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
        .unwrap_or_default();
    constant_time_eq(presented, &state.onboarding.workspaces.control_token)
}

fn unauthorized() -> Response {
    (
        StatusCode::UNAUTHORIZED,
        Json(serde_json::json!({"detail": "the control token is required"})),
    )
        .into_response()
}

fn answered<T: Serialize>(result: Result<T, SendError>) -> Response {
    let error = match result {
        Ok(body) => return Json(body).into_response(),
        Err(error) => error,
    };
    let status = match &error {
        SendError::Refused(_) => StatusCode::BAD_REQUEST,
        SendError::Suppressed(_) | SendError::Silenced { .. } => StatusCode::CONFLICT,
        SendError::Absent(_) => StatusCode::NOT_FOUND,
        SendError::Aws(_) => StatusCode::BAD_GATEWAY,
        SendError::Pool(_) | SendError::Query(_) => StatusCode::INTERNAL_SERVER_ERROR,
    };
    if !status.is_client_error() {
        tracing::error!(target: "ufo_control::email_send", "email_send.failed {error}");
    }
    (
        status,
        Json(serde_json::json!({"detail": error.to_string()})),
    )
        .into_response()
}
