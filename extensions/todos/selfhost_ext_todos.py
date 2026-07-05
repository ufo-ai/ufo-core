"""The todo checklist pack: update_todo_list creates or revises the list, update_todo_status marks
individual tasks — the two tools that let the agent track progress on a multi-step request.

The board is not turn-scoped state the agent holds in context; it is the extension's own durable
record, kept in the pack's scoped store keyed by the conversation so a later turn in the same
conversation reads back the list it left. update_todo_list writes the whole board;
update_todo_status reads it, applies 1-based index updates, and writes it back — refusing an update
before any list exists and an index outside the list. Each tool returns the current board so the
model always sees the checklist state, and every call rides the turn's live activity frame so a
surface shows the progress as it moves."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from selfhost.sdk.context import ExtensionContext
from selfhost.sdk.manifest import Manifest, PromptSection
from selfhost.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult

NAME = "todos"
VERSION = "0.1.0"
UPDATE_TODO_LIST_TOOL = "update_todo_list"
UPDATE_TODO_STATUS_TOOL = "update_todo_status"
TODO_KEY_PREFIX = "todo/"

TodoStatus = Literal["pending", "in_progress", "completed"]

UPDATE_TODO_LIST_DESCRIPTION = (
    "Create or revise a task checklist to track progress on complex, multi-step requests. Use for "
    "any task with multiple steps or tool calls. The checklist appears in the UI to show progress. "
    "Create at the START of work, not after."
)
UPDATE_TODO_STATUS_DESCRIPTION = (
    "Update individual task statuses in the checklist. Mark tasks 'in_progress' when starting and "
    "'completed' when done — immediately, don't batch. Multiple tasks can be in_progress "
    "simultaneously for parallel work."
)

SECTION_NAME = "todo_list"
SECTION_BODY = (Path(__file__).parent / "todo_list_section.md").read_text().strip()


class TodoTask(BaseModel):
    description: str = Field(description="The task text.")
    status: TodoStatus = Field(default="pending", description="The task's current status.")


class UpdateTodoListInput(BaseModel):
    title: str = Field(description="Title of the todo list.")
    tasks: tuple[TodoTask, ...] = Field(
        description="Complete list of tasks — this REPLACES the existing list entirely."
    )
    user_description: str = Field(
        description="Brief plain-language description shown in the activity timeline."
    )


class TodoStatusUpdate(BaseModel):
    index: int = Field(description="1-based index of the task in the list.")
    status: TodoStatus = Field(description="New status for the task.")


class UpdateTodoStatusInput(BaseModel):
    updates: tuple[TodoStatusUpdate, ...] = Field(
        min_length=1, description="List of status updates to apply."
    )
    user_description: str = Field(
        description="Brief plain-language description shown in the activity timeline."
    )


class TodoBoard(BaseModel):
    """The durable checklist the pack keeps in its scoped store — a validated shape both tools read
    and write, distinct from the wire input models the model fills."""

    title: str
    tasks: list[TodoTask]


def _require_ext(ctx: ToolContext) -> ExtensionContext:
    if ctx.ext is None:
        raise RuntimeError("todo tools require the todos extension context")
    return ctx.ext


def _board_key(ctx: ToolContext) -> str:
    return f"{TODO_KEY_PREFIX}{ctx.turn.conversation_id}"


def _board_result(board: TodoBoard) -> ToolResult:
    return ToolResult(content=(TextContent(text=board.model_dump_json()),))


async def _read_board(ext: ExtensionContext, key: str) -> TodoBoard | None:
    raw = await ext.store.get(key)
    return None if raw is None else TodoBoard.model_validate(raw)


async def update_todo_list(ctx: ToolContext, args: UpdateTodoListInput) -> ToolResult:
    ext = _require_ext(ctx)
    board = TodoBoard(title=args.title, tasks=list(args.tasks))
    await ext.store.put(_board_key(ctx), json.loads(board.model_dump_json()))
    return _board_result(board)


async def update_todo_status(ctx: ToolContext, args: UpdateTodoStatusInput) -> ToolResult:
    ext = _require_ext(ctx)
    key = _board_key(ctx)
    board = await _read_board(ext, key)
    if board is None or not board.tasks:
        raise ValueError("no todo list — call update_todo_list first")
    for update in args.updates:
        if not 1 <= update.index <= len(board.tasks):
            raise ValueError(f"todo index {update.index} out of range")
        board.tasks[update.index - 1].status = update.status
    await ext.store.put(key, json.loads(board.model_dump_json()))
    return _board_result(board)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(
            ToolDef(
                name=UPDATE_TODO_LIST_TOOL,
                description=UPDATE_TODO_LIST_DESCRIPTION,
                input_model=UpdateTodoListInput,
                handler=update_todo_list,
            ),
            ToolDef(
                name=UPDATE_TODO_STATUS_TOOL,
                description=UPDATE_TODO_STATUS_DESCRIPTION,
                input_model=UpdateTodoStatusInput,
                handler=update_todo_status,
            ),
        ),
        prompt_sections=(PromptSection(name=SECTION_NAME, body=SECTION_BODY),),
    )
