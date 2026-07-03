"""The core connectors seam: a manifest's `connectors` point becomes the connect-flow provider
registry and the derived grant's admit/inject/meter at the egress proxy.

The installed sample extension is the real consumer — its stub connector (a canned OAuth handoff and
one connector tool) stands in for a provider so the seam runs end to end without a live provider.
These tests source the provider registry the way `serve` does (`_connect_flow` over the manifests)
and drive the resulting grant through the proxy exactly as U8b/U8c do."""

import asyncio
import base64
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
import selfhost_ext_sample as sample
import sqlalchemy as sa
from cryptography.fernet import Fernet

from selfhost.config import Config
from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.grants import install_connect_flow
from selfhost.sandbox.proxy.rules import (
    GRANT_METER_DIMENSION,
    InjectionRule,
    MeterRule,
)
from selfhost.sandbox.proxy.server import EgressProxy, PerAgentRules, _inject, generate_ca
from selfhost.sandbox.session import RunToken
from selfhost.schema import tables
from selfhost.schema.records import Agent, Turn
from selfhost.serve import _connect_flow, _connect_redirect_uri
from selfhost.tools.builtins import ConnectAccountInput, connect_account_handler
from selfhost.tools.context import ToolContext

UNGRANTED_HOST = "api.ungranted.test"
PUBLIC_BASE_URL = "https://selfhost.example.com"
EXPECTED_REDIRECT_URI = "https://selfhost.example.com/v1/connect/callback"


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
        _connect_redirect_uri(_config("selfhost.example.com"), providers)


def test_redirect_uri_requires_config_once_a_connector_is_registered() -> None:
    providers = {c.oauth.provider: c.oauth for c in sample.manifest().connectors}
    with pytest.raises(RuntimeError, match="public_base_url"):
        _connect_redirect_uri(_config(None), providers)


async def test_connect_via_a_manifest_connector_binds_a_grant_and_egress_is_metered(
    db: None,
) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    turn_id = await _turn(workspace_id, agent_id, conversation_id)
    flow = _connect_flow(_credentials(), _config(PUBLIC_BASE_URL), (sample.manifest(),))
    assert flow is not None
    install_connect_flow(flow)

    ctx = _turn_context(workspace_id, agent_id, conversation_id, member_id)
    result = await connect_account_handler(
        ctx, ConnectAccountInput(provider=sample.CONNECTOR_PROVIDER)
    )
    url = result.content[0].text
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
    proxy = EgressProxy(resolve=resolver.resolve, ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    try:
        run = RunToken(workspace_id, turn_id).encode()
        assert await _connect_status(endpoint.port, sample.CONNECTOR_HOST, run) == 200
        assert await _connect_status(endpoint.port, UNGRANTED_HOST, run) == 403
        rules = await proxy._rules_for(RunToken(workspace_id, turn_id))
        assert (
            MeterRule(host=sample.CONNECTOR_HOST, dimension=GRANT_METER_DIMENSION) in rules
        )
        candidates = [
            r for r in rules if isinstance(r, InjectionRule) and r.host == sample.CONNECTOR_HOST
        ]
        sentinel = (
            f"authorization: Bearer SELFHOST_SENTINEL_GRANT_"
            f"{sample.CONNECTOR_PROVIDER}_{sample.CONNECTOR_ACCOUNT}\r\n"
        ).encode()
        assert f"Bearer {sample.CONNECTOR_TOKEN}".encode() in _inject([sentinel], candidates)
        proxy._meter_ledger(sample.CONNECTOR_HOST, _basic(run), rules)
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


def _turn_context(
    workspace_id: UUID, agent_id: UUID, conversation_id: UUID, member_id: UUID
) -> ToolContext:
    turn = Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        agent_id=agent_id,
        seq=1,
        status="running",
        inbound="connect my sample account",
    )
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        memory=None,
        member_id=member_id,
        artifact_token_secret="",
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
