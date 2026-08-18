"""Connections and grants: an OAuth account owned by a member, with an edge granting one agent
access. The proxy derives authenticated egress from the agent's edges, while feed sources resolve
the member-owned connection directly. BYOK values remain workspace credentials.

`ConnectFlow` runs the two-legged OAuth handoff: `authorize` opens a provider's link carrying sealed
state; `complete` verifies that state, exchanges the code for the connected account, reuses or
creates its connection, grants the intended agent, and publishes the landed connection to the
extensions that derive state from it (`ConnectionHooks`, the `connection_recorded` event). The
broker holds the account's token and executes tools server-side, so no secret crosses this
boundary."""

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
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

from ufo.agent_scope import agent
from ufo.db import workspace_tx
from ufo.object_name import OBJECT_NAME_MAX_LENGTH
from ufo.object_scope import object_agent_id
from ufo.schema import tables
from ufo.schema.records import TerminalFrame
from ufo.workspace import ws, ws_current

CONNECT_STATE_TTL_SECONDS = 600
GRANT_SENTINEL_PREFIX = "UFO_SENTINEL_GRANT_"
CONNECTED_MESSAGE = "Connected {provider}: {account}."
CONNECTED_KEY_PREFIX = "connect:"
CONNECTED_KEY_DIGEST_LENGTH = 32


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
    account_label: str | None = None


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
    """One agent's usable view of a connection."""

    id: UUID
    connection_id: UUID
    provider: str
    account_id: str
    host: str
    owner_member_id: UUID
    connection_shared: bool


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
class MainAgentConnection:
    """One member-owned connection the workspace's main agent is granted: the account a feed would
    sync and the member whose connection pays for it."""

    id: UUID
    provider: str
    account_id: str
    owner_member_id: UUID


@dataclass(frozen=True)
class GrantRecorded:
    """What `complete` returns once the handoff lands durably: the provider account now bound, and
    whether the conversation that asked for it was told. `resumed` is false where this deploy wires
    no resumption, and where the message did not reach the queue — the callback page states what is
    true rather than promising work the agent was never asked to do.

    `label` and `account_label` are the member-facing halves of the same two facts `provider` and
    `account_id` carry internally: the connector's declared display name, and whatever the broker
    calls this account. A page that showed a member `composio_github` and `ca_9x2QpLm4` would be
    naming the wiring instead of the thing they just connected."""

    provider: str
    account_id: str
    agent_id: UUID
    label: str = ""
    account_label: str = ""
    resumed: bool = False


@dataclass(frozen=True)
class ConnectionRecorded:
    """The connection a completed handoff landed, as the extensions deriving state from it read it:
    the connection generation now live, the provider account it holds, the member who owns it, and
    the agent the handoff granted. It is published once that row has committed, so a handler creates
    what the connection implies while the member is still on the callback. The `connection_recorded`
    hook payload lives beside the flow that publishes it, since `ufo.ext.manifest` imports this
    module and folds it into `HookPayload`."""

    connection_id: UUID
    provider: str
    account_id: str
    owner_member_id: UUID
    agent_id: UUID


class ConnectionHooks(Protocol):
    """Where a landed connection reaches the extensions that derive state from it — the source rows
    a connected account feeds. Structural and injected rather than imported: the chain binds the
    installed manifests, which import this module. `fire` swallows a handler's failure, so the
    connection is recorded whatever an extension makes of it."""

    async def fire(self, connection: ConnectionRecorded) -> None: ...


class ConnectResumption(Protocol):
    """Where a landed connection reaches the conversation that asked for it. The turn that began
    the connect is the one blocked on it, and it cannot learn the grant arrived — the member left
    for a browser and comes back to a thread that has said nothing since. Admitting the outcome
    there is what lets the agent carry on without the member asking it to.

    Structural and injected for the same reason as `ConnectionHooks`: this module is imported by
    the surface layer that admits, so it may not import back. `resume` swallows its own failure —
    the grant is recorded and the member is owed that answer whatever the queue does with it."""

    async def resume(
        self,
        conversation_id: UUID,
        message: str,
        *,
        speaker_member_id: UUID,
        idempotency_key: str,
    ) -> bool: ...


def _resume_key(state: str) -> str:
    """The idempotency key for one connect act, taken from the sealed state that act carries.

    The connection is the wrong identity here. `GrantStore.record` settles every connect of one
    account onto a single connection row, so a re-consent, a reconnect that shares the account, or a
    connect for a second agent all read back the id the first connect wrote. A key built from it
    would repeat, and admission — which matches on (workspace, key) — would drop the later resume
    or refuse it as a key reused for a different turn. The member would sit in a conversation that
    was never told, behind a page saying the agent has the connection.

    A state is minted once per authorization and memoized for the turn that asked, so a member who
    refreshes the callback replays the same one and rejoins the message it already sent, while a
    later connect carries a state of its own. Digested rather than carried whole: the key is a
    stored column, and the state is a credential-sealed token that has no business being one."""
    digest = hashlib.sha256(state.encode()).hexdigest()
    return CONNECTED_KEY_PREFIX + digest[:CONNECTED_KEY_DIGEST_LENGTH]


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
    """Persists member-owned connections and their per-agent grant edges. Edges bind to the
    object-dispatch target agent when a verb names one, else the bound agent."""

    @property
    def workspace_id(self) -> UUID:
        return ws_current().workspace_id

    @property
    def agent_id(self) -> UUID:
        return object_agent_id()

    async def record(
        self,
        *,
        provider: str,
        account_id: str,
        host: str,
        grantor_member_id: UUID,
        conversation_id: UUID,
        shared: bool,
        account_label: str | None = None,
    ) -> UUID:
        """Create or reuse the member's connection, grant the bound agent, and answer the connection
        the grant landed on. A broker account has
        one owner per workspace; reconnecting it as another member fails instead of reassigning the
        account, its sources, and every existing edge. Reconnecting only widens sharing: a
        `shared=False` reconnect keeps a workspace-shared connection shared, so a per-agent
        connect never revokes other members' access — narrowing is `set_shared`'s act alone."""
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
                    shared=shared,
                    account_label=account_label,
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
                .values(
                    host=host,
                    shared=sa.or_(tables.connection.c.shared, sa.literal(shared)),
                    account_label=account_label,
                    updated_at=sa.func.now(),
                )
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
                        "updated_at": sa.func.now(),
                    },
                )
            )
        return existing.id

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
                        tables.connection.c.shared,
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
                connection_shared=row.shared,
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

    async def attach(
        self,
        *,
        provider: str,
        account_id: str,
        conversation_id: UUID,
        actor_member_id: UUID,
        shared: bool,
    ) -> bool:
        """Attach an existing connection to this agent. The actor must own the connection or the
        connection must be workspace-shared — an admin holds no escape, since attaching a private
        connection widens the owner's access. Attach never changes sharing: a `shared` claim that
        would widen the connection is refused."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.connection.c.id,
                        tables.connection.c.owner_member_id,
                        tables.connection.c.shared,
                    )
                    .where(
                        tables.connection.c.workspace_id == self.workspace_id,
                        tables.connection.c.provider == provider,
                        tables.connection.c.account_id == account_id,
                    )
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                return False
            if row.owner_member_id != actor_member_id and not row.shared:
                raise ConnectionPermissionDenied("member cannot attach this connection")
            if shared and not row.shared:
                raise ValueError(
                    "attach cannot share a connection; apply shared on the existing grant"
                )
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            await connection.execute(
                insert(tables.connector_grant)
                .values(
                    id=uuid4(),
                    workspace_id=self.workspace_id,
                    agent_id=self.agent_id,
                    connection_id=row.id,
                    conversation_id=conversation_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        tables.connector_grant.c.workspace_id,
                        tables.connector_grant.c.agent_id,
                        tables.connector_grant.c.connection_id,
                    ]
                )
            )
        return True

    async def set_shared(
        self,
        grant_id: UUID,
        shared: bool,
        *,
        actor_member_id: UUID,
    ) -> bool:
        """Flip the connection's sharing flag after rechecking owner and one-way admin authority."""
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
                sa.update(tables.connection)
                .values(shared=shared, updated_at=sa.func.now())
                .where(
                    tables.connection.c.workspace_id == self.workspace_id,
                    tables.connection.c.id
                    == sa.select(tables.connector_grant.c.connection_id)
                    .where(tables.connector_grant.c.id == selected)
                    .scalar_subquery(),
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
        is_admin = await self._is_admin(connection, actor_member_id)
        if not admin_allowed or not is_admin:
            raise ConnectionPermissionDenied("member cannot mutate this connection")
        return selected.id

    async def _is_admin(self, connection: AsyncConnection, actor_member_id: UUID) -> bool:
        return bool(
            (
                await connection.execute(
                    sa.select(tables.member.c.is_admin).where(
                        tables.member.c.workspace_id == self.workspace_id,
                        tables.member.c.id == actor_member_id,
                    )
                )
            ).scalar_one_or_none()
        )

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
    connections: ConnectionHooks | None = None
    resumption: ConnectResumption | None = None
    labels: Mapping[str, str] = field(default_factory=dict)

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
        """Land the handoff, then publish the connection to the extensions that derive state from
        it: what a connected account implies — the feeds it syncs — exists by the time the member
        reads the callback, rather than at the next sweep of whatever job would notice later.

        The conversation that began the connect is told last, once the grant and everything derived
        from it stand — so the turn it wakes reads a workspace where the account is already usable,
        rather than racing the feeds its own answer is about."""
        claims = self._open(state)
        descriptor = self._provider(claims.provider)
        with ws(claims.workspace_id), agent(claims.agent_id):
            account = await descriptor.exchange(code, self.redirect_uri, claims.workspace_id, state)
            connection_id = await self.store.record(
                provider=descriptor.provider,
                account_id=account.account_id,
                host=descriptor.host,
                grantor_member_id=claims.grantor_member_id,
                conversation_id=claims.conversation_id,
                shared=claims.shared,
                account_label=account.account_label,
            )
            if self.connections is not None:
                await self.connections.fire(
                    ConnectionRecorded(
                        connection_id=connection_id,
                        provider=descriptor.provider,
                        account_id=account.account_id,
                        owner_member_id=claims.grantor_member_id,
                        agent_id=claims.agent_id,
                    )
                )
            label = self.label_for(descriptor.provider)
            named = account.account_label or account.account_id
            resumed = False
            if self.resumption is not None:
                resumed = await self.resumption.resume(
                    claims.conversation_id,
                    CONNECTED_MESSAGE.format(provider=label, account=named),
                    speaker_member_id=claims.grantor_member_id,
                    idempotency_key=_resume_key(state),
                )
        return GrantRecorded(
            provider=descriptor.provider,
            account_id=account.account_id,
            agent_id=claims.agent_id,
            label=label,
            account_label=account.account_label or "",
            resumed=resumed,
        )

    def label_for(self, provider: str) -> str:
        """The connector's member-facing name. Declared on the manifest entry rather than on the
        OAuth descriptor, so it is handed in beside the providers; an open-namespace slug the broker
        serves without a declaration falls back to the slug read as words, which is the same
        rendering the catalog gives it."""
        return self.labels.get(provider) or provider.replace("_", " ").title()

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
                agent_id=request.grantee_agent_id or row.agent_id,
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
ACCOUNT_NAME_HEAD_MAX = OBJECT_NAME_MAX_LENGTH - ACCOUNT_NAME_DIGEST_LENGTH - 1


def account_object_name(provider: str, account_id: str) -> str:
    """The stable object name a provider account renders as — for both the `connection` and
    `connector_grant` kinds and the portal's prepared intents, so every surface names one edge
    the same way: slugged provider and account with a digest qualifier that keeps two accounts
    whose slugs collide distinct. `account_id` is unbounded, so the slugged head is truncated to
    leave the digest whole — it is what separates two accounts sharing a prefix."""
    identity = f"{provider}\0{account_id}".encode()
    qualifier = hashlib.sha256(identity).hexdigest()[:ACCOUNT_NAME_DIGEST_LENGTH]
    head = f"{_slug(provider)}-{_slug(account_id)}".strip("-")[:ACCOUNT_NAME_HEAD_MAX].strip("-")
    return f"{head}-{qualifier}" if head else qualifier


def _slug(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")


async def grant_summaries() -> tuple[GrantSummary, ...]:
    """One agent's connector grants as provider-ordered audit rows — the object-dispatch target
    agent when a verb names one, else the bound agent."""
    return await _grant_summaries(
        sa.and_(
            tables.connector_grant.c.workspace_id == ws_current().workspace_id,
            tables.connector_grant.c.agent_id == object_agent_id(),
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
                    tables.connection.c.shared,
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


async def main_agent_connections() -> tuple[MainAgentConnection, ...]:
    """This workspace's connections the main agent holds a grant for, provider-ordered — what a feed
    registrar may sync without being told. A feed registered off one of these grants the main agent,
    so a connection held only by a shipped agent is absent: the account a member connected for that
    agent stays with it."""
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.connection.c.id,
                    tables.connection.c.provider,
                    tables.connection.c.account_id,
                    tables.connection.c.owner_member_id,
                )
                .select_from(
                    tables.connection.join(
                        tables.connector_grant,
                        tables.connector_grant.c.connection_id == tables.connection.c.id,
                    ).join(tables.agent, tables.connector_grant.c.agent_id == tables.agent.c.id)
                )
                .where(
                    tables.connection.c.workspace_id == ws_current().workspace_id,
                    tables.agent.c.is_main.is_(True),
                )
                .order_by(tables.connection.c.provider, tables.connection.c.account_id)
            )
        ).all()
    return tuple(
        MainAgentConnection(
            id=row.id,
            provider=row.provider,
            account_id=row.account_id,
            owner_member_id=row.owner_member_id,
        )
        for row in rows
    )
