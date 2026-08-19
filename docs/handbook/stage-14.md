# Source synchronization, indexing, search, research, and memory recall  `stage-14`

This stage is the system’s knowledge supply chain. It runs mostly behind the scenes before and during conversations, so the assistant can use up-to-date company data, web research, and remembered facts instead of relying only on its built-in training.

The syncing part connects to outside services such as Gmail, Slack, GitHub, Notion, Salesforce, and many others. Each connector knows how to ask its service for records, page through long lists of results, and turn them into stored “pages” the system can read. The shared sync engine registers these sources, saves changed document bodies, marks missing documents as deleted, and tells later steps what needs re-indexing.

The indexing and retrieval part works like a library catalog. It breaks documents into smaller text chunks, creates embeddings, which are number-based summaries of meaning, and stores them for keyword or meaning-based search. Memory code keeps durable facts, cleans duplicates, and records when memories are recalled.

Research support adds outside web search and page fetching through a shared search shape, so the rest of the system can request research results without caring which search provider supplies them.

## Sub-stages

- [Connector-backed source sync](stage-14.1.md) `stage-14.1` — 55 files
- [Indexing, retrieval, and memory extraction](stage-14.2.md) `stage-14.2` — 11 files

## Files in this stage

### Source sync interfaces
Defines the shared search abstraction and the core synchronization flow that fetches, stores, deletes, and publishes source document changes.

### `core/src/ufo/search.py`

`data_model` · `cross-cutting during boot setup and research tool calls`

This file is a contract between the core system and any web search extension. The core project deliberately does not contain a built-in search engine or hold search API keys. Instead, an extension supplies a search provider, and this file says what that provider must accept and return.

The small data classes describe the pieces passed across that boundary. A `SearchQuery` is the request: the user’s search intent, how many results are wanted, optional date limits, allowed websites, and an optional category such as academic or image search. A `SearchResults` object comes back with ranked `SearchHit` entries, and sometimes a direct answer if the search backend can produce one. For reading a specific webpage, `FetchRequest` describes the URL and optional extraction instructions, while `FetchedPage` is the cleaned page text and optional summary.

The `SearchProvider` protocol is the key seam. A protocol is like a checklist: any backend can be used if it provides the listed abilities. The core can call `search`, and it can call `fetch` only when `supports_fetch` says that fetching is available. This separation matters because it keeps credentials and backend-specific details outside the core and away from the sandboxed tool environment.

#### Function details

##### `SearchProvider.supports_fetch`  (lines 82–82)

```
def supports_fetch(self) -> bool
```

**Purpose**: This property tells the rest of the system whether this search provider can also retrieve and extract the contents of a webpage. It prevents tools from asking a provider to fetch a page when that provider only supports search results.

**Data flow**: The caller reads this value from a provider instance. The provider reports a simple true-or-false answer. Nothing is changed; the result is used to decide whether fetching is allowed.

**Call relations**: Research tools check this capability before trying to fetch a URL. If it is true, the flow can continue to `SearchProvider.fetch`; if it is false, the tool should avoid that call and report that fetching is not supported.


##### `SearchProvider.search`  (lines 84–84)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: This asynchronous method runs a web search using a `SearchQuery` and returns structured search results. Someone uses it when the system needs outside information from the web without caring which search company or service provides it.

**Data flow**: A `SearchQuery` goes in, containing the search wording, result count, and optional filters such as date range or allowed domains. The provider sends that request to its own backend and turns the backend response into `SearchResults`. The output is a ranked set of hits, plus possibly a direct answer.

**Call relations**: The research extension calls this through the current turn’s tool context. The concrete provider behind the protocol does the real backend work, while core code only relies on the common request and response shapes defined here.


##### `SearchProvider.fetch`  (lines 86–86)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: This asynchronous method retrieves the readable text from a single URL and may also return a summary guided by a prompt. It is used when the system needs more than a search result snippet and wants the content of a specific page.

**Data flow**: A `FetchRequest` goes in, carrying the URL and optional instructions such as a prompt, maximum returned text length, or whether to bypass cached content. The provider fetches and extracts the page through its backend. A `FetchedPage` comes out with the URL, extracted text, and possibly a summary.

**Call relations**: This is called only after the caller has checked `SearchProvider.supports_fetch`. In the larger research flow, a fetch tool receives a URL, confirms that the selected provider can fetch, and then hands the request to this method for the provider-specific work.


### `core/src/ufo/sources/sync.py`

`orchestration` · `startup registration, scheduled sync runs, and downstream page-change replay`

This file solves a practical problem: outside content changes over time, and the system needs a safe, repeatable way to keep its internal page records up to date. A source backend is like a delivery route. Each backend knows how to fetch documents from one kind of place, such as a local folder or an external connector. The core sync driver does not need to know each provider’s details; it just asks the backend for pages and a cursor, which is a bookmark for where to resume next time.

The sync flow is careful. First, it finds sources that are due and claims them so two workers do not sync the same source at once. Then it validates the source’s typed configuration, fetches pages, writes new or changed page bodies to the blob store, and updates the database rows for pages. If a full snapshot says a page is gone, or an incremental fetch explicitly names a deletion, the page is tombstoned, meaning it is marked deleted without losing the historical row.

Failures are treated differently depending on meaning. A real error backs off future retries. An expired cursor is cleared so the next run can start fresh. A skipped stream is rescheduled normally without deleting anything. Finally, PageFeed lets indexers replay changed pages in a stable order, like reading a ledger of updates.

#### Function details

##### `SourceRowConfig.requested_fields`  (lines 73–76)

```
def requested_fields(cls) -> frozenset[str]
```

**Purpose**: This tells callers which non-identity configuration fields must still match when a source is registered again. It separates fields the user actually requested from fields that were automatically resolved later.

**Data flow**: It reads the class-level sets of non-identity fields and resolved fields. It subtracts the resolved ones from the non-identity ones, then returns the remaining field names as a frozen set.

**Call relations**: It belongs to source configuration models. Registration code can use this idea to decide whether a new registration describes the same dataset request without treating automatically resolved values as user input.


##### `normalize_page_timestamp`  (lines 79–99)

```
def normalize_page_timestamp(value: str) -> str
```

**Purpose**: This converts page timestamps into one consistent UTC format. It accepts numeric Unix-style times or ISO date strings, and rejects ambiguous timestamps that do not say what timezone they are in.

**Data flow**: A string timestamp goes in. The function parses it as seconds or milliseconds since 1970 if it is all digits, otherwise as an ISO date/time string. It checks timezone safety, converts the moment to UTC, and returns a standardized string with microsecond precision.

**Call relations**: Page.normalize_timestamp calls this when a Page is built or validated. That keeps all source backends from storing timestamps in mixed formats.

*Call graph*: called by 1 (normalize_timestamp); 2 external calls (fromisoformat, fromtimestamp).


##### `Page.digest`  (lines 115–116)

```
def digest(self) -> str
```

**Purpose**: This gives a page body a stable fingerprint. The sync driver uses that fingerprint to avoid rewriting pages whose text has not changed.

**Data flow**: It reads the page body text, encodes it as bytes, runs SHA-256 over it, and returns a string beginning with "sha256:" followed by the hash.

**Call relations**: SyncDriver._commit reads this property while comparing fetched pages with prior database rows. If the digest is unchanged, the driver can skip rewriting the body.

*Call graph*: 1 external calls (sha256).


##### `Page.normalize_timestamp`  (lines 120–123)

```
def normalize_timestamp(cls, value: str | None) -> str | None
```

**Purpose**: This validates and standardizes the optional created_at and updated_at fields on a page. It makes sure any timestamp attached to a page is safe to compare later.

**Data flow**: A timestamp string or null goes in. Null stays null. A real string is passed to normalize_page_timestamp, and the normalized UTC string comes back.

**Call relations**: Pydantic, the data validation library used by the model, calls this when Page objects are created. It delegates the actual parsing rules to normalize_page_timestamp.

*Call graph*: calls 1 internal fn (normalize_page_timestamp).


##### `StreamSkipped.__init__`  (lines 155–157)

```
def __init__(self, reason: str) -> None
```

**Purpose**: This records the human-readable reason a source stream was skipped rather than failed. It is used when a provider refuses access in an expected way, such as a missing permission or plan limit.

**Data flow**: A reason string goes in. The exception is initialized with that text and also stores it on the object as reason for later logging.

**Call relations**: Many connector backends raise this while fetching provider data. SyncDriver.run catches it, logs a skipped sync, and reschedules the source without deleting pages or increasing the error count.

*Call graph*: called by 48 (paginate, paginate, paginate, paginate, _org_stream, paginate, paginate, paginate, paginate, paginate (+15 more)).


##### `validation_fault`  (lines 160–166)

```
def validation_fault(error: ValidationError) -> str
```

**Purpose**: This turns a validation error into a safe summary for logs. It describes which fields failed and why, without including the rejected values themselves.

**Data flow**: A Pydantic ValidationError goes in. The function reads each error’s field path and error type, joins them into short messages, and returns one combined string.

**Call relations**: SyncDriver._report_failed uses this when bad source data or bad configuration fails validation. It lets operators see the shape of the problem without leaking provider payloads or user-supplied secrets.

*Call graph*: called by 1 (_report_failed); 1 external calls (errors).


##### `StreamFault.__init__`  (lines 176–178)

```
def __init__(self, reason: str) -> None
```

**Purpose**: This records a provider-data problem that a backend understands well enough to explain safely. It means the provider returned data in a shape the stream could not read.

**Data flow**: A reason string goes in. The exception is initialized and the same reason is stored for later reporting.

**Call relations**: Connector code can raise this during fetch. SyncDriver.run treats it as a real failure, and SyncDriver._report_failed includes the backend-authored reason as the safe provider fault.

*Call graph*: called by 1 (_sheet_value_records).


##### `SourceBackend.config_model`  (lines 218–218)

```
def config_model(self) -> type[ConfigT]
```

**Purpose**: This is the protocol requirement saying every source backend must name the typed configuration model it expects. That keeps source settings structured instead of being an unchecked dictionary.

**Data flow**: A backend exposes a model class. The sync driver uses that class to validate the stored source config before fetching.

**Call relations**: SyncDriver._fetch relies on this property before calling SourceBackend.fetch. Implementations such as FolderSource provide the concrete model.


##### `SourceBackend.fetch`  (lines 220–220)

```
async def fetch(self, config: ConfigT, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This is the protocol requirement for fetching documents from a source. Each backend implements it to return the current pages, a resume cursor, and any deletions it knows about.

**Data flow**: The backend receives a validated config, the previously stored cursor, and SourceAuth describing the workspace and credential access. It returns a SyncResult containing fetched pages, the next cursor, deletion information, and whether the fetch was a full snapshot.

**Call relations**: SyncDriver._fetch calls this after preparing config and auth. Concrete backends, including FolderSource and connector extensions, provide the provider-specific behavior.


##### `FolderSource.fetch`  (lines 234–245)

```
async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This reads a local folder and turns every file into a page. It is the built-in source backend for simple filesystem content.

**Data flow**: It receives a SourceConfig with a root path, ignores cursor and auth, reads the folder on a worker thread, creates one Page per file using the relative path as both key and title, and returns a full-snapshot SyncResult.

**Call relations**: SyncDriver._fetch calls this through the SourceBackend interface when a source row uses the folder backend. It hands the actual disk scan to FolderSource._read.

*Call graph*: 4 external calls (__init__, __init__, to_thread, Path).


##### `FolderSource._read`  (lines 248–255)

```
def _read(root: Path) -> tuple[tuple[str, str], ...]
```

**Purpose**: This performs the blocking filesystem scan for FolderSource. It reads all files under a root directory as UTF-8 text.

**Data flow**: A filesystem Path goes in. The function checks that it is a directory, walks all files below it in sorted order, reads each file’s bytes, decodes them as UTF-8, and returns pairs of relative path and text.

**Call relations**: FolderSource.fetch runs this in a background thread so the asynchronous sync loop is not blocked by disk I/O. If the folder is missing, the error causes the sync to fail rather than accidentally deleting all pages.

*Call graph*: 2 external calls (is_dir, rglob).


##### `source_row_id`  (lines 258–278)

```
def source_row_id(workspace_id: UUID, backend: str, config: Mapping[str, object], *, connection_id: UUID | None=None, non_identity_keys: frozenset[str]=frozenset()) -> UUID
```

**Purpose**: This creates the stable database id for a source row. It prevents duplicate source rows when the same configured source is registered again after a restart.

**Data flow**: It receives the workspace id, backend name, config values, optional connection id, and any config keys that should not count as identity. It removes non-identity keys, serializes the remaining config in a stable order, and returns a deterministic UUID.

**Call relations**: register_sources calls this while inserting configured sources. Because the id is deterministic, the same source settles on the same row instead of creating a new one.

*Call graph*: called by 1 (register_sources); 2 external calls (dumps, uuid5).


##### `page_id_for`  (lines 281–284)

```
def page_id_for(source_id: UUID, source_ref: str) -> UUID
```

**Purpose**: This creates the stable database id for one page inside one source. It lets updates and deletes for the same source document find the same row every time.

**Data flow**: A source id and source-specific page reference go in. The function combines them into a deterministic UUID and returns it.

**Call relations**: SyncDriver._commit uses this for every fetched page and every explicit delete reference. That makes upserts, unchanged refetches, and tombstones all target the same page row.

*Call graph*: called by 1 (_commit); 1 external calls (uuid5).


##### `register_sources`  (lines 287–352)

```
async def register_sources(configured: tuple[SourceEntry, ...]) -> None
```

**Purpose**: This makes sure sources listed in configuration exist in the database. It is mainly a startup step so configured sources are ready for the sync job.

**Data flow**: A tuple of configured source entries goes in. The function opens a workspace database transaction, finds the workspace and main agent, computes each source’s stable id, inserts missing live source rows, and grants the main agent access to them. It skips already removed rows rather than resurrecting them.

**Call relations**: This runs outside the polling sync loop. It uses source_row_id to avoid duplicates and writes source and source_grant rows that SyncDriver later claims and syncs.

*Call graph*: calls 1 internal fn (source_row_id); 4 external calls (now, insert, select, workspace_tx).


##### `_rescheduled`  (lines 370–381)

```
def _rescheduled(claimed: ClaimedSource, when: datetime) -> sa.Case[datetime]
```

**Purpose**: This decides what next_sync_at should become when a sync finishes, without overwriting a newer resync request. It protects a request made during a running sync from being lost.

**Data flow**: It receives the claimed source and a proposed next run time. It builds a database expression: use the proposed time only if next_sync_at was not changed after the claim started; otherwise keep the newer database value.

**Call relations**: SyncDriver._write, SyncDriver._release, and SyncDriver._skip use this when freeing a claim. It is the shared rule that keeps normal completion, failure, and skip paths from stranding a requested resync.

*Call graph*: called by 3 (_release, _skip, _write); 1 external calls (case).


##### `_stream_tags`  (lines 384–389)

```
def _stream_tags(source: ClaimedSource) -> dict[str, str]
```

**Purpose**: This builds small labels for logs and metrics that identify the provider and stream involved in a sync. These labels make sync outcomes searchable and countable.

**Data flow**: A claimed source goes in. The function reads the backend name and the optional stream value from the source config, then returns them in a dictionary.

**Call relations**: SyncDriver.run uses it when logging skipped streams. SyncDriver._report_ok and SyncDriver._report_failed also use it so success and failure events carry the same provider-stream tags.

*Call graph*: calls 1 internal fn (_config_value); called by 3 (_report_failed, _report_ok, run).


##### `_config_value`  (lines 392–394)

```
def _config_value(source: ClaimedSource, key: str) -> str
```

**Purpose**: This safely reads a string field from a source’s config for logging and metrics. If the field is missing or not a string, it returns an empty string instead of leaking odd values.

**Data flow**: A claimed source and config key go in. The function looks up the value, returns it if it is a string, otherwise returns an empty string.

**Call relations**: _stream_tags uses it for the stream label. SyncDriver._report_ok and SyncDriver._report_failed use it for fields such as account id.

*Call graph*: called by 3 (_report_failed, _report_ok, _stream_tags).


##### `SyncDriver.candidate_workspaces`  (lines 428–448)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This finds workspaces that currently have at least one source due for syncing. It helps the scheduler avoid opening work for tenants that have nothing ready.

**Data flow**: It reads the current time, opens an owner-level database transaction, selects distinct workspace ids from live source rows that are due and not actively claimed, and returns those ids.

**Call relations**: A higher-level dispatcher can call this before binding a workspace and running SyncDriver.run. It is a fast pre-check across workspaces.

*Call graph*: 4 external calls (now, or_, select, owner_tx).


##### `SyncDriver.run`  (lines 450–469)

```
async def run(self) -> None
```

**Purpose**: This is the main sync loop for one driver tick. It claims due sources, fetches each one, commits changes, and handles skip or failure outcomes.

**Data flow**: It creates a unique claim token, asks _claim_due for sources, and processes each source one by one. Successful fetches go to _commit. Skipped streams go to _skip. Failures are classified, logged, backed off, and released through _release.

**Call relations**: This is the central coordinator in the file. It calls _claim_due, _fetch, _commit, _error_backoff, _report_failed, _release, and _skip, while using _stream_tags for skipped-stream logging.

*Call graph*: calls 8 internal fn (_claim_due, _commit, _error_backoff, _fetch, _release, _report_failed, _skip, _stream_tags); 4 external calls (suppress, now, log, uuid4).


##### `SyncDriver._claim_due`  (lines 471–521)

```
async def _claim_due(self, claim: str) -> tuple[ClaimedSource, ...]
```

**Purpose**: This reserves a batch of sources that are ready to sync. Claiming is the lock-like step that stops two workers from doing the same source at the same time.

**Data flow**: A claim token goes in. The function finds live due sources whose previous claim is absent or expired, optionally uses database row locking on PostgreSQL, writes the new claim and lease expiry, and returns ClaimedSource objects containing the source details.

**Call relations**: SyncDriver.run calls this at the start of a tick. The returned claim token is later checked by _write, _release, and _skip so only the worker that claimed the source can finish it.

*Call graph*: called by 1 (run); 7 external calls (__init__, now, timedelta, or_, select, update, workspace_tx).


##### `SyncDriver._fetch`  (lines 523–542)

```
async def _fetch(self, source: ClaimedSource) -> SyncResult
```

**Purpose**: This prepares everything a backend needs and asks it for source data. It is the bridge between generic core sync code and provider-specific backend code.

**Data flow**: A ClaimedSource goes in. The function finds the matching backend, validates the stored config with that backend’s config model, resolves optional identity and credential access, builds SourceAuth, and awaits backend.fetch. A SyncResult comes back.

**Call relations**: SyncDriver.run calls this after claiming a source. It calls the backend through the SourceBackend interface, so folder and connector backends can plug in without changing the driver.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `SyncDriver._commit`  (lines 544–585)

```
async def _commit(self, source: ClaimedSource, result: SyncResult) -> None
```

**Purpose**: This turns a backend’s fetched result into database and blob-store changes. It writes only pages whose content or browse metadata changed, and marks deletions when needed.

**Data flow**: A claimed source and SyncResult go in. The function loads prior page state, computes stable page ids, compares digests and metadata, writes changed bodies to the blob store, prepares changed and metadata-only lists, converts delete refs to page ids, calls _write, and reports success counts.

**Call relations**: SyncDriver.run calls this after _fetch succeeds. It depends on _prior_pages to compare old state, page_id_for to identify rows, _write to persist changes, and _report_ok to log the outcome.

*Call graph*: calls 4 internal fn (_prior_pages, _report_ok, _write, page_id_for); called by 1 (run); 2 external calls (__init__, __init__).


##### `SyncDriver._prior_pages`  (lines 587–619)

```
async def _prior_pages(self, source_id: UUID) -> dict[UUID, tuple[str, bool, PageBrowse]]
```

**Purpose**: This loads the current database state for all pages in a source. The commit step uses it to decide what is new, changed, unchanged, or already tombstoned.

**Data flow**: A source id goes in. The function reads page rows for that source from the workspace database and returns a dictionary keyed by page id, containing digest, tombstone flag, and browse metadata.

**Call relations**: SyncDriver._commit calls this before comparing fetched pages. It creates PageBrowse objects so the later comparison can treat page metadata as one simple value.

*Call graph*: called by 1 (_commit); 3 external calls (__init__, select, workspace_tx).


##### `SyncDriver._write`  (lines 621–743)

```
async def _write(self, source: ClaimedSource, next_cursor: str | None, changed: list[ChangedPage], metadata: list[PageBrowse], fetched: list[UUID], deleted: list[UUID], snapshot: bool) -> int
```

**Purpose**: This performs the database write for one fetched batch. It updates or inserts changed pages, applies metadata-only edits, tombstones deleted pages, and releases the source claim on success.

**Data flow**: It receives the claimed source, next cursor, changed pages, metadata-only pages, fetched page ids, explicit deleted page ids, and a snapshot flag. Inside a workspace transaction it verifies the claim is still valid, writes page rows, tombstones explicit deletes and snapshot-missing pages, updates live page subjects, updates the source cursor and next sync time, clears the claim, and returns the number of tombstoned pages.

**Call relations**: SyncDriver._commit calls this after preparing all changes. It uses _rescheduled so a newer resync request is not overwritten while setting the next normal sync time.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (_commit); 6 external calls (now, timedelta, insert, select, update, workspace_tx).


##### `SyncDriver._report_ok`  (lines 745–757)

```
def _report_ok(self, source: ClaimedSource, fetched: int, written: int, tombstoned: int) -> None
```

**Purpose**: This writes a success log entry for a source sync. It records how many pages were fetched, written, and tombstoned.

**Data flow**: It receives the source and count values. It builds provider, stream, and account labels, then emits a structured log entry; logging errors are suppressed so telemetry cannot break syncing.

**Call relations**: SyncDriver._commit calls this after _write succeeds. It uses _stream_tags and _config_value to keep success logs consistent with failure logs.

*Call graph*: calls 2 internal fn (_config_value, _stream_tags); called by 1 (_commit); 2 external calls (suppress, log).


##### `SyncDriver._error_backoff`  (lines 759–767)

```
def _error_backoff(self, source: ClaimedSource, now: datetime) -> tuple[int, datetime]
```

**Purpose**: This calculates how long to wait before retrying a source that failed. Repeated failures wait longer, up to a cap, so the system does not hammer a broken provider.

**Data flow**: It receives the source and the current time. It increments the consecutive error count, computes a doubling delay based on that count, caps the delay, and returns the new error count plus the next retry time.

**Call relations**: SyncDriver.run calls this when _fetch or _commit raises an error. Its results are passed to _report_failed for logging and _release for database updates.

*Call graph*: called by 1 (run); 1 external calls (timedelta).


##### `SyncDriver._report_failed`  (lines 769–816)

```
def _report_failed(self, source: ClaimedSource, error: Exception, cursor_reset: bool, errors: int, next_sync_at: datetime) -> None
```

**Purpose**: This records a sync failure in logs and metrics without exposing sensitive data. It reports the provider stream, error class, retry state, and a carefully sanitized provider fault when available.

**Data flow**: It receives the source, exception, cursor-reset flag, error count, and next retry time. It chooses a safe fault message depending on the exception type, trims it to a maximum length, logs the failure, and emits a failure metric. Telemetry errors are suppressed.

**Call relations**: SyncDriver.run calls this after calculating backoff. It uses _stream_tags, _config_value, and validation_fault to produce consistent, safe observability data.

*Call graph*: calls 3 internal fn (_config_value, _stream_tags, validation_fault); called by 1 (run); 4 external calls (suppress, isoformat, emit_metric, log_error).


##### `SyncDriver._release`  (lines 818–844)

```
async def _release(self, source: ClaimedSource, cursor_reset: bool, errors: int, next_sync_at: datetime) -> None
```

**Purpose**: This frees a source claim after a real failure and schedules its retry. It also clears a dead cursor when the provider said the cursor expired.

**Data flow**: It receives the claimed source, whether the cursor should be reset, the new error count, and next retry time. It updates the source row to keep or clear the cursor, set next_sync_at with _rescheduled, store the error count, and remove claim fields.

**Call relations**: SyncDriver.run calls this on failure after _report_failed. It shares the _rescheduled rule with success and skip paths so a resync request made during the failed run can still win.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (run); 2 external calls (update, workspace_tx).


##### `SyncDriver._skip`  (lines 846–869)

```
async def _skip(self, source: ClaimedSource) -> None
```

**Purpose**: This frees a source claim after a backend intentionally skipped the stream. It reschedules normally and does not count the outcome as an error.

**Data flow**: It receives the claimed source, computes the next normal sync time, and updates the source row to reset consecutive errors, clear the claim, and preserve the cursor and existing pages.

**Call relations**: SyncDriver.run calls this when it catches StreamSkipped. It uses _rescheduled for the same reason as _write and _release: a newer resync request should not be pushed away.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (run); 4 external calls (now, timedelta, update, workspace_tx).


##### `PageFeed.pages_changed_since`  (lines 909–909)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: This is the protocol method an indexer uses to read page changes after a cursor. It defines the contract for replaying page updates in order.

**Data flow**: A cursor and limit go in. An implementation returns a PageBatch containing page changes and the next cursor to continue from.

**Call relations**: CorePageFeed.pages_changed_since is the core implementation. Extension code can depend on the PageFeed protocol without knowing the database and blob-store details.


##### `page_cursor`  (lines 912–921)

```
def page_cursor(cursor: object) -> tuple[int, UUID]
```

**Purpose**: This parses the page-feed cursor format. The cursor marks the last seen revision and page id so replay can continue exactly after that point.

**Data flow**: An object goes in. The function requires it to be a string shaped like revision, a vertical bar, and a UUID. It returns the revision as an integer and the page id as a UUID, or raises ValueError if invalid.

**Call relations**: CorePageFeed.pages_changed_since calls this when a caller provides a cursor. The parsed values become the database filter for the next batch of page changes.

*Call graph*: called by 1 (pages_changed_since); 1 external calls (UUID).


##### `CorePageFeed.pages_changed_since`  (lines 933–991)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: This reads changed pages for downstream indexers in a stable order. It includes page bodies for live pages and an empty body for tombstoned pages so readers know to remove their indexed copy.

**Data flow**: It receives an optional cursor and requested limit. It builds a database query ordered by revision and page id, caps the batch size, filters after the cursor if present, reads page rows, fetches each live body from the blob store, creates PageChange objects, and returns them with a next cursor based on the last row.

**Call relations**: Indexing extensions call this through the PageFeed interface. It uses page_cursor to resume safely and returns PageBatch so the caller can process changes and store the next cursor.

*Call graph*: calls 1 internal fn (page_cursor); 7 external calls (__init__, __init__, fromisoformat, and_, or_, select, workspace_tx).

## 📊 State Registers Touched

- `reg-extension-registry` — The loaded list of installed extensions, packs, routes, tools, skills, jobs, credentials, backends, and migrations.
- `reg-database-store` — The shared database connection and tables where workspaces, users, agents, turns, files, jobs, costs, and extension data are saved.
- `reg-workspace-principals` — The current workspace, members, agents, controlling users, and ownership identities used to decide who is acting.
- `reg-visibility-boundaries` — The saved rules for who may see each conversation, agent, transcript, source, memory, artifact, or workspace object.
- `reg-credential-vault` — The encrypted store of API keys, OAuth tokens, and other secrets that can be injected only into approved places.
- `reg-connection-grants` — The saved account connections and per-agent permissions that say which outside accounts an agent may use.
- `reg-model-catalog` — The shared directory of available AI models, their providers, limits, prices, key requirements, and routing behavior.
- `reg-egress-policy` — The network access rules and proxy authorization state that decide what sandboxed code may contact outside the system.
- `reg-file-blob-store` — The shared byte storage for uploads, generated files, previews, media, and other raw data, separated by workspace or deployment scope.
- `reg-source-sync-catalog` — The saved catalog of external sources, pages, sync cursors, deletion marks, retry backoff, and indexing needs.
- `reg-search-index` — The shared keyword and embedding indexes that let conversations, tools, and background jobs find relevant stored documents.
- `reg-memory-store` — The durable store of remembered facts and memory-search results that can be written, deduplicated, recalled, and shown later.
- `reg-scheduled-jobs` — The durable background job and scheduled task state used for recurring work, wakeups, retries, monitors, billing, and offline evaluation.
- `reg-extension-object-slots` — The extension-owned object and conversation-panel data, such as artifacts, sources, tasks, sites, automations, and custom workspace objects.
- `reg-extension-workflow-state` — Extension-owned durable workflow records that are not just UI slots, such as code-review inboxes, evaluation runs, objectives, pauses, briefs, notes, monitors, triggers, and web-chat state.
- `reg-turn-created-references` — Saved references or citations created by a turn so final replies, source panels, transcripts, and later turns can resolve cited material consistently.
