//! The hosted onboarding server and shared-workspace resolver.
//!
//! One state machine serves every surface. `Onboarding::advance` reads the claim a session holds and
//! answers with directive lines: no claim collects the work email, an unverified claim grades the
//! code WorkOS mailed, and a verified claim resolves a workspace and mints the bearer. The terminal
//! renders those lines as a screen and the browser renders them as a transcript — two renderers,
//! never two machines, so nothing outside this file ever collects an address and the work-email
//! policy runs before any code is sent.

use std::collections::HashMap;

use axum::body::Body;
use axum::extract::{Path, Query, Request, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Redirect, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::Utc;

use crate::claim::{ClaimError, ClaimWorkflow};
use crate::directives::{client_install, directive, render, PROMPT};
use crate::email::WorkEmailError;
use crate::invite::{InviteCodes, InviteError, Redemption, SignupProfile};
use crate::shared::{EnsuredWorkspace, SeatError, SharedWorkspaces, WorkspaceChoice};
use crate::store::{OnboardClaim, OnboardStore};
use crate::token;
use crate::web::{parse_directives, LOGIN_PAGE, ONBOARD_SESSION_COOKIE, WEB_CHANNEL};
use crate::workos::{
    console_signin_page, open_session, pack_state, seal_session, unpack_state, AuthCarry, Verifier,
    AUTH_CALLBACK_PATH, AUTH_CONSOLE_PATH, AUTH_START_PATH, SIGN_IN_FAILED,
};

pub const INVITE_REQUIRED_ENV: &str = "UFO_INVITE_REQUIRED";
pub const CLIENT_BIN_DIR_ENV: &str = "UFO_CLIENT_BIN_DIR";
pub const GATEWAY_PORT_ENV: &str = "UFO_GATEWAY_PORT";

pub const OPERATOR_EMAIL_DOMAIN: &str = "metalcraft.ai";
pub const DEBUG_SURFACE_PATH: &str = "/surface/debug";
pub const FIRST_MOVE_PROMPT: &str = "What first?";
pub const SLACK_CHOICE: &str = "Connect Slack";
pub const BILLING_CHOICE: &str = "Set up billing";
pub const TOUR_CHOICE: &str = "Show me what you can do";
pub const WORKSPACE_PROMPT: &str = "Choose a workspace:";
pub const SCRIPT_URL_DEFAULT: &str = r#"UFO_URL="${UFO_URL:-https://flyingobject.ai}""#;
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

/// The served installer, with this deploy's own base URL written into it. The default in the script
/// is what a local checkout runs against, so the stamp is a replacement rather than an append.
pub fn stamped_script(public_base_url: &str) -> String {
    CLIENT_SCRIPT.replacen(
        SCRIPT_URL_DEFAULT,
        &format!(r#"UFO_URL="${{UFO_URL:-{public_base_url}}}""#),
        1,
    )
}

/// A body the request could not carry: too long, or not the shape a turn takes.
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
}

impl Onboarding {
    /// One machine for every surface. A session with no claim collects the work email and the code
    /// WorkOS mailed for it — the browser on our own `/login` page, exactly as the terminal does — a
    /// verified claim resolves a workspace and mints the bearer. The browser reaches a verified claim
    /// either that way or from the Google hop the callback stamps; nothing outside this machine ever
    /// collects the address, so the work-email policy runs before any code.
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

    /// The email step both surfaces share: an empty turn asks for the address, a submitted one
    /// validates the work-email policy through `ClaimWorkflow::start` and, only once it passes, has
    /// WorkOS mail the code — so a denylisted address is refused with no code sent.
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
                directive("say", &["ufo · flyingobject.ai"]),
                directive("ask", &["Enter your work email:"]),
            ]);
        }
        if let Err(error) = self.claims.start(body, channel, session).await {
            return render(&[
                install.to_vec(),
                directive("say", &[&error.to_string()]),
                directive("ask", &["Enter your work email:"]),
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

    /// Grade the code. A refusal that lost a race to another attempt re-reads the claim: if that
    /// attempt already verified it, this turn resolves rather than reporting a stale refusal.
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
            "Enter your work email:"
        };
        render(&[
            install.to_vec(),
            directive("say", &[&error.to_string()]),
            directive("ask", &[prompt]),
        ])
    }

    /// A verified claim, resolved to one workspace. With no candidate the domain founds its own
    /// (behind the invite gate); with candidates the member picks, and the create option appears only
    /// where a grant would actually admit it.
    async fn resolve(&self, claim: &OnboardClaim, body: &str, install: &[u8]) -> Vec<u8> {
        let choices = match self
            .workspaces
            .choices(&claim.email_domain, &claim.email)
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

    /// Which workspace this turn lands on, or the menu that asks. A single candidate the member could
    /// not have founded instead is entered without a prompt; anything else is a choice.
    async fn choose(
        &self,
        claim: &OnboardClaim,
        body: &str,
        install: &[u8],
        choices: &[WorkspaceChoice],
    ) -> Result<Chosen, SeatError> {
        let create_available = claim.invite_id.is_some()
            || self
                .invites
                .available(&claim.email_domain)
                .await
                .unwrap_or(false);
        if choices.len() == 1 && !create_available {
            let ensured = self
                .workspaces
                .join(&choices[0], &claim.email_domain, &claim.email)
                .await?;
            return Ok(Chosen::Ensured(ensured, false));
        }
        let create_label = format!("Create {} workspace", claim.email_domain);
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
            .join(selected, &claim.email_domain, &claim.email)
            .await?;
        Ok(Chosen::Ensured(ensured, false))
    }

    /// Open this domain's workspace with whatever the intake form recorded about the customer, so
    /// their agent opens knowing who it works for.
    async fn create(&self, claim: &OnboardClaim) -> Result<EnsuredWorkspace, SeatError> {
        let profile: Option<SignupProfile> = self
            .invites
            .profile(&claim.email_domain)
            .await
            .unwrap_or(None);
        let carried = profile.map(|profile| crate::shared::SignupProfile {
            business: profile.business,
            goals: profile.goals,
        });
        self.workspaces
            .create(&claim.email_domain, &claim.email, carried.as_ref())
            .await
    }

    /// A refusal screen, or `None` when the flow may open the workspace. The verified email domain is
    /// the whole answer: a live grant for it opens the workspace with nothing to type, and every
    /// refusal ends the session rather than prompting, because the member holds no secret that could
    /// change the outcome. The claim keeps its verified email, so re-running the installer once a
    /// grant lands resolves the same claim.
    async fn invite_gate(&self, claim: &OnboardClaim, install: &[u8]) -> Option<Vec<u8>> {
        if claim.invite_id.is_some() {
            return None;
        }
        let domain = &claim.email_domain;
        let redemption = match self.invites.redeem(domain, claim.claim_id).await {
            Ok(redemption) => redemption,
            Err(InviteError::Pool(_) | InviteError::Query(_)) => {
                return Some(self.failed(install, "the invite ledger is unreachable"))
            }
            Err(error) => return Some(self.failed(install, &error.to_string())),
        };
        match redemption {
            Redemption::Accepted(_) => None,
            Redemption::Expired { expires_at } => Some(render(&[
                install.to_vec(),
                directive(
                    "say",
                    &[&format!(
                        "The invite for {domain} expired {} UTC.",
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
                    &[&format!("The invite for {domain} was already used.")],
                ),
                directive("say", &["Contact us if you cannot sign in."]),
                directive("exit", &["0"]),
            ])),
            Redemption::Unknown => Some(render(&[
                install.to_vec(),
                directive("say", &[&format!("{domain} has no invite.")]),
                directive(
                    "say",
                    &[&format!("Join the waitlist: https://{}", self.apex_host)],
                ),
                directive("exit", &["0"]),
            ])),
        }
    }

    /// The signed-in cap: token and workspace for every member, plus the `debugger` directive — the
    /// operator session debugger's base URL — only when the claim's channel-verified email domain is
    /// the operator's. The gate is server-side policy; every renderer (the terminal client drops
    /// unknown verbs) simply carries or ignores the extra line.
    ///
    /// A terminal owner caps on `choose` rather than `ask`, so setting up billing costs one
    /// selection. A joined teammate caps on the ordinary prompt — billing is not theirs to set up.
    /// The web renderer ends on its signed-in card rather than a prompt, so it is never handed a menu
    /// it cannot drive.
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
            if ensured.admin {
                directive("slack", &[SLACK_CHOICE])
            } else {
                Vec::new()
            },
            if ensured.admin && claim.surface != WEB_CHANNEL {
                directive(
                    "choose",
                    &[FIRST_MOVE_PROMPT, SLACK_CHOICE, BILLING_CHOICE, TOUR_CHOICE],
                )
            } else {
                directive("ask", &[PROMPT])
            },
        ])
    }

    /// The workspace service could not answer. A member cannot act on which hop failed, so they read
    /// one sentence and the detail goes to the log.
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

/// Either the screen this turn answers with, or the workspace it landed on.
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
}

pub fn router(state: GatewayState) -> Router {
    let mut router = Router::new()
        .route("/healthz", get(healthz))
        .route("/ufo", get(serve_script))
        .route("/ufo/bin/{target}", get(client_binary))
        .route("/fleet", get(fleet))
        .route("/login", get(login))
        .route(AUTH_START_PATH, get(auth_start))
        .route(AUTH_CALLBACK_PATH, get(auth_callback))
        .route("/v1/onboard/web", post(onboard_web))
        .route("/v1/onboard/{channel}", post(onboard));
    if state.console_mode {
        router = router.route(AUTH_CONSOLE_PATH, get(auth_console));
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

/// The native terminal client for one build target, from the deploy's binary directory — an
/// unconfigured deploy answers the same 404 an unknown target does.
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

async fn login() -> Response {
    (
        [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
        LOGIN_PAGE,
    )
        .into_response()
}

/// The `Continue with Google` button's target: it mints the onboarding session, binds it to this
/// browser as the cookie the page cannot read (never taken from the query), and 302s to WorkOS with
/// `provider=GoogleOAuth`, so WorkOS goes straight to Google with no hosted page. Packing the carry
/// and unpacking it again is the validation: a conversation or artifact the query invented is dropped
/// here, under the same rules the callback reads it back by, so the state carries only what will be
/// honored.
async fn auth_start(
    State(state): State<GatewayState>,
    Query(query): Query<HashMap<String, String>>,
) -> Response {
    let secret = &state.onboarding.token_secret;
    let session = seal_session(&fresh_session(), secret);
    let carry = AuthCarry {
        session: session.clone(),
        conversation: query.get("c").cloned(),
        artifact: query.get("a").cloned(),
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

/// The local stand-in for the Google hop, mounted only under `WORKOS_MODE=console`: the dev enters a
/// work email that the callback reads as the code. The cookie the start path set still binds the
/// return, so the walk past this page is a real return's own.
async fn auth_console(Query(query): Query<HashMap<String, String>>) -> Response {
    let state = query.get("state").cloned().unwrap_or_default();
    (
        [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
        console_signin_page(&state),
    )
        .into_response()
}

/// The Google hop's return, honored only in the browser that left: the state has to be one this
/// gateway signed, and the session it names has to be the one the start path bound as the cookie, so
/// a state a caller wrote — or one of ours replayed anywhere else — verifies nothing and writes no
/// claim under a session someone chose. The email the Google account carries passes the same
/// work-email policy a typed address does, so a personal `@gmail.com` Google account is refused; the
/// refusal rides back to the page as a sentence rather than a status. A callback for a session that
/// already holds a claim resolves that claim, so a repeated return signs the same member in.
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
                    Err(ClaimError::Email(WorkEmailError::NotWork(domain))) => {
                        Some(format!("{domain} is not a work email domain."))
                    }
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

/// The page's session is the `__Host-ufo_onboard` cookie the gateway mints and seals server-side.
/// Two things keep a verified email bound to the browser that earned it, so a planted value can never
/// key its claim. The `__Host-` prefix is host-only by the cookie the browser enforces: it refuses to
/// set such a cookie with a `Domain`, and no sibling host can write the app host's copy. And a
/// presented cookie is trusted only to *continue* a claim it already keys: a claim is only ever
/// started under a session freshly minted here, so even a validly sealed value a caller obtained by
/// asking is discarded and re-minted unless it already stands behind a live claim.
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
    let (session, minted) = match (presented, live) {
        (Some(session), Some(_)) => (session, None),
        _ => {
            let sealed = seal_session(&fresh_session(), &secret);
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

/// The turn's body, bounded next to the read. A body over the cap is refused rather than truncated:
/// a truncated address or code would be graded as if the member typed it.
async fn request_body(request: Request) -> Result<String, RequestInputError> {
    let bytes = axum::body::to_bytes(request.into_body(), MAX_BODY_BYTES + 1)
        .await
        .map_err(|_| RequestInputError("Request body is too large.".to_string()))?;
    if bytes.len() > MAX_BODY_BYTES {
        return Err(RequestInputError("Request body is too large.".to_string()));
    }
    Ok(String::from_utf8_lossy(&bytes).trim().to_string())
}

/// The sealed `__Host-ufo_onboard` session the request carries. Reading every cookie of that name and
/// honoring the one that opens under our signature is belt against a forged or duplicate value; a
/// value carrying no signature of ours is read as absent.
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

/// The one sanctioned way to bind the onboarding session. It takes no `Domain`, so the cookie is
/// host-only and can never widen to a parent domain; `HttpOnly` and `Secure` are not negotiable, and
/// the `__Host-` prefix the name carries makes the browser enforce the same.
fn set_session_cookie(response: &mut Response, sealed: &str) {
    let cookie =
        format!("{ONBOARD_SESSION_COOKIE}={sealed}; HttpOnly; Secure; SameSite=Lax; Path=/");
    if let Ok(value) = cookie.parse() {
        response.headers_mut().append(header::SET_COOKIE, value);
    }
}

fn fresh_session() -> String {
    let mut bytes = [0_u8; ONBOARD_SESSION_BYTES];
    getrandom::fill(&mut bytes).expect("the platform has a random source");
    base64::Engine::encode(&base64::engine::general_purpose::URL_SAFE_NO_PAD, bytes)
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

/// New-workspace invites gate signup unless a deploy explicitly opts out (local dev). Unset means
/// required, so forgetting the knob never opens signup; garbage fails loud, never defaults.
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
