"""The e2b carrier extension against a fake standing in for the async e2b SDK.

The e2b service is not reachable from CI, so a fake plays the provider — it records the calls the
carrier makes and returns canned results in the SDK's exact shape (a create/connect factory whose
sandboxes carry `commands` and `files`). Every assertion is the carrier's own behavior: the handle
it returns, the ExecResult it maps a run, a non-zero exit, and a timeout into, the bytes it reads
out, the create-or-resume it picks, and that `serve`'s `[sandbox] backend = "e2b"` resolves
this extension-contributed carrier — never the fake, which is only the dependency it stands in for.
The exceptions raised are the real e2b and transport types, so the mapping is exercised against the
classes the live SDK throws, and the fake keeps each sandbox's lease on the same clock the carrier
reads — a container pauses when its span runs out and answers the renewal the way the live service
was measured to answer it, so a lapsed lease is a state a test reaches rather than one it asserts
about."""

import asyncio
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import httpcore
import httpx
import pytest
import ufo_ext_e2b as e2b_ext
from e2b.exceptions import (
    FileNotFoundException,
    SandboxException,
    SandboxNotFoundException,
    TimeoutException,
)
from e2b.sandbox.commands.command_handle import CommandExitException
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from ufo_ext_e2b import (
    CAP_WORKLOAD_COMMAND,
    CARRIER_NAME,
    CLIENT_PATH,
    CONVERSATION_METADATA_KEY,
    E2B_API_KEY_ENV,
    E2B_LIFECYCLE,
    E2B_NETWORK,
    E2B_TEMPLATES_ENV,
    ENSURE_WORKSPACE_COMMAND,
    EXEC_TIMEOUT_CODE,
    INSTALL_CA_COMMAND,
    LEASE_MARGIN_SECONDS,
    PREPARE_ATTEMPTS,
    RESUME_RETRIES,
    RESUME_RETRY_DELAY_SECONDS,
    RESUME_TOTAL_TIMEOUT_SECONDS,
    SANDBOX_LEASE_SECONDS,
    SILENT_PROBE_CMD,
    WORKLOAD_CAP_TIMEOUT_SECONDS,
    WORKLOAD_CGROUPS,
    WORKLOAD_MEMORY_RESERVE_KB,
    WORKLOAD_PIDS_MAX,
    WORKSPACE_ENSURE_TIMEOUT_SECONDS,
    E2BCarrier,
    build_e2b_carrier,
)

from ufo.config import BlobConfig, Config, DatabaseConfig, SandboxConfig
from ufo.harness import o11y
from ufo.harness.sandbox.client_binary import client_binary
from ufo.harness.sandbox.select import select_carriers
from ufo.harness.sandbox.session import (
    CA_SANDBOX_PATH,
    CA_STAGING_PATH,
    SANDBOX_SIZES,
    WORKSPACE_DIR,
    ExecResult,
    ProxyEndpoint,
    SandboxHandle,
    SandboxProviderUnavailable,
    SandboxSession,
    SandboxSpec,
    SandboxUnreachable,
)
from ufo.runtime.tools.tasks import (
    MAX_COMMAND_TIMEOUT_MS,
    TASK_PROBE,
)


@dataclass
class _Result:
    stdout: str
    stderr: str
    exit_code: int


class _Clock:
    """A hand-wound monotonic clock, so a lease running out is a fact a test states rather than a
    wall-clock wait. The carrier and the provider read the same one, which is what lets a test say
    "the turn went quiet for twenty minutes" and have the container pause exactly as it would."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class _Provider:
    """One sandbox's lifetime as e2b keeps it: the timeout is a wall clock and reaching it pauses
    the container, `connect` resumes a paused sandbox and sets its span, and a sandbox the provider
    no longer has is absent from `_Sdk.sandboxes`, where `connect` is the only call that answers and
    it answers not-found. Modelled only as far as the carrier can observe it — the service's other
    documented behaviours are recorded where they are load-bearing, in the carrier itself."""

    clock: _Clock
    expires_at: float

    def lease(self, span: int) -> None:
        self.expires_at = self.clock() + span

    @property
    def paused(self) -> bool:
        return self.clock() >= self.expires_at


@dataclass
class _Handle:
    """A launched command envd still holds: the pid the carrier signals, and the wait that yields
    the outcome. Its deadline severs this wait and never the command — `alive` keeps the pid until
    something signals its group, which is the whole behaviour a stopped command turns on."""

    commands: "_Commands"
    pid: int
    cmd: str

    async def wait(self) -> _Result:
        result = await self.commands.outcome(self.cmd)
        self.commands.alive.pop(self.pid, None)
        return result


@dataclass
class _Commands:
    runs: list[tuple[str, str | None, float | None]] = field(default_factory=list)
    hangs: bool = False
    hang_on: tuple[str, ...] = ()
    users: list[str | None] = field(default_factory=list)
    envs: list[dict[str, str] | None] = field(default_factory=list)
    result: _Result = field(default_factory=lambda: _Result("out", "", 0))
    raises: Exception | None = None
    fail_on: tuple[str, ...] = ()
    fail_counts: dict[str, int] = field(default_factory=dict)
    timeout_on: tuple[str, ...] = ()
    timeout_counts: dict[str, int] = field(default_factory=dict)
    alive: dict[int, str] = field(default_factory=dict)
    stops_fail: bool = False
    stops_reject: bool = False
    launch_never_answers: bool = False
    """A box that answers nothing at all — its launch, its stop and any probe alike, which is what
    a frozen `envd` looks like from outside."""
    launch_hangs: bool = False
    """A launch the provider holds without answering: the window in which the caller has no pid for
    a command that may already be running."""
    _next_pid: int = 2000

    async def outcome(self, cmd: str) -> _Result:
        if self.hangs or any(token in cmd for token in self.hang_on):
            await asyncio.Event().wait()
        if any(token in cmd for token in self.fail_on):
            raise CommandExitException(stderr="", stdout="", exit_code=1, error="not mounted")
        for token, remaining in self.fail_counts.items():
            if token in cmd and remaining > 0:
                self.fail_counts[token] -= 1
                raise CommandExitException(
                    stderr="trust failed", stdout="", exit_code=1, error="trust failed"
                )
        if any(token in cmd for token in self.timeout_on):
            raise TimeoutException("probe hung")
        for token, remaining in self.timeout_counts.items():
            if token in cmd and remaining > 0:
                self.timeout_counts[token] -= 1
                raise TimeoutException("probe hung")
        if self.raises is not None:
            raise self.raises
        return self.result

    async def run(
        self,
        cmd: str,
        *,
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        user: str | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
        background: bool = False,
    ) -> _Result | _Handle:
        self.runs.append((cmd, cwd, timeout))
        if self.launch_never_answers:
            raise TimeoutException("the box answers nothing")
        signalled = re.fullmatch(r"kill -9 -(\d+)", cmd)
        if signalled:
            if self.stops_fail:
                raise TimeoutException("stop hung")
            if self.stops_reject:
                raise CommandExitException(
                    stderr="No such process", stdout="", exit_code=1, error="no such process"
                )
            self.alive.pop(int(signalled.group(1)), None)
            return _Result("", "", 0)
        self.users.append(user)
        self.envs.append(envs)
        if self.launch_hangs and background:
            await asyncio.Event().wait()
        if not background:
            return await self.outcome(cmd)
        self._next_pid += 1
        self.alive[self._next_pid] = cmd
        return _Handle(self, self._next_pid, cmd)


@dataclass
class _ProcessHandle:
    proc: asyncio.subprocess.Process
    pid: int
    deadline: float | None

    async def wait(self) -> _Result:
        try:
            async with asyncio.timeout(self.deadline):
                stdout, stderr = await self.proc.communicate()
        except TimeoutError:
            raise TimeoutException("timed out") from None
        code = self.proc.returncode or 0
        if code != 0:
            raise CommandExitException(
                stderr=stderr.decode(), stdout=stdout.decode(), exit_code=code, error=""
            )
        return _Result(stdout.decode(), stderr.decode(), 0)


@dataclass
class _ProcessCommands:
    """envd as real processes: every command the carrier sends runs as a local shell, so what a
    signal ends and what survives it is read back from live pids and files rather than modelled.
    Stands in for the transport only — the launched supervisor, its pid file and its exit file are
    the real things asserted. The container's workspace path becomes `root`, and a host without a
    `setsid` binary gets one on PATH that does what util-linux's does for a non-leader — setsid(2),
    then exec.

    A launch `exec`s, so the pid handed back is the command's own as envd's is: a shell that stays
    in front of it holds a pid in the *launcher's* group, and every signal the carrier aims at that
    pid would then name a group the command was never in. Only a launch — the signal that follows
    is a shell builtin, which `exec` cannot replace a shell with."""

    root: Path
    launched: list[asyncio.subprocess.Process] = field(default_factory=list)

    async def run(
        self,
        cmd: str,
        *,
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        user: str | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
        background: bool = False,
    ) -> _Result | _ProcessHandle:
        proc = await asyncio.create_subprocess_shell(
            f"exec {cmd}" if background else cmd,
            cwd=self.root,
            env=self._env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if background:
            self.launched.append(proc)
            return _ProcessHandle(proc=proc, pid=proc.pid, deadline=timeout)
        stdout, stderr = await proc.communicate()
        code = proc.returncode or 0
        if code != 0:
            raise CommandExitException(
                stderr=stderr.decode(), stdout=stdout.decode(), exit_code=code, error=""
            )
        return _Result(stdout.decode(), stderr.decode(), 0)

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if shutil.which("setsid") is None:
            shim = self.root / "bin" / "setsid"
            if not shim.exists():
                shim.parent.mkdir(parents=True, exist_ok=True)
                shim.write_text(
                    "#!/bin/sh\n"
                    f'exec "{sys.executable}" -c '
                    "'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' \"$@\"\n"
                )
                shim.chmod(0o755)
            env["PATH"] = f"{shim.parent}:{env['PATH']}"
        return env


@dataclass
class _Stream:
    """The SDK's streamed reader, standing in only for the transport: it counts its own closes, so
    the carrier's release of an open connection is asserted rather than assumed."""

    chunks: tuple[bytes, ...]
    files: "_Files"
    served: int = 0

    def __aiter__(self) -> "_Stream":
        return self

    async def __anext__(self) -> bytes:
        if self.served >= len(self.chunks):
            raise StopAsyncIteration
        self.served += 1
        return self.chunks[self.served - 1]

    async def aclose(self) -> None:
        self.files.closed += 1


@dataclass
class _Files:
    written: list[tuple[str, str | bytes]] = field(default_factory=list)
    write_users: list[str | None] = field(default_factory=list)
    reads: list[tuple[str, str]] = field(default_factory=list)
    chunks: tuple[bytes, ...] = ()
    closed: int = 0
    missing: bool = False
    raises: Exception | None = None
    raise_writes: int | None = None
    hangs: bool = False

    async def read(self, path: str, format: str) -> _Stream:
        if self.missing:
            raise FileNotFoundException(f"{path} not found")
        self.reads.append((path, format))
        return _Stream(chunks=self.chunks, files=self)

    async def write(self, path: str, data: str | bytes, *, user: str | None = None) -> object:
        if self.hangs:
            await asyncio.Event().wait()
        if self.raises is not None and (self.raise_writes is None or self.raise_writes > 0):
            if self.raise_writes is not None:
                self.raise_writes -= 1
            raise self.raises
        self.written.append((path, data))
        self.write_users.append(user)
        return None


@dataclass
class _Sandbox:
    sandbox_id: str
    provider: _Provider
    commands: _Commands
    files: _Files
    traffic_access_token: str | None = "traffic-tok"

    def get_host(self, port: int) -> str:
        return f"{port}-{self.sandbox_id}.e2b.test"


@dataclass
class _Sdk:
    clock: _Clock = field(default_factory=_Clock)
    created: list[dict[str, object]] = field(default_factory=list)
    connected: list[str] = field(default_factory=list)
    connect_leases: list[int] = field(default_factory=list)
    sandboxes: dict[str, _Sandbox] = field(default_factory=dict)
    counter: int = 0
    command_fail_on: tuple[str, ...] = ()
    command_fail_counts: dict[str, int] = field(default_factory=dict)
    command_timeout_on: tuple[str, ...] = ()
    command_timeout_counts: dict[str, int] = field(default_factory=dict)
    command_hangs: bool = False
    on_call: Callable[[], None] | None = None
    traffic_access_token: str | None = "traffic-tok"
    file_write_raises: Exception | None = None
    file_write_raise_count: int | None = None
    connect_faults: list[Exception] = field(default_factory=list)

    async def create(
        self,
        *,
        template: str,
        timeout: int,  # noqa: ASYNC109
        metadata: dict[str, str],
        lifecycle: object,
        network: object,
        api_key: str,
    ) -> _Sandbox:
        if self.on_call is not None:
            self.on_call()
        self.counter += 1
        sandbox_id = f"sbx-{self.counter}"
        provider = _Provider(clock=self.clock, expires_at=self.clock() + timeout)
        sandbox = _Sandbox(
            sandbox_id=sandbox_id,
            provider=provider,
            traffic_access_token=self.traffic_access_token,
            commands=_Commands(
                hangs=self.command_hangs,
                fail_on=self.command_fail_on,
                fail_counts=dict(self.command_fail_counts),
                timeout_on=self.command_timeout_on,
                timeout_counts=dict(self.command_timeout_counts),
            ),
            files=_Files(raises=self.file_write_raises, raise_writes=self.file_write_raise_count),
        )
        self.sandboxes[sandbox_id] = sandbox
        self.created.append(
            {
                "template": template,
                "timeout": timeout,
                "metadata": metadata,
                "lifecycle": lifecycle,
                "network": network,
                "api_key": api_key,
            }
        )
        return sandbox

    async def connect(
        self,
        sandbox_id: str,
        *,
        timeout: int,  # noqa: ASYNC109
        api_key: str,
    ) -> _Sandbox:
        if self.on_call is not None:
            self.on_call()
        self.connected.append(sandbox_id)
        self.connect_leases.append(timeout)
        if self.connect_faults:
            raise self.connect_faults.pop(0)
        sandbox = self.sandboxes.get(sandbox_id)
        if sandbox is None:
            raise SandboxNotFoundException(f"Paused sandbox {sandbox_id} not found")
        sandbox.provider.lease(timeout)
        return sandbox


PROXY_PUBLIC_URL = "https://sandbox-proxy.test"


def _templates(reference: str) -> dict[str, str]:
    return dict.fromkeys(SANDBOX_SIZES, reference)


def _carrier(**kwargs: object) -> E2BCarrier:
    return E2BCarrier(client=b"client-binary", **kwargs)


def _spec(conversation: UUID, size: str | None = "small", turn: UUID | None = None) -> SandboxSpec:
    return SandboxSpec(
        conversation_id=conversation,
        image_ref="ufo-sandbox:latest",
        workspace_host_path="/tmp/ws",
        proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem", public_url=PROXY_PUBLIC_URL),
        run_token="run-token",
        size=size,
        turn_id=turn,
    )


async def test_create_opens_the_template_of_the_specs_size() -> None:
    sdk = _Sdk()
    carrier = _carrier(
        api_key="k",
        templates={"small": "tpl-s", "medium": "tpl-m", "large": "tpl-l"},
        sdk=sdk,
    )

    await carrier.create(_spec(uuid4(), size="large"))

    assert sdk.created[0]["template"] == "tpl-l"


async def test_create_refuses_a_size_no_template_serves() -> None:
    """A spec with no size reaching a sizing carrier is a threading fault upstream, not a case to
    default quietly — the sandbox it would open is not the one the agent's row names."""
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("tpl"), sdk=sdk)

    with pytest.raises(RuntimeError, match="sandbox size"):
        await carrier.create(_spec(uuid4(), size=None))
    assert sdk.created == []


async def test_resume_ignores_the_size_and_reconnects(monkeypatch: pytest.MonkeyPatch) -> None:
    """An existing sandbox keeps the size it was created at: the resume path never consults the
    template map, so a changed agent setting shapes only the next fresh sandbox."""
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("tpl"), sdk=sdk)
    first = await carrier.create(_spec(uuid4(), size="small"))

    resumed = await carrier.create(
        replace(_spec(first.conversation_id, size="large"), resume_id=first.container_id)
    )

    assert resumed.container_id == first.container_id
    assert len(sdk.created) == 1


async def test_create_opens_a_sandbox_on_the_template_and_returns_its_handle() -> None:
    sdk = _Sdk()
    carrier = _carrier(api_key="key-1", templates=_templates("tpl-1"), sdk=sdk)
    conversation = uuid4()

    handle = await carrier.create(_spec(conversation))

    assert handle.conversation_id == conversation
    assert handle.container_id == "sbx-1"
    created = sdk.created[0]
    assert created["template"] == "tpl-1"
    assert created["api_key"] == "key-1"
    assert created["lifecycle"] == E2B_LIFECYCLE
    assert created["network"] == E2B_NETWORK
    assert created["metadata"] == {CONVERSATION_METADATA_KEY: str(conversation)}
    runs = sdk.sandboxes["sbx-1"].commands.runs
    assert (ENSURE_WORKSPACE_COMMAND, None, WORKSPACE_ENSURE_TIMEOUT_SECONDS) in runs


async def test_create_fails_loud_when_no_traffic_token_returns() -> None:
    """The token is not on the handle any more — `dial` reads it off the live container — but a
    template that returns none at create leaves every port unreachable through the ingress, so the
    create is where that is caught."""
    sdk = _Sdk(traffic_access_token=None)
    carrier = _carrier(api_key="k", templates=_templates("tpl"), sdk=sdk)

    with pytest.raises(RuntimeError, match="traffic access token"):
        await carrier.create(_spec(uuid4()))


async def test_every_preparation_caps_the_workload_cgroups_as_root() -> None:
    """The ceilings are kernel state a pause snapshots and a resume restores, but nothing vouches
    for a box this process did not prepare itself — so every preparation path re-asserts them, on
    the fresh box, the cached one, and the reconnected one alike."""
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    await carrier.create(_spec(conversation))
    await carrier.create(_spec(conversation))
    other = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    await other.create(replace(_spec(conversation), resume_id="sbx-1"))

    commands = sdk.sandboxes["sbx-1"].commands
    capped = [index for index, run in enumerate(commands.runs) if run[0] == CAP_WORKLOAD_COMMAND]
    assert len(capped) == 3
    assert all(commands.users[index] == "root" for index in capped)
    assert commands.runs[capped[0]] == (CAP_WORKLOAD_COMMAND, None, WORKLOAD_CAP_TIMEOUT_SECONDS)


async def test_a_failed_trust_update_raises_a_named_ca_install_error() -> None:
    """The CA install is the precondition for every HTTPS call a sandbox makes, so its failure
    detail is what an operator reads when egress starts refusing. Mapping `CommandExitException`
    to a named RuntimeError is all that stands between them and a bare provider exception. The exit
    is the box's own deterministic answer, the same one a further attempt would get, so it is raised
    on the first attempt rather than retried."""
    sdk = _Sdk(command_fail_counts={"update-ca-certificates": 1})
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)

    with pytest.raises(RuntimeError, match="sandbox CA install failed"):
        await carrier.create(_spec(uuid4()))

    runs = sdk.sandboxes["sbx-1"].commands.runs
    assert CLIENT_PATH in runs[0][0]
    assert [command for command, _, _ in runs[1:]] == [INSTALL_CA_COMMAND]


async def test_a_cached_box_whose_preparation_failed_drops_its_lease() -> None:
    """The lease's deadline is evidence only while the container answers to it, and this one just
    did not — so it goes, and the next open reaches the provider instead of reading it back."""
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk, prepare_retry_seconds=0.0)
    spec = _spec(uuid4())

    await carrier.create(spec)
    sdk.sandboxes["sbx-1"].files.raises = httpx.ReadError("connection broken")

    with pytest.raises(httpx.ReadError):
        await carrier.create(spec)

    assert carrier._leased(spec.conversation_id) is None


async def test_a_dropped_ca_upload_is_tried_again_on_the_same_new_box(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sdk = _Sdk(
        file_write_raises=httpx.ReadError("envd dropped the connection"),
        file_write_raise_count=1,
    )
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk, prepare_retry_seconds=0.0)
    conversation = uuid4()

    with caplog.at_level(logging.INFO, logger="ufo"):
        handle = await carrier.create(_spec(conversation))

    assert handle.container_id == "sbx-1"
    assert len(sdk.created) == 1
    assert _events(caplog, "sandbox.e2b.prepare_retried") == [
        {
            "conversation_id": str(conversation),
            "sandbox_id": "sbx-1",
            "error_class": "ReadError",
        }
    ]
    sandbox = sdk.sandboxes["sbx-1"]
    assert sandbox.files.written == [(CA_STAGING_PATH, "ca-pem")]
    assert CLIENT_PATH in sandbox.commands.runs[0][0]
    assert [command for command, _, _ in sandbox.commands.runs[1:]] == [
        INSTALL_CA_COMMAND,
        ENSURE_WORKSPACE_COMMAND,
        CAP_WORKLOAD_COMMAND,
    ]


async def test_a_drop_that_outlasts_the_attempts_ends_the_turn_on_the_provider_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The attempts are what bounds the retry, so the last one's fault is the answer — the member
    reads the provider's failure rather than a wait that never ends."""
    reader = _counters(monkeypatch)
    sdk = _Sdk(
        file_write_raises=httpx.ReadError("envd dropped the connection"),
        file_write_raise_count=PREPARE_ATTEMPTS,
    )
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk, prepare_retry_seconds=0.0)
    conversation = uuid4()

    with pytest.raises(httpx.ReadError):
        await carrier.create(_spec(conversation))

    assert _counted(reader, "ufo.sandbox_prepare_retried_total") == [
        (PREPARE_ATTEMPTS - 1, {"carrier": CARRIER_NAME})
    ]
    assert carrier._leased(conversation) is None


async def test_a_resumed_box_whose_client_upload_drops_fails_closed() -> None:
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    opened = await carrier.create(_spec(conversation))
    sandbox = sdk.sandboxes[opened.container_id]
    sandbox.commands.fail_counts[CLIENT_PATH] = 1
    sandbox.files.raises = httpx.ReadError("envd dropped it")

    restarted = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    with pytest.raises(SandboxUnreachable, match="workload client"):
        await restarted.create(replace(_spec(conversation), resume_id=opened.container_id))

    assert restarted._leased(conversation) is None


async def test_a_resume_the_control_plane_never_answers_is_retried(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The three turns #1346 lost: `POST /sandboxes/{id}/connect` read-timed out and the turn died
    before its first round. The provider's control plane is off this cluster and `connect` opens
    nothing, so re-issuing it converges on the one container the id names."""
    sdk = _Sdk(connect_faults=[httpx.ReadTimeout("timed out")])
    carrier = _carrier(
        api_key="k", templates=_templates("t"), sdk=sdk, resume_retry_delay_seconds=0.0
    )
    conversation = uuid4()
    opened = await carrier.create(_spec(conversation))
    sdk.connected.clear()

    with caplog.at_level(logging.INFO, logger="ufo"):
        resumed = await carrier.create(replace(_spec(conversation), resume_id=opened.container_id))

    assert resumed.container_id == opened.container_id
    assert sdk.connected == [opened.container_id, opened.container_id]
    assert len(sdk.created) == 1
    assert [entry["attempt"] for entry in _events(caplog, "sandbox.e2b.resume_retried")] == [1]


@pytest.mark.parametrize(
    "fault",
    (
        SandboxException("429: Rate limit exceeded"),
        SandboxException("500: Error when setting sandbox timeout"),
    ),
)
async def test_a_retryable_control_plane_answer_is_retried(fault: SandboxException) -> None:
    sdk = _Sdk(connect_faults=[fault])
    carrier = _carrier(
        api_key="k", templates=_templates("t"), sdk=sdk, resume_retry_delay_seconds=0.0
    )
    conversation = uuid4()
    opened = await carrier.create(_spec(conversation))
    sdk.connected.clear()

    resumed = await carrier.create(replace(_spec(conversation), resume_id=opened.container_id))

    assert resumed.container_id == opened.container_id
    assert sdk.connected == [opened.container_id, opened.container_id]


async def test_a_final_control_plane_answer_is_not_retried() -> None:
    sdk = _Sdk(connect_faults=[SandboxException("400: invalid timeout")])
    carrier = _carrier(
        api_key="k", templates=_templates("t"), sdk=sdk, resume_retry_delay_seconds=0.0
    )
    conversation = uuid4()
    opened = await carrier.create(_spec(conversation))
    sdk.connected.clear()

    with pytest.raises(SandboxException, match="400: invalid timeout"):
        await carrier.create(replace(_spec(conversation), resume_id=opened.container_id))

    assert sdk.connected == [opened.container_id]


async def test_retryable_control_plane_answers_exhaust_to_provider_unavailable() -> None:
    faults = [SandboxException("500: Error when setting sandbox timeout")] * (RESUME_RETRIES + 1)
    sdk = _Sdk(connect_faults=list(faults))
    carrier = _carrier(
        api_key="k", templates=_templates("t"), sdk=sdk, resume_retry_delay_seconds=0.0
    )
    conversation = uuid4()
    opened = await carrier.create(_spec(conversation))
    sdk.connected.clear()

    with pytest.raises(SandboxProviderUnavailable) as raised:
        await carrier.create(replace(_spec(conversation), resume_id=opened.container_id))

    assert isinstance(raised.value.__cause__, SandboxException)
    assert sdk.connected == [opened.container_id] * len(faults)


async def test_a_resume_the_control_plane_keeps_dropping_raises_bounded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A member waits on this, so the retry is bounded: past the budget the fault is the answer,
    and it never becomes a fresh box — the paused container the id names holds the workspace."""
    faults = [httpx.ReadTimeout("timed out")] * (RESUME_RETRIES + 1)
    sdk = _Sdk(connect_faults=list(faults))
    carrier = _carrier(
        api_key="k", templates=_templates("t"), sdk=sdk, resume_retry_delay_seconds=0.0
    )
    conversation = uuid4()
    opened = await carrier.create(_spec(conversation))
    sdk.connected.clear()

    with caplog.at_level(logging.INFO, logger="ufo"), pytest.raises(SandboxProviderUnavailable):
        await carrier.create(replace(_spec(conversation), resume_id=opened.container_id))

    assert sdk.connected == [opened.container_id] * len(faults)
    assert len(sdk.created) == 1
    assert _events(caplog, "sandbox.e2b.resume_unavailable") == [
        {
            "conversation_id": str(conversation),
            "sandbox_id": opened.container_id,
            "attempts": RESUME_RETRIES + 1,
            "error_class": "ReadTimeout",
        }
    ]


async def test_the_resume_backoff_doubles_and_the_whole_retry_is_wall_clock_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The schedule a member actually waits out. Every other retry test zeroes the delay, so the
    doubling and the production default are unobserved there — this one records the sleeps."""
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(e2b_ext.asyncio, "sleep", record)
    faults = [httpx.ReadTimeout("timed out")] * (RESUME_RETRIES + 1)
    sdk = _Sdk(connect_faults=list(faults))
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    opened = await carrier.create(_spec(conversation))

    with pytest.raises(SandboxProviderUnavailable):
        await carrier.create(replace(_spec(conversation), resume_id=opened.container_id))

    assert slept == [RESUME_RETRY_DELAY_SECONDS, RESUME_RETRY_DELAY_SECONDS * 2]
    assert sum(slept) < RESUME_TOTAL_TIMEOUT_SECONDS


def test_ca_install_command_removes_a_target_after_a_failed_bundle_update(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging.pem"
    installed = tmp_path / "installed.crt"
    staging.write_text("ca-pem")
    paths = (
        (CA_STAGING_PATH, shlex.quote(str(staging))),
        (CA_SANDBOX_PATH, shlex.quote(str(installed))),
    )
    failing = (
        INSTALL_CA_COMMAND.replace(*paths[0])
        .replace(*paths[1])
        .replace("/usr/sbin/update-ca-certificates", "false")
    )

    failed = subprocess.run(["bash", "-c", failing], check=False)

    assert failed.returncode != 0
    assert not installed.exists()

    succeeding = (
        INSTALL_CA_COMMAND.replace(*paths[0])
        .replace(*paths[1])
        .replace("/usr/sbin/update-ca-certificates", "true")
    )

    succeeded = subprocess.run(["bash", "-c", succeeding], check=False)

    assert succeeded.returncode == 0
    assert installed.read_text() == "ca-pem"


def _cap_command_against(tmp_path: Path, total_kb: int) -> tuple[str, dict[str, Path]]:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemTotal:       {total_kb} kB\nMemFree:        1024 kB\n")
    directories = {cgroup: tmp_path / Path(cgroup).name for cgroup in WORKLOAD_CGROUPS}
    command = CAP_WORKLOAD_COMMAND.replace("/proc/meminfo", shlex.quote(str(meminfo)))
    for cgroup, directory in directories.items():
        directory.mkdir()
        command = command.replace(cgroup, str(directory))
    return command, directories


def test_cap_workload_command_writes_the_ceilings_from_the_guest_total(tmp_path: Path) -> None:
    command, directories = _cap_command_against(tmp_path, total_kb=4_024_320)

    done = subprocess.run(["bash", "-c", command], check=False)

    assert done.returncode == 0
    expected_bytes = (4_024_320 - WORKLOAD_MEMORY_RESERVE_KB) * 1024
    for directory in directories.values():
        assert (directory / "memory.max").read_text().strip() == str(expected_bytes)
        assert (directory / "pids.max").read_text().strip() == str(WORKLOAD_PIDS_MAX)


def test_cap_workload_command_refuses_a_guest_smaller_than_the_reserve(tmp_path: Path) -> None:
    command, directories = _cap_command_against(tmp_path, total_kb=262_144)

    done = subprocess.run(["bash", "-c", command], check=False)

    assert done.returncode != 0
    for directory in directories.values():
        assert not (directory / "memory.max").exists()


async def test_create_without_a_reachable_proxy_url_fails_loud() -> None:
    """The carrier's own fail-loud, behind the boot guard: an e2b spec whose proxy carries no public
    URL cannot build a metered egress env, so create raises rather than run an open sandbox."""
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    spec = SandboxSpec(
        conversation_id=uuid4(),
        image_ref="ufo-sandbox:latest",
        workspace_host_path="/tmp/ws",
        proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem"),
        run_token="run-token",
    )
    with pytest.raises(RuntimeError, match="proxy_public_url"):
        await carrier.create(spec)


async def test_create_with_a_plaintext_proxy_url_fails_loud() -> None:
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    spec = SandboxSpec(
        conversation_id=uuid4(),
        image_ref="ufo-sandbox:latest",
        workspace_host_path="/tmp/ws",
        proxy=ProxyEndpoint(
            port=8080,
            ca_cert="ca-pem",
            public_url="http://sandbox-proxy.test:8888",
        ),
        run_token="run-token",
    )
    with pytest.raises(RuntimeError, match="HTTPS"):
        await carrier.create(spec)


async def test_second_create_for_the_conversation_resumes_rather_than_recreates() -> None:
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()

    await carrier.create(_spec(conversation))
    handle = await carrier.create(_spec(conversation))

    assert len(sdk.created) == 1
    assert sdk.connected == ["sbx-1"]
    assert handle.container_id == "sbx-1"
    commands = [command for command, _, _ in sdk.sandboxes["sbx-1"].commands.runs]
    assert [command for command in commands if command == INSTALL_CA_COMMAND] == [
        INSTALL_CA_COMMAND,
        INSTALL_CA_COMMAND,
    ]


async def test_create_resumes_a_prior_process_sandbox_and_exec_works() -> None:
    """Cross-process resume: a fresh carrier (empty in-process maps, as after a serve restart) given
    the conversation's stored id via spec.resume_id connects to that sandbox rather than opening a
    new one, and rebuilds the egress env through create so the following exec never KeyErrors on a
    missing _egress — create is the one seam that seeds it, and every turn opens through create."""
    sdk = _Sdk()
    first = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    opened = await first.create(_spec(conversation))

    restarted = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    resumed = await restarted.create(replace(_spec(conversation), resume_id=opened.container_id))

    assert len(sdk.created) == 1
    assert sdk.connected == [opened.container_id]
    assert resumed.container_id == opened.container_id
    commands = [command for command, _, _ in sdk.sandboxes["sbx-1"].commands.runs]
    assert [command for command in commands if command == INSTALL_CA_COMMAND] == [
        INSTALL_CA_COMMAND,
        INSTALL_CA_COMMAND,
    ]
    result = await restarted.exec(resumed, ("bash", "-lc", "echo hi"), 60)
    assert result.exit_code == 0


async def test_a_resumed_box_whose_runtime_command_stream_hangs_still_opens(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The case the bound exists for, and the only one no SDK deadline reaches: `commands.run` goes
    out over a stream the SDK gives no read timeout, so a silent `envd` holds it open forever. The
    upload answers here, so the hang this tolerates is the command's alone."""
    sdk = _Sdk()
    first = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    opened = await first.create(_spec(conversation))
    sandbox = sdk.sandboxes[opened.container_id]
    sandbox.files.written.clear()
    sandbox.commands.hang_on = (INSTALL_CA_COMMAND,)

    restarted = _carrier(
        api_key="k", templates=_templates("t"), sdk=sdk, resume_prepare_seconds=0.01
    )
    with caplog.at_level(logging.INFO, logger="ufo"):
        resumed = await restarted.create(
            replace(_spec(conversation), resume_id=opened.container_id)
        )

    assert resumed.container_id == opened.container_id
    assert sandbox.files.written == [(CA_STAGING_PATH, "ca-pem")]
    assert _events(caplog, "sandbox.e2b.prepare_deferred") == [
        {"conversation_id": str(conversation), "sandbox_id": opened.container_id}
    ]


async def test_a_resumed_box_whose_client_upload_hangs_fails_closed() -> None:
    sdk = _Sdk()
    first = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    opened = await first.create(_spec(conversation))
    sandbox = sdk.sandboxes[opened.container_id]
    sandbox.commands.runs.clear()
    sandbox.commands.fail_counts[CLIENT_PATH] = 1
    sandbox.files.hangs = True

    restarted = _carrier(
        api_key="k", templates=_templates("t"), sdk=sdk, resume_prepare_seconds=0.01
    )
    with pytest.raises(SandboxUnreachable, match="workload client"):
        await restarted.create(replace(_spec(conversation), resume_id=opened.container_id))

    assert len(sandbox.commands.runs) == 1
    assert CLIENT_PATH in sandbox.commands.runs[0][0]
    assert restarted._leased(conversation) is None


async def test_a_fresh_box_is_never_served_on_a_deferral() -> None:
    """Only a box the durable handle vouches for may defer. A fresh one holds no CA and no
    `/workspace`, so its preparation has no deadline to expire into — routing it through the
    deferring branch would hand the turn a container that reaches no host."""
    sdk = _Sdk()
    sdk.command_hangs = True
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk, resume_prepare_seconds=0.01)

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.2):
            await carrier.create(_spec(uuid4()))


async def test_a_replacement_for_a_lost_resume_id_is_never_deferred() -> None:
    """The one path where "the durable id is set" and "this is the box that id named" come apart:
    the provider no longer has that sandbox, so a fresh one opens in its place. The id vouches for
    a container that is gone, not for this one, which holds no CA and no `/workspace` — so its
    preparation is strict and has no deadline to expire into."""
    sdk = _Sdk(command_hangs=True)
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk, resume_prepare_seconds=0.01)

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.2):
            await carrier.create(replace(_spec(uuid4()), resume_id="gone-1"))

    assert sdk.connected == ["gone-1"]
    assert len(sdk.created) == 1


def _counters(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    monkeypatch.setattr(o11y.metrics, "get_meter", MeterProvider(metric_readers=[reader]).get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    return reader


def _counted(reader: InMemoryMetricReader, name: str) -> list[tuple[int, dict[str, object]]]:
    return [
        (point.value, dict(point.attributes or {}))
        for resource in reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


async def test_exec_runs_the_joined_command_in_the_workspace_and_maps_the_result() -> None:
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))
    sdk.sandboxes["sbx-1"].commands.result = _Result("hello\n", "", 0)

    result = await carrier.exec(handle, ("bash", "-lc", "echo hi"), 60)

    assert result == ExecResult(stdout="hello\n", stderr="", exit_code=0)
    command, cwd, timeout = sdk.sandboxes["sbx-1"].commands.runs[-1]
    assert command == "setsid bash -lc 'echo hi'"
    assert cwd == WORKSPACE_DIR
    assert timeout == 60


async def test_exec_maps_a_timeout_to_the_timeout_code() -> None:
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))
    sdk.sandboxes["sbx-1"].commands.raises = TimeoutException("timed out")

    result = await carrier.exec(handle, ("bash", "-lc", "sleep 999"), 1)

    assert result.exit_code == EXEC_TIMEOUT_CODE
    assert result.timed_out_after_s == 1
    assert "timed out" in result.stderr


async def test_a_stop_the_sandbox_refuses_still_reports_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caller's own deadline is the outcome; a cleanup that cannot land counts rather than
    replacing that outcome with an error about itself."""
    reader = _counters(monkeypatch)
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))
    commands = sdk.sandboxes["sbx-1"].commands
    commands.raises = TimeoutException("timed out")
    commands.stops_fail = True

    result = await carrier.exec(handle, ("bash", "-lc", "pytest -n auto"), 60)

    assert result.exit_code == EXEC_TIMEOUT_CODE
    assert result.timed_out_after_s == 60
    assert _counted(reader, "ufo.sandbox_exec_stop_failed_total") == [
        (1, {"carrier": CARRIER_NAME})
    ]


async def _until(condition: Callable[[], bool]) -> None:
    """Let the carrier's own task run until it reaches the state a cancel is aimed at, so the cancel
    lands at a known point rather than wherever a fixed number of loop turns leaves it."""
    for _ in range(1_000):
        if condition():
            await asyncio.sleep(0)
            return
        await asyncio.sleep(0)
    raise AssertionError("the carrier never reached the state the cancel is aimed at")


async def test_a_cancelled_command_keeps_running_and_leaves_no_lease(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An executor preemption — a deploy roll, a pod death — cancels the step and replays it, and
    the replay finds the command's work done only if the command was left alone. Nothing at this
    call site tells that cancel from a member's stop, so the group runs on and the stop is issued
    from where the difference is known. The lease goes all the same: a cancel says nothing about how
    long the container still answers."""
    sdk, carrier = _leased(_Clock())
    handle = await carrier.create(_spec(uuid4()))
    commands = sdk.sandboxes["sbx-1"].commands
    commands.hangs = True

    with caplog.at_level(logging.INFO, logger="ufo"):
        running = asyncio.ensure_future(carrier.exec(handle, ("bash", "-lc", "pytest -n auto"), 60))
        await _until(lambda: bool(commands.alive))
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

    assert list(commands.alive.values()) == ["setsid bash -lc 'pytest -n auto'"]
    assert not any(cmd.startswith("kill -9 -") for cmd, _, _ in commands.runs)
    assert _events(caplog, "sandbox.e2b.lease_dropped") == [
        {"conversation_id": str(handle.conversation_id), "during": "cancel"}
    ]
    commands.hangs = False
    recovered = await carrier.exec(handle, ("bash", "-lc", "echo back"), 60)

    assert recovered.exit_code == 0
    assert sdk.connected == ["sbx-1"]


async def test_a_member_cancel_stops_the_group_the_cancelled_step_left_running() -> None:
    """The stop a member's cancel earns, reached the way the engine reaches it — through the
    session, so the capability the carrier declares is what carries the signal. The turn is over on
    that path, so the command answers to nobody and its whole group ends."""
    sdk, carrier = _leased(_Clock())
    handle = await carrier.create(_spec(uuid4()))
    commands = sdk.sandboxes["sbx-1"].commands
    commands.hangs = True
    running = asyncio.ensure_future(carrier.exec(handle, ("bash", "-lc", "pytest -n auto"), 60))
    await _until(lambda: bool(commands.alive))
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    (pid,) = commands.alive

    await SandboxSession(carrier=carrier, handle=handle).stop_commands()

    assert commands.alive == {}
    assert [cmd for cmd, _, _ in commands.runs if cmd.startswith("kill")] == [f"kill -9 -{pid}"]


async def test_a_cancel_before_the_launch_answers_names_no_group(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A launch still in flight leaves no pid, so there is no group to signal and nothing here
    invents one. The lease is dropped all the same: the next call reattaches to the container rather
    than trusting a deadline this call never finished using."""
    sdk, carrier = _leased(_Clock())
    handle = await carrier.create(_spec(uuid4()))
    commands = sdk.sandboxes["sbx-1"].commands
    commands.launch_hangs = True

    with caplog.at_level(logging.INFO, logger="ufo"):
        running = asyncio.ensure_future(carrier.exec(handle, ("bash", "-lc", "pytest -n auto"), 60))
        await _until(lambda: any("pytest" in cmd for cmd, _, _ in commands.runs))
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

    assert not any(cmd.startswith("kill -9 -") for cmd, _, _ in commands.runs)
    assert _events(caplog, "sandbox.e2b.lease_dropped") == [
        {"conversation_id": str(handle.conversation_id), "during": "cancel"}
    ]

    await carrier.stop_commands(handle)

    assert not any(cmd.startswith("kill -9 -") for cmd, _, _ in commands.runs)


async def test_a_box_that_stopped_answering_refuses_the_next_command_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of remembering it. A container whose kernel went down keeps its provider
    record — the control plane answers `running` for one that has not run a command in twenty
    minutes — so without this every later call in the turn waits its own full deadline to learn
    what the one before it already established."""
    reader = _counters(monkeypatch)
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))
    commands = sdk.sandboxes["sbx-1"].commands
    commands.raises = TimeoutException("timed out")
    commands.stops_fail = True
    await carrier.exec(handle, ("bash", "-lc", "pytest -n auto"), 600)
    before = len(commands.runs)

    with pytest.raises(SandboxUnreachable):
        await carrier.exec(handle, ("bash", "-lc", "cat /proc/loadavg"), 600)

    assert [cmd for cmd, _, _ in commands.runs[before:]] == [SILENT_PROBE_CMD]
    assert _counted(reader, "ufo.sandbox_unreachable_total") == [(1, {"carrier": CARRIER_NAME})]


async def test_a_cancel_leaves_the_launcher_running_until_the_member_stop(tmp_path: Path) -> None:
    """Proved on real processes: a cancelled exec leaves the launcher's group running, because the
    replay of a preempted step must find the work it launched still going, and only the member's
    stop ends it. That stop reaches exactly as far as the deadline's does — the launcher dies and
    the task supervisor keeps running, log and exit file intact."""
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    commands = _ProcessCommands(root=tmp_path)
    sdk.sandboxes["sbx-live"] = _Sandbox(
        sandbox_id="sbx-live",
        provider=_Provider(clock=sdk.clock, expires_at=0),
        commands=cast(_Commands, commands),
        files=_Files(),
    )
    handle = SandboxHandle(conversation_id=uuid4(), container_id="sbx-live")
    base = f"{tmp_path}/tasks/t-0002"
    launch = (
        str(client_binary()),
        "run",
        "--task",
        base,
        "--",
        "bash",
        "-lc",
        "sleep 30",
    )

    running = asyncio.ensure_future(carrier.exec(handle, launch, 60))
    for _ in range(200):
        if commands.launched:
            break
        await asyncio.sleep(0.05)
    launcher = commands.launched[0]
    for _ in range(200):
        launched = await carrier.exec(
            handle, ("bash", "-lc", f'cat "{base}.pid" 2>/dev/null || true'), 5
        )
        if launched.stdout.strip():
            break
        await asyncio.sleep(0.05)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert launcher.returncode is None

    await carrier.stop_commands(handle)

    assert await asyncio.wait_for(launcher.wait(), 10) == -9
    probe = await carrier.exec(handle, ("sh", "-c", TASK_PROBE, "sh", base), 5)
    assert probe.exit_code == 0
    supervisor = probe.stdout.strip()
    assert supervisor
    stopped = await carrier.exec(handle, ("bash", "-lc", f'kill "{supervisor}"'), 5)
    assert stopped.exit_code == 0


def _leased(clock: _Clock) -> tuple[_Sdk, E2BCarrier]:
    sdk = _Sdk(clock=clock)
    return sdk, _carrier(api_key="k", templates=_templates("t"), sdk=sdk, clock=clock)


def _events(caplog: pytest.LogCaptureFixture, name: str) -> list[dict[str, object]]:
    return [record.ufo for record in caplog.records if record.getMessage() == name]


async def test_a_standing_lease_covers_short_commands_without_a_round_trip() -> None:
    """A command asks the lease only for its own timeout plus the answer margin, so the common case
    reaches the provider once, for the command itself. Renewing per call would put a control-plane
    round trip in front of every tool call a turn makes."""
    sdk, carrier = _leased(_Clock())
    handle = await carrier.create(_spec(uuid4()))

    for _ in range(5):
        await carrier.exec(handle, ("bash", "-lc", "sleep 100"), 120)

    assert sdk.created[0]["timeout"] == SANDBOX_LEASE_SECONDS
    assert sdk.connected == []


async def test_a_bash_command_at_the_tools_ceiling_never_pauses_mid_run() -> None:
    """The bash tool's ceiling outruns the five-minute autosuspend, so the renewal is what keeps a
    long build or test run alive: the exec buys a lease covering its whole timeout before running,
    and the container cannot pause under it."""
    sdk, carrier = _leased(_Clock())
    handle = await carrier.create(_spec(uuid4()))
    ceiling = MAX_COMMAND_TIMEOUT_MS // 1000

    result = await carrier.exec(handle, ("bash", "-lc", "make test"), ceiling)

    assert result.exit_code == 0
    assert ceiling > SANDBOX_LEASE_SECONDS
    assert sdk.connect_leases == [ceiling + LEASE_MARGIN_SECONDS]
    assert not sdk.sandboxes["sbx-1"].provider.paused


async def test_an_expired_lease_is_dropped_and_the_next_dial_reconnects() -> None:
    """The ingress process only ever dials, never destroys, so a lapsed lease is the map's one
    remaining exit — without it a process that serves a site for every conversation it is asked
    about holds an SDK client per conversation for the life of the pod. The sweep runs on lookup
    and drops every lapsed lease, not only the one being looked up, so a conversation served once
    and never returned to leaves nothing behind; the dial that follows reconnects, which is what
    resumes the container the provider paused."""
    clock = _Clock()
    sdk, carrier = _leased(clock)
    served_once, served_again = uuid4(), uuid4()
    await carrier.create(_spec(served_once))
    handle = await carrier.create(_spec(served_again))
    clock.advance(SANDBOX_LEASE_SECONDS)
    assert sdk.sandboxes["sbx-1"].provider.paused

    target = await carrier.dial(handle, 8000)

    assert list(carrier._live) == [served_again]
    assert sdk.connected == [handle.container_id]
    assert not sdk.sandboxes[handle.container_id].provider.paused
    assert target.host == f"8000-{handle.container_id}.e2b.test"


async def test_a_turn_whose_lease_lapsed_resumes_the_paused_container(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A turn can go quiet longer than its lease — a foreground subagent it is waiting on holds no
    sandbox of this conversation's — and the provider pauses the container out from under it. The
    renewal is what the next tool call meets first, so it has to be a call that answers for a
    paused sandbox: `connect` resumes it and leases it in one, where asking the control plane to
    extend the timeout of a container it has already paused is answered not-found. Nothing about
    the turn is lost, and the calls after it are covered as before."""
    clock = _Clock()
    sdk, carrier = _leased(clock)
    handle = await carrier.create(_spec(uuid4()))
    clock.advance(SANDBOX_LEASE_SECONDS + 1)
    assert sdk.sandboxes["sbx-1"].provider.paused

    with caplog.at_level(logging.INFO, logger="ufo"):
        first = await carrier.exec(handle, ("bash", "-lc", "echo back"), 60)
        second = await carrier.exec(handle, ("bash", "-lc", "echo again"), 60)

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert sdk.connect_leases == [SANDBOX_LEASE_SECONDS]
    assert not sdk.sandboxes["sbx-1"].provider.paused
    assert [entry["lapsed"] for entry in _events(caplog, "sandbox.e2b.leased")] == [True]


async def test_a_provider_fault_leaves_no_lease_for_the_next_call_to_trust(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A command whose stream is severed leaves the deadline here vouching for a container the
    provider has stopped answering for. Holding that lease is what turns one lost command into a
    conversation that never reaches its sandbox again, since every later call reads the same
    deadline and skips the reattach. The command is not replayed — it is still running inside the
    sandbox — so recovery is the next call's, and it has to reattach for that to be possible."""
    clock = _Clock()
    sdk, carrier = _leased(clock)
    handle = await carrier.create(_spec(uuid4()))
    severed = httpcore.RemoteProtocolError("<StreamReset stream_id:3, error_code:2>")
    sdk.sandboxes["sbx-1"].commands.raises = severed

    with caplog.at_level(logging.INFO, logger="ufo"), pytest.raises(httpcore.RemoteProtocolError):
        await carrier.exec(handle, ("bash", "-lc", "make"), 120)
    assert _events(caplog, "sandbox.e2b.lease_dropped") == [
        {"conversation_id": str(handle.conversation_id), "during": "exec"}
    ]
    sdk.sandboxes["sbx-1"].commands.raises = None
    recovered = await carrier.exec(handle, ("bash", "-lc", "echo back"), 60)

    assert recovered.exit_code == 0
    assert sdk.connected == ["sbx-1"]
    assert [command for command, _, _ in sdk.sandboxes["sbx-1"].commands.runs][-2:] == [
        "setsid bash -lc make",
        "setsid bash -lc 'echo back'",
    ]


async def test_a_failed_upload_leaves_no_lease_for_the_next_call_to_trust(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An upload reaches the same container over the same connection, so a fault there says the
    same thing about the lease as a severed command does — the drop belongs to the provider call,
    not to the one entry point whose failure was seen first."""
    clock = _Clock()
    sdk, carrier = _leased(clock)
    handle = await carrier.create(_spec(uuid4()))
    sdk.sandboxes["sbx-1"].files.raises = httpcore.RemoteProtocolError("severed")

    with caplog.at_level(logging.INFO, logger="ufo"), pytest.raises(httpcore.RemoteProtocolError):
        await carrier.write(handle, "/workspace/out.txt", b"payload")
    assert _events(caplog, "sandbox.e2b.lease_dropped") == [
        {"conversation_id": str(handle.conversation_id), "during": "write"}
    ]
    sdk.sandboxes["sbx-1"].files.raises = None
    await carrier.write(handle, "/workspace/out.txt", b"payload")

    assert sdk.connected == ["sbx-1"]


TEMPLATES_ENV_VALUE = (
    "small=ufo-sbx-small:build-1,medium=ufo-sbx-medium:build-2,large=ufo-sbx-large:build-3"
)


def _clear_e2b_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(E2B_API_KEY_ENV, raising=False)
    monkeypatch.delenv(E2B_TEMPLATES_ENV, raising=False)
    monkeypatch.delenv("UFO_CLIENT_BINARY", raising=False)


@pytest.mark.parametrize(
    "value",
    [
        "ufo-sbx:build-1",
        "small=ufo-sbx-small:build-1",
        "small=ufo-sbx-small:build-1,medium=ufo-sbx-medium:build-2,huge=ufo-sbx-huge:build-3",
        "small=,medium=ufo-sbx-medium:build-2,large=ufo-sbx-large:build-3",
    ],
)
def test_build_e2b_carrier_refuses_a_template_map_missing_a_size(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Every declared size must name a template: a map missing one would boot a deploy whose
    portal offers a size no sandbox can be created at."""
    _clear_e2b_env(monkeypatch)
    monkeypatch.setenv(E2B_API_KEY_ENV, "sk-env")
    monkeypatch.setenv(E2B_TEMPLATES_ENV, value)
    with pytest.raises(RuntimeError, match=E2B_TEMPLATES_ENV):
        build_e2b_carrier()


def test_e2b_backend_without_proxy_public_url_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The off-cluster fail-closed guard: `[sandbox] backend = "e2b"` with no `proxy_public_url`
    raises at carrier selection — its off-cluster sandbox could reach no metered egress route, so
    open egress is never a silent default. The e2b Manifest marks the carrier `off_cluster`, so core
    refuses it without naming e2b."""
    _clear_e2b_env(monkeypatch)
    monkeypatch.setenv(E2B_API_KEY_ENV, "sk-env")
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///carrier.db"),
        blob=BlobConfig(backend="filesystem", root=Path("blobs")),
        sandbox=SandboxConfig(backend="e2b"),
    )
    with pytest.raises(RuntimeError, match="proxy_public_url"):
        select_carriers(config, (e2b_ext.manifest(),))


def test_e2b_backend_with_plaintext_proxy_public_url_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(E2B_API_KEY_ENV, "k")
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///ufo.db"),
        blob=BlobConfig(backend="filesystem", root=Path("blobs")),
        sandbox=SandboxConfig(
            backend="e2b",
            proxy_public_url="http://sandbox-proxy.test:8888",
        ),
    )

    with pytest.raises(RuntimeError, match="HTTPS"):
        select_carriers(config, (e2b_ext.manifest(),))


async def test_create_provisions_ca_then_workspace_as_root_on_every_branch() -> None:
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    await carrier.create(_spec(conversation))
    await carrier.create(_spec(conversation))
    other = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    spec = replace(_spec(conversation), resume_id="sbx-1")
    await other.create(spec)

    runs = sdk.sandboxes["sbx-1"].commands.runs
    users = sdk.sandboxes["sbx-1"].commands.users
    ensured = [index for index, run in enumerate(runs) if run[0] == ENSURE_WORKSPACE_COMMAND]
    assert len(ensured) == 3
    assert all(users[index] == "root" for index in ensured)


async def test_create_fails_loud_when_the_workspace_setup_fails() -> None:
    sdk = _Sdk(command_fail_counts={"chown": 1})
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)

    with pytest.raises(RuntimeError, match="workspace setup"):
        await carrier.create(_spec(uuid4()))


async def test_create_fails_loud_when_the_workload_cap_fails() -> None:
    sdk = _Sdk(command_fail_counts={"memory.max": 1})
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)

    with pytest.raises(RuntimeError, match="workload cap"):
        await carrier.create(_spec(uuid4()))


async def test_read_streams_through_the_filesystem_api_and_closes_the_reader() -> None:
    """The copy-out asks the provider to stream the body, so a large produced file crosses in
    bounded pieces; the reader holds an open connection with no finalizer to release it, so it is
    closed even when the consumer stops after the first chunk."""
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))
    files = sdk.sandboxes["sbx-1"].files
    files.chunks = (b"produced-", b"bytes")

    whole = [chunk async for chunk in carrier.read(handle, f"{WORKSPACE_DIR}/out.txt")]

    assert whole == [b"produced-", b"bytes"]
    assert files.reads == [(f"{WORKSPACE_DIR}/out.txt", "stream")]
    assert files.closed == 1

    partial = carrier.read(handle, f"{WORKSPACE_DIR}/out.txt")
    assert await partial.__anext__() == b"produced-"
    await partial.aclose()

    assert files.closed == 2


async def test_create_evicts_expired_leases_without_touching_the_provider() -> None:
    """The lease map must not grow by one SDK object per conversation forever: a lease whose
    provider clock ran out is already paused provider-side, so a later create sheds it as pure
    bookkeeping — no pause, no kill, no reconnect for the evicted conversation — and its next
    touch reconnects from the durable handle exactly as a fresh process would."""
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk, clock=sdk.clock)
    settled, fresh = uuid4(), uuid4()
    await carrier.create(_spec(settled))
    assert settled in carrier._live

    sdk.clock.now += SANDBOX_LEASE_SECONDS + 1.0
    await carrier.create(_spec(fresh))

    assert settled not in carrier._live
    assert fresh in carrier._live
    assert sdk.connected == []

    handle = SandboxHandle(conversation_id=settled, container_id="sbx-1", run_token="turn-a")
    await carrier.exec(handle, ("bash", "-lc", "true"), 30)
    assert sdk.connected == ["sbx-1"]


async def test_attach_resumes_the_stored_sandbox_for_a_read() -> None:
    """The read path: attach answers the sandbox the stored handle names — always the provider's
    own answer through the connect that leases it, never the in-process cache's — with no egress
    env, since a read runs nothing that leaves the box."""
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    await carrier.create(_spec(conversation))
    reader = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)

    attached = await reader.attach(replace(_spec(conversation), resume_id="sbx-1"))

    assert attached is not None
    assert attached.container_id == "sbx-1"
    assert attached.egress_env == {}
    assert sdk.connected[-1] == "sbx-1"


async def test_attach_answers_absent_for_a_lost_or_never_opened_sandbox() -> None:
    """A read never provisions: no stored id, or an id the provider no longer has, answers None —
    and no fresh sandbox is created for it."""
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)

    assert await carrier.attach(_spec(uuid4())) is None
    assert await carrier.attach(replace(_spec(uuid4()), resume_id="sbx-gone")) is None
    assert sdk.created == []


async def test_create_prefers_the_named_resume_id_over_its_own_live_cache() -> None:
    """The row is the arbiter of concurrent opens: a caller retrying with the persisted winner's id
    must converge on that sandbox even when this process's cache still holds its own losing one —
    a cache-first read would hand the loser back and carry its id over the winner's row."""
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    loser = await carrier.create(_spec(conversation))
    winner = await _carrier(api_key="k", templates=_templates("t"), sdk=sdk).create(
        _spec(conversation)
    )
    assert loser.container_id != winner.container_id

    adopted = await carrier.create(replace(_spec(conversation), resume_id=winner.container_id))

    assert adopted.container_id == winner.container_id
    assert sdk.connected[-1] == winner.container_id


async def test_attach_connects_to_the_named_id_even_when_the_cache_holds_another() -> None:
    """attach answers for the id the stored handle names — the provider's own connect against
    that id — even while this process's cache still leases a different sandbox of the same
    conversation."""
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    cached = await carrier.create(_spec(conversation))
    named = await _carrier(api_key="k", templates=_templates("t"), sdk=sdk).create(
        _spec(conversation)
    )
    assert cached.container_id != named.container_id

    attached = await carrier.attach(replace(_spec(conversation), resume_id=named.container_id))

    assert attached is not None
    assert attached.container_id == named.container_id
    assert sdk.connected[-1] == named.container_id


async def test_attach_without_a_named_id_answers_none_never_the_cache() -> None:
    """A conversation whose row holds no handle has no sandbox to read, even while this process's
    cache still leases one — the row is the authority, and answering off the cache would hand a
    reader a sandbox no row references."""
    sdk = _Sdk()
    carrier = _carrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    await carrier.create(_spec(conversation))
    connects = len(sdk.connected)

    assert await carrier.attach(replace(_spec(conversation), resume_id=None)) is None
    assert len(sdk.connected) == connects
