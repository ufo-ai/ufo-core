"""The core-registered `conversation` object kind: past conversations as read-only objects.

Artifacts and scheduled tasks link to the conversation they came from or report into; this kind is
what those links resolve to — the surface, disclosure member, row timestamps, and, through status,
the text exchange written into the turn's workspace. A conversation is visible inside its agent
when it has no disclosure member or the caller is that member; surfaces create conversations, so
every mutation is refused."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field

from ufo.agent_scope import agent_current
from ufo.blob import BlobNotFound
from ufo.db import workspace_tx
from ufo.ext.context import JsonValue
from ufo.models.interface import TextBlock
from ufo.objects import (
    MATERIALIZE_MAX_BYTES,
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
from ufo.transcript import TranscriptDecodeError, decode, transcript_key
from ufo.workspace import ws_current

CONVERSATION_KIND = "conversation"
TRANSCRIPT_WORKSPACE_DIR = "transcripts"
CONVERSATIONS_ARE_SURFACE_MADE = (
    "conversations are created by surfaces and closed by retention, never authored"
)


class ConversationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    surface: str = Field(description="The chat surface the conversation runs on.")
    member_id: str | None = Field(
        description="The disclosure member the conversation belongs to; null for a shared "
        "(channel) conversation."
    )


@dataclass(frozen=True)
class ConversationObjects:
    """Read-only handlers over the agent's `conversation` rows the caller may see: a row with no
    disclosure member is shared, a member-bound row is visible to that member alone. Another
    agent's conversations are not found. Status materializes that same row's transcript. Resolves
    artifact `created_in` and scheduled-task `reports_to` links; every mutation refuses."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        rows = tuple(
            ObjectRow(
                name=str(row.id),
                summary=f"{row.surface} conversation, created {row.created_at.date().isoformat()}",
                fields={"surface": row.surface},
            )
            for row in await self._visible_rows(ctx)
        )
        return object_page(rows, query)

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ConversationSpec] | None:
        row = await self._find(ctx, name)
        if row is None:
            return None
        return ObjectDetail(
            spec=ConversationSpec(
                surface=row.surface,
                member_id=None if row.member_id is None else str(row.member_id),
            ),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None:
        row = await self._find(ctx, name)
        if row is None or row.member_id != ctx.audience_member_id:
            return None
        exchange = await self._exchange(ctx, row.id)
        body = "\n".join(exchange).encode()
        path: str | None = None
        if body and len(body) <= MATERIALIZE_MAX_BYTES:
            path = f"{TRANSCRIPT_WORKSPACE_DIR}/{row.id}.txt"
            await ctx.sandbox.write_file(path, body)
        return {"messages": len(exchange), "size_bytes": len(body), "workspace_path": path}

    async def apply(
        self, ctx: ToolContext, name: str, spec: ConversationSpec, old: ConversationSpec | None
    ) -> None:
        raise VerbNotSupported(CONVERSATIONS_ARE_SURFACE_MADE)

    async def delete(self, ctx: ToolContext, name: str) -> None:
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

    async def _find(self, ctx: ToolContext, name: str) -> sa.Row | None:
        try:
            conversation_id = UUID(name)
        except ValueError:
            return None
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    self._visible(ctx).where(tables.conversation.c.id == conversation_id)
                )
            ).one_or_none()

    async def _visible_rows(self, ctx: ToolContext) -> tuple[sa.Row, ...]:
        async with workspace_tx() as connection:
            rows = (await connection.execute(self._visible(ctx))).all()
        return tuple(rows)

    def _visible(self, ctx: ToolContext) -> sa.Select:
        member = ctx.audience_member_id
        visibility = (
            tables.conversation.c.member_id.is_(None)
            if member is None
            else sa.or_(
                tables.conversation.c.member_id.is_(None),
                tables.conversation.c.member_id == member,
            )
        )
        return sa.select(
            tables.conversation.c.id,
            tables.conversation.c.surface,
            tables.conversation.c.member_id,
            tables.conversation.c.created_at,
            tables.conversation.c.updated_at,
        ).where(
            tables.conversation.c.workspace_id == ws_current().workspace_id,
            tables.conversation.c.agent_id == agent_current().agent_id,
            visibility,
        )


CONVERSATION_OBJECT = ObjectKind(
    name=CONVERSATION_KIND,
    description=(
        "A past conversation: its surface, disclosure member, timestamps, and text transcript. "
        "Created by surfaces; every mutation is refused."
    ),
    guidance=(
        "Conversations resolve artifact `created_in` and scheduled-task `reports_to` links: get "
        "one by its id to see which surface it runs on, whose private conversation it is "
        "(member_id null means a shared channel), and when it started. Reads show this agent's "
        "shared conversations plus the audience member's own; another agent's conversations are "
        "not found. When a conversation has the same audience as the current turn, "
        "`status.workspace_path` is its text exchange written into your workspace. Conversations "
        "cannot be created, changed, or deleted through objects."
    ),
    spec_model=ConversationSpec,
    store=ConversationObjects(),
    list_fields=frozenset({"surface"}),
)
