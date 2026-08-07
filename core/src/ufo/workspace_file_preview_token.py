import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from hmac import compare_digest
from pathlib import PurePosixPath
from typing import cast
from uuid import UUID

from ufo.image_previews import (
    IMAGE_PREVIEW_MAX_BYTES,
    RASTER_IMAGE_SUFFIXES,
    ImagePreviewGrant,
    RasterImageMediaType,
)
from ufo.token_signing import SignedTokenError, sign_token, verify_token

WORKSPACE_FILE_PREVIEW_KIND = "workspace_file_preview"
WORKSPACE_FILE_PREVIEW_TTL_SECONDS = 3600


class WorkspaceFilePreviewTokenError(ValueError):
    pass


@dataclass(frozen=True)
class WorkspaceFilePreviewClaims:
    workspace_id: UUID
    conversation_id: UUID
    path_digest: str
    preview: ImagePreviewGrant
    expires_at: int


def mint_workspace_file_preview_token(
    secret: str,
    workspace_id: UUID,
    conversation_id: UUID,
    path: str,
    preview: ImagePreviewGrant,
    expires_at: int,
) -> str:
    if not secret:
        raise WorkspaceFilePreviewTokenError("workspace file preview secret is not configured")
    path_digest = workspace_file_preview_path_digest(path)
    _validate_preview(preview)
    payload = json.dumps(
        {
            "kind": WORKSPACE_FILE_PREVIEW_KIND,
            "workspace_id": str(workspace_id),
            "conversation_id": str(conversation_id),
            "path_digest": path_digest,
            "media_type": preview.media_type,
            "size_bytes": preview.size_bytes,
            "expires_at": expires_at,
        }
    ).encode()
    return sign_token(secret.encode(), payload)


def verify_workspace_file_preview_token(
    token: str, secret: str, now: datetime
) -> WorkspaceFilePreviewClaims:
    if not secret:
        raise WorkspaceFilePreviewTokenError("workspace file preview secret is not configured")
    try:
        payload = json.loads(verify_token(token, secret.encode()))
        if not isinstance(payload, dict) or payload.get("kind") != WORKSPACE_FILE_PREVIEW_KIND:
            raise ValueError
        media_type = str(payload.get("media_type", ""))
        if media_type not in RASTER_IMAGE_SUFFIXES.values():
            raise ValueError
        claims = WorkspaceFilePreviewClaims(
            workspace_id=UUID(str(payload.get("workspace_id", ""))),
            conversation_id=UUID(str(payload.get("conversation_id", ""))),
            path_digest=str(payload.get("path_digest", "")),
            preview=ImagePreviewGrant(
                media_type=cast(RasterImageMediaType, media_type),
                size_bytes=int(payload.get("size_bytes", -1)),
            ),
            expires_at=int(payload.get("expires_at", 0)),
        )
        if len(claims.path_digest) != 64 or any(
            character not in "0123456789abcdef" for character in claims.path_digest
        ):
            raise ValueError
        _validate_preview(claims.preview)
    except (SignedTokenError, TypeError, ValueError) as error:
        raise WorkspaceFilePreviewTokenError("workspace file preview token is invalid") from error
    if claims.expires_at <= int(now.timestamp()):
        raise WorkspaceFilePreviewTokenError("workspace file preview token is expired")
    return claims


def workspace_file_preview_path_digest(path: str) -> str:
    """A fixed-size identity for the exact workspace-relative path carried by a preview request."""
    if not path or path.startswith("/") or ".." in PurePosixPath(path).parts:
        raise WorkspaceFilePreviewTokenError("workspace file preview path is invalid")
    return sha256(path.encode()).hexdigest()


def workspace_file_preview_path_matches(path: str, expected_digest: str) -> bool:
    """Whether a request path is the exact path identity granted by a preview token."""
    try:
        actual_digest = workspace_file_preview_path_digest(path)
    except WorkspaceFilePreviewTokenError:
        return False
    return compare_digest(actual_digest, expected_digest)


def _validate_preview(preview: ImagePreviewGrant) -> None:
    if preview.media_type not in RASTER_IMAGE_SUFFIXES.values():
        raise WorkspaceFilePreviewTokenError("workspace file preview media type is invalid")
    if preview.size_bytes < 0 or preview.size_bytes > IMAGE_PREVIEW_MAX_BYTES:
        raise WorkspaceFilePreviewTokenError("workspace file preview exceeds the byte limit")
