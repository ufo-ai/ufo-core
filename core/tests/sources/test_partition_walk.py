"""`PartitionWalk` — the SDK driver for a stream that fans out over partitions, each with its own
cursor. These drive it with a fake partition source (the connector's enumerator and page factory
stood in for, honoring the `PartitionBound` exactly as a real API would) and assert the `StreamPage`
sequence and the per-partition cursor map it threads. The last test runs a capped newest-first
backfill end-to-end through `ConnectorBackend` and proves it loses no record even when newer rows
are prepended between slices — the descending window resumes by value, never by position."""

import json
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources import backend as backend_module
from ufo.runtime.sources import connector as connector_module
from ufo.runtime.sources.backend import ConnectorBackend, ConnectorSourceConfig
from ufo.runtime.sources.connector import (
    Connector,
    Ordering,
    PartitionBound,
    PartitionSkipped,
    PartitionWalk,
    StreamPage,
    StreamSpec,
    WalkPage,
)
from ufo.runtime.sources.sync import SourceAuth, SyncResult


def _rec(value: str) -> dict[str, Any]:
    return {"id": value, "v": value}


@dataclass
class _FakePartitions:
    """A canned multi-partition source. The factory honors the `PartitionBound` the walk hands it
    exactly as a real API would — an ascending source returns records `> after`, a newest-first
    source returns them newest-first and, on a backfill, `<= before` (inclusive, as the real
    connectors implement it) — and records every bound it
    received so a test can assert the resume position."""

    ordering: Ordering
    data: dict[str, list[str]]
    page_size: int = 2
    seen: list[tuple[str, PartitionBound]] = field(default_factory=list)

    async def partitions(self) -> AsyncIterator[str]:
        for key in self.data:
            yield key

    def pages(self, partition: str, bound: PartitionBound) -> AsyncIterator[WalkPage]:
        return self._pages(partition, bound)

    async def _pages(self, partition: str, bound: PartitionBound) -> AsyncIterator[WalkPage]:
        self.seen.append((partition, bound))
        values = self._bounded(partition, bound)
        for start in range(0, len(values), self.page_size):
            chunk = values[start : start + self.page_size]
            if self.ordering is Ordering.none:
                yield WalkPage(records=[_rec(v) for v in chunk])
            else:
                yield WalkPage(records=[_rec(v) for v in chunk], high=max(chunk), low=min(chunk))

    def _bounded(self, partition: str, bound: PartitionBound) -> list[str]:
        values = self.data[partition]
        if self.ordering is Ordering.ascending:
            return [v for v in sorted(values) if bound.after is None or v > bound.after]
        if self.ordering is Ordering.newest_first:
            newest_first = sorted(values, reverse=True)
            return [
                v
                for v in newest_first
                if (bound.before is None or v <= bound.before)
                and (bound.since is None or v >= bound.since)
            ]
        return list(values)


async def _collect(
    fake: _FakePartitions, cursor: str | None, *, floor: str | None = None
) -> list[StreamPage]:
    return [
        page
        async for page in PartitionWalk(
            ordering=fake.ordering,
            partitions=fake.partitions,
            pages=fake.pages,
            floor=floor,
        ).stream(cursor)
    ]


def _cursors(pages: list[StreamPage]) -> list[Any]:
    return [json.loads(page.next_cursor) if page.next_cursor else None for page in pages]


def _landed(pages: list[StreamPage]) -> list[str]:
    return [record["id"] for page in pages for record in page.records]


async def _check_ascending_checkpoints_running_max_per_page() -> None:
    fake = _FakePartitions(Ordering.ascending, {"p": ["a1", "a2", "a3"]})
    pages = await _collect(fake, None)
    assert _landed(pages) == ["a1", "a2", "a3"]
    assert _cursors(pages) == [{"p": "a2"}, {"p": "a3"}]
    assert fake.seen == [("p", PartitionBound(after=None))]


async def _check_ascending_resumes_from_the_stored_watermark() -> None:
    fake = _FakePartitions(Ordering.ascending, {"p": ["a1", "a2", "a3", "a4"]})
    pages = await _collect(fake, json.dumps({"p": "a3"}))
    assert _landed(pages) == ["a4"]
    assert fake.seen == [("p", PartitionBound(after="a3"))]


async def _check_newest_first_backfill_windows_then_dissolves() -> None:
    fake = _FakePartitions(Ordering.newest_first, {"p": ["n1", "n2", "n3"]})
    pages = await _collect(fake, None)
    assert _landed(pages) == ["n3", "n2", "n1"]
    assert _cursors(pages) == [
        {"p": {"high": "n3", "until": "n2"}},
        {"p": {"high": "n3", "until": "n1"}},
        {"p": "n3"},
    ]
    assert pages[-1].records == []


async def _check_a_floor_stops_a_fresh_backfill_descending_past_it() -> None:
    """Without a floor a fresh partition walks to the beginning of its history, and the fan-out
    multiplies that by the partition count — every message in every channel. The floor rides down
    as `PartitionBound.since` so the factory never fetches below it, and the walk then exhausts
    there and dissolves to its `high` watermark exactly as a whole walk does: the partition moves
    to steady state instead of re-descending every run."""
    fake = _FakePartitions(Ordering.newest_first, {"p": ["n1", "n2", "n3", "n4"]}, page_size=1)
    pages = await _collect(fake, None, floor="n3")

    assert _landed(pages) == ["n4", "n3"]  # n2 and n1 are below the floor and never fetched
    assert fake.seen == [("p", PartitionBound(since="n3"))]
    assert _cursors(pages)[-1] == {"p": "n4"}  # dissolved: steady state next run


async def _check_a_floor_bounds_a_resumed_backfill_too() -> None:
    """A capped run leaves `{high, until}` and the resume descends from `until`. The floor has to
    reach that request as well, or the bound would hold on a first run and be forgotten on every
    instalment after it — which is the case that actually walks the long tail."""
    fake = _FakePartitions(Ordering.newest_first, {"p": ["n1", "n2", "n3", "n4"]}, page_size=1)
    pages = await _collect(fake, json.dumps({"p": {"high": "n4", "until": "n4"}}), floor="n3")

    assert _landed(pages) == ["n4", "n3"]
    assert fake.seen == [("p", PartitionBound(before="n4", since="n3"))]
    assert _cursors(pages)[-1] == {"p": "n4"}


async def _check_a_floor_leaves_steady_state_alone() -> None:
    """Once a partition has dissolved to a watermark it is incremental, and the floor is a bound on
    the FIRST walk only. Passing it as `since` beside `after` would re-assert a backfill bound on
    an incremental pass — and on a row whose floor is older than its watermark that is simply
    wrong."""
    fake = _FakePartitions(Ordering.newest_first, {"p": ["n1", "n2", "n3"]}, page_size=1)
    pages = await _collect(fake, json.dumps({"p": "n2"}), floor="n1")

    assert fake.seen == [("p", PartitionBound(after="n2"))]
    assert _landed(pages) == ["n3", "n2"]


async def _check_a_floor_does_not_reach_an_ascending_walk() -> None:
    """`ascending` climbs from a watermark and `none` re-walks whole; neither descends, so neither
    can overshoot a floor and neither takes one."""
    fake = _FakePartitions(Ordering.ascending, {"p": ["a1", "a2", "a3"]})
    await _collect(fake, None, floor="a2")
    assert fake.seen == [("p", PartitionBound(after=None))]

    plain = _FakePartitions(Ordering.none, {"p": ["x", "y"]})
    await _collect(plain, None, floor="x")
    assert plain.seen == [("p", PartitionBound())]


async def _check_a_factory_that_ignores_the_floor_still_stops_descending() -> None:
    """`since` is a request, and an API that cannot bound server-side answers it with everything.
    The walk stops itself on the first page that reaches the floor and dissolves, so such a factory
    costs one page of overshoot rather than the whole history. Filtering those records is the
    connector's job — `WalkPage` carries `high`/`low`, not per-record values, so the walk cannot do
    it here."""

    async def partitions() -> AsyncIterator[str]:
        yield "p"

    async def unbounded(partition: str, bound: PartitionBound) -> AsyncIterator[WalkPage]:
        for value in ("n4", "n3", "n2", "n1"):
            yield WalkPage(records=[_rec(value)], high=value, low=value)

    pages = [
        page
        async for page in PartitionWalk(
            ordering=Ordering.newest_first, partitions=partitions, pages=unbounded, floor="n3"
        ).stream(None)
    ]

    assert _landed(pages) == ["n4", "n3"]  # stopped at the floor, did not walk to n1
    assert _cursors(pages)[-1] == {"p": "n4"}


async def _check_newest_first_backfill_resumes_downward_from_until() -> None:
    fake = _FakePartitions(Ordering.newest_first, {"p": ["n1", "n2", "n3", "n4"]})
    pages = await _collect(fake, json.dumps({"p": {"high": "n4", "until": "n3"}}))
    assert _landed(pages) == ["n3", "n2", "n1"]
    assert fake.seen == [("p", PartitionBound(before="n3"))]
    assert _cursors(pages)[-1] == {"p": "n4"}


async def _check_newest_first_steady_stops_early_below_the_watermark() -> None:
    """Steady state re-yields the page tying the watermark and stops paging only once a whole page
    sits strictly below it — the tied repeat dedups downstream."""
    fake = _FakePartitions(Ordering.newest_first, {"p": ["n1", "n2", "n3"]}, page_size=1)
    pages = await _collect(fake, json.dumps({"p": "n2"}))
    assert _landed(pages) == ["n3", "n2"]
    assert _cursors(pages)[-1] == {"p": "n3"}
    assert fake.seen == [("p", PartitionBound(after="n2"))]


async def _check_newest_first_steady_lands_a_tied_but_new_record() -> None:
    """A record created at exactly the watermark's value must land: dropping the tying page as
    already-seen would lose it forever, since the watermark never advances past a tie."""

    async def partitions() -> AsyncIterator[str]:
        yield "p"

    async def tied_pages(partition: str, bound: PartitionBound) -> AsyncIterator[WalkPage]:
        assert bound.after == "4"
        yield WalkPage(
            records=[{"id": "4-new", "v": "4"}, {"id": "4-old", "v": "4"}], high="4", low="4"
        )
        yield WalkPage(records=[_rec("3")], high="3", low="3")

    pages = [
        page
        async for page in PartitionWalk(
            ordering=Ordering.newest_first, partitions=partitions, pages=tied_pages
        ).stream(json.dumps({"p": "4"}))
    ]
    assert "4-new" in _landed(pages)
    assert "3" not in _landed(pages)


async def _check_none_marks_boundaries_within_a_pass_but_dissolves_on_completion() -> None:
    fake = _FakePartitions(Ordering.none, {"p1": ["x"], "p2": ["y"]})
    pages = await _collect(fake, None)
    assert _landed(pages) == ["x", "y"]
    assert {"p1": ""} in _cursors(pages)
    assert _cursors(pages)[-1] == {}


async def _check_none_capped_resume_skips_the_partition_already_finished() -> None:
    resume = _FakePartitions(Ordering.none, {"p1": ["x"], "p2": ["y"]})
    await _collect(resume, json.dumps({"p1": ""}))
    assert [partition for partition, _ in resume.seen] == ["p2"]


async def _check_completed_pass_prunes_partitions_no_longer_enumerated() -> None:
    """A dropped partition's watermark dies with the pass that no longer sees it, so a partition
    later recreated under the same name starts from scratch instead of inheriting it."""
    fake = _FakePartitions(Ordering.ascending, {"kept": ["1", "2"]})
    pages = await _collect(fake, json.dumps({"kept": "1", "dropped": "9"}))
    assert _cursors(pages)[-1] == {"kept": "2"}


async def _check_capped_abandonment_closes_the_partition_enumerator() -> None:
    """Abandoning the walk mid-pass — the adapter's cap — unwinds the partitions enumerator
    itself, not only the page factories."""
    closed: list[str] = []

    async def partitions() -> AsyncIterator[str]:
        try:
            yield "p1"
            yield "p2"
        finally:
            closed.append("enumerator")

    fake = _FakePartitions(Ordering.ascending, {"p1": ["1", "2", "3"], "p2": ["4"]})
    walk = PartitionWalk(
        ordering=Ordering.ascending, partitions=partitions, pages=fake.pages
    ).stream(None)
    assert (await anext(walk)).records
    await walk.aclose()
    assert closed == ["enumerator"]


async def _check_partition_skip_mid_backfill_keeps_the_window() -> None:
    """A partition refusal mid-backfill must not read as exhaustion: the walk keeps the
    `{high, until}` window through the skip, so the next run resumes the descent — dissolving to a
    bare watermark there would orphan every record below `until` forever."""
    calls: list[int] = []

    async def partitions() -> AsyncIterator[str]:
        yield "p1"

    async def flaky_pages(partition: str, bound: PartitionBound) -> AsyncIterator[WalkPage]:
        calls.append(1)
        if len(calls) == 1:
            yield WalkPage(records=[_rec("5"), _rec("4")], high="5", low="4")
            raise PartitionSkipped("p1 refused")
        assert bound.before == "4"
        yield WalkPage(records=[_rec("4"), _rec("3")], high="4", low="3")

    first = [
        page
        async for page in PartitionWalk(
            ordering=Ordering.newest_first, partitions=partitions, pages=flaky_pages
        ).stream(None)
    ]
    assert _cursors(first)[-1] == {"p1": {"high": "5", "until": "4"}}
    resumed = [
        page
        async for page in PartitionWalk(
            ordering=Ordering.newest_first, partitions=partitions, pages=flaky_pages
        ).stream(first[-1].next_cursor)
    ]
    assert _cursors(resumed)[-1] == {"p1": "5"}


async def _check_partition_map_decoder_rejects_extra_window_keys() -> None:
    with pytest.raises(RuntimeError, match="malformed partition cursor entry"):
        PartitionWalk._decode(json.dumps({"p": {"high": "a", "until": "b", "junk": 1}}))


def test_partition_map_decoder_reads_plain_watermark_cursors_but_fails_loud_on_corruption() -> None:
    decode = PartitionWalk._decode
    # a cursor that is not a JSON object is a plain watermark another shape wrote — re-walk absorbs
    assert decode("2026-01-01T00:00:00Z") == {}
    assert decode("not json at all") == {}
    # the walk's own map decodes to watermark strings and window entries
    assert decode(json.dumps({"p": "w"})) == {"p": "w"}
    assert decode(json.dumps({"p": {"high": "h", "until": "u"}})) == {
        "p": connector_module._Window(high="h", until="u")
    }
    # a JSON object with a value the walk never writes is corruption — fail loud like its sibling
    with pytest.raises(RuntimeError, match="malformed partition cursor"):
        decode(json.dumps({"p": 5}))
    with pytest.raises(RuntimeError, match="malformed partition cursor"):
        decode(json.dumps({"p": {"high": "h"}}))


class _WalkConnector(Connector):
    """A newest-first connector over one mutable partition, driving `PartitionWalk`. Records are
    `(ref, value)` pairs so a test can tie two refs at one cursor value; the `before` bound is
    honored *inclusively* (`<= before`), modelling the real connectors, so a capped resume
    re-fetches the boundary value and the adapter dedups by `source_ref`. The record list can be
    mutated between runs (a newer value prepended) to prove a capped backfill resumes by value and
    loses nothing."""

    name = "walk"
    base_url = "https://walk.example"

    def __init__(
        self, stream: StreamSpec, records: list[tuple[str, str]], page_size: int = 1
    ) -> None:
        self._stream = stream
        self.records = records
        self.page_size = page_size
        self.factory_closed = False

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
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        async def partitions() -> AsyncIterator[str]:
            yield "p"

        def pages(partition: str, bound: PartitionBound) -> AsyncIterator[WalkPage]:
            return self._pages(bound)

        walk = PartitionWalk(ordering=stream.ordering, partitions=partitions, pages=pages).stream(
            cursor
        )
        try:
            async for page in walk:
                yield page
        finally:
            if isinstance(walk, AsyncGenerator):
                await walk.aclose()

    async def _pages(self, bound: PartitionBound) -> AsyncIterator[WalkPage]:
        try:
            ordered = sorted(self.records, key=lambda record: record[1], reverse=True)
            if bound.before is not None:
                ordered = [record for record in ordered if record[1] <= bound.before]
            for start in range(0, len(ordered), self.page_size):
                chunk = ordered[start : start + self.page_size]
                records = [{"id": ref, "v": value} for ref, value in chunk]
                values = [value for _, value in chunk]
                yield WalkPage(records=records, high=max(values), low=min(values))
        finally:
            self.factory_closed = True


class _NoAuthProxy:
    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(bearer="unused")


async def _fetch(connector: _WalkConnector, cursor: str | None) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_NoAuthProxy())
    return await ConnectorBackend(connector=connector).fetch(
        ConnectorSourceConfig(account="a", stream="feed"), cursor, auth
    )


async def _drive(connector: _WalkConnector, on_landed: Any = None) -> tuple[list[str], str | None]:
    landed: list[str] = []
    cursor: str | None = None
    for _ in range(20):
        result = await _fetch(connector, cursor)
        landed.extend(page.source_ref.split("/")[-1] for page in result.pages)
        if on_landed is not None:
            on_landed(landed)
        if cursor == result.next_cursor:
            break
        cursor = result.next_cursor
    return landed, cursor


async def test_capped_newest_first_backfill_loses_no_record_under_prepend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 2)
    stream = StreamSpec(
        name="feed", source_object="feed", cursor_field="v", ordering=Ordering.newest_first
    )
    values = ["01", "02", "03", "04", "05", "06"]
    connector = _WalkConnector(stream, [(v, v) for v in values], page_size=1)
    prepended: list[bool] = []

    def prepend(landed: list[str]) -> None:
        if not prepended and len(landed) >= 2:
            connector.records.append(("07", "07"))
            prepended.append(True)

    landed, cursor = await _drive(connector, prepend)
    assert set(landed) == {"01", "02", "03", "04", "05", "06", "07"}
    assert json.loads(cursor) == {"p": "07"}


async def test_capped_newest_first_backfill_loses_no_record_at_a_tied_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 3)
    stream = StreamSpec(
        name="feed", source_object="feed", cursor_field="v", ordering=Ordering.newest_first
    )
    # "b" and "c" tie at value "7"; a cap of 3 lands a1/a2/b and splits the tie, leaving "c"
    # unlanded — the inclusive resume boundary must re-reach it rather than skip past it.
    records = [("a1", "9"), ("a2", "9"), ("b", "7"), ("c", "7"), ("d", "5")]
    connector = _WalkConnector(stream, records, page_size=1)
    landed, _ = await _drive(connector)
    assert set(landed) == {"a1", "a2", "b", "c", "d"}


class _NonePartitionsConnector(Connector):
    """An unordered two-partition connector driving `PartitionWalk` — the shape whose only
    checkpoints are partition boundaries."""

    name = "walk"
    base_url = "https://walk.example"

    def __init__(self, stream: StreamSpec, data: dict[str, list[str]]) -> None:
        self._stream = stream
        self.data = data

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
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        async def partitions() -> AsyncIterator[str]:
            for key in self.data:
                yield key

        def pages(partition: str, bound: PartitionBound) -> AsyncIterator[WalkPage]:
            return self._pages(partition)

        walk = PartitionWalk(ordering=stream.ordering, partitions=partitions, pages=pages).stream(
            cursor
        )
        try:
            async for page in walk:
                yield page
        finally:
            if isinstance(walk, AsyncGenerator):
                await walk.aclose()

    async def _pages(self, partition: str) -> AsyncIterator[WalkPage]:
        for ref in self.data[partition]:
            yield WalkPage(records=[{"id": ref}])


async def test_none_partition_larger_than_the_cap_completes_and_unblocks_its_neighbors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `none` partition carries no intra-partition checkpoint, so the cap is soft across it: the
    run overruns to the partition boundary, lands the done-marker, and stops — and the next run
    walks the neighbor instead of restarting the oversized partition forever."""
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 2)
    stream = StreamSpec(name="feed", source_object="feed", ordering=Ordering.none)
    connector = _NonePartitionsConnector(stream, {"big": ["b1", "b2", "b3"], "small": ["s1"]})
    first = await _fetch(connector, None)
    assert {page.source_ref for page in first.pages} == {"feed/b1", "feed/b2", "feed/b3"}
    second = await _fetch(connector, first.next_cursor)
    assert {page.source_ref for page in second.pages} == {"feed/s1"}


async def test_capped_run_closes_the_nested_walk_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 1)
    stream = StreamSpec(
        name="feed", source_object="feed", cursor_field="v", ordering=Ordering.newest_first
    )
    connector = _WalkConnector(stream, [("a", "3"), ("b", "2"), ("c", "1")], page_size=1)
    result = await _fetch(connector, None)
    assert len(result.pages) == 2
    assert connector.factory_closed is True


async def test_partition_walk_in_memory_contract() -> None:
    checks = tuple(value for name, value in globals().items() if name.startswith("_check_"))
    assert len(checks) == 17
    for check in checks:
        await check()
