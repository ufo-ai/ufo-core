mod harness;

use std::collections::HashSet;
use std::path::PathBuf;

use chrono::Utc;
use deadpool_postgres::Pool;
use harness::{ledger_pool, spawn_http};
use ufo_control::campaign::{
    campaign_message, Campaign, Campaigns, Draft, RECIPIENT_TABLE, SEND_CANCELLED, SEND_FAILED,
    SEND_PENDING, SEND_SENT, STATE_APPROVED, STATE_COMPLETED, STATE_DRAFT, STATE_PREPARED,
    STATE_SENDING,
};
use ufo_control::campaign_feedback::{CampaignFeedback, FeedbackQueue};
use ufo_control::campaign_send::CampaignSends;
use ufo_control::email::{parse_senders, AwsEndpoints, FounderSender};
use ufo_control::hud::csrf_token;
use ufo_control::message::UNSUBSCRIBE_PLACEHOLDER;
use ufo_control::shared::SharedWorkspaces;
use uuid::Uuid;

const OPERATOR: &str = "founder@metalcraft.ai";
const SENDERS: &str = "ufo founders <founders@ufo.ai>,Marshall at ufo <marshall@ufo.ai>";
const FOUNDERS: &str = "ufo founders <founders@ufo.ai>";
const APEX: &str = "ufo.ai";

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

const NO_CONTACTS: &str = r#"{"Contacts": []}"#;

fn sent(message_id: &str) -> String {
    format!(r#"{{"MessageId": "{message_id}"}}"#)
}

fn seated(rows: &[(&str, Uuid)]) -> String {
    let listed = rows
        .iter()
        .enumerate()
        .map(|(index, (email, member_id))| {
            format!(
                r#"{{"workspace_id": "{}", "member_id": "{member_id}", "email": "{email}", "created_at": "2026-07-0{}T00:00:00Z"}}"#,
                Uuid::nil(),
                index + 1
            )
        })
        .collect::<Vec<_>>()
        .join(", ");
    format!(r#"{{"recipients": [{listed}]}}"#)
}

const NO_SEATS: &str = r#"{"recipients": []}"#;

fn token_file() -> (tempfile::TempDir, PathBuf) {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("token");
    std::fs::write(&path, "projected-web-identity-token").unwrap();
    (directory, path)
}

fn sender(aws: &str, token: PathBuf) -> FounderSender {
    FounderSender {
        senders: parse_senders(SENDERS).unwrap(),
        configuration_set: "ufo-testing-founder-email".to_string(),
        contact_list: "ufo-users".to_string(),
        topic: "founder-updates".to_string(),
        region: "us-east-1".to_string(),
        role_arn: "arn:aws:iam::111122223333:role/ufo-testing-gateway-ses".to_string(),
        token_file: token,
        endpoints: AwsEndpoints {
            ses: aws.to_string(),
            sts: format!("{aws}/"),
        },
    }
}

fn campaigns(pool: Pool, aws: &str, core: String, token: PathBuf) -> Campaigns {
    Campaigns {
        pool,
        core: SharedWorkspaces {
            workspace_url: "https://app.ufo.ai".to_string(),
            serve_internal_url: core,
            control_token: "onboard-token".to_string(),
        },
        sender: sender(aws, token),
        apex_host: APEX.to_string(),
    }
}

fn draft(subject: &str) -> Draft {
    Draft {
        subject: subject.to_string(),
        body: "We shipped a thing.\n\nIt is on by default.".to_string(),
        action_label: Some("Open the changelog".to_string()),
        action_url: Some("https://ufo.ai/changelog".to_string()),
        audience: "members".to_string(),
        sender: FOUNDERS.to_string(),
    }
}

fn held(id: Uuid, subject: &str, body: &str) -> Campaign {
    Campaign {
        id,
        revision: 1,
        subject: subject.to_string(),
        body: body.to_string(),
        action_label: None,
        action_url: None,
        sender: "ufo founders <founders@ufo.ai>".to_string(),
        audience: "members".to_string(),
        state: STATE_DRAFT.to_string(),
        scheduled_at: None,
        created_by: OPERATOR.to_string(),
        approved_by: None,
        approved_revision: None,
        created_at: Utc::now(),
        updated_at: Utc::now(),
    }
}

#[test]
fn the_message_carries_the_unsubscribe_placeholder_in_both_renderings() {
    let message = campaign_message(&held(Uuid::new_v4(), "Update", "One.\n\nTwo."), APEX);
    assert!(
        message.text.contains(UNSUBSCRIBE_PLACEHOLDER),
        "{}",
        message.text
    );
    assert!(
        message.html.contains(UNSUBSCRIBE_PLACEHOLDER),
        "{}",
        message.html
    );
    assert_eq!(
        message.html.matches(UNSUBSCRIBE_PLACEHOLDER).count(),
        1,
        "SES replaces at most two occurrences, and one link is what the footer holds"
    );
}

#[test]
fn a_body_reaches_the_html_escaped_and_split_into_paragraphs() {
    let message = campaign_message(
        &held(Uuid::new_v4(), "Update", "<script>go()</script>\n\nSecond."),
        APEX,
    );
    assert!(message.html.contains("&lt;script&gt;"), "{}", message.html);
    assert!(!message.html.contains("<script>go()"), "{}", message.html);
    assert_eq!(message.html.matches("<p style=").count(), 2);
}

#[test]
fn a_draft_states_every_refusal_before_the_column_does() {
    let lone_label = Draft {
        action_url: None,
        ..draft("Update")
    };
    assert!(lone_label
        .checked(&parse_senders(SENDERS).unwrap())
        .unwrap_err()
        .to_string()
        .contains("both a label and a URL"));

    let insecure = Draft {
        action_url: Some("http://ufo.ai".to_string()),
        ..draft("Update")
    };
    assert!(insecure
        .checked(&parse_senders(SENDERS).unwrap())
        .unwrap_err()
        .to_string()
        .contains("must be https"));

    let unknown = Draft {
        audience: "everyone".to_string(),
        ..draft("Update")
    };
    assert!(unknown
        .checked(&parse_senders(SENDERS).unwrap())
        .unwrap_err()
        .to_string()
        .contains("members or operators"));

    let empty = Draft {
        subject: "   ".to_string(),
        ..draft("Update")
    };
    assert!(empty
        .checked(&parse_senders(SENDERS).unwrap())
        .unwrap_err()
        .to_string()
        .contains("subject is required"));
}

#[test]
fn a_request_token_is_bound_to_the_session_it_was_minted_for() {
    assert_eq!(csrf_token("secret", "abc"), csrf_token("secret", "abc"));
    assert_ne!(csrf_token("secret", "abc"), csrf_token("secret", "abd"));
    assert_ne!(csrf_token("secret", "abc"), csrf_token("other", "abc"));
}

#[tokio::test]
async fn preparing_freezes_the_audience_and_excludes_who_it_must_not_reach() {
    let pool = ledger_pool().await;
    let (_directory, token) = token_file();
    let (aws, _aws_log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, NO_CONTACTS.to_string()),
    ])
    .await;
    let member = Uuid::new_v4();
    let (core, _core_log) = spawn_http(vec![
        (
            200,
            seated(&[
                ("Founder@Acme.com", member),
                ("founder@acme.com", Uuid::new_v4()),
                ("staff@metalcraft.ai", Uuid::new_v4()),
            ]),
        ),
        (200, NO_SEATS.to_string()),
    ])
    .await;
    let ledger = campaigns(pool.clone(), &aws, core, token);

    let created = ledger.create(OPERATOR, draft("Update")).await.unwrap();
    let prepared = ledger.prepare(created.id, created.revision).await.unwrap();

    assert_eq!(prepared.campaign.state, STATE_PREPARED);
    assert_eq!(prepared.counts.audience, 1, "one address survives the fold");
    assert_eq!(prepared.sample, vec!["founder@acme.com".to_string()]);

    let connection = pool.get().await.unwrap();
    let frozen: Uuid = connection
        .query_one(
            &format!("select member_id from {RECIPIENT_TABLE} where campaign_id = $1"),
            &[&created.id],
        )
        .await
        .unwrap()
        .get("member_id");
    assert_eq!(
        frozen, member,
        "the first seat of a duplicated address wins"
    );
}

#[tokio::test]
async fn an_approval_names_the_revision_and_the_count_it_was_granted_over() {
    let pool = ledger_pool().await;
    let (_directory, token) = token_file();
    let (aws, _aws_log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, NO_CONTACTS.to_string()),
    ])
    .await;
    let (core, _core_log) = spawn_http(vec![
        (200, seated(&[("founder@acme.com", Uuid::new_v4())])),
        (200, NO_SEATS.to_string()),
    ])
    .await;
    let ledger = campaigns(pool, &aws, core, token);

    let created = ledger.create(OPERATOR, draft("Update")).await.unwrap();
    ledger.prepare(created.id, created.revision).await.unwrap();

    let miscounted = ledger
        .approve(created.id, created.revision, 9, OPERATOR)
        .await
        .unwrap_err();
    assert!(
        miscounted.to_string().contains("holds 1 recipients, not 9"),
        "{miscounted}"
    );

    let stale = ledger
        .approve(created.id, created.revision + 1, 1, OPERATOR)
        .await
        .unwrap_err();
    assert!(stale.to_string().contains("is at revision 1"), "{stale}");

    let approved = ledger
        .approve(created.id, created.revision, 1, OPERATOR)
        .await
        .unwrap();
    assert_eq!(approved.state, STATE_APPROVED);
    assert_eq!(approved.approved_by.as_deref(), Some(OPERATOR));
    assert_eq!(approved.approved_revision, Some(created.revision));
}

#[tokio::test]
async fn editing_approved_content_drops_the_approval_and_the_frozen_list() {
    let pool = ledger_pool().await;
    let (_directory, token) = token_file();
    let (aws, _aws_log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, NO_CONTACTS.to_string()),
    ])
    .await;
    let (core, _core_log) = spawn_http(vec![
        (200, seated(&[("founder@acme.com", Uuid::new_v4())])),
        (200, NO_SEATS.to_string()),
    ])
    .await;
    let ledger = campaigns(pool.clone(), &aws, core, token);

    let created = ledger.create(OPERATOR, draft("Update")).await.unwrap();
    ledger.prepare(created.id, created.revision).await.unwrap();
    let approved = ledger
        .approve(created.id, created.revision, 1, OPERATOR)
        .await
        .unwrap();

    let revised = ledger
        .revise(approved.id, approved.revision, draft("A better subject"))
        .await
        .unwrap();

    assert_eq!(revised.state, STATE_DRAFT);
    assert_eq!(revised.revision, approved.revision + 1);
    assert_eq!(revised.approved_by, None);
    assert_eq!(revised.approved_revision, None);
    let connection = pool.get().await.unwrap();
    let frozen: i64 = connection
        .query_one(
            &format!("select count(*) from {RECIPIENT_TABLE} where campaign_id = $1"),
            &[&created.id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(
        frozen, 0,
        "the list belonged to the words that were replaced"
    );
}

#[tokio::test]
async fn a_scheduled_campaign_sends_one_message_per_recipient_and_then_completes() {
    let pool = ledger_pool().await;
    let (_directory, token) = token_file();
    let (aws, aws_log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, NO_CONTACTS.to_string()),
        (200, STS_RESPONSE.to_string()),
        (200, sent("message-one")),
        (200, STS_RESPONSE.to_string()),
        (200, sent("message-two")),
    ])
    .await;
    let (core, _core_log) = spawn_http(vec![
        (
            200,
            seated(&[
                ("one@acme.com", Uuid::new_v4()),
                ("two@acme.com", Uuid::new_v4()),
            ]),
        ),
        (200, NO_SEATS.to_string()),
    ])
    .await;
    let ledger = campaigns(pool.clone(), &aws, core, token.clone());

    let created = ledger.create(OPERATOR, draft("Update")).await.unwrap();
    ledger.prepare(created.id, created.revision).await.unwrap();
    ledger
        .approve(created.id, created.revision, 2, OPERATOR)
        .await
        .unwrap();
    ledger
        .schedule(created.id, created.revision, Utc::now())
        .await
        .unwrap();

    let sends = CampaignSends {
        pool: pool.clone(),
        sender: sender(&aws, token),
        apex_host: APEX.to_string(),
        worker_id: "test.1".to_string(),
        poll_interval: std::time::Duration::from_millis(1),
    };
    for _ in 0..4 {
        sends.poll().await.unwrap();
    }

    let posted: Vec<String> = aws_log
        .lock()
        .unwrap()
        .iter()
        .filter(|exchange| exchange.path == "/v2/email/outbound-emails")
        .map(|exchange| exchange.body.clone())
        .collect();
    assert_eq!(posted.len(), 2, "one send per recipient");
    for body in &posted {
        let sent: serde_json::Value = serde_json::from_str(body).unwrap();
        assert_eq!(sent["ConfigurationSetName"], "ufo-testing-founder-email");
        assert_eq!(
            sent["ListManagementOptions"]["ContactListName"],
            "ufo-users"
        );
        assert_eq!(
            sent["ListManagementOptions"]["TopicName"],
            "founder-updates"
        );
        assert_eq!(sent["FromEmailAddress"], FOUNDERS);
        assert_eq!(sent["ReplyToAddresses"][0], "founders@ufo.ai");
        assert_eq!(
            sent["Destination"]["ToAddresses"].as_array().unwrap().len(),
            1,
            "SES adds the unsubscribe header only to a single-recipient message"
        );
    }

    let preview = ledger.preview(created.id).await.unwrap();
    assert_eq!(preview.campaign.state, STATE_COMPLETED);
    assert_eq!(preview.counts.submitted, 2);

    let connection = pool.get().await.unwrap();
    let recorded: Vec<String> = connection
        .query(
            &format!(
                "select ses_message_id from {RECIPIENT_TABLE} where campaign_id = $1 \
                 and state = '{SEND_SENT}' order by email"
            ),
            &[&created.id],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| row.get::<_, String>("ses_message_id"))
        .collect();
    assert_eq!(recorded, vec!["message-one", "message-two"]);
}

#[tokio::test]
async fn a_cancel_stops_the_rows_that_never_left_and_leaves_the_sent_one() {
    let pool = ledger_pool().await;
    let (_directory, token) = token_file();
    let (aws, _aws_log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, NO_CONTACTS.to_string()),
        (200, STS_RESPONSE.to_string()),
        (200, sent("message-one")),
    ])
    .await;
    let (core, _core_log) = spawn_http(vec![
        (
            200,
            seated(&[
                ("one@acme.com", Uuid::new_v4()),
                ("two@acme.com", Uuid::new_v4()),
            ]),
        ),
        (200, NO_SEATS.to_string()),
    ])
    .await;
    let ledger = campaigns(pool.clone(), &aws, core, token.clone());

    let created = ledger.create(OPERATOR, draft("Update")).await.unwrap();
    ledger.prepare(created.id, created.revision).await.unwrap();
    ledger
        .approve(created.id, created.revision, 2, OPERATOR)
        .await
        .unwrap();
    ledger
        .schedule(created.id, created.revision, Utc::now())
        .await
        .unwrap();

    let sends = CampaignSends {
        pool: pool.clone(),
        sender: sender(&aws, token),
        apex_host: APEX.to_string(),
        worker_id: "test.1".to_string(),
        poll_interval: std::time::Duration::from_millis(1),
    };
    sends.poll().await.unwrap();
    ledger.cancel(created.id, created.revision).await.unwrap();

    let connection = pool.get().await.unwrap();
    let states: Vec<String> = connection
        .query(
            &format!("select state from {RECIPIENT_TABLE} where campaign_id = $1 order by email"),
            &[&created.id],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| row.get::<_, String>("state"))
        .collect();
    assert_eq!(
        states,
        vec![SEND_SENT.to_string(), SEND_CANCELLED.to_string()]
    );

    assert!(
        sends.poll().await.unwrap().is_some(),
        "nothing is left to claim"
    );
    let still: Vec<String> = connection
        .query(
            &format!("select state from {RECIPIENT_TABLE} where campaign_id = $1 order by email"),
            &[&created.id],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| row.get::<_, String>("state"))
        .collect();
    assert_eq!(still, states, "a cancelled row is never claimed");
}

#[tokio::test]
async fn a_hard_bounce_lands_on_its_recipient_and_bars_the_address_from_the_next_campaign() {
    let pool = ledger_pool().await;
    let (_directory, token) = token_file();
    let (aws, _aws_log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, NO_CONTACTS.to_string()),
        (200, STS_RESPONSE.to_string()),
        (200, sent("message-one")),
        (200, STS_RESPONSE.to_string()),
        (200, NO_CONTACTS.to_string()),
    ])
    .await;
    let (core, _core_log) = spawn_http(vec![
        (200, seated(&[("one@acme.com", Uuid::new_v4())])),
        (200, NO_SEATS.to_string()),
        (200, seated(&[("one@acme.com", Uuid::new_v4())])),
        (200, NO_SEATS.to_string()),
    ])
    .await;
    let ledger = campaigns(pool.clone(), &aws, core, token.clone());

    let first = ledger.create(OPERATOR, draft("Update")).await.unwrap();
    ledger.prepare(first.id, first.revision).await.unwrap();
    ledger
        .approve(first.id, first.revision, 1, OPERATOR)
        .await
        .unwrap();
    ledger
        .schedule(first.id, first.revision, Utc::now())
        .await
        .unwrap();
    CampaignSends {
        pool: pool.clone(),
        sender: sender(&aws, token.clone()),
        apex_host: APEX.to_string(),
        worker_id: "test.1".to_string(),
        poll_interval: std::time::Duration::from_millis(1),
    }
    .poll()
    .await
    .unwrap();

    let bounce = r#"{"eventType": "Bounce", "mail": {"timestamp": "2026-07-10T12:00:00.000Z", "messageId": "message-one", "destination": ["one@acme.com"]}, "bounce": {"bounceType": "Permanent", "bounceSubType": "General", "timestamp": "2026-07-10T12:00:05.000Z"}}"#;
    let (queue, _queue_log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (
            200,
            serde_json::json!({"Messages": [{"MessageId": "1", "ReceiptHandle": "r1", "Body": bounce}]})
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
            token_file: token.clone(),
            sts: format!("{queue}/"),
        },
        poll_interval: std::time::Duration::from_millis(1),
    }
    .poll()
    .await
    .unwrap();

    let results = ledger.preview(first.id).await.unwrap();
    assert_eq!(results.counts.bounced, 1);
    assert_eq!(results.counts.delivered, 0);

    let second = ledger
        .create(OPERATOR, draft("A second note"))
        .await
        .unwrap();
    let prepared = ledger.prepare(second.id, second.revision).await.unwrap();
    assert_eq!(prepared.counts.audience, 0, "the bounced address is barred");
    assert_eq!(
        prepared.exclusions.suppressed, 0,
        "the frozen view counts rows"
    );

    let connection = pool.get().await.unwrap();
    let pending: i64 = connection
        .query_one(
            &format!(
                "select count(*) from {RECIPIENT_TABLE} where campaign_id = $1 \
                 and state = '{SEND_PENDING}'"
            ),
            &[&second.id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(pending, 0);
}

#[test]
fn a_sender_outside_the_configured_list_is_refused_before_ses_sees_it() {
    let senders = parse_senders(SENDERS).unwrap();
    let outside = Draft {
        sender: "Someone Else <someone@elsewhere.example>".to_string(),
        ..draft("Update")
    };
    assert!(outside
        .checked(&senders)
        .unwrap_err()
        .to_string()
        .contains("is not a configured sender"));
    assert!(draft("Update").checked(&senders).is_ok());
}

#[test]
fn the_configured_list_keeps_its_order_and_raises_on_an_entry_it_cannot_read() {
    let senders = parse_senders(SENDERS).unwrap();
    assert_eq!(
        senders
            .iter()
            .map(|s| s.address.as_str())
            .collect::<Vec<_>>(),
        vec!["founders@ufo.ai", "marshall@ufo.ai"]
    );
    assert_eq!(senders[0].label, FOUNDERS);
    assert!(parse_senders("ufo founders <not an address>").is_err());
    assert!(parse_senders("").is_err());
}

#[tokio::test]
async fn the_opt_out_read_walks_every_page_and_stops_on_the_last() {
    let (_directory, token) = token_file();
    let (aws, log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (
            200,
            r#"{"Contacts": [{"EmailAddress": "One@Acme.com"}], "NextToken": "page-two"}"#
                .to_string(),
        ),
        (
            200,
            r#"{"Contacts": [{"EmailAddress": "two@acme.com"}]}"#.to_string(),
        ),
    ])
    .await;

    let opted_out = sender(&aws, token).opted_out().await.unwrap();

    assert_eq!(
        opted_out,
        HashSet::from(["one@acme.com".to_string(), "two@acme.com".to_string()]),
        "both pages land, normalized"
    );
    let asked: Vec<String> = log
        .lock()
        .unwrap()
        .iter()
        .filter(|exchange| exchange.path.ends_with("/contacts/list"))
        .map(|exchange| exchange.body.clone())
        .collect();
    assert_eq!(
        asked.len(),
        2,
        "the last page carries no token and ends the walk"
    );
    let first: serde_json::Value = serde_json::from_str(&asked[0]).unwrap();
    let second: serde_json::Value = serde_json::from_str(&asked[1]).unwrap();
    assert!(first["NextToken"].is_null());
    assert_eq!(
        second["NextToken"], "page-two",
        "the token advances the walk"
    );
    assert_eq!(
        second["Filter"]["TopicFilter"]["TopicName"],
        "founder-updates"
    );
    assert_eq!(
        second["Filter"]["TopicFilter"]["UseDefaultIfPreferenceUnavailable"], false,
        "only an explicit opt-out suppresses: the topic default is OPT_IN"
    );
}

#[tokio::test]
async fn a_credential_failure_that_never_reached_ses_puts_the_row_back() {
    let pool = ledger_pool().await;
    let (_directory, token) = token_file();
    // STS answers 500 for the send, so the SES POST is never made and the row may be repeated.
    let (aws, _aws_log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, NO_CONTACTS.to_string()),
        (
            500,
            "<Error><Message>unavailable</Message></Error>".to_string(),
        ),
    ])
    .await;
    let (core, _core_log) = spawn_http(vec![
        (200, seated(&[("one@acme.com", Uuid::new_v4())])),
        (200, NO_SEATS.to_string()),
    ])
    .await;
    let ledger = campaigns(pool.clone(), &aws, core, token.clone());

    let created = ledger.create(OPERATOR, draft("Update")).await.unwrap();
    ledger.prepare(created.id, created.revision).await.unwrap();
    ledger
        .approve(created.id, created.revision, 1, OPERATOR)
        .await
        .unwrap();
    ledger
        .schedule(created.id, created.revision, Utc::now())
        .await
        .unwrap();

    let sends = CampaignSends {
        pool: pool.clone(),
        sender: sender(&aws, token),
        apex_host: APEX.to_string(),
        worker_id: "test.1".to_string(),
        poll_interval: std::time::Duration::from_millis(1),
    };
    let paused = sends.poll().await.unwrap();

    assert!(
        paused.is_some(),
        "the worker waits rather than burning the audience"
    );
    let connection = pool.get().await.unwrap();
    let row = connection
        .query_one(
            &format!(
                "select state, attempted_at, last_error from {RECIPIENT_TABLE} \
                 where campaign_id = $1"
            ),
            &[&created.id],
        )
        .await
        .unwrap();
    assert_eq!(row.get::<_, String>("state"), SEND_PENDING);
    assert!(row
        .get::<_, Option<chrono::DateTime<Utc>>>("attempted_at")
        .is_none());
    assert!(row.get::<_, Option<String>>("last_error").is_some());

    let preview = ledger.preview(created.id).await.unwrap();
    assert_eq!(
        preview.counts.failed, 0,
        "a send that never happened is not a failure"
    );
    assert_eq!(
        preview.campaign.state, STATE_SENDING,
        "the campaign is not completed"
    );
}

#[tokio::test]
async fn a_test_send_refuses_an_address_the_frozen_audience_already_holds() {
    let pool = ledger_pool().await;
    let (_directory, token) = token_file();
    let (aws, _aws_log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, NO_CONTACTS.to_string()),
    ])
    .await;
    let (core, _core_log) = spawn_http(vec![
        (200, seated(&[(OPERATOR, Uuid::new_v4())])),
        (200, NO_SEATS.to_string()),
    ])
    .await;
    let ledger = campaigns(pool.clone(), &aws, core, token);

    let staff = Draft {
        audience: "operators".to_string(),
        ..draft("Update")
    };
    let created = ledger.create(OPERATOR, staff).await.unwrap();
    ledger.prepare(created.id, created.revision).await.unwrap();

    let refused = ledger
        .test(created.id, created.revision, OPERATOR)
        .await
        .unwrap_err();

    assert!(
        refused
            .to_string()
            .contains("already in this campaign's audience"),
        "{refused}"
    );
    let preview = ledger.preview(created.id).await.unwrap();
    assert_eq!(
        preview.counts.audience, 1,
        "the audience row survives the refusal"
    );
    assert_eq!(preview.counts.tests, 0);
}

#[tokio::test]
async fn the_opt_out_read_raises_on_a_cursor_that_does_not_move() {
    let (_directory, token) = token_file();
    // Two answers carrying the same token. A walk that trusted the server's word would ask forever.
    let (aws, log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (
            200,
            r#"{"Contacts": [{"EmailAddress": "one@acme.com"}], "NextToken": "stuck"}"#.to_string(),
        ),
        (
            200,
            r#"{"Contacts": [{"EmailAddress": "two@acme.com"}], "NextToken": "stuck"}"#.to_string(),
        ),
    ])
    .await;

    let refused = sender(&aws, token).opted_out().await.unwrap_err();

    assert!(
        refused.to_string().contains("NextToken"),
        "a repeated cursor raises rather than returning a set missing everyone past page one: \
         {refused}"
    );
    let asked = log
        .lock()
        .unwrap()
        .iter()
        .filter(|exchange| exchange.path.ends_with("/contacts/list"))
        .count();
    assert_eq!(asked, 2, "and it stops asking");
}

#[tokio::test]
async fn a_campaign_whose_sender_left_the_list_fails_its_rows_and_frees_the_worker() {
    let pool = ledger_pool().await;
    let (_directory, token) = token_file();
    let (aws, aws_log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, NO_CONTACTS.to_string()),
    ])
    .await;
    let (core, _core_log) = spawn_http(vec![
        (200, seated(&[("one@acme.com", Uuid::new_v4())])),
        (200, NO_SEATS.to_string()),
    ])
    .await;
    let ledger = campaigns(pool.clone(), &aws, core, token.clone());

    let created = ledger.create(OPERATOR, draft("Update")).await.unwrap();
    ledger.prepare(created.id, created.revision).await.unwrap();
    ledger
        .approve(created.id, created.revision, 1, OPERATOR)
        .await
        .unwrap();
    ledger
        .schedule(created.id, created.revision, Utc::now())
        .await
        .unwrap();

    // The deploy no longer configures the address the campaign froze.
    let mut narrowed = sender(&aws, token);
    narrowed.senders = parse_senders("Marshall at ufo <marshall@ufo.ai>").unwrap();
    let sends = CampaignSends {
        pool: pool.clone(),
        sender: narrowed,
        apex_host: APEX.to_string(),
        worker_id: "test.1".to_string(),
        poll_interval: std::time::Duration::from_millis(1),
    };
    let paused = sends.poll().await.unwrap();

    assert!(
        paused.is_none(),
        "the worker moves on rather than re-claiming this row"
    );
    let connection = pool.get().await.unwrap();
    let row = connection
        .query_one(
            &format!(
                "select state, worker_id, claim_expires_at, last_error from {RECIPIENT_TABLE} \
                 where campaign_id = $1"
            ),
            &[&created.id],
        )
        .await
        .unwrap();
    // Terminal, not re-armed: `claim` orders by `created_at`, so a released row would sit at the
    // head of the queue on every poll and no other campaign would ever be sent.
    assert_eq!(row.get::<_, String>("state"), SEND_FAILED);
    assert!(
        row.get::<_, Option<String>>("worker_id").is_none(),
        "the lease is cleared"
    );
    assert!(
        row.get::<_, Option<chrono::DateTime<Utc>>>("claim_expires_at")
            .is_none(),
        "so no lease sweep can call it an unanswered send"
    );
    assert!(
        row.get::<_, Option<String>>("last_error")
            .unwrap()
            .contains("no longer a configured sender"),
        "and the reason is the true one"
    );
    assert!(
        !aws_log
            .lock()
            .unwrap()
            .iter()
            .any(|exchange| exchange.path == "/v2/email/outbound-emails"),
        "SES was never called"
    );
}

#[tokio::test]
async fn a_test_sent_before_the_prepare_does_not_take_an_audience_member_out_of_it() {
    let pool = ledger_pool().await;
    let (_directory, token) = token_file();
    let (aws, _aws_log) = spawn_http(vec![
        (200, STS_RESPONSE.to_string()),
        (200, NO_CONTACTS.to_string()),
    ])
    .await;
    let (core, _core_log) = spawn_http(vec![
        (200, seated(&[(OPERATOR, Uuid::new_v4())])),
        (200, NO_SEATS.to_string()),
    ])
    .await;
    let ledger = campaigns(pool.clone(), &aws, core, token);

    let staff = Draft {
        audience: "operators".to_string(),
        ..draft("Update")
    };
    let created = ledger.create(OPERATOR, staff).await.unwrap();
    // The test comes first, so its row already holds the key the audience row needs.
    ledger
        .test(created.id, created.revision, OPERATOR)
        .await
        .unwrap();
    let prepared = ledger.prepare(created.id, created.revision).await.unwrap();

    assert_eq!(
        prepared.counts.audience, 1,
        "the operator is in an operators audience and the prepare must say so"
    );
    assert_eq!(
        prepared.counts.tests, 0,
        "the rehearsal gives the key up to the campaign"
    );
    assert_eq!(prepared.sample, vec![OPERATOR.to_string()]);
    let connection = pool.get().await.unwrap();
    let rows: i64 = connection
        .query_one(
            &format!("select count(*) from {RECIPIENT_TABLE} where campaign_id = $1"),
            &[&created.id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(rows, 1, "one row carries one address");
}
