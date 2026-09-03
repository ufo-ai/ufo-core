# Source sync, indexing, memory, and enrichment pipelines  `stage-15`

This stage is the system’s background knowledge pipeline. It runs after a member connects an account, during scheduled syncs, or when stored knowledge is needed for a later prompt. First, external source connectors poll outside tools like Slack, GitHub, or Stripe and translate their records into a common “page” format. The core sync code then saves those pages in the database and blob storage, and keeps an ordered feed so indexers can replay changes safely.

Not all sources are remote services. The gbrain files let a local folder of Markdown notes act like a source too. They check file safety, read valid text, and choose useful page titles.

Next, the indexing and memory parts split pages into searchable chunks, make optional “embeddings” or meaning fingerprints, store them in a local or Turbopuffer-backed index, and recall relevant facts later. The memory condenser cleans raw notes into summaries and durable facts.

Finally, the enrichment extension adds consent-based profile lookup. Its manifest defines the feature, providers fetch or replay person and company data, and the store records consent, results, and rate-limit pauses.

## Sub-stages

- [External source connector polling](stage-15.1.md) `stage-15.1` — 60 files
- [Indexing, embeddings, and recall](stage-15.2.md) `stage-15.2` — 8 files

## Files in this stage

### Source sync runtime
Core source package plumbing and synchronization machinery normalize external pages into durable database and blob-storage feeds for downstream consumers.

### `core/src/ufo/runtime/sources/__init__.py`

`other` · `import time`

This is an empty Python package marker. In Python projects, a file named `__init__.py` tells Python that the folder should be treated as an importable package. Here, it makes `ufo.runtime.sources` a valid place for code to import from. Think of it like putting a label on a drawer: the drawer may contain useful tools in other files, but this label lets the rest of the program find the drawer reliably. Because the file is empty, it does not run setup code, expose shortcuts, or change how the source modules behave. Its main value is structural: without it, some Python environments or packaging tools might not recognize this directory as part of the package, which could make imports fail.


### `core/src/ufo/runtime/sources/sync.py`

`orchestration` · `startup, scheduled sync polling, and downstream indexing`

This file solves the problem of turning many kinds of content sources into a steady, reliable stream of pages the rest of the system can use. A source might be a local folder, or an extension such as Slack, GitHub, S3, or another connector. The file defines the contract each source backend must follow: given a typed configuration, an old cursor, and workspace authentication, return pages, deletions, and a new cursor.

The main worker is SyncDriver. On each polling tick, it finds source rows that are due, claims them so two workers do not sync the same source at once, fetches content from the right backend, writes page bodies to the blob store, and updates page rows in the database. It avoids rewriting unchanged pages by comparing content digests, which are like fingerprints of the text. If a source gives a full snapshot, missing old pages are tombstoned, meaning marked as deleted instead of removed outright.

The file is careful about failure. Temporary failures back off so providers are not hammered. Refused streams can be skipped or parked without waking operators. Finally, CorePageFeed lets an indexer read page changes in revision order, like following a bookmark through a logbook.

#### Function details

##### `SourceRowConfig.requested_fields`  (lines 95–98)

```
def requested_fields(cls) -> frozenset[str]
```

**Purpose**: Returns the configuration fields that a caller explicitly requested and that must match when the same source is registered again. This helps avoid creating duplicate source rows when some fields are resolved automatically by the backend.

**Data flow**: It reads the class-level sets of non-identity fields and resolved fields, subtracts the resolved ones, and returns the remaining field names as a frozen set. Nothing outside the class is changed.

**Call relations**: This is part of the source configuration model contract. Registration code and backend-specific models can use it to decide which settings identify a source request and which settings are merely computed results.


##### `normalize_page_timestamp`  (lines 101–121)

```
def normalize_page_timestamp(value: str) -> str
```

**Purpose**: Turns a page timestamp into one consistent UTC text format. This lets pages from different providers be compared and replayed without guessing time zones later.

**Data flow**: It receives a timestamp string, accepts either a numeric Unix time or an ISO-style date/time string, checks that a real timezone is present when needed, converts the moment to UTC, and returns a normalized ISO string with microseconds. Bad or ambiguous input becomes a clear ValueError.

**Call relations**: Page.normalize_timestamp calls this whenever a Page is validated. That means timestamps are cleaned at the edge, before sync results are committed.

*Call graph*: called by 1 (normalize_timestamp); 2 external calls (fromisoformat, fromtimestamp).


##### `Page.digest`  (lines 138–139)

```
def digest(self) -> str
```

**Purpose**: Computes a stable fingerprint for a page body. The sync driver uses this fingerprint to know whether the page text actually changed.

**Data flow**: It reads the page body text, encodes it as bytes, hashes it with SHA-256, and returns a string beginning with `sha256:`. It does not store anything by itself.

**Call relations**: SyncDriver._commit reads this property while deciding whether to write a new body blob and update the page row. It is the cheap comparison that prevents unnecessary rewrites.

*Call graph*: 1 external calls (sha256).


##### `Page.normalize_timestamp`  (lines 143–146)

```
def normalize_timestamp(cls, value: str | None) -> str | None
```

**Purpose**: Validates and standardizes the created and updated timestamps on a fetched page. It keeps page metadata predictable no matter how a provider formats dates.

**Data flow**: It receives either a timestamp string or None. None passes through unchanged; a string is sent to normalize_page_timestamp and returned in normalized UTC form.

**Call relations**: Pydantic, the data validation library used here, calls this during Page construction. It delegates the actual timestamp parsing to normalize_page_timestamp.

*Call graph*: calls 1 internal fn (normalize_page_timestamp).


##### `StreamSkipped.__init__`  (lines 196–199)

```
def __init__(self, reason: str, *, awaits_grant: bool=False) -> None
```

**Purpose**: Creates an exception that means a provider refused this stream, but the data pipeline itself did not break. Examples include a missing permission scope or a plan gate.

**Data flow**: It receives a human-readable reason and an optional flag saying whether the stream is waiting for a grant event. It stores both on the exception so the sync driver can choose the right reschedule behavior.

**Call relations**: Connector backends raise this while fetching when they know a stream should be skipped rather than treated as a hard failure. SyncDriver._sync_claimed catches it and sends the source through the skip and possible park path.

*Call graph*: called by 58 (_credential, paginate, paginate, paginate, _paginate_named, paginate, paginate, _org_stream, paginate, paginate (+15 more)).


##### `validation_fault`  (lines 202–208)

```
def validation_fault(error: ValidationError) -> str
```

**Purpose**: Turns a validation error into a safe, compact explanation. It reports which fields failed and why, without including provider data or user-supplied secret values.

**Data flow**: It receives a ValidationError, reads each structured error entry, formats the field path and error type, and joins them into one semicolon-separated string.

**Call relations**: SyncDriver._report_failed uses this when a backend returns data that does not fit the expected model. It helps logs say what was wrong without leaking the rejected payload.

*Call graph*: called by 1 (_report_failed); 1 external calls (errors).


##### `response_fault`  (lines 211–237)

```
def response_fault(response: httpx.Response) -> str
```

**Purpose**: Extracts a safe provider-facing reason from an HTTP response, especially GraphQL responses that put useful messages in an `errors` array. It avoids logging the whole body because that body may contain sensitive request details.

**Data flow**: It receives an httpx response, tries to parse JSON, looks for a list named `errors`, and collects each error message plus an optional error code. If the response is unreadable or does not have that shape, it returns an empty string.

**Call relations**: SyncDriver._report_failed calls this for HTTP status failures. It adds provider context to failure logs without exposing credentials or full response bodies.

*Call graph*: called by 1 (_report_failed); 1 external calls (json).


##### `StreamFault.__init__`  (lines 247–249)

```
def __init__(self, reason: str) -> None
```

**Purpose**: Creates an exception for a provider response that the backend cannot safely interpret. It lets the backend provide a clean reason that can be logged as the provider fault.

**Data flow**: It receives a reason string, initializes the runtime error, and stores the same reason on the object. No external state changes.

**Call relations**: Several source extensions raise this when they detect a broken or unsupported provider shape. SyncDriver._report_failed recognizes it and logs its authored reason.

*Call graph*: called by 8 (_read, _markdown_entries, _spool_tarball, _refuse_client_error, decoded, _account_base, _sheet_value_records, _ensure_tenant).


##### `SourceBackend.config_model`  (lines 289–289)

```
def config_model(self) -> type[ConfigT]
```

**Purpose**: Declares the typed configuration model a source backend expects. This prevents source settings from being treated as an unstructured bag of values.

**Data flow**: A backend implementation provides a model class. The sync driver later feeds the source row’s stored JSON configuration into that model for validation.

**Call relations**: SyncDriver._fetch depends on this property before calling the backend. Each backend owns its own configuration shape, while the core driver stays generic.


##### `SourceBackend.fetch`  (lines 291–291)

```
async def fetch(self, config: ConfigT, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Defines the required method every source backend must implement to fetch pages. It is the seam between the core sync engine and provider-specific code.

**Data flow**: It receives validated backend config, the previous cursor if any, and workspace authentication details. It returns a SyncResult containing fetched pages, deletions, snapshot status, dropped count, and the next cursor.

**Call relations**: SyncDriver._fetch calls this after preparing config and auth. FolderSource and extension backends implement this contract.


##### `FolderSource.fetch`  (lines 305–316)

```
async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Reads a configured local folder and turns every file into a page. This is the built-in source backend shipped by core.

**Data flow**: It receives folder source config, ignores cursor and auth, reads files in a background thread, wraps each file’s relative path and text into a Page, and returns a snapshot SyncResult. Because it is a snapshot, files missing from the folder on later runs can be tombstoned.

**Call relations**: SyncDriver._fetch calls this when the source backend is `folder`. It delegates the disk scan to FolderSource._read, then hands pages back to the normal commit path.

*Call graph*: 4 external calls (__init__, __init__, to_thread, Path).


##### `FolderSource._read`  (lines 319–326)

```
def _read(root: Path) -> tuple[tuple[str, str], ...]
```

**Purpose**: Performs the actual local directory scan for FolderSource. It reads each file as UTF-8 text and records its path relative to the source root.

**Data flow**: It receives a root Path, checks that it is a directory, walks all files below it in sorted order, reads each file’s bytes, decodes them as UTF-8, and returns pairs of relative path and text. If the root is missing, it raises FileNotFoundError rather than pretending all files were deleted.

**Call relations**: FolderSource.fetch runs this in a background thread so file I/O does not block the async event loop. The returned file entries become Page objects.

*Call graph*: 2 external calls (is_dir, rglob).


##### `source_row_id`  (lines 329–349)

```
def source_row_id(workspace_id: UUID, backend: str, config: Mapping[str, object], *, connection_id: UUID | None=None, non_identity_keys: frozenset[str]=frozenset()) -> UUID
```

**Purpose**: Builds a deterministic ID for a source row. Restarting the service or registering the same source again produces the same ID instead of creating duplicates.

**Data flow**: It receives workspace ID, backend name, config, optional connection ID, and config keys that should not count as identity. It removes non-identity keys, serializes the remaining config in sorted order, and hashes the whole identity into a UUID.

**Call relations**: register_sources uses this when bootstrapping configured sources. Backend models influence it by declaring which fields are not part of the source’s identity.

*Call graph*: called by 1 (register_sources); 2 external calls (dumps, uuid5).


##### `page_id_for`  (lines 352–355)

```
def page_id_for(source_id: UUID, source_ref: str) -> UUID
```

**Purpose**: Builds a deterministic ID for a page inside a source. The same source reference always maps to the same page row.

**Data flow**: It receives a source ID and a source reference string, combines them into a stable namespace string, and returns a UUID. It does not read or write storage.

**Call relations**: SyncDriver._commit uses this for fetched pages and explicit delete references. It is what lets updates and tombstones land on the right existing row.

*Call graph*: called by 1 (_commit); 1 external calls (uuid5).


##### `source_body_ref_matches`  (lines 358–368)

```
def source_body_ref_matches(body_ref: str, source_id: UUID, page_id: UUID, digest: str) -> bool
```

**Purpose**: Checks whether a blob reference looks like the expected stored body for a particular source, page, and digest. This is a safety check for blob naming.

**Data flow**: It receives a blob reference, source ID, page ID, and digest. It checks the expected prefix, suffix, and claim-shaped middle segment, then returns true or false.

**Call relations**: This helper supports code that needs to verify source-sync blob references. It follows the naming pattern used by SyncDriver._commit when writing page bodies.


##### `register_sources`  (lines 371–436)

```
async def register_sources(configured: tuple[SourceEntry, ...]) -> None
```

**Purpose**: Creates database rows for statically configured sources at startup. It ensures configured sources exist without duplicating rows on every restart.

**Data flow**: It receives configured source entries, opens a workspace transaction, finds the workspace and main agent, computes each deterministic source ID, and inserts missing source and grant rows. Removed sources are left alone instead of silently resurrected.

**Call relations**: This runs outside the polling sync loop, typically during boot. It calls source_row_id and writes to source-related tables so SyncDriver can later claim and sync those rows.

*Call graph*: calls 1 internal fn (source_row_id); 4 external calls (now, insert, select, workspace_tx).


##### `_rescheduled`  (lines 454–465)

```
def _rescheduled(claimed: ClaimedSource, when: datetime | sa.Case[datetime]) -> sa.Case[datetime]
```

**Purpose**: Chooses the next sync time without overwriting a resync request made while the current worker held the source claim. It protects a fresh `sync now` request from being pushed into the future by an older run finishing late.

**Data flow**: It receives the claimed source and a proposed next time. It returns a SQL expression: use the proposed time only if the database’s next_sync_at is not newer than when the claim began; otherwise keep the database’s newer value.

**Call relations**: SyncDriver._write, SyncDriver._release, and SyncDriver._skip all use this when freeing a claim. It is the shared guard for successful, failed, and skipped runs.

*Call graph*: called by 3 (_release, _skip, _write); 1 external calls (case).


##### `_stream_tags`  (lines 468–473)

```
def _stream_tags(source: ClaimedSource) -> dict[str, str]
```

**Purpose**: Builds low-cardinality metric tags that identify the provider and stream involved in a sync result. These tags are suitable for counters and logs without exploding the number of metric series.

**Data flow**: It receives a claimed source, reads the backend name and optional `stream` config value, and returns them as a small dictionary of strings.

**Call relations**: Reporting paths use this for success, failure, skip, park, and claim-lost records. _check_tags builds on it when service checks need a specific source row too.

*Call graph*: calls 1 internal fn (_config_value); called by 6 (_report_failed, _report_ok, _report_parked, _run_with_lease, _sync_claimed, _check_tags).


##### `_check_tags`  (lines 476–484)

```
def _check_tags(source: ClaimedSource) -> dict[str, str]
```

**Purpose**: Builds service-check tags that identify one exact source row. Service checks need this precision so one healthy source does not accidentally clear another source’s failure status.

**Data flow**: It receives a claimed source, starts with provider and stream tags from _stream_tags, adds the source row ID, and returns the tag dictionary.

**Call relations**: SyncDriver._report_ok and SyncDriver._report_failed use this when sending health status. It extends _stream_tags specifically for row-level health tracking.

*Call graph*: calls 1 internal fn (_stream_tags); called by 2 (_report_failed, _report_ok).


##### `_config_value`  (lines 487–489)

```
def _config_value(source: ClaimedSource, key: str) -> str
```

**Purpose**: Safely reads a string value from a source’s stored config. It avoids putting non-string values directly into logs or metrics.

**Data flow**: It receives a claimed source and a key, reads that key from the config mapping, and returns the value only if it is a string. Otherwise it returns an empty string.

**Call relations**: _stream_tags and the reporting functions use this for fields such as stream and account. It keeps telemetry formatting predictable.

*Call graph*: called by 3 (_report_failed, _report_ok, _stream_tags).


##### `_readers_remain`  (lines 513–539)

```
def _readers_remain() -> sa.ColumnElement[bool]
```

**Purpose**: Builds a database condition that says a source still has at least one live reader, or has no grants at all. If every granted agent is archived, syncing would waste provider calls and indexing work for content nobody can use.

**Data flow**: It creates SQL subqueries over source grants and agents, then returns a SQL boolean expression. It does not execute the query itself.

**Call relations**: SyncDriver.candidate_workspaces and SyncDriver._claim_due include this condition when choosing work. It keeps unreadable-by-anyone sources out of the sync queue until a reader returns.

*Call graph*: called by 2 (_claim_due, candidate_workspaces); 5 external calls (and_, exists, literal, or_, select).


##### `SyncDriver.candidate_workspaces`  (lines 562–583)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds workspaces that currently have at least one due source. This lets a scheduler avoid opening per-workspace transactions when there is nothing to do.

**Data flow**: It reads the current time, opens an owner-level transaction, selects distinct workspace IDs for due, unremoved, unclaimed or expired-claim sources with remaining readers, and returns those IDs as a tuple.

**Call relations**: A higher-level dispatcher can call this before binding work to a workspace. It uses _readers_remain so the dispatcher only wakes workspaces with useful sync work.

*Call graph*: calls 1 internal fn (_readers_remain); 4 external calls (now, or_, select, owner_tx).


##### `SyncDriver.run`  (lines 585–596)

```
async def run(self) -> None
```

**Purpose**: Runs one sync polling pass for the currently bound workspace. It claims due sources, starts lease-renewal tasks, and processes each claimed source.

**Data flow**: It creates a fresh claim token, asks _claim_due for source rows, starts one renewal task per source, runs each source through _run_with_lease, and finally cancels and waits for renewal tasks. It changes database claim state through the helpers it calls.

**Call relations**: This is the main entry method for the scheduled source sync job. It orchestrates claiming, lease renewal, and per-source syncing without knowing provider details.

*Call graph*: calls 3 internal fn (_claim_due, _renew_claim, _run_with_lease); 3 external calls (create_task, gather, uuid4).


##### `SyncDriver._run_with_lease`  (lines 598–618)

```
async def _run_with_lease(self, source: ClaimedSource, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Runs one claimed source while watching the lease-renewal task. If the lease is lost, it stops rather than writing under a claim it no longer owns.

**Data flow**: It receives a claimed source and its renewal task, starts the actual sync task, waits until either sync or renewal finishes, handles claim loss specially, logs it, and cancels any remaining task. It returns nothing but may raise if renewal failed.

**Call relations**: SyncDriver.run calls this for each claimed source. It wraps SyncDriver._sync_claimed with the safety rail provided by SyncDriver._renew_claim.

*Call graph*: calls 2 internal fn (_sync_claimed, _stream_tags); called by 1 (run); 4 external calls (create_task, gather, wait, log).


##### `SyncDriver._sync_claimed`  (lines 620–639)

```
async def _sync_claimed(self, source: ClaimedSource) -> None
```

**Purpose**: Performs the fetch-and-commit flow for one claimed source and routes errors to the right recovery path. This is where success, skipped streams, cursor expiry, and real failures branch apart.

**Data flow**: It receives a claimed source, fetches a SyncResult, and commits it. If the backend says the stream was skipped, it logs and calls _skip. For other errors, it computes backoff, reports failure, and releases the claim with updated error state.

**Call relations**: SyncDriver._run_with_lease starts this as the actual sync task. It calls _fetch, _commit, _skip, _error_backoff, _report_failed, and _release depending on what happens.

*Call graph*: calls 7 internal fn (_commit, _error_backoff, _fetch, _release, _report_failed, _skip, _stream_tags); called by 1 (_run_with_lease); 3 external calls (suppress, now, log).


##### `SyncDriver._renew_claim`  (lines 641–644)

```
async def _renew_claim(self, source: ClaimedSource) -> None
```

**Purpose**: Keeps a claimed source lease alive while a slow fetch or commit is running. This prevents another worker from assuming the source is abandoned too soon.

**Data flow**: It repeatedly sleeps for the refresh interval and then calls _refresh_claim. It runs until cancelled or until refreshing fails.

**Call relations**: SyncDriver.run starts this in the background for every claimed source. SyncDriver._run_with_lease watches it alongside the sync task.

*Call graph*: calls 1 internal fn (_refresh_claim); called by 1 (run); 1 external calls (sleep).


##### `SyncDriver._refresh_claim`  (lines 646–662)

```
async def _refresh_claim(self, source: ClaimedSource) -> None
```

**Purpose**: Extends the current worker’s lease on a source row. A lease is a time-limited claim that says, in effect, `I am syncing this source now.`

**Data flow**: It receives a claimed source, computes a new expiry time, updates the matching source row only if the claim token still matches, and raises _SourceClaimLost if no row was updated.

**Call relations**: SyncDriver._renew_claim calls this repeatedly, and SyncDriver._commit calls it before committing writes. It is the database-level guard against two workers writing the same source at once.

*Call graph*: called by 2 (_commit, _renew_claim); 5 external calls (__init__, now, timedelta, update, workspace_tx).


##### `SyncDriver._claim_due`  (lines 664–715)

```
async def _claim_due(self, claim: str) -> tuple[ClaimedSource, ...]
```

**Purpose**: Claims a batch of due source rows for this worker. Claiming is like putting a temporary reservation on each row so other workers skip it.

**Data flow**: It receives a claim token, selects due source rows in the workspace, optionally uses database row locking on Postgres, updates selected rows with the claim and expiry, and returns ClaimedSource objects containing the data needed to sync them.

**Call relations**: SyncDriver.run calls this at the start of a polling pass. It uses _readers_remain and feeds the resulting ClaimedSource values into renewal and sync processing.

*Call graph*: calls 1 internal fn (_readers_remain); called by 1 (run); 7 external calls (__init__, now, timedelta, or_, select, update, workspace_tx).


##### `SyncDriver._fetch`  (lines 717–736)

```
async def _fetch(self, source: ClaimedSource) -> SyncResult
```

**Purpose**: Calls the correct backend to fetch content for one source. It prepares typed configuration and authentication so provider-specific code can do its job.

**Data flow**: It receives a claimed source, looks up its backend, validates the stored config using that backend’s model, optionally resolves the current external self user, builds SourceAuth, and awaits the backend’s fetch result.

**Call relations**: SyncDriver._sync_claimed calls this before committing. It is the bridge from core scheduling logic to backend-specific fetch implementations.

*Call graph*: called by 1 (_sync_claimed); 1 external calls (__init__).


##### `SyncDriver._commit`  (lines 738–840)

```
async def _commit(self, source: ClaimedSource, result: SyncResult) -> None
```

**Purpose**: Turns a backend’s SyncResult into blob writes and database page updates. It writes only material changes, updates metadata-only changes separately, and tombstones deleted pages.

**Data flow**: It receives a claimed source and SyncResult, refreshes the claim, reads prior pages, assigns stable page IDs, writes changed bodies to the blob store, prepares changed and metadata lists, computes deletions, and calls _write to commit database changes. If database commit fails after blob writes, it cleans up unreferenced blobs where safe.

**Call relations**: SyncDriver._sync_claimed calls this after _fetch succeeds. It relies on _prior_pages, page_id_for, _write, and _report_ok to finish the happy path.

*Call graph*: calls 5 internal fn (_prior_pages, _refresh_claim, _report_ok, _write, page_id_for); called by 1 (_sync_claimed); 4 external calls (__init__, __init__, gather, uuid5).


##### `SyncDriver._prior_pages`  (lines 842–886)

```
async def _prior_pages(self, source_id: UUID) -> tuple[dict[UUID, tuple[str, bool, PageBrowse]], dict[str, tuple[str, bool, PageBrowse]]]
```

**Purpose**: Loads the existing pages for a source so the commit step can compare old and new state. This is how the driver knows whether a fetched page changed, moved identity, or is already tombstoned.

**Data flow**: It receives a source ID, reads matching page rows from the database, builds PageBrowse summaries keyed by page ID, and also builds a second lookup keyed by source identity. It returns both lookup dictionaries.

**Call relations**: SyncDriver._commit calls this before examining fetched pages. The two lookup shapes support stable IDs both by source reference and by provider identity.

*Call graph*: called by 1 (_commit); 3 external calls (__init__, select, workspace_tx).


##### `SyncDriver._write`  (lines 888–1016)

```
async def _write(self, source: ClaimedSource, next_cursor: str | None, changed: list[ChangedPage], metadata: list[PageBrowse], fetched: list[UUID], deleted: list[UUID], snapshot: bool) -> int
```

**Purpose**: Persists one completed sync batch to the database. It updates or inserts changed pages, applies metadata-only updates, marks deletions, resets success state, and releases the claim.

**Data flow**: It receives source state, next cursor, changed pages, metadata updates, fetched page IDs, explicit deleted page IDs, and a snapshot flag. Inside one workspace transaction it verifies the claim, writes page rows, tombstones explicit or snapshot-missing pages, updates live page subjects, updates the source cursor and next sync time, clears errors and parking, and returns the number of pages tombstoned.

**Call relations**: SyncDriver._commit calls this after blob bodies have been written. It uses _rescheduled so it does not erase a newer resync request made during the run.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (_commit); 7 external calls (__init__, now, timedelta, insert, select, update, workspace_tx).


##### `SyncDriver._report_ok`  (lines 1018–1041)

```
async def _report_ok(self, source: ClaimedSource, fetched: int, written: int, tombstoned: int, dropped: int) -> None
```

**Purpose**: Reports a successful source sync to logs and health checks. It records how many pages were fetched, changed, tombstoned, or dropped.

**Data flow**: It receives counts and source details, builds telemetry tags, writes a success log, and emits an OK service check. Telemetry errors are suppressed so reporting cannot break a successful sync.

**Call relations**: SyncDriver._commit calls this after _write succeeds. Its OK service check is what clears a previous critical status for that source row.

*Call graph*: calls 3 internal fn (_check_tags, _config_value, _stream_tags); called by 1 (_commit); 3 external calls (suppress, emit_service_check, log).


##### `SyncDriver._error_backoff`  (lines 1043–1051)

```
def _error_backoff(self, source: ClaimedSource, now: datetime) -> tuple[int, datetime]
```

**Purpose**: Calculates how long to wait before retrying a failing source. Repeated failures wait longer, up to a cap, so the system does not hammer a broken provider.

**Data flow**: It receives the source and current time, increments the consecutive error count, computes an exponential backoff from the normal sync interval, caps it, and returns the new count plus the next retry time.

**Call relations**: SyncDriver._sync_claimed calls this when fetch or commit raises an error. The result is passed to _report_failed and _release so logs and database state agree.

*Call graph*: called by 1 (_sync_claimed); 1 external calls (timedelta).


##### `SyncDriver._report_failed`  (lines 1053–1111)

```
async def _report_failed(self, source: ClaimedSource, error: Exception, cursor_reset: bool, errors: int, next_sync_at: datetime) -> None
```

**Purpose**: Reports a failed source sync in a safe and useful way. It names the error class and, where possible, a sanitized provider fault without leaking credentials or raw payloads.

**Data flow**: It receives the source, exception, cursor reset flag, new error count, and next retry time. It derives a safe fault string for HTTP, stream, or validation errors, logs the failure, increments a failure metric, and emits a critical service check.

**Call relations**: SyncDriver._sync_claimed calls this before releasing a failed source. It uses response_fault, validation_fault, and tag helpers to produce searchable but safe telemetry.

*Call graph*: calls 5 internal fn (_check_tags, _config_value, _stream_tags, response_fault, validation_fault); called by 1 (_sync_claimed); 5 external calls (suppress, isoformat, emit_metric, emit_service_check, log_error).


##### `SyncDriver._release`  (lines 1113–1139)

```
async def _release(self, source: ClaimedSource, cursor_reset: bool, errors: int, next_sync_at: datetime) -> None
```

**Purpose**: Frees a source claim after a real failure and records retry state. It makes sure the source can be picked up later instead of staying stuck as claimed.

**Data flow**: It receives source state, whether the cursor should be reset, the new error count, and next retry time. It updates the source row to clear the claim, set the cursor or clear it, store the error count, and schedule the next attempt using _rescheduled.

**Call relations**: SyncDriver._sync_claimed calls this after reporting a failure. Successful runs release through _write, while skipped runs release through _skip.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (_sync_claimed); 2 external calls (update, workspace_tx).


##### `SyncDriver._skip`  (lines 1141–1206)

```
async def _skip(self, source: ClaimedSource, reason: str, *, awaits_grant: bool) -> None
```

**Purpose**: Handles a stream that the provider refused but that should not count as a broken sync. It keeps existing pages untouched, preserves the cursor, and may slow future attempts by parking the source.

**Data flow**: It receives a source, refusal reason, and whether the refusal waits on a grant event. It increments the stored refusal count, chooses either normal retry time or a park hold time, clears the claim, resets consecutive errors, and stores park metadata when the threshold is reached. If the row was parked, it reports that separately.

**Call relations**: SyncDriver._sync_claimed calls this after catching StreamSkipped. It uses _rescheduled and may call _report_parked after the database update.

*Call graph*: calls 2 internal fn (_report_parked, _rescheduled); called by 1 (_sync_claimed); 5 external calls (now, timedelta, case, update, workspace_tx).


##### `SyncDriver._report_parked`  (lines 1208–1233)

```
async def _report_parked(self, source: ClaimedSource, reason: str, refusals: int) -> None
```

**Purpose**: Reports that a repeatedly refused stream has been parked, meaning it will retry less often. This is a warning for humans to notice, not an alert that wakes someone up.

**Data flow**: It receives source details, the refusal reason, and refusal count. It writes a warning log and emits a parked metric, suppressing telemetry failures.

**Call relations**: SyncDriver._skip calls this only after the source row is successfully marked parked. It uses _stream_tags to keep the metric dimensions bounded.

*Call graph*: calls 1 internal fn (_stream_tags); called by 1 (_skip); 3 external calls (suppress, emit_metric, warn).


##### `PageFeed.pages_changed_since`  (lines 1273–1273)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: Defines the interface an indexer uses to read page changes after a saved cursor. It lets downstream code replay source content without knowing how sync writes are stored.

**Data flow**: An implementation receives a cursor and limit, reads changes newer than that cursor, and returns a PageBatch with changes and the next cursor. The protocol itself does not implement storage access.

**Call relations**: CorePageFeed implements this method for the core database and blob store. Extensions receive this seam through their context when they need to index pages.


##### `page_cursor`  (lines 1276–1285)

```
def page_cursor(cursor: object) -> tuple[int, UUID]
```

**Purpose**: Parses a page feed cursor into its revision number and page ID. The cursor is the bookmark that says where an indexer last stopped reading.

**Data flow**: It receives an object, requires it to be a string shaped like `revision|uuid`, validates the revision and UUID, and returns them as typed values. Invalid input raises ValueError.

**Call relations**: CorePageFeed.pages_changed_since calls this when a caller supplies a cursor. The parsed values become the database filter for reading only later changes.

*Call graph*: called by 1 (pages_changed_since); 1 external calls (UUID).


##### `CorePageFeed.pages_changed_since`  (lines 1297–1355)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: Reads changed pages from the core page table in a stable order and includes each page body for indexing. Tombstoned pages are returned with an empty body so readers know to delete their derived data.

**Data flow**: It receives an optional cursor and requested limit, caps the limit, builds a query ordered by revision and page ID, filters after the cursor when present, reads rows from the database, fetches each non-tombstoned body from the blob store, converts rows into PageChange objects, and returns them with the next cursor.

**Call relations**: This is the concrete PageFeed used by downstream indexers. It calls page_cursor for cursor parsing and returns PageBatch values that consumers can process and then save as their new bookmark.

*Call graph*: calls 1 internal fn (page_cursor); 7 external calls (__init__, __init__, fromisoformat, and_, or_, select, workspace_tx).


### Markdown folder source
The gbrain source adapter safely reads local Markdown files and converts them into titled, searchable pages for the shared sync pipeline.

### `extensions/gbrain/ufo_ext_gbrain/folder.py`

`io_transport` · `source sync`

This backend is for a simple but important use case: someone has a directory full of Markdown notes, and they want the system to read those notes as pages. The configuration names an absolute root folder on the host machine. During a sync, the code scans that folder, finds Markdown files, reads their bytes, and turns each one into a page using the shared gbrain page helpers.

The careful part is safety. The folder path can come from outside the program, so the code does not trust it blindly. It first turns the root into a contained root, meaning a checked directory boundary. Then every file is opened through a containment guard, like a librarian making sure every requested book really belongs to the allowed shelf. Symbolic links are skipped, because they can point somewhere else on the machine. Files that are not Markdown are skipped too.

The source reads the whole folder each time rather than keeping an incremental cursor. It returns a snapshot, meaning “this is the current complete set.” The wider sync driver can then skip unchanged pages and remove pages whose files disappeared. There is also a total size limit, so a very large folder cannot flood the sync with too much Markdown data.

#### Function details

##### `GbrainFolderSource.fetch`  (lines 35–40)

```
async def fetch(self, config: GbrainFolderConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This is the public sync entry point for the folder source. It reads the configured folder, converts each Markdown file into a page, and returns a complete snapshot of the folder’s current contents.

**Data flow**: It receives a folder configuration, an optional cursor, and authentication information. The cursor and authentication are not used here because this source is just reading local files. It asks a background thread to do the blocking disk read, decodes each file’s bytes into text, turns the text into page objects, and returns a SyncResult with those pages, no next cursor, and snapshot mode turned on.

**Call relations**: The sync system calls this when it wants fresh content from a local gbrain folder. To avoid blocking the main asynchronous event loop, it hands the disk work to GbrainFolderSource._read through asyncio.to_thread. After that, it relies on the page helpers decoded and markdown_page to interpret the Markdown files, then wraps the finished pages in SyncResult for the rest of the source pipeline.

*Call graph*: 4 external calls (__init__, to_thread, decoded, markdown_page).


##### `GbrainFolderSource._read`  (lines 43–57)

```
def _read(root: str, max_bytes: int) -> tuple[tuple[str, bytes], ...]
```

**Purpose**: This function does the actual safe folder scan. It finds Markdown files under the chosen root, reads their bytes, and refuses to read too much data or anything outside the allowed folder.

**Data flow**: It starts with a root path and a maximum byte limit. It checks the root with contained_root, walks all paths below it in sorted order, skips symbolic links, non-files, and non-Markdown paths, then opens each accepted file through contained_file. It adds up the bytes read across all files; if the total is over the limit, it raises StreamFault instead of returning partial data. Otherwise it returns a tuple of pairs: each file’s relative path and its raw bytes.

**Call relations**: GbrainFolderSource.fetch calls this when a sync begins. This function delegates the path-safety checks to contained_root and contained_file, and delegates the Markdown filename decision to is_markdown_path. If the folder is too large, it raises StreamFault so the surrounding sync can fail clearly instead of silently indexing an unsafe or excessive amount of data.

*Call graph*: calls 1 internal fn (__init__); 3 external calls (contained_file, contained_root, is_markdown_path).


### `extensions/gbrain/ufo_ext_gbrain/pages.py`

`domain_logic` · `source sync`

This file is the shared “Markdown page preparation” code for gbrain backends. Its job is to decide which files are real Markdown pages, safely read their text, and wrap each page in the standard Page object used by the rest of the system. Without this file, a sync could accidentally pull in hidden files like .git contents, crash with unclear text-decoding errors, or index YAML frontmatter as if it were part of the article body.

The flow is simple. First, a path is checked: only files ending in .md or .markdown are accepted, and anything inside a hidden path segment, such as .git or .drafts, is rejected. Next, the raw file bytes are decoded as UTF-8, which is the common text format expected here. If decoding fails, the code raises a StreamFault, a structured error that names the bad file so the user can fix it.

Finally, the Markdown text is turned into a Page. The file looks for a frontmatter block, which is a small YAML metadata section at the top of many Markdown files, surrounded by --- or ended by .... If that metadata contains a title, it becomes the page title and the metadata is removed from the indexed body. If there is no metadata title, the first top-level Markdown heading, like “# Project Notes”, is used. If neither exists, the file path becomes the fallback title.

#### Function details

##### `is_markdown_path`  (lines 16–22)

```
def is_markdown_path(relpath: str) -> bool
```

**Purpose**: This function decides whether a root-relative path should be treated as a Markdown page. It keeps the sync focused on visible Markdown files and avoids hidden folders, dotfiles, and unrelated file types.

**Data flow**: It takes a path string such as notes/today.md. It splits the path into parts, rejects it if any part starts with a dot, then checks the file extension in a case-insensitive way. It returns true for accepted Markdown paths and false for everything else.

**Call relations**: This is the first gate in the page-reading flow. Code that scans a source can call it before opening files, so only likely Markdown documents move on to decoding and page creation.

*Call graph*: 1 external calls (PurePosixPath).


##### `decoded`  (lines 25–31)

```
def decoded(source_ref: str, data: bytes) -> str
```

**Purpose**: This function converts raw file bytes into normal text using UTF-8. If the file is not valid UTF-8, it reports a clear source-stream error that includes the file name or path.

**Data flow**: It receives a source reference, which identifies the file, and the file’s bytes. It tries to decode those bytes as UTF-8 text. On success it returns the decoded string; on failure it raises a StreamFault saying that this specific source is not UTF-8 text.

**Call relations**: After a path has been accepted as Markdown, callers use this before parsing the page. If decoding fails, it stops the run with a useful, named fault instead of letting a low-level Unicode error appear without context.

*Call graph*: calls 1 internal fn (__init__).


##### `markdown_page`  (lines 34–43)

```
def markdown_page(source_ref: str, text: str) -> Page
```

**Purpose**: This function turns one Markdown document into a Page object that the rest of the system can index or sync. It also chooses the best available title for that page.

**Data flow**: It receives the source reference and the Markdown text. It first asks _split_frontmatter to separate any top-of-file metadata from the real body. Then it chooses a title: frontmatter title first, first Markdown heading second, source reference last. It returns a Page containing the source reference, cleaned body, stream name, and title.

**Call relations**: This is the main assembly step after a file has passed filtering and decoding. It relies on _split_frontmatter to remove metadata and on _first_heading when metadata does not provide a title, then hands the finished Page to the broader source pipeline.

*Call graph*: calls 2 internal fn (_first_heading, _split_frontmatter); 1 external calls (__init__).


##### `_split_frontmatter`  (lines 46–60)

```
def _split_frontmatter(text: str) -> tuple[str | None, str]
```

**Purpose**: This helper looks for a YAML frontmatter block at the top of a Markdown file and extracts a title from it if possible. It also removes that metadata block from the page body so it is not indexed as normal content.

**Data flow**: It receives the full Markdown text. If the text does not start with a frontmatter delimiter, it returns no title and the original text. If it finds a closing delimiter, it tries to parse the lines between as YAML, reads the title field when it is a non-empty string, and returns that title plus the body after the metadata. If the YAML is invalid or no closing delimiter is found, it safely falls back to no title and the original text.

**Call relations**: markdown_page calls this before choosing the final title. Its output decides whether the page gets a metadata title immediately or whether markdown_page needs to fall back to scanning headings.

*Call graph*: called by 1 (markdown_page); 1 external calls (safe_load).


##### `_first_heading`  (lines 63–68)

```
def _first_heading(body: str) -> str | None
```

**Purpose**: This helper finds the first top-level Markdown heading in the page body. It is used as a friendly title when the file has no usable frontmatter title.

**Data flow**: It receives the Markdown body as text. It checks each line, trims surrounding whitespace, and looks for a line starting with “# ”. If it finds one, it returns the heading text after the marker; if not, it returns nothing.

**Call relations**: markdown_page calls this only after frontmatter title extraction has not produced a title. It provides the second-best title choice before the source path is used as the final fallback.

*Call graph*: called by 1 (markdown_page).


### Consented enrichment
The enrichment extension records lookup consent, chooses live or recorded profile providers, stores results and throttling state, and exposes confirmed profile guesses to the product.

### `extensions/enrichment/ufo_ext_enrichment/__init__.py`

`other` · `import time`

This is the package entry file for the enrichment extension. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package, like labeling a drawer so other parts of the program know what is inside. Here, the file only contains a short docstring: “The enrichment extension.” That means it does not run setup code, define functions, or store configuration. Its main value is structural. Without it, depending on the Python version and import style, code that expects `extensions.enrichment.ufo_ext_enrichment` to behave like a regular package might fail or become harder to discover. It also gives documentation tools and readers a simple description of what this package is meant to contain.


### `extensions/enrichment/ufo_ext_enrichment/manifest.py`

`domain_logic` · `startup, scheduled enrichment runs, object reads, and user prompt hooks`

This file is the front door and main behavior for the enrichment feature. The real-world problem it solves is: when a new workspace member signs up, the system may want a helpful starting guess about who they are and what company they represent, but it must not send them to an outside data provider unless they agree. The file treats a confirmed website as that agreement. Clearing the website means “do not look me up.”

The flow is split into a few parts. First, the `confirm_website` action records the member’s choice and website, but does not contact the provider right away. Then a scheduled job runs about once a minute, finds consenting members without profiles, and asks the configured provider for person and company information. If the provider is missing, the action and job are not registered at all, so the portal will not ask for a website it cannot use.

The file also exposes stored enrichment rows as a read-only object called `enrichment_profile`. Users can list and view the guessed data, but cannot edit or delete it directly; the website confirmation is the only write path. Finally, on each user prompt, the file may inject a short, clearly walled-off note into the agent’s context. That note is deliberately framed as an unverified outside guess, like a sticky note saying “starting clue, not truth.”

#### Function details

##### `website_host`  (lines 162–172)

```
def website_host(raw: str) -> str | None
```

**Purpose**: Turns whatever a member typed as a website into a clean host name, such as changing `https://www.example.com/path` into `example.com`. It rejects values that do not look like a real website.

**Data flow**: It receives a raw text string from the website field. It trims spaces, lowercases it, parses it like a URL, removes a leading `www.`, and checks that the host contains a dot. It returns the cleaned host, returns `None` for an empty field, or raises an error for something that is not a website.

**Call relations**: The website confirmation action calls this before storing the member’s answer. That keeps later lookup code working with a simple domain instead of many possible URL shapes.

*Call graph*: called by 1 (confirm_website); 1 external calls (urlsplit).


##### `Enrichment.confirm_website`  (lines 182–203)

```
async def confirm_website(self, ctx: ToolContext, args: ConfirmWebsiteInput) -> ToolResult
```

**Purpose**: Records a speaking member’s consent choice and confirmed company website. It intentionally does not perform the lookup immediately; it leaves that work for the scheduled job.

**Data flow**: It receives the tool context and the submitted website. It checks that extension context exists, verifies there is a speaking member, normalizes the website, loads the seated member, records whether consent was granted, stores the website unless it is a free email provider domain, and deletes any older profile for that member. It returns a short message saying either that a profile is being built or that nothing was looked up.

**Call relations**: This is the write path declared by `manifest` when a provider is available. It relies on `website_host` to clean the submitted website and `_require_ext` to ensure it has workspace access. The later `Enrichment.tick` job picks up the recorded consent and actually builds the profile.

*Call graph*: calls 2 internal fn (_require_ext, website_host); 5 external calls (__init__, __init__, __init__, __init__, __init__).


##### `Enrichment.tick`  (lines 205–224)

```
async def tick(self, ctx: ExtensionContext) -> None
```

**Purpose**: Runs the background enrichment work for members who already gave consent and still have no stored profile. It processes a small batch so the system does not try to enrich everyone at once.

**Data flow**: It receives an extension context for a workspace. It reads the next due seated members, looks each one up, writes the resulting profile, and clears any provider backoff after successful work. If the provider reports a retryable problem, it records a pause for the workspace, logs a warning, and stops early.

**Call relations**: The scheduled job registered by `manifest` calls this. For each member it hands the email and website to `Enrichment._lookup`; when provider trouble occurs, it uses the backoff store so the next run waits instead of hammering a failing service.

*Call graph*: calls 2 internal fn (transaction, _lookup); 3 external calls (__init__, __init__, warn).


##### `Enrichment._lookup`  (lines 226–233)

```
async def _lookup(self, email: str, website: str | None) -> Profile
```

**Purpose**: Asks the provider for the person and company data needed to build one profile. It avoids treating common email services, such as Gmail, as someone’s employer.

**Data flow**: It receives an email address and an optional confirmed website. It asks the provider for person information using the email. It chooses the company domain from the confirmed website if present, otherwise from the email domain, but skips company lookup for known free mail domains. It returns a `Profile` created from those results.

**Call relations**: `Enrichment.tick` calls this for each due member. After gathering raw provider answers, it hands them to `Enrichment._profile` to turn them into the stored profile shape.

*Call graph*: calls 1 internal fn (_profile); called by 1 (tick).


##### `Enrichment._profile`  (lines 235–249)

```
def _profile(self, email: str, website: str | None, person: Person | None, company: Company | None) -> Profile
```

**Purpose**: Packages provider results into one stored profile record. It decides whether the lookup matched anything and stamps the result with the current time.

**Data flow**: It receives the member email, confirmed website, optional person data, and optional company data. It sets the status to `matched` if either person or company data exists, otherwise `no_match`, adds the provider source, and records the fetch time. It returns a complete `Profile` object.

**Call relations**: `Enrichment._lookup` calls this after provider requests finish. The returned profile is later written to storage by `Enrichment.tick`.

*Call graph*: called by 1 (_lookup); 2 external calls (__init__, now).


##### `inject`  (lines 252–274)

```
async def inject(ctx: HookContext) -> HookOutcome
```

**Purpose**: Adds a short enrichment note to an agent turn when useful profile data exists. The note is marked as third-party, unverified information so the agent should not treat it as certain truth.

**Data flow**: It receives hook context for a user prompt. If there is no turn, it returns nothing. Otherwise it reads the speaker’s profile and other workspace profiles, chooses company information from the best available row, formats company and member lines, wraps them in an untrusted-data wall, and returns an injection context. If there is nothing useful to say, it returns nothing.

**Call relations**: `manifest` registers this as a best-effort hook for user prompt submission. It calls `_company_lines` and `_member_lines` to make the short text, and uses `wall` so the agent sees the information as externally sourced.

*Call graph*: calls 2 internal fn (_company_lines, _member_lines); 3 external calls (__init__, __init__, wall).


##### `_company_lines`  (lines 277–285)

```
def _company_lines(company: Company | None) -> list[str]
```

**Purpose**: Builds the company part of the short prompt note. It keeps the text compact by including only a name and a few useful details.

**Data flow**: It receives optional company data. If there is no company name, it returns an empty list. Otherwise it clips the company name and selected details such as industry, size, and location, then returns one formatted line.

**Call relations**: `inject` calls this while preparing the walled-off note for the agent. It uses `_clip` so long provider values cannot make the injected text too large.

*Call graph*: calls 1 internal fn (_clip); called by 1 (inject).


##### `_member_lines`  (lines 288–292)

```
def _member_lines(person: Person | None) -> list[str]
```

**Purpose**: Builds the member part of the short prompt note, usually the person’s name and job title. It only emits a line when there is something meaningful to say.

**Data flow**: It receives optional person data. If there is no name or job title, it returns an empty list. Otherwise it clips the available pieces and returns one formatted member line.

**Call relations**: `inject` calls this after reading the speaker’s profile. Like `_company_lines`, it relies on `_clip` to keep outside data concise.

*Call graph*: calls 1 internal fn (_clip); called by 1 (inject).


##### `_clip`  (lines 295–297)

```
def _clip(value: str, limit: int) -> str
```

**Purpose**: Shortens text to a safe display length and collapses awkward whitespace. It is a small guardrail against long or messy provider strings.

**Data flow**: It receives a text value and a maximum length. It turns all runs of whitespace into single spaces. If the result fits, it returns it unchanged; if not, it cuts it short and adds an ellipsis.

**Call relations**: Formatting helpers call this before putting provider text into summaries or prompt injections. That keeps `inject`, `_company_lines`, `_member_lines`, and `summary` from producing oversized text.

*Call graph*: called by 3 (_company_lines, _member_lines, summary).


##### `summary`  (lines 300–312)

```
def summary(profile: Profile) -> str
```

**Purpose**: Creates the one-line summary shown for a profile row. It tries to say the most useful thing available, such as a job title at a company.

**Data flow**: It receives a profile. It looks for a person job title, company name, person name, and company industry, in that order of usefulness. It combines what it finds into one line, falls back to `No match` if nothing was found, clips the result, and returns the summary text.

**Call relations**: `_row` calls this when converting a stored profile into an object row for lists and detail views. It uses `_clip` so row summaries stay short.

*Call graph*: calls 1 internal fn (_clip); called by 1 (_row).


##### `_row`  (lines 315–338)

```
def _row(profile: Profile) -> ObjectRow
```

**Purpose**: Converts a stored profile into the row format used by the object system. This is what makes enrichment data visible in lists with named fields.

**Data flow**: It receives a `Profile`. It copies the email, website, status, source, person fields, and company fields into a plain field dictionary, creates a readable summary, and returns an `ObjectRow` named by the email address.

**Call relations**: Both `ProfileObjects._page` and `ProfileObjects._entry` use this before returning data to callers. It depends on `summary` for the row’s human-friendly label.

*Call graph*: calls 1 internal fn (summary); called by 2 (_entry, _page); 1 external calls (__init__).


##### `_require_ext`  (lines 341–344)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Checks that an extension context is present before code tries to read or write workspace data. Without that context, the file cannot know which workspace’s storage to use.

**Data flow**: It receives an optional extension context. If the context exists, it returns it unchanged. If it is missing, it raises an error explaining that enrichment objects were dispatched without the needed context.

**Call relations**: The website action and object read methods call this at their boundaries. It acts like a front-door check before functions open transactions or query profile storage.

*Call graph*: called by 5 (confirm_website, get, list, member_detail, member_page).


##### `ProfileObjects.list`  (lines 353–356)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns a page of enrichment profile rows for callers allowed to read shared workspace data. If the caller does not have the shared subject, it returns an empty page.

**Data flow**: It receives a tool context and list query. It checks the caller’s read subjects, and either returns an empty object page or uses the extension context to load a real page of profile rows. The result is an `ObjectPage` ready for display or API use.

**Call relations**: The object system calls this when someone lists `enrichment_profile`. It delegates the actual loading and row formatting to `ProfileObjects._page` after `_require_ext` confirms workspace context.

*Call graph*: calls 2 internal fn (_page, _require_ext); 1 external calls (object_page).


##### `ProfileObjects.member_page`  (lines 358–374)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns enrichment rows through the member-object view, but only from the main agent lane. This prevents the same workspace-level profile rows from appearing once per agent.

**Data flow**: It receives optional extension context, member information, admin flag, and a list query. It checks whether the current object agent is the main one. If not, it returns an empty page; if yes, it loads and returns the normal profile page.

**Call relations**: The broader member-object listing path calls this when it fans out over agents. It checks `agent_is_main`, then either stops with an empty page or delegates to `ProfileObjects._page`.

*Call graph*: calls 2 internal fn (_page, _require_ext); 3 external calls (object_agent_id, object_page, agent_is_main).


##### `ProfileObjects.get`  (lines 376–380)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[Profile] | None
```

**Purpose**: Fetches one enrichment profile by name, where the name is the member email. It only returns data to callers allowed to read shared workspace information.

**Data flow**: It receives a tool context and row name. It checks read permission through the shared subject, then asks `_entry` for that email’s stored profile. It returns the object detail if found, or `None` if access is denied or no row exists.

**Call relations**: The object system calls this when a caller opens one `enrichment_profile` row. It uses `_require_ext` for workspace context and `ProfileObjects._entry` for the storage lookup.

*Call graph*: calls 2 internal fn (_entry, _require_ext).


##### `ProfileObjects.member_detail`  (lines 382–397)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[Profile] | None
```

**Purpose**: Fetches one member-object enrichment detail, but only from the main agent lane. This mirrors `member_page` for single-row reads.

**Data flow**: It receives optional extension context, row name, member information, and admin flag. It checks whether the current object agent is the main one. If not, it returns nothing; if yes, it loads the named profile entry and returns it if present.

**Call relations**: The member-object detail path calls this when asking an agent for one row. It uses `agent_is_main` to avoid duplicate workspace rows and then delegates to `ProfileObjects._entry`.

*Call graph*: calls 2 internal fn (_entry, _require_ext); 2 external calls (object_agent_id, agent_is_main).


##### `ProfileObjects.status`  (lines 399–406)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports no special write or sync status for enrichment profiles. These profiles are read-only objects built by the confirmation action and background job.

**Data flow**: It receives the context, row name, and optional expected generation value. It does not inspect or change anything and always returns `None`.

**Call relations**: The object system may call this as part of its standard object interface. In this file, it is intentionally a quiet no-op because enrichment rows are not edited through normal apply-style updates.


##### `ProfileObjects.apply`  (lines 408–417)

```
async def apply(self, ctx: ToolContext, name: str, spec: Profile, old: Profile | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or edit an enrichment profile directly. This protects the rule that profiles are produced only from confirmed consent and provider lookup.

**Data flow**: It receives the proposed profile, old profile, row name, context, and optional generation check. Instead of saving anything, it raises a `VerbNotSupported` error with an explanation of the allowed write path.

**Call relations**: The object system calls this if someone tries to apply a change to an enrichment profile. The function stops that path and points back to `confirm_website` as the correct way to influence the data.

*Call graph*: 1 external calls (__init__).


##### `ProfileObjects.delete`  (lines 419–426)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses direct deletion of an enrichment profile through the object interface. Clearing the confirmed website is the supported way to withdraw consent and remove stored data.

**Data flow**: It receives the context, row name, and optional generation check. It does not delete anything directly and raises a `VerbNotSupported` error explaining that normal object deletion is not supported.

**Call relations**: The object system calls this if someone tries to delete an enrichment row. The function blocks the direct mutation so all changes continue to go through the consent-aware website confirmation flow.

*Call graph*: 1 external calls (__init__).


##### `ProfileObjects._page`  (lines 428–431)

```
async def _page(self, ext: ExtensionContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Loads stored enrichment profiles and turns them into a paged object-list response. It is the shared helper behind the public list methods.

**Data flow**: It receives an extension context and list query. It opens a transaction, reads up to the configured maximum profile rows for the workspace, converts each stored profile into an object row, and applies the requested paging. It returns an `ObjectPage`.

**Call relations**: `ProfileObjects.list` and `ProfileObjects.member_page` call this after doing their access and agent-lane checks. It uses `_row` to translate stored profile data into the object system’s list format.

*Call graph*: calls 2 internal fn (transaction, _row); called by 2 (list, member_page); 2 external calls (__init__, object_page).


##### `ProfileObjects._entry`  (lines 433–445)

```
async def _entry(self, ext: ExtensionContext, name: str) -> MemberObject[Profile] | None
```

**Purpose**: Loads one stored profile by email and wraps it in the object-detail shape used by the rest of the system. It is the shared helper behind single-row reads.

**Data flow**: It receives an extension context and row name. It opens a transaction, looks up the profile by email, and returns `None` if no row exists. If found, it builds a row plus detailed profile data with created and updated times set to the fetch time.

**Call relations**: `ProfileObjects.get` and `ProfileObjects.member_detail` call this after permission or main-agent checks. It uses `_row` for the list-style part and then adds the full stored profile as detail.

*Call graph*: calls 2 internal fn (transaction, _row); called by 2 (get, member_detail); 3 external calls (__init__, __init__, __init__).


##### `manifest`  (lines 468–510)

```
def manifest() -> Manifest
```

**Purpose**: Builds the extension manifest, which tells the host system what this enrichment extension offers. It always exposes read access and the prompt hook, and only exposes lookup-producing actions and jobs when a provider is configured.

**Data flow**: It reads provider configuration from the environment. If no provider is available, it creates a manifest with the read-only object and prompt hook only. If a provider exists, it creates an `Enrichment` instance, declares the `confirm_website` tool, schedules the enrichment job, and returns a full `Manifest` containing tools, objects, jobs, hooks, and deploy-key information.

**Call relations**: The host calls this at extension startup to discover the extension’s capabilities. It wires `Enrichment.confirm_website` into the tool system, `Enrichment.tick` into scheduled jobs, `PROFILE_OBJECT` into object reads, and `inject` into user prompt submission.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, owner_candidates, provider_from_env).


### `extensions/enrichment/ufo_ext_enrichment/providers.py`

`io_transport` · `startup and enrichment lookup`

When someone signs up with an email address or a company website, this extension can try to learn more about the person or company behind it. This file is the bridge between that need and the outside data source, People Data Labs, often shortened to PDL. It also supports a “recorded” mode, where the system reads saved PDL response bodies from a local JSON file instead of contacting the internet. That is useful for repeatable tests, demos, or deployments that should not make live enrichment calls.

The file starts by defining a common Provider shape: anything that can look up a person by email and a company by website. Then it defines small parsers that convert raw PDL-style JSON into the project’s own Person, Company, and Location models. Both live and recorded providers use the same parsers, so a saved response means the same thing as a live response.

The Recordings helper treats a JSON file like a small replay notebook: each lookup key points to the raw body returned earlier. The live PDL provider can use that file as a read-through cache. It checks the notebook first, calls PDL only if needed, and then writes the new body back.

Finally, provider_from_env chooses the mode from environment variables at startup. Without this file, enrichment would either have no safe way to contact PDL, no reliable replay mode, or inconsistent parsing between live and saved data.

#### Function details

##### `RateLimited.__init__`  (lines 80–82)

```
def __init__(self, message: str, retry_after: float | None=None) -> None
```

**Purpose**: This creates a special enrichment error for the case where People Data Labs says the system is asking too often. It can carry a suggested wait time, so the caller can pause instead of immediately trying again.

**Data flow**: It receives an error message and an optional retry delay in seconds. It stores the normal error message through the parent error class, then saves the retry delay on the exception object. The result is an exception that tells the rest of the system both what went wrong and, if known, how long to wait.

**Call relations**: PdlProvider._get uses this when the PDL HTTP response is 429, which means rate limited. Instead of treating that as a normal failure, it raises RateLimited so the enrichment worker can leave the lookup for a later tick.

*Call graph*: called by 1 (_get).


##### `Provider.source`  (lines 87–87)

```
def source(self) -> ProfileSource
```

**Purpose**: This is part of the provider contract. It says every provider must identify where its enrichment data came from, such as live PDL or recorded replay.

**Data flow**: A concrete provider supplies no input to this property beyond itself. It returns a short source label that can be stored with or attached to enriched profile data.

**Call relations**: The Provider protocol sets the expectation; RecordedProvider.source and PdlProvider.source provide the actual answers. Code using a Provider can ask for the source without needing to know which provider implementation it received.


##### `Provider.person`  (lines 89–89)

```
async def person(self, email: str) -> Person | None
```

**Purpose**: This is part of the provider contract for looking up a person by email address. It promises that provider implementations will return either a Person record or no match.

**Data flow**: It takes an email address as input. A concrete provider uses that email to find or replay a raw response, turns that response into a Person when possible, and returns either the Person or None.

**Call relations**: The protocol lets the rest of the enrichment extension call person lookups in one uniform way. RecordedProvider.person and PdlProvider.person are the concrete versions that do the real work.


##### `Provider.company`  (lines 91–91)

```
async def company(self, website: str) -> Company | None
```

**Purpose**: This is part of the provider contract for looking up a company by website. It promises that provider implementations will return either a Company record or no match.

**Data flow**: It takes a website string as input. A concrete provider uses that website to find or replay a raw response, turns that response into a Company when possible, and returns either the Company or None.

**Call relations**: The protocol lets higher-level enrichment code ask for company data without caring whether the answer comes from PDL or a recordings file. RecordedProvider.company and PdlProvider.company fulfill this contract.


##### `person_from_body`  (lines 137–148)

```
def person_from_body(body: object) -> Person | None
```

**Purpose**: This turns one raw People Data Labs person response into the project’s Person model. It also recognizes PDL’s “not found” response and returns no match instead of treating it as an error.

**Data flow**: It receives an arbitrary response body, usually parsed JSON. First it checks whether the body represents a 404 not-found result. If so, it returns None. Otherwise it validates that the body has the expected person-match shape, copies the nested person fields, adds the likelihood score, and returns a Person. If the shape is wrong, it raises EnrichmentError.

**Call relations**: Both PdlProvider.person and RecordedProvider.person send raw bodies here. This shared path is important because live PDL responses and replayed recorded responses are interpreted in exactly the same way.

*Call graph*: calls 1 internal fn (_is_not_found); called by 2 (person, person); 2 external calls (__init__, __init__).


##### `company_from_body`  (lines 151–163)

```
def company_from_body(body: object) -> Company | None
```

**Purpose**: This turns one raw People Data Labs company response into the project’s Company model. It treats PDL’s “not found” response as no match, not as a crash.

**Data flow**: It receives an arbitrary response body. It first checks for the not-found shape and returns None if present. Otherwise it validates the company response, converts any nested location information into a Location model, and returns a Company. If the response is not shaped like a valid company match, it raises EnrichmentError.

**Call relations**: PdlProvider.company and RecordedProvider.company both hand raw company bodies to this function. That keeps the meaning of a live result and a recorded result aligned.

*Call graph*: calls 1 internal fn (_is_not_found); called by 2 (company, company); 3 external calls (__init__, __init__, __init__).


##### `_is_not_found`  (lines 166–167)

```
def _is_not_found(body: object) -> bool
```

**Purpose**: This is a small helper that recognizes the response shape People Data Labs uses for a clean “no match found.”

**Data flow**: It receives any object. It checks whether the object is a dictionary and whether its status field is 404. It returns true for that exact not-found shape and false otherwise.

**Call relations**: person_from_body and company_from_body call this before trying to validate a response as a real match. That lets them return None for a normal miss instead of raising an error.

*Call graph*: called by 2 (company_from_body, person_from_body).


##### `Recordings.body`  (lines 180–182)

```
async def body(self, key: str) -> object | None
```

**Purpose**: This reads a saved raw provider response from the recordings file for one lookup key. It lets recorded mode, and live mode with caching, replay an earlier answer.

**Data flow**: It receives a key such as a person email key or company website key. It reads the JSON file in a background thread so the asynchronous event loop is not blocked by disk work, then returns the saved body for that key or None if the key is absent.

**Call relations**: RecordedProvider.person, RecordedProvider.company, and PdlProvider._body rely on this behavior. It is the lookup side of the recordings notebook.

*Call graph*: 1 external calls (to_thread).


##### `Recordings.append`  (lines 184–186)

```
async def append(self, key: str, body: object) -> None
```

**Purpose**: This saves a raw provider response into the recordings file. It is used so a live PDL lookup can be replayed later without making another network call.

**Data flow**: It receives a lookup key and a raw response body. It takes an asynchronous lock, which is a guard that stops two tasks writing the same file at the same time, then runs the file-writing work in a background thread. The recordings file ends up containing the new key and body.

**Call relations**: PdlProvider._body calls this after it gets a fresh response from PDL and recordings are enabled. It hands off to Recordings._append to do the actual disk rewrite safely.

*Call graph*: 1 external calls (to_thread).


##### `Recordings._read`  (lines 188–194)

```
def _read(self) -> dict[str, object]
```

**Purpose**: This reads the entire recordings JSON file from disk and checks that it is shaped like a dictionary of saved response bodies.

**Data flow**: It uses the Recordings path stored on the object. If the file does not exist, it returns an empty dictionary. If the file exists, it parses the text as JSON and verifies the top-level value is an object. If not, it raises EnrichmentError.

**Call relations**: Recordings.body uses this through a background thread to fetch saved values. Recordings._append also calls it before adding or replacing one entry.

*Call graph*: called by 1 (_append); 2 external calls (__init__, loads).


##### `Recordings._append`  (lines 196–201)

```
def _append(self, key: str, body: object) -> None
```

**Purpose**: This rewrites the recordings file with one new or updated saved response. It writes through a temporary file so readers do not see a half-written JSON file.

**Data flow**: It receives a key and a raw body. It reads the current recordings, inserts or replaces the entry for that key, writes the full updated JSON to a temporary file named with the current process id, then atomically replaces the real file with the temporary one. The result is a complete recordings file containing the new body.

**Call relations**: Recordings.append calls this while holding the lock. The lock prevents competing writes inside this process, and the temporary-file replacement protects readers from partial writes.

*Call graph*: calls 1 internal fn (_read); 2 external calls (dumps, getpid).


##### `RecordedProvider.source`  (lines 213–214)

```
def source(self) -> ProfileSource
```

**Purpose**: This labels the provider’s data as coming from recorded responses. That lets later code know the data was replayed locally, not freshly fetched.

**Data flow**: It reads no external input. It simply returns the source label "recorded".

**Call relations**: This is the RecordedProvider implementation of the Provider.source contract. Code that receives a Provider can use this source label without knowing it is talking to RecordedProvider specifically.


##### `RecordedProvider.person`  (lines 216–218)

```
async def person(self, email: str) -> Person | None
```

**Purpose**: This looks up a person using only the recordings file. It never contacts People Data Labs or any other outside service.

**Data flow**: It receives an email address, builds the matching recordings key, and asks Recordings for the saved raw body. If there is no saved body, it returns None. If a body exists, it passes that body to person_from_body and returns the resulting Person or None.

**Call relations**: This is used when provider_from_env selects recorded mode. It depends on Recordings.body for the saved data and on person_from_body for consistent parsing.

*Call graph*: calls 1 internal fn (person_from_body).


##### `RecordedProvider.company`  (lines 220–222)

```
async def company(self, website: str) -> Company | None
```

**Purpose**: This looks up a company using only the recordings file. It gives the system a local replay path for company enrichment.

**Data flow**: It receives a website, builds the matching recordings key, and reads the saved raw body from Recordings. If the key is missing, it returns None. If the body exists, it sends it to company_from_body and returns the resulting Company or None.

**Call relations**: This is used when provider_from_env selects recorded mode. It mirrors RecordedProvider.person, but for company data, and shares the same parser used by live PDL results.

*Call graph*: calls 1 internal fn (company_from_body).


##### `PdlProvider.source`  (lines 236–237)

```
def source(self) -> ProfileSource
```

**Purpose**: This labels the provider’s data as coming from People Data Labs. The label can be attached to enriched records so their origin is clear.

**Data flow**: It reads no external input. It simply returns the source label "pdl".

**Call relations**: This is the PdlProvider implementation of the Provider.source contract. Higher-level code can record or display the source without knowing how PdlProvider performs its lookups.


##### `PdlProvider.person`  (lines 239–247)

```
async def person(self, email: str) -> Person | None
```

**Purpose**: This looks up a person in People Data Labs by email address, optionally using recordings as a cache. It also rejects emails that are too long for safe provider use.

**Data flow**: It receives an email address. It first checks the length limit and raises EnrichmentError if the email is too long. Then it builds the PDL request parameters, asks _body for the raw response, and passes that body to person_from_body. The output is a Person, None for no match, or an enrichment error if the lookup cannot be completed.

**Call relations**: This is the live provider’s implementation of Provider.person. It delegates fetching or replaying to PdlProvider._body, then delegates interpretation to person_from_body.

*Call graph*: calls 2 internal fn (_body, person_from_body); 1 external calls (__init__).


##### `PdlProvider.company`  (lines 249–257)

```
async def company(self, website: str) -> Company | None
```

**Purpose**: This looks up a company in People Data Labs by website, optionally using recordings as a cache. It also rejects website strings that are too long.

**Data flow**: It receives a website string. It checks the length limit and raises EnrichmentError if the website is too long. Then it builds the PDL request parameters, asks _body for the raw response, and passes that body to company_from_body. The output is a Company, None for no match, or an enrichment error.

**Call relations**: This is the live provider’s implementation of Provider.company. It uses PdlProvider._body for the raw data and company_from_body to convert that data into the project’s Company model.

*Call graph*: calls 2 internal fn (_body, company_from_body); 1 external calls (__init__).


##### `PdlProvider._body`  (lines 259–267)

```
async def _body(self, key: str, path: str, params: dict[str, str]) -> object
```

**Purpose**: This is the live provider’s fetch-or-replay step. It checks the recordings file first when one is configured, and only calls People Data Labs when there is no saved body.

**Data flow**: It receives a recordings key, a PDL API path, and request parameters. If recordings are available, it asks for the saved body and immediately returns it if found. Otherwise it calls _get to make the HTTP request. If recordings are available, it appends the fresh response body before returning it.

**Call relations**: PdlProvider.person and PdlProvider.company call this after building their lookup-specific paths and parameters. It hands network work to PdlProvider._get and hands disk caching to Recordings.body and Recordings.append.

*Call graph*: calls 1 internal fn (_get); called by 2 (company, person).


##### `PdlProvider._get`  (lines 269–298)

```
async def _get(self, path: str, params: dict[str, str]) -> object
```

**Purpose**: This makes the actual HTTPS request to People Data Labs and turns HTTP-level outcomes into clear enrichment outcomes. It is where API keys, timeouts, status codes, JSON parsing, and rate limits are dealt with.

**Data flow**: It receives a PDL path and query parameters. It reads the deploy API key from the environment. If no key is available, it raises EnrichmentError. It sends a GET request with the API key header. A 200 or 404 response is parsed as JSON and returned. A 429 response raises RateLimited with any usable retry delay. Other failures raise EnrichmentError with a shortened response message.

**Call relations**: PdlProvider._body calls this only when there is no recorded response to replay. It calls _retry_after to interpret rate-limit headers and creates RateLimited when PDL asks the caller to slow down.

*Call graph*: calls 2 internal fn (__init__, _retry_after); called by 1 (_body); 3 external calls (__init__, AsyncClient, deploy_env).


##### `_retry_after`  (lines 301–310)

```
def _retry_after(header: str | None) -> float | None
```

**Purpose**: This interprets a Retry-After HTTP header when People Data Labs rate limits the system. It only accepts simple positive second counts.

**Data flow**: It receives the header value or None. If there is no header, if the value is not a number, or if the number is not positive, it returns None. Otherwise it returns the number of seconds as a float.

**Call relations**: PdlProvider._get calls this when PDL returns a 429 rate-limit response. The result is placed into the RateLimited exception so the caller can choose an appropriate delay.

*Call graph*: called by 1 (_get).


##### `provider_from_env`  (lines 313–348)

```
def provider_from_env() -> Provider | None
```

**Purpose**: This chooses and builds the enrichment provider for the current deployment based on environment variables. It is the startup decision point for live PDL mode, recorded replay mode, or no provider at all.

**Data flow**: It reads the provider mode from UFO_ENRICHMENT_PROVIDER, defaulting to PDL, and reads the optional recordings file path from UFO_ENRICHMENT_RECORDINGS. In PDL mode, it checks for the deploy API key; without one it warns and returns None. With a recordings path, it verifies the path is outside the extension package before returning a PdlProvider with Recordings. In recorded mode, it requires an existing recordings file, warns that replay mode is active, and returns a RecordedProvider. Unknown modes raise RuntimeError.

**Call relations**: Startup code calls this to decide what enrichment capability exists in this run. It constructs PdlProvider, RecordedProvider, and Recordings as needed, and uses warnings to make important deployment choices visible.

*Call graph*: 6 external calls (__init__, __init__, __init__, Path, deploy_env, warn).


### `extensions/enrichment/ufo_ext_enrichment/store.py`

`io_transport` · `request handling and background enrichment jobs`

This file is the memory cabinet for the enrichment feature. Enrichment means taking a member’s email or confirmed website and asking an outside data provider for extra person or company details. Because that can involve personal data, the file keeps consent separate and explicit: only members with a granted consent row are eligible. Someone who has not answered is treated the same as someone who declined: they are not enriched.

The file defines three database tables. One table stores enrichment profiles, one row per member. One stores consent decisions, including the website the member confirmed. One stores a temporary “backoff” delay for a workspace after a provider refusal, so the system does not keep retrying every minute like someone repeatedly pressing a doorbell.

It also defines typed shapes for the JSON data saved in the profile table. `Person`, `Company`, and `Profile` describe what the provider returned in a safer, predictable form. The `Profiles`, `Consents`, and `Backoff` classes are small database helpers that read and write those tables inside a caller-provided transaction. A few helper functions find workspaces ready for enrichment, check whether an agent is the main agent for a workspace, and turn raw database rows back into typed profile objects.

#### Function details

##### `due_workspaces`  (lines 161–176)

```
def due_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds a database query for finding workspaces that have enrichment work waiting. A workspace qualifies only if it has seated members who granted consent, do not already have profile rows, and are not currently paused by backoff.

**Data flow**: It takes no direct input. It reads the member, consent, profile, and backoff table definitions, compares backoff times with the current time, and produces a SQL query object. Nothing is fetched yet; the caller can run the query to get workspace IDs.

**Call relations**: This is used at the start of a background enrichment pass to decide which workspaces deserve attention. It leans on `_has_profile` so the same “already enriched” test is shared with the per-workspace member lookup.

*Call graph*: calls 1 internal fn (_has_profile); 3 external calls (now, exists, select).


##### `_has_profile`  (lines 179–180)

```
def _has_profile() -> sa.ColumnElement[bool]
```

**Purpose**: Creates the small database condition that answers, “Does this member already have an enrichment profile?” It exists so multiple queries use the same rule and do not accidentally disagree.

**Data flow**: It takes no explicit input, but it refers to the current member row in the surrounding SQL query. It returns a SQL condition that checks for a matching row in the enrichment profile table.

**Call relations**: It is a helper for both `due_workspaces` and `Profiles.due`. Those larger queries use it to skip members who have already been enriched.

*Call graph*: called by 2 (due, due_workspaces); 1 external calls (exists).


##### `Profiles.due`  (lines 190–212)

```
async def due(self, limit: int) -> tuple[SeatedMember, ...]
```

**Purpose**: Finds the next seated members in one workspace who gave permission and still need enrichment. It also brings along the website each member confirmed, because that website is part of what the lookup should use.

**Data flow**: It receives a maximum number of members to return and uses the `Profiles` object's database connection and workspace ID. It queries members joined with their consent records, filters to seated and granted members without profiles, orders them predictably, and returns `SeatedMember` objects containing member ID, email, and confirmed website.

**Call relations**: After `due_workspaces` identifies a workspace, the enrichment job can call this method to get the actual members to process. It calls `_has_profile` to avoid returning members who already have stored enrichment data.

*Call graph*: calls 1 internal fn (_has_profile); 2 external calls (__init__, select).


##### `Profiles.seated`  (lines 214–224)

```
async def seated(self, member_id: UUID) -> SeatedMember | None
```

**Purpose**: Checks whether a specific member is seated in this workspace and, if so, returns the basic information needed for enrichment. “Seated” here means the member is active enough to count for this feature.

**Data flow**: It receives a member ID and uses the stored connection and workspace ID. It queries the member table for that exact seated member. If found, it returns a `SeatedMember` with ID and email; otherwise it returns `None`.

**Call relations**: This is useful when code starts from one member rather than from the background job queue. It hands back the same simple `SeatedMember` shape that `Profiles.due` uses.

*Call graph*: 2 external calls (__init__, select).


##### `Profiles.write`  (lines 226–249)

```
async def write(self, member_id: UUID, profile: Profile) -> None
```

**Purpose**: Saves a member’s enrichment result. It updates the existing profile row if one is already there, or inserts a new row if this is the first result for that member.

**Data flow**: It receives a member ID and a typed `Profile`. It turns nested person and company objects into JSON-friendly dictionaries, then writes those values to the enrichment profile table for this workspace. The output is no returned value; the database row is changed or created.

**Call relations**: The enrichment job calls this after a provider lookup succeeds or after it determines there was no match. It is the point where in-memory provider results become durable data that later UI or hook code can read.

*Call graph*: 2 external calls (insert, update).


##### `Profiles.forget`  (lines 251–257)

```
async def forget(self, member_id: UUID) -> None
```

**Purpose**: Deletes the stored enrichment profile for one member in this workspace. This supports cases where the system must remove enrichment data, such as consent changes or cleanup.

**Data flow**: It receives a member ID and uses the stored workspace ID. It issues a delete against the profile table for that workspace-member pair. It returns nothing; the before-and-after change is that the profile row is gone if it existed.

**Call relations**: Other parts of the enrichment flow can call this when a stored profile should no longer be kept. Unlike `Profiles.write`, it removes the saved result rather than creating or updating one.

*Call graph*: 1 external calls (delete).


##### `Profiles.rows`  (lines 259–268)

```
async def rows(self, limit: int) -> tuple[StoredProfile, ...]
```

**Purpose**: Reads a page of stored enrichment profiles for one workspace. It returns them in a stable order so callers can display or process them consistently.

**Data flow**: It receives a limit and uses the stored database connection and workspace ID. It selects profile columns from the enrichment profile table, orders by fetch time and member ID, converts each raw row with `_stored`, and returns a tuple of `StoredProfile` objects.

**Call relations**: This is a bulk-reading path for code that needs several saved profiles. It delegates the row-to-object conversion to `_stored` so the same validation and date handling are used everywhere profiles are read.

*Call graph*: calls 1 internal fn (_stored); 1 external calls (select).


##### `Profiles.one`  (lines 270–279)

```
async def one(self, member_id: UUID) -> StoredProfile | None
```

**Purpose**: Reads the stored enrichment profile for one member, if it exists. It is the direct lookup form of `Profiles.rows`.

**Data flow**: It receives a member ID and queries the profile table for that member in the current workspace. If no row is found, it returns `None`; if a row is found, it passes the row to `_stored` and returns a `StoredProfile`.

**Call relations**: Callers use this when rendering or checking one member’s enrichment data. Like the other read methods, it relies on `_stored` to rebuild the typed profile object from database JSON.

*Call graph*: calls 1 internal fn (_stored); 1 external calls (select).


##### `Profiles.by_email`  (lines 281–290)

```
async def by_email(self, email: str) -> StoredProfile | None
```

**Purpose**: Finds a stored enrichment profile by email address within one workspace. It compares email addresses case-insensitively and ignores extra spaces around the input.

**Data flow**: It receives an email string, trims and lowercases it for comparison, then queries the profile table in the current workspace. It returns `None` if there is no match, or a `StoredProfile` converted through `_stored` if one is found.

**Call relations**: This supports flows that know an email address but not the member ID. After the database finds the row, `_stored` performs the shared conversion into the application’s profile shape.

*Call graph*: calls 1 internal fn (_stored); 1 external calls (select).


##### `Consents.record`  (lines 301–316)

```
async def record(self, member_id: UUID, *, granted: bool, website: str | None=None) -> None
```

**Purpose**: Records a member’s answer about whether enrichment is allowed. It stores both the yes-or-no decision and the website the member confirmed, if any.

**Data flow**: It receives a member ID, a required `granted` value, and an optional website. It adds the current time as the decision time, then updates the existing consent row or inserts a new one if none exists. It returns nothing; the consent table becomes the source of truth for that member’s answer.

**Call relations**: This is called when a member or related workflow captures an enrichment consent decision. The background job later depends on these rows through `due_workspaces` and `Profiles.due`; without a granted row, those queries will not pick the member.

*Call graph*: 3 external calls (now, insert, update).


##### `Backoff.pause`  (lines 328–354)

```
async def pause(self, retry_after: float | None) -> float
```

**Purpose**: Pauses enrichment attempts for one workspace after the provider refuses or asks the system to wait. The delay either follows the provider’s requested wait time or grows gradually from one minute up to one hour.

**Data flow**: It receives an optional retry delay in seconds and reads the current number of failed attempts for the workspace. It calculates the next wait time, stores the increased attempt count and the future retry time in the backoff table, and returns the number of seconds chosen.

**Call relations**: The enrichment job calls this when provider lookup should stop temporarily. `due_workspaces` later reads the backoff table and skips the workspace until the stored retry time has passed.

*Call graph*: 5 external calls (now, timedelta, insert, select, update).


##### `Backoff.clear`  (lines 356–361)

```
async def clear(self) -> None
```

**Purpose**: Removes the pause for a workspace. This lets the workspace resume normal enrichment after a successful tick or recovery.

**Data flow**: It uses the stored workspace ID to delete that workspace’s row from the backoff table. It returns nothing; the before-and-after change is that future due-workspace checks no longer see this workspace as paused.

**Call relations**: The enrichment job can call this after it successfully enriches someone. It complements `Backoff.pause`: one adds a waiting period, the other clears it.

*Call graph*: 1 external calls (delete).


##### `agent_is_main`  (lines 364–375)

```
async def agent_is_main(connection: AsyncConnection, workspace_id: UUID, agent_id: UUID) -> bool
```

**Purpose**: Checks whether a given agent is the main agent for a workspace. This mirrors the core member feature’s rule for narrowing what should appear on a portal page.

**Data flow**: It receives a database connection, workspace ID, and agent ID. It queries the agent table for that exact agent in that workspace and reads its `is_main` flag. It returns `True` or `False`.

**Call relations**: Code that needs to decide whether an agent has the main-agent role can call this small lookup. It does not write anything; it simply turns one database flag into a boolean answer for the caller.

*Call graph*: 2 external calls (execute, select).


##### `_stored`  (lines 378–392)

```
def _stored(row: sa.Row) -> StoredProfile
```

**Purpose**: Turns a raw database row from the enrichment profile table into the typed object used by the rest of the code. This keeps profile reading consistent and validates the saved JSON shapes.

**Data flow**: It receives a SQL row containing profile columns. It builds a `Profile`, validating person and company JSON when present, fixes a missing timezone on `fetched_at` by treating it as UTC, then wraps the profile with the member ID in a `StoredProfile`.

**Call relations**: `Profiles.rows`, `Profiles.one`, and `Profiles.by_email` all call this after reading from the database. It is the shared doorway from stored table data back into clean application objects.

*Call graph*: called by 3 (by_email, one, rows); 2 external calls (__init__, __init__).

## 📊 State Registers Touched

- `reg-credentials-connections` — The stored secrets, connected accounts, grants, and refreshable permissions used to call outside services.
- `reg-access-subjects` — The shared visibility rules that say which members or audiences may read conversations, sources, and objects.
- `reg-connector-brokers` — The shared catalog and runtime state for service connectors, MCP servers, broker accounts, and approved actions.
- `reg-source-index-memory` — The saved external pages, search chunks, embeddings, memories, and recall indexes used as workspace knowledge.
- `reg-extension-store` — The per-workspace storage area where extensions keep their own durable settings and small JSON records.
- `reg-conversation-slots-ui` — The shared side-panel and workspace UI state for artifacts, sources, tasks, sites, automations, and app home screens.
- `reg-blob-store` — The durable binary-object namespace and storage keys for large files, imported page bodies, previews, attachments, and other non-row data.
- `reg-source-sync-state` — The source-ingestion control state: source definitions, cursors/change-feed positions, error counters, backoff or parked status, and removal markers.
- `reg-enrichment-state` — The consent, fetched profile/company enrichment results, replay data, and provider rate-limit pause state for enrichment pipelines.
