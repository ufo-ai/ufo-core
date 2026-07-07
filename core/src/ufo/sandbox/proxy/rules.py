"""What the egress proxy is allowed to do, derived — never registered.

Rules are values the proxy reads, not an API extensions call: a credential slot implies its
sentinel→real injection, a granted host implies its scope, a metered host implies its dimension.
Three derivations produce them — the deploy's model provider from its key, each manifest credential
slot from its stored secret, and each OAuth grant from its host — and derivation is the only path
in. A grant injects nothing: the broker holds the account's token and executes server-side, so a
grant only admits and meters its host."""

from dataclasses import dataclass
from uuid import UUID

from ufo.credentials import CredentialSlotUnset, CredentialStore
from ufo.ext.manifest import Manifest
from ufo.grants import Grant
from ufo.sandbox.session import SENTINEL_MODEL_KEY

GRANT_METER_DIMENSION = "requests"

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
PROVIDER_AUTH = {
    ANTHROPIC_HOST: ("x-api-key", ""),
    OPENAI_HOST: ("authorization", "Bearer "),
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
    """Every request to `host` is metered under `dimension`."""

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
    sentinel the sandbox sees; nothing else is allowed out. The auth header is provider-shaped —
    Anthropic reads a raw key from `x-api-key`, OpenAI a `Bearer` token from `authorization`."""
    host = provider_host(model)
    header, prefix = PROVIDER_AUTH[host]
    return (
        ScopeRule(allowed_hosts=frozenset({host})),
        InjectionRule(
            host=host,
            header=header,
            sentinel=f"{prefix}{SENTINEL_MODEL_KEY}",
            real=f"{prefix}{real_key}",
        ),
        MeterRule(host=host, dimension="tokens"),
    )


def derive_grant_rules(grants: tuple[Grant, ...]) -> tuple[Rule, ...]:
    """Each active grant admits its provider's own host and meters every request to it under
    `requests`, so any egress to a granted host shows in `ufoctl spend`. A grant injects nothing —
    the broker holds the account's token and runs connector tools server-side, so no secret is on
    the wire. An ungranted host derives no ScopeRule, so the proxy refuses it at CONNECT."""
    rules: list[Rule] = []
    for grant in grants:
        rules.append(ScopeRule(allowed_hosts=frozenset({grant.host})))
        rules.append(MeterRule(host=grant.host, dimension=GRANT_METER_DIMENSION))
    return tuple(rules)


async def derive_credential_rules(
    manifests: tuple[Manifest, ...], workspace_id: UUID, store: CredentialStore
) -> tuple[Rule, ...]:
    """Each declared slot with an injection target becomes the egress rules that reach its host and
    swap its stored secret in for the sentinel; a slot with no stored secret opens no egress, and a
    slot with no injection target is code-only, never on the wire."""
    rules: list[Rule] = []
    for manifest in manifests:
        for slot in manifest.credentials:
            target = slot.injection
            if target is None:
                continue
            try:
                real = await store.get(workspace_id, slot.name)
            except CredentialSlotUnset:
                continue
            rules.append(ScopeRule(allowed_hosts=frozenset({target.host})))
            rules.append(
                InjectionRule(
                    host=target.host, header=target.header, sentinel=target.sentinel, real=real
                )
            )
            if target.dimension is not None:
                rules.append(MeterRule(host=target.host, dimension=target.dimension))
    return tuple(rules)
