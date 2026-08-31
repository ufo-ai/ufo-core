"""A signed, expiring download URL for artifact delivery: no signature, no bytes.

An artifact's URL path is its workspace-relative blob key — `/artifacts/<artifact-id>/<filename>`
names exactly the bytes `artifacts/<artifact-id>/<filename>` stores under the owning workspace's
prefix — and the query string carries the grant that opens it anonymously: `exp`, an expiry, `ws`,
the workspace whose store holds the bytes, `sig`, an HMAC over all of it, and the optional
`preview` claim, all signed with the deploy's secret. A preview claim opts a raster image into
inline rendering, declaring the type and exact size the route validates the bytes against before
serving. A tampered grant yields nothing, an expired one only what a signed-in member of the
owning workspace can reclaim, and only the artifact namespace is addressable by construction. A
URL carrying no `ws` claim — the address form living in messages minted before the claim — is
authenticated against its own signed message and never served directly: it always takes the
member-refresh path, which re-scopes it to the owning workspace. The filename rides outside the
signature: it re-joins the signed artifact id to address the one blob stored under it, so a
renamed segment addresses nothing and 404s. `mint_artifact_url` signs and `verify_artifact_url`
checks the same values, so a round-trip agrees by construction: the `share_file` builtin mints,
core's artifact route verifies, and both read the one deploy secret.

Expiry is quantized: `artifact_url_expiry` answers the smallest bucket boundary at least the TTL
away, so every mint of one artifact within a bucket is a byte-identical URL. A URL that holds still
between reads is what lets the browser and the edge answer a repeat fetch from cache — a per-second
expiry made every mint unique and every poll a refetch — and quantizing up keeps the stated TTL as
the validity floor."""

import mimetypes
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import cast
from urllib.parse import quote
from uuid import UUID

from ufo.runtime.auth.token_signing import sign_detached, verify_detached
from ufo.runtime.media.image_previews import (
    IMAGE_PREVIEW_MAX_BYTES,
    RASTER_IMAGE_SUFFIXES,
    ImagePreviewGrant,
    RasterImageMediaType,
    raster_image_media_type,
)

ARTIFACT_KEY_PREFIX = "artifacts/"
ARTIFACT_URL_TTL_SECONDS = 3600
ARTIFACT_URL_BUCKET_SECONDS = 3600
FALLBACK_MEDIA_TYPE = "application/octet-stream"
ARTIFACT_MEDIA_TYPES = {
    ".diff": "text/x-patch",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".patch": "text/x-patch",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class ArtifactUrlError(ValueError):
    """A download URL is malformed, tampered, expired, or outside the artifact namespace."""


def artifact_url_expiry(now: datetime) -> int:
    """The `exp` a mint carries: the smallest `ARTIFACT_URL_BUCKET_SECONDS` boundary at least
    `ARTIFACT_URL_TTL_SECONDS` past `now`, so mints of one artifact within a bucket agree on the
    whole URL and a repeat fetch is a cache's to answer. Validity lands in
    (`ARTIFACT_URL_TTL_SECONDS`, `ARTIFACT_URL_TTL_SECONDS + ARTIFACT_URL_BUCKET_SECONDS`]."""
    return (
        (int(now.timestamp()) + ARTIFACT_URL_TTL_SECONDS) // ARTIFACT_URL_BUCKET_SECONDS + 1
    ) * ARTIFACT_URL_BUCKET_SECONDS


@dataclass(frozen=True)
class ArtifactClaims:
    """What a verified URL grants: the workspace whose store holds the bytes (None for an address
    minted without the claim, which only the member refresh can re-scope), the workspace-relative
    blob key to serve, the download filename it ends in, and the raster preview claim when the
    minter opted the file into inline rendering."""

    workspace_id: UUID | None
    blob_key: str
    filename: str
    expires_at: int
    preview: ImagePreviewGrant | None


class ArtifactUrlExpired(ArtifactUrlError):
    """A URL whose signature verifies but whose expiry has passed. Expiry is a fact about time,
    not authenticity, so the exception carries the claims for a caller holding another proof of
    access — the download route refreshes them for a signed-in member of the owning workspace."""

    def __init__(self, claims: ArtifactClaims) -> None:
        super().__init__("artifact url is expired")
        self.claims = claims


def artifact_media_type(filename: str) -> str:
    """The MIME type an artifact stores and serves, from its one filename. The suffixes the product
    keys features on — the media families, previews, and inline text rendering — resolve through
    `ARTIFACT_MEDIA_TYPES`, never the registry: `mimetypes` answers from the host's own mime files,
    and the hosted image (`python:3.12-slim`) carries none, so an office type guessed right on a
    laptop and landed as the fallback in production. A name whose type carries a compression
    encoding (`chart.png.gz`) is not the inner type — declaring `image/png` without
    `content-encoding: gzip` serves broken bytes — so it falls back with the unknowns."""
    declared = ARTIFACT_MEDIA_TYPES.get(PurePosixPath(filename).suffix.lower())
    if declared is not None:
        return declared
    guessed, encoding = mimetypes.guess_type(filename)
    return guessed if guessed and encoding is None else FALLBACK_MEDIA_TYPE


def mint_artifact_url(
    secret: str,
    blob_key: str,
    expires_at: int,
    *,
    workspace_id: UUID,
    preview: ImagePreviewGrant | None = None,
) -> str:
    """The signed download path for `blob_key` in `workspace_id`'s store: the workspace-relative
    blob key as the path, the grant as the query. Callers needing an absolute URL prepend their
    public base."""
    if not secret:
        raise ArtifactUrlError("artifact url secret is not configured")
    artifact_id, filename = _split_key(blob_key)
    preview_value = "" if preview is None else f"{preview.media_type}:{preview.size_bytes}"
    if preview is not None and _parsed_preview(preview_value) is None:
        raise ArtifactUrlError("artifact preview claim is invalid")
    signature = sign_detached(
        secret.encode(),
        _signed_message(str(workspace_id), artifact_id, str(expires_at), preview_value),
    )
    url = f"/{ARTIFACT_KEY_PREFIX}{artifact_id}/{quote(filename, safe='')}"
    url += f"?exp={expires_at}&ws={workspace_id}&sig={signature}"
    return url if not preview_value else f"{url}&preview={quote(preview_value, safe='')}"


def mint_image_preview_url(
    secret: str,
    public_base_url: str | None,
    blob_key: str,
    size_bytes: int | None,
    *,
    workspace_id: UUID,
) -> str | None:
    """The absolute signed link that renders one stored picture inline, or None when its type, size,
    or delivery is ineligible.

    The blob key names the picture's type, so the grant the route validates against is read off the
    bytes it will serve rather than off anything a caller declares. Every producer of an inline
    picture mints through here — a shared file's raster and an extension row's own preview alike —
    so one answer says which bytes the preview route will serve inline."""
    if not secret or not public_base_url:
        return None
    media_type = raster_image_media_type(blob_key)
    if media_type is None or size_bytes is None or size_bytes > IMAGE_PREVIEW_MAX_BYTES:
        return None
    expires_at = artifact_url_expiry(datetime.now(UTC))
    path = mint_artifact_url(
        secret,
        blob_key,
        expires_at,
        workspace_id=workspace_id,
        preview=ImagePreviewGrant(media_type=media_type, size_bytes=size_bytes),
    )
    return f"{public_base_url.rstrip('/')}{path}"


def verify_artifact_url(
    secret: str,
    artifact_id: str,
    filename: str,
    expires_at: str,
    signature: str,
    preview: str,
    workspace: str,
    now: datetime,
) -> ArtifactClaims:
    """The claims a URL proves, or `ArtifactUrlError`. The filename joins the signed artifact id
    into the blob key it addresses; nothing outside `artifacts/<uuid>/` is constructible. An empty
    `workspace` is the claim-less address form: its signature covers only the artifact id, expiry,
    and preview, and a valid one always raises `ArtifactUrlExpired` — unscoped bytes never serve
    directly, the member refresh re-scopes them."""
    if not secret:
        raise ArtifactUrlError("artifact url secret is not configured")
    if (
        not _is_canonical_uuid(artifact_id)
        or not _is_filename(filename)
        or not expires_at.isdigit()
    ):
        raise ArtifactUrlError("artifact url is malformed")
    if workspace and not _is_canonical_uuid(workspace):
        raise ArtifactUrlError("artifact url is malformed")
    grant = _parsed_preview(preview) if preview else None
    if preview and grant is None:
        raise ArtifactUrlError("artifact preview claim is invalid")
    if not verify_detached(
        secret.encode(), _signed_message(workspace, artifact_id, expires_at, preview), signature
    ):
        raise ArtifactUrlError("artifact url signature does not match")
    claims = ArtifactClaims(
        workspace_id=UUID(workspace) if workspace else None,
        blob_key=f"{ARTIFACT_KEY_PREFIX}{artifact_id}/{filename}",
        filename=filename,
        expires_at=int(expires_at),
        preview=grant,
    )
    if claims.workspace_id is None or claims.expires_at <= int(now.timestamp()):
        raise ArtifactUrlExpired(claims)
    return claims


def _signed_message(workspace: str, artifact_id: str, expires_at: str, preview_value: str) -> bytes:
    head = f"{workspace}:" if workspace else ""
    return f"{head}{artifact_id}:{expires_at}:{preview_value}".encode()


def _parsed_preview(value: str) -> ImagePreviewGrant | None:
    media_type, separator, size = value.rpartition(":")
    if (
        not separator
        or media_type not in RASTER_IMAGE_SUFFIXES.values()
        or not size.isdigit()
        or int(size) > IMAGE_PREVIEW_MAX_BYTES
    ):
        return None
    return ImagePreviewGrant(
        media_type=cast(RasterImageMediaType, media_type), size_bytes=int(size)
    )


def _split_key(blob_key: str) -> tuple[str, str]:
    artifact_id, separator, filename = blob_key.removeprefix(ARTIFACT_KEY_PREFIX).partition("/")
    if (
        not blob_key.startswith(ARTIFACT_KEY_PREFIX)
        or not separator
        or not _is_canonical_uuid(artifact_id)
        or not _is_filename(filename)
    ):
        raise ArtifactUrlError(f"{blob_key!r} is not an artifact address")
    return artifact_id, filename


def _is_canonical_uuid(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _is_filename(value: str) -> bool:
    return bool(value) and "/" not in value and value not in (".", "..")
