//! The `ufo tool` JSON bridge to object and connector tools.

use std::io::{IsTerminal, Read, Write};
use std::time::Duration;

pub const USAGE: &str = "usage: ufo tool --list | ufo tool TOOL [--describe]";
const BRIDGE_URL_ENV: &str = "UFO_TOOL_BRIDGE_URL";
const REQUEST_TIMEOUT: Duration = Duration::from_secs(605);
const MAX_REQUEST_BYTES: usize = 1_048_576;
const MAX_RESPONSE_BYTES: usize = 2 * 1_048_576;

#[derive(Debug, PartialEq)]
pub enum Call {
    List,
    Describe(String),
    Execute(String),
}

#[derive(Debug, PartialEq)]
struct Endpoint {
    url: String,
    host: String,
    port: u16,
    path: String,
}

pub fn main(args: &[String]) -> i32 {
    let call = match parse(args) {
        Ok(call) => call,
        Err(usage) => {
            eprintln!("{usage}");
            return 2;
        }
    };
    let result = execute(&call);
    let (envelope, code) = match result {
        Ok(envelope) => {
            let code = if envelope.get("ok").and_then(serde_json::Value::as_bool) == Some(true) {
                0
            } else {
                1
            };
            (envelope, code)
        }
        Err(error) => (serde_json::json!({"ok": false, "error": error}), 1),
    };
    let mut out = std::io::stdout().lock();
    let _ = serde_json::to_writer(&mut out, &envelope);
    let _ = out.write_all(b"\n");
    let _ = out.flush();
    code
}

pub fn parse(args: &[String]) -> Result<Call, String> {
    match args {
        [flag] if flag == "--list" => Ok(Call::List),
        [tool] if !tool.starts_with("--") => Ok(Call::Execute(tool.clone())),
        [tool, flag]
            if !tool.starts_with("--") && matches!(flag.as_str(), "--describe" | "--schema") =>
        {
            Ok(Call::Describe(tool.clone()))
        }
        [flag, tool]
            if matches!(flag.as_str(), "--describe" | "--schema") && !tool.starts_with("--") =>
        {
            Ok(Call::Describe(tool.clone()))
        }
        _ => Err(USAGE.to_string()),
    }
}

fn execute(call: &Call) -> Result<serde_json::Value, String> {
    let endpoint = endpoint(&require_env(BRIDGE_URL_ENV)?)?;
    let arguments = match call {
        Call::Execute(_) => read_arguments()?,
        Call::List | Call::Describe(_) => serde_json::json!({}),
    };
    let body = request_body(call, &arguments)?;
    let (status, response) = crate::egress::post(
        &endpoint.url,
        &endpoint.host,
        endpoint.port,
        &request_head(&endpoint, body.len()),
        &body,
        REQUEST_TIMEOUT,
        MAX_RESPONSE_BYTES,
    )?;
    let decoded: serde_json::Value = serde_json::from_slice(&response)
        .map_err(|_| format!("{} returned invalid JSON", endpoint.url))?;
    if !decoded.is_object()
        || decoded
            .get("ok")
            .and_then(serde_json::Value::as_bool)
            .is_none()
    {
        return Err(format!("{} returned an invalid envelope", endpoint.url));
    }
    if status != 200 && decoded.get("ok") != Some(&serde_json::Value::Bool(false)) {
        return Err(format!("{} returned HTTP {status}", endpoint.url));
    }
    Ok(decoded)
}

fn read_arguments() -> Result<serde_json::Value, String> {
    if std::io::stdin().is_terminal() {
        return Ok(serde_json::json!({}));
    }
    let stdin = std::io::stdin();
    read_arguments_from(stdin.lock())
}

fn read_arguments_from(reader: impl Read) -> Result<serde_json::Value, String> {
    let mut body = Vec::new();
    reader
        .take((MAX_REQUEST_BYTES + 1) as u64)
        .read_to_end(&mut body)
        .map_err(|error| format!("could not read JSON input: {error}"))?;
    if body.len() > MAX_REQUEST_BYTES {
        return Err(format!(
            "JSON input exceeds the {MAX_REQUEST_BYTES}-byte limit"
        ));
    }
    if body.iter().all(u8::is_ascii_whitespace) {
        return Ok(serde_json::json!({}));
    }
    let decoded: serde_json::Value =
        serde_json::from_slice(&body).map_err(|error| format!("invalid JSON input: {error}"))?;
    if !decoded.is_object() {
        return Err("JSON input must be an object".to_string());
    }
    Ok(decoded)
}

fn request_body(call: &Call, arguments: &serde_json::Value) -> Result<Vec<u8>, String> {
    let mut request_id = [0u8; 16];
    getrandom::fill(&mut request_id)
        .map_err(|error| format!("could not create a request id: {error}"))?;
    let request_id = request_id
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    let request = match call {
        Call::List => serde_json::json!({
            "request_id": request_id,
            "action": "list",
            "arguments": arguments,
        }),
        Call::Describe(tool) => serde_json::json!({
            "request_id": request_id,
            "action": "get_schema",
            "tool_name": tool,
            "arguments": arguments,
        }),
        Call::Execute(tool) => serde_json::json!({
            "request_id": request_id,
            "action": "execute",
            "tool_name": tool,
            "arguments": arguments,
        }),
    };
    let body = serde_json::to_vec(&request)
        .map_err(|error| format!("could not encode the bridge request: {error}"))?;
    if body.len() > MAX_REQUEST_BYTES {
        return Err(format!(
            "bridge request exceeds the {MAX_REQUEST_BYTES}-byte limit"
        ));
    }
    Ok(body)
}

fn endpoint(raw: &str) -> Result<Endpoint, String> {
    let invalid = || format!("invalid {BRIDGE_URL_ENV}: {raw:?}");
    let rest = raw.strip_prefix("https://").ok_or_else(invalid)?;
    if rest.contains(['?', '#', '@']) {
        return Err(invalid());
    }
    let (authority, prefix) = rest.split_once('/').unwrap_or((rest, ""));
    let (host, port) = match authority.rsplit_once(':') {
        Some((host, port)) => (host, port.parse::<u16>().map_err(|_| invalid())?),
        None => (authority, 443),
    };
    if host.is_empty() || port == 0 {
        return Err(invalid());
    }
    let base = raw.trim_end_matches('/');
    let path = if prefix.is_empty() {
        "/request".to_string()
    } else {
        format!("/{}/request", prefix.trim_end_matches('/'))
    };
    Ok(Endpoint {
        url: format!("{base}/request"),
        host: host.to_string(),
        port,
        path,
    })
}

fn request_head(endpoint: &Endpoint, length: usize) -> String {
    let authority = if endpoint.port == 443 {
        endpoint.host.clone()
    } else {
        format!("{}:{}", endpoint.host, endpoint.port)
    };
    format!(
        "POST {} HTTP/1.1\r\n\
         Host: {authority}\r\n\
         content-type: application/json\r\n\
         content-length: {length}\r\n\
         connection: close\r\n\r\n",
        endpoint.path
    )
}

fn require_env(name: &str) -> Result<String, String> {
    std::env::var(name)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .ok_or_else(|| format!("{name} is required in the sandbox environment"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_list_call_and_describe_forms() {
        assert_eq!(parse(&["--list".to_string()]).unwrap(), Call::List);
        assert_eq!(
            parse(&["object_list".to_string()]).unwrap(),
            Call::Execute("object_list".to_string())
        );
        assert_eq!(
            parse(&["--describe".to_string(), "call_external_tool".to_string()]).unwrap(),
            Call::Describe("call_external_tool".to_string())
        );
    }

    #[test]
    fn rejects_extra_arguments() {
        assert_eq!(
            parse(&["object_list".to_string(), "extra".to_string()]),
            Err(USAGE.to_string())
        );
    }

    #[test]
    fn reads_only_json_objects() {
        assert_eq!(
            read_arguments_from(b"{\"kind\":\"agent\"}".as_slice()).unwrap(),
            serde_json::json!({"kind": "agent"})
        );
        assert_eq!(
            read_arguments_from(b"[]".as_slice()),
            Err("JSON input must be an object".to_string())
        );
    }

    #[test]
    fn parses_bridge_endpoint() {
        assert_eq!(
            endpoint("https://tools.ufo.internal").unwrap(),
            Endpoint {
                url: "https://tools.ufo.internal/request".to_string(),
                host: "tools.ufo.internal".to_string(),
                port: 443,
                path: "/request".to_string(),
            }
        );
        assert_eq!(
            endpoint("https://bridge.local:8443/tools/").unwrap(),
            Endpoint {
                url: "https://bridge.local:8443/tools/request".to_string(),
                host: "bridge.local".to_string(),
                port: 8443,
                path: "/tools/request".to_string(),
            }
        );
    }

    #[test]
    fn requests_use_the_bridge_json_contract() {
        let call = Call::Execute("object_get".to_string());
        let decoded: serde_json::Value = serde_json::from_slice(
            &request_body(&call, &serde_json::json!({"kind": "agent"})).unwrap(),
        )
        .unwrap();
        assert_eq!(decoded["action"], "execute");
        assert_eq!(decoded["tool_name"], "object_get");
        assert_eq!(decoded["arguments"], serde_json::json!({"kind": "agent"}));
        assert_eq!(decoded["request_id"].as_str().unwrap().len(), 32);

        let listed: serde_json::Value =
            serde_json::from_slice(&request_body(&Call::List, &serde_json::json!({})).unwrap())
                .unwrap();
        assert_eq!(listed["action"], "list");
        assert!(listed.get("tool_name").is_none());
    }
}
