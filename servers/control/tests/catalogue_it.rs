mod harness;

use harness::{ledger_pool, spawn_http};
use ufo_control::catalogue::{Messages, BY_DEPLOY, BY_OPERATOR};
use ufo_control::lifecycle::{Draft, Sequences, Step};
use ufo_control::shared::SharedWorkspaces;

const OPERATOR: &str = "founder@metalcraft.ai";

const DECLARED: &str = r#"{"messages": [
  {"kind": "account_parked", "topic": "transactional",
   "fires": "A provider stopped answering a connected account.",
   "subject": "{workspace} cannot reach {account}", "body": "It has stopped syncing."},
  {"kind": "balance_exhausted", "topic": "transactional",
   "fires": "The workspace has spent its credit.",
   "subject": "{workspace} is out of credit", "body": "It has no credit left."}
]}"#;

async fn messages(answers: Vec<(u16, String)>) -> (Messages, Sequences) {
    let pool = ledger_pool().await;
    let (core, _log) = spawn_http(answers).await;
    (
        Messages {
            pool: pool.clone(),
            core: SharedWorkspaces {
                workspace_url: "https://app.ufo.ai".to_string(),
                serve_internal_url: core,
                control_token: "onboard-token".to_string(),
            },
        },
        Sequences { pool },
    )
}

#[tokio::test]
async fn the_catalogue_is_what_the_fleet_declares_and_what_the_operator_approved() {
    let (messages, ledger) = messages(vec![
        (200, DECLARED.to_string()),
        (200, DECLARED.to_string()),
        (200, DECLARED.to_string()),
    ])
    .await;

    let held = messages.catalogue().await.unwrap();
    assert_eq!(
        held.messages
            .iter()
            .map(|listed| listed.kind.as_str())
            .collect::<Vec<_>>(),
        vec!["account_parked", "balance_exhausted"],
        "the running image's own declaration, in one order"
    );
    assert!(held
        .messages
        .iter()
        .all(|listed| listed.edited == BY_DEPLOY));

    let created = ledger
        .create(
            OPERATOR,
            Draft {
                name: "welcome_drip".to_string(),
                event: "member_invited".to_string(),
                steps: vec![Step {
                    after_seconds: 172_800,
                    kind: "welcome_day_two".to_string(),
                    subject: "Two days in".to_string(),
                    body: "Give it a repository to read.".to_string(),
                    action_label: None,
                    action_url: None,
                }],
            },
        )
        .await
        .unwrap();
    assert_eq!(
        messages.catalogue().await.unwrap().messages.len(),
        2,
        "a sequence nobody approved sends nothing, so it is not a message this deploy sends"
    );

    ledger.approve(created.id, 1, OPERATOR).await.unwrap();
    let live = messages.catalogue().await.unwrap();
    let step = live.messages.last().expect("the step is listed");
    assert_eq!(step.kind, "welcome_day_two");
    assert_eq!(step.fires, "welcome_drip, 2 days after member_invited");
    assert_eq!(step.edited, BY_OPERATOR);
}

#[tokio::test]
async fn a_fleet_that_cannot_be_reached_is_a_refusal_and_never_an_empty_page() {
    let (messages, _ledger) = messages(vec![(500, "boom".to_string())]).await;

    let refusal = messages.catalogue().await.unwrap_err();
    assert!(
        !refusal.to_string().is_empty(),
        "a page that cannot say what this deploy sends says so, rather than drawing nothing"
    );
}
