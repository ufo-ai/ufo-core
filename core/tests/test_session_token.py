"""The shared-fleet session token: a mint→verify round-trip agrees, and every tampered, stale, or
malformed token is refused. The surface trusts a verified token's workspace claim before any
RLS-scoped read, so a token that survives verification must be exactly one the deploy secret signed.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ufo.session_token import (
    SESSION_TOKEN_TTL_SECONDS,
    SessionTokenError,
    mint_session_token,
    verify_session_token,
)

SECRET = "deploy-signing-secret"
NOW = datetime(2026, 7, 8, tzinfo=UTC)


def _mint(
    secret: str = SECRET, *, ttl: int = SESSION_TOKEN_TTL_SECONDS
) -> tuple[str, object, object]:
    member_id, workspace_id = uuid4(), uuid4()
    expires_at = int((NOW + timedelta(seconds=ttl)).timestamp())
    return mint_session_token(secret, member_id, workspace_id, expires_at), member_id, workspace_id


def test_round_trip_recovers_the_member_and_workspace_claims() -> None:
    token, member_id, workspace_id = _mint()
    claims = verify_session_token(token, SECRET, NOW)
    assert claims.member_id == member_id
    assert claims.workspace_id == workspace_id


def test_a_token_signed_with_another_secret_is_refused() -> None:
    token, _, _ = _mint()
    with pytest.raises(SessionTokenError, match="signature"):
        verify_session_token(token, "a-different-secret", NOW)


def test_a_tampered_workspace_claim_breaks_the_signature() -> None:
    token, _, _ = _mint()
    _, _, signature = token.partition(".")
    forged = mint_session_token(SECRET, uuid4(), uuid4(), 9999999999)
    forged_body = forged.partition(".")[0]
    with pytest.raises(SessionTokenError, match="signature"):
        verify_session_token(f"{forged_body}.{signature}", SECRET, NOW)


def test_an_expired_token_is_refused() -> None:
    token, _, _ = _mint(ttl=60)
    later = NOW + timedelta(seconds=61)
    with pytest.raises(SessionTokenError, match="expired"):
        verify_session_token(token, SECRET, later)


def test_a_malformed_token_is_refused() -> None:
    with pytest.raises(SessionTokenError, match="malformed"):
        verify_session_token("no-dot-no-signature", SECRET, NOW)


def test_an_empty_secret_refuses_to_mint_or_verify() -> None:
    with pytest.raises(SessionTokenError, match="not configured"):
        mint_session_token("", uuid4(), uuid4(), 9999999999)
    token, _, _ = _mint()
    with pytest.raises(SessionTokenError, match="not configured"):
        verify_session_token(token, "", NOW)
