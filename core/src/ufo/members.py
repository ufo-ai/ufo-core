"""The core-registered `member` object kind: workspace membership, admin role, and seat, plus
`add_member`, the one verb that mints a member ahead of their first contact.

The roster is the main agent's: a read off any other agent narrows to the reader's own row, and a
channel another organization sits in narrows the same way. The portal reads through that one rule —
`member_page`/`member_detail` answer on the agent the request names, with the signed-in member as
the reader."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.agent_scope import agent_current
from ufo.audience import FOREIGN_AUDIENCE_PREFIX
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, JsonValue
from ufo.objects import (
    AdminRequired,
    MemberObject,
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
from ufo.seats import Seats, create_member, email_domain, member_is_admin
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
            "admission, which is how a workspace removes someone's access. Every member holds "
            "one from creation and nothing bounds how many do; the last seated admin cannot be "
            "unseated."
        )
    )


@dataclass(frozen=True)
class MemberObjects:
    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        return object_page(
            tuple(_row(row) for row in await self._visible_rows(ctx)),
            query,
        )

    async def member_page(
        self,
        ext: ExtensionContext | None,
        *,
        member_id: UUID,
        admin: bool,
        query: ObjectListQuery,
    ) -> ObjectPage:
        """The roster a signed-in member reads outside a turn, on the rule `list` answers by: the
        whole workspace off the main agent, the reader's own row alone off any other. The reader
        is the speaker — a portal read is always live and member-made — and its audience is that
        member's own, never a channel another organization sits in, so the foreign narrowing
        `list` also applies has nothing to catch here."""
        return object_page(
            tuple(_row(row) for row in await self._member_rows(member_id)),
            query,
        )

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemberSpec] | None:
        row = await self._visible_row(ctx, name)
        return None if row is None else _detail(row)

    async def member_detail(
        self,
        ext: ExtensionContext | None,
        name: str,
        *,
        member_id: UUID,
        admin: bool,
    ) -> MemberObject[MemberSpec] | None:
        """One member as the portal reads them — the row `list` renders beside the detail `get`
        reads, behind the same main-agent rule. A colleague's row is absent off a child agent for
        everyone, an admin included: the roster is the main agent's."""
        row = next(
            (row for row in await self._member_rows(member_id) if str(row.id) == name),
            None,
        )
        return None if row is None else MemberObject(row=_row(row), detail=_detail(row))

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
        if ctx.speaker_member_id is None:
            return ()
        if ctx.audience.startswith(FOREIGN_AUDIENCE_PREFIX):
            return await self._roster(ctx.speaker_member_id, whole=False)
        return await self._roster(ctx.speaker_member_id, whole=await ctx.agent_is_main())

    async def _visible_row(self, ctx: ToolContext, name: str) -> sa.Row | None:
        return next(
            (row for row in await self._visible_rows(ctx) if str(row.id) == name),
            None,
        )

    async def _member_rows(self, member_id: UUID) -> tuple[sa.Row, ...]:
        async with workspace_tx() as connection:
            is_main = bool(
                (
                    await connection.execute(
                        sa.select(tables.agent.c.is_main).where(
                            tables.agent.c.workspace_id == ws_current().workspace_id,
                            tables.agent.c.id == agent_current().agent_id,
                        )
                    )
                ).scalar_one_or_none()
            )
        return await self._roster(member_id, whole=is_main)

    async def _roster(self, member_id: UUID, *, whole: bool) -> tuple[sa.Row, ...]:
        """The membership rows a reader may see: the workspace's whole roster, or their own row
        alone. One derivation for the turn's `list`/`get` and the portal's
        `member_page`/`member_detail`, so the roster a member reads in the portal is the roster the
        agent would tell them."""
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
        if not whole:
            query = query.where(tables.member.c.id == member_id)
        async with workspace_tx() as connection:
            return tuple((await connection.execute(query)).all())


def _row(row: sa.Row) -> ObjectRow:
    return ObjectRow(
        name=str(row.id),
        summary=(
            f"{row.email}, "
            f"{'workspace admin' if row.is_admin else 'workspace member'}, "
            f"{'seated' if row.seated_at is not None else 'unseated'}"
        ),
        fields={
            "email": row.email,
            "admin": row.is_admin,
            "seated": row.seated_at is not None,
        },
    )


def _detail(row: sa.Row) -> ObjectDetail[MemberSpec]:
    return ObjectDetail(
        spec=MemberSpec(admin=row.is_admin, seated=row.seated_at is not None),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class AddMemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(description="The email of the person to add, at any domain.")
    admin: bool = Field(
        default=False,
        description="Whether they administer the workspace — a workspace admin manages every "
        "member's role and seat, and reads the workspace's shape.",
    )
    notify: bool = Field(
        default=True,
        description="Whether to email them that they were added, with a link to sign in. Set "
        "false only when the member asks you not to write to this person.",
    )
    user_description: str = Field(
        description="Who you are adding, in plain language for the activity timeline."
    )


@dataclass(frozen=True)
class AddMember:
    """Mint a member before their first contact, so an admin can staff a workspace instead of
    waiting for each person to arrive. Every other creation path is self-service and anchors on the
    workspace's own email domain — hosted onboarding's first admin, a domain-matching teammate
    speaking on a verified chat surface, a login that resolves this workspace. This one does not:
    a contractor, an advisor, or a colleague at a sister company is admitted at whatever domain
    their address carries, because a speaking admin vetted them, which is the same authority the
    role and seat fields carry.

    Someone minted here has had no contact, so nothing else would ever tell them the workspace
    exists: the member row itself carries who added them and when, and that stamp is what the
    invitation email is delivered from."""

    async def add(self, ctx: ToolContext, args: AddMemberInput) -> ToolResult:
        if ctx.speaker_member_id is None or not await ctx.agent_is_main():
            raise AdminRequired(ADD_MEMBER_GATE)
        if ctx.audience.startswith(FOREIGN_AUDIENCE_PREFIX):
            raise AdminRequired(ADD_MEMBER_ROOM)
        email = args.email.strip().lower()
        if not email_domain(email):
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
            await self._absent(connection, email)
            await create_member(
                connection,
                ws_current().workspace_id,
                email,
                is_admin=args.admin,
                invited_by=ctx.speaker_member_id if args.notify else None,
            )
        role = "a workspace admin" if args.admin else "a workspace member"
        told = " They will get an email with a link to sign in." if args.notify else ""
        return ToolResult(
            content=(
                TextContent(text=f"{email} is {role}. They can speak to the agent now.{told}"),
            )
        )

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
        "Add someone to this workspace by their email, optionally as an admin, before they "
        "have ever contacted the agent, at any email domain — an outside contractor or advisor is "
        "added the same way as a colleague. They are emailed a link to sign in unless notify is "
        "false. Only a workspace admin using the main agent may add a member. Changing an "
        "existing member's role or seat is an apply on the member object, not this."
    ),
    input_model=AddMemberInput,
    handler=AddMember().add,
    side_effecting=True,
    parallel_safe=True,
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
        "change either. Unseating removes access at admission and seating restores it; members "
        "are unlimited, so nothing is counted. The last admin and last seated admin cannot be "
        f"removed. Use {ADD_MEMBER_TOOL} to "
        "add someone who has not arrived yet; members also join by themselves through a verified "
        "chat surface. Members cannot be deleted here."
    ),
    spec_model=MemberSpec,
    store=MemberObjects(),
    list_fields=frozenset({"email", "admin", "seated"}),
)
