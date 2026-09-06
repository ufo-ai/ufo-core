"""The core-registered `conversation` object kind: past conversations as read-only objects.

Artifacts and scheduled tasks link to the conversation they came from or report into; this kind is
what those links resolve to — the surface, the origin label that surface wrote, audience, row
timestamps, and, through status, the text exchange written into the turn's workspace. The portal
resolves the same links against the same row: `member_detail` answers a signed-in member outside a
turn, on the subjects their own conversation carries. Surfaces create conversations, so every
mutation is refused."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field

from ufo.blob import BlobNotFound
from ufo.db import workspace_tx
from ufo.harness.models.interface import TextBlock
from ufo.runtime.ext.context import ExtensionContext, JsonValue
from ufo.runtime.ext.surface import ConversationDirectory, ListedConversation
from ufo.runtime.kinds.agents import AGENT_KIND
from ufo.runtime.object_name import ObjectRef
from ufo.runtime.object_scope import object_agent_id
from ufo.runtime.objects import (
    MATERIALIZE_MAX_BYTES,
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
from ufo.runtime.tools.context import ToolContext
from ufo.runtime.turns.audience import (
    FOREIGN_AUDIENCE_PREFIX,
    audience_subjects,
    conversation_audience,
)
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
    a foreign-audience turn never widens. Resolves artifact and scheduled-task links; every
    mutation refuses."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        rows = tuple(_row(row) for row in await self._rows(ctx.read_subjects, conversation_id=None))
        if query.filters.get("private") is True and await self._widens_for_admin(ctx):
            rows += tuple(
                _row(row, private=True)
                for row in await self._private_rows(ctx, conversation_id=None)
            )
        return object_page(rows, query)

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
        `CONVERSATION_OTHERS_LIMIT` rows whatever the workspace holds."""
        directory = ConversationDirectory(ws_current().workspace_id)
        agent_id = object_agent_id()
        sides: tuple[tuple[Literal["mine", "others"], int], ...] = (
            ("mine", CONVERSATION_MINE_LIMIT),
            ("others", CONVERSATION_OTHERS_LIMIT),
        )
        portal = query.filters.get("portal")
        rows: list[ObjectRow] = []
        for participation, limit in sides:
            for entry in await directory.list(
                agent_id,
                member_id,
                admin=False,
                limit=limit,
                participation=participation,
                member_admitted=True,
                portal=portal if isinstance(portal, bool) else None,
            ):
                if entry.title:
                    rows.append(_member_row(entry, mine=participation == "mine"))
        return object_page(tuple(rows), query)

    async def member_detail(
        self,
        ext: ExtensionContext | None,
        name: str,
        *,
        member_id: UUID,
        admin: bool,
    ) -> MemberObject[ConversationSpec] | None:
        """One conversation as the portal reads it outside a turn — the row `list` renders beside
        the detail `get` reads, gated on the subjects a member's own conversation carries: their
        own and the workspace-shared. A room's conversation and another member's private one are
        absent here for everyone, an admin included; the recorded acknowledgement opens transcript
        content, never this row. The agent is the caller's ambient one, the same wall `list`
        answers behind."""
        row = await self._find(audience_subjects(conversation_audience(member_id)), name)
        return None if row is None else MemberObject(row=_row(row), detail=_detail(row))

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
        self, subjects: frozenset[str], *, conversation_id: UUID | None
    ) -> tuple[sa.Row, ...]:
        query = _visible(subjects)
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


def _agent_conversations() -> sa.Select:
    member_name = sa.func.coalesce(tables.agent.c.archived_name, tables.agent.c.name)
    return (
        sa.select(
            tables.conversation.c.id,
            tables.conversation.c.surface,
            tables.conversation.c.surface_label,
            tables.conversation.c.audience,
            tables.conversation.c.created_at,
            tables.conversation.c.updated_at,
            member_name.label("agent_name"),
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


def _visible(subjects: frozenset[str]) -> sa.Select:
    return _agent_conversations().where(tables.conversation.c.audience.in_(subjects))


def _member_row(entry: ListedConversation, *, mine: bool) -> ObjectRow:
    speakers = [who.sender or who.email for who in entry.speakers]
    stamp = entry.summary.last_turn_at or entry.summary.created_at
    return ObjectRow(
        name=str(entry.summary.id),
        summary=entry.title,
        fields={
            "title": entry.title,
            "mine": mine,
            "speaker": None if mine or not speakers else speakers[0],
            "surface": entry.summary.surface,
            "surface_label": entry.surface_label,
            "portal": (
                entry.summary.surface == PORTAL_SURFACE
                or entry.summary.surface.startswith(EXTENSION_SURFACE_PREFIX)
            ),
            "last_at": stamp.isoformat(),
        },
    )


def _row(row: sa.Row, *, private: bool = False) -> ObjectRow:
    origin = (
        f"{row.surface} conversation"
        if row.surface_label is None
        else f"{row.surface_label} on {row.surface}"
    )
    fields: dict[str, JsonValue] = {"surface": row.surface}
    if row.surface_label is not None:
        fields["surface_label"] = row.surface_label
    if private:
        fields["private"] = True
    return ObjectRow(
        name=str(row.id),
        summary=f"{origin}, created {row.created_at.date().isoformat()}",
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
        "transcript. Created by surfaces; every mutation is refused."
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
        "A member listing also carries `title`, `mine`, `speaker`, and `last_at` — order by "
        "`last_at` desc for the newest activity first. "
        "`status.workspace_path` writes a visible text exchange into your workspace. Conversations "
        "cannot be created, changed, or deleted through objects."
    ),
    spec_model=ConversationSpec,
    store=ConversationObjects(),
    list_fields=frozenset(
        {"surface", "surface_label", "title", "mine", "speaker", "portal", "last_at", "private"}
    ),
    agent_target_verbs=frozenset({"list", "get"}),
)
