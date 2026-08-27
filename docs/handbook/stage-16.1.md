# Core Source Sync Contracts and Checkpointing  `stage-16.1`

This stage defines the basic contract for bringing outside content into the system and remembering where syncing left off. It is part of the main work loop, after sources have been configured and before search indexes are updated. The package marker, __init__.py, is just the doorway: it lets Python treat this folder as the place where source-sync code lives.

backend.py acts like an adapter plug. External connectors may each describe files or pages in their own way. This code takes their stream of records and turns it into the project’s standard SyncResult, so the rest of the system can treat every source consistently. That includes knowing what was fetched, what checkpoint to resume from, and what may have been deleted.

sync.py does the core work. It fetches documents, saves their page text safely, records checkpoints, tracks changes and deletions, and then offers those page changes to downstream indexers so search can stay up to date.

## Files in this stage

### Package Boundary
Defines the import boundary for the core source-sync package.

### `core/src/ufo/sources/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a file named `__init__.py` tells the interpreter that a directory should be treated as an importable package. That means code elsewhere can refer to modules inside this folder using names like `ufo.sources.some_module` instead of relying on raw file paths.

Even though this file does not define any functions, classes, or settings, it still matters for the shape of the project. Think of it like a label on a drawer: the label does not contain the tools, but it lets everyone know the drawer is part of the organized toolbox. Without it, depending on the Python version and packaging setup, imports from this directory might fail or behave differently.

Because it is empty, it does not run any startup logic, change global state, or connect to anything. Its job is purely structural: it helps Python and project readers understand that `sources` is a named part of the `ufo` codebase.


### Provider Stream Adapter
Bridges external connector record streams into the standard sync contract used by the rest of the system.

### `core/src/ufo/sources/backend.py`

`orchestration` · `source sync run`

Connectors talk to outside services such as GitHub, Zendesk, or Freshdesk and return records in pages. The core sync engine wants something simpler: one result per run, containing pages to save, records to delete, a cursor for where to resume, and a count of unusable records. This file translates between those worlds.

`ConnectorBackend` is the main adapter. Given a source row, it finds the right connector stream, asks the authentication proxy for a usable credential, calls the connector, and converts each provider record into a `Page`. A `Page` is the project’s recallable document shape: it has a stable source reference, text body, title, stream name, and optional timestamps.

The file also protects sync jobs from running forever. Incremental streams are capped at a fixed number of saved records per run. If a connector provides its own checkpoint cursor, this backend stores it. If not, it stores a small “backfill envelope” saying where the run began, how many records were already skipped, and the best timestamp-like watermark seen so far. This is like putting a bookmark plus a note saying “skip the first 5,000 items next time.”

Full snapshot streams are different. They are allowed to enumerate everything because their result is used to detect missing records and tombstone deletions. Capping those would make deletion detection unsafe.

#### Function details

##### `binding_name`  (lines 101–111)

```
def binding_name(provider: str, account: str, base_url: str | None) -> str
```

**Purpose**: Builds a stable, human-looking name for a connector binding. The name is based on the provider, account, and optional tenant URL, so the same connection gets the same name in different parts of the system.

**Data flow**: It receives a provider name, account name, and optional base URL. It serializes those values in a consistent order, hashes them, takes a short digest, and returns a name like the provider plus that digest. The input details are not exposed directly, but they still determine the final name.

**Call relations**: This is a standalone naming helper. It relies on JSON serialization for a consistent byte string and SHA-256 hashing for a compact identity marker.

*Call graph*: 2 external calls (sha256, dumps).


##### `ConnectorBackend.fetch`  (lines 149–256)

```
async def fetch(self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Runs one sync pass for one connector stream. It gets credentials, calls the connector, converts records into pages, tracks deletions, and decides what cursor should be saved for the next run.

**Data flow**: It starts with a connector source config, the previously saved cursor, and source authentication context. It asks the auth proxy for a credential, finds the configured stream, chooses the correct starting cursor, and reads pages from the connector. Each record is either turned into a `Page`, skipped because it belongs to an already-counted prefix, or dropped if it cannot fit the page model. At the end, it returns a `SyncResult` containing saved pages, deletions, whether this was a full snapshot, the next cursor, and the number of dropped records.

**Call relations**: This is the main flow in the file. It calls `_stream` to find the stream declaration, `_decode_cursor` to understand its own saved backfill envelope, `_page` to convert individual provider records, and `_max_str` to advance a simple watermark. When a connector grant is unusable, it raises `StreamSkipped` so the wider sync system can treat the run as waiting on access instead of as a normal crash.

*Call graph*: calls 5 internal fn (_decode_cursor, _page, _stream, _max_str, __init__); 4 external calls (__init__, __init__, dumps, warn).


##### `ConnectorBackend._stream`  (lines 258–262)

```
def _stream(self, name: str) -> StreamSpec
```

**Purpose**: Finds the stream specification named in a source row. A stream specification describes what kind of records to fetch and how to interpret them.

**Data flow**: It receives a stream name and looks through the connector’s declared streams. If it finds a matching stream, it returns that `StreamSpec`; if not, it raises an error explaining that the connector does not have that stream.

**Call relations**: It is used by `ConnectorBackend.fetch` near the start of a run. Fetch needs this lookup before it can call the connector or know which fields act as IDs, timestamps, cursors, and deletion markers.

*Call graph*: called by 1 (fetch).


##### `ConnectorBackend._decode_cursor`  (lines 265–282)

```
def _decode_cursor(cursor: str | None) -> '_BackfillEnvelope | None'
```

**Purpose**: Recognizes the backend’s own special resume cursor for capped backfills. If the cursor is just normal connector state, it leaves it alone by returning nothing.

**Data flow**: It receives the stored cursor string, if any. It tries to parse it as JSON. Only a JSON object containing the reserved `ufo_backfill` key is treated as this backend’s envelope; that envelope is validated and returned. Plain strings, ordinary connector JSON, or missing cursors come out as `None`. A malformed envelope raises an error because this backend is supposed to be the only writer of that format.

**Call relations**: It is called by `ConnectorBackend.fetch` before reading an incremental stream. Fetch uses the result to decide whether to resume directly from the stored cursor or to re-drive the connector from an older origin and skip records already consumed.

*Call graph*: called by 1 (fetch); 1 external calls (loads).


##### `ConnectorBackend._page`  (lines 284–326)

```
def _page(self, stream: StreamSpec, record: dict[str, Any]) -> Page | None
```

**Purpose**: Turns one provider record into the project’s standard `Page` object. If the record cannot be represented safely, it logs a warning and drops only that record instead of failing the whole sync run.

**Data flow**: It receives a stream specification and a raw provider record. It builds a stable source reference, asks the connector to render a title and body, extracts created and updated timestamps, and tries to construct a `Page`. If validation fails, it emits a warning with the bad field information and returns `None`; otherwise it returns the completed page.

**Call relations**: It is called repeatedly by `ConnectorBackend.fetch` for each non-skipped record. It delegates small details to `_record_ref` for the stable ID and `_record_timestamp` for timestamp cleanup, then hands the finished shape back to fetch so it can be included in the final `SyncResult`.

*Call graph*: calls 2 internal fn (_record_ref, _record_timestamp); called by 1 (fetch); 3 external calls (__init__, warn, validation_fault).


##### `_record_timestamp`  (lines 329–360)

```
def _record_timestamp(record: dict[str, Any], field: str | None, *, connector: str, stream: str) -> str | None
```

**Purpose**: Extracts and normalizes a timestamp from a provider record. It keeps bad timestamp data from poisoning the whole page by warning and returning no timestamp instead.

**Data flow**: It receives a record, the field path to read, and names of the connector and stream for warning messages. If no field is configured or the value is missing, it returns `None`. If the value is a string or integer, it tries to normalize it into the page timestamp format. If the value is malformed or of an unsupported type, it logs a warning and returns `None`.

**Call relations**: It is called by `ConnectorBackend._page` for created-at and updated-at fields. It uses `get_path` when the timestamp is nested inside the record and `normalize_page_timestamp` to turn accepted inputs into the project’s standard timestamp text.

*Call graph*: called by 1 (_page); 3 external calls (warn, get_path, normalize_page_timestamp).


##### `_record_ref`  (lines 363–367)

```
def _record_ref(stream: StreamSpec, record: dict[str, Any]) -> str
```

**Purpose**: Creates the stable per-record reference used in page IDs and deletion IDs. It prefers the stream’s declared primary key, but falls back to hashing the whole record if that key is not a simple string or number.

**Data flow**: It receives the stream specification and one provider record. It reads the configured primary key. If the value is a string or integer, it returns that value as text. Otherwise it serializes the whole record in a consistent order, hashes it, and returns the hash.

**Call relations**: It is called by `ConnectorBackend._page` before building the `Page`. The resulting reference becomes part of `stream/name` style identifiers, so re-fetches, updates, and delete notices can all point at the same logical item.

*Call graph*: called by 1 (_page); 2 external calls (sha256, dumps).


##### `_max_str`  (lines 370–375)

```
def _max_str(current: str | None, value: Any) -> str | None
```

**Purpose**: Keeps the largest string watermark seen so far. This is used for streams where the cursor can advance by comparing string values, such as sortable timestamp strings.

**Data flow**: It receives the current watermark and a new candidate value. If the candidate is not a string, the current watermark is kept. If there is no current watermark or the candidate sorts later, the candidate becomes the new watermark. The returned value is the updated watermark.

**Call relations**: It is called inside `ConnectorBackend.fetch` while records are being read. Fetch uses it to remember progress across records when the stream has a cursor field but does not necessarily provide a native page checkpoint.

*Call graph*: called by 1 (fetch).


### Sync State and Changes
Implements document syncing, text persistence, checkpointing, deletion tracking, and change exposure for indexers.

### `core/src/ufo/sources/sync.py`

`domain_logic` · `scheduled sync ticks and downstream page-feed reads`

This file turns outside content into internal “pages” that the rest of the system can read. A source can be a local folder or an extension-backed provider such as a connector. Each backend knows how to fetch its own data, while the core sync driver knows the shared routine: find sources that are due, claim one so two workers do not sync it at once, ask its backend for pages, save changed bodies into blob storage, update page rows in the database, and mark missing pages as deleted when the backend says the fetch was a full snapshot.

The design separates fetching from storing. Backends return `SyncResult`, which is like a delivery note: here are the pages, here is the next cursor to resume later, here are explicit deletes, and whether this was a full list. The driver compares each page’s digest, a content fingerprint, so unchanged text is not written again.

The file also deals carefully with failure. Real providers refuse streams for many reasons: missing permissions, expired cursors, bad response shapes, or temporary errors. The driver records failures, backs off repeated errors, and “parks” repeatedly refused streams so the system does not ask every minute forever. Finally, `CorePageFeed` lets downstream indexers replay page changes in a stable order, like reading a ledger from the last bookmark.

#### Function details

##### `SourceRowConfig.requested_fields`  (lines 94–97)

```
def requested_fields(cls) -> frozenset[str]
```

**Purpose**: This tells registration code which non-identity configuration fields must still match when a source is registered again. It separates fields the user actually requested from fields the system resolved automatically.

**Data flow**: It reads the class-level sets `non_identity_fields` and `resolved_fields` → subtracts resolved fields from non-identity fields → returns the fields that callers must repeat exactly.

**Call relations**: Backend-specific config models use this rule when deciding whether a new registration is really the same source row. It supports the broader source identity scheme used when sources are created or re-created.


##### `normalize_page_timestamp`  (lines 100–120)

```
def normalize_page_timestamp(value: str) -> str
```

**Purpose**: This accepts a page timestamp from a provider and turns it into one consistent UTC ISO timestamp. It protects the rest of the system from mixed timestamp formats.

**Data flow**: It receives a string → treats all-digit values as Unix time, and other values as ISO date/time text → rejects impossible or timezone-less times except plain dates → returns a normalized UTC timestamp with microseconds.

**Call relations**: Page model validation calls this through `Page.normalize_timestamp` whenever a fetched page includes creation or update times. That means sync backends can send common timestamp formats while stored pages stay consistent.

*Call graph*: called by 1 (normalize_timestamp); 2 external calls (fromisoformat, fromtimestamp).


##### `Page.digest`  (lines 136–137)

```
def digest(self) -> str
```

**Purpose**: This computes a stable fingerprint of a page body. The sync driver uses it to decide whether the text really changed.

**Data flow**: It reads the page’s `body` text → encodes it and hashes it with SHA-256, a common one-way fingerprint algorithm → returns a string beginning with `sha256:`.

**Call relations**: During `SyncDriver._commit`, each fetched page’s digest is compared with the previous database value. If the digest is unchanged, the driver can skip rewriting the body.

*Call graph*: 1 external calls (sha256).


##### `Page.normalize_timestamp`  (lines 141–144)

```
def normalize_timestamp(cls, value: str | None) -> str | None
```

**Purpose**: This validates and standardizes the optional `created_at` and `updated_at` fields on a fetched page. It keeps bad provider dates from entering the page table.

**Data flow**: It receives either `None` or a timestamp string → leaves `None` alone → otherwise sends the string to `normalize_page_timestamp` → returns the normalized timestamp.

**Call relations**: Pydantic, the data validation library, runs this when a `Page` is built by a backend such as `FolderSource.fetch` or connector code. It is the gatekeeper before page metadata reaches the sync driver.

*Call graph*: calls 1 internal fn (normalize_page_timestamp).


##### `StreamSkipped.__init__`  (lines 194–197)

```
def __init__(self, reason: str, *, awaits_grant: bool=False) -> None
```

**Purpose**: This builds a special exception for a source stream that is refused but not truly broken, such as a missing permission or disabled provider feature. It carries whether the stream is waiting for a grant event.

**Data flow**: It receives a human-readable `reason` and an `awaits_grant` flag → stores both on the exception → produces an exception object that can be raised by a backend.

**Call relations**: Connector backends and provider paginators raise this when they know a stream should be skipped. `SyncDriver.run` catches it and routes the source to `_skip` instead of the normal failure path.

*Call graph*: called by 49 (fetch, paginate, paginate, paginate, paginate, _org_stream, paginate, paginate, paginate, paginate (+15 more)).


##### `validation_fault`  (lines 200–206)

```
def validation_fault(error: ValidationError) -> str
```

**Purpose**: This turns a data validation error into a safe, compact explanation. It names which fields failed and why without copying the rejected values.

**Data flow**: It receives a `ValidationError` → reads its structured error list → formats each field path and error type → returns a semicolon-separated summary.

**Call relations**: `SyncDriver._report_failed` uses this when a backend config or fetched model fails validation. The resulting text goes into failure telemetry without leaking provider payloads or user-entered secrets.

*Call graph*: called by 1 (_report_failed); 1 external calls (errors).


##### `response_fault`  (lines 209–235)

```
def response_fault(response: httpx.Response) -> str
```

**Purpose**: This extracts the useful error messages from a provider HTTP response, especially GraphQL-style `errors` arrays. It avoids logging the full response body, which may contain sensitive data.

**Data flow**: It receives an HTTP response → tries to parse JSON → looks for a list named `errors` → pulls each error’s `message` and optional code → returns a short combined reason, or an empty string if none is safe and recognizable.

**Call relations**: `SyncDriver._report_failed` calls this for HTTP status failures. It adds provider-specific context to logs while still keeping raw response bodies out of telemetry.

*Call graph*: called by 1 (_report_failed); 1 external calls (json).


##### `StreamFault.__init__`  (lines 245–247)

```
def __init__(self, reason: str) -> None
```

**Purpose**: This builds a backend-authored failure for provider data that has the wrong shape or cannot be read. It lets the backend explain the provider problem in safe words.

**Data flow**: It receives a `reason` string → stores it on the exception → produces an exception that sync failure reporting can recognize.

**Call relations**: Several extension backends raise this when fetched content or provider metadata is unusable. `SyncDriver._report_failed` treats it specially and reports its safe reason as the provider fault.

*Call graph*: called by 6 (_read, _markdown_entries, _spool_tarball, _refuse_client_error, decoded, _sheet_value_records).


##### `SourceBackend.config_model`  (lines 287–287)

```
def config_model(self) -> type[ConfigT]
```

**Purpose**: This protocol property says every source backend must declare the typed configuration model it expects. It prevents the core from treating backend settings as an unstructured bag.

**Data flow**: A backend exposes a model class → the sync driver reads it → source-row JSON is validated into that backend’s typed config before fetch.

**Call relations**: `SyncDriver._fetch` relies on this property before calling `fetch`. Concrete backends such as `FolderSource` provide the model.


##### `SourceBackend.fetch`  (lines 289–289)

```
async def fetch(self, config: ConfigT, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This protocol method is the contract every source backend must fulfill: given config, a resume cursor, and workspace auth, return the current fetched pages. It is the seam between provider-specific code and the shared sync engine.

**Data flow**: It receives validated backend config, the previous cursor, and source authentication context → the backend contacts or reads its source → returns a `SyncResult` describing pages, deletes, cursor, and snapshot status.

**Call relations**: `SyncDriver._fetch` calls this after preparing config and auth. Implementations include `FolderSource.fetch` and extension-provided connector backends.


##### `FolderSource.fetch`  (lines 303–314)

```
async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This reads a local directory and turns every file into a page. It is the built-in source backend for simple file-based content.

**Data flow**: It receives folder configuration → reads files on a background thread so the async event loop is not blocked → wraps each file’s relative path and text into a `Page` → returns a full-snapshot `SyncResult`.

**Call relations**: `SyncDriver._fetch` calls this when the source row’s backend is `folder`. Its full snapshot result lets `_commit` tombstone pages for files that disappeared.

*Call graph*: 4 external calls (__init__, __init__, to_thread, Path).


##### `FolderSource._read`  (lines 317–324)

```
def _read(root: Path) -> tuple[tuple[str, str], ...]
```

**Purpose**: This does the actual disk scan for `FolderSource.fetch`. It reads every file under a root folder as UTF-8 text.

**Data flow**: It receives a filesystem path → checks that it is a directory → walks all nested files in sorted order → returns pairs of relative path and decoded text.

**Call relations**: `FolderSource.fetch` runs this in a worker thread. If the folder itself is missing, the error bubbles up so the sync fails instead of deleting all previously indexed files by accident.

*Call graph*: 2 external calls (is_dir, rglob).


##### `source_row_id`  (lines 327–347)

```
def source_row_id(workspace_id: UUID, backend: str, config: Mapping[str, object], *, connection_id: UUID | None=None, non_identity_keys: frozenset[str]=frozenset()) -> UUID
```

**Purpose**: This creates the stable database ID for a source row. The same workspace, backend, and identity-defining config produce the same ID every time.

**Data flow**: It receives workspace ID, backend name, config values, optional connection ID, and keys to ignore for identity → removes non-identity config keys → serializes the remaining identity in sorted order → returns a deterministic UUID.

**Call relations**: `register_sources` uses this when boot-time configured sources are inserted. Because IDs are deterministic, restarting the app does not create duplicate source rows.

*Call graph*: called by 1 (register_sources); 2 external calls (dumps, uuid5).


##### `page_id_for`  (lines 350–353)

```
def page_id_for(source_id: UUID, source_ref: str) -> UUID
```

**Purpose**: This creates the stable database ID for one page inside one source. It makes updates, re-fetches, and deletes all point to the same page row.

**Data flow**: It receives a source ID and the page’s source-local reference → combines them into a namespaced string → returns a deterministic UUID.

**Call relations**: `SyncDriver._commit` uses this for every fetched page and explicit delete. That is how the driver knows whether it is updating an existing page or creating a new one.

*Call graph*: called by 1 (_commit); 1 external calls (uuid5).


##### `register_sources`  (lines 356–421)

```
async def register_sources(configured: tuple[SourceEntry, ...]) -> None
```

**Purpose**: This ensures that sources listed in configuration exist in the database when the app starts. It also grants the main agent access to newly inserted sources.

**Data flow**: It receives configured source entries → opens a workspace database transaction → finds the workspace and main agent → computes each source’s stable ID → inserts missing live rows and matching grants, while leaving removed rows alone.

**Call relations**: This runs at boot, separate from the polling sync job. Later, `SyncDriver` can claim and sync the rows this function created.

*Call graph*: calls 1 internal fn (source_row_id); 4 external calls (now, insert, select, workspace_tx).


##### `_rescheduled`  (lines 439–450)

```
def _rescheduled(claimed: ClaimedSource, when: datetime | sa.Case[datetime]) -> sa.Case[datetime]
```

**Purpose**: This protects manual resync requests from being overwritten by a sync that started earlier. It decides what `next_sync_at` should become when a claimed source is released.

**Data flow**: It receives the claimed source and a proposed future time → builds a database expression that uses that time only if no newer scheduling request appeared after the claim began → otherwise keeps the existing database time.

**Call relations**: `SyncDriver._write`, `_release`, and `_skip` use this when finishing a source. It is the small rule that keeps background sync completion from swallowing a user- or event-triggered resync.

*Call graph*: called by 3 (_release, _skip, _write); 1 external calls (case).


##### `_stream_tags`  (lines 453–458)

```
def _stream_tags(source: ClaimedSource) -> dict[str, str]
```

**Purpose**: This builds low-cardinality metric tags that identify the provider and stream type involved in a sync outcome. Low-cardinality means the tag values stay limited enough for monitoring systems to handle well.

**Data flow**: It receives a claimed source → reads the backend name and the `stream` value from config if present → returns a small tag dictionary.

**Call relations**: `SyncDriver.run`, `_report_ok`, `_report_failed`, `_report_parked`, and `_check_tags` use these tags for logs and metrics. It keeps stream-level reporting consistent across success, failure, skip, and park paths.

*Call graph*: calls 1 internal fn (_config_value); called by 5 (_report_failed, _report_ok, _report_parked, run, _check_tags).


##### `_check_tags`  (lines 461–469)

```
def _check_tags(source: ClaimedSource) -> dict[str, str]
```

**Purpose**: This builds service-check tags that identify one exact source row. Service checks need the source ID so one failing source is not confused with another similar stream.

**Data flow**: It receives a claimed source → starts with `_stream_tags` → adds the source row ID as text → returns the full service-check tag dictionary.

**Call relations**: `SyncDriver._report_ok` and `_report_failed` use this for health status updates. A healthy run for one source can then clear only that source’s alert, not another source with the same provider.

*Call graph*: calls 1 internal fn (_stream_tags); called by 2 (_report_failed, _report_ok).


##### `_config_value`  (lines 472–474)

```
def _config_value(source: ClaimedSource, key: str) -> str
```

**Purpose**: This safely reads a string value from a source config map. It returns an empty string when the key is missing or not a string.

**Data flow**: It receives a claimed source and a config key → looks up the value → returns it if it is a string, otherwise `""`.

**Call relations**: _stream_tags and sync reporting functions use this for fields such as `stream` and `account`. It prevents logs and metrics from receiving unexpected non-string values.

*Call graph*: called by 3 (_report_failed, _report_ok, _stream_tags).


##### `_readers_remain`  (lines 493–519)

```
def _readers_remain() -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database test for whether a source still has at least one live reader. A source whose every granted agent is archived should not spend work fetching content nobody can use.

**Data flow**: It creates SQL conditions → checks whether the source has no grants, or has at least one grant to an unarchived agent → returns that condition for larger queries.

**Call relations**: `SyncDriver.candidate_workspaces` and `_claim_due` include this condition. It keeps unreadable archived-only sources out of the sync queue until a reader is restored.

*Call graph*: called by 2 (_claim_due, candidate_workspaces); 5 external calls (and_, exists, literal, or_, select).


##### `SyncDriver.candidate_workspaces`  (lines 541–562)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This finds which workspaces currently have source rows due for syncing. It lets the scheduler avoid opening workspace-specific work when nothing is ready.

**Data flow**: It reads the current time → queries the owner-level database view for distinct workspaces with due, live, unclaimed or expired-claim sources that still have readers → returns their workspace IDs.

**Call relations**: A dispatcher calls this before binding work to a workspace. Once a workspace is selected, `SyncDriver.run` can claim and process its due sources.

*Call graph*: calls 1 internal fn (_readers_remain); 4 external calls (now, or_, select, owner_tx).


##### `SyncDriver.run`  (lines 564–583)

```
async def run(self) -> None
```

**Purpose**: This is the main per-workspace sync loop. It claims due sources, fetches them, commits successful results, and routes skips or failures to the right recovery path.

**Data flow**: It creates a unique claim token → asks `_claim_due` for source rows it owns for this run → for each source, calls `_fetch` and `_commit` → on `StreamSkipped`, logs and calls `_skip` → on other errors, computes backoff, reports failure, and releases the claim.

**Call relations**: This is the driver method the scheduled job uses after a workspace has been selected. It ties together claiming, backend fetching, database writing, telemetry, and retry scheduling.

*Call graph*: calls 8 internal fn (_claim_due, _commit, _error_backoff, _fetch, _release, _report_failed, _skip, _stream_tags); 4 external calls (suppress, now, log, uuid4).


##### `SyncDriver._claim_due`  (lines 585–636)

```
async def _claim_due(self, claim: str) -> tuple[ClaimedSource, ...]
```

**Purpose**: This reserves a batch of due sources for the current worker. The claim stops two sync workers from writing the same source at the same time.

**Data flow**: It receives a claim token → selects due source rows that are live, readable, and not actively claimed → uses row locking on PostgreSQL when available → writes the claim and expiry time → returns `ClaimedSource` objects describing the reserved rows.

**Call relations**: `SyncDriver.run` calls this first. The returned claim token is later checked by `_write`, `_release`, and `_skip` so only the worker that claimed the source can finish it.

*Call graph*: calls 1 internal fn (_readers_remain); called by 1 (run); 7 external calls (__init__, now, timedelta, or_, select, update, workspace_tx).


##### `SyncDriver._fetch`  (lines 638–657)

```
async def _fetch(self, source: ClaimedSource) -> SyncResult
```

**Purpose**: This prepares one claimed source for its backend and asks the backend to fetch pages. It is where stored JSON config becomes typed backend config.

**Data flow**: It receives a claimed source → finds the matching backend → validates the source config using that backend’s model → resolves optional identity and credential access → builds `SourceAuth` → calls the backend’s `fetch` method → returns the `SyncResult`.

**Call relations**: `SyncDriver.run` calls this after claiming a source. Its result goes to `_commit`, while exceptions are handled by the skip or failure branches in `run`.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `SyncDriver._commit`  (lines 659–706)

```
async def _commit(self, source: ClaimedSource, result: SyncResult) -> None
```

**Purpose**: This compares fetched pages with what is already stored and prepares the minimum necessary writes. It avoids rewriting unchanged page bodies.

**Data flow**: It receives a claimed source and `SyncResult` → reads prior pages with `_prior_pages` → assigns stable page IDs → compares digests and browse metadata → writes changed bodies to blob storage → builds lists of changed pages, metadata-only updates, fetched IDs, and deletes → passes them to `_write` → reports success.

**Call relations**: `SyncDriver.run` calls this after `_fetch` succeeds. It hands actual database mutation to `_write` and success telemetry to `_report_ok`.

*Call graph*: calls 4 internal fn (_prior_pages, _report_ok, _write, page_id_for); called by 1 (run); 2 external calls (__init__, __init__).


##### `SyncDriver._prior_pages`  (lines 708–740)

```
async def _prior_pages(self, source_id: UUID) -> dict[UUID, tuple[str, bool, PageBrowse]]
```

**Purpose**: This reads the current stored pages for a source before a new sync result is applied. It gives `_commit` the baseline needed to detect changes.

**Data flow**: It receives a source ID → queries the page table for digest, tombstone status, and browse metadata → returns a dictionary keyed by page ID.

**Call relations**: `SyncDriver._commit` calls this before comparing fetched pages. The returned map lets `_commit` distinguish new pages, changed bodies, metadata-only changes, unchanged pages, and previously tombstoned pages.

*Call graph*: called by 1 (_commit); 3 external calls (__init__, select, workspace_tx).


##### `SyncDriver._write`  (lines 742–867)

```
async def _write(self, source: ClaimedSource, next_cursor: str | None, changed: list[ChangedPage], metadata: list[PageBrowse], fetched: list[UUID], deleted: list[UUID], snapshot: bool) -> int
```

**Purpose**: This persists one sync result to the database and releases the source claim on success. It inserts or updates changed pages, updates metadata, tombstones deleted pages, and schedules the next normal sync.

**Data flow**: It receives the claimed source, next cursor, changed pages, metadata-only pages, fetched IDs, explicit deleted IDs, and snapshot flag → verifies the source still belongs to this claim → writes changed page rows and metadata updates → marks explicit deletes and snapshot-missing pages as tombstones → updates live page subject if needed → resets error/refusal state and clears the claim → returns the number of pages tombstoned.

**Call relations**: `SyncDriver._commit` calls this after preparing the write lists and blob bodies. It uses `_rescheduled` so a newer resync request is not overwritten while the successful run is being completed.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (_commit); 6 external calls (now, timedelta, insert, select, update, workspace_tx).


##### `SyncDriver._report_ok`  (lines 869–892)

```
async def _report_ok(self, source: ClaimedSource, fetched: int, written: int, tombstoned: int, dropped: int) -> None
```

**Purpose**: This records a successful sync run in logs and service health checks. It includes how many pages were fetched, written, tombstoned, or dropped.

**Data flow**: It receives source details and counts → builds stream and check tags → writes a success log event → emits an OK service check, with telemetry failures suppressed so they do not break syncing.

**Call relations**: `SyncDriver._commit` calls this after `_write` succeeds. Its OK service check is what clears a previous critical sync status for that exact source row.

*Call graph*: calls 3 internal fn (_check_tags, _config_value, _stream_tags); called by 1 (_commit); 3 external calls (suppress, emit_service_check, log).


##### `SyncDriver._error_backoff`  (lines 894–902)

```
def _error_backoff(self, source: ClaimedSource, now: datetime) -> tuple[int, datetime]
```

**Purpose**: This calculates how long to wait before retrying a source after a failure. Repeated failures wait longer, up to a maximum.

**Data flow**: It receives the source and current time → increments the consecutive error count → doubles the normal interval according to that count, capped at one hour → returns the new count and next retry time.

**Call relations**: `SyncDriver.run` calls this in the general exception path before reporting and releasing the source. `_report_failed` logs the computed values, and `_release` writes them.

*Call graph*: called by 1 (run); 1 external calls (timedelta).


##### `SyncDriver._report_failed`  (lines 904–962)

```
async def _report_failed(self, source: ClaimedSource, error: Exception, cursor_reset: bool, errors: int, next_sync_at: datetime) -> None
```

**Purpose**: This records a failed sync run without leaking sensitive provider data. It reports what kind of error happened, how many failures have happened in a row, and when retry is planned.

**Data flow**: It receives the source, exception, cursor-reset flag, error count, and next retry time → turns known error types into safe fault text using `response_fault` or `validation_fault` where appropriate → logs the failure → emits a failure metric → emits a critical service check.

**Call relations**: `SyncDriver.run` calls this after `_error_backoff` when fetch or commit fails. It does not release the database claim itself; `_release` handles that next.

*Call graph*: calls 5 internal fn (_check_tags, _config_value, _stream_tags, response_fault, validation_fault); called by 1 (run); 5 external calls (suppress, isoformat, emit_metric, emit_service_check, log_error).


##### `SyncDriver._release`  (lines 964–990)

```
async def _release(self, source: ClaimedSource, cursor_reset: bool, errors: int, next_sync_at: datetime) -> None
```

**Purpose**: This frees a source claim after a failed run and schedules the retry. It also clears a dead cursor when the provider says the cursor expired.

**Data flow**: It receives the claimed source, whether to reset the cursor, the new error count, and next retry time → updates the source row with the chosen cursor, backoff schedule, error count, and cleared claim fields.

**Call relations**: `SyncDriver.run` calls this after `_report_failed`. It uses `_rescheduled` so a resync requested during the failed run can still happen immediately.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (run); 2 external calls (update, workspace_tx).


##### `SyncDriver._skip`  (lines 992–1057)

```
async def _skip(self, source: ClaimedSource, reason: str, *, awaits_grant: bool) -> None
```

**Purpose**: This handles a provider refusal that should not be treated as a normal error. It keeps existing pages, preserves the cursor, resets error count, and may slow the source down by parking it after repeated refusals.

**Data flow**: It receives a claimed source, refusal reason, and whether it awaits a grant event → increments the stored refusal count → schedules the next attempt at the normal interval, hourly park interval, or long grant-wait hold → clears the claim → if the row became parked, calls `_report_parked`.

**Call relations**: `SyncDriver.run` calls this when a backend raises `StreamSkipped`. It uses `_rescheduled` to respect newer resync requests and `_report_parked` to record repeated refusals.

*Call graph*: calls 2 internal fn (_report_parked, _rescheduled); called by 1 (run); 5 external calls (now, timedelta, case, update, workspace_tx).


##### `SyncDriver._report_parked`  (lines 1059–1084)

```
async def _report_parked(self, source: ClaimedSource, reason: str, refusals: int) -> None
```

**Purpose**: This records that a repeatedly refused stream has been parked, meaning retried less often. It is a warning, not an alert, because an operator usually cannot fix missing member permissions.

**Data flow**: It receives the source, refusal reason, and refusal count → builds stream tags → emits a warning log and a parked counter metric, suppressing telemetry failures.

**Call relations**: `SyncDriver._skip` calls this only after the database update confirms the row is parked. The next successful `_write` later clears the parked state.

*Call graph*: calls 1 internal fn (_stream_tags); called by 1 (_skip); 3 external calls (suppress, emit_metric, warn).


##### `PageFeed.pages_changed_since`  (lines 1124–1124)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: This protocol method defines how an indexer asks for page changes after a saved cursor. It is the contract for replaying the page-change ledger.

**Data flow**: A caller provides a cursor and limit → an implementation reads changed pages after that point → it returns a `PageBatch` with changes and the next cursor.

**Call relations**: Extension contexts can depend on this interface instead of knowing database and blob details. `CorePageFeed.pages_changed_since` is the core implementation.


##### `page_cursor`  (lines 1127–1136)

```
def page_cursor(cursor: object) -> tuple[int, UUID]
```

**Purpose**: This parses the saved page-feed cursor. The cursor is a bookmark made from a revision number and page ID.

**Data flow**: It receives an object → verifies it is a string shaped like `revision|uuid` → converts the revision to an integer and the page ID to a UUID → returns both, or raises an error for invalid cursors.

**Call relations**: `CorePageFeed.pages_changed_since` calls this when a caller resumes from a previous batch. It ensures bad cursors fail clearly before the database query is built.

*Call graph*: called by 1 (pages_changed_since); 1 external calls (UUID).


##### `CorePageFeed.pages_changed_since`  (lines 1148–1206)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: This returns a bounded batch of page changes after a cursor, with page bodies included for live pages. It is how downstream indexers catch up with synced content.

**Data flow**: It receives an optional cursor and requested limit → caps the limit to the feed maximum → queries page rows ordered by revision and ID after the cursor → reads each non-tombstoned body from blob storage, using an empty body for tombstones → builds `PageChange` objects → returns them with a next cursor pointing at the last row.

**Call relations**: Indexers call this through the `PageFeed` interface. It reads the rows written by `SyncDriver._write` and translates them into a replayable stream for indexing or deletion.

*Call graph*: calls 1 internal fn (page_cursor); 7 external calls (__init__, __init__, fromisoformat, and_, or_, select, workspace_tx).
