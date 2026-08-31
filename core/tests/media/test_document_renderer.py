import io
import json
import zipfile
from base64 import b64decode

import httpx

from ufo.harness.document_renderer import DocumentRenderer


def _bundle(
    kind: str = "docx",
    start_page: int = 2,
    limit: int = 1,
    page_file: str = "page-01.png",
) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "kind": kind,
                    "total_pages": 3,
                    "requested_range": {"start_page": start_page, "limit": limit},
                    "pages": [
                        {
                            "number": 2,
                            "file": page_file,
                            "width": 640,
                            "height": 480,
                            "text": "visible page two",
                        }
                    ],
                }
            ),
        )
        archive.writestr(page_file, b"\x89PNG\r\n\x1a\nrendered")
    return target.getvalue()


async def test_render_posts_bounded_bytes_and_returns_the_paginated_document_shape() -> None:
    async def preview(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer preview-real"
        body = await request.aread()
        assert b"document.docx" in body
        assert b"document bytes" in body
        assert b'"start_page":2' in body
        return httpx.Response(200, content=_bundle())

    renderer = DocumentRenderer(
        service_url="https://preview.test",
        token="preview-real",
        transport=httpx.MockTransport(preview),
    )
    result = await renderer.render(
        "/workspace/letter.docx", "docx", b"document bytes", start_page=2, limit=1
    )
    assert result["type"] == "docx"
    assert result["text"] == "visible page two"
    assert result["total_pages"] == 3
    assert result["start_page"] == 2
    assert result["pages_returned"] == 1
    assert result["next_page"] == 3
    assert b64decode(result["pages"][0]["data"]).startswith(b"\x89PNG")


async def test_render_refuses_a_bundle_with_entries_outside_the_manifest() -> None:
    bundle = io.BytesIO(_bundle())
    rewritten = io.BytesIO()
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(rewritten, "w") as target:
        for info in source.infolist():
            target.writestr(info.filename, source.read(info))
        target.writestr("extra.png", b"x")

    async def preview(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=rewritten.getvalue())

    renderer = DocumentRenderer(
        service_url="https://preview.test",
        token="preview-real",
        transport=httpx.MockTransport(preview),
    )
    try:
        await renderer.render("/workspace/letter.docx", "docx", b"bytes", 2, 1)
    except RuntimeError as error:
        assert str(error) == "document render returned unexpected bundle entries"
    else:
        raise AssertionError("unexpected bundle entry was accepted")


async def test_render_refuses_a_bundle_with_noncanonical_page_names() -> None:
    async def preview(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_bundle(page_file="other.png"))

    renderer = DocumentRenderer(
        service_url="https://preview.test",
        token="preview-real",
        transport=httpx.MockTransport(preview),
    )
    try:
        await renderer.render("/workspace/letter.docx", "docx", b"bytes", 2, 1)
    except RuntimeError as error:
        assert str(error) == "document render returned an invalid page name"
    else:
        raise AssertionError("noncanonical page name was accepted")
