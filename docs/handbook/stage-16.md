# Durable Data, Blob Storage, and Persistence Contracts  `stage-16` (cross-cutting infrastructure)

This stage is the system’s long-term memory. It is shared behind-the-scenes support used during startup, normal request handling, agent turns, background jobs, and shutdown. Its job is to make sure data is saved in forms that other parts of the system can safely read later.

The database side is centered on tables.py, which defines the application’s tables and rules for SQLAlchemy, a library that maps Python objects to database rows. db.py is the safe doorway into that database, making sure each operation is tied to the correct workspace so different customers or projects do not get mixed together. records.py defines the agreed shapes of important events and messages, such as agent turns, user questions, terminal results, and credential requests. transcript.py does the same for saved conversations and summaries.

Large files are handled by blob.py, which hides whether bytes are stored on a local disk or in S3-style cloud storage, while keeping workspace files separate from deployment-wide files. durability.py protects saved workflow data from code changes. schema/__init__.py simply makes the schema folder importable.

## Files in this stage

### Conversation Storage Contracts
Defines the durable transcript and compaction formats that all conversation readers and writers share.

### `core/src/ufo/runtime/turns/transcript.py`

`data_model` · `cross-cutting: used when turns save transcripts, when compactions are written, and when debug or evaluation tools read them back`

A running agent conversation can be long, and different parts of the system need to save and later inspect it: the turn loop writes it, debugging tools show it, and evaluation tools replay or grade it. This file is the common agreement between those parts. Think of it like a labeled filing system: it says which drawer a transcript goes in, what the papers inside must look like, and how they are packed for storage.

The main transcript record is `Conversation`. It stores the message window for a conversation, plus optional details about the system prompt and extra context that were actually shown to the model. That matters because a debug view needs to show the full model input, not only the visible chat messages.

The file also defines compaction records. Compaction is when an older, bulky part of a conversation is summarized so the model can keep working within its context limit. The code stores the window before compaction, the window after compaction, and a structured summary. It also records verification details, such as facts that must not be lost.

All stored blobs are JSON compressed with LZ4, a fast compression format. If bytes cannot be decompressed or no longer match the expected shape, the file raises `TranscriptDecodeError` so callers know the saved data is unusable rather than quietly wrong.

#### Function details

##### `transcript_key`  (lines 42–43)

```
def transcript_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the storage path for a conversation's saved transcript. Callers use it so every part of the system looks in the same place for the same conversation.

**Data flow**: It receives a conversation ID. It places that ID into a fixed path pattern ending in `messages.json.lz4`. The result is a string key that can be used with the blob store.

**Call relations**: This is the shared naming rule for transcript blobs. Writers and readers can use this same rule to avoid inventing different paths for the same saved conversation.


##### `encode`  (lines 46–48)

```
def encode(conversation: Conversation) -> bytes
```

**Purpose**: Turns a `Conversation` object into compressed bytes ready to store. This keeps saved transcripts compact while preserving the exact structured fields readers expect.

**Data flow**: It takes a validated conversation record. It first converts that record into plain data, then serializes it as JSON, meaning a text-based data format, and finally compresses the JSON with LZ4. The output is a bytes object suitable for the blob store.

**Call relations**: This is the write-side companion to `decode`. Transcript-writing code can call it before saving, and later readers can reverse the process with `decode`.

*Call graph*: 2 external calls (model_dump, dumps).


##### `decode`  (lines 51–55)

```
def decode(body: bytes) -> Conversation
```

**Purpose**: Reads compressed transcript bytes back into a validated `Conversation`. It protects callers from corrupt or outdated stored data by raising a clear transcript-specific error.

**Data flow**: It receives raw bytes from storage. It decompresses them, interprets the result as JSON, and checks that the data matches the `Conversation` shape. If that works, it returns a `Conversation`; if not, it raises `TranscriptDecodeError`.

**Call relations**: This is the read-side companion to `encode`. Any reader that fetches a transcript blob can use it to get back a safe, typed conversation record instead of dealing with raw compressed bytes.

*Call graph*: 1 external calls (__init__).


##### `compaction_key`  (lines 141–142)

```
def compaction_key(conversation_id: UUID, index: int, half: CompactionHalf) -> str
```

**Purpose**: Builds the storage path for one part of one compaction record. A compaction has separate saved pieces: the window before, the window after, and the summary.

**Data flow**: It receives a conversation ID, a compaction index, and which half or piece is wanted: `before`, `after`, or `summary`. It combines them into a fixed blob-store key ending in `.json.lz4`.

**Call relations**: The compaction-reading functions call this whenever they fetch compaction data. Because they all use the same path builder, the system has one consistent layout for compaction records.

*Call graph*: called by 2 (read_compaction_after, read_compaction_record).


##### `decode_compaction`  (lines 145–154)

```
def decode_compaction(index: int, before: bytes, after: bytes, summary: bytes) -> CompactionRecord
```

**Purpose**: Turns the three stored byte blobs for a compaction into one usable `CompactionRecord`. It reconstructs the before window, after window, and structured summary together.

**Data flow**: It receives the compaction index and three compressed byte strings: before, after, and summary. It decompresses and validates each piece, pulls out the message windows, and returns a `CompactionRecord`. If any piece is unreadable or has the wrong shape, it raises `TranscriptDecodeError`.

**Call relations**: `read_compaction_record` fetches the three blobs from storage and then hands them to this function. This keeps fetching separate from decoding, like separating picking up envelopes from reading and checking their contents.

*Call graph*: called by 1 (read_compaction_record); 2 external calls (__init__, __init__).


##### `read_compaction_after`  (lines 157–170)

```
async def read_compaction_after(blob: BlobStore, conversation_id: UUID, index: int) -> tuple[Message, ...] | None
```

**Purpose**: Fetches only the `after` window for a specific compaction. This is a lighter read when a caller only needs the compacted replacement window, not the full before-and-summary record.

**Data flow**: It receives a blob store, a conversation ID, and a compaction index. It builds the key for the `after` piece and asks the blob store for it. If the blob is missing, it returns `None`; otherwise it decompresses and validates the saved message window and returns the messages.

**Call relations**: This function calls `compaction_key` to find the right blob and `BlobStore.get` to fetch it. It is useful for readers that want to check the installed compacted window without paying the cost of loading the whole compaction record.

*Call graph*: calls 2 internal fn (get, compaction_key); 1 external calls (__init__).


##### `read_compaction_record`  (lines 173–184)

```
async def read_compaction_record(blob: BlobStore, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Fetches and reconstructs one complete compaction record. It returns `None` when that compaction index does not exist.

**Data flow**: It receives a blob store, a conversation ID, and an index. It builds keys for the `before`, `after`, and `summary` pieces, fetches all three from storage, and if any piece is missing returns `None`. If all are present, it passes the bytes to `decode_compaction` and returns the resulting record.

**Call relations**: This is the shared per-index read path for tools that need full compaction details, such as debugging or evaluation. `read_compaction_records` repeatedly calls it to walk through all saved compactions.

*Call graph*: calls 3 internal fn (get, compaction_key, decode_compaction); called by 1 (read_compaction_records).


##### `read_compaction_records`  (lines 187–197)

```
async def read_compaction_records(blob: BlobStore, conversation_id: UUID) -> tuple[CompactionRecord, ...]
```

**Purpose**: Reads all compaction records for one conversation in order. It stops when it reaches the first missing index.

**Data flow**: It receives a blob store and a conversation ID. Starting at index 1, it asks `read_compaction_record` for each record. Each found record is added to a list; the first `None` means there are no more sequential records. It returns the collected records as an immutable tuple.

**Call relations**: This function builds on `read_compaction_record` to provide the higher-level view: not just one compaction, but the conversation's whole compaction history from oldest to newest.

*Call graph*: calls 1 internal fn (read_compaction_record).


### Storage Transports and Durable Serialization
Provides blob storage, workspace-scoped database access, and resilient object serialization for durable workflow state.

### `core/src/ufo/blob.py`

`io_transport` · `cross-cutting storage access during request handling, background work, and asset publishing`

A “blob” here means an opaque bundle of bytes, such as an uploaded file, a generated artifact, a transcript record, or a static web asset. This file hides where those bytes actually live. Other code can ask to put, get, stream, delete, or list blobs without caring whether the backing store is a folder on disk or an S3 bucket.

The file has three layers. First, BlobStore describes the common promise all blob stores must keep. Second, FilesystemBlobStore and S3BlobStore implement that promise for local files and S3. The filesystem version writes through a temporary file and then swaps it into place, so readers do not see half-written data. The S3 version uses the async S3 client, streams large objects in chunks, and can create short-lived signed URLs so another process can upload or download directly.

Third, WorkspaceBlobStore and FleetBlobStore add safety rails. WorkspaceBlobStore automatically prefixes keys with the current workspace, like giving each tenant its own locked filing cabinet. FleetBlobStore only allows a few deployment-wide prefixes, such as static assets and terminal data. Without these wrappers, a bug could accidentally read or overwrite data belonging to another workspace.

#### Function details

##### `BlobStore.put`  (lines 54–54)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Defines the standard operation for saving a complete byte payload under a string key. Code uses this when the whole object is already in memory.

**Data flow**: A key and bytes go in. A concrete store, such as the filesystem or S3 version, writes those bytes at that key. Nothing is returned, but the stored object should be available afterward.

**Call relations**: This is part of the shared BlobStore promise. Web asset publishing calls on this promise so it can save assets without knowing which storage backend is underneath.

*Call graph*: called by 1 (_publish_assets).


##### `BlobStore.get`  (lines 56–56)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Defines the standard operation for reading a complete stored object into memory. It is the simple read counterpart to put.

**Data flow**: A key goes in. The concrete backend looks up the object and returns its bytes, or raises BlobNotFound if it is absent. The store itself is not changed.

**Call relations**: Transcript reading, Slack identity loading, and web asset serving call through this shared promise. That lets those features read stored data without depending on filesystem or S3 details.

*Call graph*: called by 4 (read_compaction_after, read_compaction_record, read_identity, _stored_asset).


##### `BlobStore.exists`  (lines 58–58)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Defines the standard operation for checking whether a key currently has an object. It is used to avoid unnecessary reads or uploads.

**Data flow**: A key goes in. The backend checks its storage and returns true or false. No blob data is returned and nothing is modified.

**Call relations**: Slack identity loading and web asset code call this before deciding whether to read or publish something. The concrete backend supplies the actual check.

*Call graph*: called by 3 (read_identity, _publish_assets, _stored_asset).


##### `BlobStore.delete`  (lines 60–62)

```
async def delete(self, key: str) -> None
```

**Purpose**: Defines the standard operation for removing an object. Deleting a missing object is allowed, so retrying a delete is safe.

**Data flow**: A key goes in. The backend removes the stored object if it exists. Nothing is returned, and an already-missing key remains missing.

**Call relations**: This is part of the common storage contract that concrete stores follow. Higher-level wrappers can expose deletion without teaching callers backend-specific behavior.


##### `BlobStore.get_stream`  (lines 64–64)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines the standard operation for reading a blob piece by piece instead of all at once. This matters for large files, where loading everything into memory would be wasteful or unsafe.

**Data flow**: A key goes in. The backend opens the object and yields chunks of bytes over time. The caller receives a stream of chunks and can process them as they arrive.

**Call relations**: This method is the streaming read part of the BlobStore promise. Filesystem, S3, workspace, and fleet stores provide concrete versions so large content can flow through the system without a whole-file buffer.


##### `BlobStore.put_stream`  (lines 66–66)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Defines the standard operation for writing a blob from chunks. It is used when the data is produced gradually or may be too large to hold in memory.

**Data flow**: A key and an async stream of byte chunks go in. The backend consumes the chunks and writes one complete object. Nothing is returned, but the object should exist afterward if the stream finishes successfully.

**Call relations**: This is the streaming write part of the shared storage promise. Concrete backends decide how to turn the incoming chunks into a local file or S3 object.


##### `BlobStore.list`  (lines 68–72)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Defines the standard operation for listing stored objects under a required key prefix. It gives callers a bounded view of one area instead of allowing a whole-store scan.

**Data flow**: A prefix goes in. The backend finds matching objects, gathers each key, size, and modification time, sorts or returns them in a stable bounded set, and gives back BlobEntry records.

**Call relations**: Web asset publishing calls this promise to inspect existing stored assets. Concrete stores implement the details for walking a directory tree or paging through S3 results.

*Call graph*: called by 1 (_publish_assets).


##### `FilesystemBlobStore.put`  (lines 81–86)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves a complete byte payload as a file under the configured blob root. It writes to a temporary file first and then replaces the final file, so callers do not see a half-written object.

**Data flow**: A key and bytes go in. The key is resolved to a safe path, parent folders are created, bytes are written to a temporary file, and the temporary file is moved into the final location. Nothing is returned.

**Call relations**: This is the filesystem implementation of BlobStore.put. It relies on _resolve to keep writes inside the blob root and uses background threads for blocking file work.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.get`  (lines 88–93)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a whole blob from the local filesystem. It turns the normal file-not-found error into BlobNotFound so callers get the same missing-object signal across backends.

**Data flow**: A key goes in. The key is converted to a safe file path, the file bytes are read, and those bytes are returned. If the file is missing, BlobNotFound comes out instead.

**Call relations**: This is the filesystem implementation of BlobStore.get. It depends on _resolve for path safety and uses the common BlobNotFound behavior expected by higher layers.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.exists`  (lines 95–97)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a local file exists for a blob key. It is a lightweight way to ask whether a blob is present.

**Data flow**: A key goes in. The key is resolved to a safe path, that path is checked for being a file, and a true or false answer is returned. No file contents are read.

**Call relations**: This is the filesystem implementation of BlobStore.exists. It calls _resolve first so even existence checks cannot probe outside the configured blob root.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.delete`  (lines 99–101)

```
async def delete(self, key: str) -> None
```

**Purpose**: Removes a local blob file if it exists. It treats a missing file as fine, which makes repeated cleanup attempts safe.

**Data flow**: A key goes in. The key is resolved to a safe path, and that file is unlinked if present. Nothing is returned.

**Call relations**: This is the filesystem implementation of BlobStore.delete. It uses _resolve for containment before touching the disk.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.get_stream`  (lines 103–116)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads a local blob in fixed-size chunks. This avoids loading a large file into memory all at once.

**Data flow**: A key goes in. The file is opened safely, chunks are read one by one, and each chunk is yielded to the caller. The file handle is closed when reading finishes or if an error interrupts it.

**Call relations**: This is the filesystem streaming read implementation. It calls _resolve for safety, converts missing files to BlobNotFound, and runs blocking file reads in worker threads.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.put_stream`  (lines 118–131)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes a local blob from an incoming stream of chunks. It uses a temporary file so an interrupted write does not leave a partial final blob behind.

**Data flow**: A key and chunk stream go in. The key becomes a safe path, directories are created, chunks are written to a temporary file, and the temporary file replaces the final path when complete. If anything fails, the temporary file is deleted and the error continues upward.

**Call relations**: This is the filesystem streaming write implementation. It calls _resolve for safe placement and uses background threads for file operations.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.list`  (lines 133–136)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists local blobs under a non-empty prefix. Requiring a prefix prevents accidental full scans of the entire blob store.

**Data flow**: A prefix goes in. If it is empty, an error is raised. Otherwise the directory walk is run in a worker thread and returns matching BlobEntry records.

**Call relations**: This is the public filesystem list operation. It hands the actual walking work to _walk so the async event loop is not blocked by disk traversal.

*Call graph*: 1 external calls (to_thread).


##### `FilesystemBlobStore._walk`  (lines 138–159)

```
def _walk(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Does the actual directory traversal for listing filesystem blobs. It filters out temporary files and only reports keys that match the requested prefix.

**Data flow**: A prefix goes in. The blob root and starting directory are found, files under that area are inspected, matching files become BlobEntry records with size and modification time, and a sorted capped tuple is returned.

**Call relations**: FilesystemBlobStore.list delegates to this helper. It uses _contained_root and _resolve to stay inside the configured storage root while it walks the disk.

*Call graph*: calls 2 internal fn (_contained_root, _resolve); 4 external calls (__init__, fromtimestamp, walk, Path).


##### `FilesystemBlobStore._resolve`  (lines 161–166)

```
def _resolve(self, key: str) -> Path
```

**Purpose**: Turns a blob key into an absolute filesystem path and rejects keys that would escape the blob root. This is the main guard against path tricks like '../secret'.

**Data flow**: A key goes in. The store root is canonicalized, the key is joined to it and resolved, and the resulting path is returned only if it stays inside the root. Unsafe keys raise ValueError.

**Call relations**: All filesystem read, write, delete, stream, and walk operations call this before touching paths. It relies on _contained_root to know the trusted base directory.

*Call graph*: calls 1 internal fn (_contained_root); called by 7 (_walk, delete, exists, get, get_stream, put, put_stream).


##### `FilesystemBlobStore._contained_root`  (lines 168–181)

```
def _contained_root(self) -> Path
```

**Purpose**: Finds the real blob root directory used for filesystem containment checks. It allows the root to be a symlink, which is common in deployments, but still refuses roots configured as non-directories.

**Data flow**: The configured root path is read from the store. The containment helper is asked to validate and canonicalize it; if the path does not exist yet, the resolved intended path is returned so the first write can create it.

**Call relations**: _resolve and _walk call this whenever they need the trusted base path. It connects blob storage safety to the shared containment checks used elsewhere in the system.

*Call graph*: called by 2 (_resolve, _walk); 1 external calls (configured_root).


##### `_is_missing_key`  (lines 184–185)

```
def _is_missing_key(error: ClientError) -> bool
```

**Purpose**: Recognizes S3 error codes that mean an object was not found. It keeps S3's different missing-object names behind one simple check.

**Data flow**: An S3 ClientError goes in. The function reads the error code from the response and returns true if it matches known not-found codes, otherwise false.

**Call relations**: S3BlobStore.get, exists, and get_stream call this when S3 raises an error. It lets those methods translate missing objects into BlobNotFound or false while re-raising real failures.

*Call graph*: called by 3 (exists, get, get_stream).


##### `S3BlobStore.put`  (lines 207–209)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves a complete byte payload as one S3 object. It is the cloud-storage version of the simple put operation.

**Data flow**: A key and bytes go in. The store gets its async S3 client, sends a put-object request to the configured bucket, and returns nothing when S3 accepts it.

**Call relations**: This implements BlobStore.put for S3. It first calls _client so client creation and reuse are centralized.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get`  (lines 211–221)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a whole S3 object into memory. It presents missing S3 objects as BlobNotFound, matching the filesystem backend.

**Data flow**: A key goes in. The S3 object is requested, its response body is read fully, and bytes are returned. If S3 reports the key is missing, BlobNotFound is raised.

**Call relations**: This implements BlobStore.get for S3. It uses _client for access and _is_missing_key to translate S3-specific errors into the shared blob behavior.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.exists`  (lines 223–231)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether an S3 object exists without downloading it. It uses S3's metadata lookup rather than reading the body.

**Data flow**: A key goes in. The store asks S3 for the object's headers. A successful answer becomes true; a missing-key error becomes false; other S3 errors are passed upward.

**Call relations**: This implements BlobStore.exists for S3. It gets the shared client through _client and uses _is_missing_key for consistent missing-object handling.

*Call graph*: calls 2 internal fn (_client, _is_missing_key).


##### `S3BlobStore.delete`  (lines 233–235)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes an object from the S3 bucket. Like S3 itself, deleting something absent is not treated as a problem here.

**Data flow**: A key goes in. The async S3 client sends a delete-object request for that bucket and key. Nothing is returned.

**Call relations**: This implements BlobStore.delete for S3. It relies on _client to provide the correctly configured S3 connection.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get_stream`  (lines 237–248)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads an S3 object chunk by chunk. This is useful for large blobs because callers can forward or process data without storing the whole object in memory.

**Data flow**: A key goes in. The S3 object is opened, its body yields fixed-size chunks, and those chunks are passed to the caller. Missing keys become BlobNotFound, and the response body is closed afterward.

**Call relations**: This implements BlobStore.get_stream for S3. It uses _client to reach S3 and _is_missing_key to keep missing-object behavior consistent with other backends.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.put_stream`  (lines 250–294)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes a streamed blob to S3, using S3 multipart upload for large data. Multipart upload means the object is sent in separate parts and finalized only when all parts arrive.

**Data flow**: A key and stream of chunks go in. Small data is buffered and sent as one object; larger data starts a multipart upload, sends parts, records their part identifiers, and completes the upload at the end. If an error interrupts a multipart upload, it is aborted so unfinished parts do not linger.

**Call relations**: This implements BlobStore.put_stream for S3. It gets the S3 client through _client and then chooses between simple upload and multipart upload based on accumulated size.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_put`  (lines 296–320)

```
async def presigned_put(self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a short-lived upload URL for exactly one measured object. The signed URL fixes the key, size, and SHA-256 checksum, so an untrusted holder cannot upload different bytes under that permission.

**Data flow**: A key, expected byte size, base64 SHA-256 checksum, and expiry time go in. The S3 client signs a put-object URL with those constraints. The resulting URL string comes out.

**Call relations**: WorkspaceBlobStore.presigned_put can forward workspace-scoped requests here when the backend is S3. This method calls _client so the URL is signed by the same configured client that talks to S3.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_put_unmeasured`  (lines 322–332)

```
async def presigned_put_unmeasured(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a short-lived upload URL for a fixed key without fixing the body size or checksum. It is for trusted producers that do not know their final output length before they generate it.

**Data flow**: A key and expiry time go in. The S3 client signs a put-object URL tied to that key and expiry. The URL string is returned.

**Call relations**: WorkspaceBlobStore.presigned_put_unmeasured delegates here for S3-backed workspace storage. This method still uses _client so signing follows the store's endpoint and region settings.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_get`  (lines 334–341)

```
async def presigned_get(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a short-lived download URL for one S3 object. Anyone holding the URL can read that object until the URL expires.

**Data flow**: A key and expiry time go in. The S3 client signs a get-object request for that key. The signed URL string is returned.

**Call relations**: WorkspaceBlobStore.presigned_get delegates here when workspace blobs are stored in S3. The method relies on _client for correct signing.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.put_host`  (lines 343–354)

```
async def put_host(self) -> str
```

**Purpose**: Reports the hostname that presigned upload URLs will contact. The sandbox egress proxy can allow that host so uploads work without opening broader network access.

**Data flow**: No explicit input beyond the store configuration. The method reads the S3 client's endpoint URL, extracts its hostname, adjusts for AWS virtual-hosted bucket style when needed, and returns the hostname. If no hostname exists, it raises an error.

**Call relations**: This method calls _client so the reported host matches the actual signing client. It uses URL parsing to avoid guessing from configuration by hand.

*Call graph*: calls 1 internal fn (_client); 1 external calls (urlsplit).


##### `S3BlobStore.list`  (lines 356–373)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists S3 objects under a required prefix. It returns a bounded set of object summaries rather than scanning without limits.

**Data flow**: A prefix goes in. Empty prefixes are rejected. The method pages through S3 list results, turns each object into a BlobEntry with key, size, and UTC modification time, stops at the configured cap, and returns a tuple.

**Call relations**: This implements BlobStore.list for S3. It uses _client for the S3 paginator and produces the same BlobEntry shape as the filesystem backend.

*Call graph*: calls 1 internal fn (_client); 1 external calls (__init__).


##### `S3BlobStore.close`  (lines 375–381)

```
async def close(self) -> None
```

**Purpose**: Closes the cached S3 client for the current async event loop. This is cleanup for long-lived client resources such as HTTP connections.

**Data flow**: The current event loop is read. Any cached client and lock for that loop are removed from the store, and the client is closed if one existed. Nothing is returned.

**Call relations**: This is the teardown partner to _client. It only affects the client owned by the running event loop, because S3 clients here are loop-specific.

*Call graph*: 1 external calls (get_running_loop).


##### `S3BlobStore._client`  (lines 383–409)

```
async def _client(self) -> AioBaseClient
```

**Purpose**: Returns the one async S3 client for the current event loop, creating it if needed. Reusing clients avoids expensive setup and keeps each client tied to the loop it is safe to use on.

**Data flow**: The running event loop and store configuration go in implicitly. If a client is already cached for that loop, it is returned. Otherwise a per-loop lock prevents duplicate creation, a configured S3 client is opened, cached, and returned.

**Call relations**: Every S3 operation calls this before talking to S3 or generating signed URLs. It centralizes endpoint style, signing version, region, and client reuse so individual methods do not duplicate that delicate setup.

*Call graph*: called by 11 (delete, exists, get, get_stream, list, presigned_get, presigned_put, presigned_put_unmeasured, put, put_host (+1 more)); 3 external calls (get_session, Lock, get_running_loop).


##### `WorkspaceBlobStore.put`  (lines 422–423)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves bytes under the current workspace's private blob prefix. Callers use workspace-relative keys and do not choose the workspace part themselves.

**Data flow**: A workspace-relative key and bytes go in. _full adds the current workspace prefix, then the backend saves the bytes at that full key. Nothing is returned.

**Call relations**: Environment document and file storage call this to save workspace-owned data. It delegates actual storage to the configured filesystem or S3 backend after adding the workspace boundary.

*Call graph*: calls 1 internal fn (_full); called by 2 (store_environment_document, store_environment_file).


##### `WorkspaceBlobStore.get`  (lines 425–426)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads bytes from the current workspace's private blob area. It prevents callers from accidentally reading another workspace by constructing the full key itself.

**Data flow**: A workspace-relative key goes in. _full adds the current workspace prefix, the backend reads the object, and the bytes are returned. Missing data is reported by the backend.

**Call relations**: Environment loading uses this to fetch workspace-owned documents and files. The method is a safety wrapper around the backend get operation.

*Call graph*: calls 1 internal fn (_full); called by 2 (load_environment_document, load_environment_file).


##### `WorkspaceBlobStore.exists`  (lines 428–429)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a blob exists inside the current workspace. It keeps the existence check scoped to the active workspace.

**Data flow**: A workspace-relative key goes in. _full turns it into a full workspace key, the backend checks for that object, and true or false is returned.

**Call relations**: This mirrors the common exists operation but adds workspace scoping first. It uses _full for the boundary and then delegates to the backend.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.delete`  (lines 431–432)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes a blob from the current workspace's storage area. The caller gives only the workspace-relative key.

**Data flow**: A workspace-relative key goes in. _full adds the workspace prefix, and the backend deletes that full key if present. Nothing is returned.

**Call relations**: This wraps backend deletion with workspace safety. It relies on _full to prevent cross-workspace key construction.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.get_stream`  (lines 434–438)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Starts a streaming read from the current workspace. It resolves the workspace key immediately, so the returned stream remains tied to the workspace even if the surrounding workspace scope ends later.

**Data flow**: A workspace-relative key goes in. _full immediately builds the full key, and the backend returns a stream of byte chunks for that object. The caller later consumes those chunks.

**Call relations**: Runtime extension context code uses this to read member blob text. The method delegates streaming to the backend after capturing the workspace prefix at call time.

*Call graph*: calls 1 internal fn (_full); called by 1 (_member_blob_text).


##### `WorkspaceBlobStore.put_stream`  (lines 440–441)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed data into the current workspace's blob area. It is the workspace-scoped version of large or gradual uploads.

**Data flow**: A workspace-relative key and an async chunk stream go in. _full adds the current workspace prefix, and the backend consumes the chunks into that full key. Nothing is returned.

**Call relations**: This wraps backend streaming writes with workspace scoping. It calls _full before handing the stream to the backend.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.list`  (lines 443–448)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists blobs under a prefix inside the current workspace and returns keys relative to that workspace. This lets callers see their own names, not the internal storage prefix.

**Data flow**: A workspace-relative prefix goes in. Empty prefixes are rejected. The workspace root prefix is added, the backend lists full keys, and each returned BlobEntry is copied with the workspace prefix removed from its key.

**Call relations**: This combines _full, the backend list operation, and dataclass replacement to preserve metadata while hiding internal workspace prefixes from callers.

*Call graph*: calls 1 internal fn (_full); 1 external calls (replace).


##### `WorkspaceBlobStore.presigned_put`  (lines 450–461)

```
async def presigned_put(self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a measured presigned S3 upload URL for a blob in the current workspace. It is only valid when the backend is S3.

**Data flow**: A workspace-relative key, size, checksum, and expiry go in. The key is expanded with _full, then the S3 backend signs an upload URL for that full key. If the backend is not S3, a TypeError is raised.

**Call relations**: This is the workspace-safe wrapper around S3BlobStore.presigned_put. It ensures the signed permission cannot target another workspace.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.presigned_put_unmeasured`  (lines 463–469)

```
async def presigned_put_unmeasured(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates an unmeasured presigned S3 upload URL for a fixed key in the current workspace. It is used when the writer is trusted but cannot know the final size ahead of time.

**Data flow**: A workspace-relative key and expiry go in. _full adds the workspace prefix, then the S3 backend signs an upload URL. Non-S3 backends cause a TypeError.

**Call relations**: This wraps S3BlobStore.presigned_put_unmeasured with workspace scoping. It refuses to pretend local filesystem storage can issue S3 URLs.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.presigned_get`  (lines 471–478)

```
async def presigned_get(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a short-lived S3 download URL for a blob in the current workspace. It is only available when the backend is S3.

**Data flow**: A workspace-relative key and expiry go in. _full builds the full workspace key, the S3 backend signs a download URL, and the URL is returned. If storage is not S3, a TypeError is raised.

**Call relations**: This is the workspace-safe wrapper around S3BlobStore.presigned_get. It keeps signed read access inside the active workspace.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore._full`  (lines 480–483)

```
def _full(self, key: str) -> str
```

**Purpose**: Builds the real storage key for a workspace-relative key. It is the small but important gate that prevents callers from supplying an already workspace-prefixed key.

**Data flow**: A key goes in. If it already starts with the internal workspace prefix, ValueError is raised. Otherwise the current workspace id is read and combined into a full key like workspaces/<id>/<key>.

**Call relations**: Every WorkspaceBlobStore operation calls this before touching the backend. It gets the active workspace from ws_current, so workspace binding is enforced at the point of use.

*Call graph*: called by 10 (delete, exists, get, get_stream, list, presigned_get, presigned_put, presigned_put_unmeasured, put, put_stream); 1 external calls (ws_current).


##### `FleetBlobStore.put`  (lines 494–495)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves deployment-wide bytes under an allowed fleet namespace. Fleet data is shared by the deployment rather than owned by a workspace.

**Data flow**: A key and bytes go in. _checked verifies the key starts with an allowed fleet prefix, then the backend writes the bytes. Nothing is returned.

**Call relations**: This wraps backend put with a namespace check. It prevents fleet storage from becoming a back door into workspace data.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.get`  (lines 497–498)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a deployment-wide blob from an allowed fleet namespace. It refuses keys outside the small set reserved for fleet data.

**Data flow**: A key goes in. _checked validates the prefix, the backend reads the object, and bytes are returned. Invalid prefixes raise ValueError.

**Call relations**: This wraps backend get with fleet namespace protection. It delegates actual storage access only after _checked approves the key.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.exists`  (lines 500–501)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether an allowed deployment-wide blob exists. It applies the same namespace rules as fleet reads and writes.

**Data flow**: A key goes in. _checked validates that the key belongs to a fleet prefix, the backend checks for it, and true or false is returned.

**Call relations**: This wraps backend exists with _checked. The check keeps callers from probing arbitrary blob keys.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.delete`  (lines 503–504)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes a deployment-wide blob from an allowed fleet namespace. Invalid namespaces are rejected before the backend is touched.

**Data flow**: A key goes in. _checked approves or rejects it. If approved, the backend deletes that key if present, and nothing is returned.

**Call relations**: This wraps backend delete with fleet namespace enforcement. It uses _checked as the gatekeeper.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.get_stream`  (lines 506–507)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a deployment-wide blob from an allowed fleet namespace. This is the fleet-safe version of chunked reads.

**Data flow**: A key goes in. _checked validates the prefix, then the backend returns a chunk stream for that key. The caller consumes chunks later.

**Call relations**: This wraps backend get_stream with _checked. It allows large fleet blobs to be read without loosening namespace boundaries.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.put_stream`  (lines 509–510)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed data into an allowed deployment-wide namespace. It is used when fleet-owned data may be large or produced in pieces.

**Data flow**: A key and chunk stream go in. _checked validates the key, then the backend consumes the stream into that key. Nothing is returned.

**Call relations**: This wraps backend put_stream with fleet namespace checks. The real write is still performed by the filesystem or S3 backend.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.list`  (lines 512–513)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists deployment-wide blobs under an allowed fleet prefix. It does not allow listing outside the reserved fleet areas.

**Data flow**: A prefix goes in. _checked confirms it starts with an allowed fleet prefix, then the backend returns BlobEntry records under that prefix.

**Call relations**: This wraps backend list with _checked. It gives fleet features listing power only inside their approved namespaces.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore._checked`  (lines 515–518)

```
def _checked(self, key: str) -> str
```

**Purpose**: Verifies that a fleet blob key belongs to one of the allowed deployment-wide prefixes. This is the fleet store's main safety rail.

**Data flow**: A key goes in. If it starts with an allowed prefix such as static/, term/, or apps/, the same key is returned. Otherwise ValueError is raised.

**Call relations**: Every FleetBlobStore operation calls this before delegating to the backend. It keeps fleet storage separate from workspace-prefixed storage.

*Call graph*: called by 7 (delete, exists, get, get_stream, list, put, put_stream).


##### `blob_store_for`  (lines 521–533)

```
def blob_store_for(config: BlobConfig) -> FilesystemBlobStore | S3BlobStore
```

**Purpose**: Builds the concrete blob backend requested by configuration. It turns a BlobConfig into either local filesystem storage or S3 storage.

**Data flow**: A BlobConfig goes in. If it names the filesystem backend, the root path is required and a FilesystemBlobStore is returned. If it names S3, the bucket is required and an S3BlobStore is returned with endpoint and region settings.

**Call relations**: Startup or setup code can call this to create the storage backend used elsewhere. WorkspaceBlobStore and FleetBlobStore can then wrap the returned backend to add scope and namespace safety.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/db.py`

`io_transport` · `startup, request handling, background jobs, migrations, teardown`

This module solves two closely related problems: opening database connections efficiently, and making sure every normal database read or write is scoped to the current workspace. A workspace is like a customer’s separate room in a shared building. The code sets a PostgreSQL session setting called app.workspace_id at the start of each workspace transaction, and database row-level security then uses that value to decide which rows are visible. If no workspace was set, the database fails closed instead of showing everything.

The file keeps database engines private and exposes transaction helpers instead. workspace_tx is the normal path: it opens a transaction, pins the current workspace for that transaction, and yields a connection. owner_tx is the special exception for background jobs that must first list work across all workspaces; callers are expected to re-enter the correct workspace before reading real contents.

The module also builds one connection pool per event loop, because async database connections belong to the event loop that created them. It includes startup checks, cleanup paths, SQLite-specific setup, logging helpers for failed SQL, and Alembic migration support. Without this file, database access would be less reliable, harder to shut down cleanly, and much easier to get wrong in a multi-workspace service.

#### Function details

##### `_build_engine`  (lines 108–118)

```
def _build_engine(url: str, pool: _Pool) -> AsyncEngine
```

*Call graph*: calls 1 internal fn (_pool_kwargs); called by 2 (_engine_for, verify_db_reachable); 1 external calls (create_async_engine).


##### `_pool_kwargs`  (lines 121–141)

```
def _pool_kwargs(url: str, pool: _Pool) -> dict[str, Any]
```

*Call graph*: calls 1 internal fn (_driver_kwargs); called by 1 (_build_engine); 1 external calls (make_url).


##### `_driver_kwargs`  (lines 144–171)

```
def _driver_kwargs(driver: str, pool: _Pool) -> dict[str, Any]
```

*Call graph*: called by 1 (_pool_kwargs).


##### `_engine_for`  (lines 174–190)

```
def _engine_for(url: str, pool: _Pool) -> AsyncEngine
```

*Call graph*: calls 1 internal fn (_build_engine); called by 2 (owner_tx, workspace_tx); 1 external calls (get_running_loop).


##### `init_db`  (lines 193–197)

```
def init_db(url: str) -> None
```


##### `init_owner_db`  (lines 200–214)

```
def init_owner_db(url: str) -> None
```


##### `verify_db_reachable`  (lines 217–237)

```
async def verify_db_reachable() -> None
```

*Call graph*: calls 1 internal fn (_build_engine).


##### `dispose_db`  (lines 240–263)

```
async def dispose_db() -> None
```

*Call graph*: calls 1 internal fn (_hand_off); 1 external calls (get_running_loop).


##### `_hand_off`  (lines 266–273)

```
def _hand_off(loop: asyncio.AbstractEventLoop, engine: AsyncEngine) -> None
```

*Call graph*: called by 1 (dispose_db); 1 external calls (call_soon_threadsafe).


##### `_dispose_on_this_loop`  (lines 276–283)

```
def _dispose_on_this_loop(engine: AsyncEngine) -> None
```

*Call graph*: 2 external calls (ensure_future, dispose).


##### `dispose_loop_engines`  (lines 286–296)

```
async def dispose_loop_engines() -> None
```

*Call graph*: 1 external calls (get_running_loop).


##### `_stopping`  (lines 299–306)

```
def _stopping() -> bool
```

*Call graph*: called by 1 (_opened); 1 external calls (current_task).


##### `_await_opening`  (lines 309–320)

```
async def _await_opening(opening: asyncio.Future[AsyncConnection]) -> tuple[AsyncConnection, asyncio.CancelledError | None]
```

*Call graph*: called by 1 (_opened); 1 external calls (shield).


##### `_await_close`  (lines 323–331)

```
async def _await_close(close: asyncio.Future[bool | None]) -> asyncio.CancelledError | None
```

*Call graph*: called by 1 (_opened); 1 external calls (shield).


##### `_opened`  (lines 335–399)

```
async def _opened(engine: AsyncEngine, path: str) -> AsyncIterator[AsyncConnection]
```

*Call graph*: calls 3 internal fn (_await_close, _await_opening, _stopping); called by 2 (owner_tx, workspace_tx); 7 external calls (Lock, ensure_future, AsyncExitStack, begin, monotonic, emit_histogram, emit_metric).


##### `workspace_tx`  (lines 403–413)

```
async def workspace_tx() -> AsyncIterator[AsyncConnection]
```

*Call graph*: calls 2 internal fn (_engine_for, _opened); 1 external calls (text).


##### `failed_statement`  (lines 416–436)

```
def failed_statement(error: BaseException) -> dict[str, str]
```


##### `owner_tx`  (lines 440–453)

```
async def owner_tx() -> AsyncIterator[AsyncConnection]
```

*Call graph*: calls 2 internal fn (_engine_for, _opened).


##### `apply_migrations`  (lines 456–494)

```
def apply_migrations(url: str, pack: str | None=None) -> None
```

*Call graph*: calls 1 internal fn (_seal_sqlite_journal); 7 external calls (__init__, upgrade, from_config, Path, migration_locations, catch_warnings, simplefilter).


##### `_seal_sqlite_journal`  (lines 497–513)

```
def _seal_sqlite_journal(url: str) -> None
```

*Call graph*: called by 1 (apply_migrations); 2 external calls (make_url, connect).


##### `core_migration_head`  (lines 516–524)

```
def core_migration_head() -> str
```

*Call graph*: 2 external calls (__init__, from_config).


##### `_sqlite_on_connect`  (lines 527–533)

```
def _sqlite_on_connect(dbapi_connection: Any, _connection_record: Any) -> None
```


##### `_sqlite_begin_immediate`  (lines 536–538)

```
def _sqlite_begin_immediate(connection: sa.Connection) -> None
```

*Call graph*: 1 external calls (exec_driver_sql).


### `core/src/ufo/harness/durability.py`

`io_transport` · `cross-cutting persistence and crash recovery`

DBOS stores workflow inputs, step results, and final errors in a database so work can resume after a crash. The hard part is that the code reading old records may not be the same version that wrote them. A normal Python pickle, which is Python’s built-in object-saving format, can restore a Pydantic model without running its usual validation or default-filling logic. That means a model field added later may simply be missing, and recovery can fail in a confusing place.

This file solves that by defining a custom serializer called ReplaySafeSerializer. When it sees a Pydantic BaseModel, it saves the model’s class plus its current field values. When the object is loaded again, it rebuilds the model through Pydantic’s normal validation path. New fields can receive defaults, removed fields can be ignored, and truly missing required fields fail in a clearer way.

It also protects against renamed modules. Old saved records contain old module paths, like an old street address. MOVED_MODULES is the forwarding-address book: during loading, old paths are translated to the current ones. Without this file, recovery could silently return raw strings, fail to find moved classes, or revive model objects in a broken shape.

#### Function details

##### `replay_safe_client`  (lines 176–180)

```
def replay_safe_client(system_database_url: str) -> DBOSClient
```

**Purpose**: This is the approved way to create a DBOS client for this project. It makes sure the client uses the replay-safe serializer, so data written by the workflow engine can also be read back correctly.

**Data flow**: It takes a database URL as input. It creates a ReplaySafeSerializer and gives both the URL and serializer to DBOSClient. The result is a DBOS client connected to the system database and configured to encode and decode this project’s saved records correctly.

**Call relations**: Startup or setup code calls this when it needs a DBOS client. Instead of letting DBOS use its default serializer, this function hands DBOS a ReplaySafeSerializer, which is what later saves and restores workflow data.

*Call graph*: 2 external calls (__init__, DBOSClient).


##### `_rebuild`  (lines 183–184)

```
def _rebuild(model_class: type[BaseModel], fields: dict[str, object]) -> BaseModel
```

**Purpose**: This rebuilds a saved Pydantic model using the model class’s current rules. It exists so old saved model data can be interpreted by today’s model definition rather than being blindly restored.

**Data flow**: It receives a Pydantic model class and a dictionary of saved field values. It asks the model class to validate those values and construct a fresh model object. The output is a normal Pydantic model, with current defaults and validation applied.

**Call relations**: _ModelPickler.reducer_override records this function inside the pickle instructions for Pydantic models. Later, during deserialization, Python’s pickle machinery calls this function to turn the saved class-and-fields pair back into a live model object.


##### `_ModelPickler.reducer_override`  (lines 188–191)

```
def reducer_override(self, obj: object) -> tuple[Callable[..., object], tuple[object, ...]]
```

**Purpose**: This customizes how Pydantic models are written into the pickle stream. Instead of saving them in the fragile default way, it saves instructions to rebuild them safely later.

**Data flow**: It receives each object that the pickler is about to save. If the object is a Pydantic BaseModel, it turns it into a pair: the _rebuild function and the model’s class plus current field dictionary. If the object is not a Pydantic model, it leaves normal pickle behavior in charge.

**Call relations**: ReplaySafeSerializer.serialize uses _ModelPickler to write data. As that pickler walks through the object graph, this method intercepts Pydantic models and redirects them through _rebuild so recovery gets validated, current-version objects.


##### `_CompatUnpickler.find_class`  (lines 195–196)

```
def find_class(self, module: str, name: str) -> object
```

**Purpose**: This helps old saved records find classes after code has been moved between modules. It translates an old module path to the current module path before loading the class.

**Data flow**: It receives the module name and class or function name recorded in the saved pickle. It checks whether the module has a newer location in MOVED_MODULES. It then asks the normal unpickler to load the named symbol from the translated module path and returns that symbol.

**Call relations**: ReplaySafeSerializer.deserialize uses _CompatUnpickler when reading saved data. Whenever the pickle stream names a class or function, this method gets a chance to apply the module forwarding table before normal loading continues.


##### `ReplaySafeSerializer.name`  (lines 202–203)

```
def name(self) -> str
```

**Purpose**: This gives DBOS the stable name for this serializer. DBOS records serializer names with stored rows, so the name must stay consistent for old data to remain readable.

**Data flow**: It takes no outside data beyond the serializer object itself. It returns the fixed string stored in SERIALIZATION_NAME. It does not change anything.

**Call relations**: DBOS calls this as part of its serializer interface when recording or reading rows. The returned name identifies records written in this project’s replay-safe format.


##### `ReplaySafeSerializer.serialize`  (lines 205–208)

```
def serialize(self, data: object) -> str
```

**Purpose**: This turns a Python object into a text string that can be stored in the database. It uses the custom pickler so Pydantic models are saved in the safer class-plus-fields form.

**Data flow**: It receives any Python object. It creates an in-memory byte buffer, pickles the object into that buffer using _ModelPickler, then base64-encodes the bytes into plain UTF-8 text. The output is a string suitable for database storage.

**Call relations**: DBOS calls this when it needs to persist workflow inputs, outputs, or errors. During the pickling step, _ModelPickler.reducer_override may take over for Pydantic models so that later replay can rebuild them safely.

*Call graph*: 3 external calls (__init__, b64encode, BytesIO).


##### `ReplaySafeSerializer.deserialize`  (lines 210–211)

```
def deserialize(self, serialized_data: str) -> object
```

**Purpose**: This turns a stored database string back into the original Python data. It uses the compatibility unpickler so old module names can still resolve after files have moved.

**Data flow**: It receives a base64 text string from storage. It decodes the text back into bytes, wraps those bytes in an in-memory stream, and loads the pickle using _CompatUnpickler. The output is the restored Python object or data structure.

**Call relations**: DBOS calls this when replaying or recovering stored workflow data. As objects are loaded, _CompatUnpickler.find_class translates old module paths, and any saved Pydantic model rebuild instructions call _rebuild to construct current-version models.

*Call graph*: 3 external calls (__init__, b64decode, BytesIO).


### Schema Records and Tables
Declares the importable schema package, shared runtime record contracts, and SQLAlchemy database table layout.

### `core/src/ufo/schema/__init__.py`

`other` · `import time`

In Python, a folder usually needs an `__init__.py` file to be treated as an importable package. This file plays that role for the `ufo.schema` area of the project. A “schema” usually means a formal description of what data should look like, such as the expected fields in a message or configuration object. Even though this file is empty, it still matters because it gives the project a stable package location for schema code. Without it, imports that expect `ufo.schema` to be a normal Python package could fail or behave differently depending on the Python version and tooling. Think of it like a labeled drawer in a filing cabinet: the drawer may not contain instructions itself, but its label lets everyone find the papers inside in a predictable way.


### `core/src/ufo/schema/records.py`

`data_model` · `cross-cutting: turn admission, queueing, execution, completion, and record loading`

This file is like the project’s official form cabinet for agent work. A “turn” is one unit of conversation or action: someone asks something, an agent runs, and the system eventually records a finished result, failure, cancellation, or request for more input. The models here make sure every part of the system writes and reads that information in the same shape.

Most records are Pydantic models, which are Python objects that also validate their data. That matters because these records cross boundaries: web clients, workers, databases, sandboxes, billing, and account-connection flows all touch them. Without these shared definitions, one component could mark a turn finished while another still thinks it is running, or a bad timezone or unsafe text could break prompt rendering later.

The file also contains small decision helpers. Some choose which queue a turn should enter, some create stable UUIDs from workspace and turn data, and one picks a readable icon for a new agent. Several validators protect important invariants: terminal data must match terminal status, timestamps are treated as UTC, runtime image information must be complete, and context text is flattened so user-supplied text cannot fake markup. In short, this file keeps the project’s durable records boring, predictable, and safe.

#### Function details

##### `auto_agent_icon`  (lines 177–196)

```
def auto_agent_icon(name: str, taken: Collection[str]) -> TablerIcon
```

**Purpose**: Chooses a starting icon for a new agent based on its name and the icons already used in the workspace. It tries to pick something meaningful first, then something stable and visually distinct.

**Data flow**: It receives an agent name and a collection of already-taken icon names. It lowercases and splits the name into simple tokens, looks for a keyword such as “billing” or “code,” and uses that matching icon if it is free. If not, it hashes the name with SHA-256 and uses that number to pick from unused icons; if every icon is already taken, it allows a repeat. It returns one icon name.

**Call relations**: This helper stands at agent creation time, when the system needs a default visual mark. Its only outside call is to the standard SHA-256 hash function, used so the same name tends to get the same fallback icon instead of a random one.

*Call graph*: 1 external calls (sha256).


##### `admits_spent_balance`  (lines 221–235)

```
def admits_spent_balance(intent: ToolIntent) -> bool
```

**Purpose**: Checks whether a prepared tool action should still be allowed even when a workspace has spent its balance. The special allowed action is opening workspace billing, because blocking it would prevent the user from fixing the billing problem.

**Data flow**: It receives a ToolIntent, which is a pre-made tool call that will be run exactly as submitted. It checks whether the tool is an object action, and whether its kind and action are exactly the workspace billing action. It returns true only for that one case, and changes nothing.

**Call relations**: Billing gates can call this when deciding whether to reject a prepared intent. This function does not hand work off to another project function; it simply answers the narrow policy question: “Is this the billing-management action that must remain available?”


##### `turn_queue_for`  (lines 249–257)

```
def turn_queue_for(parent_turn_id: UUID | None, admission_source: 'TurnAdmissionSource') -> str
```

**Purpose**: Decides which work queue a turn should use. Normal top-level agent turns go to the regular turns queue, while child turns and prepared intents go to the faster express queue to avoid deadlocks and unnecessary waiting.

**Data flow**: It receives an optional parent turn id and the turn’s admission source. If there is a parent turn, or if the turn came from a prepared intent, it returns the express queue name. Otherwise it returns the normal turns queue name.

**Call relations**: This is used when a surface or internal workflow admits a turn. It gives the queueing layer the right queue name so workers can start the turn under the right capacity rules.


##### `turn_id_for`  (lines 266–268)

```
def turn_id_for(workspace_id: UUID, conversation_id: UUID, seq: int) -> UUID
```

**Purpose**: Creates the stable id for a turn from its workspace, conversation, and sequence number. The same inputs always produce the same UUID, which helps retries avoid creating duplicate turns.

**Data flow**: It receives a workspace id, conversation id, and turn sequence number. It combines them into a string and passes that string to UUID version 5, which creates a deterministic UUID from a namespace and name. It returns that UUID.

**Call relations**: Turn admission code can call this before storing or starting a turn. It hands off to the standard UUID function so the resulting id can also be used as the durable workflow identity.

*Call graph*: 1 external calls (uuid5).


##### `ledger_id_for`  (lines 271–276)

```
def ledger_id_for(workspace_id: UUID, turn_id: UUID, dimension: str, attempt: str='') -> UUID
```

**Purpose**: Creates a stable billing-ledger id for one turn, one billing dimension, and one run attempt. This lets repeated replay of the same attempt collapse into one billing row, while a resumed attempt can be billed separately.

**Data flow**: It receives a workspace id, turn id, billing dimension such as a token category, and an optional attempt id. It builds a string from those values and turns it into a deterministic UUID. It returns that UUID and does not modify anything.

**Call relations**: Billing-writing code can call this when recording usage for a turn. It relies on the standard UUID version 5 function so replayed workflow steps write to the same ledger identity instead of duplicating charges.

*Call graph*: 1 external calls (uuid5).


##### `mid_turn_reply_id_for`  (lines 279–290)

```
def mid_turn_reply_id_for(turn_id: UUID, round_index: int, span_index: int, attempt: str='') -> UUID
```

**Purpose**: Creates a stable id for a reply that is sent before a turn fully ends. This prevents a recovered workflow from delivering the same mid-turn message twice, while still allowing a later resumed run to produce new messages.

**Data flow**: It receives the turn id, the round number, the span position inside that round, and an optional run attempt id. It combines those into a deterministic UUID name and returns the UUID created from it.

**Call relations**: Code that records or delivers partial replies during a running turn can call this. It hands off to UUID version 5 so repeated replay of the same attempt points to the same delivery record.

*Call graph*: 1 external calls (uuid5).


##### `RuntimeIdentity._artifact_pair`  (lines 446–449)

```
def _artifact_pair(self) -> 'RuntimeIdentity'
```

**Purpose**: Validates that runtime revision and image digest are either both present or both absent. This prevents a record from naming only half of the deployed artifact it ran on.

**Data flow**: It reads the RuntimeIdentity being built. If exactly one of revision or image digest is missing, it raises a validation error. If the pair is complete or entirely absent, it returns the same object.

**Call relations**: Pydantic calls this automatically after creating a RuntimeIdentity. Other code benefits because any accepted RuntimeIdentity has a consistent description of the service image and configuration used for a turn.


##### `TurnRuntimeConfig._pinned_values`  (lines 468–476)

```
def _pinned_values(self) -> 'TurnRuntimeConfig'
```

**Purpose**: Checks that per-turn runtime overrides are concrete and well-formed. A turn may pin a real model id or a stored environment document digest, but not vague values such as “auto.”

**Data flow**: It reads the TurnRuntimeConfig being built. If the model field is the string “auto,” it raises an error because a pinned turn must name a specific model. If an environment value exists, it must match the expected SHA-256 digest format. Valid data is returned unchanged.

**Call relations**: Pydantic runs this during TurnRuntimeConfig construction. Turn execution code can then trust that any runtime override attached to a turn is specific enough to reproduce and audit.


##### `TurnContext._tag_safe_line`  (lines 533–537)

```
def _tag_safe_line(cls, value: str | None) -> str | None
```

**Purpose**: Cleans surface-provided text so it can be safely placed into prompt context. It removes angle brackets and collapses the text to one line, which stops the text from pretending to be structured markup.

**Data flow**: It receives one optional string field, such as sender, question, or source. If the value is missing, it stays missing. Otherwise the function removes “<” and “>”, splits whitespace, rejoins it with single spaces, and returns the cleaned line, or null if nothing remains.

**Call relations**: Pydantic calls this automatically for selected TurnContext fields. Prompt-building code later reads those fields knowing they are plain one-line facts rather than text that can forge tags.


##### `TurnContext._known_zone`  (lines 541–548)

```
def _known_zone(cls, value: str | None) -> str | None
```

**Purpose**: Validates that a timezone name is real. This catches bad timezone data at the boundary, before a turn is running and needs to format times.

**Data flow**: It receives an optional timezone string. If there is no value, it returns no value. If there is a value, it asks Python’s timezone database to load it; unknown names cause a validation error, while known names are returned unchanged.

**Call relations**: Pydantic calls this when TurnContext is created. It uses the standard ZoneInfo lookup, so later engine code can rely on the timezone string being one the system understands.

*Call graph*: 1 external calls (ZoneInfo).


##### `Turn.spawned`  (lines 583–586)

```
def spawned(self) -> bool
```

**Purpose**: Answers whether this turn was created as a child of another turn. In this system, a spawned turn is identified by having a parent turn id.

**Data flow**: It reads the Turn’s parent_turn_id field. If that field is present, it returns true; otherwise it returns false. It does not change the turn.

**Call relations**: Other code can read this property when it needs to treat spawned child turns differently from top-level turns, such as for queueing, result delivery, or parent-child bookkeeping.


##### `Turn._nothing_created`  (lines 590–593)

```
def _nothing_created(cls, value: object) -> object
```

**Purpose**: Normalizes a missing created-objects column into an empty tuple. This lets the rest of the code treat “nothing was created” as an empty list-like value instead of worrying about database nulls.

**Data flow**: It receives the raw value for created_refs before normal validation. If the value is null, it returns an empty tuple. Any other value is passed through for normal parsing.

**Call relations**: Pydantic calls this while loading or building a Turn. Storage may return SQL NULL for no created objects, and this validator turns that into the safer in-memory shape expected by turn and cancellation logic.


##### `Turn._aware_utc`  (lines 597–602)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Ensures turn timestamps carry UTC timezone information. This avoids accidentally interpreting database timestamps as local machine time.

**Data flow**: It receives a created_at or updated_at datetime, or no value. Missing values stay missing. If the datetime already has timezone information, it is returned as-is; if not, the function marks it as UTC using datetime.replace.

**Call relations**: Pydantic calls this for Turn timestamp fields. Database drivers can sometimes return timestamps without timezone markers, and this validator repairs that before scheduling, display, or comparison code uses the time.

*Call graph*: 1 external calls (replace).


##### `Turn._terminal_matches_status`  (lines 605–610)

```
def _terminal_matches_status(self) -> 'Turn'
```

**Purpose**: Checks that a turn’s status and terminal result agree. A running, queued, or parked turn must not have a terminal frame, and a finished, failed, or cancelled turn must have one with the same status.

**Data flow**: It reads the whole Turn after its fields are built. If the status is non-terminal but terminal data exists, or if the status is terminal but terminal data is missing, it raises a validation error. It also rejects terminal data whose own status does not match the turn status. Valid turns are returned unchanged.

**Call relations**: Pydantic runs this after Turn creation or loading. Downstream code can then rely on one clear rule: terminal details are present exactly when the turn is truly finished, failed, or cancelled.


### `core/src/ufo/schema/tables.py`

`data_model` · `database setup and any runtime code that builds or queries tables`

This file is the project’s database blueprint. Like an architect’s floor plan, it does not store the data itself; it describes where every kind of data belongs and which rules the database must enforce. Without it, different parts of the system could disagree about what a workspace, member, agent, conversation, turn, billing record, connector, source, or synced page should look like.

The file creates one shared SQLAlchemy metadata object. Metadata is SQLAlchemy’s collection of table definitions. Each table then describes a real concept in the product: workspaces contain members; members and agents participate in conversations; conversations contain turns; billing usage goes into a ledger; external services are represented through connections, sources, grants, cursors, and listener claims.

A lot of the value here is in the guardrails. Foreign keys link rows that must belong together, such as a conversation pointing to its workspace and agent. Unique rules prevent duplicates, such as two members with the same email in one workspace. Check constraints stop invalid states, such as a turn having an impossible status or an archived main agent. Indexes help the database find common records quickly, such as pending work, live turns, or due writebacks.

The file also includes one small defaulting helper for conversation audience. When a conversation row is inserted, it can derive whether the conversation is shared or tied to a specific member.

#### Function details

##### `_conversation_audience`  (lines 12–13)

```
def _conversation_audience(context: DefaultExecutionContext) -> str
```

**Purpose**: This function supplies a default value for a conversation’s audience when a new conversation is inserted. It turns the current row’s member ID, if any, into the standard audience string used by the rest of the system.

**Data flow**: It receives SQLAlchemy’s execution context, which is the database toolkit’s snapshot of the row currently being written. It reads the pending row values, pulls out `member_id`, passes that to `conversation_audience`, and returns the resulting audience as text. The database insert then uses that text as the conversation’s audience value.

**Call relations**: This helper is attached to the `conversation` table’s `audience` column as a Python-side default. When SQLAlchemy inserts a conversation and no audience was explicitly supplied, SQLAlchemy calls this function. The function delegates the actual audience-format decision to `ufo.runtime.turns.audience.conversation_audience`, so this schema file stays aligned with the runtime’s audience rules.

*Call graph*: 2 external calls (get_current_parameters, conversation_audience).

## 📊 State Registers Touched

- `reg-effective-config` — The merged settings that tell the whole system how it should run in this deployment.
- `reg-durable-database` — The main long-term database where shared business and runtime records are stored.
- `reg-workspace-member-agent-state` — The saved list of workspaces, people, memberships, seats, and agents.
- `reg-agent-configuration` — Each agent’s saved settings, such as model choice, reasoning mode, tools, visibility, internet access, sandbox size, and setup needs.
- `reg-extension-install-store` — The saved record of which extensions are installed, removed, or holding extension-specific data.
- `reg-surface-routing` — The shared routing state that maps browser, Slack, iMessage, terminal, site, and object requests to the right workspace, agent, and conversation.
- `reg-credential-connections` — The encrypted accounts, secrets, connection grants, and credential fulfillments that let agents use outside services safely.
- `reg-access-permissions-audience` — The shared rules for who may read, use, share, or act on workspace content and conversations.
- `reg-egress-policy-proxy` — The network allowlist and proxy state that decide which outside hosts sandboxed work may contact.
- `reg-billing-spend-ledger` — The shared accounting state for spend caps, usage charges, prepaid balances, BYOK billing, and ledger exports.
- `reg-memory-index-state` — The stored knowledge, embeddings, chunks, and memory indexes that agents can search later.
- `reg-source-config-sync-state` — The configured external sources plus their sync progress, errors, backoff, ownership, and access grants.
- `reg-skill-prompt-library` — The reusable instructions, skills, prompt rules, and agent setup guidance loaded into turns.
- `reg-conversation-turn-queue` — The durable state of conversations and turns, including admission, ordering, current runner, lifecycle status, and queued work.
- `reg-inbound-message-queue` — The saved queue of incoming external messages waiting to be rendered, ordered, deduplicated, and admitted as turns.
- `reg-transcript-history` — The saved conversation transcript, summaries, compactions, and access records that preserve what happened in a chat.
- `reg-sandbox-runtime` — The durable sandbox and browser workspace handles where agent commands, files, web browsing, and hosted previews run safely.
- `reg-blob-artifact-store` — The shared file, blob, artifact, preview, download, and hosted media storage used by turns and surfaces.
- `reg-presentation-slots` — The shared conversation display slots for showing artifacts, sources, tasks, sites, automations, image previews, and other side-panel content.
- `reg-background-jobs` — The shared job schedule, due-work candidates, claims, retries, and worker state for background and autonomous work.
- `reg-runtime-fleet-heartbeats` — The fleet-wide record of which runtime processes are alive and what work they may be responsible for.
- `reg-delegation-workflows` — The saved state for subagents, parent-child turns, objectives, workflow checkpoints, pending deliveries, and recovery.
- `reg-object-journal` — The shared naming and change history for workspace objects such as tasks, prompts, skills, monitors, memories, and reports.
- `reg-observability-trace` — The logs, metrics, traces, health signals, and trace links used to understand what the system is doing.
- `reg-schema-migration-version` — The Alembic/database schema version state that records which migrations have been applied and gates safe startup against the expected database shape.
- `reg-database-connection-pool` — The shared SQLAlchemy engine/session and connection-pool state used by requests, turns, workers, migrations, and persistence helpers to access the database safely.
- `reg-surface-listener-leases` — The stored claims/leases that coordinate which runtime instance is allowed to listen on a shared surface installation or address, avoiding duplicate external listeners.
- `reg-outbound-surface-delivery-queue` — The durable outgoing reply/writeback state, including mid-turn replies and surface deliveries that must be claimed, sent, retried, and acknowledged exactly once.
- `reg-conversation-workspace-change-state` — The persisted record of file/workspace changes detected for a conversation sandbox, used for commit summaries, artifact presentation, recovery, and debugging.
- `reg-workspace-object-store` — The current persisted workspace object records, such as tasks, monitors, todos, reports, prompts, and site metadata, read and mutated through object APIs, tools, jobs, and slots.
- `reg-turn-runtime-snapshot` — The per-turn frozen runtime configuration and generated references used to run, recover, bill, and debug a turn consistently after settings change.
- `reg-execution-step-log` — The structured persisted model, tool, and workflow execution records that power debugger timelines and post-run inspection beyond the user transcript.
- `reg-source-page-corpus` — The canonical stored page/document records fetched from sources, including content and browse metadata before they are chunked, embedded, searched, or displayed.
- `reg-proposal-approval-state` — Persisted proposed changes with before/after payloads, authoring information, and pending/approved/rejected status used for review and application workflows.
