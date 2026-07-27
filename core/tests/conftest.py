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

from sandbox.build_template import ROOT, pod_dockerfile
from ufo.blob import S3BlobStore
from ufo.sandbox.session import SANDBOX_GID, SANDBOX_UID

MINIO_IMAGE = "minio/minio"
MINIO_CREDENTIAL = "minioadmin"
MINIO_OP_TIMEOUT_S = 180
MINIO_READY_SECONDS = 60.0
TEST_BUCKET = "ufo-test"
SANDBOX_TEST_IMAGE = "ufo-sandbox:test"
IMAGE_BUILD_TIMEOUT_S = 1200
CONTAINER_OP_TIMEOUT_S = 180


def docker_or_skip(
    argv: list[str], *, timeout: int, stdin_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a docker CLI command with a hard wall. A stalled image pull/build or a wedged daemon is
    external, network-bound work; bounding it skips this docker-gated test with a clear reason
    instead of hanging the whole suite forever (a client's wait always ends)."""
    try:
        return subprocess.run(
            argv, input=stdin_text, capture_output=True, text=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        pytest.skip(f"docker '{argv[1]}' exceeded {timeout}s (stalled pull/build or wedged daemon)")


def docker_or_fail(argv: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    """Run a docker CLI command against the already-built image. Unlike the build, these are local
    operations on local state, so a breached wall or a nonzero exit is our own bug and fails the
    test — never skips it. Skipping is what let a container that ignored its argv, and so never ran
    the command it was given, read as an absent dependency for 21 straight runs."""
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise AssertionError(
            f"docker '{argv[1]}' exceeded {timeout}s — the container never exited, so it ignored "
            f"the command it was given: {' '.join(argv)}"
        ) from None
    assert completed.returncode == 0, f"docker '{argv[1]}' failed: {completed.stderr.strip()}"
    return completed


@pytest.fixture(scope="session")
def sandbox_image() -> str:
    """The real sandbox image every docker-gated test runs against, built once per session. Session
    scope is what the build actually is — one tag in one daemon, global to the run. A narrower scope
    rebuilds it each time the `db` param reorders tests across the owning module's boundary."""
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")
    built = docker_or_skip(
        ["docker", "build", "-t", SANDBOX_TEST_IMAGE, "-f", "-", str(ROOT)],
        timeout=IMAGE_BUILD_TIMEOUT_S,
        stdin_text=pod_dockerfile(),
    )
    if built.returncode != 0:
        pytest.skip(f"cannot build the sandbox image: {built.stderr.strip()}")
    return SANDBOX_TEST_IMAGE


@pytest.fixture
def sandbox_container(sandbox_image: str, tmp_path: Path) -> Iterator[tuple[str, Path]]:
    """A live container over a host-bind-mounted /workspace, set up exactly as prod: the mount is
    chowned to the sandbox uid (prod's `_workspace_mount` does the same with `os.chown` when serve
    runs as root) and the container then runs as the image's default non-root user. The chown runs
    in a throwaway `--user 0` container, so the test needs no host root.

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
        ["docker", "run", "-d", "--rm", "-v", f"{workspace}:/workspace", sandbox_image],
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
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")
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
        pytest.skip(f"docker run minio exceeded {MINIO_OP_TIMEOUT_S}s (stalled image pull)")
    if started.returncode != 0:
        pytest.skip(f"docker cannot run minio: {started.stderr.strip()}")
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
