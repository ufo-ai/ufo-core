import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from ufo.blob import S3BlobStore
from ufo.db import workspace_tx
from ufo.runtime.sources.sync import FOLDER_BACKEND
from ufo.schema import tables

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "infra" / "blob_relayout.py"
pytestmark = pytest.mark.parametrize("database_url", ["postgres"], indirect=True)


def _relayout_module():
    spec = importlib.util.spec_from_file_location("blob_relayout", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def _seed_rows(artifact_key: str, body_ref: str):
    workspace_id, agent_id = uuid4(), uuid4()
    conversation_id, turn_id, source_id, page_id = uuid4(), uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="be brief",
                model="claude-opus-4-8",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key=str(uuid4()),
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="share",
                terminal={"status": "done", "text": "shared"},
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn_id,
                blob_key=artifact_key,
                workspace_id=workspace_id,
                filename="report.txt",
                subject=None,
                media_type="text/plain",
                size_bytes=4,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend=FOLDER_BACKEND,
                config={"root": "/seed"},
                cursor=None,
                next_sync_at=sa.func.now(),
                claimed_by=None,
                claim_expires_at=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                id=page_id,
                workspace_id=workspace_id,
                source_id=source_id,
                digest="sha256:seed",
                body_ref=body_ref,
                stream="files",
                title="seed",
                subject="shared",
                tombstone=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, conversation_id, turn_id


async def test_relayout_copies_verifies_and_deletes_the_old_layout(
    db: None, database_url: str, s3_store: S3BlobStore
) -> None:
    """The whole operator sequence against real Postgres and real object storage: `copy` moves
    every row-mapped object under its workspace and leaves orphans in place, a rerun copies
    nothing, `verify` proves the row-referenced keys, and `delete` clears the old prefixes —
    orphans included — leaving only the workspace layout."""
    module = _relayout_module()
    artifact_key = f"artifacts/{uuid4()}/report.txt"
    body_ref = f"sources/{uuid4()}/{uuid4()}/seed"
    workspace_id, conversation_id, turn_id = await _seed_rows(artifact_key, body_ref)
    transcript_key = f"conversations/{conversation_id}/messages.json.lz4"
    image_key = f"tool-images/{turn_id}/c1/0"
    orphan_key = f"conversations/{uuid4()}/messages.json.lz4"
    for key, body in (
        (transcript_key, b"talk"),
        (image_key, b"img"),
        (artifact_key, b"file"),
        (body_ref, b"body"),
        (orphan_key, b"orphan"),
    ):
        await s3_store.put(key, body)

    relayout = module.Relayout(
        client=await s3_store._client(), bucket=s3_store.bucket, database_url=database_url
    )
    assert await relayout.copy() == 0
    root = f"workspaces/{workspace_id}"
    for key, body in (
        (transcript_key, b"talk"),
        (image_key, b"img"),
        (artifact_key, b"file"),
        (body_ref, b"body"),
    ):
        assert await s3_store.get(f"{root}/{key}") == body
    moved = await s3_store.list("workspaces/")
    assert len(moved) == 4
    assert await relayout.copy() == 0
    assert await relayout.verify() == 0

    await s3_store.delete(f"{root}/{transcript_key}")
    assert await relayout.verify() == 1
    assert await relayout.delete() == 1
    assert await s3_store.exists(transcript_key)
    assert await relayout.copy() == 0
    assert await relayout.verify() == 0

    assert await relayout.delete() == 0
    for prefix in ("conversations/", "tool-images/", "artifacts/", "sources/"):
        assert await s3_store.list(prefix) == ()
    assert not await s3_store.exists(orphan_key)
    assert [entry.key for entry in await s3_store.list("workspaces/")] == sorted(
        f"{root}/{key}" for key in (transcript_key, image_key, artifact_key, body_ref)
    )


async def test_relayout_refuses_a_database_with_no_rows(
    db: None, database_url: str, s3_store: S3BlobStore
) -> None:
    """An empty mapping is the signature of the wrong DSN (an RLS-scoped role reads zero rows), and
    a copy that maps nothing would let a later delete treat the whole bucket as orphans — so the
    tool refuses to run against a database that names no owners."""
    module = _relayout_module()
    relayout = module.Relayout(
        client=await s3_store._client(), bucket=s3_store.bucket, database_url=database_url
    )
    with pytest.raises(RuntimeError, match="no rows"):
        await relayout.copy()
    with pytest.raises(RuntimeError, match="no rows"):
        await relayout.delete()
