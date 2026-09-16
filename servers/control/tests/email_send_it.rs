mod harness;

use std::path::PathBuf;

use deadpool_postgres::Pool;
use harness::{ledger_pool, spawn_http, Exchange};
use ufo_control::campaign_feedback::{CampaignFeedback, FeedbackQueue};
use ufo_control::email::{AwsEndpoints, EmailSender, SesEmailSender};
use ufo_control::email_send::{
    Asked, EmailSends, Preference, SendError, PRODUCT_NEWS, TABLE, TRANSACTIONAL,
};

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
async fn a_malformed_ask_is_refused_with_the_reason() {
    let pool = ledger_pool().await;
    let (_directory, token_file) = projected_token();
    let (aws, log) = spawn_http(Vec::new()).await;
    let sends = EmailSends {
        pool,
        sender: ses(&aws, token_file),
        apex_host: APEX.to_string(),
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
        (200, STS_RESPONSE.to_string()),
        (200, r#"{"MessageId": "message-two"}"#.to_string()),
    ])
    .await;
    let sends = EmailSends {
        pool,
        sender: ses(&aws, token_file),
        apex_host: APEX.to_string(),
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
