from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_todos as todos

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.runtime.ext.context import ScopedStore, context_for
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.sdk.manifest import ConversationSlotContext, HookContext, InjectContext

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]


@dataclass
class _NoSandbox:
    """The todo tools never touch the sandbox — they reach only the extension's scoped store and
    the spawn port — so the context's sandbox is a stand-in the handlers must never call."""


async def _unavailable_spawn(target: str, payload: dict, *args: object, **kwargs: object) -> None:
    raise AssertionError("this todo call must not spawn")


@dataclass
class _RecordingSpawn:
    """A spawn port that records what it was handed. The seam under test is what `delegate_todos`
    sends and what it writes back, not what a child does with it."""

    turn_id: UUID
    calls: list[tuple[str, dict, dict]]

    async def __call__(
        self, target: str, payload: dict, *args: object, **kwargs: object
    ) -> SpawnResult:
        self.calls.append((target, payload, dict(kwargs)))
        return SpawnResult(turn_id=self.turn_id, conversation_id=uuid4(), output=None)


@dataclass
class _RefusingSpawn:
    async def __call__(self, target: str, payload: dict, *args: object, **kwargs: object) -> None:
        raise RuntimeError("no such target")


async def _seed_workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


def _context(
    workspace_id: UUID,
    conversation_id: UUID,
    tmp_path: Path,
    spawn: object = _unavailable_spawn,
    member_id: UUID | None = None,
) -> ToolContext:
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
        agent=Agent(prompt="p", model="claude-opus-5-5"),
        spawn=spawn,
        speaker_member_id=member_id,
        audience=conversation_audience(None),
        artifact_token_secret="",
        idempotency_key="call-1",
        ext=ext,
    )


def _slot_context(ctx: ToolContext) -> ConversationSlotContext:
    return ConversationSlotContext(
        ext=ctx.ext,
        conversation_id=ctx.turn.conversation_id,
        agent_id=ctx.turn.agent_id,
        audience=ctx.audience,
        messages=(),
    )


NESTED = todos.UpdateTodoListInput(
    title="Launch",
    tasks=(
        todos.TodoTask(
            description="ship the migration",
            tasks=(
                todos.TodoTask(description="write the revision"),
                todos.TodoTask(description="run it"),
            ),
        ),
        todos.TodoTask(description="land the consumer"),
    ),
)


def test_manifest_declares_three_subagent_tools_a_hook_and_the_todo_section() -> None:
    manifest = todos.manifest()
    assert {tool.name for tool in manifest.tools} == {
        "update_todo_list",
        "update_todo_status",
        "delegate_todos",
    }
    assert all(tool.side_effecting for tool in manifest.tools)
    delegate = next(tool for tool in manifest.tools if tool.name == "delegate_todos")
    bookkeeping = tuple(tool for tool in manifest.tools if tool.name != "delegate_todos")
    assert all(tool.subagent_default for tool in bookkeeping), (
        "a subagent keeps a nested list of its own, so it holds both bookkeeping verbs"
    )
    assert not delegate.subagent_default, (
        "delegate_todos starts a turn and binds the speaker's authority, and a subagent's profile "
        "allowlist is what decides which turns it may start — no other spawning verb is offered "
        "to subagents by default, and one that was would reach past that allowlist"
    )
    assert delegate.binds_member_authority, (
        "the engine fills speaker_member_id only for a tool that declares member authority, and "
        "without it a private workspace agent refuses its own owner"
    )
    assert not any(tool.binds_member_authority for tool in bookkeeping), (
        "the bookkeeping verbs reach no member, so they bind no member's authority"
    )
    (section,) = manifest.prompt_sections
    assert section.name == "todo_list"
    assert "<todo_list>" in section.body
    (hook,) = manifest.hooks
    assert hook.event == "user_prompt_submit"
    assert hook.best_effort, (
        "user_prompt_submit gates, so a store read that faults would deny the turn over a checklist"
    )
    assert not any(tool.parallel_safe for tool in manifest.tools), (
        "the board is one key, so a read-modify-write handler must not run beside itself"
    )
    (slot,) = manifest.conversation_slots
    assert slot is todos.TASKS_SLOT


def test_the_board_key_is_one_the_replaced_image_never_reads() -> None:
    assert todos.BOARD_KEY_PREFIX == "board/"
    assert not todos.BOARD_KEY_PREFIX.startswith("todo/")


def test_a_node_takes_its_status_from_its_children() -> None:
    node = todos.TodoTask(
        description="ship",
        tasks=(
            todos.TodoTask(description="a", status="completed"),
            todos.TodoTask(description="b", status="pending"),
        ),
    )
    assert node.rolled_up == "in_progress"
    assert node.model_copy(update={"tasks": ()}).rolled_up == "pending"


def test_a_node_whose_children_are_all_complete_is_complete() -> None:
    node = todos.TodoTask(
        description="ship",
        tasks=(
            todos.TodoTask(description="a", status="completed"),
            todos.TodoTask(description="b", status="completed"),
        ),
    )
    assert node.rolled_up == "completed"


def test_a_blocked_child_carries_over_an_in_progress_sibling() -> None:
    """Blocked is the state the reader has to act on, so it wins the roll-up. A node reported
    in_progress over a child that cannot move hides the one thing nothing else will surface."""
    node = todos.TodoTask(
        description="ship",
        tasks=(
            todos.TodoTask(description="a", status="in_progress"),
            todos.TodoTask(description="b", status="blocked"),
        ),
    )
    assert node.rolled_up == "blocked"


def test_walk_addresses_every_task_by_its_path() -> None:
    board = todos.TodoBoard(title="Launch", tasks=list(NESTED.tasks))
    assert [path for path, _task in todos.walk(board.tasks)] == ["1", "1.1", "1.2", "2"]


def test_a_board_nested_past_the_bound_fails_loud() -> None:
    deep = todos.TodoTask(description="d")
    for _level in range(todos.MAX_DEPTH):
        deep = todos.TodoTask(description="d", tasks=(deep,))
    with pytest.raises(ValueError, match="nests deeper"):
        todos.TodoBoard(title="Deep", tasks=[deep])


async def test_a_nested_board_round_trips_through_the_store(db: None, tmp_path: Path) -> None:
    workspace_id = await _seed_workspace()
    conversation_id = uuid4()
    ctx = _context(workspace_id, conversation_id, tmp_path)
    with ws(workspace_id):
        created = await todos.update_todo_list(ctx, NESTED)
        assert "1.1. [pending] write the revision" in created.content[0].text

        updated = await todos.update_todo_status(
            ctx,
            todos.UpdateTodoStatusInput(
                updates=(
                    todos.TodoStatusUpdate(path="1.1", status="completed"),
                    todos.TodoStatusUpdate(path="1.2", status="completed"),
                ),
            ),
        )
        assert "1. [completed] ship the migration" in updated.content[0].text

        stored = await ScopedStore(extension=todos.NAME).get(
            f"{todos.BOARD_KEY_PREFIX}{conversation_id}"
        )
        assert stored["tasks"][0]["tasks"][0]["status"] == "completed"
        assert stored["tasks"][0]["status"] == "pending", (
            "a node's own stored status is never written; it is derived on read"
        )


async def test_a_node_cannot_be_marked_over_its_children(db: None, tmp_path: Path) -> None:
    workspace_id = await _seed_workspace()
    ctx = _context(workspace_id, uuid4(), tmp_path)
    with ws(workspace_id):
        await todos.update_todo_list(ctx, NESTED)
        with pytest.raises(ValueError, match="takes its status from them"):
            await todos.update_todo_status(
                ctx,
                todos.UpdateTodoStatusInput(
                    updates=(todos.TodoStatusUpdate(path="1", status="completed"),),
                ),
            )


async def test_an_unknown_path_fails_loud_and_names_the_paths_there(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _seed_workspace()
    ctx = _context(workspace_id, uuid4(), tmp_path)
    with ws(workspace_id):
        await todos.update_todo_list(ctx, NESTED)
        with pytest.raises(ValueError, match=r"no todo at path '3'.*1, 1.1, 1.2, 2"):
            await todos.update_todo_status(
                ctx,
                todos.UpdateTodoStatusInput(
                    updates=(todos.TodoStatusUpdate(path="3", status="completed"),),
                ),
            )


async def test_status_before_any_list_fails_loud(db: None, tmp_path: Path) -> None:
    workspace_id = await _seed_workspace()
    ctx = _context(workspace_id, uuid4(), tmp_path)
    with ws(workspace_id), pytest.raises(ValueError, match="no todo list"):
        await todos.update_todo_status(
            ctx,
            todos.UpdateTodoStatusInput(
                updates=(todos.TodoStatusUpdate(path="1", status="completed"),),
            ),
        )


async def test_delegation_sends_the_payload_the_target_declares_and_marks_the_tasks(
    db: None, tmp_path: Path
) -> None:
    """The payload is the model's."""
    workspace_id = await _seed_workspace()
    conversation_id = uuid4()
    child_turn_id = uuid4()
    member_id = uuid4()
    spawn = _RecordingSpawn(turn_id=child_turn_id, calls=[])
    ctx = _context(workspace_id, conversation_id, tmp_path, spawn=spawn, member_id=member_id)
    with ws(workspace_id):
        await todos.update_todo_list(ctx, NESTED)
        result = await todos.delegate_todos(
            ctx,
            todos.DelegateTodosInput(
                target="coding",
                paths=("1",),
                payload={"objective": "ship the migration, writing and running the revision"},
            ),
        )
    (target, payload, kwargs) = spawn.calls[0]
    assert target == "coding"
    assert payload == {"objective": "ship the migration, writing and running the revision"}
    assert kwargs["background"] is True
    assert kwargs["delivers_result"] is True
    assert kwargs["dedup_key"] == "call-1"
    assert kwargs["requester_member_id"] == member_id, (
        "without the speaker a member's own agent refuses its owner, and a target needing the "
        "member's own model key looks up an empty account set"
    )
    assert str(child_turn_id) in result.content[0].text
    with ws(workspace_id):
        board = todos.TodoBoard.model_validate(
            await ScopedStore(extension=todos.NAME).get(
                f"{todos.BOARD_KEY_PREFIX}{conversation_id}"
            )
        )
    assert [task.status for task in board.tasks[0].tasks] == ["in_progress", "in_progress"]


async def test_a_refused_delegation_moves_nothing(db: None, tmp_path: Path) -> None:
    """A spawn that never started leaves tasks nobody is working on. Marking them first would put
    the board in the one state it cannot be read out of: in_progress with no worker."""
    workspace_id = await _seed_workspace()
    conversation_id = uuid4()
    ctx = _context(workspace_id, conversation_id, tmp_path, spawn=_RefusingSpawn())
    with ws(workspace_id):
        await todos.update_todo_list(ctx, NESTED)
        result = await todos.delegate_todos(
            ctx,
            todos.DelegateTodosInput(target="nowhere", paths=("2",), payload={"task": "port beta"}),
        )
        board = todos.TodoBoard.model_validate(
            await ScopedStore(extension=todos.NAME).get(
                f"{todos.BOARD_KEY_PREFIX}{conversation_id}"
            )
        )
    assert result.is_error
    assert "no such target" in result.content[0].text
    assert "No task moved — task 2 is still yours" in result.content[0].text
    assert "Take it" not in result.content[0].text
    assert "delegate to another target" not in result.content[0].text
    assert board.tasks[1].status == "pending"


async def test_every_turn_on_a_conversation_with_a_board_opens_holding_it(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _seed_workspace()
    conversation_id = uuid4()
    ctx = _context(workspace_id, conversation_id, tmp_path)
    with ws(workspace_id):
        await todos.update_todo_list(ctx, NESTED)
        outcome = await todos._inject_board(
            HookContext(ext=ctx.ext, payload={}, turn=ctx.turn, agent=ctx.agent)
        )
    assert isinstance(outcome, InjectContext)
    assert "1.1. [pending] write the revision" in outcome.text
    assert "update it as you go" in outcome.text


async def test_a_conversation_with_no_board_is_injected_nothing(db: None, tmp_path: Path) -> None:
    workspace_id = await _seed_workspace()
    ctx = _context(workspace_id, uuid4(), tmp_path)
    with ws(workspace_id):
        assert (
            await todos._inject_board(
                HookContext(ext=ctx.ext, payload={}, turn=ctx.turn, agent=ctx.agent)
            )
            is None
        )


async def test_the_tasks_slot_flattens_the_tree_with_its_depths(db: None, tmp_path: Path) -> None:
    workspace_id = await _seed_workspace()
    conversation_id = uuid4()
    ctx = _context(workspace_id, conversation_id, tmp_path)
    with ws(workspace_id):
        await todos.update_todo_list(ctx, NESTED)
        await todos.update_todo_status(
            ctx,
            todos.UpdateTodoStatusInput(
                updates=(todos.TodoStatusUpdate(path="1.1", status="completed"),),
            ),
        )
        assert await todos.TASKS_SLOT.summarize(_slot_context(ctx)) == 4
        payload = await todos.TASKS_SLOT.read(_slot_context(ctx))
    assert payload.title == "Launch"
    assert [task.depth for task in payload.tasks] == [0, 1, 1, 0]
    assert [task.status for task in payload.tasks] == [
        "in_progress",
        "completed",
        "pending",
        "pending",
    ]
    assert payload.total_count == 4
    assert payload.completed_count == 1


async def test_tasks_slot_distinguishes_no_board_from_an_empty_board(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _seed_workspace()
    ctx = _context(workspace_id, uuid4(), tmp_path)
    with ws(workspace_id):
        assert await todos.TASKS_SLOT.summarize(_slot_context(ctx)) is None
        missing = await todos.TASKS_SLOT.read(_slot_context(ctx))
        assert missing.title == ""
        assert missing.tasks == ()
        assert missing.total_count == 0
        assert missing.completed_count == 0
        assert missing.truncated is False
        await todos.update_todo_list(
            ctx, todos.UpdateTodoListInput(title="Nothing queued", tasks=())
        )
        assert await todos.TASKS_SLOT.summarize(_slot_context(ctx)) == 0


async def test_tasks_slot_bounds_the_projection_without_changing_its_source_count(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _seed_workspace()
    conversation_id = uuid4()
    ctx = _context(workspace_id, conversation_id, tmp_path)
    task_count = todos.CONVERSATION_TASKS_MAX + 1
    with ws(workspace_id):
        await ctx.ext.store.put(
            f"{todos.BOARD_KEY_PREFIX}{conversation_id}",
            {
                "title": "t" * (todos.CONVERSATION_TASK_TITLE_MAX_CHARS + 1),
                "tasks": [
                    {
                        "description": "x" * (todos.CONVERSATION_TASK_DESCRIPTION_MAX_CHARS + 1),
                        "status": "pending",
                        "tasks": [],
                    }
                    for _index in range(task_count)
                ],
            },
        )
        assert await todos.TASKS_SLOT.summarize(_slot_context(ctx)) == task_count
        payload = await todos.TASKS_SLOT.read(_slot_context(ctx))
    assert len(payload.title) == todos.CONVERSATION_TASK_TITLE_MAX_CHARS
    assert len(payload.tasks) == todos.CONVERSATION_TASKS_MAX
    assert len(payload.tasks[0].description) == todos.CONVERSATION_TASK_DESCRIPTION_MAX_CHARS
    assert payload.total_count == task_count
    assert payload.completed_count == 0
    assert payload.truncated is True
