# Durable records and binary objects  `stage-19.2` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support for keeping important data safe and readable over time. It defines the system’s long-term records: conversations, agent settings, questions, credentials, job state, artifacts, and stored files.

The schema records file is the common language for live data such as agents, turns, runtime settings, and turn results, so the user interface, workers, billing, and runtime engine all mean the same thing. The schema tables file turns that language into database tables, with columns, links, defaults, and safety rules that work in both local SQLite and deployed PostgreSQL.

Conversation history gets special care. The turns transcript model defines the saved transcript format, including compacted versions used when long conversations are shortened. The runtime transcript file reads and writes those transcripts in shared storage, while guarding against an older or smaller copy replacing a newer one.

Binary objects, or “blobs,” such as artifacts and source files, go through one storage layer that can use local files or S3-style cloud storage. Durability support makes old saved workflow data still load correctly after code changes.

## Files in this stage

### Transcript record format
Defines the durable conversation and compaction data shape shared by writers, readers, debuggers, and evaluators.

### `core/src/ufo/runtime/turns/transcript.py`

`data_model` · `cross-cutting transcript persistence and readback`

This file is the contract for durable conversation history: the messages, summaries, and verification details that get stored outside a running process. Think of it like the label format and packing instructions for boxes in a shared archive. If the writer labels a box one way and the reader expects another, old conversations become unreadable.

The main record is `Conversation`, which stores the current message window, its sequence number, and optional details about the exact system prompt and injected context used during a model turn. The file also defines how that record is turned into compact JSON and compressed with LZ4, a fast compression format, before being put in blob storage.

The second half covers compaction. Compaction means replacing an older, long stretch of conversation with a shorter summary plus the important recent tail, so the model context does not grow forever. The file defines what a summary must contain, what facts must survive the summarizing step, how verification results are stored, and where each compaction artifact lives in the blob store.

The important behavior is strict validation. Stored bytes are not trusted blindly. They are decompressed, parsed, and checked against typed models. If the bytes are corrupt or no longer match the expected shape, the code raises `TranscriptDecodeError` so callers know the saved record cannot safely be used.

#### Function details

##### `transcript_key`  (lines 42–43)

```
def transcript_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the storage path for the main saved transcript of one conversation. Callers use this so every part of the system agrees on the exact blob-store location for conversation messages.

**Data flow**: It takes a conversation ID, which is a unique identifier, and places it into a fixed path pattern. The result is a string such as a file path inside the blob store, ending in `messages.json.lz4` to show it is compressed JSON.

**Call relations**: This is the shared naming rule for transcript storage. Writers and readers can use the same key-building rule so they meet at the same stored blob instead of saving and looking in different places.


##### `encode`  (lines 46–48)

```
def encode(conversation: Conversation) -> bytes
```

**Purpose**: Turns a validated `Conversation` object into compressed bytes ready to store. This keeps saved transcripts small and gives all writers one common way to serialize the record.

**Data flow**: It receives a `Conversation`, asks the model for its plain data form, converts that data to compact JSON text, encodes the text as bytes, and compresses those bytes with LZ4. The output is the byte string that can be written to durable storage.

**Call relations**: This is the write-side partner to `decode`. It relies on the `Conversation` model’s own dump behavior and standard JSON conversion before compression, so later readers can reverse the process in a predictable order.

*Call graph*: 2 external calls (model_dump, dumps).


##### `decode`  (lines 51–55)

```
def decode(body: bytes) -> Conversation
```

**Purpose**: Turns stored compressed transcript bytes back into a validated `Conversation`. It protects callers from using damaged or incompatible transcript data.

**Data flow**: It receives compressed bytes, decompresses them, and asks `Conversation` to validate the JSON inside. If that succeeds, the caller gets a usable `Conversation`; if decompression or validation fails, the function raises `TranscriptDecodeError` instead of returning questionable data.

**Call relations**: This is the read-side partner to `encode`. When a stored transcript is read back, this function is the gatekeeper that either restores the conversation record or clearly reports that the blob cannot be decoded.

*Call graph*: 1 external calls (__init__).


##### `compaction_key`  (lines 141–142)

```
def compaction_key(conversation_id: UUID, index: int, half: CompactionHalf) -> str
```

**Purpose**: Builds the storage path for one piece of one compaction record. A compaction has separate stored parts, and this function makes sure all code uses the same naming scheme for them.

**Data flow**: It takes a conversation ID, a compaction index, and a half name such as `before`, `after`, or `summary`. It combines them into a blob-store path under that conversation’s compaction folder and marks the contents as compressed JSON.

**Call relations**: `read_compaction_after` and `read_compaction_record` call this before fetching data from the blob store. It is the small routing step that tells those readers exactly where each compaction artifact should be found.

*Call graph*: called by 2 (read_compaction_after, read_compaction_record).


##### `decode_compaction`  (lines 145–154)

```
def decode_compaction(index: int, before: bytes, after: bytes, summary: bytes) -> CompactionRecord
```

**Purpose**: Rebuilds a complete compaction record from its three stored byte blobs: the window before compaction, the window after compaction, and the summary. It gives readers one typed object instead of three raw storage blobs.

**Data flow**: It receives the compaction index and three compressed byte strings. It decompresses and validates the `before` and `after` message windows, decompresses and validates the summary, and packages them into a `CompactionRecord`. If any part is corrupt or does not match the expected shape, it raises `TranscriptDecodeError`.

**Call relations**: `read_compaction_record` fetches the three blobs, then hands them to this function for decoding. This separates storage lookup from format checking: one function gets the bytes, and this one proves they form a valid compaction record.

*Call graph*: called by 1 (read_compaction_record); 2 external calls (__init__, __init__).


##### `read_compaction_after`  (lines 157–170)

```
async def read_compaction_after(blob: BlobStore, conversation_id: UUID, index: int) -> tuple[Message, ...] | None
```

**Purpose**: Fetches only the `after` window for one compaction. This is useful when a caller only needs the compact replacement window and does not want to pay the cost of reading the full pre-compaction history.

**Data flow**: It receives a blob store, a conversation ID, and a compaction index. It builds the key for the `after` blob, asks the blob store for it, and returns `None` if the blob is missing. If the blob exists, it decompresses and validates the message window, returning the messages or raising `TranscriptDecodeError` if the stored data is bad.

**Call relations**: This function calls `compaction_key` to find the right blob, then calls the blob store’s `get` method to retrieve it. It is the lightweight read path for code that wants to compare or inspect the installed post-compaction window without loading the whole record.

*Call graph*: calls 2 internal fn (get, compaction_key); 1 external calls (__init__).


##### `read_compaction_record`  (lines 173–184)

```
async def read_compaction_record(blob: BlobStore, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Fetches one full compaction record from storage. It returns the complete before-and-after story of a compaction, or `None` if that compaction index has not been written.

**Data flow**: It receives a blob store, a conversation ID, and an index. It builds and fetches the `before`, `after`, and `summary` blobs. If any expected blob is missing, it returns `None`; if all are present, it passes the bytes to `decode_compaction` and returns the resulting `CompactionRecord`.

**Call relations**: This is the shared full-record reader. `read_compaction_records` calls it repeatedly while walking through all saved compactions. Inside, it uses `compaction_key` for consistent storage paths and `decode_compaction` for validation and assembly.

*Call graph*: calls 3 internal fn (get, compaction_key, decode_compaction); called by 1 (read_compaction_records).


##### `read_compaction_records`  (lines 187–197)

```
async def read_compaction_records(blob: BlobStore, conversation_id: UUID) -> tuple[CompactionRecord, ...]
```

**Purpose**: Reads all compaction records for a conversation in order. It gives callers the full compaction history without requiring them to know how many records exist.

**Data flow**: It starts at compaction index 1 and asks `read_compaction_record` for each record. Each found record is added to a list. When an index returns `None`, meaning there is no record there, the loop stops and the collected records are returned as an immutable tuple.

**Call relations**: This function is the simple walking reader built on top of `read_compaction_record`. Because compaction indices are written sequentially, the first missing record acts like the end marker for the conversation’s compaction history.

*Call graph*: calls 1 internal fn (read_compaction_record).


### Blob-backed persistence
Provides binary object storage, compatibility for persisted workflow data, and safe transcript reads and writes through the shared blob layer.

### `core/src/ufo/blob.py`

`io_transport` · `cross-cutting`

This file is the project’s “blob storage” layer. A blob is just a saved pile of bytes, such as an uploaded file, a transcript record, a generated asset, or terminal output. The rest of the system should not have to care whether those bytes are stored as files on disk or as objects in an S3 bucket, so this file defines one shared shape, BlobStore, and then provides concrete versions for disk and S3.

The filesystem version turns each slash-separated key into a file under a configured root folder. It writes through a temporary file and then swaps it into place, like writing a new page beside an old one before replacing it, so readers do not see half-written data. It also checks that keys cannot escape the storage root.

The S3 version talks to cloud object storage using an asynchronous client, meaning it can wait on the network without blocking other work. It supports whole-object reads and writes, streamed reads and writes for large files, listing by prefix, and short-lived signed URLs that let another process upload or download one specific object.

Two wrapper stores add safety boundaries. WorkspaceBlobStore automatically prefixes keys with the current workspace ID. FleetBlobStore only allows a small set of deployment-wide prefixes. Without these wrappers, one part of the system could accidentally read or overwrite another workspace’s files.

#### Function details

##### `BlobStore.put`  (lines 54–54)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Defines the common operation for saving a complete blob as one byte string. Code can call this without knowing whether the storage is local disk or S3.

**Data flow**: A key and bytes go in. The chosen store writes those bytes under that key. Nothing is returned, but the stored object should be available afterward.

**Call relations**: Publishing web assets uses this protocol method when it wants to store finished bytes. The actual work is done by whichever implementation is behind the BlobStore, such as FilesystemBlobStore or S3BlobStore.

*Call graph*: called by 1 (_publish_assets).


##### `BlobStore.get`  (lines 56–56)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Defines the common operation for reading a complete blob into memory. It is for data that is small enough to fetch as one byte string.

**Data flow**: A key goes in. The store looks up the stored object and returns its bytes, or raises BlobNotFound if the key does not exist.

**Call relations**: Transcript reading, Slack identity loading, and web asset serving call this common method. Those callers do not need to know which storage backend is currently configured.

*Call graph*: called by 4 (read_compaction_after, read_compaction_record, read_identity, _stored_asset).


##### `BlobStore.exists`  (lines 58–58)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Defines the common check for whether a blob is present. It lets callers avoid re-uploading or trying to serve missing data.

**Data flow**: A key goes in. The store checks its backend and returns true if an object exists at that key, false otherwise.

**Call relations**: Slack identity lookup and web asset publishing use this before deciding whether to read, write, or serve stored data. The concrete backend supplies the real check.

*Call graph*: called by 3 (read_identity, _publish_assets, _stored_asset).


##### `BlobStore.delete`  (lines 60–62)

```
async def delete(self, key: str) -> None
```

**Purpose**: Defines the common operation for removing a blob. Deleting a missing key is intentionally harmless, which makes retrying cleanup safe.

**Data flow**: A key goes in. The store removes the object if it exists. Nothing is returned, and absent objects are treated as already deleted.

**Call relations**: This is part of the shared storage contract. Filesystem, S3, workspace, and fleet stores provide matching behavior so cleanup code can be backend-independent.


##### `BlobStore.get_stream`  (lines 64–64)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines the common operation for reading a blob piece by piece. It is used when a file may be too large to comfortably load all at once.

**Data flow**: A key goes in. The store produces an asynchronous sequence of byte chunks. The caller receives the same blob content gradually instead of as one large buffer.

**Call relations**: This method is the streaming counterpart to BlobStore.get. Implementations use it to support downloads, file inspection, and other flows where bounded memory use matters.


##### `BlobStore.put_stream`  (lines 66–66)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Defines the common operation for writing a blob piece by piece. It lets callers store large or incoming data without first collecting the whole file in memory.

**Data flow**: A key and an asynchronous source of byte chunks go in. The store consumes the chunks in order and saves them as one object. Nothing is returned after the write completes.

**Call relations**: This method is the streaming counterpart to BlobStore.put. Disk and S3 implementations use different mechanisms, but callers get the same simple shape.


##### `BlobStore.list`  (lines 68–72)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Defines the common operation for listing stored objects under a required key prefix. The prefix requirement prevents accidental whole-store scans.

**Data flow**: A prefix goes in. The store returns a sorted, capped collection of BlobEntry records, each describing one matching object’s key, size, and modification time.

**Call relations**: Web asset publishing uses this to see what is already stored under an asset prefix. Concrete stores implement the listing for either a directory tree or an S3 prefix.

*Call graph*: called by 1 (_publish_assets).


##### `FilesystemBlobStore.put`  (lines 81–86)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves a complete byte string as a file under the blob root. It uses a temporary file first so readers do not see a partially written file.

**Data flow**: A storage key and bytes go in. The key is turned into a safe path, parent folders are created, bytes are written to a uniquely named temporary file, and that temporary file replaces the final path. Nothing is returned.

**Call relations**: This is the filesystem implementation of BlobStore.put. It relies on _resolve to keep the path inside the configured store root and uses background thread work so file I/O does not block the async event loop.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.get`  (lines 88–93)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a whole blob from the local filesystem. It turns missing files into the project’s BlobNotFound error so callers see the same behavior across backends.

**Data flow**: A key goes in. The key is safely resolved to a path, the file bytes are read, and those bytes are returned. If the file is absent, BlobNotFound is raised.

**Call relations**: This is the filesystem implementation of BlobStore.get. It calls _resolve before touching disk, then performs the blocking file read in a worker thread.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.exists`  (lines 95–97)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a local filesystem blob exists as a regular file. It is a lightweight presence check.

**Data flow**: A key goes in. The key is resolved to a safe path, that path is checked on disk, and a true or false answer comes back.

**Call relations**: This is the filesystem implementation of BlobStore.exists. Like the other filesystem methods, it delegates path safety to _resolve and runs disk work outside the event loop.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.delete`  (lines 99–101)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes a local blob file if it is present. Missing files are ignored so repeated deletes are safe.

**Data flow**: A key goes in. The key is resolved to a safe path, and that file is unlinked from disk if present. Nothing is returned.

**Call relations**: This is the filesystem implementation of BlobStore.delete. It uses _resolve for containment and the operating system delete operation in a worker thread.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.get_stream`  (lines 103–116)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads a local blob in fixed-size chunks. This avoids loading large files into memory all at once.

**Data flow**: A key goes in. The file is opened safely, chunks are read one at a time and yielded to the caller, and the file handle is closed at the end. If the file is missing, BlobNotFound is raised.

**Call relations**: This is the filesystem implementation of BlobStore.get_stream. It depends on _resolve for safe paths and uses thread offloading for open, read, and close operations.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.put_stream`  (lines 118–131)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes a local blob from incoming chunks. It keeps memory use bounded and still makes the final write atomic with a temporary file.

**Data flow**: A key and a stream of chunks go in. The method resolves the final path, creates parent folders, writes chunks into a temporary file, closes it, and replaces the final file. If anything fails, it removes the temporary file before re-raising the error.

**Call relations**: This is the filesystem implementation of BlobStore.put_stream. It uses _resolve for safety and mirrors FilesystemBlobStore.put’s temp-file replacement strategy for streamed data.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.list`  (lines 133–136)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists local blobs below a given prefix. It refuses an empty prefix to avoid accidentally walking the whole storage root.

**Data flow**: A prefix goes in. If the prefix is non-empty, the directory walk is run in a worker thread and returns matching BlobEntry records. If the prefix is empty, a ValueError is raised.

**Call relations**: This is the filesystem implementation of BlobStore.list. It hands the real directory traversal to _walk so the async method can keep blocking filesystem work off the event loop.

*Call graph*: 1 external calls (to_thread).


##### `FilesystemBlobStore._walk`  (lines 138–159)

```
def _walk(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Performs the actual directory scan for FilesystemBlobStore.list. It gathers only files whose keys match the requested prefix and skips temporary write files.

**Data flow**: A prefix goes in. The method finds the safe root and starting directory, walks files below it, converts file paths back into blob keys, records size and modification time, sorts by key, and returns at most the configured limit.

**Call relations**: FilesystemBlobStore.list calls this inside a worker thread. It uses _contained_root and _resolve to keep the scan tied to the configured store, and it creates BlobEntry records for callers.

*Call graph*: calls 2 internal fn (_contained_root, _resolve); 4 external calls (__init__, fromtimestamp, walk, Path).


##### `FilesystemBlobStore._resolve`  (lines 161–166)

```
def _resolve(self, key: str) -> Path
```

**Purpose**: Turns a blob key into a safe filesystem path. Its main job is to stop keys like ../secret from escaping the blob storage directory.

**Data flow**: A string key goes in. The method combines it with the canonical blob root, resolves the result, checks that it is still under the root and not the root itself, then returns the path. Unsafe keys raise ValueError.

**Call relations**: All filesystem read, write, delete, stream, and listing paths go through this method. It calls _contained_root so every operation uses the same root safety rule.

*Call graph*: calls 1 internal fn (_contained_root); called by 7 (_walk, delete, exists, get, get_stream, put, put_stream).


##### `FilesystemBlobStore._contained_root`  (lines 168–181)

```
def _contained_root(self) -> Path
```

**Purpose**: Finds the real filesystem root for blob storage and validates that it is usable. It allows a missing root before the first write, but refuses a root that points to the wrong kind of thing.

**Data flow**: The configured root path is read from the store. The method asks the containment helper to canonicalize and validate it; if the path does not exist yet, it returns the resolved would-be path. The result is a root path used for later containment checks.

**Call relations**: _resolve and _walk call this whenever they need the safe base directory. It delegates the stricter path validation to configured_root and uses the blob.root setting name in related error messages.

*Call graph*: called by 2 (_resolve, _walk); 1 external calls (configured_root).


##### `_is_missing_key`  (lines 184–185)

```
def _is_missing_key(error: ClientError) -> bool
```

**Purpose**: Recognizes S3 error responses that mean “that object is not there.” Different S3 services use slightly different error codes, so this gives the rest of the file one test.

**Data flow**: A ClientError from the S3 library goes in. The function reads the error code inside the response and returns true if it matches known missing-object codes, otherwise false.

**Call relations**: S3BlobStore.get, S3BlobStore.exists, and S3BlobStore.get_stream call this when S3 reports an error. It lets those methods turn missing objects into BlobNotFound or false while re-raising other S3 problems.

*Call graph*: called by 3 (exists, get, get_stream).


##### `S3BlobStore.put`  (lines 207–209)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves a complete byte string as one object in an S3 bucket. It is the cloud-storage version of a simple blob write.

**Data flow**: A key and bytes go in. The method gets the async S3 client, sends a put-object request with the bucket, key, and bytes, and returns after S3 accepts it.

**Call relations**: This is the S3 implementation of BlobStore.put. It relies on _client so client creation and reuse are handled consistently.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get`  (lines 211–221)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a complete object from S3 into memory. Missing objects are reported as BlobNotFound, matching the filesystem backend.

**Data flow**: A key goes in. The method gets the S3 client, requests the object, reads the response body bytes, and returns them. If S3 says the key is missing, BlobNotFound is raised.

**Call relations**: This is the S3 implementation of BlobStore.get. It calls _client for the connection and _is_missing_key to translate S3’s missing-object errors into the project’s common error.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.exists`  (lines 223–231)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether an object exists in S3 without downloading it. It uses S3’s metadata lookup rather than reading the whole object.

**Data flow**: A key goes in. The method sends a head-object request. A successful answer becomes true, a known missing-key error becomes false, and other errors are passed on.

**Call relations**: This is the S3 implementation of BlobStore.exists. It uses _client for S3 access and _is_missing_key to distinguish absence from real failures.

*Call graph*: calls 2 internal fn (_client, _is_missing_key).


##### `S3BlobStore.delete`  (lines 233–235)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes an object from the S3 bucket. S3 delete requests are safe to send even when the object is already absent.

**Data flow**: A key goes in. The method gets the S3 client and sends a delete-object request for that bucket and key. Nothing is returned.

**Call relations**: This is the S3 implementation of BlobStore.delete. It depends on _client for the reusable S3 connection.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get_stream`  (lines 237–248)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads an S3 object in chunks. This is for large blobs where holding the whole object in memory would be wasteful or risky.

**Data flow**: A key goes in. The method opens the S3 object body and yields fixed-size chunks until the body ends. If S3 reports the key as missing, BlobNotFound is raised.

**Call relations**: This is the S3 implementation of BlobStore.get_stream. It uses _client to talk to S3 and _is_missing_key for consistent missing-object behavior.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.put_stream`  (lines 250–294)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed data to S3, using multipart upload for larger content. Multipart upload means S3 receives the object in numbered pieces and assembles them at the end.

**Data flow**: A key and a stream of chunks go in. The method buffers chunks until they reach the multipart part size, uploads parts as needed, then either does a simple put for small data or completes the multipart upload for larger data. If an error happens during multipart upload, it aborts the unfinished upload.

**Call relations**: This is the S3 implementation of BlobStore.put_stream. It calls _client once, then uses S3 multipart calls internally so callers can provide a normal chunk stream.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_put`  (lines 296–320)

```
async def presigned_put(self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a short-lived upload URL for one exact S3 object with a known size and checksum. This lets an untrusted sandbox upload directly to S3 without receiving broader storage credentials.

**Data flow**: A key, expected byte size, checksum, and expiry time go in. The method asks S3 to generate a signed PUT URL that only works for that key and those measured bytes, then returns the URL string.

**Call relations**: WorkspaceBlobStore.presigned_put delegates to this when the backend is S3. It uses _client so the URL is signed with the same S3 configuration used for normal operations.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_put_unmeasured`  (lines 322–332)

```
async def presigned_put_unmeasured(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a short-lived upload URL for one S3 key without locking the body size or checksum. It is for trusted producers that cannot know the final size before rendering content.

**Data flow**: A key and expiry time go in. The method asks S3 for a signed PUT URL limited to that bucket and key, then returns the URL string.

**Call relations**: WorkspaceBlobStore.presigned_put_unmeasured delegates to this for S3-backed workspaces. It uses _client to keep URL signing aligned with the configured endpoint and region.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_get`  (lines 334–341)

```
async def presigned_get(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a short-lived download URL for one S3 object. Anyone holding the URL can read that object until the URL expires.

**Data flow**: A key and expiry time go in. The method asks S3 for a signed GET URL for that bucket and key, then returns the URL string.

**Call relations**: WorkspaceBlobStore.presigned_get delegates to this for S3-backed workspaces. The method relies on _client for the signing configuration.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.put_host`  (lines 343–354)

```
async def put_host(self) -> str
```

**Purpose**: Reports the hostname that presigned upload URLs will contact. The system can use this to allow sandbox network access only to the exact S3 host it needs.

**Data flow**: The store’s S3 client configuration is read. The method parses the client endpoint URL, extracts its hostname, adjusts for AWS virtual-hosted bucket addressing when needed, and returns the bare hostname. If no hostname can be found, it raises RuntimeError.

**Call relations**: This method calls _client so it reads the host from the same client that signs URLs. That keeps network proxy rules and generated URLs from drifting apart.

*Call graph*: calls 1 internal fn (_client); 1 external calls (urlsplit).


##### `S3BlobStore.list`  (lines 356–373)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists S3 objects under a required prefix. It returns a bounded set of object descriptions instead of scanning or returning the whole bucket.

**Data flow**: A non-empty prefix goes in. The method pages through S3 list results, converts each object into a BlobEntry with key, size, and UTC modification time, stops at the configured cap, and returns the entries.

**Call relations**: This is the S3 implementation of BlobStore.list. It uses _client for S3 access and mirrors the filesystem list behavior so higher-level code can treat both backends alike.

*Call graph*: calls 1 internal fn (_client); 1 external calls (__init__).


##### `S3BlobStore.close`  (lines 375–381)

```
async def close(self) -> None
```

**Purpose**: Closes the cached S3 client for the currently running event loop. This releases network resources when that loop is done using blob storage.

**Data flow**: The current async event loop is read. Any client and lock stored for that loop are removed from the store, and the client is closed if one existed. Nothing is returned.

**Call relations**: This is cleanup support for the per-event-loop client cache created by _client. It is used during teardown or loop shutdown rather than during each blob operation.

*Call graph*: 1 external calls (get_running_loop).


##### `S3BlobStore._client`  (lines 383–409)

```
async def _client(self) -> AioBaseClient
```

**Purpose**: Returns the reusable async S3 client for the current event loop, creating it if needed. Reusing the client avoids expensive setup on every blob call and respects that the underlying network client belongs to one event loop.

**Data flow**: The running event loop is read. If a client is already cached for that loop, it is returned. Otherwise a lock prevents duplicate creation, a new aiobotocore S3 client is created with the right addressing and signing settings, cached, and returned.

**Call relations**: Every S3BlobStore operation that talks to S3 calls this first. It is the shared doorway to S3, so normal reads, writes, listing, signed URLs, and host discovery all use the same client setup.

*Call graph*: called by 11 (delete, exists, get, get_stream, list, presigned_get, presigned_put, presigned_put_unmeasured, put, put_host (+1 more)); 3 external calls (get_session, Lock, get_running_loop).


##### `WorkspaceBlobStore.put`  (lines 422–423)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves bytes inside the currently bound workspace. Callers provide a workspace-relative key, and this method adds the workspace prefix automatically.

**Data flow**: A workspace-relative key and bytes go in. The key is expanded by _full to include the current workspace ID, then the backend stores the bytes. Nothing is returned.

**Call relations**: Environment document and file storage call this when saving workspace-owned data. It delegates to the backend only after _full has enforced the workspace boundary.

*Call graph*: calls 1 internal fn (_full); called by 2 (store_environment_document, store_environment_file).


##### `WorkspaceBlobStore.get`  (lines 425–426)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads bytes from the currently bound workspace. It prevents callers from accidentally reaching into another workspace by deriving the prefix from ambient workspace context.

**Data flow**: A workspace-relative key goes in. _full adds the current workspace prefix, the backend reads that full key, and the bytes are returned.

**Call relations**: Environment loading calls this to fetch workspace-owned documents and files. It relies on _full to bind the read to the active workspace before using the backend.

*Call graph*: calls 1 internal fn (_full); called by 2 (load_environment_document, load_environment_file).


##### `WorkspaceBlobStore.exists`  (lines 428–429)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a blob exists inside the current workspace. It is the workspace-safe version of the common exists check.

**Data flow**: A workspace-relative key goes in. _full adds the active workspace prefix, the backend checks that full key, and a true or false result comes back.

**Call relations**: This wrapper method fits the BlobStore shape while adding workspace isolation. It hands the actual storage check to the backend after key expansion.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.delete`  (lines 431–432)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes a blob from the current workspace. It cannot delete outside that workspace because the full key is built from the active workspace context.

**Data flow**: A workspace-relative key goes in. _full turns it into a full workspace-prefixed key, and the backend deletes that object if present. Nothing is returned.

**Call relations**: This wrapper method delegates cleanup to the backend only after _full has fixed the key under the current workspace.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.get_stream`  (lines 434–438)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Starts a chunked read from a blob in the current workspace. It resolves the workspace prefix immediately so the stream remains tied to the workspace where it was opened.

**Data flow**: A workspace-relative key goes in. _full builds the full key right away, and the backend returns a chunk-producing stream for that full key.

**Call relations**: Runtime extension context code calls this when reading member blob text. The immediate _full call is important because the actual chunks may be consumed after the workspace scope has ended.

*Call graph*: calls 1 internal fn (_full); called by 1 (_member_blob_text).


##### `WorkspaceBlobStore.put_stream`  (lines 440–441)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed bytes into the current workspace. It is used for larger content or incoming data where collecting all bytes first is not ideal.

**Data flow**: A workspace-relative key and chunk stream go in. _full adds the workspace prefix, and the backend consumes the chunks and stores them at the full key. Nothing is returned.

**Call relations**: This is the workspace-safe wrapper around the backend’s put_stream method. Its main added value is the _full key expansion before handing off storage.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.list`  (lines 443–448)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists blobs under a prefix inside the current workspace, but returns keys relative to that workspace. This keeps callers from having to know the internal storage prefix.

**Data flow**: A non-empty workspace-relative prefix goes in. The method builds the workspace root prefix, asks the backend to list full keys below it, strips the workspace prefix from each returned key, and returns adjusted BlobEntry records.

**Call relations**: This method combines _full and the backend list method. It preserves workspace isolation while presenting a simpler workspace-local view to callers.

*Call graph*: calls 1 internal fn (_full); 1 external calls (replace).


##### `WorkspaceBlobStore.presigned_put`  (lines 450–461)

```
async def presigned_put(self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a measured presigned upload URL for an object inside the current workspace. It only works when the backend is S3, because local files cannot be uploaded through S3 URLs.

**Data flow**: A workspace-relative key, size, checksum, and expiry go in. The key is expanded with _full, then the request is delegated to S3BlobStore.presigned_put. If the backend is not S3, TypeError is raised.

**Call relations**: Higher-level upload flows use this wrapper after they have chosen an S3-backed blob store. The method keeps the workspace prefixing rule in one place before handing off to S3.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.presigned_put_unmeasured`  (lines 463–469)

```
async def presigned_put_unmeasured(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates an unmeasured presigned upload URL for an object inside the current workspace. It is for S3-backed producers whose final output size is not known before upload.

**Data flow**: A workspace-relative key and expiry go in. _full expands the key, S3BlobStore.presigned_put_unmeasured creates the URL, and that URL is returned. Non-S3 backends raise TypeError.

**Call relations**: This is the workspace-safe wrapper around the S3-only unmeasured upload URL feature. It ensures the uploaded object lands under the active workspace.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.presigned_get`  (lines 471–478)

```
async def presigned_get(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a short-lived download URL for an object inside the current workspace. It is only available for the S3 backend.

**Data flow**: A workspace-relative key and expiry go in. _full adds the current workspace prefix, S3BlobStore.presigned_get creates the URL, and the URL is returned. Non-S3 backends raise TypeError.

**Call relations**: This method wraps the S3 signed-download feature with workspace isolation. It hands off to S3 only after the key has been safely rooted in the active workspace.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore._full`  (lines 480–483)

```
def _full(self, key: str) -> str
```

**Purpose**: Builds the real storage key for the active workspace. It also rejects keys that are already workspace-prefixed, which prevents double-prefix mistakes and cross-workspace confusion.

**Data flow**: A workspace-relative key goes in. The method reads the current workspace ID from workspace context and returns workspaces/<id>/<key>. If the input already starts with the workspace prefix, it raises ValueError.

**Call relations**: Every WorkspaceBlobStore operation calls this before touching the backend. It is the central guardrail that makes workspace blob access depend on the active workspace scope.

*Call graph*: called by 10 (delete, exists, get, get_stream, list, presigned_get, presigned_put, presigned_put_unmeasured, put, put_stream); 1 external calls (ws_current).


##### `FleetBlobStore.put`  (lines 494–495)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves deployment-wide bytes under one of the allowed fleet namespaces. Fleet data is shared by the deployment rather than owned by a workspace.

**Data flow**: A key and bytes go in. _checked confirms the key starts with an approved fleet prefix, then the backend stores the bytes. Nothing is returned.

**Call relations**: This is the fleet wrapper around backend put. It uses _checked to keep deployment-wide storage from becoming an escape hatch into workspace data.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.get`  (lines 497–498)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads deployment-wide bytes from an allowed fleet namespace. It refuses keys outside the fleet prefixes.

**Data flow**: A key goes in. _checked validates the prefix, the backend reads the object, and the bytes are returned.

**Call relations**: This is the fleet wrapper around backend get. It delegates the actual read only after _checked approves the namespace.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.exists`  (lines 500–501)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a deployment-wide blob exists under an allowed fleet prefix. It prevents existence checks against workspace or arbitrary keys.

**Data flow**: A key goes in. _checked validates it, the backend checks for the object, and true or false is returned.

**Call relations**: This is the fleet wrapper around backend exists. It depends on _checked for namespace control before the backend is contacted.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.delete`  (lines 503–504)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes a deployment-wide blob from an allowed fleet namespace. It will not delete keys outside the fleet-owned areas.

**Data flow**: A key goes in. _checked approves or rejects the prefix, and the backend deletes the approved key if present. Nothing is returned.

**Call relations**: This is the fleet wrapper around backend delete. The important handoff is from namespace validation in _checked to the actual storage backend.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.get_stream`  (lines 506–507)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a deployment-wide blob from an allowed fleet namespace. It is useful for large shared files or payloads.

**Data flow**: A key goes in. _checked validates the fleet prefix, and the backend returns an asynchronous stream of byte chunks for that key.

**Call relations**: This is the fleet wrapper around backend get_stream. It adds the fleet namespace guard before allowing streamed access.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.put_stream`  (lines 509–510)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed data into an allowed fleet namespace. It keeps large deployment-wide writes from needing one full in-memory buffer.

**Data flow**: A key and stream of byte chunks go in. _checked validates the key, then the backend consumes the chunks and stores the object. Nothing is returned.

**Call relations**: This is the fleet wrapper around backend put_stream. It relies on _checked to enforce the limited fleet key space before the backend writes.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.list`  (lines 512–513)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists deployment-wide blobs under an allowed fleet prefix. It refuses prefixes outside the fleet namespaces.

**Data flow**: A prefix goes in. _checked validates that it belongs to the fleet key space, then the backend returns matching BlobEntry records.

**Call relations**: This is the fleet wrapper around backend list. It gives callers a controlled way to enumerate shared deployment assets without touching workspace storage.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore._checked`  (lines 515–518)

```
def _checked(self, key: str) -> str
```

**Purpose**: Validates that a fleet key belongs to one of the approved deployment-wide namespaces. This is the main safety check for FleetBlobStore.

**Data flow**: A key goes in. If it starts with an allowed prefix such as static/, term/, or apps/, the same key is returned. Otherwise ValueError is raised.

**Call relations**: Every FleetBlobStore operation calls this before using the backend. It is the simple gate that keeps fleet storage separate from workspace storage.

*Call graph*: called by 7 (delete, exists, get, get_stream, list, put, put_stream).


##### `blob_store_for`  (lines 521–533)

```
def blob_store_for(config: BlobConfig) -> FilesystemBlobStore | S3BlobStore
```

**Purpose**: Builds the concrete blob backend described by configuration. It chooses local filesystem storage or S3 storage and checks that the required settings are present.

**Data flow**: A BlobConfig goes in. If it names the filesystem backend, the configured root is used to create a FilesystemBlobStore. If it names S3, the bucket and optional endpoint or region are used to create an S3BlobStore. Missing required fields raise ValueError.

**Call relations**: Startup or configuration wiring calls this to create the base backend that higher-level code will use directly or wrap in WorkspaceBlobStore and FleetBlobStore.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/harness/durability.py`

`io_transport` · `cross-cutting: used whenever DBOS persists or replays workflow data`

DBOS stores workflow inputs, step results, and errors in a database so work can continue after a crash. The hard part is that the code reading the saved data may be newer than the code that wrote it. A normal Python pickle can restore an object exactly as it was, but that can be too exact: if a Pydantic model has gained a new field, the restored object may be missing it and crash later with a confusing error.

This file wraps pickle with a safer rule for Pydantic BaseModel objects. Instead of saving the raw internal object shape, it saves the model class plus its field values. When loading, it rebuilds the model through Pydantic validation, so current defaults are applied, removed fields are ignored, and truly missing required fields fail in a clearer way.

It also keeps a map of old module names to new module names. That matters because saved pickle data records where classes lived. If a class moved from one Python module to another, this map is like a forwarding address so old database rows can still be read.

The main public entry is replay_safe_client, which creates a DBOS client using this serializer. Without that, the engine could write rows that later clients cannot properly decode.

#### Function details

##### `replay_safe_client`  (lines 176–180)

```
def replay_safe_client(system_database_url: str) -> DBOSClient
```

**Purpose**: Creates the DBOS client in the one safe way this repository expects: with the replay-safe serializer attached. Someone uses this when connecting to the DBOS system database so saved workflow data is written and read in the compatible format.

**Data flow**: It takes a system database URL as input. It builds a ReplaySafeSerializer, gives that serializer and the URL to DBOSClient, and returns the ready-to-use client. The database is not changed by this function by itself; it prepares the client that will later read and write rows.

**Call relations**: This is the front door for code that needs a DBOSClient. It calls the ReplaySafeSerializer constructor so the client knows this file's encoding rules, then hands that serializer to DBOSClient. Later, DBOS calls the serializer's name, serialize, and deserialize methods while recording and replaying workflow data.

*Call graph*: 2 external calls (__init__, DBOSClient).


##### `_rebuild`  (lines 183–184)

```
def _rebuild(model_class: type[BaseModel], fields: dict[str, object]) -> BaseModel
```

**Purpose**: Recreates a Pydantic model from saved field values using the model class's current validation rules. This is what lets old saved objects pick up new default fields instead of coming back as half-formed objects.

**Data flow**: It receives a model class and a dictionary of saved field values. It asks that class to validate the dictionary and construct a fresh model instance. The output is a current, normal Pydantic model object, or a validation error if required data is truly missing or invalid.

**Call relations**: The custom pickler records this function as the way to rebuild Pydantic models. During deserialization, pickle invokes it to turn the saved class-and-fields pair back into a real model. Because old database recordings refer to this function by module and name, it must remain available under this name for old rows to replay.


##### `_ModelPickler.reducer_override`  (lines 188–191)

```
def reducer_override(self, obj: object) -> tuple[Callable[..., object], tuple[object, ...]]
```

**Purpose**: Changes how Pydantic models are saved by pickle. Instead of letting pickle freeze their internal state directly, it tells pickle to save enough information to rebuild them cleanly later.

**Data flow**: It receives any object that pickle is about to serialize. If the object is a Pydantic BaseModel, it returns instructions saying: save the model's class and its current field dictionary, then rebuild it later with _rebuild. If the object is not a Pydantic model, it returns NotImplemented so normal pickle behavior continues.

**Call relations**: ReplaySafeSerializer.serialize uses _ModelPickler to write data. Whenever that pickler encounters a Pydantic model, this method supplies the safer recipe. For all other objects, serialization flows back to the standard pickle machinery.


##### `_CompatUnpickler.find_class`  (lines 195–196)

```
def find_class(self, module: str, name: str) -> object
```

**Purpose**: Finds classes while loading pickled data, with support for modules that have been renamed or moved. This keeps old saved rows readable after code has been reorganized.

**Data flow**: It receives the module name and class or function name recorded in the saved pickle data. It first checks whether the recorded module has a newer replacement in MOVED_MODULES. It then asks the normal unpickler to load the named object from the updated module path, returning the class, function, or other object pickle needs.

**Call relations**: ReplaySafeSerializer.deserialize uses _CompatUnpickler when reading saved data. During that read, pickle calls find_class whenever it needs to resolve a recorded class or function name. This method quietly redirects old module paths before handing the lookup back to Python's standard unpickling logic.


##### `ReplaySafeSerializer.name`  (lines 202–203)

```
def name(self) -> str
```

**Purpose**: Returns the stable name DBOS stores alongside data written with this serializer. That name tells future readers which serializer format was used.

**Data flow**: It takes no outside data beyond the serializer instance. It returns the constant string used as this serializer's identifier. It does not change anything.

**Call relations**: A DBOS client created by replay_safe_client uses this method when DBOS needs to label newly persisted rows. Later, that label helps DBOS choose the correct deserialization path for recorded workflow data.


##### `ReplaySafeSerializer.serialize`  (lines 205–208)

```
def serialize(self, data: object) -> str
```

**Purpose**: Turns a Python object into a database-safe text string using the custom pickle rules in this file. It is used when DBOS needs to persist workflow inputs, step outputs, or errors.

**Data flow**: It receives any Python object. It creates an in-memory byte buffer, uses _ModelPickler to pickle the object into that buffer, then base64-encodes the bytes into UTF-8 text. The output is a string suitable for storage in the DBOS database.

**Call relations**: DBOS calls this method through the serializer attached to its client. Inside, it hands the object to _ModelPickler, which applies the special Pydantic model behavior when needed. The final base64 text is what DBOS can store as a row value.

*Call graph*: 3 external calls (__init__, b64encode, BytesIO).


##### `ReplaySafeSerializer.deserialize`  (lines 210–211)

```
def deserialize(self, serialized_data: str) -> object
```

**Purpose**: Turns a stored text string back into the original Python object, while accepting old module paths from earlier builds. It is used during replay and recovery when DBOS reads persisted workflow data.

**Data flow**: It receives a base64 text string from storage. It decodes that string back into pickle bytes, wraps those bytes in an in-memory buffer, and uses _CompatUnpickler to load the object. The output is the reconstructed Python object, with Pydantic models rebuilt through the safer path when they were saved that way.

**Call relations**: DBOS calls this method through the serializer attached to its client when reading stored rows. It delegates class lookup to _CompatUnpickler, which applies the module-move map. If the saved data contains Pydantic models serialized by this file, the unpickling process reaches _rebuild to recreate them under the current model definitions.

*Call graph*: 3 external calls (__init__, b64decode, BytesIO).


### `core/src/ufo/runtime/transcript.py`

`io_transport` · `turn completion and repair/retry transcript persistence`

A conversation transcript is the durable record of what has happened in a conversation so far. This file wraps the raw blob store, which is a shared storage place for bytes, and turns it into a safer transcript store for one conversation. Without this guard, two parts of the system could write transcript snapshots out of order, and the final saved history could lose messages or replace the real turn result with a weaker fallback record.

The main class, `Transcript`, knows two things: where the blobs live, and which conversation it belongs to. To read, it builds the storage key for that conversation, fetches the saved bytes, and decodes them back into a `Conversation` object. If nothing has been saved yet, it returns nothing instead of treating that as an error.

To write, it first reads the current saved transcript. It then asks `_supersedes` whether the new transcript is allowed to replace the old one. The rule is like keeping a notebook where later page numbers win, but if two entries claim the same page number, only the fuller and more authoritative entry may replace the weaker one. This matters during retry or repair flows, where a fallback transcript may arrive before the real turn writer finishes. The file makes sure the durable transcript moves forward and does not shrink.

#### Function details

##### `Transcript.read`  (lines 18–23)

```
async def read(self) -> Conversation | None
```

**Purpose**: This reads the saved transcript for this conversation from the blob store. If the transcript has not been written yet, it returns `None` so callers can treat that as an empty starting point.

**Data flow**: It starts with the `Transcript` object’s conversation id and blob store. It turns the conversation id into the correct blob key, asks the blob store for the stored bytes, and decodes those bytes into a `Conversation`. If the blob store says the blob does not exist, the result is `None` instead of a decoded conversation.

**Call relations**: This is the first step used by `Transcript.write` before saving anything. It also relies on the shared transcript helpers to build the storage key and decode the stored bytes, so this file does not need to know the byte format itself.

*Call graph*: called by 1 (write); 2 external calls (decode, transcript_key).


##### `Transcript.write`  (lines 25–29)

```
async def write(self, conversation: Conversation) -> None
```

**Purpose**: This saves a conversation transcript, but only if it is newer or more trustworthy than what is already stored. It protects the durable history from going backwards or losing detail.

**Data flow**: It receives a `Conversation` object that someone wants to persist. First it reads the currently stored transcript. If there is already a stored transcript and `_supersedes` says the incoming one should not replace it, the function stops without changing storage. Otherwise, it encodes the incoming conversation into bytes, builds the blob key for this conversation, and writes those bytes to the blob store.

**Call relations**: This is the public save path for this file. It calls `Transcript.read` to see what is already durable, asks `_supersedes` to make the safety decision, then hands the accepted conversation to the shared encoder and blob store for the actual write.

*Call graph*: calls 2 internal fn (read, _supersedes); 2 external calls (encode, transcript_key).


##### `_supersedes`  (lines 32–48)

```
def _supersedes(incoming: Conversation, stored: Conversation) -> bool
```

**Purpose**: This decides whether one transcript is allowed to replace another. Its job is to keep the saved transcript moving forward in turn order and to stop a shorter same-turn record from overwriting a fuller one.

**Data flow**: It compares an incoming `Conversation` with the already stored `Conversation`. If their sequence numbers differ, the one with the larger sequence number wins. If the sequence numbers are the same, the incoming one may replace the stored one only when it came from the real run, the stored one did not, and the incoming transcript has at least as many messages as the stored transcript. The output is a simple yes-or-no answer.

**Call relations**: `Transcript.write` calls this right before deciding whether to write to the blob store. This helper does not perform any I/O itself; it only supplies the rule that prevents repair fallback records and shorter same-sequence snapshots from displacing the better transcript.

*Call graph*: called by 1 (write).


### Shared database schemas
Defines common record contracts and the unified database table layout used across runtime, workers, user surfaces, and deployments.

### `core/src/ufo/schema/records.py`

`data_model` · `cross-cutting`

This file is like the project’s set of official forms. When a user sends a message, an agent runs, a child agent is spawned, a question is asked, or a turn finishes, the system needs one agreed shape for that information. These Pydantic models provide that shape and also check that the data makes sense before it moves across boundaries.

The central idea is a “turn”: one unit of agent work. A Turn records who spoke, where it belongs, whether it is queued, running, parked, or finished, and what final result it produced. A TerminalFrame records the ending: success, failure, cancellation, token cost, model used, and any final request such as asking the user a question or collecting credentials.

The file also defines small but important rules around routing and identity. It decides which queue a turn should enter, creates repeatable UUIDs for turns, billing records, and mid-turn replies, and chooses default agent icons. Repeatable IDs matter because workers may replay work; the same logical event must get the same ID instead of creating duplicates.

Several validators act as safety rails. They reject inconsistent runtime identity, invalid pinned runtime settings, unsafe context text, unknown time zones, and mismatches between a turn’s status and its terminal frame. Without this file, different parts of the system could disagree about what a turn means, duplicate work, show unsafe text, or store impossible states.

#### Function details

##### `auto_agent_icon`  (lines 178–197)

```
def auto_agent_icon(name: str, taken: Collection[str]) -> TablerIcon
```

**Purpose**: Chooses an icon for a newly created agent. It tries to pick something meaningful from the agent’s name, and otherwise spreads icons across a workspace so agents are easier to tell apart.

**Data flow**: It receives an agent name and the icons already taken in the workspace. It lowercases and splits the name into simple words, checks whether any word maps to a known icon, and uses that icon if it is still free. If not, it hashes the name with SHA-256, which turns the name into a stable number, and uses that number to pick from unused icons; if all icons are used, it repeats a stable choice. The output is one valid icon name.

**Call relations**: This is used when the system needs a starting icon for an agent row. It calls the standard SHA-256 hash function so the same name tends to get the same icon, while still avoiding icons already used when possible.

*Call graph*: 1 external calls (sha256).


##### `admits_spent_balance`  (lines 222–236)

```
def admits_spent_balance(intent: ToolIntent) -> bool
```

**Purpose**: Checks whether a prepared tool action should still be allowed when a workspace has run out of balance. The only allowed action in that state is the billing-management action, because blocking it would also block the way to refill the account.

**Data flow**: It receives a ToolIntent, which is a prebuilt tool call that will run exactly as submitted. It checks that the tool is an object action and that its kind and action are exactly the workspace billing pair. It returns true only for that one billing-management intent; otherwise it returns false.

**Call relations**: Billing or admission gates can call this before rejecting work for lack of funds. It does not hand off to other project code; it simply answers whether this intent is the special refill path.


##### `turn_queue_for`  (lines 250–258)

```
def turn_queue_for(parent_turn_id: UUID | None, admission_source: 'TurnAdmissionSource') -> str
```

**Purpose**: Decides which work queue a turn should enter. Normal root turns go to the regular queue, while child turns and prepared intents go to a faster express queue so they do not deadlock or wait behind ordinary model work.

**Data flow**: It receives an optional parent turn ID and the source that admitted the turn. If there is a parent turn, or if the turn came from an intent, it returns the express queue name. Otherwise it returns the regular turns queue name.

**Call relations**: Turn admission code uses this when placing new work onto a queue. It sits at the boundary between surfaces that create turns and workers that later pick them up.


##### `turn_id_for`  (lines 267–269)

```
def turn_id_for(workspace_id: UUID, conversation_id: UUID, seq: int) -> UUID
```

**Purpose**: Creates the stable ID for a turn. The same workspace, conversation, and sequence number always produce the same UUID, which helps replayed work avoid creating duplicate turns.

**Data flow**: It receives a workspace ID, conversation ID, and turn sequence number. It formats those values into one namespaced string and passes it to UUID version 5, which creates a deterministic UUID from text. The result is the turn’s UUID.

**Call relations**: Code that admits or reconstructs turns calls this before storing or referring to a turn. It relies on the standard UUID v5 function to make repeatable IDs.

*Call graph*: 1 external calls (uuid5).


##### `ledger_id_for`  (lines 272–277)

```
def ledger_id_for(workspace_id: UUID, turn_id: UUID, dimension: str, attempt: str='') -> UUID
```

**Purpose**: Creates the stable ID for one billing ledger entry. It makes billing writes safe to replay by giving the same turn, billing dimension, and run attempt the same record ID.

**Data flow**: It receives the workspace ID, turn ID, billing dimension, and optionally the run attempt ID. It combines them into a namespaced string and turns that string into a deterministic UUID. The output is the ledger row ID to use for that billing write.

**Call relations**: Billing code can call this before recording token or cost usage. It uses UUID v5 so a repeated workflow attempt points back to the same billing entry, while a resumed attempt can intentionally create a separate entry.

*Call graph*: 1 external calls (uuid5).


##### `mid_turn_reply_id_for`  (lines 280–291)

```
def mid_turn_reply_id_for(turn_id: UUID, round_index: int, span_index: int, attempt: str='') -> UUID
```

**Purpose**: Creates the stable ID for a reply sent before a turn fully ends. This prevents a replayed worker from delivering the same mid-turn message twice.

**Data flow**: It receives the turn ID, round number, span number, and optionally the run attempt ID. It builds a string from those pieces and converts it into a deterministic UUID. The output identifies exactly one mid-turn reply.

**Call relations**: The turn-running logic can call this when it emits partial replies. It uses UUID v5 so repeated playback of the same attempt reuses the same delivery record, while a new resumed attempt gets fresh reply IDs.

*Call graph*: 1 external calls (uuid5).


##### `RuntimeIdentity._artifact_pair`  (lines 447–450)

```
def _artifact_pair(self) -> 'RuntimeIdentity'
```

**Purpose**: Checks that a runtime revision and container image digest appear together. This prevents records that name one half of a deployed runtime but not the other.

**Data flow**: It reads the RuntimeIdentity being built. If exactly one of revision or image digest is missing, it raises a validation error. If both are present or both are absent, it returns the same RuntimeIdentity object as valid.

**Call relations**: Pydantic calls this automatically after creating a RuntimeIdentity. It protects any code that later trusts runtime identity records to describe a coherent deployed artifact.


##### `TurnRuntimeConfig._pinned_values`  (lines 469–477)

```
def _pinned_values(self) -> 'TurnRuntimeConfig'
```

**Purpose**: Checks that per-turn runtime overrides are concrete and valid. A turn may pin a specific model or environment document, but it may not pin vague or malformed values.

**Data flow**: It reads the TurnRuntimeConfig being built. It rejects the model value "auto" because a pinned model must be a real model ID. It also checks that the environment value, if present, looks like a SHA-256 digest for a stored environment document. If everything is valid, it returns the config object.

**Call relations**: Pydantic calls this during TurnRuntimeConfig validation. It protects the runtime setup path from receiving ambiguous model choices or invalid environment references.


##### `TurnContext._tag_safe_line`  (lines 534–538)

```
def _tag_safe_line(cls, value: str | None) -> str | None
```

**Purpose**: Cleans user- or surface-provided context text so it can be placed safely into a prompt-like context block. It prevents fields such as sender or source from smuggling angle-bracket tag structure.

**Data flow**: It receives a text value or null. Null stays null. Text has angle brackets removed, whitespace collapsed into single spaces, and empty results converted to null. The output is a safe one-line string or null.

**Call relations**: Pydantic calls this for TurnContext sender, question, and source fields. It prepares context before the engine renders it into the turn’s context section.


##### `TurnContext._known_zone`  (lines 542–549)

```
def _known_zone(cls, value: str | None) -> str | None
```

**Purpose**: Verifies that a supplied timezone name is real. This catches bad timezone data at the surface boundary instead of failing later during a turn.

**Data flow**: It receives a timezone string or null. Null passes through unchanged. For a string, it asks the standard timezone database to load that zone; if the zone is unknown, it raises a validation error. Otherwise it returns the original timezone name.

**Call relations**: Pydantic calls this when building TurnContext. It uses Python’s ZoneInfo library as the authority for valid IANA timezone names, such as "America/New_York".

*Call graph*: 1 external calls (ZoneInfo).


##### `Turn.spawned`  (lines 584–587)

```
def spawned(self) -> bool
```

**Purpose**: Answers whether this turn was created by another turn. In plain terms, it tells whether the turn is a child task rather than a root user request.

**Data flow**: It reads the Turn’s parent_turn_id field. If that field is present, it returns true; if not, it returns false. It does not change the turn.

**Call relations**: Other code can read this property when it needs to treat child turns differently, such as routing, display, or result delivery. It is a simple view over the stored parent linkage.


##### `Turn.authority`  (lines 590–592)

```
def authority(self) -> ExecutionAuthority
```

**Purpose**: Computes whose authority the turn runs under. That means deciding whether actions should be treated as coming from a member or from workspace-level authority.

**Data flow**: It reads speaker_member_id and on_behalf_of_member_id from the Turn. It passes those IDs to turn_authority, which builds an ExecutionAuthority object. The returned object is what later permission checks can use.

**Call relations**: Validators and execution code can ask a Turn for its authority before running tools or checking permissions. This property delegates the actual authority rules to ufo.runtime.authority.turn_authority.

*Call graph*: 1 external calls (turn_authority).


##### `Turn._nothing_created`  (lines 596–599)

```
def _nothing_created(cls, value: object) -> object
```

**Purpose**: Turns a missing created-objects value into an empty list-like tuple. This lets database null mean “nothing was created” instead of forcing the rest of the code to handle a special null case.

**Data flow**: It receives the raw value for created_refs before normal validation. If the value is null, it returns an empty tuple. Otherwise it returns the value unchanged for normal parsing.

**Call relations**: Pydantic calls this when building a Turn. It smooths over database storage behavior so later code can always treat created_refs as a tuple of object references.


##### `Turn._aware_utc`  (lines 603–608)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Ensures turn timestamps carry UTC timezone information. This prevents a timestamp read from a database driver as “timezone-less” from being mistaken for local time.

**Data flow**: It receives a datetime value or null for created_at or updated_at. Null stays null. A datetime that already has timezone information is returned unchanged. A datetime with no timezone marker is copied with UTC attached. The output is a timezone-aware datetime or null.

**Call relations**: Pydantic calls this for Turn timestamp fields. It uses datetime.replace to attach UTC when a database, such as SQLite, has dropped the timezone marker.

*Call graph*: 1 external calls (replace).


##### `Turn._terminal_matches_status`  (lines 611–617)

```
def _terminal_matches_status(self) -> 'Turn'
```

**Purpose**: Checks that a Turn’s status and final result agree with each other. A running turn must not have a terminal frame, and a finished, failed, or cancelled turn must have one with the same status.

**Data flow**: It reads the completed Turn object after fields have been parsed. It first touches the authority property, which also verifies that the authority can be computed. Then it compares the turn status with the presence and status of the terminal frame. If the combination is impossible, it raises a validation error; otherwise it returns the Turn.

**Call relations**: Pydantic calls this after building a Turn. It protects all later queue, display, and execution code from seeing contradictory states, such as a turn marked running but already carrying a final failure frame.


### `core/src/ufo/schema/tables.py`

`data_model` · `database setup and cross-cutting data access`

This file is the blueprint for the system's database. Think of it like the floor plan for a large office: it says what rooms exist, what belongs in each room, and which doors connect them. Here, the “rooms” are database tables such as workspaces, members, agents, conversations, turns, billing ledger entries, credentials, sources, pages, and delivery queues.

It uses SQLAlchemy, a Python library that lets code describe database tables without writing separate SQL by hand for each database engine. The shared `metadata` object collects all table definitions. Other parts of the system can use that metadata to create tables, run migrations, or build database queries safely.

The file also encodes important rules directly into the database. For example, a workspace member must have a unique email within that workspace, a turn must have one of a small set of statuses, an archived main agent is not allowed, and money or token amounts cannot be negative where that would not make sense. These rules matter because they protect the system even if a bug elsewhere tries to save impossible data.

Most of the file is declarative: it names tables and their columns rather than running logic. The one small helper chooses the default audience for a conversation based on whether a member is attached.

#### Function details

##### `_conversation_audience`  (lines 12–13)

```
def _conversation_audience(context: DefaultExecutionContext) -> str
```

**Purpose**: This function supplies the default audience value for a new conversation row. It decides whether the conversation should be shared or tied to a particular member, based on the `member_id` being inserted.

**Data flow**: It receives SQLAlchemy's execution context, which contains the values currently being written to the database. It reads the pending `member_id`, passes that value to `conversation_audience`, converts the result to text, and returns that text as the value to store in the conversation's `audience` column.

**Call relations**: This function is not called directly by normal application code. SQLAlchemy calls it automatically when inserting a conversation row that does not already provide an `audience`. It hands the actual audience decision to `ufo.runtime.turns.audience.conversation_audience`, while this helper adapts that decision to the database default mechanism.

*Call graph*: 2 external calls (get_current_parameters, conversation_audience).
