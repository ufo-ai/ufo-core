"""The adapter that puts a connector on the core source seam.

A connector speaks in streams and async page generators; the source seam speaks in one `SyncResult`
per run. `ConnectorBackend` bridges them: one `source` row is one (account, stream), so `fetch`
confirms the account belongs to this workspace's broker user, drives the connector's one stream to
completion through the Composio proxy, and renders each record into a recallable `Page`. A
full-collection stream (`delete_missing`) returns as an authoritative `snapshot` so the driver
tombstones records that vanished; an incremental stream returns `snapshot=False`, advances a
watermark over its `cursor_field`, and names any provider-reported removals in `deletes`. The source
holds no token — every provider request is rewritten through Composio, which injects the credential
server-side. The manifest registers one backend per connector in the registry."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel

from selfhost.sdk.sources import SHARED_SUBJECT, Page, SourceAuth, SyncResult
from selfhost_ext_connectors import composio, composio_proxy
from selfhost_ext_connectors.connector import Connector, StreamPage, StreamSpec

_TITLE_KEYS = ("title", "name", "full_name", "login", "subject")


class ConnectorSourceConfig(BaseModel):
    """Which brokered account + stream one connector source row syncs. `account` is the
    connected-account id the OAuth consent recorded; `stream` is the connector stream this row
    pulls. The backend derives the workspace's broker user from the runner's auth and proxies every
    provider call through Composio under this account — it never reads the token."""

    account: str
    stream: str


@dataclass(frozen=True)
class ConnectorBackend:
    """Drives one connector's one stream per run onto the source seam. See the module docstring."""

    connector: Connector
    config_model: ClassVar[type[ConnectorSourceConfig]] = ConnectorSourceConfig

    async def fetch(
        self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth
    ) -> SyncResult:
        broker_user = f"{composio.EXTERNAL_USER_PREFIX}{auth.workspace_id}"
        client = composio.composio_client()
        await client.connected_account(config.account, broker_user)
        stream = self._stream(config.stream)
        token = f"{composio_proxy.PROXY_TOKEN_PREFIX}{config.account}"
        pages: list[Page] = []
        deletes: list[str] = []
        watermark = cursor
        page_cursor: str | None = None
        async for page in self.connector.fetch_page(
            stream, cursor=cursor, access_token=token, base_url=self.connector.base_url
        ):
            records = page.records if isinstance(page, StreamPage) else page
            for record in records:
                pages.append(_render(self.connector.name, stream, record))
                if stream.cursor_field:
                    watermark = _max_str(watermark, record.get(stream.cursor_field))
            if isinstance(page, StreamPage):
                deletes.extend(f"{stream.name}/{external_id}" for external_id in page.deletes)
                if page.next_cursor:
                    page_cursor = page.next_cursor
        snapshot = stream.delete_missing
        next_cursor = None if snapshot else (page_cursor if page_cursor is not None else watermark)
        return SyncResult(
            pages=tuple(pages),
            next_cursor=next_cursor,
            deletes=tuple(deletes),
            snapshot=snapshot,
        )

    def _stream(self, name: str) -> StreamSpec:
        for stream in self.connector.streams():
            if stream.name == name:
                return stream
        raise ValueError(f"connector {self.connector.name!r} has no stream {name!r}")


def _render(connector_name: str, stream: StreamSpec, record: dict[str, Any]) -> Page:
    """One provider record as a recallable page: a title line, its stream/id key, and the record's
    JSON. Keyed by `stream.name/<primary key>` so a re-fetch of an unchanged record, an upsert, and
    a `deletes` entry all settle on the same page."""
    ref = _record_ref(stream, record)
    title = next(
        (record[key] for key in _TITLE_KEYS if isinstance(record.get(key), str)),
        "",
    )
    body = (
        f"# {connector_name} {stream.name}: {title}\n"
        f"{stream.primary_key}: {ref}\n\n{json.dumps(record, sort_keys=True)}"
    )
    return Page(
        source_ref=f"{stream.name}/{ref}",
        digest="sha256:" + hashlib.sha256(body.encode()).hexdigest(),
        subject=SHARED_SUBJECT,
        body=body,
    )


def _record_ref(stream: StreamSpec, record: dict[str, Any]) -> str:
    value = record.get(stream.primary_key)
    if isinstance(value, (str, int)):
        return str(value)
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()


def _max_str(current: str | None, value: Any) -> str | None:
    if not isinstance(value, str):
        return current
    if current is None or value > current:
        return value
    return current
