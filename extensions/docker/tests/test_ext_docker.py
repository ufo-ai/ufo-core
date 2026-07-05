"""The Docker carrier extension's selection seam.

The container-integration proof lives in test_file_tools.py (Docker-gated). This asserts only the
seam: with `[sandbox] backend = "docker"` and the docker extension's Manifest present, `serve`
resolves exactly this extension's carrier by name — the deploy swaps to it without core naming
Docker. Constructing the carrier needs no daemon, so this is not Docker-gated."""

from pathlib import Path
from uuid import uuid4

import pytest
import selfhost_ext_docker as docker_ext
from selfhost_ext_docker import DockerCarrier

from selfhost.config import BlobConfig, Config, DatabaseConfig, SandboxConfig
from selfhost.sandbox.fs_creds import SandboxFsCredentials
from selfhost.sdk.sandbox import MountSpec, SandboxHandle
from selfhost.serve import _select_carrier

_S3_CREDS = SandboxFsCredentials("AKIASBX", "sbx-secret", "sbx-token")


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
        bucket="selfhost-blobs",
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
