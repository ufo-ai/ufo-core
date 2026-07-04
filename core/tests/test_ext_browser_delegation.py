"""The browser delegation tools' proof: browser_task hands one objective to the `browser` profile
through `ctx.spawn`, and wide_browse reads an entities file, fans a bounded pool of `browser`
children over it, and writes the collected rows to a workspace JSON file. A RecordingSpawn stands
in for the Subagents workflow (a dependency, never asserted); the tests assert the tools' own
marshalling — the payloads spawned, the entity dedupe and cap, and the workspace write."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel
from selfhost_ext_browser.delegation import DELEGATION_TOOLS, MAX_WIDE_BROWSE_ENTITIES

from selfhost.blob import FilesystemBlobStore
from selfhost.sandbox.session import ExecResult
from selfhost.schema.records import Agent, Turn
from selfhost.tools.context import SpawnResult, ToolContext


class _Result(BaseModel):
    result: str


@dataclass
class RecordingSpawn:
    spawned: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def __call__(
        self, profile: str, payload: dict[str, object], background: bool = False
    ) -> SpawnResult:
        self.spawned.append((profile, payload))
        name = payload.get("task_name")
        return SpawnResult(turn_id=uuid4(), output=_Result(result=f"did {name}"))


@dataclass
class FilesSandbox:
    """Answers `cat <path>` from a scripted file map and captures write_file calls."""

    files: dict[str, str] = field(default_factory=dict)
    writes: dict[str, bytes] = field(default_factory=dict)

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        for path, content in self.files.items():
            if json.dumps(path) in command:
                return ExecResult(stdout=content, stderr="", exit_code=0)
        return ExecResult(stdout="", stderr="not found", exit_code=1)

    async def write_file(self, path: str, content: bytes) -> None:
        self.writes[path] = content


def _context(sandbox: FilesSandbox, spawn: RecordingSpawn, tmp_path: Path) -> ToolContext:
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
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=spawn,
        memory=None,
        member_id=None,
        artifact_token_secret="",
    )


def _tool(name: str):
    return next(tool for tool in DELEGATION_TOOLS if tool.name == name)


async def test_browser_task_spawns_the_browser_profile_with_the_objective(tmp_path: Path) -> None:
    spawn = RecordingSpawn()
    tool = _tool("browser_task")
    result = await tool.handler(
        _context(FilesSandbox(), spawn, tmp_path),
        tool.input_model.model_validate(
            {
                "url": "https://jobs.example.com",
                "task": "list open roles",
                "task_name": "jobs",
                "user_description": "browse jobs",
            }
        ),
    )
    assert spawn.spawned == [
        (
            "browser",
            {"task": "list open roles", "url": "https://jobs.example.com", "task_name": "jobs"},
        )
    ]
    assert json.loads(result.content[0].text) == {"result": "did jobs"}


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
                "user_description": "batch",
            }
        ),
    )
    entities = [payload["task_name"] for _, payload in spawn.spawned]
    assert entities == ["acme.com", "beta.io"]
    assert all('{"price": "number"}' in payload["task"] for _, payload in spawn.spawned)
    assert "wide_browse.json" in sandbox.writes
    rows = json.loads(sandbox.writes["wide_browse.json"])
    assert [row["entity"] for row in rows] == ["acme.com", "beta.io"]
    assert json.loads(result.content[0].text)["output_file"] == "wide_browse.json"


async def test_wide_browse_caps_the_entity_count(tmp_path: Path) -> None:
    too_many = "\n".join(f"site{i}.com" for i in range(MAX_WIDE_BROWSE_ENTITIES + 1))
    sandbox = FilesSandbox(files={"entities.txt": too_many, "schema.json": ""})
    tool = _tool("wide_browse")
    import pytest

    with pytest.raises(ValueError, match="at most"):
        await tool.handler(
            _context(sandbox, RecordingSpawn(), tmp_path),
            tool.input_model.model_validate(
                {
                    "entities_file": "entities.txt",
                    "prompt_template": "get {entity}",
                    "output_schema_file": "schema.json",
                    "user_description": "batch",
                }
            ),
        )
