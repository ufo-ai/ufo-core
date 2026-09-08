use chrono::{DateTime, Duration, Utc};
use deadpool_postgres::Pool;
use uuid::Uuid;

use crate::email::{EmailError, SignupEmailPolicy};
use crate::store::{StoreError, TABLE as CLAIM_TABLE};

pub const TABLE: &str = "ufo_control.invite_code";
pub const LIVE_OBJECT_INDEX: &str = "invite_code_live_object";
pub const LIVE_SUBJECT_INDEX: &str = "invite_code_live_subject";
pub const INVITE_TTL_DAYS: i64 = 14;
pub const MAX_PROFILE_CHARS: usize = 500;

pub const DDL: &[&str] = &[
    "create table if not exists ufo_control.invite_code (\
       id uuid primary key,\
       object_number integer check (object_number > 0),\
       email text not null,\
       email_domain text not null,\
       signup_subject text not null,\
       business text,\
       goals text,\
       expires_at timestamptz not null,\
       consumed_at timestamptz,\
       created_at timestamptz not null default now())",
    "create unique index if not exists invite_code_live_object \
       on ufo_control.invite_code (object_number) where consumed_at is null",
];

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

#[derive(Debug, thiserror::Error)]
pub enum InviteError {
    #[error("{0} is already identified")]
    AlreadyIdentified(String),
    #[error("{subject} already holds a live invite, expires {expires} UTC")]
    LiveGrant { subject: String, expires: String },
    #[error("each profile field is 1 to {MAX_PROFILE_CHARS} characters")]
    ProfileLength,
    #[error(transparent)]
    Email(#[from] EmailError),
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

    pub async fn available(&self, email: &str) -> Result<bool, InviteError> {
        let signup = SignupEmailPolicy::default().validate(email)?;
        let connection = self.pool.get().await?;
        let row = connection
            .query_opt(
                &format!(
                    "select 1 from {TABLE} \
                     where signup_subject = $1 \
                       and consumed_at is null and expires_at > now()"
                ),
                &[&signup.subject],
            )
            .await?;
        Ok(row.is_some())
    }

    pub async fn profile(&self, email: &str) -> Result<Option<SignupProfile>, InviteError> {
        let signup = SignupEmailPolicy::default().validate(email)?;
        let connection = self.pool.get().await?;
        let row = connection
            .query_opt(
                &format!(
                    "select business, goals from {TABLE} \
                     where signup_subject = $1 and business is not null \
                     order by created_at desc, id desc limit 1"
                ),
                &[&signup.subject],
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
        let signup = SignupEmailPolicy::default().validate(email)?;
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
        refuse_standing(
            &transaction,
            "signup_subject",
            &signup.subject,
            &signup.subject,
            now,
        )
        .await?;
        transaction
            .execute(
                &format!(
                    "delete from {TABLE} \
                     where (object_number = $1 \
                            or signup_subject = $2) \
                       and consumed_at is null and expires_at <= $3"
                ),
                &[&object_number, &signup.subject, &now],
            )
            .await?;
        let inserted = transaction
            .execute(
                &format!(
                    "insert into {TABLE} \
                     (id, object_number, email, email_domain, signup_subject, expires_at, business, \
                      goals) values ($1, $2, $3, $4, $5, $6, $7, $8)"
                ),
                &[
                    &Uuid::new_v4(),
                    &object_number,
                    &signup.address,
                    &signup.subject,
                    &signup.subject,
                    &expires_at,
                    &profile.map(|profile| profile.business.as_str()),
                    &profile.map(|profile| profile.goals.as_str()),
                ],
            )
            .await;
        if let Err(raced) = inserted {
            if raced.code() == Some(&tokio_postgres::error::SqlState::UNIQUE_VIOLATION) {
                let subject = match object_number {
                    Some(number) => format!("object #{number} or {}", signup.subject),
                    None => signup.subject,
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
            email: signup.address,
            expires_at,
        })
    }

    pub async fn redeem(&self, email: &str, claim_id: Uuid) -> Result<Redemption, InviteError> {
        let signup = SignupEmailPolicy::default().validate(email)?;
        let mut connection = self.pool.get().await?;
        let transaction = connection.transaction().await?;
        let Some(row) = transaction
            .query_opt(
                &format!(
                    "select id, object_number, expires_at, consumed_at from {TABLE} \
                     where signup_subject = $1 \
                     order by (consumed_at is not null) desc, expires_at desc limit 1 \
                     for update"
                ),
                &[&signup.subject],
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
