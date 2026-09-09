"""Public re-export: the content-source seam an extension implements, plus the REST connector
framework any extension reuses to build one.

An extension implements `SourceBackend` — its `fetch` renders a provider's records into `Page`
documents given the backend's typed config, the resume cursor, and the workspace `auth` (from which
it resolves its own provider token) — pairs it with a backend name in a `SourceProvider` Manifest
point, and creates a source row in chat through `ExtensionContext.register_source`. A feed that
names no broker account and no member — a repository, a folder root — hangs off a connection whose
`account_id` is `feed_handle(config)`, the same handle the `[[sources]]` boot path mints, so either
registrar settles on one connection per feed. Core polls the
row on the sync interval and lands its pages in memory; embedding stays a job. A backend that reads
a complete collection each run returns `SyncResult(snapshot=True)` and core tombstones prior pages
the fetch no longer holds; a delta/incremental backend returns `snapshot=False` and names removals
in `SyncResult.deletes`, so core tombstones only those and never sweeps pages a partial fetch didn't
mention. A backend raises `CursorExpired` when a stored incremental cursor is rejected, and core
clears it so the next run refetches fresh. A backend raises `StreamSkipped` when the provider
refuses the stream for this account (a missing scope, a plan gate), and core records the run
skipped, not failed — committing no pages, so nothing is tombstoned. A backend raises `StreamFault`
when the provider answers a shape the stream cannot read, and the failure event carries the reason
the backend authored rather than an exception message built out of the response.

A provider that publishes no REST host at all — an MCP-only service a broker fronts — subclasses
`ToolConnector` instead and pages through `ToolExecutor` tool executions the broker runs
server-side.

Rather than implement `SourceBackend` from scratch, a REST provider subclasses `RestConnector` —
declaring its `StreamSpec`s and a `Pagination` strategy (or overriding `paginate`) — and wraps it in
`ConnectorBackend`, the adapter that drives one stream to completion per run and collapses its pages
into a `SyncResult`. A provider returns opaque checkpoint state through `RestConnector.checkpoint`
after each record page; the framework publishes it after enumeration. A provider that can resume
between pages emits `StreamPage.next_cursor` directly, which takes priority over that callback.
The pagination helpers (`get_path`, `list_or_empty`, `records_at`,
`with_context`) are the shared record-shaping primitives a provider reaches for, and
`normalize_page_timestamp` is the one rule for reading a provider's instant — the same rule the
adapter projects a page's own timestamps through, so a provider rendering an instant of its own
cannot disagree with the page beside it. A stream that fans
out over partitions (one cursor per repo, channel) drives `PartitionWalk` with an `Ordering` and a
per-partition page factory, so the per-partition cursor-map codec and bounded-backfill resume live
once here, not in each connector. The concrete shapes live in `ufo.runtime.sources`, reached only
here."""

from ufo.runtime.access.connectors import (
    ToolExecutor as ToolExecutor,
)
from ufo.runtime.sources.backend import (
    ConnectorBackend as ConnectorBackend,
)
from ufo.runtime.sources.backend import (
    ConnectorSourceConfig as ConnectorSourceConfig,
)
from ufo.runtime.sources.connector import (
    CHAT_BACKFILL_WINDOW_DAYS as CHAT_BACKFILL_WINDOW_DAYS,
)
from ufo.runtime.sources.connector import (
    MAIL_BACKFILL_WINDOW_DAYS as MAIL_BACKFILL_WINDOW_DAYS,
)
from ufo.runtime.sources.connector import (
    REPO_BACKFILL_WINDOW_DAYS as REPO_BACKFILL_WINDOW_DAYS,
)
from ufo.runtime.sources.connector import (
    Connector as Connector,
)
from ufo.runtime.sources.connector import (
    Ordering as Ordering,
)
from ufo.runtime.sources.connector import (
    Pagination as Pagination,
)
from ufo.runtime.sources.connector import (
    PaginationStrategy as PaginationStrategy,
)
from ufo.runtime.sources.connector import (
    PartitionBound as PartitionBound,
)
from ufo.runtime.sources.connector import (
    PartitionSkipped as PartitionSkipped,
)
from ufo.runtime.sources.connector import (
    PartitionWalk as PartitionWalk,
)
from ufo.runtime.sources.connector import (
    StreamPage as StreamPage,
)
from ufo.runtime.sources.connector import (
    StreamSpec as StreamSpec,
)
from ufo.runtime.sources.connector import (
    WalkPage as WalkPage,
)
from ufo.runtime.sources.rest import (
    ProviderRateLimited as ProviderRateLimited,
)
from ufo.runtime.sources.rest import (
    RestConnector as RestConnector,
)
from ufo.runtime.sources.rest import (
    dict_or_empty as dict_or_empty,
)
from ufo.runtime.sources.rest import (
    get_path as get_path,
)
from ufo.runtime.sources.rest import (
    list_or_empty as list_or_empty,
)
from ufo.runtime.sources.rest import (
    records_at as records_at,
)
from ufo.runtime.sources.rest import (
    with_context as with_context,
)
from ufo.runtime.sources.sync import (
    CursorExpired as CursorExpired,
)
from ufo.runtime.sources.sync import (
    Page as Page,
)
from ufo.runtime.sources.sync import (
    PageBatch as PageBatch,
)
from ufo.runtime.sources.sync import (
    PageChange as PageChange,
)
from ufo.runtime.sources.sync import (
    PageFeed as PageFeed,
)
from ufo.runtime.sources.sync import (
    SourceAuth as SourceAuth,
)
from ufo.runtime.sources.sync import (
    SourceBackend as SourceBackend,
)
from ufo.runtime.sources.sync import (
    StreamFault as StreamFault,
)
from ufo.runtime.sources.sync import (
    StreamSkipped as StreamSkipped,
)
from ufo.runtime.sources.sync import (
    SyncResult as SyncResult,
)
from ufo.runtime.sources.sync import (
    feed_handle as feed_handle,
)
from ufo.runtime.sources.sync import (
    normalize_page_timestamp as normalize_page_timestamp,
)
from ufo.runtime.sources.tool import (
    ToolConnector as ToolConnector,
)
