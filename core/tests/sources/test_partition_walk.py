"""`PartitionWalk` — the SDK driver for a stream that fans out over partitions, each with its own
cursor. These drive it with a fake partition source (the connector's enumerator and page factory
stood in for, honoring the `PartitionBound` exactly as a real API would) and assert the `StreamPage`
sequence and the per-partition cursor map it threads. The last test runs a capped newest-first
backfill end-to-end through `ConnectorBackend` and proves it loses no record even when newer rows
are prepended between slices — the descending window resumes by value, never by position."""

import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources import backend as backend_module
from ufo.runtime.sources import connector as connector_module
from ufo.runtime.sources.backend import ConnectorBackend, ConnectorSourceConfig
from ufo.runtime.sources.connector import (
    DEFAULT_FETCH_BUDGET,
    Connector,
    Ordering,
    ParentEdge,
    ParentRecord,
    Partition,
    PartitionBound,
    PartitionSkipped,
    PartitionWalk,
    Run,
    StreamPage,
    StreamSpec,
    TreeFanOut,
    WalkPage,
    fanned_out,
)
from ufo.runtime.sources.sync import SourceAuth, SyncResult


def _entry(partition: str) -> str:
    """The cursor entry one of the fake's partitions checkpoints under: enumerated by the fake
    itself under no edge, it is a root partition and keyed by its ref alone."""
    return partition


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

    async def partitions(self) -> AsyncIterator[Partition]:
        for key in self.data:
            yield Partition(ref=key, path=f"/{key}")

    def pages(self, partition: Partition, bound: PartitionBound) -> AsyncIterator[WalkPage]:
        return self._pages(partition, bound)

    async def _pages(self, partition: Partition, bound: PartitionBound) -> AsyncIterator[WalkPage]:
        self.seen.append((partition.ref, bound))
        values = self._bounded(partition.ref, bound)
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
    assert _cursors(pages) == [{_entry("p"): "a2"}, {_entry("p"): "a3"}]
    assert fake.seen == [("p", PartitionBound(after=None))]


async def _check_ascending_resumes_from_the_stored_watermark() -> None:
    fake = _FakePartitions(Ordering.ascending, {"p": ["a1", "a2", "a3", "a4"]})
    pages = await _collect(fake, json.dumps({_entry("p"): "a3"}))
    assert _landed(pages) == ["a4"]
    assert fake.seen == [("p", PartitionBound(after="a3"))]


async def _check_newest_first_backfill_windows_then_dissolves() -> None:
    fake = _FakePartitions(Ordering.newest_first, {"p": ["n1", "n2", "n3"]})
    pages = await _collect(fake, None)
    assert _landed(pages) == ["n3", "n2", "n1"]
    assert _cursors(pages) == [
        {_entry("p"): {"high": "n3", "until": "n2"}},
        {_entry("p"): {"high": "n3", "until": "n1"}},
        {_entry("p"): "n3"},
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
    assert _cursors(pages)[-1] == {_entry("p"): "n4"}  # dissolved: steady state next run


async def _check_a_floor_bounds_a_resumed_backfill_too() -> None:
    """A capped run leaves `{high, until}` and the resume descends from `until`. The floor has to
    reach that request as well, or the bound would hold on a first run and be forgotten on every
    instalment after it — which is the case that actually walks the long tail."""
    fake = _FakePartitions(Ordering.newest_first, {"p": ["n1", "n2", "n3", "n4"]}, page_size=1)
    pages = await _collect(
        fake, json.dumps({_entry("p"): {"high": "n4", "until": "n4"}}), floor="n3"
    )

    assert _landed(pages) == ["n4", "n3"]
    assert fake.seen == [("p", PartitionBound(before="n4", since="n3"))]
    assert _cursors(pages)[-1] == {_entry("p"): "n4"}


async def _check_a_floor_leaves_steady_state_alone() -> None:
    """Once a partition has dissolved to a watermark it is incremental, and the floor is a bound on
    the FIRST walk only. Passing it as `since` beside `after` would re-assert a backfill bound on
    an incremental pass — and on a row whose floor is older than its watermark that is simply
    wrong."""
    fake = _FakePartitions(Ordering.newest_first, {"p": ["n1", "n2", "n3"]}, page_size=1)
    pages = await _collect(fake, json.dumps({_entry("p"): "n2"}), floor="n1")

    assert fake.seen == [("p", PartitionBound(after="n2"))]
    assert _landed(pages) == ["n3", "n2"]


async def _check_a_floor_starts_a_fresh_ascending_walk() -> None:
    """An `ascending` walk climbs, so its floor is where a fresh partition starts rather than where
    a descent stops: it rides down as `after`, the same bound the watermark takes, so an endpoint
    that answers `?since` never sends the older history at all."""
    fake = _FakePartitions(Ordering.ascending, {"p": ["a1", "a2", "a3"]})
    pages = await _collect(fake, None, floor="a2")

    assert fake.seen == [("p", PartitionBound(after="a2"))]
    assert _landed(pages) == ["a3"]


async def _check_a_stored_watermark_replaces_an_ascending_floor() -> None:
    """A partition that has synced has already climbed past its own floor, so its watermark is the
    resume point. Sending the floor as well would re-assert a first-pass bound on an incremental
    walk, and on a row whose floor is newer than its watermark it would skip the records between
    them — landed once, then never again."""
    fake = _FakePartitions(Ordering.ascending, {"p": ["a1", "a2", "a3"]})
    pages = await _collect(fake, json.dumps({_entry("p"): "a1"}), floor="a2")

    assert fake.seen == [("p", PartitionBound(after="a1"))]
    assert _landed(pages) == ["a2", "a3"]


async def _check_a_floor_does_not_reach_a_none_walk() -> None:
    """`none` re-walks whole on every completed pass because it has no cursor to filter on. A bound
    it cannot resume from would drop the records below it on every pass, never to land."""
    plain = _FakePartitions(Ordering.none, {"p": ["x", "y"]})
    await _collect(plain, None, floor="x")
    assert plain.seen == [("p", PartitionBound())]


async def _check_a_factory_that_ignores_the_floor_still_stops_descending() -> None:
    """`since` is a request, and an API that cannot bound server-side answers it with everything.
    The walk stops itself on the first page that reaches the floor and dissolves, so such a factory
    costs one page of overshoot rather than the whole history. Filtering those records is the
    connector's job — `WalkPage` carries `high`/`low`, not per-record values, so the walk cannot do
    it here."""

    async def partitions() -> AsyncIterator[Partition]:
        yield Partition(ref="p", path="/p")

    async def unbounded(partition: Partition, bound: PartitionBound) -> AsyncIterator[WalkPage]:
        for value in ("n4", "n3", "n2", "n1"):
            yield WalkPage(records=[_rec(value)], high=value, low=value)

    pages = [
        page
        async for page in PartitionWalk(
            ordering=Ordering.newest_first, partitions=partitions, pages=unbounded, floor="n3"
        ).stream(None)
    ]

    assert _landed(pages) == ["n4", "n3"]  # stopped at the floor, did not walk to n1
    assert _cursors(pages)[-1] == {_entry("p"): "n4"}


async def _check_newest_first_backfill_resumes_downward_from_until() -> None:
    fake = _FakePartitions(Ordering.newest_first, {"p": ["n1", "n2", "n3", "n4"]})
    pages = await _collect(fake, json.dumps({_entry("p"): {"high": "n4", "until": "n3"}}))
    assert _landed(pages) == ["n3", "n2", "n1"]
    assert fake.seen == [("p", PartitionBound(before="n3"))]
    assert _cursors(pages)[-1] == {_entry("p"): "n4"}


async def _check_newest_first_steady_stops_early_below_the_watermark() -> None:
    """Steady state re-yields the page tying the watermark and stops paging only once a whole page
    sits strictly below it — the tied repeat dedups downstream."""
    fake = _FakePartitions(Ordering.newest_first, {"p": ["n1", "n2", "n3"]}, page_size=1)
    pages = await _collect(fake, json.dumps({_entry("p"): "n2"}))
    assert _landed(pages) == ["n3", "n2"]
    assert _cursors(pages)[-1] == {_entry("p"): "n3"}
    assert fake.seen == [("p", PartitionBound(after="n2"))]


async def _check_newest_first_steady_lands_a_tied_but_new_record() -> None:
    """A record created at exactly the watermark's value must land: dropping the tying page as
    already-seen would lose it forever, since the watermark never advances past a tie."""

    async def partitions() -> AsyncIterator[Partition]:
        yield Partition(ref="p", path="/p")

    async def tied_pages(partition: Partition, bound: PartitionBound) -> AsyncIterator[WalkPage]:
        assert bound.after == "4"
        yield WalkPage(
            records=[{"id": "4-new", "v": "4"}, {"id": "4-old", "v": "4"}], high="4", low="4"
        )
        yield WalkPage(records=[_rec("3")], high="3", low="3")

    pages = [
        page
        async for page in PartitionWalk(
            ordering=Ordering.newest_first, partitions=partitions, pages=tied_pages
        ).stream(json.dumps({_entry("p"): "4"}))
    ]
    assert "4-new" in _landed(pages)
    assert "3" not in _landed(pages)


async def _check_none_marks_boundaries_within_a_pass_but_dissolves_on_completion() -> None:
    fake = _FakePartitions(Ordering.none, {"p1": ["x"], "p2": ["y"]})
    pages = await _collect(fake, None)
    assert _landed(pages) == ["x", "y"]
    assert {_entry("p1"): ""} in _cursors(pages)
    assert _cursors(pages)[-1] == {}


async def _check_none_capped_resume_skips_the_partition_already_finished() -> None:
    resume = _FakePartitions(Ordering.none, {"p1": ["x"], "p2": ["y"]})
    await _collect(resume, json.dumps({_entry("p1"): ""}))
    assert [partition for partition, _ in resume.seen] == ["p2"]


async def _check_completed_pass_prunes_partitions_no_longer_enumerated() -> None:
    """A dropped partition's watermark dies with the pass that no longer sees it, so a partition
    later recreated under the same name starts from scratch instead of inheriting it."""
    fake = _FakePartitions(Ordering.ascending, {"kept": ["1", "2"]})
    pages = await _collect(fake, json.dumps({_entry("kept"): "1", "dropped": "9"}))
    assert _cursors(pages)[-1] == {_entry("kept"): "2"}


async def _check_capped_abandonment_closes_the_partition_enumerator() -> None:
    """Abandoning the walk mid-pass — the adapter's cap — unwinds the partitions enumerator
    itself, not only the page factories."""
    closed: list[str] = []

    async def partitions() -> AsyncIterator[Partition]:
        try:
            yield Partition(ref="p1", path="/p1")
            yield Partition(ref="p2", path="/p2")
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

    async def partitions() -> AsyncIterator[Partition]:
        yield Partition(ref="p1", path="/p1")

    async def flaky_pages(partition: Partition, bound: PartitionBound) -> AsyncIterator[WalkPage]:
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
    assert _cursors(first)[-1] == {_entry("p1"): {"high": "5", "until": "4"}}
    resumed = [
        page
        async for page in PartitionWalk(
            ordering=Ordering.newest_first, partitions=partitions, pages=flaky_pages
        ).stream(first[-1].next_cursor)
    ]
    assert _cursors(resumed)[-1] == {_entry("p1"): "5"}


async def _check_partition_map_decoder_rejects_extra_window_keys() -> None:
    with pytest.raises(RuntimeError, match="malformed partition cursor entry"):
        PartitionWalk._decode(json.dumps({_entry("p"): {"high": "a", "until": "b", "junk": 1}}))


def test_partition_map_decoder_reads_plain_watermark_cursors_but_fails_loud_on_corruption() -> None:
    decode = PartitionWalk._decode
    assert decode("2026-01-01T00:00:00Z") == {}
    assert decode("not json at all") == {}
    assert decode(json.dumps({_entry("p"): "w"})) == {_entry("p"): "w"}
    assert decode(json.dumps({_entry("p"): {"high": "h", "until": "u"}})) == {
        _entry("p"): connector_module._Window(high="h", until="u")
    }
    with pytest.raises(RuntimeError, match="malformed partition cursor"):
        decode(json.dumps({_entry("p"): 5}))
    with pytest.raises(RuntimeError, match="malformed partition cursor"):
        decode(json.dumps({_entry("p"): {"high": "h"}}))


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
        yield_rate_limits: bool = True,
        parents: Any = None,
        watched: Any = None,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        async def partitions() -> AsyncIterator[Partition]:
            yield Partition(ref="p", path="/p")

        def pages(partition: Partition, bound: PartitionBound) -> AsyncIterator[WalkPage]:
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
    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(bearer="unused")


async def _fetch(connector: _WalkConnector, cursor: str | None) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_NoAuthProxy())
    return await ConnectorBackend(connector=connector).fetch(
        ConnectorSourceConfig(stream="feed"), cursor, auth
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
    assert json.loads(cursor) == {_entry("p"): "07"}


async def test_capped_newest_first_backfill_loses_no_record_at_a_tied_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 3)
    stream = StreamSpec(
        name="feed", source_object="feed", cursor_field="v", ordering=Ordering.newest_first
    )
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
        yield_rate_limits: bool = True,
        parents: Any = None,
        watched: Any = None,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        async def partitions() -> AsyncIterator[Partition]:
            for key in self.data:
                yield Partition(ref=key, path=f"/{key}")

        def pages(partition: Partition, bound: PartitionBound) -> AsyncIterator[WalkPage]:
            return self._pages(partition.ref)

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
    walks the neighbor instead of restarting the oversized partition forever.

    The stream declares no parent, so the partition it keys its cursor by is not a parent page ref
    and its records are addressed by their key alone."""
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


async def _check_an_edge_reads_its_path_off_each_parent_record() -> None:
    """Where a provider is HATEOAS the child's path is one field of its parent, and where it is not
    the parent's own id fields compose it. Both are one rule: each parent record is one partition,
    keyed by the page ref that addresses it and scoped by the values the path read. The address the
    provider rendered itself rides as it came — it opens the path, where a segment never does."""
    stream = StreamSpec(
        name="workflow_jobs",
        source_object="jobs",
        parents=(ParentEdge(stream="workflow_runs", path="{jobs_url}"),),
    )

    async def parents(name: str) -> AsyncIterator[ParentRecord]:
        assert name == "workflow_runs"
        for run in ("7", "8"):
            yield ParentRecord(
                ref=f"workflow_runs/repositories/1/{run}",
                fields={"jobs_url": f"/repos/acme/ufo/actions/runs/{run}/jobs"},
            )

    assert [
        partition async for partition in TreeFanOut(stream=stream, parents=parents).partitions()
    ] == [
        Partition(
            ref="workflow_runs/repositories/1/7",
            path="/repos/acme/ufo/actions/runs/7/jobs",
            scope="/repos/acme/ufo/actions/runs/7/jobs",
            edge="{jobs_url}",
        ),
        Partition(
            ref="workflow_runs/repositories/1/8",
            path="/repos/acme/ufo/actions/runs/8/jobs",
            scope="/repos/acme/ufo/actions/runs/8/jobs",
            edge="{jobs_url}",
        ),
    ]


async def _check_two_edges_fan_out_over_both_parents() -> None:
    """ClickUp's lists hang under a space and under a folder at different paths; Notion's blocks
    hang under pages and under blocks at one. An edge set is one declaration for either."""
    stream = StreamSpec(
        name="lists",
        source_object="lists",
        parents=(
            ParentEdge(stream="spaces", path="/space/{id}/list"),
            ParentEdge(stream="folders", path="/folder/{id}/list"),
        ),
    )

    async def parents(name: str) -> AsyncIterator[ParentRecord]:
        yield ParentRecord(ref=f"{name}/9", fields={"id": "9"})

    assert [
        (partition.path, partition.scope)
        async for partition in TreeFanOut(stream=stream, parents=parents).partitions()
    ] == [("/space/9/list", "9"), ("/folder/9/list", "9")]


async def _check_a_root_partition_is_keyed_by_its_ref_and_a_fanned_one_by_its_collection() -> None:
    """A connector that enumerates its own partitions under no edge stores the entry a hand-walked
    stream stored before the tree, so the image being replaced keeps matching its map byte for byte.
    A fanned-out partition adds the collection it asks, because two edges to one parent record ask
    two collections of it and one entry between them would read a capped first as both finished."""
    assert Partition(ref="C1", path="/api/conversations.history?channel=C1").key == "C1"
    campaign = ParentRecord(ref="campaigns/c1", fields={"id": "c1"})
    forms = ParentEdge(stream="campaigns", path="/campaigns/{id}/forms")
    emails = ParentEdge(stream="campaigns", path="/campaigns/{id}/emails")
    assert {forms.partition(campaign, "assets").key, emails.partition(campaign, "assets").key} == {
        "campaigns/c1\n/campaigns/c1/forms",
        "campaigns/c1\n/campaigns/c1/emails",
    }


async def _check_a_placeholder_the_parent_does_not_carry_raises() -> None:
    """The failure a flat declaration hides: a path built around a field nobody landed reaches a
    collection that answers nothing, the record cap never trips, and the row just looks quiet. It
    raises here instead, naming the stream, the field and the parent record."""
    stream = StreamSpec(
        name="issues",
        source_object="issues",
        parents=(ParentEdge(stream="repositories", path="/repos/{full_name}/issues"),),
    )

    async def parents(name: str) -> AsyncIterator[ParentRecord]:
        yield ParentRecord(ref="repositories/1", fields={"name": "ufo"})

    with pytest.raises(RuntimeError, match="'issues' reads 'full_name' off 'repositories'"):
        [partition async for partition in TreeFanOut(stream=stream, parents=parents).partitions()]


async def _widths(*highs: str) -> list[StreamPage]:
    async def partitions() -> AsyncIterator[Partition]:
        yield Partition(ref="p", path="/p")

    async def pages(partition: Partition, bound: PartitionBound) -> AsyncIterator[WalkPage]:
        for high in highs:
            yield WalkPage(records=[_rec(high)], high=high, low=high)

    return [
        page
        async for page in PartitionWalk(
            ordering=Ordering.ascending, partitions=partitions, pages=pages
        ).stream(None)
    ]


async def _check_a_partition_numbering_its_watermarks_unpadded_is_refused() -> None:
    """The walk orders every watermark as text, so a provider whose counter gains a digit crosses a
    boundary where `"1000" < "999"`: the running maximum stops climbing, the partition resumes
    behind itself, and what it passed over is never fetched again — no error, no log. The refusal
    names both values, because which two disagree is the whole of what a connector needs to know."""
    with pytest.raises(RuntimeError, match=r"different widths as text: \['1000', '999'\]"):
        await _widths("999", "1000")


async def _check_watermarks_of_one_width_and_of_no_digits_are_left_alone() -> None:
    """A fixed-width counter and an instant both order as text already — the padded epoch ClickUp
    sends and the ISO instant GitHub sends are the two shapes the catalog actually carries."""
    padded = await _widths("0999", "1000")
    assert _cursors(padded)[-1] == {_entry("p"): "1000"}
    instants = await _widths("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")
    assert _cursors(instants)[-1] == {_entry("p"): "2026-02-01T00:00:00Z"}


@dataclass
class _BudgetedPartitions:
    """A source of `count` partitions, each answering one page, recording which it was asked for.
    `watched` names the ones a standing watch pinned, which the connector yields first."""

    count: int
    watched: tuple[str, ...] = ()
    fanned: dict[str, int] = field(default_factory=dict)
    asked: list[str] = field(default_factory=list)
    ordering: Ordering = Ordering.none

    async def partitions(self) -> AsyncIterator[Partition]:
        for name in self.watched:
            yield Partition(ref=name, path=f"/{name}", watched=True)
        for index in range(self.count):
            name = f"p{index:02d}"
            yield Partition(ref=name, path=f"/{name}", fan_revision=self.fanned.get(name))

    async def pages(self, partition: Partition, bound: PartitionBound) -> AsyncIterator[WalkPage]:
        self.asked.append(partition.ref)
        value = partition.ref
        if self.ordering is Ordering.none:
            yield WalkPage(records=[_rec(value)])
        else:
            yield WalkPage(records=[_rec(value)], high=value, low=value)


async def _tick(
    fake: _BudgetedPartitions,
    cursor: str | None,
    *,
    budget: int | None = None,
    interval: int | None = None,
) -> str | None:
    pages = [
        page
        async for page in PartitionWalk(
            ordering=fake.ordering,
            partitions=fake.partitions,
            pages=fake.pages,
            budget=budget,
            pass_interval_seconds=interval,
        ).stream(cursor)
    ]
    return next((page.next_cursor for page in reversed(pages) if page.next_cursor), cursor)


async def _check_a_budget_spreads_one_pass_across_ticks() -> None:
    """Forty partitions at ten fetches a tick: each tick asks exactly ten, none of them twice, and
    the fourth completes the pass and dissolves its markers."""
    fake = _BudgetedPartitions(count=40)
    cursor = None
    for _ in range(4):
        fake.asked.clear()
        cursor = await _tick(fake, cursor, budget=10)
        assert len(fake.asked) == 10
    assert sorted(set(fake.asked)) == [f"p{index:02d}" for index in range(30, 40)]
    assert json.loads(cursor) == {}


async def _check_a_watched_partition_is_fetched_inside_the_budget() -> None:
    """A watched partition goes first and spends the budget like any other, so a budget of ten
    leaves nine for the catalog — an exemption would let watches starve the feed."""
    fake = _BudgetedPartitions(count=40, watched=("w",))
    cursor = await _tick(fake, None, budget=10)
    assert fake.asked[0] == "w"
    assert len(fake.asked) == 10

    fake.asked.clear()
    await _tick(fake, cursor, budget=10)
    assert fake.asked[0] == "w"
    assert fake.asked[1] == "p09"


async def _check_an_ordered_budget_rotates_rather_than_starving_its_tail() -> None:
    """An ordered partition keeps its own watermark and no pass marker, so a budget that always
    started at the head would sync the first partitions forever. The walk resumes the enumeration
    after the one it reached."""
    fake = _BudgetedPartitions(count=4, ordering=Ordering.ascending)
    cursor = await _tick(fake, None, budget=2)
    assert fake.asked == ["p00", "p01"]

    fake.asked.clear()
    cursor = await _tick(fake, cursor, budget=2)
    assert fake.asked == ["p02", "p03"]
    assert json.loads(cursor) == {
        _entry("p00"): "p00",
        _entry("p01"): "p01",
        _entry("p02"): "p02",
        _entry("p03"): "p03",
    }

    fake.asked.clear()
    await _tick(fake, cursor, budget=2)
    assert fake.asked == ["p00", "p01"]


async def _check_a_completed_pass_waits_out_its_interval() -> None:
    """A pass that completed does not open again until the interval has elapsed; the ticks in
    between ask nothing at all."""
    fake = _BudgetedPartitions(count=2)
    cursor = await _tick(fake, None, interval=900)
    assert fake.asked == ["p00", "p01"]

    fake.asked.clear()
    cursor = await _tick(fake, cursor, interval=900)
    assert fake.asked == []

    stale = json.loads(cursor)
    stale[connector_module.PASS_AT_KEY] = (datetime.now(UTC) - timedelta(seconds=1800)).isoformat()
    fake.asked.clear()
    await _tick(fake, json.dumps(stale), interval=900)
    assert fake.asked == ["p00", "p01"]


async def _check_a_watched_partition_ignores_a_closed_interval() -> None:
    """The whole point of a watch: the interval holds the catalog back and the watched partition is
    read every tick regardless."""
    fake = _BudgetedPartitions(count=2, watched=("w",))
    cursor = await _tick(fake, None, interval=900)
    assert fake.asked == ["w", "p00", "p01"]

    fake.asked.clear()
    await _tick(fake, cursor, interval=900)
    assert fake.asked == ["w"]


async def _check_an_interval_does_not_stall_a_pass_the_budget_cut_short() -> None:
    """The interval gates the opening of a pass, not its middle: a pass the budget stopped resumes
    on the next tick however long the interval is."""
    fake = _BudgetedPartitions(count=4)
    cursor = await _tick(fake, None, budget=2, interval=900)
    assert fake.asked == ["p00", "p01"]

    fake.asked.clear()
    await _tick(fake, cursor, budget=2, interval=900)
    assert fake.asked == ["p02", "p03"]


async def _check_refan_asks_only_the_parents_that_moved() -> None:
    """`refan` restores the `1 + changed` request count a hand-rolled descent had: the first pass
    fans every parent and records the revision it reached, and the next asks only the parents whose
    page moved past it."""
    fake = _BudgetedPartitions(count=3, fanned={"p00": 4, "p01": 7, "p02": 5})
    cursor = await _tick(fake, None)
    assert fake.asked == ["p00", "p01", "p02"]
    assert json.loads(cursor) == {connector_module.FANNED_KEY: "7"}

    fake.asked.clear()
    cursor = await _tick(fake, cursor)
    assert fake.asked == []

    fake.fanned["p01"] = 9
    fake.asked.clear()
    await _tick(fake, cursor)
    assert fake.asked == ["p01"]


async def _check_refan_freezes_the_parent_revision_window_across_ticks() -> None:
    fake = _BudgetedPartitions(
        count=4,
        fanned={"p00": 10, "p01": 20, "p02": 30, "p03": 40},
    )
    cursor = await _tick(fake, None, budget=2)
    assert fake.asked == ["p00", "p01"]
    assert json.loads(cursor)[connector_module.PASS_FAN_THROUGH_KEY] == "40"

    fake.fanned["p00"] = 50
    fake.fanned["p02"] = 51
    fake.asked.clear()
    cursor = await _tick(fake, cursor, budget=2)
    assert fake.asked == ["p03"]
    assert json.loads(cursor) == {connector_module.FANNED_KEY: "40"}

    fake.asked.clear()
    cursor = await _tick(fake, cursor, budget=2)
    assert fake.asked == ["p00", "p02"]
    assert json.loads(cursor) == {connector_module.FANNED_KEY: "51"}


@dataclass
class _LandedParents:
    """The parent pages of one stream as the driver reads them back, each with the page revision the
    database assigned it. A child fans over these."""

    records: dict[str, int]
    fields: str = "id"

    def reader(self):
        async def parents(name: str) -> AsyncIterator[ParentRecord]:
            for identity, revision in self.records.items():
                yield ParentRecord(
                    ref=identity,
                    fields={self.fields: identity.rsplit("/", 1)[-1]},
                    revision=revision,
                )

        return parents


@dataclass
class _ChildFetches:
    asked: list[str] = field(default_factory=list)

    async def pages(self, partition: Partition, bound: PartitionBound) -> AsyncIterator[WalkPage]:
        self.asked.append(partition.path)
        yield WalkPage(records=[_rec(partition.path)])


async def _fan(
    stream: StreamSpec,
    landed: _LandedParents,
    child: _ChildFetches,
    cursor: str | None,
    *,
    watch: str | None = None,
) -> str | None:
    """One run of a declared stream through the helper a connector calls, so the edges, the budget,
    the interval and the records the run writes about itself are the shipped ones."""
    run = Run(
        cursor=cursor,
        parents=landed.reader(),
        pinned=None if watch is None else lambda enumerated: [_watch(watch)],
    )
    pages = [page async for page in fanned_out(stream, run, child.pages)]
    return next((page.next_cursor for page in reversed(pages) if page.next_cursor), cursor)


def _watch(path: str) -> Partition:
    return Partition(ref=f"watched{path}", path=path, watched=True)


def _passes(caplog: pytest.LogCaptureFixture) -> list[dict[str, str]]:
    return [record.ufo for record in caplog.records if record.getMessage() == "source_sync.pass"]


_CONVERSATIONS = StreamSpec(
    name="conversations",
    source_object="conversations",
    parents=(ParentEdge(stream="tickets", path="/api/v2/tickets/{id}/conversations"),),
)


async def test_a_completed_pass_records_what_it_spent_and_what_it_enumerated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The run's own account of itself, once per run: a pass that reached the end of its enumeration
    fetched every parent it read, pins nothing, and leaves no partition for the next run to resume
    at. A stream declaring no budget reports none rather than a number nothing bounded."""
    landed = _LandedParents({"tickets/1": 11, "tickets/2": 12, "tickets/3": 13})
    child = _ChildFetches()

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _fan(_CONVERSATIONS, landed, child, None)

    assert _passes(caplog) == [
        {
            "stream": "conversations",
            "outcome": "completed",
            "spent": "3",
            "budget": str(DEFAULT_FETCH_BUDGET),
            "watched": "0",
            "resume_from": "",
            "enumerated": "3",
        }
    ]


async def test_a_budget_cut_short_records_where_the_next_run_picks_up(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A budget stops the pass mid-enumeration, so the record names the partition the next run
    resumes at and shows more parents enumerated than fetched — the reading that tells a bounded
    tick from a budget the catalog under it has outgrown, which reports `truncated` and never
    `completed`."""
    stream = replace(_CONVERSATIONS, fetch_budget=2)
    landed = _LandedParents({"tickets/1": 11, "tickets/2": 12, "tickets/3": 13})
    child = _ChildFetches()

    with caplog.at_level(logging.INFO, logger="ufo"):
        cursor = await _fan(stream, landed, child, None)
        await _fan(stream, landed, child, cursor)

    assert _passes(caplog) == [
        {
            "stream": "conversations",
            "outcome": "truncated",
            "spent": "2",
            "budget": "2",
            "watched": "0",
            "resume_from": "tickets/2\n/api/v2/tickets/2/conversations",
            "enumerated": "3",
        },
        {
            "stream": "conversations",
            "outcome": "completed",
            "spent": "1",
            "budget": "2",
            "watched": "0",
            "resume_from": "",
            "enumerated": "3",
        },
    ]


async def test_a_held_pass_records_the_watch_it_served_and_the_catalog_it_did_not(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A tick inside a closed interval still reads its parents' landed pages and still serves the
    watch, and spends one provider request rather than three. `enumerated` above `spent` is what
    that costs; `held` is what stops it being read as a pass that found nothing."""
    stream = replace(_CONVERSATIONS, pass_interval_seconds=900)
    landed = _LandedParents({"tickets/1": 11, "tickets/2": 12, "tickets/3": 13})
    child = _ChildFetches()

    with caplog.at_level(logging.INFO, logger="ufo"):
        cursor = await _fan(stream, landed, child, None, watch="/api/v2/tickets/9/conversations")
        await _fan(stream, landed, child, cursor, watch="/api/v2/tickets/9/conversations")

    assert _passes(caplog) == [
        {
            "stream": "conversations",
            "outcome": "completed",
            "spent": "4",
            "budget": str(DEFAULT_FETCH_BUDGET),
            "watched": "1",
            "resume_from": "",
            "enumerated": "3",
        },
        {
            "stream": "conversations",
            "outcome": "held",
            "spent": "1",
            "budget": str(DEFAULT_FETCH_BUDGET),
            "watched": "1",
            "resume_from": "",
            "enumerated": "3",
        },
    ]


async def _check_refan_restores_freshdesk_conversations_to_one_plus_changed() -> None:
    """Freshdesk publishes no flat `/conversations`, so the stream fans over every landed ticket and
    a completed pass costs one request per ticket. Freshdesk updates the ticket when a reply lands,
    so the edge declares `refan` and the pass after the first asks only for the tickets that moved
    — which is the `1 + changed` the hand-rolled descent had."""
    stream = StreamSpec(
        name="conversations",
        source_object="conversations",
        parents=(
            ParentEdge(
                stream="tickets",
                path="/api/v2/tickets/{id}/conversations",
                refan="on_parent_change",
            ),
        ),
        key_scope="global",
    )
    landed = _LandedParents({"tickets/1": 11, "tickets/2": 12, "tickets/3": 13})
    child = _ChildFetches()

    cursor = await _fan(stream, landed, child, None)
    assert child.asked == [
        "/api/v2/tickets/1/conversations",
        "/api/v2/tickets/2/conversations",
        "/api/v2/tickets/3/conversations",
    ]

    child.asked.clear()
    landed.records["tickets/2"] = 20
    await _fan(stream, landed, child, cursor)
    assert child.asked == ["/api/v2/tickets/2/conversations"]


async def _check_refan_restores_intercom_conversation_parts_to_one_plus_changed() -> None:
    """Intercom's parts come back only on the conversation itself, and the conversation moves when a
    part lands — the same fact on a different provider, and the same request count."""
    stream = StreamSpec(
        name="conversation_parts",
        source_object="conversation_parts",
        parents=(
            ParentEdge(
                stream="conversations", path="/conversations/{id}", refan="on_parent_change"
            ),
        ),
        key_scope="global",
    )
    landed = _LandedParents({"conversations/a": 4, "conversations/b": 5})
    child = _ChildFetches()

    cursor = await _fan(stream, landed, child, None)
    assert child.asked == ["/conversations/a", "/conversations/b"]

    child.asked.clear()
    await _fan(stream, landed, child, cursor)
    assert child.asked == []

    landed.records["conversations/a"] = 9
    child.asked.clear()
    await _fan(stream, landed, child, cursor)
    assert child.asked == ["/conversations/a"]


async def _check_an_edge_without_refan_fans_every_parent_each_pass() -> None:
    """The default: GitHub does not bump a repository when a comment lands, so an edge assuming it
    would drop that repository's comments. Every completed pass fans every parent."""
    stream = StreamSpec(
        name="conversations",
        source_object="conversations",
        parents=(ParentEdge(stream="tickets", path="/api/v2/tickets/{id}/conversations"),),
    )
    landed = _LandedParents({"tickets/1": 11, "tickets/2": 12})
    child = _ChildFetches()

    cursor = await _fan(stream, landed, child, None)
    child.asked.clear()
    await _fan(stream, landed, child, cursor)

    assert child.asked == [
        "/api/v2/tickets/1/conversations",
        "/api/v2/tickets/2/conversations",
    ]


async def _check_a_stream_declaring_no_budget_takes_the_seams() -> None:
    """No production stream declares `fetch_budget`, so without a default every tree child is
    unbounded in exactly the configuration the RFC calls quadratic. A child over 250 parents asks
    100 a tick, resumes where the budget stopped it, and finishes the pass on the third."""
    stream = StreamSpec(
        name="wide",
        source_object="wide",
        parents=(ParentEdge(stream="root", path="/{id}"),),
    )
    landed = _LandedParents({f"root/{index:03d}": 1 for index in range(250)})
    child = _ChildFetches()
    cursor: str | None = None
    ticks: list[list[str]] = []
    for _ in range(3):
        child.asked.clear()
        cursor = await _fan(stream, landed, child, cursor)
        ticks.append(list(child.asked))

    assert [len(asked) for asked in ticks] == [DEFAULT_FETCH_BUDGET, DEFAULT_FETCH_BUDGET, 50]
    assert sorted(path for asked in ticks for path in asked) == [f"/{i:03d}" for i in range(250)]
    assert json.loads(cursor) == {}


class _BudgetedConnector(Connector):
    """A connector whose stream fans over `count` parents and declares a fetch budget, driven
    through the real `ConnectorBackend` so the count asserted is the count of requests a run makes
    at the transport."""

    name = "budgeted"
    base_url = "https://budgeted.test"

    def __init__(self, stream: StreamSpec, count: int, watched: tuple[str, ...] = ()) -> None:
        self._stream = stream
        self._count = count
        self._watched = watched
        self.asked: list[str] = []

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
        walk = PartitionWalk(
            ordering=stream.ordering,
            partitions=self._partitions,
            pages=self._pages,
            budget=stream.fetch_budget,
            pass_interval_seconds=stream.pass_interval_seconds,
        ).stream(cursor)
        async for page in walk:
            yield page

    async def _partitions(self) -> AsyncIterator[Partition]:
        for name in self._watched:
            yield Partition(ref=name, path=f"/{name}", watched=True)
        for index in range(self._count):
            yield Partition(ref=f"q{index:02d}", path=f"/q{index:02d}")

    async def _pages(self, partition: Partition, bound: PartitionBound) -> AsyncIterator[WalkPage]:
        self.asked.append(partition.path)
        yield WalkPage(records=[_rec(partition.path)])


async def _run(connector: _BudgetedConnector, cursor: str | None) -> SyncResult:
    async def parents(name: str) -> AsyncIterator[ParentRecord]:
        return
        yield

    return await ConnectorBackend(connector=connector).fetch(
        ConnectorSourceConfig(stream=connector.streams()[0].name),
        cursor,
        SourceAuth(workspace_id=uuid4(), auth_proxy=_NoAuthProxy(), parents=parents),
    )


async def test_a_fetch_budget_spends_one_pass_over_four_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forty partitions at ten fetches a tick, counted where the requests are made: each tick asks
    exactly ten and the fourth finishes the pass. `MAX_RECORDS_PER_RUN` counts what a run lands and
    would not have stopped this at all — a partition that answers nothing still costs a request."""
    stream = StreamSpec(
        name="wide",
        source_object="wide",
        parents=(ParentEdge(stream="root", path="/{id}"),),
        fetch_budget=10,
    )
    connector = _BudgetedConnector(stream, count=40)
    cursor: str | None = None
    for tick in range(4):
        connector.asked.clear()
        result = await _run(connector, cursor)
        cursor = result.next_cursor
        assert len(connector.asked) == 10, tick
    assert json.loads(cursor) == {}


async def test_a_watched_partition_is_fetched_first_and_inside_the_budget() -> None:
    """The watch goes first and spends one of the ten, so nine reach the catalog. An exemption would
    let watches starve the feed; priority lets them slow each other down instead."""
    stream = StreamSpec(
        name="wide",
        source_object="wide",
        parents=(ParentEdge(stream="root", path="/{id}"),),
        fetch_budget=10,
    )
    connector = _BudgetedConnector(stream, count=40, watched=("w",))
    result = await _run(connector, None)

    assert connector.asked[0] == "/w"
    assert len(connector.asked) == 10

    connector.asked.clear()
    await _run(connector, result.next_cursor)
    assert connector.asked[0] == "/w"
    assert connector.asked[1] == "/q09"


async def test_a_pass_interval_holds_the_catalog_and_not_the_watch() -> None:
    """A completed pass waits its interval out: the tick after it asks for the watched partition and
    nothing else."""
    stream = StreamSpec(
        name="wide",
        source_object="wide",
        parents=(ParentEdge(stream="root", path="/{id}"),),
        pass_interval_seconds=900,
    )
    connector = _BudgetedConnector(stream, count=3, watched=("w",))
    result = await _run(connector, None)
    assert connector.asked == ["/w", "/q00", "/q01", "/q02"]

    connector.asked.clear()
    await _run(connector, result.next_cursor)
    assert connector.asked == ["/w"]


async def test_partition_walk_in_memory_contract() -> None:
    checks = tuple(value for name, value in globals().items() if name.startswith("_check_"))
    assert len(checks) == 37
    for check in checks:
        await check()
