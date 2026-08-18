//! One-time new-workspace invites, each a grant to one email domain.
//!
//! A verified email whose domain already has a workspace joins ungranted; only the flow that
//! creates a workspace consults the ledger. `ufo-control invite <object-number> <email>` grants a
//! waitlist object's domain and emails it the invitation. Redeeming consumes the grant —
//! `consumed_at` claimed under a row lock while still null, so two concurrent flows can never both
//! open a workspace on one grant — and stamps the claim's `invite_id` in the same transaction, so a
//! crash can never leave a consumed grant detached from its claim. The consumption lands before the
//! workspace write; repeated redemption by that claim is accepted.
//!
//! An object number names the waitlist object a grant approved, and only that. A grant approved
//! from the intake form answers a form response, which is no waitlist object, so it carries none —
//! inventing one would ask an operator to pick a number nothing holds and to check it is free
//! against a ledger no agent can read. Postgres treats nulls as distinct, so any number of
//! unnumbered grants coexist while the waitlist's own numbers stay one to one.
//!
//! A grant names a domain rather than travelling as a bearer secret. The member proves the granted
//! domain by verifying their own email, so the invitation carries nothing to retype, a forwarded
//! invitation reaches only the company it was issued to, and the colleague who actually runs the
//! installer is identified without a second grant.

use chrono::{DateTime, Duration, Utc};
use deadpool_postgres::Pool;
use uuid::Uuid;

use crate::email::{normalize_email, WorkEmailError, WorkEmailPolicy};
use crate::store::{StoreError, TABLE as CLAIM_TABLE};

pub const TABLE: &str = "ufo_control.invite_code";
pub const LIVE_OBJECT_INDEX: &str = "invite_code_live_object";
pub const LIVE_DOMAIN_INDEX: &str = "invite_code_live_domain";
pub const INVITE_TTL_DAYS: i64 = 14;
pub const MAX_PROFILE_CHARS: usize = 500;

pub const DDL: &[&str] = &[
    "create table if not exists ufo_control.invite_code (\
       id uuid primary key,\
       object_number integer check (object_number > 0),\
       email text not null,\
       email_domain text not null,\
       business text,\
       goals text,\
       expires_at timestamptz not null,\
       consumed_at timestamptz,\
       created_at timestamptz not null default now())",
    "create unique index if not exists invite_code_live_object \
       on ufo_control.invite_code (object_number) where consumed_at is null",
    "create unique index if not exists invite_code_live_domain \
       on ufo_control.invite_code (email_domain) where consumed_at is null",
];

/// What the intake form collected about one customer: what their company does, and what they want
/// an agent to do. It reaches their workspace as the main agent's opening context, so the agent
/// knows who it works for on its first turn rather than asking for what they already told us.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct SignupProfile {
    pub business: String,
    pub goals: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MintedInvite {
    pub object_number: Option<i32>,
    pub email: String,
    pub expires_at: DateTime<Utc>,
}

/// What a redemption found. `Accepted` carries the grant it spent so the claim can be stamped with
/// it, and a repeat by the same claim reads back as accepted rather than consumed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Redemption {
    Unknown,
    Expired { expires_at: DateTime<Utc> },
    Consumed,
    Accepted(InviteAccepted),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InviteAccepted {
    pub invite_id: Uuid,
    pub object_number: Option<i32>,
    pub consumed_at: DateTime<Utc>,
}

/// Granting refused: the object or the domain is already identified, or already holds a live grant.
#[derive(Debug, thiserror::Error)]
pub enum InviteError {
    #[error("{0} is already identified")]
    AlreadyIdentified(String),
    #[error("{subject} already holds a live invite, expires {expires} UTC")]
    LiveGrant { subject: String, expires: String },
    #[error("each profile field is 1 to {MAX_PROFILE_CHARS} characters")]
    ProfileLength,
    #[error(transparent)]
    Email(#[from] WorkEmailError),
    #[error(transparent)]
    Store(#[from] StoreError),
    #[error(transparent)]
    Pool(#[from] deadpool_postgres::PoolError),
    #[error(transparent)]
    Query(#[from] tokio_postgres::Error),
}

#[derive(Debug, Clone)]
pub struct InviteCodes {
    pub pool: Pool,
    pub ttl: Duration,
}

impl InviteCodes {
    pub fn new(pool: Pool) -> Self {
        Self {
            pool,
            ttl: Duration::days(INVITE_TTL_DAYS),
        }
    }

    /// Whether this domain holds a live grant that can create its workspace.
    pub async fn available(&self, email_domain: &str) -> Result<bool, InviteError> {
        let connection = self.pool.get().await?;
        let row = connection
            .query_opt(
                &format!(
                    "select 1 from {TABLE} \
                     where email_domain = $1 and consumed_at is null and expires_at > now()"
                ),
                &[&email_domain],
            )
            .await?;
        Ok(row.is_some())
    }

    /// What the newest grant for this domain recorded, or None when the form collected nothing. The
    /// newest grant wins: a re-granted domain describes the customer as they are now.
    pub async fn profile(&self, email_domain: &str) -> Result<Option<SignupProfile>, InviteError> {
        let connection = self.pool.get().await?;
        let row = connection
            .query_opt(
                &format!(
                    "select business, goals from {TABLE} \
                     where email_domain = $1 and business is not null \
                     order by created_at desc, id desc limit 1"
                ),
                &[&email_domain],
            )
            .await?;
        Ok(row.map(|row| SignupProfile {
            business: row.get("business"),
            goals: row.get("goals"),
        }))
    }

    pub async fn mint(
        &self,
        object_number: Option<i32>,
        email: &str,
        profile: Option<&SignupProfile>,
    ) -> Result<MintedInvite, InviteError> {
        let (address, domain) = normalize_email(email)?;
        WorkEmailPolicy::default().validate(&address)?;
        if let Some(profile) = profile {
            let sized = |field: &str| (1..=MAX_PROFILE_CHARS).contains(&field.chars().count());
            if !sized(&profile.business) || !sized(&profile.goals) {
                return Err(InviteError::ProfileLength);
            }
        }
        let now = Utc::now();
        let expires_at = now + self.ttl;
        let mut connection = self.pool.get().await?;
        let transaction = connection.transaction().await?;
        if let Some(number) = object_number {
            refuse_standing(
                &transaction,
                "object_number",
                &number,
                &format!("object #{number}"),
                now,
            )
            .await?;
        }
        refuse_standing(&transaction, "email_domain", &domain, &domain, now).await?;
        transaction
            .execute(
                &format!(
                    "delete from {TABLE} \
                     where (object_number = $1 or email_domain = $2) \
                       and consumed_at is null and expires_at <= $3"
                ),
                &[&object_number, &domain, &now],
            )
            .await?;
        let inserted = transaction
            .execute(
                &format!(
                    "insert into {TABLE} \
                     (id, object_number, email, email_domain, expires_at, business, goals) \
                     values ($1, $2, $3, $4, $5, $6, $7)"
                ),
                &[
                    &Uuid::new_v4(),
                    &object_number,
                    &address,
                    &domain,
                    &expires_at,
                    &profile.map(|profile| profile.business.as_str()),
                    &profile.map(|profile| profile.goals.as_str()),
                ],
            )
            .await;
        if let Err(raced) = inserted {
            // The partial unique indexes are the arbiter, so a grant that raced another to the same
            // object or domain is refused here rather than doubling one.
            if raced.code() == Some(&tokio_postgres::error::SqlState::UNIQUE_VIOLATION) {
                let subject = match object_number {
                    Some(number) => format!("object #{number} or {domain}"),
                    None => domain,
                };
                return Err(InviteError::LiveGrant {
                    subject,
                    expires: expires_at.format("%Y-%m-%d %H:%M").to_string(),
                });
            }
            return Err(raced.into());
        }
        transaction.commit().await?;
        Ok(MintedInvite {
            object_number,
            email: address,
            expires_at,
        })
    }

    /// Spend this domain's grant for one claim, under a row lock so two flows cannot both open a
    /// workspace on it. The claim's `invite_id` is stamped in the same transaction, so a consumed
    /// grant is never detached from the claim that spent it.
    pub async fn redeem(
        &self,
        email_domain: &str,
        claim_id: Uuid,
    ) -> Result<Redemption, InviteError> {
        let mut connection = self.pool.get().await?;
        let transaction = connection.transaction().await?;
        let Some(row) = transaction
            .query_opt(
                &format!(
                    "select id, object_number, expires_at, consumed_at from {TABLE} \
                     where email_domain = $1 \
                     order by (consumed_at is not null) desc, expires_at desc limit 1 \
                     for update"
                ),
                &[&email_domain],
            )
            .await?
        else {
            return Ok(Redemption::Unknown);
        };
        let invite_id: Uuid = row.get("id");
        let object_number: Option<i32> = row.get("object_number");
        let expires_at: DateTime<Utc> = row.get("expires_at");
        let consumed_at: Option<DateTime<Utc>> = row.get("consumed_at");

        if let Some(consumed_at) = consumed_at {
            let attached: bool = transaction
                .query_one(
                    &format!(
                        "select exists(select 1 from {CLAIM_TABLE} \
                         where id = $1 and invite_id = $2)"
                    ),
                    &[&claim_id, &invite_id],
                )
                .await?
                .get(0);
            return Ok(if attached {
                Redemption::Accepted(InviteAccepted {
                    invite_id,
                    object_number,
                    consumed_at,
                })
            } else {
                Redemption::Consumed
            });
        }
        if Utc::now() >= expires_at {
            return Ok(Redemption::Expired { expires_at });
        }
        let consumed_at: DateTime<Utc> = transaction
            .query_one(
                &format!(
                    "update {TABLE} set consumed_at = now() where id = $1 returning consumed_at"
                ),
                &[&invite_id],
            )
            .await?
            .get("consumed_at");
        transaction
            .execute(
                &format!("update {CLAIM_TABLE} set invite_id = $2 where id = $1"),
                &[&claim_id, &invite_id],
            )
            .await?;
        transaction.commit().await?;
        Ok(Redemption::Accepted(InviteAccepted {
            invite_id,
            object_number,
            consumed_at,
        }))
    }
}

/// Refuse a grant whose object or domain is already spoken for: a consumed grant identifies the
/// customer for good, and a live one has to expire or be spent before another can be issued.
async fn refuse_standing(
    transaction: &deadpool_postgres::Transaction<'_>,
    column: &str,
    value: &(dyn tokio_postgres::types::ToSql + Sync),
    subject: &str,
    now: DateTime<Utc>,
) -> Result<(), InviteError> {
    let standing = transaction
        .query_opt(
            &format!(
                "select consumed_at, expires_at from {TABLE} \
                 where {column} = $1 and (consumed_at is not null or expires_at > $2) \
                 order by (consumed_at is not null) desc limit 1"
            ),
            &[value, &now],
        )
        .await?;
    let Some(standing) = standing else {
        return Ok(());
    };
    if standing
        .get::<_, Option<DateTime<Utc>>>("consumed_at")
        .is_some()
    {
        return Err(InviteError::AlreadyIdentified(subject.to_string()));
    }
    let expires: DateTime<Utc> = standing.get("expires_at");
    Err(InviteError::LiveGrant {
        subject: subject.to_string(),
        expires: expires.format("%Y-%m-%d %H:%M").to_string(),
    })
}
