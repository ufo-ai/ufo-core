"""The connectors extension: Composio-brokered OAuth providers and the dynamic Composio tools.

The extension imports only `selfhost.sdk`. These tests source its manifest the way `serve` does
(`_connect_flow` over the manifest, `turn_tools` for the tool set) and drive the two seams it owns:
the OAuth consent handoff (grant binding + confused-deputy close) and the dynamic tools
(`list_external_tools`/`describe_external_tools`/`call_external_tool`). Composio's HTTP is mocked
with an `httpx.MockTransport` — no live Composio API or key — so the real client, provider, route,
and tool code run against canned Composio responses. Execution is server-side on Composio's execute
API, so a dynamic tool never touches the sandbox egress proxy (the sample proves that path)."""

import json
from collections.abc import Callable, Iterator
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import httpx
import pytest
import selfhost_ext_connectors.composio as composio
import selfhost_ext_connectors.manifest as connectors
import selfhost_ext_connectors.provider as provider
import sqlalchemy as sa
from cryptography.fernet import Fernet
from selfhost_ext_connectors.tools import (
    CallExternalToolInput,
    DescribeExternalToolsInput,
    ListExternalToolsInput,
    call_external_tool,
    describe_external_tools,
    list_external_tools,
)
from starlette.requests import Request

from selfhost.config import Config
from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.ext.context import context_for
from selfhost.ext.loader import turn_tools
from selfhost.grants import GrantStore, install_connect_flow
from selfhost.schema import tables
from selfhost.schema.records import Agent, Turn
from selfhost.serve import _connect_flow
from selfhost.tools.builtins import ConnectAccountInput, connect_account_handler
from selfhost.tools.context import ToolContext

PUBLIC_BASE_URL = "https://selfhost.example.com"
EXPECTED_REDIRECT_URI = "https://selfhost.example.com/v1/connect/callback"
PROVIDER = "github"
PROVIDER_HOST = "api.github.com"
COMPOSIO_CONSENT_URL = "https://github.com/login/oauth/authorize?client_id=x&state=y"
COMPOSIO_ACCOUNT = "ca_test123"
GITHUB_TOKEN = "gho_realsecrettoken"
COMPOSIO_USER = "selfhost_ws"
GITHUB_SLUG = "GITHUB_LIST_PULL_REQUESTS"
UNKNOWN_SLUG = "GITHUB_DEFINITELY_NOT_A_TOOL"
TOOL_DESCRIPTION = "List pull requests on a repository."


@pytest.fixture(autouse=True)
def _reset_connect_flow() -> Iterator[None]:
    yield
    install_connect_flow(None)


def _composio_handler(
    owner: str, executed: list[dict[str, object]] | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    """A Composio mock: connect endpoints (reporting `owner` as the account's owning user, so the
    ownership assertion passes for a match and refuses a foreign one), the tool catalog
    (`GET /tools`, `GET /tools/{slug}`), and server-side execute (`POST /tools/execute/{slug}`,
    recording the request body into `executed`). An unknown slug 404s on schema and execute."""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        if method == "POST" and path.endswith("/connected_accounts/link"):
            return httpx.Response(200, json={"redirect_url": COMPOSIO_CONSENT_URL})
        if method == "GET" and path.endswith("/auth_configs"):
            return httpx.Response(200, json={"items": [{"id": "ac_test"}]})
        if method == "GET" and "/connected_accounts/" in path:
            return httpx.Response(
                200,
                json={
                    "status": "ACTIVE",
                    "user_id": owner,
                    "state": {"val": {"access_token": GITHUB_TOKEN}},
                },
            )
        if method == "GET" and path.endswith("/tools"):
            return httpx.Response(
                200, json={"items": [{"slug": GITHUB_SLUG, "description": TOOL_DESCRIPTION}]}
            )
        if method == "GET" and path.endswith(f"/tools/{GITHUB_SLUG}"):
            return httpx.Response(
                200,
                json={
                    "slug": GITHUB_SLUG,
                    "input_schema": {"type": "object", "properties": {"owner": {"type": "string"}}},
                },
            )
        if method == "GET" and "/tools/" in path:
            return httpx.Response(404, json={"error": "unknown tool"})
        if method == "POST" and path.endswith(f"/tools/execute/{GITHUB_SLUG}"):
            if executed is not None:
                executed.append(json.loads(request.content))
            return httpx.Response(200, json={"successful": True, "data": {"items": []}})
        if method == "POST" and "/tools/execute/" in path:
            return httpx.Response(404, json={"error": "unknown tool"})
        return httpx.Response(404, json={})

    return handle


def _mock_client(
    owner: str = COMPOSIO_USER, executed: list[dict[str, object]] | None = None
) -> composio.ComposioClient:
    return composio.ComposioClient(
        api_key="test", transport=httpx.MockTransport(_composio_handler(owner, executed))
    )


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


async def test_composio_client_confirms_an_active_accounts_owner() -> None:
    account = await _mock_client().connected_account(COMPOSIO_ACCOUNT, COMPOSIO_USER)
    assert account.account_id == COMPOSIO_ACCOUNT


async def test_composio_client_refuses_an_account_owned_by_a_foreign_user() -> None:
    with pytest.raises(composio.ComposioError, match="owned by"):
        await _mock_client("selfhost_someone_else").connected_account(
            COMPOSIO_ACCOUNT, COMPOSIO_USER
        )


async def test_composio_client_refuses_an_inactive_account() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": "INITIATED", "user_id": COMPOSIO_USER, "state": {"val": {}}}
        )

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handler))
    with pytest.raises(composio.ComposioError, match="INITIATED"):
        await client.connected_account(COMPOSIO_ACCOUNT, COMPOSIO_USER)


async def test_composio_client_mints_a_connect_link() -> None:
    redirect = await _mock_client().connect_link(
        toolkit="github", user_id="selfhost_ws", callback_url="https://selfhost.example.com/back"
    )
    assert redirect == COMPOSIO_CONSENT_URL


async def test_composio_client_executes_a_tool_with_the_bound_account() -> None:
    executed: list[dict[str, object]] = []
    response = await _mock_client(executed=executed).execute_tool(
        GITHUB_SLUG, {"owner": "acme"}, COMPOSIO_USER, COMPOSIO_ACCOUNT
    )
    assert response["successful"] is True
    assert executed[0] == {
        "user_id": COMPOSIO_USER,
        "arguments": {"owner": "acme"},
        "connected_account_id": COMPOSIO_ACCOUNT,
    }


async def test_composio_client_refuses_an_oversized_execute_payload() -> None:
    with pytest.raises(ValueError, match="payload bound"):
        await _mock_client().execute_tool(
            GITHUB_SLUG,
            {"blob": "x" * (composio.MAX_EXECUTE_ARGUMENTS_BYTES + 1)},
            COMPOSIO_USER,
            COMPOSIO_ACCOUNT,
        )


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


def test_serve_registers_every_provider_and_declares_the_dynamic_tools() -> None:
    flow = _connect_flow(_credentials(), _config(), (connectors.manifest(),))
    assert flow is not None
    assert set(flow.providers) == set(composio.CONNECTORS)
    assert flow.providers[PROVIDER].host == PROVIDER_HOST
    assert flow.redirect_uri == EXPECTED_REDIRECT_URI
    tools, _ = turn_tools((connectors.manifest(),), uuid4(), _credentials())
    names = {tool.name for tool in tools}
    assert {"list_external_tools", "describe_external_tools", "call_external_tool"} <= names


async def test_list_external_tools_filters_the_connector_catalog() -> None:
    result = await list_external_tools(
        _ctx(uuid4(), uuid4(), uuid4(), None),
        ListExternalToolsInput(queries=("github",), user_description="find a code host"),
    )
    payload = json.loads(result.content[0].text)
    rows = {row["source_id"] for row in payload["connectors"]}
    assert PROVIDER in rows
    assert "stripe" not in rows


async def test_list_external_tools_select_prefix_fetches_one_by_exact_id() -> None:
    result = await list_external_tools(
        _ctx(uuid4(), uuid4(), uuid4(), None),
        ListExternalToolsInput(queries=("select:github",), user_description="the code host"),
    )
    payload = json.loads(result.content[0].text)
    assert [row["source_id"] for row in payload["connectors"]] == [PROVIDER]


async def test_describe_external_tools_fetches_schemas_and_available_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(composio, "composio_client", _mock_client)
    result = await describe_external_tools(
        _ctx(uuid4(), uuid4(), uuid4(), None),
        DescribeExternalToolsInput(source_id=PROVIDER, tool_names=(GITHUB_SLUG,)),
    )
    payload = json.loads(result.content[0].text)
    assert payload["source_id"] == PROVIDER
    assert GITHUB_SLUG in payload["schemas"]
    assert payload["schemas"][GITHUB_SLUG]["input_schema"]["properties"] == {
        "owner": {"type": "string"}
    }


async def test_describe_external_tools_marks_an_unknown_name_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(composio, "composio_client", _mock_client)
    result = await describe_external_tools(
        _ctx(uuid4(), uuid4(), uuid4(), None),
        DescribeExternalToolsInput(source_id=PROVIDER, tool_names=(UNKNOWN_SLUG,)),
    )
    payload = json.loads(result.content[0].text)
    assert payload["unresolved"] == [UNKNOWN_SLUG]
    assert [tool["slug"] for tool in payload["availableTools"]] == [GITHUB_SLUG]


async def test_connect_binds_a_grant_and_call_external_tool_executes_via_composio(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: connect the account in chat (its owner read through mocked Composio), binding a
    grant that carries the Composio connected-account id, then `call_external_tool` resolves that
    grant and POSTs to Composio's server-side execute API with the workspace's broker user id and
    the bound account — no sandbox, no proxy."""
    workspace_id = await _workspace()
    owner = f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"
    executed: list[dict[str, object]] = []
    client = _mock_client(owner, executed)
    monkeypatch.setattr(composio, "composio_client", lambda: client)
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
    tool = next(t for t in tools if t.name == "call_external_tool")
    ctx = _ctx(workspace_id, agent_id, conversation_id, turn_id, flow.store, ext_by_tool[tool.name])
    result = await tool.handler(
        ctx,
        tool.input_model.model_validate(
            {"tool_name": GITHUB_SLUG, "source_id": PROVIDER, "arguments": {"owner": "acme"}}
        ),
    )
    assert result.is_error is False
    assert json.loads(result.content[0].text)["successful"] is True
    assert executed == [
        {
            "user_id": owner,
            "arguments": {"owner": "acme"},
            "connected_account_id": COMPOSIO_ACCOUNT,
        }
    ]


async def test_call_external_tool_without_a_grant_fails_loud(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, agent_id, conversation_id, turn_id = uuid4(), uuid4(), uuid4(), uuid4()
    executed: list[dict[str, object]] = []
    monkeypatch.setattr(
        composio, "composio_client", lambda: _mock_client(COMPOSIO_USER, executed)
    )
    ctx = _ctx(workspace_id, agent_id, conversation_id, turn_id, GrantStore())
    with pytest.raises(ValueError, match="grant"):
        await call_external_tool(
            ctx,
            CallExternalToolInput(tool_name=GITHUB_SLUG, source_id=PROVIDER, arguments={}),
        )
    assert executed == []


async def test_call_external_tool_augments_a_404_with_the_real_slugs(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 404 from execute carries the source's real tool slugs, so the model's next attempt is
    informed instead of another blind guess at the naming convention."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    turn_id = await _turn(workspace_id, agent_id, conversation_id)
    store = GrantStore()
    await store.record(
        workspace_id=workspace_id,
        agent_id=agent_id,
        provider=PROVIDER,
        account_id=COMPOSIO_ACCOUNT,
        host=PROVIDER_HOST,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
    )
    monkeypatch.setattr(composio, "composio_client", _mock_client)
    ctx = _ctx(workspace_id, agent_id, conversation_id, turn_id, store)
    with pytest.raises(composio.ComposioError, match=f"tools available on github: {GITHUB_SLUG}"):
        await call_external_tool(
            ctx,
            CallExternalToolInput(tool_name=UNKNOWN_SLUG, source_id=PROVIDER, arguments={}),
        )


async def test_complete_rejects_an_account_owned_by_a_foreign_composio_user(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confused-deputy close: driving `complete` with a connectedAccountId whose owning Composio
    user is not this workspace's brokered user is refused before any token is read, so no grant
    binds to an attacker-controlled account."""
    workspace_id = uuid4()
    foreign_owner = f"{composio.EXTERNAL_USER_PREFIX}{uuid4()}"
    monkeypatch.setattr(composio, "composio_client", lambda: _mock_client(foreign_owner))
    flow = _connect_flow(_credentials(), _config(), (connectors.manifest(),))
    assert flow is not None
    url = flow.authorize(
        workspace_id=workspace_id,
        agent_id=uuid4(),
        provider=PROVIDER,
        grantor_member_id=uuid4(),
        conversation_id=uuid4(),
    )
    state = parse_qs(urlparse(url).query)["state"][0]
    with pytest.raises(composio.ComposioError, match="owned by"):
        await flow.complete(state=state, code=COMPOSIO_ACCOUNT)
    async with workspace_tx() as connection:
        count = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.grant)
                .where(tables.grant.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert count == 0


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


def _ctx(
    workspace_id: UUID,
    agent_id: UUID,
    conversation_id: UUID,
    turn_id: UUID | None,
    grants: GrantStore | None = None,
    ext: object = None,
) -> ToolContext:
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=Turn(
            id=turn_id or uuid4(),
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="use a connector",
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        memory=None,
        member_id=None,
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
