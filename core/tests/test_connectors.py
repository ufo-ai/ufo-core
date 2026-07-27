"""The core connectors seam: a manifest's `connectors` point becomes the connect-flow provider
registry and the derived grant's admit/meter at the egress proxy plus its connected-account id for
server-side execution.

The installed sample extension is the real consumer — its stub connector (a canned OAuth handoff and
one server-side-execute tool) stands in for a provider so the seam runs end to end without a live
provider. These tests source the provider registry the way `serve` does (`_connect_flow` over the
manifests) and drive the resulting grant through the proxy exactly as U8b/U8c do. The broker holds
the account's token and executes tools server-side, so a grant admits and meters its host but
injects nothing on the wire."""

import asyncio
import base64
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample as sample
from cryptography.fernet import Fernet

from ufo.agent_scope import agent
from ufo.audience import conversation_audience
from ufo.config import Config
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.grants import ConnectHandoff, GrantStore, install_connect_flow
from ufo.sandbox.proxy.rules import REQUEST_METER_DIMENSION, MeterRule, ScopeRule
from ufo.sandbox.proxy.server import EgressProxy, PerAgentRules, generate_ca
from ufo.sandbox.session import RunToken, RunTokenCodec
from ufo.schema import tables
from ufo.schema.records import Agent, ConnectRequest, TerminalFrame, Turn
from ufo.serve import _connect_flow, _connect_redirect_uri
from ufo.tools.builtins import ConnectAccountInput, connect_account_handler
from ufo.tools.context import ToolContext
from ufo.workspace import ws

UNGRANTED_HOST = "api.ungranted.test"
PUBLIC_BASE_URL = "https://ufo.example.com"
EXPECTED_REDIRECT_URI = "https://ufo.example.com/v1/connect/callback"
RUN_TOKENS = RunTokenCodec(b"connectors-test-run-token-secret")


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


def test_two_connectors_claiming_the_same_provider_fail_loud() -> None:
    with pytest.raises(RuntimeError, match=sample.CONNECTOR_PROVIDER):
        _connect_flow(
            _credentials(), _config(PUBLIC_BASE_URL), (sample.manifest(), sample.manifest())
        )


def test_no_credential_key_means_no_connect_flow() -> None:
    assert _connect_flow(None, _config(PUBLIC_BASE_URL), (sample.manifest(),)) is None


def test_connect_is_inert_without_a_connector_and_needs_no_callback_config() -> None:
    flow = _connect_flow(_credentials(), _config(None), ())
    assert flow is not None
    assert flow.providers == {}
    assert flow.redirect_uri == ""


@pytest.mark.parametrize("base", ["http://0.0.0.0:8710", "http://127.0.0.1:8710"])
def test_redirect_uri_rejects_a_bind_address_when_a_connector_is_registered(base: str) -> None:
    providers = {c.oauth.provider: c.oauth for c in sample.manifest().connectors}
    with pytest.raises(RuntimeError, match="bind address"):
        _connect_redirect_uri(_config(base), providers)


def test_redirect_uri_rejects_a_scheme_less_callback() -> None:
    providers = {c.oauth.provider: c.oauth for c in sample.manifest().connectors}
    with pytest.raises(RuntimeError, match="scheme and host"):
        _connect_redirect_uri(_config("ufo.example.com"), providers)


def test_redirect_uri_requires_config_once_a_connector_is_registered() -> None:
    providers = {c.oauth.provider: c.oauth for c in sample.manifest().connectors}
    with pytest.raises(RuntimeError, match="public_base_url"):
        _connect_redirect_uri(_config(None), providers)


async def test_connect_binds_a_grant_and_the_proxy_admits_and_meters_the_host(
    db: None,
) -> None:
    """Connect the account in chat, binding a grant, then resolve it through the REAL egress proxy:
    the provider host is admitted (a ScopeRule) and metered (a MeterRule), an ungranted host is
    refused at CONNECT. The broker holds the token, so the grant injects nothing on the wire."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    turn_id = await _turn(workspace_id, agent_id, conversation_id)
    flow = _connect_flow(_credentials(), _config(PUBLIC_BASE_URL), (sample.manifest(),))
    assert flow is not None
    install_connect_flow(flow)

    ctx = _turn_context(workspace_id, agent_id, conversation_id, member_id, turn_id=turn_id)
    result = await connect_account_handler(
        ctx, ConnectAccountInput(provider=sample.CONNECTOR_PROVIDER)
    )
    request = ConnectRequest.model_validate_json(result.content[0].text.splitlines()[1])
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .where(tables.turn.c.id == turn_id)
            .values(
                status="done",
                speaker_member_id=member_id,
                terminal=TerminalFrame(status="done", connect_request=request).model_dump(
                    mode="json"
                ),
                updated_at=sa.func.now(),
            )
        )
    url = await ConnectHandoff(flow).authorize(workspace_id, turn_id, member_id)
    assert url.startswith(sample.CONNECTOR_AUTHORIZE_URL)
    state = parse_qs(urlparse(url).query)["state"][0]
    recorded = await flow.complete(state=state, code="the-code")
    assert (recorded.provider, recorded.account_id) == (
        sample.CONNECTOR_PROVIDER,
        sample.CONNECTOR_ACCOUNT,
    )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.grant.c.host, tables.grant.c.grantor_member_id).where(
                    tables.grant.c.workspace_id == workspace_id
                )
            )
        ).one()
    assert (row.host, row.grantor_member_id) == (sample.CONNECTOR_HOST, member_id)

    resolver = PerAgentRules(base=(), grants=flow.store)
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
        run = RunToken(workspace_id, turn_id)
        assert await _connect_status(endpoint.port, UNGRANTED_HOST, RUN_TOKENS.encode(run)) == 403
        rules = await proxy._rules_for(run)
        assert any(
            isinstance(r, ScopeRule) and sample.CONNECTOR_HOST in r.allowed_hosts for r in rules
        )
        assert MeterRule(host=sample.CONNECTOR_HOST, dimension=REQUEST_METER_DIMENSION) in rules
        await proxy._meter_ledger(sample.CONNECTOR_HOST, run, rules)
    finally:
        await proxy.stop()

    async with workspace_tx() as connection:
        ledger = (
            await connection.execute(
                sa.select(tables.ledger.c.dimension, tables.ledger.c.amount).where(
                    tables.ledger.c.turn_id == turn_id
                )
            )
        ).one()
    assert (ledger.dimension, int(ledger.amount)) == ("egress", 1)


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


async def test_connector_account_selects_the_named_account(db: None) -> None:
    """An agent holding two accounts for one provider: `connector_account(provider, account_id=X)`
    returns X, not the other account; omitting the choice or naming one it does not hold fails
    loud."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    with ws(workspace_id), agent(agent_id):
        for account in ("acct-1", "acct-2"):
            await store.record(
                provider=sample.CONNECTOR_PROVIDER,
                account_id=account,
                host=sample.CONNECTOR_HOST,
                grantor_member_id=member_id,
                conversation_id=conversation_id,
                shared=False,
            )
        ctx = _turn_context(workspace_id, agent_id, conversation_id, member_id, grants=store)
        with pytest.raises(ValueError, match=r"multiple active.*pass account_id"):
            await ctx.connector_account(sample.CONNECTOR_PROVIDER)
        assert (
            await ctx.connector_account(sample.CONNECTOR_PROVIDER, account_id="acct-2") == "acct-2"
        )
        with pytest.raises(ValueError, match="acct-9"):
            await ctx.connector_account(sample.CONNECTOR_PROVIDER, account_id="acct-9")


async def test_connector_accounts_lists_only_the_turn_agents_provider_accounts(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    for provider, account in (
        (sample.CONNECTOR_PROVIDER, "acct-2"),
        (sample.CONNECTOR_PROVIDER, "acct-1"),
        ("other", "acct-other"),
    ):
        with ws(workspace_id), agent(agent_id):
            await store.record(
                provider=provider,
                account_id=account,
                host=sample.CONNECTOR_HOST,
                grantor_member_id=member_id,
                conversation_id=conversation_id,
                shared=False,
            )
    ctx = _turn_context(workspace_id, agent_id, conversation_id, member_id, grants=store)
    with ws(workspace_id), agent(agent_id):
        assert await ctx.connector_accounts(sample.CONNECTOR_PROVIDER) == ("acct-1", "acct-2")


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
            on_behalf_of_member_id=member_m,
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


async def _turn(workspace_id: UUID, agent_id: UUID, conversation_id: UUID) -> UUID:
    turn_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="running",
                inbound="hi",
                terminal=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn_id


def _basic(run_token: str) -> str:
    return "Basic " + base64.b64encode(f"{run_token}:".encode()).decode()


async def _connect_status(port: int, host: str, run_token: str) -> int:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    head = f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}\r\n"
    head += f"Proxy-Authorization: {_basic(run_token)}\r\n"
    writer.write((head + "\r\n").encode())
    await writer.drain()
    status_line = await reader.readline()
    writer.close()
    return int(status_line.split()[1])
