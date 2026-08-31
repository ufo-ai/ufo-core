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
import ufo_ext_browser.delegation as delegation
import ufo_ext_browserbase as browserbase
from pydantic import BaseModel, ValidationError
from ufo_ext_browser.delegation import (
    BROWSER_TASK_TIMEOUT_FLOOR_MINUTES,
    DELEGATION_TOOLS,
    MAX_WIDE_BROWSE_ENTITIES,
)

from ufo.blob import FilesystemBlobStore
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import (
    ExecResult,
    ProxyEndpoint,
    SandboxSession,
    SandboxSpec,
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


async def test_wide_browse_keys_each_child_on_the_call_and_entity(tmp_path: Path) -> None:
    """The producer half of the recovery-dedup seam: wide_browse is side_effecting and spawns each
    child under a dedup_key derived from the call's idempotency_key and the entity, deterministic
    across a crash-recovery re-run so the parent reconnects rather than respawns. browser_task
    keys its one child on the bare idempotency_key (proven above)."""
    wide_browse = _tool("wide_browse")
    assert wide_browse.side_effecting is True
    sandbox = FilesSandbox(files={"entities.txt": "acme.com\nbeta.io\n", "schema.json": ""})
    spawn = RecordingSpawn()
    ctx = _context(sandbox, spawn, tmp_path, idempotency_key="turn-1/wide_browse/call-3")
    await wide_browse.handler(
        ctx,
        wide_browse.input_model.model_validate(
            {
                "entities_file": "entities.txt",
                "prompt_template": "get pricing from {entity}",
                "output_schema_file": "schema.json",
            }
        ),
    )
    assert [dedup for *_, dedup in spawn.spawned] == [
        "turn-1/wide_browse/call-3/acme.com",
        "turn-1/wide_browse/call-3/beta.io",
    ]


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


def test_a_browser_task_budget_stays_within_what_a_leased_session_can_hold() -> None:
    """The browser a run holds is one leased session. A budget above what a transport will keep
    alive would drop the live connection mid-task instead of ending it through the graceful cancel
    path, so the tool refuses it rather than accepting a budget it cannot honour."""
    assert (
        delegation.BrowserTaskInput(
            url="https://example.com",
            task="t",
            task_name="n",
            timeout_minutes=delegation.BROWSER_TASK_TIMEOUT_CEILING_MINUTES,
        ).timeout_minutes
        == delegation.BROWSER_TASK_TIMEOUT_CEILING_MINUTES
    )
    with pytest.raises(ValidationError):
        delegation.BrowserTaskInput(
            url="https://example.com",
            task="t",
            task_name="n",
            timeout_minutes=delegation.BROWSER_TASK_TIMEOUT_CEILING_MINUTES + 1,
        )
    assert (
        browserbase.SESSION_TIMEOUT_SECONDS > delegation.BROWSER_TASK_TIMEOUT_CEILING_MINUTES * 60
    )


async def test_a_real_shell_reads_hostile_paths_literally(tmp_path: Path) -> None:
    """The injection proof, against a REAL bash through LocalCarrier: BOTH reads the handler makes —
    the entities file and the output schema — are given a name containing `$(…)` and backticks, and
    each must come back as file contents with the substitution it would have run never happening."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    entities = 'entities.txt"; $(touch pwned_entities) `touch ticked_entities` $HOME'
    schema = 'schema.json"; $(touch pwned_schema) `touch ticked_schema` $HOME'
    (workspace / entities).write_text("acme.com\n")
    (workspace / schema).write_text('{"price": "number"}')
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref="ufo-sandbox:latest",
            workspace_host_path=str(workspace),
            proxy=ProxyEndpoint(port=9999, ca_cert="CA-PEM-BYTES"),
            run_token="run-token-abc",
        )
    )
    session = SandboxSession(carrier=carrier, handle=handle)
    spawn = RecordingSpawn()
    tool = _tool("wide_browse")
    await tool.handler(
        _context(session, spawn, tmp_path),
        tool.input_model.model_validate(
            {
                "entities_file": entities,
                "prompt_template": "get pricing from {entity}",
                "output_schema_file": schema,
            }
        ),
    )
    assert [payload["task_name"] for _, payload, *_ in spawn.spawned] == ["acme.com"]
    assert all('{"price": "number"}' in payload["task"] for _, payload, *_ in spawn.spawned)
    assert not any(workspace.glob("pwned_*")) and not any(workspace.glob("ticked_*"))
