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
ToolContext for the dynamic connector tools, and hands it to the sync runner as its auth proxy —
`credential` routes a source holding a broker grant to that provider's broker and a source holding
`DIRECT_ACCOUNT` (the member set a key, not a grant) to the deploy-selected fallback backend
(`[connectors] auth_backend`, the `auth_proxies` Manifest point), so one deploy brokers gmail
through one broker and github through another while keyed providers sync through `direct`."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

import httpx


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
provider key instead of a broker grant — the routing signal `ConnectorRegistry.credential` reads to
reach the fallback backend. A source registers with it when the member set the provider's credential
rather than connecting an account, so the run replays the decision registration made. It has to be
the handle that carries it: a broker's open namespace claims every slug, so the provider name alone
cannot tell a keyed source from a granted one."""


class AuthProxy(Protocol):
    """Resolves the `Credential` a feed-sync source authenticates a provider with, given the
    workspace the sync runs for, the connector's provider name, and the source's account handle. A
    broker-backed proxy returns a `transport` (the secret never leaves the broker); a BYOK/direct
    proxy reads a member-added key from the credential store host-side and returns a `bearer` (or
    auth `headers`). Core never mints or holds a provider token itself — it selects one proxy
    backend at boot and threads it onto the sync runner."""

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential: ...


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
    slug it brokers without registering each as an explicit `ConnectorProvider`. `entry` builds the
    routing entry for a claimed slug (pure — the broker is shared and the label is cosmetic, since
    the real label rides `catalog`); `catalog` searches the broker's live service catalog so the
    discovery tool surfaces connectable services the closed registry never enumerated;
    `transfer_hosts` are the broker's file-store hosts every grant in the namespace additionally
    admits at the egress proxy. A namespace is the catch-all — it claims any slug, so its broker's
    own calls fail loud on a slug the broker cannot serve."""

    @property
    def transfer_hosts(self) -> tuple[str, ...]: ...

    def entry(self, provider: str) -> ConnectorEntry: ...

    async def catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]: ...


@dataclass(frozen=True)
class ConnectorRegistry:
    """Every installed connector keyed by provider, an optional open `resolver` for any other slug
    its broker brokers, and the deploy-selected fallback auth backend — the one routing object
    `serve` builds from the manifests' `connectors` points. The dynamic connector tools read
    `entries` and `search_catalog` to list providers and dispatch through `entry` to the owning
    broker (`entry` resolves an unregistered slug through the resolver, else fails loud); the sync
    runner uses the registry as its auth proxy — `credential` routes on the source's account handle
    first, since a `DIRECT_ACCOUNT` source has no grant for any broker to resolve: it goes to the
    fallback, and a granted account goes to its provider's broker (registered, else the resolver's),
    falling back for a provider no installed broker claims."""

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

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        if account != DIRECT_ACCOUNT:
            found = self.entries.get(provider)
            if found is not None:
                return await found.broker.credential(workspace_id, provider, account)
            if self.resolver is not None:
                return await self.resolver.entry(provider).broker.credential(
                    workspace_id, provider, account
                )
        if self.fallback is not None:
            return await self.fallback.credential(workspace_id, provider, account)
        raise RuntimeError(
            f"no connector broker or [connectors] auth_backend resolves {provider!r} credentials"
        )
