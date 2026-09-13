"""The source-trigger table and the scoped store that owns it.

A trigger is one conversation's standing interest in one connection's feed. Each batch returns to
that conversation. The row carries the connection it watches, the owning conversation, the agent
it invokes, and the member who asked for it.

A trigger narrows to one resource of that feed — a pull request, an issue — named by the row's
`resource`, and to the streams its `streams` names. A live narrowed row is also what the connection
syncs for: `pinned` answers the sync driver which resources it reads every tick. Both join the
conversation and the connection in the table's unique key, so a thread watches the two pull
requests it is talking about and the whole feed beside them. The trigger on everything is the row
whose two are empty.

The connection is a foreign key that cascades, so disconnecting an account takes its triggers with
its source rows and its pages: nothing here sweeps them, and no trigger outlives the feed it
watches.

Every statement filters `workspace_id` itself — `ExtensionContext.transaction` yields an unscoped
connection. The alert sweep runs workspace-wide, because a connection belongs to the workspace and
the conversations waking on it belong to whichever agents subscribed; a member-facing listing also
filters the selected object namespace, which defaults to the turn's agent."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ufo.sdk.audience import Audience
from ufo.sdk.context import ExtensionContext
from ufo.sdk.objects import object_agent_id

_metadata = sa.MetaData()
source_trigger = sa.Table(
    "source_trigger",
    _metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("conversation_id", sa.Uuid, nullable=False),
    sa.Column("agent_id", sa.Uuid, nullable=False),
    sa.Column("connection_id", sa.Uuid, nullable=False),
    sa.Column("resource", sa.Text, nullable=False, server_default=""),
    sa.Column("streams", sa.Text, nullable=False, server_default=""),
    sa.Column("delivery", sa.Text, nullable=False),
    sa.Column("paused", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("created_by_member_id", sa.Uuid, nullable=True),
    sa.Column("internet_access", sa.Boolean, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "workspace_id",
        "conversation_id",
        "connection_id",
        "resource",
        "streams",
        name="source_trigger_conversation",
    ),
)

_COLUMNS = (
    source_trigger.c.id,
    source_trigger.c.conversation_id,
    source_trigger.c.agent_id,
    source_trigger.c.connection_id,
    source_trigger.c.resource,
    source_trigger.c.streams,
    source_trigger.c.paused,
    source_trigger.c.created_by_member_id,
    source_trigger.c.internet_access,
    source_trigger.c.created_at,
    source_trigger.c.updated_at,
)


SourceTriggerDelivery = Literal["current"]


@dataclass(frozen=True)
class SourceTrigger:
    """One trigger as a handler reads it. A value object — never leaves the process, so a live
    capability hands it out and a wire type never mirrors it."""

    id: UUID
    conversation_id: UUID
    agent_id: UUID
    connection_id: UUID
    resource: str
    streams: tuple[str, ...]
    delivery: SourceTriggerDelivery
    paused: bool
    created_by_member_id: UUID | None
    internet_access: Literal[False] | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ListedTrigger:
    """One trigger beside the disclosure audience of the conversation that owns it — the pair every
    member-facing read decides visibility from, since who may see a trigger is a fact of where it
    fires and not of the trigger row. An alert reads `SourceTrigger` alone, so only a read that
    must answer for a member pays for the lookup."""

    trigger: SourceTrigger
    audience: Audience
    surface_label: str | None


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _trigger(row: sa.RowMapping) -> SourceTrigger:
    """One row as a handler reads it. An empty `resource` or `streams` narrows nothing. The
    column holds the sorted names joined by commas, which is what the unique key compares."""
    internet_access = row["internet_access"]
    if internet_access is True:
        raise ValueError("a source trigger internet scope can only narrow access")
    return SourceTrigger(
        id=row["id"],
        conversation_id=row["conversation_id"],
        agent_id=row["agent_id"],
        connection_id=row["connection_id"],
        resource=row["resource"],
        streams=tuple(row["streams"].split(",")) if row["streams"] else (),
        delivery="current",
        paused=bool(row["paused"]),
        created_by_member_id=row["created_by_member_id"],
        internet_access=False if internet_access is False else None,
        created_at=_utc(row["created_at"]),
        updated_at=_utc(row["updated_at"]),
    )


@dataclass(frozen=True)
class SourceTriggerStore:
    """Source-trigger rows behind the ambient workspace boundary."""

    ctx: ExtensionContext

    @property
    def workspace_id(self) -> UUID:
        return self.ctx.workspace_id

    async def create(
        self,
        conversation_id: UUID,
        connection_id: UUID,
        delivery: SourceTriggerDelivery,
        created_by_member_id: UUID,
        internet_access: Literal[False] | None = None,
        resource: str = "",
        streams: tuple[str, ...] = (),
    ) -> SourceTrigger:
        """Create one wake-up on one connection's feed, over the whole feed or over the
        resource and streams of it `resource` and `streams` name. The owning conversation is checked
        against the object namespace, so a trigger can never invoke as an agent other than its
        owner. What this conversation already watches refuses in this vocabulary rather than as a
        constraint violation: two turns can read no trigger and both write one, and the loser of
        that race is a caller to answer, not a driver error to surface."""
        agent_id = object_agent_id()
        if await self.ctx.conversation_agent(conversation_id) != agent_id:
            raise ValueError(
                "a source trigger must wake a conversation bound to its executing agent"
            )
        values = {
            "id": uuid4(),
            "workspace_id": self.workspace_id,
            "conversation_id": conversation_id,
            "agent_id": agent_id,
            "connection_id": connection_id,
            "resource": resource,
            "streams": ",".join(sorted(streams)),
            "delivery": delivery,
            "paused": False,
            "created_by_member_id": created_by_member_id,
            "internet_access": internet_access,
            "created_at": sa.func.now(),
            "updated_at": sa.func.now(),
        }
        async with self.ctx.transaction() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            statement = (
                insert(source_trigger)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=(
                        source_trigger.c.workspace_id,
                        source_trigger.c.conversation_id,
                        source_trigger.c.connection_id,
                        source_trigger.c.resource,
                        source_trigger.c.streams,
                    )
                )
                .returning(*_COLUMNS)
            )
            row = (await connection.execute(statement)).mappings().one_or_none()
        if row is None:
            watched = repr(resource) if resource else "this feed"
            narrowed = f"{watched} on {', '.join(sorted(streams))}" if streams else watched
            raise ValueError(f"this conversation already watches {narrowed}")
        return _trigger(row)

    async def set_paused(self, expected: SourceTrigger, paused: bool) -> SourceTrigger:
        """Stop this trigger waking its conversation, or start it again. Pausing is the whole of
        what a standing trigger can be changed to: the connection, resource, streams and
        conversation it names are its identity, and those are applied under a new name or not at
        all."""
        agent_id = object_agent_id()
        if expected.agent_id != agent_id:
            raise ValueError("source trigger executor changed while pausing")
        async with self.ctx.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        sa.update(source_trigger)
                        .where(
                            source_trigger.c.workspace_id == self.workspace_id,
                            source_trigger.c.id == expected.id,
                            source_trigger.c.agent_id == expected.agent_id,
                        )
                        .values(paused=paused, updated_at=sa.func.now())
                        .returning(*_COLUMNS)
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ValueError(f"source trigger {expected.id} changed while pausing")
        return _trigger(row)

    async def remove(self, expected: SourceTrigger) -> None:
        agent_id = object_agent_id()
        if expected.agent_id != agent_id:
            raise ValueError("source trigger executor changed while removing")
        async with self.ctx.transaction() as connection:
            deleted = await connection.execute(
                sa.delete(source_trigger).where(
                    source_trigger.c.workspace_id == self.workspace_id,
                    source_trigger.c.id == expected.id,
                    source_trigger.c.agent_id == expected.agent_id,
                    source_trigger.c.conversation_id == expected.conversation_id,
                    source_trigger.c.connection_id == expected.connection_id,
                )
            )
        if deleted.rowcount == 0:
            raise ValueError(f"source trigger {expected.id} changed while removing")

    async def retire_unattributed(self, expected: SourceTrigger) -> None:
        """Delete a trigger that has no member authority to fire under."""
        if expected.created_by_member_id is not None:
            raise ValueError("only an unattributed source trigger may be retired here")
        async with self.ctx.transaction() as connection:
            await connection.execute(
                sa.delete(source_trigger).where(
                    source_trigger.c.workspace_id == self.workspace_id,
                    source_trigger.c.id == expected.id,
                    source_trigger.c.created_by_member_id.is_(None),
                )
            )

    async def watched(self, conversation_id: UUID) -> frozenset[tuple[UUID, str]]:
        """The (connection, resource) pairs one conversation's narrowed triggers watch — what an
        offer to watch a link is checked against, so a conversation is never offered what it
        already has."""
        async with self.ctx.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(source_trigger.c.connection_id, source_trigger.c.resource).where(
                        source_trigger.c.workspace_id == self.workspace_id,
                        source_trigger.c.conversation_id == conversation_id,
                        source_trigger.c.resource != "",
                    )
                )
            ).all()
        return frozenset((row.connection_id, row.resource) for row in rows)

    async def pinned(self, connection_id: UUID) -> tuple[str, ...]:
        """The resources a live watch on this connection names, workspace-wide and once each — what
        the connection's sync pins a partition on, so a pull request a thread is watching is read
        every tick however quiet the repository around it is. A paused trigger names none: pausing
        stops the wake, and reading for a wake nobody gets is a request an hour spent on nothing."""
        async with self.ctx.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(source_trigger.c.resource)
                    .where(
                        source_trigger.c.workspace_id == self.workspace_id,
                        source_trigger.c.connection_id == connection_id,
                        source_trigger.c.paused == sa.false(),
                        source_trigger.c.resource != "",
                    )
                    .distinct()
                )
            ).all()
        return tuple(sorted(row.resource for row in rows))

    async def waking(self, connection_id: UUID) -> tuple[SourceTrigger, ...]:
        """Every running trigger for this connection, workspace-wide — the alert sweep's read, the
        whole-feed triggers and the narrowed ones in one pass, oldest first with the id breaking a
        tie, so one batch wakes conversations in the order they subscribed. It spans agents on
        purpose: a connection belongs to the workspace, and each row names its agent. A paused
        trigger is absent: pausing is what stops the wake, and the row stays for the member who
        resumes it."""
        query = sa.select(*_COLUMNS).where(
            source_trigger.c.workspace_id == self.workspace_id,
            source_trigger.c.connection_id == connection_id,
            source_trigger.c.paused == sa.false(),
        )
        async with self.ctx.transaction() as connection:
            rows = (await connection.execute(query)).mappings().all()
        woken = [_trigger(row) for row in rows]
        return tuple(sorted(woken, key=lambda trigger: (trigger.created_at, trigger.id)))

    async def list_reported(
        self, *, conversation_id: UUID | None = None
    ) -> tuple[ListedTrigger, ...]:
        """This agent's triggers, each beside the audience and surface label of its owning
        conversation — the read a member-facing surface answers visibility from, ordered by
        connection and then by resource so a connection's whole feed heads the resources of it. The
        facts are read live rather than snapshotted onto the row: an audience never changes, but a
        channel's label does when it is renamed, and a listing showing a channel's old name is one
        that lies. A trigger whose conversation is gone is absent from this page."""
        agent_id = object_agent_id()
        query = sa.select(*_COLUMNS).where(
            source_trigger.c.workspace_id == self.workspace_id,
            source_trigger.c.agent_id == agent_id,
        )
        if conversation_id is not None:
            query = query.where(source_trigger.c.conversation_id == conversation_id)
        async with self.ctx.transaction() as connection:
            rows = (await connection.execute(query)).mappings().all()
        listed = [_trigger(row) for row in rows]
        triggers = tuple(
            sorted(
                listed,
                key=lambda trigger: (
                    trigger.connection_id,
                    trigger.resource,
                    trigger.streams,
                    trigger.id,
                ),
            )
        )
        if not triggers:
            return ()
        facts = await self.ctx.conversation_facts(
            tuple({trigger.conversation_id for trigger in triggers})
        )
        return tuple(
            ListedTrigger(
                trigger=trigger,
                audience=facts[trigger.conversation_id].audience,
                surface_label=facts[trigger.conversation_id].surface_label,
            )
            for trigger in triggers
            if trigger.conversation_id in facts
        )
