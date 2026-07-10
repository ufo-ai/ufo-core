"""The Pipedream Connect Proxy transport, carried by the Pipedream broker's `Credential`.

Pipedream never exposes the provider credential, so a feed-sync source cannot hold a token and call
the provider directly. This transport rewrites each provider request to
`POST /v1/connect/{project}/proxy/{base64url(url)}?external_user_id=…&account_id=…`, which injects
the account's credential server-side and answers with the provider's response verbatim — body,
headers, AND status code pass through, so a connector issues ordinary provider HTTP over an
`httpx.AsyncClient` bound to the provider host, header-driven pagination works, and the status
semantics a connector leans on (Gmail's 404 → `CursorExpired`, 401/403 → `StreamSkipped`) survive
the hop. Pipedream forwards only `x-pd-proxy-`-prefixed request headers upstream (prefix stripped),
so the caller's interesting headers are re-prefixed and transport-level ones dropped. The account id
rides the query; ownership is confirmed against the workspace's external user before the transport
is built (`PipedreamBroker.credential`), so a foreign account id is refused before a page is
fetched."""

import base64
from dataclasses import dataclass

import httpx

from ufo_ext_pipedream.client import ENVIRONMENT_HEADER, PIPEDREAM_API_BASE, PipedreamClient

PROXY_HEADER_PREFIX = "x-pd-proxy-"

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
class PipedreamProxyTransport(httpx.AsyncBaseTransport):
    """Rewrites each provider request through the Connect Proxy so Pipedream injects the account's
    credential server-side — the source never holds the token. The upstream URL rides
    base64url-encoded in the proxy path with the account and external user in the query; the
    proxy's answer IS the provider's response (verbatim status/body/headers), so it is returned
    unchanged."""

    client: PipedreamClient
    account_id: str
    external_user_id: str
    inner: httpx.AsyncBaseTransport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        token = await self.client.access_token()
        content = await request.aread()
        headers = {
            "authorization": f"Bearer {token}",
            ENVIRONMENT_HEADER: self.client.environment,
        }
        for raw_key, raw_value in request.headers.raw:
            key = raw_key.decode("latin-1").lower()
            if key in _SKIP_REQUEST_HEADERS:
                continue
            name = key if key.startswith(PROXY_HEADER_PREFIX) else f"{PROXY_HEADER_PREFIX}{key}"
            headers[name] = raw_value.decode("latin-1")
        encoded = base64.urlsafe_b64encode(str(request.url).encode()).rstrip(b"=").decode("ascii")
        proxy_url = httpx.URL(
            f"{PIPEDREAM_API_BASE}/connect/{self.client.project_id}/proxy/{encoded}",
            params={"external_user_id": self.external_user_id, "account_id": self.account_id},
        )
        proxied = httpx.Request(request.method, proxy_url, headers=headers, content=content)
        return await self.inner.handle_async_request(proxied)

    async def aclose(self) -> None:
        await self.inner.aclose()
