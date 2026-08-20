"""The Composio broker's feed-sync credential end to end: `credential` confirms the account's owner
from metadata (never a token) and returns a `Credential` whose transport proxies provider HTTP
through Composio's proxy-execute, and `account_parameter` reads one identifier off that same
confirmed metadata — the company id a per-company provider's API address carries. Composio's
connected-account read and proxy-execute are mocked with `httpx.MockTransport` — no live API, no
key, no direct provider call — so the real broker and proxy transport run against canned Composio
responses. This proves the credential the `ConnectorRegistry` routes a brokered provider's feed-sync
to; the source framework that consumes a `Credential` keeps its own proof in
`extensions/sources/tests`."""

import json
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import httpx
import pytest
import ufo_ext_composio.client as composio
import ufo_ext_composio.proxy as composio_proxy
from ufo_ext_composio.broker import ComposioBroker

ACCOUNT = "ca_asana_1"
ASANA_BASE = "https://app.asana.com/api/1.0"


def _composio_handler(
    owner: str, page: dict[str, object]
) -> Callable[[httpx.Request], httpx.Response]:
    """A Composio mock: the connected-account read reports `owner` (metadata, no token), and
    proxy-execute returns `page` wrapped in Composio's `{data: {data, status, headers}}` envelope so
    the transport's unwrap runs."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/connected_accounts/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "id": ACCOUNT,
                    "user_id": owner,
                    "status": "ACTIVE",
                    "toolkit": {"slug": "asana"},
                },
            )
        if request.method == "POST" and request.url.path.endswith(
            composio_proxy.PROXY_EXECUTE_PATH
        ):
            return httpx.Response(200, json={"data": {"data": page, "status": 200, "headers": {}}})
        return httpx.Response(404, json={})

    return handle


def _client(owner: str, page: dict[str, object]) -> composio.ComposioClient:
    return composio.ComposioClient(
        api_key="test", transport=httpx.MockTransport(_composio_handler(owner, page))
    )


async def test_composio_broker_yields_a_transport_that_proxies_provider_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    owner = f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"
    body = {"data": [{"gid": "111", "name": "Acme HQ"}]}
    monkeypatch.setattr(composio, "composio_client", lambda: _client(owner, body))

    credential = await ComposioBroker().credential(workspace_id, "asana", ACCOUNT)
    assert credential.transport is not None
    assert credential.bearer is None

    async with httpx.AsyncClient(base_url=ASANA_BASE, transport=credential.transport) as http:
        response = await http.get("/workspaces")
    assert response.status_code == 200
    assert response.json() == body


SHEETS_ACCOUNT = "ca_googlesheets_1"
SHEETS_BASE = "https://sheets.googleapis.com/v4"
SHEET_RANGES = ["'Summary'", "'Q1 2026'", "'Owner''s View'"]


async def test_composio_broker_transport_carries_a_query_name_repeated_per_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider request repeating one query name reaches proxy-execute with every value: Sheets'
    `values:batchGet` names one `ranges` per tab, and proxy-execute's `parameters` list is keyed by
    name, so a repeat expressed there would ask for a single range and the batch would answer one
    value range for the three the caller asked for."""
    workspace_id = uuid4()
    owner = f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"
    payloads: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/connected_accounts/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "id": SHEETS_ACCOUNT,
                    "user_id": owner,
                    "status": "ACTIVE",
                    "toolkit": {"slug": "googlesheets"},
                },
            )
        if request.method == "POST" and request.url.path.endswith(
            composio_proxy.PROXY_EXECUTE_PATH
        ):
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"data": {"data": {}, "status": 200, "headers": {}}})
        return httpx.Response(404, json={})

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handle))
    monkeypatch.setattr(composio, "composio_client", lambda: client)

    credential = await ComposioBroker().credential(workspace_id, "googlesheets", SHEETS_ACCOUNT)
    async with httpx.AsyncClient(base_url=SHEETS_BASE, transport=credential.transport) as http:
        await http.get(
            "/spreadsheets/sheet-1/values:batchGet",
            params={"ranges": SHEET_RANGES, "majorDimension": "ROWS"},
        )

    endpoint = httpx.URL(str(payloads[0]["endpoint"]))
    assert endpoint.params.get_list("ranges") == SHEET_RANGES
    assert endpoint.params.get("majorDimension") == "ROWS"
    parameters = payloads[0].get("parameters") or []
    assert [item for item in parameters if item["type"] == "query"] == []


R2_URL = "https://temp.store.r2.cloudflarestorage.test/export/abc?X-Amz-Signature=deadbeef"


async def test_composio_broker_transport_answers_binary_data_as_a_named_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The proxying channel's binary case end to end: proxy-execute answers `binary_data` with the
    bytes on Composio's file store, and the transport hands the feed-sync client a 302 whose body
    names the condition. That client follows no redirect and a feed page is JSON, never a file, so
    the sync run fails loud on the non-success response — `is_success` is what the source
    framework's status check keys on — with the reason in the body it reports, instead of ingesting
    the empty `data` beside `binary_data`. The store bytes never enter the serve process."""
    workspace_id = uuid4()
    owner = f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/connected_accounts/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "id": ACCOUNT,
                    "user_id": owner,
                    "status": "ACTIVE",
                    "toolkit": {"slug": "asana"},
                },
            )
        if request.method == "POST" and request.url.path.endswith(
            composio_proxy.PROXY_EXECUTE_PATH
        ):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "data": {},
                        "binary_data": {"url": R2_URL},
                        "status": 200,
                        "headers": {},
                    }
                },
            )
        return httpx.Response(404, json={})

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handle))
    monkeypatch.setattr(composio, "composio_client", lambda: client)

    credential = await ComposioBroker().credential(workspace_id, "asana", ACCOUNT)
    async with httpx.AsyncClient(base_url=ASANA_BASE, transport=credential.transport) as http:
        response = await http.get("/project_exports/123")

    assert response.status_code == 302
    assert response.headers["location"] == R2_URL
    assert not response.is_success
    assert b"binary provider response" in response.content


async def test_composio_broker_refuses_a_foreign_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The confused-deputy guard reads the account's owning user (never a token); an account owned
    by another workspace's broker user fails loud before any transport is built."""
    foreign = f"{composio.EXTERNAL_USER_PREFIX}{uuid4()}"
    monkeypatch.setattr(composio, "composio_client", lambda: _client(foreign, {"data": []}))
    with pytest.raises(composio.ComposioError, match="owned by"):
        await ComposioBroker().credential(uuid4(), "asana", ACCOUNT)


async def test_composio_broker_refuses_an_account_on_another_toolkit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    owner = f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"
    monkeypatch.setattr(composio, "composio_client", lambda: _client(owner, {"data": []}))
    with pytest.raises(composio.ComposioError, match="not 'github'"):
        await ComposioBroker().credential(workspace_id, "github", ACCOUNT)


QUICKBOOKS_ACCOUNT = "ca_quickbooks_1"
REALM = "9130347596"


def _quickbooks_client(owner: str, state: dict[str, object]) -> composio.ComposioClient:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/connected_accounts/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "id": QUICKBOOKS_ACCOUNT,
                    "user_id": owner,
                    "status": "ACTIVE",
                    "toolkit": {"slug": "quickbooks"},
                    "state": state,
                },
            )
        return httpx.Response(404, json={})

    return composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handle))


async def test_composio_broker_reads_the_company_the_connection_carries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """QuickBooks addresses one company per request, and the id rides the connection Composio stored
    at consent — nested under its auth state, beside credentials this read never returns."""
    workspace_id = uuid4()
    owner = f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"
    state = {"authScheme": "OAUTH2", "val": {"status": "ACTIVE", "realmId": REALM}}
    monkeypatch.setattr(composio, "composio_client", lambda: _quickbooks_client(owner, state))

    resolved = await ComposioBroker().account_parameter(
        workspace_id, "quickbooks", QUICKBOOKS_ACCOUNT, "realmId"
    )
    assert resolved == REALM


async def test_composio_broker_answers_no_company_when_the_connection_carries_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    owner = f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"
    state = {"authScheme": "OAUTH2", "val": {"status": "ACTIVE"}}
    monkeypatch.setattr(composio, "composio_client", lambda: _quickbooks_client(owner, state))

    resolved = await ComposioBroker().account_parameter(
        workspace_id, "quickbooks", QUICKBOOKS_ACCOUNT, "realmId"
    )
    assert resolved is None


async def test_composio_broker_refuses_reading_a_foreign_accounts_company(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The confused-deputy guard covers the identifier read too: an account owned by another
    workspace's broker user tells this workspace nothing about itself."""
    foreign = f"{composio.EXTERNAL_USER_PREFIX}{uuid4()}"
    state = {"authScheme": "OAUTH2", "val": {"status": "ACTIVE", "realmId": REALM}}
    monkeypatch.setattr(composio, "composio_client", lambda: _quickbooks_client(foreign, state))
    with pytest.raises(composio.ComposioError, match="owned by"):
        await ComposioBroker().account_parameter(
            uuid4(), "quickbooks", QUICKBOOKS_ACCOUNT, "realmId"
        )


async def test_composio_broker_says_reconnect_for_an_account_it_does_not_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A feed-sync grant this broker no longer holds answers 404 on the account read — the run's
    failure names the repair (reconnect), never a bare not-found."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(composio, "composio_client", lambda: client)
    with pytest.raises(composio.ComposioError, match="reconnect with connect_account"):
        await ComposioBroker().credential(uuid4(), "asana", ACCOUNT)
