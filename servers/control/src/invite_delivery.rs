//! The teammate invitation email: one message to each person an admin adds to a workspace.
//!
//! An admin adds a teammate from the portal, core stamps `member.invited_at`, and that stamp is the
//! durable event source. There is no enqueue transaction to lose: this workflow reads the fact back
//! over the onboarding RPC on either gateway replica and materializes what it finds. The admin's
//! own action never waits on mail, and a member who signs in by themselves was never invited, so
//! they carry no stamp and earn no message.
//!
//! `(workspace_id, email)` is the key, because that pair is the person being invited. One row per
//! pair means the fact can be re-enumerated forever, and a sweep does exactly that: it walks every
//! invitation core holds, every cycle, and re-reading a row already materialized costs an
//! `on conflict do nothing`. No mark is carried between sweeps. A stamp is taken when its
//! transaction starts and the seat write then waits for the workspace row lock, so members commit
//! out of stamp order, and a sweep landing between two commits would carry a mark past the one that
//! committed late. Nothing else writes this ledger, so that person would get no message and leave
//! no failed row.
//!
//! SES answers no read: nothing can be asked whether a message was accepted after the fact. So the
//! ledger records the attempt *before* the call — `sent_at` under the same lease as every other
//! write — and a claim that finds it already set treats the row as ambiguous and lands it `failed`
//! for an operator. Only a verdict that proves SES never accepted the message (a status it
//! answered, or a connection that never opened) clears the marker and returns the row to `pending`.
//! A resend is a person's decision, taken with `ufo-control invite-delivery-retry`.

use chrono::{DateTime, Utc};
use deadpool_postgres::Pool;
use uuid::Uuid;

use crate::email::{EmailSender, SendError};
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

/// One workspace's day of invitations. An admin staffing a company reaches a few dozen; a hundred
/// in a day is somebody's script, and the rows over the line wait for a person to look at them.
pub const INVITATIONS_PER_WORKSPACE_PER_DAY: i64 = 100;

pub const ERROR_CHARS: usize = 500;
pub const MAX_EMAIL_CHARS: usize = 254;
pub const MAX_LABEL_CHARS: usize = 253;

pub const INVITATION_SUBJECT: &str = "You were added to a ufo workspace";
pub const INVITATION_BODY: &str = "{invited_by} added you to the {workspace_label} workspace \
                                   on ufo.\n\nSign in as {email} at https://{apex_host}{sign_in_path}\n";

/// The same message with the sign-in page's own look: its surface, its card, its button.
///
/// Every rule is an inline `style` attribute and every layout is a table, because a mail client is
/// not a browser — Gmail drops a `<style>` block, Outlook renders no flexbox, and neither resolves
/// a custom property or a `light-dark()` pair, so the page's tokens are written here as the literal
/// light values they hold. The mark is set as text rather than drawn: an SVG is the one image
/// format clients reliably refuse, and a message whose only mark is blocked opens with a hole.
/// The button is an anchor over a coloured cell, and the card carries its width twice — the
/// attribute Outlook reads and the rule everything else does — which is the shape that survives
/// Outlook's engine without a second, Outlook-only card beside this one.
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

/// SES error names that name a throttle rather than a refusal. Everything else in the 4xx band —
/// a suspended account, an unverified sender, a rejected message — is a condition no retry fixes.
const TRANSIENT_SES_ERRORS: &[&str] = &["TooManyRequestsException", "ThrottlingException"];

#[derive(Debug, thiserror::Error)]
pub enum LedgerError {
    #[error(transparent)]
    Pool(#[from] deadpool_postgres::PoolError),
    #[error(transparent)]
    Query(#[from] tokio_postgres::Error),
}

/// A sweep ended before any row was carried. Our own ledger and core's answer fail differently: one
/// is an internal fault to root-cause, the other is a service this replica does not own.
#[derive(Debug, thiserror::Error)]
pub enum SweepError {
    #[error(transparent)]
    Ledger(#[from] LedgerError),
    #[error("the invitation read was refused: {0}")]
    Core(#[from] SeatError),
}

/// The operator recovery surface: re-arm one failed row once its cause is corrected, returning when
/// it last changed so the verb can report how long it sat. None when nothing was re-armed — a
/// delivered row is untouchable, and this never sends anything itself.
///
/// `sent_at` clears with the rest. An ambiguous row is exactly the one an operator re-arms, and
/// they re-arm it having decided the message never landed; leaving the marker would fail the row
/// again on its next claim for the reason they just settled.
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

/// What SES answered, and what that licenses.
///
/// `Transient` is proven external uncertainty *and* proof the message was not accepted — a status
/// SES or STS answered, or a connection that never opened — so the row returns to `pending` behind
/// a bounded schedule with its attempt marker cleared. `Terminal` is authentication, policy,
/// verification, or a recipient no retry fixes. `Unanswered` is a request that left with no answer
/// read: the message may be in flight, so the row lands `failed` with its marker standing rather
/// than risking a second copy.
#[derive(Debug, thiserror::Error)]
pub enum SendVerdict {
    #[error("{message}")]
    Transient {
        message: String,
        retry_after: Option<f64>,
    },
    #[error("{0}")]
    Terminal(String),
    #[error("{0}")]
    Unanswered(String),
}

/// The verdict one `SesEmailSender::send` failure carries.
pub fn verdict(error: SendError) -> SendVerdict {
    match error {
        SendError::Ses {
            status: 429,
            body,
            retry_after,
        } => SendVerdict::Transient {
            message: format!("SES SendEmail returned 429: {body}"),
            retry_after,
        },
        SendError::Ses { status, body, .. } if status >= 500 => SendVerdict::Transient {
            message: format!("SES SendEmail returned {status}: {body}"),
            retry_after: None,
        },
        SendError::Ses { status, body, .. } => {
            let message = format!("SES SendEmail returned {status}: {body}");
            match TRANSIENT_SES_ERRORS.iter().any(|name| body.contains(name)) {
                true => SendVerdict::Transient {
                    message,
                    retry_after: None,
                },
                false => SendVerdict::Terminal(message),
            }
        }
        // Every STS fault precedes the SES POST, so none of them can have sent anything.
        SendError::Sts { status, body } if status == 429 || status >= 500 => {
            SendVerdict::Transient {
                message: format!("STS AssumeRoleWithWebIdentity returned {status}: {body}"),
                retry_after: None,
            }
        }
        SendError::Sts { status, body } => SendVerdict::Terminal(format!(
            "STS AssumeRoleWithWebIdentity returned {status}: {body}"
        )),
        SendError::StsUnreachable(message) => SendVerdict::Transient {
            message: format!("STS AssumeRoleWithWebIdentity: {message}"),
            retry_after: None,
        },
        // The projected web identity token is rewritten in place as it rotates, so a read that
        // misses it is a moment of the platform's plumbing rather than a decision.
        SendError::TokenFile { path, source } => SendVerdict::Transient {
            message: format!(
                "the projected web identity token at {path:?} is unreadable: {source}"
            ),
            retry_after: None,
        },
        SendError::MissingCredential(name) => SendVerdict::Terminal(format!(
            "STS AssumeRoleWithWebIdentity response is missing {name}"
        )),
        SendError::MalformedXml(message) => SendVerdict::Terminal(format!(
            "STS AssumeRoleWithWebIdentity response is not XML: {message}"
        )),
        SendError::Http(error) if error.is_builder() || error.is_connect() => {
            SendVerdict::Transient {
                message: format!("SES SendEmail never opened: {error}"),
                retry_after: None,
            }
        }
        SendError::Http(error) => {
            SendVerdict::Unanswered(format!("SES SendEmail was not answered: {error}"))
        }
    }
}

/// One row this worker leased.
#[derive(Debug, Clone)]
pub struct Delivery {
    pub workspace_id: Uuid,
    pub email: String,
    pub invited_by: String,
    pub workspace_label: String,
    pub sent_at: Option<DateTime<Utc>>,
    pub attempts: i32,
}

/// Another replica owns this row now, so this worker abandons its writeback untouched.
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
    /// The gateway's background task: sweep, and wait a whole interval only when nothing was due.
    /// Only cancellation ends this loop. Nothing a sweep raises retires the poller — a dead poller
    /// would strand every later invitation behind a green `/healthz` — so every fault that belongs
    /// to a row lands in that row, and the rest is reported with its consecutive count. A quiet
    /// tick logs nothing, so one fault is a blip and a climbing count is a fault to root-cause.
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

    /// Materialize every invitation core holds, then carry one due row as far as SES allows. True
    /// when a row was leased, so a busy queue drains without waiting.
    ///
    /// A core that will not answer ends the sweep before any row is claimed. Holding delivery while
    /// the fact's own source is unreachable is the honest reading: every row here exists because
    /// core said so, and the next tick asks again.
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

    /// Every invitation core holds, page by page, inserted where this ledger has none.
    ///
    /// The read is O(every invited member) each cycle. Bounding it would take a mark this ledger
    /// carries forward, and no mark over a stamp assigned before its transaction commits is safe to
    /// carry — the row that commits late falls behind it and is never enumerated again. Bounding it
    /// exactly instead would take core knowing which invitations were delivered, which is a
    /// control-to-core write that does not exist. So the whole source is read, and the cursor here
    /// walks one sweep's pages rather than resuming the next one.
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

    /// One statement for the whole page. Core orders it, so both replicas insert the same rows in
    /// the same order and two concurrent sweeps queue rather than deadlock on each other. A row
    /// already here keeps every field it holds, so a delivered invitation is never re-armed.
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

    /// Take one due row under a lease. `for update skip locked` is what lets both replicas poll the
    /// same table without either waiting on the other, and a claimed row whose lease lapsed is
    /// taken again — the worker holding it did not survive.
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

    /// Hold the lease across an in-flight send. Losing the compare-and-set is silent — the delivery
    /// path's own writes discover it and abandon — but a renewal that cannot *reach* the database
    /// is reported and retried on the next tick rather than ending the task. A dead renewal would
    /// let the lease lapse mid-send, and a second replica claiming the row while this one's SES
    /// POST is still in flight is how one person gets two copies.
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
            .send(&delivery.email, INVITATION_SUBJECT, &text, Some(&html))
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

    /// Three facts and nothing else: who added them, which workspace, and where to sign in. Each
    /// field is bounded here, next to the only call that puts them on a wire.
    ///
    /// Both bodies carry them. The plain-text one is what a client refusing HTML renders and what
    /// a spam filter reads for a message that has an HTML part; the HTML one is the same three
    /// facts under the sign-in page's look. A local part may hold any character an address allows,
    /// so every value crosses `escaped` on its way into the markup.
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

    /// The day's cap and this row's attempt marker, written together under one workspace lock.
    ///
    /// Both replicas count the same rows, so counting outside a lock would let each read ninety-nine
    /// and each send. The lock is held across two statements and no network call, and it is keyed on
    /// the workspace, so one busy workspace never serializes the fleet. What it counts is what was
    /// handed to SES rather than what SES accepted: a message in flight is spent whether or not its
    /// answer came back.
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

    /// The attempt marker clears with the reschedule: this verdict is proof SES never took the
    /// message, so the next claim is a first send rather than an ambiguous one.
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

    /// Every writeback is a compare-and-set on this worker's own lease, so a row a second replica
    /// took is left exactly as that replica left it.
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

/// The five characters that would otherwise close a tag or an attribute early. An address's local
/// part accepts almost anything, so a member could carry one into the markup and the recipient
/// would read whatever it opened.
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

/// A Postgres `interval` built from seconds, so the lease and the backoff bind as one parameter.
fn pg_interval(seconds: i64) -> String {
    format!("{seconds} seconds")
}

/// What a delivery step can carry back: a send verdict, a lost lease, or our own database failing.
#[derive(Debug, thiserror::Error)]
enum Carried {
    #[error(transparent)]
    Send(#[from] SendVerdict),
    #[error(transparent)]
    Lease(#[from] LeaseLost),
    #[error(transparent)]
    Ledger(#[from] LedgerError),
}
