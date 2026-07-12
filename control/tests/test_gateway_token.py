from datetime import UTC, datetime, timedelta
from uuid import UUID

from ufo_ext_ufo.surface import verify_token, workspace_claim

from ufo_control.gateway_token import TOKEN_TTL, mint_token

WORKSPACE_ID = UUID("11111111-1111-1111-1111-111111111111")


def test_mint_matches_the_surface_verifier() -> None:
    now = datetime(2026, 7, 7, tzinfo=UTC)
    token = mint_token("s3cret", str(WORKSPACE_ID), "Me@Acme.com", now=now)
    timestamp = int(now.timestamp())
    assert verify_token("s3cret", token, WORKSPACE_ID, timestamp) == "me@acme.com"
    assert workspace_claim("s3cret", token, timestamp) == WORKSPACE_ID


def test_surface_rejects_a_bad_secret() -> None:
    token = mint_token("s3cret", str(WORKSPACE_ID), "e@x.com")
    assert verify_token("other", token, WORKSPACE_ID) is None


def test_surface_rejects_an_expired_token() -> None:
    now = datetime(2026, 7, 7, tzinfo=UTC)
    token = mint_token("s3cret", str(WORKSPACE_ID), "e@x.com", now=now)
    expired = int((now + TOKEN_TTL + timedelta(seconds=1)).timestamp())
    assert verify_token("s3cret", token, WORKSPACE_ID, expired) is None
