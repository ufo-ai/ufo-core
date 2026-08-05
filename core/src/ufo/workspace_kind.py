"""The `workspace` object kind: this workspace's seat shape, read-only.

One `workspace` row is one instance, named by its id. Nothing it reports is authored: `seat_limit`
is the grantable ceiling and `included_seats` the silent auto-seat allowance, both established once
from the purchased plan (`Seats.ensure_limit` and `ensure_included` write only while the column is
NULL) and both NULL on every deploy without a billing extension; the counts beside them are derived
from the member rows. So the spec carries no field at all — the schema `object_explain` renders is
the form a caller may fill, and here there is nothing to fill — and the whole shape is status.
Seating one member is the `member` kind's admin-gated apply; the bounds move with the plan.

Status names who holds a seat, so any member reads the roster the way they always have. Reads
answer an internal conversation only: an externally shared channel never hears the seat shape,
exactly as the `member` kind's roster does not travel there.

The loader binds it with no extension context, so the handlers read the ambient workspace
directly."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from ufo.audience import FOREIGN_AUDIENCE_PREFIX
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
from ufo.tools.context import ToolContext
from ufo.workspace import ws_current

WORKSPACE_KIND = "workspace"
SEAT_BOUNDS_ARE_PLAN_OWNED = (
    "a workspace's seat bounds come from its plan, not from a manifest — grant or revoke one "
    "member's seat by applying `seated` on that member object"
)
WORKSPACE_IS_PERMANENT = "a workspace is created at first run and is never deleted"


class WorkspaceSpec(BaseModel):
    """The `workspace` kind accepts no authored field. Its bounds are the plan's and its counts are
    derived, so the spec is empty and every value the kind reports is status."""

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class SeatHolder:
    """One member as the seat roster names them: who they are, whether the agent answers them, and
    whether they administer the workspace."""

    email: str
    seated: bool
    admin: bool


@dataclass(frozen=True)
class WorkspaceShape:
    """The one read every verb of this kind answers from: the two plan-established bounds, the
    member counts they gate, the seats billed beyond the included allowance, the roster naming who
    holds one, and the row's own timestamps."""

    seat_limit: int | None
    included_seats: int | None
    members: int
    seated: int
    billed_overage_seats: int
    roster: tuple[SeatHolder, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class WorkspaceObjects:
    """Read-only handlers over the bound workspace's own row: one instance named by its id, listed
    and read with its seat bounds and member counts, and every mutation refused."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        if ctx.audience.startswith(FOREIGN_AUDIENCE_PREFIX):
            return object_page((), query)
        shape = await self._shape()
        limit = "no seat limit" if shape.seat_limit is None else f"seat limit {shape.seat_limit}"
        return object_page(
            (
                ObjectRow(
                    name=str(ws_current().workspace_id),
                    summary=f"{shape.members} members, {shape.seated} seated, {limit}",
                    fields={
                        "seat_limit": shape.seat_limit,
                        "included_seats": shape.included_seats,
                        "members": shape.members,
                        "seated": shape.seated,
                    },
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
        return {
            "seat_limit": shape.seat_limit,
            "included_seats": shape.included_seats,
            "members": shape.members,
            "seated": shape.seated,
            "billed_overage_seats": shape.billed_overage_seats,
            "roster": [
                {"email": holder.email, "seated": holder.seated, "admin": holder.admin}
                for holder in shape.roster
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
        raise VerbNotSupported(SEAT_BOUNDS_ARE_PLAN_OWNED)

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
                        tables.workspace.c.seat_limit,
                        tables.workspace.c.included_seats,
                        tables.workspace.c.created_at,
                        tables.workspace.c.updated_at,
                    ).where(tables.workspace.c.id == workspace_id)
                )
            ).one()
            members = (
                await connection.execute(
                    sa.select(
                        tables.member.c.email,
                        tables.member.c.seated_at,
                        tables.member.c.is_admin,
                    )
                    .where(tables.member.c.workspace_id == workspace_id)
                    .order_by(tables.member.c.email)
                )
            ).all()
        seated = sum(1 for member in members if member.seated_at is not None)
        return WorkspaceShape(
            seat_limit=row.seat_limit,
            included_seats=row.included_seats,
            members=len(members),
            seated=seated,
            billed_overage_seats=(
                0 if row.included_seats is None else max(0, seated - row.included_seats)
            ),
            roster=tuple(
                SeatHolder(
                    email=member.email,
                    seated=member.seated_at is not None,
                    admin=member.is_admin,
                )
                for member in members
            ),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


WORKSPACE_OBJECT = ObjectKind(
    name=WORKSPACE_KIND,
    description=(
        "Show the workspace's seat limit and who holds a seat: the limit, the included allowance, "
        "the seats billed as overage, and the roster. Read-only — the bounds come from the plan "
        "and a member's seat changes on the member object."
    ),
    guidance=(
        "One object per workspace, named by the workspace id, so a listing returns exactly one "
        "row. Listings filter and order on `seat_limit`, `included_seats`, `members`, and "
        "`seated`; status carries those four plus `billed_overage_seats` and `roster`, which "
        "names every member with whether they hold a seat and whether they administer the "
        "workspace. Any member may read it. A null `seat_limit` and a null `included_seats` mean "
        "the workspace is ungated: every member is answered and no seat is counted. "
        "`included_seats` bounds the seats handed out silently at member creation; `seat_limit` "
        "is the ceiling an admin may grant up to, and every seat past the included allowance "
        "bills as overage on the invoice. The spec is empty because nothing here is authored: "
        "create and update are refused, and so is delete. Seat one member by applying "
        "{seated: true} on their member object; raising either bound is a plan change, not a chat "
        "act. Reads answer an internal conversation only — an externally shared channel lists "
        "nothing."
    ),
    spec_model=WorkspaceSpec,
    store=WorkspaceObjects(),
    list_fields=frozenset({"seat_limit", "included_seats", "members", "seated"}),
)
