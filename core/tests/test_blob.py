import asyncio
from base64 import b64encode
from collections.abc import AsyncIterator
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import AsyncClient

from ufo import blob
from ufo.blob import (
    BLOB_ROOT_SETTING,
    BlobNotFound,
    FilesystemBlobStore,
    S3BlobStore,
    blob_store_for,
)
from ufo.config import BlobConfig
from ufo.sandbox.containment import NonDirectoryAncestor


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


async def test_filesystem_symlinked_store_root_is_followed(tmp_path: Path) -> None:
    """The store root is deploy config, not a path an agent can reach, and `/var/lib/ufo/blobs ->
    /mnt/data/blobs` is an ordinary compose or k8s layout — so the link is followed once, keys are
    contained under the canonical result, and the store works. Refusing it would take transcripts,
    compaction records and every shared artifact down on a deploy doing nothing unusual."""
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_root = tmp_path / "blobs"
    linked_root.symlink_to(outside, target_is_directory=True)
    store = FilesystemBlobStore(root=linked_root)

    await store.put("artifacts/x/report.txt", b"bytes")

    assert await store.get("artifacts/x/report.txt") == b"bytes"
    assert [entry.key for entry in await store.list("artifacts/")] == ["artifacts/x/report.txt"]
    assert (outside / "artifacts" / "x" / "report.txt").read_bytes() == b"bytes"


async def test_filesystem_store_root_that_is_not_a_directory_names_its_setting(
    tmp_path: Path,
) -> None:
    """An operator who pointed the key at a file reads the key back in the message: the fix is in
    their config, not in any key the store was handed."""
    not_a_directory = tmp_path / "blobs"
    not_a_directory.write_bytes(b"not a directory")
    store = FilesystemBlobStore(root=not_a_directory)

    with pytest.raises(NonDirectoryAncestor, match=BLOB_ROOT_SETTING):
        await store.put("artifacts/x/report.txt", b"bytes")

    assert not_a_directory.read_bytes() == b"not a directory"


async def test_filesystem_key_through_a_planted_symlink_refused(tmp_path: Path) -> None:
    """A link planted inside the store resolves out of it, so a key naming it is refused rather than
    written through — the store holds artifact bytes an agent named, on a filesystem it also has a
    workspace on."""
    root = tmp_path / "blobs"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "artifacts").symlink_to(outside, target_is_directory=True)
    store = FilesystemBlobStore(root=root)

    with pytest.raises(ValueError):
        await store.put("artifacts/x/report.txt", b"bytes")

    assert list(outside.iterdir()) == []


async def test_filesystem_overwrite_replaces(tmp_path: Path) -> None:
    store = FilesystemBlobStore(root=tmp_path)
    await store.put("key", b"first")
    await store.put("key", b"second")
    assert await store.get("key") == b"second"


async def test_filesystem_list_scopes_to_the_prefix_sorted(tmp_path: Path) -> None:
    store = FilesystemBlobStore(root=tmp_path)
    await store.put("conversations/c1/workspace/b.txt", b"bb")
    await store.put("conversations/c1/workspace/a/deep.txt", b"d")
    await store.put("conversations/c1/messages.json.lz4", b"transcript")
    await store.put("conversations/c2/workspace/other.txt", b"o")
    entries = await store.list("conversations/c1/workspace/")
    assert [entry.key for entry in entries] == [
        "conversations/c1/workspace/a/deep.txt",
        "conversations/c1/workspace/b.txt",
    ]
    assert entries[1].size_bytes == 2
    assert entries[0].modified_at.tzinfo is not None


async def test_filesystem_list_requires_a_prefix(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        await FilesystemBlobStore(root=tmp_path).list("")


async def test_filesystem_list_of_absent_prefix_is_empty(tmp_path: Path) -> None:
    assert await FilesystemBlobStore(root=tmp_path).list("conversations/none/") == ()


async def test_filesystem_delete_removes_and_absent_is_a_noop(tmp_path: Path) -> None:
    store = FilesystemBlobStore(root=tmp_path)
    await store.put("artifacts/x/report.txt", b"bytes")
    await store.delete("artifacts/x/report.txt")
    assert not await store.exists("artifacts/x/report.txt")
    await store.delete("artifacts/x/report.txt")


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


async def test_s3_delete_removes_and_absent_is_a_noop(s3_store: S3BlobStore) -> None:
    await s3_store.put("artifacts/x/report.txt", b"bytes")
    await s3_store.delete("artifacts/x/report.txt")
    assert not await s3_store.exists("artifacts/x/report.txt")
    await s3_store.delete("artifacts/x/report.txt")


async def test_s3_list_scopes_to_the_prefix_sorted(s3_store: S3BlobStore) -> None:
    await s3_store.put("listing/c1/workspace/b.txt", b"bb")
    await s3_store.put("listing/c1/workspace/a/deep.txt", b"d")
    await s3_store.put("listing/c1/messages.json.lz4", b"transcript")
    await s3_store.put("listing/c2/workspace/other.txt", b"o")
    entries = await s3_store.list("listing/c1/workspace/")
    assert [entry.key for entry in entries] == [
        "listing/c1/workspace/a/deep.txt",
        "listing/c1/workspace/b.txt",
    ]
    assert entries[1].size_bytes == 2
    assert entries[0].modified_at.tzinfo is not None


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


async def test_s3_presigned_put_accepts_only_the_measured_bytes(s3_store: S3BlobStore) -> None:
    """The URL serve hands the sandbox is authority to store one measured file under one key, and
    nothing else: the signed content length and sha256 make S3 reject a body of another size and a
    body of another content, so the sandbox cannot substitute what it uploads and serve has nothing
    left to verify. Run against real object storage — this is a property of the signature and of
    the server, not of our code."""
    payload = bytes(range(256)) * 64
    checksum = b64encode(sha256(payload).digest()).decode()
    url = await s3_store.presigned_put("artifacts/abc/report.bin", len(payload), checksum, 300)
    headers = {"x-amz-checksum-sha256": checksum}

    async with AsyncClient() as client:
        accepted = await client.put(url, content=payload, headers=headers)
        tampered = await client.put(url, content=b"z" * len(payload), headers=headers)
        resized = await client.put(url, content=payload + b"more", headers=headers)

    assert accepted.status_code == 200
    assert tampered.status_code == 400
    assert "XAmzContentChecksumMismatch" in tampered.text
    assert resized.status_code == 403
    assert "SignatureDoesNotMatch" in resized.text
    assert await s3_store.get("artifacts/abc/report.bin") == payload


async def test_s3_put_host_is_the_hostname_a_presigned_url_resolves_to(
    s3_store: S3BlobStore,
) -> None:
    """The proxy admits `put_host()` exactly, so a URL that resolves anywhere else is refused at
    CONNECT. Reading the host off a signed URL is what keeps the rule and the URL from drifting
    apart over addressing style or region — and it must be the bare hostname, since the proxy's
    CONNECT parser splits the port off before matching; this fixture's endpoint carries one, so it
    proves the port never leaks into the rule."""
    url = await s3_store.presigned_put("artifacts/abc/host.bin", 1, "x" * 44, 60)
    host = await s3_store.put_host()
    assert urlsplit(url).hostname == host
    assert ":" not in host


async def test_s3_presigned_put_signs_v4_with_both_measurements(s3_store: S3BlobStore) -> None:
    """Left to botocore's defaults a presigned S3 URL is SigV2, which the deploy's temporary
    credentials cannot authorize; and without both measurements in the signed headers the sandbox
    could upload anything under the key. Assert the query, not the client's reported config — the
    client reports `s3v4` either way."""
    url = await s3_store.presigned_put("artifacts/abc/signed.bin", 7, "y" * 44, 60)
    query = parse_qs(urlsplit(url).query)
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-SignedHeaders"] == ["content-length;host;x-amz-checksum-sha256"]


async def test_s3_client_is_built_once_per_loop() -> None:
    store = S3BlobStore(bucket="b", endpoint_url="http://localhost:1", region="us-east-1")
    first = await store._client()
    assert await store._client() is first


def test_s3_clients_are_loop_affine() -> None:
    store = S3BlobStore(bucket="b", endpoint_url="http://localhost:1", region="us-east-1")
    first = asyncio.run(store._client())
    second = asyncio.run(store._client())
    assert first is not second


class _StubClient:
    def __init__(self, close_error: Exception | None = None) -> None:
        self.closed = False
        self.close_error = close_error

    async def __aexit__(self, *exc: object) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class _StubCreator:
    """Stands in for aiobotocore's ClientCreatorContext: yields once before producing the client,
    so two concurrent first calls both pass the cache-miss check and the setdefault race runs."""

    def __init__(self, made: list[_StubClient], close_error: Exception | None = None) -> None:
        self.made = made
        self.close_error = close_error

    async def __aenter__(self) -> _StubClient:
        await asyncio.sleep(0)
        client = _StubClient(close_error=self.close_error)
        self.made.append(client)
        return client


async def test_concurrent_first_calls_share_one_client_and_close_the_loser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made: list[_StubClient] = []
    session = SimpleNamespace(create_client=lambda *args, **kwargs: _StubCreator(made))
    monkeypatch.setattr(blob, "get_session", lambda: session)
    store = S3BlobStore(bucket="b")
    first, second = await asyncio.gather(store._client(), store._client())
    assert first is second
    assert len(made) == 2
    assert sum(1 for client in made if client.closed) == 1
    assert not first.closed


async def test_losers_failing_close_does_not_mask_the_won_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made: list[_StubClient] = []
    session = SimpleNamespace(
        create_client=lambda *args, **kwargs: _StubCreator(made, close_error=RuntimeError("boom"))
    )
    monkeypatch.setattr(blob, "get_session", lambda: session)
    store = S3BlobStore(bucket="b")
    first, second = await asyncio.gather(store._client(), store._client())
    assert first is second is made[0]
    assert made[1].closed
