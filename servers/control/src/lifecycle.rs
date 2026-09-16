//! Drip copy an operator edits, under the rule the campaign ledger already holds: editing bumps
//! the revision and drops the approval, and only an approved revision goes out.
//!
//! It lives here rather than in the extension that runs it because a sequence is one set of words
//! for the whole fleet, and every table an extension can reach is scoped to one workspace by row
//! security — a fleet-wide row there is invisible to the role the extension reads with. This
//! schema is outside that fence, which is the same reason the campaign ledger is here.
//!
//! Two doors, both already cut: the operator surface beside the campaign HUD, cookie-gated on the
//! operator domain with the same CSRF proof; and one bearer-gated internal route the runner reads
//! the approved set through.

use std::sync::LazyLock;

use axum::extract::{Path, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use deadpool_postgres::Pool;
use regex::Regex;
use serde::{Deserialize, Serialize};
use tokio_postgres::Row;
use uuid::Uuid;

use crate::gateway::GatewayState;
use crate::hud::{operator, operator_write, session, unauthorized};
use crate::workos::constant_time_eq;

pub const TABLE: &str = "ufo_control.lifecycle_sequence";
pub const SURFACE_PATH: &str = "/surface/email/sequences";
pub const APPROVED_PATH: &str = "/internal/lifecycle/sequences";

pub const MAX_STEPS: usize = 10;
pub const MAX_SUBJECT_CHARS: usize = 200;
pub const MAX_BODY_CHARS: usize = 20_000;
pub const MAX_NAME_CHARS: usize = 64;
pub const MAX_ACTION_LABEL_CHARS: usize = 60;
pub const MAX_ACTION_URL_CHARS: usize = 2000;
pub const MAX_DELAY_SECONDS: i64 = 365 * 24 * 60 * 60;

/// The one thing a row's words are not literal about. The runner replaces it with this
/// deploy's portal, so it is a link even though it is not yet a URL.
pub const URL_PLACEHOLDER: &str = "{url}";

static NAME_PATTERN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[a-z][a-z0-9_]*$").expect("the name pattern compiles"));

const HUD_PAGE: &str = include_str!("sequence_hud.html");

pub const DDL: &[&str] = &[
    "create table if not exists ufo_control.lifecycle_sequence (\
       id uuid primary key,\
       name text not null unique,\
       event text not null,\
       revision integer not null default 1,\
       steps jsonb not null,\
       approved jsonb,\
       approved_revision integer,\
       approved_by text,\
       created_by text not null,\
       retired_at timestamptz,\
       created_at timestamptz not null default now(),\
       updated_at timestamptz not null default now())",
];

const COLUMNS: &str = "id, name, event, revision, steps, approved_revision, approved_by, \
                       created_by, retired_at, created_at, updated_at";

/// One message in a drip sequence. `after_seconds` is measured from the event the sequence enrols
/// on, never from the step before it, so a late send never carries its lateness forward.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Step {
    pub after_seconds: i64,
    pub kind: String,
    pub subject: String,
    pub body: String,
    pub action_label: Option<String>,
    pub action_url: Option<String>,
}

/// What the editor posts. Every bound is checked here rather than at the column, so a refusal
/// reaches the operator as a sentence.
#[derive(Debug, Clone, Deserialize)]
pub struct Draft {
    pub name: String,
    pub event: String,
    pub steps: Vec<Step>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Sequence {
    pub id: Uuid,
    pub name: String,
    pub event: String,
    pub revision: i32,
    pub steps: Vec<Step>,
    pub approved_revision: Option<i32>,
    pub approved_by: Option<String>,
    pub created_by: String,
    pub retired_at: Option<DateTime<Utc>>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Approved {
    pub sequences: Vec<ApprovedSequence>,
}

/// What the runner is handed: the row's identity, the words, and the event they measure from. The
/// revision and who approved it are the editor's business, and the runner has no use for them.
///
/// The id is here because it is what an enrolment keys on. A name is not: an operator may free one
/// by renaming its sequence, and a second sequence that then takes it would answer for the first
/// one's members half-way through their steps.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ApprovedSequence {
    pub id: Uuid,
    pub name: String,
    pub event: String,
    pub steps: Vec<Step>,
}

#[derive(Debug, thiserror::Error)]
pub enum SequenceError {
    #[error("there is no sequence {0}")]
    Absent(Uuid),
    #[error("{0}")]
    Refused(String),
    #[error("sequence {id} is at revision {held}, not {stated}")]
    Stale { id: Uuid, held: i32, stated: i32 },
    #[error(transparent)]
    Pool(#[from] deadpool_postgres::PoolError),
    #[error(transparent)]
    Query(#[from] tokio_postgres::Error),
}

fn sequence(row: &Row) -> Sequence {
    Sequence {
        id: row.get("id"),
        name: row.get("name"),
        event: row.get("event"),
        revision: row.get("revision"),
        steps: serde_json::from_value(row.get("steps")).unwrap_or_default(),
        approved_revision: row.get("approved_revision"),
        approved_by: row.get("approved_by"),
        created_by: row.get("created_by"),
        retired_at: row.get("retired_at"),
        created_at: row.get("created_at"),
        updated_at: row.get("updated_at"),
    }
}

#[derive(Clone)]
pub struct Sequences {
    pub pool: Pool,
}

impl Sequences {
    pub async fn list(&self) -> Result<Vec<Sequence>, SequenceError> {
        let connection = self.pool.get().await?;
        let rows = connection
            .query(&format!("select {COLUMNS} from {TABLE} order by name"), &[])
            .await?;
        Ok(rows.iter().map(sequence).collect())
    }

    /// Every sequence the runner may send: approved at the revision it holds now, and not retired.
    /// A revision edited after approval is absent until it is approved again, which is what makes
    /// the gate hold — the runner never sees words nobody signed off.
    /// The copy an operator approved, which is not the copy they are editing. Approving stores the
    /// revision's own words, so an edit that drops the approval leaves the approved set unchanged
    /// and a member part-way through a sequence keeps receiving it. Retiring is what stops a
    /// sequence, and it is the only thing that does.
    pub async fn approved(&self) -> Result<Approved, SequenceError> {
        let connection = self.pool.get().await?;
        let rows = connection
            .query(
                &format!(
                    "select approved from {TABLE} \
                     where retired_at is null and approved is not null order by name"
                ),
                &[],
            )
            .await?;
        Ok(Approved {
            sequences: rows
                .iter()
                .filter_map(|row| serde_json::from_value(row.get("approved")).ok())
                .collect(),
        })
    }

    pub async fn create(&self, operator: &str, draft: Draft) -> Result<Sequence, SequenceError> {
        let checked = self.checked(draft)?;
        let connection = self.pool.get().await?;
        let row = connection
            .query_one(
                &format!(
                    "insert into {TABLE} (id, name, event, steps, created_by) \
                     values ($1, $2, $3, $4, $5) returning {COLUMNS}"
                ),
                &[
                    &Uuid::new_v4(),
                    &checked.name,
                    &checked.event,
                    &serde_json::to_value(&checked.steps).expect("steps serialize"),
                    &operator,
                ],
            )
            .await
            .map_err(|error| match error.code() {
                Some(code) if code.code() == "23505" => {
                    SequenceError::Refused(format!("a sequence named {:?} exists", checked.name))
                }
                _ => SequenceError::Query(error),
            })?;
        Ok(sequence(&row))
    }

    /// Editing bumps the revision and drops the approval in one statement, so no window exists in
    /// which edited words read as approved.
    /// Editing keeps the approved copy running, so a member part-way through a sequence is not cut
    /// off by words nobody has signed off yet. A *rename* is the exception: a sequence called
    /// something else is a different sequence to the members reading it, so the words go back for
    /// approval under the name they will arrive under.
    pub async fn revise(
        &self,
        id: Uuid,
        stated: i32,
        draft: Draft,
    ) -> Result<Sequence, SequenceError> {
        let checked = self.checked(draft)?;
        self.at_revision(id, stated).await?;
        let connection = self.pool.get().await?;
        let row = connection
            .query_opt(
                &format!(
                    "update {TABLE} set name = $3, event = $4, steps = $5, \
                       revision = revision + 1, approved_revision = null, approved_by = null, \
                       approved = case when name = $3 then approved else null end, \
                       updated_at = now() \
                     where id = $1 and revision = $2 returning {COLUMNS}"
                ),
                &[
                    &id,
                    &stated,
                    &checked.name,
                    &checked.event,
                    &serde_json::to_value(&checked.steps).expect("steps serialize"),
                ],
            )
            .await?
            .ok_or(SequenceError::Absent(id))?;
        Ok(sequence(&row))
    }

    pub async fn approve(
        &self,
        id: Uuid,
        stated: i32,
        operator: &str,
    ) -> Result<Sequence, SequenceError> {
        self.at_revision(id, stated).await?;
        let connection = self.pool.get().await?;
        let row = connection
            .query_opt(
                &format!(
                    "update {TABLE} set approved_revision = revision, approved_by = $3, \
                       approved = jsonb_build_object('id', id, 'name', name, 'event', event, \
                                                     'steps', steps), \
                       updated_at = now() \
                     where id = $1 and revision = $2 returning {COLUMNS}"
                ),
                &[&id, &stated, &operator],
            )
            .await?
            .ok_or(SequenceError::Absent(id))?;
        Ok(sequence(&row))
    }

    /// Retiring stops the sends and keeps the words. A live enrolment ends the next time the
    /// runner reaches it, because a sequence it cannot resolve has no step left.
    pub async fn retire(&self, id: Uuid) -> Result<Sequence, SequenceError> {
        let connection = self.pool.get().await?;
        let row = connection
            .query_opt(
                &format!(
                    "update {TABLE} set retired_at = now(), updated_at = now() \
                     where id = $1 returning {COLUMNS}"
                ),
                &[&id],
            )
            .await?
            .ok_or(SequenceError::Absent(id))?;
        Ok(sequence(&row))
    }

    async fn at_revision(&self, id: Uuid, stated: i32) -> Result<(), SequenceError> {
        let connection = self.pool.get().await?;
        let held: i32 = connection
            .query_opt(
                &format!("select revision from {TABLE} where id = $1"),
                &[&id],
            )
            .await?
            .ok_or(SequenceError::Absent(id))?
            .get("revision");
        if held != stated {
            return Err(SequenceError::Stale { id, held, stated });
        }
        Ok(())
    }

    fn checked(&self, draft: Draft) -> Result<Draft, SequenceError> {
        let name = draft.name.trim().to_string();
        let event = draft.event.trim().to_string();
        for (field, value) in [("name", &name), ("event", &event)] {
            if value.chars().count() > MAX_NAME_CHARS || !NAME_PATTERN.is_match(value) {
                return Err(SequenceError::Refused(format!(
                    "the {field} holds lowercase letters, digits, and underscores, \
                     at most {MAX_NAME_CHARS} characters"
                )));
            }
        }
        if draft.steps.is_empty() || draft.steps.len() > MAX_STEPS {
            return Err(SequenceError::Refused(format!(
                "a sequence holds between one and {MAX_STEPS} steps"
            )));
        }
        let mut previous = -1_i64;
        for step in &draft.steps {
            if step.after_seconds <= previous || step.after_seconds > MAX_DELAY_SECONDS {
                return Err(SequenceError::Refused(
                    "each step is due later than the one before it, and within a year of the event"
                        .to_string(),
                ));
            }
            previous = step.after_seconds;
            if step.kind.chars().count() > MAX_NAME_CHARS || !NAME_PATTERN.is_match(&step.kind) {
                return Err(SequenceError::Refused(format!(
                    "{:?} is not a step kind (lowercase letters, digits, and underscores)",
                    step.kind
                )));
            }
            if step.subject.trim().is_empty() || step.subject.chars().count() > MAX_SUBJECT_CHARS {
                return Err(SequenceError::Refused(format!(
                    "every step has a subject of at most {MAX_SUBJECT_CHARS} characters"
                )));
            }
            if step.body.trim().is_empty() || step.body.chars().count() > MAX_BODY_CHARS {
                return Err(SequenceError::Refused(format!(
                    "every step has a body of at most {MAX_BODY_CHARS} characters"
                )));
            }
            match (&step.action_label, &step.action_url) {
                (None, None) => {}
                (Some(label), Some(url))
                    if !label.trim().is_empty()
                        && label.chars().count() <= MAX_ACTION_LABEL_CHARS
                        && (url == URL_PLACEHOLDER || url.starts_with("https://"))
                        && url.chars().count() <= MAX_ACTION_URL_CHARS => {}
                _ => {
                    return Err(SequenceError::Refused(format!(
                        "a step's act is a label of at most {MAX_ACTION_LABEL_CHARS} characters \
                         and either {URL_PLACEHOLDER} or an https URL of at most \
                         {MAX_ACTION_URL_CHARS}, or neither"
                    )))
                }
            }
        }
        Ok(Draft {
            name,
            event,
            steps: draft.steps,
        })
    }
}

#[derive(Deserialize)]
struct At {
    revision: i32,
}

#[derive(Deserialize)]
struct Composed {
    revision: i32,
    #[serde(flatten)]
    draft: Draft,
}

pub fn routes() -> Router<GatewayState> {
    Router::new()
        .route(SURFACE_PATH, get(page))
        .route("/surface/email/sequences/list", get(listed).post(created))
        .route("/surface/email/sequences/{id}/content", post(revised))
        .route("/surface/email/sequences/{id}/approve", post(approved_by))
        .route("/surface/email/sequences/{id}/retire", post(retired))
        .route(APPROVED_PATH, get(live))
}

async fn page(State(state): State<GatewayState>, headers: HeaderMap) -> Response {
    match operator(&state, &headers) {
        Some(_) => (
            [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
            HUD_PAGE,
        )
            .into_response(),
        None => axum::response::Redirect::to(crate::hud::OPERATOR_LOGIN_PATH).into_response(),
    }
}

async fn listed(State(state): State<GatewayState>, headers: HeaderMap) -> Response {
    let Some(operator) = operator(&state, &headers) else {
        return unauthorized();
    };
    match state.sequences.list().await {
        Ok(sequences) => Json(serde_json::json!({
            "operator": operator,
            "sequences": sequences,
            "csrf": crate::hud::csrf_token(
                &state.onboarding.token_secret,
                &session(&headers).unwrap_or_default(),
            ),
        }))
        .into_response(),
        Err(error) => refusal(error),
    }
}

async fn created(
    State(state): State<GatewayState>,
    headers: HeaderMap,
    Json(draft): Json<Draft>,
) -> Response {
    let operator = match operator_write(&state, &headers) {
        Ok(operator) => operator,
        Err(refused) => return *refused,
    };
    answered(state.sequences.create(&operator, draft).await)
}

async fn revised(
    State(state): State<GatewayState>,
    headers: HeaderMap,
    Path(id): Path<Uuid>,
    Json(composed): Json<Composed>,
) -> Response {
    if let Err(refused) = operator_write(&state, &headers) {
        return *refused;
    }
    answered(
        state
            .sequences
            .revise(id, composed.revision, composed.draft)
            .await,
    )
}

async fn approved_by(
    State(state): State<GatewayState>,
    headers: HeaderMap,
    Path(id): Path<Uuid>,
    Json(at): Json<At>,
) -> Response {
    let operator = match operator_write(&state, &headers) {
        Ok(operator) => operator,
        Err(refused) => return *refused,
    };
    answered(state.sequences.approve(id, at.revision, &operator).await)
}

async fn retired(
    State(state): State<GatewayState>,
    headers: HeaderMap,
    Path(id): Path<Uuid>,
) -> Response {
    if let Err(refused) = operator_write(&state, &headers) {
        return *refused;
    }
    answered(state.sequences.retire(id).await)
}

async fn live(State(state): State<GatewayState>, headers: HeaderMap) -> Response {
    let presented = headers
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
        .unwrap_or_default();
    if !constant_time_eq(presented, &state.onboarding.workspaces.control_token) {
        return unauthorized();
    }
    answered(state.sequences.approved().await)
}

fn answered<T: Serialize>(result: Result<T, SequenceError>) -> Response {
    match result {
        Ok(body) => Json(body).into_response(),
        Err(error) => refusal(error),
    }
}

fn refusal(error: SequenceError) -> Response {
    let status = match &error {
        SequenceError::Absent(_) => StatusCode::NOT_FOUND,
        SequenceError::Refused(_) => StatusCode::BAD_REQUEST,
        SequenceError::Stale { .. } => StatusCode::CONFLICT,
        SequenceError::Pool(_) | SequenceError::Query(_) => StatusCode::INTERNAL_SERVER_ERROR,
    };
    if !status.is_client_error() {
        tracing::error!(target: "ufo_control::lifecycle", "lifecycle.failed {error}");
    }
    (
        status,
        Json(serde_json::json!({"detail": error.to_string()})),
    )
        .into_response()
}
