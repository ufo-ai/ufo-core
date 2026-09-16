mod harness;

use std::path::PathBuf;

use deadpool_postgres::Pool;
use harness::{ledger_pool, spawn_http, Exchange};
use ufo_control::campaign_feedback::{CampaignFeedback, FeedbackQueue};
use ufo_control::email::{AwsEndpoints, EmailSender, SesEmailSender};
use ufo_control::email_send::{
    Asked, EmailSends, Preference, SendError, FOUNDER_UPDATES, PREFERENCE_TABLE, PRODUCT_NEWS,
    SILENCEABLE, TABLE, TRANSACTIONAL,
};
use ufo_control::message::UNSUBSCRIBE_PLACEHOLDER;

const STS_RESPONSE: &str = r#"<AssumeRoleWithWebIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">
  <AssumeRoleWithWebIdentityResult>
    <Credentials>
      <AccessKeyId>ASIAEXAMPLE</AccessKeyId>
      <SecretAccessKey>secret</SecretAccessKey>
      <SessionToken>token</SessionToken>
      <Expiration>2026-07-10T13:00:00Z</Expiration>
    </Credentials>
  </AssumeRoleWithWebIdentityResult>
</AssumeRoleWithWebIdentityResponse>"#;

const CONFIGURATION_SET: &str = "ufo-testing-transactional";
const APEX: &str = "ufo.ai";
const CONTACT_LIST: &str = "ufo-users";
const SES_PRODUCT_TOPIC: &str = "ufo-testing-product-news";
const SES_FOUNDER_TOPIC: &str = "ufo-testing-founder-updates";

fn asked(email: &str) -> Asked {
    Asked {
        email: email.to_string(),
        kind: "balance_exhausted".to_string(),
        topic: TRANSACTIONAL.to_string(),
        subject: "acme.com is out of credit".to_string(),
        body: "acme.com has no credit left, so the agent has stopped answering.".to_string(),
        action_label: Some("Add credit".to_string()),
        action_url: Some("https://app.ufo.ai/surface/web#/workspace/billing".to_string()),
    }
}

fn held_contact(unsubscribe_all: bool, held: &[(&str, &str)]) -> String {
    serde_json::json!({
        "EmailAddress": "member@acme.com",
        "UnsubscribeAll": unsubscribe_all,
        "TopicDefaultPreferences": [
            {"TopicName": SES_PRODUCT_TOPIC, "SubscriptionStatus": "OPT_IN"},
            {"TopicName": SES_FOUNDER_TOPIC, "SubscriptionStatus": "OPT_IN"},
        ],
        "TopicPreferences": held
            .iter()
            .map(|(topic, status)| serde_json::json!({
                "TopicName": topic,
                "SubscriptionStatus": status,
            }))
            .collect::<Vec<_>>(),
    })
    .to_string()
}

fn written(put: &Exchange) -> serde_json::Value {
    serde_json::from_str(&put.body).expect("the write is JSON")
}

fn projected_token() -> (tempfile::TempDir, PathBuf) {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("token");
    std::fs::write(&path, "projected-web-identity-token").unwrap();
    (directory, path)
}

fn ses(base: &str, token_file: PathBuf) -> EmailSender {
    EmailSender::Ses(Box::new(SesEmailSender {
        source: "no-reply@flyingobject.ai".to_string(),
        configuration_set: CONFIGURATION_SET.to_string(),
        contact_list: CONTACT_LIST.to_string(),
        region: "us-east-1".to_string(),
        role_arn: "arn:aws:iam::111122223333:role/ufo-testing-gateway-ses".to_string(),
        token_file,
        endpoints: AwsEndpoints {
            ses: base.to_string(),
            sts: format!("{base}/"),
        },
    }))
}

async fn recorded(pool: &Pool, message_id: &str) -> Option<(String, String, Option<String>)> {
    let connection = pool.get().await.unwrap();
    connection
        .query_opt(
            &format!("select kind, email, delivery from {TABLE} where ses_message_id = $1"),
            &[&message_id],
        )
        .await
        .unwrap()
        .map(|row| (row.get("kind"), row.get("email"), row.get("delivery")))
}

async fn silenced_topics(pool: &Pool, email: &str) -> Vec<String> {
    let connection = pool.get().await.unwrap();
    let mut topics: Vec<String> = connection
        .query(
            &format!("select topic from {PREFERENCE_TABLE} where email = $1"),
            &[&email],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| row.get::<_, String>("topic"))
        .collect();
    topics.sort();
    topics
}

async fn drain_feedback(pool: &Pool, token_file: PathBuf, event: &str) {
    let (queue, _log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (
            200,
            serde_json::json!({"Messages": [{"MessageId": "1", "ReceiptHandle": "r1", "Body": event}]})
                .to_string(),
        ),
        (200, "{}".to_string()),
    ])
    .await;
    CampaignFeedback {
        pool: pool.clone(),
        queue: FeedbackQueue {
            url: format!("{queue}/111122223333/ufo-testing-founder-email-feedback"),
            region: "us-east-1".to_string(),
            role_arn: "arn:aws:iam::111122223333:role/ufo-testing-gateway-ses".to_string(),
            token_file,
            sts: format!("{queue}/"),
        },
        poll_interval: std::time::Duration::from_millis(1),
        topics: std::collections::BTreeMap::from([(
            SES_FOUNDER_TOPIC.to_string(),
            FOUNDER_UPDATES.to_string(),
        )]),
    }
    .poll()
    .await
    .unwrap();
}

#[tokio::test]
async fn one_send_is_recorded_and_its_delivery_is_read_back() {
    let pool = ledger_pool().await;
    let (_directory, token_file) = projected_token();
    let (aws, log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, r#"{"MessageId": "message-one"}"#.to_string()),
    ])
    .await;
    let sends = EmailSends {
        pool: pool.clone(),
        sender: ses(&aws, token_file.clone()),
        apex_host: APEX.to_string(),
        product_topic: Some(SES_PRODUCT_TOPIC.to_string()),
    };

    let sent = sends.send(asked("Member@Acme.com")).await.unwrap();
    assert_eq!(sent.message_id, "message-one");
    assert_eq!(
        recorded(&pool, "message-one").await,
        Some((
            "balance_exhausted".to_string(),
            "member@acme.com".to_string(),
            None
        )),
        "the address is recorded normalized, with no delivery until SES reports one"
    );
    assert_eq!(
        sends.delivery("message-one").await.unwrap().delivery,
        None,
        "a send SES has not reported on yet reads as undelivered, not as absent"
    );

    let outbound: Vec<Exchange> = log.lock().unwrap().drain(..).collect();
    let body = &outbound.last().expect("SES was called").body;
    assert!(
        body.contains(CONFIGURATION_SET),
        "the send names the configuration set that publishes its delivery events: {body}"
    );

    let delivered = r#"{"eventType": "Delivery", "mail": {"timestamp": "2026-07-10T12:00:00.000Z", "messageId": "message-one", "destination": ["member@acme.com"]}, "delivery": {"timestamp": "2026-07-10T12:00:05.000Z"}}"#;
    drain_feedback(&pool, token_file, delivered).await;

    assert_eq!(
        sends.delivery("message-one").await.unwrap().delivery,
        Some("delivered".to_string()),
        "the consumer that reports a campaign reports a one-message send the same way"
    );
}

#[tokio::test]
async fn an_address_that_bounced_is_barred_before_ses_is_called() {
    let pool = ledger_pool().await;
    let (_directory, token_file) = projected_token();
    let (aws, log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, r#"{"MessageId": "message-one"}"#.to_string()),
    ])
    .await;
    let sends = EmailSends {
        pool: pool.clone(),
        sender: ses(&aws, token_file.clone()),
        apex_host: APEX.to_string(),
        product_topic: Some(SES_PRODUCT_TOPIC.to_string()),
    };
    sends.send(asked("member@acme.com")).await.unwrap();

    let bounce = r#"{"eventType": "Bounce", "mail": {"timestamp": "2026-07-10T12:00:00.000Z", "messageId": "message-one", "destination": ["member@acme.com"]}, "bounce": {"bounceType": "Permanent", "bounceSubType": "General", "timestamp": "2026-07-10T12:00:05.000Z"}}"#;
    drain_feedback(&pool, token_file, bounce).await;
    log.lock().unwrap().clear();

    let refusal = sends.send(asked("member@acme.com")).await.unwrap_err();
    assert!(
        matches!(refusal, SendError::Suppressed(address) if address == "member@acme.com"),
        "a bounced address bars every kind"
    );
    assert!(
        log.lock().unwrap().is_empty(),
        "the refusal lands before SES is reached"
    );
}

#[tokio::test]
async fn an_address_that_left_a_campaign_still_hears_about_its_own_workspace() {
    let pool = ledger_pool().await;
    let (_directory, token_file) = projected_token();
    let (aws, log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, r#"{"MessageId": "message-one"}"#.to_string()),
        (200, STS_RESPONSE.to_string()),
        (200, r#"{"MessageId": "message-two"}"#.to_string()),
    ])
    .await;
    let sends = EmailSends {
        pool: pool.clone(),
        sender: ses(&aws, token_file.clone()),
        apex_host: APEX.to_string(),
        product_topic: Some(SES_PRODUCT_TOPIC.to_string()),
    };
    sends.send(asked("member@acme.com")).await.unwrap();

    let unsubscribe = r#"{"eventType": "Subscription", "mail": {"timestamp": "2026-07-10T12:00:00.000Z", "messageId": "message-one", "destination": ["member@acme.com"]}, "subscription": {"timestamp": "2026-07-10T12:00:05.000Z", "newTopicPreferences": {"unsubscribeAll": true}}}"#;
    drain_feedback(&pool, token_file, unsubscribe).await;
    log.lock().unwrap().clear();

    let message_id = sends
        .send(asked("member@acme.com"))
        .await
        .expect("leaving a campaign is a preference about our news, not an unreachable address");
    assert_eq!(message_id.message_id, "message-two");
    assert!(
        !log.lock().unwrap().is_empty(),
        "the message reaches SES rather than being barred"
    );
}

#[tokio::test]
async fn a_member_cannot_lift_the_founder_opt_out_from_chat() {
    let pool = ledger_pool().await;
    let (_directory, token_file) = projected_token();
    let (aws, _log) = spawn_http(Vec::new()).await;
    let sends = EmailSends {
        pool,
        sender: ses(&aws, token_file),
        apex_host: APEX.to_string(),
        product_topic: Some(SES_PRODUCT_TOPIC.to_string()),
    };

    let refusal = sends
        .prefer(Preference {
            email: "member@acme.com".to_string(),
            topic: FOUNDER_UPDATES.to_string(),
            silenced: false,
        })
        .await
        .unwrap_err();
    assert!(
        refusal
            .to_string()
            .contains("is not a topic a member can set"),
        "SES holds that opt-out on its contact list, so lifting our row alone would report a \
         resume that never happens: {refusal}"
    );
}

#[tokio::test]
async fn a_malformed_ask_is_refused_with_the_reason() {
    let pool = ledger_pool().await;
    let (_directory, token_file) = projected_token();
    let (aws, log) = spawn_http(Vec::new()).await;
    let sends = EmailSends {
        pool,
        sender: ses(&aws, token_file),
        apex_host: APEX.to_string(),
        product_topic: Some(SES_PRODUCT_TOPIC.to_string()),
    };

    for (broken, expected) in [
        (
            Asked {
                email: "not-an-address".to_string(),
                ..asked("member@acme.com")
            },
            "malformed",
        ),
        (
            Asked {
                kind: "Balance Exhausted".to_string(),
                ..asked("member@acme.com")
            },
            "send kind",
        ),
        (
            Asked {
                subject: "   ".to_string(),
                ..asked("member@acme.com")
            },
            "subject is required",
        ),
        (
            Asked {
                body: "x".repeat(20_001),
                ..asked("member@acme.com")
            },
            "body is required",
        ),
        (
            Asked {
                action_url: Some("http://app.ufo.ai/billing".to_string()),
                ..asked("member@acme.com")
            },
            "an act is a label",
        ),
        (
            Asked {
                action_label: None,
                ..asked("member@acme.com")
            },
            "an act is a label",
        ),
        (
            asked("swebench@0a1b2c.eval.invalid"),
            "reserved top-level domain",
        ),
        (asked("someone@box.localhost"), "reserved top-level domain"),
    ] {
        let refusal = sends.send(broken).await.unwrap_err();
        let stated = refusal.to_string().to_lowercase();
        assert!(
            stated.contains(expected),
            "{stated:?} does not name {expected:?}"
        );
    }
    assert!(
        log.lock().unwrap().is_empty(),
        "nothing malformed reaches SES"
    );
}

#[tokio::test]
async fn a_silenced_topic_bars_product_news_and_never_the_transactional() {
    let pool = ledger_pool().await;
    let (_directory, token_file) = projected_token();
    let (aws, log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, r#"{"MessageId": "message-one"}"#.to_string()),
        // Asking for product news back lifts the opt-out SES holds before anything is sent again.
        (200, STS_RESPONSE.to_string()),
        (200, held_contact(false, &[]).to_string()),
        (200, "{}".to_string()),
        (200, STS_RESPONSE.to_string()),
        (200, r#"{"MessageId": "message-two"}"#.to_string()),
    ])
    .await;
    let sends = EmailSends {
        pool,
        sender: ses(&aws, token_file),
        apex_host: APEX.to_string(),
        product_topic: Some(SES_PRODUCT_TOPIC.to_string()),
    };

    sends
        .prefer(Preference {
            email: "Member@Acme.com".to_string(),
            topic: PRODUCT_NEWS.to_string(),
            silenced: true,
        })
        .await
        .unwrap();

    let news = Asked {
        kind: "connect_something_reminder".to_string(),
        topic: PRODUCT_NEWS.to_string(),
        ..asked("member@acme.com")
    };
    let refusal = sends.send(news.clone()).await.unwrap_err();
    assert!(
        matches!(refusal, SendError::Silenced { ref topic, .. } if topic == PRODUCT_NEWS),
        "a member who asked to hear nothing more about the product hears nothing more"
    );
    assert!(
        log.lock().unwrap().is_empty(),
        "the refusal lands before SES"
    );

    sends.send(asked("member@acme.com")).await.unwrap();
    assert!(
        !log.lock().unwrap().is_empty(),
        "what the workspace is doing with their money is still theirs to read"
    );

    sends
        .prefer(Preference {
            email: "member@acme.com".to_string(),
            topic: PRODUCT_NEWS.to_string(),
            silenced: false,
        })
        .await
        .unwrap();
    assert!(
        sends.send(news).await.is_ok(),
        "a member can ask for it back"
    );
}

#[tokio::test]
async fn the_transactional_topic_cannot_be_silenced() {
    let pool = ledger_pool().await;
    let (_directory, token_file) = projected_token();
    let (aws, _log) = spawn_http(Vec::new()).await;
    let sends = EmailSends {
        pool,
        sender: ses(&aws, token_file),
        apex_host: APEX.to_string(),
        product_topic: Some(SES_PRODUCT_TOPIC.to_string()),
    };

    for topic in [TRANSACTIONAL, "invented"] {
        let refusal = sends
            .prefer(Preference {
                email: "member@acme.com".to_string(),
                topic: topic.to_string(),
                silenced: true,
            })
            .await
            .unwrap_err();
        assert!(
            matches!(refusal, SendError::Refused(stated) if stated.contains("topic a member can")),
            "{topic} is not a member's to silence"
        );
    }
}

#[tokio::test]
async fn the_message_reaches_ses_drawn_in_the_deploy_frame_and_carries_no_unsubscribe() {
    let pool = ledger_pool().await;
    let (_directory, token_file) = projected_token();
    let (aws, log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, r#"{"MessageId": "message-one"}"#.to_string()),
    ])
    .await;
    let sends = EmailSends {
        pool,
        sender: ses(&aws, token_file),
        apex_host: APEX.to_string(),
        product_topic: Some(SES_PRODUCT_TOPIC.to_string()),
    };

    sends.send(asked("member@acme.com")).await.unwrap();

    let outbound: Vec<Exchange> = log.lock().unwrap().drain(..).collect();
    let body = &outbound.last().expect("SES was called").body;
    let sent: serde_json::Value = serde_json::from_str(body).expect("the send is JSON");
    let content = &sent["Content"]["Simple"];
    assert_eq!(content["Subject"]["Data"], "acme.com is out of credit");
    let text = content["Body"]["Text"]["Data"]
        .as_str()
        .expect("a text body");
    let html = content["Body"]["Html"]["Data"]
        .as_str()
        .expect("an html body");
    assert!(
        text.contains("no credit left") && text.contains("Add credit: https://"),
        "the text rendering states the act as a line the member can copy: {text:?}"
    );
    assert!(
        html.contains("<p style=") && html.contains(">Add credit</a>"),
        "the html rendering is the frame, with the act drawn as a button"
    );
    assert!(
        !text.contains("amazonSESUnsubscribeUrl") && !html.contains("amazonSESUnsubscribeUrl"),
        "a transactional message carries no unsubscribe: SES would leave the braces in it"
    );
}

#[tokio::test]
async fn an_unsubscribe_from_the_founder_topic_bars_that_topic_alone() {
    let pool = ledger_pool().await;
    let (_directory, token_file) = projected_token();
    let (aws, _log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, r#"{"MessageId": "message-one"}"#.to_string()),
        (200, STS_RESPONSE.to_string()),
        (200, r#"{"MessageId": "message-two"}"#.to_string()),
    ])
    .await;
    let sends = EmailSends {
        pool: pool.clone(),
        sender: ses(&aws, token_file.clone()),
        apex_host: APEX.to_string(),
        product_topic: Some(SES_PRODUCT_TOPIC.to_string()),
    };
    sends.send(asked("member@acme.com")).await.unwrap();

    let unsubscribed = format!(
        r#"{{"eventType": "Subscription", "mail": {{"timestamp": "2026-07-10T12:00:00.000Z", "messageId": "message-one", "destination": ["member@acme.com"]}}, "subscription": {{"timestamp": "2026-07-10T12:00:05.000Z", "newTopicPreferences": {{"unsubscribeAll": false, "topicSubscriptionStatus": [{{"topicName": "{SES_FOUNDER_TOPIC}", "subscriptionStatus": "OPT_OUT"}}]}}}}}}"#
    );
    drain_feedback(&pool, token_file, &unsubscribed).await;

    let connection = pool.get().await.unwrap();
    let silenced: Vec<String> = connection
        .query(
            &format!("select topic from {PREFERENCE_TABLE} where email = $1"),
            &[&"member@acme.com"],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| row.get::<_, String>("topic"))
        .collect();
    assert_eq!(
        silenced,
        vec![FOUNDER_UPDATES.to_string()],
        "the unsubscribe SES reports is held against the topic it names"
    );

    assert!(
        sends.send(asked("member@acme.com")).await.is_ok(),
        "leaving the founder list does not stop the notice that the workspace is out of credit"
    );
}

#[tokio::test]
async fn product_news_is_sent_on_the_topic_a_member_can_leave() {
    let pool = ledger_pool().await;
    let (_directory, token_file) = projected_token();
    let (aws, log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, r#"{"MessageId": "message-one"}"#.to_string()),
    ])
    .await;
    let sends = EmailSends {
        pool,
        sender: ses(&aws, token_file),
        apex_host: APEX.to_string(),
        product_topic: Some(SES_PRODUCT_TOPIC.to_string()),
    };

    sends
        .send(Asked {
            kind: "connect_something_reminder".to_string(),
            topic: PRODUCT_NEWS.to_string(),
            ..asked("member@acme.com")
        })
        .await
        .unwrap();

    let outbound: Vec<Exchange> = log.lock().unwrap().drain(..).collect();
    let sent: serde_json::Value =
        serde_json::from_str(&outbound.last().expect("SES was called").body)
            .expect("the send is JSON");
    assert_eq!(
        sent["ListManagementOptions"]["ContactListName"],
        CONTACT_LIST
    );
    assert_eq!(
        sent["ListManagementOptions"]["TopicName"],
        SES_PRODUCT_TOPIC
    );
    let content = &sent["Content"]["Simple"];
    let text = content["Body"]["Text"]["Data"]
        .as_str()
        .expect("a text body");
    let html = content["Body"]["Html"]["Data"]
        .as_str()
        .expect("an html body");
    assert!(
        text.contains(UNSUBSCRIBE_PLACEHOLDER) && html.contains(UNSUBSCRIBE_PLACEHOLDER),
        "the footer carries the placeholder SES fills for the topic this send names"
    );
}

#[tokio::test]
async fn product_news_on_a_deploy_with_no_list_carries_no_footer() {
    let pool = ledger_pool().await;
    let (_directory, token_file) = projected_token();
    let (aws, log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, r#"{"MessageId": "message-one"}"#.to_string()),
    ])
    .await;
    let sends = EmailSends {
        pool,
        sender: ses(&aws, token_file),
        apex_host: APEX.to_string(),
        product_topic: None,
    };

    sends
        .send(Asked {
            kind: "connect_something_reminder".to_string(),
            topic: PRODUCT_NEWS.to_string(),
            ..asked("member@acme.com")
        })
        .await
        .unwrap();

    let outbound: Vec<Exchange> = log.lock().unwrap().drain(..).collect();
    let sent: serde_json::Value =
        serde_json::from_str(&outbound.last().expect("SES was called").body)
            .expect("the send is JSON");
    assert!(sent.get("ListManagementOptions").is_none());
    let html = sent["Content"]["Simple"]["Body"]["Html"]["Data"]
        .as_str()
        .expect("an html body");
    assert!(
        !html.contains(UNSUBSCRIBE_PLACEHOLDER),
        "a footer SES would not fill is a message reaching the member with braces in it"
    );
}

#[tokio::test]
async fn an_opt_in_on_the_hosted_page_lifts_what_the_opt_out_wrote() {
    let pool = ledger_pool().await;
    let (_directory, token_file) = projected_token();

    let left = format!(
        r#"{{"eventType": "Subscription", "mail": {{"timestamp": "2026-07-10T12:00:00.000Z", "messageId": "message-one", "destination": ["member@acme.com"]}}, "subscription": {{"timestamp": "2026-07-10T12:00:05.000Z", "newTopicPreferences": {{"unsubscribeAll": false, "topicSubscriptionStatus": [{{"topicName": "{SES_FOUNDER_TOPIC}", "subscriptionStatus": "OPT_OUT"}}]}}}}}}"#
    );
    drain_feedback(&pool, token_file.clone(), &left).await;
    assert_eq!(
        silenced_topics(&pool, "member@acme.com").await,
        vec![FOUNDER_UPDATES.to_string()]
    );

    let returned = format!(
        r#"{{"eventType": "Subscription", "mail": {{"timestamp": "2026-07-10T13:00:00.000Z", "messageId": "message-two", "destination": ["member@acme.com"]}}, "subscription": {{"timestamp": "2026-07-10T13:00:05.000Z", "newTopicPreferences": {{"unsubscribeAll": false, "topicSubscriptionStatus": [{{"topicName": "{SES_FOUNDER_TOPIC}", "subscriptionStatus": "OPT_IN"}}]}}}}}}"#
    );
    drain_feedback(&pool, token_file, &returned).await;

    assert!(
        silenced_topics(&pool, "member@acme.com").await.is_empty(),
        "the hosted page is the only surface that lifts this topic, so an opt-in it publishes has \
         to reach the row the opt-out wrote"
    );
}

#[tokio::test]
async fn unsubscribe_from_everything_silences_every_topic_a_member_may_silence() {
    let pool = ledger_pool().await;
    let (_directory, token_file) = projected_token();
    let (aws, _log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, r#"{"MessageId": "message-one"}"#.to_string()),
        (200, STS_RESPONSE.to_string()),
        (200, r#"{"MessageId": "message-two"}"#.to_string()),
    ])
    .await;
    let sends = EmailSends {
        pool: pool.clone(),
        sender: ses(&aws, token_file.clone()),
        apex_host: APEX.to_string(),
        product_topic: Some(SES_PRODUCT_TOPIC.to_string()),
    };
    sends.send(asked("member@acme.com")).await.unwrap();

    let everything = r#"{"eventType": "Subscription", "mail": {"timestamp": "2026-07-10T12:00:00.000Z", "messageId": "message-one", "destination": ["member@acme.com"]}, "subscription": {"timestamp": "2026-07-10T12:00:05.000Z", "newTopicPreferences": {"unsubscribeAll": true, "topicSubscriptionStatus": []}}}"#;
    drain_feedback(&pool, token_file, everything).await;

    let connection = pool.get().await.unwrap();
    let mut silenced: Vec<String> = connection
        .query(
            &format!("select topic from {PREFERENCE_TABLE} where email = $1"),
            &[&"member@acme.com"],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| row.get::<_, String>("topic"))
        .collect();
    silenced.sort();
    let mut expected: Vec<String> = SILENCEABLE.iter().map(|topic| topic.to_string()).collect();
    expected.sort();
    assert_eq!(
        silenced, expected,
        "a member who asked for no mail at all asked about every topic they may silence, not \
         only the ones this deploy happens to send"
    );

    let refusal = sends
        .send(Asked {
            kind: "connect_something_reminder".to_string(),
            topic: PRODUCT_NEWS.to_string(),
            ..asked("member@acme.com")
        })
        .await
        .unwrap_err();
    assert!(matches!(refusal, SendError::Silenced { .. }));
    assert!(
        sends.send(asked("member@acme.com")).await.is_ok(),
        "the notice that the workspace is out of credit is still not theirs to silence"
    );
}

#[tokio::test]
async fn turning_product_email_back_on_lifts_the_opt_out_ses_holds() {
    let pool = ledger_pool().await;
    let (_directory, token_file) = projected_token();
    let (aws, log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, held_contact(false, &[(SES_PRODUCT_TOPIC, "OPT_OUT")])),
        (200, "{}".to_string()),
    ])
    .await;
    let sends = EmailSends {
        pool: pool.clone(),
        sender: ses(&aws, token_file),
        apex_host: APEX.to_string(),
        product_topic: Some(SES_PRODUCT_TOPIC.to_string()),
    };

    sends
        .prefer(Preference {
            email: "Member@Acme.com".to_string(),
            topic: PRODUCT_NEWS.to_string(),
            silenced: true,
        })
        .await
        .unwrap();
    assert!(
        log.lock().unwrap().is_empty(),
        "silencing is ours to record; SES learns it from the member's own click"
    );

    sends
        .prefer(Preference {
            email: "member@acme.com".to_string(),
            topic: PRODUCT_NEWS.to_string(),
            silenced: false,
        })
        .await
        .unwrap();

    let outbound: Vec<Exchange> = log.lock().unwrap().drain(..).collect();
    let put = outbound.last().expect("SES was asked to lift the opt-out");
    assert!(
        put.path.contains("/contacts/member%40acme.com"),
        "the address is the contact, percent-encoded into the path: {}",
        put.path
    );
    let asked = written(put);
    assert_eq!(asked["TopicPreferences"][0]["TopicName"], SES_PRODUCT_TOPIC);
    assert_eq!(asked["TopicPreferences"][0]["SubscriptionStatus"], "OPT_IN");
    assert_eq!(
        asked["UnsubscribeAll"], false,
        "SES applies UnsubscribeAll over every topic preference, so opting the topic back in \
         without lifting it would leave the send refused"
    );

    let connection = pool.get().await.unwrap();
    let held: i64 = connection
        .query_one(
            &format!("select count(*) from {PREFERENCE_TABLE} where email = $1"),
            &[&"member@acme.com"],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(held, 0, "and our own row goes with it");
}

#[tokio::test]
async fn resuming_product_news_keeps_the_founder_topic_the_member_left() {
    let pool = ledger_pool().await;
    let (_directory, token_file) = projected_token();
    let (aws, log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, held_contact(true, &[])),
        (200, "{}".to_string()),
    ])
    .await;
    let sends = EmailSends {
        pool,
        sender: ses(&aws, token_file),
        apex_host: APEX.to_string(),
        product_topic: Some(SES_PRODUCT_TOPIC.to_string()),
    };

    sends
        .prefer(Preference {
            email: "member@acme.com".to_string(),
            topic: PRODUCT_NEWS.to_string(),
            silenced: false,
        })
        .await
        .unwrap();

    let outbound: Vec<Exchange> = log.lock().unwrap().drain(..).collect();
    let asked = written(outbound.last().expect("SES was asked to lift the opt-out"));
    assert_eq!(asked["UnsubscribeAll"], false);
    let preferences: Vec<(&str, &str)> = asked["TopicPreferences"]
        .as_array()
        .expect("the write carries the whole preference list")
        .iter()
        .map(|held| {
            (
                held["TopicName"].as_str().unwrap(),
                held["SubscriptionStatus"].as_str().unwrap(),
            )
        })
        .collect();
    assert_eq!(
        preferences,
        vec![
            (SES_FOUNDER_TOPIC, "OPT_OUT"),
            (SES_PRODUCT_TOPIC, "OPT_IN"),
        ],
        "unsubscribe-from-all bars every topic, so lifting the flag for the one topic the member \
         named has to bar the rest by name"
    );
}

#[tokio::test]
async fn a_lift_ses_refuses_leaves_our_own_bar_standing() {
    let pool = ledger_pool().await;
    let (_directory, token_file) = projected_token();
    let (aws, _log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, held_contact(false, &[(SES_PRODUCT_TOPIC, "OPT_OUT")])),
        (
            500,
            r#"{"message": "TooManyRequestsException"}"#.to_string(),
        ),
    ])
    .await;
    let sends = EmailSends {
        pool: pool.clone(),
        sender: ses(&aws, token_file),
        apex_host: APEX.to_string(),
        product_topic: Some(SES_PRODUCT_TOPIC.to_string()),
    };

    sends
        .prefer(Preference {
            email: "member@acme.com".to_string(),
            topic: PRODUCT_NEWS.to_string(),
            silenced: true,
        })
        .await
        .unwrap();
    sends
        .prefer(Preference {
            email: "member@acme.com".to_string(),
            topic: PRODUCT_NEWS.to_string(),
            silenced: false,
        })
        .await
        .expect_err("the member is told the lift failed");

    let connection = pool.get().await.unwrap();
    let held: i64 = connection
        .query_one(
            &format!("select count(*) from {PREFERENCE_TABLE} where email = $1"),
            &[&"member@acme.com"],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(
        held, 1,
        "SES still refuses the topic, so our gate stays shut with it"
    );
}
