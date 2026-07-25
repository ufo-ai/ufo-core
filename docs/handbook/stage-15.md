# Persistence, workspace objects, and blob storage  `stage-15` (cross-cutting infrastructure)

This stage is the system’s storage backbone. It is shared behind the scenes by startup, the main work loop, and admin flows whenever they need to remember something safely. The database map in tables.py defines the shared table layout. db.py is the guarded doorway that opens database connections, runs updates to the schema, and keeps each workspace’s data separated. gateway_store.py keeps temporary signup claims before a workspace exists.

Large files use a separate “blob” store through blob.py, so the rest of the code can save bytes without knowing whether they are on disk or in S3-style storage. transcript.py and loop/transcript.py define and safely update saved conversations, including compacted summaries.

The workspace object layer in objects.py turns stored records into named things people can list, inspect, change, or delete, with validation and permission checks. Specific object types plug into it: agents, shared artifacts, connected accounts, synced source pages, external content sources, and user-created skills. Together, these pieces act like labeled shelves and locked cabinets for the project’s durable data.

## Files in this stage

### Database and onboarding storage
Shared relational storage definitions and access paths support workspace-scoped persistence and signup claim records.

### `core/src/ufo/schema/tables.py`

`data_model` · `database setup and all database access`

This file is the blueprint for the system’s database. It uses SQLAlchemy, a Python library for describing databases, to define one neutral schema that can be created in different database engines. Without this file, the rest of the application would not have a clear, shared understanding of where workspaces, members, conversations, agent runs, billing records, credentials, scheduled tasks, and synced content are stored.

The central object is `metadata`, which is like the binder that holds every table definition. Each `sa.Table(...)` entry adds one page to that binder. For example, `workspace` stores customer or team spaces, `member` stores people inside them, `agent` stores AI agents, `conversation` groups message activity, and `turn` records one unit of agent work. Other tables track incoming messages, costs, spending limits, external account grants, writebacks to chat surfaces, shared files, extension data, runtime heartbeats, synced sources, scheduled tasks, and indexed pages.

The file also encodes important guardrails. Foreign keys keep rows connected to valid parent rows, like ensuring a member belongs to an existing workspace. Unique rules prevent duplicate identities, names, or message sequence numbers. Check constraints reject impossible states, such as a negative spend amount or an invalid turn status. Indexes act like labeled tabs in a filing cabinet, helping the database quickly find common records such as pending work, parked turns, due scheduled tasks, or unacknowledged ledger exports.


### `core/src/ufo/db.py`

`io_transport` · `startup, request handling, background jobs, migrations, teardown`

This file protects the system’s tenancy boundary: the line that keeps one workspace’s data separate from another’s. In a multi-workspace service, a database connection is not just a pipe to storage. It must also know which workspace the current request or job belongs to, otherwise a bug could accidentally read or write the wrong tenant’s rows.

The main idea is simple: normal code should use `workspace_tx`, which opens a transaction and, for PostgreSQL, pins the current workspace ID into a database setting named `app.workspace_id`. Database row-level security, or RLS (rules inside the database that filter rows per user or tenant), then uses that setting to allow only the right rows. If no workspace was set, PostgreSQL policies fail closed instead of leaking data.

There is one deliberate exception: `owner_tx`. It opens a transaction without setting a workspace. Background sweeps use this to find work across all workspaces, but they are expected to re-enter the correct workspace before reading tenant-specific content.

The file also handles practical database setup. It builds async SQLAlchemy engines, forces their first connection to happen safely before multiple event loops use them, applies Alembic migrations to create or update tables, and adds SQLite-specific settings so local or test databases behave more reliably.

#### Function details

##### `_build_engine`  (lines 39–50)

```
def _build_engine(url: str) -> AsyncEngine
```

*Call graph*: calls 1 internal fn (_first_connect); called by 2 (init_db, init_owner_db); 1 external calls (create_async_engine).


##### `init_db`  (lines 53–57)

```
def init_db(url: str) -> None
```

*Call graph*: calls 1 internal fn (_build_engine).


##### `init_owner_db`  (lines 60–69)

```
def init_owner_db(url: str) -> None
```

*Call graph*: calls 1 internal fn (_build_engine).


##### `_first_connect`  (lines 72–92)

```
def _first_connect(engine: AsyncEngine) -> None
```

*Call graph*: called by 1 (_build_engine); 1 external calls (Thread).


##### `_first_connect.run`  (lines 79–86)

```
def run() -> None
```

*Call graph*: calls 1 internal fn (_open_and_close); 1 external calls (new_event_loop).


##### `_open_and_close`  (lines 95–97)

```
async def _open_and_close(engine: AsyncEngine) -> None
```

*Call graph*: called by 1 (run); 1 external calls (connect).


##### `dispose_db`  (lines 100–107)

```
async def dispose_db() -> None
```


##### `workspace_tx`  (lines 111–121)

```
async def workspace_tx() -> AsyncIterator[AsyncConnection]
```

*Call graph*: 1 external calls (text).


##### `owner_tx`  (lines 125–138)

```
async def owner_tx() -> AsyncIterator[AsyncConnection]
```


##### `apply_migrations`  (lines 141–157)

```
def apply_migrations(url: str, pack: str | None=None) -> None
```

*Call graph*: 3 external calls (__init__, upgrade, migration_locations).


##### `_sqlite_on_connect`  (lines 160–166)

```
def _sqlite_on_connect(dbapi_connection: Any, _connection_record: Any) -> None
```


##### `_sqlite_begin_immediate`  (lines 169–171)

```
def _sqlite_begin_immediate(connection: sa.Connection) -> None
```

*Call graph*: 1 external calls (exec_driver_sql).


### `control/src/ufo_control/gateway_store.py`

`io_transport` · `startup and onboarding request handling`

This file is the database layer for hosted onboarding. It gives the rest of the system a small, clear set of actions: create the database table, save a new claim, find the current unfinished claim for a signup surface, count verification attempts, mark a claim as verified, finish it with a workspace id, or delete it.

The central idea is custody: once an onboarding flow starts, the important facts are kept in Postgres so they survive process restarts and can be shared safely across running services. The table stores the email, email domain, hashed verification code, where the signup came from, expiry time, number of attempts, verification time, and final workspace result. A unique database index makes sure there is only one active unfinished claim for the same surface and reference, like allowing only one open ticket for the same desk and ticket number.

The `OnboardClaim` data class is the in-memory shape of one row from the table. `OnboardStore` is the small wrapper around an asyncpg connection pool. Asyncpg is an asynchronous Postgres library, meaning database calls can wait without blocking the whole program. One helper, `_aware`, makes sure timestamps coming back from the database have timezone information, which avoids subtle time comparison bugs around expiry.

#### Function details

##### `_aware`  (lines 47–50)

```
def _aware(value: datetime | None) -> datetime | None
```

**Purpose**: This helper makes a timestamp safe to use by ensuring it has timezone information. If the timestamp is missing a timezone, it treats it as UTC, the common worldwide time standard.

**Data flow**: It receives either a datetime value or nothing. If it receives nothing, it returns nothing. If it receives a datetime that already has timezone information, it returns it unchanged; otherwise it adds UTC timezone information and returns the corrected value.

**Call relations**: When `OnboardStore.live_claim` rebuilds an `OnboardClaim` from a database row, it calls `_aware` for timestamp fields. `_aware` uses the datetime object's `replace` operation only when it needs to attach UTC.

*Call graph*: called by 1 (live_claim); 1 external calls (replace).


##### `OnboardStore.ensure_table`  (lines 57–60)

```
async def ensure_table(self) -> None
```

**Purpose**: This prepares the database storage needed for onboarding claims. It creates the schema, table, and active-claim index if they do not already exist.

**Data flow**: It starts with the store's database connection pool. It borrows one connection, runs each setup SQL statement in order, and returns nothing after the database has the required structure.

**Call relations**: This is typically called during startup before onboarding requests are accepted. Other store methods assume the table and index already exist, so this method lays the groundwork they rely on.


##### `OnboardStore.insert_claim`  (lines 62–76)

```
async def insert_claim(self, claim: OnboardClaim) -> None
```

**Purpose**: This saves a new onboarding claim in the database. It is used when a user begins onboarding and the system needs a durable record of the verification step.

**Data flow**: It receives an `OnboardClaim` object containing the claim id, email details, hashed code, signup surface, attempt count, and expiry time. It borrows a database connection and inserts those fields into the onboarding table. It does not return a value; the change is the new row in Postgres.

**Call relations**: This method is called by higher-level onboarding code after it has created the claim details. It does not call other project helpers; it directly writes the row through asyncpg.


##### `OnboardStore.live_claim`  (lines 78–99)

```
async def live_claim(self, surface: str, surface_ref: str) -> OnboardClaim | None
```

**Purpose**: This looks up the unfinished onboarding claim for a particular signup location. It ignores claims that have already produced a workspace, so callers get only the currently active claim.

**Data flow**: It receives a `surface` and `surface_ref`, which together identify where the onboarding attempt came from. It queries Postgres for a matching row whose `resulting_workspace_id` is still empty. If there is no row, it returns `None`; if there is a row, it converts the database fields into an `OnboardClaim` object and returns it.

**Call relations**: Higher-level onboarding code uses this when it needs to continue or inspect an existing claim. While rebuilding the claim object, it calls `_aware` to normalize timestamp fields and then constructs an `OnboardClaim` for the caller.

*Call graph*: calls 1 internal fn (_aware); 1 external calls (__init__).


##### `OnboardStore.record_attempt`  (lines 101–102)

```
async def record_attempt(self, claim_id: UUID, attempts: int) -> None
```

**Purpose**: This updates how many times someone has tried to verify an onboarding claim. It helps the system enforce limits or track failed code entries.

**Data flow**: It receives the claim id and the new attempt count. It passes an update instruction and those values to the shared `_update` helper. It returns nothing; the database row is changed.

**Call relations**: This is a focused public method for attempt counting. Instead of writing SQL itself, it hands the actual database update to `OnboardStore._update`, the common helper also used by verification and completion updates.

*Call graph*: calls 1 internal fn (_update).


##### `OnboardStore.mark_verified`  (lines 104–105)

```
async def mark_verified(self, claim_id: UUID) -> None
```

**Purpose**: This records that an onboarding claim has passed verification. It stamps the database row with the current database time.

**Data flow**: It receives the claim id. It asks `_update` to set `verified_at` to `now()` in Postgres. It returns nothing; the visible result is that the claim row now has a verification timestamp.

**Call relations**: This is called when the onboarding flow has accepted the user's verification code. It delegates the common update mechanics to `OnboardStore._update`.

*Call graph*: calls 1 internal fn (_update).


##### `OnboardStore.complete`  (lines 107–108)

```
async def complete(self, claim_id: UUID, resulting_workspace_id: str) -> None
```

**Purpose**: This marks an onboarding claim as finished by recording the workspace that resulted from it. Once this is set, the claim is no longer considered active by `live_claim`.

**Data flow**: It receives the claim id and the resulting workspace id. It sends those to `_update`, which writes the workspace id into the database row. It returns nothing; the database now shows that the claim has completed.

**Call relations**: This is used near the end of a successful onboarding flow. It relies on `OnboardStore._update` for the database write, and its result affects future calls to `OnboardStore.live_claim`, which only returns claims without a resulting workspace id.

*Call graph*: calls 1 internal fn (_update).


##### `OnboardStore.delete_claim`  (lines 110–112)

```
async def delete_claim(self, claim_id: UUID) -> None
```

**Purpose**: This removes an onboarding claim from the database. It is useful when a claim should be discarded rather than completed.

**Data flow**: It receives a claim id, borrows a database connection, and runs a delete statement for that id. It returns nothing; if a matching row existed, it is gone afterward.

**Call relations**: Higher-level onboarding code can call this for cleanup or cancellation. Unlike the small field updates, it does not use `_update` because deleting a whole row is a different database action.


##### `OnboardStore._update`  (lines 114–118)

```
async def _update(self, assignment: str, claim_id: UUID, *values: object) -> None
```

**Purpose**: This is the shared helper for simple updates to one onboarding claim row. It keeps repeated database update code in one place.

**Data flow**: It receives a SQL assignment such as setting attempts, setting verified time, or setting the resulting workspace id, plus the claim id and any needed values. It borrows a connection and runs an update against the row with that id. It returns nothing; the matching row is modified.

**Call relations**: `OnboardStore.record_attempt`, `OnboardStore.mark_verified`, and `OnboardStore.complete` all call this helper when they need to change one field on a claim. It is an internal helper rather than the main interface callers should use directly.

*Call graph*: called by 3 (complete, mark_verified, record_attempt).


### Blob and transcript storage
Large byte storage and transcript helpers provide durable conversation, compaction, and shared file data.

### `core/src/ufo/blob.py`

`io_transport` · `cross-cutting storage access during request handling and background work`

A “blob” here means a chunk of bytes such as an attachment, workspace file, exported artifact, or saved record. This file is the storage adapter for those bytes. It defines a common promise, `BlobStore`, with actions like save, read, stream, copy, delete, and list. Then it provides two real versions of that promise: `FilesystemBlobStore`, which turns blob keys into files under a local root folder, and `S3BlobStore`, which stores the same kind of objects in an S3 bucket.

The important idea is that callers use slash-separated keys, like paths, but they do not need to know whether those keys map to disk files or cloud objects. Large data can be moved in pieces through streaming, so a huge file does not have to sit in memory all at once. Local writes use a temporary file and then replace the final file, like writing a new page off to the side before swapping it into a binder. This avoids leaving half-written files behind as the visible result. S3 writes use multipart uploads for large content, which is S3’s way of safely sending big objects in pieces.

The file also normalizes missing-object behavior: both backends raise `BlobNotFound` when a requested key is absent. Finally, `blob_store_for` builds the right backend from configuration.

#### Function details

##### `BlobStore.put`  (lines 46–46)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: This is the shared interface for saving a complete in-memory byte string under a blob key. Code uses it when the data is already small enough or already collected in memory.

**Data flow**: A caller provides a key and bytes. A concrete store, such as the filesystem or S3 version, writes those bytes under that key. Nothing is returned, but the stored object should be available afterward.

**Call relations**: This is the promise that implementations must satisfy. The sample extension calls it when exporting data that can be written as one complete byte value.

*Call graph*: called by 1 (export).


##### `BlobStore.put_file`  (lines 48–51)

```
async def put_file(self, key: str, source: Path) -> None
```

**Purpose**: This is the shared interface for storing an existing local file under a blob key without loading the whole file into memory. It is useful for larger exported files.

**Data flow**: A caller gives a destination key and a path to a local file. The chosen backend reads from that file and writes the bytes into blob storage. The result is a stored object at the key.

**Call relations**: Local and Docker sandbox export code call this when a produced file needs to become a stored blob. Each backend decides the safest and most efficient way to copy the file into storage.

*Call graph*: called by 2 (export, export).


##### `BlobStore.get`  (lines 53–53)

```
async def get(self, key: str) -> bytes
```

**Purpose**: This is the shared interface for reading a whole blob into memory as bytes. It is meant for stored objects that are expected to be reasonably small.

**Data flow**: A caller provides a key. The configured backend looks up that key and returns its bytes, or raises `BlobNotFound` if it is absent.

**Call relations**: Transcript compaction reading and Slack identity reading use this when they need the complete stored content at once. The concrete backend supplies the actual disk or S3 read.

*Call graph*: called by 2 (read_compaction_record, read_identity).


##### `BlobStore.exists`  (lines 55–55)

```
async def exists(self, key: str) -> bool
```

**Purpose**: This is the shared interface for asking whether a blob key is present. It lets callers check for optional stored data without trying to read it first.

**Data flow**: A caller provides a key. The backend checks its storage location and returns `true` if an object is present, otherwise `false`.

**Call relations**: Slack identity loading uses this before reading identity data. The interface keeps that check independent of whether the project is using local files or S3.

*Call graph*: called by 1 (read_identity).


##### `BlobStore.delete`  (lines 57–59)

```
async def delete(self, key: str) -> None
```

**Purpose**: This is the shared interface for removing a stored object. Deleting a key that is already absent is treated as harmless, so repeated cleanup attempts are safe.

**Data flow**: A caller provides a key. The backend asks the storage system to remove that object. Nothing is returned, and no error is expected just because the key was missing.

**Call relations**: This method is part of the common storage contract. Any code that needs cleanup can call it through `BlobStore` and leave the backend-specific deletion details to the implementation.


##### `BlobStore.get_stream`  (lines 61–61)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: This is the shared interface for reading a blob in chunks instead of all at once. It is used when the object may be large and should not fill memory.

**Data flow**: A caller provides a key. The backend opens the object and yields byte chunks one by one until the object is fully read, or raises `BlobNotFound` if the key is absent.

**Call relations**: This method belongs to the common storage contract. The filesystem and S3 implementations provide chunked reading in their own ways while presenting the same shape to callers.


##### `BlobStore.put_stream`  (lines 63–63)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: This is the shared interface for saving incoming chunks of bytes as one blob. It is useful when data arrives gradually, such as from a network stream or a large generated file.

**Data flow**: A caller provides a key and an asynchronous stream of byte chunks. The backend writes each chunk into storage and finishes with one complete object at the key.

**Call relations**: This method is the write-side partner to `get_stream`. Callers can feed data through the common interface while the selected backend chooses local temporary files or S3 multipart upload.


##### `BlobStore.copy`  (lines 65–70)

```
async def copy(self, src_key: str, dst_key: str) -> None
```

**Purpose**: This is the shared interface for duplicating one stored blob to another key inside the same store. It avoids making the application read the bytes out and write them back itself.

**Data flow**: A caller gives a source key and a destination key. The backend copies the stored object internally and leaves a duplicate at the destination, or raises `BlobNotFound` if the source is missing.

**Call relations**: Docker and E2B export paths use this when a file already in storage needs to be promoted or shared as an artifact. The S3 implementation can do this server-side, which saves time and bandwidth.

*Call graph*: called by 2 (export, export).


##### `BlobStore.list`  (lines 72–76)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: This is the shared interface for listing stored objects under a required key prefix. It gives callers a bounded view of one area of blob storage rather than scanning everything.

**Data flow**: A caller provides a prefix. The backend returns a sorted tuple of `BlobEntry` records, each carrying a key, byte size, and last-modified time, up to a fixed maximum.

**Call relations**: This method is part of the common read-view contract. Both backends use it to let higher-level code browse a conversation workspace, artifact area, or similar prefix-shaped group.


##### `FilesystemBlobStore.put`  (lines 85–90)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: This saves in-memory bytes as a file below the configured local storage root. It writes through a temporary file first so callers do not see a half-written final file.

**Data flow**: It receives a key and bytes. It turns the key into a safe path, creates parent folders, writes the bytes to a uniquely named temporary file, then replaces the destination path with that temporary file. The result is a complete local file.

**Call relations**: This is the filesystem implementation of `BlobStore.put`. It relies on `_resolve` to keep keys inside the storage root and uses background thread work so file I/O does not block the async event loop.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.put_file`  (lines 92–97)

```
async def put_file(self, key: str, source: Path) -> None
```

**Purpose**: This stores an existing local file into the filesystem blob store. It copies the file through a temporary destination so the visible blob appears only after the copy is complete.

**Data flow**: It receives a blob key and a source file path. It resolves the destination, makes needed folders, copies the source file to a temporary file, then replaces the final destination. Afterward the blob key points to the copied file.

**Call relations**: This implements `BlobStore.put_file` for local storage. Sandbox export code can call the interface, and this method performs the disk-level copy safely.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.get`  (lines 99–104)

```
async def get(self, key: str) -> bytes
```

**Purpose**: This reads a whole local blob file into memory. If the file is not present, it raises the project’s standard `BlobNotFound` error.

**Data flow**: It receives a key, resolves it to a safe file path, and reads all bytes from that file. The bytes are returned to the caller, or a missing file is translated into `BlobNotFound`.

**Call relations**: This is the filesystem implementation of `BlobStore.get`. It uses `_resolve` before reading so callers cannot escape the configured blob directory.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.exists`  (lines 106–108)

```
async def exists(self, key: str) -> bool
```

**Purpose**: This checks whether a local blob exists as a regular file. It is a quick yes-or-no lookup for optional local data.

**Data flow**: It receives a key, resolves it to a path under the root, and asks the filesystem whether that path is a file. It returns a boolean answer.

**Call relations**: This implements `BlobStore.exists` for local storage. Higher-level code can ask through the shared interface without knowing the answer comes from a filesystem check.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.delete`  (lines 110–112)

```
async def delete(self, key: str) -> None
```

**Purpose**: This removes a local blob file if it exists. Missing files are ignored so cleanup can be retried safely.

**Data flow**: It receives a key, resolves it to a safe path, and asks the filesystem to unlink, or remove, that file with missing files allowed. It returns nothing.

**Call relations**: This is the filesystem version of `BlobStore.delete`. It uses `_resolve` to avoid deleting anything outside the blob store root.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.get_stream`  (lines 114–127)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: This reads a local blob file piece by piece. It is meant for large files that should not be loaded into memory all at once.

**Data flow**: It receives a key, resolves and opens the file, then repeatedly reads fixed-size chunks and yields them to the caller. It closes the file afterward, and a missing file becomes `BlobNotFound`.

**Call relations**: This implements `BlobStore.get_stream` for local files. It hands chunks back to whatever caller is consuming the stream, while using thread offloading for blocking file reads.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.put_stream`  (lines 129–142)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: This writes a stream of incoming byte chunks into a local blob file. It protects the final destination by writing to a temporary file until the stream finishes successfully.

**Data flow**: It receives a key and an asynchronous source of chunks. It resolves the destination, opens a temporary file, writes each chunk, closes it, and replaces the final path. If anything fails, it closes and removes the temporary file.

**Call relations**: This is the filesystem implementation of `BlobStore.put_stream`. It works with callers that produce data gradually and uses the same atomic temporary-file pattern as the simpler write methods.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.copy`  (lines 144–152)

```
async def copy(self, src_key: str, dst_key: str) -> None
```

**Purpose**: This duplicates one local blob file to another local blob key. It first verifies the source exists, then writes the destination safely through a temporary file.

**Data flow**: It receives source and destination keys. It resolves the source, checks that it is a file, resolves the destination, copies the source to a temporary destination file, and replaces the final destination. If the source is missing, it raises `BlobNotFound`.

**Call relations**: This is the filesystem implementation of `BlobStore.copy`. Export flows can call the common copy operation, and this backend performs a local file copy.

*Call graph*: calls 1 internal fn (_resolve); 3 external calls (__init__, to_thread, uuid4).


##### `FilesystemBlobStore.list`  (lines 154–157)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: This lists local blob files whose keys start with a given prefix. It requires a prefix so callers do not accidentally scan the whole store.

**Data flow**: It receives a prefix. If the prefix is empty, it raises an error. Otherwise it runs `_walk` in a background thread and returns the resulting tuple of entries.

**Call relations**: This implements `BlobStore.list` for local storage. It delegates the actual directory walking and entry building to `_walk`.

*Call graph*: 1 external calls (to_thread).


##### `FilesystemBlobStore._walk`  (lines 159–180)

```
def _walk(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: This does the actual filesystem scan for `FilesystemBlobStore.list`. It turns matching files into `BlobEntry` records with key, size, and modification time.

**Data flow**: It receives a prefix, finds the local directory that could contain matching files, walks through files below it, filters out nonmatching keys and temporary files, gathers file metadata, sorts by key, and returns at most the configured maximum number of entries.

**Call relations**: `FilesystemBlobStore.list` calls this in a worker thread because walking directories can block. `_walk` uses `_resolve` to choose the starting directory safely and creates the entries that list callers receive.

*Call graph*: calls 1 internal fn (_resolve); 4 external calls (__init__, fromtimestamp, walk, Path).


##### `FilesystemBlobStore._resolve`  (lines 182–187)

```
def _resolve(self, key: str) -> Path
```

**Purpose**: This converts a blob key into a real filesystem path while enforcing that the path stays inside the configured storage root. It is the safety gate for all local file operations.

**Data flow**: It receives a key, joins it to the root directory, resolves any `..` or symbolic path pieces, and checks that the result is still below the root and not the root itself. It returns the safe path or raises an error if the key tries to escape.

**Call relations**: All filesystem read, write, delete, copy, stream, and walk operations call this before touching disk. It prevents a blob key from becoming an accidental path to unrelated files on the machine.

*Call graph*: called by 9 (_walk, copy, delete, exists, get, get_stream, put, put_file, put_stream).


##### `_is_missing_key`  (lines 190–191)

```
def _is_missing_key(error: ClientError) -> bool
```

**Purpose**: This recognizes S3 error responses that mean “that object does not exist.” It lets the S3 backend turn several provider-specific error codes into one project-level meaning.

**Data flow**: It receives a `ClientError` from the S3 library, looks inside the error response for its code, and returns `true` if that code is one of the known missing-object codes.

**Call relations**: S3 read, existence, stream, and copy methods call this when S3 reports an error. If it says the key is missing, those methods return `false` or raise `BlobNotFound` as appropriate.

*Call graph*: called by 4 (copy, exists, get, get_stream).


##### `S3BlobStore.put`  (lines 210–212)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: This saves in-memory bytes as one object in an S3 bucket. It is the cloud-storage version of writing a complete blob at once.

**Data flow**: It receives a key and bytes, gets or creates an S3 client for the current event loop, and sends a `put_object` request to store the bytes in the configured bucket. It returns nothing after S3 accepts the write.

**Call relations**: This implements `BlobStore.put` for S3. It first goes through `_client`, which reuses a cached S3 client instead of rebuilding one for every operation.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.put_file`  (lines 214–246)

```
async def put_file(self, key: str, source: Path) -> None
```

**Purpose**: This uploads a local file into S3, using multipart upload for non-empty files. Multipart upload means the file is sent in pieces and then S3 joins those pieces into one object.

**Data flow**: It receives a destination key and a local source path. It checks the file size, stores an empty object directly if the file is empty, otherwise opens the file, reads fixed-size ranges, uploads each part, and asks S3 to complete the upload. If an error happens, it aborts the upload and closes the file.

**Call relations**: This is the S3 implementation of `BlobStore.put_file`. Export code can provide a file path through the shared interface, and this method performs the cloud upload without reading the entire file into memory at once.

*Call graph*: calls 1 internal fn (_client); 1 external calls (to_thread).


##### `S3BlobStore.get`  (lines 248–258)

```
async def get(self, key: str) -> bytes
```

**Purpose**: This reads a whole S3 object into memory. It translates S3’s missing-object errors into the project’s `BlobNotFound` error.

**Data flow**: It receives a key, gets an S3 client, asks S3 for the object, reads the response body fully, and returns the bytes. If S3 says the key is missing, it raises `BlobNotFound`; other S3 errors are passed upward.

**Call relations**: This implements `BlobStore.get` for S3. It uses `_client` for the connection and `_is_missing_key` to normalize missing-object behavior.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.exists`  (lines 260–268)

```
async def exists(self, key: str) -> bool
```

**Purpose**: This checks whether an S3 object exists without downloading it. It uses S3 metadata lookup, which is lighter than reading the full object.

**Data flow**: It receives a key, gets an S3 client, and sends a `head_object` request. If S3 finds the object, it returns `true`; if S3 reports a missing key, it returns `false`; other errors are raised.

**Call relations**: This implements `BlobStore.exists` for S3. It relies on `_is_missing_key` so provider-specific missing-object codes become a simple boolean answer.

*Call graph*: calls 2 internal fn (_client, _is_missing_key).


##### `S3BlobStore.delete`  (lines 270–272)

```
async def delete(self, key: str) -> None
```

**Purpose**: This asks S3 to delete an object from the configured bucket. Like the common interface promises, deleting an absent key is safe from the caller’s point of view.

**Data flow**: It receives a key, gets an S3 client, and sends a delete request for that bucket and key. It returns nothing after the request completes.

**Call relations**: This is the S3 implementation of `BlobStore.delete`. It uses `_client` to share the S3 connection setup used by the other S3 operations.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get_stream`  (lines 274–285)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: This reads an S3 object in chunks. It is designed for large objects that should pass through the application gradually instead of all at once.

**Data flow**: It receives a key, gets an S3 client, requests the object, and yields fixed-size chunks from the response body. If S3 says the object is missing, it raises `BlobNotFound`.

**Call relations**: This implements `BlobStore.get_stream` for S3. It uses `_client` for access and `_is_missing_key` for consistent missing-key behavior.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.put_stream`  (lines 287–331)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: This writes an incoming stream of byte chunks into S3. Small streams are saved with one request, while larger streams are uploaded in S3 multipart form.

**Data flow**: It receives a key and an asynchronous stream of chunks. It buffers chunks until they reach the multipart part size, starts a multipart upload if needed, uploads each part, uploads the final remaining bytes, and completes the upload. If anything fails after multipart upload starts, it aborts the upload.

**Call relations**: This is the S3 implementation of `BlobStore.put_stream`. It lets callers produce data gradually while still using S3’s required multipart process for large objects.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.copy`  (lines 333–369)

```
async def copy(self, src_key: str, dst_key: str) -> None
```

**Purpose**: This duplicates an object inside the same S3 bucket without routing the object bytes through the application process. For very large objects, it uses S3’s multipart copy feature.

**Data flow**: It receives a source key and destination key. It first checks the source object and its size. Small enough objects are copied with one S3 copy request; larger objects are copied in byte ranges as multipart parts, then completed. If the source is missing, it raises `BlobNotFound`; if multipart copy fails, it aborts the unfinished copy.

**Call relations**: This implements `BlobStore.copy` for S3. Export flows use the common copy operation, and this method keeps the work inside S3, which avoids downloading and re-uploading large artifacts.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.list`  (lines 371–388)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: This lists S3 objects whose keys begin with a required prefix. It returns a bounded set of simple entries rather than exposing raw S3 pages to callers.

**Data flow**: It receives a prefix, rejects an empty one, gets an S3 client, pages through S3 list results, converts each object into a `BlobEntry`, stops at the configured maximum, and returns the entries as a tuple.

**Call relations**: This is the S3 implementation of `BlobStore.list`. It uses S3 pagination behind the scenes, while callers receive the same kind of entries they would get from the filesystem backend.

*Call graph*: calls 1 internal fn (_client); 1 external calls (__init__).


##### `S3BlobStore._client`  (lines 390–406)

```
async def _client(self) -> AioBaseClient
```

**Purpose**: This gets the reusable S3 client for the currently running async event loop. Reusing the client avoids repeated expensive setup and respects that the underlying network client belongs to one event loop.

**Data flow**: It looks up the current event loop, checks whether a client already exists for it, and returns that client if found. If not, it creates a new S3 client from the configured endpoint and region, stores it in the cache, and returns it. If another task already stored a client first, it closes the redundant one and logs if that cleanup fails.

**Call relations**: Every S3 operation calls this before talking to S3. It is the connection factory and cache that keeps blob traffic from repeatedly rebuilding the S3 service client.

*Call graph*: called by 9 (copy, delete, exists, get, get_stream, list, put, put_file, put_stream); 3 external calls (get_session, get_running_loop, log).


##### `blob_store_for`  (lines 409–421)

```
def blob_store_for(config: BlobConfig) -> FilesystemBlobStore | S3BlobStore
```

**Purpose**: This builds the correct blob store from configuration. It is the small factory that turns settings into either a local filesystem store or an S3-backed store.

**Data flow**: It receives a `BlobConfig`. If the configured backend is `filesystem`, it checks for a root path and returns a `FilesystemBlobStore`; if the backend is `s3`, it checks for a bucket and returns an `S3BlobStore` with the configured endpoint and region. Missing required settings raise a clear error.

**Call relations**: Startup or setup code can call this once it has loaded configuration. After this function returns, the rest of the system can use the common `BlobStore` behavior without branching on the chosen backend.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/transcript.py`

`io_transport` · `conversation persistence and readback`

A conversation with an AI can be long, and the system needs to save it durably so other tools can inspect it later. This file is the common rulebook for that saved data. Without it, one part of the project might write a transcript in one format while another part tries to read it in a different format, causing old conversations, debug views, or evaluation tools to break.

The main saved transcript is represented by `Conversation`. It contains the message list at a particular sequence number, plus optional extra context such as the system prompt and injected text used for a completed turn. The file turns that record into compact bytes by converting it to JSON, then compressing it with LZ4, a fast compression format. It also reverses that process when reading.

The file also describes “compaction” records. Compaction is when an older, bulky part of a conversation is summarized so the model can keep working with a smaller context window. For each compaction, the system stores the messages before, the messages after, and a structured summary explaining what was preserved. Helper functions build the storage paths and read these records back from a blob store, which is a generic place for storing named chunks of bytes.

#### Function details

##### `transcript_key`  (lines 37–38)

```
def transcript_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the storage name for the main saved transcript of one conversation. Someone uses it when they need to put or fetch the conversation messages from the blob store.

**Data flow**: It takes a conversation ID, which is a unique identifier, and inserts it into a fixed path pattern. The result is a string like a file path pointing to that conversation’s compressed transcript.

**Call relations**: This function provides the agreed address for transcript blobs. Writers and readers can use the same path convention so they meet at the same saved object.


##### `encode`  (lines 41–43)

```
def encode(conversation: Conversation) -> bytes
```

**Purpose**: Turns a `Conversation` object into compressed bytes suitable for durable storage. This keeps the saved transcript compact and in a predictable JSON shape.

**Data flow**: It receives a validated conversation record. It first turns that record into ordinary data, writes it as JSON with stable formatting, converts the JSON text into bytes, and then compresses those bytes. The output is the byte blob that can be stored.

**Call relations**: This is the write-side codec for conversations. Code that saves transcripts calls this before sending data to storage, while `decode` performs the matching read-side operation later.

*Call graph*: 2 external calls (model_dump, dumps).


##### `decode`  (lines 46–50)

```
def decode(body: bytes) -> Conversation
```

**Purpose**: Turns stored compressed transcript bytes back into a validated `Conversation`. It also gives callers a clear `TranscriptDecodeError` if the stored data is corrupt or no longer matches the expected shape.

**Data flow**: It receives compressed bytes from storage. It decompresses them, asks the `Conversation` model to validate and parse the JSON, and returns the resulting conversation object. If decompression or validation fails, it converts that failure into a transcript-specific error.

**Call relations**: This is the read-side partner to `encode`. Any reader of a saved transcript can rely on it to either return a proper `Conversation` or clearly report that the stored transcript cannot be understood.

*Call graph*: 1 external calls (__init__).


##### `compaction_key`  (lines 100–101)

```
def compaction_key(conversation_id: UUID, index: int, half: CompactionHalf) -> str
```

**Purpose**: Builds the storage name for one piece of a compaction record. A compaction has three saved pieces: the window before compaction, the window after compaction, and the summary.

**Data flow**: It takes a conversation ID, a compaction index, and which piece is being addressed. It combines them into a fixed path string under that conversation’s compaction folder.

**Call relations**: When `read_compaction_record` wants to fetch a compaction from storage, it calls this three times to locate the `before`, `after`, and `summary` blobs.

*Call graph*: called by 1 (read_compaction_record).


##### `decode_compaction`  (lines 104–113)

```
def decode_compaction(index: int, before: bytes, after: bytes, summary: bytes) -> CompactionRecord
```

**Purpose**: Rebuilds one complete compaction record from its three stored byte blobs. It validates both message windows and the structured summary before returning them together.

**Data flow**: It receives the compaction number plus compressed bytes for the before window, after window, and summary. It decompresses each blob, parses the JSON into the expected model, and packages the result into a `CompactionRecord`. If any piece cannot be decoded or validated, it raises `TranscriptDecodeError`.

**Call relations**: `read_compaction_record` calls this after it has fetched the three blobs from storage. This function is the point where raw stored bytes become a usable compaction object for debug views, evaluation tools, or other readers.

*Call graph*: called by 1 (read_compaction_record); 2 external calls (__init__, __init__).


##### `read_compaction_record`  (lines 116–127)

```
async def read_compaction_record(blob: BlobStore, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Reads one compaction record for a conversation from the blob store. If that numbered compaction does not exist, it returns `None` instead of treating that as a hard failure.

**Data flow**: It receives a blob store, a conversation ID, and a compaction index. It builds the three storage keys, fetches the three blobs, and decodes them into a `CompactionRecord`. If any required blob is missing, it returns `None` to mean there is no record at that index.

**Call relations**: This function is the shared per-record reader. `read_compaction_records` calls it repeatedly, starting at index 1, to collect every saved compaction until the first missing one.

*Call graph*: calls 3 internal fn (get, compaction_key, decode_compaction); called by 1 (read_compaction_records).


##### `read_compaction_records`  (lines 130–140)

```
async def read_compaction_records(blob: BlobStore, conversation_id: UUID) -> tuple[CompactionRecord, ...]
```

**Purpose**: Reads all compaction records for a conversation in order, from oldest to newest. It stops when it reaches the first missing compaction index.

**Data flow**: It receives a blob store and a conversation ID. It starts at compaction index 1, asks `read_compaction_record` for that record, appends each found record to a list, and moves to the next index. When a record is missing, it returns the collected records as an immutable tuple.

**Call relations**: This is the higher-level reader used when a caller wants the full compaction history. It relies on `read_compaction_record` for each individual fetch and uses the system’s convention that compaction indices are written sequentially.

*Call graph*: calls 1 internal fn (read_compaction_record).


### `core/src/ufo/loop/transcript.py`

`io_transport` · `turn completion and repair republishing`

A conversation transcript is the durable record of what has happened so far. This file wraps a shared blob store, which is a simple storage place for named chunks of bytes, and gives the rest of the system a small, careful interface for one conversation. Its main job is to protect the transcript from going backwards. Each saved conversation has a sequence number, called `seq`, that increases as the conversation advances. Before writing, this code reads the current stored transcript. If the stored transcript already has the same or a higher sequence number, the new write is ignored. In everyday terms, it is like a clerk refusing to replace page 10 of a logbook with another copy of page 9. This matters because more than one part of the system may try to publish the final state for a turn, especially during repair or retry flows. The first valid write for a sequence number is treated as authoritative, and stale writes cannot erase it. The file relies on shared transcript helpers to turn conversation objects into bytes and back, and to choose the storage key for a specific conversation ID.

#### Function details

##### `Transcript.read`  (lines 17–22)

```
async def read(self) -> Conversation | None
```

**Purpose**: Reads the saved transcript for this conversation, if one exists. It gives callers either a decoded `Conversation` object or `None` when nothing has been saved yet.

**Data flow**: It starts with the `conversation_id` stored in the `Transcript` object. It turns that ID into the blob-store key, asks the blob store for the saved bytes, and if the blob is missing it returns `None`. If bytes are found, it decodes them into a `Conversation` object and returns that.

**Call relations**: This is the lookup step used before deciding whether a write is safe. `Transcript.write` calls it first so it can compare the existing sequence number with the new one. It also uses the shared transcript helpers to build the storage key and decode the stored bytes.

*Call graph*: called by 1 (write); 2 external calls (decode, transcript_key).


##### `Transcript.write`  (lines 24–28)

```
async def write(self, conversation: Conversation) -> None
```

**Purpose**: Writes a conversation transcript only if it is newer than what is already stored. This prevents stale or repeated work from overwriting the current durable transcript.

**Data flow**: It receives a `Conversation` object to save. First it reads the currently stored conversation, if any. If the current saved version exists and its `seq` is greater than or equal to the incoming `seq`, it stops without changing storage. Otherwise, it encodes the incoming conversation into bytes and stores those bytes under this conversation’s transcript key.

**Call relations**: This is the protective publishing step used when a run finishes a turn, or when a repair flow republishes a committed final state. It calls `Transcript.read` to inspect the existing record, then hands the new conversation to the shared encoder and writes it to the blob store only when the sequence check says it is safe.

*Call graph*: calls 1 internal fn (read); 2 external calls (encode, transcript_key).


### Workspace object framework
The central workspace object layer validates names, schemas, permissions, and dispatches object operations to the owning kind.

### `core/src/ufo/objects.py`

`domain_logic` · `startup registration and tool request handling`

A workspace object is a durable named item, written as one YAML document with three parts: what kind it is, what its name is, and its spec, meaning the user-authored settings for that object. This file makes those objects work in a consistent way across core code and extensions. Without it, every extension would need its own rules for names, validation, permissions, and tool behavior, which would make objects harder to trust and easier to misuse.

The file has three main jobs. First, it defines the shared shape of object kinds and stores. A store is the kind-specific code that actually reads, writes, and deletes rows in its own storage. Second, it validates registered object kinds at startup. It rejects duplicate kind names, unsafe schema choices, secret-bearing fields, and models that cannot be represented as JSON, because specs are shown back to users and may appear in transcripts. Third, it exposes five tool verbs through ObjectVerbs: list, get, explain, apply, and delete.

It also includes MemberOwnedObjects, a reusable permission gate for objects owned by members. Think of it like a front desk: before the storage room is opened, it checks whether the requester is allowed to see or change the item. Subclasses provide the actual data and mutations; this base class enforces visibility and ownership consistently.

#### Function details

##### `ObjectStore.list`  (lines 92–92)

```
async def list(self, ctx: ToolContext, query: str, cursor: str) -> ObjectPage
```

**Purpose**: This is the required listing operation for any object kind's storage code. A kind implements it to return a page of visible object names and short summaries.

**Data flow**: It receives the current tool context, a search query, and a cursor for paging. The implementing store uses those inputs to find matching rows and returns an ObjectPage containing short listing rows and possibly a cursor for the next page.

**Call relations**: ObjectVerbs._list calls this through the registered kind's store after it has resolved which kind is being listed. Implementations may also be supplied indirectly by MemberOwnedObjects.list for member-owned kinds.


##### `ObjectStore.get`  (lines 94–94)

```
async def get(self, ctx: ToolContext, name: str) -> SpecT | None
```

**Purpose**: This is the required read operation for one object. A kind implements it to return the stored spec for a named object, or nothing if that object is not available.

**Data flow**: It receives the tool context and an object name. The store looks up that name in its own storage and returns the validated spec model, or None if no readable object exists.

**Call relations**: ObjectVerbs._get, ObjectVerbs._apply, and ObjectVerbs._delete use this before reading, updating, or deleting. For member-owned kinds, MemberOwnedObjects.get can provide the permission-aware version.


##### `ObjectStore.status`  (lines 96–96)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: This is the optional live-status read for an object. A kind implements it to show current state that is not part of the authored spec, such as last sync time or next scheduled run.

**Data flow**: It receives the current context and object name. The store gathers live state for that object and returns a plain JSON-like dictionary, or None if there is no status to show.

**Call relations**: ObjectVerbs._get asks for this after it has successfully read the spec, so the user can see both the desired settings and the current state together.


##### `ObjectStore.apply`  (lines 98–98)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None) -> None
```

**Purpose**: This is the required create-or-update operation for an object kind. A kind implements it to perform the real mutation after core code has already validated the YAML envelope, object name, and spec shape.

**Data flow**: It receives the context, object name, newly validated spec, and the old spec if one already existed. The store writes or updates its own backing data and returns no value; errors explain why the change is refused.

**Call relations**: ObjectVerbs._apply calls this after parsing and validation. MemberOwnedObjects.apply can sit in front of a kind's custom mutation code to enforce ownership before _apply_owned runs.


##### `ObjectStore.delete`  (lines 100–100)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: This is the required delete operation for an object kind. A kind implements it to remove the named object from its own storage when deletion is allowed.

**Data flow**: It receives the context and object name. The store removes the stored row or raises an error if deletion is not supported or not allowed, and it returns no value on success.

**Call relations**: ObjectVerbs._delete calls this after confirming the object exists. MemberOwnedObjects.delete can provide the common visibility and ownership checks before a subclass's _delete_owned performs the actual deletion.


##### `MemberOwnedObjects.list`  (lines 141–158)

```
async def list(self, ctx: ToolContext, query: str, cursor: str) -> ObjectPage
```

**Purpose**: This lists only the member-owned objects the current actor is allowed to see. It applies sharing and ownership rules before returning names and short summaries.

**Data flow**: It starts with the current context, query text, and paging cursor. It asks the subclass for all owned rows, filters out rows that are invisible to the actor, filters by query, sorts by name, slices one page, and returns an ObjectPage with public listing rows.

**Call relations**: This is the list implementation a member-owned object kind can inherit. It uses _owned_rows for raw data and _visible for permission checks, then hands a safe page back to ObjectVerbs._list through the ObjectStore interface.

*Call graph*: calls 3 internal fn (_owned_rows, _visible, speaker_is_owner); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects.get`  (lines 160–166)

```
async def get(self, ctx: ToolContext, name: str) -> SpecT | None
```

**Purpose**: This reads a member-owned object's spec only if the actor is allowed to see it. Invisible objects are treated the same as missing objects.

**Data flow**: It receives the context and name. It finds the object's owner, checks whether the current actor can see that owner’s row, and either returns None or asks the subclass for the actual spec.

**Call relations**: ObjectVerbs._get can call this through a kind's store. The method relies on _owner and _visible before handing off to _spec, so subclasses do not have to repeat the visibility rule.

*Call graph*: calls 4 internal fn (_owner, _spec, _visible, speaker_is_owner).


##### `MemberOwnedObjects.status`  (lines 168–174)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: This reads live status for a member-owned object only when the actor can see the object. It prevents hidden objects from leaking status information.

**Data flow**: It receives the context and object name. It looks up ownership, checks visibility, and either returns None or asks the subclass for the status dictionary.

**Call relations**: ObjectVerbs._get may call this after reading a spec. It uses the same _owner and _visible gate as MemberOwnedObjects.get, then delegates the kind-specific status work to _status.

*Call graph*: calls 4 internal fn (_owner, _status, _visible, speaker_is_owner).


##### `MemberOwnedObjects.apply`  (lines 176–186)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None) -> None
```

**Purpose**: This creates or updates a member-owned object while enforcing who may change it. It allows subclasses to focus on the domain-specific write while this method handles the access rules.

**Data flow**: It receives the context, name, new spec, and previous spec. It checks whether an existing row is visible; if not, it raises a not-found style error. If the row is visible but not owned by the actor and the actor is not the workspace owner, it refuses with an owner-required error. It may also require a live speaker for sensitive changes. If all checks pass, it passes the work to _apply_owned.

**Call relations**: ObjectVerbs._apply reaches this through the store interface for member-owned kinds. The method uses _owner, _visible, and _owned as the gate, then hands the actual create or update to the subclass's _apply_owned.

*Call graph*: calls 5 internal fn (_apply_owned, _owned, _owner, _visible, speaker_is_owner); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects.delete`  (lines 188–199)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: This deletes a member-owned object only when the requester has permission. It hides invisible objects by reporting them as not found and blocks visible-but-not-owned rows unless the speaker is the workspace owner.

**Data flow**: It receives the context and name. It looks up the owner, checks existence, visibility, ownership, and any live-speaker requirement. On success, it calls the subclass deletion hook; on failure, it raises a clear error.

**Call relations**: ObjectVerbs._delete reaches this through the store interface. The method performs the shared permission story with _owner, _visible, and _owned, then delegates the actual removal to _delete_owned.

*Call graph*: calls 5 internal fn (_delete_owned, _owned, _owner, _visible, speaker_is_owner); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects._owned`  (lines 201–205)

```
def _owned(self, owner: ObjectOwner, acting: UUID | None) -> bool
```

**Purpose**: This answers whether the current acting member is the member-owner of a row. Owner-only rows, which have no member id, are deliberately not considered owned by any member.

**Data flow**: It receives an ObjectOwner and the acting member id. It compares the row's member id to the acting member id and returns true only when both exist and match.

**Call relations**: MemberOwnedObjects._visible uses this to decide what can be seen. MemberOwnedObjects.apply and MemberOwnedObjects.delete use it to decide whether a non-workspace-owner may change a row.

*Call graph*: called by 3 (_visible, apply, delete).


##### `MemberOwnedObjects._visible`  (lines 207–208)

```
def _visible(self, owner: ObjectOwner, acting: UUID | None, is_owner: bool) -> bool
```

**Purpose**: This decides whether a row is visible to the current actor. A row is visible if it is shared, owned by the acting member, or the speaker is the workspace owner.

**Data flow**: It receives the row owner, the acting member id, and whether the speaker is the workspace owner. It combines those facts into one true-or-false visibility answer.

**Call relations**: All read and mutation paths in MemberOwnedObjects use this as the common visibility test. It calls _owned for the member-owner part of that decision.

*Call graph*: calls 1 internal fn (_owned); called by 5 (apply, delete, get, list, status).


##### `MemberOwnedObjects._owner`  (lines 210–211)

```
async def _owner(self, ctx: ToolContext, name: str) -> ObjectOwner | None
```

**Purpose**: This finds the ownership record for one named object. It is a small lookup helper used before visibility and mutation decisions.

**Data flow**: It receives the context and object name. It asks the subclass for all owned rows, searches for the matching name, and returns that row's ObjectOwner or None if no such row exists.

**Call relations**: MemberOwnedObjects.get, status, apply, and delete call this before deciding whether the object is visible or changeable. It depends on _owned_rows, which subclasses must implement.

*Call graph*: calls 1 internal fn (_owned_rows); called by 4 (apply, delete, get, status).


##### `MemberOwnedObjects._owned_rows`  (lines 213–214)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow, ...]
```

**Purpose**: This is a required subclass hook that supplies the raw list of member-owned rows. The base class cannot know where each kind stores its data, so subclasses provide it.

**Data flow**: It receives the context and should return all rows with names, summaries, and owners. In this base class it raises NotImplementedError, meaning a subclass must replace it.

**Call relations**: MemberOwnedObjects.list uses this to build visible listings, and _owner uses it to find the owner for one name. It is the data source for the shared permission gate.

*Call graph*: called by 2 (_owner, list).


##### `MemberOwnedObjects._spec`  (lines 216–217)

```
async def _spec(self, ctx: ToolContext, name: str) -> SpecT | None
```

**Purpose**: This is a required subclass hook that fetches the actual spec for a named object. The base class calls it only after visibility has already been checked.

**Data flow**: It receives the context and object name. A subclass should read and return the stored spec model, or None if the spec is unavailable; the base version only signals that it must be implemented.

**Call relations**: MemberOwnedObjects.get calls this after _owner and _visible say the actor may read the object. This keeps permission logic in the base class and storage details in the subclass.

*Call graph*: called by 1 (get).


##### `MemberOwnedObjects._status`  (lines 219–220)

```
async def _status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: This is a required subclass hook that fetches live status for a named object. It lets each object kind decide what current state is useful to show.

**Data flow**: It receives the context and object name. A subclass should return a JSON-like status dictionary or None; the base implementation raises NotImplementedError.

**Call relations**: MemberOwnedObjects.status calls this only after the shared visibility check has passed. The hook supplies the kind-specific status while the base class supplies the access control.

*Call graph*: called by 1 (status).


##### `MemberOwnedObjects._apply_owned`  (lines 222–230)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, owner: ObjectOwner | None) -> None
```

**Purpose**: This is a required subclass hook that performs the real create or update for a member-owned object. It runs after the base class has checked visibility and ownership.

**Data flow**: It receives the context, name, new spec, old spec, and the existing owner if any. A subclass writes the change to its storage and returns no value; the base version raises NotImplementedError.

**Call relations**: MemberOwnedObjects.apply calls this after all shared mutation gates pass. This separation keeps authorization in one place and domain-specific writing in the subclass.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._delete_owned`  (lines 232–233)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: This is a required subclass hook that performs the real deletion for a member-owned object. It is called only after the base class confirms deletion is allowed.

**Data flow**: It receives the context, object name, and owner record. A subclass removes the row from its storage and returns no value; the base implementation requires subclasses to provide it.

**Call relations**: MemberOwnedObjects.delete calls this at the end of the permission flow. The base class decides whether deletion may happen; the subclass decides how deletion is done.

*Call graph*: called by 1 (delete).


##### `object_registry`  (lines 261–278)

```
def object_registry(bound: tuple[BoundKind, ...]) -> dict[str, BoundKind]
```

**Purpose**: This builds the lookup table of all registered object kinds for one deployment. It also acts as a startup safety check so bad or conflicting object kinds fail before the system serves requests.

**Data flow**: It receives bound object kinds from core and extensions. For each one, it checks the kind name format, rejects duplicate names, validates the spec model, and stores the kind under its name. It returns a dictionary from kind name to BoundKind.

**Call relations**: Startup wiring calls this before ObjectVerbs can dispatch tools. It calls _validate_spec_model for the deeper schema checks, then ObjectVerbs later uses the resulting registry to resolve tool requests.

*Call graph*: calls 1 internal fn (_validate_spec_model).


##### `_validate_spec_model`  (lines 281–301)

```
def _validate_spec_model(owner: str, kind: ObjectKind) -> None
```

**Purpose**: This checks that an object kind's spec model is safe to store and show back to users. It prevents loose fields, secret fields, and types that cannot be represented as JSON.

**Data flow**: It receives the owner label and ObjectKind. It walks the main model and any nested models, checks that unexpected keys are forbidden, scans field annotations for secret types, and asks Pydantic to produce a JSON schema. It returns nothing on success or raises a startup error on failure.

**Call relations**: object_registry calls this for every registered kind. It uses _reachable_models to find nested Pydantic models and _annotation_types to inspect complex type annotations.

*Call graph*: calls 2 internal fn (_annotation_types, _reachable_models); called by 1 (object_registry).


##### `_reachable_models`  (lines 304–318)

```
def _reachable_models(model: type[BaseModel]) -> tuple[type[BaseModel], ...]
```

**Purpose**: This finds the Pydantic models nested inside a spec model. It is used so validation rules apply not just to the top-level spec but also to embedded objects.

**Data flow**: It receives a Pydantic model class. It walks through its fields, follows annotations that are also Pydantic models, avoids revisiting models it has already seen, and returns all discovered model classes.

**Call relations**: _validate_spec_model calls this before checking configuration and secret fields. It uses _annotation_types to unpack annotations such as lists or unions that may hide nested model types.

*Call graph*: calls 1 internal fn (_annotation_types); called by 1 (_validate_spec_model).


##### `_annotation_types`  (lines 321–328)

```
def _annotation_types(annotation: object) -> tuple[object, ...]
```

**Purpose**: This flattens a type annotation into the concrete pieces inside it. For example, it lets the code inspect the item type inside a list or the choices inside a union.

**Data flow**: It receives an annotation object. It asks Python's typing system for inner arguments; if there are none, it returns the annotation itself. If there are inner arguments, it recursively flattens them into one tuple.

**Call relations**: _validate_spec_model uses this to spot secret-bearing fields, and _reachable_models uses it to find nested Pydantic models. It is a small helper for schema inspection.

*Call graph*: called by 2 (_reachable_models, _validate_spec_model); 1 external calls (get_args).


##### `ObjectVerbs.tools`  (lines 363–422)

```
def tools(self) -> tuple[ToolDef, ...]
```

**Purpose**: This declares the five public object tools: list, get, explain, apply, and delete. Each declaration includes the user-facing description, input shape, and the method that will run.

**Data flow**: It uses the ObjectVerbs instance's handler methods and input models to create ToolDef objects. The result is a tuple of tool definitions that the tool registry can expose to the model or caller.

**Call relations**: Tool setup calls this to publish the object API. Later, when a tool is invoked, the registered ToolDef routes the request to _list, _get, _explain, _apply, or _delete.

*Call graph*: 1 external calls (__init__).


##### `ObjectVerbs._list`  (lines 424–438)

```
async def _list(self, ctx: ToolContext, args: ObjectListInput) -> ToolResult
```

**Purpose**: This implements the object_list tool. It either lists all registered object kinds or lists objects of one chosen kind.

**Data flow**: It receives the tool context and list arguments. If no kind is provided, it turns the registry into a JSON list of kind names and descriptions. If a kind is provided, it resolves that kind, binds the context to the owning extension, asks the store for a page, and returns object names, summaries, and possibly a next cursor.

**Call relations**: The object_list ToolDef routes here. It uses _resolve when a specific kind is requested, _bound_ctx so the store runs under the right extension context, and _json_result to format the response.

*Call graph*: calls 3 internal fn (_bound_ctx, _resolve, _json_result).


##### `ObjectVerbs._get`  (lines 440–454)

```
async def _get(self, ctx: ToolContext, args: ObjectGetInput) -> ToolResult
```

**Purpose**: This implements the object_get tool. It returns one object's saved spec and, when available, its live status.

**Data flow**: It receives the context plus kind and name. It resolves the kind, binds the context, asks the store for the spec, raises a not-found error if absent, asks for status, then renders the result as YAML text.

**Call relations**: The object_get ToolDef routes here. It depends on _resolve and _bound_ctx before calling the kind's store, and it packages the answer directly as a ToolResult rather than using the JSON helper because the output is YAML.

*Call graph*: calls 2 internal fn (_bound_ctx, _resolve); 4 external calls (__init__, __init__, __init__, safe_dump).


##### `ObjectVerbs._explain`  (lines 456–468)

```
async def _explain(self, ctx: ToolContext, args: ObjectExplainInput) -> ToolResult
```

**Purpose**: This implements the object_explain tool. It tells a user how to author objects of a given kind before they write a manifest.

**Data flow**: It receives the context and kind name. It resolves the kind and returns its description, guidance, naming rule, and generated JSON schema for the spec.

**Call relations**: The object_explain ToolDef routes here. It uses _resolve to find the kind and _json_result to send back the explanation in a machine-readable form.

*Call graph*: calls 2 internal fn (_resolve, _json_result).


##### `ObjectVerbs._apply`  (lines 470–489)

```
async def _apply(self, ctx: ToolContext, args: ObjectApplyInput) -> ToolResult
```

**Purpose**: This implements the object_apply tool, which creates or updates an object from a YAML manifest. It is the main validation path before a kind-specific store is allowed to mutate data.

**Data flow**: It receives the context and manifest text. It parses the YAML envelope, resolves the kind, validates the object name, validates the spec against the kind's model, binds the context to the owning extension, reads any old spec, and calls the store's apply method. It returns whether the result was created or updated.

**Call relations**: The object_apply ToolDef routes here. The method chains together _parse_envelope, _resolve, _validate_name, model validation, _bound_ctx, and the store's apply operation, then formats success through _json_result.

*Call graph*: calls 5 internal fn (_bound_ctx, _resolve, _json_result, _parse_envelope, _validate_name); 1 external calls (__init__).


##### `ObjectVerbs._delete`  (lines 491–505)

```
async def _delete(self, ctx: ToolContext, args: ObjectDeleteInput) -> ToolResult
```

**Purpose**: This implements the object_delete tool. It deletes one object and echoes the deleted spec so the user has enough information to recreate it if deletion was accidental.

**Data flow**: It receives the context plus kind and name. It resolves the kind, binds the context, reads the existing spec, raises a not-found error if absent, asks the store to delete the object, and returns a JSON result containing the deleted spec.

**Call relations**: The object_delete ToolDef routes here. It uses _resolve and _bound_ctx before calling the store, then uses _json_result to format the deletion confirmation.

*Call graph*: calls 3 internal fn (_bound_ctx, _resolve, _json_result); 1 external calls (__init__).


##### `ObjectVerbs._resolve`  (lines 507–512)

```
def _resolve(self, kind: str) -> BoundKind
```

**Purpose**: This looks up an object kind by name in the registry. It turns an unknown kind into a helpful error that names the registered kinds.

**Data flow**: It receives a kind string. It checks the registry mapping and returns the matching BoundKind, or raises UnknownKind with a list of available choices.

**Call relations**: The list, get, explain, apply, and delete handlers all call this before touching a store. It is the shared doorway from a user-provided kind name to the registered object implementation.

*Call graph*: called by 5 (_apply, _delete, _explain, _get, _list); 1 external calls (__init__).


##### `ObjectVerbs._bound_ctx`  (lines 514–515)

```
def _bound_ctx(self, ctx: ToolContext, bound: BoundKind) -> ToolContext
```

**Purpose**: This adjusts the tool context so a kind's store runs with the extension context that owns that kind. That matters because extension stores need their own workspace-scoped resources.

**Data flow**: It receives the current ToolContext and the resolved BoundKind. It returns a copy of the context with its extension context replaced by the bound kind's context.

**Call relations**: The list, get, apply, and delete handlers call this after _resolve and before invoking store methods. It is the adapter that lets one shared tool implementation dispatch safely into extension-owned storage.

*Call graph*: called by 4 (_apply, _delete, _get, _list); 1 external calls (replace).


##### `_parse_envelope`  (lines 518–536)

```
def _parse_envelope(manifest: str) -> tuple[str, str, Mapping[str, object]]
```

**Purpose**: This parses and checks the outer YAML document used by object_apply. It enforces the simple three-key shape: kind, name, and spec.

**Data flow**: It receives manifest text. It rejects overly large input, invalid YAML, non-mapping YAML, missing or extra top-level keys, non-string kind or name values, and non-mapping specs. On success it returns the kind string, name string, and spec mapping.

**Call relations**: ObjectVerbs._apply calls this first, before resolving the kind or validating the spec. Its errors stop malformed manifests before they reach any object store.

*Call graph*: called by 1 (_apply); 2 external calls (__init__, safe_load).


##### `_validate_name`  (lines 539–544)

```
def _validate_name(name: str) -> None
```

**Purpose**: This enforces the shared naming rule for all object instances. Names must be short, lowercase, and hyphen-friendly so they are predictable and safe to address.

**Data flow**: It receives a name string. It checks length and the allowed pattern, returning nothing if valid or raising InvalidName with the rule if invalid.

**Call relations**: ObjectVerbs._apply calls this after parsing the manifest and resolving the kind. It ensures every kind uses the same object-name grammar.

*Call graph*: called by 1 (_apply); 1 external calls (__init__).


##### `_json_result`  (lines 547–548)

```
def _json_result(payload: Mapping[str, object]) -> ToolResult
```

**Purpose**: This wraps a plain mapping as a JSON tool response. It keeps the common response formatting for most object tools in one small helper.

**Data flow**: It receives a payload mapping. It converts the payload to a JSON string, wraps that string in TextContent, then wraps it in a ToolResult.

**Call relations**: ObjectVerbs._list, _explain, _apply, and _delete use this to return structured results. ObjectVerbs._get is the main exception because it returns YAML for readability.

*Call graph*: called by 4 (_apply, _delete, _explain, _list); 3 external calls (__init__, __init__, dumps).


### Core workspace objects
Built-in workspace object kinds expose the workspace agent and shared conversation artifacts through the common object interface.

### `core/src/ufo/agents.py`

`domain_logic` · `request handling`

This file makes the workspace's agent look like a normal object that tools can list, inspect, and update. The important rule is separation of responsibility: the agent's model can be changed here, but its system prompt cannot. Prompt changes go through a separate governance proposal path, so prompt edits can be reviewed and checked safely.

Think of the agent as a shared office computer. This file lets the owner swap which engine the computer runs on, but it does not let anyone rewrite the company policy posted above the desk. That policy, the prompt, is only shown here along with a digest, which is like a fingerprint used to prove exactly which prompt a proposal refers to.

There is only one agent per workspace. It is created when the workspace is initialized, so this object kind refuses creation and deletion. Updates are also guarded: if the current speaker is not the workspace owner, changing the model is rejected. When a model change succeeds, it updates the database row for the current workspace. The change does not interrupt a running turn; the next turn reads the fresh agent row and uses the new model.

#### Function details

##### `AgentObjects.list`  (lines 48–64)

```
async def list(self, ctx: ToolContext, query: str, cursor: str) -> ObjectPage
```

**Purpose**: Shows the agent objects available in the current workspace. Since there is normally only one agent, this is mostly a searchable listing that says what model the workspace agent currently uses.

**Data flow**: It receives a tool context, a search string, and a cursor used for paging. It reads agent names and models from the database for the current workspace, keeps only rows whose name or model contains the search text, skips rows before the cursor when one is supplied, and returns a page of object rows with short human-readable summaries.

**Call relations**: This is used when the object system needs to browse `agent` objects. It opens a workspace database transaction, asks for the current workspace identity, builds the query, then packages the matching database rows into the standard object-page shape expected by the rest of the object tooling.

*Call graph*: 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


##### `AgentObjects.get`  (lines 66–68)

```
async def get(self, ctx: ToolContext, name: str) -> AgentSpec | None
```

**Purpose**: Fetches the editable specification for one agent object. In this file, the editable specification is only the model name, not the prompt.

**Data flow**: It receives a tool context and an agent name. It asks `_row` to find the matching database row; if there is no row, it returns nothing. If the row exists, it returns an `AgentSpec` containing the model from that row.

**Call relations**: This is the read half used before showing or applying an object spec. It relies on `_row` for the actual database lookup, then converts the stored row into the narrow public spec that callers are allowed to edit.

*Call graph*: calls 1 internal fn (_row); 1 external calls (__init__).


##### `AgentObjects.status`  (lines 70–78)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: Returns read-only status details for an agent, especially the current prompt and a digest of that prompt. This lets callers see the prompt without making prompt editing part of the agent spec.

**Data flow**: It receives a tool context and an agent name. It looks up the database row with `_row`; if nothing is found, it returns nothing. Otherwise it returns a dictionary containing the prompt text, the prompt's digest, and the last update time as text.

**Call relations**: This supports inspection of the agent beyond the editable model field. It calls `_row` to read the stored prompt and update time, then calls `prompt_digest` to compute the fingerprint used by the governance proposal flow.

*Call graph*: calls 1 internal fn (_row); 1 external calls (prompt_digest).


##### `AgentObjects.apply`  (lines 80–95)

```
async def apply(self, ctx: ToolContext, name: str, spec: AgentSpec, old: AgentSpec | None) -> None
```

**Purpose**: Changes the agent's model, but only for an existing agent and only when the speaker is the workspace owner. It refuses attempts to create a new agent through this path.

**Data flow**: It receives the tool context, the agent name, the requested new spec, and the old spec if one existed. If there was no old spec, it raises an error because agents cannot be created here. It then checks whether the speaker is the owner; if not, it raises an owner-required error. If allowed, it updates the current workspace's agent row in the database with the new model and a fresh update time.

**Call relations**: This is the write path for changing the model. The object system calls it when someone applies an `agent` object spec. It checks creation rules first, asks the tool context about ownership, and then writes the update through the workspace transaction so the next agent turn can read the new model.

*Call graph*: calls 1 internal fn (speaker_is_owner); 5 external calls (__init__, __init__, update, workspace_tx, ws_current).


##### `AgentObjects.delete`  (lines 97–98)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Always refuses deletion of the workspace agent. The project currently assumes every workspace has exactly one agent.

**Data flow**: It receives the tool context and the agent name, but does not read or change any stored data. It immediately raises a not-supported error explaining that the agent cannot be deleted.

**Call relations**: The object system calls this when someone tries to delete an `agent` object. Instead of handing off to the database, it stops the flow at once because deleting the single workspace agent would break the workspace's expected shape.

*Call graph*: 1 external calls (__init__).


##### `AgentObjects._row`  (lines 100–113)

```
async def _row(self, name: str) -> sa.Row | None
```

**Purpose**: Looks up the stored database row for a named agent in the current workspace. It is a small shared helper so `get` and `status` read the same source of truth.

**Data flow**: It receives an agent name. It opens a workspace database transaction, uses the current workspace id and the given name to search the agent table, and returns the row containing the prompt, model, and update time. If there is no matching row, it returns nothing.

**Call relations**: `get` calls this when it needs the editable model value, and `status` calls it when it needs the prompt and timestamp. By keeping the lookup here, both paths use the same workspace scoping and avoid accidentally reading another workspace's agent.

*Call graph*: called by 2 (get, status); 3 external calls (select, workspace_tx, ws_current).


### `core/src/ufo/artifacts.py`

`domain_logic` · `request handling`

An artifact is a file produced during a conversation and shared with `share_file`. This file is the read-and-delete side of that feature. Without it, shared files would still exist in storage, but users and tools would not have a clean way to find them again, reuse them in the workspace, or remove them.

The file treats each artifact as the combination of a conversation and a filename. If the same conversation shares `report.txt` more than once, those shares become versions of one artifact, and the newest one is what users see. If a different conversation shares a file with the same name, it is a separate artifact. To make that visible, artifact names start with a short piece of the conversation ID and then a cleaned-up filename.

The main class, `ArtifactObjects`, plugs this behavior into the project’s object system. It can list artifacts, return the latest file details, build a status report, copy the latest bytes back into the sandbox workspace, and delete all stored versions. It refuses create and update requests because artifacts are not edited directly; they are born only when a file is shared. A useful detail: large files over about 32 MiB are not copied back into the workspace, but they can still be fetched through a temporary download link when token signing is configured.

#### Function details

##### `artifact_object_names`  (lines 54–74)

```
def artifact_object_names(shares: Iterable[tuple[UUID, str]]) -> dict[tuple[UUID, str], str]
```

**Purpose**: Builds the public object name for each shared-file identity. It keeps files from different conversations separate, while making files from the same conversation naturally group together in lists.

**Data flow**: It receives pairs of conversation ID and filename. It turns each filename into a safe short slug, prefixes it with the first part of the conversation ID, checks whether any names accidentally collide, and adds a short digest only for the colliding cases. It returns a lookup table from each original pair to its final object name.

**Call relations**: When `ArtifactObjects._groups` has loaded the stored artifact rows and grouped them by conversation and filename, it asks this function to assign stable names to those groups. This function relies on `_slug` for readable filename cleanup and `_identity_digest` only when two different identities would otherwise get the same name.

*Call graph*: calls 2 internal fn (_identity_digest, _slug); called by 1 (_groups); 1 external calls (Counter).


##### `_slug`  (lines 77–79)

```
def _slug(filename: str) -> str
```

**Purpose**: Turns a filename into a short, URL-like name fragment that is safe and readable. This prevents odd characters, spaces, or very long names from becoming awkward object names.

**Data flow**: It receives a filename, lowercases it, replaces runs of non-letter and non-number characters with dashes, trims extra dashes, and cuts it to the allowed length. If nothing usable remains, it returns the fallback word `artifact`.

**Call relations**: `artifact_object_names` calls this whenever it needs the filename part of an artifact object name. It is a small helper that keeps naming rules in one place.

*Call graph*: called by 1 (artifact_object_names).


##### `_identity_digest`  (lines 82–84)

```
def _identity_digest(identity: tuple[UUID, str]) -> str
```

**Purpose**: Creates a short source for a collision-breaking suffix. It is used when two different artifact identities would otherwise receive the same object name.

**Data flow**: It receives a conversation ID and filename, combines them into one string, and runs that string through SHA-256, a standard one-way hashing method. It returns the hexadecimal digest, from which callers use only a short prefix.

**Call relations**: `artifact_object_names` calls this only after it discovers that two generated names collide. The digest lets the final names stay stable and distinct without making every artifact name long.

*Call graph*: called by 1 (artifact_object_names); 1 external calls (sha256).


##### `ArtifactObjects.list`  (lines 102–114)

```
async def list(self, ctx: ToolContext, query: str, cursor: str) -> ObjectPage
```

**Purpose**: Returns a page of artifact objects that match a search query. This is what lets a user or tool browse shared files in the workspace.

**Data flow**: It reads all artifact groups through `_groups`, filters them by the query text against the object name, latest filename, or latest caption, then applies the cursor so results can be paged. It turns the selected groups into object rows with short summaries and returns an object page with a next cursor when more results remain.

**Call relations**: The object system calls this when someone lists artifacts. It depends on `_groups` for the current artifact set and `_summary` to make each row understandable in a compact listing.

*Call graph*: calls 2 internal fn (_groups, _summary); 2 external calls (__init__, __init__).


##### `ArtifactObjects.get`  (lines 116–123)

```
async def get(self, ctx: ToolContext, name: str) -> ArtifactSpec | None
```

**Purpose**: Returns the editable-looking specification for one artifact, which describes the latest shared version. In practice this is read-only information, because artifacts cannot be created or updated through this object interface.

**Data flow**: It receives an object name, looks up the matching group with `_find`, and returns nothing if no artifact has that name. If found, it takes the newest share and returns its filename, media type, and caption as an `ArtifactSpec`.

**Call relations**: The object system calls this when someone asks for a single artifact. It delegates name lookup to `_find`; unlike `status`, it only describes the artifact and does not copy file bytes into the workspace.

*Call graph*: calls 1 internal fn (_find); 1 external calls (__init__).


##### `ArtifactObjects.status`  (lines 125–145)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: Builds a detailed status report for one artifact and, when possible, makes the latest file available again inside the workspace. This is the path used when a later turn wants to reuse a file made earlier.

**Data flow**: It receives the tool context and object name, finds the artifact group, and returns nothing if it does not exist. For the latest version, it records size, share time, turn ID, conversation ID, and version count; if token signing is configured, it also creates a temporary download URL. Finally, it asks `_materialize` to copy the file bytes into the sandbox unless the file is too large, and includes that workspace path or null.

**Call relations**: The object system calls this as part of getting an artifact’s practical status. It uses `_find` to locate the latest share, calls `mint_artifact_token` to create a temporary link when allowed, and hands off to `_materialize` for the workspace copy.

*Call graph*: calls 2 internal fn (_find, _materialize); 2 external calls (now, mint_artifact_token).


##### `ArtifactObjects.apply`  (lines 147–150)

```
async def apply(self, ctx: ToolContext, name: str, spec: ArtifactSpec, old: ArtifactSpec | None) -> None
```

**Purpose**: Rejects attempts to create or update artifacts through the object interface. This protects the rule that artifacts are produced only by writing a workspace file and sharing it with `share_file`.

**Data flow**: It receives the requested object name, desired artifact spec, and any old spec, but does not use them to change storage. Instead, it immediately raises a `VerbNotSupported` error with guidance explaining how artifacts should be created.

**Call relations**: If the broader object system tries to apply a create or update operation to this object kind, this method is the guardrail. It stops the flow before any database, blob, or workspace changes can happen.

*Call graph*: 1 external calls (__init__).


##### `ArtifactObjects.delete`  (lines 152–164)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Deletes an artifact and every stored version of it. This removes both the database records and the saved file bytes, so old download links stop working.

**Data flow**: It receives an artifact name, uses `_find` to collect all versions, and raises an error if the name is unknown. It then opens a workspace database transaction, deletes matching `shared_artifact` rows for the current workspace, and afterwards deletes each version’s blob from blob storage.

**Call relations**: The object system calls this when a user requests deletion. It relies on `_find` to translate the object name into stored rows, uses the workspace transaction and current workspace ID to delete only the right records, then cleans up the actual stored bytes through the blob service.

*Call graph*: calls 1 internal fn (_find); 3 external calls (delete, workspace_tx, ws_current).


##### `ArtifactObjects._materialize`  (lines 166–177)

```
async def _materialize(self, ctx: ToolContext, name: str, latest: sa.Row) -> str | None
```

**Purpose**: Copies the latest artifact bytes back into the conversation workspace when the file is small enough. This makes a previously shared file usable again by later tools as a normal workspace file.

**Data flow**: It receives the context, artifact name, and latest database row. If the file is larger than the configured limit, it returns null and copies nothing. Otherwise it reads the blob bytes, raises a clear error if the blob is missing, writes the bytes into `artifacts/<name>/<filename>` in the sandbox, and returns that path.

**Call relations**: `ArtifactObjects.status` calls this while building an artifact status report. It is deliberately not used by `_find`, `get`, or `delete`, so simple lookups and deletion do not unexpectedly write files into the workspace.

*Call graph*: called by 1 (status).


##### `ArtifactObjects._find`  (lines 179–181)

```
async def _find(self, name: str) -> tuple[sa.Row, ...] | None
```

**Purpose**: Finds the stored versions for one artifact object name. It is the shared lookup step used before reading details, reporting status, or deleting.

**Data flow**: It receives an artifact object name, asks `_groups` for all named artifact groups, and searches for an exact name match. It returns the tuple of stored rows for that artifact, newest first, or null if no group matches.

**Call relations**: `get`, `status`, and `delete` all call this before doing their own work. It keeps name resolution consistent by relying on the same grouping and naming rules used by `list`.

*Call graph*: calls 1 internal fn (_groups); called by 3 (delete, get, status).


##### `ArtifactObjects._groups`  (lines 183–216)

```
async def _groups(self) -> Sequence[tuple[str, tuple[sa.Row, ...]]]
```

**Purpose**: Loads all shared artifacts for the current workspace and organizes them into object groups. Each group represents one conversation-and-filename pair, with versions sorted newest first.

**Data flow**: It opens a workspace database transaction, selects shared artifact rows joined with their conversation IDs, and limits the query to the current workspace. It groups rows by conversation ID and filename, asks `artifact_object_names` to assign object names, sorts each group by share time and blob key with newest first, and returns the groups sorted by name.

**Call relations**: `list` calls this to browse all artifacts, and `_find` calls it to resolve one name. It is the central bridge between raw database rows and the object view that the rest of this file presents.

*Call graph*: calls 1 internal fn (artifact_object_names); called by 2 (_find, list); 3 external calls (select, workspace_tx, ws_current).


##### `_summary`  (lines 219–225)

```
def _summary(shares: tuple[sa.Row, ...]) -> str
```

**Purpose**: Creates the short text shown for an artifact in a listing. It gives enough detail to recognize the file without opening it.

**Data flow**: It receives the rows for one artifact group, looks at the newest version, and builds a sentence containing the filename, media type, size, share date, and version count when there is more than one version. It trims the result to the configured maximum length.

**Call relations**: `ArtifactObjects.list` calls this for every artifact row it returns. It turns the grouped database information into a human-readable summary for the object list.

*Call graph*: called by 1 (list).


### Extension workspace objects
Extension-owned object kinds expose connected accounts, synced pages, external sources, and workspace-created skills while preserving workspace boundaries.

### `extensions/connectors/ufo_ext_connectors/objects.py`

`domain_logic` · `request handling for connector object reads, sharing changes, and revocation`

A connected account is not just ordinary data. It comes from a real third-party service and usually involves a secret token, like an OAuth permission grant. This file protects that boundary. It says: accounts can only be created through the special `connect_account` chat flow, not by naming an object by hand. That prevents someone from pretending to create a provider account without going through the proper consent and secret-handling path.

Once an account exists, this file presents it as a workspace object with a stable name, such as a provider name plus an account identifier. If two accounts would end up with the same cleaned-up name, it adds a short fingerprint so the names stay unique. Listing and reading show useful facts such as who granted the account, whether it is shared, and when it was granted.

The only allowed edit is changing `shared`, which decides whether the account is private to the grantor or visible for broader workspace use. Deleting the object revokes the grant, meaning the account stops being available to tools, syncs, and proxy rules. The surrounding object framework enforces who is allowed to do that: the original grantor or a workspace owner.

#### Function details

##### `_slug`  (lines 45–46)

```
def _slug(raw: str) -> str
```

**Purpose**: Turns a provider name or account id into a safe, simple name part. It lowercases the text, replaces runs of non-letter-or-number characters with dashes, and trims extra dashes from the ends.

**Data flow**: It receives raw text such as a provider name or account identifier. It cleans that text into a lowercase slug that fits the object naming style. It returns the cleaned string and changes nothing else.

**Call relations**: ConnectorObjects._named uses this helper when building human-readable object names for connected accounts. It is the small cleanup step before account grants are exposed as named workspace objects.

*Call graph*: called by 1 (_named); 1 external calls (sub).


##### `ConnectorObjects._owned_rows`  (lines 63–74)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow, ...]
```

**Purpose**: Builds the list view for connector objects. Each row says the object name, which provider account it represents, and who owns or shares it.

**Data flow**: It receives the current tool context, which includes the current workspace and turn. It asks _named for the connected accounts in that workspace, then turns each grant into an owned object row with a short summary and ownership information. It returns all rows as a tuple.

**Call relations**: The broader object system calls this when it needs to list connector objects a member can see. It relies on _named to translate grant records into object names, then hands the resulting rows back to the object framework for visibility and presentation.

*Call graph*: calls 1 internal fn (_named); 2 external calls (__init__, __init__).


##### `ConnectorObjects._spec`  (lines 76–82)

```
async def _spec(self, ctx: ToolContext, name: str) -> ConnectorSpec | None
```

**Purpose**: Returns the editable specification for one connector object. In practice, this exposes the provider, account id, and whether the account is shared.

**Data flow**: It receives the current context and an object name. It looks up that name through _named. If no such grant exists, it returns nothing. If it finds one, it creates and returns a ConnectorSpec containing the grant’s provider, account id, and shared flag.

**Call relations**: The object framework calls this when someone reads or applies an object. It uses _named as the source of truth for object names, then returns the small spec model that the rest of the object machinery understands.

*Call graph*: calls 1 internal fn (_named); 1 external calls (__init__).


##### `ConnectorObjects._status`  (lines 84–94)

```
async def _status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: Provides extra read-only status details for a connector object. This is audit-style information, such as who granted it and when.

**Data flow**: It receives the current context and an object name. It looks up the corresponding grant through _named. If the name is unknown, it returns nothing. Otherwise it returns a dictionary with the grantor member id, grant time, host, agent, and sharing state.

**Call relations**: The object system calls this when it wants more than the basic spec. It sits beside _spec: _spec gives the editable shape, while _status gives helpful facts that should not be edited directly.

*Call graph*: calls 1 internal fn (_named).


##### `ConnectorObjects._apply_owned`  (lines 96–113)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectorSpec, old: ConnectorSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Applies the one allowed change to a connected account: switching its `shared` setting on or off. It refuses attempts to create an account or change anything else, because account connection must go through the secure connect flow.

**Data flow**: It receives the current context, the object name, the requested new spec, the old spec if one exists, and ownership information. If there is no old object, or if anything besides `shared` changed, it raises an error explaining that account connection is not supported here. If the shared value did not change, it does nothing. If it did change, it finds the grant and asks the grant store to update the shared flag for that provider account in the workspace.

**Call relations**: The object framework calls this after permission checks decide the speaker may mutate the object. This function then enforces the connector-specific rule: only sharing may change. When a real sharing change is needed, it uses _named to find the grant and hands the update to the grant store.

*Call graph*: calls 1 internal fn (_named); 2 external calls (__init__, model_copy).


##### `ConnectorObjects._delete_owned`  (lines 115–119)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Revokes a connected account by deleting its grant record. This removes the account’s availability for tools and related connector behavior.

**Data flow**: It receives the current context, the object name, and ownership information. It finds the matching grant through _named. If the grant store is unavailable, it raises an error. Otherwise it tells the grant store to revoke the provider/account pair for the current workspace.

**Call relations**: The object framework calls this after permission checks confirm that the grantor or a workspace owner is allowed to delete the connector object. This function translates that object deletion into the real domain action: revoking the underlying grant.

*Call graph*: calls 1 internal fn (_named).


##### `ConnectorObjects._named`  (lines 121–138)

```
async def _named(self, ctx: ToolContext) -> dict[str, GrantSummary]
```

**Purpose**: Builds the map from connector object names to grant summaries. This is the central name-making step that lets raw connected-account grants appear as stable workspace objects.

**Data flow**: It receives the current context and reads grant summaries for the current workspace. It keeps one grant per provider/account pair, builds a cleaned name from the provider and account id, and groups grants that would have the same name. If a name is unique, it uses it directly. If several grants collapse to the same name, it adds a short hash-based suffix so each object name stays distinct. It returns a dictionary from object name to grant summary.

**Call relations**: All the main connector object operations call this first: listing, reading specs, reading status, changing sharing, and deleting. It depends on _slug to make readable name parts, on the grants API to fetch current grants, and on hashing only when needed to avoid name collisions.

*Call graph*: calls 1 internal fn (_slug); called by 5 (_apply_owned, _delete_owned, _owned_rows, _spec, _status); 2 external calls (sha256, grant_summaries).


### `extensions/sources/ufo_ext_sources/pages.py`

`domain_logic` · `request handling`

A source page is a document that came from an external content source, such as a synced provider. This file is the bridge between those stored synced pages and the system’s general object interface. It deliberately does not let people create or edit pages here, because pages are produced by the content-sync driver, not by users typing object changes. Think of it like a library catalogue for imported documents: you can search the card catalogue, read where an item came from, and ask the librarian to remove a record, but you cannot rewrite the book through the catalogue.

The file defines what a page looks like to the outside world: its source provider, who can see it, a digest that identifies the body content, and a reference to where the body is stored. The actual body is never copied into the object response. When listing or fetching pages, the code only shows pages visible to the current audience: shared pages, plus that member’s private pages if the current turn belongs to a member. When deleting, it first checks that the speaker is the workspace owner. If allowed, it asks the extension context to forget the page, which tombstones the row so the existing cleanup pipeline can remove derived index data.

#### Function details

##### `_require_ext`  (lines 49–52)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure the tool request has an extension context attached. The extension context is the object that knows how to read synced sources and forget pages.

**Data flow**: It receives a tool context. If that context contains an extension context, it returns it. If not, it stops the request with a runtime error, because page objects cannot work without access to the source-page storage API.

**Call relations**: PageObjects._pages calls this before reading sources and pages. PageObjects.delete also calls it before asking the extension layer to forget a page.

*Call graph*: called by 2 (_pages, delete).


##### `_audience_subjects`  (lines 55–61)

```
def _audience_subjects(ctx: ToolContext) -> frozenset[str]
```

**Purpose**: This decides which visibility buckets the current caller is allowed to read. It prevents one member from seeing another member’s private synced pages.

**Data flow**: It reads the current audience member id from the tool context. If there is no member, it returns only the shared subject. If there is a member, it returns both the shared subject and that member’s private subject.

**Call relations**: PageObjects._pages calls this when asking the extension context for source pages. When a private member space is needed, this helper uses member_subject to build the correct member-specific subject name.

*Call graph*: called by 1 (_pages); 1 external calls (member_subject).


##### `_Page.name`  (lines 76–77)

```
def name(self) -> str
```

**Purpose**: This gives a page its public object name. The name is simply the page row’s UUID written as text.

**Data flow**: It reads the page’s internal id and converts it to a string. Nothing else is changed.

**Call relations**: The surrounding page-object code uses this name when listing pages, searching by name, building cursors, and finding a requested page.


##### `_Page.spec`  (lines 79–82)

```
def spec(self) -> PageSpec
```

**Purpose**: This turns an internal page record into the public page specification returned by object get. The specification contains metadata only, not the page body.

**Data flow**: It reads the page’s backend name, visibility subject, digest, and body reference. It uses those values to create and return a PageSpec.

**Call relations**: PageObjects.get relies on this conversion after PageObjects._find has located the matching page. The function hands off to PageSpec construction so the result follows the declared schema.

*Call graph*: 1 external calls (__init__).


##### `_Page.summary`  (lines 84–85)

```
def summary(self) -> str
```

**Purpose**: This creates a short human-readable description for a page in list results. It gives enough detail to recognize the page without loading the body.

**Data flow**: It combines the source backend, visibility subject, and digest into one sentence-like string. It then cuts that string to the maximum summary length and returns it.

**Call relations**: PageObjects.list uses this summary when filtering search results and when building each ObjectRow shown to callers.


##### `PageObjects.list`  (lines 96–104)

```
async def list(self, ctx: ToolContext, query: str, cursor: str) -> ObjectPage
```

**Purpose**: This returns a searchable, paginated list of visible synced pages. It is used when someone wants to browse page objects without fetching each one.

**Data flow**: It receives the tool context, a search query, and a cursor. It loads all pages visible to the caller, keeps only pages whose name or summary contains the query, sorts them by name, applies the cursor, and returns one page of ObjectRow entries plus a next cursor if more results remain.

**Call relations**: This is one of the main object-kind operations exposed through PAGE_OBJECT. It calls PageObjects._pages to get the readable page set, then creates ObjectRow values and wraps them in an ObjectPage for the object system.

*Call graph*: calls 1 internal fn (_pages); 2 external calls (__init__, __init__).


##### `PageObjects.get`  (lines 106–108)

```
async def get(self, ctx: ToolContext, name: str) -> PageSpec | None
```

**Purpose**: This fetches the public metadata for one page by name. It returns nothing if the page does not exist or is not visible to the caller.

**Data flow**: It receives the tool context and requested page name. It asks PageObjects._find for the matching visible page. If found, it converts that page to a PageSpec; otherwise it returns null.

**Call relations**: This is the read path for a single page object. It depends on PageObjects._find to apply the same visibility rules used elsewhere before returning the page specification.

*Call graph*: calls 1 internal fn (_find).


##### `PageObjects.status`  (lines 110–119)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: This returns operational details about one page, such as its source row and timestamps. It is useful for understanding where a synced page came from and when it last changed.

**Data flow**: It receives the tool context and page name. It looks up the visible page. If none is found, it returns null. If found, it returns a small dictionary with the source id, backend name, creation time, and update time, with dates formatted as text.

**Call relations**: Like get and delete, this starts with PageObjects._find so it only reports status for pages the caller is allowed to see.

*Call graph*: calls 1 internal fn (_find).


##### `PageObjects.apply`  (lines 121–124)

```
async def apply(self, ctx: ToolContext, name: str, spec: PageSpec, old: PageSpec | None) -> None
```

**Purpose**: This refuses create and update attempts for page objects. Pages can only be landed by the sync driver, so user-side object apply is not allowed.

**Data flow**: It receives the requested name, new spec, and optional old spec, but does not use them to change storage. It always raises a VerbNotSupported error explaining that pages are synced rather than authored here.

**Call relations**: The object system calls apply for create or update-style operations. This implementation immediately stops that flow with VerbNotSupported instead of handing anything to storage.

*Call graph*: 1 external calls (__init__).


##### `PageObjects.delete`  (lines 126–132)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: This forgets a synced page, but only when the speaker is the workspace owner. Forgetting means marking the page as removed so later cleanup can clear indexed data derived from it.

**Data flow**: It receives the tool context and page name. First it checks whether the speaker is the owner. If not, it raises OwnerRequired. Then it finds the visible page by name. If no page is found, it raises a value error. If found, it gets the extension context and asks it to forget that page id.

**Call relations**: This is the delete path exposed through PAGE_OBJECT. It calls ToolContext.speaker_is_owner for the permission gate, PageObjects._find to identify the page, and _require_ext so it can hand the final forget request to the extension context.

*Call graph*: calls 3 internal fn (speaker_is_owner, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects._find`  (lines 134–135)

```
async def _find(self, ctx: ToolContext, name: str) -> _Page | None
```

**Purpose**: This looks for one visible page with a specific object name. It centralizes the common lookup used by get, status, and delete.

**Data flow**: It receives the tool context and a page name. It loads the caller-visible page list through PageObjects._pages and returns the first page whose name matches. If none match, it returns null.

**Call relations**: PageObjects.get, PageObjects.status, and PageObjects.delete all call this before doing their own work. It delegates the actual reading and visibility filtering to PageObjects._pages.

*Call graph*: calls 1 internal fn (_pages); called by 3 (delete, get, status).


##### `PageObjects._pages`  (lines 137–152)

```
async def _pages(self, ctx: ToolContext) -> tuple[_Page, ...]
```

**Purpose**: This loads all live source pages the current caller is allowed to see and wraps them in the file’s internal _Page shape. It is the shared reader behind listing and lookup.

**Data flow**: It receives the tool context. It gets the extension context, reads registered sources so it can map source ids to backend names, computes the allowed visibility subjects, then reads source page records for those subjects. Each record becomes an _Page containing ids, source information, visibility, digest, body reference, and timestamps.

**Call relations**: PageObjects.list calls this to build browse results, and PageObjects._find calls it to locate one page. Inside, it uses _require_ext for access to extension APIs, _audience_subjects for visibility filtering, and _Page construction to present records in a convenient local form.

*Call graph*: calls 2 internal fn (_audience_subjects, _require_ext); called by 2 (_find, list); 1 external calls (__init__).


### `extensions/sources/ufo_ext_sources/tools.py`

`domain_logic` · `object requests and page-change notifications`

A “source” here means one connection to an outside provider account, plus the selected streams of data to sync from it. For example, one Zendesk account might sync tickets and users. This file turns that connection into an object with a stable name, ownership rules, validation, and change notifications.

The important idea is that the source’s identity comes from its real connection details: provider, account, and tenant URL. Like a passport number, the name is derived from those facts, not chosen freely. If someone applies the same setup under the wrong name, the code refuses and tells them the right name. If they want different streams, they must delete and recreate the source, because changing streams changes what is being synced.

The file also protects privacy. A source is private to the registering member unless it is explicitly shared. Only the registering member or workspace owner can share or delete it. Subscriptions are different: any member who can see a source may subscribe their own conversation, but cannot subscribe or unsubscribe anyone else.

Finally, when synced pages change, the page-change hook looks up which source binding they came from, filters out private pages, and invokes subscribed conversations with readable page references.

#### Function details

##### `_binding_name`  (lines 132–138)

```
def _binding_name(provider: str, account: str, base_url: str | None) -> str
```

**Purpose**: Creates the official object name for a source binding. The name is based on the provider, account, and base URL, so the same real-world connection always gets the same name.

**Data flow**: It receives a provider name, an account id, and an optional base URL. It turns those identity details into sorted JSON, hashes them with SHA-256, keeps a short digest, and returns a readable name like provider-1234abcd.

**Call relations**: SourceObjects._apply_owned uses this when checking whether a user applied a source under the correct name. _Binding.name uses it whenever an existing binding needs to expose its derived object name.

*Call graph*: called by 2 (_apply_owned, name); 2 external calls (sha256, dumps).


##### `_Binding.name`  (lines 159–160)

```
def name(self) -> str
```

**Purpose**: Returns the derived object name for an existing source binding. This keeps stored bindings and newly applied specs using the same naming rule.

**Data flow**: It reads the binding’s provider, account, and base URL, passes them to _binding_name, and returns the resulting string.

**Call relations**: Other code treats this property as the binding’s object name. It delegates the actual naming rule to _binding_name so listing, finding, applying, and alerts all agree.

*Call graph*: calls 1 internal fn (_binding_name).


##### `_Binding.spec`  (lines 162–170)

```
def spec(self, subscribers: tuple[str, ...]=()) -> SourceSpec
```

**Purpose**: Turns an internal source binding back into the public spec shown to object users. This is how object_get can describe the source in the same shape that object_apply accepts.

**Data flow**: It reads the binding’s provider, streams, account, base URL, sharing subject, and optional subscriber list. It returns a SourceSpec with direct-account details hidden as an empty account_id and sharing converted into a true or false value.

**Call relations**: This is used when the object layer needs to compare or show a binding’s configuration. SourceObjects._apply_owned also uses the same spec shape to decide whether a re-apply is a no-op, a share change, or an unsupported config change.

*Call graph*: 1 external calls (__init__).


##### `_Binding.summary`  (lines 172–174)

```
def summary(self) -> str
```

**Purpose**: Builds a short human-readable summary of a source binding. It is meant for lists and alert messages where a compact label is more useful than the full spec.

**Data flow**: It reads the provider, account, and stream names, joins the stream names together, and trims the final sentence to a maximum length.

**Call relations**: _alert_message uses this to make change alerts understandable to the subscribed conversation.

*Call graph*: called by 1 (_alert_message).


##### `_require_ext`  (lines 177–180)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Checks that the current tool call has access to the extension context. The extension context is the gateway to stored sources, credentials, and the extension’s private storage.

**Data flow**: It receives a ToolContext. If the extension context is present, it returns it; if not, it raises a runtime error because source objects cannot work without it.

**Call relations**: Most SourceObjects methods call this before reading or changing registered sources. It is a guardrail that catches a wiring mistake early instead of failing later in a less clear way.

*Call graph*: called by 7 (_apply_owned, _bindings, _delete_owned, _resolved_account, _spec, _status, apply).


##### `_require_connectors`  (lines 183–186)

```
def _require_connectors(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Checks that the current tool call has a connector registry. The connector registry is the catalog of connected-account providers available in this turn.

**Data flow**: It receives a ToolContext. If connector information exists, it returns it; otherwise it raises a runtime error.

**Call relations**: SourceObjects._resolved_account calls this while deciding whether a provider should use a connected account or a workspace credential.

*Call graph*: called by 1 (_resolved_account).


##### `_bindings_from_ext`  (lines 189–216)

```
async def _bindings_from_ext(ext: ExtensionContext) -> tuple[_Binding, ...]
```

**Purpose**: Reconstructs source bindings from the lower-level stored source rows. Each stream is stored separately, so this function groups rows back into the one source object users see.

**Data flow**: It asks the extension context for all registered source rows. It ignores rows for unknown providers, validates each row’s connector config, groups rows by provider, account, and base URL, and returns _Binding objects with sorted streams.

**Call relations**: SourceObjects._bindings uses this for object listing, lookup, status, and specs. on_page_change also uses it to connect changed page rows back to the source binding that produced them.

*Call graph*: calls 1 internal fn (sources); called by 2 (_bindings, on_page_change); 3 external calls (__init__, __init__, model_validate).


##### `_subscribers_map`  (lines 219–231)

```
async def _subscribers_map(ext: ExtensionContext, name: str) -> dict[str, str]
```

**Purpose**: Reads the saved subscriber list for one source. The list maps conversation ids to agent ids so alerts can re-enter the right conversation with the right agent.

**Data flow**: It receives the extension context and source name. It reads a key from extension storage, returns an empty map if nothing is stored, returns a copy if the stored value is valid, and raises an error if the stored data has the wrong shape.

**Call relations**: SourceObjects._edit_subscribers reads this before changing one subscriber entry. SourceObjects._spec and SourceObjects._status use it to show subscription state. on_page_change uses it to decide whom to alert.

*Call graph*: called by 4 (_edit_subscribers, _spec, _status, on_page_change).


##### `_store_subscribers`  (lines 234–240)

```
async def _store_subscribers(ext: ExtensionContext, name: str, mapping: dict[str, str]) -> None
```

**Purpose**: Writes the subscriber map for a source, or removes it when there are no subscribers left. This keeps extension storage tidy.

**Data flow**: It receives the extension context, source name, and subscriber map. If the map has entries, it writes them under the source’s subscriber key; if the map is empty, it deletes that key.

**Call relations**: SourceObjects._edit_subscribers calls this after adding or removing the caller. SourceObjects._delete_owned calls it with an empty map when the source itself is removed.

*Call graph*: called by 2 (_delete_owned, _edit_subscribers).


##### `_binding_identity`  (lines 243–244)

```
def _binding_identity(spec: SourceSpec) -> tuple[str, tuple[str, ...], str, str, bool]
```

**Purpose**: Extracts the parts of a source spec that define the source’s real identity. It ignores subscriber changes because subscriptions are allowed to change without recreating the source.

**Data flow**: It receives a SourceSpec. It returns a tuple containing provider, sorted streams, account id, base URL, and sharing flag.

**Call relations**: SourceObjects.apply compares old and new identities with this helper. If only subscribers changed, apply takes the lighter subscription-edit path.

*Call graph*: called by 1 (apply).


##### `_self_only_change`  (lines 247–254)

```
def _self_only_change(old: tuple[str, ...], new: tuple[str, ...], caller: str) -> None
```

**Purpose**: Enforces the rule that a conversation may only add or remove its own subscriber id. This stops one conversation from silently subscribing or unsubscribing another.

**Data flow**: It receives the old subscriber ids, the new subscriber ids, and the caller’s conversation id. It compares the two sets and raises a ValueError if anything changed besides the caller’s own id.

**Call relations**: SourceObjects.apply calls this before allowing a subscribers-only edit. If the check passes, SourceObjects.apply hands off to _edit_subscribers.

*Call graph*: called by 1 (apply).


##### `SourceObjects.apply`  (lines 276–291)

```
async def apply(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None) -> None
```

**Purpose**: Applies a requested source object change, with special treatment for subscription edits. It lets visible members subscribe themselves without requiring ownership, while all other changes follow the stricter owner rules from the base object system.

**Data flow**: It receives the tool context, object name, requested SourceSpec, and the old visible spec if one exists. If the source identity is unchanged, it verifies that only the caller’s subscriber id changed and updates subscriptions. Otherwise it delegates to the parent apply logic for normal create, share, or replace behavior.

**Call relations**: This is the first custom method reached for object_apply on a source. It calls _binding_identity, _self_only_change, _require_ext, and _edit_subscribers for subscription-only edits; for everything else, it relies on MemberOwnedObjects’ apply flow, which later calls _apply_owned.

*Call graph*: calls 4 internal fn (_edit_subscribers, _binding_identity, _require_ext, _self_only_change).


##### `SourceObjects._edit_subscribers`  (lines 293–304)

```
async def _edit_subscribers(self, ext: ExtensionContext, name: str, desired: tuple[str, ...], caller: str, agent: UUID) -> None
```

**Purpose**: Adds or removes the caller’s conversation from a source’s subscriber list. When adding, it remembers the agent id so later alerts go back to the same agent in the same conversation.

**Data flow**: It receives the extension context, source name, desired subscriber ids, caller conversation id, and agent id. It loads the current subscriber map, adds or removes only the caller’s entry, and stores the updated map.

**Call relations**: SourceObjects.apply calls this after proving the requested change is subscriber-only and self-only. It uses _subscribers_map and _store_subscribers as the read and write steps.

*Call graph*: calls 2 internal fn (_store_subscribers, _subscribers_map); called by 1 (apply).


##### `SourceObjects._owned_rows`  (lines 306–317)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow, ...]
```

**Purpose**: Builds the lightweight rows used when listing source objects. Each row contains the object name, a short summary, and ownership information.

**Data flow**: It reads all bindings through _bindings. For each binding, it creates an OwnedRow with the derived name, summary text, and an ObjectOwner showing whether it is shared and who registered it.

**Call relations**: The base object system calls this when it needs to list or find visible source objects. It depends on _bindings to turn stored stream rows into user-facing source bindings.

*Call graph*: calls 1 internal fn (_bindings); 2 external calls (__init__, __init__).


##### `SourceObjects._spec`  (lines 319–324)

```
async def _spec(self, ctx: ToolContext, name: str) -> SourceSpec | None
```

**Purpose**: Returns the public spec for a named source object, including its subscriber ids. This powers object_get-style reads of the source manifest.

**Data flow**: It receives the context and object name. It finds the binding, reads subscriber ids from extension storage, and returns the binding as a SourceSpec; if the binding does not exist, it returns null.

**Call relations**: The object framework calls this when it needs the full spec for a source. It uses _find to locate the binding, then _subscribers_map to add the subscription information.

*Call graph*: calls 3 internal fn (_find, _require_ext, _subscribers_map).


##### `SourceObjects._status`  (lines 326–347)

```
async def _status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: Builds live status information for a named source. This tells the caller whether it is shared, whether the current conversation is subscribed, and how each stream is doing.

**Data flow**: It receives the context and object name. It finds the binding, reads subscribers, checks the caller’s conversation id, and returns a dictionary with sharing state, subscriber id, subscribed true or false, per-stream next sync time, and error counts. For private owned sources, it also includes the owner member id.

**Call relations**: The object framework calls this alongside object reads or explanations. It uses _find for the source data and _subscribers_map for the caller’s subscription state.

*Call graph*: calls 3 internal fn (_find, _require_ext, _subscribers_map).


##### `SourceObjects._apply_owned`  (lines 349–417)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Creates a new source binding or performs the limited allowed changes to an existing one. It validates the provider, stream names, tenant URL, account or credential, derived object name, and sharing rules before registering anything.

**Data flow**: It receives the context, requested name, spec, previous spec, and owner information. It verifies there is a speaking member, rejects subscribers during first registration, checks that the provider and streams exist, validates the base URL, resolves the account or credential, derives the required name, and compares it to the supplied name. If an existing binding is found, it either does nothing, flips private to shared, or refuses unsupported config changes. If no binding exists, it registers one source row per stream.

**Call relations**: The parent object apply flow calls this after ownership gates pass. It calls _require_ext, _validated_base_url, _resolved_account, _binding_name, and _find, then uses the extension context to register sources or mark existing source rows as shared.

*Call graph*: calls 5 internal fn (_find, _resolved_account, _binding_name, _require_ext, _validated_base_url); 5 external calls (__init__, __init__, __init__, member_subject, get).


##### `SourceObjects._delete_owned`  (lines 419–426)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Deletes a source binding and clears its subscriptions. Removing the binding also removes each stream row that feeds synced pages.

**Data flow**: It receives the context, source name, and owner information. It finds the binding, raises an UnknownObject error if it is missing, removes every stream source id from the extension, and deletes the subscriber map.

**Call relations**: The base object system calls this after delete permission has been checked. It uses _find to locate the binding, _require_ext to reach the extension API, and _store_subscribers to clear alert subscriptions.

*Call graph*: calls 3 internal fn (_find, _require_ext, _store_subscribers); 1 external calls (__init__).


##### `SourceObjects._resolved_account`  (lines 428–466)

```
async def _resolved_account(self, ctx: ToolContext, spec: SourceSpec) -> str
```

**Purpose**: Decides which authentication identity a source should use. Some providers use connected accounts; others use a workspace credential, sometimes called BYOK, meaning “bring your own key.”

**Data flow**: It receives the context and source spec. If the provider is in the connector registry, it fetches active connected accounts, asks the user to choose when needed, and returns the chosen account id. If the provider uses a direct workspace credential, it checks that no account_id was supplied, verifies the credential is set, and returns the special direct-account marker.

**Call relations**: SourceObjects._apply_owned calls this before deriving the final source name and registering streams. It uses _require_ext and _require_connectors because it needs both credential information and connector-account information.

*Call graph*: calls 3 internal fn (connector_accounts, _require_connectors, _require_ext); called by 1 (_apply_owned).


##### `SourceObjects._find`  (lines 468–471)

```
async def _find(self, ctx: ToolContext, name: str) -> _Binding | None
```

**Purpose**: Looks up one source binding by its derived object name. It is the small search helper used by read, status, apply, and delete paths.

**Data flow**: It receives the context and source name. It loads all current bindings, scans for a binding whose name matches, and returns that binding or null.

**Call relations**: SourceObjects._spec, _status, _apply_owned, and _delete_owned call this whenever they need to work with one named source. It relies on _bindings for the current reconstructed binding list.

*Call graph*: calls 1 internal fn (_bindings); called by 4 (_apply_owned, _delete_owned, _spec, _status).


##### `SourceObjects._bindings`  (lines 473–474)

```
async def _bindings(self, ctx: ToolContext) -> tuple[_Binding, ...]
```

**Purpose**: Loads all source bindings visible to this object store implementation. It is a thin wrapper that ensures the extension context exists before reconstructing bindings.

**Data flow**: It receives the tool context, extracts the extension context, calls _bindings_from_ext, and returns the resulting tuple of bindings.

**Call relations**: SourceObjects._owned_rows and _find call this as their common source of truth. It connects the object-level code to the extension’s stored source rows.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); called by 2 (_find, _owned_rows).


##### `on_page_change`  (lines 477–516)

```
async def on_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Sends alert turns to conversations subscribed to sources whose synced pages changed. It only alerts about shared pages, so private page changes are not leaked.

**Data flow**: It receives a hook context and expects a PageChangeBatch payload. It rebuilds bindings, maps changed source ids to bindings, groups changes by binding, loads each binding’s subscribers, keeps only shared changes, builds an alert message, and invokes each subscribed conversation with an idempotency key so replayed batches do not duplicate alerts.

**Call relations**: The extension hook system calls this when page changes are reported. It uses _bindings_from_ext to understand which source produced each change, _subscribers_map to find listeners, and _alert_message to create the text sent into each subscribed conversation.

*Call graph*: calls 3 internal fn (_alert_message, _bindings_from_ext, _subscribers_map); 1 external calls (UUID).


##### `_alert_message`  (lines 519–530)

```
def _alert_message(binding: _Binding, changes: list[PageChange]) -> str
```

**Purpose**: Creates the plain-language message sent when a subscribed source changes. The message tells the agent which source changed and which page objects to inspect.

**Data flow**: It receives a binding and a list of page changes. It builds a short list of page references, counts extra and removed pages, chooses singular or plural wording, includes the binding summary, and returns one complete instruction message.

**Call relations**: on_page_change calls this once per changed binding before invoking subscribers. It calls _page_reference for each displayed page and _Binding.summary to describe the source.

*Call graph*: calls 2 internal fn (summary, _page_reference); called by 1 (on_page_change).


##### `_page_reference`  (lines 533–539)

```
def _page_reference(change: PageChange) -> str
```

**Purpose**: Formats one changed page as a readable object reference. It gives the alerted agent both the page object id and a small label from the page body when available.

**Data flow**: It receives a PageChange. If the page is not deleted and has body text, it takes the first line as a label; otherwise it uses a fallback label. It returns text like page/<id> (label).

**Call relations**: _alert_message calls this while building the list of changed pages included in a subscriber alert.

*Call graph*: called by 1 (_alert_message).


##### `_validated_base_url`  (lines 542–576)

```
def _validated_base_url(provider: str, base_url: str | None) -> str | None
```

**Purpose**: Checks and normalizes tenant API URLs for providers that need them. This prevents unsafe or wrongly shaped URLs from being stored as source configuration.

**Data flow**: It receives a provider and optional base URL. For providers with a fixed API host, it rejects overrides. For tenant-specific providers, it requires a URL, parses it, checks that it uses HTTPS, has no username, password, port, query, or fragment, and matches the provider’s allowed host and path pattern. It returns a normalized HTTPS URL or null for fixed-host providers.

**Call relations**: SourceObjects._apply_owned calls this before resolving the account and deriving the binding name. Its validation errors tell the caller the expected URL shape for that provider.

*Call graph*: called by 1 (_apply_owned); 1 external calls (urlsplit).


### `extensions/skill_create/ufo_ext_skill_create/store.py`

`domain_logic` · `request handling and per-turn skill loading`

A “skill” here is a small directory of files, led by a SKILL.md file, that teaches the agent some reusable behavior. This file is the safe storage layer for skills created by a user inside a workspace. Without it, a skill made during one turn could disappear when the temporary sandbox is thrown away, or worse, leak into another workspace or replace a built-in skill.

The store uses a database table named user_skill. Each row belongs to one workspace and one skill name. The actual files are packed into a JSON object where each file’s bytes are base64-encoded. Base64 is a common way to turn raw bytes into plain text so they can be stored safely in a text database column. A SHA-256 digest, which is a fingerprint of the stored content, records the saved version.

Before saving, the store checks that the skill name is a simple safe slug, such as “daily-report”. It parses the skill with the same parser used for normal runtime skills, refuses names that would collide with core or pack skills, and caps each workspace at 100 saved skills. When loading, it reads only that workspace’s rows, decodes the files, parses them back into runtime skills, and skips any broken stored skill with a warning instead of blocking the whole workspace.

#### Function details

##### `UserSkillStore.save`  (lines 86–147)

```
async def save(self, workspace_id: UUID, name: str, files: Mapping[str, bytes], registry_names: frozenset[str]) -> RuntimeSkill
```

**Purpose**: Saves a user-authored skill for one workspace after checking that it is safe, valid, and allowed. It returns the parsed skill so the caller can use the same version that was persisted.

**Data flow**: It receives a workspace ID, a proposed skill name, a map of file paths to file bytes, and the set of skill names already available this turn. It first rejects unsafe names, parses the files as a real skill, checks whether this workspace already owns that name, rejects attempts to override core or pack skills, and enforces the per-workspace skill limit for new names. It then base64-encodes every file, stores the bundle as JSON, computes a SHA-256 fingerprint, and updates the existing database row or inserts a new one. The output is the parsed RuntimeSkill, and the database is changed to contain the latest saved files.

**Call relations**: This is the main entry point for persisting a new or edited user skill. During its checks, it asks UserSkillStore._owns whether this workspace already has the name and UserSkillStore._count how many skills the workspace has saved. It hands the file contents to parse_skill_content so saving uses the same validation path as other skills, then writes the final bundle through the extension transaction.

*Call graph*: calls 2 internal fn (_count, _owns); 9 external calls (__init__, __init__, __init__, __init__, b64encode, sha256, insert, update, parse_skill_content).


##### `UserSkillStore.load_all`  (lines 149–186)

```
async def load_all(self, workspace_id: UUID) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Loads every saved skill for one workspace so they can be added to the runtime skill registry for a turn. It keeps failures isolated: one bad stored skill is logged and skipped instead of breaking the whole turn.

**Data flow**: It receives a workspace ID and reads all stored skill names and content blobs for that workspace from the database. For each row, it validates the stored JSON shape, base64-decodes the files back to bytes, and parses those files into a RuntimeSkill. The output is a tuple of successfully parsed skills; invalid or corrupt rows do not appear in the result, and a warning is written to the log.

**Call relations**: This function is used when the system is preparing the skills available during a turn. It reads from the same database rows that UserSkillStore.save writes, then hands each decoded file bundle to parse_skill_content so the runtime sees normal RuntimeSkill objects rather than raw stored text.

*Call graph*: 3 external calls (b64decode, select, parse_skill_content).


##### `UserSkillStore.files`  (lines 188–203)

```
async def files(self, workspace_id: UUID, name: str) -> dict[str, bytes] | None
```

**Purpose**: Fetches the original file bundle for one saved skill in one workspace. This is useful when another part of the extension needs to inspect, edit, or display the saved files rather than just load the skill into the registry.

**Data flow**: It receives a workspace ID and skill name, then looks for that exact row in the database. If no row exists, it returns None. If the row exists, it validates the stored JSON and base64-decodes each saved file back into bytes. The output is a dictionary from relative file paths to raw file bytes.

**Call relations**: This is a read-back helper for one specific skill. It uses the same stored content format created by UserSkillStore.save, but unlike UserSkillStore.load_all, it does not parse the files into a RuntimeSkill; it returns the files themselves.

*Call graph*: 2 external calls (b64decode, select).


##### `UserSkillStore.delete`  (lines 205–212)

```
async def delete(self, workspace_id: UUID, name: str) -> None
```

**Purpose**: Removes one saved user skill from one workspace. It lets a workspace clean up skills it no longer wants, including making room under the saved-skill limit.

**Data flow**: It receives a workspace ID and skill name, then deletes the matching database row if it exists. It returns nothing. After it runs, that skill will no longer be loaded for that workspace on future turns.

**Call relations**: This is the counterpart to UserSkillStore.save. Where save inserts or replaces a skill row, delete removes the row so later calls to UserSkillStore.load_all or UserSkillStore.files will not find that skill.

*Call graph*: 1 external calls (delete).


##### `UserSkillStore.updated_at`  (lines 214–224)

```
async def updated_at(self, workspace_id: UUID, name: str) -> datetime | None
```

**Purpose**: Looks up when a saved skill was last changed. Callers can use this timestamp to show freshness, compare versions, or decide whether something needs to be refreshed.

**Data flow**: It receives a workspace ID and skill name, then reads the updated_at column for that exact database row. If the skill is not saved in that workspace, it returns None. Otherwise, it returns the stored datetime.

**Call relations**: This is a small lookup beside the main save/load flow. UserSkillStore.save updates the timestamp whenever it writes a skill, and updated_at later exposes that saved time to callers that need metadata rather than file contents.

*Call graph*: 1 external calls (select).


##### `UserSkillStore._count`  (lines 226–234)

```
async def _count(self, workspace_id: UUID) -> int
```

**Purpose**: Counts how many user skills are currently saved in one workspace. It exists to enforce the rule that a workspace cannot accumulate unlimited saved skills.

**Data flow**: It receives a workspace ID and asks the database how many user_skill rows belong to that workspace. It returns that number as an integer and does not change anything.

**Call relations**: UserSkillStore.save calls this when it is about to add a new skill name. If the count has reached the workspace limit, save refuses the new skill instead of letting future turns become slower and storage grow without bound.

*Call graph*: called by 1 (save); 1 external calls (select).


##### `UserSkillStore._owns`  (lines 236–246)

```
async def _owns(self, workspace_id: UUID, name: str) -> bool
```

**Purpose**: Checks whether a workspace already has a saved user skill with a given name. This tells the save path whether it is updating an existing user skill or trying to create a new one.

**Data flow**: It receives a workspace ID and skill name, then searches for a matching database row. It returns true if the row exists and false otherwise. It only reads the database.

**Call relations**: UserSkillStore.save uses this check before deciding what rules apply. If the workspace already owns the name, saving is treated as an update; if not, save checks for collisions with core or pack skills and checks the workspace skill count before inserting.

*Call graph*: called by 1 (save); 1 external calls (select).

## 📊 State Registers Touched

- `reg-workspace-context` — The current workspace and member context that keeps every request acting inside the right tenant boundary.
- `reg-workspaces-members-agents` — The durable records for workspaces, their members, and the agents that can act for them.
- `reg-identity-and-session-tokens` — The identities, bearer tokens, gateway tokens, operator sessions, and other passes that prove who is allowed in.
- `reg-onboarding-claims-invites` — The temporary signup codes, email claims, and invite records used before or during workspace creation.
- `reg-connected-credentials` — The encrypted store of outside account connections and secrets that tools and sync jobs may use safely.
- `reg-extension-pack-lock` — The saved choice of active packs and installed extensions for a workspace.
- `reg-extension-state-store` — The per-workspace key-value storage area where extensions keep their own persistent settings and data.
- `reg-capability-registry` — The loaded menu of extension-provided routes, tools, skills, hooks, jobs, credentials, models, and search backends.
- `reg-search-index-state` — The searchable content indexes, chunks, embeddings, and search backend choices used for recall and source replay.
- `reg-sandbox-session-policy` — The safe execution room state: sandbox handles, mounted workspace files, storage access, and restrictions.
- `reg-conversation-transcript` — The saved conversation thread, messages, transcript edits, and compacted summaries.
- `reg-turn-queue-run-state` — The durable state of each agent turn, including whether it is queued, claimed, running, parked, finished, failed, or cancelled.
- `reg-inbound-surface-state` — The stored inbound messages and surface delivery keys that connect Slack, web, terminal, and other fronts to conversations.
- `reg-artifact-blob-store` — The shared file and blob storage used for uploaded content, generated artifacts, and token-protected downloads.
- `reg-source-sync-state` — The remembered external sources, imported pages, raw bodies, change records, cursors, errors, and deletion markers.
- `reg-memory-store` — The workspace memory facts, episodes, ownership labels, confidence, and consolidation indexes used for recall.
- `reg-knowledge-graph` — The stored people, companies, things, and relationships used as structured background knowledge.
- `reg-scheduled-task-calendar` — The durable calendar of one-time and repeating tasks that workers can safely claim and run later.
- `reg-runtime-instance-fleet` — The heartbeat records for live worker and runtime processes, used to detect dead workers and clean up abandoned work.
- `reg-accounting-ledger-spend` — The usage ledger, spend caps, exports, and cost totals for models, egress, and other billable work.
- `reg-seat-billing-state` — The workspace seat limits, granted seats, included seats, and external billing integration state.
- `reg-workspace-object-catalog` — The named workspace objects and object-type registry used to list, inspect, validate, change, or delete stored things.
- `reg-prompt-governance` — The saved prompt digests, prompt-change proposals, approvals, and experiment evidence that control instruction changes.
- `reg-subagent-work-tree` — The parent-child turn links, delegated work state, and message flow between main agents and subagents.
- `reg-schema-migration-version` — The Alembic/schema version state recording which core and extension migrations have been applied before runtime uses the database.
- `reg-database-connection-pool` — The process-wide database engine/session factory and connection pool used by request handlers, workers, schedulers, and persistence code.
- `reg-action-proposal-state` — Durable non-governance proposals created by agents or tools for later user/operator review, approval, rejection, or commit.
- `reg-hosted-domain-workspace-map` — Durable hosted onboarding mapping from verified email domains to the shared workspace used for automatic member provisioning.
- `reg-agent-todo-goal-state` — The agent-maintained goal/todo checklist state that tools update and later prompt/runtime assembly can reload as working context.
