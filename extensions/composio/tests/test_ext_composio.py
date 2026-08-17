"""The composio extension: Composio-brokered OAuth providers and the Composio `ConnectorBroker`.

The extension imports only `ufo.sdk`. These tests source its manifest the way `serve` does
(`_connect_flow` and `_connector_registry` over the manifests) and drive the two seams it owns: the
OAuth consent handoff (grant binding + confused-deputy close) and the broker behind the dynamic
connector tools — driven through the `connectors` extension's real tools over the built registry,
so the whole chain (generic tool → registry → Composio broker → execute API) runs as one. Composio's
HTTP is mocked with an `httpx.MockTransport` — no live Composio API or key — so the real client,
provider, route, broker, and tool code run against canned Composio responses. Execution is
server-side on Composio's execute API, so a dynamic tool never touches the sandbox egress proxy
(the sample proves that path)."""

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_composio.client as composio
import ufo_ext_composio.manifest as composio_manifest
import ufo_ext_composio.mcp_session as mcp_session
import ufo_ext_composio.provider as provider
import ufo_ext_connectors.manifest as connectors_manifest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from starlette.requests import Request
from ufo_ext_composio.broker import ComposioBroker
from ufo_ext_connectors.tools import (
    AVAILABLE_TOOLS_FALLBACK_NOTE,
    AVAILABLE_TOOLS_NOTE_KEY,
    AVAILABLE_TOOLS_OMITTED_NOTE,
    CallExternalToolInput,
    DescribeExternalToolsInput,
    ListExternalToolsInput,
    SearchConnectorToolsInput,
    call_external_tool,
    describe_external_tools,
    list_external_tools,
    search_connector_tools,
)

from ufo.agent_scope import agent
from ufo.config import Config
from ufo.connectors import ConnectorRegistry
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.egress_rules import connector_transfer_hosts
from ufo.ext.context import context_for
from ufo.ext.loader import turn_tools
from ufo.ext.manifest import open_connector_namespace
from ufo.grants import (
    ConnectHandoff,
    GrantStore,
    UnknownProvider,
    install_connect_flow,
)
from ufo.loop.engine import MAX_TOOL_RESULT_CHARS
from ufo.schema import tables
from ufo.schema.records import Agent, ConnectRequest, TerminalFrame, Turn
from ufo.sdk.audience import conversation_audience
from ufo.serve import _connect_flow, _connector_registry, _mount_ext_routes
from ufo.surfaces.cli import callback_router
from ufo.tools.builtins import ConnectAccountInput, connect_account_handler
from ufo.tools.context import ToolContext
from ufo.workspace import ws

TOOL_NARRATION = "using the connected account"

PUBLIC_BASE_URL = "https://ufo.example.com"
EXPECTED_REDIRECT_URI = "https://ufo.example.com/v1/connect/callback"
PROVIDER = "github"
PROVIDER_HOST = "api.github.com"
COMPOSIO_CONSENT_URL = "https://github.com/login/oauth/authorize?client_id=x&state=y"
COMPOSIO_ACCOUNT = "ca_test123"
GITHUB_TOKEN = "gho_realsecrettoken"
COMPOSIO_USER = "ufo_ws"
GITHUB_SLUG = "GITHUB_LIST_PULL_REQUESTS"
UNKNOWN_SLUG = "GITHUB_DEFINITELY_NOT_A_TOOL"
TOOL_DESCRIPTION = "List pull requests on a repository."
TOOL_INPUT_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {"owner": {"type": "string"}},
}
SECOND_PAGE_CURSOR = "cursor_page_two"
SECOND_PAGE_SLUG = "GITHUB_FIND_ISSUE"
DISCOVERY_QUERY = "find an issue on a repository"
BANNED_SLUG = "attio"
TOOLKIT_CATALOG = {
    "github": ("GitHub", ["OAUTH2"], 871),
    "notion": ("Notion", ["OAUTH2"], 45),
    "stripe": ("Stripe", ["OAUTH2"], 425),
    "xero": ("Xero", [], 53),
    BANNED_SLUG: ("Attio", ["OAUTH2"], 99),
}
"""The catalog Composio answers with, as `(label, managed auth schemes, tool count)`, holding one
of each shape the namespace refuses. `xero` is the real shape of a toolkit Composio brokers but
holds no managed credentials for — the consent leg has no client to ride. `attio` is the opposite,
and the reason the ban cannot be derived: fully credentialed, rich in tools, yet on `BANNED`."""


def _tool_row(slug: str) -> dict[str, object]:
    """One row of Composio's tool listing, carrying the `input_parameters` schema every row does."""
    return {
        "slug": slug,
        "description": TOOL_DESCRIPTION,
        "input_parameters": TOOL_INPUT_PARAMETERS,
    }


def _paged_tools_handler(
    recorded: list[httpx.QueryParams],
) -> Callable[[httpx.Request], httpx.Response]:
    """A two-page Composio tool listing: the first page fills `TOOL_PAGE_LIMIT` with bare slugs and
    carries the cursor of the second, which holds `SECOND_PAGE_SLUG` in full and no cursor. Every
    request's query params land in `recorded`, so what went on the wire is assertable."""

    def handle(request: httpx.Request) -> httpx.Response:
        recorded.append(request.url.params)
        if request.url.params.get("cursor") == SECOND_PAGE_CURSOR:
            return httpx.Response(200, json={"items": [_tool_row(SECOND_PAGE_SLUG)]})
        head = [{"slug": f"GITHUB_HEAD_TOOL_{n}"} for n in range(composio.TOOL_PAGE_LIMIT)]
        return httpx.Response(200, json={"items": head, "next_cursor": SECOND_PAGE_CURSOR})

    return handle


def _toolkit_record(slug: str) -> dict[str, object]:
    label, schemes, tools = TOOLKIT_CATALOG[slug]
    return {
        "slug": slug,
        "name": label,
        "composio_managed_auth_schemes": schemes,
        "meta": {"tools_count": tools},
    }


@pytest.fixture(autouse=True)
def _reset_connect_flow() -> Iterator[None]:
    yield
    install_connect_flow(None)


def _composio_handler(
    owner: str,
    executed: list[dict[str, object]] | None = None,
    toolkit: str = "github",
    tool_slug: str = GITHUB_SLUG,
    alias: str | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """A Composio mock: connect endpoints (reporting `owner` as the account's owning user, so the
    ownership assertion passes for a match and refuses a foreign one), the tool catalog
    (`GET /tools`, `GET /tools/{slug}`), and server-side execute (`POST /tools/execute/{slug}`,
    recording the request body into `executed`). An unknown slug 404s on schema and execute. The
    connected account authenticates `toolkit` and its one catalog tool is `tool_slug`, so the same
    mock serves an explicit connector (github) or an open-namespace slug (its own toolkit)."""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        if method == "POST" and path.endswith("/connected_accounts/link"):
            return httpx.Response(200, json={"redirect_url": COMPOSIO_CONSENT_URL})
        if method == "GET" and path.endswith("/auth_configs"):
            return httpx.Response(200, json={"items": [{"id": "ac_test"}]})
        if method == "GET" and "/connected_accounts/" in path:
            payload = {
                "status": "ACTIVE",
                "user_id": owner,
                "toolkit": {"slug": toolkit},
                "state": {"val": {"access_token": GITHUB_TOKEN}},
            }
            if alias is not None:
                payload["alias"] = alias
            return httpx.Response(200, json=payload)
        if method == "GET" and path.endswith("/tools"):
            return httpx.Response(200, json={"items": [_tool_row(tool_slug)]})
        if method == "GET" and path.endswith(f"/tools/{tool_slug}"):
            return httpx.Response(
                200, json={"slug": tool_slug, "input_parameters": TOOL_INPUT_PARAMETERS}
            )
        if method == "GET" and "/tools/" in path:
            return httpx.Response(404, json={"error": "unknown tool"})
        if method == "POST" and path.endswith(f"/tools/execute/{tool_slug}"):
            if executed is not None:
                executed.append(json.loads(request.content))
            return httpx.Response(200, json={"successful": True, "data": {"items": []}})
        if method == "POST" and "/tools/execute/" in path:
            return httpx.Response(404, json={"error": "unknown tool"})
        if method == "POST" and path.endswith("/tool_router/session"):
            return httpx.Response(
                200, json={"session_id": "s", "mcp": {"url": "https://router.test/mcp"}}
            )
        if method == "GET" and "/toolkits/" in path:
            slug = path.rsplit("/", 1)[-1]
            if slug not in TOOLKIT_CATALOG:
                return httpx.Response(404, json={"error": "unknown toolkit"})
            return httpx.Response(200, json=_toolkit_record(slug))
        if method == "GET" and path.endswith("/toolkits"):
            search = (request.url.params.get("search") or "").lower()
            items = [
                _toolkit_record(slug)
                for slug, (label, _, _) in TOOLKIT_CATALOG.items()
                if not search or search in slug or search in label.lower()
            ]
            return httpx.Response(200, json={"items": items})
        return httpx.Response(404, json={})

    return handle


def _mock_client(
    owner: str = COMPOSIO_USER,
    executed: list[dict[str, object]] | None = None,
    toolkit: str = "github",
    tool_slug: str = GITHUB_SLUG,
    alias: str | None = None,
) -> composio.ComposioClient:
    return composio.ComposioClient(
        api_key="test",
        transport=httpx.MockTransport(
            _composio_handler(owner, executed, toolkit, tool_slug, alias)
        ),
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


def _registry() -> ConnectorRegistry:
    return _connector_registry(_config(), (composio_manifest.manifest(),), _credentials())


async def test_composio_client_confirms_an_active_accounts_owner() -> None:
    account = await _mock_client().connected_account(COMPOSIO_ACCOUNT, COMPOSIO_USER, "github")
    assert account.account_id == COMPOSIO_ACCOUNT


async def test_composio_account_label_reads_the_alias_field() -> None:
    assert await _mock_client(alias="Work GitHub").account_label(COMPOSIO_ACCOUNT) == "Work GitHub"


async def test_composio_identity_failure_does_not_fail_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _mock_client(owner=f"ufo_{UUID(int=1)}")
    monkeypatch.setattr(composio, "composio_client", lambda: client)

    async def fail(_client: composio.ComposioClient, _account_id: str) -> str | None:
        raise RuntimeError("identity unavailable")

    monkeypatch.setattr(composio.ComposioClient, "account_label", fail)
    account = await provider.ComposioOAuthProvider("github", PROVIDER_HOST).exchange(
        COMPOSIO_ACCOUNT, "https://ufo.example.com/callback", UUID(int=1), "state"
    )
    assert account.account_label is None


async def test_connectable_toolkit_rejects_a_path_traversing_slug_without_calling() -> None:
    """A member-supplied provider slug that isn't a plain toolkit identifier is no toolkit and never
    reaches the URL — else a `/` or `..` would traverse out of `/toolkits` to any same-host Composio
    endpoint under the deploy's key (httpx resolves `..` against the base path)."""

    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"a malformed slug must not reach Composio: {request.url}")

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(explode))
    assert await client.connectable_toolkit("../../../etc/passwd") is None
    assert await client.connectable_toolkit("tool/kit") is None
    assert await client.connectable_toolkit("with space") is None


@pytest.mark.parametrize(
    ("toolkit", "expected"),
    [
        ({"composio_managed_auth_schemes": ["OAUTH2"], "meta": {"tools_count": 61}}, True),
        ({"composio_managed_auth_schemes": [], "meta": {"tools_count": 53}}, False),
        ({"composio_managed_auth_schemes": ["OAUTH2"], "meta": {"tools_count": 0}}, False),
        ({"composio_managed_auth_schemes": [], "meta": {"tools_count": 0}}, False),
        ({"meta": {"tools_count": 61}}, False),
        ({"composio_managed_auth_schemes": ["OAUTH2"]}, False),
        ({}, False),
        ({"composio_managed_auth_schemes": "OAUTH2", "meta": {"tools_count": 61}}, False),
        ({"composio_managed_auth_schemes": ["OAUTH2"], "meta": "61"}, False),
        ({"composio_managed_auth_schemes": ["OAUTH2"], "meta": {"tools_count": "61"}}, False),
        ({"composio_managed_auth_schemes": ["OAUTH2"], "meta": {"tools_count": 6.1}}, False),
    ],
)
def test_connectable_requires_managed_credentials_and_tools(
    toolkit: dict[str, object], expected: bool
) -> None:
    """A toolkit is connectable only when Composio holds managed credentials for it AND it catalogs
    tools: the consent leg rides managed auth (Composio refuses to create a managed auth config
    without credentials) and a toolkit with no tools brokers nothing. A record missing either key —
    or carrying one under a type Composio never sends, which a truthiness check would wave
    through — answers no rather than assuming a default."""
    assert composio.connectable("notion", toolkit) is expected


def test_connectable_refuses_a_banned_toolkit_however_well_credentialed() -> None:
    """A toolkit on `BANNED` connects fine and catalogs plenty of tools, yet is withheld — so it is
    refused on the slug alone, before the credential checks."""
    record: dict[str, object] = {
        "composio_managed_auth_schemes": ["OAUTH2"],
        "meta": {"tools_count": 99},
    }
    assert composio.connectable(BANNED_SLUG, record) is False
    assert composio.connectable("notion", record) is True


def test_connectable_bans_on_the_callers_slug_not_the_payloads() -> None:
    """The ban keys on the slug the caller validated, never one read back out of the record — a
    detail payload that omits or renames `slug` must not silently open a banned toolkit, and one
    that carries a different slug must not close an allowed one."""
    banned: dict[str, object] = {
        "composio_managed_auth_schemes": ["OAUTH2"],
        "meta": {"tools_count": 99},
    }
    assert composio.connectable(BANNED_SLUG, banned) is False
    assert composio.connectable(BANNED_SLUG, {**banned, "slug": "notion"}) is False
    assert composio.connectable("notion", {**banned, "slug": BANNED_SLUG}) is True


@pytest.mark.parametrize("cased", ["attio", "Attio", "ATTIO", "AtTiO"])
def test_connectable_bans_whatever_the_casing(cased: str) -> None:
    """Composio resolves a toolkit path case-insensitively — `/toolkits/AtTiO` answers for `attio` —
    so an exact-case ban would be walked past by capitalising the slug."""
    record: dict[str, object] = {
        "composio_managed_auth_schemes": ["OAUTH2"],
        "meta": {"tools_count": 99},
    }
    assert composio.connectable(cased, record) is False


def test_every_banned_reason_names_the_gap() -> None:
    """Each entry carries the gap that justifies it — the list is the record of why, so an entry
    without a reason is an unexplained ban the next reader cannot re-evaluate — and the keys stay
    alphabetical so a new one lands where a reader looks for it. Comparing key lists, not dicts:
    dict equality ignores order, so `BANNED == dict(sorted(...))` holds for any dict at all."""
    assert all(reason.strip() for reason in composio.BANNED.values())
    assert list(composio.BANNED) == sorted(composio.BANNED)


async def test_connectable_toolkit_refuses_a_toolkit_composio_cannot_broker() -> None:
    """A toolkit Composio lists but holds no managed credentials for is not connectable, so the slug
    is refused before a consent link is minted — the link would die at `POST /auth_configs`. A
    banned slug is refused over the same path despite answering with credentials and 99 tools, so
    the check is proven through the client, not only by calling `connectable` directly."""
    client = _mock_client()
    assert await client.connectable_toolkit("notion") == "Notion"
    assert await client.connectable_toolkit("xero") is None
    assert await client.connectable_toolkit(BANNED_SLUG) is None


async def test_connect_flow_classifies_banned_without_a_broker_key_and_fails_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(composio.COMPOSIO_API_KEY_ENV, raising=False)
    flow = _connect_flow(_credentials(), _config(), (composio_manifest.manifest(),))
    assert flow is not None
    with pytest.raises(UnknownProvider):
        await flow.validate_provider("greenhouse")
    with pytest.raises(UnknownProvider):
        await flow.validate_provider("Greenhouse")
    with pytest.raises(RuntimeError, match="COMPOSIO_API_KEY"):
        await flow.validate_provider("googledrive")


async def test_composio_client_refuses_an_account_owned_by_a_foreign_user() -> None:
    with pytest.raises(composio.ComposioError, match="owned by"):
        await _mock_client("ufo_someone_else").connected_account(
            COMPOSIO_ACCOUNT, COMPOSIO_USER, "github"
        )


async def test_composio_client_refuses_an_inactive_account() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": "INITIATED", "user_id": COMPOSIO_USER, "state": {"val": {}}}
        )

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handler))
    with pytest.raises(composio.ComposioError, match="INITIATED"):
        await client.connected_account(COMPOSIO_ACCOUNT, COMPOSIO_USER, "github")


async def test_composio_client_refuses_an_account_for_another_toolkit() -> None:
    with pytest.raises(composio.ComposioError, match="not 'asana'"):
        await _mock_client().connected_account(COMPOSIO_ACCOUNT, COMPOSIO_USER, "asana")


async def test_composio_client_mints_a_connect_link() -> None:
    redirect = await _mock_client().connect_link(
        toolkit="github", user_id="ufo_ws", callback_url="https://ufo.example.com/back"
    )
    assert redirect == COMPOSIO_CONSENT_URL


async def test_connect_link_rides_the_projects_own_auth_config() -> None:
    """The auth-config lookup takes the project's existing config whether managed or custom, so an
    operator-created Google OAuth config carrying only a connector's minimal scopes is the one the
    consent leg opens."""
    lookups: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/auth_configs"):
            lookups.append(dict(request.url.params))
            return httpx.Response(200, json={"items": [{"id": "ac_custom_google"}]})
        if request.method == "POST" and request.url.path.endswith("/connected_accounts/link"):
            body = json.loads(request.content)
            assert body["auth_config_id"] == "ac_custom_google"
            return httpx.Response(200, json={"redirect_url": COMPOSIO_CONSENT_URL})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handle))
    redirect = await client.connect_link(
        toolkit="googlemeet", user_id="ufo_ws", callback_url="https://ufo.example.com/back"
    )
    assert redirect == COMPOSIO_CONSENT_URL
    assert lookups == [{"toolkit_slug": "googlemeet", "limit": "1"}]


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


async def test_composio_client_sends_the_idempotency_key_as_a_dedup_header() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get(composio.IDEMPOTENCY_HEADER))
        return httpx.Response(200, json={"successful": True})

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handler))
    await client.execute_tool(
        GITHUB_SLUG, {"owner": "acme"}, COMPOSIO_USER, COMPOSIO_ACCOUNT, idempotency_key="t1/x/c1"
    )
    assert seen == ["t1/x/c1"]


async def test_composio_client_refuses_an_oversized_execute_payload() -> None:
    with pytest.raises(ValueError, match="payload bound"):
        await _mock_client().execute_tool(
            GITHUB_SLUG,
            {"blob": "x" * (composio.MAX_EXECUTE_ARGUMENTS_BYTES + 1)},
            COMPOSIO_USER,
            COMPOSIO_ACCOUNT,
        )


UPLOAD_KEY = "455236/googledrive/GOOGLEDRIVE_UPLOAD_FILE/request/abc123"
UPLOAD_PUT_URL = "https://temp.example.r2.test/put?X-Amz-Signature=sig"


async def test_composio_client_mints_an_upload_slot() -> None:
    posted: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(composio.FILES_UPLOAD_PATH)
        posted.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "f1",
                "key": UPLOAD_KEY,
                "type": "new",
                "new_presigned_url": UPLOAD_PUT_URL,
            },
        )

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handler))
    upload = await client.create_upload(
        "googledrive", "GOOGLEDRIVE_UPLOAD_FILE", "form.pdf", "application/pdf", "abc123"
    )
    assert (upload.key, upload.put_url) == (UPLOAD_KEY, UPLOAD_PUT_URL)
    assert posted == [
        {
            "md5": "abc123",
            "filename": "form.pdf",
            "mimetype": "application/pdf",
            "tool_slug": "GOOGLEDRIVE_UPLOAD_FILE",
            "toolkit_slug": "googledrive",
        }
    ]


async def test_composio_client_reuses_a_deduped_upload_slot() -> None:
    """Composio dedups by MD5 and answers `type: "exists"` with a usable key and no
    `new_presigned_url` — the key already holds the bytes, so the slot carries no put_url and the
    sandbox reuses it rather than failing before the tool can run."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "f1", "key": UPLOAD_KEY, "type": "exists"})

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handler))
    upload = await client.create_upload(
        "googledrive", "SLUG", "form.pdf", "application/pdf", "abc123"
    )
    assert (upload.key, upload.put_url) == (UPLOAD_KEY, None)


async def test_composio_client_fails_loud_on_an_upload_slot_without_a_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "f1", "type": "new"})

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handler))
    with pytest.raises(composio.ComposioError, match="no key"):
        await client.create_upload("googledrive", "SLUG", "form.pdf", "application/pdf", "abc123")


async def test_stage_upload_names_the_staged_object_for_the_tool_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "f1",
                "key": UPLOAD_KEY,
                "type": "new",
                "new_presigned_url": UPLOAD_PUT_URL,
            },
        )

    monkeypatch.setattr(
        composio,
        "composio_client",
        lambda: composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handler)),
    )
    staged = await ComposioBroker().stage_upload(
        uuid4(), "googledrive", "GOOGLEDRIVE_UPLOAD_FILE", "form.pdf", "application/pdf", "abc123"
    )
    assert staged.put_url == UPLOAD_PUT_URL
    assert staged.content_type == "application/pdf"
    assert staged.argument == {
        "name": "form.pdf",
        "mimetype": "application/pdf",
        "s3key": UPLOAD_KEY,
    }


def test_file_outputs_finds_only_full_file_objects() -> None:
    """A produced file is Composio's `{name, mimetype, s3url}` object. A nested `s3url` string
    without that shape — a provider payload that merely carries a URL field, possibly pointing at
    a host outside `transfer_hosts` — is returned as data, never fetched as a file."""
    response = {
        "successful": True,
        "data": {
            "file": {"name": "probe.txt", "mimetype": "text/plain", "s3url": "https://t.test/one"},
            "pages": [{"attachment": {"s3url": "https://t.test/two"}}],
            "record": {"s3url": "https://external.test/incidental", "id": 42},
        },
    }
    outputs = ComposioBroker().file_outputs(response)
    assert [(file.name, file.url) for file in outputs] == [("probe.txt", "https://t.test/one")]


async def test_stage_upload_reuses_a_deduped_slot_without_a_put_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dedup hit propagates put_url=None through the broker, so the tool layer skips the PUT."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "f1", "key": UPLOAD_KEY, "type": "exists"})

    monkeypatch.setattr(
        composio,
        "composio_client",
        lambda: composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handler)),
    )
    staged = await ComposioBroker().stage_upload(
        uuid4(), "googledrive", "GOOGLEDRIVE_UPLOAD_FILE", "form.pdf", "application/pdf", "abc123"
    )
    assert staged.put_url is None
    assert staged.argument == {
        "name": "form.pdf",
        "mimetype": "application/pdf",
        "s3key": UPLOAD_KEY,
    }


async def test_schema_rewrites_file_params_to_the_workspace_vocabulary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "slug": GITHUB_SLUG,
        "description": TOOL_DESCRIPTION,
        "input_parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "media": {
                    "type": "object",
                    "description": "The file to attach.",
                    "file_uploadable": True,
                },
            },
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        composio,
        "composio_client",
        lambda: composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handler)),
    )
    described = await ComposioBroker().schema(uuid4(), PROVIDER, GITHUB_SLUG)
    properties = described.input_schema["properties"]
    assert properties["title"] == {"type": "string"}
    assert properties["media"] == {
        "type": "object",
        "properties": {
            "workspace_file": {
                "type": "string",
                "description": "Absolute /workspace path of the file to send.",
            }
        },
        "required": ["workspace_file"],
        "description": "The file to attach.",
    }


def test_manifest_declares_the_broker_file_transfer_hosts() -> None:
    hosts = connector_transfer_hosts((composio_manifest.manifest(),))
    assert hosts.of(PROVIDER) == composio.COMPOSIO_TRANSFER_HOSTS
    assert hosts.of("notion") == composio.COMPOSIO_TRANSFER_HOSTS
    assert hosts.default == composio.COMPOSIO_TRANSFER_HOSTS


def test_authorize_url_points_the_browser_at_the_oauth_bridge() -> None:
    oauth = provider.ComposioOAuthProvider(provider=PROVIDER, host=PROVIDER_HOST)
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


async def test_oauth_route_start_leg_redirects_to_composio_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(composio, "composio_client", _mock_client)
    ctx = context_for(composio_manifest.NAME, frozenset())
    query = f"provider={PROVIDER}&state=SEALED&callback={EXPECTED_REDIRECT_URI}"
    with ws(uuid4()):
        response = await provider.oauth_route(ctx, _request(query))
    assert response.status_code == provider.REDIRECT_STATUS
    assert response.headers["location"] == COMPOSIO_CONSENT_URL


async def test_oauth_route_start_leg_mints_a_link_for_an_open_toolkit_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider no explicit connector registers is a Composio toolkit slug: the start leg mints
    its consent link (toolkit == slug) rather than 404-ing on the closed registry."""
    monkeypatch.setattr(composio, "composio_client", _mock_client)
    ctx = context_for(composio_manifest.NAME, frozenset())
    query = f"provider=notion&state=SEALED&callback={EXPECTED_REDIRECT_URI}"
    with ws(uuid4()):
        response = await provider.oauth_route(ctx, _request(query))
    assert response.status_code == provider.REDIRECT_STATUS
    assert response.headers["location"] == COMPOSIO_CONSENT_URL


async def test_oauth_route_return_leg_hands_the_account_id_to_core_as_code() -> None:
    """The Composio-appended params are pinned as literals — the documented redirect carries
    snake_case `status` and `connected_account_id`; asserting our own constant back at itself
    would let a casing mismatch between the two slip past unnoticed."""
    ctx = context_for(composio_manifest.NAME, frozenset())
    query = (
        f"state=SEALED&callback={EXPECTED_REDIRECT_URI}"
        f"&status=success&connected_account_id={COMPOSIO_ACCOUNT}"
    )
    with ws(uuid4()):
        response = await provider.oauth_route(ctx, _request(query))
    assert response.status_code == provider.REDIRECT_STATUS
    landing = urlparse(response.headers["location"])
    assert f"{landing.scheme}://{landing.netloc}{landing.path}" == EXPECTED_REDIRECT_URI
    landing_query = parse_qs(landing.query)
    assert landing_query["state"] == ["SEALED"]
    assert landing_query["code"] == [COMPOSIO_ACCOUNT]


async def test_oauth_route_failed_consent_answers_loud_instead_of_reminting_consent() -> None:
    ctx = context_for(composio_manifest.NAME, frozenset())
    query = f"provider={PROVIDER}&state=SEALED&callback={EXPECTED_REDIRECT_URI}&status=failed"
    with ws(uuid4()):
        response = await provider.oauth_route(ctx, _request(query))
    assert response.status_code == provider.FAILED_CONSENT_STATUS
    assert "location" not in response.headers


def test_serve_registers_the_cli_exception_explicitly_and_opens_the_rest() -> None:
    """`serve` builds both registries from the manifest: the CLI exception (github) lands as an
    explicit provider with its real host, and the open `ComposioResolver` serves every other toolkit
    by its slug alone — resolved to the shared broker and an OAuth descriptor with no provider host,
    since a brokered grant admits none (execution is server-side)."""
    flow = _connect_flow(_credentials(), _config(), (composio_manifest.manifest(),))
    assert flow is not None
    assert set(flow.providers) == {PROVIDER}
    assert flow.providers[PROVIDER].host == PROVIDER_HOST
    assert flow.redirect_uri == EXPECTED_REDIRECT_URI
    assert flow.resolver is not None
    descriptor = flow.resolver.descriptor("notion")
    assert descriptor.provider == "notion"
    assert descriptor.host == ""
    registry = _registry()
    assert set(registry.entries) == {PROVIDER}
    assert registry.entry(PROVIDER).label == "GitHub"
    opened = registry.entry("notion")
    assert opened.provider == "notion"
    assert isinstance(opened.broker, ComposioBroker)
    assert isinstance(registry.entry(PROVIDER).broker, ComposioBroker)


def test_two_open_connector_namespaces_fail_loud() -> None:
    """At most one open namespace can own the unregistered-slug space; two would leave the connect
    flow and registry unable to decide which brokers a slug, so serve refuses at boot rather than
    resolve it arbitrarily."""
    manifest = composio_manifest.manifest()
    assert open_connector_namespace((manifest,)) is manifest.connector_resolver
    with pytest.raises(RuntimeError, match="two extensions register an open connector namespace"):
        open_connector_namespace((manifest, manifest))


def test_knows_provider_is_a_cheap_check_that_never_hits_the_catalog() -> None:
    """The memoizing `authorize` runs this under a turn-row lock, so it must not reach Composio: an
    installed open namespace answers yes for any slug without a client (no `composio_client` is
    patched, so an actual catalog call would raise)."""
    flow = _connect_flow(_credentials(), _config(), (composio_manifest.manifest(),))
    assert flow is not None
    assert flow.knows_provider(PROVIDER)
    assert flow.knows_provider("any-open-slug")


async def test_connect_flow_validates_a_toolkit_slug_against_the_live_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A member connecting an unregistered slug is validated against Composio's catalog, so a real
    toolkit passes while a typo — or a toolkit Composio cannot broker for this deploy — fails loud
    at connect time rather than minting a dead consent link."""
    monkeypatch.setattr(composio, "composio_client", _mock_client)
    flow = _connect_flow(_credentials(), _config(), (composio_manifest.manifest(),))
    assert flow is not None
    await flow.validate_provider("notion")
    with pytest.raises(UnknownProvider):
        await flow.validate_provider("definitelynotatoolkit")
    with pytest.raises(UnknownProvider):
        await flow.validate_provider("xero")


async def test_list_external_tools_surfaces_the_open_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(composio, "composio_client", _mock_client)
    result = await list_external_tools(
        _ctx(uuid4(), uuid4(), uuid4(), None),
        ListExternalToolsInput(queries=("notion",), user_description="find a doc store"),
    )
    payload = json.loads(result.content[0].text)
    rows = {row["source_id"] for row in payload["connectors"]}
    assert "notion" in rows
    assert "stripe" not in rows


async def test_list_toolkits_filters_each_item_of_a_mixed_page() -> None:
    """One catalog page carries connectable and refused toolkits together, so the filter runs per
    item — an empty query returns the whole fixture and only the two refused slugs are dropped."""
    rows = await _mock_client().list_toolkits("", 50)
    slugs = {slug for slug, _ in rows}
    assert slugs == {"github", "notion", "stripe"}
    assert rows, "a mixed page must still yield its connectable toolkits"


async def test_list_external_tools_never_offers_an_unbrokerable_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery surfaces only services the member can go on to connect — a toolkit Composio holds
    no managed credentials for, and one that is banned, are both filtered out of the catalog, so the
    agent never names either."""
    monkeypatch.setattr(composio, "composio_client", _mock_client)
    result = await list_external_tools(
        _ctx(uuid4(), uuid4(), uuid4(), None),
        ListExternalToolsInput(
            queries=("xero", BANNED_SLUG, "notion"), user_description="find accounting and a crm"
        ),
    )
    rows = {row["source_id"] for row in json.loads(result.content[0].text)["connectors"]}
    assert "xero" not in rows
    assert BANNED_SLUG not in rows
    assert "notion" in rows, "filtering the refused must not drop their connectable siblings"


async def test_list_external_tools_fans_multiple_queries_across_the_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Several distinct queries each search the catalog (concurrently) and merge deduped, so the
    fan-out surfaces every match — the path a single query never exercises."""
    monkeypatch.setattr(composio, "composio_client", _mock_client)
    result = await list_external_tools(
        _ctx(uuid4(), uuid4(), uuid4(), None),
        ListExternalToolsInput(
            queries=("notion", "stripe", "notion"), user_description="find a few tools"
        ),
    )
    rows = [row["source_id"] for row in json.loads(result.content[0].text)["connectors"]]
    assert set(rows) >= {"notion", "stripe"}
    assert rows.count("notion") == 1


def test_list_external_tools_input_bounds_the_query_count() -> None:
    with pytest.raises(ValidationError):
        ListExternalToolsInput(queries=tuple(f"q{n}" for n in range(9)), user_description="x")


async def test_describe_external_tools_fetches_schemas_and_available_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(composio, "composio_client", _mock_client)
    result = await describe_external_tools(
        _ctx(uuid4(), uuid4(), uuid4(), None),
        DescribeExternalToolsInput(
            user_description=TOOL_NARRATION, source_id=PROVIDER, tool_names=(GITHUB_SLUG,)
        ),
    )
    payload = json.loads(result.content[0].text)
    assert payload["source_id"] == PROVIDER
    assert payload["schemas"][GITHUB_SLUG]["input_schema"]["properties"] == {
        "owner": {"type": "string"}
    }


async def test_describe_external_tools_marks_an_unknown_name_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(composio, "composio_client", _mock_client)
    result = await describe_external_tools(
        _ctx(uuid4(), uuid4(), uuid4(), None),
        DescribeExternalToolsInput(
            user_description=TOOL_NARRATION, source_id=PROVIDER, tool_names=(UNKNOWN_SLUG,)
        ),
    )
    payload = json.loads(result.content[0].text)
    assert payload["unresolved"] == [UNKNOWN_SLUG]
    assert [tool["slug"] for tool in payload["availableTools"]] == [GITHUB_SLUG]
    assert payload["availableTools"][0]["input_schema"] == TOOL_INPUT_PARAMETERS


async def test_describe_external_tools_falls_back_to_top_tools_and_marks_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Composio's `query` is a full-text filter over name/slug/description, so a use-case sentence
    routinely matches nothing. Discovery answers the connector's unqueried top tools rather than an
    empty list, and marks them as not being query matches so the head is never read as a ranking."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("query"):
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json={"items": [_tool_row(GITHUB_SLUG)]})

    monkeypatch.setattr(
        composio,
        "composio_client",
        lambda: composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handle)),
    )
    result = await describe_external_tools(
        _ctx(uuid4(), uuid4(), uuid4(), None),
        DescribeExternalToolsInput(
            user_description=TOOL_NARRATION, source_id=PROVIDER, query=DISCOVERY_QUERY
        ),
    )
    payload = json.loads(result.content[0].text)
    assert [tool["slug"] for tool in payload["availableTools"]] == [GITHUB_SLUG]
    assert payload[AVAILABLE_TOOLS_NOTE_KEY] == AVAILABLE_TOOLS_FALLBACK_NOTE


async def test_describe_external_tools_reaches_the_listings_second_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A toolkit's listing is paged, so the slug the query really wants can sit past the first
    page — the walk follows `next_cursor` and every row reaches `availableTools`, schema
    included."""
    monkeypatch.setattr(
        composio,
        "composio_client",
        lambda: composio.ComposioClient(
            api_key="test", transport=httpx.MockTransport(_paged_tools_handler([]))
        ),
    )
    result = await describe_external_tools(
        _ctx(uuid4(), uuid4(), uuid4(), None),
        DescribeExternalToolsInput(
            user_description=TOOL_NARRATION, source_id=PROVIDER, query=DISCOVERY_QUERY
        ),
    )
    payload = json.loads(result.content[0].text)
    listed = payload["availableTools"]
    assert len(listed) == composio.TOOL_PAGE_LIMIT + 1
    assert listed[-1] == {
        "slug": SECOND_PAGE_SLUG,
        "description": TOOL_DESCRIPTION,
        "input_schema": TOOL_INPUT_PARAMETERS,
    }
    assert AVAILABLE_TOOLS_NOTE_KEY not in payload


async def test_list_tools_stops_at_the_row_cap_while_a_cursor_is_still_offered() -> None:
    """The walk is bounded whatever the listing says: a toolkit that keeps offering a cursor is read
    to `MAX_LISTED_TOOLS` rows and no further, so a runaway catalog cannot drive an unbounded number
    of requests."""
    pages: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        pages.append(1)
        items = [{"slug": f"GITHUB_TOOL_{len(pages)}_{n}"} for n in range(composio.TOOL_PAGE_LIMIT)]
        return httpx.Response(200, json={"items": items, "next_cursor": SECOND_PAGE_CURSOR})

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handle))
    rows = await client.list_tools(PROVIDER)
    assert len(rows) == composio.MAX_LISTED_TOOLS
    assert len(pages) == composio.MAX_LISTED_TOOLS // composio.TOOL_PAGE_LIMIT


async def test_describe_external_tools_bounds_the_listing_it_answers_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A big toolkit catalogs hundreds of tools and every listed row carries its input schema, so
    the answer is bounded to what the model keeps in context — past the budget the rows stop and the
    note says how many were left out, rather than the whole result being offloaded to a file."""
    fat_schema = {
        "type": "object",
        "properties": {
            f"field_{n}": {"type": "string", "description": "x" * 40} for n in range(20)
        },
    }

    def handle(request: httpx.Request) -> httpx.Response:
        items = [
            {
                "slug": f"GITHUB_TOOL_{n}",
                "description": TOOL_DESCRIPTION,
                "input_parameters": fat_schema,
            }
            for n in range(composio.TOOL_PAGE_LIMIT)
        ]
        return httpx.Response(200, json={"items": items})

    monkeypatch.setattr(
        composio,
        "composio_client",
        lambda: composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handle)),
    )
    result = await describe_external_tools(
        _ctx(uuid4(), uuid4(), uuid4(), None),
        DescribeExternalToolsInput(
            user_description=TOOL_NARRATION, source_id=PROVIDER, query=DISCOVERY_QUERY
        ),
    )
    text = result.content[0].text
    payload = json.loads(text)
    listed = payload["availableTools"]
    assert 0 < len(listed) < composio.TOOL_PAGE_LIMIT
    assert len(text) <= MAX_TOOL_RESULT_CHARS, f"{len(text)} chars is past the inline budget"
    assert payload[AVAILABLE_TOOLS_NOTE_KEY] == AVAILABLE_TOOLS_OMITTED_NOTE.format(
        omitted=composio.TOOL_PAGE_LIMIT - len(listed), total=composio.TOOL_PAGE_LIMIT
    )


async def test_listing_requests_carry_the_page_size_the_query_and_the_cursor() -> None:
    """What actually goes on the wire: a full page per request, the caller's query on every request,
    and the previous page's cursor on the second — the three params the discovery loss hung on."""
    recorded: list[httpx.QueryParams] = []
    client = composio.ComposioClient(
        api_key="test", transport=httpx.MockTransport(_paged_tools_handler(recorded))
    )
    rows = await client.list_tools(PROVIDER, DISCOVERY_QUERY)
    assert len(rows) == composio.TOOL_PAGE_LIMIT + 1
    assert [dict(params) for params in recorded] == [
        {
            "toolkit_slug": PROVIDER,
            "limit": str(composio.TOOL_PAGE_LIMIT),
            "query": DISCOVERY_QUERY,
        },
        {
            "toolkit_slug": PROVIDER,
            "limit": str(composio.TOOL_PAGE_LIMIT),
            "query": DISCOVERY_QUERY,
            "cursor": SECOND_PAGE_CURSOR,
        },
    ]


async def test_connect_binds_a_grant_and_call_external_tool_executes_via_composio(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end across both extensions: connect the account in chat (its owner read through
    mocked Composio), binding a grant that carries the Composio connected-account id, then the
    `connectors` extension's `call_external_tool` resolves that grant, routes through the registry
    to the Composio broker, and POSTs to Composio's server-side execute API with the workspace's
    broker user id and the bound account — no sandbox, no proxy."""
    workspace_id = await _workspace()
    owner = f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"
    executed: list[dict[str, object]] = []
    client = _mock_client(owner, executed)
    monkeypatch.setattr(composio, "composio_client", lambda: client)
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    turn_id = await _turn(workspace_id, agent_id, conversation_id)
    credentials = _credentials()
    flow = _connect_flow(
        credentials, _config(), (connectors_manifest.manifest(), composio_manifest.manifest())
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
    recorded = await flow.complete(state=state, code=COMPOSIO_ACCOUNT)
    assert (recorded.provider, recorded.account_id) == (PROVIDER, COMPOSIO_ACCOUNT)
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.connection.c.host, tables.connection.c.account_id).where(
                    tables.connection.c.workspace_id == workspace_id
                )
            )
        ).one()
    assert (row.host, row.account_id) == (PROVIDER_HOST, COMPOSIO_ACCOUNT)

    tools, ext_by_tool = turn_tools(
        (connectors_manifest.manifest(), composio_manifest.manifest()),
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
                    "tool_name": GITHUB_SLUG,
                    "source_id": PROVIDER,
                    "arguments": {"owner": "acme"},
                }
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


async def test_open_namespace_slug_connects_describes_searches_and_executes(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The headline capability, whole chain, for a slug no connector registered. Front leg: the
    member asks to connect it, `connect_account_handler` validates it against the live catalog, and
    the turn-locked `ConnectHandoff.authorize` (via `knows_provider` + the resolver's descriptor)
    mints the consent URL; `complete` then binds a host="" grant. Back leg: `describe`, `search`,
    and `call` each dispatch through the registry's resolver fallback (`entry`) to the Composio
    broker's schema, Tool Router, and server-side execute — so an open-namespace provider is
    connectable, describable, searchable, and callable, not just registered."""
    notion_tool = "NOTION_INSERT_ROW"
    workspace_id = await _workspace()
    owner = f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"
    executed: list[dict[str, object]] = []
    client = _mock_client(owner, executed, toolkit="notion", tool_slug=notion_tool)
    monkeypatch.setattr(composio, "composio_client", lambda: client)
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    turn_id = await _turn(workspace_id, agent_id, conversation_id)
    credentials = _credentials()
    flow = _connect_flow(
        credentials, _config(), (connectors_manifest.manifest(), composio_manifest.manifest())
    )
    assert flow is not None
    install_connect_flow(flow)

    begin = await connect_account_handler(
        _turn_context(workspace_id, agent_id, conversation_id, member_id, turn_id),
        ConnectAccountInput(user_description=TOOL_NARRATION, provider="notion"),
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
    recorded = await flow.complete(state=state, code=COMPOSIO_ACCOUNT)
    assert (recorded.provider, recorded.account_id) == ("notion", COMPOSIO_ACCOUNT)
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.connection.c.host, tables.connection.c.provider).where(
                    tables.connection.c.workspace_id == workspace_id
                )
            )
        ).one()
    assert (row.host, row.provider) == ("", "notion")

    ctx = _ctx(
        workspace_id, agent_id, conversation_id, turn_id, flow.store, speaker_member_id=member_id
    )
    described = await describe_external_tools(
        ctx,
        DescribeExternalToolsInput(
            user_description=TOOL_NARRATION, source_id="notion", tool_names=(notion_tool,)
        ),
    )
    assert notion_tool in json.loads(described.content[0].text)["schemas"]

    monkeypatch.setattr(composio, "_SEARCH_SESSIONS", {})

    async def fake_router_call(*args: object, **kwargs: object) -> dict[str, object]:
        return {"data": {"results": [{"primary_tool_slugs": [notion_tool]}], "tool_schemas": {}}}

    monkeypatch.setattr(mcp_session, "mcp_call_tool", fake_router_call)
    found = await search_connector_tools(
        ctx,
        SearchConnectorToolsInput(
            user_description=TOOL_NARRATION, source_id="notion", query="insert a row"
        ),
    )
    assert notion_tool in [t["slug"] for t in json.loads(found.content[0].text)["tools"]]

    with ws(workspace_id), agent(agent_id):
        result = await call_external_tool(
            ctx,
            CallExternalToolInput(
                user_description=TOOL_NARRATION,
                tool_name=notion_tool,
                source_id="notion",
                arguments={},
            ),
        )
    assert result.is_error is False
    assert json.loads(result.content[0].text)["successful"] is True
    assert executed and executed[0]["connected_account_id"] == COMPOSIO_ACCOUNT


async def test_shared_oauth_bridge_verifies_workspace_and_lands_the_grant(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _workspace()
    owner = f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    turn_id = await _turn(workspace_id, agent_id, conversation_id)
    link_requests: list[dict[str, object]] = []
    base_handler = _composio_handler(owner)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/connected_accounts/link"):
            link_requests.append(json.loads(request.content))
        return base_handler(request)

    monkeypatch.setattr(
        composio,
        "composio_client",
        lambda: composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handler)),
    )
    credentials = _credentials()
    flow = _connect_flow(credentials, _config(), (composio_manifest.manifest(),))
    assert flow is not None
    install_connect_flow(flow)
    begun = await connect_account_handler(
        _turn_context(workspace_id, agent_id, conversation_id, member_id, turn_id),
        ConnectAccountInput(user_description=TOOL_NARRATION, provider=PROVIDER),
    )
    request = ConnectRequest.model_validate_json(begun.content[0].text.splitlines()[1])
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
    bridge = urlparse(await ConnectHandoff(flow).authorize(workspace_id, turn_id, member_id))
    params = {key: values[0] for key, values in parse_qs(bridge.query).items()}
    app = FastAPI()
    app.include_router(callback_router)
    _mount_ext_routes(app, (composio_manifest.manifest(),), credentials, None, None)

    async with AsyncClient(transport=ASGITransport(app=app), base_url=PUBLIC_BASE_URL) as client:
        rejected = await client.get(
            bridge.path, params={**params, "callback": "https://attacker.test/steal"}
        )
        wrong_provider = await client.get(
            bridge.path, params={**params, "provider": "not-the-sealed-provider"}
        )
        started = await client.get(bridge.path, params=params)
        returned = await client.get(
            bridge.path,
            params={
                **params,
                "status": "success",
                "connected_account_id": COMPOSIO_ACCOUNT,
            },
        )
        completed = await client.get(returned.headers["location"])

    assert rejected.status_code == 401
    assert wrong_provider.status_code == 401
    assert started.status_code == provider.REDIRECT_STATUS
    assert started.headers["location"] == COMPOSIO_CONSENT_URL
    assert link_requests[0]["user_id"] == owner
    assert completed.status_code == 200
    async with workspace_tx() as connection:
        grant = (
            await connection.execute(
                sa.select(
                    tables.connection.c.account_id,
                    tables.connector_grant.c.agent_id,
                )
                .select_from(
                    tables.connector_grant.join(
                        tables.connection,
                        tables.connector_grant.c.connection_id == tables.connection.c.id,
                    )
                )
                .where(tables.connector_grant.c.workspace_id == workspace_id)
            )
        ).one()
    assert (grant.account_id, grant.agent_id) == (COMPOSIO_ACCOUNT, agent_id)


async def test_call_external_tool_without_a_grant_fails_loud(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, agent_id, conversation_id, turn_id = uuid4(), uuid4(), uuid4(), uuid4()
    executed: list[dict[str, object]] = []
    monkeypatch.setattr(composio, "composio_client", lambda: _mock_client(COMPOSIO_USER, executed))
    ctx = _ctx(workspace_id, agent_id, conversation_id, turn_id, GrantStore())
    with (
        ws(workspace_id),
        agent(agent_id),
        pytest.raises(ValueError, match="no 'github' account is available"),
    ):
        await call_external_tool(
            ctx,
            CallExternalToolInput(
                user_description=TOOL_NARRATION,
                tool_name=GITHUB_SLUG,
                source_id=PROVIDER,
                arguments={},
            ),
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
    with ws(workspace_id), agent(agent_id):
        await store.record(
            provider=PROVIDER,
            account_id=COMPOSIO_ACCOUNT,
            host=PROVIDER_HOST,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=False,
        )
    monkeypatch.setattr(composio, "composio_client", _mock_client)
    ctx = _ctx(workspace_id, agent_id, conversation_id, turn_id, store, speaker_member_id=member_id)
    with (
        ws(workspace_id),
        agent(agent_id),
        pytest.raises(composio.ComposioError, match=f"tools available on github: {GITHUB_SLUG}"),
    ):
        await call_external_tool(
            ctx,
            CallExternalToolInput(
                user_description=TOOL_NARRATION,
                tool_name=UNKNOWN_SLUG,
                source_id=PROVIDER,
                arguments={},
            ),
        )


async def test_call_external_tool_with_a_stale_account_says_reconnect(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An execute failure naming the granted account (a grant this broker no longer holds — an org
    or key rotation) routes the agent into the connect flow, never into the slug-miss augmentation
    that would answer a dead account with a list of tools."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    turn_id = await _turn(workspace_id, agent_id, conversation_id)
    store = GrantStore()
    with ws(workspace_id), agent(agent_id):
        await store.record(
            provider=PROVIDER,
            account_id=COMPOSIO_ACCOUNT,
            host=PROVIDER_HOST,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=False,
        )
    base = _composio_handler(COMPOSIO_USER)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/tools/execute/" in request.url.path:
            return httpx.Response(
                404, json={"error": f"connected account {COMPOSIO_ACCOUNT} not found"}
            )
        return base(request)

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(composio, "composio_client", lambda: client)
    ctx = _ctx(workspace_id, agent_id, conversation_id, turn_id, store, speaker_member_id=member_id)
    with (
        ws(workspace_id),
        agent(agent_id),
        pytest.raises(composio.ComposioError, match="reconnect with connect_account"),
    ):
        await call_external_tool(
            ctx,
            CallExternalToolInput(
                user_description=TOOL_NARRATION,
                tool_name=GITHUB_SLUG,
                source_id=PROVIDER,
                arguments={},
            ),
        )


async def test_complete_rejects_an_account_owned_by_a_foreign_composio_user(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confused-deputy close: driving `complete` with a connected-account id whose owning Composio
    user is not this workspace's brokered user is refused before any token is read, so no grant
    binds to an attacker-controlled account."""
    workspace_id = uuid4()
    foreign_owner = f"{composio.EXTERNAL_USER_PREFIX}{uuid4()}"
    monkeypatch.setattr(composio, "composio_client", lambda: _mock_client(foreign_owner))
    flow = _connect_flow(_credentials(), _config(), (composio_manifest.manifest(),))
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
    with pytest.raises(composio.ComposioError, match="owned by"):
        await flow.complete(state=state, code=COMPOSIO_ACCOUNT)
    async with workspace_tx() as connection:
        count = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.connector_grant)
                .where(tables.connector_grant.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert count == 0


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
            inbound="connect my github",
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
