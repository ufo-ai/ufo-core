import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import selfhost_ext_repl as repl
from selfhost_ext_repl import JsReplInput, XlsxReplInput

from selfhost.blob import FilesystemBlobStore
from selfhost.sandbox.session import ExecResult
from selfhost.schema.records import Agent, Turn
from selfhost.tools.context import SpawnResult, ToolContext


@dataclass
class FakeSandbox:
    """Scripts the sandbox for the REPL handlers: write_file/file_exists track a file dict, `cat`
    reads it back (the accumulate read-back), and `node`/`python3` return canned results — so the
    accumulate-then-run flow is exercised without a real container."""

    files: dict[str, bytes] = field(default_factory=dict)
    node_result: ExecResult = field(
        default_factory=lambda: ExecResult(stdout="ran", stderr="", exit_code=0)
    )
    python_result: ExecResult = field(
        default_factory=lambda: ExecResult(stdout="{}", stderr="", exit_code=0)
    )
    commands: list[str] = field(default_factory=list)

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        self.commands.append(command)
        head, path = command.split(" ", 1)
        if head == "cat":
            return ExecResult(
                stdout=self.files.get(shlex.split(path)[0], b"").decode(), stderr="", exit_code=0
            )
        if head == "node":
            return self.node_result
        if head == "python3":
            return self.python_result
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def file_exists(self, path: str) -> bool:
        return path in self.files

    async def write_file(self, path: str, content: bytes) -> None:
        self.files[path] = content


async def _unavailable_spawn(profile: str, payload: dict, background: bool = False) -> SpawnResult:
    raise AssertionError("repl tools must not spawn")


@dataclass
class _StubMemory:
    async def recall(self, query: str, subjects: frozenset[str], limit: int) -> tuple:
        return ()

    async def commit(self, write: object) -> None:
        return None


def _context(sandbox: FakeSandbox, tmp_path: Path) -> ToolContext:
    turn = Turn(
        id=uuid4(),
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=0,
        status="running",
        inbound="hi",
    )
    return ToolContext(
        sandbox=sandbox,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        memory=_StubMemory(),
        member_id=None,
        artifact_token_secret="",
    )


def test_manifest_declares_both_repls_with_verbatim_descriptions() -> None:
    manifest = repl.manifest()
    tools = {tool.name: tool for tool in manifest.tools}
    assert set(tools) == {"js_repl", "xlsx_repl"}
    assert tools["js_repl"].description.startswith("Persistent Node.js REPL for Playwright")
    assert "openpyxl" in tools["xlsx_repl"].description
    assert "code" in tools["js_repl"].input_model.model_json_schema()["properties"]


async def test_js_repl_first_call_writes_fresh_and_runs_node(tmp_path: Path) -> None:
    sandbox = FakeSandbox(node_result=ExecResult(stdout="hello", stderr="", exit_code=0))
    ctx = _context(sandbox, tmp_path)
    result = await repl.js_repl(ctx, JsReplInput(code="console.log('hi')", user_description="d"))
    assert sandbox.files[repl.JS_REPL_PATH] == b"console.log('hi')\n"
    assert any(command.startswith("node ") for command in sandbox.commands)
    assert json.loads(result.content[0].text)["stdout"] == "hello"
    assert result.is_error is False


async def test_js_repl_accumulates_state_across_calls(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    await repl.js_repl(ctx, JsReplInput(code="let x = 1", user_description="d"))
    await repl.js_repl(ctx, JsReplInput(code="console.log(x)", user_description="d"))
    assert sandbox.files[repl.JS_REPL_PATH] == b"let x = 1\nconsole.log(x)\n"


async def test_js_repl_reset_overwrites_state(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    await repl.js_repl(ctx, JsReplInput(code="let x = 1", user_description="d"))
    await repl.js_repl(ctx, JsReplInput(code="let y = 2", reset=True, user_description="d"))
    assert sandbox.files[repl.JS_REPL_PATH] == b"let y = 2\n"


async def test_xlsx_repl_wraps_accumulated_code_with_the_result_footer(tmp_path: Path) -> None:
    sandbox = FakeSandbox(python_result=ExecResult(stdout='{"a": 1}', stderr="", exit_code=0))
    ctx = _context(sandbox, tmp_path)
    result = await repl.xlsx_repl(ctx, XlsxReplInput(code="result = {'a': 1}"))
    run_file = sandbox.files[repl.XLSX_RUN_PATH].decode()
    assert run_file.startswith("result = {'a': 1}\n")
    assert "_json.dumps(result, default=str)" in run_file
    assert any(command.startswith("python3 ") for command in sandbox.commands)
    assert json.loads(result.content[0].text)["stdout"] == '{"a": 1}'


async def test_nonzero_exit_is_flagged_as_error(tmp_path: Path) -> None:
    sandbox = FakeSandbox(node_result=ExecResult(stdout="", stderr="boom", exit_code=1))
    ctx = _context(sandbox, tmp_path)
    result = await repl.js_repl(ctx, JsReplInput(code="throw new Error()", user_description="d"))
    assert result.is_error is True
    assert json.loads(result.content[0].text)["stderr"] == "boom"
