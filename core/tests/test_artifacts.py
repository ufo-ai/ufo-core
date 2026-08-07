import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from email.message import Message
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image

from ufo.artifact_token import (
    ARTIFACT_KEY_PREFIX,
    ArtifactTokenError,
    mint_artifact_token,
    verify_artifact_token,
)
from ufo.blob import FilesystemBlobStore
from ufo.image_previews import IMAGE_PREVIEW_MAX_BYTES, ImagePreviewGrant
from ufo.surfaces.artifacts import router as artifacts_router
from ufo.token_signing import sign_token

SECRET = "artifact-signing-secret"
DOWNLOAD_PATH = "/artifacts/download"


def _future() -> int:
    return int(datetime.now(UTC).timestamp()) + 3600


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), (12, 34, 56)).save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
async def artifact_client(
    tmp_path,
) -> AsyncIterator[tuple[AsyncClient, FilesystemBlobStore]]:
    blob = FilesystemBlobStore(root=tmp_path)
    app = FastAPI()
    app.state.blob = blob
    app.state.artifact_token_secret = SECRET
    app.include_router(artifacts_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://core") as client:
        yield client, blob


def test_verify_artifact_token_accepts_valid_and_rejects_everything_else() -> None:
    now = datetime.now(UTC)
    key = "artifacts/abc123"
    token = mint_artifact_token(SECRET, key, "report.txt", int(now.timestamp()) + 100)
    claims = verify_artifact_token(token, SECRET, now)
    assert claims.blob_key == key
    assert claims.filename == "report.txt"
    assert claims.preview is None
    with pytest.raises(ArtifactTokenError):
        verify_artifact_token(token, "wrong-secret", now)
    with pytest.raises(ArtifactTokenError):
        verify_artifact_token(token + "x", SECRET, now)
    with pytest.raises(ArtifactTokenError):
        verify_artifact_token(
            mint_artifact_token(SECRET, key, "", int(now.timestamp()) - 1), SECRET, now
        )
    with pytest.raises(ArtifactTokenError):
        verify_artifact_token(
            mint_artifact_token(
                SECRET, "conversations/c/messages.json.lz4", "", int(now.timestamp()) + 100
            ),
            SECRET,
            now,
        )
    with pytest.raises(ArtifactTokenError):
        verify_artifact_token(
            mint_artifact_token(
                SECRET,
                "artifacts/../conversations/c/messages.json.lz4",
                "",
                int(now.timestamp()) + 100,
            ),
            SECRET,
            now,
        )
    with pytest.raises(ArtifactTokenError):
        verify_artifact_token("not-a-token", SECRET, now)
    with pytest.raises(ArtifactTokenError):
        verify_artifact_token(token, "", now)


async def test_artifact_download_serves_bytes_for_a_valid_token(
    artifact_client: tuple[AsyncClient, FilesystemBlobStore],
) -> None:
    client, blob = artifact_client
    key = f"artifacts/{uuid4()}"
    await blob.put(key, b"the shared bytes")
    token = mint_artifact_token(SECRET, key, "report.txt", _future())
    response = await client.get(DOWNLOAD_PATH, params={"token": token})
    assert response.status_code == 200
    assert response.content == b"the shared bytes"
    assert "report.txt" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_artifact_preview_serves_only_safe_raster_types_inline(
    artifact_client: tuple[AsyncClient, FilesystemBlobStore],
) -> None:
    client, blob = artifact_client
    key = f"artifacts/{uuid4()}"
    raster = _png()
    await blob.put(key, raster)

    png = mint_artifact_token(
        SECRET,
        key,
        "chart.png",
        _future(),
        preview=ImagePreviewGrant(media_type="image/png", size_bytes=len(raster)),
    )
    shown = await client.get(DOWNLOAD_PATH, params={"token": png})
    assert shown.status_code == 200
    assert shown.content == raster
    assert shown.headers["content-type"] == "image/png"
    assert shown.headers["x-content-type-options"] == "nosniff"
    assert "content-disposition" not in shown.headers

    svg = mint_artifact_token(SECRET, key, "chart.svg", _future())
    refused = await client.get(DOWNLOAD_PATH, params={"token": svg, "preview": "true"})
    assert refused.status_code == 200
    assert refused.headers["content-type"] == "application/octet-stream"
    assert refused.headers["content-disposition"].startswith("attachment")


async def test_artifact_preview_rejects_mislabeled_invalid_and_oversize_claims(
    artifact_client: tuple[AsyncClient, FilesystemBlobStore],
) -> None:
    client, blob = artifact_client
    key = f"artifacts/{uuid4()}"
    raster = _png()
    await blob.put(key, raster)

    mislabeled = mint_artifact_token(
        SECRET,
        key,
        "chart.jpg",
        _future(),
        preview=ImagePreviewGrant(media_type="image/jpeg", size_bytes=len(raster)),
    )
    invalid_key = f"artifacts/{uuid4()}"
    invalid = b"not an image"
    await blob.put(invalid_key, invalid)
    invalid_token = mint_artifact_token(
        SECRET,
        invalid_key,
        "chart.png",
        _future(),
        preview=ImagePreviewGrant(media_type="image/png", size_bytes=len(invalid)),
    )
    wrong_size = mint_artifact_token(
        SECRET,
        key,
        "chart.png",
        _future(),
        preview=ImagePreviewGrant(media_type="image/png", size_bytes=len(raster) - 1),
    )
    oversized = sign_token(
        SECRET.encode(),
        json.dumps(
            {
                "key": key,
                "filename": "chart.png",
                "expires_at": _future(),
                "preview": {
                    "media_type": "image/png",
                    "size_bytes": IMAGE_PREVIEW_MAX_BYTES + 1,
                },
            }
        ).encode(),
    )

    assert (await client.get(DOWNLOAD_PATH, params={"token": mislabeled})).status_code == 415
    assert (await client.get(DOWNLOAD_PATH, params={"token": invalid_token})).status_code == 415
    assert (await client.get(DOWNLOAD_PATH, params={"token": wrong_size})).status_code == 415
    assert (await client.get(DOWNLOAD_PATH, params={"token": oversized})).status_code == 403


async def test_artifact_download_escapes_special_filenames(
    artifact_client: tuple[AsyncClient, FilesystemBlobStore],
) -> None:
    """A `"` would break the quoted `filename=` and a unicode name is not header-safe; both must
    ride out as a well-formed Content-Disposition the download still serves."""
    client, blob = artifact_client
    for filename in ('a"quote.txt', "résumé pièce.txt"):
        key = f"artifacts/{uuid4()}"
        await blob.put(key, b"the bytes")
        token = mint_artifact_token(SECRET, key, filename, _future())
        response = await client.get(DOWNLOAD_PATH, params={"token": token})
        assert response.status_code == 200
        assert response.content == b"the bytes"
        parsed = Message()
        parsed["content-disposition"] = response.headers["content-disposition"]
        assert parsed.get_content_disposition() == "attachment"
        assert parsed.get_filename() == filename


async def test_artifact_download_rejects_missing_tampered_expired_and_out_of_namespace(
    artifact_client: tuple[AsyncClient, FilesystemBlobStore],
) -> None:
    client, blob = artifact_client
    key = f"artifacts/{uuid4()}"
    await blob.put(key, b"x")
    missing = await client.get(DOWNLOAD_PATH)
    tampered = await client.get(
        DOWNLOAD_PATH,
        params={"token": mint_artifact_token(SECRET, key, "", _future()) + "z"},
    )
    expired = await client.get(
        DOWNLOAD_PATH,
        params={
            "token": mint_artifact_token(SECRET, key, "", int(datetime.now(UTC).timestamp()) - 10)
        },
    )
    outside = await client.get(
        DOWNLOAD_PATH,
        params={"token": mint_artifact_token(SECRET, "conversations/c/x", "", _future())},
    )
    assert missing.status_code == 401
    assert tampered.status_code == 403
    assert expired.status_code == 403
    assert outside.status_code == 403


async def test_artifact_download_with_a_valid_token_but_absent_blob_is_a_404(
    artifact_client: tuple[AsyncClient, FilesystemBlobStore],
) -> None:
    """A well-formed token whose blob is gone (e.g. reaped) is a 404 decided up front, before the
    stream opens — not a 200 with a broken body once delivery moved from a buffered read to a
    streamed one."""
    client, _ = artifact_client
    token = mint_artifact_token(SECRET, f"artifacts/{uuid4()}", "gone.txt", _future())
    response = await client.get(DOWNLOAD_PATH, params={"token": token})
    assert response.status_code == 404


async def test_download_endpoint_serves_a_minted_artifact(
    artifact_client: tuple[AsyncClient, FilesystemBlobStore],
) -> None:
    """The download endpoint is the consumer of a share_file token: bytes under an artifacts key
    plus a valid token serve. `share_file` producing that token+blob is proven end-to-end against a
    real container in test_file_tools; here the token is minted directly to keep this non-Docker."""
    client, blob = artifact_client
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    await blob.put(key, b"produced report bytes")
    now = datetime.now(UTC)
    token = mint_artifact_token(SECRET, key, "report.txt", int(now.timestamp()) + 100)
    response = await client.get(DOWNLOAD_PATH, params={"token": token})
    assert response.status_code == 200
    assert response.content == b"produced report bytes"
    assert "report.txt" in response.headers["content-disposition"]
