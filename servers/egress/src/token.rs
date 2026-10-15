use base64::alphabet;
use base64::engine::general_purpose::{GeneralPurpose, GeneralPurposeConfig, URL_SAFE_NO_PAD};
use base64::engine::DecodePaddingMode;
use base64::Engine;
use hmac::{Hmac, Mac};
use sha2::Sha256;
use uuid::Uuid;

use crate::types::{Principal, ProbeToken, RunActor, RunToken};

type HmacSha256 = Hmac<Sha256>;

const RUN_TOKEN_KIND: &str = "ufo-run";
const PROBE_TOKEN_KIND: &str = "ufo-probe";

const BASIC_AUTH_DECODER: GeneralPurpose = GeneralPurpose::new(
    &alphabet::STANDARD,
    GeneralPurposeConfig::new()
        .with_decode_padding_mode(DecodePaddingMode::RequireCanonical)
        .with_decode_allow_trailing_bits(true),
);

#[derive(Debug)]
pub enum TokenError {
    Malformed,
    BadSignature,
    BadPayload,
    WrongDomain,
}

fn sign_detached(secret: &[u8], message: &[u8]) -> String {
    let mut mac = HmacSha256::new_from_slice(secret).expect("HMAC accepts any key length");
    mac.update(message);
    URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes())
}

fn verify_detached(secret: &[u8], message: &[u8], signature: &str) -> bool {
    let Ok(tag) = URL_SAFE_NO_PAD.decode(signature) else {
        return false;
    };
    let mut mac = HmacSha256::new_from_slice(secret).expect("HMAC accepts any key length");
    mac.update(message);
    mac.verify_slice(&tag).is_ok()
}

pub fn sign_token(secret: &[u8], payload: &[u8]) -> String {
    let body = URL_SAFE_NO_PAD.encode(payload);
    let signature = sign_detached(secret, body.as_bytes());
    format!("{body}.{signature}")
}

pub fn verify_token(token: &str, secret: &[u8]) -> Result<Vec<u8>, TokenError> {
    let (body, signature) = match token.split_once('.') {
        Some((body, signature)) if !signature.is_empty() => (body, signature),
        _ => return Err(TokenError::Malformed),
    };
    if !verify_detached(secret, body.as_bytes(), signature) {
        return Err(TokenError::BadSignature);
    }
    URL_SAFE_NO_PAD
        .decode(body)
        .map_err(|_| TokenError::BadPayload)
}

fn basic_username(header: &str) -> Result<String, TokenError> {
    let (scheme, encoded) = header.split_once(' ').ok_or(TokenError::Malformed)?;
    if !scheme.eq_ignore_ascii_case("basic") || encoded.is_empty() {
        return Err(TokenError::Malformed);
    }
    let decoded = BASIC_AUTH_DECODER
        .decode(encoded)
        .map_err(|_| TokenError::Malformed)?;
    let text = String::from_utf8(decoded).map_err(|_| TokenError::Malformed)?;
    Ok(text
        .split_once(':')
        .map(|(username, _)| username)
        .unwrap_or(&text)
        .to_string())
}

fn member_field(member_id: Option<Uuid>) -> String {
    member_id
        .map(|member| member.to_string())
        .unwrap_or_else(|| "-".to_string())
}

/// The image a deploy replaces writes this where the member goes; it decodes as no member.
const OUTGOING_PROBE_MEMBER_FIELD: &str = "connections";

fn actor_field(actor: RunActor) -> String {
    match actor {
        RunActor::Turn => "-".to_string(),
        RunActor::Nobody => "~".to_string(),
        RunActor::Member(member) => member.to_string(),
    }
}

fn parse_actor(field: &str) -> Result<RunActor, TokenError> {
    match field {
        "-" => Ok(RunActor::Turn),
        "~" => Ok(RunActor::Nobody),
        _ => Uuid::parse_str(field)
            .map(RunActor::Member)
            .map_err(|_| TokenError::BadPayload),
    }
}

fn parse_member(field: &str) -> Result<Option<Uuid>, TokenError> {
    if field == "-" {
        return Ok(None);
    }
    Uuid::parse_str(field)
        .map(Some)
        .map_err(|_| TokenError::BadPayload)
}

pub struct RunTokenCodec {
    pub secret: Vec<u8>,
}

impl RunTokenCodec {
    pub fn encode(&self, run: &RunToken) -> String {
        let payload = format!(
            "{RUN_TOKEN_KIND}/{}/{}/{}",
            run.workspace_id,
            run.turn_id,
            actor_field(run.acts_for),
        );
        sign_token(&self.secret, payload.as_bytes())
    }

    pub fn from_proxy_auth(&self, header: &str) -> Result<RunToken, TokenError> {
        let username = basic_username(header)?;
        let payload = verify_token(&username, &self.secret)?;
        let text = String::from_utf8(payload).map_err(|_| TokenError::BadPayload)?;
        let parts: Vec<&str> = text.split('/').collect();
        let (kind, workspace, turn, actor) = match parts.as_slice() {
            [kind, workspace, turn, actor] => (*kind, *workspace, *turn, *actor),
            _ => return Err(TokenError::BadPayload),
        };
        if kind != RUN_TOKEN_KIND {
            return Err(TokenError::WrongDomain);
        }
        Ok(RunToken {
            workspace_id: Uuid::parse_str(workspace).map_err(|_| TokenError::BadPayload)?,
            turn_id: Uuid::parse_str(turn).map_err(|_| TokenError::BadPayload)?,
            acts_for: parse_actor(actor)?,
        })
    }
}

pub struct ProbeTokenCodec {
    pub secret: Vec<u8>,
}

impl ProbeTokenCodec {
    pub fn encode(&self, probe: &ProbeToken) -> String {
        let payload = format!(
            "{PROBE_TOKEN_KIND}/{}/{}/{}/{}/{}/-/{}",
            probe.workspace_id,
            probe.conversation_id,
            probe.probe_id,
            member_field(probe.member_id),
            probe.expires_at,
            if probe.internet_access { "-" } else { "0" },
        );
        sign_token(&self.secret, payload.as_bytes())
    }

    pub fn from_proxy_auth(&self, header: &str) -> Result<ProbeToken, TokenError> {
        let username = basic_username(header)?;
        let payload = verify_token(&username, &self.secret)?;
        let text = String::from_utf8(payload).map_err(|_| TokenError::BadPayload)?;
        let parts: Vec<&str> = text.split('/').collect();
        let [kind, workspace, conversation, probe, member, expires, _connections, internet] =
            parts.as_slice()
        else {
            return Err(TokenError::BadPayload);
        };
        if !matches!(*internet, "-" | "0") {
            return Err(TokenError::BadPayload);
        }
        if *kind != PROBE_TOKEN_KIND {
            return Err(TokenError::WrongDomain);
        }
        Ok(ProbeToken {
            workspace_id: Uuid::parse_str(workspace).map_err(|_| TokenError::BadPayload)?,
            conversation_id: Uuid::parse_str(conversation).map_err(|_| TokenError::BadPayload)?,
            probe_id: Uuid::parse_str(probe).map_err(|_| TokenError::BadPayload)?,
            expires_at: expires.parse().map_err(|_| TokenError::BadPayload)?,
            member_id: if *member == OUTGOING_PROBE_MEMBER_FIELD {
                None
            } else {
                parse_member(member)?
            },
            internet_access: *internet == "-",
        })
    }
}

pub fn principal_from_proxy_auth(secret: &[u8], header: &str) -> Option<Principal> {
    if let Ok(run) = (RunTokenCodec {
        secret: secret.to_vec(),
    })
    .from_proxy_auth(header)
    {
        return Some(Principal::Run(run));
    }
    if let Ok(probe) = (ProbeTokenCodec {
        secret: secret.to_vec(),
    })
    .from_proxy_auth(header)
    {
        return Some(Principal::Probe(probe));
    }
    None
}

#[cfg(test)]
mod tests {
    use base64::engine::general_purpose::STANDARD;

    use super::*;

    const SECRET: &[u8] = b"proxy-test-run-token-secret";
    const WORKSPACE: &str = "11111111-1111-1111-1111-111111111111";
    const TURN: &str = "22222222-2222-2222-2222-222222222222";
    const CONVERSATION: &str = "33333333-3333-3333-3333-333333333333";
    const PROBE: &str = "44444444-4444-4444-4444-444444444444";
    const MEMBER: &str = "55555555-5555-5555-5555-555555555555";
    const EXPIRES_AT: i64 = 1893456000;

    const HMAC_PAYLOAD: &str = "ufo-run/hello/world";
    const HMAC_TOKEN: &str =
        "dWZvLXJ1bi9oZWxsby93b3JsZA.mjrJCK4I_3zmPNicFs_d3Aa2ExDtjCwWDzA0m-24AIg";

    const RUN_ENCODED: &str = "dWZvLXJ1bi8xMTExMTExMS0xMTExLTExMTEtMTExMS0xMTExMTExMTExMTEvMjIyMjIyMjItMjIyMi0yMjIyLTIyMjItMjIyMjIyMjIyMjIyLzU1NTU1NTU1LTU1NTUtNTU1NS01NTU1LTU1NTU1NTU1NTU1NQ.pJ2JfiGJUhBglEDEuFnp_kOGjtWDq5Flrn5YLZWmKlA";
    const RUN_ENCODED_EMPTY: &str = "dWZvLXJ1bi8xMTExMTExMS0xMTExLTExMTEtMTExMS0xMTExMTExMTExMTEvMjIyMjIyMjItMjIyMi0yMjIyLTIyMjItMjIyMjIyMjIyMjIyLy0.JHEdgNA7dR1LyTRQUsdhGom8Kjgpf0EW8382drNMsgY";
    const RUN_ENCODED_NOBODY: &str = "dWZvLXJ1bi8xMTExMTExMS0xMTExLTExMTEtMTExMS0xMTExMTExMTExMTEvMjIyMjIyMjItMjIyMi0yMjIyLTIyMjItMjIyMjIyMjIyMjIyL34.HX71N4qcWsBTyzQNMHB0wHNQl5xUxlZAvDPB_TuORTM";

    const PROBE_ENCODED: &str = "dWZvLXByb2JlLzExMTExMTExLTExMTEtMTExMS0xMTExLTExMTExMTExMTExMS8zMzMzMzMzMy0zMzMzLTMzMzMtMzMzMy0zMzMzMzMzMzMzMzMvNDQ0NDQ0NDQtNDQ0NC00NDQ0LTQ0NDQtNDQ0NDQ0NDQ0NDQ0LzU1NTU1NTU1LTU1NTUtNTU1NS01NTU1LTU1NTU1NTU1NTU1NS8xODkzNDU2MDAwLy0vLQ.29BTU20UhOIY2_fCic_WChh1LdNbiFnwIID2792lMpQ";
    const PROBE_ENCODED_EMPTY: &str = "dWZvLXByb2JlLzExMTExMTExLTExMTEtMTExMS0xMTExLTExMTExMTExMTExMS8zMzMzMzMzMy0zMzMzLTMzMzMtMzMzMy0zMzMzMzMzMzMzMzMvNDQ0NDQ0NDQtNDQ0NC00NDQ0LTQ0NDQtNDQ0NDQ0NDQ0NDQ0Ly0vMTg5MzQ1NjAwMC8tLzA.hEI8STHlTxbP6PVBA4WbhHMG6Bfd9zKbCbwRg2VLyqU";
    const PROBE_ENCODED_OUTGOING: &str = "dWZvLXByb2JlLzExMTExMTExLTExMTEtMTExMS0xMTExLTExMTExMTExMTExMS8zMzMzMzMzMy0zMzMzLTMzMzMtMzMzMy0zMzMzMzMzMzMzMzMvNDQ0NDQ0NDQtNDQ0NC00NDQ0LTQ0NDQtNDQ0NDQ0NDQ0NDQ0L2Nvbm5lY3Rpb25zLzE4OTM0NTYwMDAvNjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjYsNzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3NzcvLQ.cCT0aHCGBlMA0td7nGhWYcoZsp_IivuOYKWpQ0SLw_E";

    fn u(text: &str) -> Uuid {
        Uuid::parse_str(text).unwrap()
    }

    fn basic_header(token: &str) -> String {
        format!("Basic {}", STANDARD.encode(format!("{token}:")))
    }

    #[test]
    fn sign_token_matches_golden() {
        assert_eq!(sign_token(SECRET, HMAC_PAYLOAD.as_bytes()), HMAC_TOKEN);
    }

    #[test]
    fn verify_token_round_trips() {
        let recovered = verify_token(HMAC_TOKEN, SECRET).unwrap();
        assert_eq!(recovered, HMAC_PAYLOAD.as_bytes());
    }

    #[test]
    fn verify_token_rejects_bad_signature() {
        let tampered = format!("{HMAC_TOKEN}x");
        assert!(matches!(
            verify_token(&tampered, SECRET),
            Err(TokenError::BadSignature)
        ));
    }

    #[test]
    fn verify_token_rejects_wrong_secret() {
        assert!(matches!(
            verify_token(HMAC_TOKEN, b"other-secret"),
            Err(TokenError::BadSignature)
        ));
    }

    #[test]
    fn verify_token_rejects_malformed() {
        assert!(matches!(
            verify_token("no-dot-here", SECRET),
            Err(TokenError::Malformed)
        ));
        assert!(matches!(
            verify_token("body-only.", SECRET),
            Err(TokenError::Malformed)
        ));
    }

    #[test]
    fn run_encode_matches_golden() {
        let scoped = RunToken {
            workspace_id: u(WORKSPACE),
            turn_id: u(TURN),
            acts_for: RunActor::Member(u(MEMBER)),
        };
        let empty = RunToken {
            workspace_id: u(WORKSPACE),
            turn_id: u(TURN),
            acts_for: RunActor::Turn,
        };
        let codec = RunTokenCodec {
            secret: SECRET.to_vec(),
        };
        assert_eq!(codec.encode(&scoped), RUN_ENCODED);
        assert_eq!(codec.encode(&empty), RUN_ENCODED_EMPTY);
    }

    #[test]
    fn run_from_proxy_auth_round_trips() {
        let codec = RunTokenCodec {
            secret: SECRET.to_vec(),
        };
        let expected = RunToken {
            workspace_id: u(WORKSPACE),
            turn_id: u(TURN),
            acts_for: RunActor::Member(u(MEMBER)),
        };
        let recovered = codec.from_proxy_auth(&basic_header(RUN_ENCODED)).unwrap();
        assert_eq!(recovered, expected);

        let expected_empty = RunToken {
            workspace_id: u(WORKSPACE),
            turn_id: u(TURN),
            acts_for: RunActor::Turn,
        };
        let recovered_empty = codec
            .from_proxy_auth(&basic_header(RUN_ENCODED_EMPTY))
            .unwrap();
        assert_eq!(recovered_empty, expected_empty);
    }

    #[test]
    fn run_token_names_nobody_with_a_tilde() {
        let codec = RunTokenCodec {
            secret: SECRET.to_vec(),
        };
        let nobody = RunToken {
            workspace_id: u(WORKSPACE),
            turn_id: u(TURN),
            acts_for: RunActor::Nobody,
        };
        assert_eq!(codec.encode(&nobody), RUN_ENCODED_NOBODY);
        assert_eq!(
            codec
                .from_proxy_auth(&basic_header(RUN_ENCODED_NOBODY))
                .unwrap(),
            nobody
        );
        assert!(parse_actor("nobody").is_err());
    }

    #[test]
    fn probe_encode_matches_golden() {
        let scoped = ProbeToken {
            workspace_id: u(WORKSPACE),
            conversation_id: u(CONVERSATION),
            probe_id: u(PROBE),
            expires_at: EXPIRES_AT,
            member_id: Some(u(MEMBER)),
            internet_access: true,
        };
        let empty = ProbeToken {
            workspace_id: u(WORKSPACE),
            conversation_id: u(CONVERSATION),
            probe_id: u(PROBE),
            expires_at: EXPIRES_AT,
            member_id: None,
            internet_access: false,
        };
        let codec = ProbeTokenCodec {
            secret: SECRET.to_vec(),
        };
        assert_eq!(codec.encode(&scoped), PROBE_ENCODED);
        assert_eq!(codec.encode(&empty), PROBE_ENCODED_EMPTY);
    }

    #[test]
    fn probe_from_proxy_auth_round_trips() {
        let codec = ProbeTokenCodec {
            secret: SECRET.to_vec(),
        };
        let expected = ProbeToken {
            workspace_id: u(WORKSPACE),
            conversation_id: u(CONVERSATION),
            probe_id: u(PROBE),
            expires_at: EXPIRES_AT,
            member_id: Some(u(MEMBER)),
            internet_access: true,
        };
        let recovered = codec.from_proxy_auth(&basic_header(PROBE_ENCODED)).unwrap();
        assert_eq!(recovered, expected);

        let expected_empty = ProbeToken {
            member_id: None,
            internet_access: false,
            ..expected.clone()
        };
        let recovered_empty = codec
            .from_proxy_auth(&basic_header(PROBE_ENCODED_EMPTY))
            .unwrap();
        assert_eq!(recovered_empty, expected_empty);
    }

    #[test]
    fn probe_reads_the_outgoing_images_connection_list_as_no_member() {
        let codec = ProbeTokenCodec {
            secret: SECRET.to_vec(),
        };
        let recovered = codec
            .from_proxy_auth(&basic_header(PROBE_ENCODED_OUTGOING))
            .unwrap();
        assert_eq!(
            recovered,
            ProbeToken {
                workspace_id: u(WORKSPACE),
                conversation_id: u(CONVERSATION),
                probe_id: u(PROBE),
                expires_at: EXPIRES_AT,
                member_id: None,
                internet_access: true,
            }
        );
    }

    #[test]
    fn a_member_field_is_a_uuid_or_a_dash() {
        assert_eq!(parse_member("-").unwrap(), None);
        assert_eq!(parse_member(MEMBER).unwrap(), Some(u(MEMBER)));
        assert!(parse_member("nobody").is_err());
    }

    #[test]
    fn codecs_refuse_the_other_domain() {
        let run = RunTokenCodec {
            secret: SECRET.to_vec(),
        };
        let probe = ProbeTokenCodec {
            secret: SECRET.to_vec(),
        };
        assert!(run.from_proxy_auth(&basic_header(PROBE_ENCODED)).is_err());
        assert!(probe.from_proxy_auth(&basic_header(RUN_ENCODED)).is_err());
    }

    #[test]
    fn principal_resolves_run_and_probe() {
        match principal_from_proxy_auth(SECRET, &basic_header(RUN_ENCODED)) {
            Some(Principal::Run(token)) => {
                assert_eq!(token.workspace_id, u(WORKSPACE));
                assert_eq!(token.turn_id, u(TURN));
                assert_eq!(token.acts_for, RunActor::Member(u(MEMBER)));
            }
            other => panic!("expected run principal, got {other:?}"),
        }
        match principal_from_proxy_auth(SECRET, &basic_header(PROBE_ENCODED)) {
            Some(Principal::Probe(token)) => {
                assert_eq!(token.conversation_id, u(CONVERSATION));
                assert_eq!(token.probe_id, u(PROBE));
                assert_eq!(token.expires_at, EXPIRES_AT);
                assert_eq!(token.member_id, Some(u(MEMBER)));
                assert!(token.internet_access);
            }
            other => panic!("expected probe principal, got {other:?}"),
        }
    }

    #[test]
    fn principal_rejects_garbage_and_empty() {
        assert!(principal_from_proxy_auth(SECRET, "").is_none());
        assert!(principal_from_proxy_auth(SECRET, "Basic not-base64!!").is_none());
        assert!(principal_from_proxy_auth(SECRET, &basic_header("forged.token")).is_none());
        assert!(principal_from_proxy_auth(b"wrong-secret", &basic_header(RUN_ENCODED)).is_none());
    }

    #[test]
    fn basic_username_extracts_userinfo() {
        assert_eq!(
            basic_username(&basic_header(RUN_ENCODED)).unwrap(),
            RUN_ENCODED
        );
    }

    fn noncanonical_basic_blob(token: &str) -> String {
        const ALPHA: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        let mut b64 = STANDARD.encode(format!("{token}:")).into_bytes();
        let pad = b64.iter().rev().take_while(|&&c| c == b'=').count();
        let idx = b64.len() - pad - 1;
        let value = ALPHA.iter().position(|&c| c == b64[idx]).unwrap();
        b64[idx] = ALPHA[value ^ 1];
        String::from_utf8(b64).unwrap()
    }

    #[test]
    fn basic_username_ignores_noncanonical_trailing_bits() {
        let header = format!("Basic {}", noncanonical_basic_blob(RUN_ENCODED));
        assert_eq!(basic_username(&header).unwrap(), RUN_ENCODED);

        let codec = RunTokenCodec {
            secret: SECRET.to_vec(),
        };
        assert_eq!(
            codec.from_proxy_auth(&header).unwrap().acts_for,
            RunActor::Member(u(MEMBER))
        );
        assert!(matches!(
            principal_from_proxy_auth(SECRET, &header),
            Some(Principal::Run(_))
        ));
    }
}
