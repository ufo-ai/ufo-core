"""The Composio proxy transport, shared by every connector source backend.

A source is a workspace-level offline sync, and Composio never exposes the provider credential — so
a source cannot hold a token and call the provider directly. This transport rewrites each provider
request to Composio's `POST /tools/execute/proxy`, which injects the account's credential
server-side and returns the provider's status/body/headers; `proxied_client` wraps it in an
`httpx.AsyncClient` bound to the provider host, so a connector issues ordinary provider HTTP and
header-driven pagination still works. The connected-account id rides in the proxy payload; ownership
is confirmed against the
workspace's broker user before any request through `composio.ComposioClient.connected_account`, so a
foreign account id is refused before a page is fetched."""

import json
from dataclasses import dataclass
from typing import Any, cast

import httpx

from selfhost_ext_connectors import composio

PROXY_EXECUTE_PATH = "/tools/execute/proxy"
PROXY_TOKEN_PREFIX = "composio-proxy:"

_BODY_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})
_SKIP_REQUEST_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "authorization",
        "accept-encoding",
        "connection",
        "user-agent",
        "transfer-encoding",
        "te",
        "upgrade",
        "expect",
    }
)


def account_id_from_proxy_token(token: str) -> str:
    """The connected-account id inside a `PROXY_TOKEN_PREFIX` sentinel — the value a source backend
    hands a connector in place of a bearer token, so the connector's `_make_client` recognizes the
    prefix and builds a proxied client rather than a direct one."""
    account_id = token.removeprefix(PROXY_TOKEN_PREFIX)
    if not account_id:
        raise ValueError("proxy token carries no connected-account id")
    return account_id


@dataclass(frozen=True)
class ComposioProxyTransport(httpx.AsyncBaseTransport):
    """Rewrites each provider request to Composio's proxy-execute so Composio injects the account's
    credential server-side — the source never holds the token. The request's method, path, query,
    headers, and body ride in the proxy payload alongside the `connected_account_id`; the provider's
    status/body/headers are reconstructed from the response so header-driven pagination works."""

    api_base: str
    api_key: str
    connected_account_id: str
    inner: httpx.AsyncBaseTransport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        content = await request.aread()
        payload: dict[str, Any] = {
            "connected_account_id": self.connected_account_id,
            "endpoint": str(request.url.copy_with(query=None)),
            "method": request.method,
        }
        parameters: list[dict[str, str]] = [
            {"name": key, "value": value, "type": "query"}
            for key, value in request.url.params.multi_items()
        ]
        for raw_key, raw_value in request.headers.raw:
            key = raw_key.decode("latin-1")
            if key.lower() in _SKIP_REQUEST_HEADERS:
                continue
            parameters.append(
                {"name": key, "value": raw_value.decode("latin-1"), "type": "header"}
            )
        if parameters:
            payload["parameters"] = parameters
        if content:
            content_type = request.headers.get("content-type") or ""
            payload["body"] = (
                json.loads(content.decode("utf-8"))
                if "application/json" in content_type
                else content.decode("utf-8", errors="replace")
            )
        proxy_request = httpx.Request(
            "POST",
            f"{self.api_base}{PROXY_EXECUTE_PATH}",
            headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
            json=payload,
        )
        proxy_response = await self.inner.handle_async_request(proxy_request)
        proxy_content = await proxy_response.aread()
        if proxy_response.status_code >= 400:
            return httpx.Response(
                status_code=proxy_response.status_code, content=proxy_content, request=request
            )
        return self._provider_response(json.loads(proxy_content.decode("utf-8")), request)

    def _provider_response(
        self, payload: dict[str, Any], request: httpx.Request
    ) -> httpx.Response:
        """Reconstruct the provider's response from a proxy-execute payload, unwrapping Composio's
        `data` envelope down to the innermost provider status/body/headers."""
        while True:
            nested = payload.get("data")
            if not (isinstance(nested, dict) and "data" in nested and "status" in nested):
                break
            payload = cast(dict[str, Any], nested)
        status = int(payload.get("status") or 200)
        headers = {
            str(key): str(value)
            for key, value in (payload.get("headers") or {}).items()
            if isinstance(key, str) and value is not None and key.lower() not in _BODY_HEADERS
        }
        data = payload.get("data")
        if isinstance(data, (dict, list)):
            content = json.dumps(data).encode("utf-8")
            headers.setdefault("content-type", "application/json")
        elif isinstance(data, str):
            content = data.encode("utf-8")
        else:
            content = b"" if data is None else json.dumps(data).encode("utf-8")
        return httpx.Response(
            status_code=status, headers=headers, content=content, request=request
        )

    async def aclose(self) -> None:
        await self.inner.aclose()


def proxied_client(
    *,
    client: composio.ComposioClient,
    connected_account_id: str,
    base_url: str,
    timeout: float = composio.COMPOSIO_TIMEOUT_SECONDS,
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    """An `httpx.AsyncClient` bound to `base_url` whose transport proxies every request through
    Composio under `connected_account_id`. The broker's test transport (a `MockTransport`) is
    honoured when set, so a source's whole fetch runs against canned Composio responses."""
    transport = ComposioProxyTransport(
        api_base=composio.COMPOSIO_API_BASE,
        api_key=client.api_key,
        connected_account_id=connected_account_id,
        inner=client.transport or httpx.AsyncHTTPTransport(),
    )
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        transport=transport,
        timeout=timeout,
        headers=headers or {},
    )
