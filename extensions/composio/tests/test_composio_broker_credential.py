"""The Composio broker's feed-sync credential end to end: `credential` confirms the account's owner
from metadata (never a token) and returns a `Credential` whose transport proxies provider HTTP
through Composio's proxy-execute. Composio's connected-account read and proxy-execute are mocked
with `httpx.MockTransport` — no live API, no key, no direct provider call — so the real broker and
proxy transport run against canned Composio responses. This proves the credential the
`ConnectorRegistry` routes a brokered provider's feed-sync to; the source framework that consumes a
`Credential` keeps its own proof in `extensions/sources/tests`."""

from collections.abc import Callable
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
            return httpx.Response(200, json={"id": ACCOUNT, "user_id": owner, "status": "ACTIVE"})
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


async def test_composio_broker_refuses_a_foreign_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The confused-deputy guard reads the account's owning user (never a token); an account owned
    by another workspace's broker user fails loud before any transport is built."""
    foreign = f"{composio.EXTERNAL_USER_PREFIX}{uuid4()}"
    monkeypatch.setattr(composio, "composio_client", lambda: _client(foreign, {"data": []}))
    with pytest.raises(composio.ComposioError, match="owned by"):
        await ComposioBroker().credential(uuid4(), "asana", ACCOUNT)
