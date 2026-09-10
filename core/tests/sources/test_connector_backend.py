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
from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import UUID, uuid4

import httpx
import pytest

from ufo.runtime.access.connectors import Credential, GrantUnusable
from ufo.runtime.sources import backend as backend_module
from ufo.runtime.sources.backend import BACKFILL_KEY, ConnectorBackend, ConnectorSourceConfig
from ufo.runtime.sources.connector import Connector, StreamPage, StreamSpec
from ufo.runtime.sources.rest import ProviderRateLimited, RestConnector
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult

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
    consent, an expired refresh token. Every call answers the same way, which is the point: there is
    no attempt count that gets past it."""

    reason = (
        "pipedream cannot authenticate connected account 'apn_1': it is unhealthy, so its grant "
        "needs the member to reconnect the account"
    )

    awaits_grant = False

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        raise GrantUnusable(self.reason, awaits_grant=self.awaits_grant)


async def _check_an_unusable_grant_skips_the_stream_instead_of_failing_the_run() -> None:
    """A grant the broker will not authenticate is a refusal, not a fault. Nothing about it changes
    between two attempts, so failing the run would climb the error backoff and hold a CRITICAL check
    that pages hourly for a repair only the member can make. As a `StreamSkipped` the driver counts
    it, parks the row onto the long interval after the threshold, and records it as a warning — and
    the reason carries the broker's own text, which names the reconnect, plus the stream it stopped.

    Read the whole reason: it is what a member sees on the sources panel and what an operator finds
    in the park log, and a bare 'refused' there would send them looking for a scope."""
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
    """`awaits_grant` reaches the driver exactly as the broker set it. A broker naming one account
    unhealthy sets it, and that feed stops polling until the reconnect. A broker that does not
    recognise the grant leaves it clear, because one broker key rotation makes every account unknown
    at once and the operator who restores that configuration raises no event — a feed held for a
    grant there would wait on a member with nothing to fix. Deciding this here, on a type both
    reach, would collapse the two into whichever guess this line made."""
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


async def test_protected_rate_limit_fails_loud_when_a_connector_yields_it() -> None:
    stream = StreamSpec(name="items", source_object="items", delete_missing=True)

    with pytest.raises(RuntimeError, match="yielded a protected rate limit"):
        await _run(_RateLimitedConnector(stream, []), stream)


async def _check_a_rows_pinned_window_reaches_the_connector_beside_the_spec_it_drives() -> None:
    """The window a row pins arrives as `fetch_page`'s own `backfill_after`, and the `StreamSpec`
    is handed down exactly as the connector declared it. That separation is the point: a spec
    carries only connector constants, so there is no per-run field on it for a connector to
    recompute a floor from, and `backfill_window_days` stays a registration-time input that the run
    path never reads. A row pinning nothing hands down nothing."""
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
    """A record the page model rejects is one record, so it costs one record: it is dropped, the
    warning names it and the field that rejected it, the result counts it, and the run lands its
    siblings and advances the watermark past it. Failing the run instead costs the stream — a failed
    run commits nothing and advances no cursor, so a record the provider keeps returning holds every
    later record behind it every interval."""
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
    assert connector.received_rate_limit_modes == [False]
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
    """A stream reaches memory unless it declares otherwise, and the adapter puts that declaration
    on the run's result — the one place the rows' `indexed` is decided — whether the run landed
    records or none."""
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
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        minimum = int(cursor) if cursor is not None else None
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


def test_connector_backend_sync_contract() -> None:
    for check in (
        _check_default_render_rejects_record_without_title_or_identity,
        _check_envelope_decoder_rejects_extra_keys,
    ):
        check()


async def test_connector_backend_async_contract() -> None:
    for check in (
        _check_an_unusable_grant_skips_the_stream_instead_of_failing_the_run,
        _check_only_the_raiser_decides_that_a_grant_event_is_the_one_repair,
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
