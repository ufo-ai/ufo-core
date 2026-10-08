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
the sync runner. A source hanging off a connection with no account handle — the workspace's own,
where the member set a provider key rather than connecting an account — reaches the deploy-selected
fallback backend (`[connectors] auth_backend`, the `auth_proxies` Manifest point), so one deploy
brokers gmail through one broker and github through another while keyed providers sync through
`direct`."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

import httpx
import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.runtime.turns.subjects import MEMBER_SUBJECT_PREFIX
from ufo.runtime.workspace import ws
from ufo.schema import tables

ToolExecutor = Callable[[str, Mapping[str, Any]], Awaitable[Mapping[str, Any]]]
"""Run one of the provider's broker tools by slug and return its response payload.

The broker holds the account's token and injects it itself, so this seam carries arguments and
results only. A provider that publishes no REST host reads its records through this rather than
over HTTP."""


@dataclass(frozen=True, repr=False)
class Credential:
    """One way to authenticate a provider request, produced by an `AuthProxy` for a connector to
    build its HTTP client from. Exactly one HTTP path is populated: a `transport` that rewrites the
    request through a broker (the secret stays server-side), a `bearer` token, or auth `headers`.

    `execute` is the second seam, not a fourth HTTP path: a broker that runs the provider's tools
    server-side offers it beside its `transport`, and a connector whose provider publishes no REST
    host (an MCP-only service) reads its records through it. A key read from the credential store
    carries none — a member's API key cannot execute a broker's tool.

    A value object — never persisted, never logged, never leaves the process."""

    transport: httpx.AsyncBaseTransport | None = None
    bearer: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    execute: ToolExecutor | None = None

    def __repr__(self) -> str:
        """Redact the secret — an accidental log line or exception-with-locals must never leak the
        token behind `bearer`/`headers`; the shape is enough to debug with."""
        if self.transport is not None:
            return "Credential(<transport: redacted>)"
        if self.execute is not None:
            return "Credential(<execute: redacted>)"
        if self.bearer is not None:
            return "Credential(<bearer: redacted>)"
        if self.headers:
            return "Credential(<headers: redacted>)"
        return "Credential(<empty>)"


FEED_HANDLE_PREFIX = "{"


def brokered_account(account_id: str) -> str | None:
    """The broker's connected-account id this connection authenticates as, or None where it holds
    none and the workspace's own provider key answers instead.

    Three handles name no broker account. The workspace's keyed feed carries an empty handle; a
    member keeping such a feed private carries their member atom instead, so two members' private
    feeds over one workspace key stay distinct rows under `connection_identity`; and a feed no key
    backs — a repository, a folder root — carries its config's identity JSON (`feed_handle`), which
    begins `{` as no broker's account id does. All three route to the key rather than to a broker
    holding no account for them."""
    if (
        not account_id
        or account_id.startswith(MEMBER_SUBJECT_PREFIX)
        or account_id.startswith(FEED_HANDLE_PREFIX)
    ):
        return None
    return account_id


class AuthProxy(Protocol):
    """Resolves the `Credential` a feed-sync source authenticates a provider with, given the
    workspace the sync runs for and the connector's provider name. Which account it authenticates
    as is the bound connection's to say, never the caller's. A broker-backed proxy returns a
    `transport` (the secret never leaves the broker); a BYOK/direct proxy reads a member-added key
    from the credential store host-side and returns a `bearer` (or auth `headers`). Core never mints
    or holds a provider token itself — it selects one proxy backend at boot and threads it onto the
    sync runner."""

    async def credential(self, workspace_id: UUID, provider: str) -> Credential: ...


class UnknownBrokerTool(LookupError):
    """The broker catalogs no tool under this slug for the provider — `describe` folds the name
    into its `unresolved` answer instead of failing the call."""


class GrantUnusable(RuntimeError):
    """A broker will not authenticate an account until the member reconnects it: the grant is
    revoked, expired, unhealthy at the broker, or unknown to it.

    Separate from a broker fault because there is nothing to wait for. A fault may be the provider
    having a bad minute, so a caller retries it and escalates if it persists; this cannot resolve
    without the member, so a caller that retried every interval would spend a request per interval
    and page an operator who can do nothing. The message names the reconnect, because whoever reads
    it is the one who has to ask for it. `sources.backend` turns it into a `StreamSkipped`, which
    parks the feed and records it as a warning rather than an alert.

    `awaits_grant` says a member reconnecting is the ONLY repair, which is a narrower claim than the
    message makes and is why it defaults to False. A broker reporting one account unhealthy earns
    it: that state belongs to the account, and the reconnect that ends it raises an event the driver
    already receives, so the feed can stop polling until then. A broker that does not recognise the
    grant does not earn it, however much the guidance says to reconnect — one broker key or org
    rotation makes every account unknown at once, and an operator restoring that configuration
    raises no event at all. A feed held for the grant through that would wait for a member who has
    nothing to fix, so it keeps the ordinary park and finds its own way back."""

    def __init__(self, reason: str, *, awaits_grant: bool = False) -> None:
        super().__init__(reason)
        self.awaits_grant = awaits_grant


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
    read_only: bool = False


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
    `transfer_hosts` a grant admits."""

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


class GrantSecret(Protocol):
    """The provider token behind one connected account, read from the broker that holds it — the
    one broker act that hands a secret to this deploy, so it exists only for a connector whose
    consent rode the deploy's own OAuth client. The egress proxy swaps it onto the wire for the
    grant's sentinel; the sandbox never sees it. Confirms the account belongs to `workspace_id`
    before answering, and raises for an account it cannot authenticate."""

    async def secret(self, workspace_id: UUID, account_id: str) -> str: ...


@dataclass(frozen=True)
class GitWire:
    """The git host a CLI credential's token also authenticates, as smart-HTTP wants it: `host` is
    the clone and push origin, `helper` the git credential helper the sandbox is configured with
    for that host — the one that answers git's prompt from the CLI's env var, so a plain `git clone`
    authenticates exactly as the CLI's own clone does — and `basic_user` the username the git cache
    daemon fetches with beside the token, where a clone rides the cache instead of the wire."""

    host: str
    basic_user: str
    helper: str


@dataclass(frozen=True)
class CliCredential:
    """A provider CLI authenticated at the egress proxy, declared by the connector that knows the
    provider: `env` is the variable the sandbox exports with the grant's sentinel so the CLI sends
    it as ordinary auth, `header` is the request header that carries it on the wire, `secret`
    reads the granted account's real token for the proxy to swap in, and `git` names the git host
    the same token clones and pushes through."""

    env: str
    header: str
    secret: GrantSecret
    git: GitWire | None = None


@dataclass(frozen=True)
class ConnectorEntry:
    """One installed connector as the registry holds it: the provider name that keys it, the
    member-facing label the discovery tool lists, the broker that serves it, and `broker_name`, the
    extension that registers that broker."""

    provider: str
    label: str
    broker: ConnectorBroker
    broker_name: str | None = None


@dataclass(frozen=True)
class CatalogEntry:
    """One connectable service the discovery tool surfaces: the provider slug a member connects and
    the broker's own label for it. A resolver answers these live from its broker's service catalog,
    so the open set the tool lists is never bounded by the explicitly registered connectors."""

    provider: str
    label: str


@dataclass(frozen=True)
class CatalogPage:
    """One page of connectable services and the broker cursor that continues it."""

    entries: tuple[CatalogEntry, ...]
    after: str | None


class ConnectorResolver(Protocol):
    """The registry half of an open connector namespace: how a broker extension serves any provider
    slug it brokers without registering each as an explicit `ConnectorProvider`. `claims` answers
    whether the broker's live catalog serves a slug (I/O), so a caller choosing between the
    namespace and a workspace credential asks instead of assuming the namespace is a catch-all;
    `entry` builds the routing entry for a claimed slug (pure — the broker is shared and the label
    is cosmetic, since the real label rides `catalog`); `catalog` pages through the broker's live
    service catalog so the discovery tool surfaces connectable services the closed registry never
    enumerated; `transfer_hosts` are the broker's file-store hosts every grant in the namespace
    additionally admits at the egress proxy."""

    @property
    def transfer_hosts(self) -> tuple[str, ...]: ...

    async def claims(self, provider: str) -> bool: ...

    def entry(self, provider: str) -> ConnectorEntry: ...

    async def catalog(self, query: str, limit: int, after: str | None) -> CatalogPage: ...


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
        return (await self.resolver.catalog(query, limit, None)).entries

    async def catalog(self, query: str, limit: int, after: str | None) -> CatalogPage:
        """Read one page of explicit providers and the open broker namespace."""
        matched = (
            [
                CatalogEntry(provider=provider, label=entry.label)
                for provider, entry in sorted(self.entries.items())
                if query.lower() in f"{provider} {entry.label}".lower()
            ]
            if after is None
            else []
        )
        page = (
            await self.resolver.catalog(query, limit, after)
            if self.resolver is not None
            else CatalogPage(entries=(), after=None)
        )
        matched.extend(page.entries)
        deduped: dict[str, CatalogEntry] = {}
        for entry in matched:
            deduped.setdefault(entry.provider, entry)
        return CatalogPage(entries=tuple(deduped.values()), after=page.after)


def _broker(registry: ConnectorRegistry, provider: str) -> ConnectorBroker | None:
    found = registry.entries.get(provider)
    if found is not None:
        return found.broker
    if registry.resolver is not None:
        return registry.resolver.entry(provider).broker
    return None


async def _live_account(workspace_id: UUID, connection_id: UUID, provider: str) -> str:
    with ws(workspace_id):
        async with workspace_tx() as connection:
            account = (
                await connection.execute(
                    sa.select(tables.connection.c.account_id).where(
                        tables.connection.c.workspace_id == workspace_id,
                        tables.connection.c.id == connection_id,
                        tables.connection.c.provider == provider,
                    )
                )
            ).scalar_one_or_none()
    if account is None:
        raise ValueError(
            f"the {provider!r} connection {connection_id} is no longer active for this source"
        )
    return account


@dataclass(frozen=True)
class _ConnectionTransport(httpx.AsyncBaseTransport):
    inner: httpx.AsyncBaseTransport
    workspace_id: UUID
    connection_id: UUID
    provider: str

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await _live_account(self.workspace_id, self.connection_id, self.provider)
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


@dataclass(frozen=True)
class _ConnectionExecutor:
    inner: ToolExecutor
    workspace_id: UUID
    connection_id: UUID
    provider: str

    async def __call__(self, slug: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        await _live_account(self.workspace_id, self.connection_id, self.provider)
        return await self.inner(slug, arguments)


@dataclass(frozen=True)
class _BoundSourceCredentials:
    registry: ConnectorRegistry
    connection_id: UUID

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        account = brokered_account(await _live_account(workspace_id, self.connection_id, provider))
        if account is None:
            if self.registry.fallback is None:
                raise RuntimeError(
                    f"no [connectors] auth_backend resolves {provider!r} credentials"
                )
            return await self.registry.fallback.credential(workspace_id, provider)
        broker = _broker(self.registry, provider)
        if broker is None:
            raise RuntimeError(f"no connector broker resolves {provider!r} credentials")
        credential = await broker.credential(workspace_id, provider, account)
        if credential.transport is None and credential.execute is None:
            raise RuntimeError(
                f"brokered {provider!r} source credentials did not provide a proxy transport or a "
                "tool executor"
            )
        return Credential(
            transport=None
            if credential.transport is None
            else _ConnectionTransport(
                inner=credential.transport,
                workspace_id=workspace_id,
                connection_id=self.connection_id,
                provider=provider,
            ),
            execute=None
            if credential.execute is None
            else _ConnectionExecutor(
                inner=credential.execute,
                workspace_id=workspace_id,
                connection_id=self.connection_id,
                provider=provider,
            ),
        )


@dataclass(frozen=True)
class SourceCredentialResolver:
    registry: ConnectorRegistry

    def bind(self, connection_id: UUID) -> AuthProxy:
        return _BoundSourceCredentials(registry=self.registry, connection_id=connection_id)
