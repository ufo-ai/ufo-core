"""The `workspace` object kind: its membership settings and access state.

One `workspace` row is one instance, named by its id. The one member-authored field is
`members_can_add`, which a speaking admin on the main agent applies. Member and seat counts are
derived from the member rows. The suspension action is deploy-gated and limited to a speaking fleet
operator; it can address any workspace id and blocks every new turn while that workspace remains
suspended.

Status names who holds a seat, under the roster's own rule: a member asking the main agent in an
internal conversation reads every colleague, and a child agent answers the speaker's own row alone.

The loader binds it with no extension context, so the handlers read the ambient workspace
directly."""

import os
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field

from ufo.db import workspace_tx
from ufo.runtime.ext.json_value import JsonValue
from ufo.runtime.ext.operator import installed_operator
from ufo.runtime.objects import (
    AdminRequired,
    ObjectDetail,
    ObjectKind,
    ObjectListQuery,
    ObjectPage,
    ObjectRow,
    UnknownObject,
    VerbNotSupported,
    object_page,
)
from ufo.runtime.seats import SeatEntry, Seats, member_is_admin
from ufo.runtime.tools.context import TextContent, ToolContext, ToolResult
from ufo.runtime.tools.registry import ActionPresentation, ObjectBinding, ToolDef
from ufo.runtime.turns.audience import FOREIGN_AUDIENCE_PREFIX
from ufo.runtime.workspace import ws, ws_current
from ufo.schema import tables

WORKSPACE_KIND = "workspace"
WORKSPACE_IS_PERMANENT = "a workspace is created at first run and is never deleted"
WORKSPACE_ADMIN_GATE = (
    "a workspace's settings are changed by a workspace admin using the main agent"
)
WORKSPACE_ROOM = (
    "a workspace's settings are changed in an internal conversation, never in a channel shared "
    "with another organization"
)
WORKSPACE_SUSPENSION_ENABLED_ENV = "UFO_WORKSPACE_SUSPENSION_ENABLED"
SUSPENSION_ACTION = "set_suspension"


class WorkspaceSpec(BaseModel):
    """The workspace's one authored setting. Its counts are derived, so they are status."""

    model_config = ConfigDict(extra="forbid")
    members_can_add: bool = Field(
        description="Whether a member who is not an admin may add a teammate. An admin always may, "
        "and only an admin may add another admin."
    )


@dataclass(frozen=True)
class WorkspaceShape:
    """The one read every verb of this kind answers from: the setting, the member count, how many
    of them hold a seat, the roster naming which, and the row's own timestamps."""

    members_can_add: bool
    members: int
    seated: int
    roster: tuple[SeatEntry, ...]
    suspended_at: datetime | None
    suspended_by_email: str | None
    suspension_reason: str | None
    created_at: datetime
    updated_at: datetime


class SetSuspensionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suspended: bool = Field(description="True suspends the workspace; false restores access.")
    reason: str = Field(min_length=1, description="Why the workspace state must change.")


def workspace_suspension_enabled() -> bool:
    return os.environ.get(WORKSPACE_SUSPENSION_ENABLED_ENV) == "1"


async def suspension_operator_email(ctx: ToolContext) -> str | None:
    if not workspace_suspension_enabled() or ctx.speaker_member_id is None:
        return None
    workspace_id = ws_current().workspace_id
    async with workspace_tx() as connection:
        email = (
            await connection.execute(
                sa.select(tables.member.c.email).where(
                    tables.member.c.workspace_id == workspace_id,
                    tables.member.c.id == ctx.speaker_member_id,
                )
            )
        ).scalar_one_or_none()
    if email is None:
        return None
    grant = await installed_operator().rule.admit(workspace_id, email)
    return email if grant is not None and grant.reach == "fleet" else None


async def set_suspension(ctx: ToolContext, args: SetSuspensionInput) -> ToolResult:
    operator_email = await suspension_operator_email(ctx)
    if operator_email is None:
        raise ValueError("workspace suspension is not available to this member")
    if ctx.target is None or ctx.target.name is None:
        raise ValueError("workspace suspension requires a workspace target")
    workspace_id = UUID(ctx.target.name)
    with ws(workspace_id):
        async with workspace_tx() as connection:
            update = sa.update(tables.workspace).where(tables.workspace.c.id == workspace_id)
            if args.suspended:
                update = update.values(
                    suspended_at=sa.func.now(),
                    suspended_by_email=operator_email,
                    suspension_reason=args.reason,
                    updated_at=sa.func.now(),
                )
            else:
                update = update.values(
                    suspended_at=None,
                    suspended_by_email=None,
                    suspension_reason=None,
                    updated_at=sa.func.now(),
                )
            await connection.execute(update)
    state = "suspended" if args.suspended else "active"
    return ToolResult(
        content=(
            TextContent(text=f"Workspace {workspace_id} is now {state}. Reason: {args.reason}"),
        )
    )


SET_SUSPENSION_TOOL_DEF = ToolDef(
    name=SUSPENSION_ACTION,
    description="Suspend this workspace so it refuses new turns, or restore access.",
    input_model=SetSuspensionInput,
    handler=set_suspension,
    side_effecting=True,
    parallel_safe=True,
    bound=ObjectBinding(kind=WORKSPACE_KIND, binding="instance"),
    presentation=ActionPresentation(label="Set suspension"),
)


@dataclass(frozen=True)
class WorkspaceObjects:
    """Handlers over the bound workspace's own row: one instance named by its id, listed and read
    with its member counts, its setting applied by an admin, and create and delete refused."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        if ctx.audience.startswith(FOREIGN_AUDIENCE_PREFIX):
            return object_page((), query)
        shape = await self._shape()
        assert shape is not None
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
        target_id = await self._target_id(ctx, name)
        if target_id is None:
            return None
        shape = await self._shape(target_id)
        if shape is None:
            return None
        return ObjectDetail(
            spec=WorkspaceSpec(members_can_add=shape.members_can_add),
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
        target_id = await self._target_id(ctx, name)
        if target_id is None:
            return None
        shape = await self._shape(target_id)
        if shape is None:
            return None
        whole = ctx.speaker_member_id is not None and await ctx.agent_is_main()
        return {
            "members": shape.members,
            "seated": shape.seated,
            "suspended_at": (
                None if shape.suspended_at is None else shape.suspended_at.isoformat()
            ),
            "suspended_by_email": shape.suspended_by_email,
            "suspension_reason": shape.suspension_reason,
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
        speaker = ctx.require_speaker(WORKSPACE_ADMIN_GATE)
        if not await ctx.agent_is_main():
            raise AdminRequired(WORKSPACE_ADMIN_GATE)
        if ctx.audience.startswith(FOREIGN_AUDIENCE_PREFIX):
            raise AdminRequired(WORKSPACE_ROOM)
        if old is None:
            raise VerbNotSupported(WORKSPACE_IS_PERMANENT)
        workspace_id = ws_current().workspace_id
        if name != str(workspace_id):
            raise UnknownObject(f"no workspace object named {name!r}")
        async with workspace_tx() as connection:
            await connection.execute(
                sa.select(tables.workspace.c.id)
                .where(tables.workspace.c.id == workspace_id)
                .with_for_update()
            )
            if not await member_is_admin(connection, workspace_id, speaker):
                raise AdminRequired(WORKSPACE_ADMIN_GATE)
            await connection.execute(
                sa.update(tables.workspace)
                .where(tables.workspace.c.id == workspace_id)
                .values(members_can_add=spec.members_can_add, updated_at=sa.func.now())
            )

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(WORKSPACE_IS_PERMANENT)

    async def _target_id(self, ctx: ToolContext, name: str) -> UUID | None:
        if ctx.audience.startswith(FOREIGN_AUDIENCE_PREFIX):
            return None
        try:
            target_id = UUID(name)
        except ValueError:
            return None
        if target_id == ws_current().workspace_id:
            return target_id
        if await suspension_operator_email(ctx) is None:
            return None
        return target_id

    async def _shape(self, workspace_id: UUID | None = None) -> WorkspaceShape | None:
        workspace_id = workspace_id or ws_current().workspace_id
        with ws(workspace_id):
            async with workspace_tx() as connection:
                row = (
                    await connection.execute(
                        sa.select(
                            tables.workspace.c.members_can_add,
                            tables.workspace.c.suspended_at,
                            tables.workspace.c.suspended_by_email,
                            tables.workspace.c.suspension_reason,
                            tables.workspace.c.created_at,
                            tables.workspace.c.updated_at,
                        ).where(tables.workspace.c.id == workspace_id)
                    )
                ).one_or_none()
                if row is None:
                    return None
                snapshot = await Seats(workspace_id).snapshot(connection)
        return WorkspaceShape(
            members_can_add=row.members_can_add,
            members=len(snapshot.members),
            seated=snapshot.seated,
            roster=snapshot.members,
            suspended_at=row.suspended_at,
            suspended_by_email=row.suspended_by_email,
            suspension_reason=row.suspension_reason,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


WORKSPACE_OBJECT = ObjectKind(
    name=WORKSPACE_KIND,
    description=(
        "Show this workspace's membership settings, seat roster, and suspension state. A member's "
        "seat changes on the member object."
    ),
    guidance=(
        "A listing returns the current workspace, named by its workspace id. A speaking fleet "
        "operator can address another workspace by id and, when suspension is enabled, use "
        "`set_suspension` with the new state and a reason. Listings filter and order on `members` "
        "and `seated`; status carries both plus the suspension state and "
        "`roster`, which names every member with whether they hold a seat and whether they "
        "administer the workspace. A member asking the main agent in an internal conversation "
        "reads the whole roster; a child agent answers the speaker's own row alone. Members are "
        "unlimited and nothing is billed per head, so the counts are figures to report and never "
        "a limit to check before adding someone. A member holds a seat from the moment they are "
        "created and the agent answers them; an admin unseats one to remove that person's "
        "access, which is the only way to remove it. The spec's one field, `members_can_add`, is "
        "whether a member who is not an admin may add a teammate; only a speaking admin using the "
        "main agent applies it. Create and delete are refused. Seat or unseat one member by "
        "applying {seated: true} or {seated: false} on their member object. Reads answer an "
        "internal conversation only — an externally shared channel lists nothing."
    ),
    spec_model=WorkspaceSpec,
    store=WorkspaceObjects(),
    list_fields=frozenset({"members", "seated"}),
)
