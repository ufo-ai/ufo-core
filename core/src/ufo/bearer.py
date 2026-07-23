"""The member bearer's codec — one home for the claim every surface checks and every minter issues.

A self-contained HMAC claim, no server-side state. Both minters — the control-plane gateway
(`ufo_control.gateway_token`, which delegates here) for a hosted member, and `ufoctl init` for the
local single-workspace developer — issue through `mint_token`; the verify roles live in extensions
(the `ufo` terminal surface, the `debug` surface) through the `ufo.sdk.bearer` re-export. The codec
is spelled out here once:

    payload_json = {"ws": "<workspace uuid>", "email": "<lower email>", "exp": <unix seconds>}
    body         = base64url(payload_json)            # padding stripped
    token        = body + "." + hex(hmac_sha256(secret, body))

The signing secret is `UFO_TOKEN_SECRET` on every party, and it never crosses the sdk: core reads
it here, so an extension hands over a token and gets claims back without ever holding the key."""

import base64
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

UFO_TOKEN_SECRET_ENV = "UFO_TOKEN_SECRET"
TOKEN_SEPARATOR = "."


def mint_token(
    secret: str, workspace_id: str, email: str, ttl: timedelta, now: datetime | None = None
) -> str:
    """Sign a bearer claiming `workspace_id` for `email`, expiring `ttl` from now — the inverse of
    `verified_claims`. Both minters issue through this one codec so the signed shape never drifts
    from the verify half below."""
    if not secret:
        raise ValueError("token secret is required")
    moment = now or datetime.now(tz=UTC)
    payload = {
        "ws": workspace_id,
        "email": email.strip().lower(),
        "exp": int((moment + ttl).timestamp()),
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    body = base64.urlsafe_b64encode(payload_json.encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}{TOKEN_SEPARATOR}{signature}"


def verified_claims(token: str, now: int | None = None) -> tuple[str, str] | None:
    """The `(ws, email)` a bearer proves — signature (constant-time) and expiry checked, else None.
    Neither field is trusted before the HMAC over this deploy's secret matches, so a forged or
    expired token yields nothing to scope or identify by."""
    secret = _secret()
    moment = int(datetime.now(tz=UTC).timestamp()) if now is None else now
    payload_b64, separator, signature = token.partition(TOKEN_SEPARATOR)
    if not separator or not signature:
        return None
    expected = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    ws, email, exp = payload.get("ws"), payload.get("email"), payload.get("exp")
    if not isinstance(ws, str) or not isinstance(email, str) or not isinstance(exp, int):
        return None
    if exp <= moment:
        return None
    return ws, email


def verify_token(token: str, workspace_id: UUID, now: int | None = None) -> str | None:
    """The lowercased member email a bearer token authenticates for this workspace, or None when its
    signature, expiry, or workspace claim fails. The claim must equal this deploy's, so a token
    minted for another tenant is rejected — the per-tenant check, where the deploy pins one
    workspace; the shared fleet has none pinned and resolves it through `workspace_claim`."""
    claims = verified_claims(token, now)
    if claims is None:
        return None
    ws, email = claims
    if ws != str(workspace_id):
        return None
    return email.lower()


def workspace_claim(token: str, now: int | None = None) -> UUID | None:
    """The workspace a bearer claims, signature- and expiry-verified — the shared fleet's
    per-request scope, resolved from the signed claim itself because one process serves every
    workspace with none pinned to match against. None when verification fails or `ws` is not a
    uuid."""
    claims = verified_claims(token, now)
    if claims is None:
        return None
    try:
        return UUID(claims[0])
    except ValueError:
        return None


def _secret() -> str:
    value = os.environ.get(UFO_TOKEN_SECRET_ENV)
    if not value:
        raise RuntimeError(f"{UFO_TOKEN_SECRET_ENV} must be set to verify member bearers")
    return value


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
