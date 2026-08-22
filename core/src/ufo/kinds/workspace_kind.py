"""The `workspace` object kind: who this workspace answers, read-only.

One `workspace` row is one instance, named by its id. Nothing it reports is authored: the member
count and the seated count are derived from the member rows, and nothing bounds either — the
workspace pays one flat fee and its members are unlimited. So the spec carries no field at all —
the schema `object_explain` renders is the form a caller may fill, and here there is nothing to
fill — and the whole shape is status. Seating one member, or unseating them to remove their
access, is the `member` kind's admin-gated apply.

Status names who holds a seat, under the roster's own rule: a member asking the main agent in an
internal conversation reads every colleague, and a child agent answers the speaker's own row
alone, exactly as the `member` kind does.

The loader binds it with no extension context, so the handlers read the ambient workspace
directly."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from ufo.db import workspace_tx
from ufo.ext.context import JsonValue
from ufo.objects import (
    ObjectDetail,
    ObjectKind,
    ObjectListQuery,
    ObjectPage,
    ObjectRow,
    VerbNotSupported,
    object_page,
)
from ufo.schema import tables
from ufo.seats import SeatEntry, Seats
from ufo.tools.context import ToolContext
from ufo.turns.audience import FOREIGN_AUDIENCE_PREFIX
from ufo.workspace import ws_current

WORKSPACE_KIND = "workspace"
SEATS_ARE_THE_MEMBER_KINDS = (
    "a workspace carries nothing authored — grant or revoke one member's seat by applying "
    "`seated` on that member object"
)
WORKSPACE_IS_PERMANENT = "a workspace is created at first run and is never deleted"


class WorkspaceSpec(BaseModel):
    """The `workspace` kind accepts no authored field. Its counts are derived, so the spec is empty
    and every value the kind reports is status."""

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class WorkspaceShape:
    """The one read every verb of this kind answers from: the member count, how many of them hold
    a seat, the roster naming which, and the row's own timestamps."""

    members: int
    seated: int
    roster: tuple[SeatEntry, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class WorkspaceObjects:
    """Read-only handlers over the bound workspace's own row: one instance named by its id, listed
    and read with its member counts, and every mutation refused."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        if ctx.audience.startswith(FOREIGN_AUDIENCE_PREFIX):
            return object_page((), query)
        shape = await self._shape()
        return object_page(
            (
                ObjectRow(
                    name=str(ws_current().workspace_id),
                    summary=f"{shape.members} members, {shape.seated} seated",
                    fields={"members": shape.members, "seated": shape.seated},
                ),
            ),
            query,
        )

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WorkspaceSpec] | None:
        if ctx.audience.startswith(FOREIGN_AUDIENCE_PREFIX):
            return None
        if name != str(ws_current().workspace_id):
            return None
        shape = await self._shape()
        return ObjectDetail(
            spec=WorkspaceSpec(),
            created_at=shape.created_at,
            updated_at=shape.updated_at,
        )

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
        if ctx.audience.startswith(FOREIGN_AUDIENCE_PREFIX):
            return None
        if name != str(ws_current().workspace_id):
            return None
        shape = await self._shape()
        whole = ctx.speaker_member_id is not None and await ctx.agent_is_main()
        return {
            "members": shape.members,
            "seated": shape.seated,
            "roster": [
                {"email": entry.email, "seated": entry.seated, "admin": entry.admin}
                for entry in shape.roster
                if whole or entry.id == ctx.speaker_member_id
            ],
        }

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: WorkspaceSpec,
        old: WorkspaceSpec | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(SEATS_ARE_THE_MEMBER_KINDS)

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(WORKSPACE_IS_PERMANENT)

    async def _shape(self) -> WorkspaceShape:
        workspace_id = ws_current().workspace_id
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.workspace.c.created_at,
                        tables.workspace.c.updated_at,
                    ).where(tables.workspace.c.id == workspace_id)
                )
            ).one()
            snapshot = await Seats(workspace_id).snapshot(connection)
        return WorkspaceShape(
            members=len(snapshot.members),
            seated=snapshot.seated,
            roster=snapshot.members,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


WORKSPACE_OBJECT = ObjectKind(
    name=WORKSPACE_KIND,
    description=(
        "Show how many members this workspace has and who holds a seat. Read-only — a member's "
        "seat changes on the member object."
    ),
    guidance=(
        "One object per workspace, named by the workspace id, so a listing returns exactly one "
        "row. Listings filter and order on `members` and `seated`; status carries both plus "
        "`roster`, which names every member with whether they hold a seat and whether they "
        "administer the workspace. A member asking the main agent in an internal conversation "
        "reads the whole roster; a child agent answers the speaker's own row alone. Members are "
        "unlimited and nothing is billed per head, so the counts are figures to report and never "
        "a limit to check before adding someone. A member holds a seat from the moment they are "
        "created and the agent answers them; an admin unseats one to remove that person's "
        "access, which is the only way to remove it. The spec is empty because nothing here is "
        "authored: create and update are refused, and so is delete. Seat or unseat one member by "
        "applying {seated: true} or {seated: false} on their member object. Reads answer an "
        "internal conversation only — an externally shared channel lists nothing."
    ),
    spec_model=WorkspaceSpec,
    store=WorkspaceObjects(),
    list_fields=frozenset({"members", "seated"}),
)
