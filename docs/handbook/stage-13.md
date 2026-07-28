# Source synchronization, page change processing, indexing, search, memory, and graph enrichment  `stage-13`

This stage is the system’s data intake and recall workshop. It runs mostly behind the scenes, either on scheduled source-sync jobs or when a page-change hook fires. Its job is to bring in information from outside tools, notice what changed, store the latest document text, remove deleted items, and prepare everything for search, memory, alerts, and graph-based lookup later.

The provider-specific source connectors are the many “plugs” for services like Google, Microsoft, Slack, GitHub, Notion, Salesforce, and others. They translate each service’s records into one common format. The package marker file simply makes the source code importable. The backend file is the adapter bridge: it turns connector output into UFO pages, progress cursors, deletes, and snapshots. The sync file is the main engine: it pulls configured sources, saves changed text, records additions and removals, and hands changes onward.

The recall and enrichment parts then make the saved knowledge useful. Indexing splits text into searchable chunks and embeddings. Memory condenses important facts. The knowledge graph extracts people, places, and links. Alerts watch changed pages for topics that matter.

## Sub-stages

- [Provider-specific source connectors](stage-13.1.md) `stage-13.1` — 52 files
- [Recall, indexing, memory consolidation, and graph extraction](stage-13.2.md) `stage-13.2` — 15 files

## Files in this stage

### Source sync orchestration
Package setup, connector bridging, and the core sync engine translate external provider records into stored page changes ready for deletion, snapshotting, indexing, and search.

### `core/src/ufo/sources/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it has an `__init__.py` file. That means code elsewhere can refer to modules inside this directory using names like `ufo.sources.something` instead of relying on raw file paths. Think of it like a label on a drawer: the drawer may contain useful tools, but this label mainly tells Python, “this drawer belongs to the project and can be opened by name.” Because the file is empty, it does not set up any shared state, run startup code, or expose shortcuts for other modules. If it were missing, imports involving `ufo.sources` might fail or behave differently depending on the Python packaging setup.


### `core/src/ufo/sources/backend.py`

`orchestration` · `source sync run`

Connectors know how to talk to outside tools, such as GitHub or Zendesk, and they return records in pages. UFO's sync engine wants one clear result per run: pages to save, a cursor for where to continue next time, and possibly records to delete. This file translates between those two worlds.

The main piece is `ConnectorBackend`. For one configured account and one connector stream, it asks the auth proxy for a credential, finds the requested stream, calls the connector, and turns each provider record into a UFO `Page`. A `Page` is a recallable stored item with a stable reference, text body, digest, title, and timestamps.

The file also protects sync jobs from running forever. Incremental streams are capped at a fixed number of records per run. If the connector gives a real checkpoint cursor, UFO stores it. If not, this adapter stores its own small resume envelope that says, in effect, “start from the old place, skip the records already consumed, then continue.” This is like putting a bookmark plus a count into a long stack of papers.

Full snapshot streams are different. They are used to detect missing records, so they must read the whole collection; otherwise UFO might wrongly delete live data. The file is also careful not to expose credentials beyond the in-process sync job.

#### Function details

##### `ConnectorBackend.fetch`  (lines 106–195)

```
async def fetch(self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Runs one connector stream for one account and returns UFO's standard sync result. It is the main adapter method: it gets credentials, reads provider records, converts them into pages, tracks deletes, and decides where the next run should resume.

**Data flow**: It starts with a source config, the previous cursor, and auth information. It asks the auth proxy for a credential, finds the requested stream, chooses the correct base URL, and decodes any UFO-made backfill cursor. Then it reads pages from the connector. Each record becomes a UFO `Page`; delete notices become stable delete references; cursor fields update the watermark. If the run reaches the incremental cap, it returns the pages collected so far plus either the connector's checkpoint or UFO's own skip-count envelope. If the stream finishes, it returns all collected pages, the final next cursor, any deletes, and whether this was a full snapshot.

**Call relations**: The sync driver calls this when it wants to pull one configured connector source. Inside the run, it relies on `_stream` to pick the stream, `_decode_cursor` to understand UFO's own resume state, `_page` to convert each record, and `_max_str` to advance a simple string watermark. If a provider does not advance its checkpoint for too long, it emits a warning before returning a safe resume cursor instead of letting the worker spin forever.

*Call graph*: calls 4 internal fn (_decode_cursor, _page, _stream, _max_str); 4 external calls (__init__, __init__, dumps, warn).


##### `ConnectorBackend._stream`  (lines 197–201)

```
def _stream(self, name: str) -> StreamSpec
```

**Purpose**: Finds the connector stream named in the source configuration. This prevents a sync from silently running the wrong stream when the configuration contains a bad name.

**Data flow**: It receives a stream name, looks through the streams exposed by the connector, and returns the matching stream specification. If none match, it stops with an error that names both the connector and the missing stream.

**Call relations**: `ConnectorBackend.fetch` calls this near the start of a run, before any provider records are fetched. The returned stream specification tells the rest of the run which primary key, cursor field, timestamp fields, and delete behavior to use.

*Call graph*: called by 1 (fetch).


##### `ConnectorBackend._decode_cursor`  (lines 204–221)

```
def _decode_cursor(cursor: str | None) -> '_BackfillEnvelope | None'
```

**Purpose**: Recognizes the special resume cursor that this adapter writes for capped backfills without native connector checkpoints. It leaves all other cursors alone so connector-specific cursor formats still pass through unchanged.

**Data flow**: It receives the stored cursor text, if any. If the cursor is missing, not JSON, not a JSON object, or does not contain the reserved `ufo_backfill` key, it returns nothing, meaning the cursor should be treated as ordinary connector state. If the reserved key is present, it validates the contained origin, skip count, and watermark. A malformed UFO envelope becomes a runtime error because this adapter is supposed to be the only writer of that format.

**Call relations**: `ConnectorBackend.fetch` calls this only for incremental streams. The result tells `fetch` whether to resume normally from the connector cursor or to re-drive from an older origin and skip already-consumed records.

*Call graph*: called by 1 (fetch); 1 external calls (loads).


##### `ConnectorBackend._page`  (lines 223–249)

```
def _page(self, stream: StreamSpec, record: dict[str, Any]) -> Page
```

**Purpose**: Turns one raw provider record into UFO's stored page format. This gives every record a stable identity, searchable body text, checksum, stream name, title, and normalized timestamps.

**Data flow**: It receives the stream specification and one record dictionary. It gets a stable record reference, asks the connector to render the record into a title and body, extracts created and updated timestamps, hashes the body to make a digest, and returns a `Page` object ready for the sync result.

**Call relations**: `ConnectorBackend.fetch` calls this for every record that should be landed in UFO. `_page` delegates the identity choice to `_record_ref` and timestamp cleanup to `_record_timestamp`, then hands the completed page back to `fetch` so it can be included in the final `SyncResult`.

*Call graph*: calls 2 internal fn (_record_ref, _record_timestamp); called by 1 (fetch); 2 external calls (__init__, sha256).


##### `_record_timestamp`  (lines 252–283)

```
def _record_timestamp(record: dict[str, Any], field: str | None, *, connector: str, stream: str) -> str | None
```

**Purpose**: Extracts and normalizes a timestamp from a provider record, or returns nothing if the timestamp is missing or unusable. This keeps stored page dates in one expected format even when providers send dates in different shapes.

**Data flow**: It receives a record, the timestamp field name, and labels for the connector and stream. If no field is configured, it returns nothing. Otherwise it reads the value directly or through a nested path, accepts strings and non-boolean integers, and passes them through the timestamp normalizer. If the value cannot be normalized, it logs a warning and returns nothing rather than storing a bad date.

**Call relations**: `ConnectorBackend._page` calls this while building each stored page, once for the created time and once for the updated time. It uses `get_path` when the field is nested inside the record and the shared timestamp normalizer so pages across connectors use the same date style.

*Call graph*: called by 1 (_page); 3 external calls (warn, get_path, normalize_page_timestamp).


##### `_record_ref`  (lines 286–290)

```
def _record_ref(stream: StreamSpec, record: dict[str, Any]) -> str
```

**Purpose**: Chooses the stable reference used to identify one provider record inside a stream. This is what lets an unchanged record, an updated record, and a delete notice all point to the same stored page.

**Data flow**: It receives the stream specification and one record. If the configured primary key is a string or integer, it returns that value as text. If the primary key is missing or not a simple value, it hashes the whole record in a stable JSON order and returns that hash as a fallback reference.

**Call relations**: `ConnectorBackend._page` calls this before creating a `Page`. The returned reference is combined with the stream name, so pages and delete markers use the same `stream/reference` shape throughout the sync flow.

*Call graph*: called by 1 (_page); 2 external calls (sha256, dumps).


##### `_max_str`  (lines 293–298)

```
def _max_str(current: str | None, value: Any) -> str | None
```

**Purpose**: Keeps the largest string watermark seen so far. This is used for simple incremental streams where newer records can be tracked by a string cursor value such as an ISO timestamp.

**Data flow**: It receives the current watermark and a candidate value from a record. If the candidate is not a string, it leaves the current watermark unchanged. If there is no current watermark, or the candidate sorts after it, it returns the candidate; otherwise it returns the current value.

**Call relations**: `ConnectorBackend.fetch` calls this while reading records from streams with a cursor field. The updated watermark is later used as the next cursor when the connector does not provide a stronger native checkpoint.

*Call graph*: called by 1 (fetch).


### `core/src/ufo/sources/sync.py`

`orchestration` · `startup registration, scheduled sync polling, and downstream index replay`

This file turns outside content into stable “pages” inside the system. A source can be a local folder or an extension such as a SaaS connector. Each backend knows how to fetch its own documents and returns a clear result: the pages it saw, a cursor for resuming later, and any deletions. The sync driver then compares those fetched pages with what is already stored. If a page is new or its digest, meaning a fingerprint of its content, has changed, the driver writes the body to the blob store and updates the database row. If only browse details like title changed, it updates just those details. If a full snapshot no longer contains a page, or a delta source explicitly reports a deletion, the page is tombstoned, meaning marked as removed rather than simply forgotten. The file also prevents duplicate work by claiming due sources before syncing them, like putting a sticky note on a task so another worker does not grab it. Failures are rescheduled with backoff, while intentional skips, such as missing permissions, do not erase existing pages. Finally, CorePageFeed lets an indexer replay changed pages in a safe order using a cursor, so downstream search can catch up without missing changes.

#### Function details

##### `normalize_page_timestamp`  (lines 50–70)

```
def normalize_page_timestamp(value: str) -> str
```

**Purpose**: This function converts a timestamp from a source into one consistent UTC format. Sources may send times as Unix numbers, milliseconds, ISO strings, or dates, so this keeps the rest of the system from guessing.

**Data flow**: It receives a timestamp string. If the string is numeric, it treats it as seconds or milliseconds since 1970; otherwise it parses it as an ISO-style date/time and requires a timezone unless it is a plain date. It returns a UTC ISO timestamp with microseconds, or raises a clear error if the value cannot be trusted.

**Call relations**: Page.normalize_timestamp calls this whenever a Page is built with created_at or updated_at. That means source backends can provide common timestamp shapes, while the database and page feed receive a consistent value.

*Call graph*: called by 1 (normalize_timestamp); 2 external calls (fromisoformat, fromtimestamp).


##### `Page.normalize_timestamp`  (lines 88–91)

```
def normalize_timestamp(cls, value: str | None) -> str | None
```

**Purpose**: This validator cleans up optional page timestamps before a Page object is accepted. It allows missing timestamps but standardizes any timestamp that is present.

**Data flow**: It receives either None or a timestamp string from a fetched page. None passes through unchanged; a string is sent to normalize_page_timestamp. The output is either None or a normalized UTC timestamp string stored on the Page.

**Call relations**: This runs as part of Page creation. It delegates the actual parsing rules to normalize_page_timestamp so every source uses the same timestamp behavior.

*Call graph*: calls 1 internal fn (normalize_page_timestamp).


##### `StreamSkipped.__init__`  (lines 123–125)

```
def __init__(self, reason: str) -> None
```

**Purpose**: This creates a special error that means a source stream was intentionally skipped, not broken. It carries a human-readable reason, such as missing permissions or a plan limitation.

**Data flow**: It receives a reason string. It stores that reason on the exception and also passes it to the normal RuntimeError machinery. The result is an exception object the sync driver can recognize and treat differently from real failures.

**Call relations**: Many connector pagination flows raise this when a provider refuses access in an expected way. SyncDriver.run catches it and calls _skip, which reschedules normally without deleting pages or increasing the error count.

*Call graph*: called by 49 (paginate, paginate, paginate, paginate, _org_stream, paginate, paginate, paginate, paginate, paginate (+15 more)).


##### `SourceBackend.config_model`  (lines 159–159)

```
def config_model(self) -> type[ConfigT]
```

**Purpose**: This protocol property says every source backend must declare the shape of its configuration. It keeps source settings typed and validated instead of being an unchecked dictionary.

**Data flow**: A backend provides a Pydantic model class, which is a validation class for structured data. The sync driver reads the source row’s stored config and validates it against that model before fetching.

**Call relations**: SyncDriver._fetch relies on this property before calling the backend. FolderSource and extension backends provide concrete models so the driver can use all backends through the same interface.


##### `SourceBackend.fetch`  (lines 161–161)

```
async def fetch(self, config: ConfigT, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This protocol method defines what every source backend must do: fetch documents for one source sync run. It is the contract between the core sync system and all source-specific connectors.

**Data flow**: It receives validated config, the last saved cursor if any, and SourceAuth containing workspace and credential access information. It returns a SyncResult containing fetched pages, a next cursor, deletions, and whether the result is a full snapshot.

**Call relations**: SyncDriver._fetch calls this on whichever backend matches the source row. FolderSource implements it locally, and extension backends implement it for external systems.


##### `FolderSource.fetch`  (lines 175–187)

```
async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This fetches all files from a configured local folder and turns each file into a Page. It is the built-in source backend for simple local-directory content.

**Data flow**: It receives folder configuration, ignores the cursor because folders are scanned fully each time, and ignores auth because local files need no provider token. It reads files in a background thread, makes each file path the stable page key and title, hashes the file text into a digest, and returns a snapshot SyncResult.

**Call relations**: SyncDriver._fetch can call this through the SourceBackend interface when a source row uses the folder backend. It uses FolderSource._read for disk access and produces Page and SyncResult objects for the driver to commit.

*Call graph*: 5 external calls (__init__, __init__, to_thread, sha256, Path).


##### `FolderSource._read`  (lines 190–197)

```
def _read(root: Path) -> tuple[tuple[str, str], ...]
```

**Purpose**: This reads the actual files from a local source directory. It deliberately fails if the root folder is missing, so a temporary mount problem does not make the system think every document was deleted.

**Data flow**: It receives a root Path. It checks that the root is a directory, walks all files under it in sorted order, reads each file as UTF-8 text, and returns pairs of relative file path and text content.

**Call relations**: FolderSource.fetch runs this in a worker thread so file reading does not block the async event loop. Its returned file list is then converted into Page objects.

*Call graph*: 2 external calls (is_dir, rglob).


##### `source_row_id`  (lines 200–207)

```
def source_row_id(workspace_id: UUID, backend: str, config: Mapping[str, object]) -> UUID
```

**Purpose**: This makes a repeatable ID for a source row from workspace, backend name, and configuration. It prevents the same configured source from being inserted again after a restart.

**Data flow**: It receives a workspace ID, backend name, and config mapping. It serializes the config in a stable key order and feeds the combined information into UUID generation. The output is always the same UUID for the same source definition.

**Call relations**: register_sources calls this while creating configured sources at startup. Because the ID is deterministic, registration can safely run again without duplicating existing sources.

*Call graph*: called by 1 (register_sources); 2 external calls (dumps, uuid5).


##### `page_id_for`  (lines 210–213)

```
def page_id_for(source_id: UUID, source_ref: str) -> UUID
```

**Purpose**: This makes a repeatable ID for one page inside one source. It lets updates, refetches, and deletion reports all point to the same database row.

**Data flow**: It receives a source ID and the page’s source_ref, which is the stable key from the backend. It combines them into a deterministic UUID. The output is the page row ID used throughout syncing.

**Call relations**: SyncDriver._commit calls this for fetched pages and explicit deletes. That ensures the later write step updates or tombstones the correct page.

*Call graph*: called by 1 (_commit); 1 external calls (uuid5).


##### `register_sources`  (lines 216–250)

```
async def register_sources(configured: tuple[SourceEntry, ...]) -> None
```

**Purpose**: This creates database rows for sources listed in configuration. It runs at boot so configured sources are known to the sync scheduler.

**Data flow**: It receives configured source entries. If there are any, it opens a workspace transaction, finds the workspace ID, computes each deterministic source ID, checks whether it already exists, and inserts missing rows due for immediate sync.

**Call relations**: It uses source_row_id to avoid duplicates. It is separate from the polling sync loop: it prepares source rows once, and SyncDriver later claims and syncs them.

*Call graph*: calls 1 internal fn (source_row_id); 4 external calls (now, insert, select, workspace_tx).


##### `SyncDriver.candidate_workspaces`  (lines 294–314)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This finds which workspaces currently have sources ready to sync. It lets the scheduler avoid opening work for workspaces with nothing due.

**Data flow**: It reads the current time, queries source rows across ownership scope for due, non-removed, unclaimed or expired-claim sources, and returns distinct workspace IDs. It does not sync anything itself.

**Call relations**: A higher-level dispatcher can call this before binding a workspace and running SyncDriver.run. It uses owner_tx because it needs a cross-workspace look, while actual sync writes happen under workspace transactions.

*Call graph*: 4 external calls (now, or_, select, owner_tx).


##### `SyncDriver.run`  (lines 316–339)

```
async def run(self) -> None
```

**Purpose**: This is the main per-workspace sync loop. It claims due sources, fetches each one, commits successful results, and reschedules failures or skips.

**Data flow**: It creates a unique claim token, asks _claim_due for available sources, and processes them one by one. For each source it calls _fetch, then _commit; if a StreamSkipped is raised it logs and calls _skip; for other errors it logs and calls _release, clearing the cursor only for CursorExpired.

**Call relations**: This ties together the driver’s private steps. It is the method the scheduler uses when a workspace has due source work.

*Call graph*: calls 5 internal fn (_claim_due, _commit, _fetch, _release, _skip); 2 external calls (log, uuid4).


##### `SyncDriver._claim_due`  (lines 341–385)

```
async def _claim_due(self, claim: str) -> tuple[ClaimedSource, ...]
```

**Purpose**: This reserves a batch of due sources for the current worker. Claiming prevents two workers from syncing the same source at the same time.

**Data flow**: It receives a claim token, finds due source rows whose claims are empty or expired, optionally uses database row locking on Postgres, and writes the claim plus an expiration time. It returns ClaimedSource value objects containing the information needed to fetch.

**Call relations**: SyncDriver.run calls this first. The returned sources are then passed through _fetch and _commit, or to _release/_skip if something prevents normal completion.

*Call graph*: called by 1 (run); 7 external calls (__init__, now, timedelta, or_, select, update, workspace_tx).


##### `SyncDriver._fetch`  (lines 387–393)

```
async def _fetch(self, source: ClaimedSource) -> SyncResult
```

**Purpose**: This calls the correct backend for one claimed source. It also validates the stored source configuration before giving it to backend code.

**Data flow**: It receives a ClaimedSource. It looks up the backend by name, validates the stored config with that backend’s config_model, builds SourceAuth with the workspace and optional auth proxy, and returns the backend’s SyncResult.

**Call relations**: SyncDriver.run calls this after claiming a source. It hands off source-specific work to FolderSource or an extension backend, while keeping the driver responsible for scheduling and commits.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `SyncDriver._commit`  (lines 395–432)

```
async def _commit(self, source: ClaimedSource, result: SyncResult) -> None
```

**Purpose**: This decides what changed after a backend fetch and prepares the database/blob updates. It avoids rewriting unchanged page bodies by comparing content digests.

**Data flow**: It receives a claimed source and SyncResult. It loads prior page state, computes stable page IDs, compares each fetched page with existing digest and tombstone state, writes new or changed bodies to the blob store, separates metadata-only updates, computes explicit deletions, and passes all write instructions to _write.

**Call relations**: SyncDriver.run calls this after _fetch succeeds. It uses _prior_pages for comparison, page_id_for for stable IDs, and _write for the actual database transaction.

*Call graph*: calls 3 internal fn (_prior_pages, _write, page_id_for); called by 1 (run); 2 external calls (__init__, __init__).


##### `SyncDriver._prior_pages`  (lines 434–466)

```
async def _prior_pages(self, source_id: UUID) -> dict[UUID, tuple[str, bool, PageBrowse]]
```

**Purpose**: This loads the current database state for all pages belonging to one source. It gives _commit the comparison baseline it needs to detect real changes.

**Data flow**: It receives a source ID, queries page rows for that source, and builds a dictionary keyed by page ID. Each entry contains the stored digest, whether it is tombstoned, and browse metadata such as stream and title.

**Call relations**: SyncDriver._commit calls this before comparing fetched pages. The result determines whether pages need new body writes, metadata updates, or no work.

*Call graph*: called by 1 (_commit); 3 external calls (__init__, select, workspace_tx).


##### `SyncDriver._write`  (lines 468–561)

```
async def _write(self, source: ClaimedSource, next_cursor: str | None, changed: list[ChangedPage], metadata: list[PageBrowse], fetched: list[UUID], deleted: list[UUID], snapshot: bool) -> None
```

**Purpose**: This performs the final database updates for a successful sync. It inserts or updates changed pages, marks deletions, and schedules the source’s next normal sync.

**Data flow**: It receives the source, next cursor, changed pages, metadata-only pages, fetched page IDs, deleted page IDs, and a snapshot flag. Inside one workspace transaction it upserts changed pages, updates metadata-only rows, tombstones explicit deletes, tombstones missing pages for snapshots, and clears the source claim while resetting errors.

**Call relations**: SyncDriver._commit calls this after it has worked out the change set. Its timestamp choices matter for CorePageFeed, which later reads pages in updated_at/id order and must not miss a quick rewrite.

*Call graph*: called by 1 (_commit); 6 external calls (now, timedelta, insert, select, update, workspace_tx).


##### `SyncDriver._release`  (lines 563–587)

```
async def _release(self, source: ClaimedSource, cursor_reset: bool) -> None
```

**Purpose**: This frees a source claim after a real failure and schedules a retry later. It uses increasing delay so a repeatedly failing source does not hammer its provider or block other sources.

**Data flow**: It receives the failed source and whether the cursor should be reset. It increments the error count, computes a capped exponential backoff, optionally clears the cursor, updates next_sync_at, and removes the claim.

**Call relations**: SyncDriver.run calls this when _fetch or _commit raises an ordinary exception. CursorExpired is treated specially by passing cursor_reset as true, so the next run starts fresh.

*Call graph*: called by 1 (run); 4 external calls (now, timedelta, update, workspace_tx).


##### `SyncDriver._skip`  (lines 589–606)

```
async def _skip(self, source: ClaimedSource) -> None
```

**Purpose**: This frees a source claim after an intentional skip, such as missing permission for a stream. It does not treat the skip as a data failure.

**Data flow**: It receives the skipped source. It updates the source row to run again at the normal interval, resets the error count, and clears the claim, while leaving the cursor and existing pages untouched.

**Call relations**: SyncDriver.run calls this only after catching StreamSkipped. Because no pages are committed, snapshot deletion logic cannot accidentally remove existing content for a skipped stream.

*Call graph*: called by 1 (run); 4 external calls (now, timedelta, update, workspace_tx).


##### `PageFeed.pages_changed_since`  (lines 642–642)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: This protocol method defines how an indexer asks for changed pages. It is the read-side contract for replaying page updates after a cursor.

**Data flow**: A caller provides an optional cursor and a limit. An implementation returns a PageBatch containing page changes and the next cursor to continue from.

**Call relations**: CorePageFeed implements this protocol for the core page table and blob store. Extensions or indexers use the protocol rather than knowing the storage details.


##### `CorePageFeed.pages_changed_since`  (lines 654–704)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: This returns a small ordered batch of page changes for an indexer. It lets downstream indexing resume safely after the last processed page.

**Data flow**: It receives an optional cursor in the form changed-time plus page ID, and a requested limit. It queries page rows after that cursor in updated_at/id order, caps the batch size, reads each non-tombstoned body from the blob store, uses an empty body for tombstones, builds PageChange objects, and returns them with a cursor pointing at the last row.

**Call relations**: Indexing code calls this through the PageFeed interface. It relies on SyncDriver._write updating page rows with strictly advancing timestamps, so replay can move forward without skipping changes.

*Call graph*: 8 external calls (__init__, __init__, fromisoformat, and_, or_, select, workspace_tx, UUID).

## 📊 State Registers Touched

- `reg-workspace-tenant-record` — The customer workspace record that all users, conversations, data, tools, and billing are kept under.
- `reg-durable-work-queue` — The shared queue of conversation and job work waiting to be claimed, retried, resumed, or completed by workers.
- `reg-credential-secret-store` — The encrypted store of workspace secrets and credential kinds used without exposing raw tokens to agents.
- `reg-authorization-grants` — The saved permissions showing which user-approved outside accounts an agent may use.
- `reg-blob-storage-backend` — The shared large-file storage used for workspace files, transcripts, source snapshots, and artifacts.
- `reg-network-egress-policy` — The allow-or-deny rules for outbound network calls, including when approved secrets may be attached.
- `reg-connector-broker-catalog` — The known external service brokers and provider actions that let agents use connected services safely.
- `reg-source-page-sync-state` — The saved sources, pages, sync cursors, deletion markers, and retry state for imported external content.
- `reg-search-index-memory-graph` — The shared recall stores for searchable chunks, remembered facts, memory pages, and knowledge-graph links.
- `reg-scheduled-task-state` — The saved clock-based tasks, waits, pauses, last-run markers, and expiration times used to wake work later.
- `reg-alert-watch-state` — Saved alert subscriptions, match rules, checkpoints, and pending notifications used to react to changed synced pages.
- `reg-web-search-fetch-backend` — The configured web-search and page-fetch provider backend, client settings, and availability used by research, browsing, source, and SDK search calls.
