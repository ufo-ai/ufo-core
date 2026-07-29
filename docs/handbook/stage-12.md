# External source sync, indexing, and durable memory work  `stage-12`

This stage is the system’s background intake and memory workshop. It is not the chat loop itself. Instead, it runs sync jobs that fetch outside pages and records, notice what changed, store safe copies, make them searchable, and turn useful facts into long-term memory.

The core source backend lets each connector behave like a normal UFO source. It takes a connector’s stream of records and packages it so syncing can resume, store results, and handle deletions safely. The sync bridge then writes fetched document bodies into the internal page database and produces a clean list of page changes for later steps.

The many source connector groups are the “adapters” for outside tools: work tracking, documents, mail, chat, CRM, marketing, HR, finance, YC data, and evaluation feeds. Each one reads its service and translates records into a common shape. The registry helps the system find and name these connectors consistently. Search indexing slices changed text into chunks and updates the search store. Memory tools save important facts, recall them later, and condense duplicates into clearer summaries.

## Sub-stages

- [Search indexing and default index backend](stage-12.1.md) `stage-12.1` — 2 files
- [Durable memory extension](stage-12.2.md) `stage-12.2` — 4 files
- [Source connector registry and YC feeds](stage-12.3.md) `stage-12.3` — 2 files
- [Work, engineering, and incident source connectors](stage-12.4.md) `stage-12.4` — 9 files
- [Knowledge, document, and database source connectors](stage-12.5.md) `stage-12.5` — 6 files
- [Mail, calendar, chat, and meeting source connectors](stage-12.6.md) `stage-12.6` — 7 files
- [CRM, sales, and support source connectors](stage-12.7.md) `stage-12.7` — 6 files
- [Marketing, advertising, social, and feedback source connectors](stage-12.8.md) `stage-12.8` — 7 files
- [HR and recruiting source connectors](stage-12.9.md) `stage-12.9` — 6 files
- [Finance, billing, accounting, and commerce source connectors](stage-12.10.md) `stage-12.10` — 7 files

## Files in this stage

### Source sync bridge
Adapts external connectors into UFO sources and synchronizes their records into the internal page database for downstream indexing.

### `core/src/ufo/sources/backend.py`

`domain_logic` · `sync run`

A connector knows how to talk to one outside service, such as GitHub or Zendesk, and read one stream of records from it. The rest of UFO expects a simpler shape: one run should return a bundle of pages, a cursor that says where to resume next time, and possibly a list of deleted records. This file is the adapter between those two worlds.

The main class, ConnectorBackend, first asks the authentication proxy for a credential. That matters because secrets must stay in the trusted sync job and must not leak into logs, sandboxes, or agents. It then finds the requested stream, calls the connector, and turns every provider record into a Page with a stable reference, title, body, and timestamps.

The tricky part is stopping long incremental syncs without losing progress. A cursor is a bookmark for the next run. Some connectors provide their own bookmark. If they do not, this backend creates its own small “envelope” that says: start again from the old position, skip records already consumed, and keep the best timestamp watermark seen so far. This is like rereading a long book from the same chapter but skipping the pages you already copied.

Full snapshot streams are different. They must read the whole collection so missing records can be tombstoned correctly, so this file deliberately does not cap those runs.

#### Function details

##### `ConnectorBackend.fetch`  (lines 106–199)

```
async def fetch(self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Runs one connector stream and converts its output into the sync result expected by the core source system. It also enforces safe progress limits for incremental backfills, while allowing full snapshots to finish completely so deletion detection stays correct.

**Data flow**: It receives a source config, an optional cursor bookmark, and source authentication context. It gets the right credential through the auth proxy, finds the requested stream, chooses the base URL, decodes any UFO-owned backfill cursor, then reads pages from the connector. Each record becomes a Page, deletions are collected, timestamp watermarks are advanced, and long incremental runs may be cut into bounded chunks. It returns a SyncResult containing pages, the next cursor, delete references, and whether this run was a full snapshot.

**Call relations**: This is the central method the source runner uses when it wants this connector-backed source to sync. Inside the run, it asks ConnectorBackend._stream to locate the stream, ConnectorBackend._decode_cursor to understand UFO’s own resume envelope, ConnectorBackend._page to render records into source pages, and _max_str to keep the newest cursor-field value. If a run must stop early, it creates a backfill envelope and may warn through the observability system when a provider is not advancing.

*Call graph*: calls 4 internal fn (_decode_cursor, _page, _stream, _max_str); 4 external calls (__init__, __init__, dumps, warn).


##### `ConnectorBackend._stream`  (lines 201–205)

```
def _stream(self, name: str) -> StreamSpec
```

**Purpose**: Finds the stream definition with the requested name inside the connector. This protects the fetch path from silently syncing the wrong thing when a source row is misconfigured.

**Data flow**: It receives a stream name and reads the connector’s available stream specifications. If one has the matching name, that StreamSpec comes out. If none match, it raises an error that names both the connector and the missing stream.

**Call relations**: ConnectorBackend.fetch calls this near the start of a sync run, before any provider records are read. The returned stream definition tells fetch which primary key, cursor field, deletion behavior, and timestamp fields to use for the rest of the run.

*Call graph*: called by 1 (fetch).


##### `ConnectorBackend._decode_cursor`  (lines 208–225)

```
def _decode_cursor(cursor: str | None) -> '_BackfillEnvelope | None'
```

**Purpose**: Checks whether a stored cursor is one of UFO’s own backfill envelopes. If it is not, the cursor is treated as connector-owned data and left alone.

**Data flow**: It receives a cursor string or nothing. If there is no cursor, or if the string is not JSON, or if the JSON does not contain the reserved UFO backfill key, it returns nothing. If the reserved key is present, it validates the saved origin, skip count, and watermark and returns them as a backfill envelope. If the reserved data is malformed, it raises an error because this backend is supposed to be the only writer of that format.

**Call relations**: ConnectorBackend.fetch calls this only for incremental streams. Its result decides whether fetch starts normally from the cursor or replays from an older origin while skipping records that were already landed in earlier capped runs.

*Call graph*: called by 1 (fetch); 1 external calls (loads).


##### `ConnectorBackend._page`  (lines 227–252)

```
def _page(self, stream: StreamSpec, record: dict[str, Any]) -> Page
```

**Purpose**: Turns one raw provider record into a UFO Page, which is the recallable unit the rest of the source system stores and searches. It gives the page a stable identity so repeated fetches, updates, and deletes all point at the same item.

**Data flow**: It receives a stream definition and one record dictionary. It builds a stable record reference, asks the connector to render a human-readable title and body, extracts normalized created and updated timestamps, and returns a Page containing those fields plus the stream name and source reference.

**Call relations**: ConnectorBackend.fetch calls this for each record that should be landed after any backfill skipping. To do its job, it relies on _record_ref for the page identity and _record_timestamp for safe timestamp conversion before constructing the Page.

*Call graph*: calls 2 internal fn (_record_ref, _record_timestamp); called by 1 (fetch); 1 external calls (__init__).


##### `_record_timestamp`  (lines 255–286)

```
def _record_timestamp(record: dict[str, Any], field: str | None, *, connector: str, stream: str) -> str | None
```

**Purpose**: Extracts and normalizes a timestamp from a provider record. If the timestamp is missing or malformed, it returns nothing and emits a warning instead of letting one bad date break the whole sync.

**Data flow**: It receives a record, the configured timestamp field name, and labels for the connector and stream. If no field is configured, it returns nothing. Otherwise it reads the field directly or through a nested path, accepts strings and non-boolean integers, and passes them through the shared timestamp normalizer. If parsing fails or the value has the wrong type, it warns with enough context to diagnose the bad field and returns nothing.

**Call relations**: ConnectorBackend._page calls this when building created_at and updated_at values for a Page. It uses get_path when the field name points inside nested data, normalize_page_timestamp to produce the source system’s standard timestamp shape, and the warning logger when provider data does not match expectations.

*Call graph*: called by 1 (_page); 3 external calls (warn, get_path, normalize_page_timestamp).


##### `_record_ref`  (lines 289–293)

```
def _record_ref(stream: StreamSpec, record: dict[str, Any]) -> str
```

**Purpose**: Builds a stable reference for a provider record. This reference is used so the same outside item maps to the same UFO page across sync runs.

**Data flow**: It receives a stream definition and a record. It first looks for the stream’s declared primary key. If that key is a string or integer, it returns it as text. If the primary key is missing or unusable, it serializes the whole record in a consistent order and returns a SHA-256 hash, which is a fixed-length fingerprint of the record contents.

**Call relations**: ConnectorBackend._page calls this before creating the Page. The reference it returns becomes part of the page’s source_ref, which is also the shape used by deletion entries collected during ConnectorBackend.fetch.

*Call graph*: called by 1 (_page); 2 external calls (sha256, dumps).


##### `_max_str`  (lines 296–301)

```
def _max_str(current: str | None, value: Any) -> str | None
```

**Purpose**: Keeps the greatest string value seen so far, used here as a simple watermark for incremental syncing. A watermark is a bookmark based on record data, often a timestamp or sortable ID.

**Data flow**: It receives the current saved string and a new value from a record. If the new value is not a string, it leaves the current value unchanged. If there is no current value, or the new string sorts after it, the new string becomes the result.

**Call relations**: ConnectorBackend.fetch calls this while reading records from a stream that has a cursor field. The updated watermark may become the next cursor for a completed incremental run, or be stored inside a UFO backfill envelope when the run is capped and must resume later.

*Call graph*: called by 1 (fetch).


### `core/src/ufo/sources/sync.py`

`orchestration` · `background sync and downstream indexing feed`

This file solves a practical problem: UFO needs to keep copies of documents from many places, such as a local folder or external connector, without every source needing to know how UFO stores and indexes data. It defines a common source backend shape: a backend receives its own typed settings, an optional cursor that marks where the last sync stopped, and workspace authentication information, then returns pages it found.

The main worker is SyncDriver. On each background tick, it finds sources that are due, claims them so two workers do not sync the same source at once, asks the right backend to fetch documents, writes changed document bodies to the blob store, and updates page rows in the database. It avoids needless work by comparing content digests, which are fingerprints of page bodies. If a full scan says a page is gone, or a delta source explicitly reports a deletion, the page is marked as a tombstone rather than simply disappearing. That gives indexers a clear delete signal.

The file also defines PageFeed, the doorway used by downstream indexers. It replays changed pages in revision order, like reading a numbered change log, so an indexer can stop and later resume from a cursor without missing or repeating work.

#### Function details

##### `normalize_page_timestamp`  (lines 51–71)

```
def normalize_page_timestamp(value: str) -> str
```

**Purpose**: Turns a page timestamp from a source into one standard UTC timestamp string. It accepts either numeric Unix-style times or ISO date strings, and rejects ambiguous times that do not say what timezone they are in.

**Data flow**: It receives a timestamp as text. If the text is all digits, it treats it as seconds or milliseconds since 1970; otherwise it parses it as an ISO date/time. It then converts the result to UTC with microsecond precision and returns that normalized string, or raises an error if the input cannot be trusted.

**Call relations**: Page.normalize_timestamp calls this when a Page is built or validated. That means source backends can pass several common timestamp formats, but the rest of the system always sees one consistent form.

*Call graph*: called by 1 (normalize_timestamp); 2 external calls (fromisoformat, fromtimestamp).


##### `Page.digest`  (lines 87–88)

```
def digest(self) -> str
```

**Purpose**: Creates a stable fingerprint for a page's body text. UFO uses this to tell whether a document's content really changed since the last sync.

**Data flow**: It reads the page body, encodes it as bytes, runs a SHA-256 hash over it, and returns the hash with a 'sha256:' prefix. It does not change the page.

**Call relations**: SyncDriver._commit reads this property while comparing fetched pages with already stored pages. If the digest is unchanged, the driver can skip uploading the body again.

*Call graph*: 1 external calls (sha256).


##### `Page.normalize_timestamp`  (lines 92–95)

```
def normalize_timestamp(cls, value: str | None) -> str | None
```

**Purpose**: Validates and standardizes the optional created_at and updated_at fields on a fetched page. This keeps provider-specific date formats from leaking into later parts of the system.

**Data flow**: It receives either a timestamp string or nothing. Empty values stay empty; real strings are passed through normalize_page_timestamp and come back as UTC ISO strings.

**Call relations**: Pydantic, the data validation library used for Page, calls this automatically when Page objects are created. It hands timestamp cleanup to normalize_page_timestamp.

*Call graph*: calls 1 internal fn (normalize_page_timestamp).


##### `StreamSkipped.__init__`  (lines 127–129)

```
def __init__(self, reason: str) -> None
```

**Purpose**: Stores the human reason why a source stream was skipped instead of treated as a failure. A connector uses this when the provider refuses access for an expected reason, such as a missing permission or plan limit.

**Data flow**: It receives a reason string, passes that message to the normal exception machinery, and also saves it on the exception as reason. The exception itself is the output signal.

**Call relations**: Many extension connectors raise this while paginating provider data. SyncDriver.run catches it and calls SyncDriver._skip, so existing pages are left alone and the source is simply tried again later.

*Call graph*: called by 49 (paginate, paginate, paginate, paginate, _org_stream, paginate, paginate, paginate, paginate, paginate (+15 more)).


##### `SourceBackend.config_model`  (lines 169–169)

```
def config_model(self) -> type[ConfigT]
```

**Purpose**: Defines the type of configuration a source backend expects. This prevents the core sync code from passing an unstructured bag of settings to connectors.

**Data flow**: A backend exposes a Pydantic model class through this property. SyncDriver._fetch reads it and uses it to validate the source row's stored JSON config before fetching.

**Call relations**: SourceBackend is a protocol, meaning it describes the shape that real backends must follow. SyncDriver._fetch relies on this property before calling SourceBackend.fetch.


##### `SourceBackend.fetch`  (lines 171–171)

```
async def fetch(self, config: ConfigT, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Defines the one operation every source backend must provide: fetch the current or next batch of documents. It is the seam between UFO core and local or external content providers.

**Data flow**: It receives typed source configuration, the previous cursor if any, and workspace authentication context. It returns a SyncResult containing fetched pages, the next cursor, and deletion information, or raises a special exception when the cursor is invalid or the stream should be skipped.

**Call relations**: SyncDriver._fetch calls this on whichever backend matches the source row. FolderSource and extension connectors implement this contract.


##### `FolderSource.fetch`  (lines 185–196)

```
async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Reads every file from a configured local folder and turns each file into a Page. It is the built-in source backend for local directories.

**Data flow**: It receives folder configuration, ignores the cursor and auth values, reads the directory on a worker thread so file I/O does not block the async loop, builds one Page per file, and returns a SyncResult marked as a full snapshot.

**Call relations**: SyncDriver._fetch calls this through the SourceBackend interface when a source uses the folder backend. It delegates the actual directory walk to FolderSource._read.

*Call graph*: 4 external calls (__init__, __init__, to_thread, Path).


##### `FolderSource._read`  (lines 199–206)

```
def _read(root: Path) -> tuple[tuple[str, str], ...]
```

**Purpose**: Performs the blocking file-system work for FolderSource.fetch. It lists files under the root folder and reads each file as UTF-8 text.

**Data flow**: It receives a folder path. If the path is not an existing directory, it raises FileNotFoundError; otherwise it returns pairs of relative file path and file contents, sorted by path.

**Call relations**: FolderSource.fetch runs this inside asyncio.to_thread, which is like moving a slow chore to a side worker so the main async process can keep going.

*Call graph*: 2 external calls (is_dir, rglob).


##### `source_row_id`  (lines 209–221)

```
def source_row_id(workspace_id: UUID, backend: str, config: Mapping[str, object], *, connection_id: UUID | None=None) -> UUID
```

**Purpose**: Creates a deterministic database ID for a source. The same workspace, backend, config, and optional connection generation always produce the same ID, which prevents duplicate source rows after restart.

**Data flow**: It receives workspace ID, backend name, config values, and optionally a connection ID. It serializes the config in sorted order, combines the pieces into a stable string, and turns that into a UUID.

**Call relations**: register_sources calls this while creating configured source rows. Because the ID is repeatable, registration can safely run at startup more than once.

*Call graph*: called by 1 (register_sources); 2 external calls (dumps, uuid5).


##### `page_id_for`  (lines 224–227)

```
def page_id_for(source_id: UUID, source_ref: str) -> UUID
```

**Purpose**: Creates a deterministic page ID for one source document. This lets updates, refetches, and delete notices all point to the same database row.

**Data flow**: It receives a source ID and the document's source_ref, which is the stable key inside that source. It combines them and returns a UUID derived from that combination.

**Call relations**: SyncDriver._commit calls this for every fetched page and every explicit delete. It is how the sync logic lines up incoming source records with existing page rows.

*Call graph*: called by 1 (_commit); 1 external calls (uuid5).


##### `register_sources`  (lines 230–264)

```
async def register_sources(configured: tuple[SourceEntry, ...]) -> None
```

**Purpose**: Ensures that sources listed in configuration exist in the database. It is used at boot so configured sources are ready for the sync worker.

**Data flow**: It receives configured source entries. It opens a workspace database transaction, finds the workspace ID, computes the stable source ID for each entry, checks whether that row already exists, and inserts a new source row only when needed.

**Call relations**: Startup code calls this outside the regular sync polling loop. It uses source_row_id to make the operation idempotent, meaning repeating it does not create duplicates.

*Call graph*: calls 1 internal fn (source_row_id); 4 external calls (now, insert, select, workspace_tx).


##### `SyncDriver.candidate_workspaces`  (lines 312–332)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds which workspaces currently have sources due for syncing. This avoids opening per-workspace work for tenants that have nothing ready.

**Data flow**: It reads the owner-level database view, looks for non-removed sources whose next_sync_at time has arrived and whose claim is empty or expired, and returns the distinct workspace IDs.

**Call relations**: A higher-level scheduler or dispatcher can call this before binding a SyncDriver run to a workspace. It uses owner_tx because it needs a cross-workspace look without normal workspace filtering.

*Call graph*: 4 external calls (now, or_, select, owner_tx).


##### `SyncDriver.run`  (lines 334–357)

```
async def run(self) -> None
```

**Purpose**: Runs one sync pass for due sources in the current workspace. It claims work, fetches documents, commits successful results, and records skips or failures correctly.

**Data flow**: It creates a unique claim token, asks _claim_due for sources it may work on, then processes each source one at a time. Successful fetches go to _commit; skipped streams go to _skip; failures go to _release, possibly clearing a dead cursor.

**Call relations**: This is the main method called by the background source sync job. It coordinates _claim_due, _fetch, _commit, _skip, and _release, and logs skipped or failed runs for observability.

*Call graph*: calls 5 internal fn (_claim_due, _commit, _fetch, _release, _skip); 2 external calls (log, uuid4).


##### `SyncDriver._claim_due`  (lines 359–408)

```
async def _claim_due(self, claim: str) -> tuple[ClaimedSource, ...]
```

**Purpose**: Reserves a batch of due sources for this worker. This stops another worker from syncing the same source at the same time.

**Data flow**: It receives a claim token, queries due and unclaimed or expired sources, optionally uses database row locking on Postgres, writes the claim and lease expiration time back to those source rows, and returns ClaimedSource objects.

**Call relations**: SyncDriver.run calls this at the start of a pass. The returned claim token is later checked by _write, _release, and _skip so only the worker that claimed the source can finish or release it.

*Call graph*: called by 1 (run); 7 external calls (__init__, now, timedelta, or_, select, update, workspace_tx).


##### `SyncDriver._fetch`  (lines 410–429)

```
async def _fetch(self, source: ClaimedSource) -> SyncResult
```

**Purpose**: Calls the correct backend for a claimed source and prepares the authentication context it needs. This is where a generic source row becomes a typed backend fetch.

**Data flow**: It receives a ClaimedSource, finds the matching backend, validates the stored config with that backend's model, resolves optional identity and credential helpers, builds SourceAuth, and awaits the backend's fetch result.

**Call relations**: SyncDriver.run calls this after claiming a source. It hands off to the backend's fetch method, which may be FolderSource.fetch or an extension connector.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `SyncDriver._commit`  (lines 431–471)

```
async def _commit(self, source: ClaimedSource, result: SyncResult) -> None
```

**Purpose**: Decides what changed in a fetched result before writing database updates. It separates new or changed bodies, metadata-only changes, fetched IDs, and deletions.

**Data flow**: It receives a claimed source and a SyncResult. It loads prior page state, gives each fetched page a stable ID, compares digests and browse metadata, uploads changed bodies to the blob store, builds change lists, converts delete refs to page IDs, and passes everything to _write.

**Call relations**: SyncDriver.run calls this after a successful fetch. It uses _prior_pages for comparison, page_id_for for stable IDs, and _write for the actual database transaction.

*Call graph*: calls 3 internal fn (_prior_pages, _write, page_id_for); called by 1 (run); 2 external calls (__init__, __init__).


##### `SyncDriver._prior_pages`  (lines 473–505)

```
async def _prior_pages(self, source_id: UUID) -> dict[UUID, tuple[str, bool, PageBrowse]]
```

**Purpose**: Loads the current stored state of all pages for a source. This gives _commit the before-picture it needs to avoid rewriting unchanged content.

**Data flow**: It receives a source ID, reads matching page rows from the workspace database, and returns a dictionary keyed by page ID containing digest, tombstone status, and browse metadata.

**Call relations**: SyncDriver._commit calls this before comparing fetched pages. It builds PageBrowse objects so metadata comparisons use the same shape as newly fetched pages.

*Call graph*: called by 1 (_commit); 3 external calls (__init__, select, workspace_tx).


##### `SyncDriver._write`  (lines 507–621)

```
async def _write(self, source: ClaimedSource, next_cursor: str | None, changed: list[ChangedPage], metadata: list[PageBrowse], fetched: list[UUID], deleted: list[UUID], snapshot: bool) -> None
```

**Purpose**: Persists the result of one successful fetch in a single workspace transaction. It updates page rows, tombstones deletions, advances the source cursor, and schedules the next normal sync.

**Data flow**: It receives the claimed source, next cursor, changed pages, metadata-only updates, fetched page IDs, deleted page IDs, and whether the fetch was a full snapshot. It verifies the source is still claimed by this worker, writes changed page rows or inserts new ones, updates metadata, marks deleted or missing snapshot pages as tombstones, fixes subject changes, then clears the claim and resets the error count.

**Call relations**: SyncDriver._commit calls this after preparing the write lists. PageFeed later reads the page rows this function changes, using database revisions assigned during these writes as the ordered change stream.

*Call graph*: called by 1 (_commit); 6 external calls (now, timedelta, insert, select, update, workspace_tx).


##### `SyncDriver._release`  (lines 623–651)

```
async def _release(self, source: ClaimedSource, cursor_reset: bool) -> None
```

**Purpose**: Releases a source after a real failure and schedules a retry with backoff. Backoff means waiting longer after repeated failures so UFO does not hammer a broken provider.

**Data flow**: It receives the claimed source and a flag saying whether the cursor should be cleared. It increments the consecutive error count, computes a capped retry delay, updates the source row with the new next_sync_at time, clears the claim, and optionally resets the cursor.

**Call relations**: SyncDriver.run calls this when _fetch or _commit raises an ordinary error. If the error is CursorExpired, run asks _release to clear the stored cursor so the next attempt starts fresh.

*Call graph*: called by 1 (run); 4 external calls (now, timedelta, update, workspace_tx).


##### `SyncDriver._skip`  (lines 653–674)

```
async def _skip(self, source: ClaimedSource) -> None
```

**Purpose**: Releases a source after a non-failure skip, such as a missing permission or provider plan gate. It preserves existing pages and does not treat the run as an error.

**Data flow**: It receives the claimed source, updates the source row to run again at the normal interval, resets the error count, and clears the claim. It does not write pages or change the cursor.

**Call relations**: SyncDriver.run calls this only after catching StreamSkipped. This keeps snapshot deletion logic from running, so a temporarily unreadable stream does not wipe existing indexed pages.

*Call graph*: called by 1 (run); 4 external calls (now, timedelta, update, workspace_tx).


##### `PageFeed.pages_changed_since`  (lines 714–714)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: Defines how an indexer asks for page changes after a saved cursor. It is the read-side contract for downstream systems that need to keep their own index in sync.

**Data flow**: It receives a cursor and a requested limit. Implementations return a PageBatch containing ordered page changes and a next cursor to save for later.

**Call relations**: PageFeed is a protocol used by extension contexts. CorePageFeed implements it with database reads and blob-store body loading.


##### `page_cursor`  (lines 717–726)

```
def page_cursor(cursor: object) -> tuple[int, UUID]
```

**Purpose**: Parses and validates a page-feed cursor. The cursor marks the last seen database revision and page ID.

**Data flow**: It receives an arbitrary cursor value. It requires a string shaped like 'revision|page_uuid', converts the revision to an integer and the page ID to a UUID, and returns both; invalid input raises ValueError.

**Call relations**: CorePageFeed.pages_changed_since calls this when a caller supplies a cursor. The parsed values become the lower bound for the next database query.

*Call graph*: called by 1 (pages_changed_since); 1 external calls (UUID).


##### `CorePageFeed.pages_changed_since`  (lines 738–796)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: Reads changed pages from core storage for an indexer, in a safe and repeatable order. It includes page bodies for live pages and empty bodies for tombstones.

**Data flow**: It receives an optional cursor and a limit. It builds a database query ordered by revision and page ID, applies the cursor if present, caps the batch size, reads page rows, loads each non-tombstoned body from the blob store, creates PageChange objects, and returns them with a cursor pointing at the last row read.

**Call relations**: This is the concrete implementation of PageFeed. Downstream indexers call it through the extension context, and it uses page_cursor to resume from exactly where the previous batch stopped.

*Call graph*: calls 1 internal fn (page_cursor); 7 external calls (__init__, __init__, fromisoformat, and_, or_, select, workspace_tx).

## 📊 State Registers Touched

- `reg-database-schema` — The shared database layout and migration version that define which long-term records the system can store.
- `reg-workspace-boundary` — The current workspace or tenant boundary used to keep each customer’s data and actions separate.
- `reg-effective-configuration` — The chosen runtime settings that tell the service how this deployment should behave.
- `reg-pack-selection` — The selected product pack that decides which bundle of extensions, skills, and infrastructure is enabled.
- `reg-extension-inventory` — The installed extension set and their declared capabilities, such as tools, routes, jobs, skills, and storage.
- `reg-extension-store` — Per-workspace saved extension data that add-ons use to remember their own small pieces of state.
- `reg-credential-store` — The encrypted store of API keys, service secrets, and owner-provided credentials.
- `reg-workspace-objects` — The shared records for workspaces, agents, members, conversations, artifacts, memories, sources, and other workspace objects.
- `reg-connector-connections` — The saved external accounts, OAuth connections, and agent grants that let tools use outside services safely.
- `reg-source-sync-state` — The saved state of external sources, synced pages, deletion markers, cursors, and retry backoff.
- `reg-search-index` — The shared searchable index and chunk store built from synced or fetched content.
- `reg-memory-store` — The long-term memory store of remembered facts and recall results used to inform later responses.
- `reg-background-jobs` — The shared registry and saved queue of scheduled, recurring, delayed, and administrative background work.
- `reg-data-backend-catalog` — The resolved registry of non-model service backends such as search, indexing, memory, source, browser, sandbox, and blob providers made available by configuration and extensions.
- `reg-outbound-http-client-pool` — Shared outbound HTTP client/session pool state used for connection reuse, retries, and provider/API calls across model adapters, connectors, search, tools, and billing jobs.
- `reg-evaluation-environment-state` — Persisted synthetic evaluation-environment data, such as fake email and calendar records, used by evaluation connectors and replay/test workflows without touching real external services.
