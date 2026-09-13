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
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection
from starlette.requests import Request

from ufo.db import workspace_tx
from ufo.runtime.agent_scope import agent
from ufo.runtime.object_name import OBJECT_NAME_MAX_LENGTH
from ufo.runtime.object_scope import object_agent_id
from ufo.runtime.turns.subjects import SHARED_SUBJECT, connection_subject
from ufo.runtime.workspace import ws, ws_current
from ufo.schema import tables
from ufo.schema.records import TerminalFrame

CONNECT_STATE_TTL_SECONDS = 600
CONNECT_MEMO_SECONDS = 120
"""How long one minted authorization is handed back before the next press mints another. Shorter
than the window the state is signed for, and by more than a consent flow takes: a URL handed out at
the end of its own signing window sends the member to a page the callback then refuses, and the
member reads that as the connection failing. Minting is local, so the cost of a fresh one is
nothing."""
GRANT_SENTINEL_PREFIX = "UFO_SENTINEL_GRANT_"
CONNECTED_MESSAGE = "Connected {provider}: {account}."
CONNECTED_KEY_PREFIX = "connect:"
CONNECTED_KEY_DIGEST_LENGTH = 32


def grant_sentinel(account_id: str) -> str:
    """The sentinel a grant's CLI credential rides the wire as — deterministic from the connected
    account, so the engine (exporting it into the sandbox env) and the egress proxy (swapping the
    account's token in for it) agree without a shared registration."""
    return f"{GRANT_SENTINEL_PREFIX}{account_id}"


def scoped_cli_accounts(
    grants: "tuple[Grant, ...]",
    provider: str,
    connections: tuple[UUID, ...],
) -> tuple[str, ...]:
    """The preferred account tier one provider's CLI may use from an exact capability set. A
    private capability outranks every shared capability regardless of its owner; within the
    winning tier every consumer sees the same sorted ambiguity."""
    scoped = tuple(
        grant
        for grant in grants
        if grant.provider == provider and grant.connection_id in connections
    )
    private = tuple(grant for grant in scoped if not grant.connection_shared)
    return tuple(sorted(grant.account_id for grant in (private or scoped)))


class UnknownProvider(LookupError):
    """No OAuth provider is installed under this name — no connectors extension declares it and no
    open namespace claims it. Its str is member-facing: the raise reaches the intent outcome and
    the turn's terminal verbatim, so it carries a sentence, never the bare slug."""

    def __init__(self, provider: str) -> None:
        super().__init__(f"No connector is available for {provider!r}.")


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
class CommitIdentity:
    """The author and committer a sandbox's git stamps a commit with, as the provider attributes it
    to the connected account the same sandbox clones and pushes as.

    A provider attributes a commit by its author address, so the address is the provider's to name,
    not this deploy's: the connected account's own is what links the commit to the account whose
    token pushed it, and the member's ufo login address — a different namespace — links it to
    nobody. Read once when consent completes and held on the connection, so no sandbox open pays a
    provider call for it."""

    name: str
    email: str


@dataclass(frozen=True)
class OAuthAccount:
    """What a completed handoff yields: the broker's stable connected-account id. The account's
    token stays with the broker (server-side execution), so no secret crosses into the grant.
    `commit` is the identity the account's own commits carry, for a connector whose token a sandbox
    clones and pushes with; None for every provider that signs no commits."""

    account_id: str
    account_label: str | None = None
    commit: CommitIdentity | None = None


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
    """One agent's usable view of a connection. `owner_member_id` and `owner_email` are None for
    the workspace's own connection — a keyed or configured feed nobody owns; `account_label` is the
    broker's own name for the connected account, empty for an account the broker named nothing;
    `commit` is the identity its commits carry, None for a connection that signs none."""

    id: UUID
    connection_id: UUID
    provider: str
    account_id: str
    host: str
    owner_member_id: UUID | None
    owner_email: str | None
    connection_shared: bool
    account_label: str = ""
    commit: CommitIdentity | None = None


@dataclass(frozen=True)
class GrantSummary:
    """The audit view of one connector-grant edge joined to its connection."""

    id: UUID
    connection_id: UUID
    agent: str
    provider: str
    account_id: str
    host: str
    owner_member_id: UUID | None
    owner_email: str | None
    granted_at: datetime
    updated_at: datetime
    shared: bool


@dataclass(frozen=True)
class ConnectionSummary:
    """One connection and the agents currently granted it. `owner_member_id` and `owner_email` are
    None for the workspace's own connection — a keyed or configured feed nobody owns."""

    id: UUID
    provider: str
    account_id: str
    host: str
    base_url: str | None
    backfill_days: int | None
    owner_member_id: UUID | None
    owner_email: str | None
    shared: bool
    connected_at: datetime
    updated_at: datetime
    agents: tuple[str, ...]


@dataclass(frozen=True)
class FeedConnection:
    """One connection as a feed registrar reads it: the account its streams authenticate as, the
    tenant URL they dial, how far back their first sync reaches, and the disclosure their pages
    carry. Every connection the workspace holds is one — which agents may read what it syncs is the
    grant's answer, not the registrar's."""

    id: UUID
    provider: str
    account_id: str
    base_url: str | None
    backfill_days: int | None
    owner_member_id: UUID | None
    shared: bool


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
    hook payload lives beside the flow that publishes it, since `ufo.runtime.ext.manifest` imports
    this module and folds it into `HookPayload`."""

    connection_id: UUID
    provider: str
    account_id: str
    owner_member_id: UUID
    agent_id: UUID
    account_label: str = ""
    """What the provider called the account, where it called it anything: the name a surface states
    to the member, since the broker's own id names nothing they would recognise."""


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
    turn_id: UUID | None = None
    """The turn whose request this state was minted for, stamped when the grant lands so the reply
    that asked reads as answered. None where the connect began outside a turn; the grant lands
    either way, since the stamp is what a surface draws and never what makes the connection."""


INDEX_REAP_EXTENSION = "core"
INDEX_REAP_KEY_PREFIX = "index_reap:"
"""Where a disconnect queues the page uids its cascade takes, for `reap_index_queue` to drain — the
core extension's key space, spelled here because `ufo.runtime.ext` imports this module."""


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
        shared: bool,
        account_label: str | None = None,
        commit: CommitIdentity | None = None,
        landed_turn_id: UUID | None = None,
    ) -> UUID:
        """Create or reuse the member's connection, grant the bound agent, and answer the connection
        the grant landed on. A broker account has
        one owner per workspace; reconnecting it as another member fails instead of reassigning the
        account, its sources, and every existing edge. Reconnecting only widens sharing: a
        `shared=False` reconnect keeps a workspace-shared connection shared, so a per-agent
        connect never revokes other members' access — narrowing is `set_shared`'s act alone.

        `commit` is written on the reused row as it is on the new one: the identity comes from the
        exchange that just completed, and a row recorded before this connector read one — or under
        an account whose display name has since changed — carries a stale identity until a connect
        replaces it. This connect is the only writer of those columns, so a reconnect is the
        member's whole remedy for a sandbox git that refuses the commit.

        `landed_turn_id` stamps the turn whose request this connect answered, in the same
        transaction as the grant: the surfaces draw a settled control off that stamp, and one
        written separately could fail on its own and leave a reply still asking for an act already
        done — or, written before the conversation is told, cost the member the answer they are
        owed.

        Reusing the connection row is one of the two things this releases. The other is this
        grantor's own parked feeds of the same provider, whichever account they are bound to. A
        broker mints a new account id often enough that a member reconnecting can land on a row this
        workspace has never seen — connections are keyed on the account id, so that is a second
        connection beside the first, and a feed bound to the older one would sit parked while its
        member believed they had just repaired it. Nothing here rebinds a feed: the older account
        usually still authenticates, and a healthy feed on it is left exactly where it is. Only the
        parked rows are released, and a release is one retry. A feed the provider still refuses
        parks again within about five minutes, so the cost of releasing a row that was beyond repair
        is three requests.

        The connection's owner bounds that second arm, because a feed is its connection's to act
        on. One member reconnecting a provider says nothing about a feed hanging off another
        member's connection to it, and clearing that feed's marks would both act outside the
        grantor's reach and take the park state from the member who reads it. The first arm needs no
        such bound: a source names one connection, so matching on it already reaches only that
        connection's own streams.

        Reusing the connection row is also what releases the feeds bound to it. A source the
        provider refused into a park, and one that backed off to the hour cap, are both an hour from
        their next look, and this act is the answer to both: the same account reconnected with the
        scope it needs. So the reconnect clears the park marks, both counters, and the backoff on
        every live source of that connection, and those rows sync on the next tick with their
        cursors and pages intact — the hour they would otherwise have waited, spent. Nothing here is
        load-bearing for recovery: a refused stream reads once an hour on its own and the run that
        succeeds releases it. This only makes it immediate, which is what the member who just
        re-granted the scope expects.

        The grantor's seat is a plain read, and this holds no explicit row lock at all: the only
        locks it takes on the `workspace`, `member` and `conversation` rows are the `FOR KEY SHARE`
        the writes below ask for through their foreign keys. A `FOR UPDATE` on any of those rows
        conflicts with that same mode, and closes a lock cycle with every transaction that reaches
        them through a foreign key of its own: `attach` holds the connection row and then needs
        `FOR KEY SHARE` on the workspace row through `connector_grant`, and `Admission._admit`
        holds the conversation row and then needs it through `turn` and `inbound_message`, while
        this side would hold the workspace row and wait for the very row each of them holds.
        Postgres breaks such a cycle by aborting one side, and the side it aborts may be this
        callback, whose provider code is already spent and cannot be exchanged again — the
        connection and its grant never land, and the member starts the whole handoff again. Sharing
        the `FOR KEY SHARE` mode queues those pairs instead, and a seat change that takes the
        workspace row `FOR UPDATE` (`Seats.revoke`) is one this waits behind while holding nothing
        that side needs."""
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in account_id):
            raise ValueError("account_id has a control character; refusing to record the grant")
        async with workspace_tx() as connection:
            grantor = (
                await connection.execute(
                    sa.select(tables.member.c.seated_at).where(
                        tables.member.c.id == grantor_member_id,
                        tables.member.c.workspace_id == self.workspace_id,
                    )
                )
            ).one_or_none()
            if grantor is None or grantor.seated_at is None:
                raise ConnectionPermissionDenied(
                    "the member who started this connection no longer has workspace access"
                )
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
                    shared=shared,
                    account_label=account_label,
                    commit_name=None if commit is None else commit.name,
                    commit_email=None if commit is None else commit.email,
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
                    commit_name=None if commit is None else commit.name,
                    commit_email=None if commit is None else commit.email,
                    updated_at=sa.func.now(),
                )
                .where(tables.connection.c.id == existing.id)
            )
            if shared:
                await self._restamp(connection, existing.id, SHARED_SUBJECT, datetime.now(UTC))
            await connection.execute(
                insert(tables.connector_grant)
                .values(
                    id=uuid4(),
                    workspace_id=self.workspace_id,
                    agent_id=self.agent_id,
                    connection_id=existing.id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_update(
                    index_elements=[
                        tables.connector_grant.c.workspace_id,
                        tables.connector_grant.c.agent_id,
                        tables.connector_grant.c.connection_id,
                    ],
                    set_={"updated_at": sa.func.now()},
                )
            )
            if landed_turn_id is not None:
                await connection.execute(
                    sa.update(tables.turn)
                    .values(connect_landed_at=sa.func.now())
                    .where(
                        tables.turn.c.id == landed_turn_id,
                        tables.turn.c.workspace_id == self.workspace_id,
                    )
                )
            await connection.execute(
                sa.update(tables.source)
                .values(
                    parked_at=None,
                    parked_reason=None,
                    consecutive_refusals=0,
                    consecutive_empty=0,
                    consecutive_errors=0,
                    next_sync_at=datetime.now(UTC),
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.source.c.workspace_id == self.workspace_id,
                    sa.or_(
                        tables.source.c.connection_id == existing.id,
                        sa.and_(
                            tables.source.c.parked_at.is_not(None),
                            tables.source.c.connection_id.in_(
                                sa.select(tables.connection.c.id).where(
                                    tables.connection.c.workspace_id == self.workspace_id,
                                    tables.connection.c.provider == provider,
                                    tables.connection.c.owner_member_id == grantor_member_id,
                                )
                            ),
                        ),
                    ),
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
                        tables.member.c.email.label("owner_email"),
                        tables.connection.c.shared,
                        tables.connection.c.account_label,
                        tables.connection.c.commit_name,
                        tables.connection.c.commit_email,
                    )
                    .select_from(
                        tables.connector_grant.join(
                            tables.connection,
                            tables.connector_grant.c.connection_id == tables.connection.c.id,
                        ).outerjoin(
                            tables.member,
                            sa.and_(
                                tables.connection.c.workspace_id == tables.member.c.workspace_id,
                                tables.connection.c.owner_member_id == tables.member.c.id,
                            ),
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
                owner_email=row.owner_email,
                connection_shared=row.shared,
                account_label=row.account_label or "",
                commit=(
                    CommitIdentity(name=row.commit_name, email=row.commit_email)
                    if row.commit_name and row.commit_email
                    else None
                ),
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
        actor_member_id: UUID,
        shared: bool,
    ) -> bool:
        """Attach an existing connection to this agent. The actor must own the connection or the
        connection must be workspace-shared — an admin holds no escape, since attaching a private
        connection widens the owner's access. Attach never changes sharing: a `shared` claim that
        would widen the connection is refused.

        It does wake the connection's streams: a stream every partition of which the provider
        refused lands nothing run after run and idles to a daily look, and the grant that finally
        admits it should not wait a day to be noticed — so the idle counter clears and the rows come
        due now, exactly as a reconnect releases them."""
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
            await connection.execute(
                sa.update(tables.source)
                .values(
                    consecutive_empty=0,
                    next_sync_at=datetime.now(UTC),
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.source.c.workspace_id == self.workspace_id,
                    tables.source.c.connection_id == row.id,
                )
            )
        return True

    async def set_shared(
        self,
        connection_id: UUID,
        shared: bool,
        *,
        actor_member_id: UUID,
    ) -> bool:
        """Flip a connection's sharing flag after rechecking owner and one-way admin authority, and
        restamp the pages its streams already synced.

        Keyed by the connection, because that is what the flag is: one column on one row, deciding
        what every agent holding it may use and what disclosure every page it syncs carries. Keying
        it by a per-agent grant would make one agent's edge look like the thing being shared.

        The flag IS the disclosure, so leaving the pages behind would keep the workspace reading
        what a member just made private. Each page takes a fresh `updated_at` so the page-change
        replay re-indexes it under the new subject, exactly as an edit does. A connection nobody
        owns cannot be made private — the schema's `connection_shared` check refuses it, and this
        refuses it first rather than raising."""
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            selected = await self._connection_for_actor(
                connection,
                connection_id,
                actor_member_id,
                admin_allowed=not shared,
            )
            if selected is None:
                return False
            owner = (
                await connection.execute(
                    sa.select(tables.connection.c.owner_member_id).where(
                        tables.connection.c.workspace_id == self.workspace_id,
                        tables.connection.c.id == selected,
                    )
                )
            ).scalar_one()
            if owner is None and not shared:
                return False
            await connection.execute(
                sa.update(tables.connection)
                .values(shared=shared, updated_at=sa.func.now())
                .where(
                    tables.connection.c.workspace_id == self.workspace_id,
                    tables.connection.c.id == selected,
                )
            )
            await self._restamp(connection, selected, connection_subject(shared, owner), now)
        return True

    async def _restamp(
        self, connection: AsyncConnection, connection_id: UUID, subject: str, now: datetime
    ) -> None:
        """Every live page the connection's streams synced takes the connection's disclosure, with
        a fresh `updated_at` so the page-change replay re-indexes it under the new subject. The flag
        IS the disclosure, so no path that moves the flag may leave the pages where they were."""
        await connection.execute(
            sa.update(tables.page)
            .values(subject=subject, updated_at=now)
            .where(
                tables.page.c.workspace_id == self.workspace_id,
                tables.page.c.source_uid.in_(
                    sa.select(tables.source.c.uid).where(
                        tables.source.c.workspace_id == self.workspace_id,
                        tables.source.c.connection_id == connection_id,
                    )
                ),
                tables.page.c.tombstone.is_(False),
            )
        )

    async def disconnect(self, connection_id: UUID, *, actor_member_id: UUID) -> bool:
        """Remove a connection. Its source rows, their synced pages, and every connector-grant edge
        follow by cascade, so the account stops syncing and stops being recallable in one statement
        — nothing outlives the authority that fetched it.

        The index chunks those pages hold are derived state, so their reap is queued in the same
        transaction as the delete — never inline: the index has no rollback, a post-commit loop
        leaves the tail behind on a fault with no retry (the rows are gone, so a second attempt
        finds nothing to read), and an unbounded per-page walk would hold the member's turn for
        every page the account ever synced. `core_jobs`'s reap job drains the queue in bounded
        batches, resuming after any fault from the queue row that survives it."""
        async with workspace_tx() as connection:
            selected = await self._connection_for_actor(connection, connection_id, actor_member_id)
            if selected is None:
                return False
            indexed = (
                (
                    await connection.execute(
                        sa.select(tables.page.c.uid).where(
                            tables.page.c.workspace_id == self.workspace_id,
                            tables.page.c.source_uid.in_(
                                sa.select(tables.source.c.uid).where(
                                    tables.source.c.workspace_id == self.workspace_id,
                                    tables.source.c.connection_id == selected,
                                )
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            await connection.execute(
                sa.delete(tables.connection).where(
                    tables.connection.c.workspace_id == self.workspace_id,
                    tables.connection.c.id == selected,
                )
            )
            if indexed:
                await connection.execute(
                    sa.insert(tables.ext_store).values(
                        workspace_id=self.workspace_id,
                        extension=INDEX_REAP_EXTENSION,
                        key=f"{INDEX_REAP_KEY_PREFIX}{uuid4()}",
                        value=[str(page_uid) for page_uid in indexed],
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        return True

    async def set_feed(
        self,
        connection_id: UUID,
        *,
        base_url: str | None,
        backfill_days: int | None,
        actor_member_id: UUID,
    ) -> bool:
        """Set what this connection's streams read: the tenant API URL they dial, and how far back
        their first sync reaches. Owner-or-admin, the rule every connection edit holds to.

        `base_url` is admitted under the connection's provider rule, read in the same transaction.
        The streams send the workspace's credential to whatever host this column names, so which
        hosts a provider may be dialled at is a security boundary the store holds — not a shape the
        provider settles on the first request, because a request to a host the caller controls
        succeeds."""
        async with workspace_tx() as connection:
            selected = await self._connection_for_actor(connection, connection_id, actor_member_id)
            if selected is None:
                return False
            provider = (
                await connection.execute(
                    sa.select(tables.connection.c.provider).where(
                        tables.connection.c.workspace_id == self.workspace_id,
                        tables.connection.c.id == selected,
                    )
                )
            ).scalar_one()
            updated = await connection.execute(
                sa.update(tables.connection)
                .values(
                    base_url=_tenant_url(provider, base_url),
                    backfill_days=backfill_days,
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.connection.c.workspace_id == self.workspace_id,
                    tables.connection.c.id == selected,
                )
            )
        return updated.rowcount > 0

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
    value both legs use, so the token exchange presents the same redirect the link did.
    `portal_url` is the browser portal the callback page carries the member to once the grant
    lands, and is None on a deploy that installs no browser surface — one with no screen to send
    them to."""

    providers: Mapping[str, OAuthProvider]
    fernet: Fernet
    store: GrantStore
    redirect_uri: str
    portal_url: str | None = None
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
        turn_id: UUID | None = None,
    ) -> str:
        descriptor = self._provider(provider)
        state = ConnectState(
            workspace_id=workspace_id,
            agent_id=agent_id,
            provider=provider,
            grantor_member_id=grantor_member_id,
            conversation_id=conversation_id,
            shared=shared,
            turn_id=turn_id,
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
                shared=claims.shared,
                account_label=account.account_label,
                commit=account.commit,
                landed_turn_id=claims.turn_id,
            )
            if self.connections is not None:
                await self.connections.fire(
                    ConnectionRecorded(
                        connection_id=connection_id,
                        provider=descriptor.provider,
                        account_id=account.account_id,
                        owner_member_id=claims.grantor_member_id,
                        agent_id=claims.agent_id,
                        account_label=account.account_label or "",
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
    """Hand a terminal connect request's OAuth URL to its speaking member, minting one where the
    turn holds none it can still use.

    The URL is memoized so every read of one request names one control, and re-minted once the state
    it carries has aged past its signing window: the window is what stops a stale state from being
    replayed at the callback, and it is far shorter than the time a member spends on the provider's
    pages. A request that refused to re-mint would leave the surface with no URL to draw, taking the
    control away from a member whose reply still tells them to press it — so the control stands as
    long as the request does, and pressing it is always a live consent page. Everything that gates
    who may open one is unchanged: the request must still stand on the turn, name a provider the
    deploy serves, name an agent the workspace still holds, and belong to the member asking. That
    last pair is why the checks are read again on every press rather than once when the request was
    made: a request outliving its own signing window may outlive the agent it was to grant, and a
    grant sealed for one that is gone fails inside the callback, where the member is waiting.

    The write that replaces an aged authorization names the URL it read, not the stamp beside it: a
    stamp written by the store's own clock reads back in whatever precision that store keeps and
    would match nothing, while every mint seals its own state, so no two URLs are equal. The read
    holds the row, but SQLite takes no row lock, so that comparison is what arbitrates a race — the
    caller whose URL has moved lost, and reads the winner's, minted just now."""

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
            grantee = request.grantee_agent_id or row.agent_id
            held = (
                await connection.execute(
                    sa.select(tables.agent.c.id).where(
                        tables.agent.c.id == grantee,
                        tables.agent.c.workspace_id == workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if held is None:
                raise ConnectRequestInvalid("connect request names an agent that is gone")
            held = self._held(row.connect_authorization_url, row.connect_authorized_at)
            if held is not None:
                return held
            url = self.flow.authorize(
                workspace_id=workspace_id,
                agent_id=grantee,
                provider=request.provider,
                grantor_member_id=request.requester_member_id,
                conversation_id=row.conversation_id,
                shared=request.shared,
                turn_id=turn_id,
            )
            held_url = (
                tables.turn.c.connect_authorization_url.is_(None)
                if row.connect_authorization_url is None
                else tables.turn.c.connect_authorization_url == row.connect_authorization_url
            )
            updated = await connection.execute(
                sa.update(tables.turn)
                .values(connect_authorization_url=url, connect_authorized_at=sa.func.now())
                .where(
                    tables.turn.c.id == turn_id,
                    tables.turn.c.workspace_id == workspace_id,
                    held_url,
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
            if memoized is None:
                raise ConnectRequestInvalid("connect request is no longer available")
            won = self._held(memoized.connect_authorization_url, memoized.connect_authorized_at)
            if won is None:
                raise ConnectRequestInvalid("connect request is no longer available")
            return won

    def _held(self, url: str | None, authorized_at: datetime | None) -> str | None:
        """The memoized authorization while a press can still finish on it — None where the turn
        holds none to hand over, having never minted one or holding one old enough that the consent
        it opens could outlive the state it carries."""
        if url is None or authorized_at is None:
            return None
        stamped = (
            authorized_at if authorized_at.tzinfo is not None else authorized_at.replace(tzinfo=UTC)
        )
        if datetime.now(UTC) - stamped > timedelta(seconds=CONNECT_MEMO_SECONDS):
            return None
        return url


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


DOMAIN_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
TENANT_URL_RULES: dict[str, tuple[re.Pattern[str], re.Pattern[str], str]] = {
    "active_campaign": (
        re.compile(rf"{DOMAIN_LABEL}\.api-us1\.com"),
        re.compile(r"/?"),
        "https://<account>.api-us1.com",
    ),
    "bamboohr": (
        re.compile(r"api\.bamboohr\.com"),
        re.compile(rf"/api/gateway\.php/{DOMAIN_LABEL}/?"),
        "https://api.bamboohr.com/api/gateway.php/<subdomain>",
    ),
    "chargebee": (
        re.compile(rf"{DOMAIN_LABEL}\.chargebee\.com"),
        re.compile(r"/api/v2/?"),
        "https://<site>.chargebee.com/api/v2",
    ),
    "datadog": (
        re.compile(
            r"api\.(?:us3\.|us5\.|ap1\.|ap2\.|uk1\.)?datadoghq\.com"
            r"|api\.datadoghq\.eu"
            r"|api\.(?:us2\.)?ddog-gov\.com"
        ),
        re.compile(r"/?"),
        "https://api.datadoghq.com",
    ),
    "freshdesk": (
        re.compile(rf"{DOMAIN_LABEL}\.freshdesk\.com"),
        re.compile(r"/?"),
        "https://<domain>.freshdesk.com",
    ),
    "mailchimp": (
        re.compile(rf"{DOMAIN_LABEL}\.api\.mailchimp\.com"),
        re.compile(r"/?"),
        "https://<dc>.api.mailchimp.com",
    ),
    "quickbooks": (
        re.compile(r"quickbooks\.api\.intuit\.com"),
        re.compile(r"/v3/company/[0-9]{1,32}/?"),
        "https://quickbooks.api.intuit.com/v3/company/<realmId>",
    ),
    "recruitee": (
        re.compile(r"api\.recruitee\.com"),
        re.compile(rf"/c/{DOMAIN_LABEL}/?"),
        "https://api.recruitee.com/c/<company_id>",
    ),
    "salesforce": (
        re.compile(rf"(?:{DOMAIN_LABEL}\.)+salesforce\.com"),
        re.compile(r"/?"),
        "https://<instance>.salesforce.com",
    ),
    "zendesk": (
        re.compile(rf"{DOMAIN_LABEL}\.zendesk\.com"),
        re.compile(r"/?"),
        "https://<subdomain>.zendesk.com",
    ),
}
"""The hosts and paths a per-tenant provider may be dialled at: host pattern, path pattern, and the
example a refusal names. A provider absent here has one fixed host and takes no `base_url` at all.

This table is a security boundary. A connection's streams send the workspace's provider credential
to whatever host `base_url` names, and a workspace admin who may apply the `connection` kind has no
read path to that credential — so a URL that only had to be a well-formed https origin would let an
admin export the key by pointing a keyed feed at a host they control. The provider never refuses
that URL: the request to it succeeds."""


def _tenant_url(provider: str, base_url: str | None) -> str | None:
    """The tenant API URL a connection stores, or None where it dials its connector's own host. A
    provider outside `TENANT_URL_RULES` refuses every `base_url`; one inside it takes only an https
    origin with no credentials, port, query or fragment whose host and path match its rule. A
    trailing slash is dropped so one tenant is one string."""
    value = (base_url or "").strip()
    if not value:
        return None
    rule = TENANT_URL_RULES.get(provider)
    if rule is None:
        raise ValueError(f"{provider!r} has a fixed API host; base_url cannot override it")
    host_pattern, path_pattern, example = rule
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{provider!r} base_url has an invalid port") from error
    hostname = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            f"{provider!r} base_url must be an https origin with no credentials, port or query"
        )
    if host_pattern.fullmatch(hostname) is None or path_pattern.fullmatch(parsed.path) is None:
        raise ValueError(f"{provider!r} base_url must match {example}")
    return f"https://{hostname}{parsed.path.rstrip('/')}"


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
    member_name = sa.func.coalesce(tables.agent.c.archived_name, tables.agent.c.name)
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.connector_grant.c.id.label("grant_id"),
                    tables.connection.c.id.label("connection_id"),
                    member_name.label("name"),
                    tables.connection.c.provider,
                    tables.connection.c.account_id,
                    tables.connection.c.host,
                    tables.connection.c.owner_member_id,
                    tables.member.c.email.label("owner_email"),
                    tables.connector_grant.c.created_at,
                    tables.connector_grant.c.updated_at,
                    tables.connection.c.shared,
                )
                .select_from(
                    tables.connector_grant.join(
                        tables.connection,
                        tables.connector_grant.c.connection_id == tables.connection.c.id,
                    )
                    .join(tables.agent, tables.connector_grant.c.agent_id == tables.agent.c.id)
                    .outerjoin(
                        tables.member,
                        sa.and_(
                            tables.connection.c.workspace_id == tables.member.c.workspace_id,
                            tables.connection.c.owner_member_id == tables.member.c.id,
                        ),
                    )
                )
                .where(scope)
                .order_by(tables.connection.c.provider, member_name)
            )
        ).all()
    return tuple(
        GrantSummary(
            id=row.grant_id,
            connection_id=row.connection_id,
            agent=row.name,
            provider=row.provider,
            account_id=row.account_id,
            host=row.host,
            owner_member_id=row.owner_member_id,
            owner_email=row.owner_email,
            granted_at=row.created_at,
            updated_at=row.updated_at,
            shared=row.shared,
        )
        for row in rows
    )


async def connection_summaries() -> tuple[ConnectionSummary, ...]:
    """This workspace's connections, independent of the bound agent."""
    member_name = sa.func.coalesce(tables.agent.c.archived_name, tables.agent.c.name)
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.connection.c.id,
                    tables.connection.c.provider,
                    tables.connection.c.account_id,
                    tables.connection.c.host,
                    tables.connection.c.base_url,
                    tables.connection.c.backfill_days,
                    tables.connection.c.owner_member_id,
                    tables.member.c.email.label("owner_email"),
                    tables.connection.c.shared,
                    tables.connection.c.created_at,
                    tables.connection.c.updated_at,
                    member_name.label("name"),
                )
                .select_from(
                    tables.connection.outerjoin(
                        tables.member,
                        sa.and_(
                            tables.connection.c.workspace_id == tables.member.c.workspace_id,
                            tables.connection.c.owner_member_id == tables.member.c.id,
                        ),
                    )
                    .outerjoin(
                        tables.connector_grant,
                        tables.connector_grant.c.connection_id == tables.connection.c.id,
                    )
                    .outerjoin(
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
                base_url=row.base_url,
                backfill_days=row.backfill_days,
                owner_member_id=row.owner_member_id,
                owner_email=row.owner_email,
                shared=row.shared,
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
            base_url=summary.base_url,
            backfill_days=summary.backfill_days,
            owner_member_id=summary.owner_member_id,
            owner_email=summary.owner_email,
            shared=summary.shared,
            connected_at=summary.connected_at,
            updated_at=summary.updated_at,
            agents=tuple(sorted(agent_names.get(key, ()))),
        )
        for key, summary in grouped.items()
    )


async def feed_connections() -> tuple[FeedConnection, ...]:
    """Every connection this workspace holds, provider-ordered — what a feed registrar gives its
    streams. It asks nothing about grants: a connection is content the workspace is authorized to
    read, and which agents may reach it is the grant's answer, checked wherever a reader asks."""
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.connection.c.id,
                    tables.connection.c.provider,
                    tables.connection.c.account_id,
                    tables.connection.c.base_url,
                    tables.connection.c.backfill_days,
                    tables.connection.c.owner_member_id,
                    tables.connection.c.shared,
                )
                .where(tables.connection.c.workspace_id == ws_current().workspace_id)
                .order_by(tables.connection.c.provider, tables.connection.c.account_id)
            )
        ).all()
    return tuple(
        FeedConnection(
            id=row.id,
            provider=row.provider,
            account_id=row.account_id,
            base_url=row.base_url,
            backfill_days=row.backfill_days,
            owner_member_id=row.owner_member_id,
            shared=row.shared,
        )
        for row in rows
    )
