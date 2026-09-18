use chrono::{DateTime, Utc};
use deadpool_postgres::Pool;
use uuid::Uuid;

pub const SCHEMA: &str = "ufo_control";
pub const TABLE: &str = "ufo_control.onboard_claim";
pub const ACTIVE_INDEX: &str = "onboard_claim_active_session";

pub const DDL: &[&str] = &[
    "create table if not exists ufo_control.onboard_claim (\
       id uuid primary key,\
       email text not null,\
       email_domain text not null,\
       signup_subject text not null,\
       surface text not null,\
       surface_ref text not null,\
       expires_at timestamptz not null,\
       verified_at timestamptz,\
       resulting_workspace_id text,\
       created_workspace boolean not null default false,\
       invite_id uuid,\
       display_name text,\
       given_name text,\
       picture_url text,\
       created_at timestamptz not null default now())",
    "create unique index if not exists onboard_claim_active_session \
       on ufo_control.onboard_claim (surface, surface_ref) \
       where resulting_workspace_id is null",
];

const COLUMNS: &str = "id, email, split_part(email, '@', 2) as email_domain, signup_subject, \
                       surface, surface_ref, expires_at, verified_at, invite_id, display_name, \
                       given_name, picture_url";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OnboardClaim {
    pub claim_id: Uuid,
    pub email: String,
    pub email_domain: String,
    pub signup_subject: String,
    pub surface: String,
    pub surface_ref: String,
    pub expires_at: DateTime<Utc>,
    pub verified_at: Option<DateTime<Utc>>,
    pub invite_id: Option<Uuid>,
    pub display_name: Option<String>,
    pub given_name: Option<String>,
    pub picture_url: Option<String>,
}

#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    #[error("the ledger is unreachable: {0}")]
    Pool(#[from] deadpool_postgres::PoolError),
    #[error(transparent)]
    Query(#[from] tokio_postgres::Error),
}

#[derive(Debug, Clone)]
pub struct OnboardStore {
    pub pool: Pool,
}

impl OnboardStore {
    pub fn new(pool: Pool) -> Self {
        Self { pool }
    }

    pub async fn insert_claim(&self, claim: &OnboardClaim) -> Result<(), StoreError> {
        let connection = self.pool.get().await?;
        connection
            .execute(
                &format!(
                    "insert into {TABLE} \
                     (id, email, email_domain, signup_subject, surface, surface_ref, expires_at, \
                      verified_at, display_name, given_name, picture_url) \
                     values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)"
                ),
                &[
                    &claim.claim_id,
                    &claim.email,
                    &claim.signup_subject,
                    &claim.signup_subject,
                    &claim.surface,
                    &claim.surface_ref,
                    &claim.expires_at,
                    &claim.verified_at,
                    &claim.display_name,
                    &claim.given_name,
                    &claim.picture_url,
                ],
            )
            .await?;
        Ok(())
    }

    pub async fn live_claim(
        &self,
        surface: &str,
        surface_ref: &str,
    ) -> Result<Option<OnboardClaim>, StoreError> {
        let connection = self.pool.get().await?;
        let row = connection
            .query_opt(
                &format!(
                    "select {COLUMNS} from {TABLE} \
                     where surface = $1 and surface_ref = $2 and resulting_workspace_id is null"
                ),
                &[&surface, &surface_ref],
            )
            .await?;
        Ok(row.map(|row| OnboardClaim {
            claim_id: row.get("id"),
            email: row.get("email"),
            email_domain: row.get("email_domain"),
            signup_subject: row.get("signup_subject"),
            surface: row.get("surface"),
            surface_ref: row.get("surface_ref"),
            expires_at: row.get("expires_at"),
            verified_at: row.get("verified_at"),
            invite_id: row.get("invite_id"),
            display_name: row.get("display_name"),
            given_name: row.get("given_name"),
            picture_url: row.get("picture_url"),
        }))
    }

    pub async fn mark_verified(&self, claim_id: Uuid) -> Result<bool, StoreError> {
        let connection = self.pool.get().await?;
        let row = connection
            .query_opt(
                &format!(
                    "update {TABLE} set verified_at = now() \
                     where id = $1 and verified_at is null returning true"
                ),
                &[&claim_id],
            )
            .await?;
        Ok(row.is_some())
    }

    pub async fn complete(
        &self,
        claim_id: Uuid,
        resulting_workspace_id: &str,
        created_workspace: bool,
    ) -> Result<(), StoreError> {
        let connection = self.pool.get().await?;
        connection
            .execute(
                &format!(
                    "update {TABLE} set resulting_workspace_id = $2, created_workspace = $3 \
                     where id = $1"
                ),
                &[&claim_id, &resulting_workspace_id, &created_workspace],
            )
            .await?;
        Ok(())
    }

    pub async fn delete_claim(&self, claim_id: Uuid) -> Result<(), StoreError> {
        let connection = self.pool.get().await?;
        connection
            .execute(&format!("delete from {TABLE} where id = $1"), &[&claim_id])
            .await?;
        Ok(())
    }

    pub async fn delete_unverified_claim(&self, claim_id: Uuid) -> Result<bool, StoreError> {
        let connection = self.pool.get().await?;
        let row = connection
            .query_opt(
                &format!(
                    "delete from {TABLE} where id = $1 and verified_at is null returning true"
                ),
                &[&claim_id],
            )
            .await?;
        Ok(row.is_some())
    }
}
