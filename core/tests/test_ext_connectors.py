"""The connectors extension: Composio-brokered OAuth providers and their egress-proxied tools.

The extension imports only `selfhost.sdk`; these tests source its manifest the way `serve` does
(`_connect_flow` over the manifest, `turn_tools` for the tool set) and drive the resulting grant
through the REAL egress proxy exactly as the grants/connectors seam tests do. Composio's HTTP is
mocked with an `httpx.MockTransport` — no live Composio API or key — so the real client, provider,
and route code run against canned Composio responses."""

import asyncio
import base64
import shlex
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import httpx
import pytest
import selfhost_ext_connectors.composio as composio
import selfhost_ext_connectors.manifest as connectors
import selfhost_ext_connectors.provider as provider
import sqlalchemy as sa
from cryptography.fernet import Fernet
from starlette.requests import Request

from selfhost.config import Config
from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.ext.context import context_for
from selfhost.ext.loader import turn_tools
from selfhost.grants import GrantStore, install_connect_flow
from selfhost.sandbox.proxy.rules import GRANT_METER_DIMENSION, InjectionRule, MeterRule
from selfhost.sandbox.proxy.server import EgressProxy, PerAgentRules, _inject, generate_ca
from selfhost.sandbox.session import ExecResult, RunToken, SandboxHandle, SandboxSession
from selfhost.schema import tables
from selfhost.schema.records import Agent, Turn
from selfhost.serve import _connect_flow
from selfhost.tools.builtins import ConnectAccountInput, connect_account_handler
from selfhost.tools.context import ToolContext

PUBLIC_BASE_URL = "https://selfhost.example.com"
EXPECTED_REDIRECT_URI = "https://selfhost.example.com/v1/connect/callback"
PROVIDER = "github"
PROVIDER_HOST = "api.github.com"
UNGRANTED_HOST = "api.ungranted.test"
COMPOSIO_CONSENT_URL = "https://github.com/login/oauth/authorize?client_id=x&state=y"
COMPOSIO_ACCOUNT = "ca_test123"
GITHUB_TOKEN = "gho_realsecrettoken"


@pytest.fixture(autouse=True)
def _reset_connect_flow() -> Iterator[None]:
    yield
    install_connect_flow(None)


def _composio_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.method == "POST" and path.endswith("/connected_accounts/link"):
        return httpx.Response(200, json={"redirect_url": COMPOSIO_CONSENT_URL})
    if request.method == "GET" and path.endswith("/auth_configs"):
        return httpx.Response(200, json={"items": [{"id": "ac_test"}]})
    if request.method == "GET" and "/connected_accounts/" in path:
        return httpx.Response(
            200, json={"status": "ACTIVE", "state": {"val": {"access_token": GITHUB_TOKEN}}}
        )
    return httpx.Response(404, json={})


def _mock_client() -> composio.ComposioClient:
    return composio.ComposioClient(api_key="test", transport=httpx.MockTransport(_composio_handler))


def _config() -> Config:
    return Config.model_validate(
        {
            "database": {"url": "sqlite+aiosqlite:///unused.db"},
            "blob": {"backend": "filesystem", "root": "/tmp/unused"},
            "connect": {"public_base_url": PUBLIC_BASE_URL},
        }
    )


def _credentials() -> CredentialStore:
    return CredentialStore(fernet=Fernet(Fernet.generate_key()))


async def test_composio_client_reads_a_connected_accounts_real_token() -> None:
    account = await _mock_client().connected_account(COMPOSIO_ACCOUNT)
    assert account.account_id == COMPOSIO_ACCOUNT
    assert account.token == GITHUB_TOKEN


async def test_composio_client_refuses_an_inactive_account() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "INITIATED", "state": {"val": {}}})

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handler))
    with pytest.raises(composio.ComposioError, match="INITIATED"):
        await client.connected_account(COMPOSIO_ACCOUNT)


async def test_composio_client_mints_a_connect_link() -> None:
    redirect = await _mock_client().connect_link(
        toolkit="github", user_id="selfhost_ws", callback_url="https://selfhost.example.com/back"
    )
    assert redirect == COMPOSIO_CONSENT_URL


def test_authorize_url_points_the_browser_at_the_oauth_bridge() -> None:
    oauth = provider.ComposioOAuthProvider(provider=PROVIDER, host=PROVIDER_HOST, toolkit="github")
    url = oauth.authorize_url("SEALED", EXPECTED_REDIRECT_URI)
    parsed = urlparse(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == (
        "https",
        "selfhost.example.com",
        provider.OAUTH_ROUTE_MOUNT,
    )
    query = parse_qs(parsed.query)
    assert query["provider"] == [PROVIDER]
    assert query["state"] == ["SEALED"]
    assert query["callback"] == [EXPECTED_REDIRECT_URI]


async def test_oauth_route_start_leg_redirects_to_composio_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(composio, "composio_client", _mock_client)
    ctx = context_for(uuid4(), connectors.NAME, frozenset(), _credentials())
    query = f"provider={PROVIDER}&state=SEALED&callback={EXPECTED_REDIRECT_URI}"
    response = await provider.oauth_route(ctx, _request(query))
    assert response.status_code == provider.REDIRECT_STATUS
    assert response.headers["location"] == COMPOSIO_CONSENT_URL


async def test_oauth_route_return_leg_hands_the_account_id_to_core_as_code() -> None:
    ctx = context_for(uuid4(), connectors.NAME, frozenset(), _credentials())
    query = (
        f"state=SEALED&callback={EXPECTED_REDIRECT_URI}"
        f"&{provider.COMPOSIO_ACCOUNT_PARAM}={COMPOSIO_ACCOUNT}"
    )
    response = await provider.oauth_route(ctx, _request(query))
    assert response.status_code == provider.REDIRECT_STATUS
    landing = urlparse(response.headers["location"])
    assert f"{landing.scheme}://{landing.netloc}{landing.path}" == EXPECTED_REDIRECT_URI
    landing_query = parse_qs(landing.query)
    assert landing_query["state"] == ["SEALED"]
    assert landing_query["code"] == [COMPOSIO_ACCOUNT]


def test_serve_registers_every_provider_and_keeps_its_host() -> None:
    flow = _connect_flow(_credentials(), _config(), (connectors.manifest(),))
    assert flow is not None
    assert set(flow.providers) == set(composio.CONNECTORS)
    assert flow.providers[PROVIDER].host == PROVIDER_HOST
    assert flow.redirect_uri == EXPECTED_REDIRECT_URI


async def test_connect_then_a_grant_drives_egress_through_the_real_proxy(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: register the Composio provider, connect the account in chat (its OAuth token read
    through mocked Composio HTTP), and drive the resulting grant through the REAL egress proxy — the
    provider host is admitted and the sentinel swapped for the real token, an ungranted host is
    refused, and the forwarded request is metered to the ledger."""
    monkeypatch.setattr(composio, "composio_client", _mock_client)
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    turn_id = await _turn(workspace_id, agent_id, conversation_id)
    credentials = _credentials()
    flow = _connect_flow(credentials, _config(), (connectors.manifest(),))
    assert flow is not None
    install_connect_flow(flow)

    begin = await connect_account_handler(
        _turn_context(workspace_id, agent_id, conversation_id, member_id, turn_id),
        ConnectAccountInput(provider=PROVIDER),
    )
    state = parse_qs(urlparse(begin.content[0].text).query)["state"][0]
    recorded = await flow.complete(state=state, code=COMPOSIO_ACCOUNT)
    assert (recorded.provider, recorded.account_id) == (PROVIDER, COMPOSIO_ACCOUNT)
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.grant.c.host, tables.grant.c.account_id).where(
                    tables.grant.c.workspace_id == workspace_id
                )
            )
        ).one()
    assert (row.host, row.account_id) == (PROVIDER_HOST, COMPOSIO_ACCOUNT)

    tools, ext_by_tool = turn_tools((connectors.manifest(),), workspace_id, credentials)
    tool = next(t for t in tools if t.name == f"{PROVIDER}_request")
    carrier = _RecordingCarrier()
    ctx = _connector_context(
        workspace_id, agent_id, conversation_id, turn_id, member_id, carrier, flow.store,
        ext_by_tool[tool.name],
    )
    result = await tool.handler(ctx, tool.input_model.model_validate({"path": "user"}))
    assert result.is_error is False
    argv = shlex.split(carrier.commands[0])
    emitted = argv[argv.index("-H") + 1].partition("Authorization: ")[2]

    resolver = PerAgentRules(base=(), grants=flow.store)
    cert, key = await generate_ca()
    proxy = EgressProxy(resolve=resolver.resolve, ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    try:
        run = RunToken(workspace_id, turn_id).encode()
        assert await _connect_status(endpoint.port, PROVIDER_HOST, run) == 200
        assert await _connect_status(endpoint.port, UNGRANTED_HOST, run) == 403
        rules = await proxy._rules_for(RunToken(workspace_id, turn_id))
        assert MeterRule(host=PROVIDER_HOST, dimension=GRANT_METER_DIMENSION) in rules
        candidates = [r for r in rules if isinstance(r, InjectionRule) and r.host == PROVIDER_HOST]
        upstream = _inject([f"authorization: {emitted}\r\n".encode()], candidates)
        proxy._meter_ledger(PROVIDER_HOST, _basic(run), rules)
    finally:
        await proxy.stop()
    assert f"Bearer {GITHUB_TOKEN}".encode() in upstream
    assert b"SELFHOST_SENTINEL_GRANT" not in upstream

    async with workspace_tx() as connection:
        ledger = (
            await connection.execute(
                sa.select(tables.ledger.c.dimension, tables.ledger.c.amount).where(
                    tables.ledger.c.turn_id == turn_id
                )
            )
        ).one()
    assert (ledger.dimension, int(ledger.amount)) == ("egress", 1)


async def test_connector_tool_targets_a_named_account_among_several(db: None) -> None:
    """Two accounts for one provider: the tool passing `account_id` puts that account's sentinel on
    the wire, which the proxy swaps for that account's token alone."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    turn_id = await _turn(workspace_id, agent_id, conversation_id)
    credentials = _credentials()
    store = GrantStore(fernet=credentials.fernet)
    for account, token in (("ca_one", "tok-one"), ("ca_two", "tok-two")):
        await store.record(
            workspace_id=workspace_id,
            agent_id=agent_id,
            provider=PROVIDER,
            account_id=account,
            host=PROVIDER_HOST,
            token=token,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
        )
    tools, ext_by_tool = turn_tools((connectors.manifest(),), workspace_id, credentials)
    tool = next(t for t in tools if t.name == f"{PROVIDER}_request")
    carrier = _RecordingCarrier()
    ctx = _connector_context(
        workspace_id, agent_id, conversation_id, turn_id, member_id, carrier, store,
        ext_by_tool[tool.name],
    )
    args = tool.input_model.model_validate({"path": "user", "account_id": "ca_two"})
    await tool.handler(ctx, args)
    argv = shlex.split(carrier.commands[0])
    emitted = argv[argv.index("-H") + 1].partition("Authorization: ")[2]
    assert "ca_two" in emitted and "ca_one" not in emitted

    rules = await PerAgentRules(base=(), grants=store).resolve(RunToken(workspace_id, turn_id))
    candidates = [r for r in rules if isinstance(r, InjectionRule) and r.host == PROVIDER_HOST]
    upstream = _inject([f"authorization: {emitted}\r\n".encode()], candidates)
    assert b"Bearer tok-two" in upstream and b"tok-one" not in upstream


class _RecordingCarrier:
    """Records the egress command the connector tool ran — so the test reads the exact
    `Authorization` header the tool put on the wire — and answers with a canned 200. A stand-in for
    the container, never the thing asserted; `create`/`destroy` stay unreachable."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    async def create(self, spec: object) -> object:
        raise AssertionError("the connector tool reaches the sandbox only through exec")

    async def exec(
        self, handle: object, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        self.commands.append(argv[-1])
        return ExecResult(stdout="200", stderr="", exit_code=0)

    async def destroy(self, handle: object) -> None:
        raise AssertionError("the connector tool reaches the sandbox only through exec")


def _request(query: str) -> Request:
    return Request(
        {"type": "http", "method": "GET", "headers": [], "query_string": query.encode()}
    )


def _turn_context(
    workspace_id: UUID,
    agent_id: UUID,
    conversation_id: UUID,
    member_id: UUID,
    turn_id: UUID,
) -> ToolContext:
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=Turn(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="connect my github",
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        memory=None,
        member_id=member_id,
        artifact_token_secret="",
    )


def _connector_context(
    workspace_id: UUID,
    agent_id: UUID,
    conversation_id: UUID,
    turn_id: UUID,
    member_id: UUID | None,
    carrier: _RecordingCarrier,
    grants: GrantStore,
    ext: object,
) -> ToolContext:
    return ToolContext(
        sandbox=SandboxSession(
            carrier=carrier,
            handle=SandboxHandle(conversation_id=conversation_id, container_id="test"),
        ),
        blob=None,
        turn=Turn(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="call the connector",
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        memory=None,
        member_id=member_id,
        artifact_token_secret="",
        grants=grants,
        ext=ext,
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
