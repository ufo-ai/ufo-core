import asyncio
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from aiobotocore.session import get_session
from botocore.exceptions import BotoCoreError, ClientError
from ufo_testsupport.plugin import (
    CONTAINER_OP_TIMEOUT_S,
    docker_or_fail,
    integration_dependency_available,
)

from ufo.blob import S3BlobStore
from ufo.harness.sandbox.client_binary import client_binary
from ufo.harness.sandbox.session import SANDBOX_GID, SANDBOX_UID

MINIO_IMAGE = (
    "quay.io/minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e"
)
MINIO_CREDENTIAL = "minioadmin"
MINIO_OP_TIMEOUT_S = 180
MINIO_READY_SECONDS = 60.0
TEST_BUCKET = "ufo-test"
IMAGE_BUILD_TIMEOUT_S = 1200


@pytest.fixture(scope="session")
def sandbox_client() -> Path:
    """A compiled `ufo` client for this host, for a test that runs a real file op or the walk parity
    check. The ops are verbs on that binary now, so such a test needs a build of the crate: the
    integration pass builds one and fails loud without it, and a checkout that has none skips."""
    try:
        return client_binary()
    except RuntimeError as error:
        integration_dependency_available(False, str(error))
        pytest.skip(str(error))


@pytest.fixture
def sandbox_container(sandbox_image: str, tmp_path: Path) -> Iterator[tuple[str, Path]]:
    """A live container over a host-bind-mounted /workspace, set up exactly as prod: the host dir is
    chowned to the sandbox uid (prod's `ConversationSandbox.open` does the same with `os.chown` when
    serve runs as root) and the container then runs as the image's default non-root user. The chown
    runs in a throwaway `--user 0` container, so the test needs no host root.

    `--entrypoint` is what makes the chown run at all: the image declares a shell-form ENTRYPOINT,
    which discards every argument `docker run` appends. Passing `chown` as a bare argument silently
    runs the image's start command instead and never returns. For the same reason the long-running
    container takes no command — the ENTRYPOINT already is one, and any argument here would be
    dropped on the floor rather than honoured."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    docker_or_fail(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "chown",
            "--user",
            "0:0",
            "-v",
            f"{workspace}:/workspace",
            sandbox_image,
            "-R",
            f"{SANDBOX_UID}:{SANDBOX_GID}",
            "/workspace",
        ],
        timeout=CONTAINER_OP_TIMEOUT_S,
    )
    started = docker_or_fail(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--add-host",
            "host.docker.internal:host-gateway",
            "-v",
            f"{workspace}:/workspace",
            sandbox_image,
        ],
        timeout=CONTAINER_OP_TIMEOUT_S,
    )
    container = started.stdout.strip()
    try:
        owner = docker_or_fail(
            ["docker", "exec", container, "stat", "-c", "%u:%g", "/workspace"],
            timeout=CONTAINER_OP_TIMEOUT_S,
        )
        assert owner.stdout.strip() == f"{SANDBOX_UID}:{SANDBOX_GID}", (
            f"/workspace is owned by {owner.stdout.strip()}, not the sandbox user — the chown "
            f"container exited without chowning, so it discarded its argv"
        )
        yield container, workspace
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container],
            capture_output=True,
            check=False,
            timeout=CONTAINER_OP_TIMEOUT_S,
        )


async def _create_bucket_when_ready(store: S3BlobStore) -> None:
    deadline = time.monotonic() + MINIO_READY_SECONDS
    while True:
        try:
            async with get_session().create_client(
                "s3", endpoint_url=store.endpoint_url, region_name=store.region
            ) as client:
                await client.create_bucket(Bucket=store.bucket)
            return
        except (BotoCoreError, ClientError):
            if time.monotonic() > deadline:
                raise
            await asyncio.sleep(0.2)


@pytest.fixture(scope="module")
def s3_store() -> Iterator[S3BlobStore]:
    if not integration_dependency_available(
        shutil.which("docker") is not None, "Docker executable is not available"
    ):
        pytest.skip("Docker executable is not available")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    try:
        started = subprocess.run(
            ["docker", "run", "-d", "-p", f"{port}:9000", MINIO_IMAGE, "server", "/data"],
            capture_output=True,
            text=True,
            check=False,
            timeout=MINIO_OP_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        reason = f"docker run minio exceeded {MINIO_OP_TIMEOUT_S}s (stalled image pull)"
        integration_dependency_available(False, reason)
        pytest.skip(reason)
    reason = f"Docker cannot run minio: {started.stderr.strip()}"
    if not integration_dependency_available(started.returncode == 0, reason):
        pytest.skip(reason)
    container = started.stdout.strip()
    store = S3BlobStore(
        bucket=TEST_BUCKET, endpoint_url=f"http://127.0.0.1:{port}", region="us-east-1"
    )
    try:
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setenv("AWS_ACCESS_KEY_ID", MINIO_CREDENTIAL)
            patcher.setenv("AWS_SECRET_ACCESS_KEY", MINIO_CREDENTIAL)
            patcher.delenv("AWS_SESSION_TOKEN", raising=False)
            patcher.delenv("AWS_PROFILE", raising=False)
            asyncio.run(_create_bucket_when_ready(store))
            yield store
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)
