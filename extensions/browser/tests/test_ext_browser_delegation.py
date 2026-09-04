"""The browser delegation tools' proof: browser_task hands one objective to the `browser` profile
through a background `ctx.spawn` and awaits it under the call's timeout budget, and wide_browse
reads an entities file, fans a bounded pool of `browser` children over it, and writes the collected
rows to a workspace JSON file. A RecordingSpawn and a ScriptedSubagents stand in for the Subagents
workflow (a dependency, never asserted); the tests assert the tools' own marshalling — the payloads
spawned, the bounded wait and the cancel it fires, the entity dedupe and cap, and the workspace
write. The payloads carry the round budget: browser_task names none, taking the profile's default
session ceiling, while every wide_browse child is spawned narrowed."""

import asyncio
import json
import shlex
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel, ValidationError
from ufo_ext_browser.delegation import (
    BROWSER_TASK_TIMEOUT_FLOOR_MINUTES,
    DELEGATION_TOOLS,
    MAX_WIDE_BROWSE_ENTITIES,
)

from ufo.blob import FilesystemBlobStore
from ufo.harness.sandbox.session import (
    ExecResult,
    SandboxSession,
)
from ufo.runtime.tools.context import SpawnResult, SubagentStatus, ToolContext
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience


class _Result(BaseModel):
    result: str


@dataclass
class RecordingSpawn:
    spawned: list[tuple[str, dict[str, object], bool, str | None]] = field(default_factory=list)

    async def __call__(
        self,
        profile: str,
        payload: dict[str, object],
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        self.spawned.append((profile, payload, background, dedup_key))
        name = payload.get("task_name")
        output = None if background else _Result(result=f"did {name}")
        return SpawnResult(turn_id=uuid4(), conversation_id=uuid4(), output=output)


@dataclass
class ScriptedSubagents:
    """Answers `wait` with a scripted terminal after `finish_after_s` and records cancels."""

    text: str = ""
    status: str = "done"
    finish_after_s: float = 0.0
    cancelled: list[UUID] = field(default_factory=list)

    async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]:
        await asyncio.sleep(self.finish_after_s)
        return tuple(
            SubagentStatus(turn_id=turn_id, status=self.status, text=self.text)
            for turn_id in turn_ids
        )

    async def cancel(self, turn_id: UUID) -> SubagentStatus:
        self.cancelled.append(turn_id)
        return SubagentStatus(turn_id=turn_id, status="cancelled", text="")

    async def message(self, turn_id: UUID, text: str, dedup_key: str) -> SubagentStatus:
        raise NotImplementedError


@dataclass
class FilesSandbox:
    """Answers `cat <path>` from a scripted file map and captures write_file calls."""

    files: dict[str, str] = field(default_factory=dict)
    writes: dict[str, bytes] = field(default_factory=dict)

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        for path, content in self.files.items():
            if shlex.quote(path) in command:
                return ExecResult(stdout=content, stderr="", exit_code=0)
        return ExecResult(stdout="", stderr="not found", exit_code=1)

    async def write_file(self, path: str, content: bytes) -> None:
        self.writes[path] = content


def _context(
    sandbox: FilesSandbox | SandboxSession,
    spawn: RecordingSpawn,
    tmp_path: Path,
    idempotency_key: str | None = None,
    subagents: ScriptedSubagents | None = None,
) -> ToolContext:
    return ToolContext(
        sandbox=sandbox,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
        subagents=subagents,
        idempotency_key=idempotency_key,
    )


def _tool(name: str):
    return next(tool for tool in DELEGATION_TOOLS if tool.name == name)


async def test_browser_task_spawns_the_browser_profile_and_awaits_its_terminal(
    tmp_path: Path,
) -> None:
    spawn = RecordingSpawn()
    control = ScriptedSubagents(text='{"result": "did jobs"}')
    tool = _tool("browser_task")
    assert tool.side_effecting is True
    args = tool.input_model.model_validate(
        {
            "url": "https://jobs.example.com",
            "task": "list open roles",
            "task_name": "jobs",
        }
    )
    assert args.timeout_minutes == BROWSER_TASK_TIMEOUT_FLOOR_MINUTES
    ctx = _context(
        FilesSandbox(),
        spawn,
        tmp_path,
        idempotency_key="turn-1/browser_task/call-7",
        subagents=control,
    )
    result = await tool.handler(ctx, args)
    assert spawn.spawned == [
        (
            "profile:browser",
            {"task": "list open roles", "url": "https://jobs.example.com", "task_name": "jobs"},
            True,
            "turn-1/browser_task/call-7",
        )
    ]
    assert control.cancelled == []
    assert json.loads(result.content[0].text) == {"result": "did jobs"}


async def test_browser_task_cancels_a_child_that_outlives_its_timeout(tmp_path: Path) -> None:
    """The enforcing half of `timeout_minutes`: the wait on the child is bounded, and an expired
    child is cancelled and reported as a recoverable tool error rather than awaited forever. Built
    with `model_construct` to slip a zero-minute budget under the schema floor (which
    `model_validate` forbids, proven below) so the deadline fires without a real 20-minute wait."""
    spawn = RecordingSpawn()
    control = ScriptedSubagents(text='{"result": "too late"}', finish_after_s=3600.0)
    tool = _tool("browser_task")
    result = await tool.handler(
        _context(FilesSandbox(), spawn, tmp_path, subagents=control),
        tool.input_model.model_construct(
            url="https://slow.example.com",
            task="wait forever",
            task_name="slow",
            timeout_minutes=0,
        ),
    )
    assert result.is_error is True
    assert "timeout" in result.content[0].text
    assert len(control.cancelled) == 1


async def test_browser_task_reattached_to_its_timeout_cancelled_child_reports_the_timeout(
    tmp_path: Path,
) -> None:
    spawn = RecordingSpawn()
    control = ScriptedSubagents(status="cancelled")
    tool = _tool("browser_task")
    result = await tool.handler(
        _context(
            FilesSandbox(),
            spawn,
            tmp_path,
            idempotency_key="turn-1/browser_task/call-7",
            subagents=control,
        ),
        tool.input_model.model_validate(
            {
                "url": "https://jobs.example.com",
                "task": "list open roles",
                "task_name": "jobs",
            }
        ),
    )
    assert result.is_error is True
    assert "timeout" in result.content[0].text
    assert control.cancelled == []


async def test_browser_task_rejects_a_timeout_below_the_floor() -> None:
    with pytest.raises(ValidationError):
        _tool("browser_task").input_model.model_validate(
            {
                "url": "https://jobs.example.com",
                "task": "list open roles",
                "task_name": "jobs",
                "timeout_minutes": BROWSER_TASK_TIMEOUT_FLOOR_MINUTES - 1,
            }
        )


async def test_wide_browse_fans_over_deduped_entities_and_writes_the_json(tmp_path: Path) -> None:
    sandbox = FilesSandbox(
        files={
            "entities.txt": "acme.com\nacme.com\n\nbeta.io\n",
            "schema.json": '{"price": "number"}',
        }
    )
    spawn = RecordingSpawn()
    tool = _tool("wide_browse")
    result = await tool.handler(
        _context(sandbox, spawn, tmp_path),
        tool.input_model.model_validate(
            {
                "entities_file": "entities.txt",
                "prompt_template": "get pricing from {entity}",
                "output_schema_file": "schema.json",
            }
        ),
    )
    entities = [payload["task_name"] for _, payload, _, _ in spawn.spawned]
    assert entities == ["acme.com", "beta.io"]
    assert all('{"price": "number"}' in payload["task"] for _, payload, _, _ in spawn.spawned)
    assert all(payload["extended_context"] is False for _, payload, _, _ in spawn.spawned)
    assert "wide_browse.json" in sandbox.writes
    rows = json.loads(sandbox.writes["wide_browse.json"])
    assert [row["entity"] for row in rows] == ["acme.com", "beta.io"]
    assert json.loads(result.content[0].text)["output_file"] == "wide_browse.json"


async def test_wide_browse_caps_the_entity_count(tmp_path: Path) -> None:
    too_many = "\n".join(f"site{i}.com" for i in range(MAX_WIDE_BROWSE_ENTITIES + 1))
    sandbox = FilesSandbox(files={"entities.txt": too_many, "schema.json": ""})
    tool = _tool("wide_browse")
    with pytest.raises(ValueError, match="at most"):
        await tool.handler(
            _context(sandbox, RecordingSpawn(), tmp_path),
            tool.input_model.model_validate(
                {
                    "entities_file": "entities.txt",
                    "prompt_template": "get {entity}",
                    "output_schema_file": "schema.json",
                }
            ),
        )


@dataclass
class FailingSpawn(RecordingSpawn):
    """Fails one named entity and answers the rest, so a row can be told from a lost batch."""

    fails: str = ""
    error: Exception = field(default_factory=lambda: RuntimeError("the browser would not start"))

    async def __call__(
        self,
        profile: str,
        payload: dict[str, object],
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        if payload.get("task_name") == self.fails:
            raise self.error
        return await super().__call__(profile, payload, background, dedup_key)


async def test_wide_browse_keeps_the_siblings_of_an_entity_that_raised(tmp_path: Path) -> None:
    """One child's fault is that entity's row. Its siblings each burned a real child turn, so a
    raise out of the fan-out throws away work already paid for and reports one message about the
    one that failed."""
    sandbox = FilesSandbox(
        files={"entities.txt": "acme.com\nbeta.io\ngamma.dev\n", "schema.json": ""}
    )
    spawn = FailingSpawn(fails="beta.io")
    tool = _tool("wide_browse")
    result = await tool.handler(
        _context(sandbox, spawn, tmp_path),
        tool.input_model.model_validate(
            {
                "entities_file": "entities.txt",
                "prompt_template": "get pricing from {entity}",
                "output_schema_file": "schema.json",
            }
        ),
    )
    rows = json.loads(sandbox.writes["wide_browse.json"])
    assert [row["entity"] for row in rows] == ["acme.com", "beta.io", "gamma.dev"]
    assert rows[1]["error"] == "the browser would not start"
    assert "error" not in rows[0] and "error" not in rows[2]
    assert rows[0]["result"] and rows[2]["result"]
    assert json.loads(result.content[0].text)["output_file"] == "wide_browse.json"


async def test_wide_browse_names_the_class_of_an_entity_that_raised_bare(tmp_path: Path) -> None:
    """An exception raised with no message leaves `str()` empty, and an empty `error` beside an
    empty `result` is a row that reads as though the entity was never attempted."""
    sandbox = FilesSandbox(files={"entities.txt": "acme.com\nbeta.io\n", "schema.json": ""})
    tool = _tool("wide_browse")
    await tool.handler(
        _context(sandbox, FailingSpawn(fails="beta.io", error=TimeoutError()), tmp_path),
        tool.input_model.model_validate(
            {
                "entities_file": "entities.txt",
                "prompt_template": "get {entity}",
                "output_schema_file": "schema.json",
            }
        ),
    )
    rows = json.loads(sandbox.writes["wide_browse.json"])
    assert rows[1]["error"] == "TimeoutError"


async def test_browser_task_names_the_cancelled_child_it_leaves_behind(tmp_path: Path) -> None:
    """A cancelled browser task was driving a real browser until the cancel, so whatever it did on
    the page stands. Its turn is the identity the agent reads that work by; a failure that names
    only the timeout reads as though nothing happened."""
    spawn = RecordingSpawn()
    control = ScriptedSubagents(finish_after_s=3600.0)
    tool = _tool("browser_task")
    result = await tool.handler(
        _context(FilesSandbox(), spawn, tmp_path, subagents=control),
        tool.input_model.model_construct(
            url="https://shop.example",
            task="buy it",
            task_name="checkout",
            timeout_minutes=0,
        ),
    )
    assert result.is_error is True
    failure = json.loads(result.content[0].text)
    assert failure["applied"] == [
        {
            "kind": "browser_turn",
            "identity": str(control.cancelled[0]),
            "state": "cancelled mid-task",
        }
    ]
    assert "checkout" in failure["summary"]
