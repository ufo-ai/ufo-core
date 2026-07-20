"""Blob storage behind one async protocol: filesystem for dev, S3 for deploys."""

import asyncio
import os
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from aiobotocore.session import ClientCreatorContext, get_session
from botocore.exceptions import ClientError

from ufo.config import BlobConfig

MISSING_KEY_CODES = ("404", "NoSuchKey", "NotFound")
BLOB_STREAM_CHUNK_BYTES = 1024 * 1024
S3_MULTIPART_PART_BYTES = 8 * 1024 * 1024
S3_SINGLE_COPY_MAX_BYTES = 5 * 1024 * 1024 * 1024
S3_COPY_PART_BYTES = 1024 * 1024 * 1024
BLOB_LIST_MAX_KEYS = 10_000


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
    crosses without a whole-file buffer; `get`/`put` are the whole-bytes shorthands."""

    async def put(self, key: str, data: bytes) -> None: ...

    async def put_file(self, key: str, source: Path) -> None:
        """Store a local file's bytes under `key`, streaming from disk so the whole file never sits
        in memory — the large-attachment write, distinct from the in-memory `put`."""
        ...

    async def get(self, key: str) -> bytes: ...

    async def exists(self, key: str) -> bool: ...

    async def delete(self, key: str) -> None:
        """Remove a stored object; deleting an absent key is a no-op, so a retried delete holds."""
        ...

    def get_stream(self, key: str) -> AsyncIterator[bytes]: ...

    async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None: ...

    async def copy(self, src_key: str, dst_key: str) -> None:
        """Duplicate a stored object to another key within the same store — the bytes never cross
        the calling process. The workspace-file share path: a produced file already in the
        conversation's workspace prefix is promoted into the artifact prefix of the same store,
        server-side on S3 (`s3:CopyObject`) with no read-through-the-pod."""
        ...

    async def list(self, prefix: str) -> tuple[BlobEntry, ...]:
        """Every stored object under a key prefix, sorted by key, capped at `BLOB_LIST_MAX_KEYS`
        entries — the bounded enumeration a read view (a conversation's workspace files, its
        compaction records) walks; never a whole-store scan, so the prefix is required."""
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

    async def put_file(self, key: str, source: Path) -> None:
        path = self._resolve(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        temp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        await asyncio.to_thread(shutil.copyfile, source, temp)
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

    async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None:
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

    async def copy(self, src_key: str, dst_key: str) -> None:
        source = self._resolve(src_key)
        if not await asyncio.to_thread(source.is_file):
            raise BlobNotFound(src_key)
        path = self._resolve(dst_key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        temp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        await asyncio.to_thread(shutil.copyfile, source, temp)
        await asyncio.to_thread(temp.replace, path)

    async def list(self, prefix: str) -> tuple[BlobEntry, ...]:
        if not prefix:
            raise ValueError("blob list requires a key prefix")
        return await asyncio.to_thread(self._walk, prefix)

    def _walk(self, prefix: str) -> tuple[BlobEntry, ...]:
        root = self.root.resolve()
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
        root = self.root.resolve()
        path = (root / key).resolve()
        if path == root or not path.is_relative_to(root):
            raise ValueError(f"blob key escapes store root: {key!r}")
        return path


def _is_missing_key(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") in MISSING_KEY_CODES


@dataclass(frozen=True)
class S3BlobStore:
    """Single-shot object storage over aiobotocore; a fresh client per call."""

    bucket: str
    endpoint_url: str | None = None
    region: str | None = None

    async def put(self, key: str, data: bytes) -> None:
        async with self._client() as client:
            await client.put_object(Bucket=self.bucket, Key=key, Body=data)

    async def put_file(self, key: str, source: Path) -> None:
        stat = await asyncio.to_thread(source.stat)
        size = stat.st_size
        async with self._client() as client:
            if size == 0:
                await client.put_object(Bucket=self.bucket, Key=key, Body=b"")
                return
            created = await client.create_multipart_upload(Bucket=self.bucket, Key=key)
            upload_id = created["UploadId"]
            fd = await asyncio.to_thread(os.open, source, os.O_RDONLY)
            try:
                parts: list[dict[str, object]] = []
                for number, offset in enumerate(range(0, size, S3_MULTIPART_PART_BYTES), start=1):
                    chunk = await asyncio.to_thread(os.pread, fd, S3_MULTIPART_PART_BYTES, offset)
                    part = await client.upload_part(
                        Bucket=self.bucket,
                        Key=key,
                        PartNumber=number,
                        UploadId=upload_id,
                        Body=chunk,
                    )
                    parts.append({"ETag": part["ETag"], "PartNumber": number})
                await client.complete_multipart_upload(
                    Bucket=self.bucket,
                    Key=key,
                    UploadId=upload_id,
                    MultipartUpload={"Parts": parts},
                )
            except Exception:
                await client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
                raise
            finally:
                await asyncio.to_thread(os.close, fd)

    async def get(self, key: str) -> bytes:
        async with self._client() as client:
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
        async with self._client() as client:
            try:
                await client.head_object(Bucket=self.bucket, Key=key)
            except ClientError as error:
                if _is_missing_key(error):
                    return False
                raise
            return True

    async def delete(self, key: str) -> None:
        async with self._client() as client:
            await client.delete_object(Bucket=self.bucket, Key=key)

    async def get_stream(self, key: str) -> AsyncIterator[bytes]:
        async with self._client() as client:
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

    async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None:
        async with self._client() as client:
            buffer = bytearray()
            upload_id: str | None = None
            parts: list[dict[str, object]] = []
            part_number = 1
            try:
                async for chunk in chunks:
                    buffer += chunk
                    if len(buffer) < S3_MULTIPART_PART_BYTES:
                        continue
                    if upload_id is None:
                        started = await client.create_multipart_upload(Bucket=self.bucket, Key=key)
                        upload_id = started["UploadId"]
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
                    await client.put_object(Bucket=self.bucket, Key=key, Body=bytes(buffer))
                    return
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
                    await client.abort_multipart_upload(
                        Bucket=self.bucket, Key=key, UploadId=upload_id
                    )
                raise

    async def copy(self, src_key: str, dst_key: str) -> None:
        source = {"Bucket": self.bucket, "Key": src_key}
        async with self._client() as client:
            try:
                head = await client.head_object(Bucket=self.bucket, Key=src_key)
            except ClientError as error:
                if _is_missing_key(error):
                    raise BlobNotFound(src_key) from error
                raise
            size = head["ContentLength"]
            if size <= S3_SINGLE_COPY_MAX_BYTES:
                await client.copy_object(CopySource=source, Bucket=self.bucket, Key=dst_key)
                return
            created = await client.create_multipart_upload(Bucket=self.bucket, Key=dst_key)
            upload_id = created["UploadId"]
            try:
                parts: list[dict[str, object]] = []
                for number, offset in enumerate(range(0, size, S3_COPY_PART_BYTES), start=1):
                    last = min(offset + S3_COPY_PART_BYTES, size) - 1
                    copied = await client.upload_part_copy(
                        Bucket=self.bucket,
                        Key=dst_key,
                        PartNumber=number,
                        UploadId=upload_id,
                        CopySource=source,
                        CopySourceRange=f"bytes={offset}-{last}",
                    )
                    parts.append({"ETag": copied["CopyPartResult"]["ETag"], "PartNumber": number})
                await client.complete_multipart_upload(
                    Bucket=self.bucket,
                    Key=dst_key,
                    UploadId=upload_id,
                    MultipartUpload={"Parts": parts},
                )
            except BaseException:
                await client.abort_multipart_upload(
                    Bucket=self.bucket, Key=dst_key, UploadId=upload_id
                )
                raise

    async def list(self, prefix: str) -> tuple[BlobEntry, ...]:
        if not prefix:
            raise ValueError("blob list requires a key prefix")
        entries: list[BlobEntry] = []
        async with self._client() as client:
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

    def _client(self) -> ClientCreatorContext:
        return get_session().create_client(
            "s3", endpoint_url=self.endpoint_url, region_name=self.region
        )


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
