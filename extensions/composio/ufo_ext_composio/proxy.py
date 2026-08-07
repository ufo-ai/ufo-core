"""The Composio proxy transport, carried by the Composio auth-proxy's `Credential`.

Composio never exposes the provider credential, so a feed-sync source cannot hold a token and call
the provider directly. This transport rewrites each provider request to Composio's
`POST /tools/execute/proxy`, which injects the account's credential server-side and returns the
provider's status/body/headers, so a connector issues ordinary provider HTTP over an
`httpx.AsyncClient` bound to the provider host and header-driven pagination still works. The
connected-account id rides in the proxy payload; ownership is confirmed against the workspace's
broker user before the transport is built (`ComposioAuthProxy.credential`), so a foreign account id
is refused before a page is fetched."""

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

import httpx

from ufo.sdk.connectors import ForwardedResponse
from ufo_ext_composio import client as composio

PROXY_EXECUTE_PATH = "/tools/execute/proxy"
MAX_FORWARD_RESPONSE_BYTES = 10 * 1024 * 1024
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
    outbound call would be unbounded and a hung broker could wedge the caller."""

    api_base: str
    api_key: str
    connected_account_id: str
    inner: httpx.AsyncBaseTransport
    max_response_bytes: int | None = None

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
        proxy_content = await self._read_bounded(proxy_response)
        if proxy_response.status_code >= 400:
            return httpx.Response(
                status_code=proxy_response.status_code, content=proxy_content, request=request
            )
        return self._provider_response(json.loads(proxy_content.decode("utf-8")), request)

    async def _read_bounded(self, response: httpx.Response) -> bytes:
        """Read the broker's whole response, bounded to `max_response_bytes` when set — a response
        past the cap raises rather than buffering unboundedly in the shared proxy pod. Unset (the
        feed-sync path, which runs host-side and pages one response at a time) reads unbounded, so
        this changes only the sandbox CLI-forward path that sets a cap."""
        if self.max_response_bytes is None:
            return await response.aread()
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) > self.max_response_bytes:
                await response.aclose()
                raise composio.ComposioError(
                    502, f"broker response exceeds {self.max_response_bytes} bytes"
                )
        return bytes(body)

    def _provider_response(self, payload: dict[str, Any], request: httpx.Request) -> httpx.Response:
        """Reconstruct the provider's response from a proxy-execute payload, unwrapping Composio's
        `data` envelope down to the innermost provider status/body/headers. A provider body that is
        not JSON never rides in `data` — Composio puts it on its file store and names it under
        `binary_data`, leaving `data` empty — so that case answers a redirect to the presigned URL.
        The caller draws the bytes from the store over its own connection, which is what keeps a
        multi-MB payload out of the shared proxy pod (`MAX_FORWARD_RESPONSE_BYTES` would cap it) and
        off this event loop. The redirect's own body names the condition, so the one channel that
        follows no redirect — feed-sync, whose pages are JSON, never files — fails loud with the
        reason instead of a bare 302."""
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


@dataclass(frozen=True)
class ComposioRequestForwarder:
    """The egress proxy's broker forward for a Composio-granted CLI credential: one provider
    request executed through proxy-execute under the granted account — the same request rewrite the
    feed-sync transport does, reached from the proxy's ForwardRule rather than an httpx client. The
    broker key is read per call so a test's transport override is honoured, and the response is
    capped at `max_response_bytes` so a large provider response cannot drive an unbounded buffer in
    the shared proxy pod. The whole exchange is bounded by a `timeout_seconds` wall-clock deadline —
    the per-read timeout alone resets on every chunk, so a backend that trickles bytes would never
    trip it; the deadline fails the forward loud instead, so a hung broker never wedges the pod."""

    max_response_bytes: int = MAX_FORWARD_RESPONSE_BYTES
    timeout_seconds: float = composio.COMPOSIO_TIMEOUT_SECONDS

    async def forward(
        self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes
    ) -> ForwardedResponse:
        client = composio.composio_client()
        transport = ComposioProxyTransport(
            api_base=composio.COMPOSIO_API_BASE,
            api_key=client.api_key,
            connected_account_id=account_id,
            inner=client.transport or httpx.AsyncHTTPTransport(),
            max_response_bytes=self.max_response_bytes,
        )
        request = httpx.Request(
            method,
            url,
            headers=dict(headers),
            content=body,
            extensions={"timeout": httpx.Timeout(self.timeout_seconds).as_dict()},
        )
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await transport.handle_async_request(request)
                content = await response.aread()
        except TimeoutError as error:
            raise composio.ComposioError(504, "broker forward timed out") from error
        finally:
            await transport.aclose()
        return ForwardedResponse(
            status=response.status_code, headers=dict(response.headers), body=content
        )
