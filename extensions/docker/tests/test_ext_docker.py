"""The Docker carrier extension's selection seam.

The container-integration proof lives in test_file_tools.py (Docker-gated). This asserts only the
seam: with `[sandbox] backend = "docker"` and the docker extension's Manifest present, `serve`
resolves exactly this extension's carrier by name — the deploy swaps to it without core naming
Docker. Constructing the carrier needs no daemon, so this is not Docker-gated."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import pytest
import ufo_ext_docker as docker_ext
from ufo_ext_docker import DockerCarrier

from ufo.blob import FilesystemBlobStore
from ufo.config import BlobConfig, Config, DatabaseConfig, SandboxConfig
from ufo.sandbox.fs_creds import SandboxFsCredentials
from ufo.sdk.sandbox import (
    SENTINEL_MODEL_KEY,
    WORKSPACE_DIR,
    MountSpec,
    ProxyEndpoint,
    SandboxHandle,
    SandboxSpec,
    mount_health_check,
)
from ufo.serve import _select_carrier

_S3_CREDS = SandboxFsCredentials("AKIASBX", "sbx-secret", "sbx-token")


@dataclass
class _RecordingBlob:
    """A spy over a real FilesystemBlobStore: it does the actual byte work so the assertion about
    the copied bytes is against the real store, and records which method the carrier routed through
    so the s3 vs filesystem export paths are distinguished."""

    inner: FilesystemBlobStore
    calls: list[str] = field(default_factory=list)

    async def put(self, key: str, data: bytes) -> None:
        self.calls.append("put")
        await self.inner.put(key, data)

    async def put_file(self, key: str, source: Path) -> None:
        self.calls.append("put_file")
        await self.inner.put_file(key, source)

    async def get(self, key: str) -> bytes:
        return await self.inner.get(key)

    async def exists(self, key: str) -> bool:
        return await self.inner.exists(key)

    def get_stream(self, key: str) -> AsyncIterator[bytes]:
        self.calls.append("get_stream")
        return self.inner.get_stream(key)

    async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None:
        self.calls.append("put_stream")
        await self.inner.put_stream(key, chunks)

    async def copy(self, src_key: str, dst_key: str) -> None:
        self.calls.append("copy")
        await self.inner.copy(src_key, dst_key)


async def test_export_s3_mount_copies_server_side_within_the_store(tmp_path: Path) -> None:
    """An s3-mount export promotes the produced file with a server-side blob copy from the workspace
    prefix to the artifact key — never a read-through-the-pod re-upload."""
    conversation = uuid4()
    key_prefix = f"conversations/{conversation}/workspace"
    mount = MountSpec(kind="s3", bucket="ufo-blobs", key_prefix=key_prefix, credentials=_S3_CREDS)
    handle = SandboxHandle(conversation_id=conversation, container_id="c1", mount=mount)
    blob = _RecordingBlob(inner=FilesystemBlobStore(root=tmp_path))
    await blob.inner.put(f"{key_prefix}/report.pdf", b"pdf-bytes")
    blob.calls.clear()

    await DockerCarrier().export(
        handle, f"{WORKSPACE_DIR}/report.pdf", blob, "artifacts/x/report.pdf"
    )

    assert blob.calls == ["copy"]
    assert await blob.inner.get("artifacts/x/report.pdf") == b"pdf-bytes"


async def test_export_filesystem_mount_streams_from_the_host_path(tmp_path: Path) -> None:
    """A filesystem bind mount already has the file at host_path/<rel>; export streams it in with
    put_file, unchanged by the s3 server-side copy path."""
    host = tmp_path / "ws"
    host.mkdir()
    (host / "out.bin").write_bytes(b"host-bytes")
    mount = MountSpec(kind="filesystem", host_path=str(host))
    handle = SandboxHandle(conversation_id=uuid4(), container_id="c1", mount=mount)
    blob = _RecordingBlob(inner=FilesystemBlobStore(root=tmp_path / "blobs"))

    await DockerCarrier().export(handle, f"{WORKSPACE_DIR}/out.bin", blob, "artifacts/y/out.bin")

    assert blob.calls == ["put_file"]
    assert await blob.inner.get("artifacts/y/out.bin") == b"host-bytes"


async def test_running_id_raises_on_docker_ps_failure_instead_of_reporting_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuine no-match is exit 0 with empty stdout; a non-zero `docker ps` means the command
    itself failed (daemon hiccup, timeout). Swallowing that as "not running" would send `create`
    down the fresh-create path against a name a live container still holds, surfacing a misleading
    name conflict instead of the real transient fault."""

    async def fake_docker(*argv: str, stdin: bytes = b"", timeout_s: int = 60):
        return 1, b"", b"error during connect: transient daemon error"

    monkeypatch.setattr(docker_ext, "_docker", fake_docker)

    with pytest.raises(RuntimeError, match="docker ps failed"):
        await DockerCarrier()._running_id("ufo-sbx-x")


def test_config_backend_docker_resolves_the_extension_contributed_carrier() -> None:
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///carrier.db"),
        blob=BlobConfig(backend="filesystem", root=Path("blobs")),
        sandbox=SandboxConfig(backend="docker"),
    )
    carrier = _select_carrier(config, (docker_ext.manifest(),))
    assert isinstance(carrier, DockerCarrier)


async def test_attach_skips_the_remount_only_when_the_shared_probe_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The skip decision rides on the shared mount_health_check recipe — a real readdir through
    s3fs plus the credential-age bound, not a bare `mountpoint` that stays green on an
    expired-credential mount — and a pass makes _mount_s3 a no-op."""
    calls: list[tuple[str, ...]] = []

    async def fake_docker(*argv: str, stdin: bytes = b"", timeout_s: int = 60):
        calls.append(argv)
        return 0, b"", b""

    monkeypatch.setattr(docker_ext, "_docker", fake_docker)
    conversation = uuid4()
    mount = MountSpec(
        kind="s3",
        bucket="ufo-blobs",
        key_prefix=f"conversations/{conversation}/workspace",
        credentials=_S3_CREDS,
        s3_url="https://minio:9000",
        region="us-east-1",
        path_style=True,
    )
    handle = SandboxHandle(conversation_id=conversation, container_id="c1", mount=mount)

    await DockerCarrier()._mount_s3(handle, mount)

    assert calls == [("exec", "-i", "c1", "sh", "-c", mount_health_check(WORKSPACE_DIR))]


async def test_s3fs_mount_execs_run_without_the_agent_egress_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """s3fs is framework infrastructure, not agent egress: the default-deny proxy would fail its S3
    CONNECT, so no exec in the mount path carries the agent's egress env — the scoped credential,
    not the proxy, confines the mount. The handle here carries a populated egress env, so the
    assertion is non-vacuous: it would fail if `_mount_s3` ever threaded `handle.egress_env` onto a
    mount exec the way `exec` does."""
    calls: list[tuple[str, ...]] = []

    async def fake_docker(*argv: str, stdin: bytes = b"", timeout_s: int = 60):
        calls.append(argv)
        return (1 if any("mountpoint" in arg for arg in argv) else 0), b"", b""

    monkeypatch.setattr(docker_ext, "_docker", fake_docker)
    conversation = uuid4()
    mount = MountSpec(
        kind="s3",
        bucket="ufo-blobs",
        key_prefix=f"conversations/{conversation}/workspace",
        credentials=_S3_CREDS,
        s3_url="https://minio:9000",
        region="us-east-1",
        path_style=True,
    )
    handle = SandboxHandle(
        conversation_id=conversation,
        container_id="c1",
        mount=mount,
        egress_env={
            "HTTPS_PROXY": "http://turn-a:@host.docker.internal:8080",
            "GH_TOKEN": "UFO_SENTINEL_GRANT_acct-1",
        },
    )

    await DockerCarrier()._mount_s3(handle, mount)

    flat = [arg for argv in calls for arg in argv]
    assert any("s3fs" in arg for arg in flat)
    assert "--env" not in flat
    assert not any("HTTPS_PROXY" in arg or "GH_TOKEN" in arg for arg in flat)


async def test_exec_carries_the_turn_env_and_run_bakes_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run token and sentinel env belong to the turn, not the container: `docker run` bakes no
    egress env (a container outliving its first turn must not pin that turn's token), and every
    `docker exec` carries its own handle's env — proxy URL with the turn's token, model sentinels,
    and the spec's per-turn sentinel entries."""
    calls: list[tuple[str, ...]] = []

    async def fake_docker(*argv: str, stdin: bytes = b"", timeout_s: int = 60):
        calls.append(argv)
        if argv[0] == "run":
            return 0, b"cid1\n", b""
        return 0, b"", b""

    monkeypatch.setattr(docker_ext, "_docker", fake_docker)
    spec = SandboxSpec(
        conversation_id=uuid4(),
        image_ref="ufo-sandbox:latest",
        mount=MountSpec(kind="filesystem", host_path="/tmp/ws"),
        proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem"),
        run_token="turn-a",
        env={"GH_TOKEN": "UFO_SENTINEL_GRANT_acct-1"},
    )
    carrier = DockerCarrier()
    handle = await carrier.create(spec)

    run_argv = next(argv for argv in calls if argv[0] == "run")
    assert "--env" not in run_argv

    await carrier.exec(handle, ("bash", "-lc", "gh api user"), b"", 30)

    exec_argv = calls[-1]
    proxy_url = "http://turn-a:@host.docker.internal:8080"
    assert f"HTTPS_PROXY={proxy_url}" in exec_argv
    assert f"https_proxy={proxy_url}" in exec_argv
    assert f"ANTHROPIC_API_KEY={SENTINEL_MODEL_KEY}" in exec_argv
    assert "GH_TOKEN=UFO_SENTINEL_GRANT_acct-1" in exec_argv
    assert exec_argv.index("cid1") > exec_argv.index(f"HTTPS_PROXY={proxy_url}")


async def test_attach_to_a_running_container_carries_the_second_turns_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The #507 bug lived in the attach branch: a later turn reusing a still-running container ran
    under the first turn's baked env. Now the second `create` (which hits `running is not None`)
    returns a handle whose `exec` carries the second turn's token and sentinels, not the first's —
    the env lives on the per-turn handle, never on the container."""
    running: list[str] = []

    async def fake_docker(*argv: str, stdin: bytes = b"", timeout_s: int = 60):
        if argv[0] == "run":
            running.append("cid1")
            return 0, b"cid1\n", b""
        if argv[0] == "ps":
            return 0, (b"cid1\n" if running else b""), b""
        return 0, b"", b""

    monkeypatch.setattr(docker_ext, "_docker", fake_docker)
    conversation = uuid4()

    def _spec(run_token: str, env: dict[str, str]) -> SandboxSpec:
        return SandboxSpec(
            conversation_id=conversation,
            image_ref="ufo-sandbox:latest",
            mount=MountSpec(kind="filesystem", host_path="/tmp/ws"),
            proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem"),
            run_token=run_token,
            env=env,
        )

    carrier = DockerCarrier()
    await carrier.create(_spec("turn-a", {"GH_TOKEN": "UFO_SENTINEL_GRANT_acct-a"}))
    second = await carrier.create(_spec("turn-b", {"GH_TOKEN": "UFO_SENTINEL_GRANT_acct-b"}))

    exec_calls: list[tuple[str, ...]] = []

    async def record_docker(*argv: str, stdin: bytes = b"", timeout_s: int = 60):
        exec_calls.append(argv)
        return 0, b"", b""

    monkeypatch.setattr(docker_ext, "_docker", record_docker)
    await carrier.exec(second, ("bash", "-lc", "gh api user"), b"", 30)

    exec_argv = exec_calls[-1]
    assert "HTTPS_PROXY=http://turn-b:@host.docker.internal:8080" in exec_argv
    assert "GH_TOKEN=UFO_SENTINEL_GRANT_acct-b" in exec_argv
    assert "HTTPS_PROXY=http://turn-a:@host.docker.internal:8080" not in exec_argv
    assert "GH_TOKEN=UFO_SENTINEL_GRANT_acct-a" not in exec_argv
