"""Copy workspace-owned blobs under `workspaces/<id>/` (RFC 0032), driven by the database.

Operator-run, per environment, with the owner DSN (an RLS-scoped role reads zero rows and is
refused):

    uv run python infra/blob_relayout.py --database-url $OWNER_DSN --bucket <blob-bucket> copy
    uv run python infra/blob_relayout.py ... verify
    uv run python infra/blob_relayout.py ... delete   # after soak; removes the old prefixes

`copy` is idempotent and never deletes: it server-side-copies every mapped object whose
destination is absent or older, and prints the unmapped keys (rows deleted since the object
landed). `verify` proves every mapped and row-referenced key exists at its workspace address.
`delete` re-verifies, then removes everything under the old toplevel prefixes, orphans included.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from aiobotocore.client import AioBaseClient
from aiobotocore.session import get_session
from botocore.exceptions import ClientError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ufo.blob import MISSING_KEY_CODES

CONCURRENCY = 32
DELETE_BATCH = 1000
OWNER_TABLES = {"conversations": "conversation", "tool-images": "turn"}
OLD_PREFIXES = ("conversations/", "tool-images/", "artifacts/", "sources/")
REPORT_CAP = 50


@dataclass(frozen=True)
class Relayout:
    client: AioBaseClient
    bucket: str
    database_url: str

    async def copy(self) -> int:
        mapping, unmapped = await self._mapping()
        print(f"{len(mapping)} objects mapped, {len(unmapped)} unmapped (left in place)")
        for key in sorted(unmapped)[:REPORT_CAP]:
            print(f"  unmapped: {key}")
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def _one(old: str, new: str, modified: datetime) -> bool:
            async with semaphore:
                return await self._copy_if_newer(old, new, modified)

        results = await asyncio.gather(
            *(_one(old, new, modified) for old, (new, modified) in mapping.items())
        )
        print(f"copied {sum(results)}, already current {len(results) - sum(results)}")
        return 0

    async def verify(self) -> int:
        """Every key a row references, and every listed old object the mapping claims, must exist
        at its workspace address — the whole precondition `delete` destroys evidence for."""
        mapping, _unmapped = await self._mapping()
        expected: dict[str, str] = {new: old for old, (new, _modified) in mapping.items()}
        engine = create_async_engine(self.database_url)
        try:
            for family, keys in (await self._row_keys(engine)).items():
                expected |= {key: family for key in keys}
        finally:
            await engine.dispose()
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def _absent(key: str) -> bool:
            async with semaphore:
                return (await self._head(key)) is None

        gone = await asyncio.gather(*(_absent(key) for key in expected))
        absent = [key for key, is_gone in zip(expected, gone, strict=True) if is_gone]
        print(f"{len(expected)} expected workspace keys, {len(absent)} missing")
        for key in sorted(absent)[:REPORT_CAP]:
            print(f"  missing: {key} (from {expected[key]})")
        return 1 if absent else 0

    async def delete(self) -> int:
        if await self.verify() != 0:
            print("verify failed — nothing deleted")
            return 1
        deleted = 0
        for prefix in OLD_PREFIXES:
            batch: list[str] = []
            async for key, _modified in self._listed(prefix):
                batch.append(key)
                if len(batch) == DELETE_BATCH:
                    deleted += await self._delete_batch(batch)
                    batch = []
            deleted += await self._delete_batch(batch)
        print(f"deleted {deleted} objects under {OLD_PREFIXES}")
        return 0

    async def _mapping(self) -> tuple[dict[str, tuple[str, datetime]], list[str]]:
        """old key -> (new key, last modified) for every object under the old prefixes, and the
        listed keys no row names — orphans that only `delete` touches."""
        engine = create_async_engine(self.database_url)
        try:
            owners = await self._owners(engine)
            row_keys = await self._row_keys(engine)
        finally:
            await engine.dispose()
        if not any(owners.values()) and not any(row_keys.values()):
            raise RuntimeError(
                "the database names no rows to map — an RLS-scoped role reads nothing; "
                "pass the owner DSN"
            )
        by_old = {new.split("/", 2)[2]: new for keys in row_keys.values() for new in keys}
        mapping: dict[str, tuple[str, datetime]] = {}
        unmapped: list[str] = []
        for prefix in OLD_PREFIXES:
            family = prefix.rstrip("/")
            async for key, modified in self._listed(prefix):
                if family in OWNER_TABLES:
                    owner = owners[family].get(key.split("/")[1])
                    new = None if owner is None else f"workspaces/{owner}/{key}"
                else:
                    new = by_old.get(key)
                if new is None:
                    unmapped.append(key)
                else:
                    mapping[key] = (new, modified)
        return mapping, unmapped

    async def _owners(self, engine: AsyncEngine) -> dict[str, dict[str, UUID]]:
        async with engine.connect() as connection:
            return {
                family: {
                    str(row.id): row.workspace_id
                    for row in await connection.execute(
                        sa.text(f"select id, workspace_id from {table}")
                    )
                }
                for family, table in OWNER_TABLES.items()
            }

    async def _row_keys(self, engine: AsyncEngine) -> dict[str, list[str]]:
        """Every row-referenced blob key, as its full workspace address."""
        async with engine.connect() as connection:
            artifacts = [
                f"workspaces/{row.workspace_id}/{key}"
                for row in await connection.execute(
                    sa.text("select workspace_id, blob_key, preview_blob_key from shared_artifact")
                )
                for key in (row.blob_key, row.preview_blob_key)
                if key is not None
            ]
            sources = [
                f"workspaces/{row.workspace_id}/{row.body_ref}"
                for row in await connection.execute(
                    sa.text("select workspace_id, body_ref from page")
                )
            ]
        return {"artifacts": artifacts, "sources": sources}

    async def _listed(self, prefix: str):
        paginator = self.client.get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents", ()):
                yield item["Key"], item["LastModified"]

    async def _copy_if_newer(self, old: str, new: str, source_modified: datetime) -> bool:
        """Copy unless the destination is strictly newer: S3 timestamps are second-granular, so an
        equal second cannot prove the destination holds the source's latest bytes — re-copying
        identical bytes is harmless where a skipped rewrite is silent loss."""
        destination_modified = await self._head(new)
        if destination_modified is not None and destination_modified > source_modified:
            return False
        await self.client.copy_object(
            Bucket=self.bucket, Key=new, CopySource={"Bucket": self.bucket, "Key": old}
        )
        return True

    async def _head(self, key: str) -> datetime | None:
        try:
            response = await self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in MISSING_KEY_CODES:
                return None
            raise
        return response["LastModified"]

    async def _delete_batch(self, keys: list[str]) -> int:
        if not keys:
            return 0
        response = await self.client.delete_objects(
            Bucket=self.bucket, Delete={"Objects": [{"Key": key} for key in keys]}
        )
        errors = response.get("Errors", ())
        if errors:
            raise RuntimeError(f"delete_objects refused {len(errors)} keys: {errors[:5]}")
        return len(keys)


async def _run(arguments: argparse.Namespace) -> int:
    async with get_session().create_client("s3") as client:
        relayout = Relayout(
            client=client, bucket=arguments.bucket, database_url=arguments.database_url
        )
        match arguments.mode:
            case "copy":
                return await relayout.copy()
            case "verify":
                return await relayout.verify()
            case _:
                return await relayout.delete()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("mode", choices=("copy", "verify", "delete"))
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
