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
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import UUID, uuid4

import httpcore
import httpx
import pytest
import ufo_ext_e2b as e2b_ext
from e2b.exceptions import (
    FileNotFoundException,
    SandboxNotFoundException,
    TimeoutException,
)
from e2b.sandbox.commands.command_handle import CommandExitException
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from ufo_ext_e2b import (
    CA_INSTALL_TIMEOUT_SECONDS,
    CA_SANDBOX_PATH,
    CA_STAGING_PATH,
    CARRIER_NAME,
    CONVERSATION_METADATA_KEY,
    DEFAULT_IDLE_SECONDS,
    E2B_API_KEY_ENV,
    E2B_LIFECYCLE,
    E2B_NETWORK,
    E2B_TEMPLATES_ENV,
    ENSURE_WORKSPACE_COMMAND,
    EXEC_LEASE_MARGIN_SECONDS,
    EXEC_TIMEOUT_CODE,
    INSTALL_CA_COMMAND,
    NODE_GLOBAL_MODULES,
    PLAYWRIGHT_BROWSERS_DIR,
    PREPARE_ATTEMPTS,
    RESUME_RETRY_DELAY_SECONDS,
    RESUME_TOTAL_TIMEOUT_SECONDS,
    RESUME_TRANSPORT_RETRIES,
    SANDBOX_LEASE_SECONDS,
    SENTINEL_MODEL_KEY,
    SYSTEM_CA_BUNDLE,
    WORKSPACE_ENSURE_TIMEOUT_SECONDS,
    E2BCarrier,
    build_e2b_carrier,
)

from ufo import o11y
from ufo.config import BlobConfig, Config, DatabaseConfig, SandboxConfig
from ufo.sandbox.select import select_carrier
from ufo.sandbox.session import (
    NO_PROXY_HOSTS,
    SANDBOX_SIZES,
    WORKSPACE_DIR,
    ExecResult,
    ProxyEndpoint,
    SandboxHandle,
    SandboxSpec,
    SandboxUnreachable,
)
from ufo.tools.builtins import MAX_BASH_TIMEOUT_MS


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
class _Commands:
    runs: list[tuple[str, str | None, float | None]] = field(default_factory=list)
    hangs: bool = False
    users: list[str | None] = field(default_factory=list)
    envs: list[dict[str, str] | None] = field(default_factory=list)
    result: _Result = field(default_factory=lambda: _Result("out", "", 0))
    raises: Exception | None = None
    fail_on: tuple[str, ...] = ()
    fail_counts: dict[str, int] = field(default_factory=dict)
    timeout_on: tuple[str, ...] = ()
    timeout_counts: dict[str, int] = field(default_factory=dict)

    async def run(
        self,
        cmd: str,
        *,
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        user: str | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> _Result:
        self.runs.append((cmd, cwd, timeout))
        if self.hangs:
            await asyncio.Event().wait()
        self.users.append(user)
        self.envs.append(envs)
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


def _spec(conversation: UUID, size: str | None = "small") -> SandboxSpec:
    return SandboxSpec(
        conversation_id=conversation,
        image_ref="ufo-sandbox:latest",
        workspace_host_path="/tmp/ws",
        proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem", public_url=PROXY_PUBLIC_URL),
        run_token="run-token",
        size=size,
    )


async def test_create_opens_the_template_of_the_specs_size() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(
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
    carrier = E2BCarrier(api_key="k", templates=_templates("tpl"), sdk=sdk)

    with pytest.raises(RuntimeError, match="sandbox size"):
        await carrier.create(_spec(uuid4(), size=None))
    assert sdk.created == []


async def test_resume_ignores_the_size_and_reconnects(monkeypatch: pytest.MonkeyPatch) -> None:
    """An existing sandbox keeps the size it was created at: the resume path never consults the
    template map, so a changed agent setting shapes only the next fresh sandbox."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("tpl"), sdk=sdk)
    first = await carrier.create(_spec(uuid4(), size="small"))

    resumed = await carrier.create(
        replace(_spec(first.conversation_id, size="large"), resume_id=first.container_id)
    )

    assert resumed.container_id == first.container_id
    assert len(sdk.created) == 1


async def test_create_opens_a_sandbox_on_the_template_and_returns_its_handle() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="key-1", templates=_templates("tpl-1"), sdk=sdk)
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


async def test_create_disables_public_port_traffic() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("tpl"), sdk=sdk)

    await carrier.create(_spec(uuid4()))

    assert sdk.created[0]["network"] == {"allow_public_traffic": False}


async def test_create_fails_loud_when_no_traffic_token_returns() -> None:
    """The token is not on the handle any more — `dial` reads it off the live container — but a
    template that returns none at create leaves every port unreachable through the ingress, so the
    create is where that is caught."""
    sdk = _Sdk(traffic_access_token=None)
    carrier = E2BCarrier(api_key="k", templates=_templates("tpl"), sdk=sdk)

    with pytest.raises(RuntimeError, match="traffic access token"):
        await carrier.create(_spec(uuid4()))


async def test_create_installs_the_proxy_ca_into_system_trust_as_root() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)

    await carrier.create(_spec(uuid4()))

    sandbox = sdk.sandboxes["sbx-1"]
    assert sandbox.files.written == [(CA_STAGING_PATH, "ca-pem")]
    assert sandbox.files.write_users == ["root"]
    assert sandbox.commands.runs[0] == (INSTALL_CA_COMMAND, None, CA_INSTALL_TIMEOUT_SECONDS)
    assert sandbox.commands.users[0] == "root"


async def test_a_failed_trust_update_raises_a_named_ca_install_error() -> None:
    """The CA install is the precondition for every HTTPS call a sandbox makes, so its failure
    detail is what an operator reads when egress starts refusing. Mapping `CommandExitException`
    to a named RuntimeError is all that stands between them and a bare provider exception. The exit
    is the box's own deterministic answer, the same one a further attempt would get, so it is raised
    on the first attempt rather than retried."""
    sdk = _Sdk(command_fail_counts={"update-ca-certificates": 1})
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)

    with pytest.raises(RuntimeError, match="sandbox CA install failed"):
        await carrier.create(_spec(uuid4()))

    runs = sdk.sandboxes["sbx-1"].commands.runs
    assert [command for command, _, _ in runs] == [INSTALL_CA_COMMAND]


async def test_a_box_reached_off_the_cache_is_prepared_again_not_deferred() -> None:
    """No durable handle names this box — it is reached off this process's own cache — so nothing
    vouches for its preparation and the next open re-asserts it strictly rather than deferring."""
    sdk = _Sdk()
    carrier = E2BCarrier(
        api_key="k", templates=_templates("t"), sdk=sdk, resume_prepare_seconds=0.01
    )
    spec = _spec(uuid4())

    await carrier.create(spec)
    sdk.sandboxes["sbx-1"].commands.hangs = True

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.2):
            await carrier.create(spec)

    assert sdk.connected == ["sbx-1"]


async def test_a_cached_box_whose_preparation_failed_drops_its_lease() -> None:
    """The lease's deadline is evidence only while the container answers to it, and this one just
    did not — so it goes, and the next open reaches the provider instead of reading it back."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk, prepare_retry_seconds=0.0)
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
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk, prepare_retry_seconds=0.0)
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
    assert [command for command, _, _ in sandbox.commands.runs] == [
        INSTALL_CA_COMMAND,
        ENSURE_WORKSPACE_COMMAND,
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
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk, prepare_retry_seconds=0.0)
    conversation = uuid4()

    with pytest.raises(httpx.ReadError):
        await carrier.create(_spec(conversation))

    assert _counted(reader, "ufo.sandbox_prepare_retried_total") == [
        (PREPARE_ATTEMPTS - 1, {"carrier": CARRIER_NAME})
    ]
    assert carrier._leased(conversation) is None


async def test_a_resumed_box_whose_upload_drops_is_deferred(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    opened = await carrier.create(_spec(conversation))
    sdk.sandboxes[opened.container_id].files.raises = httpx.ReadError("envd dropped it")

    restarted = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    with caplog.at_level(logging.INFO, logger="ufo"):
        resumed = await restarted.create(
            replace(_spec(conversation), resume_id=opened.container_id)
        )

    assert resumed.container_id == opened.container_id
    assert _events(caplog, "sandbox.e2b.prepare_deferred") == [
        {"conversation_id": str(conversation), "sandbox_id": opened.container_id}
    ]


async def test_a_resume_the_control_plane_never_answers_is_retried(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The three turns #1346 lost: `POST /sandboxes/{id}/connect` read-timed out and the turn died
    before its first round. The provider's control plane is off this cluster and `connect` opens
    nothing, so re-issuing it converges on the one container the id names."""
    sdk = _Sdk(connect_faults=[httpx.ReadTimeout("timed out")])
    carrier = E2BCarrier(
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


async def test_a_resume_the_control_plane_keeps_dropping_raises_bounded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A member waits on this, so the retry is bounded: past the budget the fault is the answer,
    and it never becomes a fresh box — the paused container the id names holds the workspace."""
    faults = [httpx.ReadTimeout("timed out")] * (RESUME_TRANSPORT_RETRIES + 1)
    sdk = _Sdk(connect_faults=list(faults))
    carrier = E2BCarrier(
        api_key="k", templates=_templates("t"), sdk=sdk, resume_retry_delay_seconds=0.0
    )
    conversation = uuid4()
    opened = await carrier.create(_spec(conversation))
    sdk.connected.clear()

    with caplog.at_level(logging.INFO, logger="ufo"), pytest.raises(httpx.ReadTimeout):
        await carrier.create(replace(_spec(conversation), resume_id=opened.container_id))

    assert sdk.connected == [opened.container_id] * len(faults)
    assert len(sdk.created) == 1
    assert _events(caplog, "sandbox.e2b.resume_unanswered") == [
        {
            "conversation_id": str(conversation),
            "sandbox_id": opened.container_id,
            "attempts": RESUME_TRANSPORT_RETRIES + 1,
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
    faults = [httpx.ReadTimeout("timed out")] * (RESUME_TRANSPORT_RETRIES + 1)
    sdk = _Sdk(connect_faults=list(faults))
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    opened = await carrier.create(_spec(conversation))

    with pytest.raises(httpx.ReadTimeout):
        await carrier.create(replace(_spec(conversation), resume_id=opened.container_id))

    assert slept == [RESUME_RETRY_DELAY_SECONDS, RESUME_RETRY_DELAY_SECONDS * 2]
    assert sum(slept) < RESUME_TOTAL_TIMEOUT_SECONDS


async def test_a_control_plane_that_answers_nothing_at_all_ends_at_the_ceiling(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Attempts bound how many times this asks; only the wall clock bounds how long. A `connect`
    that never returns would otherwise hold a member's setup for as long as the SDK's own default
    allows — a third-party value this repo neither sets nor asserts."""
    conversation = uuid4()
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    opened = await carrier.create(_spec(conversation))

    async def never_answers(
        sandbox_id: str,
        *,
        timeout: int,  # noqa: ASYNC109
        api_key: str,
    ) -> _Sandbox:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    sdk.connect = never_answers  # type: ignore[method-assign]
    stalling = E2BCarrier(
        api_key="k", templates=_templates("t"), sdk=sdk, resume_total_timeout_seconds=0.05
    )

    with caplog.at_level(logging.INFO, logger="ufo"), pytest.raises(httpx.ReadTimeout):
        await stalling.create(replace(_spec(conversation), resume_id=opened.container_id))

    assert _events(caplog, "sandbox.e2b.resume_timed_out") != []


async def test_a_fresh_box_is_not_lease_visible_until_it_is_prepared() -> None:
    """The lease is what a concurrent open adopts, so it must vouch only for a box already made
    ready. Publishing it before preparation hands the next caller a container this one is still
    working on and may be about to fail out of."""
    conversation = uuid4()
    sdk = _Sdk(file_write_raises=httpx.ReadError("connection broken"))
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk, prepare_retry_seconds=0.0)

    with pytest.raises(httpx.ReadError):
        await carrier.create(_spec(conversation))

    assert carrier._leased(conversation) is None


async def test_every_connect_in_the_carrier_retries_an_unanswered_control_plane() -> None:
    """One endpoint, one uncertainty. A stall that kills a turn at setup kills it just as dead on
    the mid-turn lease renewal ten minutes in, and on the read path — so all three `connect` sites
    come through the one retrying seam rather than only the one #1346 happened to report."""
    conversation = uuid4()
    clock = _Clock()
    sdk = _Sdk(clock=clock)
    carrier = E2BCarrier(
        api_key="k", templates=_templates("t"), sdk=sdk, clock=clock, resume_retry_delay_seconds=0.0
    )
    opened = await carrier.create(_spec(conversation))

    sdk.connect_faults = [httpx.ReadTimeout("timed out")]
    attached = await carrier.attach(replace(_spec(conversation), resume_id=opened.container_id))
    assert attached is not None
    assert sdk.connected == [opened.container_id] * 2

    sdk.connected.clear()
    clock.now += SANDBOX_LEASE_SECONDS
    sdk.connect_faults = [httpx.ReadTimeout("timed out")]
    assert (await carrier.exec(opened, ("true",), 5)).exit_code == 0
    assert sdk.connected == [opened.container_id] * 2


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


async def test_exec_runs_under_the_turn_egress_env() -> None:
    """Every sandbox command routes out through the proxy: exec passes an egress env whose
    HTTP(S)_PROXY dial the proxy's public URL with the turn's run token as the basic-auth username
    (so the proxy meters the request to the turn), whose NO_PROXY exempts the sandbox's own
    loopback, whose model keys are the sentinels the proxy swaps for the real key on the wire, and
    whose CA vars point at the written proxy CA."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))

    await carrier.exec(handle, ("bash", "-lc", "curl https://example.com"), 60)

    envs = sdk.sandboxes["sbx-1"].commands.envs[-1]
    assert envs is not None
    proxy_url = "https://run-token:@sandbox-proxy.test"
    assert envs["HTTP_PROXY"] == proxy_url
    assert envs["HTTPS_PROXY"] == proxy_url
    assert envs["http_proxy"] == proxy_url
    assert envs["https_proxy"] == proxy_url
    assert envs["NO_PROXY"] == NO_PROXY_HOSTS
    assert envs["no_proxy"] == NO_PROXY_HOSTS
    assert envs["ANTHROPIC_API_KEY"] == SENTINEL_MODEL_KEY
    assert envs["OPENAI_API_KEY"] == SENTINEL_MODEL_KEY
    assert envs["SSL_CERT_FILE"] == SYSTEM_CA_BUNDLE
    assert envs["REQUESTS_CA_BUNDLE"] == SYSTEM_CA_BUNDLE
    assert envs["CURL_CA_BUNDLE"] == SYSTEM_CA_BUNDLE
    assert envs["NODE_EXTRA_CA_CERTS"] == CA_SANDBOX_PATH
    assert envs["NODE_PATH"] == NODE_GLOBAL_MODULES
    assert envs["PLAYWRIGHT_BROWSERS_PATH"] == PLAYWRIGHT_BROWSERS_DIR


async def test_create_without_a_reachable_proxy_url_fails_loud() -> None:
    """The carrier's own fail-loud, behind the boot guard: an e2b spec whose proxy carries no public
    URL cannot build a metered egress env, so create raises rather than run an open sandbox."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
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
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
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


async def test_dial_returns_the_per_port_host_and_traffic_header() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("tpl"), sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))

    target = await carrier.dial(handle, 8000)

    assert target.host == f"8000-{handle.container_id}.e2b.test"
    assert target.tls is True
    assert target.headers == {"e2b-traffic-access-token": "traffic-tok"}


async def test_dial_omits_the_header_when_the_sandbox_carries_no_traffic_token() -> None:
    """A resumed sandbox `dial` reaches through `connect`, not `create`'s fresh-token guard — a
    sandbox with no token on the wire dials without the header rather than raising."""
    sdk = _Sdk()
    sdk.sandboxes["sbx-1"] = _Sandbox(
        sandbox_id="sbx-1",
        provider=_Provider(clock=sdk.clock, expires_at=sdk.clock() + SANDBOX_LEASE_SECONDS),
        commands=_Commands(),
        files=_Files(),
        traffic_access_token=None,
    )
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    handle = SandboxHandle(conversation_id=uuid4(), container_id="sbx-1")

    target = await carrier.dial(handle, 9223)

    assert target.headers == {}


async def test_a_lease_naming_another_container_is_a_miss() -> None:
    """The lease is keyed by conversation, the caller names a container. The ingress re-reads the
    conversation's handle per request precisely so a sandbox recreated since is picked up at once,
    so a fresh lease naming the old container must not answer for the new one — otherwise every
    viewer keeps reaching a sandbox the conversation no longer runs on until the lease lapses."""
    clock = _Clock()
    sdk, carrier = _leased(clock)
    handle = await carrier.create(_spec(uuid4()))
    recreated = SandboxHandle(conversation_id=handle.conversation_id, container_id="sbx-2")
    sdk.sandboxes["sbx-2"] = _Sandbox(
        sandbox_id="sbx-2",
        provider=_Provider(clock=sdk.clock, expires_at=sdk.clock() + SANDBOX_LEASE_SECONDS),
        commands=_Commands(),
        files=_Files(),
    )

    target = await carrier.dial(recreated, 8000)

    assert target.host == "8000-sbx-2.e2b.test"
    assert sdk.connected == ["sbx-2"]


async def test_dial_raises_sandbox_unreachable_when_the_sandbox_is_gone() -> None:
    """A sandbox the provider no longer has raises `SandboxNotFoundException` on reconnect; `dial`
    maps it to `SandboxUnreachable`, the one error every carrier's `dial` raises, rather than
    leaking the e2b SDK's own exception type."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    handle = SandboxHandle(conversation_id=uuid4(), container_id="sbx-1")

    with pytest.raises(SandboxUnreachable):
        await carrier.dial(handle, 9223)


async def test_second_create_for_the_conversation_resumes_rather_than_recreates() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
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
    first = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    opened = await first.create(_spec(conversation))

    restarted = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
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


async def test_a_resumed_box_whose_command_stream_hangs_still_opens(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The case the bound exists for, and the only one no SDK deadline reaches: `commands.run` goes
    out over a stream the SDK gives no read timeout, so a silent `envd` holds it open forever. The
    upload answers here, so the hang this tolerates is the command's alone."""
    sdk = _Sdk()
    first = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    opened = await first.create(_spec(conversation))
    sandbox = sdk.sandboxes[opened.container_id]
    sandbox.files.written.clear()
    sandbox.commands.hangs = True

    restarted = E2BCarrier(
        api_key="k", templates=_templates("t"), sdk=sdk, resume_prepare_seconds=0.01
    )
    with caplog.at_level(logging.INFO, logger="ufo"):
        resumed = await restarted.create(
            replace(_spec(conversation), resume_id=opened.container_id)
        )

    assert resumed.container_id == opened.container_id
    assert sandbox.files.written == [(CA_STAGING_PATH, "ca-pem")], (
        "the upload answered during this resume; only the command hung"
    )
    assert _events(caplog, "sandbox.e2b.prepare_deferred") == [
        {"conversation_id": str(conversation), "sandbox_id": opened.container_id}
    ]


async def test_a_resumed_box_whose_upload_hangs_still_opens() -> None:
    """The upload is bounded by the SDK but raises a class it never maps, so a bound that named
    exception classes would miss it. It hangs before any command runs."""
    sdk = _Sdk()
    first = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    opened = await first.create(_spec(conversation))
    sandbox = sdk.sandboxes[opened.container_id]
    sandbox.commands.runs.clear()
    sandbox.files.hangs = True

    restarted = E2BCarrier(
        api_key="k", templates=_templates("t"), sdk=sdk, resume_prepare_seconds=0.01
    )
    resumed = await restarted.create(replace(_spec(conversation), resume_id=opened.container_id))

    assert resumed.container_id == opened.container_id
    assert sandbox.commands.runs == []


async def test_a_fresh_box_is_never_served_on_a_deferral() -> None:
    """Only a box the durable handle vouches for may defer. A fresh one holds no CA and no
    `/workspace`, so its preparation has no deadline to expire into — routing it through the
    deferring branch would hand the turn a container that reaches no host."""
    sdk = _Sdk()
    sdk.command_hangs = True
    carrier = E2BCarrier(
        api_key="k", templates=_templates("t"), sdk=sdk, resume_prepare_seconds=0.01
    )

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.2):
            await carrier.create(_spec(uuid4()))


async def test_a_replacement_for_a_lost_resume_id_is_never_deferred() -> None:
    """The one path where "the durable id is set" and "this is the box that id named" come apart:
    the provider no longer has that sandbox, so a fresh one opens in its place. The id vouches for
    a container that is gone, not for this one, which holds no CA and no `/workspace` — so its
    preparation is strict and has no deadline to expire into."""
    sdk = _Sdk(command_hangs=True)
    carrier = E2BCarrier(
        api_key="k", templates=_templates("t"), sdk=sdk, resume_prepare_seconds=0.01
    )

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


async def test_a_deferred_preparation_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deferral is how a wedged box stops costing a turn, so its rate is the thing to watch —
    a log line alone answers "did it happen once", never "is it getting worse"."""
    reader = _counters(monkeypatch)
    sdk = _Sdk()
    first = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    opened = await first.create(_spec(conversation))
    sdk.sandboxes[opened.container_id].commands.hangs = True

    restarted = E2BCarrier(
        api_key="k", templates=_templates("t"), sdk=sdk, resume_prepare_seconds=0.01
    )
    await restarted.create(replace(_spec(conversation), resume_id=opened.container_id))

    assert _counted(reader, "ufo.sandbox_prepare_deferred_total") == [
        (1, {"carrier": CARRIER_NAME})
    ]


async def test_a_command_that_timed_out_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """`exec` maps a wedged `envd` to an exit code the model reads and moves past, so nothing else
    records that it happened."""
    reader = _counters(monkeypatch)
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))
    sdk.sandboxes[handle.container_id].commands.raises = TimeoutException("probe hung")

    result = await carrier.exec(handle, ("bash", "-lc", "pytest -q"), 60)

    assert result.exit_code == EXEC_TIMEOUT_CODE
    assert _counted(reader, "ufo.sandbox_exec_timeout_total") == [(1, {"carrier": CARRIER_NAME})]


async def test_exec_runs_the_joined_command_in_the_workspace_and_maps_the_result() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))
    sdk.sandboxes["sbx-1"].commands.result = _Result("hello\n", "", 0)

    result = await carrier.exec(handle, ("bash", "-lc", "echo hi"), 60)

    assert result == ExecResult(stdout="hello\n", stderr="", exit_code=0)
    command, cwd, timeout = sdk.sandboxes["sbx-1"].commands.runs[-1]
    assert command == "bash -lc 'echo hi'"
    assert cwd == WORKSPACE_DIR
    assert timeout == 60


async def test_write_uploads_through_the_filesystem_api() -> None:
    """The bytes go through `files.write`, never the command line: inlining them is what e2b rejects
    once the payload is large, exactly when a caller offloads an oversized tool result."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))
    content = b"x" * (2 * 1024 * 1024)

    await carrier.write(handle, "/workspace/f", content)

    sandbox = sdk.sandboxes["sbx-1"]
    assert ("/workspace/f", content) in sandbox.files.written
    assert not any("base64" in command for command, _, _ in sandbox.commands.runs)


async def test_exec_maps_a_nonzero_exit_to_the_command_result() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))
    sdk.sandboxes["sbx-1"].commands.raises = CommandExitException(
        stderr="boom", stdout="partial", exit_code=3, error="boom"
    )

    result = await carrier.exec(handle, ("bash", "-lc", "false"), 60)

    assert result == ExecResult(stdout="partial", stderr="boom", exit_code=3)


async def test_exec_maps_a_timeout_to_the_timeout_code() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))
    sdk.sandboxes["sbx-1"].commands.raises = TimeoutException("timed out")

    result = await carrier.exec(handle, ("bash", "-lc", "sleep 999"), 1)

    assert result.exit_code == EXEC_TIMEOUT_CODE
    assert "timed out" in result.stderr


def _leased(clock: _Clock) -> tuple[_Sdk, E2BCarrier]:
    sdk = _Sdk(clock=clock)
    return sdk, E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk, clock=clock)


def _events(caplog: pytest.LogCaptureFixture, name: str) -> list[dict[str, object]]:
    return [record.ufo for record in caplog.records if record.getMessage() == name]


async def test_a_standing_lease_covers_a_command_without_a_round_trip() -> None:
    """The lease `create` opens already outlives anything the bash tool can ask for, so the common
    case reaches the provider once, for the command itself. Renewing per call would put a
    control-plane round trip in front of every tool call a turn makes."""
    sdk, carrier = _leased(_Clock())
    handle = await carrier.create(_spec(uuid4()))

    for _ in range(5):
        await carrier.exec(handle, ("bash", "-lc", "sleep 590"), 600)

    assert sdk.created[0]["timeout"] == SANDBOX_LEASE_SECONDS
    assert sdk.connected == []


async def test_a_command_longer_than_the_standing_lease_leases_past_it() -> None:
    """The lease is sized to outlast the tool's own ceiling, but the carrier does not assume that
    ceiling: a command asking for more than the standing lease gets one sized to the command, so
    raising the tool's cap can never silently reintroduce a container that pauses mid-command."""
    sdk, carrier = _leased(_Clock())
    handle = await carrier.create(_spec(uuid4()))

    await carrier.exec(handle, ("bash", "-lc", "sleep 3500"), 3_600)

    assert sdk.connect_leases == [3_600 + EXEC_LEASE_MARGIN_SECONDS]


def test_the_standing_lease_covers_the_longest_command_the_tool_can_ask_for() -> None:
    """Skipping the renewal is only safe while no need the tool surface can raise reaches the
    standing lease. `bash`'s ceiling is the largest of them, so lifting it past the lease is the one
    edit that would quietly turn that skip into a container paused mid-command — pinned here rather
    than argued in a docstring."""
    longest_need = MAX_BASH_TIMEOUT_MS // 1000 + EXEC_LEASE_MARGIN_SECONDS

    assert longest_need <= SANDBOX_LEASE_SECONDS


async def test_a_turn_still_working_when_the_lease_runs_low_renews_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """e2b's timeout is a wall clock, not an idle timer, so one lease counts down across a whole
    turn and whichever command straddles its end is paused out from under and its stream torn down.
    A turn still working as the lease runs low buys another before running anything."""
    clock = _Clock()
    sdk, carrier = _leased(clock)
    handle = await carrier.create(_spec(uuid4()))

    await carrier.exec(handle, ("bash", "-lc", "sleep 240"), 300)
    assert sdk.connected == []

    clock.advance(SANDBOX_LEASE_SECONDS - 100)
    with caplog.at_level(logging.INFO, logger="ufo"):
        await carrier.exec(handle, ("bash", "-lc", "sleep 240"), 300)

    assert sdk.connect_leases == [SANDBOX_LEASE_SECONDS]
    assert not sdk.sandboxes["sbx-1"].provider.paused
    assert _events(caplog, "sandbox.e2b.leased") == [
        {
            "conversation_id": str(handle.conversation_id),
            "sandbox_id": "sbx-1",
            "span": SANDBOX_LEASE_SECONDS,
            "lapsed": False,
        }
    ]


async def test_a_renewed_lease_carries_the_calls_after_it() -> None:
    """A renewal has to leave behind a deadline the next call can trust. One recorded as anything
    already past would still buy the right span from the provider and still look right in the call
    it makes — and then force a round trip on every remaining call of the conversation, which is
    the whole cost the skip exists to avoid."""
    clock = _Clock()
    sdk, carrier = _leased(clock)
    handle = await carrier.create(_spec(uuid4()))
    clock.advance(SANDBOX_LEASE_SECONDS - 100)
    await carrier.exec(handle, ("bash", "-lc", "sleep 240"), 300)
    assert sdk.connect_leases == [SANDBOX_LEASE_SECONDS]

    for _ in range(3):
        await carrier.exec(handle, ("bash", "-lc", "sleep 240"), 300)

    assert sdk.connect_leases == [SANDBOX_LEASE_SECONDS]


async def test_the_lease_deadline_is_taken_before_the_call_that_sets_it() -> None:
    """The provider starts counting when it handles the request, not when the reply lands, so both
    the open and the renewal read the clock before the call goes out. Recording it after would
    believe the lease runs later than it does — the one direction that works a container past what
    the provider agreed to. Each round trip here burns 100s, and each renewal below is due only if
    that 100s is charged against the lease."""
    clock = _Clock()
    sdk, carrier = _leased(clock)
    sdk.on_call = lambda: clock.advance(100)
    opened = clock.now
    handle = await carrier.create(_spec(uuid4()))

    clock.now = opened + SANDBOX_LEASE_SECONDS - DEFAULT_IDLE_SECONDS + 1
    renewed_at = clock.now
    await carrier.exec(handle, ("bash", "-lc", "echo hi"), 2)

    clock.now = renewed_at + SANDBOX_LEASE_SECONDS - DEFAULT_IDLE_SECONDS + 1
    await carrier.exec(handle, ("bash", "-lc", "echo hi"), 2)

    assert sdk.connect_leases == [SANDBOX_LEASE_SECONDS, SANDBOX_LEASE_SECONDS]


async def test_a_write_renews_a_lease_that_no_longer_covers_the_idle_span() -> None:
    """A write is the same kind of work and renews the same lease. The span it asks for is the idle
    one, not its own duration: what has to survive is the model's thinking after the write, so a
    turn offloading a run of results must not lose the container between two of them."""
    clock = _Clock()
    sdk, carrier = _leased(clock)
    handle = await carrier.create(_spec(uuid4()))
    clock.advance(SANDBOX_LEASE_SECONDS - 100)

    await carrier.write(handle, "/workspace/out.txt", b"payload")

    assert sdk.connect_leases == [SANDBOX_LEASE_SECONDS]


async def test_a_dial_renews_a_lease_that_no_longer_covers_the_idle_span() -> None:
    """A caller dialling a port is about to reach the service behind it, so the dial renews the
    same lease — an address is worthless if the container pauses before the exchange."""
    clock = _Clock()
    sdk, carrier = _leased(clock)
    handle = await carrier.create(_spec(uuid4()))
    clock.advance(SANDBOX_LEASE_SECONDS - 100)

    target = await carrier.dial(handle, 9223)

    assert target.host == "9223-sbx-1.e2b.test"
    assert sdk.connect_leases == [SANDBOX_LEASE_SECONDS]


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


async def test_a_process_that_reconnects_carries_the_lease_on_connect() -> None:
    """A process that never created the sandbox reaches it through the same renewal, so the first
    command after a restart is covered exactly like any later one, with no unleased window between
    attaching and running."""
    clock = _Clock()
    sdk, opener = _leased(clock)
    handle = await opener.create(_spec(uuid4()))
    restarted = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk, clock=clock)

    await restarted.exec(handle, ("bash", "-lc", "sleep 500"), 540)

    assert sdk.connect_leases == [SANDBOX_LEASE_SECONDS]


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
        await carrier.exec(handle, ("bash", "-lc", "make"), 600)
    assert _events(caplog, "sandbox.e2b.lease_dropped") == [
        {"conversation_id": str(handle.conversation_id), "during": "exec"}
    ]
    sdk.sandboxes["sbx-1"].commands.raises = None
    recovered = await carrier.exec(handle, ("bash", "-lc", "echo back"), 60)

    assert recovered.exit_code == 0
    assert sdk.connected == ["sbx-1"]
    assert [command for command, _, _ in sdk.sandboxes["sbx-1"].commands.runs][-2:] == [
        "bash -lc make",
        "bash -lc 'echo back'",
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


async def test_a_stored_sandbox_the_provider_no_longer_has_opens_a_fresh_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The durable handle outlives the container it names: e2b keeps a paused sandbox until
    something kills it, but a sandbox killed out of band leaves an id nothing can resurrect. Failing
    the resume would fail every turn the conversation ever admits, so the id is abandoned for a
    fresh container over the same durable workspace."""
    sdk, carrier = _leased(_Clock())
    conversation = uuid4()

    with caplog.at_level(logging.INFO, logger="ufo"):
        handle = await carrier.create(replace(_spec(conversation), resume_id="killed-1"))

    assert sdk.connected == ["killed-1"]
    assert _events(caplog, "sandbox.e2b.resume_missed") == [
        {"conversation_id": str(conversation), "sandbox_id": "killed-1"}
    ]
    assert handle.container_id == "sbx-1"
    assert sdk.created[0]["metadata"] == {CONVERSATION_METADATA_KEY: str(conversation)}


TEMPLATES_ENV_VALUE = (
    "small=ufo-sbx-small:build-1,medium=ufo-sbx-medium:build-2,large=ufo-sbx-large:build-3"
)


def _clear_e2b_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(E2B_API_KEY_ENV, raising=False)
    monkeypatch.delenv(E2B_TEMPLATES_ENV, raising=False)


def test_build_e2b_carrier_requires_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_e2b_env(monkeypatch)
    with pytest.raises(RuntimeError, match=E2B_API_KEY_ENV):
        build_e2b_carrier()


def test_build_e2b_carrier_requires_the_template_map(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_e2b_env(monkeypatch)
    monkeypatch.setenv(E2B_API_KEY_ENV, "sk-env")
    with pytest.raises(RuntimeError, match=E2B_TEMPLATES_ENV):
        build_e2b_carrier()


def test_build_e2b_carrier_reads_the_templates_and_key_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_e2b_env(monkeypatch)
    monkeypatch.setenv(E2B_API_KEY_ENV, "sk-env")
    monkeypatch.setenv(E2B_TEMPLATES_ENV, TEMPLATES_ENV_VALUE)
    carrier = build_e2b_carrier()
    assert carrier.api_key == "sk-env"
    assert carrier.templates == {
        "small": "ufo-sbx-small:build-1",
        "medium": "ufo-sbx-medium:build-2",
        "large": "ufo-sbx-large:build-3",
    }


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


def test_config_backend_e2b_resolves_the_extension_contributed_carrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seam end to end: with `[sandbox] backend = "e2b"` and the e2b extension's Manifest
    present, `serve` builds exactly this extension's carrier by name — the deploy swaps the sandbox
    backend to an extension's without core naming e2b."""
    _clear_e2b_env(monkeypatch)
    monkeypatch.setenv(E2B_API_KEY_ENV, "sk-env")
    monkeypatch.setenv(E2B_TEMPLATES_ENV, TEMPLATES_ENV_VALUE)
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///carrier.db"),
        blob=BlobConfig(backend="filesystem", root=Path("blobs")),
        sandbox=SandboxConfig(backend="e2b", proxy_public_url=PROXY_PUBLIC_URL),
    )
    carrier, spec = select_carrier(config, (e2b_ext.manifest(),))
    assert isinstance(carrier, E2BCarrier)
    assert spec.off_cluster
    assert spec.sizes == SANDBOX_SIZES
    assert carrier.templates["small"] == "ufo-sbx-small:build-1"


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
        select_carrier(config, (e2b_ext.manifest(),))


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
        select_carrier(config, (e2b_ext.manifest(),))


async def test_exec_env_rides_the_handle_not_the_conversation() -> None:
    """Two turns can hold the same conversation's sandbox at once (a subagent beside its parent):
    each exec runs under its own handle's run token, so egress attribution never leaks across turns
    and a later create never re-points an earlier turn's env."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    first = await carrier.create(replace(_spec(conversation), run_token="turn-a"))
    second = await carrier.create(replace(_spec(conversation), run_token="turn-b"))

    await carrier.exec(first, ("bash", "-lc", "true"), 60)
    await carrier.exec(second, ("bash", "-lc", "true"), 60)

    envs = sdk.sandboxes["sbx-1"].commands.envs
    assert envs[-2] is not None and envs[-2]["HTTPS_PROXY"] == "https://turn-a:@sandbox-proxy.test"
    assert envs[-1] is not None and envs[-1]["HTTPS_PROXY"] == "https://turn-b:@sandbox-proxy.test"


async def test_spec_env_joins_the_exec_env() -> None:
    """The engine's per-turn sentinel entries (a grant CLI credential like GH_TOKEN) ride the spec
    onto the handle and into every exec of that turn."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    handle = await carrier.create(
        replace(_spec(uuid4()), env={"GH_TOKEN": "UFO_SENTINEL_GRANT_acct-1"})
    )

    await carrier.exec(handle, ("bash", "-lc", "gh api user"), 60)

    envs = sdk.sandboxes["sbx-1"].commands.envs[-1]
    assert envs is not None
    assert envs["GH_TOKEN"] == "UFO_SENTINEL_GRANT_acct-1"


async def test_create_provisions_ca_then_workspace_as_root_on_every_branch() -> None:
    """create ensures `/workspace` as root after the CA lands, on the fresh, resumed, and
    reconnected paths alike — the first process to touch a sandbox is not always the one that
    created it, and the sandbox user can neither create nor own a directory under root's `/`."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    await carrier.create(_spec(conversation))
    await carrier.create(_spec(conversation))
    other = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    spec = replace(_spec(conversation), resume_id="sbx-1")
    await other.create(spec)

    runs = sdk.sandboxes["sbx-1"].commands.runs
    users = sdk.sandboxes["sbx-1"].commands.users
    ensured = [index for index, run in enumerate(runs) if run[0] == ENSURE_WORKSPACE_COMMAND]
    assert len(ensured) == 3
    assert all(users[index] == "root" for index in ensured)


async def test_create_fails_loud_when_the_workspace_setup_fails() -> None:
    sdk = _Sdk(command_fail_counts={"chown": 1})
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)

    with pytest.raises(RuntimeError, match="workspace setup"):
        await carrier.create(_spec(uuid4()))


async def test_read_streams_through_the_filesystem_api_and_closes_the_reader() -> None:
    """The copy-out asks the provider to stream the body, so a large produced file crosses in
    bounded pieces; the reader holds an open connection with no finalizer to release it, so it is
    closed even when the consumer stops after the first chunk."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
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


async def test_read_of_an_absent_file_raises_file_not_found() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))
    sdk.sandboxes["sbx-1"].files.missing = True

    with pytest.raises(FileNotFoundError):
        [chunk async for chunk in carrier.read(handle, f"{WORKSPACE_DIR}/out.txt")]


async def test_create_evicts_expired_leases_without_touching_the_provider() -> None:
    """The lease map must not grow by one SDK object per conversation forever: a lease whose
    provider clock ran out is already paused provider-side, so a later create sheds it as pure
    bookkeeping — no pause, no kill, no reconnect for the evicted conversation — and its next
    touch reconnects from the durable handle exactly as a fresh process would."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk, clock=sdk.clock)
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
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    await carrier.create(_spec(conversation))
    reader = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)

    attached = await reader.attach(replace(_spec(conversation), resume_id="sbx-1"))

    assert attached is not None
    assert attached.container_id == "sbx-1"
    assert attached.egress_env == {}
    assert sdk.connected[-1] == "sbx-1"


async def test_attach_answers_absent_for_a_lost_or_never_opened_sandbox() -> None:
    """A read never provisions: no stored id, or an id the provider no longer has, answers None —
    and no fresh sandbox is created for it."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)

    assert await carrier.attach(_spec(uuid4())) is None
    assert await carrier.attach(replace(_spec(uuid4()), resume_id="sbx-gone")) is None
    assert sdk.created == []


async def test_create_prefers_the_named_resume_id_over_its_own_live_cache() -> None:
    """The row is the arbiter of concurrent opens: a caller retrying with the persisted winner's id
    must converge on that sandbox even when this process's cache still holds its own losing one —
    a cache-first read would hand the loser back and carry its id over the winner's row."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    loser = await carrier.create(_spec(conversation))
    winner = await E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk).create(
        _spec(conversation)
    )
    assert loser.container_id != winner.container_id

    adopted = await carrier.create(replace(_spec(conversation), resume_id=winner.container_id))

    assert adopted.container_id == winner.container_id
    assert sdk.connected[-1] == winner.container_id


async def test_attach_answers_absent_for_a_sandbox_the_cache_outlived() -> None:
    """A cached lease can outlive its sandbox — a kill, a provider fault — and a read that answered
    present off the cache would raise where absence was promised. Attach asks the provider every
    time, sheds the dead cache entry, and answers None."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    opened = await carrier.create(_spec(conversation))
    assert conversation in carrier._live
    del sdk.sandboxes[opened.container_id]

    attached = await carrier.attach(replace(_spec(conversation), resume_id=opened.container_id))

    assert attached is None
    assert conversation not in carrier._live


async def test_a_lease_renewal_on_a_lost_sandbox_sheds_the_lease_and_raises() -> None:
    """`_sandbox`'s reconnect can meet a sandbox the provider no longer has; the lease is dropped so
    the next call reattaches from durable state instead of trusting a deadline the provider
    abandoned, and the loss surfaces rather than reading as a transport fault."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk, clock=sdk.clock)
    conversation = uuid4()
    handle = await carrier.create(_spec(conversation))
    sdk.clock.now += SANDBOX_LEASE_SECONDS + 1
    del sdk.sandboxes[handle.container_id]

    with pytest.raises(SandboxNotFoundException):
        await carrier.exec(handle, ("bash", "-lc", "true"), 30)

    assert conversation not in carrier._live


async def test_attach_connects_to_the_named_id_even_when_the_cache_holds_another() -> None:
    """attach answers for the id the stored handle names — the provider's own connect against
    that id — even while this process's cache still leases a different sandbox of the same
    conversation."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    cached = await carrier.create(_spec(conversation))
    named = await E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk).create(
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
    carrier = E2BCarrier(api_key="k", templates=_templates("t"), sdk=sdk)
    conversation = uuid4()
    await carrier.create(_spec(conversation))
    connects = len(sdk.connected)

    assert await carrier.attach(replace(_spec(conversation), resume_id=None)) is None
    assert len(sdk.connected) == connects
