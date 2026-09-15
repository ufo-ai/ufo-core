"""The core-registered `conversation` object kind: past conversations as read-only objects.

Artifacts and scheduled tasks link to the conversation they came from or report into; this kind is
what those links resolve to — the surface, the origin label that surface wrote, audience, row
timestamps, and, through status, the text exchange written into the turn's workspace. The portal
resolves the same links against the same row: `member_detail` answers a signed-in member outside a
turn, on the subjects their own conversation carries. Surfaces create conversations, so apply and
delete are refused; what a member does own is the filing — the archive and delete marks the
conversation carries, and their own pin — which the actions at the foot of this module set and
clear."""

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import ClassVar, Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.blob import BlobNotFound
from ufo.db import workspace_tx
from ufo.harness.models.interface import TextBlock
from ufo.runtime.ext.context import ExtensionContext, JsonValue
from ufo.runtime.ext.surface import ConversationDirectory, ListedConversation, opening_sentence
from ufo.runtime.kinds.agents import AGENT_KIND
from ufo.runtime.object_name import ObjectRef
from ufo.runtime.object_scope import object_agent_id
from ufo.runtime.objects import (
    MATERIALIZE_MAX_BYTES,
    OBJECT_LIST_PAGE,
    MemberObject,
    ObjectDetail,
    ObjectKind,
    ObjectLink,
    ObjectListQuery,
    ObjectPage,
    ObjectRow,
    UnknownObject,
    VerbNotSupported,
    object_page,
)
from ufo.runtime.tools.context import TextContent, ToolContext, ToolResult
from ufo.runtime.tools.registry import ActionPresentation, ObjectBinding, ToolDef
from ufo.runtime.turns.audience import FOREIGN_AUDIENCE_PREFIX
from ufo.runtime.turns.subjects import MEMBER_SUBJECT_PREFIX
from ufo.runtime.turns.transcript import TranscriptDecodeError, decode, transcript_key
from ufo.runtime.workspace import ws_current
from ufo.schema import tables
from ufo.schema.records import EXTENSION_SURFACE_PREFIX, PORTAL_SURFACE

CONVERSATION_KIND = "conversation"
TRANSCRIPT_WORKSPACE_DIR = "transcripts"
CONVERSATION_MINE_LIMIT = 100
CONVERSATION_OTHERS_LIMIT = 25
CONVERSATIONS_ARE_SURFACE_MADE = (
    "conversations are created by surfaces and closed by retention, never authored"
)
NO_SUCH_CONVERSATION = "No conversation with that id on this agent."
NOT_THE_OWNER = "Only the member who owns a conversation can delete or restore it."
FILING_GATE = "filing a conversation"


class ConversationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    surface: str = Field(description="The chat surface the conversation runs on.")
    surface_label: str | None = Field(
        default=None,
        description=(
            "What the surface calls the conversation's origin, in its own grammar — a Slack "
            "channel as `#general`. Null when the surface names none."
        ),
    )
    audience: str = Field(description="The conversation's disclosure audience.")


@dataclass(frozen=True)
class ConversationObjects:
    """Read-only handlers over the selected agent's conversations visible to the caller, in a turn
    and — through `member_detail` — for a signed-in member outside one. Status materializes a
    visible transcript. A speaking workspace admin's turn also reads another member's private
    conversation of the selected agent as a metadata row: get serves its spec and links, list
    shows such rows only under the explicit `{"private": true}` filter, status answers nothing,
    and no transcript is read or written — content stays behind the recorded acknowledgement, and
    a foreign-audience turn never widens. `member_page` and `member_detail` are the portal's one
    row for a listed and a resolved conversation alike. Resolves artifact and scheduled-task links;
    every mutation refuses."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        # The mark filters narrow the read in the query, before the page is cut; the default page
        # is the conversations under no mark.
        narrowed = {
            name: query.filters.get(name) if isinstance(query.filters.get(name), bool) else False
            for name in ("archived", "deleted")
        }
        token = ActingMember.current.set(ctx.speaker_member_id)
        try:
            rows = [
                _row(row)
                for row in await self._rows(
                    ctx.read_subjects,
                    conversation_id=None,
                    archived=narrowed["archived"],
                    deleted=narrowed["deleted"],
                )
            ]
        finally:
            ActingMember.current.reset(token)
        if query.filters.get("private") is True and await self._widens_for_admin(ctx):
            rows += tuple(
                _row(row, private=True)
                for row in await self._private_rows(ctx, conversation_id=None)
            )
        return object_page(tuple(rows), query)

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ConversationSpec] | None:
        row = await self._find(ctx.read_subjects, name)
        if row is None:
            row = await self._private_find(ctx, name)
        return None if row is None else _detail(row)

    async def member_page(
        self,
        ext: ExtensionContext | None,
        *,
        member_id: UUID,
        admin: bool,
        query: ObjectListQuery,
    ) -> ObjectPage:
        """The selected agent's conversations as the portal's rail reads them: the member's own
        beside the readable ones colleagues are in, `mine` saying which, newest activity first
        under `last_at`. Read as the member whatever their standing — an admin's rail is their own
        work, never every private conversation in the workspace, so `admin` deliberately never
        widens this listing. A machine lane sharing a chat surface — a homepage seed's
        conversation, the portal's prepared-intent queue — holds no member-admitted turn and is
        absent; so is a conversation with no title yet, a nameless row leading to an empty
        screen. `portal` says whether the web chat transport carries the row — the portal's own
        surface and the extension-opened ones its composer answers — as a declared filter, because
        a filter must narrow the page before it is cut: cut first, a run of newer rows from other
        surfaces renders an empty chat list. It narrows in the directory read, ahead of each
        side's own bound, so one rail read costs `CONVERSATION_MINE_LIMIT` plus
        `CONVERSATION_OTHERS_LIMIT` rows whatever the workspace holds. Both sides are one member's
        wait, so they read together rather than one behind the other. `source` is the link the
        admitting surface reported for the message that opened the row, so a rail row leads back
        out to the thread it came in on; a portal row's source is no link out and answers None.

        Every row states `readable`, `disclosable` and `speakable` — whether its content reads
        now, whether an admin may open it by acknowledging, and whether the viewer's messages may
        land in it on the audience alone. The rail's rows are all readable; the declared filter
        `readable: false` is the one read that widens, for an admin: the selected agent's
        conversations they may not read — another member's private ones, disclosable, and rooms,
        not — as metadata rows under `CONVERSATION_OTHERS_LIMIT`, titles and words withheld with
        the rest of the content. Asked for, not granted: a non-admin's `readable: false` page is
        empty, and no unfiltered read carries a row its viewer cannot read.

        A search runs in the directory read rather than over the rows it returns, for the reason
        `portal` does: searched after the bound, a query for an older conversation answers nothing
        while the conversation stands. It runs there and nowhere else — the page is ordered and cut
        by the term but never narrowed by it a second time, because two narrowings over two field
        sets leave the page their intersection, and a term either of them alone would match then
        answers nothing. A side that comes back full is a side the workspace holds more of, so the
        page states `cut` and the screen drawing it says so.

        `archived` and `deleted` are declared filters for the same reason `portal` is: the marks
        narrow the directory read ahead of each side's bound, so an archived conversation past the
        bound is listed by the archived page rather than cut out of it. Neither filter carries the
        page by default — the default page is the conversations under no mark."""
        directory = ConversationDirectory(ws_current().workspace_id)
        agent_id = object_agent_id()
        portal = query.filters.get("portal")
        carried = portal if isinstance(portal, bool) else None
        search = query.query or None
        archived = query.filters.get("archived") is True
        deleted = query.filters.get("deleted") is True
        if query.filters.get("readable") is False:
            listed = await directory.list(
                agent_id,
                member_id,
                admin=admin,
                limit=CONVERSATION_OTHERS_LIMIT,
                readable=False,
                member_admitted=True,
                portal=carried,
                search=search,
                archived=archived,
                deleted=deleted,
            )
            gathered = tuple(_member_row(entry) for entry in listed)
            cut = len(listed) >= CONVERSATION_OTHERS_LIMIT
        else:
            sides: tuple[tuple[Literal["mine", "others"], int], ...] = (
                ("mine", CONVERSATION_MINE_LIMIT),
                ("others", CONVERSATION_OTHERS_LIMIT),
            )
            listed_sides = await asyncio.gather(
                *(
                    directory.list(
                        agent_id,
                        member_id,
                        admin=False,
                        limit=limit,
                        participation=participation,
                        member_admitted=True,
                        portal=carried,
                        search=search,
                        archived=archived,
                        deleted=deleted,
                    )
                    for participation, limit in sides
                )
            )
            gathered = tuple(
                _member_row(entry) for side in listed_sides for entry in side if entry.title
            )
            cut = any(
                len(side) >= limit for side, (_, limit) in zip(listed_sides, sides, strict=True)
            )
        # The pin survives the page bound without the cut eating the newest rows or the
        # colleagues' side: order by the page's own key, pinned ahead, then last activity.
        ordered = sorted(
            gathered,
            key=lambda row: (
                not row.fields.get("pinned"),
                [-ord(c) for c in str(row.fields.get("last_at") or "")],
            ),
        )
        page = object_page(tuple(ordered[:OBJECT_LIST_PAGE]), replace(query, query=""))
        return replace(page, cut=cut)

    async def member_detail(
        self,
        ext: ExtensionContext | None,
        name: str,
        *,
        member_id: UUID,
        admin: bool,
    ) -> MemberObject[ConversationSpec] | None:
        """One conversation as the portal reads it outside a turn — the `#/c/<id>` permalink's
        resolve: the same row `member_page` lists, read by id past the listing's bound and its
        title filter, beside the detail `get` reads. A member reaches their own conversations and
        the workspace-shared ones; an admin also reaches another member's private one and a room's
        as the metadata row the listing's `readable: false` carries — unreadable, disclosable or
        not — so a permalink an admin was offered resolves rather than reading as absent. The
        recorded acknowledgement opens transcript content, never this row. The agent is the
        caller's ambient one, the same wall `list` answers behind."""
        try:
            conversation_id = UUID(name)
        except ValueError:
            return None
        listed = await ConversationDirectory(ws_current().workspace_id).list(
            object_agent_id(), member_id, admin=admin, limit=1, conversation_id=conversation_id
        )
        if not listed:
            return None
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    _agent_conversations().where(tables.conversation.c.id == conversation_id)
                )
            ).one()
        return MemberObject(row=_member_row(listed[0]), detail=_detail(row))

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
        row = await self._find(ctx.read_subjects, name)
        if row is None:
            return None
        exchange = await self._exchange(ctx, row.id)
        if not await self._unchanged_visible(ctx.read_subjects, row):
            raise UnknownObject(f"no conversation object named {name!r}")
        body = "\n".join(exchange).encode()
        path: str | None = None
        if body and len(body) <= MATERIALIZE_MAX_BYTES:
            path = f"{TRANSCRIPT_WORKSPACE_DIR}/{row.id}.txt"
            await ctx.sandbox.write_file(path, body)
        return {"messages": len(exchange), "size_bytes": len(body), "workspace_path": path}

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: ConversationSpec,
        old: ConversationSpec | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(CONVERSATIONS_ARE_SURFACE_MADE)

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(CONVERSATIONS_ARE_SURFACE_MADE)

    async def _exchange(self, ctx: ToolContext, conversation_id: UUID) -> tuple[str, ...]:
        try:
            transcript = decode(await ctx.blob.get(transcript_key(conversation_id)))
        except BlobNotFound:
            return ()
        except TranscriptDecodeError as error:
            raise ValueError(
                f"conversation {conversation_id} has an unreadable transcript"
            ) from error
        lines = []
        for message in transcript.messages:
            text = (
                message.content
                if isinstance(message.content, str)
                else "\n".join(
                    block.text for block in message.content if isinstance(block, TextBlock)
                )
            )
            if text:
                lines.append(f"{message.role}: {text}")
        return tuple(lines)

    async def _unchanged_visible(self, subjects: frozenset[str], row: sa.Row) -> bool:
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(
                        sa.exists(
                            _visible(subjects).where(
                                tables.conversation.c.id == row.id,
                                tables.conversation.c.audience == row.audience,
                            )
                        )
                    )
                )
            ).scalar_one()

    async def _find(self, subjects: frozenset[str], name: str) -> sa.Row | None:
        try:
            conversation_id = UUID(name)
        except ValueError:
            return None
        rows = await self._rows(subjects, conversation_id=conversation_id)
        return rows[0] if rows else None

    async def _rows(
        self,
        subjects: frozenset[str],
        *,
        conversation_id: UUID | None,
        archived: JsonValue | None = None,
        deleted: JsonValue | None = None,
    ) -> tuple[sa.Row, ...]:
        query = _visible(
            subjects,
            archived=archived if isinstance(archived, bool) else None,
            deleted=deleted if isinstance(deleted, bool) else None,
        )
        if conversation_id is not None:
            query = query.where(tables.conversation.c.id == conversation_id)
        async with workspace_tx() as connection:
            return tuple((await connection.execute(query)).all())

    async def _widens_for_admin(self, ctx: ToolContext) -> bool:
        if ctx.audience.startswith(FOREIGN_AUDIENCE_PREFIX):
            return False
        return await ctx.speaker_is_admin()

    async def _private_find(self, ctx: ToolContext, name: str) -> sa.Row | None:
        if not await self._widens_for_admin(ctx):
            return None
        try:
            conversation_id = UUID(name)
        except ValueError:
            return None
        rows = await self._private_rows(ctx, conversation_id=conversation_id)
        return rows[0] if rows else None

    async def _private_rows(
        self, ctx: ToolContext, *, conversation_id: UUID | None
    ) -> tuple[sa.Row, ...]:
        query = _agent_conversations().where(
            tables.conversation.c.audience.startswith(MEMBER_SUBJECT_PREFIX),
            tables.conversation.c.audience.notin_(ctx.read_subjects),
        )
        if conversation_id is not None:
            query = query.where(tables.conversation.c.id == conversation_id)
        async with workspace_tx() as connection:
            return tuple((await connection.execute(query)).all())


class ConversationMarkInput(BaseModel):
    """Empty: the conversation is the action's target on the `conversation` kind."""

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class ConversationArchive:
    """The archive mark on a conversation, set or cleared by the act, and the sentence read back.
    The mark is the conversation's rather than the reader's — a workspace conversation one member
    archives is archived for everyone who lists it, the way its title and audience are — and every
    surface's next arrival clears it, which admission does under the same row lock."""

    archived: bool
    said: str

    async def act(self, ctx: ToolContext, args: ConversationMarkInput) -> ToolResult:
        ctx.require_speaker(FILING_GATE)
        conversation_id = _marked_conversation(ctx)
        async with workspace_tx() as connection:
            if not await _readable(connection, ctx.read_subjects, conversation_id):
                return _no_conversation()
            await connection.execute(
                _filing(conversation_id).values(
                    archived_at=sa.func.now() if self.archived else None
                )
            )
        return ToolResult(content=(TextContent(text=self.said),))


@dataclass(frozen=True)
class ConversationDelete:
    """The delete mark on a conversation, set or cleared by the act. The conversation leaves every
    member's listing and its transcript stands, so the act is the owner's alone: taking a
    conversation off a colleague's list is not a filing anybody else gets to do."""

    deleted: bool
    said: str

    async def act(self, ctx: ToolContext, args: ConversationMarkInput) -> ToolResult:
        speaker = ctx.require_speaker(FILING_GATE)
        conversation_id = _marked_conversation(ctx)
        async with workspace_tx() as connection:
            if not await _readable(connection, ctx.read_subjects, conversation_id):
                return _no_conversation()
            if await _owner(connection, conversation_id) != speaker:
                return ToolResult(content=(TextContent(text=NOT_THE_OWNER),), is_error=True)
            await connection.execute(
                _filing(conversation_id).values(deleted_at=sa.func.now() if self.deleted else None)
            )
        return ToolResult(content=(TextContent(text=self.said),))


@dataclass(frozen=True)
class ConversationPin:
    """The speaking member's own pin on a conversation, which holds it above the rest of their own
    listing. It is keyed by member beside the read cursor rather than stored on the conversation:
    a shared conversation is listed by everyone it is shared with, and one member's filing of it
    must not reorder anybody else's list."""

    pinned: bool
    said: str

    async def act(self, ctx: ToolContext, args: ConversationMarkInput) -> ToolResult:
        speaker = ctx.require_speaker(FILING_GATE)
        conversation_id = _marked_conversation(ctx)
        workspace_id = ws_current().workspace_id
        pinned_at = datetime.now(UTC)
        async with workspace_tx() as connection:
            if not await _readable(connection, ctx.read_subjects, conversation_id):
                return _no_conversation()
            if not self.pinned:
                await connection.execute(
                    sa.delete(tables.conversation_pin).where(
                        tables.conversation_pin.c.workspace_id == workspace_id,
                        tables.conversation_pin.c.conversation_id == conversation_id,
                        tables.conversation_pin.c.member_id == speaker,
                    )
                )
                return ToolResult(content=(TextContent(text=self.said),))
            insert = postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert
            await connection.execute(
                insert(tables.conversation_pin)
                .values(
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    member_id=speaker,
                    pinned_at=pinned_at,
                )
                .on_conflict_do_update(
                    index_elements=("workspace_id", "conversation_id", "member_id"),
                    set_={"pinned_at": pinned_at},
                )
            )
        return ToolResult(content=(TextContent(text=self.said),))


def _filing(conversation_id: UUID) -> sa.Update:
    return sa.update(tables.conversation).where(
        tables.conversation.c.workspace_id == ws_current().workspace_id,
        tables.conversation.c.id == conversation_id,
    )


def _marked_conversation(ctx: ToolContext) -> UUID:
    if ctx.target is None or ctx.target.name is None:
        raise RuntimeError("a conversation filing action dispatched without its target")
    try:
        return UUID(ctx.target.name)
    except ValueError as error:
        raise UnknownObject(NO_SUCH_CONVERSATION) from error


async def _readable(
    connection: AsyncConnection, subjects: frozenset[str], conversation_id: UUID
) -> bool:
    found = (
        await connection.execute(
            _visible(subjects).where(tables.conversation.c.id == conversation_id)
        )
    ).first()
    return found is not None


def _no_conversation() -> ToolResult:
    return ToolResult(content=(TextContent(text=NO_SUCH_CONVERSATION),), is_error=True)


async def _owner(connection: AsyncConnection, conversation_id: UUID) -> UUID | None:
    """Whose the conversation is: the member it is bound to, or — a workspace one, which
    `conversation_audience_member` leaves bound to nobody — whoever spoke in it first. The rule
    `ListedConversation.owner_email` states over a listed page, read for one row."""
    workspace_id = ws_current().workspace_id
    first_speaker = (
        sa.select(tables.turn.c.speaker_member_id)
        .where(
            tables.turn.c.workspace_id == workspace_id,
            tables.turn.c.conversation_id == conversation_id,
            tables.turn.c.speaker_member_id.is_not(None),
        )
        .order_by(tables.turn.c.seq.asc())
        .limit(1)
        .scalar_subquery()
    )
    return (
        await connection.execute(
            sa.select(sa.func.coalesce(tables.conversation.c.member_id, first_speaker)).where(
                tables.conversation.c.workspace_id == workspace_id,
                tables.conversation.c.id == conversation_id,
            )
        )
    ).scalar_one_or_none()


CONVERSATION_MARK_ACTIONS: tuple[ToolDef, ...] = (
    ToolDef(
        name="archive_conversation",
        description=(
            "Archive this conversation: it leaves the conversation list and reads as before. The "
            "next message a member sends in it brings it back."
        ),
        input_model=ConversationMarkInput,
        handler=ConversationArchive(
            archived=True,
            said="Archived. It is out of the conversation list until its next message.",
        ).act,
        side_effecting=True,
        parallel_safe=True,
        bound=ObjectBinding(kind=CONVERSATION_KIND, binding="instance"),
        agent_targetable=True,
        presentation=ActionPresentation(label="Archive", frame=True),
    ),
    ToolDef(
        name="unarchive_conversation",
        description="Take this conversation out of the archive, back into the conversation list.",
        input_model=ConversationMarkInput,
        handler=ConversationArchive(
            archived=False,
            said="Unarchived. It is back in the conversation list.",
        ).act,
        side_effecting=True,
        parallel_safe=True,
        bound=ObjectBinding(kind=CONVERSATION_KIND, binding="instance"),
        agent_targetable=True,
        presentation=ActionPresentation(label="Unarchive", frame=True),
    ),
    ToolDef(
        name="pin_conversation",
        description=(
            "Pin this conversation above the others in the speaking member's own conversation "
            "list. The pin is theirs alone; no other member's list moves."
        ),
        input_model=ConversationMarkInput,
        handler=ConversationPin(
            pinned=True,
            said="Pinned. It stands above your other conversations.",
        ).act,
        side_effecting=True,
        parallel_safe=True,
        bound=ObjectBinding(kind=CONVERSATION_KIND, binding="instance"),
        agent_targetable=True,
        presentation=ActionPresentation(label="Pin", frame=True),
    ),
    ToolDef(
        name="unpin_conversation",
        description=(
            "Unpin this conversation from the speaking member's own conversation list; it sorts "
            "with the others again."
        ),
        input_model=ConversationMarkInput,
        handler=ConversationPin(
            pinned=False,
            said="Unpinned. It sorts with your other conversations again.",
        ).act,
        side_effecting=True,
        parallel_safe=True,
        bound=ObjectBinding(kind=CONVERSATION_KIND, binding="instance"),
        agent_targetable=True,
        presentation=ActionPresentation(label="Unpin", frame=True),
    ),
    ToolDef(
        name="delete_conversation",
        description=(
            "Delete this conversation: it leaves every member's conversation list and its own "
            "owner reaches it by link alone. Only the member who owns it may delete it. The "
            "transcript itself stays and retention closes it."
        ),
        input_model=ConversationMarkInput,
        handler=ConversationDelete(
            deleted=True,
            said="Deleted. It is off the conversation list.",
        ).act,
        side_effecting=True,
        parallel_safe=True,
        bound=ObjectBinding(kind=CONVERSATION_KIND, binding="instance"),
        agent_targetable=True,
        presentation=ActionPresentation(
            label="Delete", confirm="Delete this conversation?", frame=True
        ),
    ),
    ToolDef(
        name="restore_conversation",
        description=(
            "Put this deleted conversation back in the conversation list. Only the member who "
            "owns it may restore it."
        ),
        input_model=ConversationMarkInput,
        handler=ConversationDelete(
            deleted=False,
            said="Restored. It is back in the conversation list.",
        ).act,
        side_effecting=True,
        parallel_safe=True,
        bound=ObjectBinding(kind=CONVERSATION_KIND, binding="instance"),
        agent_targetable=True,
        presentation=ActionPresentation(label="Restore", frame=True),
    ),
)


def _agent_conversations() -> sa.Select:
    member_name = sa.func.coalesce(tables.agent.c.archived_name, tables.agent.c.name)
    acting = _acting_member()
    return (
        sa.select(
            tables.conversation.c.id,
            tables.conversation.c.surface,
            tables.conversation.c.surface_label,
            tables.conversation.c.audience,
            tables.conversation.c.title,
            tables.conversation.c.created_at,
            tables.conversation.c.updated_at,
            tables.conversation.c.archived_at,
            tables.conversation.c.deleted_at,
            member_name.label("agent_name"),
            (
                sa.select(tables.conversation_pin.c.pinned_at)
                .where(
                    tables.conversation_pin.c.workspace_id == ws_current().workspace_id,
                    tables.conversation_pin.c.conversation_id == tables.conversation.c.id,
                    tables.conversation_pin.c.member_id == acting,
                )
                .correlate(tables.conversation)
                .scalar_subquery()
            ).label("pinned_at")
            if acting is not None
            else sa.literal(None).label("pinned_at"),
        )
        .select_from(
            tables.conversation.join(
                tables.agent, tables.conversation.c.agent_id == tables.agent.c.id
            )
        )
        .where(
            tables.conversation.c.workspace_id == ws_current().workspace_id,
            tables.conversation.c.agent_id == object_agent_id(),
        )
    )


def _visible(
    subjects: frozenset[str], *, archived: bool | None = None, deleted: bool | None = None
) -> sa.Select:
    query = _agent_conversations().where(tables.conversation.c.audience.in_(subjects))
    # The marks narrow the read in the query; a deleted row answers its owner alone — the rule
    # `_owns` reads in the rail.
    if archived is not None or deleted is not None:
        owns = sa.func.coalesce(
            tables.conversation.c.member_id,
            sa.select(tables.turn.c.speaker_member_id)
            .where(
                tables.turn.c.workspace_id == tables.conversation.c.workspace_id,
                tables.turn.c.conversation_id == tables.conversation.c.id,
                tables.turn.c.speaker_member_id.is_not(None),
            )
            .order_by(tables.turn.c.seq.asc())
            .limit(1)
            .correlate(tables.conversation)
            .scalar_subquery(),
        )
        if deleted is True:
            query = query.where(
                tables.conversation.c.deleted_at.is_not(None),
                owns == _acting_member(),
            )
        else:
            query = query.where(
                tables.conversation.c.deleted_at.is_(None),
                tables.conversation.c.archived_at.is_not(None)
                if archived is True
                else tables.conversation.c.archived_at.is_(None),
            )
    return query


def _acting_member() -> UUID | None:
    """The member the turn reads as, when the turn reads as one — the subject the delete mark's
    ownership rule answers. An agent reading with no member speaking reads no one's deleted rows."""
    return _acting_member_var.get()


class ActingMember:
    """The member the standing turn reads as, bound beside the workspace and agent scopes — the
    one fact the delete mark's ownership predicate needs that the ambient scopes do not hold."""

    current: ClassVar[ContextVar[UUID | None]] = ContextVar("acting_member", default=None)


_acting_member_var = ActingMember.current


def _member_row(entry: ListedConversation) -> ObjectRow:
    speakers = [who.sender or who.email for who in entry.speakers]
    stamp = entry.summary.last_turn_at or entry.summary.created_at
    portal = entry.summary.surface == PORTAL_SURFACE or entry.summary.surface.startswith(
        EXTENSION_SURFACE_PREFIX
    )
    return ObjectRow(
        name=str(entry.summary.id),
        summary=entry.title,
        fields={
            "title": entry.title,
            "opening": opening_sentence(entry.summary.opening_message),
            "mine": entry.mine,
            "speaker": None if entry.mine or not speakers else speakers[0],
            "surface": entry.summary.surface,
            "surface_label": entry.surface_label,
            "audience": entry.audience,
            "member_email": entry.summary.member_email,
            "owner_email": entry.owner_email,
            "owner_name": entry.owner_name,
            "portal": portal,
            "source": None if portal else entry.source,
            "last_at": stamp.isoformat(),
            "turn": entry.turn,
            "automation_kind": None if entry.automation is None else entry.automation.kind,
            "automation_name": None if entry.automation is None else entry.automation.name,
            "automation_title": None if entry.automation is None else entry.automation.title,
            "unread": entry.unread,
            "readable": entry.readable,
            "disclosable": entry.disclosable,
            "speakable": entry.speakable,
            "archived": entry.archived,
            "deleted": entry.deleted,
            "pinned": entry.pinned,
        },
    )


def _row(row: sa.Row, *, private: bool = False) -> ObjectRow:
    origin = (
        f"{row.surface} conversation"
        if row.surface_label is None
        else f"{row.surface_label} on {row.surface}"
    )
    title = None if private else row.title
    fields: dict[str, JsonValue] = {
        "surface": row.surface,
        # The marks ride every row the agent-facing listing serves, the way `_member_row` writes
        # them on the rail's: the declared filters can only answer fields the rows carry.
        "archived": row.archived_at is not None,
        "deleted": row.deleted_at is not None,
        "pinned": row.pinned_at is not None,
    }
    if row.surface_label is not None:
        fields["surface_label"] = row.surface_label
    if private:
        fields["private"] = True
    if title is not None:
        fields["title"] = title
    return ObjectRow(
        name=str(row.id),
        summary=title or f"{origin}, created {row.created_at.date().isoformat()}",
        fields=fields,
    )


def _detail(row: sa.Row) -> ObjectDetail[ConversationSpec]:
    return ObjectDetail(
        spec=ConversationSpec(
            surface=row.surface, surface_label=row.surface_label, audience=row.audience
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
        links=(
            ObjectLink(
                relation="scoped_to", target=ObjectRef(kind=AGENT_KIND, name=row.agent_name)
            ),
        ),
    )


CONVERSATION_OBJECT = ObjectKind(
    name=CONVERSATION_KIND,
    description=(
        "A past conversation: its surface, origin label, audience, timestamps, and text "
        "transcript. Created by surfaces; a member files one with the archive, pin and delete "
        "actions and changes nothing else about it."
    ),
    guidance=(
        "Conversations resolve artifact `created_in` and scheduled-task `reports_to` links: get "
        "one by its id to see which surface and audience it runs on and when it started; its own "
        "`scoped_to` link names the agent it runs with. Reads "
        "show the selected agent's conversations visible to the conversation and exact requester. "
        "A speaking workspace admin also gets another member's private conversation of the "
        "selected agent as metadata — its spec and links, with no status and no transcript — and "
        'lists such rows only under the explicit filter {"private": true}; a channel shared with '
        "another organization never widens. "
        "Filter or order a listing on `surface` and on `surface_label`, the surface's own name for "
        "where the conversation runs — a Slack channel as `#general`, a Slack DM as `DM`. A "
        "conversation whose surface names no origin carries no `surface_label`. "
        "A readable conversation carries `title`, what its members call it, once it has been "
        "named; search on it to find the conversation a member names. "
        "A member listing also carries `mine`, `speaker`, `audience`, `member_email`, "
        "`owner_email` — whose the conversation is, the member it is bound to or whoever spoke "
        "first in a workspace one — and `owner_name` where a surface reported them under a name, "
        "`last_at`, `turn` — the liveest turn the conversation holds as `running`, `queued` or "
        "`parked`, and `idle` where it holds none — `automation_kind`, `automation_name` and "
        "`automation_title`, naming what last fired a turn in it where an automation did, and "
        "`unread`, whether it moved after the "
        "member last read it and last spoke in it. Order by "
        "`last_at` desc for the newest activity first. "
        "It also carries the filing marks the actions on a row set and clear: `archived` and "
        "`deleted`, which the conversation carries for everyone who lists it, and `pinned`, which "
        "is the reading member's own. A listing answers the "
        "conversations under no mark; ask for the archived ones with the filter "
        '{"archived": true}, and for your own deleted ones with {"deleted": true}. A member\'s '
        "next message in an archived conversation clears the archive mark. Only the member who "
        "owns a conversation may delete or restore it. "
        "`status.workspace_path` writes a visible text exchange into your workspace. Conversations "
        "cannot be created or changed through objects."
    ),
    spec_model=ConversationSpec,
    store=ConversationObjects(),
    list_fields=frozenset(
        {
            "surface",
            "surface_label",
            "title",
            "opening",
            "mine",
            "speaker",
            "portal",
            "audience",
            "member_email",
            "owner_email",
            "owner_name",
            "source",
            "last_at",
            "turn",
            "automation_kind",
            "automation_name",
            "automation_title",
            "unread",
            "readable",
            "disclosable",
            "speakable",
            "archived",
            "deleted",
            "pinned",
            "private",
        }
    ),
    agent_target_verbs=frozenset({"list", "get"}),
)
