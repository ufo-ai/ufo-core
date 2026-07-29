import json
import shlex
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import ufo_ext_documents.manifest as documents
import ufo_ext_repl.manifest as repl
from ufo_ext_repl.manifest import JsReplInput, XlsxReplInput

from ufo.blob import FilesystemBlobStore
from ufo.ext.loader import skill_registry
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import (
    ExecResult,
    ProxyEndpoint,
    SandboxSession,
    SandboxSpec,
)
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.tools.context import SpawnResult, ToolContext

TOOL_NARRATION = "working through the numbers"


@dataclass
class FakeSandbox:
    """Scripts the sandbox for the REPL handlers: write_file/file_exists track a file dict, `cat`
    reads it back (the accumulate read-back), `rm` deletes, and `node`/`python3` return canned
    results — `node` also drops `node_emit` into the emit file when set, standing in for a run
    whose code called emitImage — so the accumulate-then-run flow is exercised without a real
    container."""

    files: dict[str, bytes] = field(default_factory=dict)
    node_result: ExecResult = field(
        default_factory=lambda: ExecResult(stdout="ran", stderr="", exit_code=0)
    )
    node_emit: bytes | None = None
    python_result: ExecResult = field(
        default_factory=lambda: ExecResult(stdout="{}", stderr="", exit_code=0)
    )
    link_result: ExecResult = field(
        default_factory=lambda: ExecResult(stdout="", stderr="", exit_code=0)
    )
    commands: list[str] = field(default_factory=list)

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        self.commands.append(command)
        head, path = command.split(" ", 1)
        if head == "set":
            return self.link_result
        if head == "cat":
            return ExecResult(
                stdout=self.files.get(shlex.split(path)[0], b"").decode(), stderr="", exit_code=0
            )
        if head == "rm":
            self.files.pop(shlex.split(path)[-1], None)
            return ExecResult(stdout="", stderr="", exit_code=0)
        if head == "node":
            if self.node_emit is not None:
                self.files[repl.JS_EMIT_PATH] = self.node_emit
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


def _context(sandbox: FakeSandbox | SandboxSession, tmp_path: Path) -> ToolContext:
    turn = Turn(
        id=uuid4(),
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=0,
        status="running",
        inbound="hi",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    return ToolContext(
        sandbox=sandbox,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
    )


def test_manifest_declares_both_repls_with_verbatim_descriptions() -> None:
    manifest = repl.manifest()
    tools = {tool.name: tool for tool in manifest.tools}
    assert set(tools) == {"js_repl", "xlsx_repl"}
    assert tools["js_repl"].description.startswith("Persistent Node.js REPL for Playwright")
    assert "openpyxl" in tools["xlsx_repl"].description
    assert "code" in tools["js_repl"].input_model.model_json_schema()["properties"]
    assert manifest.sandbox_internet is True


def test_data_skills_parse_and_index() -> None:
    index = dict(skill_registry((repl.manifest(),)).index())
    for name in (
        "data-exploration",
        "data-sql-queries",
        "data-statistical-analysis",
        "data-validation",
        "data-visualization",
    ):
        assert name in index


def test_data_visualization_pulls_design_foundations() -> None:
    registry = skill_registry((documents.manifest(), repl.manifest()))
    assert [entry.skill.name for entry in registry.closure("data-visualization")] == [
        "data-visualization",
        "design-foundations",
    ]


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


async def test_js_repl_discards_failed_code(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    await repl.js_repl(ctx, JsReplInput(code="const x = 1", user_description="d"))
    sandbox.node_result = ExecResult(stdout="", stderr="boom", exit_code=1)
    await repl.js_repl(ctx, JsReplInput(code="const x = broken", user_description="d"))
    sandbox.node_result = ExecResult(stdout="2", stderr="", exit_code=0)
    await repl.js_repl(ctx, JsReplInput(code="const y = 2", user_description="d"))
    assert sandbox.files[repl.JS_REPL_PATH] == b"const x = 1\nconst y = 2\n"
    run_file = sandbox.files[repl.JS_RUN_PATH].decode()
    assert "broken" not in run_file


async def test_js_repl_failed_reset_leaves_the_repl_empty(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    await repl.js_repl(ctx, JsReplInput(code="const a = 1", user_description="d"))
    sandbox.node_result = ExecResult(stdout="", stderr="boom", exit_code=1)
    await repl.js_repl(ctx, JsReplInput(code="const b = broken", reset=True, user_description="d"))
    sandbox.node_result = ExecResult(stdout="", stderr="", exit_code=0)
    await repl.js_repl(ctx, JsReplInput(code="const c = 3", user_description="d"))
    assert sandbox.files[repl.JS_REPL_PATH] == b"const c = 3\n"


async def test_xlsx_repl_discards_failed_code(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    await repl.xlsx_repl(ctx, XlsxReplInput(user_description=TOOL_NARRATION, code="a = 1"))
    sandbox.python_result = ExecResult(stdout="", stderr="boom", exit_code=1)
    await repl.xlsx_repl(ctx, XlsxReplInput(user_description=TOOL_NARRATION, code="a = broken"))
    sandbox.python_result = ExecResult(stdout="{}", stderr="", exit_code=0)
    await repl.xlsx_repl(ctx, XlsxReplInput(user_description=TOOL_NARRATION, code="b = 2"))
    assert sandbox.files[repl.XLSX_REPL_PATH] == b"a = 1\nb = 2\n"


async def test_js_repl_reset_overwrites_state(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    await repl.js_repl(ctx, JsReplInput(code="let x = 1", user_description="d"))
    await repl.js_repl(ctx, JsReplInput(code="let y = 2", reset=True, user_description="d"))
    assert sandbox.files[repl.JS_REPL_PATH] == b"let y = 2\n"


async def test_js_repl_composes_prelude_and_accumulated_code_into_the_run_file(
    tmp_path: Path,
) -> None:
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    await repl.js_repl(ctx, JsReplInput(code="let x = 1", user_description="d"))
    await repl.js_repl(ctx, JsReplInput(code="console.log(x)", user_description="d"))
    run_file = sandbox.files[repl.JS_RUN_PATH].decode()
    assert run_file == repl.JS_EMIT_PRELUDE + "let x = 1\nconsole.log(x)\n"
    assert any(command == f"node {repl.JS_RUN_PATH}" for command in sandbox.commands)


async def test_js_repl_returns_emitted_images_after_the_text_result(tmp_path: Path) -> None:
    emitted = (
        json.dumps({"media_type": "image/jpeg", "data": "anBn"})
        + "\n"
        + json.dumps({"media_type": "image/png", "data": "cG5n"})
        + "\n"
    )
    sandbox = FakeSandbox(node_emit=emitted.encode())
    ctx = _context(sandbox, tmp_path)
    result = await repl.js_repl(ctx, JsReplInput(code="emitImage(shot)", user_description="d"))
    assert json.loads(result.content[0].text)["stdout"] == "ran"
    assert [(image.media_type, image.data) for image in result.content[1:]] == [
        ("image/jpeg", "anBn"),
        ("image/png", "cG5n"),
    ]


async def test_js_repl_keeps_only_the_last_five_emitted_images(tmp_path: Path) -> None:
    lines = "".join(
        json.dumps({"media_type": "image/png", "data": f"aW1n{index}"}) + "\n" for index in range(7)
    )
    sandbox = FakeSandbox(node_emit=lines.encode())
    ctx = _context(sandbox, tmp_path)
    result = await repl.js_repl(ctx, JsReplInput(code="emitImage(shot)", user_description="d"))
    assert [image.data for image in result.content[1:]] == [f"aW1n{index}" for index in range(2, 7)]


async def test_js_repl_drops_a_torn_emit_line(tmp_path: Path) -> None:
    emitted = json.dumps({"media_type": "image/png", "data": "b2s="}) + '\n{"media_type": "image/'
    sandbox = FakeSandbox(node_emit=emitted.encode())
    ctx = _context(sandbox, tmp_path)
    result = await repl.js_repl(ctx, JsReplInput(code="emitImage(shot)", user_description="d"))
    assert result.is_error is False
    assert [image.data for image in result.content[1:]] == ["b2s="]


async def test_js_repl_clears_stale_emits_and_stays_text_only(tmp_path: Path) -> None:
    stale = b'{"media_type": "image/png", "data": "old"}\n'
    sandbox = FakeSandbox(files={repl.JS_EMIT_PATH: stale})
    ctx = _context(sandbox, tmp_path)
    result = await repl.js_repl(ctx, JsReplInput(code="console.log(1)", user_description="d"))
    assert len(result.content) == 1
    assert repl.JS_EMIT_PATH not in sandbox.files


async def test_xlsx_repl_wraps_accumulated_code_with_the_result_footer(tmp_path: Path) -> None:
    sandbox = FakeSandbox(python_result=ExecResult(stdout='{"a": 1}', stderr="", exit_code=0))
    ctx = _context(sandbox, tmp_path)
    result = await repl.xlsx_repl(
        ctx, XlsxReplInput(user_description=TOOL_NARRATION, code="result = {'a': 1}")
    )
    run_file = sandbox.files[repl.XLSX_RUN_PATH].decode()
    assert run_file.startswith("result = {'a': 1}\n")
    assert "_json.dumps(result, default=str)" in run_file
    assert any(command.startswith("python3 ") for command in sandbox.commands)
    assert json.loads(result.content[0].text)["stdout"] == '{"a": 1}'


async def test_js_repl_emits_images_through_the_real_local_carrier(tmp_path: Path) -> None:
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref="ufo-sandbox:latest",
            workspace_host_path=str(tmp_path / "workspace"),
            proxy=ProxyEndpoint(port=9999, ca_cert="CA-PEM-BYTES"),
            run_token="run-token-abc",
        )
    )
    ctx = _context(SandboxSession(carrier=carrier, handle=handle), tmp_path)
    state_dir = tmp_path / "workspace" / ".repl"
    state_dir.mkdir(parents=True)
    (state_dir / "node_modules").symlink_to("/nonexistent")
    code = (
        "await Promise.resolve();\n"
        'emitImage(Buffer.from([1, 2, 3]), "image/jpeg");\n'
        'console.log("emitted");'
    )
    result = await repl.js_repl(ctx, JsReplInput(code=code, user_description="d"))
    assert result.is_error is False
    assert json.loads(result.content[0].text)["stdout"] == "emitted\n"
    assert [(image.media_type, image.data) for image in result.content[1:]] == [
        ("image/jpeg", "AQID")
    ]
    assert (tmp_path / "workspace" / ".repl" / "node_modules" / "npm").is_symlink()
    resolved = await repl.js_repl(
        ctx,
        JsReplInput(code="console.log(import.meta.resolve('npm'));", user_description="d"),
    )
    assert resolved.is_error is False
    assert "/node_modules/npm/" in json.loads(resolved.content[0].text)["stdout"]


async def test_global_modules_link_resolves_a_package_only_in_a_secondary_root(
    tmp_path: Path,
) -> None:
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref="ufo-sandbox:latest",
            workspace_host_path=str(tmp_path / "workspace"),
            proxy=ProxyEndpoint(port=9999, ca_cert="CA-PEM-BYTES"),
            run_token="run-token-abc",
        )
    )
    session = SandboxSession(carrier=carrier, handle=handle)
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    for root, marker in ((primary, "from-primary"), (secondary, "from-secondary")):
        (root / "dup").mkdir(parents=True)
        (root / "dup" / "package.json").write_text('{"name": "dup", "main": "index.js"}')
        (root / "dup" / "index.js").write_text(f"module.exports = '{marker}';")
    (secondary / "solo").mkdir()
    (secondary / "solo" / "package.json").write_text('{"name": "solo", "main": "index.js"}')
    (secondary / "solo" / "index.js").write_text("module.exports = 'from-solo';")
    await session.bash(repl.global_modules_link((str(primary), str(secondary))))
    ctx = _context(session, tmp_path)
    code = (
        "const solo = await import('solo');\n"
        "const dup = await import('dup');\n"
        "console.log(solo.default, dup.default);"
    )
    result = await repl.js_repl(ctx, JsReplInput(code=code, user_description="d"))
    assert result.is_error is False
    assert json.loads(result.content[0].text)["stdout"] == "from-solo from-primary\n"


async def test_js_repl_raises_when_global_module_linking_fails(tmp_path: Path) -> None:
    sandbox = FakeSandbox(
        link_result=ExecResult(stdout="", stderr="ln: permission denied", exit_code=1)
    )
    ctx = _context(sandbox, tmp_path)
    with pytest.raises(OSError, match="permission denied"):
        await repl.js_repl(ctx, JsReplInput(code="1", user_description="d"))


async def test_nonzero_exit_is_flagged_as_error(tmp_path: Path) -> None:
    sandbox = FakeSandbox(node_result=ExecResult(stdout="", stderr="boom", exit_code=1))
    ctx = _context(sandbox, tmp_path)
    result = await repl.js_repl(ctx, JsReplInput(code="throw new Error()", user_description="d"))
    assert result.is_error is True
    assert json.loads(result.content[0].text)["stderr"] == "boom"
