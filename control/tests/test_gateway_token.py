"""The member bearer codec is a fixed contract with the `ufo` surface, so the mint is asserted
against an inline re-implementation of the surface's verifier (not the module's own), then the
module's `verify_token` roundtrip is exercised for signature and expiry."""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest

from ufo_control.gateway_token import TOKEN_TTL, mint_token, verify_token


def _surface_verify(token: str, secret: str, workspace_id: str) -> str | None:
    """The `ufo` surface's verification, recomputed inline: split on `.`, recompute the HMAC over
    the base64url body, constant-time compare, decode, and check the workspace claim."""
    payload_b64, separator, signature = token.partition(".")
    if not separator or not signature:
        return None
    expected = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
    if payload["ws"] != workspace_id:
        return None
    email = payload["email"]
    assert isinstance(email, str)
    return email.lower()


def test_mint_matches_the_surface_verifier() -> None:
    now = datetime(2026, 7, 7, tzinfo=UTC)
    workspace_id = "11111111-1111-1111-1111-111111111111"
    token = mint_token("s3cret", workspace_id, "Me@Acme.com", now=now)
    assert _surface_verify(token, "s3cret", workspace_id) == "me@acme.com"
    assert _surface_verify(token, "s3cret", "22222222-2222-2222-2222-222222222222") is None


def test_verify_roundtrips() -> None:
    token = mint_token("s3cret", "ws-1", "e@x.com")
    assert verify_token(token, "s3cret")["email"] == "e@x.com"


def test_verify_rejects_a_bad_secret() -> None:
    token = mint_token("s3cret", "ws-1", "e@x.com")
    with pytest.raises(ValueError, match="signature"):
        verify_token(token, "other")


def test_verify_rejects_an_expired_token() -> None:
    now = datetime(2026, 7, 7, tzinfo=UTC)
    token = mint_token("s3cret", "ws-1", "e@x.com", now=now)
    with pytest.raises(ValueError, match="expired"):
        verify_token(token, "s3cret", now=now + TOKEN_TTL + timedelta(seconds=1))
