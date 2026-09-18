"""Blob storage behind one async protocol: filesystem for dev, S3 for deploys."""

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from aiobotocore.client import AioBaseClient
from aiobotocore.session import get_session
from botocore.config import Config
from botocore.exceptions import ClientError

from ufo.config import BlobConfig
from ufo.runtime.workspace import ws_current

MISSING_KEY_CODES = ("404", "NoSuchKey", "NotFound")
BLOB_STREAM_CHUNK_BYTES = 1024 * 1024
S3_MULTIPART_PART_BYTES = 8 * 1024 * 1024
S3_MAX_PARTS = 10_000
S3_MIN_PART_BYTES = 5 * 1024 * 1024
S3_MAX_PART_BYTES = 5 * 1024 * 1024 * 1024
BLOB_LIST_MAX_KEYS = 10_000
BLOB_ROOT_SETTING = "blob.root"
WORKSPACE_KEY_PREFIX = "workspaces/"
FLEET_KEY_PREFIXES = ("static/", "term/", "apps/")
S3_VIRTUAL_CONFIG = Config(signature_version="s3v4", s3={"addressing_style": "virtual"})
S3_PATH_CONFIG = Config(signature_version="s3v4", s3={"addressing_style": "path"})


class BlobNotFound(KeyError):
    """Raised by get for a key that does not exist."""


@dataclass(frozen=True)
class BlobEntry:
    """One stored object as `list` reports it: its full key, byte size, and last-modified time."""

    key: str
    size_bytes: int
    modified_at: datetime


class BlobStore(Protocol):
    """Async byte storage keyed by slash-separated string keys. `get_stream`/`put_stream` move a
    payload in bounded chunks so an arbitrary-large blob (a shared attachment, an inbound file)
    crosses without a whole-file buffer; `get`/`put` are the whole-bytes shorthands. Writing an
    artifact is the one op with two shapes and so is not a member here: on S3 serve mints a
    presigned PUT and the sandbox uploads to it directly, on the filesystem backend the bytes
    stream through `put_stream`."""

    async def put(self, key: str, data: bytes) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def exists(self, key: str) -> bool: ...

    async def delete(self, key: str) -> None:
        """Remove a stored object; deleting an absent key is a no-op, so a retried delete holds."""
        ...

    def get_stream(self, key: str) -> AsyncIterator[bytes]: ...

    async def put_stream(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        part_size_bytes: int = S3_MULTIPART_PART_BYTES,
        tags: dict[str, str] | None = None,
    ) -> None: ...

    async def list(self, prefix: str) -> tuple[BlobEntry, ...]:
        """Every stored object under a key prefix, sorted by key, capped at `BLOB_LIST_MAX_KEYS`
        entries — the bounded enumeration a read view (a conversation's rollover records) walks;
        never a whole-store scan, so the prefix is required."""
        ...


@dataclass(frozen=True)
class FilesystemBlobStore:
    """Keys become files under root; writes are atomic via temp file + replace."""

    root: Path

    async def put(self, key: str, data: bytes) -> None:
        path = self._resolve(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        temp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        await asyncio.to_thread(temp.write_bytes, data)
        await asyncio.to_thread(temp.replace, path)

    async def get(self, key: str) -> bytes:
        path = self._resolve(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as error:
            raise BlobNotFound(key) from error

    async def exists(self, key: str) -> bool:
        path = self._resolve(key)
        return await asyncio.to_thread(path.is_file)

    async def delete(self, key: str) -> None:
        path = self._resolve(key)
        await asyncio.to_thread(path.unlink, missing_ok=True)

    async def get_stream(self, key: str) -> AsyncIterator[bytes]:
        path = self._resolve(key)
        try:
            handle = await asyncio.to_thread(path.open, "rb")
        except FileNotFoundError as error:
            raise BlobNotFound(key) from error
        try:
            while True:
                chunk = await asyncio.to_thread(handle.read, BLOB_STREAM_CHUNK_BYTES)
                if not chunk:
                    return
                yield chunk
        finally:
            await asyncio.to_thread(handle.close)

    async def put_stream(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        part_size_bytes: int = S3_MULTIPART_PART_BYTES,
        tags: dict[str, str] | None = None,
    ) -> None:
        path = self._resolve(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        temp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        handle = await asyncio.to_thread(temp.open, "wb")
        try:
            async for chunk in chunks:
                await asyncio.to_thread(handle.write, chunk)
            await asyncio.to_thread(handle.close)
            await asyncio.to_thread(temp.replace, path)
        except BaseException:
            await asyncio.to_thread(handle.close)
            await asyncio.to_thread(temp.unlink, missing_ok=True)
            raise

    async def list(self, prefix: str) -> tuple[BlobEntry, ...]:
        if not prefix:
            raise ValueError("blob list requires a key prefix")
        return await asyncio.to_thread(self._walk, prefix)

    def _walk(self, prefix: str) -> tuple[BlobEntry, ...]:
        root = self._canonical_root()
        base_dir = self._resolve(prefix) if prefix.endswith("/") else self._resolve(prefix).parent
        if not base_dir.is_dir():
            return ()
        entries: list[BlobEntry] = []
        for base, _dirs, names in os.walk(base_dir):
            for name in names:
                path = Path(base) / name
                key = path.relative_to(root).as_posix()
                if not key.startswith(prefix) or name.endswith(".tmp"):
                    continue
                stat = path.stat()
                entries.append(
                    BlobEntry(
                        key=key,
                        size_bytes=stat.st_size,
                        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                    )
                )
        entries.sort(key=lambda entry: entry.key)
        return tuple(entries[:BLOB_LIST_MAX_KEYS])

    def _resolve(self, key: str) -> Path:
        root = self._canonical_root()
        path = (root / key).resolve()
        if path == root or not path.is_relative_to(root):
            raise ValueError(f"blob key escapes store root: {key!r}")
        return path

    def _canonical_root(self) -> Path:
        root = self.root.resolve()
        if root.exists() and not root.is_dir():
            raise NotADirectoryError(
                f"{BLOB_ROOT_SETTING} names {self.root}, which is not a directory"
            )
        return root


def _is_missing_key(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") in MISSING_KEY_CODES


@dataclass(frozen=True)
class S3BlobStore:
    """Single-shot object storage over aiobotocore; one client per event loop, held for the
    process's life. Building a client parses the whole botocore service model on the calling
    loop — tens of milliseconds of CPU — so a per-call client starves every task sharing the
    loop under per-page blob traffic. Blob ops run on both the serve loop and the DBOS
    background loop, and an aiohttp-backed client is loop-affine, so the cache keys by the
    running loop."""

    bucket: str
    endpoint_url: str | None = None
    region: str | None = None
    _clients: dict[asyncio.AbstractEventLoop, AioBaseClient] = field(
        default_factory=dict, compare=False
    )
    _client_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = field(
        default_factory=dict, compare=False
    )

    async def put(self, key: str, data: bytes) -> None:
        client = await self._client()
        await client.put_object(Bucket=self.bucket, Key=key, Body=data)

    async def get(self, key: str) -> bytes:
        client = await self._client()
        try:
            response = await client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if _is_missing_key(error):
                raise BlobNotFound(key) from error
            raise
        body = response["Body"]
        async with body:
            return await body.read()

    async def exists(self, key: str) -> bool:
        client = await self._client()
        try:
            await client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if _is_missing_key(error):
                return False
            raise
        return True

    async def delete(self, key: str) -> None:
        client = await self._client()
        await client.delete_object(Bucket=self.bucket, Key=key)

    async def get_stream(self, key: str) -> AsyncIterator[bytes]:
        client = await self._client()
        try:
            response = await client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if _is_missing_key(error):
                raise BlobNotFound(key) from error
            raise
        body = response["Body"]
        async with body:
            async for chunk in body.iter_chunks(BLOB_STREAM_CHUNK_BYTES):
                yield chunk

    async def put_stream(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        part_size_bytes: int = S3_MULTIPART_PART_BYTES,
        tags: dict[str, str] | None = None,
    ) -> None:
        if not S3_MIN_PART_BYTES <= part_size_bytes <= S3_MAX_PART_BYTES:
            raise ValueError("S3 multipart part size is outside its supported range")
        client = await self._client()
        tagging = {} if not tags else {"Tagging": urlencode(tags)}
        buffer = bytearray()
        upload_id: str | None = None
        parts: list[dict[str, object]] = []
        part_number = 1
        try:
            async for chunk in chunks:
                buffer += chunk
                if len(buffer) < part_size_bytes:
                    continue
                if upload_id is None:
                    started = await client.create_multipart_upload(
                        Bucket=self.bucket, Key=key, **tagging
                    )
                    upload_id = started["UploadId"]
                if part_number > S3_MAX_PARTS:
                    raise ValueError("multipart upload exceeds the S3 part limit")
                uploaded = await client.upload_part(
                    Bucket=self.bucket,
                    Key=key,
                    PartNumber=part_number,
                    UploadId=upload_id,
                    Body=bytes(buffer),
                )
                parts.append({"ETag": uploaded["ETag"], "PartNumber": part_number})
                part_number += 1
                buffer = bytearray()
            if upload_id is None:
                await client.put_object(Bucket=self.bucket, Key=key, Body=bytes(buffer), **tagging)
                return
            if buffer:
                if part_number > S3_MAX_PARTS:
                    raise ValueError("multipart upload exceeds the S3 part limit")
                uploaded = await client.upload_part(
                    Bucket=self.bucket,
                    Key=key,
                    PartNumber=part_number,
                    UploadId=upload_id,
                    Body=bytes(buffer),
                )
                parts.append({"ETag": uploaded["ETag"], "PartNumber": part_number})
            await client.complete_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except BaseException:
            if upload_id is not None:
                await client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
            raise

    async def presigned_put(
        self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int
    ) -> str:
        """A URL the sandbox can PUT `key` to itself, valid only for exactly these bytes. Signing
        `ContentLength` and `ChecksumSHA256` (base64, the header the PUT must carry) puts both in
        `X-Amz-SignedHeaders`, so S3 answers `SignatureDoesNotMatch` for a body of another length
        and `XAmzContentChecksumMismatch` for other content: the URL is authority to store one
        measured file under one key until it expires, not write access to the key. That is what lets
        an untrusted sandbox hold it, and what makes the size serve records the size S3 accepted,
        with nothing left to re-verify afterwards.

        The content type is deliberately unsigned — a signed header the client must reproduce
        byte-identically buys nothing here, since the media type serve records comes from the
        filename."""
        client = await self._client()
        return await client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ContentLength": size_bytes,
                "ChecksumSHA256": checksum_sha256,
            },
            ExpiresIn=ttl_seconds,
        )

    async def presigned_put_unmeasured(self, key: str, ttl_seconds: int) -> str:
        """A presigned PUT that signs only the key and expiry, not the body's size or checksum — so
        the holder may store bytes of any length under `key`. `presigned_put` is the rule for an
        untrusted writer of an arbitrary file, where the measurement is what bounds it; this is for
        a writer that already controls the content and whose length is unknown at mint time (the
        preview renderer, which cannot report its output's size until it has produced it). The key
        is still fixed here, so the holder can write only this one key until the URL expires."""
        client = await self._client()
        return await client.generate_presigned_url(
            "put_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=ttl_seconds
        )

    async def presigned_get(self, key: str, ttl_seconds: int, *, attachment: bool = False) -> str:
        """A temporary GET for one object, optionally served as an uncached download."""
        client = await self._client()
        params = {"Bucket": self.bucket, "Key": key}
        if attachment:
            params.update(ResponseContentDisposition="attachment", ResponseCacheControl="no-store")
        return await client.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=ttl_seconds
        )

    async def put_host(self) -> str:
        """The host a presigned URL resolves to — what the egress proxy admits so the sandbox can
        reach it. Read off the client that does the signing rather than recomposed from config, so
        the rule and the URL cannot disagree about region or addressing: virtual-hosted addressing
        (AWS) puts the bucket in front of the endpoint host, path addressing (an S3-compatible
        endpoint) does not. The bare hostname, never `host:port` — the proxy admits by the hostname
        its CONNECT parser extracts, and a port-carrying rule would match nothing."""
        client = await self._client()
        hostname = urlsplit(client.meta.endpoint_url).hostname
        if hostname is None:
            raise RuntimeError(f"the S3 client's endpoint has no hostname: {self.bucket}")
        return hostname if self.endpoint_url is not None else f"{self.bucket}.{hostname}"

    async def list(self, prefix: str) -> tuple[BlobEntry, ...]:
        if not prefix:
            raise ValueError("blob list requires a key prefix")
        entries: list[BlobEntry] = []
        client = await self._client()
        paginator = client.get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents", ()):
                entries.append(
                    BlobEntry(
                        key=item["Key"],
                        size_bytes=item["Size"],
                        modified_at=item["LastModified"].astimezone(UTC),
                    )
                )
            if len(entries) >= BLOB_LIST_MAX_KEYS:
                break
        return tuple(entries[:BLOB_LIST_MAX_KEYS])

    async def close(self) -> None:
        """Close and discard the client owned by the running event loop."""
        loop = asyncio.get_running_loop()
        client = self._clients.pop(loop, None)
        self._client_locks.pop(loop, None)
        if client is not None:
            await client.close()

    async def _client(self) -> AioBaseClient:
        """The store's one client per event loop. Its signing config is pinned rather than left to
        botocore's defaults: unpinned, `generate_presigned_url` emits SigV2 for a bucket in a
        legacy-global region and resolves a virtual host that differs from the client's own
        endpoint, so a presigned artifact PUT would be both unsignable by the deploy's temporary
        credentials and unreachable through the exact host the proxy admits."""
        loop = asyncio.get_running_loop()
        client = self._clients.get(loop)
        if client is not None:
            return client
        lock = self._client_locks.setdefault(loop, asyncio.Lock())
        async with lock:
            client = self._clients.get(loop)
            if client is not None:
                return client
            client = (
                await get_session()
                .create_client(
                    "s3",
                    endpoint_url=self.endpoint_url,
                    region_name=self.region,
                    config=S3_PATH_CONFIG if self.endpoint_url is not None else S3_VIRTUAL_CONFIG,
                )
                .__aenter__()
            )
            self._clients[loop] = client
            return client


@dataclass(frozen=True)
class WorkspaceBlobStore:
    """Blob storage under the bound workspace's prefix. Keys are workspace-relative; the ambient
    `ws(...)` scope supplies the `workspaces/<id>/` prefix at call time — the workspace is never an
    argument, so a key cannot land under another workspace and an unscoped call raises
    `WorkspaceUnbound` rather than reading or writing across one. A key never names the prefix —
    the scope supplies it — so one that does is refused."""

    backend: FilesystemBlobStore | S3BlobStore

    async def put(self, key: str, data: bytes) -> None:
        await self.backend.put(self._full(key), data)

    async def get(self, key: str) -> bytes:
        return await self.backend.get(self._full(key))

    async def exists(self, key: str) -> bool:
        return await self.backend.exists(self._full(key))

    async def delete(self, key: str) -> None:
        await self.backend.delete(self._full(key))

    def get_stream(self, key: str) -> AsyncIterator[bytes]:
        """The key resolves against the bound workspace here, at the call — not lazily at the
        first chunk — so a stream opened inside the scope (a download response body) keeps working
        after the block exits."""
        return self.backend.get_stream(self._full(key))

    async def put_stream(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        part_size_bytes: int = S3_MULTIPART_PART_BYTES,
        tags: dict[str, str] | None = None,
    ) -> None:
        await self.backend.put_stream(
            self._full(key), chunks, part_size_bytes=part_size_bytes, tags=tags
        )

    async def list(self, prefix: str) -> tuple[BlobEntry, ...]:
        if not prefix:
            raise ValueError("blob list requires a key prefix")
        root = self._full("")
        entries = await self.backend.list(root + prefix)
        return tuple(replace(entry, key=entry.key.removeprefix(root)) for entry in entries)

    async def presigned_put(
        self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int
    ) -> str:
        """The backend's presigned PUT for the full workspace key — S3 only, and the caller's
        backend match already established that."""
        match self.backend:
            case S3BlobStore() as s3:
                return await s3.presigned_put(
                    self._full(key), size_bytes, checksum_sha256, ttl_seconds
                )
            case _:
                raise TypeError("presigned_put requires the s3 backend")

    async def presigned_put_unmeasured(self, key: str, ttl_seconds: int) -> str:
        """The backend's unmeasured presigned PUT for the full workspace key — S3 only."""
        match self.backend:
            case S3BlobStore() as s3:
                return await s3.presigned_put_unmeasured(self._full(key), ttl_seconds)
            case _:
                raise TypeError("presigned_put_unmeasured requires the s3 backend")

    async def presigned_get(self, key: str, ttl_seconds: int) -> str:
        """The backend's presigned GET for the full workspace key — S3 only, and the caller's
        backend match already established that."""
        match self.backend:
            case S3BlobStore() as s3:
                return await s3.presigned_get(self._full(key), ttl_seconds)
            case _:
                raise TypeError("presigned_get requires the s3 backend")

    async def download_url(self, key: str, ttl_seconds: int) -> str | None:
        """Return a direct S3 download link, or None for local filesystem storage."""
        match self.backend:
            case S3BlobStore() as s3:
                return await s3.presigned_get(self._full(key), ttl_seconds, attachment=True)
            case FilesystemBlobStore():
                return None

    def _full(self, key: str) -> str:
        if key.startswith(WORKSPACE_KEY_PREFIX):
            raise ValueError(f"key is already workspace-prefixed: {key!r}")
        return f"{WORKSPACE_KEY_PREFIX}{ws_current().workspace_id}/{key}"


@dataclass(frozen=True)
class FleetBlobStore:
    """The one unprefixed handle, for data owned by the deploy rather than any workspace: portal
    static assets and terminal payload spill. Its namespace is closed — a key outside
    `FLEET_KEY_PREFIXES` is refused, so workspace data cannot flow around the workspace store."""

    backend: FilesystemBlobStore | S3BlobStore

    async def put(self, key: str, data: bytes) -> None:
        await self.backend.put(self._checked(key), data)

    async def get(self, key: str) -> bytes:
        return await self.backend.get(self._checked(key))

    async def exists(self, key: str) -> bool:
        return await self.backend.exists(self._checked(key))

    async def delete(self, key: str) -> None:
        await self.backend.delete(self._checked(key))

    def get_stream(self, key: str) -> AsyncIterator[bytes]:
        return self.backend.get_stream(self._checked(key))

    async def put_stream(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        part_size_bytes: int = S3_MULTIPART_PART_BYTES,
        tags: dict[str, str] | None = None,
    ) -> None:
        await self.backend.put_stream(
            self._checked(key), chunks, part_size_bytes=part_size_bytes, tags=tags
        )

    async def list(self, prefix: str) -> tuple[BlobEntry, ...]:
        return await self.backend.list(self._checked(prefix))

    def _checked(self, key: str) -> str:
        if not key.startswith(FLEET_KEY_PREFIXES):
            raise ValueError(f"key is outside the fleet namespaces {FLEET_KEY_PREFIXES}: {key!r}")
        return key


def blob_store_for(config: BlobConfig) -> FilesystemBlobStore | S3BlobStore:
    """Build the configured backend; BlobConfig validation guarantees field completeness."""
    match config.backend:
        case "filesystem":
            if config.root is None:
                raise ValueError("blob.root is required for the filesystem backend")
            return FilesystemBlobStore(root=config.root)
        case "s3":
            if config.bucket is None:
                raise ValueError("blob.bucket is required for the s3 backend")
            return S3BlobStore(
                bucket=config.bucket, endpoint_url=config.endpoint_url, region=config.region
            )
