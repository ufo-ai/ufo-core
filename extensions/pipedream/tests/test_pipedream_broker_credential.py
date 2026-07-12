"""The Pipedream broker's feed-sync credential end to end: `credential` confirms the account's
owner from metadata (never a token) and its app, and returns a `Credential` whose transport proxies
provider HTTP through the Connect Proxy verbatim. Pipedream's account read and proxy are mocked
with `httpx.MockTransport` — no live API, no credentials, no direct provider call — so the real
broker and proxy transport run against canned Connect responses. The passthrough cases mirror what
the gmail source leans on: a 200 page body, and an upstream 404 arriving as a 404 (the
`CursorExpired` signal), never an envelope. This proves the credential the `ConnectorRegistry`
routes gmail's feed-sync to; the gmail connector itself keeps its proof in
`extensions/sources/tests`."""

import base64
import json
from collections.abc import Callable, Iterator
from urllib.parse import parse_qs
from uuid import UUID, uuid4

import httpx
import pytest
import ufo_ext_pipedream.client as pipedream
from ufo_ext_pipedream.broker import PipedreamBroker

ACCOUNT = "apn_gmail_1"
GMAIL_BASE = "https://gmail.googleapis.com"
HISTORY_PATH = "/gmail/v1/users/me/history"


@pytest.fixture(autouse=True)
def _pipedream_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(pipedream.PIPEDREAM_CLIENT_ID_ENV, f"cid_{uuid4().hex}")
    monkeypatch.setenv(pipedream.PIPEDREAM_CLIENT_SECRET_ENV, "csecret")
    monkeypatch.setenv(pipedream.PIPEDREAM_PROJECT_ID_ENV, "proj_test")
    yield


def _handler(
    owner: str, upstream: Callable[[str, httpx.Request], httpx.Response]
) -> Callable[[httpx.Request], httpx.Response]:
    """A Pipedream mock: the OAuth token grant, the account read (reporting `owner`), and the
    Connect Proxy — which decodes the base64url upstream URL from its path and answers with
    `upstream`'s verbatim response for it."""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/oauth/token":
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        if request.method == "GET" and f"/accounts/{ACCOUNT}" in path:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": ACCOUNT,
                        "external_id": owner,
                        "healthy": True,
                        "app": {"name_slug": "gmail"},
                    }
                },
            )
        if "/proxy/" in path:
            assert request.url.params["account_id"] == ACCOUNT
            assert request.url.params["external_user_id"] == owner
            encoded = path.rsplit("/", 1)[-1]
            padded = encoded + "=" * (-len(encoded) % 4)
            target = base64.urlsafe_b64decode(padded.encode()).decode()
            return upstream(target, request)
        return httpx.Response(404, json={})

    return handle


def _install(
    monkeypatch: pytest.MonkeyPatch,
    owner: str,
    upstream: Callable[[str, httpx.Request], httpx.Response],
) -> None:
    client = pipedream.PipedreamClient(
        client_id=f"cid_{uuid4().hex}",
        client_secret="s",
        project_id="proj_test",
        transport=httpx.MockTransport(_handler(owner, upstream)),
    )
    monkeypatch.setattr(pipedream, "pipedream_client", lambda: client)


def _owner(workspace_id: UUID) -> str:
    return pipedream.connection_user_id(workspace_id, "feed-sync")


def test_workspace_external_user_ownership_is_exact() -> None:
    workspace_id = UUID("f795c197-6a20-4bb7-82f0-4d220fe1a62d")
    foreign_workspace_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert pipedream._workspace_owns_external_user(
        workspace_id, f"{pipedream.EXTERNAL_USER_PREFIX}{workspace_id}"
    )
    assert pipedream._workspace_owns_external_user(
        workspace_id, pipedream.connection_user_id(workspace_id, "state")
    )
    refused = (
        f"{pipedream.EXTERNAL_USER_PREFIX}{workspace_id}x",
        f"{pipedream.EXTERNAL_USER_PREFIX}{foreign_workspace_id}",
        pipedream.connection_user_id(foreign_workspace_id, "state"),
        f"{pipedream.workspace_user_prefix(workspace_id)}{'f' * 31}",
        f"{pipedream.workspace_user_prefix(workspace_id)}{'F' * 32}",
        f"{pipedream.workspace_user_prefix(workspace_id)}{'g' * 32}",
    )
    assert not any(
        pipedream._workspace_owns_external_user(workspace_id, owner) for owner in refused
    )


async def test_pipedream_broker_yields_a_transport_that_proxies_provider_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider HTTP rides the Connect Proxy untouched: the query survives inside the encoded
    upstream URL, the caller's interesting headers arrive `x-pd-proxy-`-prefixed, and the upstream
    body and status come back verbatim."""
    workspace_id = uuid4()
    owner = _owner(workspace_id)
    page = {"history": [], "historyId": "777"}

    def upstream(target: str, request: httpx.Request) -> httpx.Response:
        url = httpx.URL(target)
        assert f"{url.scheme}://{url.host}{url.path}" == f"{GMAIL_BASE}{HISTORY_PATH}"
        assert parse_qs(url.query.decode())["startHistoryId"] == ["42"]
        assert request.headers["x-pd-proxy-accept"] == "application/json"
        return httpx.Response(200, json=page)

    _install(monkeypatch, owner, upstream)
    credential = await PipedreamBroker().credential(workspace_id, "gmail", ACCOUNT)
    assert credential.transport is not None
    assert credential.bearer is None

    async with httpx.AsyncClient(base_url=GMAIL_BASE, transport=credential.transport) as http:
        response = await http.get(
            HISTORY_PATH, params={"startHistoryId": "42"}, headers={"accept": "application/json"}
        )
    assert response.status_code == 200
    assert response.json() == page


async def test_pipedream_broker_proxies_a_workspace_owned_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    owner = f"{pipedream.EXTERNAL_USER_PREFIX}{workspace_id}"

    def upstream(target: str, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"history": [], "historyId": "777"})

    _install(monkeypatch, owner, upstream)
    credential = await PipedreamBroker().credential(workspace_id, "gmail", ACCOUNT)
    assert credential.transport is not None
    async with httpx.AsyncClient(base_url=GMAIL_BASE, transport=credential.transport) as http:
        response = await http.get(HISTORY_PATH)
    assert response.status_code == 200


async def test_proxy_passes_an_upstream_404_through_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gmail's expired-cursor signal is a 404 on the history walk; the proxy hop must not swallow
    or re-shape it, or the source would never raise `CursorExpired` and refetch."""
    workspace_id = uuid4()
    owner = _owner(workspace_id)

    def upstream(target: str, request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"code": 404, "message": "historyId expired"}})

    _install(monkeypatch, owner, upstream)
    credential = await PipedreamBroker().credential(workspace_id, "gmail", ACCOUNT)
    async with httpx.AsyncClient(base_url=GMAIL_BASE, transport=credential.transport) as http:
        response = await http.get(HISTORY_PATH, params={"startHistoryId": "1"})
    assert response.status_code == 404
    assert json.loads(response.content)["error"]["message"] == "historyId expired"


async def test_pipedream_broker_refuses_a_foreign_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The confused-deputy guard reads the account's owning external user (never a token); an
    account owned by another workspace's user fails loud before any transport is built."""
    workspace_id = uuid4()
    foreign = _owner(uuid4())
    _install(monkeypatch, foreign, lambda target, request: httpx.Response(200))
    with pytest.raises(pipedream.PipedreamError, match="not owned by workspace"):
        await PipedreamBroker().credential(workspace_id, "gmail", ACCOUNT)


async def test_pipedream_broker_says_reconnect_for_an_account_it_does_not_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A feed-sync grant recorded through a previous broker answers 404 on the account read — the
    run's failure names the repair (reconnect), never a bare not-found."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth/token":
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        return httpx.Response(404, json={"error": "Account not found"})

    client = pipedream.PipedreamClient(
        client_id=f"cid_{uuid4().hex}",
        client_secret="s",
        project_id="proj_test",
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(pipedream, "pipedream_client", lambda: client)
    with pytest.raises(pipedream.PipedreamError, match="reconnect with connect_account"):
        await PipedreamBroker().credential(uuid4(), "gmail", "ca_composio_era")


async def test_pipedream_broker_refuses_an_account_on_the_wrong_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    owner = _owner(workspace_id)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth/token":
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": ACCOUNT,
                    "external_id": owner,
                    "healthy": True,
                    "app": {"name_slug": "slack"},
                }
            },
        )

    client = pipedream.PipedreamClient(
        client_id=f"cid_{uuid4().hex}",
        client_secret="s",
        project_id="proj_test",
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(pipedream, "pipedream_client", lambda: client)
    with pytest.raises(pipedream.PipedreamError, match="authenticates 'slack'"):
        await PipedreamBroker().credential(workspace_id, "gmail", ACCOUNT)
