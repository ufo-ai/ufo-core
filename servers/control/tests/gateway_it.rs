mod harness;

use chrono::{Duration, Utc};
use harness::{ledger_pool, spawn_http};
use reqwest::redirect::Policy;
use reqwest::StatusCode;
use ufo_control::claim::ClaimWorkflow;
use ufo_control::gateway::{
    keyed_mark, parse_invite_required, router, stamped_script, GatewayState, Onboarding,
    BILLING_CHOICE, FIRST_MOVE_PROMPT, INVITATION_LOGIN_PATH, JOIN_LOGIN_PATH, LOGIN_PATH,
    LOGOUT_PATH, MAX_BODY_BYTES, OPERATOR_COOKIE, OPERATOR_EMAIL_DOMAIN, SIGNUP_MARK_TTL_MINUTES,
    WORKSPACE_PROMPT,
};
use ufo_control::invite::InviteCodes;
use ufo_control::shared::SharedWorkspaces;
use ufo_control::store::OnboardStore;
use ufo_control::token::{mint_token, sign, SESSION_COOKIE};
use ufo_control::web::{
    ASSET_CACHE, ILLUSTRATION_BYTES, ILLUSTRATION_PATH, LOGIN_PAGE, LOGO_PATH, LOGO_PNG_BYTES,
    LOGO_PNG_PATH, ONBOARD_SESSION_COOKIE, SHARE_HOME_BYTES, SHARE_HOME_PATH, SHARE_SITE_BYTES,
    SHARE_SITE_PATH,
};
use ufo_control::workos::{seal_session, Verifier, WorkosVerifier, CONSOLE_CODE};

const SECRET: &str = "local-dev-token-secret";
const APEX: &str = "flyingobject.ai";
const WORKSPACE: &str = "11111111-1111-1111-1111-111111111111";
const CONVERSATION: &str = "6f1c8038-1111-4222-8333-444455556666";
const SIGNUP_KEY: &str = "ufo";

struct Rig {
    base: String,
    pool: deadpool_postgres::Pool,
}

async fn rig(workos: Vec<(u16, String)>, serve: Vec<(u16, String)>, gate: bool) -> Rig {
    rig_with(workos, serve, gate, Some(SIGNUP_KEY)).await
}

async fn rig_with(
    workos: Vec<(u16, String)>,
    serve: Vec<(u16, String)>,
    gate: bool,
    signup_key: Option<&str>,
) -> Rig {
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
            signup_key: signup_key.map(str::to_string),
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
async fn a_browser_already_holding_a_session_is_forwarded_where_it_asked_to_land() {
    let rig = rig(vec![], vec![], true).await;
    let held = mint_token(SECRET, WORKSPACE, "dana@acme.com", Utc::now()).unwrap();
    let response = client()
        .get(format!("{}/login?c={CONVERSATION}", rig.base))
        .header("cookie", format!("{SESSION_COOKIE}={held}"))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::SEE_OTHER);
    assert_eq!(
        response.headers()["location"],
        format!("https://app.flyingobject.ai/surface/web?c={CONVERSATION}")
    );
}

#[tokio::test]
async fn an_ask_only_the_page_can_answer_gets_it_even_holding_a_session() {
    let rig = rig(vec![], vec![], true).await;
    let held = mint_token(SECRET, WORKSPACE, "ops@metalcraft.ai", Utc::now()).unwrap();
    for asked in [
        "debug=1",
        "a=%2Fartifacts%2F1%2Freport.pdf",
        INVITATION_LOGIN_PATH.split_once('?').unwrap().1,
    ] {
        let response = client()
            .get(format!("{}{LOGIN_PATH}?{asked}", rig.base))
            .header("cookie", format!("{SESSION_COOKIE}={held}"))
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK, "{asked}");
        assert!(response
            .text()
            .await
            .unwrap()
            .starts_with("<!doctype html>"));
    }
}

#[tokio::test]
async fn a_cookie_that_outlived_its_bearer_gets_the_sign_in_page() {
    let rig = rig(vec![], vec![], true).await;
    let stale = sign(
        SECRET,
        WORKSPACE,
        "dana@acme.com",
        Utc::now() - Duration::days(1),
    )
    .unwrap();
    let response = client()
        .get(format!("{}/login", rig.base))
        .header("cookie", format!("{SESSION_COOKIE}={stale}"))
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
async fn signing_out_expires_the_held_session_and_lands_on_the_form() {
    let rig = rig(vec![], vec![], true).await;
    let held = mint_token(SECRET, WORKSPACE, "dana@acme.com", Utc::now()).unwrap();
    for carried in [Some(format!("{SESSION_COOKIE}={held}")), None] {
        let mut request = client().get(format!("{}{LOGOUT_PATH}", rig.base));
        if let Some(cookie) = &carried {
            request = request.header("cookie", cookie);
        }
        let response = request.send().await.unwrap();
        assert_eq!(response.status(), StatusCode::SEE_OTHER);
        assert_eq!(response.headers()["location"], LOGIN_PATH);
        let cleared: Vec<&str> = response
            .headers()
            .get_all("set-cookie")
            .iter()
            .map(|value| value.to_str().unwrap())
            .collect();
        for name in [SESSION_COOKIE, OPERATOR_COOKIE, ONBOARD_SESSION_COOKIE] {
            let expired = cleared
                .iter()
                .find(|cookie| cookie.starts_with(&format!("{name}=;")))
                .unwrap_or_else(|| panic!("{name} is not cleared: {cleared:?}"));
            assert!(expired.contains("Max-Age=0"), "{expired}");
            assert!(expired.contains("Path=/"), "{expired}");
        }
    }
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
async fn the_sign_in_illustration_is_served_as_a_webp() {
    let rig = rig(vec![], vec![], true).await;
    let response = client()
        .get(format!("{}{ILLUSTRATION_PATH}", rig.base))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.headers()["content-type"], "image/webp");
    assert_eq!(response.bytes().await.unwrap().as_ref(), ILLUSTRATION_BYTES);
}

#[tokio::test]
async fn the_share_cards_are_served_anonymously_and_cached_for_good() {
    let rig = rig(vec![], vec![], true).await;
    for (path, card) in [
        (SHARE_HOME_PATH, SHARE_HOME_BYTES),
        (SHARE_SITE_PATH, SHARE_SITE_BYTES),
    ] {
        let response = client()
            .get(format!("{}{path}", rig.base))
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK, "{path}");
        assert_eq!(response.headers()["content-type"], "image/jpeg");
        assert_eq!(response.headers()["cache-control"], ASSET_CACHE);
        assert!(
            response.headers().get("set-cookie").is_none(),
            "{path} binds a session"
        );
        let bytes = response.bytes().await.unwrap();
        assert_eq!(bytes.as_ref(), card);
        assert!(bytes.starts_with(b"\xff\xd8\xff"), "{path} is not a jpeg");
    }
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
    // A body over the cap is refused rather than truncated: a truncated address or code would be graded
    // as if the member typed it.
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
async fn an_empty_first_turn_asks_for_the_email() {
    let rig = rig(vec![], vec![], true).await;
    let screen = turn(&rig, "session-1", "").await;
    assert!(screen.contains("say\tflyingobject.ai"), "{screen}");
    assert!(screen.contains("ask\tEnter your email:"), "{screen}");
}

#[tokio::test]
async fn a_disposable_address_is_refused_with_no_code_sent() {
    let rig = rig(vec![], vec![], true).await;
    let screen = turn(&rig, "session-1", "someone@mailinator.com").await;
    assert!(screen.contains("disposable email domain"), "{screen}");
    assert!(screen.contains("ask\tEnter your email:"), "{screen}");
}

#[tokio::test]
async fn a_personal_address_earns_a_code_prompt() {
    let rig = rig(vec![(200, r#"{"id":"m1"}"#.to_string())], vec![], true).await;
    let screen = turn(&rig, "session-1", "  Someone@Gmail.com ").await;
    assert!(
        screen.contains("say\tWe emailed a code to someone@gmail.com"),
        "{screen}"
    );
    assert!(screen.contains("ask\tEnter the code:"), "{screen}");
}

#[tokio::test]
async fn an_organization_address_earns_a_code_prompt() {
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
    assert!(
        screen.contains(&format!("choose\t{FIRST_MOVE_PROMPT}")),
        "{screen}"
    );
    assert!(screen.contains(BILLING_CHOICE), "{screen}");
    assert!(screen.contains("first\t1"), "{screen}");
}

#[tokio::test]
async fn a_teammate_joining_an_existing_workspace_carries_no_first_run() {
    let rig = rig(
        vec![
            (200, r#"{"id":"m1"}"#.to_string()),
            (200, r#"{"user":{"email":"second@gmail.com"}}"#.to_string()),
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

    turn(&rig, "session-2", "second@gmail.com").await;
    let screen = turn(&rig, "session-2", "123456").await;
    assert!(
        screen.contains("say\tSigned in: second@gmail.com"),
        "{screen}"
    );
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
    assert!(!screen.contains("choose\tWhat first?"), "{screen}");
    assert!(screen.contains("ask\t>"), "{screen}");
    assert!(!screen.contains("slack\t"), "{screen}");
}

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
async fn the_invitation_ask_survives_the_google_hop_it_was_carried_into() {
    let rig = rig(vec![], vec![], true).await;
    let start = client()
        .get(format!("{}/v1/onboard/auth/start?invite=1", rig.base))
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
    let landing = response
        .headers()
        .get("location")
        .unwrap()
        .to_str()
        .unwrap();
    assert!(landing.ends_with("&invite=1"), "{landing}");
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
    assert!(page.contains(CONSOLE_CODE), "{page}");
    assert!(
        page.contains(&format!("<img src=\"{LOGO_PATH}\"")),
        "{page}"
    );
}

#[tokio::test]
async fn the_mark_is_served_as_a_raster_for_the_readers_that_refuse_the_vector() {
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
    for closed in ["", "true", "TRUE", " 1 "] {
        assert!(parse_invite_required(closed).unwrap(), "{closed:?}");
    }
    for open in ["false", "FALSE", "0"] {
        assert!(!parse_invite_required(open).unwrap(), "{open:?}");
    }
    for garbage in ["yes", "no", "maybe", "2"] {
        let refused = parse_invite_required(garbage).unwrap_err();
        assert!(refused.contains("is not a boolean"), "{refused}");
    }
}

async fn join(rig: &Rig, key: &str) -> (StatusCode, Option<String>) {
    let response = client()
        .get(format!("{}/join/{key}", rig.base))
        .send()
        .await
        .unwrap();
    let cookie = response.headers().get("set-cookie").map(|value| {
        value
            .to_str()
            .unwrap()
            .split(';')
            .next()
            .unwrap()
            .to_string()
    });
    (response.status(), cookie)
}

#[tokio::test]
async fn the_join_door_binds_a_session_and_sends_the_browser_to_the_sign_in_page() {
    let rig = rig(vec![], vec![], true).await;
    let response = client()
        .get(format!("{}/join/{SIGNUP_KEY}", rig.base))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::SEE_OTHER);
    assert_eq!(
        response
            .headers()
            .get("location")
            .unwrap()
            .to_str()
            .unwrap(),
        JOIN_LOGIN_PATH
    );
    assert_eq!(
        response
            .headers()
            .get("referrer-policy")
            .unwrap()
            .to_str()
            .unwrap(),
        "no-referrer"
    );
    let cookie = response
        .headers()
        .get("set-cookie")
        .unwrap()
        .to_str()
        .unwrap();
    assert!(cookie.starts_with(ONBOARD_SESSION_COOKIE), "{cookie}");
    assert!(cookie.contains("HttpOnly"), "{cookie}");
    assert!(cookie.contains("Secure"), "{cookie}");
}

#[tokio::test]
async fn a_key_that_does_not_match_is_answered_as_an_unrouted_path() {
    let rig = rig(vec![], vec![], true).await;
    let (status, cookie) = join(&rig, "not-the-key").await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert!(cookie.is_none(), "{cookie:?}");
}

#[tokio::test]
async fn a_deploy_that_configures_no_key_serves_no_join_door() {
    let rig = rig_with(vec![], vec![], true, None).await;
    let (status, cookie) = join(&rig, SIGNUP_KEY).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert!(cookie.is_none(), "{cookie:?}");
}

#[tokio::test]
async fn the_signup_key_founds_the_domain_workspace_with_no_operator_grant() {
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
    let (status, cookie) = join(&rig, SIGNUP_KEY).await;
    assert_eq!(status, StatusCode::SEE_OTHER);
    let cookie = cookie.expect("the door binds a session");

    let (_, carried) = web_turn(&rig, Some(&cookie), "founder@acme.com").await;
    let cookie = carried.unwrap_or(cookie);
    let (payload, _) = web_turn(&rig, Some(&cookie), "123456").await;
    let verbs: Vec<&str> = payload["directives"]
        .as_array()
        .unwrap()
        .iter()
        .map(|directive| directive["verb"].as_str().unwrap())
        .collect();
    assert!(verbs.contains(&"token"), "{payload}");
    assert!(verbs.contains(&"workspace"), "{payload}");

    let connection = rig.pool.get().await.unwrap();
    let rows = connection
        .query(
            "select email, consumed_at, object_number, business \
             from ufo_control.invite_code where email_domain = $1",
            &[&"acme.com"],
        )
        .await
        .unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].get::<_, String>("email"), "founder@acme.com");
    assert!(rows[0]
        .get::<_, Option<chrono::DateTime<Utc>>>("consumed_at")
        .is_some());
    assert!(rows[0].get::<_, Option<i32>>("object_number").is_none());
    assert!(rows[0].get::<_, Option<String>>("business").is_none());
}

#[tokio::test]
async fn the_signup_key_founds_an_address_workspace_for_gmail() {
    let rig = rig(
        vec![
            (200, r#"{"id":"m1"}"#.to_string()),
            (200, r#"{"user":{"email":"someone@gmail.com"}}"#.to_string()),
        ],
        vec![
            (200, r#"{"choices":[]}"#.to_string()),
            (
                200,
                r#"{"workspace_id":"c9ff4df7-cade-5134-aa05-67f2689645d7","admin":true,"founding":true}"#
                    .to_string(),
            ),
        ],
        true,
    )
    .await;
    let (_, cookie) = join(&rig, SIGNUP_KEY).await;
    let cookie = cookie.expect("the door binds a session");
    let (_, carried) = web_turn(&rig, Some(&cookie), "someone@gmail.com").await;
    let cookie = carried.unwrap_or(cookie);
    let (payload, _) = web_turn(&rig, Some(&cookie), "123456").await;
    assert!(
        payload.to_string().contains("\"verb\":\"token\""),
        "{payload}"
    );

    let connection = rig.pool.get().await.unwrap();
    let filed = connection
        .query_one(
            "select signup_subject, email_domain from ufo_control.invite_code where email = $1",
            &[&"someone@gmail.com"],
        )
        .await
        .unwrap();
    let subject: String = filed.get("signup_subject");
    let identity: String = filed.get("email_domain");
    assert_eq!(subject, "someone@gmail.com");
    assert_eq!(identity, "someone@gmail.com");
}

#[tokio::test]
async fn a_browser_that_never_opened_the_join_link_is_still_refused() {
    let rig = rig(
        vec![
            (200, r#"{"id":"m1"}"#.to_string()),
            (200, r#"{"user":{"email":"founder@acme.com"}}"#.to_string()),
        ],
        vec![(200, r#"{"choices":[]}"#.to_string())],
        true,
    )
    .await;
    let (_, cookie) = web_turn(&rig, None, "founder@acme.com").await;
    let cookie = cookie.expect("the email turn binds a session");
    let (payload, _) = web_turn(&rig, Some(&cookie), "123456").await;
    assert!(
        payload.to_string().contains("acme.com has no invite."),
        "{payload}"
    );
}

#[tokio::test]
async fn the_signup_key_survives_the_google_hop() {
    let rig = rig(
        vec![],
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
    let (_, cookie) = join(&rig, SIGNUP_KEY).await;
    let cookie = cookie.expect("the door binds a session");

    let start = client()
        .get(format!("{}/v1/onboard/auth/start", rig.base))
        .header("cookie", &cookie)
        .send()
        .await
        .unwrap();
    let hopped = start
        .headers()
        .get("set-cookie")
        .unwrap()
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_string();
    let location = start.headers().get("location").unwrap().to_str().unwrap();
    let state = location.split("state=").nth(1).unwrap().to_string();

    let returned = client()
        .get(format!(
            "{}/v1/onboard/auth/callback?state={state}&code=founder%40acme.com",
            rig.base
        ))
        .header("cookie", &hopped)
        .send()
        .await
        .unwrap();
    assert_eq!(returned.status(), StatusCode::SEE_OTHER);

    let (payload, _) = web_turn(&rig, Some(&hopped), "").await;
    let verbs: Vec<&str> = payload["directives"]
        .as_array()
        .unwrap()
        .iter()
        .map(|directive| directive["verb"].as_str().unwrap())
        .collect();
    assert!(verbs.contains(&"token"), "{payload}");
}

#[tokio::test]
async fn a_keyed_session_offers_creation_beside_a_membership_it_already_holds() {
    let rig = rig(
        vec![
            (200, r#"{"id":"m1"}"#.to_string()),
            (200, r#"{"user":{"email":"founder@acme.com"}}"#.to_string()),
        ],
        vec![(
            200,
            r#"{"choices":[{"workspace_id":"3e38d44d-322e-53af-97b6-6204849f6a5c","label":"other.example","member":true}]}"#
                .to_string(),
        )],
        true,
    )
    .await;
    let (_, cookie) = join(&rig, SIGNUP_KEY).await;
    let cookie = cookie.expect("the door binds a session");
    let (_, carried) = web_turn(&rig, Some(&cookie), "founder@acme.com").await;
    let cookie = carried.unwrap_or(cookie);
    let (payload, _) = web_turn(&rig, Some(&cookie), "123456").await;
    assert!(
        payload.to_string().contains("Create acme.com workspace"),
        "{payload}"
    );
}

#[tokio::test]
async fn a_keyed_session_writes_no_second_grant_over_a_spent_one() {
    let rig = rig(
        vec![
            (200, r#"{"id":"m1"}"#.to_string()),
            (200, r#"{"user":{"email":"second@acme.com"}}"#.to_string()),
        ],
        vec![(200, r#"{"choices":[]}"#.to_string())],
        true,
    )
    .await;
    let invites = InviteCodes::new(rig.pool.clone());
    invites.mint(None, "founder@acme.com", None).await.unwrap();
    invites
        .redeem("founder@acme.com", uuid::Uuid::new_v4())
        .await
        .unwrap();

    let (_, cookie) = join(&rig, SIGNUP_KEY).await;
    let cookie = cookie.expect("the door binds a session");
    let (_, carried) = web_turn(&rig, Some(&cookie), "second@acme.com").await;
    let cookie = carried.unwrap_or(cookie);
    let (payload, _) = web_turn(&rig, Some(&cookie), "123456").await;
    assert!(
        payload
            .to_string()
            .contains("The invite for acme.com was already used."),
        "{payload}"
    );

    let connection = rig.pool.get().await.unwrap();
    let rows = connection
        .query(
            "select 1 from ufo_control.invite_code where email_domain = $1",
            &[&"acme.com"],
        )
        .await
        .unwrap();
    assert_eq!(rows.len(), 1);
}

#[tokio::test]
async fn a_keyed_session_replaces_a_grant_that_expired_unspent() {
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
    InviteCodes {
        pool: rig.pool.clone(),
        ttl: Duration::seconds(-1),
    }
    .mint(None, "founder@acme.com", None)
    .await
    .unwrap();

    let (_, cookie) = join(&rig, SIGNUP_KEY).await;
    let cookie = cookie.expect("the door binds a session");
    let (_, carried) = web_turn(&rig, Some(&cookie), "founder@acme.com").await;
    let cookie = carried.unwrap_or(cookie);
    let (payload, _) = web_turn(&rig, Some(&cookie), "123456").await;
    let verbs: Vec<&str> = payload["directives"]
        .as_array()
        .unwrap()
        .iter()
        .map(|directive| directive["verb"].as_str().unwrap())
        .collect();
    assert!(verbs.contains(&"token"), "{payload}");

    let connection = rig.pool.get().await.unwrap();
    let rows = connection
        .query(
            "select consumed_at from ufo_control.invite_code where email_domain = $1",
            &[&"acme.com"],
        )
        .await
        .unwrap();
    assert_eq!(rows.len(), 1);
    assert!(rows[0]
        .get::<_, Option<chrono::DateTime<Utc>>>("consumed_at")
        .is_some());
}

fn keyed_cookie(key: &str, expires_at: chrono::DateTime<Utc>) -> String {
    let mark = keyed_mark(key, SECRET, expires_at);
    let sealed = seal_session(
        &format!("{mark}~Zm9yLXRoZS10ZXN0LW9ubHktbm90LXJhbmRvbS1pZA"),
        SECRET,
    );
    format!("{ONBOARD_SESSION_COOKIE}={sealed}")
}

fn mark_expiry(cookie: &str) -> String {
    cookie
        .split_once('=')
        .unwrap()
        .1
        .split('~')
        .nth(2)
        .expect("a marked session names an expiry")
        .to_string()
}

#[tokio::test]
async fn the_join_door_draws_the_form_for_a_browser_already_signed_in() {
    let rig = rig(vec![], vec![], true).await;
    let response = client()
        .get(format!("{}/join/{SIGNUP_KEY}", rig.base))
        .send()
        .await
        .unwrap();
    let landing = response
        .headers()
        .get("location")
        .unwrap()
        .to_str()
        .unwrap()
        .to_string();
    let cookie = response
        .headers()
        .get("set-cookie")
        .unwrap()
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_string();

    let bearer = mint_token(SECRET, WORKSPACE, "dana@acme.com", Utc::now()).unwrap();
    let page = client()
        .get(format!("{}{landing}", rig.base))
        .header("cookie", format!("{SESSION_COOKIE}={bearer}; {cookie}"))
        .send()
        .await
        .unwrap();
    assert_eq!(
        page.status(),
        StatusCode::OK,
        "the form is drawn, not forwarded"
    );
}

#[tokio::test]
async fn a_mark_the_deploy_has_stopped_serving_grants_nothing() {
    let door = rig(vec![], vec![], true).await;
    let (_, cookie) = join(&door, SIGNUP_KEY).await;
    let captured = cookie.expect("the door binds a session");

    for signup_key in [None, Some("rotated")] {
        let closed = rig_with(
            vec![
                (200, r#"{"id":"m1"}"#.to_string()),
                (200, r#"{"user":{"email":"founder@acme.com"}}"#.to_string()),
            ],
            vec![(200, r#"{"choices":[]}"#.to_string())],
            true,
            signup_key,
        )
        .await;
        let (_, carried) = web_turn(&closed, Some(&captured), "founder@acme.com").await;
        let cookie = carried.unwrap_or_else(|| captured.clone());
        let (payload, _) = web_turn(&closed, Some(&cookie), "123456").await;
        assert!(
            payload.to_string().contains("acme.com has no invite."),
            "{signup_key:?}: {payload}"
        );
    }
}

#[tokio::test]
async fn a_captured_mark_stops_counting_when_it_expires() {
    let rig = rig(
        vec![
            (200, r#"{"id":"m1"}"#.to_string()),
            (200, r#"{"user":{"email":"founder@acme.com"}}"#.to_string()),
        ],
        vec![(200, r#"{"choices":[]}"#.to_string())],
        true,
    )
    .await;
    let stale = keyed_cookie(
        SIGNUP_KEY,
        Utc::now() - Duration::minutes(SIGNUP_MARK_TTL_MINUTES + 1),
    );
    let (_, carried) = web_turn(&rig, Some(&stale), "founder@acme.com").await;
    let cookie = carried.unwrap_or(stale);
    let (payload, _) = web_turn(&rig, Some(&cookie), "123456").await;
    assert!(
        payload.to_string().contains("acme.com has no invite."),
        "{payload}"
    );
}

#[tokio::test]
async fn a_re_mint_carries_the_mark_without_extending_it() {
    let rig = rig(vec![], vec![], true).await;
    let (_, cookie) = join(&rig, SIGNUP_KEY).await;
    let cookie = cookie.expect("the door binds a session");
    let before = mark_expiry(&cookie);

    let (_, carried) = web_turn(&rig, Some(&cookie), "founder@acme.com").await;
    let carried = carried.expect("the email turn re-mints");
    assert_ne!(carried, cookie, "the session id itself is reissued");
    assert_eq!(
        mark_expiry(&carried),
        before,
        "the expiry is copied, not renewed"
    );
}

#[tokio::test]
async fn the_join_ask_survives_the_google_hop_it_was_carried_into() {
    let rig = rig(
        vec![],
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
    let (_, cookie) = join(&rig, SIGNUP_KEY).await;
    let cookie = cookie.expect("the door binds a session");
    let bearer = mint_token(SECRET, WORKSPACE, "dana@elsewhere.com", Utc::now()).unwrap();

    let start = client()
        .get(format!("{}/v1/onboard/auth/start?join=1", rig.base))
        .header("cookie", &cookie)
        .send()
        .await
        .unwrap();
    let hopped = start
        .headers()
        .get("set-cookie")
        .unwrap()
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_string();
    let state = start
        .headers()
        .get("location")
        .unwrap()
        .to_str()
        .unwrap()
        .split("state=")
        .nth(1)
        .unwrap()
        .to_string();

    let returned = client()
        .get(format!(
            "{}/v1/onboard/auth/callback?state={state}&code=founder%40acme.com",
            rig.base
        ))
        .header("cookie", &hopped)
        .send()
        .await
        .unwrap();
    let landing = returned
        .headers()
        .get("location")
        .unwrap()
        .to_str()
        .unwrap()
        .to_string();
    assert!(landing.contains("join=1"), "{landing}");

    let page = client()
        .get(format!("{}{landing}", rig.base))
        .header("cookie", format!("{SESSION_COOKIE}={bearer}; {hopped}"))
        .send()
        .await
        .unwrap();
    assert_eq!(page.status(), StatusCode::OK, "{landing} forwarded instead");

    let (payload, _) = web_turn(&rig, Some(&hopped), "").await;
    let verbs: Vec<&str> = payload["directives"]
        .as_array()
        .unwrap()
        .iter()
        .map(|directive| directive["verb"].as_str().unwrap())
        .collect();
    assert!(verbs.contains(&"token"), "{payload}");
}
