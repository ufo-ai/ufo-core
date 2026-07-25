"""What the egress proxy is allowed to do, derived — never registered.

Rules are values the proxy reads, not an API extensions call: a credential slot implies its
sentinel→real injection, a granted host implies its scope, a metered host implies its dimension.
A live turn adds public internet. A grant injects nothing: the broker holds the account's token
and executes server-side, so a grant only admits and meters its host."""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from ufo.connectors import CliCredential, RequestForwarder
from ufo.ext.manifest import Manifest, open_connector_namespace
from ufo.grants import Grant, grant_sentinel
from ufo.sandbox.session import SENTINEL_MODEL_KEY

REQUEST_METER_DIMENSION = "requests"

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
    """An exact host allowlist for model and connector traffic."""

    allowed_hosts: frozenset[str]


@dataclass(frozen=True)
class InternetRule:
    """A live turn may reach metered public internet."""


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


Rule = ScopeRule | InternetRule | InjectionRule | MeterRule | ForwardRule


def provider_host(model: str) -> str:
    for prefix, host in PROVIDER_HOSTS.items():
        if model.startswith(prefix):
            return host
    raise ValueError(f"no provider host for model {model!r}")


def derive_model_rules(model: str, real_key: str) -> tuple[Rule, ...]:
    """The U2 rule set: the deploy's model provider is reachable and its key is injected from the
    sentinel the sandbox sees. The auth header is provider-shaped — Anthropic reads a raw key from
    `x-api-key`, OpenAI a `Bearer` token from `authorization`."""
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


def derive_manifest_rules(manifests: tuple[Manifest, ...]) -> tuple[InternetRule, ...]:
    """A deploy with an extension that needs sandbox internet admits its live turns."""
    return (InternetRule(),) if any(manifest.sandbox_internet for manifest in manifests) else ()


def derive_grant_rules(
    grants: tuple[Grant, ...], transfer_hosts: "ConnectorTransferHosts | None" = None
) -> tuple[Rule, ...]:
    """Each active grant admits its provider's own host — plus the broker file-store hosts
    `transfer_hosts` resolves for it, where the sandbox fetches a tool's presigned file outputs and
    stages its file inputs — and meters every request to each under `requests`, so any egress to a
    granted host shows in `ufoctl spend`. A grant injects nothing — the broker holds the account's
    token and runs connector tools server-side, so no secret is on the wire. A brokered grant admits
    no provider host of its own (its `host` is empty), so only its transfer hosts scope; an
    ungranted host derives no exact ScopeRule, MeterRule, or authenticated path."""
    rules: list[Rule] = []
    for grant in grants:
        extra = transfer_hosts.of(grant.provider) if transfer_hosts is not None else ()
        hosts = tuple(dict.fromkeys(host for host in (grant.host, *extra) if host))
        if hosts:
            rules.append(ScopeRule(allowed_hosts=frozenset(hosts)))
            rules.extend(MeterRule(host=host, dimension=REQUEST_METER_DIMENSION) for host in hosts)
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


@dataclass(frozen=True)
class ConnectorTransferHosts:
    """The broker file-store hosts a grant admits at the egress proxy, derived live from the
    deploy's manifests so a broker-side store move never strands a persisted copy. `explicit` maps
    every registered connector's provider to its declared hosts (empty for one that declares none),
    so `default` — the open connector namespace's hosts — reaches only a provider no connector
    registered. `of` is the one lookup the grant-rule derivation makes."""

    explicit: Mapping[str, tuple[str, ...]]
    default: tuple[str, ...] = ()

    def of(self, provider: str) -> tuple[str, ...]:
        return self.explicit.get(provider, self.default)


def connector_transfer_hosts(manifests: tuple[Manifest, ...]) -> ConnectorTransferHosts:
    """The deploy's connector file-store hosts: every registered connector's declared hosts keyed by
    provider (so one that declares none admits none, never the namespace default), plus the open
    namespace's hosts as the default for any other slug it brokers."""
    explicit = {
        connector.oauth.provider: connector.transfer_hosts
        for manifest in manifests
        for connector in manifest.connectors
    }
    namespace = open_connector_namespace(manifests)
    default = namespace.transfer_hosts if namespace is not None else ()
    return ConnectorTransferHosts(explicit=explicit, default=default)
