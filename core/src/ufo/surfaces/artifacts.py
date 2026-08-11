"""The artifact download route: core-mounted, signed-URL file delivery — no signature, no bytes.

A shared file's bytes serve only for a URL minted with the deploy's artifact secret, the same URL
the `share_file` builtin mints and `SurfaceContext.artifact_link` hands a member. An expired URL
keeps one way back in: a signed-in member of the workspace that shared the file, proven by the
portal's session cookie, is redirected to the same path under a fresh grant — so a link in a
day-old thread self-heals for a teammate while it stays dead for anyone else; a browser with no
session is sent to sign in carrying the link as its target. The route is core and always mounted,
so it stays reachable whether or not a chat surface is installed — the web surface links to it,
Slack links to it for an oversize attachment, and it verifies with the one deploy secret it reads
off `app.state`."""

from datetime import UTC, datetime
from urllib.parse import quote
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse

from ufo.artifact_url import (
    ARTIFACT_KEY_PREFIX,
    ARTIFACT_URL_TTL_SECONDS,
    ArtifactClaims,
    ArtifactUrlError,
    ArtifactUrlExpired,
    artifact_media_type,
    mint_artifact_url,
    verify_artifact_url,
)
from ufo.bearer import LOGIN_PATH, SESSION_COOKIE, verified_claims
from ufo.blob import BlobStore
from ufo.db import workspace_tx
from ufo.image_previews import InvalidImagePreview, validated_image_preview
from ufo.schema import tables
from ufo.workspace import ws

router = APIRouter()

ARTIFACT_TARGET_PARAM = "a"
EXPIRED_DETAIL = "The download link expired. Ask the agent to share the file again."
UNCACHED = {"x-content-type-options": "nosniff", "cache-control": "private, no-store"}


@router.get("/" + ARTIFACT_KEY_PREFIX + "{artifact_id}/{filename}")
async def download(
    request: Request,
    artifact_id: str,
    filename: str,
    exp: str = "",
    sig: str = "",
    preview: str = "",
) -> Response:
    """Serve the URL's blob as a streamed download or a bounded validated raster preview. A
    download streams out in bounded chunks — the bytes never buffer whole, so a large or
    concurrent fetch can't spike memory — under the real media type its filename names, always as
    an attachment; only a signed preview claim renders inline, after the bytes prove to be the
    raster type and size it declares. `nosniff` holds the browser to the declared type and
    `no-store` keeps any cache from answering with the grant unchecked."""
    blob: BlobStore = request.app.state.blob
    secret: str = request.app.state.artifact_token_secret
    try:
        claims = verify_artifact_url(
            secret, artifact_id, filename, exp, sig, preview, datetime.now(UTC)
        )
    except ArtifactUrlExpired as expired:
        return await _refreshed_for_member(request, expired.claims, secret)
    except ArtifactUrlError as error:
        raise HTTPException(403, str(error)) from error
    if not await blob.exists(claims.blob_key):
        raise HTTPException(404, "artifact not found")
    if claims.preview is not None:
        try:
            data = await validated_image_preview(blob.get_stream(claims.blob_key), claims.preview)
        except InvalidImagePreview as error:
            raise HTTPException(415, str(error)) from error
        return Response(content=data, media_type=claims.preview.media_type, headers=UNCACHED)
    encoded = quote(claims.filename, safe="")
    headers = {
        "content-disposition": (
            f'attachment; filename="{claims.filename}"'
            if encoded == claims.filename
            else f"attachment; filename*=UTF-8''{encoded}"
        ),
        **UNCACHED,
    }
    return StreamingResponse(
        blob.get_stream(claims.blob_key),
        media_type=artifact_media_type(claims.filename),
        headers=headers,
    )


async def _refreshed_for_member(
    request: Request, claims: ArtifactClaims, secret: str
) -> RedirectResponse:
    """An expired URL is still an authentic grant record: its signature named the blob it granted.
    A signed-in member of the workspace that shared it — the teammate opening a day-old thread —
    is redirected to the same path under a fresh grant, so the link in their address bar works for
    another hour and bytes only ever serve under a live signature. The session bearer rides the
    same `ufo_session` cookie the portal authenticates by, and the membership and ownership reads
    run under that bearer's own workspace scope. A refused browser is sent to sign in with this
    link as its target, so signing in lands back here and the grant refreshes; any other client
    gets the 403 that names the next step."""
    bearer = request.cookies.get(SESSION_COOKIE, "")
    session = verified_claims(bearer) if bearer else None
    if session is None:
        raise _refusal(request)
    workspace_raw, email = session
    try:
        workspace = UUID(workspace_raw)
    except ValueError:
        raise _refusal(request) from None
    with ws(workspace):
        async with workspace_tx() as connection:
            member = (
                await connection.execute(
                    sa.select(tables.member.c.id).where(
                        tables.member.c.workspace_id == workspace,
                        tables.member.c.email == email,
                    )
                )
            ).scalar_one_or_none()
            owner = (
                await connection.execute(
                    sa.select(tables.shared_artifact.c.workspace_id)
                    .where(tables.shared_artifact.c.blob_key == claims.blob_key)
                    .limit(1)
                )
            ).scalar_one_or_none()
    if member is None or owner != workspace:
        raise _refusal(request)
    expires_at = int(datetime.now(UTC).timestamp()) + ARTIFACT_URL_TTL_SECONDS
    return RedirectResponse(
        mint_artifact_url(secret, claims.blob_key, expires_at, preview=claims.preview),
        status_code=303,
    )


def _refusal(request: Request) -> HTTPException:
    if "text/html" in request.headers.get("accept", ""):
        target = quote(f"{request.url.path}?{request.url.query}", safe="")
        location = f"{LOGIN_PATH}?{ARTIFACT_TARGET_PARAM}={target}"
        return HTTPException(303, EXPIRED_DETAIL, headers={"location": location})
    return HTTPException(403, EXPIRED_DETAIL)
