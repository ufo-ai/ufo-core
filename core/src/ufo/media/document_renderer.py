import asyncio
import io
import json
import zipfile
from base64 import b64encode
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ConfigDict

DOCUMENT_INPUT_MAX_BYTES = 100 * 1024 * 1024
DOCUMENT_BUNDLE_MAX_BYTES = 20 * 1024 * 1024
DOCUMENT_MANIFEST_MAX_BYTES = 1024 * 1024
DOCUMENT_IMAGE_MAX_BYTES = 5 * 1024 * 1024
DOCUMENT_IMAGES_MAX_BYTES = 20 * 1024 * 1024
DOCUMENT_BASE64_MAX_BYTES = (DOCUMENT_IMAGES_MAX_BYTES + 2) // 3 * 4
DOCUMENT_PAGES_MAX = 20
DOCUMENT_RENDER_TIMEOUT_SECONDS = 330.0
DOCUMENT_QUALITY_REMINDER = (
    "CRITICAL: Before sharing, carefully examine each page for quality issues (e.g. overlapping "
    "text, hidden/cut-off text, text squished together). These are common and must be fixed."
)


class RequestedRange(BaseModel):
    model_config = ConfigDict(frozen=True)

    start_page: int
    limit: int


class RenderedPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    number: int
    file: str
    width: int
    height: int
    text: str


class DocumentManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    total_pages: int
    requested_range: RequestedRange
    pages: tuple[RenderedPage, ...]


@dataclass(frozen=True)
class DocumentRenderer:
    """Render bounded document bytes through ufo-preview and return the file tool's paginated
    text-plus-images result."""

    service_url: str
    token: str
    transport: httpx.AsyncBaseTransport | None = None

    async def render(
        self, path: str, kind: str, content: bytes, start_page: int, limit: int
    ) -> dict[str, object]:
        if len(content) > DOCUMENT_INPUT_MAX_BYTES:
            raise ValueError(
                f"{path} is {len(content)} bytes; over the {DOCUMENT_INPUT_MAX_BYTES}-byte "
                "document read cap"
            )
        start_page = max(1, start_page)
        limit = min(max(1, limit), DOCUMENT_PAGES_MAX)
        request = {
            "kind": kind,
            "max_width": 1400,
            "max_height": 1800,
            "start_page": start_page,
            "pages": limit,
            "sink": {"bundle": True},
        }
        timeout = httpx.Timeout(DOCUMENT_RENDER_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
            async with client.stream(
                "POST",
                f"{self.service_url}/render",
                headers={"Authorization": f"Bearer {self.token}"},
                files={
                    "request": (None, json.dumps(request, separators=(",", ":"))),
                    "file": (f"document.{kind}", content, "application/octet-stream"),
                },
            ) as response:
                if response.status_code != 200:
                    detail = bytearray()
                    async for chunk in response.aiter_bytes():
                        detail.extend(chunk[: 501 - len(detail)])
                        if len(detail) >= 501:
                            break
                    raise ValueError(
                        f"document render failed with HTTP {response.status_code}: "
                        f"{bytes(detail[:500]).decode(errors='replace')}"
                    )
                bundle = bytearray()
                async for chunk in response.aiter_bytes():
                    bundle.extend(chunk)
                    if len(bundle) > DOCUMENT_BUNDLE_MAX_BYTES:
                        raise ValueError(
                            f"document render exceeds the {DOCUMENT_BUNDLE_MAX_BYTES}-byte "
                            "bundle cap"
                        )
        return await asyncio.to_thread(self._unpack, path, kind, start_page, limit, bytes(bundle))

    def _unpack(
        self, path: str, kind: str, start_page: int, limit: int, bundle: bytes
    ) -> dict[str, object]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(bundle))
            manifest_info = archive.getinfo("manifest.json")
        except (KeyError, zipfile.BadZipFile) as error:
            raise RuntimeError(f"document render bundle: {error}") from error
        if manifest_info.file_size > DOCUMENT_MANIFEST_MAX_BYTES:
            raise ValueError(
                f"document manifest exceeds the {DOCUMENT_MANIFEST_MAX_BYTES}-byte cap"
            )
        try:
            manifest = DocumentManifest.model_validate_json(archive.read(manifest_info))
        except ValueError as error:
            raise RuntimeError(f"document render manifest: {error}") from error
        if (
            manifest.kind != kind
            or manifest.total_pages < 1
            or manifest.requested_range.start_page != start_page
            or manifest.requested_range.limit != limit
            or not manifest.pages
            or len(manifest.pages) > limit
            or manifest.pages[0].number != min(start_page, manifest.total_pages)
            or any(
                page.number < 1
                or page.number > manifest.total_pages
                or page.width < 1
                or page.height < 1
                for page in manifest.pages
            )
            or any(
                right.number != left.number + 1
                for left, right in zip(manifest.pages, manifest.pages[1:], strict=False)
            )
        ):
            raise RuntimeError("document render returned a mismatched manifest")
        names = archive.namelist()
        expected = ["manifest.json", *(page.file for page in manifest.pages)]
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise RuntimeError("document render returned unexpected bundle entries")
        decoded = 0
        encoded = 0
        images: list[dict[str, object]] = []
        text: list[str] = []
        for index, page in enumerate(manifest.pages, start=1):
            if page.file != f"page-{index:02}.png":
                raise RuntimeError("document render returned an invalid page name")
            info = archive.getinfo(page.file)
            if info.file_size > DOCUMENT_IMAGE_MAX_BYTES:
                raise ValueError(
                    f"document page {page.number} exceeds the {DOCUMENT_IMAGE_MAX_BYTES}-byte "
                    "image cap"
                )
            decoded += info.file_size
            encoded += (info.file_size + 2) // 3 * 4
            if decoded > DOCUMENT_IMAGES_MAX_BYTES or encoded > DOCUMENT_BASE64_MAX_BYTES:
                raise ValueError("document pages exceed the decoded image or base64 cap")
            image = archive.read(info)
            if not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise RuntimeError("document render returned a non-png page")
            images.append(
                {
                    "page": page.number,
                    "width": page.width,
                    "height": page.height,
                    "media_type": "image/png",
                    "data": b64encode(image).decode(),
                }
            )
            if page.text.strip():
                text.append(page.text.strip())
        returned = len(images)
        actual_start = manifest.pages[0].number if manifest.pages else start_page
        next_page = manifest.pages[-1].number + 1 if manifest.pages else None
        if next_page is not None and next_page > manifest.total_pages:
            next_page = None
        return {
            "path": path,
            "type": kind,
            "text": "\n\n".join(text),
            "total_pages": manifest.total_pages,
            "start_page": actual_start,
            "pages_returned": returned,
            "next_page": next_page,
            "pages": images,
            "quality_reminder": DOCUMENT_QUALITY_REMINDER,
        }
