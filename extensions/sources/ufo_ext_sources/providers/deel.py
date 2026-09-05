"""The Deel connector — HR objects (contracts, tasks, timesheets, payslips, forms) synced into
recallable pages.

Deel's REST v2 API paginates uniformly: `?limit=100&offset=N`, response `{data: [...], total: N}`,
looping until the page returns short of the limit. Records arrive flat under `data`, so `flatten`
stays the identity passthrough. Streams whose `cursor_field` is set filter incrementally with
`?updated_after=<iso>`; streams without one full-refresh each run. Auth is the OAuth bearer the
resolved `Credential` carries. A refusal (401/403) raises `StreamSkipped`. The write path is
intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec
from ufo_ext_sources.watermark import text_checkpoint

PAGE_SIZE = 100
_REFUSAL_STATUS = frozenset({401, 403})


def _stream(
    name: str,
    *,
    source_object: str | None = None,
    primary_key: str = "id",
    cursor_field: str | None = "updated_at",
    canonical: bool = False,
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=source_object or name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        canonical=canonical,
    )


# Stream set mirrors Deel's REST v2 read surface (5 streams). `contracts` carries employee identity
# via its embedded `worker` sub-object; `forms` has no `updated_at` cursor and full-refreshes.
DEEL_STREAMS: list[StreamSpec] = [
    _stream("contracts", canonical=True),
    _stream("forms", cursor_field=None),
    _stream("payslips"),
    _stream("timesheets"),
    _stream("tasks"),
]


class DeelConnector(RestConnector):
    name = "deel"
    base_url = "https://api.letsdeel.com"
    streams_list = DEEL_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    @staticmethod
    def _initial_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": PAGE_SIZE}
        if cursor and stream.cursor_field:
            params["updated_after"] = cursor
        return params

    @staticmethod
    def _extract_records(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, dict):
            inner = data.get("data")
            if isinstance(inner, list):
                return [record for record in inner if isinstance(record, dict)]
        if isinstance(data, list):
            return [record for record in data if isinstance(record, dict)]
        return []

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        path = f"/rest/v2/{stream.source_object.lstrip('/')}"
        base_params = self._initial_params(stream, cursor)
        offset = 0
        try:
            while True:
                params = dict(base_params)
                params["offset"] = offset
                data = await self._get(client, path, params=params)
                records = self._extract_records(data)
                if not records:
                    return
                yield records
                if len(records) < PAGE_SIZE:
                    return
                offset += PAGE_SIZE
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"deel: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks the scope"
                ) from error
            raise
