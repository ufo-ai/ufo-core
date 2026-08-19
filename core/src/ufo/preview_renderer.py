"""The backstop that renders a shared document's preview whose share-time render never landed.

`share_file` renders a preview inline as the file is shared, best-effort: a transient
preview-service outage leaves the `shared_artifact` row with its three preview columns null and the
file already stored. This job finds those rows and renders them the same way the share path does — a
presigned GET of the source and a plain presigned PUT of the preview key handed to the service,
which fetches, renders, PUTs the PNG, and answers only the size to record. Bytes never enter core.

Only rows shared within `PREVIEW_RETRY_WINDOW` are candidates: a document the service can never
render (a corrupt file) fails every attempt, so an unbounded window would retry it forever; the
window lets a permanent failure age out while a transient one is caught. Batch-at-interval is the
whole retry — a row that fails this tick is left for the next, never looped on here."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from uuid import UUID, uuid4

import httpx
import sqlalchemy as sa

from ufo.artifact_url import ARTIFACT_KEY_PREFIX
from ufo.blob import WorkspaceBlobStore
from ufo.db import owner_tx, workspace_tx
from ufo.o11y import log
from ufo.schema import tables
from ufo.tools.builtins import (
    ARTIFACT_PREVIEW_MAX_HEIGHT,
    ARTIFACT_PREVIEW_MAX_WIDTH,
    ARTIFACT_PREVIEW_MEDIA_TYPE,
    ARTIFACT_PREVIEW_SUFFIXES,
)

PREVIEW_SERVICE_URL_ENV = "UFO_PREVIEW_URL"
PREVIEW_TOKEN_ENV = "UFO_PREVIEW_TOKEN"
PREVIEW_RETRY_WINDOW = timedelta(hours=1)
PREVIEW_RENDER_BATCH = 8
PREVIEW_SOURCE_TTL_SECONDS = 900
PREVIEW_PUT_TTL_SECONDS = 900
PREVIEW_RENDER_TIMEOUT_SECONDS = 330.0


def _eligible(filename_column: sa.Column) -> sa.ColumnElement[bool]:
    return sa.or_(*(filename_column.ilike(f"%{suffix}") for suffix in ARTIFACT_PREVIEW_SUFFIXES))


@dataclass(frozen=True)
class PreviewRenderer:
    """The `render_previews` job's body: render the previews `share_file` could not, one workspace
    at a time. The jobs role constructs it with the deploy's blob store and the preview service's
    in-cluster URL and schedules it; nothing here reaches another workspace's rows — the read and
    the write run under the bound workspace's RLS."""

    blob: WorkspaceBlobStore
    service_url: str
    render_timeout: float = PREVIEW_RENDER_TIMEOUT_SECONDS

    async def run(self) -> None:
        cutoff = datetime.now(UTC) - PREVIEW_RETRY_WINDOW
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.shared_artifact.c.blob_key,
                        tables.shared_artifact.c.filename,
                    )
                    .where(
                        tables.shared_artifact.c.preview_blob_key.is_(None),
                        tables.shared_artifact.c.created_at > cutoff,
                        _eligible(tables.shared_artifact.c.filename),
                    )
                    .order_by(tables.shared_artifact.c.created_at.desc())
                    .limit(PREVIEW_RENDER_BATCH)
                )
            ).all()
        if not rows:
            return
        async with httpx.AsyncClient(timeout=self.render_timeout) as client:
            for row in rows:
                await self._render_one(client, row.blob_key, row.filename)

    async def _render_one(self, client: httpx.AsyncClient, blob_key: str, filename: str) -> None:
        suffix = PurePosixPath(filename).suffix.lower()
        preview_key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/{PurePosixPath(filename).stem}.png"
        source_url = await self.blob.presigned_get(blob_key, PREVIEW_SOURCE_TTL_SECONDS)
        put_url = await self.blob.presigned_put_unmeasured(preview_key, PREVIEW_PUT_TTL_SECONDS)
        request = {
            "kind": suffix[1:],
            "max_width": ARTIFACT_PREVIEW_MAX_WIDTH,
            "max_height": ARTIFACT_PREVIEW_MAX_HEIGHT,
            "pages": 1,
            "source_url": source_url,
            "sink": {"put_url": put_url},
        }
        try:
            response = await client.post(
                f"{self.service_url}/render", files={"request": (None, json.dumps(request))}
            )
        except httpx.HTTPError as error:
            log("render_previews.unreachable", filename=filename, error_class=type(error).__name__)
            return
        if response.status_code != 200:
            log("render_previews.refused", filename=filename, status=response.status_code)
            return
        size_bytes = int(response.json()["size_bytes"])
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.shared_artifact)
                .where(
                    tables.shared_artifact.c.blob_key == blob_key,
                    tables.shared_artifact.c.preview_blob_key.is_(None),
                )
                .values(
                    preview_blob_key=preview_key,
                    preview_media_type=ARTIFACT_PREVIEW_MEDIA_TYPE,
                    preview_size_bytes=size_bytes,
                )
            )

    async def candidate_workspaces(self) -> tuple[UUID, ...]:
        cutoff = datetime.now(UTC) - PREVIEW_RETRY_WINDOW
        async with owner_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.shared_artifact.c.workspace_id)
                    .where(
                        tables.shared_artifact.c.preview_blob_key.is_(None),
                        tables.shared_artifact.c.created_at > cutoff,
                        _eligible(tables.shared_artifact.c.filename),
                    )
                    .distinct()
                )
            ).all()
        return tuple(row.workspace_id for row in rows)
