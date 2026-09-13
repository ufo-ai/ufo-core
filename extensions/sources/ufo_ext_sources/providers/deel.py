"""The Deel connector — HR objects (contracts, tasks, timesheets, payslips, forms) synced into
recallable pages.

Every list answers `{data: [...]}`, so `flatten` stays the identity passthrough, but each one is
reached its own way. `contracts` pages by cursor, carrying `page.cursor` back as `after_cursor`;
`timesheets` pages by `offset`/`limit` until a page comes up short. `tasks` answers under one
contract at a time, never as a collection of its own, so it declares `contracts` as its parent and
carries each contract's id onto the rows it holds — the only thing tying a task to a worker, which
the task record does not name. A task id is unique inside its contract and no further: Deel types
it as a bare string, writes `format: uuid` where it means one, and publishes no `GET /tasks/{id}`
(`developer.deel.com/openapi/endpoints-5.json`). The per-contract read pages not at all and takes
no filter, so a pass re-reads every contract's tasks whole and is the authoritative collection the
driver tombstones against. Streams whose `cursor_field` is set filter
incrementally with `?updated_after=<iso>`; streams without one full-refresh each run. Auth is the
OAuth bearer the resolved `Credential` carries. A refusal (401/403) raises `StreamSkipped`. The
write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from functools import partial
from typing import Any

import httpx

from ufo.sdk.sources import (
    ParentEdge,
    Partition,
    PartitionBound,
    RestConnector,
    Run,
    StreamPage,
    StreamSkipped,
    StreamSpec,
    WalkPage,
    fanned_out,
)
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
    delete_missing: bool = False,
    parents: tuple[ParentEdge, ...] = (),
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=source_object or name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        canonical=canonical,
        delete_missing=delete_missing,
        parents=parents,
    )


DEEL_STREAMS: list[StreamSpec] = [
    _stream("contracts", canonical=True),
    _stream("forms", cursor_field=None),
    _stream("payslips", canonical=True),
    _stream("timesheets", canonical=True),
    _stream(
        "tasks",
        cursor_field=None,
        canonical=True,
        delete_missing=True,
        parents=(
            ParentEdge(
                stream="contracts",
                path=f"{CONTRACTS_PATH}/{{id}}/tasks",
                carry={"contract_id": "id"},
            ),
        ),
    ),
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
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            if stream.name == "tasks":
                pages = partial(self._contract_tasks, client)
                async for task_page in fanned_out(stream, run, pages):
                    yield task_page
                return
            if stream.name == "contracts":
                async for contract_page in self._paginate_cursor(
                    client, CONTRACTS_PATH, stream, run.cursor
                ):
                    yield contract_page
                return
            async for offset_page in self._paginate_offset(client, stream, run.cursor):
                yield offset_page
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

    async def _contract_tasks(
        self, client: httpx.AsyncClient, partition: Partition, bound: PartitionBound
    ) -> AsyncIterator[WalkPage]:
        """One contract's tasks, which the endpoint answers whole. A refusal fails the run rather
        than dropping the contract out of the pass: this stream tombstones against its own
        enumeration, so a partition missing from a pass that still completed would sweep tasks that
        are merely unread."""
        data = await self._get(client, partition.path, params={"limit": PAGE_SIZE})
        yield WalkPage(records=self._extract_records(data))
