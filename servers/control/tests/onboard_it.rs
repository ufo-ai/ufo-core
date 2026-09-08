mod harness;

use harness::spawn_http;
use std::collections::BTreeMap;
use ufo_control::shared::{
    deterministic_workspace_id, SeatError, SharedWorkspaces, SignupProfile, WorkspaceChoice,
};

const TOKEN: &str = "onboard-control-token";

fn workspaces(base: &str) -> SharedWorkspaces {
    SharedWorkspaces {
        workspace_url: "https://app.flyingobject.ai".to_string(),
        serve_internal_url: base.to_string(),
        control_token: TOKEN.to_string(),
    }
}

#[test]
fn every_signup_subject_derives_the_workspace_python_derives() {
    let raw = include_str!("onboard_contract.json");
    let vectors: BTreeMap<String, String> = serde_json::from_str(raw).expect("vectors parse");
    assert!(vectors.len() >= 4);
    for (domain, expected) in vectors {
        assert_eq!(
            deterministic_workspace_id(&domain).to_string(),
            expected,
            "{domain} derives a different workspace here than in core"
        );
    }
}

#[tokio::test]
async fn choices_asks_for_the_normalized_address_and_subject() {
    let (base, log) = spawn_http(vec![(
        200,
        r#"{"choices":[{"workspace_id":"3e38d44d-322e-53af-97b6-6204849f6a5c","label":"acme.com","member":true}]}"#
            .to_string(),
    )])
    .await;
    let listed = workspaces(&base)
        .choices("acme.com", "  Founder@Acme.com ")
        .await
        .unwrap();
    assert_eq!(listed.len(), 1);
    assert_eq!(listed[0].label, "acme.com");
    assert!(listed[0].member);

    let exchanges = log.lock().unwrap();
    assert!(exchanges[0].path.starts_with("/internal/onboard/choices?"));
    assert!(
        exchanges[0].path.contains("email=founder%40acme.com"),
        "{}",
        exchanges[0].path
    );
    assert!(
        exchanges[0].path.contains("domain=acme.com"),
        "{}",
        exchanges[0].path
    );
    assert!(
        exchanges[0].path.contains("signup_subject=acme.com"),
        "{}",
        exchanges[0].path
    );
    assert_eq!(
        exchanges[0].authorization.as_deref(),
        Some(&format!("Bearer {TOKEN}")[..])
    );
}

#[tokio::test]
async fn a_personal_mail_call_states_the_exact_address_as_the_previous_identity() {
    let (base, log) = spawn_http(vec![
        (200, r#"{"choices":[]}"#.to_string()),
        (
            200,
            r#"{"workspace_id":"c9ff4df7-cade-5134-aa05-67f2689645d7","admin":true,"founding":true}"#
                .to_string(),
        ),
    ])
    .await;
    let shared = workspaces(&base);
    assert!(shared
        .choices("carol@gmail.com", "  Carol@Gmail.com ")
        .await
        .unwrap()
        .is_empty());
    shared
        .create("carol@gmail.com", "carol@gmail.com", None)
        .await
        .unwrap();

    let exchanges = log.lock().unwrap();
    assert!(
        exchanges[0].path.contains("domain=carol%40gmail.com"),
        "{}",
        exchanges[0].path
    );
    assert!(
        exchanges[0]
            .path
            .contains("signup_subject=carol%40gmail.com"),
        "{}",
        exchanges[0].path
    );
    let body: serde_json::Value = serde_json::from_str(&exchanges[1].body).unwrap();
    assert_eq!(body["domain"], "carol@gmail.com");
    assert_eq!(body["email"], "carol@gmail.com");
    assert_eq!(body["signup_subject"], "carol@gmail.com");
}

#[tokio::test]
async fn an_empty_candidate_list_is_not_an_error() {
    let (base, _log) = spawn_http(vec![(200, r#"{"choices":[]}"#.to_string())]).await;
    assert!(workspaces(&base)
        .choices("acme.com", "founder@acme.com")
        .await
        .unwrap()
        .is_empty());
}

#[tokio::test]
async fn the_invitation_read_reads_back_whole_and_carries_the_next_page_cursor() {
    let (base, log) = spawn_http(vec![
        (
            200,
            r#"{"invitations":[{"workspace_id":"3e38d44d-322e-53af-97b6-6204849f6a5c",
             "email":"teammate@acme.com","invited_by":"admin@acme.com",
             "workspace_label":"acme.com","invited_at":"2026-08-18T10:00:00Z"}]}"#
                .to_string(),
        ),
        (200, r#"{"invitations":[]}"#.to_string()),
    ])
    .await;
    let listed = workspaces(&base).invitations(None).await.unwrap();

    assert_eq!(listed.len(), 1);
    assert_eq!(listed[0].email, "teammate@acme.com");
    assert_eq!(listed[0].invited_by, "admin@acme.com");
    assert_eq!(listed[0].workspace_label, "acme.com");
    assert_eq!(
        listed[0].workspace_id.to_string(),
        "3e38d44d-322e-53af-97b6-6204849f6a5c"
    );
    assert_eq!(
        log.lock().unwrap()[0].path,
        "/internal/onboard/invitations",
        "the first page asks for no cursor at all"
    );

    assert!(workspaces(&base)
        .invitations(Some(&listed[0]))
        .await
        .unwrap()
        .is_empty());
    let asked = log.lock().unwrap()[1].path.clone();
    assert!(
        asked.contains("after_invited_at=2026-08-18T10%3A00%3A00"),
        "{asked}"
    );
    assert!(
        asked.contains("after_workspace_id=3e38d44d-322e-53af-97b6-6204849f6a5c"),
        "{asked}"
    );
    assert!(asked.contains("after_email=teammate%40acme.com"), "{asked}");
}

#[tokio::test]
async fn the_fleet_count_reads_back_as_a_number() {
    let (base, log) = spawn_http(vec![(200, r#"{"craft":42}"#.to_string())]).await;
    assert_eq!(workspaces(&base).fleet().await.unwrap(), 42);
    assert_eq!(log.lock().unwrap()[0].path, "/internal/onboard/fleet");
}

#[tokio::test]
async fn create_sends_the_derived_workspace_and_the_intake_profile() {
    let (base, log) = spawn_http(vec![(
        200,
        r#"{"workspace_id":"3e38d44d-322e-53af-97b6-6204849f6a5c","admin":true,"founding":true}"#
            .to_string(),
    )])
    .await;
    let profile = SignupProfile {
        business: "we sell widgets".to_string(),
        goals: "answer support mail".to_string(),
    };
    let ensured = workspaces(&base)
        .create("acme.com", "  Founder@Acme.com ", Some(&profile))
        .await
        .unwrap();
    assert_eq!(ensured.workspace_id, "3e38d44d-322e-53af-97b6-6204849f6a5c");
    assert!(ensured.admin);

    let exchanges = log.lock().unwrap();
    assert_eq!(exchanges[0].path, "/internal/onboard/seat");
    let body: serde_json::Value = serde_json::from_str(&exchanges[0].body).unwrap();
    assert_eq!(body["workspace_id"], "3e38d44d-322e-53af-97b6-6204849f6a5c");
    assert_eq!(body["domain"], "acme.com");
    assert_eq!(body["email"], "founder@acme.com");
    assert_eq!(body["signup_subject"], "acme.com");
    assert_eq!(body["profile"]["business"], "we sell widgets");
    assert_eq!(body["profile"]["goals"], "answer support mail");
}

#[tokio::test]
async fn create_without_a_profile_sends_none_rather_than_an_empty_one() {
    let (base, log) = spawn_http(vec![(
        200,
        r#"{"workspace_id":"3e38d44d-322e-53af-97b6-6204849f6a5c","admin":true,"founding":true}"#
            .to_string(),
    )])
    .await;
    workspaces(&base)
        .create("acme.com", "founder@acme.com", None)
        .await
        .unwrap();
    let body: serde_json::Value = serde_json::from_str(&log.lock().unwrap()[0].body).unwrap();
    assert!(body["profile"].is_null(), "{body}");
}

#[tokio::test]
async fn joining_an_existing_membership_reads_it_rather_than_seating_again() {
    let (base, log) = spawn_http(vec![(200, r#"{"admin":false}"#.to_string())]).await;
    let choice = WorkspaceChoice {
        workspace_id: "3e38d44d-322e-53af-97b6-6204849f6a5c".to_string(),
        label: "acme.com".to_string(),
        member: true,
    };
    let ensured = workspaces(&base)
        .join(&choice, "acme.com", "  Teammate@Acme.com ")
        .await
        .unwrap();
    assert_eq!(ensured.workspace_id, choice.workspace_id);
    assert!(!ensured.admin, "a joined teammate is not an admin");

    let exchanges = log.lock().unwrap();
    assert!(
        exchanges[0]
            .path
            .starts_with("/internal/onboard/membership?"),
        "a seated member must not be re-seated: {}",
        exchanges[0].path
    );
    assert!(exchanges[0].path.contains("email=teammate%40acme.com"));
}

#[tokio::test]
async fn joining_a_domain_match_seats_the_member() {
    let (base, log) = spawn_http(vec![(
        200,
        r#"{"workspace_id":"3e38d44d-322e-53af-97b6-6204849f6a5c","admin":false,"founding":false}"#
            .to_string(),
    )])
    .await;
    let choice = WorkspaceChoice {
        workspace_id: "3e38d44d-322e-53af-97b6-6204849f6a5c".to_string(),
        label: "acme.com".to_string(),
        member: false,
    };
    workspaces(&base)
        .join(&choice, "acme.com", "newcomer@acme.com")
        .await
        .unwrap();
    let exchanges = log.lock().unwrap();
    assert_eq!(exchanges[0].path, "/internal/onboard/seat");
    let body: serde_json::Value = serde_json::from_str(&exchanges[0].body).unwrap();
    assert!(
        body["profile"].is_null(),
        "a join carries no intake profile — the form described the founder, not this member"
    );
}

#[tokio::test]
async fn a_workspace_id_that_is_not_a_uuid_is_refused_before_any_call() {
    let (base, log) = spawn_http(vec![]).await;
    let choice = WorkspaceChoice {
        workspace_id: "not-a-uuid".to_string(),
        label: "acme.com".to_string(),
        member: true,
    };
    let refused = workspaces(&base)
        .join(&choice, "acme.com", "founder@acme.com")
        .await
        .unwrap_err();
    assert!(matches!(refused, SeatError::Refused(_)), "{refused}");
    assert!(log.lock().unwrap().is_empty(), "nothing was asked");
}

#[tokio::test]
async fn cores_own_sentence_rides_a_refusal_back() {
    let (base, _log) = spawn_http(vec![(
        409,
        r#"{"detail":"domain acme.com maps to 2 workspaces"}"#.to_string(),
    )])
    .await;
    let refused = workspaces(&base)
        .choices("acme.com", "founder@acme.com")
        .await
        .unwrap_err();
    assert_eq!(refused.to_string(), "domain acme.com maps to 2 workspaces");
}

#[tokio::test]
async fn a_missing_membership_carries_cores_refusal() {
    let (base, _log) = spawn_http(vec![(
        404,
        r#"{"detail":"gone@acme.com is no longer a member of 3e38d44d-322e-53af-97b6-6204849f6a5c"}"#
            .to_string(),
    )])
    .await;
    let choice = WorkspaceChoice {
        workspace_id: "3e38d44d-322e-53af-97b6-6204849f6a5c".to_string(),
        label: "acme.com".to_string(),
        member: true,
    };
    let refused = workspaces(&base)
        .join(&choice, "acme.com", "gone@acme.com")
        .await
        .unwrap_err();
    assert!(
        refused.to_string().contains("no longer a member"),
        "{refused}"
    );
}

#[tokio::test]
async fn a_refused_token_is_reported_as_a_status_not_a_sentence() {
    let (base, _log) = spawn_http(vec![(401, "{}".to_string())]).await;
    let refused = workspaces(&base).fleet().await.unwrap_err();
    assert!(
        matches!(refused, SeatError::Unexpected { status: 401 }),
        "{refused}"
    );
}

#[tokio::test]
async fn an_unreachable_service_is_named_as_such() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    drop(listener);
    let refused = workspaces(&base).fleet().await.unwrap_err();
    assert!(matches!(refused, SeatError::Unreachable), "{refused}");
}

#[tokio::test]
async fn a_success_body_that_is_not_the_shape_is_not_read_as_one() {
    let (base, _log) = spawn_http(vec![(200, r#"{"unexpected":true}"#.to_string())]).await;
    let refused = workspaces(&base).fleet().await.unwrap_err();
    assert!(
        matches!(refused, SeatError::Unexpected { status: 200 }),
        "{refused}"
    );
}
