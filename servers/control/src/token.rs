//! The member bearer the gateway mints for a hosted member (the client stores it in
//! `~/.ufo/credentials`, surfaces verify it).
//!
//! One self-contained HMAC claim, no server-side state. The codec is core's — `core/src/ufo/
//! bearer.py` spells it out and owns the verify half every product surface reads through — so this
//! half signs, and reads a claim back only for the sign-in door, which stands in front of those
//! surfaces and answers before any of them sees the request. `tests/contract.rs` holds both
//! directions to core's own vectors:
//!
//! ```text
//! payload_json = {"email": "<lower email>", "exp": <unix seconds>, "ws": "<workspace uuid>"}
//! body         = base64url(payload_json)            # padding stripped
//! token        = body + "." + hex(hmac_sha256(secret, body))
//! ```

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use chrono::{DateTime, Duration, Utc};
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;

use crate::workos::constant_time_eq;

pub const TOKEN_SECRET_ENV: &str = "UFO_TOKEN_SECRET";
pub const TOKEN_TTL_DAYS: i64 = 30;
const TOKEN_SEPARATOR: char = '.';

/// The cookie a bearer rides in a browser, core's `SESSION_COOKIE` under the same name it binds.
pub const SESSION_COOKIE: &str = "ufo_session";

/// The claim's fields in the order core's `json.dumps(..., sort_keys=True)` writes them. Serde
/// serializes a struct in declaration order, so alphabetical here is byte-identical there — and the
/// bytes are what the signature covers, so a reordering would verify nowhere.
#[derive(Serialize)]
struct Claim<'a> {
    email: &'a str,
    exp: i64,
    ws: &'a str,
}

/// Sign a bearer claiming `workspace_id` for `email`, expiring `TOKEN_TTL_DAYS` from `now`.
pub fn mint_token(
    secret: &str,
    workspace_id: &str,
    email: &str,
    now: DateTime<Utc>,
) -> Result<String, TokenError> {
    sign(
        secret,
        workspace_id,
        email,
        now + Duration::days(TOKEN_TTL_DAYS),
    )
}

/// The codec itself, taking the expiry outright so a test can pin one.
pub fn sign(
    secret: &str,
    workspace_id: &str,
    email: &str,
    expires_at: DateTime<Utc>,
) -> Result<String, TokenError> {
    if secret.is_empty() {
        return Err(TokenError::MissingSecret);
    }
    let lowered = email.trim().to_lowercase();
    let claim = Claim {
        email: &lowered,
        exp: expires_at.timestamp(),
        ws: workspace_id,
    };
    let json = serde_json::to_string(&claim).map_err(TokenError::Encode)?;
    let body = URL_SAFE_NO_PAD.encode(escape_non_ascii(&json));
    let mut mac = <Hmac<Sha256>>::new_from_slice(secret.as_bytes())
        .expect("hmac-sha256 accepts a key of any length");
    mac.update(body.as_bytes());
    let signature = mac.finalize().into_bytes();
    Ok(format!("{body}{TOKEN_SEPARATOR}{signature:x}"))
}

/// The lowercased member email a presented bearer proves, or None when it proves nothing: signature
/// under this deploy's secret and expiry are both checked before either field is read, so a forged,
/// tampered, or expired token yields no identity. A deploy holding no secret verifies nothing rather
/// than accepting a token signed with the empty key.
pub fn verified_email(secret: &str, token: &str, now: DateTime<Utc>) -> Option<String> {
    if secret.is_empty() {
        return None;
    }
    let (body, signature) = token.split_once(TOKEN_SEPARATOR)?;
    let mut mac = <Hmac<Sha256>>::new_from_slice(secret.as_bytes())
        .expect("hmac-sha256 accepts a key of any length");
    mac.update(body.as_bytes());
    if !constant_time_eq(signature, &format!("{:x}", mac.finalize().into_bytes())) {
        return None;
    }
    let decoded = URL_SAFE_NO_PAD.decode(body).ok()?;
    let presented: PresentedClaim = serde_json::from_slice(&decoded).ok()?;
    if presented.exp <= now.timestamp() {
        return None;
    }
    Some(presented.email.to_lowercase())
}

/// The claim read back off the wire. Only the fields the sign-in door acts on are named, and every
/// one of them is read after the signature over the whole body verifies.
#[derive(Deserialize)]
struct PresentedClaim {
    email: String,
    exp: i64,
}

/// Re-escape every non-ASCII character as `\uXXXX`, the way `json.dumps` does under its default
/// `ensure_ascii=True`. Core writes the signed body with that default and `serde_json` writes UTF-8
/// straight through, so without this an address carrying one accented letter signs a different body
/// here than the codec core verifies against — a token no surface would accept, for the members
/// least able to guess why. In valid JSON every non-ASCII byte sits inside a string literal, and
/// `serde_json` has already escaped the quotes, backslashes, and control characters, so rewriting
/// the rest one character at a time cannot disturb the structure. A character above the basic plane
/// becomes the surrogate pair Python emits for it.
fn escape_non_ascii(json: &str) -> String {
    if json.is_ascii() {
        return json.to_string();
    }
    let mut escaped = String::with_capacity(json.len());
    for character in json.chars() {
        match character as u32 {
            code if code < 0x80 => escaped.push(character),
            code if code <= 0xFFFF => escaped.push_str(&format!("\\u{code:04x}")),
            code => {
                let astral = code - 0x1_0000;
                escaped.push_str(&format!("\\u{:04x}", 0xD800 + (astral >> 10)));
                escaped.push_str(&format!("\\u{:04x}", 0xDC00 + (astral & 0x3FF)));
            }
        }
    }
    escaped
}

#[derive(Debug, thiserror::Error)]
pub enum TokenError {
    #[error("token secret is required")]
    MissingSecret,
    #[error("claim is not serializable: {0}")]
    Encode(serde_json::Error),
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    const SECRET: &str = "local-dev-token-secret";
    const WORKSPACE: &str = "11111111-1111-1111-1111-111111111111";

    fn at(seconds: i64) -> DateTime<Utc> {
        Utc.timestamp_opt(seconds, 0).unwrap()
    }

    #[test]
    fn a_token_is_a_body_and_a_hex_signature() {
        let token = sign(SECRET, WORKSPACE, "dana@acme.com", at(1_800_000_000)).unwrap();
        let (body, signature) = token.split_once('.').expect("one separator");
        assert_eq!(signature.len(), 64);
        assert!(signature.chars().all(|c| c.is_ascii_hexdigit()));
        assert!(!body.contains('='), "padding is stripped");
    }

    #[test]
    fn the_claim_decodes_to_core_field_order() {
        let token = sign(SECRET, WORKSPACE, "dana@acme.com", at(1_800_000_000)).unwrap();
        let body = token.split_once('.').unwrap().0;
        let json = String::from_utf8(URL_SAFE_NO_PAD.decode(body).unwrap()).unwrap();
        assert_eq!(
            json,
            r#"{"email":"dana@acme.com","exp":1800000000,"ws":"11111111-1111-1111-1111-111111111111"}"#
        );
    }

    #[test]
    fn an_address_is_trimmed_and_lowercased_before_signing() {
        let messy = sign(SECRET, WORKSPACE, "  Dana@Acme.COM ", at(1_800_000_000)).unwrap();
        let clean = sign(SECRET, WORKSPACE, "dana@acme.com", at(1_800_000_000)).unwrap();
        assert_eq!(messy, clean);
    }

    #[test]
    fn a_different_secret_signs_differently() {
        let mine = sign(SECRET, WORKSPACE, "dana@acme.com", at(1_800_000_000)).unwrap();
        let theirs = sign("other", WORKSPACE, "dana@acme.com", at(1_800_000_000)).unwrap();
        assert_ne!(mine, theirs);
        assert_eq!(
            mine.split_once('.').unwrap().0,
            theirs.split_once('.').unwrap().0,
            "same claim, so only the signature moves"
        );
    }

    #[test]
    fn an_empty_secret_refuses() {
        assert!(matches!(
            sign("", WORKSPACE, "dana@acme.com", at(1_800_000_000)),
            Err(TokenError::MissingSecret)
        ));
    }

    #[test]
    fn a_non_ascii_address_signs_the_escaped_body_core_writes() {
        // `json.dumps` escapes non-ASCII by default, so the signed body core verifies against
        // carries `é` rather than the UTF-8 byte a straight-through writer would emit.
        let token = sign(SECRET, WORKSPACE, "josé@exämple.com", at(1_735_689_600)).unwrap();
        let body = token.split_once('.').unwrap().0;
        let json = String::from_utf8(URL_SAFE_NO_PAD.decode(body).unwrap()).unwrap();
        assert!(json.is_ascii(), "{json}");
        assert!(
            json.contains(r#""email":"jos\u00e9@ex\u00e4mple.com""#),
            "{json}"
        );
    }

    #[test]
    fn an_astral_character_becomes_the_surrogate_pair_python_emits() {
        let token = sign(SECRET, WORKSPACE, "a\u{1F600}@b.com", at(0)).unwrap();
        let body = token.split_once('.').unwrap().0;
        let json = String::from_utf8(URL_SAFE_NO_PAD.decode(body).unwrap()).unwrap();
        assert!(json.contains(r#""email":"a\ud83d\ude00@b.com""#), "{json}");
    }

    #[test]
    fn minting_expires_thirty_days_out() {
        let now = at(1_800_000_000);
        let token = mint_token(SECRET, WORKSPACE, "dana@acme.com", now).unwrap();
        let body = token.split_once('.').unwrap().0;
        let json = String::from_utf8(URL_SAFE_NO_PAD.decode(body).unwrap()).unwrap();
        let expected = 1_800_000_000 + TOKEN_TTL_DAYS * 24 * 60 * 60;
        assert!(json.contains(&format!(r#""exp":{expected}"#)), "{json}");
    }

    #[test]
    fn a_live_token_verifies_to_the_address_it_claims() {
        let token = mint_token(SECRET, WORKSPACE, "  Dana@Acme.COM ", at(1_800_000_000)).unwrap();
        assert_eq!(
            verified_email(SECRET, &token, at(1_800_000_000)).as_deref(),
            Some("dana@acme.com")
        );
    }

    #[test]
    fn an_expired_token_verifies_to_nothing() {
        let token = sign(SECRET, WORKSPACE, "dana@acme.com", at(1_800_000_000)).unwrap();
        assert!(verified_email(SECRET, &token, at(1_799_999_999)).is_some());
        assert_eq!(verified_email(SECRET, &token, at(1_800_000_000)), None);
        assert_eq!(verified_email(SECRET, &token, at(1_800_000_001)), None);
    }

    #[test]
    fn a_token_signed_elsewhere_or_tampered_with_verifies_to_nothing() {
        let token = sign(SECRET, WORKSPACE, "dana@acme.com", at(1_800_000_000)).unwrap();
        let (body, signature) = token.split_once('.').unwrap();
        let forged = sign(
            "another-secret",
            WORKSPACE,
            "dana@acme.com",
            at(1_800_000_000),
        )
        .unwrap();
        for candidate in [
            forged.as_str(),
            &format!("{body}x.{signature}"),
            &format!("{body}.{}", "0".repeat(64)),
            body,
            "",
        ] {
            assert_eq!(
                verified_email(SECRET, candidate, at(1_799_999_999)),
                None,
                "{candidate}"
            );
        }
    }

    #[test]
    fn a_deploy_holding_no_secret_verifies_nothing() {
        let token = sign(SECRET, WORKSPACE, "dana@acme.com", at(1_800_000_000)).unwrap();
        assert_eq!(verified_email("", &token, at(1_799_999_999)), None);
    }

    #[test]
    fn a_non_ascii_address_verifies_back_to_the_address_core_signed() {
        let token = sign(SECRET, WORKSPACE, "josé@exämple.com", at(1_800_000_000)).unwrap();
        assert_eq!(
            verified_email(SECRET, &token, at(1_799_999_999)).as_deref(),
            Some("josé@exämple.com")
        );
    }
}
