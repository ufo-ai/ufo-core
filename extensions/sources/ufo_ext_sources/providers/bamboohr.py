"""The BambooHR REST v1 connector — the employee directory, per-employee detail, time-off and
timesheet windows, the field catalog, and a custom report synced as recallable pages.

BambooHR has no traditional pagination — each list endpoint returns its full dataset in one
response, in a shape that varies per endpoint, so `paginate` dispatches on stream name: the
directory (`/employees/directory`, list under `employees`), the time-off and timesheet date windows
(`start`/`end`, seeded from the run cursor), the field catalog (`/meta/fields`), and a POST custom
report (`/reports/custom`, rows under `employees`). `employees` is the detail view of a directory
row and declares it as an edge. Its key is the directory's own `id`, unique across the account, so
it is addressed by the record alone rather than under the scope its path read. Auth is HTTP Basic
with the API key as the username and the literal `"x"` as the password: when the resolved
`Credential` carries a direct key, `_make_client` sends it as Basic auth; a broker's proxying
transport is honored unchanged. `Accept: application/json` is mandatory — BambooHR defaults to
XML. The base URL is per-tenant
(`https://api.bamboohr.com/api/gateway.php/<subdomain>`), so the class default is empty and a run
without a resolved host fails loud. A refusal (401/403) raises `StreamSkipped`. The write path is
intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from functools import partial
from typing import Any, Literal

import httpx

from ufo.sdk.authproxy import Credential
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

TIMEOUT_CONNECT_SECONDS = 30.0
TIMEOUT_READ_SECONDS = 60.0
_REFUSAL_STATUS = frozenset({401, 403})


def _stream(
    name: str,
    *,
    source_object: str | None = None,
    primary_key: str = "id",
    cursor_field: str | None = None,
    created_at_field: str | None = None,
    updated_at_field: str | None = None,
    canonical: bool = False,
    parents: tuple[ParentEdge, ...] = (),
    key_scope: Literal["local", "global"] = "local",
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=source_object or name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        created_at_field=created_at_field,
        updated_at_field=updated_at_field,
        canonical=canonical,
        parents=parents,
        key_scope=key_scope,
    )


BAMBOOHR_STREAMS: list[StreamSpec] = [
    _stream("employees_directory", canonical=True),
    _stream(
        "time_off_requests",
        cursor_field="created",
        created_at_field="created",
        canonical=True,
    ),
    _stream(
        "employees",
        parents=(ParentEdge(stream="employees_directory", path="/v1/employees/{id}"),),
        key_scope="global",
    ),
    _stream("timesheet_entries", cursor_field="start", created_at_field="start", canonical=True),
    _stream("meta_fields"),
    _stream("custom_reports"),
]


class BambooHRConnector(RestConnector):
    name = "bamboohr"
    base_url = ""
    streams_list = BAMBOOHR_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        timeout = httpx.Timeout(TIMEOUT_CONNECT_SECONDS, read=TIMEOUT_READ_SECONDS)
        base = base_url.rstrip("/")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if credential.transport is not None:
            return httpx.AsyncClient(
                base_url=base, transport=credential.transport, timeout=timeout, headers=headers
            )
        if credential.bearer is not None:
            return httpx.AsyncClient(
                base_url=base,
                timeout=timeout,
                auth=httpx.BasicAuth(credential.bearer, "x"),
                headers=headers,
            )
        raise RuntimeError("bamboohr: credential carries no auth")

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            if stream.name == "employees_directory":
                async for page in self._fetch_directory(client):
                    yield page
                return
            if stream.name == "employees":
                pages = partial(self._fetch_employee, client)
                async for detail in fanned_out(stream, run, pages):
                    yield detail
                return
            if stream.name == "time_off_requests":
                async for page in self._fetch_time_off(client, cursor=run.cursor):
                    yield page
                return
            if stream.name == "timesheet_entries":
                async for page in self._fetch_timesheets(client, cursor=run.cursor):
                    yield page
                return
            if stream.name == "meta_fields":
                async for page in self._fetch_meta_fields(client):
                    yield page
                return
            if stream.name == "custom_reports":
                async for page in self._fetch_custom_reports(client):
                    yield page
                return
            raise NotImplementedError(f"bamboohr: stream {stream.name!r} has no paginate dispatch")
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"bamboohr: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks scope or the key is invalid"
                ) from error
            raise

    async def _fetch_directory(
        self, client: httpx.AsyncClient
    ) -> AsyncIterator[list[dict[str, Any]]]:
        data = await self._get(client, "/v1/employees/directory")
        records = data.get("employees") or []
        if records:
            yield records

    async def _fetch_employee(
        self, client: httpx.AsyncClient, partition: Partition, bound: PartitionBound
    ) -> AsyncIterator[WalkPage]:
        detail = await self._get(client, partition.path)
        if isinstance(detail, dict):
            yield WalkPage(records=[detail])

    async def _fetch_time_off(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        params = self._date_window_params(cursor)
        data = await self._get(client, "/v1/time_off/requests/", params=params)
        records = data if isinstance(data, list) else (data.get("requests") or [])
        if records:
            yield records

    async def _fetch_timesheets(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        params = self._date_window_params(cursor)
        data = await self._get(client, "/v1/time_tracking/timesheet_entries", params=params)
        records = data if isinstance(data, list) else (data.get("entries") or [])
        if records:
            yield records

    async def _fetch_meta_fields(
        self, client: httpx.AsyncClient
    ) -> AsyncIterator[list[dict[str, Any]]]:
        data = await self._get(client, "/v1/meta/fields")
        records = data if isinstance(data, list) else (data.get("fields") or [])
        if records:
            yield records

    async def _fetch_custom_reports(
        self, client: httpx.AsyncClient
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """`/v1/reports/custom` — POST with a fields list; rows come back under `employees`."""
        body = {
            "title": "ufo",
            "fields": [
                "id",
                "displayName",
                "firstName",
                "lastName",
                "workEmail",
                "jobTitle",
                "department",
                "supervisor",
                "hireDate",
                "employmentHistoryStatus",
            ],
        }
        data = await self._post(client, "/v1/reports/custom", json=body)
        records = data.get("employees") or []
        if records:
            yield records

    @staticmethod
    def _date_window_params(cursor: str | None) -> dict[str, Any]:
        """The `start`/`end` window BambooHR requires on time-off and timesheet endpoints.
        BambooHR's date format is `YYYY-MM-DD`; an ISO-timestamp cursor is sliced to its date.
        Default `start` is the epoch (a fresh sync grabs everything); `end` is a far-future
        bound."""
        start = "1970-01-01"
        if cursor:
            text = str(cursor).strip()
            if text:
                start = text[:10]
        return {"start": start, "end": "2100-01-01"}
