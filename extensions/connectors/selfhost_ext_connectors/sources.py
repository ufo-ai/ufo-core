"""Composio-fed content sources: a provider's records synced into recallable memory pages.

Unlike the action tools (which reach a provider per turn through selfhost's egress proxy under an
agent-scoped grant), a source is a workspace-level offline sync. Composio never exposes the provider
credential, so a source cannot hold a token and call the provider directly: every provider request
is rewritten through `composio_proxy`, which injects the account's credential server-side. On the
sync interval the driver calls `fetch`, which asserts the account belongs to this workspace's broker
user (metadata, not a token), pulls the provider's records through the proxy, and renders each into
a `Page`. Core lands the pages in memory and a derivation job embeds them — the source holds no
token and writes no chunk. The generic connector framework (`backend.ConnectorBackend`) drives the
REST providers; `AsanaSource` is a hand-written source kept for the endpoints it already proves."""

import hashlib
import json
from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel

from selfhost.sdk.sources import SHARED_SUBJECT, Page, SourceAuth, SyncResult
from selfhost_ext_connectors import composio, composio_proxy

ASANA_BACKEND = "asana"
ASANA_BASE_URL = "https://app.asana.com/api/1.0"
ASANA_WORKSPACES = "workspaces"
ASANA_PAGE_LIMIT = 100


class AsanaSourceConfig(BaseModel):
    """Which Composio-brokered Asana account this source syncs: the connected-account id the OAuth
    consent recorded. The backend derives the workspace's broker user from the runner's auth and
    proxies every Asana call through Composio under this account — it never reads the token."""

    account: str


@dataclass(frozen=True)
class AsanaSource:
    """Syncs an Asana account's workspaces into memory pages, following Asana's `next_page.offset`
    cursor to the end. Every provider call is proxied through Composio (the credential is injected
    server-side); the confused-deputy guard confirms the account's owning broker user before any
    fetch, reading only metadata, never a token."""

    config_model: ClassVar[type[AsanaSourceConfig]] = AsanaSourceConfig

    async def fetch(
        self, config: AsanaSourceConfig, cursor: str | None, auth: SourceAuth
    ) -> SyncResult:
        broker_user = f"{composio.EXTERNAL_USER_PREFIX}{auth.workspace_id}"
        client = composio.composio_client()
        await client.connected_account(config.account, broker_user)
        pages: list[Page] = []
        async with composio_proxy.proxied_client(
            client=client, connected_account_id=config.account, base_url=ASANA_BASE_URL
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
        return SyncResult(pages=tuple(pages), next_cursor=None, snapshot=True)
