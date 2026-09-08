mod harness;

use harness::{ledger_pool, spawn_http};
use ufo_control::invite::InviteCodes;
use ufo_control::slack_connect::{
    slack_connect_from_env, SlackConnectClient, SlackConnectInviter, BOT_TOKEN_ENV, ENABLED_ENV,
    STATE_DELIVERED, STATE_FAILED, STATE_PENDING, TEAM_ID_ENV,
};

const TEAM: &str = "T0OPERATOR";
const TOKEN: &str = "xoxb-secret-token";

fn ok(body: &str) -> (u16, String) {
    (200, body.to_string())
}

fn auth_ok() -> (u16, String) {
    ok(&format!(r#"{{"ok":true,"team_id":"{TEAM}"}}"#))
}

async fn inviter(pool: deadpool_postgres::Pool, slack: Vec<(u16, String)>) -> SlackConnectInviter {
    let (base, _log) = spawn_http(slack).await;
    SlackConnectInviter {
        pool,
        slack: SlackConnectClient::with_base(TOKEN.to_string(), base),
        team_id: TEAM.to_string(),
        apex_host: "flyingobject.ai".to_string(),
        worker_id: "test-worker".to_string(),
        poll_interval: std::time::Duration::from_millis(10),
    }
}

async fn row(pool: &deadpool_postgres::Pool, domain: &str) -> tokio_postgres::Row {
    pool.get()
        .await
        .unwrap()
        .query_one(
            "select * from ufo_control.slack_connect_delivery where email_domain = $1",
            &[&domain],
        )
        .await
        .unwrap()
}

#[tokio::test]
async fn a_granted_domain_materializes_one_row_with_a_derived_channel_name() {
    let pool = ledger_pool().await;
    InviteCodes::new(pool.clone())
        .mint(None, "founder@acme.com", None)
        .await
        .unwrap();
    let worker = inviter(pool.clone(), vec![]).await;
    worker.poll().await.unwrap();

    let row = row(&pool, "acme.com").await;
    assert_eq!(
        row.get::<_, String>("channel_name"),
        "ext-acme-ufo",
        "the name drops the local part and the TLD"
    );
    assert_eq!(row.get::<_, String>("email"), "founder@acme.com");
}

#[tokio::test]
async fn two_domains_sharing_a_label_yield_one_channel_and_the_second_is_skipped() {
    let pool = ledger_pool().await;
    let invites = InviteCodes::new(pool.clone());
    invites.mint(None, "founder@acme.com", None).await.unwrap();
    invites.mint(None, "founder@acme.io", None).await.unwrap();

    let worker = inviter(pool.clone(), vec![]).await;
    worker.poll().await.unwrap();

    let count: i64 = pool
        .get()
        .await
        .unwrap()
        .query_one(
            "select count(*) from ufo_control.slack_connect_delivery \
             where channel_name = 'ext-acme-ufo'",
            &[],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(count, 1, "one channel name is one row");
}

#[tokio::test]
async fn a_member_who_joined_an_existing_workspace_earns_no_channel() {
    let pool = ledger_pool().await;
    pool.get()
        .await
        .unwrap()
        .execute(
            "insert into ufo_control.onboard_claim \
             (id, email, email_domain, surface, surface_ref, expires_at, created_workspace) \
             values (gen_random_uuid(), 'teammate@acme.com', 'acme.com', 'web', 's1', \
                     now() + interval '10 minutes', false)",
            &[],
        )
        .await
        .unwrap();
    let worker = inviter(pool.clone(), vec![]).await;
    worker.poll().await.unwrap();

    let count: i64 = pool
        .get()
        .await
        .unwrap()
        .query_one(
            "select count(*) from ufo_control.slack_connect_delivery",
            &[],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(count, 0, "only a grant or a founding claim earns a channel");
}

#[tokio::test]
async fn a_personal_mail_grant_earns_no_shared_customer_channel() {
    let pool = ledger_pool().await;
    InviteCodes::new(pool.clone())
        .mint(None, "someone@gmail.com", None)
        .await
        .unwrap();
    let worker = inviter(pool.clone(), vec![]).await;
    worker.poll().await.unwrap();

    let count: i64 = pool
        .get()
        .await
        .unwrap()
        .query_one(
            "select count(*) from ufo_control.slack_connect_delivery",
            &[],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(count, 0);
}

#[tokio::test]
async fn a_personal_mail_workspace_earns_no_shared_customer_channel() {
    let pool = ledger_pool().await;
    pool.get()
        .await
        .unwrap()
        .execute(
            "insert into ufo_control.onboard_claim \
             (id, email, email_domain, surface, surface_ref, expires_at, created_workspace) \
             values (gen_random_uuid(), 'someone@gmail.com', 'someone@gmail.com', 'web', 's2', \
                     now() + interval '10 minutes', true)",
            &[],
        )
        .await
        .unwrap();
    let worker = inviter(pool.clone(), vec![]).await;
    worker.poll().await.unwrap();

    let count: i64 = pool
        .get()
        .await
        .unwrap()
        .query_one(
            "select count(*) from ufo_control.slack_connect_delivery",
            &[],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(count, 0, "a personal-mail subject earns no shared channel");
}

#[tokio::test]
async fn a_full_delivery_opens_the_channel_invites_and_greets() {
    let pool = ledger_pool().await;
    InviteCodes::new(pool.clone())
        .mint(None, "founder@acme.com", None)
        .await
        .unwrap();
    let worker = inviter(
        pool.clone(),
        vec![
            auth_ok(),
            ok(r#"{"ok":true,"channel":{"id":"C123"}}"#),
            ok(r#"{"ok":true,"invite_id":"I456"}"#),
            ok(r#"{"ok":true}"#),
        ],
    )
    .await;
    assert!(worker.poll().await.unwrap());

    let row = row(&pool, "acme.com").await;
    assert_eq!(row.get::<_, String>("state"), STATE_DELIVERED);
    assert_eq!(
        row.get::<_, Option<String>>("channel_id").as_deref(),
        Some("C123")
    );
    assert_eq!(
        row.get::<_, Option<String>>("slack_invitation_id")
            .as_deref(),
        Some("I456")
    );
    assert!(row
        .get::<_, Option<chrono::DateTime<chrono::Utc>>>("greeted_at")
        .is_some());
    assert!(row
        .get::<_, Option<chrono::DateTime<chrono::Utc>>>("delivered_at")
        .is_some());
    assert!(
        row.get::<_, Option<String>>("worker_id").is_none(),
        "the lease is released"
    );
}

#[tokio::test]
async fn a_token_belonging_to_another_team_fails_the_row_before_anything_is_mutated() {
    let pool = ledger_pool().await;
    InviteCodes::new(pool.clone())
        .mint(None, "founder@acme.com", None)
        .await
        .unwrap();
    let worker = inviter(pool.clone(), vec![ok(r#"{"ok":true,"team_id":"T0OTHER"}"#)]).await;
    worker.poll().await.unwrap();

    let row = row(&pool, "acme.com").await;
    assert_eq!(row.get::<_, String>("state"), STATE_FAILED);
    assert!(
        row.get::<_, Option<String>>("channel_id").is_none(),
        "no channel was opened"
    );
    let error = row
        .get::<_, Option<String>>("last_error")
        .unwrap_or_default();
    assert!(error.contains("T0OTHER"), "{error}");
}

#[tokio::test]
async fn a_rate_limit_reschedules_rather_than_failing() {
    let pool = ledger_pool().await;
    InviteCodes::new(pool.clone())
        .mint(None, "founder@acme.com", None)
        .await
        .unwrap();
    let worker = inviter(pool.clone(), vec![(429, r#"{"ok":false}"#.to_string())]).await;
    worker.poll().await.unwrap();

    let row = row(&pool, "acme.com").await;
    assert_eq!(
        row.get::<_, String>("state"),
        STATE_PENDING,
        "it comes back"
    );
    assert!(
        row.get::<_, Option<chrono::DateTime<chrono::Utc>>>("next_attempt_at")
            .is_some(),
        "behind a schedule"
    );
}

#[tokio::test]
async fn a_documented_transient_slack_error_reschedules_and_a_policy_refusal_fails() {
    for (payload, expected) in [
        (r#"{"ok":false,"error":"ratelimited"}"#, STATE_PENDING),
        (
            r#"{"ok":false,"error":"service_unavailable"}"#,
            STATE_PENDING,
        ),
        (r#"{"ok":false,"error":"missing_scope"}"#, STATE_FAILED),
        (
            r#"{"ok":false,"error":"not_allowed_token_type"}"#,
            STATE_FAILED,
        ),
    ] {
        let pool = ledger_pool().await;
        InviteCodes::new(pool.clone())
            .mint(None, "founder@acme.com", None)
            .await
            .unwrap();
        let worker = inviter(pool.clone(), vec![ok(payload)]).await;
        worker.poll().await.unwrap();
        assert_eq!(
            row(&pool, "acme.com").await.get::<_, String>("state"),
            expected,
            "{payload}"
        );
    }
}

#[tokio::test]
async fn a_name_taken_channel_is_resolved_by_its_exact_name() {
    let pool = ledger_pool().await;
    InviteCodes::new(pool.clone())
        .mint(None, "founder@acme.com", None)
        .await
        .unwrap();
    let worker = inviter(
        pool.clone(),
        vec![
            auth_ok(),
            ok(r#"{"ok":false,"error":"name_taken"}"#),
            ok(r#"{"ok":true,"channels":[{"name":"ext-acme-ufo","id":"CEXIST"}]}"#),
            ok(r#"{"ok":true,"invite_id":"I456"}"#),
            ok(r#"{"ok":true}"#),
        ],
    )
    .await;
    worker.poll().await.unwrap();

    let row = row(&pool, "acme.com").await;
    assert_eq!(
        row.get::<_, Option<String>>("channel_id").as_deref(),
        Some("CEXIST")
    );
    assert_eq!(row.get::<_, String>("state"), STATE_DELIVERED);
}

#[tokio::test]
async fn a_retried_row_reconciles_a_live_invite_rather_than_sending_a_second() {
    let pool = ledger_pool().await;
    pool.get()
        .await
        .unwrap()
        .execute(
            "insert into ufo_control.slack_connect_delivery \
             (email_domain, email, state, channel_name, channel_id, invite_attempted_at) \
             values ('acme.com','founder@acme.com','pending','ext-acme-ufo','C123', now())",
            &[],
        )
        .await
        .unwrap();
    let worker = inviter(
        pool.clone(),
        vec![
            auth_ok(),
            ok(r#"{"ok":true,"invites":[{"channel":{"id":"C123"},"status":"sent","invite":{"id":"IPRIOR"}}]}"#),
            ok(r#"{"ok":true}"#),
        ],
    )
    .await;
    worker.poll().await.unwrap();

    let row = row(&pool, "acme.com").await;
    assert_eq!(
        row.get::<_, Option<String>>("slack_invitation_id")
            .as_deref(),
        Some("IPRIOR"),
        "the invitation already sent is adopted, never duplicated"
    );
    assert_eq!(row.get::<_, String>("state"), STATE_DELIVERED);
}

#[tokio::test]
async fn a_dead_invite_is_not_read_as_proof_one_landed() {
    let pool = ledger_pool().await;
    pool.get()
        .await
        .unwrap()
        .execute(
            "insert into ufo_control.slack_connect_delivery \
             (email_domain, email, state, channel_name, channel_id, invite_attempted_at) \
             values ('acme.com','founder@acme.com','pending','ext-acme-ufo','C123', now())",
            &[],
        )
        .await
        .unwrap();
    let worker = inviter(
        pool.clone(),
        vec![
            auth_ok(),
            ok(r#"{"ok":true,"invites":[{"channel":{"id":"C123"},"status":"revoked","invite":{"id":"IDEAD"}}]}"#),
            ok(r#"{"ok":true,"channel":{"is_ext_shared":false}}"#),
            ok(r#"{"ok":true,"invite_id":"IFRESH"}"#),
            ok(r#"{"ok":true}"#),
        ],
    )
    .await;
    worker.poll().await.unwrap();

    let row = row(&pool, "acme.com").await;
    assert_eq!(
        row.get::<_, Option<String>>("slack_invitation_id")
            .as_deref(),
        Some("IFRESH"),
        "a revoked invitation is not proof, so a fresh one is sent"
    );
}

#[tokio::test]
async fn an_unrecognized_invite_status_is_ambiguous_and_fails_for_review() {
    let pool = ledger_pool().await;
    pool.get()
        .await
        .unwrap()
        .execute(
            "insert into ufo_control.slack_connect_delivery \
             (email_domain, email, state, channel_name, channel_id, invite_attempted_at) \
             values ('acme.com','founder@acme.com','pending','ext-acme-ufo','C123', now())",
            &[],
        )
        .await
        .unwrap();
    let worker = inviter(
        pool.clone(),
        vec![
            auth_ok(),
            ok(r#"{"ok":true,"invites":[{"channel":{"id":"C123"},"status":"quantum","invite":{"id":"I"}}]}"#),
        ],
    )
    .await;
    worker.poll().await.unwrap();

    let row = row(&pool, "acme.com").await;
    assert_eq!(row.get::<_, String>("state"), STATE_FAILED);
    let error = row
        .get::<_, Option<String>>("last_error")
        .unwrap_or_default();
    assert!(error.contains("quantum"), "{error}");
}

#[tokio::test]
async fn an_externally_shared_channel_proves_an_invitation_landed() {
    let pool = ledger_pool().await;
    pool.get()
        .await
        .unwrap()
        .execute(
            "insert into ufo_control.slack_connect_delivery \
             (email_domain, email, state, channel_name, channel_id, invite_attempted_at) \
             values ('acme.com','founder@acme.com','pending','ext-acme-ufo','C123', now())",
            &[],
        )
        .await
        .unwrap();
    let worker = inviter(
        pool.clone(),
        vec![
            auth_ok(),
            ok(r#"{"ok":true,"invites":[]}"#),
            ok(r#"{"ok":true,"channel":{"is_pending_ext_shared":true}}"#),
            ok(r#"{"ok":true}"#),
        ],
    )
    .await;
    worker.poll().await.unwrap();
    assert_eq!(
        row(&pool, "acme.com").await.get::<_, String>("state"),
        STATE_DELIVERED,
        "a shared channel settles the row without a second invitation"
    );
}

#[tokio::test]
async fn a_row_at_its_attempt_ceiling_fails_instead_of_rescheduling_forever() {
    let pool = ledger_pool().await;
    pool.get()
        .await
        .unwrap()
        .execute(
            "insert into ufo_control.slack_connect_delivery \
             (email_domain, email, state, channel_name, attempts) \
             values ('acme.com','founder@acme.com','pending','ext-acme-ufo', 8)",
            &[],
        )
        .await
        .unwrap();
    let worker = inviter(pool.clone(), vec![(429, r#"{"ok":false}"#.to_string())]).await;
    worker.poll().await.unwrap();
    assert_eq!(
        row(&pool, "acme.com").await.get::<_, String>("state"),
        STATE_FAILED
    );
}

#[tokio::test]
async fn the_bot_token_never_reaches_a_stored_error() {
    let pool = ledger_pool().await;
    InviteCodes::new(pool.clone())
        .mint(None, "founder@acme.com", None)
        .await
        .unwrap();
    let worker = inviter(
        pool.clone(),
        vec![(400, format!(r#"{{"ok":false,"error":"bad {TOKEN}"}}"#))],
    )
    .await;
    worker.poll().await.unwrap();

    let error = row(&pool, "acme.com")
        .await
        .get::<_, Option<String>>("last_error")
        .unwrap_or_default();
    assert!(!error.contains(TOKEN), "the token was stored: {error}");
    assert!(error.contains("«token»"), "{error}");
}

#[tokio::test]
async fn a_lease_another_worker_holds_is_left_untouched() {
    let pool = ledger_pool().await;
    pool.get()
        .await
        .unwrap()
        .execute(
            "insert into ufo_control.slack_connect_delivery \
             (email_domain, email, state, channel_name, worker_id, claim_expires_at) \
             values ('acme.com','founder@acme.com','claimed','ext-acme-ufo', \
                     'other-worker', now() + interval '2 minutes')",
            &[],
        )
        .await
        .unwrap();
    let worker = inviter(pool.clone(), vec![]).await;
    assert!(
        !worker.poll().await.unwrap(),
        "a live lease held elsewhere is not claimable"
    );
    assert_eq!(
        row(&pool, "acme.com")
            .await
            .get::<_, Option<String>>("worker_id")
            .as_deref(),
        Some("other-worker")
    );
}

#[tokio::test]
async fn a_lapsed_lease_is_taken_again() {
    let pool = ledger_pool().await;
    pool.get()
        .await
        .unwrap()
        .execute(
            "insert into ufo_control.slack_connect_delivery \
             (email_domain, email, state, channel_name, worker_id, claim_expires_at) \
             values ('acme.com','founder@acme.com','claimed','ext-acme-ufo', \
                     'dead-worker', now() - interval '1 minute')",
            &[],
        )
        .await
        .unwrap();
    let worker = inviter(pool.clone(), vec![]).await;
    assert!(
        worker.poll().await.unwrap(),
        "the worker holding it did not survive, so the row is taken again"
    );
}

#[test]
fn the_switch_is_off_by_default_and_garbage_fails_loud() {
    let guard = harness::ROLE_LOCK.try_lock();
    drop(guard);
    let previous: Vec<(&str, Option<String>)> = [ENABLED_ENV, BOT_TOKEN_ENV, TEAM_ID_ENV]
        .iter()
        .map(|name| (*name, std::env::var(name).ok()))
        .collect();

    std::env::remove_var(ENABLED_ENV);
    let pool_free = slack_connect_from_env_unset();
    assert!(pool_free, "unset means off");

    std::env::set_var(ENABLED_ENV, "maybe");
    assert!(
        slack_connect_from_env(dummy_pool(), "flyingobject.ai".to_string(), "w".to_string())
            .is_err(),
        "garbage fails loud"
    );

    std::env::set_var(ENABLED_ENV, "true");
    std::env::remove_var(BOT_TOKEN_ENV);
    assert!(
        slack_connect_from_env(dummy_pool(), "flyingobject.ai".to_string(), "w".to_string())
            .is_err(),
        "enabled without a token fails loud"
    );

    for (name, value) in previous {
        match value {
            Some(value) => std::env::set_var(name, value),
            None => std::env::remove_var(name),
        }
    }
}

fn slack_connect_from_env_unset() -> bool {
    slack_connect_from_env(dummy_pool(), "flyingobject.ai".to_string(), "w".to_string())
        .map(|inviter| inviter.is_none())
        .unwrap_or(false)
}

fn dummy_pool() -> deadpool_postgres::Pool {
    let config: tokio_postgres::Config = "postgresql://ufo:ufo@127.0.0.1:1/none".parse().unwrap();
    let manager = deadpool_postgres::Manager::new(config, tokio_postgres::NoTls);
    deadpool_postgres::Pool::builder(manager).build().unwrap()
}
