use chrono::{TimeZone, Utc};
use serde::Deserialize;
use ufo_control::directives::directive;
use ufo_control::email_send::{
    Asked, Delivered, Preference, Sent, Silenced, PREFERENCE_PATH, PRODUCT_NEWS, SEND_PATH,
    TRANSACTIONAL,
};
use ufo_control::token::{sign, verified_email};

#[derive(Deserialize)]
struct Vector {
    secret: String,
    workspace_id: String,
    email: String,
    exp: i64,
    token: String,
}

#[test]
fn every_core_vector_signs_and_reads_back_identically_here() {
    let raw = include_str!("bearer_contract.json");
    let vectors: Vec<Vector> = serde_json::from_str(raw).expect("contract vectors parse");
    assert!(
        vectors.len() >= 5,
        "the contract covers more than a happy path"
    );
    for vector in vectors {
        let expires_at = Utc.timestamp_opt(vector.exp, 0).unwrap();
        let minted = sign(
            &vector.secret,
            &vector.workspace_id,
            &vector.email,
            expires_at,
        )
        .expect("a contract vector signs");
        assert_eq!(
            minted, vector.token,
            "vector for {} at {} drifted from core",
            vector.email, vector.exp
        );
        assert_eq!(
            verified_email(
                &vector.secret,
                &vector.token,
                Utc.timestamp_opt(vector.exp - 1, 0).unwrap()
            ),
            Some(vector.email.trim().to_lowercase()),
            "the sign-in door reads back the address core signed for {}",
            vector.email
        );
    }
}

#[derive(Deserialize)]
struct WireRow {
    wire: String,
    verb: String,
    fields: Vec<String>,
    line: String,
}

#[test]
fn every_onboard_fixture_line_is_this_encoder() {
    let raw = include_str!("../../../client/tests/fixtures/directives.jsonl");
    let rows: Vec<WireRow> = raw
        .lines()
        .filter(|line| !line.is_empty())
        .map(|line| serde_json::from_str(line).expect("a fixture row parses"))
        .collect();
    let onboard: Vec<&WireRow> = rows.iter().filter(|row| row.wire == "onboard").collect();
    assert!(
        onboard.len() >= 5,
        "the onboarding wire carries more verbs than this"
    );
    for row in onboard {
        let borrowed: Vec<&str> = row.fields.iter().map(String::as_str).collect();
        let rendered = String::from_utf8(directive(&row.verb, &borrowed)).unwrap();
        assert_eq!(
            rendered.trim_end_matches('\n'),
            row.line,
            "the {} directive drifted from the fixture",
            row.verb
        );
    }
}

#[derive(Deserialize)]
struct EmailSendContract {
    send_path: String,
    delivery_path: String,
    preference_path: String,
    send_request: serde_json::Value,
    send_response: serde_json::Value,
    reported_response: serde_json::Value,
    unreported_response: serde_json::Value,
    preference_request: serde_json::Value,
    preference_response: serde_json::Value,
}

#[test]
fn the_send_seam_reads_and_answers_exactly_what_core_writes() {
    let contract: EmailSendContract =
        serde_json::from_str(include_str!("email_send_contract.json"))
            .expect("the contract parses");
    assert_eq!(SEND_PATH, contract.send_path);
    assert!(
        contract.delivery_path.starts_with(&format!("{SEND_PATH}/")),
        "a delivery is read under the send it names"
    );

    let asked: Asked =
        serde_json::from_value(contract.send_request.clone()).expect("core's body deserializes");
    assert_eq!(asked.email, "member@acme.com");
    assert_eq!(asked.kind, "balance_exhausted");
    assert_eq!(asked.topic, TRANSACTIONAL);
    assert_eq!(asked.subject, "acme.com is out of credit");
    assert!(asked.body.contains("no credit left"));
    assert_eq!(asked.action_label.as_deref(), Some("Add credit"));
    assert!(asked
        .action_url
        .as_deref()
        .is_some_and(|url| url.starts_with("https://")));

    let message_id = contract.send_response["message_id"]
        .as_str()
        .expect("the fixture names a message id")
        .to_string();
    assert_eq!(
        serde_json::to_value(Sent { message_id }).unwrap(),
        contract.send_response,
        "core reads message_id off this shape"
    );
    assert_eq!(
        serde_json::to_value(Delivered {
            delivery: Some("delivered".to_string())
        })
        .unwrap(),
        contract.reported_response
    );
    assert_eq!(
        serde_json::to_value(Delivered { delivery: None }).unwrap(),
        contract.unreported_response,
        "a send SES has not reported on answers with a null, never an absent field"
    );

    let mut older = contract.send_request.clone();
    older
        .as_object_mut()
        .expect("the body is an object")
        .remove("topic");
    let from_the_previous_image: Asked =
        serde_json::from_value(older).expect("a body with no topic still deserializes");
    assert_eq!(
        from_the_previous_image.topic, TRANSACTIONAL,
        "the gateway and the fleet roll separately, so a post from the image being replaced is \
         read rather than refused"
    );

    assert_eq!(PREFERENCE_PATH, contract.preference_path);
    let preference: Preference =
        serde_json::from_value(contract.preference_request).expect("the preference deserializes");
    assert_eq!(preference.topic, PRODUCT_NEWS);
    assert!(preference.silenced);
    assert_eq!(
        serde_json::to_value(Silenced {
            topic: PRODUCT_NEWS.to_string(),
            silenced: true,
        })
        .unwrap(),
        contract.preference_response
    );
}
