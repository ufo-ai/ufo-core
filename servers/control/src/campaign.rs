use std::collections::HashSet;

use chrono::{DateTime, Utc};
use deadpool_postgres::Pool;
use serde::{Deserialize, Serialize};
use tokio_postgres::Row;
use uuid::Uuid;

use crate::email::{normalize_email, FounderSender, Sender};
use crate::email_send::{FOUNDER_UPDATES, PREFERENCE_TABLE};
use crate::gateway::OPERATOR_EMAIL_DOMAIN;
use crate::message::{render, Message, Words};
use crate::shared::{SeatError, SeatedMember, SharedWorkspaces};

pub const TABLE: &str = "ufo_control.email_campaign";
pub const RECIPIENT_TABLE: &str = "ufo_control.email_recipient";
pub const EVENT_TABLE: &str = "ufo_control.email_event";

pub const STATE_DRAFT: &str = "draft";
pub const STATE_PREPARED: &str = "prepared";
pub const STATE_APPROVED: &str = "approved";
pub const STATE_SENDING: &str = "sending";
pub const STATE_COMPLETED: &str = "completed";
pub const STATE_CANCELLED: &str = "cancelled";

pub const AUDIENCE_MEMBERS: &str = "members";
pub const AUDIENCE_OPERATORS: &str = "operators";

pub const SEND_PENDING: &str = "pending";
pub const SEND_ATTEMPTED: &str = "attempted";
pub const SEND_SENT: &str = "sent";
pub const SEND_FAILED: &str = "failed";
pub const SEND_CANCELLED: &str = "cancelled";

pub const DELIVERED: &str = "delivered";
pub const DELAYED: &str = "delayed";
pub const BOUNCED: &str = "bounced";
pub const COMPLAINED: &str = "complained";
pub const UNSUBSCRIBED: &str = "unsubscribed";
pub const REJECTED: &str = "rejected";
pub const RENDERING_FAILED: &str = "rendering_failed";

/// An address in either of these cannot receive mail at all: it bounced, or someone called it
/// spam. Reaching it again costs the sending domain its standing, so it is barred from every
/// message this deploy sends, of every topic, transactional included.
///
/// `UNSUBSCRIBED` is deliberately absent. An unsubscribe is a member saying they want no more of
/// one *topic*, not that the address is unreachable — so it is recorded as a topic preference and
/// bars that topic alone. A member who wants no more product news is still told their workspace ran
/// out of credit.
pub const SUPPRESSED: &[&str] = &[BOUNCED, COMPLAINED];

/// What bars a message a workspace sends about a member's own money or access. Only the states
/// that say the address itself does not work: leaving a campaign is a preference about marketing,
/// and a member who asked for no more of our news has not asked to be left unable to pay.
pub const UNREACHABLE: &[&str] = &[BOUNCED, COMPLAINED];

pub const MAX_SUBJECT_CHARS: usize = 200;
pub const MAX_BODY_CHARS: usize = 20_000;
pub const MAX_ACTION_LABEL_CHARS: usize = 60;
pub const MAX_ACTION_URL_CHARS: usize = 2000;
pub const SAMPLE_RECIPIENTS: i64 = 10;

pub const DDL: &[&str] = &[
    "create table if not exists ufo_control.email_campaign (\
       id uuid primary key,\
       revision integer not null default 1,\
       subject text not null,\
       body text not null,\
       action_label text,\
       action_url text,\
       sender text not null,\
       audience text not null check (audience in ('members', 'operators')),\
       state text not null check (state in ('draft', 'prepared', 'approved', 'sending', \
                                            'completed', 'cancelled')),\
       scheduled_at timestamptz,\
       created_by text not null,\
       approved_by text,\
       approved_revision integer,\
       prepared_at timestamptz,\
       approved_at timestamptz,\
       started_at timestamptz,\
       completed_at timestamptz,\
       cancelled_at timestamptz,\
       created_at timestamptz not null default now(),\
       updated_at timestamptz not null default now())",
    "create table if not exists ufo_control.email_recipient (\
       campaign_id uuid not null references ufo_control.email_campaign (id) on delete cascade,\
       email text not null,\
       member_id uuid,\
       test boolean not null default false,\
       state text not null check (state in ('pending', 'attempted', 'sent', 'failed', \
                                            'cancelled')),\
       delivery text,\
       attempted_at timestamptz,\
       ses_message_id text,\
       last_error text,\
       worker_id text,\
       claim_expires_at timestamptz,\
       created_at timestamptz not null default now(),\
       updated_at timestamptz not null default now(),\
       primary key (campaign_id, email))",
    "create index if not exists email_recipient_due \
       on ufo_control.email_recipient (state, claim_expires_at)",
    "create index if not exists email_recipient_message \
       on ufo_control.email_recipient (ses_message_id)",
    "create index if not exists email_recipient_suppressed \
       on ufo_control.email_recipient (email) \
       where delivery in ('bounced', 'complained', 'unsubscribed')",
    "create table if not exists ufo_control.email_event (\
       id uuid primary key,\
       campaign_id uuid,\
       email text not null,\
       ses_message_id text not null,\
       event_type text not null,\
       occurred_at timestamptz not null,\
       payload jsonb not null,\
       received_at timestamptz not null default now(),\
       unique (ses_message_id, event_type, occurred_at))",
];

const COLUMNS: &str = "id, revision, subject, body, action_label, action_url, sender, audience, \
                       state, scheduled_at, created_by, approved_by, approved_revision, \
                       created_at, updated_at";

#[derive(Debug, thiserror::Error)]
pub enum CampaignError {
    #[error("there is no campaign {0}")]
    Absent(Uuid),
    #[error("{0}")]
    Refused(String),
    #[error("campaign {id} is at revision {held}, not {stated}")]
    Stale { id: Uuid, held: i32, stated: i32 },
    #[error("a campaign in {state} cannot {act}")]
    State { state: String, act: &'static str },
    #[error(transparent)]
    Pool(#[from] deadpool_postgres::PoolError),
    #[error(transparent)]
    Query(#[from] tokio_postgres::Error),
    #[error("the member listing was refused: {0}")]
    Core(#[from] SeatError),
    #[error("the contact list could not be read: {0}")]
    Contacts(String),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Campaign {
    pub id: Uuid,
    pub revision: i32,
    pub subject: String,
    pub body: String,
    pub action_label: Option<String>,
    pub action_url: Option<String>,
    pub sender: String,
    pub audience: String,
    pub state: String,
    pub scheduled_at: Option<DateTime<Utc>>,
    pub created_by: String,
    pub approved_by: Option<String>,
    pub approved_revision: Option<i32>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

fn campaign(row: &Row) -> Campaign {
    Campaign {
        id: row.get("id"),
        revision: row.get("revision"),
        subject: row.get("subject"),
        body: row.get("body"),
        action_label: row.get("action_label"),
        action_url: row.get("action_url"),
        sender: row.get("sender"),
        audience: row.get("audience"),
        state: row.get("state"),
        scheduled_at: row.get("scheduled_at"),
        created_by: row.get("created_by"),
        approved_by: row.get("approved_by"),
        approved_revision: row.get("approved_revision"),
        created_at: row.get("created_at"),
        updated_at: row.get("updated_at"),
    }
}

/// What compose posts. Every bound is checked here rather than at the column, so a refusal reaches
/// the operator as a sentence instead of a constraint violation.
#[derive(Debug, Clone, Deserialize)]
pub struct Draft {
    pub subject: String,
    pub body: String,
    pub action_label: Option<String>,
    pub action_url: Option<String>,
    pub audience: String,
    pub sender: String,
}

impl Draft {
    /// `senders` is the deploy's configured list. A sender outside it is refused here rather than by
    /// SES, whose IAM condition names the same addresses and would fail the send instead.
    pub fn checked(self, senders: &[Sender]) -> Result<Self, CampaignError> {
        let subject = self.subject.trim().to_string();
        let body = self.body.trim().to_string();
        let action_label = trimmed(self.action_label);
        let action_url = trimmed(self.action_url);
        for (what, value, bound) in [
            ("subject", &subject, MAX_SUBJECT_CHARS),
            ("body", &body, MAX_BODY_CHARS),
        ] {
            if value.is_empty() {
                return Err(CampaignError::Refused(format!("the {what} is required")));
            }
            if value.chars().count() > bound {
                return Err(CampaignError::Refused(format!(
                    "the {what} exceeds {bound} characters"
                )));
            }
        }
        if action_label.is_some() != action_url.is_some() {
            return Err(CampaignError::Refused(
                "an action takes both a label and a URL".to_string(),
            ));
        }
        if let Some(label) = &action_label {
            if label.chars().count() > MAX_ACTION_LABEL_CHARS {
                return Err(CampaignError::Refused(format!(
                    "the action label exceeds {MAX_ACTION_LABEL_CHARS} characters"
                )));
            }
        }
        if let Some(url) = &action_url {
            if !url.starts_with("https://") || url.chars().count() > MAX_ACTION_URL_CHARS {
                return Err(CampaignError::Refused(format!(
                    "the action URL must be https and under {MAX_ACTION_URL_CHARS} characters"
                )));
            }
        }
        if ![AUDIENCE_MEMBERS, AUDIENCE_OPERATORS].contains(&self.audience.as_str()) {
            return Err(CampaignError::Refused(format!(
                "the audience is {AUDIENCE_MEMBERS} or {AUDIENCE_OPERATORS}"
            )));
        }
        if !senders.iter().any(|sender| sender.label == self.sender) {
            return Err(CampaignError::Refused(format!(
                "{:?} is not a configured sender",
                self.sender
            )));
        }
        Ok(Self {
            subject,
            body,
            action_label,
            action_url,
            audience: self.audience,
            sender: self.sender,
        })
    }
}

fn trimmed(value: Option<String>) -> Option<String> {
    value
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

/// The exact bytes the send carries, so the preview and the worker cannot disagree.
pub fn campaign_message(campaign: &Campaign, apex_host: &str) -> Message {
    render(
        &Words {
            subject: &campaign.subject,
            body: &campaign.body,
            action_label: campaign.action_label.as_deref(),
            action_url: campaign.action_url.as_deref(),
            unsubscribe: true,
        },
        apex_host,
    )
}

pub async fn read_campaign(pool: &Pool, id: Uuid) -> Result<Campaign, CampaignError> {
    let connection = pool.get().await?;
    connection
        .query_opt(
            &format!("select {COLUMNS} from {TABLE} where id = $1"),
            &[&id],
        )
        .await?
        .as_ref()
        .map(campaign)
        .ok_or(CampaignError::Absent(id))
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct Counts {
    pub audience: i64,
    pub submitted: i64,
    pub delivered: i64,
    pub delayed: i64,
    pub bounced: i64,
    pub complained: i64,
    pub unsubscribed: i64,
    pub failed: i64,
    pub cancelled: i64,
    pub tests: i64,
}

/// What preparing would resolve to, or has resolved to. Before the freeze the tallies are computed
/// live so an operator sees who a prepare would drop; after it they describe the frozen rows.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct Exclusions {
    pub operator: i64,
    pub suppressed: i64,
    pub duplicate: i64,
}

#[derive(Debug, Clone, Serialize)]
pub struct Preview {
    pub campaign: Campaign,
    pub message: Message,
    pub counts: Counts,
    pub exclusions: Exclusions,
    pub sample: Vec<String>,
    pub frozen: bool,
}

struct Audience {
    seated: Vec<(String, Uuid)>,
    exclusions: Exclusions,
}

#[derive(Clone)]
pub struct Campaigns {
    pub pool: Pool,
    pub core: SharedWorkspaces,
    pub sender: FounderSender,
    pub apex_host: String,
}

impl Campaigns {
    pub async fn list(&self) -> Result<Vec<Campaign>, CampaignError> {
        let connection = self.pool.get().await?;
        let rows = connection
            .query(
                &format!("select {COLUMNS} from {TABLE} order by created_at desc"),
                &[],
            )
            .await?;
        Ok(rows.iter().map(campaign).collect())
    }

    pub async fn create(&self, operator: &str, draft: Draft) -> Result<Campaign, CampaignError> {
        let draft = draft.checked(&self.sender.senders)?;
        let connection = self.pool.get().await?;
        let row = connection
            .query_one(
                &format!(
                    "insert into {TABLE} \
                       (id, subject, body, action_label, action_url, sender, audience, state, \
                        created_by) \
                     values ($1, $2, $3, $4, $5, $6, $7, '{STATE_DRAFT}', $8) returning {COLUMNS}"
                ),
                &[
                    &Uuid::new_v4(),
                    &draft.subject,
                    &draft.body,
                    &draft.action_label,
                    &draft.action_url,
                    &draft.sender,
                    &draft.audience,
                    &operator,
                ],
            )
            .await?;
        Ok(campaign(&row))
    }

    /// Editing content is a new revision, and a new revision is a new campaign as far as approval
    /// and the audience are concerned: both are dropped, because each was granted over the words
    /// this edit just replaced.
    pub async fn revise(
        &self,
        id: Uuid,
        revision: i32,
        draft: Draft,
    ) -> Result<Campaign, CampaignError> {
        let draft = draft.checked(&self.sender.senders)?;
        let mut connection = self.pool.get().await?;
        let transaction = connection.transaction().await?;
        let row = transaction
            .query_opt(
                &format!(
                    "update {TABLE} set revision = revision + 1, subject = $4, body = $5, \
                       action_label = $6, action_url = $7, audience = $8, sender = $9, \
                       state = '{STATE_DRAFT}', \
                       approved_by = null, approved_revision = null, approved_at = null, \
                       prepared_at = null, scheduled_at = null, updated_at = now() \
                     where id = $1 and revision = $2 and state = any($3::text[]) \
                     returning {COLUMNS}"
                ),
                &[
                    &id,
                    &revision,
                    &vec![
                        STATE_DRAFT.to_string(),
                        STATE_PREPARED.to_string(),
                        STATE_APPROVED.to_string(),
                    ],
                    &draft.subject,
                    &draft.body,
                    &draft.action_label,
                    &draft.action_url,
                    &draft.audience,
                    &draft.sender,
                ],
            )
            .await?;
        let Some(row) = row else {
            return Err(self.why(id, revision, "be edited").await);
        };
        transaction
            .execute(
                &format!(
                    "delete from {RECIPIENT_TABLE} where campaign_id = $1 and not test \
                       and state = '{SEND_PENDING}'"
                ),
                &[&id],
            )
            .await?;
        transaction.commit().await?;
        Ok(campaign(&row))
    }

    /// Freeze the words and the exact list. Everything the send will read is written here, so a
    /// member seated after this moment is not in the campaign and an approval names a fixed count.
    pub async fn prepare(&self, id: Uuid, revision: i32) -> Result<Preview, CampaignError> {
        let held = self.read(id).await?;
        if held.revision != revision {
            return Err(CampaignError::Stale {
                id,
                held: held.revision,
                stated: revision,
            });
        }
        if ![STATE_DRAFT, STATE_PREPARED].contains(&held.state.as_str()) {
            return Err(CampaignError::State {
                state: held.state,
                act: "be prepared",
            });
        }
        let audience = self.audience(&held).await?;
        let emails: Vec<String> = audience
            .seated
            .iter()
            .map(|(email, _)| email.clone())
            .collect();
        let members: Vec<Uuid> = audience.seated.iter().map(|(_, member)| *member).collect();
        let mut connection = self.pool.get().await?;
        let transaction = connection.transaction().await?;
        let owned = transaction
            .query_opt(
                &format!(
                    "update {TABLE} set state = '{STATE_PREPARED}', prepared_at = now(), \
                       approved_by = null, approved_revision = null, approved_at = null, \
                       updated_at = now() \
                     where id = $1 and revision = $2 and state = any($3::text[]) returning true"
                ),
                &[
                    &id,
                    &revision,
                    &vec![STATE_DRAFT.to_string(), STATE_PREPARED.to_string()],
                ],
            )
            .await?;
        if owned.is_none() {
            return Err(self.why(id, revision, "be prepared").await);
        }
        // Every row this campaign holds for an address the audience names goes, test rows included:
        // one row carries one address, and the audience is what the approval counts.
        transaction
            .execute(
                &format!(
                    "delete from {RECIPIENT_TABLE} where campaign_id = $1 \
                       and (not test or email = any($2::text[]))"
                ),
                &[&id, &emails],
            )
            .await?;
        transaction
            .execute(
                &format!(
                    "insert into {RECIPIENT_TABLE} (campaign_id, email, member_id, state) \
                     select $1, email, member_id, '{SEND_PENDING}' \
                     from unnest($2::text[], $3::uuid[]) as frozen(email, member_id) \
                     on conflict (campaign_id, email) do nothing"
                ),
                &[&id, &emails, &members],
            )
            .await?;
        transaction.commit().await?;
        self.preview(id).await
    }

    /// The approval names the revision and the count it was granted over. A list that moved between
    /// the operator reading it and pressing the button no longer matches, and the approval fails.
    pub async fn approve(
        &self,
        id: Uuid,
        revision: i32,
        count: i64,
        operator: &str,
    ) -> Result<Campaign, CampaignError> {
        let frozen = self.frozen_count(id).await?;
        if frozen != count {
            return Err(CampaignError::Refused(format!(
                "the campaign holds {frozen} recipients, not {count}"
            )));
        }
        self.advance(
            id,
            revision,
            &[STATE_PREPARED],
            "be approved",
            &format!(
                "state = '{STATE_APPROVED}', approved_by = $4, approved_revision = revision, \
                 approved_at = now()"
            ),
            &[&operator],
        )
        .await
    }

    /// Send now is this with `at` already past. The worker is what starts the campaign, so the
    /// HTTP request records the intent and nothing leaves the process on its thread.
    pub async fn schedule(
        &self,
        id: Uuid,
        revision: i32,
        at: DateTime<Utc>,
    ) -> Result<Campaign, CampaignError> {
        self.advance(
            id,
            revision,
            &[STATE_APPROVED],
            "be scheduled",
            "scheduled_at = $4",
            &[&at],
        )
        .await
    }

    /// A send that started cannot be recalled, so only the rows that never left are stopped.
    pub async fn cancel(&self, id: Uuid, revision: i32) -> Result<Campaign, CampaignError> {
        let mut connection = self.pool.get().await?;
        let transaction = connection.transaction().await?;
        let row = transaction
            .query_opt(
                &format!(
                    "update {TABLE} set state = '{STATE_CANCELLED}', cancelled_at = now(), \
                       updated_at = now() \
                     where id = $1 and revision = $2 and state = any($3::text[]) \
                     returning {COLUMNS}"
                ),
                &[
                    &id,
                    &revision,
                    &vec![
                        STATE_DRAFT.to_string(),
                        STATE_PREPARED.to_string(),
                        STATE_APPROVED.to_string(),
                        STATE_SENDING.to_string(),
                    ],
                ],
            )
            .await?;
        let Some(row) = row else {
            return Err(self.why(id, revision, "be cancelled").await);
        };
        transaction
            .execute(
                &format!(
                    "update {RECIPIENT_TABLE} set state = '{SEND_CANCELLED}', updated_at = now() \
                     where campaign_id = $1 and state = '{SEND_PENDING}'"
                ),
                &[&id],
            )
            .await?;
        transaction.commit().await?;
        Ok(campaign(&row))
    }

    /// One row for the signed-in operator, sent whatever state the campaign is in, and never
    /// counted as part of the audience. An address the frozen audience already holds is refused
    /// rather than converted: rewriting that row would drop a member from the campaign, and after
    /// the send would erase the message id its delivery events are keyed by.
    pub async fn test(
        &self,
        id: Uuid,
        revision: i32,
        operator: &str,
    ) -> Result<Campaign, CampaignError> {
        let (address, _) =
            normalize_email(operator).map_err(|error| CampaignError::Refused(error.to_string()))?;
        let held = self.read(id).await?;
        if held.revision != revision {
            return Err(CampaignError::Stale {
                id,
                held: held.revision,
                stated: revision,
            });
        }
        let connection = self.pool.get().await?;
        let armed = connection
            .query_opt(
                &format!(
                    "insert into {RECIPIENT_TABLE} (campaign_id, email, test, state) \
                     values ($1, $2, true, '{SEND_PENDING}') \
                     on conflict (campaign_id, email) do update \
                       set state = '{SEND_PENDING}', delivery = null, \
                           attempted_at = null, ses_message_id = null, last_error = null, \
                           worker_id = null, claim_expires_at = null, updated_at = now() \
                     where {RECIPIENT_TABLE}.test \
                     returning true"
                ),
                &[&id, &address],
            )
            .await?;
        if armed.is_none() {
            return Err(CampaignError::Refused(format!(
                "{address} is already in this campaign's audience, so it receives the campaign \
                 itself"
            )));
        }
        Ok(held)
    }

    pub async fn read(&self, id: Uuid) -> Result<Campaign, CampaignError> {
        read_campaign(&self.pool, id).await
    }

    pub async fn preview(&self, id: Uuid) -> Result<Preview, CampaignError> {
        let held = self.read(id).await?;
        let frozen = held.state != STATE_DRAFT;
        let counts = self.counts(id).await?;
        let exclusions = match frozen {
            true => Exclusions::default(),
            false => self.audience(&held).await?.exclusions,
        };
        let connection = self.pool.get().await?;
        let sample = connection
            .query(
                &format!(
                    "select email from {RECIPIENT_TABLE} where campaign_id = $1 and not test \
                     order by email limit $2"
                ),
                &[&id, &SAMPLE_RECIPIENTS],
            )
            .await?
            .iter()
            .map(|row| row.get::<_, String>("email"))
            .collect();
        let message = campaign_message(&held, &self.apex_host);
        Ok(Preview {
            campaign: held,
            message,
            counts,
            exclusions,
            sample,
            frozen,
        })
    }

    async fn counts(&self, id: Uuid) -> Result<Counts, CampaignError> {
        let connection = self.pool.get().await?;
        let row = connection
            .query_one(
                &format!(
                    "select \
                       count(*) filter (where not test) as audience, \
                       count(*) filter (where not test and state = '{SEND_SENT}') as submitted, \
                       count(*) filter (where not test and delivery = '{DELIVERED}') as delivered, \
                       count(*) filter (where not test and delivery = '{DELAYED}') as delayed, \
                       count(*) filter (where not test and delivery = '{BOUNCED}') as bounced, \
                       count(*) filter (where not test and delivery = '{COMPLAINED}') as complained, \
                       count(*) filter (where not test and delivery = '{UNSUBSCRIBED}') \
                         as unsubscribed, \
                       count(*) filter (where not test and state = '{SEND_FAILED}') as failed, \
                       count(*) filter (where not test and state = '{SEND_CANCELLED}') as cancelled, \
                       count(*) filter (where test) as tests \
                     from {RECIPIENT_TABLE} where campaign_id = $1"
                ),
                &[&id],
            )
            .await?;
        Ok(Counts {
            audience: row.get("audience"),
            submitted: row.get("submitted"),
            delivered: row.get("delivered"),
            delayed: row.get("delayed"),
            bounced: row.get("bounced"),
            complained: row.get("complained"),
            unsubscribed: row.get("unsubscribed"),
            failed: row.get("failed"),
            cancelled: row.get("cancelled"),
            tests: row.get("tests"),
        })
    }

    /// Every seated member the fleet holds, minus the addresses this campaign must not reach. The
    /// walk is the whole member table, so it is an operator act and never a background sweep.
    async fn audience(&self, held: &Campaign) -> Result<Audience, CampaignError> {
        let suppressed = self.suppressed().await?;
        let mut seen: HashSet<String> = HashSet::new();
        let mut seated: Vec<(String, Uuid)> = Vec::new();
        let mut exclusions = Exclusions::default();
        let mut after: Option<SeatedMember> = None;
        loop {
            let page = self.core.recipients(after.as_ref()).await?;
            let Some(last) = page.last().cloned() else {
                return Ok(Audience { seated, exclusions });
            };
            for member in page {
                let Ok((address, domain)) = normalize_email(&member.email) else {
                    continue;
                };
                if (domain == OPERATOR_EMAIL_DOMAIN) != (held.audience == AUDIENCE_OPERATORS) {
                    exclusions.operator += 1;
                    continue;
                }
                if !seen.insert(address.clone()) {
                    exclusions.duplicate += 1;
                    continue;
                }
                if suppressed.contains(&address) {
                    exclusions.suppressed += 1;
                    continue;
                }
                seated.push((address, member.member_id));
            }
            after = Some(last);
        }
    }

    /// Ours and SES's, unioned: SES is the gate that refuses the send, and the feedback consumer
    /// records the hosted page's opt-out before SES's own list is paged again.
    async fn suppressed(&self) -> Result<HashSet<String>, CampaignError> {
        let connection = self.pool.get().await?;
        let states: Vec<String> = SUPPRESSED.iter().map(|state| state.to_string()).collect();
        let rows = connection
            .query(
                &format!(
                    "select distinct email from {RECIPIENT_TABLE} where delivery = any($1) \
                     union select email from {PREFERENCE_TABLE} where topic = $2"
                ),
                &[&states, &FOUNDER_UPDATES],
            )
            .await?;
        let mut suppressed: HashSet<String> = rows
            .iter()
            .map(|row| row.get::<_, String>("email"))
            .collect();
        suppressed.extend(
            self.sender
                .opted_out()
                .await
                .map_err(|error| CampaignError::Contacts(error.to_string()))?,
        );
        Ok(suppressed)
    }

    async fn frozen_count(&self, id: Uuid) -> Result<i64, CampaignError> {
        let connection = self.pool.get().await?;
        Ok(connection
            .query_one(
                &format!(
                    "select count(*) from {RECIPIENT_TABLE} where campaign_id = $1 and not test"
                ),
                &[&id],
            )
            .await?
            .get(0))
    }

    async fn advance(
        &self,
        id: Uuid,
        revision: i32,
        from: &[&str],
        act: &'static str,
        assignment: &str,
        values: &[&(dyn tokio_postgres::types::ToSql + Sync)],
    ) -> Result<Campaign, CampaignError> {
        let connection = self.pool.get().await?;
        let states: Vec<String> = from.iter().map(|state| state.to_string()).collect();
        let mut parameters: Vec<&(dyn tokio_postgres::types::ToSql + Sync)> =
            vec![&id, &revision, &states];
        parameters.extend_from_slice(values);
        let row = connection
            .query_opt(
                &format!(
                    "update {TABLE} set {assignment}, updated_at = now() \
                     where id = $1 and revision = $2 and state = any($3::text[]) \
                     returning {COLUMNS}"
                ),
                &parameters,
            )
            .await?;
        match row {
            Some(row) => Ok(campaign(&row)),
            None => Err(self.why(id, revision, act).await),
        }
    }

    /// A refused mutation says which of the three reasons it was, so the HUD states it rather than
    /// re-deriving it from an empty result.
    async fn why(&self, id: Uuid, revision: i32, act: &'static str) -> CampaignError {
        match self.read(id).await {
            Err(error) => error,
            Ok(held) if held.revision != revision => CampaignError::Stale {
                id,
                held: held.revision,
                stated: revision,
            },
            Ok(held) => CampaignError::State {
                state: held.state,
                act,
            },
        }
    }
}
