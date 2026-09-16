use chrono::{DateTime, Utc};
use deadpool_postgres::Pool;
use uuid::Uuid;

use crate::email::{verdict, EmailSender, SendVerdict};
use crate::gateway::INVITATION_LOGIN_PATH;
use crate::shared::{Invitation, SeatError, SharedWorkspaces};
use crate::web::LOGO_PNG_PATH;

pub const TABLE: &str = "ufo_control.invite_delivery";
pub const DUE_INDEX: &str = "invite_delivery_due";
pub const SENT_INDEX: &str = "invite_delivery_sent";

pub const STATE_PENDING: &str = "pending";
pub const STATE_CLAIMED: &str = "claimed";
pub const STATE_DELIVERED: &str = "delivered";
pub const STATE_FAILED: &str = "failed";

pub const DDL: &[&str] = &[
    "create table if not exists ufo_control.invite_delivery (\
       workspace_id uuid not null,\
       email text not null,\
       state text not null check (state in ('pending', 'claimed', 'delivered', 'failed')),\
       invited_by text not null,\
       workspace_label text not null,\
       invited_at timestamptz not null,\
       sent_at timestamptz,\
       worker_id text,\
       claim_expires_at timestamptz,\
       next_attempt_at timestamptz,\
       attempts integer not null default 0,\
       last_error text,\
       created_at timestamptz not null default now(),\
       updated_at timestamptz not null default now(),\
       delivered_at timestamptz,\
       primary key (workspace_id, email))",
    "create index if not exists invite_delivery_due \
       on ufo_control.invite_delivery (state, next_attempt_at)",
    "create index if not exists invite_delivery_sent \
       on ufo_control.invite_delivery (workspace_id, sent_at)",
];

pub const POLL_INTERVAL_SECONDS: u64 = 15;
pub const LEASE_SECONDS: i64 = 120;
pub const LEASE_RENEW_SECONDS: u64 = 30;
pub const MAX_ATTEMPTS: i32 = 8;
pub const RETRY_BACKOFF_SECONDS: i64 = 30;
pub const RETRY_BACKOFF_MAX_SECONDS: i64 = 3600;

pub const INVITATIONS_PER_WORKSPACE_PER_DAY: i64 = 100;

pub const ERROR_CHARS: usize = 500;
pub const MAX_EMAIL_CHARS: usize = 254;
pub const MAX_LABEL_CHARS: usize = 253;

pub const INVITATION_SUBJECT: &str = "You were added to a ufo workspace";
pub const INVITATION_BODY: &str = "{invited_by} added you to the {workspace_label} workspace \
                                   on ufo.\n\nSign in as {email} at https://{apex_host}{sign_in_path}\n";

pub const INVITATION_HTML: &str = r##"<!doctype html>
<html lang="en">
<body style="margin:0;padding:0;background:#FAF9F7;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:#FAF9F7;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="440" cellpadding="0" cellspacing="0" border="0"
       style="width:100%;max-width:440px;">
<tr><td align="center" style="padding-bottom:20px;">
<img src="https://{apex_host}{logo_path}" alt="ufo" width="72" height="18"
     style="display:block;border:0;width:72px;height:18px;"></td></tr>
<tr><td style="padding:20px;background:#FAF9F7;border:1px solid #EBEAE9;border-radius:4px;">
<p style="margin:0 0 8px;font:15px/1.5 system-ui,sans-serif;color:#191A1A;">{invited_by} added you to the {workspace_label} workspace on ufo.</p>
<p style="margin:0 0 20px;font:14px/1.5 system-ui,sans-serif;color:#919090;">Sign in as {email}.</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td align="center" bgcolor="#191A1A" style="border-radius:4px;">
<a href="https://{apex_host}{sign_in_path}"
   style="display:block;padding:10px 18px;font:500 15px/1.5 system-ui,sans-serif;color:#FAF9F7;text-decoration:none;">Sign in</a>
</td></tr></table>
</td></tr></table>
</td></tr></table>
</body>
</html>
"##;

const AMBIGUOUS_SEND: &str = "a previous attempt reached SES and no answer was recorded; re-arm \
                              this row only once it is known the message never landed";

#[derive(Debug, thiserror::Error)]
pub enum LedgerError {
    #[error(transparent)]
    Pool(#[from] deadpool_postgres::PoolError),
    #[error(transparent)]
    Query(#[from] tokio_postgres::Error),
}

#[derive(Debug, thiserror::Error)]
pub enum SweepError {
    #[error(transparent)]
    Ledger(#[from] LedgerError),
    #[error("the invitation read was refused: {0}")]
    Core(#[from] SeatError),
}

pub async fn rearm_failed_delivery(
    pool: &Pool,
    workspace_id: Uuid,
    email: &str,
) -> Result<Option<DateTime<Utc>>, LedgerError> {
    let connection = pool.get().await?;
    let row = connection
        .query_opt(
            &format!(
                "with previous as (\
                   select workspace_id, email, updated_at from {TABLE} \
                   where workspace_id = $1 and email = $2 and state = '{STATE_FAILED}' \
                   for update) \
                 update {TABLE} d set state = '{STATE_PENDING}', worker_id = null, \
                   claim_expires_at = null, next_attempt_at = null, sent_at = null, attempts = 0, \
                   last_error = null, updated_at = now() \
                 from previous p \
                 where d.workspace_id = p.workspace_id and d.email = p.email \
                 returning p.updated_at"
            ),
            &[&workspace_id, &email],
        )
        .await?;
    Ok(row.map(|row| row.get("updated_at")))
}

#[derive(Debug, Clone)]
pub struct Delivery {
    pub workspace_id: Uuid,
    pub email: String,
    pub invited_by: String,
    pub workspace_label: String,
    pub sent_at: Option<DateTime<Utc>>,
    pub attempts: i32,
}

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct LeaseLost(String);

fn lease_lost(delivery: &Delivery) -> LeaseLost {
    LeaseLost(format!(
        "{} in {} is no longer leased by this worker",
        delivery.email, delivery.workspace_id
    ))
}

#[derive(Debug, Clone)]
pub struct InviteDeliveries {
    pub pool: Pool,
    pub core: SharedWorkspaces,
    pub sender: EmailSender,
    pub apex_host: String,
    pub worker_id: String,
    pub poll_interval: std::time::Duration,
}

impl InviteDeliveries {
    pub async fn run(self) {
        let mut consecutive_failures = 0_u32;
        loop {
            let claimed = match self.poll().await {
                Ok(claimed) => {
                    consecutive_failures = 0;
                    claimed
                }
                Err(error) => {
                    consecutive_failures += 1;
                    tracing::error!(
                        target: "ufo_control::invite_delivery",
                        "invite_delivery.sweep.failed consecutive={consecutive_failures} {error}"
                    );
                    false
                }
            };
            if !claimed {
                tokio::time::sleep(self.poll_interval).await;
            }
        }
    }

    pub async fn poll(&self) -> Result<bool, SweepError> {
        self.materialize().await?;
        let Some(delivery) = self.claim().await? else {
            return Ok(false);
        };
        let renewal = {
            let deliveries = self.clone();
            let leased = delivery.clone();
            tokio::spawn(async move { deliveries.renew_lease(leased).await })
        };
        let advanced = self.advance(&delivery).await;
        renewal.abort();
        if let Err(lost) = advanced {
            tracing::warn!(
                target: "ufo_control::invite_delivery",
                "invite_delivery.lease.lost {lost}"
            );
        }
        Ok(true)
    }

    async fn materialize(&self) -> Result<(), SweepError> {
        let mut after: Option<Invitation> = None;
        loop {
            let page = self.core.invitations(after.as_ref()).await?;
            let Some(last) = page.last().cloned() else {
                return Ok(());
            };
            self.insert(&page).await?;
            after = Some(last);
        }
    }

    async fn insert(&self, invitations: &[Invitation]) -> Result<(), LedgerError> {
        let workspaces: Vec<Uuid> = invitations.iter().map(|row| row.workspace_id).collect();
        let emails: Vec<String> = invitations.iter().map(|row| row.email.clone()).collect();
        let inviters: Vec<String> = invitations
            .iter()
            .map(|row| row.invited_by.clone())
            .collect();
        let labels: Vec<String> = invitations
            .iter()
            .map(|row| row.workspace_label.clone())
            .collect();
        let stamps: Vec<DateTime<Utc>> = invitations.iter().map(|row| row.invited_at).collect();
        let connection = self.pool.get().await?;
        connection
            .execute(
                &format!(
                    "insert into {TABLE} \
                       (workspace_id, email, state, invited_by, workspace_label, invited_at) \
                     select workspace_id, email, '{STATE_PENDING}', invited_by, workspace_label, \
                            invited_at \
                     from unnest($1::uuid[], $2::text[], $3::text[], $4::text[], \
                                 $5::timestamptz[]) \
                       as page(workspace_id, email, invited_by, workspace_label, invited_at) \
                     on conflict (workspace_id, email) do nothing"
                ),
                &[&workspaces, &emails, &inviters, &labels, &stamps],
            )
            .await?;
        Ok(())
    }

    async fn claim(&self) -> Result<Option<Delivery>, LedgerError> {
        let connection = self.pool.get().await?;
        let row = connection
            .query_opt(
                &format!(
                    "with candidate as (\
                       select workspace_id, email from {TABLE} \
                       where (state = '{STATE_PENDING}' \
                              and (next_attempt_at is null or next_attempt_at <= now())) \
                          or (state = '{STATE_CLAIMED}' and claim_expires_at <= now()) \
                       order by invited_at, workspace_id, email \
                       for update skip locked limit 1) \
                     update {TABLE} d \
                     set state = '{STATE_CLAIMED}', worker_id = $1, \
                         claim_expires_at = now() + $2::text::interval, \
                         attempts = d.attempts + 1, updated_at = now() \
                     from candidate c \
                     where d.workspace_id = c.workspace_id and d.email = c.email \
                     returning d.workspace_id, d.email, d.invited_by, d.workspace_label, \
                               d.sent_at, d.attempts"
                ),
                &[&self.worker_id, &pg_interval(LEASE_SECONDS)],
            )
            .await?;
        Ok(row.map(|row| Delivery {
            workspace_id: row.get("workspace_id"),
            email: row.get("email"),
            invited_by: row.get("invited_by"),
            workspace_label: row.get("workspace_label"),
            sent_at: row.get("sent_at"),
            attempts: row.get("attempts"),
        }))
    }

    async fn renew_lease(&self, delivery: Delivery) {
        loop {
            tokio::time::sleep(std::time::Duration::from_secs(LEASE_RENEW_SECONDS)).await;
            let renewed = async {
                let connection = self.pool.get().await?;
                connection
                    .execute(
                        &format!(
                            "update {TABLE} set claim_expires_at = now() + $4::text::interval, \
                             updated_at = now() \
                             where workspace_id = $1 and email = $2 and worker_id = $3"
                        ),
                        &[
                            &delivery.workspace_id,
                            &delivery.email,
                            &self.worker_id,
                            &pg_interval(LEASE_SECONDS),
                        ],
                    )
                    .await?;
                Ok::<(), LedgerError>(())
            }
            .await;
            if let Err(error) = renewed {
                tracing::error!(
                    target: "ufo_control::invite_delivery",
                    "invite_delivery.lease.renew_failed workspace={} {error}",
                    delivery.workspace_id
                );
            }
        }
    }

    async fn advance(&self, delivery: &Delivery) -> Result<(), LeaseLost> {
        match self.deliver(delivery).await {
            Ok(()) => Ok(()),
            Err(Carried::Lease(lost)) => Err(lost),
            Err(Carried::Send(SendVerdict::Transient {
                message,
                retry_after,
            })) => self.reschedule(delivery, &message, retry_after).await,
            Err(Carried::Send(other)) => self.fail(delivery, &other.to_string()).await,
            Err(Carried::Ledger(error)) => {
                tracing::error!(
                    target: "ufo_control::invite_delivery",
                    "invite_delivery.unexpected workspace={} {error}", delivery.workspace_id
                );
                self.fail(delivery, &error.to_string()).await
            }
        }
    }

    async fn deliver(&self, delivery: &Delivery) -> Result<(), Carried> {
        if delivery.sent_at.is_some() {
            return Err(Carried::Send(SendVerdict::Unanswered(
                AMBIGUOUS_SEND.to_string(),
            )));
        }
        let (text, html) = self.message(delivery)?;
        self.mark_sending(delivery).await?;
        self.sender
            .send(
                &delivery.email,
                INVITATION_SUBJECT,
                &text,
                Some(&html),
                None,
            )
            .await
            .map_err(|error| Carried::Send(verdict(error)))?;
        self.write(
            delivery,
            &format!(
                "state = '{STATE_DELIVERED}', delivered_at = now(), worker_id = null, \
                 claim_expires_at = null, next_attempt_at = null, last_error = null"
            ),
            &[],
        )
        .await?;
        tracing::info!(
            target: "ufo_control::invite_delivery",
            "invite_delivery.delivered workspace={} invited_by={}",
            delivery.workspace_id, delivery.invited_by
        );
        Ok(())
    }

    fn message(&self, delivery: &Delivery) -> Result<(String, String), Carried> {
        for (what, value, bound) in [
            ("recipient email", &delivery.email, MAX_EMAIL_CHARS),
            ("inviting email", &delivery.invited_by, MAX_EMAIL_CHARS),
            (
                "workspace label",
                &delivery.workspace_label,
                MAX_LABEL_CHARS,
            ),
        ] {
            if value.chars().count() > bound {
                return Err(Carried::Send(SendVerdict::Terminal(format!(
                    "the {what} exceeds {bound} characters"
                ))));
            }
        }
        let text = INVITATION_BODY
            .replace("{invited_by}", &delivery.invited_by)
            .replace("{workspace_label}", &delivery.workspace_label)
            .replace("{email}", &delivery.email)
            .replace("{apex_host}", &self.apex_host)
            .replace("{sign_in_path}", INVITATION_LOGIN_PATH);
        let html = INVITATION_HTML
            .replace("{invited_by}", &escaped(&delivery.invited_by))
            .replace("{workspace_label}", &escaped(&delivery.workspace_label))
            .replace("{email}", &escaped(&delivery.email))
            .replace("{apex_host}", &escaped(&self.apex_host))
            .replace("{sign_in_path}", &escaped(INVITATION_LOGIN_PATH))
            .replace("{logo_path}", LOGO_PNG_PATH);
        Ok((text, html))
    }

    async fn mark_sending(&self, delivery: &Delivery) -> Result<(), Carried> {
        let mut connection = self.pool.get().await.map_err(LedgerError::from)?;
        let transaction = connection.transaction().await.map_err(LedgerError::from)?;
        transaction
            .query_one(
                "select pg_advisory_xact_lock(hashtext($1))",
                &[&format!("{TABLE} {}", delivery.workspace_id)],
            )
            .await
            .map_err(LedgerError::from)?;
        let sent_today: i64 = transaction
            .query_one(
                &format!(
                    "select count(*) from {TABLE} \
                     where workspace_id = $1 and sent_at > now() - interval '1 day'"
                ),
                &[&delivery.workspace_id],
            )
            .await
            .map_err(LedgerError::from)?
            .get(0);
        if sent_today >= INVITATIONS_PER_WORKSPACE_PER_DAY {
            return Err(Carried::Send(SendVerdict::Terminal(format!(
                "workspace {} has sent {sent_today} invitations in the last day, at its cap of \
                 {INVITATIONS_PER_WORKSPACE_PER_DAY}",
                delivery.workspace_id
            ))));
        }
        let owned = transaction
            .query_opt(
                &format!(
                    "update {TABLE} set sent_at = now(), updated_at = now() \
                     where workspace_id = $1 and email = $2 and worker_id = $3 returning true"
                ),
                &[&delivery.workspace_id, &delivery.email, &self.worker_id],
            )
            .await
            .map_err(LedgerError::from)?;
        if owned.is_none() {
            return Err(Carried::Lease(lease_lost(delivery)));
        }
        transaction.commit().await.map_err(LedgerError::from)?;
        Ok(())
    }

    async fn reschedule(
        &self,
        delivery: &Delivery,
        message: &str,
        retry_after: Option<f64>,
    ) -> Result<(), LeaseLost> {
        if delivery.attempts >= MAX_ATTEMPTS {
            return self.fail(delivery, message).await;
        }
        let backoff = RETRY_BACKOFF_SECONDS
            .saturating_mul(1_i64 << (delivery.attempts.max(1) - 1).min(20))
            .min(RETRY_BACKOFF_MAX_SECONDS);
        let delay = retry_after.map(|seconds| seconds as i64).unwrap_or(backoff);
        let written = self
            .write(
                delivery,
                &format!(
                    "state = '{STATE_PENDING}', worker_id = null, claim_expires_at = null, \
                     sent_at = null, next_attempt_at = now() + $4::text::interval, last_error = $5"
                ),
                &[&pg_interval(delay), &clipped(message)],
            )
            .await;
        tracing::warn!(
            target: "ufo_control::invite_delivery",
            "invite_delivery.retry workspace={} attempts={} in={delay}s",
            delivery.workspace_id, delivery.attempts
        );
        written.map_err(carried_lease)
    }

    async fn fail(&self, delivery: &Delivery, message: &str) -> Result<(), LeaseLost> {
        let stated = clipped(message);
        let written = self
            .write(
                delivery,
                &format!(
                    "state = '{STATE_FAILED}', worker_id = null, claim_expires_at = null, \
                     next_attempt_at = null, last_error = $4"
                ),
                &[&stated],
            )
            .await;
        tracing::error!(
            target: "ufo_control::invite_delivery",
            "invite_delivery.failed workspace={} error={stated}", delivery.workspace_id
        );
        written.map_err(carried_lease)
    }

    async fn write(
        &self,
        delivery: &Delivery,
        assignment: &str,
        values: &[&(dyn tokio_postgres::types::ToSql + Sync)],
    ) -> Result<(), Carried> {
        let connection = self.pool.get().await.map_err(LedgerError::from)?;
        let mut parameters: Vec<&(dyn tokio_postgres::types::ToSql + Sync)> =
            vec![&delivery.workspace_id, &delivery.email, &self.worker_id];
        parameters.extend_from_slice(values);
        let owned = connection
            .query_opt(
                &format!(
                    "update {TABLE} set {assignment}, updated_at = now() \
                     where workspace_id = $1 and email = $2 and worker_id = $3 returning true"
                ),
                &parameters,
            )
            .await
            .map_err(LedgerError::from)?;
        if owned.is_none() {
            return Err(Carried::Lease(lease_lost(delivery)));
        }
        Ok(())
    }
}

fn carried_lease(carried: Carried) -> LeaseLost {
    match carried {
        Carried::Lease(lost) => lost,
        other => LeaseLost(format!("writeback failed: {other}")),
    }
}

fn escaped(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

fn clipped(message: &str) -> String {
    message.chars().take(ERROR_CHARS).collect()
}

fn pg_interval(seconds: i64) -> String {
    format!("{seconds} seconds")
}

#[derive(Debug, thiserror::Error)]
enum Carried {
    #[error(transparent)]
    Send(#[from] SendVerdict),
    #[error(transparent)]
    Lease(#[from] LeaseLost),
    #[error(transparent)]
    Ledger(#[from] LedgerError),
}
