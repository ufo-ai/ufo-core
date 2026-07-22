"""What the egress proxy is allowed to do, derived — never registered.

Rules are values the proxy reads, not an API extensions call: a credential slot implies its
sentinel→real injection, a granted host implies its scope, a metered host implies its dimension.
Three derivations produce them — the deploy's model provider from its key, each manifest credential
slot from its stored secret, and each OAuth grant from its host — and derivation is the only path
in. A grant injects nothing: the broker holds the account's token and executes server-side, so a
grant only admits and meters its host."""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from ufo.connectors import CliCredential, RequestForwarder
from ufo.credentials import CredentialSlotUnset, CredentialStore
from ufo.ext.manifest import Manifest
from ufo.grants import Grant, grant_sentinel
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


@dataclass(frozen=True)
class ForwardRule:
    """On the wire to `host`, a request whose `header` carries `sentinel` is not re-originated
    upstream — it is executed through the broker's `forward` under the granted `account_id`, which
    injects the real credential server-side. The wire analog of a connector tool call: the token
    never exists on this deploy, so there is nothing to inject."""

    host: str
    header: str
    sentinel: str
    account_id: str
    forward: RequestForwarder


Rule = ScopeRule | InjectionRule | MeterRule | ForwardRule


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


def derive_grant_rules(
    grants: tuple[Grant, ...], transfer_hosts: Mapping[str, tuple[str, ...]] | None = None
) -> tuple[Rule, ...]:
    """Each active grant admits its provider's own host — plus the broker file-store hosts its
    connector declares as `transfer_hosts`, where the sandbox fetches a tool's presigned file
    outputs and stages its file inputs — and meters every request to each under `requests`, so any
    egress to a granted host shows in `ufoctl spend`. A grant injects nothing — the broker holds
    the account's token and runs connector tools server-side, so no secret is on the wire. An
    ungranted host derives no ScopeRule, so the proxy refuses it at CONNECT."""
    rules: list[Rule] = []
    for grant in grants:
        hosts = (grant.host, *(transfer_hosts or {}).get(grant.provider, ()))
        rules.append(ScopeRule(allowed_hosts=frozenset(hosts)))
        rules.extend(MeterRule(host=host, dimension=GRANT_METER_DIMENSION) for host in hosts)
    return tuple(rules)


def derive_cli_rules(
    grants: tuple[Grant, ...],
    acting_member_id: UUID | None,
    clis: Mapping[str, CliCredential],
) -> tuple[Rule, ...]:
    """Each grant whose connector declares a CLI credential and whose account the acting member may
    use — their own grant, or one shared with the workspace — forwards its sentinel-carrying
    requests through the broker. Use gates on the acting member exactly as connector tools do: a
    foreign private grant derives nothing, and a memberless turn forwards only shared grants."""
    return tuple(
        ForwardRule(
            host=grant.host,
            header=cli.header,
            sentinel=grant_sentinel(grant.account_id),
            account_id=grant.account_id,
            forward=cli.forward,
        )
        for grant in grants
        if (cli := clis.get(grant.provider)) is not None
        and (grant.shared or grant.grantor_member_id == acting_member_id)
    )


def connector_transfer_hosts(manifests: tuple[Manifest, ...]) -> dict[str, tuple[str, ...]]:
    """Each installed connector's declared broker file-store hosts, keyed by provider — the map the
    per-turn resolver folds into `derive_grant_rules` so a grant admits them live from the current
    deploy's manifests, never a persisted copy a broker-side store move would strand."""
    return {
        connector.oauth.provider: connector.transfer_hosts
        for manifest in manifests
        for connector in manifest.connectors
        if connector.transfer_hosts
    }


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
