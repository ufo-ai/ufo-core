import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ufo.image_previews import IMAGE_PREVIEW_MAX_BYTES, ImagePreviewGrant
from ufo.token_signing import sign_token
from ufo.workspace_file_preview_token import (
    WORKSPACE_FILE_PREVIEW_KIND,
    WorkspaceFilePreviewTokenError,
    mint_workspace_file_preview_token,
    verify_workspace_file_preview_token,
    workspace_file_preview_path_digest,
    workspace_file_preview_path_matches,
)

SECRET = "workspace-file-preview-secret"


def test_workspace_file_preview_token_round_trip_binds_every_claim() -> None:
    now = datetime.now(UTC)
    workspace_id = uuid4()
    conversation_id = uuid4()
    preview = ImagePreviewGrant(media_type="image/png", size_bytes=42)
    token = mint_workspace_file_preview_token(
        SECRET,
        workspace_id,
        conversation_id,
        "images/chart.png",
        preview,
        int(now.timestamp()) + 60,
    )

    claims = verify_workspace_file_preview_token(token, SECRET, now)
    assert claims.workspace_id == workspace_id
    assert claims.conversation_id == conversation_id
    assert claims.path_digest == workspace_file_preview_path_digest("images/chart.png")
    assert workspace_file_preview_path_matches("images/chart.png", claims.path_digest)
    assert not workspace_file_preview_path_matches("images/other.png", claims.path_digest)
    assert claims.preview == preview


def test_workspace_file_preview_token_rejects_tampering_and_expiry() -> None:
    now = datetime.now(UTC)
    token = mint_workspace_file_preview_token(
        SECRET,
        uuid4(),
        uuid4(),
        "images/chart.png",
        ImagePreviewGrant(media_type="image/png", size_bytes=42),
        int(now.timestamp()) - 1,
    )

    with pytest.raises(WorkspaceFilePreviewTokenError):
        verify_workspace_file_preview_token(token, SECRET, now)
    with pytest.raises(WorkspaceFilePreviewTokenError):
        verify_workspace_file_preview_token(token + "x", SECRET, now)


def test_workspace_file_preview_token_rejects_signed_oversize_claim() -> None:
    now = datetime.now(UTC)
    payload = {
        "kind": WORKSPACE_FILE_PREVIEW_KIND,
        "workspace_id": str(uuid4()),
        "conversation_id": str(uuid4()),
        "path_digest": workspace_file_preview_path_digest("images/chart.png"),
        "media_type": "image/png",
        "size_bytes": IMAGE_PREVIEW_MAX_BYTES + 1,
        "expires_at": int(now.timestamp()) + 60,
    }
    token = sign_token(SECRET.encode(), json.dumps(payload).encode())

    with pytest.raises(WorkspaceFilePreviewTokenError, match="invalid"):
        verify_workspace_file_preview_token(token, SECRET, now)


def test_workspace_file_preview_token_size_does_not_scale_with_path() -> None:
    now = datetime.now(UTC)
    workspace_id = uuid4()
    conversation_id = uuid4()
    preview = ImagePreviewGrant(media_type="image/png", size_bytes=42)
    short = mint_workspace_file_preview_token(
        SECRET,
        workspace_id,
        conversation_id,
        "x.png",
        preview,
        int(now.timestamp()) + 60,
    )
    long = mint_workspace_file_preview_token(
        SECRET,
        workspace_id,
        conversation_id,
        "nested/" + "x" * 4_000 + ".png",
        preview,
        int(now.timestamp()) + 60,
    )

    assert len(short) == len(long)
