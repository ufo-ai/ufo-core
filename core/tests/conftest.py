import asyncio
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator

import pytest
from aiobotocore.session import get_session
from botocore.exceptions import BotoCoreError, ClientError

from ufo.blob import S3BlobStore

MINIO_IMAGE = "minio/minio"
MINIO_CREDENTIAL = "minioadmin"
MINIO_OP_TIMEOUT_S = 180
MINIO_READY_SECONDS = 60.0
TEST_BUCKET = "ufo-test"


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
