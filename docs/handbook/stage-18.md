# Cross-cutting persistence, shared schema, and durable records  `stage-18` (cross-cutting infrastructure)

This stage is the system’s shared filing cabinet. It is behind-the-scenes support used during startup, normal requests, background jobs, recovery after crashes, and reporting. At startup, the database doorway in db.py opens connection pools, runs migrations, and enforces workspace boundaries so one customer’s rows are not mixed with another’s. The table definitions in schema/tables.py describe the cabinet’s drawers and rules, while schema/records.py defines common record shapes for turns, agents, questions, credentials, final results, and workflow IDs so all parts of the system speak the same language.

Several files handle durable content. turns/transcript.py defines the saved format for conversations and compacted summaries. loop/transcript.py safely reads and writes those transcripts in workspace storage, making sure old results do not overwrite newer ones. blob.py stores raw files, such as images or attachments, either locally or in S3, while keeping workspace files separate from deployment-wide files. media/previews.py records where image previews live and their sizes. durability.py makes saved workflow objects safe to reload after crashes or code changes. Finally, the skill creation store keeps user-made workspace skills saved, listed, loaded, and deleted without name clashes.

## Files in this stage

### Conversation blob formats
Shared transcript storage conventions and blob access establish durable, workspace-safe conversation data.

### `core/src/ufo/turns/transcript.py`

`io_transport` · `cross-cutting transcript persistence and readback`

This file is the contract for durable conversation history: the saved record that survives beyond one running process. Different parts of the project need the same transcript data, but they cannot all import each other. This file is the neutral meeting place, like a standardized filing cabinet label and form template.

It defines what a saved conversation looks like: a sequence number, the messages in the current window, and optional extra context such as the system prompt and injected text that the model actually saw. It also defines records for “compaction,” which is when an older, bulky part of a conversation is summarized so the model can keep going without carrying every old message. For compaction, the file stores the messages before the swap, the messages after the swap, and a structured summary with verification details.

The file also provides the storage keys used in the blob store, which is the project’s generic place for saving bytes. Records are JSON, then compressed with LZ4, a fast compression format, so they take less space but remain deterministic to decode. If stored bytes are corrupt or no longer match the expected shape, the code raises a clear TranscriptDecodeError instead of letting readers quietly misunderstand the data.

#### Function details

##### `transcript_key`  (lines 37–38)

```
def transcript_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the exact blob-store path for the main saved transcript of one conversation. Callers use it so everyone writes and reads the transcript from the same place.

**Data flow**: It receives a conversation UUID, which is a unique identifier. It inserts that ID into a fixed path string and returns a storage key ending in messages.json.lz4.

**Call relations**: This is the naming rule shared by transcript writers and readers. By centralizing the path here, the write side and read side do not invent slightly different locations for the same conversation.


##### `encode`  (lines 41–43)

```
def encode(conversation: Conversation) -> bytes
```

**Purpose**: Turns a Conversation object into compressed bytes suitable for storage. It is used when a transcript needs to be saved durably.

**Data flow**: It receives a validated Conversation. It converts the conversation into ordinary JSON data, serializes that JSON compactly, turns it into bytes, compresses those bytes with LZ4, and returns the compressed result.

**Call relations**: This function sits on the write path for transcripts. It relies on Conversation.model_dump to get the record into plain data and json.dumps to produce JSON before compression.

*Call graph*: 2 external calls (model_dump, dumps).


##### `decode`  (lines 46–50)

```
def decode(body: bytes) -> Conversation
```

**Purpose**: Turns stored transcript bytes back into a validated Conversation. It protects readers from corrupt data by raising a transcript-specific error when decoding fails.

**Data flow**: It receives compressed bytes from storage. It decompresses them, asks the Conversation schema to validate the JSON, and returns a Conversation object. If decompression or validation fails, it turns that failure into TranscriptDecodeError.

**Call relations**: This is the read-side partner to encode. Debug tools, evaluation code, or other readers can use it to recover the saved transcript without knowing the compression and validation details.

*Call graph*: 1 external calls (__init__).


##### `compaction_key`  (lines 136–137)

```
def compaction_key(conversation_id: UUID, index: int, half: CompactionHalf) -> str
```

**Purpose**: Builds the exact blob-store path for one part of one compaction record. The part can be the before window, the after window, or the summary.

**Data flow**: It receives a conversation UUID, a compaction index, and a half name such as before, after, or summary. It combines them into the standard storage path and returns that key.

**Call relations**: read_compaction_after and read_compaction_record call this when fetching compaction data. This keeps all compaction readers using the same folder-like layout in the blob store.

*Call graph*: called by 2 (read_compaction_after, read_compaction_record).


##### `decode_compaction`  (lines 140–149)

```
def decode_compaction(index: int, before: bytes, after: bytes, summary: bytes) -> CompactionRecord
```

**Purpose**: Rebuilds a complete CompactionRecord from the three stored compressed blobs. It is used when a reader needs the full story of a compaction: what was replaced, what replaced it, and the summary used.

**Data flow**: It receives the compaction index plus compressed bytes for the before window, after window, and summary. It decompresses and validates each piece, then returns a CompactionRecord containing typed messages and the typed summary. If any piece is unreadable or invalid, it raises TranscriptDecodeError.

**Call relations**: read_compaction_record calls this after it has fetched the three blobs. The function gathers those separate storage pieces into one easier-to-use record.

*Call graph*: called by 1 (read_compaction_record); 2 external calls (__init__, __init__).


##### `read_compaction_after`  (lines 152–165)

```
async def read_compaction_after(blob: BlobStore, conversation_id: UUID, index: int) -> tuple[Message, ...] | None
```

**Purpose**: Reads only the after window for one compaction. This is a cheaper read when a caller only needs to know what message window was installed after summarizing.

**Data flow**: It receives a BlobStore, a conversation UUID, and a compaction index. It builds the after key, asks the blob store for those bytes, decompresses and validates them, and returns the messages. If the blob is missing, it returns None; if the blob exists but cannot be decoded, it raises TranscriptDecodeError.

**Call relations**: It calls compaction_key to find the right blob and BlobStore.get to fetch it. It avoids decode_compaction because it deliberately reads only the small after part rather than the full before, after, and summary set.

*Call graph*: calls 2 internal fn (get, compaction_key); 1 external calls (__init__).


##### `read_compaction_record`  (lines 168–179)

```
async def read_compaction_record(blob: BlobStore, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Reads one full compaction record from durable storage. It returns None when that compaction index has no stored record.

**Data flow**: It receives a BlobStore, conversation UUID, and index. It builds and fetches the before, after, and summary blobs. If any expected blob is missing, it returns None. If all are present, it passes the bytes to decode_compaction and returns the resulting CompactionRecord.

**Call relations**: It calls compaction_key for each stored piece, BlobStore.get to retrieve bytes, and decode_compaction to turn those bytes into typed data. read_compaction_records uses it repeatedly while walking through all compactions.

*Call graph*: calls 3 internal fn (get, compaction_key, decode_compaction); called by 1 (read_compaction_records).


##### `read_compaction_records`  (lines 182–192)

```
async def read_compaction_records(blob: BlobStore, conversation_id: UUID) -> tuple[CompactionRecord, ...]
```

**Purpose**: Reads every stored compaction for a conversation in order. It is useful for debug views, evaluation tools, or any reader that wants the full compaction history.

**Data flow**: It receives a BlobStore and conversation UUID. Starting at index 1, it asks read_compaction_record for each record. It collects records until the first missing index, then returns them as an immutable tuple.

**Call relations**: This function is the simple loop over read_compaction_record. It relies on the convention that compaction indices are written sequentially, so the first missing record means there are no later records to read.

*Call graph*: calls 1 internal fn (read_compaction_record).


### `core/src/ufo/blob.py`

`io_transport` · `cross-cutting during request handling, background jobs, uploads, downloads, and startup-created storage setup`

This file is the project’s “storage cupboard” for blobs: chunks of bytes such as uploaded files, shared artifacts, static web assets, transcript compaction records, and terminal payloads. The rest of the system can ask for a key like a path, read or write bytes, stream large data in pieces, or list files under a prefix without caring whether the real storage is a local folder or an S3 bucket.

There are three layers. First, BlobStore describes the shared promise: put, get, check, delete, stream, and list. Second, FilesystemBlobStore and S3BlobStore fulfill that promise using different backends. The filesystem version turns keys into files under a configured root and writes through temporary files so readers never see a half-written object. The S3 version talks to object storage, supports large uploads with multipart upload, and can mint short-lived signed URLs so another process can upload or download one object directly.

Third, WorkspaceBlobStore and FleetBlobStore wrap a backend with safety rails. WorkspaceBlobStore automatically adds the current workspace’s prefix, like labeling every box with the right tenant name before putting it on a shelf. FleetBlobStore only allows known deploy-wide prefixes such as static assets. Without these wrappers, one part of the system could accidentally read or write another workspace’s data or store fleet data in the wrong namespace.

#### Function details

##### `BlobStore.put`  (lines 54–54)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Defines the common promise for storing a complete byte string under a key. Code can call this without knowing whether the storage is local disk or S3.

**Data flow**: A key and a block of bytes go in. The chosen blob store writes those bytes at that key. Nothing is returned, but later reads can fetch the same bytes.

**Call relations**: Higher-level code such as web asset publishing calls this protocol method. At runtime, the call is carried out by a concrete store such as FilesystemBlobStore, S3BlobStore, WorkspaceBlobStore, or FleetBlobStore.

*Call graph*: called by 1 (_publish_assets).


##### `BlobStore.get`  (lines 56–56)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Defines the common promise for reading a whole stored object into memory. It is for objects small enough that loading all bytes at once is acceptable.

**Data flow**: A key goes in. The backing store looks up the object and returns its bytes, or raises BlobNotFound if the key is absent.

**Call relations**: Transcript readers, Slack identity code, and web asset serving use this shared interface. The actual read is performed by whichever backend is configured.

*Call graph*: called by 4 (read_compaction_after, read_compaction_record, read_identity, _stored_asset).


##### `BlobStore.exists`  (lines 58–58)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Defines the common promise for checking whether a stored object is present. Callers use it to avoid unnecessary uploads or to decide whether cached data can be reused.

**Data flow**: A key goes in. The store checks for an object at that key and returns true or false without returning the object contents.

**Call relations**: Slack identity and web asset code call this before deciding what to read or publish. The configured backend supplies the real check.

*Call graph*: called by 3 (read_identity, _publish_assets, _stored_asset).


##### `BlobStore.delete`  (lines 60–62)

```
async def delete(self, key: str) -> None
```

**Purpose**: Defines the common promise for removing an object. Deleting a missing key is intentionally harmless, which makes retrying cleanup safe.

**Data flow**: A key goes in. The backend removes any stored object at that key if one exists. Nothing is returned.

**Call relations**: Concrete stores implement this behavior for disk, S3, workspace-scoped storage, and fleet-scoped storage so callers can clean up without backend-specific code.


##### `BlobStore.get_stream`  (lines 64–64)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines the common promise for reading a large object in smaller pieces. This avoids holding a huge file in memory all at once.

**Data flow**: A key goes in. The store opens the object and yields byte chunks one after another until the object is fully read.

**Call relations**: Concrete implementations provide chunked reads for local files and S3 objects. Higher-level code can pass the stream onward to response bodies or parsers.


##### `BlobStore.put_stream`  (lines 66–66)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Defines the common promise for writing a large object from a stream of chunks. It is used when the full content may be too large to buffer in memory.

**Data flow**: A key and an asynchronous stream of byte chunks go in. The backend consumes each chunk and writes one final object at the key. Nothing is returned.

**Call relations**: FilesystemBlobStore writes chunks into a temporary file, while S3BlobStore may use multipart upload. Wrappers add namespace checks before handing the stream to the backend.


##### `BlobStore.list`  (lines 68–72)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Defines the common promise for listing stored objects under a required key prefix. The required prefix prevents accidental whole-store scans.

**Data flow**: A prefix goes in. The backend finds matching objects, packages each as a BlobEntry with key, size, and modified time, sorts or returns them in bounded form, and returns a tuple.

**Call relations**: Web asset publishing uses this interface to see what is already stored. Concrete backends implement the listing for disk traversal or S3 pagination.

*Call graph*: called by 1 (_publish_assets).


##### `FilesystemBlobStore.put`  (lines 81–86)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Stores a complete byte string as a file under the configured blob root. It writes through a temporary file first so a crash or interruption does not leave a half-written final file.

**Data flow**: A key and bytes go in. The key is resolved to a safe path inside the blob root, parent folders are created, bytes are written to a unique temporary file, and that file is atomically renamed into place. Nothing is returned.

**Call relations**: This is the filesystem implementation of BlobStore.put. It relies on _resolve to keep keys inside the store and uses background threads for blocking file operations.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.get`  (lines 88–93)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a whole local blob file into memory. If the file is not there, it reports the storage-level BlobNotFound error instead of leaking a raw filesystem error.

**Data flow**: A key goes in. The key is converted to a safe file path, the file’s bytes are read, and those bytes come out. If the file is missing, BlobNotFound comes out as an exception.

**Call relations**: This is the filesystem implementation of BlobStore.get. It calls _resolve first, then performs the disk read in a worker thread so the async event loop is not blocked.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.exists`  (lines 95–97)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a local blob exists as a regular file. It gives callers a simple true-or-false answer.

**Data flow**: A key goes in. The key is safely resolved under the root, the filesystem is checked, and a boolean comes out.

**Call relations**: This is the filesystem implementation of BlobStore.exists. It depends on _resolve for containment and delegates the blocking file check to a worker thread.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.delete`  (lines 99–101)

```
async def delete(self, key: str) -> None
```

**Purpose**: Removes a local blob file if it exists. Missing files are ignored so cleanup can be safely repeated.

**Data flow**: A key goes in. The key is resolved to a safe path, and the file is unlinked with a missing-file option. Nothing is returned.

**Call relations**: This is the filesystem implementation of BlobStore.delete. It uses _resolve before touching disk and runs the delete operation outside the event loop thread.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.get_stream`  (lines 103–116)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads a local blob file in fixed-size chunks. This is for downloads or large objects where reading the whole file at once would waste memory.

**Data flow**: A key goes in. The file is opened safely, chunks are read one by one, each chunk is yielded to the caller, and the file handle is closed at the end. If opening fails because the file is missing, BlobNotFound is raised.

**Call relations**: This is the filesystem implementation of BlobStore.get_stream. It uses _resolve for safety and runs open, read, and close operations through worker threads.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.put_stream`  (lines 118–131)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed chunks to a local blob file. Like put, it writes to a temporary file first so the final key only appears after the complete stream succeeds.

**Data flow**: A key and an async stream of chunks go in. The destination path is resolved, directories are created, chunks are written to a temporary file, and the temporary file replaces the final path. If anything fails, the temporary file is cleaned up and the error is re-raised.

**Call relations**: This is the filesystem implementation of BlobStore.put_stream. It calls _resolve, uses a generated temporary name, and shields the async event loop from blocking disk writes.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.list`  (lines 133–136)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists local blob files under a non-empty prefix. Requiring a prefix keeps callers from accidentally walking the entire store.

**Data flow**: A prefix goes in. If it is empty, an error is raised. Otherwise, _walk scans the matching area on disk and returns BlobEntry records.

**Call relations**: This is the filesystem implementation of BlobStore.list. It hands the blocking directory walk to a worker thread, where _walk does the real scanning.

*Call graph*: 1 external calls (to_thread).


##### `FilesystemBlobStore._walk`  (lines 138–159)

```
def _walk(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Does the actual filesystem scan for list. It turns matching files into BlobEntry records and skips temporary files.

**Data flow**: A prefix goes in. The method finds the safe root, chooses the starting directory, walks files below it, filters to keys that really start with the prefix, reads file size and modified time, sorts by key, and returns at most the configured maximum.

**Call relations**: FilesystemBlobStore.list calls this in a worker thread. It uses _contained_root and _resolve to keep the walk anchored inside the configured storage root.

*Call graph*: calls 2 internal fn (_contained_root, _resolve); 4 external calls (__init__, fromtimestamp, walk, Path).


##### `FilesystemBlobStore._resolve`  (lines 161–166)

```
def _resolve(self, key: str) -> Path
```

**Purpose**: Converts a blob key into a safe filesystem path. Its main job is to stop keys like '../secret' from escaping the blob root.

**Data flow**: A key goes in. The configured root is canonicalized, the key is joined to it and resolved, and the method returns the path only if it stays inside the root. Unsafe keys raise ValueError.

**Call relations**: All filesystem read, write, delete, stream, and walk operations call this before touching disk. It calls _contained_root to get the trusted base path.

*Call graph*: calls 1 internal fn (_contained_root); called by 7 (_walk, delete, exists, get, get_stream, put, put_stream).


##### `FilesystemBlobStore._contained_root`  (lines 168–181)

```
def _contained_root(self) -> Path
```

**Purpose**: Finds the canonical blob root directory used for filesystem containment checks. It accepts a not-yet-created root but rejects roots that point somewhere invalid.

**Data flow**: The store’s configured root path is read. The method asks the containment helper to validate and canonicalize it; if the path does not exist yet, it returns the resolved future path instead.

**Call relations**: _resolve and _walk call this whenever they need the trusted storage root. It uses the shared containment helper so operator-facing errors point at the blob.root setting.

*Call graph*: called by 2 (_resolve, _walk); 1 external calls (configured_root).


##### `_is_missing_key`  (lines 184–185)

```
def _is_missing_key(error: ClientError) -> bool
```

**Purpose**: Recognizes S3 error responses that mean “this object does not exist.” Different S3-compatible systems use slightly different codes, so this helper centralizes the check.

**Data flow**: A ClientError from S3 goes in. The error code is read from the response and compared with known missing-key codes. A boolean comes out.

**Call relations**: S3BlobStore.get, exists, and get_stream call this when S3 reports an error. It lets those methods turn missing objects into BlobNotFound or false while re-raising real service failures.

*Call graph*: called by 3 (exists, get, get_stream).


##### `S3BlobStore.put`  (lines 207–209)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Stores a complete byte string as one S3 object. It is the simple upload path for data already held in memory.

**Data flow**: A key and bytes go in. The method gets or creates the S3 client for the current async loop, sends a put_object request to the configured bucket, and returns nothing.

**Call relations**: This is the S3 implementation of BlobStore.put. It depends on _client for efficient, correctly configured S3 access.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get`  (lines 211–221)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a whole S3 object into memory. Missing objects are reported as BlobNotFound so callers see the same behavior as the filesystem backend.

**Data flow**: A key goes in. The method asks S3 for the object, reads the response body bytes, and returns them. If S3 says the key is missing, BlobNotFound is raised.

**Call relations**: This is the S3 implementation of BlobStore.get. It uses _client for the S3 connection and _is_missing_key to normalize S3 missing-object errors.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.exists`  (lines 223–231)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether an object exists in S3 without downloading it. It uses S3’s metadata request rather than reading the object body.

**Data flow**: A key goes in. The method sends a head_object request. Success returns true, a known missing-key error returns false, and other S3 errors are re-raised.

**Call relations**: This is the S3 implementation of BlobStore.exists. It uses _client and _is_missing_key to match the common BlobStore behavior.

*Call graph*: calls 2 internal fn (_client, _is_missing_key).


##### `S3BlobStore.delete`  (lines 233–235)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes an S3 object at a key. S3 deletion is naturally safe to repeat for missing objects.

**Data flow**: A key goes in. The method gets the S3 client and sends delete_object for the configured bucket and key. Nothing is returned.

**Call relations**: This is the S3 implementation of BlobStore.delete. It routes all network access through _client.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get_stream`  (lines 237–248)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams an S3 object in chunks instead of reading the whole object at once. This is useful for large downloads or passing data through the server.

**Data flow**: A key goes in. The method opens the S3 object body, yields chunks of up to the configured size, and closes the response body afterward. Missing keys become BlobNotFound.

**Call relations**: This is the S3 implementation of BlobStore.get_stream. It calls _client to talk to S3 and _is_missing_key to translate missing-object errors.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.put_stream`  (lines 250–294)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Uploads streamed bytes to S3, using multipart upload for large content. Multipart upload is S3’s way of sending a large object as numbered pieces and then asking S3 to assemble them.

**Data flow**: A key and an async stream of chunks go in. The method buffers chunks until they are large enough for an S3 part; small streams are uploaded with one put_object call, while larger streams start multipart upload, upload each part, and complete the upload. If anything fails after multipart upload starts, it aborts the upload so stray partial data is not left behind.

**Call relations**: This is the S3 implementation of BlobStore.put_stream. It relies on _client for all S3 requests and contains the backend-specific large-upload behavior hidden behind the shared BlobStore interface.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_put`  (lines 296–320)

```
async def presigned_put(self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a short-lived URL that lets another process upload exactly one measured object to S3. The signed URL includes the expected size and checksum, so S3 rejects different bytes.

**Data flow**: A key, expected byte size, base64 SHA-256 checksum, and time-to-live go in. The S3 client signs a put_object URL restricted to that bucket, key, size, checksum, and expiry time. The URL string comes out.

**Call relations**: WorkspaceBlobStore.presigned_put calls this after adding the workspace prefix. It uses _client so the URL is signed with the same S3 configuration the backend uses.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_put_unmeasured`  (lines 322–332)

```
async def presigned_put_unmeasured(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a short-lived upload URL for one fixed key without signing the body size or checksum. It is for trusted producers whose output size is not known before they generate it.

**Data flow**: A key and expiry time go in. The S3 client signs a put_object URL limited to that key and lifetime, and returns the URL string.

**Call relations**: WorkspaceBlobStore.presigned_put_unmeasured calls this for S3-backed workspaces. It delegates signing to _client and is intentionally less restrictive than presigned_put.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_get`  (lines 334–341)

```
async def presigned_get(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a short-lived URL that lets a holder download one S3 object. Since downloads have no request body, there is no size or checksum to sign.

**Data flow**: A key and expiry time go in. The S3 client signs a get_object URL for that bucket and key, and the URL string comes out.

**Call relations**: WorkspaceBlobStore.presigned_get calls this after adding the workspace prefix. It uses _client so reads are signed consistently with the backend.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.put_host`  (lines 343–354)

```
async def put_host(self) -> str
```

**Purpose**: Reports the hostname that presigned upload URLs will contact. The sandbox egress proxy uses this so it can allow the upload destination and block unrelated network access.

**Data flow**: The method reads the S3 client’s resolved endpoint URL, extracts its hostname, and returns either that hostname or the bucket-prefixed hostname used by AWS virtual-hosted addressing. If no hostname exists, it raises an error.

**Call relations**: This method calls _client and urlsplit. It keeps proxy allow-rules aligned with the exact URL format produced by S3 signing.

*Call graph*: calls 1 internal fn (_client); 1 external calls (urlsplit).


##### `S3BlobStore.list`  (lines 356–373)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists S3 objects under a required prefix and returns lightweight records for them. The prefix requirement avoids expensive or accidental bucket-wide scans.

**Data flow**: A prefix goes in. Empty prefixes are rejected. The method pages through S3 list_objects_v2 results, turns each object into a BlobEntry with key, size, and UTC modified time, stops at the configured maximum, and returns a tuple.

**Call relations**: This is the S3 implementation of BlobStore.list. It uses _client for the paginator and produces the same BlobEntry shape as the filesystem backend.

*Call graph*: calls 1 internal fn (_client); 1 external calls (__init__).


##### `S3BlobStore.close`  (lines 375–381)

```
async def close(self) -> None
```

**Purpose**: Closes the cached S3 client for the currently running async event loop. This releases the client’s network resources when that loop is done.

**Data flow**: The current event loop is read. Any cached client and lock for that loop are removed from the store; if a client existed, it is closed. Nothing is returned.

**Call relations**: This is a cleanup companion to _client. It acts only on the current loop because clients are cached separately per loop.

*Call graph*: 1 external calls (get_running_loop).


##### `S3BlobStore._client`  (lines 383–409)

```
async def _client(self) -> AioBaseClient
```

**Purpose**: Returns the reusable S3 client for the current async event loop, creating it if needed. Reusing the client avoids repeatedly parsing S3 service data and avoids using an async network client on the wrong loop.

**Data flow**: The current event loop is read. If a client is already cached for that loop, it is returned. Otherwise a per-loop lock is used so only one task creates the client, then aiobotocore builds an S3 client with the correct endpoint, region, and signing/addressing settings, stores it, and returns it.

**Call relations**: Every S3 operation in this file calls _client before talking to S3. It is the central place that makes S3 access efficient and makes presigned URLs match the configured deployment.

*Call graph*: called by 11 (delete, exists, get, get_stream, list, presigned_get, presigned_put, presigned_put_unmeasured, put, put_host (+1 more)); 3 external calls (get_session, Lock, get_running_loop).


##### `WorkspaceBlobStore.put`  (lines 422–423)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Stores bytes under the current workspace’s private prefix. Callers provide only a workspace-relative key, which helps prevent cross-workspace writes.

**Data flow**: A workspace-relative key and bytes go in. _full adds the current workspace prefix, then the backend stores the bytes at that full key. Nothing is returned.

**Call relations**: This wrapper delegates to the configured filesystem or S3 backend after _full applies the workspace namespace.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.get`  (lines 425–426)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads bytes from the current workspace’s private blob area. Callers do not choose the workspace prefix themselves.

**Data flow**: A workspace-relative key goes in. _full turns it into a full storage key for the current workspace, the backend reads the object, and bytes come out.

**Call relations**: This wrapper calls _full first, then delegates to the backend. If no workspace is bound, _full’s workspace lookup is where that problem is caught.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.exists`  (lines 428–429)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a blob exists inside the current workspace. It keeps the existence check scoped to the active workspace.

**Data flow**: A workspace-relative key goes in. _full adds the workspace prefix, the backend checks that full key, and a boolean comes out.

**Call relations**: This wrapper uses _full for isolation and then hands the check to the underlying store.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.delete`  (lines 431–432)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes a blob from the current workspace’s storage area. It cannot be used to delete another workspace’s object by supplying a full prefixed key.

**Data flow**: A workspace-relative key goes in. _full builds the full key for the active workspace, the backend deletes that key if present, and nothing is returned.

**Call relations**: This wrapper calls _full before delegating delete to the backend.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.get_stream`  (lines 434–438)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Opens a chunked read for a blob in the current workspace. It resolves the workspace prefix immediately, so the stream can keep working even if the workspace scope exits before all chunks are consumed.

**Data flow**: A workspace-relative key goes in. _full immediately creates the full key, and the backend returns an async stream of bytes for that full key.

**Call relations**: Context code that reads member blob text calls this. It delegates to the backend’s stream reader after fixing the workspace-scoped key.

*Call graph*: calls 1 internal fn (_full); called by 1 (_member_blob_text).


##### `WorkspaceBlobStore.put_stream`  (lines 440–441)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed bytes into the current workspace’s blob area. It is the workspace-safe version of large-object upload.

**Data flow**: A workspace-relative key and chunk stream go in. _full adds the workspace prefix, and the backend consumes the stream into that full key. Nothing is returned.

**Call relations**: This wrapper adds namespace safety, then hands the large write to FilesystemBlobStore or S3BlobStore.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.list`  (lines 443–448)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists blobs under a prefix inside the current workspace and returns keys without the internal workspace prefix. This lets callers think in workspace-relative paths.

**Data flow**: A workspace-relative prefix goes in. The method rejects an empty prefix, computes the workspace root prefix, asks the backend to list under root plus prefix, then strips the workspace root from each returned key before returning the entries.

**Call relations**: This wrapper calls _full and dataclasses.replace to translate between internal full keys and caller-facing workspace-relative keys.

*Call graph*: calls 1 internal fn (_full); 1 external calls (replace).


##### `WorkspaceBlobStore.presigned_put`  (lines 450–461)

```
async def presigned_put(self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a measured presigned S3 upload URL for an object inside the current workspace. It is only available when the backend is S3.

**Data flow**: A workspace-relative key, size, checksum, and expiry go in. If the backend is S3, _full adds the workspace prefix and S3BlobStore.presigned_put returns a URL. If the backend is not S3, TypeError is raised.

**Call relations**: This is the workspace-scoped front door to S3BlobStore.presigned_put. It keeps direct sandbox uploads inside the active workspace.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.presigned_put_unmeasured`  (lines 463–469)

```
async def presigned_put_unmeasured(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates an unmeasured presigned S3 upload URL for one object inside the current workspace. It is for S3-only cases where the writer cannot know the final size beforehand.

**Data flow**: A workspace-relative key and expiry go in. If the backend is S3, _full prefixes the key and S3BlobStore.presigned_put_unmeasured returns the URL. Otherwise TypeError is raised.

**Call relations**: This wrapper connects workspace scoping to the S3-only unmeasured upload signer.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.presigned_get`  (lines 471–478)

```
async def presigned_get(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a short-lived S3 download URL for an object inside the current workspace. It is only valid for S3-backed storage.

**Data flow**: A workspace-relative key and expiry go in. If the backend is S3, _full builds the full key and S3BlobStore.presigned_get returns the URL. Otherwise TypeError is raised.

**Call relations**: This wrapper adds the workspace prefix before handing off to S3BlobStore.presigned_get.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore._full`  (lines 480–483)

```
def _full(self, key: str) -> str
```

**Purpose**: Builds the real storage key for the current workspace. It also rejects keys that are already workspace-prefixed, which prevents accidental double-prefixing or attempts to choose another workspace.

**Data flow**: A workspace-relative key goes in. The method checks that it does not already start with workspaces/, reads the currently bound workspace id, and returns workspaces/<id>/<key>.

**Call relations**: Every WorkspaceBlobStore operation calls _full before reaching the backend. It depends on ws_current to know which workspace is active.

*Call graph*: called by 10 (delete, exists, get, get_stream, list, presigned_get, presigned_put, presigned_put_unmeasured, put, put_stream); 1 external calls (ws_current).


##### `FleetBlobStore.put`  (lines 494–495)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Stores bytes in a deploy-wide, non-workspace namespace such as static assets or terminal spill data. It refuses keys outside approved fleet prefixes.

**Data flow**: A key and bytes go in. _checked verifies the key starts with an allowed fleet prefix, then the backend stores the bytes. Nothing is returned.

**Call relations**: This wrapper delegates to the backend only after _checked confirms the key belongs to a fleet namespace.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.get`  (lines 497–498)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads bytes from an approved deploy-wide namespace. It prevents callers from using the fleet store to reach workspace data.

**Data flow**: A key goes in. _checked validates the prefix, the backend reads that key, and bytes come out.

**Call relations**: This wrapper calls _checked before delegating the read to the filesystem or S3 backend.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.exists`  (lines 500–501)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether an approved deploy-wide blob exists. Prefix checking keeps the question limited to known fleet areas.

**Data flow**: A key goes in. _checked accepts or rejects the namespace, the backend checks for the object, and a boolean comes out.

**Call relations**: This wrapper performs the fleet namespace guard before using the backend’s exists operation.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.delete`  (lines 503–504)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes a deploy-wide blob from an approved namespace. It cannot be used as a back door into workspace-prefixed data.

**Data flow**: A key goes in. _checked validates the key, the backend deletes it if present, and nothing is returned.

**Call relations**: This wrapper calls _checked before handing deletion to the configured backend.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.get_stream`  (lines 506–507)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a deploy-wide blob from an approved namespace. It is useful for large fleet-owned data.

**Data flow**: A key goes in. _checked validates the prefix, and the backend returns an async stream of byte chunks for that key.

**Call relations**: This wrapper adds namespace validation before delegating to the backend stream reader.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.put_stream`  (lines 509–510)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed bytes into an approved deploy-wide namespace. It is the fleet-safe version of large-object writes.

**Data flow**: A key and async chunk stream go in. _checked validates the key, and the backend consumes the chunks into that object. Nothing is returned.

**Call relations**: This wrapper calls _checked before using the backend’s streaming upload behavior.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.list`  (lines 512–513)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists deploy-wide blobs under an approved prefix. It prevents broad listing outside the fleet namespaces.

**Data flow**: A prefix goes in. _checked verifies it starts with an allowed fleet prefix, then the backend lists matching objects and returns BlobEntry records.

**Call relations**: This wrapper validates the fleet namespace before delegating listing to the backend.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore._checked`  (lines 515–518)

```
def _checked(self, key: str) -> str
```

**Purpose**: Enforces the closed list of fleet namespaces. It is the guardrail that separates deploy-owned blobs from workspace-owned blobs.

**Data flow**: A key goes in. If it starts with one of the allowed prefixes, the same key is returned. Otherwise ValueError is raised.

**Call relations**: Every FleetBlobStore operation calls _checked before touching the backend. It is the central safety check for fleet storage.

*Call graph*: called by 7 (delete, exists, get, get_stream, list, put, put_stream).


##### `blob_store_for`  (lines 521–533)

```
def blob_store_for(config: BlobConfig) -> FilesystemBlobStore | S3BlobStore
```

**Purpose**: Builds the configured base blob backend from application configuration. It chooses local filesystem storage or S3 storage and checks that required settings are present.

**Data flow**: A BlobConfig goes in. If the backend is filesystem, the root must be present and a FilesystemBlobStore is returned. If the backend is S3, the bucket must be present and an S3BlobStore is returned with endpoint and region settings.

**Call relations**: Startup or setup code uses this factory to create the storage backend. WorkspaceBlobStore and FleetBlobStore can then wrap the returned backend for safer namespacing.

*Call graph*: 2 external calls (__init__, __init__).


### Database durability foundations
Database connection, migration, transaction, tenancy, and object-replay helpers provide safe persistence primitives.

### `core/src/ufo/db.py`

`io_transport` · `startup, request handling, migrations, and teardown`

This file exists because one running service can serve many workspaces, and a database mistake here could leak one customer’s data to another. Its main job is to make database access safe by default. Normal code uses `workspace_tx`, which opens a transaction and, for PostgreSQL, pins the current workspace ID into the transaction. PostgreSQL row-level security, meaning database rules that hide rows unless the current workspace matches, then enforces the boundary. If no workspace was set, the database fails closed instead of guessing.

The file also builds and remembers database engines, which are SQLAlchemy objects that own pools of reusable connections. It keeps separate engines per async event loop because some database drivers tie a connection to the loop that created it. Think of this like keeping a separate key ring for each worker thread, so no worker tries to use keys made for another.

There is one deliberate exception: `owner_tx`. It is for background sweeps that must list work across all workspaces, then re-enter each workspace safely afterward. The file also checks reachability at startup, cleans up pools during shutdown, configures SQLite so local tests behave predictably, records useful metrics around slow or failed transaction opening, and runs Alembic migrations so the database schema matches the code.

#### Function details

##### `_build_engine`  (lines 101–111)

```
def _build_engine(url: str, pool: _Pool) -> AsyncEngine
```

*Call graph*: calls 1 internal fn (_pool_kwargs); called by 2 (_engine_for, verify_db_reachable); 1 external calls (create_async_engine).


##### `_pool_kwargs`  (lines 114–134)

```
def _pool_kwargs(url: str, pool: _Pool) -> dict[str, Any]
```

*Call graph*: calls 1 internal fn (_driver_kwargs); called by 1 (_build_engine); 1 external calls (make_url).


##### `_driver_kwargs`  (lines 137–164)

```
def _driver_kwargs(driver: str, pool: _Pool) -> dict[str, Any]
```

*Call graph*: called by 1 (_pool_kwargs).


##### `_engine_for`  (lines 167–183)

```
def _engine_for(url: str, pool: _Pool) -> AsyncEngine
```

*Call graph*: calls 1 internal fn (_build_engine); called by 2 (owner_tx, workspace_tx); 1 external calls (get_running_loop).


##### `init_db`  (lines 186–190)

```
def init_db(url: str) -> None
```


##### `init_owner_db`  (lines 193–207)

```
def init_owner_db(url: str) -> None
```


##### `verify_db_reachable`  (lines 210–230)

```
async def verify_db_reachable() -> None
```

*Call graph*: calls 1 internal fn (_build_engine).


##### `dispose_db`  (lines 233–256)

```
async def dispose_db() -> None
```

*Call graph*: calls 1 internal fn (_hand_off); 1 external calls (get_running_loop).


##### `_hand_off`  (lines 259–266)

```
def _hand_off(loop: asyncio.AbstractEventLoop, engine: AsyncEngine) -> None
```

*Call graph*: called by 1 (dispose_db); 1 external calls (call_soon_threadsafe).


##### `_dispose_on_this_loop`  (lines 269–276)

```
def _dispose_on_this_loop(engine: AsyncEngine) -> None
```

*Call graph*: 2 external calls (ensure_future, dispose).


##### `dispose_loop_engines`  (lines 279–289)

```
async def dispose_loop_engines() -> None
```

*Call graph*: 1 external calls (get_running_loop).


##### `_stopping`  (lines 292–299)

```
def _stopping() -> bool
```

*Call graph*: called by 1 (_opened); 1 external calls (current_task).


##### `_opened`  (lines 303–381)

```
async def _opened(engine: AsyncEngine, path: str) -> AsyncIterator[AsyncConnection]
```

*Call graph*: calls 1 internal fn (_stopping); called by 2 (owner_tx, workspace_tx); 8 external calls (Lock, ensure_future, shield, AsyncExitStack, begin, monotonic, emit_histogram, emit_metric).


##### `workspace_tx`  (lines 385–395)

```
async def workspace_tx() -> AsyncIterator[AsyncConnection]
```

*Call graph*: calls 2 internal fn (_engine_for, _opened); 1 external calls (text).


##### `failed_statement`  (lines 398–418)

```
def failed_statement(error: BaseException) -> dict[str, str]
```


##### `owner_tx`  (lines 422–435)

```
async def owner_tx() -> AsyncIterator[AsyncConnection]
```

*Call graph*: calls 2 internal fn (_engine_for, _opened).


##### `apply_migrations`  (lines 438–476)

```
def apply_migrations(url: str, pack: str | None=None) -> None
```

*Call graph*: calls 1 internal fn (_seal_sqlite_journal); 7 external calls (__init__, upgrade, from_config, Path, migration_locations, catch_warnings, simplefilter).


##### `_seal_sqlite_journal`  (lines 479–495)

```
def _seal_sqlite_journal(url: str) -> None
```

*Call graph*: called by 1 (apply_migrations); 2 external calls (make_url, connect).


##### `core_migration_head`  (lines 498–506)

```
def core_migration_head() -> str
```

*Call graph*: 2 external calls (__init__, from_config).


##### `_sqlite_on_connect`  (lines 509–515)

```
def _sqlite_on_connect(dbapi_connection: Any, _connection_record: Any) -> None
```


##### `_sqlite_begin_immediate`  (lines 518–520)

```
def _sqlite_begin_immediate(connection: sa.Connection) -> None
```

*Call graph*: 1 external calls (exec_driver_sql).


### `core/src/ufo/durability.py`

`io_transport` · `cross-cutting persistence and replay`

DBOS stores workflow inputs, step results, and errors in a database so work can be replayed after a crash. The tricky part is that replay may happen with a newer version of the code than the one that originally saved the data. A normal Python pickle, which is Python’s built-in object packaging format, can restore a Pydantic model without running its normal validation or default-setting logic. That means a field added later might simply be missing, causing a confusing failure much later.

This file solves that by defining a custom serializer named `ufo_pickle`. It still uses pickle for the overall format, but it treats Pydantic `BaseModel` objects specially. Instead of freezing the model exactly as-is, it records the model’s class and its current field values. When the data is loaded again, the model is rebuilt through Pydantic’s normal validation path. This lets new default fields appear automatically, ignores fields that no longer exist, and gives clearer failures when a required field is missing.

The file also provides `replay_safe_client`, the project’s standard way to create a DBOS client. That matters because saved database rows are tagged with the serializer’s name; a client without this serializer may read the stored text but not understand it as the original object.

#### Function details

##### `replay_safe_client`  (lines 27–31)

```
def replay_safe_client(system_database_url: str) -> DBOSClient
```

**Purpose**: Creates a DBOS client that knows how to read and write this project’s replay-safe saved data. Someone uses this instead of constructing a plain DBOS client so recovered workflow records decode correctly.

**Data flow**: It takes a system database URL as input. It builds a `ReplaySafeSerializer`, gives that serializer and the URL to `DBOSClient`, and returns the configured client. The outside effect is that future database reads and writes through that client use the `ufo_pickle` format.

**Call relations**: This is the doorway into the rest of the file’s behavior. It calls `ReplaySafeSerializer` to get the custom packaging logic, then hands it to `dbos.DBOSClient` so DBOS uses it whenever it persists or reloads workflow data.

*Call graph*: 2 external calls (__init__, DBOSClient).


##### `_rebuild`  (lines 34–35)

```
def _rebuild(model_class: type[BaseModel], fields: dict[str, object]) -> BaseModel
```

**Purpose**: Recreates a Pydantic model from saved field values using the model’s current rules. This is the key step that lets old saved objects adapt safely to newer code.

**Data flow**: It receives a model class and a dictionary of saved field values. It asks that class to validate and build a fresh model from those values. The result is a normal Pydantic model object with current defaults and validation applied.

**Call relations**: This function is not usually called by project code directly. `_ModelPickler.reducer_override` writes references to it into the pickle data, and Python’s pickle loader calls it later during `ReplaySafeSerializer.deserialize` when rebuilding saved models.


##### `_ModelPickler.reducer_override`  (lines 39–42)

```
def reducer_override(self, obj: object) -> tuple[Callable[..., object], tuple[object, ...]]
```

**Purpose**: Tells pickle to save Pydantic models in the project’s safer form instead of pickle’s default form. It is like giving the packer special instructions for fragile items.

**Data flow**: It receives each object that pickle is about to save. If the object is a Pydantic `BaseModel`, it returns instructions saying: rebuild this later by calling `_rebuild` with the object’s class and field dictionary. If the object is not a Pydantic model, it returns `NotImplemented`, meaning normal pickle behavior should be used.

**Call relations**: This method is used by the pickle machinery inside `ReplaySafeSerializer.serialize`. It provides the special case that makes model replay safer, while leaving all other kinds of objects to pickle’s standard behavior.


##### `ReplaySafeSerializer.name`  (lines 48–49)

```
def name(self) -> str
```

**Purpose**: Returns the fixed name DBOS stores alongside data written with this serializer. That name is how DBOS knows which decoding method to use later.

**Data flow**: It takes no outside data beyond the serializer object itself. It returns the constant string `ufo_pickle`. Nothing else is changed.

**Call relations**: DBOS calls this as part of its serializer interface. The name links database rows written by `ReplaySafeSerializer.serialize` with future reads performed by `ReplaySafeSerializer.deserialize`.


##### `ReplaySafeSerializer.serialize`  (lines 51–54)

```
def serialize(self, data: object) -> str
```

**Purpose**: Turns a Python object into a text string that can be stored in the database, while saving Pydantic models in the replay-safe way. This is used whenever DBOS needs to persist workflow-related data.

**Data flow**: It receives any Python object. It creates an in-memory byte buffer, uses `_ModelPickler` to pickle the object into that buffer, then base64-encodes the bytes into ordinary UTF-8 text. The returned string is suitable for database storage.

**Call relations**: DBOS calls this serializer when recording data through a client created by `replay_safe_client`. Inside, it creates `_ModelPickler`, whose `reducer_override` decides whether Pydantic models need special rebuild instructions, then uses `base64.b64encode` so the binary pickle can travel as text.

*Call graph*: 3 external calls (__init__, b64encode, BytesIO).


##### `ReplaySafeSerializer.deserialize`  (lines 56–57)

```
def deserialize(self, serialized_data: str) -> object
```

**Purpose**: Turns a stored text string back into the original Python object. During this process, Pydantic models saved by this file are rebuilt through validation instead of blindly restored.

**Data flow**: It receives a base64 text string from storage. It decodes the text back into pickle bytes, then asks pickle to load the object. If the saved data included Pydantic models recorded by `_ModelPickler`, pickle calls `_rebuild` to recreate them safely.

**Call relations**: DBOS calls this when replaying or reading stored workflow data that was written with the `ufo_pickle` serializer name. It uses `base64.b64decode` first, then `pickle.loads` to reconstruct the object graph.

*Call graph*: 2 external calls (b64decode, loads).


### Workspace artifact records
Workspace transcript and preview records define small durable handles for saved conversation and media artifacts.

### `core/src/ufo/loop/transcript.py`

`io_transport` · `end of turn and repair republish`

A conversation transcript is the durable record of what has happened so far. This file wraps the shared blob store, which is a place for saving named chunks of data, and gives the rest of the system a simple way to read or update that record for one conversation.

The important rule here is: transcript versions only move forward. Each transcript has a sequence number, called `seq`, that works like a page number in a notebook. Before writing a new transcript, this code first reads the current one. If the saved transcript already has the same or a later sequence number, the write is ignored. That means a slow or repeated task cannot accidentally replace the official record with an older copy.

The `Transcript` class is small on purpose. It knows which blob store to use and which conversation ID it belongs to. When reading, it builds the right storage key, fetches the saved bytes, and turns them back into a `Conversation`. When writing, it checks the existing version, then turns the new `Conversation` into bytes and stores it. Without this guard, competing end-of-turn or repair flows could corrupt the conversation history by writing out of order.

#### Function details

##### `Transcript.read`  (lines 17–22)

```
async def read(self) -> Conversation | None
```

**Purpose**: Reads the saved transcript for this conversation, if one exists. It gives callers either the current `Conversation` record or `None` when nothing has been saved yet.

**Data flow**: It starts with the transcript object's blob store and conversation ID. It turns the conversation ID into the storage key, asks the blob store for the saved bytes, and, if found, decodes those bytes into a `Conversation`. If the blob is missing, it returns `None` instead of treating that as an error.

**Call relations**: This is the lookup step used before saving. `Transcript.write` calls it first so it can compare the currently saved sequence number with the new one and avoid overwriting newer transcript data.

*Call graph*: called by 1 (write); 2 external calls (decode, transcript_key).


##### `Transcript.write`  (lines 24–28)

```
async def write(self, conversation: Conversation) -> None
```

**Purpose**: Writes a new transcript only if it is newer than the one already saved. This protects the durable conversation history from being rolled back by a late or repeated writer.

**Data flow**: It receives a `Conversation` to save. First it reads the currently stored transcript. If a transcript already exists with an equal or higher sequence number, it stops and changes nothing. Otherwise, it encodes the new conversation into bytes, builds the storage key for this conversation, and writes those bytes to the blob store.

**Call relations**: This is the update step used when a turn finishes or when a repair flow republishes a committed result. It relies on `Transcript.read` for the freshness check, then hands the conversation to the shared transcript encoder before storing it under the conversation's transcript key.

*Call graph*: calls 1 internal fn (read); 2 external calls (encode, transcript_key).


### `core/src/ufo/media/previews.py`

`data_model` · `cross-cutting`

This file contains one simple data container: `StoredPreview`. It represents a picture preview that has already been saved somewhere in the workspace’s blob storage. A “blob” is just stored raw data, such as an image file, and the `blob_key` is the workspace-relative name or path used to find it again. The `size_bytes` field records the exact size of that stored data in bytes.

The class is a frozen dataclass. A dataclass is a Python shortcut for making plain record-like objects, and “frozen” means its values cannot be changed after creation. That matters because storage results should be treated like receipts: once a preview has been written, its location and size should not accidentally drift or be edited in place.

Without this file, different parts of the media system might pass preview information around as loose dictionaries, tuples, or separate values. That would make mistakes easier, such as mixing up the key and size or forgetting one of them. This file keeps that small but important piece of information named, typed, and predictable.


### Shared schema contracts
Common record types and table definitions describe the durable entities and constraints used across the system.

### `core/src/ufo/schema/records.py`

`data_model` · `cross-cutting`

This file is like the set of standardized forms used by the whole system. A “turn” is one unit of work: a member says something, an agent runs, and the result eventually becomes done, failed, or cancelled. Without these records, different parts of the system could disagree about the shape of a request, whether it is finished, what final action it left for the user, or how to identify retries safely.

Most classes here are Pydantic models, meaning they are data shapes that also check incoming values. They describe things such as an agent’s settings, a structured question to ask a user, a private credential request, an account connection request, and the terminal frame that records how a turn ended. The file also defines fixed vocabularies such as allowed turn statuses, reasoning levels, sandbox sizes, and proposal states.

A few helper functions create stable UUIDs, which are unique identifiers generated from known inputs. That matters because repeated workflow replays should point to the same turn, bill, or mid-turn reply instead of creating duplicates. The validators protect boundaries too: they flatten unsafe text that might be rendered as context, reject unknown timezones early, repair database timestamp quirks, and enforce the rule that only finished turns may carry a terminal result.

#### Function details

##### `auto_agent_icon`  (lines 177–196)

```
def auto_agent_icon(name: str, taken: Collection[str]) -> TablerIcon
```

**Purpose**: Chooses a starting icon for a new agent. It first looks for meaningful words in the agent name, such as “support” or “billing,” and otherwise picks a stable icon from the unused ones so agents in one workspace are easier to tell apart.

**Data flow**: It receives the agent name and the set of icons already taken in that workspace. It hashes the name to get a repeatable number, checks whether any name word maps to a preferred icon, then chooses either that unused keyword icon, an unused hash-based icon, or a repeated icon when all are taken. It returns one valid icon name.

**Call relations**: This helper is called when something needs to assign an icon before storing or showing a new agent. Inside, it relies on SHA-256 hashing from the standard library so the same name tends to make the same choice without needing randomness.

*Call graph*: 1 external calls (sha256).


##### `admits_spent_balance`  (lines 221–235)

```
def admits_spent_balance(intent: ToolIntent) -> bool
```

**Purpose**: Decides whether a prepared tool action should still be allowed when a workspace has run out of balance. The one allowed action is managing billing, because blocking that would also block the way to fix the problem.

**Data flow**: It receives a ToolIntent, which is a prepared tool call. It checks whether the tool is an object action and whether its kind and action match the billing-management action. It returns true only for that exact case, and changes nothing.

**Call relations**: Balance-gating code can call this before refusing a turn for lack of funds. This function does not hand off to other project code; it simply recognizes the safe billing-refill intent.


##### `turn_id_for`  (lines 252–254)

```
def turn_id_for(workspace_id: UUID, conversation_id: UUID, seq: int) -> UUID
```

**Purpose**: Creates the stable ID for a turn from its workspace, conversation, and sequence number. This lets the same logical turn get the same identifier even if workflow machinery retries or replays work.

**Data flow**: It receives a workspace ID, a conversation ID, and a turn sequence number. It combines them into one text key and feeds that into UUID version 5, which makes a repeatable UUID from a namespace and text. It returns the generated turn ID.

**Call relations**: Code that admits or reconstructs turns can call this when it needs the canonical turn identity. It delegates the actual repeatable UUID creation to the standard uuid5 function.

*Call graph*: 1 external calls (uuid5).


##### `ledger_id_for`  (lines 257–262)

```
def ledger_id_for(workspace_id: UUID, turn_id: UUID, dimension: str, attempt: str='') -> UUID
```

**Purpose**: Creates a stable billing-ledger ID for one turn, one billing dimension, and one run attempt. This prevents replayed billing writes for the same attempt from becoming duplicate charges.

**Data flow**: It receives the workspace ID, turn ID, billing dimension, and optionally an attempt ID. It builds a text key that names exactly that billing slice, then turns it into a repeatable UUID. It returns that ledger-row ID.

**Call relations**: Billing code can call this before writing usage or cost records. It hands the composed key to uuid5 so workflow replays collapse onto the same billing record, while separate resumed attempts get separate IDs.

*Call graph*: 1 external calls (uuid5).


##### `mid_turn_reply_id_for`  (lines 265–276)

```
def mid_turn_reply_id_for(turn_id: UUID, round_index: int, span_index: int, attempt: str='') -> UUID
```

**Purpose**: Creates the stable ID for a reply sent before a turn fully ends. This keeps a recovered workflow from delivering the same mid-turn message twice.

**Data flow**: It receives the turn ID, round number, span number, and optionally the run attempt ID. It combines those into a precise text key and converts that key into a repeatable UUID. It returns the reply ID for that exact spoken span.

**Call relations**: Delivery or transcript-writing code can call this when recording a mid-turn reply. It uses uuid5 so replaying the same attempt finds the same reply record, while a new resumed attempt produces distinct reply records.

*Call graph*: 1 external calls (uuid5).


##### `TurnContext._tag_safe_line`  (lines 459–463)

```
def _tag_safe_line(cls, value: str | None) -> str | None
```

**Purpose**: Cleans surface-provided text before it is later rendered as turn context. It stops sender names, question text, or source text from pretending to be markup-like tags.

**Data flow**: It receives a string or nothing. If there is text, it removes angle brackets, collapses all whitespace into single spaces, and turns an empty result into null. It returns the cleaned one-line value.

**Call relations**: Pydantic calls this automatically when creating or loading a TurnContext for the sender, question, and source fields. It does not call project helpers; it is the boundary filter before the engine sees these context strings.


##### `TurnContext._known_zone`  (lines 467–474)

```
def _known_zone(cls, value: str | None) -> str | None
```

**Purpose**: Checks that a supplied timezone name is real. This catches bad timezone values at the boundary instead of letting a turn fail later while running.

**Data flow**: It receives a timezone string or nothing. If there is a string, it asks the system timezone database to load it; if that fails, it raises a clear validation error. It returns the original timezone name when it is valid.

**Call relations**: Pydantic calls this automatically for the TurnContext timezone field. It hands the check to ZoneInfo, the standard Python timezone lookup tool.

*Call graph*: 1 external calls (ZoneInfo).


##### `Turn.spawned`  (lines 508–511)

```
def spawned(self) -> bool
```

**Purpose**: Answers whether this turn was created by another turn rather than directly admitted as an ordinary member or internal turn. It is a small convenience property for recognizing child-agent work.

**Data flow**: It reads the turn’s parent_turn_id field. If that field has a value, it returns true; otherwise it returns false. It does not change the turn.

**Call relations**: Any code with a Turn object can read this property when it needs to branch on whether the turn came from a spawn path. It has no handoff; it is a readable summary of an existing field.


##### `Turn._nothing_created`  (lines 515–518)

```
def _nothing_created(cls, value: object) -> object
```

**Purpose**: Turns a missing database value for created objects into an empty list-like tuple. This lets the rest of the code treat “nothing was created” consistently.

**Data flow**: It receives the raw value for created_refs before normal parsing. If the value is null, it returns an empty tuple; otherwise it returns the value unchanged for normal validation. The resulting Turn has an empty collection instead of null.

**Call relations**: Pydantic calls this automatically when loading or constructing a Turn. It sits between database storage, where the column may be null, and application code, which expects a safe empty collection.


##### `Turn._aware_utc`  (lines 522–527)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Ensures turn timestamps carry timezone information. This protects against database drivers that return UTC timestamps without marking them as UTC.

**Data flow**: It receives a created_at or updated_at datetime, or nothing. If there is no value, it stays null; if the datetime already has timezone information, it is returned unchanged; if it is missing timezone information, it is marked as UTC. The Turn then carries timestamps that will not be mistaken for local time.

**Call relations**: Pydantic calls this automatically for Turn timestamp fields. When a timestamp lacks timezone data, it uses datetime.replace to attach the UTC marker without shifting the clock time.

*Call graph*: 1 external calls (replace).


##### `Turn._terminal_matches_status`  (lines 530–535)

```
def _terminal_matches_status(self) -> 'Turn'
```

**Purpose**: Enforces the core rule that a turn has a terminal result exactly when its status is terminal. It also checks that the terminal frame’s own status agrees with the turn’s status.

**Data flow**: It receives the fully built Turn model. It compares the turn status with whether terminal data is present, then compares terminal.status to status when terminal data exists. It returns the same Turn if consistent, or raises a validation error if the record contradicts itself.

**Call relations**: Pydantic calls this after constructing a Turn. This is the final consistency gate that protects downstream workers and surfaces from impossible records, such as a running turn with a final result or a failed turn whose terminal frame says done.


### `core/src/ufo/schema/tables.py`

`data_model` · `database setup and all database-backed runtime operations`

This file is the system’s database blueprint. Instead of writing separate table definitions for local development and production, it uses SQLAlchemy, a Python library for describing databases, to define one shared schema that can be used with SQLite in development and Postgres in deployed environments.

The central object is `metadata`, which acts like a binder holding all table plans. Each `sa.Table` entry describes one kind of stored record: workspaces, members, agents, conversations, turns in a conversation, incoming messages, billing ledger entries, credentials, external connections, synced sources, pages, artifacts, and runtime coordination records. The columns say what information is stored. Foreign keys link records together, like saying every conversation belongs to a workspace and an agent. Unique constraints prevent duplicate identities, such as two members with the same email in one workspace. Check constraints are safety rules inside the database, such as allowed status values or making sure token totals add up.

A useful analogy is a building code for the app’s data: application code may come and go, but the database itself refuses shapes that do not fit these rules. Without this file, migrations, queries, and runtime storage would not share a single understanding of what valid data looks like.

#### Function details

##### `_conversation_audience`  (lines 12–13)

```
def _conversation_audience(context: DefaultExecutionContext) -> str
```

**Purpose**: This function chooses the default audience for a new conversation when the caller has not supplied one directly. In plain terms, it decides whether the conversation is shared or tied to a particular member based on the row being inserted.

**Data flow**: It receives a SQLAlchemy execution context, which is the database library’s snapshot of the values currently being inserted. It reads the current `member_id`, passes that value to `conversation_audience`, then turns the result into text. The returned text becomes the conversation row’s `audience` value.

**Call relations**: SQLAlchemy calls this function automatically when inserting a conversation row that needs a Python-side default for `audience`. The function asks `ufo.turns.audience.conversation_audience` to apply the project’s audience rule, so this schema file does not duplicate that decision logic.

*Call graph*: 2 external calls (get_current_parameters, conversation_audience).


### Extension-owned storage
Skill creation persistence manages workspace-scoped extension records while preventing conflicts and overwrites.

### `extensions/skill_create/ufo_ext_skill_create/store.py`

`domain_logic` · `request handling and cross-turn persistence`

A workspace can have its own user-written skills, and those skills need to survive across conversations and temporary sandboxes. This file is the place that turns a skill folder into a database row, and later turns that row back into files or a runnable skill.

The main class, UserSkillStore, works like a careful librarian. When someone saves a skill, it first checks that the name is safe, that the skill content can be parsed, and that it does not steal the name of a built-in skill. It also enforces limits: a workspace can only have so many saved skills, and only so many pinned skills.

The file uses a generation value, which is like a version stamp. A caller must save using the generation it previously read. If someone else changed or deleted the skill in the meantime, the save is rejected instead of silently overwriting their work.

Skill files are stored as JSON containing base64 text, which is a safe text form of raw bytes. Separate columns keep quick-to-read card information such as description, dependencies, agents, and pin status. That lets the system list skills without fully unpacking every stored bundle.

Deletion also cleans up search index data when available, so removed skills do not leave stale searchable fragments behind.

#### Function details

##### `_save_lock_key`  (lines 54–56)

```
def _save_lock_key(workspace_id: UUID) -> int
```

**Purpose**: This helper turns a workspace ID into a stable number used for a database lock. The lock makes saves within the same workspace happen one at a time, so two writers cannot slip past the same safety checks.

**Data flow**: It receives a workspace UUID, converts it to text, hashes it with SHA-256, then turns the first part of that hash into an integer. The result is a repeatable lock key for that workspace and does not change any stored data.

**Call relations**: UserSkillStore.save calls this right before entering the critical save section. The returned key is handed to PostgreSQL as an advisory transaction lock, which is a database-level 'only one at a time' sign for that workspace.

*Call graph*: called by 1 (save); 1 external calls (sha256).


##### `UserSkillStore.save`  (lines 123–238)

```
async def save(self, name: str, files: Mapping[str, bytes], registry_names: frozenset[str], pinned: bool=False, generation: UUID | None=None) -> RuntimeSkill
```

**Purpose**: This saves a new or edited user skill for the current workspace. It validates the name and content, prevents overwriting built-in skills, enforces workspace limits, and uses a generation stamp so stale edits are refused.

**Data flow**: It receives a skill name, a mapping of file paths to bytes, the names already owned by built-in or pack skills, a pin choice, and optionally the generation last read by the caller. It checks the name, parses the files into a runtime skill, encodes the files into JSON-safe text, computes a content digest, and writes or updates one database row. It returns the parsed RuntimeSkill, and the database gets a new generation value when the save succeeds.

**Call relations**: This is the main write path for user-created skills. During the save it calls _save_lock_key to serialize competing saves, _count to enforce the total skill cap, and _pinned_count to enforce the pinned-skill cap. If a rule is broken, it raises a specific error such as InvalidSkillName, StaleSkillGeneration, SkillCollidesWithCoreSkill, TooManyUserSkills, or PinnedSkillLimit instead of writing unsafe data.

*Call graph*: calls 3 internal fn (_count, _pinned_count, _save_lock_key); 16 external calls (__init__, __init__, __init__, __init__, __init__, __init__, b64encode, sha256, dumps, cast (+6 more)).


##### `UserSkillStore.cards`  (lines 240–281)

```
async def cards(self) -> tuple[SkillCard, ...]
```

**Purpose**: This returns lightweight routing cards for every saved skill in the current workspace. A card contains enough information for the system to decide which skills are available without loading every file.

**Data flow**: It reads the current workspace ID, queries the database for each skill's name, description, dependencies, agents, and pin status, then turns each usable row into a SkillCard. Rows with an empty description are skipped and logged. The result is a tuple of SkillCard objects.

**Call relations**: This is used when the system needs the workspace's skill catalog. It does not call the heavier file-loading functions, which means a damaged stored bundle does not stop the card list from being built; corruption is left to show up when that exact skill is loaded.

*Call graph*: 4 external calls (__init__, loads, select, agent_current).


##### `UserSkillStore.listing`  (lines 283–307)

```
async def listing(self) -> tuple[SkillListing, ...]
```

**Purpose**: This returns the simple list shown for saved user skills: name, description, and whether each skill is pinned. It is meant for object listings or workspace views rather than running a skill.

**Data flow**: It reads the current workspace ID, fetches matching database rows ordered by name, filters out rows whose description is empty, and wraps each remaining row in a SkillListing object. The output is a tuple of those listing objects and the database is unchanged.

**Call relations**: This is the display-oriented companion to cards. Like cards, it tolerates rows with missing descriptions by skipping them, so one bad backfilled row does not spoil the whole list.

*Call graph*: 3 external calls (__init__, select, agent_current).


##### `UserSkillStore.record`  (lines 309–340)

```
async def record(self, name: str) -> SkillRecord | None
```

**Purpose**: This loads the full stored record for one named skill, including its files, version generation, pin state, and timestamps. It is the read path a caller uses before editing, because it supplies the generation needed for a safe later save.

**Data flow**: It receives a skill name, reads the current workspace ID, and looks up that one row in the database. If no row exists, it returns None. If it finds one, it validates the stored JSON, decodes each base64 file back into bytes, and returns a SkillRecord with the files and metadata.

**Call relations**: This function supports detailed reads of a single skill. Its generation value feeds back into UserSkillStore.save, where that value acts as the version stamp that prevents accidental overwrites.

*Call graph*: 4 external calls (__init__, b64decode, select, agent_current).


##### `UserSkillStore.materialize`  (lines 342–349)

```
async def materialize(self, name: str) -> RuntimeSkill | None
```

**Purpose**: This turns one saved skill back into a RuntimeSkill, which is the parsed form the system can actually use. It is for loading a specific named skill when the caller expects it to be valid.

**Data flow**: It receives a skill name and asks UserSkillStore.files for the saved file bytes. If no files are found, it returns None. Otherwise it passes the name and files to parse_skill_content and returns the resulting RuntimeSkill.

**Call relations**: This is a small bridge between raw storage and usable skill behavior. It delegates file retrieval to UserSkillStore.files, then hands the bytes to the shared skill parser so the loaded skill is interpreted the same way as a freshly saved one.

*Call graph*: calls 1 internal fn (files); 1 external calls (parse_skill_content).


##### `UserSkillStore.materialize_all`  (lines 351–379)

```
async def materialize_all(self) -> tuple[RuntimeSkill, ...]
```

**Purpose**: This loads every saved workspace skill into RuntimeSkill form. It is built to be tolerant: if one stored skill is corrupt, it logs the failure and still returns the rest.

**Data flow**: It reads all skill names and stored content for the current workspace from the database. For each row, it validates the JSON, decodes base64 text back into file bytes, parses the skill, and appends it to the result list. If one row fails during decoding or parsing, that row is skipped and a warning is logged. The output is a tuple of successfully loaded RuntimeSkill objects.

**Call relations**: This is the bulk-loading path for workspace skills. Unlike UserSkillStore.materialize, which fails loudly for one named skill, this function favors keeping the workspace usable even if a single saved bundle is bad.

*Call graph*: 4 external calls (b64decode, select, agent_current, parse_skill_content).


##### `UserSkillStore.files`  (lines 381–396)

```
async def files(self, name: str) -> dict[str, bytes] | None
```

**Purpose**: This returns the raw files for one saved skill as bytes. It is useful when another part of the system wants the stored folder contents without also asking for database metadata.

**Data flow**: It receives a skill name, reads the current workspace ID, and fetches the stored content field for that skill. If there is no row, it returns None. Otherwise it validates the stored JSON and decodes each base64 string back into its original bytes, returning a dictionary of path to file bytes.

**Call relations**: UserSkillStore.materialize calls this first when it needs to parse a saved skill into a RuntimeSkill. This function is the focused 'get me the files' step underneath that higher-level load.

*Call graph*: called by 1 (materialize); 3 external calls (b64decode, select, agent_current).


##### `UserSkillStore.delete`  (lines 398–420)

```
async def delete(self, name: str) -> None
```

**Purpose**: This removes one saved skill from the current workspace and, when search indexing is available, removes that skill's indexed data too. It is careful about order so a crash is less likely to leave confusing leftover search chunks.

**Data flow**: It receives a skill name and reads the current workspace ID. If an index service exists, it first marks the database row as needing re-index attention by clearing its indexed digest, then asks the index to delete the skill's index scope. Finally it deletes the skill row from the database. It returns nothing.

**Call relations**: This is the destructive path for user skills. It uses IndexScope to tell the index exactly which skill-owned search data should be removed, then uses database delete logic to remove the persisted skill itself.

*Call graph*: 4 external calls (__init__, delete, update, agent_current).


##### `UserSkillStore._count`  (lines 422–430)

```
async def _count(self, connection: AsyncConnection) -> int
```

**Purpose**: This counts how many user skills are currently saved in the workspace. It exists so saving a new skill can enforce the workspace-wide skill limit.

**Data flow**: It receives an open database connection and reads the current workspace ID. It asks the database to count rows in the user_skill table for that workspace and returns the count as an integer. It does not change any data.

**Call relations**: UserSkillStore.save calls this only when it is creating a new skill. The count decides whether the save would exceed MAX_USER_SKILLS_PER_WORKSPACE and should therefore be rejected.

*Call graph*: called by 1 (save); 3 external calls (execute, select, agent_current).


##### `UserSkillStore._pinned_count`  (lines 432–444)

```
async def _pinned_count(self, connection: AsyncConnection, excluding: str) -> int
```

**Purpose**: This counts how many other skills are pinned in the workspace. It helps enforce the maximum number of pinned user skills while allowing an already pinned skill to be re-saved.

**Data flow**: It receives an open database connection and the name of a skill to exclude from the count. It reads the current workspace ID, counts pinned rows in that workspace whose name is not the excluded name, and returns that number. It does not modify the database.

**Call relations**: UserSkillStore.save calls this when a save requests a pinned skill that was not already pinned. The result determines whether adding this pin would go over MAX_PINNED_USER_SKILLS and should be refused.

*Call graph*: called by 1 (save); 3 external calls (execute, select, agent_current).

## 📊 State Registers Touched

- `reg-database-schema-version` — The database migration state that records which durable tables and columns the running code can rely on.
- `reg-workspace-records` — The saved workspace records that identify each customer space and hold its limits, setup state, balance settings, and routing boundaries.
- `reg-member-identity` — The shared record of who each user is, how they logged in, what workspace they belong to, and what timezone or invitation state is known.
- `reg-agent-registry` — The durable list of agents, including their names, visibility, owners, purposes, model behavior, provisioning source, and tool policy.
- `reg-conversation-turn-state` — The conversation and turn queue state that tracks each unit of agent work from admission through running, completion, cancellation, or recovery.
- `reg-inbound-message-log` — The saved incoming-message log that keeps outside chat, terminal, web, and scheduled events ordered, unique, and ready to become turns.
- `reg-surface-routing` — The shared mapping from external surfaces such as Slack, iMessage, web, terminal, and hosted sites to the right workspace, agent, and conversation.
- `reg-transcript-history` — The saved conversation transcript, including compacted summaries and durable final results that later stages read instead of relying on memory.
- `reg-runtime-fleet-liveness` — The heartbeat and listener-claim records that show which long-running service instances are alive and what work they currently own.
- `reg-workflow-claims` — The workflow attempt and run-claim state that prevents two workers from running the same turn, scheduled task, listener, or cleanup job at once.
- `reg-cancellation-state` — The shared brake state that marks work as stopping or cancelled so model calls, tools, workflows, and retries do not continue stale work.
- `reg-credentials-and-grants` — The encrypted secrets, account connections, and grants that say which member or agent may use an outside service.
- `reg-sandbox-handles` — The remembered sandbox or workspace handle for each conversation so tools can resume the same isolated files, terminals, browsers, and services.
- `reg-billing-ledger` — The usage ledger, spend caps, price versions, exports, and prepaid balance records used to meter and charge workspace activity.
- `reg-observability-traces` — The shared logs, metrics, traces, traceparent links, and safety-filtered operator views used to understand what the system is doing.
- `reg-object-store` — The workspace object records and change journal for agents, members, files, credentials, sites, connectors, memory records, reports, and extension objects.
- `reg-audience-visibility` — The saved visibility and audience rules that decide who may see a conversation, transcript, agent, source, artifact, or object.
- `reg-artifact-blob-store` — The shared file, blob, attachment, artifact, signed download, and media-preview storage used to publish and recover produced work.
- `reg-source-page-sync-state` — The source and page records that remember connected feeds, cursors, backoff, deletes, ownership, grants, and the latest synced content.
- `reg-index-memory-store` — The searchable index and long-term memory store built from synced pages, embeddings, recalled facts, and deduplicated notes.
- `reg-scheduled-jobs` — The durable background-job state for scheduled tasks, pauses, monitor checks, report writing, thumbnail repair, product metrics, and self-improvement runs.
- `reg-subagent-tasks` — The parent-child delegation state that tracks spawned helper agents, their inputs, outputs, names, costs, and undelivered results.
- `reg-skill-store` — The saved and selected skills that can be provisioned by packs, loaded into agent sandboxes, or created by users inside a workspace.
- `reg-hosted-site-registry` — The hosted-site records that remember who owns each site, which conversation created it, where it runs, and how previews or sharing are allowed.
- `reg-prompt-and-delivery-policy` — The prompt, delivery-rule, compaction, and prompt-change proposal state that controls what instructions are rendered and how replies should be shaped.
- `reg-extension-data-store` — The per-workspace extension storage area where optional features save their own small durable JSON state.
- `reg-service-connection-pools` — Process-local shared connection/client pools for database, Redis/pubsub, HTTP/provider calls, and other long-lived service clients reused by requests, workers, tools, and jobs.
- `reg-surface-delivery-outbox` — Durable outgoing surface delivery state for final replies, mid-turn replies, writebacks, claims, retries, and de-duplication across Slack, iMessage, web, terminal, and other surfaces.
- `reg-agent-setup-state` — Durable setup checklist and progress state for provisioned agents, including required account links, credentials, schedules, and extension-specific onboarding needs.
- `reg-conversation-workspace-changes` — Durable git-like summaries of files added, edited, or removed inside a conversation workspace during a turn.
- `reg-objectives-plan-store` — Durable objective, plan, step, evidence, and blocker records used by multi-step agent workflows and objective-tracking extensions.
- `reg-evaluation-feedback-store` — Saved evaluation and self-improvement state, including test cases from failures, replay outputs, judge results, prompt-candidate gates, and promotion decisions.
- `reg-security-audit-log` — Durable audit records for sensitive reads and administrative/object changes, such as transcript access and object-change journaling.
- `reg-human-question-state` — Pending human-question and answer state used when tools or workflows ask a member for input and later resume the affected turn.
- `reg-turn-created-reference-index` — Durable per-turn list of objects, artifacts, sites, files, or other references created during a turn for later transcript display, panels, delivery, and recovery.
- `reg-durable-workflow-store` — Serialized durable workflow/checkpoint objects used to reload or resume long-running turns, jobs, and recovery work after crashes or code changes.
- `reg-rendered-prompt-audit` — Per-turn rendered prompt metadata, slot validation results, and prompt digests used to trace or replay the exact model prompt later.
