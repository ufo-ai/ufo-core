from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import pytest

from selfhost.blob import FilesystemBlobStore
from selfhost.sandbox.session import ExecResult
from selfhost.schema.records import Agent, Turn
from selfhost.tools.builtins import BUILTIN_TOOLS
from selfhost.tools.context import SpawnResult, ToolContext
from selfhost.tools.registry import ToolRegistry

REGISTRY = ToolRegistry(BUILTIN_TOOLS)


@dataclass
class FakeSandbox:
    """A stand-in for the carrier-backed session: shell output is scripted, files live in a dict.

    It exists so the builtin handlers can be driven without Docker; tests assert the handlers'
    behavior (combined output, the EOF marker, the read-before-edit guard, unique replace), never
    the stand-in itself."""

    files: dict[str, bytes] = field(default_factory=dict)
    bash_result: ExecResult = field(
        default_factory=lambda: ExecResult(stdout="", stderr="", exit_code=0)
    )

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        return self.bash_result

    async def read_file(self, path: str) -> bytes:
        try:
            return self.files[path]
        except KeyError as error:
            raise FileNotFoundError(path) from error

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


def make_context(sandbox: FakeSandbox, tmp_path: Path) -> ToolContext:
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
        "spawn_subagent",
        "memory_search",
        "memory_update",
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


async def test_read_windows_and_records_path(tmp_path: Path) -> None:
    sandbox = FakeSandbox(files={"notes.txt": b"a\nb\nc\nd"})
    ctx = make_context(sandbox, tmp_path)
    result = await run("read", ctx, path="notes.txt", offset=1, limit=2)
    assert result.content[0].text == "b\nc"
    assert "notes.txt" in ctx.read_paths


async def test_read_offset_past_eof_returns_marker(tmp_path: Path) -> None:
    sandbox = FakeSandbox(files={"notes.txt": b"a\nb\nc"})
    ctx = make_context(sandbox, tmp_path)
    result = await run("read", ctx, path="notes.txt", offset=5)
    assert result.content[0].text == "(no lines at offset 5; file has 3 lines)"


async def test_write_persists_encoded_content(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    ctx = make_context(sandbox, tmp_path)
    await run("write", ctx, path="out.txt", content="hello")
    assert sandbox.files["out.txt"] == b"hello"


async def test_edit_requires_read_before_write(tmp_path: Path) -> None:
    sandbox = FakeSandbox(files={"code.py": b"x = 1"})
    ctx = make_context(sandbox, tmp_path)
    with pytest.raises(ValueError, match="must be read before it is edited"):
        await run("edit", ctx, path="code.py", old_string="x = 1", new_string="x = 2")


async def test_edit_replaces_unique_string(tmp_path: Path) -> None:
    sandbox = FakeSandbox(files={"code.py": b"x = 1\ny = 2"})
    ctx = make_context(sandbox, tmp_path)
    await run("read", ctx, path="code.py")
    await run("edit", ctx, path="code.py", old_string="x = 1", new_string="x = 9")
    assert sandbox.files["code.py"] == b"x = 9\ny = 2"


async def test_edit_rejects_non_unique_string(tmp_path: Path) -> None:
    sandbox = FakeSandbox(files={"code.py": b"n = n"})
    ctx = make_context(sandbox, tmp_path)
    await run("read", ctx, path="code.py")
    with pytest.raises(ValueError, match="not unique"):
        await run("edit", ctx, path="code.py", old_string="n", new_string="m")


async def test_edit_rejects_missing_string(tmp_path: Path) -> None:
    sandbox = FakeSandbox(files={"code.py": b"x = 1"})
    ctx = make_context(sandbox, tmp_path)
    await run("read", ctx, path="code.py")
    with pytest.raises(ValueError, match="not found"):
        await run("edit", ctx, path="code.py", old_string="zzz", new_string="q")
