"""What a connector is: the stream vocabulary, the `Connector` ABC every provider implements, and
the render hook that turns a record into a recallable page body.

A connector knows a set of `StreamSpec`s (one per source-side collection it can sync) and, given a
stream plus a `Credential` (the auth-proxy resolves it, so the connector stays agnostic about where
the secret lives) and a base URL, async-yields the provider's records grouped into pages.

The set is a tree, not a list: a stream names the `ParentEdge`s it hangs under, and its partitions
are those parents' landed records — one request per record at the edge's path, whose `{…}`
placeholders are dotted field paths read off the parent. A root collection names no edge. So a
collection a provider publishes only under a parent is a declaration rather than a descent each
connector hand-rolls, `syncing_streams` derives which streams a connection registers (the canonical
ones and the ancestors they fan from), and a child's page is addressed by the values its edge's
path read off the parent — which the provider addresses the collection by, so they are what tells a
record apart from the one of the same key under another parent. A page is
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
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, ClassVar, Literal
from urllib.parse import quote, quote_plus

from pydantic import BaseModel, ConfigDict, ValidationError

from ufo.harness.o11y import log
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


PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


FieldValue = str | int | float | bool


@dataclass(frozen=True)
class ParentRecord:
    """One landed record of a parent stream, as the child fanning out over it reads it: the page ref
    that addresses the record, the record fields its children's edges name, projected when the page
    landed, and the page's `revision`. Values are the provider's own scalars, so a predicate
    compares what the provider sent and a path renders it.

    `revision` is the workspace-wide counter the page row carries, which moves when the body, the
    disclosure or the liveness of the page moves and not when its metadata alone is rewritten — so
    an edge that re-fans only changed parents compares it and a projection backfill does not look
    like a change."""

    ref: str
    fields: Mapping[str, FieldValue]
    revision: int = 0


@dataclass(frozen=True)
class UnprojectedParent:
    """A live page of a parent stream that landed with no projection at all — before any stream
    declared an edge under it, or on an image that wrote none — so its children read nothing off
    it. The reader hands it down rather than passing it over, because what it means depends on the
    child: a stream that asserts nothing about deletions skips it and fans out on the pass after
    the parent re-lands, while a `delete_missing` stream must not sweep against an enumeration it
    is missing from. It is not a `ParentRecord` carrying no fields, because a projection that
    answered none of the fields asked of it is a wrong declaration and raises where it is read."""

    ref: str


@dataclass(frozen=True)
class UnreadyParent:
    """A parent source that has no page proving a completed run and no successful completion of its
    own. An empty completed catalog has no records; this marker is the distinct state before that
    result exists."""


@dataclass(frozen=True)
class Partition:
    """One fan-out target of a stream: the parent page `ref` its records were fanned out from, the
    provider `path` they are read from, the `scope` they are addressed under (None is a collection
    the provider publishes flat, whose records are addressed by their key alone), the fields its
    parent `carried` onto them, and the `edge` it hangs under, which is that edge's path template
    and so tells two edges to one parent apart where the resolved paths cannot.

    `watched` marks a partition a standing watch pinned: it is fetched on every tick, ahead of the
    catalog and through a closed pass interval, it reads the one resource rather than the collection
    its siblings walk, and it spends the fetch budget like any other. `fan_revision` is the revision
    of the parent page this partition was fanned from, carried only where the edge declared
    `refan` — the walk freezes the greatest revision when a pass opens, skips a partition whose
    parent has not moved since the last completed pass, and leaves later revisions for the next
    pass."""

    ref: str
    path: str
    scope: str | None = None
    carried: Mapping[str, FieldValue] = field(default_factory=dict)
    edge: str = ""
    watched: bool = False
    fan_revision: int | None = None

    @property
    def key(self) -> str:
        """The cursor entry this partition checkpoints under. A partition fanned out under an edge
        is keyed by the parent record it came from and the collection it asks of that record — both,
        because neither alone tells two partitions apart: a stream declaring two edges to one parent
        asks two collections of every parent record, and a connector whose provider names the
        collection in a query parameter asks one path of every parent. A root partition, one a
        connector enumerated itself under no edge, is keyed by its ref alone: that is the entry a
        hand-walked stream stored before the tree, so the image being replaced keeps reading and
        writing the map it stored, byte for byte."""
        return f"{self.ref}\n{self.path}" if self.edge else self.ref


@dataclass(frozen=True)
class ParentEdge:
    """One edge up the catalog tree: the stream whose landed records are this stream's partitions,
    and the path one request under one of those records takes. Every `{…}` placeholder in the path
    is a dotted field path read off the parent record, so the parent's own fields compose both the
    request and the address, and there is no mapping beside the path to drift from it.

    `where` and `unless` narrow which of the parent's records this edge hangs under, each a parent
    field to the values it admits or excludes: a coupon is a parent of its unique codes only where
    `coupon_type` is `bulk`, and a block is a parent of further blocks only where `has_children` is
    true and its `type` is none of the ones that contain none. Both are data rather than a callable,
    because an edge is a declaration a gate and a reader can both take in. They are two maps and not
    one because an exclusion is open-ended — the admitted set of a Notion block `type` is every type
    the provider has not shipped yet, which nothing can enumerate, and enumerating it would drop
    each new one silently. A record carrying none of the fields a predicate names is simply not a
    parent of this edge: a coupon with no `coupon_type` is not bulk, a block with no `has_children`
    is a leaf. That is the opposite of a path field, whose absence is a wrong declaration and
    raises.

    `optional` says some of the parent's records are not of the kind this stream hangs under at all:
    a HubSpot owner that is not a user carries no `userId`, and the sequences under a user are not a
    collection it has. Such a record is then no partition of this edge rather than a fault. It is
    off by default because a path field absent from a record that should carry one is a wrong
    declaration, and the whole point of reading the fields off the parent is that it says so.

    `carry` writes fields of the parent onto every child record before the connector shapes it, each
    named by the field the child reads and the parent field path it is read from: a Slack message is
    recalled by the channel it was posted in, and the channel's `name` reaches it as `channel_name`.
    The two names differ often enough that one would not do — the child's name is the connector's,
    the parent's is the provider's. A parent carrying nothing for one writes nothing, as a predicate
    reads nothing there; a record that already carries the field the parent would write raises,
    because overwriting what the provider sent is a thing to say out loud and `flatten` is where a
    connector says it.

    `refan` narrows a completed pass to the parents that moved since the last one, which restores
    the `1 + changed` request count a hand-rolled descent had before the tree. It is sound only
    where the provider bumps the parent whenever a child lands — Freshdesk updates the ticket on a
    reply, Intercom the conversation on a part — so the connector declares it per edge and it
    defaults off: GitHub does not bump a repository when a comment lands, and an edge assuming it
    would drop that repository's comments."""

    stream: str
    path: str
    where: Mapping[str, tuple[FieldValue, ...]] = field(default_factory=dict)
    unless: Mapping[str, tuple[FieldValue, ...]] = field(default_factory=dict)
    carry: Mapping[str, str] = field(default_factory=dict)
    optional: bool = False
    refan: Literal["on_parent_change"] | None = None

    def __post_init__(self) -> None:
        if not self.fields:
            raise ValueError(
                f"edge under {self.stream!r} reads no field of it: {self.path!r} asks one "
                "collection for every parent record"
            )
        narrowed = set(self.where) | set(self.unless)
        overlap = narrowed & set(self.fields)
        if overlap:
            raise ValueError(
                f"edge under {self.stream!r} both addresses and filters on {sorted(overlap)}: a "
                "field the path reads already names one collection"
            )

    @property
    def fields(self) -> tuple[str, ...]:
        """The parent-record field paths this edge's path reads."""
        return tuple(PLACEHOLDER.findall(self.path))

    @property
    def reads(self) -> tuple[str, ...]:
        """Every parent-record field this edge needs: the ones its path renders, the ones its
        predicate weighs, and the ones it writes onto each child record."""
        return (*self.fields, *self.where, *self.unless, *self.carry.values())

    def value(self, parent: ParentRecord, name: str) -> str | None:
        """The parent's value for one field this edge's path reads, as the path renders it, or None
        where the record carries nothing that addresses a collection."""
        value = parent.fields.get(name)
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return None
        return str(value)

    def admits(self, parent: ParentRecord) -> bool:
        """Whether this parent record is one the edge hangs under. See the class docstring."""
        if self.optional and any(self.value(parent, name) is None for name in self.fields):
            return False
        for name, allowed in self.where.items():
            if parent.fields.get(name) not in allowed:
                return False
        return all(
            name not in parent.fields or parent.fields[name] not in excluded
            for name, excluded in self.unless.items()
        )

    def partition(self, parent: ParentRecord, child: str) -> Partition:
        """This edge's fan-out target under one parent record. The scope is the parent's own values
        for the fields the path reads, in path order: the provider addresses the collection by
        exactly those, so they are exactly what tells one record of the child apart from the record
        of the same key under another parent. A placeholder the parent carries no address for
        raises, since a request sent at a half-filled path reaches a collection nobody meant and
        lands nothing, which reads as a quiet stream rather than a broken one.

        A value reaches the wire percent-encoded and the scope raw. A parent key is whatever the
        provider let someone type — a contact's email, a board's title — and a `#` in it would start
        a fragment, a `?` a query and a `/` another segment, so each is encoded for the one place
        that reads them as syntax.

        Where the placeholder sits says how: one inside the path is a segment and encodes `/` with
        the rest, one that BEGINS the path is the address the provider rendered itself — a workflow
        run's `jobs_url`, an issue's `url` — and rides exactly as it came, and one in the query
        encodes the way `parse_qsl` reads it back, which is what the request helper decodes a
        declared query with. The scope stays raw whichever it was, because it addresses a page
        rather than a collection, and a page keyed on an encoding would move the day the encoding
        did."""
        address, mark, query = self.path.partition("?")
        values: list[str] = []
        for name in self.fields:
            value = self.value(parent, name)
            if value is None:
                raise RuntimeError(
                    f"stream {child!r} reads {name!r} off {self.stream!r} record {parent.ref!r}, "
                    "which carries no value that addresses a collection"
                )
            values.append(value)
            rendered = value if self.path.startswith(f"{{{name}}}") else quote(value, safe="")
            address = address.replace(f"{{{name}}}", rendered)
            query = query.replace(f"{{{name}}}", quote_plus(value))
        path = f"{address}?{query}" if mark else address
        return Partition(
            ref=parent.ref,
            path=path,
            scope="/".join(values),
            edge=self.path,
            carried={
                target: parent.fields[source]
                for target, source in self.carry.items()
                if source in parent.fields
            },
            fan_revision=parent.revision if self.refan else None,
        )


@dataclass(frozen=True)
class StreamSpec:
    """One stream a connector knows how to sync: its registry `name`, the source-side object, the
    `primary_key` inside each record (the stable id the page is keyed by), and the optional
    `cursor_field` the connector uses to compute its checkpoint. `created_at_field` and
    `updated_at_field` are independent provider-record paths projected onto each page; they default
    to the conventional `created_at` and `updated_at` keys. `delete_missing` marks a stream
    whose run enumerates the complete current collection — the adapter returns it as an
    authoritative snapshot so vanished records are tombstoned; left False, the stream is
    incremental and the adapter only upserts and names explicit removals. `ordering` tells
    `PartitionWalk` how a partitioned stream's `cursor_field` is ordered, so a fan-out over
    repos/channels checkpoints and resumes each partition soundly. `pagination` routes a declared
    strategy; None means the connector's `paginate` handles the stream directly.

    `parents` are the edges this stream hangs under: its partitions are those parents' landed
    records, one request per record at the edge's path. An empty tuple is a root collection the
    provider publishes flat. A stream reached under more than one parent names each edge, so a
    catalog that is a tree in most connectors and a graph in a few (a stream that is its own parent,
    reaching one level deeper per pass) is the one declaration either way.

    `canonical` marks a stream as the content this account exists to carry — a core collection
    (issues, messages, invoices, candidates), never a lookup list a join would want (custom-field
    definitions, pipelines, tags, users). Connecting an account syncs its connector's canonical
    streams and the ancestors those fan out from (`syncing_streams`), so this field is the whole of
    what a connection reads for. It defaults to False, exactly as every provider helper defaults it,
    because a stream nobody has judged is not content; a connector that marks none would sync
    nothing, which `gates.py` refuses.

    `backfill_window_days` is how far back this stream's FIRST sync reaches when the connection
    names no window; None declares none, and such a stream takes no override either. It is a
    declaration and nothing more — read once at registration, where it resolves into the instant the
    row persists.
    Every field here is a connector constant, so the run's own floor is not one: it arrives beside
    the spec as `fetch_page`'s `backfill_after`. Keeping them apart is what stops a connector
    recomputing `now - N days` per run.

    `indexed` is whether this stream's pages reach memory: left False, its pages still land — source
    triggers and `object_get` read them — but no chunks and no facts are derived from them.

    `fetch_budget` is the most partitions one run of this row may fetch — None takes the seam's
    `DEFAULT_FETCH_BUDGET`, and a connector declares a number only where its provider's limit
    demands one — and `pass_interval_seconds` is how long a completed pass waits before the next
    one opens. Both are per row and not per
    connection: rows are claimed and run independently, so a connection-wide counter would be shared
    state the driver does not have, while a row already owns its cursor map, its `Ordering` and its
    `next_sync_at`. A connection's spend is the sum of its rows' declarations, which an operator
    reads off them. `MAX_RECORDS_PER_RUN` bounds what a run lands and never trips on pages that land
    nothing, which is the shape a fan-out over many quiet partitions has; these two bound what it
    asks. A watched partition is fetched inside the budget and through a closed interval. A
    `delete_missing` stream declares neither, nor a `refan` edge: its run is the authoritative
    enumeration the driver tombstones every unmentioned page against, so a pass that visited some
    partitions, or none, would sweep the pages of every parent it did not visit — a snapshot fan-out
    is unbounded and whole, and the declaration raises on the combination.

    `key_scope` is the provider's promise about `primary_key`: `global` where a record's key is
    unique across the account (a Stripe charge, an Airtable record, a Sentry issue), `local` where
    the provider only promises it unique inside the parent — a Teams `chatMessage.id` is documented
    as repeatable in another chat or channel, and a Drive permission id repeats on every file. A
    `local` key is addressed under the scope its edge's path read, which cannot collide; a `global`
    one is addressed by the record alone. `local` is the default because it is the answer that is
    never wrong: a scope on a key that did not need one costs a longer address, where a missing one
    silently merges two records onto a page that rewrites itself every sync. The bit says nothing on
    a stream that declares no parent, which has no scope to leave out, so declaring it there
    raises."""

    name: str
    source_object: str
    primary_key: str = "id"
    cursor_field: str | None = None
    created_at_field: str | None = "created_at"
    updated_at_field: str | None = "updated_at"
    delete_missing: bool = False
    canonical: bool = False
    ordering: Ordering = Ordering.none
    pagination: Pagination | None = None
    backfill_window_days: int | None = None
    indexed: bool = True
    parents: tuple[ParentEdge, ...] = ()
    key_scope: Literal["local", "global"] = "local"
    fetch_budget: int | None = None
    pass_interval_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.key_scope == "global" and not self.parents:
            raise ValueError(f"stream {self.name!r} declares no parent and so has no key scope")
        if self.fetch_budget is not None and self.fetch_budget < 1:
            raise ValueError(f"stream {self.name!r} declares a fetch budget of {self.fetch_budget}")
        if not self.delete_missing:
            return
        bounded = [
            knob
            for knob, declared in (
                ("fetch_budget", self.fetch_budget is not None),
                ("pass_interval_seconds", self.pass_interval_seconds is not None),
                ("refan", any(edge.refan is not None for edge in self.parents)),
            )
            if declared
        ]
        if bounded:
            raise ValueError(
                f"stream {self.name!r} declares delete_missing with {', '.join(bounded)}: a "
                "snapshot enumerates every partition on every run, and a pass bounded that way "
                "would sweep the pages of the parents it did not visit"
            )


def syncing_streams(streams: Sequence[StreamSpec]) -> frozenset[str]:
    """The streams a connection registers a row for: the canonical ones, and every ancestor one of
    those fans from. An ancestor is not content — GitHub's `organizations` is the root its
    repositories hang under — but its landed records are the partitions of the stream that is, so a
    canonical stream whose parent does not sync has nothing to fan over. It is the transitive
    closure of `canonical` up the edges and nothing declares it. Whether an ancestor's pages reach
    memory is its own `indexed` declaration, a separate question already answered.

    An edge naming a stream the connector does not declare raises here, which is registration."""
    by_name = {stream.name: stream for stream in streams}
    syncing: set[str] = set()
    pending = [stream.name for stream in streams if stream.canonical]
    while pending:
        name = pending.pop()
        if name in syncing:
            continue
        syncing.add(name)
        parent = by_name.get(name)
        if parent is None:
            raise ValueError(f"stream {name!r} is named as a parent but is not declared")
        pending.extend(edge.stream for edge in parent.parents)
    return frozenset(syncing)


@dataclass(frozen=True)
class StreamPage:
    """Records, removals, and an opaque provider checkpoint. The adapter stores `next_cursor`
    without interpreting record fields. A connector emits a checkpoint only after it has yielded
    every record in the connector-defined read that the checkpoint completes.

    `scope` is what the partition these records were fanned out from addresses them under, which the
    adapter makes part of the page's address: a key unique only inside one partition (a branch
    `name`, a commit `sha`) addresses one page per partition rather than colliding onto one. None is
    a flat collection, whose records are addressed by their key alone."""

    records: list[dict[str, Any]] = field(default_factory=list)
    deletes: tuple[str, ...] = ()
    next_cursor: str | None = None
    scope: str | None = None


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


@dataclass(frozen=True)
class PassOutcome:
    """What one run of a tree stream did with the declarations that bound it: whether the pass ran
    to the end of the enumeration, was cut short by the fetch budget, or was `held` behind a pass
    interval that has not elapsed; how many partitions it fetched of the cap it was given, how many
    of those a watch pinned, and the partition the next run resumes the enumeration at.

    `spent` counts partitions fetched and `enumerated` counts parent records read to find them, so
    the two apart say where the run stopped: a `truncated` run enumerated past what it fetched."""

    outcome: Literal["completed", "truncated", "held"]
    spent: int
    watched: int
    budget: int | None
    resume_from: str | None


FanOutReport = Callable[[Mapping[str, int], Mapping[str, int], PassOutcome], None]
ParentPages = Callable[[str], AsyncIterator[ParentRecord | UnprojectedParent | UnreadyParent]]


async def no_parents(stream: str) -> AsyncIterator[ParentRecord]:
    """The reader a stream with no edges is driven with. A root collection hangs under nothing, so
    there is nothing landed for it to enumerate — and a reader that reads nothing is one answer,
    where a reader that may be absent is two and leaves every connector to say what absent means."""
    return
    yield


"""Reads the landed pages of one named parent stream: a record for each page carrying its
projection, an `UnprojectedParent` for each live page carrying none, or an `UnreadyParent` when the
source has neither a page nor a completed run. An `ascending` or `newest_first` parent never
re-lands its history, so an edge declared under one later reaches only the parents landed after the
declaration; the unit adding such an edge ships the parent's refetch with it."""
WatchedResources = Callable[[], Awaitable[tuple[str, ...]]]
"""Reads the resources a standing watch on this connection pins, as the provider's own URLs. The
strings are opaque to core: the extension holding the watches answers them and the connector reading
them owns what they name, so a partition a member asked to be told about is visited every tick
whatever the catalog walk is doing. Awaited by the connectors that pin partitions and by nothing
else, so a provider with no watch shape spends no read."""
Partitions = Callable[[], AsyncIterator[Partition]]
PageFactory = Callable[[Partition, PartitionBound], AsyncIterator[WalkPage]]


@dataclass
class TreeFanOut:
    """Every fan-out target of one declared stream, and what each of its edges cost.

    One request per parent record, and the catalog walk that produced those records belongs to the
    parent's own row rather than being re-derived here. The tally is what makes a quiet stream
    readable, which the shape of this seam otherwise hides twice over: `enumerated` above zero with
    `admitted` at zero is a `where` naming a field the parent kind never carries, which admits
    nothing of a full parent set and reads exactly like a set legitimately all filtered out; and
    `fetched` above zero with `landed` at zero across a pass is the wrong-path class the connector
    audit found eight of, where the request is answered, the records are dropped by a record path
    that does not match, and nothing counts the loss."""

    stream: StreamSpec
    parents: ParentPages
    enumerated: dict[str, int] = field(default_factory=dict)
    admitted: dict[str, int] = field(default_factory=dict)

    async def partitions(self) -> AsyncIterator[Partition]:
        for edge in self.stream.parents:
            self.enumerated.setdefault(edge.path, 0)
            self.admitted.setdefault(edge.path, 0)
            records = self.parents(edge.stream)
            try:
                async for parent in records:
                    match parent:
                        case ParentRecord():
                            self.enumerated[edge.path] += 1
                            if edge.admits(parent):
                                self.admitted[edge.path] += 1
                                yield edge.partition(parent, self.stream.name)
                        case UnprojectedParent() | UnreadyParent():
                            continue
            finally:
                if isinstance(records, AsyncGenerator):
                    await records.aclose()

    def report(
        self, fetched: Mapping[str, int], landed: Mapping[str, int], outcome: PassOutcome
    ) -> None:
        """One record per edge of the run that just ended, and one for the run itself. See the
        class docstring.

        A stream whose partitions outnumber its budget reports `truncated` on every run and
        `completed` on none, which is the one reading that says a budget is too small for the
        catalog under it rather than merely bounding a tick."""
        for edge in self.stream.parents:
            log(
                "source_sync.fan_out",
                stream=self.stream.name,
                parent=edge.stream,
                edge=edge.path,
                enumerated=str(self.enumerated.get(edge.path, 0)),
                admitted=str(self.admitted.get(edge.path, 0)),
                fetched=str(fetched.get(edge.path, 0)),
                landed=str(landed.get(edge.path, 0)),
            )
        log(
            "source_sync.pass",
            stream=self.stream.name,
            outcome=outcome.outcome,
            spent=str(outcome.spent),
            budget="" if outcome.budget is None else str(outcome.budget),
            watched=str(outcome.watched),
            resume_from=outcome.resume_from or "",
            enumerated=str(sum(self.enumerated.values())),
        )


DEFAULT_FETCH_BUDGET = 100
"""Partitions one run of a tree stream fetches where its declaration names no budget. One request
per partition per tick is at most 100 requests a minute per row, under the lowest documented limit
of the converted providers that declare none of their own — Zendesk 200/min on its Team plan,
HubSpot 100 per 10 s per private app, Intercom 10,000/min — and over GitHub's 5,000 an hour
(83/min) per installation, which a github row must declare down from."""
PASS_AT_KEY = "ufo_pass_at"
PASS_FROM_KEY = "ufo_pass_from"
FANNED_KEY = "ufo_fanned"
PASS_FAN_THROUGH_KEY = "ufo_pass_fan_through"
RESERVED_KEYS = (PASS_AT_KEY, PASS_FROM_KEY, FANNED_KEY, PASS_FAN_THROUGH_KEY)
"""The walk's own entries in the cursor map: when its last pass completed, where a budget stopped
it, the parent revision the last pass fanned through, and the fixed revision ceiling of a pass in
flight. They sit in the map rather than in a column because the map is the one durable object the
walk owns and already threads through `StreamPage.next_cursor`, and they cannot collide with a
partition: a fanned-out partition's key joins a ref and a path with a newline, and a root
partition's is the provider's own id, which no provider spells `ufo_`."""


@dataclass(frozen=True)
class _PassMarks:
    """The walk's own state beside the partitions: when its last pass completed, where a budget
    stopped it, the completed parent revision, and an open pass's fixed revision ceiling. Read out
    of the cursor map at the top of a run and written back at the bottom."""

    completed_at: datetime | None = None
    resume_from: str | None = None
    fanned: int | None = None
    fan_through: int | None = None

    @classmethod
    def take(cls, stored: dict[str, str | _Window]) -> "_PassMarks":
        """Lift the reserved entries out of the decoded map, leaving it the partitions alone. A
        reserved entry the walk did not write — a stamp another shape left, a number that is not
        one — raises, because the walk is its only writer and reading it wrong would restart a pass
        that is mid-flight or skip one that is due."""
        marked: dict[str, str | None] = {}
        for key in RESERVED_KEYS:
            value = stored.pop(key, None)
            if value is not None and not isinstance(value, str):
                raise RuntimeError(f"malformed pass marker {key!r}: {value!r}")
            marked[key] = value
        stamp = marked[PASS_AT_KEY]
        reached = marked[PASS_FROM_KEY]
        fanned = marked[FANNED_KEY]
        fan_through = marked[PASS_FAN_THROUGH_KEY]
        try:
            completed_at = None if stamp is None else datetime.fromisoformat(stamp)
        except ValueError as error:
            raise RuntimeError(f"malformed pass marker {PASS_AT_KEY!r}: {stamp!r}") from error
        try:
            count = None if fanned is None else int(fanned)
        except ValueError as error:
            raise RuntimeError(f"malformed pass marker {FANNED_KEY!r}: {fanned!r}") from error
        try:
            through = None if fan_through is None else int(fan_through)
        except ValueError as error:
            raise RuntimeError(
                f"malformed pass marker {PASS_FAN_THROUGH_KEY!r}: {fan_through!r}"
            ) from error
        return cls(
            completed_at=completed_at,
            resume_from=reached,
            fanned=count,
            fan_through=through,
        )

    def opens(self, interval_seconds: int | None) -> bool:
        """Whether a pass may run this tick: one is already in flight, or the last one completed
        long enough ago. A watched partition is read either way."""
        if interval_seconds is None or self.completed_at is None or self.resume_from is not None:
            return True
        return datetime.now(UTC) - self.completed_at >= timedelta(seconds=interval_seconds)

    def truncated(self, reached: str | None, fan_through: int | None) -> "_PassMarks":
        return _PassMarks(
            completed_at=self.completed_at,
            resume_from=reached,
            fanned=self.fanned,
            fan_through=fan_through,
        )

    def completed(self, fanned: int | None, interval_seconds: int | None) -> "_PassMarks":
        """The marks a finished pass leaves. A stream declaring no interval keeps no instant and a
        stream whose edges declare no `refan` keeps no revision, so a row that uses neither writes
        the cursor it always wrote."""
        return _PassMarks(
            completed_at=None if interval_seconds is None else datetime.now(UTC),
            resume_from=None,
            fanned=fanned,
            fan_through=None,
        )

    def entries(self) -> dict[str, str]:
        stamped = {
            PASS_AT_KEY: None if self.completed_at is None else self.completed_at.isoformat(),
            PASS_FROM_KEY: self.resume_from,
            FANNED_KEY: None if self.fanned is None else str(self.fanned),
            PASS_FAN_THROUGH_KEY: (None if self.fan_through is None else str(self.fan_through)),
        }
        return {key: value for key, value in stamped.items() if value is not None}


@dataclass(frozen=True)
class PartitionWalk:
    """Drive a stream that fans out over partitions — its parent stream's landed records, each
    carrying its own cursor — onto the one-cursor source seam. Owns the per-partition cursor map — a
    single JSON object `{partition key: <watermark> | {"high", "until"}}` threaded through
    `StreamPage.next_cursor`,
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
    would discard. An `ascending` walk takes it the other way round: it climbs, so the floor is
    where a fresh partition starts, riding down as `PartitionBound.after` — the same road the stored
    watermark takes, and a stored watermark replaces it, since a partition already synced has
    climbed past its own floor. `none` ignores it: a stream with no cursor to filter on re-walks
    whole, and a bound it cannot resume from would drop records it never lands again."""

    ordering: Ordering
    partitions: Partitions
    pages: PageFactory
    floor: str | None = None
    report: FanOutReport | None = None
    budget: int | None = None
    pass_interval_seconds: int | None = None

    async def stream(self, cursor: str | None) -> AsyncGenerator[StreamPage, None]:
        stored = self._decode(cursor)
        marks = _PassMarks.take(stored)
        checkpoint: dict[str, str | _Window] = {**stored, **marks.entries()}
        open_pass = marks.opens(self.pass_interval_seconds)
        fetched: dict[str, int] = {}
        landed: dict[str, int] = {}
        spent = 0
        watched = 0
        reached: str | None = None
        truncated = False
        partition_iter = self.partitions()
        try:
            enumerated = sorted(
                [partition async for partition in partition_iter],
                key=lambda partition: (not partition.watched, partition.key),
            )
        finally:
            if isinstance(partition_iter, AsyncGenerator):
                await partition_iter.aclose()
        active = {partition.key for partition in enumerated}
        fan_through = self._pass_ceiling(marks, enumerated, open_pass)
        for partition in enumerated:
            if not partition.watched:
                if not open_pass:
                    continue
                if marks.resume_from is not None and partition.key <= marks.resume_from:
                    continue
                if not self._within_refan_window(partition, marks, fan_through):
                    continue
                if self.ordering is Ordering.none and partition.key in stored:
                    continue
            if self.budget is not None and spent >= self.budget:
                truncated = True
                break
            fetched[partition.edge] = fetched.get(partition.edge, 0) + 1
            spent += 1
            if partition.watched:
                watched += 1
            else:
                reached = partition.key
            page_iter = (
                self._stream_unordered(partition, checkpoint)
                if self.ordering is Ordering.none
                else self._stream_ordered(partition, stored, checkpoint)
            )
            try:
                async for page in page_iter:
                    landed[partition.edge] = landed.get(partition.edge, 0) + len(page.records)
                    yield page
            finally:
                if isinstance(page_iter, AsyncGenerator):
                    await page_iter.aclose()
        if self.report is not None:
            held = not open_pass
            self.report(
                fetched,
                landed,
                PassOutcome(
                    outcome="held" if held else "truncated" if truncated else "completed",
                    spent=spent,
                    watched=watched,
                    budget=self.budget,
                    resume_from=marks.resume_from if held else reached if truncated else None,
                ),
            )
        completed = self._pass_state(
            marks,
            checkpoint,
            active,
            open_pass,
            truncated,
            reached,
            fan_through,
        )
        if completed != self._encode(checkpoint):
            yield StreamPage(records=[], next_cursor=completed)

    def _pass_state(
        self,
        marks: "_PassMarks",
        checkpoint: dict[str, str | _Window],
        active: set[str],
        open_pass: bool,
        truncated: bool,
        reached: str | None,
        fan_through: int | None,
    ) -> str:
        """The cursor this run leaves. A pass the budget cut short keeps every partition marker it
        stamped and the partition it reached, so the next run picks the enumeration up after it
        rather than spending the budget on the head again — after it in key order, which the walk
        imposes on every enumeration: the next run skips every key at or below the mark, so a parent
        deleted between the two ticks costs nothing and one inserted below the mark waits for the
        pass after, where a resume that looked for the exact key would find it gone, drop the tail
        and close the pass early. A pass that ran to the end of the
        enumeration dissolves its markers, records the instant it completed and the parent revision
        ceiling fixed when it opened, and prunes the map to the partitions it enumerated — and a
        run that only served watched partitions through a closed interval leaves all of it alone.

        The marks ride inside the working map so that every page's own checkpoint carries them:
        the adapter stores the last cursor a run yields, and a watched partition yielding the last
        page of a closed-interval tick would otherwise drop the instant that closed it."""
        if not open_pass:
            return self._encode(checkpoint)
        partitions = {key: value for key, value in checkpoint.items() if key not in RESERVED_KEYS}
        if truncated:
            return self._encode({**partitions, **marks.truncated(reached, fan_through).entries()})
        pruned: dict[str, str | _Window] = (
            {}
            if self.ordering is Ordering.none
            else {key: value for key, value in partitions.items() if key in active}
        )
        return self._encode(
            {**pruned, **marks.completed(fan_through, self.pass_interval_seconds).entries()}
        )

    @staticmethod
    def _pass_ceiling(
        marks: _PassMarks, enumerated: Sequence[Partition], open_pass: bool
    ) -> int | None:
        if not open_pass or marks.fan_through is not None:
            return marks.fan_through
        revisions = [
            partition.fan_revision
            for partition in enumerated
            if not partition.watched and partition.fan_revision is not None
        ]
        return max([marks.fanned or 0, *revisions]) if revisions else marks.fanned

    @staticmethod
    def _within_refan_window(
        partition: Partition, marks: _PassMarks, fan_through: int | None
    ) -> bool:
        revision = partition.fan_revision
        if revision is None:
            return True
        if marks.fanned is not None and revision <= marks.fanned:
            return False
        return fan_through is None or revision <= fan_through

    async def _stream_unordered(
        self,
        partition: Partition,
        checkpoint: dict[str, str | _Window],
    ) -> AsyncIterator[StreamPage]:
        page_iter = self.pages(partition, PartitionBound())
        try:
            async for page in page_iter:
                yield StreamPage(
                    records=self._carried(page, partition),
                    deletes=page.deletes,
                    next_cursor=self._encode(checkpoint),
                    scope=partition.scope,
                )
        except PartitionSkipped:
            return
        finally:
            if isinstance(page_iter, AsyncGenerator):
                await page_iter.aclose()
        checkpoint[partition.key] = ""
        yield StreamPage(records=[], next_cursor=self._encode(checkpoint), scope=partition.scope)

    async def _stream_ordered(
        self,
        partition: Partition,
        stored: dict[str, str | _Window],
        checkpoint: dict[str, str | _Window],
    ) -> AsyncIterator[StreamPage]:
        bound, high, until, synced, backfill = self._ordered_state(stored.get(partition.key))
        page_iter = self.pages(partition, bound)
        try:
            async for page in page_iter:
                self._comparable(partition, page.high, page.low, high, until, synced, self.floor)
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
                    checkpoint[partition.key] = _Window(high=high, until=until)
                elif not backfill and high is not None:
                    checkpoint[partition.key] = high
                yield StreamPage(
                    records=self._carried(page, partition),
                    deletes=page.deletes,
                    next_cursor=self._encode(checkpoint),
                    scope=partition.scope,
                )
                if grounded:
                    break
        except PartitionSkipped:
            return
        finally:
            if isinstance(page_iter, AsyncGenerator):
                await page_iter.aclose()
        if backfill and high is not None:
            checkpoint[partition.key] = high
            yield StreamPage(
                records=[], next_cursor=self._encode(checkpoint), scope=partition.scope
            )

    def _ordered_state(
        self, stored: str | _Window | None
    ) -> tuple[PartitionBound, str | None, str | None, str | None, bool]:
        match stored:
            case _Window(high=high, until=until):
                return PartitionBound(before=until, since=self.floor), high, until, None, True
            case str() as synced:
                return PartitionBound(after=synced), synced, None, synced, False
            case _:
                match self.ordering:
                    case Ordering.newest_first:
                        return PartitionBound(since=self.floor), None, None, None, True
                    case Ordering.ascending:
                        return PartitionBound(after=self.floor), None, None, None, False
                    case _:
                        return PartitionBound(), None, None, None, False

    @staticmethod
    def _comparable(partition: Partition, *values: str | None) -> None:
        """Refuse a partition whose all-digit watermarks are not one width, because the walk orders
        every watermark as text and `"1000" < "999"`.

        A provider numbering its cursor without padding crosses that boundary the moment its counter
        gains a digit, and every comparison the walk makes after it — the running maximum, the
        stop-early, the floor — reads backwards, so the partition resumes behind itself and drops
        what it passed over, with nothing raised and nothing logged. The values are the connector's
        to render, so it renders them comparably or says what it means."""
        widths = {len(value) for value in values if value is not None and value.isdigit()}
        if len(widths) > 1:
            numeric = sorted({value for value in values if value is not None and value.isdigit()})
            raise RuntimeError(
                f"partition {partition.ref!r} at {partition.path!r} compares watermarks of "
                f"different widths as text: {numeric}"
            )

    @staticmethod
    def _carried(page: WalkPage, partition: Partition) -> list[dict[str, Any]]:
        """This page's records with the fields its partition carries from the parent written onto
        each, before the connector shapes them, so a child is rendered and keyed with them. A
        record already carrying one of those fields raises: the provider's own value and the
        parent's are two answers, and which wins is a connector's to state in `flatten`."""
        if not partition.carried:
            return page.records
        carried: list[dict[str, Any]] = []
        for record in page.records:
            held = sorted(partition.carried.keys() & record.keys())
            if held:
                raise RuntimeError(
                    f"partition {partition.path!r} carries {held} onto a record that already "
                    "carries it"
                )
            carried.append({**record, **partition.carried})
        return carried

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


PinnedPartitions = Callable[[Sequence[Partition]], Sequence[Partition]]
"""Which partitions a run visits before the catalog, chosen from the ones the fan-out enumerated.
A watch names a resource of the provider rather than a stream of the declaration, so it reaches the
connector through the run and not off `StreamSpec` — and the rule picking them needs the enumerated
set, because what addresses a watched partition is how the parent that landed spells it."""


@dataclass(frozen=True)
class Run:
    """What one sync run of one stream hands the connector: where the last run left off, the reader
    of the landed parent records this stream's edges fan out over, the external identity the product
    itself speaks as where a surface resolved one, the instant this row's first sync reaches back
    to, and the partitions a standing watch pins ahead of the catalog.

    One object rather than a keyword each, because a run gains values — a stream reached the pinned
    resource of a watch rather than the whole collection, say — and a connector that reads none of
    them should not have to name them to pass them on. `cursor` and `parents` every stream is driven
    with; `self_user_id`, `backfill_after` and `pinned` only some read, and reading none of them is
    the common case."""

    cursor: str | None
    parents: ParentPages
    self_user_id: str | None = None
    backfill_after: datetime | None = None
    pinned: PinnedPartitions | None = None


async def fanned_out(
    stream: StreamSpec,
    run: Run,
    pages: PageFactory,
    floor: str | None = None,
) -> AsyncIterator[StreamPage]:
    """One declared stream's pages, fanned out over its parents' landed records and checkpointed per
    partition — the whole of what a connector does with an edge, so a connector writes the page
    factory and nothing else. The declaration carries the rest: `fetch_budget` bounds what one run
    asks — the seam's `DEFAULT_FETCH_BUDGET` where none is declared, and no bound at all on a
    `delete_missing` stream, whose declaration refuses one — `pass_interval_seconds` how long a
    completed pass waits before the next one opens, and the fan-out counts what each edge
    enumerated and cost.

    The walk is closed when its consumer stops taking pages, which the adapter does the moment a run
    reaches its record cap: an abandoned async generator releases the response it was reading only
    when it is collected, and a connector that forgot the close held an open HTTP body until then.
    It is also the only way partitions are consumed, so a per-partition cursor and the fields a
    parent carries reach every record of every edge, rather than whichever of them the connector
    that enumerated them by hand remembered.

    `floor` is not read off the run because it is not the run's to state: a walk compares it against
    the provider's own cursor values, and only the connector knows whether that space is an ISO
    instant or an epoch, so it renders `run.backfill_after` into one and hands it down.

    `run.pinned` puts a watch's partitions ahead of the catalog. The walk holds the whole
    enumeration either way, to order it by key: the order a budget resumes in has to be the same
    on every tick, and the order two edges are declared in, or a database returns rows in, is not.

    A partition the provider refuses is passed over and the pass still completes, which is what a
    connector's `PartitionSkipped` asks for — except on a `delete_missing` stream, whose run is an
    authoritative enumeration the driver tombstones every unmentioned page against. A partition
    missing from that is indistinguishable from a collection that emptied, so the refusal would
    delete the pages of a parent whose records were merely unreadable this tick. The refusal is
    raised there instead: a run that raises commits no page and sweeps none.

    A parent page that landed without its projection is the same gap one step up: its children are
    live pages of this stream that no partition of this pass will mention. An incremental stream
    passes it over, as it does a refused partition, and reaches them once the parent re-lands with
    the fields — a `none` parent re-lists whole on its next pass, an ordered one when the record
    next changes. A `delete_missing` stream raises on it for the same reason it raises on the
    refusal, and clears the same way."""

    async def projected(name: str) -> AsyncIterator[ParentRecord]:
        records = run.parents(name)
        try:
            async for parent in records:
                match parent:
                    case ParentRecord():
                        yield parent
                    case UnprojectedParent():
                        raise RuntimeError(
                            f"stream {stream.name!r} cannot snapshot under {name!r}: page "
                            f"{parent.ref!r} landed without the fields this stream reads"
                        )
                    case UnreadyParent():
                        raise RuntimeError(
                            f"stream {stream.name!r} cannot snapshot under {name!r}: the parent "
                            "has not completed a sync"
                        )
        finally:
            if isinstance(records, AsyncGenerator):
                await records.aclose()

    async def whole(partition: Partition, bound: PartitionBound) -> AsyncIterator[WalkPage]:
        page_iter = pages(partition, bound)
        try:
            async for page in page_iter:
                yield page
        except PartitionSkipped as skipped:
            raise RuntimeError(
                f"stream {stream.name!r} cannot snapshot {partition.path!r} of "
                f"{partition.ref!r}: {skipped}"
            ) from skipped
        finally:
            if isinstance(page_iter, AsyncGenerator):
                await page_iter.aclose()

    fan_out = TreeFanOut(stream=stream, parents=projected if stream.delete_missing else run.parents)
    walk = PartitionWalk(
        ordering=stream.ordering,
        partitions=(
            fan_out.partitions if run.pinned is None else _pinned_first(fan_out, run.pinned)
        ),
        pages=whole if stream.delete_missing else pages,
        floor=floor,
        report=fan_out.report,
        budget=None if stream.delete_missing else stream.fetch_budget or DEFAULT_FETCH_BUDGET,
        pass_interval_seconds=stream.pass_interval_seconds,
    )
    pages_of = walk.stream(run.cursor)
    try:
        async for page in pages_of:
            yield page
    finally:
        await pages_of.aclose()


def _pinned_first(fan_out: TreeFanOut, pinned: PinnedPartitions) -> Partitions:
    async def partitions() -> AsyncIterator[Partition]:
        enumerated = [partition async for partition in fan_out.partitions()]
        for partition in pinned(enumerated):
            yield partition
        for partition in enumerated:
            yield partition

    return partitions


class Connector(ABC):
    """A SaaS data-source connector. `streams` names the streams it can sync; `fetch_page`
    async-yields a stream's records grouped into pages given a resolved `Credential` and base URL;
    `render` turns one record into the `(title, body)` the adapter lands as a page. The runner
    consumes pages in order. Returning a smaller page gives more durable cursor checkpoints at the
    cost of more work.

    A connector declares its API address in one of two shapes. `base_url` alone is a complete fixed
    host every account of the provider shares. `base_url` empty is a per-tenant host — a subdomain,
    a data centre, a company file — carried by the connection, which its owner names and the grant
    store's tenant rule for the provider admits or refuses.

    `dials_host` False says the connector dials no provider host at all: it reads through tool
    executions the broker runs server-side (`ToolConnector`), so an empty `base_url` is its whole
    address rather than a per-tenant one the connection must name.

    It declares its direct-key auth shape the same way. `key_headers` empty is one bearer token, and
    the slot named for the connector holds it. `key_headers` populated is a provider authenticating
    with headers instead — header name to the credential slot holding that secret — so a provider
    demanding several keys names the slot for each."""

    name: ClassVar[str] = ""
    base_url: ClassVar[str] = ""
    dials_host: ClassVar[bool] = True
    key_headers: ClassVar[Mapping[str, str]] = {}

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
        yield_rate_limits: bool,
        parents: ParentPages,
        watched: WatchedResources | None,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        """Async-yield records from `cursor`, excluding exact `self_user_id` where applicable.

        `backfill_after` is this row's pinned floor, resolved at registration and replayed every
        run. A connector declaring `backfill_window_days` translates it into its provider's own
        floor (a `q=after:` term, a `$filter`) on the request that opens a walk; a resume carries
        its own and needs none. None is unbounded. `yield_rate_limits` is false when the active
        enumeration must retain its in-memory state through a provider wait.

        `parents` reads the landed records of a named parent stream of this connection, which are a
        declared child stream's partitions; a stream that declares no edge is handed a reader of
        nothing rather than none of one. `watched` reads the resources a standing watch on this
        connection pins, which a connector turns into the partitions it visits every tick ahead of
        its catalog walk; it is None where no extension answers the connection's watches."""

    def checkpoint(
        self, stream: StreamSpec, records: list[dict[str, Any]], cursor: str | None
    ) -> str | None:
        """Return opaque state after records the adapter accepted. Native page checkpoints take
        priority."""
        return cursor

    def pinned_partitions(
        self, stream: StreamSpec, resources: tuple[str, ...]
    ) -> PinnedPartitions | None:
        """The rule putting a standing watch's partitions ahead of one stream's catalog, given the
        resources it pins as this provider's own URLs. None — the default — is a provider with no
        watch shape: it reads what the declaration says and nothing more.

        A provider that has one answers a rule rather than a list, because what addresses a watched
        partition is how the parent that landed spells it, which only the enumerated set says. It
        answers one per stream, because a watched resource is a record of one stream and its
        partition addresses that stream's collection: a rule that ignored the stream would put a
        pull request's partition at the head of every other stream's walk, where the request lands
        nothing and costs a read a tick."""
        return None

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
