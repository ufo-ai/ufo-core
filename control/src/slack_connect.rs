//! The signup Slack Connect invitation: one operator-workspace channel per approved customer.
//!
//! A granted email domain — one `ufo-control invite` minted — earns one public channel in UFO's
//! *own* Slack workspace and one Slack-generated Slack Connect invitation to the address the grant
//! names. It lands at approval, before that customer signs up, so the channel is open by the time
//! they read the invitation email. Approval never waits for any of it: the grant is the durable
//! event source, so there is no enqueue transaction to lose, and this workflow polls in the
//! background on either gateway replica. A member joining an existing workspace never earns a
//! grant, so the same eligibility test excludes them.
//!
//! The domain is the key, not the grant. One domain is one customer and one channel, so re-granting
//! a domain finds its row already there and sends nothing. Keying on the grant instead would resend
//! on every re-grant, and would lose the row entirely when `InviteCodes::mint` clears an expired
//! grant out of the way.
//!
//! The customer's channel is the idempotency boundary, and `CHANNEL_NAME_SQL` is what makes it one.
//! The name it derives is `ext-<domain-label>-flyingobject` — the domain's first label only, never
//! an email address, since the local part and the TLD both stay out of it — and it derives it where
//! the grant lives, so materializing stays one statement and every replica reaches the same name.
//! It is bounded to 77 characters, inside Slack's 80-character limit.
//!
//! Dropping the TLD means the name is *not* unique across customers: `acme.com` and `acme.io` both
//! derive `ext-acme-flyingobject`. That is a deliberate readability trade. The `channel_name` unique
//! constraint is what keeps it safe — the second customer's row is skipped rather than created, so
//! two customers can never be pointed at one channel. A skipped customer needs a channel by hand.

use chrono::{DateTime, Utc};
use deadpool_postgres::Pool;

use crate::invite::TABLE as INVITE_TABLE;
use crate::store::TABLE as CLAIM_TABLE;

pub const TABLE: &str = "ufo_control.slack_connect_delivery";
pub const DUE_INDEX: &str = "slack_connect_delivery_due";

pub const ENABLED_ENV: &str = "UFO_CONTROL_SLACK_CONNECT_ENABLED";
pub const BOT_TOKEN_ENV: &str = "UFO_CONTROL_SLACK_CONNECT_BOT_TOKEN";
pub const TEAM_ID_ENV: &str = "UFO_CONTROL_SLACK_CONNECT_TEAM_ID";

pub const CHANNEL_PREFIX: &str = "ext";
pub const CHANNEL_SUFFIX: &str = "flyingobject";
pub const MAX_DOMAIN_LABEL_CHARS: usize = 60;

pub const STATE_PENDING: &str = "pending";
pub const STATE_CLAIMED: &str = "claimed";
pub const STATE_DELIVERED: &str = "delivered";
pub const STATE_FAILED: &str = "failed";
pub const DELIVERY_STATES: &[&str] = &[STATE_PENDING, STATE_CLAIMED, STATE_DELIVERED, STATE_FAILED];

pub const DDL: &[&str] = &[
    "create table if not exists ufo_control.slack_connect_delivery (\
       email_domain text primary key,\
       email text not null,\
       state text not null check (state in ('pending', 'claimed', 'delivered', 'failed')),\
       channel_name text not null unique,\
       channel_id text,\
       slack_invitation_id text,\
       invite_attempted_at timestamptz,\
       greeted_at timestamptz,\
       worker_id text,\
       claim_expires_at timestamptz,\
       next_attempt_at timestamptz,\
       attempts integer not null default 0,\
       last_error text,\
       created_at timestamptz not null default now(),\
       updated_at timestamptz not null default now(),\
       delivered_at timestamptz)",
    "create index if not exists slack_connect_delivery_due \
       on ufo_control.slack_connect_delivery (state, next_attempt_at)",
];

/// The channel name derived where the grant lives, so every replica reaches the same one.
pub fn channel_name_sql() -> String {
    format!(
        "'{CHANNEL_PREFIX}-' \
         || btrim(left(regexp_replace(split_part(lower(email_domain), '.', 1), \
            '[^a-z0-9]+', '-', 'g'), {MAX_DOMAIN_LABEL_CHARS}), '-') \
         || '-{CHANNEL_SUFFIX}'"
    )
}

/// Every domain that earned a channel: one holding a live or spent grant, plus one whose claim
/// actually opened a workspace. A member who joined an existing workspace appears in neither.
pub fn earned_sql() -> String {
    format!(
        "select email_domain, email, created_at from {INVITE_TABLE} \
           where consumed_at is not null or expires_at > now() \
         union all \
         select email_domain, email, created_at from {CLAIM_TABLE} where created_workspace"
    )
}

pub fn materialize_sql() -> String {
    let channel_name = channel_name_sql();
    let earned = earned_sql();
    format!(
        "insert into {TABLE} (email_domain, email, state, channel_name) \
           select distinct on ({channel_name}) \
             email_domain, email, '{STATE_PENDING}', {channel_name} \
           from ({earned}) earned \
           order by {channel_name}, created_at, email \
         on conflict do nothing"
    )
}

/// Take one due row under a lease. `for update skip locked` is what lets both replicas poll the
/// same table without either waiting on the other, and a claimed row whose lease lapsed is taken
/// again — the worker holding it did not survive.
pub fn claim_sql() -> String {
    format!(
        "with candidate as (\
           select email_domain from {TABLE} \
           where (state = '{STATE_PENDING}' \
                  and (next_attempt_at is null or next_attempt_at <= now())) \
              or (state = '{STATE_CLAIMED}' and claim_expires_at <= now()) \
           order by created_at, email_domain \
           for update skip locked limit 1) \
         update {TABLE} d \
         set state = '{STATE_CLAIMED}', worker_id = $1, \
             claim_expires_at = now() + $2::text::interval, \
             attempts = d.attempts + 1, updated_at = now() \
         from candidate c where d.email_domain = c.email_domain \
         returning d.email_domain, d.email, d.channel_name, d.channel_id, d.slack_invitation_id, \
                   d.invite_attempted_at, d.greeted_at, d.attempts"
    )
}

#[derive(Debug, thiserror::Error)]
pub enum DeliveryError {
    #[error(transparent)]
    Pool(#[from] deadpool_postgres::PoolError),
    #[error(transparent)]
    Query(#[from] tokio_postgres::Error),
}

/// The operator recovery surface: re-arm one failed row once its cause is corrected, returning when
/// it last changed so the verb can report how long it sat. None when nothing was re-armed — a
/// delivered row is untouchable, and this never speaks to Slack itself.
pub async fn rearm_failed_delivery(
    pool: &Pool,
    email_domain: &str,
) -> Result<Option<DateTime<Utc>>, DeliveryError> {
    let connection = pool.get().await?;
    let row = connection
        .query_opt(
            &format!(
                "with previous as (\
                   select email_domain, updated_at from {TABLE} \
                   where email_domain = $1 and state = '{STATE_FAILED}' for update) \
                 update {TABLE} d set state = '{STATE_PENDING}', worker_id = null, \
                   claim_expires_at = null, next_attempt_at = null, attempts = 0, \
                   last_error = null, updated_at = now() \
                 from previous p where d.email_domain = p.email_domain \
                 returning p.updated_at"
            ),
            &[&email_domain],
        )
        .await?;
    Ok(row.map(|row| row.get("updated_at")))
}

pub const SLACK_API_BASE: &str = "https://slack.com/api";
pub const SLACK_TIMEOUT_SECONDS: u64 = 10;
pub const CHANNEL_PAGE_SIZE: u32 = 200;
pub const INVITE_PAGE_SIZE: u32 = 100;
pub const MAX_PAGES: usize = 20;
pub const ERROR_CHARS: usize = 500;
pub const MAX_EMAIL_CHARS: usize = 254;
pub const MAX_MESSAGE_CHARS: usize = 4000;
pub const REDACTION: &str = "«token»";

pub const POLL_INTERVAL_SECONDS: u64 = 15;
pub const LEASE_SECONDS: i64 = 120;
pub const LEASE_RENEW_SECONDS: u64 = 30;
pub const MAX_ATTEMPTS: i32 = 8;
pub const RETRY_BACKOFF_SECONDS: i64 = 30;
pub const RETRY_BACKOFF_MAX_SECONDS: i64 = 3600;
pub const MAX_RETRY_AFTER_SECONDS: f64 = 60.0;

const NAME_TAKEN: &str = "name_taken";
const LIVE_INVITE_STATUSES: &[&str] = &["sent", "accepted"];
const DEAD_INVITE_STATUSES: &[&str] = &["revoked", "declined", "expired"];
const TRANSIENT_SLACK_ERRORS: &[&str] = &[
    "ratelimit",
    "ratelimited",
    "accesslimited",
    "service_unavailable",
    "internal_error",
    "fatal_error",
    "request_timeout",
];

pub const GREETING: &str = "This channel is shared with ufo. Anyone at {email_domain} can sign in \
                            at https://{apex_host}/login, or from a terminal with \
                            `curl -fsSL https://{apex_host}/ufo | sh`.";

/// What a Slack call answered with. `Transient` is proven external uncertainty — transport, timeout,
/// 429, 5xx, or a documented transient Slack error — and returns the row to `pending` behind a
/// bounded schedule. `Terminal` is authentication, scope, plan, policy, recipient, or channel state
/// no retry can fix, plus any reconciliation Slack refuses to expose; the row lands `failed` for
/// operator review.
#[derive(Debug, thiserror::Error)]
pub enum SlackError {
    #[error("{message}")]
    Transient {
        message: String,
        retry_after: Option<f64>,
    },
    #[error("{0}")]
    Terminal(String),
    /// `conversations.create` refused the deterministic name: this customer's channel already
    /// exists, so the exact name resolves its ID.
    #[error("{0}")]
    NameTaken(String),
}

impl SlackError {
    fn transient(message: impl Into<String>) -> Self {
        Self::Transient {
            message: message.into(),
            retry_after: None,
        }
    }
}

/// Outbound Slack Web API calls under one bot token. Every payload is bounded next to its call, and
/// no message this raises carries the token — `redact` is applied to everything written to a row.
#[derive(Clone)]
pub struct SlackConnectClient {
    bot_token: String,
    base: String,
}

impl std::fmt::Debug for SlackConnectClient {
    /// The token never reaches a repr, so a traceback or a captured local cannot print it.
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("SlackConnectClient")
            .field("base", &self.base)
            .finish_non_exhaustive()
    }
}

impl SlackConnectClient {
    pub fn new(bot_token: String) -> Self {
        Self {
            bot_token,
            base: SLACK_API_BASE.to_string(),
        }
    }

    pub fn with_base(bot_token: String, base: String) -> Self {
        Self { bot_token, base }
    }

    pub fn redact(&self, message: &str) -> String {
        message
            .replace(&self.bot_token, REDACTION)
            .chars()
            .take(ERROR_CHARS)
            .collect()
    }

    pub async fn team_id(&self) -> Result<String, SlackError> {
        let payload = self.call("auth.test", &[]).await?;
        text_at(&payload, &["team_id"])
    }

    pub async fn create_channel(&self, name: &str) -> Result<String, SlackError> {
        let payload = self.call("conversations.create", &[("name", name)]).await?;
        text_at(&payload, &["channel", "id"])
    }

    /// The exact deterministic name, archived channels included — a name-taken refusal names one of
    /// them. Anything else is inconsistent channel state, not a retryable condition.
    pub async fn channel_id_by_name(&self, name: &str) -> Result<String, SlackError> {
        let mut cursor = String::new();
        for _ in 0..MAX_PAGES {
            let payload = self
                .call(
                    "conversations.list",
                    &[
                        ("types", "public_channel"),
                        ("exclude_archived", "false"),
                        ("limit", &CHANNEL_PAGE_SIZE.to_string()),
                        ("cursor", &cursor),
                    ],
                )
                .await?;
            if let Some(channels) = payload.get("channels").and_then(|value| value.as_array()) {
                for channel in channels {
                    if channel.get("name").and_then(|value| value.as_str()) == Some(name) {
                        return text_at(channel, &["id"]);
                    }
                }
            }
            cursor = next_cursor(&payload);
            if cursor.is_empty() {
                break;
            }
        }
        Err(SlackError::Terminal(format!(
            "conversations.create refused {name} but no channel carries it"
        )))
    }

    /// The *live* Slack Connect invitation for `channel_id`, or None when this channel has none.
    ///
    /// Slack keeps listing an invitation after it dies — archiving a channel flips its invite to
    /// `revoked` and leaves it in the list — so matching the channel alone would read a dead
    /// invitation as proof one was sent and settle a customer who never got a working invite. Only
    /// the live statuses reconcile; the dead ones mean it is safe to invite again, which is evidence
    /// rather than a blind duplicate. A status this code does not recognize, and a walk that exceeds
    /// its bound, are both ambiguous — they raise for operator review.
    pub async fn outgoing_invite_id(&self, channel_id: &str) -> Result<Option<String>, SlackError> {
        let mut cursor = String::new();
        let mut unrecognized: std::collections::BTreeSet<String> = Default::default();
        for _ in 0..MAX_PAGES {
            let payload = self
                .call(
                    "conversations.listConnectInvites",
                    &[
                        ("count", &INVITE_PAGE_SIZE.to_string()),
                        ("cursor", &cursor),
                    ],
                )
                .await?;
            if let Some(invites) = payload.get("invites").and_then(|value| value.as_array()) {
                for entry in invites {
                    let matches = entry
                        .get("channel")
                        .and_then(|channel| channel.get("id"))
                        .and_then(|value| value.as_str())
                        == Some(channel_id);
                    if !matches {
                        continue;
                    }
                    let status = entry
                        .get("status")
                        .and_then(|value| value.as_str())
                        .unwrap_or_default()
                        .to_string();
                    if LIVE_INVITE_STATUSES.contains(&status.as_str()) {
                        return text_at(entry, &["invite", "id"]).map(Some);
                    }
                    if !DEAD_INVITE_STATUSES.contains(&status.as_str()) {
                        unrecognized.insert(status);
                    }
                }
            }
            cursor = next_cursor(&payload);
            if cursor.is_empty() {
                if !unrecognized.is_empty() {
                    return Err(SlackError::Terminal(format!(
                        "Slack Connect invite status {unrecognized:?} is unrecognized"
                    )));
                }
                return Ok(None);
            }
        }
        Err(SlackError::Terminal(
            "outgoing Slack Connect invites exceed the bounded walk".to_string(),
        ))
    }

    pub async fn is_externally_shared(&self, channel_id: &str) -> Result<bool, SlackError> {
        let payload = self
            .call("conversations.info", &[("channel", channel_id)])
            .await?;
        let channel = payload.get("channel");
        let flag = |name: &str| {
            channel
                .and_then(|channel| channel.get(name))
                .and_then(|value| value.as_bool())
                .unwrap_or(false)
        };
        Ok(flag("is_ext_shared") || flag("is_pending_ext_shared"))
    }

    pub async fn post_message(&self, channel_id: &str, text: &str) -> Result<(), SlackError> {
        if text.chars().count() > MAX_MESSAGE_CHARS {
            return Err(SlackError::Terminal(format!(
                "message exceeds {MAX_MESSAGE_CHARS} characters"
            )));
        }
        self.call(
            "chat.postMessage",
            &[("channel", channel_id), ("text", text)],
        )
        .await?;
        Ok(())
    }

    pub async fn invite_shared(&self, channel_id: &str, email: &str) -> Result<String, SlackError> {
        if email.chars().count() > MAX_EMAIL_CHARS {
            return Err(SlackError::Terminal(format!(
                "recipient email exceeds {MAX_EMAIL_CHARS} characters"
            )));
        }
        let payload = self
            .call(
                "conversations.inviteShared",
                &[
                    ("channel", channel_id),
                    ("emails", email),
                    ("external_limited", "false"),
                ],
            )
            .await?;
        text_at(&payload, &["invite_id"])
    }

    async fn call(
        &self,
        method: &str,
        params: &[(&str, &str)],
    ) -> Result<serde_json::Value, SlackError> {
        let form: Vec<(&str, &str)> = params
            .iter()
            .filter(|(_, value)| !value.is_empty())
            .copied()
            .collect();
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(SLACK_TIMEOUT_SECONDS))
            .build()
            .map_err(|error| SlackError::transient(format!("{method} client: {error}")))?;
        let response = client
            .post(format!("{}/{method}", self.base.trim_end_matches('/')))
            .bearer_auth(&self.bot_token)
            .form(&form)
            .send()
            .await
            .map_err(|error| {
                SlackError::transient(format!("{method} transport failure: {error}"))
            })?;
        let status = response.status();
        if status.as_u16() == 429 {
            let retry_after = response
                .headers()
                .get("retry-after")
                .and_then(|value| value.to_str().ok())
                .and_then(|value| value.trim().parse::<f64>().ok())
                .map(|seconds| seconds.min(MAX_RETRY_AFTER_SECONDS));
            return Err(SlackError::Transient {
                message: format!("{method} was rate limited"),
                retry_after,
            });
        }
        if status.as_u16() >= 500 {
            return Err(SlackError::transient(format!("{method} returned {status}")));
        }
        let body = response.text().await.unwrap_or_default();
        if status.is_client_error() {
            let clipped: String = body.chars().take(ERROR_CHARS).collect();
            return Err(SlackError::Terminal(format!(
                "{method} returned {}: {clipped}",
                status.as_u16()
            )));
        }
        let payload: serde_json::Value = serde_json::from_str(&body)
            .map_err(|_| SlackError::Terminal(format!("{method} answered no JSON")))?;
        if payload.get("ok").and_then(|value| value.as_bool()) == Some(true) {
            return Ok(payload);
        }
        let error = payload
            .get("error")
            .and_then(|value| value.as_str())
            .unwrap_or("unknown")
            .to_string();
        if error == NAME_TAKEN {
            return Err(SlackError::NameTaken(format!("{method}: {error}")));
        }
        if TRANSIENT_SLACK_ERRORS.contains(&error.as_str()) {
            return Err(SlackError::transient(format!("{method}: {error}")));
        }
        Err(SlackError::Terminal(format!("{method}: {error}")))
    }
}

fn next_cursor(payload: &serde_json::Value) -> String {
    payload
        .get("response_metadata")
        .and_then(|metadata| metadata.get("next_cursor"))
        .and_then(|value| value.as_str())
        .unwrap_or_default()
        .to_string()
}

fn text_at(payload: &serde_json::Value, path: &[&str]) -> Result<String, SlackError> {
    let mut value = payload;
    for key in path {
        value = value.get(key).ok_or_else(|| {
            SlackError::Terminal(format!("Slack response is missing {}", path.join(".")))
        })?;
    }
    match value {
        serde_json::Value::String(text) => Ok(text.clone()),
        other => Ok(other.to_string()),
    }
}

/// One row this worker leased.
#[derive(Debug, Clone)]
pub struct Delivery {
    pub email_domain: String,
    pub email: String,
    pub channel_name: String,
    pub channel_id: Option<String>,
    pub slack_invitation_id: Option<String>,
    pub invite_attempted_at: Option<DateTime<Utc>>,
    pub greeted_at: Option<DateTime<Utc>>,
    pub attempts: i32,
}

/// Another replica owns this row now, so this worker abandons its writeback untouched.
#[derive(Debug, thiserror::Error)]
#[error("{0} is no longer leased by this worker")]
pub struct LeaseLost(String);

#[derive(Debug, Clone)]
pub struct SlackConnectInviter {
    pub pool: Pool,
    pub slack: SlackConnectClient,
    pub team_id: String,
    pub apex_host: String,
    pub worker_id: String,
    pub poll_interval: std::time::Duration,
}

impl SlackConnectInviter {
    /// The gateway's background task: sweep, and wait a whole interval only when nothing was due.
    /// Only cancellation ends this loop. Nothing a sweep raises retires the poller — a dead poller
    /// would strand every later signup's row behind a green `/healthz`, and gateway health stays
    /// clear of Slack — so every fault lands in the row instead.
    ///
    /// A sweep that fails before any row is claimed — our own database, not Slack — is reported with
    /// its consecutive count rather than masked: a quiet tick logs nothing at all, so one fault is a
    /// blip and a climbing count is an internal fault to root-cause.
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
                        target: "ufo_control::slack_connect",
                        "slack_connect.sweep.failed consecutive={consecutive_failures} {error}"
                    );
                    false
                }
            };
            if !claimed {
                tokio::time::sleep(self.poll_interval).await;
            }
        }
    }

    /// Materialize every granted domain, then carry one due row as far as Slack allows. True when a
    /// row was leased, so a busy queue drains without waiting.
    pub async fn poll(&self) -> Result<bool, DeliveryError> {
        self.materialize().await?;
        let Some(delivery) = self.claim().await? else {
            return Ok(false);
        };
        let renewal = {
            let inviter = self.clone();
            let domain = delivery.email_domain.clone();
            tokio::spawn(async move { inviter.renew_lease(domain).await })
        };
        let advanced = self.advance(&delivery).await;
        renewal.abort();
        if let Err(lost) = advanced {
            tracing::warn!(
                target: "ufo_control::slack_connect",
                "slack_connect.lease.lost domain={} {lost}", delivery.email_domain
            );
        }
        Ok(true)
    }

    /// One replica materializes at a time, and no single grant can poison the batch. The insert is
    /// one `INSERT ... SELECT`, so any unique violation would roll back every row it was inserting
    /// rather than only the offender — and because the select re-enumerates every grant each cycle,
    /// one bad row would stall delivery for everyone forever. So the conflict clause arbitrates
    /// *all* unique constraints, not only the primary key. The advisory lock stops two replicas
    /// racing on the same insert, which the primary-key arbiter alone did not cover.
    async fn materialize(&self) -> Result<(), DeliveryError> {
        let mut connection = self.pool.get().await?;
        let transaction = connection.transaction().await?;
        transaction
            .batch_execute(&format!(
                "select pg_advisory_xact_lock(hashtext('{TABLE}'))"
            ))
            .await?;
        transaction.batch_execute(&materialize_sql()).await?;
        transaction.commit().await?;
        Ok(())
    }

    async fn claim(&self) -> Result<Option<Delivery>, DeliveryError> {
        let connection = self.pool.get().await?;
        let lease = pg_interval(LEASE_SECONDS);
        let row = connection
            .query_opt(&claim_sql(), &[&self.worker_id, &lease])
            .await?;
        Ok(row.map(|row| Delivery {
            email_domain: row.get("email_domain"),
            email: row.get("email"),
            channel_name: row.get("channel_name"),
            channel_id: row.get("channel_id"),
            slack_invitation_id: row.get("slack_invitation_id"),
            invite_attempted_at: row.get("invite_attempted_at"),
            greeted_at: row.get("greeted_at"),
            attempts: row.get("attempts"),
        }))
    }

    /// Hold the lease across an in-flight Slack call. Losing the compare-and-set is silent — the
    /// delivery path's own writes discover it and abandon — but a renewal that cannot *reach* the
    /// database is reported and retried on the next tick rather than ending the task. A dead renewal
    /// would let the lease lapse mid-delivery, and a second replica claiming the row while this
    /// one's `inviteShared` is still in flight is how a duplicate invitation gets sent.
    async fn renew_lease(&self, email_domain: String) {
        loop {
            tokio::time::sleep(std::time::Duration::from_secs(LEASE_RENEW_SECONDS)).await;
            let renewed = async {
                let connection = self.pool.get().await?;
                connection
                    .execute(
                        &format!(
                            "update {TABLE} set claim_expires_at = now() + $3::text::interval, \
                             updated_at = now() where email_domain = $1 and worker_id = $2"
                        ),
                        &[&email_domain, &self.worker_id, &pg_interval(LEASE_SECONDS)],
                    )
                    .await?;
                Ok::<(), DeliveryError>(())
            }
            .await;
            if let Err(error) = renewed {
                tracing::error!(
                    target: "ufo_control::slack_connect",
                    "slack_connect.lease.renew_failed domain={email_domain} {error}"
                );
            }
        }
    }

    async fn advance(&self, delivery: &Delivery) -> Result<(), LeaseLost> {
        match self.deliver(delivery).await {
            Ok(()) => Ok(()),
            Err(Carried::Lease(lost)) => Err(lost),
            Err(Carried::Slack(SlackError::Transient {
                message,
                retry_after,
            })) => self.reschedule(delivery, &message, retry_after).await,
            Err(Carried::Slack(other)) => self.fail(delivery, &other.to_string()).await,
            Err(Carried::Database(error)) => {
                tracing::error!(
                    target: "ufo_control::slack_connect",
                    "slack_connect.unexpected domain={} {error}", delivery.email_domain
                );
                self.fail(delivery, &error.to_string()).await
            }
        }
    }

    async fn deliver(&self, delivery: &Delivery) -> Result<(), Carried> {
        self.verify_team().await?;
        let channel_id = match &delivery.channel_id {
            Some(channel_id) => channel_id.clone(),
            None => self.open_channel(delivery).await?,
        };
        let invitation_id = match &delivery.slack_invitation_id {
            Some(invitation_id) => Some(invitation_id.clone()),
            None => self.invite(delivery, &channel_id).await?,
        };
        if delivery.greeted_at.is_none() {
            self.greet(delivery, &channel_id).await?;
        }
        self.write(
            &delivery.email_domain,
            &format!(
                "state = '{STATE_DELIVERED}', delivered_at = now(), worker_id = null, \
                 claim_expires_at = null, next_attempt_at = null, last_error = null"
            ),
            &[],
        )
        .await?;
        tracing::info!(
            target: "ufo_control::slack_connect",
            "slack_connect.delivered domain={} channel={channel_id} invitation={invitation_id:?}",
            delivery.email_domain
        );
        Ok(())
    }

    /// Every delivery re-proves the token's workspace before anything is mutated. A memo would buy
    /// one Slack call per new customer and cost the guarantee: a token rotated to another workspace
    /// would keep delivering on a replica's stale word until that replica restarted.
    async fn verify_team(&self) -> Result<(), Carried> {
        let team_id = self.slack.team_id().await?;
        if team_id != self.team_id {
            return Err(Carried::Slack(SlackError::Terminal(format!(
                "{BOT_TOKEN_ENV} belongs to team {team_id}, not {}",
                self.team_id
            ))));
        }
        Ok(())
    }

    async fn open_channel(&self, delivery: &Delivery) -> Result<String, Carried> {
        let channel_id = match self.slack.create_channel(&delivery.channel_name).await {
            Ok(channel_id) => channel_id,
            Err(SlackError::NameTaken(_)) => {
                self.slack
                    .channel_id_by_name(&delivery.channel_name)
                    .await?
            }
            Err(other) => return Err(other.into()),
        };
        self.write(&delivery.email_domain, "channel_id = $3", &[&channel_id])
            .await?;
        Ok(channel_id)
    }

    /// A row whose previous attempt reached Slack reconciles first: an outgoing invitation names
    /// itself, and an externally shared channel proves one landed even where the invitation is no
    /// longer listed. Only a fresh delivery skips these rate-limited reads.
    async fn invite(
        &self,
        delivery: &Delivery,
        channel_id: &str,
    ) -> Result<Option<String>, Carried> {
        if delivery.invite_attempted_at.is_some() {
            if let Some(reconciled) = self.slack.outgoing_invite_id(channel_id).await? {
                self.write(
                    &delivery.email_domain,
                    "slack_invitation_id = $3",
                    &[&reconciled],
                )
                .await?;
                return Ok(Some(reconciled));
            }
            if self.slack.is_externally_shared(channel_id).await? {
                return Ok(None);
            }
            tracing::info!(
                target: "ufo_control::slack_connect",
                "slack_connect.reconcile.no_live_invite domain={} channel={channel_id}",
                delivery.email_domain
            );
        }
        self.write(&delivery.email_domain, "invite_attempted_at = now()", &[])
            .await?;
        let invitation_id = self
            .slack
            .invite_shared(channel_id, &delivery.email)
            .await?;
        self.write(
            &delivery.email_domain,
            "slack_invitation_id = $3",
            &[&invitation_id],
        )
        .await?;
        Ok(Some(invitation_id))
    }

    /// One message naming where this customer signs in, posted before the row settles so a transient
    /// failure retries it. The marker is written after Slack answers — a duplicate message costs a
    /// reader one repeated line, while a missing one leaves a channel that names nowhere to sign in.
    async fn greet(&self, delivery: &Delivery, channel_id: &str) -> Result<(), Carried> {
        let text = GREETING
            .replace("{email_domain}", &delivery.email_domain)
            .replace("{apex_host}", &self.apex_host);
        self.slack.post_message(channel_id, &text).await?;
        self.write(&delivery.email_domain, "greeted_at = now()", &[])
            .await?;
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
        let redacted = self.slack.redact(message);
        let written = self
            .write(
                &delivery.email_domain,
                &format!(
                    "state = '{STATE_PENDING}', worker_id = null, claim_expires_at = null, \
                     next_attempt_at = now() + $3::text::interval, last_error = $4"
                ),
                &[&pg_interval(delay), &redacted],
            )
            .await;
        tracing::warn!(
            target: "ufo_control::slack_connect",
            "slack_connect.retry domain={} attempts={} in={delay}s",
            delivery.email_domain, delivery.attempts
        );
        written.map_err(carried_lease)
    }

    async fn fail(&self, delivery: &Delivery, message: &str) -> Result<(), LeaseLost> {
        let redacted = self.slack.redact(message);
        let written = self
            .write(
                &delivery.email_domain,
                &format!(
                    "state = '{STATE_FAILED}', worker_id = null, claim_expires_at = null, \
                     next_attempt_at = null, last_error = $3"
                ),
                &[&redacted],
            )
            .await;
        tracing::error!(
            target: "ufo_control::slack_connect",
            "slack_connect.failed domain={} error={redacted}", delivery.email_domain
        );
        written.map_err(carried_lease)
    }

    /// Every writeback is a compare-and-set on this worker's own lease, so a row a second replica
    /// took is left exactly as that replica left it.
    async fn write(
        &self,
        email_domain: &str,
        assignment: &str,
        values: &[&(dyn tokio_postgres::types::ToSql + Sync)],
    ) -> Result<(), Carried> {
        let connection = self.pool.get().await.map_err(DeliveryError::from)?;
        let mut parameters: Vec<&(dyn tokio_postgres::types::ToSql + Sync)> =
            vec![&email_domain, &self.worker_id];
        parameters.extend_from_slice(values);
        let owned = connection
            .query_opt(
                &format!(
                    "update {TABLE} set {assignment}, updated_at = now() \
                     where email_domain = $1 and worker_id = $2 returning true"
                ),
                &parameters,
            )
            .await
            .map_err(DeliveryError::from)?;
        if owned.is_none() {
            return Err(Carried::Lease(LeaseLost(email_domain.to_string())));
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

/// A Postgres `interval` built from seconds, so the lease and the backoff bind as one parameter.
fn pg_interval(seconds: i64) -> String {
    format!("{seconds} seconds")
}

/// What a delivery step can carry back: a Slack verdict, a lost lease, or our own database failing.
#[derive(Debug, thiserror::Error)]
enum Carried {
    #[error(transparent)]
    Slack(#[from] SlackError),
    #[error(transparent)]
    Lease(#[from] LeaseLost),
    #[error(transparent)]
    Database(#[from] DeliveryError),
}

#[derive(Debug, thiserror::Error)]
pub enum SlackConfigError {
    #[error("{0} is unset — required when {ENABLED_ENV} is true")]
    MissingEnv(&'static str),
    #[error("{ENABLED_ENV}={0:?} is not a boolean (true/false)")]
    NotABoolean(String),
}

/// None when the deploy has not enabled signup Slack Connect invitations. Enabled, the token and the
/// expected operator team are required here — at gateway startup — so a half-configured deploy never
/// reaches Slack. Garbage in the switch fails loud and never defaults to on.
pub fn slack_connect_from_env(
    pool: Pool,
    apex_host: String,
    worker_id: String,
) -> Result<Option<SlackConnectInviter>, SlackConfigError> {
    let enabled = std::env::var(ENABLED_ENV)
        .unwrap_or_else(|_| "false".to_string())
        .trim()
        .to_lowercase();
    match enabled.as_str() {
        "false" | "0" => Ok(None),
        "true" | "1" => Ok(Some(SlackConnectInviter {
            pool,
            slack: SlackConnectClient::new(require_env(BOT_TOKEN_ENV)?),
            team_id: require_env(TEAM_ID_ENV)?,
            apex_host,
            worker_id,
            poll_interval: std::time::Duration::from_secs(POLL_INTERVAL_SECONDS),
        })),
        other => Err(SlackConfigError::NotABoolean(other.to_string())),
    }
}

fn require_env(name: &'static str) -> Result<String, SlackConfigError> {
    std::env::var(name)
        .ok()
        .filter(|value| !value.is_empty())
        .ok_or(SlackConfigError::MissingEnv(name))
}
