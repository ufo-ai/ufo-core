"""Public re-export: the content-source seam an extension implements to register a sync backend.

An extension implements `SourceBackend` — its `fetch` renders a provider's records into `Page`
documents given the backend's typed config, the resume cursor, and the workspace `auth` (from which
it resolves its own provider token) — pairs it with a backend name in a `SourceProvider` Manifest
point, and creates a source row in chat through `ExtensionContext.register_source`. Core polls the
row on the sync interval and lands its pages in memory; embedding stays a job. A backend that reads
a complete collection each run returns `SyncResult(snapshot=True)` and core tombstones prior pages
the fetch no longer holds; a delta/incremental backend returns `snapshot=False` and names removals
in `SyncResult.deletes`, so core tombstones only those and never sweeps pages a partial fetch didn't
mention. A backend raises `CursorExpired` when a stored incremental cursor is rejected, and core
clears it so the next run refetches fresh. The concrete shapes live in `selfhost.memory.sources`,
reached only here."""

from selfhost.memory.sources import (
    CursorExpired as CursorExpired,
)
from selfhost.memory.sources import (
    Page as Page,
)
from selfhost.memory.sources import (
    SourceAuth as SourceAuth,
)
from selfhost.memory.sources import (
    SourceBackend as SourceBackend,
)
from selfhost.memory.sources import (
    SyncResult as SyncResult,
)
from selfhost.subjects import (
    SHARED_SUBJECT as SHARED_SUBJECT,
)
