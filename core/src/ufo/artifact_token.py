"""A signed, expiring URL token for artifact delivery: no token, no bytes.

Core's artifact route serves a shared file's bytes only for a token minted with the deploy's secret
— an HMAC over the blob key and an expiry — so a tampered or stale token yields nothing, and a key
outside the artifact namespace (a transcript, a compaction record) is refused even with a valid
signature. `mint_artifact_token` signs and `verify_artifact_token` checks the same body, so a
round-trip agrees by construction: the `share_file` builtin mints, core's artifact route verifies,
and both read the one deploy secret."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import cast

from ufo.image_previews import (
    IMAGE_PREVIEW_MAX_BYTES,
    RASTER_IMAGE_SUFFIXES,
    ImagePreviewGrant,
    RasterImageMediaType,
)
from ufo.token_signing import SignedTokenError, sign_token, verify_token

ARTIFACT_KEY_PREFIX = "artifacts/"
ARTIFACT_TOKEN_TTL_SECONDS = 3600
ARTIFACT_DOWNLOAD_PATH = "/artifacts/download"


class ArtifactTokenError(ValueError):
    """A delivery token is malformed, tampered, expired, or outside the artifact namespace."""


@dataclass(frozen=True)
class ArtifactClaims:
    """What a verified token grants: the blob key to serve and the download filename to suggest."""

    blob_key: str
    filename: str
    expires_at: int
    preview: ImagePreviewGrant | None


def mint_artifact_token(
    secret: str,
    blob_key: str,
    filename: str,
    expires_at: int,
    *,
    preview: ImagePreviewGrant | None = None,
) -> str:
    if not secret:
        raise ArtifactTokenError("artifact token secret is not configured")
    if preview is not None:
        _validate_preview(preview)
    payload: dict[str, object] = {
        "key": blob_key,
        "filename": filename,
        "expires_at": expires_at,
    }
    if preview is not None:
        payload["preview"] = {
            "media_type": preview.media_type,
            "size_bytes": preview.size_bytes,
        }
    return sign_token(secret.encode(), json.dumps(payload).encode())


def verify_artifact_token(token: str, secret: str, now: datetime) -> ArtifactClaims:
    if not secret:
        raise ArtifactTokenError("artifact token secret is not configured")
    try:
        payload = json.loads(verify_token(token, secret.encode()))
    except (SignedTokenError, ValueError) as error:
        raise ArtifactTokenError("artifact token is invalid") from error
    if not isinstance(payload, dict):
        raise ArtifactTokenError("artifact token payload is not an object")
    try:
        preview_payload = payload.get("preview")
        preview = None
        if preview_payload is not None:
            if not isinstance(preview_payload, dict):
                raise ValueError
            media_type = str(preview_payload.get("media_type", ""))
            if media_type not in RASTER_IMAGE_SUFFIXES.values():
                raise ValueError
            preview = ImagePreviewGrant(
                media_type=cast(RasterImageMediaType, media_type),
                size_bytes=int(preview_payload.get("size_bytes", -1)),
            )
            _validate_preview(preview)
        claims = ArtifactClaims(
            blob_key=str(payload.get("key", "")),
            filename=str(payload.get("filename", "")),
            expires_at=int(payload.get("expires_at", 0)),
            preview=preview,
        )
    except (TypeError, ValueError) as error:
        raise ArtifactTokenError("artifact token payload is invalid") from error
    if (
        not claims.blob_key.startswith(ARTIFACT_KEY_PREFIX)
        or ".." in PurePosixPath(claims.blob_key).parts
    ):
        raise ArtifactTokenError("artifact token key escapes the artifact namespace")
    if claims.expires_at <= int(now.timestamp()):
        raise ArtifactTokenError("artifact token is expired")
    return claims


def _validate_preview(preview: ImagePreviewGrant) -> None:
    if preview.media_type not in RASTER_IMAGE_SUFFIXES.values():
        raise ArtifactTokenError("artifact preview media type is invalid")
    if preview.size_bytes < 0 or preview.size_bytes > IMAGE_PREVIEW_MAX_BYTES:
        raise ArtifactTokenError("artifact preview exceeds the byte limit")
