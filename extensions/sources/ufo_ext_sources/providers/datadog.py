"""The Datadog connector — every monitor, and the alert events monitors raise, synced as recallable
pages.

Datadog authenticates a read with two headers rather than one bearer, so the class declares
`key_headers` and the direct backend fills each header from the slot named beside it. Those slots
are the feed's own: the keyed connector declares `datadog_api_key` and `datadog_application_key`
with an `InjectionTarget`, so a feed reading those names would hand every sandbox of the workspace a
key the member filled for sync alone. Its host is per site, so the class carries no `base_url` and
the connection names the host its own org answers on.

`monitors` reads the whole collection each run (`GET /api/v1/monitor`, page-numbered over a
top-level array) and lands as a snapshot: a deleted monitor is tombstoned, and a monitor whose
`overall_state` moves rewrites its page — which is what a trigger on this stream wakes on. A
snapshot is only safe to tombstone against when it is complete, so a walk still paging at
`MONITOR_PAGES_MAX` raises rather than commit a partial collection.

`monitor_alerts` is the transition feed (`GET /api/v2/events` filtered to `source:alert`), read
oldest-first from a watermark. `flatten` lifts each event's nested attributes flat and renders its
instant as ISO-8601 UTC under `timestamp`: the page's `created_at`, the watermark, and the next
run's `filter[from]` are then one value. An event carrying no readable instant raises `StreamFault`,
because a feed whose position cannot be read re-lands or skips alerts in silence. A run still paging
at `EVENT_PAGES_MAX` ends there and the next resumes from the watermark it landed. The first sync
reaches `ALERT_BACKFILL_WINDOW_DAYS` back, so a woken agent has recent history to read one alert
against; every page of that window can open a conversation, and the row's own `backfill_days`
narrows it. A row registered for the whole history pins no floor, and Datadog's own default window
answers its opening read.

A refusal (401/403) raises `StreamSkipped`. The write path is intentionally absent — the source seam
only reads."""

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx

from ufo.sdk.sources import (
    RestConnector,
    Run,
    StreamFault,
    StreamSkipped,
    StreamSpec,
    dict_or_empty,
    get_path,
    normalize_page_timestamp,
    records_at,
)
from ufo_ext_sources.watermark import text_checkpoint

_REFUSAL_STATUS = frozenset({401, 403})

MONITOR_PATH = "/api/v1/monitor"
EVENTS_PATH = "/api/v2/events"
MONITOR_PAGE_SIZE = 100
MONITOR_PAGES_MAX = 100
EVENT_PAGE_SIZE = 100
EVENT_PAGES_MAX = 100
ALERT_QUERY = "source:alert"
ALERT_BACKFILL_WINDOW_DAYS = 7

MONITORS = StreamSpec(
    name="monitors",
    source_object="monitor",
    primary_key="id",
    created_at_field="created",
    updated_at_field="modified",
    delete_missing=True,
    canonical=True,
)
MONITOR_ALERTS = StreamSpec(
    name="monitor_alerts",
    source_object="events",
    primary_key="id",
    cursor_field="timestamp",
    created_at_field="timestamp",
    updated_at_field=None,
    canonical=True,
    backfill_window_days=ALERT_BACKFILL_WINDOW_DAYS,
)

ALL_STREAMS = [MONITORS, MONITOR_ALERTS]


def _instant(value: Any) -> str:
    """One alert event's own instant, read by the rule the adapter projects a page's timestamps
    through — an ISO string or a POSIX epoch, so the watermark and the page cannot disagree. A value
    that rule cannot read raises `StreamFault`: this is the stream's position, and a position that
    cannot be read loses or repeats alerts."""
    if isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool)):
        try:
            return normalize_page_timestamp(str(value))
        except ValueError as error:
            raise StreamFault(
                f"datadog: alert event timestamp {value!r} is not an instant"
            ) from error
    raise StreamFault(f"datadog: alert event carries no readable timestamp ({value!r})")


class DatadogConnector(RestConnector):
    name = "datadog"
    streams_list = ALL_STREAMS
    key_headers: ClassVar[Mapping[str, str]] = {
        "DD-API-KEY": "datadog_feed_api_key",
        "DD-APPLICATION-KEY": "datadog_feed_application_key",
    }
    checkpoint = staticmethod(text_checkpoint)

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            match stream.name:
                case MONITORS.name:
                    async for page in self._monitor_pages(client):
                        yield page
                case MONITOR_ALERTS.name:
                    async for page in self._alert_pages(
                        client, cursor=run.cursor, floor=run.backfill_after
                    ):
                        yield page
                case _:
                    raise StreamSkipped(f"datadog stream {stream.name!r} is not implemented")
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"datadog: {stream.name!r} refused ({error.response.status_code}); the API key "
                    "or the application key is invalid or lacks scope"
                ) from error
            raise

    async def _monitor_pages(
        self, client: httpx.AsyncClient
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Every monitor, page-numbered from zero over a top-level array. The walk reaches the end
        of the collection or it raises: this stream tombstones against what its run enumerated, so a
        truncated snapshot would remove monitors it never read."""
        for page in range(MONITOR_PAGES_MAX):
            records = records_at(
                await self._get(
                    client, MONITOR_PATH, params={"page": page, "page_size": MONITOR_PAGE_SIZE}
                ),
                None,
            )
            if records:
                yield records
            if len(records) < MONITOR_PAGE_SIZE:
                return
        raise StreamFault(
            f"datadog: monitors did not end within {MONITOR_PAGES_MAX} pages of {MONITOR_PAGE_SIZE}"
        )

    async def _alert_pages(
        self, client: httpx.AsyncClient, *, cursor: str | None, floor: datetime | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Alert events oldest first from the watermark, else from the row's pinned floor. The
        boundary is inclusive on Datadog's side, so the event tying the watermark comes back and the
        driver's digest-skip absorbs it."""
        params: dict[str, Any] = {
            "filter[query]": ALERT_QUERY,
            "sort": "timestamp",
            "page[limit]": EVENT_PAGE_SIZE,
        }
        opening = cursor or (None if floor is None else floor.astimezone(UTC).isoformat())
        if opening is not None:
            params["filter[from]"] = opening
        token: str | None = None
        for _page in range(EVENT_PAGES_MAX):
            data = await self._get(
                client,
                EVENTS_PATH,
                params=params if token is None else {**params, "page[cursor]": token},
            )
            records = records_at(data, "data")
            if records:
                yield records
            token = get_path(data, "meta.page.after")
            if not isinstance(token, str) or not token:
                return

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        """One alert event as a flat record: the envelope's `attributes`, the provider payload
        nested under it, the event id, and the instant under `timestamp`."""
        if stream.name != MONITOR_ALERTS.name:
            return record
        attributes = dict_or_empty(record.get("attributes"))
        flat = {**dict_or_empty(attributes.get("attributes")), **attributes}
        flat.pop("attributes", None)
        flat["id"] = record.get("id")
        flat["timestamp"] = _instant(attributes.get("timestamp"))
        return flat
