"""Datadog connector over a mock transport: the monitor collection page-numbered over a top-level
array and landed as a snapshot, a walk that never reaches the end of that collection failing rather
than tombstoning against a partial one, the alert feed's cursor walk with its `filter[from]` read
from the watermark or the row's pinned floor, the flattening of a v2 event onto one ISO instant
whether the payload carries an instant or POSIX milliseconds, an unreadable instant as a
`StreamFault`, and a refusal as a `StreamSkipped`. Offline — a canned transport, no DB, no key."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers import datadog
from ufo_ext_sources.providers.datadog import DatadogConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamFault, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig

SITE = "https://api.us5.datadoghq.com"
PINNED_FLOOR = datetime(2026, 1, 15, 9, 30, tzinfo=UTC)


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    cursor: str | None = None,
    backfill_after: datetime | None = None,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler), base_url=SITE)
    return await ConnectorBackend(connector=DatadogConnector()).fetch(
        ConnectorSourceConfig(stream=stream, backfill_after=backfill_after), cursor, auth
    )


def _monitor(monitor_id: int, state: str) -> dict[str, object]:
    return {
        "id": monitor_id,
        "name": f"api latency {monitor_id}",
        "query": "avg(last_5m):p99:trace.http.request{env:prod} > 2",
        "overall_state": state,
        "overall_state_modified": "2026-03-01T10:00:00+00:00",
        "created": "2026-01-01T00:00:00+00:00",
        "modified": "2026-03-01T10:00:00+00:00",
    }


def _monitors_handler(
    seen: list[httpx.QueryParams], pages: list[list[dict[str, object]]]
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.us5.datadoghq.com"
        if request.url.path != "/api/v1/monitor":
            return httpx.Response(404, json={"path": request.url.path})
        seen.append(request.url.params)
        page = int(request.url.params["page"])
        return httpx.Response(200, json=pages[page] if page < len(pages) else [])

    return handle


async def test_monitors_land_the_whole_collection_as_a_snapshot() -> None:
    seen: list[httpx.QueryParams] = []
    result = await _fetch("monitors", _monitors_handler(seen, [[_monitor(1, "OK")]]))
    assert {page.source_ref for page in result.pages} == {"monitors/1"}
    assert result.snapshot is True
    assert result.next_cursor is None
    assert result.pages[0].title == "api latency 1"
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-03-01T10:00:00.000000+00:00"
    assert "OK" in result.pages[0].body
    assert [params["page"] for params in seen] == ["0"]
    assert seen[0]["page_size"] == str(datadog.MONITOR_PAGE_SIZE)


async def test_monitors_page_until_the_collection_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(datadog, "MONITOR_PAGE_SIZE", 1)
    seen: list[httpx.QueryParams] = []
    result = await _fetch(
        "monitors", _monitors_handler(seen, [[_monitor(1, "OK")], [_monitor(2, "Alert")]])
    )
    assert {page.source_ref for page in result.pages} == {"monitors/1", "monitors/2"}
    assert [params["page"] for params in seen] == ["0", "1", "2"]


async def test_monitors_that_never_end_fail_instead_of_snapshotting_a_partial_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(datadog, "MONITOR_PAGE_SIZE", 1)
    monkeypatch.setattr(datadog, "MONITOR_PAGES_MAX", 2)

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_monitor(int(request.url.params["page"]), "OK")])

    with pytest.raises(StreamFault, match="did not end within 2 pages"):
        await _fetch("monitors", handle)


def _event(instant: object, event_id: str = "AAAAAiE") -> dict[str, object]:
    attributes: dict[str, object] = {
        "title": "[Triggered] api latency",
        "message": "p99 above 2s on env:prod",
        "tags": ["env:prod", "service:api"],
        "attributes": {
            "monitor_id": 4242,
            "status": "Alert",
            "monitor_groups": ["region:us-east-1"],
        },
    }
    if instant is not None:
        attributes["timestamp"] = instant
    return {"id": event_id, "type": "event", "attributes": attributes}


def _alerts_handler(
    seen: list[httpx.QueryParams], pages: dict[str | None, dict[str, object]]
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/api/v2/events":
            return httpx.Response(404, json={"path": request.url.path})
        seen.append(request.url.params)
        return httpx.Response(200, json=pages[request.url.params.get("page[cursor]")])

    return handle


async def test_alerts_flatten_onto_one_instant_and_advance_the_watermark() -> None:
    seen: list[httpx.QueryParams] = []
    pages: dict[str | None, dict[str, object]] = {
        None: {
            "data": [_event("2026-03-01T10:00:00Z")],
            "meta": {"page": {"after": "cursor-2"}},
        },
        "cursor-2": {"data": [_event("2026-03-01T10:05:00Z", "BBBBBiE")], "meta": {"page": {}}},
    }
    result = await _fetch("monitor_alerts", _alerts_handler(seen, pages), cursor="2026-03-01")
    assert {page.source_ref for page in result.pages} == {
        "monitor_alerts/AAAAAiE",
        "monitor_alerts/BBBBBiE",
    }
    assert result.snapshot is False
    assert result.next_cursor == "2026-03-01T10:05:00.000000+00:00"
    assert result.pages[0].title == "[Triggered] api latency"
    assert result.pages[0].created_at == "2026-03-01T10:00:00.000000+00:00"
    assert '"monitor_id": 4242' in result.pages[0].body
    assert '"status": "Alert"' in result.pages[0].body
    assert seen[0]["filter[query]"] == datadog.ALERT_QUERY
    assert seen[0]["sort"] == "timestamp"
    assert seen[0]["filter[from]"] == "2026-03-01"
    assert seen[0]["page[limit]"] == str(datadog.EVENT_PAGE_SIZE)


async def test_alerts_read_posix_milliseconds_as_the_same_instant() -> None:
    pages: dict[str | None, dict[str, object]] = {
        None: {"data": [_event(1772013600000)], "meta": {"page": {}}}
    }
    result = await _fetch("monitor_alerts", _alerts_handler([], pages))
    assert result.next_cursor == "2026-02-25T10:00:00.000000+00:00"
    assert result.pages[0].created_at == "2026-02-25T10:00:00.000000+00:00"


async def test_alerts_open_from_the_pinned_floor_and_otherwise_from_datadogs_own_window() -> None:
    seen: list[httpx.QueryParams] = []
    pages: dict[str | None, dict[str, object]] = {
        None: {"data": [_event("2026-03-01T10:00:00Z")], "meta": {"page": {}}}
    }
    await _fetch("monitor_alerts", _alerts_handler(seen, pages), backfill_after=PINNED_FLOOR)
    assert seen[0]["filter[from]"] == "2026-01-15T09:30:00+00:00"
    await _fetch("monitor_alerts", _alerts_handler(seen, pages))
    assert "filter[from]" not in seen[1]


async def test_an_alert_without_a_readable_instant_faults() -> None:
    pages: dict[str | None, dict[str, object]] = {
        None: {"data": [_event(None)], "meta": {"page": {}}}
    }
    with pytest.raises(StreamFault, match="no readable timestamp"):
        await _fetch("monitor_alerts", _alerts_handler([], pages))


async def test_an_unparseable_instant_faults() -> None:
    pages: dict[str | None, dict[str, object]] = {
        None: {"data": [_event("last tuesday")], "meta": {"page": {}}}
    }
    with pytest.raises(StreamFault, match="is not an instant"):
        await _fetch("monitor_alerts", _alerts_handler([], pages))


async def test_refusal_maps_to_stream_skipped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errors": ["Forbidden"]})

    with pytest.raises(StreamSkipped, match="application key"):
        await _fetch("monitors", handle)


async def test_an_unknown_stream_is_skipped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"path": request.url.path})

    with pytest.raises(ValueError, match="has no stream"):
        await _fetch("downtimes", handle)
