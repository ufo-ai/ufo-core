"""The e2b carrier extension against a fake standing in for the async e2b SDK.

The e2b service is not reachable from CI, so a fake plays the provider — it records the calls the
carrier makes and returns canned results in the SDK's exact shape (a create/connect factory whose
sandboxes carry `commands`, `files`, and `pause`). Every assertion is the carrier's own behavior:
the handle it returns, the ExecResult it maps a run, a non-zero exit, and a timeout into, the bytes
it exports, the create-or-resume it picks, and that `serve`'s `[sandbox] backend = "e2b"` resolves
this extension-contributed carrier — never the fake, which is only the dependency it stands in for.
The exceptions raised are the real e2b and transport types, so the mapping is exercised against the
classes the live SDK throws, and the fake keeps each sandbox's lease on the same clock the carrier
reads — a container pauses when its span runs out and answers the renewal the way the live service
was measured to answer it, so a lapsed lease is a state a test reaches rather than one it asserts
about."""

import logging
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import UUID, uuid4

import httpcore
import pytest
import ufo_ext_e2b as e2b_ext
from e2b.exceptions import SandboxNotFoundException, TimeoutException
from e2b.sandbox.commands.command_handle import CommandExitException
from ufo_ext_e2b import (
    CA_INSTALL_TIMEOUT_SECONDS,
    CA_SANDBOX_PATH,
    CA_STAGING_PATH,
    CONVERSATION_METADATA_KEY,
    DEFAULT_IDLE_SECONDS,
    E2B_API_KEY_ENV,
    E2B_LIFECYCLE,
    E2B_TEMPLATE_NAME,
    EXEC_LEASE_MARGIN_SECONDS,
    EXEC_TIMEOUT_CODE,
    INSTALL_CA_COMMAND,
    NODE_GLOBAL_MODULES,
    PLAYWRIGHT_BROWSERS_DIR,
    SANDBOX_LEASE_SECONDS,
    SENTINEL_MODEL_KEY,
    SYSTEM_CA_BUNDLE,
    E2BCarrier,
    build_e2b_carrier,
)

from ufo.blob import FilesystemBlobStore
from ufo.config import BlobConfig, Config, DatabaseConfig, SandboxConfig
from ufo.sandbox.session import (
    NO_PROXY_HOSTS,
    WORKSPACE_DIR,
    ExecResult,
    MountSpec,
    ProxyEndpoint,
    SandboxHandle,
    SandboxSpec,
)
from ufo.sdk.sandbox import (
    SANDBOX_FS_TOKEN_STAGING_PATH,
    install_token_command,
    mount_health_check,
    prepare_token_staging_command,
)
from ufo.serve import _select_carrier
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
class _Files:
    made_dirs: list[str] = field(default_factory=list)
    written: list[tuple[str, str | bytes]] = field(default_factory=list)
    write_users: list[str | None] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    raises: Exception | None = None

    async def read(self, path: str, format: str) -> bytes:
        self.reads.append(path)
        return b""

    async def make_dir(self, path: str, *, user: str | None = None) -> bool:
        self.made_dirs.append(path)
        return True

    async def write(self, path: str, data: str | bytes, *, user: str | None = None) -> object:
        if self.raises is not None:
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
    paused: int = 0
    traffic_access_token: str | None = "traffic-tok"

    async def pause(self, **opts: object) -> bool:
        self.paused += 1
        return True

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
    on_call: Callable[[], None] | None = None

    async def create(
        self,
        *,
        template: str,
        timeout: int,  # noqa: ASYNC109
        metadata: dict[str, str],
        lifecycle: object,
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
            commands=_Commands(
                fail_on=self.command_fail_on,
                fail_counts=dict(self.command_fail_counts),
                timeout_on=self.command_timeout_on,
                timeout_counts=dict(self.command_timeout_counts),
            ),
            files=_Files(),
        )
        self.sandboxes[sandbox_id] = sandbox
        self.created.append(
            {
                "template": template,
                "timeout": timeout,
                "metadata": metadata,
                "lifecycle": lifecycle,
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
        sandbox = self.sandboxes.get(sandbox_id)
        if sandbox is None:
            raise SandboxNotFoundException(f"Paused sandbox {sandbox_id} not found")
        sandbox.provider.lease(timeout)
        return sandbox


PROXY_PUBLIC_URL = "https://sandbox-proxy.test"


def _spec(conversation: UUID) -> SandboxSpec:
    return SandboxSpec(
        conversation_id=conversation,
        image_ref="ufo-sandbox:latest",
        mount=MountSpec(kind="filesystem", host_path="/tmp/ws"),
        proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem", public_url=PROXY_PUBLIC_URL),
        run_token="run-token",
    )


_S3_TOKEN = "signed-conversation-token"


def _s3_spec(conversation: UUID) -> SandboxSpec:
    return SandboxSpec(
        conversation_id=conversation,
        image_ref="ufo-sandbox:latest",
        mount=MountSpec(
            kind="s3",
            bucket="ufo-blobs",
            key_prefix=f"conversations/{conversation}/workspace",
            credential_token=_S3_TOKEN,
            s3_url="https://minio:9000",
            region="us-east-1",
            path_style=True,
        ),
        proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem", public_url=PROXY_PUBLIC_URL),
        run_token="run-token",
    )


async def test_create_mounts_the_s3_workspace_prefix_over_s3fs() -> None:
    """The carrier writes a private endpoint token and mounts through an unprivileged s3fs."""
    sdk = _Sdk(command_fail_counts={"mountpoint": 1})
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    conversation = uuid4()

    handle = await carrier.create(_s3_spec(conversation))

    sandbox = sdk.sandboxes["sbx-1"]
    assert sandbox.files.written == [
        (CA_STAGING_PATH, "ca-pem"),
        (SANDBOX_FS_TOKEN_STAGING_PATH, _S3_TOKEN),
    ]
    assert sandbox.files.write_users == ["root", "root"]
    commands = [cmd for cmd, _, _ in sandbox.commands.runs]
    assert commands[0] == INSTALL_CA_COMMAND
    assert commands[1] == prepare_token_staging_command()
    assert commands[2] == install_token_command()
    assert commands[3] == mount_health_check(WORKSPACE_DIR)
    assert "chmod 666 /dev/fuse" in commands[4]
    assert commands[5].startswith(f"mkdir -p {WORKSPACE_DIR} && chown nobody")
    assert commands[6] == mount_health_check(WORKSPACE_DIR)
    assert "sbxcred https://sandbox-proxy.test/sandbox-fs-credentials" in commands[5]
    assert "-o ecs" in commands[5]
    assert f"s3fs ufo-blobs:/conversations/{conversation}/workspace {WORKSPACE_DIR}" in commands[5]
    assert "-o url=https://minio:9000" in commands[5]
    assert "-o use_path_request_style" in commands[5]
    assert "runuser -u nobody -- sh -c" in commands[5]
    assert sandbox.commands.users == ["root", "root", "root", "root", "root", "root", "root"]
    # The mount steps carry no egress env — s3fs reaches S3 directly, never through the proxy.
    assert sandbox.commands.envs == [None, None, None, None, None, None, None]
    assert handle.mount is not None and handle.mount.kind == "s3"


async def test_create_rejects_a_mount_that_disconnects_after_s3fs_returns() -> None:
    sdk = _Sdk(command_fail_counts={"mountpoint": 2})
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)

    with pytest.raises(RuntimeError, match="mount failed its health check"):
        await carrier.create(_s3_spec(uuid4()))


async def test_create_skips_the_s3_mount_when_already_healthy() -> None:
    """Idempotent-if-healthy: a subagent's create over a live mount health-checks and returns
    without remounting, so it never yanks the mount out from under an in-flight dispatch."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)

    await carrier.create(_s3_spec(uuid4()))

    sandbox = sdk.sandboxes["sbx-1"]
    assert sandbox.files.written == [
        (CA_STAGING_PATH, "ca-pem"),
        (SANDBOX_FS_TOKEN_STAGING_PATH, _S3_TOKEN),
    ]
    assert sandbox.files.write_users == ["root", "root"]
    assert [cmd for cmd, _, _ in sandbox.commands.runs] == [
        INSTALL_CA_COMMAND,
        prepare_token_staging_command(),
        install_token_command(),
        mount_health_check(WORKSPACE_DIR),
    ]


async def test_create_remounts_when_the_health_probe_times_out() -> None:
    """A wedged FUSE mount hangs the probe rather than failing it; the carrier reads the timeout
    as unhealthy and rebuilds the mount from its durable S3 workspace."""
    sdk = _Sdk(command_timeout_counts={"mountpoint": 1})
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    conversation = uuid4()

    await carrier.create(_s3_spec(conversation))

    sandbox = sdk.sandboxes["sbx-1"]
    assert (SANDBOX_FS_TOKEN_STAGING_PATH, _S3_TOKEN) in sandbox.files.written
    commands = [cmd for cmd, _, _ in sandbox.commands.runs]
    assert commands[0] == INSTALL_CA_COMMAND
    assert commands[1] == prepare_token_staging_command()
    assert commands[2] == install_token_command()
    assert commands[3] == mount_health_check(WORKSPACE_DIR)
    assert "umount -l" in commands[4]
    assert f"s3fs ufo-blobs:/conversations/{conversation}/workspace {WORKSPACE_DIR}" in commands[5]


async def test_create_opens_a_sandbox_on_the_template_and_returns_its_handle() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="key-1", template="tpl-1", sdk=sdk)
    conversation = uuid4()

    handle = await carrier.create(_spec(conversation))

    assert handle.conversation_id == conversation
    assert handle.container_id == "sbx-1"
    created = sdk.created[0]
    assert created["template"] == "tpl-1"
    assert created["api_key"] == "key-1"
    assert created["lifecycle"] == E2B_LIFECYCLE
    assert created["metadata"] == {CONVERSATION_METADATA_KEY: str(conversation)}
    assert WORKSPACE_DIR in sdk.sandboxes["sbx-1"].files.made_dirs
    assert handle.traffic_token == "traffic-tok"


async def test_create_installs_the_proxy_ca_into_system_trust_as_root() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)

    await carrier.create(_spec(uuid4()))

    sandbox = sdk.sandboxes["sbx-1"]
    assert sandbox.files.written == [(CA_STAGING_PATH, "ca-pem")]
    assert sandbox.files.write_users == ["root"]
    assert sandbox.commands.runs[0] == (INSTALL_CA_COMMAND, None, CA_INSTALL_TIMEOUT_SECONDS)
    assert sandbox.commands.users[0] == "root"


async def test_create_retries_system_trust_after_an_update_failure() -> None:
    sdk = _Sdk(command_fail_counts={"update-ca-certificates": 1})
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    spec = _spec(uuid4())

    with pytest.raises(RuntimeError, match="sandbox CA install failed"):
        await carrier.create(spec)

    await carrier.create(spec)

    commands = [command for command, _, _ in sdk.sandboxes["sbx-1"].commands.runs]
    assert commands == [INSTALL_CA_COMMAND, INSTALL_CA_COMMAND]


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
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
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
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    spec = SandboxSpec(
        conversation_id=uuid4(),
        image_ref="ufo-sandbox:latest",
        mount=MountSpec(kind="filesystem", host_path="/tmp/ws"),
        proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem"),
        run_token="run-token",
    )
    with pytest.raises(RuntimeError, match="proxy_public_url"):
        await carrier.create(spec)


async def test_create_with_a_plaintext_proxy_url_fails_loud() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    spec = SandboxSpec(
        conversation_id=uuid4(),
        image_ref="ufo-sandbox:latest",
        mount=MountSpec(kind="filesystem", host_path="/tmp/ws"),
        proxy=ProxyEndpoint(
            port=8080,
            ca_cert="ca-pem",
            public_url="http://sandbox-proxy.test:8888",
        ),
        run_token="run-token",
    )
    with pytest.raises(RuntimeError, match="HTTPS"):
        await carrier.create(spec)


async def test_host_returns_the_sandbox_public_per_port_host() -> None:
    """The producer half of the browser's `Carrier.host` seam: e2b routes an in-sandbox port over
    its public per-port host, so the serve process can dial Chrome's CDP endpoint inside the
    sandbox."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))

    assert await carrier.host(handle, 9223) == "9223-sbx-1.e2b.test"


async def test_second_create_for_the_conversation_resumes_rather_than_recreates() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    conversation = uuid4()

    await carrier.create(_spec(conversation))
    handle = await carrier.create(_spec(conversation))

    assert len(sdk.created) == 1
    assert sdk.connected == ["sbx-1"]
    assert handle.container_id == "sbx-1"
    assert [command for command, _, _ in sdk.sandboxes["sbx-1"].commands.runs] == [
        INSTALL_CA_COMMAND,
        INSTALL_CA_COMMAND,
    ]


async def test_create_resumes_a_prior_process_sandbox_and_exec_works() -> None:
    """Cross-process resume: a fresh carrier (empty in-process maps, as after a serve restart) given
    the conversation's stored id via spec.resume_id connects to that sandbox rather than opening a
    new one, and rebuilds the egress env through create so the following exec never KeyErrors on a
    missing _egress — create is the one seam that seeds it, and every turn opens through create."""
    sdk = _Sdk()
    first = E2BCarrier(api_key="k", template="t", sdk=sdk)
    conversation = uuid4()
    opened = await first.create(_spec(conversation))

    restarted = E2BCarrier(api_key="k", template="t", sdk=sdk)
    resumed = await restarted.create(replace(_spec(conversation), resume_id=opened.container_id))

    assert len(sdk.created) == 1
    assert sdk.connected == [opened.container_id]
    assert resumed.container_id == opened.container_id
    assert [command for command, _, _ in sdk.sandboxes["sbx-1"].commands.runs[:2]] == [
        INSTALL_CA_COMMAND,
        INSTALL_CA_COMMAND,
    ]
    result = await restarted.exec(resumed, ("bash", "-lc", "echo hi"), 60)
    assert result.exit_code == 0


async def test_destroy_connects_to_a_prior_process_sandbox_to_pause_it() -> None:
    """The reaper reclaims a sandbox a prior process created by passing the stored id: a fresh
    carrier holds no live sandbox, so destroy reconnects that id and pauses it — idle reclaim
    reaches across a restart, not only sandboxes this process opened."""
    sdk = _Sdk()
    opener = E2BCarrier(api_key="k", template="t", sdk=sdk)
    conversation = uuid4()
    opened = await opener.create(_spec(conversation))

    reaper_carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    await reaper_carrier.destroy(
        SandboxHandle(conversation_id=conversation, container_id=opened.container_id)
    )

    assert sdk.connected == [opened.container_id]
    assert sdk.sandboxes[opened.container_id].paused == 1


async def test_exec_runs_the_joined_command_in_the_workspace_and_maps_the_result() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
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
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))
    content = b"x" * (2 * 1024 * 1024)

    await carrier.write(handle, "/workspace/f", content)

    sandbox = sdk.sandboxes["sbx-1"]
    assert ("/workspace/f", content) in sandbox.files.written
    assert not any("base64" in command for command, _, _ in sandbox.commands.runs)


async def test_exec_maps_a_nonzero_exit_to_the_command_result() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))
    sdk.sandboxes["sbx-1"].commands.raises = CommandExitException(
        stderr="boom", stdout="partial", exit_code=3, error="boom"
    )

    result = await carrier.exec(handle, ("bash", "-lc", "false"), 60)

    assert result == ExecResult(stdout="partial", stderr="boom", exit_code=3)


async def test_exec_maps_a_timeout_to_the_timeout_code() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))
    sdk.sandboxes["sbx-1"].commands.raises = TimeoutException("timed out")

    result = await carrier.exec(handle, ("bash", "-lc", "sleep 999"), 1)

    assert result.exit_code == EXEC_TIMEOUT_CODE
    assert "timed out" in result.stderr


def _leased(clock: _Clock) -> tuple[_Sdk, E2BCarrier]:
    sdk = _Sdk(clock=clock)
    return sdk, E2BCarrier(api_key="k", template="t", sdk=sdk, clock=clock)


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


async def test_a_host_lookup_renews_a_lease_that_no_longer_covers_the_idle_span() -> None:
    """A caller asking for the public host is about to dial the service behind it, so the lookup
    renews the same lease — an address is worthless if the container pauses before the dial."""
    clock = _Clock()
    sdk, carrier = _leased(clock)
    handle = await carrier.create(_spec(uuid4()))
    clock.advance(SANDBOX_LEASE_SECONDS - 100)

    assert await carrier.host(handle, 9223) == "9223-sbx-1.e2b.test"
    assert sdk.connect_leases == [SANDBOX_LEASE_SECONDS]


async def test_a_process_that_reconnects_carries_the_lease_on_connect() -> None:
    """A process that never created the sandbox reaches it through the same renewal, so the first
    command after a restart is covered exactly like any later one, with no unleased window between
    attaching and running."""
    clock = _Clock()
    sdk, opener = _leased(clock)
    handle = await opener.create(_spec(uuid4()))
    restarted = E2BCarrier(api_key="k", template="t", sdk=sdk, clock=clock)

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


async def test_export_copies_the_file_server_side_from_the_workspace_prefix(
    tmp_path: Path,
) -> None:
    """export promotes the produced file with a blob-store copy from the workspace S3 prefix to the
    artifact key — it never reads the bytes back out through the sandbox SDK."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    conversation = uuid4()
    handle = await carrier.create(_s3_spec(conversation))
    key_prefix = f"conversations/{conversation}/workspace"
    blob = FilesystemBlobStore(root=tmp_path)
    await blob.put(f"{key_prefix}/out.txt", b"produced-bytes")

    await carrier.export(handle, f"{WORKSPACE_DIR}/out.txt", blob, "artifacts/abc/out.txt")

    assert await blob.get("artifacts/abc/out.txt") == b"produced-bytes"
    assert sdk.sandboxes["sbx-1"].files.reads == []


async def test_export_without_an_s3_mount_fails_loud(tmp_path: Path) -> None:
    """A filesystem-mount handle has no S3 prefix to copy from, so export refuses it rather than
    fall back to a read-through-the-pod."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))
    blob = FilesystemBlobStore(root=tmp_path)

    with pytest.raises(RuntimeError, match="s3 workspace mount"):
        await carrier.export(handle, f"{WORKSPACE_DIR}/out.txt", blob, "artifacts/abc/out.txt")


async def test_destroy_pauses_the_sandbox() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))

    await carrier.destroy(handle)

    assert sdk.sandboxes["sbx-1"].paused == 1


async def test_destroy_on_a_conversation_never_created_is_a_no_op() -> None:
    """The idle reaper reaps by conversation identity with no container id; a conversation this
    process never held is a no-op that neither connects nor raises."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)

    await carrier.destroy(SandboxHandle(conversation_id=uuid4(), container_id=""))

    assert sdk.connected == []
    assert sdk.sandboxes == {}


async def test_destroy_on_an_already_gone_sandbox_is_a_no_op() -> None:
    """The reaper retries a stored handle whose sandbox the provider no longer has (killed out of
    band, or reaped by a concurrent process) every sweep unless destroy absorbs the reconnect's
    SandboxNotFoundException as the no-op its own contract promises — otherwise the raise stops the
    reaper from ever clearing the durable handle."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)

    await carrier.destroy(SandboxHandle(conversation_id=uuid4(), container_id="gone-1"))

    assert sdk.connected == ["gone-1"]


def _clear_e2b_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(E2B_API_KEY_ENV, raising=False)


def test_build_e2b_carrier_requires_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_e2b_env(monkeypatch)
    with pytest.raises(RuntimeError, match=E2B_API_KEY_ENV):
        build_e2b_carrier()


def test_build_e2b_carrier_reads_the_template_and_key_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_e2b_env(monkeypatch)
    monkeypatch.setenv(E2B_API_KEY_ENV, "sk-env")
    carrier = build_e2b_carrier()
    assert carrier.api_key == "sk-env"
    assert carrier.template == E2B_TEMPLATE_NAME


def test_config_backend_e2b_resolves_the_extension_contributed_carrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seam end to end: with `[sandbox] backend = "e2b"` and the e2b extension's Manifest
    present, `serve` builds exactly this extension's carrier by name — the deploy swaps the sandbox
    backend to an extension's without core naming e2b."""
    _clear_e2b_env(monkeypatch)
    monkeypatch.setenv(E2B_API_KEY_ENV, "sk-env")
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///carrier.db"),
        blob=BlobConfig(backend="filesystem", root=Path("blobs")),
        sandbox=SandboxConfig(backend="e2b", proxy_public_url=PROXY_PUBLIC_URL),
    )
    carrier = _select_carrier(config, (e2b_ext.manifest(),))
    assert isinstance(carrier, E2BCarrier)
    assert carrier.template == E2B_TEMPLATE_NAME


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
        _select_carrier(config, (e2b_ext.manifest(),))


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
        _select_carrier(config, (e2b_ext.manifest(),))


async def test_exec_env_rides_the_handle_not_the_conversation() -> None:
    """Two turns can hold the same conversation's sandbox at once (a subagent beside its parent):
    each exec runs under its own handle's run token, so egress attribution never leaks across turns
    and a later create never re-points an earlier turn's env."""
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
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
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    handle = await carrier.create(
        replace(_spec(uuid4()), env={"GH_TOKEN": "UFO_SENTINEL_GRANT_acct-1"})
    )

    await carrier.exec(handle, ("bash", "-lc", "gh api user"), 60)

    envs = sdk.sandboxes["sbx-1"].commands.envs[-1]
    assert envs is not None
    assert envs["GH_TOKEN"] == "UFO_SENTINEL_GRANT_acct-1"
