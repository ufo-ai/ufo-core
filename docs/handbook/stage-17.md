# Cross-cutting data model, persistence, blob storage, and database safety  `stage-17` (cross-cutting infrastructure)

This stage is the system’s shared filing cabinet. It sits behind every phase: startup uses it to prepare the database, the main work loop uses it to save and read state, and shutdown uses it to close connections safely. The database gateway opens connections, runs migrations, wraps work in safe transactions, and cleans up afterward. The table blueprint defines the common database shape, while the schema package groups these rules. Record models define stable conversation turns and IDs, and the transcript format keeps saved conversations readable over time, including compacted versions.

Safety is a major part of this stage. The row-level security setup keeps each workspace’s data separate, like locked drawers in the same cabinet, and creates the limited database role used during normal service. Blob storage provides one interface for large files, whether stored locally or in S3.

The migration files are upgrade instructions. They add and adjust storage for searchable chunks, memory records, memory pages, timestamps, provenance links, fast lookup indexes, and knowledge-graph entities and relationships. Together they let the data model grow without breaking old installations.

## Files in this stage

### Transcript contracts
Defines the persisted conversation and compaction shapes that higher-level readers and writers share.

### `core/src/ufo/transcript.py`

`data_model` · `cross-cutting transcript persistence and readback`

A conversation in this system is stored as durable blobs: compressed chunks of data that can be written by one part of the program and read later by another. This file is the common agreement about what those blobs are called, what they contain, and how they are turned back into usable Python objects. Without it, the conversation writer, debug tools, and evaluation code could each make slightly different assumptions and break each other.

The main record is `Conversation`, which stores the message window at a sequence number, plus optional extra context such as the system prompt and injected text used for a completed turn. The file also defines how to build the storage path for that transcript, how to convert it to compact JSON, and how to compress it with LZ4, a fast compression format.

It also describes compaction records. Compaction is when an older, larger conversation window is summarized into a smaller one so the model can keep working without carrying every old message. The file records the messages before compaction, the messages after compaction, and a structured summary explaining what was preserved. Reading helpers fetch these pieces from a `BlobStore`, which is the project’s abstract place for stored bytes. If bytes are missing, the reader treats that compaction index as absent; if bytes are present but invalid, it raises a clear transcript decoding error.

#### Function details

##### `transcript_key`  (lines 37–38)

```
def transcript_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the storage path for the main transcript blob of one conversation. Code that saves or loads a conversation uses this so everyone looks in the same place.

**Data flow**: It takes a conversation identifier, formats it into a predictable path under `conversations/`, and returns that path as text. It does not read or write storage itself; it only names the shelf where the transcript belongs.

**Call relations**: This is the shared naming rule for transcript blobs. Writers and readers can use it independently so they agree on the exact location of `messages.json.lz4` for a conversation.


##### `encode`  (lines 41–43)

```
def encode(conversation: Conversation) -> bytes
```

**Purpose**: Turns a `Conversation` object into compressed bytes ready to store. It is used when the system wants a durable, compact copy of a conversation window.

**Data flow**: It receives a validated `Conversation`, converts it into plain data, writes that data as stable JSON, encodes the JSON as bytes, and compresses those bytes with LZ4. The result is a byte string suitable for saving in a blob store.

**Call relations**: This is the write-side partner to `decode`. It relies on the conversation model’s own dump method and JSON encoding before compression, so later readers can reverse the process consistently.

*Call graph*: 2 external calls (model_dump, dumps).


##### `decode`  (lines 46–50)

```
def decode(body: bytes) -> Conversation
```

**Purpose**: Turns stored transcript bytes back into a validated `Conversation`. It gives callers a clean error if the blob is corrupt, compressed incorrectly, or no longer matches the expected shape.

**Data flow**: It receives compressed bytes from storage, decompresses them, and asks the `Conversation` model to validate the JSON inside. If validation or decompression fails, it wraps the problem in `TranscriptDecodeError`; otherwise it returns the reconstructed `Conversation`.

**Call relations**: This is the read-side partner to `encode`. Any part of the system that loads a transcript can use it to get a safe, checked conversation object instead of working with raw bytes.

*Call graph*: 1 external calls (__init__).


##### `compaction_key`  (lines 101–102)

```
def compaction_key(conversation_id: UUID, index: int, half: CompactionHalf) -> str
```

**Purpose**: Builds the storage path for one piece of a compaction record: the before window, after window, or summary. It prevents different parts of the system from inventing different folder layouts.

**Data flow**: It takes a conversation identifier, a compaction number, and which part is wanted. It returns a path such as a labeled drawer inside that conversation’s `compactions` folder.

**Call relations**: When `read_compaction_record` needs to fetch the three stored pieces of a compaction, it calls this function three times to get the exact blob keys.

*Call graph*: called by 1 (read_compaction_record).


##### `decode_compaction`  (lines 105–114)

```
def decode_compaction(index: int, before: bytes, after: bytes, summary: bytes) -> CompactionRecord
```

**Purpose**: Rebuilds a complete compaction record from its three stored byte blobs. It checks that the before messages, after messages, and summary all match the expected formats.

**Data flow**: It receives the compaction index and three compressed byte strings. It decompresses and validates the before window, decompresses and validates the after window, decompresses and validates the structured summary, then returns a `CompactionRecord` tying them together. If any piece is unreadable or malformed, it raises `TranscriptDecodeError`.

**Call relations**: This function is called by `read_compaction_record` after the raw blobs have been fetched. It turns the storage-level pieces into the single object that debug tools, evaluation code, or other readers can understand.

*Call graph*: called by 1 (read_compaction_record); 2 external calls (__init__, __init__).


##### `read_compaction_record`  (lines 117–128)

```
async def read_compaction_record(blob: BlobStore, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Fetches one numbered compaction record for a conversation, if it exists. It is the shared read path for code that wants to inspect how a conversation was summarized.

**Data flow**: It receives a blob store, a conversation identifier, and a compaction index. It builds the three needed keys, asks the blob store for the before, after, and summary blobs, and returns `None` if any of them are missing. If all are present, it decodes them into a `CompactionRecord`.

**Call relations**: This function uses `compaction_key` to find each stored piece and `decode_compaction` to interpret the bytes. `read_compaction_records` calls it repeatedly, one index at a time, to gather the full history.

*Call graph*: calls 3 internal fn (get, compaction_key, decode_compaction); called by 1 (read_compaction_records).


##### `read_compaction_records`  (lines 131–141)

```
async def read_compaction_records(blob: BlobStore, conversation_id: UUID) -> tuple[CompactionRecord, ...]
```

**Purpose**: Reads all compaction records for a conversation in order. It stops at the first missing index because compactions are expected to be stored sequentially from one upward.

**Data flow**: It receives a blob store and conversation identifier, starts at index 1, and repeatedly asks for one compaction record. Each found record is added to a list; when an index has no record, the loop ends and the collected records are returned as an immutable tuple.

**Call relations**: This is the convenience reader built on top of `read_compaction_record`. Higher-level readers can call it when they want the whole compaction history without knowing the per-index storage pattern.

*Call graph*: calls 1 internal fn (read_compaction_record).


### Storage gateways and isolation
Provides the runtime persistence gateways for workspace-safe database access and local or cloud blob storage.

### `control/src/ufo_control/rls.py`

`domain_logic` · `startup / database bootstrap`

This file is about drawing a hard boundary inside a shared Postgres database. The project stores data for multiple workspaces in the same tables, so the database itself must help prevent one workspace from reading or changing another workspace’s rows. Postgres calls this row-level security, or RLS: rules attached to a table that decide which rows a database user may see or write.

The file has two main jobs. First, it creates or refreshes a restricted database role called `ufo_serve`. This is the role used by the running service, not the all-powerful owner role. It gives that role the table and sequence permissions it needs, sets safe timeouts, and creates a companion DBOS database if missing.

Second, it walks through every public table and makes sure the workspace policy is present and correct. Most tables must have a `workspace_id` column. The special `workspace` table uses its own `id` column instead. The policy compares that column with a per-session Postgres setting, `app.workspace_id`, which is like a label placed on the current database connection saying “this request is for workspace X.”

A useful detail is that the file checks whether a table is already correct before changing it. That avoids unnecessary database locks. If a lock blocks setup for too long, it reports who is holding the lock instead of silently hanging.

#### Function details

##### `owner_dsn`  (lines 22–26)

```
def owner_dsn() -> str
```

**Purpose**: Reads the database connection string for the powerful owner account from an environment variable. It refuses to continue if that value is missing, because the RLS setup cannot safely run without owner-level database access.

**Data flow**: It reads `UFO_CONTROL_POSTGRES_OWNER_DSN` from the process environment. If the value exists, it returns that string. If it is absent or empty, it raises an error explaining that there is no owner database connection string for the row-security setup.

**Call relations**: No caller is shown inside this file’s call graph, but this function is meant to be used by startup or provisioning code before calling the database setup routines here. It supplies the privileged connection information those routines need.


##### `serve_password`  (lines 29–33)

```
def serve_password() -> str
```

**Purpose**: Creates the password for the restricted serving database role in a repeatable way. Instead of storing the password directly, it derives it from a secret seed and the role name.

**Data flow**: It reads `UFO_CONTROL_PG_ROLE_SEED` from the environment. If the seed is missing, it raises an error. Otherwise it combines the seed with the serving role name, hashes that text with SHA-256, and returns the resulting hexadecimal password string.

**Call relations**: `ensure_serve_role` calls this when creating or updating the Postgres role password. `serve_dsn` also calls it when building the application’s normal database connection string. The hashing work is delegated to Python’s `hashlib.sha256`.

*Call graph*: called by 2 (ensure_serve_role, serve_dsn); 1 external calls (sha256).


##### `serve_dsn`  (lines 36–37)

```
def serve_dsn(postgres_host: str, app_database: str) -> str
```

**Purpose**: Builds the database connection string that the application should use when connecting as the restricted serving role. This keeps normal application traffic away from the owner account.

**Data flow**: It receives a Postgres host and an application database name. It calls `serve_password` to get the derived password, then combines the role name, password, host, and database into a SQLAlchemy-style async Postgres URL. The returned string is ready to be used as a connection DSN.

**Call relations**: This function depends on `serve_password` so the generated connection string always matches the password assigned by `ensure_serve_role`. It is typically used by code that needs to configure runtime database access.

*Call graph*: calls 1 internal fn (serve_password).


##### `ensure_serve_role`  (lines 40–62)

```
async def ensure_serve_role(admin_dsn: str) -> None
```

**Purpose**: Creates or refreshes the restricted Postgres role used by the service. It makes sure that role can connect, has the right password and permissions, can set the current workspace ID, and is protected by timeout settings.

**Data flow**: It receives an administrator database connection string. It derives the serving password, opens a Postgres connection, sets a lock timeout, checks whether the `ufo_serve` role already exists, and either creates it or updates its password. It grants the role permission to set `app.workspace_id`, applies idle transaction timeouts to key roles, grants table and sequence access, and ensures a related DBOS database exists. It closes the database connection when finished.

**Call relations**: This is one of the main setup routines in the file. It calls `serve_password` for the role password, uses `asyncpg.connect` to talk to Postgres, calls `_grant_serve_role` to apply permissions, and calls `_ensure_database` to create the companion database if needed.

*Call graph*: calls 3 internal fn (_ensure_database, _grant_serve_role, serve_password); 1 external calls (connect).


##### `bootstrap_policies`  (lines 65–92)

```
async def bootstrap_policies(dsn: str) -> None
```

**Purpose**: Checks every public application table and makes sure the workspace row-security policy is enabled and correct. Without this, a shared table might accidentally expose rows from the wrong workspace.

**Data flow**: It receives a database connection string, opens a Postgres connection, sets a short lock timeout, and lists all public tables. It skips the Alembic migration-version table because that table is bookkeeping, not workspace data. For each other table, it first asks `_conformant` whether the existing policy is already correct. If not, it opens a short transaction and calls `_policy_for` to recreate the policy. If a database lock blocks the work too long, it asks `_lock_holders` who is blocking it and raises a clear error. The connection is closed at the end.

**Call relations**: This is the second main setup routine in the file. It uses `asyncpg.connect` for database access, `_conformant` for the cheap safety check, `_policy_for` for actual policy creation, and `_lock_holders` only when Postgres reports that a lock could not be obtained.

*Call graph*: calls 3 internal fn (_conformant, _lock_holders, _policy_for); 1 external calls (connect).


##### `_conformant`  (lines 95–123)

```
async def _conformant(connection: asyncpg.Connection, table: str) -> bool
```

**Purpose**: Checks whether one table already has the exact row-security setup this project expects. It is a safer first step than blindly rewriting policies, because it usually avoids taking heavy database locks.

**Data flow**: It receives an open database connection and a table name. It reads Postgres system catalogs to find whether row-level security is enabled and whether the managed policy has the expected filter, write check, command coverage, and role coverage. It calls `_scope_column` to know which column should be compared with the current workspace setting. It returns `true` if everything matches exactly, otherwise `false`.

**Call relations**: `bootstrap_policies` calls this before attempting any table changes. `_conformant` hands off the table-column decision to `_scope_column`, then returns a simple yes-or-no answer that decides whether `bootstrap_policies` can skip the table or must call `_policy_for`.

*Call graph*: calls 1 internal fn (_scope_column); called by 1 (bootstrap_policies); 1 external calls (fetchrow).


##### `_lock_holders`  (lines 126–141)

```
async def _lock_holders(connection: asyncpg.Connection, table: str) -> str
```

**Purpose**: Produces a human-readable explanation of who is currently holding locks on a table. This is used to turn a confusing database timeout into a useful error message.

**Data flow**: It receives an open database connection and a table name. It asks Postgres for active lock holders on that table, including process ID, database user, state, transaction age, and a shortened version of the SQL query. It returns one formatted string describing the holders, or `"(no holder visible)"` if none can be seen.

**Call relations**: `bootstrap_policies` calls this only after Postgres says a table lock timed out. The returned text is included in the raised error so an operator can identify what blocked policy setup.

*Call graph*: called by 1 (bootstrap_policies); 1 external calls (fetch).


##### `_grant_serve_role`  (lines 144–157)

```
async def _grant_serve_role(connection: asyncpg.Connection) -> None
```

**Purpose**: Gives the restricted serving role the database permissions it needs for normal application work, while also cleaning up default privileges that might grant too much automatically.

**Data flow**: It receives an open database connection. It runs SQL commands that revoke certain future default table and sequence privileges from `ufo_serve`, then explicitly grants schema usage, read/write table access, and sequence usage on current public objects. It does not return a value; it changes database permissions.

**Call relations**: `ensure_serve_role` calls this after creating or updating the serving role. The function performs the permission part of role setup, while `ensure_serve_role` coordinates the wider sequence of password, timeout, grant, and database-creation steps.

*Call graph*: called by 1 (ensure_serve_role); 1 external calls (execute).


##### `_ensure_database`  (lines 160–163)

```
async def _ensure_database(connection: asyncpg.Connection, name: str, owner: str) -> None
```

**Purpose**: Creates a named Postgres database if it does not already exist. Here it is used to make sure the related DBOS database exists and is owned by the serving role.

**Data flow**: It receives an open database connection, a database name, and an owner role name. It queries Postgres to see whether the database already exists. If it is missing, it runs a create-database command with the requested owner. It returns nothing; the database server is changed only when creation is needed.

**Call relations**: `ensure_serve_role` calls this near the end of role setup. After the serving role and permissions are in place, this helper makes sure the companion database is available.

*Call graph*: called by 1 (ensure_serve_role); 2 external calls (execute, fetchval).


##### `_policy_for`  (lines 166–173)

```
async def _policy_for(connection: asyncpg.Connection, table: str) -> None
```

**Purpose**: Creates the actual workspace row-security policy for a single table. The policy says that rows are visible and writable only when their workspace column matches the current connection’s workspace setting.

**Data flow**: It receives an open database connection and a table name. It calls `_scope_column` to decide whether the policy should use `id` or `workspace_id`. It enables row-level security on the table, drops the old managed policy if present, and creates a fresh policy using the expected condition for both reading rows and writing rows. It returns nothing; it changes the table’s security rules.

**Call relations**: `bootstrap_policies` calls this only when `_conformant` says the table is missing the expected setup or has drifted from it. `_policy_for` relies on `_scope_column` to avoid creating a policy against the wrong column.

*Call graph*: calls 1 internal fn (_scope_column); called by 1 (bootstrap_policies); 1 external calls (execute).


##### `_scope_column`  (lines 176–190)

```
async def _scope_column(connection: asyncpg.Connection, table: str) -> str
```

**Purpose**: Decides which column identifies the workspace for a table. This keeps policy creation consistent and catches tables that are not safely tied to a workspace.

**Data flow**: It receives an open database connection and a table name. If the table is the `workspace` table, it returns `id`, because each row is itself a workspace. For any other table, it checks whether a `workspace_id` column exists. If the column exists, it returns `workspace_id`; if not, it raises an error saying the table is not workspace-scoped.

**Call relations**: Both `_conformant` and `_policy_for` call this before comparing or creating policies. That means the same workspace-column rule is used during the safety check and during the actual policy rewrite.

*Call graph*: called by 2 (_conformant, _policy_for); 1 external calls (fetchval).


### `core/src/ufo/blob.py`

`io_transport` · `cross-cutting storage access during request handling and background work`

Many parts of the system need to save files: workspace outputs, shared attachments, compaction records, and integration data. This file is the storage adapter for those files. It treats every stored item as a “blob”: a named pile of bytes, addressed by a slash-separated key like a file path.

The central idea is the BlobStore protocol, which is like a promise that any storage backend will know how to put, get, stream, copy, delete, and list blobs. Two concrete versions keep that promise. FilesystemBlobStore stores blobs as ordinary files under a configured root directory, which is useful for local development. S3BlobStore stores blobs in an S3 bucket, which is useful in production or cloud deployments.

The file is careful about large files. It provides streaming methods so callers do not have to load an entire file into memory at once. The filesystem version writes to a temporary file and then swaps it into place, so readers do not see half-written data. The S3 version uses multipart uploads and multipart copies for large objects, which is how S3 safely accepts or copies big files in pieces.

A key safety feature is path checking in the filesystem backend: a blob key is not allowed to escape the storage root. Without this file, every caller would need separate, error-prone code for local files versus S3, and large-file handling would be much easier to get wrong.

#### Function details

##### `BlobStore.put`  (lines 46–46)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Defines the standard operation for saving a small or already-in-memory byte value under a blob key. Callers use this when they already have the full content as bytes.

**Data flow**: The caller provides a key and bytes. A concrete blob store writes those bytes to its backing storage under that key. Nothing is returned, but the stored object should be available afterward.

**Call relations**: This is part of the shared storage contract used by code such as workspace marker creation and sample extension export. Those callers do not need to know whether the bytes end up in a local file or in S3.

*Call graph*: called by 2 (ensure_workspace_marker, export).


##### `BlobStore.put_file`  (lines 48–51)

```
async def put_file(self, key: str, source: Path) -> None
```

**Purpose**: Defines the standard operation for storing a local file without first reading the whole file into memory. It exists for large exports and attachments.

**Data flow**: The caller gives a destination key and a path to a local source file. The concrete store reads from that file and writes the content to the blob store. The result is a stored blob at the destination key.

**Call relations**: Carrier export code calls this when a local or Docker-backed workspace file needs to be saved. The actual backend decides whether that means copying into the filesystem store or uploading to S3.

*Call graph*: called by 2 (export, export).


##### `BlobStore.get`  (lines 53–53)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Defines the standard operation for reading a whole blob into memory. It is useful for small records where the caller wants all bytes at once.

**Data flow**: The caller provides a key. The concrete store finds the object, reads all its bytes, and returns them. If the key is missing, implementations raise BlobNotFound.

**Call relations**: Transcript and Slack identity code use this contract when reading stored records. They can ask for bytes without caring which storage backend is configured.

*Call graph*: called by 2 (read_compaction_record, read_identity).


##### `BlobStore.exists`  (lines 55–55)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Defines the standard operation for checking whether a blob key currently has an object. It lets callers avoid treating a missing optional file as an error.

**Data flow**: The caller provides a key. The concrete store checks its backing storage and returns true if an object exists there, false if not.

**Call relations**: Slack identity reading uses this before deciding whether stored identity data is available. The method keeps that check backend-neutral.

*Call graph*: called by 1 (read_identity).


##### `BlobStore.delete`  (lines 57–59)

```
async def delete(self, key: str) -> None
```

**Purpose**: Defines the standard operation for removing a blob. Deleting a missing key is expected to be harmless, which makes retries safe.

**Data flow**: The caller provides a key. The concrete store removes the object if it exists. It returns nothing and should not fail just because the object was already gone.

**Call relations**: This is part of the common storage interface, so cleanup code can call delete without knowing whether the backing store is local disk or S3.


##### `BlobStore.get_stream`  (lines 61–61)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines the standard operation for reading a blob piece by piece. This is for large data where loading everything into memory would be wasteful or risky.

**Data flow**: The caller provides a key. The concrete store opens the object and yields chunks of bytes over time. The caller receives each chunk until the object is fully read.

**Call relations**: This method belongs to the shared contract used by any code that needs efficient large-file reads. Backend implementations provide the actual chunking behavior.


##### `BlobStore.put_stream`  (lines 63–63)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Defines the standard operation for writing a blob from an incoming stream of byte chunks. It is the upload-side partner to get_stream.

**Data flow**: The caller provides a key and an asynchronous sequence of byte chunks. The concrete store consumes the chunks and writes them as one blob. Nothing is returned once the full stream is stored.

**Call relations**: This lets callers pass data through without first collecting it all in memory. Filesystem and S3 implementations each turn the same chunk stream into their own storage format.


##### `BlobStore.copy`  (lines 65–70)

```
async def copy(self, src_key: str, dst_key: str) -> None
```

**Purpose**: Defines the standard operation for duplicating an existing blob to a new key inside the same store. This is important because the bytes do not have to travel through the application process.

**Data flow**: The caller provides a source key and a destination key. The concrete store verifies or reads the source and creates a matching object at the destination. If the source is missing, it raises BlobNotFound.

**Call relations**: Docker and E2B carrier export paths call this when a file already exists in blob storage and needs to be promoted or shared under another key. On S3 this can happen server-side, which saves network traffic.

*Call graph*: called by 2 (export, export).


##### `BlobStore.list`  (lines 72–76)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Defines the standard operation for listing stored blobs below a required key prefix. It returns a bounded, sorted view rather than scanning the entire store.

**Data flow**: The caller provides a prefix. The concrete store finds matching objects and returns entries containing each key, size, and modification time, capped at the configured maximum.

**Call relations**: Read views that show workspace files or stored records can use this contract to enumerate a specific area of storage without depending on the backend layout.


##### `FilesystemBlobStore.put`  (lines 85–90)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves an in-memory byte value as a file under the filesystem blob root. It writes through a temporary file first so callers do not see a half-written blob.

**Data flow**: It receives a key and bytes, resolves the key to a safe path under the root, creates parent folders, writes the bytes to a uniquely named temporary file, then replaces the final file with that temp file. The visible result is an complete file at the requested key.

**Call relations**: This is the filesystem implementation of BlobStore.put. It relies on _resolve for safety and uses background threads for blocking file operations so the async event loop is not stalled.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.put_file`  (lines 92–97)

```
async def put_file(self, key: str, source: Path) -> None
```

**Purpose**: Stores an existing local file into the filesystem blob store. It copies from disk to disk without loading the whole file into Python memory.

**Data flow**: It receives a destination key and source path, turns the key into a safe destination path, creates needed folders, copies the source file to a temporary file, then atomically replaces the final destination. The result is a blob file with the source content.

**Call relations**: This fulfills BlobStore.put_file for local development storage. Export code can call the protocol method and get safe local copying when the filesystem backend is selected.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.get`  (lines 99–104)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a filesystem-backed blob fully into memory. It turns ordinary missing-file errors into the project’s BlobNotFound error.

**Data flow**: It receives a key, resolves it to a safe file path, and reads all bytes from that file. If the file does not exist, it raises BlobNotFound instead of leaking the lower-level file error.

**Call relations**: This is the filesystem implementation of BlobStore.get. It uses _resolve before reading, keeping all reads inside the configured storage root.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.exists`  (lines 106–108)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a key points to an existing file in the filesystem blob store. It is a lightweight way to test for optional stored data.

**Data flow**: It receives a key, resolves it safely under the root, checks whether that path is a file, and returns true or false.

**Call relations**: This implements BlobStore.exists for local storage. Callers using the shared interface get the same yes-or-no answer shape as they would from S3.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.delete`  (lines 110–112)

```
async def delete(self, key: str) -> None
```

**Purpose**: Removes a filesystem-backed blob if it exists. It intentionally treats an already-missing file as fine.

**Data flow**: It receives a key, resolves it to a safe path, and asks the filesystem to unlink, or remove, that file with missing files allowed. It returns nothing.

**Call relations**: This is the filesystem implementation of BlobStore.delete. It fits cleanup and retry flows because repeated deletes do not become errors.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.get_stream`  (lines 114–127)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads a filesystem-backed blob in fixed-size chunks. This protects memory use when the blob is large.

**Data flow**: It receives a key, resolves and opens the file for reading, then repeatedly reads a chunk and yields it to the caller. When there is no more data, it closes the file; if the file is missing at open time, it raises BlobNotFound.

**Call relations**: This is the local-file version of BlobStore.get_stream. It uses background threads for file reads so other asynchronous work can continue while disk I/O happens.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.put_stream`  (lines 129–142)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes a stream of byte chunks into a filesystem-backed blob. It is designed for large incoming content that should not be buffered all at once.

**Data flow**: It receives a key and an asynchronous stream of chunks, resolves a safe destination path, opens a temporary file, writes each chunk as it arrives, closes the file, and replaces the final path. If something goes wrong, it closes the file and removes the temporary file.

**Call relations**: This implements BlobStore.put_stream for local storage. The temp-file pattern mirrors put and put_file, giving callers an all-or-nothing write.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.copy`  (lines 144–152)

```
async def copy(self, src_key: str, dst_key: str) -> None
```

**Purpose**: Copies one filesystem-backed blob to another key. It checks that the source exists and writes the destination atomically.

**Data flow**: It receives source and destination keys, resolves both safely, verifies the source is a file, copies it to a temporary destination file, then replaces the final destination path. If the source is absent, it raises BlobNotFound.

**Call relations**: This is the filesystem counterpart to the shared copy operation used by carrier export flows. It uses _resolve for both keys to prevent copying outside the blob root.

*Call graph*: calls 1 internal fn (_resolve); 3 external calls (__init__, to_thread, uuid4).


##### `FilesystemBlobStore.list`  (lines 154–157)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists filesystem-backed blobs under a required prefix. The required prefix prevents accidental whole-store scans.

**Data flow**: It receives a prefix, rejects an empty one, then runs the directory walk in a background thread. It returns a tuple of BlobEntry records for matching files.

**Call relations**: This is the public list method for the filesystem backend. It delegates the actual walking and filtering work to _walk so the async caller is not blocked by filesystem traversal.

*Call graph*: 1 external calls (to_thread).


##### `FilesystemBlobStore._walk`  (lines 159–180)

```
def _walk(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Performs the actual filesystem search for list. It turns matching files into BlobEntry records with key, size, and modification time.

**Data flow**: It receives a prefix, chooses the directory area that could contain matching keys, walks through files there, skips temporary files and nonmatching paths, reads file metadata, sorts entries by key, and returns only up to the configured maximum.

**Call relations**: FilesystemBlobStore.list calls this in a background thread. It uses _resolve to choose a safe starting point and BlobEntry to return storage-neutral list results.

*Call graph*: calls 1 internal fn (_resolve); 4 external calls (__init__, fromtimestamp, walk, Path).


##### `FilesystemBlobStore._resolve`  (lines 182–187)

```
def _resolve(self, key: str) -> Path
```

**Purpose**: Turns a blob key into a real filesystem path and enforces that the path stays inside the configured root. This is the main safety guard for the local backend.

**Data flow**: It receives a key, combines it with the root directory, resolves symbolic path parts, and checks that the final path is still under the root and not the root itself. It returns the safe path or raises ValueError if the key tries to escape.

**Call relations**: Nearly every filesystem operation calls this before touching disk. It is like a gatekeeper that prevents a malicious or mistaken key such as '../secret' from reading or writing outside the blob store.

*Call graph*: called by 9 (_walk, copy, delete, exists, get, get_stream, put, put_file, put_stream).


##### `_is_missing_key`  (lines 190–191)

```
def _is_missing_key(error: ClientError) -> bool
```

**Purpose**: Recognizes S3 error responses that mean “the object is not there.” It helps convert S3’s many missing-object spellings into one project-level behavior.

**Data flow**: It receives a ClientError from the S3 library, reads the error code inside it, and returns true if that code matches one of the known missing-key codes. Otherwise it returns false.

**Call relations**: S3 get, stream, exists, and copy call this when S3 reports an error. If it says the key is missing, those methods return false or raise BlobNotFound as appropriate; otherwise they re-raise the original S3 problem.

*Call graph*: called by 4 (copy, exists, get, get_stream).


##### `S3BlobStore.put`  (lines 210–212)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves an in-memory byte value as an object in an S3 bucket. It is the S3 version of the simple put operation.

**Data flow**: It receives a key and bytes, gets or creates an S3 client for the current event loop, and sends the bytes to S3 as one object. Nothing is returned after S3 accepts the write.

**Call relations**: This implements BlobStore.put for cloud storage. It depends on _client so S3 connections are reused instead of rebuilt on every call.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.put_file`  (lines 214–246)

```
async def put_file(self, key: str, source: Path) -> None
```

**Purpose**: Uploads a local file to S3, using multipart upload for non-empty files. Multipart upload means the file is sent in pieces that S3 later assembles.

**Data flow**: It receives a key and source path, checks the file size, and gets an S3 client. Empty files are uploaded directly. Non-empty files are opened, read in fixed-size pieces, each piece is uploaded as a numbered part, and S3 is told to complete the object; if an upload fails, it is aborted and the local file handle is closed.

**Call relations**: This fulfills BlobStore.put_file for S3-backed deployments. It uses _client for the S3 connection and background thread calls for local file metadata and reads.

*Call graph*: calls 1 internal fn (_client); 1 external calls (to_thread).


##### `S3BlobStore.get`  (lines 248–258)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a whole S3 object into memory. It converts S3’s missing-object errors into BlobNotFound.

**Data flow**: It receives a key, gets an S3 client, asks S3 for the object, opens the response body, reads all bytes, and returns them. If S3 says the key is missing, it raises BlobNotFound.

**Call relations**: This is the S3 implementation of BlobStore.get. It uses _is_missing_key to distinguish an ordinary absent blob from other S3 failures.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.exists`  (lines 260–268)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether an S3 object exists without downloading its content. It uses S3 metadata lookup for a cheap yes-or-no answer.

**Data flow**: It receives a key, gets an S3 client, and asks S3 for the object headers. If S3 finds the object, it returns true; if S3 reports a missing key, it returns false; other errors are passed upward.

**Call relations**: This implements BlobStore.exists for S3. It uses _is_missing_key so callers see the same boolean behavior as they do with the filesystem backend.

*Call graph*: calls 2 internal fn (_client, _is_missing_key).


##### `S3BlobStore.delete`  (lines 270–272)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes an object from the S3 bucket. Like S3 itself, this is safe to call even if the object is already absent.

**Data flow**: It receives a key, gets an S3 client, and sends a delete request for that bucket and key. It returns nothing after the request completes.

**Call relations**: This is the S3 implementation of BlobStore.delete. It relies on _client for the reusable S3 client.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get_stream`  (lines 274–285)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads an S3 object in chunks instead of loading the whole object at once. This is the memory-safe read path for large blobs.

**Data flow**: It receives a key, gets an S3 client, asks S3 for the object, and then yields chunks from the response body. If S3 reports a missing key, it raises BlobNotFound.

**Call relations**: This implements BlobStore.get_stream for S3. It uses _is_missing_key for consistent missing-object behavior and hands chunks directly to the caller as S3 provides them.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.put_stream`  (lines 287–331)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes an incoming stream of chunks to S3. Small streams are uploaded as one object, while larger streams are uploaded in multipart form.

**Data flow**: It receives a key and an asynchronous stream of chunks, collects data until it reaches the multipart part size, starts a multipart upload when needed, uploads each part, then uploads the final remainder and completes the object. If anything fails after multipart upload has started, it aborts the unfinished upload.

**Call relations**: This is the S3 version of BlobStore.put_stream. It lets callers send large data gradually while still following S3’s rule that multipart uploads must be completed or aborted.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.copy`  (lines 333–369)

```
async def copy(self, src_key: str, dst_key: str) -> None
```

**Purpose**: Copies an object within the same S3 bucket without downloading it through the application. For very large objects, it uses multipart copy.

**Data flow**: It receives source and destination keys, checks the source object and its size, then either performs one S3 copy request for smaller objects or creates a multipart upload for the destination and asks S3 to copy byte ranges as parts. It completes the copy when all parts succeed, or aborts the destination upload on failure.

**Call relations**: Carrier export flows use the shared BlobStore.copy operation to promote existing stored files. This S3 implementation keeps the bytes inside S3, which is faster and avoids using application memory or network bandwidth for the file content.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.list`  (lines 371–388)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists S3 objects under a required prefix and returns them as BlobEntry records. It stops at a configured maximum so listing stays bounded.

**Data flow**: It receives a prefix, rejects an empty one, gets an S3 client, pages through S3 list results for that prefix, converts each object’s key, size, and modified time into BlobEntry, and returns up to the maximum number of entries.

**Call relations**: This implements BlobStore.list for S3. It uses S3 pagination because S3 returns large listings in pages, and it returns the same neutral BlobEntry shape as the filesystem backend.

*Call graph*: calls 1 internal fn (_client); 1 external calls (__init__).


##### `S3BlobStore._client`  (lines 390–406)

```
async def _client(self) -> AioBaseClient
```

**Purpose**: Provides a reusable S3 client for the currently running async event loop. This avoids the cost and problems of creating a new client for every blob operation.

**Data flow**: It checks which event loop is running, looks for an existing client tied to that loop, and returns it if present. If not, it creates an S3 client with the configured endpoint and region, stores it in the per-loop cache, and returns it; if another task already stored one first, it closes the redundant client and logs if closing fails.

**Call relations**: Every S3BlobStore operation calls this before talking to S3. The per-loop cache matters because the underlying network client is tied to the async loop that created it, much like a tool that only works at the workbench where it was assembled.

*Call graph*: called by 9 (copy, delete, exists, get, get_stream, list, put, put_file, put_stream); 3 external calls (get_session, get_running_loop, log).


##### `blob_store_for`  (lines 409–421)

```
def blob_store_for(config: BlobConfig) -> FilesystemBlobStore | S3BlobStore
```

**Purpose**: Builds the correct blob store from configuration. It is the small factory that turns settings into either local filesystem storage or S3 storage.

**Data flow**: It receives a BlobConfig, checks the selected backend, validates that the required fields are present, and returns a FilesystemBlobStore or S3BlobStore with the configured values. If required configuration is missing, it raises ValueError.

**Call relations**: Startup or setup code can call this once it has loaded configuration. After this function returns, the rest of the system can depend on the BlobStore-style operations without branching on the backend.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/db.py`

`io_transport` · `startup, request handling, background jobs, teardown`

This file protects one of the most important boundaries in the system: which workspace’s data a piece of code is allowed to see. A workspace is like a tenant or account area. The project may serve many workspaces from one running process, so every normal database transaction must be tied to the workspace currently being handled.

The main path is `workspace_tx`. Code enters it when it wants to read or write normal workspace data. If the database is PostgreSQL, it writes the current workspace id into a transaction-local database setting called `app.workspace_id`. PostgreSQL row-level security, often shortened to RLS, means the database itself checks that setting before allowing rows to be seen. If no workspace was set, the system is designed to fail closed instead of accidentally showing another workspace’s data.

There is one deliberate exception: `owner_tx`. It is for background sweeps that must first find work across all workspaces. Callers are expected to use it only to find identifiers, then switch back into the correct workspace before reading real row contents.

The file also creates engines carefully so they are safe across multiple asynchronous event loops, configures SQLite to behave better under concurrent writes, applies Alembic database migrations, and disposes engines during shutdown.

#### Function details

##### `_build_engine`  (lines 41–52)

```
def _build_engine(url: str) -> AsyncEngine
```

**Purpose**: Creates an asynchronous database engine from a database URL and prepares it before anyone else uses it. It also adds special setup rules when the database is SQLite.

**Data flow**: It takes a database URL as input. It creates an async SQLAlchemy engine without a connection pool, adds SQLite connection behavior if needed, forces the engine to make its first connection once, and returns the ready-to-use engine.

**Call relations**: This is the shared builder used by both `init_db` and `init_owner_db`. Before handing the engine back, it calls `_first_connect` so later code does not trip over first-use setup while multiple event loops are active.

*Call graph*: calls 1 internal fn (_first_connect); called by 2 (init_db, init_owner_db); 1 external calls (create_async_engine).


##### `init_db`  (lines 55–59)

```
def init_db(url: str) -> None
```

**Purpose**: Sets up the normal database engine used for workspace-scoped work. This should happen once at application startup.

**Data flow**: It receives a database URL. If the normal engine already exists, it raises an error to prevent accidental re-initialization; otherwise it builds the engine and stores it in the module for later transactions.

**Call relations**: Startup code calls this before `workspace_tx` or, in some deployments, `owner_tx` can be used. It delegates the actual engine creation to `_build_engine`.

*Call graph*: calls 1 internal fn (_build_engine).


##### `init_owner_db`  (lines 62–71)

```
def init_owner_db(url: str) -> None
```

**Purpose**: Sets up a separate owner database engine that can read across workspaces for carefully limited background tasks. This is the escape hatch for cross-workspace enumeration, not normal data access.

**Data flow**: It receives an owner-role database URL. If an owner engine already exists, it raises an error; otherwise it builds that engine and stores it for later use by `owner_tx`.

**Call relations**: Service startup calls this when the deployment has a privileged owner connection. `owner_tx` later chooses this engine when it needs the cross-workspace path.

*Call graph*: calls 1 internal fn (_build_engine).


##### `_first_connect`  (lines 74–94)

```
def _first_connect(engine: AsyncEngine) -> None
```

**Purpose**: Forces a newly created engine to complete its first real database connection immediately. This avoids a subtle deadlock risk when the same engine is later used from different asynchronous event loops.

**Data flow**: It receives an async engine. It starts a fresh thread, opens and closes one database connection there, waits for the thread to finish, and re-raises any error that happened inside the thread.

**Call relations**: `_build_engine` calls this before publishing the engine to the rest of the program. The helper’s inner `run` function does the actual event-loop work in the new thread.

*Call graph*: called by 1 (_build_engine); 1 external calls (Thread).


##### `_first_connect.run`  (lines 81–88)

```
def run() -> None
```

**Purpose**: Performs the first database connection inside a brand-new event loop on a separate thread. This isolates first-use database setup from any event loop the caller might already be running.

**Data flow**: It creates a new asyncio event loop, runs `_open_and_close` with the engine, records any exception in a shared list, and closes the loop afterward.

**Call relations**: This is the thread target created by `_first_connect`. It hands the actual open-and-close operation to `_open_and_close` so the engine’s first connection is completed safely.

*Call graph*: calls 1 internal fn (_open_and_close); 1 external calls (new_event_loop).


##### `_open_and_close`  (lines 97–99)

```
async def _open_and_close(engine: AsyncEngine) -> None
```

**Purpose**: Opens one connection from an engine and immediately closes it. Its job is to trigger SQLAlchemy and database-driver first-time setup.

**Data flow**: It receives an async engine, enters a connection context, does no database work, and exits the context so the connection is closed.

**Call relations**: `_first_connect.run` calls this inside its private event loop. It is intentionally small because its only purpose is to make the engine finish first-connect initialization.

*Call graph*: called by 1 (run); 1 external calls (connect).


##### `dispose_db`  (lines 102–109)

```
async def dispose_db() -> None
```

**Purpose**: Shuts down any database engines created by this module. This is used during teardown so database resources are not left open.

**Data flow**: It reads the stored normal and owner engines. For each one that exists, it disposes it and then clears the stored reference back to `None`.

**Call relations**: Shutdown or test cleanup code calls this after database use is finished. It reverses the setup done by `init_db` and `init_owner_db`.


##### `workspace_tx`  (lines 113–123)

```
async def workspace_tx() -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens a normal database transaction for the currently active workspace. This is the safe default path for reading or writing workspace data.

**Data flow**: It checks that the normal engine exists, opens a transaction, reads the current workspace id from a context variable, and, on PostgreSQL, stores that id in the transaction’s `app.workspace_id` setting. It then yields the connection to the caller and commits or rolls back when the caller leaves the context.

**Call relations**: Request handlers, jobs, or turns use this after some outer boundary has set `current_workspace`. It uses SQL text to set the PostgreSQL transaction setting that row-level security policies rely on.

*Call graph*: 1 external calls (text).


##### `owner_tx`  (lines 127–140)

```
async def owner_tx() -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens the one allowed cross-workspace transaction path. It is meant for finding work across workspaces, not for reading tenant data as if it were ordinary scoped data.

**Data flow**: It chooses the owner engine if one was initialized, otherwise falls back to the normal engine. It opens a transaction without setting any workspace id and yields the connection to the caller.

**Call relations**: Background sweep code uses this to enumerate rows across workspaces, then should re-enter the correct workspace before doing real per-workspace work. It deliberately does not call `workspace_tx` and does not set the workspace database setting.


##### `apply_migrations`  (lines 143–168)

```
def apply_migrations(url: str, pack: str | None=None) -> None
```

**Purpose**: Runs database schema migrations so the database has the tables and columns expected by the code. It includes both core migrations and migrations from active extensions.

**Data flow**: It receives a database URL and optionally a pack name. It builds an Alembic configuration, gathers migration folders, checks for duplicate migration revision ids, and upgrades the database to all current migration heads.

**Call relations**: Command-line startup, deployment setup, or tests call this before normal database use. It asks the extension loader for migration locations, validates the migration graph with Alembic, and then hands off to Alembic’s upgrade command.

*Call graph*: 6 external calls (__init__, upgrade, from_config, migration_locations, catch_warnings, simplefilter).


##### `_sqlite_on_connect`  (lines 171–177)

```
def _sqlite_on_connect(dbapi_connection: Any, _connection_record: Any) -> None
```

**Purpose**: Applies SQLite-specific settings whenever a SQLite connection is opened. These settings make SQLite safer and smoother for this application’s transaction style.

**Data flow**: It receives a low-level SQLite connection. It disables the driver’s automatic transaction behavior, turns on write-ahead logging, enforces foreign keys, sets a busy timeout, and then closes the temporary cursor used for setup.

**Call relations**: `_build_engine` registers this as a SQLite connection hook. SQLAlchemy calls it whenever a new SQLite database connection is made.


##### `_sqlite_begin_immediate`  (lines 180–182)

```
def _sqlite_begin_immediate(connection: sa.Connection) -> None
```

**Purpose**: Starts SQLite transactions with `begin immediate`, which claims the writer slot at the start. This turns some write conflicts into waiting in line instead of deadlocking later.

**Data flow**: It receives a SQLAlchemy connection and sends the raw SQL command `begin immediate` to the database. Nothing is returned; the connection’s transaction state is changed.

**Call relations**: `_build_engine` registers this as SQLite’s begin hook. SQLAlchemy calls it when a SQLite transaction begins, so SQLite write locking follows the application’s safer pattern.

*Call graph*: 1 external calls (exec_driver_sql).


### Core schema model
Collects the shared schema package, turn record models, stable IDs, and database table blueprint used across the system.

### `core/src/ufo/schema/__init__.py`

`data_model` · `import/package setup`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package. Here, it means other code can refer to the schema area using imports such as `ufo.schema`. Think of it like a labeled folder in a filing cabinet: the label matters even if the folder’s front page is blank. Without this file, depending on the Python version and packaging setup, importing schema modules from this directory could be less predictable or fail in some environments. Because it has no functions, classes, or variables, it does not transform data or perform work at runtime. Its value is structural: it reserves a clean namespace where schema definitions can live.


### `core/src/ufo/schema/records.py`

`data_model` · `cross-cutting`

This file is a common dictionary for the system. A “turn” is one unit of work in a conversation: someone says something, the assistant works on it, and the turn eventually finishes, fails, is cancelled, or pauses to ask for more information. Without these shared records, the surface that receives a message and the worker that processes it could disagree about field names, statuses, timestamps, or what counts as a finished result.

Most of the file is made of Pydantic models. Pydantic is a validation library: it turns incoming data into structured Python objects and checks that the data makes sense. The file defines statuses, constants, usage counts, user-question shapes, credential requests, account-connection requests, terminal results, agents, and proposed agent changes.

The central record is `Turn`. It carries IDs, workspace and conversation links, the incoming message, timing, who admitted the turn, optional context, and an optional final `TerminalFrame`. A key rule is enforced: unfinished turns must not have a terminal frame, and finished turns must have one whose status matches the turn.

The file also protects boundary data. Sender names are flattened so they cannot fake markup-like tags, timezones must be real IANA timezone names, and database timestamps that come back without timezone information are treated as UTC. Stable UUIDs are generated from business facts, so retries and workflow replays refer to the same logical turn or billing entry instead of creating duplicates.

#### Function details

##### `turn_id_for`  (lines 42–44)

```
def turn_id_for(workspace_id: UUID, conversation_id: UUID, seq: int) -> UUID
```

**Purpose**: Creates the stable ID for a turn from its workspace, conversation, and sequence number. This matters because the turn ID is also used as the workflow ID, so retrying or replaying the same turn finds the same identity instead of inventing a new one.

**Data flow**: It receives a workspace ID, a conversation ID, and a turn sequence number. It combines them into one text key and feeds that key into UUID version 5, which creates the same UUID every time for the same input. The result is a deterministic turn UUID.

**Call relations**: When code admits or schedules a new turn, it can call this helper before storing or running the turn. The helper hands off the actual UUID creation to `uuid.uuid5`, using a fixed namespace so the same conversation position always maps to the same turn ID.

*Call graph*: 1 external calls (uuid5).


##### `ledger_id_for`  (lines 47–52)

```
def ledger_id_for(workspace_id: UUID, turn_id: UUID, dimension: str, attempt: str='') -> UUID
```

**Purpose**: Creates a stable billing-ledger ID for one kind of usage on one turn and one run attempt. This prevents replayed work from writing duplicate billing rows, while still allowing resumed attempts to be billed separately when they really spend more tokens.

**Data flow**: It receives the workspace ID, turn ID, billing dimension, and optionally an attempt ID. It builds a text key from those pieces and turns it into a deterministic UUID. The output is the ledger-row ID for that exact billing slice.

**Call relations**: Code that records token or cost usage can call this before writing billing data. Like `turn_id_for`, it delegates UUID generation to `uuid.uuid5`, making billing writes repeat-safe for the same attempt.

*Call graph*: 1 external calls (uuid5).


##### `TurnContext._tag_safe_line`  (lines 163–167)

```
def _tag_safe_line(cls, value: str | None) -> str | None
```

**Purpose**: Cleans the sender name before it is stored in turn context. It removes angle brackets and collapses the value to one plain line so a sender name cannot pretend to be a structured tag when later rendered into the assistant context.

**Data flow**: It receives the optional sender string from outside the system. If the value is missing, it stays missing. Otherwise, the function removes `<` and `>`, splits and rejoins whitespace into a single clean line, and returns either that cleaned text or `None` if nothing useful remains.

**Call relations**: Pydantic calls this automatically when a `TurnContext` is created or validated. It runs at the boundary where outside, surface-reported text enters the shared record, before that context can be used by the engine.


##### `TurnContext._known_zone`  (lines 171–178)

```
def _known_zone(cls, value: str | None) -> str | None
```

**Purpose**: Checks that a supplied timezone name is real before the turn is accepted. This keeps a bad timezone from causing a surprise failure later while the assistant is already working.

**Data flow**: It receives an optional timezone string. If no timezone was supplied, it returns that empty value unchanged. If a value exists, it tries to load it as a known timezone; success returns the original string, while failure becomes a validation error saying the timezone is unknown.

**Call relations**: Pydantic calls this automatically while building `TurnContext`. It uses Python’s `zoneinfo.ZoneInfo` lookup as the source of truth for valid timezone names, so invalid surface input is rejected early.

*Call graph*: 1 external calls (ZoneInfo).


##### `Turn._aware_utc`  (lines 202–207)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Ensures turn timestamps always carry timezone information. This is important because some database drivers can return UTC timestamps without the UTC marker, and later time conversion could otherwise mistake them for local time.

**Data flow**: It receives either `created_at`, `updated_at`, or `None`. Missing timestamps pass through unchanged. A timestamp that already has timezone information is returned as-is; a timestamp without timezone information is marked as UTC and then returned.

**Call relations**: Pydantic calls this validator automatically for `Turn.created_at` and `Turn.updated_at`. When it needs to repair a timezone-less timestamp, it uses `datetime.replace` to add the UTC marker without changing the clock time.

*Call graph*: 1 external calls (replace).


##### `Turn._terminal_matches_status`  (lines 210–215)

```
def _terminal_matches_status(self) -> 'Turn'
```

**Purpose**: Enforces the rule that a turn’s final-result record and its status must agree. A running, queued, or parked turn cannot have a final frame, and a done, failed, or cancelled turn must have one.

**Data flow**: It reads the completed `Turn` object after its fields have been validated. It compares the turn status with whether `terminal` is present, and if a terminal frame exists it also checks that `terminal.status` is the same as `Turn.status`. If the rule holds, it returns the same turn; otherwise it raises a validation error.

**Call relations**: Pydantic runs this after constructing a `Turn`. It acts as a final consistency gate before the record is accepted by surfaces, workers, storage, or any other code that relies on the turn’s lifecycle state.


### `core/src/ufo/schema/tables.py`

`data_model` · `database setup, migrations, and any code that builds database queries`

This file is like the floor plan for the system’s database. It does not store data itself and it does not run business logic. Instead, it tells SQLAlchemy, a Python library for describing and talking to databases, what tables exist and what each table is allowed to contain.

The central object is `metadata`, which is the container holding the whole schema. Each `sa.Table` then adds one table to that container. The tables describe the main parts of the product: workspaces, members, agents, conversations, turns in a conversation, incoming messages, billing ledger entries, spending caps, credentials, grants, proposals, writebacks, shared files, extension storage, runtime instances, synced sources, scheduled tasks, and indexed pages.

The file also records how records connect. For example, members belong to workspaces, conversations belong to agents and workspaces, and turns belong to conversations. These links are written as foreign keys, which are database rules that prevent dangling references.

Many tables include constraints, which are guardrails enforced by the database. They keep values sensible, such as requiring positive seat limits, known turn statuses, nonempty installation IDs, and valid spending limits. Indexes are also declared here so common lookups, like pending work or conversation activity, can be found quickly.

Without this file, the rest of the system would not have one reliable definition of its data shape. Different environments could drift apart, and invalid records could enter the database more easily.


### Index and graph migrations
Adds extension-owned persistence for searchable chunks and knowledge graph entities and relationships.

### `extensions/index_default/migrations/0001_chunk.py`

`data_model` · `database migration/setup`

This file is like the construction plan for a new library shelf in the database. The system needs a place to store small pieces of text, called chunks, along with information about where each chunk came from and optional machine-learning data called an embedding, which represents the meaning of the text as numbers. Without this migration, the index extension would have nowhere reliable to save or search those chunks.

The file supports two database engines. If the database is PostgreSQL, it creates a `chunk` table with normal text fields, a generated full-text-search column, and a vector embedding column using PostgreSQL's `vector` extension. It then adds indexes so searches by subject, keyword text, and embedding similarity can be fast. If the database is not PostgreSQL, it uses a simpler table shape suitable for SQLite: embeddings are stored as raw binary data, and a separate SQLite full-text-search virtual table is created for searching chunk text.

The important idea is that the same application feature can run on different databases, but each database needs slightly different building blocks. The `upgrade` function builds those blocks, and the `downgrade` function removes them in the reverse direction.

#### Function details

##### `upgrade`  (lines 31–51)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `chunk` storage and search indexes. It chooses the right database-specific layout depending on whether the current database is PostgreSQL or another supported database such as SQLite.

**Data flow**: It reads the active database connection to find the database type. If the database is PostgreSQL, it runs raw SQL to enable vector support, create the chunk table, and add full-text and embedding indexes, then creates a subject index. Otherwise, it builds the chunk table through Alembic and SQLAlchemy helpers, adds the subject index, and creates a SQLite full-text-search table. The result is a database ready to store and search indexed text chunks.

**Call relations**: Alembic calls this function when the project is moved forward to this migration version. Inside that migration step, it asks Alembic for the current connection, then hands the actual database changes to Alembic operations such as executing SQL, creating tables, and creating indexes.

*Call graph*: 9 external calls (create_index, create_table, execute, get_bind, Column, Integer, LargeBinary, PrimaryKeyConstraint, Text).


##### `downgrade`  (lines 54–60)

```
def downgrade() -> None
```

**Purpose**: This function rolls the migration back by removing the database objects created for chunk indexing. It is used when the database schema needs to move back to the version before this extension created its storage.

**Data flow**: It reads the active database connection to find the database type. For PostgreSQL, it drops the `chunk` table, which also removes the related table-owned structures. For other databases, it drops the SQLite full-text-search table, removes the subject index, and then drops the main `chunk` table. After it runs, the chunk indexing storage from this migration is gone.

**Call relations**: Alembic calls this function when reversing this migration. The function again uses Alembic's database-operation helpers, but this time it sends drop commands instead of create commands so the schema is taken apart cleanly.

*Call graph*: 4 external calls (drop_index, drop_table, execute, get_bind).


### `extensions/index_default/migrations/0002_chunk_workspace_id.py`

`data_model` · `database migration`

This migration updates the database table named `chunk`. Before this change, a chunk was identified only by its `chunk_digest`, which is like using only a fingerprint as its address. That works if there is only one shared space, but it becomes unsafe when the system has multiple workspaces: two workspaces might need to store or refer to chunks independently. This file adds a `workspace_id` column and changes the primary key, which is the database’s rule for what makes a row unique, so a chunk is now uniquely identified by both `workspace_id` and `chunk_digest` together.

The file is written for Alembic, a tool that applies database changes in order. It defines an `upgrade` path for moving forward and a `downgrade` path for undoing the change. Both paths only run on PostgreSQL databases. If the connected database is not PostgreSQL, the migration does nothing. That matters because the raw SQL statements here are written for PostgreSQL and may not work on other database engines.

On upgrade, it removes the old primary key, adds the required workspace column, and creates the new combined primary key. On downgrade, it reverses those steps by removing workspace scoping and returning to the old single-column key.

#### Function details

##### `upgrade`  (lines 22–26)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds workspace scoping to the `chunk` table so chunks are keyed by both workspace and digest.

**Data flow**: It first asks Alembic for the current database connection and reads what kind of database is being used. If it is not PostgreSQL, it stops without changing anything. If it is PostgreSQL, it sends each SQL statement in the upgrade list to the database: drop the old primary key, add the required `workspace_id` column, then create the new combined primary key.

**Call relations**: Alembic calls this function when the migration is applied during an upgrade. Inside the function, it relies on Alembic’s database connection lookup to decide whether the migration is safe to run, then hands each schema-changing SQL command to Alembic for execution.

*Call graph*: 2 external calls (execute, get_bind).


##### `downgrade`  (lines 29–33)

```
def downgrade() -> None
```

**Purpose**: This function undoes the database change made by `upgrade`. It removes workspace scoping from the `chunk` table and restores the old digest-only primary key.

**Data flow**: It asks Alembic for the active database connection and checks the database type. If the database is not PostgreSQL, it exits without doing anything. If it is PostgreSQL, it runs the rollback SQL statements in order: drop the combined primary key, remove the `workspace_id` column, then recreate the old primary key using only `chunk_digest`.

**Call relations**: Alembic calls this function when rolling this migration back. Like `upgrade`, it uses Alembic to inspect the database connection first, then sends each rollback statement to Alembic so the database schema is changed back in the correct order.

*Call graph*: 2 external calls (execute, get_bind).


### `extensions/knowledge_graph/ufo_ext_knowledge_graph/migrations/knowledge_graph_0001_graph.py`

`data_model` · `database migration during install, upgrade, or rollback`

This file is a database migration, which is a scripted change to the database structure. Its job is to add the basic tables needed for a knowledge graph: one table for things the system knows about, and one table for links between those things. Without this file, the knowledge graph feature would have nowhere reliable to store its extracted people, companies, topics, or relationships.

The `graph_entity` table stores each known item. It records which workspace it belongs to, whether it is shared or tied to a specific member, its name, a normalized version of the name for lookup, its type, and timestamps. It also uses database checks to make sure only allowed entity types are stored.

The `graph_edge` table stores relationships between two entities, like “works at” or “founded.” Each edge points to a source entity and a target entity, records where the relationship came from, and includes a confidence score. A `tombstone` flag lets the system mark a relationship as no longer active without immediately deleting the row.

Indexes are added so common searches are fast, like finding an entity by name or finding all edges connected to an entity. The downgrade path reverses all of this, like carefully disassembling shelves before removing the room they were installed in.

#### Function details

##### `upgrade`  (lines 12–66)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the knowledge graph tables and their indexes. It is used when the database is being moved forward to support the knowledge graph extension.

**Data flow**: It takes no direct input from application code; Alembic, the migration tool, runs it in the context of a database connection. It defines the `graph_entity` table, the `graph_edge` table, their columns, rules, foreign-key links, and indexes. After it finishes, the database can store graph entities and relationships in a structured way.

**Call relations**: When this migration revision is applied, Alembic calls `upgrade`. The function then hands table and index definitions to Alembic operations such as creating tables and indexes, while SQLAlchemy objects describe column types, constraints, and relationships to existing tables like `workspace`.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 69–75)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the indexes and tables created by `upgrade`. It is used when rolling the database back to a state before the knowledge graph tables existed.

**Data flow**: It takes no direct input from application code; Alembic runs it during rollback. It first removes indexes that depend on the tables, then drops the edge table, then removes the entity lookup index and entity table. After it finishes, the database no longer contains this knowledge graph schema.

**Call relations**: When this migration revision is rolled back, Alembic calls `downgrade`. The function uses Alembic drop operations in the safe reverse order of creation, removing dependent pieces like edge indexes and the edge table before removing the entity table they rely on.

*Call graph*: 2 external calls (drop_index, drop_table).


### Memory migrations
Evolves memory extension storage from the initial memory table through pages, workspace links, indexes, timestamps, and provenance.

### `extensions/memory/ufo_ext_memory/migrations/0001_memory.py`

`data_model` · `database migration/setup`

This is a database migration, which is a small script that changes the shape of the database in a controlled way. Here, it adds a new table called `memory_item`. Think of it like adding a new filing cabinet to the system: each drawer entry has an ID, belongs to a workspace, has a subject, contains text, and is labeled with what kind of memory it is.

The table stores memory text in two main fields: `subject`, which says who or what the memory is about, and `body`, which contains the actual remembered content. Each item is tied to a workspace, and the foreign key rule says that if a workspace is deleted, its memory items are deleted too. That prevents orphaned memory records from being left behind.

The file also adds guardrails. `item_class` must be one of `fact`, `episodic`, or `semantic`, so the database rejects unknown memory types. `subject` must either be `shared` or start with `member:`, which keeps memory ownership in a predictable format. The `embedding_digest` and `embedding_claimed_at` fields support later processing for embeddings, which are machine-readable representations of text. An index on `embedding_digest` helps the system quickly find memory items that need that processing.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `memory_item` table and its lookup index. It is used when the system is moving forward to a database version that supports the memory extension.

**Data flow**: Before it runs, the database has no `memory_item` table from this migration. The function sends table-building instructions to Alembic, the database migration tool: create columns for IDs, workspace ownership, memory text, memory type, embedding status, replacement tracking, and timestamps; add rules that enforce valid values; then create an index for faster searches by `embedding_digest`. After it finishes, the database can store memory records in the expected format.

**Call relations**: During an upgrade, Alembic calls this function as part of applying the memory extension's first migration. The function hands the actual database work to Alembic operations such as creating a table and index, while SQLAlchemy objects describe the columns and constraints in a database-independent way.

*Call graph*: 9 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the index and then deleting the `memory_item` table. It is used if the database must be rolled back to a version before this memory feature existed.

**Data flow**: Before it runs, the database contains the `memory_item` table and its `memory_item_due` index. The function first removes the index, then removes the table itself. After it finishes, the database no longer has the storage created by this migration, and any data in that table would be gone.

**Call relations**: During a rollback, Alembic calls this function instead of `upgrade`. It gives Alembic two cleanup instructions in the safe order: remove the index that depends on the table, then remove the table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0002_mem_page.py`

`data_model` · `database migration during install, upgrade, or rollback`

This file is part of the project’s database change history. A database migration is like a dated instruction card for changing the shape of the database safely and in order. Here, the change adds a new table called `mem_page`, which appears to store memory “pages”: each page has a unique ID, a text subject, and a creation time.

The file matters because the application code can only store and read these memory pages if the database has the right table. Without this migration, a fresh installation or upgraded system would not know to create `mem_page`, and any feature that expects that table would fail when it tried to use it.

The migration uses Alembic, a tool that applies database changes step by step. The `revision` and `down_revision` values place this change after the earlier `memory_0001` migration. The `upgrade` function is the forward step: it creates the table and its columns. The `downgrade` function is the reverse step: it drops the table, undoing this migration. That rollback is useful during development, testing, or controlled reversions, but it would also delete the stored page data in that table.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Creates the `mem_page` database table so the memory extension has a place to store page records. Each record gets a unique `page_id`, a required text `subject`, and a required timestamp showing when it was created.

**Data flow**: Before this runs, the database is expected not to have the `mem_page` table from this migration. The function sends Alembic a table definition made from SQLAlchemy column objects: one UUID identifier, one text field, one timezone-aware date-time field, and a primary key rule on `page_id`. After it runs successfully, the database contains the new table ready for use.

**Call relations**: Alembic calls this function when applying revision `memory_0002` after `memory_0001`. Inside, it hands the table blueprint to `alembic.op.create_table`, using SQLAlchemy helpers to describe the column types and the primary key.

*Call graph*: 6 external calls (create_table, Column, DateTime, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `mem_page` table when this migration is rolled back. This is the undo step for the table created by `upgrade`.

**Data flow**: Before this runs, the database may contain the `mem_page` table created by the upgrade step. The function tells Alembic to drop that table. After it runs, the table is gone, along with any data that was stored in it.

**Call relations**: Alembic calls this function when rolling the database back from revision `memory_0002` to the previous memory migration. It delegates the actual removal to `alembic.op.drop_table`.

*Call graph*: 1 external calls (drop_table).


### `extensions/memory/ufo_ext_memory/migrations/0003_memory_kind.py`

`data_model` · `database migration during upgrade or rollback`

This file is an Alembic migration, which is a small script used to move the database structure from one version to the next. Here, the project is changing the `memory_item` table so each saved memory can carry two extra fields. `memory_kind` says what sort of memory it is, and it defaults to `fact` so old rows still have a sensible value. `confidence` stores a whole-number confidence score, defaulting to `5`, again so existing data is not left blank.

The important problem this solves is compatibility. A live database may already contain many `memory_item` rows. Adding required columns without defaults would break because old rows would not have values for them. This migration avoids that by making both columns required but giving the database default values to fill in automatically.

The file also provides the reverse operation. If the system needs to move back to the previous database version, it removes the two added columns. Think of it like adding two labeled drawers to a filing cabinet during an upgrade, and removing those drawers again during a rollback.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: This function moves the database schema forward by adding `memory_kind` and `confidence` to the `memory_item` table. It is used when installing or upgrading to this migration version.

**Data flow**: It starts with the existing `memory_item` table. It asks Alembic, the database migration tool, to add a text column named `memory_kind` with the default value `fact`, then an integer column named `confidence` with the default value `5`. After it runs, every memory row can store those two extra values, and existing rows receive safe defaults.

**Call relations**: During an upgrade, Alembic calls this function as the step for revision `memory_0003`. The function hands the actual table-changing work to `alembic.op.add_column`, using SQLAlchemy column definitions to describe exactly what should be added.

*Call graph*: 4 external calls (add_column, Column, Integer, Text).


##### `downgrade`  (lines 23–25)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the two columns that `upgrade` added. It is used when rolling the database back to the previous version.

**Data flow**: It starts with a `memory_item` table that includes `confidence` and `memory_kind`. It tells Alembic to drop `confidence` first and then `memory_kind`. After it runs, the table returns to the older shape and no longer stores those values.

**Call relations**: During a rollback from revision `memory_0003`, Alembic calls this function. The function delegates the actual database changes to `alembic.op.drop_column`, which removes the named columns from the table.

*Call graph*: 1 external calls (drop_column).


### `extensions/memory/ufo_ext_memory/migrations/0004_mem_page_workspace.py`

`io_transport` · `database migration`

This migration changes the shape of the database for the memory extension. Before this change, a `mem_page` was connected to a page, and the page had a workspace, but the memory page did not store that workspace directly. This file adds that missing direct link.

The upgrade works carefully in stages, like adding a new required field to a paper form without throwing away old forms. First it adds a new `workspace_id` column that is allowed to be empty. Then it fills that column for existing memory pages by looking up the workspace from the related `page` row. Only after the old data has been filled in does it make the column required. Finally, it adds a foreign key, which is a database rule saying that each `workspace_id` must point to a real workspace. The rule also says that if a workspace is deleted, its memory page rows should be deleted too.

The downgrade reverses this by removing the database rule and then removing the column. This matters because migrations need to be reversible when possible, so developers can move the database backward during testing or rollback.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: This function moves the database forward to the new schema. It adds `workspace_id` to `mem_page`, fills it for existing rows, makes it required, and connects it to the `workspace` table with a database rule.

**Data flow**: It starts with the existing `mem_page` table, where rows only know their related page. It adds a temporary nullable `workspace_id`, copies each value from the matching `page` row, then tightens the column so future rows must have a workspace. The result is a `mem_page` table where every row belongs directly to a valid workspace, and deleting a workspace also deletes its memory pages.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside it, the function asks Alembic to add the column, run a SQL update to backfill old data, and then alter the table in a safe batch operation so the new required rule and foreign key are put in place.

*Call graph*: 5 external calls (add_column, batch_alter_table, execute, Column, Uuid).


##### `downgrade`  (lines 29–32)

```
def downgrade() -> None
```

**Purpose**: This function moves the database back to the previous schema. It removes the workspace foreign key rule and then deletes the `workspace_id` column from `mem_page`.

**Data flow**: It starts with a `mem_page` table that has a required `workspace_id` linked to the `workspace` table. It first removes that link rule, then removes the column itself. The result is the older table shape, where `mem_page` no longer stores a direct workspace reference.

**Call relations**: Alembic calls this function when rolling this migration back. It uses a batch table change so the constraint and column can be removed cleanly as one database schema change.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0005_consolidate_index.py`

`data_model` · `database migration`

This file is a small Alembic migration. Alembic is the tool used here to apply controlled changes to the database structure over time, like adding or removing indexes. The problem it solves is speed: the memory system has an hourly sweep that looks for live memory facts that are old enough to be candidates for consolidation. An index is like a sorted card catalog in a library. Instead of walking every shelf, the database can jump straight to the likely records.

The migration creates a partial index on the `memory_item` table. “Partial” means it only covers rows matching a condition: items whose class is `fact` and that have not been replaced by another item, shown by `superseded_by is null`. The index is organized by `workspace_id` and `created_at`, which fits the likely search pattern: look within a workspace, then find older records by creation time.

The file also includes the reverse operation. If the migration is rolled back, it removes the index. No application data is changed directly; only the database’s lookup structure is added or removed.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by creating an index for faster lookup of live fact records in `memory_item`. This is used when moving the database schema forward to version `memory_0005`.

**Data flow**: It takes no direct input from the caller. It tells Alembic to create an index named `memory_item_consolidate` on the `memory_item` table, using the `workspace_id` and `created_at` columns, but only for rows where `item_class` is `fact` and `superseded_by` is empty. The result is a database with a new lookup aid; no rows are inserted, deleted, or edited.

**Call relations**: When Alembic runs this migration during an upgrade, it calls `upgrade`. Inside, the function asks SQLAlchemy to express the filter condition as SQL text, then hands everything to Alembic’s `create_index` operation so the database can build the index.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the consolidation index. This is used if the database schema needs to be rolled back from this version.

**Data flow**: It takes no direct input from the caller. It tells Alembic to drop the index named `memory_item_consolidate` from the `memory_item` table. After it runs, the database no longer has that special shortcut for finding consolidation candidates.

**Call relations**: When Alembic rolls this migration backward, it calls `downgrade`. The function delegates the actual database change to Alembic’s `drop_index` operation.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0006_inventory_index.py`

`data_model` · `database migration during deployment or upgrade`

This file is an Alembic migration, which means it is a small, versioned database change that can be applied or undone. Its job is to add a database index to the `memory_item` table. An index is like the index at the back of a book: instead of reading every page to find entries for one topic, the database can jump straight to the right section.

The problem being solved is specific. The operator inventory explorer reads memory items for a single workspace and wants them ordered by creation time. It also reads all classes of memory items, including older or superseded rows. An existing partial index only helps with a narrower case, so it cannot speed up this explorer query.

The `upgrade` step creates a new non-partial index named `memory_item_inventory` over `workspace_id` and `created_at`. That lets the database first narrow results to one workspace, then read them in creation-time order. This keeps page-sized reads bounded and predictable.

The `downgrade` step removes the same index, so the migration can be rolled back cleanly if needed.

#### Function details

##### `upgrade`  (lines 17–18)

```
def upgrade() -> None
```

**Purpose**: Applies the migration by creating the index that speeds up workspace-specific inventory reads ordered by creation time. This is used when moving the database schema forward to this version.

**Data flow**: It takes no direct input from application code. When Alembic runs this migration, the function asks the database to add an index named `memory_item_inventory` to the `memory_item` table using the `workspace_id` and `created_at` columns. After it finishes, future explorer queries can use that index instead of scanning the whole table.

**Call relations**: Alembic calls this function when applying the migration. Inside it, the function hands the actual database work to `alembic.op.create_index`, which sends the appropriate schema-change command to the database.

*Call graph*: 1 external calls (create_index).


##### `downgrade`  (lines 21–22)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the inventory index. This is used if the database schema needs to be rolled back to the previous version.

**Data flow**: It takes no direct input from application code. When Alembic rolls this migration back, the function tells the database to drop the `memory_item_inventory` index from the `memory_item` table. After it finishes, the database no longer has this specific shortcut for inventory explorer reads.

**Call relations**: Alembic calls this function when undoing the migration. It delegates the database change to `alembic.op.drop_index`, which removes the index created by `upgrade`.

*Call graph*: 1 external calls (drop_index).


### `extensions/memory/ufo_ext_memory/migrations/0007_memory_as_of.py`

`data_model` · `database migration`

This migration changes the shape of the database table that stores memory entries. The real-world problem it solves is that a memory may not just be something saved at a certain time; it may describe information that was true at some other time. For example, a note saved today might say, “The user's address as of January 2023 was...” The new `as_of` column gives the system a place to store that “true as of” time.

The file uses Alembic, a tool that applies database changes step by step, like numbered renovation instructions for a building. When moving forward, it opens the `memory_item` table in a safe editing mode and adds a nullable timezone-aware date-time column. “Nullable” means old rows do not need an immediate value, which keeps the upgrade safe for existing data.

It also includes the reverse instruction. If the system needs to roll this migration back, the `downgrade` function removes the same column. The revision markers at the top tell Alembic where this change belongs in the migration chain, so it runs in the right order.

#### Function details

##### `upgrade`  (lines 12–14)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change. It adds an optional `as_of` timestamp column to the `memory_item` table so memory records can say when their information applies.

**Data flow**: It reads the migration instruction in the file, opens the `memory_item` table for alteration, creates a new timezone-aware date-time column named `as_of`, and adds it to the table. After it runs, existing and future memory rows have a new place to store this timestamp, while old rows can leave it empty.

**Call relations**: Alembic calls this function when the database is being upgraded to this revision. Inside, it asks Alembic to safely alter the `memory_item` table and uses SQLAlchemy to describe the new column and its date-time type.

*Call graph*: 3 external calls (batch_alter_table, Column, DateTime).


##### `downgrade`  (lines 17–19)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration. It removes the `as_of` column from the `memory_item` table if the database is rolled back to the previous version.

**Data flow**: It opens the `memory_item` table for alteration and drops the `as_of` column. After it runs, the table returns to its earlier shape, and any stored `as_of` values are gone.

**Call relations**: Alembic calls this function during a rollback from this revision. It uses Alembic's table-alteration helper to undo the change made by `upgrade`.

*Call graph*: 1 external calls (batch_alter_table).


### `extensions/memory/ufo_ext_memory/migrations/0008_page_information_time.py`

`io_transport` · `database migration`

This file is a one-time database update used during an Alembic migration. Alembic is the tool that applies database changes in order, like a careful checklist for bringing an old database up to the current shape and content.

The problem it solves is that some rows in the `memory_item` table have no `as_of` time, even though they point back to a page through `source_ref`. That missing time matters because it tells the system when the information was true or last known. Without this backfill, older memory entries could look timeless or incomplete compared with newer ones.

The migration reads `memory_item` rows whose `source_ref` is present but whose `as_of` value is missing. It treats `source_ref` as a possible page ID. If it is not a valid UUID, it skips that row rather than failing the whole migration. It then finds the matching rows in the `page` table and uses the page’s updated time, or its created time if there is no updated time, as the memory item’s information time.

It processes records in batches of 500. That is like moving boxes a cartload at a time instead of trying to carry the whole warehouse at once. The downgrade does nothing, because this data fill-in is not reversed automatically.

#### Function details

##### `upgrade`  (lines 17–69)

```
def upgrade() -> None
```

**Purpose**: This applies the forward migration. It finds memory records with missing time information, looks up the related page records, and writes the best available page timestamp back into the memory records.

**Data flow**: It starts by defining lightweight views of the `memory_item` and `page` tables, then opens the current database connection. It reads memory rows where `source_ref` is set and `as_of` is empty. For each batch, it tries to turn each `source_ref` into a UUID, uses those IDs to fetch matching pages, chooses each page’s updated time or created time, converts that text into a datetime value, and updates the matching memory rows. The output is not a returned value; the database is changed in place.

**Call relations**: The Alembic migration runner calls this when upgrading to revision `memory_0008`. Inside, it asks Alembic for the active database connection, uses SQLAlchemy to build the selects and updates safely, and uses Python’s datetime parser to turn stored page time text into a real timestamp before writing it into `memory_item.as_of`.

*Call graph*: 11 external calls (get_bind, fromisoformat, DateTime, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 72–73)

```
def downgrade() -> None
```

**Purpose**: This is the placeholder for reversing the migration, but it intentionally does nothing. The migration fills in missing historical data, and the file does not try to remove that data later.

**Data flow**: It receives no input, reads nothing, changes nothing, and returns nothing. The database is left exactly as it was before this function was called.

**Call relations**: The Alembic migration runner would call this if someone tried to downgrade past this revision. Because the function body is empty, it does not hand work off to SQLAlchemy or Alembic helpers and performs no rollback of the timestamp backfill.


### `extensions/memory/ufo_ext_memory/migrations/0009_memory_page_provenance.py`

`data_model` · `database migration`

This file is part of the database change history for the memory extension. Its job is to make the origin of a memory item more explicit. Before this migration, some memory items stored their source page in a general text field called source_ref. That is like writing an address in a free-form notes box: it works, but the database cannot reliably treat it as a real link. This migration adds a new created_from_page_id column to memory_item, meant specifically to point to a row in the page table.

After adding the column, the migration looks through existing memory items that still have source_ref filled in. It reads them in small batches so it does not try to load the whole table at once. For each row, it checks whether source_ref is shaped like a UUID, which is a standard unique identifier. If it is not, the row is left alone. If it is, the migration checks that a page with that ID actually exists. Only then does it copy that ID into created_from_page_id and clear source_ref.

This matters because it turns a loose text reference into structured provenance: the system can later ask, “Which page did this memory come from?” without guessing.

#### Function details

##### `upgrade`  (lines 16–59)

```
def upgrade() -> None
```

**Purpose**: This applies the migration. It adds the new created_from_page_id column and backfills it from old source_ref values when those values are valid page IDs.

**Data flow**: It starts with the existing memory_item table, where some rows may have a source_ref text value. It adds a new nullable column, then reads memory rows in batches. For each source_ref, it tries to treat the text as a UUID. It keeps only IDs that match real rows in the page table, then updates the matching memory items by setting created_from_page_id and clearing source_ref. The result is a database where old page references have been moved into the new dedicated column when possible.

**Call relations**: Alembic, the database migration tool, calls this when the project is moved forward to this revision. Inside the function, Alembic supplies the database connection and table-alteration context, while SQLAlchemy builds the database queries and updates. The function does not hand work to project-specific code; it performs the schema change and data cleanup directly.

*Call graph*: 11 external calls (batch_alter_table, get_bind, Column, Text, Uuid, bindparam, column, select, table, update (+1 more)).


##### `downgrade`  (lines 62–64)

```
def downgrade() -> None
```

**Purpose**: This reverses the schema part of the migration by removing the created_from_page_id column. It is used if the database must be rolled back to the previous revision.

**Data flow**: It starts with a memory_item table that includes created_from_page_id. It opens a safe table-alteration block and drops that column. The database ends without that column. Any values stored only there are removed as part of dropping the column.

**Call relations**: Alembic calls this when rolling the database backward from this revision. It uses Alembic’s batch table alteration helper to make the column removal in the database. Unlike the upgrade path, it does not copy data back into source_ref; it only removes the new column.

*Call graph*: 1 external calls (batch_alter_table).

## 📊 State Registers Touched

- `reg-deployment-schema-version` — The shared record of which database and extension upgrades have already been applied.
- `reg-workspace-tenant-record` — The customer workspace record that all users, conversations, data, tools, and billing are kept under.
- `reg-agent-profile-settings` — The saved assistant settings for a workspace, including which agent is used and what it is allowed to do.
- `reg-surface-installation-binding` — The stored connection between outside channels like Slack, web chat, or terminal clients and an internal workspace conversation.
- `reg-inbound-message-dedup` — The durable inbox and duplicate-detection state for messages arriving from external surfaces.
- `reg-conversation-thread-state` — The saved conversation identity and history that let the system continue the same thread over time.
- `reg-turn-record-lifecycle` — The durable record of each unit of agent work, including who started it, its status, parent links, and final result.
- `reg-durable-work-queue` — The shared queue of conversation and job work waiting to be claimed, retried, resumed, or completed by workers.
- `reg-runtime-presence` — The live roll-call of server and worker processes used to recover abandoned work safely.
- `reg-cancellation-state` — The shared stop signal and saved cancellation status for turns, child turns, and paused work.
- `reg-seat-entitlement` — The workspace membership and seat-limit state that decides which people the agent may serve.
- `reg-spend-ledger-billing` — The shared usage ledger, prices, caps, exports, and billing records used to track and limit spending.
- `reg-credential-secret-store` — The encrypted store of workspace secrets and credential kinds used without exposing raw tokens to agents.
- `reg-prompt-skill-library` — The enabled instructions, skill folders, helper profiles, and prompt versions that shape how the agent behaves.
- `reg-sandbox-workspace-handle` — The saved handle and lease for the safe workspace where a conversation can run commands and keep files.
- `reg-blob-storage-backend` — The shared large-file storage used for workspace files, transcripts, source snapshots, and artifacts.
- `reg-source-page-sync-state` — The saved sources, pages, sync cursors, deletion markers, and retry state for imported external content.
- `reg-search-index-memory-graph` — The shared recall stores for searchable chunks, remembered facts, memory pages, and knowledge-graph links.
- `reg-scheduled-task-state` — The saved clock-based tasks, waits, pauses, last-run markers, and expiration times used to wake work later.
- `reg-extension-object-store` — The durable per-workspace storage and named objects that extensions expose or update over time.
- `reg-transcript-compaction-store` — The saved conversation transcript and compacted summaries used to rebuild context and inspect past turns.
- `reg-artifact-download-tokens` — The short-lived signed passes that let private files produced by a turn be downloaded safely.
- `reg-row-level-security-context` — The database safety context that keeps each workspace’s rows separated even when code uses shared tables.
- `reg-database-connection-pool` — The shared database engine/session pool and transaction lifecycle used by migrations, request handlers, workers, and shutdown cleanup.
- `reg-artifact-share-registry` — Durable artifact metadata linking produced files to workspaces, conversations, turns, blob keys, and sharing visibility.
- `reg-self-improvement-governance-state` — Saved failure corpora, replay/evaluation results, prompt-change proposals, approvals, and guards for the self-improvement loop.
- `reg-workspace-domain-claim-map` — The hosted onboarding mapping from verified company email domains to existing or newly-created workspaces, reused for domain-based workspace lookup and operator authorization.
- `reg-human-approval-proposal-state` — Durable proposal records for changes or actions that must be reviewed, accepted, rejected, or superseded before taking effect.
- `reg-user-created-skill-store` — Persistent user-authored skill definitions and metadata that are loaded into the skill library and made available to prompts and tools across turns.
- `reg-repl-snippet-state` — Remembered Python or JavaScript REPL snippets and scratch execution context retained by the REPL extension for reuse across tool calls or turns.
- `reg-workspace-file-access-leases` — Short-lived scoped credentials or signed grants that let sandboxes mount or access only approved workspace blob/file paths.
