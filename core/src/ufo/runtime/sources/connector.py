"""What a connector is: the stream vocabulary, the `Connector` ABC every provider implements, and
the render hook that turns a record into a recallable page body.

A connector knows a set of `StreamSpec`s (one per source-side collection it can sync) and, given a
stream plus a `Credential` (the auth-proxy resolves it, so the connector stays agnostic about where
the secret lives) and a base URL, async-yields the provider's records grouped into pages. A page is
a plain `list[dict]` when the source only returns live rows, or a `StreamPage` when a delta/token
source also reports removals (the removed records' external ids) and carries its own resume cursor.
`render` turns one record into `(title, body)` — the default emits the record's JSON under a title
line; a content provider (docs, notion, gmail) overrides it to produce prose. The `ConnectorBackend`
adapter drives one stream to completion per sync run and collapses the pages into the core
`SyncResult` the source seam expects — a full-collection stream (`delete_missing`) becomes an
authoritative snapshot, an incremental stream advances a watermark and names its removals. `Page`
shapes are internal value objects: they never cross a wire, so they are frozen dataclasses, not
`BaseModel`."""

import hashlib
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from ufo.runtime.access.connectors import Credential

TITLE_KEYS = ("title", "name", "full_name", "login", "subject")
MAIL_BACKFILL_WINDOW_DAYS = 30
CHAT_BACKFILL_WINDOW_DAYS = 30
REPO_BACKFILL_WINDOW_DAYS = 30


def get_path(data: Mapping[str, Any], path: str, default: Any = None) -> Any:
    """Read a dotted path from a nested mapping."""
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, Mapping):
            return default
        value = value.get(part)
        if value is None:
            return default
    return value


def record_key(record: Mapping[str, Any], primary_key: str) -> str | None:
    """The immutable provider id a record is addressed by: its stream's declared `primary_key`, read
    as a flat field first and then as a dotted path (`author.id`), so a provider that carries its id
    one level down is declarable. One producer, so the page ref and the default title agree.

    None when the record carries no such value, which drops the record. Keying on the record's
    content instead — a hash of every field — would move the ref whenever any provider field moved,
    minting a new page that derives from scratch and leaving the old page behind on every stream
    that is not a `delete_missing` snapshot."""
    value = record[primary_key] if primary_key in record else get_path(record, primary_key)
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    return str(value) or None


class PaginationStrategy(StrEnum):
    """Named pagination shape a REST stream can declare. Concrete loops live on `RestConnector` —
    one per strategy. Connectors with provider-specific quirks (POST-body cursors, envelope
    unwrapping, fan-out across parent ids) keep overriding `paginate` directly; the strategy is the
    shared escape hatch for the common matrix."""

    next_cursor = "next_cursor"
    next_link = "next_link"
    offset_limit = "offset_limit"
    none = "none"


@dataclass(frozen=True)
class Pagination:
    """Declarative pagination for a `StreamSpec`, run by `RestConnector.paginate_from_strategy`.
    Per-strategy required knobs: `next_cursor` needs `record_path`, `cursor_path`, `cursor_param`;
    `next_link` needs `record_path`; `offset_limit` needs `record_path`, `offset_param`,
    `limit_param`, `page_size`; `none` marks a stream whose connector implements bespoke
    pagination."""

    strategy: PaginationStrategy = PaginationStrategy.none
    path: str | None = None
    record_path: str | None = None
    cursor_path: str | None = None
    cursor_param: str | None = None
    page_size_param: str | None = None
    page_size: int | None = None
    offset_param: str | None = None
    limit_param: str | None = None
    extra_params: Mapping[str, Any] | None = None


class Ordering(StrEnum):
    """How a partitioned stream's records are ordered relative to its `cursor_field`, which decides
    how `PartitionWalk` checkpoints and resumes each partition. `ascending`: the provider returns
    records oldest-first (a `?since` walk), so the running max value is a sound resume watermark —
    everything before it is already seen. `newest_first`: an append-only feed returned newest-first,
    so a first backfill walks a descending `{high, until}` window and resumes downward from `until`
    (loss-free under prepend, because `high` is frozen at the record the backfill started from and
    anything newer is caught by the next steady-state pass), then steady-state stops early once a
    page sits strictly below the synced watermark — a page tying it still yields, so a tied-but-new
    record lands and the repeats dedup downstream. `none`: no `cursor_field`, so only a partition
    boundary is checkpointed — a finished partition is skipped on resume, an in-progress one is
    redone."""

    ascending = "ascending"
    newest_first = "newest_first"
    none = "none"


@dataclass(frozen=True)
class StreamSpec:
    """One stream a connector knows how to sync: its registry `name`, the source-side object, the
    `primary_key` inside each record (the stable id the page is keyed by), and the optional
    `cursor_field` an incremental stream advances a watermark over. `created_at_field` and
    `updated_at_field` are independent provider-record paths projected onto each page; they default
    to the conventional `created_at` and `updated_at` keys. `delete_missing` marks a stream
    whose run enumerates the complete current collection — the adapter returns it as an
    authoritative snapshot so vanished records are tombstoned; left False, the stream is
    incremental and the adapter only upserts and names explicit removals. `ordering` tells
    `PartitionWalk` how a partitioned stream's `cursor_field` is ordered, so a fan-out over
    repos/channels checkpoints and resumes each partition soundly. `pagination` routes a declared
    strategy; None means the connector's `paginate` handles the stream directly.

    `backfill_window_days` is how far back this stream's FIRST sync reaches when the member names no
    window; None declares none, and such a stream takes no override either. It is a declaration and
    nothing more — read once at registration, where it resolves into the instant the row persists.
    Every field here is a connector constant, so the run's own floor is not one: it arrives beside
    the spec as `fetch_page`'s `backfill_after`. Keeping them apart is what stops a connector
    recomputing `now - N days` per run."""

    name: str
    source_object: str
    primary_key: str = "id"
    cursor_field: str | None = None
    created_at_field: str | None = "created_at"
    updated_at_field: str | None = "updated_at"
    delete_missing: bool = False
    canonical: bool = True
    ordering: Ordering = Ordering.none
    pagination: Pagination | None = None
    backfill_window_days: int | None = None


@dataclass(frozen=True)
class StreamPage:
    """One provider page of stream changes. Most connectors yield plain `list[dict]` because the
    source only returns live rows; delta-token and webhook-backed sources yield this richer shape so
    the adapter advances the provider cursor and tombstones removals (named by their source-side
    external ids in `deletes`) through the same run."""

    records: list[dict[str, Any]] = field(default_factory=list)
    deletes: tuple[str, ...] = ()
    next_cursor: str | None = None


@dataclass(frozen=True)
class PartitionBound:
    """Where a partition's page factory resumes, handed down by `PartitionWalk`. `after` is the
    highest `cursor_field` value already synced — fetch strictly newer records (steady-state
    incremental). `before` is the upper bound for continuing a newest-first backfill downward —
    fetch records at or below it: the bound is *inclusive*, so records tied at the boundary value
    (which a capped page split may have left half-landed) are re-fetched rather than dropped, and
    the repeat of the ones already landed is absorbed by the driver's digest-skip. Both None means
    the partition is fresh: walk it whole from the newest record. A connector translates these into
    its own API — a `since`/`oldest` lower bound, an inclusive `until`/`latest` upper bound, or a
    client-side filter for an API that supports neither (and wraps any record filter of its own,
    like GitHub's pull-request exclusion, here too).

    `since` is the pinned floor of a newest-first backfill, in the partition's own `cursor_field`
    value space — the connector renders `backfill_after` into it, since only the connector knows
    whether that space is an epoch string or an ISO instant. It bounds the FIRST walk, never a
    steady-state pass: `PartitionWalk` sets `after` and `since` on different requests."""

    after: str | None = None
    before: str | None = None
    since: str | None = None


@dataclass(frozen=True)
class WalkPage:
    """One page a partition's factory hands `PartitionWalk`: the `records` to land, the highest
    and lowest `cursor_field` values they carry, and any provider-reported `deletes`. `high`/`low`
    are reported by the connector rather than read off the records because a derived record may not
    carry the ordering field itself (a Slack thread row orders by its source message `ts`); they
    drive watermark/window tracking and stop-early, and an unordered (`none`) page leaves them
    None."""

    records: list[dict[str, Any]]
    high: str | None = None
    low: str | None = None
    deletes: tuple[str, ...] = ()


class _Window(BaseModel):
    """One partition's in-flight backfill window — persisted inside the source row's cursor map, so
    a validated model that rejects anything but its two bounds."""

    model_config = ConfigDict(extra="forbid")

    high: str
    until: str


class PartitionSkipped(Exception):
    """A page factory raises this when the provider refuses one partition in a non-data way — a
    repo gone 404 mid-walk, a channel the grant lost. The walk keeps the partition's stored cursor
    state exactly as it stands (a mid-backfill `{high, until}` window survives to resume downward)
    and moves to the next partition; a silent generator end means genuine exhaustion and is the
    only path that dissolves a window."""


Partitions = Callable[[], AsyncIterator[str]]
PageFactory = Callable[[str, PartitionBound], AsyncIterator[WalkPage]]


@dataclass(frozen=True)
class PartitionWalk:
    """Drive a stream that fans out over partitions (a repo, a channel), each carrying its own
    cursor, onto the one-cursor source seam. Owns the per-partition cursor map — a single JSON
    object `{partition: <watermark> | {"high", "until"}}` threaded through `StreamPage.next_cursor`,
    so a capped run resumes without re-reading a finished partition or skipping an unvisited one —
    and the resume state machine per `ordering`; the connector only enumerates `partitions` and
    yields each partition's already-bounded `pages`.

    `ascending`: checkpoint the running max `cursor_field` value after every page; resume re-drives
    the factory with `after` set to that watermark. `newest_first`: a first backfill has no bound,
    walks the partition newest-first, and checkpoints `{high: newest seen, until: oldest seen}`
    after each page; a capped resume re-drives with `before` set to `until` (inclusive) and
    continues the window downward until the walk exhausts, when the entry dissolves to the plain
    `high` watermark — no record is lost even if rows were prepended between slices (since `high` is
    frozen and the window descends by value, never by position) nor when a tie in the cursor value
    straddles a capped page split (the inclusive boundary re-fetches it, and the repeat is deduped).
    Once dissolved, steady-state resumes with `after` set to `high` and stops early once a whole
    page sits strictly below it — a page tying `high` still yields, so a new record sharing the
    watermark's exact value lands and the re-fetched repeats dedup downstream. `none`: checkpoint
    the partition boundary so an in-progress capped pass skips the partitions it already
    finished — but that marker only holds within one pass; when
    the pass runs to completion the entries dissolve, so the next pass re-walks every partition in
    full (a `none` stream has no cursor to filter on, so a full re-walk is how it picks up new and
    changed rows). A pass that runs to completion prunes the map to the partitions it enumerated, so
    a dropped partition's watermark cannot outlive it — though a partition deleted and recreated
    under the same name between two syncs (no completed pass observing the absence) inherits the
    old watermark, the residual trade of name-keyed partitions. The other trade is that a factory
    whose API cannot bound server-side re-fetches the walked prefix each slice.

    `floor` bounds how far back a `newest_first` FIRST walk descends. Without one a fresh partition
    walks to the beginning of its history, times the partition count. With one the descent stops on
    the first page reaching it and dissolves to `high` as an exhausted walk does, so the partition
    moves to steady state rather than re-descending every run; it also rides down as
    `PartitionBound.since`, so a factory that can bound server-side never fetches what the walk
    would discard. `ascending` and `none` ignore it — neither descends."""

    ordering: Ordering
    partitions: Partitions
    pages: PageFactory
    floor: str | None = None

    async def stream(self, cursor: str | None) -> AsyncIterator[StreamPage]:
        stored = self._decode(cursor)
        checkpoint: dict[str, str | _Window] = dict(stored)
        seen: set[str] = set()
        partition_iter = self.partitions()
        try:
            async for partition in partition_iter:
                seen.add(partition)
                page_iter = (
                    self._stream_unordered(partition, stored, checkpoint)
                    if self.ordering is Ordering.none
                    else self._stream_ordered(partition, stored, checkpoint)
                )
                try:
                    async for page in page_iter:
                        yield page
                finally:
                    if isinstance(page_iter, AsyncGenerator):
                        await page_iter.aclose()
        finally:
            if isinstance(partition_iter, AsyncGenerator):
                await partition_iter.aclose()
        completed: dict[str, str | _Window] = (
            {}
            if self.ordering is Ordering.none
            else {key: value for key, value in checkpoint.items() if key in seen}
        )
        if completed != checkpoint:
            yield StreamPage(records=[], next_cursor=self._encode(completed))

    async def _stream_unordered(
        self,
        partition: str,
        stored: dict[str, str | _Window],
        checkpoint: dict[str, str | _Window],
    ) -> AsyncIterator[StreamPage]:
        if partition in stored:
            return
        page_iter = self.pages(partition, PartitionBound())
        try:
            async for page in page_iter:
                yield StreamPage(
                    records=page.records,
                    deletes=page.deletes,
                    next_cursor=self._encode(checkpoint),
                )
        except PartitionSkipped:
            return
        finally:
            if isinstance(page_iter, AsyncGenerator):
                await page_iter.aclose()
        checkpoint[partition] = ""
        yield StreamPage(records=[], next_cursor=self._encode(checkpoint))

    async def _stream_ordered(
        self,
        partition: str,
        stored: dict[str, str | _Window],
        checkpoint: dict[str, str | _Window],
    ) -> AsyncIterator[StreamPage]:
        bound, high, until, synced, backfill = self._ordered_state(stored.get(partition))
        page_iter = self.pages(partition, bound)
        try:
            async for page in page_iter:
                if (
                    self.ordering is Ordering.newest_first
                    and not backfill
                    and synced is not None
                    and page.high is not None
                    and page.high < synced
                ):
                    break
                if page.high is not None and (high is None or page.high > high):
                    high = page.high
                if backfill and page.low is not None and (until is None or page.low < until):
                    until = page.low
                grounded = (
                    backfill
                    and self.floor is not None
                    and until is not None
                    and until <= self.floor
                )
                if backfill and high is not None and until is not None and not grounded:
                    checkpoint[partition] = _Window(high=high, until=until)
                elif not backfill and high is not None:
                    checkpoint[partition] = high
                yield StreamPage(
                    records=page.records,
                    deletes=page.deletes,
                    next_cursor=self._encode(checkpoint),
                )
                if grounded:
                    break
        except PartitionSkipped:
            return
        finally:
            if isinstance(page_iter, AsyncGenerator):
                await page_iter.aclose()
        if backfill and high is not None:
            checkpoint[partition] = high
            yield StreamPage(records=[], next_cursor=self._encode(checkpoint))

    def _ordered_state(
        self, stored: str | _Window | None
    ) -> tuple[PartitionBound, str | None, str | None, str | None, bool]:
        match stored:
            case _Window(high=high, until=until):
                return PartitionBound(before=until, since=self.floor), high, until, None, True
            case str() as synced:
                return PartitionBound(after=synced), synced, None, synced, False
            case _:
                backfill = self.ordering is Ordering.newest_first
                return (
                    PartitionBound(since=self.floor if backfill else None),
                    None,
                    None,
                    None,
                    backfill,
                )

    @staticmethod
    def _decode(cursor: str | None) -> dict[str, str | _Window]:
        """A stored cursor as the per-partition map. A cursor that is not a JSON object at all —
        absent, non-JSON, or a bare JSON scalar — is a plain watermark another cursor shape wrote,
        so it reads as empty and the partitions re-walk. A JSON object is the walk's own map: a
        value that is neither a watermark string nor a `{high, until}` window is corruption the
        walk alone could have written, so it raises rather than silently dropping the
        partition."""
        if not cursor:
            return {}
        try:
            parsed = json.loads(cursor)
        except ValueError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        result: dict[str, str | _Window] = {}
        for key, value in parsed.items():
            match value:
                case str():
                    result[key] = value
                case dict():
                    try:
                        result[key] = _Window.model_validate(value)
                    except ValidationError as error:
                        raise RuntimeError(
                            f"malformed partition cursor entry {key!r}: {value!r}"
                        ) from error
                case _:
                    raise RuntimeError(f"malformed partition cursor entry {key!r}: {value!r}")
        return result

    @staticmethod
    def _encode(partition_map: Mapping[str, "str | _Window"]) -> str:
        raw: dict[str, Any] = {
            key: value.model_dump() if isinstance(value, _Window) else value
            for key, value in partition_map.items()
        }
        return json.dumps(raw, sort_keys=True)


class Connector(ABC):
    """A SaaS data-source connector. `streams` names the streams it can sync; `fetch_page`
    async-yields a stream's records grouped into pages given a resolved `Credential` and base URL;
    `render` turns one record into the `(title, body)` the adapter lands as a page. The runner
    consumes pages in order. Returning a smaller page gives more durable cursor checkpoints at the
    cost of more work.

    A connector declares its API address in one of two shapes. `base_url` alone is a complete fixed
    host every account of the provider shares. `base_url` empty is a per-tenant host — a subdomain,
    a data centre, a company file — carried by the source row, which the registering member's submit
    names and the provider's own URL rule validates."""

    name: ClassVar[str] = ""
    base_url: ClassVar[str] = ""

    @abstractmethod
    def streams(self) -> list[StreamSpec]:
        """The streams this connector can sync."""

    @abstractmethod
    def fetch_page(
        self,
        stream: StreamSpec,
        *,
        cursor: str | None,
        credential: Credential,
        base_url: str,
        self_user_id: str | None,
        backfill_after: datetime | None,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        """Async-yield records from `cursor`, excluding exact `self_user_id` where applicable.

        `backfill_after` is this row's pinned floor, resolved at registration and replayed every
        run. A connector declaring `backfill_window_days` translates it into its provider's own
        floor (a `q=after:` term, a `$filter`) on the request that opens a walk; a resume carries
        its own and needs none. None is unbounded."""

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        """One record as `(title, body)` for recall. The default titles from the first present
        non-empty title-like key or its provider identity and dumps the record's JSON beneath it; a
        content provider overrides this to emit prose (a doc's text, an email's body) so the page
        recalls as readable content."""
        title = next(
            (record[key] for key in TITLE_KEYS if isinstance(record.get(key), str) and record[key]),
            None,
        )
        if title is None:
            identity = self.record_identity(record, stream)
            if identity is None:
                raise ValueError(
                    f"record must supply a non-empty title or {stream.primary_key!r} identity"
                )
            ref = self.record_ref(record, stream) or identity
            title = f"{stream.name}/{ref}"
        return (
            title,
            f"# {self.name} {stream.name}: {title}\n\n{json.dumps(record, sort_keys=True)}",
        )

    def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None:
        """The provider-stable identity for one record, unique within the source."""
        return record_key(record, stream.primary_key)

    def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None:
        """The source-side reference used when a page row is first created."""
        value = record.get(stream.primary_key)
        if isinstance(value, bool):
            return None
        if isinstance(value, (str, int)):
            return str(value)
        return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
