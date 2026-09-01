# Source Sync, Memory, Indexing, and Knowledge Ingestion  `stage-12`

This stage is the system’s knowledge intake and recall pipeline. It runs mostly behind the scenes after accounts, folders, or API keys are connected. Its job is to bring in outside or workspace information, keep it fresh, and store it so agents can search it later.

External Source Connectors and Feed Sync are the front gate. Each connector knows how to talk to one service, such as Slack, Google, Git, or a CRM, and converts that service’s records into a common page-like format. Shared helpers handle routine web work like logins, retries, and “pagination,” which means fetching long result lists a page at a time.

The sync worker in `core/src/ufo/runtime/sources/sync.py` is the pump. It pulls documents from configured sources, saves the newest content, notes what changed, and hands those changes to indexing.

Memory, Embeddings, and Search Index Backends are the library shelves. They clean and condense information, turn text into meaning-based search numbers, and store chunks in searchable databases or external index services.

## Sub-stages

- [External Source Connectors and Feed Sync](stage-12.1.md) `stage-12.1` — 65 files
- [Memory, Embeddings, and Search Index Backends](stage-12.2.md) `stage-12.2` — 11 files

## Files in this stage

### Source Sync, Memory, Indexing, and Knowledge Ingestion
### `core/src/ufo/runtime/sources/sync.py`

`domain_logic` · `startup, scheduled sync polling, page-change replay`

This file turns many possible document sources into one steady, reliable stream of pages. A source backend is like a courier: it knows how to fetch documents from one place, such as a local folder or an external connector. The sync driver is the dispatcher: it finds sources that are due, claims one so another worker does not sync it at the same time, asks the right backend for pages, writes page bodies into blob storage, and updates database rows that describe those pages.

The file is careful about safety. If a document has not changed, it avoids rewriting it by comparing a content digest, which is a fingerprint of the body. If a full source scan no longer sees a page, the page is tombstoned, meaning marked as deleted without simply vanishing. If an external provider refuses a stream because of permissions or plan limits, the driver records a skip instead of deleting old content. Repeated refusals are “parked,” meaning retried less often so the system does not waste requests.

The second half exposes a page feed. Indexers read this feed using a cursor, like a bookmark, and receive changed pages in a stable order. This keeps syncing separate from indexing: the sync job only lands page changes, and downstream systems decide how to process them.

#### Function details

##### `SourceRowConfig.requested_fields`  (lines 95–98)

```
def requested_fields(cls) -> frozenset[str]
```

**Purpose**: This tells callers which non-identity settings must still match when a source is registered again. It separates fields a person asked for from fields the system resolved automatically.

**Data flow**: It reads the class-level sets of non-identity fields and resolved fields, subtracts the resolved ones, and returns the remaining field names. Nothing outside the class is changed.

**Call relations**: This supports source registration rules for backends that use typed source configuration. It helps the surrounding source system decide whether a repeated registration is asking for the same live source row.


##### `normalize_page_timestamp`  (lines 101–121)

```
def normalize_page_timestamp(value: str) -> str
```

**Purpose**: This converts provider timestamps into one standard UTC format. It prevents different date styles, such as Unix milliseconds or ISO strings, from being stored inconsistently.

**Data flow**: It receives a timestamp string, parses it either as a number or an ISO date-time, checks that it has a real timezone unless it is a plain date, converts it to UTC, and returns a precise ISO-formatted string. Invalid or ambiguous values become clear errors.

**Call relations**: Page.normalize_timestamp calls this whenever a fetched page supplies created or updated times, so all page metadata is normalized before the sync driver stores it.

*Call graph*: called by 1 (normalize_timestamp); 2 external calls (fromisoformat, fromtimestamp).


##### `Page.digest`  (lines 138–139)

```
def digest(self) -> str
```

**Purpose**: This creates a stable fingerprint of a page body. The sync driver uses it to tell whether the document text actually changed.

**Data flow**: It reads the page body text, encodes it as bytes, hashes it with SHA-256, and returns the hash with a `sha256:` prefix. It does not modify the page.

**Call relations**: SyncDriver._commit relies on this value when deciding whether to write a new blob and page row update, or skip content that is already current.

*Call graph*: 1 external calls (sha256).


##### `Page.normalize_timestamp`  (lines 143–146)

```
def normalize_timestamp(cls, value: str | None) -> str | None
```

**Purpose**: This validates and normalizes a page's created and updated timestamps as the Page model is built. It keeps messy provider date formats from leaking into stored page metadata.

**Data flow**: It receives either a timestamp string or None. None stays None; a string is passed through normalize_page_timestamp and returned in normalized UTC form.

**Call relations**: This is part of Page validation. Backends create Page objects, and this validator prepares their timestamps before the sync driver commits them.

*Call graph*: calls 1 internal fn (normalize_page_timestamp).


##### `StreamSkipped.__init__`  (lines 196–199)

```
def __init__(self, reason: str, *, awaits_grant: bool=False) -> None
```

**Purpose**: This creates a special exception for a source stream that should be skipped, not treated as broken. It records the human-readable reason and whether the problem waits for a credential or grant event.

**Data flow**: It receives a reason string and an optional awaits_grant flag, stores both on the exception, and passes the reason to the base RuntimeError. The caller gets an exception object carrying the skip details.

**Call relations**: Many connector backends raise this when a provider refuses a stream for expected reasons, such as missing access. SyncDriver._sync_claimed catches it and routes the source through the skip-and-possibly-park path.

*Call graph*: called by 58 (_credential, paginate, paginate, paginate, _paginate_named, paginate, paginate, _org_stream, paginate, paginate (+15 more)).


##### `validation_fault`  (lines 202–208)

```
def validation_fault(error: ValidationError) -> str
```

**Purpose**: This turns a Pydantic validation error into a safe, compact explanation. It names which fields failed and why, without copying rejected provider data into logs.

**Data flow**: It reads the validation error's structured errors, formats each location and error type, joins them with semicolons, and returns the resulting text. It does not include raw values.

**Call relations**: SyncDriver._report_failed uses this when bad fetched data fails model validation, so failure logs are useful but do not leak sensitive payloads.

*Call graph*: called by 1 (_report_failed); 1 external calls (errors).


##### `response_fault`  (lines 211–237)

```
def response_fault(response: httpx.Response) -> str
```

**Purpose**: This extracts a safe provider error message from an HTTP response body, especially GraphQL-style `errors` arrays. It avoids logging the whole response, which may contain secrets.

**Data flow**: It tries to parse the response as JSON, looks for an `errors` list, pulls each error message and optional code, and returns a joined explanation. If the body is unreadable or not in that shape, it returns an empty string.

**Call relations**: SyncDriver._report_failed calls this for HTTP status failures so logs can say what the provider refused without storing the provider's full response.

*Call graph*: called by 1 (_report_failed); 1 external calls (json).


##### `StreamFault.__init__`  (lines 247–249)

```
def __init__(self, reason: str) -> None
```

**Purpose**: This creates a provider-stream failure with a backend-authored reason. It is used when a backend can describe a bad provider response safely and specifically.

**Data flow**: It receives a reason string, stores it on the exception, and passes it to RuntimeError. The result is an exception that carries a safe failure explanation.

**Call relations**: Several extension backends raise this when provider data has an unreadable shape. SyncDriver._report_failed recognizes it and logs its reason as the provider fault.

*Call graph*: called by 8 (_read, _markdown_entries, _spool_tarball, _refuse_client_error, decoded, _account_base, _sheet_value_records, _ensure_tenant).


##### `SourceBackend.config_model`  (lines 289–289)

```
def config_model(self) -> type[ConfigT]
```

**Purpose**: This protocol property says every source backend must declare the typed configuration model it expects. That lets core validate each source row before calling the backend.

**Data flow**: A backend supplies a model class; the sync driver reads that class and validates stored JSON config against it. The protocol itself does not run code.

**Call relations**: SyncDriver._fetch depends on this contract before it invokes SourceBackend.fetch. FolderSource and connector backends fulfill the contract with their own config models.


##### `SourceBackend.fetch`  (lines 291–291)

```
async def fetch(self, config: ConfigT, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This protocol method defines the main job of a source backend: fetch pages from its source and return what changed or what exists now. It is the seam between core syncing and provider-specific code.

**Data flow**: It receives typed config, the previous cursor or None, and source auth context. A backend returns a SyncResult containing pages, a next cursor, delete references, and whether the result is a full snapshot.

**Call relations**: SyncDriver._fetch calls this after preparing config and credentials. Implementations such as FolderSource.fetch or connector fetchers do the real provider reading.


##### `FolderSource.fetch`  (lines 305–316)

```
async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This fetches every file from a local folder and turns each file into a page. It gives the system a built-in source backend that does not need an external connector.

**Data flow**: It receives folder config, ignores the cursor and auth, reads files on a worker thread, builds Page objects whose references are relative file paths, and returns a full-snapshot SyncResult. The caller receives all current files as pages.

**Call relations**: SyncDriver._fetch can call this through the SourceBackend interface when a source row uses the folder backend. It delegates the disk scan to FolderSource._read.

*Call graph*: 4 external calls (__init__, __init__, to_thread, Path).


##### `FolderSource._read`  (lines 319–326)

```
def _read(root: Path) -> tuple[tuple[str, str], ...]
```

**Purpose**: This performs the actual local folder scan for FolderSource. It reads all files below the root in a stable order.

**Data flow**: It receives a root path, checks that it is a directory, walks all files below it, reads each as UTF-8 text, and returns pairs of relative path and text. If the folder is missing, it raises an error rather than pretending all files were deleted.

**Call relations**: FolderSource.fetch calls this inside a thread so blocking disk reads do not stall the async event loop.

*Call graph*: 2 external calls (is_dir, rglob).


##### `source_row_id`  (lines 329–349)

```
def source_row_id(workspace_id: UUID, backend: str, config: Mapping[str, object], *, connection_id: UUID | None=None, non_identity_keys: frozenset[str]=frozenset()) -> UUID
```

**Purpose**: This creates a repeatable database id for a source row. The same workspace, backend, and identity-defining config produce the same id, so restarts do not create duplicate sources.

**Data flow**: It receives workspace id, backend name, config, optional connection id, and config keys that should not affect identity. It removes non-identity config keys, serializes the rest in sorted order, and returns a UUID derived from that text.

**Call relations**: register_sources uses this when inserting configured sources at startup. Connector registration code can use the same idea so one dataset maps to one row.

*Call graph*: called by 1 (register_sources); 2 external calls (dumps, uuid5).


##### `page_id_for`  (lines 352–355)

```
def page_id_for(source_id: UUID, source_ref: str) -> UUID
```

**Purpose**: This creates a repeatable id for one page inside one source. It lets updates, refetches, and deletes all point at the same page row.

**Data flow**: It receives a source id and source reference string, combines them into a stable name, and returns a UUID derived from that name. It changes no stored data.

**Call relations**: SyncDriver._commit uses this as the fallback page identity when a backend has no separate provider identity for the document.

*Call graph*: called by 1 (_commit); 1 external calls (uuid5).


##### `source_body_ref_matches`  (lines 358–368)

```
def source_body_ref_matches(body_ref: str, source_id: UUID, page_id: UUID, digest: str) -> bool
```

**Purpose**: This checks whether a blob-storage reference looks like it belongs to a particular source page and digest. It is a guard for recognizing body objects written by the source sync process.

**Data flow**: It receives a body reference, source id, page id, and digest. It checks the expected prefix, claim-shaped middle piece, and digest suffix, then returns true or false.

**Call relations**: This helper is available to code that needs to validate source blob references. It follows the same naming pattern SyncDriver._commit uses when writing page bodies.


##### `register_sources`  (lines 371–436)

```
async def register_sources(configured: tuple[SourceEntry, ...]) -> None
```

**Purpose**: This ensures configured source entries exist in the database when the system starts. It makes declarative config turn into live source rows ready for the sync driver.

**Data flow**: It receives configured source entries, opens a workspace transaction, finds the workspace and main agent, computes each source id, skips removed rows, inserts missing source rows, and grants the main agent access. It returns nothing but changes the database.

**Call relations**: This runs at boot, outside the normal sync polling loop. It uses source_row_id so the same configured source is registered idempotently.

*Call graph*: calls 1 internal fn (source_row_id); 4 external calls (now, insert, select, workspace_tx).


##### `_rescheduled`  (lines 454–465)

```
def _rescheduled(claimed: ClaimedSource, when: datetime | sa.Case[datetime]) -> sa.Case[datetime]
```

**Purpose**: This decides what `next_sync_at` should become when a claimed source finishes. It protects a manual resync request that arrived while the current sync was still running.

**Data flow**: It receives a claimed source and a proposed future time. It builds a database expression: use the proposed time only if the row was not rescheduled after the claim began; otherwise keep the newer existing due time.

**Call relations**: SyncDriver._write, SyncDriver._release, and SyncDriver._skip all use this when freeing a claim, so successful, failed, and skipped runs do not accidentally bury a newer resync request.

*Call graph*: called by 3 (_release, _skip, _write); 1 external calls (case).


##### `_stream_tags`  (lines 468–473)

```
def _stream_tags(source: ClaimedSource) -> dict[str, str]
```

**Purpose**: This builds low-cardinality telemetry tags that identify the provider and stream. These tags help metrics group failures and successes without exploding into one series per source row.

**Data flow**: It receives a claimed source, reads its backend and `stream` config value if present, and returns a small dictionary of tag strings.

**Call relations**: The sync driver uses this across success, failure, skip, claim-lost, and parked reporting. _check_tags builds on it when service checks need the specific source id too.

*Call graph*: calls 1 internal fn (_config_value); called by 6 (_report_failed, _report_ok, _report_parked, _run_with_lease, _sync_claimed, _check_tags).


##### `_check_tags`  (lines 476–484)

```
def _check_tags(source: ClaimedSource) -> dict[str, str]
```

**Purpose**: This builds service-check tags that identify one exact source row. Service checks need this precision so one healthy source does not hide another failing source with the same provider stream.

**Data flow**: It receives a claimed source, starts with _stream_tags, adds the source id, and returns the combined tag dictionary.

**Call relations**: SyncDriver._report_ok and SyncDriver._report_failed use this when sending health status for a source row.

*Call graph*: calls 1 internal fn (_stream_tags); called by 2 (_report_failed, _report_ok).


##### `_config_value`  (lines 487–489)

```
def _config_value(source: ClaimedSource, key: str) -> str
```

**Purpose**: This safely pulls a string value from a claimed source's config. It avoids putting non-string config values directly into telemetry tags.

**Data flow**: It receives a claimed source and config key, reads the value, and returns it only if it is a string; otherwise it returns an empty string.

**Call relations**: _stream_tags uses this for the stream tag, and reporting functions use it for account-related logging.

*Call graph*: called by 3 (_report_failed, _report_ok, _stream_tags).


##### `_readers_remain`  (lines 513–539)

```
def _readers_remain() -> sa.ColumnElement[bool]
```

**Purpose**: This builds a database condition that says a source still has at least one live reader, or has no explicit grants. It prevents syncing feeds that no active agent can use.

**Data flow**: It constructs SQL that checks source grants and agent archive status. The output is a SQL condition, not a true-or-false value yet; the database evaluates it inside later queries.

**Call relations**: SyncDriver.candidate_workspaces and SyncDriver._claim_due include this condition so archived-only sources are not selected for work.

*Call graph*: called by 2 (_claim_due, candidate_workspaces); 5 external calls (and_, exists, literal, or_, select).


##### `SyncDriver.candidate_workspaces`  (lines 562–583)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This finds workspaces that currently have at least one source due for syncing. It lets the scheduler avoid opening per-workspace work when nothing is ready.

**Data flow**: It reads the current time, queries the owner-level database view for due, unremoved, unclaimed-or-expired sources with remaining readers, and returns distinct workspace ids.

**Call relations**: A higher-level dispatcher can call this before binding work to a workspace. It uses _readers_remain to skip sources that no active agent can read.

*Call graph*: calls 1 internal fn (_readers_remain); 4 external calls (now, or_, select, owner_tx).


##### `SyncDriver.run`  (lines 585–596)

```
async def run(self) -> None
```

**Purpose**: This is one sync-driver pass for the currently bound workspace. It claims due sources, starts lease-renewal tasks, and syncs each claimed source.

**Data flow**: It creates a claim token, claims due source rows, starts a background renewal task for each, runs each source under its lease, then cancels and gathers renewal tasks during cleanup. Its main effects are database and blob-store writes done by deeper methods.

**Call relations**: This is the top-level method used by the scheduled sync job. It hands each source to _run_with_lease and relies on _claim_due and _renew_claim to keep ownership safe.

*Call graph*: calls 3 internal fn (_claim_due, _renew_claim, _run_with_lease); 3 external calls (create_task, gather, uuid4).


##### `SyncDriver._run_with_lease`  (lines 598–618)

```
async def _run_with_lease(self, source: ClaimedSource, renewal: asyncio.Task[None]) -> None
```

**Purpose**: This runs one claimed source while watching that the claim renewal stays alive. It stops the sync if the worker loses the right to own the source.

**Data flow**: It starts the actual sync and waits for either the sync or the renewal task to finish first. If renewal fails, it raises that problem; if the claim is lost, it logs that fact; in all cases it cancels unfinished tasks.

**Call relations**: SyncDriver.run calls this for each claimed source. It wraps _sync_claimed with the lease-renewal safety net provided by _renew_claim.

*Call graph*: calls 2 internal fn (_sync_claimed, _stream_tags); called by 1 (run); 4 external calls (create_task, gather, wait, log).


##### `SyncDriver._sync_claimed`  (lines 620–639)

```
async def _sync_claimed(self, source: ClaimedSource) -> None
```

**Purpose**: This is the decision point for one claimed source: fetch, commit, skip, or fail. It turns backend outcomes into the right database updates and telemetry.

**Data flow**: It receives a claimed source, fetches a SyncResult, and commits it on success. If the backend says the stream was skipped, it records a skip path; for other errors it computes backoff, reports failure, and releases the claim.

**Call relations**: _run_with_lease calls this after a source has been claimed. It delegates normal work to _fetch and _commit, skip handling to _skip, and failure handling to _report_failed and _release.

*Call graph*: calls 7 internal fn (_commit, _error_backoff, _fetch, _release, _report_failed, _skip, _stream_tags); called by 1 (_run_with_lease); 3 external calls (suppress, now, log).


##### `SyncDriver._renew_claim`  (lines 641–644)

```
async def _renew_claim(self, source: ClaimedSource) -> None
```

**Purpose**: This keeps a source claim alive during a long fetch or commit. It is like periodically saying, “I am still working on this; do not give it to another worker.”

**Data flow**: It loops forever, sleeps for the refresh interval, then calls _refresh_claim. It ends only if cancelled or if refreshing fails.

**Call relations**: SyncDriver.run starts this as a background task for each claimed source. _run_with_lease watches it alongside the actual sync.

*Call graph*: calls 1 internal fn (_refresh_claim); called by 1 (run); 1 external calls (sleep).


##### `SyncDriver._refresh_claim`  (lines 646–662)

```
async def _refresh_claim(self, source: ClaimedSource) -> None
```

**Purpose**: This extends the database lease for a claimed source. If the row no longer belongs to this claim, it raises a special claim-lost error.

**Data flow**: It reads the current time, updates the source row's claim expiration if the claim token still matches and the row is not removed, and checks that exactly one row changed. If not, it signals that the claim was lost.

**Call relations**: _renew_claim calls this repeatedly, and _commit calls it once before doing heavier writes. _run_with_lease treats _SourceClaimLost as a normal race outcome to log.

*Call graph*: called by 2 (_commit, _renew_claim); 5 external calls (__init__, now, timedelta, update, workspace_tx).


##### `SyncDriver._claim_due`  (lines 664–715)

```
async def _claim_due(self, claim: str) -> tuple[ClaimedSource, ...]
```

**Purpose**: This claims a batch of due sources for the current worker. Claiming prevents two workers from syncing the same source at once.

**Data flow**: It queries due, readable, unremoved sources whose claim is empty or expired, optionally locks rows in PostgreSQL, writes the new claim token and expiration, and returns ClaimedSource objects describing the rows.

**Call relations**: SyncDriver.run calls this at the start of a pass. The returned ClaimedSource values drive every later step, from fetching to telemetry.

*Call graph*: calls 1 internal fn (_readers_remain); called by 1 (run); 7 external calls (__init__, now, timedelta, or_, select, update, workspace_tx).


##### `SyncDriver._fetch`  (lines 717–736)

```
async def _fetch(self, source: ClaimedSource) -> SyncResult
```

**Purpose**: This prepares and calls the correct backend for one source. It is where stored source config becomes typed config and where credential context is attached.

**Data flow**: It receives a claimed source, looks up its backend, validates the source config with that backend's model, resolves optional identity and credential helpers, builds SourceAuth, and awaits the backend's fetch result.

**Call relations**: SyncDriver._sync_claimed calls this before committing. It is the bridge from generic core scheduling into backend-specific provider reading.

*Call graph*: called by 1 (_sync_claimed); 1 external calls (__init__).


##### `SyncDriver._commit`  (lines 738–840)

```
async def _commit(self, source: ClaimedSource, result: SyncResult) -> None
```

**Purpose**: This turns fetched pages into blob writes and database changes. It decides which pages are new, changed, metadata-only updates, or deleted.

**Data flow**: It refreshes the claim, loads prior pages, assigns stable page ids, writes changed bodies to blob storage, prepares changed and metadata records, computes deletes, and calls _write. If a later database write fails, it deletes newly written blobs to avoid orphaned data when possible.

**Call relations**: SyncDriver._sync_claimed calls this after a successful fetch. It relies on _prior_pages, page_id_for, and _write, then reports success through _report_ok.

*Call graph*: calls 5 internal fn (_prior_pages, _refresh_claim, _report_ok, _write, page_id_for); called by 1 (_sync_claimed); 4 external calls (__init__, __init__, gather, uuid5).


##### `SyncDriver._prior_pages`  (lines 842–886)

```
async def _prior_pages(self, source_id: UUID) -> tuple[dict[UUID, tuple[str, bool, PageBrowse]], dict[str, tuple[str, bool, PageBrowse]]]
```

**Purpose**: This loads the existing page state for a source before committing a new fetch. The sync driver needs this to compare digests, reuse ids, and avoid unnecessary writes.

**Data flow**: It receives a source id, queries page rows for that source, builds one lookup by page id and another by source identity, and returns both maps with digest, tombstone state, and browsing metadata.

**Call relations**: SyncDriver._commit calls this at the start of reconciliation. Its results guide whether each fetched page is treated as unchanged, changed, renamed, or newly inserted.

*Call graph*: called by 1 (_commit); 3 external calls (__init__, select, workspace_tx).


##### `SyncDriver._write`  (lines 888–1016)

```
async def _write(self, source: ClaimedSource, next_cursor: str | None, changed: list[ChangedPage], metadata: list[PageBrowse], fetched: list[UUID], deleted: list[UUID], snapshot: bool) -> int
```

**Purpose**: This performs the database transaction that lands one fetched batch. It updates page rows, tombstones deleted pages, updates the source cursor and schedule, and releases the claim.

**Data flow**: It receives changed pages, metadata-only updates, fetched ids, delete ids, snapshot mode, and next cursor. Inside a workspace transaction it verifies the claim, upserts changed pages, updates metadata, tombstones explicit deletes and snapshot-missing pages, refreshes page subjects, resets error/refusal state, and returns how many pages were tombstoned.

**Call relations**: SyncDriver._commit calls this after writing needed blobs. It uses _rescheduled so a manual resync request made during the run is not lost.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (_commit); 7 external calls (__init__, now, timedelta, insert, select, update, workspace_tx).


##### `SyncDriver._report_ok`  (lines 1018–1041)

```
async def _report_ok(self, source: ClaimedSource, fetched: int, written: int, tombstoned: int, dropped: int) -> None
```

**Purpose**: This records a successful source-sync run in logs and service checks. It says how many pages were fetched, written, tombstoned, or dropped.

**Data flow**: It receives counts from the completed commit, builds telemetry tags, logs the success details, and sends an OK service check. Telemetry failures are suppressed so reporting cannot break the sync.

**Call relations**: SyncDriver._commit calls this after _write succeeds. Its OK service check clears the critical status produced by earlier failed runs for the same source row.

*Call graph*: calls 3 internal fn (_check_tags, _config_value, _stream_tags); called by 1 (_commit); 3 external calls (suppress, emit_service_check, log).


##### `SyncDriver._error_backoff`  (lines 1043–1051)

```
def _error_backoff(self, source: ClaimedSource, now: datetime) -> tuple[int, datetime]
```

**Purpose**: This computes how long to wait after a failed sync before trying again. Repeated failures wait longer, up to a fixed cap, so the system does not hammer a broken provider.

**Data flow**: It receives the claimed source and current time, increments the consecutive error count, calculates an exponential backoff from the normal sync interval, caps it, and returns the new count plus next retry time.

**Call relations**: SyncDriver._sync_claimed calls this on ordinary failures before reporting and releasing the source. _report_failed logs the same values that _release writes.

*Call graph*: called by 1 (_sync_claimed); 1 external calls (timedelta).


##### `SyncDriver._report_failed`  (lines 1053–1111)

```
async def _report_failed(self, source: ClaimedSource, error: Exception, cursor_reset: bool, errors: int, next_sync_at: datetime) -> None
```

**Purpose**: This records a failed source-sync run safely. It provides enough information to diagnose the provider stream without logging raw credentials or full provider payloads.

**Data flow**: It receives the source, exception, cursor-reset flag, error count, and next retry time. It builds a safe provider fault message for known error types, logs the failure, increments a failure metric, and sends a critical service check; each reporting step is protected from raising.

**Call relations**: SyncDriver._sync_claimed calls this on failures after _error_backoff. It uses response_fault and validation_fault for safe details, and _check_tags so the service check belongs to the exact source row.

*Call graph*: calls 5 internal fn (_check_tags, _config_value, _stream_tags, response_fault, validation_fault); called by 1 (_sync_claimed); 5 external calls (suppress, isoformat, emit_metric, emit_service_check, log_error).


##### `SyncDriver._release`  (lines 1113–1139)

```
async def _release(self, source: ClaimedSource, cursor_reset: bool, errors: int, next_sync_at: datetime) -> None
```

**Purpose**: This frees a source claim after a failed sync and schedules the next attempt. It also clears a dead cursor when the provider said the cursor expired.

**Data flow**: It receives the claimed source, whether to reset the cursor, the new error count, and retry time. It updates the source row with the cursor choice, rescheduled next sync time, error count, and no active claim.

**Call relations**: SyncDriver._sync_claimed calls this after reporting a failure. It uses _rescheduled so a resync request made during the failed run can still take priority.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (_sync_claimed); 2 external calls (update, workspace_tx).


##### `SyncDriver._skip`  (lines 1141–1206)

```
async def _skip(self, source: ClaimedSource, reason: str, *, awaits_grant: bool) -> None
```

**Purpose**: This handles a provider refusal that is not treated as a data failure. It releases the claim without changing pages, counts the refusal, and may park the source so it retries less often.

**Data flow**: It receives the source, reason, and awaits_grant flag. It updates the source row to reset error count, increment refusal count, choose the next sync time, record parked fields if the threshold is reached, and clear the claim. If the row was parked, it reports that fact.

**Call relations**: SyncDriver._sync_claimed calls this when a backend raises StreamSkipped. It calls _report_parked only after the database confirms the source actually parked.

*Call graph*: calls 2 internal fn (_report_parked, _rescheduled); called by 1 (_sync_claimed); 5 external calls (now, timedelta, case, update, workspace_tx).


##### `SyncDriver._report_parked`  (lines 1208–1233)

```
async def _report_parked(self, source: ClaimedSource, reason: str, refusals: int) -> None
```

**Purpose**: This records that a repeatedly refused stream has been parked. A park is a warning and metric, not an alert, because the system will keep retrying slowly or wait for a grant event.

**Data flow**: It receives the source, refusal reason, and refusal count, builds stream tags, writes a warning log, and emits a parked counter metric. Reporting failures are suppressed.

**Call relations**: SyncDriver._skip calls this after updating the source row into a parked state. It uses _stream_tags so the metric groups by provider and stream.

*Call graph*: calls 1 internal fn (_stream_tags); called by 1 (_skip); 3 external calls (suppress, emit_metric, warn).


##### `PageFeed.pages_changed_since`  (lines 1273–1273)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: This protocol method defines how indexers ask for page changes after a saved cursor. It gives extensions a stable way to replay source-sync output.

**Data flow**: A caller supplies a cursor and limit. An implementation returns a PageBatch containing ordered page changes and the next cursor to save.

**Call relations**: CorePageFeed.pages_changed_since is the core implementation. Indexing extensions read through this protocol instead of querying source tables directly.


##### `page_cursor`  (lines 1276–1285)

```
def page_cursor(cursor: object) -> tuple[int, UUID]
```

**Purpose**: This parses a page-feed cursor into its revision number and page id. It protects the feed from malformed bookmarks.

**Data flow**: It receives an object, requires it to be a string shaped like `revision|uuid`, converts the revision to an integer and the page id to a UUID, and returns both. Bad input raises ValueError.

**Call relations**: CorePageFeed.pages_changed_since calls this when a caller provides a cursor, so the database query can resume just after that saved position.

*Call graph*: called by 1 (pages_changed_since); 1 external calls (UUID).


##### `CorePageFeed.pages_changed_since`  (lines 1297–1355)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: This returns a bounded, ordered batch of page changes for downstream indexers. It inlines live page bodies from blob storage and uses an empty body for tombstones.

**Data flow**: It receives an optional cursor and requested limit, caps the limit, queries page rows ordered by revision and id, filters after the cursor if present, reads blob bodies for non-deleted pages, builds PageChange objects, and returns them with a next cursor from the last row.

**Call relations**: Indexers call this through the PageFeed interface. It depends on page_cursor for resume positions and on the blob store for the actual stored document text.

*Call graph*: calls 1 internal fn (page_cursor); 7 external calls (__init__, __init__, fromisoformat, and_, or_, select, workspace_tx).

## 📊 State Registers Touched

- `reg-selected-pack-services` — The chosen product pack and the shared service objects it wires up for the rest of the app.
- `reg-durable-database` — The main long-term database where shared business and runtime records are stored.
- `reg-workspace-member-agent-state` — The saved list of workspaces, people, memberships, seats, and agents.
- `reg-extension-registry` — The loaded set of extensions and the routes, tools, hooks, jobs, skills, agents, and backends they contribute.
- `reg-extension-install-store` — The saved record of which extensions are installed, removed, or holding extension-specific data.
- `reg-credential-connections` — The encrypted accounts, secrets, connection grants, and credential fulfillments that let agents use outside services safely.
- `reg-access-permissions-audience` — The shared rules for who may read, use, share, or act on workspace content and conversations.
- `reg-egress-policy-proxy` — The network allowlist and proxy state that decide which outside hosts sandboxed work may contact.
- `reg-feature-flags` — The rollout switches that turn product and infrastructure behavior on or off across the system.
- `reg-search-provider-catalog` — The common search and page-fetching service state used when the system needs outside web information.
- `reg-memory-index-state` — The stored knowledge, embeddings, chunks, and memory indexes that agents can search later.
- `reg-source-config-sync-state` — The configured external sources plus their sync progress, errors, backoff, ownership, and access grants.
- `reg-background-jobs` — The shared job schedule, due-work candidates, claims, retries, and worker state for background and autonomous work.
- `reg-object-journal` — The shared naming and change history for workspace objects such as tasks, prompts, skills, monitors, memories, and reports.
- `reg-source-page-corpus` — The canonical stored page/document records fetched from sources, including content and browse metadata before they are chunked, embedded, searched, or displayed.
- `reg-provider-rate-limit-budgets` — Shared per-provider throttle, retry, and backoff budget state for model, search, connector, and external API calls so workers avoid overrunning provider limits.
- `reg-evaluation-fixture-backends` — Deterministic fake connector/backend data for evaluation packs, such as mailbox, calendar, code-search, and business records used across test routes and tools.
