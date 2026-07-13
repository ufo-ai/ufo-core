"""Grants: an OAuth account bound to an agent through `/connect` — a connection won in chat rather
than a BYOK value set at deploy. The proxy's egress scope is derived from the workspace's grants, so
a granted host is reachable and metered and every ungranted host is refused at CONNECT.

`ConnectFlow` runs the two-legged OAuth handoff: `authorize` opens a provider's link carrying sealed
state; `complete` verifies that state, exchanges the code for the connected account, and records the
grant. The broker holds the account's token and executes tools server-side, so no secret crosses
into the grant — only the broker's connected-account id, frozen onto the row at record time so later
derivation needs no live provider. The OAuth mechanics are an injected `OAuthProvider`, never known
to core."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from starlette.requests import Request

from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.schema.records import TerminalFrame
from ufo.workspace import ws

CONNECT_STATE_TTL_SECONDS = 600


class UnknownProvider(KeyError):
    """No OAuth provider is installed under this name — no connectors extension declares it."""


class ConnectStateInvalid(ValueError):
    """The sealed OAuth state is tampered, expired, or unreadable — the callback refuses it."""


class ConnectUnavailable(RuntimeError):
    """No connect flow is installed — the deploy set no credential key, so grants can be neither
    sealed nor recorded. The `connect_account` tool and the OAuth callback fail loud with this."""


class ConnectRequestInvalid(ValueError):
    """A private connect handoff is absent, stale, or belongs to another member."""


@dataclass(frozen=True)
class OAuthAccount:
    """What a completed handoff yields: the broker's stable connected-account id. The account's
    token stays with the broker (server-side execution), so no secret crosses into the grant."""

    account_id: str


class OAuthProvider(Protocol):
    """A connector's OAuth descriptor, injected by the extension that declares it. `authorize_url`
    builds the link the member opens; `exchange` turns the returned code into the connected account
    — its id, verified against this workspace's brokered user so a foreign account (injected on the
    return leg) is refused (the confused-deputy guard). `workspace_id` is the sealed workspace the
    code was scoped to; `state` lets a broker bind overlapping consent flows independently. `host`
    is the provider's own host the grant admits and meters at the egress proxy —
    direct-provider-host, provider-agnostic."""

    @property
    def provider(self) -> str: ...

    @property
    def host(self) -> str: ...

    def authorize_url(self, state: str, redirect_uri: str) -> str: ...

    async def exchange(
        self, code: str, redirect_uri: str, workspace_id: UUID, state: str
    ) -> OAuthAccount: ...


@dataclass(frozen=True)
class Grant:
    """A grant as the proxy-rule derivation and connector tools read it: the host it admits and
    meters, and the provider account that identifies it. The broker holds the account's token, so a
    grant carries no secret — a connector tool passes its `account_id` to the broker's server-side
    execute API, and the proxy injects nothing on the wire to `host`."""

    provider: str
    account_id: str
    host: str


@dataclass(frozen=True)
class GrantSummary:
    """The audit view of a grant for `ufoctl grants` — no secret, only who granted which provider
    account to which agent, and when."""

    agent: str
    provider: str
    account_id: str
    grantor_member_id: UUID
    conversation_id: UUID
    granted_at: datetime


@dataclass(frozen=True)
class GrantRecorded:
    """What `complete` returns once the handoff lands durably: the provider account now bound."""

    provider: str
    account_id: str
    agent_id: UUID


class ConnectState(BaseModel):
    """The state carried across the OAuth redirect: who is granting, to which agent, for which
    provider, in which conversation. Fernet-sealed into the `state` param so the callback trusts it
    without a server-side pending row, and TTL-bounded so a stale handoff is refused."""

    workspace_id: UUID
    agent_id: UUID
    provider: str
    grantor_member_id: UUID
    conversation_id: UUID


@dataclass(frozen=True)
class GrantStore:
    """Persists and reads OAuth grants. A grant binds a provider account to an agent; the broker
    holds the account's token and executes tools server-side, so nothing here is a secret — the
    grant carries only the connected-account id, the host it admits, and its audit trail."""

    async def record(
        self,
        *,
        workspace_id: UUID,
        agent_id: UUID,
        provider: str,
        account_id: str,
        host: str,
        grantor_member_id: UUID,
        conversation_id: UUID,
    ) -> None:
        """Upsert on (workspace, agent, provider, account): re-connecting the same account refreshes
        its audit fields rather than duplicating the grant. One atomic insert-on-conflict, so two
        near-simultaneous first connects of the same account settle on one row instead of colliding
        on the unique identity — the loser updates, never raises. `account_id` comes from the
        provider's OAuth exchange and a connector tool sends it to the broker, so a control
        character (CR/LF and friends) that could forge a broker request is refused here, before any
        grant it would malform is recorded."""
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in account_id):
            raise ValueError("account_id has a control character; refusing to record the grant")
        async with workspace_tx() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            await connection.execute(
                insert(tables.grant)
                .values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    provider=provider,
                    account_id=account_id,
                    host=host,
                    grantor_member_id=grantor_member_id,
                    conversation_id=conversation_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_update(
                    index_elements=[
                        tables.grant.c.workspace_id,
                        tables.grant.c.agent_id,
                        tables.grant.c.provider,
                        tables.grant.c.account_id,
                    ],
                    set_={
                        "host": host,
                        "grantor_member_id": grantor_member_id,
                        "conversation_id": conversation_id,
                        "updated_at": sa.func.now(),
                    },
                )
            )

    async def active_grants(self, workspace_id: UUID, agent_id: UUID) -> tuple[Grant, ...]:
        """One agent's grants in this workspace, in the shape the proxy-rule derivation and
        connector tools read. Agent-scoped: the per-turn resolver admits and meters only the turn
        agent's own grants, so agent A's rule set never carries agent B's host, and a tool executes
        only against A's own accounts."""
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.grant.c.provider,
                        tables.grant.c.account_id,
                        tables.grant.c.host,
                    ).where(
                        tables.grant.c.workspace_id == workspace_id,
                        tables.grant.c.agent_id == agent_id,
                    )
                )
            ).all()
        return tuple(
            Grant(provider=row.provider, account_id=row.account_id, host=row.host) for row in rows
        )


@dataclass(frozen=True)
class ConnectFlow:
    """The connect workflow, twinned across a browser redirect: `authorize` opens a provider's link
    carrying sealed state; `complete` verifies that state, exchanges the code, and records the
    grant. Deps: the providers the deploy installs (empty until a connectors extension declares
    any), the Fernet that seals the state, the grant store, and the deploy's callback `redirect_uri`
    — one value both legs use, so the token exchange presents the same redirect the link did."""

    providers: Mapping[str, OAuthProvider]
    fernet: Fernet
    store: GrantStore
    redirect_uri: str

    def authorize(
        self,
        *,
        workspace_id: UUID,
        agent_id: UUID,
        provider: str,
        grantor_member_id: UUID,
        conversation_id: UUID,
    ) -> str:
        descriptor = self._provider(provider)
        state = ConnectState(
            workspace_id=workspace_id,
            agent_id=agent_id,
            provider=provider,
            grantor_member_id=grantor_member_id,
            conversation_id=conversation_id,
        )
        sealed = self.fernet.encrypt(state.model_dump_json().encode()).decode()
        return descriptor.authorize_url(sealed, self.redirect_uri)

    def validate_provider(self, provider: str) -> None:
        self._provider(provider)

    def bridge_workspace(self, *, state: str, provider: str, callback: str) -> UUID:
        """Verify a browser bridge request and return the workspace it may run as."""
        claims = self._open(state)
        if claims.provider != provider or callback != self.redirect_uri:
            raise ConnectStateInvalid("connect bridge does not match its sealed state")
        self._provider(provider)
        return claims.workspace_id

    async def complete(self, *, state: str, code: str) -> GrantRecorded:
        claims = self._open(state)
        descriptor = self._provider(claims.provider)
        with ws(claims.workspace_id):
            account = await descriptor.exchange(code, self.redirect_uri, claims.workspace_id, state)
            await self.store.record(
                workspace_id=claims.workspace_id,
                agent_id=claims.agent_id,
                provider=descriptor.provider,
                account_id=account.account_id,
                host=descriptor.host,
                grantor_member_id=claims.grantor_member_id,
                conversation_id=claims.conversation_id,
            )
        return GrantRecorded(
            provider=descriptor.provider, account_id=account.account_id, agent_id=claims.agent_id
        )

    def _provider(self, name: str) -> OAuthProvider:
        descriptor = self.providers.get(name)
        if descriptor is None:
            raise UnknownProvider(name)
        return descriptor

    def _open(self, state: str) -> ConnectState:
        try:
            raw = self.fernet.decrypt(state.encode(), ttl=CONNECT_STATE_TTL_SECONDS)
        except InvalidToken as error:
            raise ConnectStateInvalid("connect state is tampered or expired") from error
        return ConnectState.model_validate_json(raw)


@dataclass(frozen=True)
class ConnectHandoff:
    """Memoize a terminal connect request's OAuth URL for its speaking member."""

    flow: ConnectFlow

    async def authorize(self, workspace_id: UUID, turn_id: UUID, member_id: UUID) -> str:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.agent_id,
                        tables.turn.c.conversation_id,
                        tables.turn.c.speaker_member_id,
                        tables.turn.c.connect_authorization_url,
                        tables.turn.c.connect_authorized_at,
                        tables.turn.c.terminal,
                        tables.turn.c.updated_at,
                    )
                    .where(
                        tables.turn.c.id == turn_id,
                        tables.turn.c.workspace_id == workspace_id,
                    )
                    .with_for_update()
                )
            ).one_or_none()
            if row is None or row.speaker_member_id != member_id:
                raise ConnectRequestInvalid("connect request belongs to another member")
            if row.terminal is None:
                raise ConnectRequestInvalid("connect request is no longer available")
            terminal = TerminalFrame.model_validate(row.terminal)
            request = terminal.connect_request
            if request is None:
                raise ConnectRequestInvalid("connect request is no longer available")
            try:
                self.flow.validate_provider(request.provider)
            except UnknownProvider as error:
                raise ConnectRequestInvalid("connect provider is no longer available") from error
            now = datetime.now(UTC)
            if row.connect_authorization_url is not None:
                if row.connect_authorized_at is None:
                    raise ConnectRequestInvalid("connect authorization is incomplete")
                authorized_at = (
                    row.connect_authorized_at
                    if row.connect_authorized_at.tzinfo is not None
                    else row.connect_authorized_at.replace(tzinfo=UTC)
                )
                if now - authorized_at > timedelta(seconds=CONNECT_STATE_TTL_SECONDS):
                    raise ConnectRequestInvalid("connect authorization has expired")
                return row.connect_authorization_url
            updated_at = (
                row.updated_at
                if row.updated_at.tzinfo is not None
                else row.updated_at.replace(tzinfo=UTC)
            )
            if now - updated_at > timedelta(seconds=CONNECT_STATE_TTL_SECONDS):
                raise ConnectRequestInvalid("connect request has expired")
            url = self.flow.authorize(
                workspace_id=workspace_id,
                agent_id=row.agent_id,
                provider=request.provider,
                grantor_member_id=member_id,
                conversation_id=row.conversation_id,
            )
            updated = await connection.execute(
                sa.update(tables.turn)
                .values(connect_authorization_url=url, connect_authorized_at=sa.func.now())
                .where(
                    tables.turn.c.id == turn_id,
                    tables.turn.c.workspace_id == workspace_id,
                    tables.turn.c.speaker_member_id == member_id,
                    tables.turn.c.connect_authorization_url.is_(None),
                )
            )
            if updated.rowcount == 1:
                return url
            memoized = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.connect_authorization_url,
                        tables.turn.c.connect_authorized_at,
                    ).where(
                        tables.turn.c.id == turn_id,
                        tables.turn.c.workspace_id == workspace_id,
                        tables.turn.c.speaker_member_id == member_id,
                    )
                )
            ).one_or_none()
            if memoized is None or memoized.connect_authorization_url is None:
                raise ConnectRequestInvalid("connect request is no longer available")
            return memoized.connect_authorization_url


_installed_flow: ConnectFlow | None = None


def install_connect_flow(flow: ConnectFlow | None) -> None:
    """The process's single connect flow, installed once at serve boot before any turn runs. The
    `connect_account` tool validates against it, a surface mints a checked private URL through it,
    and the OAuth callback completes through it rather than
    threading a deploy-fixed singleton (one credential key, one provider map, one callback URL)
    through every turn's tool context. None when no credential key is set — both readers then fail
    loud with `ConnectUnavailable`. A test reinstalls to inject a stub provider."""
    global _installed_flow
    _installed_flow = flow


def installed_connect_flow() -> ConnectFlow:
    if _installed_flow is None:
        raise ConnectUnavailable("grants unavailable: no credential key configured")
    return _installed_flow


def connect_bridge_workspace(request: Request) -> UUID | None:
    """The verified workspace for a connector browser bridge request, or None to reject it."""
    try:
        return installed_connect_flow().bridge_workspace(
            state=request.query_params.get("state", ""),
            provider=request.query_params.get("provider", ""),
            callback=request.query_params.get("callback", ""),
        )
    except (ConnectStateInvalid, ConnectUnavailable, UnknownProvider):
        return None


async def grant_summaries(workspace_id: UUID) -> tuple[GrantSummary, ...]:
    """The workspace's grants as audit rows, provider-ordered, joined to the granted agent's name —
    the read behind `ufoctl grants`. Reads no secret, so it needs no encryption key."""
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.agent.c.name,
                    tables.grant.c.provider,
                    tables.grant.c.account_id,
                    tables.grant.c.grantor_member_id,
                    tables.grant.c.conversation_id,
                    tables.grant.c.created_at,
                )
                .select_from(
                    tables.grant.join(tables.agent, tables.grant.c.agent_id == tables.agent.c.id)
                )
                .where(tables.grant.c.workspace_id == workspace_id)
                .order_by(tables.grant.c.provider)
            )
        ).all()
    return tuple(
        GrantSummary(
            agent=row.name,
            provider=row.provider,
            account_id=row.account_id,
            grantor_member_id=row.grantor_member_id,
            conversation_id=row.conversation_id,
            granted_at=row.created_at,
        )
        for row in rows
    )
