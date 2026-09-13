"""What the egress proxy is allowed to do, derived — never registered.

Rules are values the proxy reads, not an API extensions call: a credential slot implies its
sentinel→real injection, a granted host implies its scope, a metered host implies its dimension.
A live turn adds public internet. A grant admits and meters its host; it injects only where its
connector declares a CLI credential, whose broker hands this deploy the account's token to swap
in for the grant's sentinel."""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from ufo.blob import FilesystemBlobStore, S3BlobStore
from ufo.harness.o11y import warn
from ufo.harness.sandbox.session import SENTINEL_MODEL_KEY
from ufo.runtime.access.connectors import CliCredential
from ufo.runtime.access.credentials import CredentialSlotUnset, CredentialStore, credential_host
from ufo.runtime.access.grants import Grant, grant_sentinel, scoped_cli_accounts
from ufo.runtime.access.workspace_slots import WorkspaceSlots
from ufo.runtime.ext.manifest import Manifest, open_connector_namespace

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
    """An exact host allowlist for model and connector traffic.

    `pinned` admits the host and still makes the proxy resolve it, refusing the CONNECT when the
    name answers a private address. A host this deploy's own code names is admitted unpinned — it
    is a provider these sources wrote down. A host a workspace admin wrote is pinned, because the
    allowlist is otherwise the one path around the private-address check that guards the open
    internet."""

    allowed_hosts: frozenset[str]
    pinned: bool = False


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
class ServiceRule:
    """Admit `host`'s CONNECT and relay its TLS-terminated requests to the local daemon that owns
    that host, which serves them on the sandbox's behalf.

    A cache host is present only for an agent that already holds `InternetRule` — the cache fronts
    several public hosts at once, so admitting it must never widen an agent's reach beyond the
    internet it already has. The preview host carries no such condition: the service fronts nothing
    public, so admitting it widens nothing, and rendering a file the sandbox already holds is not
    reaching the internet — an agent narrowed off the internet still shares files.

    `daemon_prefix` distinguishes the two shapes a daemon serves. None: `host` is the synthetic
    service host and the sandbox already addressed the daemon (`/git/<origin>/…`, `/render`), so the
    request relays verbatim. Set: `host` is a real registry the proxy transparently intercepts, so
    the request's path is prefixed with `daemon_prefix` (`/pkg/<host>`) to name the daemon's package
    route while the origin stays `host` itself."""

    host: str
    daemon_prefix: str | None = None


@dataclass(frozen=True)
class MeterRule:
    """Every request to `host` is metered under `dimension`."""

    host: str
    dimension: str


Rule = ScopeRule | InternetRule | InjectionRule | MeterRule | ServiceRule


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


async def derive_artifact_store_rules(blob: FilesystemBlobStore | S3BlobStore) -> tuple[Rule, ...]:
    """A deploy whose artifact store is S3 admits that store's own host and meters every request to
    it: sharing a file is the sandbox PUTting it to a presigned URL serve minted, so without this
    the only path a produced file leaves the sandbox is refused at CONNECT. Exact scope, not the
    public-internet rule — an agent narrowed off the internet still shares files, and only an
    exactly-scoped host is tunnelled opaquely, which a query-signed request must be to survive.
    Nothing is injected: the URL carries its own authority, bounded to one key and one measured
    body. The filesystem backend has no host, and no share reaches the network there."""
    match blob:
        case S3BlobStore():
            host = await blob.put_host()
            return (
                ScopeRule(allowed_hosts=frozenset({host})),
                MeterRule(host=host, dimension=REQUEST_METER_DIMENSION),
            )
        case _:
            return ()


async def derive_credential_rules(
    slots: WorkspaceSlots, workspace_id: UUID, store: CredentialStore
) -> tuple[Rule, ...]:
    """This workspace's keyed providers — the deploy's declared slots and the ones this workspace
    declares for itself: each injecting slot holding a secret swaps that secret in
    for the sentinel the sandbox sees, and every host so reached is admitted and metered. Resolved
    per workspace against the run token's own `workspace_id`, so one shared proxy serves every
    workspace and no workspace's secret enters a static base; a slot with nothing stored opens no
    egress, and one whose stored host selection the declaration does not offer opens none either.

    An injection is per slot, because each key rides its own header; scope and metering are per
    host, because a request is one request however many keys it carries. Grouping by the resolved
    host is what makes that true — a provider taking two keys reaches one host, so it admits and
    meters it once, and the egress metric counts requests rather than headers.

    **One slot resolves or one slot is withheld — never the turn.** Whatever a slot's secret needs
    to resolve (a stored value this deploy decrypts, a host selection) is that slot's own
    uncertainty, so a slot that raises contributes nothing and the derivation continues.
    This function is total by construction, and that is what makes the rest of the turn's rules
    safe: the public-internet rule and the grant rules are composed around this call, so a fault
    escaping here takes the workspace's whole egress with it — every host refused but the model
    provider, on every turn, over one slot's unreadable value. `error_class` carries which fault it
    was.

    A host a workspace declared for itself is admitted pinned: nobody on this deploy wrote it, so
    the proxy resolves it and refuses a name that answers a private address, which an exact scope
    otherwise skips."""
    resolved = tuple((slot, False) for slot in slots.deploy) + tuple(
        (slot, True) for slot in await slots.workspace(workspace_id)
    )
    grouped: dict[str, list[InjectionRule]] = {}
    dimensions: dict[str, str] = {}
    pinned: dict[str, bool] = {}
    for slot, workspace_owned in resolved:
        target = slot.injection
        if target is None:
            continue
        try:
            real = await store.get(workspace_id, slot.name)
            host = await credential_host(store, workspace_id, target.host)
        except CredentialSlotUnset:
            continue
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
        grouped.setdefault(host, []).append(
            InjectionRule(host=host, header=target.header, sentinel=target.sentinel, real=real)
        )
        pinned[host] = pinned.get(host, False) or workspace_owned
        if target.dimension is not None:
            dimensions[host] = target.dimension
    rules: list[Rule] = []
    for host, injections in sorted(grouped.items()):
        rules.append(ScopeRule(allowed_hosts=frozenset({host}), pinned=pinned[host]))
        rules.extend(injections)
        if host in dimensions:
            rules.append(MeterRule(host=host, dimension=dimensions[host]))
    return tuple(rules)


def derive_grant_rules(
    grants: tuple[Grant, ...],
    transfer_hosts: "ConnectorTransferHosts | None" = None,
    connections: tuple[UUID, ...] = (),
) -> tuple[Rule, ...]:
    """Each active grant admits its provider's own host — plus the broker file-store hosts
    `transfer_hosts` resolves for it, where the sandbox fetches a tool's presigned file outputs and
    stages its file inputs — and meters every request to each under `requests`, so any egress to a
    granted host shows in `ufoctl spend`. Nothing is injected here: connector tools run server-side
    at the broker, and the one grant whose token reaches the wire is a CLI credential's, derived
    by `derive_cli_rules`. A brokered grant admits no provider host of its own (its `host` is
    empty), so only its transfer hosts scope; an ungranted host derives no exact ScopeRule,
    MeterRule, or authenticated path. An exact connection scope admits only its listed grants."""
    rules: list[Rule] = []
    for grant in grants:
        if grant.connection_id not in connections:
            continue
        extra = transfer_hosts.of(grant.provider) if transfer_hosts is not None else ()
        hosts = tuple(dict.fromkeys(host for host in (grant.host, *extra) if host))
        if hosts:
            rules.append(ScopeRule(allowed_hosts=frozenset(hosts)))
            rules.extend(MeterRule(host=host, dimension=REQUEST_METER_DIMENSION) for host in hosts)
    return tuple(rules)


async def derive_cli_rules(
    grants: tuple[Grant, ...],
    clis: Mapping[str, CliCredential],
    workspace_id: UUID,
    connections: tuple[UUID, ...],
) -> tuple[Rule, ...]:
    """The preferred tier of each exact connector scope whose connector declares a CLI credential
    swaps its real token in for the grant's sentinel: private capabilities outrank shared ones,
    matching the static sandbox environment's selection. On the provider host the CLI sends it as
    ordinary auth, and on the connector's git host — admitted and metered here, since a grant's own
    rules scope only the API host — the sandbox's git helper sends it as the password half of a
    Basic credential, which the proxy re-encodes around the token.

    The token is read from the broker per grant, and one grant's fault withholds that grant alone:
    an account the broker can no longer authenticate loses its wire and nothing else, exactly as a
    credential slot's fault withholds one slot."""
    rules: list[Rule] = []
    git_hosts: dict[str, list[InjectionRule]] = {}
    usable = {
        (provider, account)
        for provider in clis
        for account in scoped_cli_accounts(grants, provider, connections)
    }
    for grant in grants:
        if (grant.provider, grant.account_id) not in usable:
            continue
        cli = clis.get(grant.provider)
        if cli is None:
            continue
        try:
            token = await cli.secret.secret(workspace_id, grant.account_id)
        except Exception as error:
            warn(
                "egress.cli_credential_failed",
                provider=grant.provider,
                account_id=grant.account_id,
                error_class=type(error).__name__,
                error=str(error),
            )
            continue
        sentinel = grant_sentinel(grant.account_id)
        rules.append(
            InjectionRule(host=grant.host, header=cli.header, sentinel=sentinel, real=token)
        )
        if cli.git is None:
            continue
        git_hosts.setdefault(cli.git.host, []).append(
            InjectionRule(host=cli.git.host, header=cli.header, sentinel=sentinel, real=token)
        )
    for host, injections in sorted(git_hosts.items()):
        rules.append(ScopeRule(allowed_hosts=frozenset({host})))
        rules.extend(injections)
        rules.append(MeterRule(host=host, dimension=REQUEST_METER_DIMENSION))
    return tuple(rules)


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
