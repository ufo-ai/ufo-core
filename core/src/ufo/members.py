"""The core-registered `member` object kind: workspace membership, admin role, and seat, plus
`add_member`, the one verb that mints a member ahead of their first contact."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.audience import FOREIGN_AUDIENCE_PREFIX
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
from ufo.seats import Seats, create_member, email_domain, member_is_admin, workspace_domain
from ufo.tools.context import TextContent, ToolContext, ToolResult
from ufo.tools.registry import ToolDef
from ufo.workspace import ws_current

MEMBER_KIND = "member"
ADD_MEMBER_TOOL = "add_member"
MEMBER_CREATE = f"members join through a verified chat surface, or are added with {ADD_MEMBER_TOOL}"
MEMBER_DELETE = "workspace members cannot be deleted through objects"
MEMBER_ADMIN_GATE = "a member's role or seat is changed by a workspace admin using the main agent"
ADD_MEMBER_GATE = "a member is added by a workspace admin using the main agent"
ADD_MEMBER_ROOM = (
    "membership is managed in an internal conversation, never in a channel shared with "
    "another organization"
)


class MemberSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    admin: bool = Field(description="Whether this member administers the workspace.")
    seated: bool = Field(
        description=(
            "Whether this member holds a seat — an unseated member's messages are refused at "
            "admission. Seating counts against the workspace's seat limit; the last seated "
            "admin cannot be unseated."
        )
    )


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
            spec=MemberSpec(admin=row.is_admin, seated=row.seated_at is not None),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
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
        *,
        expected_generation: UUID | None,
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
                        tables.member.c.email,
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
            if spec.seated != (row.seated_at is not None):
                seats = Seats(ws_current().workspace_id)
                if spec.seated:
                    await seats.grant(connection, row.email)
                else:
                    await seats.revoke(connection, row.email)
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
                if spec.seated:
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

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
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
        if ctx.speaker_member_id is None:
            return ()
        if not await ctx.agent_is_main() or ctx.audience.startswith(FOREIGN_AUDIENCE_PREFIX):
            query = query.where(tables.member.c.id == ctx.speaker_member_id)
        async with workspace_tx() as connection:
            return tuple((await connection.execute(query)).all())

    async def _visible_row(self, ctx: ToolContext, name: str) -> sa.Row | None:
        return next(
            (row for row in await self._visible_rows(ctx) if str(row.id) == name),
            None,
        )


class AddMemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(
        description="The work email of the person to add, at the workspace's own domain."
    )
    admin: bool = Field(
        default=False,
        description="Whether they administer the workspace — a workspace admin manages every "
        "member's role and seat, and reads the workspace's shape.",
    )
    user_description: str = Field(
        description="Who you are adding, in plain language for the activity timeline."
    )


@dataclass(frozen=True)
class AddMember:
    """Mint a member before their first contact, so an admin can staff a workspace instead of
    waiting for each person to arrive. Every other creation path is self-service — hosted
    onboarding's first admin, a domain-matching teammate speaking on a verified chat surface, a
    login that resolves this workspace — and each anchors on the workspace's own email domain, so
    this one anchors there too: the gateway resolves a sign-in by the address's domain, so a row
    minted for any other domain could never sign in to answer for it."""

    async def add(self, ctx: ToolContext, args: AddMemberInput) -> ToolResult:
        if ctx.speaker_member_id is None or not await ctx.agent_is_main():
            raise AdminRequired(ADD_MEMBER_GATE)
        if ctx.audience.startswith(FOREIGN_AUDIENCE_PREFIX):
            raise AdminRequired(ADD_MEMBER_ROOM)
        email = args.email.strip().lower()
        domain = email_domain(email)
        if not domain:
            raise ValueError(f"{args.email!r} is not an email address")
        async with workspace_tx() as connection:
            await connection.execute(
                sa.select(tables.workspace.c.id)
                .where(tables.workspace.c.id == ws_current().workspace_id)
                .with_for_update()
            )
            if not await member_is_admin(
                connection, ws_current().workspace_id, ctx.speaker_member_id
            ):
                raise AdminRequired(ADD_MEMBER_GATE)
            await self._domain_matches(connection, domain)
            await self._absent(connection, email)
            member_id = await create_member(
                connection, ws_current().workspace_id, email, is_admin=args.admin
            )
            seated = (
                await connection.execute(
                    sa.select(tables.member.c.seated_at).where(tables.member.c.id == member_id)
                )
            ).scalar_one() is not None
        role = "a workspace admin" if args.admin else "a workspace member"
        seat = (
            "They can speak to the agent now."
            if seated
            else "They hold no seat, so the agent refuses them until an admin grants one."
        )
        return ToolResult(content=(TextContent(text=f"{email} is {role}. {seat}"),))

    async def _domain_matches(self, connection: AsyncConnection, domain: str) -> None:
        """The one derivation the portal also advertises, so the domain a panel shows and the
        domain this admits cannot diverge."""
        own = await workspace_domain(connection, ws_current().workspace_id)
        if own is None:
            raise ValueError(
                "this workspace's own address carries no domain, so no added address can be "
                "matched against it"
            )
        if domain != own:
            raise ValueError(f"this workspace admits {own} addresses")

    async def _absent(self, connection: AsyncConnection, email: str) -> None:
        existing = (
            await connection.execute(
                sa.select(tables.member.c.id).where(
                    tables.member.c.workspace_id == ws_current().workspace_id,
                    sa.func.lower(tables.member.c.email) == email,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError(
                f"{email} is already a member; change their role or seat on member "
                f"{existing} instead"
            )


ADD_MEMBER_TOOL_DEF = ToolDef(
    name=ADD_MEMBER_TOOL,
    description=(
        "Add someone to this workspace by their work email, optionally as an admin, before they "
        "have ever contacted the agent. Only a workspace admin using the main agent may add a "
        "member, and only at the workspace's own email domain. Changing an existing member's role "
        "or seat is an apply on the member object, not this."
    ),
    input_model=AddMemberInput,
    handler=AddMember().add,
    side_effecting=True,
)

MEMBER_OBJECT = ObjectKind(
    name=MEMBER_KIND,
    description="A workspace member, their admin role, and their seat.",
    guidance=(
        "List members from the main agent for the workspace roster — every member reads it in an "
        "internal conversation, so any member can be told who their colleagues are and which of "
        "them administer the workspace. A child agent and an externally shared channel list only "
        "the speaker: the roster is internal, and a channel another organization sits in never "
        "hears it. Apply {admin: true|false, seated: "
        "true|false} to an existing member id; only a speaking admin using the main agent may "
        "change either. Unseating removes access at admission; seating counts against the seat "
        f"limit. The last admin and last seated admin cannot be removed. Use {ADD_MEMBER_TOOL} to "
        "add someone who has not arrived yet; members also join by themselves through a verified "
        "chat surface. Members cannot be deleted here."
    ),
    spec_model=MemberSpec,
    store=MemberObjects(),
)
