"""The Deel connector — HR objects (contracts, tasks, timesheets, payslips, forms) synced into
recallable pages.

Every list answers `{data: [...]}`, so `flatten` stays the identity passthrough, but each one is
reached its own way. `contracts` pages by cursor, carrying `page.cursor` back as `after_cursor`;
`timesheets` pages by `offset`/`limit` until a page comes up short. `tasks` answers under one
contract at a time and pages not at all, so the walk enumerates contracts and reads each one's,
stamping the contract it came from onto every row. Streams whose `cursor_field` is set filter
incrementally with `?updated_after=<iso>`; streams without one full-refresh each run. Auth is the
OAuth bearer the resolved `Credential` carries. A refusal (401/403) raises `StreamSkipped`. The
write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec
from ufo_ext_sources.watermark import text_checkpoint

PAGE_SIZE = 100
CONTRACTS_PATH = "/rest/contracts"
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


DEEL_STREAMS: list[StreamSpec] = [
    _stream("contracts", canonical=True),
    _stream("forms", cursor_field=None),
    _stream("payslips", canonical=True),
    _stream("timesheets", canonical=True),
    _stream("tasks", cursor_field=None, canonical=True),
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
        try:
            if stream.name == "tasks":
                async for page in self._paginate_contract_tasks(client):
                    yield page
                return
            if stream.name == "contracts":
                async for page in self._paginate_cursor(client, CONTRACTS_PATH, stream, cursor):
                    yield page
                return
            async for page in self._paginate_offset(client, stream, cursor):
                yield page
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"deel: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks the scope"
                ) from error
            raise

    async def _paginate_cursor(
        self,
        client: httpx.AsyncClient,
        path: str,
        stream: StreamSpec,
        cursor: str | None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """A cursor-paged list: each page names the cursor the next one starts after, and a page
        naming none is the last. An `offset` here would be a parameter this endpoint does not read,
        so every request would answer with the first page again."""
        params = self._initial_params(stream, cursor)
        while True:
            data = await self._get(client, path, params=params)
            records = self._extract_records(data)
            if records:
                yield records
            page = data.get("page") if isinstance(data, dict) else None
            after = page.get("cursor") if isinstance(page, dict) else None
            if not records or not isinstance(after, str) or not after:
                return
            params = {**self._initial_params(stream, cursor), "after_cursor": after}

    async def _paginate_offset(
        self, client: httpx.AsyncClient, stream: StreamSpec, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        path = f"/rest/{stream.source_object.lstrip('/')}"
        base_params = self._initial_params(stream, cursor)
        offset = 0
        while True:
            params = {**base_params, "offset": offset}
            data = await self._get(client, path, params=params)
            records = self._extract_records(data)
            if not records:
                return
            yield records
            if len(records) < PAGE_SIZE:
                return
            offset += PAGE_SIZE

    async def _paginate_contract_tasks(
        self, client: httpx.AsyncClient
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Tasks answer under one contract at a time, so the walk enumerates contracts and reads
        each one's. Every row carries the contract it came from, which the task record does not
        name and which is the only thing tying it to a worker.

        The contract list goes out unfiltered, and the per-contract read takes no filter of its own,
        so every run reads every contract's tasks. A task's own change does not touch its contract,
        so narrowing the enumeration by a task watermark would stop asking the contracts that did
        not change and lose their tasks for good."""
        async for page in self._paginate_cursor(client, CONTRACTS_PATH, _stream("contracts"), None):
            for contract in page:
                identity = contract.get("id")
                if identity is None:
                    continue
                data = await self._get(
                    client, f"{CONTRACTS_PATH}/{identity}/tasks", params={"limit": PAGE_SIZE}
                )
                records = [
                    {**record, "contract_id": identity} for record in self._extract_records(data)
                ]
                if records:
                    yield records
