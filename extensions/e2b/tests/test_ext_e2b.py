"""The e2b carrier extension against a fake standing in for the synchronous e2b SDK.

The e2b service is not reachable from CI, so a fake plays the provider — it records the calls the
carrier makes and returns canned results in the SDK's exact shape (a create/connect factory whose
sandboxes carry `commands`, `files`, and `pause`). Every assertion is the carrier's own behavior:
the handle it returns, the ExecResult it maps a run, a non-zero exit, and a timeout into, the bytes
it exports, the create-or-resume it picks, and that `serve`'s `[sandbox] backend = "e2b"` resolves
this extension-contributed carrier — never the fake, which is only the dependency it stands in for.
The two exceptions raised are the real e2b types, so the mapping is exercised against the classes
the live SDK throws."""

import base64
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import ufo_ext_e2b as e2b_ext
from e2b.exceptions import TimeoutException
from e2b.sandbox.commands.command_handle import CommandExitException
from ufo_ext_e2b import (
    CONVERSATION_METADATA_KEY,
    E2B_API_KEY_ENVS,
    E2B_LIFECYCLE,
    E2B_TEMPLATE_ENV,
    EXEC_TIMEOUT_CODE,
    E2BCarrier,
    build_e2b_carrier,
)

from ufo.blob import FilesystemBlobStore
from ufo.config import BlobConfig, Config, DatabaseConfig, SandboxConfig
from ufo.sandbox.fs_creds import SandboxFsCredentials
from ufo.sandbox.session import (
    WORKSPACE_DIR,
    ExecResult,
    MountSpec,
    ProxyEndpoint,
    SandboxHandle,
    SandboxSpec,
)
from ufo.sdk.sandbox import AWS_CREDENTIALS_PATH, aws_credentials_file
from ufo.serve import _select_carrier


@dataclass
class _Result:
    stdout: str
    stderr: str
    exit_code: int


@dataclass
class _Commands:
    runs: list[tuple[str, str | None, float | None]] = field(default_factory=list)
    users: list[str | None] = field(default_factory=list)
    result: _Result = field(default_factory=lambda: _Result("out", "", 0))
    raises: Exception | None = None
    fail_on: tuple[str, ...] = ()

    def run(
        self,
        cmd: str,
        *,
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        user: str | None = None,
        timeout: float | None = None,
    ) -> _Result:
        self.runs.append((cmd, cwd, timeout))
        self.users.append(user)
        if any(token in cmd for token in self.fail_on):
            raise CommandExitException(stderr="", stdout="", exit_code=1, error="not mounted")
        if self.raises is not None:
            raise self.raises
        return self.result


@dataclass
class _Files:
    contents: bytes = b""
    made_dirs: list[str] = field(default_factory=list)
    written: list[tuple[str, str | bytes]] = field(default_factory=list)

    def read(self, path: str, format: str) -> bytes:
        return self.contents

    def make_dir(self, path: str, *, user: str | None = None) -> bool:
        self.made_dirs.append(path)
        return True

    def write(self, path: str, data: str | bytes) -> object:
        self.written.append((path, data))
        return None


@dataclass
class _Sandbox:
    sandbox_id: str
    commands: _Commands = field(default_factory=_Commands)
    files: _Files = field(default_factory=_Files)
    paused: int = 0
    traffic_access_token: str | None = "traffic-tok"

    def pause(self, **opts: object) -> bool:
        self.paused += 1
        return True

    def get_host(self, port: int) -> str:
        return f"{port}-{self.sandbox_id}.e2b.test"


@dataclass
class _Sdk:
    created: list[dict[str, object]] = field(default_factory=list)
    connected: list[str] = field(default_factory=list)
    sandboxes: dict[str, _Sandbox] = field(default_factory=dict)
    counter: int = 0
    command_fail_on: tuple[str, ...] = ()

    def create(
        self,
        *,
        template: str,
        timeout: int,
        metadata: dict[str, str],
        lifecycle: object,
        api_key: str,
    ) -> _Sandbox:
        self.counter += 1
        sandbox_id = f"sbx-{self.counter}"
        sandbox = _Sandbox(sandbox_id=sandbox_id, commands=_Commands(fail_on=self.command_fail_on))
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

    def connect(self, sandbox_id: str, *, timeout: int, api_key: str) -> _Sandbox:
        self.connected.append(sandbox_id)
        return self.sandboxes[sandbox_id]


def _spec(conversation: UUID) -> SandboxSpec:
    return SandboxSpec(
        conversation_id=conversation,
        image_ref="ufo-sandbox:latest",
        mount=MountSpec(kind="filesystem", host_path="/tmp/ws"),
        proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem"),
        run_token="run-token",
    )


_S3_CREDS = SandboxFsCredentials("AKIASBX", "sbx-secret", "sbx-token")


def _s3_spec(conversation: UUID) -> SandboxSpec:
    return SandboxSpec(
        conversation_id=conversation,
        image_ref="ufo-sandbox:latest",
        mount=MountSpec(
            kind="s3",
            bucket="ufo-blobs",
            key_prefix=f"conversations/{conversation}/workspace",
            credentials=_S3_CREDS,
            s3_url="https://minio:9000",
            region="us-east-1",
            path_style=True,
        ),
        proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem"),
        run_token="run-token",
    )


async def test_create_mounts_the_s3_workspace_prefix_over_s3fs() -> None:
    """On an s3 mount the carrier writes the prefix-scoped credential, then runs the root `prepare`
    and the agent `mount` — the privileged FUSE prep never runs as the agent, and s3fs mounts this
    conversation's prefix at /workspace with the config's endpoint and path-style."""
    sdk = _Sdk(command_fail_on=("mountpoint",))  # fresh sandbox: /workspace not yet mounted
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    conversation = uuid4()

    handle = await carrier.create(_s3_spec(conversation))

    sandbox = sdk.sandboxes["sbx-1"]
    assert sandbox.files.written == [(AWS_CREDENTIALS_PATH, aws_credentials_file(_S3_CREDS))]
    commands = [cmd for cmd, _, _ in sandbox.commands.runs]
    assert commands[0] == f"mountpoint -q {WORKSPACE_DIR}"
    assert "chmod 666 /dev/fuse" in commands[1]
    assert commands[2].startswith(
        f"mkdir -p {WORKSPACE_DIR} && chmod 600 {AWS_CREDENTIALS_PATH} && "
    )
    assert f"s3fs ufo-blobs:/conversations/{conversation}/workspace {WORKSPACE_DIR}" in commands[2]
    assert "-o url=https://minio:9000" in commands[2]
    assert "-o use_path_request_style" in commands[2]
    assert sandbox.commands.users == [None, "root", None]
    assert handle.mount is not None and handle.mount.kind == "s3"


async def test_create_skips_the_s3_mount_when_already_healthy() -> None:
    """Idempotent-if-healthy: a subagent's create over a live mount health-checks and returns
    without remounting, so it never yanks the mount out from under an in-flight dispatch."""
    sdk = _Sdk()  # mountpoint succeeds → already mounted
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)

    await carrier.create(_s3_spec(uuid4()))

    sandbox = sdk.sandboxes["sbx-1"]
    assert sandbox.files.written == []
    assert [cmd for cmd, _, _ in sandbox.commands.runs] == [f"mountpoint -q {WORKSPACE_DIR}"]


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


async def test_exec_runs_the_joined_command_in_the_workspace_and_maps_the_result() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))
    sdk.sandboxes["sbx-1"].commands.result = _Result("hello\n", "", 0)

    result = await carrier.exec(handle, ("bash", "-lc", "echo hi"), b"", 60)

    assert result == ExecResult(stdout="hello\n", stderr="", exit_code=0)
    command, cwd, timeout = sdk.sandboxes["sbx-1"].commands.runs[0]
    assert command == "bash -lc 'echo hi'"
    assert cwd == WORKSPACE_DIR
    assert timeout == 60


async def test_exec_pipes_stdin_through_base64() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))

    await carrier.exec(handle, ("sh", "-c", 'cat > "$1"', "sh", "/workspace/f"), b"payload", 30)

    command = sdk.sandboxes["sbx-1"].commands.runs[0][0]
    assert command.startswith("printf %s ")
    assert "| base64 -d | " in command
    assert base64.b64encode(b"payload").decode() in command
    assert command.endswith("sh -c 'cat > \"$1\"' sh /workspace/f")


async def test_exec_maps_a_nonzero_exit_to_the_command_result() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))
    sdk.sandboxes["sbx-1"].commands.raises = CommandExitException(
        stderr="boom", stdout="partial", exit_code=3, error="boom"
    )

    result = await carrier.exec(handle, ("bash", "-lc", "false"), b"", 60)

    assert result == ExecResult(stdout="partial", stderr="boom", exit_code=3)


async def test_exec_maps_a_timeout_to_the_timeout_code() -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))
    sdk.sandboxes["sbx-1"].commands.raises = TimeoutException("timed out")

    result = await carrier.exec(handle, ("bash", "-lc", "sleep 999"), b"", 1)

    assert result.exit_code == EXEC_TIMEOUT_CODE
    assert "timed out" in result.stderr


async def test_export_reads_the_file_into_the_blob_store(tmp_path: Path) -> None:
    sdk = _Sdk()
    carrier = E2BCarrier(api_key="k", template="t", sdk=sdk)
    handle = await carrier.create(_spec(uuid4()))
    sdk.sandboxes["sbx-1"].files.contents = b"produced-bytes"
    blob = FilesystemBlobStore(root=tmp_path)

    await carrier.export(handle, "/workspace/out.txt", blob, "exports/out.txt")

    assert await blob.get("exports/out.txt") == b"produced-bytes"


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


def _clear_e2b_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(E2B_TEMPLATE_ENV, raising=False)
    for name in E2B_API_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)


def test_build_e2b_carrier_requires_a_template(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_e2b_env(monkeypatch)
    with pytest.raises(RuntimeError, match=E2B_TEMPLATE_ENV):
        build_e2b_carrier()


def test_build_e2b_carrier_requires_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_e2b_env(monkeypatch)
    monkeypatch.setenv(E2B_TEMPLATE_ENV, "tpl")
    with pytest.raises(RuntimeError, match="none of"):
        build_e2b_carrier()


def test_build_e2b_carrier_reads_the_template_and_key_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_e2b_env(monkeypatch)
    monkeypatch.setenv(E2B_TEMPLATE_ENV, "tpl")
    monkeypatch.setenv("E2B_API_KEY", "sk-env")
    carrier = build_e2b_carrier()
    assert carrier.api_key == "sk-env"
    assert carrier.template == "tpl"


def test_config_backend_e2b_resolves_the_extension_contributed_carrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seam end to end: with `[sandbox] backend = "e2b"` and the e2b extension's Manifest
    present, `serve` builds exactly this extension's carrier by name — the deploy swaps the sandbox
    backend to an extension's without core naming e2b."""
    _clear_e2b_env(monkeypatch)
    monkeypatch.setenv(E2B_TEMPLATE_ENV, "tpl")
    monkeypatch.setenv("E2B_API_KEY", "sk-env")
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///carrier.db"),
        blob=BlobConfig(backend="filesystem", root=Path("blobs")),
        sandbox=SandboxConfig(backend="e2b"),
    )
    carrier = _select_carrier(config, (e2b_ext.manifest(),))
    assert isinstance(carrier, E2BCarrier)
    assert carrier.template == "tpl"
