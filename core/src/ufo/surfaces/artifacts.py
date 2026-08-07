"""The artifact download route: core-mounted, token-gated file delivery — no token, no bytes.

A shared file's bytes serve only for a token minted with the deploy's artifact secret, the same
token the `share_file` builtin mints and `SurfaceContext.artifact_link` hands a member. The route is
core and always mounted, so it stays reachable whether or not a chat surface is installed — the web
surface links to it, Slack links to it for an oversize attachment, and it verifies with the one
deploy secret it reads off `app.state`."""

from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from ufo.artifact_token import (
    ARTIFACT_DOWNLOAD_PATH,
    ArtifactTokenError,
    verify_artifact_token,
)
from ufo.blob import BlobStore
from ufo.image_previews import InvalidImagePreview, validated_image_preview

router = APIRouter()


@router.get(ARTIFACT_DOWNLOAD_PATH)
async def download(request: Request, token: str = "") -> Response:
    """Serve a token-gated artifact as a streamed download or a bounded validated raster preview."""
    blob: BlobStore = request.app.state.blob
    secret: str = request.app.state.artifact_token_secret
    if not token:
        raise HTTPException(401, "missing artifact token")
    try:
        claims = verify_artifact_token(token, secret, datetime.now(UTC))
    except ArtifactTokenError as error:
        raise HTTPException(403, str(error)) from error
    if not await blob.exists(claims.blob_key):
        raise HTTPException(404, "artifact not found")
    headers = {"X-Content-Type-Options": "nosniff"}
    if claims.preview is not None:
        try:
            data = await validated_image_preview(blob.get_stream(claims.blob_key), claims.preview)
        except InvalidImagePreview as error:
            raise HTTPException(415, str(error)) from error
        return Response(content=data, media_type=claims.preview.media_type, headers=headers)
    if claims.filename:
        encoded = quote(claims.filename, safe="")
        headers["content-disposition"] = (
            f'attachment; filename="{claims.filename}"'
            if encoded == claims.filename
            else f"attachment; filename*=UTF-8''{encoded}"
        )
    return StreamingResponse(
        blob.get_stream(claims.blob_key),
        media_type="application/octet-stream",
        headers=headers,
    )
