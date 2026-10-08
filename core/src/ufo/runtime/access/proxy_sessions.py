"""The client of the proxy service's session API.

A session is the authority one sandbox, terminal, or probe egresses under: the policy core compiled
by name, the token the proxy service signed for it, and the env a client exports in place of every
secret. Core calls as the workspace, presenting the bearer the extension declaring
`proxy_credentials` answers for it. A deploy with no such extension, an unreachable service, and a
5xx raise `SandboxProviderUnavailable`, which parks a turn; a 4xx and a replay of a revoked session
raise `ProxyRefused`."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ufo.harness.sandbox.session import SandboxProviderUnavailable
from ufo.runtime.access.egress_rules import SessionPolicy
from ufo.runtime.ext.manifest import ProxyCredentials
from ufo.runtime.workspace import ws

PROXY_CALL_TIMEOUT_SECONDS = 10.0
PROXY_SESSION_TTL_SECONDS = 3600
PROXY_SESSION_MAX_TTL_SECONDS = 86400
PROXY_SESSION_RENEW_BELOW_SECONDS = 1800
PROXY_SESSION_PAGE_LIMIT = 200
PROXY_SESSION_CREATED_VERSION = 1
IDEMPOTENCY_HEADER = "Idempotency-Key"
SESSIONS_PATH = "/v1/sessions"
SESSION_REVOKED_CODE = "session_revoked"
SESSION_EXPIRED_CODE = "conflict"
CONVERSATION_LABEL = "conversation"
AGENT_LABEL = "agent"
MEMBER_LABEL = "member"
PROBE_LABEL = "probe"


class CreateSession(BaseModel):
    """The body of a session create."""

    model_config = ConfigDict(extra="forbid")
    ttl_s: int = Field(ge=1, le=PROXY_SESSION_MAX_TTL_SECONDS)
    labels: dict[str, str]
    policy: SessionPolicy


class UpdateSession(BaseModel):
    """The body of a session patch: the policy that replaces the session's."""

    model_config = ConfigDict(extra="forbid")
    policy: SessionPolicy


class Renew(BaseModel):
    """The body of a session renew."""

    model_config = ConfigDict(extra="forbid")
    ttl_s: int = Field(ge=1, le=PROXY_SESSION_MAX_TTL_SECONDS)


class Budget(BaseModel):
    """A session's spend limit."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    micro_usd: int


class SessionView(BaseModel):
    """A session as every answer carries it. Its `policy` is not read: every answer withholds a
    route's header values, so a caller comparing policies keeps the one it sent."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    id: UUID
    workspace_id: UUID
    token_id: UUID
    version: int
    labels: dict[str, str]
    budget: Budget | None
    created_at: AwareDatetime
    expires_at: AwareDatetime
    revoked_at: AwareDatetime | None
    proxy_url: str


class SessionWithEnv(SessionView):
    """A session with the env a client exports: the proxy variables carrying its token, and each
    bind's sentinel under the bind's `env`."""

    env: dict[str, str]


class SessionCreated(SessionWithEnv):
    """A created session with its token and the CA a client trusts for the hosts it binds."""

    token: str
    ca_pem: str


class SessionPage(BaseModel):
    """One page of a session listing."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    items: tuple[SessionView, ...]
    next_cursor: str | None


class Renewed(BaseModel):
    """A session's new deadline."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    id: UUID
    expires_at: AwareDatetime


class ProxyError(BaseModel):
    """The proxy service's refusal: its code, its sentence, and the ceiling a
    `ceiling_exceeded` names."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    code: str
    message: str
    ceiling: str | None = None


class ProxyRefused(RuntimeError):
    """A 4xx from the proxy service: `status` and the envelope's `error`. A replayed create that
    answers a revoked session raises the `409 session_revoked` a patch or a renew of that session
    answers; the create itself answers 200, as a revoke of it does."""

    def __init__(self, status: int, error: ProxyError, summary: str | None = None) -> None:
        super().__init__(
            summary or f"the proxy service answered {status} {error.code}: {error.message}"
        )
        self.status = status
        self.error = error


@dataclass(frozen=True)
class ProxySessions:
    """The session API at `base_url`, called as one workspace at a time with the bearer
    `credentials` answers for it."""

    base_url: str
    credentials: ProxyCredentials | None
    http: httpx.AsyncClient

    async def open(
        self,
        workspace_id: UUID,
        *,
        key: str,
        labels: Mapping[str, str],
        policy: SessionPolicy,
        ttl_s: int = PROXY_SESSION_TTL_SECONDS,
    ) -> SessionCreated:
        """Create the session `key` names, or answer the live one an earlier create under `key`
        made. A replayed session whose deadline is within one call's timeout gives way to the one a
        successor key `<key>:1`, `<key>:2`, … names, the first that is live or new. A replayed
        session that was revoked, under `key` or a successor, raises `ProxyRefused` with
        `session_revoked`, so no reopen undoes a revoke. A replayed session whose deadline is near
        is renewed for `ttl_s`, and one a patch has moved since its create is sent `policy`
        again."""
        bearer = await self._bearer(workspace_id)
        body = CreateSession(ttl_s=ttl_s, labels=dict(labels), policy=policy)
        attempt, successor = key, 0
        while True:
            response = await self._send(bearer, "POST", SESSIONS_PATH, body, key=attempt)
            created = SessionCreated.model_validate_json(response.content)
            if response.status_code == httpx.codes.CREATED:
                return created
            if created.revoked_at is not None:
                revoked = ProxyError(code=SESSION_REVOKED_CODE, message="The session was revoked.")
                raise ProxyRefused(
                    httpx.codes.CONFLICT,
                    revoked,
                    f"the session under key {attempt!r} was revoked, so it is not reopened",
                )
            remaining = (created.expires_at - datetime.now(UTC)).total_seconds()
            if remaining > PROXY_CALL_TIMEOUT_SECONDS:
                break
            successor += 1
            attempt = f"{key}:{successor}"
        if remaining < PROXY_SESSION_RENEW_BELOW_SECONDS:
            renewed = await self._renew(bearer, created.id, ttl_s)
            created = created.model_copy(update={"expires_at": renewed.expires_at})
        if created.version != PROXY_SESSION_CREATED_VERSION:
            patched = await self._update(bearer, created.id, policy)
            created = created.model_copy(update=dict(patched))
        return created

    async def update(
        self, workspace_id: UUID, session_id: UUID, policy: SessionPolicy
    ) -> SessionWithEnv:
        """Replace the session's policy and answer its env: a bind `env` the session did not hold
        gets a new sentinel, and one it held keeps its own."""
        return await self._update(await self._bearer(workspace_id), session_id, policy)

    async def renew(self, workspace_id: UUID, session_id: UUID, ttl_s: int) -> Renewed:
        """Set the session's deadline `ttl_s` from now; its token stays the same."""
        return await self._renew(await self._bearer(workspace_id), session_id, ttl_s)

    async def revoke(self, workspace_id: UUID, session_id: UUID) -> None:
        """End the session; ending it again changes nothing."""
        await self._revoke(await self._bearer(workspace_id), session_id)

    async def labelled(self, workspace_id: UUID, label: str, value: str) -> list[SessionView]:
        """Every unrevoked session whose `label` holds `value`, newest first, expired ones
        included. A listing filters on one label, the most the session API takes."""
        return await self._labelled(await self._bearer(workspace_id), label, value)

    async def revoke_labelled(self, workspace_id: UUID, label: str, value: str) -> int:
        """Revoke every unrevoked session whose `label` holds `value`, expired ones included, and
        answer how many."""
        bearer = await self._bearer(workspace_id)
        live = await self._labelled(bearer, label, value)
        for session in live:
            await self._revoke(bearer, session.id)
        return len(live)

    async def _bearer(self, workspace_id: UUID) -> str:
        if self.credentials is None:
            raise SandboxProviderUnavailable(
                "No extension declares proxy_credentials, so there is no proxy bearer."
            )
        with ws(workspace_id):
            return await self.credentials.bearer()

    async def _labelled(self, bearer: str, label: str, value: str) -> list[SessionView]:
        params: dict[str, str | int] = {
            f"labels.{label}": value,
            "limit": PROXY_SESSION_PAGE_LIMIT,
        }
        live: list[SessionView] = []
        while True:
            page = SessionPage.model_validate_json(
                (await self._send(bearer, "GET", SESSIONS_PATH, params=params)).content
            )
            live.extend(session for session in page.items if session.revoked_at is None)
            if page.next_cursor is None:
                return live
            params["cursor"] = page.next_cursor

    async def _update(self, bearer: str, session_id: UUID, policy: SessionPolicy) -> SessionWithEnv:
        path = f"{SESSIONS_PATH}/{session_id}"
        response = await self._send(bearer, "PATCH", path, UpdateSession(policy=policy))
        return SessionWithEnv.model_validate_json(response.content)

    async def _renew(self, bearer: str, session_id: UUID, ttl_s: int) -> Renewed:
        path = f"{SESSIONS_PATH}/{session_id}/renew"
        response = await self._send(bearer, "POST", path, Renew(ttl_s=ttl_s))
        return Renewed.model_validate_json(response.content)

    async def _revoke(self, bearer: str, session_id: UUID) -> None:
        await self._send(bearer, "POST", f"{SESSIONS_PATH}/{session_id}/revoke")

    async def _send(
        self,
        bearer: str,
        method: str,
        path: str,
        body: BaseModel | None = None,
        *,
        key: str | None = None,
        params: Mapping[str, str | int] | None = None,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {bearer}"}
        if key is not None:
            headers[IDEMPOTENCY_HEADER] = key
        try:
            response = await self.http.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                json=None if body is None else body.model_dump(mode="json"),
                params=params,
                timeout=PROXY_CALL_TIMEOUT_SECONDS,
            )
        except httpx.TransportError as error:
            raise SandboxProviderUnavailable(type(error).__name__) from error
        if response.is_server_error:
            raise SandboxProviderUnavailable(f"the proxy service answered {response.status_code}")
        if response.is_client_error:
            refusal = ProxyError.model_validate(response.json()["error"])
            raise ProxyRefused(response.status_code, refusal)
        return response
