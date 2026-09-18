import hashlib
import io
import json
import tarfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_memory.condenser import memory_profile
from ufo_ext_memory.manifest import manifest
from ufo_ext_memory.store import memory_item
from ufo_ext_memory.workspace_export import (
    EXPORT_ACTION,
    ExportAccess,
    ExportInput,
    ExportWorker,
    WorkspaceArchive,
    export_request,
)

from ufo.blob import BlobNotFound, FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.runtime.ext.context import context_for
from ufo.runtime.tools.context import SpeakerRequired, ToolContext
from ufo.runtime.turns.audience import (
    SHARED_AUDIENCE,
    conversation_audience,
    foreign_room_audience,
    room_audience,
)
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.export import export_reader

pytestmark = pytest.mark.usefixtures("db")
NOW = datetime(2026, 9, 17, tzinfo=UTC)
ARTIFACT_BYTES = b"original bytes\x00\xff" * 30_000


@dataclass(frozen=True)
class ExportFixture:
    ctx: ToolContext
    other_member: UUID
    shared_conversation: UUID
    private_conversation: UUID
    room_conversation: UUID
    artifact_key: str


async def _seed(tmp_path: Path) -> ExportFixture:
    workspace_id, member_id, other, agent_id = (uuid4() for _ in range(4))
    own, shared, private, room, foreign = (uuid4() for _ in range(5))
    current_turn = uuid4()
    blob = WorkspaceBlobStore(FilesystemBlobStore(tmp_path))
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.workspace).values(
                    id=workspace_id,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            for mid, admin in ((member_id, True), (other, False)):
                await connection.execute(
                    sa.insert(tables.member).values(
                        id=mid,
                        workspace_id=workspace_id,
                        email=f"{mid}@example.com",
                        is_admin=admin,
                        seated_at=NOW,
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=agent_id,
                    workspace_id=workspace_id,
                    name="main",
                    prompt="test",
                    model="test",
                    is_main=True,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            await connection.execute(
                sa.insert(memory_profile).values(
                    workspace_id=workspace_id,
                    member_id=member_id,
                    role="Support lead",
                    focus="Launch support",
                    written_at=NOW,
                )
            )
            await connection.execute(
                sa.insert(memory_item).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    subject="shared",
                    body="Retired shared fact",
                    item_class="fact",
                    memory_kind="fact",
                    confidence=5,
                    retired_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            for cid, audience, text in (
                (own, conversation_audience(member_id), "admin private"),
                (shared, SHARED_AUDIENCE, "shared conversation"),
                (private, conversation_audience(other), "OTHER PRIVATE SECRET"),
                (room, room_audience("slack", "room"), "ROOM SECRET"),
                (foreign, foreign_room_audience("slack", "external"), "EXTERNAL CHANNEL SECRET"),
            ):
                turn_id = current_turn if cid == own else uuid4()
                await connection.execute(
                    sa.insert(tables.conversation).values(
                        id=cid,
                        workspace_id=workspace_id,
                        agent_id=agent_id,
                        surface="web",
                        queue_key=str(cid),
                        audience=audience,
                        member_id=member_id if cid == own else other if cid == private else None,
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
                await connection.execute(
                    sa.insert(tables.turn).values(
                        id=turn_id,
                        workspace_id=workspace_id,
                        conversation_id=cid,
                        agent_id=agent_id,
                        seq=1,
                        status="running" if cid == own else "done",
                        inbound=text,
                        terminal=None
                        if cid == own
                        else {
                            "status": "done",
                            "text": "reply to " + text,
                            "credential_request": {"secret": "AUTHORIZATION SECRET"},
                        },
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
                await connection.execute(
                    sa.insert(tables.inbound_message).values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        conversation_id=cid,
                        seq=1,
                        body=text,
                        admission_source="member",
                        admitted_turn_id=turn_id,
                        consumed_turn_id=turn_id,
                        created_at=NOW,
                    )
                )
                await connection.execute(
                    sa.insert(tables.mid_turn_reply).values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        turn_id=turn_id,
                        round_index=1,
                        span_index=1,
                        text=text,
                        status="delivered",
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
                await connection.execute(
                    sa.insert(memory_item).values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        subject=audience,
                        body=text,
                        item_class="fact",
                        memory_kind="fact",
                        confidence=5,
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
                key = f"artifacts/{cid}/notes.bin"
                data = ARTIFACT_BYTES if cid == shared else text.encode()
                await blob.put(key, data)
                await connection.execute(
                    sa.insert(tables.shared_artifact).values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        turn_id=turn_id,
                        blob_key=key,
                        filename="notes.bin",
                        subject="Original file caption",
                        media_type="application/octet-stream",
                        size_bytes=len(data),
                        digest="sha256:" + hashlib.sha256(data).hexdigest(),
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
        ctx = ToolContext(
            sandbox=None,
            blob=blob,
            turn=Turn(
                id=current_turn,
                workspace_id=workspace_id,
                conversation_id=own,
                agent_id=agent_id,
                seq=1,
                status="running",
                inbound="export",
                created_at=NOW,
            ),
            agent=Agent(prompt="test", model="test"),
            spawn=None,
            speaker_member_id=member_id,
            audience=conversation_audience(member_id),
            artifact_token_secret="test-secret",
            ext=context_for(
                "memory", frozenset(), blob=blob, audience=conversation_audience(member_id)
            ),
            idempotency_key="export-test",
        )
    return ExportFixture(ctx, other, shared, private, room, f"artifacts/{shared}/notes.bin")


async def _run_export(ctx: ToolContext):
    result = await WorkspaceArchive(ctx).run()
    await ExportWorker(ctx.ext).run()
    return result


async def _export_row() -> sa.Row | None:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(export_request).order_by(export_request.c.created_at.desc()).limit(1)
            )
        ).one_or_none()


async def test_admin_archive_contains_portable_records_and_original_bytes(tmp_path: Path) -> None:
    fixture = await _seed(tmp_path)
    ctx = fixture.ctx
    with ws(ctx.turn.workspace_id):
        result = await EXPORT_ACTION.handler(ctx, ExportInput())
        assert (await _export_row()).status == "queued"
        assert not await ctx.blob.exists((await _export_row()).blob_key)
        await ExportWorker(ctx.ext).run()
        assert not result.is_error
        row = await _export_row()
        assert row is not None
        data = await ctx.blob.get(row.blob_key)
        assert row.size_bytes == len(data)
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            files = {entry.name: archive.extractfile(entry).read() for entry in archive}
        assert b"AUTHORIZATION SECRET" not in data
        assert b"OTHER PRIVATE SECRET" in data
        assert b"ROOM SECRET" in data
        conversations = [
            json.loads(line) for line in files["conversations/000000.jsonl"].splitlines()
        ]
        assert {row["id"] for row in conversations} == {
            str(ctx.turn.conversation_id),
            str(fixture.shared_conversation),
            str(fixture.private_conversation),
            str(fixture.room_conversation),
        }
        assert b"Support lead" in files["profiles/000000.jsonl"]
        assert b"Retired shared fact" in files["memory/000000.jsonl"]
        assert b"admin private" in files["memory/000000.jsonl"]
        assert b"shared conversation" in files["memory/000000.jsonl"]
        assert b"OTHER PRIVATE SECRET" in files["memory/000000.jsonl"]
        assert b"ROOM SECRET" in files["memory/000000.jsonl"]
        assert b"reply to shared conversation" in files["turns/000000.jsonl"]
        assert b"shared conversation" in files["messages/000000.jsonl"]
        assert b"shared conversation" in files["replies/000000.jsonl"]
        artifacts = [
            json.loads(line)
            for line in b"".join(
                value for key, value in files.items() if key.startswith("artifact-records/")
            ).splitlines()
        ]
        shared = next(
            row for row in artifacts if row["conversation_id"] == str(fixture.shared_conversation)
        )
        assert files[shared["path"]] == ARTIFACT_BYTES
        assert shared["subject"] == "Original file caption"
        assert shared["created_at"].startswith("2026-09-17")
        metadata = json.loads(files["manifest.json"])
        assert metadata["not_included"]
        assert metadata["requested_by"] == str(ctx.speaker_member_id)


@pytest.mark.parametrize("refusal", ["member", "unbound", "shared", "room", "child", "delivery"])
async def test_export_refuses_unauthorized_or_public_delivery(tmp_path: Path, refusal: str) -> None:
    fixture = await _seed(tmp_path)
    ctx = fixture.ctx
    match refusal:
        case "member":
            ctx = replace(
                ctx,
                speaker_member_id=fixture.other_member,
                audience=conversation_audience(fixture.other_member),
            )
        case "unbound":
            ctx = replace(ctx, speaker_member_id=None)
        case "shared":
            ctx = replace(ctx, audience=SHARED_AUDIENCE)
        case "room":
            ctx = replace(ctx, audience=room_audience("slack", "room"))
        case "delivery":
            ctx = replace(
                ctx,
                turn=ctx.turn.model_copy(update={"conversation_id": fixture.shared_conversation}),
            )
        case "child":
            with ws(ctx.turn.workspace_id):
                async with workspace_tx() as connection:
                    await connection.execute(
                        sa.update(tables.agent)
                        .where(
                            tables.agent.c.id == ctx.turn.agent_id,
                        )
                        .values(is_main=False)
                    )
    with ws(ctx.turn.workspace_id):
        with pytest.raises((PermissionError, SpeakerRequired)):
            await _run_export(ctx)
        assert await _export_row() is None


@pytest.mark.parametrize("failure", ["missing", "short", "digest"])
async def test_export_refuses_incomplete_artifact_without_publishing(
    tmp_path: Path, failure: str
) -> None:
    fixture = await _seed(tmp_path)
    ctx = fixture.ctx
    with ws(ctx.turn.workspace_id):
        match failure:
            case "missing":
                await ctx.blob.delete(fixture.artifact_key)
            case "short":
                await ctx.blob.put(fixture.artifact_key, b"short")
            case "digest":
                await ctx.blob.put(fixture.artifact_key, bytes(len(ARTIFACT_BYTES)))
        with pytest.raises((BlobNotFound, ValueError)):
            await _run_export(ctx)
        assert (await _export_row()).status == "failed"
        assert not any(
            entry.key.endswith("workspace-export.tar") for entry in await ctx.blob.list("exports/")
        )


def test_export_is_a_member_bound_workspace_action() -> None:
    action = next(tool for tool in manifest().tools if tool.name == "export")
    assert action.bound.kind == "workspace"
    assert action.binds_member_authority
    assert action.side_effecting


async def test_export_stays_inside_its_workspace(tmp_path: Path) -> None:
    fixture = await _seed(tmp_path / "first")
    other = await _seed(tmp_path / "second")
    with ws(other.ctx.turn.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(memory_item)
                .where(
                    memory_item.c.workspace_id == other.ctx.turn.workspace_id,
                )
                .values(body="OTHER WORKSPACE SECRET")
            )
    with ws(fixture.ctx.turn.workspace_id):
        await _run_export(fixture.ctx)
        row = await _export_row()
        data = await fixture.ctx.blob.get(row.blob_key)
        assert b"OTHER WORKSPACE SECRET" not in data
        assert str(other.shared_conversation).encode() not in data


async def test_streamed_export_replay_keeps_the_published_archive(tmp_path: Path) -> None:
    fixture = await _seed(tmp_path)
    with ws(fixture.ctx.turn.workspace_id):
        await _run_export(fixture.ctx)
        row = await _export_row()
        before = await fixture.ctx.blob.get(row.blob_key)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.shared_artifact)
                .where(
                    tables.shared_artifact.c.blob_key == fixture.artifact_key,
                )
                .values(size_bytes=1024 * 1024 * 1024)
            )
        await _run_export(fixture.ctx)
        assert await fixture.ctx.blob.get(row.blob_key) == before
        assert (await _export_row()).size_bytes == len(before)


async def test_export_includes_source_memory_for_every_member(tmp_path: Path) -> None:
    fixture = await _seed(tmp_path)
    with ws(fixture.ctx.turn.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(memory_item).values(
                    id=uuid4(),
                    workspace_id=fixture.ctx.turn.workspace_id,
                    subject=conversation_audience(fixture.other_member),
                    body="INACCESSIBLE SOURCE SECRET",
                    item_class="fact",
                    memory_kind="fact",
                    confidence=5,
                    created_from_page_uid=uuid4(),
                    created_from_page_revision=1,
                    source_uid=uuid4(),
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        await _run_export(fixture.ctx)
        row = await _export_row()
        data = await fixture.ctx.blob.get(row.blob_key)
        assert b"INACCESSIBLE SOURCE SECRET" in data
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            metadata = json.load(archive.extractfile("manifest.json"))
        assert (
            metadata["scope"] == "All workspace members; externally shared channels are excluded."
        )


@pytest.mark.parametrize("actual", [b"short", b"too many bytes"])
async def test_stream_delivery_refuses_a_false_size(tmp_path: Path, actual: bytes) -> None:
    fixture = await _seed(tmp_path)

    async def chunks():
        yield actual

    with ws(fixture.ctx.turn.workspace_id):
        with pytest.raises(ValueError, match="declared size"):
            await fixture.ctx.share_artifact_stream("workspace-export.tar", chunks(), 8, "Export")
        assert await _export_row() is None
        assert not any(
            entry.key.endswith("workspace-export.tar")
            for entry in await fixture.ctx.blob.list("artifacts/")
        )


async def test_export_pages_all_record_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = await _seed(tmp_path)
    monkeypatch.setattr("ufo.runtime.workspace_export.EXPORT_BATCH_ROWS", 1)
    with ws(fixture.ctx.turn.workspace_id):
        await _run_export(fixture.ctx)
        data = await fixture.ctx.blob.get((await _export_row()).blob_key)
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            names = archive.getnames()
            assert len([name for name in names if name.startswith("conversations/")]) == 4
            assert len([name for name in names if name.startswith("memory/")]) == 5
            assert b"OTHER PRIVATE SECRET" in data


@pytest.mark.parametrize("filename", ["../../outside.bin", "..\\..\\outside.bin"])
async def test_export_keeps_artifact_paths_inside_the_archive(
    tmp_path: Path, filename: str
) -> None:
    fixture = await _seed(tmp_path)
    with ws(fixture.ctx.turn.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.shared_artifact)
                .where(
                    tables.shared_artifact.c.blob_key == fixture.artifact_key,
                )
                .values(filename=filename)
            )
        await _run_export(fixture.ctx)
        row = await _export_row()
        data = await fixture.ctx.blob.get(row.blob_key)
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            entries = [item for item in archive if item.name.endswith("outside.bin")]
            assert len(entries) == 1
            assert entries[0].name.startswith("artifacts/")
            assert ".." not in entries[0].name
            assert "\\" not in entries[0].name
            assert archive.extractfile(entries[0]).read() == ARTIFACT_BYTES


async def test_export_includes_another_members_private_agent(tmp_path: Path) -> None:
    fixture = await _seed(tmp_path)
    private_agent = uuid4()
    with ws(fixture.ctx.turn.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=private_agent,
                    workspace_id=fixture.ctx.turn.workspace_id,
                    name="private",
                    prompt="test",
                    model="test",
                    visibility="private",
                    owner_member_id=fixture.other_member,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            await connection.execute(
                sa.update(tables.conversation)
                .where(
                    tables.conversation.c.id == fixture.shared_conversation,
                )
                .values(agent_id=private_agent)
            )
            await connection.execute(
                sa.update(tables.turn)
                .where(
                    tables.turn.c.conversation_id == fixture.shared_conversation,
                )
                .values(agent_id=private_agent)
            )
        await _run_export(fixture.ctx)
        row = await _export_row()
        data = await fixture.ctx.blob.get(row.blob_key)
        assert str(fixture.shared_conversation).encode() in data
        assert ARTIFACT_BYTES in data


async def test_repeated_exports_exclude_archives_but_keep_same_named_files(tmp_path: Path) -> None:
    fixture = await _seed(tmp_path)
    ctx = fixture.ctx
    with ws(ctx.turn.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.shared_artifact)
                .where(tables.shared_artifact.c.blob_key == fixture.artifact_key)
                .values(filename="workspace-export.tar")
            )
        for seq in range(2, 5):
            turn = ctx.turn.model_copy(update={"id": uuid4(), "seq": seq})
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.turn).values(
                        id=turn.id,
                        workspace_id=turn.workspace_id,
                        conversation_id=turn.conversation_id,
                        agent_id=turn.agent_id,
                        seq=turn.seq,
                        status=turn.status,
                        inbound=turn.inbound,
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
            await _run_export(replace(ctx, turn=turn, idempotency_key=str(turn.id)))
            async with workspace_tx() as connection:
                row = (
                    await connection.execute(
                        sa.select(export_request).where(export_request.c.id == turn.id)
                    )
                ).one()
            data = await ctx.blob.get(row.blob_key)
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                artifacts = [
                    json.loads(line)
                    for item in archive
                    if item.name.startswith("artifact-records/")
                    for line in archive.extractfile(item)
                ]
                assert len(artifacts) == 4
                original = next(
                    item for item in artifacts if item["filename"] == "workspace-export.tar"
                )
                assert archive.extractfile(original["path"]).read() == ARTIFACT_BYTES
                metadata = json.load(archive.extractfile("manifest.json"))
                assert (
                    "Archives produced by previous workspace exports." in metadata["not_included"]
                )
                assert len([item for item in archive if item.name.startswith("artifacts/")]) == 4


async def test_export_includes_retained_deleted_threads(tmp_path: Path) -> None:
    fixture = await _seed(tmp_path)
    with ws(fixture.ctx.turn.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.conversation)
                .where(tables.conversation.c.id == fixture.private_conversation)
                .values(deleted_at=NOW, archived_at=NOW)
            )
        await _run_export(fixture.ctx)
        row = await _export_row()
        data = await fixture.ctx.blob.get(row.blob_key)
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            conversations = [
                json.loads(line) for line in archive.extractfile("conversations/000000.jsonl")
            ]
            deleted = next(
                item for item in conversations if item["id"] == str(fixture.private_conversation)
            )
            assert deleted["deleted_at"].startswith("2026-09-17")
            assert deleted["archived_at"].startswith("2026-09-17")
            assert b"OTHER PRIVATE SECRET" in archive.extractfile("turns/000000.jsonl").read()


async def test_export_excludes_external_channel_transcripts_files_and_memory(
    tmp_path: Path,
) -> None:
    fixture = await _seed(tmp_path)
    with ws(fixture.ctx.turn.workspace_id):
        await _run_export(fixture.ctx)
        row = await _export_row()
        data = await fixture.ctx.blob.get(row.blob_key)
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            for entry in archive:
                content = archive.extractfile(entry).read()
                assert b"EXTERNAL CHANNEL SECRET" not in content
                assert b"foreign:slack:external" not in content
            manifest = json.load(archive.extractfile("manifest.json"))
            assert manifest["artifacts"] == 4
            assert (
                "Externally shared channel conversations, files, and memory."
                in manifest["not_included"]
            )


async def test_export_resumes_preparing_after_restart_and_expires(tmp_path: Path) -> None:
    fixture = await _seed(tmp_path)
    ctx = fixture.ctx
    with ws(ctx.turn.workspace_id):
        await WorkspaceArchive(ctx).run()
        await WorkspaceArchive(ctx).run()
        async with workspace_tx() as connection:
            assert (
                await connection.scalar(sa.select(sa.func.count()).select_from(export_request)) == 1
            )
            await connection.execute(sa.update(export_request).values(status="preparing"))
        await ExportWorker(ctx.ext).run()
        row = await _export_row()
        assert row.status == "ready"
        assert await ctx.blob.exists(row.blob_key)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(export_request).values(
                    expires_at=datetime.now(UTC) - timedelta(seconds=1)
                )
            )
        await ExportWorker(ctx.ext).run()
        assert (await _export_row()).status == "expired"
        assert not await ctx.blob.exists(row.blob_key)


async def test_export_rechecks_admin_at_execution_and_read(tmp_path: Path) -> None:
    fixture = await _seed(tmp_path)
    ctx = fixture.ctx
    with ws(ctx.turn.workspace_id):
        await WorkspaceArchive(ctx).run()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.member)
                .where(tables.member.c.id == ctx.speaker_member_id)
                .values(is_admin=False)
            )
        with pytest.raises(PermissionError):
            await ExportWorker(ctx.ext).run()
        row = await _export_row()
        assert row.status == "failed"
        assert not await ctx.blob.exists(row.blob_key)
        async with workspace_tx() as connection:
            with pytest.raises(PermissionError):
                await ExportAccess(connection, ctx.turn.workspace_id, ctx.speaker_member_id).read(
                    row.id
                )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_export_streams_more_than_one_gib(tmp_path: Path) -> None:
    fixture = await _seed(tmp_path)
    ctx = fixture.ctx
    chunk = bytes(1024 * 1024)
    count = 1025
    checksum = hashlib.sha256()

    async def content():
        for _ in range(count):
            checksum.update(chunk)
            yield chunk

    with ws(ctx.turn.workspace_id):
        await ctx.blob.put_stream(fixture.artifact_key, content())
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.shared_artifact)
                .where(tables.shared_artifact.c.blob_key == fixture.artifact_key)
                .values(size_bytes=len(chunk) * count, digest=checksum.hexdigest())
            )
        await _run_export(ctx)
        row = await _export_row()
        assert row.status == "ready"
        assert row.size_bytes > 1024**3
        size = 0
        async for part in ctx.blob.get_stream(row.blob_key):
            size += len(part)
            assert len(part) <= 1024 * 1024
        assert size == row.size_bytes


async def test_export_snapshot_keeps_records_consistent_without_blocking_writes(
    tmp_path: Path,
) -> None:
    fixture = await _seed(tmp_path)
    ctx = fixture.ctx
    with ws(ctx.turn.workspace_id):
        stream = ExportWorker(ctx.ext)._archive(ctx.speaker_member_id)
        first = await anext(stream)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(memory_item)
                .where(memory_item.c.workspace_id == ctx.turn.workspace_id)
                .values(body="AFTER SNAPSHOT")
            )
        collected = bytearray(first)
        async for chunk in stream:
            collected.extend(chunk)
        assert b"AFTER SNAPSHOT" not in collected
        assert b"Retired shared fact" in collected


async def test_export_snapshot_refuses_writes_and_releases_read_only_state(tmp_path: Path) -> None:
    fixture = await _seed(tmp_path)
    ctx = fixture.ctx
    with ws(ctx.turn.workspace_id):
        with pytest.raises(sa.exc.DBAPIError):
            async with export_reader(ctx.speaker_member_id, ctx.ext.exports) as reader:
                await reader.connection.execute(
                    sa.update(memory_item)
                    .where(memory_item.c.workspace_id == ctx.turn.workspace_id)
                    .values(body="forbidden")
                )
        async with ctx.ext.transaction() as connection:
            await connection.execute(
                sa.update(memory_item)
                .where(memory_item.c.workspace_id == ctx.turn.workspace_id)
                .values(body="allowed")
            )
            assert await connection.scalar(sa.select(memory_item.c.body).limit(1)) == "allowed"


async def test_export_file_stream_is_bound_to_its_authorized_record(tmp_path: Path) -> None:
    fixture = await _seed(tmp_path)
    ctx = fixture.ctx
    with ws(ctx.turn.workspace_id):
        async with export_reader(ctx.speaker_member_id, ctx.ext.exports) as reader:
            async with reader.artifacts() as files:
                file = await anext(files)
                expected = await ctx.blob.get(file.record.blob_key)
                file.record.blob_key = "artifacts/not-authorized.bin"
                content = bytearray()
                async for chunk in file.content:
                    content.extend(chunk)
                assert bytes(content) == expected


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_export_closes_snapshot_before_archive_transfer(tmp_path: Path) -> None:
    fixture = await _seed(tmp_path)
    ctx = fixture.ctx
    with ws(ctx.turn.workspace_id):
        stream = ExportWorker(ctx.ext)._archive(ctx.speaker_member_id)
        try:
            await anext(stream)
            async with workspace_tx() as connection:
                idle_snapshots = await connection.scalar(
                    sa.text(
                        "select count(*) from pg_stat_activity "
                        "where datname = current_database() "
                        "and pid <> pg_backend_pid() "
                        "and state = 'idle in transaction'"
                    )
                )
            assert idle_snapshots == 0
            async for _ in stream:
                pass
        finally:
            await stream.aclose()
