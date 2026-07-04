"""What a connector is: the stream vocabulary and the `Connector` ABC every provider implements.

A connector knows a set of `StreamSpec`s (one per source-side collection it can sync) and, given a
stream plus an access token and base URL, async-yields the provider's records grouped into pages. A
page is a plain `list[dict]` when the source only returns live rows, or a `StreamPage` when a
delta/token source also reports removals (the removed records' external ids) and carries its own
resume cursor. The
`ConnectorBackend` adapter drives one stream to completion per sync run and collapses the pages into
the core `SyncResult` the source seam expects — a full-collection stream (`delete_missing`) becomes
an authoritative snapshot, an incremental stream advances a watermark and names its removals. `Page`
shapes are internal value objects: they never cross a wire, so they are frozen dataclasses, not
`BaseModel`."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar


class PaginationStrategy(StrEnum):
    """Named pagination shape a REST stream can declare. Concrete loops live on `RestConnector` —
    one per strategy. Connectors with provider-specific quirks (POST-body cursors, envelope
    unwrapping, fan-out across parent ids) keep overriding `paginate` directly; the strategy is the
    shared escape hatch for the common matrix."""

    next_cursor = "next_cursor"
    next_link = "next_link"
    page_number = "page_number"
    offset_limit = "offset_limit"
    time_window = "time_window"
    none = "none"


@dataclass(frozen=True)
class Pagination:
    """Declarative pagination for a `StreamSpec`, run by `RestConnector.paginate_from_strategy`.
    Per-strategy required knobs: `next_cursor` needs `record_path`, `cursor_path`, `cursor_param`;
    `next_link` needs `record_path`; `page_number` needs `record_path`, `page_size`, `cursor_param`;
    `offset_limit` needs `record_path`, `offset_param`, `limit_param`, `page_size`; `time_window`
    needs `cursor_param` and reads the value from the run's cursor; `none` is a single GET."""

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


@dataclass(frozen=True)
class StreamSpec:
    """One stream a connector knows how to sync: its registry `name`, the source-side object, the
    `primary_key` inside each record (the stable id the page is keyed by), and the optional
    `cursor_field` an incremental stream advances a watermark over. `delete_missing` marks a stream
    whose run enumerates the complete current collection — the adapter returns it as an
    authoritative snapshot so vanished records are tombstoned; left False, the stream is incremental
    and the adapter only upserts and names explicit removals. `pagination` routes a declared
    strategy; None
    means the connector's `paginate` handles the stream directly."""

    name: str
    source_object: str
    primary_key: str = "id"
    cursor_field: str | None = None
    delete_missing: bool = False
    canonical: bool = True
    pagination: Pagination | None = None


@dataclass(frozen=True)
class StreamPage:
    """One provider page of stream changes. Most connectors yield plain `list[dict]` because the
    source only returns live rows; delta-token and webhook-backed sources yield this richer shape so
    the adapter advances the provider cursor and tombstones removals (named by their source-side
    external ids in `deletes`) through the same run."""

    records: list[dict[str, Any]] = field(default_factory=list)
    deletes: tuple[str, ...] = ()
    next_cursor: str | None = None


class Connector(ABC):
    """A SaaS data-source connector. `streams` names the streams it can sync; `fetch_page`
    async-yields a stream's records grouped into pages given a per-account token and base URL. The
    runner
    consumes pages in order. Returning a smaller page gives more durable cursor checkpoints at the
    cost of more work."""

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
        access_token: str,
        base_url: str,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        """Async-yield the stream's records grouped into pages, incrementally from `cursor`."""
