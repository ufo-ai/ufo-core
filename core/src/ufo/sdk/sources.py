"""Public re-export: the content-source seam an extension implements, plus the REST connector
framework any extension reuses to build one.

An extension implements `SourceBackend` — its `fetch` renders a provider's records into `Page`
documents given the backend's typed config, the resume cursor, and the workspace `auth` (from which
it resolves its own provider token) — pairs it with a backend name in a `SourceProvider` Manifest
point, and creates a source row in chat through `ExtensionContext.register_source`. Core polls the
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

Rather than implement `SourceBackend` from scratch, a REST provider subclasses `RestConnector` —
declaring its `StreamSpec`s and a `Pagination` strategy (or overriding `paginate`) — and wraps it in
`ConnectorBackend`, the adapter that drives one stream to completion per run and collapses its pages
into a `SyncResult`. The pagination helpers (`get_path`, `list_or_empty`, `records_at`,
`with_context`) are the shared record-shaping primitives a provider reaches for. A stream that fans
out over partitions (one cursor per repo, channel) drives `PartitionWalk` with an `Ordering` and a
per-partition page factory, so the per-partition cursor-map codec and bounded-backfill resume live
once here, not in each connector. The concrete shapes live in `ufo.sources`, reached only here."""

from ufo.sources.backend import (
    ConnectorBackend as ConnectorBackend,
)
from ufo.sources.backend import (
    ConnectorSourceConfig as ConnectorSourceConfig,
)
from ufo.sources.backend import (
    binding_name as binding_name,
)
from ufo.sources.connector import (
    CHAT_BACKFILL_WINDOW_DAYS as CHAT_BACKFILL_WINDOW_DAYS,
)
from ufo.sources.connector import (
    MAIL_BACKFILL_WINDOW_DAYS as MAIL_BACKFILL_WINDOW_DAYS,
)
from ufo.sources.connector import (
    REPO_BACKFILL_WINDOW_DAYS as REPO_BACKFILL_WINDOW_DAYS,
)
from ufo.sources.connector import (
    Connector as Connector,
)
from ufo.sources.connector import (
    Ordering as Ordering,
)
from ufo.sources.connector import (
    Pagination as Pagination,
)
from ufo.sources.connector import (
    PaginationStrategy as PaginationStrategy,
)
from ufo.sources.connector import (
    PartitionBound as PartitionBound,
)
from ufo.sources.connector import (
    PartitionSkipped as PartitionSkipped,
)
from ufo.sources.connector import (
    PartitionWalk as PartitionWalk,
)
from ufo.sources.connector import (
    StreamPage as StreamPage,
)
from ufo.sources.connector import (
    StreamSpec as StreamSpec,
)
from ufo.sources.connector import (
    WalkPage as WalkPage,
)
from ufo.sources.rest import (
    RestConnector as RestConnector,
)
from ufo.sources.rest import (
    dict_or_empty as dict_or_empty,
)
from ufo.sources.rest import (
    get_path as get_path,
)
from ufo.sources.rest import (
    list_or_empty as list_or_empty,
)
from ufo.sources.rest import (
    records_at as records_at,
)
from ufo.sources.rest import (
    with_context as with_context,
)
from ufo.sources.sync import (
    CursorExpired as CursorExpired,
)
from ufo.sources.sync import (
    Page as Page,
)
from ufo.sources.sync import (
    PageBatch as PageBatch,
)
from ufo.sources.sync import (
    PageChange as PageChange,
)
from ufo.sources.sync import (
    PageFeed as PageFeed,
)
from ufo.sources.sync import (
    SourceAuth as SourceAuth,
)
from ufo.sources.sync import (
    SourceBackend as SourceBackend,
)
from ufo.sources.sync import (
    StreamFault as StreamFault,
)
from ufo.sources.sync import (
    StreamSkipped as StreamSkipped,
)
from ufo.sources.sync import (
    SyncResult as SyncResult,
)
from ufo.subjects import (
    SHARED_SUBJECT as SHARED_SUBJECT,
)
from ufo.subjects import (
    member_subject as member_subject,
)
