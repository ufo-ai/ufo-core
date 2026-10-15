//! The client for core `serve`'s internal egress-control RPC. Every policy decision, resolved
//! secret, ledger write, and tool-bridge dispatch lives behind these calls — the proxy holds none
//! of the keys or DB access itself. Each request presents the shared control bearer; the run/probe
//! token rides in the body as the raw `Proxy-Authorization` value core re-verifies and scopes by.

use serde::Deserialize;

use crate::types::{MeterRecord, Rule, ToolBridgeResponse};

const MAX_TOOL_BRIDGE_RESPONSE_BYTES: usize = 2 * 1_048_576;

#[derive(Clone)]
pub struct Control {
    base: String,
    token: String,
    http: reqwest::Client,
}

#[derive(Deserialize)]
struct AuthorizeResponse {
    authorized: bool,
    // Null on the wire when the turn is not authorized — core sends `generation: null`, so this is an
    // Option, not an i64 with a default (serde's default fills a missing key, never a present null).
    #[serde(default)]
    generation: Option<i64>,
}

#[derive(Deserialize)]
struct ResolveResponse {
    rules: Vec<Rule>,
}

impl Control {
    pub fn new(base: &str, token: &str) -> Control {
        Control {
            base: base.trim_end_matches('/').to_string(),
            token: token.to_string(),
            http: reqwest::Client::new(),
        }
    }

    /// The per-CONNECT authorization gate: the workspace's egress-rules generation while the token
    /// still names a live turn (or an unspent probe), None otherwise. Read fresh, never cached.
    pub async fn authorize(&self, proxy_auth: &str) -> anyhow::Result<Option<i64>> {
        let response: AuthorizeResponse = self
            .post(
                "/internal/egress/authorize",
                &serde_json::json!({
                    "proxy_auth": proxy_auth,
                }),
            )
            .await?;
        Ok(if response.authorized {
            response.generation
        } else {
            None
        })
    }

    /// The resolved rule set for this principal's agent — the model base, the workspace's keyed
    /// credentials (real secrets), and the agent's grants. Cached by the proxy per (token, generation).
    pub async fn resolve(&self, proxy_auth: &str) -> anyhow::Result<Vec<Rule>> {
        let response: ResolveResponse = self
            .post(
                "/internal/egress/resolve",
                &serde_json::json!({
                    "proxy_auth": proxy_auth,
                }),
            )
            .await?;
        Ok(response.rules)
    }

    /// Dispatch one authenticated sandbox request through core's live-turn tool bridge.
    pub async fn tool_bridge(
        &self,
        proxy_auth: &str,
        body: &[u8],
    ) -> anyhow::Result<ToolBridgeResponse> {
        let request: serde_json::Value = serde_json::from_slice(body)?;
        let response = self
            .http
            .post(format!("{}/internal/egress/tool-bridge", self.base))
            .bearer_auth(&self.token)
            .json(&serde_json::json!({
                "proxy_auth": proxy_auth,
                "request": request,
            }))
            .send()
            .await?;
        let status = response.status().as_u16();
        if response
            .content_length()
            .is_some_and(|length| length > MAX_TOOL_BRIDGE_RESPONSE_BYTES as u64)
        {
            anyhow::bail!("tool bridge response exceeds its body cap");
        }
        let body = response.bytes().await?.to_vec();
        if body.len() > MAX_TOOL_BRIDGE_RESPONSE_BYTES {
            anyhow::bail!("tool bridge response exceeds its body cap");
        }
        Ok(ToolBridgeResponse {
            status,
            headers: vec![("content-type".to_string(), "application/json".to_string())],
            body,
        })
    }

    /// Post a batch of metered events; core groups, prices, and writes the ledger.
    pub async fn meter(&self, records: &[MeterRecord]) -> anyhow::Result<()> {
        let _: serde::de::IgnoredAny = self
            .post(
                "/internal/egress/meter",
                &serde_json::json!({ "records": records }),
            )
            .await?;
        Ok(())
    }

    async fn post<T: serde::de::DeserializeOwned>(
        &self,
        path: &str,
        body: &serde_json::Value,
    ) -> anyhow::Result<T> {
        let response = self
            .http
            .post(format!("{}{path}", self.base))
            .bearer_auth(&self.token)
            .json(body)
            .send()
            .await?
            .error_for_status()?;
        Ok(response.json().await?)
    }
}
