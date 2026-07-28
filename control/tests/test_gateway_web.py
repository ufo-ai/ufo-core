"""The web renderer of the onboarding machine: the JSON directive wire, the sign-in page's
self-containment, and the full email → code → invite walk over the real `onboard_claim` table and
`SharedWorkspaces` through `POST /v1/onboard/web` — one machine, a second renderer. The `debugger`
directive is asserted at both poles: emitted with the exact URL for an operator-domain email,
absent for everyone else."""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_DNS, UUID, uuid5

import asyncpg
import pytest
from fastapi.testclient import TestClient
from starlette.routing import Route
from ufo.bearer import verify_token
from ufo.ext.surface import OPERATOR_EMAIL_DOMAIN
from ufo.serve import RESERVED_HOST_PREFIXES

import ufo_control.gateway as gateway
from ufo_control.gateway import (
    INVITE_REQUIRED_ENV,
    TOKEN_SECRET_ENV,
    WORKSPACE_BASE_URL_ENV,
    gateway_app,
)
from ufo_control.gateway_directives import PROMPT, directive, render
from ufo_control.gateway_email import (
    AWS_ROLE_ARN_ENV,
    AWS_WEB_IDENTITY_TOKEN_FILE_ENV,
    SES_SENDER_ENV,
)
from ufo_control.gateway_invite import InviteCodes
from ufo_control.gateway_shared import SERVE_DSN_ENV
from ufo_control.gateway_web import LOGIN_PAGE, parse_directives

TOKEN_SECRET = "web-token-secret"
WORKSPACE_URL = "https://app.testing.flyingobject.ai"


@dataclass
class RecordingSender:
    sent: dict[str, str] = field(default_factory=dict)

    async def send(self, email: str, code: str, expires_at: datetime, ttl: timedelta) -> None:
        self.sent[email] = code


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


def test_parse_directives_round_trips_the_wire_escaping() -> None:
    payload = render(
        directive("say", "tab\there"),
        directive("say", "line\nbreak and back\\slash"),
        directive("ask", "enter your work email:"),
    )
    assert parse_directives(payload) == [
        {"verb": "say", "fields": ["tab\there"]},
        {"verb": "say", "fields": ["line\nbreak and back\\slash"]},
        {"verb": "ask", "fields": ["enter your work email:"]},
    ]


def test_login_page_is_self_contained_and_targets_the_web_wire() -> None:
    assert LOGIN_PAGE.startswith("<!doctype html>")
    assert "<script src=" not in LOGIN_PAGE
    assert "<link" not in LOGIN_PAGE
    assert "//cdn" not in LOGIN_PAGE
    assert "https://" not in LOGIN_PAGE
    assert "/v1/onboard/web" in LOGIN_PAGE


def test_login_page_hands_the_token_off_by_post_after_the_whole_batch() -> None:
    """The debugger handoff is a form POST (the bearer never rides a URL), and the signed-in card
    completes only after every directive in a batch has been handled — `debugger` arrives after
    `token`/`workspace`, so a per-directive completion check would hide the link."""
    assert '<form id="debugger-row" method="post">' in LOGIN_PAGE
    assert 'name="token"' in LOGIN_PAGE
    assert "token=" not in LOGIN_PAGE
    batch = "for (const directive of payload.directives) handle(directive);"
    completion = "if (token && workspace && !finished) complete();"
    assert LOGIN_PAGE.index(completion) > LOGIN_PAGE.index(batch)
    assert LOGIN_PAGE.count("complete()") == 2


def _mint_invite(dsn: str, object_number: int) -> str:
    async def _mint() -> str:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
        try:
            return (await InviteCodes(pool=pool).mint(object_number)).code
        finally:
            await pool.close()

    return asyncio.run(_mint())


def _advance(client: TestClient, session: str, body: str) -> list[dict[str, object]]:
    response = client.post("/v1/onboard/web", headers={"x-ufo-session": session}, content=body)
    assert response.status_code == 200
    return response.json()["directives"]


def _fields(directives: list[dict[str, object]], verb: str) -> list[str]:
    collected: list[str] = []
    for entry in directives:
        if entry["verb"] != verb:
            continue
        fields = entry["fields"]
        assert isinstance(fields, list)
        if fields:
            collected.append(str(fields[0]))
    return collected


def test_web_channel_walks_email_code_invite_to_the_signed_in_card(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The web renderer end to end, and the one directive it must never receive: this member
    administers the workspace they just created, but the page ends on its signed-in card rather
    than a prompt, so it is never handed a `choose` menu it has no way to drive. The terminal
    admin's billing choice is asserted in test_rls."""
    _configure(monkeypatch, tmp_path, gateway_postgres)
    sender = RecordingSender()
    monkeypatch.setattr(gateway, "email_sender_from_env", lambda: sender)
    invite = _mint_invite(gateway_postgres, 71)
    session = str(uuid.uuid4())
    email = "boss@webco.io"
    with TestClient(gateway_app()) as client:
        opening = _advance(client, session, "")
        assert "enter your work email:" in _fields(opening, "ask")
        assert not any(d["verb"] == "install" for d in opening)

        coded = _advance(client, session, email)
        assert "enter the code:" in _fields(coded, "ask")

        gated = _advance(client, session, sender.sent[email])
        assert any("invite" in text for text in _fields(gated, "say"))

        signed_in = _advance(client, session, invite)

    (token,) = _fields(signed_in, "token")
    (workspace,) = _fields(signed_in, "workspace")
    workspace_id = UUID(str(uuid5(NAMESPACE_DNS, "webco.io")))
    assert verify_token(token, workspace_id) == email
    assert workspace == WORKSPACE_URL
    assert not _fields(signed_in, "debugger")
    assert all(token not in text for text in _fields(signed_in, "say"))
    assert not _fields(signed_in, "choose")
    assert _fields(signed_in, "ask") == [PROMPT]


def test_disabled_invite_gate_opens_a_new_workspace_without_a_code(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path, gateway_postgres)
    monkeypatch.setenv(INVITE_REQUIRED_ENV, "false")
    sender = RecordingSender()
    monkeypatch.setattr(gateway, "email_sender_from_env", lambda: sender)
    session = str(uuid.uuid4())
    email = "founder@nogate.io"
    with TestClient(gateway_app()) as client:
        _advance(client, session, "")
        _advance(client, session, email)
        signed_in = _advance(client, session, sender.sent[email])
    assert not any("invite" in text for text in _fields(signed_in, "say"))
    (token,) = _fields(signed_in, "token")
    assert verify_token(token, UUID(str(uuid5(NAMESPACE_DNS, "nogate.io")))) == email


def test_web_and_terminal_sessions_never_share_a_claim(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path, gateway_postgres)
    sender = RecordingSender()
    monkeypatch.setattr(gateway, "email_sender_from_env", lambda: sender)
    session = str(uuid.uuid4())
    with TestClient(gateway_app()) as client:
        _advance(client, session, "")
        coded = _advance(client, session, "pilot@twochannel.io")
        assert "enter the code:" in _fields(coded, "ask")
        terminal = client.post(
            "/v1/onboard/ufo",
            headers={"x-ufo-session": session, "x-ufo-installed": "1"},
            content="",
        )
        assert "enter your work email:" in terminal.text


def test_debugger_directive_lands_only_for_the_operator_domain(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path, gateway_postgres)
    sender = RecordingSender()
    monkeypatch.setattr(gateway, "email_sender_from_env", lambda: sender)
    invite = _mint_invite(gateway_postgres, 72)
    session = str(uuid.uuid4())
    email = f"alex@{OPERATOR_EMAIL_DOMAIN}"
    with TestClient(gateway_app()) as client:
        _advance(client, session, "")
        _advance(client, session, email)
        gated = _advance(client, session, sender.sent[email])
        assert any("invite" in text for text in _fields(gated, "say"))
        signed_in = _advance(client, session, invite)
    assert _fields(signed_in, "debugger") == [f"{WORKSPACE_URL}/surface/debug"]


def test_debugger_directive_never_lands_outside_the_operator_domain(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path, gateway_postgres)
    sender = RecordingSender()
    monkeypatch.setattr(gateway, "email_sender_from_env", lambda: sender)
    invite = _mint_invite(gateway_postgres, 73)
    session = str(uuid.uuid4())
    email = "pilot@customerco.io"
    with TestClient(gateway_app()) as client:
        _advance(client, session, "")
        _advance(client, session, email)
        _advance(client, session, sender.sent[email])
        signed_in = _advance(client, session, invite)
    assert _fields(signed_in, "token")
    assert not _fields(signed_in, "debugger")


def test_login_page_and_session_header_requirements(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path, gateway_postgres)
    sender = RecordingSender()
    monkeypatch.setattr(gateway, "email_sender_from_env", lambda: sender)
    with TestClient(gateway_app()) as client:
        page = client.get("/login")
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        assert page.text == LOGIN_PAGE
        missing = client.post("/v1/onboard/web", content="")
        assert missing.status_code == 400
        assert missing.json() == {"error": "x-ufo-session header is required"}


def test_gateway_serves_every_reserved_host_prefix() -> None:
    """The single-app-host ingress routes the reserved prefixes to this gateway (`RESERVED_HOST_
    PREFIXES`, the same constant the serve fleet forbids). The gateway must serve a route under
    each, or the split aims a path at a 404."""
    paths = [route.path for route in gateway_app().routes if isinstance(route, Route)]
    for prefix in RESERVED_HOST_PREFIXES:
        assert any(path.startswith(prefix) for path in paths), prefix
