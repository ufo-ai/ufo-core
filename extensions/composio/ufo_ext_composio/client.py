"""Composio the connector broker: the async client, the provider registry, the errors.

Composio brokers hundreds of services and thousands of tools, so the agent never holds a fixed
per-provider tool — it discovers and executes them dynamically. `connect_link`/`connected_account`
run the consent handoff (managed OAuth, then the account bound to this workspace's broker user, its
ownership confirmed from account metadata); `list_tools`/`tool_schema` are the catalog the dynamic
tools search and describe; `execute_tool` runs a tool on Composio's server-side execute API, which
holds the account's token and injects it itself — no sentinel, no egress proxy. The token never
leaves Composio, so the grant stores only the connected-account id, never a secret. The client
speaks Composio's v3 REST API over httpx; the deploy's single broker key is read loud from the
environment (one Composio account per deploy, the analog of the model key)."""

import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

import httpx

from ufo.sdk.connectors import BrokerSearch, BrokerTool, OAuthAccount
from ufo_ext_composio import mcp_session

COMPOSIO_API_BASE = "https://backend.composio.dev/api/v3.1"
COMPOSIO_API_KEY_ENV = "COMPOSIO_API_KEY"
EXTERNAL_USER_PREFIX = "ufo_"
COMPOSIO_TIMEOUT_SECONDS = 30.0
ACTIVE_STATUS = "ACTIVE"
TOOL_SEARCH_LIMIT = 10
MAX_EXECUTE_ARGUMENTS_BYTES = 1024 * 1024
IDEMPOTENCY_HEADER = "x-idempotency-key"
TOOL_ROUTER_TIMEOUT_SECONDS = 30.0
TOOL_ROUTER_SESSION_PATH = "/tool_router/session"
COMPOSIO_SEARCH_TOOL = "COMPOSIO_SEARCH_TOOLS"


@dataclass(frozen=True)
class ConnectorSpec:
    """One brokered connector: the member-facing label, the Composio toolkit slug whose managed or
    custom OAuth config grants the account, and the provider's own API `host` the derived grant
    admits and injects at the egress proxy — direct-provider-host, never Composio's backend."""

    label: str
    toolkit: str
    host: str


@dataclass(frozen=True)
class ToolRouterSession:
    """A Composio Tool Router session: its id and the MCP endpoint (`mcp.url`) semantic tool search
    runs against. Minted per (broker user, toolkit) and cached, so a burst of searches on one
    connector shares one session rather than reopening the router each time."""

    id: str
    url: str


CONNECTORS: dict[str, ConnectorSpec] = {
    "github": ConnectorSpec("GitHub", "github", "api.github.com"),
    "google_calendar": ConnectorSpec("Google Calendar", "googlecalendar", "www.googleapis.com"),
    "google_sheets": ConnectorSpec("Google Sheets", "googlesheets", "sheets.googleapis.com"),
    "google_drive": ConnectorSpec("Google Drive", "googledrive", "www.googleapis.com"),
    "google_meet": ConnectorSpec("Google Meet", "googlemeet", "meet.googleapis.com"),
    "slack": ConnectorSpec("Slack", "slack", "slack.com"),
    "notion": ConnectorSpec("Notion", "notion", "api.notion.com"),
    "linear": ConnectorSpec("Linear", "linear", "api.linear.app"),
    "asana": ConnectorSpec("Asana", "asana", "app.asana.com"),
    "stripe": ConnectorSpec("Stripe", "stripe", "api.stripe.com"),
    "hubspot": ConnectorSpec("HubSpot", "hubspot", "api.hubapi.com"),
    "calendly": ConnectorSpec("Calendly", "calendly", "api.calendly.com"),
    "intercom": ConnectorSpec("Intercom", "intercom", "api.intercom.io"),
    "airtable": ConnectorSpec("Airtable", "airtable", "api.airtable.com"),
    "monday": ConnectorSpec("Monday", "monday", "api.monday.com"),
    "pagerduty": ConnectorSpec("PagerDuty", "pagerduty", "api.pagerduty.com"),
    "sentry": ConnectorSpec("Sentry", "sentry", "sentry.io"),
    "typeform": ConnectorSpec("Typeform", "typeform", "api.typeform.com"),
    "klaviyo": ConnectorSpec("Klaviyo", "klaviyo", "a.klaviyo.com"),
    "clickup": ConnectorSpec("ClickUp", "clickup", "api.clickup.com"),
    "outlook": ConnectorSpec("Outlook", "outlook", "graph.microsoft.com"),
    "microsoft_teams": ConnectorSpec("Microsoft Teams", "microsoft_teams", "graph.microsoft.com"),
    "zendesk": ConnectorSpec("Zendesk", "zendesk", "api.zendesk.com"),
    "jira": ConnectorSpec("Jira", "jira", "api.atlassian.com"),
    "confluence": ConnectorSpec("Confluence", "confluence", "api.atlassian.com"),
    "freshdesk": ConnectorSpec("Freshdesk", "freshdesk", "api.freshdesk.com"),
    "bamboohr": ConnectorSpec("BambooHR", "bamboohr", "api.bamboohr.com"),
    "activecampaign": ConnectorSpec("ActiveCampaign", "active_campaign", "api.activecampaign.com"),
    "ashby": ConnectorSpec("Ashby", "ashby", "api.ashbyhq.com"),
    "brex": ConnectorSpec("Brex", "brex", "platform.brexapis.com"),
    "instagram": ConnectorSpec("Instagram", "instagram", "graph.instagram.com"),
    "mailchimp": ConnectorSpec("Mailchimp", "mailchimp", "api.mailchimp.com"),
    "quickbooks": ConnectorSpec("QuickBooks", "quickbooks", "quickbooks.api.intuit.com"),
    "recruitee": ConnectorSpec("Recruitee", "recruitee", "api.recruitee.com"),
    "square": ConnectorSpec("Square", "square", "connect.squareup.com"),
    "wrike": ConnectorSpec("Wrike", "wrike", "www.wrike.com"),
    "xero": ConnectorSpec("Xero", "xero", "api.xero.com"),
}


class ComposioError(RuntimeError):
    """A Composio API call failed or answered a shape the broker cannot use — fail loud, never a
    silent empty grant."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"composio {status}: {body}")
        self.status = status
        self.body = body


@dataclass(frozen=True)
class ComposioClient:
    """Composio v3 over httpx. `connect_link` mints the hosted OAuth link the member opens (riding
    the toolkit's existing auth config, else a managed one), and `connected_account` confirms an
    account is active, owned by the expected workspace user, and authenticates the requested
    toolkit. A foreign or cross-toolkit account id is refused from metadata without reading a
    token. `list_tools` and `tool_schema` are the catalog the dynamic tools search and describe;
    `execute_tool` runs one on Composio's server-side execute API for a bound account, bounding the
    arguments payload before the call. Each call opens and closes its own client so a transport
    override (a test's MockTransport) is honoured and no connection leaks."""

    api_key: str
    transport: httpx.AsyncBaseTransport | None = None

    async def connect_link(self, toolkit: str, user_id: str, callback_url: str) -> str:
        auth_config_id = await self._auth_config(toolkit)
        payload = await self._post(
            "/connected_accounts/link",
            {"auth_config_id": auth_config_id, "user_id": user_id, "callback_url": callback_url},
        )
        redirect = payload.get("redirect_url")
        if not isinstance(redirect, str) or not redirect:
            raise ComposioError(502, f"connect link carried no redirect_url: {payload!r}")
        return redirect

    async def connected_account(
        self, account_id: str, expected_user_id: str, expected_toolkit: str
    ) -> OAuthAccount:
        payload = await self._get(f"/connected_accounts/{account_id}")
        owner = payload.get("user_id")
        if not isinstance(owner, str) or owner != expected_user_id:
            raise ComposioError(
                403,
                f"connected account {account_id!r} is owned by {owner!r}, not {expected_user_id!r}",
            )
        status = str(payload.get("status") or "").upper()
        if status != ACTIVE_STATUS:
            raise ComposioError(409, f"connected account {account_id!r} is {status or 'unknown'}")
        toolkit = payload.get("toolkit")
        auth_config = payload.get("auth_config")
        if toolkit is None and isinstance(auth_config, dict):
            toolkit = auth_config.get("toolkit")
        slug = toolkit.get("slug") if isinstance(toolkit, dict) else toolkit
        if slug != expected_toolkit:
            raise ComposioError(
                403,
                f"connected account {account_id!r} authenticates toolkit {slug!r}, "
                f"not {expected_toolkit!r}",
            )
        return OAuthAccount(account_id=account_id)

    async def list_tools(
        self, toolkit: str, query: str = "", limit: int = TOOL_SEARCH_LIMIT
    ) -> dict[str, object]:
        params = {"toolkit_slug": toolkit, "limit": str(limit)}
        if query:
            params["query"] = query
        return await self._get("/tools", params=params)

    async def tool_schema(self, slug: str) -> dict[str, object]:
        return await self._get(f"/tools/{slug}")

    async def execute_tool(
        self,
        slug: str,
        arguments: Mapping[str, object],
        user_id: str,
        connected_account_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        body: dict[str, object] = {"user_id": user_id, "arguments": dict(arguments)}
        if connected_account_id:
            body["connected_account_id"] = connected_account_id
        if len(json.dumps(body).encode()) > MAX_EXECUTE_ARGUMENTS_BYTES:
            raise ValueError("connector tool arguments exceed the Composio execute payload bound")
        headers = {IDEMPOTENCY_HEADER: idempotency_key} if idempotency_key else None
        return await self._post(f"/tools/execute/{slug}", body, headers=headers)

    async def tool_router_session(self, user_id: str, toolkits: list[str]) -> ToolRouterSession:
        """Open a Tool Router session scoped to `toolkits` for `user_id`, returning its id and MCP
        endpoint. The endpoint hosts the `COMPOSIO_SEARCH_TOOLS` tool that semantic search calls."""
        payload = await self._post(
            TOOL_ROUTER_SESSION_PATH, {"user_id": user_id, "toolkits": {"enable": list(toolkits)}}
        )
        session_id = payload.get("session_id")
        mcp = payload.get("mcp")
        url = mcp.get("url") if isinstance(mcp, dict) else None
        if not isinstance(session_id, str) or not isinstance(url, str) or not url:
            raise ComposioError(502, f"tool router session carried no session_id/url: {payload!r}")
        return ToolRouterSession(id=session_id, url=url)

    async def _auth_config(self, toolkit: str) -> str:
        """The auth config the consent leg rides: the project's existing config for the toolkit —
        managed or custom, so an operator-created config (e.g. the deploy's own Google client
        requesting only the scopes a connector needs) wins — else a Composio-managed one is
        created."""
        existing = await self._get("/auth_configs", params={"toolkit_slug": toolkit, "limit": "1"})
        config_id = _auth_config_id(existing)
        if config_id:
            return config_id
        created = await self._post(
            "/auth_configs",
            {"toolkit": {"slug": toolkit}, "auth_config": {"type": "use_composio_managed_auth"}},
        )
        record = created.get("auth_config")
        record = record if isinstance(record, dict) else created
        created_id = record.get("id")
        if not isinstance(created_id, str):
            raise ComposioError(502, f"auth config carried no id: {created!r}")
        return created_id

    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, object]:
        async with self._http() as http:
            return _body(await http.get(path, params=params))

    async def _post(
        self, path: str, body: dict[str, object], headers: dict[str, str] | None = None
    ) -> dict[str, object]:
        async with self._http() as http:
            return _body(await http.post(path, json=body, headers=headers))

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=COMPOSIO_API_BASE,
            headers={"x-api-key": self.api_key},
            timeout=COMPOSIO_TIMEOUT_SECONDS,
            transport=self.transport,
        )


def _body(response: httpx.Response) -> dict[str, object]:
    if response.status_code >= 400:
        raise ComposioError(response.status_code, response.text)
    if not response.content:
        return {}
    payload = response.json()
    if not isinstance(payload, dict):
        raise ComposioError(response.status_code, f"composio answered a non-object: {payload!r}")
    return payload


def _auth_config_id(payload: dict[str, object]) -> str | None:
    items = payload.get("items")
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            return item["id"]
    return None


def composio_client() -> ComposioClient:
    """The deploy's Composio broker client, keyed from the environment. Raises when unset — a
    connector's consent leg cannot run without it, so the OAuth route and `exchange` fail loud
    rather than mint a link or a grant against no broker."""
    key = os.environ.get(COMPOSIO_API_KEY_ENV)
    if not key:
        raise RuntimeError(f"{COMPOSIO_API_KEY_ENV} is required to broker a connector's OAuth")
    return ComposioClient(api_key=key)


_SEARCH_SESSIONS: dict[tuple[str, str], ToolRouterSession] = {}
_SEARCH_SESSIONS_LOCK = asyncio.Lock()


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _search_result(result: dict[str, object]) -> BrokerSearch:
    """Project a Tool Router `COMPOSIO_SEARCH_TOOLS` result into the `BrokerSearch` the dynamic
    tools render: the matched tool slugs + input schemas plus the recommended plan steps, execution
    guidance, and known pitfalls the router surfaces so the model's next call is informed, not a
    blind guess."""
    inner = _dict(result.get("data")) or result
    schemas = _dict(inner.get("tool_schemas"))
    tools: list[BrokerTool] = []
    plan: list[str] = []
    guidance: list[str] = []
    pitfalls: list[str] = []
    seen: set[str] = set()
    results = inner.get("results")
    for res in results if isinstance(results, list) else []:
        item = _dict(res)
        for slug in _str_tuple(item.get("primary_tool_slugs")) + _str_tuple(
            item.get("related_tool_slugs")
        ):
            if slug in seen:
                continue
            seen.add(slug)
            schema = _dict(schemas.get(slug))
            tools.append(
                BrokerTool(
                    slug=slug,
                    description=str(schema.get("description") or ""),
                    input_schema=_dict(schema.get("input_schema")),
                )
            )
        plan.extend(_str_tuple(item.get("recommended_plan_steps")))
        guidance.extend(_str_tuple([item.get("execution_guidance")]))
        pitfalls.extend(_str_tuple(item.get("known_pitfalls")))
    return BrokerSearch(
        tools=tuple(tools), plan=tuple(plan), guidance=tuple(guidance), pitfalls=tuple(pitfalls)
    )


async def search_connector_tools(
    client: ComposioClient, workspace_id: UUID, connector: str, query: str
) -> BrokerSearch:
    """Semantic tool discovery via Composio's Tool Router: matched tool slugs + input schemas plus
    the recommended execution plan, guidance, and pitfalls. Search only — execution never goes via
    Tool Router, so metering and the grant stay on the execute API. The (broker user, toolkit)
    session is opened once and cached, so concurrent searches on one connector share it."""
    spec = CONNECTORS.get(connector)
    toolkit = spec.toolkit if spec is not None else connector
    user_id = f"{EXTERNAL_USER_PREFIX}{workspace_id}"
    key = (user_id, toolkit)
    session = _SEARCH_SESSIONS.get(key)
    if session is None:
        async with _SEARCH_SESSIONS_LOCK:
            session = _SEARCH_SESSIONS.get(key)
            if session is None:
                session = await client.tool_router_session(user_id, [toolkit])
                _SEARCH_SESSIONS[key] = session
    result = await mcp_session.mcp_call_tool(
        session.url,
        COMPOSIO_SEARCH_TOOL,
        {"queries": [{"use_case": query}]},
        {"x-api-key": client.api_key},
        TOOL_ROUTER_TIMEOUT_SECONDS,
    )
    return _search_result(result)
