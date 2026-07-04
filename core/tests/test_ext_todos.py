import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import selfhost_ext_todos as todos
import sqlalchemy as sa
from cryptography.fernet import Fernet

from selfhost.blob import FilesystemBlobStore
from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.ext.context import ScopedStore, context_for
from selfhost.schema import tables
from selfhost.schema.records import Agent, Turn
from selfhost.tools.context import SpawnResult, ToolContext


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
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    ext = context_for(workspace_id, todos.NAME, frozenset(), store)
    turn = Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        agent_id=uuid4(),
        seq=0,
        status="running",
        inbound="hello",
    )
    return ToolContext(
        sandbox=_NoSandbox(),
        blob=FilesystemBlobStore(root=tmp_path),
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        member_id=None,
        artifact_token_secret="",
        ext=ext,
    )


def test_manifest_declares_both_tools_and_the_todo_section() -> None:
    manifest = todos.manifest()
    assert {tool.name for tool in manifest.tools} == {"update_todo_list", "update_todo_status"}
    update_list = next(tool for tool in manifest.tools if tool.name == "update_todo_list")
    assert "checklist" in update_list.description
    assert "title" in update_list.input_model.model_json_schema()["properties"]
    (section,) = manifest.prompt_sections
    assert section.name == "todo_list"
    assert "<todo_list>" in section.body


async def test_update_status_round_trips_through_the_store(db: None, tmp_path: Path) -> None:
    workspace_id = await _seed_workspace()
    conversation_id = uuid4()
    ctx = _context(workspace_id, conversation_id, tmp_path)
    created = await todos.update_todo_list(
        ctx,
        todos.UpdateTodoListInput(
            title="Launch",
            tasks=(
                todos.TodoTask(description="build"),
                todos.TodoTask(description="test"),
            ),
            user_description="tracking the launch",
        ),
    )
    board = json.loads(created.content[0].text)
    assert board["title"] == "Launch"
    assert [task["status"] for task in board["tasks"]] == ["pending", "pending"]

    updated = await todos.update_todo_status(
        ctx,
        todos.UpdateTodoStatusInput(
            updates=(todos.TodoStatusUpdate(index=1, status="completed"),),
            user_description="finished the build",
        ),
    )
    updated_board = json.loads(updated.content[0].text)
    assert [task["status"] for task in updated_board["tasks"]] == ["completed", "pending"]

    scoped = ScopedStore(workspace_id=workspace_id, extension=todos.NAME)
    stored = await scoped.get(f"{todos.TODO_KEY_PREFIX}{conversation_id}")
    assert stored["tasks"][0]["status"] == "completed"


async def test_status_before_any_list_fails_loud(db: None, tmp_path: Path) -> None:
    workspace_id = await _seed_workspace()
    ctx = _context(workspace_id, uuid4(), tmp_path)
    with pytest.raises(ValueError, match="no todo list"):
        await todos.update_todo_status(
            ctx,
            todos.UpdateTodoStatusInput(
                updates=(todos.TodoStatusUpdate(index=1, status="completed"),),
                user_description="nothing to update",
            ),
        )


async def test_an_out_of_range_index_fails_loud(db: None, tmp_path: Path) -> None:
    workspace_id = await _seed_workspace()
    ctx = _context(workspace_id, uuid4(), tmp_path)
    await todos.update_todo_list(
        ctx,
        todos.UpdateTodoListInput(
            title="Small",
            tasks=(todos.TodoTask(description="only one"),),
            user_description="one task",
        ),
    )
    with pytest.raises(ValueError, match="todo index 5 out of range"):
        await todos.update_todo_status(
            ctx,
            todos.UpdateTodoStatusInput(
                updates=(todos.TodoStatusUpdate(index=5, status="completed"),),
                user_description="bad index",
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
    )
    ctx = ToolContext(
        sandbox=_NoSandbox(),
        blob=FilesystemBlobStore(root=tmp_path),
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        member_id=None,
        artifact_token_secret="",
        ext=None,
    )
    with pytest.raises(RuntimeError, match="todos extension context"):
        await todos.update_todo_list(
            ctx,
            todos.UpdateTodoListInput(title="x", tasks=(), user_description="d"),
        )
