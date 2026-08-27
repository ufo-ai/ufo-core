//! Sign-in: the signed state and cookie seals, the four WorkOS calls, and the claim workflow the
//! machine drives them through.
//!
//! The WorkOS half runs against a local server rather than a fake verifier, so the request bodies
//! and the `grant_type` literals WorkOS actually accepts are what the assertions cover. The claim
//! half runs against a real Postgres for the same reason its ledger tests do: every write that could
//! race another attempt is a conditional one, and only the database arbitrates it.

mod harness;

use harness::{ledger_pool, spawn_http};
use ufo_control::claim::{
    ClaimError, ClaimWorkflow, CODE_EXPIRED, CODE_INCORRECT, VERIFICATION_CHANGED,
};
use ufo_control::store::OnboardStore;
use ufo_control::web::WEB_CHANNEL;
use ufo_control::workos::{
    console_signin_page, open_session, pack_state, seal_session, unpack_state, AuthCarry,
    StateError, Verifier, WorkosVerifier, AUTH_CALLBACK_PATH, AUTH_CONSOLE_PATH, CONSOLE_CODE,
    GOOGLE_PROVIDER, MAX_STATE_SESSION_BYTES,
};

const SECRET: &str = "local-dev-token-secret";
const CONVERSATION: &str = "6f1c8038-1111-4222-8333-444455556666";

fn carry(session: &str, conversation: Option<&str>, artifact: Option<&str>) -> AuthCarry {
    AuthCarry {
        session: session.to_string(),
        conversation: conversation.map(str::to_string),
        artifact: artifact.map(str::to_string),
        first_run: false,
        debug: false,
        invite: false,
        join: false,
    }
}

#[test]
fn a_packed_state_round_trips_its_carry() {
    let original = AuthCarry {
        first_run: true,
        debug: true,
        invite: true,
        join: true,
        ..carry("session-1", Some(CONVERSATION), Some("/artifacts/abc"))
    };
    let unpacked = unpack_state(&pack_state(&original, SECRET), SECRET).unwrap();
    assert_eq!(unpacked, original);
}

#[test]
fn a_state_signed_with_another_secret_is_not_ours() {
    let packed = pack_state(&carry("session-1", None, None), SECRET);
    assert_eq!(
        unpack_state(&packed, "another-secret"),
        Err(StateError::NotOurs)
    );
}

#[test]
fn a_tampered_state_body_is_refused() {
    let packed = pack_state(&carry("session-1", None, None), SECRET);
    let (body, signature) = packed.split_once('.').unwrap();
    let forged = format!("{body}x.{signature}");
    assert_eq!(unpack_state(&forged, SECRET), Err(StateError::NotOurs));
    assert_eq!(unpack_state("", SECRET), Err(StateError::NotOurs));
    assert_eq!(unpack_state("nodot", SECRET), Err(StateError::NotOurs));
}

#[test]
fn a_state_naming_no_session_is_refused() {
    let empty = pack_state(&carry("", None, None), SECRET);
    assert_eq!(unpack_state(&empty, SECRET), Err(StateError::NoSession));

    let oversized = pack_state(
        &carry(&"s".repeat(MAX_STATE_SESSION_BYTES + 1), None, None),
        SECRET,
    );
    assert_eq!(
        unpack_state(&oversized, SECRET),
        Err(StateError::OversizedSession)
    );
}

#[test]
fn a_carry_the_query_invented_is_dropped_rather_than_refused() {
    // The member still signs in; they land on a new conversation instead of somewhere invented.
    let invented = carry("session-1", Some("not-a-uuid"), Some("https://elsewhere/x"));
    let unpacked = unpack_state(&pack_state(&invented, SECRET), SECRET).unwrap();
    assert_eq!(unpacked.session, "session-1");
    assert!(unpacked.conversation.is_none());
    assert!(unpacked.artifact.is_none());
}

#[test]
fn a_uuid_shaped_conversation_survives_and_a_near_miss_does_not() {
    for accepted in [CONVERSATION, "00000000-0000-0000-0000-000000000000"] {
        let unpacked = unpack_state(
            &pack_state(&carry("s", Some(accepted), None), SECRET),
            SECRET,
        )
        .unwrap();
        assert_eq!(unpacked.conversation.as_deref(), Some(accepted));
    }
    for refused in [
        "6F1C8038-1111-4222-8333-444455556666", // uppercase is not the shape written
        "6f1c8038-1111-4222-8333-44445555666",  // one short
        "6f1c8038-1111-4222-8333-4444555566667",
        "6f1c8038111142228333444455556666",
        "6f1c8038-1111-4222-8333-44445555666g",
        "6f1c8038-1111-4222-8333-444455556666-extra",
    ] {
        let unpacked = unpack_state(
            &pack_state(&carry("s", Some(refused), None), SECRET),
            SECRET,
        )
        .unwrap();
        assert!(unpacked.conversation.is_none(), "{refused} was carried");
    }
}

#[test]
fn a_sealed_session_opens_only_under_our_own_signature() {
    let sealed = seal_session("minted-id", SECRET);
    assert_eq!(open_session(&sealed, SECRET).as_deref(), Some("minted-id"));
    assert!(open_session(&sealed, "another-secret").is_none());
    assert!(open_session("minted-id", SECRET).is_none(), "unsigned");
    assert!(
        open_session("minted-id.deadbeef", SECRET).is_none(),
        "forged"
    );
    assert!(open_session(".signature", SECRET).is_none(), "empty id");
}

#[test]
fn the_cookie_seal_and_the_state_seal_come_out_of_different_keys() {
    // One key for both would let a state be replayed as a cookie, or the reverse.
    let sealed = seal_session("shared-value", SECRET);
    let cookie_signature = sealed.split_once('.').unwrap().1;
    let state = pack_state(&carry("shared-value", None, None), SECRET);
    let state_signature = state.split_once('.').unwrap().1;
    assert_ne!(cookie_signature, state_signature);
}

#[test]
fn the_console_page_carries_its_state_escaped_to_the_real_callback() {
    let page = console_signin_page("a\"b<c&d");
    assert!(page.contains(&format!("action=\"{AUTH_CALLBACK_PATH}\"")));
    assert!(page.contains("a&quot;b&lt;c&amp;d"), "{page}");
    assert!(!page.contains("a\"b<c&d"), "the raw state reached the page");
}

#[test]
fn the_console_verifier_takes_its_own_code_and_nothing_else() {
    let verifier = Verifier::Console;
    let url = verifier.authorization_url("st=ate&x");
    assert!(url.starts_with(AUTH_CONSOLE_PATH));
    assert!(url.contains("st%3Date%26x"), "{url}");
}

#[tokio::test]
async fn the_console_verifier_confirms_only_its_fixed_code() {
    let verifier = Verifier::Console;
    assert!(verifier
        .confirm("founder@acme.com", CONSOLE_CODE)
        .await
        .unwrap());
    assert!(verifier
        .confirm("founder@acme.com", &format!("  {CONSOLE_CODE} "))
        .await
        .unwrap());
    assert!(!verifier
        .confirm("founder@acme.com", "111111")
        .await
        .unwrap());
    // The console exchange reads the typed address straight back as the identity.
    assert_eq!(
        verifier.exchange("  Founder@Acme.com ").await.unwrap(),
        "founder@acme.com"
    );
}

fn workos(base: &str) -> Verifier {
    Verifier::Workos(WorkosVerifier {
        api_key: "sk_test_key".to_string(),
        client_id: "client_123".to_string(),
        redirect_uri: "https://flyingobject.ai/v1/onboard/auth/callback".to_string(),
        base_url: base.to_string(),
    })
}

#[test]
fn the_authorization_url_routes_straight_to_google() {
    let Verifier::Workos(verifier) = workos("https://api.workos.com") else {
        unreachable!()
    };
    let url = verifier.authorization_url("packed.state");
    assert!(url.starts_with("https://api.workos.com/user_management/authorize?"));
    assert!(url.contains(&format!("provider={GOOGLE_PROVIDER}")));
    assert!(url.contains("response_type=code"));
    assert!(url.contains("client_id=client_123"));
    assert!(url.contains("state=packed.state"));
    // The redirect URI is a full URL, so it has to survive as one parameter.
    assert!(
        url.contains("redirect_uri=https%3A%2F%2Fflyingobject.ai%2Fv1%2Fonboard%2Fauth%2Fcallback"),
        "{url}"
    );
}

#[tokio::test]
async fn exchange_posts_the_authorization_code_grant_and_reads_the_email() {
    let (base, log) = spawn_http(vec![(
        200,
        r#"{"user":{"email":"  Founder@Acme.COM "}}"#.to_string(),
    )])
    .await;
    let email = workos(&base).exchange("code_abc").await.unwrap();
    assert_eq!(email, "founder@acme.com");

    let exchanges = log.lock().unwrap();
    assert_eq!(exchanges[0].path, "/user_management/authenticate");
    let body: serde_json::Value = serde_json::from_str(&exchanges[0].body).unwrap();
    assert_eq!(body["grant_type"], "authorization_code");
    assert_eq!(body["client_id"], "client_123");
    assert_eq!(body["client_secret"], "sk_test_key");
    assert_eq!(body["code"], "code_abc");
    assert_eq!(
        exchanges[0].authorization.as_deref(),
        Some("Bearer sk_test_key")
    );
}

#[tokio::test]
async fn a_refused_exchange_is_a_sign_in_the_member_cannot_retype_past() {
    let (base, _log) = spawn_http(vec![(400, r#"{"error":"invalid_grant"}"#.to_string())]).await;
    let refused = workos(&base).exchange("code_abc").await.unwrap_err();
    assert_eq!(refused.to_string(), "Sign-in failed. Try again.");
}

#[tokio::test]
async fn begin_sends_the_magic_auth_code_to_the_address() {
    let (base, log) = spawn_http(vec![(200, r#"{"id":"magic_auth_1"}"#.to_string())]).await;
    workos(&base).begin("founder@acme.com").await.unwrap();
    let exchanges = log.lock().unwrap();
    assert_eq!(exchanges[0].path, "/user_management/magic_auth");
    let body: serde_json::Value = serde_json::from_str(&exchanges[0].body).unwrap();
    assert_eq!(body["email"], "founder@acme.com");
}

#[tokio::test]
async fn a_refused_begin_says_the_code_was_not_sent() {
    let (base, _log) = spawn_http(vec![(422, r#"{"code":"invalid"}"#.to_string())]).await;
    let refused = workos(&base).begin("founder@acme.com").await.unwrap_err();
    assert_eq!(
        refused.to_string(),
        "Could not send the verification code. Try again."
    );
}

#[tokio::test]
async fn confirm_posts_the_magic_auth_grant() {
    let (base, log) = spawn_http(vec![(
        200,
        r#"{"user":{"email":"f@acme.com"}}"#.to_string(),
    )])
    .await;
    assert!(workos(&base).confirm("f@acme.com", "123456").await.unwrap());
    let exchanges = log.lock().unwrap();
    let body: serde_json::Value = serde_json::from_str(&exchanges[0].body).unwrap();
    assert_eq!(
        body["grant_type"], "urn:workos:oauth:grant-type:magic-auth:code",
        "the grant type WorkOS accepts for a magic auth code"
    );
    assert_eq!(body["code"], "123456");
    assert_eq!(body["email"], "f@acme.com");
}

#[tokio::test]
async fn an_invalid_grant_is_a_wrong_code_and_anything_else_is_a_refusal() {
    // WorkOS answers a code it will not redeem with `invalid_grant` whether the digits are wrong,
    // expired, or spent — so that answer alone is the one the member may retype past.
    for field in ["error", "code"] {
        let (base, _log) =
            spawn_http(vec![(400, format!(r#"{{"{field}":"invalid_grant"}}"#))]).await;
        assert!(
            !workos(&base).confirm("f@acme.com", "000000").await.unwrap(),
            "an invalid_grant in {field} must read as a wrong code"
        );
    }

    let (base, _log) = spawn_http(vec![(401, r#"{"code":"unauthorized"}"#.to_string())]).await;
    let refused = workos(&base)
        .confirm("f@acme.com", "000000")
        .await
        .unwrap_err();
    assert_eq!(refused.to_string(), "Sign-in failed. Try again.");
}

/// A claim workflow whose verifier is a real WorkOS client pointed at a local server.
async fn workflow(responses: Vec<(u16, String)>) -> (ClaimWorkflow, OnboardStore) {
    let pool = ledger_pool().await;
    let store = OnboardStore::new(pool);
    let (base, _log) = spawn_http(responses).await;
    (ClaimWorkflow::new(store.clone(), workos(&base)), store)
}

#[tokio::test]
async fn starting_a_claim_writes_it_and_asks_workos_to_mail_the_code() {
    let (flow, store) = workflow(vec![(200, r#"{"id":"m1"}"#.to_string())]).await;
    let domain = flow
        .start("Founder@Acme.com", WEB_CHANNEL, "session-1")
        .await
        .unwrap();
    assert_eq!(domain, "acme.com");

    let claim = store
        .live_claim(WEB_CHANNEL, "session-1")
        .await
        .unwrap()
        .unwrap();
    assert_eq!(claim.email, "founder@acme.com", "the address is normalized");
    assert_eq!(claim.email_domain, "acme.com");
    assert!(claim.verified_at.is_none(), "the terminal earns its stamp");
}

#[tokio::test]
async fn a_disposable_address_never_reaches_workos() {
    let (flow, store) = workflow(vec![]).await;
    let refused = flow
        .start("someone@mailinator.com", WEB_CHANNEL, "session-1")
        .await
        .unwrap_err();
    assert!(matches!(refused, ClaimError::Email(_)), "{refused}");
    assert!(store
        .live_claim(WEB_CHANNEL, "session-1")
        .await
        .unwrap()
        .is_none());
}

#[tokio::test]
async fn a_verifier_that_cannot_send_takes_the_claim_with_it() {
    // Otherwise the session holds a claim whose code was never mailed, and the member is stuck.
    let (flow, store) = workflow(vec![(500, "boom".to_string())]).await;
    let refused = flow
        .start("founder@acme.com", WEB_CHANNEL, "session-1")
        .await
        .unwrap_err();
    assert!(refused.to_string().contains("Could not send"), "{refused}");
    assert!(store
        .live_claim(WEB_CHANNEL, "session-1")
        .await
        .unwrap()
        .is_none());
}

#[tokio::test]
async fn a_correct_code_stamps_the_claim_once() {
    let (flow, store) = workflow(vec![
        (200, r#"{"id":"m1"}"#.to_string()),
        (200, r#"{"user":{"email":"founder@acme.com"}}"#.to_string()),
    ])
    .await;
    flow.start("founder@acme.com", WEB_CHANNEL, "session-1")
        .await
        .unwrap();
    let claim = store
        .live_claim(WEB_CHANNEL, "session-1")
        .await
        .unwrap()
        .unwrap();
    flow.verify(&claim, "123456").await.unwrap();

    let stamped = store
        .live_claim(WEB_CHANNEL, "session-1")
        .await
        .unwrap()
        .unwrap();
    assert!(stamped.verified_at.is_some());

    // A replay of the same code finds the claim already stamped and says so.
    let refused = flow.verify(&claim, "123456").await.unwrap_err();
    assert_eq!(refused.to_string(), VERIFICATION_CHANGED);
}

#[tokio::test]
async fn a_wrong_code_leaves_the_claim_open_to_retype() {
    let (flow, store) = workflow(vec![
        (200, r#"{"id":"m1"}"#.to_string()),
        (400, r#"{"error":"invalid_grant"}"#.to_string()),
    ])
    .await;
    flow.start("founder@acme.com", WEB_CHANNEL, "session-1")
        .await
        .unwrap();
    let claim = store
        .live_claim(WEB_CHANNEL, "session-1")
        .await
        .unwrap()
        .unwrap();
    let refused = flow.verify(&claim, "000000").await.unwrap_err();
    assert_eq!(refused.to_string(), CODE_INCORRECT);
    // The claim survives, so the member types the code again rather than starting over.
    assert!(store
        .live_claim(WEB_CHANNEL, "session-1")
        .await
        .unwrap()
        .is_some());
}

#[tokio::test]
async fn a_claim_past_its_window_ends_and_says_so() {
    let (flow, store) = workflow(vec![(200, r#"{"id":"m1"}"#.to_string())]).await;
    flow.start("founder@acme.com", WEB_CHANNEL, "session-1")
        .await
        .unwrap();
    let mut claim = store
        .live_claim(WEB_CHANNEL, "session-1")
        .await
        .unwrap()
        .unwrap();
    claim.expires_at = chrono::Utc::now() - chrono::Duration::seconds(1);

    let refused = flow.verify(&claim, "123456").await.unwrap_err();
    assert_eq!(refused.to_string(), CODE_EXPIRED);
    assert!(
        store
            .live_claim(WEB_CHANNEL, "session-1")
            .await
            .unwrap()
            .is_none(),
        "an expired unverified claim is taken, freeing the session"
    );
}

#[tokio::test]
async fn a_refusal_that_lost_its_race_reports_the_race_not_a_verdict() {
    let (flow, store) = workflow(vec![(200, r#"{"id":"m1"}"#.to_string())]).await;
    flow.start("founder@acme.com", WEB_CHANNEL, "session-1")
        .await
        .unwrap();
    let mut claim = store
        .live_claim(WEB_CHANNEL, "session-1")
        .await
        .unwrap()
        .unwrap();
    claim.expires_at = chrono::Utc::now() - chrono::Duration::seconds(1);
    // Another attempt already stamped it, so this one cannot delete it.
    store.mark_verified(claim.claim_id).await.unwrap();

    let refused = flow.verify(&claim, "123456").await.unwrap_err();
    assert_eq!(refused.to_string(), VERIFICATION_CHANGED);
}

#[tokio::test]
async fn the_browser_claim_arrives_verified_and_a_repeat_callback_resolves_it() {
    let (flow, store) = workflow(vec![]).await;
    let admitted = flow
        .admit_verified("Founder@Acme.com", WEB_CHANNEL, "session-1")
        .await
        .unwrap();
    assert_eq!(admitted.email, "founder@acme.com");
    assert!(admitted.verified_at.is_some(), "WorkOS already answered");

    // A second callback for the same session lands on the first claim rather than opening another.
    let again = flow
        .admit_verified("Founder@Acme.com", WEB_CHANNEL, "session-1")
        .await
        .unwrap();
    assert_eq!(again.claim_id, admitted.claim_id);
    assert!(store
        .live_claim(WEB_CHANNEL, "session-1")
        .await
        .unwrap()
        .is_some());
}

#[tokio::test]
async fn the_browser_claim_uses_the_address_for_a_personal_mail_subject() {
    let (flow, _store) = workflow(vec![]).await;
    let admitted = flow
        .admit_verified("someone@gmail.com", WEB_CHANNEL, "session-1")
        .await
        .unwrap();
    assert_eq!(admitted.email_domain, "gmail.com");
    assert_eq!(admitted.signup_subject, "someone@gmail.com");
}
