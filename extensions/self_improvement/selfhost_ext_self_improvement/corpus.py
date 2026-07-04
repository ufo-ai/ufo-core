"""The eval corpus: the workspace's trajectories reduced to the task classes worth refining. A
trajectory is worth refining when a tool round errored inside it — the friction signal that survives
in the transcript (selfhost has no self-report side-channel) — and its class is the tool that
failed. Each class splits into a mine set (the proposer's examples) and a held-out set (the gate
replays), so a candidate is never graded on the turns it was proposed from."""

from dataclasses import dataclass
from uuid import UUID

from selfhost.sdk.context import Trajectory
from selfhost.sdk.models import Message, ToolResultBlock, ToolUseBlock

EVAL_MIN = 1
MINE_MIN = 1
MIN_CLASS_SIZE = EVAL_MIN + MINE_MIN
MAX_EXAMPLES = 5
EXAMPLE_CHARS = 2000


@dataclass(frozen=True)
class TaskExample:
    """One flagged trajectory reduced to what the loop needs: the conversation it came from (the
    held-out handle), the user's request (the grading key), the full messages (the replay context),
    and the tool error the proposer learns from."""

    conversation_id: UUID
    request: str
    messages: tuple[Message, ...]
    problem: str


@dataclass(frozen=True)
class TaskClass:
    name: str
    mine: tuple[TaskExample, ...]
    held_out: tuple[TaskExample, ...]


def first_request(messages: tuple[Message, ...]) -> str | None:
    for message in messages:
        if message.role == "user" and isinstance(message.content, str) and message.content:
            return message.content
    return None


def first_tool_error(messages: tuple[Message, ...]) -> tuple[str, str] | None:
    """The first errored tool round as (tool_name, error_content), or None. The erroring tool's name
    comes from the tool_use its result answers."""
    names: dict[str, str] = {}
    for message in messages:
        if isinstance(message.content, str):
            continue
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                names[block.id] = block.name
    for message in messages:
        if isinstance(message.content, str):
            continue
        for block in message.content:
            if isinstance(block, ToolResultBlock) and block.is_error:
                name = names.get(block.tool_use_id)
                if name:
                    assert isinstance(block.content, str), "an error result is always plain text"
                    return name, block.content
    return None


def bad_trajectory(trajectory: Trajectory) -> tuple[str, TaskExample] | None:
    """A flagged trajectory as (class-name, example), or None when nothing errored or there is no
    request to grade against. The class is `tool:<name>` — stratify by which tool failed."""
    error = first_tool_error(trajectory.messages)
    request = first_request(trajectory.messages)
    if error is None or request is None:
        return None
    name, content = error
    problem = f"the {name} tool errored" + (f": {content}" if content else "")
    example = TaskExample(trajectory.conversation_id, request, trajectory.messages, problem)
    return f"tool:{name}", example


def task_classes(trajectories: tuple[Trajectory, ...]) -> tuple[TaskClass, ...]:
    by_class: dict[str, list[TaskExample]] = {}
    for trajectory in trajectories:
        flagged = bad_trajectory(trajectory)
        if flagged is None:
            continue
        name, example = flagged
        by_class.setdefault(name, []).append(example)
    classes = [
        split
        for name, examples in by_class.items()
        if (split := _split(name, tuple(examples))) is not None
    ]
    return tuple(sorted(classes, key=lambda cls: (-(len(cls.mine) + len(cls.held_out)), cls.name)))


def _split(name: str, examples: tuple[TaskExample, ...]) -> TaskClass | None:
    if len(examples) < MIN_CLASS_SIZE:
        return None
    ordered = sorted(examples, key=lambda example: str(example.conversation_id))
    eval_count = min(max(EVAL_MIN, len(ordered) // 2), len(ordered) - MINE_MIN)
    return TaskClass(name, tuple(ordered[eval_count:]), tuple(ordered[:eval_count]))
