"""The source-trigger table and the scoped store that owns it.

A trigger is one conversation's standing interest in one shared source — the durable form of "wake
this agent, here, when that source's content changes". The row carries the conversation the alert
re-enters, the agent it re-enters as, and the member who asked for it.

Every statement filters `workspace_id` itself — `ExtensionContext.transaction` yields an unscoped
connection. The alert sweep and a binding's removal run workspace-wide, because a source belongs to
the workspace and the conversations waking on it belong to whichever agents subscribed; a
member-facing listing also filters the selected object namespace, which defaults to the turn's
agent."""

from dataclasses import dataclass
from datetime import UTC, datetime
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
    sa.Column("created_by_member_id", sa.Uuid, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "workspace_id", "conversation_id", "binding", name="source_trigger_conversation"
    ),
)

_COLUMNS = (
    source_trigger.c.id,
    source_trigger.c.conversation_id,
    source_trigger.c.agent_id,
    source_trigger.c.binding,
    source_trigger.c.created_by_member_id,
    source_trigger.c.created_at,
    source_trigger.c.updated_at,
)


@dataclass(frozen=True)
class SourceTrigger:
    """One trigger as a handler reads it. A value object — never leaves the process, so a live
    capability hands it out and a wire type never mirrors it."""

    id: UUID
    conversation_id: UUID
    agent_id: UUID
    binding: str
    created_by_member_id: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ListedTrigger:
    """One trigger beside the disclosure audience of the conversation it wakes — the pair every
    member-facing read decides visibility from, since who may see a trigger is a fact of where it
    fires and not of the trigger row. An alert reads `SourceTrigger` alone, so only a read that
    must answer for a member pays for the lookup."""

    trigger: SourceTrigger
    audience: str
    surface_label: str | None


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _trigger(row: sa.RowMapping) -> SourceTrigger:
    return SourceTrigger(
        id=row["id"],
        conversation_id=row["conversation_id"],
        agent_id=row["agent_id"],
        binding=row["binding"],
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
        created_by_member_id: UUID | None = None,
    ) -> SourceTrigger:
        """Wake one conversation on one binding. The conversation is checked against the object
        namespace, so a trigger can never name a conversation another agent answers — the alert
        re-enters as this agent, and an agent that cannot speak there would fire into nothing. A
        pair already watched refuses in this vocabulary rather than as a constraint violation: two
        turns can read no trigger and both write one, and the loser of that race is a caller to
        answer, not a driver error to surface."""
        agent_id = object_agent_id()
        if await self.ctx.conversation_agent(conversation_id) != agent_id:
            raise ValueError(
                "a source trigger must wake a conversation bound to its executing agent"
            )
        async with self.ctx.transaction() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            row = (
                (
                    await connection.execute(
                        insert(source_trigger)
                        .values(
                            id=uuid4(),
                            workspace_id=self.workspace_id,
                            conversation_id=conversation_id,
                            agent_id=agent_id,
                            binding=binding,
                            created_by_member_id=created_by_member_id,
                            created_at=sa.func.now(),
                            updated_at=sa.func.now(),
                        )
                        .on_conflict_do_nothing(
                            index_elements=(
                                source_trigger.c.workspace_id,
                                source_trigger.c.conversation_id,
                                source_trigger.c.binding,
                            )
                        )
                        .returning(*_COLUMNS)
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ValueError(f"this conversation already watches {binding!r}")
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
                    source_trigger.c.binding == expected.binding,
                )
            )
        if deleted.rowcount == 0:
            raise ValueError(f"source trigger on {expected.binding!r} changed while removing")

    async def remove_binding(self, binding: str) -> None:
        """Drop every workspace trigger on one binding — what a source's removal takes with it,
        across every agent that subscribed, since nothing else ever will."""
        async with self.ctx.transaction() as connection:
            await connection.execute(
                sa.delete(source_trigger).where(
                    source_trigger.c.workspace_id == self.workspace_id,
                    source_trigger.c.binding == binding,
                )
            )

    async def waking(self, binding: str) -> tuple[SourceTrigger, ...]:
        """Every conversation this binding wakes, workspace-wide — the alert sweep's read. It spans
        agents on purpose: a source belongs to the workspace, and the row already names the agent
        each alert re-enters as."""
        query = (
            sa.select(*_COLUMNS)
            .where(
                source_trigger.c.workspace_id == self.workspace_id,
                source_trigger.c.binding == binding,
            )
            .order_by(source_trigger.c.created_at, source_trigger.c.id)
        )
        async with self.ctx.transaction() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return tuple(_trigger(row) for row in rows)

    async def list_reported(
        self, *, conversation_id: UUID | None = None
    ) -> tuple[ListedTrigger, ...]:
        """This agent's triggers, each beside the audience and surface label of the conversation it
        wakes — the read a member-facing surface answers visibility from. The conversation facts are
        read live rather than snapshotted onto the row: an audience never changes, but a channel's
        label does when it is renamed, and a listing showing a channel's old name is one that lies.
        A trigger whose conversation is gone is absent from this page."""
        query = (
            sa.select(*_COLUMNS)
            .where(
                source_trigger.c.workspace_id == self.workspace_id,
                source_trigger.c.agent_id == object_agent_id(),
            )
            .order_by(source_trigger.c.binding, source_trigger.c.id)
        )
        if conversation_id is not None:
            query = query.where(source_trigger.c.conversation_id == conversation_id)
        async with self.ctx.transaction() as connection:
            rows = (await connection.execute(query)).mappings().all()
        triggers = tuple(_trigger(row) for row in rows)
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
