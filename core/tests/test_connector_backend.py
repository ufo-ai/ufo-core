"""`ConnectorBackend` bounds one run's consumption at `MAX_RECORDS_PER_RUN` and guarantees progress
across slices by two tiers.

Tier 1: a connector yielding native `StreamPage.next_cursor` checkpoints resumes a capped run from
the last one, stored verbatim. Tier 2: a connector yielding none gets a skip-count envelope stored
as its cursor — `{"ufo_backfill": {origin, skip, watermark}}`, parsed only by the adapter; the next
run re-drives `fetch_page` from `origin`, discards the first `skip` records, lands the rest, and
grows the count until the stream exhausts and the envelope dissolves to a plain watermark. A cursor
that is a plain string or a connector's own JSON map is opaque and passes through untouched. A
`delete_missing` (full-snapshot) stream is exempt from the cap and always returns `snapshot=True`:
tombstone correctness requires the complete enumeration, so it is never sliced. Every assertion
reads the adapter's `SyncResult`."""

import json
from collections.abc import AsyncIterator
from typing import Any, ClassVar
from uuid import UUID, uuid4

import httpx
import pytest

from ufo.connectors import Credential
from ufo.sources import backend as backend_module
from ufo.sources.backend import BACKFILL_KEY, ConnectorBackend, ConnectorSourceConfig
from ufo.sources.connector import Connector, StreamPage, StreamSpec
from ufo.sources.rest import RestConnector
from ufo.sources.sync import SourceAuth, SyncResult

ACCOUNT = "acct-1"
Feed = list[list[dict[str, Any]] | StreamPage]


class _FeedConnector(Connector):
    """Yields a canned page feed — the dependency stood in for; every assertion reads the
    adapter's `SyncResult`. `received_cursors` records the cursor each `fetch_page` call was
    driven from, so a test can prove a resumed run drives from the envelope's `origin`."""

    name = "probe"
    base_url = "https://probe.example"

    def __init__(self, stream: StreamSpec, feed: Feed) -> None:
        self._stream = stream
        self._feed = feed
        self.received_cursors: list[str | None] = []
        self.closed = False

    def streams(self) -> list[StreamSpec]:
        return [self._stream]

    async def fetch_page(
        self,
        stream: StreamSpec,
        *,
        cursor: str | None,
        credential: Credential,
        base_url: str,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        self.received_cursors.append(cursor)
        try:
            for page in self._feed:
                yield page
        finally:
            self.closed = True


class _NoAuthProxy:
    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(bearer="unused")


async def _run(
    connector: _FeedConnector, stream: StreamSpec, *, cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_NoAuthProxy())
    return await ConnectorBackend(connector=connector).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream.name), cursor, auth
    )


async def _fetch(stream: StreamSpec, feed: Feed, *, cursor: str | None = None) -> SyncResult:
    return await _run(_FeedConnector(stream, feed), stream, cursor=cursor)


def _records(*ids: int) -> list[dict[str, Any]]:
    return [{"id": i, "updated_at": f"2026-01-{i:02d}T00:00:00Z"} for i in ids]


def _envelope(origin: str | None, skip: int, watermark: str | None) -> str:
    return json.dumps(
        {BACKFILL_KEY: {"origin": origin, "skip": skip, "watermark": watermark}}, sort_keys=True
    )


async def test_capped_run_resumes_at_the_last_native_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 3)
    stream = StreamSpec(name="items", source_object="items", cursor_field="updated_at")
    feed: Feed = [
        StreamPage(records=_records(1, 2), next_cursor="ck1"),
        StreamPage(records=_records(3, 4), next_cursor="ck2"),
        StreamPage(records=_records(5), next_cursor="ck3"),
        StreamPage(records=_records(6), next_cursor="ck4"),
    ]
    result = await _fetch(stream, feed)
    assert len(result.pages) == 5
    assert result.next_cursor == "ck3"
    assert result.snapshot is False


async def test_capped_run_without_checkpoint_stores_the_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 2)
    stream = StreamSpec(name="items", source_object="items", cursor_field="updated_at")
    feed: Feed = [_records(7, 8), _records(9)]
    result = await _fetch(stream, feed, cursor="2026-01-01T00:00:00Z")
    assert len(result.pages) == 2
    assert result.next_cursor == _envelope("2026-01-01T00:00:00Z", 2, "2026-01-08T00:00:00Z")
    assert result.snapshot is False


async def test_capped_run_closes_the_connector_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 1)
    stream = StreamSpec(name="items", source_object="items", cursor_field="updated_at")
    connector = _FeedConnector(stream, [_records(1), _records(2), _records(3)])
    result = await _run(connector, stream)
    assert len(result.pages) == 1
    assert connector.closed is True


async def test_resumed_run_drives_from_origin_and_skips_the_prefix() -> None:
    stream = StreamSpec(name="items", source_object="items", cursor_field="updated_at")
    connector = _FeedConnector(stream, [_records(1, 2, 3, 4)])
    result = await _run(connector, stream, cursor=_envelope("ORIGIN", 2, "WATERMARK"))
    assert connector.received_cursors == ["ORIGIN"]
    assert [page.source_ref for page in result.pages] == ["items/3", "items/4"]
    assert result.snapshot is False


async def test_slicing_lands_every_record_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 2)
    stream = StreamSpec(name="items", source_object="items", cursor_field="updated_at")
    feed: Feed = [_records(1), _records(2), _records(3), _records(4), _records(5)]
    landed: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        result = await _fetch(stream, feed, cursor=cursor)
        landed.extend(page.source_ref for page in result.pages)
        cursor = result.next_cursor
        if ConnectorBackend._decode_cursor(cursor) is None:
            break
    assert landed == [f"items/{i}" for i in range(1, 6)]
    assert cursor == "2026-01-05T00:00:00Z"


async def test_uncapped_snapshot_run_keeps_snapshot_semantics() -> None:
    stream = StreamSpec(name="items", source_object="items", delete_missing=True)
    result = await _fetch(stream, [_records(1, 2)])
    assert result.snapshot is True
    assert result.next_cursor is None
    assert len(result.pages) == 2


async def test_delete_missing_stream_ignores_the_cap_and_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 2)
    stream = StreamSpec(name="items", source_object="items", delete_missing=True)
    feed: Feed = [_records(1), _records(2), _records(3), _records(4), _records(5)]
    result = await _fetch(stream, feed)
    assert result.snapshot is True
    assert result.next_cursor is None
    assert [page.source_ref for page in result.pages] == [f"items/{i}" for i in range(1, 6)]


async def test_connector_json_map_cursor_round_trips_untouched() -> None:
    stream = StreamSpec(name="items", source_object="items", cursor_field="updated_at")
    incoming = json.dumps({"acme/repo1": "2026-02-01T00:00:00Z"}, sort_keys=True)
    outgoing = json.dumps({"acme/repo1": "2026-02-04T00:00:00Z"}, sort_keys=True)
    connector = _FeedConnector(stream, [StreamPage(records=_records(1, 2), next_cursor=outgoing)])
    result = await _run(connector, stream, cursor=incoming)
    assert connector.received_cursors == [incoming]
    assert result.next_cursor == outgoing
    assert len(result.pages) == 2


def test_envelope_decoder_rejects_extra_keys() -> None:
    corrupted = json.dumps(
        {"ufo_backfill": {"origin": None, "skip": 0, "watermark": None, "junk": True}}
    )
    with pytest.raises(RuntimeError, match="malformed"):
        ConnectorBackend._decode_cursor(corrupted)


async def test_non_advancing_checkpoints_end_at_the_overrun_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider whose pagination never advances the checkpoint — a self-referential page cursor —
    must not spin the run forever: at CAP_OVERRUN_FACTOR times the cap the run returns with the
    stale cursor and warns, and the next run re-walks."""
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 2)
    stream = StreamSpec(name="items", source_object="items", cursor_field="updated_at")
    feed: Feed = [StreamPage(records=[{"id": index}], next_cursor="stuck") for index in range(100)]
    ceiling = 2 * backend_module.CAP_OVERRUN_FACTOR
    first = await _fetch(stream, feed)
    assert len(first.pages) == ceiling
    assert json.loads(first.next_cursor) == {
        "ufo_backfill": {"origin": None, "skip": ceiling, "watermark": None}
    }
    second = await _fetch(stream, feed, cursor=first.next_cursor)
    landed = {page.source_ref for page in second.pages}
    assert landed == {f"items/{index}" for index in range(ceiling, 2 * ceiling)}
    assert second.snapshot is False


class _RestFeedConnector(RestConnector):
    """A RestConnector whose paginate is a plain generator — the shape whose deterministic close
    on a capped early return is `fetch_page`'s own `finally`."""

    name = "restprobe"
    base_url = "https://restprobe.example"
    streams_list: ClassVar[list[StreamSpec]] = [StreamSpec(name="items", source_object="items")]

    def __init__(self) -> None:
        super().__init__()
        self.paginate_closed = False

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            for index in range(100):
                yield StreamPage(records=[{"id": index}], next_cursor=str(index))
        finally:
            self.paginate_closed = True


async def test_capped_run_closes_a_rest_connectors_paginate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 2)
    connector = _RestFeedConnector()
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_NoAuthProxy())
    result = await ConnectorBackend(connector=connector).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream="items"), None, auth
    )
    assert result.next_cursor == "2"
    assert connector.paginate_closed is True
