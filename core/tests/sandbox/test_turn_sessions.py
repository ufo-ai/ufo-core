import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from starlette.applications import Starlette

from core.tests.access.proxy_fake import SENTINEL_HEAD, proxy_app
from ufo.db import workspace_tx
from ufo.harness.sandbox.session import RunTokenCodec, SandboxProviderUnavailable
from ufo.runtime.access.connectors import CliCredential, GitWire
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.access.egress_resolver import PerAgentRules
from ufo.runtime.access.egress_rules import UFO_MODELS_SECRET, Bind, policy_hosts
from ufo.runtime.access.grants import GrantStore
from ufo.runtime.access.proxy_sessions import (
    IDEMPOTENCY_HEADER,
    PROXY_SESSION_MAX_TTL_SECONDS,
    PROXY_SESSION_TTL_SECONDS,
    SESSION_REVOKED_CODE,
    ProxyRefused,
    ProxySessions,
)
from ufo.runtime.access.turn_sessions import TurnSessions
from ufo.runtime.access.workspace_slots import WorkspaceSlots
from ufo.runtime.agent_scope import agent
from ufo.runtime.background_tasks import mark_detached
from ufo.runtime.billing.accounting import AGENT_LABEL, CONVERSATION_LABEL, MEMBER_LABEL, TURN_LABEL
from ufo.runtime.ext.manifest import CredentialSlot, InjectionTarget
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import NON_TERMINAL_STATUSES, Turn

BEARER = "ufo_turn-sessions-system-token"
PROXY_URL = "https://proxy.test"
RUN_TOKENS = RunTokenCodec(b"turn-sessions-secret")
ACME_SLOT = CredentialSlot(
    name="acme_api_key",
    description="Acme key the proxy service binds on its API host.",
    injection=InjectionTarget(host="api.acmekeys.com", header="x-api-key", env="ACME_KEY"),
)
MODEL_BIND = Bind(
    host="api.anthropic.com", header="x-api-key", secret=UFO_MODELS_SECRET, env="ANTHROPIC_API_KEY"
)
BRIDGE_UPSTREAM = "https://serve.test/internal/egress/tool-bridge"
GITHUB = "github"


@dataclass(frozen=True)
class _NoToken:
    async def secret(self, workspace_id: UUID, account_id: str) -> str:
        raise AssertionError("a session names a connection and never reads its token")


GH_CLI = CliCredential(
    env="GH_TOKEN",
    header="authorization",
    secret=_NoToken(),
    git=GitWire(host="github.com", basic_user="x-access-token", helper="!gh auth git-credential"),
)


@dataclass(frozen=True)
class _Bearer:
    async def bearer(self) -> str:
        return BEARER


@dataclass(frozen=True)
class _Seeded:
    turn: Turn
    member_b: UUID
    rules: PerAgentRules


@pytest.fixture
def fake() -> Starlette:
    return proxy_app(BEARER)


@pytest.fixture
async def proxy(fake: Starlette) -> AsyncIterator[ProxySessions]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake), base_url=PROXY_URL
    ) as http:
        yield ProxySessions(PROXY_URL, _Bearer(), http)


@pytest.fixture
async def seeded(db: None) -> _Seeded:
    workspace_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4()
    member_a, member_b = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                internet_access_allowed=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        for member_id, email in ((member_a, "a@work.com"), (member_b, "b@work.com")):
            await connection.execute(
                sa.insert(tables.member).values(
                    id=member_id,
                    workspace_id=workspace_id,
                    email=email,
                    seated_at=sa.func.now(),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=member_a,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    turn = Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        agent_id=agent_id,
        member_id=member_a,
        seq=1,
        status="running",
        inbound="hi",
        created_at=datetime.now(UTC),
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn.id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                member_id=member_a,
                seq=1,
                status="running",
                inbound="hi",
                created_at=turn.created_at,
                updated_at=turn.created_at,
            )
        )
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    with ws(workspace_id):
        await store.put(workspace_id, ACME_SLOT.name, "acme-real-secret")
    with ws(workspace_id), agent(agent_id):
        await GrantStore().record(
            provider=GITHUB,
            account_id="acct-b",
            host="api.github.com",
            grantor_member_id=member_b,
            shared=False,
        )
    rules = PerAgentRules(
        hosts=policy_hosts("api.anthropic.com"),
        binds=(MODEL_BIND,),
        grants=GrantStore(),
        credentials=store,
        slots=WorkspaceSlots(deploy=(ACME_SLOT,)),
        internet=True,
        clis={GITHUB: GH_CLI},
        bridge_upstream=BRIDGE_UPSTREAM,
    )
    return _Seeded(turn=turn, member_b=member_b, rules=rules)


def _sessions(seeded: _Seeded, proxy: ProxySessions | None) -> TurnSessions:
    return TurnSessions(
        proxy=proxy,
        rules=seeded.rules,
        run_tokens=RUN_TOKENS,
        turn=seeded.turn,
        agent_id=seeded.turn.agent_id,
        internet_access_allowed=True,
    )


async def _commit(seeded: _Seeded, status: str) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .where(tables.turn.c.id == seeded.turn.id)
            .values(
                status=status,
                terminal=None if status in NON_TERMINAL_STATUSES else {"status": status},
            )
        )


def _calls(fake: Starlette) -> list[tuple[str, str]]:
    return [(method, target) for method, target, _, _ in fake.state.calls]


def _bodies(fake: Starlette, method: str) -> list[dict[str, object]]:
    return [json.loads(body) for sent, _, _, body in fake.state.calls if sent == method and body]


async def test_the_base_session_opens_once_under_the_turn_key(
    fake: Starlette, proxy: ProxySessions, seeded: _Seeded
) -> None:
    sessions = _sessions(seeded, proxy)

    first = await sessions.open()
    again = await sessions.open()

    assert first is not None and again is not None
    assert again.id == first.id
    ((method, target, headers, body),) = fake.state.calls
    assert (method, target) == ("POST", "/v1/sessions")
    assert headers[IDEMPOTENCY_HEADER.lower()].startswith(f"turn:{seeded.turn.id}:-:")
    assert len(headers[IDEMPOTENCY_HEADER.lower()].rsplit(":", 1)[1]) == 16
    assert json.loads(body)["labels"] == {
        TURN_LABEL: str(seeded.turn.id),
        CONVERSATION_LABEL: str(seeded.turn.conversation_id),
        AGENT_LABEL: str(seeded.turn.agent_id),
        MEMBER_LABEL: str(seeded.turn.member_id),
    }
    assert json.loads(body)["ttl_s"] == PROXY_SESSION_TTL_SECONDS


async def test_each_acting_member_has_its_own_session(
    fake: Starlette, proxy: ProxySessions, seeded: _Seeded
) -> None:
    sessions = _sessions(seeded, proxy)

    base = await sessions.reconcile(sessions.actor(seeded.turn.member_id))
    acting = await sessions.reconcile(sessions.actor(seeded.member_b))

    assert base is not None and acting is not None
    assert acting.id != base.id
    assert acting.token != base.token
    assert acting.env["GH_TOKEN"].startswith(SENTINEL_HEAD)
    assert "GH_TOKEN" not in base.env
    assert acting.env["ACME_KEY"] != base.env["ACME_KEY"]
    keys = [
        headers[IDEMPOTENCY_HEADER.lower()]
        for method, _, headers, _ in fake.state.calls
        if method == "POST" and IDEMPOTENCY_HEADER.lower() in headers
    ]
    assert [key.split(":")[2] for key in keys] == ["-", str(seeded.member_b)]
    created = _bodies(fake, "POST")
    assert created[0]["labels"][MEMBER_LABEL] == str(seeded.turn.member_id)
    assert created[2]["labels"][MEMBER_LABEL] == str(seeded.member_b)


async def test_reconcile_patches_only_when_the_policy_moved(
    fake: Starlette, proxy: ProxySessions, seeded: _Seeded
) -> None:
    sessions = _sessions(seeded, proxy)

    opened = await sessions.reconcile("turn")
    await sessions.reconcile("turn")
    assert opened is not None
    assert _calls(fake) == [
        ("POST", "/v1/sessions"),
        ("POST", f"/v1/sessions/{opened.id}/renew"),
        ("POST", f"/v1/sessions/{opened.id}/renew"),
    ]
    assert "GH_TOKEN" not in opened.env

    with ws(seeded.turn.workspace_id), agent(seeded.turn.agent_id):
        await GrantStore().record(
            provider=GITHUB,
            account_id="acct-shared",
            host="api.github.com",
            grantor_member_id=seeded.member_b,
            shared=True,
        )
    moved = await sessions.reconcile("turn")

    assert moved is not None
    assert moved.id == opened.id
    assert _calls(fake)[3:] == [
        ("PATCH", f"/v1/sessions/{opened.id}"),
        ("POST", f"/v1/sessions/{opened.id}/renew"),
    ]
    assert moved.env["GH_TOKEN"].startswith(SENTINEL_HEAD)
    assert moved.env["ACME_KEY"] == opened.env["ACME_KEY"]
    assert moved.version == opened.version + 1
    assert moved.expires_at >= opened.expires_at


async def test_close_revokes_every_session(
    fake: Starlette, proxy: ProxySessions, seeded: _Seeded
) -> None:
    sessions = _sessions(seeded, proxy)
    base = await sessions.reconcile("turn")
    acting = await sessions.reconcile(seeded.member_b)
    assert base is not None and acting is not None

    await _commit(seeded, "done")
    await sessions.close()

    assert {
        session_id: held["revoked_at"] is not None
        for session_id, held in fake.state.sessions.items()
    } == {base.id: True, acting.id: True}
    assert _calls(fake)[4] == ("GET", f"/v1/sessions?labels.turn={seeded.turn.id}&limit=200")


async def test_close_revokes_an_earlier_runs_session(
    fake: Starlette, proxy: ProxySessions, seeded: _Seeded
) -> None:
    crashed = await _sessions(seeded, proxy).reconcile(seeded.member_b)
    assert crashed is not None

    await _commit(seeded, "done")
    await _sessions(seeded, proxy).close()

    assert fake.state.sessions[crashed.id]["revoked_at"] is not None


async def test_close_narrows_and_renews_every_session_of_a_detached_turn(
    fake: Starlette, proxy: ProxySessions, seeded: _Seeded
) -> None:
    earlier = await _sessions(seeded, proxy).reconcile(seeded.member_b)
    sessions = _sessions(seeded, proxy)
    base = await sessions.reconcile("turn")
    assert earlier is not None and base is not None
    with ws(seeded.turn.workspace_id):
        await mark_detached(seeded.turn, "build", "/home/user/.ufo/runs/build")
    sent = len(fake.state.calls)

    await _commit(seeded, "done")
    await sessions.close()

    closing = fake.state.calls[sent:]
    assert [(method, target) for method, target, _, _ in closing] == [
        ("GET", f"/v1/sessions?labels.turn={seeded.turn.id}&limit=200"),
        ("PATCH", f"/v1/sessions/{base.id}"),
        ("POST", f"/v1/sessions/{base.id}/renew"),
        ("PATCH", f"/v1/sessions/{earlier.id}"),
        ("POST", f"/v1/sessions/{earlier.id}/renew"),
    ]
    patches = [json.loads(body)["policy"] for method, _, _, body in closing if method == "PATCH"]
    for policy in patches:
        assert policy["routes"] == []
        assert UFO_MODELS_SECRET not in {bind["secret"] for bind in policy["bind"]}
        assert "acme_api_key" in {bind["secret"] for bind in policy["bind"]}
    assert "GH_TOKEN" in {bind["env"] for bind in patches[1]["bind"]}
    assert "GH_TOKEN" not in {bind["env"] for bind in patches[0]["bind"]}
    renewals = [json.loads(body)["ttl_s"] for method, _, _, body in closing if method == "POST"]
    assert all(0 < ttl_s <= PROXY_SESSION_MAX_TTL_SECONDS for ttl_s in renewals)
    assert all(held["revoked_at"] is None for held in fake.state.sessions.values())


async def test_close_leaves_a_revoked_or_expired_session_of_a_detached_turn(
    fake: Starlette, proxy: ProxySessions, seeded: _Seeded
) -> None:
    sessions = _sessions(seeded, proxy)
    base = await sessions.reconcile("turn")
    acting = await sessions.reconcile(seeded.member_b)
    assert base is not None and acting is not None
    fake.state.sessions[base.id]["revoked_at"] = datetime.now(UTC)
    fake.state.sessions[acting.id]["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    with ws(seeded.turn.workspace_id):
        await mark_detached(seeded.turn, "build", "/home/user/.ufo/runs/build")
    sent = len(fake.state.calls)

    await _commit(seeded, "done")
    await sessions.close()

    assert _calls(fake)[sent:] == [("GET", f"/v1/sessions?labels.turn={seeded.turn.id}&limit=200")]


async def test_close_logs_and_never_raises(
    fake: Starlette,
    proxy: ProxySessions,
    seeded: _Seeded,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sessions = _sessions(seeded, proxy)
    await sessions.reconcile("turn")
    fake.state.fault = 503

    await _commit(seeded, "done")
    with caplog.at_level(logging.WARNING, logger="ufo"):
        await sessions.close()

    failed = [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "turn.sessions.close_failed"
    ]
    assert [entry["error_class"] for entry in failed] == ["SandboxProviderUnavailable"]


async def test_an_outage_at_open_is_a_provider_outage(
    fake: Starlette, proxy: ProxySessions, seeded: _Seeded
) -> None:
    fake.state.fault = 503

    with pytest.raises(SandboxProviderUnavailable):
        await _sessions(seeded, proxy).open()
    with pytest.raises(SandboxProviderUnavailable):
        await _sessions(seeded, proxy).reconcile(seeded.member_b)


async def test_a_turn_with_no_proxy_opens_nothing(seeded: _Seeded) -> None:
    sessions = _sessions(seeded, None)

    assert await sessions.open() is None
    assert await sessions.reconcile(seeded.member_b) is None
    await sessions.close()
    assert sessions.opened == {}


async def test_a_reopen_after_park_renews_a_near_deadline(
    fake: Starlette, proxy: ProxySessions, seeded: _Seeded
) -> None:
    parked = await _sessions(seeded, proxy).open()
    assert parked is not None
    near = datetime.now(UTC) + timedelta(minutes=10)
    fake.state.sessions[parked.id]["expires_at"] = near

    resumed = await _sessions(seeded, proxy).open()

    assert resumed is not None
    assert resumed.id == parked.id
    assert _calls(fake) == [
        ("POST", "/v1/sessions"),
        ("POST", "/v1/sessions"),
        ("POST", f"/v1/sessions/{parked.id}/renew"),
    ]
    assert resumed.expires_at > near + timedelta(minutes=30)


async def test_reconcile_reopens_a_session_that_expired_between_dispatches(
    fake: Starlette, proxy: ProxySessions, seeded: _Seeded
) -> None:
    sessions = _sessions(seeded, proxy)
    expired = await sessions.reconcile("turn")
    assert expired is not None
    fake.state.sessions[expired.id]["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    sent = len(fake.state.calls)

    successor = await sessions.reconcile("turn")
    again = await sessions.reconcile("turn")

    assert successor is not None and again is not None
    assert successor.id != expired.id
    assert again.id == successor.id
    reopened = fake.state.calls[sent:]
    assert [(method, target) for method, target, _, _ in reopened] == [
        ("POST", f"/v1/sessions/{expired.id}/renew"),
        ("POST", "/v1/sessions"),
        ("POST", "/v1/sessions"),
        ("POST", f"/v1/sessions/{successor.id}/renew"),
    ]
    keys = [headers.get(IDEMPOTENCY_HEADER.lower()) for _, _, headers, _ in reopened[1:3]]
    assert keys[1] == f"{keys[0]}:1"


async def test_reconcile_never_reopens_a_revoked_session(
    fake: Starlette, proxy: ProxySessions, seeded: _Seeded
) -> None:
    sessions = _sessions(seeded, proxy)
    revoked = await sessions.reconcile("turn")
    assert revoked is not None
    fake.state.sessions[revoked.id]["revoked_at"] = datetime.now(UTC)

    with pytest.raises(ProxyRefused) as refused:
        await sessions.reconcile("turn")

    assert refused.value.error.code == SESSION_REVOKED_CODE
    assert len(fake.state.sessions) == 1


async def test_close_leaves_the_sessions_of_a_turn_whose_terminal_is_not_committed(
    fake: Starlette, proxy: ProxySessions, seeded: _Seeded
) -> None:
    sessions = _sessions(seeded, proxy)
    opened = await sessions.reconcile("turn")
    assert opened is not None
    sent = len(fake.state.calls)

    for status in ("running", "parked", "queued"):
        await _commit(seeded, status)
        await sessions.close()
    left = fake.state.calls[sent:]
    await _commit(seeded, "cancelled")
    await sessions.close()

    assert left == []
    assert fake.state.sessions[opened.id]["revoked_at"] is not None
