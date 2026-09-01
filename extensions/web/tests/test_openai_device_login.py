"""The device-code sign-in's wire, against a stubbed OpenAI auth server.

The grant is the whole point of the feature and nothing else in the tree exercises its shape, so
these assert the requests OpenAI actually receives — the grant types, the device code, the
token-exchange that turns an approved grant into an API key — rather than the happy path alone.
"""

import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from ufo_ext_web import openai_login
from ufo_ext_web.openai_login import (
    AUTHORIZATION_CODE_GRANT,
    DEVICE_UNAVAILABLE,
    EXCHANGE_REFUSED,
    OpenAiDeviceLogin,
)

from ufo.harness.models.grant import read_grant
from ufo.harness.models.openai import CHATGPT_AUTH_CLAIM

USER_CODE_URL = "https://auth.test/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_URL = "https://auth.test/api/accounts/deviceauth/token"
TOKEN_URL = "https://auth.test/oauth/token"
REDIRECT_URI = "https://auth.test/deviceauth/callback"
CLIENT_ID = "app_test"


def _account_token(account: str = "acct_1") -> str:
    """A token shaped like the one the exchange returns: three dot-joined parts whose middle
    carries the account claim the Codex backend is addressed under."""
    claims = (
        base64.urlsafe_b64encode(
            json.dumps({CHATGPT_AUTH_CLAIM: {"chatgpt_account_id": account}}).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    return f"header.{claims}.signature"


def _wire(monkeypatch: pytest.MonkeyPatch, handler: object, seen: list[tuple]) -> None:
    """Point every client this module opens at `handler`, recording each form it posts. The stand-in
    replaces the name `openai_login` reads rather than anything on httpx itself, so no other caller
    in the process sees it."""

    def transport(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = request.content or b""
            form = request.headers.get("content-type", "").startswith(
                "application/x-www-form-urlencoded"
            )
            seen.append(
                (
                    str(request.url),
                    dict(httpx.QueryParams(body.decode())) if form else json.loads(body or b"{}"),
                )
            )
        return handler(request)  # type: ignore[operator]

    def client(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(transport))

    monkeypatch.setattr(
        openai_login, "httpx", SimpleNamespace(AsyncClient=client, HTTPError=httpx.HTTPError)
    )


def _login() -> OpenAiDeviceLogin:
    return OpenAiDeviceLogin(
        client_id=CLIENT_ID,
        user_code_url=USER_CODE_URL,
        device_token_url=DEVICE_TOKEN_URL,
        token_url=TOKEN_URL,
        redirect_uri=REDIRECT_URI,
    )


async def test_request_code_asks_for_a_user_code_and_carries_openais_own_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAI's device sign-in is its own shape, not RFC 8628: one JSON post naming the client
    buys a `device_auth_id` and the short `user_code` the member types, both of which the poll is
    keyed on. The interval comes back as a string and is read as the number it is."""
    seen: list[tuple] = []
    _wire(
        monkeypatch,
        lambda _request: httpx.Response(
            200,
            json={
                "device_auth_id": "deviceauth_abc",
                "user_code": "KBLB-A8Q5C",
                "interval": "5",
                "expires_at": "2026-08-30T03:27:47+00:00",
            },
        ),
        seen,
    )
    opened = await _login().request_code()
    assert opened is not None
    assert (opened.device_auth_id, opened.user_code) == ("deviceauth_abc", "KBLB-A8Q5C")
    assert opened.interval == 5
    assert opened.verification_uri == openai_login.VERIFICATION_URL
    assert seen == [(USER_CODE_URL, {"client_id": CLIENT_ID})]


async def test_an_unreadable_interval_takes_the_specified_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polling as fast as the loop can is what earns `slow_down`, so a missing interval falls back
    rather than to zero."""
    _wire(
        monkeypatch,
        lambda _r: httpx.Response(200, json={"device_auth_id": "d", "user_code": "u"}),
        [],
    )
    opened = await _login().request_code()
    assert opened is not None
    assert opened.interval == openai_login.DEFAULT_POLL_INTERVAL


@pytest.mark.parametrize(
    "response",
    (
        httpx.Response(403, json={}),
        httpx.Response(404, json={}),
        httpx.Response(400, json={"error": {"code": "deviceauth_authorization_pending"}}),
        httpx.Response(400, json={"error": "slow_down"}),
    ),
)
async def test_the_poll_answers_pending_for_every_shape_that_means_still_deciding(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response
) -> None:
    """Before approval OpenAI refuses the poll four different ways — two bare statuses and two
    error codes, one nested and one plain. Reading any of them as an ending drops the member out of
    a flow they are mid-way through."""
    _wire(monkeypatch, lambda _r: response, [])
    assert (await _login().claim("deviceauth_abc", "KBLB-A8Q5C")).status == "pending"


async def test_an_unrecognised_poll_failure_ends_the_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, lambda _r: httpx.Response(500, json={"error": "boom"}), [])
    claimed = await _login().claim("deviceauth_abc", "KBLB-A8Q5C")
    assert (claimed.status, claimed.refusal) == ("refused", DEVICE_UNAVAILABLE)


async def test_an_approved_grant_is_redeemed_at_the_token_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approval hands back an authorization code with the verifier the server minted for it. The
    redemption is form-encoded where the device legs are JSON, and names the device callback the
    approval was issued against."""
    seen: list[tuple] = []
    token = _account_token()

    def respond(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DEVICE_TOKEN_URL:
            return httpx.Response(
                200, json={"authorization_code": "code-1", "code_verifier": "verify-1"}
            )
        return httpx.Response(
            200, json={"access_token": token, "refresh_token": "r", "expires_in": 3600}
        )

    _wire(monkeypatch, respond, seen)
    claimed = await _login().claim("deviceauth_abc", "KBLB-A8Q5C")

    assert claimed.status == "granted"
    stored = read_grant(claimed.key)
    assert stored is not None
    assert (stored.access, stored.refresh) == (token, "r")
    assert seen == [
        (DEVICE_TOKEN_URL, {"device_auth_id": "deviceauth_abc", "user_code": "KBLB-A8Q5C"}),
        (
            TOKEN_URL,
            {
                "grant_type": AUTHORIZATION_CODE_GRANT,
                "client_id": CLIENT_ID,
                "code": "code-1",
                "code_verifier": "verify-1",
                "redirect_uri": REDIRECT_URI,
            },
        ),
    ]


async def test_a_token_carrying_no_account_never_reaches_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The token is spent against the Codex backend under the account its own claims name, so one
    that names none is refused here rather than failing on every turn."""

    def respond(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DEVICE_TOKEN_URL:
            return httpx.Response(
                200, json={"authorization_code": "code-1", "code_verifier": "verify-1"}
            )
        return httpx.Response(200, json={"access_token": "not.a.jwt"})

    _wire(monkeypatch, respond, [])
    claimed = await _login().claim("deviceauth_abc", "KBLB-A8Q5C")
    assert (claimed.status, claimed.refusal) == ("refused", EXCHANGE_REFUSED)


async def test_a_grant_answered_without_a_refresh_token_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An hour of work and then nothing is worse than refusing the sign-in: a member whose account
    cannot be refreshed would read as connected long after it stopped answering."""

    def respond(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DEVICE_TOKEN_URL:
            return httpx.Response(
                200, json={"authorization_code": "code-1", "code_verifier": "verify-1"}
            )
        return httpx.Response(200, json={"access_token": _account_token(), "expires_in": 3600})

    _wire(monkeypatch, respond, [])
    claimed = await _login().claim("deviceauth_abc", "KBLB-A8Q5C")

    assert claimed.status == "refused"
