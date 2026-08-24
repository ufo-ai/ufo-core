"""The pipedream extension: Pipedream-backed OAuth providers and the Pipedream `ConnectorBroker`.

The extension imports only `ufo.sdk`. These tests source its manifest the way `serve` does
(`_connect_flow` and `_connector_registry` over the manifests) and drive the two seams it owns: the
OAuth consent handoff (Connect Link bridge + server-side account resolution, so the untrusted
return leg can never name a foreign account) and the broker behind the dynamic connector tools —
driven through the `connectors` extension's real tools over the built registry, so the whole chain
(generic tool → registry → Pipedream broker → actions/run) runs as one. Pipedream's HTTP is mocked
with an `httpx.MockTransport` — no live Pipedream API or credentials — so the real client,
provider, route, broker, and tool code run against canned Connect responses. Execution is
server-side on Pipedream's run API, so a dynamic tool never touches the sandbox egress proxy (the
sample proves that path)."""

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_connectors.manifest as connectors_manifest
import ufo_ext_pipedream.client as pipedream
import ufo_ext_pipedream.manifest as pipedream_manifest
import ufo_ext_pipedream.provider as provider
from cryptography.fernet import Fernet
from starlette.requests import Request
from ufo_ext_connectors.tools import (
    AVAILABLE_TOOLS_FALLBACK_NOTE,
    AVAILABLE_TOOLS_OMITTED_NOTE,
    SEARCH_TOOLS_NOTE_KEY,
    CallExternalToolInput,
    DescribeExternalToolsInput,
    SearchConnectorToolsInput,
    call_external_tool,
    describe_external_tools,
    search_connector_tools,
)
from ufo_ext_pipedream.broker import PipedreamBroker

from ufo.access.connectors import ConnectorRegistry
from ufo.access.credentials import CredentialStore
from ufo.access.egress_rules import connector_transfer_hosts
from ufo.access.grants import ConnectHandoff, GrantStore, install_connect_flow
from ufo.agent_scope import agent
from ufo.config import Config
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.ext.loader import turn_tools
from ufo.loop.engine import MAX_TOOL_RESULT_CHARS
from ufo.schema import tables
from ufo.schema.records import Agent, ConnectRequest, TerminalFrame, Turn
from ufo.sdk.audience import conversation_audience
from ufo.sdk.connectors import GrantUnusable
from ufo.serve import _connect_flow, _connector_registry
from ufo.tools.builtins import ConnectAccountInput, connect_account_handler
from ufo.tools.context import ToolContext
from ufo.workspace import ws

TOOL_NARRATION = "using the connected account"

PUBLIC_BASE_URL = "https://ufo.example.com"
EXPECTED_REDIRECT_URI = "https://ufo.example.com/v1/connect/callback"
PROVIDER = "gmail"
PROVIDER_HOST = "gmail.googleapis.com"
CONNECT_LINK = "https://pipedream.com/_static/connect.html?token=ctok_abc&connectLink=true"
PIPEDREAM_ACCOUNT = "apn_test123"
GMAIL_ACTION = "gmail-send-email"
UNKNOWN_ACTION = "gmail-definitely-not-an-action"
TYPO_ACTION = "gmail-send-emails"
ACTION_DESCRIPTION = "Send an email from your Gmail account."
ACTION_PROPS: list[dict[str, object]] = [
    {"name": "gmail", "type": "app", "app": "gmail"},
    {"name": "to", "type": "string", "label": "Recipient"},
    {"name": "draft", "type": "boolean", "optional": True},
    {"name": "syncDir", "type": "dir", "optional": True},
]
SECOND_PAGE_CURSOR = "cursor_page_two"
SECOND_PAGE_ACTION = "gmail-find-email"
DISCOVERY_QUERY = "find an email from a sender"
OAUTH_APP_ID = "oa_gmail_custom"


@pytest.fixture(autouse=True)
def _pipedream_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(pipedream.PIPEDREAM_CLIENT_ID_ENV, f"cid_{uuid4().hex}")
    monkeypatch.setenv(pipedream.PIPEDREAM_CLIENT_SECRET_ENV, "csecret")
    monkeypatch.setenv(pipedream.PIPEDREAM_PROJECT_ID_ENV, "proj_test")
    monkeypatch.setenv("PIPEDREAM_GMAIL_OAUTH_APP_ID", OAUTH_APP_ID)
    yield
    install_connect_flow(None)


def _pipedream_handler(
    owner: str,
    executed: list[dict[str, object]] | None = None,
    minted: list[dict[str, object]] | None = None,
    token_mints: list[int] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """A Pipedream Connect mock: the OAuth token grant (counting mints into `token_mints`), connect
    tokens (recording the request body into `minted`), the accounts reads (reporting `owner` as
    each account's `external_id`, so the ownership assertion passes for a match and refuses a
    foreign one), the action catalog, and server-side run (recording the request body into
    `executed`). An unknown component key 404s on retrieve."""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        if method == "POST" and path == "/v1/oauth/token":
            assert pipedream.ENVIRONMENT_HEADER not in request.headers
            if token_mints is not None:
                token_mints.append(1)
            return httpx.Response(200, json={"access_token": "at_test", "expires_in": 3600})
        assert request.headers[pipedream.ENVIRONMENT_HEADER] == pipedream.DEFAULT_ENVIRONMENT
        if method == "POST" and path.endswith("/tokens"):
            if minted is not None:
                minted.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "token": "ctok_abc",
                    "expires_at": "2026-07-10T00:00:00Z",
                    "connect_link_url": CONNECT_LINK,
                },
            )
        if method == "GET" and path.endswith(f"/accounts/{PIPEDREAM_ACCOUNT}"):
            return httpx.Response(200, json={"data": _account(owner, PIPEDREAM_ACCOUNT)})
        if method == "GET" and path.endswith("/accounts"):
            assert request.url.params["app"] == "gmail"
            assert request.url.params["external_user_id"] == owner
            return httpx.Response(
                200,
                json={
                    "data": [
                        _account(owner, "apn_older", created_at="2026-07-01T00:00:00Z"),
                        _account(owner, PIPEDREAM_ACCOUNT, created_at="2026-07-09T12:00:00Z"),
                    ]
                },
            )
        if method == "GET" and path.endswith("/actions"):
            return httpx.Response(200, json={"data": [_action_row(GMAIL_ACTION)]})
        if method == "GET" and path.endswith(f"/components/{GMAIL_ACTION}"):
            return httpx.Response(200, json={"data": _action_row(GMAIL_ACTION)})
        if method == "GET" and "/components/" in path:
            return httpx.Response(404, json={"error": "unknown component"})
        if method == "POST" and path.endswith("/actions/run"):
            if executed is not None:
                executed.append(json.loads(request.content))
            return httpx.Response(
                200, json={"exports": {"$summary": "sent"}, "os": [], "ret": {"id": "msg_1"}}
            )
        return httpx.Response(404, json={})

    return handle


def _action_row(key: str) -> dict[str, object]:
    """One component as Connect carries it in both the listing and the definition — the
    `configurable_props` the agent-facing schema is derived from included."""
    return {"key": key, "description": ACTION_DESCRIPTION, "configurable_props": ACTION_PROPS}


def _paged_actions_handler(
    recorded: list[httpx.QueryParams],
) -> Callable[[httpx.Request], httpx.Response]:
    """A two-page Connect action listing: the first page fills `ACTION_PAGE_LIMIT` with bare keys
    and carries the cursor of the second, which holds `SECOND_PAGE_ACTION` in full and no cursor.
    Every listing request's query params land in `recorded`, so the wire is assertable."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth/token":
            return httpx.Response(200, json={"access_token": "at_test", "expires_in": 3600})
        recorded.append(request.url.params)
        if request.url.params.get("after") == SECOND_PAGE_CURSOR:
            return httpx.Response(200, json={"data": [_action_row(SECOND_PAGE_ACTION)]})
        head = [{"key": f"gmail-head-{n}"} for n in range(pipedream.ACTION_PAGE_LIMIT)]
        return httpx.Response(
            200, json={"data": head, "page_info": {"end_cursor": SECOND_PAGE_CURSOR}}
        )

    return handle


def _account(owner: str, account_id: str, created_at: str = "2026-07-09T12:00:00Z") -> dict:
    return {
        "id": account_id,
        "external_id": owner,
        "healthy": True,
        "created_at": created_at,
        "app": {"name_slug": "gmail", "name": "Gmail"},
    }


def _install_transport(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    """Every `pipedream_client()` read answers a client pinned to the mock transport, exactly as a
    deploy's env-built client would be shaped."""
    built = pipedream.pipedream_client()
    client = pipedream.PipedreamClient(
        client_id=built.client_id,
        client_secret=built.client_secret,
        project_id=built.project_id,
        environment=built.environment,
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(pipedream, "pipedream_client", lambda: client)


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


def _registry() -> ConnectorRegistry:
    return _connector_registry(_config(), (pipedream_manifest.manifest(),), _credentials())


async def test_access_token_is_minted_once_and_cached() -> None:
    token_mints: list[int] = []
    client = pipedream.PipedreamClient(
        client_id=f"cid_{uuid4().hex}",
        client_secret="s",
        project_id="proj_test",
        transport=httpx.MockTransport(_pipedream_handler("ufo_ws", token_mints=token_mints)),
    )
    await client.list_actions("gmail")
    await client.list_actions("gmail")
    assert token_mints == [1]


async def test_connect_token_pins_both_return_legs(monkeypatch: pytest.MonkeyPatch) -> None:
    minted: list[dict[str, object]] = []
    _install_transport(monkeypatch, _pipedream_handler("ufo_ws", minted=minted))
    token = await pipedream.pipedream_client().connect_token(
        "ufo_ws", "https://x.test/ok", "https://x.test/err"
    )
    assert token.connect_link_url == CONNECT_LINK
    assert minted == [
        {
            "external_user_id": "ufo_ws",
            "success_redirect_uri": "https://x.test/ok",
            "error_redirect_uri": "https://x.test/err",
        }
    ]


async def test_connected_account_refuses_a_foreign_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_transport(monkeypatch, _pipedream_handler("ufo_someone_else"))
    with pytest.raises(pipedream.PipedreamError, match="owned by"):
        await pipedream.pipedream_client().connected_account(PIPEDREAM_ACCOUNT, "ufo_ws")


async def test_connected_account_refuses_an_unhealthy_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth/token":
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        record = _account("ufo_ws", PIPEDREAM_ACCOUNT)
        record["healthy"] = False
        return httpx.Response(200, json={"data": record})

    _install_transport(monkeypatch, handler)
    with pytest.raises(GrantUnusable, match="unhealthy"):
        await pipedream.pipedream_client().connected_account(PIPEDREAM_ACCOUNT, "ufo_ws")


async def test_pipedream_account_label_reads_the_name_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth/token":
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        return httpx.Response(
            200,
            json={"data": {**_account("ufo_ws", PIPEDREAM_ACCOUNT), "name": "Work Gmail"}},
        )

    _install_transport(monkeypatch, handler)
    assert await pipedream.pipedream_client().account_label(PIPEDREAM_ACCOUNT) == "Work Gmail"


async def test_pipedream_identity_failure_does_not_fail_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = UUID(int=1)
    client = pipedream.pipedream_client()
    client = pipedream.PipedreamClient(
        client_id=client.client_id,
        client_secret=client.client_secret,
        project_id=client.project_id,
        environment=client.environment,
        transport=httpx.MockTransport(
            _pipedream_handler(pipedream.connection_user_id(workspace_id, "state"))
        ),
    )
    monkeypatch.setattr(pipedream, "pipedream_client", lambda: client)

    async def fail(_client: pipedream.PipedreamClient, _account_id: str) -> str | None:
        raise RuntimeError("identity unavailable")

    monkeypatch.setattr(pipedream.PipedreamClient, "account_label", fail)
    account = await provider.PipedreamOAuthProvider(PROVIDER, PROVIDER_HOST, "gmail").exchange(
        PIPEDREAM_ACCOUNT, "https://ufo.example.com/callback", workspace_id, "state"
    )
    assert account.account_label is None


async def test_newest_account_picks_the_latest_of_the_workspaces_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_transport(monkeypatch, _pipedream_handler("ufo_ws"))
    account = await pipedream.pipedream_client().newest_account("ufo_ws", "gmail")
    assert account.account_id == PIPEDREAM_ACCOUNT
    assert account.app == "gmail"


async def test_run_action_refuses_an_oversized_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_transport(monkeypatch, _pipedream_handler("ufo_ws"))
    with pytest.raises(ValueError, match="payload bound"):
        await pipedream.pipedream_client().run_action(
            GMAIL_ACTION,
            "ufo_ws",
            {"blob": "x" * (pipedream.MAX_RUN_ARGUMENTS_BYTES + 1)},
        )


def test_authorize_url_points_the_browser_at_the_oauth_bridge() -> None:
    oauth = provider.PipedreamOAuthProvider(provider=PROVIDER, host=PROVIDER_HOST, app="gmail")
    url = oauth.authorize_url("SEALED", EXPECTED_REDIRECT_URI)
    parsed = urlparse(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == (
        "https",
        "ufo.example.com",
        provider.OAUTH_ROUTE_MOUNT,
    )
    query = parse_qs(parsed.query)
    assert query["provider"] == [PROVIDER]
    assert query["state"] == ["SEALED"]
    assert query["callback"] == [EXPECTED_REDIRECT_URI]


async def test_oauth_route_start_leg_redirects_to_connect_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The start leg mints a Connect token whose success and error redirects both return to this
    bridge, then sends the member to the hosted Connect Link pinned to the provider's app — and to
    the deploy's own Google OAuth client when its env is set."""
    minted: list[dict[str, object]] = []
    _install_transport(monkeypatch, _pipedream_handler("ufo_ws", minted=minted))
    ctx = context_for(pipedream_manifest.NAME, frozenset())
    query = f"provider={PROVIDER}&state=SEALED&callback={EXPECTED_REDIRECT_URI}"
    workspace_id = uuid4()
    with ws(workspace_id):
        response = await provider.oauth_route(ctx, _request(query))
    assert response.status_code == provider.REDIRECT_STATUS
    location = urlparse(response.headers["location"])
    link_query = parse_qs(location.query)
    assert response.headers["location"].startswith(CONNECT_LINK)
    assert link_query["app"] == ["gmail"]
    assert link_query["oauthAppId"] == [OAUTH_APP_ID]
    success = urlparse(str(minted[0]["success_redirect_uri"]))
    assert success.path == provider.OAUTH_ROUTE_MOUNT
    success_query = parse_qs(success.query)
    assert success_query["state"] == ["SEALED"]
    assert success_query[provider.OUTCOME_PARAM] == [provider.OUTCOME_CONNECTED]
    assert minted[0]["external_user_id"] == pipedream.connection_user_id(workspace_id, "SEALED")
    error = parse_qs(urlparse(str(minted[0]["error_redirect_uri"])).query)
    assert error[provider.OUTCOME_PARAM] == [provider.OUTCOME_FAILED]


async def test_oauth_route_start_leg_keeps_the_consent_in_one_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hosted Connect Link opens the provider's consent in a second window unless told
    otherwise (`<base target="_blank">`, popup mode). `popups=false` pins its redirect mode, so
    the one window the member opened rides to the provider and back through this bridge."""
    _install_transport(monkeypatch, _pipedream_handler("ufo_ws"))
    ctx = context_for(pipedream_manifest.NAME, frozenset())
    query = f"provider={PROVIDER}&state=SEALED&callback={EXPECTED_REDIRECT_URI}"
    with ws(uuid4()):
        response = await provider.oauth_route(ctx, _request(query))
    link_query = parse_qs(urlparse(response.headers["location"]).query)
    assert link_query[provider.CONNECT_LINK_POPUPS_PARAM] == ["false"]


async def test_oauth_route_start_leg_rides_the_shared_client_when_no_custom_one_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pipedream's shared Google client passes consent for restricted Gmail scopes (verified
    live), so an unset custom-client env means the Connect Link simply carries no `oauthAppId` —
    never a refusal."""
    monkeypatch.delenv("PIPEDREAM_GMAIL_OAUTH_APP_ID")
    _install_transport(monkeypatch, _pipedream_handler("ufo_ws"))
    ctx = context_for(pipedream_manifest.NAME, frozenset())
    query = f"provider={PROVIDER}&state=SEALED&callback={EXPECTED_REDIRECT_URI}"
    with ws(uuid4()):
        response = await provider.oauth_route(ctx, _request(query))
    assert response.status_code == provider.REDIRECT_STATUS
    link_query = parse_qs(urlparse(response.headers["location"]).query)
    assert link_query["app"] == ["gmail"]
    assert "oauthAppId" not in link_query


async def test_oauth_route_return_leg_resolves_the_state_scoped_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    owner = pipedream.connection_user_id(workspace_id, "SEALED")
    _install_transport(monkeypatch, _pipedream_handler(owner))
    ctx = context_for(pipedream_manifest.NAME, frozenset())
    query = (
        f"provider={PROVIDER}&state=SEALED&callback={EXPECTED_REDIRECT_URI}"
        f"&{provider.OUTCOME_PARAM}={provider.OUTCOME_CONNECTED}"
    )
    with ws(workspace_id):
        response = await provider.oauth_route(ctx, _request(query))
    assert response.status_code == provider.REDIRECT_STATUS
    landing = urlparse(response.headers["location"])
    assert f"{landing.scheme}://{landing.netloc}{landing.path}" == EXPECTED_REDIRECT_URI
    landing_query = parse_qs(landing.query)
    assert landing_query["state"] == ["SEALED"]
    assert landing_query["code"] == [PIPEDREAM_ACCOUNT]


async def test_oauth_route_failed_consent_answers_loud_instead_of_reminting_consent() -> None:
    ctx = context_for(pipedream_manifest.NAME, frozenset())
    query = (
        f"provider={PROVIDER}&state=SEALED&callback={EXPECTED_REDIRECT_URI}"
        f"&{provider.OUTCOME_PARAM}={provider.OUTCOME_FAILED}"
    )
    with ws(uuid4()):
        response = await provider.oauth_route(ctx, _request(query))
    assert response.status_code == provider.FAILED_CONSENT_STATUS
    assert "location" not in response.headers


def test_serve_registers_gmail_with_label_and_broker() -> None:
    flow = _connect_flow(_credentials(), _config(), (pipedream_manifest.manifest(),))
    assert flow is not None
    assert set(flow.providers) == set(pipedream.CONNECTORS)
    assert flow.providers[PROVIDER].host == PROVIDER_HOST
    registry = _registry()
    entry = registry.entry(PROVIDER)
    assert entry.label == "Gmail"
    assert isinstance(entry.broker, PipedreamBroker)


def test_composio_and_pipedream_register_disjoint_providers() -> None:
    """Both brokers install side by side: gmail resolves to Pipedream, everything else to
    Composio, in the one registry `serve` builds — the routing the user-visible split rides on."""
    import ufo_ext_composio.manifest as composio_manifest
    from ufo_ext_composio.broker import ComposioBroker

    registry = _connector_registry(
        _config(),
        (composio_manifest.manifest(), pipedream_manifest.manifest()),
        _credentials(),
    )
    assert isinstance(registry.entry("gmail").broker, PipedreamBroker)
    assert isinstance(registry.entry("github").broker, ComposioBroker)


async def test_describe_external_tools_builds_the_schema_from_configurable_props(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The action's agent-facing schema offers exactly the settable props: the app slot is the
    broker's to bind (never the agent's), an optional prop is not required."""
    _install_transport(monkeypatch, _pipedream_handler("ufo_ws"))
    result = await describe_external_tools(
        _ctx(uuid4(), uuid4(), uuid4(), None),
        DescribeExternalToolsInput(
            user_description=TOOL_NARRATION, source_id=PROVIDER, tool_names=(GMAIL_ACTION,)
        ),
    )
    payload = json.loads(result.content[0].text)
    schema = payload["schemas"][GMAIL_ACTION]["input_schema"]
    assert set(schema["properties"]) == {"to", "draft"}
    assert schema["required"] == ["to"]
    assert schema["properties"]["to"] == {"type": "string", "description": "Recipient"}


async def test_describe_external_tools_marks_an_unknown_name_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_transport(monkeypatch, _pipedream_handler("ufo_ws"))
    result = await describe_external_tools(
        _ctx(uuid4(), uuid4(), uuid4(), None),
        DescribeExternalToolsInput(
            user_description=TOOL_NARRATION, source_id=PROVIDER, tool_names=(UNKNOWN_ACTION,)
        ),
    )
    payload = json.loads(result.content[0].text)
    assert payload["unresolved"] == [UNKNOWN_ACTION]
    assert [tool["slug"] for tool in payload["availableTools"]] == [GMAIL_ACTION]
    assert set(payload["availableTools"][0]["input_schema"]["properties"]) == {"to", "draft"}


async def test_describe_external_tools_reaches_the_action_listings_second_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An app's actions are paged, so the key the query really wants can sit past the first page:
    the walk follows `page_info.end_cursor` into it, each request carries a full page size and the
    caller's `q`, and every row reaches `availableTools` with the schema its props derive."""
    recorded: list[httpx.QueryParams] = []
    _install_transport(monkeypatch, _paged_actions_handler(recorded))
    result = await describe_external_tools(
        _ctx(uuid4(), uuid4(), uuid4(), None),
        DescribeExternalToolsInput(
            user_description=TOOL_NARRATION, source_id=PROVIDER, query=DISCOVERY_QUERY
        ),
    )
    listed = json.loads(result.content[0].text)["availableTools"]
    assert len(listed) == pipedream.ACTION_PAGE_LIMIT + 1
    assert listed[-1]["slug"] == SECOND_PAGE_ACTION
    assert listed[-1]["input_schema"]["required"] == ["to"]
    assert [dict(params) for params in recorded] == [
        {"app": "gmail", "limit": str(pipedream.ACTION_PAGE_LIMIT), "q": DISCOVERY_QUERY},
        {
            "app": "gmail",
            "limit": str(pipedream.ACTION_PAGE_LIMIT),
            "q": DISCOVERY_QUERY,
            "after": SECOND_PAGE_CURSOR,
        },
    ]


async def test_list_actions_stops_at_the_row_cap_while_a_cursor_is_still_offered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The walk is bounded whatever Connect says: an app that keeps offering a cursor is read to
    `MAX_LISTED_ACTIONS` rows and no further, so a runaway catalog cannot drive an unbounded number
    of requests."""
    pages: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth/token":
            return httpx.Response(200, json={"access_token": "at_test", "expires_in": 3600})
        pages.append(1)
        data = [{"key": f"gmail-{len(pages)}-{n}"} for n in range(pipedream.ACTION_PAGE_LIMIT)]
        return httpx.Response(
            200, json={"data": data, "page_info": {"end_cursor": SECOND_PAGE_CURSOR}}
        )

    _install_transport(monkeypatch, handle)
    rows = await pipedream.pipedream_client().list_actions("gmail")
    assert len(rows) == pipedream.MAX_LISTED_ACTIONS
    assert len(pages) == pipedream.MAX_LISTED_ACTIONS // pipedream.ACTION_PAGE_LIMIT


async def test_search_connector_tools_bounds_the_catalog_it_answers_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pipedream has no router, so its search is the same paged catalog listing, every row of which
    now carries a schema — the answer is bounded to what the model keeps in context and says how
    many actions were left out, rather than being offloaded to a file."""
    props = ACTION_PROPS + [
        {"name": f"field_{n}", "type": "string", "description": "x" * 40} for n in range(20)
    ]

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth/token":
            return httpx.Response(200, json={"access_token": "at_test", "expires_in": 3600})
        data = [
            {
                "key": f"gmail-action-{n}",
                "description": ACTION_DESCRIPTION,
                "configurable_props": props,
            }
            for n in range(pipedream.ACTION_PAGE_LIMIT)
        ]
        return httpx.Response(200, json={"data": data})

    _install_transport(monkeypatch, handle)
    result = await search_connector_tools(
        _ctx(uuid4(), uuid4(), uuid4(), None),
        SearchConnectorToolsInput(
            user_description=TOOL_NARRATION, source_id=PROVIDER, query=DISCOVERY_QUERY
        ),
    )
    text = result.content[0].text
    payload = json.loads(text)
    assert 0 < len(payload["tools"]) < pipedream.ACTION_PAGE_LIMIT
    assert len(text) <= MAX_TOOL_RESULT_CHARS, f"{len(text)} chars is past the inline budget"
    assert payload[SEARCH_TOOLS_NOTE_KEY] == AVAILABLE_TOOLS_OMITTED_NOTE.format(
        omitted=pipedream.ACTION_PAGE_LIMIT - len(payload["tools"]),
        total=pipedream.ACTION_PAGE_LIMIT,
    )


async def test_search_connector_tools_falls_back_to_top_actions_and_marks_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connect's `q` is a term match, so the use-case sentence this search is built for routinely
    answers empty. It must not dead-end there: the answer is the app's unqueried top actions, marked
    as catalog order — the same rule discovery follows, held at the seam both tools share."""
    recorded: list[httpx.QueryParams] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth/token":
            return httpx.Response(200, json={"access_token": "at_test", "expires_in": 3600})
        recorded.append(request.url.params)
        if request.url.params.get("q"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"data": [_action_row(GMAIL_ACTION)]})

    _install_transport(monkeypatch, handle)
    result = await search_connector_tools(
        _ctx(uuid4(), uuid4(), uuid4(), None),
        SearchConnectorToolsInput(
            user_description=TOOL_NARRATION, source_id=PROVIDER, query=DISCOVERY_QUERY
        ),
    )
    payload = json.loads(result.content[0].text)
    assert [tool["slug"] for tool in payload["tools"]] == [GMAIL_ACTION]
    assert payload["tools"][0]["input_schema"]["required"] == ["to"]
    assert payload[SEARCH_TOOLS_NOTE_KEY] == AVAILABLE_TOOLS_FALLBACK_NOTE
    assert [params.get("q") for params in recorded] == [DISCOVERY_QUERY, None]


async def test_connect_binds_a_grant_and_call_external_tool_executes_via_pipedream(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end across both extensions: connect the Gmail account in chat — `exchange` resolves
    the newest account of this workspace's external user, never trusting the return leg — binding a
    grant that carries the Pipedream account id, then the `connectors` extension's
    `call_external_tool` resolves that grant, routes through the registry to the Pipedream broker,
    and runs the action server-side with the account bound through the app slot's
    `authProvisionId` — no sandbox, no proxy."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    turn_id = await _turn(workspace_id, agent_id, conversation_id)
    credentials = _credentials()
    flow = _connect_flow(
        credentials, _config(), (connectors_manifest.manifest(), pipedream_manifest.manifest())
    )
    assert flow is not None
    install_connect_flow(flow)

    begin = await connect_account_handler(
        _turn_context(workspace_id, agent_id, conversation_id, member_id, turn_id),
        ConnectAccountInput(user_description=TOOL_NARRATION, provider=PROVIDER),
    )
    request = ConnectRequest.model_validate_json(begin.content[0].text.splitlines()[1])
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
    state = parse_qs(urlparse(url).query)["state"][0]
    owner = pipedream.connection_user_id(workspace_id, state)
    executed: list[dict[str, object]] = []
    _install_transport(monkeypatch, _pipedream_handler(owner, executed=executed))
    recorded = await flow.complete(state=state, code=PIPEDREAM_ACCOUNT)
    assert (recorded.provider, recorded.account_id) == (PROVIDER, PIPEDREAM_ACCOUNT)
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.connection.c.host, tables.connection.c.account_id).where(
                    tables.connection.c.workspace_id == workspace_id
                )
            )
        ).one()
    assert (row.host, row.account_id) == (PROVIDER_HOST, PIPEDREAM_ACCOUNT)

    tools, ext_by_tool = turn_tools(
        (connectors_manifest.manifest(), pipedream_manifest.manifest()),
        credentials,
        audience=conversation_audience(None),
    )
    tool = next(t for t in tools if t.name == "call_external_tool")
    ctx = _ctx(
        workspace_id,
        agent_id,
        conversation_id,
        turn_id,
        flow.store,
        ext_by_tool[tool.name],
        speaker_member_id=member_id,
    )
    with ws(workspace_id), agent(agent_id):
        result = await tool.handler(
            ctx,
            tool.input_model.model_validate(
                {
                    "user_description": TOOL_NARRATION,
                    "tool_name": GMAIL_ACTION,
                    "source_id": PROVIDER,
                    "arguments": {"to": "a@b.test"},
                }
            ),
        )
    assert result.is_error is False
    assert json.loads(result.content[0].text)["exports"]["$summary"] == "sent"
    assert executed == [
        {
            "id": GMAIL_ACTION,
            "external_user_id": owner,
            "configured_props": {
                "to": "a@b.test",
                "gmail": {"authProvisionId": PIPEDREAM_ACCOUNT},
            },
            "stash_id": pipedream.STASH_NEW,
        }
    ]


async def test_call_external_tool_executes_a_workspace_owned_grant(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    turn_id = await _turn(workspace_id, agent_id, conversation_id)
    store = GrantStore()
    with ws(workspace_id), agent(agent_id):
        await store.record(
            provider=PROVIDER,
            account_id=PIPEDREAM_ACCOUNT,
            host=PROVIDER_HOST,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=False,
        )
    owner = f"{pipedream.EXTERNAL_USER_PREFIX}{workspace_id}"
    executed: list[dict[str, object]] = []
    _install_transport(monkeypatch, _pipedream_handler(owner, executed=executed))
    with ws(workspace_id), agent(agent_id):
        result = await call_external_tool(
            _ctx(
                workspace_id,
                agent_id,
                conversation_id,
                turn_id,
                store,
                speaker_member_id=member_id,
            ),
            CallExternalToolInput(
                user_description=TOOL_NARRATION,
                tool_name=GMAIL_ACTION,
                source_id=PROVIDER,
                arguments={"to": "a@b.test"},
            ),
        )
    assert result.is_error is False
    assert executed == [
        {
            "id": GMAIL_ACTION,
            "external_user_id": owner,
            "configured_props": {
                "to": "a@b.test",
                "gmail": {"authProvisionId": PIPEDREAM_ACCOUNT},
            },
            "stash_id": pipedream.STASH_NEW,
        }
    ]


def test_file_outputs_projects_the_stash_uploads_to_presigned_urls() -> None:
    response = {
        "exports": {
            "$summary": "downloaded",
            pipedream.FILESTASH_UPLOADS_EXPORT: [
                {
                    "localPath": "/tmp/Order_Form.pdf",
                    "s3Key": "1day/proj_x/exu_y/Order_Form.pdf",
                    "get_url": "https://stash.test/Order_Form.pdf?sig=x",
                },
                {"localPath": "/tmp/broken", "s3Key": "k"},
            ],
        },
        "ret": {"filename": "Order_Form.pdf"},
    }
    outputs = PipedreamBroker().file_outputs(response)
    assert [(file.name, file.url) for file in outputs] == [
        ("Order_Form.pdf", "https://stash.test/Order_Form.pdf?sig=x")
    ]


def test_file_outputs_is_empty_without_a_stash() -> None:
    assert PipedreamBroker().file_outputs({"exports": {"$summary": "sent"}, "ret": None}) == ()


async def test_stage_upload_routes_the_agent_to_share_file() -> None:
    with pytest.raises(ValueError, match="share_file"):
        await PipedreamBroker().stage_upload(
            uuid4(), PROVIDER, GMAIL_ACTION, "form.pdf", "application/pdf", "abc123"
        )


def test_manifest_declares_the_broker_file_transfer_hosts() -> None:
    hosts = connector_transfer_hosts((pipedream_manifest.manifest(),))
    assert hosts.of(PROVIDER) == pipedream.PIPEDREAM_TRANSFER_HOSTS
    assert hosts.default == ()


async def test_call_external_tool_answers_an_unknown_key_with_the_closest_actions(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A near-miss key is answered with the app's closest real keys; a key nothing resembles is
    routed to the discovery verb rather than the catalog — a live app lists hundreds of actions, so
    the whole list costs more context than the miss."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    turn_id = await _turn(workspace_id, agent_id, conversation_id)
    store = GrantStore()
    with ws(workspace_id), agent(agent_id):
        await store.record(
            provider=PROVIDER,
            account_id=PIPEDREAM_ACCOUNT,
            host=PROVIDER_HOST,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=False,
        )
    _install_transport(
        monkeypatch,
        _pipedream_handler(pipedream.connection_user_id(workspace_id, "unknown-key")),
    )
    ctx = _ctx(workspace_id, agent_id, conversation_id, turn_id, store, speaker_member_id=member_id)

    async def _call(tool_name: str) -> str:
        with (
            ws(workspace_id),
            agent(agent_id),
            pytest.raises(pipedream.PipedreamError) as raised,
        ):
            await call_external_tool(
                ctx,
                CallExternalToolInput(
                    user_description=TOOL_NARRATION,
                    tool_name=tool_name,
                    source_id=PROVIDER,
                    arguments={},
                ),
            )
        return str(raised.value)

    assert f"closest: {GMAIL_ACTION}" in await _call(TYPO_ACTION)
    unresembled = await _call(UNKNOWN_ACTION)
    assert "describe_external_tools" in unresembled
    assert GMAIL_ACTION not in unresembled


async def test_call_external_tool_with_a_stale_grant_says_reconnect(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure observed live on testing: a gmail grant recorded through a previous broker
    resolves normally, but Pipedream knows no such external user — the error must route the agent
    into the connect flow, never read as an outage."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    turn_id = await _turn(workspace_id, agent_id, conversation_id)
    store = GrantStore()
    with ws(workspace_id), agent(agent_id):
        await store.record(
            provider=PROVIDER,
            account_id="ca_composio_era",
            host=PROVIDER_HOST,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=False,
        )
    base = _pipedream_handler(pipedream.connection_user_id(workspace_id, "stale"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/accounts/ca_composio_era"):
            return httpx.Response(404, json={"error": "External user not found"})
        return base(request)

    _install_transport(monkeypatch, handler)
    ctx = _ctx(workspace_id, agent_id, conversation_id, turn_id, store, speaker_member_id=member_id)
    with (
        ws(workspace_id),
        agent(agent_id),
        pytest.raises(pipedream.PipedreamError, match="reconnect with connect_account"),
    ):
        await call_external_tool(
            ctx,
            CallExternalToolInput(
                user_description=TOOL_NARRATION,
                tool_name=GMAIL_ACTION,
                source_id=PROVIDER,
                arguments={},
            ),
        )


async def test_an_in_band_action_error_says_reconnect_only_for_a_stale_account(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`actions/run` can fail in-band — a 200 whose body carries `error` — and that path must make
    the same stale-account call: a body naming the unknown external user routes the agent to
    reconnect, while a provider-domain error that merely says some upstream account was not found
    raises plain, its shape untouched — never a false reconnect."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    turn_id = await _turn(workspace_id, agent_id, conversation_id)
    store = GrantStore()
    with ws(workspace_id), agent(agent_id):
        await store.record(
            provider=PROVIDER,
            account_id=PIPEDREAM_ACCOUNT,
            host=PROVIDER_HOST,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=False,
        )
    base = _pipedream_handler(pipedream.connection_user_id(workspace_id, "in-band"))
    in_band_error: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/actions/run"):
            return httpx.Response(200, json={"exports": {}, "os": [], "error": in_band_error})
        return base(request)

    _install_transport(monkeypatch, handler)
    ctx = _ctx(workspace_id, agent_id, conversation_id, turn_id, store, speaker_member_id=member_id)

    in_band_error.update({"name": "Error", "message": "External user not found"})
    with (
        ws(workspace_id),
        agent(agent_id),
        pytest.raises(pipedream.PipedreamError, match="reconnect with connect_account"),
    ):
        await call_external_tool(
            ctx,
            CallExternalToolInput(
                user_description=TOOL_NARRATION,
                tool_name=GMAIL_ACTION,
                source_id=PROVIDER,
                arguments={},
            ),
        )

    in_band_error.clear()
    in_band_error.update({"name": "Error", "message": "CRM account not found for id 123"})
    with ws(workspace_id), agent(agent_id), pytest.raises(pipedream.PipedreamError) as raised:
        await call_external_tool(
            ctx,
            CallExternalToolInput(
                user_description=TOOL_NARRATION,
                tool_name=GMAIL_ACTION,
                source_id=PROVIDER,
                arguments={},
            ),
        )
    assert "connect_account" not in str(raised.value)


async def test_complete_rejects_a_forged_success_marker(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth/token":
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        return httpx.Response(404, json={"error": f"{provider.OUTCOME_CONNECTED} not found"})

    _install_transport(monkeypatch, handler)
    workspace_id = uuid4()
    flow = _connect_flow(_credentials(), _config(), (pipedream_manifest.manifest(),))
    assert flow is not None
    url = flow.authorize(
        workspace_id=workspace_id,
        agent_id=uuid4(),
        provider=PROVIDER,
        grantor_member_id=uuid4(),
        conversation_id=uuid4(),
        shared=False,
    )
    state = parse_qs(urlparse(url).query)["state"][0]
    with pytest.raises(pipedream.PipedreamError, match=provider.OUTCOME_CONNECTED):
        await flow.complete(state=state, code=provider.OUTCOME_CONNECTED)
    async with workspace_tx() as connection:
        count = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.connector_grant)
                .where(tables.connector_grant.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert count == 0


async def test_overlapping_connect_flows_cannot_cross_bind_accounts(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _workspace()
    member_a, agent_a = await _member_agent(workspace_id)
    member_b, agent_b = await _member_agent(workspace_id, "assistant-b")
    conversation_a = await _conversation(workspace_id, member_a)
    conversation_b = await _conversation(workspace_id, member_b)
    flow = _connect_flow(_credentials(), _config(), (pipedream_manifest.manifest(),))
    assert flow is not None

    state_a = parse_qs(
        urlparse(
            flow.authorize(
                workspace_id=workspace_id,
                agent_id=agent_a,
                provider=PROVIDER,
                grantor_member_id=member_a,
                conversation_id=conversation_a,
                shared=False,
            )
        ).query
    )["state"][0]
    state_b = parse_qs(
        urlparse(
            flow.authorize(
                workspace_id=workspace_id,
                agent_id=agent_b,
                provider=PROVIDER,
                grantor_member_id=member_b,
                conversation_id=conversation_b,
                shared=False,
            )
        ).query
    )["state"][0]
    account_a = "apn_flow_a"
    account_b = "apn_flow_b"
    owners = {
        pipedream.connection_user_id(workspace_id, state_a): account_a,
        pipedream.connection_user_id(workspace_id, state_b): account_b,
    }
    accounts = {account_id: _account(owner, account_id) for owner, account_id in owners.items()}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/oauth/token":
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        if request.method == "GET" and path.endswith("/accounts"):
            owner = request.url.params["external_user_id"]
            return httpx.Response(200, json={"data": [accounts[owners[owner]]]})
        if request.method == "GET" and "/accounts/" in path:
            account_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"data": accounts[account_id]})
        return httpx.Response(404, json={})

    _install_transport(monkeypatch, handler)
    ctx = context_for(pipedream_manifest.NAME, frozenset())

    async def return_code(state: str) -> str:
        query = urlencode(
            {
                "provider": PROVIDER,
                "state": state,
                "callback": EXPECTED_REDIRECT_URI,
                provider.OUTCOME_PARAM: provider.OUTCOME_CONNECTED,
            }
        )
        response = await provider.oauth_route(ctx, _request(query))
        return parse_qs(urlparse(response.headers["location"]).query)["code"][0]

    with ws(workspace_id):
        code_b = await return_code(state_b)
        code_a = await return_code(state_a)
    assert (code_a, code_b) == (account_a, account_b)

    with pytest.raises(pipedream.PipedreamError, match="owned by"):
        await flow.complete(state=state_a, code=code_b)
    await flow.complete(state=state_a, code=code_a)
    await flow.complete(state=state_b, code=code_b)

    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.connector_grant.c.agent_id,
                    tables.connection.c.account_id,
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
    assert set(rows) == {(agent_a, account_a), (agent_b, account_b)}


def _request(query: str) -> Request:
    return Request({"type": "http", "method": "GET", "headers": [], "query_string": query.encode()})


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
            inbound="connect my gmail",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=member_id,
        audience=conversation_audience(member_id),
        artifact_token_secret="",
    )


def _ctx(
    workspace_id: UUID,
    agent_id: UUID,
    conversation_id: UUID,
    turn_id: UUID | None,
    grants: GrantStore | None = None,
    ext: object = None,
    speaker_member_id: UUID | None = None,
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
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
            speaker_member_id=speaker_member_id,
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=speaker_member_id,
        audience=conversation_audience(None),
        artifact_token_secret="",
        grants=grants,
        ext=ext,
        connectors=_registry(),
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


async def _member_agent(workspace_id: UUID, agent_name: str = "assistant") -> tuple[UUID, UUID]:
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
                name=agent_name,
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
