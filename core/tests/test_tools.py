import json
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from selfhost.blob import FilesystemBlobStore
from selfhost.sandbox.session import ExecResult
from selfhost.schema.records import Agent, Turn
from selfhost.tools.builtins import BUILTIN_TOOLS
from selfhost.tools.context import SpawnResult, ToolContext
from selfhost.tools.registry import ToolRegistry

REGISTRY = ToolRegistry(BUILTIN_TOOLS)
ARTIFACT_SECRET = "tools-test-secret"


@dataclass
class FakeSandbox:
    """A stand-in for the carrier-backed session for the handler logic that never reaches the
    sandbox: `bash` output is scripted, and `run_sbxfs` raises so a guard test proves the read
    guard short-circuits before any file op runs. The sbxfs-backed behavior (windowing, edits,
    streamed share) is proven against a real container in test_file_tools, never against this
    stand-in."""

    bash_result: ExecResult = field(
        default_factory=lambda: ExecResult(stdout="", stderr="", exit_code=0)
    )
    files: dict[str, bytes] = field(default_factory=dict)

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        return self.bash_result

    async def run_sbxfs(self, op: str, args: dict[str, object]) -> dict[str, object]:
        raise AssertionError(f"run_sbxfs({op}) must not run once a guard has rejected the call")

    async def write_file(self, path: str, content: bytes) -> None:
        self.files[path] = content


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in this context")


@dataclass
class StubMemory:
    """Stand-in for the memory service: these tests drive the file/shell builtins, not the memory
    tools, so recall/commit are never asserted here (their own tests cover them)."""

    async def recall(self, query: str, subjects: frozenset[str], limit: int) -> tuple:
        return ()

    async def commit(self, write: object) -> None:
        return None


def make_context(
    sandbox: FakeSandbox, tmp_path: Path, artifact_secret: str = ARTIFACT_SECRET
) -> ToolContext:
    workspace_id, conversation_id, agent_id = uuid4(), uuid4(), uuid4()
    turn = Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        agent_id=agent_id,
        seq=0,
        status="running",
        inbound="hello",
    )
    return ToolContext(
        sandbox=sandbox,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=turn,
        agent=Agent(prompt="be terse", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        memory=StubMemory(),
        member_id=None,
        artifact_token_secret=artifact_secret,
    )


async def run(name: str, ctx: ToolContext, **args: object):
    tool = REGISTRY.get(name)
    return await tool.handler(ctx, tool.input_model.model_validate(args))


def test_registry_rejects_duplicate_names() -> None:
    bash = REGISTRY.get("bash")
    with pytest.raises(ValueError, match="duplicate tool names: bash"):
        ToolRegistry((bash, bash))


def test_registry_get_unknown_raises() -> None:
    with pytest.raises(KeyError, match="unknown tool: nope"):
        REGISTRY.get("nope")


def test_registry_get_returns_named_tool() -> None:
    assert REGISTRY.get("edit").name == "edit"


def test_registry_schemas_cover_every_tool() -> None:
    schemas = REGISTRY.schemas()
    assert {schema.name for schema in schemas} == {
        "bash",
        "read",
        "write",
        "edit",
        "share_file",
        "spawn_subagent",
        "memory_search",
        "memory_update",
        "load_sessions",
        "ask_user",
        "load_skill",
        "connect_account",
    }
    bash = next(schema for schema in schemas if schema.name == "bash")
    assert "command" in bash.input_schema["properties"]


async def test_bash_combines_output_and_flags_nonzero_exit(tmp_path: Path) -> None:
    sandbox = FakeSandbox(bash_result=ExecResult(stdout="out", stderr="err", exit_code=1))
    ctx = make_context(sandbox, tmp_path)
    result = await run("bash", ctx, command="do it")
    assert result.content[0].text == "outerr"
    assert result.is_error is True


async def test_bash_zero_exit_is_not_error(tmp_path: Path) -> None:
    sandbox = FakeSandbox(bash_result=ExecResult(stdout="ok", stderr="", exit_code=0))
    ctx = make_context(sandbox, tmp_path)
    result = await run("bash", ctx, command="echo ok")
    assert result.is_error is False
    assert result.content[0].text == "ok"


async def test_edit_requires_read_before_write(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path)
    with pytest.raises(ValueError, match="must be read before it is edited"):
        await run(
            "edit",
            ctx,
            file_path="code.py",
            edits=[{"old_string": "x = 1", "new_string": "x = 2"}],
        )


async def test_share_file_without_a_secret_fails_loud_and_writes_nothing(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path, artifact_secret="")
    with pytest.raises(RuntimeError, match="not configured"):
        await run("share_file", ctx, file_path="report.txt")
    assert not (tmp_path / "artifacts").exists()


async def test_ask_user_returns_the_structured_question_and_the_end_turn_directive(
    tmp_path: Path,
) -> None:
    ctx = make_context(FakeSandbox(), tmp_path)
    result = await run(
        "ask_user",
        ctx,
        title="Scope",
        questions=[{"question": "Which environment?", "header": "Deploy"}],
    )
    assert result.is_error is False
    text = result.content[0].text
    assert "arrives as the next message" in text
    payload = json.loads(text.split("\n", 1)[1])
    assert payload["awaiting"] == "question"
    assert payload["title"] == "Scope"
    assert payload["questions"] == [{"question": "Which environment?", "header": "Deploy"}]


async def test_ask_user_folds_confirmation_as_a_question_with_options(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path)
    result = await run(
        "ask_user",
        ctx,
        title="Confirm send",
        questions=[
            {
                "question": "Send the email to the whole team?",
                "options": [{"label": "Send"}, {"label": "Cancel"}],
            }
        ],
    )
    payload = json.loads(result.content[0].text.split("\n", 1)[1])
    assert [option["label"] for option in payload["questions"][0]["options"]] == [
        "Send",
        "Cancel",
    ]


async def test_ask_user_requires_at_least_one_question(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path)
    with pytest.raises(ValidationError):
        await run("ask_user", ctx, title="Empty", questions=[])


async def _load_skill(ctx: ToolContext, name: str):
    tool = REGISTRY.get("load_skill")
    return await tool.handler(ctx, tool.input_model.model_validate({"name": name}))


async def test_load_skill_mounts_files_under_the_workspace_and_returns_instructions(
    tmp_path: Path,
) -> None:
    sandbox = FakeSandbox()
    ctx = make_context(sandbox, tmp_path)
    result = await _load_skill(ctx, "memory")
    assert result.is_error is False
    assert "Loaded skill(s): memory" in result.content[0].text
    assert "Remembering and recalling" in result.content[0].text
    mounted = sandbox.files["/workspace/.skills/memory/SKILL.md"]
    assert b"name: memory" in mounted


async def test_load_skill_unknown_name_fails_loud(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path)
    with pytest.raises(ValueError, match="unknown skill 'nope'"):
        await _load_skill(ctx, "nope")
