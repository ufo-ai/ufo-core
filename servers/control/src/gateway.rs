use std::collections::HashMap;

use axum::body::Body;
use axum::extract::{Path, Query, Request, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Redirect, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::{DateTime, Duration, Utc};

use crate::campaign::Campaigns;
use crate::claim::ClaimWorkflow;
use crate::directives::{client_install, directive, render, PROMPT};
use crate::email_send::{self, EmailSends};
use crate::hud;
use crate::invite::{InviteCodes, InviteError, Redemption, SignupProfile};
use crate::shared::{EnsuredWorkspace, SeatError, SharedWorkspaces, WorkspaceChoice};
use crate::store::{OnboardClaim, OnboardStore};
use crate::token;
use crate::web::{
    parse_directives, ASSET_CACHE, ILLUSTRATION_BYTES, ILLUSTRATION_PATH, LOGIN_PAGE, LOGO_BYTES,
    LOGO_PATH, LOGO_PNG_BYTES, LOGO_PNG_PATH, MARK_BYTES, MARK_PATH, ONBOARD_SESSION_COOKIE,
    SHARE_HOME_BYTES, SHARE_HOME_PATH, SHARE_SITE_BYTES, SHARE_SITE_PATH, WEB_CHANNEL,
};
use crate::workos::{
    console_signin_page, constant_time_eq, is_uuid_shaped, open_session, pack_state, seal_session,
    subkey_signature, unpack_state, AuthCarry, Verifier, AUTH_CALLBACK_PATH, AUTH_CONSOLE_PATH,
    AUTH_START_PATH, SIGN_IN_FAILED,
};

pub const INVITE_REQUIRED_ENV: &str = "UFO_INVITE_REQUIRED";
pub const SIGNUP_KEY_ENV: &str = "UFO_SIGNUP_KEY";
pub const CLIENT_BIN_DIR_ENV: &str = "UFO_CLIENT_BIN_DIR";
pub const GATEWAY_PORT_ENV: &str = "UFO_GATEWAY_PORT";

pub const OPERATOR_EMAIL_DOMAIN: &str = "metalcraft.ai";
pub const LOGIN_PATH: &str = "/login";
pub const SIGNUP_LOGIN_PATH: &str = "/login?signup=1";
pub const LOGOUT_PATH: &str = "/logout";
pub const PORTAL_SURFACE_PATH: &str = "/surface/web";
pub const DEBUG_SURFACE_PATH: &str = "/surface/debug";

pub const INVITATION_LOGIN_PATH: &str = "/login?invite=1";

/// The operator session cookie, core's `ufo.runtime.ext.operator.OPERATOR_COOKIE` under the name it
/// binds. The operator surfaces are served on this host, so a sign-out here clears it.
pub const OPERATOR_COOKIE: &str = "ufo_debug";

const JOIN_PATH: &str = "/join/{key}";

/// The ask is load-bearing: `/login` forwards a browser holding a live bearer straight to its portal,
/// which would spend the marked session on nothing.
pub const JOIN_LOGIN_PATH: &str = "/login?join=1";
pub const FIRST_MOVE_PROMPT: &str = "What first?";
pub const SLACK_CHOICE: &str = "Connect Slack";
pub const BILLING_CHOICE: &str = "Set up billing";
pub const TOUR_CHOICE: &str = "Show me what you can do";
pub const WORKSPACE_PROMPT: &str = "Choose a workspace:";
pub const SCRIPT_URL_DEFAULT: &str = r#"UFO_URL="${UFO_URL:-https://ufo.ai}""#;
pub const SHELLSCRIPT_MEDIA_TYPE: &str = "text/x-shellscript";
pub const ONBOARDING_FAILED: &str = "Onboarding failed.";

pub const CLIENT_TARGETS: &[&str] = &[
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
    "aarch64-unknown-linux-musl",
    "x86_64-unknown-linux-musl",
    "x86_64-pc-windows-msvc",
];

pub const MAX_CHANNEL_BYTES: usize = 64;
pub const MAX_SESSION_BYTES: usize = 128;
pub const ONBOARD_SESSION_BYTES: usize = 32;
pub const MAX_BODY_BYTES: usize = 4096;

const CLIENT_SCRIPT: &str = include_str!("client/ufo");

pub fn stamped_script(public_base_url: &str) -> String {
    CLIENT_SCRIPT.replacen(
        SCRIPT_URL_DEFAULT,
        &format!(r#"UFO_URL="${{UFO_URL:-{public_base_url}}}""#),
        1,
    )
}

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
struct RequestInputError(String);

#[derive(Debug, Clone)]
pub struct Onboarding {
    pub claims: ClaimWorkflow,
    pub store: OnboardStore,
    pub workspaces: SharedWorkspaces,
    pub invites: InviteCodes,
    pub verifier: Verifier,
    pub token_secret: String,
    pub apex_host: String,
    pub invite_required: bool,
    pub signup_key: Option<String>,
}

impl Onboarding {
    pub async fn advance(
        &self,
        channel: &str,
        session: &str,
        body: &str,
        install: &[u8],
    ) -> Vec<u8> {
        let claim = match self.store.live_claim(channel, session).await {
            Ok(claim) => claim,
            Err(error) => return self.failed(install, &error.to_string()),
        };
        match claim {
            None => self.collect_email(channel, session, body, install).await,
            Some(claim) if claim.verified_at.is_none() => {
                self.verify_code(&claim, body, install).await
            }
            Some(claim) => self.resolve(&claim, body, install).await,
        }
    }

    async fn collect_email(
        &self,
        channel: &str,
        session: &str,
        body: &str,
        install: &[u8],
    ) -> Vec<u8> {
        if body.is_empty() {
            return render(&[
                install.to_vec(),
                directive("say", &[&self.apex_host]),
                directive("ask", &["Enter your email:"]),
            ]);
        }
        if let Err(error) = self.claims.start(body, channel, session).await {
            return render(&[
                install.to_vec(),
                directive("say", &[&error.to_string()]),
                directive("ask", &["Enter your email:"]),
            ]);
        }
        render(&[
            install.to_vec(),
            directive(
                "say",
                &[&format!(
                    "We emailed a code to {}",
                    body.trim().to_lowercase()
                )],
            ),
            directive("ask", &["Enter the code:"]),
        ])
    }

    async fn verify_code(&self, claim: &OnboardClaim, body: &str, install: &[u8]) -> Vec<u8> {
        let Err(error) = self.claims.verify(claim, body).await else {
            return self.resolve(claim, "", install).await;
        };
        let current = match self
            .store
            .live_claim(&claim.surface, &claim.surface_ref)
            .await
        {
            Ok(current) => current,
            Err(error) => return self.failed(install, &error.to_string()),
        };
        if let Some(current) = &current {
            if current.verified_at.is_some() {
                return self.resolve(current, "", install).await;
            }
        }
        let prompt = if current.is_some() {
            "Enter the code:"
        } else {
            "Enter your email:"
        };
        render(&[
            install.to_vec(),
            directive("say", &[&error.to_string()]),
            directive("ask", &[prompt]),
        ])
    }

    async fn resolve(&self, claim: &OnboardClaim, body: &str, install: &[u8]) -> Vec<u8> {
        let choices = match self
            .workspaces
            .choices(&claim.signup_subject, &claim.email)
            .await
        {
            Ok(choices) => choices,
            Err(error) => return self.unreachable(install, &error),
        };
        let (ensured, created) = if choices.is_empty() {
            if self.invite_required {
                if let Some(refusal) = self.invite_gate(claim, install).await {
                    return refusal;
                }
            }
            match self.create(claim).await {
                Ok(ensured) => (ensured, true),
                Err(error) => return self.unreachable(install, &error),
            }
        } else {
            match self.choose(claim, body, install, &choices).await {
                Ok(outcome) => match outcome {
                    Chosen::Screen(screen) => return screen,
                    Chosen::Ensured(ensured, created) => (ensured, created),
                },
                Err(error) => return self.unreachable(install, &error),
            }
        };
        if let Err(error) = self
            .store
            .complete(claim.claim_id, &ensured.workspace_id, created)
            .await
        {
            return self.failed(install, &error.to_string());
        }
        self.signed_in(claim, &ensured, install)
    }

    async fn choose(
        &self,
        claim: &OnboardClaim,
        body: &str,
        install: &[u8],
        choices: &[WorkspaceChoice],
    ) -> Result<Chosen, SeatError> {
        let create_available = claim.invite_id.is_some()
            || self.keyed(&claim.surface_ref)
            || self.invites.available(&claim.email).await.unwrap_or(false);
        if choices.len() == 1 && !create_available {
            let ensured = self
                .workspaces
                .join(&choices[0], &claim.signup_subject, &claim.email)
                .await?;
            return Ok(Chosen::Ensured(ensured, false));
        }
        let create_label = if claim.signup_subject == claim.email {
            "Create new workspace".to_string()
        } else {
            format!("Create {} workspace", claim.email_domain)
        };
        let mut options: Vec<String> = choices.iter().map(|choice| choice.label.clone()).collect();
        if create_available {
            options.push(create_label.clone());
        }
        if body == create_label && create_available {
            if let Some(refusal) = self.invite_gate(claim, install).await {
                return Ok(Chosen::Screen(refusal));
            }
            return Ok(Chosen::Ensured(self.create(claim).await?, true));
        }
        let selected = choices.iter().find(|choice| choice.label == body);
        let Some(selected) = selected else {
            let mut fields = vec![WORKSPACE_PROMPT.to_string()];
            fields.extend(options);
            let borrowed: Vec<&str> = fields.iter().map(String::as_str).collect();
            return Ok(Chosen::Screen(render(&[
                install.to_vec(),
                if body.is_empty() {
                    Vec::new()
                } else {
                    directive("say", &["Choose a listed workspace."])
                },
                directive("choose", &borrowed),
            ])));
        };
        let ensured = self
            .workspaces
            .join(selected, &claim.signup_subject, &claim.email)
            .await?;
        Ok(Chosen::Ensured(ensured, false))
    }

    fn keyed(&self, session: &str) -> bool {
        session_is_keyed(
            session,
            &self.token_secret,
            self.signup_key.as_deref(),
            Utc::now(),
        )
    }

    /// `mint` is the only thing that clears an expired unconsumed row, so a gate that skipped it would
    /// refuse that subject for good. A subject that already holds a grant is not an error here.
    async fn self_grant(&self, claim: &OnboardClaim) -> Result<Redemption, InviteError> {
        match self.invites.mint(None, &claim.email, None).await {
            Ok(_) => tracing::info!(
                target: "ufo_control::gateway",
                "gateway.signup_key.granted domain={}",
                claim.email_domain
            ),
            Err(InviteError::LiveGrant { .. } | InviteError::AlreadyIdentified(_)) => {}
            Err(error) => return Err(error),
        }
        self.invites.redeem(&claim.email, claim.claim_id).await
    }

    async fn create(&self, claim: &OnboardClaim) -> Result<EnsuredWorkspace, SeatError> {
        let profile: Option<SignupProfile> =
            self.invites.profile(&claim.email).await.unwrap_or(None);
        let carried = profile.map(|profile| crate::shared::SignupProfile {
            business: profile.business,
            goals: profile.goals,
        });
        self.workspaces
            .create(&claim.signup_subject, &claim.email, carried.as_ref())
            .await
    }

    async fn invite_gate(&self, claim: &OnboardClaim, install: &[u8]) -> Option<Vec<u8>> {
        if claim.invite_id.is_some() {
            return None;
        }
        let subject = &claim.signup_subject;
        let redemption = match self.invites.redeem(&claim.email, claim.claim_id).await {
            Ok(redemption) => redemption,
            Err(InviteError::Pool(_) | InviteError::Query(_)) => {
                return Some(self.failed(install, "the invite ledger is unreachable"))
            }
            Err(error) => return Some(self.failed(install, &error.to_string())),
        };
        let redemption = match redemption {
            Redemption::Unknown | Redemption::Expired { .. } if self.keyed(&claim.surface_ref) => {
                match self.self_grant(claim).await {
                    Ok(redemption) => redemption,
                    Err(InviteError::Pool(_) | InviteError::Query(_)) => {
                        return Some(self.failed(install, "the invite ledger is unreachable"))
                    }
                    Err(error) => return Some(self.failed(install, &error.to_string())),
                }
            }
            answered => answered,
        };
        match redemption {
            Redemption::Accepted(_) => None,
            Redemption::Expired { expires_at } => Some(render(&[
                install.to_vec(),
                directive(
                    "say",
                    &[&format!(
                        "The invite for {subject} expired {} UTC.",
                        expires_at.format("%Y-%m-%d %H:%M")
                    )],
                ),
                directive("say", &["Reply to your invite email for a new one."]),
                directive("exit", &["0"]),
            ])),
            Redemption::Consumed => Some(render(&[
                install.to_vec(),
                directive(
                    "say",
                    &[&format!("The invite for {subject} was already used.")],
                ),
                directive("say", &["Contact us if you cannot sign in."]),
                directive("exit", &["0"]),
            ])),
            Redemption::Unknown => Some(render(&[
                install.to_vec(),
                directive("say", &[&format!("{subject} has no invite.")]),
                directive(
                    "say",
                    &[&format!("Join the waitlist: https://{}", self.apex_host)],
                ),
                directive("exit", &["0"]),
            ])),
        }
    }

    fn signed_in(
        &self,
        claim: &OnboardClaim,
        ensured: &EnsuredWorkspace,
        install: &[u8],
    ) -> Vec<u8> {
        let minted = token::mint_token(
            &self.token_secret,
            &ensured.workspace_id,
            &claim.email,
            Utc::now(),
        );
        let Ok(minted) = minted else {
            return self.failed(install, "the deploy holds no token secret");
        };
        let operator = claim.email_domain == OPERATOR_EMAIL_DOMAIN;
        let workspace_url = &self.workspaces.workspace_url;
        render(&[
            install.to_vec(),
            directive("token", &[&minted]),
            directive("workspace", &[workspace_url]),
            if operator {
                directive(
                    "debugger",
                    &[&format!("{workspace_url}{DEBUG_SURFACE_PATH}")],
                )
            } else {
                Vec::new()
            },
            directive("say", &[&format!("Signed in: {}", claim.email)]),
            if ensured.founding {
                directive("first", &["1"])
            } else {
                Vec::new()
            },
            if claim.surface == WEB_CHANNEL {
                Vec::new()
            } else if ensured.admin {
                directive(
                    "choose",
                    &[FIRST_MOVE_PROMPT, SLACK_CHOICE, BILLING_CHOICE, TOUR_CHOICE],
                )
            } else {
                directive("ask", &[PROMPT])
            },
        ])
    }

    fn unreachable(&self, install: &[u8], error: &SeatError) -> Vec<u8> {
        match error {
            SeatError::Refused(message) => render(&[
                install.to_vec(),
                directive("say", &[message]),
                directive("exit", &["1"]),
            ]),
            other => {
                tracing::error!(target: "ufo_control::gateway", "onboard.workspace_service {other}");
                self.failed(install, "Could not reach the workspace service. Try again.")
            }
        }
    }

    fn failed(&self, install: &[u8], message: &str) -> Vec<u8> {
        tracing::error!(target: "ufo_control::gateway", "onboard.failed {message}");
        render(&[
            install.to_vec(),
            directive("say", &[ONBOARDING_FAILED]),
            directive("exit", &["1"]),
        ])
    }
}

enum Chosen {
    Screen(Vec<u8>),
    Ensured(EnsuredWorkspace, bool),
}

#[derive(Clone)]
pub struct GatewayState {
    pub onboarding: Onboarding,
    pub stamped_script: String,
    pub client_bin_dir: Option<String>,
    pub client_version: String,
    pub console_mode: bool,
    /// Absent where this deploy sends no campaigns, which is what keeps the operator surface off a
    /// self-hosted install rather than serving a page every act of which would fail.
    pub campaigns: Option<Campaigns>,
    /// The one-message seam core calls.
    pub email_sends: EmailSends,
}

pub fn router(state: GatewayState) -> Router {
    let mut router = Router::new()
        .route("/healthz", get(healthz))
        .route("/ufo", get(serve_script))
        .route("/ufo/bin/{target}", get(client_binary))
        .route("/fleet", get(fleet))
        .route(LOGIN_PATH, get(login))
        .route(LOGOUT_PATH, get(logout))
        .route(LOGO_PATH, get(logo))
        .route(MARK_PATH, get(mark))
        .route(LOGO_PNG_PATH, get(logo_png))
        .route(ILLUSTRATION_PATH, get(illustration))
        .route(SHARE_HOME_PATH, get(share_home))
        .route(SHARE_SITE_PATH, get(share_site))
        .route(JOIN_PATH, get(join))
        .route(AUTH_START_PATH, get(auth_start))
        .route(AUTH_CALLBACK_PATH, get(auth_callback))
        .route("/v1/onboard/web", post(onboard_web))
        .route("/v1/onboard/{channel}", post(onboard))
        .merge(email_send::routes());
    if state.console_mode {
        router = router.route(AUTH_CONSOLE_PATH, get(auth_console));
    }
    if state.campaigns.is_some() {
        router = router.merge(hud::routes());
    }
    router.with_state(state)
}

async fn healthz(State(state): State<GatewayState>) -> Response {
    match state.onboarding.store.pool.get().await {
        Ok(_) => Json(serde_json::json!({"status": "ok"})).into_response(),
        Err(error) => {
            tracing::error!(target: "ufo_control::gateway", "gateway.health.failed {error}");
            (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(serde_json::json!({"status": "unavailable"})),
            )
                .into_response()
        }
    }
}

async fn serve_script(State(state): State<GatewayState>) -> Response {
    (
        [(header::CONTENT_TYPE, SHELLSCRIPT_MEDIA_TYPE)],
        state.stamped_script,
    )
        .into_response()
}

async fn client_binary(State(state): State<GatewayState>, Path(target): Path<String>) -> Response {
    let refusal = (
        StatusCode::NOT_FOUND,
        format!("no client binary for {target}"),
    );
    let Some(directory) = state.client_bin_dir.filter(|value| !value.is_empty()) else {
        return refusal.into_response();
    };
    if !CLIENT_TARGETS.contains(&target.as_str()) {
        return refusal.into_response();
    }
    let name = if target == "x86_64-pc-windows-msvc" {
        "ufo.exe"
    } else {
        "ufo"
    };
    let binary = std::path::Path::new(&directory).join(&target).join(name);
    match tokio::fs::read(&binary).await {
        Ok(bytes) => (
            [
                (header::CONTENT_TYPE, "application/octet-stream".to_string()),
                (
                    header::CONTENT_DISPOSITION,
                    format!("attachment; filename=\"{name}\""),
                ),
            ],
            Body::from(bytes),
        )
            .into_response(),
        Err(_) => refusal.into_response(),
    }
}

async fn fleet(State(state): State<GatewayState>) -> Response {
    match state.onboarding.workspaces.fleet().await {
        Ok(craft) => Json(serde_json::json!({"craft": craft})).into_response(),
        Err(error) => {
            tracing::error!(target: "ufo_control::gateway", "gateway.fleet.failed {error}");
            (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(serde_json::json!({"status": "unavailable"})),
            )
                .into_response()
        }
    }
}

async fn login(
    State(state): State<GatewayState>,
    headers: HeaderMap,
    Query(query): Query<HashMap<String, String>>,
) -> Response {
    let landing = signed_in_landing(
        &state.onboarding.token_secret,
        &state.onboarding.workspaces.workspace_url,
        &headers,
        &query,
    );
    if let Some(landing) = landing {
        return Redirect::to(&landing).into_response();
    }
    if query.get("signup").is_some_and(|value| value == "1") && state.onboarding.invite_required {
        return Redirect::to(&format!("https://{}", state.onboarding.apex_host)).into_response();
    }
    (
        [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
        LOGIN_PAGE,
    )
        .into_response()
}

/// Each reaches this door from exactly one caller — so its presence, not its value, is the whole signal
/// — and each is a caller a forward would send straight back to.
const FORM_ONLY_ASKS: [&str; 5] = ["debug", "a", "invite", "error", "join"];

fn signed_in_landing(
    token_secret: &str,
    workspace_url: &str,
    headers: &HeaderMap,
    query: &HashMap<String, String>,
) -> Option<String> {
    if FORM_ONLY_ASKS.iter().any(|ask| query.contains_key(*ask)) {
        return None;
    }
    let bearer = session_bearer(headers)?;
    token::verified_email(token_secret, bearer, Utc::now())?;
    let carried = query
        .get("c")
        .filter(|value| is_uuid_shaped(value))
        .map(|conversation| format!("?c={conversation}"))
        .or_else(|| {
            query
                .get("first")
                .filter(|value| *value == "1")
                .map(|_| "?first=1".to_string())
        })
        .unwrap_or_default();
    Some(format!("{workspace_url}{PORTAL_SURFACE_PATH}{carried}"))
}

async fn logout() -> Response {
    let mut response = Redirect::to(LOGIN_PATH).into_response();
    for name in BOUND_COOKIES {
        expire_cookie(&mut response, name);
    }
    response
}

const BOUND_COOKIES: [&str; 3] = [
    token::SESSION_COOKIE,
    OPERATOR_COOKIE,
    ONBOARD_SESSION_COOKIE,
];

fn session_bearer(headers: &HeaderMap) -> Option<&str> {
    let cookies = headers.get(header::COOKIE)?.to_str().ok()?;
    cookies.split(';').find_map(|part| {
        let (name, value) = part.trim().split_once('=')?;
        (name == token::SESSION_COOKIE && !value.is_empty()).then_some(value)
    })
}

async fn logo() -> Response {
    (
        [
            (header::CONTENT_TYPE, "image/svg+xml"),
            (header::CACHE_CONTROL, ASSET_CACHE),
        ],
        LOGO_BYTES,
    )
        .into_response()
}

async fn mark() -> Response {
    (
        [
            (header::CONTENT_TYPE, "image/svg+xml"),
            (header::CACHE_CONTROL, ASSET_CACHE),
        ],
        MARK_BYTES,
    )
        .into_response()
}

/// An invitation is read where SVG is blocked, so it fetches this one; nothing in a browser does.
async fn logo_png() -> Response {
    (
        [
            (header::CONTENT_TYPE, "image/png"),
            (header::CACHE_CONTROL, ASSET_CACHE),
        ],
        LOGO_PNG_BYTES,
    )
        .into_response()
}

async fn illustration() -> Response {
    (
        [
            (header::CONTENT_TYPE, "image/webp"),
            (header::CACHE_CONTROL, ASSET_CACHE),
        ],
        ILLUSTRATION_BYTES,
    )
        .into_response()
}

/// Nothing on this deploy fetches it: an unfurler reads the page's `og:image` and comes here
/// anonymously, so the route takes no session and answers the same bytes to everyone.
async fn share_home() -> Response {
    share(SHARE_HOME_BYTES)
}

async fn share_site() -> Response {
    share(SHARE_SITE_BYTES)
}

fn share(card: &'static [u8]) -> Response {
    (
        [
            (header::CONTENT_TYPE, "image/jpeg"),
            (header::CACHE_CONTROL, ASSET_CACHE),
        ],
        card,
    )
        .into_response()
}

/// A key that does not match is answered exactly as an unrouted path is, because a 403 would tell a
/// caller the door is there. `no-referrer` keeps this URL out of the next request's `Referer`.
async fn join(State(state): State<GatewayState>, Path(key): Path<String>) -> Response {
    let Some(configured) = state.onboarding.signup_key.as_deref() else {
        return StatusCode::NOT_FOUND.into_response();
    };
    if !constant_time_eq(&key, configured) {
        return StatusCode::NOT_FOUND.into_response();
    }
    let secret = &state.onboarding.token_secret;
    let mark = keyed_mark(
        configured,
        secret,
        Utc::now() + Duration::minutes(SIGNUP_MARK_TTL_MINUTES),
    );
    let sealed = seal_session(&fresh_session(Some(&mark)), secret);
    let mut response = Redirect::to(JOIN_LOGIN_PATH).into_response();
    if let Ok(policy) = "no-referrer".parse() {
        response
            .headers_mut()
            .insert(header::REFERRER_POLICY, policy);
    }
    set_session_cookie(&mut response, &sealed);
    response
}

/// 302s to WorkOS with `provider=GoogleOAuth`, so WorkOS goes straight to Google with no hosted page.
/// Packing the carry and unpacking it again is the validation.
async fn auth_start(
    State(state): State<GatewayState>,
    headers: HeaderMap,
    Query(query): Query<HashMap<String, String>>,
) -> Response {
    let secret = &state.onboarding.token_secret;
    let mark =
        onboard_session(&headers, secret).and_then(|presented| carried_mark(&presented, secret));
    let session = seal_session(&fresh_session(mark.as_deref()), secret);
    let carry = AuthCarry {
        session: session.clone(),
        conversation: query.get("c").cloned(),
        artifact: query.get("a").cloned(),
        first_run: query.get("first").is_some_and(|value| value == "1"),
        debug: query.get("debug").is_some_and(|value| value == "1"),
        invite: query.get("invite").is_some_and(|value| value == "1"),
        join: query.get("join").is_some_and(|value| value == "1"),
    };
    let honored = match unpack_state(&pack_state(&carry, secret), secret) {
        Ok(honored) => honored,
        Err(_) => {
            return (StatusCode::BAD_REQUEST, "The sign-in link is not valid.").into_response()
        }
    };
    let url = state
        .onboarding
        .verifier
        .authorization_url(&pack_state(&honored, secret));
    let mut response = Redirect::temporary(&url).into_response();
    set_session_cookie(&mut response, &session);
    response
}

async fn auth_console(Query(query): Query<HashMap<String, String>>) -> Response {
    let state = query.get("state").cloned().unwrap_or_default();
    (
        [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
        console_signin_page(&state),
    )
        .into_response()
}

/// Honored only in the browser that left: the state has to be one this gateway signed, and the session
/// it names has to be the one the start path bound as the cookie.
async fn auth_callback(
    State(state): State<GatewayState>,
    headers: HeaderMap,
    Query(query): Query<HashMap<String, String>>,
) -> Response {
    let secret = &state.onboarding.token_secret;
    let code = query.get("code").cloned().unwrap_or_default();
    let Ok(carry) = unpack_state(query.get("state").map(String::as_str).unwrap_or(""), secret)
    else {
        return (StatusCode::BAD_REQUEST, "The sign-in link is not valid.").into_response();
    };
    let mut landing: Vec<(String, String)> = Vec::new();
    if let Some(conversation) = &carry.conversation {
        landing.push(("c".to_string(), conversation.clone()));
    }
    if let Some(artifact) = &carry.artifact {
        landing.push(("a".to_string(), artifact.clone()));
    }
    if carry.first_run {
        landing.push(("first".to_string(), "1".to_string()));
    }
    if carry.debug {
        landing.push(("debug".to_string(), "1".to_string()));
    }
    if carry.invite {
        landing.push(("invite".to_string(), "1".to_string()));
    }
    if carry.join {
        landing.push(("join".to_string(), "1".to_string()));
    }
    let bound = onboard_session(&headers, secret);
    let refusal = match bound {
        Some(bound) if !code.is_empty() && bound == carry.session => {
            match state.onboarding.verifier.exchange(&code).await {
                Ok(email) => match state
                    .onboarding
                    .claims
                    .admit_verified(&email, WEB_CHANNEL, &carry.session)
                    .await
                {
                    Ok(_) => None,
                    Err(error) => Some(error.to_string()),
                },
                Err(error) => Some(error.to_string()),
            }
        }
        _ => Some(SIGN_IN_FAILED.to_string()),
    };
    if let Some(refusal) = refusal {
        landing.insert(0, ("error".to_string(), refusal));
    }
    let target = if landing.is_empty() {
        "/login".to_string()
    } else {
        format!("/login?{}", encode_pairs(&landing))
    };
    Redirect::to(&target).into_response()
}

/// The `__Host-` prefix is enforced by the browser: it refuses to set such a cookie with a `Domain`,
/// and no sibling host can write it. A presented cookie may only continue a claim it already keys.
async fn onboard_web(State(state): State<GatewayState>, request: Request) -> Response {
    let secret = state.onboarding.token_secret.clone();
    let headers = request.headers().clone();
    let presented = onboard_session(&headers, &secret);
    let live = match &presented {
        Some(session) => state
            .onboarding
            .store
            .live_claim(WEB_CHANNEL, session)
            .await
            .unwrap_or(None),
        None => None,
    };
    let mark = presented
        .as_deref()
        .and_then(|presented| carried_mark(presented, &secret));
    let (session, minted) = match (presented, live) {
        (Some(session), Some(_)) => (session, None),
        _ => {
            let sealed = seal_session(&fresh_session(mark.as_deref()), &secret);
            (sealed.clone(), Some(sealed))
        }
    };
    let payload = match request_body(request).await {
        Ok(body) => {
            state
                .onboarding
                .advance(WEB_CHANNEL, &session, &body, &[])
                .await
        }
        Err(error) => render(&[
            directive("say", &[&error.to_string()]),
            directive("exit", &["1"]),
        ]),
    };
    let mut response =
        Json(serde_json::json!({"directives": parse_directives(&payload)})).into_response();
    if let Some(minted) = minted {
        set_session_cookie(&mut response, &minted);
    }
    response
}

async fn onboard(
    State(state): State<GatewayState>,
    Path(channel): Path<String>,
    request: Request,
) -> Response {
    let headers = request.headers().clone();
    let header_map: HashMap<String, String> = headers
        .iter()
        .filter_map(|(name, value)| {
            value
                .to_str()
                .ok()
                .map(|value| (name.as_str().to_string(), value.to_string()))
        })
        .collect();
    let install = client_install(&header_map, &state.client_version);
    let Some(session) = header_map
        .iter()
        .find(|(name, _)| name.eq_ignore_ascii_case("x-ufo-session"))
        .map(|(_, value)| value.clone())
        .filter(|value| !value.is_empty())
    else {
        return plain(render(&[
            install,
            directive("say", &["x-ufo-session header is required."]),
            directive("exit", &["1"]),
        ]));
    };
    if channel.len() > MAX_CHANNEL_BYTES {
        return plain(render(&[
            install,
            directive("say", &["Channel is too long."]),
            directive("exit", &["1"]),
        ]));
    }
    if session.len() > MAX_SESSION_BYTES {
        return plain(render(&[
            install,
            directive("say", &["Session is too long."]),
            directive("exit", &["1"]),
        ]));
    }
    match request_body(request).await {
        Ok(body) => plain(
            state
                .onboarding
                .advance(&channel, &session, &body, &install)
                .await,
        ),
        Err(error) => plain(render(&[
            install,
            directive("say", &[&error.to_string()]),
            directive("exit", &["1"]),
        ])),
    }
}

fn plain(payload: Vec<u8>) -> Response {
    (
        [(header::CONTENT_TYPE, "text/plain; charset=utf-8")],
        Body::from(payload),
    )
        .into_response()
}

/// A body over the cap is refused rather than truncated: a truncated address or code would be graded
/// as if the member typed it.
async fn request_body(request: Request) -> Result<String, RequestInputError> {
    let bytes = axum::body::to_bytes(request.into_body(), MAX_BODY_BYTES + 1)
        .await
        .map_err(|_| RequestInputError("Request body is too large.".to_string()))?;
    if bytes.len() > MAX_BODY_BYTES {
        return Err(RequestInputError("Request body is too large.".to_string()));
    }
    Ok(String::from_utf8_lossy(&bytes).trim().to_string())
}

/// Reading every cookie of that name and honoring the one that opens under our signature is belt
/// against a forged or duplicate value; a value carrying no signature of ours is read as absent.
fn onboard_session(headers: &HeaderMap, secret: &str) -> Option<String> {
    let cookies = headers.get(header::COOKIE)?.to_str().ok()?;
    for part in cookies.split(';') {
        let (name, value) = part.trim().split_once('=')?;
        if name == ONBOARD_SESSION_COOKIE && open_session(value, secret).is_some() {
            return Some(value.to_string());
        }
    }
    None
}

/// It takes no `Domain`, so the cookie is host-only and can never widen to a parent domain; the
/// `__Host-` prefix makes the browser enforce the same.
fn set_session_cookie(response: &mut Response, sealed: &str) {
    let cookie =
        format!("{ONBOARD_SESSION_COOKIE}={sealed}; HttpOnly; Secure; SameSite=Lax; Path=/");
    if let Ok(value) = cookie.parse() {
        response.headers_mut().append(header::SET_COOKIE, value);
    }
}

/// The date a cleared cookie carries, so a browser that honors no `Max-Age` drops the value on the
/// same header.
const EXPIRED: &str = "Thu, 01 Jan 1970 00:00:00 GMT";

/// A browser replaces a cookie only when the header names it under the same `Path` and `Domain`, so
/// these attributes are the ones the binders write — or the live value survives beside the cleared one.
fn expire_cookie(response: &mut Response, name: &str) {
    let cookie =
        format!("{name}=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0; Expires={EXPIRED}");
    if let Ok(value) = cookie.parse() {
        response.headers_mut().append(header::SET_COOKIE, value);
    }
}

/// The separator is outside the base64url alphabet the id is drawn from, so an ordinary session can
/// never read as a marked one. Emptying the key closes the door for marks already out; so does rotating it.
const KEYED_SESSION_PREFIX: &str = "key~";
const KEYED_SESSION_SEPARATOR: char = '~';
const SIGNUP_KEY_LABEL: &[u8] = b"ufo.signup-key.v1";
const SIGNUP_KEY_TAG_CHARS: usize = 32;

pub const SIGNUP_MARK_TTL_MINUTES: i64 = 30;

fn signup_key_tag(key: &str, secret: &str) -> String {
    let mut tag = subkey_signature(secret, SIGNUP_KEY_LABEL, key.as_bytes());
    tag.truncate(SIGNUP_KEY_TAG_CHARS);
    tag
}

pub fn keyed_mark(key: &str, secret: &str, expires_at: DateTime<Utc>) -> String {
    format!(
        "{KEYED_SESSION_PREFIX}{}{KEYED_SESSION_SEPARATOR}{}",
        signup_key_tag(key, secret),
        expires_at.timestamp()
    )
}

fn carried_mark(sealed: &str, secret: &str) -> Option<String> {
    let session = open_session(sealed, secret)?;
    let rest = session.strip_prefix(KEYED_SESSION_PREFIX)?;
    let (tag, rest) = rest.split_once(KEYED_SESSION_SEPARATOR)?;
    let (expires_at, _) = rest.split_once(KEYED_SESSION_SEPARATOR)?;
    Some(format!(
        "{KEYED_SESSION_PREFIX}{tag}{KEYED_SESSION_SEPARATOR}{expires_at}"
    ))
}

fn fresh_session(mark: Option<&str>) -> String {
    let mut bytes = [0_u8; ONBOARD_SESSION_BYTES];
    getrandom::fill(&mut bytes).expect("the platform has a random source");
    let id = base64::Engine::encode(&base64::engine::general_purpose::URL_SAFE_NO_PAD, bytes);
    match mark {
        Some(mark) => format!("{mark}{KEYED_SESSION_SEPARATOR}{id}"),
        None => id,
    }
}

fn session_is_keyed(
    sealed: &str,
    secret: &str,
    signup_key: Option<&str>,
    now: DateTime<Utc>,
) -> bool {
    let Some(key) = signup_key else {
        return false;
    };
    let Some(session) = open_session(sealed, secret) else {
        return false;
    };
    let Some(rest) = session.strip_prefix(KEYED_SESSION_PREFIX) else {
        return false;
    };
    let Some((tag, rest)) = rest.split_once(KEYED_SESSION_SEPARATOR) else {
        return false;
    };
    let Some((expires_at, _)) = rest.split_once(KEYED_SESSION_SEPARATOR) else {
        return false;
    };
    if !constant_time_eq(tag, &signup_key_tag(key, secret)) {
        return false;
    }
    expires_at
        .parse::<i64>()
        .is_ok_and(|expires_at| now.timestamp() < expires_at)
}

fn encode_pairs(pairs: &[(String, String)]) -> String {
    pairs
        .iter()
        .map(|(name, value)| format!("{}={}", encode(name), encode(value)))
        .collect::<Vec<_>>()
        .join("&")
}

fn encode(value: &str) -> String {
    let mut encoded = String::with_capacity(value.len());
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                encoded.push(byte as char)
            }
            other => encoded.push_str(&format!("%{other:02X}")),
        }
    }
    encoded
}

/// Unset means required, so forgetting the knob never opens signup; garbage fails loud, never defaults.
pub fn parse_invite_required(value: &str) -> Result<bool, String> {
    match value.trim().to_lowercase().as_str() {
        "" | "true" | "1" => Ok(true),
        "false" | "0" => Ok(false),
        other => Err(format!(
            "{INVITE_REQUIRED_ENV}={other:?} is not a boolean (true/false)"
        )),
    }
}

pub fn invite_required_from_env() -> Result<bool, String> {
    parse_invite_required(&std::env::var(INVITE_REQUIRED_ENV).unwrap_or_default())
}

#[cfg(test)]
mod tests {
    use super::*;

    const SECRET: &str = "local-dev-token-secret";
    const WORKSPACE: &str = "11111111-1111-1111-1111-111111111111";
    const WORKSPACE_URL: &str = "https://app.flyingobject.ai";
    const CONVERSATION: &str = "6f1c8038-1111-4222-8333-444455556666";

    fn landing(cookie: Option<&str>, asked: &[(&str, &str)]) -> Option<String> {
        let mut headers = HeaderMap::new();
        if let Some(cookie) = cookie {
            headers.insert(header::COOKIE, cookie.parse().unwrap());
        }
        let query = asked
            .iter()
            .map(|(name, value)| (name.to_string(), value.to_string()))
            .collect();
        signed_in_landing(SECRET, WORKSPACE_URL, &headers, &query)
    }

    fn session(email: &str) -> String {
        let bearer = token::mint_token(SECRET, WORKSPACE, email, Utc::now()).unwrap();
        format!("{}={bearer}", token::SESSION_COOKIE)
    }

    #[test]
    fn a_live_session_lands_where_the_page_would_have_posted_it() {
        let held = session("dana@acme.com");
        for (asked, landed) in [
            (vec![], format!("{WORKSPACE_URL}{PORTAL_SURFACE_PATH}")),
            (
                vec![("c", CONVERSATION)],
                format!("{WORKSPACE_URL}{PORTAL_SURFACE_PATH}?c={CONVERSATION}"),
            ),
            (
                vec![("first", "1")],
                format!("{WORKSPACE_URL}{PORTAL_SURFACE_PATH}?first=1"),
            ),
        ] {
            assert_eq!(
                landing(Some(&held), &asked).as_deref(),
                Some(landed.as_str())
            );
        }
    }

    #[test]
    fn a_target_the_query_invented_is_dropped_rather_than_carried() {
        let held = session("dana@acme.com");
        let portal = format!("{WORKSPACE_URL}{PORTAL_SURFACE_PATH}");
        for asked in [
            vec![("c", "../../elsewhere")],
            vec![("c", "6F1C8038-1111-4222-8333-444455556666")],
            vec![("first", "0")],
        ] {
            assert_eq!(
                landing(Some(&held), &asked).as_deref(),
                Some(portal.as_str()),
                "{asked:?}"
            );
        }
    }

    #[test]
    fn an_ask_only_the_page_can_answer_draws_it_over_a_live_session() {
        for held in [session("dana@acme.com"), session("ops@metalcraft.ai")] {
            for asked in [
                vec![("debug", "1")],
                vec![("a", "/artifacts/1/report.pdf")],
                vec![("invite", "1")],
                vec![("error", "That address cannot be used to sign in.")],
                vec![("c", CONVERSATION), ("a", "/artifacts/1/report.pdf")],
            ] {
                assert_eq!(landing(Some(&held), &asked), None, "{asked:?}");
            }
        }
    }

    #[test]
    fn the_invitation_mail_names_the_door_that_draws_the_form() {
        let (ask, _) = INVITATION_LOGIN_PATH
            .strip_prefix(&format!("{LOGIN_PATH}?"))
            .and_then(|query| query.split_once('='))
            .expect("the invitation path carries one ask");
        assert!(FORM_ONLY_ASKS.contains(&ask), "{ask}");
    }

    #[test]
    fn the_join_door_names_an_ask_the_form_answers() {
        let (ask, _) = JOIN_LOGIN_PATH
            .strip_prefix(&format!("{LOGIN_PATH}?"))
            .and_then(|query| query.split_once('='))
            .expect("the join path carries one ask");
        assert!(FORM_ONLY_ASKS.contains(&ask), "{ask}");
    }

    #[test]
    fn a_browser_holding_no_live_session_is_left_to_the_page() {
        let expired = token::sign(SECRET, WORKSPACE, "dana@acme.com", Utc::now()).unwrap();
        let elsewhere =
            token::mint_token("another-secret", WORKSPACE, "dana@acme.com", Utc::now()).unwrap();
        for cookie in [
            None,
            Some("ufo_onboard=other".to_string()),
            Some(format!("{}=", token::SESSION_COOKIE)),
            Some(format!("{}=not-a-token", token::SESSION_COOKIE)),
            Some(format!("{}={expired}", token::SESSION_COOKIE)),
            Some(format!("{}={elsewhere}", token::SESSION_COOKIE)),
        ] {
            assert_eq!(landing(cookie.as_deref(), &[]), None, "{cookie:?}");
        }
    }

    fn cookie_headers(response: &Response) -> Vec<String> {
        response
            .headers()
            .get_all(header::SET_COOKIE)
            .iter()
            .map(|value| value.to_str().unwrap().to_string())
            .collect()
    }

    #[tokio::test]
    async fn signing_out_clears_every_bound_cookie_and_sends_the_browser_to_the_form() {
        let cleared = |name: &str| {
            format!("{name}=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0; Expires={EXPIRED}")
        };
        let response = logout().await;
        assert_eq!(response.status(), StatusCode::SEE_OTHER);
        assert_eq!(response.headers()[header::LOCATION], LOGIN_PATH);
        assert_eq!(
            cookie_headers(&response),
            BOUND_COOKIES.map(cleared).to_vec()
        );
    }

    #[test]
    fn the_cleared_cookie_carries_the_attributes_the_bound_one_was_set_with() {
        let mut bound = Response::new(Body::empty());
        set_session_cookie(&mut bound, "sealed");
        let mut cleared = Response::new(Body::empty());
        expire_cookie(&mut cleared, ONBOARD_SESSION_COOKIE);
        let attributes = |cookie: &str| {
            cookie
                .split("; ")
                .skip(1)
                .filter(|part| !part.starts_with("Max-Age") && !part.starts_with("Expires"))
                .map(str::to_string)
                .collect::<Vec<_>>()
        };
        assert_eq!(
            attributes(&cookie_headers(&bound)[0]),
            attributes(&cookie_headers(&cleared)[0])
        );
    }

    #[test]
    fn the_session_is_read_out_of_a_jar_holding_other_cookies() {
        let held = session("dana@acme.com");
        let jar = format!("__Host-ufo_onboard=sealed; {held}; theme=dark");
        assert_eq!(
            landing(Some(&jar), &[]).as_deref(),
            Some(format!("{WORKSPACE_URL}{PORTAL_SURFACE_PATH}").as_str())
        );
    }
}
