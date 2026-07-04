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
skipped, not failed — committing no pages, so nothing is tombstoned.

Rather than implement `SourceBackend` from scratch, a REST provider subclasses `RestConnector` —
declaring its `StreamSpec`s and a `Pagination` strategy (or overriding `paginate`) — and wraps it in
`ConnectorBackend`, the adapter that drives one stream to completion per run and collapses its pages
into a `SyncResult`. The pagination helpers (`get_path`, `list_or_empty`, `records_at`,
`with_context`) are the shared record-shaping primitives a provider reaches for. The concrete shapes
live in `selfhost.sources`, reached only here."""

from selfhost.sources.backend import (
    ConnectorBackend as ConnectorBackend,
)
from selfhost.sources.backend import (
    ConnectorSourceConfig as ConnectorSourceConfig,
)
from selfhost.sources.connector import (
    Connector as Connector,
)
from selfhost.sources.connector import (
    Pagination as Pagination,
)
from selfhost.sources.connector import (
    PaginationStrategy as PaginationStrategy,
)
from selfhost.sources.connector import (
    StreamPage as StreamPage,
)
from selfhost.sources.connector import (
    StreamSpec as StreamSpec,
)
from selfhost.sources.rest import (
    RestConnector as RestConnector,
)
from selfhost.sources.rest import (
    dict_or_empty as dict_or_empty,
)
from selfhost.sources.rest import (
    get_path as get_path,
)
from selfhost.sources.rest import (
    list_or_empty as list_or_empty,
)
from selfhost.sources.rest import (
    records_at as records_at,
)
from selfhost.sources.rest import (
    with_context as with_context,
)
from selfhost.sources.sync import (
    CursorExpired as CursorExpired,
)
from selfhost.sources.sync import (
    Page as Page,
)
from selfhost.sources.sync import (
    PageBatch as PageBatch,
)
from selfhost.sources.sync import (
    PageChange as PageChange,
)
from selfhost.sources.sync import (
    PageFeed as PageFeed,
)
from selfhost.sources.sync import (
    SourceAuth as SourceAuth,
)
from selfhost.sources.sync import (
    SourceBackend as SourceBackend,
)
from selfhost.sources.sync import (
    StreamSkipped as StreamSkipped,
)
from selfhost.sources.sync import (
    SyncResult as SyncResult,
)
from selfhost.subjects import (
    SHARED_SUBJECT as SHARED_SUBJECT,
)
from selfhost.subjects import (
    member_subject as member_subject,
)
