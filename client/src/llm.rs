//! The `ufo llm` verb: the one credentialed egress a sandbox script gets.
//!
//! The Anthropic Messages API, reached through the egress proxy the carrier set. The sandbox holds
//! only the sentinel key (`ANTHROPIC_API_KEY`) and dials out through `HTTPS_PROXY`, trusting the
//! proxy CA (`SSL_CERT_FILE`, else this machine's own store); the proxy swaps the sentinel for the
//! real key on the wire, so the raw credential never enters the sandbox. The proxy's own userinfo
//! becomes a `Proxy-Authorization` header and leaves the request line, so the run token is never
//! written into a URL.
//!
//! The request is spoken directly over rustls rather than through the client's HTTP agent, because
//! the deploy's proxy terminates TLS itself: a `https://` proxy needs TLS to the proxy and then TLS
//! to the host inside the tunnel, which the agent's proxy support does not offer.

use std::io::Write;
use std::time::Duration;

pub const USAGE: &str = "usage: ufo llm [--model MODEL] [--max-tokens N] PROMPT";
const DEFAULT_MODEL: &str = "claude-opus-4-8";
const DEFAULT_MAX_TOKENS: u64 = 8192;
const ANTHROPIC_HOST: &str = "api.anthropic.com";
const ANTHROPIC_PORT: u16 = 443;
const ANTHROPIC_PATH: &str = "/v1/messages";
const ANTHROPIC_URL: &str = "https://api.anthropic.com/v1/messages";
const ANTHROPIC_VERSION: &str = "2023-06-01";
const API_KEY_ENV: &str = "ANTHROPIC_API_KEY";
const REQUEST_TIMEOUT: Duration = Duration::from_secs(60);
const MAX_RESPONSE_BYTES: usize = 2 * 1024 * 1024;

/// One call's argv, after the flags are read off it.
#[derive(Debug, PartialEq)]
pub struct Call {
    pub model: String,
    pub max_tokens: u64,
    pub prompt: String,
}

/// Write the answer and answer the exit code: the response text plus one newline on stdout, a
/// handled failure as `ufo llm: {error}` on stderr and exit 1, a usage error exit 2.
pub fn main(args: &[String]) -> i32 {
    let call = match parse(args) {
        Ok(call) => call,
        Err(usage) => {
            eprintln!("{usage}");
            return 2;
        }
    };
    match answered(&call) {
        Ok(text) => {
            let mut out = std::io::stdout().lock();
            let _ = out.write_all(text.as_bytes());
            let _ = out.write_all(b"\n");
            let _ = out.flush();
            0
        }
        Err(error) => {
            eprintln!("ufo llm: {error}");
            1
        }
    }
}

fn answered(call: &Call) -> Result<String, String> {
    let key = require_env(API_KEY_ENV)?;
    anthropic_text(&posted(call, &key)?)
}

pub fn parse(args: &[String]) -> Result<Call, String> {
    let mut model = DEFAULT_MODEL.to_string();
    let mut max_tokens = DEFAULT_MAX_TOKENS;
    let mut prompt: Option<String> = None;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--model" => {
                model = args
                    .get(index + 1)
                    .ok_or_else(|| USAGE.to_string())?
                    .clone();
                index += 2;
            }
            "--max-tokens" => {
                max_tokens = args
                    .get(index + 1)
                    .and_then(|value| value.parse::<u64>().ok())
                    .ok_or_else(|| USAGE.to_string())?;
                index += 2;
            }
            other if prompt.is_none() && !other.starts_with("--") => {
                prompt = Some(other.to_string());
                index += 1;
            }
            _ => return Err(USAGE.to_string()),
        }
    }
    Ok(Call {
        model,
        max_tokens,
        prompt: prompt.ok_or_else(|| USAGE.to_string())?,
    })
}

pub fn request_body(call: &Call) -> Vec<u8> {
    serde_json::to_vec(&serde_json::json!({
        "model": call.model,
        "max_tokens": call.max_tokens,
        "messages": [{"role": "user", "content": call.prompt}],
    }))
    .expect("a call serializes")
}

pub fn request_head(key: &str, length: usize) -> String {
    format!(
        "POST {ANTHROPIC_PATH} HTTP/1.1\r\n\
         Host: {ANTHROPIC_HOST}\r\n\
         content-type: application/json\r\n\
         x-api-key: {key}\r\n\
         anthropic-version: {ANTHROPIC_VERSION}\r\n\
         content-length: {length}\r\n\
         connection: close\r\n\r\n"
    )
}

/// Every `text` block of the response, concatenated.
pub fn anthropic_text(response: &serde_json::Value) -> Result<String, String> {
    let Some(blocks) = response.get("content").and_then(|value| value.as_array()) else {
        return Err(format!(
            "anthropic response has no content array: {response}"
        ));
    };
    Ok(blocks
        .iter()
        .filter(|block| block.get("type").and_then(|kind| kind.as_str()) == Some("text"))
        .filter_map(|block| block.get("text").and_then(|text| text.as_str()))
        .collect())
}

fn posted(call: &Call, key: &str) -> Result<serde_json::Value, String> {
    let body = request_body(call);
    let (status, body) = crate::egress::post(
        ANTHROPIC_URL,
        ANTHROPIC_HOST,
        ANTHROPIC_PORT,
        &request_head(key, body.len()),
        &body,
        REQUEST_TIMEOUT,
        MAX_RESPONSE_BYTES,
    )?;
    if status != 200 {
        return Err(format!(
            "{ANTHROPIC_URL} -> {status}: {}",
            String::from_utf8_lossy(&body)
        ));
    }
    let decoded: serde_json::Value = serde_json::from_slice(&body)
        .map_err(|_| format!("{ANTHROPIC_URL} returned invalid JSON"))?;
    if !decoded.is_object() {
        return Err(format!(
            "{ANTHROPIC_URL} returned non-object JSON: {}",
            String::from_utf8_lossy(&body)
        ));
    }
    Ok(decoded)
}

fn require_env(name: &str) -> Result<String, String> {
    env_nonempty(name).ok_or_else(|| format!("{name} is required in the sandbox environment"))
}

fn env_nonempty(name: &str) -> Option<String> {
    std::env::var(name).ok().filter(|value| !value.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_defaults_and_flags() {
        assert_eq!(
            parse(&["hello".to_string()]).unwrap(),
            Call {
                model: DEFAULT_MODEL.to_string(),
                max_tokens: DEFAULT_MAX_TOKENS,
                prompt: "hello".to_string(),
            }
        );
        let flagged = parse(&[
            "--model".to_string(),
            "claude-3".to_string(),
            "--max-tokens".to_string(),
            "16".to_string(),
            "why".to_string(),
        ])
        .unwrap();
        assert_eq!(flagged.model, "claude-3");
        assert_eq!(flagged.max_tokens, 16);
        assert_eq!(flagged.prompt, "why");
    }

    #[test]
    fn parse_refuses_a_usage_error() {
        for args in [
            vec![],
            vec!["--model".to_string()],
            vec!["--max-tokens".to_string(), "many".to_string()],
            vec!["--unknown".to_string(), "x".to_string()],
            vec!["one".to_string(), "two".to_string()],
        ] {
            assert_eq!(parse(&args).unwrap_err(), USAGE);
        }
    }

    #[test]
    fn the_request_carries_the_sentinel_key_and_the_api_version() {
        let call = Call {
            model: "claude-opus-4-8".to_string(),
            max_tokens: 8192,
            prompt: "hi".to_string(),
        };
        let body = request_body(&call);
        assert_eq!(
            String::from_utf8(body.clone()).unwrap(),
            r#"{"max_tokens":8192,"messages":[{"content":"hi","role":"user"}],"model":"claude-opus-4-8"}"#
        );
        let head = request_head("sentinel", body.len());
        assert!(head.starts_with("POST /v1/messages HTTP/1.1\r\n"), "{head}");
        assert!(head.contains("x-api-key: sentinel\r\n"));
        assert!(head.contains("anthropic-version: 2023-06-01\r\n"));
        assert!(head.contains(&format!("content-length: {}\r\n", body.len())));
    }

    #[test]
    fn text_blocks_concatenate_and_a_missing_array_refuses() {
        let response = serde_json::json!({
            "content": [
                {"type": "text", "text": "one "},
                {"type": "thinking", "text": "skipped"},
                {"type": "text", "text": "two"},
            ]
        });
        assert_eq!(anthropic_text(&response).unwrap(), "one two");
        let empty = serde_json::json!({"content": []});
        assert_eq!(anthropic_text(&empty).unwrap(), "");
        let broken = serde_json::json!({"stop_reason": "end_turn"});
        assert_eq!(
            anthropic_text(&broken).unwrap_err(),
            "anthropic response has no content array: {\"stop_reason\":\"end_turn\"}"
        );
    }

    #[test]
    fn a_missing_api_key_names_the_variable() {
        assert_eq!(
            require_env("UFO_LLM_TEST_ABSENT").unwrap_err(),
            "UFO_LLM_TEST_ABSENT is required in the sandbox environment"
        );
    }
}
