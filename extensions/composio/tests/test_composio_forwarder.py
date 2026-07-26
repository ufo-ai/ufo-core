"""The Composio request forwarder the egress proxy calls for a sentinel-carrying CLI request: one
provider request executed through proxy-execute under the granted account, its response
reconstructed for the wire. Composio is mocked with `httpx.MockTransport` — the real forwarder and
proxy transport run against canned envelopes, and the assertions read the proxy-execute payload the
forwarder actually sent. The manifest test pins the declaration end: the github connector exports
`GH_TOKEN` and forwards `authorization`, so `gh` inside the sandbox authenticates through the
grant."""

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest
import ufo_ext_composio.client as composio
from ufo_ext_composio.manifest import manifest
from ufo_ext_composio.proxy import PROXY_EXECUTE_PATH, ComposioRequestForwarder

ACCOUNT = "ca_github_1"


def _proxy_execute_handler(
    captured: list[dict[str, object]],
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith(PROXY_EXECUTE_PATH):
            captured.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "data": {
                        "data": {"login": "me"},
                        "status": 200,
                        "headers": {"x-github-request-id": "r1"},
                    }
                },
            )
        return httpx.Response(404, json={})

    return handle


def _install(monkeypatch: pytest.MonkeyPatch, captured: list[dict[str, object]]) -> None:
    client = composio.ComposioClient(
        api_key="test", transport=httpx.MockTransport(_proxy_execute_handler(captured))
    )
    monkeypatch.setattr(composio, "composio_client", lambda: client)


async def test_forwarder_executes_the_request_through_proxy_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []
    _install(monkeypatch, captured)

    response = await ComposioRequestForwarder().forward(
        ACCOUNT,
        "GET",
        "https://api.github.com/user",
        {"accept": "application/vnd.github+json"},
        b"",
    )

    assert response.status == 200
    assert json.loads(response.body) == {"login": "me"}
    assert response.headers.get("x-github-request-id") == "r1"
    payload = captured[0]
    assert payload["connected_account_id"] == ACCOUNT
    assert payload["endpoint"] == "https://api.github.com/user"
    assert payload["method"] == "GET"
    parameters = payload.get("parameters")
    assert isinstance(parameters, list)
    assert {"name": "accept", "value": "application/vnd.github+json", "type": "header"} in (
        parameters
    )


async def test_forwarder_carries_a_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, object]] = []
    _install(monkeypatch, captured)

    await ComposioRequestForwarder().forward(
        ACCOUNT,
        "POST",
        "https://api.github.com/repos/o/r/issues",
        {"content-type": "application/json"},
        b'{"title": "hi"}',
    )

    assert captured[0]["body"] == {"title": "hi"}
    assert captured[0]["method"] == "POST"


R2_URL = "https://temp.store.r2.cloudflarestorage.test/zipball/abc?X-Amz-Signature=deadbeef"


@pytest.mark.parametrize(
    "envelope",
    [
        {
            "data": {},
            "binary_data": {
                "url": R2_URL,
                "content_type": "application/zip",
                "size": 4_194_304,
                "expires_at": "2026-07-26T00:00:00Z",
            },
            "status": 200,
            "headers": {"x-github-request-id": "r1"},
        },
        {
            "data": {
                "data": {},
                "binary_data": {"url": R2_URL},
                "status": 200,
                "headers": {"x-github-request-id": "r1"},
            }
        },
    ],
    ids=["flat", "nested"],
)
async def test_forwarder_redirects_a_binary_response_to_its_presigned_url(
    monkeypatch: pytest.MonkeyPatch, envelope: dict[str, object]
) -> None:
    """A non-JSON provider body never rides in `data` — Composio puts the bytes on its file store
    and names them under `binary_data`, leaving `data` an empty object. Reconstructing from `data`
    alone hands back the two bytes `{}` under the provider's own 200, so a zipball, PDF, or image
    arrives empty with nothing looking wrong. The presigned URL is answered as a redirect instead,
    through both envelope shapes: the caller draws the bytes from the store over its own connection
    and the shared proxy pod never buffers them."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=envelope)

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handle))
    monkeypatch.setattr(composio, "composio_client", lambda: client)

    response = await ComposioRequestForwarder().forward(
        ACCOUNT, "GET", "https://api.github.com/repos/o/r/zipball/main", {}, b""
    )

    assert response.status == 302
    assert response.headers["location"] == R2_URL
    assert b"binary provider response" in response.body
    assert response.headers["x-github-request-id"] == "r1"


async def test_forwarder_refuses_binary_data_it_cannot_redirect_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `binary_data` envelope carrying no url has no bytes to return and nowhere to send the
    caller. It fails loud rather than falling through to the empty `data` beside it, which is the
    silent two-byte body."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {}, "binary_data": {"size": 12}, "status": 200})

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handle))
    monkeypatch.setattr(composio, "composio_client", lambda: client)

    with pytest.raises(composio.ComposioError, match="binary_data"):
        await ComposioRequestForwarder().forward(
            ACCOUNT, "GET", "https://api.github.com/repos/o/r/zipball/main", {}, b""
        )


async def test_forwarder_refuses_an_oversized_broker_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared proxy pod runs this forward for every workspace, so a broker/provider response
    is bounded before it is buffered whole — unlike the outbound request (1 MiB), the response had
    no cap. A response past the cap is refused loud (so the proxy answers a defined error) rather
    than buffering unboundedly in the shared process."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"data": "x" * 4096, "status": 200}})

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handle))
    monkeypatch.setattr(composio, "composio_client", lambda: client)

    with pytest.raises(composio.ComposioError, match="response exceeds"):
        await ComposioRequestForwarder(max_response_bytes=64).forward(
            ACCOUNT, "GET", "https://api.github.com/user", {}, b""
        )


async def test_forwarder_bounds_the_proxy_execute_call_with_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The proxy-execute call carries the client timeout so a hung Composio backend cannot wedge the
    shared proxy: the forwarder sets it on the request, and the transport must copy it onto the
    fresh proxy_request it actually sends (a bare AsyncHTTPTransport injects no default)."""
    seen: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json={"data": {"data": {}, "status": 200}})

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handle))
    monkeypatch.setattr(composio, "composio_client", lambda: client)

    await ComposioRequestForwarder().forward(ACCOUNT, "GET", "https://api.github.com/user", {}, b"")

    assert seen["timeout"] == httpx.Timeout(composio.COMPOSIO_TIMEOUT_SECONDS).as_dict()


class _SlowInner(httpx.AsyncBaseTransport):
    """An inner transport that never answers in time — stands in for a Composio backend that
    accepts the connection then trickles or hangs, the case a per-read timeout cannot bound."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json={"data": {"data": {}, "status": 200}})


async def test_forwarder_bounds_the_total_exchange_by_wall_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A per-read timeout resets on every chunk, so a trickling/hung backend never trips it and
    would wedge the shared proxy. The forward is bounded by a total wall-clock deadline instead, so
    the call fails loud (the proxy answers a defined error) rather than blocking forever."""
    client = composio.ComposioClient(api_key="test", transport=_SlowInner())
    monkeypatch.setattr(composio, "composio_client", lambda: client)

    with pytest.raises(composio.ComposioError, match="timed out"):
        await ComposioRequestForwarder(timeout_seconds=0.05).forward(
            ACCOUNT, "GET", "https://api.github.com/user", {}, b""
        )


def test_the_github_connector_declares_the_gh_cli() -> None:
    connectors = {provider.oauth.provider: provider for provider in manifest().connectors}
    assert set(connectors) == {"github"}, "only the CLI exception is an explicit connector"
    github = connectors["github"].cli
    assert github is not None
    assert github.env == "GH_TOKEN"
    assert github.header == "authorization"
    assert isinstance(github.forward, ComposioRequestForwarder)
