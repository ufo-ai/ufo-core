"""A signed member session token for the shared fleet: the token *is* the workspace claim.

One shared-serve process serves every workspace, so a request must name its workspace before any
RLS-scoped query runs — and a database lookup cannot resolve it, since finding the workspace is the
very thing that needs the scope (a token→workspace read would itself be cross-workspace, blocked by
the RLS-subject role). The token instead carries member_id + workspace_id under an HMAC over the
deploy secret, so `verify_session_token` resolves identity from the signature alone: no
cross-workspace read, no chicken-and-egg. `mint_session_token` signs and `verify_session_token`
checks the same body, so a round-trip agrees by construction — onboarding mints, the surface
verifies, and both read the one deploy secret. The single-workspace per-tenant deploy has no shared
fleet and issues no session token: its surface still resolves identity by the stored opaque token.
"""

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

SESSION_TOKEN_TTL_SECONDS = 30 * 24 * 3600


class SessionTokenError(ValueError):
    """A session token is malformed, tampered, or expired."""


@dataclass(frozen=True)
class SessionClaims:
    """Who a verified token speaks for: the member and the workspace the request scopes to."""

    member_id: UUID
    workspace_id: UUID
    expires_at: int


def _sign(secret: str, body: str) -> str:
    digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def mint_session_token(secret: str, member_id: UUID, workspace_id: UUID, expires_at: int) -> str:
    if not secret:
        raise SessionTokenError("session token secret is not configured")
    body = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "member_id": str(member_id),
                    "workspace_id": str(workspace_id),
                    "expires_at": expires_at,
                }
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    return f"{body}.{_sign(secret, body)}"


def verify_session_token(token: str, secret: str, now: datetime) -> SessionClaims:
    if not secret:
        raise SessionTokenError("session token secret is not configured")
    body, _, signature = token.partition(".")
    if not signature:
        raise SessionTokenError("session token is malformed")
    if not hmac.compare_digest(signature, _sign(secret, body)):
        raise SessionTokenError("session token signature does not match")
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError as error:
        raise SessionTokenError("session token payload is unreadable") from error
    if not isinstance(payload, dict):
        raise SessionTokenError("session token payload is not an object")
    try:
        claims = SessionClaims(
            member_id=UUID(str(payload["member_id"])),
            workspace_id=UUID(str(payload["workspace_id"])),
            expires_at=int(payload["expires_at"]),
        )
    except (KeyError, ValueError) as error:
        raise SessionTokenError("session token payload is incomplete") from error
    if claims.expires_at <= int(now.timestamp()):
        raise SessionTokenError("session token is expired")
    return claims
