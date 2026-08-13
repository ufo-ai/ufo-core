"""The fleet route counts the shared workspaces through the real application lifespan."""

import asyncio
import re
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest
from fake_workos import MAGIC_CODE, FakeVerifier
from fastapi.testclient import TestClient
from ufo.bearer import verify_token

import ufo_control.gateway as gateway
from ufo_control import gateway_slack_connect
from ufo_control.gateway import TOKEN_SECRET_ENV, WORKSPACE_BASE_URL_ENV, gateway_app
from ufo_control.gateway_email import (
    AWS_ROLE_ARN_ENV,
    AWS_WEB_IDENTITY_TOKEN_FILE_ENV,
    SES_SENDER_ENV,
    invite_email,
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
from ufo_control.gateway_web import LOGIN_PAGE, parse_directives
from ufo_control.gateway_workos import (
    AUTH_CALLBACK_PATH,
    WORKOS_API_KEY_ENV,
    WORKOS_CLIENT_ID_ENV,
    WORKOS_REDIRECT_URI_ENV,
)

TOKEN_SECRET = "test-token-secret"
WORKSPACE_URL = "https://app.testing.flyingobject.ai"
OPERATOR_TEAM_ID = "T0PERATOR"
UNREACHABLE_SLACK = "http://127.0.0.1:1"
DELIVERY_POLL_SECONDS = 0.05
DELIVERY_TIMEOUT_SECONDS = 20.0
LEXICON_EXPIRES_AT = datetime(2026, 7, 28, 18, 45, tzinfo=UTC)


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
    monkeypatch.setenv(WORKOS_API_KEY_ENV, "sk_test_gateway")
    monkeypatch.setenv(WORKOS_CLIENT_ID_ENV, "client_01GATEWAY")
    monkeypatch.setenv(WORKOS_REDIRECT_URI_ENV, f"{WORKSPACE_URL}{AUTH_CALLBACK_PATH}")


def _verify_through(monkeypatch: pytest.MonkeyPatch) -> FakeVerifier:
    verifier = FakeVerifier()
    monkeypatch.setattr(gateway, "workos_verifier_from_env", lambda: verifier)
    return verifier


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
    _verify_through(monkeypatch)
    asyncio.run(_grant(gateway_postgres, 9, "founder@inviteco.io"))
    headers = {"x-ufo-session": "invite-flow", "x-ufo-installed": "1"}
    with TestClient(gateway_app()) as client:
        email = "colleague@inviteco.io"
        client.post("/v1/onboard/ufo", headers=headers, content="")
        client.post("/v1/onboard/ufo", headers=headers, content=email)
        signed_in = client.post("/v1/onboard/ufo", headers=headers, content=MAGIC_CODE)

    assert "enter your invite" not in signed_in.text
    assert f"Signed in: {email}" in signed_in.text
    assert asyncio.run(_claim_invite_id(gateway_postgres, email)) is not None


def test_the_gate_ends_the_session_on_every_refusal(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No refusal prompts, because the member holds nothing that could change the answer: each one
    says why, names the waitlist, and exits cleanly rather than looping on a dead question."""
    _configure(monkeypatch, tmp_path, gateway_postgres)
    _verify_through(monkeypatch)
    asyncio.run(_grant(gateway_postgres, 8, "founder@expiredco.io", timedelta(days=-1)))
    with TestClient(gateway_app()) as client:
        ungranted = _walk(client, "no-grant", "founder@ungrantedco.io")
        assert "ungrantedco.io has no invite." in ungranted
        assert (
            "Join the waitlist: curl https://flyingobject.ai/waitlist"
            " -d email=founder@ungrantedco.io" in ungranted
        )
        assert "exit\t0" in ungranted
        assert "\task\t" not in ungranted

        expired = _walk(client, "expired-grant", "founder@expiredco.io")
        assert re.search(
            r"The invite for expiredco\.io expired \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC\.", expired
        )
        assert "Reply to your invite email for a new one." in expired
        assert "exit\t0" in expired

        asyncio.run(_grant(gateway_postgres, 10, "founder@burnedco.io"))
        asyncio.run(_burn_grant(gateway_postgres, "burnedco.io"))
        burned = _walk(client, "burned-grant", "founder@burnedco.io")
        assert "The invite for burnedco.io was already used." in burned
        assert "Contact us if you cannot sign in." in burned
        assert "exit\t0" in burned


def _walk(client: TestClient, session: str, email: str) -> str:
    headers = {"x-ufo-session": session, "x-ufo-installed": "1"}
    client.post("/v1/onboard/ufo", headers=headers, content="")
    client.post("/v1/onboard/ufo", headers=headers, content=email)
    return client.post("/v1/onboard/ufo", headers=headers, content=MAGIC_CODE).text


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
    assert "Channel is too long" in channel.text
    assert "Session is too long" in session.text
    assert "Request body is too large" in body.text


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
    assert "Onboarding failed" in response.text
    assert "database-password" not in response.text


def test_http_onboarding_joins_and_returns_a_surface_verified_token(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path, gateway_postgres)
    _verify_through(monkeypatch)
    email = "me@httpco.io"
    workspace_id = uuid.uuid4()
    asyncio.run(_add_workspace(gateway_postgres, workspace_id, "founder@httpco.io"))
    headers = {"x-ufo-session": "http-flow", "x-ufo-installed": "1"}
    with TestClient(gateway_app()) as client:
        opening = client.post("/v1/onboard/ufo", headers=headers, content="")
        assert "Enter your work email" in opening.text
        asked = client.post("/v1/onboard/ufo", headers=headers, content=email)
        assert "Enter the code" in asked.text
        signed_in = client.post("/v1/onboard/ufo", headers=headers, content=MAGIC_CODE)
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
    _verify_through(monkeypatch)
    email = "founder@slackco.io"
    asyncio.run(_grant(gateway_postgres, 11, email))
    headers = {"x-ufo-session": "slack-connect-flow", "x-ufo-installed": "1"}
    with TestClient(gateway_app()) as client:
        client.post("/v1/onboard/ufo", headers=headers, content="")
        client.post("/v1/onboard/ufo", headers=headers, content=email)
        signed_in = client.post("/v1/onboard/ufo", headers=headers, content=MAGIC_CODE)
        assert f"Signed in: {email}" in signed_in.text
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
    _verify_through(monkeypatch)
    domain = "ambiguous-httpco.io"
    asyncio.run(_add_workspace(gateway_postgres, uuid.uuid4(), f"first@{domain}"))
    asyncio.run(_add_workspace(gateway_postgres, uuid.uuid4(), f"second@{domain}"))
    email = f"me@{domain}"
    headers = {"x-ufo-session": "ambiguous-flow", "x-ufo-installed": "1"}
    with TestClient(gateway_app()) as client:
        client.post("/v1/onboard/ufo", headers=headers, content=email)
        response = client.post("/v1/onboard/ufo", headers=headers, content=MAGIC_CODE)
    assert "Onboarding failed" in response.text
    assert uuid.uuid5(uuid.NAMESPACE_DNS, domain) not in asyncio.run(
        _workspace_ids(gateway_postgres)
    )


# The product is named ufo; nothing a member reads plays the part. One sweep over every screen the
# control plane renders, so a new branch or a reworded line cannot reintroduce the lexicon.
BANNED_METAPHOR = re.compile(
    r"\bbeam\w*|\btransmit\w*|\bsignals?\b|\bsaucers?\b|\bmothership\b|\babduct\w*"
    r"|\b(un)?identified\b|\bidentification\b",
    re.IGNORECASE,
)
COPY_VERBS = frozenset({"say", "ask", "choose"})

# Standard typography: a sentence never opens lowercase unless it opens with a literal — a
# command, an address, a header name. Lines split into sentences on a spaced terminator, and
# glyph-only tokens (check marks, prompts, art) are skipped so the word behind them is inspected.
# A terminator fused to its next word (`Done.try`, `Sent!check`) goes unjudged: the dot form is
# the same shape as a cased dotted literal (`Node.js`, `README.md`) and `?` rides in URLs, so
# with two of the three terminators ambiguous, fused terminators are uniformly out of scope —
# the accept list pins all three fused shapes as accepted.
LITERAL_START = re.compile(r"^(?:curl|ufo|x-ufo-|\S*[.@]\S+)")
SENTENCE_END = re.compile(r"[.?!]\s+")


def _assert_standard_case(copy: str) -> None:
    for line in copy.splitlines():
        for sentence in SENTENCE_END.split(line):
            words = [word for word in sentence.split() if any(ch.isalnum() for ch in word)]
            if not words or LITERAL_START.match(words[0]):
                continue
            first_alnum = next(ch for ch in words[0] if ch.isalnum())
            assert not first_alnum.islower(), (words[0], line)


def test_the_case_gate_anchors_every_sentence_and_sees_past_glyphs() -> None:
    for styled in (
        "Done. try again.",
        "Sent! check your inbox.",
        "✓ installed ufo",
        "> type ufo to continue.",
        "✓installed ufo",
        "(optional) set your name.",
    ):
        with pytest.raises(AssertionError):
            _assert_standard_case(styled)
    for plain in (
        "✓ Installed ufo (/x/bin/ufo)",
        "#3 on the waitlist. We will email you when access opens.",
        "gmail.com is not a work email domain.",
        "Gmail.com is not a work email domain.",
        "Read README.md for the format.",
        "Node.js is required.",
        "Done.try again",
        "Sent!check your inbox.",
        "Done?try again",
        "curl -fsSL https://flyingobject.ai/ufo | sh",
    ):
        _assert_standard_case(plain)


def _member_copy(payload: str) -> list[str]:
    """Only the verbs a member reads — a token and a workspace URL are wire, not copy."""
    copy: list[str] = []
    for parsed in parse_directives(payload.encode()):
        if parsed["verb"] in COPY_VERBS:
            fields = parsed["fields"]
            assert isinstance(fields, list)
            copy.extend(str(field) for field in fields)
    return copy


def test_every_word_a_member_reads_carries_no_ufo_metaphor(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every rendered onboarding screen — the missing-header refusal, the opening, both prompts, a
    rejected address, a wrong code, the signed-in cap, and all three invite refusals — plus the two
    emails, the sign-in page, and the served terminal client."""
    _configure(monkeypatch, tmp_path, gateway_postgres)
    _verify_through(monkeypatch)
    asyncio.run(_grant(gateway_postgres, 21, "founder@lexiconco.io"))
    asyncio.run(_grant(gateway_postgres, 22, "founder@lexiconexpired.io", timedelta(days=-1)))
    asyncio.run(_grant(gateway_postgres, 23, "founder@lexiconburned.io"))
    asyncio.run(_burn_grant(gateway_postgres, "lexiconburned.io"))
    email = "founder@lexiconco.io"
    headers = {"x-ufo-session": "lexicon", "x-ufo-installed": "1"}
    screens: list[str] = []
    with TestClient(gateway_app()) as client:
        screens += _member_copy(client.post("/v1/onboard/ufo", content="").text)
        screens += _member_copy(client.post("/v1/onboard/ufo", headers=headers, content="").text)
        screens += _member_copy(
            client.post("/v1/onboard/ufo", headers=headers, content="me@gmail.com").text
        )
        screens += _member_copy(client.post("/v1/onboard/ufo", headers=headers, content=email).text)
        screens += _member_copy(
            client.post("/v1/onboard/ufo", headers=headers, content="000000").text
        )
        screens += _member_copy(
            client.post("/v1/onboard/ufo", headers=headers, content=MAGIC_CODE).text
        )
        for session, refused in (
            ("lexicon-none", "founder@lexiconnone.io"),
            ("lexicon-expired", "founder@lexiconexpired.io"),
            ("lexicon-burned", "founder@lexiconburned.io"),
        ):
            screens += _member_copy(_walk(client, session, refused))
    invite_subject, invite_body = invite_email(email, LEXICON_EXPIRES_AT, "flyingobject.ai")
    screens += [invite_subject, invite_body]
    assert len(screens) > 15
    for copy in screens:
        assert BANNED_METAPHOR.search(copy) is None, copy
        _assert_standard_case(copy)
    for source in (LOGIN_PAGE, gateway.STAMPED_SCRIPT):
        assert BANNED_METAPHOR.search(source) is None, source


# The client's fixed member copy — say/die arguments and the printf literals a member sees —
# each pinned with its occurrence count. The served script is shell source: the rendered sweep
# in test_every_word_a_member_reads_carries_no_ufo_metaphor judges its lexicon whole, but its
# typography is out of reach there, so each listed line is held verbatim at every site it
# occurs. Presence proves each listed string, never that no unlisted one exists.
CLIENT_MEMBER_COPY = (
    ("""say 'Signed out.'""", 1),
    ('say "${DIM}Resume this conversation: ${RESET}${BOLD}ufo --resume $UFO_CHANNEL${RESET}"', 1),
    ("""die '--resume needs a conversation id.'""", 1),
    ("die 'Could not read 16 random bytes from /dev/urandom'", 1),
    ('say "${DIM}✓ Installed ufo ($BIN)${RESET}"', 1),
    ('say "${DIM}✓ Added ufo to PATH in $profile${RESET}"', 1),
    ('say "${DIM}✓ Linked ufo into ~/.local/bin${RESET}"', 1),
    (r'say "${DIM}  For this shell:${RESET} export PATH=\"$UFO_HOME/bin:\$PATH\""', 1),
    ('say "${DIM}No confirmation — ask the assistant to check.${RESET}"', 1),
    ('say "${DIM}Skipped $SS_SLOT${RESET}"', 1),
    ('say "Run ufo in a terminal to enter: $SS_PROMPT"', 1),
    ('die "Could not fetch $UFO_URL/ufo"', 1),
    ('die "No response from $UFO_URL"', 1),
    ('F_LINE="shared $F_NAME ($F_SIZE bytes)"', 1),
    ("printf 'ufo: %s\\n'", 1),
    ("printf '%s%s%s (hidden): '", 1),
    ("printf '\\n# added by ufo installer\\n%s\\n'", 1),
)


def test_client_member_copy_is_the_fixed_copy() -> None:
    for copy, occurrences in CLIENT_MEMBER_COPY:
        assert gateway.STAMPED_SCRIPT.count(copy) == occurrences, copy
