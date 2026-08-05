"""The core-registered `conversation` object kind: past conversations as read-only objects.

Artifacts and scheduled tasks link to the conversation they came from or report into; this kind is
what those links resolve to — the surface, the origin label that surface wrote, audience, row
timestamps, and, through status, the text exchange written into the turn's workspace. Surfaces
create conversations, so every mutation is refused."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field

from ufo.agents import AGENT_KIND
from ufo.blob import BlobNotFound
from ufo.db import workspace_tx
from ufo.ext.context import JsonValue
from ufo.models.interface import TextBlock
from ufo.object_scope import object_agent_id
from ufo.objects import (
    MATERIALIZE_MAX_BYTES,
    ObjectDetail,
    ObjectKind,
    ObjectLink,
    ObjectListQuery,
    ObjectPage,
    ObjectRef,
    ObjectRow,
    UnknownObject,
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
    """Read-only handlers over the selected agent's conversations visible to the caller. Status
    materializes a visible transcript. Resolves artifact and scheduled-task links; every mutation
    refuses."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        rows = []
        for row in await self._visible_rows(ctx):
            origin = (
                f"{row.surface} conversation"
                if row.surface_label is None
                else f"{row.surface_label} on {row.surface}"
            )
            fields: dict[str, JsonValue] = {"surface": row.surface}
            if row.surface_label is not None:
                fields["surface_label"] = row.surface_label
            rows.append(
                ObjectRow(
                    name=str(row.id),
                    summary=f"{origin}, created {row.created_at.date().isoformat()}",
                    fields=fields,
                )
            )
        return object_page(tuple(rows), query)

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ConversationSpec] | None:
        row = await self._find(ctx, name)
        if row is None:
            return None
        return ObjectDetail(
            spec=ConversationSpec(
                surface=row.surface,
                surface_label=row.surface_label,
                audience=row.audience,
            ),
            created_at=row.created_at,
            updated_at=row.updated_at,
            links=(
                ObjectLink(
                    relation="scoped_to",
                    target=ObjectRef(kind=AGENT_KIND, name=row.agent_name),
                ),
            ),
        )

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
        row = await self._find(ctx, name)
        if row is None:
            return None
        exchange = await self._exchange(ctx, row.id)
        if not await self._unchanged_visible(ctx, row):
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

    async def _unchanged_visible(self, ctx: ToolContext, row: sa.Row) -> bool:
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(
                        sa.exists(
                            self._visible(ctx).where(
                                tables.conversation.c.id == row.id,
                                tables.conversation.c.audience == row.audience,
                            )
                        )
                    )
                )
            ).scalar_one()

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
        return (
            sa.select(
                tables.conversation.c.id,
                tables.conversation.c.surface,
                tables.conversation.c.surface_label,
                tables.conversation.c.audience,
                tables.conversation.c.created_at,
                tables.conversation.c.updated_at,
                tables.agent.c.name.label("agent_name"),
            )
            .select_from(
                tables.conversation.join(
                    tables.agent, tables.conversation.c.agent_id == tables.agent.c.id
                )
            )
            .where(
                tables.conversation.c.workspace_id == ws_current().workspace_id,
                tables.conversation.c.agent_id == object_agent_id(),
                tables.conversation.c.audience.in_(ctx.read_subjects),
            )
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
        "Filter or order a listing on `surface` and on `surface_label`, the surface's own name for "
        "where the conversation runs — a Slack channel as `#general`, a Slack DM as `Direct "
        "message`. A conversation whose surface names no origin carries no `surface_label`. "
        "`status.workspace_path` writes a visible text exchange into your workspace. Conversations "
        "cannot be created, changed, or deleted through objects."
    ),
    spec_model=ConversationSpec,
    store=ConversationObjects(),
    list_fields=frozenset({"surface", "surface_label"}),
    agent_target_verbs=frozenset({"list", "get"}),
)
