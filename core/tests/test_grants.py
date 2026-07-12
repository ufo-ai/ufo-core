import asyncio
import base64
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ufo.db import workspace_tx
from ufo.grants import (
    ConnectFlow,
    ConnectStateInvalid,
    ConnectUnavailable,
    Grant,
    GrantStore,
    OAuthAccount,
    UnknownProvider,
    grant_summaries,
    install_connect_flow,
)
from ufo.sandbox.proxy.rules import (
    GRANT_METER_DIMENSION,
    InjectionRule,
    MeterRule,
    ScopeRule,
    derive_grant_rules,
)
from ufo.sandbox.proxy.server import EgressProxy, PerAgentRules, generate_ca
from ufo.sandbox.session import RunToken
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.surfaces.cli import router
from ufo.tools.builtins import ConnectAccountInput, connect_account_handler
from ufo.tools.context import ToolContext

GRANTED_HOST = "api.granted.test"
UNGRANTED_HOST = "api.ungranted.test"
HOST_A = "api.aaa.test"
HOST_B = "api.bbb.test"
REDIRECT_URI = "http://surface/v1/connect/callback"


@pytest.fixture(autouse=True)
def _reset_connect_flow() -> Iterator[None]:
    yield
    install_connect_flow(None)


@dataclass(frozen=True)
class StubProvider:
    """The injected OAuth descriptor for the proof — stands in for a connector extension's provider,
    never the thing asserted. `authorize_url` echoes the sealed state so the test can drive the
    callback; `exchange` yields a fixed connected-account id (the broker holds the token)."""

    provider: str = "stub"
    host: str = GRANTED_HOST
    account_id: str = "acct-42"

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return f"https://stub.test/oauth?state={state}&redirect_uri={redirect_uri}"

    async def exchange(
        self, code: str, redirect_uri: str, workspace_id: UUID, state: str
    ) -> OAuthAccount:
        return OAuthAccount(account_id=self.account_id)


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _member_agent(workspace_id: UUID) -> tuple[UUID, UUID]:
    member_id, agent_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=f"{member_id.hex[:8]}@x.test",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return member_id, agent_id


def test_derive_admits_and_meters_the_granted_host_without_injecting() -> None:
    """A grant admits and meters its host but injects nothing — the broker holds the account's token
    and runs connector tools server-side, so no secret is on the wire."""
    grant = Grant(provider="stub", account_id="acct-42", host=GRANTED_HOST)
    rules = derive_grant_rules((grant,))
    scope = next(r for r in rules if isinstance(r, ScopeRule))
    assert scope.allowed_hosts == frozenset({GRANTED_HOST})
    assert MeterRule(host=GRANTED_HOST, dimension=GRANT_METER_DIMENSION) in rules
    assert not any(isinstance(r, InjectionRule) for r in rules)


def test_no_grants_derive_no_rules() -> None:
    assert derive_grant_rules(()) == ()


async def test_grant_round_trips_carrying_only_the_account_id(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    await store.record(
        workspace_id=workspace_id,
        agent_id=agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
    )
    grants = await store.active_grants(workspace_id, agent_id)
    assert grants == (Grant(provider="stub", account_id="acct-42", host=GRANTED_HOST),)


async def test_record_rejects_a_control_char_account_id(db: None) -> None:
    """The account_id is external (the provider's OAuth exchange) and a connector tool sends it to
    the broker, so a CR/LF that could forge a broker request is refused at record."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    with pytest.raises(ValueError, match="control character"):
        await GrantStore().record(
            workspace_id=workspace_id,
            agent_id=agent_id,
            provider="stub",
            account_id="acct-42\r\nX-Injected: 1",
            host=GRANTED_HOST,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
        )


async def test_reconnecting_the_same_account_updates_not_duplicates(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    for host in (HOST_A, HOST_B):
        await store.record(
            workspace_id=workspace_id,
            agent_id=agent_id,
            provider="stub",
            account_id="acct-42",
            host=host,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
        )
    async with workspace_tx() as connection:
        count = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.grant))
        ).scalar_one()
    assert count == 1
    grants = await store.active_grants(workspace_id, agent_id)
    assert grants[0].host == HOST_B


async def test_connect_flow_records_a_durable_grant_with_account_id(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    flow = ConnectFlow(
        providers={"stub": StubProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
    )
    url = flow.authorize(
        workspace_id=workspace_id,
        agent_id=agent_id,
        provider="stub",
        grantor_member_id=member_id,
        conversation_id=conversation_id,
    )
    state = parse_qs(urlparse(url).query)["state"][0]
    recorded = await flow.complete(state=state, code="the-code")
    assert (recorded.provider, recorded.account_id, recorded.agent_id) == (
        "stub",
        "acct-42",
        agent_id,
    )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.grant.c.account_id,
                    tables.grant.c.host,
                    tables.grant.c.grantor_member_id,
                ).where(tables.grant.c.workspace_id == workspace_id)
            )
        ).one()
    assert (row.account_id, row.host, row.grantor_member_id) == (
        "acct-42",
        GRANTED_HOST,
        member_id,
    )


async def test_tampered_connect_state_is_refused() -> None:
    flow = ConnectFlow(
        providers={"stub": StubProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
    )
    with pytest.raises(ConnectStateInvalid):
        await flow.complete(state="not-a-sealed-token", code="x")


def test_unknown_provider_is_rejected() -> None:
    flow = ConnectFlow(
        providers={},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
    )
    with pytest.raises(UnknownProvider):
        flow.authorize(
            workspace_id=uuid4(),
            agent_id=uuid4(),
            provider="nope",
            grantor_member_id=uuid4(),
            conversation_id=uuid4(),
        )


async def test_grant_summaries_expose_the_audit_view(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    await GrantStore().record(
        workspace_id=workspace_id,
        agent_id=agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
    )
    summaries = await grant_summaries(workspace_id)
    assert len(summaries) == 1
    summary = summaries[0]
    assert (summary.agent, summary.provider, summary.account_id) == ("assistant", "stub", "acct-42")
    assert (summary.grantor_member_id, summary.conversation_id) == (member_id, conversation_id)


async def test_proxy_admits_the_granted_host_and_blocks_the_ungranted() -> None:
    """A grant admits its host — the resolved rule set carries a ScopeRule for it — while an
    ungranted host is refused at CONNECT (403). No token is injected."""
    grant = Grant(provider="stub", account_id="acct-42", host=GRANTED_HOST)
    resolver = PerAgentRules(base=derive_grant_rules((grant,)), grants=None)
    cert, key = await generate_ca()
    proxy = EgressProxy(
        resolve=resolver.resolve, authorize=resolver.turn_live, ca_cert=cert, ca_key=key
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    try:
        assert await _connect_status(endpoint.port, UNGRANTED_HOST) == 403
        rules = await proxy._rules_for(None)
    finally:
        await proxy.stop()
    assert any(isinstance(r, ScopeRule) and GRANTED_HOST in r.allowed_hosts for r in rules)
    assert not any(isinstance(r, InjectionRule) for r in rules)


async def test_agent_a_reaches_only_its_own_granted_host(db: None) -> None:
    """Per-agent isolation on the wire: agent A's turn resolves a ScopeRule for A's granted host and
    none for agent B's, so A is refused B's host and an ungranted host at CONNECT (403)."""
    workspace_id = await _workspace()
    member_id, agent_a = await _member_agent(workspace_id)
    agent_b = await _agent(workspace_id, "assistant-b")
    conversation_id = await _conversation(workspace_id, member_id)
    turn_a = await _turn(workspace_id, agent_a, conversation_id)
    store = GrantStore()
    await store.record(
        workspace_id=workspace_id,
        agent_id=agent_a,
        provider="stub",
        account_id="acct-a",
        host=HOST_A,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
    )
    await store.record(
        workspace_id=workspace_id,
        agent_id=agent_b,
        provider="stub",
        account_id="acct-b",
        host=HOST_B,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
    )
    resolver = PerAgentRules(base=(), grants=store)
    cert, key = await generate_ca()
    proxy = EgressProxy(
        resolve=resolver.resolve, authorize=resolver.turn_live, ca_cert=cert, ca_key=key
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    try:
        run_a = RunToken(workspace_id, turn_a).encode()
        assert await _connect_status(endpoint.port, HOST_B, run_a) == 403
        assert await _connect_status(endpoint.port, UNGRANTED_HOST, run_a) == 403
        rules_a = await proxy._rules_for(RunToken(workspace_id, turn_a))
    finally:
        await proxy.stop()
    assert any(isinstance(r, ScopeRule) and HOST_A in r.allowed_hosts for r in rules_a)
    assert not any(isinstance(r, ScopeRule) and HOST_B in r.allowed_hosts for r in rules_a)


async def test_a_grant_recorded_after_start_is_live_for_the_next_turn(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    turn_1 = await _turn(workspace_id, agent_id, conversation_id, seq=1)
    turn_2 = await _turn(workspace_id, agent_id, conversation_id, seq=2)
    store = GrantStore()
    resolver = PerAgentRules(base=(), grants=store)
    cert, key = await generate_ca()
    proxy = EgressProxy(
        resolve=resolver.resolve, authorize=resolver.turn_live, ca_cert=cert, ca_key=key
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    try:
        assert (
            await _connect_status(endpoint.port, HOST_A, RunToken(workspace_id, turn_1).encode())
            == 403
        )
        await store.record(
            workspace_id=workspace_id,
            agent_id=agent_id,
            provider="stub",
            account_id="acct-a",
            host=HOST_A,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
        )
        rules_2 = await proxy._rules_for(RunToken(workspace_id, turn_2))
    finally:
        await proxy.stop()
    assert any(isinstance(r, ScopeRule) and HOST_A in r.allowed_hosts for r in rules_2)


def _turn_context(
    workspace_id: UUID, agent_id: UUID, conversation_id: UUID, member_id: UUID | None
) -> ToolContext:
    """A tool context whose only live fields the connect tool reads are the turn (workspace, agent,
    conversation) and the speaking member; the sandbox and other capabilities the tool never touches
    stay unset."""
    turn = Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        agent_id=agent_id,
        seq=1,
        status="running",
        inbound="connect my gmail",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        member_id=member_id,
        artifact_token_secret="",
    )


async def test_connect_account_tool_yields_authorize_url_and_callback_binds_the_speaker(
    db: None,
) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    install_connect_flow(
        ConnectFlow(
            providers={"stub": StubProvider()},
            fernet=Fernet(Fernet.generate_key()),
            store=GrantStore(),
            redirect_uri=REDIRECT_URI,
        )
    )
    ctx = _turn_context(workspace_id, agent_id, conversation_id, member_id)
    result = await connect_account_handler(ctx, ConnectAccountInput(provider="stub"))
    url = result.content[0].text
    assert result.is_error is False
    assert url.startswith("https://stub.test/oauth")
    state = parse_qs(urlparse(url).query)["state"][0]
    app = FastAPI()
    app.state.shared_workspace = False
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://surface") as client:
        for _ in range(2):
            done = await client.get(
                "/v1/connect/callback", params={"state": state, "code": "the-code"}
            )
            assert done.status_code == 200
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.grant.c.account_id,
                    tables.grant.c.agent_id,
                    tables.grant.c.grantor_member_id,
                    tables.grant.c.conversation_id,
                ).where(tables.grant.c.workspace_id == workspace_id)
            )
        ).all()
    assert len(rows) == 1
    assert (rows[0].account_id, rows[0].agent_id) == ("acct-42", agent_id)
    assert (rows[0].grantor_member_id, rows[0].conversation_id) == (member_id, conversation_id)


async def test_connect_account_without_a_speaker_is_refused() -> None:
    ctx = _turn_context(uuid4(), uuid4(), uuid4(), None)
    with pytest.raises(ValueError, match="speaking member"):
        await connect_account_handler(ctx, ConnectAccountInput(provider="stub"))


async def test_connect_account_without_an_installed_flow_raises() -> None:
    install_connect_flow(None)
    ctx = _turn_context(uuid4(), uuid4(), uuid4(), uuid4())
    with pytest.raises(ConnectUnavailable):
        await connect_account_handler(ctx, ConnectAccountInput(provider="stub"))


async def test_the_begin_route_is_gone_and_the_callback_reports_unavailable_without_a_flow() -> (
    None
):
    install_connect_flow(None)
    app = FastAPI()
    app.state.shared_workspace = False
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://surface") as client:
        begun = await client.post("/v1/connect", params={"provider": "stub"})
        callback = await client.get("/v1/connect/callback", params={"state": "s", "code": "c"})
    assert begun.status_code == 404
    assert callback.status_code == 503


async def _agent(workspace_id: UUID, name: str) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=name,
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


async def _turn(workspace_id: UUID, agent_id: UUID, conversation_id: UUID, seq: int = 1) -> UUID:
    turn_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=seq,
                status="running",
                inbound="hi",
                terminal=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn_id


async def _conversation(workspace_id: UUID, member_id: UUID) -> UUID:
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id


def _basic(run_token: str) -> str:
    return "Basic " + base64.b64encode(f"{run_token}:".encode()).decode()


async def _connect_status(port: int, host: str, run_token: str = "") -> int:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    head = f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}\r\n"
    if run_token:
        head += f"Proxy-Authorization: {_basic(run_token)}\r\n"
    writer.write((head + "\r\n").encode())
    await writer.drain()
    status_line = await reader.readline()
    writer.close()
    return int(status_line.split()[1])
