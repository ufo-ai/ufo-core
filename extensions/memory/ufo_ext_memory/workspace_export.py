"""Durable admin exports of workspace conversations, files, and memory."""

import asyncio
import tarfile
import tempfile
from collections.abc import AsyncGenerator, AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack, aclosing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from pydantic_core import to_json
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.sdk.audience import FOREIGN_AUDIENCE_PREFIX
from ufo.sdk.context import ExtensionContext
from ufo.sdk.export import (
    WorkspaceExport,
    export_documents,
    export_key,
    export_reader,
    require_export_admin,
)
from ufo.sdk.jobs import JobSpec, owner_candidates
from ufo.sdk.tools import (
    ActionPresentation,
    ObjectBinding,
    TextContent,
    ToolContext,
    ToolDef,
    ToolResult,
)
from ufo_ext_memory.condenser import memory_profile
from ufo_ext_memory.store import memory_item

ARCHIVE_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="export-archive")
TAR_FILE_MODE = 0o600
TAR_BLOCK_BYTES = 512
TAR_END = bytes(TAR_BLOCK_BYTES * 2)
EXPORT_RETENTION = timedelta(hours=24)
EXPORT_LINK_SECONDS = 900
EXPORT_PART_BYTES = 64 * 1024 * 1024
EXPORT_CLEANUP_BATCH = 16
EXPORT_READ_BYTES = 1024 * 1024
EXPORT_EXCLUSIONS = (
    "Data from other workspaces and records or files no longer stored by ufo.",
    "Archives produced by previous workspace exports.",
    "Externally shared channel conversations, files, and memory.",
    "Connector-side content, synced source pages, and files not shared as artifacts.",
    "Credentials, connector grants, usage ledger, audit records, and workspace configuration.",
    "Other workspace objects, extension records, and member account settings and photos.",
    "Model context windows, system prompts, tool traces, embeddings, search indexes, and previews.",
    "Changes made after the database snapshot began; active turns can be incomplete.",
)

export_request = sa.Table(
    "workspace_export",
    sa.MetaData(),
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column("workspace_id", sa.Uuid(), nullable=False),
    sa.Column("member_id", sa.Uuid(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("active", sa.Integer()),
    sa.Column("blob_key", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("expires_at", sa.DateTime(timezone=True)),
    sa.Column("size_bytes", sa.BigInteger()),
    sa.Column("error", sa.Text()),
    sa.UniqueConstraint("workspace_id", "active"),
)


class ExportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExportStatus(BaseModel):
    id: UUID
    status: Literal["queued", "preparing", "ready", "failed", "expired"]
    created_at: datetime
    finished_at: datetime | None
    expires_at: datetime | None
    size_bytes: int | None
    error: str | None


class ExportView(BaseModel):
    export: ExportStatus | None


@dataclass(frozen=True)
class ExportAccess:
    connection: AsyncConnection
    workspace_id: UUID
    member_id: UUID

    async def read(self, export_id: UUID | None = None) -> sa.Row | None:
        """Read an export only for a currently seated workspace admin."""
        await require_export_admin(self.connection, self.member_id)
        query = sa.select(export_request).where(export_request.c.workspace_id == self.workspace_id)
        if export_id is not None:
            query = query.where(export_request.c.id == export_id)
        return (
            await self.connection.execute(
                query.order_by(
                    export_request.c.created_at.desc(), export_request.c.id.desc()
                ).limit(1)
            )
        ).one_or_none()


@dataclass(frozen=True)
class WorkspaceArchive:
    ctx: ToolContext

    async def run(self) -> ToolResult:
        """Record an authorized, idempotent export request for the background worker."""
        member_id = await WorkspaceExport(self.ctx).authorize()
        ext = self.ctx.ext
        if ext is None:
            raise RuntimeError("workspace export requires the memory extension")
        async with ext.transaction() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            await connection.execute(
                insert(export_request)
                .values(
                    id=self.ctx.turn.id,
                    workspace_id=ext.workspace_id,
                    member_id=member_id,
                    status="queued",
                    active=1,
                    blob_key=export_key(self.ctx.turn.id),
                    created_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing()
            )
        return ToolResult(
            content=(
                TextContent(
                    text=(
                        "Workspace export requested. "
                        "Check Workspace > Admin for its status and download. "
                        "The archive includes all members' private conversations, "
                        "files, and memory."
                    )
                ),
            )
        )


@dataclass(frozen=True)
class ExportWorker:
    ctx: ExtensionContext

    async def run(self) -> None:
        """Build one queued export and remove archives whose retention period ended."""
        storage = self.ctx.exports
        if storage is None:
            raise RuntimeError("workspace export requires blob storage")
        now = datetime.now(UTC)
        async with self.ctx.transaction() as connection:
            expired = (
                await connection.execute(
                    sa.select(export_request)
                    .where(
                        export_request.c.workspace_id == self.ctx.workspace_id,
                        export_request.c.status == "ready",
                        export_request.c.expires_at <= now,
                    )
                    .order_by(export_request.c.expires_at)
                    .limit(EXPORT_CLEANUP_BATCH)
                )
            ).all()
        for expired_row in expired:
            await storage.delete(expired_row.id)
            await self._update(expired_row.id, status="expired")
        async with self.ctx.transaction() as connection:
            row = (
                await connection.execute(
                    sa.select(export_request).where(
                        export_request.c.workspace_id == self.ctx.workspace_id,
                        export_request.c.active == 1,
                    )
                )
            ).one_or_none()
        if row is None:
            return
        await self._update(row.id, status="preparing")
        try:
            size = 0

            async def measured() -> AsyncIterator[bytes]:
                nonlocal size
                async for chunk in archive:
                    size += len(chunk)
                    yield chunk

            async with aclosing(self._archive(row.member_id)) as archive:
                await storage.put_stream(
                    row.id,
                    measured(),
                    part_size_bytes=EXPORT_PART_BYTES,
                )
            async with self.ctx.transaction() as connection:
                await require_export_admin(connection, row.member_id)
            finished = datetime.now(UTC)
            await self._update(
                row.id,
                status="ready",
                active=None,
                size_bytes=size,
                finished_at=finished,
                expires_at=finished + EXPORT_RETENTION,
            )
        except Exception:
            await self._update(
                row.id,
                status="failed",
                active=None,
                finished_at=datetime.now(UTC),
                error="Export failed. Request a new export.",
            )
            await storage.delete(row.id)
            raise

    async def _update(self, export_id: UUID, **values: str | int | datetime | None) -> None:
        async with self.ctx.transaction() as connection:
            await connection.execute(
                sa.update(export_request)
                .where(
                    export_request.c.workspace_id == self.ctx.workspace_id,
                    export_request.c.id == export_id,
                )
                .values(**values)
            )

    async def _archive(self, member_id: UUID) -> AsyncGenerator[bytes]:
        started = datetime.now(UTC)
        count = 0
        storage = self.ctx.exports
        if storage is None:
            raise RuntimeError("workspace export requires blob storage")
        async with AsyncExitStack() as staging:
            metadata = await asyncio.to_thread(tempfile.TemporaryFile, mode="w+b")
            staging.push_async_callback(asyncio.to_thread, metadata.close)
            async with export_reader(member_id, storage) as reader:
                connection = reader.connection
                async for document in reader.documents():
                    async for chunk in self._document(document.path, document.data):
                        await asyncio.to_thread(metadata.write, chunk)
                queries = (
                    (
                        "memory",
                        sa.select(
                            memory_item.c.id,
                            memory_item.c.subject,
                            memory_item.c.body,
                            memory_item.c.item_class,
                            memory_item.c.memory_kind,
                            memory_item.c.confidence,
                            memory_item.c.source_ref,
                            memory_item.c.created_from_page_uid,
                            memory_item.c.created_from_page_revision,
                            memory_item.c.source_uid,
                            memory_item.c.as_of,
                            memory_item.c.superseded_by,
                            memory_item.c.retired_at,
                            memory_item.c.created_at,
                            memory_item.c.updated_at,
                        )
                        .where(
                            memory_item.c.workspace_id == self.ctx.workspace_id,
                            ~memory_item.c.subject.startswith(FOREIGN_AUDIENCE_PREFIX),
                        )
                        .order_by(memory_item.c.id),
                    ),
                    (
                        "profiles",
                        sa.select(
                            memory_profile.c.member_id,
                            memory_profile.c.role,
                            memory_profile.c.focus,
                            memory_profile.c.written_at,
                        )
                        .where(memory_profile.c.workspace_id == self.ctx.workspace_id)
                        .order_by(memory_profile.c.member_id),
                    ),
                )
                for path, query in queries:
                    async for document in export_documents(connection, query, path):
                        async for chunk in self._document(document.path, document.data):
                            await asyncio.to_thread(metadata.write, chunk)
                files = await staging.enter_async_context(reader.artifacts())
            await asyncio.to_thread(metadata.seek, 0)
            while chunk := await asyncio.to_thread(metadata.read, EXPORT_READ_BYTES):
                yield chunk
            async for file in files:
                artifact = file.record
                data = artifact.model_dump_json(exclude={"blob_key"}).encode() + b"\n"
                async for chunk in self._document(f"artifact-records/{count:06d}.jsonl", data):
                    yield chunk
                yield await self._header(artifact.path, artifact.size_bytes)
                async for chunk in file.content:
                    yield chunk
                yield bytes(-artifact.size_bytes % TAR_BLOCK_BYTES)
                count += 1
        manifest = to_json(
            {
                "workspace_id": self.ctx.workspace_id,
                "requested_by": member_id,
                "started_at": started,
                "completed_at": datetime.now(UTC),
                "format": "Numbered JSON lines files and original artifact bytes in a tar archive",
                "scope": "All workspace members; externally shared channels are excluded.",
                "snapshot": (
                    "All database records share one snapshot. "
                    "Original files are verified against their recorded size and digest."
                ),
                "artifacts": count,
                "not_included": EXPORT_EXCLUSIONS,
            },
            indent=2,
        )
        async for chunk in self._document("manifest.json", manifest):
            yield chunk
        yield TAR_END

    async def _document(self, path: str, data: bytes) -> AsyncIterator[bytes]:
        yield await self._header(path, len(data))
        yield data
        yield bytes(-len(data) % TAR_BLOCK_BYTES)

    async def _header(self, path: str, size: int) -> bytes:
        return await asyncio.get_running_loop().run_in_executor(
            ARCHIVE_POOL, archive_header, path, size
        )


def archive_header(path: str, size: int) -> bytes:
    """Build a TAR header off the event loop."""
    header = tarfile.TarInfo(path)
    header.size = size
    header.mode = TAR_FILE_MODE
    return header.tobuf(format=tarfile.PAX_FORMAT)


def export_candidates() -> sa.Select[tuple[UUID]]:
    """Workspaces with pending exports or expired download objects."""
    return (
        sa.select(export_request.c.workspace_id)
        .where(
            sa.or_(
                export_request.c.active == 1,
                sa.and_(
                    export_request.c.status == "ready",
                    export_request.c.expires_at <= datetime.now(UTC),
                ),
            )
        )
        .distinct()
    )


async def run_exports(ctx: ExtensionContext) -> None:
    await ExportWorker(ctx).run()


async def export_handler(ctx: ToolContext, args: ExportInput) -> ToolResult:
    return await WorkspaceArchive(ctx).run()


EXPORT_JOB = JobSpec(
    name="workspace_export",
    schedule="0 * * * * *",
    handler=run_exports,
    candidates=owner_candidates(export_candidates),
)


EXPORT_ACTION = ToolDef(
    name="export",
    description="",
    input_model=ExportInput,
    handler=export_handler,
    bound=ObjectBinding(kind="workspace", binding="collection"),
    side_effecting=True,
    presentation=ActionPresentation(
        label="Export workspace",
        confirm=(
            "This archive includes all members' private conversations, files, and memory, "
            "including private-agent and room conversations. "
            "Anyone with the archive can read this data. Store and share it securely."
        ),
    ),
)
