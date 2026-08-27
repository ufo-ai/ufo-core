//! Postgres custody of the hosted onboarding claim ledger. `schema` shapes `DDL`.
//!
//! Two columns hold one fact. `signup_subject` is the honest name for the identity a claim carries;
//! `email_domain` predates personal-mail signup, when that identity could only ever be a domain,
//! and it is the column the release being replaced selects and reads as the identity. Neither
//! image can be taught the other's column name mid-rollout, so both are written with the subject
//! and the domain is derived from the verified address instead. `email_domain` is droppable once
//! no pod of that release is left to read it.

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
       created_at timestamptz not null default now())",
    "create unique index if not exists onboard_claim_active_session \
       on ufo_control.onboard_claim (surface, surface_ref) \
       where resulting_workspace_id is null",
];

// The domain is read back off the verified address rather than out of `email_domain`: that column
// carries the signup subject, which for a personal-mail address is the address itself. See
// `insert_claim`.
const COLUMNS: &str = "id, email, split_part(email, '@', 2) as email_domain, signup_subject, \
                       surface, surface_ref, expires_at, verified_at, invite_id";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OnboardClaim {
    pub claim_id: Uuid,
    pub email: String,
    /// The verified address's own domain. It is what a company signup is identified by and is never
    /// the identity of a personal-mail signup — `signup_subject` is that.
    pub email_domain: String,
    pub signup_subject: String,
    pub surface: String,
    pub surface_ref: String,
    pub expires_at: DateTime<Utc>,
    pub verified_at: Option<DateTime<Utc>>,
    pub invite_id: Option<Uuid>,
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

    /// Write the claim. The `email_domain` column is written with the signup subject rather than
    /// with the domain: it is the column the release being replaced reads as the whole signup
    /// identity — that image declares no `signup_subject` — and the identity of a personal-mail
    /// address is that address.
    ///
    /// Left as the shared provider domain, a gateway pod of that release picking up this session
    /// mid-rollout carries `gmail.com` as the identity: it lists every workspace whose first member
    /// is at `gmail.com` as a candidate, and founds `uuid5(gmail.com)` on a personal member, which
    /// hands every `@gmail.com` stranger a domain match on that workspace for good. Written as the
    /// subject, every request that pod can build names this member's own subject and nobody else's.
    pub async fn insert_claim(&self, claim: &OnboardClaim) -> Result<(), StoreError> {
        let connection = self.pool.get().await?;
        connection
            .execute(
                &format!(
                    "insert into {TABLE} \
                     (id, email, email_domain, signup_subject, surface, surface_ref, expires_at, \
                      verified_at) values ($1, $2, $3, $4, $5, $6, $7, $8)"
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
                ],
            )
            .await?;
        Ok(())
    }

    /// The claim this surface session is still working through — one at a time, held by the partial
    /// unique index: a completed claim releases the pair for the next attempt.
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
        }))
    }

    /// Stamp the first verification and report whether this call is the one that did it. A second
    /// caller gets false, so a replayed code cannot re-open a claim already spent.
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

    /// Whether this claim opened the workspace or joined one already there. A join is not a new
    /// customer — a contractor seated at their own email domain, or operator staff, resolves to a
    /// workspace their domain does not name — so the two cannot share one mark.
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
