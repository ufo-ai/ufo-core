from io import BytesIO
from uuid import uuid4

import httpx
from PIL import Image

from ufo.auth.bearer import UFO_TOKEN_SECRET_ENV
from ufo.blob import FilesystemBlobStore, S3BlobStore, WorkspaceBlobStore
from ufo.media.site_previewer import SitePreviewer
from ufo.workspace import ws


def _png() -> bytes:
    buffer = BytesIO()
    image = Image.new("RGB", (1200, 900), "#111111")
    image.paste("#eeeeee", (100, 100, 400, 300))
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def test_filesystem_preview_uses_service_bytes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "site-preview-secret")
    picture = _png()
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["authorization"]
        seen["body"] = request.content
        return httpx.Response(
            200,
            content=picture,
            headers={
                "content-type": "image/png",
                "x-preview-width": "1200",
                "x-preview-height": "900",
                "x-preview-page-count": "1",
                "x-preview-size-bytes": str(len(picture)),
                "x-preview-sha256": "a" * 64,
            },
        )

    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    renderer = SitePreviewer(
        blob=blob,
        service_url="http://preview.svc:8930",
        token="preview-token",
        ingress_public_url="https://testing.ufo.ai",
        transport=httpx.MockTransport(handler),
    )
    workspace_id = uuid4()
    with ws(workspace_id):
        stored = await renderer.render(uuid4(), 25000, "marketing", 1200, 900)
        assert stored is not None
        assert await blob.get(stored.blob_key) == picture

    body = bytes(seen["body"])
    assert seen["authorization"] == "Bearer preview-token"
    assert b'"kind":"site"' in body
    assert b'"source_url":"https://' in body
    assert b".testing.ufo.ai/~t/" in body
    assert b'"inline":true' in body


async def test_s3_preview_sends_source_and_sink_capabilities(
    s3_store: S3BlobStore, monkeypatch
) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "site-preview-secret")
    seen: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        return httpx.Response(
            200,
            json={
                "width": 1200,
                "height": 900,
                "page_count": 1,
                "size_bytes": 4242,
                "sha256": "a" * 64,
            },
        )

    renderer = SitePreviewer(
        blob=WorkspaceBlobStore(backend=s3_store),
        service_url="http://preview.svc:8930",
        token="preview-token",
        ingress_public_url="https://testing.ufo.ai",
        transport=httpx.MockTransport(handler),
    )
    with ws(uuid4()):
        stored = await renderer.render(uuid4(), 25000, "marketing", 1200, 900)

    assert stored is not None
    assert stored.size_bytes == 4242
    assert stored.blob_key.endswith("/marketing.png")
    body = seen["body"]
    assert b'"source_url":"https://' in body
    assert b'"put_url":"http://' in body


async def test_service_refusal_leaves_no_preview(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "site-preview-secret")
    renderer = SitePreviewer(
        blob=WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path)),
        service_url="http://preview.svc:8930",
        token="preview-token",
        ingress_public_url="https://testing.ufo.ai",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(502, json={"error": "fetch_refused"})
        ),
    )
    with ws(uuid4()):
        assert await renderer.render(uuid4(), 25000, "marketing", 1200, 900) is None
