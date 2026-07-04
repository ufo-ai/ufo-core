"""The Recruitee connector — candidates, job offers, and departments synced as recallable pages.

Recruitee has one shape across every list endpoint: page-number pagination (`?page=N&limit=100`)
over a response that wraps the rows under a key matching the stream name (`{"candidates": [...]}`),
looping until a short page. The base URL is per-tenant (`https://api.recruitee.com/c/<company_id>`),
so the class default is empty and a run without a resolved tenant URL fails loud rather than hit the
wrong host; the sync supplies the full prefix and the per-stream paths stay clean (`/candidates`).
Airbyte declares no incremental cursor for any stream, so all three full-refresh. A 401/403
raises `StreamSkipped`. The credential is resolved through the auth proxy the runner threads; this
connector holds no token. The write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from selfhost.sdk.sources import RestConnector, StreamSkipped, StreamSpec

PAGE_SIZE = 100
_REFUSAL_STATUS = frozenset({401, 403})

RECRUITEE_STREAMS: list[StreamSpec] = [
    StreamSpec(name="candidates", source_object="candidates", primary_key="id", canonical=True),
    StreamSpec(name="offers", source_object="offers", primary_key="id", canonical=True),
    StreamSpec(name="departments", source_object="departments", primary_key="id", canonical=False),
]


class RecruiteeConnector(RestConnector):
    name = "recruitee"
    base_url = ""
    streams_list = RECRUITEE_STREAMS

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            async for page in self._get_page_number_pages(
                client,
                f"/{stream.source_object}",
                records_path=stream.name,
                page_size=PAGE_SIZE,
            ):
                yield page
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"recruitee: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks scope or the key is invalid"
                ) from error
            raise
