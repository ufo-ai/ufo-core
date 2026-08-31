"""The Anthropic authorization leg's wire, against a stubbed token endpoint.

Anthropic validates the authorize query before it draws a consent screen, so the parameters are
asserted here rather than discovered as "Invalid request format" in a member's browser.
"""

import base64
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
from ufo_ext_web import anthropic_login
from ufo_ext_web.anthropic_login import (
    AUTHORIZE_URL,
    OAUTH_SCOPE,
    REDIRECT_URI,
    AnthropicCodeLogin,
)

CLIENT_ID = "client-1"
TOKEN_URL = "https://claude.test/v1/oauth/token"


def _login() -> AnthropicCodeLogin:
    return AnthropicCodeLogin(client_id=CLIENT_ID, token_url=TOKEN_URL)


def _wire(monkeypatch: pytest.MonkeyPatch, handler: object, seen: list[dict]) -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content or b"{}"))
        return handler(request)  # type: ignore[operator]

    def client(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(transport))

    monkeypatch.setattr(
        anthropic_login, "httpx", SimpleNamespace(AsyncClient=client, HTTPError=httpx.HTTPError)
    )


def test_the_authorize_query_carries_what_anthropic_accepts() -> None:
    """`state` is the verifier itself. Anthropic refuses an independent, shorter state as an
    invalid request format — before the member ever sees a consent screen — so this pins the one
    parameter whose shape is not free."""
    pending = _login().authorize()
    query = dict(parse_qsl(urlsplit(pending.url).query))

    assert pending.url.startswith(f"{AUTHORIZE_URL}?")
    assert query["state"] == pending.cookie
    assert len(query["state"]) == 43
    assert (
        query["code_challenge"]
        == base64.urlsafe_b64encode(hashlib.sha256(pending.cookie.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert query["code_challenge_method"] == "S256"
    assert query["code"] == "true"
    assert query["response_type"] == "code"
    assert query["client_id"] == CLIENT_ID
    assert query["redirect_uri"] == REDIRECT_URI
    assert query["scope"] == OAUTH_SCOPE


def test_two_authorizations_never_share_a_verifier() -> None:
    """Each door draw mints its own, so one member's paste can never redeem another's code."""
    assert _login().authorize().cookie != _login().authorize().cookie


@pytest.mark.parametrize(
    "pasted",
    (
        "code-1",
        "code-1#VERIFIER",
        "https://platform.claude.com/oauth/code/callback?code=code-1&state=VERIFIER",
    ),
)
async def test_every_form_anthropic_puts_in_front_of_the_member_redeems(
    monkeypatch: pytest.MonkeyPatch, pasted: str
) -> None:
    """The page shows `code#state`, the address bar shows the whole URL, and a member may copy just
    the code. All three are what they are handed, so all three redeem."""
    seen: list[dict] = []
    _wire(
        monkeypatch,
        lambda _r: httpx.Response(
            200,
            json={"access_token": "sk-ant-oat01-x", "refresh_token": "r-1", "expires_in": 3600},
        ),
        seen,
    )
    pending = _login().authorize()

    token = await _login().claim(pasted.replace("VERIFIER", pending.cookie), pending.cookie)

    assert token is not None
    assert (token.access, token.refresh) == ("sk-ant-oat01-x", "r-1")
    assert seen == [
        {
            "grant_type": "authorization_code",
            "code": "code-1",
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "code_verifier": pending.cookie,
            "state": pending.cookie,
        }
    ]


async def test_a_state_from_another_browser_never_redeems(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pasted state is checked against the verifier this browser holds, so a code lifted from
    somewhere else is refused before it reaches the token endpoint."""
    seen: list[dict] = []
    _wire(
        monkeypatch, lambda _r: httpx.Response(200, json={"access_token": "sk-ant-oat01-x"}), seen
    )

    assert await _login().claim("code-1#someone-elses-state", _login().authorize().cookie) is None
    assert seen == []


async def test_a_refused_exchange_yields_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, lambda _r: httpx.Response(400, json={"error": "invalid_grant"}), [])
    pending = _login().authorize()
    assert await _login().claim("code-1", pending.cookie) is None
