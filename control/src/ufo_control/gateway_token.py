"""The member bearer the client stores in ~/.ufo/credentials and surfaces verify.

A self-contained HMAC claim, no server-side state. The codec is a fixed contract with the verify
half in core (`ufo/bearer.py`, re-exported through `ufo.sdk.bearer` for the `ufo` and `debug`
surfaces), so it is spelled out exactly:

    payload_json = {"ws": "<workspace uuid>", "email": "<lower email>", "exp": <unix seconds>}
    body         = base64url(payload_json)            # padding stripped
    token        = body + "." + hex(hmac_sha256(secret, body))

`mint_token` produces it; `ufo.bearer` owns verification."""

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
