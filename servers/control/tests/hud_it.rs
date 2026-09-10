mod harness;

use std::path::PathBuf;

use harness::{ledger_pool, spawn_http};
use reqwest::header::{HeaderMap, HeaderValue, COOKIE, ORIGIN};
use reqwest::redirect::Policy;
use reqwest::StatusCode;
use ufo_control::campaign::Campaigns;
use ufo_control::claim::ClaimWorkflow;
use ufo_control::email::{parse_senders, AwsEndpoints, FounderSender};
use ufo_control::gateway::{router, stamped_script, GatewayState, Onboarding, OPERATOR_COOKIE};
use ufo_control::hud::{csrf_token, CSRF_HEADER, EMAIL_SURFACE_PATH, OPERATOR_LOGIN_PATH};
use ufo_control::invite::InviteCodes;
use ufo_control::shared::SharedWorkspaces;
use ufo_control::store::OnboardStore;
use ufo_control::token::mint_token;
use ufo_control::workos::Verifier;
use uuid::Uuid;

const SECRET: &str = "local-dev-token-secret";
const WORKSPACE: &str = "11111111-1111-1111-1111-111111111111";
const OPERATOR: &str = "founder@metalcraft.ai";
const MEMBER: &str = "founder@acme.com";
const SENDERS: &str = "ufo founders <founders@ufo.ai>,Marshall at ufo <marshall@ufo.ai>";

fn bearer(email: &str) -> String {
    mint_token(SECRET, WORKSPACE, email, chrono::Utc::now()).unwrap()
}

async fn rig(sends: bool) -> (String, String) {
    let pool = ledger_pool().await;
    let (serve, _serve_log) = spawn_http(Vec::new()).await;
    let (aws, _aws_log) = spawn_http(Vec::new()).await;
    let workspaces = SharedWorkspaces {
        workspace_url: "https://app.ufo.ai".to_string(),
        serve_internal_url: serve,
        control_token: "onboard-token".to_string(),
    };
    let store = OnboardStore::new(pool.clone());
    let state = GatewayState {
        onboarding: Onboarding {
            claims: ClaimWorkflow::new(store.clone(), Verifier::Console),
            store,
            workspaces: workspaces.clone(),
            invites: InviteCodes::new(pool.clone()),
            verifier: Verifier::Console,
            token_secret: SECRET.to_string(),
            apex_host: "ufo.ai".to_string(),
            invite_required: true,
            signup_key: None,
        },
        stamped_script: stamped_script("https://ufo.ai"),
        client_bin_dir: None,
        client_version: String::new(),
        console_mode: false,
        campaigns: sends.then(|| Campaigns {
            pool,
            core: workspaces,
            sender: FounderSender {
                senders: parse_senders(SENDERS).unwrap(),
                configuration_set: "ufo-testing-founder-email".to_string(),
                contact_list: "ufo-users".to_string(),
                topic: "founder-updates".to_string(),
                region: "us-east-1".to_string(),
                role_arn: "arn:aws:iam::111122223333:role/ufo-testing-gateway-ses".to_string(),
                token_file: PathBuf::from("/var/run/token"),
                endpoints: AwsEndpoints {
                    ses: aws.clone(),
                    sts: format!("{aws}/"),
                },
            },
            apex_host: "ufo.ai".to_string(),
        }),
    };
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap().to_string();
    tokio::spawn(async move {
        let _ = axum::serve(listener, router(state)).await;
    });
    let base = format!("http://{address}");
    (base, address)
}

fn client() -> reqwest::Client {
    reqwest::Client::builder()
        .redirect(Policy::none())
        .build()
        .unwrap()
}

fn session(email: &str) -> (String, HeaderMap) {
    let token = bearer(email);
    let mut headers = HeaderMap::new();
    headers.insert(
        COOKIE,
        HeaderValue::from_str(&format!("{OPERATOR_COOKIE}={token}")).unwrap(),
    );
    (token, headers)
}

fn draft() -> serde_json::Value {
    serde_json::json!({
        "subject": "Update",
        "body": "We shipped a thing.",
        "action_label": null,
        "action_url": null,
        "audience": "members",
        "sender": "ufo founders <founders@ufo.ai>",
    })
}

#[tokio::test]
async fn the_page_bounces_a_visitor_with_no_operator_session_to_the_sign_in_form() {
    let (base, _address) = rig(true).await;
    let anonymous = client()
        .get(format!("{base}{EMAIL_SURFACE_PATH}"))
        .send()
        .await
        .unwrap();
    assert_eq!(anonymous.status(), StatusCode::SEE_OTHER);
    assert_eq!(
        anonymous.headers()["location"].to_str().unwrap(),
        OPERATOR_LOGIN_PATH
    );

    let (_token, headers) = session(MEMBER);
    let member = client()
        .get(format!("{base}{EMAIL_SURFACE_PATH}"))
        .headers(headers)
        .send()
        .await
        .unwrap();
    assert_eq!(
        member.status(),
        StatusCode::SEE_OTHER,
        "a verified member is not an operator"
    );

    let (_token, headers) = session(OPERATOR);
    let operator = client()
        .get(format!("{base}{EMAIL_SURFACE_PATH}"))
        .headers(headers)
        .send()
        .await
        .unwrap();
    assert_eq!(operator.status(), StatusCode::OK);
    assert!(operator.text().await.unwrap().contains("Founder Email"));
}

#[tokio::test]
async fn a_read_route_refuses_rather_than_redirecting() {
    let (base, _address) = rig(true).await;
    let refused = client()
        .get(format!("{base}{EMAIL_SURFACE_PATH}/campaigns"))
        .send()
        .await
        .unwrap();
    assert_eq!(refused.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn a_deploy_that_sends_no_campaigns_routes_nothing() {
    let (base, _address) = rig(false).await;
    for path in [
        EMAIL_SURFACE_PATH.to_string(),
        format!("{EMAIL_SURFACE_PATH}/campaigns"),
    ] {
        let (_token, headers) = session(OPERATOR);
        let answered = client()
            .get(format!("{base}{path}"))
            .headers(headers)
            .send()
            .await
            .unwrap();
        assert_eq!(answered.status(), StatusCode::NOT_FOUND, "{path}");
    }
}

#[tokio::test]
async fn a_mutation_needs_the_session_its_origin_and_its_own_token() {
    let (base, address) = rig(true).await;
    let (token, headers) = session(OPERATOR);
    let bound = csrf_token(SECRET, &token);
    let url = format!("{base}{EMAIL_SURFACE_PATH}/campaigns");

    let no_session = client()
        .post(&url)
        .header(ORIGIN, format!("http://{address}"))
        .header(CSRF_HEADER, &bound)
        .json(&draft())
        .send()
        .await
        .unwrap();
    assert_eq!(no_session.status(), StatusCode::UNAUTHORIZED);

    let cross_origin = client()
        .post(&url)
        .headers(headers.clone())
        .header(ORIGIN, "https://elsewhere.example")
        .header(CSRF_HEADER, &bound)
        .json(&draft())
        .send()
        .await
        .unwrap();
    assert_eq!(cross_origin.status(), StatusCode::FORBIDDEN);

    let no_origin = client()
        .post(&url)
        .headers(headers.clone())
        .header(CSRF_HEADER, &bound)
        .json(&draft())
        .send()
        .await
        .unwrap();
    assert_eq!(no_origin.status(), StatusCode::FORBIDDEN);

    let wrong_token = client()
        .post(&url)
        .headers(headers.clone())
        .header(ORIGIN, format!("http://{address}"))
        .header(CSRF_HEADER, csrf_token(SECRET, "another-session"))
        .json(&draft())
        .send()
        .await
        .unwrap();
    assert_eq!(wrong_token.status(), StatusCode::FORBIDDEN);

    let created = client()
        .post(&url)
        .headers(headers)
        .header(ORIGIN, format!("http://{address}"))
        .header(CSRF_HEADER, &bound)
        .json(&draft())
        .send()
        .await
        .unwrap();
    assert_eq!(created.status(), StatusCode::OK);
    let payload: serde_json::Value = created.json().await.unwrap();
    assert_eq!(payload["created_by"], OPERATOR);
    assert_eq!(payload["state"], "draft");
    assert!(payload["id"].as_str().unwrap().parse::<Uuid>().is_ok());
}
