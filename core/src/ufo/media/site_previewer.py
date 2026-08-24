import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ufo.blob import S3BlobStore, WorkspaceBlobStore
from ufo.media.artifact_url import ARTIFACT_KEY_PREFIX
from ufo.media.previews import StoredPreview
from ufo.o11y import log
from ufo.sandbox.ingress_url import mint_ingress_view_url
from ufo.workspace import ws_current

SITE_PREVIEW_KIND = "site"
SITE_PREVIEW_MEDIA_TYPE = "image/png"
SITE_PREVIEW_PUT_TTL_SECONDS = 900
SITE_PREVIEW_TIMEOUT_SECONDS = 90.0
SITE_PREVIEW_MAX_BYTES = 20 * 1024 * 1024
SITE_PREVIEW_RESPONSE_MAX_BYTES = 1024 * 1024
SITE_PREVIEW_DETAIL_BYTES = 500
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class PreviewMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    width: int = Field(gt=0)
    height: int = Field(gt=0)
    page_count: Literal[1]
    size_bytes: int = Field(gt=0, le=SITE_PREVIEW_MAX_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class SitePreviewer:
    """Capture one conversation's hosted sandbox port through ufo-preview."""

    blob: WorkspaceBlobStore
    service_url: str
    token: str
    ingress_public_url: str
    transport: httpx.AsyncBaseTransport | None = None

    async def render(
        self, conversation_id: UUID, port: int, name: str, width: int, height: int
    ) -> StoredPreview | None:
        if PurePosixPath(name).name != name or not name:
            raise ValueError(f"preview name is not a filename: {name!r}")
        if not 16 <= width <= 4096 or not 16 <= height <= 4096:
            raise ValueError("site preview dimensions must be from 16 through 4096")
        source_url = mint_ingress_view_url(
            self.ingress_public_url,
            ws_current().workspace_id,
            conversation_id,
            port,
            "/",
        )
        if source_url is None:
            return None
        key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/{name}.png"
        direct = isinstance(self.blob.backend, S3BlobStore)
        sink: dict[str, str | bool]
        if direct:
            sink = {
                "put_url": await self.blob.presigned_put_unmeasured(
                    key, SITE_PREVIEW_PUT_TTL_SECONDS
                )
            }
        else:
            sink = {"inline": True}
        request = {
            "kind": SITE_PREVIEW_KIND,
            "source_url": source_url,
            "max_width": width,
            "max_height": height,
            "pages": 1,
            "sink": sink,
        }
        try:
            timeout = httpx.Timeout(SITE_PREVIEW_TIMEOUT_SECONDS)
            async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
                async with client.stream(
                    "POST",
                    f"{self.service_url}/render",
                    headers={"Authorization": f"Bearer {self.token}"},
                    files={"request": (None, json.dumps(request, separators=(",", ":")))},
                ) as response:
                    limit = SITE_PREVIEW_RESPONSE_MAX_BYTES if direct else SITE_PREVIEW_MAX_BYTES
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > limit:
                            raise ValueError(f"site preview response exceeds {limit} bytes")
                    if response.status_code != 200:
                        detail = bytes(body[:SITE_PREVIEW_DETAIL_BYTES]).decode(errors="replace")
                        raise ValueError(
                            f"site preview returned HTTP {response.status_code}: {detail}"
                        )
                    if direct:
                        metadata = PreviewMetadata.model_validate_json(body)
                        if metadata.width != width or metadata.height != height:
                            raise ValueError("site preview returned mismatched dimensions")
                        return StoredPreview(blob_key=key, size_bytes=metadata.size_bytes)
                    if response.headers.get("content-type") != SITE_PREVIEW_MEDIA_TYPE:
                        raise ValueError("site preview returned a non-png content type")
                    if not body.startswith(PNG_SIGNATURE):
                        raise ValueError("site preview returned non-png bytes")
                    returned_width = response.headers.get("x-preview-width")
                    returned_height = response.headers.get("x-preview-height")
                    if returned_width != str(width) or returned_height != str(height):
                        raise ValueError("site preview returned mismatched dimensions")
            await self.blob.put(key, bytes(body))
            return StoredPreview(blob_key=key, size_bytes=len(body))
        except (httpx.HTTPError, ValueError) as error:
            log(
                "site_preview.undrawn",
                site=name,
                error_class=type(error).__name__,
                detail=str(error)[:SITE_PREVIEW_DETAIL_BYTES],
            )
            return None
