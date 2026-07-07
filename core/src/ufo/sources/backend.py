"""The adapter that puts a connector on the core source seam.

A connector speaks in streams and async page generators; the source seam speaks in one `SyncResult`
per run. `ConnectorBackend` bridges them: one `source` row is one (account, stream), so `fetch`
resolves the account's `Credential` through the runner's auth proxy, drives the connector's one
stream to completion, and renders each record into a recallable `Page`. A full-collection stream
(`delete_missing`) returns as an authoritative `snapshot` so the driver tombstones records that
vanished; an incremental stream returns `snapshot=False`, advances a watermark over its
`cursor_field`, and names any provider-reported removals in `deletes`.

The credential the proxy hands back is a broker's proxying transport (the secret never leaves the
broker) or a member-added key read host-side from the credential store (the direct/BYOK backend) —
either way it is used in-process by the sync job in the jobs role and NEVER reaches the sandbox or
agent surface, which is the invariant this preserves. Keep the `Credential` out of any structured
log. The manifest registers one backend per connector in the registry."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel

from ufo.sources.connector import Connector, StreamPage, StreamSpec
from ufo.sources.sync import Page, SourceAuth, SyncResult
from ufo.subjects import SHARED_SUBJECT


class ConnectorSourceConfig(BaseModel):
    """Which account + stream one connector source row syncs. `account` is the handle the auth proxy
    resolves the credential for (a broker connected-account id under Composio, a label under the
    direct backend, whose key is keyed by the provider name); `stream` is the connector stream this
    row pulls. `base_url` overrides the connector's host for a per-tenant provider (Freshdesk's
    `https://<account>.freshdesk.com`, Zendesk's `<subdomain>.zendesk.com`), whose connector class
    leaves `base_url` empty; it is part of the config the `source_row_id` hashes, so two tenants of
    the same provider settle on distinct rows. The backend never reads a raw token — it asks the
    proxy for a `Credential`."""

    account: str
    stream: str
    base_url: str | None = None


@dataclass(frozen=True)
class ConnectorBackend:
    """Drives one connector's one stream per run onto the source seam. See the module docstring."""

    connector: Connector
    config_model: ClassVar[type[ConnectorSourceConfig]] = ConnectorSourceConfig

    async def fetch(
        self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth
    ) -> SyncResult:
        if auth.auth_proxy is None:
            raise RuntimeError(
                f"connector source {self.connector.name!r} needs an auth proxy but none is wired "
                "(set [connectors] auth_backend and install a backend that registers it)"
            )
        credential = await auth.auth_proxy.credential(
            auth.workspace_id, self.connector.name, config.account
        )
        stream = self._stream(config.stream)
        base_url = config.base_url or self.connector.base_url
        if not base_url:
            raise RuntimeError(
                f"connector source {self.connector.name!r} resolved no base_url: it is a "
                "per-tenant provider (its connector class leaves base_url empty) and the source "
                "row set no base_url — a misconfigured source fails its run rather than dial an "
                "empty host"
            )
        pages: list[Page] = []
        deletes: list[str] = []
        watermark = cursor
        page_cursor: str | None = None
        async for page in self.connector.fetch_page(
            stream, cursor=cursor, credential=credential, base_url=base_url
        ):
            records = page.records if isinstance(page, StreamPage) else page
            for record in records:
                pages.append(self._page(stream, record))
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

    def _page(self, stream: StreamSpec, record: dict[str, Any]) -> Page:
        """One provider record as a recallable page: the connector's rendered body, keyed by
        `stream.name/<primary key>` so a re-fetch of an unchanged record, an upsert, and a `deletes`
        entry all settle on the same page."""
        ref = _record_ref(stream, record)
        _title, body = self.connector.render(record, stream)
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
