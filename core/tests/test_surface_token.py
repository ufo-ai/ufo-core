import json
from datetime import timedelta
from uuid import uuid4

import pytest

from ufo.bearer import UFO_TOKEN_SECRET_ENV, mint_token
from ufo.surface_token import mint_surface_token, verify_surface_token
from ufo.token_signing import sign_token


def test_roundtrip_returns_the_minted_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    payload = {"ws": str(uuid4()), "site": "landing"}
    assert verify_surface_token("sites", mint_surface_token("sites", payload)) == payload


def test_a_token_never_verifies_at_another_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    """The namespace is the point: a link minted for one surface must yield nothing at another's
    route, so a route can trust its own claims without re-deriving where they came from."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    token = mint_surface_token("sites", {"ws": str(uuid4())})
    assert verify_surface_token("web", token) is None
    assert verify_surface_token("sites", token) is not None


def test_rejects_tampered_foreign_and_malformed_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    bearer = mint_token("s3cret", str(uuid4()), "a@b.co", timedelta(hours=1))
    unnamespaced = sign_token(b"s3cret", json.dumps({"ws": "w"}).encode())
    non_string = sign_token(b"s3cret", json.dumps({"surface": "sites", "port": 8000}).encode())
    not_an_object = sign_token(b"s3cret", json.dumps(["sites"]).encode())
    foreign_secret = sign_token(b"stolen", json.dumps({"surface": "sites"}).encode())
    for token in (
        mint_surface_token("sites", {"ws": "w"}) + "x",
        "",
        bearer,
        unnamespaced,
        non_string,
        not_an_object,
        foreign_secret,
    ):
        assert verify_surface_token("sites", token) is None


def test_refuses_to_mint_over_the_namespace_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    with pytest.raises(ValueError, match="reserved"):
        mint_surface_token("sites", {"surface": "web"})
    with pytest.raises(ValueError, match="must name its surface"):
        mint_surface_token("", {"ws": "w"})


def test_fails_loud_without_the_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(UFO_TOKEN_SECRET_ENV, raising=False)
    with pytest.raises(RuntimeError, match=UFO_TOKEN_SECRET_ENV):
        mint_surface_token("sites", {"ws": "w"})
    with pytest.raises(RuntimeError, match=UFO_TOKEN_SECRET_ENV):
        verify_surface_token("sites", "anything")
