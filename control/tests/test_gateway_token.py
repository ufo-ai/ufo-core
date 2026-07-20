from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from ufo.bearer import verify_token, workspace_claim

from ufo_control.gateway_token import TOKEN_TTL, mint_token

WORKSPACE_ID = UUID("11111111-1111-1111-1111-111111111111")


def test_mint_matches_the_core_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", "s3cret")
    now = datetime(2026, 7, 7, tzinfo=UTC)
    token = mint_token("s3cret", str(WORKSPACE_ID), "Me@Acme.com", now=now)
    timestamp = int(now.timestamp())
    assert verify_token(token, WORKSPACE_ID, timestamp) == "me@acme.com"
    assert workspace_claim(token, timestamp) == WORKSPACE_ID


def test_verifier_rejects_a_bad_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", "other")
    token = mint_token("s3cret", str(WORKSPACE_ID), "e@x.com")
    assert verify_token(token, WORKSPACE_ID) is None


def test_verifier_rejects_an_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", "s3cret")
    now = datetime(2026, 7, 7, tzinfo=UTC)
    token = mint_token("s3cret", str(WORKSPACE_ID), "e@x.com", now=now)
    expired = int((now + TOKEN_TTL + timedelta(seconds=1)).timestamp())
    assert verify_token(token, WORKSPACE_ID, expired) is None


def test_verifier_fails_loud_without_the_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UFO_TOKEN_SECRET", raising=False)
    token = mint_token("s3cret", str(WORKSPACE_ID), "e@x.com")
    with pytest.raises(RuntimeError, match="UFO_TOKEN_SECRET"):
        verify_token(token, WORKSPACE_ID)
