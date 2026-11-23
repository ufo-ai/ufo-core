from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa

from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.harness.models.interface import Message, ToolResultBlock, ToolUseBlock
from ufo.runtime.ext.manifest import JobSpec
from ufo.runtime.jobs import (
    CORE_EXTENSION,
    SETTLED_ACTIVITY_JOB,
    JobRunner,
    bindings_from,
    settle_subagent_activity,
    unsettled_activity_workspaces,
)
from ufo.runtime.turns.transcript import Conversation, encode, transcript_key
from ufo.runtime.workspace import ws
from ufo.schema import tables

KEY = f"{CORE_EXTENSION}:{SETTLED_ACTIVITY_JOB}"


async def _child(workspace_id: UUID, terminal: dict[str, object] | None) -> tuple[UUID, UUID]:
    agent_id, conversation_id, turn_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
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
                surface="subagent",
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
                inbound="{}",
                admission_source="internal",
                terminal=terminal,
                parent_turn_id=uuid4(),
                subagent_profile="coding",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id, turn_id


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _terminal(turn_id: UUID) -> dict[str, object]:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.turn.c.terminal).where(tables.turn.c.id == turn_id)
            )
        ).scalar_one()


def _runner(spec: JobSpec, root: Path) -> JobRunner:
    return JobRunner(
        bindings=bindings_from((), (spec,)),
        manifests=(),
        blob=WorkspaceBlobStore(backend=FilesystemBlobStore(root=root)),
    )


async def test_a_settled_child_gains_the_work_its_transcript_records(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    unsettled = {"status": "done", "text": "ok"}
    with_blob, recorded = await _child(workspace_id, unsettled)
    _lost, orphaned = await _child(workspace_id, unsettled)
    _root_conversation, already = await _child(workspace_id, {**unsettled, "activity": []})
    store = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    with ws(workspace_id):
        await store.put(
            transcript_key(with_blob),
            encode(
                Conversation(
                    seq=1,
                    messages=(
                        Message(role="user", content="{}"),
                        Message(
                            role="assistant",
                            content=(ToolUseBlock(id="c1", name="fetch_url", input={"url": "x"}),),
                        ),
                        Message(
                            role="user",
                            content=(
                                ToolResultBlock(
                                    tool_use_id="c1",
                                    content="…",
                                    activity=True,
                                    activity_text="Reading the source.",
                                ),
                            ),
                        ),
                    ),
                )
            ),
        )
    spec = JobSpec(
        name=SETTLED_ACTIVITY_JOB,
        schedule="* * * * * *",
        handler=settle_subagent_activity,
        candidates=unsettled_activity_workspaces(),
    )
    runner = _runner(spec, tmp_path)

    assert workspace_id in await runner.candidates(KEY)
    for candidate in await runner.candidates(KEY):
        await runner.fire(KEY, candidate)

    assert (await _terminal(recorded))["activity"] == [
        {"kind": "activity", "text": "Reading the source."}
    ]
    assert (await _terminal(orphaned))["activity"] == []
    assert (await _terminal(already))["activity"] == []
    assert workspace_id not in await runner.candidates(KEY)


async def test_a_settle_pass_writes_only_the_bound_workspaces_children(
    db: None, tmp_path: Path
) -> None:
    mine, theirs = await _workspace(), await _workspace()
    unsettled = {"status": "done", "text": "ok"}
    _, my_child = await _child(mine, unsettled)
    _, their_child = await _child(theirs, unsettled)
    spec = JobSpec(
        name=SETTLED_ACTIVITY_JOB,
        schedule="* * * * * *",
        handler=settle_subagent_activity,
        candidates=unsettled_activity_workspaces(),
    )

    await _runner(spec, tmp_path).fire(KEY, mine)

    assert (await _terminal(my_child))["activity"] == []
    assert await _terminal(their_child) == unsettled
