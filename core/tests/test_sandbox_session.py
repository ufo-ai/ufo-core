import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

import ufo.tools.builtins as builtins_module
from ufo.audience import conversation_audience
from ufo.blob import FilesystemBlobStore
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import (
    DEFAULT_EXEC_TIMEOUT_SECONDS,
    WORKSPACE_DIR,
    ExecResult,
    ProbeToken,
    ProbeTokenCodec,
    ProxyEndpoint,
    RunToken,
    RunTokenCodec,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
)
from ufo.schema.records import Agent, Turn
from ufo.tools.builtins import (
    EXEC_TIMEOUT_COMMAND_MAX_CHARS,
    EXEC_TIMEOUT_VITALS_CMD,
    BashInput,
    bash_handler,
)
from ufo.tools.context import SpawnResult, ToolContext

RUN_TOKENS = RunTokenCodec(b"run-token-test-secret")
PROBE_TOKENS = ProbeTokenCodec(b"run-token-test-secret")


def _basic(username: str) -> str:
    """The Proxy-Authorization header an HTTP client sends for `http://<username>:@host` — Basic
    base64 of `username:` (empty password), exactly what the container's proxy URL produces."""
    return "Basic " + base64.b64encode(f"{username}:".encode()).decode()


def test_run_token_round_trips_encode_then_proxy_auth() -> None:
    token = RunToken(workspace_id=uuid4(), turn_id=uuid4(), acting_member_id=uuid4())
    assert RUN_TOKENS.from_proxy_auth(_basic(RUN_TOKENS.encode(token))) == token


def test_encoded_token_is_url_safe_userinfo() -> None:
    encoded = RUN_TOKENS.encode(RunToken(workspace_id=uuid4(), turn_id=uuid4()))
    assert all(char.isalnum() or char in "-_." for char in encoded)


def test_from_proxy_auth_rejects_non_basic_scheme() -> None:
    with pytest.raises(ValueError, match="basic"):
        RUN_TOKENS.from_proxy_auth("Bearer " + RUN_TOKENS.encode(RunToken(uuid4(), uuid4())))


def test_from_proxy_auth_rejects_missing_header() -> None:
    with pytest.raises(ValueError, match="basic"):
        RUN_TOKENS.from_proxy_auth("")


def test_from_proxy_auth_rejects_a_malformed_run_token() -> None:
    with pytest.raises(ValueError):
        RUN_TOKENS.from_proxy_auth(_basic("not-a-run-token"))


def test_run_token_rejects_a_valid_shape_signed_by_another_deployment() -> None:
    run = RunToken(uuid4(), uuid4())
    forged = RunTokenCodec(b"other-deployment").encode(run)
    with pytest.raises(ValueError, match="signed"):
        RUN_TOKENS.from_proxy_auth(_basic(forged))


def _probe(expires_at: int = 1_800_000_000, member: UUID | None = None) -> ProbeToken:
    return ProbeToken(
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        probe_id=uuid4(),
        expires_at=expires_at,
        acting_member_id=member,
    )


def test_probe_token_round_trips_encode_then_proxy_auth() -> None:
    probe = _probe()
    assert PROBE_TOKENS.from_proxy_auth(_basic(PROBE_TOKENS.encode(probe))) == probe


def test_probe_token_round_trips_its_acting_member() -> None:
    """The member a probe acts as is signed with it, so the proxy reads an authority this deployment
    granted rather than one the sandbox could name for itself. Absent is a distinct value, not a
    zero: it means nobody, and the wire keeps the two apart."""
    acting = _probe(member=uuid4())
    assert PROBE_TOKENS.from_proxy_auth(_basic(PROBE_TOKENS.encode(acting))) == acting
    assert PROBE_TOKENS.from_proxy_auth(_basic(PROBE_TOKENS.encode(_probe()))).acting_member_id is (
        None
    )


def test_encoded_probe_token_is_url_safe_userinfo() -> None:
    encoded = PROBE_TOKENS.encode(_probe())
    assert all(char.isalnum() or char in "-_." for char in encoded)


def test_probe_from_proxy_auth_rejects_non_basic_scheme() -> None:
    with pytest.raises(ValueError, match="basic"):
        PROBE_TOKENS.from_proxy_auth("Bearer " + PROBE_TOKENS.encode(_probe()))


def test_probe_from_proxy_auth_rejects_a_malformed_probe_token() -> None:
    with pytest.raises(ValueError):
        PROBE_TOKENS.from_proxy_auth(_basic("not-a-probe-token"))


def test_probe_token_rejects_a_valid_shape_signed_by_another_deployment() -> None:
    forged = ProbeTokenCodec(b"other-deployment").encode(_probe())
    with pytest.raises(ValueError, match="signed"):
        PROBE_TOKENS.from_proxy_auth(_basic(forged))


def test_neither_codec_reads_the_other_domain_under_one_secret() -> None:
    """Both classes are signed with the one deploy secret, so only the domain inside the payload
    keeps them apart: a run token presented where a probe token is expected must be refused as
    firmly as a forgery, else a turn's token would authorize egress with no turn read behind it."""
    run = RUN_TOKENS.encode(RunToken(uuid4(), uuid4()))
    probe = PROBE_TOKENS.encode(_probe())
    with pytest.raises(ValueError, match="probe token"):
        PROBE_TOKENS.from_proxy_auth(_basic(run))
    with pytest.raises(ValueError, match="run token"):
        RUN_TOKENS.from_proxy_auth(_basic(probe))


def test_authorized_session_scopes_proxy_and_cli_environment_without_mutating_base() -> None:
    conversation_id = uuid4()
    common = RUN_TOKENS.encode(RunToken(uuid4(), uuid4()))
    member = RUN_TOKENS.encode(RunToken(uuid4(), uuid4(), uuid4()))
    proxy = f"http://{common}:@proxy:9000"
    base = SandboxSession(
        carrier=_RecordingCarrier(),
        handle=SandboxHandle(
            conversation_id=conversation_id,
            container_id="c",
            run_token=common,
            egress_env={
                "HTTP_PROXY": proxy,
                "HTTPS_PROXY": proxy,
                "http_proxy": proxy,
                "https_proxy": proxy,
                "ALICE_KEY": "alice",
                "UNRELATED": common,
            },
        ),
    )

    authorized = base.authorize(
        member,
        frozenset(("ALICE_KEY", "BOB_KEY")),
        {"BOB_KEY": "bob"},
    )

    assert authorized.handle.run_token == member
    assert all(
        member in authorized.handle.egress_env[name]
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
    )
    assert authorized.handle.egress_env["UNRELATED"] == common
    assert "ALICE_KEY" not in authorized.handle.egress_env
    assert authorized.handle.egress_env["BOB_KEY"] == "bob"
    assert base.handle.run_token == common
    assert base.handle.egress_env["HTTP_PROXY"] == proxy


class _RecordingCarrier:
    """Records the exec timeout so the bash tool's ms→s conversion and cap can be asserted."""

    def __init__(self, result: ExecResult | None = None, probe: ExecResult | None = None) -> None:
        self.timeouts: list[int] = []
        self.commands: list[str] = []
        self.result = result or ExecResult(stdout="", stderr="", exit_code=0)
        self.probe = probe

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise NotImplementedError

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        self.timeouts.append(timeout_s)
        self.commands.append(argv[-1] if argv else "")
        if self.probe is not None and argv and argv[-1] == EXEC_TIMEOUT_VITALS_CMD:
            return self.probe
        return self.result

    def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]:
        raise NotImplementedError


async def _no_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn unused")


def _bash_ctx(carrier: _RecordingCarrier, tmp_path: Path) -> ToolContext:
    session = SandboxSession(
        carrier=carrier, handle=SandboxHandle(conversation_id=uuid4(), container_id="c")
    )
    return _tool_ctx(session, tmp_path)


async def _live_ctx(tmp_path: Path) -> ToolContext:
    """A live local sandbox whose workspace root holds a space, so every shell string a
    background task builds is exercised against the rewrite the terminal carrier performs on a
    member's real directory."""
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref=SANDBOX_IMAGE_REF,
            workspace_host_path=str(tmp_path / "my ws"),
            proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
            run_token="off-turn-test",
        )
    )
    return _tool_ctx(SandboxSession(carrier=carrier, handle=handle), tmp_path)


async def _wait_for_file(session: SandboxSession, path: str) -> str:
    for _ in range(200):
        result = await session.bash(f'cat "{path}" 2>/dev/null || true')
        if result.stdout.strip():
            return result.stdout.strip()
        await asyncio.sleep(0.05)
    raise AssertionError(f"{path} never appeared")


def _tool_ctx(session: SandboxSession, tmp_path: Path) -> ToolContext:
    return ToolContext(
        sandbox=session,
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
        spawn=_no_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
    )


async def test_bash_timeout_is_milliseconds_capped_and_converted(tmp_path: Path) -> None:
    """Source-faithful: bash `timeout` is milliseconds (max 600000). The handler caps then converts
    to the carrier's seconds; an omitted timeout falls back to the default budget."""
    carrier = _RecordingCarrier()
    ctx = _bash_ctx(carrier, tmp_path)
    await bash_handler(
        ctx, BashInput(command="echo hi", timeout=5000, user_description="checking the box")
    )
    await bash_handler(
        ctx, BashInput(command="echo hi", timeout=9_000_000, user_description="checking the box")
    )
    await bash_handler(ctx, BashInput(command="echo hi", user_description="checking the box"))
    assert carrier.timeouts == [5, 600, DEFAULT_EXEC_TIMEOUT_SECONDS]


def _task_payload(text: str) -> dict[str, str]:
    """The handles exactly as the result reports them, parsed as the data they are — every path
    absolute, since an exec's working directory is carrier-dependent and only the reported
    handles are the contract."""
    payload = json.loads(text.splitlines()[-1])
    assert isinstance(payload, dict)
    return payload


async def test_background_bash_detaches_and_signals_completion(tmp_path: Path) -> None:
    """The handler returns before the command ends, and the directive's own probe line — clean
    exit, empty output — stays quiet until the exit file appears exactly once with the code."""
    ctx = await _live_ctx(tmp_path)
    result = await bash_handler(
        ctx,
        BashInput(
            command="sleep 1; echo finished-marker", background=True, user_description="building"
        ),
    )
    assert not result.is_error
    task = _task_payload(result.content[0].text)
    probe = await ctx.sandbox.bash(task["watch"])
    assert probe.exit_code == 0
    assert probe.stdout == ""
    assert await _wait_for_file(ctx.sandbox, task["exit_file"]) == "0"
    log = await ctx.sandbox.bash(f'cat "{task["log"]}"')
    assert "finished-marker" in log.stdout


async def test_background_bash_records_a_failing_exit_code(tmp_path: Path) -> None:
    ctx = await _live_ctx(tmp_path)
    result = await bash_handler(
        ctx,
        BashInput(command="echo boom >&2; exit 7", background=True, user_description="building"),
    )
    task = _task_payload(result.content[0].text)
    assert await _wait_for_file(ctx.sandbox, task["exit_file"]) == "7"
    log = await ctx.sandbox.bash(f'cat "{task["log"]}"')
    assert "boom" in log.stdout


async def test_background_bash_carries_the_commands_own_quoting(tmp_path: Path) -> None:
    """The command travels as its own argv element: one containing the launcher's delimiter must run
    verbatim rather than break out of the wrapper."""
    ctx = await _live_ctx(tmp_path)
    result = await bash_handler(
        ctx,
        BashInput(command='echo "x\'y"', background=True, user_description="building"),
    )
    task = _task_payload(result.content[0].text)
    assert await _wait_for_file(ctx.sandbox, task["exit_file"]) == "0"
    log = await ctx.sandbox.bash(f'cat "{task["log"]}"')
    assert log.stdout.strip() == "x'y"


async def test_background_bash_rewrites_workspace_paths_inside_the_command(tmp_path: Path) -> None:
    """The command travels as an argv element precisely so the carrier's /workspace rewrite
    reaches its text: an absolute workspace path inside the command must land under the real
    root, exactly as it would in the foreground."""
    ctx = await _live_ctx(tmp_path)
    result = await bash_handler(
        ctx,
        BashInput(
            command=f'echo rewritten > "{WORKSPACE_DIR}/out.txt"',
            background=True,
            user_description="building",
        ),
    )
    task = _task_payload(result.content[0].text)
    assert await _wait_for_file(ctx.sandbox, task["exit_file"]) == "0"
    written = await ctx.sandbox.bash(f'cat "{WORKSPACE_DIR}/out.txt"')
    assert written.stdout.strip() == "rewritten"


async def test_the_advertised_stop_line_ends_the_command_and_signals(tmp_path: Path) -> None:
    """The result's own stop line — reading the pid back from the id-keyed file — must end the
    work AND still write the exit file, so a task is stoppable from a later turn that holds only
    the id, and a stopped task's watch fires once instead of waiting forever."""
    ctx = await _live_ctx(tmp_path)
    result = await bash_handler(
        ctx,
        BashInput(command="sleep 30", background=True, user_description="building"),
    )
    task = _task_payload(result.content[0].text)
    killed = await ctx.sandbox.bash(task["stop"])
    assert killed.exit_code == 0
    assert await _wait_for_file(ctx.sandbox, task["exit_file"]) != "0"


async def test_the_stop_line_reaches_the_commands_descendants(tmp_path: Path) -> None:
    """A command whose work runs in a descendant of its shell — a build's compilers, a runner's
    workers — must die with the stop, not outlive an exit file that claims it ended: the wrapper
    signals the command's whole process group, never just the shell in front of the work."""
    ctx = await _live_ctx(tmp_path)
    result = await bash_handler(
        ctx,
        BashInput(
            command=f'sleep 30 & echo $! > "{WORKSPACE_DIR}/desc.pid"; wait',
            background=True,
            user_description="building",
        ),
    )
    task = _task_payload(result.content[0].text)
    descendant = await _wait_for_file(ctx.sandbox, f"{WORKSPACE_DIR}/desc.pid")
    killed = await ctx.sandbox.bash(task["stop"])
    assert killed.exit_code == 0
    assert await _wait_for_file(ctx.sandbox, task["exit_file"]) != "0"
    for _ in range(200):
        alive = await ctx.sandbox.bash(f'kill -0 "{descendant}" 2>/dev/null && echo alive || true')
        if not alive.stdout.strip():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"descendant {descendant} survived the stop")


async def test_a_stopped_command_names_the_deadline_that_stopped_it(tmp_path: Path) -> None:
    """The seconds reported are the ones that applied, and a request the cap reduced says so. An
    agent that cannot tell which deadline fired re-runs the same command against the same wall,
    and one that never learns its request was capped keeps asking for a budget it cannot have."""
    default_stop = _RecordingCarrier(
        ExecResult(
            stdout="",
            stderr="",
            exit_code=124,
            timed_out_after_s=DEFAULT_EXEC_TIMEOUT_SECONDS,
        )
    )
    capped = _RecordingCarrier(
        ExecResult(stdout="", stderr="", exit_code=124, timed_out_after_s=600)
    )
    exact = _RecordingCarrier(ExecResult(stdout="", stderr="", exit_code=124, timed_out_after_s=5))

    unset = await bash_handler(
        _bash_ctx(default_stop, tmp_path),
        BashInput(command="pytest -q", user_description="running the tests"),
    )
    reduced = await bash_handler(
        _bash_ctx(capped, tmp_path),
        BashInput(command="pytest -q", timeout=9_000_000, user_description="running the tests"),
    )
    honoured = await bash_handler(
        _bash_ctx(exact, tmp_path),
        BashInput(command="pytest -q", timeout=5000, user_description="running the tests"),
    )

    assert unset.is_error and reduced.is_error and honoured.is_error
    assert f"{DEFAULT_EXEC_TIMEOUT_SECONDS}s" in unset.content[0].text
    assert "no timeout" in unset.content[0].text
    assert (
        "600s" in reduced.content[0].text
        and "9000s requested was capped" in reduced.content[0].text
    )
    assert "5s" in honoured.content[0].text and "capped" not in honoured.content[0].text


async def test_a_commands_own_timeout_is_not_reported_as_the_sandboxs(tmp_path: Path) -> None:
    """`timeout` inside the command exits 124 exactly as a carrier-stopped command does. Only the
    carrier says whether its own deadline fired, so a 124 it did not produce stays a plain exit
    code — reporting it as a sandbox timeout would send the agent raising a limit that was never
    what stopped it."""
    carrier = _RecordingCarrier(
        ExecResult(stdout="", stderr="", exit_code=124, timed_out_after_s=None)
    )

    result = await bash_handler(
        _bash_ctx(carrier, tmp_path),
        BashInput(
            command="timeout 3000 pytest -q", timeout=600_000, user_description="running the tests"
        ),
    )

    assert result.is_error
    assert result.content[0].text == "exit code: 124"


async def test_a_stopped_command_records_the_container_it_was_stopped_in(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The counter names the carrier and nothing else, so a timeout is unattributable: which
    profile, which command, and what the container was doing are all absent. The record carries
    them, and the vitals come from the container itself at the moment it stopped the work."""
    carrier = _RecordingCarrier(
        ExecResult(stdout="", stderr="", exit_code=124, timed_out_after_s=600),
        probe=ExecResult(stdout="1.90 1.20 0.80 3/210 900\n", stderr="", exit_code=0),
    )
    with caplog.at_level(logging.INFO, logger="ufo"):
        result = await bash_handler(
            _bash_ctx(carrier, tmp_path),
            BashInput(
                command="uv run pytest -n auto " + "x" * 500,
                timeout=9_000_000,
                user_description="running the tests",
            ),
        )

    logged = next(r for r in caplog.records if r.message == "sandbox.exec_timeout")
    assert logged.ufo["applied_seconds"] == 600
    assert logged.ufo["requested_seconds"] == 9000
    assert logged.ufo["vitals_reached"] is True
    assert "1.90 1.20 0.80" in logged.ufo["vitals"]
    assert logged.ufo["command"].startswith("uv run pytest -n auto")
    assert len(logged.ufo["command"]) == EXEC_TIMEOUT_COMMAND_MAX_CHARS
    assert result.is_error and "600s" in result.content[0].text


async def test_a_container_that_cannot_answer_is_the_reading_that_matters(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A busy container answers `cat /proc/loadavg` in milliseconds, so a probe that never returns
    says the command channel stopped answering rather than the work being slow. The probe is
    bounded by its own wait, and the tool result is the command's timeout either way — diagnosis
    must never change what the caller is told."""
    monkeypatch.setattr(builtins_module, "EXEC_TIMEOUT_VITALS_SECONDS", 0.05)

    class _Unanswering(_RecordingCarrier):
        async def exec(
            self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
        ) -> ExecResult:
            if argv and argv[-1] == EXEC_TIMEOUT_VITALS_CMD:
                await asyncio.sleep(5)
            return await super().exec(handle, argv, timeout_s)

    carrier = _Unanswering(
        ExecResult(stdout="", stderr="", exit_code=124, timed_out_after_s=120),
        probe=ExecResult(stdout="0.10 0.05 0.01 1/80 900\n", stderr="", exit_code=0),
    )
    with caplog.at_level(logging.INFO, logger="ufo"):
        result = await bash_handler(
            _bash_ctx(carrier, tmp_path),
            BashInput(command="echo alive", user_description="checking the box"),
        )

    logged = next(r for r in caplog.records if r.message == "sandbox.exec_timeout")
    assert logged.ufo["vitals_reached"] is False
    assert logged.ufo["vitals"] == ""
    assert result.is_error and "120s" in result.content[0].text


async def test_a_command_that_failed_on_its_own_records_no_timeout(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Only the carrier's own deadline is a sandbox timeout. A command that chose its exit — even
    124 from its own `timeout` — records nothing, else the record would count the failures it is
    meant to tell apart."""
    carrier = _RecordingCarrier(
        ExecResult(stdout="", stderr="boom", exit_code=124, timed_out_after_s=None)
    )
    with caplog.at_level(logging.INFO, logger="ufo"):
        await bash_handler(
            _bash_ctx(carrier, tmp_path),
            BashInput(command="timeout 3000 pytest", user_description="running the tests"),
        )

    assert not [r for r in caplog.records if r.message == "sandbox.exec_timeout"]
    assert EXEC_TIMEOUT_VITALS_CMD not in carrier.commands
