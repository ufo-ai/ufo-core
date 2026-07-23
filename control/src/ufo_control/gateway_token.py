"""The member bearer the gateway mints for a hosted member (the client stores it in
~/.ufo/credentials, surfaces verify it). A self-contained HMAC claim over a 30-day expiry, issued
through the one codec in `ufo.bearer` so the signed shape never drifts from the verify half."""

from datetime import datetime, timedelta

from ufo.bearer import mint_token as _mint_token

TOKEN_TTL = timedelta(days=30)
TOKEN_SECRET_ENV = "UFO_TOKEN_SECRET"


def mint_token(secret: str, workspace_id: str, email: str, now: datetime | None = None) -> str:
    return _mint_token(secret, workspace_id, email, TOKEN_TTL, now)
