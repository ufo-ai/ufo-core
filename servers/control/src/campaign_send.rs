use deadpool_postgres::Pool;
use uuid::Uuid;

use crate::campaign::{
    campaign_message, read_campaign, Campaign, CampaignError, RECIPIENT_TABLE, SEND_ATTEMPTED,
    SEND_FAILED, SEND_PENDING, SEND_SENT, STATE_APPROVED, STATE_COMPLETED, STATE_SENDING, TABLE,
};
use crate::email::{
    verdict, AwsError, FounderSender, SendVerdict, Sender, MAX_RETRY_AFTER_SECONDS,
};

pub const POLL_INTERVAL_SECONDS: u64 = 5;
pub const LEASE_SECONDS: i64 = 120;
pub const ERROR_CHARS: usize = 500;

/// A row nothing can decide fails rather than retrying: a duplicate campaign email is worse than a
/// missing one, and the campaign completes instead of waiting on an answer that is not coming.
const UNANSWERED: &str = "the send was attempted and no answer was recorded";

#[derive(Debug, Clone)]
struct Claimed {
    campaign_id: Uuid,
    email: String,
}

#[derive(Clone)]
pub struct CampaignSends {
    pub pool: Pool,
    pub sender: FounderSender,
    pub apex_host: String,
    pub worker_id: String,
    pub poll_interval: std::time::Duration,
}

impl CampaignSends {
    pub async fn run(self) {
        loop {
            let paused = match self.poll().await {
                Ok(paused) => paused,
                Err(error) => {
                    tracing::error!(
                        target: "ufo_control::campaign_send",
                        "campaign_send.sweep.failed {error}"
                    );
                    Some(self.poll_interval)
                }
            };
            if let Some(paused) = paused {
                tokio::time::sleep(paused).await;
            }
        }
    }

    /// How long to wait before looking again: nothing when a row was just sent, because the next one
    /// is already claimable.
    pub async fn poll(&self) -> Result<Option<std::time::Duration>, CampaignError> {
        self.start_due().await?;
        self.abandon_unanswered().await?;
        let Some(claimed) = self.claim().await? else {
            self.complete_drained().await?;
            return Ok(Some(self.poll_interval));
        };
        let held = match read_campaign(&self.pool, claimed.campaign_id).await {
            Ok(held) => held,
            Err(error) => {
                self.rearm(&claimed).await?;
                return Err(error);
            }
        };
        // A frozen sender the deploy no longer configures fails the row. Putting it back would set
        // it at the head of the claim order again every poll, which stalls every other campaign.
        let Some(from) = self
            .sender
            .senders
            .iter()
            .find(|sender| sender.label == held.sender)
        else {
            let stated = format!("{:?} is no longer a configured sender", held.sender);
            self.write(
                &claimed,
                &format!(
                    "state = '{SEND_FAILED}', worker_id = null, claim_expires_at = null, \
                     last_error = $4"
                ),
                &[&stated],
            )
            .await?;
            tracing::error!(
                target: "ufo_control::campaign_send",
                "campaign_send.sender_absent campaign={} {stated}", claimed.campaign_id
            );
            return Ok(None);
        };
        match self.deliver(&claimed, &held, from).await {
            Ok(()) => Ok(None),
            Err(Carried::Ledger(error)) => Err(error),
            Err(Carried::Aws(error)) => self.settle(&claimed, error).await,
        }
    }

    async fn start_due(&self) -> Result<(), CampaignError> {
        let connection = self.pool.get().await?;
        connection
            .execute(
                &format!(
                    "update {TABLE} set state = '{STATE_SENDING}', started_at = now(), \
                       updated_at = now() \
                     where state = '{STATE_APPROVED}' and scheduled_at is not null \
                       and scheduled_at <= now()"
                ),
                &[],
            )
            .await?;
        Ok(())
    }

    async fn abandon_unanswered(&self) -> Result<(), CampaignError> {
        let connection = self.pool.get().await?;
        let abandoned = connection
            .execute(
                &format!(
                    "update {RECIPIENT_TABLE} set state = '{SEND_FAILED}', last_error = $1, \
                       worker_id = null, claim_expires_at = null, updated_at = now() \
                     where state = '{SEND_ATTEMPTED}' and claim_expires_at <= now()"
                ),
                &[&UNANSWERED],
            )
            .await?;
        if abandoned > 0 {
            tracing::error!(
                target: "ufo_control::campaign_send",
                "campaign_send.unanswered rows={abandoned}"
            );
        }
        Ok(())
    }

    /// The attempt is marked in the same statement that claims the row, so no path exists on which
    /// SES is called before the ledger says it was.
    async fn claim(&self) -> Result<Option<Claimed>, CampaignError> {
        let connection = self.pool.get().await?;
        let row = connection
            .query_opt(
                &format!(
                    "with candidate as (\
                       select r.campaign_id, r.email from {RECIPIENT_TABLE} r \
                       join {TABLE} c on c.id = r.campaign_id \
                       where r.state = '{SEND_PENDING}' \
                         and (r.test or c.state = '{STATE_SENDING}') \
                       order by r.created_at, r.email \
                       for update of r skip locked limit 1) \
                     update {RECIPIENT_TABLE} r \
                     set state = '{SEND_ATTEMPTED}', attempted_at = now(), worker_id = $1, \
                         claim_expires_at = now() + $2::text::interval, updated_at = now() \
                     from candidate c \
                     where r.campaign_id = c.campaign_id and r.email = c.email \
                     returning r.campaign_id, r.email"
                ),
                &[&self.worker_id, &format!("{LEASE_SECONDS} seconds")],
            )
            .await?;
        Ok(row.map(|row| Claimed {
            campaign_id: row.get("campaign_id"),
            email: row.get("email"),
        }))
    }

    async fn deliver(
        &self,
        claimed: &Claimed,
        held: &Campaign,
        from: &Sender,
    ) -> Result<(), Carried> {
        let message = campaign_message(held, &self.apex_host);
        let message_id = self
            .sender
            .send(
                from,
                &claimed.email,
                &message.subject,
                &message.text,
                &message.html,
            )
            .await
            .map_err(Carried::Aws)?;
        self.write(
            claimed,
            &format!(
                "state = '{SEND_SENT}', ses_message_id = $4, worker_id = null, \
                 claim_expires_at = null, last_error = null"
            ),
            &[&message_id],
        )
        .await?;
        tracing::info!(
            target: "ufo_control::campaign_send",
            "campaign_send.sent campaign={} message={message_id}", claimed.campaign_id
        );
        Ok(())
    }

    /// A refusal that provably never reached SES puts the row back and pauses the loop; repeating
    /// it duplicates nothing. A terminal refusal, and an unanswered send, fail the row for good.
    async fn settle(
        &self,
        claimed: &Claimed,
        error: AwsError,
    ) -> Result<Option<std::time::Duration>, CampaignError> {
        let stated = clipped(&error.to_string());
        let SendVerdict::Transient {
            message,
            retry_after,
        } = verdict(error)
        else {
            self.write(
                claimed,
                &format!(
                    "state = '{SEND_FAILED}', worker_id = null, claim_expires_at = null, \
                     last_error = $4"
                ),
                &[&stated],
            )
            .await?;
            tracing::error!(
                target: "ufo_control::campaign_send",
                "campaign_send.failed campaign={} {stated}", claimed.campaign_id
            );
            return Ok(None);
        };
        self.write(
            claimed,
            &format!(
                "state = '{SEND_PENDING}', attempted_at = null, worker_id = null, \
                 claim_expires_at = null, last_error = $4"
            ),
            &[&clipped(&message)],
        )
        .await?;
        let delay = std::time::Duration::from_secs_f64(
            retry_after
                .unwrap_or(POLL_INTERVAL_SECONDS as f64)
                .clamp(0.0, MAX_RETRY_AFTER_SECONDS),
        );
        tracing::warn!(
            target: "ufo_control::campaign_send",
            "campaign_send.requeued campaign={} in={}s {message}",
            claimed.campaign_id, delay.as_secs()
        );
        Ok(Some(delay))
    }

    async fn complete_drained(&self) -> Result<(), CampaignError> {
        let connection = self.pool.get().await?;
        connection
            .execute(
                &format!(
                    "update {TABLE} c set state = '{STATE_COMPLETED}', completed_at = now(), \
                       updated_at = now() \
                     where c.state = '{STATE_SENDING}' and not exists (\
                       select 1 from {RECIPIENT_TABLE} r where r.campaign_id = c.id \
                         and not r.test \
                         and r.state in ('{SEND_PENDING}', '{SEND_ATTEMPTED}'))"
                ),
                &[],
            )
            .await?;
        Ok(())
    }

    async fn rearm(&self, claimed: &Claimed) -> Result<(), CampaignError> {
        self.write(
            claimed,
            &format!(
                "state = '{SEND_PENDING}', attempted_at = null, worker_id = null, \
                 claim_expires_at = null"
            ),
            &[],
        )
        .await
    }

    async fn write(
        &self,
        claimed: &Claimed,
        assignment: &str,
        values: &[&(dyn tokio_postgres::types::ToSql + Sync)],
    ) -> Result<(), CampaignError> {
        let connection = self.pool.get().await?;
        let mut parameters: Vec<&(dyn tokio_postgres::types::ToSql + Sync)> =
            vec![&claimed.campaign_id, &claimed.email, &self.worker_id];
        parameters.extend_from_slice(values);
        connection
            .execute(
                &format!(
                    "update {RECIPIENT_TABLE} set {assignment}, updated_at = now() \
                     where campaign_id = $1 and email = $2 and worker_id = $3"
                ),
                &parameters,
            )
            .await?;
        Ok(())
    }
}

fn clipped(message: &str) -> String {
    message.chars().take(ERROR_CHARS).collect()
}

/// The two ways a delivery ends badly, and they mean opposite things. `Aws` is SES refusing, which
/// the row records itself. `Ledger` is the writeback failing after SES accepted, which nothing here
/// can record — the lease expires and `abandon_unanswered` marks it.
#[derive(Debug, thiserror::Error)]
enum Carried {
    #[error(transparent)]
    Ledger(#[from] CampaignError),
    #[error(transparent)]
    Aws(AwsError),
}
