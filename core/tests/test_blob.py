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

from selfhost.blob import BlobNotFound, FilesystemBlobStore, S3BlobStore, blob_store_for
from selfhost.config import BlobConfig

MINIO_IMAGE = "minio/minio"
MINIO_CREDENTIAL = "minioadmin"
MINIO_READY_SECONDS = 60.0
TEST_BUCKET = "selfhost-test"


async def test_filesystem_round_trip(tmp_path: Path) -> None:
    store = FilesystemBlobStore(root=tmp_path)
    await store.put("conversations/c1/messages.json", b"hello")
    assert await store.get("conversations/c1/messages.json") == b"hello"


async def test_filesystem_exists(tmp_path: Path) -> None:
    store = FilesystemBlobStore(root=tmp_path)
    assert not await store.exists("absent")
    await store.put("present", b"x")
    assert await store.exists("present")


async def test_filesystem_get_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(BlobNotFound):
        await FilesystemBlobStore(root=tmp_path).get("absent")


@pytest.mark.parametrize("key", ["../escape", "/etc/passwd", "a/../../escape", "a/..", ""])
async def test_filesystem_traversal_key_rejected(tmp_path: Path, key: str) -> None:
    store = FilesystemBlobStore(root=tmp_path)
    with pytest.raises(ValueError):
        await store.put(key, b"x")


async def test_filesystem_overwrite_replaces(tmp_path: Path) -> None:
    store = FilesystemBlobStore(root=tmp_path)
    await store.put("key", b"first")
    await store.put("key", b"second")
    assert await store.get("key") == b"second"


def test_blob_store_for_filesystem(tmp_path: Path) -> None:
    store = blob_store_for(BlobConfig(backend="filesystem", root=tmp_path))
    assert store == FilesystemBlobStore(root=tmp_path)


def test_blob_store_for_s3() -> None:
    config = BlobConfig(backend="s3", bucket="b", endpoint_url="http://e", region="r")
    assert blob_store_for(config) == S3BlobStore(bucket="b", endpoint_url="http://e", region="r")


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
    started = subprocess.run(
        ["docker", "run", "-d", "-p", f"{port}:9000", MINIO_IMAGE, "server", "/data"],
        capture_output=True,
        text=True,
        check=False,
    )
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


async def test_s3_round_trip(s3_store: S3BlobStore) -> None:
    await s3_store.put("conversations/c1/messages.json", b"hello")
    assert await s3_store.get("conversations/c1/messages.json") == b"hello"


async def test_s3_exists(s3_store: S3BlobStore) -> None:
    assert not await s3_store.exists("absent")
    await s3_store.put("present", b"x")
    assert await s3_store.exists("present")


async def test_s3_get_missing_raises(s3_store: S3BlobStore) -> None:
    with pytest.raises(BlobNotFound):
        await s3_store.get("absent")


async def test_s3_overwrite_replaces(s3_store: S3BlobStore) -> None:
    await s3_store.put("versioned", b"first")
    await s3_store.put("versioned", b"second")
    assert await s3_store.get("versioned") == b"second"
