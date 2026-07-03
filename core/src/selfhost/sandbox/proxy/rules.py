"""What the egress proxy is allowed to do, derived — never registered.

Rules are values the proxy reads, not an API extensions call: a credential slot implies its
sentinel→real injection, a granted account implies its host scope, a metered host implies its
ledger dimension. U2 derives the one rule set it can without the extension system: the model
provider the deploy already holds a key for. Manifests (U3) and connector grants (U8) widen the
inputs; the derivation stays the only path in."""

from dataclasses import dataclass

SENTINEL_MODEL_KEY = "SELFHOST_SENTINEL_MODEL_KEY"
ANTHROPIC_HOST = "api.anthropic.com"
OPENAI_HOST = "api.openai.com"
PROVIDER_HOSTS = {
    "claude-": ANTHROPIC_HOST,
    "gpt-": OPENAI_HOST,
    "o1": OPENAI_HOST,
    "o3": OPENAI_HOST,
    "o4": OPENAI_HOST,
    "chatgpt-": OPENAI_HOST,
}


@dataclass(frozen=True)
class ScopeRule:
    """The container may open a connection only to a host on this allowlist; everything else is
    refused at CONNECT. Default-deny is the absence of a matching ScopeRule."""

    allowed_hosts: frozenset[str]


@dataclass(frozen=True)
class InjectionRule:
    """On the wire to `host`, replace the sentinel header value the container sees with the real
    secret, so the raw credential never enters the sandbox."""

    host: str
    header: str
    sentinel: str
    real: str


@dataclass(frozen=True)
class MeterRule:
    """Every request to `host` is metered under `dimension`; the ledger write lands in U7."""

    host: str
    dimension: str


Rule = ScopeRule | InjectionRule | MeterRule


def provider_host(model: str) -> str:
    for prefix, host in PROVIDER_HOSTS.items():
        if model.startswith(prefix):
            return host
    raise ValueError(f"no provider host for model {model!r}")


def derive_model_rules(model: str, real_key: str) -> tuple[Rule, ...]:
    """The U2 rule set: the deploy's model provider is reachable and its key is injected from the
    sentinel the sandbox sees; nothing else is allowed out."""
    host = provider_host(model)
    return (
        ScopeRule(allowed_hosts=frozenset({host})),
        InjectionRule(
            host=host,
            header="authorization",
            sentinel=f"Bearer {SENTINEL_MODEL_KEY}",
            real=f"Bearer {real_key}",
        ),
        MeterRule(host=host, dimension="tokens"),
    )
