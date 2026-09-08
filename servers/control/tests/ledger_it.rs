mod harness;

use chrono::{Duration, Utc};
use harness::ledger_pool;
use ufo_control::invite::{InviteCodes, InviteError, Redemption, SignupProfile, MAX_PROFILE_CHARS};
use ufo_control::schema::{require_control_schema, shape_control_schema, LEDGERS};
use ufo_control::slack_connect::{rearm_failed_delivery, STATE_FAILED};
use ufo_control::store::{OnboardClaim, OnboardStore};
use uuid::Uuid;

fn claim(surface_ref: &str, email: &str) -> OnboardClaim {
    let signup = ufo_control::email::SignupEmailPolicy::default()
        .validate(email)
        .unwrap();
    OnboardClaim {
        claim_id: Uuid::new_v4(),
        email: signup.address,
        email_domain: signup.domain,
        signup_subject: signup.subject,
        surface: "web".to_string(),
        surface_ref: surface_ref.to_string(),
        expires_at: Utc::now() + Duration::minutes(15),
        verified_at: None,
        invite_id: None,
    }
}

#[tokio::test]
async fn shaping_twice_is_a_no_op_and_leaves_the_schema_required() {
    let pool = ledger_pool().await;
    let mut client = pool.get().await.unwrap();
    shape_control_schema(&mut client).await.unwrap();
    require_control_schema(&client).await.unwrap();
    for table in LEDGERS {
        let present: Option<String> = client
            .query_one("select to_regclass($1)::text", &[table])
            .await
            .unwrap()
            .get(0);
        assert!(present.is_some(), "{table} is absent after shaping");
    }
}

#[tokio::test]
async fn reshape_fills_a_subject_omitted_by_either_ledger_writer() {
    let pool = ledger_pool().await;
    let client = pool.get().await.unwrap();
    let claim_id = Uuid::new_v4();
    let invite_id = Uuid::new_v4();
    let expires_at = Utc::now() + Duration::minutes(15);
    client
        .execute(
            "insert into ufo_control.onboard_claim \
             (id, email, email_domain, surface, surface_ref, expires_at) \
             values ($1, 'founder@acme.com', 'acme.com', 'web', 'session', $2)",
            &[&claim_id, &expires_at],
        )
        .await
        .unwrap();
    client
        .execute(
            "insert into ufo_control.invite_code \
             (id, email, email_domain, expires_at) \
             values ($1, 'founder@beta.com', 'beta.com', $2)",
            &[&invite_id, &expires_at],
        )
        .await
        .unwrap();
    let claim_subject: String = client
        .query_one(
            "select signup_subject from ufo_control.onboard_claim where id = $1",
            &[&claim_id],
        )
        .await
        .unwrap()
        .get(0);
    let invite_subject: String = client
        .query_one(
            "select signup_subject from ufo_control.invite_code where id = $1",
            &[&invite_id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(claim_subject, "acme.com");
    assert_eq!(invite_subject, "beta.com");
}

#[tokio::test]
async fn a_missing_ledger_names_the_verb_that_shapes_it() {
    let pool = ledger_pool().await;
    let client = pool.get().await.unwrap();
    client
        .batch_execute("drop table ufo_control.invite_code")
        .await
        .unwrap();
    let refused = require_control_schema(&client)
        .await
        .unwrap_err()
        .to_string();
    assert!(refused.contains("invite_code"), "{refused}");
    assert!(refused.contains("ufo-control migrate"), "{refused}");
}

#[tokio::test]
async fn a_dropped_column_is_drift_the_gateway_refuses_to_serve_against() {
    let pool = ledger_pool().await;
    let client = pool.get().await.unwrap();
    client
        .batch_execute("alter table ufo_control.onboard_claim drop column invite_id")
        .await
        .unwrap();
    let refused = require_control_schema(&client)
        .await
        .unwrap_err()
        .to_string();
    assert!(refused.contains("onboard_claim.invite_id"), "{refused}");
}

#[tokio::test]
async fn a_claim_is_written_read_back_and_verified_once() {
    let pool = ledger_pool().await;
    let store = OnboardStore::new(pool);
    let written = claim("session-a", "founder@acme.com");
    store.insert_claim(&written).await.unwrap();

    let read = store.live_claim("web", "session-a").await.unwrap().unwrap();
    assert_eq!(read.claim_id, written.claim_id);
    assert_eq!(read.email, "founder@acme.com");
    assert_eq!(read.email_domain, "acme.com");
    assert!(read.verified_at.is_none());
    assert!(read.invite_id.is_none());

    assert!(store.mark_verified(written.claim_id).await.unwrap());
    assert!(!store.mark_verified(written.claim_id).await.unwrap());
    let read = store.live_claim("web", "session-a").await.unwrap().unwrap();
    assert!(read.verified_at.is_some());
}

#[tokio::test]
async fn a_personal_mail_claim_is_filed_under_the_subject_the_previous_image_reads() {
    let pool = ledger_pool().await;
    let store = OnboardStore::new(pool.clone());
    let written = claim("session-personal", "carol@gmail.com");
    store.insert_claim(&written).await.unwrap();

    let filed: String = pool
        .get()
        .await
        .unwrap()
        .query_one(
            "select email_domain from ufo_control.onboard_claim where id = $1",
            &[&written.claim_id],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(filed, "carol@gmail.com");

    let read = store
        .live_claim("web", "session-personal")
        .await
        .unwrap()
        .unwrap();
    assert_eq!(read.signup_subject, "carol@gmail.com");
    assert_eq!(
        read.email_domain, "gmail.com",
        "the domain read back is the verified address's own"
    );
}

#[tokio::test]
async fn a_personal_mail_grant_is_filed_under_the_subject_too() {
    let pool = ledger_pool().await;
    InviteCodes::new(pool.clone())
        .mint(None, "carol@gmail.com", None)
        .await
        .unwrap();
    let filed: String = pool
        .get()
        .await
        .unwrap()
        .query_one(
            "select email_domain from ufo_control.invite_code where email = $1",
            &[&"carol@gmail.com"],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(filed, "carol@gmail.com");
}

#[tokio::test]
async fn completing_a_claim_releases_the_session_for_the_next_attempt() {
    let pool = ledger_pool().await;
    let store = OnboardStore::new(pool);
    let first = claim("session-b", "founder@acme.com");
    store.insert_claim(&first).await.unwrap();
    store
        .complete(first.claim_id, "11111111-1111-1111-1111-111111111111", true)
        .await
        .unwrap();

    assert!(store
        .live_claim("web", "session-b")
        .await
        .unwrap()
        .is_none());
    let second = claim("session-b", "second@acme.com");
    store.insert_claim(&second).await.unwrap();
    let read = store.live_claim("web", "session-b").await.unwrap().unwrap();
    assert_eq!(read.claim_id, second.claim_id);
}

#[tokio::test]
async fn two_live_claims_cannot_share_one_session() {
    let pool = ledger_pool().await;
    let store = OnboardStore::new(pool);
    store
        .insert_claim(&claim("session-c", "a@acme.com"))
        .await
        .unwrap();
    let raced = store.insert_claim(&claim("session-c", "b@acme.com")).await;
    assert!(
        raced.is_err(),
        "the active-session index admitted a second live claim"
    );
}

#[tokio::test]
async fn an_unverified_claim_deletes_and_a_verified_one_does_not() {
    let pool = ledger_pool().await;
    let store = OnboardStore::new(pool);
    let fresh = claim("session-d", "a@acme.com");
    store.insert_claim(&fresh).await.unwrap();
    assert!(store.delete_unverified_claim(fresh.claim_id).await.unwrap());

    let verified = claim("session-e", "b@acme.com");
    store.insert_claim(&verified).await.unwrap();
    store.mark_verified(verified.claim_id).await.unwrap();
    assert!(!store
        .delete_unverified_claim(verified.claim_id)
        .await
        .unwrap());
    assert!(store
        .live_claim("web", "session-e")
        .await
        .unwrap()
        .is_some());

    store.delete_claim(verified.claim_id).await.unwrap();
    assert!(store
        .live_claim("web", "session-e")
        .await
        .unwrap()
        .is_none());
}

#[tokio::test]
async fn a_grant_opens_its_domain_and_a_second_live_grant_is_refused() {
    let pool = ledger_pool().await;
    let invites = InviteCodes::new(pool);
    assert!(!invites.available("founder@acme.com").await.unwrap());

    let minted = invites
        .mint(Some(7), "founder@acme.com", None)
        .await
        .unwrap();
    assert_eq!(minted.object_number, Some(7));
    assert_eq!(minted.email, "founder@acme.com");
    assert!(invites.available("founder@acme.com").await.unwrap());

    let refused = invites
        .mint(Some(8), "other@acme.com", None)
        .await
        .unwrap_err();
    assert!(
        matches!(refused, InviteError::LiveGrant { .. }),
        "{refused}"
    );
}

#[tokio::test]
async fn personal_mail_grants_are_isolated_by_address() {
    let pool = ledger_pool().await;
    let invites = InviteCodes::new(pool);
    invites.mint(None, "first@gmail.com", None).await.unwrap();
    invites.mint(None, "second@gmail.com", None).await.unwrap();
    assert!(invites.available("first@gmail.com").await.unwrap());
    assert!(invites.available("second@gmail.com").await.unwrap());
}

#[tokio::test]
async fn the_intake_profile_rides_the_grant() {
    let pool = ledger_pool().await;
    let invites = InviteCodes::new(pool);
    assert!(invites.profile("founder@acme.com").await.unwrap().is_none());

    let collected = SignupProfile {
        business: "we sell widgets".to_string(),
        goals: "answer support mail".to_string(),
    };
    invites
        .mint(None, "founder@acme.com", Some(&collected))
        .await
        .unwrap();
    assert_eq!(
        invites.profile("founder@acme.com").await.unwrap().unwrap(),
        collected
    );

    invites.mint(None, "founder@other.com", None).await.unwrap();
    assert!(invites
        .profile("founder@other.com")
        .await
        .unwrap()
        .is_none());
}

#[tokio::test]
async fn a_regranted_domain_describes_the_customer_as_they_are_now() {
    let pool = ledger_pool().await;
    let invites = InviteCodes::new(pool.clone());
    let stale = SignupProfile {
        business: "we sold widgets".to_string(),
        goals: "answer support mail".to_string(),
    };
    invites
        .mint(None, "founder@acme.com", Some(&stale))
        .await
        .unwrap();

    pool.get()
        .await
        .unwrap()
        .batch_execute(
            "update ufo_control.invite_code set expires_at = now() - interval '1 minute'",
        )
        .await
        .unwrap();
    let fresh = SignupProfile {
        business: "we sell better widgets".to_string(),
        goals: "answer sales mail".to_string(),
    };
    invites
        .mint(None, "founder@acme.com", Some(&fresh))
        .await
        .unwrap();
    assert_eq!(
        invites.profile("founder@acme.com").await.unwrap().unwrap(),
        fresh
    );
}

#[tokio::test]
async fn a_consumed_grant_identifies_its_domain_for_good() {
    let pool = ledger_pool().await;
    let invites = InviteCodes::new(pool.clone());
    let store = OnboardStore::new(pool);
    invites.mint(None, "founder@acme.com", None).await.unwrap();

    let opened = claim("session-p", "founder@acme.com");
    store.insert_claim(&opened).await.unwrap();
    invites
        .redeem("founder@acme.com", opened.claim_id)
        .await
        .unwrap();

    let refused = invites
        .mint(None, "founder@acme.com", None)
        .await
        .unwrap_err();
    assert!(
        matches!(refused, InviteError::AlreadyIdentified(_)),
        "{refused}"
    );
}

#[tokio::test]
async fn a_profile_field_outside_its_bounds_is_refused() {
    let pool = ledger_pool().await;
    let invites = InviteCodes::new(pool);
    for profile in [
        SignupProfile {
            business: String::new(),
            goals: "goals".to_string(),
        },
        SignupProfile {
            business: "business".to_string(),
            goals: "g".repeat(MAX_PROFILE_CHARS + 1),
        },
    ] {
        let refused = invites
            .mint(None, "founder@acme.com", Some(&profile))
            .await
            .unwrap_err();
        assert!(matches!(refused, InviteError::ProfileLength), "{refused}");
    }
}

#[tokio::test]
async fn redeeming_spends_the_grant_once_and_stamps_the_claim() {
    let pool = ledger_pool().await;
    let invites = InviteCodes::new(pool.clone());
    let store = OnboardStore::new(pool);
    invites
        .mint(Some(3), "founder@acme.com", None)
        .await
        .unwrap();

    let mine = claim("session-r", "founder@acme.com");
    store.insert_claim(&mine).await.unwrap();
    let Redemption::Accepted(accepted) = invites
        .redeem("founder@acme.com", mine.claim_id)
        .await
        .unwrap()
    else {
        panic!("a live grant is accepted");
    };
    assert_eq!(accepted.object_number, Some(3));

    let read = store.live_claim("web", "session-r").await.unwrap().unwrap();
    assert_eq!(read.invite_id, Some(accepted.invite_id));

    let again = invites
        .redeem("founder@acme.com", mine.claim_id)
        .await
        .unwrap();
    assert_eq!(again, Redemption::Accepted(accepted));

    let theirs = claim("session-s", "second@acme.com");
    store.insert_claim(&theirs).await.unwrap();
    assert_eq!(
        invites
            .redeem("second@acme.com", theirs.claim_id)
            .await
            .unwrap(),
        Redemption::Consumed
    );
}

#[tokio::test]
async fn an_ungranted_domain_and_an_expired_grant_read_apart() {
    let pool = ledger_pool().await;
    let invites = InviteCodes::new(pool.clone());
    let store = OnboardStore::new(pool.clone());
    let mine = claim("session-t", "founder@acme.com");
    store.insert_claim(&mine).await.unwrap();

    assert_eq!(
        invites
            .redeem("founder@nowhere.com", mine.claim_id)
            .await
            .unwrap(),
        Redemption::Unknown
    );

    invites.mint(None, "founder@acme.com", None).await.unwrap();
    pool.get()
        .await
        .unwrap()
        .batch_execute(
            "update ufo_control.invite_code set expires_at = now() - interval '1 minute'",
        )
        .await
        .unwrap();
    assert!(matches!(
        invites
            .redeem("founder@acme.com", mine.claim_id)
            .await
            .unwrap(),
        Redemption::Expired { .. }
    ));
    assert!(!invites.available("founder@acme.com").await.unwrap());
}

#[tokio::test]
async fn a_failed_slack_delivery_rearms_and_a_delivered_one_does_not() {
    let pool = ledger_pool().await;
    assert!(rearm_failed_delivery(&pool, "acme.com")
        .await
        .unwrap()
        .is_none());

    pool.get()
        .await
        .unwrap()
        .batch_execute(&format!(
            "insert into ufo_control.slack_connect_delivery \
             (email_domain, email, state, channel_name, attempts, last_error) values \
             ('acme.com', 'founder@acme.com', '{STATE_FAILED}', 'ext-acme-ufo', 8, 'nope'), \
             ('other.com', 'founder@other.com', 'delivered', 'ext-other-ufo', 1, null)"
        ))
        .await
        .unwrap();

    let rearmed = rearm_failed_delivery(&pool, "acme.com").await.unwrap();
    assert!(
        rearmed.is_some(),
        "the failed row re-arms and reports when it last moved"
    );

    let client = pool.get().await.unwrap();
    let row = client
        .query_one(
            "select state, attempts, last_error from ufo_control.slack_connect_delivery \
             where email_domain = 'acme.com'",
            &[],
        )
        .await
        .unwrap();
    assert_eq!(row.get::<_, String>("state"), "pending");
    assert_eq!(row.get::<_, i32>("attempts"), 0);
    assert!(row.get::<_, Option<String>>("last_error").is_none());

    assert!(rearm_failed_delivery(&pool, "other.com")
        .await
        .unwrap()
        .is_none());
}
