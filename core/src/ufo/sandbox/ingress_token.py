"""A signed, expiring token for sandbox ingress: authenticated port access.

A token claims a workspace, conversation, and port, and expires. The ingress verifies it against
the deploy secret — a tampered, stale, or malformed token yields nothing, and a token claiming a
port outside the addressable range is refused even with a valid signature. `mint_ingress_token`
signs and `verify_ingress_token` checks the same body, so a round-trip agrees by construction, and
both read the one deploy secret rather than taking it as a parameter.

A visit has two hops and each carries its own kind, named at both the mint and the verify. A
**view** token is what `SurfaceContext.ingress_url` puts in the link the frame opens at
`INGRESS_VIEW_PATH`; a **session** token is what the ingress binds as that origin's cookie
afterwards. Neither passes where the other is expected, so a cookie cannot be replayed at the view
path to mint itself a successor, and a view token pasted into the cookie jar opens nothing. A view
token is usable until it expires and may open several sessions in that window — a reload is a
second one; each session then runs its own TTL from the moment it was minted, and nothing extends
it."""

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from ufo.auth.bearer import UFO_TOKEN_SECRET_ENV
from ufo.auth.token_signing import SignedTokenError, sign_token, verify_token

IngressTokenKind = Literal["sandbox-ingress-view", "sandbox-ingress-session"]
INGRESS_VIEW_KIND: IngressTokenKind = "sandbox-ingress-view"
INGRESS_SESSION_KIND: IngressTokenKind = "sandbox-ingress-session"
INGRESS_VIEW_PATH = "/~t"
INGRESS_VIEW_TTL_SECONDS = 900


class IngressTokenError(ValueError):
    """An ingress token is malformed, tampered, expired, or not an ingress token at all."""


@dataclass(frozen=True)
class IngressClaims:
    """What a verified token grants: one workspace's conversation, and the one sandbox port its
    bearer may reach, until it expires."""

    workspace_id: UUID
    conversation_id: UUID
    port: int
    expires_at: int


def mint_ingress_token(claims: IngressClaims, kind: IngressTokenKind) -> str:
    """Sign `claims` as one hop's token. The kind is signed with them, so the hop a token was minted
    for is the only hop that accepts it."""
    payload = json.dumps(
        {
            "kind": kind,
            "ws": str(claims.workspace_id),
            "conversation": str(claims.conversation_id),
            "port": claims.port,
            "exp": claims.expires_at,
        }
    ).encode()
    return sign_token(ingress_secret().encode(), payload)


def verify_ingress_token(token: str, now: datetime, kind: IngressTokenKind) -> IngressClaims:
    """The claims of a live token of exactly `kind`, or `IngressTokenError`. A valid token of the
    other kind is refused as firmly as a forged one — the caller states which hop it is serving."""
    try:
        payload = json.loads(verify_token(token, ingress_secret().encode()))
    except (SignedTokenError, ValueError) as error:
        raise IngressTokenError("ingress token is invalid") from error
    if not isinstance(payload, dict) or payload.get("kind") != kind:
        raise IngressTokenError("ingress token is invalid")
    try:
        claims = IngressClaims(
            workspace_id=UUID(str(payload.get("ws"))),
            conversation_id=UUID(str(payload.get("conversation"))),
            port=int(payload.get("port", 0)),
            expires_at=int(payload.get("exp", 0)),
        )
    except (TypeError, ValueError) as error:
        raise IngressTokenError("ingress token is invalid") from error
    if not 0 < claims.port < 65536:
        raise IngressTokenError("ingress token is invalid")
    if claims.expires_at <= int(now.timestamp()):
        raise IngressTokenError("ingress token is expired")
    return claims


def ingress_secret() -> str:
    """The deploy secret both ends read. Public so the ingress process resolves it at boot: a
    missing secret is a misconfigured deploy, and resolving it only on the first request would bind
    the port, pass the readiness probe, and 500 every viewer."""
    value = os.environ.get(UFO_TOKEN_SECRET_ENV)
    if not value:
        raise RuntimeError(f"{UFO_TOKEN_SECRET_ENV} must be set to mint or verify ingress tokens")
    return value
