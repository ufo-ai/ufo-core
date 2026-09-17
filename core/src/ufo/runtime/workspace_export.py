"""Admin-scoped reads of runtime-owned conversation and artifact records."""

import asyncio
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PureWindowsPath
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel
from pydantic_core import to_json

from ufo.db import workspace_tx
from ufo.runtime.tools.context import ToolContext
from ufo.runtime.turns.audience import conversation_audience, readable_audiences
from ufo.runtime.workspace import ws_current
from ufo.schema import tables

EXPORT_WORKERS = 1
EXPORT_POOL = ThreadPoolExecutor(max_workers=EXPORT_WORKERS, thread_name_prefix="workspace-export")
EXPORT_ROW_LIMIT = 100_000
EXPORT_DOCUMENT_LIMIT = 32 * 1024 * 1024


@dataclass(frozen=True)
class ExportDocument:
    path: str
    data: bytes


class ExportArtifact(BaseModel):
    id: UUID
    turn_id: UUID
    conversation_id: UUID
    filename: str
    subject: str | None
    member_id: UUID | None
    attached_by_member: bool
    created_at: datetime
    media_type: str
    size_bytes: int
    blob_key: str
    digest: str | None
    path: str


@dataclass(frozen=True)
class ExportRecords:
    started_at: datetime
    documents: tuple[ExportDocument, ...]
    artifacts: tuple[ExportArtifact, ...]


async def export_document(path: str, rows: Sequence[sa.Row]) -> ExportDocument:
    """Encode complete rows off the event loop, refusing a truncated or oversized read."""
    return await asyncio.get_running_loop().run_in_executor(
        EXPORT_POOL, _encode_document, path, rows
    )


def _encode_document(path: str, rows: Sequence[sa.Row]) -> ExportDocument:
    if len(rows) > EXPORT_ROW_LIMIT:
        raise ValueError(f"export refused: {path} exceeds {EXPORT_ROW_LIMIT} records")
    data = bytearray()
    for row in rows:
        data.extend(to_json(dict(row._mapping)))
        data.extend(b"\n")
        if len(data) > EXPORT_DOCUMENT_LIMIT:
            raise ValueError(f"export refused: {path} exceeds {EXPORT_DOCUMENT_LIMIT} bytes")
    return ExportDocument(path=path, data=bytes(data))


@dataclass(frozen=True)
class WorkspaceExport:
    ctx: ToolContext

    async def read(self) -> ExportRecords:
        """Read member-visible records after checking the admin and private delivery context."""
        ctx = self.ctx
        if not await ctx.require_speaking_admin("a workspace admin exports workspace data"):
            raise PermissionError("only a workspace admin can export workspace data")
        member_id = ctx.require_speaker()
        if ctx.audience != conversation_audience(member_id) or not await ctx.agent_is_main():
            raise PermissionError(
                "request the export in your private conversation with the main agent"
            )
        workspace_id = ws_current().workspace_id
        if workspace_id != ctx.turn.workspace_id:
            raise PermissionError("export workspace does not match the requesting turn")
        started_at = datetime.now(UTC)
        c, t, a = tables.conversation, tables.turn, tables.agent
        visible = (
            sa.select(c.c.id)
            .join(a, a.c.id == c.c.agent_id)
            .where(
                c.c.workspace_id == workspace_id,
                a.c.workspace_id == workspace_id,
                c.c.audience.in_(readable_audiences(member_id)),
                c.c.deleted_at.is_(None),
                sa.or_(
                    a.c.is_main, a.c.visibility == "workspace", a.c.owner_member_id == member_id
                ),
            )
        )
        turns = sa.select(t.c.id).where(
            t.c.workspace_id == workspace_id, t.c.conversation_id.in_(visible)
        )
        m, r, f = tables.inbound_message, tables.mid_turn_reply, tables.shared_artifact
        queries = (
            (
                "conversations.jsonl",
                sa.select(
                    c.c.id,
                    c.c.agent_id,
                    c.c.surface,
                    c.c.title,
                    c.c.audience,
                    c.c.created_at,
                    c.c.updated_at,
                    c.c.archived_at,
                )
                .where(c.c.id.in_(visible))
                .order_by(c.c.id),
            ),
            (
                "turns.jsonl",
                sa.select(
                    t.c.id,
                    t.c.conversation_id,
                    t.c.seq,
                    t.c.agent_id,
                    t.c.status,
                    t.c.inbound,
                    t.c.speaker_member_id,
                    t.c.parent_turn_id,
                    t.c.terminal["text"].as_string().label("reply"),
                    t.c.terminal["question"].label("question"),
                    t.c.created_at,
                    t.c.updated_at,
                )
                .where(t.c.id.in_(turns))
                .order_by(t.c.conversation_id, t.c.seq),
            ),
            (
                "messages.jsonl",
                sa.select(
                    m.c.id,
                    m.c.conversation_id,
                    m.c.seq,
                    m.c.body,
                    m.c.speaker_member_id,
                    m.c.admitted_turn_id,
                    m.c.consumed_turn_id,
                    m.c.created_at,
                )
                .where(m.c.workspace_id == workspace_id, m.c.conversation_id.in_(visible))
                .order_by(m.c.conversation_id, m.c.seq),
            ),
            (
                "replies.jsonl",
                sa.select(
                    r.c.id,
                    r.c.turn_id,
                    r.c.round_index,
                    r.c.span_index,
                    r.c.text,
                    r.c.status,
                    r.c.created_at,
                )
                .where(r.c.workspace_id == workspace_id, r.c.turn_id.in_(turns))
                .order_by(r.c.turn_id, r.c.round_index, r.c.span_index),
            ),
        )
        document_rows = []
        async with workspace_tx(snapshot=True) as connection:
            audience = (
                await connection.execute(
                    sa.select(c.c.audience).where(
                        c.c.workspace_id == workspace_id,
                        c.c.id == ctx.turn.conversation_id,
                    )
                )
            ).scalar_one()
            if audience != conversation_audience(member_id):
                raise PermissionError("export delivery conversation is not private to the admin")
            for path, query in queries:
                rows = (await connection.execute(query.limit(EXPORT_ROW_LIMIT + 1))).all()
                document_rows.append((path, rows))
            files = (
                await connection.execute(
                    sa.select(
                        f.c.id,
                        f.c.turn_id,
                        t.c.conversation_id,
                        f.c.filename,
                        f.c.subject,
                        f.c.member_id,
                        f.c.attached_by_member,
                        f.c.created_at,
                        f.c.media_type,
                        f.c.size_bytes,
                        f.c.blob_key,
                        f.c.digest,
                    )
                    .join(t, t.c.id == f.c.turn_id)
                    .where(
                        f.c.workspace_id == workspace_id,
                        f.c.turn_id.in_(turns),
                        f.c.role == "file",
                        f.c.is_workspace_export.is_(False),
                    )
                    .order_by(f.c.id)
                    .limit(EXPORT_ROW_LIMIT + 1)
                )
            ).all()
            if len(files) > EXPORT_ROW_LIMIT:
                raise ValueError("export refused: too many artifacts")
        return await asyncio.get_running_loop().run_in_executor(
            EXPORT_POOL, self._records, started_at, document_rows, files
        )

    def _records(
        self,
        started_at: datetime,
        document_rows: Sequence[tuple[str, Sequence[sa.Row]]],
        files: Sequence[sa.Row],
    ) -> ExportRecords:
        documents = tuple(_encode_document(path, rows) for path, rows in document_rows)
        artifacts = tuple(
            ExportArtifact(
                **dict(row._mapping),
                path=f"artifacts/{row.id}-{PureWindowsPath(row.filename).name}",
            )
            for row in files
        )
        return ExportRecords(started_at, documents, artifacts)
