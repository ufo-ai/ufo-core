"""Composio-fed content sources: a provider's records synced into recallable memory pages.

Unlike the action tools (which reach a provider per turn through selfhost's egress proxy under an
agent-scoped grant), a source is a workspace-level offline sync. Composio never exposes the provider
credential, so a source cannot hold a token and call the provider directly: every provider request
is rewritten to Composio's `POST /tools/execute/proxy`, which injects the account's credential
server-side and returns the provider's status/body/headers. On the sync interval the driver calls
`fetch`, which asserts the account belongs to this workspace's broker user (metadata, not a token),
pulls the provider's records through the proxy, and renders each into a `Page`. Core lands the pages
in memory and a derivation job embeds them — the source holds no token and writes no chunk."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, ClassVar, cast

import httpx
from pydantic import BaseModel

from selfhost.sdk.sources import SHARED_SUBJECT, Page, SourceAuth, SyncResult
from selfhost_ext_connectors import composio

ASANA_BACKEND = "asana"
ASANA_BASE_URL = "https://app.asana.com/api/1.0"
ASANA_WORKSPACES = "workspaces"
ASANA_PAGE_LIMIT = 100
PROXY_EXECUTE_PATH = "/tools/execute/proxy"

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
    credential server-side — the source never holds the token. The request's method, path, query,
    headers, and body ride in the proxy payload alongside the `connected_account_id` and the
    workspace's broker `user_id`; the provider's status/body/headers are reconstructed from the
    response so header-driven pagination still works."""

    api_base: str
    api_key: str
    connected_account_id: str
    user_id: str
    inner: httpx.AsyncBaseTransport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        content = await request.aread()
        payload: dict[str, Any] = {
            "connected_account_id": self.connected_account_id,
            "user_id": self.user_id,
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


class AsanaSourceConfig(BaseModel):
    """Which Composio-brokered Asana account this source syncs: the connected-account id the OAuth
    consent recorded. The backend derives the workspace's broker user from the runner's auth and
    proxies every Asana call through Composio under this account — it never reads the token."""

    account: str


@dataclass(frozen=True)
class AsanaSource:
    """Syncs an Asana account's workspaces into memory pages, following Asana's `next_page.offset`
    cursor to the end. Every provider call is proxied through Composio (the credential is injected
    server-side); the confused-deputy guard reads only the account's owning `user_id` metadata,
    never a token."""

    config_model: ClassVar[type[AsanaSourceConfig]] = AsanaSourceConfig

    async def fetch(
        self, config: AsanaSourceConfig, cursor: str | None, auth: SourceAuth
    ) -> SyncResult:
        broker_user = f"{composio.EXTERNAL_USER_PREFIX}{auth.workspace_id}"
        client = composio.composio_client()
        async with httpx.AsyncClient(
            base_url=composio.COMPOSIO_API_BASE,
            headers={"x-api-key": client.api_key},
            transport=client.transport,
            timeout=composio.COMPOSIO_TIMEOUT_SECONDS,
        ) as api:
            metadata = await api.get(f"/connected_accounts/{config.account}")
        if metadata.status_code >= 400:
            raise composio.ComposioError(metadata.status_code, metadata.text)
        owner = metadata.json().get("user_id")
        if owner != broker_user:
            raise composio.ComposioError(
                403,
                f"connected account {config.account!r} is owned by {owner!r}, not {broker_user!r}",
            )
        transport = ComposioProxyTransport(
            api_base=composio.COMPOSIO_API_BASE,
            api_key=client.api_key,
            connected_account_id=config.account,
            user_id=broker_user,
            inner=client.transport or httpx.AsyncHTTPTransport(),
        )
        pages: list[Page] = []
        async with httpx.AsyncClient(
            base_url=ASANA_BASE_URL, transport=transport, timeout=composio.COMPOSIO_TIMEOUT_SECONDS
        ) as http:
            offset: str | None = None
            while True:
                params: dict[str, str | int] = {"limit": ASANA_PAGE_LIMIT}
                if offset:
                    params["offset"] = offset
                response = await http.get(f"/{ASANA_WORKSPACES}", params=params)
                response.raise_for_status()
                body = response.json()
                for record in body.get("data") or []:
                    gid = record.get("gid") or ""
                    text = (
                        f"# Asana workspace: {record.get('name') or ''}\n"
                        f"gid: {gid}\n\n{json.dumps(record, sort_keys=True)}"
                    )
                    pages.append(
                        Page(
                            source_ref=f"{ASANA_WORKSPACES}/{gid}",
                            digest="sha256:" + hashlib.sha256(text.encode()).hexdigest(),
                            subject=SHARED_SUBJECT,
                            body=text,
                        )
                    )
                next_page = body.get("next_page")
                offset = next_page.get("offset") if isinstance(next_page, dict) else None
                if not offset:
                    break
        return SyncResult(pages=tuple(pages), next_cursor=None)
