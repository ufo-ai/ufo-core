# Persistence, Schema, and Durable Store Contracts  `stage-20` (cross-cutting infrastructure)

This stage is the system’s durable memory. It sits behind almost every other phase, from startup through the main work loop, because many parts need to save conversations, queue work, store files, and later read them back safely. The database doorway in db.py opens connections, runs migrations, meaning planned database shape changes, and keeps each workspace’s data separated. tables.py defines the full database map using SQLAlchemy, a tool that connects Python code to database tables, so development and production use the same layout. records.py defines the shared “paper forms” for turns, agents, user questions, credentials, billing, and queue state, so workers and request handlers agree on what each record means. transcript.py does the same for saved conversations and compacted conversation summaries. blob.py stores large byte data, using local disk or S3-style cloud storage through one common interface. The hosted-site store adds durable records for live generated sites, tying names, ports, owners, and permissions together so links stay safe and cannot be hijacked.

## Files in this stage

### Transcript object storage
Shared transcript formats are paired with the durable blob interface used to persist large conversation artifacts.

### `core/src/ufo/transcript.py`

`data_model` · `cross-cutting`

A conversation in this project is not just live memory. It is saved as a durable blob, meaning bytes stored somewhere that can be read back later. This file is the contract for those saved bytes. Without it, the writer might save a transcript one way while the debugger or evaluation tools try to read it another way, causing old conversations to become unreadable.

The main transcript shape is `Conversation`: a sequence number, the visible message list, and optional extra context such as the system prompt and injected text used for the model call. The file also defines how to turn that record into compact compressed bytes and back again. Compression uses LZ4, a fast compression format, so stored transcripts take less space but can still be quickly restored.

The second half covers compaction. Compaction is when a long conversation window is summarized and replaced with a shorter version so the model can keep working without carrying every old message. This file records the before window, the after window, and the structured summary that explains what was preserved. It also provides helpers to read one or all compaction records from the blob store, stopping when the next numbered record is missing.

#### Function details

##### `transcript_key`  (lines 37–38)

```
def transcript_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the storage path for the saved transcript of one conversation. It gives all writers and readers the same address for the same conversation.

**Data flow**: A conversation UUID goes in. The function inserts it into the standard transcript path. A string like a file path comes out, pointing to where that conversation's compressed message record belongs.

**Call relations**: Other parts of the system use this key when they need to store or fetch the main conversation transcript. This function does not read or write anything itself; it only provides the shared address.


##### `encode`  (lines 41–43)

```
def encode(conversation: Conversation) -> bytes
```

**Purpose**: Turns a `Conversation` record into compressed bytes that are ready to save. This protects the system from each caller inventing its own transcript encoding.

**Data flow**: A validated `Conversation` object goes in. The function converts it to plain data, serializes that data as JSON text, and compresses the JSON with LZ4. The result is a byte string suitable for blob storage.

**Call relations**: Transcript writers call this before saving a conversation. Inside, it uses the conversation model's dump operation to get standard data and `json.dumps` to make stable JSON before compression.

*Call graph*: 2 external calls (model_dump, dumps).


##### `decode`  (lines 46–50)

```
def decode(body: bytes) -> Conversation
```

**Purpose**: Restores compressed transcript bytes back into a validated `Conversation`. It turns unreadable or outdated stored data into a clear transcript-specific error.

**Data flow**: Compressed bytes go in. The function decompresses them, asks the `Conversation` model to validate the JSON, and returns a `Conversation` object. If decompression or validation fails, it raises `TranscriptDecodeError` instead of leaking a lower-level error.

**Call relations**: Transcript readers use this after fetching a stored transcript blob. It hands failures to `TranscriptDecodeError`, which tells callers that the saved transcript could not be understood.

*Call graph*: 1 external calls (__init__).


##### `compaction_key`  (lines 101–102)

```
def compaction_key(conversation_id: UUID, index: int, half: CompactionHalf) -> str
```

**Purpose**: Builds the storage path for one piece of one compaction record. A compaction is split into `before`, `after`, and `summary`, and this function gives each piece its exact address.

**Data flow**: A conversation UUID, a compaction number, and the requested half go in. The function combines them into the standard compressed JSON blob path. A storage key string comes out.

**Call relations**: `read_compaction_record` calls this three times when it needs to fetch the `before`, `after`, and `summary` blobs for the same compaction index.

*Call graph*: called by 1 (read_compaction_record).


##### `decode_compaction`  (lines 105–114)

```
def decode_compaction(index: int, before: bytes, after: bytes, summary: bytes) -> CompactionRecord
```

**Purpose**: Turns the three compressed blobs for a compaction into one easy-to-use `CompactionRecord`. It validates both message windows and the structured summary before returning them.

**Data flow**: A compaction index and three byte strings go in: the old window, the replacement window, and the summary. Each byte string is decompressed and parsed into its expected shape. A `CompactionRecord` comes out containing the index, the before messages, the after messages, and the summary; bad data becomes `TranscriptDecodeError`.

**Call relations**: `read_compaction_record` calls this after it has fetched all three stored pieces. This function then assembles those pieces into the single record that callers actually want to work with.

*Call graph*: called by 1 (read_compaction_record); 2 external calls (__init__, __init__).


##### `read_compaction_record`  (lines 117–128)

```
async def read_compaction_record(blob: BlobStore, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Reads one numbered compaction record from the blob store, if it exists. It returns `None` when that compaction number has not been written.

**Data flow**: A blob store, conversation UUID, and compaction index go in. The function builds the three keys for that index, fetches the `before`, `after`, and `summary` blobs, and decodes them into a `CompactionRecord`. If any requested blob is missing, it returns `None`.

**Call relations**: `read_compaction_records` uses this as its per-index reader. During the read, it relies on `compaction_key` to find the blobs, `BlobStore.get` to retrieve them, and `decode_compaction` to turn the raw bytes into a usable record.

*Call graph*: calls 3 internal fn (get, compaction_key, decode_compaction); called by 1 (read_compaction_records).


##### `read_compaction_records`  (lines 131–141)

```
async def read_compaction_records(blob: BlobStore, conversation_id: UUID) -> tuple[CompactionRecord, ...]
```

**Purpose**: Reads all compaction records for a conversation in order, starting with the oldest. It uses the convention that compaction numbers start at 1 and stop at the first missing number.

**Data flow**: A blob store and conversation UUID go in. The function repeatedly asks for compaction record 1, then 2, then 3, and so on. Each found record is collected; when a number is missing, the loop stops and a tuple of all found records comes out.

**Call relations**: This is the higher-level reader used when a caller wants the full compaction history. It delegates each individual fetch to `read_compaction_record`, which knows how to locate and decode the three stored pieces for one index.

*Call graph*: calls 1 internal fn (read_compaction_record).


### `core/src/ufo/blob.py`

`io_transport` · `cross-cutting storage operations during request handling and background work`

Many parts of this project need somewhere to put files or file-like records: attachments, transcript compaction records, sandbox artifacts, and other byte payloads. This file is the storage adapter for those blobs. A “blob” here just means a named pile of bytes, like a file stored under a slash-separated key.

The file defines a shared contract, `BlobStore`, that says what any blob backend must be able to do: write bytes, read bytes, check if something exists, delete it, stream it in chunks, stream writes in chunks, and list entries under a prefix. Streaming matters because some files may be too large to safely hold in memory all at once.

There are two concrete backends. `FilesystemBlobStore` turns blob keys into files under a root folder. It writes through a temporary file and then swaps it into place, like drafting a document on scrap paper before replacing the official copy. It also protects the root folder so a bad key cannot escape into the rest of the machine’s filesystem.

`S3BlobStore` talks to S3 or an S3-compatible service. It reuses one async S3 client per event loop because creating clients is expensive and clients are tied to the loop they were made on. It also supports multipart uploads for large streams and presigned upload URLs so an isolated sandbox can upload exactly one approved file directly to storage.

#### Function details

##### `BlobStore.put`  (lines 50–50)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Defines the promise that a blob store can save a full byte string under a key. Code uses this when the whole payload is already in memory and does not need chunk-by-chunk streaming.

**Data flow**: A caller provides a storage key and bytes → the chosen backend stores those bytes at that key → nothing is returned, but the object should be available for later reads.

**Call relations**: This is part of the shared `BlobStore` protocol. The actual work is done by `FilesystemBlobStore.put` or `S3BlobStore.put`, depending on which backend `blob_store_for` created.


##### `BlobStore.get`  (lines 52–52)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Defines the promise that a blob store can read a complete object back as bytes. It is used when callers expect the stored item to be small enough or convenient enough to load all at once.

**Data flow**: A caller provides a key → the backend looks up the stored object → the bytes come back, or a missing-key error is raised if the object is not there.

**Call relations**: Higher-level code such as transcript compaction reading and Slack identity reading calls through this protocol. Those callers do not need to know whether the bytes came from disk or S3.

*Call graph*: called by 2 (read_compaction_record, read_identity).


##### `BlobStore.exists`  (lines 54–54)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Defines the promise that a blob store can answer whether a key is present without reading the whole object. This is useful for quick checks before deciding what to load or create.

**Data flow**: A caller provides a key → the backend checks for an object at that key → it returns true if present and false if absent.

**Call relations**: Slack identity reading calls this through the protocol before or around fetching stored identity data. The concrete filesystem and S3 methods provide the backend-specific checks.

*Call graph*: called by 1 (read_identity).


##### `BlobStore.delete`  (lines 56–58)

```
async def delete(self, key: str) -> None
```

**Purpose**: Defines the promise that a blob store can remove an object. Deleting something that is already gone is deliberately harmless, which makes retrying cleanup safe.

**Data flow**: A caller provides a key → the backend asks storage to remove that object → nothing is returned, and an absent object is treated as already deleted.

**Call relations**: This is the protocol-level shape used by storage users. The filesystem backend unlinks a file, while the S3 backend sends a delete request.


##### `BlobStore.get_stream`  (lines 60–60)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines the promise that a blob store can read an object piece by piece. This avoids loading a large file into memory all at once.

**Data flow**: A caller provides a key → the backend opens the stored object → chunks of bytes are yielded one after another until the object is fully read.

**Call relations**: This protocol method lets higher-level code treat local files and S3 objects the same way when it needs streaming. The concrete backends supply the chunking behavior.


##### `BlobStore.put_stream`  (lines 62–62)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Defines the promise that a blob store can write an object from a stream of byte chunks. This is the matching write path for large payloads.

**Data flow**: A caller provides a key and an async sequence of chunks → the backend writes each chunk in order → when the stream finishes, the stored object is complete.

**Call relations**: This is the shared entry shape for streamed uploads. The filesystem backend writes to a temporary file, while the S3 backend may use a simple upload or multipart upload depending on size.


##### `BlobStore.list`  (lines 64–68)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Defines the promise that a blob store can list stored objects under a required key prefix. It is intentionally bounded so a caller cannot accidentally scan the entire store.

**Data flow**: A caller provides a non-empty prefix → the backend finds matching objects and gathers their key, size, and modification time → it returns a sorted, capped tuple of entries.

**Call relations**: This protocol method supports read views that need to walk a known area, such as records for one conversation. The filesystem and S3 implementations each translate the prefix into their own listing mechanism.


##### `FilesystemBlobStore.put`  (lines 77–82)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Stores a complete byte payload as a local file under the blob root. It writes safely by using a temporary file first, then replacing the final file in one step.

**Data flow**: A key and bytes come in → `_resolve` turns the key into a safe path, parent folders are created, bytes are written to a unique temporary file, and that file replaces the target → the final file now contains the new bytes.

**Call relations**: This is the filesystem version of `BlobStore.put`. It relies on `_resolve` to prevent unsafe paths and uses background threads for blocking file operations so the async event loop is not frozen.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.get`  (lines 84–89)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a complete blob from the local filesystem. If the file is missing, it raises the project’s `BlobNotFound` error instead of leaking a raw filesystem error.

**Data flow**: A key comes in → `_resolve` maps it to a safe file path → the file bytes are read in a thread → bytes are returned, or a missing file becomes `BlobNotFound`.

**Call relations**: This is the filesystem version of `BlobStore.get`. Callers using the protocol get the same missing-object behavior they would get from the S3 backend.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.exists`  (lines 91–93)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a local blob file exists. It gives callers a cheap yes-or-no answer without reading the file contents.

**Data flow**: A key comes in → `_resolve` maps it safely under the root folder → the filesystem is asked whether that path is a file → a boolean answer is returned.

**Call relations**: This is the filesystem version of `BlobStore.exists`. It shares the same path-safety guard used by the other filesystem operations.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.delete`  (lines 95–97)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes a local blob file if it exists. If the file is already absent, it quietly succeeds so cleanup can be retried safely.

**Data flow**: A key comes in → `_resolve` turns it into a safe path → the file is unlinked with missing files allowed → no value is returned.

**Call relations**: This is the filesystem version of `BlobStore.delete`. Like other file operations here, it runs the blocking disk call in a thread.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.get_stream`  (lines 99–112)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads a local blob in fixed-size chunks. This is useful for large files because memory only holds one chunk at a time.

**Data flow**: A key comes in → `_resolve` finds the safe path → the file is opened for reading → chunks are read and yielded until the file ends → the file handle is closed even if reading stops early.

**Call relations**: This is the filesystem version of `BlobStore.get_stream`. It turns a normal blocking file into an async stream by doing each file operation through `asyncio.to_thread`.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.put_stream`  (lines 114–127)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes a local blob from incoming chunks. It protects readers from seeing a half-written file by writing to a temporary file and replacing the real file only after all chunks arrive.

**Data flow**: A key and chunk stream come in → `_resolve` chooses a safe target path, folders are created, chunks are written to a unique temporary file → on success the temporary file replaces the target; on failure it is removed.

**Call relations**: This is the filesystem version of `BlobStore.put_stream`. It uses the same atomic-write idea as `FilesystemBlobStore.put`, but feeds the file gradually from an async chunk source.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.list`  (lines 129–132)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists local blob files under a non-empty prefix. Requiring a prefix prevents accidental whole-store scans.

**Data flow**: A prefix comes in → if it is empty, an error is raised → otherwise `_walk` is run in a thread to inspect matching files → a tuple of `BlobEntry` records comes back.

**Call relations**: This is the filesystem version of `BlobStore.list`. It delegates the actual directory walking to `_walk` so the async method can keep blocking filesystem work off the event loop.

*Call graph*: 1 external calls (to_thread).


##### `FilesystemBlobStore._walk`  (lines 134–155)

```
def _walk(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Walks the local directory tree and builds the list of blob entries for a prefix. It filters out temporary files so unfinished writes do not appear in listings.

**Data flow**: A prefix comes in → the method finds the base directory to inspect, walks files below it, converts each matching file path back into a blob key, reads size and modified time → sorted, capped `BlobEntry` records are returned.

**Call relations**: `FilesystemBlobStore.list` calls this inside a worker thread. It uses `_resolve` to keep the starting point under the blob root and creates `BlobEntry` objects that match the protocol’s listing shape.

*Call graph*: calls 1 internal fn (_resolve); 4 external calls (__init__, fromtimestamp, walk, Path).


##### `FilesystemBlobStore._resolve`  (lines 157–162)

```
def _resolve(self, key: str) -> Path
```

**Purpose**: Turns a blob key into a real filesystem path and makes sure it stays inside the configured storage root. This is the safety gate that stops keys like `../secret` from reaching outside the blob store.

**Data flow**: A key comes in → it is joined to the root folder and normalized to an absolute path → if the result is the root itself or outside the root, an error is raised; otherwise the safe path is returned.

**Call relations**: All filesystem read, write, delete, stream, and walk operations call this before touching disk. It is the shared guardrail for the local backend.

*Call graph*: called by 7 (_walk, delete, exists, get, get_stream, put, put_stream).


##### `_is_missing_key`  (lines 165–166)

```
def _is_missing_key(error: ClientError) -> bool
```

**Purpose**: Recognizes the different S3 error codes that all mean “that object is not here.” This lets the rest of the file treat missing S3 objects consistently.

**Data flow**: An S3 `ClientError` comes in → the function reads the error code from the response → it returns true if the code is one of the known missing-object codes, otherwise false.

**Call relations**: `S3BlobStore.get`, `S3BlobStore.exists`, and `S3BlobStore.get_stream` call this when S3 returns an error. It decides whether they should translate the error into `BlobNotFound` or `False`, or let a real storage failure bubble up.

*Call graph*: called by 3 (exists, get, get_stream).


##### `S3BlobStore.put`  (lines 185–187)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Stores a complete byte payload as an S3 object. It is the simple upload path for data already available in memory.

**Data flow**: A key and bytes come in → `_client` supplies the S3 client for the current event loop → the bytes are sent to S3 with the configured bucket and key → nothing is returned on success.

**Call relations**: This is the S3 version of `BlobStore.put`. It depends on `_client` for efficient client reuse and consistent S3 signing settings.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get`  (lines 189–199)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a complete S3 object into memory. If S3 says the key is missing, it raises `BlobNotFound` so callers see the same kind of error as with local storage.

**Data flow**: A key comes in → `_client` provides an S3 client → S3 is asked for the object → the response body is read fully and returned as bytes; missing-key errors are translated to `BlobNotFound`.

**Call relations**: This is the S3 version of `BlobStore.get`. It uses `_is_missing_key` to separate ordinary absence from other S3 problems that should still be reported.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.exists`  (lines 201–209)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether an S3 object exists without downloading it. It uses S3’s metadata lookup for a quick yes-or-no answer.

**Data flow**: A key comes in → `_client` provides an S3 client → S3 is asked for object metadata → success returns true; a recognized missing-key error returns false; other errors are raised.

**Call relations**: This is the S3 version of `BlobStore.exists`. It uses `_is_missing_key` for the same missing-object interpretation used by S3 reads.

*Call graph*: calls 2 internal fn (_client, _is_missing_key).


##### `S3BlobStore.delete`  (lines 211–213)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes an object from the S3 bucket. S3 delete operations are naturally safe to repeat for already-missing objects.

**Data flow**: A key comes in → `_client` supplies an S3 client → a delete request is sent for that bucket and key → no value is returned.

**Call relations**: This is the S3 version of `BlobStore.delete`. It is one of the simple operations that only needs a client and a single S3 request.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get_stream`  (lines 215–226)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads an S3 object as a stream of chunks. This lets the system pass around large objects without holding the whole object in memory.

**Data flow**: A key comes in → `_client` gets an S3 client → S3 returns a response body → chunks are yielded from that body until it ends; missing keys become `BlobNotFound`.

**Call relations**: This is the S3 version of `BlobStore.get_stream`. Like `S3BlobStore.get`, it calls `_is_missing_key` to normalize missing-object errors.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.put_stream`  (lines 228–272)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes a stream of chunks to S3, using multipart upload when the data grows large. Multipart upload is S3’s way of sending a big object as several numbered pieces and then asking S3 to assemble them.

**Data flow**: A key and chunk stream come in → chunks are gathered until they reach the multipart part size → small streams are uploaded with one normal put; larger streams start a multipart upload, send each part, then complete the upload → if anything fails after multipart starts, the unfinished upload is aborted.

**Call relations**: This is the S3 version of `BlobStore.put_stream`. It uses `_client` for all S3 calls and contains the cleanup logic that prevents failed large uploads from leaving abandoned parts behind.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_put`  (lines 274–298)

```
async def presigned_put(self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary upload URL that lets an external sandbox put exactly one measured file into S3. The URL is signed with the expected size and SHA-256 checksum, so different bytes or a different length will be rejected by S3.

**Data flow**: A key, expected byte size, expected checksum, and time-to-live come in → `_client` supplies the signing-capable S3 client → a presigned PUT URL is generated → the URL string is returned.

**Call relations**: This method is specific to the S3 backend and is used when the sandbox should upload directly instead of sending file bytes through the main service. It relies on `_client` so the URL is signed with the same endpoint and addressing rules the client actually uses.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.put_host`  (lines 300–311)

```
async def put_host(self) -> str
```

**Purpose**: Returns the hostname that a presigned upload URL will contact. The system can allow that hostname through an egress proxy so the sandbox can reach only the intended upload destination.

**Data flow**: No caller data beyond the store configuration is needed → `_client` reveals its actual endpoint URL → the hostname is extracted, with the bucket added for normal AWS virtual-hosted addressing → the bare hostname is returned.

**Call relations**: This works alongside `S3BlobStore.presigned_put`: one method creates the upload URL, and this one tells the network policy which host that URL will use. It uses the real client endpoint instead of rebuilding it by hand, avoiding mismatches.

*Call graph*: calls 1 internal fn (_client); 1 external calls (urlsplit).


##### `S3BlobStore.list`  (lines 313–330)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists S3 objects under a non-empty prefix and returns their keys, sizes, and modification times. The result is capped so listing stays bounded.

**Data flow**: A prefix comes in → an empty prefix raises an error → `_client` supplies an S3 client and paginator → pages of S3 listing results are read until enough entries are collected or results end → capped `BlobEntry` records are returned.

**Call relations**: This is the S3 version of `BlobStore.list`. It creates the same `BlobEntry` shape as the filesystem backend, so callers can consume listings without knowing where the blobs live.

*Call graph*: calls 1 internal fn (_client); 1 external calls (__init__).


##### `S3BlobStore._client`  (lines 332–358)

```
async def _client(self) -> AioBaseClient
```

**Purpose**: Returns the reusable S3 client for the current async event loop, creating it if needed. Reusing clients avoids repeated expensive setup and respects that these async clients are tied to the loop they were created on.

**Data flow**: The current running event loop is read → the method checks the per-loop client cache → if a client exists, it is returned; otherwise a new S3 client is created with fixed signing and addressing settings, stored in the cache, and returned.

**Call relations**: Every S3 operation calls this before talking to storage. If two tasks race and create an extra client, the loser is closed and a log message is written if that cleanup fails.

*Call graph*: called by 9 (delete, exists, get, get_stream, list, presigned_put, put, put_host, put_stream); 3 external calls (get_session, get_running_loop, log).


##### `blob_store_for`  (lines 361–373)

```
def blob_store_for(config: BlobConfig) -> FilesystemBlobStore | S3BlobStore
```

**Purpose**: Builds the correct blob store from configuration. It is the small factory that turns settings into either a local filesystem store or an S3 store.

**Data flow**: A `BlobConfig` comes in → the backend name is inspected → required fields such as filesystem root or S3 bucket are checked → a configured `FilesystemBlobStore` or `S3BlobStore` is returned.

**Call relations**: Startup or setup code calls this when it needs a `BlobStore` for the rest of the application. After this point, callers can use the common blob interface without branching on the backend.

*Call graph*: 2 external calls (__init__, __init__).


### Database access and schema
The application database doorway, shared queue record contracts, and SQLAlchemy table map define the durable relational store.

### `core/src/ufo/db.py`

`io_transport` · `startup, request handling, background jobs, migrations, teardown`

This module is the project’s tenancy boundary: the place that keeps one customer workspace from accidentally reading another workspace’s rows. The main idea is simple: code should not open database sessions directly. It should go through `workspace_tx`, which starts a database transaction and, for PostgreSQL, tells the database which workspace is active for that transaction. PostgreSQL row-level security, meaning rules inside the database that filter rows automatically, then uses that setting to allow only matching rows.

The file also has `owner_tx`, a special escape hatch for background jobs that need to list work across all workspaces. That path must only be used to find identifiers, then the caller re-enters the normal workspace-scoped path before reading real workspace data.

Connections are pooled, like keeping a small set of reusable phone lines to the database instead of dialing a new one for every request. Because async database connections belong to the event loop that created them, this file keeps separate engine pools per event loop. It also knows how to configure PostgreSQL and SQLite differently, check at startup that the database is reachable, run Alembic migrations to update the schema, and dispose of connections during shutdown or tests. Without this file, workspace isolation, connection limits, startup health checks, and cleanup would be scattered and much easier to get wrong.

#### Function details

##### `_build_engine`  (lines 98–107)

```
def _build_engine(url: str, pool: _Pool) -> AsyncEngine
```

**Purpose**: Creates a SQLAlchemy async engine, which is the object used to open database connections. It also adds SQLite-specific setup hooks when the database is SQLite.

**Data flow**: It receives a database URL and a pool description. It asks `_pool_kwargs` what connection settings to use, builds the engine, and, if the engine is for SQLite, attaches small setup routines for new connections and transaction starts. It returns the ready-to-use engine.

**Call relations**: `_engine_for` calls this when an event loop first needs an engine, and `verify_db_reachable` calls it to make a temporary engine for a startup connectivity check. It delegates the detailed connection settings to `_pool_kwargs`.

*Call graph*: calls 1 internal fn (_pool_kwargs); called by 2 (_engine_for, verify_db_reachable); 1 external calls (create_async_engine).


##### `_pool_kwargs`  (lines 110–121)

```
def _pool_kwargs(url: str, pool: _Pool) -> dict[str, Any]
```

**Purpose**: Chooses the right connection-pool settings for the kind of database being used. PostgreSQL gets a bounded reusable pool, while SQLite gets no pool because SQLite has different locking behavior.

**Data flow**: It receives the database URL and pool limits. It parses the URL, checks whether the backend is SQLite, and returns either SQLite’s no-pool setting or PostgreSQL-style pool size, timeout, recycle, health-check, and driver settings.

**Call relations**: `_build_engine` calls this just before creating an engine. When the database is not SQLite, it asks `_driver_kwargs` for the driver-specific details.

*Call graph*: calls 1 internal fn (_driver_kwargs); called by 1 (_build_engine); 1 external calls (make_url).


##### `_driver_kwargs`  (lines 124–151)

```
def _driver_kwargs(driver: str, pool: _Pool) -> dict[str, Any]
```

**Purpose**: Translates the project’s connection intent into the exact options required by the database driver. It sets connection timeouts, labels connections with an application name, and disables cached prepared statements that can break after live schema changes.

**Data flow**: It receives a driver name and a pool description. If the driver is `asyncpg`, it returns options in asyncpg’s format; otherwise it returns options in psycopg’s format. The output is a dictionary that SQLAlchemy passes to the driver when opening connections.

**Call relations**: `_pool_kwargs` calls this while building PostgreSQL connection settings. It is the small adapter that keeps `_pool_kwargs` from pretending all drivers spell their options the same way.

*Call graph*: called by 1 (_pool_kwargs).


##### `_engine_for`  (lines 154–170)

```
def _engine_for(url: str, pool: _Pool) -> AsyncEngine
```

**Purpose**: Finds or creates the engine for the current asynchronous event loop and database URL. This matters because async database connections are tied to the event loop that opened them.

**Data flow**: It reads the currently running event loop, combines that loop with the URL as a lookup key, removes records for loops that are already closed, and returns the existing engine if one is registered. If not, it builds a new engine with `_build_engine`, stores it, and returns it.

**Call relations**: `workspace_tx` and `owner_tx` call this whenever they need a transaction. If no engine exists yet for that loop, `_engine_for` hands off to `_build_engine` to create one.

*Call graph*: calls 1 internal fn (_build_engine); called by 2 (owner_tx, workspace_tx); 1 external calls (get_running_loop).


##### `init_db`  (lines 173–177)

```
def init_db(url: str) -> None
```

**Purpose**: Records the main application database URL for later use. This is called once by startup code before anything tries to open a workspace transaction.

**Data flow**: It receives a database URL. If a URL is already set, it raises an error to prevent accidental reconfiguration; otherwise it stores the URL in module-level state. It returns nothing.

**Call relations**: This function prepares the module for later calls to `workspace_tx`, `owner_tx`, and `verify_db_reachable`. It does not open a connection itself; the actual engine is created lazily later.


##### `init_owner_db`  (lines 180–189)

```
def init_owner_db(url: str) -> None
```

**Purpose**: Records the special owner-role database URL used for cross-workspace background enumeration. This URL can bypass normal row-level security when the full serve process has that permission.

**Data flow**: It receives an owner database URL. If one is already set, it raises an error; otherwise it stores the URL. It returns nothing and does not immediately connect to the database.

**Call relations**: `owner_tx` later uses this stored URL when it needs the owner pool. If this function was never called, `owner_tx` falls back to the normal application database URL instead.


##### `verify_db_reachable`  (lines 192–212)

```
async def verify_db_reachable() -> None
```

**Purpose**: Checks during startup that the configured database or databases can actually be reached. This prevents a service from appearing ready while every real request would fail on its first database access.

**Data flow**: It looks at the stored application and owner URLs. If none are configured, it raises an error. For each configured URL, it builds a temporary engine, opens and closes one connection, then disposes that temporary engine so no pooled connection remains.

**Call relations**: Startup code can call this after `init_db` or `init_owner_db`. It uses `_build_engine` directly rather than publishing an engine through `_engine_for`, because this is only a one-time reachability check.

*Call graph*: calls 1 internal fn (_build_engine).


##### `dispose_db`  (lines 215–238)

```
async def dispose_db() -> None
```

**Purpose**: Shuts down all registered database engines and clears the stored database URLs. It is used during teardown, command cleanup, and tests so old connections do not leak into the next run.

**Data flow**: It clears the application and owner URLs first. Then it looks through both engine registries: engines owned by the current event loop are disposed immediately, while engines owned by other still-open loops are handed back to those loops for disposal. It returns nothing and suppresses cleanup races with already-closed loops.

**Call relations**: Teardown code calls this when the whole database layer should be reset. For engines on other loops, it calls `_hand_off` because only the owning loop can safely close those async connections.

*Call graph*: calls 1 internal fn (_hand_off); 1 external calls (get_running_loop).


##### `_hand_off`  (lines 241–248)

```
def _hand_off(loop: asyncio.AbstractEventLoop, engine: AsyncEngine) -> None
```

**Purpose**: Asks another event loop to dispose of an engine it owns. This avoids trying to close async database connections from the wrong loop.

**Data flow**: It receives an event loop and an engine. It schedules `_dispose_on_this_loop` to run on that loop using a thread-safe scheduling call. If the loop closed during the attempt, it quietly gives up because there is no live loop left to do the cleanup.

**Call relations**: `dispose_db` calls this for engines that belong to another event loop. It hands the actual disposal work to `_dispose_on_this_loop` on the correct loop.

*Call graph*: called by 1 (dispose_db); 1 external calls (call_soon_threadsafe).


##### `_dispose_on_this_loop`  (lines 251–258)

```
def _dispose_on_this_loop(engine: AsyncEngine) -> None
```

**Purpose**: Runs engine disposal on the event loop that owns the engine’s connections. It also keeps the disposal task alive until it finishes.

**Data flow**: It receives an async engine, starts `engine.dispose()` as an asynchronous task, stores that task in a module-level set so it is not garbage-collected early, and removes it from the set when done. It returns nothing immediately.

**Call relations**: `_hand_off` schedules this on another event loop. It is the final step in cross-loop cleanup after `dispose_db` removes the engine from the registry.

*Call graph*: 2 external calls (ensure_future, dispose).


##### `dispose_loop_engines`  (lines 261–271)

```
async def dispose_loop_engines() -> None
```

**Purpose**: Disposes only the engines that belong to the currently running event loop, while leaving the configured database URLs intact. This is useful for short-lived event loops that are about to close.

**Data flow**: It reads the current event loop, scans the application and owner engine registries, removes entries for that loop, and disposes those engines. Other loops’ engines and the saved URLs are left alone.

**Call relations**: Code that creates temporary event loops can call this before the loop ends. Unlike `dispose_db`, it does not reset the whole database module; it only prevents this loop’s connections from being abandoned.

*Call graph*: 1 external calls (get_running_loop).


##### `_opened`  (lines 275–304)

```
async def _opened(engine: AsyncEngine, path: str) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens a database transaction and records metrics about how long it took, including failures to get a connection. It is the shared transaction wrapper used by both workspace and owner transactions.

**Data flow**: It receives an engine and a path label such as `workspace` or `owner`. It measures the time needed to begin a transaction, emits metrics if acquiring a connection fails, emits a timing histogram either way, and yields the open connection to the caller. When the caller leaves the context, the transaction context closes normally.

**Call relations**: `workspace_tx` and `owner_tx` call this to get their actual transaction connection. It calls observability functions only inside the function body to avoid import cycles, then hands the live connection back to its caller.

*Call graph*: called by 2 (owner_tx, workspace_tx); 5 external calls (AsyncExitStack, begin, monotonic, emit_histogram, emit_metric).


##### `workspace_tx`  (lines 308–318)

```
async def workspace_tx() -> AsyncIterator[AsyncConnection]
```

**Purpose**: Provides the normal workspace-scoped database transaction. This is the safe path most application code should use when reading or writing workspace data.

**Data flow**: It checks that the main database URL was initialized, gets the current loop’s application engine through `_engine_for`, and opens a transaction through `_opened`. It reads the ambient `current_workspace` context variable; if a workspace is set and the database is PostgreSQL, it sets the transaction-local database setting `app.workspace_id`. It then yields the connection to the caller.

**Call relations**: Request handlers, jobs, or other boundary code first set the current workspace, then call `workspace_tx`. This function uses `_engine_for` for the right engine and `_opened` for the measured transaction, then applies the workspace setting before caller SQL runs.

*Call graph*: calls 2 internal fn (_engine_for, _opened); 1 external calls (text).


##### `owner_tx`  (lines 322–335)

```
async def owner_tx() -> AsyncIterator[AsyncConnection]
```

**Purpose**: Provides the special cross-workspace transaction used to enumerate work across workspaces. It deliberately does not set a workspace, so callers must not use it to read tenant data beyond what is needed to re-scope later work.

**Data flow**: It chooses the owner URL and owner pool if configured; otherwise it uses the application URL and pool. It verifies that some URL exists, gets the current loop’s engine through `_engine_for`, opens a transaction through `_opened`, and yields the connection without setting any workspace database variable.

**Call relations**: Background sweep code uses this to find rows across workspaces, then switches back into a workspace-scoped flow for each row. It shares `_engine_for` and `_opened` with `workspace_tx`, but intentionally skips the workspace-setting step.

*Call graph*: calls 2 internal fn (_engine_for, _opened).


##### `apply_migrations`  (lines 338–365)

```
def apply_migrations(url: str, pack: str | None=None) -> None
```

**Purpose**: Runs database schema migrations, which are versioned changes that create or update tables and other database structures. It combines core migrations with active extension migrations so the whole selected system reaches the right schema version.

**Data flow**: It receives a database URL and optionally an extension pack name. It builds an Alembic configuration, points it at the core migration folder plus extension migration folders, validates that migration revision identifiers do not collide, and runs Alembic upgrade to all current heads. If the target is SQLite, it then calls `_seal_sqlite_journal` to leave the file in the expected journal mode.

**Call relations**: Command-line tools, startup jobs, or test fixtures call this before normal database use. It asks the extension loader for migration locations, delegates schema changes to Alembic, and uses `_seal_sqlite_journal` for a SQLite-specific finishing step.

*Call graph*: calls 1 internal fn (_seal_sqlite_journal); 6 external calls (__init__, upgrade, from_config, migration_locations, catch_warnings, simplefilter).


##### `_seal_sqlite_journal`  (lines 368–384)

```
def _seal_sqlite_journal(url: str) -> None
```

**Purpose**: Finishes a migrated SQLite database file by switching it to write-ahead logging mode before normal engines open it. This avoids later lock surprises during the first real connection.

**Data flow**: It receives a SQLite URL, extracts the database file path, raises an error if no file is named, opens the file with Python’s SQLite library, runs `pragma journal_mode=wal`, and closes the connection. The database file is left in the journal mode the rest of this module expects.

**Call relations**: `apply_migrations` calls this after Alembic has migrated a SQLite database. It is separate because Alembic creates its own engine and therefore does not run this module’s normal SQLite connection setup.

*Call graph*: called by 1 (apply_migrations); 2 external calls (make_url, connect).


##### `_sqlite_on_connect`  (lines 387–393)

```
def _sqlite_on_connect(dbapi_connection: Any, _connection_record: Any) -> None
```

**Purpose**: Applies required SQLite settings whenever this module opens a new SQLite connection. These settings make SQLite behave more predictably for this application.

**Data flow**: It receives the raw SQLite connection and its connection record. It disables the driver’s default transaction behavior, turns on write-ahead logging, enables foreign-key checks, sets a busy timeout so lock waits do not fail immediately, and closes the temporary cursor. It returns nothing.

**Call relations**: `_build_engine` registers this as a connection hook for SQLite engines. SQLAlchemy calls it automatically whenever a new SQLite database connection is created.


##### `_sqlite_begin_immediate`  (lines 396–398)

```
def _sqlite_begin_immediate(connection: sa.Connection) -> None
```

**Purpose**: Starts SQLite transactions with an immediate write lock. This turns possible lock-upgrade deadlocks into ordinary waiting in line.

**Data flow**: It receives a SQLAlchemy connection and sends the SQLite command `begin immediate`. After that, the transaction has claimed SQLite’s single-writer slot up front instead of discovering later that it cannot upgrade its lock.

**Call relations**: `_build_engine` registers this as a transaction-begin hook for SQLite engines. SQLAlchemy calls it automatically when a SQLite transaction begins.

*Call graph*: 1 external calls (exec_driver_sql).


### `core/src/ufo/schema/records.py`

`data_model` · `cross-cutting`

A “turn” is one unit of conversation work: someone says something, the system queues it, an agent works on it, and the turn eventually finishes, fails, or is cancelled. This file gives that process a clear paper form. It defines the allowed status words, the fields every turn must carry, and the extra structured records used when the agent asks the user a question, requests credentials, connects an account, reports final output, or records token usage and cost.

The file matters because many different parts of the system need to agree on the same facts. A surface such as Slack or another client creates a turn. A worker later reads it. Billing code may record usage. UI code may render a final question or credential prompt. Without these shared models, each part could interpret the same row differently.

Most records are Pydantic models, meaning they validate incoming data when objects are built. For example, TurnContext cleans user-supplied sender and source text so it cannot accidentally imitate internal markup, and it rejects unknown time zones early. Turn also checks that unfinished turns do not have a final result, while finished turns do. The helper ID functions create stable UUIDs from meaningful inputs, so retries produce the same IDs instead of duplicate work.

#### Function details

##### `turn_id_for`  (lines 68–70)

```
def turn_id_for(workspace_id: UUID, conversation_id: UUID, seq: int) -> UUID
```

**Purpose**: Creates the permanent ID for a conversation turn from its workspace, conversation, and sequence number. This makes the same turn get the same ID every time, which helps retries avoid creating duplicates.

**Data flow**: It receives a workspace ID, a conversation ID, and a turn number. It combines them into one stable text key and turns that key into a UUID. The result is a repeatable turn ID: the same inputs always produce the same output.

**Call relations**: When a new turn is admitted or replayed, other code can call this function to name that turn consistently. It hands the final ID to storage or workflow code so the turn and its background workflow share one identity.

*Call graph*: 1 external calls (uuid5).


##### `ledger_id_for`  (lines 73–78)

```
def ledger_id_for(workspace_id: UUID, turn_id: UUID, dimension: str, attempt: str='') -> UUID
```

**Purpose**: Creates a stable billing ledger ID for one kind of usage on one turn and one run attempt. It prevents the same attempt from being billed twice while still allowing resumed attempts to be recorded separately.

**Data flow**: It receives a workspace ID, turn ID, billing dimension, and optional attempt ID. It folds those values into a stable text key and converts that key into a UUID. The returned UUID identifies exactly one billing write for that turn, category, and attempt.

**Call relations**: Billing or usage-recording code can call this when it needs to write token or cost information. The function hands back an ID that lets repeated execution collapse onto the same ledger entry, while a later resumed run can use a different attempt value and therefore a different ledger entry.

*Call graph*: 1 external calls (uuid5).


##### `TurnContext._tag_safe_line`  (lines 194–198)

```
def _tag_safe_line(cls, value: str | None) -> str | None
```

**Purpose**: Cleans sender and source text before it is stored in a turn context. It makes sure surface-provided text stays on one plain line and cannot pretend to be internal markup.

**Data flow**: It receives either a text value or nothing. If there is no value, it returns nothing. If text is present, it removes angle brackets, collapses extra whitespace and line breaks into single spaces, and returns the cleaned line; if nothing meaningful remains, it returns nothing.

**Call relations**: Pydantic calls this validator automatically when a TurnContext is created or validated. It protects later code that renders the context into an internal prompt-like format, so user-supplied sender or source text cannot forge tags or structure.


##### `TurnContext._known_zone`  (lines 202–209)

```
def _known_zone(cls, value: str | None) -> str | None
```

**Purpose**: Checks that a timezone name in the turn context is real. This catches bad timezone values at the boundary, before a worker is halfway through processing the turn.

**Data flow**: It receives a timezone string or nothing. If the value is missing, it passes it through. If a string is present, it asks the system timezone database whether that name exists; valid names are returned unchanged, and unknown names become a validation error.

**Call relations**: Pydantic calls this validator automatically while building a TurnContext. It uses the standard ZoneInfo lookup to verify the timezone, then hands a clean context to the rest of the system.

*Call graph*: 1 external calls (ZoneInfo).


##### `Turn._aware_utc`  (lines 234–239)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Ensures turn timestamps always carry timezone information. This avoids a subtle bug where a timestamp read back without a timezone marker could be mistaken for local time.

**Data flow**: It receives a datetime value or nothing for created_at or updated_at. If the value is missing, it stays missing. If it already has timezone information, it is returned unchanged. If it has no timezone marker, the function marks it as UTC and returns that corrected value.

**Call relations**: Pydantic calls this validator automatically when a Turn is created or loaded. It compensates for storage layers that may drop timezone markers, then gives the rest of the code timestamps that can be compared and converted safely.

*Call graph*: 1 external calls (replace).


##### `Turn._terminal_matches_status`  (lines 242–247)

```
def _terminal_matches_status(self) -> 'Turn'
```

**Purpose**: Checks that a turn’s final result matches its status. Unfinished turns must not have a terminal frame, and finished turns must have one whose status says the same thing as the turn itself.

**Data flow**: It looks at the completed Turn object after its fields have been parsed. If the status is queued, running, or parked, terminal must be empty. If the status is done, failed, or cancelled, terminal must be present and must repeat that same status. If the rule holds, the Turn is returned; otherwise validation fails.

**Call relations**: Pydantic runs this model-level check after individual Turn fields are validated. It guards the shared turn record before workers, storage, or UI code rely on it, making sure there is one clear source of truth about whether the turn has ended.


### `core/src/ufo/schema/tables.py`

`data_model` · `database schema setup and all database access`

This file is the project’s database blueprint. It does not run business actions itself; instead, it describes what kinds of records the system can store and what rules those records must follow. Without it, the rest of the system would not have a reliable shared understanding of where workspaces, members, agents, conversations, turns, billing records, credentials, sources, pages, scheduled tasks, and related objects live.

The central object is `metadata`, a SQLAlchemy `MetaData` object. Think of it like the master floor plan for a building. Each `sa.Table` adds a room to that plan. Columns describe what can be stored in each room. Foreign keys connect rooms together, such as a member belonging to a workspace or a turn belonging to a conversation. Unique rules stop duplicate records where duplicates would be confusing, such as two agents with the same name in one workspace. Check constraints are guardrails enforced by the database, for example making sure a turn status is one of the allowed values or that a spend cap is greater than zero.

A key detail is that this schema is meant to be dialect-neutral: it avoids tying the application to only one database engine. It also includes indexes, which are lookup shortcuts the database can use for common searches, such as finding due scheduled tasks or pending writebacks quickly.

#### Function details

##### `_conversation_audience`  (lines 11–12)

```
def _conversation_audience(context: DefaultExecutionContext) -> str
```

**Purpose**: This function chooses the default audience value for a new conversation when the application is inserting one. It turns the optional member identifier into the audience label the rest of the system expects, such as a shared conversation or a member-specific one.

**Data flow**: SQLAlchemy gives it the current insert context, including the values being written for the new conversation. The function reads the `member_id` from those values, passes it to the shared audience helper, turns the result into text, and returns that text to be stored in the conversation’s `audience` column.

**Call relations**: This function is attached to the `conversation` table as the Python-side default for the `audience` column. When code creates a conversation without explicitly giving an audience, SQLAlchemy calls this helper during the insert so the database row gets a consistent audience value before it is saved.

*Call graph*: 2 external calls (get_current_parameters, conversation_audience).


### Hosted site records
Hosted-site persistence tracks ownership, serving ports, creators, and visibility permissions for live workspace links.

### `extensions/sites/ufo_ext_sites/store.py`

`domain_logic` · `site deploy, site lookup, visibility changes, and unhosting`

A hosted site here is like a forwarding address. The stable link points to a record in the database, and that record says which workspace, conversation, site name, and sandbox port currently serve the site. This matters because a sandbox port can change or be reused, but the public-facing site link should stay meaningful and safe.

The file defines the hosted_site table shape, a small HostedSite data object, and the HostedSites class that reads and writes the registry for one workspace. Every database query is explicitly limited to that workspace, because the database connection itself can see the whole database.

The main workflow is registration. When a deploy finishes, HostedSites.register records the name and port. If the same name already exists, it updates the port and usually keeps the old visibility choice. If a different site is already using the same port, that older site may need to be unhosted first. The file refuses that change unless the acting member is allowed to do it.

It also normalizes site names into safe short “slugs,” chooses a default visibility from the conversation audience, validates visibility values, lists sites, changes visibility, and unregisters sites. The important rule is ownership: the creator controls visibility and cannot be bypassed by redeploying.

#### Function details

##### `site_name`  (lines 88–96)

```
def site_name(raw: str) -> str
```

**Purpose**: Turns a member-supplied site name into a safe stored name. It lowercases the text, replaces runs of non-letter-or-number characters with hyphens, trims it to a fixed length, and refuses names that contain no usable letters or digits.

**Data flow**: It receives raw text from a user or tool call. It converts that text into a short URL/object-friendly slug. It returns the slug, or raises InvalidSiteName if the result would be empty.

**Call relations**: Callers use this before writing a site record or building a link, so the same safe name is used everywhere. If the name cannot become a valid slug, it creates an InvalidSiteName error instead of letting a broken identity enter the registry.

*Call graph*: 1 external calls (__init__).


##### `default_visibility`  (lines 99–107)

```
def default_visibility(audience: Audience) -> Visibility
```

**Purpose**: Chooses who can see a brand-new site when the deploy did not explicitly choose a visibility. Private or external one-to-one-style audiences stay private, while ordinary internal workspace audiences default to workspace visibility.

**Data flow**: It receives an Audience value. It parses that audience and checks whether it points to an individual member or an external audience. It returns one of the allowed visibility strings: private or workspace.

**Call relations**: HostedSites.register calls this only when inserting a new site and no explicit visibility was supplied. It relies on audience parsing helpers so the registry’s default matches the conversation that produced the site.

*Call graph*: called by 1 (register); 2 external calls (audience_member, parse_audience).


##### `visibility_level`  (lines 110–118)

```
def visibility_level(value: str) -> Visibility
```

**Purpose**: Checks that a stored or submitted visibility value is one of the three supported choices: private, workspace, or public. It prevents unexpected text from being treated as a valid permission level.

**Data flow**: It receives a string. If the string is one of the known visibility values, it returns that value in the expected type. Otherwise it raises a ValueError explaining the allowed choices.

**Call relations**: _site calls this when turning a database row into a HostedSite object. That means bad database contents are caught at the boundary where raw stored data becomes application data.

*Call graph*: called by 1 (_site).


##### `HostedSites.register`  (lines 142–202)

```
async def register(self, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, audience: Audience, may_unhost: bool) -> HostedSite
```

**Purpose**: Writes or updates the registry entry for a site after a deploy. It also enforces the safety rules that stop someone from changing another creator’s visibility choice or taking over a port in a way that would unhost someone else’s site.

**Data flow**: It receives the conversation, safe site name, sandbox port, creator member, optional visibility, audience, and whether this turn is allowed to unhost. Inside a database transaction, it first asks _refuse whether the operation should be blocked. If another site on the same port may be displaced, it deletes that old registration. It then updates the existing same-name site, or inserts a new row with a default visibility if needed. Finally it reads the finished row and returns it as a HostedSite.

**Call relations**: This is the main write path used after deployment. It calls _refuse for policy checks, uses default_visibility when creating a new site without an explicit choice, and calls _read at the end to return the actual stored result.

*Call graph*: calls 3 internal fn (_read, _refuse, default_visibility); 3 external calls (delete, insert, update).


##### `HostedSites.read`  (lines 204–206)

```
async def read(self, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Looks up one hosted site by conversation and name. It is used when code needs to resolve a stable site identity into its current port, creator, and visibility record.

**Data flow**: It receives a conversation ID and site name. It opens a transaction and asks _read to query the database inside this workspace. It returns a HostedSite if found, or null-like None if there is no matching registration.

**Call relations**: This is the public lookup wrapper around _read. Other parts of the system can call it without needing to know how the SQL query is built.

*Call graph*: calls 1 internal fn (_read).


##### `HostedSites.all`  (lines 208–218)

```
async def all(self) -> tuple[HostedSite, ...]
```

**Purpose**: Returns every hosted site in the current workspace, ordered from oldest to newest. This is useful for listing or exposing the workspace’s registered sites before other visibility gates decide what a viewer may actually see.

**Data flow**: It opens a transaction, builds the standard hosted-site column selection, filters to this workspace, orders by creation time and name, and fetches all rows. Each raw row is converted into a HostedSite object. It returns a tuple of those objects.

**Call relations**: This public listing method uses _columns so it selects the same fields as the other read paths, and _site so database rows are converted consistently.

*Call graph*: calls 2 internal fn (_columns, _site).


##### `HostedSites.set_visibility`  (lines 220–235)

```
async def set_visibility(self, conversation_id: UUID, name: str, visibility: Visibility) -> HostedSite | None
```

**Purpose**: Changes the visibility level for one registered site and returns the updated site. It is the focused write path for moving a site between private, workspace, and public access.

**Data flow**: It receives the conversation ID, site name, and new visibility value. It updates the matching row in the current workspace and refreshes its updated time. It then reads the row back and returns the updated HostedSite, or None if the site no longer exists.

**Call relations**: This method uses a direct SQL update and then calls _read to return the fresh state. Authorization is expected to be checked by the caller or surrounding flow; this method performs the database change.

*Call graph*: calls 1 internal fn (_read); 1 external calls (update).


##### `HostedSites.unregister`  (lines 237–248)

```
async def unregister(self, conversation_id: UUID, name: str) -> None
```

**Purpose**: Removes a site’s registry entry so its permanent link no longer resolves. It does not stop the sandbox process itself; it only removes the official hosted-site mapping.

**Data flow**: It receives the conversation ID and site name. It opens a transaction and deletes the matching row scoped to this workspace and conversation. It returns nothing after the delete is requested.

**Call relations**: This is the public unhosting operation for a named site. It uses SQL deletion directly, while the broader permission rules for displacement are enforced in _refuse when unregistering happens indirectly through a new deploy.

*Call graph*: 1 external calls (delete).


##### `HostedSites.refuse_or_pass`  (lines 250–268)

```
async def refuse_or_pass(self, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, may_unhost: bool) -> None
```

**Purpose**: Runs the same refusal checks that registration would run, but without changing the database. It lets a caller find out before deployment whether the later registration would be blocked.

**Data flow**: It receives the proposed conversation, site name, port, creator, optional visibility, and unhost permission flag. It opens a transaction and calls _refuse. If the operation is allowed, nothing is returned; if not, the same error register would raise is raised.

**Call relations**: This is a preflight check. The deploy flow can call it before serving on a port, while HostedSites.register calls _refuse again during the actual write so the final database decision is still protected.

*Call graph*: calls 1 internal fn (_refuse).


##### `HostedSites._refuse`  (lines 270–299)

```
async def _refuse(self, connection: AsyncConnection, conversation_id: UUID, name: str, port: int, creator_member_id: UUID, visibility: Visibility | None, may_unhost: bool) -> HostedSite | None
```

**Purpose**: Centralizes the rules for when a site registration must be rejected. It protects a creator’s visibility decision and prevents a port takeover from silently unhosting another member’s site.

**Data flow**: It receives an open database connection plus the proposed registration details. It reads the existing site with the same name, if any, and rejects a visibility change made by someone other than the creator. It then looks for a different site already using the requested port. If that displaced site belongs to another member, or this turn is not allowed to unhost, it raises an error. If a site can be displaced safely, it returns that HostedSite; otherwise it returns None.

**Call relations**: HostedSites.refuse_or_pass uses this for dry-run checking, and HostedSites.register uses it before writing. It calls _read to inspect the same-name site and _on_port to find a possible port conflict.

*Call graph*: calls 2 internal fn (_on_port, _read); called by 2 (refuse_or_pass, register); 2 external calls (__init__, __init__).


##### `HostedSites._on_port`  (lines 301–316)

```
async def _on_port(self, connection: AsyncConnection, conversation_id: UUID, port: int, name: str) -> HostedSite | None
```

**Purpose**: Finds whether another site in the same conversation is already registered on the requested sandbox port. This matters because one port can only serve one actual origin, so a new deployment on that port may make an older name misleading.

**Data flow**: It receives a database connection, conversation ID, port, and the name being registered. It queries this workspace for a site in the same conversation with the same port but a different name. It returns that site as a HostedSite, or None if no conflicting site exists.

**Call relations**: _refuse calls this while deciding whether registration would displace an existing site. It uses _columns to build the shared select list and _site to convert the database row into the registry’s normal object.

*Call graph*: calls 2 internal fn (_columns, _site); called by 1 (_refuse); 1 external calls (execute).


##### `HostedSites._read`  (lines 318–330)

```
async def _read(self, connection: AsyncConnection, conversation_id: UUID, name: str) -> HostedSite | None
```

**Purpose**: Reads one site row from the database using the workspace, conversation, and name. It is the shared low-level lookup used by the public read method and by write paths that need to confirm current state.

**Data flow**: It receives an open database connection, conversation ID, and site name. It queries for exactly that site inside the current workspace. It returns a HostedSite if found, or None if no row exists.

**Call relations**: HostedSites.read exposes this lookup publicly. HostedSites.register uses it after writing to return the stored row, HostedSites.set_visibility uses it after updating, and _refuse uses it to compare a proposed change with the existing same-name site.

*Call graph*: calls 2 internal fn (_columns, _site); called by 4 (_refuse, read, register, set_visibility); 1 external calls (execute).


##### `HostedSites._columns`  (lines 332–341)

```
def _columns(self) -> sa.Select
```

**Purpose**: Builds the standard database column selection for hosted-site reads. It keeps all read paths selecting the same fields in the same way.

**Data flow**: It takes no outside data beyond the table definition in this file. It creates a SQL select statement for the fields needed to build a HostedSite. It returns that unfinished select statement so callers can add filters and ordering.

**Call relations**: The read-style methods _read, _on_port, and all call this before adding their own where clauses. This keeps row-to-object conversion through _site predictable.

*Call graph*: called by 3 (_on_port, _read, all); 1 external calls (select).


##### `_site`  (lines 344–353)

```
def _site(row: sa.Row) -> HostedSite
```

**Purpose**: Converts a raw database row into a HostedSite data object. It is the bridge between SQL results and the rest of the code’s plain Python representation of a hosted site.

**Data flow**: It receives a row returned by a hosted-site query. It copies the row’s fields into a HostedSite object and validates the visibility string through visibility_level. It returns the completed HostedSite.

**Call relations**: HostedSites._read, HostedSites._on_port, and HostedSites.all call this whenever they fetch rows. By using this single converter, every read path gets the same validation and object shape.

*Call graph*: calls 1 internal fn (visibility_level); called by 3 (_on_port, _read, all); 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-durable-store-schema` — The shared database layout and migration version that all services rely on when saving or reading system records.
- `reg-workspace-tenant-state` — The saved customer workspace boundary, including its owners, admins, limits, main agent, and tenant separation rules.
- `reg-identity-auth-state` — The current proof of who is calling, such as member identity, cookies, bearer tokens, operator sessions, and signed access tokens.
- `reg-onboarding-claims-invites` — The temporary signup claims, email verification codes, invite records, and hosted gateway tokens used to admit new users.
- `reg-agent-profile` — The saved assistant setup for each workspace, including model choice, audience, internet access, skills, and control settings.
- `reg-conversation-state` — The durable record of each conversation, including its workspace, surface, audience, agent, sandbox link, and object identity.
- `reg-transcript-state` — The shared conversation notebook containing saved messages, model events, summaries, and compaction records.
- `reg-inbound-message-state` — The durable inbox of incoming messages and surface events waiting to be admitted into a conversation turn.
- `reg-turn-run-state` — The durable job ticket for each agent turn, including admission source, queue status, claim owner, parent turn, and final result.
- `reg-cancellation-state` — The shared stop signal and cancellation record used to safely halt turns, child turns, jobs, and cleanup work.
- `reg-runtime-fleet-state` — The live fleet heartbeat table that says which runtime processes are alive and what stranded work they may own.
- `reg-usage-accounting-ledger` — The spending ledger that records model usage, egress usage, prices, caps, billing exports, and payment-related state.
- `reg-credential-secret-store` — The encrypted store of workspace and connector secrets, plus the requests that say which secrets a tool or proxy may reveal.
- `reg-access-grants` — The saved approvals that say which workspace, member, agent, account, source, or conversation is allowed to use a protected resource.
- `reg-connector-account-state` — The connected-app state for OAuth, hosted connector accounts, GitHub installations, Slack setup, and provider action access.
- `reg-source-page-sync-state` — The source records, page bodies, cursors, revisions, deletion markers, and replay feed used to keep external content synchronized.
- `reg-memory-search-index` — The long-term memory and searchable text index that stores remembered facts, chunks, embeddings, and recall results.
- `reg-sandbox-workspace-state` — The remembered sandbox workspace for a conversation, including its backend handle, files, runtime folder, and cleanup ownership.
- `reg-skill-inventory` — The declared and user-created skill inventory, including skill ownership, dependencies, files, and the load order copied into a sandbox.
- `reg-subagent-workflow-state` — The shared parent-child workflow state used when an agent delegates work to helper agents and waits for or cancels them.
- `reg-scheduled-task-state` — The durable timers and recurring jobs that remember what should run later, whether it is paused, expired, claimed, or rescheduled.
- `reg-artifact-blob-store` — The shared file and blob store for generated artifacts, copied outputs, download records, and signed access links.
- `reg-hosted-site-state` — The durable records for generated hosted sites, including names, ports, owners, conversations, sharing, and viewing permissions.
- `reg-workspace-object-state` — The shared shelf of workspace objects, their types, names, owners, permissions, listings, and object-specific actions.
- `reg-prompt-governance-state` — The prompt templates, rendered fingerprints, proposals, evaluations, approvals, and replacement decisions used to change agent behavior safely.
- `reg-observability-trace-state` — The shared logging, metrics, trace IDs, trace parents, and redaction context used to follow work across processes without leaking secrets.
- `reg-database-connection-pool` — The live database engine/session pool and transaction doorway shared by migrations, request handlers, workers, and shutdown cleanup.
- `reg-extension-kv-store` — The generic per-workspace extension JSON store used by add-ons to persist small feature-specific state outside core tables.
- `reg-surface-delivery-state` — The surface installation keys, outbound delivery/writeback queue, and acknowledgement state used to send completed replies back to external surfaces.
- `reg-security-audit-log` — The durable audit records for sensitive access and administrative/security-relevant actions, distinct from operational traces.
- `reg-member-seat-state` — The durable workspace membership and seat-assignment state used to decide who belongs, who is an admin, and whether a member may admit or run work.
- `reg-user-question-state` — The pending human-question/answer state created when an agent asks the user for information and later resumed when the surface delivers a reply.
