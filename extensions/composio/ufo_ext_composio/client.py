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
import re
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

import httpx

from ufo.sdk.connectors import (
    WORKSPACE_FILE_KEY,
    BrokerSearch,
    BrokerTool,
    CatalogEntry,
    CatalogPage,
    OAuthAccount,
)
from ufo_ext_composio import mcp_session

COMPOSIO_API_BASE = "https://backend.composio.dev/api/v3.1"
COMPOSIO_API_KEY_ENV = "COMPOSIO_API_KEY"
EXTERNAL_USER_PREFIX = "ufo_"
COMPOSIO_TIMEOUT_SECONDS = 30.0
ACTIVE_STATUS = "ACTIVE"
TOOL_PAGE_LIMIT = 100
MAX_LISTED_TOOLS = 500
TOOLKIT_SEARCH_LIMIT = 10
MAX_EXECUTE_ARGUMENTS_BYTES = 1024 * 1024
ACCOUNT_PARAMETER_DEPTH = 4
IDEMPOTENCY_HEADER = "x-idempotency-key"
TOOL_ROUTER_TIMEOUT_SECONDS = 30.0
TOOL_ROUTER_SESSION_PATH = "/tool_router/session"
COMPOSIO_SEARCH_TOOL = "COMPOSIO_SEARCH_TOOLS"
FILES_UPLOAD_PATH = "/files/upload/request"
TOOLKITS_PATH = "/toolkits"
NOT_FOUND_STATUS = 404
TOOLKIT_SLUG = re.compile(r"[A-Za-z0-9_-]+\Z")
MANAGED_AUTH_SCHEMES_KEY = "composio_managed_auth_schemes"
TOOLKIT_META_KEY = "meta"
TOOLS_COUNT_KEY = "tools_count"
COMPOSIO_TRANSFER_HOSTS = ("temp.4d4f16c61d89ec64e760039c4ec50717.r2.cloudflarestorage.com",)
FILE_UPLOADABLE_KEY = "file_uploadable"


@dataclass(frozen=True)
class ConnectorSpec:
    """One connector whose grant needs a real provider host — the small CLI exception to the open
    catalog. Every other Composio toolkit is connectable through `ComposioResolver` by its slug
    alone, its brokered grant admitting no provider host (execution is server-side). Its provider
    name is the Composio toolkit slug (the key it is registered under). `cli_env` names the env var
    a provider CLI reads its token from (github's `GH_TOKEN`): the sandbox exports the grant's
    sentinel there and the proxy forwards the matching request to `host` through proxy-execute, so
    the CLI authenticates without the token existing on this deploy. `label` is member-facing."""

    label: str
    host: str
    cli_env: str | None = None


@dataclass(frozen=True)
class ToolRouterSession:
    """A Composio Tool Router session: its id and the MCP endpoint (`mcp.url`) semantic tool search
    runs against. Minted per (broker user, toolkit) and cached, so a burst of searches on one
    connector shares one session rather than reopening the router each time."""

    id: str
    url: str


@dataclass(frozen=True)
class ComposioUpload:
    """One minted upload slot on Composio's file store: the store `key` the tool argument names
    and the presigned `put_url` the sandbox PUTs the bytes to. `put_url` is None when Composio
    deduplicates by MD5 and answers `type: "exists"` — the key already holds the bytes, so the
    sandbox reuses it without re-PUTing."""

    key: str
    put_url: str | None


CONNECTORS: dict[str, ConnectorSpec] = {
    "github": ConnectorSpec("GitHub", "api.github.com", cli_env="GH_TOKEN"),
}


BANNED: dict[str, str] = {
    "apaleo": "no reservation, availability or rate tool — only property and unit setup",
    "attio": "record writes need record_permission:read-write, ungranted — a read-only CRM",
    "blackbaud": "no constituent tool, and the one gift write needs a batch it cannot create",
    "boldsign": "no tool retrieves a signed document — the signature round trip never closes",
    "confluence": "creating a page and listing spaces need read:space, ungranted",
    "digital_ocean": "the managed grant is read-only, so nothing can be provisioned",
    "discord": "no message or channel tool at all — user OAuth cannot reach chat content",
    "dynamics365": "no read tool for contacts, accounts or opportunities — only leads and invoices",
    "freshbooks": "no invoice, payment or expense tool — only client and project lookups",
    "google_classroom": "no tool enters or returns a grade on a submission",
    "googlephotos": "the grant sees only app-created media, never the member's own library",
    "googlesuper": "restates gmail, drive, calendar and meet under one slug, reachable differently",
    "gorgias": "no tool posts a reply into a ticket",
    "greenhouse": "advancing, rejecting and noting a candidate all need ungranted scopes",
    "linear": "no server-side filter for issue state or label",
    "mural": "a sticky note is its only write — cannot create a board or edit anything",
    "omnisend": "no tool creates or sends a campaign",
    "servicem8": "no tool updates a job after creation, or creates the customer it is for",
    "square": "only the customer directory is granted — no payments, orders or invoices",
    "yandex": "the grant authorizes identity alone; Disk reads only others' public files",
    "ynab": "no tool enters or categorises a transaction, only scheduled ones",
    "zoho_desk": "ticket threads are read-only — no tool answers the customer",
}
"""Toolkits the open namespace declines to claim, each mapped to the gap that disqualifies it.

Judgement, not a derived gate: the gaps are visible in the live catalog but not safely computable
from it, because scope metadata both over- and under-reports. Every entry's evidence, and the rule
for what earns one, is in docs/composio-provider-coverage.md."""


def connectable(slug: str, toolkit: Mapping[str, object]) -> bool:
    """Whether a member can reach a toolkit's tools through this deploy: `slug` is the identifier
    the caller already trusts, `toolkit` the record Composio's catalog carries for it (either the
    search item or the detail payload).

    The ban is keyed on the caller's slug, never on one read back out of `toolkit` — the detail
    payload echoing a `slug` field is Composio's to change, and a gate that silently no-ops when a
    remote drops a field is no gate. It is matched case-folded: Composio resolves a toolkit path
    case-insensitively (`/toolkits/AtTiO` answers for `attio`), so an exact-case test would let any
    banned slug through under a different capitalisation. The credential facts have no such
    alternative and are read off the record.

    Three preconditions, and a toolkit failing any brokers nothing worth having here. The consent
    leg rides Composio-managed credentials (`_auth_config` creates a managed config when the project
    holds none), so a toolkit Composio holds no managed credentials for cannot mint a working link —
    `POST /auth_configs` refuses it outright. A toolkit cataloguing no tool has nothing for the
    dynamic connector tools to search or execute. And a toolkit named in `BANNED` is withheld by
    judgement. Checked before a slug is claimed rather than after, so the
    failure lands on the connect request instead of a dead grant."""
    if slug.lower() in BANNED:
        return False
    schemes = toolkit.get(MANAGED_AUTH_SCHEMES_KEY)
    meta = toolkit.get(TOOLKIT_META_KEY)
    tools = meta.get(TOOLS_COUNT_KEY) if isinstance(meta, Mapping) else None
    return bool(isinstance(schemes, list) and schemes and isinstance(tools, int) and tools > 0)


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
    token, and `account_parameter` reads one named identifier off that same confirmed metadata (the
    company id a per-company provider's API address carries). `list_tools` and `tool_schema` are the
    catalog the dynamic tools search and describe;
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
        await self._account(account_id, expected_user_id, expected_toolkit)
        return OAuthAccount(account_id=account_id)

    async def account_parameter(
        self, account_id: str, expected_user_id: str, expected_toolkit: str, key: str
    ) -> str | None:
        """One named identifier the account's connection carries — the QuickBooks company id the
        provider issued at consent — or None where it carries none. The account is confirmed as this
        workspace's first, exactly as `connected_account` does. Composio nests a connection's own
        parameters under the auth state that stored them, and the nesting differs per auth scheme,
        so the key is read wherever the account payload holds it."""
        payload = await self._account(account_id, expected_user_id, expected_toolkit)
        return _parameter(payload, key)

    async def _account(
        self, account_id: str, expected_user_id: str, expected_toolkit: str
    ) -> dict[str, object]:
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
        return payload

    async def account_label(self, account_id: str) -> str | None:
        payload = await self._get(f"/connected_accounts/{account_id}")
        alias = payload.get("alias")
        return alias if isinstance(alias, str) and alias else None

    async def list_tools(self, toolkit: str, query: str = "") -> tuple[dict[str, object], ...]:
        """Every tool row the toolkit catalogs for `query`, following `next_cursor` to the end of
        the listing — a toolkit's tools run to the hundreds, so one page is a slice of Composio's
        own order and a real slug on page two would be invisible to discovery. The walk ends on a
        short page (the last page carries no cursor) or at `MAX_LISTED_TOOLS` rows, which stops it
        even where the listing offers another cursor: past that count no caller renders the rows."""
        rows: list[dict[str, object]] = []
        cursor = ""
        while True:
            params = {"toolkit_slug": toolkit, "limit": str(TOOL_PAGE_LIMIT)}
            if query:
                params["query"] = query
            if cursor:
                params["cursor"] = cursor
            payload = await self._get("/tools", params=params)
            items = payload.get("items")
            page = (
                [item for item in items if isinstance(item, dict)]
                if isinstance(items, list)
                else []
            )
            rows.extend(page)
            next_cursor = payload.get("next_cursor")
            if (
                len(page) < TOOL_PAGE_LIMIT
                or len(rows) >= MAX_LISTED_TOOLS
                or not isinstance(next_cursor, str)
                or not next_cursor
            ):
                return tuple(rows[:MAX_LISTED_TOOLS])
            cursor = next_cursor

    async def tool_schema(self, slug: str) -> dict[str, object]:
        return await self._get(f"/tools/{slug}")

    async def connectable_toolkit(self, slug: str) -> str | None:
        """The member-facing name Composio holds for a toolkit this deploy can broker per
        `connectable`, else None — the check `ComposioResolver.claims` runs. A slug carrying
        anything but a toolkit identifier's charset is no toolkit and never reaches the URL — `/`
        or `.` would otherwise let a member-supplied string traverse out of the toolkits path to
        any same-host Composio endpoint under this deploy's key."""
        if not TOOLKIT_SLUG.match(slug):
            return None
        try:
            payload = await self._get(f"{TOOLKITS_PATH}/{slug}")
        except ComposioError as error:
            if error.status == NOT_FOUND_STATUS:
                return None
            raise
        if not connectable(slug, payload):
            return None
        name = payload.get("name")
        return name if isinstance(name, str) and name else slug

    async def list_toolkits(self, query: str, limit: int, after: str | None) -> CatalogPage:
        """Read one page of Composio's connectable toolkit catalog and its continuation cursor."""
        params = {"limit": str(limit)}
        if query:
            params["search"] = query
        if after:
            params["cursor"] = after
        payload = await self._get(TOOLKITS_PATH, params=params)
        items = payload.get("items")
        rows: list[CatalogEntry] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            slug = item.get("slug")
            if not isinstance(slug, str) or not slug or not connectable(slug, item):
                continue
            name = item.get("name")
            rows.append(
                CatalogEntry(
                    provider=slug,
                    label=name if isinstance(name, str) and name else slug,
                )
            )
        next_cursor = payload.get("next_cursor")
        return CatalogPage(
            entries=tuple(rows),
            after=next_cursor if isinstance(next_cursor, str) and next_cursor else None,
        )

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

    async def create_upload(
        self, toolkit: str, slug: str, filename: str, mimetype: str, md5: str
    ) -> "ComposioUpload":
        """Mint where a file for `slug` is staged: Composio's upload-request API answers the store
        key the tool argument names and the presigned PUT URL the sandbox sends the bytes to — this
        client never carries them."""
        payload = await self._post(
            FILES_UPLOAD_PATH,
            {
                "md5": md5,
                "filename": filename,
                "mimetype": mimetype,
                "tool_slug": slug,
                "toolkit_slug": toolkit,
            },
        )
        key = payload.get("key")
        if not isinstance(key, str) or not key:
            raise ComposioError(502, f"upload request carried no key: {payload!r}")
        put_url = payload.get("new_presigned_url")
        if put_url is None:
            return ComposioUpload(key=key, put_url=None)
        if not isinstance(put_url, str) or not put_url:
            raise ComposioError(502, f"upload request carried a malformed url: {payload!r}")
        return ComposioUpload(key=key, put_url=put_url)

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


def _parameter(payload: Mapping[str, object], key: str, depth: int = 0) -> str | None:
    """The value a connected-account payload holds under `key`, as a string — read at the top level
    or in any nested object, since Composio files a connection's own parameters under the auth state
    that stored them. Bounded by `ACCOUNT_PARAMETER_DEPTH` so one deep payload cannot cost an
    unbounded walk."""
    match payload.get(key):
        case str() as value if value:
            return value
        case bool():
            return None
        case int() as value:
            return str(value)
    if depth >= ACCOUNT_PARAMETER_DEPTH:
        return None
    for nested in payload.values():
        if isinstance(nested, Mapping):
            found = _parameter(nested, key, depth + 1)
            if found is not None:
                return found
    return None


def _body(response: httpx.Response) -> dict[str, object]:
    if response.status_code >= 400:
        raise ComposioError(response.status_code, response.text)
    if not response.content:
        return {}
    payload = response.json()
    if not isinstance(payload, dict):
        raise ComposioError(response.status_code, f"composio answered a non-object: {payload!r}")
    return payload


def workspace_file_schema(value: object) -> object:
    """Project a tool input schema for the agent: every `file_uploadable` parameter becomes an
    object taking a `workspace_file` path — the one file vocabulary the dynamic connector tools
    stage. The raw `{name, mimetype, s3key}` store reference is the broker's to build from the
    staged upload, never the model's."""
    match value:
        case dict() if value.get(FILE_UPLOADABLE_KEY):
            replacement: dict[str, object] = {
                "type": "object",
                "properties": {
                    WORKSPACE_FILE_KEY: {
                        "type": "string",
                        "description": "Absolute /workspace path of the file to send.",
                    }
                },
                "required": [WORKSPACE_FILE_KEY],
            }
            description = value.get("description")
            if isinstance(description, str) and description:
                replacement["description"] = description
            return replacement
        case dict():
            return {key: workspace_file_schema(item) for key, item in value.items()}
        case list():
            return [workspace_file_schema(item) for item in value]
        case _:
            return value


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
                    input_schema=_dict(workspace_file_schema(_dict(schema.get("input_schema")))),
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
    user_id = f"{EXTERNAL_USER_PREFIX}{workspace_id}"
    key = (user_id, connector)
    session = _SEARCH_SESSIONS.get(key)
    if session is None:
        async with _SEARCH_SESSIONS_LOCK:
            session = _SEARCH_SESSIONS.get(key)
            if session is None:
                session = await client.tool_router_session(user_id, [connector])
                _SEARCH_SESSIONS[key] = session
    result = await mcp_session.mcp_call_tool(
        session.url,
        COMPOSIO_SEARCH_TOOL,
        {"queries": [{"use_case": query}]},
        {"x-api-key": client.api_key},
        TOOL_ROUTER_TIMEOUT_SECONDS,
    )
    return _search_result(result)
