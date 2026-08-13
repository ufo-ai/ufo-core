"""The browser's `Continue with Google` hop: the start path mints and binds the session and 302s to
WorkOS with `provider=GoogleOAuth`, and the callback is the only thing that writes the verified
claim behind it. The walk runs over the real `onboard_claim` table with WorkOS behind
`FakeVerifier`, so what is asserted is the redirect, the carry, the cookie the session is bound by,
and the row — a Google account whose email is a personal address is refused by the same work-email
policy a typed address is."""

import asyncio
import base64
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import asyncpg
import pytest
from fake_workos import FakeVerifier
from fastapi.testclient import TestClient

import ufo_control.gateway as gateway
from ufo_control.gateway import (
    INVITE_REQUIRED_ENV,
    TOKEN_SECRET_ENV,
    WORKSPACE_BASE_URL_ENV,
    gateway_app,
)
from ufo_control.gateway_email import (
    AWS_ROLE_ARN_ENV,
    AWS_WEB_IDENTITY_TOKEN_FILE_ENV,
    SES_SENDER_ENV,
)
from ufo_control.gateway_invite import InviteCodes
from ufo_control.gateway_shared import SERVE_DSN_ENV
from ufo_control.gateway_web import ONBOARD_SESSION_COOKIE
from ufo_control.gateway_workos import (
    AUTH_CALLBACK_PATH,
    AUTH_CONSOLE_PATH,
    AUTH_START_PATH,
    SIGN_IN_FAILED,
    WORKOS_API_KEY_ENV,
    WORKOS_CLIENT_ID_ENV,
    WORKOS_MODE_ENV,
    WORKOS_REDIRECT_URI_ENV,
    AuthCarry,
    unpack_state,
)

TOKEN_SECRET = "auth-token-secret"
WORKSPACE_URL = "https://app.testing.flyingobject.ai"
CONVERSATION = "123e4567-e89b-12d3-a456-426614174000"
ARTIFACT = "/artifacts/abc/report.html"
GOOD_CODE = "google-code"
OTHER_CODE = "google-code-2"
PLANTED_SESSION = "attacker-chosen-session"


def _configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, gateway_postgres: str) -> None:
    token_file = tmp_path / "web-identity"
    token_file.write_text("token")
    monkeypatch.setenv(
        SERVE_DSN_ENV, gateway_postgres.replace("postgresql://", "postgresql+asyncpg://")
    )
    monkeypatch.setenv(TOKEN_SECRET_ENV, TOKEN_SECRET)
    monkeypatch.setenv(WORKSPACE_BASE_URL_ENV, WORKSPACE_URL)
    monkeypatch.setenv(SES_SENDER_ENV, "no-reply@flyingobject.ai")
    monkeypatch.setenv(AWS_ROLE_ARN_ENV, "arn:aws:iam::123456789012:role/gateway-ses")
    monkeypatch.setenv(AWS_WEB_IDENTITY_TOKEN_FILE_ENV, str(token_file))
    monkeypatch.setenv(WORKOS_API_KEY_ENV, "sk_test_gateway")
    monkeypatch.setenv(WORKOS_CLIENT_ID_ENV, "client_01GATEWAY")
    monkeypatch.setenv(WORKOS_REDIRECT_URI_ENV, f"{WORKSPACE_URL}{AUTH_CALLBACK_PATH}")


def _verify_through(monkeypatch: pytest.MonkeyPatch, **exchanges: str) -> FakeVerifier:
    verifier = FakeVerifier(exchanges=dict(exchanges))
    monkeypatch.setattr(gateway, "workos_verifier_from_env", lambda: verifier)
    return verifier


def _client() -> TestClient:
    """The redirects are the subject, so they are read rather than followed. The base URL is https
    so the jar carries the `Secure` session cookie between requests the way a browser does."""
    return TestClient(gateway_app(), base_url="https://testserver", follow_redirects=False)


def _query(location: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(location).query)


def _grant(dsn: str, object_number: int, email: str) -> None:
    async def _mint() -> None:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
        try:
            await InviteCodes(pool=pool).mint(object_number, email)
        finally:
            await pool.close()

    asyncio.run(_mint())


def _web_claim(dsn: str, session: str) -> asyncpg.Record | None:
    async def _read() -> asyncpg.Record | None:
        connection = await asyncpg.connect(dsn)
        try:
            return await connection.fetchrow(
                "select email, verified_at from ufo_control.onboard_claim"
                " where surface = 'web' and surface_ref = $1",
                session,
            )
        finally:
            await connection.close()

    return asyncio.run(_read())


def test_start_mints_the_session_it_binds_and_carries_the_click(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The session in the state is the one the response binds as the cookie, and the cookie is
    host-only, `HttpOnly`, `Secure`, and `SameSite=lax` — lax is load-bearing, since the Google hop
    returns the member by a cross-site redirect a strict cookie would not ride."""
    _configure(monkeypatch, tmp_path, gateway_postgres)
    verifier = _verify_through(monkeypatch)
    with _client() as client:
        response = client.get(AUTH_START_PATH, params={"c": CONVERSATION, "a": ARTIFACT})
    assert response.status_code == 302
    location = response.headers["location"]
    packed = _query(location)["state"][0]
    assert location == verifier.authorization_url(packed)
    bound = response.cookies[ONBOARD_SESSION_COOKIE]
    assert unpack_state(packed, TOKEN_SECRET) == AuthCarry(
        session=bound, conversation=CONVERSATION, artifact=ARTIFACT
    )
    crumb = response.headers["set-cookie"]
    assert crumb.startswith(f"{ONBOARD_SESSION_COOKIE}={bound};")
    assert crumb.startswith("__Host-")
    assert "; HttpOnly" in crumb
    assert "; Secure" in crumb
    assert "; SameSite=lax" in crumb
    assert "; Path=/" in crumb
    assert "Domain=" not in crumb


def test_start_drops_a_click_that_is_not_its_shape(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path, gateway_postgres)
    _verify_through(monkeypatch)
    with _client() as client:
        response = client.get(
            AUTH_START_PATH, params={"c": "../../etc", "a": "https://evil.example/"}
        )
    carry = unpack_state(_query(response.headers["location"])["state"][0], TOKEN_SECRET)
    assert carry == AuthCarry(
        session=response.cookies[ONBOARD_SESSION_COOKIE], conversation=None, artifact=None
    )


def test_start_never_signs_in_under_a_session_the_query_names(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A caller who hands the start path a session gets a minted one anyway: the query cannot decide
    which id a verified email is written under."""
    _configure(monkeypatch, tmp_path, gateway_postgres)
    _verify_through(monkeypatch)
    with _client() as client:
        response = client.get(AUTH_START_PATH, params={"session": PLANTED_SESSION})
    carry = unpack_state(_query(response.headers["location"])["state"][0], TOKEN_SECRET)
    assert carry.session != PLANTED_SESSION
    assert carry.session == response.cookies[ONBOARD_SESSION_COOKIE]
    assert PLANTED_SESSION not in response.headers["set-cookie"]


def test_the_callback_writes_a_verified_claim_and_returns_the_carry(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path, gateway_postgres)
    email = "member@googauthco.io"
    verifier = _verify_through(monkeypatch, **{GOOD_CODE: email})
    with _client() as client:
        start = client.get(AUTH_START_PATH, params={"c": CONVERSATION, "a": ARTIFACT})
        packed = _query(start.headers["location"])["state"][0]
        session = start.cookies[ONBOARD_SESSION_COOKIE]
        response = client.get(AUTH_CALLBACK_PATH, params={"code": GOOD_CODE, "state": packed})
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/login?")
    assert _query(location) == {"c": [CONVERSATION], "a": [ARTIFACT]}
    assert session not in location
    claim = _web_claim(gateway_postgres, session)
    assert claim is not None
    assert claim["email"] == email
    assert claim["verified_at"] is not None
    assert verifier.begun == []


def test_the_callback_refuses_an_email_the_policy_denies(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Google proves the account's address; whether it is a work address is still ours to answer, so
    a personal `@gmail.com` Google account is refused by the same policy a typed address is, and the
    refusal rides back as the sentence the page prints."""
    _configure(monkeypatch, tmp_path, gateway_postgres)
    _verify_through(monkeypatch, **{GOOD_CODE: "someone@gmail.com"})
    with _client() as client:
        start = client.get(AUTH_START_PATH)
        packed = _query(start.headers["location"])["state"][0]
        session = start.cookies[ONBOARD_SESSION_COOKIE]
        response = client.get(AUTH_CALLBACK_PATH, params={"code": GOOD_CODE, "state": packed})
    assert response.status_code == 303
    assert _query(response.headers["location"]) == {
        "error": ["gmail.com is not a work email domain."]
    }
    assert _web_claim(gateway_postgres, session) is None


def test_the_callback_refuses_a_code_workos_does_not_know(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path, gateway_postgres)
    _verify_through(monkeypatch)
    with _client() as client:
        start = client.get(AUTH_START_PATH)
        packed = _query(start.headers["location"])["state"][0]
        session = start.cookies[ONBOARD_SESSION_COOKIE]
        unknown = client.get(AUTH_CALLBACK_PATH, params={"code": "not-a-code", "state": packed})
        missing = client.get(AUTH_CALLBACK_PATH, params={"state": packed})
    for response in (unknown, missing):
        assert response.status_code == 303
        assert _query(response.headers["location"])["error"] == [SIGN_IN_FAILED]
    assert _web_claim(gateway_postgres, session) is None


def test_the_callback_refuses_a_state_it_did_not_pack(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path, gateway_postgres)
    _verify_through(monkeypatch, **{GOOD_CODE: "member@garbagestate.io"})
    with _client() as client:
        response = client.get(
            AUTH_CALLBACK_PATH, params={"code": GOOD_CODE, "state": "not-base64-json"}
        )
    assert response.status_code == 400
    assert response.text == "The sign-in link is not valid."


def test_the_callback_refuses_a_state_and_cookie_the_caller_chose(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A caller who writes the state and plants the cookie to match it still names nothing: the
    state carries no signature of this gateway's, so the exchange never runs and no claim is written
    under the session they chose."""
    _configure(monkeypatch, tmp_path, gateway_postgres)
    _verify_through(monkeypatch, **{GOOD_CODE: "member@forgedstate.io"})
    payload = json.dumps({"s": PLANTED_SESSION, "c": None, "a": None}).encode()
    forged = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    with _client() as client:
        client.cookies.set(ONBOARD_SESSION_COOKIE, PLANTED_SESSION)
        response = client.get(AUTH_CALLBACK_PATH, params={"code": GOOD_CODE, "state": forged})
    assert response.status_code == 400
    assert response.text == "The sign-in link is not valid."
    assert _web_claim(gateway_postgres, PLANTED_SESSION) is None


def test_the_callback_verifies_nothing_in_a_browser_that_did_not_start(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The state proves nothing on its own: the return is only honored where the session it names is
    the cookie that browser holds, so a callback replayed anywhere else writes no claim."""
    _configure(monkeypatch, tmp_path, gateway_postgres)
    _verify_through(monkeypatch, **{GOOD_CODE: "member@replayco.io"})
    with _client() as starting:
        start = starting.get(AUTH_START_PATH)
        packed = _query(start.headers["location"])["state"][0]
        session = start.cookies[ONBOARD_SESSION_COOKIE]
    with _client() as elsewhere:
        response = elsewhere.get(AUTH_CALLBACK_PATH, params={"code": GOOD_CODE, "state": packed})
    assert response.status_code == 303
    assert _query(response.headers["location"])["error"] == [SIGN_IN_FAILED]
    assert _web_claim(gateway_postgres, session) is None


def test_a_session_an_attacker_chose_never_carries_the_members_bearer(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The fixation the flow is closed against: the attacker picks a session, gets the member to
    open the start path with it, and then posts as that session from their own machine. The member
    signs in under the session the gateway minted and bound to their browser, so the planted id keys
    no claim, and the attacker's post — a cookie they set to the planted id — finds no claim and is
    answered with the email prompt, never the member's token."""
    _configure(monkeypatch, tmp_path, gateway_postgres)
    monkeypatch.setenv(INVITE_REQUIRED_ENV, "false")
    email = "member@fixationco.io"
    _verify_through(monkeypatch, **{GOOD_CODE: email})
    with _client() as member:
        start = member.get(AUTH_START_PATH, params={"session": PLANTED_SESSION})
        packed = _query(start.headers["location"])["state"][0]
        landed = member.get(AUTH_CALLBACK_PATH, params={"code": GOOD_CODE, "state": packed})
        assert "error" not in _query(landed.headers["location"])
        signed_in = member.post("/v1/onboard/web", content="").json()["directives"]
    assert [entry["verb"] for entry in signed_in if entry["verb"] == "token"] == ["token"]
    with _client() as attacker:
        attacker.cookies.set(ONBOARD_SESSION_COOKIE, PLANTED_SESSION)
        posted = attacker.post("/v1/onboard/web", content="").json()["directives"]
    assert [entry["verb"] for entry in posted if entry["verb"] == "token"] == []
    assert [entry["fields"][0] for entry in posted if entry["verb"] == "ask"] == [
        "Enter your work email:"
    ]
    assert _web_claim(gateway_postgres, PLANTED_SESSION) is None


def test_a_repeated_callback_keeps_the_first_email(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The Google hop returning twice for one session must not reopen the claim under a second
    address: the session already holds a live one, so the second return resolves it."""
    _configure(monkeypatch, tmp_path, gateway_postgres)
    first = "first@repeatco.io"
    _verify_through(monkeypatch, **{GOOD_CODE: first, OTHER_CODE: "second@repeatco.io"})
    with _client() as client:
        start = client.get(AUTH_START_PATH)
        packed = _query(start.headers["location"])["state"][0]
        session = start.cookies[ONBOARD_SESSION_COOKIE]
        client.get(AUTH_CALLBACK_PATH, params={"code": GOOD_CODE, "state": packed})
        repeated = client.get(AUTH_CALLBACK_PATH, params={"code": OTHER_CODE, "state": packed})
    assert repeated.status_code == 303
    assert "error" not in _query(repeated.headers["location"])
    claim = _web_claim(gateway_postgres, session)
    assert claim is not None and claim["email"] == first


def test_the_page_resumes_the_machine_in_workspace_resolution(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The whole point of the claim the callback wrote: the next POST walks past sign-in, so the
    member reads the signed-in card rather than the hop they just came back from."""
    _configure(monkeypatch, tmp_path, gateway_postgres)
    monkeypatch.setenv(INVITE_REQUIRED_ENV, "false")
    email = "founder@resumeco.io"
    _verify_through(monkeypatch, **{GOOD_CODE: email})
    with _client() as client:
        start = client.get(AUTH_START_PATH)
        packed = _query(start.headers["location"])["state"][0]
        client.get(AUTH_CALLBACK_PATH, params={"code": GOOD_CODE, "state": packed})
        resumed = client.post("/v1/onboard/web", content="")
    directives = resumed.json()["directives"]
    assert [entry["verb"] for entry in directives if entry["verb"] == "auth"] == []
    assert [entry["fields"][0] for entry in directives if entry["verb"] == "say"] == [
        f"Signed in: {email}"
    ]
    assert [entry["verb"] for entry in directives if entry["verb"] == "token"] == ["token"]


def test_console_mode_signs_in_through_the_local_page_without_workos(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Local dev with no WorkOS keys: the page asks for the email inline as everywhere, and the
    `Continue with Google` hop lands on the gateway's own email form standing in for WorkOS, the
    address the dev types returns as the code, and the walk past it is the one a real return takes —
    claim written, workspace resolved, token minted."""
    monkeypatch.setenv(
        SERVE_DSN_ENV, gateway_postgres.replace("postgresql://", "postgresql+asyncpg://")
    )
    monkeypatch.setenv(TOKEN_SECRET_ENV, TOKEN_SECRET)
    monkeypatch.setenv(WORKSPACE_BASE_URL_ENV, WORKSPACE_URL)
    monkeypatch.setenv(INVITE_REQUIRED_ENV, "false")
    monkeypatch.setenv(WORKOS_MODE_ENV, "console")
    for name in (WORKOS_API_KEY_ENV, WORKOS_CLIENT_ID_ENV, WORKOS_REDIRECT_URI_ENV):
        monkeypatch.delenv(name, raising=False)
    email = "founder@localdev.io"
    with _client() as client:
        opening = client.post("/v1/onboard/web", content="")
        assert [f["fields"][0] for f in opening.json()["directives"] if f["verb"] == "ask"] == [
            "Enter your work email:"
        ]
        start = client.get(AUTH_START_PATH)
        assert start.status_code == 302
        console = start.headers["location"]
        assert console.startswith(AUTH_CONSOLE_PATH)
        page = client.get(console)
        assert page.status_code == 200
        assert f'action="{AUTH_CALLBACK_PATH}"' in page.text
        state = _query(console)["state"][0]
        callback = client.get(AUTH_CALLBACK_PATH, params={"state": state, "code": email})
        assert callback.status_code == 303
        signed_in = client.post("/v1/onboard/web", content="").json()["directives"]
    assert [entry["fields"][0] for entry in signed_in if entry["verb"] == "say"] == [
        f"Signed in: {email}"
    ]
    assert [entry["verb"] for entry in signed_in if entry["verb"] == "token"] == ["token"]


def test_the_console_page_is_not_mounted_under_the_workos_mode(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The dev stand-in exists only in console mode: a real deploy serves no such route, so the one
    door to a signed claim through the Google hop is WorkOS's own return."""
    _configure(monkeypatch, tmp_path, gateway_postgres)
    _verify_through(monkeypatch)
    with _client() as client:
        response = client.get(AUTH_CONSOLE_PATH, params={"state": "x"})
    assert response.status_code == 404
