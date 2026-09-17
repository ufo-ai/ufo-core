"""Portable workspace archives delivered to the requesting admin's private conversation."""

import asyncio
import hashlib
import tarfile
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from pydantic_core import to_json

from ufo.sdk.export import (
    EXPORT_DOCUMENT_LIMIT,
    EXPORT_ROW_LIMIT,
    ExportDocument,
    ExportRecords,
    WorkspaceExport,
    export_document,
)
from ufo.sdk.tools import ObjectBinding, TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_memory.condenser import memory_profile
from ufo_ext_memory.store import memory_item

ARCHIVE_WORKERS = 1
ARCHIVE_POOL = ThreadPoolExecutor(max_workers=ARCHIVE_WORKERS, thread_name_prefix="export-archive")
TAR_FILE_MODE = 0o600
TAR_BLOCK_BYTES = 512
TAR_END = bytes(TAR_BLOCK_BYTES * 2)
SHA256_DIGEST_PREFIX = "sha256:"
EXPORT_FILENAME = "workspace-export.tar"
EXPORT_EXCLUSIONS = (
    "Other members' private conversations, private agents, artifacts, and memory.",
    "Room and external-channel data: room membership cannot be verified.",
    "Deleted conversations and files no longer stored by ufo.",
    "Archives produced by previous workspace exports.",
    "Source-derived memory whose source access or revision cannot be verified.",
    "Connector-side content, synced source pages, and files not shared as artifacts.",
    "Credentials, connector grants, usage ledger, audit records, and workspace configuration.",
    "Other workspace objects, extension records, and member account settings and photos.",
    "Model context windows, system prompts, tool traces, embeddings, search indexes, and previews.",
    "Changes made after each record was read; active turns can be incomplete.",
)


class ExportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class ArchiveEntry:
    header: bytes
    size: int
    content: AsyncIterator[bytes]

    @property
    def padding(self) -> int:
        return -self.size % TAR_BLOCK_BYTES


async def document_bytes(data: bytes) -> AsyncIterator[bytes]:
    yield data


@dataclass(frozen=True)
class WorkspaceArchive:
    ctx: ToolContext

    async def run(self) -> ToolResult:
        """Export every reachable record or refuse without publishing an archive."""
        records = await WorkspaceExport(self.ctx).read()
        memory, profiles, excluded_memory = await self._memory()
        entries = await asyncio.get_running_loop().run_in_executor(
            ARCHIVE_POOL, self._entries, records, memory, profiles, excluded_memory
        )
        size = sum(len(entry.header) + entry.size + entry.padding for entry in entries) + len(
            TAR_END
        )
        await self.ctx.share_artifact_stream(
            EXPORT_FILENAME,
            self._archive(entries),
            size,
            "Workspace conversations, artifacts, and memory. Exclusions are in manifest.json.",
            is_workspace_export=True,
        )
        return ToolResult(
            content=(
                TextContent(
                    text=(
                        "Workspace export ready: workspace-export.tar. "
                        "It contains shared data and your private data. "
                        "manifest.json lists excluded data."
                    )
                ),
            )
        )

    async def _memory(self) -> tuple[ExportDocument, ExportDocument, int]:
        ext = self.ctx.ext
        if ext is None:
            raise RuntimeError("workspace export requires the memory extension")
        async with ext.transaction() as connection:
            rows = (
                await connection.execute(
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
                        memory_item.c.workspace_id == ext.workspace_id,
                        memory_item.c.subject.in_(self.ctx.read_subjects),
                    )
                    .order_by(memory_item.c.id)
                    .limit(EXPORT_ROW_LIMIT + 1)
                )
            ).all()
            profiles = (
                await connection.execute(
                    sa.select(
                        memory_profile.c.member_id,
                        memory_profile.c.role,
                        memory_profile.c.focus,
                        memory_profile.c.written_at,
                    )
                    .where(memory_profile.c.workspace_id == ext.workspace_id)
                    .order_by(memory_profile.c.member_id)
                    .limit(EXPORT_ROW_LIMIT + 1)
                )
            ).all()
        if len(rows) > EXPORT_ROW_LIMIT:
            raise ValueError("export refused: too many memory records")
        page_ids = tuple({row.created_from_page_uid for row in rows if row.created_from_page_uid})
        pages = await ext.readable_page_states(page_ids, self.ctx.source_reader())
        readable = [
            row
            for row in rows
            if row.created_from_page_uid is None
            or (
                (page := pages.get(row.created_from_page_uid)) is not None
                and page.subject == row.subject
                and page.revision == row.created_from_page_revision
            )
        ]
        return (
            await export_document("memory.jsonl", readable),
            await export_document("profiles.jsonl", profiles),
            len(rows) - len(readable),
        )

    def _entries(
        self,
        records: ExportRecords,
        memory: ExportDocument,
        profiles: ExportDocument,
        excluded_memory: int,
    ) -> tuple[ArchiveEntry, ...]:
        artifact_data = bytearray()
        for item in records.artifacts:
            artifact_data.extend(item.model_dump_json(exclude={"blob_key"}).encode())
            artifact_data.extend(b"\n")
            if len(artifact_data) > EXPORT_DOCUMENT_LIMIT:
                raise ValueError("export refused: artifact manifest exceeds its size limit")
        artifact_manifest = ExportDocument("artifacts.jsonl", bytes(artifact_data))
        manifest = ExportDocument(
            "manifest.json",
            to_json(
                {
                    "workspace_id": self.ctx.turn.workspace_id,
                    "requested_by": self.ctx.speaker_member_id,
                    "started_at": records.started_at,
                    "records_read_at": datetime.now(UTC),
                    "format": "JSON lines and original artifact bytes in a tar archive",
                    "subjects": sorted(self.ctx.read_subjects),
                    "transcripts": {
                        "conversations.jsonl": "Conversation metadata.",
                        "turns.jsonl": "Turn inputs, state, and terminal replies.",
                        "messages.jsonl": "Admitted messages, including arrivals during a turn.",
                        "replies.jsonl": "Replies emitted before a turn ended.",
                    },
                    "memory": (
                        "Visible memory, including superseded and retired records, "
                        "and shared member profiles."
                    ),
                    "artifacts": len(records.artifacts),
                    "excluded_source_memory_records": excluded_memory,
                    "not_included": EXPORT_EXCLUSIONS,
                },
                indent=2,
            ),
        )
        entries = []
        for document in (*records.documents, memory, profiles, artifact_manifest, manifest):
            entries.append(
                self._entry(document.path, len(document.data), document_bytes(document.data))
            )
        for artifact in records.artifacts:
            entries.append(
                self._entry(
                    artifact.path,
                    artifact.size_bytes,
                    self._artifact_bytes(artifact.blob_key, artifact.size_bytes, artifact.digest),
                )
            )
        return tuple(entries)

    def _entry(self, path: str, size: int, content: AsyncIterator[bytes]) -> ArchiveEntry:
        header = tarfile.TarInfo(path)
        header.size = size
        header.mode = TAR_FILE_MODE
        return ArchiveEntry(header.tobuf(format=tarfile.PAX_FORMAT), size, content)

    async def _artifact_bytes(
        self, key: str, expected: int, digest: str | None
    ) -> AsyncIterator[bytes]:
        size = 0
        checksum = hashlib.sha256()
        async for chunk in self.ctx.blob.get_stream(key):
            size += len(chunk)
            if size > expected:
                raise ValueError("export refused: artifact size changed")
            checksum.update(chunk)
            yield chunk
        if size != expected or (
            digest is not None and checksum.hexdigest() != digest.removeprefix(SHA256_DIGEST_PREFIX)
        ):
            raise ValueError("export refused: artifact content does not match its record")

    async def _archive(self, entries: tuple[ArchiveEntry, ...]) -> AsyncIterator[bytes]:
        for entry in entries:
            yield entry.header
            async for chunk in entry.content:
                yield chunk
            yield bytes(entry.padding)
        yield TAR_END


async def export_handler(ctx: ToolContext, args: ExportInput) -> ToolResult:
    return await WorkspaceArchive(ctx).run()


EXPORT_ACTION = ToolDef(
    name="export",
    description="",
    input_model=ExportInput,
    handler=export_handler,
    bound=ObjectBinding(kind="workspace", binding="collection"),
    side_effecting=True,
)
