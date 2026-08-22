import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ufo.auth.bearer import UFO_TOKEN_SECRET_ENV, mint_token
from ufo.auth.token_signing import sign_token
from ufo.sandbox.ingress_token import (
    INGRESS_SESSION_KIND,
    INGRESS_VIEW_KIND,
    IngressClaims,
    IngressTokenError,
    ingress_secret,
    mint_ingress_token,
    verify_ingress_token,
)


def _claims(exp_offset: int = 900) -> IngressClaims:
    return IngressClaims(
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        port=8000,
        expires_at=int(datetime.now(UTC).timestamp()) + exp_offset,
    )


def test_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    claims = _claims()
    for kind in (INGRESS_VIEW_KIND, INGRESS_SESSION_KIND):
        assert verify_ingress_token(mint_ingress_token(claims, kind), datetime.now(UTC), kind) == (
            claims
        )


def test_one_kind_never_passes_for_the_other(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two hops of a visit are two kinds, signed as such. Without this, one `verify` served
    both: the session cookie the ingress mints would itself open the view path and mint a successor,
    so a single leaked link renewed forever, and a view token pasted into the cookie jar would serve
    the site with no handshake at all."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    now = datetime.now(UTC)
    claims = _claims()
    view = mint_ingress_token(claims, INGRESS_VIEW_KIND)
    session = mint_ingress_token(claims, INGRESS_SESSION_KIND)
    assert view != session
    with pytest.raises(IngressTokenError):
        verify_ingress_token(view, now, INGRESS_SESSION_KIND)
    with pytest.raises(IngressTokenError):
        verify_ingress_token(session, now, INGRESS_VIEW_KIND)


def test_rejects_tampered_expired_and_foreign_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    now = datetime.now(UTC)
    with pytest.raises(IngressTokenError):
        verify_ingress_token(
            mint_ingress_token(_claims(), INGRESS_VIEW_KIND) + "x", now, INGRESS_VIEW_KIND
        )
    with pytest.raises(IngressTokenError, match="expired"):
        verify_ingress_token(
            mint_ingress_token(_claims(exp_offset=-1), INGRESS_VIEW_KIND), now, INGRESS_VIEW_KIND
        )
    bearer = mint_token("s3cret", str(uuid4()), "a@b.co", timedelta(hours=1))
    with pytest.raises(IngressTokenError):
        verify_ingress_token(bearer, now, INGRESS_VIEW_KIND)
    unkinded = sign_token(b"s3cret", json.dumps({"ws": str(uuid4())}).encode())
    with pytest.raises(IngressTokenError):
        verify_ingress_token(unkinded, now, INGRESS_VIEW_KIND)
    wrong_kind = sign_token(
        b"s3cret",
        json.dumps(
            {
                "kind": "artifact",
                "ws": str(uuid4()),
                "conversation": str(uuid4()),
                "port": 8000,
                "exp": int(datetime.now(UTC).timestamp()) + 900,
            }
        ).encode(),
    )
    with pytest.raises(IngressTokenError):
        verify_ingress_token(wrong_kind, now, INGRESS_VIEW_KIND)


def test_rejects_invalid_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    now = datetime.now(UTC)
    for bad_port in [0, 65536, -1]:
        claims = IngressClaims(
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            port=bad_port,
            expires_at=int(datetime.now(UTC).timestamp()) + 900,
        )
        token = mint_ingress_token(claims, INGRESS_VIEW_KIND)
        with pytest.raises(IngressTokenError):
            verify_ingress_token(token, now, INGRESS_VIEW_KIND)


def test_fails_loud_without_the_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both ends read the one accessor, so the ingress process resolving it at boot is the same
    check every mint and verify makes — a deploy missing the secret dies on its feet rather than
    binding the port, passing its readiness probe, and 500ing every viewer."""
    monkeypatch.delenv(UFO_TOKEN_SECRET_ENV, raising=False)
    with pytest.raises(RuntimeError, match=UFO_TOKEN_SECRET_ENV):
        mint_ingress_token(_claims(), INGRESS_VIEW_KIND)
    with pytest.raises(RuntimeError, match=UFO_TOKEN_SECRET_ENV):
        ingress_secret()
