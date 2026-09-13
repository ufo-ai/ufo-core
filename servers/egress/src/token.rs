use base64::alphabet;
use base64::engine::general_purpose::{GeneralPurpose, GeneralPurposeConfig, URL_SAFE_NO_PAD};
use base64::engine::DecodePaddingMode;
use base64::Engine;
use hmac::{Hmac, Mac};
use sha2::Sha256;
use uuid::Uuid;

use crate::types::{Principal, ProbeToken, RunToken};

type HmacSha256 = Hmac<Sha256>;

const RUN_TOKEN_KIND: &str = "ufo-run";
const PROBE_TOKEN_KIND: &str = "ufo-probe";
const CONNECTION_CAPABILITY: &str = "connections";
const CONNECTION_SCOPE_MAX: usize = 50;

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

fn connection_field(connections: &[Uuid]) -> String {
    if connections.is_empty() {
        return "-".to_string();
    }
    connections
        .iter()
        .map(|connection| connection.simple().to_string())
        .collect::<Vec<_>>()
        .join(",")
}

fn parse_connections(field: &str) -> Result<Vec<Uuid>, TokenError> {
    if field == "-" {
        return Ok(Vec::new());
    }
    let mut connections = field
        .split(',')
        .map(|connection| Uuid::parse_str(connection).map_err(|_| TokenError::BadPayload))
        .collect::<Result<Vec<_>, _>>()?;
    if connections.len() > CONNECTION_SCOPE_MAX {
        return Err(TokenError::BadPayload);
    }
    connections.sort_unstable();
    if connections.windows(2).any(|pair| pair[0] == pair[1]) {
        return Err(TokenError::BadPayload);
    }
    Ok(connections)
}

fn validate_discarded_member(field: &str) -> Result<(), TokenError> {
    if field != "-" {
        Uuid::parse_str(field).map_err(|_| TokenError::BadPayload)?;
    }
    Ok(())
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
            run.capability_id
                .map(|capability| capability.to_string())
                .unwrap_or_else(|| "-".to_string()),
        );
        sign_token(&self.secret, payload.as_bytes())
    }

    pub fn from_proxy_auth(&self, header: &str) -> Result<RunToken, TokenError> {
        let username = basic_username(header)?;
        let payload = verify_token(&username, &self.secret)?;
        let text = String::from_utf8(payload).map_err(|_| TokenError::BadPayload)?;
        let parts: Vec<&str> = text.split('/').collect();
        let (kind, workspace, turn, nonce) = match parts.as_slice() {
            [kind, workspace, turn, nonce] => (*kind, *workspace, *turn, *nonce),
            _ => return Err(TokenError::BadPayload),
        };
        if kind != RUN_TOKEN_KIND {
            return Err(TokenError::WrongDomain);
        }
        Ok(RunToken {
            workspace_id: Uuid::parse_str(workspace).map_err(|_| TokenError::BadPayload)?,
            turn_id: Uuid::parse_str(turn).map_err(|_| TokenError::BadPayload)?,
            capability_id: if nonce == "-" {
                None
            } else {
                Some(Uuid::parse_str(nonce).map_err(|_| TokenError::BadPayload)?)
            },
        })
    }
}

pub struct ProbeTokenCodec {
    pub secret: Vec<u8>,
}

impl ProbeTokenCodec {
    pub fn encode(&self, probe: &ProbeToken) -> String {
        let payload = format!(
            "{PROBE_TOKEN_KIND}/{}/{}/{}/{CONNECTION_CAPABILITY}/{}/{}/{}",
            probe.workspace_id,
            probe.conversation_id,
            probe.probe_id,
            probe.expires_at,
            connection_field(&probe.connections),
            if probe.internet_access { "-" } else { "0" },
        );
        sign_token(&self.secret, payload.as_bytes())
    }

    pub fn from_proxy_auth(&self, header: &str) -> Result<ProbeToken, TokenError> {
        let username = basic_username(header)?;
        let payload = verify_token(&username, &self.secret)?;
        let text = String::from_utf8(payload).map_err(|_| TokenError::BadPayload)?;
        let parts: Vec<&str> = text.split('/').collect();
        let (kind, workspace, conversation, probe, expires, connections, internet) = match parts
            .as_slice()
        {
            [kind, workspace, conversation, probe, discarded_member, expires] => {
                validate_discarded_member(discarded_member)?;
                (*kind, *workspace, *conversation, *probe, *expires, "-", "0")
            }
            [kind, workspace, conversation, probe, capability, expires, connections, internet] => {
                if *capability != CONNECTION_CAPABILITY {
                    validate_discarded_member(capability)?;
                }
                (
                    *kind,
                    *workspace,
                    *conversation,
                    *probe,
                    *expires,
                    *connections,
                    *internet,
                )
            }
            _ => return Err(TokenError::BadPayload),
        };
        if !matches!(internet, "-" | "0") {
            return Err(TokenError::BadPayload);
        };
        if kind != PROBE_TOKEN_KIND {
            return Err(TokenError::WrongDomain);
        }
        Ok(ProbeToken {
            workspace_id: Uuid::parse_str(workspace).map_err(|_| TokenError::BadPayload)?,
            conversation_id: Uuid::parse_str(conversation).map_err(|_| TokenError::BadPayload)?,
            probe_id: Uuid::parse_str(probe).map_err(|_| TokenError::BadPayload)?,
            expires_at: expires.parse().map_err(|_| TokenError::BadPayload)?,
            connections: parse_connections(connections)?,
            internet_access: internet == "-",
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

    const PROBE_ENCODED: &str = "dWZvLXByb2JlLzExMTExMTExLTExMTEtMTExMS0xMTExLTExMTExMTExMTExMS8zMzMzMzMzMy0zMzMzLTMzMzMtMzMzMy0zMzMzMzMzMzMzMzMvNDQ0NDQ0NDQtNDQ0NC00NDQ0LTQ0NDQtNDQ0NDQ0NDQ0NDQ0L2Nvbm5lY3Rpb25zLzE4OTM0NTYwMDAvNTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTUvLQ.FjWCzN62EDs8F8NeWkbb_tgw84192FdAgrV4RXjRngo";
    const PROBE_ENCODED_EMPTY: &str = "dWZvLXByb2JlLzExMTExMTExLTExMTEtMTExMS0xMTExLTExMTExMTExMTExMS8zMzMzMzMzMy0zMzMzLTMzMzMtMzMzMy0zMzMzMzMzMzMzMzMvNDQ0NDQ0NDQtNDQ0NC00NDQ0LTQ0NDQtNDQ0NDQ0NDQ0NDQ0L2Nvbm5lY3Rpb25zLzE4OTM0NTYwMDAvLS8w.96qFcq_VXDTo0YlRa95AyE6-H0-puijm3xrCWnPovcI";
    const OLD_PROBE_MEMBER_ENCODED: &str = "dWZvLXByb2JlLzExMTExMTExLTExMTEtMTExMS0xMTExLTExMTExMTExMTExMS8zMzMzMzMzMy0zMzMzLTMzMzMtMzMzMy0zMzMzMzMzMzMzMzMvNDQ0NDQ0NDQtNDQ0NC00NDQ0LTQ0NDQtNDQ0NDQ0NDQ0NDQ0LzU1NTU1NTU1LTU1NTUtNTU1NS01NTU1LTU1NTU1NTU1NTU1NS8xODkzNDU2MDAw.eUycmsfqQHnHNr84eX8p7JcJgbovmFYBTCcI43jYF8Q";

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
            capability_id: Some(u(MEMBER)),
        };
        let empty = RunToken {
            workspace_id: u(WORKSPACE),
            turn_id: u(TURN),
            capability_id: None,
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
            capability_id: Some(u(MEMBER)),
        };
        let recovered = codec.from_proxy_auth(&basic_header(RUN_ENCODED)).unwrap();
        assert_eq!(recovered, expected);

        let expected_empty = RunToken {
            workspace_id: u(WORKSPACE),
            turn_id: u(TURN),
            capability_id: None,
        };
        let recovered_empty = codec
            .from_proxy_auth(&basic_header(RUN_ENCODED_EMPTY))
            .unwrap();
        assert_eq!(recovered_empty, expected_empty);
    }

    #[test]
    fn probe_encode_matches_golden() {
        let scoped = ProbeToken {
            workspace_id: u(WORKSPACE),
            conversation_id: u(CONVERSATION),
            probe_id: u(PROBE),
            expires_at: EXPIRES_AT,
            connections: vec![u(MEMBER)],
            internet_access: true,
        };
        let empty = ProbeToken {
            workspace_id: u(WORKSPACE),
            conversation_id: u(CONVERSATION),
            probe_id: u(PROBE),
            expires_at: EXPIRES_AT,
            connections: vec![],
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
            connections: vec![u(MEMBER)],
            internet_access: true,
        };
        let recovered = codec.from_proxy_auth(&basic_header(PROBE_ENCODED)).unwrap();
        assert_eq!(recovered, expected);

        let expected_empty = ProbeToken {
            connections: vec![],
            internet_access: false,
            ..expected.clone()
        };
        let recovered_empty = codec
            .from_proxy_auth(&basic_header(PROBE_ENCODED_EMPTY))
            .unwrap();
        assert_eq!(recovered_empty, expected_empty);
        let recovered_old = codec
            .from_proxy_auth(&basic_header(OLD_PROBE_MEMBER_ENCODED))
            .unwrap();
        assert_eq!(recovered_old, expected_empty);
    }

    #[test]
    fn connection_capabilities_are_bounded_and_distinct() {
        let repeated = format!("{MEMBER},{MEMBER}");
        assert!(parse_connections(&repeated).is_err());
        let oversized = (0..=CONNECTION_SCOPE_MAX)
            .map(|value| Uuid::from_u128(value as u128 + 1).simple().to_string())
            .collect::<Vec<_>>()
            .join(",");
        assert!(parse_connections(&oversized).is_err());
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
                assert_eq!(token.capability_id, Some(u(MEMBER)));
            }
            other => panic!("expected run principal, got {other:?}"),
        }
        match principal_from_proxy_auth(SECRET, &basic_header(PROBE_ENCODED)) {
            Some(Principal::Probe(token)) => {
                assert_eq!(token.conversation_id, u(CONVERSATION));
                assert_eq!(token.probe_id, u(PROBE));
                assert_eq!(token.expires_at, EXPIRES_AT);
                assert_eq!(token.connections, vec![u(MEMBER)]);
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
            codec.from_proxy_auth(&header).unwrap().capability_id,
            Some(u(MEMBER))
        );
        assert!(matches!(
            principal_from_proxy_auth(SECRET, &header),
            Some(Principal::Run(_))
        ));
    }
}
