"""Blob storage behind one async protocol: filesystem for dev, S3 for deploys."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from aiobotocore.session import ClientCreatorContext, get_session
from botocore.exceptions import ClientError

from selfhost.config import BlobConfig

MISSING_KEY_CODES = ("404", "NoSuchKey", "NotFound")


class BlobNotFound(KeyError):
    """Raised by get for a key that does not exist."""


class BlobStore(Protocol):
    """Async byte storage keyed by slash-separated string keys."""

    async def put(self, key: str, data: bytes) -> None: ...

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


@dataclass(frozen=True)
class S3BlobStore:
    """Single-shot object storage over aiobotocore; a fresh client per call."""

    bucket: str
    endpoint_url: str | None = None
    region: str | None = None

    async def put(self, key: str, data: bytes) -> None:
        async with self._client() as client:
            await client.put_object(Bucket=self.bucket, Key=key, Body=data)

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
