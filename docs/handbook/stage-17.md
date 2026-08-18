# Cross-cutting persistence, schema contracts, and durable records  `stage-17` (cross-cutting infrastructure)

This stage is the system’s shared filing cabinet. It is behind-the-scenes support used during startup, normal work, recovery, and background jobs. Its job is to make sure every part of the project agrees on how durable data is named, shaped, stored, and read later.

The database doorway is in db.py. It opens safe database connections, starts transactions, ties them to the right workspace, runs migrations, and cleans up. tables.py defines the actual table layout for both local SQLite and production Postgres. records.py defines the shapes of important saved items, such as turns, agents, tool requests, billing usage, and terminal results. transcript.py defines the saved conversation format, while loop/transcript.py safely reads and writes conversation snapshots so an old copy cannot replace a newer one.

blob.py stores large files in either local storage or cloud storage, keeping workspace files separate. durability.py makes recovered saved Python objects survive software changes. listings.py provides stable page-by-page browsing with cursor tokens. object_name.py rejects unsafe object names. schema/__init__.py simply makes the schema folder importable.

## Files in this stage

### Conversation blob storage
Shared conversation transcript formats and blob-backed storage keep saved conversation state portable and safely versioned.

### `core/src/ufo/transcript.py`

`io_transport` · `cross-cutting`

This file is the project’s “filing cabinet rulebook” for conversation history. Different parts of the system write transcripts, read them for debugging, evaluate them, or compact old messages into summaries. Those parts cannot all import each other directly, so this file holds the common contract they all share.

A transcript is stored as a compressed JSON blob. JSON is a plain text data format, and LZ4 is a fast compression format that makes the stored bytes smaller. The `Conversation` model says exactly what a saved conversation must contain: a sequence number, the messages, and optionally the system prompt and extra injected context used for a completed turn.

The file also defines the records used when a long conversation is “compacted.” Compaction means replacing an older chunk of messages with a structured summary, while keeping enough facts to continue safely. The models here describe the files touched, important facts called anchors, verification results, and the before-and-after message windows.

The key idea is consistency. If the storage path or schema changed in only one writer or reader, old records could silently become unreadable. By keeping the keys, record shapes, compression, and decoding rules here, the system changes the durable format in one place.

#### Function details

##### `transcript_key`  (lines 37–38)

```
def transcript_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the storage path for the main transcript blob of one conversation. Code that saves or reads a conversation uses this so everyone looks in the same place.

**Data flow**: It receives a conversation ID, which is a unique identifier. It places that ID into a fixed folder-and-file pattern. The result is a string path such as the place where that conversation’s compressed message file belongs.

**Call relations**: This is the shared naming rule for transcript blobs. Writers and readers can call it before touching storage, so they do not each invent their own path.


##### `encode`  (lines 41–43)

```
def encode(conversation: Conversation) -> bytes
```

**Purpose**: Turns a validated `Conversation` object into compressed bytes ready to store. It is used when the system wants to persist a transcript safely and compactly.

**Data flow**: It takes a `Conversation`, asks it for its plain data form, converts that data to JSON with stable ordering, turns the JSON text into bytes, and compresses those bytes. The output is the exact byte payload that can be written to blob storage.

**Call relations**: This function sits on the write side of the transcript format. It relies on the `Conversation` model’s own dump behavior, then uses JSON as the portable shape before compression.

*Call graph*: 2 external calls (model_dump, dumps).


##### `decode`  (lines 46–50)

```
def decode(body: bytes) -> Conversation
```

**Purpose**: Turns stored compressed transcript bytes back into a `Conversation`. It also gives callers a clear transcript-specific error if the bytes are corrupt or no longer match the expected shape.

**Data flow**: It receives compressed bytes from storage, decompresses them, and validates the resulting JSON as a `Conversation`. If that works, it returns the conversation object. If decompression or validation fails, it raises `TranscriptDecodeError` instead of leaking a lower-level error.

**Call relations**: This is the read-side partner to `encode`. Any reader of saved transcripts can use it to get a trusted `Conversation` object or a clear failure that the stored transcript cannot be decoded.

*Call graph*: 1 external calls (__init__).


##### `compaction_key`  (lines 136–137)

```
def compaction_key(conversation_id: UUID, index: int, half: CompactionHalf) -> str
```

**Purpose**: Builds the storage path for one part of a compaction record. A compaction has separate saved pieces for the message window before compaction, the window after compaction, and the summary.

**Data flow**: It receives a conversation ID, a compaction index, and which piece is being requested. It combines them into a predictable blob-storage path. The output is the string key for that exact compaction artifact.

**Call relations**: When `read_compaction_record` needs to fetch the three saved pieces of one compaction, it asks this function for each path. This keeps the read logic tied to the same naming convention as the write logic.

*Call graph*: called by 1 (read_compaction_record).


##### `decode_compaction`  (lines 140–149)

```
def decode_compaction(index: int, before: bytes, after: bytes, summary: bytes) -> CompactionRecord
```

**Purpose**: Rebuilds one full compaction record from its three stored byte blobs. It verifies that each stored piece still matches the expected schema.

**Data flow**: It receives the compaction index plus compressed bytes for the before window, after window, and summary. It decompresses and validates each piece, pulls out the message windows, and packages everything into a `CompactionRecord`. If any piece cannot be decoded, it raises `TranscriptDecodeError`.

**Call relations**: This is called after `read_compaction_record` has fetched the raw blobs. It turns storage bytes into the structured record that debug tools, evaluation code, or other readers can inspect.

*Call graph*: called by 1 (read_compaction_record); 2 external calls (__init__, __init__).


##### `read_compaction_record`  (lines 152–163)

```
async def read_compaction_record(blob: BlobStore, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Reads one numbered compaction record for a conversation from blob storage. If that numbered record does not exist, it returns `None` instead of treating absence as a crash.

**Data flow**: It receives a blob store, a conversation ID, and a compaction index. It builds the three expected keys, fetches the before, after, and summary blobs, then decodes them into a `CompactionRecord`. If storage says one of those blobs is missing, the result is `None`.

**Call relations**: This is the per-record reader used by `read_compaction_records`. It delegates path building to `compaction_key`, storage access to the blob store, and byte decoding to `decode_compaction`.

*Call graph*: calls 3 internal fn (get, compaction_key, decode_compaction); called by 1 (read_compaction_records).


##### `read_compaction_records`  (lines 166–176)

```
async def read_compaction_records(blob: BlobStore, conversation_id: UUID) -> tuple[CompactionRecord, ...]
```

**Purpose**: Reads all compaction records for one conversation, in order from oldest to newest. It stops at the first missing index because compactions are expected to be numbered sequentially.

**Data flow**: It receives a blob store and a conversation ID. Starting at index 1, it repeatedly asks for one compaction record. Each found record is added to a list; the first `None` means there are no more. It returns the collected records as an immutable tuple.

**Call relations**: This is the convenient “read the whole history of compactions” function. It repeatedly calls `read_compaction_record`, letting that lower-level function handle the details of paths, blob reads, and decoding.

*Call graph*: calls 1 internal fn (read_compaction_record).


### `core/src/ufo/blob.py`

`io_transport` · `cross-cutting: active whenever code reads, writes, streams, lists, or deletes stored blob data`

This file is the project’s “storage counter” for blobs: chunks of bytes such as attachments, static web assets, compaction records, and other files that are too file-like to live directly in ordinary application records. The important idea is that callers use one simple async interface — async means the code can wait for disk or network work without freezing other tasks — and do not need to care whether the bytes are stored in local files or in S3, Amazon’s object storage system, or a compatible service.

There are three layers. At the bottom, `FilesystemBlobStore` turns blob keys into files under a configured root directory, using temporary files and a final rename so half-written files do not appear. `S3BlobStore` performs the same operations against an S3 bucket, including streamed reads and multipart uploads for large data. In the middle, the shared `BlobStore` protocol describes the operations all backends must provide. At the top, `WorkspaceBlobStore` automatically adds the current workspace prefix, while `FleetBlobStore` only allows known deploy-wide prefixes. These wrappers are like labeled filing cabinets: one cabinet is per customer workspace, and another is for shared system material.

The file also contains safety checks. Filesystem keys cannot escape the storage root. Lists require a prefix so callers do not scan the whole store. S3 “not found” errors are translated into the project’s own `BlobNotFound` error. Without this file, every feature needing stored bytes would have to know storage details, and mistakes could mix workspace data or expose broad write access.

#### Function details

##### `BlobStore.put`  (lines 55–55)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Defines the common operation for saving a complete byte payload under a string key. Callers use this when the whole content is already in memory.

**Data flow**: A key and a bytes value go in. A concrete backend stores those bytes under that key. Nothing is returned, but the store is changed so later reads can find the object.

**Call relations**: This is the interface method used by code such as web asset publishing. The actual work is supplied by a concrete store, such as the filesystem or S3 implementation.

*Call graph*: called by 1 (_publish_assets).


##### `BlobStore.get`  (lines 57–57)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Defines the common operation for reading a complete stored object into memory. It is for small or reasonably sized blob values where loading all bytes at once is acceptable.

**Data flow**: A key goes in. The chosen backend looks up the stored object and returns its bytes, or raises `BlobNotFound` if the key is absent.

**Call relations**: Transcript reading, Slack identity loading, and web asset serving call this interface. The request is fulfilled by whichever backend was configured.

*Call graph*: called by 3 (read_compaction_record, read_identity, _stored_asset).


##### `BlobStore.exists`  (lines 59–59)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Defines the common operation for checking whether a blob key currently has an object. It lets callers avoid unnecessary reads or writes.

**Data flow**: A key goes in. The backend checks its storage and returns `true` if an object is present, otherwise `false`.

**Call relations**: Slack and web surface code use this check before reading or publishing stored data. Concrete stores implement the actual filesystem or S3 check.

*Call graph*: called by 3 (read_identity, _publish_assets, _stored_asset).


##### `BlobStore.delete`  (lines 61–63)

```
async def delete(self, key: str) -> None
```

**Purpose**: Defines the common operation for removing a stored object. Deleting a missing object is treated as harmless, which makes retries safe.

**Data flow**: A key goes in. The backend attempts to remove the stored object. Nothing is returned, and an absent key does not count as a failure.

**Call relations**: This belongs to the shared blob interface so cleanup code can delete data without knowing which storage backend is in use.


##### `BlobStore.get_stream`  (lines 65–65)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines the common operation for reading a blob piece by piece instead of all at once. This is important for large files because it avoids holding the whole file in memory.

**Data flow**: A key goes in. The backend opens the object and yields byte chunks until the object is fully read, or raises `BlobNotFound` if it is missing.

**Call relations**: Concrete stores provide streaming from disk or S3. Higher-level code can consume the stream as data is needed.


##### `BlobStore.put_stream`  (lines 67–67)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Defines the common operation for saving a blob from a stream of chunks. It is used when content arrives gradually or may be too large to buffer in memory.

**Data flow**: A key and an async sequence of byte chunks go in. The backend writes each chunk in order and finishes with one stored object under that key.

**Call relations**: Concrete stores implement this differently: the filesystem writes a temporary file, while S3 may use multipart upload for large content.


##### `BlobStore.list`  (lines 69–73)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Defines the common operation for listing stored objects under a key prefix. It gives readers a bounded view of one area of the store rather than allowing a whole-store scan.

**Data flow**: A non-empty prefix goes in. The backend finds matching objects and returns entries containing each key, byte size, and last modified time, capped at a fixed maximum.

**Call relations**: Concrete stores use filesystem walking or S3 pagination to satisfy this shared listing operation.


##### `FilesystemBlobStore.put`  (lines 82–87)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves a complete byte payload as a file beneath the blob root. It writes through a temporary file first so readers do not see a half-written result.

**Data flow**: A blob key and bytes go in. The key is converted to a safe path, parent directories are created, bytes are written to a uniquely named temporary file, and that file replaces the final path. The result is one complete file at the target key.

**Call relations**: This is the filesystem version of `BlobStore.put`. It relies on `_resolve` to keep paths inside the configured root and uses background threads for blocking file operations.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.get`  (lines 89–94)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a complete blob from the local filesystem. It translates a missing file into the project’s `BlobNotFound` error.

**Data flow**: A blob key goes in. The key is resolved to a safe path, the file is read as bytes, and those bytes come out. If the file is not present, `BlobNotFound` is raised.

**Call relations**: This is the filesystem version of `BlobStore.get`. It uses `_resolve` for safety and runs the disk read outside the event loop so other async tasks can keep moving.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.exists`  (lines 96–98)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a local file exists for a blob key. It is a lightweight way to ask whether a blob is present.

**Data flow**: A blob key goes in. The key is resolved to a safe path, the filesystem is asked whether that path is a file, and a boolean comes out.

**Call relations**: This is the filesystem version of `BlobStore.exists`. It depends on `_resolve` before touching the disk.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.delete`  (lines 100–102)

```
async def delete(self, key: str) -> None
```

**Purpose**: Removes a blob file from local storage if it exists. Missing files are ignored so callers can safely retry deletion.

**Data flow**: A blob key goes in. The key is resolved to a safe path, and the file at that path is unlinked if present. Nothing is returned.

**Call relations**: This is the filesystem version of `BlobStore.delete`. It uses `_resolve` for containment and a thread for the blocking filesystem call.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.get_stream`  (lines 104–117)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads a local blob in fixed-size chunks. This lets callers download or process large files without loading the entire file into memory.

**Data flow**: A blob key goes in. The safe file path is opened, chunks are read one at a time, and each chunk is yielded. The file handle is closed afterward, even if reading stops early.

**Call relations**: This is the filesystem streaming read. It calls `_resolve`, raises `BlobNotFound` for missing files, and uses background threads for file open, read, and close operations.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.put_stream`  (lines 119–132)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes a streamed blob to local storage. It protects readers from partial content by writing to a temporary file and only replacing the final file after all chunks arrive.

**Data flow**: A blob key and a stream of byte chunks go in. The method resolves the path, creates parent directories, writes each chunk to a temporary file, and replaces the target path when finished. If anything fails, the temporary file is removed.

**Call relations**: This is the filesystem streaming write. It uses `_resolve` for path safety and a unique temporary name so simultaneous writes do not collide.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.list`  (lines 134–137)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists local blob files whose keys start with a given prefix. It refuses an empty prefix to avoid accidentally walking the entire store.

**Data flow**: A prefix goes in. If the prefix is empty, an error is raised. Otherwise the actual directory walk is run in a worker thread, and a tuple of matching `BlobEntry` records comes out.

**Call relations**: This is the public filesystem listing method. It hands the slow disk traversal to `_walk` so the async event loop is not blocked.

*Call graph*: 1 external calls (to_thread).


##### `FilesystemBlobStore._walk`  (lines 139–160)

```
def _walk(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Does the actual filesystem traversal for listing blobs. It turns matching files into `BlobEntry` records and skips temporary write files.

**Data flow**: A prefix goes in. The method finds the contained store root, chooses the directory area to scan, walks files below it, filters to keys that really start with the prefix, records size and modification time, sorts by key, and returns at most the configured maximum.

**Call relations**: `FilesystemBlobStore.list` calls this in a background thread. It uses `_contained_root` and `_resolve` so the listing is based on the same safe root rules as reads and writes.

*Call graph*: calls 2 internal fn (_contained_root, _resolve); 4 external calls (__init__, fromtimestamp, walk, Path).


##### `FilesystemBlobStore._resolve`  (lines 162–167)

```
def _resolve(self, key: str) -> Path
```

**Purpose**: Converts a blob key into a real filesystem path while preventing directory escape. This is the main safety gate for the local backend.

**Data flow**: A key goes in. The method combines it with the canonical blob root, resolves the result, and checks that the final path is still under the root and not the root itself. A safe `Path` comes out, or a `ValueError` is raised.

**Call relations**: Every filesystem operation that touches a key calls this first. It depends on `_contained_root` to know the safe base directory.

*Call graph*: calls 1 internal fn (_contained_root); called by 7 (_walk, delete, exists, get, get_stream, put, put_stream).


##### `FilesystemBlobStore._contained_root`  (lines 169–182)

```
def _contained_root(self) -> Path
```

**Purpose**: Finds the real filesystem root directory for blob storage and checks that it is usable. It allows a not-yet-created root so the first write can create it.

**Data flow**: The configured root path stored on the object is read. The method asks the containment helper to canonicalize and validate it; if the path does not exist yet, it resolves the configured path directly. The canonical root path comes out.

**Call relations**: `_resolve` and `_walk` call this before comparing paths. It uses the sandbox containment helper so operator mistakes in `blob.root` are reported clearly.

*Call graph*: called by 2 (_resolve, _walk); 1 external calls (configured_root).


##### `_is_missing_key`  (lines 185–186)

```
def _is_missing_key(error: ClientError) -> bool
```

**Purpose**: Recognizes S3 error responses that mean “this object does not exist.” It keeps S3-specific error codes from leaking into the rest of the code.

**Data flow**: An S3 `ClientError` goes in. The method reads the error code from the response and returns `true` if it matches known missing-object codes, otherwise `false`.

**Call relations**: S3 read, existence check, and streaming read call this when the S3 client raises an error. Those methods then turn missing-object cases into `BlobNotFound` or `false`.

*Call graph*: called by 3 (exists, get, get_stream).


##### `S3BlobStore.put`  (lines 205–207)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves a complete byte payload as one S3 object. It is the cloud-storage equivalent of writing a full local blob file.

**Data flow**: A key and bytes go in. The method gets an S3 client and sends a `put_object` request for the configured bucket and key. Nothing is returned after S3 accepts the object.

**Call relations**: This is the S3 version of `BlobStore.put`. It relies on `_client` to reuse the correct async S3 client for the current event loop.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get`  (lines 209–219)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a complete S3 object into memory. Missing S3 objects are reported as the project’s `BlobNotFound` error.

**Data flow**: A key goes in. The method gets an S3 client, requests the object, reads the response body fully, and returns the bytes. If S3 says the key is missing, the method raises `BlobNotFound`.

**Call relations**: This is the S3 version of `BlobStore.get`. It calls `_client` for the connection and `_is_missing_key` to interpret S3 errors.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.exists`  (lines 221–229)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether an S3 object exists without downloading it. It uses S3 metadata lookup, which is cheaper than reading the whole object.

**Data flow**: A key goes in. The method asks S3 for the object header. If S3 confirms it, `true` comes out; if S3 reports a missing key, `false` comes out; other errors are re-raised.

**Call relations**: This is the S3 version of `BlobStore.exists`. It calls `_client` for S3 access and `_is_missing_key` to separate ordinary absence from real failures.

*Call graph*: calls 2 internal fn (_client, _is_missing_key).


##### `S3BlobStore.delete`  (lines 231–233)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes an object from the configured S3 bucket. S3 deletion is safe to call even when the object is already absent.

**Data flow**: A key goes in. The method gets an S3 client and sends a delete request for that bucket and key. Nothing is returned.

**Call relations**: This is the S3 version of `BlobStore.delete`. It uses `_client` to reach S3.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get_stream`  (lines 235–246)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams an S3 object in chunks. This is used for large objects so the application does not need to keep the whole object in memory.

**Data flow**: A key goes in. The method gets the object from S3, then yields chunks from the response body until the object is done. If S3 reports the key is absent, `BlobNotFound` is raised.

**Call relations**: This is the S3 streaming read. It calls `_client` for access and `_is_missing_key` to translate missing-object errors.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.put_stream`  (lines 248–292)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Uploads streamed data to S3, using multipart upload when the data is large. Multipart upload means S3 receives the object in numbered pieces and assembles them at the end.

**Data flow**: A key and a stream of chunks go in. The method buffers incoming bytes until a part is large enough, starts multipart upload if needed, uploads each part, and completes the object after the final part. For small data, it uses a single normal S3 put. If an error happens during multipart upload, it aborts the unfinished upload.

**Call relations**: This is the S3 streaming write. It relies on `_client` and contains the S3-specific upload strategy hidden behind the common blob interface.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_put`  (lines 294–318)

```
async def presigned_put(self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary URL that lets another process upload exactly one measured object directly to S3. This is useful for a sandbox that should be allowed to upload one file but should not receive broad S3 credentials.

**Data flow**: A key, expected size, SHA-256 checksum, and time-to-live go in. The method asks S3 to sign a PUT URL that includes the bucket, key, content length, and checksum. A URL string comes out, and it expires after the requested time.

**Call relations**: Workspace-level code can call this through `WorkspaceBlobStore.presigned_put` when the backend is S3. It calls `_client` so the URL is signed with the same client settings used for normal S3 operations.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.put_host`  (lines 320–331)

```
async def put_host(self) -> str
```

**Purpose**: Returns the hostname that a presigned upload URL will contact. The system uses this to allow the sandbox’s network proxy to reach only the needed S3 host.

**Data flow**: The store’s S3 configuration is read. The method gets the client endpoint URL, extracts its hostname, and returns either that host or the bucket-prefixed host used by AWS virtual-hosted S3 URLs. If no hostname can be found, it raises an error.

**Call relations**: This supports the presigned-upload path by keeping the proxy’s allow rule aligned with the real signed URL. It calls `_client` and parses the client endpoint.

*Call graph*: calls 1 internal fn (_client); 1 external calls (urlsplit).


##### `S3BlobStore.list`  (lines 333–350)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists S3 objects under a required prefix. It gathers object keys, sizes, and modification times without scanning unrelated areas.

**Data flow**: A non-empty prefix goes in. The method uses S3 pagination to fetch matching objects page by page, converts each object into a `BlobEntry`, stops after the configured maximum, and returns the entries.

**Call relations**: This is the S3 version of `BlobStore.list`. It calls `_client` and hides S3 pagination behind the same list shape used by the filesystem backend.

*Call graph*: calls 1 internal fn (_client); 1 external calls (__init__).


##### `S3BlobStore._client`  (lines 352–378)

```
async def _client(self) -> AioBaseClient
```

**Purpose**: Returns the reusable async S3 client for the current event loop. Reusing clients avoids expensive setup work and avoids using one loop’s network client from another loop.

**Data flow**: The current event loop and the store’s cached clients are read. If a client already exists for this loop, it is returned. Otherwise a new S3 client is created with fixed signing and addressing settings, stored in the cache, and returned; any redundant client made during a race is closed.

**Call relations**: Every S3 operation calls this before talking to S3. It also logs if closing an extra client fails, which helps diagnose unusual client-creation races.

*Call graph*: called by 9 (delete, exists, get, get_stream, list, presigned_put, put, put_host, put_stream); 3 external calls (get_session, get_running_loop, log).


##### `WorkspaceBlobStore.put`  (lines 391–392)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Stores bytes under the current workspace’s private blob prefix. Callers provide a workspace-relative key, not the full global key.

**Data flow**: A relative key and bytes go in. `_full` adds `workspaces/<current workspace>/` to the key, then the backend stores the bytes under that full key. The backend storage changes; nothing is returned.

**Call relations**: This wrapper calls `_full` before handing off to the configured filesystem or S3 backend, preventing callers from accidentally writing into another workspace.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.get`  (lines 394–395)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a complete blob from the current workspace. It lets callers think in workspace-relative paths while preserving storage isolation.

**Data flow**: A relative key goes in. `_full` turns it into a full workspace-prefixed key, the backend reads that object, and the bytes come out.

**Call relations**: This wrapper calls `_full` and then delegates to the underlying backend’s `get` method.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.exists`  (lines 397–398)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a blob exists in the current workspace. It avoids exposing or checking other workspaces’ data.

**Data flow**: A relative key goes in. `_full` adds the current workspace prefix, the backend checks that full key, and a boolean comes out.

**Call relations**: This wrapper calls `_full` before delegating to the backend’s existence check.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.delete`  (lines 400–401)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes a blob from the current workspace. The caller cannot delete data outside the current workspace through this method.

**Data flow**: A relative key goes in. `_full` builds the full workspace key, and the backend deletes that object if present. Nothing is returned.

**Call relations**: This wrapper calls `_full` and then delegates deletion to the concrete backend.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.get_stream`  (lines 403–407)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Opens a streamed read for a blob in the current workspace. It resolves the workspace immediately, so the stream remains tied to the right workspace even if the surrounding workspace scope ends later.

**Data flow**: A relative key goes in. `_full` captures the full workspace-prefixed key right away, and the backend returns a stream of byte chunks for that object.

**Call relations**: Context-building code uses this when it needs member blob text. The method calls `_full` first, then hands off streaming to the backend.

*Call graph*: calls 1 internal fn (_full); called by 1 (_member_blob_text).


##### `WorkspaceBlobStore.put_stream`  (lines 409–410)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed content into the current workspace. It is the workspace-safe wrapper around large or gradual uploads.

**Data flow**: A relative key and a stream of chunks go in. `_full` adds the current workspace prefix, and the backend writes the stream under the full key. The stored object is created or replaced.

**Call relations**: This wrapper calls `_full` and then delegates the actual streaming write to the filesystem or S3 backend.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.list`  (lines 412–417)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists blobs under a prefix inside the current workspace, while returning keys as workspace-relative names. Callers see their own workspace view, not the global storage layout.

**Data flow**: A non-empty relative prefix goes in. The method computes the workspace root prefix, asks the backend to list under that root plus the requested prefix, then removes the workspace prefix from each returned key before returning the entries.

**Call relations**: This wrapper calls `_full` to find the workspace root and uses `replace` to adjust each `BlobEntry` key for callers.

*Call graph*: calls 1 internal fn (_full); 1 external calls (replace).


##### `WorkspaceBlobStore.presigned_put`  (lines 419–430)

```
async def presigned_put(self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary direct-upload URL for a blob in the current workspace, but only when the backend is S3. It keeps direct uploads inside the workspace prefix.

**Data flow**: A relative key, size, checksum, and expiry time go in. If the backend is S3, `_full` adds the workspace prefix and the S3 backend creates the presigned URL. If the backend is not S3, a `TypeError` is raised.

**Call relations**: This method is the workspace-safe entry to `S3BlobStore.presigned_put`. It refuses filesystem backends because local files cannot be uploaded through an S3 presigned URL.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore._full`  (lines 432–435)

```
def _full(self, key: str) -> str
```

**Purpose**: Builds the real storage key for the current workspace. It is the guardrail that keeps workspace callers from supplying their own workspace prefix.

**Data flow**: A workspace-relative key goes in. If it already starts with the global workspace prefix, a `ValueError` is raised. Otherwise the current workspace ID is read and combined with the key to produce `workspaces/<id>/<key>`.

**Call relations**: All workspace blob operations call this before reaching the backend. It depends on `ws_current`, so calls outside a bound workspace scope fail instead of silently using the wrong workspace.

*Call graph*: called by 8 (delete, exists, get, get_stream, list, presigned_put, put, put_stream); 1 external calls (ws_current).


##### `FleetBlobStore.put`  (lines 446–447)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Stores deploy-wide bytes under one of the allowed fleet prefixes. This is for data owned by the whole deployment, not by a single workspace.

**Data flow**: A key and bytes go in. `_checked` confirms the key starts with an allowed fleet prefix, then the backend stores the bytes under that key. Nothing is returned.

**Call relations**: This wrapper calls `_checked` before delegating to the backend, preventing fleet storage from becoming a back door into workspace data.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.get`  (lines 449–450)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a complete deploy-wide blob from an allowed fleet namespace. It refuses keys outside the approved prefixes.

**Data flow**: A key goes in. `_checked` validates the prefix, the backend reads the object, and the bytes come out.

**Call relations**: This wrapper calls `_checked` and then delegates to the concrete backend’s read operation.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.exists`  (lines 452–453)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a deploy-wide blob exists under an allowed fleet prefix. It gives shared system code a safe presence check.

**Data flow**: A key goes in. `_checked` validates that it belongs to a fleet namespace, the backend checks the object, and a boolean comes out.

**Call relations**: This wrapper calls `_checked` before using the backend’s existence check.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.delete`  (lines 455–456)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes a deploy-wide blob from an allowed fleet namespace. Keys outside those namespaces are refused.

**Data flow**: A key goes in. `_checked` validates it, and the backend removes the object if present. Nothing is returned.

**Call relations**: This wrapper calls `_checked` and then delegates deletion to the backend.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.get_stream`  (lines 458–459)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a deploy-wide blob from an allowed fleet namespace. It is useful for larger shared files or payloads.

**Data flow**: A key goes in. `_checked` confirms the key is allowed, and the backend returns a chunk-by-chunk stream for that object.

**Call relations**: This wrapper calls `_checked` before handing off to the backend’s streaming read.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.put_stream`  (lines 461–462)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed content to a deploy-wide allowed namespace. It supports large shared data without loading everything into memory.

**Data flow**: A key and a stream of chunks go in. `_checked` validates the key, and the backend writes the chunks under that key. The stored object is created or replaced.

**Call relations**: This wrapper calls `_checked` before delegating the streaming write to the backend.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.list`  (lines 464–465)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists deploy-wide blobs under an allowed fleet prefix. It prevents callers from listing arbitrary areas of blob storage.

**Data flow**: A prefix goes in. `_checked` verifies that the prefix belongs to a fleet namespace, and the backend returns matching `BlobEntry` records.

**Call relations**: This wrapper calls `_checked` and then uses the backend’s list operation.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore._checked`  (lines 467–470)

```
def _checked(self, key: str) -> str
```

**Purpose**: Validates that a fleet blob key belongs to one of the approved deploy-wide namespaces. It is the safety check for all fleet storage operations.

**Data flow**: A key goes in. If it starts with an allowed prefix such as `static/` or `term/`, the same key comes out. Otherwise a `ValueError` is raised.

**Call relations**: Every fleet blob operation calls this before touching the backend. This keeps workspace-prefixed data from being accessed through the fleet store.

*Call graph*: called by 7 (delete, exists, get, get_stream, list, put, put_stream).


##### `blob_store_for`  (lines 473–485)

```
def blob_store_for(config: BlobConfig) -> FilesystemBlobStore | S3BlobStore
```

**Purpose**: Builds the configured low-level blob backend. It turns configuration into either a filesystem store or an S3 store.

**Data flow**: A `BlobConfig` goes in. The backend type is inspected: filesystem requires a root path and produces a `FilesystemBlobStore`; S3 requires a bucket and produces an `S3BlobStore` with endpoint and region settings. Missing required settings raise `ValueError`.

**Call relations**: Startup or setup code uses this to create the storage backend used elsewhere. Higher-level workspace and fleet stores can then wrap the returned backend.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/loop/transcript.py`

`io_transport` · `end of turn and repair publishing`

A conversation transcript is the durable record of what has happened so far. This file gives the rest of the system one small object, `Transcript`, for reading that record and writing a new version back. The important protection here is the sequence number, or `seq`: a simple counter that says how far along the conversation is. Before writing, the code first reads the current saved transcript. If the saved one already has the same or a later `seq`, the new write is ignored. This is like a noticeboard where each notice has a version number: if someone tries to pin up an older copy, the board keeps the newer one. Without this guard, a late-running task or repair path could accidentally erase the authoritative end-of-turn transcript with stale data. The file also hides the storage details. Callers do not need to know how the blob key is built or how the conversation is turned into bytes. They ask to read, and get either a `Conversation` object or nothing. They ask to write, and the file stores the encoded conversation only if it moves the transcript forward.

#### Function details

##### `Transcript.read`  (lines 17–22)

```
async def read(self) -> Conversation | None
```

**Purpose**: This reads the saved transcript for one conversation, if it exists. It lets callers work with a normal `Conversation` object instead of raw stored bytes.

**Data flow**: It starts with the `conversation_id` held by the `Transcript` object and uses it to build the storage key. It asks the blob store for the saved bytes at that key. If no blob exists, it returns `None`; otherwise it decodes the bytes into a `Conversation` and returns that.

**Call relations**: This is the read side of the transcript wrapper. `Transcript.write` uses it first to check what version is already saved before deciding whether a new transcript should replace it.

*Call graph*: called by 1 (write); 2 external calls (decode, transcript_key).


##### `Transcript.write`  (lines 24–28)

```
async def write(self, conversation: Conversation) -> None
```

**Purpose**: This saves a conversation transcript only if it is newer than what is already stored. It protects the durable record from being rolled back by an older copy.

**Data flow**: It receives a `Conversation` to save. First it reads the currently stored transcript. If the stored transcript exists and its sequence number is the same or higher, it stops without changing storage. If the incoming conversation is ahead, it encodes that conversation into bytes and writes those bytes to the blob key for this conversation.

**Call relations**: This is called when the system wants to publish a transcript snapshot, especially at the end of a turn or when a repair flow republishes a committed terminal state. It relies on `Transcript.read` for the safety check, then hands the final encoded data to the blob store for durable storage.

*Call graph*: calls 1 internal fn (read); 2 external calls (encode, transcript_key).


### Database durability primitives
Database connection handling and durable object reload rules provide safe persistence across transactions, migrations, and workflow recovery.

### `core/src/ufo/db.py`

`io_transport` · `startup, request/job database access, migrations, teardown`

This module protects the project’s main safety rule: one running service may serve many workspaces, but database reads and writes must stay inside the current workspace unless a special owner path is used. A workspace is like a tenant or account. Before normal code uses the database, the surrounding request, job, or turn sets `current_workspace`. Then `workspace_tx` opens a transaction and, for PostgreSQL, tells the database which workspace this transaction belongs to. PostgreSQL row-level security, meaning database rules that hide rows automatically, uses that value to prevent leaks. If no workspace is set, the database fails closed rather than accidentally showing everything.

The file also keeps connection pools. A pool is a reusable set of database connections, like a taxi stand instead of calling a new taxi every time. Because async database connections belong to the event loop that created them, this file keeps separate engines per event loop and database URL.

There is one deliberate exception: `owner_tx`. It is for cross-workspace background scans that only enumerate work, then re-enter each workspace safely before reading details.

Finally, the module runs Alembic migrations, which update the database schema, and contains SQLite-specific setup so local or test databases behave predictably.

#### Function details

##### `_build_engine`  (lines 98–108)

```
def _build_engine(url: str, pool: _Pool) -> AsyncEngine
```

**Purpose**: Creates a SQLAlchemy async database engine for one database URL and one pool definition. This is the object later used to borrow connections and begin transactions.

**Data flow**: It receives a database URL and a pool description. It asks `_pool_kwargs` for the right connection-pool settings, creates the async engine, and, if the database is SQLite, attaches setup hooks that configure SQLite connections and transaction starts. It returns the ready-to-use engine.

**Call relations**: When `_engine_for` needs an engine for the current event loop, it calls this to build one lazily. `verify_db_reachable` also calls it to make a temporary engine just to test whether the database can be contacted.

*Call graph*: calls 1 internal fn (_pool_kwargs); called by 2 (_engine_for, verify_db_reachable); 1 external calls (create_async_engine).


##### `_pool_kwargs`  (lines 111–131)

```
def _pool_kwargs(url: str, pool: _Pool) -> dict[str, Any]
```

**Purpose**: Chooses the connection-pool settings that match the database kind. PostgreSQL and SQLite need different settings, so this keeps those choices in one place.

**Data flow**: It receives a URL and a pool description. It parses the URL to see which database backend is being used. For SQLite it returns settings that keep a small pool but allow extra connections; for other databases it returns bounded pool settings, recycling, pre-ping checks, and driver-specific connection arguments from `_driver_kwargs`.

**Call relations**: _build_engine` relies on this before creating an engine. If the URL points to PostgreSQL or another non-SQLite backend, this function hands part of the decision to `_driver_kwargs` so the lower-level driver gets arguments it understands.

*Call graph*: calls 1 internal fn (_driver_kwargs); called by 1 (_build_engine); 1 external calls (make_url).


##### `_driver_kwargs`  (lines 134–161)

```
def _driver_kwargs(driver: str, pool: _Pool) -> dict[str, Any]
```

**Purpose**: Builds the driver-specific connection options, such as connection timeout, application name, and prepared-statement cache behavior. This matters because different PostgreSQL drivers use different option names.

**Data flow**: It receives a driver name and pool description. If the driver is asyncpg, it returns asyncpg-style connection arguments. Otherwise it returns psycopg-style arguments. In both cases the output names the pool in the database, limits how long connection attempts may hang, and disables statement caching that can break after schema changes.

**Call relations**: _pool_kwargs` calls this when preparing non-SQLite engine settings. It is the adapter that lets the rest of the module ask for one intent while each database driver receives the spelling it expects.

*Call graph*: called by 1 (_pool_kwargs).


##### `_engine_for`  (lines 164–180)

```
def _engine_for(url: str, pool: _Pool) -> AsyncEngine
```

**Purpose**: Finds or creates the database engine for the current event loop and URL. This prevents an async connection created on one loop from being reused on a different loop, which would be unsafe.

**Data flow**: It looks at the currently running event loop and combines that loop with the URL as a lookup key. It removes entries for loops that have already closed, then returns the existing engine for this loop and URL or builds and stores a new one with `_build_engine`.

**Call relations**: `workspace_tx` and `owner_tx` call this whenever they need a transaction. It is the registry gatekeeper between high-level transaction helpers and the lower-level engine builder.

*Call graph*: calls 1 internal fn (_build_engine); called by 2 (owner_tx, workspace_tx); 1 external calls (get_running_loop).


##### `init_db`  (lines 183–187)

```
def init_db(url: str) -> None
```

**Purpose**: Registers the main application database URL. Code must call this during setup before normal workspace transactions can be opened.

**Data flow**: It receives a database URL. If a URL has already been registered, it raises an error to prevent accidental reconfiguration. Otherwise it stores the URL for later use by `workspace_tx`, `owner_tx`, and reachability checks.

**Call relations**: This is called by composition or startup code before the rest of the application touches the database. Later functions read the stored URL instead of receiving it every time.


##### `init_owner_db`  (lines 190–204)

```
def init_owner_db(url: str) -> None
```

**Purpose**: Registers the special owner database URL used for cross-workspace enumeration. It also normalizes plain PostgreSQL URLs into the async driver form this module can use.

**Data flow**: It receives an owner-role database URL. If one is already registered, it raises an error. Otherwise it rewrites a leading `postgresql://` into `postgresql+asyncpg://` and stores the result as the owner URL.

**Call relations**: Startup code calls this when the process has an owner-role connection string. Later, `owner_tx` uses this stored owner URL when it needs to scan across workspaces.


##### `verify_db_reachable`  (lines 207–227)

```
async def verify_db_reachable() -> None
```

**Purpose**: Checks at startup that the configured database URLs can actually be reached. This makes a bad database connection fail early instead of letting the service look healthy while every request fails.

**Data flow**: It reads the registered app and owner URLs. If neither exists, it raises an initialization error. For each configured URL, it builds a temporary engine, tries to open a connection, then disposes that engine so the test connection does not stay in a pool.

**Call relations**: Startup code can await this after `init_db` and optional `init_owner_db`. It uses `_build_engine` directly because it wants a throwaway check, not an engine stored in the per-loop registry.

*Call graph*: calls 1 internal fn (_build_engine).


##### `dispose_db`  (lines 230–253)

```
async def dispose_db() -> None
```

**Purpose**: Shuts down all registered database engines and clears the stored database URLs. This is used by command-line tools, tests, and shutdown paths so pooled connections do not linger.

**Data flow**: It clears the app and owner URLs first. Then it looks through every app and owner engine. Engines owned by the current event loop are disposed immediately. Engines owned by another still-running loop are removed from the registry and handed back to that loop for disposal. Entries for closed loops are simply dropped.

**Call relations**: Teardown code calls this when the process or test is done with the database. When it finds an engine owned by another event loop, it delegates to `_hand_off` because only that loop can safely close its own async connections.

*Call graph*: calls 1 internal fn (_hand_off); 1 external calls (get_running_loop).


##### `_hand_off`  (lines 256–263)

```
def _hand_off(loop: asyncio.AbstractEventLoop, engine: AsyncEngine) -> None
```

**Purpose**: Asks another event loop to dispose an engine that belongs to it. This avoids closing async database connections from the wrong loop.

**Data flow**: It receives an event loop and an engine. It schedules `_dispose_on_this_loop` on that loop in a thread-safe way. If the loop has already closed, it quietly gives up because there is no safe loop left to do the cleanup.

**Call relations**: `dispose_db` calls this for engines created on other loops. It passes the actual disposal work to `_dispose_on_this_loop`, which will run in the correct place.

*Call graph*: called by 1 (dispose_db); 1 external calls (call_soon_threadsafe).


##### `_dispose_on_this_loop`  (lines 266–273)

```
def _dispose_on_this_loop(engine: AsyncEngine) -> None
```

**Purpose**: Starts engine disposal on the event loop that owns the engine. It also keeps the cleanup task alive until it finishes.

**Data flow**: It receives an async engine. It creates an asynchronous task to dispose that engine, stores the task in a module-level set so it cannot be garbage-collected too early, and removes the task from that set when it completes.

**Call relations**: This is scheduled by `_hand_off` onto the engine’s own event loop. It is the final step in cleaning up engines that `dispose_db` could not await directly.

*Call graph*: 2 external calls (ensure_future, dispose).


##### `dispose_loop_engines`  (lines 276–286)

```
async def dispose_loop_engines() -> None
```

**Purpose**: Disposes only the engines that belong to the currently running event loop, while leaving the configured database URLs in place. This is useful for short-lived loops that are about to close.

**Data flow**: It gets the current event loop, scans both engine registries, removes entries owned by this loop, and awaits disposal for each matching engine. Engines for other loops remain registered and alive.

**Call relations**: Code that creates throwaway event loops can call this before the loop ends. It prevents connections from being stranded on a loop that is about to disappear, without fully shutting down database access for the whole process.

*Call graph*: 1 external calls (get_running_loop).


##### `_opened`  (lines 290–339)

```
async def _opened(engine: AsyncEngine, path: str) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens a database transaction and measures how long it took to acquire. It also records metrics when the transaction cannot start, such as when the pool is exhausted.

**Data flow**: It receives an engine and a label such as `workspace` or `owner`. It starts a timer, tries to begin a transaction, records acquisition time, and emits failure counters if connection acquisition fails. It yields the open connection to the caller. When the caller is done, it carefully closes or rolls back/commits through the async context stack, even if cancellation happens during cleanup.

**Call relations**: `workspace_tx` and `owner_tx` wrap their database work with this helper. It is the shared transaction-opening machinery beneath both safe workspace access and special owner access.

*Call graph*: called by 2 (owner_tx, workspace_tx); 7 external calls (ensure_future, shield, AsyncExitStack, begin, monotonic, emit_histogram, emit_metric).


##### `workspace_tx`  (lines 343–353)

```
async def workspace_tx() -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens the normal database transaction for code that should be scoped to the current workspace. This is the main safe entry point for application database reads and writes.

**Data flow**: It reads the registered app database URL and errors if setup has not happened. It gets the current loop’s app engine through `_engine_for`, opens a transaction through `_opened`, then reads `current_workspace`. If a workspace is set and the database is PostgreSQL, it sets the transaction-local database variable `app.workspace_id`. It yields the connection to the caller.

**Call relations**: Application code uses this when doing workspace-scoped database work. It sits above `_engine_for` and `_opened`, adding the key tenant-safety step before handing the connection back to the caller.

*Call graph*: calls 2 internal fn (_engine_for, _opened); 1 external calls (text).


##### `owner_tx`  (lines 357–370)

```
async def owner_tx() -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens the special transaction used to enumerate work across workspaces. It intentionally does not set a workspace variable, so callers must not use it as a normal tenant-scoped read path.

**Data flow**: It chooses the owner URL and owner pool if an owner URL was registered; otherwise it falls back to the app URL and app pool. It errors if no usable URL exists. It gets the correct engine with `_engine_for`, opens a transaction through `_opened`, and yields the connection without binding any workspace.

**Call relations**: Background sweeps use this to find identifiers across workspaces, then re-enter each workspace separately through normal workspace binding. It shares the same engine and transaction helper machinery as `workspace_tx` but deliberately skips the workspace pin.

*Call graph*: calls 2 internal fn (_engine_for, _opened).


##### `apply_migrations`  (lines 373–400)

```
def apply_migrations(url: str, pack: str | None=None) -> None
```

**Purpose**: Runs database schema migrations. Migrations are the ordered changes that create or update tables so the database matches the code.

**Data flow**: It receives a database URL and optionally an extension pack name. It builds an Alembic configuration, combines the core migration folder with active extension migration folders, stores the database URL, checks for duplicate migration revision IDs, then upgrades the database to all current migration heads. If the target is SQLite, it finishes by sealing the SQLite journal mode.

**Call relations**: Startup tools, deployment jobs, or tests call this before using a database that may need schema updates. It asks the extension loader for migration locations and calls `_seal_sqlite_journal` after SQLite migrations because Alembic uses its own engine and misses this module’s normal SQLite connection setup.

*Call graph*: calls 1 internal fn (_seal_sqlite_journal); 6 external calls (__init__, upgrade, from_config, migration_locations, catch_warnings, simplefilter).


##### `_seal_sqlite_journal`  (lines 403–419)

```
def _seal_sqlite_journal(url: str) -> None
```

**Purpose**: Puts a migrated SQLite database file into write-ahead-log journal mode before normal engines open it. This avoids later lock errors caused by the first live connection trying to convert the file.

**Data flow**: It parses the SQLite URL to find the database filename. If no file is named, it raises an error. Otherwise it opens the file with Python’s SQLite library, runs `pragma journal_mode=wal`, and closes the connection.

**Call relations**: `apply_migrations` calls this after running migrations against a SQLite database. It compensates for the fact that Alembic’s migration engine does not use `_sqlite_on_connect`.

*Call graph*: called by 1 (apply_migrations); 2 external calls (make_url, connect).


##### `_sqlite_on_connect`  (lines 422–428)

```
def _sqlite_on_connect(dbapi_connection: Any, _connection_record: Any) -> None
```

**Purpose**: Configures every SQLite connection created by this module. It makes SQLite behave better for this application by enabling write-ahead logging, foreign-key checks, and a short wait when the database is busy.

**Data flow**: It receives a raw SQLite database connection from SQLAlchemy. It switches isolation control to manual mode, opens a cursor, runs SQLite `pragma` commands for journal mode, foreign keys, and busy timeout, then closes the cursor.

**Call relations**: `_build_engine` attaches this as a listener when it creates a SQLite engine. SQLAlchemy then calls it automatically whenever that engine opens a new SQLite connection.


##### `_sqlite_begin_immediate`  (lines 431–433)

```
def _sqlite_begin_immediate(connection: sa.Connection) -> None
```

**Purpose**: Starts SQLite transactions with `begin immediate`, which claims the single writer slot up front. This turns certain lock conflicts into waiting in line instead of deadlocking later.

**Data flow**: It receives a SQLAlchemy connection and sends the SQL command `begin immediate` to SQLite. The result is that the transaction begins while reserving write access early.

**Call relations**: `_build_engine` attaches this as SQLite’s transaction-begin listener. SQLAlchemy calls it whenever a SQLite transaction starts through engines created by this module.

*Call graph*: 1 external calls (exec_driver_sql).


### `core/src/ufo/durability.py`

`io_transport` · `database persistence and crash recovery`

DBOS stores workflow inputs, step results, and final errors in a database so work can be replayed after a crash. The tricky part is that replay may happen with a newer version of the code than the one that wrote the data. A normal Python pickle, which is Python's built-in object-saving format, can restore a Pydantic model as a raw pile of old field values without running the model's normal validation. That means a newly added field may simply not exist on the restored object, causing a confusing failure later.

This file solves that by defining `ReplaySafeSerializer`, the serializer used when talking to DBOS. It still uses pickle underneath, but it changes how Pydantic `BaseModel` objects are saved. Instead of storing the model in a way that bypasses construction, it stores the model's class plus its field values. When loaded, `_rebuild` asks Pydantic to validate those fields again against the current model class. In everyday terms, it does not just pull an old form out of a filing cabinet; it recopies the saved answers onto today's version of the form.

This lets new fields receive current defaults, lets removed fields be ignored, and gives clearer failures when required data is truly missing. The serializer also has a fixed name, `ufo_pickle`, because DBOS records which serializer wrote each row and needs that name to read it back later.

#### Function details

##### `replay_safe_client`  (lines 27–31)

```
def replay_safe_client(system_database_url: str) -> DBOSClient
```

**Purpose**: Creates a DBOS client that is configured with this project's replay-safe serializer. This matters because data written with this serializer must later be read by a client that knows the same serializer name and behavior.

**Data flow**: It takes a system database URL as input. It builds a `ReplaySafeSerializer`, gives it to `DBOSClient` along with the database URL, and returns the ready-to-use client. The database is not transformed here; the important change is that all future DBOS reads and writes through this client use the safe serializer.

**Call relations**: This is the intended doorway for constructing DBOS clients in this repository. It calls `ReplaySafeSerializer` to create the serializer, then hands that serializer to the external `dbos.DBOSClient` so DBOS can use it whenever it records or replays stored workflow data.

*Call graph*: 2 external calls (__init__, DBOSClient).


##### `_rebuild`  (lines 34–35)

```
def _rebuild(model_class: type[BaseModel], fields: dict[str, object]) -> BaseModel
```

**Purpose**: Reconstructs a saved Pydantic model using the current version of its class. This is the key step that lets defaults and validation run again during replay.

**Data flow**: It receives a Pydantic model class and a dictionary of saved field values. It passes those fields into the class's `model_validate` method, which builds a fresh model according to today's class definition. The output is a new validated model object.

**Call relations**: This function is named inside the pickle data produced for Pydantic models. During deserialization, pickle calls back to `_rebuild` so the old saved fields can be turned into a current model object rather than a half-restored old one.


##### `_ModelPickler.reducer_override`  (lines 39–42)

```
def reducer_override(self, obj: object) -> tuple[Callable[..., object], tuple[object, ...]]
```

**Purpose**: Tells pickle to use the special safe recipe whenever it sees a Pydantic model. For all other objects, it leaves pickle's normal behavior alone.

**Data flow**: It receives an object that pickle is about to save. If the object is a Pydantic `BaseModel`, it returns instructions saying: save the model's class and its current field dictionary, then rebuild it later with `_rebuild`. If the object is not a Pydantic model, it returns `NotImplemented`, which means normal pickle rules continue.

**Call relations**: This method is used inside `ReplaySafeSerializer.serialize` when `_ModelPickler` writes data into a byte buffer. It is the point where ordinary pickling is redirected only for Pydantic models, while the rest of the object graph continues through standard pickle behavior.


##### `ReplaySafeSerializer.name`  (lines 48–49)

```
def name(self) -> str
```

**Purpose**: Returns the stable name DBOS records next to data written by this serializer. That name is how DBOS knows which serializer should read the data later.

**Data flow**: It takes no outside data beyond the serializer instance. It returns the constant string `ufo_pickle`. Nothing else is changed.

**Call relations**: DBOS calls this method when it needs to label serialized rows or match stored rows to a serializer. The fixed name connects data written today with future calls to `ReplaySafeSerializer.deserialize` during recovery.


##### `ReplaySafeSerializer.serialize`  (lines 51–54)

```
def serialize(self, data: object) -> str
```

**Purpose**: Turns a Python object into a text string that can be stored in the database, while using the safer Pydantic model behavior defined in this file.

**Data flow**: It receives any Python object. It creates an in-memory byte buffer, uses `_ModelPickler` to pickle the object into that buffer, then base64-encodes the bytes into ordinary text. The output is a UTF-8 string suitable for database storage.

**Call relations**: DBOS calls this when it needs to persist workflow-related data. Inside, it creates `_ModelPickler`, whose `reducer_override` supplies the special treatment for Pydantic models, and it uses standard `io.BytesIO` and `base64.b64encode` to produce a database-friendly string.

*Call graph*: 3 external calls (__init__, b64encode, BytesIO).


##### `ReplaySafeSerializer.deserialize`  (lines 56–57)

```
def deserialize(self, serialized_data: str) -> object
```

**Purpose**: Turns a stored text string back into the original Python object, rebuilding Pydantic models through the safe path when the pickle data asks for it.

**Data flow**: It receives a base64 text string from storage. It decodes the text back into bytes, then runs `pickle.loads` to recreate the saved object graph. If the saved data includes Pydantic models written by this serializer, pickle invokes `_rebuild` as part of loading them.

**Call relations**: DBOS calls this when reading persisted workflow data, especially during replay after a crash. It hands off to `base64.b64decode` and `pickle.loads`; the special model reconstruction happens because `serialize` encoded `_rebuild` into the pickle instructions.

*Call graph*: 2 external calls (b64decode, loads).


### Listing and naming contracts
Cursor pagination and object-name validation provide small shared contracts for safely storing, linking, and browsing durable objects.

### `core/src/ufo/listings.py`

`domain_logic` · `request handling`

Listings in this system are shown newest first. A simple page number or offset would be fragile: if a new row is added while someone is reading, the next page could repeat something they already saw or miss something entirely. This file avoids that by using keyset paging, which is like leaving a bookmark at a specific item rather than saying “start at item 20.”

The bookmark is a ListingCursor. It records the item’s creation time and id, because many rows can share the same timestamp, and the id breaks ties. The cursor also says whether the reader is asking for rows newer than that bookmark or older than it.

page_query takes a caller’s database query and adds the shared listing rules: sort by creation time and id, filter to the correct side of the cursor, and ask for one extra row. That extra row is not shown; it only tells the code whether another page exists.

page_of then turns those raw rows into a ListingPage. It trims off the extra row, reverses rows when needed so the final page is still newest-first, renders each row into the caller’s output shape, and creates older/newer cursors only when those directions are available. Without this file, each listing would need to reinvent this careful boundary logic, making subtle paging bugs much more likely.

#### Function details

##### `ListingCursor.encode`  (lines 42–45)

```
def encode(self) -> str
```

**Purpose**: This turns a ListingCursor into a single text token that can travel in a web link or query string. Someone uses it when they need to give the client a “next page” or “previous page” marker.

**Data flow**: It starts with the cursor’s direction, creation time, and item id. It converts the time to standard text, prefixes the direction as either newer or older, joins the three pieces with a separator, and returns that combined string. It does not change the cursor itself.

**Call relations**: This is the outward-facing half of cursor use. After page_of creates boundary cursors for a page, the surrounding web or API layer can call encode to put those positions into links or responses.


##### `ListingCursor.decode`  (lines 48–60)

```
def decode(cls, token: str) -> 'ListingCursor'
```

**Purpose**: This reads a cursor token that came back from a client and turns it into a ListingCursor the server can trust. If the token is missing pieces, has an invalid date, or does not contain a valid UUID item id, it rejects it instead of guessing.

**Data flow**: It receives one text token. It splits the token into direction, timestamp, and item id; checks that the direction is allowed; parses the timestamp into a datetime; and verifies the id is a UUID. If everything is valid, it returns a ListingCursor. If not, it raises MalformedCursor so the caller can report a bad cursor to the client.

**Call relations**: The web surfaces for artifacts, memory, and radar call this when a request includes a cursor. Once decoded, that cursor can be passed into page_query and page_of so the listing continues from the exact position the client requested.

*Call graph*: called by 3 (workspace_artifacts, workspace_memory, workspace_radar); 3 external calls (__init__, fromisoformat, UUID).


##### `page_query`  (lines 74–96)

```
def page_query(query: sa.Select[Any], cursor: ListingCursor | None, limit: int, *, created_at: sa.ColumnElement[datetime], ident: sa.ColumnElement[Any]) -> sa.Select[Any]
```

**Purpose**: This prepares a database query for one page of a listing. It applies the shared newest-first ordering, moves to the correct side of a cursor when one is present, and asks for one extra row to detect whether there is another page.

**Data flow**: It receives a SQLAlchemy select query, an optional cursor, a page size limit, and the two database columns that define listing order: created_at and id. If there is no cursor, it simply orders newest-first and limits the result to one more than requested. If there is a cursor, it compares the row’s created_at and id pair with the cursor’s pair, chooses older or newer rows depending on the cursor direction, and returns the modified query.

**Call relations**: A listing endpoint or provider uses this before reading from the database. The rows returned by this prepared query are then meant to be passed to page_of, which interprets the extra row and builds the page envelope.

*Call graph*: 2 external calls (tuple_, UUID).


##### `page_of`  (lines 99–128)

```
def page_of(rows: Sequence[SourceT], cursor: ListingCursor | None, limit: int, *, render: Callable[[SourceT], RowT], position: Callable[[SourceT], tuple[datetime, str]]) -> ListingPage[RowT]
```

**Purpose**: This turns the rows fetched for a listing page into a ListingPage: the visible rows plus optional cursors for moving older or newer. It is the part that decides which navigation controls should exist.

**Data flow**: It receives the fetched rows, the cursor that led here, the requested limit, a render function that turns source rows into output rows, and a position function that extracts each row’s timestamp and id. It checks whether there is an extra row beyond the limit, keeps only the visible rows, reverses them if the database had to search in the newer direction, renders them, and creates older/newer boundary cursors when there are rows in those directions. The result is a ListingPage.

**Call relations**: This is used after page_query has supplied the correctly ordered and bounded rows. It hands the caller a clean page object that can be returned to a client, and it uses its small helper page_of.at to build cursors from the first and last visible rows.

*Call graph*: 1 external calls (__init__).


##### `page_of.at`  (lines 118–120)

```
def at(source: SourceT, *, newer: bool) -> ListingCursor
```

**Purpose**: This small helper creates a ListingCursor for a particular source row. It exists so page_of can make accurate boundary cursors without duplicating the same extraction code.

**Data flow**: It receives one source row and a direction flag. It asks the supplied position function for that row’s creation time and item id, then returns a new ListingCursor with those values and the requested direction.

**Call relations**: Only page_of uses this helper, when it needs to mark the first visible row as the path toward newer items or the last visible row as the path toward older items.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/object_name.py`

`domain_logic` · `cross-cutting, especially when object names are created or written`

Object names are used as stable, human-readable identifiers elsewhere in the project. This file exists so every part of the system follows the same naming rule, instead of each part inventing its own version. Without it, one area might create a name that another area cannot read, display, or turn into a link.

The rule is deliberately narrow. A valid name is made from lowercase letters, digits, and hyphens. It must start and end with a lowercase letter or digit, so names cannot begin or end with a hyphen. It also cannot be longer than 64 characters. In everyday terms, it is like setting the rules for usernames before accounts can be created: if the name does not fit the rule, the system refuses it immediately.

The file also defines `InvalidName`, a specific kind of error used when a name breaks the rule. The main function, `validate_object_name`, checks a supplied string against the length limit and the pattern. If the name is acceptable, it quietly returns. If not, it raises `InvalidName` with a message that explains the allowed shape. This module is kept separate from the larger object system because names sometimes need to be created in lower-level code that should not import the full object-reading machinery.

#### Function details

##### `validate_object_name`  (lines 17–25)

```
def validate_object_name(name: str) -> None
```

**Purpose**: This function checks whether a proposed object name follows the one naming rule shared by all object kinds. Code uses it before saving or accepting a caller-supplied name, so invalid names are rejected at the point they enter the system.

**Data flow**: It receives a name as text. It checks two things: whether the name is no more than 64 characters, and whether it matches the allowed pattern of lowercase letters, digits, and internal hyphens. If both checks pass, nothing is returned and nothing changes. If either check fails, it raises an `InvalidName` error with a clear explanation of the rule.

**Call relations**: When some other part of the system is about to persist an object under a supplied name, it calls this function as the gatekeeper. If the name is bad, this function creates and raises an `InvalidName` error, stopping the write before a broken reference can be stored.

*Call graph*: 1 external calls (__init__).


### Schema record contracts
The schema package defines the shared durable record and table shapes used by surfaces, workers, tools, and background jobs.

### `core/src/ufo/schema/__init__.py`

`other` · `import time`

This is an empty Python package marker. In Python, a folder often needs an `__init__.py` file so it can be treated as an importable package. Think of it like a label on a drawer: the label does not contain the tools, but it tells Python that the drawer belongs to the project and can be opened with imports such as `ufo.schema...`.

Because this file is empty, it does not create classes, functions, settings, or side effects. Its value is structural. Without it, depending on the Python version and packaging setup, code elsewhere in the project might not be able to reliably import modules inside `core/src/ufo/schema`. That would break schema-related code even though the real work lives in neighboring files.

In short, this file exists so the package layout is clear and import-friendly. It is part of the project’s wiring rather than its feature logic.


### `core/src/ufo/schema/records.py`

`data_model` · `cross-cutting`

This file is like the set of standardized forms used by the system whenever someone asks the assistant to do something. The central idea is a “turn”: one submitted message or internal task that enters a queue, runs, and eventually ends as done, failed, or cancelled. Without these shared record definitions, different parts of the system could disagree about basic facts such as a turn’s status, who sent it, what agent should answer, or what final result was produced.

Most classes here are Pydantic models, meaning they are data containers that also check their own shape and values when created. For example, Turn records include IDs, timestamps, status, input text, optional context, and an optional terminal result. TerminalFrame records describe the final outcome, including text, errors, token usage, cost, and special handoffs such as asking the user a question or requesting credentials.

The file also defines small helper functions that create stable UUIDs. A UUID is a unique identifier; these are derived from known inputs so the same turn, billing entry, or mid-turn reply gets the same ID if work is replayed. That matters because background workflow systems may retry work, and retries must not create duplicate turns, duplicate billing rows, or duplicate messages.

A few validators protect data at the boundary. User-reported context is flattened so it cannot pretend to be structured markup, time zones are checked early, and terminal turn records are required to match their final status.

#### Function details

##### `turn_id_for`  (lines 76–78)

```
def turn_id_for(workspace_id: UUID, conversation_id: UUID, seq: int) -> UUID
```

**Purpose**: Creates the stable ID for a turn from its workspace, conversation, and sequence number. This lets the system recognize the same turn again during a retry instead of treating it as a new request.

**Data flow**: It receives a workspace ID, a conversation ID, and a sequence number. It joins those pieces into one predictable text key and turns that key into a UUID. The output is the turn ID that can also be used as the workflow ID.

**Call relations**: When a surface or queueing path needs to admit a new turn, it can call this helper to name that turn deterministically. The helper hands the actual UUID creation to uuid5, which produces the same UUID whenever the same input text is used.

*Call graph*: 1 external calls (uuid5).


##### `ledger_id_for`  (lines 81–86)

```
def ledger_id_for(workspace_id: UUID, turn_id: UUID, dimension: str, attempt: str='') -> UUID
```

**Purpose**: Creates the stable ID for one billing ledger entry for a turn. It keeps repeated workflow runs from charging the same attempt twice, while still allowing separate resumed attempts to be recorded separately.

**Data flow**: It receives the workspace ID, turn ID, billing dimension, and an optional attempt ID. It combines them into a predictable key and converts that key into a UUID. The result is the billing row ID for that exact turn, dimension, and attempt.

**Call relations**: Billing code can call this when recording token or cost usage. It delegates UUID generation to uuid5 so replaying the same recorded attempt lands on the same ledger row instead of making a duplicate.

*Call graph*: 1 external calls (uuid5).


##### `mid_turn_reply_id_for`  (lines 89–100)

```
def mid_turn_reply_id_for(turn_id: UUID, round_index: int, span_index: int, attempt: str='') -> UUID
```

**Purpose**: Creates the stable ID for a reply that is delivered before a turn fully finishes. This prevents a recovered workflow from sending the same mid-turn message twice to the member.

**Data flow**: It receives the turn ID, the round number, the reply span position inside that round, and an optional attempt ID. It builds a key from those values and converts it into a UUID. The output identifies one specific mid-turn reply.

**Call relations**: Code that records or delivers partial replies can call this while a turn is still running. It relies on uuid5 so replayed work within the same attempt reuses the same reply ID, while a later resumed attempt gets different IDs.

*Call graph*: 1 external calls (uuid5).


##### `TurnContext._tag_safe_line`  (lines 239–243)

```
def _tag_safe_line(cls, value: str | None) -> str | None
```

**Purpose**: Cleans user- or surface-provided context text so it is safe to place inside the engine’s context markup. It removes angle brackets and collapses the value to a single readable line.

**Data flow**: It receives a possible text value for sender, question, or source. If the value is missing, it stays missing. Otherwise, the function removes '<' and '>', splits whitespace, joins the words with single spaces, and returns either the cleaned line or None if nothing remains.

**Call relations**: Pydantic calls this automatically when a TurnContext is created or validated. It runs before the engine later renders the context, so surface-reported text cannot fake tags or inject extra structure.


##### `TurnContext._known_zone`  (lines 247–254)

```
def _known_zone(cls, value: str | None) -> str | None
```

**Purpose**: Checks that a provided time zone name is real before the turn is accepted. This catches bad time zone values at the boundary instead of failing later while the assistant is running.

**Data flow**: It receives a possible time zone string. If no time zone was provided, it returns None. If a value is present, it asks the system time zone database to load it; success returns the original string, while failure raises a clear validation error.

**Call relations**: Pydantic calls this automatically for the timezone field on TurnContext. It uses ZoneInfo to verify names such as IANA time zones, so later code can trust that a stored time zone is usable.

*Call graph*: 1 external calls (ZoneInfo).


##### `Turn.spawned`  (lines 280–283)

```
def spawned(self) -> bool
```

**Purpose**: Tells whether this turn was created by another turn rather than directly admitted as a normal top-level request. In plain terms, it answers: is this a child task?

**Data flow**: It reads the turn’s parent_turn_id field. If that parent ID exists, it returns true; if not, it returns false. It does not change the turn.

**Call relations**: Other code can use this property whenever it needs to distinguish spawned child turns from ordinary turns. The property is based on the convention that only the spawn path writes parent_turn_id.


##### `Turn._aware_utc`  (lines 287–292)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Makes sure turn timestamps always carry UTC time zone information. This protects the system from database drivers that return timestamps without a time zone marker.

**Data flow**: It receives a possible datetime value for created_at or updated_at. If the value is missing, it remains missing. If it already has time zone information, it is returned unchanged; if it is naive, meaning it has no time zone marker, UTC is attached to it.

**Call relations**: Pydantic calls this automatically when Turn timestamps are validated. It uses datetime.replace to attach UTC when needed, so later time calculations do not accidentally treat stored UTC times as local machine time.

*Call graph*: 1 external calls (replace).


##### `Turn._terminal_matches_status`  (lines 295–300)

```
def _terminal_matches_status(self) -> 'Turn'
```

**Purpose**: Checks that a turn’s final-result record agrees with the turn’s status. A running, queued, or parked turn must not already have a terminal result, and a done, failed, or cancelled turn must have one.

**Data flow**: It reads the turn’s status and terminal fields after the model is built. If the presence of terminal does not match whether the status is final, it raises a validation error. If a terminal frame is present but its own status differs from the turn status, it also raises an error. Otherwise, it returns the unchanged turn.

**Call relations**: Pydantic calls this after constructing a Turn. It acts as a final consistency gate so queue readers, workers, and surfaces can trust that a terminal turn has a matching terminal frame and an unfinished turn does not.


### `core/src/ufo/schema/tables.py`

`data_model` · `database setup and all database access`

This file is like the blueprint for the project’s filing cabinet. It says what drawers exist, what labels each drawer has, which records must point to other records, and which mistakes the database should refuse to store. Without it, different parts of the system could disagree about what a workspace, member, agent, conversation, turn, ledger entry, source, page, or shared artifact looks like.

The file uses SQLAlchemy, a Python library for describing and talking to databases, to build a single `metadata` object. That object contains all table definitions. Other code can use it to create tables, run migrations, or build queries without hard-coding database details in many places.

The schema covers the main product concepts: workspaces and members; agents and their settings; conversations and turns; incoming messages and outgoing writebacks; usage billing and balances; external connections and grants; synced sources and pages; credentials; runtime heartbeats; and access records. The many constraints are important guardrails. They make the database reject impossible states, such as a turn with an invalid status, a negative artifact size, duplicate member emails inside one workspace, or a private conversation audience that does not match its member.

One small helper chooses a default conversation audience from the member information being inserted. This keeps newly created conversations consistently marked as shared or member-specific.

#### Function details

##### `_conversation_audience`  (lines 11–12)

```
def _conversation_audience(context: DefaultExecutionContext) -> str
```

**Purpose**: This function supplies the default audience value for a new conversation row. In plain terms, it decides whether the conversation should be treated as shared or tied to a particular member when the row is being inserted.

**Data flow**: It receives SQLAlchemy’s execution context, which contains the values currently being written to the database. It reads the `member_id` from those pending values, passes it to `conversation_audience`, turns the result into text, and returns that text for the conversation’s `audience` column.

**Call relations**: This function is not called directly by ordinary application code. SQLAlchemy calls it when inserting a conversation that has not been given an explicit audience. It asks the execution context for the current row values, then hands the member identifier to `ufo.audience.conversation_audience` so the project’s audience-formatting rule is kept in one place.

*Call graph*: 2 external calls (get_current_parameters, conversation_audience).

## 📊 State Registers Touched

- `reg-effective-config` — The merged settings that tell the whole system what is enabled, safe, and available in this run.
- `reg-database-schema` — The durable database layout and migration version that every runtime component must agree on.
- `reg-db-session` — The active database connection, transaction, and workspace-safe persistence context used while work is running.
- `reg-workspace-roster` — The saved list of workspaces, members, admins, seats, and membership rules.
- `reg-agent-definitions` — The saved assistant agents for each workspace, including their settings, tools, model choices, and provisioning source.
- `reg-onboarding-ledger` — The temporary claims, invitations, verified email proofs, and hosted sign-in records used to create or join workspaces.
- `reg-credential-vault` — The encrypted store of workspace secrets and API keys that tools and connectors can request through guarded paths.
- `reg-connection-grants` — The saved outside-service account connections and the grants saying which agents may use them.
- `reg-conversation-state` — The durable conversation records, titles, audience, surface labels, sandbox links, and visible thread metadata.
- `reg-transcript-state` — The saved message history and transcript snapshots that are read, compacted, updated, audited, and shown later.
- `reg-turn-queue` — The durable queue of conversation turns, including admitted work, claimed work, failures, retries, and completion state.
- `reg-cancellation-state` — The shared stop-and-recovery state used to cancel running turns and prevent abandoned work from continuing.
- `reg-live-delivery` — The live stream and delivery state for partial replies, tool updates, terminal output, final status, and missed messages.
- `reg-runtime-fleet` — The records of which runtime processes and workers are alive, what they own, and when they last checked in.
- `reg-sandbox-state` — The per-conversation sandbox handle, size, filesystem environment, command execution state, and cleanup state.
- `reg-network-egress-policy` — The shared network access rules and freshness counter that tell sandboxes and proxies where code may connect.
- `reg-object-store` — The durable named workspace objects owned by extensions, with their names, data, permissions, and owner routing.
- `reg-blob-artifacts` — The shared file and artifact storage for large bytes, generated files, previews, and signed downloads.
- `reg-surface-ingress` — The shared records that connect external surfaces like web, Slack, shell, OAuth, and inbound messages to conversations and replies.
- `reg-source-feeds` — The registered external content sources, sync cursors, backoff state, ownership, grants, and wake-up triggers.
- `reg-page-index` — The stored pages, revisions, chunks, embeddings, and search indexes used to find synced knowledge later.
- `reg-memory-store` — The durable remembered facts and notes that agents can search, browse, update, consolidate, and show with provenance.
- `reg-scheduled-work` — The saved jobs, scheduled tasks, pauses, monitors, due times, retry state, and duplicate-run guards.
- `reg-workflow-plans` — The longer-running goals, objective steps, blockers, todos, delegated work, and progress evidence that survive across turns.
- `reg-subagent-delivery` — The parent-child turn links and pending result records used when helper agents run work and report back.
- `reg-usage-ledger` — The shared cost and usage records for model calls, tools, sandboxes, connectors, network use, and generated media.
- `reg-spend-controls` — The spend caps, prepaid balances, price table fingerprints, and checks that decide whether work may continue.
- `reg-billing-export` — The billing integration state for exported usage, member counts, Stripe setup, Metronome sync, and BYOK reporting.
- `reg-observability` — The shared logs, traces, metrics, trace links, and sanitized diagnostic records used to understand system behavior.
- `reg-extension-kv-store` — Private per-workspace JSON/key-value state owned by extensions for setup, feature bookkeeping, and small durable extension data that is not a user-visible object.
- `reg-workspace-change-log` — Durable records of file/Git workspace changes attached to conversations so side effects can be recovered, summarized, and rendered safely in portal panels.
- `reg-research-observations` — Durable per-conversation web/search source observations and retrieval metadata saved by research tools for later citation and Sources-panel rendering.
- `reg-db-engine-pool` — The process-wide database engine, DSN binding, and connection pool from which per-request sessions and migration runners obtain connections.
- `reg-listing-cursors` — Opaque pagination and browsing cursor state used to resume stable listings across objects, pages, memory, artifacts, usage records, and portal panels.
