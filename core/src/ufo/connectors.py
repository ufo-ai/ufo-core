"""The two connector seams: the auth proxy a feed-sync source resolves a credential through, and
the broker a dynamic connector tool executes through.

A feed-sync source runs host-side in the jobs role and pulls a provider's records into memory
pages. It never holds the secret at the wire the way an agent tool call is proxied — instead it asks
an `AuthProxy` for a `Credential`, a value object that carries exactly one way to authenticate the
provider HTTP the connector issues, keeping the connector backend-agnostic about where the secret
lives:

  - `transport` — a request rewriter (a broker's proxy-execute) that injects the credential
    server-side, so the process never sees the token (the Composio/Pipedream model);
  - `bearer` — a member-added API key read from the credential store host-side, sent as
    `Authorization: Bearer <key>` (the BYOK/direct model);
  - `headers` — provider-specific auth headers (api-key, basic) for a provider whose auth is not a
    bearer token.

A provider whose API address carries the connected account's own identifier (QuickBooks' company id
in `/v3/company/<realmId>`) reads it through the same seam: an auth proxy implementing
`AccountParameters` answers the connector's declared `account_url_key` off the connection's
metadata, so the address follows the account the member connected instead of being typed a second
time.

The secret behind `bearer`/`headers` lives encrypted in the credential store and is read in-process
by the sync job under a workspace-scoped `CredentialAccess` — it NEVER reaches the sandbox or the
agent surface, the actual invariant (like the BYOK model/embed backends). Keep a `Credential` out of
any structured log.

A `ConnectorBroker` is the server-side surface behind a brokered provider: it catalogs the
provider's real tools, executes one with the granted account (the broker holds the token and
injects it itself), and resolves the feed-sync `Credential` for that provider's accounts. A broker
extension (Composio, Pipedream) declares one per provider through the `connectors` Manifest point;
`serve` merges every declaration into the one `ConnectorRegistry`, threads it onto the turn's
ToolContext for the dynamic connector tools, and hands its connection-bound credential resolver to
the sync runner. A source holding `DIRECT_ACCOUNT` (the member set a key, not a connection) reaches
the deploy-selected fallback backend (`[connectors] auth_backend`, the `auth_proxies` Manifest
point), so one deploy brokers gmail through one broker and github through another while keyed
providers sync through `direct`."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from uuid import UUID

import httpx
import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.workspace import ws


@dataclass(frozen=True, repr=False)
class Credential:
    """One way to authenticate a provider request, produced by an `AuthProxy` for a connector to
    build its HTTP client from. Exactly one path is populated: a `transport` that rewrites the
    request through a broker (the secret stays server-side), a `bearer` token, or auth `headers`.
    A value object — never persisted, never logged, never leaves the process."""

    transport: httpx.AsyncBaseTransport | None = None
    bearer: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        """Redact the secret — an accidental log line or exception-with-locals must never leak the
        token behind `bearer`/`headers`; the shape is enough to debug with."""
        if self.transport is not None:
            return "Credential(<transport: redacted>)"
        if self.bearer is not None:
            return "Credential(<bearer: redacted>)"
        if self.headers:
            return "Credential(<headers: redacted>)"
        return "Credential(<empty>)"


DIRECT_ACCOUNT = "default"
"""The account handle a feed-sync source carries when it authenticates with the workspace's own
provider key instead of a broker connection. A source registers with it when the member set the
provider's credential rather than connecting an account, so the run replays the decision
registration made. It has to be the handle that carries it: the provider name alone cannot tell a
keyed source from a connected one."""


class AuthProxy(Protocol):
    """Resolves the `Credential` a feed-sync source authenticates a provider with, given the
    workspace the sync runs for, the connector's provider name, and the source's account handle. A
    broker-backed proxy returns a `transport` (the secret never leaves the broker); a BYOK/direct
    proxy reads a member-added key from the credential store host-side and returns a `bearer` (or
    auth `headers`). Core never mints or holds a provider token itself — it selects one proxy
    backend at boot and threads it onto the sync runner."""

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential: ...


@runtime_checkable
class AccountParameters(Protocol):
    """Reads one named identifier off a connected account's connection metadata — how a connector
    whose API address carries a per-account id (QuickBooks' company/realm id in
    `/v3/company/<realmId>`) resolves it from the account the member connected, instead of the
    member typing what the broker already holds. `key` is the connector's declared
    `account_url_key` and names a provider-issued identifier, never a secret: its value reaches the
    request URL.

    A capability, not a requirement: an auth proxy or broker without it answers nothing (a BYOK key
    carries no connection metadata), and such a source must carry a `base_url` of its own or fail
    its run."""

    async def account_parameter(
        self, workspace_id: UUID, provider: str, account: str, key: str
    ) -> str | None: ...


class UnknownBrokerTool(LookupError):
    """The broker catalogs no tool under this slug for the provider — `describe` folds the name
    into its `unresolved` answer instead of failing the call."""


def stale_grant_guidance(provider: str) -> str:
    """The suffix a broker appends when a failure names an account it does not hold — a grant made
    through a previous broker, or a broker key/org rotation. The agent cannot repair that by
    retrying, so the error says what can: ask the member to reconnect."""
    return (
        f"the {provider!r} grant references an account this broker does not recognize (it likely "
        "predates the current broker); ask the member to reconnect with connect_account"
    )


@dataclass(frozen=True)
class BrokerTool:
    """One provider tool as its broker catalogs it: the executable slug, a short description, and
    — once described — the input schema the agent builds arguments against (a raw JSON schema,
    freeform by nature)."""

    slug: str
    description: str = ""
    input_schema: Mapping[str, object] = field(default_factory=dict)


WORKSPACE_FILE_KEY = "workspace_file"
"""The one file-argument vocabulary across the connector seam: a broker whose tools take file
inputs rewrites each file parameter's schema to an object holding this key (a `/workspace` path),
and the dynamic connector tools stage exactly the argument values carrying it."""


@dataclass(frozen=True)
class BrokerFile:
    """One file a connector tool produced, as its broker surfaces it: the download filename and the
    short-lived presigned URL on the broker's file store. The sandbox fetches the bytes from `url`
    itself, through the egress proxy — they never cross the serve process."""

    name: str
    url: str


@dataclass(frozen=True)
class StagedUpload:
    """Where the sandbox PUTs a workspace file so a broker tool can read it: the presigned
    `put_url` on the broker's file store, the `content_type` the PUT must carry, and the
    `argument` value that names the staged object in the tool call. The broker mints only these
    references — the bytes go sandbox → broker store directly. `put_url` is None when the store
    already holds the bytes (a content-addressed dedup hit): the argument names the existing
    object and the sandbox skips the PUT."""

    put_url: str | None
    content_type: str
    argument: dict[str, object]


@dataclass(frozen=True)
class BrokerSearch:
    """A semantic tool-search answer: the matching tools (schemas included) plus whatever execution
    plan, guidance, and pitfalls the broker's router surfaces — empty for a broker without one."""

    tools: tuple[BrokerTool, ...]
    plan: tuple[str, ...] = ()
    guidance: tuple[str, ...] = ()
    pitfalls: tuple[str, ...] = ()


class ConnectorBroker(Protocol):
    """A brokered provider's server-side surface, implemented by the broker extension that declares
    the provider and consumed through the `ConnectorRegistry`: `tools` and `schema` are the catalog
    the dynamic connector tools search and describe (`schema` raises `UnknownBrokerTool` for a slug
    the provider does not have); `execute` runs one tool against the granted account on the broker's
    execute API — the broker holds the account's token and injects it itself, so no secret crosses
    this seam; `search` is semantic discovery; `credential` resolves the provider `Credential` a
    feed-sync source authenticates with, confirming the account belongs to the workspace first.

    Files cross the seam as references, never bytes: `file_outputs` projects an execute response's
    produced files to their presigned URLs, and `stage_upload` mints where a workspace file is PUT
    before a tool call consumes it (raising ValueError for a broker whose tools take URL inputs
    instead). The sandbox runs both transfers itself, through the egress proxy — the declared
    `transfer_hosts` a grant admits.

    A broker that also holds a connection's provider-issued identifiers implements
    `AccountParameters`, which is what resolves the per-account API address of a provider whose
    host carries one."""

    async def tools(
        self, workspace_id: UUID, provider: str, query: str
    ) -> tuple[BrokerTool, ...]: ...

    async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool: ...

    async def execute(
        self,
        workspace_id: UUID,
        provider: str,
        slug: str,
        arguments: Mapping[str, object],
        account_id: str,
        idempotency_key: str | None,
    ) -> dict[str, object]: ...

    def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]: ...

    async def stage_upload(
        self,
        workspace_id: UUID,
        provider: str,
        slug: str,
        filename: str,
        mimetype: str,
        md5: str,
    ) -> StagedUpload: ...

    async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch: ...

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential: ...


@dataclass(frozen=True)
class ForwardedResponse:
    """What a broker answered for one forwarded provider request: the provider's status, headers,
    and body, reconstructed for the egress proxy to write back to the sandbox verbatim."""

    status: int
    headers: Mapping[str, str]
    body: bytes


class RequestForwarder(Protocol):
    """Executes one provider HTTP request through the broker under a granted account — the broker
    injects the account's credential server-side, so no token ever reaches this deploy or the wire
    the sandbox sees. The egress proxy calls this for a MITM'd request whose auth header carries
    the grant's sentinel."""

    async def forward(
        self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes
    ) -> ForwardedResponse: ...


@dataclass(frozen=True)
class CliCredential:
    """A provider CLI authenticated at the egress proxy, declared by the connector that knows the
    provider: `env` is the variable the sandbox exports with the grant's sentinel so the CLI sends
    it as ordinary auth, `header` is the request header that carries it on the wire, and `forward`
    executes the matched request through the broker under the granted account."""

    env: str
    header: str
    forward: RequestForwarder


@dataclass(frozen=True)
class ConnectorEntry:
    """One installed connector as the registry holds it: the provider name that keys it, the
    member-facing label the discovery tool lists, and the broker that serves it."""

    provider: str
    label: str
    broker: ConnectorBroker


@dataclass(frozen=True)
class CatalogEntry:
    """One connectable service the discovery tool surfaces: the provider slug a member connects and
    the broker's own label for it. A resolver answers these live from its broker's service catalog,
    so the open set the tool lists is never bounded by the explicitly registered connectors."""

    provider: str
    label: str


class ConnectorResolver(Protocol):
    """The registry half of an open connector namespace: how a broker extension serves any provider
    slug it brokers without registering each as an explicit `ConnectorProvider`. `claims` answers
    whether the broker's live catalog serves a slug (I/O), so a caller choosing between the
    namespace and a workspace credential asks instead of assuming the namespace is a catch-all;
    `entry` builds the routing entry for a claimed slug (pure — the broker is shared and the label
    is cosmetic, since the real label rides `catalog`); `catalog` searches the broker's live service
    catalog so the discovery tool surfaces connectable services the closed registry never
    enumerated; `transfer_hosts` are the broker's file-store hosts every grant in the namespace
    additionally admits at the egress proxy."""

    @property
    def transfer_hosts(self) -> tuple[str, ...]: ...

    async def claims(self, provider: str) -> bool: ...

    def entry(self, provider: str) -> ConnectorEntry: ...

    async def catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]: ...


@dataclass(frozen=True)
class ConnectorRegistry:
    """Every installed connector keyed by provider, an optional open `resolver` for any other slug
    its broker brokers, and the deploy-selected fallback auth backend — the one routing object
    `serve` builds from the manifests' `connectors` points. The dynamic connector tools read
    `entries` and `search_catalog` to list providers and dispatch through `entry` to the owning
    broker (`entry` resolves an unregistered slug through the resolver, else fails loud); the sync
    source runner reaches its broker routing only through `SourceCredentialResolver`, which binds
    each request to the source's exact member-owned connection generation."""

    entries: Mapping[str, ConnectorEntry]
    resolver: ConnectorResolver | None = None
    fallback: AuthProxy | None = None

    def entry(self, provider: str) -> ConnectorEntry:
        found = self.entries.get(provider)
        if found is not None:
            return found
        if self.resolver is not None:
            return self.resolver.entry(provider)
        raise KeyError(f"no installed connector registers provider {provider!r}")

    async def search_catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]:
        """The open catalog the discovery tool appends to its registered connectors: the resolver's
        live service search, empty when no open namespace is installed."""
        if self.resolver is None:
            return ()
        return await self.resolver.catalog(query, limit)


def _broker(registry: ConnectorRegistry, provider: str) -> ConnectorBroker | None:
    found = registry.entries.get(provider)
    if found is not None:
        return found.broker
    if registry.resolver is not None:
        return registry.resolver.entry(provider).broker
    return None


async def _credential(
    registry: ConnectorRegistry,
    workspace_id: UUID,
    provider: str,
    account: str,
) -> Credential:
    if account != DIRECT_ACCOUNT:
        broker = _broker(registry, provider)
        if broker is not None:
            return await broker.credential(workspace_id, provider, account)
        raise RuntimeError(f"no connector broker resolves {provider!r} credentials")
    if registry.fallback is not None:
        return await registry.fallback.credential(workspace_id, provider, account)
    raise RuntimeError(f"no [connectors] auth_backend resolves {provider!r} credentials")


async def _require_source_connection(
    workspace_id: UUID,
    connection_id: UUID,
    owner_member_id: UUID,
    provider: str,
    account: str,
) -> None:
    with ws(workspace_id):
        async with workspace_tx() as connection:
            authorized = (
                await connection.execute(
                    sa.select(tables.connection.c.id).where(
                        tables.connection.c.workspace_id == workspace_id,
                        tables.connection.c.id == connection_id,
                        tables.connection.c.owner_member_id == owner_member_id,
                        tables.connection.c.provider == provider,
                        tables.connection.c.account_id == account,
                    )
                )
            ).scalar_one_or_none()
    if authorized is None:
        raise ValueError(
            f"the {provider!r} connection for account {account!r} "
            "is no longer active for this source"
        )


@dataclass(frozen=True)
class _ConnectionTransport(httpx.AsyncBaseTransport):
    inner: httpx.AsyncBaseTransport
    workspace_id: UUID
    connection_id: UUID
    owner_member_id: UUID
    provider: str
    account: str

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await _require_source_connection(
            self.workspace_id,
            self.connection_id,
            self.owner_member_id,
            self.provider,
            self.account,
        )
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


@dataclass(frozen=True)
class _BoundSourceCredentials:
    registry: ConnectorRegistry
    connection_id: UUID | None
    owner_member_id: UUID | None

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        if account == DIRECT_ACCOUNT:
            if self.connection_id is not None:
                raise ValueError("a connection-bound source cannot use direct credentials")
            return await _credential(self.registry, workspace_id, provider, account)
        if self.connection_id is None or self.owner_member_id is None:
            raise ValueError(
                f"source has no member-owned {provider!r} connection for account {account!r}"
            )
        await _require_source_connection(
            workspace_id,
            self.connection_id,
            self.owner_member_id,
            provider,
            account,
        )
        credential = await _credential(self.registry, workspace_id, provider, account)
        if credential.transport is None:
            raise RuntimeError(
                f"brokered {provider!r} source credentials did not provide a proxy transport"
            )
        return Credential(
            transport=_ConnectionTransport(
                inner=credential.transport,
                workspace_id=workspace_id,
                connection_id=self.connection_id,
                owner_member_id=self.owner_member_id,
                provider=provider,
                account=account,
            )
        )

    async def account_parameter(
        self, workspace_id: UUID, provider: str, account: str, key: str
    ) -> str | None:
        """The connection's value for one provider-issued identifier, from the broker that holds the
        account — None where the source spends a workspace key or its broker holds no such
        metadata. Read after `credential` bound the source's connection, so the account is already
        confirmed as this source's own."""
        if account == DIRECT_ACCOUNT:
            return None
        broker = _broker(self.registry, provider)
        if not isinstance(broker, AccountParameters):
            return None
        return await broker.account_parameter(workspace_id, provider, account, key)


@dataclass(frozen=True)
class SourceCredentialResolver:
    registry: ConnectorRegistry

    def bind(self, connection_id: UUID | None, owner_member_id: UUID | None) -> AuthProxy:
        return _BoundSourceCredentials(
            registry=self.registry,
            connection_id=connection_id,
            owner_member_id=owner_member_id,
        )
