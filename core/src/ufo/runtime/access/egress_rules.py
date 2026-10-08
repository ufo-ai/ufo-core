"""The session policy the proxy service enforces, compiled by name — derived, never registered.

A policy says which hosts a session reaches and which secret rides to each: a workspace credential
slot by its own name, a connection as `connection:<id>`, the deploy's model key as `ufo/models`. It
carries no value. The proxy service resolves a name when a request needs it and mints the sentinel
the sandbox holds in its place. Every entry is implied by something already declared: an injecting
slot holding a value binds its host, a grant admits its provider's host and broker file stores, a
connector's CLI credential binds the acting member's one account, and the deploy's model providers
and artifact store come from its config."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ufo.blob import FilesystemBlobStore, S3BlobStore
from ufo.harness.o11y import warn
from ufo.runtime.access.connectors import CliCredential
from ufo.runtime.access.credentials import CredentialStore, credential_host
from ufo.runtime.access.grants import Grant, cli_accounts
from ufo.runtime.access.workspace_slots import WorkspaceSlots
from ufo.runtime.ext.manifest import Manifest, open_connector_namespace

UFO_MODELS_SECRET = "ufo/models"
CONNECTION_SECRET_PREFIX = "connection:"
RUN_HEADER = "x-ufo-run"
ANTHROPIC_HOST = "api.anthropic.com"
OPENAI_HOST = "api.openai.com"
OPENROUTER_HOST = "openrouter.ai"
PROVIDER_AUTH = {
    ANTHROPIC_HOST: "x-api-key",
    OPENAI_HOST: "authorization",
    OPENROUTER_HOST: "authorization",
}


class HostEntry(BaseModel):
    """One host a session may reach."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    host: str


class Bind(BaseModel):
    """On the wire to `host`, the proxy service puts the value named `secret` into `header` where
    the sandbox sent the sentinel it was handed as `env`. A header name is case-insensitive, so
    `header` is held lowercased, the form the proxy service keys a bind by."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    host: str
    header: Annotated[str, StringConstraints(to_lower=True)]
    secret: str
    env: str


class Route(BaseModel):
    """The proxy service relays a request for `host` to `upstream`, adding `headers`."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    host: str
    upstream: str
    headers: dict[str, str] = Field(default_factory=dict)


class SessionPolicy(BaseModel):
    """The `policy` of a proxy session: the hosts it reaches, the secrets bound on them by name,
    the routes it relays, and whether any other public host is open to it."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    internet: bool = False
    hosts: tuple[HostEntry, ...] = ()
    bind: tuple[Bind, ...] = ()
    routes: tuple[Route, ...] = ()

    def digest(self) -> str:
        """The SHA-256 of the policy's canonical JSON: two compiles of one policy share it."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PolicyScope:
    """Whom one session acts for. `running` is a live turn: only it binds the deploy's model key
    and the routes core serves, which present `run_token` back to core."""

    workspace_id: UUID
    member_id: UUID | None
    internet_access_allowed: bool
    running: bool
    run_token: str | None


def policy_hosts(*hosts: str) -> tuple[HostEntry, ...]:
    """The named hosts as a policy lists them: sorted, each once, an empty name dropped."""
    return tuple(HostEntry(host=host) for host in sorted({host for host in hosts if host}))


async def derive_artifact_store_hosts(
    blob: FilesystemBlobStore | S3BlobStore,
) -> tuple[HostEntry, ...]:
    """A deploy whose artifact store is S3 admits that store's own host: sharing a file is the
    sandbox PUTting it to a presigned URL serve minted, so without it the only path a produced file
    leaves the sandbox is refused. Nothing is bound — the URL carries its own authority, bounded to
    one key and one measured body. The filesystem backend has no host, and no share reaches the
    network there."""
    match blob:
        case S3BlobStore():
            return policy_hosts(await blob.put_host())
        case _:
            return ()


def derive_manifest_internet(manifests: tuple[Manifest, ...]) -> bool:
    """A deploy with an extension that needs sandbox internet opens it to its live turns."""
    return any(manifest.sandbox_internet for manifest in manifests)


async def derive_credential_binds(
    slots: WorkspaceSlots, workspace_id: UUID, store: CredentialStore
) -> tuple[Bind, ...]:
    """This workspace's keyed providers — the deploy's declared slots and the ones this workspace
    declares for itself: each injecting slot holding a value binds its name on the host its
    declaration resolves to, under the env the sandbox sends its sentinel as. A slot with nothing
    stored binds nothing, and neither does one whose stored host selection the declaration does not
    offer. Which slots hold a value is one read; no value is read.

    **One slot resolves or one slot is withheld — never the turn.** A host selection this deploy
    cannot decrypt is that slot's own uncertainty, so a slot that raises contributes nothing and the
    derivation continues. This function is total by construction, and that is what makes the rest
    of the policy safe: the grant hosts and the model binds are composed around this call, so a
    fault escaping here would take the workspace's whole session with it over one slot's unreadable
    row. `error_class` carries which fault it was."""
    stored = await store.stored_slots(workspace_id)
    binds: list[Bind] = []
    for slot in await slots.all(workspace_id):
        target = slot.injection
        if target is None or slot.name not in stored:
            continue
        try:
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
        binds.append(Bind(host=host, header=target.header, secret=slot.name, env=target.env))
    return tuple(binds)


def derive_grant_hosts(
    grants: tuple[Grant, ...],
    transfer_hosts: "ConnectorTransferHosts",
    member_id: UUID | None,
) -> tuple[HostEntry, ...]:
    """Each grant the acting member may use admits its provider's own host plus the broker
    file-store hosts `transfer_hosts` resolves for it, where the sandbox fetches a tool's presigned
    file outputs and stages its file inputs. A brokered grant has no provider host of its own, so
    only its transfer hosts are admitted. A private grant admits for its owner alone; a shared one
    for every member."""
    return policy_hosts(
        *(
            host
            for grant in grants
            if grant.connection_shared or grant.owner_member_id == member_id
            for host in (grant.host, *transfer_hosts.of(grant.provider))
        )
    )


def derive_cli_binds(
    grants: tuple[Grant, ...], clis: Mapping[str, CliCredential], member_id: UUID | None
) -> tuple[Bind, ...]:
    """Each connector whose CLI credential the acting member's tier resolves to exactly one account
    binds that account's connection on the grant's host and, for a CLI that clones, on its git
    host, both under the CLI's one env. The tier is the member's own accounts, else the shared ones
    (`cli_accounts`); a tier holding several is ambiguous and binds nothing, as the sandbox's own
    environment refuses to choose."""
    binds: list[Bind] = []
    for provider, cli in clis.items():
        accounts = cli_accounts(grants, provider, member_id)
        if len(accounts) != 1:
            continue
        grant = next(
            grant
            for grant in grants
            if grant.provider == provider and grant.account_id == accounts[0]
        )
        secret = f"{CONNECTION_SECRET_PREFIX}{grant.connection_id}"
        binds.append(Bind(host=grant.host, header=cli.header, secret=secret, env=cli.env))
        if cli.git is not None:
            binds.append(Bind(host=cli.git.host, header=cli.header, secret=secret, env=cli.env))
    return tuple(binds)


@dataclass(frozen=True)
class ConnectorTransferHosts:
    """The broker file-store hosts a grant admits, derived live from the deploy's manifests so a
    broker-side store move never strands a persisted copy. `explicit` maps every registered
    connector's provider to its declared hosts (empty for one that declares none), so `default` —
    the open connector namespace's hosts — reaches only a provider no connector registered. `of`
    is the one lookup the grant derivation makes."""

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
