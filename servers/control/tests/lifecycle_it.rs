mod harness;

use harness::ledger_pool;
use ufo_control::lifecycle::{Draft, SequenceError, Sequences, Step, URL_PLACEHOLDER};

const OPERATOR: &str = "founder@metalcraft.ai";

fn step(after_seconds: i64, kind: &str) -> Step {
    Step {
        after_seconds,
        kind: kind.to_string(),
        subject: "Getting started".to_string(),
        body: "Ask it to connect an account.".to_string(),
        action_label: Some("Open the workspace".to_string()),
        action_url: Some("https://app.ufo.ai".to_string()),
    }
}

fn draft() -> Draft {
    Draft {
        name: "welcome_drip".to_string(),
        event: "member_invited".to_string(),
        steps: vec![
            step(86_400, "welcome_day_one"),
            step(345_600, "welcome_day_four"),
        ],
    }
}

#[tokio::test]
async fn an_edited_revision_leaves_the_approved_set_until_it_is_approved_again() {
    let ledger = Sequences {
        pool: ledger_pool().await,
    };
    let created = ledger.create(OPERATOR, draft()).await.unwrap();
    assert_eq!(created.revision, 1);
    assert_eq!(created.approved_revision, None);
    assert!(
        ledger.approved().await.unwrap().sequences.is_empty(),
        "nothing goes out before an operator signs off"
    );

    let approved = ledger.approve(created.id, 1, OPERATOR).await.unwrap();
    assert_eq!(approved.approved_revision, Some(1));
    assert_eq!(approved.approved_by.as_deref(), Some(OPERATOR));
    let live = ledger.approved().await.unwrap().sequences;
    assert_eq!(live.len(), 1);
    assert_eq!(live[0].steps, draft().steps);
    assert_eq!(
        live[0].id, created.id,
        "the runner keys an enrolment on the row, not on the name it holds today"
    );

    let reworded = Draft {
        steps: vec![step(86_400, "welcome_day_one")],
        ..draft()
    };
    let revised = ledger.revise(created.id, 1, reworded).await.unwrap();
    assert_eq!(revised.revision, 2);
    assert_eq!(
        revised.approved_revision, None,
        "editing drops the approval"
    );
    assert_eq!(
        ledger.approved().await.unwrap().sequences[0].steps,
        draft().steps,
        "the runner keeps the approved copy: an edit in progress must not end a live enrolment"
    );

    ledger.approve(created.id, 2, OPERATOR).await.unwrap();
    assert_eq!(ledger.approved().await.unwrap().sequences[0].steps.len(), 1);
}

#[tokio::test]
async fn a_step_links_its_button_at_the_portal_placeholder_or_an_https_url() {
    let ledger = Sequences {
        pool: ledger_pool().await,
    };
    let at_the_portal = Draft {
        steps: vec![Step {
            action_url: Some(URL_PLACEHOLDER.to_string()),
            ..step(86_400, "welcome_day_one")
        }],
        ..draft()
    };
    let created = ledger.create(OPERATOR, at_the_portal).await.unwrap();
    assert_eq!(
        created.steps[0].action_url.as_deref(),
        Some(URL_PLACEHOLDER),
        "the runner replaces it with this deploy's portal, so the editor may save it"
    );

    let elsewhere = Draft {
        steps: vec![Step {
            action_url: Some("http://app.ufo.ai".to_string()),
            ..step(86_400, "welcome_day_one")
        }],
        ..draft()
    };
    let refused = ledger.create(OPERATOR, elsewhere).await.unwrap_err();
    assert!(
        matches!(refused, SequenceError::Refused(stated) if stated.contains("https")),
        "nothing else but an https link"
    );
}

#[tokio::test]
async fn a_retired_sequence_sends_nothing_and_keeps_its_words() {
    let ledger = Sequences {
        pool: ledger_pool().await,
    };
    let created = ledger.create(OPERATOR, draft()).await.unwrap();
    ledger.approve(created.id, 1, OPERATOR).await.unwrap();
    let retired = ledger.retire(created.id).await.unwrap();

    assert!(retired.retired_at.is_some());
    assert!(ledger.approved().await.unwrap().sequences.is_empty());
    assert_eq!(ledger.list().await.unwrap().len(), 1, "the record stays");
}

#[tokio::test]
async fn an_edit_against_a_revision_that_moved_is_refused() {
    let ledger = Sequences {
        pool: ledger_pool().await,
    };
    let created = ledger.create(OPERATOR, draft()).await.unwrap();
    ledger.revise(created.id, 1, draft()).await.unwrap();

    let stale = ledger.revise(created.id, 1, draft()).await.unwrap_err();
    assert!(
        matches!(
            stale,
            SequenceError::Stale {
                held: 2,
                stated: 1,
                ..
            }
        ),
        "a second editor's save cannot silently overwrite the first"
    );
    let stale_approval = ledger.approve(created.id, 1, OPERATOR).await.unwrap_err();
    assert!(
        matches!(stale_approval, SequenceError::Stale { .. }),
        "approving names the revision being approved"
    );
}

#[tokio::test]
async fn a_malformed_sequence_is_refused_with_the_reason() {
    let ledger = Sequences {
        pool: ledger_pool().await,
    };
    for (broken, expected) in [
        (
            Draft {
                name: "Welcome Drip".to_string(),
                ..draft()
            },
            "lowercase letters",
        ),
        (
            Draft {
                steps: Vec::new(),
                ..draft()
            },
            "between one and",
        ),
        (
            Draft {
                steps: vec![step(345_600, "later"), step(86_400, "earlier")],
                ..draft()
            },
            "later than the one before",
        ),
        (
            Draft {
                steps: vec![Step {
                    subject: "   ".to_string(),
                    ..step(86_400, "welcome_day_one")
                }],
                ..draft()
            },
            "subject",
        ),
    ] {
        let refusal = ledger.create(OPERATOR, broken).await.unwrap_err();
        let stated = refusal.to_string().to_lowercase();
        assert!(
            stated.contains(expected),
            "{stated:?} does not name {expected:?}"
        );
    }
    assert!(ledger.list().await.unwrap().is_empty());
}

#[tokio::test]
async fn one_name_belongs_to_one_sequence() {
    let ledger = Sequences {
        pool: ledger_pool().await,
    };
    ledger.create(OPERATOR, draft()).await.unwrap();
    let refusal = ledger.create(OPERATOR, draft()).await.unwrap_err();
    assert!(
        matches!(refusal, SequenceError::Refused(stated) if stated.contains("welcome_drip")),
        "a sequence is resolved by name, so two cannot hold one"
    );
}

#[tokio::test]
async fn a_rename_takes_the_approved_copy_with_it_so_one_name_answers_to_one_sequence() {
    let ledger = Sequences {
        pool: ledger_pool().await,
    };
    let created = ledger.create(OPERATOR, draft()).await.unwrap();
    ledger.approve(created.id, 1, OPERATOR).await.unwrap();

    let reworded = Draft {
        steps: vec![step(86_400, "welcome_day_one")],
        ..draft()
    };
    ledger.revise(created.id, 1, reworded).await.unwrap();
    assert_eq!(
        ledger.approved().await.unwrap().sequences.len(),
        1,
        "editing the words keeps the approved copy running"
    );

    let renamed = Draft {
        name: "second_drip".to_string(),
        ..draft()
    };
    ledger.revise(created.id, 2, renamed).await.unwrap();
    assert!(
        ledger.approved().await.unwrap().sequences.is_empty(),
        "a rename sends the words back for approval under the name they will arrive under"
    );

    let taken = ledger
        .create(
            OPERATOR,
            Draft {
                name: "welcome_drip".to_string(),
                ..draft()
            },
        )
        .await
        .unwrap();
    ledger.approve(taken.id, 1, OPERATOR).await.unwrap();
    let live = ledger.approved().await.unwrap().sequences;
    assert_eq!(live.len(), 1, "the freed name belongs to one sequence");
    assert_eq!(live[0].name, "welcome_drip");
    assert_eq!(
        live[0].id, taken.id,
        "and it is the second sequence's own row, so the first one's enrolments resolve to nothing"
    );
}
