"""The artifact download route: core-mounted, token-gated file delivery — no token, no bytes.

A shared file's bytes serve only for a token minted with the deploy's artifact secret, the same
token the `share_file` builtin mints and `SurfaceContext.artifact_link` hands a member. The route is
core and always mounted, so it stays reachable whether or not a chat surface is installed — the web
surface links to it, Slack links to it for an oversize attachment, and it verifies with the one
deploy secret it reads off `app.state`."""

from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from selfhost.artifact_token import (
    ARTIFACT_DOWNLOAD_PATH,
    ArtifactTokenError,
    verify_artifact_token,
)
from selfhost.blob import BlobNotFound, BlobStore

router = APIRouter()


@router.get(ARTIFACT_DOWNLOAD_PATH)
async def download(request: Request, token: str = "") -> Response:
    blob: BlobStore = request.app.state.blob
    secret: str = request.app.state.artifact_token_secret
    if not token:
        raise HTTPException(401, "missing artifact token")
    try:
        claims = verify_artifact_token(token, secret, datetime.now(UTC))
    except ArtifactTokenError as error:
        raise HTTPException(403, str(error)) from error
    try:
        data = await blob.get(claims.blob_key)
    except BlobNotFound as error:
        raise HTTPException(404, "artifact not found") from error
    headers: dict[str, str] = {}
    if claims.filename:
        encoded = quote(claims.filename, safe="")
        headers["content-disposition"] = (
            f'attachment; filename="{claims.filename}"'
            if encoded == claims.filename
            else f"attachment; filename*=UTF-8''{encoded}"
        )
    return Response(data, media_type="application/octet-stream", headers=headers)
