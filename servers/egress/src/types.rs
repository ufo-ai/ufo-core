//! The shared value types. `Rule` and `MeterRecord` are the RPC wire contract with core `serve`
//! (Python serializes, Rust deserializes / posts); the token types are what the proxy verifies
//! locally to scope connection caps and the rule cache.

use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};
use uuid::Uuid;

/// A model call's token split, one i64 per class — posted to the meter RPC, priced core-side.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Usage {
    pub input_tokens: i64,
    pub output_tokens: i64,
    pub cache_read_tokens: i64,
    pub cache_write_5m_tokens: i64,
    pub cache_write_30m_tokens: i64,
    pub cache_write_1h_tokens: i64,
}

impl Usage {
    pub fn total(&self) -> i64 {
        self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_5m_tokens
            + self.cache_write_30m_tokens
            + self.cache_write_1h_tokens
    }
}

/// What egress is allowed for one principal, resolved by core `serve` and returned over the resolve
/// RPC. `Injection.real` carries the resolved secret the proxy swaps onto the wire — a bare secret
/// the request's scheme prefix stays in front of, or, for a sentinel riding as a Basic password, the
/// complete `Basic …` header value; the master key that produced it never crosses. The `kind`-tagged
/// JSON is the one contract both sides share.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Rule {
    Scope {
        #[serde(rename = "hosts")]
        allowed_hosts: BTreeSet<String>,
        /// Admit these hosts, and still resolve the name and refuse an answer inside a private
        /// network. Core pins a scope whose host a workspace admin wrote, because an exact scope is
        /// otherwise the one path around the private-address check the internet rule applies.
        #[serde(default)]
        pinned: bool,
    },
    Internet,
    Injection {
        host: String,
        header: String,
        sentinel: String,
        real: String,
    },
    Meter {
        host: String,
        dimension: String,
    },
    Service {
        host: String,
        #[serde(default)]
        daemon_prefix: Option<String>,
    },
    /// Dial `host` through the deploy's residential proxy, so the request arrives from a consumer
    /// address. It admits nothing: the scope or internet rule that already reached it still decides.
    Residential {
        host: String,
    },
}

/// One metered event the proxy posts to the meter RPC; `serve` groups, prices, and writes the ledger.
/// `Metric` carries the connecting principal's workspace — no ledger charge, but the
/// `sandbox_egress_total` counter it becomes is tagged with the workspace, so usage splits by one.
/// counter the in-process Python proxy emitted directly, now teed through the RPC so `serve` (which
/// holds the OTLP meter provider) emits it. It fires once per metered CONNECT — for a token-metered
/// host that means at establish time, before and independent of whether usage parses.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum MeterRecord {
    Egress {
        workspace_id: Uuid,
        turn_id: Option<Uuid>,
    },
    Tokens {
        workspace_id: Uuid,
        turn_id: Uuid,
        model: String,
        usage: Usage,
    },
    Metric {
        host: String,
        dimension: String,
        workspace_id: Uuid,
    },
}

/// Whom one exec acts for: its turn's own member (`-`), a named member, or nobody (`~`).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum RunActor {
    Turn,
    Nobody,
    Member(Uuid),
}

/// The turn one sandbox process tree carries, and whom it acts for.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RunToken {
    pub workspace_id: Uuid,
    pub turn_id: Uuid,
    pub acts_for: RunActor,
}

/// The conversation one off-turn sandbox exec carries, and the member it acts for.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProbeToken {
    pub workspace_id: Uuid,
    pub conversation_id: Uuid,
    pub probe_id: Uuid,
    pub expires_at: i64,
    pub member_id: Option<Uuid>,
    pub internet_access: bool,
}

/// What a CONNECT presents itself as: a turn's run token, or one probe exec's own token.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Principal {
    Run(RunToken),
    Probe(ProbeToken),
}

impl Principal {
    pub fn workspace_id(&self) -> Uuid {
        match self {
            Principal::Run(t) => t.workspace_id,
            Principal::Probe(t) => t.workspace_id,
        }
    }
}

/// One tool-bridge dispatch's answer — core's status and JSON body — written back into the sandbox's
/// tunnel as a complete HTTP response.
#[derive(Clone, Debug)]
pub struct ToolBridgeResponse {
    pub status: u16,
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
}

pub const SENTINEL_MODEL_KEY: &str = "UFO_SENTINEL_MODEL_KEY";
pub const ANTHROPIC_HOST: &str = "api.anthropic.com";
pub const OPENAI_HOST: &str = "api.openai.com";
pub const REQUEST_METER_DIMENSION: &str = "requests";
pub const TOKENS_DIMENSION: &str = "tokens";
