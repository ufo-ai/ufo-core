"""Admin-scoped reads of runtime-owned conversation and artifact records."""

import asyncio
import hashlib
import tempfile
from collections.abc import AsyncIterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING, BinaryIO
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel
from pydantic_core import to_json
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.blob import WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.runtime.turns.audience import FOREIGN_AUDIENCE_PREFIX, conversation_audience
from ufo.runtime.workspace import ws_current
from ufo.schema import tables

if TYPE_CHECKING:
    from ufo.runtime.tools.context import ToolContext

EXPORT_FILENAME = "workspace-export.tar"
EXPORT_WORKERS = 1
EXPORT_POOL = ThreadPoolExecutor(max_workers=EXPORT_WORKERS, thread_name_prefix="workspace-export")
EXPORT_BATCH_ROWS = 256


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
class ExportFile:
    record: ExportArtifact
    content: AsyncIterator[bytes]


def export_key(export_id: UUID) -> str:
    """The object key of one export in the bound workspace."""
    return f"exports/{export_id}/{EXPORT_FILENAME}"


@dataclass(frozen=True)
class ExportStorage:
    """Write and remove export objects; original file reads require an authorized snapshot."""

    _blob: WorkspaceBlobStore

    async def put_stream(
        self, export_id: UUID, chunks: AsyncIterator[bytes], *, part_size_bytes: int
    ) -> None:
        """Upload one private, expiring archive in the export namespace."""
        await self._blob.put_stream(
            export_key(export_id),
            chunks,
            part_size_bytes=part_size_bytes,
            tags={"ufo-export": "true"},
        )

    async def delete(self, export_id: UUID) -> None:
        """Remove one archive without access to other stored files."""
        await self._blob.delete(export_key(export_id))


async def export_documents(
    connection: AsyncConnection, query: sa.Select, path: str
) -> AsyncIterator[ExportDocument]:
    """Encode numbered JSON files from a server-side cursor with bounded row batches."""
    async with connection.stream(query) as result:
        index = 0
        async for rows in result.partitions(EXPORT_BATCH_ROWS):
            data = await asyncio.get_running_loop().run_in_executor(EXPORT_POOL, _encode, rows)
            yield ExportDocument(f"{path}/{index:06d}.jsonl", data)
            index += 1


def _encode(rows: Sequence[sa.Row]) -> bytes:
    return b"".join(to_json(dict(row._mapping)) + b"\n" for row in rows)


async def require_export_admin(connection: AsyncConnection, member_id: UUID) -> None:
    """Require a currently seated admin in the bound workspace."""
    member = tables.member
    allowed = await connection.scalar(
        sa.select(member.c.id).where(
            member.c.workspace_id == ws_current().workspace_id,
            member.c.id == member_id,
            member.c.is_admin.is_(True),
            member.c.seated_at.is_not(None),
        )
    )
    if allowed is None:
        raise PermissionError("only a seated workspace admin can export workspace data")


@dataclass(frozen=True)
class WorkspaceExport:
    ctx: "ToolContext"

    async def authorize(self) -> UUID:
        """Read all workspace records after checking the admin and private delivery context."""
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
        async with workspace_tx() as connection:
            await require_export_admin(connection, member_id)
            audience = await connection.scalar(
                sa.select(tables.conversation.c.audience).where(
                    tables.conversation.c.workspace_id == workspace_id,
                    tables.conversation.c.id == ctx.turn.conversation_id,
                )
            )
            if audience != conversation_audience(member_id):
                raise PermissionError("export delivery conversation is not private to the admin")
        return member_id


@asynccontextmanager
async def export_reader(member_id: UUID, storage: ExportStorage) -> AsyncIterator["_ExportReader"]:
    """Open an authorized, read-only workspace snapshot for runtime and extension records."""
    async with workspace_tx(snapshot=True, read_only=True) as connection:
        await require_export_admin(connection, member_id)
        yield _ExportReader(connection, storage._blob)


@dataclass(frozen=True)
class _ExportReader:
    connection: AsyncConnection
    _blob: WorkspaceBlobStore

    async def documents(self) -> AsyncIterator[ExportDocument]:
        """Read runtime records within the caller's authorized database snapshot."""
        workspace_id = ws_current().workspace_id
        c, t = tables.conversation, tables.turn
        conversations = sa.select(c.c.id).where(
            c.c.workspace_id == workspace_id,
            ~c.c.audience.startswith(FOREIGN_AUDIENCE_PREFIX),
        )
        turns = sa.select(t.c.id).where(
            t.c.workspace_id == workspace_id, t.c.conversation_id.in_(conversations)
        )
        m, r = tables.inbound_message, tables.mid_turn_reply
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
                    c.c.deleted_at,
                )
                .where(c.c.id.in_(conversations))
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
                .where(m.c.workspace_id == workspace_id, m.c.conversation_id.in_(conversations))
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
        for path, query in queries:
            async for document in export_documents(
                self.connection, query, path.removesuffix(".jsonl")
            ):
                yield document

    @asynccontextmanager
    async def artifacts(self) -> AsyncIterator[AsyncIterator[ExportFile]]:
        """Stage authorized records for bounded file reads after the snapshot closes."""
        workspace_id = ws_current().workspace_id
        f, t, c = tables.shared_artifact, tables.turn, tables.conversation
        query = (
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
            .join(c, c.c.id == t.c.conversation_id)
            .where(
                f.c.workspace_id == workspace_id,
                t.c.workspace_id == workspace_id,
                c.c.workspace_id == workspace_id,
                ~c.c.audience.startswith(FOREIGN_AUDIENCE_PREFIX),
                f.c.role == "file",
                f.c.is_workspace_export.is_(False),
            )
            .order_by(f.c.id)
        )
        staged = await asyncio.to_thread(tempfile.TemporaryFile, mode="w+b")
        try:
            async with self.connection.stream(query) as result:
                async for rows in result.partitions(EXPORT_BATCH_ROWS):
                    for row in rows:
                        artifact = ExportArtifact(
                            **dict(row._mapping),
                            path=f"artifacts/{row.id}-{PureWindowsPath(row.filename).name}",
                        )
                        await asyncio.to_thread(
                            staged.write, artifact.model_dump_json().encode() + b"\n"
                        )
            await asyncio.to_thread(staged.seek, 0)
            yield self._staged_files(staged)
        finally:
            await asyncio.to_thread(staged.close)

    async def _staged_files(self, staged: BinaryIO) -> AsyncIterator[ExportFile]:
        while line := await asyncio.to_thread(staged.readline):
            artifact = ExportArtifact.model_validate_json(line)
            yield ExportFile(
                artifact,
                self._artifact_bytes(artifact.blob_key, artifact.size_bytes, artifact.digest),
            )

    async def _artifact_bytes(
        self, key: str, expected: int, digest: str | None
    ) -> AsyncIterator[bytes]:
        size = 0
        checksum = hashlib.sha256()
        async for chunk in self._blob.get_stream(key):
            size += len(chunk)
            if size > expected:
                raise ValueError("export refused: artifact size changed")
            checksum.update(chunk)
            yield chunk
        if size != expected or (
            digest is not None and checksum.hexdigest() != digest.removeprefix("sha256:")
        ):
            raise ValueError("export refused: artifact content does not match its record")
