"""The fleet route counts the shared workspaces through the real application lifespan."""

import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest
from fastapi.testclient import TestClient
from ufo.bearer import verify_token

import ufo_control.gateway as gateway
from ufo_control import gateway_slack_connect
from ufo_control.gateway import TOKEN_SECRET_ENV, WORKSPACE_BASE_URL_ENV, gateway_app
from ufo_control.gateway_email import (
    AWS_ROLE_ARN_ENV,
    AWS_WEB_IDENTITY_TOKEN_FILE_ENV,
    SES_SENDER_ENV,
)
from ufo_control.gateway_invite import INVITE_TTL, InviteCodes
from ufo_control.gateway_shared import SERVE_DSN_ENV
from ufo_control.gateway_slack_connect import (
    BOT_TOKEN_ENV,
    ENABLED_ENV,
    TABLE,
    TEAM_ID_ENV,
    SlackConnectInviter,
)

TOKEN_SECRET = "test-token-secret"
WORKSPACE_URL = "https://app.testing.flyingobject.ai"
OPERATOR_TEAM_ID = "T0PERATOR"
UNREACHABLE_SLACK = "http://127.0.0.1:1"
DELIVERY_POLL_SECONDS = 0.05
DELIVERY_TIMEOUT_SECONDS = 20.0


CODE_IN_BODY = re.compile(r"\d{6}")


@dataclass
class RecordingSender:
    sent: dict[str, str] = field(default_factory=dict)

    async def send(self, email: str, subject: str, text: str) -> None:
        """The sender is handed a rendered message, never a code, so the code is read back out of
        the body the way a member reads it."""
        found = CODE_IN_BODY.search(text)
        assert found is not None
        self.sent[email] = found.group()


async def _add_workspaces(dsn: str, count: int) -> None:
    connection = await asyncpg.connect(dsn)
    try:
        for _ in range(count):
            await connection.execute(
                "insert into workspace (id, created_at, updated_at) values ($1, now(), now())",
                uuid.uuid4(),
            )
    finally:
        await connection.close()


async def _add_workspace(dsn: str, workspace_id: UUID, owner_email: str) -> None:
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(
            "insert into workspace (id, created_at, updated_at) "
            "values ($1, now(), now()) on conflict do nothing",
            workspace_id,
        )
        await connection.execute(
            "insert into member "
            "(id, workspace_id, email, is_admin, created_at, updated_at) "
            "values ($1, $2, $3, true, now(), now())",
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


def test_a_granted_domain_is_identified_without_a_third_prompt(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The step this cut removes: verifying the email is the last thing a granted member does. The
    grant went to the founder, and the colleague who actually runs the installer redeems it — no
    prompt, nothing retyped, and the claim still stamps its invite so the Slack Connect invitation
    keeps its trigger."""
    _configure(monkeypatch, tmp_path, gateway_postgres)
    sender = RecordingSender()
    monkeypatch.setattr(gateway, "email_sender_from_env", lambda: sender)
    asyncio.run(_grant(gateway_postgres, 9, "founder@inviteco.io"))
    headers = {"x-ufo-session": "invite-flow", "x-ufo-installed": "1"}
    with TestClient(gateway_app()) as client:
        email = "colleague@inviteco.io"
        client.post("/v1/onboard/ufo", headers=headers, content="")
        client.post("/v1/onboard/ufo", headers=headers, content=email)
        signed_in = client.post("/v1/onboard/ufo", headers=headers, content=sender.sent[email])

    assert "enter your invite" not in signed_in.text
    assert re.search(r"object #9 identified\. \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", signed_in.text)
    assert f"signed in: {email}" in signed_in.text
    assert asyncio.run(_claim_invite_id(gateway_postgres, email)) is not None


def test_the_gate_ends_the_session_on_every_refusal(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No refusal prompts, because the member holds nothing that could change the answer: each one
    says why, names the waitlist, and exits cleanly rather than looping on a dead question."""
    _configure(monkeypatch, tmp_path, gateway_postgres)
    sender = RecordingSender()
    monkeypatch.setattr(gateway, "email_sender_from_env", lambda: sender)
    asyncio.run(_grant(gateway_postgres, 8, "founder@expiredco.io", timedelta(days=-1)))
    with TestClient(gateway_app()) as client:
        ungranted = _walk(client, "no-grant", "founder@ungrantedco.io", sender)
        assert "ungrantedco.io is not identified yet." in ungranted
        assert (
            "request identification: curl https://flyingobject.ai/waitlist"
            " -d email=founder@ungrantedco.io" in ungranted
        )
        assert "exit\t0" in ungranted
        assert "\task\t" not in ungranted

        expired = _walk(client, "expired-grant", "founder@expiredco.io", sender)
        assert re.search(
            r"expiredco\.io identification expired \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC\.", expired
        )
        assert "reply to your invite email for a new one." in expired
        assert "exit\t0" in expired

        asyncio.run(_grant(gateway_postgres, 10, "founder@burnedco.io"))
        asyncio.run(_burn_grant(gateway_postgres, "burnedco.io"))
        burned = _walk(client, "burned-grant", "founder@burnedco.io", sender)
        assert "burnedco.io is already identified." in burned
        assert "contact us if you cannot reach your fleet." in burned
        assert "exit\t0" in burned


def _walk(client: TestClient, session: str, email: str, sender: RecordingSender) -> str:
    headers = {"x-ufo-session": session, "x-ufo-installed": "1"}
    client.post("/v1/onboard/ufo", headers=headers, content="")
    client.post("/v1/onboard/ufo", headers=headers, content=email)
    return client.post("/v1/onboard/ufo", headers=headers, content=sender.sent[email]).text


async def _grant(dsn: str, object_number: int, email: str, ttl: timedelta = INVITE_TTL) -> None:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
    try:
        await InviteCodes(pool=pool, ttl=ttl).mint(object_number, email)
    finally:
        await pool.close()


async def _burn_grant(dsn: str, domain: str) -> None:
    """A grant consumed with no workspace behind it — the state a crash between the two writes
    leaves, and the only way the flow meets a consumed grant at all."""
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(
            "update ufo_control.invite_code set consumed_at = now() where email_domain = $1", domain
        )
    finally:
        await connection.close()


async def _claim_invite_id(dsn: str, email: str) -> UUID | None:
    connection = await asyncpg.connect(dsn)
    try:
        return await connection.fetchval(
            "select invite_id from ufo_control.onboard_claim where email = $1", email
        )
    finally:
        await connection.close()


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
    assert verify_token(directives["token"], workspace_id) == email
    assert directives["workspace"] == WORKSPACE_URL
    assert asyncio.run(_member_emails(gateway_postgres, workspace_id)) == [
        "founder@httpco.io",
        email,
    ]
    assert uuid.uuid5(uuid.NAMESPACE_DNS, "httpco.io") not in asyncio.run(
        _workspace_ids(gateway_postgres)
    )


def test_signup_completes_and_delivery_runs_while_slack_is_unreachable(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The gateway lifespan owns the Slack Connect worker, and Slack owns none of signup: with the
    Web API unreachable the member still signs in, and the delivery row records the outage."""
    _configure(monkeypatch, tmp_path, gateway_postgres)
    monkeypatch.setenv(ENABLED_ENV, "true")
    monkeypatch.setenv(BOT_TOKEN_ENV, "xoxb-unreachable")
    monkeypatch.setenv(TEAM_ID_ENV, OPERATOR_TEAM_ID)
    monkeypatch.setattr(gateway_slack_connect, "SLACK_API_BASE", UNREACHABLE_SLACK)
    from_env = gateway.slack_connect_from_env

    def promptly(pool: asyncpg.Pool) -> SlackConnectInviter:
        inviter = from_env(pool)
        assert inviter is not None
        return replace(inviter, poll_interval=DELIVERY_POLL_SECONDS)

    monkeypatch.setattr(gateway, "slack_connect_from_env", promptly)
    sender = RecordingSender()
    monkeypatch.setattr(gateway, "email_sender_from_env", lambda: sender)
    email = "founder@slackco.io"
    asyncio.run(_grant(gateway_postgres, 11, email))
    headers = {"x-ufo-session": "slack-connect-flow", "x-ufo-installed": "1"}
    with TestClient(gateway_app()) as client:
        client.post("/v1/onboard/ufo", headers=headers, content="")
        client.post("/v1/onboard/ufo", headers=headers, content=email)
        signed_in = client.post("/v1/onboard/ufo", headers=headers, content=sender.sent[email])
        assert f"signed in: {email}" in signed_in.text
        row = _await_attempted_delivery(gateway_postgres, email)
    assert row["state"] == "pending"
    assert row["channel_name"] == "ext-slackco-flyingobject"
    assert row["channel_id"] is None
    assert row["last_error"].startswith("auth.test transport failure")


def test_enabled_slack_connect_without_a_token_fails_startup(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path, gateway_postgres)
    monkeypatch.setenv(ENABLED_ENV, "true")
    monkeypatch.delenv(BOT_TOKEN_ENV, raising=False)
    with pytest.raises(RuntimeError, match=BOT_TOKEN_ENV), TestClient(gateway_app()):
        pass


def _await_attempted_delivery(dsn: str, email: str) -> asyncpg.Record:
    deadline = time.monotonic() + DELIVERY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        row = asyncio.run(_delivery_row(dsn, email))
        if row is not None and row["last_error"] is not None:
            return row
        time.sleep(DELIVERY_POLL_SECONDS)
    raise AssertionError(f"no slack connect delivery was attempted for {email}")


async def _delivery_row(dsn: str, email: str) -> asyncpg.Record | None:
    connection = await asyncpg.connect(dsn)
    try:
        return await connection.fetchrow(
            f"select d.* from {TABLE} d"
            " join ufo_control.onboard_claim c on c.id = d.onboard_claim_id"
            " where c.email = $1",
            email,
        )
    finally:
        await connection.close()


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
