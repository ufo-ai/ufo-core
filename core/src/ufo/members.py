"""The core-registered `member` object kind: workspace membership and admin role."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field

from ufo.db import workspace_tx
from ufo.ext.context import JsonValue
from ufo.objects import (
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
from ufo.schema import tables
from ufo.seats import member_is_admin
from ufo.tools.context import ToolContext
from ufo.workspace import ws_current

MEMBER_KIND = "member"
MEMBER_CREATE = "members join through a verified chat surface"
MEMBER_DELETE = "workspace members cannot be deleted through objects"
MEMBER_ADMIN_GATE = "admin roles can only be changed by a workspace admin using the main agent"


class MemberSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    admin: bool = Field(description="Whether this member administers the workspace.")


@dataclass(frozen=True)
class MemberObjects:
    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        rows = await self._visible_rows(ctx)
        return object_page(
            tuple(
                ObjectRow(
                    name=str(row.id),
                    summary=(
                        f"{row.email}, "
                        f"{'workspace admin' if row.is_admin else 'workspace member'}, "
                        f"{'seated' if row.seated_at is not None else 'unseated'}"
                    ),
                )
                for row in rows
            ),
            query,
        )

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemberSpec] | None:
        row = await self._visible_row(ctx, name)
        if row is None:
            return None
        return ObjectDetail(
            spec=MemberSpec(admin=row.is_admin),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None:
        row = await self._visible_row(ctx, name)
        return (
            None
            if row is None
            else {
                "email": row.email,
                "seated": row.seated_at is not None,
            }
        )

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: MemberSpec,
        old: MemberSpec | None,
    ) -> None:
        if not await ctx.agent_is_main() or ctx.speaker_member_id is None:
            raise AdminRequired(MEMBER_ADMIN_GATE)
        if old is None:
            raise VerbNotSupported(MEMBER_CREATE)
        try:
            member_id = UUID(name)
        except ValueError as error:
            raise UnknownObject(f"no member object named {name!r}") from error
        async with workspace_tx() as connection:
            await connection.execute(
                sa.select(tables.workspace.c.id)
                .where(tables.workspace.c.id == ws_current().workspace_id)
                .with_for_update()
            )
            if not await member_is_admin(
                connection,
                ws_current().workspace_id,
                ctx.speaker_member_id,
            ):
                raise AdminRequired(MEMBER_ADMIN_GATE)
            row = (
                await connection.execute(
                    sa.select(
                        tables.member.c.id,
                        tables.member.c.is_admin,
                        tables.member.c.seated_at,
                    ).where(
                        tables.member.c.workspace_id == ws_current().workspace_id,
                        tables.member.c.id == member_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise UnknownObject(f"no member object named {name!r}")
            if row.is_admin == spec.admin:
                return
            if not spec.admin:
                admins = (
                    await connection.execute(
                        sa.select(sa.func.count()).where(
                            tables.member.c.workspace_id == ws_current().workspace_id,
                            tables.member.c.is_admin,
                        )
                    )
                ).scalar_one()
                if admins == 1:
                    raise ValueError("a workspace must have at least one admin")
                if row.seated_at is not None:
                    seated_admins = (
                        await connection.execute(
                            sa.select(sa.func.count()).where(
                                tables.member.c.workspace_id == ws_current().workspace_id,
                                tables.member.c.is_admin,
                                tables.member.c.seated_at.is_not(None),
                            )
                        )
                    ).scalar_one()
                    if seated_admins == 1:
                        raise ValueError("a workspace must have a seated admin")
            await connection.execute(
                sa.update(tables.member)
                .where(tables.member.c.id == row.id)
                .values(is_admin=spec.admin, updated_at=sa.func.now())
            )

    async def delete(self, ctx: ToolContext, name: str) -> None:
        raise VerbNotSupported(MEMBER_DELETE)

    async def _visible_rows(self, ctx: ToolContext) -> tuple[sa.Row, ...]:
        query = (
            sa.select(
                tables.member.c.email,
                tables.member.c.id,
                tables.member.c.is_admin,
                tables.member.c.seated_at,
                tables.member.c.created_at,
                tables.member.c.updated_at,
            )
            .where(tables.member.c.workspace_id == ws_current().workspace_id)
            .order_by(tables.member.c.email)
        )
        if not await ctx.speaker_is_admin() or not await ctx.agent_is_main():
            if ctx.speaker_member_id is None:
                return ()
            query = query.where(tables.member.c.id == ctx.speaker_member_id)
        async with workspace_tx() as connection:
            return tuple((await connection.execute(query)).all())

    async def _visible_row(self, ctx: ToolContext, name: str) -> sa.Row | None:
        return next(
            (row for row in await self._visible_rows(ctx) if str(row.id) == name),
            None,
        )


MEMBER_OBJECT = ObjectKind(
    name=MEMBER_KIND,
    description="A workspace member and their admin role.",
    guidance=(
        "List members from the main agent to find stable member ids and manage workspace admins. "
        "Apply {admin: true|false} to an existing member id; only a speaking admin using the main "
        "agent may change roles. The "
        "last admin and last seated admin cannot be removed. Members join through verified chat "
        "surfaces and cannot be created or deleted here."
    ),
    spec_model=MemberSpec,
    store=MemberObjects(),
)
