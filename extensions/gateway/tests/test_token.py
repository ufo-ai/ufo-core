"""The member bearer codec is a fixed cross-extension contract with the `ufo` surface, so the mint
is asserted against an inline re-implementation of the codec (not the module's own verifier), then
the module's `verify_token` roundtrip is exercised for signature and expiry."""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from ufo_ext_gateway.token import TOKEN_TTL, mint_token, verify_token


def _inline_verify(token: str, secret: str) -> dict[str, object]:
    body, _, signature = token.partition(".")
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    assert hmac.compare_digest(signature, expected)
    padding = "=" * (-len(body) % 4)
    return json.loads(base64.urlsafe_b64decode(body + padding))


def test_mint_matches_the_codec() -> None:
    now = datetime(2026, 7, 7, tzinfo=UTC)
    token = mint_token("s3cret", "11111111-1111-1111-1111-111111111111", "Me@Acme.com", now=now)
    payload = _inline_verify(token, "s3cret")
    assert payload == {
        "ws": "11111111-1111-1111-1111-111111111111",
        "email": "me@acme.com",
        "exp": int((now + TOKEN_TTL).timestamp()),
    }


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
