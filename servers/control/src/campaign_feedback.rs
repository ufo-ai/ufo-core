use std::path::PathBuf;

use chrono::{DateTime, Utc};
use deadpool_postgres::Pool;
use serde::Deserialize;
use uuid::Uuid;

use crate::campaign::{
    CampaignError, BOUNCED, COMPLAINED, DELAYED, DELIVERED, EVENT_TABLE, RECIPIENT_TABLE, REJECTED,
    RENDERING_FAILED, SUPPRESSED, UNSUBSCRIBED,
};
use crate::email::{
    assume_role, signed_post, AwsCall, AwsError, SesCredentials, AWS_TIMEOUT_SECONDS, SQS_SERVICE,
};

pub const POLL_INTERVAL_SECONDS: u64 = 20;
pub const WAIT_TIME_SECONDS: u32 = 20;
pub const RECEIVE_BATCH: u32 = 10;

const SQS_CONTENT_TYPE: &str = "application/x-amz-json-1.0";
const SQS_TARGET_PREFIX: &str = "AmazonSQS.";
const RECEIVE_TARGET: &str = "AmazonSQS.ReceiveMessage";
const DELETE_TARGET: &str = "AmazonSQS.DeleteMessageBatch";

const PERMANENT_BOUNCE: &str = "Permanent";
const OPT_OUT: &str = "OPT_OUT";

/// The SES `eventType` values this deploy publishes. `Rendering Failure` carries a space, which is
/// why the mapping matches on the wire string rather than on a name of ours.
const SEND: &str = "Send";
const DELIVERY: &str = "Delivery";
const DELIVERY_DELAY: &str = "DeliveryDelay";
const BOUNCE: &str = "Bounce";
const COMPLAINT: &str = "Complaint";
const REJECT: &str = "Reject";
const RENDERING_FAILURE: &str = "Rendering Failure";
const SUBSCRIPTION: &str = "Subscription";

#[derive(Debug, Clone)]
pub struct QueueMessage {
    pub receipt: String,
    pub body: String,
}

#[derive(Debug, Clone)]
pub struct FeedbackQueue {
    pub url: String,
    pub region: String,
    pub role_arn: String,
    pub token_file: PathBuf,
    pub sts: String,
}

impl FeedbackQueue {
    pub async fn receive(&self) -> Result<(SesCredentials, Vec<QueueMessage>), AwsError> {
        let credentials = assume_role(&self.sts, &self.role_arn, &self.token_file).await?;
        let body = serde_json::json!({
            "QueueUrl": self.url,
            "MaxNumberOfMessages": RECEIVE_BATCH,
            "WaitTimeSeconds": WAIT_TIME_SECONDS,
        })
        .to_string()
        .into_bytes();
        let answered = signed_post(
            &self.call(
                RECEIVE_TARGET,
                WAIT_TIME_SECONDS as u64 + AWS_TIMEOUT_SECONDS,
            ),
            body,
            &self.region,
            &credentials,
        )
        .await?;
        let received: Received =
            serde_json::from_str(&answered).map_err(|_| AwsError::Unreadable {
                service: SQS_SERVICE,
                field: "Messages",
                body: answered.chars().take(1000).collect(),
            })?;
        Ok((
            credentials,
            received
                .messages
                .into_iter()
                .map(|message| QueueMessage {
                    receipt: message.receipt_handle,
                    body: message.body,
                })
                .collect(),
        ))
    }

    pub async fn delete(
        &self,
        credentials: &SesCredentials,
        receipts: &[String],
    ) -> Result<(), AwsError> {
        if receipts.is_empty() {
            return Ok(());
        }
        let entries: Vec<serde_json::Value> = receipts
            .iter()
            .enumerate()
            .map(|(index, receipt)| serde_json::json!({"Id": index.to_string(), "ReceiptHandle": receipt}))
            .collect();
        let body = serde_json::json!({"QueueUrl": self.url, "Entries": entries})
            .to_string()
            .into_bytes();
        signed_post(
            &self.call(DELETE_TARGET, AWS_TIMEOUT_SECONDS),
            body,
            &self.region,
            credentials,
        )
        .await?;
        Ok(())
    }

    fn call(&self, target: &'static str, timeout_seconds: u64) -> AwsCall<'_> {
        AwsCall {
            service: SQS_SERVICE,
            operation: target.trim_start_matches(SQS_TARGET_PREFIX),
            url: &self.url,
            content_type: SQS_CONTENT_TYPE,
            target: Some(target),
            timeout_seconds,
        }
    }
}

#[derive(Deserialize)]
struct Received {
    #[serde(rename = "Messages", default)]
    messages: Vec<ReceivedMessage>,
}

#[derive(Deserialize)]
struct ReceivedMessage {
    #[serde(rename = "ReceiptHandle")]
    receipt_handle: String,
    #[serde(rename = "Body")]
    body: String,
}

#[derive(Deserialize)]
struct SesEvent {
    #[serde(rename = "eventType")]
    event_type: String,
    mail: SesMail,
    bounce: Option<SesBounce>,
    complaint: Option<SesStamped>,
    delivery: Option<SesStamped>,
    #[serde(rename = "deliveryDelay")]
    delivery_delay: Option<SesStamped>,
    subscription: Option<SesSubscription>,
}

#[derive(Deserialize)]
struct SesMail {
    timestamp: DateTime<Utc>,
    #[serde(rename = "messageId")]
    message_id: String,
    #[serde(default)]
    destination: Vec<String>,
}

#[derive(Deserialize)]
struct SesBounce {
    #[serde(rename = "bounceType")]
    bounce_type: String,
    timestamp: Option<DateTime<Utc>>,
}

#[derive(Deserialize)]
struct SesStamped {
    timestamp: Option<DateTime<Utc>>,
}

#[derive(Deserialize)]
struct SesSubscription {
    timestamp: Option<DateTime<Utc>>,
    #[serde(rename = "newTopicPreferences")]
    new_topic_preferences: Option<TopicPreferences>,
}

#[derive(Deserialize)]
struct TopicPreferences {
    #[serde(rename = "unsubscribeAll")]
    unsubscribe_all: Option<bool>,
    #[serde(rename = "topicSubscriptionStatus", default)]
    topic_subscription_status: Vec<TopicStatus>,
}

#[derive(Deserialize)]
struct TopicStatus {
    #[serde(rename = "subscriptionStatus")]
    subscription_status: String,
}

impl SesEvent {
    fn occurred_at(&self) -> DateTime<Utc> {
        let stamped = match self.event_type.as_str() {
            BOUNCE => self.bounce.as_ref().and_then(|bounce| bounce.timestamp),
            COMPLAINT => self.complaint.as_ref().and_then(|part| part.timestamp),
            DELIVERY => self.delivery.as_ref().and_then(|part| part.timestamp),
            DELIVERY_DELAY => self.delivery_delay.as_ref().and_then(|part| part.timestamp),
            SUBSCRIPTION => self.subscription.as_ref().and_then(|part| part.timestamp),
            _ => None,
        };
        stamped.unwrap_or(self.mail.timestamp)
    }

    /// What this event makes true of the recipient, or `None` where it says nothing new — a `Send`
    /// only repeats what the ledger wrote when it recorded the message id.
    fn delivery_state(&self) -> Option<&'static str> {
        match self.event_type.as_str() {
            SEND => None,
            DELIVERY => Some(DELIVERED),
            DELIVERY_DELAY => Some(DELAYED),
            COMPLAINT => Some(COMPLAINED),
            REJECT => Some(REJECTED),
            RENDERING_FAILURE => Some(RENDERING_FAILED),
            BOUNCE => match self
                .bounce
                .as_ref()
                .map(|bounce| bounce.bounce_type.as_str())
            {
                Some(PERMANENT_BOUNCE) => Some(BOUNCED),
                _ => Some(DELAYED),
            },
            SUBSCRIPTION => self.opted_out().then_some(UNSUBSCRIBED),
            _ => None,
        }
    }

    fn opted_out(&self) -> bool {
        let Some(preferences) = self
            .subscription
            .as_ref()
            .and_then(|part| part.new_topic_preferences.as_ref())
        else {
            return false;
        };
        preferences.unsubscribe_all.unwrap_or(false)
            || preferences
                .topic_subscription_status
                .iter()
                .any(|topic| topic.subscription_status == OPT_OUT)
    }
}

#[derive(Clone)]
pub struct CampaignFeedback {
    pub pool: Pool,
    pub queue: FeedbackQueue,
    pub poll_interval: std::time::Duration,
}

impl CampaignFeedback {
    pub async fn run(self) {
        loop {
            if let Err(error) = self.poll().await {
                tracing::error!(
                    target: "ufo_control::campaign_feedback",
                    "campaign_feedback.poll.failed {error}"
                );
                tokio::time::sleep(self.poll_interval).await;
            }
        }
    }

    /// A message this cannot decode is left on the queue on purpose: five receives park it in the
    /// dead-letter queue for an operator to read, rather than deleting a delivery fact unseen.
    pub async fn poll(&self) -> Result<(), FeedbackError> {
        let (credentials, messages) = self.queue.receive().await?;
        let mut done: Vec<String> = Vec::new();
        for message in messages {
            match serde_json::from_str::<SesEvent>(&message.body) {
                Ok(event) => {
                    self.record(&event, &message.body).await?;
                    done.push(message.receipt);
                }
                Err(error) => tracing::error!(
                    target: "ufo_control::campaign_feedback",
                    "campaign_feedback.undecodable {error}"
                ),
            }
        }
        self.queue.delete(&credentials, &done).await?;
        Ok(())
    }

    async fn record(&self, event: &SesEvent, raw: &str) -> Result<(), FeedbackError> {
        let Some(email) = event
            .mail
            .destination
            .first()
            .map(|address| address.trim().to_lowercase())
        else {
            return Ok(());
        };
        let connection = self.pool.get().await?;
        let campaign_id: Option<Uuid> = connection
            .query_opt(
                &format!(
                    "select campaign_id from {RECIPIENT_TABLE} \
                     where ses_message_id = $1 and email = $2"
                ),
                &[&event.mail.message_id, &email],
            )
            .await?
            .map(|row| row.get("campaign_id"));
        connection
            .execute(
                &format!(
                    "insert into {EVENT_TABLE} \
                       (id, campaign_id, email, ses_message_id, event_type, occurred_at, payload) \
                     values ($1, $2, $3, $4, $5, $6, $7) \
                     on conflict (ses_message_id, event_type, occurred_at) do nothing"
                ),
                &[
                    &Uuid::new_v4(),
                    &campaign_id,
                    &email,
                    &event.mail.message_id,
                    &event.event_type,
                    &event.occurred_at(),
                    &serde_json::from_str::<serde_json::Value>(raw)
                        .unwrap_or_else(|_| serde_json::json!({})),
                ],
            )
            .await?;
        let Some(delivery) = event.delivery_state() else {
            return Ok(());
        };
        let suppressed: Vec<String> = SUPPRESSED.iter().map(|state| state.to_string()).collect();
        connection
            .execute(
                &format!(
                    "update {RECIPIENT_TABLE} set delivery = $3, updated_at = now() \
                     where ses_message_id = $1 and email = $2 \
                       and (delivery is null or delivery <> all($4::text[]) \
                            or $3 = any($4::text[]))"
                ),
                &[&event.mail.message_id, &email, &delivery, &suppressed],
            )
            .await?;
        Ok(())
    }
}

#[derive(Debug, thiserror::Error)]
pub enum FeedbackError {
    #[error(transparent)]
    Queue(#[from] AwsError),
    #[error(transparent)]
    Ledger(#[from] CampaignError),
    #[error(transparent)]
    Pool(#[from] deadpool_postgres::PoolError),
    #[error(transparent)]
    Query(#[from] tokio_postgres::Error),
}
