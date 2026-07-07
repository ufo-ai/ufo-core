"""The auth-proxy seam: how a feed-sync connector source reaches a provider's credential.

A feed-sync source runs host-side in the jobs role and pulls a provider's records into memory
pages. It never holds the secret at the wire the way an agent tool call is proxied — instead it asks
an `AuthProxy` for a `Credential`, a value object that carries exactly one way to authenticate the
provider HTTP the connector issues, keeping the connector backend-agnostic about where the secret
lives:

  - `transport` — a request rewriter (a broker's proxy-execute) that injects the credential
    server-side, so the process never sees the token (Composio's model);
  - `bearer` — a member-added API key read from the credential store host-side, sent as
    `Authorization: Bearer <key>` (the BYOK/direct model);
  - `headers` — provider-specific auth headers (api-key, basic) for a provider whose auth is not a
    bearer token.

The secret behind `bearer`/`headers` lives encrypted in the credential store and is read in-process
by the sync job under a workspace-scoped `CredentialAccess` — it NEVER reaches the sandbox or the
agent surface, the actual invariant (like the BYOK model/embed backends). Keep a `Credential` out of
any structured log. A deploy selects one backend by name (`config.connectors.auth_backend`); an
extension registers a backend through the `auth_proxies` Manifest point, and `serve` builds the
selected one once at boot and threads it onto the sync runner's `SourceAuth`."""

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
