# Core persistence infrastructure and shared schema  `stage-21.1`

This stage is shared behind-the-scenes support for anything that needs durable storage. It is not one user-facing workflow. Instead, it provides the common “plumbing” that other parts of the system rely on when they save files, read database records, or recover long-running work.

The blob module is the file cabinet for large chunks of bytes. In development those bytes may be local files, while in production they may live in S3-style cloud storage. The rest of the code uses the same async calls either way, so it does not need to know where the bytes are physically stored.

The database module is the guarded front door to the database. It creates connection pools, runs transactions, applies migrations, and keeps workspace data separated unless trusted owner-level access is requested.

The durability module helps DBOS reload saved Python workflow state safely, even when Pydantic data models have changed between versions.

The schema package marker gives schema code a clear import home. The tables module is the shared blueprint for the database tables, columns, links, and rules used by SQLite and Postgres.

## Files in this stage

### Blob storage interface
A shared asynchronous abstraction for storing and retrieving large byte objects across local and cloud-backed storage.

### `core/src/ufo/blob.py`

`io_transport` · `cross-cutting; used whenever code reads, writes, streams, lists, or deletes stored blobs`

This file is the project’s storage adapter for blobs: chunks of bytes such as attachments, inbound files, transcripts, compaction records, or shared artifacts. Without it, every caller would need separate code for local disk and S3, and large files might be read into memory all at once.

The central idea is a shared contract called BlobStore. It says a blob store can put bytes, get bytes, check if a key exists, delete a key, stream bytes in or out, and list stored objects under a prefix. A “key” is a slash-separated name, a bit like a path inside a private storage cabinet.

There are two real implementations. FilesystemBlobStore turns keys into files under a configured root directory. It is careful to keep every key inside that root, and it writes through a temporary file before replacing the final file, so readers do not see half-written data. Slow file operations are moved to a worker thread so they do not block the async event loop.

S3BlobStore talks to S3 or an S3-compatible service. It reuses one client per event loop, streams large downloads, uses multipart upload for large uploads, and can create tightly limited presigned upload URLs so an isolated sandbox can upload one exact file directly. Both stores return the same BlobEntry records when listing, so callers get a consistent view.

#### Function details

##### `BlobStore.put`  (lines 52–52)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Defines the shared promise that any blob store can save a whole blob from bytes already held in memory. Callers use this for small or already-collected payloads.

**Data flow**: A key and a bytes object go in. A concrete store writes those bytes under that key. Nothing is returned, but the store is changed so the key now points to the saved data.

**Call relations**: This is part of the common BlobStore contract. FilesystemBlobStore.put and S3BlobStore.put provide the real behavior behind this promise, so higher-level code can call put without knowing which backend was configured.


##### `BlobStore.get`  (lines 54–54)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Defines the shared promise that any blob store can read a whole stored blob into memory. It is useful when the expected object is small enough to return as one bytes value.

**Data flow**: A key goes in. The concrete store looks up that key and returns its bytes. If the key is missing, implementations raise BlobNotFound rather than returning empty data.

**Call relations**: This protocol method is used by code such as transcript compaction reading and Slack identity loading. At runtime those calls land on either FilesystemBlobStore.get or S3BlobStore.get, depending on configuration.

*Call graph*: called by 2 (read_compaction_record, read_identity).


##### `BlobStore.exists`  (lines 56–56)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Defines the shared promise that a store can answer whether a blob key is present. It lets callers check for optional stored data without trying to read it first.

**Data flow**: A key goes in. The concrete store checks its backend and returns true if a normal stored object exists there, otherwise false.

**Call relations**: Slack identity loading calls this through the BlobStore interface. The configured backend supplies the actual check through FilesystemBlobStore.exists or S3BlobStore.exists.

*Call graph*: called by 1 (read_identity).


##### `BlobStore.delete`  (lines 58–60)

```
async def delete(self, key: str) -> None
```

**Purpose**: Defines the shared promise that a store can remove a blob. Deleting something that is already gone is treated as success, which makes retries safe.

**Data flow**: A key goes in. The concrete store asks its backend to remove that object. Nothing is returned, and after the call the key should not have stored data.

**Call relations**: This is the interface-level rule that concrete stores follow. FilesystemBlobStore.delete and S3BlobStore.delete implement it for disk and S3.


##### `BlobStore.get_stream`  (lines 62–62)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines the shared promise that a store can read a blob piece by piece instead of all at once. This matters for large files because it avoids holding the entire file in memory.

**Data flow**: A key goes in. The concrete store opens the object and yields chunks of bytes until the object is fully read. The caller receives an async stream of byte chunks.

**Call relations**: This is the contract used when callers want bounded-memory downloads. FilesystemBlobStore.get_stream reads file chunks, while S3BlobStore.get_stream reads chunks from an S3 response body.


##### `BlobStore.put_stream`  (lines 64–64)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Defines the shared promise that a store can write a blob from an incoming stream of chunks. It is the upload-side partner to get_stream.

**Data flow**: A key and an async stream of byte chunks go in. The concrete store consumes each chunk and writes it to the backend. Nothing is returned, but the full collected stream becomes the stored object.

**Call relations**: This protocol method lets upstream code send large data without building one huge bytes object first. The filesystem implementation writes chunks to a temporary file, while the S3 implementation may use multipart upload.


##### `BlobStore.list`  (lines 66–70)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Defines the shared promise that a store can list stored objects under a required key prefix. It gives callers a bounded directory-like view instead of scanning everything.

**Data flow**: A prefix string goes in. The concrete store finds matching stored objects, gathers each object’s key, size, and last-modified time, sorts or returns them in key order, and returns BlobEntry records up to the configured limit.

**Call relations**: This interface supports read views such as walking a conversation’s stored compaction records. FilesystemBlobStore.list and S3BlobStore.list provide backend-specific listing.


##### `FilesystemBlobStore.put`  (lines 79–84)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves a complete bytes object as a file under the blob root. It uses a temporary file and then swaps it into place so a partial write is not mistaken for a finished blob.

**Data flow**: A key and bytes go in. The function resolves the key to a safe path inside the root, creates parent folders if needed, writes the bytes to a uniquely named temporary file, and replaces the final path with that temporary file. It returns nothing, but the filesystem now contains the finished blob.

**Call relations**: This is the disk-backed version of BlobStore.put. It relies on FilesystemBlobStore._resolve to prevent keys from escaping the storage root, uses thread offloading for blocking file work, and is chosen when blob_store_for builds a filesystem backend.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.get`  (lines 86–91)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a whole blob from the local filesystem and returns it as bytes. It translates a missing file into the project’s BlobNotFound error so callers get the same behavior as S3.

**Data flow**: A key goes in. The function resolves it to a safe file path, reads all bytes from that file in a worker thread, and returns those bytes. If the file is absent, BlobNotFound comes out instead.

**Call relations**: This is the disk-backed version of BlobStore.get. It uses FilesystemBlobStore._resolve for path safety and matches the missing-key behavior used by S3BlobStore.get.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.exists`  (lines 93–95)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a blob key points to a normal file inside the local blob root. It lets callers ask a yes-or-no question without loading the blob.

**Data flow**: A key goes in. The function resolves it safely, asks the filesystem whether that path is a file, and returns true or false.

**Call relations**: This is the disk-backed version of BlobStore.exists. It depends on FilesystemBlobStore._resolve before checking the path, so even existence checks cannot probe outside the store.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.delete`  (lines 97–99)

```
async def delete(self, key: str) -> None
```

**Purpose**: Removes a local blob file if it exists. It deliberately treats an already-missing file as fine, which makes repeated cleanup attempts harmless.

**Data flow**: A key goes in. The function resolves it to a safe path and asks the filesystem to unlink, or remove, that file while ignoring missing-file errors. It returns nothing.

**Call relations**: This is the disk-backed version of BlobStore.delete. Like the other filesystem operations, it first goes through FilesystemBlobStore._resolve to keep deletion inside the configured blob directory.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.get_stream`  (lines 101–114)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads a local blob in fixed-size chunks. This is used when a blob may be large enough that loading all bytes at once would waste memory.

**Data flow**: A key goes in. The function resolves the file path, opens the file for binary reading, repeatedly reads one configured chunk, and yields each chunk to the caller. It closes the file when done or if the caller stops early; if the file is missing, it raises BlobNotFound.

**Call relations**: This is the streaming disk implementation promised by BlobStore.get_stream. It uses FilesystemBlobStore._resolve for safety and runs blocking file operations through asyncio.to_thread so the async loop can keep serving other work.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.put_stream`  (lines 116–129)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes a local blob from an incoming stream of chunks. It protects readers from partial files by writing to a temporary file first and only replacing the final file after every chunk is written.

**Data flow**: A key and async chunk stream go in. The function resolves a safe destination, creates folders, opens a unique temporary file, writes each incoming chunk, closes the file, and moves it into place. If anything fails, it closes and deletes the temporary file before re-raising the error.

**Call relations**: This is the filesystem version of BlobStore.put_stream. It shares the same safety pattern as FilesystemBlobStore.put, uses FilesystemBlobStore._resolve, and offloads blocking file writes to worker threads.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.list`  (lines 131–134)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists local blobs below a required prefix. Requiring a prefix prevents accidental whole-store scans.

**Data flow**: A prefix goes in. If the prefix is empty, the function rejects it. Otherwise it runs the slower directory walk in a worker thread and returns the resulting BlobEntry records.

**Call relations**: This is the filesystem implementation of BlobStore.list. It delegates the real walking and filtering work to FilesystemBlobStore._walk so the async event loop is not blocked by filesystem traversal.

*Call graph*: 1 external calls (to_thread).


##### `FilesystemBlobStore._walk`  (lines 136–157)

```
def _walk(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Walks the local storage directory and builds the list entries for matching blobs. It skips temporary files so unfinished writes do not appear as real objects.

**Data flow**: A prefix goes in. The function finds the canonical root, chooses the directory that could contain matching files, walks files below it, converts each file path back into a blob key, filters by prefix and non-temporary name, reads size and modification time, and returns sorted BlobEntry records capped at the maximum.

**Call relations**: FilesystemBlobStore.list calls this in a worker thread. It uses FilesystemBlobStore._contained_root to know the real root and FilesystemBlobStore._resolve to choose a safe starting directory.

*Call graph*: calls 2 internal fn (_contained_root, _resolve); 4 external calls (__init__, fromtimestamp, walk, Path).


##### `FilesystemBlobStore._resolve`  (lines 159–164)

```
def _resolve(self, key: str) -> Path
```

**Purpose**: Turns a blob key into a filesystem path and proves that the result stays inside the blob root. This is the main guard against keys like '../secret' reaching files outside storage.

**Data flow**: A key goes in. The function combines it with the canonical root, resolves symbolic links and relative path pieces, then checks that the final path is still below the root and is not the root itself. It returns the safe path or raises ValueError for an escaping key.

**Call relations**: All filesystem operations call this before touching a path: put, get, exists, delete, get_stream, put_stream, and _walk. It depends on FilesystemBlobStore._contained_root for the root it compares against.

*Call graph*: calls 1 internal fn (_contained_root); called by 7 (_walk, delete, exists, get, get_stream, put, put_stream).


##### `FilesystemBlobStore._contained_root`  (lines 166–179)

```
def _contained_root(self) -> Path
```

**Purpose**: Finds the canonical local blob root directory used for safety checks. It allows a configured root that does not exist yet, because the first write may create it.

**Data flow**: The configured root path stored on the object is read. The function asks the containment helper to validate and canonicalize it; if the path does not exist yet, it resolves the configured path directly. The resulting Path is returned.

**Call relations**: FilesystemBlobStore._resolve and FilesystemBlobStore._walk call this whenever they need the trusted root. It delegates validation to ufo.sandbox.containment.configured_root and uses the blob.root setting name in errors so operators know which configuration value is wrong.

*Call graph*: called by 2 (_resolve, _walk); 1 external calls (configured_root).


##### `_is_missing_key`  (lines 182–183)

```
def _is_missing_key(error: ClientError) -> bool
```

**Purpose**: Recognizes S3 error responses that mean “that object is not there.” It gives the S3 code one small, consistent test for missing blobs.

**Data flow**: An S3 ClientError goes in. The function reads the error code from the response and checks it against known missing-object codes. It returns true for a missing key and false for other errors.

**Call relations**: S3BlobStore.get, S3BlobStore.exists, and S3BlobStore.get_stream call this after S3 reports an error. It decides whether those functions should translate the error into BlobNotFound or false, or let the original S3 failure continue upward.

*Call graph*: called by 3 (exists, get, get_stream).


##### `S3BlobStore.put`  (lines 202–204)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves a complete bytes object to an S3 bucket under the given key. It is the cloud-backed equivalent of writing one finished local file.

**Data flow**: A key and bytes go in. The function gets the cached S3 client, sends a put_object request with the bucket, key, and bytes body, and returns after S3 accepts the object.

**Call relations**: This is the S3 implementation of BlobStore.put. It calls S3BlobStore._client first so client creation and reuse stay centralized.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get`  (lines 206–216)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a whole S3 object into memory and returns it as bytes. It converts S3’s missing-object errors into BlobNotFound so callers see the same error shape as the filesystem backend.

**Data flow**: A key goes in. The function gets the S3 client, requests the object, opens the response body, reads all bytes, and returns them. If S3 says the key is missing, BlobNotFound is raised; other S3 errors are left unchanged.

**Call relations**: This is the S3 implementation of BlobStore.get. It uses S3BlobStore._client for connection access and _is_missing_key to decide how to treat ClientError responses.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.exists`  (lines 218–226)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether an S3 object exists without downloading it. It uses S3’s metadata check, which is cheaper than reading the whole object.

**Data flow**: A key goes in. The function gets the S3 client and sends a head_object request. A successful response becomes true; a recognized missing-key error becomes false; any other S3 error is raised.

**Call relations**: This is the S3 implementation of BlobStore.exists. It shares the missing-key interpretation with get and get_stream through _is_missing_key.

*Call graph*: calls 2 internal fn (_client, _is_missing_key).


##### `S3BlobStore.delete`  (lines 228–230)

```
async def delete(self, key: str) -> None
```

**Purpose**: Asks S3 to remove an object from the bucket. S3 delete calls are naturally safe to repeat for a missing key, matching the BlobStore contract.

**Data flow**: A key goes in. The function gets the S3 client, sends a delete_object request for the bucket and key, and returns nothing after the request completes.

**Call relations**: This is the S3 implementation of BlobStore.delete. It relies on S3BlobStore._client for the reusable client used by all S3 operations.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get_stream`  (lines 232–243)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams an S3 object in chunks instead of reading it all at once. This is important for large blobs because memory use stays bounded.

**Data flow**: A key goes in. The function gets the S3 client, requests the object, opens the response body, and yields chunks of up to the configured size. If S3 reports that the key is missing, BlobNotFound is raised.

**Call relations**: This is the S3 implementation of BlobStore.get_stream. It calls S3BlobStore._client for access and _is_missing_key to translate missing-object errors consistently.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.put_stream`  (lines 245–289)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Uploads a stream of chunks to S3, using a simple one-shot upload for small data and multipart upload for larger data. Multipart upload is S3’s way to send a large object in numbered pieces and then assemble them.

**Data flow**: A key and async chunk stream go in. The function buffers incoming bytes until a part is large enough; if the data stays small, it sends one put_object request. For larger data, it starts a multipart upload, uploads each buffered part, records the part identifiers S3 returns, uploads the final part, and asks S3 to complete the object. If anything fails after multipart upload starts, it aborts the upload so stray unfinished parts are not left behind.

**Call relations**: This is the S3 implementation of BlobStore.put_stream. It uses S3BlobStore._client for all S3 requests and complements S3BlobStore.get_stream for large-object transfer.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_put`  (lines 291–315)

```
async def presigned_put(self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary upload URL that an outside sandbox can use to PUT exactly one expected file to S3. The URL is signed with the file size and checksum, so changing the length or contents makes S3 reject it.

**Data flow**: A key, expected byte size, SHA-256 checksum, and expiration time go in. The function gets the S3 client and asks it to generate a presigned put_object URL containing those restrictions. The resulting URL string comes out.

**Call relations**: This function is specific to the S3 backend and is used when the sandbox should upload directly instead of sending bytes through the main service. It depends on S3BlobStore._client so the URL is signed using the same endpoint and configuration as other S3 operations.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.put_host`  (lines 317–328)

```
async def put_host(self) -> str
```

**Purpose**: Returns the hostname that a presigned upload URL will contact. The egress proxy can then allow the sandbox to reach that exact host and no broader destination.

**Data flow**: The function reads the configured S3 client’s endpoint URL, extracts its hostname, and returns either that hostname for custom endpoints or the bucket-prefixed hostname for normal AWS virtual-hosted S3 URLs. If no hostname can be found, it raises RuntimeError.

**Call relations**: This supports the same direct-sandbox-upload flow as S3BlobStore.presigned_put. It calls S3BlobStore._client so the allowed host is derived from the actual signing client, avoiding mismatches between the URL and the proxy rule.

*Call graph*: calls 1 internal fn (_client); 1 external calls (urlsplit).


##### `S3BlobStore.list`  (lines 330–347)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists S3 objects under a required prefix and returns their key, size, and last-modified time. The prefix requirement prevents expensive bucket-wide browsing.

**Data flow**: A prefix goes in. Empty prefixes are rejected. The function gets the S3 client, pages through S3 list_objects_v2 results for that prefix, turns each object into a BlobEntry with UTC time, stops once the maximum count is reached, and returns the capped tuple.

**Call relations**: This is the S3 implementation of BlobStore.list. It uses S3BlobStore._client for the paginator and produces the same BlobEntry shape as FilesystemBlobStore._walk.

*Call graph*: calls 1 internal fn (_client); 1 external calls (__init__).


##### `S3BlobStore._client`  (lines 349–375)

```
async def _client(self) -> AioBaseClient
```

**Purpose**: Creates and reuses one async S3 client per event loop. This avoids repeatedly paying the cost of building clients and respects that the underlying HTTP client belongs to the event loop where it was created.

**Data flow**: The current running event loop is read. If a client for that loop already exists, it is returned. Otherwise the function creates an aiobotocore S3 client with explicit signing and addressing settings, stores it in the per-loop cache, closes any duplicate client created during a race, and returns the cached client.

**Call relations**: Every S3 operation calls this before talking to S3: put, get, exists, delete, get_stream, put_stream, presigned_put, put_host, and list. If a redundant client cannot be closed cleanly, it logs a warning through the observability logger.

*Call graph*: called by 9 (delete, exists, get, get_stream, list, presigned_put, put, put_host, put_stream); 3 external calls (get_session, get_running_loop, log).


##### `blob_store_for`  (lines 378–390)

```
def blob_store_for(config: BlobConfig) -> FilesystemBlobStore | S3BlobStore
```

**Purpose**: Builds the concrete blob store selected by configuration. It is the small factory that turns settings into either local-file storage or S3 storage.

**Data flow**: A BlobConfig goes in. The function checks the backend name, verifies the required fields for that backend, and returns a FilesystemBlobStore for filesystem mode or an S3BlobStore for S3 mode. If required configuration is missing, it raises ValueError.

**Call relations**: Startup or wiring code can call this once it has loaded configuration. The object it returns is then used through the BlobStore-style methods by the rest of the project.

*Call graph*: 2 external calls (__init__, __init__).


### Database runtime services
Connection, transaction, migration, tenant-safety, and workflow-durability helpers for persistent runtime state.

### `core/src/ufo/db.py`

`io_transport` · `startup, request handling, background jobs, migrations, teardown`

This file solves a critical safety problem: one running service can serve many workspaces, but database reads and writes must not accidentally cross from one workspace into another. It does this by making `workspace_tx` the normal way to open a database transaction. A transaction is a bundle of database work that succeeds or fails as one unit. When the database is PostgreSQL, `workspace_tx` pins the current workspace ID into a temporary database setting for that transaction, so row-level security can filter rows correctly. Think of it like putting a colored wristband on a visitor before they enter a room; the room’s guards only show them things for that color.

The file also keeps database engines and connection pools private. A connection pool is a small reusable supply of database connections, so the app does not have to open a new connection for every query. Because async database connections are tied to the event loop that created them, this file keeps a separate pool per event loop.

There is one intentional exception: `owner_tx`, used for cross-workspace background sweeps. It must only find identifiers to re-enter each workspace safely later, not read tenant data as if it were scoped. The file also supports startup checks, cleanup, SQLite tuning for local/test use, and Alembic migrations, which update the database schema.

#### Function details

##### `_build_engine`  (lines 98–108)

```
def _build_engine(url: str, pool: _Pool) -> AsyncEngine
```

**Purpose**: Creates a SQLAlchemy async database engine, which is the object that owns a pool of reusable database connections. It also adds special SQLite setup hooks when the database is SQLite.

**Data flow**: It receives a database URL and a pool description. It asks `_pool_kwargs` for the right pool and driver settings, creates the async engine, and, for SQLite, attaches small callbacks that configure each connection and start writes safely. It returns the ready-to-use engine.

**Call relations**: When `_engine_for` needs a pool for the current event loop, it calls this to build one lazily. `verify_db_reachable` also calls it to make a temporary engine just to test that the database can be reached.

*Call graph*: calls 1 internal fn (_pool_kwargs); called by 2 (_engine_for, verify_db_reachable); 1 external calls (create_async_engine).


##### `_pool_kwargs`  (lines 111–131)

```
def _pool_kwargs(url: str, pool: _Pool) -> dict[str, Any]
```

**Purpose**: Chooses the correct connection pool settings for the database type. SQLite and PostgreSQL need different rules, so this function keeps those differences in one place.

**Data flow**: It receives a database URL and pool limits. It parses the URL, then returns a dictionary of settings: SQLite gets a queue-style pool with unlimited overflow, while PostgreSQL-style databases get bounded overflow, recycling, health checks, and driver-specific connection options.

**Call relations**: `_build_engine` calls this just before creating an engine. If the URL is not SQLite, this function hands off to `_driver_kwargs` to add the exact options needed by the database driver.

*Call graph*: calls 1 internal fn (_driver_kwargs); called by 1 (_build_engine); 1 external calls (make_url).


##### `_driver_kwargs`  (lines 134–161)

```
def _driver_kwargs(driver: str, pool: _Pool) -> dict[str, Any]
```

**Purpose**: Builds the connection options that differ between database drivers. This matters because drivers use different names for the same ideas, such as timeout settings and application names.

**Data flow**: It receives a driver name and pool description. For asyncpg, it returns asyncpg-specific options, including a connection timeout, an application name, and disabled prepared statement caching. For other supported PostgreSQL drivers, it returns their equivalent options.

**Call relations**: `_pool_kwargs` uses this when preparing non-SQLite engine settings. It does not call other project code; it is the small translation layer between this project’s pool policy and each driver’s vocabulary.

*Call graph*: called by 1 (_pool_kwargs).


##### `_engine_for`  (lines 164–180)

```
def _engine_for(url: str, pool: _Pool) -> AsyncEngine
```

**Purpose**: Finds or creates the database engine for the current async event loop and URL. This prevents connections made on one event loop from being wrongly reused on another.

**Data flow**: It reads the currently running event loop and combines it with the database URL as a lookup key. It removes registry entries for loops that are already closed, reuses an existing engine if one is present, or builds and stores a new one with `_build_engine`. It returns the engine for this loop and URL.

**Call relations**: `workspace_tx` and `owner_tx` call this whenever they need a transaction. If no suitable engine exists yet, `_engine_for` creates it through `_build_engine`; otherwise it quietly reuses the existing pool.

*Call graph*: calls 1 internal fn (_build_engine); called by 2 (owner_tx, workspace_tx); 1 external calls (get_running_loop).


##### `init_db`  (lines 183–187)

```
def init_db(url: str) -> None
```

**Purpose**: Registers the main application database URL. This must happen before normal database transactions can be opened.

**Data flow**: It receives a URL string. If the application database has not been initialized yet, it stores the URL in module-level state. If it was already initialized, it raises an error to avoid silently switching databases mid-run.

**Call relations**: Startup or command setup code calls this before `workspace_tx`, `owner_tx`, or `verify_db_reachable` can do useful work. It does not open a connection itself; actual engines are created later on first use.


##### `init_owner_db`  (lines 190–204)

```
def init_owner_db(url: str) -> None
```

**Purpose**: Registers the special owner database URL used for cross-workspace enumeration. This owner path can bypass normal row-level security, so it is deliberately separate from normal workspace transactions.

**Data flow**: It receives an owner database URL. It rejects a second initialization, then normalizes plain `postgresql://` URLs into SQLAlchemy’s asyncpg form so the async engine can use them. It stores the normalized URL for later owner transactions.

**Call relations**: Service startup calls this when an owner role is available. Later, `owner_tx` uses the stored URL; if this was never called, `owner_tx` falls back to the normal application URL instead.


##### `verify_db_reachable`  (lines 207–227)

```
async def verify_db_reachable() -> None
```

**Purpose**: Checks at startup that configured databases can actually be contacted. This helps the process fail early instead of accepting traffic while every later request would fail.

**Data flow**: It reads the registered app and owner URLs. For each one, it builds a temporary engine, tries to open and close a connection, and always disposes the engine afterward. If no database was initialized, or a connection cannot be opened, it raises an error.

**Call relations**: Startup code calls this after `init_db` and optionally `init_owner_db`. It uses `_build_engine` directly because the check should not publish a long-lived engine into the normal per-loop registry.

*Call graph*: calls 1 internal fn (_build_engine).


##### `dispose_db`  (lines 230–253)

```
async def dispose_db() -> None
```

**Purpose**: Shuts down all known database engines and clears the registered database URLs. It is used by command-line tools, tests, and teardown paths so connections do not linger.

**Data flow**: It gets the current event loop, clears the app and owner URLs first, then walks both engine registries. Engines owned by the current loop are disposed immediately. Engines owned by still-running other loops are removed from the registry and handed back to their own loops for cleanup.

**Call relations**: Teardown code calls this when the whole database layer should be reset. For engines on other event loops, it calls `_hand_off` because only the loop that created those async connections can safely close them.

*Call graph*: calls 1 internal fn (_hand_off); 1 external calls (get_running_loop).


##### `_hand_off`  (lines 256–263)

```
def _hand_off(loop: asyncio.AbstractEventLoop, engine: AsyncEngine) -> None
```

**Purpose**: Asks another event loop to dispose an engine that belongs to it. This avoids closing async database connections from the wrong loop.

**Data flow**: It receives an event loop and an engine. It schedules `_dispose_on_this_loop` to run on that loop using a thread-safe call. If the loop has already closed, it quietly gives up because there is no safe place left to run the cleanup.

**Call relations**: `dispose_db` calls this for engines owned by other still-open loops. `_hand_off` then arranges for `_dispose_on_this_loop` to do the actual disposal on the correct loop.

*Call graph*: called by 1 (dispose_db); 1 external calls (call_soon_threadsafe).


##### `_dispose_on_this_loop`  (lines 266–273)

```
def _dispose_on_this_loop(engine: AsyncEngine) -> None
```

**Purpose**: Starts engine cleanup on the event loop that owns the engine’s connections. It keeps the cleanup task alive until it finishes.

**Data flow**: It receives an engine. It schedules `engine.dispose()` as an async task, stores that task in the `_disposing` set so it is not garbage-collected early, and removes the task from the set when it completes.

**Call relations**: _hand_off schedules this on a foreign event loop during teardown. It is the final step that actually closes the engine’s pool when cleanup cannot be awaited directly by the caller.

*Call graph*: 2 external calls (ensure_future, dispose).


##### `dispose_loop_engines`  (lines 276–286)

```
async def dispose_loop_engines() -> None
```

**Purpose**: Closes only the database engines owned by the currently running event loop, while leaving the configured database URLs intact. This is useful for temporary event loops that are about to close.

**Data flow**: It reads the current event loop, scans the app and owner engine registries, removes entries owned by that loop, and awaits disposal of each matching engine. Other loops’ engines are left alone.

**Call relations**: Code that creates short-lived event loops can call this before the loop shuts down. Unlike `dispose_db`, it does not reset the whole database module; it just prevents connections from being abandoned on a closing loop.

*Call graph*: 1 external calls (get_running_loop).


##### `_opened`  (lines 290–339)

```
async def _opened(engine: AsyncEngine, path: str) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens a database transaction and records observability metrics about how long it took or why it failed. It also carefully finishes the transaction even if the caller is cancelled.

**Data flow**: It receives an engine and a path label such as `workspace` or `owner`. It times how long acquiring a transaction takes, emits metrics for slow or failed acquisition, yields the open connection to the caller, then commits or rolls back through the transaction context. If cancellation happens during cleanup, it shields the close operation so the database is still left in a clean state, then re-raises the original problem.

**Call relations**: `workspace_tx` and `owner_tx` both use this as their common transaction wrapper. It calls observability functions only inside the function body to avoid import cycles, then hands the live connection back to the higher-level transaction context.

*Call graph*: called by 2 (owner_tx, workspace_tx); 7 external calls (ensure_future, shield, AsyncExitStack, begin, monotonic, emit_histogram, emit_metric).


##### `workspace_tx`  (lines 343–353)

```
async def workspace_tx() -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens the normal workspace-scoped database transaction. This is the safe default path for application code that should only see data for the current workspace.

**Data flow**: It checks that the app database URL has been initialized, gets the right engine for the current loop, and opens a transaction through `_opened`. It reads `current_workspace`; when a workspace ID is present and the database is PostgreSQL, it sets a transaction-local database setting named `app.workspace_id`. It yields the connection for queries, then the surrounding context finishes the transaction.

**Call relations**: Request, turn, or job code uses this after setting the ambient `current_workspace`. It relies on `_engine_for` for the correct pool and `_opened` for transaction timing and cleanup, then adds the workspace pin before handing the connection to callers.

*Call graph*: calls 2 internal fn (_engine_for, _opened); 1 external calls (text).


##### `owner_tx`  (lines 357–370)

```
async def owner_tx() -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens the special cross-workspace transaction used for controlled background enumeration. It deliberately does not pin a workspace ID.

**Data flow**: It chooses the owner URL and owner pool if an owner database was initialized; otherwise it uses the normal app URL and app pool. It verifies a URL exists, opens a transaction through `_opened`, and yields the connection without setting the workspace database setting.

**Call relations**: Background sweeps use this to find work across workspaces, then should re-enter each row’s workspace separately. It calls `_engine_for` to get the right engine and `_opened` to start and finish the transaction, but unlike `workspace_tx` it never applies a workspace boundary.

*Call graph*: calls 2 internal fn (_engine_for, _opened).


##### `apply_migrations`  (lines 373–400)

```
def apply_migrations(url: str, pack: str | None=None) -> None
```

**Purpose**: Runs Alembic database migrations, which update the database schema to the versions expected by the code. It combines core migrations with active extension migrations.

**Data flow**: It receives a database URL and optionally a pack name. It builds an Alembic configuration, adds the core migration folder plus extension migration folders, checks for duplicate revision IDs, then upgrades to all migration heads. If the target is SQLite, it finishes by sealing the SQLite journal mode with `_seal_sqlite_journal`.

**Call relations**: Startup tools, deployment jobs, or test setup call this before normal database use. It asks the extension loader for migration locations, uses Alembic to run the schema changes, and calls `_seal_sqlite_journal` only for SQLite databases.

*Call graph*: calls 1 internal fn (_seal_sqlite_journal); 6 external calls (__init__, upgrade, from_config, migration_locations, catch_warnings, simplefilter).


##### `_seal_sqlite_journal`  (lines 403–419)

```
def _seal_sqlite_journal(url: str) -> None
```

**Purpose**: Puts a migrated SQLite database file into the journal mode this project expects. This prevents the first later connection from needing an exclusive conversion lock.

**Data flow**: It receives a SQLite URL, extracts the database file path, and raises an error if the URL does not name a file. It opens the file with Python’s SQLite library, runs `pragma journal_mode=wal`, and closes the connection. The database file is left ready for later pooled connections.

**Call relations**: `apply_migrations` calls this after SQLite migrations. It exists because Alembic uses its own engine, so the normal SQLite connection setup hooks in `_build_engine` would not have run during migration.

*Call graph*: called by 1 (apply_migrations); 2 external calls (make_url, connect).


##### `_sqlite_on_connect`  (lines 422–428)

```
def _sqlite_on_connect(dbapi_connection: Any, _connection_record: Any) -> None
```

**Purpose**: Configures each new SQLite connection for the project’s expected behavior. It enables write-ahead logging, foreign key checks, and a wait time when the database is busy.

**Data flow**: It receives a raw SQLite database connection from SQLAlchemy’s connection event. It turns off the driver’s automatic transaction mode, opens a cursor, applies three SQLite pragmas, and closes the cursor. The connection is now configured before application code uses it.

**Call relations**: _build_engine registers this as a SQLite connection listener. SQLAlchemy calls it whenever a new SQLite connection is opened in the engine’s pool.


##### `_sqlite_begin_immediate`  (lines 431–433)

```
def _sqlite_begin_immediate(connection: sa.Connection) -> None
```

**Purpose**: Starts SQLite transactions with an immediate write lock. This turns some lock-upgrade deadlocks into orderly waiting.

**Data flow**: It receives a SQLAlchemy connection and sends the raw SQL command `begin immediate`. After that, SQLite has claimed the writer slot at the start of the transaction instead of trying to upgrade later.

**Call relations**: _build_engine registers this as a SQLite begin listener. SQLAlchemy calls it when a SQLite transaction starts, so local or test database writes behave more predictably under contention.

*Call graph*: 1 external calls (exec_driver_sql).


### `core/src/ufo/durability.py`

`io_transport` · `cross-cutting persistence and crash recovery`

DBOS stores workflow inputs, step results, and final errors in a system database so work can be replayed after a crash. The tricky part is that replay may happen with newer code than the code that originally saved the data. A normal Python pickle can bring back a Pydantic model as a raw object with exactly the old fields, without running the model’s normal validation. That is like restoring a form from an old filing cabinet and never checking it against the current form template; a newly required box may simply be missing until the program trips over it later.

This file fixes that for Pydantic BaseModel objects. When saving, it still uses pickle, but it teaches pickle to record a model as two things: the model class and the model’s field values. When loading, the model is rebuilt through Pydantic’s normal validation. That means new fields can receive current defaults, removed fields can be ignored, and truly missing required fields fail in a clearer place.

The file also gives this serializer a stable name, "ufo_pickle". That name matters because DBOS records which serializer wrote each database row. Clients must be built with this serializer to read rows written by the engine.

#### Function details

##### `replay_safe_client`  (lines 27–31)

```
def replay_safe_client(system_database_url: str) -> DBOSClient
```

**Purpose**: Creates the DBOS database client in the one supported way for this project: with the replay-safe serializer attached. This matters because database rows written with this serializer’s name need the same serializer available when they are read back.

**Data flow**: It takes a system database URL as input. It builds a ReplaySafeSerializer, passes both the URL and serializer into DBOSClient, and returns the ready-to-use client. The function does not itself read or write the database; it prepares the client that will.

**Call relations**: Startup or setup code calls this when it needs a DBOSClient. Inside, it constructs ReplaySafeSerializer and hands it to the external DBOSClient constructor so later persistence and replay use this file’s serialization rules.

*Call graph*: 2 external calls (__init__, DBOSClient).


##### `_rebuild`  (lines 34–35)

```
def _rebuild(model_class: type[BaseModel], fields: dict[str, object]) -> BaseModel
```

**Purpose**: Reconstructs a saved Pydantic model using the model class’s current validation rules. This is the key step that makes old saved data adapt to today’s model definition where possible.

**Data flow**: It receives a Pydantic model class and a dictionary of saved field values. It asks that class to validate those fields and create a fresh model instance. The result is a normal current-version model, with defaults and validation applied.

**Call relations**: This function is named in the pickle data produced by _ModelPickler.reducer_override. Later, when ReplaySafeSerializer.deserialize uses pickle to load the data, pickle calls _rebuild to recreate each saved Pydantic model.


##### `_ModelPickler.reducer_override`  (lines 39–42)

```
def reducer_override(self, obj: object) -> tuple[Callable[..., object], tuple[object, ...]]
```

**Purpose**: Tells pickle to save Pydantic models in a special replay-safe form instead of using pickle’s normal object snapshot behavior. For all other objects, it leaves pickle to behave normally.

**Data flow**: It receives each object that pickle is about to save. If the object is a Pydantic BaseModel, it turns it into instructions saying: call _rebuild later with this object’s class and field dictionary. If it is not a Pydantic model, it returns NotImplemented, which means the normal pickle machinery should decide how to save it.

**Call relations**: ReplaySafeSerializer.serialize uses _ModelPickler when writing data. During that save process, pickle calls reducer_override whenever it needs to know whether an object needs custom treatment.


##### `ReplaySafeSerializer.name`  (lines 48–49)

```
def name(self) -> str
```

**Purpose**: Returns the stable serializer name that DBOS stores alongside serialized rows. This name is how DBOS knows which serializer should read the data later.

**Data flow**: It takes no outside data beyond the serializer instance. It returns the constant string "ufo_pickle". It does not change anything.

**Call relations**: DBOS calls this method as part of its serializer interface. The name it returns connects rows written by ReplaySafeSerializer.serialize with later reads by ReplaySafeSerializer.deserialize.


##### `ReplaySafeSerializer.serialize`  (lines 51–54)

```
def serialize(self, data: object) -> str
```

**Purpose**: Turns a Python object into a text string that can be stored in the DBOS database. While doing so, it gives Pydantic models the special safe treatment defined in this file.

**Data flow**: It receives any Python object. It creates an in-memory byte buffer, uses _ModelPickler to pickle the object into bytes, then base64-encodes those bytes into a UTF-8 string safe for database storage. The output is that encoded string.

**Call relations**: DBOS calls this when it needs to persist workflow data. The method hands the actual pickling work to _ModelPickler, whose reducer_override changes how Pydantic models are recorded.

*Call graph*: 3 external calls (__init__, b64encode, BytesIO).


##### `ReplaySafeSerializer.deserialize`  (lines 56–57)

```
def deserialize(self, serialized_data: str) -> object
```

**Purpose**: Turns a stored database string back into the original Python data structure. For Pydantic models saved by this serializer, loading triggers the replay-safe rebuild path.

**Data flow**: It receives a base64 text string from storage. It decodes the text back into pickle bytes, then asks pickle to load the object. The result is the restored Python object, with any saved Pydantic models rebuilt through _rebuild.

**Call relations**: DBOS calls this when reading persisted workflow data, especially during replay after a crash. It relies on pickle’s stored instructions, including references to _rebuild, to recreate models correctly.

*Call graph*: 2 external calls (b64decode, loads).


### Shared schema definitions
The schema package home and canonical SQL table blueprint shared by the system.

### `core/src/ufo/schema/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Here, that means code elsewhere can refer to modules under `core/src/ufo/schema` using names like `ufo.schema...`.

Because the file is empty, it does not define any classes, functions, settings, or side effects. Nothing is computed when it is imported beyond Python creating the package itself. Its value is organizational: it gives the schema folder a clear place in the project’s module tree. You can think of it like a label on a filing cabinet drawer. The label does not contain the documents, but it lets the rest of the system find the drawer by name.

If this file were removed, imports may still work in some modern Python setups because Python supports “namespace packages,” but keeping it makes the package boundary explicit and compatible with tools or environments that expect traditional package markers.


### `core/src/ufo/schema/tables.py`

`data_model` · `database setup and any database write that relies on schema defaults`

This file is like the floor plan for the product’s database. Instead of writing separate table definitions for the local development database, SQLite, and the deployed database, Postgres, it uses SQLAlchemy, a Python library for describing databases, to define one neutral schema that can be used by both.

The tables describe the main objects the system cares about: workspaces, members, agents, conversations, turns in a conversation, incoming messages, billing ledger entries, spending caps, credentials, external connections, shared files, synced sources, pages, and access records. Each table says what information is stored, what fields are required, and how records relate to each other. For example, a conversation belongs to a workspace and an agent, and a turn belongs to a conversation.

The file also encodes many guardrails directly into the database. These include uniqueness rules, such as not allowing two members with the same email in one workspace, and check rules, such as only allowing known turn statuses like queued, running, done, or failed. These database-level rules matter because they protect the data even if a bug elsewhere tries to save something invalid.

The only function here supplies a default conversation audience. If new code creates a conversation without explicitly saying who can see it, the schema derives that from the member information when possible.

#### Function details

##### `_conversation_audience`  (lines 11–12)

```
def _conversation_audience(context: DefaultExecutionContext) -> str
```

**Purpose**: This function chooses the default audience for a new conversation when the caller did not provide one. In plain terms, it helps decide whether a conversation should be shared or tied to a specific member.

**Data flow**: It receives SQLAlchemy’s execution context, which is the database library’s bundle of information about the row currently being inserted. It reads the current insert values, looks for `member_id`, passes that member ID to `conversation_audience`, and turns the answer into text. The result becomes the value stored in the conversation’s `audience` column.

**Call relations**: SQLAlchemy calls this function automatically when inserting a conversation row that needs the Python-side default for `audience`. The function asks the execution context for the row’s current parameters, then hands the member ID to `ufo.audience.conversation_audience`, which contains the actual rule for building the audience value.

*Call graph*: 2 external calls (get_current_parameters, conversation_audience).
