import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_todos as todos

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.ext.context import ScopedStore, context_for
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.sdk.manifest import ConversationSlotContext
from ufo.tools.context import SpawnResult, ToolContext
from ufo.workspace import ws


@dataclass
class _NoSandbox:
    """The todo tools never touch the sandbox — they reach only the extension's scoped store — so
    the context's sandbox is a stand-in the handlers must never call."""


async def _unavailable_spawn(profile: str, payload: dict, background: bool = False) -> SpawnResult:
    raise AssertionError("todo tools must not spawn")


@dataclass
class _StubMemory:
    async def recall(self, query: str, subjects: frozenset[str], limit: int) -> tuple:
        return ()

    async def commit(self, write: object) -> None:
        return None


async def _seed_workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


def _context(workspace_id: UUID, conversation_id: UUID, tmp_path: Path) -> ToolContext:
    ext = context_for(todos.NAME, frozenset())
    turn = Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        agent_id=uuid4(),
        seq=0,
        status="running",
        inbound="hello",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    return ToolContext(
        sandbox=_NoSandbox(),
        blob=FilesystemBlobStore(root=tmp_path),
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
        ext=ext,
    )


def test_manifest_declares_both_tools_and_the_todo_section() -> None:
    manifest = todos.manifest()
    assert {tool.name for tool in manifest.tools} == {"update_todo_list", "update_todo_status"}
    update_list = next(tool for tool in manifest.tools if tool.name == "update_todo_list")
    assert "checklist" in update_list.description
    assert "title" in update_list.input_model.model_json_schema()["properties"]
    assert all(tool.side_effecting for tool in manifest.tools)
    (section,) = manifest.prompt_sections
    assert section.name == "todo_list"
    assert "<todo_list>" in section.body
    (slot,) = manifest.conversation_slots
    assert slot is todos.TASKS_SLOT


async def test_update_status_round_trips_through_the_store(db: None, tmp_path: Path) -> None:
    workspace_id = await _seed_workspace()
    conversation_id = uuid4()
    ctx = _context(workspace_id, conversation_id, tmp_path)
    with ws(workspace_id):
        created = await todos.update_todo_list(
            ctx,
            todos.UpdateTodoListInput(
                title="Launch",
                tasks=(
                    todos.TodoTask(description="build"),
                    todos.TodoTask(description="test"),
                ),
            ),
        )
        board = json.loads(created.content[0].text)
        assert board["title"] == "Launch"
        assert [task["status"] for task in board["tasks"]] == ["pending", "pending"]

        updated = await todos.update_todo_status(
            ctx,
            todos.UpdateTodoStatusInput(
                updates=(todos.TodoStatusUpdate(index=1, status="completed"),),
            ),
        )
        updated_board = json.loads(updated.content[0].text)
        assert [task["status"] for task in updated_board["tasks"]] == ["completed", "pending"]

        scoped = ScopedStore(extension=todos.NAME)
        stored = await scoped.get(f"{todos.TODO_KEY_PREFIX}{conversation_id}")
        assert stored["tasks"][0]["status"] == "completed"
        assert "type" not in stored
        slot_context = ConversationSlotContext(
            ext=ctx.ext,
            conversation_id=conversation_id,
            agent_id=ctx.turn.agent_id,
            audience=ctx.audience,
            messages=(),
        )
        assert await todos.TASKS_SLOT.summarize(slot_context) == 2
        payload = await todos.TASKS_SLOT.read(slot_context)
        assert payload.title == "Launch"
        assert [task.status for task in payload.tasks] == ["completed", "pending"]
        assert payload.total_count == 2
        assert payload.completed_count == 1


async def test_tasks_slot_distinguishes_no_board_from_an_empty_board(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _seed_workspace()
    conversation_id = uuid4()
    ctx = _context(workspace_id, conversation_id, tmp_path)
    slot_context = ConversationSlotContext(
        ext=ctx.ext,
        conversation_id=conversation_id,
        agent_id=ctx.turn.agent_id,
        audience=ctx.audience,
        messages=(),
    )
    with ws(workspace_id):
        assert await todos.TASKS_SLOT.summarize(slot_context) is None
        missing = await todos.TASKS_SLOT.read(slot_context)
        assert missing.title == ""
        assert missing.tasks == ()
        assert missing.total_count == 0
        assert missing.completed_count == 0
        assert missing.truncated is False
        await todos.update_todo_list(
            ctx,
            todos.UpdateTodoListInput(
                title="Nothing queued",
                tasks=(),
            ),
        )
        assert await todos.TASKS_SLOT.summarize(slot_context) == 0


async def test_tasks_slot_bounds_the_projection_without_changing_its_source_count(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _seed_workspace()
    conversation_id = uuid4()
    ctx = _context(workspace_id, conversation_id, tmp_path)
    slot_context = ConversationSlotContext(
        ext=ctx.ext,
        conversation_id=conversation_id,
        agent_id=ctx.turn.agent_id,
        audience=ctx.audience,
        messages=(),
    )
    task_count = todos.CONVERSATION_TASKS_MAX + 1
    with ws(workspace_id):
        await ctx.ext.store.put(
            f"{todos.TODO_KEY_PREFIX}{conversation_id}",
            {
                "title": "t" * (todos.CONVERSATION_TASK_TITLE_MAX_CHARS + 1),
                "tasks": [
                    {
                        "description": "x" * (todos.CONVERSATION_TASK_DESCRIPTION_MAX_CHARS + 1),
                        "status": "pending",
                    }
                    for _index in range(task_count)
                ],
            },
        )
        assert await todos.TASKS_SLOT.summarize(slot_context) == task_count
        payload = await todos.TASKS_SLOT.read(slot_context)
        assert len(payload.title) == todos.CONVERSATION_TASK_TITLE_MAX_CHARS
        assert len(payload.tasks) == todos.CONVERSATION_TASKS_MAX
        assert len(payload.tasks[0].description) == todos.CONVERSATION_TASK_DESCRIPTION_MAX_CHARS
        assert payload.total_count == task_count
        assert payload.completed_count == 0
        assert payload.truncated is True


async def test_status_before_any_list_fails_loud(db: None, tmp_path: Path) -> None:
    workspace_id = await _seed_workspace()
    ctx = _context(workspace_id, uuid4(), tmp_path)
    with ws(workspace_id), pytest.raises(ValueError, match="no todo list"):
        await todos.update_todo_status(
            ctx,
            todos.UpdateTodoStatusInput(
                updates=(todos.TodoStatusUpdate(index=1, status="completed"),),
            ),
        )


async def test_an_out_of_range_index_fails_loud(db: None, tmp_path: Path) -> None:
    workspace_id = await _seed_workspace()
    ctx = _context(workspace_id, uuid4(), tmp_path)
    with ws(workspace_id):
        await todos.update_todo_list(
            ctx,
            todos.UpdateTodoListInput(
                title="Small",
                tasks=(todos.TodoTask(description="only one"),),
            ),
        )
        with pytest.raises(ValueError, match="todo index 5 out of range"):
            await todos.update_todo_status(
                ctx,
                todos.UpdateTodoStatusInput(
                    updates=(todos.TodoStatusUpdate(index=5, status="completed"),),
                ),
            )


async def test_requires_the_extension_context(tmp_path: Path) -> None:
    turn = Turn(
        id=uuid4(),
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=0,
        status="running",
        inbound="hi",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    ctx = ToolContext(
        sandbox=_NoSandbox(),
        blob=FilesystemBlobStore(root=tmp_path),
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
        ext=None,
    )
    with pytest.raises(RuntimeError, match="todos extension context"):
        await todos.update_todo_list(
            ctx,
            todos.UpdateTodoListInput(title="x", tasks=()),
        )
