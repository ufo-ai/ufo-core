"""Blob storage behind one async protocol: filesystem for dev, S3 for deploys."""

import asyncio
import os
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from aiobotocore.session import ClientCreatorContext, get_session
from botocore.exceptions import ClientError

from selfhost.config import BlobConfig

MISSING_KEY_CODES = ("404", "NoSuchKey", "NotFound")
BLOB_STREAM_CHUNK_BYTES = 1024 * 1024
S3_MULTIPART_PART_BYTES = 8 * 1024 * 1024


class BlobNotFound(KeyError):
    """Raised by get for a key that does not exist."""


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

    def get_stream(self, key: str) -> AsyncIterator[bytes]: ...

    async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None: ...


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
            async with response["Body"] as body:
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

    async def get_stream(self, key: str) -> AsyncIterator[bytes]:
        async with self._client() as client:
            try:
                response = await client.get_object(Bucket=self.bucket, Key=key)
            except ClientError as error:
                if _is_missing_key(error):
                    raise BlobNotFound(key) from error
                raise
            async with response["Body"] as body:
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
