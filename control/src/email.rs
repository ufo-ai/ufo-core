//! Work-email policy and the outbound mail sender.
//!
//! `WorkEmailPolicy` rejects free, personal, and disposable domains so a workspace maps to a real
//! organization — the denylist fails CLOSED and a malformed address is rejected up front. The
//! sender delivers a rendered subject, text body, and HTML body through SESv2, carrying no message
//! shape of its own: `invite_email` is the one message the service sends, since WorkOS delivers the
//! sign-in code.
//! Signing is pure CPU (hmac/sha256) so it runs inline, and every network call is async.
//! Credentials are the pod's IRSA web identity (`AWS_ROLE_ARN` + `AWS_WEB_IDENTITY_TOKEN_FILE`,
//! injected by the EKS pod identity webhook from the gateway ServiceAccount's annotation),
//! exchanged at STS per send. Missing SES configuration fails loud.
//!
//! `UFO_CONTROL_EMAIL_MODE` picks the sender: `ses` (the default) is the SES delivery above;
//! `console` logs the message instead of sending it, so a local stack reads it from the process log
//! with no SES account. An unrecognized mode fails loud.

use std::collections::{BTreeMap, HashSet};
use std::path::PathBuf;
use std::sync::LazyLock;

use chrono::{DateTime, Utc};
use hmac::{Hmac, Mac};
use quick_xml::events::Event;
use quick_xml::Reader;
use regex::Regex;
use sha2::{Digest, Sha256};

const DOMAIN_LABEL: &str = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?";

static EMAIL_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!(
        r"^[^@\s]+@({DOMAIN_LABEL}(?:\.{DOMAIN_LABEL})+)\.?$"
    ))
    .expect("the email pattern compiles")
});

pub const FREE_EMAIL_DOMAINS: &[&str] = &[
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "ymail.com",
    "hotmail.com",
    "outlook.com",
    "live.com",
    "msn.com",
    "aol.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "proton.me",
    "protonmail.com",
    "pm.me",
    "gmx.com",
    "mail.com",
    "zoho.com",
    "yandex.com",
    "fastmail.com",
    "hey.com",
];

pub const DISPOSABLE_EMAIL_DOMAINS: &[&str] = &[
    "mailinator.com",
    "guerrillamail.com",
    "10minutemail.com",
    "tempmail.com",
    "temp-mail.org",
    "throwawaymail.com",
    "yopmail.com",
    "trashmail.com",
    "getnada.com",
    "dispostable.com",
    "sharklasers.com",
    "maildrop.cc",
];

pub const PUBLIC_BASE_URL_ENV: &str = "UFO_PUBLIC_BASE_URL";
pub const DEFAULT_PUBLIC_BASE_URL: &str = "https://ufo.ai";

pub const INVITE_SUBJECT: &str = "Your invitation";

const INVITE_HTML: &str = r##"<!doctype html>
<html lang="en">
<body style="margin:0;padding:0;background:#FAF9F7;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#FAF9F7;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="440" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:440px;">
<tr><td align="center" style="padding-bottom:20px;">
<img src="{workspace_url}/login/logo.png" alt="ufo" width="72" height="18" style="display:block;border:0;width:72px;height:18px;"></td></tr>
<tr><td style="padding:20px;background:#FAF9F7;border:1px solid #EBEAE9;border-radius:4px;">
<h1 style="margin:0 0 8px;font:600 16px/1.5 system-ui,sans-serif;color:#191A1A;">Your invitation</h1>
<p style="margin:0 0 20px;font:14px/1.5 system-ui,sans-serif;color:#919090;">Sign in as {email}. This invitation expires {expires} UTC.</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td align="center" bgcolor="#191A1A" style="border-radius:4px;">
<a href="{first_run_url}" style="display:block;padding:10px 18px;font:500 15px/1.5 system-ui,sans-serif;color:#FAF9F7;text-decoration:none;">Sign in</a>
</td></tr></table>
</td></tr></table>
</td></tr></table>
</body>
</html>
"##;

pub const SES_SERVICE: &str = "ses";
pub const SES_PATH: &str = "/v2/email/outbound-emails";
pub const SES_TIMEOUT_SECONDS: u64 = 10;
pub const ERROR_BODY_CHARS: usize = 1000;

pub const SES_SENDER_ENV: &str = "UFO_SES_SENDER";
pub const SES_REGION_ENV: &str = "UFO_SES_REGION";
pub const AWS_ROLE_ARN_ENV: &str = "AWS_ROLE_ARN";
pub const AWS_WEB_IDENTITY_TOKEN_FILE_ENV: &str = "AWS_WEB_IDENTITY_TOKEN_FILE";
pub const DEFAULT_SES_REGION: &str = "us-east-1";

pub const EMAIL_MODE_ENV: &str = "UFO_CONTROL_EMAIL_MODE";
pub const SES_EMAIL_MODE: &str = "ses";
pub const CONSOLE_EMAIL_MODE: &str = "console";

const STS_VERSION: &str = "2011-06-15";
const STS_SESSION_NAME: &str = "ufo-gateway-email";
const STS_SESSION_SECONDS: u32 = 900;
const STS_TIMEOUT_SECONDS: u64 = 10;

/// The email is not an acceptable work email — bad format or a denylisted domain.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum WorkEmailError {
    #[error("The email address is malformed.")]
    Malformed,
    #[error("{0} is not a work email domain.")]
    NotWork(String),
}

/// Lowercased (address, domain), the domain a strict hostname with its FQDN root dot dropped — so a
/// trailing-dot or otherwise malformed domain cannot carry a denylisted address (`gmail.com.`) past
/// the policy.
pub fn normalize_email(email: &str) -> Result<(String, String), WorkEmailError> {
    let candidate = email.trim().to_lowercase();
    let captures = EMAIL_PATTERN
        .captures(&candidate)
        .ok_or(WorkEmailError::Malformed)?;
    let domain = captures[1].to_string();
    Ok((candidate, domain))
}

#[derive(Debug, Clone)]
pub struct WorkEmailPolicy {
    denylist: HashSet<&'static str>,
}

impl Default for WorkEmailPolicy {
    fn default() -> Self {
        Self {
            denylist: FREE_EMAIL_DOMAINS
                .iter()
                .chain(DISPOSABLE_EMAIL_DOMAINS)
                .copied()
                .collect(),
        }
    }
}

impl WorkEmailPolicy {
    pub fn validate(&self, email: &str) -> Result<String, WorkEmailError> {
        let (_, domain) = normalize_email(email)?;
        if self.denylist.contains(domain.as_str()) {
            return Err(WorkEmailError::NotWork(domain));
        }
        Ok(domain)
    }
}

/// The public front-door host from a base URL, path and scheme stripped. A schemeless value has no
/// authority to take, so it fails rather than silently reading the host out of the path.
pub fn apex_host(base_url: &str) -> Result<String, EmailConfigError> {
    let after_scheme = base_url
        .split_once("://")
        .map(|(_, rest)| rest)
        .ok_or_else(|| EmailConfigError::BadBaseUrl(base_url.to_string()))?;
    let host = after_scheme
        .split(['/', '?', '#'])
        .next()
        .unwrap_or_default();
    if host.is_empty() {
        return Err(EmailConfigError::BadBaseUrl(base_url.to_string()));
    }
    Ok(host.to_string())
}

/// One invitation rendered for clients that accept HTML and clients that accept only text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InviteEmail {
    pub subject: String,
    pub text: String,
    pub html: String,
}

/// The invitation delivered by `ufo-control invite`.
pub fn invite_email(
    email: &str,
    expires_at: DateTime<Utc>,
    apex_host: &str,
    workspace_url: &str,
) -> Result<InviteEmail, WorkEmailError> {
    normalize_email(email)?;
    let expires = expires_at.format("%Y-%m-%d %H:%M");
    let workspace_url = workspace_url.trim_end_matches('/');
    let first_run_url = format!("{workspace_url}/surface/web#/first-run");
    let text = format!(
        "Sign in: {first_run_url}\n\n\
         Or install it: curl -fsSL https://{apex_host}/ufo | sh\n\n\
         Sign in as {email}. This invitation expires {expires} UTC.\n"
    );
    let html = INVITE_HTML
        .replace("{workspace_url}", &html_escape(workspace_url))
        .replace("{first_run_url}", &html_escape(&first_run_url))
        .replace("{email}", &html_escape(email))
        .replace("{expires}", &expires.to_string());
    Ok(InviteEmail {
        subject: INVITE_SUBJECT.to_string(),
        text,
        html,
    })
}

fn html_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SesCredentials {
    pub access_key: String,
    pub secret_key: String,
    pub session_token: String,
}

#[derive(Debug, thiserror::Error)]
pub enum EmailConfigError {
    #[error("{PUBLIC_BASE_URL_ENV} must be a base URL like {DEFAULT_PUBLIC_BASE_URL}, got {0:?}")]
    BadBaseUrl(String),
    #[error("{0} is unset — required by the email sender")]
    MissingEnv(&'static str),
    #[error(
        "{EMAIL_MODE_ENV}={0:?} is not a valid email mode \
         (expected {SES_EMAIL_MODE:?} or {CONSOLE_EMAIL_MODE:?})"
    )]
    UnknownMode(String),
}

#[derive(Debug, thiserror::Error)]
pub enum SendError {
    #[error("SES SendEmail returned {status}: {body}")]
    Ses { status: u16, body: String },
    #[error("STS AssumeRoleWithWebIdentity returned {status}: {body}")]
    Sts { status: u16, body: String },
    #[error("STS AssumeRoleWithWebIdentity response is missing {0}")]
    MissingCredential(&'static str),
    #[error("STS AssumeRoleWithWebIdentity response is not XML: {0}")]
    MalformedXml(String),
    #[error("the projected web identity token at {path} is unreadable: {source}")]
    TokenFile {
        path: PathBuf,
        source: std::io::Error,
    },
    #[error(transparent)]
    Http(#[from] reqwest::Error),
}

/// Where the two AWS calls go. Held apart from `region` so a test can point them at a local server;
/// a deploy builds them from the region and never names them.
#[derive(Debug, Clone)]
pub struct AwsEndpoints {
    pub ses: String,
    pub sts: String,
}

impl AwsEndpoints {
    pub fn for_region(region: &str) -> Self {
        Self {
            ses: format!("https://email.{region}.amazonaws.com"),
            sts: format!("https://sts.{region}.amazonaws.com/"),
        }
    }
}

/// SESv2 `SendEmail` with a local SigV4 signer. `source` is the verified From address; `region`
/// selects the signing scope. Credentials are the pod's IRSA web identity: the projected token at
/// `token_file` is exchanged for `role_arn` at STS on every send (`AssumeRoleWithWebIdentity` is
/// unsigned, so no bootstrap credential exists) — onboarding email is rare enough that a credential
/// cache would be dead weight.
#[derive(Debug, Clone)]
pub struct SesEmailSender {
    pub source: String,
    pub region: String,
    pub role_arn: String,
    pub token_file: PathBuf,
    pub endpoints: AwsEndpoints,
}

impl SesEmailSender {
    pub async fn send(
        &self,
        email: &str,
        subject: &str,
        text: &str,
        html: &str,
    ) -> Result<(), SendError> {
        let credentials = self.assume_role().await?;
        let body = serde_json::json!({
            "FromEmailAddress": self.source,
            "Destination": {"ToAddresses": [email]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": subject},
                    "Body": {
                        "Text": {"Data": text},
                        "Html": {"Data": html},
                    },
                }
            },
        })
        .to_string()
        .into_bytes();
        let url = format!("{}{SES_PATH}", self.endpoints.ses);
        let host = host_of(&url);
        let headers = sigv4_headers(&host, &body, &self.region, &credentials, Utc::now());
        let mut request = client(SES_TIMEOUT_SECONDS)?.post(&url).body(body);
        for (name, value) in &headers {
            request = request.header(name, value);
        }
        let response = request.send().await?;
        let status = response.status();
        if status.is_client_error() || status.is_server_error() {
            return Err(SendError::Ses {
                status: status.as_u16(),
                body: clipped(&response.text().await?),
            });
        }
        Ok(())
    }

    async fn assume_role(&self) -> Result<SesCredentials, SendError> {
        let token = tokio::fs::read_to_string(&self.token_file)
            .await
            .map_err(|source| SendError::TokenFile {
                path: self.token_file.clone(),
                source,
            })?;
        let response = client(STS_TIMEOUT_SECONDS)?
            .post(&self.endpoints.sts)
            .form(&[
                ("Action", "AssumeRoleWithWebIdentity"),
                ("Version", STS_VERSION),
                ("RoleArn", self.role_arn.as_str()),
                ("RoleSessionName", STS_SESSION_NAME),
                ("WebIdentityToken", token.trim()),
                ("DurationSeconds", &STS_SESSION_SECONDS.to_string()),
            ])
            .send()
            .await?;
        let status = response.status();
        let payload = response.text().await?;
        if status.is_client_error() || status.is_server_error() {
            return Err(SendError::Sts {
                status: status.as_u16(),
                body: clipped(&payload),
            });
        }
        parse_assume_role_credentials(&payload)
    }
}

fn client(timeout_seconds: u64) -> Result<reqwest::Client, reqwest::Error> {
    reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(timeout_seconds))
        .build()
}

fn clipped(body: &str) -> String {
    body.chars().take(ERROR_BODY_CHARS).collect()
}

fn host_of(url: &str) -> String {
    url.split_once("://")
        .map(|(_, rest)| rest.split('/').next().unwrap_or_default())
        .unwrap_or_default()
        .to_string()
}

/// The three credential fields STS nests under `Credentials`. Read positionally rather than by
/// XPath: the response carries an `Expiration` and an `AssumedRoleUser` this never needs, and the
/// element names inside `Credentials` are unambiguous.
pub fn parse_assume_role_credentials(payload: &str) -> Result<SesCredentials, SendError> {
    let mut reader = Reader::from_str(payload);
    let mut inside_credentials = false;
    let mut current = String::new();
    let mut found: BTreeMap<String, String> = BTreeMap::new();
    let mut buffer = Vec::new();
    loop {
        match reader.read_event_into(&mut buffer) {
            Ok(Event::Start(element)) => {
                let name = local_name(element.name().as_ref());
                if name == "Credentials" {
                    inside_credentials = true;
                } else if inside_credentials {
                    current = name;
                }
            }
            Ok(Event::Text(text)) if inside_credentials && !current.is_empty() => {
                let value = text
                    .unescape()
                    .map_err(|error| SendError::MalformedXml(error.to_string()))?
                    .to_string();
                found.entry(current.clone()).or_insert(value);
            }
            Ok(Event::End(element)) => {
                if local_name(element.name().as_ref()) == "Credentials" {
                    inside_credentials = false;
                }
                current.clear();
            }
            Ok(Event::Eof) => break,
            Err(error) => return Err(SendError::MalformedXml(error.to_string())),
            _ => {}
        }
        buffer.clear();
    }
    let take = |name: &'static str| -> Result<String, SendError> {
        found
            .get(name)
            .filter(|value| !value.is_empty())
            .cloned()
            .ok_or(SendError::MissingCredential(name))
    };
    Ok(SesCredentials {
        access_key: take("AccessKeyId")?,
        secret_key: take("SecretAccessKey")?,
        session_token: take("SessionToken")?,
    })
}

fn local_name(qualified: &[u8]) -> String {
    let text = String::from_utf8_lossy(qualified);
    text.rsplit(':').next().unwrap_or_default().to_string()
}

/// The SigV4 headers for one SES POST. Every header the signature covers is also sent, and the
/// session token is among them — an omitted `x-amz-security-token` signs a request AWS refuses.
pub fn sigv4_headers(
    host: &str,
    body: &[u8],
    region: &str,
    credentials: &SesCredentials,
    now: DateTime<Utc>,
) -> BTreeMap<String, String> {
    let amz_date = now.format("%Y%m%dT%H%M%SZ").to_string();
    let date_stamp = now.format("%Y%m%d").to_string();
    let payload_hash = format!("{:x}", Sha256::digest(body));
    let mut headers = BTreeMap::from([
        ("content-type".to_string(), "application/json".to_string()),
        ("host".to_string(), host.to_string()),
        ("x-amz-content-sha256".to_string(), payload_hash.clone()),
        ("x-amz-date".to_string(), amz_date.clone()),
        (
            "x-amz-security-token".to_string(),
            credentials.session_token.clone(),
        ),
    ]);
    let signed_headers = headers.keys().cloned().collect::<Vec<_>>().join(";");
    let canonical_headers = headers
        .iter()
        .map(|(key, value)| format!("{key}:{value}\n"))
        .collect::<String>();
    let canonical_request =
        format!("POST\n{SES_PATH}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}");
    let scope = format!("{date_stamp}/{region}/{SES_SERVICE}/aws4_request");
    let string_to_sign = format!(
        "AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{:x}",
        Sha256::digest(canonical_request.as_bytes())
    );
    let signing_key = signing_key(&credentials.secret_key, &date_stamp, region);
    let mut mac =
        <Hmac<Sha256>>::new_from_slice(&signing_key).expect("hmac-sha256 accepts any key length");
    mac.update(string_to_sign.as_bytes());
    let signature = format!("{:x}", mac.finalize().into_bytes());
    headers.insert(
        "authorization".to_string(),
        format!(
            "AWS4-HMAC-SHA256 Credential={}/{scope}, SignedHeaders={signed_headers}, \
             Signature={signature}",
            credentials.access_key
        ),
    );
    headers
}

fn signing_key(secret_key: &str, date_stamp: &str, region: &str) -> Vec<u8> {
    let mut key = format!("AWS4{secret_key}").into_bytes();
    for message in [date_stamp, region, SES_SERVICE, "aws4_request"] {
        let mut mac =
            <Hmac<Sha256>>::new_from_slice(&key).expect("hmac-sha256 accepts any key length");
        mac.update(message.as_bytes());
        key = mac.finalize().into_bytes().to_vec();
    }
    key
}

/// The two deliveries, one closed set: SES for a deploy, and a log line for a local stack that has
/// no SES account. A trait object would buy nothing — nothing else will ever send mail here.
#[derive(Debug, Clone)]
pub enum EmailSender {
    Ses(Box<SesEmailSender>),
    Console,
}

impl EmailSender {
    pub async fn send(
        &self,
        email: &str,
        subject: &str,
        text: &str,
        html: &str,
    ) -> Result<(), SendError> {
        match self {
            Self::Ses(sender) => sender.send(email, subject, text, html).await,
            Self::Console => {
                tracing::info!(
                    target: "ufo_control::email",
                    "email (console mode) → {email} | {subject} | {text}"
                );
                Ok(())
            }
        }
    }
}

pub fn email_sender_from_env() -> Result<EmailSender, EmailConfigError> {
    let mode = std::env::var(EMAIL_MODE_ENV).unwrap_or_else(|_| SES_EMAIL_MODE.to_string());
    if mode == CONSOLE_EMAIL_MODE {
        return Ok(EmailSender::Console);
    }
    if mode != SES_EMAIL_MODE {
        return Err(EmailConfigError::UnknownMode(mode));
    }
    let region = std::env::var(SES_REGION_ENV).unwrap_or_else(|_| DEFAULT_SES_REGION.to_string());
    Ok(EmailSender::Ses(Box::new(SesEmailSender {
        source: require_env(SES_SENDER_ENV)?,
        endpoints: AwsEndpoints::for_region(&region),
        region,
        role_arn: require_env(AWS_ROLE_ARN_ENV)?,
        token_file: PathBuf::from(require_env(AWS_WEB_IDENTITY_TOKEN_FILE_ENV)?),
    })))
}

pub fn public_apex_host_from_env() -> Result<String, EmailConfigError> {
    let base_url =
        std::env::var(PUBLIC_BASE_URL_ENV).unwrap_or_else(|_| DEFAULT_PUBLIC_BASE_URL.to_string());
    apex_host(&base_url)
}

fn require_env(name: &'static str) -> Result<String, EmailConfigError> {
    std::env::var(name)
        .ok()
        .filter(|value| !value.is_empty())
        .ok_or(EmailConfigError::MissingEnv(name))
}
