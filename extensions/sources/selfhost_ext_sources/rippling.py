"""The Rippling connector — companies, workers, and teams synced as recallable pages.

Rippling's public REST API carries one pagination shape: every list response holds the rows under a
key matching the resource (`workers`, `teams`, `companies`) or a generic `data` envelope, plus a
`next` link (absolute URL or relative path) that is absent on the final page. `paginate` follows
`next` until it is missing. Workers and teams filter incrementally with `?updatedAfter=<iso>`;
companies has no documented cursor and full-refreshes. Auth is a bearer token the API accepts on the
default client. A refusal (401/403) raises `StreamSkipped`. The credential is resolved through the
auth proxy the runner threads; this connector holds no token. The write path is intentionally absent
— the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

import httpx

from selfhost.sdk.sources import RestConnector, StreamSkipped, StreamSpec

PAGE_SIZE = 100
_REFUSAL_STATUS = frozenset({401, 403})

RIPPLING_STREAMS: list[StreamSpec] = [
    StreamSpec(name="companies", source_object="companies", primary_key="id", cursor_field=None),
    StreamSpec(
        name="workers",
        source_object="workers",
        primary_key="id",
        cursor_field="updatedAt",
        canonical=True,
    ),
    StreamSpec(
        name="teams",
        source_object="teams",
        primary_key="id",
        cursor_field="updatedAt",
        canonical=True,
    ),
]


class RipplingConnector(RestConnector):
    name = "rippling"
    base_url = "https://rest.ripplingapis.com"
    streams_list = RIPPLING_STREAMS

    @staticmethod
    def _next_path(next_link: str | None) -> str | None:
        if not next_link:
            return None
        parsed = urlparse(next_link)
        if parsed.scheme:
            if not parsed.path:
                return None
            path = parsed.path
            if parsed.query:
                path = f"{path}?{parsed.query}"
            return path
        return next_link

    @staticmethod
    def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": PAGE_SIZE}
        if stream.cursor_field and cursor:
            params["updatedAfter"] = cursor
        return params

    @staticmethod
    def _extract_records(data: Any, stream: StreamSpec) -> list[dict[str, Any]]:
        if isinstance(data, dict):
            named = data.get(stream.name)
            if isinstance(named, list):
                return [r for r in named if isinstance(r, dict)]
            envelope = data.get("data")
            if isinstance(envelope, list):
                return [r for r in envelope if isinstance(r, dict)]
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        return []

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        path: str | None = f"/{stream.source_object.lstrip('/')}"
        params: dict[str, Any] | None = self._initial_query(stream, cursor)
        try:
            while path:
                data = await self._get(client, path, params=params)
                params = None
                records = self._extract_records(data, stream)
                if records:
                    yield records
                path = self._next_path(data.get("next"))
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"rippling: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks scope or the key is invalid"
                ) from error
            raise
