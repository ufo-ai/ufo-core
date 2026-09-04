import asyncio
import json
import shlex
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import ufo_ext_repl.manifest as repl
from ufo_ext_repl.manifest import JsReplInput, XlsxReplInput

import ufo.runtime.tools.tasks as tasks
from ufo.blob import FilesystemBlobStore
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import (
    ExecResult,
    ProxyEndpoint,
    SandboxSession,
    SandboxSpec,
)
from ufo.host.ext.loader import skill_registry
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.tools.tasks import MAX_COMMAND_TIMEOUT_MS
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience

TOOL_NARRATION = "working through the numbers"


@dataclass
class FakeSandbox:
    """Scripts the sandbox for the REPL handlers: write_file/file_exists track a file dict, `cat`
    reads it back (the accumulate read-back), `rm` deletes, and `node`/`python3` return canned
    results — `node` also drops `node_emit` into the emit file the run file names, standing in for a
    run whose code called emitImage, and `late_emit` into the emit file of the call before it,
    standing in for the process of an expired call writing on past its result — so the
    accumulate-then-run flow is exercised without a real container.

    `bash_task` stands in for the task journal the interpreter is launched through, and `sh`
    answers its liveness probe with `probe_pid` — the supervisor that outlived an expired wait,
    empty for a sandbox that ran nothing."""

    files: dict[str, bytes] = field(default_factory=dict)
    probe_pid: str = ""
    timeouts: list[int | None] = field(default_factory=list)
    node_result: ExecResult = field(
        default_factory=lambda: ExecResult(stdout="ran", stderr="", exit_code=0)
    )
    node_emit: bytes | None = None
    late_emit: bytes | None = None
    emit_paths: list[str] = field(default_factory=list)
    python_result: ExecResult = field(
        default_factory=lambda: ExecResult(stdout="{}", stderr="", exit_code=0)
    )
    link_result: ExecResult = field(
        default_factory=lambda: ExecResult(stdout="", stderr="", exit_code=0)
    )
    state_read_result: ExecResult | None = None
    state_remove_result: ExecResult | None = None
    commands: list[str] = field(default_factory=list)
    runtime_root: str = ""

    async def runtime_path(self, relative: str) -> str:
        return f"{self.runtime_root}/{relative}" if self.runtime_root else relative

    async def runtime_display_path(self, relative: str) -> str:
        return f"$UFO_HOME/runs/test/{relative}"

    async def runtime_file_exists(self, relative: str) -> bool:
        return relative in self.files

    async def write_runtime_file(self, relative: str, content: bytes) -> None:
        self.files[relative] = content

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        self.commands.append(command)
        head, path = command.split(" ", 1)
        if head == "set":
            return self.link_result
        if head == "cat":
            if self.state_read_result is not None:
                return self.state_read_result
            return ExecResult(
                stdout=self.files.get(shlex.split(path)[0], b"").decode(), stderr="", exit_code=0
            )
        if head == "rm":
            if self.state_remove_result is not None:
                return self.state_remove_result
            self.files.pop(shlex.split(path)[-1], None)
            return ExecResult(stdout="", stderr="", exit_code=0)
        if head == "node":
            emit_path = self._emit_path()
            if self.node_emit is not None:
                self.files[emit_path] = self.node_emit
            if self.late_emit is not None and self.emit_paths:
                self.files[self.emit_paths[-1]] = self.late_emit
            self.emit_paths.append(emit_path)
            return self.node_result
        if head == "python3":
            return self.python_result
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def bash_task(
        self,
        command: str,
        base: str,
        *,
        detach: bool,
        model_authored: bool,
        timeout_s: int | None = None,
    ) -> ExecResult:
        self.timeouts.append(timeout_s)
        return await self.bash(command)

    async def sh(self, script: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        if tasks.TASK_PROBE in script:
            return ExecResult(stdout=self.probe_pid, stderr="", exit_code=0)
        self.timeouts.append(timeout_s)
        return await self.bash(args[-1])

    def _emit_path(self) -> str:
        run_file = self.files[repl.JS_RUN_PATH].decode()
        return json.loads(run_file.split("__ufoEmitResolve(", 1)[1].split(")", 1)[0])


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
    description = tools["js_repl"].description
    assert description.startswith("Persistent Node.js ES-module REPL")
    assert "standalone Node scripts do not use its linked modules" in description
    assert "close each browser in `finally`" in description
    assert (
        "`require` is unavailable"
        in tools["js_repl"].input_model.model_json_schema()["properties"]["code"]["description"]
    )
    assert "openpyxl" in tools["xlsx_repl"].description
    assert "code" in tools["js_repl"].input_model.model_json_schema()["properties"]
    assert manifest.sandbox_internet is True


def test_both_repls_take_a_capped_budget_and_state_the_replay_rule() -> None:
    """The model raises a REPL budget the way it raises bash's, to the same ceiling, and each
    description says what a call that does not exit 0 does to the state — unsaid, a retry replays
    the same starting point and buys the same failure again."""
    for tool in repl.manifest().tools:
        timeout = tool.input_model.model_json_schema()["properties"]["timeout"]
        assert str(MAX_COMMAND_TIMEOUT_MS) in timeout["description"]
        assert repl.REPLAY_SEMANTICS in tool.description


async def test_repl_timeout_is_milliseconds_capped_and_converted(tmp_path: Path) -> None:
    """The budget arrives in milliseconds like bash's and is capped before it converts to the
    carrier's seconds; an omitted one asks for no deadline, leaving the session's default budget."""
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    await repl.js_repl(ctx, JsReplInput(code="1", timeout=5000))
    await repl.js_repl(ctx, JsReplInput(code="2", timeout=9_000_000))
    await repl.js_repl(ctx, JsReplInput(code="3"))
    await repl.xlsx_repl(ctx, XlsxReplInput(code="a = 1", timeout=9_000_000))
    assert sandbox.timeouts == [5, 600, None, 600]


async def test_an_expired_cell_keeps_running_and_hands_back_its_handles(tmp_path: Path) -> None:
    """The budget is how long the caller waits, not how long the work may take: an interpreter still
    running when it expires is handed back as the task it now is, under the same handle names bash
    reports, and the result says the state did not advance."""
    sandbox = FakeSandbox(
        node_result=ExecResult(stdout="", stderr="", exit_code=124, timed_out_after_s=120),
        probe_pid="4321",
    )
    ctx = _context(sandbox, tmp_path)

    result = await repl.js_repl(ctx, JsReplInput(code="await hang()"))

    assert result.is_error is False
    text = result.content[0].text
    assert "did not complete within its 120s timeout" in text
    assert repl.STATE_UNCHANGED in text
    handles = json.loads(text.splitlines()[-1])
    assert handles["pid"] == "4321"
    assert set(handles) == {"task", "pid", "log", "exit_file", "watch", "stop"}
    assert repl.JS_REPL_PATH not in sandbox.files


async def test_an_expired_cell_the_sandbox_stopped_names_the_budget(tmp_path: Path) -> None:
    """A wait that expired with nothing left alive is the sandbox failing to run the code, and only
    that is the caller's error. It reports which deadline fired and that the request was capped —
    the exit code cannot say either, since code running `timeout` exits 124 the same way."""
    sandbox = FakeSandbox(
        python_result=ExecResult(stdout="", stderr="", exit_code=124, timed_out_after_s=600)
    )
    ctx = _context(sandbox, tmp_path)

    result = await repl.xlsx_repl(
        ctx,
        XlsxReplInput(code="load()", timeout=9_000_000),
    )

    assert result.is_error is True
    assert "600s" in result.content[0].text
    assert "9000s requested was capped" in result.content[0].text
    assert repl.STATE_UNCHANGED in result.content[0].text
    assert repl.XLSX_REPL_PATH not in sandbox.files


async def test_an_expired_cell_leaves_the_state_a_later_call_composes_onto(tmp_path: Path) -> None:
    """What the notice claims, held as behaviour: the expired call is discarded whole, so the next
    call composes onto the state before it rather than onto the code that never finished."""
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    await repl.js_repl(ctx, JsReplInput(code="const x = 1"))
    sandbox.node_result = ExecResult(stdout="", stderr="", exit_code=124, timed_out_after_s=120)
    await repl.js_repl(ctx, JsReplInput(code="await hang()"))
    sandbox.node_result = ExecResult(stdout="ok", stderr="", exit_code=0)
    await repl.js_repl(ctx, JsReplInput(code="const y = 2"))
    assert sandbox.files[repl.JS_REPL_PATH] == b"const x = 1\nconst y = 2\n"


async def test_a_failed_run_says_its_code_was_discarded(tmp_path: Path) -> None:
    """A failing run carries the replay rule with it: the code it ran is not committed, so an agent
    that re-sends the same block gets the same failure — which is how one broken block became three
    identical losses."""
    sandbox = FakeSandbox(node_result=ExecResult(stdout="", stderr="boom", exit_code=1))
    ctx = _context(sandbox, tmp_path)
    result = await repl.js_repl(ctx, JsReplInput(code="throw new Error()"))
    assert json.loads(result.content[0].text)["notice"] == repl.STATE_UNCHANGED


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


async def test_js_repl_accumulates_state_across_calls(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    await repl.js_repl(ctx, JsReplInput(code="let x = 1"))
    await repl.js_repl(ctx, JsReplInput(code="console.log(x)"))
    assert sandbox.files[repl.JS_REPL_PATH] == b"let x = 1\nconsole.log(x)\n"


async def test_js_repl_discards_failed_code(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    await repl.js_repl(ctx, JsReplInput(code="const x = 1"))
    sandbox.node_result = ExecResult(stdout="", stderr="boom", exit_code=1)
    await repl.js_repl(ctx, JsReplInput(code="const x = broken"))
    sandbox.node_result = ExecResult(stdout="2", stderr="", exit_code=0)
    await repl.js_repl(ctx, JsReplInput(code="const y = 2"))
    assert sandbox.files[repl.JS_REPL_PATH] == b"const x = 1\nconst y = 2\n"
    run_file = sandbox.files[repl.JS_RUN_PATH].decode()
    assert "broken" not in run_file


async def test_js_repl_failed_reset_leaves_the_repl_empty(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    await repl.js_repl(ctx, JsReplInput(code="const a = 1"))
    sandbox.node_result = ExecResult(stdout="", stderr="boom", exit_code=1)
    await repl.js_repl(ctx, JsReplInput(code="const b = broken", reset=True))
    sandbox.node_result = ExecResult(stdout="", stderr="", exit_code=0)
    await repl.js_repl(ctx, JsReplInput(code="const c = 3"))
    assert sandbox.files[repl.JS_REPL_PATH] == b"const c = 3\n"


async def test_a_state_read_that_fails_runs_nothing_and_keeps_the_session(
    tmp_path: Path,
) -> None:
    """The worst outcome this tool has. An unchecked `cat` returns empty stdout, so the candidate
    becomes this call's code alone — every variable the session built gone — and the run then
    succeeds and commits that as the whole state. Nothing may run past a failed read."""
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    await repl.js_repl(ctx, JsReplInput(code="const a = 1"))
    sandbox.state_read_result = ExecResult(stdout="", stderr="I/O error", exit_code=2)

    result = await repl.js_repl(ctx, JsReplInput(code="const b = 2"))

    assert result.is_error is True
    failure = json.loads(result.content[0].text)
    assert repl.JS_REPL_PATH in failure["operation"]
    assert failure["command"] == {"exit_code": 2, "stdout": "", "stderr": "I/O error"}
    assert sandbox.files[repl.JS_REPL_PATH] == b"const a = 1\n"
    assert "node" not in [command.split(" ", 1)[0] for command in sandbox.commands[-1:]]


async def test_a_reset_that_cannot_clear_the_state_says_so_rather_than_running(
    tmp_path: Path,
) -> None:
    """A reset whose `rm` failed leaves the old cells on disk. Running anyway composes onto state
    the caller asked to be rid of, and commits the result as if the reset had happened."""
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    await repl.xlsx_repl(ctx, XlsxReplInput(code="a = 1"))
    sandbox.state_remove_result = ExecResult(stdout="", stderr="read-only file system", exit_code=1)

    result = await repl.xlsx_repl(ctx, XlsxReplInput(code="b = 2", reset=True))

    assert result.is_error is True
    failure = json.loads(result.content[0].text)
    assert "clear" in failure["operation"]
    assert failure["command"]["stderr"] == "read-only file system"
    assert sandbox.files[repl.XLSX_REPL_PATH] == b"a = 1\n"


async def test_xlsx_repl_discards_failed_code(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    await repl.xlsx_repl(ctx, XlsxReplInput(code="a = 1"))
    sandbox.python_result = ExecResult(stdout="", stderr="boom", exit_code=1)
    await repl.xlsx_repl(ctx, XlsxReplInput(code="a = broken"))
    sandbox.python_result = ExecResult(stdout="{}", stderr="", exit_code=0)
    await repl.xlsx_repl(ctx, XlsxReplInput(code="b = 2"))
    assert sandbox.files[repl.XLSX_REPL_PATH] == b"a = 1\nb = 2\n"


async def test_js_repl_keeps_only_the_last_five_emitted_images(tmp_path: Path) -> None:
    lines = "".join(
        json.dumps({"media_type": "image/png", "data": f"aW1n{index}"}) + "\n" for index in range(7)
    )
    sandbox = FakeSandbox(node_emit=lines.encode())
    ctx = _context(sandbox, tmp_path)
    result = await repl.js_repl(ctx, JsReplInput(code="emitImage(shot)"))
    assert [image.data for image in result.content[1:]] == [f"aW1n{index}" for index in range(2, 7)]


async def test_js_repl_drops_a_torn_emit_line(tmp_path: Path) -> None:
    emitted = json.dumps({"media_type": "image/png", "data": "b2s="}) + '\n{"media_type": "image/'
    sandbox = FakeSandbox(node_emit=emitted.encode())
    ctx = _context(sandbox, tmp_path)
    result = await repl.js_repl(ctx, JsReplInput(code="emitImage(shot)"))
    assert result.is_error is False
    assert [image.data for image in result.content[1:]] == ["b2s="]


async def test_js_repl_leaves_an_earlier_calls_emits_out_of_a_text_only_result(
    tmp_path: Path,
) -> None:
    stale_path = repl.js_emit_relative("0ldca11")
    stale = b'{"media_type": "image/png", "data": "old"}\n'
    sandbox = FakeSandbox(files={stale_path: stale})
    ctx = _context(sandbox, tmp_path)
    result = await repl.js_repl(ctx, JsReplInput(code="console.log(1)"))
    assert len(result.content) == 1


async def test_js_repl_removes_the_emit_file_it_read(tmp_path: Path) -> None:
    emitted = json.dumps({"media_type": "image/png", "data": "cG5n"}) + "\n"
    sandbox = FakeSandbox(node_emit=emitted.encode())
    ctx = _context(sandbox, tmp_path)
    result = await repl.js_repl(ctx, JsReplInput(code="emitImage(shot)"))
    assert [image.data for image in result.content[1:]] == ["cG5n"]
    assert sandbox.emit_paths[-1] not in sandbox.files


async def test_a_js_cell_over_its_budget_survives_in_the_real_local_carrier(
    tmp_path: Path,
) -> None:
    """The whole point of the journal, against a real process group: the carrier ends the expired
    exec by killing the launcher's group, and the node process sits outside it, so the work goes
    on — the log grows after the tool answered and the exit code lands later. A cell that opened a
    browser keeps it, instead of losing the launch to the deadline."""
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
    ctx = _context(session, tmp_path)
    state_dir = Path(handle.runtime_root) / repl.REPL_STATE_DIR
    code = (
        "for (let tick = 1; tick <= 8; tick++) {\n"
        "  console.log(`tick ${tick}`);\n"
        "  await new Promise((done) => setTimeout(done, 400));\n"
        "}"
    )

    result = await repl.js_repl(ctx, JsReplInput(code=code, timeout=1000))

    assert result.is_error is False
    assert repl.STATE_UNCHANGED in result.content[0].text
    task = json.loads(result.content[0].text.splitlines()[-1])
    at_return = await session.bash(f'cat "{task["log"]}"')
    for _ in range(200):
        landed = await session.bash(f'cat "{task["exit_file"]}" 2>/dev/null || true')
        if landed.stdout.strip():
            break
        await asyncio.sleep(0.05)
    assert landed.stdout.strip() == "0"
    after = await session.bash(f'cat "{task["log"]}"')
    assert after.stdout != at_return.stdout
    assert "tick 8" in after.stdout
    assert not (state_dir / "js-repl.js").exists()


async def test_an_expired_cell_emitting_on_leaves_the_next_calls_images_alone(
    tmp_path: Path,
) -> None:
    """Against real node processes: the cell that outgrew its budget goes on emitting an image every
    100ms, and the next call emits one of its own and then waits, so the survivor writes several
    times after that emit. The next call reads its own file, so it answers with its own image."""
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
    survivor = (
        "for (let tick = 1; tick <= 40; tick++) {\n"
        '  emitImage(Buffer.from([9, 9, 9]), "image/png");\n'
        "  await new Promise((done) => setTimeout(done, 100));\n"
        "}"
    )
    expired = await repl.js_repl(ctx, JsReplInput(code=survivor, timeout=1000))
    assert repl.STATE_UNCHANGED in expired.content[0].text
    own = (
        'emitImage(Buffer.from([1, 2, 3]), "image/jpeg");\n'
        "await new Promise((done) => setTimeout(done, 600));"
    )

    result = await repl.js_repl(ctx, JsReplInput(code=own))

    assert result.is_error is False
    assert [(image.media_type, image.data) for image in result.content[1:]] == [
        ("image/jpeg", "AQID")
    ]


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
    state_dir = await session.runtime_path(repl.REPL_STATE_DIR)
    await session.bash(repl.global_modules_link(state_dir, (str(primary), str(secondary))))
    ctx = _context(session, tmp_path)
    code = (
        "const solo = await import('solo');\n"
        "const dup = await import('dup');\n"
        "console.log(solo.default, dup.default);"
    )
    result = await repl.js_repl(ctx, JsReplInput(code=code))
    assert result.is_error is False
    assert json.loads(result.content[0].text)["stdout"] == "from-solo from-primary\n"


async def test_js_repl_raises_when_global_module_linking_fails(tmp_path: Path) -> None:
    sandbox = FakeSandbox(
        link_result=ExecResult(stdout="", stderr="ln: permission denied", exit_code=1)
    )
    ctx = _context(sandbox, tmp_path)
    with pytest.raises(OSError, match="permission denied"):
        await repl.js_repl(ctx, JsReplInput(code="1"))


async def test_nonzero_exit_is_flagged_as_error(tmp_path: Path) -> None:
    sandbox = FakeSandbox(node_result=ExecResult(stdout="", stderr="boom", exit_code=1))
    ctx = _context(sandbox, tmp_path)
    result = await repl.js_repl(ctx, JsReplInput(code="throw new Error()"))
    assert result.is_error is True
    assert json.loads(result.content[0].text)["stderr"] == "boom"


async def test_js_repl_composes_prelude_and_accumulated_code_into_the_run_file(
    tmp_path: Path,
) -> None:
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    await repl.js_repl(ctx, JsReplInput(code="let x = 1"))
    await repl.js_repl(ctx, JsReplInput(code="console.log(x)"))
    run_file = sandbox.files[repl.JS_RUN_PATH].decode()
    assert run_file == (
        repl.js_emit_prelude(sandbox.emit_paths[-1]) + "let x = 1\nconsole.log(x)\n"
    )
    assert any(command == f"node {repl.JS_RUN_PATH}" for command in sandbox.commands)
