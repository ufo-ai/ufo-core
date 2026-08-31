import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from ufo.runtime.auth.bearer import UFO_TOKEN_SECRET_ENV, mint_token
from ufo.runtime.auth.surface_token import mint_surface_token, verify_surface_token
from ufo.runtime.auth.token_signing import sign_token


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


BEARER_CONTRACT = (
    Path(__file__).parents[3] / "servers" / "control" / "tests" / "bearer_contract.json"
)


def test_the_rust_contract_vectors_are_this_codec() -> None:
    """The gateway mints in Rust and every surface verifies here, so one drift in either half is a
    token nothing accepts. `servers/control/tests/contract.rs` signs these same vectors and
    asserts the same strings; this end proves the file still describes the codec it was generated
    from, so neither half can move without the other going red.

    The unicode vector is the one that earns its place: `json.dumps` escapes non-ASCII under its
    default `ensure_ascii=True`, and a JSON writer that emits UTF-8 straight through signs a
    different body for the same address.
    """
    vectors = json.loads(BEARER_CONTRACT.read_text())
    assert len(vectors) >= 5
    assert any(not vector["email"].isascii() for vector in vectors)
    for vector in vectors:
        moment = datetime.fromtimestamp(vector["exp"], tz=UTC)
        minted = mint_token(
            vector["secret"], vector["workspace_id"], vector["email"], timedelta(0), moment
        )
        assert minted == vector["token"], vector["email"]
