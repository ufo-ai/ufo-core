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

from ufo.blob import WorkspaceBlobStore
from ufo.db import owner_tx, workspace_tx
from ufo.harness.o11y import log
from ufo.runtime.media.artifact_url import ARTIFACT_KEY_PREFIX
from ufo.schema import tables

ARTIFACT_PREVIEW_SUFFIXES = frozenset((".csv", ".docx", ".md", ".pdf", ".pptx", ".svg", ".xlsx"))
RASTER_PREVIEW_SUFFIXES = {
    ".jpeg": "jpeg",
    ".jpg": "jpeg",
    ".png": "png",
    ".webp": "webp",
}
PREVIEW_KINDS = {
    ".csv": "csv",
    ".docx": "docx",
    ".md": "md",
    ".mkv": "mkv",
    ".mov": "mov",
    ".mp4": "mp4",
    ".pdf": "pdf",
    ".pptx": "pptx",
    ".svg": "svg",
    ".webm": "webm",
    ".xlsx": "xlsx",
}
"""What the preview service draws a cover of, by extension — the document types plus the video
formats it takes a first frame from. One list answers every caller: the composer rendering a file in
hand, and the cover drawn for a file a member attached."""
ARTIFACT_PREVIEW_MEDIA_TYPE = "image/png"
ARTIFACT_PREVIEW_MAX_WIDTH = 800
ARTIFACT_PREVIEW_MAX_HEIGHT = 1000
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
class DrawnCover:
    """The picture one render produced: the key it was PUT under and the size the service reported.
    The caller writes the row, so the same render serves a share, the backstop, and a member's own
    attachment while each owns which row it fills."""

    preview_key: str
    size_bytes: int


async def render_document_cover(
    blob: WorkspaceBlobStore,
    service_url: str,
    blob_key: str,
    filename: str,
    client: httpx.AsyncClient,
) -> DrawnCover | None:
    """Draw one stored document's cover through the preview service, or None when the store cannot
    presign or the service does not render.

    The service does the reading and the writing: a presigned GET of the source and a plain
    presigned PUT of the preview key are handed over in one request, and the service fetches,
    renders, and PUTs the PNG itself. No document's bytes enter this process — the same shape
    `share_file` and the `render_previews` backstop already render under, so a member's own
    attachment draws the picture the same way an agent's share does. `client` is the caller's, so a
    batch keeps one and a single draw holds it to its own budget."""
    kind = PREVIEW_KINDS.get(PurePosixPath(filename).suffix.lower())
    if kind is None:
        return None
    preview_key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/{PurePosixPath(filename).stem}.png"
    try:
        source_url = await blob.presigned_get(blob_key, PREVIEW_SOURCE_TTL_SECONDS)
        put_url = await blob.presigned_put_unmeasured(preview_key, PREVIEW_PUT_TTL_SECONDS)
    except TypeError:
        return None
    request = {
        "kind": kind,
        "max_width": ARTIFACT_PREVIEW_MAX_WIDTH,
        "max_height": ARTIFACT_PREVIEW_MAX_HEIGHT,
        "pages": 1,
        "source_url": source_url,
        "sink": {"put_url": put_url},
    }
    try:
        response = await client.post(
            f"{service_url}/render", files={"request": (None, json.dumps(request))}
        )
    except httpx.HTTPError as error:
        log("document_cover.unreachable", filename=filename, error_class=type(error).__name__)
        return None
    if response.status_code != 200:
        log("document_cover.refused", filename=filename, http_status=response.status_code)
        return None
    return DrawnCover(preview_key=preview_key, size_bytes=int(response.json()["size_bytes"]))


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
        drawn = await render_document_cover(self.blob, self.service_url, blob_key, filename, client)
        if drawn is None:
            return
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.shared_artifact)
                .where(
                    tables.shared_artifact.c.blob_key == blob_key,
                    tables.shared_artifact.c.preview_blob_key.is_(None),
                )
                .values(
                    preview_blob_key=drawn.preview_key,
                    preview_media_type=ARTIFACT_PREVIEW_MEDIA_TYPE,
                    preview_size_bytes=drawn.size_bytes,
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
