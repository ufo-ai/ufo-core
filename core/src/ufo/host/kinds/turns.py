"""The core-registered `turn` object kind: the selected agent's turns as read-only objects.

A turn is the unit of work under a conversation — one admission, one status, one closing reply —
and the runs list is a listing of them: a turn that an object fired on its own carries `fired_by`
from admission, so `fired=true` is every scheduled task's fire and every trigger's wake across the
workspace, newest first, a turn still running included. The fence is the reader's own audiences,
the same fence every turn read holds, and it never widens for an admin. Turns exist by admission,
so every mutation is refused."""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from ufo.db import workspace_tx
from ufo.host.kinds.conversations import CONVERSATION_KIND
from ufo.runtime.ext.context import ExtensionContext, JsonValue
from ufo.runtime.object_name import ObjectRef
from ufo.runtime.object_scope import object_agent_id
from ufo.runtime.objects import (
    MemberObject,
    ObjectDetail,
    ObjectKind,
    ObjectLink,
    ObjectListQuery,
    ObjectPage,
    ObjectRow,
    VerbNotSupported,
    object_page,
)
from ufo.runtime.tools.context import ToolContext
from ufo.runtime.turns.audience import readable_audiences
from ufo.runtime.workspace import ws_current
from ufo.schema import tables
from ufo.schema.records import TerminalFrame

TURN_KIND = "turn"
TURN_LIST_MAX = 200
"""How many of an agent's newest turns one listing read materializes — the pages behind it read
through the cursor, and the SQL narrowings below keep the window meaningful under a filter."""
TEXT_EXCERPT_MAX = 400
TURNS_ARE_ADMITTED = "a turn exists by admission — say something, or let a task or trigger fire"
PORTAL_ORIGIN = "Portal"
FIRED_FIELD = "fired"
SOURCE_FIELD = "source"
SOURCE_NAME_FIELD = "source_name"
STATUS_FIELD = "status"
CONVERSATION_FIELD = "conversation"


class TurnSpec(BaseModel):
    """A turn has no applied configuration: it is the record of an admission, so its spec carries
    nothing and every mutation is refused."""

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class TurnObjects:
    """Read handlers over the selected agent's turns the caller's audiences admit, in a turn and —
    through `member_page` and `member_detail` — for a signed-in member outside one."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        return object_page(
            _rows(await _turns(ctx.read_subjects, ctx.turn.agent_id, query=query)), query
        )

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[TurnSpec] | None:
        found = await _one(ctx.read_subjects, ctx.turn.agent_id, name)
        return None if found is None else found.detail

    async def member_page(
        self,
        ext: ExtensionContext | None,
        *,
        member_id: UUID,
        admin: bool,
        query: ObjectListQuery,
    ) -> ObjectPage:
        subjects = frozenset(str(audience) for audience in readable_audiences(member_id))
        return object_page(_rows(await _turns(subjects, object_agent_id(), query=query)), query)

    async def member_detail(
        self,
        ext: ExtensionContext | None,
        name: str,
        *,
        member_id: UUID,
        admin: bool,
    ) -> MemberObject[TurnSpec] | None:
        subjects = frozenset(str(audience) for audience in readable_audiences(member_id))
        return await _one(subjects, object_agent_id(), name)

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
        return None

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: TurnSpec,
        old: TurnSpec | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(TURNS_ARE_ADMITTED)

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(TURNS_ARE_ADMITTED)


async def _one(
    subjects: frozenset[str], agent_id: UUID, name: str
) -> MemberObject[TurnSpec] | None:
    try:
        turn_id = UUID(name)
    except ValueError:
        return None
    rows = await _turns(subjects, agent_id, turn_id=turn_id)
    if not rows:
        return None
    (row,) = rows
    links = [
        ObjectLink(
            relation="created_in",
            target=ObjectRef(kind=CONVERSATION_KIND, name=str(row.conversation_id)),
        )
    ]
    if row.fired_by_kind is not None:
        links.append(
            ObjectLink(
                relation="created_from",
                target=ObjectRef(kind=row.fired_by_kind, name=row.fired_by_name),
            )
        )
    return MemberObject(
        row=_row(row),
        detail=ObjectDetail(
            spec=TurnSpec(),
            created_at=_utc(row.created_at),
            updated_at=_utc(row.updated_at),
            links=tuple(links),
        ),
    )


async def _turns(
    subjects: frozenset[str],
    agent_id: UUID | None,
    *,
    query: ObjectListQuery | None = None,
    turn_id: UUID | None = None,
) -> tuple[sa.Row, ...]:
    """The newest turns the subjects read, narrowed in SQL on the filters a listing names — `fired`,
    `source`, `source_name`, `status`, `conversation` — so a task's sparse runs are the window
    rather than a slice of one full of member turns. `object_page` applies the same filters again
    over the window, which is how it also answers the ones no narrowing here names."""
    stmt = (
        sa.select(
            tables.turn.c.id,
            tables.turn.c.conversation_id,
            tables.turn.c.agent_id,
            tables.turn.c.status,
            tables.turn.c.admission_source,
            tables.turn.c.fired_by_kind,
            tables.turn.c.fired_by_name,
            tables.turn.c.fired_by_title,
            tables.turn.c.terminal,
            tables.turn.c.created_at,
            tables.turn.c.updated_at,
            tables.conversation.c.surface,
            tables.conversation.c.surface_label,
        )
        .select_from(
            tables.turn.join(
                tables.conversation, tables.turn.c.conversation_id == tables.conversation.c.id
            )
        )
        .where(
            tables.turn.c.workspace_id == ws_current().workspace_id,
            tables.conversation.c.audience.in_(subjects),
        )
    )
    if agent_id is not None:
        stmt = stmt.where(tables.turn.c.agent_id == agent_id)
    if turn_id is not None:
        stmt = stmt.where(tables.turn.c.id == turn_id)
    if query is not None:
        stmt = _narrowed(stmt, query.filters)
    stmt = stmt.order_by(tables.turn.c.created_at.desc(), tables.turn.c.id.desc()).limit(
        TURN_LIST_MAX
    )
    async with workspace_tx() as connection:
        return tuple((await connection.execute(stmt)).all())


async def last_fires(kind: str, names: Collection[str]) -> dict[str, datetime]:
    """When each named object of `kind` last fired — the newest turn carrying it as `fired_by`.

    A scheduled task keeps its own `last_run_at`; a source trigger keeps none, because a trigger's
    run history is the turns it woke. A kind whose rows sort beside the other's on one last-run
    column asks here rather than growing a column of its own. The caller has already answered
    whether this reader may see the row it stamps, so the fence here is the workspace."""
    wanted = tuple(names)
    if not wanted:
        return {}
    stmt = (
        sa.select(
            tables.turn.c.fired_by_name,
            sa.func.max(tables.turn.c.created_at).label("fired_at"),
        )
        .where(
            tables.turn.c.workspace_id == ws_current().workspace_id,
            tables.turn.c.fired_by_kind == kind,
            tables.turn.c.fired_by_name.in_(wanted),
        )
        .group_by(tables.turn.c.fired_by_name)
    )
    async with workspace_tx() as connection:
        rows = (await connection.execute(stmt)).all()
    return {row.fired_by_name: _utc(row.fired_at) for row in rows}


def _narrowed(stmt: sa.Select[Any], filters: Mapping[str, JsonValue]) -> sa.Select[Any]:
    match filters.get(FIRED_FIELD):
        case True:
            stmt = stmt.where(tables.turn.c.fired_by_kind.is_not(None))
        case False:
            stmt = stmt.where(tables.turn.c.fired_by_kind.is_(None))
    match filters.get(SOURCE_FIELD):
        case str() as source:
            stmt = stmt.where(tables.turn.c.fired_by_kind == source)
    match filters.get(SOURCE_NAME_FIELD):
        case str() as source_name:
            stmt = stmt.where(tables.turn.c.fired_by_name == source_name)
    match filters.get(STATUS_FIELD):
        case str() as status:
            stmt = stmt.where(tables.turn.c.status == status)
    match filters.get(CONVERSATION_FIELD):
        case str() as conversation:
            try:
                stmt = stmt.where(tables.turn.c.conversation_id == UUID(conversation))
            except ValueError:
                stmt = stmt.where(sa.false())
    return stmt


def _rows(rows: tuple[sa.Row, ...]) -> tuple[ObjectRow, ...]:
    return tuple(_row(row) for row in rows)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _row(row: sa.Row) -> ObjectRow:
    fired = row.fired_by_kind is not None
    title = row.fired_by_title if fired else row.admission_source
    created_at = _utc(row.created_at)
    return ObjectRow(
        name=str(row.id),
        summary=f"{title}, {row.status}, {created_at.strftime('%Y-%m-%d %H:%M')}",
        fields={
            CONVERSATION_FIELD: str(row.conversation_id),
            "agent_id": str(row.agent_id),
            STATUS_FIELD: row.status,
            "admission": row.admission_source,
            FIRED_FIELD: fired,
            SOURCE_FIELD: row.fired_by_kind,
            SOURCE_NAME_FIELD: row.fired_by_name,
            "title": row.fired_by_title,
            "created_at": created_at.isoformat(),
            "surface": row.surface,
            "origin": row.surface_label or PORTAL_ORIGIN,
            "text": (
                ""
                if row.terminal is None
                else TerminalFrame.model_validate(row.terminal).text[:TEXT_EXCERPT_MAX]
            ),
        },
    )


TURN_OBJECT = ObjectKind(
    name=TURN_KIND,
    description=(
        "One turn of the selected agent: its conversation, status, how it was admitted, and — "
        "for a scheduled task's fire or a trigger's wake — what fired it. Read-only."
    ),
    guidance=(
        "The agent's turns, named by turn id, newest first under order_by=created_at desc; "
        "each carries conversation, status (queued, running, parked, done, failed, cancelled), "
        "admission (member, internal, scheduled, intent), fired, source and source_name (the "
        "kind and name of the scheduled task or source trigger that fired it, null for a turn a "
        "member or the platform admitted), title, created_at, surface, origin (the "
        "conversation's surface label, else Portal), and text (a finished turn's closing reply, "
        "empty while it runs). Filter fired=true for the runs of scheduled work, "
        "source_name=<name> for one task's or trigger's runs, status=running for work in flight, "
        "or conversation=<id> "
        "for one conversation's turns. Get one by id for its created_in conversation and "
        "created_from task or trigger links. Reads stay inside the reader's own audiences and "
        "never widen for an admin. Turns cannot be created, changed, or deleted through objects."
    ),
    spec_model=TurnSpec,
    store=TurnObjects(),
    list_fields=frozenset(
        {
            CONVERSATION_FIELD,
            "agent_id",
            STATUS_FIELD,
            "admission",
            FIRED_FIELD,
            SOURCE_FIELD,
            SOURCE_NAME_FIELD,
            "title",
            "created_at",
            "surface",
            "origin",
            "text",
        }
    ),
)
