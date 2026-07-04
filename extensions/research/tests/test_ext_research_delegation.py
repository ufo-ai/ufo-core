"""The research delegation tool's proof: `wide_research` reads an entities file, fans a bounded pool
of `research` children over it through `ctx.spawn`, and writes the collected rows to a workspace
JSON file. A RecordingSpawn stands in for the Subagents workflow (a dependency, never asserted); the
tests assert the tool's own marshalling — the objectives spawned, the entity dedupe and cap, the
write."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import BaseModel
from selfhost_ext_research.delegation import MAX_WIDE_RESEARCH_ENTITIES, WIDE_RESEARCH_TOOL

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
        return SpawnResult(turn_id=uuid4(), output=_Result(result=f"did {payload['objective']}"))


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
        member_id=None,
        artifact_token_secret="",
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
    result = await WIDE_RESEARCH_TOOL.handler(
        _context(sandbox, spawn, tmp_path),
        WIDE_RESEARCH_TOOL.input_model.model_validate(
            {
                "entities_file": "entities.txt",
                "prompt_template": "research {entity}",
                "output_schema_file": "schema.json",
                "user_description": "batch",
            }
        ),
    )
    assert {profile for profile, _ in spawn.spawned} == {"research"}
    objectives = [payload["objective"] for _, payload in spawn.spawned]
    assert objectives[0].startswith("research acme.com")
    assert objectives[1].startswith("research beta.io")
    assert all('{"headcount": "number"}' in objective for objective in objectives)
    assert "wide_research.json" in sandbox.writes
    rows = json.loads(sandbox.writes["wide_research.json"])
    assert [row["entity"] for row in rows] == ["acme.com", "beta.io"]
    assert json.loads(result.content[0].text)["output_file"] == "wide_research.json"


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
                    "user_description": "batch",
                }
            ),
        )
