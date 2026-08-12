import asyncio
import base64
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ufo.agent_scope import AgentUnbound, agent
from ufo.audience import conversation_audience
from ufo.connectors import CliCredential, ForwardedResponse
from ufo.db import workspace_tx
from ufo.grants import (
    ConnectFlow,
    ConnectHandoff,
    ConnectionOwnedByAnotherMember,
    ConnectionPermissionDenied,
    ConnectRequestInvalid,
    ConnectStateInvalid,
    ConnectUnavailable,
    Grant,
    GrantStore,
    GrantSummary,
    OAuthAccount,
    UnknownProvider,
    grant_summaries,
    install_connect_flow,
)
from ufo.sandbox.proxy.rules import (
    REQUEST_METER_DIMENSION,
    ConnectorTransferHosts,
    ForwardRule,
    InjectionRule,
    InternetRule,
    MeterRule,
    ScopeRule,
    derive_grant_rules,
)
from ufo.sandbox.proxy.server import EgressProxy, PerAgentRules, generate_ca
from ufo.sandbox.session import RunToken, RunTokenCodec
from ufo.schema import tables
from ufo.schema.records import Agent, ConnectRequest, TerminalFrame, Turn
from ufo.surfaces.cli import callback_router
from ufo.tools.builtins import ConnectAccountInput, connect_account_handler
from ufo.tools.context import ToolContext
from ufo.workspace import ws

GRANTED_HOST = "api.granted.test"
UNGRANTED_HOST = "api.ungranted.test"
HOST_A = "api.aaa.test"
HOST_B = "api.bbb.test"
REDIRECT_URI = "http://surface/v1/connect/callback"
RUN_TOKENS = RunTokenCodec(b"grants-test-run-token-secret")


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
    account_label: str | None = "Work account"

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return f"https://stub.test/oauth?state={state}&redirect_uri={redirect_uri}"

    async def exchange(
        self, code: str, redirect_uri: str, workspace_id: UUID, state: str
    ) -> OAuthAccount:
        return OAuthAccount(account_id=self.account_id, account_label=self.account_label)


class _ForbiddenForwarder:
    async def forward(
        self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes
    ) -> ForwardedResponse:
        raise AssertionError("cross-agent forwarding")


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


async def _record(
    store: GrantStore,
    workspace_id: UUID,
    agent_id: UUID,
    provider: str,
    account_id: str,
    host: str,
    grantor_member_id: UUID,
    conversation_id: UUID,
    shared: bool,
) -> None:
    with ws(workspace_id), agent(agent_id):
        await store.record(
            provider=provider,
            account_id=account_id,
            host=host,
            grantor_member_id=grantor_member_id,
            conversation_id=conversation_id,
            shared=shared,
        )


async def _active(store: GrantStore, workspace_id: UUID, agent_id: UUID) -> tuple[Grant, ...]:
    with ws(workspace_id), agent(agent_id):
        return await store.active_grants()


async def _set_shared(
    store: GrantStore,
    workspace_id: UUID,
    agent_id: UUID,
    actor_member_id: UUID,
    grant_id: UUID,
    shared: bool,
) -> bool:
    with ws(workspace_id), agent(agent_id):
        return await store.set_shared(
            grant_id,
            shared,
            actor_member_id=actor_member_id,
        )


async def _revoke(
    store: GrantStore,
    workspace_id: UUID,
    agent_id: UUID,
    actor_member_id: UUID,
    grant_id: UUID,
) -> bool:
    with ws(workspace_id), agent(agent_id):
        return await store.revoke(
            grant_id,
            actor_member_id=actor_member_id,
        )


async def _summaries(workspace_id: UUID, agent_id: UUID) -> tuple[GrantSummary, ...]:
    with ws(workspace_id), agent(agent_id):
        return await grant_summaries()


async def test_grant_store_requires_an_agent_boundary(db: None) -> None:
    with ws(await _workspace()), pytest.raises(AgentUnbound):
        await GrantStore().active_grants()


def test_derive_admits_and_meters_the_granted_host_without_injecting() -> None:
    """A grant admits and meters its host but injects nothing — the broker holds the account's token
    and runs connector tools server-side, so no secret is on the wire."""
    grant = Grant(
        id=uuid4(),
        connection_id=uuid4(),
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        owner_member_id=uuid4(),
        connection_shared=False,
    )
    rules = derive_grant_rules((grant,))
    scope = next(r for r in rules if isinstance(r, ScopeRule))
    assert scope.allowed_hosts == frozenset({GRANTED_HOST})
    assert MeterRule(host=GRANTED_HOST, dimension=REQUEST_METER_DIMENSION) in rules
    assert not any(isinstance(r, InjectionRule) for r in rules)


def test_no_grants_derive_no_rules() -> None:
    assert derive_grant_rules(()) == ()


TRANSFER_HOST = "stash.broker.test"


def test_derive_admits_the_providers_transfer_hosts_with_the_grant() -> None:
    """A grant also admits and meters its broker's declared file-store hosts — where the sandbox
    fetches a tool's presigned file outputs and stages its file inputs — still injecting nothing."""
    grant = Grant(
        id=uuid4(),
        connection_id=uuid4(),
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        owner_member_id=uuid4(),
        connection_shared=False,
    )
    rules = derive_grant_rules((grant,), ConnectorTransferHosts({"stub": (TRANSFER_HOST,)}))
    scope = next(r for r in rules if isinstance(r, ScopeRule))
    assert scope.allowed_hosts == frozenset({GRANTED_HOST, TRANSFER_HOST})
    assert MeterRule(host=TRANSFER_HOST, dimension=REQUEST_METER_DIMENSION) in rules
    assert not any(isinstance(r, InjectionRule) for r in rules)


def test_derive_ignores_another_providers_transfer_hosts() -> None:
    grant = Grant(
        id=uuid4(),
        connection_id=uuid4(),
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        owner_member_id=uuid4(),
        connection_shared=False,
    )
    rules = derive_grant_rules((grant,), ConnectorTransferHosts({"other": (TRANSFER_HOST,)}))
    scope = next(r for r in rules if isinstance(r, ScopeRule))
    assert scope.allowed_hosts == frozenset({GRANTED_HOST})


async def test_resolver_folds_the_transfer_hosts_into_the_turns_rules(db: None) -> None:
    """The per-turn resolver carries the manifests' provider→transfer-hosts map, so a granted
    provider's broker file store is reachable for exactly the turns its grant covers."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    turn_id = await _turn(workspace_id, agent_id, conversation_id)
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    resolver = PerAgentRules(
        base=(), grants=store, transfer_hosts=ConnectorTransferHosts({"stub": (TRANSFER_HOST,)})
    )
    rules = await resolver.resolve(RunToken(workspace_id, turn_id))
    assert any(isinstance(r, ScopeRule) and TRANSFER_HOST in r.allowed_hosts for r in rules)
    assert not any(isinstance(r, InjectionRule) for r in rules)


async def test_grant_round_trips_carrying_only_the_account_id(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    grants = await _active(store, workspace_id, agent_id)
    assert grants == (
        Grant(
            id=grants[0].id,
            connection_id=grants[0].connection_id,
            provider="stub",
            account_id="acct-42",
            host=GRANTED_HOST,
            owner_member_id=member_id,
            connection_shared=False,
        ),
    )


async def test_record_rejects_a_control_char_account_id(db: None) -> None:
    """The account_id is external (the provider's OAuth exchange) and a connector tool sends it to
    the broker, so a CR/LF that could forge a broker request is refused at record."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    with pytest.raises(ValueError, match="control character"):
        await _record(
            GrantStore(),
            workspace_id,
            agent_id,
            provider="stub",
            account_id="acct-42\r\nX-Injected: 1",
            host=GRANTED_HOST,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=False,
        )


async def test_reconnecting_the_same_account_updates_not_duplicates(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    for host in (HOST_A, HOST_B):
        await _record(
            store,
            workspace_id,
            agent_id,
            provider="stub",
            account_id="acct-42",
            host=host,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=False,
        )
    async with workspace_tx() as connection:
        connection_count = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.connection))
        ).scalar_one()
        grant_count = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.connector_grant))
        ).scalar_one()
    assert (connection_count, grant_count) == (1, 1)
    grants = await _active(store, workspace_id, agent_id)
    assert grants[0].host == HOST_B


async def test_reconnecting_an_owned_account_cannot_reassign_it(db: None) -> None:
    workspace_id = await _workspace()
    owner_id, agent_id = await _member_agent(workspace_id)
    other_id = uuid4()
    conversation_id = await _conversation(workspace_id, owner_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=other_id,
                workspace_id=workspace_id,
                email="other@x.test",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=HOST_A,
        grantor_member_id=owner_id,
        conversation_id=conversation_id,
        shared=False,
    )
    with pytest.raises(ConnectionOwnedByAnotherMember):
        await _record(
            store,
            workspace_id,
            agent_id,
            provider="stub",
            account_id="acct-42",
            host=HOST_B,
            grantor_member_id=other_id,
            conversation_id=conversation_id,
            shared=True,
        )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.connection.c.owner_member_id,
                    tables.connection.c.host,
                    tables.connection.c.shared,
                ).select_from(
                    tables.connection.join(
                        tables.connector_grant,
                        tables.connector_grant.c.connection_id == tables.connection.c.id,
                    )
                )
            )
        ).one()
    assert (row.owner_member_id, row.host, row.shared) == (owner_id, HOST_A, False)


async def test_reconnect_never_narrows_a_shared_connection(db: None) -> None:
    """The owner reconnects a workspace-shared account for another agent with shared=False —
    the connect tool's default — and the connection stays shared: reconnect widens only."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=HOST_A,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=HOST_A,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=True,
    )
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=HOST_A,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    async with workspace_tx() as connection:
        row = (await connection.execute(sa.select(tables.connection.c.shared))).one()
    assert bool(row.shared) is True


async def test_connect_flow_records_a_durable_grant_with_account_label(db: None) -> None:
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
        shared=False,
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
                    tables.connection.c.account_id,
                    tables.connection.c.account_label,
                    tables.connection.c.host,
                    tables.connection.c.owner_member_id,
                ).where(tables.connection.c.workspace_id == workspace_id)
            )
        ).one()
    assert (row.account_id, row.account_label, row.host, row.owner_member_id) == (
        "acct-42",
        "Work account",
        GRANTED_HOST,
        member_id,
    )


async def test_connect_flow_records_null_account_label(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    flow = ConnectFlow(
        providers={"stub": StubProvider(account_label=None)},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
    )
    state = parse_qs(
        urlparse(
            flow.authorize(
                workspace_id=workspace_id,
                agent_id=agent_id,
                provider="stub",
                grantor_member_id=member_id,
                conversation_id=conversation_id,
                shared=False,
            )
        ).query
    )["state"][0]
    await flow.complete(state=state, code="the-code")
    async with workspace_tx() as connection:
        label = (
            await connection.execute(
                sa.select(tables.connection.c.account_label).where(
                    tables.connection.c.workspace_id == workspace_id
                )
            )
        ).scalar_one()
    assert label is None


async def test_reconnect_refreshes_account_label(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)

    async def complete(label: str) -> None:
        flow = ConnectFlow(
            providers={"stub": StubProvider(account_label=label)},
            fernet=Fernet(Fernet.generate_key()),
            store=GrantStore(),
            redirect_uri=REDIRECT_URI,
        )
        state = parse_qs(
            urlparse(
                flow.authorize(
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    provider="stub",
                    grantor_member_id=member_id,
                    conversation_id=conversation_id,
                    shared=False,
                )
            ).query
        )["state"][0]
        await flow.complete(state=state, code="the-code")

    await complete("Work account")
    await complete("Personal account")
    async with workspace_tx() as connection:
        label = (
            await connection.execute(
                sa.select(tables.connection.c.account_label).where(
                    tables.connection.c.workspace_id == workspace_id
                )
            )
        ).scalar_one()
    assert label == "Personal account"


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
            shared=False,
        )


async def test_grant_summaries_expose_the_audit_view(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    await _record(
        GrantStore(),
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    summaries = await _summaries(workspace_id, agent_id)
    assert len(summaries) == 1
    summary = summaries[0]
    assert (summary.agent, summary.provider, summary.account_id) == ("assistant", "stub", "acct-42")
    assert (summary.owner_member_id, summary.conversation_id) == (member_id, conversation_id)


async def test_proxy_resolves_the_granted_host_but_blocks_tokenless_connect() -> None:
    """A grant contributes an exact host rule but no unsigned caller can exercise it."""
    grant = Grant(
        id=uuid4(),
        connection_id=uuid4(),
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        owner_member_id=uuid4(),
        connection_shared=False,
    )
    resolver = PerAgentRules(base=derive_grant_rules((grant,)), grants=None)
    cert, key = await generate_ca()
    proxy = EgressProxy(
        resolve=resolver.resolve,
        authorize=resolver.turn_live,
        ca_cert=cert,
        ca_key=key,
        run_tokens=RUN_TOKENS,
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    try:
        assert await _connect_status(endpoint.port, GRANTED_HOST) == 403
        rules = await proxy._rules_for(None)
    finally:
        await proxy.stop()
    assert any(isinstance(r, ScopeRule) and GRANTED_HOST in r.allowed_hosts for r in rules)
    assert not any(isinstance(r, InjectionRule) for r in rules)


async def test_agent_a_authenticates_only_to_its_own_granted_host(db: None) -> None:
    """Per-agent authentication isolation: agent A gets an exact rule for A's provider only. The
    public internet rule may reach B's host opaquely, but cannot forward or inject B's account."""
    workspace_id = await _workspace()
    member_id, agent_a = await _member_agent(workspace_id)
    agent_b = await _agent(workspace_id, "assistant-b")
    conversation_id = await _conversation(workspace_id, member_id)
    turn_a = await _turn(workspace_id, agent_a, conversation_id)
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_a,
        provider="stub",
        account_id="acct-a",
        host=HOST_A,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    await _record(
        store,
        workspace_id,
        agent_b,
        provider="stub",
        account_id="acct-b",
        host=HOST_B,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    resolver = PerAgentRules(
        base=(),
        grants=store,
        internet=(InternetRule(),),
        clis={
            "stub": CliCredential(
                env="STUB_TOKEN", header="authorization", forward=_ForbiddenForwarder()
            )
        },
    )
    rules_a = await resolver.resolve(RunToken(workspace_id, turn_a))
    assert any(isinstance(r, ScopeRule) and HOST_A in r.allowed_hosts for r in rules_a)
    assert not any(isinstance(r, ScopeRule) and HOST_B in r.allowed_hosts for r in rules_a)
    assert not any(isinstance(r, ForwardRule) and r.host == HOST_B for r in rules_a)
    assert InternetRule() in rules_a

    async def local_public(_host: str, _port: int) -> str:
        return "127.0.0.1"

    received = asyncio.Future[bytes]()

    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        received.set_result(await reader.read())
        writer.close()

    stub = await asyncio.start_server(upstream, "127.0.0.1", 0)
    stub_port = stub.sockets[0].getsockname()[1]
    cert, key = await generate_ca()
    proxy = EgressProxy(
        resolve=resolver.resolve,
        authorize=resolver.turn_live,
        ca_cert=cert,
        ca_key=key,
        run_tokens=RUN_TOKENS,
        resolve_public=local_public,
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    try:
        run_a = RUN_TOKENS.encode(RunToken(workspace_id, turn_a))
        assert await _connect_status(endpoint.port, HOST_B, run_a, stub_port, b"opaque") == 200
        assert await received == b"opaque"
    finally:
        await proxy.stop()
        stub.close()
        await stub.wait_closed()


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
        resolve=resolver.resolve,
        authorize=resolver.turn_live,
        ca_cert=cert,
        ca_key=key,
        run_tokens=RUN_TOKENS,
    )
    endpoint = await proxy.start(bind_host="127.0.0.1")
    try:
        assert (
            await _connect_status(
                endpoint.port, HOST_A, RUN_TOKENS.encode(RunToken(workspace_id, turn_1))
            )
            == 403
        )
        await _record(
            store,
            workspace_id,
            agent_id,
            provider="stub",
            account_id="acct-a",
            host=HOST_A,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=False,
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
        created_at=datetime.now(UTC),
    )
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=member_id,
        audience=conversation_audience(member_id),
        artifact_token_secret="",
    )


async def test_connect_account_handoff_is_private_memoized_and_binds_the_speaker(
    db: None,
) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    flow = ConnectFlow(
        providers={"stub": StubProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
    )
    install_connect_flow(flow)
    ctx = _turn_context(workspace_id, agent_id, conversation_id, member_id)
    result = await connect_account_handler(
        ctx, ConnectAccountInput(provider="stub", user_description="connecting their account")
    )
    assert result.is_error is False
    tool_text = result.content[0].text
    assert "https://" not in tool_text
    request = ConnectRequest.model_validate_json(tool_text.splitlines()[1])
    terminal = TerminalFrame(status="done", connect_request=request)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=ctx.turn.id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="connect my gmail",
                admission_source="member",
                speaker_member_id=member_id,
                terminal=terminal.model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with pytest.raises(ConnectRequestInvalid, match="another member"):
        await ConnectHandoff(flow).authorize(workspace_id, ctx.turn.id, uuid4())
    urls = await asyncio.gather(
        ConnectHandoff(flow).authorize(workspace_id, ctx.turn.id, member_id),
        ConnectHandoff(flow).authorize(workspace_id, ctx.turn.id, member_id),
    )
    assert urls[0] == urls[1]
    url = urls[0]
    assert url.startswith("https://stub.test/oauth")
    assert await ConnectHandoff(flow).authorize(workspace_id, ctx.turn.id, member_id) == url
    state = parse_qs(urlparse(url).query)["state"][0]
    app = FastAPI()
    app.include_router(callback_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://surface") as client:
        for _ in range(2):
            done = await client.get(
                "/v1/connect/callback", params={"state": state, "code": "the-code"}
            )
            assert done.status_code == 200
            assert "return to chat and ask me to continue" in done.text
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.connection.c.account_id,
                    tables.connector_grant.c.agent_id,
                    tables.connection.c.owner_member_id,
                    tables.connector_grant.c.conversation_id,
                )
                .select_from(
                    tables.connector_grant.join(
                        tables.connection,
                        tables.connector_grant.c.connection_id == tables.connection.c.id,
                    )
                )
                .where(tables.connector_grant.c.workspace_id == workspace_id)
            )
        ).all()
    assert len(rows) == 1
    assert (rows[0].account_id, rows[0].agent_id) == ("acct-42", agent_id)
    assert (rows[0].owner_member_id, rows[0].conversation_id) == (member_id, conversation_id)


async def test_connect_handoff_expires_with_its_terminal_request(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    flow = ConnectFlow(
        providers={"stub": StubProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
    )
    turn_id = uuid4()
    terminal = TerminalFrame(
        status="done",
        connect_request=ConnectRequest(provider="stub", requester_member_id=member_id),
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="connect",
                admission_source="member",
                speaker_member_id=member_id,
                terminal=terminal.model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=datetime.now(UTC) - timedelta(minutes=11),
            )
        )
    with pytest.raises(ConnectRequestInvalid, match="expired"):
        await ConnectHandoff(flow).authorize(workspace_id, turn_id, member_id)


async def test_connect_handoff_replays_against_its_authorization_ttl(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    flow = ConnectFlow(
        providers={"stub": StubProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
    )
    turn_id = uuid4()
    terminal = TerminalFrame(
        status="done",
        connect_request=ConnectRequest(provider="stub", requester_member_id=member_id),
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="connect",
                admission_source="member",
                speaker_member_id=member_id,
                terminal=terminal.model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    handoff = ConnectHandoff(flow)
    url = await handoff.authorize(workspace_id, turn_id, member_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(updated_at=datetime.now(UTC) - timedelta(minutes=11))
            .where(tables.turn.c.id == turn_id)
        )
    assert await handoff.authorize(workspace_id, turn_id, member_id) == url
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(connect_authorized_at=datetime.now(UTC) - timedelta(minutes=11))
            .where(tables.turn.c.id == turn_id)
        )
    with pytest.raises(ConnectRequestInvalid, match="authorization has expired"):
        await handoff.authorize(workspace_id, turn_id, member_id)


async def test_connect_account_without_a_speaker_is_refused() -> None:
    ctx = _turn_context(uuid4(), uuid4(), uuid4(), None)
    with pytest.raises(ValueError, match="speaking member"):
        await connect_account_handler(
            ctx, ConnectAccountInput(provider="stub", user_description="connecting their account")
        )


async def test_connect_account_without_an_installed_flow_raises() -> None:
    install_connect_flow(None)
    ctx = _turn_context(uuid4(), uuid4(), uuid4(), uuid4())
    with pytest.raises(ConnectUnavailable):
        await connect_account_handler(
            ctx, ConnectAccountInput(provider="stub", user_description="connecting their account")
        )


async def test_the_begin_route_is_gone_and_the_callback_reports_unavailable_without_a_flow() -> (
    None
):
    install_connect_flow(None)
    app = FastAPI()
    app.include_router(callback_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://surface") as client:
        begun = await client.post("/v1/connect", params={"provider": "stub"})
        callback = await client.get("/v1/connect/callback", params={"state": "s", "code": "c"})
    assert begun.status_code == 404
    assert callback.status_code == 503


async def test_connect_flow_records_the_models_shared_decision(db: None) -> None:
    """The model decides disclosure at connect time: shared=True lands a workspace-shared grant,
    the default lands one private to its grantor."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    flow = ConnectFlow(
        providers={"stub": StubProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=store,
        redirect_uri=REDIRECT_URI,
    )
    url = flow.authorize(
        workspace_id=workspace_id,
        agent_id=agent_id,
        provider="stub",
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=True,
    )
    state = parse_qs(urlparse(url).query)["state"][0]
    await flow.complete(state=state, code="c")
    (grant,) = await _active(store, workspace_id, agent_id)
    assert grant.connection_shared is True
    assert grant.owner_member_id == member_id


async def test_a_grant_defaults_private_and_reconnect_updates_shared(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    for shared in (False, True):
        await _record(
            store,
            workspace_id,
            agent_id,
            provider="stub",
            account_id="acct-42",
            host=GRANTED_HOST,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=shared,
        )
    (grant,) = await _active(store, workspace_id, agent_id)
    assert grant.connection_shared is True
    (summary,) = await _summaries(workspace_id, agent_id)
    assert summary.shared is True


async def test_set_shared_flips_the_connection_for_every_attached_agent(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    second_agent = await _agent(workspace_id, "reviewer")
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    for target in (agent_id, second_agent):
        await _record(
            store,
            workspace_id,
            target,
            provider="stub",
            account_id="acct-42",
            host=GRANTED_HOST,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=False,
        )
    (initial,) = await _active(store, workspace_id, agent_id)
    assert await _set_shared(store, workspace_id, agent_id, member_id, initial.id, True) is True
    (flipped,) = await _active(store, workspace_id, agent_id)
    assert flipped.connection_shared is True
    (untouched,) = await _active(store, workspace_id, second_agent)
    assert untouched.connection_shared is True
    assert await _set_shared(store, workspace_id, agent_id, member_id, uuid4(), True) is False


async def test_attach_honors_ownership_and_sharing_and_never_widens(db: None) -> None:
    """The owner attaches their private connection to a second agent; another member is refused
    until the connection is shared, and an admin holds no escape past that refusal. A `shared`
    claim that would widen the connection refuses instead of silently dropping."""
    workspace_id = await _workspace()
    owner_id, agent_id = await _member_agent(workspace_id)
    second_agent = await _agent(workspace_id, "reviewer")
    other_id, admin_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member),
            (
                {
                    "id": other_id,
                    "workspace_id": workspace_id,
                    "email": "other@x.test",
                    "is_admin": False,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": admin_id,
                    "workspace_id": workspace_id,
                    "email": "admin@x.test",
                    "is_admin": True,
                    "created_at": now,
                    "updated_at": now,
                },
            ),
        )
    conversation_id = await _conversation(workspace_id, owner_id)
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=owner_id,
        conversation_id=conversation_id,
        shared=False,
    )
    with ws(workspace_id), agent(second_agent):
        for actor in (other_id, admin_id):
            with pytest.raises(ConnectionPermissionDenied):
                await store.attach(
                    provider="stub",
                    account_id="acct-42",
                    conversation_id=conversation_id,
                    actor_member_id=actor,
                    shared=False,
                )
        with pytest.raises(ValueError, match="attach cannot share"):
            await store.attach(
                provider="stub",
                account_id="acct-42",
                conversation_id=conversation_id,
                actor_member_id=owner_id,
                shared=True,
            )
        assert (
            await store.attach(
                provider="stub",
                account_id="acct-42",
                conversation_id=conversation_id,
                actor_member_id=owner_id,
                shared=False,
            )
            is True
        )
        assert (
            await store.attach(
                provider="stub",
                account_id="missing",
                conversation_id=conversation_id,
                actor_member_id=owner_id,
                shared=False,
            )
            is False
        )
    (attached,) = await _active(store, workspace_id, second_agent)
    assert attached.account_id == "acct-42"
    assert attached.connection_shared is False
    (initial,) = await _active(store, workspace_id, agent_id)
    assert await _set_shared(store, workspace_id, agent_id, owner_id, initial.id, True) is True
    with ws(workspace_id), agent(second_agent):
        assert (
            await store.attach(
                provider="stub",
                account_id="acct-42",
                conversation_id=conversation_id,
                actor_member_id=other_id,
                shared=True,
            )
            is True
        )


async def test_revoke_removes_only_the_named_agents_binding(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    second_agent = await _agent(workspace_id, "reviewer")
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    for target in (agent_id, second_agent):
        await _record(
            store,
            workspace_id,
            target,
            provider="stub",
            account_id="acct-42",
            host=GRANTED_HOST,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=False,
        )
    (initial,) = await _active(store, workspace_id, agent_id)
    assert await _revoke(store, workspace_id, agent_id, member_id, initial.id) is True
    assert await _active(store, workspace_id, agent_id) == ()
    (kept,) = await _active(store, workspace_id, second_agent)
    assert kept.account_id == "acct-42"
    assert await _revoke(store, workspace_id, agent_id, member_id, initial.id) is False


async def test_same_owner_replacements_refuse_stale_generations(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    stale_connection = (await _active(store, workspace_id, agent_id))[0]
    with ws(workspace_id), agent(agent_id):
        assert (
            await store.disconnect(
                stale_connection.connection_id,
                actor_member_id=member_id,
            )
            is True
        )
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    reconnected = (await _active(store, workspace_id, agent_id))[0]
    assert reconnected.connection_id != stale_connection.connection_id
    assert reconnected.id != stale_connection.id
    with ws(workspace_id), agent(agent_id):
        assert (
            await store.disconnect(
                stale_connection.connection_id,
                actor_member_id=member_id,
            )
            is False
        )
        assert await store.revoke(reconnected.id, actor_member_id=member_id) is True
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=True,
    )
    regranted = (await _active(store, workspace_id, agent_id))[0]
    assert regranted.connection_id == reconnected.connection_id
    assert regranted.id != reconnected.id
    with ws(workspace_id), agent(agent_id):
        assert await store.set_shared(reconnected.id, False, actor_member_id=member_id) is False
        assert await store.revoke(reconnected.id, actor_member_id=member_id) is False
    assert (await _active(store, workspace_id, agent_id)) == (regranted,)


async def test_admin_may_narrow_and_revoke_but_not_widen(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    admin_id = uuid4()
    conversation_id = await _conversation(workspace_id, member_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=admin_id,
                workspace_id=workspace_id,
                email="admin@x.test",
                is_admin=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    private = (await _active(store, workspace_id, agent_id))[0]
    with ws(workspace_id), agent(agent_id):
        with pytest.raises(ConnectionPermissionDenied):
            await store.set_shared(private.id, True, actor_member_id=admin_id)
        assert await store.set_shared(private.id, False, actor_member_id=admin_id) is True
        assert await store.set_shared(private.id, True, actor_member_id=member_id) is True
        assert await store.revoke(private.id, actor_member_id=admin_id) is True
    assert await _active(store, workspace_id, agent_id) == ()


async def test_stale_owner_mutation_cannot_touch_a_reconnected_account(db: None) -> None:
    workspace_id = await _workspace()
    alice, agent_id = await _member_agent(workspace_id)
    bob, admin = uuid4(), uuid4()
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member),
            (
                {
                    "id": bob,
                    "workspace_id": workspace_id,
                    "email": "bob@x.test",
                    "is_admin": False,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": admin,
                    "workspace_id": workspace_id,
                    "email": "admin@x.test",
                    "is_admin": True,
                    "created_at": now,
                    "updated_at": now,
                },
            ),
        )
    alice_conversation = await _conversation(workspace_id, alice)
    bob_conversation = await _conversation(workspace_id, bob)
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=alice,
        conversation_id=alice_conversation,
        shared=False,
    )
    stale = (await _active(store, workspace_id, agent_id))[0]
    assert (await _summaries(workspace_id, agent_id))[0].owner_member_id == alice
    with ws(workspace_id), agent(agent_id):
        assert await store.disconnect(stale.connection_id, actor_member_id=admin) is True
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=bob,
        conversation_id=bob_conversation,
        shared=False,
    )
    current = (await _active(store, workspace_id, agent_id))[0]
    with ws(workspace_id), agent(agent_id):
        assert await store.set_shared(stale.id, True, actor_member_id=alice) is False
        assert await store.revoke(stale.id, actor_member_id=alice) is False
        assert await store.disconnect(stale.connection_id, actor_member_id=alice) is False
        with pytest.raises(ConnectionPermissionDenied):
            await store.set_shared(
                current.id,
                True,
                actor_member_id=alice,
            )
        with pytest.raises(ConnectionPermissionDenied):
            await store.revoke(current.id, actor_member_id=alice)
        with pytest.raises(ConnectionPermissionDenied):
            await store.disconnect(current.connection_id, actor_member_id=alice)
    assert (current.owner_member_id, current.connection_shared) == (bob, False)


async def test_connect_account_carries_the_shared_intent(db: None) -> None:
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
    result = await connect_account_handler(
        ctx,
        ConnectAccountInput(
            provider="stub", shared=True, user_description="connecting the team account"
        ),
    )
    payload = json.loads(result.content[0].text.splitlines()[-1])
    assert payload == {
        "provider": "stub",
        "requester_member_id": str(member_id),
        "shared": True,
    }


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
                agent_id=sa.select(tables.agent.c.id)
                .where(tables.agent.c.workspace_id == workspace_id)
                .order_by(tables.agent.c.created_at, tables.agent.c.id)
                .limit(1)
                .scalar_subquery(),
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


async def _connect_status(
    port: int,
    host: str,
    run_token: str = "",
    target_port: int = 443,
    payload: bytes = b"",
) -> int:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    head = f"CONNECT {host}:{target_port} HTTP/1.1\r\nHost: {host}\r\n"
    if run_token:
        head += f"Proxy-Authorization: {_basic(run_token)}\r\n"
    writer.write((head + "\r\n").encode())
    await writer.drain()
    status_line = await reader.readline()
    writer.write(payload)
    await writer.drain()
    writer.close()
    return int(status_line.split()[1])


async def test_connector_accounts_admit_only_the_speakers_own_and_shared_grants(db: None) -> None:
    """The runtime check: a private grant resolves only for its grantor's turns; a shared grant
    for anyone's; a speakerless (scheduled/internal) turn sees only shared grants."""
    workspace_id = await _workspace()
    grantor_id, agent_id = await _member_agent(workspace_id)
    other_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=other_id,
                workspace_id=workspace_id,
                email="other@x.test",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    conversation_id = await _conversation(workspace_id, grantor_id)
    store = GrantStore()
    for account_id, member, shared in (
        ("acct-private", grantor_id, False),
        ("acct-shared", other_id, True),
        ("acct-other-private", other_id, False),
    ):
        await _record(
            store,
            workspace_id,
            agent_id,
            provider="stub",
            account_id=account_id,
            host=GRANTED_HOST,
            grantor_member_id=member,
            conversation_id=conversation_id,
            shared=shared,
        )
    ctx = _turn_context(workspace_id, agent_id, conversation_id, grantor_id)
    ctx = replace(ctx, grants=store)
    with ws(workspace_id), agent(agent_id):
        assert await ctx.connector_accounts("stub") == ("acct-private", "acct-shared")
        speakerless = replace(
            _turn_context(workspace_id, agent_id, conversation_id, None), grants=store
        )
        assert await speakerless.connector_accounts("stub") == ("acct-shared",)
        with pytest.raises(ValueError, match="acct-other-private"):
            await ctx.connector_account("stub", "acct-other-private")


async def test_connector_accounts_resolve_the_on_behalf_of_member_for_speakerless_turns(
    db: None,
) -> None:
    """A scheduled fire or subagent carries the initiating member as on_behalf_of, so it keeps that
    member's private connections even with no live speaker — a turn with no member at all sees only
    shared grants."""
    workspace_id = await _workspace()
    initiator_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, initiator_id)
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-initiator-private",
        host=GRANTED_HOST,
        grantor_member_id=initiator_id,
        conversation_id=conversation_id,
        shared=False,
    )
    on_behalf = replace(
        _turn_context(workspace_id, agent_id, conversation_id, None),
        grants=store,
        on_behalf_of_member_id=initiator_id,
    )
    assert on_behalf.speaker_member_id is None
    with ws(workspace_id), agent(agent_id):
        assert await on_behalf.connector_accounts("stub") == ("acct-initiator-private",)
    anonymous = replace(_turn_context(workspace_id, agent_id, conversation_id, None), grants=store)
    with ws(workspace_id), agent(agent_id):
        assert await anonymous.connector_accounts("stub") == ()
