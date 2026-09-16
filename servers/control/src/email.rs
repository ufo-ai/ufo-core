//! Credentials are the pod's IRSA web identity (`AWS_ROLE_ARN` + `AWS_WEB_IDENTITY_TOKEN_FILE`,
//! injected by the EKS pod identity webhook from the gateway ServiceAccount's annotation).

use std::collections::{BTreeMap, HashSet};
use std::path::PathBuf;
use std::sync::LazyLock;

use chrono::{DateTime, Utc};
use hmac::{Hmac, Mac};
use quick_xml::events::Event;
use quick_xml::Reader;
use regex::Regex;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use uuid::Uuid;

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
pub const SQS_SERVICE: &str = "sqs";
pub const SES_OUTBOUND_PATH: &str = "/v2/email/outbound-emails";
pub const JSON_CONTENT_TYPE: &str = "application/json";
pub const AWS_TIMEOUT_SECONDS: u64 = 10;
pub const ERROR_BODY_CHARS: usize = 1000;
pub const MAX_RETRY_AFTER_SECONDS: f64 = 60.0;
pub const CONTACT_PAGE: u32 = 1000;

const THROTTLED_SES_ERRORS: &[&str] = &["TooManyRequestsException", "ThrottlingException"];

pub const SEND_EMAIL: &str = "SendEmail";
pub const LIST_CONTACTS: &str = "ListContacts";
pub const GET_CONTACT: &str = "GetContact";
pub const UPDATE_CONTACT: &str = "UpdateContact";

const OPT_IN: &str = "OPT_IN";
const OPT_OUT: &str = "OPT_OUT";

pub const SES_SENDER_ENV: &str = "UFO_SES_SENDER";
pub const SES_CONFIGURATION_SET_ENV: &str = "UFO_SES_CONFIGURATION_SET";
pub const SES_REGION_ENV: &str = "UFO_SES_REGION";
pub const FOUNDER_SENDERS_ENV: &str = "UFO_FOUNDER_SENDERS";
pub const FOUNDER_CONFIGURATION_SET_ENV: &str = "UFO_FOUNDER_CONFIGURATION_SET";
pub const SES_CONTACT_LIST_ENV: &str = "UFO_SES_CONTACT_LIST";
pub const SES_PRODUCT_TOPIC_ENV: &str = "UFO_SES_PRODUCT_TOPIC";
pub const FOUNDER_TOPIC_ENV: &str = "UFO_FOUNDER_TOPIC";
pub const FOUNDER_FEEDBACK_QUEUE_ENV: &str = "UFO_FOUNDER_FEEDBACK_QUEUE_URL";
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

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum EmailError {
    #[error("The email address is malformed.")]
    Malformed,
    #[error("{0} is a disposable email domain.")]
    Disposable(String),
}

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

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InviteEmail {
    pub subject: String,
    pub text: String,
    pub html: String,
}

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
    #[error("{FOUNDER_SENDERS_ENV} entry {0:?} is not an address or a `Name <address>`")]
    BadSender(String),
}

#[derive(Debug, thiserror::Error)]
pub enum AwsError {
    #[error("{service} {operation} returned {status}: {body}")]
    Api {
        service: &'static str,
        operation: &'static str,
        status: u16,
        body: String,
        retry_after: Option<f64>,
    },
    #[error("STS AssumeRoleWithWebIdentity returned {status}: {body}")]
    Sts { status: u16, body: String },
    /// The credential exchange never completed, so the call it was for was never made — which is what
    /// tells a caller a repeat cannot duplicate anything.
    #[error("STS AssumeRoleWithWebIdentity could not be reached: {0}")]
    StsUnreachable(String),
    #[error("STS AssumeRoleWithWebIdentity response is missing {0}")]
    MissingCredential(&'static str),
    #[error("STS AssumeRoleWithWebIdentity response is not XML: {0}")]
    MalformedXml(String),
    #[error("{service} answered with no {field}: {body}")]
    Unreadable {
        service: &'static str,
        field: &'static str,
        body: String,
    },
    #[error("the projected web identity token at {path} is unreadable: {source}")]
    TokenFile {
        path: PathBuf,
        source: std::io::Error,
    },
    #[error(transparent)]
    Http(#[from] reqwest::Error),
}

/// What an AWS refusal means for the row that caused it. `Transient` is a certain non-send — the
/// credential exchange failed, nothing opened, or SES throttled — so repeating it duplicates
/// nothing. `Unanswered` is a send whose outcome is unknown, which no caller may repeat.
#[derive(Debug, thiserror::Error)]
pub enum SendVerdict {
    #[error("{message}")]
    Transient {
        message: String,
        retry_after: Option<f64>,
    },
    #[error("{0}")]
    Terminal(String),
    #[error("{0}")]
    Unanswered(String),
}

pub fn verdict(error: AwsError) -> SendVerdict {
    match error {
        AwsError::Api {
            status: 429,
            body,
            retry_after,
            ..
        } => SendVerdict::Transient {
            message: format!("SES SendEmail returned 429: {body}"),
            retry_after,
        },
        AwsError::Api { status, body, .. } if status >= 500 => SendVerdict::Transient {
            message: format!("SES SendEmail returned {status}: {body}"),
            retry_after: None,
        },
        AwsError::Api { status, body, .. } => {
            let message = format!("SES SendEmail returned {status}: {body}");
            match THROTTLED_SES_ERRORS.iter().any(|name| body.contains(name)) {
                true => SendVerdict::Transient {
                    message,
                    retry_after: None,
                },
                false => SendVerdict::Terminal(message),
            }
        }
        AwsError::Sts { status, body } if status == 429 || status >= 500 => {
            SendVerdict::Transient {
                message: format!("STS AssumeRoleWithWebIdentity returned {status}: {body}"),
                retry_after: None,
            }
        }
        AwsError::Sts { status, body } => SendVerdict::Terminal(format!(
            "STS AssumeRoleWithWebIdentity returned {status}: {body}"
        )),
        AwsError::StsUnreachable(message) => SendVerdict::Transient {
            message: format!("STS AssumeRoleWithWebIdentity: {message}"),
            retry_after: None,
        },
        AwsError::TokenFile { path, source } => SendVerdict::Transient {
            message: format!(
                "the projected web identity token at {path:?} is unreadable: {source}"
            ),
            retry_after: None,
        },
        AwsError::MissingCredential(name) => SendVerdict::Terminal(format!(
            "STS AssumeRoleWithWebIdentity response is missing {name}"
        )),
        AwsError::MalformedXml(message) => SendVerdict::Terminal(format!(
            "STS AssumeRoleWithWebIdentity response is not XML: {message}"
        )),
        AwsError::Unreadable {
            service,
            field,
            body,
        } => SendVerdict::Terminal(format!("{service} answered with no {field}: {body}")),
        AwsError::Http(error) if error.is_builder() || error.is_connect() => {
            SendVerdict::Transient {
                message: format!("SES SendEmail never opened: {error}"),
                retry_after: None,
            }
        }
        AwsError::Http(error) => {
            SendVerdict::Unanswered(format!("SES SendEmail was not answered: {error}"))
        }
    }
}

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

/// One signed POST to an AWS JSON API. `target` carries SQS's `x-amz-target`, which SES v2 does not
/// use; it is signed as well as sent, because a header covered by the signature and then omitted
/// signs a request AWS refuses.
#[derive(Debug, Clone)]
pub struct AwsCall<'a> {
    pub service: &'static str,
    pub operation: &'static str,
    /// The verb, which SigV4 signs as the first line of the canonical request. Every call here is
    /// a POST but the two that address one contact: SES takes the read as a GET and the write of a
    /// topic preference as a PUT.
    pub method: &'static str,
    pub url: &'a str,
    pub content_type: &'a str,
    pub target: Option<&'a str>,
    pub timeout_seconds: u64,
}

pub const POST: &str = "POST";
pub const PUT: &str = "PUT";
pub const GET: &str = "GET";

/// The projected token is exchanged for `role_arn` at STS on every call: `AssumeRoleWithWebIdentity`
/// is unsigned, so no bootstrap credential exists.
pub async fn assume_role(
    sts: &str,
    role_arn: &str,
    token_file: &std::path::Path,
) -> Result<SesCredentials, AwsError> {
    let token = tokio::fs::read_to_string(token_file)
        .await
        .map_err(|source| AwsError::TokenFile {
            path: token_file.to_path_buf(),
            source,
        })?;
    let response = client(STS_TIMEOUT_SECONDS)
        .map_err(|error| AwsError::StsUnreachable(error.to_string()))?
        .post(sts)
        .form(&[
            ("Action", "AssumeRoleWithWebIdentity"),
            ("Version", STS_VERSION),
            ("RoleArn", role_arn),
            ("RoleSessionName", STS_SESSION_NAME),
            ("WebIdentityToken", token.trim()),
            ("DurationSeconds", &STS_SESSION_SECONDS.to_string()),
        ])
        .send()
        .await
        .map_err(|error| AwsError::StsUnreachable(error.to_string()))?;
    let status = response.status();
    let payload = response
        .text()
        .await
        .map_err(|error| AwsError::StsUnreachable(error.to_string()))?;
    if status.is_client_error() || status.is_server_error() {
        return Err(AwsError::Sts {
            status: status.as_u16(),
            body: clipped(&payload),
        });
    }
    parse_assume_role_credentials(&payload)
}

pub async fn signed_post(
    call: &AwsCall<'_>,
    body: Vec<u8>,
    region: &str,
    credentials: &SesCredentials,
) -> Result<String, AwsError> {
    let headers = sigv4_headers(call, &body, region, credentials, Utc::now());
    let client = client(call.timeout_seconds)?;
    let mut request = match call.method {
        PUT => client.put(call.url),
        GET => client.get(call.url),
        _ => client.post(call.url),
    }
    .body(body);
    for (name, value) in &headers {
        request = request.header(name, value);
    }
    let response = request.send().await?;
    let status = response.status();
    if status.is_client_error() || status.is_server_error() {
        let retry_after = retry_after_seconds(response.headers());
        return Err(AwsError::Api {
            service: call.service,
            operation: call.operation,
            status: status.as_u16(),
            body: clipped(&response.text().await?),
            retry_after,
        });
    }
    Ok(response.text().await?)
}

/// The transactional sender. `configuration_set` is what makes a send reportable: SES publishes a
/// delivery event only for a message sent under one, and every event lands in the queue
/// `CampaignFeedback` already drains.
///
/// `contact_list` is the account's one list, shared with the campaign sender. A send that names a
/// topic on it is the send a member can leave: SES adds `List-Unsubscribe`, hosts the page the
/// header and the footer link point at, and refuses the next send to a contact who used it.
#[derive(Debug, Clone)]
pub struct SesEmailSender {
    pub source: String,
    pub configuration_set: String,
    pub contact_list: String,
    pub region: String,
    pub role_arn: String,
    pub token_file: PathBuf,
    pub endpoints: AwsEndpoints,
}

impl SesEmailSender {
    /// Put this address back on a topic it left through SES's own hosted page.
    ///
    /// SES holds that opt-out on its contact, and SES is what refuses the send — so deleting our
    /// row lifts nothing, and a member who asked to hear from us again would hear nothing and be
    /// told otherwise. An address SES holds no contact for has left no topic, so a `NotFound` is
    /// the same answer as a write: there is nothing to lift.
    ///
    /// `UnsubscribeAll` is cleared beside the topic because SES applies it over every topic
    /// preference the contact holds: the hosted page's "unsubscribe from all" sets it, and a write
    /// that opted the topic back in without lifting it would leave the send refused. Clearing it
    /// alone would resume every other topic too, and `UpdateContact` replaces the preference list
    /// it is given, so the contact is read first and the whole list is written back: the flag
    /// becomes an explicit `OPT_OUT` on each topic the member did not name.
    pub async fn resubscribe(&self, email: &str, topic: &str) -> Result<(), AwsError> {
        let credentials =
            assume_role(&self.endpoints.sts, &self.role_arn, &self.token_file).await?;
        let url = format!(
            "{}/v2/email/contact-lists/{}/contacts/{}",
            self.endpoints.ses,
            self.contact_list,
            path_segment(email)
        );
        let held = match signed_post(
            &ses_verb(&url, GET_CONTACT, GET),
            Vec::new(),
            &self.region,
            &credentials,
        )
        .await
        {
            Ok(answered) => answered,
            Err(AwsError::Api { status: 404, .. }) => return Ok(()),
            Err(error) => return Err(error),
        };
        let contact: HeldContact =
            serde_json::from_str(&held).map_err(|_| AwsError::Unreadable {
                service: SES_SERVICE,
                field: "TopicPreferences",
                body: clipped(&held),
            })?;
        let mut preferences: BTreeMap<&str, &str> = contact
            .topic_preferences
            .iter()
            .map(|held| (held.topic_name.as_str(), held.subscription_status.as_str()))
            .collect();
        if contact.unsubscribe_all {
            for held in contact
                .topic_default_preferences
                .iter()
                .chain(&contact.topic_preferences)
            {
                preferences.insert(&held.topic_name, OPT_OUT);
            }
        }
        preferences.insert(topic, OPT_IN);
        let body = serde_json::json!({
            "UnsubscribeAll": false,
            "TopicPreferences": preferences
                .into_iter()
                .map(|(name, status)| serde_json::json!({
                    "TopicName": name,
                    "SubscriptionStatus": status,
                }))
                .collect::<Vec<_>>(),
        })
        .to_string()
        .into_bytes();
        match signed_post(
            &ses_verb(&url, UPDATE_CONTACT, PUT),
            body,
            &self.region,
            &credentials,
        )
        .await
        {
            Ok(_) => Ok(()),
            Err(AwsError::Api { status: 404, .. }) => Ok(()),
            Err(error) => Err(error),
        }
    }

    /// The SES message id, which every delivery event this send later produces is keyed by.
    pub async fn send(
        &self,
        email: &str,
        subject: &str,
        text: &str,
        html: Option<&str>,
        topic: Option<&str>,
    ) -> Result<String, AwsError> {
        let credentials =
            assume_role(&self.endpoints.sts, &self.role_arn, &self.token_file).await?;
        let mut content = serde_json::Map::new();
        content.insert("Text".to_string(), serde_json::json!({"Data": text}));
        if let Some(html) = html {
            content.insert("Html".to_string(), serde_json::json!({"Data": html}));
        }
        let mut asked = serde_json::json!({
            "FromEmailAddress": self.source,
            "Destination": {"ToAddresses": [email]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": subject},
                    "Body": content,
                }
            },
            "ConfigurationSetName": self.configuration_set,
        });
        if let Some(topic) = topic {
            asked["ListManagementOptions"] = serde_json::json!({
                "ContactListName": self.contact_list,
                "TopicName": topic,
            });
        }
        let body = asked.to_string().into_bytes();
        let url = format!("{}{SES_OUTBOUND_PATH}", self.endpoints.ses);
        let answered = signed_post(
            &ses_call(&url, SEND_EMAIL),
            body,
            &self.region,
            &credentials,
        )
        .await?;
        let sent: SentMessage =
            serde_json::from_str(&answered).map_err(|_| AwsError::Unreadable {
                service: SES_SERVICE,
                field: "MessageId",
                body: clipped(&answered),
            })?;
        Ok(sent.message_id)
    }
}

/// One address a campaign may be sent from. `label` is what SES puts in the From header; `address`
/// is what the IAM condition and the Reply-To name.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Sender {
    pub label: String,
    pub address: String,
}

/// The configured list, in the order an operator sees it. A malformed entry raises rather than
/// being skipped: a display name carrying a comma would otherwise split into two senders, one of
/// which SES would refuse.
pub fn parse_senders(configured: &str) -> Result<Vec<Sender>, EmailConfigError> {
    let mut senders = Vec::new();
    for entry in configured
        .split(',')
        .map(str::trim)
        .filter(|e| !e.is_empty())
    {
        let address = match entry.split_once('<') {
            Some((_, rest)) => rest.trim_end_matches('>').trim(),
            None => entry,
        };
        let (normalized, _) =
            normalize_email(address).map_err(|_| EmailConfigError::BadSender(entry.to_string()))?;
        senders.push(Sender {
            label: entry.to_string(),
            address: normalized,
        });
    }
    if senders.is_empty() {
        return Err(EmailConfigError::BadSender(configured.to_string()));
    }
    Ok(senders)
}

/// The founder campaign sender. It differs from the transactional one in everything a bulk message
/// needs and a transactional one must not have: a reply-to a person reads, the configuration set
/// that publishes delivery events, and the contact list SES enforces the unsubscribe against.
#[derive(Debug, Clone)]
pub struct FounderSender {
    pub senders: Vec<Sender>,
    pub configuration_set: String,
    pub contact_list: String,
    pub topic: String,
    pub region: String,
    pub role_arn: String,
    pub token_file: PathBuf,
    pub endpoints: AwsEndpoints,
}

impl FounderSender {
    /// The SES message id, which every delivery event this send later produces is keyed by. The
    /// campaign names the address it froze, and `Reply-To` is that same address: a reply to one
    /// founder reaches that founder, not a shared alias none of them reads.
    pub async fn send(
        &self,
        from: &Sender,
        email: &str,
        subject: &str,
        text: &str,
        html: &str,
    ) -> Result<String, AwsError> {
        let credentials =
            assume_role(&self.endpoints.sts, &self.role_arn, &self.token_file).await?;
        let body = serde_json::json!({
            "FromEmailAddress": from.label,
            "ReplyToAddresses": [from.address],
            "Destination": {"ToAddresses": [email]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": subject},
                    "Body": {"Text": {"Data": text}, "Html": {"Data": html}},
                }
            },
            "ConfigurationSetName": self.configuration_set,
            "ListManagementOptions": {
                "ContactListName": self.contact_list,
                "TopicName": self.topic,
            },
        })
        .to_string()
        .into_bytes();
        let url = format!("{}{SES_OUTBOUND_PATH}", self.endpoints.ses);
        let answered = signed_post(
            &ses_call(&url, SEND_EMAIL),
            body,
            &self.region,
            &credentials,
        )
        .await?;
        let sent: SentMessage =
            serde_json::from_str(&answered).map_err(|_| AwsError::Unreadable {
                service: SES_SERVICE,
                field: "MessageId",
                body: clipped(&answered),
            })?;
        Ok(sent.message_id)
    }

    /// Every address SES holds as opted out of the topic — the gate's own answer, which outranks the
    /// unsubscribe events this deploy happened to consume. Only an explicit preference counts: the
    /// topic default is OPT_IN, and a contact who has said nothing has not opted out.
    pub async fn opted_out(&self) -> Result<HashSet<String>, AwsError> {
        let credentials =
            assume_role(&self.endpoints.sts, &self.role_arn, &self.token_file).await?;
        let url = format!(
            "{}/v2/email/contact-lists/{}/contacts/list",
            self.endpoints.ses, self.contact_list
        );
        let mut excluded = HashSet::new();
        let mut next: Option<String> = None;
        loop {
            let body = serde_json::json!({
                "Filter": {
                    "FilteredStatus": "OPT_OUT",
                    "TopicFilter": {
                        "TopicName": self.topic,
                        "UseDefaultIfPreferenceUnavailable": false,
                    },
                },
                "PageSize": CONTACT_PAGE,
                "NextToken": next,
            })
            .to_string()
            .into_bytes();
            let answered = signed_post(
                &ses_call(&url, LIST_CONTACTS),
                body,
                &self.region,
                &credentials,
            )
            .await?;
            let page: ContactPage =
                serde_json::from_str(&answered).map_err(|_| AwsError::Unreadable {
                    service: SES_SERVICE,
                    field: "Contacts",
                    body: clipped(&answered),
                })?;
            excluded.extend(
                page.contacts
                    .into_iter()
                    .map(|contact| contact.email_address.trim().to_lowercase()),
            );
            if page.next_token.is_none() {
                return Ok(excluded);
            }
            // A cursor that repeats would walk forever, and stopping on it would quietly return a
            // suppression set missing everyone past page one. Neither is safe, so it raises.
            if page.next_token == next {
                return Err(AwsError::Unreadable {
                    service: SES_SERVICE,
                    field: "NextToken",
                    body: clipped(&answered),
                });
            }
            next = page.next_token;
        }
    }
}

fn ses_call<'a>(url: &'a str, operation: &'static str) -> AwsCall<'a> {
    ses_verb(url, operation, POST)
}

fn ses_verb<'a>(url: &'a str, operation: &'static str, method: &'static str) -> AwsCall<'a> {
    AwsCall {
        service: SES_SERVICE,
        operation,
        method,
        url,
        content_type: JSON_CONTENT_TYPE,
        target: None,
        timeout_seconds: AWS_TIMEOUT_SECONDS,
    }
}

#[derive(Deserialize)]
struct SentMessage {
    #[serde(rename = "MessageId")]
    message_id: String,
}

#[derive(Deserialize)]
struct ContactPage {
    #[serde(rename = "Contacts", default)]
    contacts: Vec<Contact>,
    #[serde(rename = "NextToken")]
    next_token: Option<String>,
}

#[derive(Deserialize)]
struct Contact {
    #[serde(rename = "EmailAddress")]
    email_address: String,
}

#[derive(Deserialize)]
struct HeldContact {
    #[serde(rename = "TopicPreferences", default)]
    topic_preferences: Vec<HeldTopic>,
    #[serde(rename = "TopicDefaultPreferences", default)]
    topic_default_preferences: Vec<HeldTopic>,
    #[serde(rename = "UnsubscribeAll", default)]
    unsubscribe_all: bool,
}

#[derive(Deserialize)]
struct HeldTopic {
    #[serde(rename = "TopicName")]
    topic_name: String,
    #[serde(rename = "SubscriptionStatus")]
    subscription_status: String,
}

fn client(timeout_seconds: u64) -> Result<reqwest::Client, reqwest::Error> {
    reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(timeout_seconds))
        .build()
}

fn clipped(body: &str) -> String {
    body.chars().take(ERROR_BODY_CHARS).collect()
}

/// Clamped next to the call that reads it: a header naming an hour must not park a workspace's
/// invitations for one.
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

/// SigV4 signs the path it is given, so the encoded form is what both the signature and SES read.
fn path_segment(raw: &str) -> String {
    raw.bytes()
        .map(|byte| match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => {
                (byte as char).to_string()
            }
            _ => format!("%{byte:02X}"),
        })
        .collect()
}

/// The canonical URI SigV4 signs, which for every service but S3 is the request path encoded a
/// second time: AWS re-encodes what it received before it signs, so an address already carrying
/// `%40` has to reach the signature as `%2540` or SES answers 403 SignatureDoesNotMatch.
pub fn canonical_path(path: &str) -> String {
    path.split('/')
        .map(path_segment)
        .collect::<Vec<_>>()
        .join("/")
}

fn path_of(url: &str) -> String {
    let after_scheme = url.split_once("://").map(|(_, rest)| rest).unwrap_or(url);
    match after_scheme.find('/') {
        Some(start) => after_scheme[start..].to_string(),
        None => "/".to_string(),
    }
}

/// Read positionally rather than by XPath: the response carries fields this never needs, and the
/// three it does are the only ones under `Credentials`.
pub fn parse_assume_role_credentials(payload: &str) -> Result<SesCredentials, AwsError> {
    let mut reader = Reader::from_str(payload);
    let mut path: Vec<String> = Vec::new();
    let mut field: Option<String> = None;
    let mut found: BTreeMap<String, String> = BTreeMap::new();
    loop {
        match reader.read_event() {
            Ok(Event::Start(start)) => {
                let name = local_name(start.name().as_ref());
                path.push(name.clone());
                field = (path.len() >= 2 && path[path.len() - 2] == "Credentials").then_some(name);
            }
            Ok(Event::Text(text)) => {
                if let Some(name) = &field {
                    let value = text
                        .unescape()
                        .map_err(|error| AwsError::MalformedXml(error.to_string()))?;
                    found.insert(name.clone(), value.to_string());
                }
            }
            Ok(Event::End(_)) => {
                path.pop();
                field = None;
            }
            Ok(Event::Eof) => break,
            Err(error) => return Err(AwsError::MalformedXml(error.to_string())),
            _ => {}
        }
    }
    let mut take = |name: &'static str| {
        found
            .remove(name)
            .filter(|value| !value.is_empty())
            .ok_or(AwsError::MissingCredential(name))
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

/// Every header the signature covers is also sent, and the session token is among them — an omitted
/// `x-amz-security-token` signs a request AWS refuses.
pub fn sigv4_headers(
    call: &AwsCall<'_>,
    body: &[u8],
    region: &str,
    credentials: &SesCredentials,
    now: DateTime<Utc>,
) -> BTreeMap<String, String> {
    let amz_date = now.format("%Y%m%dT%H%M%SZ").to_string();
    let date_stamp = now.format("%Y%m%d").to_string();
    let payload_hash = format!("{:x}", Sha256::digest(body));
    let mut headers = BTreeMap::from([
        ("content-type".to_string(), call.content_type.to_string()),
        ("host".to_string(), host_of(call.url)),
        ("x-amz-content-sha256".to_string(), payload_hash.clone()),
        ("x-amz-date".to_string(), amz_date.clone()),
        (
            "x-amz-security-token".to_string(),
            credentials.session_token.clone(),
        ),
    ]);
    if let Some(target) = call.target {
        headers.insert("x-amz-target".to_string(), target.to_string());
    }
    let signed_headers = headers.keys().cloned().collect::<Vec<_>>().join(";");
    let canonical_headers = headers
        .iter()
        .map(|(key, value)| format!("{key}:{value}\n"))
        .collect::<String>();
    let canonical_request = format!(
        "{}\n{}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}",
        call.method,
        canonical_path(&path_of(call.url))
    );
    let scope = format!("{date_stamp}/{region}/{}/aws4_request", call.service);
    let string_to_sign = format!(
        "AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{:x}",
        Sha256::digest(canonical_request.as_bytes())
    );
    let signing_key = signing_key(&credentials.secret_key, &date_stamp, region, call.service);
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

fn signing_key(secret_key: &str, date_stamp: &str, region: &str, service: &str) -> Vec<u8> {
    let mut key = format!("AWS4{secret_key}").into_bytes();
    for message in [date_stamp, region, service, "aws4_request"] {
        let mut mac =
            <Hmac<Sha256>>::new_from_slice(&key).expect("hmac-sha256 accepts any key length");
        mac.update(message.as_bytes());
        key = mac.finalize().into_bytes().to_vec();
    }
    key
}

#[derive(Debug, Clone)]
pub enum EmailSender {
    Ses(Box<SesEmailSender>),
    Console,
}

impl EmailSender {
    /// The SES message id a delivery event is keyed by. Console mode mints one of its own, so a
    /// caller recording the send holds the same shape of row on a deploy with no SES identity.
    pub async fn send(
        &self,
        email: &str,
        subject: &str,
        text: &str,
        html: Option<&str>,
        topic: Option<&str>,
    ) -> Result<String, AwsError> {
        match self {
            Self::Ses(sender) => sender.send(email, subject, text, html, topic).await,
            Self::Console => {
                tracing::info!(
                    target: "ufo_control::email",
                    "email (console mode) → {email} | {subject} | {text}"
                );
                Ok(Uuid::new_v4().to_string())
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
    let region = ses_region();
    Ok(EmailSender::Ses(Box::new(SesEmailSender {
        source: require_env(SES_SENDER_ENV)?,
        configuration_set: require_env(SES_CONFIGURATION_SET_ENV)?,
        contact_list: require_env(SES_CONTACT_LIST_ENV)?,
        endpoints: AwsEndpoints::for_region(&region),
        region,
        role_arn: require_env(AWS_ROLE_ARN_ENV)?,
        token_file: PathBuf::from(require_env(AWS_WEB_IDENTITY_TOKEN_FILE_ENV)?),
    })))
}

/// The campaign sender, or `None` where this deploy sends no campaigns: a self-hosted install and a
/// local stack have no SES identity, no configuration set, and no feedback queue.
pub fn founder_sender_from_env() -> Result<Option<(FounderSender, String)>, EmailConfigError> {
    let Some(configured) = std::env::var(FOUNDER_SENDERS_ENV)
        .ok()
        .filter(|value| !value.is_empty())
    else {
        return Ok(None);
    };
    let region = ses_region();
    Ok(Some((
        FounderSender {
            senders: parse_senders(&configured)?,
            configuration_set: require_env(FOUNDER_CONFIGURATION_SET_ENV)?,
            contact_list: require_env(SES_CONTACT_LIST_ENV)?,
            topic: require_env(FOUNDER_TOPIC_ENV)?,
            endpoints: AwsEndpoints::for_region(&region),
            region,
            role_arn: require_env(AWS_ROLE_ARN_ENV)?,
            token_file: PathBuf::from(require_env(AWS_WEB_IDENTITY_TOKEN_FILE_ENV)?),
        },
        require_env(FOUNDER_FEEDBACK_QUEUE_ENV)?,
    )))
}

fn ses_region() -> String {
    std::env::var(SES_REGION_ENV).unwrap_or_else(|_| DEFAULT_SES_REGION.to_string())
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
