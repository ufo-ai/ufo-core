use std::time::Duration;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

pub const ONBOARD_CONTROL_TOKEN_ENV: &str = "UFO_ONBOARD_CONTROL_TOKEN";
pub const SERVE_INTERNAL_URL_ENV: &str = "UFO_CONTROL_SERVE_INTERNAL_URL";
pub const WORKSPACE_BASE_URL_ENV: &str = "UFO_WORKSPACE_BASE_URL";
pub const SEAT_TIMEOUT_SECONDS: u64 = 15;

const NAMESPACE_DNS: Uuid = Uuid::from_bytes([
    0x6b, 0xa7, 0xb8, 0x10, 0x9d, 0xad, 0x11, 0xd1, 0x80, 0xb4, 0x00, 0xc0, 0x4f, 0xd4, 0x30, 0xc8,
]);

pub fn deterministic_workspace_id(subject: &str) -> Uuid {
    Uuid::new_v5(&NAMESPACE_DNS, subject.to_lowercase().as_bytes())
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

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct EnsuredWorkspace {
    pub workspace_id: String,
    pub admin: bool,
    pub founding: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Invitation {
    pub workspace_id: Uuid,
    pub email: String,
    pub invited_by: String,
    pub workspace_label: String,
    pub invited_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
struct Invitations {
    invitations: Vec<Invitation>,
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
    signup_subject: &'a str,
    profile: Option<&'a SignupProfile>,
}

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

#[derive(Debug, Clone)]
pub struct SharedWorkspaces {
    pub workspace_url: String,
    pub serve_internal_url: String,
    pub control_token: String,
}

impl SharedWorkspaces {
    pub async fn choices(
        &self,
        signup_subject: &str,
        email: &str,
    ) -> Result<Vec<WorkspaceChoice>, SeatError> {
        let member = email.trim().to_lowercase();
        let subject = signup_subject.trim().to_lowercase();
        let query = [
            ("email", &member),
            ("domain", &subject),
            ("signup_subject", &subject),
        ];
        let listed: WorkspaceChoices = self.get("choices", &query).await?;
        Ok(listed.choices)
    }

    pub async fn fleet(&self) -> Result<i64, SeatError> {
        let fleet: Fleet = self.get("fleet", &[]).await?;
        Ok(fleet.craft)
    }

    pub async fn invitations(
        &self,
        after: Option<&Invitation>,
    ) -> Result<Vec<Invitation>, SeatError> {
        let listed: Invitations = match after {
            Some(row) => {
                let stamp = row.invited_at.to_rfc3339();
                let workspace = row.workspace_id.to_string();
                self.get(
                    "invitations",
                    &[
                        ("after_invited_at", &stamp),
                        ("after_workspace_id", &workspace),
                        ("after_email", &row.email),
                    ],
                )
                .await?
            }
            None => self.get("invitations", &[]).await?,
        };
        Ok(listed.invitations)
    }

    pub async fn create(
        &self,
        signup_subject: &str,
        email: &str,
        profile: Option<&SignupProfile>,
    ) -> Result<EnsuredWorkspace, SeatError> {
        self.seat(
            deterministic_workspace_id(signup_subject),
            signup_subject,
            email,
            profile,
        )
        .await
    }

    pub async fn join(
        &self,
        choice: &WorkspaceChoice,
        signup_subject: &str,
        email: &str,
    ) -> Result<EnsuredWorkspace, SeatError> {
        let workspace_id = choice.workspace_id.parse::<Uuid>().map_err(|_| {
            SeatError::Refused(format!("{} is not a workspace", choice.workspace_id))
        })?;
        if !choice.member {
            return self.seat(workspace_id, signup_subject, email, None).await;
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
            founding: false,
        })
    }

    async fn seat(
        &self,
        workspace_id: Uuid,
        signup_subject: &str,
        email: &str,
        profile: Option<&SignupProfile>,
    ) -> Result<EnsuredWorkspace, SeatError> {
        let subject = signup_subject.trim().to_lowercase();
        let body = SeatRequest {
            workspace_id,
            domain: &subject,
            email: &email.trim().to_lowercase(),
            signup_subject: &subject,
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
    fn a_subject_derives_the_same_workspace_core_derives() {
        assert_eq!(
            deterministic_workspace_id("acme.com"),
            deterministic_workspace_id("ACME.COM"),
            "the subject is lowercased before it is hashed"
        );
        assert_ne!(
            deterministic_workspace_id("acme.com"),
            deterministic_workspace_id("acme.io")
        );
    }
}
