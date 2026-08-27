//! Signup-email policy and the outbound mail sender.
//!
//! `SignupEmailPolicy` rejects disposable domains and maps a shared personal-mail domain to the
//! exact address rather than granting that domain authority over a workspace. A malformed address
//! is rejected up front. The sender delivers a rendered subject, a plain-text body, and the HTML
//! alternative beside it where
//! one is rendered, through SESv2, carrying no message shape of its own
//! beyond `invite_email`, the grant's own invitation; the teammate invitation renders in
//! `invite_delivery`, and WorkOS delivers the sign-in code.
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
    "hotmail.co.uk",
    "hotmail.fr",
    "hotmail.de",
    "hotmail.it",
    "hotmail.es",
    "hotmail.be",
    "hotmail.nl",
    "hotmail.se",
    "hotmail.no",
    "hotmail.dk",
    "hotmail.ca",
    "hotmail.com.au",
    "hotmail.com.br",
    "hotmail.co.jp",
    "live.co.uk",
    "live.fr",
    "live.de",
    "live.it",
    "live.nl",
    "live.be",
    "live.se",
    "live.no",
    "live.dk",
    "live.ca",
    "live.com.au",
    "live.com.mx",
    "live.cn",
    "live.jp",
    "live.at",
    "live.ie",
    "outlook.fr",
    "outlook.de",
    "outlook.es",
    "outlook.it",
    "outlook.be",
    "outlook.dk",
    "outlook.jp",
    "outlook.pt",
    "outlook.sa",
    "outlook.com.au",
    "outlook.com.br",
    "outlook.com.tr",
    "outlook.co.id",
    "outlook.co.th",
    "outlook.in",
    "outlook.ie",
    "outlook.cl",
    "outlook.hu",
    "outlook.kr",
    "passport.com",
    "windowslive.com",
    "yahoo.co.uk",
    "yahoo.co.jp",
    "yahoo.co.in",
    "yahoo.co.id",
    "yahoo.co.kr",
    "yahoo.co.nz",
    "yahoo.co.th",
    "yahoo.fr",
    "yahoo.de",
    "yahoo.es",
    "yahoo.it",
    "yahoo.ca",
    "yahoo.se",
    "yahoo.dk",
    "yahoo.no",
    "yahoo.fi",
    "yahoo.gr",
    "yahoo.ie",
    "yahoo.pt",
    "yahoo.pl",
    "yahoo.ro",
    "yahoo.cz",
    "yahoo.hu",
    "yahoo.com.au",
    "yahoo.com.br",
    "yahoo.com.mx",
    "yahoo.com.ar",
    "yahoo.com.co",
    "yahoo.com.pe",
    "yahoo.com.ph",
    "yahoo.com.sg",
    "yahoo.com.hk",
    "yahoo.com.tw",
    "yahoo.com.tr",
    "yahoo.com.vn",
    "rocketmail.com",
    "sbcglobal.net",
    "ameritech.net",
    "flash.net",
    "web.de",
    "gmx.de",
    "gmx.net",
    "gmx.at",
    "gmx.ch",
    "gmx.eu",
    "t-online.de",
    "freenet.de",
    "arcor.de",
    "posteo.de",
    "mailbox.org",
    "bluewin.ch",
    "aon.at",
    "chello.at",
    "mail.ru",
    "internet.ru",
    "bk.ru",
    "inbox.ru",
    "list.ru",
    "yandex.ru",
    "ya.ru",
    "yandex.by",
    "yandex.kz",
    "yandex.ua",
    "rambler.ru",
    "lenta.ru",
    "autorambler.ru",
    "myrambler.ru",
    "ro.ru",
    "ukr.net",
    "i.ua",
    "meta.ua",
    "bigmir.net",
    "qq.com",
    "foxmail.com",
    "163.com",
    "126.com",
    "yeah.net",
    "sina.com",
    "sina.cn",
    "sohu.com",
    "aliyun.com",
    "tom.com",
    "21cn.com",
    "139.com",
    "189.cn",
    "vip.163.com",
    "vip.126.com",
    "vip.qq.com",
    "naver.com",
    "daum.net",
    "hanmail.net",
    "nate.com",
    "kakao.com",
    "docomo.ne.jp",
    "ezweb.ne.jp",
    "softbank.ne.jp",
    "nifty.com",
    "biglobe.ne.jp",
    "ocn.ne.jp",
    "so-net.ne.jp",
    "excite.co.jp",
    "orange.fr",
    "wanadoo.fr",
    "free.fr",
    "laposte.net",
    "sfr.fr",
    "neuf.fr",
    "bbox.fr",
    "numericable.fr",
    "aliceadsl.fr",
    "club-internet.fr",
    "voila.fr",
    "libero.it",
    "virgilio.it",
    "alice.it",
    "tiscali.it",
    "tin.it",
    "inwind.it",
    "email.it",
    "fastwebnet.it",
    "poste.it",
    "terra.com",
    "terra.es",
    "telefonica.net",
    "movistar.es",
    "ono.com",
    "uol.com.br",
    "bol.com.br",
    "terra.com.br",
    "ig.com.br",
    "globo.com",
    "sapo.pt",
    "clix.pt",
    "telia.com",
    "online.no",
    "broadpark.no",
    "start.no",
    "sol.dk",
    "mail.dk",
    "post.dk",
    "spray.se",
    "comhem.se",
    "bredband.net",
    "telenor.dk",
    "ziggo.nl",
    "kpnmail.nl",
    "planet.nl",
    "home.nl",
    "casema.nl",
    "hetnet.nl",
    "telenet.be",
    "skynet.be",
    "scarlet.be",
    "seznam.cz",
    "email.cz",
    "centrum.cz",
    "volny.cz",
    "atlas.cz",
    "post.cz",
    "azet.sk",
    "zoznam.sk",
    "wp.pl",
    "o2.pl",
    "onet.pl",
    "onet.eu",
    "interia.pl",
    "gazeta.pl",
    "poczta.onet.pl",
    "op.pl",
    "tlen.pl",
    "freemail.hu",
    "citromail.hu",
    "abv.bg",
    "mail.bg",
    "dir.bg",
    "seznam.sk",
    "inbox.lv",
    "inbox.lt",
    "one.lv",
    "mail.ee",
    "hot.ee",
    "mynet.com",
    "superonline.com",
    "ttmail.com",
    "maktoob.com",
    "rediffmail.com",
    "sify.com",
    "indiatimes.com",
    "in.com",
    "singnet.com.sg",
    "pacific.net.sg",
    "streamyx.com",
    "tm.net.my",
    "comcast.net",
    "verizon.net",
    "att.net",
    "bellsouth.net",
    "cox.net",
    "charter.net",
    "earthlink.net",
    "juno.com",
    "netzero.net",
    "optonline.net",
    "roadrunner.com",
    "rr.com",
    "twc.com",
    "windstream.net",
    "frontier.com",
    "centurylink.net",
    "embarqmail.com",
    "q.com",
    "prodigy.net",
    "netscape.net",
    "compuserve.com",
    "excite.com",
    "lycos.com",
    "aim.com",
    "sympatico.ca",
    "rogers.com",
    "shaw.ca",
    "telus.net",
    "videotron.ca",
    "bell.net",
    "cogeco.ca",
    "btinternet.com",
    "btopenworld.com",
    "sky.com",
    "virginmedia.com",
    "virgin.net",
    "talktalk.net",
    "tiscali.co.uk",
    "ntlworld.com",
    "blueyonder.co.uk",
    "plus.net",
    "orangehome.co.uk",
    "hotmail.co.nz",
    "eircom.net",
    "iol.ie",
    "bigpond.com",
    "bigpond.net.au",
    "optusnet.com.au",
    "iinet.net.au",
    "tpg.com.au",
    "internode.on.net",
    "xtra.co.nz",
    "webmail.co.za",
    "telkomsa.net",
    "mweb.co.za",
    "tutanota.com",
    "tutanota.de",
    "tuta.io",
    "tutamail.com",
    "hushmail.com",
    "mailfence.com",
    "runbox.com",
    "startmail.com",
    "countermail.com",
    "disroot.org",
    "riseup.net",
    "autistici.org",
    "safe-mail.net",
    "zoho.eu",
    "zohomail.com",
    "gmx.us",
    "usa.com",
    "email.com",
    "europe.com",
    "consultant.com",
    "myself.com",
    "post.com",
    "writeme.com",
    "dr.com",
    "engineer.com",
    "cheerful.com",
    "techie.com",
    "hotmail.ch",
    "hotmail.at",
    "hotmail.gr",
    "hotmail.pt",
    "hotmail.cz",
    "hotmail.hu",
    "hotmail.rs",
    "hotmail.fi",
    "hotmail.lv",
    "hotmail.lt",
    "hotmail.ee",
    "hotmail.ro",
    "hotmail.bg",
    "hotmail.sk",
    "hotmail.si",
    "hotmail.hr",
    "hotmail.my",
    "hotmail.sg",
    "hotmail.ph",
    "hotmail.kr",
    "hotmail.co.th",
    "hotmail.co.kr",
    "hotmail.co.il",
    "hotmail.co.in",
    "hotmail.co.za",
    "hotmail.com.ar",
    "hotmail.com.mx",
    "hotmail.com.tr",
    "hotmail.com.vn",
    "hotmail.com.hk",
    "hotmail.com.tw",
    "live.co.za",
    "live.co.kr",
    "live.co.in",
    "live.com.pt",
    "live.com.ar",
    "live.hk",
    "live.in",
    "live.ru",
    "live.fi",
    "live.gr",
    "live.pl",
    "live.pt",
    "live.ro",
    "live.sk",
    "live.si",
    "live.hu",
    "live.cz",
    "live.com.sg",
    "live.com.my",
    "live.com.ph",
    "live.co.il",
    "outlook.co.nz",
    "outlook.co.za",
    "outlook.co.il",
    "outlook.gr",
    "outlook.at",
    "outlook.ch",
    "outlook.cz",
    "outlook.fi",
    "outlook.lv",
    "outlook.lt",
    "outlook.my",
    "outlook.ph",
    "outlook.sg",
    "outlook.vn",
    "outlook.nl",
    "outlook.se",
    "outlook.no",
    "outlook.com.gr",
    "outlook.com.pe",
    "outlook.com.vn",
    "outlook.com.es",
    "outlook.com.pk",
    "outlook.com.hk",
    "outlook.com.gt",
    "outlook.com.co",
    "yahoo.co.za",
    "yahoo.co.il",
    "yahoo.nl",
    "yahoo.be",
    "yahoo.ch",
    "yahoo.at",
    "yahoo.lu",
    "yahoo.bg",
    "yahoo.hr",
    "yahoo.si",
    "yahoo.sk",
    "yahoo.lt",
    "yahoo.lv",
    "yahoo.ee",
    "yahoo.rs",
    "yahoo.ua",
    "yahoo.by",
    "yahoo.kz",
    "yahoo.in",
    "yahoo.com.my",
    "yahoo.com.pk",
    "yahoo.com.sa",
    "yahoo.com.eg",
    "yahoo.com.ua",
    "yahoo.com.cn",
    "yahoo.com.is",
    "gmx.co.uk",
    "gmx.fr",
    "gmx.es",
    "gmx.it",
    "gmx.li",
    "gmx.tm",
    "gmx.biz",
    "gmx.info",
    "gmx.org",
    "gmx.hk",
    "aol.co.uk",
    "aol.de",
    "aol.fr",
    "aol.it",
    "aol.es",
    "aol.jp",
    "aol.in",
    "aol.ca",
    "aol.com.au",
    "aol.com.br",
    "aol.com.mx",
    "aol.pl",
    "aol.se",
    "aol.dk",
    "aol.nl",
    "aol.be",
    "aol.ch",
    "aol.at",
    "yandex.com.tr",
    "yandex.fr",
    "yandex.az",
    "yandex.uz",
    "yandex.eu",
    "zoho.in",
    "zoho.com.au",
    "zoho.com.cn",
    "zoho.jp",
    "msn.co.uk",
    "msn.cn",
    "msn.com.au",
    "protonmail.ch",
    "hush.com",
    "hushmail.me",
    "fastmail.fm",
    "fastmail.co.uk",
    "fastmail.us",
    "fastmail.net",
    "fastmail.to",
    "fastmail.es",
    "fastmail.im",
    "fastmail.jp",
    "fastmail.mx",
    "fastmail.nl",
    "fastmail.se",
    "messagingengine.com",
    "sent.com",
    "xsmail.com",
    "150mail.com",
    "mailbolt.com",
    "mailhaven.com",
    "eml.cc",
    "fmgirl.com",
    "fmguy.com",
    "nospammail.net",
    "mm.st",
    "love.com",
    "games.com",
    "wow.com",
    "ygm.com",
    "cs.com",
    "rediff.com",
    "zohomail.eu",
    "gmail.co.uk",
    "inbox.com",
    "lycos.co.uk",
    "operamail.com",
    "mail.de",
    "emailn.de",
    "mailo.com",
    "net-c.com",
    "gmx.com.br",
    "bluemail.ch",
    "kabelmail.de",
    "unitybox.de",
    "vodafonemail.de",
    "vodafone.de",
    "o2online.de",
    "telekom.de",
    "magenta.at",
    "a1.net",
    "utanet.at",
    "inode.at",
    "liwest.at",
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
    "mailnesia.com",
    "fakeinbox.com",
    "spamgourmet.com",
    "moakt.com",
    "emailondeck.com",
    "tempr.email",
    "discard.email",
    "luxusmail.org",
    "tempmailo.com",
    "minuteinbox.com",
    "mohmal.com",
    "inboxkitten.com",
    "temp-mail.io",
    "tempmail.net",
    "20minutemail.com",
    "33mail.com",
    "anonaddy.com",
    "burnermail.io",
    "spambox.us",
    "mailcatch.com",
    "mintemail.com",
    "spam4.me",
    "grr.la",
    "guerrillamail.info",
    "guerrillamail.biz",
    "guerrillamail.de",
    "pokemail.net",
    "spamherelots.com",
    "tempinbox.com",
    "throwawayemail.com",
    "trbvm.com",
    "yopmail.fr",
    "yopmail.net",
    "cool.fr.nf",
    "jetable.fr.nf",
    "nospam.ze.tc",
    "dodgit.com",
    "mailexpire.com",
    "mytrashmail.com",
    "no-spam.ws",
    "sogetthis.com",
    "spamfree24.org",
    "tempemail.net",
    "tempomail.fr",
    "wegwerfmail.de",
    "wegwerfmail.net",
    "trash-mail.com",
    "byom.de",
    "einrot.com",
    "fleckens.hu",
    "gustr.com",
    "superrito.com",
    "teleworm.us",
    "armyspy.com",
    "cuvox.de",
    "dayrep.com",
    "jourrapide.com",
    "rhyta.com",
    "1secmail.com",
    "1secmail.org",
    "1secmail.net",
    "kzccv.com",
    "qiott.com",
    "vusra.com",
    "wuuvo.com",
    "icznn.com",
    "ezztt.com",
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
pub const MAX_RETRY_AFTER_SECONDS: f64 = 60.0;

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

/// The email is not an acceptable signup identity.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum EmailError {
    #[error("The email address is malformed.")]
    Malformed,
    #[error("{0} is a disposable email domain.")]
    Disposable(String),
}

/// Lowercased (address, domain), the domain a strict hostname with its FQDN root dot dropped — so a
/// trailing-dot domain cannot change how an address such as `someone@gmail.com.` is classified.
pub fn normalize_email(email: &str) -> Result<(String, String), EmailError> {
    let candidate = email.trim().to_lowercase();
    let captures = EMAIL_PATTERN
        .captures(&candidate)
        .ok_or(EmailError::Malformed)?;
    let domain = captures[1].to_string();
    let local = candidate
        .split_once('@')
        .map(|(local, _)| local)
        .ok_or(EmailError::Malformed)?;
    Ok((format!("{local}@{domain}"), domain))
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SignupEmail {
    pub address: String,
    pub domain: String,
    pub subject: String,
}

#[derive(Debug, Clone)]
pub struct SignupEmailPolicy {
    personal_domains: HashSet<&'static str>,
    disposable_domains: HashSet<&'static str>,
}

impl Default for SignupEmailPolicy {
    fn default() -> Self {
        Self {
            personal_domains: FREE_EMAIL_DOMAINS.iter().copied().collect(),
            disposable_domains: DISPOSABLE_EMAIL_DOMAINS.iter().copied().collect(),
        }
    }
}

impl SignupEmailPolicy {
    pub fn validate(&self, email: &str) -> Result<SignupEmail, EmailError> {
        let (address, domain) = normalize_email(email)?;
        if self.disposable_domains.contains(domain.as_str()) {
            return Err(EmailError::Disposable(domain));
        }
        let subject = if self.personal_domains.contains(domain.as_str()) {
            address.clone()
        } else {
            domain.clone()
        };
        Ok(SignupEmail {
            address,
            domain,
            subject,
        })
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
) -> Result<InviteEmail, EmailError> {
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
    /// SES answered, so nothing was accepted. `retry_after` carries the bounded `Retry-After` a
    /// throttle names, which is the only schedule a caller should prefer over its own.
    #[error("SES SendEmail returned {status}: {body}")]
    Ses {
        status: u16,
        body: String,
        retry_after: Option<f64>,
    },
    #[error("STS AssumeRoleWithWebIdentity returned {status}: {body}")]
    Sts { status: u16, body: String },
    /// The credential exchange never completed, so the SES POST was never made — which is what
    /// tells a caller a resend cannot duplicate anything.
    #[error("STS AssumeRoleWithWebIdentity could not be reached: {0}")]
    StsUnreachable(String),
    #[error("STS AssumeRoleWithWebIdentity response is missing {0}")]
    MissingCredential(&'static str),
    #[error("STS AssumeRoleWithWebIdentity response is not XML: {0}")]
    MalformedXml(String),
    #[error("the projected web identity token at {path} is unreadable: {source}")]
    TokenFile {
        path: PathBuf,
        source: std::io::Error,
    },
    /// The SES POST alone: a client that would not build, a connection that never opened, or a
    /// request that left with no answer read. Which of the three decides whether a resend is safe.
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
        html: Option<&str>,
    ) -> Result<(), SendError> {
        let credentials = self.assume_role().await?;
        let mut content = serde_json::Map::new();
        content.insert("Text".to_string(), serde_json::json!({"Data": text}));
        if let Some(html) = html {
            content.insert("Html".to_string(), serde_json::json!({"Data": html}));
        }
        let body = serde_json::json!({
            "FromEmailAddress": self.source,
            "Destination": {"ToAddresses": [email]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": subject},
                    "Body": content,
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
            let retry_after = retry_after_seconds(response.headers());
            return Err(SendError::Ses {
                status: status.as_u16(),
                body: clipped(&response.text().await?),
                retry_after,
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
        let response = client(STS_TIMEOUT_SECONDS)
            .map_err(|error| SendError::StsUnreachable(error.to_string()))?
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
            .await
            .map_err(|error| SendError::StsUnreachable(error.to_string()))?;
        let status = response.status();
        let payload = response
            .text()
            .await
            .map_err(|error| SendError::StsUnreachable(error.to_string()))?;
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

/// The `Retry-After` a throttled response names, in seconds, clamped next to the call that reads
/// it: a header naming an hour must not park a workspace's invitations for one.
fn retry_after_seconds(headers: &reqwest::header::HeaderMap) -> Option<f64> {
    headers
        .get("retry-after")
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.trim().parse::<f64>().ok())
        .map(|seconds| seconds.clamp(0.0, MAX_RETRY_AFTER_SECONDS))
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
        html: Option<&str>,
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
