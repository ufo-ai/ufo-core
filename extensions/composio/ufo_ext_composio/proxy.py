"""The Composio proxy transport, carried by the Composio auth-proxy's `Credential`.

Composio never exposes the provider credential, so a feed-sync source cannot hold a token and call
the provider directly. This transport rewrites each provider request to Composio's
`POST /tools/execute/proxy`, which injects the account's credential server-side and returns the
provider's status/body/headers, so a connector issues ordinary provider HTTP over an
`httpx.AsyncClient` bound to the provider host and header-driven pagination still works. The
connected-account id rides in the proxy payload; ownership is confirmed against the workspace's
broker user before the transport is built (`ComposioAuthProxy.credential`), so a foreign account id
is refused before a page is fetched."""

import json
from dataclasses import dataclass
from typing import Any, cast

import httpx

from ufo_ext_composio import client as composio

PROXY_EXECUTE_PATH = "/tools/execute/proxy"
PROXY_FAULT_MAX_CHARS = 300
BINARY_REDIRECT_STATUS = 302
BINARY_REDIRECT_BODY = (
    b"binary provider response; the bytes live on the broker file store, follow the location header"
)

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


@dataclass(frozen=True)
class ComposioProxyTransport(httpx.AsyncBaseTransport):
    """Rewrites each provider request to Composio's proxy-execute so Composio injects the account's
    credential server-side — the source never holds the token. The request's method, URL with its
    query, headers, and body ride in the proxy payload alongside the `connected_account_id`; the
    provider's status/body/headers are reconstructed from the response so header-driven pagination
    works. The query stays on the `endpoint` URL rather than riding as `parameters` items, because
    that list is keyed by name and a name repeated there reaches the provider once — which turns a
    Sheets `values:batchGet` naming one `ranges` per tab into a read of a single range. The
    incoming request's timeout extension is carried onto the proxy-execute request — the transport
    is driven directly (not via an httpx client that would inject a default), so without this the
    outbound call would be unbounded and a hung broker could wedge the caller.

    The provider's status rides inside proxy-execute's 200 payload, so a status of 400 or more on
    the proxy-execute response itself is Composio's own answer — it did not execute the request.
    Its usual shape is a 400 saying `Connection failed to <url>: fetch failed`, Composio's dial to
    the provider having failed. That is a transport fault of the hop this transport is, so it
    raises as `httpx.ProxyError`, which the connector's retry envelope retries like any other
    transport error; handed through as the provider's status it read as the provider refusing the
    request, which no connector retries and one run's worth of pages died on."""

    api_base: str
    api_key: str
    connected_account_id: str
    inner: httpx.AsyncBaseTransport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        content = await request.aread()
        payload: dict[str, Any] = {
            "connected_account_id": self.connected_account_id,
            "endpoint": str(request.url),
            "method": request.method,
        }
        parameters: list[dict[str, str]] = []
        for raw_key, raw_value in request.headers.raw:
            key = raw_key.decode("latin-1")
            if key.lower() in _SKIP_REQUEST_HEADERS:
                continue
            parameters.append({"name": key, "value": raw_value.decode("latin-1"), "type": "header"})
        if parameters:
            payload["parameters"] = parameters
        if content:
            content_type = request.headers.get("content-type") or ""
            payload["body"] = (
                json.loads(content.decode("utf-8"))
                if "application/json" in content_type
                else content.decode("utf-8", errors="replace")
            )
        timeout = request.extensions.get("timeout")
        proxy_request = httpx.Request(
            "POST",
            f"{self.api_base}{PROXY_EXECUTE_PATH}",
            headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
            json=payload,
            extensions={"timeout": timeout} if timeout is not None else {},
        )
        proxy_response = await self.inner.handle_async_request(proxy_request)
        proxy_content = await proxy_response.aread()
        if proxy_response.status_code >= 400:
            raise httpx.ProxyError(
                f"composio proxy-execute {proxy_response.status_code}: "
                f"{_proxy_fault(proxy_content)}",
                request=request,
            )
        return self._provider_response(json.loads(proxy_content.decode("utf-8")), request)

    def _provider_response(self, payload: dict[str, Any], request: httpx.Request) -> httpx.Response:
        """Reconstruct the provider's response from a proxy-execute payload, unwrapping Composio's
        `data` envelope down to the innermost provider status/body/headers. A provider body that is
        not JSON never rides in `data` — Composio puts it on its file store and names it under
        `binary_data`, leaving `data` empty — so that case answers a redirect to the presigned URL.
        The caller draws the bytes from the store over its own connection, which keeps a multi-MB
        payload off this event loop. The redirect's own body names the condition, so the one
        channel that follows no redirect — feed-sync, whose pages are JSON, never files — fails
        loud with the reason instead of a bare 302."""
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
        binary = payload.get("binary_data")
        if binary is not None:
            url = binary.get("url") if isinstance(binary, dict) else None
            if not isinstance(url, str) or not url:
                raise composio.ComposioError(
                    502, "proxy-execute returned binary_data without a presigned url"
                )
            headers["location"] = url
            headers["content-type"] = "text/plain; charset=utf-8"
            return httpx.Response(
                status_code=BINARY_REDIRECT_STATUS,
                headers=headers,
                content=BINARY_REDIRECT_BODY,
                request=request,
            )
        data = payload.get("data")
        if isinstance(data, (dict, list)):
            content = json.dumps(data).encode("utf-8")
            headers.setdefault("content-type", "application/json")
        elif isinstance(data, str):
            content = data.encode("utf-8")
        else:
            content = b"" if data is None else json.dumps(data).encode("utf-8")
        return httpx.Response(status_code=status, headers=headers, content=content, request=request)

    async def aclose(self) -> None:
        await self.inner.aclose()


def _proxy_fault(content: bytes) -> str:
    text = content.decode("utf-8", errors="replace")
    try:
        body = json.loads(text)
    except ValueError:
        return text[:PROXY_FAULT_MAX_CHARS]
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    return (message if isinstance(message, str) and message else text)[:PROXY_FAULT_MAX_CHARS]
