"""The source-trigger tables and the scoped store that owns them.

A trigger is one conversation's standing interest in one shared source. It can send each batch to
that conversation or partition changes into one stable agent conversation per page. The row carries
the owning conversation, the agent it invokes, the delivery mode, and the member who asked for it.

A trigger narrows to one resource of that source — a pull request, an issue — and a narrowed one is
a row of `source_resource_watch`, keyed by the conversation, the binding and the resource, so a
thread watches the two pull requests it is talking about and the whole feed beside them. Those rows
keep a table of their own because `source_trigger` keys one row per (workspace, conversation,
binding) and the release this one replaces inserts into it with `ON CONFLICT (workspace_id,
conversation_id, binding)`: that key answers the outgoing image through the roll, so a second row
over one pair cannot live in that table. The store reads both and hands out one `SourceTrigger`
either way — a caller asks what a conversation watches, not which table holds it.

Every statement filters `workspace_id` itself — `ExtensionContext.transaction` yields an unscoped
connection. The alert sweep and a binding's removal run workspace-wide, because a source belongs to
the workspace and the conversations waking on it belong to whichever agents subscribed; a
member-facing listing also filters the selected object namespace, which defaults to the turn's
agent."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

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
    sa.Column("binding", sa.Text, nullable=False),
    sa.Column("delivery", sa.Text, nullable=False),
    sa.Column("created_by_member_id", sa.Uuid, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "workspace_id", "conversation_id", "binding", name="source_trigger_conversation"
    ),
)

source_resource_watch = sa.Table(
    "source_resource_watch",
    _metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("conversation_id", sa.Uuid, nullable=False),
    sa.Column("agent_id", sa.Uuid, nullable=False),
    sa.Column("binding", sa.Text, nullable=False),
    sa.Column("resource", sa.Text, nullable=False),
    sa.Column("delivery", sa.Text, nullable=False),
    sa.Column("created_by_member_id", sa.Uuid, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "workspace_id",
        "conversation_id",
        "binding",
        "resource",
        name="source_resource_watch_resource",
    ),
)

_COLUMNS = (
    source_trigger.c.id,
    source_trigger.c.conversation_id,
    source_trigger.c.agent_id,
    source_trigger.c.binding,
    source_trigger.c.delivery,
    source_trigger.c.created_by_member_id,
    source_trigger.c.created_at,
    source_trigger.c.updated_at,
)

_WATCH_COLUMNS = (
    source_resource_watch.c.id,
    source_resource_watch.c.conversation_id,
    source_resource_watch.c.agent_id,
    source_resource_watch.c.binding,
    source_resource_watch.c.resource,
    source_resource_watch.c.delivery,
    source_resource_watch.c.created_by_member_id,
    source_resource_watch.c.created_at,
    source_resource_watch.c.updated_at,
)


SourceTriggerDelivery = Literal["current", "per_page"]


@dataclass(frozen=True)
class SourceTrigger:
    """One trigger as a handler reads it. A value object — never leaves the process, so a live
    capability hands it out and a wire type never mirrors it."""

    id: UUID
    conversation_id: UUID
    agent_id: UUID
    binding: str
    resource: str
    delivery: SourceTriggerDelivery
    created_by_member_id: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ListedTrigger:
    """One trigger beside the disclosure audience of the conversation that owns it — the pair every
    member-facing read decides visibility from, since who may see a trigger is a fact of where it
    fires and not of the trigger row. An alert reads `SourceTrigger` alone, so only a read that
    must answer for a member pays for the lookup."""

    trigger: SourceTrigger
    audience: str
    surface_label: str | None


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _trigger(row: sa.RowMapping) -> SourceTrigger:
    """One row of either table as a handler reads it. A `source_trigger` row carries no `resource`
    column — that table keeps the shape the release being replaced writes — and a row of it watches
    the whole binding, which is what the empty key says."""
    match row["delivery"]:
        case "current" | "per_page" as delivery:
            pass
        case value:
            raise ValueError(f"unknown source trigger delivery {value!r}")
    return SourceTrigger(
        id=row["id"],
        conversation_id=row["conversation_id"],
        agent_id=row["agent_id"],
        binding=row["binding"],
        resource=row.get("resource", ""),
        delivery=delivery,
        created_by_member_id=row["created_by_member_id"],
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
        binding: str,
        delivery: SourceTriggerDelivery,
        created_by_member_id: UUID | None = None,
        resource: str = "",
    ) -> SourceTrigger:
        """Create one delivery rule on one binding, over the whole binding or over the one resource
        of it `resource` names. The owning conversation is checked against the
        object namespace, so a trigger can never invoke as an agent other than its owner. A
        pair already watched refuses in this vocabulary rather than as a constraint violation: two
        turns can read no trigger and both write one, and the loser of that race is a caller to
        answer, not a driver error to surface."""
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
            "binding": binding,
            "delivery": delivery,
            "created_by_member_id": created_by_member_id,
            "created_at": sa.func.now(),
            "updated_at": sa.func.now(),
        }
        async with self.ctx.transaction() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            if resource:
                statement = (
                    insert(source_resource_watch)
                    .values(**values, resource=resource)
                    .on_conflict_do_nothing(
                        index_elements=(
                            source_resource_watch.c.workspace_id,
                            source_resource_watch.c.conversation_id,
                            source_resource_watch.c.binding,
                            source_resource_watch.c.resource,
                        )
                    )
                    .returning(*_WATCH_COLUMNS)
                )
            else:
                statement = (
                    insert(source_trigger)
                    .values(**values)
                    .on_conflict_do_nothing(
                        index_elements=(
                            source_trigger.c.workspace_id,
                            source_trigger.c.conversation_id,
                            source_trigger.c.binding,
                        )
                    )
                    .returning(*_COLUMNS)
                )
            row = (await connection.execute(statement)).mappings().one_or_none()
        if row is None:
            watched = f"{resource} on {binding}" if resource else binding
            raise ValueError(f"this conversation already watches {watched!r}")
        return _trigger(row)

    async def remove(self, expected: SourceTrigger) -> None:
        agent_id = object_agent_id()
        if expected.agent_id != agent_id:
            raise ValueError("source trigger executor changed while removing")
        table = source_resource_watch if expected.resource else source_trigger
        async with self.ctx.transaction() as connection:
            deleted = await connection.execute(
                sa.delete(table).where(
                    table.c.workspace_id == self.workspace_id,
                    table.c.id == expected.id,
                    table.c.agent_id == expected.agent_id,
                    table.c.conversation_id == expected.conversation_id,
                    table.c.binding == expected.binding,
                )
            )
        if deleted.rowcount == 0:
            raise ValueError(f"source trigger on {expected.binding!r} changed while removing")

    async def remove_binding(self, binding: str) -> None:
        """Drop every workspace trigger on one binding — what a source's removal takes with it,
        across every agent that subscribed, since nothing else ever will. The resource watches on
        that binding go with them: the feed their changes would have arrived on is gone."""
        async with self.ctx.transaction() as connection:
            for table in (source_trigger, source_resource_watch):
                await connection.execute(
                    sa.delete(table).where(
                        table.c.workspace_id == self.workspace_id,
                        table.c.binding == binding,
                    )
                )

    async def watched(self, conversation_id: UUID) -> frozenset[tuple[str, str]]:
        """The (binding, resource) pairs one conversation's narrowed triggers watch — what an offer
        to watch a link is checked against, so a conversation is never offered what it already
        has."""
        async with self.ctx.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        source_resource_watch.c.binding, source_resource_watch.c.resource
                    ).where(
                        source_resource_watch.c.workspace_id == self.workspace_id,
                        source_resource_watch.c.conversation_id == conversation_id,
                    )
                )
            ).all()
        return frozenset((row.binding, row.resource) for row in rows)

    async def waking(self, binding: str) -> tuple[SourceTrigger, ...]:
        """Every delivery rule for this binding, workspace-wide — the alert sweep's read, over both
        the whole-binding triggers and the resource watches. It spans agents on purpose: a source
        belongs to the workspace, and each row names its agent."""
        whole = sa.select(*_COLUMNS).where(
            source_trigger.c.workspace_id == self.workspace_id,
            source_trigger.c.binding == binding,
        )
        narrowed = sa.select(*_WATCH_COLUMNS).where(
            source_resource_watch.c.workspace_id == self.workspace_id,
            source_resource_watch.c.binding == binding,
        )
        async with self.ctx.transaction() as connection:
            rows = (await connection.execute(whole)).mappings().all()
            watches = (await connection.execute(narrowed)).mappings().all()
        woken = [_trigger(row) for row in (*rows, *watches)]
        return tuple(sorted(woken, key=lambda trigger: (trigger.created_at, trigger.id)))

    async def list_reported(
        self, *, conversation_id: UUID | None = None
    ) -> tuple[ListedTrigger, ...]:
        """This agent's triggers, each beside the audience and surface label of its owning
        conversation — the read a member-facing surface answers visibility from. The facts are
        read live rather than snapshotted onto the row: an audience never changes, but a channel's
        label does when it is renamed, and a listing showing a channel's old name is one that lies.
        A trigger whose conversation is gone is absent from this page."""
        agent_id = object_agent_id()
        whole = sa.select(*_COLUMNS).where(
            source_trigger.c.workspace_id == self.workspace_id,
            source_trigger.c.agent_id == agent_id,
        )
        narrowed = sa.select(*_WATCH_COLUMNS).where(
            source_resource_watch.c.workspace_id == self.workspace_id,
            source_resource_watch.c.agent_id == agent_id,
        )
        if conversation_id is not None:
            whole = whole.where(source_trigger.c.conversation_id == conversation_id)
            narrowed = narrowed.where(source_resource_watch.c.conversation_id == conversation_id)
        async with self.ctx.transaction() as connection:
            rows = (await connection.execute(whole)).mappings().all()
            watches = (await connection.execute(narrowed)).mappings().all()
        listed = [_trigger(row) for row in (*rows, *watches)]
        triggers = tuple(
            sorted(listed, key=lambda trigger: (trigger.binding, trigger.resource, trigger.id))
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
