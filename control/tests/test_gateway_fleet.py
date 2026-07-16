"""The fleet route counts the shared workspaces through the real application lifespan."""

import asyncio
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest
from fastapi.testclient import TestClient
from ufo_ext_ufo.surface import verify_token

import ufo_control.gateway as gateway
from ufo_control.gateway import TOKEN_SECRET_ENV, WORKSPACE_BASE_URL_ENV, gateway_app
from ufo_control.gateway_email import (
    AWS_ROLE_ARN_ENV,
    AWS_WEB_IDENTITY_TOKEN_FILE_ENV,
    SES_SENDER_ENV,
)
from ufo_control.gateway_invite import InviteCodes
from ufo_control.gateway_shared import SERVE_DSN_ENV

TOKEN_SECRET = "test-token-secret"
WORKSPACE_URL = "https://app.testing.flyingobject.ai"


@dataclass
class RecordingSender:
    sent: dict[str, str] = field(default_factory=dict)

    async def send(self, email: str, code: str, expires_at: datetime, ttl: timedelta) -> None:
        self.sent[email] = code


async def _add_workspaces(dsn: str, count: int) -> None:
    connection = await asyncpg.connect(dsn)
    try:
        for _ in range(count):
            await connection.execute("insert into workspace (id) values ($1)", uuid.uuid4())
    finally:
        await connection.close()


async def _add_workspace(dsn: str, workspace_id: UUID, owner_email: str) -> None:
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(
            "insert into workspace (id) values ($1) on conflict do nothing", workspace_id
        )
        await connection.execute(
            "insert into member (id, workspace_id, email, created_at, updated_at) "
            "values ($1, $2, $3, now(), now())",
            uuid.uuid4(),
            workspace_id,
            owner_email,
        )
    finally:
        await connection.close()


async def _member_emails(dsn: str, workspace_id: UUID) -> list[str]:
    connection = await asyncpg.connect(dsn)
    try:
        rows = await connection.fetch(
            "select email from member where workspace_id = $1 order by email", workspace_id
        )
        return [row["email"] for row in rows]
    finally:
        await connection.close()


async def _workspace_ids(dsn: str) -> set[UUID]:
    connection = await asyncpg.connect(dsn)
    try:
        return {row["id"] for row in await connection.fetch("select id from workspace")}
    finally:
        await connection.close()


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


def test_fleet_answers_the_workspace_count(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path, gateway_postgres)
    with TestClient(gateway_app()) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        before = client.get("/fleet").json()["craft"]
        asyncio.run(_add_workspaces(gateway_postgres, 2))
        assert client.get("/fleet").json()["craft"] == before + 2


def test_http_invite_gate_reports_each_code_state(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path, gateway_postgres)
    sender = RecordingSender()
    monkeypatch.setattr(gateway, "email_sender_from_env", lambda: sender)
    expired_code, live_code = asyncio.run(_mint_invites(gateway_postgres))
    headers = {"x-ufo-session": "invite-flow", "x-ufo-installed": "1"}
    with TestClient(gateway_app()) as client:
        email = "founder@inviteco.io"
        client.post("/v1/onboard/ufo", headers=headers, content="")
        client.post("/v1/onboard/ufo", headers=headers, content=email)
        gate = client.post("/v1/onboard/ufo", headers=headers, content=sender.sent[email])
        assert "a new workspace needs an invite code." in gate.text

        unknown = client.post("/v1/onboard/ufo", headers=headers, content="zzzz-zzzz-zzzz")
        assert "code not recognized." in unknown.text
        assert (
            "request identification: curl https://flyingobject.ai/waitlist"
            " -d email=you@yourco.com" in unknown.text
        )

        expired = client.post("/v1/onboard/ufo", headers=headers, content=expired_code)
        assert re.search(
            rf"code {expired_code} expired \d{{4}}-\d{{2}}-\d{{2}} \d{{2}}:\d{{2}} UTC\.",
            expired.text,
        )
        assert "reply to your invite email for a new one." in expired.text

        accepted = client.post("/v1/onboard/ufo", headers=headers, content=live_code)
        assert re.search(
            rf"code {live_code} accepted\. object #9 identified\."
            r" \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
            accepted.text,
        )
        assert f"signed in: {email}" in accepted.text

        second = {"x-ufo-session": "invite-flow-2", "x-ufo-installed": "1"}
        other = "boss@twiceco.io"
        client.post("/v1/onboard/ufo", headers=second, content="")
        client.post("/v1/onboard/ufo", headers=second, content=other)
        client.post("/v1/onboard/ufo", headers=second, content=sender.sent[other])
        consumed = client.post("/v1/onboard/ufo", headers=second, content=live_code)
        assert f"code {live_code} already used. contact us if that wasn't your team." in (
            consumed.text
        )


async def _mint_invites(dsn: str) -> tuple[str, str]:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
    try:
        expired = await InviteCodes(pool=pool, ttl=timedelta(days=-1)).mint(8)
        live = await InviteCodes(pool=pool).mint(9)
        return expired.code, live.code
    finally:
        await pool.close()


def test_health_rejects_a_mismatched_database_role(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path, gateway_postgres)
    monkeypatch.setattr(gateway, "_dsn_role", lambda dsn: "unexpected")
    with TestClient(gateway_app()) as client:
        response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_http_onboarding_bounds_public_inputs(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path, gateway_postgres)
    headers = {"x-ufo-session": "session", "x-ufo-installed": "1"}
    with TestClient(gateway_app()) as client:
        channel = client.post(f"/v1/onboard/{'c' * 65}", headers=headers)
        session = client.post("/v1/onboard/ufo", headers={**headers, "x-ufo-session": "s" * 129})
        body = client.post("/v1/onboard/ufo", headers=headers, content="b" * 4097)
    assert "channel is too long" in channel.text
    assert "session is too long" in session.text
    assert "request body is too large" in body.text


def test_http_onboarding_hides_internal_errors(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path, gateway_postgres)

    async def fail(*args: object, **kwargs: object) -> bytes:
        raise RuntimeError("database-password")

    monkeypatch.setattr(gateway.Onboarding, "advance", fail)
    with TestClient(gateway_app()) as client:
        response = client.post(
            "/v1/onboard/ufo", headers={"x-ufo-session": "session", "x-ufo-installed": "1"}
        )
    assert "onboarding failed" in response.text
    assert "database-password" not in response.text


def test_http_onboarding_joins_and_returns_a_surface_verified_token(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path, gateway_postgres)
    sender = RecordingSender()
    monkeypatch.setattr(gateway, "email_sender_from_env", lambda: sender)
    email = "me@httpco.io"
    workspace_id = uuid.uuid4()
    asyncio.run(_add_workspace(gateway_postgres, workspace_id, "founder@httpco.io"))
    headers = {"x-ufo-session": "http-flow", "x-ufo-installed": "1"}
    with TestClient(gateway_app()) as client:
        opening = client.post("/v1/onboard/ufo", headers=headers, content="")
        assert "enter your work email" in opening.text
        asked = client.post("/v1/onboard/ufo", headers=headers, content=email)
        assert "enter the code" in asked.text
        signed_in = client.post("/v1/onboard/ufo", headers=headers, content=sender.sent[email])
    directives = dict(line.split("\t", 1) for line in signed_in.text.splitlines() if "\t" in line)
    assert verify_token(TOKEN_SECRET, directives["token"], workspace_id) == email
    assert directives["workspace"] == WORKSPACE_URL
    assert asyncio.run(_member_emails(gateway_postgres, workspace_id)) == [
        "founder@httpco.io",
        email,
    ]
    assert uuid.uuid5(uuid.NAMESPACE_DNS, "httpco.io") not in asyncio.run(
        _workspace_ids(gateway_postgres)
    )


def test_http_onboarding_refuses_an_ambiguous_domain(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path, gateway_postgres)
    sender = RecordingSender()
    monkeypatch.setattr(gateway, "email_sender_from_env", lambda: sender)
    domain = "ambiguous-httpco.io"
    asyncio.run(_add_workspace(gateway_postgres, uuid.uuid4(), f"first@{domain}"))
    asyncio.run(_add_workspace(gateway_postgres, uuid.uuid4(), f"second@{domain}"))
    email = f"me@{domain}"
    headers = {"x-ufo-session": "ambiguous-flow", "x-ufo-installed": "1"}
    with TestClient(gateway_app()) as client:
        client.post("/v1/onboard/ufo", headers=headers, content=email)
        response = client.post("/v1/onboard/ufo", headers=headers, content=sender.sent[email])
    assert "onboarding failed" in response.text
    assert uuid.uuid5(uuid.NAMESPACE_DNS, domain) not in asyncio.run(
        _workspace_ids(gateway_postgres)
    )
