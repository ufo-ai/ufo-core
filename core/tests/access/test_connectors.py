"""The core connectors seam: a manifest's `connectors` point becomes the connect-flow provider
registry and the derived grant's admit/meter at the egress proxy plus its connected-account id for
server-side execution.

The installed sample extension is the real consumer — its stub connector (a canned OAuth handoff and
one server-side-execute tool) stands in for a provider so the seam runs end to end without a live
provider. These tests source the provider registry the way `serve` does (`_connect_flow` over the
manifests) and read the resulting grant back through the turn's tool context. The broker holds the
account's token and executes tools server-side, so a grant admits and meters its host but injects
nothing on the wire — the admit/meter rules the grant derives are proved in `test_egress_rules` and
`test_egress_control`, the wire that enforces them in the Rust `proxy_it`."""

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample as sample
from cryptography.fernet import Fernet
from httpx import AsyncBaseTransport, AsyncClient, Request, Response

from ufo.config import Config
from ufo.db import workspace_tx
from ufo.runtime.access.connectors import (
    CatalogEntry,
    CatalogPage,
    ConnectorEntry,
    ConnectorRegistry,
    Credential,
    SourceCredentialResolver,
)
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.access.grants import GrantStore, install_connect_flow
from ufo.runtime.agent_scope import agent
from ufo.runtime.tools.context import ToolContext
from ufo.runtime.turns.audience import conversation_audience
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.serve import (
    CONNECT_CALLBACK_PATH,
    _connect_flow,
    _connect_redirect_uri,
    _connector_entries,
)

PUBLIC_BASE_URL = "https://ufo.example.com"
EXPECTED_REDIRECT_URI = "https://ufo.example.com/v1/connect/callback"
DISCONNECT_TIMEOUT_SECONDS = 5


class _BarrierTransport(AsyncBaseTransport):
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.requests = 0

    async def handle_async_request(self, request: Request) -> Response:
        self.requests += 1
        self.entered.set()
        await self.release.wait()
        return Response(200, json={"ok": True})


@dataclass(frozen=True)
class _BarrierBroker:
    transport: _BarrierTransport

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=self.transport)


@dataclass(frozen=True)
class _CatalogResolver:
    rows: tuple[CatalogEntry, ...]
    broker: _BarrierBroker

    @property
    def transfer_hosts(self) -> tuple[str, ...]:
        return ()

    async def claims(self, provider: str) -> bool:
        return provider in {row.provider for row in self.rows}

    def entry(self, provider: str) -> ConnectorEntry:
        return ConnectorEntry(provider=provider, label=provider.title(), broker=self.broker)

    async def catalog(self, query: str, limit: int, after: str | None) -> CatalogPage:
        rows = tuple(row for row in self.rows if query.lower() in row.label.lower())
        if after == "next":
            return CatalogPage(entries=rows[1:limit], after=None)
        return CatalogPage(entries=rows[:limit], after="next" if len(rows) > limit else None)


@pytest.fixture(autouse=True)
def _reset_connect_flow() -> Iterator[None]:
    yield
    install_connect_flow(None)


def _config(public_base_url: str | None) -> Config:
    return Config.model_validate(
        {
            "database": {"url": "sqlite+aiosqlite:///unused.db"},
            "blob": {"backend": "filesystem", "root": "/tmp/unused"},
            "connect": {"public_base_url": public_base_url},
        }
    )


def _credentials() -> CredentialStore:
    return CredentialStore(fernet=Fernet(Fernet.generate_key()))


def test_serve_builds_the_provider_registry_from_manifest_connectors() -> None:
    flow = _connect_flow(_credentials(), _config(PUBLIC_BASE_URL), (sample.manifest(),))
    assert flow is not None
    assert set(flow.providers) == {sample.CONNECTOR_PROVIDER}
    assert flow.providers[sample.CONNECTOR_PROVIDER].host == sample.CONNECTOR_HOST
    assert flow.redirect_uri == EXPECTED_REDIRECT_URI


async def test_connector_catalog_merges_explicit_and_open_providers() -> None:
    broker = _BarrierBroker(_BarrierTransport())
    registry = ConnectorRegistry(
        entries=_connector_entries((sample.manifest(),)),
        resolver=_CatalogResolver(
            rows=(
                CatalogEntry(provider=sample.CONNECTOR_PROVIDER, label="Sample Cloud"),
                CatalogEntry(provider="notion", label="Notion"),
            ),
            broker=broker,
        ),
    )

    assert await registry.catalog("", 10, None) == CatalogPage(
        entries=(
            CatalogEntry(provider=sample.CONNECTOR_PROVIDER, label=sample.CONNECTOR_LABEL),
            CatalogEntry(provider="notion", label="Notion"),
        ),
        after=None,
    )
    assert await registry.catalog("not", 1, None) == CatalogPage(
        entries=(CatalogEntry(provider="notion", label="Notion"),),
        after=None,
    )


def test_two_connectors_claiming_the_same_provider_fail_loud() -> None:
    with pytest.raises(RuntimeError, match=sample.CONNECTOR_PROVIDER):
        _connect_flow(
            _credentials(), _config(PUBLIC_BASE_URL), (sample.manifest(), sample.manifest())
        )


def test_connect_is_inert_without_a_connector_and_needs_no_callback_config() -> None:
    flow = _connect_flow(_credentials(), _config(None), ())
    assert flow is not None
    assert flow.providers == {}
    assert flow.redirect_uri == ""


@pytest.mark.parametrize("base", ["http://0.0.0.0:8710", "http://[::]:8710"])
def test_redirect_uri_rejects_a_wildcard_bind_when_a_connector_is_registered(base: str) -> None:
    providers = {c.oauth.provider: c.oauth for c in sample.manifest().connectors}
    with pytest.raises(RuntimeError, match="wildcard bind"):
        _connect_redirect_uri(_config(base), providers)


@pytest.mark.parametrize("base", ["http://localhost:8710", "http://127.0.0.1:8710"])
def test_redirect_uri_accepts_loopback_because_a_local_node_serves_the_same_browser(
    base: str,
) -> None:
    providers = {c.oauth.provider: c.oauth for c in sample.manifest().connectors}
    assert _connect_redirect_uri(_config(base), providers) == f"{base}{CONNECT_CALLBACK_PATH}"


def test_redirect_uri_requires_config_once_a_connector_is_registered() -> None:
    providers = {c.oauth.provider: c.oauth for c in sample.manifest().connectors}
    with pytest.raises(RuntimeError, match="public_base_url"):
        _connect_redirect_uri(_config(None), providers)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_connector_account_is_scoped_to_the_turn_agents_own_grants(db: None) -> None:
    """The server-side-execution accessor is agent-scoped: agent A's context resolves A's
    connected-account id only — B's grant on the same provider is not in A's resolved grants, so A
    can neither read nor name B's account."""
    workspace_id = await _workspace()
    member_id, agent_a = await _member_agent(workspace_id)
    agent_b = await _agent(workspace_id, "assistant-b")
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    for agent_id, account in ((agent_a, "acct-a"), (agent_b, "acct-b")):
        with ws(workspace_id), agent(agent_id):
            await store.record(
                provider=sample.CONNECTOR_PROVIDER,
                account_id=account,
                host=sample.CONNECTOR_HOST,
                grantor_member_id=member_id,
                conversation_id=conversation_id,
                shared=False,
            )
    ctx_a = _turn_context(workspace_id, agent_a, conversation_id, member_id, grants=store)
    with ws(workspace_id), agent(agent_a):
        assert await ctx_a.connector_account(sample.CONNECTOR_PROVIDER) == "acct-a"
        with pytest.raises(ValueError, match="acct-b"):
            await ctx_a.connector_account(sample.CONNECTOR_PROVIDER, account_id="acct-b")


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_source_credential_stays_bound_to_its_connection_generation(db: None) -> None:
    workspace_id = await _workspace()
    alice, agent_id = await _member_agent(workspace_id)
    alice_conversation = await _conversation(workspace_id, alice)
    store = GrantStore()
    with ws(workspace_id), agent(agent_id):
        await store.record(
            provider="stub",
            account_id="same-account",
            host="api.stub.test",
            grantor_member_id=alice,
            conversation_id=alice_conversation,
            shared=False,
        )
        (grant,) = await store.active_grants()

    transport = _BarrierTransport()
    registry = ConnectorRegistry(
        entries={
            "stub": ConnectorEntry(
                provider="stub",
                label="Stub",
                broker=_BarrierBroker(transport),
            )
        }
    )
    credential = (
        await SourceCredentialResolver(registry)
        .bind(
            grant.connection_id,
            alice,
        )
        .credential(workspace_id, "stub", "same-account")
    )
    assert credential.transport is not None

    async def disconnect() -> bool:
        with ws(workspace_id), agent(agent_id):
            return await store.disconnect(
                grant.connection_id,
                actor_member_id=alice,
            )

    async with AsyncClient(transport=credential.transport) as client:
        request = asyncio.create_task(client.get("https://api.stub.test/items"))
        await transport.entered.wait()
        deletion = asyncio.create_task(disconnect())
        assert await asyncio.wait_for(deletion, timeout=DISCONNECT_TIMEOUT_SECONDS) is True
        transport.release.set()
        assert (await request).status_code == 200

        bob = uuid4()
        with ws(workspace_id):
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.member).values(
                        id=bob,
                        workspace_id=workspace_id,
                        email="bob@x.test",
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        bob_conversation = await _conversation(workspace_id, bob)
        with ws(workspace_id), agent(agent_id):
            await store.record(
                provider="stub",
                account_id="same-account",
                host="api.stub.test",
                grantor_member_id=bob,
                conversation_id=bob_conversation,
                shared=False,
            )
        with pytest.raises(ValueError, match="no longer active for this source"):
            await (
                SourceCredentialResolver(registry)
                .bind(grant.connection_id, alice)
                .credential(workspace_id, "stub", "same-account")
            )
        with pytest.raises(ValueError, match="no longer active for this source"):
            await client.get("https://api.stub.test/items")

    assert transport.requests == 1


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_connector_account_prefers_the_acting_members_private_account(db: None) -> None:
    """A provider bound both privately and agent-shared resolves by tier: the acting member's own
    private account first, the agent-shared one as the fallback — M's turns act as M's account, a
    member without a private grant acts as the shared one and can never name M's, and a scheduled
    fire acting on behalf of M keeps M's private account."""
    workspace_id = await _workspace()
    member_m, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_m)
    member_n = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_n,
                workspace_id=workspace_id,
                email="n@x.test",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    store = GrantStore()
    with ws(workspace_id), agent(agent_id):
        for account, shared in (("acct-m", False), ("acct-shared", True)):
            await store.record(
                provider=sample.CONNECTOR_PROVIDER,
                account_id=account,
                host=sample.CONNECTOR_HOST,
                grantor_member_id=member_m,
                conversation_id=conversation_id,
                shared=shared,
            )
        ctx_m = _turn_context(workspace_id, agent_id, conversation_id, member_m, grants=store)
        assert await ctx_m.connector_account(sample.CONNECTOR_PROVIDER) == "acct-m"
        assert await ctx_m.connector_accounts(sample.CONNECTOR_PROVIDER) == (
            "acct-m",
            "acct-shared",
        )
        assert (
            await ctx_m.connector_account(sample.CONNECTOR_PROVIDER, account_id="acct-shared")
            == "acct-shared"
        )
        ctx_n = _turn_context(workspace_id, agent_id, conversation_id, member_n, grants=store)
        assert await ctx_n.connector_account(sample.CONNECTOR_PROVIDER) == "acct-shared"
        assert await ctx_n.connector_accounts(sample.CONNECTOR_PROVIDER) == ("acct-shared",)
        with pytest.raises(ValueError, match="acct-m"):
            await ctx_n.connector_account(sample.CONNECTOR_PROVIDER, account_id="acct-m")
        scheduled = replace(
            ctx_m,
            speaker_member_id=None,
            turn=ctx_m.turn.model_copy(update={"on_behalf_of_member_id": member_m}),
        )
        assert await scheduled.connector_account(sample.CONNECTOR_PROVIDER) == "acct-m"


def _turn_context(
    workspace_id: UUID,
    agent_id: UUID,
    conversation_id: UUID,
    member_id: UUID,
    grants: GrantStore | None = None,
    turn_id: UUID | None = None,
) -> ToolContext:
    turn = Turn(
        id=turn_id or uuid4(),
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        agent_id=agent_id,
        seq=1,
        status="running",
        inbound="connect my sample account",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
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
        grants=grants,
    )


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
