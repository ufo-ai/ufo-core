"""A signed, expiring URL token for artifact delivery: no token, no bytes.

The web surface serves a shared file's bytes only for a token minted with the deploy's secret — an
HMAC over the blob key and an expiry — so a tampered or stale token yields nothing, and a key
outside the artifact namespace (a transcript, a compaction record) is refused even with a valid
signature. This module ships the verify half the surface enforces and the claims it returns; the
mint side is the `share_file` builtin, which signs with the same secret."""

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime

ARTIFACT_KEY_PREFIX = "artifacts/"
ARTIFACT_TOKEN_TTL_SECONDS = 3600


class ArtifactTokenError(ValueError):
    """A delivery token is malformed, tampered, expired, or outside the artifact namespace."""


@dataclass(frozen=True)
class ArtifactClaims:
    """What a verified token grants: the blob key to serve and the download filename to suggest."""

    blob_key: str
    filename: str
    expires_at: int


def verify_artifact_token(token: str, secret: str, now: datetime) -> ArtifactClaims:
    if not secret:
        raise ArtifactTokenError("artifact token secret is not configured")
    body, _, signature = token.partition(".")
    if not signature:
        raise ArtifactTokenError("artifact token is malformed")
    expected = (
        base64.urlsafe_b64encode(
            hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
        )
        .decode()
        .rstrip("=")
    )
    if not hmac.compare_digest(signature, expected):
        raise ArtifactTokenError("artifact token signature does not match")
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError as error:
        raise ArtifactTokenError("artifact token payload is unreadable") from error
    if not isinstance(payload, dict):
        raise ArtifactTokenError("artifact token payload is not an object")
    claims = ArtifactClaims(
        blob_key=str(payload.get("key", "")),
        filename=str(payload.get("filename", "")),
        expires_at=int(payload.get("expires_at", 0)),
    )
    if not claims.blob_key.startswith(ARTIFACT_KEY_PREFIX):
        raise ArtifactTokenError("artifact token key escapes the artifact namespace")
    if claims.expires_at <= int(now.timestamp()):
        raise ArtifactTokenError("artifact token is expired")
    return claims
