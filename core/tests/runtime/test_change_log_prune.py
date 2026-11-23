from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa

from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.runtime.ext.manifest import JobSpec
from ufo.runtime.jobs import (
    CHANGE_LOG_PRUNE_JOB,
    CHANGE_LOG_RETENTION,
    CORE_EXTENSION,
    JobRunner,
    bindings_from,
    prune_conversation_changes,
    stale_change_workspaces,
)
from ufo.runtime.turns.changes import conversation_changed, turn_conversation_changed
from ufo.schema import tables

KEY = f"{CORE_EXTENSION}:{CHANGE_LOG_PRUNE_JOB}"


async def _conversation(workspace_id: UUID) -> tuple[UUID, UUID]:
    agent_id, conversation_id, turn_id = uuid4(), uuid4(), uuid4()
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
                name=f"agent-{agent_id.hex[:8]}",
                prompt="p",
                model="claude-opus-5-5",
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
                queue_key=uuid4().hex,
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
                inbound="ask",
                admission_source="member",
                terminal={"status": "done", "text": "ok"},
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id, turn_id


async def _logged(workspace_id: UUID) -> list[UUID]:
    async with workspace_tx() as connection:
        rows = await connection.execute(
            sa.select(tables.conversation_change_log.c.conversation_id)
            .where(tables.conversation_change_log.c.workspace_id == workspace_id)
            .order_by(tables.conversation_change_log.c.position)
        )
    return list(rows.scalars())


async def test_both_appends_name_the_conversation_and_the_prune_keeps_the_retention(
    db: None, tmp_path: Path
) -> None:
    workspace_id = uuid4()
    conversation_id, turn_id = await _conversation(workspace_id)
    async with workspace_tx() as connection:
        await conversation_changed(connection, workspace_id, conversation_id)
        await turn_conversation_changed(connection, turn_id)
    assert await _logged(workspace_id) == [conversation_id, conversation_id]

    stale = datetime.now(UTC) - CHANGE_LOG_RETENTION - timedelta(minutes=1)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.conversation_change_log)
            .values(created_at=stale)
            .where(
                tables.conversation_change_log.c.workspace_id == workspace_id,
                tables.conversation_change_log.c.position == 1,
            )
        )
    spec = JobSpec(
        name=CHANGE_LOG_PRUNE_JOB,
        schedule="* * * * * *",
        handler=prune_conversation_changes,
        candidates=stale_change_workspaces(),
    )
    runner = JobRunner(
        bindings=bindings_from((), (spec,)),
        manifests=(),
        blob=WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path)),
    )

    assert workspace_id in await runner.candidates(KEY)
    for candidate in await runner.candidates(KEY):
        await runner.fire(KEY, candidate)

    assert await _logged(workspace_id) == [conversation_id]
    assert workspace_id not in await runner.candidates(KEY)
