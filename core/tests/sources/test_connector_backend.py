"""`ConnectorBackend` bounds one run's consumption at `MAX_RECORDS_PER_RUN` and guarantees progress
across slices by two tiers.

Tier 1: a connector yielding native `StreamPage.next_cursor` checkpoints resumes a capped run from
the last one, stored verbatim. Tier 2: a connector yielding none gets a skip-count envelope stored
as its cursor — `{"ufo_backfill": {origin, skip, watermark}}`, parsed only by the adapter; the next
run re-drives `fetch_page` from `origin`, discards the first `skip` records, lands the rest, and
grows the count until the stream exhausts and the envelope dissolves to a plain watermark. A cursor
that is a plain string or a connector's own JSON map is opaque and passes through untouched. A
`delete_missing` (full-snapshot) stream is exempt from the cap and always returns `snapshot=True`:
tombstone correctness requires the complete enumeration, so it is never sliced. The adapter also
hands a row's pinned backfill window down on the `StreamSpec` it drives, which is how a connector
floors a first sync, and resolves the base URL each run drives — the connector's own fixed host, or
the address a row pins for a per-tenant one. Every assertion reads the adapter's `SyncResult` or the
specs and addresses the probe connector was driven with."""

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from typing import Any, ClassVar
from uuid import UUID, uuid4

import httpx
import pytest

from ufo.runtime.access.connectors import Credential, GrantUnusable
from ufo.runtime.sources import backend as backend_module
from ufo.runtime.sources.backend import BACKFILL_KEY, ConnectorBackend, ConnectorSourceConfig
from ufo.runtime.sources.connector import (
    Connector,
    Ordering,
    ParentEdge,
    ParentPages,
    ParentRecord,
    Partition,
    PartitionBound,
    PartitionSkipped,
    PartitionWalk,
    Run,
    StreamPage,
    StreamSpec,
    TreeFanOut,
    UnprojectedParent,
    UnreadyParent,
    WalkPage,
    fanned_out,
    no_parents,
    syncing_streams,
)
from ufo.runtime.sources.rest import ProviderRateLimited, RestConnector
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"
Feed = list[list[dict[str, Any]] | StreamPage]


class _FeedConnector(Connector):
    """Yields a canned page feed — the dependency stood in for; every assertion reads the
    adapter's `SyncResult`."""

    name = "probe"
    base_url = "https://probe.example"

    def __init__(self, stream: StreamSpec, feed: Feed) -> None:
        self._stream = stream
        self._feed = feed
        self.received_cursors: list[str | None] = []
        self.received_streams: list[StreamSpec] = []
        self.received_windows: list[datetime | None] = []
        self.received_base_urls: list[str] = []
        self.received_rate_limit_modes: list[bool] = []
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
        self_user_id: str | None,
        backfill_after: datetime | None = None,
        yield_rate_limits: bool = True,
        parents: Any = None,
        watched: Any = None,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        self.received_cursors.append(cursor)
        self.received_streams.append(stream)
        self.received_windows.append(backfill_after)
        self.received_base_urls.append(base_url)
        self.received_rate_limit_modes.append(yield_rate_limits)
        try:
            for page in self._feed:
                yield page
        finally:
            self.closed = True


class _UntitledRecordConnector(_FeedConnector):
    """Renders record 2 with the empty title the page model rejects, every other record normally."""

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        if record.get("id") == 2:
            return "", json.dumps(record, sort_keys=True)
        return super().render(record, stream)


class _NoAuthProxy:
    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(bearer="unused")


class _UnusableGrantProxy:
    """The proxy a broker answers with once the provider stopped honouring the grant — a revoked
    consent, an expired refresh token."""

    reason = (
        "pipedream cannot authenticate connected account 'apn_1': it is unhealthy, so its grant "
        "needs the member to reconnect the account"
    )

    awaits_grant = False

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        raise GrantUnusable(self.reason, awaits_grant=self.awaits_grant)


async def _check_an_unusable_grant_skips_the_stream_instead_of_failing_the_run() -> None:
    """A grant the broker will not authenticate is a refusal, not a fault."""
    stream = StreamSpec(name="tickets", source_object="tickets")
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_UnusableGrantProxy())

    with pytest.raises(StreamSkipped) as raised:
        await ConnectorBackend(connector=_FeedConnector(stream, [])).fetch(
            ConnectorSourceConfig(stream=stream.name), None, auth
        )

    assert raised.value.reason == f"probe: 'tickets' {_UnusableGrantProxy.reason}"
    assert "reconnect the account" in raised.value.reason
    assert raised.value.awaits_grant is False  # the raiser's claim, never invented here


async def _check_only_the_raiser_decides_that_a_grant_event_is_the_one_repair() -> None:
    """`awaits_grant` reaches the driver exactly as the broker set it. A broker naming one
    account unhealthy sets it, and that feed stops polling until the reconnect."""
    stream = StreamSpec(name="tickets", source_object="tickets")

    class _Unhealthy(_UnusableGrantProxy):
        awaits_grant = True

    for proxy, expected in ((_UnusableGrantProxy(), False), (_Unhealthy(), True)):
        with pytest.raises(StreamSkipped) as raised:
            await ConnectorBackend(connector=_FeedConnector(stream, [])).fetch(
                ConnectorSourceConfig(stream=stream.name),
                None,
                SourceAuth(workspace_id=uuid4(), auth_proxy=proxy),
            )
        assert raised.value.awaits_grant is expected


async def _run(
    connector: _FeedConnector,
    stream: StreamSpec,
    *,
    cursor: str | None = None,
    backfill_after: datetime | None = None,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_NoAuthProxy())
    return await ConnectorBackend(connector=connector).fetch(
        ConnectorSourceConfig(stream=stream.name, backfill_after=backfill_after),
        cursor,
        auth,
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
    assert result.pages[0].stream == "items"
    assert result.pages[0].updated_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].title == "items/1"
    assert result.next_cursor == "ck3"
    assert result.snapshot is False


class _RateLimitedConnector(_FeedConnector):
    async def fetch_page(
        self,
        stream: StreamSpec,
        *,
        cursor: str | None,
        credential: Credential,
        base_url: str,
        self_user_id: str | None,
        backfill_after: datetime | None = None,
        yield_rate_limits: bool = True,
        parents: Any = None,
        watched: Any = None,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        yield StreamPage(records=_records(1, 2), next_cursor="ck1")
        raise ProviderRateLimited(60)


async def test_rate_limit_returns_pages_and_the_last_safe_checkpoint() -> None:
    stream = StreamSpec(name="items", source_object="items", cursor_field="updated_at")
    result = await _run(_RateLimitedConnector(stream, []), stream)

    assert [page.source_ref for page in result.pages] == ["items/1", "items/2"]
    assert result.next_cursor == "ck1"
    assert result.retry_after_seconds == 60
    assert result.snapshot is False


class _FeedThenRateLimited(_FeedConnector):
    async def fetch_page(
        self,
        stream: StreamSpec,
        *,
        cursor: str | None,
        credential: Credential,
        base_url: str,
        self_user_id: str | None,
        backfill_after: datetime | None = None,
        yield_rate_limits: bool = True,
        parents: Any = None,
        watched: Any = None,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        for page in self._feed:
            yield page
        raise ProviderRateLimited(60)


@pytest.mark.parametrize(
    ("feed", "landed", "skip"),
    [([_records(1, 2, 3)], ["items/3"], 3), ([_records(1)], [], 2)],
    ids=["past-the-prefix", "inside-the-prefix"],
)
async def test_a_resumed_run_yields_a_rate_limit_and_keeps_its_progress(
    feed: Feed, landed: list[str], skip: int
) -> None:
    stream = StreamSpec(name="items", source_object="items", cursor_field="updated_at")
    cursor = _envelope("ORIGIN", 2, "WATERMARK")

    result = await _run(_FeedThenRateLimited(stream, feed), stream, cursor=cursor)

    assert [page.source_ref for page in result.pages] == landed
    assert result.retry_after_seconds == 60
    envelope = json.loads(result.next_cursor or "{}")[BACKFILL_KEY]
    assert (envelope["origin"], envelope["skip"]) == ("ORIGIN", skip)


async def test_protected_rate_limit_fails_loud_when_a_connector_yields_it() -> None:
    stream = StreamSpec(name="items", source_object="items", delete_missing=True)

    with pytest.raises(RuntimeError, match="yielded a protected rate limit"):
        await _run(_RateLimitedConnector(stream, []), stream)


async def _check_a_rows_pinned_window_reaches_the_connector_beside_the_spec_it_drives() -> None:
    """The window a row pins arrives as `fetch_page`'s own `backfill_after`, and the `StreamSpec`
    is handed down exactly as the connector declared it."""
    cutoff = datetime(2026, 1, 15, 9, 30, tzinfo=UTC)
    stream = StreamSpec(name="items", source_object="items", backfill_window_days=30)
    connector = _FeedConnector(stream, [_records(1)])

    await _run(connector, stream, backfill_after=cutoff)
    await _run(connector, stream)

    assert connector.received_windows == [cutoff, None]
    assert connector.received_streams == [stream, stream]
    assert not hasattr(stream, "backfill_after")
    assert connector.streams()[0].backfill_window_days == 30


async def test_an_unrepresentable_record_is_dropped_and_named_not_run_failing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stream = StreamSpec(name="items", source_object="items", cursor_field="updated_at")
    connector = _UntitledRecordConnector(
        stream, [StreamPage(records=_records(1, 2), next_cursor="2026-01-02T00:00:00Z")]
    )

    with caplog.at_level(logging.WARNING, logger="ufo"):
        result = await _run(connector, stream)

    assert [page.source_ref for page in result.pages] == ["items/1"]
    assert result.next_cursor == "2026-01-02T00:00:00Z"
    assert result.dropped == 1
    dropped = [
        record
        for record in caplog.records
        if record.getMessage() == "source_sync.unrepresentable_record"
    ]
    assert [record.ufo for record in dropped] == [
        {
            "connector": "probe",
            "stream": "items",
            "source_ref": "items/2",
            "fault": "title: string_too_short",
        }
    ]


async def _check_cursor_field_supplies_updated_at_when_provider_value_is_absent() -> None:
    stream = StreamSpec(
        name="items",
        source_object="items",
        cursor_field="watermark",
        updated_at_field="watermark",
    )
    result = await _fetch(
        stream,
        [[{"id": 1, "watermark": "2026-07-23T18:30:00Z"}]],
    )
    assert result.pages[0].updated_at == "2026-07-23T18:30:00.000000+00:00"


async def _check_record_timestamp_fields_resolve_nested_provider_paths() -> None:
    stream = StreamSpec(
        name="items",
        source_object="items",
        created_at_field="timestamps.created",
        updated_at_field="timestamps.updated",
    )
    result = await _fetch(
        stream,
        [
            [
                {
                    "id": 1,
                    "timestamps": {
                        "created": "2026-07-22T18:30:00Z",
                        "updated": "2026-07-23T18:30:00Z",
                    },
                }
            ]
        ],
    )
    assert result.pages[0].created_at == "2026-07-22T18:30:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-07-23T18:30:00.000000+00:00"


async def _check_opaque_cursor_field_does_not_supply_updated_at() -> None:
    stream = StreamSpec(name="items", source_object="items", cursor_field="checkpoint")
    result = await _fetch(stream, [[{"id": 1, "checkpoint": "ck-1"}]])
    assert result.pages[0].updated_at is None


async def _check_large_numeric_cursor_field_does_not_supply_updated_at() -> None:
    stream = StreamSpec(name="items", source_object="items", cursor_field="checkpoint")
    result = await _fetch(stream, [[{"id": 1, "checkpoint": "9" * 100}]])
    assert result.pages[0].updated_at is None


def _check_default_render_rejects_record_without_title_or_identity() -> None:
    stream = StreamSpec(name="items", source_object="items")
    with pytest.raises(ValueError, match="non-empty title or 'id' identity"):
        _FeedConnector(stream, []).render({"body": "untitled"}, stream)


async def test_a_record_with_no_declared_key_is_dropped_and_named_not_content_keyed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stream = StreamSpec(name="items", source_object="items", cursor_field="updated_at")
    feed: Feed = [
        StreamPage(
            records=[{"name": "keyless", "updated_at": "2026-01-03T00:00:00Z"}, *_records(1)],
            next_cursor="2026-01-03T00:00:00Z",
        )
    ]

    with caplog.at_level(logging.WARNING, logger="ufo"):
        result = await _fetch(stream, feed)

    assert [page.source_ref for page in result.pages] == ["items/1"]
    assert result.dropped == 1
    assert result.next_cursor == "2026-01-03T00:00:00Z"
    unkeyed = [
        record for record in caplog.records if record.getMessage() == "source_sync.unkeyed_record"
    ]
    assert [record.ufo for record in unkeyed] == [
        {"connector": "probe", "stream": "items", "primary_key": "id"}
    ]


async def test_a_cursor_stream_whose_records_lack_the_field_is_named(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stream = StreamSpec(name="contacts", source_object="contacts", cursor_field="lastmodified")
    feed: Feed = [[{"id": 1, "modified": "2026-01-03T00:00:00Z"}]]

    with caplog.at_level(logging.WARNING, logger="ufo"):
        result = await _fetch(stream, feed)

    assert result.next_cursor is None
    absent = [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "source_sync.cursor_field_absent"
    ]
    assert absent == [{"connector": "probe", "stream": "contacts", "cursor_field": "lastmodified"}]

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="ufo"):
        await _fetch(
            stream,
            [
                StreamPage(
                    records=[{"id": 1, "lastmodified": "2026-01-03T00:00:00Z"}],
                    next_cursor="2026-01-03T00:00:00Z",
                )
            ],
        )
    assert not [r for r in caplog.records if r.getMessage() == "source_sync.cursor_field_absent"]


async def _check_an_empty_key_is_no_key_so_the_record_is_dropped() -> None:
    stream = StreamSpec(name="items", source_object="items")
    result = await _fetch(stream, [[{"id": "", "name": "blank"}, *_records(1)]])
    assert [page.source_ref for page in result.pages] == ["items/1"]
    assert result.dropped == 1


async def _check_a_declared_key_resolves_a_nested_provider_id() -> None:
    stream = StreamSpec(name="items", source_object="items", primary_key="author.id")
    result = await _fetch(stream, [[{"author": {"id": 7, "login": "ada"}, "total": 3}]])
    assert [page.source_identity for page in result.pages] == ["items/7"]


async def _check_connector_normalizes_integer_timestamps() -> None:
    stream = StreamSpec(
        name="items",
        source_object="items",
        cursor_field="watermark",
        updated_at_field="watermark",
    )
    result = await _fetch(
        stream,
        [[{"id": 1, "created_at": 1_753_296_600, "watermark": 1_753_300_200_000}]],
    )
    assert result.pages[0].created_at == "2025-07-23T18:50:00.000000+00:00"
    assert result.pages[0].updated_at == "2025-07-23T19:50:00.000000+00:00"


async def _check_record_fields_cannot_change_an_opaque_cursor() -> None:
    stream = StreamSpec(name="charges", source_object="charges", cursor_field="created")
    feed: Feed = [[{"id": "ch_1", "created": 999}, {"id": "ch_2", "created": 1000}]]
    result = await _fetch(stream, feed, cursor="encoded:998")
    assert result.next_cursor == "encoded:998"


async def test_malformed_record_timestamp_warns_without_dropping_pages(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stream = StreamSpec(
        name="items",
        source_object="items",
        created_at_field="provider_created",
        updated_at_field="provider_updated",
    )
    with caplog.at_level(logging.WARNING, logger="ufo"):
        result = await _fetch(
            stream,
            [
                [
                    {"id": 1, "provider_created": True, "provider_updated": "not-a-time"},
                    {
                        "id": 2,
                        "provider_created": "2025-07-23T18:50:00Z",
                        "provider_updated": "2025-07-23T19:50:00Z",
                    },
                ]
            ],
        )
    assert len(result.pages) == 2
    assert result.pages[0].created_at is None
    assert result.pages[0].updated_at is None
    assert result.pages[1].created_at == "2025-07-23T18:50:00.000000+00:00"
    assert result.pages[1].updated_at == "2025-07-23T19:50:00.000000+00:00"
    warnings = [
        record
        for record in caplog.records
        if record.getMessage() == "source_sync.malformed_timestamp"
    ]
    assert [record.ufo for record in warnings] == [
        {"connector": "probe", "stream": "items", "field": "provider_created"},
        {"connector": "probe", "stream": "items", "field": "provider_updated"},
    ]


async def test_capped_run_without_checkpoint_stores_the_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 2)
    stream = StreamSpec(name="items", source_object="items", cursor_field="updated_at")
    feed: Feed = [_records(7, 8), _records(9)]
    result = await _fetch(stream, feed, cursor="2026-01-01T00:00:00Z")
    assert len(result.pages) == 2
    assert result.next_cursor == _envelope("2026-01-01T00:00:00Z", 2, "2026-01-01T00:00:00Z")
    assert result.snapshot is False


async def test_capped_run_closes_the_connector_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 1)
    stream = StreamSpec(name="items", source_object="items", cursor_field="updated_at")
    connector = _FeedConnector(stream, [_records(1), _records(2), _records(3)])
    result = await _run(connector, stream)
    assert len(result.pages) == 1
    assert connector.closed is True


async def _check_resumed_run_drives_from_origin_and_skips_the_prefix() -> None:
    stream = StreamSpec(name="items", source_object="items", cursor_field="updated_at")
    connector = _FeedConnector(stream, [_records(1, 2, 3, 4)])
    result = await _run(connector, stream, cursor=_envelope("ORIGIN", 2, "WATERMARK"))
    assert connector.received_cursors == ["ORIGIN"]
    assert connector.received_rate_limit_modes == [True]
    assert [page.source_ref for page in result.pages] == ["items/3", "items/4"]
    assert result.snapshot is False


async def test_slicing_lands_every_record_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 2)
    stream = StreamSpec(name="items", source_object="items", cursor_field="updated_at")
    feed: Feed = [_records(1), _records(2), _records(3), _records(4), _records(5)]
    feed.append(StreamPage(next_cursor="2026-01-05T00:00:00Z"))
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


async def _check_uncapped_snapshot_run_keeps_snapshot_semantics() -> None:
    stream = StreamSpec(name="items", source_object="items", delete_missing=True)
    connector = _FeedConnector(stream, [_records(1, 2)])
    result = await _run(connector, stream)
    assert result.snapshot is True
    assert result.next_cursor is None
    assert len(result.pages) == 2
    assert connector.received_rate_limit_modes == [False]


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


async def test_every_run_carries_its_streams_indexed_declaration() -> None:
    declared = StreamSpec(name="runs", source_object="runs", indexed=False)
    assert StreamSpec(name="items", source_object="items").indexed is True
    unindexed = await _fetch(declared, [_records(1, 2)])
    empty = await _fetch(declared, [])
    indexed = await _fetch(StreamSpec(name="items", source_object="items"), [_records(1)])
    assert (unindexed.indexed, len(unindexed.pages)) == (False, 2)
    assert (empty.indexed, empty.pages) == (False, ())
    assert indexed.indexed is True


async def _check_connector_json_map_cursor_round_trips_untouched() -> None:
    stream = StreamSpec(name="items", source_object="items", cursor_field="updated_at")
    incoming = json.dumps({"acme/repo1": "2026-02-01T00:00:00Z"}, sort_keys=True)
    outgoing = json.dumps({"acme/repo1": "2026-02-04T00:00:00Z"}, sort_keys=True)
    connector = _FeedConnector(stream, [StreamPage(records=_records(1, 2), next_cursor=outgoing)])
    result = await _run(connector, stream, cursor=incoming)
    assert connector.received_cursors == [incoming]
    assert result.next_cursor == outgoing
    assert len(result.pages) == 2


@pytest.mark.parametrize("token", ["", "000999", "eyJvZmZzZXQiOiAxMDB9", '{"offset": 100}'])
async def test_provider_cursor_round_trips_without_interpretation(token: str) -> None:
    stream = StreamSpec(name="items", source_object="items", cursor_field="updated_at")
    connector = _FeedConnector(stream, [StreamPage(records=_records(1), next_cursor=token)])
    first = await _run(connector, stream)
    second = await _run(connector, stream, cursor=first.next_cursor)
    assert first.next_cursor == token
    assert second.next_cursor == token
    assert connector.received_cursors == [None, token]


def _check_envelope_decoder_rejects_extra_keys() -> None:
    corrupted = json.dumps(
        {"ufo_backfill": {"origin": None, "skip": 0, "watermark": None, "junk": True}}
    )
    with pytest.raises(RuntimeError, match="malformed"):
        ConnectorBackend._decode_cursor(corrupted)


async def test_non_advancing_checkpoints_end_at_the_overrun_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 2)
    stream = StreamSpec(name="items", source_object="items", cursor_field="updated_at")
    feed: Feed = [StreamPage(records=[{"id": index}], next_cursor="stuck") for index in range(100)]
    ceiling = 2 * backend_module.CAP_OVERRUN_FACTOR
    first = await _fetch(stream, feed)
    assert len(first.pages) == ceiling
    assert json.loads(first.next_cursor) == {
        "ufo_backfill": {"origin": None, "skip": ceiling, "watermark": "stuck"}
    }
    second = await _fetch(stream, feed, cursor=first.next_cursor)
    landed = {page.source_ref for page in second.pages}
    assert landed == {f"items/{index}" for index in range(ceiling, 2 * ceiling)}
    assert second.snapshot is False


class _NewestFirstConnector(RestConnector):
    name = "newest-first"
    base_url = "https://newest-first.example"
    streams_list: ClassVar[list[StreamSpec]] = [
        StreamSpec(name="items", source_object="items", cursor_field="sequence")
    ]

    def __init__(self) -> None:
        super().__init__()
        self.records = [{"id": str(sequence), "sequence": sequence} for sequence in range(4, 0, -1)]

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        minimum = int(run.cursor) if run.cursor is not None else None
        for record in self.records:
            if minimum is None or record["sequence"] >= minimum:
                yield [record]

    def checkpoint(
        self, stream: StreamSpec, records: list[dict[str, Any]], cursor: str | None
    ) -> str | None:
        values = [int(cursor)] if cursor is not None else []
        values.extend(record["sequence"] for record in records)
        return str(max(values)) if values else None


async def test_tier_two_checkpoint_excludes_the_skipped_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 2)
    connector = _NewestFirstConnector()
    stream = connector.streams_list[0]

    first = await _run(connector, stream)
    connector.records = [
        {"id": str(sequence), "sequence": sequence} for sequence in range(6, 0, -1)
    ]
    second = await _run(connector, stream, cursor=first.next_cursor)
    third = await _run(connector, stream, cursor=second.next_cursor)
    fourth = await _run(connector, stream, cursor=third.next_cursor)
    fifth = await _run(connector, stream, cursor=fourth.next_cursor)

    assert fourth.next_cursor == "4"
    assert [page.source_ref for page in fifth.pages] == ["items/6", "items/5"]


class _RestFeedConnector(RestConnector):
    """A RestConnector whose paginate is a plain generator — the shape whose deterministic close
    on a capped early return is `fetch_page`'s own `finally`."""

    name = "restprobe"
    base_url = "https://restprobe.example"
    streams_list: ClassVar[list[StreamSpec]] = [StreamSpec(name="items", source_object="items")]

    def __init__(self) -> None:
        super().__init__()
        self.paginate_closed = False

    def checkpoint(
        self, stream: StreamSpec, records: list[dict[str, Any]], cursor: str | None
    ) -> str | None:
        raise AssertionError("native page checkpoints must bypass the record checkpoint callback")

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
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
        ConnectorSourceConfig(stream="items"), None, auth
    )
    assert result.next_cursor == "2"
    assert connector.paginate_closed is True


ADDRESS_STREAM = StreamSpec(name="items", source_object="items")


class _PerTenantConnector(_FeedConnector):
    """A per-tenant connector: it declares no host of its own, so only a row's address can drive a
    run of it."""

    base_url = ""


async def _check_a_connectors_fixed_host_drives_the_run() -> None:
    connector = _FeedConnector(ADDRESS_STREAM, [_records(1)])
    await _run(connector, ADDRESS_STREAM)
    assert connector.received_base_urls == ["https://probe.example"]


async def _check_a_row_that_pins_the_address_drives_the_run() -> None:
    connector = _PerTenantConnector(ADDRESS_STREAM, [_records(1)])
    auth = SourceAuth(
        workspace_id=uuid4(),
        auth_proxy=_NoAuthProxy(),
        base_url="https://tenant-1.probe.example",
    )
    await ConnectorBackend(connector=connector).fetch(
        ConnectorSourceConfig(stream=ADDRESS_STREAM.name), None, auth
    )
    assert connector.received_base_urls == ["https://tenant-1.probe.example"]


async def test_a_child_stream_with_no_parent_reader_fails_loud() -> None:
    """A stream declaring a parent has no collection of its own to fall back on, so a run
    threaded no reader of the parent's landed records cannot proceed."""
    child = StreamSpec(
        name="issues",
        source_object="issues",
        parents=(ParentEdge(stream="repositories", path="/repos/{full_name}/issues"),),
    )
    connector = _FeedConnector(child, [_records(1)])

    with pytest.raises(RuntimeError, match="threaded no reader"):
        await ConnectorBackend(connector=connector).fetch(
            ConnectorSourceConfig(stream=child.name),
            None,
            SourceAuth(workspace_id=uuid4(), auth_proxy=_NoAuthProxy()),
        )


async def test_a_page_carries_the_fields_its_children_build_their_paths_from() -> None:
    parent = StreamSpec(name="repositories", source_object="repositories")
    child = StreamSpec(
        name="workflow_jobs",
        source_object="jobs",
        parents=(
            ParentEdge(
                stream="repositories",
                path="{owner.url}/{full_name}/jobs",
                carry={"repository": "name"},
            ),
        ),
    )

    class _Catalog(_FeedConnector):
        def streams(self) -> list[StreamSpec]:
            return [parent, child]

    connector = _Catalog(
        parent,
        [
            [
                {
                    "id": 1,
                    "name": "ufo",
                    "full_name": "acme/ufo",
                    "owner": {"url": "https://x/acme"},
                },
                {"id": 2},
            ]
        ],
    )
    result = await _run(connector, parent)

    assert [page.parent_fields for page in result.pages] == [
        {"full_name": "acme/ufo", "name": "ufo", "owner.url": "https://x/acme"},
        {},
    ]


class _TreeConnector(Connector):
    """Drives the real fan-out — `TreeFanOut` over the parent records handed in, through
    `PartitionWalk` — so the pages the adapter addresses are the ones a declared child yields."""

    name = "probe"
    base_url = "https://probe.example"

    def __init__(self, specs: list[StreamSpec], records: dict[str, list[dict[str, Any]]]) -> None:
        self._specs = specs
        self._records = records

    def streams(self) -> list[StreamSpec]:
        return list(self._specs)

    async def fetch_page(
        self,
        stream: StreamSpec,
        *,
        cursor: str | None,
        credential: Credential,
        base_url: str,
        self_user_id: str | None,
        backfill_after: datetime | None = None,
        yield_rate_limits: bool = True,
        parents: Any = None,
        watched: Any = None,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        fan_out = TreeFanOut(stream=stream, parents=parents)
        walk = PartitionWalk(
            ordering=stream.ordering,
            partitions=fan_out.partitions,
            pages=self._pages,
            report=fan_out.report,
        ).stream(cursor)
        async for page in walk:
            yield page

    async def _pages(self, partition: Partition, bound: PartitionBound) -> AsyncIterator[WalkPage]:
        records = self._records[partition.scope or ""]
        values = [str(record["v"]) for record in records]
        yield WalkPage(records=records, high=max(values), low=min(values))


def _landed(**streams: tuple[ParentRecord | UnprojectedParent | UnreadyParent, ...]):
    async def read(
        name: str,
    ) -> AsyncIterator[ParentRecord | UnprojectedParent | UnreadyParent]:
        for record in streams.get(name, ()):
            yield record

    return read


async def _drive(connector: _TreeConnector, stream: StreamSpec, parents: Any) -> SyncResult:
    return await ConnectorBackend(connector=connector).fetch(
        ConnectorSourceConfig(stream=stream.name),
        None,
        SourceAuth(workspace_id=uuid4(), auth_proxy=_NoAuthProxy(), parents=parents),
    )


def _two_parent_child(**scope: Any) -> StreamSpec:
    return StreamSpec(
        name="records",
        source_object="records",
        cursor_field="v",
        ordering=Ordering.ascending,
        parents=(
            ParentEdge(stream="tables", path="/tables/{id}/records"),
            ParentEdge(stream="views", path="/views/{id}/records"),
        ),
        **scope,
    )


async def _check_a_globally_keyed_child_is_addressed_by_its_record_alone() -> None:
    child = _two_parent_child(key_scope="global")
    connector = _TreeConnector(
        [StreamSpec(name="tables", source_object="tables"), child],
        {"tblY": [{"id": "recZ", "v": "2026-01-01"}], "viwZ": [{"id": "recW", "v": "2026-01-02"}]},
    )
    result = await _drive(
        connector,
        child,
        _landed(
            tables=(ParentRecord(ref="tables/tblY", fields={"id": "tblY"}),),
            views=(ParentRecord(ref="views/viwZ", fields={"id": "viwZ"}),),
        ),
    )

    assert {page.source_identity for page in result.pages} == {"records/recZ", "records/recW"}
    assert {page.source_ref for page in result.pages} == {"records/recZ", "records/recW"}
    assert json.loads(result.next_cursor) == {
        "tables/tblY\n/tables/tblY/records": "2026-01-01",
        "views/viwZ\n/views/viwZ/records": "2026-01-02",
    }


async def _check_a_locally_keyed_child_addresses_one_page_per_parent() -> None:
    child = _two_parent_child()
    connector = _TreeConnector(
        [StreamSpec(name="tables", source_object="tables"), child],
        {"tblY": [{"id": "1", "v": "2026-01-01"}], "viwZ": [{"id": "1", "v": "2026-01-02"}]},
    )
    result = await _drive(
        connector,
        child,
        _landed(
            tables=(ParentRecord(ref="tables/tblY", fields={"id": "tblY"}),),
            views=(ParentRecord(ref="views/viwZ", fields={"id": "viwZ"}),),
        ),
    )

    assert {page.source_identity for page in result.pages} == {
        "records/tblY/1",
        "records/viwZ/1",
    }
    assert json.loads(result.next_cursor) == {
        "tables/tblY\n/tables/tblY/records": "2026-01-01",
        "views/viwZ\n/views/viwZ/records": "2026-01-02",
    }


class _EdgeConnector(RestConnector):
    name = "probe"
    base_url = "https://probe.example"

    def __init__(self, specs: list[StreamSpec], seen: list[str]) -> None:
        self.streams_list = specs
        self._seen = seen
        self._urls: list[httpx.URL] = []
        self._params: dict[str, Any] | None = None

    def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        return fanned_out(stream, run, partial(self._pages, client))

    async def _pages(
        self, client: httpx.AsyncClient, partition: Partition, bound: PartitionBound
    ) -> AsyncIterator[WalkPage]:
        response = await self._get_raw(client, partition.path, params=self._params)
        self._seen.append(str(response.request.url.path))
        self._urls.append(response.request.url)
        records = list(response.json())
        spans = [str(record["v"]) for record in records if "v" in record]
        yield WalkPage(
            records=records,
            high=max(spans, default=None),
            low=min(spans, default=None),
        )


def _edge_run(
    specs: list[StreamSpec],
    parents: Any,
    seen: list[str],
    *,
    cursor: str | None = None,
    urls: list[httpx.URL] | None = None,
    params: dict[str, Any] | None = None,
    records: int = 1,
) -> Any:
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"id": f"r{len(seen)}-{index}", "v": "2026-01-01"} for index in range(records)],
        )

    connector = _EdgeConnector(specs, seen)
    connector._urls = urls if urls is not None else []
    connector._params = params
    return ConnectorBackend(connector=connector).fetch(
        ConnectorSourceConfig(stream=specs[-1].name),
        cursor,
        SourceAuth(
            workspace_id=uuid4(),
            auth_proxy=_TransportProxy(answer),
            parents=parents,
        ),
    )


class _TransportProxy:
    def __init__(self, handler: Any) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _check_an_edge_hangs_only_under_the_parents_its_predicate_admits() -> None:
    """Recurly publishes unique codes only under a bulk coupon, so the coupon that is not bulk is
    not a parent of that stream and costs no request."""
    coupons = StreamSpec(name="coupons", source_object="coupons")
    codes = StreamSpec(
        name="unique_coupon_codes",
        source_object="codes",
        parents=(
            ParentEdge(
                stream="coupons",
                path="/coupons/{id}/unique_coupon_codes",
                where={"coupon_type": ("bulk",)},
            ),
        ),
    )
    seen: list[str] = []
    await _edge_run(
        [coupons, codes],
        _landed(
            coupons=(
                ParentRecord(ref="coupons/c1", fields={"id": "c1", "coupon_type": "bulk"}),
                ParentRecord(ref="coupons/c2", fields={"id": "c2", "coupon_type": "single_code"}),
            )
        ),
        seen,
    )

    assert seen == ["/coupons/c1/unique_coupon_codes"]


async def _check_a_parent_lacking_the_field_a_predicate_weighs_is_not_a_parent() -> None:
    coupons = StreamSpec(name="coupons", source_object="coupons")
    codes = StreamSpec(
        name="unique_coupon_codes",
        source_object="codes",
        parents=(
            ParentEdge(
                stream="coupons",
                path="/coupons/{id}/unique_coupon_codes",
                where={"coupon_type": ("bulk",)},
            ),
        ),
    )
    seen: list[str] = []
    result = await _edge_run(
        [coupons, codes],
        _landed(coupons=(ParentRecord(ref="coupons/c3", fields={"id": "c3"}),)),
        seen,
    )

    assert seen == []
    assert result.pages == ()


async def _check_a_recursive_edge_asks_nothing_of_the_blocks_that_contain_none() -> None:
    """Notion's blocks contain blocks, and a leaf costs one empty request per pass unless the
    declaration says it is not a parent."""
    pages = StreamSpec(name="pages", source_object="pages")
    child = ParentEdge(
        stream="blocks",
        path="/blocks/{id}/children",
        where={"has_children": (True,)},
        unless={"type": ("child_page", "child_database", "ai_block")},
    )
    blocks = StreamSpec(
        name="blocks",
        source_object="blocks",
        canonical=True,
        parents=(ParentEdge(stream="pages", path="/blocks/{id}/children"), child),
    )
    seen: list[str] = []
    await _edge_run(
        [pages, blocks],
        _landed(
            pages=(ParentRecord(ref="pages/p1", fields={"id": "p1"}),),
            blocks=(
                ParentRecord(
                    ref="blocks/b1",
                    fields={"id": "b1", "has_children": True, "type": "paragraph"},
                ),
                ParentRecord(
                    ref="blocks/b2",
                    fields={"id": "b2", "has_children": False, "type": "paragraph"},
                ),
                ParentRecord(
                    ref="blocks/b3",
                    fields={"id": "b3", "has_children": True, "type": "child_page"},
                ),
            ),
        ),
        seen,
    )

    assert seen == ["/blocks/b1/children", "/blocks/p1/children"]


def _check_a_predicate_on_a_field_the_path_reads_is_refused() -> None:
    with pytest.raises(ValueError, match="both addresses and filters"):
        ParentEdge(stream="coupons", path="/coupons/{id}/codes", where={"id": ("c1",)})


async def _check_two_edges_to_one_parent_resume_separately() -> None:
    """HubSpot publishes 26 asset collections under one campaign, so a stream can declare several
    edges to the same parent."""
    campaigns = StreamSpec(name="campaigns", source_object="campaigns")
    assets = StreamSpec(
        name="campaign_assets",
        source_object="assets",
        cursor_field="v",
        ordering=Ordering.ascending,
        parents=(
            ParentEdge(stream="campaigns", path="/campaigns/{id}/forms"),
            ParentEdge(stream="campaigns", path="/campaigns/{id}/emails"),
        ),
    )
    landed = _landed(campaigns=(ParentRecord(ref="campaigns/c1", fields={"id": "c1"}),))
    seen: list[str] = []
    first = await _edge_run([campaigns, assets], landed, seen)

    assert seen == ["/campaigns/c1/emails", "/campaigns/c1/forms"]
    assert set(json.loads(first.next_cursor)) == {
        "campaigns/c1\n/campaigns/c1/forms",
        "campaigns/c1\n/campaigns/c1/emails",
    }

    resumed: list[str] = []
    await _edge_run(
        [campaigns, assets],
        landed,
        resumed,
        cursor=json.dumps({"campaigns/c1\n/campaigns/c1/forms": "2026-01-01"}),
    )
    assert resumed == ["/campaigns/c1/emails", "/campaigns/c1/forms"]


async def _check_an_entry_under_a_key_this_declaration_no_longer_produces_is_re_walked() -> None:
    campaigns = StreamSpec(name="campaigns", source_object="campaigns")
    assets = StreamSpec(
        name="campaign_assets",
        source_object="assets",
        parents=(ParentEdge(stream="campaigns", path="/campaigns/{id}/forms"),),
    )
    seen: list[str] = []
    result = await _edge_run(
        [campaigns, assets],
        _landed(campaigns=(ParentRecord(ref="campaigns/c1", fields={"id": "c1"}),)),
        seen,
        cursor=json.dumps({"campaigns/c1": ""}),
    )

    assert seen == ["/campaigns/c1/forms"]
    assert json.loads(result.next_cursor) == {}


async def _check_an_edge_that_carries_its_own_query_keeps_it_beside_the_pagers() -> None:
    """Stripe reaches the balance transactions of one payout by query parameter rather than by
    path segment, and httpx REPLACES a URL's query with the params it is handed."""
    payouts = StreamSpec(name="payouts", source_object="payouts")
    transactions = StreamSpec(
        name="balance_transactions",
        source_object="transactions",
        parents=(ParentEdge(stream="payouts", path="/v1/balance_transactions?payout={id}"),),
    )
    asked: list[httpx.URL] = []
    await _edge_run(
        [payouts, transactions],
        _landed(payouts=(ParentRecord(ref="payouts/po_1", fields={"id": "po_1"}),)),
        [],
        urls=asked,
        params={"limit": "100"},
    )

    assert asked[0].path == "/v1/balance_transactions"
    assert dict(asked[0].params) == {"payout": "po_1", "limit": "100"}


async def _check_an_optional_edge_passes_over_a_parent_of_another_kind() -> None:
    """A HubSpot owner that is not a user carries no `userId`, and the sequences under a user are
    not a collection it has — so it is no partition of that edge."""
    owners = StreamSpec(name="owners", source_object="owners")
    landed = _landed(
        owners=(
            ParentRecord(ref="owners/o1", fields={"userId": "u1"}),
            ParentRecord(ref="owners/o2", fields={}),
        )
    )
    optional = StreamSpec(
        name="sequences",
        source_object="sequences",
        parents=(ParentEdge(stream="owners", path="/sequences/{userId}", optional=True),),
    )
    seen: list[str] = []
    await _edge_run([owners, optional], landed, seen)
    assert seen == ["/sequences/u1"]

    required = StreamSpec(
        name="sequences",
        source_object="sequences",
        parents=(ParentEdge(stream="owners", path="/sequences/{userId}"),),
    )
    with pytest.raises(RuntimeError, match="carries no value that addresses a collection"):
        await _edge_run([owners, required], landed, [])


def _fan_out(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [record.ufo for record in caplog.records if record.getMessage() == "source_sync.fan_out"]


COUPONS = StreamSpec(name="coupons", source_object="coupons")
THREE_COUPONS = _landed(
    coupons=(
        ParentRecord(ref="coupons/c1", fields={"id": "c1", "coupon_type": "bulk"}),
        ParentRecord(ref="coupons/c2", fields={"id": "c2", "coupon_type": "single_code"}),
        ParentRecord(ref="coupons/c3", fields={"id": "c3", "coupon_type": "single_code"}),
    )
)


def _codes(where: dict[str, tuple[Any, ...]]) -> StreamSpec:
    return StreamSpec(
        name="unique_coupon_codes",
        source_object="codes",
        parents=(ParentEdge(stream="coupons", path="/coupons/{id}/codes", where=where),),
    )


async def test_a_completed_pass_counts_what_each_edge_enumerated_and_what_it_cost(
    caplog: pytest.LogCaptureFixture,
) -> None:
    codes = _codes({"coupon_type": ("bulk",)})
    with caplog.at_level(logging.INFO, logger="ufo"):
        await _edge_run([COUPONS, codes], THREE_COUPONS, [], records=2)

    assert _fan_out(caplog) == [
        {
            "stream": "unique_coupon_codes",
            "parent": "coupons",
            "edge": "/coupons/{id}/codes",
            "enumerated": "3",
            "admitted": "1",
            "fetched": "1",
            "landed": "2",
        }
    ]


async def test_an_edge_naming_a_field_no_parent_carries_admits_none_of_a_full_set(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The trap the tree adds: a `where` on a field the parent kind never carries matches
    nothing, and a parent set legitimately all filtered out looks identical."""
    codes = _codes({"coupon_kind": ("bulk",)})
    with caplog.at_level(logging.INFO, logger="ufo"):
        await _edge_run([COUPONS, codes], THREE_COUPONS, [], records=2)

    assert _fan_out(caplog) == [
        {
            "stream": "unique_coupon_codes",
            "parent": "coupons",
            "edge": "/coupons/{id}/codes",
            "enumerated": "3",
            "admitted": "0",
            "fetched": "0",
            "landed": "0",
        }
    ]


async def test_a_pass_that_asks_and_gets_nothing_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The audit's own class: the request is answered, the records are behind an envelope key the
    stream does not read, and every page parses to nothing."""
    codes = _codes({})
    with caplog.at_level(logging.INFO, logger="ufo"):
        await _edge_run([COUPONS, codes], THREE_COUPONS, [], records=0)

    assert _fan_out(caplog) == [
        {
            "stream": "unique_coupon_codes",
            "parent": "coupons",
            "edge": "/coupons/{id}/codes",
            "enumerated": "3",
            "admitted": "3",
            "fetched": "3",
            "landed": "0",
        }
    ]


class _PlainConnector(RestConnector):
    """A connector that overrides nothing but `paginate` — the shape 22 of 24 conversions could not
    have, because the seam dropped the reader unless a connector overrode the seam to keep it."""

    name = "probe"
    base_url = "https://probe.example"

    def __init__(self, spec: StreamSpec) -> None:
        self.streams_list = [spec]
        self.received: list[ParentPages] = []

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        self.received.append(run.parents)
        yield []


async def _check_a_declared_stream_is_handed_its_reader_without_overriding_the_seam() -> None:
    stream = StreamSpec(
        name="issues",
        source_object="issues",
        parents=(ParentEdge(stream="repositories", path="/repos/{full_name}/issues"),),
    )
    connector = _PlainConnector(stream)
    reader = _landed(
        repositories=(ParentRecord(ref="repositories/1", fields={"full_name": "a/b"}),)
    )
    await ConnectorBackend(connector=connector).fetch(
        ConnectorSourceConfig(stream=stream.name),
        None,
        SourceAuth(workspace_id=uuid4(), auth_proxy=_NoAuthProxy(), parents=reader),
    )

    assert connector.received == [reader]


async def _check_a_root_stream_is_handed_a_reader_of_nothing() -> None:
    stream = StreamSpec(name="charges", source_object="charges")
    connector = _PlainConnector(stream)
    await ConnectorBackend(connector=connector).fetch(
        ConnectorSourceConfig(stream=stream.name),
        None,
        SourceAuth(workspace_id=uuid4(), auth_proxy=_NoAuthProxy()),
    )

    assert [record async for record in connector.received[0]("anything")] == []


async def _check_the_fan_out_closes_its_walk_when_its_consumer_stops_early() -> None:
    """The adapter returns out of its `async for` the moment a run reaches its record cap, which
    abandons the generator underneath."""
    closed: list[str] = []
    stream = StreamSpec(
        name="issues",
        source_object="issues",
        parents=(ParentEdge(stream="repositories", path="/repos/{full_name}/issues"),),
    )

    async def pages(partition: Partition, bound: PartitionBound) -> AsyncIterator[WalkPage]:
        try:
            yield WalkPage(records=[{"id": "1"}])
            yield WalkPage(records=[{"id": "2"}])
        finally:
            closed.append(partition.path)

    reader = _landed(
        repositories=(ParentRecord(ref="repositories/1", fields={"full_name": "a/b"}),)
    )
    driven = fanned_out(stream, Run(cursor=None, parents=reader), pages)
    assert [record["id"] for record in (await anext(driven)).records] == ["1"]
    await driven.aclose()

    assert closed == ["/repos/a%2Fb/issues"]


class _SkippingConnector(Connector):
    name = "probe"
    base_url = "https://probe.example"

    def __init__(self, spec: StreamSpec) -> None:
        self._spec = spec

    def streams(self) -> list[StreamSpec]:
        return [self._spec]

    async def fetch_page(
        self,
        stream: StreamSpec,
        *,
        cursor: str | None,
        credential: Credential,
        base_url: str,
        self_user_id: str | None,
        backfill_after: datetime | None = None,
        yield_rate_limits: bool = True,
        parents: Any = no_parents,
        watched: Any = None,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        async for page in fanned_out(stream, Run(cursor=cursor, parents=parents), self._pages):
            yield page

    async def _pages(self, partition: Partition, bound: PartitionBound) -> AsyncIterator[WalkPage]:
        if partition.scope == "c1":
            raise PartitionSkipped("probe: c1 refused")
        yield WalkPage(records=[{"id": "t1"}])


def _two_contracts() -> Any:
    return _landed(
        contracts=(
            ParentRecord(ref="contracts/c1", fields={"id": "c1"}),
            ParentRecord(ref="contracts/c2", fields={"id": "c2"}),
        )
    )


def _tasks(*, delete_missing: bool) -> StreamSpec:
    return StreamSpec(
        name="tasks",
        source_object="tasks",
        delete_missing=delete_missing,
        parents=(ParentEdge(stream="contracts", path="/contracts/{id}/tasks"),),
    )


async def _check_a_snapshot_stream_refuses_to_pass_over_a_partition() -> None:
    """A `delete_missing` run is an authoritative enumeration, and the driver tombstones every
    prior page it does not mention."""
    spec = _tasks(delete_missing=True)
    with pytest.raises(RuntimeError, match="'tasks' cannot snapshot"):
        await ConnectorBackend(connector=_SkippingConnector(spec)).fetch(
            ConnectorSourceConfig(stream=spec.name),
            None,
            SourceAuth(workspace_id=uuid4(), auth_proxy=_NoAuthProxy(), parents=_two_contracts()),
        )


async def _check_a_stream_that_tombstones_nothing_still_passes_over_one() -> None:
    spec = _tasks(delete_missing=False)
    result = await ConnectorBackend(connector=_SkippingConnector(spec)).fetch(
        ConnectorSourceConfig(stream=spec.name),
        None,
        SourceAuth(workspace_id=uuid4(), auth_proxy=_NoAuthProxy(), parents=_two_contracts()),
    )

    assert {page.source_identity for page in result.pages} == {"tasks/c2/t1"}
    assert result.snapshot is False


def _one_contract_unprojected() -> Any:
    return _landed(
        contracts=(
            UnprojectedParent(ref="contracts/c1"),
            ParentRecord(ref="contracts/c2", fields={"id": "c2"}),
        )
    )


async def _check_a_snapshot_stream_refuses_a_parent_landed_without_its_projection() -> None:
    spec = _tasks(delete_missing=True)
    with pytest.raises(
        RuntimeError,
        match="'tasks' cannot snapshot under 'contracts': page 'contracts/c1' landed without",
    ):
        await ConnectorBackend(connector=_SkippingConnector(spec)).fetch(
            ConnectorSourceConfig(stream=spec.name),
            None,
            SourceAuth(
                workspace_id=uuid4(), auth_proxy=_NoAuthProxy(), parents=_one_contract_unprojected()
            ),
        )


async def _check_a_stream_that_tombstones_nothing_passes_over_an_unprojected_parent() -> None:
    spec = _tasks(delete_missing=False)
    result = await ConnectorBackend(connector=_SkippingConnector(spec)).fetch(
        ConnectorSourceConfig(stream=spec.name),
        None,
        SourceAuth(
            workspace_id=uuid4(), auth_proxy=_NoAuthProxy(), parents=_one_contract_unprojected()
        ),
    )

    assert {page.source_identity for page in result.pages} == {"tasks/c2/t1"}
    assert result.snapshot is False


async def _check_a_snapshot_stream_refuses_an_unready_parent_catalog() -> None:
    spec = _tasks(delete_missing=True)
    with pytest.raises(RuntimeError, match="the parent has not completed a sync"):
        await ConnectorBackend(connector=_SkippingConnector(spec)).fetch(
            ConnectorSourceConfig(stream=spec.name),
            None,
            SourceAuth(
                workspace_id=uuid4(),
                auth_proxy=_NoAuthProxy(),
                parents=_landed(contracts=(UnreadyParent(),)),
            ),
        )


async def _check_an_incremental_stream_waits_for_an_unready_parent_catalog() -> None:
    spec = _tasks(delete_missing=False)
    result = await ConnectorBackend(connector=_SkippingConnector(spec)).fetch(
        ConnectorSourceConfig(stream=spec.name),
        None,
        SourceAuth(
            workspace_id=uuid4(),
            auth_proxy=_NoAuthProxy(),
            parents=_landed(contracts=(UnreadyParent(),)),
        ),
    )

    assert result.pages == ()
    assert result.snapshot is False


FREE_TEXT_KEY = "a b/c#d?e@f"


async def _check_a_free_text_parent_key_reaches_the_wire_as_one_encoded_segment() -> None:
    contacts = StreamSpec(name="contacts", source_object="contacts")
    statuses = StreamSpec(
        name="consent_states",
        source_object="statuses",
        parents=(ParentEdge(stream="contacts", path="/statuses/{email}"),),
    )
    asked: list[httpx.URL] = []
    result = await _edge_run(
        [contacts, statuses],
        _landed(contacts=(ParentRecord(ref="contacts/c1", fields={"email": FREE_TEXT_KEY}),)),
        [],
        urls=asked,
    )

    assert str(asked[0]) == "https://probe.example/statuses/a%20b%2Fc%23d%3Fe%40f"
    assert result.pages[0].source_identity == f"consent_states/{FREE_TEXT_KEY}/r0-0"


async def _check_a_free_text_parent_key_in_a_query_reaches_it_as_a_query_value() -> None:
    owners = StreamSpec(name="owners", source_object="owners")
    sequences = StreamSpec(
        name="sequences",
        source_object="sequences",
        parents=(ParentEdge(stream="owners", path="/sequences?owner={handle}"),),
    )
    asked: list[httpx.URL] = []
    await _edge_run(
        [owners, sequences],
        _landed(owners=(ParentRecord(ref="owners/o1", fields={"handle": "ada & co/x"}),)),
        [],
        urls=asked,
        params={"limit": "100"},
    )

    assert asked[0].path == "/sequences"
    assert dict(asked[0].params) == {"owner": "ada & co/x", "limit": "100"}


def _conversations_edge() -> ParentEdge:
    return ParentEdge(
        stream="conversations",
        path="/history/{id}",
        carry={"channel_name": "name", "channel_type": "type", "is_private": "is_private"},
    )


async def _check_an_edge_carries_its_parents_fields_onto_every_child_record() -> None:
    """A Slack message is recalled by the channel it was posted in, which the message itself does
    not name."""
    conversations = StreamSpec(name="conversations", source_object="conversations")
    messages = StreamSpec(
        name="messages", source_object="messages", canonical=True, parents=(_conversations_edge(),)
    )
    seen: list[str] = []
    result = await _edge_run(
        [conversations, messages],
        _landed(
            conversations=(
                ParentRecord(
                    ref="conversations/C1",
                    fields={"id": "C1", "name": "general", "type": "channel", "is_private": False},
                ),
            )
        ),
        seen,
    )

    assert seen == ["/history/C1"]
    landed = json.loads(result.pages[0].body.split("\n\n", 1)[1])
    assert landed["channel_name"] == "general"
    assert landed["channel_type"] == "channel"
    assert landed["is_private"] is False


async def _check_a_parent_carrying_nothing_for_a_field_writes_nothing() -> None:
    """A parent of another kind carries none of it, and the child lands without it rather than
    failing the run — the same answer a predicate gives, and the opposite of a path field."""
    conversations = StreamSpec(name="conversations", source_object="conversations")
    messages = StreamSpec(
        name="messages", source_object="messages", parents=(_conversations_edge(),)
    )
    seen: list[str] = []
    result = await _edge_run(
        [conversations, messages],
        _landed(conversations=(ParentRecord(ref="conversations/D1", fields={"id": "D1"}),)),
        seen,
    )

    assert seen == ["/history/D1"]
    landed = json.loads(result.pages[0].body.split("\n\n", 1)[1])
    assert "channel_name" not in landed


async def _check_a_record_already_carrying_a_carried_field_raises() -> None:
    """Two answers for one field is a thing to say out loud: the provider sent one and the parent
    would write the other, and which wins is the connector's to state in `flatten`."""
    conversations = StreamSpec(name="conversations", source_object="conversations")
    messages = StreamSpec(
        name="messages",
        source_object="messages",
        parents=(ParentEdge(stream="conversations", path="/history/{id}", carry={"v": "name"}),),
    )

    with pytest.raises(RuntimeError, match="already carries it"):
        await _edge_run(
            [conversations, messages],
            _landed(
                conversations=(
                    ParentRecord(ref="conversations/C1", fields={"id": "C1", "name": "general"}),
                )
            ),
            [],
        )


def _check_a_globally_keyed_root_is_refused() -> None:
    """The bit says whether a record's key needs its parent's scope to be unique."""
    with pytest.raises(ValueError, match="declares no parent"):
        StreamSpec(name="charges", source_object="charges", key_scope="global")


def _check_a_snapshot_stream_declaring_a_bounded_pass_is_refused() -> None:
    edge = ParentEdge(stream="contracts", path="/contracts/{id}/tasks")
    for knob, declaration in (
        ("fetch_budget", {"fetch_budget": 10, "parents": (edge,)}),
        ("pass_interval_seconds", {"pass_interval_seconds": 60, "parents": (edge,)}),
        ("refan", {"parents": (replace(edge, refan="on_parent_change"),)}),
    ):
        with pytest.raises(ValueError, match=f"'tasks' declares delete_missing with {knob}"):
            StreamSpec(name="tasks", source_object="tasks", delete_missing=True, **declaration)
    StreamSpec(name="tasks", source_object="tasks", delete_missing=True, parents=(edge,))


def _check_the_ancestors_of_a_canonical_stream_sync() -> None:
    """A canonical stream whose parent does not sync has no landed records to fan over, so the
    closure up the edges is what a connection registers. Nothing declares it."""
    streams = [
        StreamSpec(name="organizations", source_object="orgs"),
        StreamSpec(
            name="repositories",
            source_object="repos",
            canonical=True,
            parents=(ParentEdge(stream="organizations", path="/orgs/{login}/repos"),),
        ),
        StreamSpec(name="teams", source_object="teams"),
    ]
    assert syncing_streams(streams) == {"organizations", "repositories"}


def _check_a_stream_that_is_its_own_parent_terminates() -> None:
    """Notion's blocks contain blocks: the catalog is a graph, and the closure walks each stream
    once rather than following the self-edge forever."""
    streams = [
        StreamSpec(name="pages", source_object="pages"),
        StreamSpec(
            name="blocks",
            source_object="blocks",
            canonical=True,
            parents=(
                ParentEdge(stream="pages", path="/blocks/{id}/children"),
                ParentEdge(stream="blocks", path="/blocks/{id}/children"),
            ),
        ),
    ]
    assert syncing_streams(streams) == {"pages", "blocks"}


def _check_an_edge_naming_no_declared_stream_raises() -> None:
    streams = [
        StreamSpec(
            name="payslips",
            source_object="payslips",
            canonical=True,
            parents=(ParentEdge(stream="gp_workers", path="/rest/gp/workers/{id}/payslips"),),
        )
    ]
    with pytest.raises(ValueError, match="gp_workers"):
        syncing_streams(streams)


def test_connector_backend_sync_contract() -> None:
    for check in (
        _check_default_render_rejects_record_without_title_or_identity,
        _check_envelope_decoder_rejects_extra_keys,
        _check_a_globally_keyed_root_is_refused,
        _check_a_snapshot_stream_declaring_a_bounded_pass_is_refused,
        _check_a_predicate_on_a_field_the_path_reads_is_refused,
        _check_the_ancestors_of_a_canonical_stream_sync,
        _check_a_stream_that_is_its_own_parent_terminates,
        _check_an_edge_naming_no_declared_stream_raises,
    ):
        check()


async def test_connector_backend_async_contract() -> None:
    for check in (
        _check_an_unusable_grant_skips_the_stream_instead_of_failing_the_run,
        _check_only_the_raiser_decides_that_a_grant_event_is_the_one_repair,
        _check_a_snapshot_stream_refuses_to_pass_over_a_partition,
        _check_a_stream_that_tombstones_nothing_still_passes_over_one,
        _check_a_snapshot_stream_refuses_a_parent_landed_without_its_projection,
        _check_a_stream_that_tombstones_nothing_passes_over_an_unprojected_parent,
        _check_a_snapshot_stream_refuses_an_unready_parent_catalog,
        _check_an_incremental_stream_waits_for_an_unready_parent_catalog,
        _check_a_declared_stream_is_handed_its_reader_without_overriding_the_seam,
        _check_a_root_stream_is_handed_a_reader_of_nothing,
        _check_the_fan_out_closes_its_walk_when_its_consumer_stops_early,
        _check_a_free_text_parent_key_reaches_the_wire_as_one_encoded_segment,
        _check_a_free_text_parent_key_in_a_query_reaches_it_as_a_query_value,
        _check_an_edge_carries_its_parents_fields_onto_every_child_record,
        _check_a_parent_carrying_nothing_for_a_field_writes_nothing,
        _check_a_record_already_carrying_a_carried_field_raises,
        _check_two_edges_to_one_parent_resume_separately,
        _check_an_entry_under_a_key_this_declaration_no_longer_produces_is_re_walked,
        _check_an_edge_that_carries_its_own_query_keeps_it_beside_the_pagers,
        _check_an_optional_edge_passes_over_a_parent_of_another_kind,
        _check_an_edge_hangs_only_under_the_parents_its_predicate_admits,
        _check_a_parent_lacking_the_field_a_predicate_weighs_is_not_a_parent,
        _check_a_recursive_edge_asks_nothing_of_the_blocks_that_contain_none,
        _check_a_globally_keyed_child_is_addressed_by_its_record_alone,
        _check_a_locally_keyed_child_addresses_one_page_per_parent,
        _check_a_rows_pinned_window_reaches_the_connector_beside_the_spec_it_drives,
        _check_cursor_field_supplies_updated_at_when_provider_value_is_absent,
        _check_record_timestamp_fields_resolve_nested_provider_paths,
        _check_opaque_cursor_field_does_not_supply_updated_at,
        _check_large_numeric_cursor_field_does_not_supply_updated_at,
        _check_an_empty_key_is_no_key_so_the_record_is_dropped,
        _check_a_declared_key_resolves_a_nested_provider_id,
        _check_connector_normalizes_integer_timestamps,
        _check_record_fields_cannot_change_an_opaque_cursor,
        _check_resumed_run_drives_from_origin_and_skips_the_prefix,
        _check_uncapped_snapshot_run_keeps_snapshot_semantics,
        _check_connector_json_map_cursor_round_trips_untouched,
        _check_a_connectors_fixed_host_drives_the_run,
        _check_a_row_that_pins_the_address_drives_the_run,
    ):
        await check()
