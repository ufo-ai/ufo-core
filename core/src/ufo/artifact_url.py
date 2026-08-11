"""A signed, expiring download URL for artifact delivery: no signature, no bytes.

An artifact's URL path is its blob key — `/artifacts/<artifact-id>/<filename>` names exactly the
bytes `artifacts/<artifact-id>/<filename>` stores — and the query string carries the grant that
opens it anonymously: `exp`, an expiry, `sig`, an HMAC over the artifact id, that expiry, and the
optional `preview` claim, all signed with the deploy's secret. A preview claim opts a raster image
into inline rendering, declaring the type and exact size the route validates the bytes against
before serving. A tampered grant yields nothing, an expired one only what a signed-in member of
the owning workspace can reclaim, and only the artifact namespace is addressable by construction.
The filename rides outside the signature: it re-joins the signed artifact id to address the one
blob stored under it, so a renamed segment addresses nothing and 404s. `mint_artifact_url` signs
and `verify_artifact_url` checks the same values, so a round-trip agrees by construction: the
`share_file` builtin mints, core's artifact route verifies, and both read the one deploy secret."""

import mimetypes
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from urllib.parse import quote
from uuid import UUID

from ufo.image_previews import (
    IMAGE_PREVIEW_MAX_BYTES,
    RASTER_IMAGE_SUFFIXES,
    ImagePreviewGrant,
    RasterImageMediaType,
)
from ufo.token_signing import sign_detached, verify_detached

ARTIFACT_KEY_PREFIX = "artifacts/"
ARTIFACT_URL_TTL_SECONDS = 3600
FALLBACK_MEDIA_TYPE = "application/octet-stream"


class ArtifactUrlError(ValueError):
    """A download URL is malformed, tampered, expired, or outside the artifact namespace."""


@dataclass(frozen=True)
class ArtifactClaims:
    """What a verified URL grants: the blob key to serve, the download filename it ends in, and
    the raster preview claim when the minter opted the file into inline rendering."""

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
    """The MIME type an artifact stores and serves, from its one filename. A name whose type
    carries a compression encoding (`chart.png.gz`) is not the inner type — declaring `image/png`
    without `content-encoding: gzip` serves broken bytes — so it falls back with the unknowns."""
    guessed, encoding = mimetypes.guess_type(filename)
    return guessed if guessed and encoding is None else FALLBACK_MEDIA_TYPE


def mint_artifact_url(
    secret: str, blob_key: str, expires_at: int, *, preview: ImagePreviewGrant | None = None
) -> str:
    """The signed download path for `blob_key`: the blob key as the path, the grant as the query.
    Callers needing an absolute URL prepend their public base."""
    if not secret:
        raise ArtifactUrlError("artifact url secret is not configured")
    artifact_id, filename = _split_key(blob_key)
    preview_value = "" if preview is None else f"{preview.media_type}:{preview.size_bytes}"
    if preview is not None and _parsed_preview(preview_value) is None:
        raise ArtifactUrlError("artifact preview claim is invalid")
    signature = sign_detached(
        secret.encode(), _signed_message(artifact_id, str(expires_at), preview_value)
    )
    url = f"/{ARTIFACT_KEY_PREFIX}{artifact_id}/{quote(filename, safe='')}"
    url += f"?exp={expires_at}&sig={signature}"
    return url if not preview_value else f"{url}&preview={quote(preview_value, safe='')}"


def verify_artifact_url(
    secret: str,
    artifact_id: str,
    filename: str,
    expires_at: str,
    signature: str,
    preview: str,
    now: datetime,
) -> ArtifactClaims:
    """The claims a URL proves, or `ArtifactUrlError`. The filename joins the signed artifact id
    into the blob key it addresses; nothing outside `artifacts/<uuid>/` is constructible."""
    if not secret:
        raise ArtifactUrlError("artifact url secret is not configured")
    if not _is_artifact_id(artifact_id) or not _is_filename(filename) or not expires_at.isdigit():
        raise ArtifactUrlError("artifact url is malformed")
    grant = _parsed_preview(preview) if preview else None
    if preview and grant is None:
        raise ArtifactUrlError("artifact preview claim is invalid")
    if not verify_detached(
        secret.encode(), _signed_message(artifact_id, expires_at, preview), signature
    ):
        raise ArtifactUrlError("artifact url signature does not match")
    claims = ArtifactClaims(
        blob_key=f"{ARTIFACT_KEY_PREFIX}{artifact_id}/{filename}",
        filename=filename,
        expires_at=int(expires_at),
        preview=grant,
    )
    if claims.expires_at <= int(now.timestamp()):
        raise ArtifactUrlExpired(claims)
    return claims


def _signed_message(artifact_id: str, expires_at: str, preview_value: str) -> bytes:
    return f"{artifact_id}:{expires_at}:{preview_value}".encode()


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
        or not _is_artifact_id(artifact_id)
        or not _is_filename(filename)
    ):
        raise ArtifactUrlError(f"{blob_key!r} is not an artifact address")
    return artifact_id, filename


def _is_artifact_id(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _is_filename(value: str) -> bool:
    return bool(value) and "/" not in value and value not in (".", "..")
