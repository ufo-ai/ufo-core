"""The daytona carrier extension against a fake standing in for the async Daytona SDK.

The Daytona service is not reachable from CI, so a fake plays the provider — it records the calls
the carrier makes and returns canned results in the SDK's exact shape, holding each sandbox's
lifecycle state the way the live service was measured to hold it (a stopped box refuses data-plane
calls with the same error class, a started one answers). Every assertion is the carrier's own
behavior: the handle it returns, the ExecResult it maps a run into, the create-or-resume it picks,
the launch wrapper and group stops it issues, and that `serve`'s `[sandbox] backend = "daytona"`
resolves this extension-contributed carrier — never the fake, which is only the dependency it
stands in for. The exceptions raised are the real Daytona SDK types, measured against the live
service 2026-08-23 on SDK 0.205.1."""

import logging
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import ufo_ext_daytona as daytona_ext
from daytona import (
    CreateSandboxFromSnapshotParams,
    DaytonaBadRequestError,
    DaytonaConnectionTimeoutError,
    DaytonaNotFoundError,
    DaytonaProcessExecutionTimeoutError,
    SandboxState,
)
from ufo_ext_daytona import (
    AUTO_ARCHIVE_MINUTES,
    AUTO_DELETE_NEVER,
    AUTO_STOP_MINUTES,
    CA_BUNDLE_PATH,
    CA_PEM_PATH,
    CONVERSATION_LABEL,
    DAYTONA_API_KEY_ENV,
    DAYTONA_SNAPSHOTS_ENV,
    EXEC_TIMEOUT_CODE,
    PREPARE_COMMAND,
    PREVIEW_TOKEN_HEADER,
    STATE_FRESH_SECONDS,
    STOP_RUN_TEMPLATE,
    STOP_TURN_TEMPLATE,
    DaytonaCarrier,
    build_daytona_carrier,
    snapshot_map,
)

from ufo.config import BlobConfig, Config, DatabaseConfig, SandboxConfig
from ufo.sandbox.select import select_carrier
from ufo.sandbox.session import (
    SANDBOX_SIZES,
    WORKSPACE_DIR,
    ProxyEndpoint,
    SandboxHandle,
    SandboxSpec,
    SandboxUnreachable,
)

SNAPSHOTS_ENV_VALUE = "small=ufo-sbx-small-aaa,medium=ufo-sbx-medium-bbb,large=ufo-sbx-large-ccc"
PROXY_PUBLIC_URL = "https://sandbox-proxy.test"
NOT_RUNNING = "Failed to execute command: bad request: failed to resolve container IP"


@dataclass
class _Exec:
    command: str
    cwd: str | None
    env: dict[str, str] | None
    timeout: int | None


@dataclass
class _ExecScript:
    exit_code: int = 0
    result: str = ""
    error: Exception | None = None


@dataclass
class _Response:
    exit_code: int
    result: str


@dataclass
class _Preview:
    url: str
    token: str | None


class _Process:
    def __init__(self, box: "_Box") -> None:
        self.box = box

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,  # noqa: ASYNC109
    ) -> _Response:
        if self.box.state != SandboxState.STARTED:
            raise DaytonaBadRequestError(NOT_RUNNING)
        self.box.execs.append(_Exec(command, cwd, env, timeout))
        script = self.box.scripts.pop(0) if self.box.scripts else _ExecScript()
        if script.error is not None:
            raise script.error
        return _Response(exit_code=script.exit_code, result=script.result)


class _Fs:
    def __init__(self, box: "_Box") -> None:
        self.box = box

    async def upload_file(self, src: bytes, dst: str) -> None:
        if self.box.state != SandboxState.STARTED:
            raise DaytonaBadRequestError(NOT_RUNNING)
        self.box.uploads.append((dst, src))


class _Box:
    def __init__(self, box_id: str, state: SandboxState) -> None:
        self.id = box_id
        self.state = state
        self.starts = 0
        self.start_waits = 0
        self.stop_waits = 0
        self.refreshes = 0
        self.execs: list[_Exec] = []
        self.scripts: list[_ExecScript] = []
        self.uploads: list[tuple[str, bytes]] = []
        self.process = _Process(self)
        self.fs = _Fs(self)

    async def start(self) -> None:
        self.starts += 1
        self.state = SandboxState.STARTED

    async def wait_for_sandbox_start(self) -> None:
        self.start_waits += 1
        self.state = SandboxState.STARTED

    async def wait_for_sandbox_stop(self) -> None:
        self.stop_waits += 1
        self.state = SandboxState.STOPPED

    async def refresh_data(self) -> None:
        self.refreshes += 1

    async def get_preview_link(self, port: int) -> _Preview:
        return _Preview(url=f"https://{port}-{self.id}.proxy.test", token="preview-token")

    async def download_url(self, path: str) -> str:
        return f"https://dl.test/{self.id}{path}?signature=sig"


class _Sdk:
    def __init__(self) -> None:
        self.boxes: dict[str, _Box] = {}
        self.created: list[CreateSandboxFromSnapshotParams] = []

    def holds(self, box_id: str, state: SandboxState = SandboxState.STARTED) -> _Box:
        box = _Box(box_id, state)
        self.boxes[box_id] = box
        return box

    async def create(self, params: CreateSandboxFromSnapshotParams) -> _Box:
        self.created.append(params)
        return self.holds(f"fresh-{len(self.created)}")

    async def get(self, sandbox_id: str) -> _Box:
        box = self.boxes.get(sandbox_id)
        if box is None:
            raise DaytonaNotFoundError(f"Sandbox with ID or name {sandbox_id} not found")
        return box


def _carrier(sdk: _Sdk, transport: httpx.MockTransport | None = None) -> DaytonaCarrier:
    http = httpx.AsyncClient(transport=transport) if transport is not None else None
    if http is None:
        return DaytonaCarrier(sdk=sdk, snapshots=snapshot_map(SNAPSHOTS_ENV_VALUE))
    return DaytonaCarrier(sdk=sdk, snapshots=snapshot_map(SNAPSHOTS_ENV_VALUE), http=http)


def _spec(
    conversation: UUID,
    *,
    resume_id: str | None = None,
    size: str | None = "medium",
    turn_id: UUID | None = None,
) -> SandboxSpec:
    return SandboxSpec(
        conversation_id=conversation,
        image_ref="unused",
        workspace_host_path="/unused",
        proxy=ProxyEndpoint(port=9, ca_cert="ca-pem", public_url=PROXY_PUBLIC_URL),
        run_token="tok-run",
        resume_id=resume_id,
        size=size,
        turn_id=turn_id,
    )


def _handle(conversation: UUID, container_id: str, turn_id: UUID | None = None) -> SandboxHandle:
    return SandboxHandle(
        conversation_id=conversation,
        container_id=container_id,
        run_token="tok-run",
        egress_env={"HTTPS_PROXY": "https://tok-run:@sandbox-proxy.test"},
        turn_id=turn_id,
    )


async def test_create_fresh_opens_from_the_size_snapshot_with_labels_and_intervals() -> None:
    sdk = _Sdk()
    carrier = _carrier(sdk)
    conversation = uuid4()
    handle = await carrier.create(_spec(conversation))
    (params,) = sdk.created
    assert params.snapshot == "ufo-sbx-medium-bbb"
    assert params.labels == {CONVERSATION_LABEL: str(conversation)}
    assert params.auto_stop_interval == AUTO_STOP_MINUTES
    assert params.auto_archive_interval == AUTO_ARCHIVE_MINUTES
    assert params.auto_delete_interval == AUTO_DELETE_NEVER
    assert handle.container_id == "fresh-1"
    assert handle.egress_env["HTTPS_PROXY"] == "https://tok-run:@sandbox-proxy.test"
    assert handle.egress_env["SSL_CERT_FILE"] == CA_BUNDLE_PATH
    assert handle.egress_env["NODE_EXTRA_CA_CERTS"] == CA_PEM_PATH


async def test_create_prepares_the_box_with_the_ca_and_workspace_check() -> None:
    sdk = _Sdk()
    carrier = _carrier(sdk)
    await carrier.create(_spec(uuid4()))
    box = sdk.boxes["fresh-1"]
    assert box.uploads == [(CA_PEM_PATH, b"ca-pem")]
    (prepare,) = box.execs
    assert prepare.command == f"sh -c {shlex.quote(PREPARE_COMMAND)}"


async def test_create_resumes_the_stored_id_and_starts_a_stopped_box() -> None:
    sdk = _Sdk()
    box = sdk.holds("s1", SandboxState.STOPPED)
    carrier = _carrier(sdk)
    handle = await carrier.create(_spec(uuid4(), resume_id="s1"))
    assert handle.container_id == "s1"
    assert box.starts == 1
    assert sdk.created == []


async def test_create_missing_resume_id_opens_fresh_and_logs_the_loss(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sdk = _Sdk()
    carrier = _carrier(sdk)
    with caplog.at_level(logging.INFO, logger="ufo"):
        handle = await carrier.create(_spec(uuid4(), resume_id="gone"))
    assert handle.container_id == "fresh-1"
    assert [
        record
        for record in caplog.records
        if record.getMessage() == "sandbox.daytona.resume_missed"
    ]


async def test_create_without_a_served_size_fails_loud() -> None:
    carrier = _carrier(_Sdk())
    with pytest.raises(RuntimeError, match="sandbox size"):
        await carrier.create(_spec(uuid4(), size=None))


async def test_create_raises_when_prepare_fails() -> None:
    sdk = _Sdk()
    carrier = _carrier(sdk)
    conversation = uuid4()
    box = sdk.holds("s1")
    box.scripts.append(_ExecScript(exit_code=1, result="cat: missing"))
    with pytest.raises(RuntimeError, match="prepare failed"):
        await carrier.create(_spec(conversation, resume_id="s1"))


async def test_attach_answers_none_without_an_id_or_for_a_missing_box() -> None:
    sdk = _Sdk()
    carrier = _carrier(sdk)
    assert await carrier.attach(_spec(uuid4(), resume_id=None)) is None
    assert await carrier.attach(_spec(uuid4(), resume_id="gone")) is None


async def test_attach_answers_none_for_a_box_being_destroyed() -> None:
    sdk = _Sdk()
    sdk.holds("s1", SandboxState.DESTROYING)
    carrier = _carrier(sdk)
    assert await carrier.attach(_spec(uuid4(), resume_id="s1")) is None


async def test_create_opens_fresh_when_the_stored_box_is_being_destroyed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sdk = _Sdk()
    sdk.holds("s1", SandboxState.DESTROYING)
    carrier = _carrier(sdk)
    with caplog.at_level(logging.INFO, logger="ufo"):
        handle = await carrier.create(_spec(uuid4(), resume_id="s1"))
    assert handle.container_id == "fresh-1"
    assert [
        record
        for record in caplog.records
        if record.getMessage() == "sandbox.daytona.resume_missed"
    ]


async def test_create_waits_out_a_box_caught_mid_stop_then_starts_it() -> None:
    sdk = _Sdk()
    box = sdk.holds("s1", SandboxState.STOPPING)
    carrier = _carrier(sdk)
    handle = await carrier.create(_spec(uuid4(), resume_id="s1"))
    assert handle.container_id == "s1"
    assert box.stop_waits == 1
    assert box.starts == 1
    assert sdk.created == []


async def test_attach_joins_a_start_already_under_way_instead_of_racing_it() -> None:
    sdk = _Sdk()
    box = sdk.holds("s1", SandboxState.STARTING)
    carrier = _carrier(sdk)
    handle = await carrier.attach(_spec(uuid4(), resume_id="s1"))
    assert handle is not None
    assert box.start_waits == 1
    assert box.starts == 0


async def test_attach_starts_a_stopped_box_and_carries_no_egress_env() -> None:
    sdk = _Sdk()
    box = sdk.holds("s1", SandboxState.STOPPED)
    carrier = _carrier(sdk)
    handle = await carrier.attach(_spec(uuid4(), resume_id="s1"))
    assert handle is not None
    assert handle.container_id == "s1"
    assert box.starts == 1
    assert dict(handle.egress_env) == {}


async def test_exec_wraps_the_command_and_maps_the_result() -> None:
    sdk = _Sdk()
    box = sdk.holds("s1")
    box.scripts.append(_ExecScript(exit_code=0, result="combined output"))
    carrier = _carrier(sdk)
    result = await carrier.exec(_handle(uuid4(), "s1"), ("echo", "hi there"), timeout_s=30)
    (call,) = box.execs
    wrapper = shlex.split(call.command)
    assert wrapper[:3] == ["setsid", "sh", "-c"]
    assert wrapper[3].endswith("; exec echo 'hi there'")
    assert call.cwd == WORKSPACE_DIR
    assert call.timeout == 30
    assert call.env is not None
    assert call.env["HTTPS_PROXY"] == "https://tok-run:@sandbox-proxy.test"
    assert call.env["NODE_PATH"]
    assert (result.stdout, result.stderr, result.exit_code) == ("combined output", "", 0)
    assert result.timed_out_after_s is None


async def test_exec_records_the_turn_group_only_when_a_turn_owns_the_call() -> None:
    sdk = _Sdk()
    box = sdk.holds("s1")
    carrier = _carrier(sdk)
    turn = uuid4()
    await carrier.exec(_handle(uuid4(), "s1", turn_id=turn), ("true",), timeout_s=5)
    assert f"/tmp/.ufo-turn-{turn}.pids" in box.execs[0].command
    await carrier.exec(_handle(uuid4(), "s1"), ("true",), timeout_s=5)
    assert "/tmp/.ufo-turn-" not in box.execs[1].command


async def test_exec_nonzero_exit_is_a_result_not_an_error() -> None:
    sdk = _Sdk()
    box = sdk.holds("s1")
    box.scripts.append(_ExecScript(exit_code=3, result="boom"))
    carrier = _carrier(sdk)
    result = await carrier.exec(_handle(uuid4(), "s1"), ("false",), timeout_s=5)
    assert (result.exit_code, result.stdout) == (3, "boom")


@pytest.mark.parametrize(
    "error",
    [
        DaytonaProcessExecutionTimeoutError("command execution timeout"),
        DaytonaConnectionTimeoutError("timed out"),
    ],
)
async def test_exec_deadline_kills_the_launched_group_and_reports_124(
    error: Exception,
) -> None:
    sdk = _Sdk()
    box = sdk.holds("s1")
    box.scripts.append(_ExecScript(error=error))
    carrier = _carrier(sdk)
    result = await carrier.exec(_handle(uuid4(), "s1"), ("sleep", "600"), timeout_s=5)
    assert result.exit_code == EXEC_TIMEOUT_CODE
    assert result.timed_out_after_s == 5
    launch, stop = box.execs
    pidfile = re.search(r"/tmp/\.ufo-run-[0-9a-f]{32}\.pid", launch.command)
    assert pidfile is not None
    assert pidfile.group(0) in stop.command
    assert 'kill -9 -"$p"' in stop.command


async def test_exec_deadline_stop_failure_is_swallowed() -> None:
    sdk = _Sdk()
    box = sdk.holds("s1")
    box.scripts.append(_ExecScript(error=DaytonaProcessExecutionTimeoutError("timeout")))
    box.scripts.append(_ExecScript(error=DaytonaConnectionTimeoutError("gone")))
    carrier = _carrier(sdk)
    result = await carrier.exec(_handle(uuid4(), "s1"), ("sleep", "600"), timeout_s=5)
    assert result.exit_code == EXEC_TIMEOUT_CODE


async def test_exec_recovers_once_when_the_box_idled_out_between_calls() -> None:
    sdk = _Sdk()
    box = sdk.holds("s1")
    carrier = _carrier(sdk)
    handle = _handle(uuid4(), "s1")
    await carrier.exec(handle, ("true",), timeout_s=5)
    box.state = SandboxState.STOPPED
    box.scripts.append(_ExecScript(exit_code=0, result="back"))
    result = await carrier.exec(handle, ("echo", "back"), timeout_s=5)
    assert (result.exit_code, result.stdout) == (0, "back")
    assert box.starts == 1


async def test_exec_failure_on_a_running_box_raises() -> None:
    sdk = _Sdk()
    box = sdk.holds("s1")
    box.scripts.append(_ExecScript(error=DaytonaBadRequestError("bad request: malformed")))
    carrier = _carrier(sdk)
    with pytest.raises(DaytonaBadRequestError):
        await carrier.exec(_handle(uuid4(), "s1"), ("true",), timeout_s=5)
    assert box.refreshes == 1
    assert box.starts == 0


async def test_stop_commands_sweeps_only_the_turns_own_groups() -> None:
    sdk = _Sdk()
    box = sdk.holds("s1")
    carrier = _carrier(sdk)
    turn = uuid4()
    await carrier.stop_commands(_handle(uuid4(), "s1", turn_id=turn))
    (stop,) = box.execs
    assert f"/tmp/.ufo-turn-{turn}.pids" in stop.command
    assert 'kill -9 -"$p"' in stop.command
    assert "ps -o sess=" in stop.command


async def test_stop_commands_without_a_turn_is_a_no_op() -> None:
    sdk = _Sdk()
    box = sdk.holds("s1")
    carrier = _carrier(sdk)
    await carrier.stop_commands(_handle(uuid4(), "s1"))
    assert box.execs == []


async def test_stop_commands_swallows_a_box_that_cannot_answer() -> None:
    sdk = _Sdk()
    box = sdk.holds("s1")
    box.scripts.append(_ExecScript(error=DaytonaConnectionTimeoutError("silent")))
    carrier = _carrier(sdk)
    await carrier.stop_commands(_handle(uuid4(), "s1", turn_id=uuid4()))


def test_stop_templates_run_in_a_real_shell(tmp_path: Path) -> None:
    """The stop commands are strings a remote shell parses, so the proof runs them through a real
    one: a rendering that misquotes (the tr-usage and escaped-kill faults the live smoke caught)
    errors here instead of silently reaping nothing. A dead pid exercises the session-leader guard
    and both templates must still remove their file."""
    pidfile = tmp_path / "run.pid"
    pidfile.write_text("999999")
    done = subprocess.run(
        STOP_RUN_TEMPLATE.format(pidfile=pidfile), shell=True, capture_output=True, text=True
    )
    assert "usage" not in done.stderr.lower()
    assert "\\" not in STOP_RUN_TEMPLATE
    assert not pidfile.exists()
    pids = tmp_path / "turn.pids"
    pids.write_text("999999\n")
    done = subprocess.run(
        STOP_TURN_TEMPLATE.format(pids=pids), shell=True, capture_output=True, text=True
    )
    assert "usage" not in done.stderr.lower()
    assert "tr:" not in done.stderr
    assert not pids.exists()


async def test_write_uploads_bytes_to_the_path() -> None:
    sdk = _Sdk()
    box = sdk.holds("s1")
    carrier = _carrier(sdk)
    await carrier.write(_handle(uuid4(), "s1"), "/workspace/out.bin", b"payload")
    assert box.uploads == [("/workspace/out.bin", b"payload")]


async def test_write_recovers_once_when_the_box_idled_out() -> None:
    sdk = _Sdk()
    box = sdk.holds("s1")
    carrier = _carrier(sdk)
    handle = _handle(uuid4(), "s1")
    await carrier.write(handle, "/workspace/a", b"a")
    box.state = SandboxState.STOPPED
    await carrier.write(handle, "/workspace/b", b"b")
    assert box.starts == 1
    assert [dst for dst, _ in box.uploads] == ["/workspace/a", "/workspace/b"]


async def test_read_streams_the_file_in_chunks() -> None:
    sdk = _Sdk()
    sdk.holds("s1")

    def serve(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/s1/workspace/big.bin"
        return httpx.Response(200, content=b"x" * 70_000)

    carrier = _carrier(sdk, transport=httpx.MockTransport(serve))
    chunks = [chunk async for chunk in carrier.read(_handle(uuid4(), "s1"), "/workspace/big.bin")]
    assert b"".join(chunks) == b"x" * 70_000


async def test_read_maps_a_missing_file_on_a_running_box_to_filenotfounderror() -> None:
    sdk = _Sdk()
    box = sdk.holds("s1")
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    carrier = _carrier(sdk, transport=transport)
    with pytest.raises(FileNotFoundError):
        async for _ in carrier.read(_handle(uuid4(), "s1"), "/workspace/missing"):
            raise AssertionError("no chunks for a missing file")
    assert box.refreshes == 1


async def test_read_restarts_a_stopped_box_before_believing_a_404() -> None:
    sdk = _Sdk()
    box = sdk.holds("s1")

    def serve(request: httpx.Request) -> httpx.Response:
        if box.state != SandboxState.STARTED:
            return httpx.Response(404)
        return httpx.Response(200, content=b"data")

    carrier = _carrier(sdk, transport=httpx.MockTransport(serve))
    handle = _handle(uuid4(), "s1")
    async for _ in carrier.read(handle, "/workspace/f"):
        pass
    box.state = SandboxState.STOPPED
    chunks = [chunk async for chunk in carrier.read(handle, "/workspace/f")]
    assert b"".join(chunks) == b"data"
    assert box.starts == 1


async def test_file_op_runs_sbxfs_through_exec_and_parses_the_json() -> None:
    sdk = _Sdk()
    box = sdk.holds("s1")
    box.scripts.append(_ExecScript(exit_code=0, result='{"files": []}'))
    carrier = _carrier(sdk)
    parsed = await carrier.file_op(_handle(uuid4(), "s1"), "glob", {"pattern": "*"})
    assert parsed == {"files": []}
    (call,) = box.execs
    assert "exec sbxfs glob" in call.command


async def test_dial_answers_the_preview_host_token_and_tls() -> None:
    sdk = _Sdk()
    sdk.holds("s1")
    carrier = _carrier(sdk)
    target = await carrier.dial(_handle(uuid4(), "s1"), 8080)
    assert target.host == "8080-s1.proxy.test"
    assert target.tls is True
    assert target.headers == {PREVIEW_TOKEN_HEADER: "preview-token"}


async def test_dial_restarts_a_stopped_box_once_its_confirmation_goes_stale() -> None:
    sdk = _Sdk()
    box = sdk.holds("s1")
    now = [1000.0]
    carrier = DaytonaCarrier(
        sdk=sdk, snapshots=snapshot_map(SNAPSHOTS_ENV_VALUE), clock=lambda: now[0]
    )
    handle = _handle(uuid4(), "s1")
    await carrier.dial(handle, 8080)
    box.state = SandboxState.STOPPED
    await carrier.dial(handle, 8080)
    assert box.starts == 0
    now[0] += STATE_FRESH_SECONDS
    await carrier.dial(handle, 8080)
    assert box.starts == 1


async def test_dial_for_a_gone_sandbox_raises_sandbox_unreachable() -> None:
    carrier = _carrier(_Sdk())
    with pytest.raises(SandboxUnreachable):
        await carrier.dial(_handle(uuid4(), "gone"), 8080)


def test_snapshot_map_parses_wire_form() -> None:
    assert snapshot_map(SNAPSHOTS_ENV_VALUE) == {
        "small": "ufo-sbx-small-aaa",
        "medium": "ufo-sbx-medium-bbb",
        "large": "ufo-sbx-large-ccc",
    }


@pytest.mark.parametrize("value", ["small=a,medium=b", "small=a,medium=b,large=", "garbage"])
def test_snapshot_map_rejects_a_malformed_or_incomplete_map(value: str) -> None:
    with pytest.raises(RuntimeError, match=DAYTONA_SNAPSHOTS_ENV):
        snapshot_map(value)


def test_build_daytona_carrier_requires_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DAYTONA_API_KEY_ENV, raising=False)
    monkeypatch.setenv(DAYTONA_SNAPSHOTS_ENV, SNAPSHOTS_ENV_VALUE)
    with pytest.raises(RuntimeError, match=DAYTONA_API_KEY_ENV):
        build_daytona_carrier()


def test_build_daytona_carrier_requires_the_snapshot_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DAYTONA_API_KEY_ENV, "dtn_key")
    monkeypatch.delenv(DAYTONA_SNAPSHOTS_ENV, raising=False)
    with pytest.raises(RuntimeError, match=DAYTONA_SNAPSHOTS_ENV):
        build_daytona_carrier()


def test_config_backend_daytona_resolves_the_extension_contributed_carrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seam end to end: with `[sandbox] backend = "daytona"` and this extension's Manifest
    present, `serve` builds exactly this extension's carrier by name."""
    monkeypatch.setenv(DAYTONA_API_KEY_ENV, "dtn_key")
    monkeypatch.setenv(DAYTONA_SNAPSHOTS_ENV, SNAPSHOTS_ENV_VALUE)
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///carrier.db"),
        blob=BlobConfig(backend="filesystem", root=Path("blobs")),
        sandbox=SandboxConfig(backend="daytona", proxy_public_url=PROXY_PUBLIC_URL),
    )
    carrier, spec = select_carrier(config, (daytona_ext.manifest(),))
    assert isinstance(carrier, DaytonaCarrier)
    assert spec.off_cluster
    assert spec.sizes == SANDBOX_SIZES
    assert carrier.snapshots["small"] == "ufo-sbx-small-aaa"


def test_daytona_backend_without_proxy_public_url_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DAYTONA_API_KEY_ENV, "dtn_key")
    monkeypatch.setenv(DAYTONA_SNAPSHOTS_ENV, SNAPSHOTS_ENV_VALUE)
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///carrier.db"),
        blob=BlobConfig(backend="filesystem", root=Path("blobs")),
        sandbox=SandboxConfig(backend="daytona"),
    )
    with pytest.raises(RuntimeError, match="proxy_public_url"):
        select_carrier(config, (daytona_ext.manifest(),))
