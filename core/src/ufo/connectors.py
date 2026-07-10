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
`credential` routes a brokered provider to its own broker and any other to the deploy-selected
fallback backend (`[connectors] auth_backend`, the `auth_proxies` Manifest point), so one deploy
brokers gmail through one broker and github through another while BYOK providers keep `direct`."""

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
    feed-sync source authenticates with, confirming the account belongs to the workspace first."""

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

    async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch: ...

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential: ...


@dataclass(frozen=True)
class ConnectorEntry:
    """One installed connector as the registry holds it: the provider name that keys it, the
    member-facing label the discovery tool lists, and the broker that serves it."""

    provider: str
    label: str
    broker: ConnectorBroker


@dataclass(frozen=True)
class ConnectorRegistry:
    """Every installed connector keyed by provider, plus the deploy-selected fallback auth backend —
    the one routing object `serve` builds from the manifests' `connectors` points. The dynamic
    connector tools read `entries` to list providers and dispatch to the owning broker (`entry`
    fails loud on a provider no extension registers); the sync runner uses the registry as its auth
    proxy — `credential` routes a brokered provider to its own broker and any other to the
    fallback."""

    entries: Mapping[str, ConnectorEntry]
    fallback: AuthProxy | None = None

    def entry(self, provider: str) -> ConnectorEntry:
        found = self.entries.get(provider)
        if found is None:
            raise KeyError(f"no installed connector registers provider {provider!r}")
        return found

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        found = self.entries.get(provider)
        if found is not None:
            return await found.broker.credential(workspace_id, provider, account)
        if self.fallback is not None:
            return await self.fallback.credential(workspace_id, provider, account)
        raise RuntimeError(
            f"no connector broker or [connectors] auth_backend resolves {provider!r} credentials"
        )
