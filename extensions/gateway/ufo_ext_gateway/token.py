"""The member bearer the client stores in ~/.ufo/credentials and the `ufo` surface verifies.

A self-contained HMAC claim, no server-side state. The codec is a fixed cross-extension contract
with the `ufo` surface (unit E), so it is spelled out exactly:

    payload_json = {"ws": "<workspace uuid>", "email": "<lower email>", "exp": <unix seconds>}
    body         = base64url(payload_json)            # padding stripped
    token        = body + "." + hex(hmac_sha256(secret, body))

`mint_token` produces it; `verify_token` is the roundtrip the surface performs — recompute the HMAC
over the received body, constant-time compare, then decode and enforce the expiry."""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

TOKEN_TTL = timedelta(days=30)
TOKEN_SECRET_ENV = "UFO_TOKEN_SECRET"


def mint_token(secret: str, workspace_id: str, email: str, now: datetime | None = None) -> str:
    if not secret:
        raise ValueError("token secret is required")
    moment = now or datetime.now(UTC)
    payload = {
        "ws": workspace_id,
        "email": email.strip().lower(),
        "exp": int((moment + TOKEN_TTL).timestamp()),
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    body = base64.urlsafe_b64encode(payload_json.encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def verify_token(token: str, secret: str, now: datetime | None = None) -> dict[str, object]:
    if not secret:
        raise ValueError("token secret is required")
    body, _, signature = token.partition(".")
    if not signature:
        raise ValueError("token shape is invalid")
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("token signature is invalid")
    padding = "=" * (-len(body) % 4)
    payload = json.loads(base64.urlsafe_b64decode(body + padding))
    if not isinstance(payload, dict):
        raise ValueError("token payload is invalid")
    moment = now or datetime.now(UTC)
    if int(payload["exp"]) <= int(moment.timestamp()):
        raise ValueError("token is expired")
    return payload
