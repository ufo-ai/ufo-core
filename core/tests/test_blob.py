import asyncio
from base64 import b64encode
from collections.abc import AsyncIterator
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from httpx import AsyncClient

from ufo import blob
from ufo.blob import (
    BLOB_ROOT_SETTING,
    BlobNotFound,
    FilesystemBlobStore,
    FleetBlobStore,
    S3BlobStore,
    WorkspaceBlobStore,
    blob_store_for,
)
from ufo.config import BlobConfig
from ufo.harness.containment import NonDirectoryAncestor
from ufo.runtime.workspace import WorkspaceUnbound, ws


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


async def test_s3_presigned_put_unmeasured_signs_neither_measurement(
    s3_store: S3BlobStore,
) -> None:
    """The preview renderer cannot report its output's size at mint time, so its PUT is unmeasured:
    only `host` is signed, and a PUT of any length under the key succeeds. The key is still fixed by
    the URL, so it stays authority over exactly one key — this is the whole difference from the
    measured `presigned_put`."""
    url = await s3_store.presigned_put_unmeasured("artifacts/abc/preview.png", 60)
    query = parse_qs(urlsplit(url).query)
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-SignedHeaders"] == ["host"]

    payload = b"a preview png of any length"
    async with AsyncClient() as client:
        put = await client.put(url, content=payload)
    assert put.status_code == 200
    assert await s3_store.get("artifacts/abc/preview.png") == payload


async def test_s3_presigned_get_fetches_the_stored_bytes(s3_store: S3BlobStore) -> None:
    """The read-side mirror of `presigned_put`: a URL any holder can GET the exact stored bytes
    from, for exactly as long as the ttl grants, and nothing else — no query authorizes writing or
    naming a different key."""
    await s3_store.put("artifacts/abc/source.pdf", b"source bytes")
    url = await s3_store.presigned_get("artifacts/abc/source.pdf", 60)

    async with AsyncClient() as client:
        fetched = await client.get(url)

    assert fetched.status_code == 200
    assert fetched.content == b"source bytes"


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
    def __init__(self) -> None:
        self.closed = False

    async def __aexit__(self, *exc: object) -> None:
        self.closed = True


class _StubCreator:
    def __init__(self, made: list[_StubClient]) -> None:
        self.made = made

    async def __aenter__(self) -> _StubClient:
        await asyncio.sleep(0)
        client = _StubClient()
        self.made.append(client)
        return client


async def test_concurrent_first_calls_build_one_session_and_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made: list[_StubClient] = []
    sessions = 0

    def get_stub_session():
        nonlocal sessions
        sessions += 1
        return SimpleNamespace(create_client=lambda *_args, **_kwargs: _StubCreator(made))

    monkeypatch.setattr(blob, "get_session", get_stub_session)
    store = S3BlobStore(bucket="b")
    clients = await asyncio.gather(*(store._client() for _ in range(26)))
    assert all(client is clients[0] for client in clients)
    assert sessions == 1
    assert made == [clients[0]]
    assert not clients[0].closed


async def test_workspace_store_prefixes_keys_under_the_bound_workspace(tmp_path: Path) -> None:
    """The workspace is never an argument: the store reads the ambient `ws(...)` scope at call
    time, so the same instance serves every workspace and no caller can name the prefix."""
    backend = FilesystemBlobStore(root=tmp_path)
    store = WorkspaceBlobStore(backend=backend)
    workspace_id = uuid4()
    with ws(workspace_id):
        await store.put("conversations/c1/messages.json", b"hello")
        assert await store.get("conversations/c1/messages.json") == b"hello"
    full_key = f"workspaces/{workspace_id}/conversations/c1/messages.json"
    assert await backend.get(full_key) == b"hello"


async def test_workspace_store_isolates_workspaces(tmp_path: Path) -> None:
    store = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    with ws(uuid4()):
        await store.put("artifacts/x/report.txt", b"bytes")
    with ws(uuid4()):
        assert not await store.exists("artifacts/x/report.txt")
        with pytest.raises(BlobNotFound):
            await store.get("artifacts/x/report.txt")


async def test_workspace_store_unbound_fails_loud(tmp_path: Path) -> None:
    store = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    with pytest.raises(WorkspaceUnbound):
        await store.put("artifacts/x/report.txt", b"bytes")
    with pytest.raises(WorkspaceUnbound):
        await store.list("artifacts/")


async def test_workspace_store_list_returns_workspace_relative_keys(tmp_path: Path) -> None:
    store = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    with ws(uuid4()):
        await store.put("conversations/c1/compactions/0/before.json.lz4", b"bb")
        await store.put("conversations/c1/compactions/1/before.json.lz4", b"b")
        await store.put("conversations/c2/compactions/0/before.json.lz4", b"other")
        entries = await store.list("conversations/c1/compactions/")
        assert [entry.key for entry in entries] == [
            "conversations/c1/compactions/0/before.json.lz4",
            "conversations/c1/compactions/1/before.json.lz4",
        ]
        assert entries[0].size_bytes == 2
    with ws(uuid4()):
        assert await store.list("conversations/c1/compactions/") == ()


async def test_workspace_store_refuses_an_already_prefixed_key(tmp_path: Path) -> None:
    """A full key reaching the scoped store is an un-migrated call site — refused loudly, never
    stored twice-prefixed."""
    store = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    with ws(uuid4()):
        with pytest.raises(ValueError):
            await store.put(f"workspaces/{uuid4()}/artifacts/x/report.txt", b"bytes")


async def test_workspace_store_list_requires_a_prefix(tmp_path: Path) -> None:
    store = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    with ws(uuid4()):
        with pytest.raises(ValueError):
            await store.list("")


async def test_workspace_store_streams_round_trip(tmp_path: Path) -> None:
    backend = FilesystemBlobStore(root=tmp_path)
    store = WorkspaceBlobStore(backend=backend)
    payload = bytes(range(256)) * 8

    async def chunks() -> AsyncIterator[bytes]:
        yield payload[:100]
        yield payload[100:]

    workspace_id = uuid4()
    with ws(workspace_id):
        await store.put_stream("artifacts/s/stream.bin", chunks())
        stream = store.get_stream("artifacts/s/stream.bin")
    collected = bytearray()
    async for chunk in stream:
        collected += chunk
    assert bytes(collected) == payload
    assert await backend.exists(f"workspaces/{workspace_id}/artifacts/s/stream.bin")


async def test_workspace_store_delete_removes(tmp_path: Path) -> None:
    backend = FilesystemBlobStore(root=tmp_path)
    store = WorkspaceBlobStore(backend=backend)
    workspace_id = uuid4()
    with ws(workspace_id):
        await store.put("artifacts/x/report.txt", b"bytes")
        await store.delete("artifacts/x/report.txt")
        assert not await store.exists("artifacts/x/report.txt")
    assert not await backend.exists(f"workspaces/{workspace_id}/artifacts/x/report.txt")


async def test_fleet_store_admits_only_fleet_namespaces(tmp_path: Path) -> None:
    """The fleet store is the one unprefixed handle, so its namespace is closed: keys outside the
    declared fleet families are refused, and workspace data cannot flow through it."""
    backend = FilesystemBlobStore(root=tmp_path)
    store = FleetBlobStore(backend=backend)
    await store.put("static/web/assets/index-abc123.js", b"js")
    await store.put("term/op/o1", b"payload")
    await store.put("apps/9f3a1c2b/radar/index.html", b"<!doctype html>")
    assert await store.get("static/web/assets/index-abc123.js") == b"js"
    assert await backend.get("static/web/assets/index-abc123.js") == b"js"
    assert await store.get("apps/9f3a1c2b/radar/index.html") == b"<!doctype html>"
    with pytest.raises(ValueError):
        await store.put("conversations/c1/messages.json", b"hello")
    with pytest.raises(ValueError):
        await store.get(f"workspaces/{uuid4()}/artifacts/x/report.txt")


async def test_workspace_store_presigns_the_full_key(s3_store: S3BlobStore) -> None:
    store = WorkspaceBlobStore(backend=s3_store)
    workspace_id = uuid4()
    with ws(workspace_id):
        url = await store.presigned_put("artifacts/abc/report.bin", 1, "x" * 44, 60)
    assert f"workspaces/{workspace_id}/artifacts/abc/report.bin" in urlsplit(url).path


async def test_workspace_store_presigned_get_reads_the_full_key(s3_store: S3BlobStore) -> None:
    store = WorkspaceBlobStore(backend=s3_store)
    workspace_id = uuid4()
    with ws(workspace_id):
        await store.put("artifacts/abc/source.pdf", b"source bytes")
        url = await store.presigned_get("artifacts/abc/source.pdf", 60)
    assert f"workspaces/{workspace_id}/artifacts/abc/source.pdf" in urlsplit(url).path
