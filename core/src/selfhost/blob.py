"""Blob storage behind one async protocol: filesystem for dev, S3 for deploys."""

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from aiobotocore.session import ClientCreatorContext, get_session
from botocore.exceptions import ClientError

from selfhost.config import BlobConfig

MISSING_KEY_CODES = ("404", "NoSuchKey", "NotFound")
S3_MULTIPART_PART_BYTES = 16 * 1024 * 1024


class BlobNotFound(KeyError):
    """Raised by get for a key that does not exist."""


class BlobStore(Protocol):
    """Async byte storage keyed by slash-separated string keys."""

    async def put(self, key: str, data: bytes) -> None: ...

    async def put_file(self, key: str, source: Path) -> None:
        """Store a local file's bytes under `key`, streaming from disk so the whole file never sits
        in memory — the large-attachment write, distinct from the in-memory `put`."""
        ...

    async def get(self, key: str) -> bytes: ...

    async def exists(self, key: str) -> bool: ...


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

    def _resolve(self, key: str) -> Path:
        root = self.root.resolve()
        path = (root / key).resolve()
        if path == root or not path.is_relative_to(root):
            raise ValueError(f"blob key escapes store root: {key!r}")
        return path


def _is_missing_key(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") in MISSING_KEY_CODES


def _read_range(source: Path, offset: int, size: int) -> bytes:
    with source.open("rb") as handle:
        handle.seek(offset)
        return handle.read(size)


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
        size = await asyncio.to_thread(lambda: source.stat().st_size)
        async with self._client() as client:
            if size == 0:
                await client.put_object(Bucket=self.bucket, Key=key, Body=b"")
                return
            created = await client.create_multipart_upload(Bucket=self.bucket, Key=key)
            upload_id = created["UploadId"]
            try:
                parts: list[dict[str, object]] = []
                for number, offset in enumerate(range(0, size, S3_MULTIPART_PART_BYTES), start=1):
                    chunk = await asyncio.to_thread(
                        _read_range, source, offset, S3_MULTIPART_PART_BYTES
                    )
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
                await client.abort_multipart_upload(
                    Bucket=self.bucket, Key=key, UploadId=upload_id
                )
                raise

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

    def _client(self) -> ClientCreatorContext:
        return get_session().create_client(
            "s3", endpoint_url=self.endpoint_url, region_name=self.region
        )


def blob_store_for(config: BlobConfig) -> FilesystemBlobStore | S3BlobStore:
    """Build the configured backend; BlobConfig validation guarantees field completeness."""
    match config.backend:
        case "filesystem":
            return FilesystemBlobStore(root=config.root)
        case "s3":
            return S3BlobStore(
                bucket=config.bucket, endpoint_url=config.endpoint_url, region=config.region
            )
