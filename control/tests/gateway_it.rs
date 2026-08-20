//! The gateway's routes and the one state machine behind them.
//!
//! The router is served on a real socket and driven over HTTP, with a local server standing in for
//! core's onboarding RPC and a real Postgres holding the ledgers. What a member types reaches the
//! machine the way it will in production — headers, cookies, body caps and all — so the screens
//! asserted here are the screens they get.

mod harness;

use harness::{ledger_pool, spawn_http};
use reqwest::redirect::Policy;
use reqwest::StatusCode;
use ufo_control::claim::ClaimWorkflow;
use ufo_control::gateway::{
    parse_invite_required, router, stamped_script, GatewayState, Onboarding, BILLING_CHOICE,
    FIRST_MOVE_PROMPT, MAX_BODY_BYTES, OPERATOR_EMAIL_DOMAIN, WORKSPACE_PROMPT,
};
use ufo_control::invite::InviteCodes;
use ufo_control::shared::SharedWorkspaces;
use ufo_control::store::OnboardStore;
use ufo_control::web::{
    LOGIN_PAGE, LOGO_PATH, LOGO_PNG_BYTES, LOGO_PNG_PATH, ONBOARD_SESSION_COOKIE,
};
use ufo_control::workos::{Verifier, WorkosVerifier, CONSOLE_CODE};

const SECRET: &str = "local-dev-token-secret";
const APEX: &str = "flyingobject.ai";

/// A gateway on its own socket, with its own database and its own two stand-in services.
struct Rig {
    base: String,
    pool: deadpool_postgres::Pool,
}

async fn rig(workos: Vec<(u16, String)>, serve: Vec<(u16, String)>, gate: bool) -> Rig {
    let pool = ledger_pool().await;
    let (workos_base, _) = spawn_http(workos).await;
    let (serve_base, _) = spawn_http(serve).await;
    let store = OnboardStore::new(pool.clone());
    let state = GatewayState {
        onboarding: Onboarding {
            claims: ClaimWorkflow::new(
                store.clone(),
                Verifier::Workos(WorkosVerifier {
                    api_key: "sk_test".to_string(),
                    client_id: "client_1".to_string(),
                    redirect_uri: "https://flyingobject.ai/v1/onboard/auth/callback".to_string(),
                    base_url: workos_base,
                }),
            ),
            store,
            workspaces: SharedWorkspaces {
                workspace_url: "https://app.flyingobject.ai".to_string(),
                serve_internal_url: serve_base,
                control_token: "onboard-token".to_string(),
            },
            invites: InviteCodes::new(pool.clone()),
            verifier: Verifier::Console,
            token_secret: SECRET.to_string(),
            apex_host: APEX.to_string(),
            invite_required: gate,
        },
        stamped_script: stamped_script("https://testing.flyingobject.ai"),
        client_bin_dir: None,
        client_version: String::new(),
        console_mode: true,
    };
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    tokio::spawn(async move {
        let _ = axum::serve(listener, router(state)).await;
    });
    Rig { base, pool }
}

fn client() -> reqwest::Client {
    reqwest::Client::builder()
        .redirect(Policy::none())
        .build()
        .unwrap()
}

/// One terminal turn: the session header the client carries, and the body it typed.
async fn turn(rig: &Rig, session: &str, body: &str) -> String {
    let response = client()
        .post(format!("{}/v1/onboard/terminal", rig.base))
        .header("x-ufo-session", session)
        .header("x-ufo-installed", "1")
        .body(body.to_string())
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    response.text().await.unwrap()
}

#[tokio::test]
async fn healthz_answers_ok_while_the_ledger_is_reachable() {
    let rig = rig(vec![], vec![], true).await;
    let response = client()
        .get(format!("{}/healthz", rig.base))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.text().await.unwrap(), r#"{"status":"ok"}"#);
}

#[tokio::test]
async fn the_installer_is_served_stamped_with_this_deploys_base_url() {
    let rig = rig(vec![], vec![], true).await;
    let response = client()
        .get(format!("{}/ufo", rig.base))
        .send()
        .await
        .unwrap();
    assert_eq!(
        response
            .headers()
            .get("content-type")
            .unwrap()
            .to_str()
            .unwrap(),
        "text/x-shellscript"
    );
    let script = response.text().await.unwrap();
    assert!(script.starts_with("#!/bin/sh"));
    assert!(
        script.contains(r#"UFO_URL="${UFO_URL:-https://testing.flyingobject.ai}""#),
        "the deploy's own base URL is written in"
    );
    assert!(
        !script.contains(r#"UFO_URL="${UFO_URL:-https://ufo.ai}""#),
        "the default was replaced, not appended"
    );
}

#[tokio::test]
async fn an_unconfigured_binary_directory_answers_the_same_404_an_unknown_target_does() {
    let rig = rig(vec![], vec![], true).await;
    for target in ["aarch64-apple-darwin", "sparc-unknown-none"] {
        let response = client()
            .get(format!("{}/ufo/bin/{target}", rig.base))
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::NOT_FOUND);
        assert!(response.text().await.unwrap().contains(target));
    }
}

#[tokio::test]
async fn the_fleet_reads_through_the_onboarding_rpc() {
    let rig = rig(vec![], vec![(200, r#"{"craft":7}"#.to_string())], true).await;
    let response = client()
        .get(format!("{}/fleet", rig.base))
        .send()
        .await
        .unwrap();
    assert_eq!(response.text().await.unwrap(), r#"{"craft":7}"#);
}

#[tokio::test]
async fn an_unreachable_workspace_service_makes_the_fleet_unavailable_not_wrong() {
    let rig = rig(vec![], vec![], true).await;
    let response = client()
        .get(format!("{}/fleet", rig.base))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
}

#[tokio::test]
async fn the_login_page_is_served_as_html() {
    let rig = rig(vec![], vec![], true).await;
    let response = client()
        .get(format!("{}/login", rig.base))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert!(response
        .text()
        .await
        .unwrap()
        .starts_with("<!doctype html>"));
}

#[tokio::test]
async fn the_invitation_logo_is_served_as_a_png() {
    let rig = rig(vec![], vec![], true).await;
    let response = client()
        .get(format!("{}{LOGO_PNG_PATH}", rig.base))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.headers()["content-type"], "image/png");
    assert_eq!(response.bytes().await.unwrap().as_ref(), LOGO_PNG_BYTES);
}

#[tokio::test]
async fn a_turn_with_no_session_header_says_so() {
    let rig = rig(vec![], vec![], true).await;
    let response = client()
        .post(format!("{}/v1/onboard/terminal", rig.base))
        .body("")
        .send()
        .await
        .unwrap();
    let screen = response.text().await.unwrap();
    assert!(
        screen.contains("x-ufo-session header is required."),
        "{screen}"
    );
    assert!(screen.contains("exit\t1"), "{screen}");
}

#[tokio::test]
async fn an_oversized_channel_or_session_is_refused_rather_than_truncated() {
    let rig = rig(vec![], vec![], true).await;
    let long = "s".repeat(200);
    let response = client()
        .post(format!("{}/v1/onboard/terminal", rig.base))
        .header("x-ufo-session", &long)
        .body("")
        .send()
        .await
        .unwrap();
    assert!(response
        .text()
        .await
        .unwrap()
        .contains("Session is too long."));

    let channel = "c".repeat(100);
    let response = client()
        .post(format!("{}/v1/onboard/{channel}", rig.base))
        .header("x-ufo-session", "s")
        .body("")
        .send()
        .await
        .unwrap();
    assert!(response
        .text()
        .await
        .unwrap()
        .contains("Channel is too long."));
}

#[tokio::test]
async fn an_oversized_body_is_refused_rather_than_graded() {
    // A truncated address or code would be graded as if the member typed it.
    let rig = rig(vec![], vec![], true).await;
    let response = client()
        .post(format!("{}/v1/onboard/terminal", rig.base))
        .header("x-ufo-session", "s")
        .body("x".repeat(MAX_BODY_BYTES + 1))
        .send()
        .await
        .unwrap();
    assert!(response
        .text()
        .await
        .unwrap()
        .contains("Request body is too large."));
}

#[tokio::test]
async fn an_empty_first_turn_asks_for_the_work_email() {
    let rig = rig(vec![], vec![], true).await;
    let screen = turn(&rig, "session-1", "").await;
    assert!(screen.contains("say\tflyingobject.ai"), "{screen}");
    assert!(screen.contains("ask\tEnter your work email:"), "{screen}");
}

#[tokio::test]
async fn a_denylisted_address_is_refused_with_no_code_sent() {
    // No WorkOS response is queued, so a code that was mailed would hang the turn.
    let rig = rig(vec![], vec![], true).await;
    let screen = turn(&rig, "session-1", "someone@gmail.com").await;
    assert!(screen.contains("not a work email domain"), "{screen}");
    assert!(screen.contains("ask\tEnter your work email:"), "{screen}");
}

#[tokio::test]
async fn a_work_address_earns_a_code_prompt() {
    let rig = rig(vec![(200, r#"{"id":"m1"}"#.to_string())], vec![], true).await;
    let screen = turn(&rig, "session-1", "  Founder@Acme.com ").await;
    assert!(
        screen.contains("say\tWe emailed a code to founder@acme.com"),
        "{screen}"
    );
    assert!(screen.contains("ask\tEnter the code:"), "{screen}");
}

#[tokio::test]
async fn a_wrong_code_asks_again_and_a_right_one_moves_on() {
    let rig = rig(
        vec![
            (200, r#"{"id":"m1"}"#.to_string()),
            (400, r#"{"error":"invalid_grant"}"#.to_string()),
        ],
        vec![],
        true,
    )
    .await;
    turn(&rig, "session-1", "founder@acme.com").await;
    let screen = turn(&rig, "session-1", "000000").await;
    assert!(
        screen.contains("The verification code is incorrect."),
        "{screen}"
    );
    assert!(screen.contains("ask\tEnter the code:"), "{screen}");
}

#[tokio::test]
async fn a_verified_address_with_no_invite_is_refused_and_pointed_at_the_waitlist() {
    let rig = rig(
        vec![
            (200, r#"{"id":"m1"}"#.to_string()),
            (200, r#"{"user":{"email":"founder@acme.com"}}"#.to_string()),
        ],
        vec![(200, r#"{"choices":[]}"#.to_string())],
        true,
    )
    .await;
    turn(&rig, "session-1", "founder@acme.com").await;
    let screen = turn(&rig, "session-1", "123456").await;
    assert!(screen.contains("acme.com has no invite."), "{screen}");
    assert!(
        screen.contains(&format!("Join the waitlist: https://{APEX}")),
        "{screen}"
    );
    assert!(screen.contains("exit\t0"), "{screen}");
}

#[tokio::test]
async fn a_granted_domain_founds_its_workspace_and_signs_the_founder_in() {
    let rig = rig(
        vec![
            (200, r#"{"id":"m1"}"#.to_string()),
            (200, r#"{"user":{"email":"founder@acme.com"}}"#.to_string()),
        ],
        vec![
            (200, r#"{"choices":[]}"#.to_string()),
            (
                200,
                r#"{"workspace_id":"3e38d44d-322e-53af-97b6-6204849f6a5c","admin":true,"founding":true}"#
                    .to_string(),
            ),
        ],
        true,
    )
    .await;
    InviteCodes::new(rig.pool.clone())
        .mint(None, "founder@acme.com", None)
        .await
        .unwrap();

    turn(&rig, "session-1", "founder@acme.com").await;
    let screen = turn(&rig, "session-1", "123456").await;
    assert!(
        screen.contains("say\tSigned in: founder@acme.com"),
        "{screen}"
    );
    assert!(
        screen.contains("workspace\thttps://app.flyingobject.ai"),
        "{screen}"
    );
    assert!(screen.contains("token\t"), "{screen}");
    // An admin on a terminal caps on the first-move menu, so billing costs one selection.
    assert!(
        screen.contains(&format!("choose\t{FIRST_MOVE_PROMPT}")),
        "{screen}"
    );
    assert!(screen.contains(BILLING_CHOICE), "{screen}");
    // The sign-in that seated the first member carries the first run; the browser opens the
    // workspace there.
    assert!(screen.contains("first\t1"), "{screen}");
}

#[tokio::test]
async fn a_teammate_joining_an_existing_workspace_carries_no_first_run() {
    let rig = rig(
        vec![
            (200, r#"{"id":"m1"}"#.to_string()),
            (200, r#"{"user":{"email":"second@acme.com"}}"#.to_string()),
        ],
        vec![
            (
                200,
                r#"{"choices":[{"workspace_id":"3e38d44d-322e-53af-97b6-6204849f6a5c","label":"acme.com","member":true}]}"#
                    .to_string(),
            ),
            (200, r#"{"admin":false}"#.to_string()),
        ],
        true,
    )
    .await;

    turn(&rig, "session-2", "second@acme.com").await;
    let screen = turn(&rig, "session-2", "123456").await;
    assert!(
        screen.contains("say\tSigned in: second@acme.com"),
        "{screen}"
    );
    // The first run belongs to the sign-in that founded the workspace. A teammate joining one that
    // already stands lands where every later sign-in does.
    assert!(!screen.contains("first\t"), "{screen}");
}

#[tokio::test]
async fn the_minted_bearer_verifies_as_the_signed_in_member() {
    let rig = rig(
        vec![
            (200, r#"{"id":"m1"}"#.to_string()),
            (200, r#"{"user":{"email":"founder@acme.com"}}"#.to_string()),
        ],
        vec![
            (200, r#"{"choices":[]}"#.to_string()),
            (
                200,
                r#"{"workspace_id":"3e38d44d-322e-53af-97b6-6204849f6a5c","admin":true,"founding":true}"#
                    .to_string(),
            ),
        ],
        true,
    )
    .await;
    InviteCodes::new(rig.pool.clone())
        .mint(None, "founder@acme.com", None)
        .await
        .unwrap();
    turn(&rig, "session-1", "founder@acme.com").await;
    let screen = turn(&rig, "session-1", "123456").await;

    let token = screen
        .lines()
        .find_map(|line| line.strip_prefix("token\t"))
        .expect("a token line");
    // The body is the claim every surface verifies; this is the one it names.
    let body = token.split_once('.').unwrap().0;
    let decoded = base64_decode(body);
    assert!(
        decoded.contains(r#""email":"founder@acme.com""#),
        "{decoded}"
    );
    assert!(
        decoded.contains(r#""ws":"3e38d44d-322e-53af-97b6-6204849f6a5c""#),
        "{decoded}"
    );
}

fn base64_decode(value: &str) -> String {
    use base64::Engine;
    let padded = value.to_string();
    String::from_utf8(
        base64::engine::general_purpose::URL_SAFE_NO_PAD
            .decode(padded)
            .unwrap(),
    )
    .unwrap()
}

#[tokio::test]
async fn an_operator_address_is_handed_the_debug_surface() {
    let operator = format!("staff@{OPERATOR_EMAIL_DOMAIN}");
    let rig = rig(
        vec![
            (200, r#"{"id":"m1"}"#.to_string()),
            (200, format!(r#"{{"user":{{"email":"{operator}"}}}}"#)),
        ],
        vec![
            (200, r#"{"choices":[]}"#.to_string()),
            (
                200,
                r#"{"workspace_id":"3e38d44d-322e-53af-97b6-6204849f6a5c","admin":true,"founding":true}"#
                    .to_string(),
            ),
        ],
        false,
    )
    .await;
    turn(&rig, "session-1", &operator).await;
    let screen = turn(&rig, "session-1", "123456").await;
    assert!(
        screen.contains("debugger\thttps://app.flyingobject.ai/surface/debug"),
        "{screen}"
    );
}

#[tokio::test]
async fn an_admin_domain_claim_lands_on_the_web_portal() {
    // The debug surface used to be the destination of every sign-in that carried no `?c=`, no `?a=`
    // and no first-run mark, so a member on the admin email domain could never reach the portal at
    // all. The claim hands the page a workspace and the operator capability; the portal is where
    // the page posts the session, and the debug surface is reached only by a member who asked.
    let operator = format!("staff@{OPERATOR_EMAIL_DOMAIN}");
    let rig = rig(
        vec![
            (200, r#"{"id":"m1"}"#.to_string()),
            (200, format!(r#"{{"user":{{"email":"{operator}"}}}}"#)),
        ],
        vec![
            (200, r#"{"choices":[]}"#.to_string()),
            (
                200,
                r#"{"workspace_id":"3e38d44d-322e-53af-97b6-6204849f6a5c","admin":true,"founding":false}"#
                    .to_string(),
            ),
        ],
        false,
    )
    .await;
    turn(&rig, "session-1", &operator).await;
    let screen = turn(&rig, "session-1", "123456").await;
    assert!(
        screen.contains("workspace\thttps://app.flyingobject.ai"),
        "{screen}"
    );
    assert!(LOGIN_PAGE.contains("const portalUrl = workspace + '/surface/web' +"));
    assert!(LOGIN_PAGE.contains("portal.action = debug && debuggerUrl ? debuggerUrl : portalUrl;"));
    assert!(
        !LOGIN_PAGE.contains("debuggerUrl || portalUrl"),
        "a sign-in that asked for nothing must never fall back to the debug surface"
    );
}

#[tokio::test]
async fn a_teammate_with_one_candidate_joins_without_a_prompt() {
    let rig = rig(
        vec![
            (200, r#"{"id":"m1"}"#.to_string()),
            (200, r#"{"user":{"email":"teammate@acme.com"}}"#.to_string()),
        ],
        vec![
            (
                200,
                r#"{"choices":[{"workspace_id":"3e38d44d-322e-53af-97b6-6204849f6a5c","label":"acme.com","member":true}]}"#
                    .to_string(),
            ),
            (200, r#"{"admin":false}"#.to_string()),
        ],
        true,
    )
    .await;
    turn(&rig, "session-1", "teammate@acme.com").await;
    let screen = turn(&rig, "session-1", "123456").await;
    assert!(
        screen.contains("say\tSigned in: teammate@acme.com"),
        "{screen}"
    );
    // Billing is not a joined teammate's to set up, so they cap on the ordinary prompt.
    assert!(!screen.contains("choose\tWhat first?"), "{screen}");
    assert!(screen.contains("ask\t>"), "{screen}");
    assert!(!screen.contains("slack\t"), "{screen}");
}

/// One web turn: the page's POST with the session cookie it holds, answering the payload and the
/// cookie the response re-minted, if any.
async fn web_turn(
    rig: &Rig,
    cookie: Option<&str>,
    body: &str,
) -> (serde_json::Value, Option<String>) {
    let mut request = client()
        .post(format!("{}/v1/onboard/web", rig.base))
        .body(body.to_string());
    if let Some(cookie) = cookie {
        request = request.header("cookie", cookie);
    }
    let response = request.send().await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let minted = response.headers().get("set-cookie").map(|value| {
        value
            .to_str()
            .unwrap()
            .split(';')
            .next()
            .unwrap()
            .to_string()
    });
    let payload = serde_json::from_str(&response.text().await.unwrap()).unwrap();
    (payload, minted)
}

#[tokio::test]
async fn the_web_sign_in_caps_on_the_handoff_not_a_prompt() {
    let rig = rig(
        vec![
            (200, r#"{"id":"m1"}"#.to_string()),
            (200, r#"{"user":{"email":"teammate@acme.com"}}"#.to_string()),
        ],
        vec![
            (
                200,
                r#"{"choices":[{"workspace_id":"3e38d44d-322e-53af-97b6-6204849f6a5c","label":"acme.com","member":true}]}"#
                    .to_string(),
            ),
            (200, r#"{"admin":false}"#.to_string()),
        ],
        true,
    )
    .await;
    let (_, minted) = web_turn(&rig, None, "teammate@acme.com").await;
    let cookie = minted.expect("the email turn binds a session");
    let (payload, _) = web_turn(&rig, Some(&cookie), "123456").await;
    let verbs: Vec<&str> = payload["directives"]
        .as_array()
        .unwrap()
        .iter()
        .map(|directive| directive["verb"].as_str().unwrap())
        .collect();
    assert!(verbs.contains(&"token"), "{payload}");
    assert!(verbs.contains(&"workspace"), "{payload}");
    assert!(
        !verbs.contains(&"ask") && !verbs.contains(&"choose"),
        "the page posts the token the moment it lands; a trailing prompt renders as a ghost workspace step: {payload}"
    );
}

#[tokio::test]
async fn two_candidates_are_offered_and_an_unlisted_answer_re_asks() {
    let choices = r#"{"choices":[
        {"workspace_id":"3e38d44d-322e-53af-97b6-6204849f6a5c","label":"acme.com","member":true},
        {"workspace_id":"f5fab2f5-db56-5fe3-bab8-1c70f57ff526","label":"other.com","member":true}]}"#;
    let rig = rig(
        vec![
            (200, r#"{"id":"m1"}"#.to_string()),
            (
                200,
                r#"{"user":{"email":"contractor@acme.com"}}"#.to_string(),
            ),
        ],
        vec![
            (200, choices.to_string()),
            (200, choices.to_string()),
            (200, choices.to_string()),
            (200, r#"{"admin":false}"#.to_string()),
        ],
        true,
    )
    .await;
    turn(&rig, "session-1", "contractor@acme.com").await;
    let offered = turn(&rig, "session-1", "123456").await;
    assert!(
        offered.contains(&format!("choose\t{WORKSPACE_PROMPT}")),
        "{offered}"
    );
    assert!(offered.contains("acme.com"), "{offered}");
    assert!(offered.contains("other.com"), "{offered}");

    let scolded = turn(&rig, "session-1", "nowhere.com").await;
    assert!(scolded.contains("Choose a listed workspace."), "{scolded}");

    let entered = turn(&rig, "session-1", "other.com").await;
    assert!(
        entered.contains("say\tSigned in: contractor@acme.com"),
        "{entered}"
    );
}

#[tokio::test]
async fn the_web_channel_mints_a_host_only_session_cookie() {
    let rig = rig(vec![], vec![], true).await;
    let response = client()
        .post(format!("{}/v1/onboard/web", rig.base))
        .body("")
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let cookie = response
        .headers()
        .get("set-cookie")
        .expect("a session is minted")
        .to_str()
        .unwrap()
        .to_string();
    assert!(cookie.starts_with(ONBOARD_SESSION_COOKIE), "{cookie}");
    assert!(cookie.contains("HttpOnly"), "{cookie}");
    assert!(cookie.contains("Secure"), "{cookie}");
    assert!(cookie.contains("SameSite=Lax"), "{cookie}");
    assert!(
        !cookie.to_lowercase().contains("domain="),
        "a __Host- cookie must carry no Domain: {cookie}"
    );

    let payload: serde_json::Value = serde_json::from_str(&response.text().await.unwrap()).unwrap();
    let verbs: Vec<&str> = payload["directives"]
        .as_array()
        .unwrap()
        .iter()
        .map(|directive| directive["verb"].as_str().unwrap())
        .collect();
    assert_eq!(
        verbs,
        vec!["say", "ask"],
        "the browser reads the same machine"
    );
}

#[tokio::test]
async fn a_planted_cookie_standing_behind_no_claim_is_discarded_and_re_minted() {
    // A validly sealed value a caller obtained by asking must not key a claim it did not start.
    let rig = rig(vec![], vec![], true).await;
    let first = client()
        .post(format!("{}/v1/onboard/web", rig.base))
        .body("")
        .send()
        .await
        .unwrap();
    let minted = first
        .headers()
        .get("set-cookie")
        .unwrap()
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_string();

    let replayed = client()
        .post(format!("{}/v1/onboard/web", rig.base))
        .header("cookie", &minted)
        .body("")
        .send()
        .await
        .unwrap();
    assert!(
        replayed.headers().get("set-cookie").is_some(),
        "a cookie behind no live claim is re-minted"
    );
}

#[tokio::test]
async fn the_google_hop_binds_its_session_to_this_browser() {
    let rig = rig(vec![], vec![], true).await;
    let response = client()
        .get(format!(
            "{}/v1/onboard/auth/start?c=6f1c8038-1111-4222-8333-444455556666",
            rig.base
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::TEMPORARY_REDIRECT);
    let cookie = response
        .headers()
        .get("set-cookie")
        .unwrap()
        .to_str()
        .unwrap();
    assert!(cookie.starts_with(ONBOARD_SESSION_COOKIE), "{cookie}");
    let location = response
        .headers()
        .get("location")
        .unwrap()
        .to_str()
        .unwrap();
    // Console mode routes to the local stand-in, carrying the signed state.
    assert!(
        location.starts_with("/v1/onboard/auth/console?state="),
        "{location}"
    );
}

#[tokio::test]
async fn a_callback_carrying_a_state_we_never_signed_is_refused() {
    let rig = rig(vec![], vec![], true).await;
    let response = client()
        .get(format!(
            "{}/v1/onboard/auth/callback?state=forged.deadbeef&code=x",
            rig.base
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert!(response
        .text()
        .await
        .unwrap()
        .contains("The sign-in link is not valid."));
}

#[tokio::test]
async fn a_callback_without_the_bound_cookie_refuses_and_lands_on_the_page() {
    let rig = rig(vec![], vec![], true).await;
    // A state this gateway signed, replayed in a browser that never left.
    let start = client()
        .get(format!("{}/v1/onboard/auth/start?first=1", rig.base))
        .send()
        .await
        .unwrap();
    let location = start.headers().get("location").unwrap().to_str().unwrap();
    let state = location.split("state=").nth(1).unwrap().to_string();

    let response = client()
        .get(format!(
            "{}/v1/onboard/auth/callback?state={state}&code=founder%40acme.com",
            rig.base
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::SEE_OTHER);
    let landing = response
        .headers()
        .get("location")
        .unwrap()
        .to_str()
        .unwrap();
    assert!(landing.starts_with("/login?error="), "{landing}");
    assert!(landing.ends_with("&first=1"), "{landing}");
}

#[tokio::test]
async fn the_console_stand_in_is_mounted_only_under_console_mode() {
    let rig = rig(vec![], vec![], true).await;
    let response = client()
        .get(format!("{}/v1/onboard/auth/console?state=abc", rig.base))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let page = response.text().await.unwrap();
    assert!(page.contains("<h1>Developer sign-in</h1>"), "{page}");
    // The code the console verifier accepts is named on the screen rather than typed from memory.
    assert!(page.contains(CONSOLE_CODE), "{page}");
    assert!(
        page.contains(&format!("<img src=\"{LOGO_PATH}\"")),
        "{page}"
    );
}

#[tokio::test]
async fn the_mark_is_served_as_a_raster_for_the_readers_that_refuse_the_vector() {
    // An invitation draws this one: mail clients block SVG, so a message pointing at the vector
    // shows its recipient nothing. It is the same artwork, served as PNG at its own path.
    let rig = rig(vec![], vec![], true).await;
    let response = reqwest::get(format!("{}{LOGO_PNG_PATH}", rig.base))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .unwrap(),
        "image/png"
    );
    let bytes = response.bytes().await.unwrap();
    assert_eq!(bytes.as_ref(), LOGO_PNG_BYTES);
    assert!(bytes.starts_with(b"\x89PNG"), "not a png");
}

#[test]
fn the_invite_gate_defaults_to_required_and_refuses_a_value_that_is_not_a_boolean() {
    // Unset means required, so a deploy that forgets the knob never opens signup by accident.
    for closed in ["", "true", "TRUE", " 1 "] {
        assert!(parse_invite_required(closed).unwrap(), "{closed:?}");
    }
    for open in ["false", "FALSE", "0"] {
        assert!(!parse_invite_required(open).unwrap(), "{open:?}");
    }
    // Garbage fails loud rather than falling to either side of a gate on signup.
    for garbage in ["yes", "no", "maybe", "2"] {
        let refused = parse_invite_required(garbage).unwrap_err();
        assert!(refused.contains("is not a boolean"), "{refused}");
    }
}
