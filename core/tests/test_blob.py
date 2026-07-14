from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from ufo import blob
from ufo.blob import (
    S3_MULTIPART_PART_BYTES,
    BlobNotFound,
    FilesystemBlobStore,
    S3BlobStore,
    blob_store_for,
)
from ufo.config import BlobConfig


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


async def test_filesystem_put_file_streams_from_disk(tmp_path: Path) -> None:
    source = tmp_path / "artifact.bin"
    payload = bytes(range(256)) * 4096
    source.write_bytes(payload)
    store = FilesystemBlobStore(root=tmp_path / "blobs")
    await store.put_file("artifacts/x/artifact.bin", source)
    assert await store.get("artifacts/x/artifact.bin") == payload


async def test_filesystem_copy_duplicates_bytes(tmp_path: Path) -> None:
    store = FilesystemBlobStore(root=tmp_path)
    payload = bytes(range(256)) * 64
    await store.put("workspace/report.bin", payload)
    await store.copy("workspace/report.bin", "artifacts/x/report.bin")
    assert await store.get("artifacts/x/report.bin") == payload
    assert await store.get("workspace/report.bin") == payload


async def test_filesystem_copy_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(BlobNotFound):
        await FilesystemBlobStore(root=tmp_path).copy("absent", "artifacts/x/y")


def test_blob_store_for_filesystem(tmp_path: Path) -> None:
    store = blob_store_for(BlobConfig(backend="filesystem", root=tmp_path))
    assert store == FilesystemBlobStore(root=tmp_path)


def test_blob_store_for_s3() -> None:
    config = BlobConfig(backend="s3", bucket="b", endpoint_url="http://e", region="r")
    assert blob_store_for(config) == S3BlobStore(bucket="b", endpoint_url="http://e", region="r")


@pytest.mark.docker
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


async def test_s3_stream_round_trip(s3_store: S3BlobStore) -> None:
    payload = bytes(range(256)) * 64

    async def chunks() -> AsyncIterator[bytes]:
        yield payload[:1000]
        yield payload[1000:]

    await s3_store.put_stream("artifacts/s/stream.bin", chunks())
    collected = bytearray()
    async for chunk in s3_store.get_stream("artifacts/s/stream.bin"):
        collected += chunk
    assert bytes(collected) == payload


async def test_s3_get_stream_missing_raises(s3_store: S3BlobStore) -> None:
    with pytest.raises(BlobNotFound):
        async for _ in s3_store.get_stream("absent"):
            pass


async def test_s3_put_file_multipart_streams_from_disk(
    s3_store: S3BlobStore, tmp_path: Path
) -> None:
    source = tmp_path / "big.bin"
    payload = bytes(range(256)) * (S3_MULTIPART_PART_BYTES // 128)
    source.write_bytes(payload)
    assert len(payload) > S3_MULTIPART_PART_BYTES
    await s3_store.put_file("artifacts/y/big.bin", source)
    assert await s3_store.get("artifacts/y/big.bin") == payload


async def test_s3_put_file_empty(s3_store: S3BlobStore, tmp_path: Path) -> None:
    source = tmp_path / "empty.bin"
    source.write_bytes(b"")
    await s3_store.put_file("artifacts/z/empty.bin", source)
    assert await s3_store.get("artifacts/z/empty.bin") == b""


async def test_s3_copy_within_bucket(s3_store: S3BlobStore) -> None:
    await s3_store.put("workspace/report.pdf", b"report-bytes")
    await s3_store.copy("workspace/report.pdf", "artifacts/abc/report.pdf")
    assert await s3_store.get("artifacts/abc/report.pdf") == b"report-bytes"


async def test_s3_copy_missing_source_raises(s3_store: S3BlobStore) -> None:
    with pytest.raises(BlobNotFound):
        await s3_store.copy("absent", "artifacts/x/y")


async def test_s3_copy_multipart_within_bucket(
    s3_store: S3BlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Above the single-copy ceiling the copy walks server-side UploadPartCopy ranges and completes
    the multipart. The ceiling and part size are dropped to 5 MiB so a small object exercises the
    path without a multi-gigabyte object; minio, like S3, still requires every part but the last to
    be at least 5 MiB, so the two exact-5 MiB parts are valid."""
    part = 5 * 1024 * 1024
    monkeypatch.setattr(blob, "S3_SINGLE_COPY_MAX_BYTES", part)
    monkeypatch.setattr(blob, "S3_COPY_PART_BYTES", part)
    payload = bytes(range(256)) * (part // 128)
    assert len(payload) > part
    await s3_store.put("workspace/big.bin", payload)
    await s3_store.copy("workspace/big.bin", "artifacts/big/big.bin")
    assert await s3_store.get("artifacts/big/big.bin") == payload
