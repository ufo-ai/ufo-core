//! Resolve a verified email address to its shared-fleet workspaces, over core's onboarding RPC.
//!
//! Control holds no privilege on any core table, so every read and write of one arrives here as an
//! HTTP call to `serve`. What a workspace *is* — the seat semantics, the balance grant, the default
//! agent's prompt — stays in core, where `create_member` and `credit` already live; this half only
//! asks, and renders what comes back.
//!
//! A returning member costs no call at all: `join` on an existing membership reads `is_admin` and
//! returns, so `serve` is reached only when a member is not yet seated.

use std::time::Duration;

use serde::{Deserialize, Serialize};
use uuid::Uuid;

pub const ONBOARD_CONTROL_TOKEN_ENV: &str = "UFO_ONBOARD_CONTROL_TOKEN";
pub const SERVE_INTERNAL_URL_ENV: &str = "UFO_CONTROL_SERVE_INTERNAL_URL";
pub const WORKSPACE_BASE_URL_ENV: &str = "UFO_WORKSPACE_BASE_URL";
pub const SEAT_TIMEOUT_SECONDS: u64 = 15;

/// The namespace `uuid5` derives a domain's workspace under — `NAMESPACE_DNS`, the same constant
/// core uses, so one domain resolves to one workspace on both ends.
const NAMESPACE_DNS: Uuid = Uuid::from_bytes([
    0x6b, 0xa7, 0xb8, 0x10, 0x9d, 0xad, 0x11, 0xd1, 0x80, 0xb4, 0x00, 0xc0, 0x4f, 0xd4, 0x30, 0xc8,
]);

/// The workspace one verified domain names. Derived here so the caller can send it and core can
/// write under it, rather than each end deriving its own and hoping they agree.
pub fn deterministic_workspace_id(domain: &str) -> Uuid {
    Uuid::new_v5(&NAMESPACE_DNS, domain.to_lowercase().as_bytes())
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SignupProfile {
    pub business: String,
    pub goals: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct WorkspaceChoice {
    pub workspace_id: String,
    pub label: String,
    pub member: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
struct WorkspaceChoices {
    choices: Vec<WorkspaceChoice>,
}

/// The binding hosted onboarding just made: the workspace this member belongs to, and whether they
/// administer it. The admin flag lets the concluding prompt offer billing management.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct EnsuredWorkspace {
    pub workspace_id: String,
    pub admin: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
struct Membership {
    admin: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
struct Fleet {
    craft: i64,
}

#[derive(Serialize)]
struct SeatRequest<'a> {
    workspace_id: Uuid,
    domain: &'a str,
    email: &'a str,
    profile: Option<&'a SignupProfile>,
}

/// What core refused, or what stopped the call reaching it. A refusal carries core's own sentence so
/// the flow renders one message rather than inventing a second vocabulary for the same condition.
#[derive(Debug, Clone, thiserror::Error)]
pub enum SeatError {
    #[error("{0}")]
    Refused(String),
    #[error("the workspace service is unreachable")]
    Unreachable,
    #[error("the workspace service answered {status}")]
    Unexpected { status: u16 },
}

#[derive(Deserialize, Default)]
struct Refusal {
    detail: Option<String>,
}

/// Resolve, create, and join shared-fleet workspaces for a verified address.
#[derive(Debug, Clone)]
pub struct SharedWorkspaces {
    pub workspace_url: String,
    pub serve_internal_url: String,
    pub control_token: String,
}

impl SharedWorkspaces {
    /// Every workspace this address may enter: its exact memberships plus the one its verified
    /// domain names. Membership grants only that workspace; a domain match grants its workspace.
    pub async fn choices(
        &self,
        domain: &str,
        email: &str,
    ) -> Result<Vec<WorkspaceChoice>, SeatError> {
        let listed: WorkspaceChoices = self
            .get(
                "choices",
                &[
                    ("email", &email.trim().to_lowercase()),
                    ("domain", &domain.to_lowercase()),
                ],
            )
            .await?;
        Ok(listed.choices)
    }

    /// One craft per workspace, for the landing page's live fleet.
    pub async fn fleet(&self) -> Result<i64, SeatError> {
        let fleet: Fleet = self.get("fleet", &[]).await?;
        Ok(fleet.craft)
    }

    /// Create the workspace identified by this verified domain and seat its first member.
    pub async fn create(
        &self,
        domain: &str,
        email: &str,
        profile: Option<&SignupProfile>,
    ) -> Result<EnsuredWorkspace, SeatError> {
        self.seat(deterministic_workspace_id(domain), domain, email, profile)
            .await
    }

    /// Seat this verified address in one workspace its candidates authorized. An address already
    /// seated there costs one membership read; anything else is a seat write in core.
    pub async fn join(
        &self,
        choice: &WorkspaceChoice,
        domain: &str,
        email: &str,
    ) -> Result<EnsuredWorkspace, SeatError> {
        let workspace_id = choice.workspace_id.parse::<Uuid>().map_err(|_| {
            SeatError::Refused(format!("{} is not a workspace", choice.workspace_id))
        })?;
        if !choice.member {
            return self.seat(workspace_id, domain, email, None).await;
        }
        let membership: Membership = self
            .get(
                "membership",
                &[
                    ("workspace_id", &choice.workspace_id),
                    ("email", &email.trim().to_lowercase()),
                ],
            )
            .await?;
        Ok(EnsuredWorkspace {
            workspace_id: choice.workspace_id.clone(),
            admin: membership.admin,
        })
    }

    async fn seat(
        &self,
        workspace_id: Uuid,
        domain: &str,
        email: &str,
        profile: Option<&SignupProfile>,
    ) -> Result<EnsuredWorkspace, SeatError> {
        let body = SeatRequest {
            workspace_id,
            domain: &domain.to_lowercase(),
            email: &email.trim().to_lowercase(),
            profile,
        };
        let response = self
            .client()?
            .post(self.url("seat"))
            .bearer_auth(&self.control_token)
            .json(&body)
            .send()
            .await
            .map_err(|_| SeatError::Unreachable)?;
        Self::decode(response).await
    }

    async fn get<T: for<'a> Deserialize<'a>>(
        &self,
        route: &str,
        query: &[(&str, &String)],
    ) -> Result<T, SeatError> {
        let response = self
            .client()?
            .get(self.url(route))
            .bearer_auth(&self.control_token)
            .query(query)
            .send()
            .await
            .map_err(|_| SeatError::Unreachable)?;
        Self::decode(response).await
    }

    /// Core's own sentence rides a refusal back, so a domain that maps to two workspaces or a member
    /// who was removed mid-sign-in reads the same either side of the wire.
    async fn decode<T: for<'a> Deserialize<'a>>(
        response: reqwest::Response,
    ) -> Result<T, SeatError> {
        let status = response.status();
        let payload = response.text().await.map_err(|_| SeatError::Unreachable)?;
        if status.is_success() {
            return serde_json::from_str(&payload).map_err(|_| SeatError::Unexpected {
                status: status.as_u16(),
            });
        }
        let refusal: Refusal = serde_json::from_str(&payload).unwrap_or_default();
        match refusal.detail {
            Some(detail) => Err(SeatError::Refused(detail)),
            None => Err(SeatError::Unexpected {
                status: status.as_u16(),
            }),
        }
    }

    fn url(&self, route: &str) -> String {
        format!(
            "{}/internal/onboard/{route}",
            self.serve_internal_url.trim_end_matches('/')
        )
    }

    fn client(&self) -> Result<reqwest::Client, SeatError> {
        reqwest::Client::builder()
            .timeout(Duration::from_secs(SEAT_TIMEOUT_SECONDS))
            .build()
            .map_err(|_| SeatError::Unreachable)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_domain_derives_the_same_workspace_core_derives() {
        // uuid5 over NAMESPACE_DNS. The literal is checked against Python's own output in
        // `tests/contract.rs`, so a wrong namespace constant is a failing test rather than a
        // second workspace for the same customer.
        assert_eq!(
            deterministic_workspace_id("acme.com"),
            deterministic_workspace_id("ACME.COM"),
            "the domain is lowercased before it is hashed"
        );
        assert_ne!(
            deterministic_workspace_id("acme.com"),
            deterministic_workspace_id("acme.io")
        );
    }
}
