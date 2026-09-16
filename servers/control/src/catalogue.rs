//! Every message this deploy can send, on one page.
//!
//! Half of them are words in the tree: a diff reviews them and a deploy ships them, so this plane
//! cannot read them — it runs no Python. The extensions that hold them declare them at the
//! `messages` Manifest point, and core answers with the running image's own declaration when this
//! page asks. Nothing is stored here, so nothing can fall out of step with a deploy.
//!
//! The other half are the rows an operator writes, which this plane already holds. The page draws
//! both, and says which is which: one is changed by a deploy and the other by an edit.

use axum::extract::State;
use axum::http::{header, HeaderMap};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use deadpool_postgres::Pool;
use serde::Serialize;

use crate::gateway::GatewayState;
use crate::hud::{operator, unauthorized};
use crate::lifecycle::{SURFACE_PATH as SEQUENCE_SURFACE_PATH, TABLE as SEQUENCE_TABLE};
use crate::shared::{SeatError, SharedWorkspaces};

pub const SURFACE_PATH: &str = "/surface/email/catalogue";

const HUD_PAGE: &str = include_str!("catalogue_hud.html");

/// One line of the catalogue, whichever half it came from. `edited` is what an operator does about
/// it: a message in the tree changes with a deploy, a row changes on the sequence screen.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Listed {
    pub kind: String,
    pub topic: String,
    pub fires: String,
    pub subject: String,
    pub body: String,
    pub edited: &'static str,
}

pub const BY_DEPLOY: &str = "a deploy";
pub const BY_OPERATOR: &str = "an edit";

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Catalogue {
    pub messages: Vec<Listed>,
    pub sequences_path: &'static str,
}

#[derive(Debug, thiserror::Error)]
pub enum CatalogueError {
    #[error(transparent)]
    Fleet(#[from] SeatError),
    #[error(transparent)]
    Pool(#[from] deadpool_postgres::PoolError),
    #[error(transparent)]
    Query(#[from] tokio_postgres::Error),
}

#[derive(Clone)]
pub struct Messages {
    pub pool: Pool,
    pub core: SharedWorkspaces,
}

impl Messages {
    /// Every message this deploy can send: what the fleet declares, then every approved sequence
    /// step, each under the name its send is recorded by.
    pub async fn catalogue(&self) -> Result<Catalogue, CatalogueError> {
        let mut messages: Vec<Listed> = self
            .core
            .messages()
            .await?
            .into_iter()
            .map(|declared| Listed {
                kind: declared.kind,
                topic: declared.topic,
                fires: declared.fires,
                subject: declared.subject,
                body: declared.body,
                edited: BY_DEPLOY,
            })
            .collect();
        messages.sort_by(|left, right| left.kind.cmp(&right.kind));
        let connection = self.pool.get().await?;
        let rows = connection
            .query(
                &format!(
                    "select approved from {SEQUENCE_TABLE} \
                     where retired_at is null and approved is not null order by name"
                ),
                &[],
            )
            .await?;
        // Editing sets the name and event columns and drops the approval, while the runner reads
        // the approved copy — so a page reading those columns names what starts nothing.
        for row in &rows {
            let approved: serde_json::Value = row.get("approved");
            let steps = approved.get("steps").and_then(|steps| steps.as_array());
            let name = text(&approved, "name");
            let event = text(&approved, "event");
            for step in steps.into_iter().flatten() {
                messages.push(Listed {
                    kind: text(step, "kind"),
                    topic: crate::email_send::PRODUCT_NEWS.to_string(),
                    fires: fires(&name, &event, step),
                    subject: text(step, "subject"),
                    body: text(step, "body"),
                    edited: BY_OPERATOR,
                });
            }
        }
        Ok(Catalogue {
            messages,
            sequences_path: SEQUENCE_SURFACE_PATH,
        })
    }
}

fn text(step: &serde_json::Value, field: &str) -> String {
    step.get(field)
        .and_then(|held| held.as_str())
        .unwrap_or_default()
        .to_string()
}

/// What an operator needs to know about a step they did not write the trigger for: the sequence it
/// belongs to, the event that sequence measures from, and how long after it this one is due.
fn fires(name: &str, event: &str, step: &serde_json::Value) -> String {
    let seconds = step
        .get("after_seconds")
        .and_then(|held| held.as_i64())
        .unwrap_or_default();
    format!("{name}, {} after {event}", after(seconds))
}

fn after(seconds: i64) -> String {
    match seconds {
        0 => "at once".to_string(),
        _ if seconds % 86_400 == 0 => plural(seconds / 86_400, "day"),
        _ if seconds % 3_600 == 0 => plural(seconds / 3_600, "hour"),
        _ => plural(seconds.max(60) / 60, "minute"),
    }
}

fn plural(count: i64, unit: &str) -> String {
    match count {
        1 => format!("1 {unit}"),
        _ => format!("{count} {unit}s"),
    }
}

pub fn routes() -> Router<GatewayState> {
    Router::new()
        .route(SURFACE_PATH, get(page))
        .route("/surface/email/catalogue/list", get(listing))
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

async fn listing(State(state): State<GatewayState>, headers: HeaderMap) -> Response {
    let Some(operator) = operator(&state, &headers) else {
        return unauthorized();
    };
    match state.messages.catalogue().await {
        Ok(catalogue) => Json(serde_json::json!({
            "operator": operator,
            "messages": catalogue.messages,
            "sequences_path": catalogue.sequences_path,
        }))
        .into_response(),
        Err(error) => (
            axum::http::StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({"detail": error.to_string()})),
        )
            .into_response(),
    }
}
