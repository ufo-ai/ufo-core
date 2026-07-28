"""What the egress proxy is allowed to do, derived — never registered.

Rules are values the proxy reads, not an API extensions call: a credential slot implies its
sentinel→real injection, a granted host implies its scope, a metered host implies its dimension.
A live turn adds public internet. A grant injects nothing: the broker holds the account's token
and executes server-side, so a grant only admits and meters its host."""

from base64 import b64encode
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from ufo.connectors import CliCredential, RequestForwarder
from ufo.credentials import (
    CredentialStore,
    credential_host,
    slot_secret,
)
from ufo.ext.manifest import CredentialSlot, Manifest, open_connector_namespace
from ufo.grants import Grant, grant_sentinel
from ufo.o11y import warn
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


async def derive_credential_rules(
    slots: tuple[CredentialSlot, ...], workspace_id: UUID, store: CredentialStore
) -> tuple[Rule, ...]:
    """This workspace's keyed providers: each injecting slot holding a secret swaps that secret in
    for the sentinel the sandbox sees, and every host so reached is admitted and metered. Resolved
    per workspace against the run token's own `workspace_id`, so one shared proxy serves every
    workspace and no workspace's secret enters a static base; a slot with nothing stored opens no
    egress, and one whose stored host selection the declaration does not offer opens none either.

    An injection is per slot, because each key rides its own header; scope and metering are per
    host, because a request is one request however many keys it carries. Grouping by the resolved
    host is what makes that true — a provider taking two keys reaches one host, so it admits and
    meters it once, and the egress metric counts requests rather than headers.

    A `git_basic_user` slot composes its header value here rather than storing it composed: the
    secret is one rotatable value, and git's smart-HTTP wants it as the password half of Basic.

    **One slot resolves or one slot is withheld — never the turn.** Whatever a slot's secret needs
    to resolve (a provider exchange, a stored seal this deploy opens, a host selection) is that
    slot's own uncertainty, so a slot that raises contributes nothing and the derivation continues.
    This function is total by construction, and that is what makes the rest of the turn's rules
    safe: the public-internet rule and the grant rules are composed around this call, so a fault
    escaping here takes the workspace's whole egress with it — every host refused but the model
    provider, on every turn, over one slot's unreadable value. `error_class` carries which fault it
    was, because an unreachable provider clears itself while a value only a rebind repairs does not.
    A withheld slot never falls through to its stored value — that would authenticate as a different
    identity than the one the workspace bound."""
    grouped: dict[str, list[InjectionRule]] = {}
    dimensions: dict[str, str] = {}
    for slot in slots:
        target = slot.injection
        if target is None:
            continue
        try:
            real = await slot_secret(slot.name, slot.source, workspace_id, store)
            if real is None:
                continue
            host = await credential_host(store, workspace_id, target.host)
        except Exception as error:
            warn(
                "egress.credential_slot_failed",
                slot=slot.name,
                error_class=type(error).__name__,
                error=str(error),
            )
            continue
        if host is None:
            warn("egress.credential_host_unavailable", slot=slot.name)
            continue
        if target.git_basic_user is not None:
            encoded = b64encode(f"{target.git_basic_user}:{real}".encode()).decode()
            real = f"Basic {encoded}"
        grouped.setdefault(host, []).append(
            InjectionRule(host=host, header=target.header, sentinel=target.sentinel, real=real)
        )
        if target.dimension is not None:
            dimensions[host] = target.dimension
    rules: list[Rule] = []
    for host, injections in sorted(grouped.items()):
        rules.append(ScopeRule(allowed_hosts=frozenset({host})))
        rules.extend(injections)
        if host in dimensions:
            rules.append(MeterRule(host=host, dimension=dimensions[host]))
    return tuple(rules)


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
