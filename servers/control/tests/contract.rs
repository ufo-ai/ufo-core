use chrono::{TimeZone, Utc};
use serde::Deserialize;
use ufo_control::directives::directive;
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
