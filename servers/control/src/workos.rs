//! WorkOS custody of email verification: Magic Auth for the email and code both the browser and the
//! terminal collect on our own pages, and a Google OAuth hop (`provider=GoogleOAuth`) for the
//! browser's `Continue with Google`. WorkOS answers one question — does this person control this
//! email — and no hosted WorkOS page collects the address: our gateway does, so the work-email
//! policy runs before any code is mailed.
//!
//! `confirm` grades the member's code: WorkOS answers a code it will not redeem with the OAuth
//! `invalid_grant`, whether the digits are wrong, expired, or already spent, so that answer is a
//! wrong code the member may retype and the claim's own time-to-live is what ends the session.
//! Every other failure is a refusal the member cannot retype past.
//!
//! The four calls are made directly rather than through a vendor SDK, because the SDK's own bodies
//! are these four literals. Each `grant_type` and path below is the one the Python client sends, and
//! `tests/workos_it.rs` drives them against a local server so a drift in either is a failing test
//! rather than a broken sign-in.

use std::time::Duration;

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use hmac::{Hmac, Mac};
use serde::Deserialize;
use sha2::Sha256;

use crate::web::LOGO_PATH;

pub const WORKOS_API_KEY_ENV: &str = "WORKOS_API_KEY";
pub const WORKOS_CLIENT_ID_ENV: &str = "WORKOS_CLIENT_ID";
pub const WORKOS_REDIRECT_URI_ENV: &str = "WORKOS_REDIRECT_URI";
pub const WORKOS_MODE_ENV: &str = "WORKOS_MODE";
pub const WORKOS_BASE_URL_ENV: &str = "WORKOS_BASE_URL";
pub const WORKOS_MODE: &str = "workos";
pub const CONSOLE_MODE: &str = "console";

pub const DEFAULT_BASE_URL: &str = "https://api.workos.com";
pub const AUTH_START_PATH: &str = "/v1/onboard/auth/start";
pub const AUTH_CALLBACK_PATH: &str = "/v1/onboard/auth/callback";
pub const AUTH_CONSOLE_PATH: &str = "/v1/onboard/auth/console";
pub const CONSOLE_CODE: &str = "000000";
pub const GOOGLE_PROVIDER: &str = "GoogleOAuth";
pub const GRANT_REFUSED: &str = "invalid_grant";
pub const SIGN_IN_FAILED: &str = "Sign-in failed. Try again.";
pub const CODE_NOT_SENT: &str = "Could not send the verification code. Try again.";
/// A bound on the session a state may name, over and above the signature that already makes it
/// ours. It has to admit the longest value this gateway mints — a sealed session carrying the join
/// door's mark, which is the random id, the mark's tag and expiry, and the seal.
pub const MAX_STATE_SESSION_BYTES: usize = 192;
pub const STATE_SEPARATOR: char = '.';
pub const WORKOS_TIMEOUT_SECONDS: u64 = 10;

const STATE_KEY_LABEL: &[u8] = b"onboard-auth-state";
const COOKIE_KEY_LABEL: &[u8] = b"onboard-session-cookie";
const ARTIFACT_CARRY_PREFIX: &str = "/artifacts/";
const MAGIC_AUTH_GRANT: &str = "urn:workos:oauth:grant-type:magic-auth:code";
const AUTHORIZATION_CODE_GRANT: &str = "authorization_code";

/// The onboarding flow renders the message to the member.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{0}")]
pub struct VerificationError(pub String);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthCarry {
    pub session: String,
    pub conversation: Option<String>,
    pub artifact: Option<String>,
    pub first_run: bool,
    pub debug: bool,
    pub invite: bool,
    pub join: bool,
}

#[derive(serde::Serialize, Deserialize)]
struct StatePayload {
    s: String,
    c: Option<String>,
    a: Option<String>,
    f: bool,
    d: bool,
    i: bool,
    j: bool,
}

/// The OAuth `state`: the onboarding session and what the member clicked to reach sign-in, under a
/// signature. The session it names is the one the callback writes a verified email under, so only a
/// state this gateway packed may name one.
pub fn pack_state(carry: &AuthCarry, secret: &str) -> String {
    let payload = StatePayload {
        s: carry.session.clone(),
        c: carry.conversation.clone(),
        a: carry.artifact.clone(),
        f: carry.first_run,
        d: carry.debug,
        i: carry.invite,
        j: carry.join,
    };
    let json = serde_json::to_vec(&payload).expect("the carry serializes");
    let body = URL_SAFE_NO_PAD.encode(json);
    let signature = state_signature(&body, secret);
    format!("{body}{STATE_SEPARATOR}{signature}")
}

/// The carry the state names, or an error when the signature is not this gateway's or the state
/// names no usable session. A conversation or artifact that is not its own shape is dropped rather
/// than refused: the member still signs in, landing on a new conversation instead of somewhere the
/// query invented.
pub fn unpack_state(raw: &str, secret: &str) -> Result<AuthCarry, StateError> {
    let (body, signature) = raw.split_once(STATE_SEPARATOR).unwrap_or((raw, ""));
    if !constant_time_eq(signature, &state_signature(body, secret)) {
        return Err(StateError::NotOurs);
    }
    let decoded = URL_SAFE_NO_PAD
        .decode(body)
        .map_err(|_| StateError::NotThePackedCarry)?;
    let payload: StatePayload =
        serde_json::from_slice(&decoded).map_err(|_| StateError::NotThePackedCarry)?;
    if payload.s.is_empty() {
        return Err(StateError::NoSession);
    }
    if payload.s.len() > MAX_STATE_SESSION_BYTES {
        return Err(StateError::OversizedSession);
    }
    Ok(AuthCarry {
        session: payload.s,
        conversation: payload.c.filter(|value| is_uuid_shaped(value)),
        artifact: payload
            .a
            .filter(|value| value.starts_with(ARTIFACT_CARRY_PREFIX)),
        first_run: payload.f,
        debug: payload.d,
        invite: payload.i,
        join: payload.j,
    })
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum StateError {
    #[error("state carries no signature of ours")]
    NotOurs,
    #[error("state is not the packed carry")]
    NotThePackedCarry,
    #[error("state carries no session")]
    NoSession,
    #[error("state carries an oversized session")]
    OversizedSession,
}

/// A canonical 8-4-4-4-12 lowercase hex uuid, the shape a conversation id takes. Anything else is a
/// value the query invented, so it is dropped rather than carried into a redirect.
pub(crate) fn is_uuid_shaped(value: &str) -> bool {
    let groups = [8, 4, 4, 4, 12];
    let mut parts = value.split('-');
    for expected in groups {
        let Some(part) = parts.next() else {
            return false;
        };
        if part.len() != expected
            || !part
                .chars()
                .all(|character| character.is_ascii_digit() || ('a'..='f').contains(&character))
        {
            return false;
        }
    }
    parts.next().is_none()
}

/// Signed under a subkey of the gateway's token secret, so a state and a member's bearer never come
/// out of one key.
fn state_signature(body: &str, secret: &str) -> String {
    subkey_signature(secret, STATE_KEY_LABEL, body.as_bytes())
}

/// The `__Host-ufo_onboard` cookie value: a minted session under a signature, so only a value this
/// gateway sealed can key a claim. The sealed string is the session everywhere it is compared —
/// cookie, state, and claim — never the raw id, and a value carrying no signature of ours opens to
/// nothing.
pub fn seal_session(session: &str, secret: &str) -> String {
    let signature = cookie_signature(session, secret);
    format!("{session}{STATE_SEPARATOR}{signature}")
}

/// The minted id a sealed cookie names, or None when the value carries no signature of ours — so a
/// planted or forged cookie is read as absent and a fresh session minted in its place, never trusted
/// to key a claim.
pub fn open_session(value: &str, secret: &str) -> Option<String> {
    let (session, signature) = value.split_once(STATE_SEPARATOR)?;
    if session.is_empty() || !constant_time_eq(signature, &cookie_signature(session, secret)) {
        return None;
    }
    Some(session.to_string())
}

/// Under a different subkey than the state signature, so the cookie seal and the state seal never
/// come out of one key.
fn cookie_signature(session: &str, secret: &str) -> String {
    subkey_signature(secret, COOKIE_KEY_LABEL, session.as_bytes())
}

pub(crate) fn subkey_signature(secret: &str, label: &[u8], message: &[u8]) -> String {
    let mut derive =
        <Hmac<Sha256>>::new_from_slice(secret.as_bytes()).expect("hmac takes any key length");
    derive.update(label);
    let key = derive.finalize().into_bytes();
    let mut sign = <Hmac<Sha256>>::new_from_slice(&key).expect("hmac takes any key length");
    sign.update(message);
    format!("{:x}", sign.finalize().into_bytes())
}

/// Signature comparison that does not leak where two strings first differ.
pub(crate) fn constant_time_eq(left: &str, right: &str) -> bool {
    if left.len() != right.len() {
        return false;
    }
    left.bytes()
        .zip(right.bytes())
        .fold(0_u8, |difference, (a, b)| difference | (a ^ b))
        == 0
}

/// The two verifiers, one closed set. Only the identity proof differs between them: the claim, the
/// cookie binding, and the signed state are the deploy's own in both, so the code paths a member
/// reaches locally are the ones a real sign-in exercises.
#[derive(Debug, Clone)]
pub enum Verifier {
    Workos(WorkosVerifier),
    Console,
}

impl Verifier {
    pub fn authorization_url(&self, state: &str) -> String {
        match self {
            Self::Workos(verifier) => verifier.authorization_url(state),
            Self::Console => format!("{AUTH_CONSOLE_PATH}?state={}", encode_query(state)),
        }
    }

    pub async fn exchange(&self, code: &str) -> Result<String, VerificationError> {
        match self {
            Self::Workos(verifier) => verifier.exchange(code).await,
            Self::Console => Ok(code.trim().to_lowercase()),
        }
    }

    pub async fn begin(&self, email: &str) -> Result<(), VerificationError> {
        match self {
            Self::Workos(verifier) => verifier.begin(email).await,
            Self::Console => {
                tracing::info!(
                    target: "ufo_control::workos",
                    "onboard.console.code email={email} code={CONSOLE_CODE}"
                );
                Ok(())
            }
        }
    }

    pub async fn confirm(&self, email: &str, code: &str) -> Result<bool, VerificationError> {
        match self {
            Self::Workos(verifier) => verifier.confirm(email, code).await,
            Self::Console => Ok(code.trim() == CONSOLE_CODE),
        }
    }
}

/// The one question WorkOS answers: does this person control this email.
#[derive(Debug, Clone)]
pub struct WorkosVerifier {
    pub api_key: String,
    pub client_id: String,
    pub redirect_uri: String,
    pub base_url: String,
}

#[derive(Deserialize)]
struct AuthenticatedUser {
    email: String,
}

#[derive(Deserialize)]
struct AuthenticateResponse {
    user: AuthenticatedUser,
}

/// The `error` and `code` fields a WorkOS refusal carries. `invalid_grant` in either is a code the
/// member may retype; anything else is a refusal they cannot.
#[derive(Deserialize, Default)]
struct WorkosRefusal {
    error: Option<String>,
    code: Option<String>,
}

impl WorkosVerifier {
    /// The `Continue with Google` hop: WorkOS routes `provider=GoogleOAuth` straight to Google, with
    /// no hosted WorkOS page in between, and returns to the callback with the code.
    pub fn authorization_url(&self, state: &str) -> String {
        let base = self.base_url.trim_end_matches('/');
        format!(
            "{base}/user_management/authorize?client_id={}&provider={GOOGLE_PROVIDER}\
             &redirect_uri={}&response_type=code&state={}",
            encode_query(&self.client_id),
            encode_query(&self.redirect_uri),
            encode_query(state)
        )
    }

    pub async fn exchange(&self, code: &str) -> Result<String, VerificationError> {
        let body = serde_json::json!({
            "grant_type": AUTHORIZATION_CODE_GRANT,
            "client_id": self.client_id,
            "client_secret": self.api_key,
            "code": code,
        });
        let response = self.post("user_management/authenticate", body).await?;
        let status = response.status();
        let payload = response
            .text()
            .await
            .map_err(|_| VerificationError(SIGN_IN_FAILED.to_string()))?;
        if !status.is_success() {
            return Err(VerificationError(SIGN_IN_FAILED.to_string()));
        }
        let authenticated: AuthenticateResponse = serde_json::from_str(&payload)
            .map_err(|_| VerificationError(SIGN_IN_FAILED.to_string()))?;
        Ok(authenticated.user.email.trim().to_lowercase())
    }

    pub async fn begin(&self, email: &str) -> Result<(), VerificationError> {
        let body = serde_json::json!({"email": email});
        let response = self.post("user_management/magic_auth", body).await;
        match response {
            Ok(response) if response.status().is_success() => Ok(()),
            _ => Err(VerificationError(CODE_NOT_SENT.to_string())),
        }
    }

    pub async fn confirm(&self, email: &str, code: &str) -> Result<bool, VerificationError> {
        let body = serde_json::json!({
            "grant_type": MAGIC_AUTH_GRANT,
            "code": code,
            "email": email,
            "client_id": self.client_id,
            "client_secret": self.api_key,
        });
        let response = self.post("user_management/authenticate", body).await?;
        let status = response.status();
        if status.is_success() {
            return Ok(true);
        }
        let payload = response.text().await.unwrap_or_default();
        let refusal: WorkosRefusal = serde_json::from_str(&payload).unwrap_or_default();
        if refusal.error.as_deref() == Some(GRANT_REFUSED)
            || refusal.code.as_deref() == Some(GRANT_REFUSED)
        {
            return Ok(false);
        }
        Err(VerificationError(SIGN_IN_FAILED.to_string()))
    }

    async fn post(
        &self,
        path: &str,
        body: serde_json::Value,
    ) -> Result<reqwest::Response, VerificationError> {
        let base = self.base_url.trim_end_matches('/');
        reqwest::Client::builder()
            .timeout(Duration::from_secs(WORKOS_TIMEOUT_SECONDS))
            .build()
            .map_err(|_| VerificationError(SIGN_IN_FAILED.to_string()))?
            .post(format!("{base}/{path}"))
            .bearer_auth(&self.api_key)
            .json(&body)
            .send()
            .await
            .map_err(|_| VerificationError(SIGN_IN_FAILED.to_string()))
    }
}

/// Percent-encode everything outside the unreserved set, so a state or a redirect URI cannot end a
/// query parameter early.
fn encode_query(value: &str) -> String {
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

/// The card the sign-in pages share: the same palette the portal paints, so a member crosses from
/// either screen into their workspace without the surface changing under them.
const CONSOLE_STYLE: &str = r#"
  :root { color-scheme: light dark;
          --surface: light-dark(#FAF9F7, #191A1A); --fill: light-dark(#F4F3F2, #262929);
          --edge: light-dark(#EBEAE9, #323535); --ink: light-dark(#191A1A, #F5F5F5);
          --ink-soft: light-dark(#919090, #A7A9A9); --radius: 4px; }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100svh; display: flex; align-items: center;
         justify-content: center; padding: 24px 16px;
         background: var(--surface); color: var(--ink);
         font: 15px/1.5 system-ui, sans-serif; }
  main { width: min(440px, 100%); display: flex; flex-direction: column; gap: 60px; }
  header { display: flex; justify-content: center; }
  header img { display: block; width: 72px; height: 18px; }
  @media (prefers-color-scheme: dark) { header img { filter: invert(1); } }
  .card { display: flex; flex-direction: column; gap: 16px; padding: 20px;
          border: 1px solid var(--edge); border-radius: var(--radius);
          background: var(--surface); }
  .head { display: flex; flex-direction: column; gap: 6px; }
  h1 { margin: 0; font-size: 16px; font-weight: 600; line-height: 1; }
  p { margin: 0; font-size: 14px; color: var(--ink-soft); }
  form { display: flex; flex-direction: column; gap: 16px; }
  .field { display: flex; flex-direction: column; gap: 8px; }
  label { font-size: 14px; font-weight: 600; color: var(--ink); }
  input { width: 100%; padding: 10px 12px; border-radius: var(--radius);
          background: var(--fill); color: var(--ink); font: inherit; font-size: 16px;
          border: 1px solid var(--edge); }
  button { width: 100%; padding: 10px 18px; border-radius: var(--radius);
           border: 1px solid transparent; background: var(--ink); color: var(--surface);
           font: inherit; font-weight: 500; cursor: pointer; }
"#;

/// The stand-in the Google hop lands on in `WORKOS_MODE=console`: a plain email form whose GET
/// reaches the same callback a real return does, carrying the state it was handed and the typed
/// address as the code the console verifier reads straight back. The card names the bypass and the
/// code the email step takes, both read off `CONSOLE_CODE`, so the screen cannot state a code the
/// verifier no longer accepts.
pub fn console_signin_page(state: &str) -> String {
    format!(
        "<!doctype html>\n\
         <html lang=\"en\"><head><meta charset=\"utf-8\">\n\
         <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n\
         <title>Developer sign-in · ufo</title>\n\
         <style>{CONSOLE_STYLE}</style></head>\n\
         <body><main>\n\
         <header><img src=\"{LOGO_PATH}\" alt=\"ufo\" width=\"72\" height=\"18\"></header>\n\
         <section class=\"card\">\n\
         <div class=\"head\">\n\
         <h1>Developer sign-in</h1>\n\
         <p>Enter your local development email. Authentication is bypassed with code \
         {CONSOLE_CODE}.</p>\n\
         </div>\n\
         <form action=\"{AUTH_CALLBACK_PATH}\" method=\"get\">\n\
         <input type=\"hidden\" name=\"state\" value=\"{}\">\n\
         <div class=\"field\">\n\
         <label for=\"code\">Email</label>\n\
         <input id=\"code\" name=\"code\" type=\"email\" placeholder=\"email@work.com\" autofocus required>\n\
         </div>\n\
         <button type=\"submit\">Continue</button>\n\
         </form>\n\
         </section>\n\
         </main></body></html>\n",
        escape_html(state)
    )
}

fn escape_html(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#x27;")
}

#[derive(Debug, thiserror::Error)]
pub enum WorkosConfigError {
    #[error("{WORKOS_MODE_ENV}={0:?} is not {WORKOS_MODE}|{CONSOLE_MODE}")]
    UnknownMode(String),
    #[error("{0} is unset — required by the gateway sign-in")]
    MissingEnv(&'static str),
}

pub fn workos_console_mode() -> Result<bool, WorkosConfigError> {
    Ok(mode()? == CONSOLE_MODE)
}

/// The gateway's verifier. `WORKOS_MODE=console` runs the credential-free dev verifier; the default
/// (`workos`) requires all three values, and the redirect URI is stated rather than derived from a
/// host: it is the origin that routes the callback to this process, which is the app host in a
/// hosted deploy and the gateway's own port locally. It has to match the URI registered in the same
/// WorkOS environment, so a deploy states the one string both ends hold.
pub fn verifier_from_env() -> Result<Verifier, WorkosConfigError> {
    if mode()? == CONSOLE_MODE {
        tracing::warn!(target: "ufo_control::workos", "gateway.workos.console_mode");
        return Ok(Verifier::Console);
    }
    Ok(Verifier::Workos(WorkosVerifier {
        api_key: require_env(WORKOS_API_KEY_ENV)?,
        client_id: require_env(WORKOS_CLIENT_ID_ENV)?,
        redirect_uri: require_env(WORKOS_REDIRECT_URI_ENV)?,
        base_url: std::env::var(WORKOS_BASE_URL_ENV)
            .ok()
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| DEFAULT_BASE_URL.to_string()),
    }))
}

fn mode() -> Result<String, WorkosConfigError> {
    let mode = std::env::var(WORKOS_MODE_ENV)
        .unwrap_or_else(|_| WORKOS_MODE.to_string())
        .trim()
        .to_lowercase();
    if mode != WORKOS_MODE && mode != CONSOLE_MODE {
        return Err(WorkosConfigError::UnknownMode(mode));
    }
    Ok(mode)
}

fn require_env(name: &'static str) -> Result<String, WorkosConfigError> {
    std::env::var(name)
        .ok()
        .filter(|value| !value.is_empty())
        .ok_or(WorkosConfigError::MissingEnv(name))
}
