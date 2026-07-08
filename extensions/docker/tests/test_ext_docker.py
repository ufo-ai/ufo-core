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
from ufo.sdk.sandbox import WORKSPACE_DIR, MountSpec, SandboxHandle
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


def test_config_backend_docker_resolves_the_extension_contributed_carrier() -> None:
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///carrier.db"),
        blob=BlobConfig(backend="filesystem", root=Path("blobs")),
        sandbox=SandboxConfig(backend="docker"),
    )
    carrier = _select_carrier(config, (docker_ext.manifest(),))
    assert isinstance(carrier, DockerCarrier)


async def test_s3fs_mount_exec_clears_the_proxy_env_but_the_health_check_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """s3fs is framework infrastructure, not agent egress: the container-wide default-deny proxy
    would fail its S3 CONNECT, so the mount exec (only) clears HTTP(S)_PROXY. The scoped credential,
    not the proxy, confines the mount — as in metalcraft's `run s3fs without the egress proxy`."""
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
    handle = SandboxHandle(conversation_id=conversation, container_id="c1", mount=mount)

    await DockerCarrier()._mount_s3(handle, mount)

    proxy_cleared = ("HTTP_PROXY=", "HTTPS_PROXY=", "http_proxy=", "https_proxy=")
    mount_exec = next(argv for argv in calls if any("s3fs" in arg for arg in argv))
    assert all(cleared in mount_exec for cleared in proxy_cleared)
    health_check = next(argv for argv in calls if any("mountpoint" in arg for arg in argv))
    assert not any(cleared in health_check for cleared in proxy_cleared)
