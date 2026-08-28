"""The Apollo connector — CRM contacts and accounts synced as recallable pages.

Apollo reads its CRM through POST search endpoints (`/api/v1/contacts/search`,
`/api/v1/accounts/search`), paged by `{page, per_page}` and bounded by the `pagination.total_pages`
the response reports. There is no `modified_since` filter, so the walk is ordered newest-first by
creation and stops at the first page holding nothing above the run's watermark — an incremental run
therefore reads the newest pages alone, and a first run walks the CRM to its end.

Apollo has no resource-change webhook at all: its only webhooks are per-request enrichment
callbacks, so polling is the whole surface. Apollo also rate-limits per minute, hour and day, and
answers 429 with `Retry-After`, which the shared retry envelope waits out. Auth is the member-added
API key the resolved `Credential` carries, which Apollo takes in its own `X-Api-Key` header rather
than as a bearer. A refusal (401/403) raises `StreamSkipped` — a non-master key is refused on parts
of the surface. The write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec, get_path, list_or_empty

PAGE_SIZE = 100
_REFUSAL_STATUS = frozenset({401, 403})

# Stream-name → (search path, response key, sort field).
_SEARCHES: dict[str, tuple[str, str, str]] = {
    "contacts": ("/api/v1/contacts/search", "contacts", "contact_created_at"),
    "accounts": ("/api/v1/accounts/search", "accounts", "account_created_at"),
}


APOLLO_STREAMS: list[StreamSpec] = [
    StreamSpec(
        name="contacts",
        source_object="contacts",
        primary_key="id",
        cursor_field="created_at",
        created_at_field="created_at",
        updated_at_field="updated_at",
        canonical=True,
    ),
    StreamSpec(
        name="accounts",
        source_object="accounts",
        primary_key="id",
        cursor_field="created_at",
        created_at_field="created_at",
        updated_at_field="updated_at",
        canonical=True,
    ),
]


class ApolloConnector(RestConnector):
    name = "apollo"
    base_url = "https://api.apollo.io"
    streams_list = APOLLO_STREAMS

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        """Apollo reads its key from its own `X-Api-Key` header and refuses a bearer, so a
        member-added key moves into that header; a broker's transport injects auth itself and is
        left unchanged."""
        client = super()._make_client(base_url, credential)
        if credential.bearer is not None:
            del client.headers["Authorization"]
            client.headers["X-Api-Key"] = credential.bearer
        return client

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        search = _SEARCHES.get(stream.name)
        if search is None:
            raise NotImplementedError(f"apollo: no search endpoint for stream {stream.name!r}")
        path, key, sort_field = search
        page = 1
        try:
            while True:
                body: dict[str, Any] = {
                    "page": page,
                    "per_page": PAGE_SIZE,
                    "sort_by_field": sort_field,
                    "sort_ascending": False,
                }
                data = await self._post(client, path, json=body)
                records = list_or_empty(data.get(key))
                landed = self._above(records, cursor)
                if landed:
                    yield landed
                if not records or len(landed) < len(records):
                    return
                total_pages = get_path(data, "pagination.total_pages")
                if not isinstance(total_pages, int) or page >= total_pages:
                    return
                page += 1
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"apollo: {stream.name!r} refused ({error.response.status_code}); the key is "
                    "invalid or is not a master key"
                ) from error
            raise

    @staticmethod
    def _above(records: list[dict[str, Any]], cursor: str | None) -> list[dict[str, Any]]:
        """The records newer than the watermark. Apollo takes no time filter, so a newest-first walk
        keeps only what sits above it, and a page that drops a record is the walk's stop signal."""
        if not cursor:
            return records
        return [
            record
            for record in records
            if isinstance(record.get("created_at"), str) and record["created_at"] > cursor
        ]
