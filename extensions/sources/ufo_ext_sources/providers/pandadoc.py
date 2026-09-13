"""The PandaDoc connector — documents, templates and contacts synced as recallable pages.

Every list endpoint sits under `/public/v1/` and pages by `?page=N&count=100` (page numbers start at
1) until a short page; the rows arrive under `results`. `documents` is incremental: the list is
ordered by `date_modified` and filtered server-side by `modified_from`, so a run reads only what
changed, and each document is then hydrated through `GET /public/v1/documents/{id}/details` — the
list row carries status and dates alone, while the details response carries the fields, tokens,
pricing and recipients a member recalls a document by. A document the account can list but not open
(403/404) lands as its list row, so one unreadable document never fails the run.

PandaDoc's own webhook (`document.state.changed`, signed with a per-subscription shared key over the
raw body) would deliver a state change instantly. The source seam is polled — core drives `fetch` on
the sync interval and no surface receives provider callbacks — so this connector polls. Auth is
whatever the resolved `Credential` carries: a broker's proxying transport, or a member-added key,
which PandaDoc takes as its own `API-Key` scheme rather than a bearer. A refusal (401/403) raises
`StreamSkipped`. The write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.sources import (
    RestConnector,
    Run,
    StreamSkipped,
    StreamSpec,
    list_or_empty,
)
from ufo_ext_sources.watermark import text_checkpoint

PAGE_SIZE = 100
_REFUSAL_STATUS = frozenset({401, 403})
_DETAILS_MISSING_STATUS = frozenset({403, 404})

_LIST_PATHS: dict[str, str] = {
    "documents": "/public/v1/documents",
    "templates": "/public/v1/templates",
    "contacts": "/public/v1/contacts",
}


def _stream(
    name: str,
    *,
    cursor_field: str | None = None,
    canonical: bool = False,
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=name,
        primary_key="id",
        cursor_field=cursor_field,
        created_at_field="date_created",
        updated_at_field="date_modified",
        canonical=canonical,
    )


PANDADOC_STREAMS: list[StreamSpec] = [
    _stream("documents", cursor_field="date_modified", canonical=True),
    _stream("templates"),
    _stream("contacts", canonical=True),
]


class PandaDocConnector(RestConnector):
    name = "pandadoc"
    base_url = "https://api.pandadoc.com"
    streams_list = PANDADOC_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        """PandaDoc's key rides its own `API-Key` scheme, so a member-added key is layered on the
        base client; a broker's transport injects auth itself and is left unchanged."""
        client = super()._make_client(base_url, credential)
        if credential.bearer is not None:
            client.headers["Authorization"] = f"API-Key {credential.bearer}"
        return client

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]]]:
        path = _LIST_PATHS.get(stream.name)
        if path is None:
            raise NotImplementedError(f"pandadoc: no list endpoint for stream {stream.name!r}")
        page = 1
        try:
            while True:
                params: dict[str, Any] = {"count": PAGE_SIZE, "page": page}
                if stream.name == "documents":
                    params["order_by"] = "date_modified"
                    if run.cursor:
                        params["modified_from"] = run.cursor
                data = await self._get(client, path, params=params)
                records = list_or_empty(data.get("results"))
                if records:
                    if stream.name == "documents":
                        records = [await self._details(client, record) for record in records]
                    yield records
                if len(records) < PAGE_SIZE:
                    return
                page += 1
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"pandadoc: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "or key lacks read access to it"
                ) from error
            raise

    async def _details(self, client: httpx.AsyncClient, record: dict[str, Any]) -> dict[str, Any]:
        """One document's list row widened by its details response — fields, tokens, pricing and
        recipients. A document the account can list but not open (403/404) keeps the list row."""
        document_id = record.get("id")
        if not isinstance(document_id, str) or not document_id:
            return record
        try:
            details = await self._get(client, f"/public/v1/documents/{document_id}/details")
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _DETAILS_MISSING_STATUS:
                return record
            raise
        return {**record, **details}
