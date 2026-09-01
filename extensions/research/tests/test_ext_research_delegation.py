"""The research delegation tool's proof: `wide_research` reads an entities file, fans a bounded pool
of `research` children over it through `ctx.spawn`, and writes the collected rows to a workspace
JSON file. A RecordingSpawn stands in for the Subagents workflow (a dependency, never asserted); the
tests assert the tool's own marshalling — the objectives spawned, the entity dedupe and cap, the
write."""

import asyncio
import fnmatch
import json
import re
import shlex
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import pytest
from pydantic import BaseModel
from ufo_ext_research.delegation import (
    MAX_WIDE_RESEARCH_ENTITIES,
    WIDE_RESEARCH_ERROR_MAX_CHARS,
    WIDE_RESEARCH_TOOL,
)

from ufo.blob import FilesystemBlobStore
from ufo.harness.sandbox.session import (
    ExecResult,
    SandboxSession,
)
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience


class _Result(BaseModel):
    result: str


class WritableSandbox(Protocol):
    async def write_file(self, path: str, content: bytes) -> None: ...


@dataclass
class RecordingSpawn:
    spawned: list[tuple[str, dict[str, object], str | None]] = field(default_factory=list)
    outputs: list[object] = field(default_factory=list)
    invalid_outputs: set[int] = field(default_factory=set)
    missing_outputs: set[int] = field(default_factory=set)
    sandbox: WritableSandbox | None = None

    async def __call__(
        self,
        profile: str,
        payload: dict[str, object],
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        self.spawned.append((profile, payload, dedup_key))
        objective = str(payload["objective"])
        path = re.search(r"(/workspace/\.wide-research-[0-9a-f]{64}\.json)", objective)
        assert path is not None
        assert self.sandbox is not None
        index = len(self.spawned) - 1
        if index not in self.missing_outputs:
            content = (
                b"{"
                if index in self.invalid_outputs
                else json.dumps(
                    self.outputs[index] if index < len(self.outputs) else {"status": "done"}
                ).encode()
            )
            await self.sandbox.write_file(path.group(1), content)
        return SpawnResult(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            output=_Result(result=path.group(1)),
        )


@dataclass
class FilesSandbox:
    """Answers `cat <path>` from a scripted file map and captures write_file calls."""

    files: dict[str, str] = field(default_factory=dict)
    writes: dict[str, bytes] = field(default_factory=dict)
    remove_exit_code: int = 0
    blocked_write_prefix: str | None = None
    blocked_write_at: int = 1
    matching_write_count: int = 0
    blocked_write_started: asyncio.Event = field(default_factory=asyncio.Event)
    blocked_write_released: asyncio.Event = field(default_factory=asyncio.Event)

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        if command.startswith("find /workspace "):
            tokens = shlex.split(command)
            patterns = [tokens[index + 1] for index, token in enumerate(tokens) if token == "-name"]
            for path in tuple(self.files):
                name = Path(path).name
                if fnmatch.fnmatch(name, patterns[0]) and not fnmatch.fnmatch(name, patterns[1]):
                    self.files.pop(path)
                    self.writes.pop(path, None)
            return ExecResult(stdout="", stderr="", exit_code=0)
        if command.startswith("mv -f -- "):
            _, _, _, source, destination = shlex.split(command)
            if source not in self.files:
                return ExecResult(stdout="", stderr="not found", exit_code=1)
            content = self.files.pop(source)
            self.writes.pop(source, None)
            self.files[destination] = content
            self.writes[destination] = content.encode()
            return ExecResult(stdout="", stderr="", exit_code=0)
        if command.startswith("rm -f -- "):
            if self.remove_exit_code != 0:
                return ExecResult(
                    stdout="", stderr="cannot remove", exit_code=self.remove_exit_code
                )
            for path in tuple(self.files):
                if shlex.quote(path) in command:
                    self.files.pop(path)
            return ExecResult(stdout="", stderr="", exit_code=0)
        for path, content in self.files.items():
            if shlex.quote(path) in command:
                return ExecResult(stdout=content, stderr="", exit_code=0)
        return ExecResult(stdout="", stderr="not found", exit_code=1)

    async def write_file(self, path: str, content: bytes) -> None:
        if self.blocked_write_prefix is not None and path.startswith(self.blocked_write_prefix):
            self.matching_write_count += 1
            if self.matching_write_count == self.blocked_write_at:
                self.blocked_write_started.set()
                await self.blocked_write_released.wait()
        self.writes[path] = content
        self.files[path] = content.decode()


def _context(
    sandbox: FilesSandbox | SandboxSession,
    spawn: RecordingSpawn,
    tmp_path: Path,
    idempotency_key: str | None = "turn-1/wide_research/call-1",
) -> ToolContext:
    spawn.sandbox = sandbox
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
        idempotency_key=idempotency_key,
    )


async def test_wide_research_fans_the_research_profile_over_deduped_entities(
    tmp_path: Path,
) -> None:
    sandbox = FilesSandbox(
        files={
            "entities.txt": "acme.com\nacme.com\n\nbeta.io\n",
            "schema.json": '{"headcount": "number"}',
        }
    )
    spawn = RecordingSpawn()
    ctx = _context(sandbox, spawn, tmp_path)
    result = await WIDE_RESEARCH_TOOL.handler(
        ctx,
        WIDE_RESEARCH_TOOL.input_model.model_validate(
            {
                "entities_file": "entities.txt",
                "prompt_template": "research {entity}",
                "output_schema_file": "schema.json",
            }
        ),
    )
    assert {profile for profile, _, _ in spawn.spawned} == {"profile:research"}
    objectives = [payload["objective"] for _, payload, _ in spawn.spawned]
    assert objectives[0].startswith("research acme.com")
    assert objectives[1].startswith("research beta.io")
    assert all('{"headcount": "number"}' in objective for objective in objectives)
    assert all(
        "Write the complete result as JSON to /workspace/.wide-research-" in objective
        for objective in objectives
    )
    assert all("Return only the result path." in objective for objective in objectives)
    assert "wide_research.json" in sandbox.writes
    output = json.loads(sandbox.writes["wide_research.json"])
    assert output["untrusted"] is True
    assert output["source"] == "wide_research"
    assert re.fullmatch(r"[0-9a-f]{64}", output["call_id"])
    rows = output["rows"]
    assert [row["entity"] for row in rows] == ["acme.com", "beta.io"]
    assert rows == [
        {"entity": "acme.com", "result": {"status": "done"}, "error": ""},
        {"entity": "beta.io", "result": {"status": "done"}, "error": ""},
    ]
    result_payload = json.loads(result.content[0].text)
    assert result_payload["output_file"] == "wide_research.json"
    assert result_payload["rows"] == rows
    assert any(".wide-research-" in path for path in sandbox.files)
    await ctx.cleanup.drain()
    assert not any(
        re.fullmatch(r"/workspace/\.wide-research-[0-9a-f]{64}\.json", path)
        for path in sandbox.files
    )


async def test_wide_research_keys_each_child_on_the_call_and_entity(tmp_path: Path) -> None:
    """The producer half of the recovery-dedup seam: each child is spawned under a dedup_key derived
    from the tool call's stable idempotency_key and the entity, so a crash-recovery re-run of the
    fan-out reconnects to the same children rather than respawning them. The tool is side_effecting,
    so core folds the idempotency_key onto the context it consumes here."""
    assert WIDE_RESEARCH_TOOL.side_effecting is True
    assert WIDE_RESEARCH_TOOL.untrusted is True
    sandbox = FilesSandbox(files={"entities.txt": "acme.com\nbeta.io\n", "schema.json": ""})
    spawn = RecordingSpawn()
    ctx = _context(sandbox, spawn, tmp_path, idempotency_key="turn-1/wide_research/call-9")
    await WIDE_RESEARCH_TOOL.handler(
        ctx,
        WIDE_RESEARCH_TOOL.input_model.model_validate(
            {
                "entities_file": "entities.txt",
                "prompt_template": "research {entity}",
                "output_schema_file": "schema.json",
            }
        ),
    )
    assert [dedup for _, _, dedup in spawn.spawned] == [
        "turn-1/wide_research/call-9/acme.com",
        "turn-1/wide_research/call-9/beta.io",
    ]


async def test_wide_research_replay_reuses_the_aggregate_after_cleanup(tmp_path: Path) -> None:
    sandbox = FilesSandbox(files={"entities.txt": "acme.com\n", "schema.json": "{}"})
    spawn = RecordingSpawn(outputs=[{"run": 1}], missing_outputs={1})
    ctx = _context(sandbox, spawn, tmp_path)
    args = WIDE_RESEARCH_TOOL.input_model.model_validate(
        {
            "entities_file": "entities.txt",
            "prompt_template": "research {entity}",
            "output_schema_file": "schema.json",
        }
    )

    await WIDE_RESEARCH_TOOL.handler(ctx, args)
    await ctx.cleanup.drain()
    await WIDE_RESEARCH_TOOL.handler(ctx, args)

    rows = json.loads(sandbox.writes["wide_research.json"])["rows"]
    assert rows == [{"entity": "acme.com", "result": {"run": 1}, "error": ""}]
    assert len(spawn.spawned) == 1
    await ctx.cleanup.drain()
    assert not any(
        re.fullmatch(r"/workspace/\.wide-research-[0-9a-f]{64}\.json", path)
        for path in sandbox.files
    )


async def test_wide_research_preemption_keeps_an_unrecorded_child_file(tmp_path: Path) -> None:
    sandbox = FilesSandbox(
        files={"entities.txt": "acme.com\nbeta.io\n", "schema.json": "{}"},
        blocked_write_prefix="/workspace/.wide-research-aggregate-",
        blocked_write_at=2,
    )
    spawn = RecordingSpawn(outputs=[{"run": 1}, {"run": 2}], missing_outputs={2})
    ctx = _context(sandbox, spawn, tmp_path)
    args = WIDE_RESEARCH_TOOL.input_model.model_validate(
        {
            "entities_file": "entities.txt",
            "prompt_template": "research {entity}",
            "output_schema_file": "schema.json",
        }
    )

    first_attempt = asyncio.create_task(WIDE_RESEARCH_TOOL.handler(ctx, args))
    await sandbox.blocked_write_started.wait()
    recovery_paths = [
        path for path in sandbox.writes if path.startswith("/workspace/.wide-research-aggregate-")
    ]
    assert len(recovery_paths) == 1
    assert len(json.loads(sandbox.writes[recovery_paths[0]])["rows"]) == 1
    first_attempt.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_attempt
    await ctx.cleanup.drain()
    child_paths = [
        match.group()
        for match in re.finditer(
            r"/workspace/\.wide-research-[0-9a-f]{64}\.json", str(spawn.spawned)
        )
    ]
    assert len(child_paths) == 2
    assert child_paths[0] not in sandbox.files
    assert child_paths[1] in sandbox.files

    await WIDE_RESEARCH_TOOL.handler(ctx, args)

    rows = json.loads(sandbox.writes["wide_research.json"])["rows"]
    assert rows == [
        {"entity": "acme.com", "result": {"run": 1}, "error": ""},
        {"entity": "beta.io", "result": {"run": 2}, "error": ""},
    ]
    assert len(spawn.spawned) == 3


async def test_wide_research_replay_keeps_each_call_aggregate(tmp_path: Path) -> None:
    sandbox = FilesSandbox(files={"entities.txt": "acme.com\n", "schema.json": "{}"})
    spawn = RecordingSpawn(outputs=[{"run": 1}, {"run": 2}])
    first = _context(
        sandbox,
        spawn,
        tmp_path,
        idempotency_key="turn-1/wide_research/call-1",
    )
    second = replace(first, idempotency_key="turn-1/wide_research/call-2")
    args = WIDE_RESEARCH_TOOL.input_model.model_validate(
        {
            "entities_file": "entities.txt",
            "prompt_template": "research {entity}",
            "output_schema_file": "schema.json",
        }
    )

    await WIDE_RESEARCH_TOOL.handler(first, args)
    await WIDE_RESEARCH_TOOL.handler(second, args)
    await first.cleanup.drain()
    assert (
        len(
            [
                path
                for path in sandbox.files
                if path.startswith("/workspace/.wide-research-aggregate-")
            ]
        )
        == 2
    )
    await WIDE_RESEARCH_TOOL.handler(first, args)

    rows = json.loads(sandbox.writes["wide_research.json"])["rows"]
    assert rows == [{"entity": "acme.com", "result": {"run": 1}, "error": ""}]
    assert len(spawn.spawned) == 2


async def test_wide_research_prunes_recovery_files_from_prior_turns(tmp_path: Path) -> None:
    old_path = "/workspace/.wide-research-aggregate-old-call.json"
    sandbox = FilesSandbox(
        files={"entities.txt": "acme.com\n", "schema.json": "{}", old_path: "{}"}
    )
    spawn = RecordingSpawn(outputs=[{"run": 1}])

    await WIDE_RESEARCH_TOOL.handler(
        _context(sandbox, spawn, tmp_path),
        WIDE_RESEARCH_TOOL.input_model.model_validate(
            {
                "entities_file": "entities.txt",
                "prompt_template": "research {entity}",
                "output_schema_file": "schema.json",
            }
        ),
    )

    assert old_path not in sandbox.files
    assert (
        len(
            [
                path
                for path in sandbox.files
                if path.startswith("/workspace/.wide-research-aggregate-")
            ]
        )
        == 1
    )


async def test_wide_research_ignores_a_malformed_recovery_aggregate(tmp_path: Path) -> None:
    key = "turn-1/wide_research/call-1"
    call_id = sha256(key.encode()).hexdigest()
    sandbox = FilesSandbox(files={"entities.txt": "acme.com\n", "schema.json": "{}"})
    spawn = RecordingSpawn(outputs=[{"run": 1}])
    ctx = _context(sandbox, spawn, tmp_path, idempotency_key=key)
    recovery_path = f"/workspace/.wide-research-aggregate-{ctx.turn.id}-{call_id}.json"
    sandbox.files[recovery_path] = json.dumps({"call_id": call_id, "rows": [{"result": {}}]})

    await WIDE_RESEARCH_TOOL.handler(
        ctx,
        WIDE_RESEARCH_TOOL.input_model.model_validate(
            {
                "entities_file": "entities.txt",
                "prompt_template": "research {entity}",
                "output_schema_file": "schema.json",
            }
        ),
    )

    rows = json.loads(sandbox.writes["wide_research.json"])["rows"]
    assert rows == [{"entity": "acme.com", "result": {"run": 1}, "error": ""}]
    assert len(spawn.spawned) == 1


async def test_wide_research_cleanup_failure_keeps_the_complete_output(tmp_path: Path) -> None:
    sandbox = FilesSandbox(
        files={"entities.txt": "acme.com\n", "schema.json": "{}"}, remove_exit_code=1
    )
    spawn = RecordingSpawn(outputs=[{"run": 1}])
    ctx = _context(sandbox, spawn, tmp_path)
    args = WIDE_RESEARCH_TOOL.input_model.model_validate(
        {
            "entities_file": "entities.txt",
            "prompt_template": "research {entity}",
            "output_schema_file": "schema.json",
        }
    )

    await WIDE_RESEARCH_TOOL.handler(ctx, args)
    await ctx.cleanup.drain()

    rows = json.loads(sandbox.writes["wide_research.json"])["rows"]
    assert rows == [{"entity": "acme.com", "result": {"run": 1}, "error": ""}]
    assert any(".wide-research-" in path for path in sandbox.files)


async def test_one_missing_child_file_keeps_other_wide_research_rows(tmp_path: Path) -> None:
    sandbox = FilesSandbox(files={"entities.txt": "acme.com\nbeta.io\n", "schema.json": "{}"})
    spawn = RecordingSpawn(outputs=[{}, {"headcount": 42}], missing_outputs={0})
    await WIDE_RESEARCH_TOOL.handler(
        _context(sandbox, spawn, tmp_path),
        WIDE_RESEARCH_TOOL.input_model.model_validate(
            {
                "entities_file": "entities.txt",
                "prompt_template": "research {entity}",
                "output_schema_file": "schema.json",
            }
        ),
    )
    rows = json.loads(sandbox.writes["wide_research.json"])["rows"]
    assert rows[0]["entity"] == "acme.com"
    assert rows[0]["result"] is None
    assert rows[0]["error"].startswith("not found; child: /workspace/.wide-research-")
    assert rows[1] == {"entity": "beta.io", "result": {"headcount": 42}, "error": ""}


async def test_one_unparsable_child_file_keeps_other_wide_research_rows(tmp_path: Path) -> None:
    sandbox = FilesSandbox(files={"entities.txt": "acme.com\nbeta.io\n", "schema.json": "{}"})
    spawn = RecordingSpawn(outputs=[{}, {"headcount": 42}], invalid_outputs={0})
    result = await WIDE_RESEARCH_TOOL.handler(
        _context(sandbox, spawn, tmp_path),
        WIDE_RESEARCH_TOOL.input_model.model_validate(
            {
                "entities_file": "entities.txt",
                "prompt_template": "research {entity}",
                "output_schema_file": "schema.json",
            }
        ),
    )
    rows = json.loads(sandbox.writes["wide_research.json"])["rows"]
    assert rows[0]["entity"] == "acme.com"
    assert rows[0]["result"] is None
    assert "is not JSON" in rows[0]["error"]
    assert len(rows[0]["error"]) <= WIDE_RESEARCH_ERROR_MAX_CHARS
    assert rows[1] == {"entity": "beta.io", "result": {"headcount": 42}, "error": ""}
    result_payload = json.loads(result.content[0].text)
    assert result_payload["output_file"] == "wide_research.json"
    assert result_payload["rows"] == rows


async def test_wide_research_caps_the_entity_count(tmp_path: Path) -> None:
    too_many = "\n".join(f"site{i}.com" for i in range(MAX_WIDE_RESEARCH_ENTITIES + 1))
    sandbox = FilesSandbox(files={"entities.txt": too_many, "schema.json": ""})
    with pytest.raises(ValueError, match="at most"):
        await WIDE_RESEARCH_TOOL.handler(
            _context(sandbox, RecordingSpawn(), tmp_path),
            WIDE_RESEARCH_TOOL.input_model.model_validate(
                {
                    "entities_file": "entities.txt",
                    "prompt_template": "research {entity}",
                    "output_schema_file": "schema.json",
                }
            ),
        )
