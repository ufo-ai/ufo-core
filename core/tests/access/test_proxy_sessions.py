import json
import logging
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from starlette.applications import Starlette

from core.tests.access.proxy_fake import FAKE_CA_PEM, SENTINEL_PREFIX, proxy_app
from ufo.db import workspace_tx
from ufo.harness.sandbox.session import SandboxProviderUnavailable
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.access.egress_rules import Bind, HostEntry, SessionPolicy
from ufo.runtime.access.proxy_sessions import (
    AGENT_LABEL,
    CLOUD_BEARER_SLOT,
    CONVERSATION_LABEL,
    IDEMPOTENCY_HEADER,
    MEMBER_LABEL,
    PROXY_CALL_TIMEOUT_SECONDS,
    PROXY_SESSION_TTL_SECONDS,
    ProxyRefused,
    ProxySessions,
)
from ufo.runtime.workspace import ws
from ufo.schema import tables

PROXY_SESSION_CONTRACT = Path(__file__).parent / "proxy_session_contract.json"
CREATE_REQUEST = json.loads(PROXY_SESSION_CONTRACT.read_text())["create_request"]
LABELS = CREATE_REQUEST["labels"]
POLICY = SessionPolicy.model_validate(CREATE_REQUEST["policy"])
NARROWED = SessionPolicy(
    hosts=(HostEntry(host="api.acmekeys.com"),),
    bind=(
        Bind(host="api.acmekeys.com", header="x-api-key", secret="acme_api_key", env="ACME_KEY"),
    ),
)
BEARER = "ufo_proxy-sessions-system-token"
PROXY_URL = "https://proxy.test"
KEY = "turn:3e914c7d-4f60-4182-9d2e-3f4a5b6c7d8e:-:0123456789abcdef"
SHORT_TTL_S = 75


@pytest.fixture
def fake() -> Starlette:
    return proxy_app(BEARER)


@pytest.fixture
async def workspace_id(db: None) -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


@pytest.fixture
async def sessions(fake: Starlette, workspace_id: UUID) -> AsyncIterator[ProxySessions]:
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    with ws(workspace_id):
        await store.put(workspace_id, CLOUD_BEARER_SLOT, BEARER)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake), base_url=PROXY_URL
    ) as http:
        yield ProxySessions(PROXY_URL, store, http)


def _sent(fake: Starlette) -> list[tuple[str, str, object]]:
    return [
        (method, target, json.loads(body) if body else None)
        for method, target, _, body in fake.state.calls
    ]


def _keyed(fake: Starlette) -> list[tuple[str, str, str | None]]:
    return [
        (method, target, headers.get(IDEMPOTENCY_HEADER.lower()))
        for method, target, headers, _ in fake.state.calls
    ]


async def test_open_sends_the_bearer_the_key_and_the_policy_and_parses_the_session(
    fake: Starlette, sessions: ProxySessions, workspace_id: UUID
) -> None:
    created = await sessions.open(workspace_id, key=KEY, labels=LABELS, policy=POLICY)

    ((method, target, headers, body),) = fake.state.calls
    assert (method, target) == ("POST", "/v1/sessions")
    assert headers["authorization"] == f"Bearer {BEARER}"
    assert headers[IDEMPOTENCY_HEADER.lower()] == KEY
    assert json.loads(body) == CREATE_REQUEST
    assert set(LABELS) == {"turn", CONVERSATION_LABEL, AGENT_LABEL, MEMBER_LABEL}
    assert created.version == 1
    assert created.proxy_url == PROXY_URL
    assert created.env["HTTPS_PROXY"].startswith(f"https://{created.token}:")
    assert created.env["HTTPS_PROXY"].startswith("https://ufo-session-")
    assert created.env["ACME_KEY"].startswith(SENTINEL_PREFIX)
    assert created.ca_pem == FAKE_CA_PEM


async def test_a_replay_under_one_key_returns_the_same_session(
    fake: Starlette, sessions: ProxySessions, workspace_id: UUID
) -> None:
    first = await sessions.open(workspace_id, key=KEY, labels=LABELS, policy=POLICY)
    again = await sessions.open(workspace_id, key=KEY, labels=LABELS, policy=POLICY)
    other = await sessions.open(workspace_id, key=f"{KEY}:other", labels=LABELS, policy=POLICY)

    assert (again.id, again.token, again.env) == (first.id, first.token, first.env)
    assert other.id != first.id
    sent = [(method, target) for method, target, _ in _sent(fake)]
    assert sent == [("POST", "/v1/sessions")] * 3


async def test_a_replay_after_a_patch_sends_its_policy_again(
    fake: Starlette, sessions: ProxySessions, workspace_id: UUID
) -> None:
    first = await sessions.open(workspace_id, key=KEY, labels=LABELS, policy=POLICY)
    narrowed = await sessions.update(workspace_id, first.id, NARROWED)
    again = await sessions.open(workspace_id, key=KEY, labels=LABELS, policy=POLICY)

    assert _sent(fake)[2:] == [
        ("POST", "/v1/sessions", CREATE_REQUEST),
        ("PATCH", f"/v1/sessions/{first.id}", {"policy": CREATE_REQUEST["policy"]}),
    ]
    assert (again.id, again.token, again.ca_pem) == (first.id, first.token, first.ca_pem)
    assert again.version == narrowed.version + 1
    assert set(again.env) == set(first.env) != set(narrowed.env)


async def test_a_fresh_open_under_a_short_ttl_sends_one_post(
    fake: Starlette, sessions: ProxySessions, workspace_id: UUID
) -> None:
    created = await sessions.open(
        workspace_id, key=KEY, labels=LABELS, policy=POLICY, ttl_s=SHORT_TTL_S
    )

    assert [(method, target) for method, target, _ in _sent(fake)] == [("POST", "/v1/sessions")]
    assert created.expires_at - created.created_at == timedelta(seconds=SHORT_TTL_S)


async def test_a_replay_of_an_ended_session_opens_its_successor(
    fake: Starlette, sessions: ProxySessions, workspace_id: UUID
) -> None:
    first = await sessions.open(workspace_id, key=KEY, labels=LABELS, policy=POLICY)
    await sessions.update(workspace_id, first.id, NARROWED)
    fake.state.sessions[first.id]["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    successor = await sessions.open(workspace_id, key=KEY, labels=LABELS, policy=POLICY)
    replayed = await sessions.open(workspace_id, key=KEY, labels=LABELS, policy=POLICY)
    await sessions.revoke(workspace_id, successor.id)
    following = await sessions.open(workspace_id, key=KEY, labels=LABELS, policy=POLICY)

    assert _keyed(fake) == [
        ("POST", "/v1/sessions", KEY),
        ("PATCH", f"/v1/sessions/{first.id}", None),
        ("POST", "/v1/sessions", KEY),
        ("POST", "/v1/sessions", f"{KEY}:1"),
        ("POST", "/v1/sessions", KEY),
        ("POST", "/v1/sessions", f"{KEY}:1"),
        ("POST", f"/v1/sessions/{successor.id}/revoke", None),
        ("POST", "/v1/sessions", KEY),
        ("POST", "/v1/sessions", f"{KEY}:1"),
        ("POST", "/v1/sessions", f"{KEY}:2"),
    ]
    assert len({first.id, successor.id, following.id}) == 3
    assert (replayed.id, replayed.token, replayed.env) == (
        successor.id,
        successor.token,
        successor.env,
    )
    assert successor.version == following.version == 1


async def test_a_replay_within_one_call_of_its_deadline_opens_its_successor(
    fake: Starlette, sessions: ProxySessions, workspace_id: UUID
) -> None:
    first = await sessions.open(workspace_id, key=KEY, labels=LABELS, policy=POLICY)
    ending = datetime.now(UTC) + timedelta(seconds=PROXY_CALL_TIMEOUT_SECONDS)
    fake.state.sessions[first.id]["expires_at"] = ending

    successor = await sessions.open(workspace_id, key=KEY, labels=LABELS, policy=POLICY)

    assert _keyed(fake)[1:] == [
        ("POST", "/v1/sessions", KEY),
        ("POST", "/v1/sessions", f"{KEY}:1"),
    ]
    assert successor.id != first.id


async def test_a_near_deadline_is_renewed_before_the_policy_is_sent_again(
    fake: Starlette, sessions: ProxySessions, workspace_id: UUID
) -> None:
    first = await sessions.open(workspace_id, key=KEY, labels=LABELS, policy=POLICY)
    narrowed = await sessions.update(workspace_id, first.id, NARROWED)
    near = datetime.now(UTC) + timedelta(minutes=10)
    fake.state.sessions[first.id]["expires_at"] = near

    reopened = await sessions.open(workspace_id, key=KEY, labels=LABELS, policy=POLICY)

    assert _sent(fake)[2:] == [
        ("POST", "/v1/sessions", CREATE_REQUEST),
        ("POST", f"/v1/sessions/{first.id}/renew", {"ttl_s": PROXY_SESSION_TTL_SECONDS}),
        ("PATCH", f"/v1/sessions/{first.id}", {"policy": CREATE_REQUEST["policy"]}),
    ]
    assert (reopened.id, reopened.version) == (first.id, narrowed.version + 1)
    assert reopened.expires_at > near + timedelta(minutes=30)


async def test_open_renews_a_session_whose_deadline_is_near(
    fake: Starlette, sessions: ProxySessions, workspace_id: UUID
) -> None:
    first = await sessions.open(workspace_id, key=KEY, labels=LABELS, policy=POLICY)
    near = datetime.now(UTC) + timedelta(minutes=10)
    fake.state.sessions[first.id]["expires_at"] = near

    reopened = await sessions.open(workspace_id, key=KEY, labels=LABELS, policy=POLICY)

    assert _sent(fake)[1:] == [
        ("POST", "/v1/sessions", CREATE_REQUEST),
        ("POST", f"/v1/sessions/{first.id}/renew", {"ttl_s": PROXY_SESSION_TTL_SECONDS}),
    ]
    assert reopened.id == first.id
    assert reopened.expires_at > near + timedelta(minutes=30)


async def test_update_renew_revoke_and_revoke_labelled_hit_their_routes(
    fake: Starlette, sessions: ProxySessions, workspace_id: UUID
) -> None:
    turn = str(uuid4())
    first, second, ended = [
        await sessions.open(workspace_id, key=key, labels={"turn": turn}, policy=POLICY)
        for key in ("first", "second", "ended")
    ]
    await sessions.open(workspace_id, key="elsewhere", labels={"turn": str(uuid4())}, policy=POLICY)
    opened = len(fake.state.calls)

    updated = await sessions.update(workspace_id, first.id, NARROWED)
    renewed = await sessions.renew(workspace_id, first.id, 1800)
    await sessions.revoke(workspace_id, ended.id)
    revoked = await sessions.revoke_labelled(workspace_id, "turn", turn)

    sent = _sent(fake)[opened:]
    assert sent[:4] == [
        ("PATCH", f"/v1/sessions/{first.id}", {"policy": NARROWED.model_dump(mode="json")}),
        ("POST", f"/v1/sessions/{first.id}/renew", {"ttl_s": 1800}),
        ("POST", f"/v1/sessions/{ended.id}/revoke", None),
        ("GET", f"/v1/sessions?labels.turn={turn}&limit=200", None),
    ]
    assert sorted(sent[4:]) == sorted(
        ("POST", f"/v1/sessions/{session.id}/revoke", None) for session in (first, second)
    )
    assert revoked == 2
    assert updated.version == 2
    assert "ACME_KEY" in updated.env and "GH_TOKEN" not in updated.env
    assert renewed.id == first.id
    assert renewed.expires_at < first.expires_at


async def test_revoke_labelled_follows_every_page(
    fake: Starlette, sessions: ProxySessions, workspace_id: UUID
) -> None:
    turn = str(uuid4())
    opened = [
        await sessions.open(workspace_id, key=key, labels={"turn": turn}, policy=POLICY)
        for key in ("one", "two", "three")
    ]
    fake.state.page_size = 1

    assert await sessions.revoke_labelled(workspace_id, "turn", turn) == 3

    sent = _sent(fake)[len(opened) :]
    listed = [target for method, target, _ in sent if method == "GET"]
    assert len(listed) == 3
    assert all("cursor=" in target for target in listed[1:])
    assert sorted(target for method, target, _ in sent if method == "POST") == sorted(
        f"/v1/sessions/{session.id}/revoke" for session in opened
    )


async def test_an_empty_bearer_slot_is_a_provider_outage(
    fake: Starlette, workspace_id: UUID
) -> None:
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fake)) as http:
        unfilled = ProxySessions(PROXY_URL, store, http)
        with pytest.raises(SandboxProviderUnavailable, match="no proxy bearer"):
            await unfilled.open(workspace_id, key=KEY, labels=LABELS, policy=POLICY)
    assert fake.state.calls == []


async def test_a_fault_is_a_provider_outage_and_a_refusal_is_refused(
    fake: Starlette, sessions: ProxySessions, workspace_id: UUID
) -> None:
    fake.state.fault = 503
    with pytest.raises(SandboxProviderUnavailable, match="503"):
        await sessions.open(workspace_id, key=KEY, labels=LABELS, policy=POLICY)

    fake.state.fault = 403
    with pytest.raises(ProxyRefused) as refused:
        await sessions.revoke(workspace_id, uuid4())
    assert (refused.value.status, refused.value.error.code) == (403, "forbidden")

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(unreachable)) as http:
        with pytest.raises(SandboxProviderUnavailable, match="ConnectError"):
            await replace(sessions, http=http).renew(workspace_id, uuid4(), 60)


async def test_nothing_secret_reaches_a_log_record(
    fake: Starlette,
    sessions: ProxySessions,
    workspace_id: UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    created = await sessions.open(workspace_id, key=KEY, labels=LABELS, policy=POLICY)
    await sessions.update(workspace_id, created.id, NARROWED)
    await sessions.renew(workspace_id, created.id, PROXY_SESSION_TTL_SECONDS)
    await sessions.revoke_labelled(workspace_id, "turn", LABELS["turn"])
    fake.state.fault = 403
    with pytest.raises(ProxyRefused):
        await sessions.revoke(workspace_id, created.id)

    held = {
        BEARER,
        created.token,
        *(value for value in created.env.values() if value.startswith(SENTINEL_PREFIX)),
    }
    logged = "\n".join(repr(vars(record)) for record in caplog.records)
    assert caplog.records
    assert not any(secret in logged for secret in held)
