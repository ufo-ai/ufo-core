"""Connections and grants: an OAuth account owned by a member, with an edge granting one agent
access. The proxy derives authenticated egress from the agent's edges, while feed sources resolve
the member-owned connection directly. BYOK values remain workspace credentials.

`ConnectFlow` runs the two-legged OAuth handoff: `authorize` opens a provider's link carrying sealed
state; `complete` verifies that state, exchanges the code for the connected account, reuses or
creates its connection, and grants the intended agent. The broker holds the account's token and
executes tools server-side, so no secret crosses this boundary."""

import hashlib
import re
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
from sqlalchemy.ext.asyncio import AsyncConnection
from starlette.requests import Request

from ufo.agent_scope import agent, agent_current
from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.schema.records import TerminalFrame
from ufo.workspace import ws, ws_current

CONNECT_STATE_TTL_SECONDS = 600
GRANT_SENTINEL_PREFIX = "UFO_SENTINEL_GRANT_"


def grant_sentinel(account_id: str) -> str:
    """The sentinel a grant's CLI credential rides the wire as — deterministic from the connected
    account, so the engine (exporting it into the sandbox env) and the egress proxy (matching it to
    forward through the broker) agree without a shared registration."""
    return f"{GRANT_SENTINEL_PREFIX}{account_id}"


class UnknownProvider(KeyError):
    """No OAuth provider is installed under this name — no connectors extension declares it."""


class ConnectStateInvalid(ValueError):
    """The sealed OAuth state is tampered, expired, or unreadable — the callback refuses it."""


class ConnectUnavailable(RuntimeError):
    """No connect flow is installed — the deploy set no credential key, so grants can be neither
    sealed nor recorded. The `connect_account` tool and the OAuth callback fail loud with this."""


class ConnectRequestInvalid(ValueError):
    """A private connect handoff is absent, stale, or belongs to another member."""


class ConnectionOwnedByAnotherMember(ValueError):
    """The broker account already belongs to another member in this workspace."""


class ConnectionPermissionDenied(ValueError):
    """The member cannot mutate this connection or its agent grant."""


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


class OAuthProviderResolver(Protocol):
    """The connect-flow half of an open connector namespace: how a broker extension answers the
    OAuth descriptor for any provider slug it brokers without registering each explicitly. `claims`
    validates the slug against the broker's live catalog (I/O), so a typo fails loud at connect time
    rather than minting a dead consent link; `descriptor` builds the pure descriptor a validated
    slug connects through — the broker holds the account's token, so the grant admits no provider
    host (`host` is empty) and injects nothing."""

    async def claims(self, provider: str) -> bool: ...

    def descriptor(self, provider: str) -> OAuthProvider: ...


@dataclass(frozen=True)
class Grant:
    """One agent's usable view of a connection. Identity and ownership come from the connection;
    disclosure comes from the edge."""

    id: UUID
    connection_id: UUID
    provider: str
    account_id: str
    host: str
    owner_member_id: UUID
    shared: bool


@dataclass(frozen=True)
class GrantSummary:
    """The audit view of one connector-grant edge joined to its connection."""

    id: UUID
    agent: str
    provider: str
    account_id: str
    host: str
    owner_member_id: UUID
    conversation_id: UUID
    granted_at: datetime
    updated_at: datetime
    shared: bool


@dataclass(frozen=True)
class ConnectionSummary:
    """One member-owned broker connection and the agents currently granted it."""

    id: UUID
    provider: str
    account_id: str
    host: str
    owner_member_id: UUID
    conversation_id: UUID
    connected_at: datetime
    updated_at: datetime
    agents: tuple[str, ...]


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
    shared: bool = False


@dataclass(frozen=True)
class GrantStore:
    """Persists member-owned connections and their per-agent grant edges."""

    @property
    def workspace_id(self) -> UUID:
        return ws_current().workspace_id

    @property
    def agent_id(self) -> UUID:
        return agent_current().agent_id

    async def record(
        self,
        *,
        provider: str,
        account_id: str,
        host: str,
        grantor_member_id: UUID,
        conversation_id: UUID,
        shared: bool,
    ) -> None:
        """Create or reuse the member's connection and grant the bound agent. A broker account has
        one owner per workspace; reconnecting it as another member fails instead of reassigning the
        account, its sources, and every existing edge."""
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in account_id):
            raise ValueError("account_id has a control character; refusing to record the grant")
        async with workspace_tx() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            await connection.execute(
                insert(tables.connection)
                .values(
                    id=uuid4(),
                    workspace_id=self.workspace_id,
                    provider=provider,
                    account_id=account_id,
                    host=host,
                    owner_member_id=grantor_member_id,
                    conversation_id=conversation_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        tables.connection.c.workspace_id,
                        tables.connection.c.provider,
                        tables.connection.c.account_id,
                    ]
                )
            )
            existing = (
                await connection.execute(
                    sa.select(
                        tables.connection.c.id,
                        tables.connection.c.owner_member_id,
                    )
                    .where(
                        tables.connection.c.workspace_id == self.workspace_id,
                        tables.connection.c.provider == provider,
                        tables.connection.c.account_id == account_id,
                    )
                    .with_for_update()
                )
            ).one()
            if existing.owner_member_id != grantor_member_id:
                raise ConnectionOwnedByAnotherMember(
                    f"{provider!r} account {account_id!r} is connected by another member"
                )
            await connection.execute(
                sa.update(tables.connection)
                .values(host=host, updated_at=sa.func.now())
                .where(tables.connection.c.id == existing.id)
            )
            await connection.execute(
                insert(tables.connector_grant)
                .values(
                    id=uuid4(),
                    workspace_id=self.workspace_id,
                    agent_id=self.agent_id,
                    connection_id=existing.id,
                    conversation_id=conversation_id,
                    shared=shared,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_update(
                    index_elements=[
                        tables.connector_grant.c.workspace_id,
                        tables.connector_grant.c.agent_id,
                        tables.connector_grant.c.connection_id,
                    ],
                    set_={
                        "conversation_id": conversation_id,
                        "shared": shared,
                        "updated_at": sa.func.now(),
                    },
                )
            )

    async def active_grants(self) -> tuple[Grant, ...]:
        """The bound agent's connection edges, joined to member-owned account identity."""
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.connector_grant.c.id.label("grant_id"),
                        tables.connection.c.id.label("connection_id"),
                        tables.connection.c.provider,
                        tables.connection.c.account_id,
                        tables.connection.c.host,
                        tables.connection.c.owner_member_id,
                        tables.connector_grant.c.shared,
                    )
                    .select_from(
                        tables.connector_grant.join(
                            tables.connection,
                            tables.connector_grant.c.connection_id == tables.connection.c.id,
                        )
                    )
                    .where(
                        tables.connector_grant.c.workspace_id == self.workspace_id,
                        tables.connector_grant.c.agent_id == self.agent_id,
                    )
                )
            ).all()
        return tuple(
            Grant(
                id=row.grant_id,
                connection_id=row.connection_id,
                provider=row.provider,
                account_id=row.account_id,
                host=row.host,
                owner_member_id=row.owner_member_id,
                shared=row.shared,
            )
            for row in rows
        )

    async def revoke(self, grant_id: UUID, *, actor_member_id: UUID) -> bool:
        """Remove the bound agent's edge after rechecking its connection owner or an admin."""
        async with workspace_tx() as connection:
            selected = await self._grant_for_actor(connection, grant_id, actor_member_id)
            if selected is None:
                return False
            deleted = await connection.execute(
                sa.delete(tables.connector_grant).where(
                    tables.connector_grant.c.workspace_id == self.workspace_id,
                    tables.connector_grant.c.id == selected,
                )
            )
        return deleted.rowcount > 0

    async def set_shared(
        self,
        grant_id: UUID,
        shared: bool,
        *,
        actor_member_id: UUID,
    ) -> bool:
        """Flip the bound agent's edge after rechecking owner and one-way admin authority."""
        async with workspace_tx() as connection:
            selected = await self._grant_for_actor(
                connection,
                grant_id,
                actor_member_id,
                admin_allowed=not shared,
            )
            if selected is None:
                return False
            updated = await connection.execute(
                sa.update(tables.connector_grant)
                .values(shared=shared, updated_at=sa.func.now())
                .where(
                    tables.connector_grant.c.workspace_id == self.workspace_id,
                    tables.connector_grant.c.id == selected,
                )
            )
        return updated.rowcount > 0

    async def disconnect(self, connection_id: UUID, *, actor_member_id: UUID) -> bool:
        """Stop every source bound to a connection, tombstone its pages, and remove the connection.
        Connector-grant edges follow by cascade."""
        async with workspace_tx() as connection:
            selected = await self._connection_for_actor(connection, connection_id, actor_member_id)
            if selected is None:
                return False
            source_ids = (
                (
                    await connection.execute(
                        sa.select(tables.source.c.id).where(
                            tables.source.c.workspace_id == self.workspace_id,
                            tables.source.c.connection_id == selected,
                        )
                    )
                )
                .scalars()
                .all()
            )
            now = datetime.now(UTC)
            if source_ids:
                await connection.execute(
                    sa.delete(tables.source_grant).where(
                        tables.source_grant.c.workspace_id == self.workspace_id,
                        tables.source_grant.c.source_id.in_(source_ids),
                    )
                )
                await connection.execute(
                    sa.update(tables.source)
                    .values(
                        connection_id=None,
                        removed_at=now,
                        claimed_by=None,
                        claim_expires_at=None,
                        updated_at=sa.func.now(),
                    )
                    .where(
                        tables.source.c.workspace_id == self.workspace_id,
                        tables.source.c.id.in_(source_ids),
                    )
                )
                await connection.execute(
                    sa.update(tables.page)
                    .values(tombstone=True, updated_at=now)
                    .where(
                        tables.page.c.workspace_id == self.workspace_id,
                        tables.page.c.source_id.in_(source_ids),
                        tables.page.c.tombstone.is_(False),
                    )
                )
            await connection.execute(
                sa.delete(tables.connection).where(
                    tables.connection.c.workspace_id == self.workspace_id,
                    tables.connection.c.id == selected,
                )
            )
        return True

    async def _connection_for_actor(
        self,
        connection: AsyncConnection,
        connection_id: UUID,
        actor_member_id: UUID,
        *,
        admin_allowed: bool = True,
    ) -> UUID | None:
        selected = (
            await connection.execute(
                sa.select(
                    tables.connection.c.id,
                    tables.connection.c.owner_member_id,
                )
                .where(
                    tables.connection.c.workspace_id == self.workspace_id,
                    tables.connection.c.id == connection_id,
                )
                .with_for_update()
            )
        ).one_or_none()
        if selected is None:
            return None
        if selected.owner_member_id == actor_member_id:
            return selected.id
        is_admin = (
            await connection.execute(
                sa.select(tables.member.c.is_admin)
                .where(
                    tables.member.c.workspace_id == self.workspace_id,
                    tables.member.c.id == actor_member_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not admin_allowed or not is_admin:
            raise ConnectionPermissionDenied("member cannot mutate this connection")
        return selected.id

    async def _grant_for_actor(
        self,
        connection: AsyncConnection,
        grant_id: UUID,
        actor_member_id: UUID,
        *,
        admin_allowed: bool = True,
    ) -> UUID | None:
        connection_id = (
            await connection.execute(
                sa.select(tables.connector_grant.c.connection_id).where(
                    tables.connector_grant.c.workspace_id == self.workspace_id,
                    tables.connector_grant.c.agent_id == self.agent_id,
                    tables.connector_grant.c.id == grant_id,
                )
            )
        ).scalar_one_or_none()
        if connection_id is None:
            return None
        selected = await self._connection_for_actor(
            connection,
            connection_id,
            actor_member_id,
            admin_allowed=admin_allowed,
        )
        if selected is None:
            return None
        return (
            await connection.execute(
                sa.select(tables.connector_grant.c.id)
                .where(
                    tables.connector_grant.c.workspace_id == self.workspace_id,
                    tables.connector_grant.c.agent_id == self.agent_id,
                    tables.connector_grant.c.id == grant_id,
                    tables.connector_grant.c.connection_id == selected,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()


@dataclass(frozen=True)
class ConnectFlow:
    """The connect workflow, twinned across a browser redirect: `authorize` opens a provider's link
    carrying sealed state; `complete` verifies that state, exchanges the code, and records the
    grant. Deps: the providers the deploy installs (empty until a connectors extension declares
    any), an optional `resolver` for an open connector namespace whose broker serves any other slug,
    the Fernet that seals the state, the grant store, and the deploy's callback `redirect_uri` — one
    value both legs use, so the token exchange presents the same redirect the link did."""

    providers: Mapping[str, OAuthProvider]
    fernet: Fernet
    store: GrantStore
    redirect_uri: str
    resolver: OAuthProviderResolver | None = None

    def authorize(
        self,
        *,
        workspace_id: UUID,
        agent_id: UUID,
        provider: str,
        grantor_member_id: UUID,
        conversation_id: UUID,
        shared: bool,
    ) -> str:
        descriptor = self._provider(provider)
        state = ConnectState(
            workspace_id=workspace_id,
            agent_id=agent_id,
            provider=provider,
            grantor_member_id=grantor_member_id,
            conversation_id=conversation_id,
            shared=shared,
        )
        sealed = self.fernet.encrypt(state.model_dump_json().encode()).decode()
        return descriptor.authorize_url(sealed, self.redirect_uri)

    async def validate_provider(self, provider: str) -> None:
        if provider in self.providers:
            return
        if self.resolver is not None and await self.resolver.claims(provider):
            return
        raise UnknownProvider(provider)

    def knows_provider(self, provider: str) -> bool:
        """Whether the connect machinery for `provider` is still installed — the cheap check the
        memoizing `authorize` runs under its turn-row lock, never the external catalog validation
        `validate_provider` already ran when the connect request was made. An open namespace serves
        any slug, so its presence alone answers yes."""
        return provider in self.providers or self.resolver is not None

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
        with ws(claims.workspace_id), agent(claims.agent_id):
            account = await descriptor.exchange(code, self.redirect_uri, claims.workspace_id, state)
            await self.store.record(
                provider=descriptor.provider,
                account_id=account.account_id,
                host=descriptor.host,
                grantor_member_id=claims.grantor_member_id,
                conversation_id=claims.conversation_id,
                shared=claims.shared,
            )
        return GrantRecorded(
            provider=descriptor.provider, account_id=account.account_id, agent_id=claims.agent_id
        )

    def _provider(self, name: str) -> OAuthProvider:
        descriptor = self.providers.get(name)
        if descriptor is not None:
            return descriptor
        if self.resolver is not None:
            return self.resolver.descriptor(name)
        raise UnknownProvider(name)

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
            if row is None:
                raise ConnectRequestInvalid("connect request is no longer available")
            if row.terminal is None:
                raise ConnectRequestInvalid("connect request is no longer available")
            terminal = TerminalFrame.model_validate(row.terminal)
            request = terminal.connect_request
            if request is None:
                raise ConnectRequestInvalid("connect request is no longer available")
            if request.requester_member_id != member_id:
                raise ConnectRequestInvalid("connect request belongs to another member")
            if not self.flow.knows_provider(request.provider):
                raise ConnectRequestInvalid("connect provider is no longer available")
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
                grantor_member_id=request.requester_member_id,
                conversation_id=row.conversation_id,
                shared=request.shared,
            )
            updated = await connection.execute(
                sa.update(tables.turn)
                .values(connect_authorization_url=url, connect_authorized_at=sa.func.now())
                .where(
                    tables.turn.c.id == turn_id,
                    tables.turn.c.workspace_id == workspace_id,
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


ACCOUNT_NAME_DIGEST_LENGTH = 8


def account_object_name(provider: str, account_id: str) -> str:
    """The stable object name a provider account renders as — for both the `connection` and
    `connector_grant` kinds and the portal's prepared intents, so every surface names one edge
    the same way: slugged provider and account with a digest qualifier that keeps two accounts
    whose slugs collide distinct."""
    identity = f"{provider}\0{account_id}".encode()
    qualifier = hashlib.sha256(identity).hexdigest()[:ACCOUNT_NAME_DIGEST_LENGTH]
    return f"{_slug(provider)}-{_slug(account_id)}-{qualifier}"


def _slug(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")


async def grant_summaries() -> tuple[GrantSummary, ...]:
    """The bound agent's connector grants as provider-ordered audit rows."""
    return await _grant_summaries(
        sa.and_(
            tables.connector_grant.c.workspace_id == ws_current().workspace_id,
            tables.connector_grant.c.agent_id == agent_current().agent_id,
        )
    )


async def workspace_grant_summaries(workspace_id: UUID) -> tuple[GrantSummary, ...]:
    """One workspace's connector grants for the operator surface."""
    with ws(workspace_id):
        return await _grant_summaries(tables.connector_grant.c.workspace_id == workspace_id)


async def _grant_summaries(scope: sa.ColumnElement[bool]) -> tuple[GrantSummary, ...]:
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.connector_grant.c.id.label("grant_id"),
                    tables.agent.c.name,
                    tables.connection.c.provider,
                    tables.connection.c.account_id,
                    tables.connection.c.host,
                    tables.connection.c.owner_member_id,
                    tables.connector_grant.c.conversation_id,
                    tables.connector_grant.c.created_at,
                    tables.connector_grant.c.updated_at,
                    tables.connector_grant.c.shared,
                )
                .select_from(
                    tables.connector_grant.join(
                        tables.connection,
                        tables.connector_grant.c.connection_id == tables.connection.c.id,
                    ).join(tables.agent, tables.connector_grant.c.agent_id == tables.agent.c.id)
                )
                .where(scope)
                .order_by(tables.connection.c.provider, tables.agent.c.name)
            )
        ).all()
    return tuple(
        GrantSummary(
            id=row.grant_id,
            agent=row.name,
            provider=row.provider,
            account_id=row.account_id,
            host=row.host,
            owner_member_id=row.owner_member_id,
            conversation_id=row.conversation_id,
            granted_at=row.created_at,
            updated_at=row.updated_at,
            shared=row.shared,
        )
        for row in rows
    )


async def connection_summaries() -> tuple[ConnectionSummary, ...]:
    """This workspace's member-owned connections, independent of the bound agent."""
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.connection.c.id,
                    tables.connection.c.provider,
                    tables.connection.c.account_id,
                    tables.connection.c.host,
                    tables.connection.c.owner_member_id,
                    tables.connection.c.conversation_id,
                    tables.connection.c.created_at,
                    tables.connection.c.updated_at,
                    tables.agent.c.name,
                )
                .select_from(
                    tables.connection.outerjoin(
                        tables.connector_grant,
                        tables.connector_grant.c.connection_id == tables.connection.c.id,
                    ).outerjoin(
                        tables.agent,
                        tables.connector_grant.c.agent_id == tables.agent.c.id,
                    )
                )
                .where(tables.connection.c.workspace_id == ws_current().workspace_id)
                .order_by(tables.connection.c.provider, tables.connection.c.account_id)
            )
        ).all()
    grouped: dict[tuple[str, str], ConnectionSummary] = {}
    agent_names: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        key = (row.provider, row.account_id)
        grouped.setdefault(
            key,
            ConnectionSummary(
                id=row.id,
                provider=row.provider,
                account_id=row.account_id,
                host=row.host,
                owner_member_id=row.owner_member_id,
                conversation_id=row.conversation_id,
                connected_at=row.created_at,
                updated_at=row.updated_at,
                agents=(),
            ),
        )
        if row.name is not None:
            agent_names.setdefault(key, []).append(row.name)
    return tuple(
        ConnectionSummary(
            id=summary.id,
            provider=summary.provider,
            account_id=summary.account_id,
            host=summary.host,
            owner_member_id=summary.owner_member_id,
            conversation_id=summary.conversation_id,
            connected_at=summary.connected_at,
            updated_at=summary.updated_at,
            agents=tuple(sorted(agent_names.get(key, ()))),
        )
        for key, summary in grouped.items()
    )
