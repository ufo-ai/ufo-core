# Persistence, Schema Contracts, and Pagination  `stage-21` (cross-cutting infrastructure)

This stage is the system’s shared storage foundation. It works behind the scenes during startup, normal work, background jobs, and migrations, so every part of the product agrees on how data is shaped, saved, and read. The central blueprint is schema/tables.py, which defines the database tables and rules, while db.py is the guarded doorway that opens database connections, runs migrations, wraps changes in transactions, and keeps each workspace’s data separate. schema/records.py defines common record formats for turns, agents, requests, billing, icons, and terminal results; schema/__init__.py simply makes those schema modules importable.

Conversation history has two layers. turns/transcript.py defines the agreed file format for saved conversations and compaction records, and loop/transcript.py reads and writes those transcripts safely in shared blob storage without letting older state overwrite newer state. blob.py stores raw files either locally or in cloud storage while keeping workspace files isolated. listings.py provides cursor-based paging, like bookmarks in a long list, so portals can move older or newer without missing items. durability.py helps saved workflow objects survive software changes across versions.

## Files in this stage

### Conversation storage and blobs
Defines the saved conversation formats and the shared binary storage layer used to persist transcript-related data safely across local and cloud backends.

### `core/src/ufo/turns/transcript.py`

`data_model` · `cross-cutting transcript persistence and readback`

This file is the common rulebook for durable conversation history. A conversation is saved as a compressed JSON blob: JSON is a plain text data format, and LZ4 is a fast compression format that makes the stored bytes smaller. Without this shared rulebook, one part of the system could write a transcript in one shape while another part tried to read it in a different shape, causing old conversations, debug views, or evaluation runs to break.

The main record is `Conversation`, which stores the current message window, its sequence number, and optional extra context such as the system prompt and injected text that the model saw. The file also defines the records used when a long conversation is compacted. Compaction is like replacing a pile of notes with a careful summary plus the most recent pages, so the model can keep working without carrying the full past conversation.

The compaction models describe what was summarized, what messages existed before and after, what files or facts mattered, and whether important facts survived the summary. The helper functions build predictable blob-store paths, compress records before saving, decompress and validate records after loading, and return `None` when a requested compaction record does not exist. If bytes are corrupt or no longer match the expected shape, the file raises `TranscriptDecodeError` so callers get one clear kind of failure.

#### Function details

##### `transcript_key`  (lines 37–38)

```
def transcript_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the exact storage path for a conversation transcript. Someone uses it when they need every part of the system to look for the same saved conversation blob in the blob store.

**Data flow**: It takes a conversation ID, which is a unique identifier, and inserts it into a fixed path pattern. The result is a string such as a folder-and-file name where the compressed transcript belongs.

**Call relations**: This is the shared naming rule for transcript blobs. Writers and readers can independently call it and still meet at the same storage location.


##### `encode`  (lines 41–43)

```
def encode(conversation: Conversation) -> bytes
```

**Purpose**: Turns a validated `Conversation` object into compressed bytes that can be saved. It is used before storing a transcript so the durable copy is compact and consistently formatted.

**Data flow**: It receives a `Conversation`, converts it into ordinary JSON-ready data, writes that as compact JSON text, changes the text into bytes, and compresses those bytes with LZ4. The output is the compressed byte string that can be put into storage.

**Call relations**: This is the write-side codec for transcripts. It relies on the `Conversation` model’s dump method to get a clean data shape, then uses JSON encoding before compression.

*Call graph*: 2 external calls (model_dump, dumps).


##### `decode`  (lines 46–50)

```
def decode(body: bytes) -> Conversation
```

**Purpose**: Turns compressed stored transcript bytes back into a validated `Conversation`. It protects callers from bad or outdated stored data by wrapping decode failures in a clear transcript-specific error.

**Data flow**: It receives compressed bytes from storage, decompresses them, and asks the `Conversation` schema to validate the JSON inside. If everything matches, it returns a `Conversation`; if decompression or validation fails, it raises `TranscriptDecodeError`.

**Call relations**: This is the read-side partner to `encode`. Any reader of transcript blobs can use it to get a safe, typed conversation instead of manually unpacking bytes.

*Call graph*: 1 external calls (__init__).


##### `compaction_key`  (lines 136–137)

```
def compaction_key(conversation_id: UUID, index: int, half: CompactionHalf) -> str
```

**Purpose**: Builds the exact storage path for one piece of a compaction record. A compaction has separate saved parts: the window before compaction, the window after compaction, and the summary.

**Data flow**: It takes a conversation ID, a compaction index, and which half or section is wanted. It returns the fixed blob-store path for that specific compressed JSON file.

**Call relations**: The compaction read helpers call this before fetching from the blob store. It keeps all compaction readers and writers using the same folder layout.

*Call graph*: called by 2 (read_compaction_after, read_compaction_record).


##### `decode_compaction`  (lines 140–149)

```
def decode_compaction(index: int, before: bytes, after: bytes, summary: bytes) -> CompactionRecord
```

**Purpose**: Rebuilds a full `CompactionRecord` from its three compressed stored pieces. It is used when a caller wants the before window, after window, and summary together.

**Data flow**: It receives an index plus three compressed byte strings: before, after, and summary. It decompresses and validates each one, pulls out the message windows and typed summary, and returns one `CompactionRecord`. If any piece is unreadable or has the wrong shape, it raises `TranscriptDecodeError`.

**Call relations**: `read_compaction_record` fetches the three stored blobs and then hands them to this function. This function is the place where raw compaction bytes become a trustworthy in-memory record.

*Call graph*: called by 1 (read_compaction_record); 2 external calls (__init__, __init__).


##### `read_compaction_after`  (lines 152–165)

```
async def read_compaction_after(blob: BlobStore, conversation_id: UUID, index: int) -> tuple[Message, ...] | None
```

**Purpose**: Fetches only the “after” window for one compaction, if it exists. This is useful when a reader needs the compacted replacement window without paying to load the larger full record.

**Data flow**: It receives a blob store, a conversation ID, and a compaction index. It builds the path for the `after` blob, asks the blob store for the bytes, returns `None` if the blob is missing, or decompresses and validates the window and returns its messages. Bad bytes become `TranscriptDecodeError`.

**Call relations**: It calls `compaction_key` to find the right blob and `BlobStore.get` to retrieve it. It stands as a lightweight read path alongside the fuller `read_compaction_record` flow.

*Call graph*: calls 2 internal fn (get, compaction_key); 1 external calls (__init__).


##### `read_compaction_record`  (lines 168–179)

```
async def read_compaction_record(blob: BlobStore, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Fetches one complete compaction record from storage, or reports that it is absent. Debugging tools, evaluation code, and other readers use this when they need the full story of a compaction boundary.

**Data flow**: It receives a blob store, a conversation ID, and an index. It builds and fetches the `before`, `after`, and `summary` blobs; if any expected blob is missing, it returns `None`. If all are present, it passes the bytes to `decode_compaction` and returns the resulting `CompactionRecord`.

**Call relations**: It calls `compaction_key` for each stored piece and `BlobStore.get` to fetch them. `read_compaction_records` repeatedly calls this function while walking through a conversation’s compaction history.

*Call graph*: calls 3 internal fn (get, compaction_key, decode_compaction); called by 1 (read_compaction_records).


##### `read_compaction_records`  (lines 182–192)

```
async def read_compaction_records(blob: BlobStore, conversation_id: UUID) -> tuple[CompactionRecord, ...]
```

**Purpose**: Loads all compaction records for a conversation in order from oldest to newest. It is the convenient “give me the whole compaction history” helper.

**Data flow**: It starts at compaction index 1 and repeatedly asks for the next record. Each record found is added to a list; the first missing index means there are no more records. It returns the collected records as an immutable tuple.

**Call relations**: It builds on `read_compaction_record` instead of fetching blobs itself. This keeps the loop simple: ask for record 1, then 2, then 3, and stop when the lower-level reader says the next record is not there.

*Call graph*: calls 1 internal fn (read_compaction_record).


### `core/src/ufo/blob.py`

`io_transport` · `cross-cutting storage during request handling, background work, asset publishing, and teardown`

A “blob” here means a named pile of bytes: an uploaded file, a generated artifact, a cached web asset, or a saved compaction record. This file hides where those bytes actually live. In development they can be ordinary files under a directory. In deployment they can be objects in an S3 bucket, which is cloud storage addressed by string keys.

The central idea is the BlobStore protocol: callers ask to put, get, stream, delete, check, or list bytes by key. Concrete stores then do the real work. FilesystemBlobStore turns keys into safe paths under one root directory and writes through temporary files so half-written data is not left behind. S3BlobStore talks to S3 asynchronously, streams large files in chunks, and can create temporary signed URLs so another process can upload or download one object without receiving broad storage credentials.

Two wrapper stores add safety rails. WorkspaceBlobStore automatically prefixes every key with the current workspace, like putting each tenant’s files in its own labeled drawer. FleetBlobStore allows only a few shared prefixes for deployment-owned data, such as static assets and terminal payloads. Without these wrappers, a mistaken key could read or overwrite data in the wrong namespace.

#### Function details

##### `BlobStore.put`  (lines 54–54)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Defines the standard promise that a blob store can save a complete byte string under a key. Code that publishes assets can rely on this without caring whether the final storage is disk or S3.

**Data flow**: A caller provides a key and bytes → the concrete store writes those bytes to its backing storage → nothing is returned, but the key should now name the saved data.

**Call relations**: This is part of the shared blob-store contract. Asset publishing code calls it through that contract, and the actual behavior is supplied by stores such as FilesystemBlobStore, S3BlobStore, WorkspaceBlobStore, or FleetBlobStore.

*Call graph*: called by 1 (_publish_assets).


##### `BlobStore.get`  (lines 56–56)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Defines the standard promise that a blob store can read a whole object into memory. It is used when callers need the complete stored value, such as a transcript record, Slack identity, or web asset.

**Data flow**: A caller provides a key → the concrete store looks up the bytes behind that key → the full byte content comes back, or a missing-key error is raised by implementations.

**Call relations**: Transcript reading, Slack identity reading, and web asset serving call this abstract operation. The call is then carried by whichever concrete store has been configured.

*Call graph*: called by 4 (read_compaction_after, read_compaction_record, read_identity, _stored_asset).


##### `BlobStore.exists`  (lines 58–58)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Defines the standard promise that callers can ask whether a key currently has stored bytes. This lets code avoid unnecessary uploads or decide whether cached data can be reused.

**Data flow**: A caller provides a key → the concrete store checks its backing storage → it returns true if an object exists there and false if not.

**Call relations**: Slack and web surface code call this through the common blob-store shape before reading or publishing stored data. Concrete stores perform the actual disk or S3 check.

*Call graph*: called by 3 (read_identity, _publish_assets, _stored_asset).


##### `BlobStore.delete`  (lines 60–62)

```
async def delete(self, key: str) -> None
```

**Purpose**: Defines the standard promise that a blob can be removed. Deleting something that is already absent is intentionally harmless, which makes retries safe.

**Data flow**: A caller provides a key → the concrete store asks its backing storage to remove that object → no value is returned, and absence is treated as success.

**Call relations**: This is a contract method for all blob stores. Higher-level code can call delete on any configured store and get the same no-surprises behavior.


##### `BlobStore.get_stream`  (lines 64–64)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines the standard promise that a blob can be read piece by piece instead of all at once. This matters for large files, where loading everything into memory would be wasteful or unsafe.

**Data flow**: A caller provides a key → the concrete store opens the stored object → chunks of bytes are yielded over time until the object is fully read.

**Call relations**: This belongs to the common storage interface. Concrete stores implement it using file reads or S3 response streaming, and wrapper stores add namespace checks before delegating.


##### `BlobStore.put_stream`  (lines 66–66)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Defines the standard promise that callers can write a blob from a stream of chunks. This lets the system accept large uploads without first collecting the entire file in memory.

**Data flow**: A caller provides a key and an asynchronous stream of byte chunks → the concrete store writes each chunk to storage → nothing is returned after the full stream is saved.

**Call relations**: This is used through the blob-store interface when data arrives gradually. Filesystem and S3 implementations handle the storage details, including temporary files or multipart upload.


##### `BlobStore.list`  (lines 68–72)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Defines the standard promise that callers can list stored objects below a required key prefix. The prefix requirement prevents accidental scans of the entire storage area.

**Data flow**: A caller provides a prefix → the concrete store finds matching objects up to a fixed limit → it returns small records describing each object’s key, size, and last modified time.

**Call relations**: Web asset publishing calls this through the common interface. Concrete stores translate the request into a filesystem walk or an S3 listing.

*Call graph*: called by 1 (_publish_assets).


##### `FilesystemBlobStore.put`  (lines 81–86)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves a complete byte string as a file under the configured blob root. It writes to a temporary file first and then swaps it into place, so readers do not see half-written data.

**Data flow**: A key and bytes come in → the key is resolved to a safe path, parent folders are created, bytes are written to a uniquely named temporary file, and that file replaces the final path → the stored file now contains the new bytes.

**Call relations**: This is the filesystem implementation of the BlobStore put operation. It relies on _resolve to keep the path inside the storage root and uses background threads for blocking file work so the async event loop stays responsive.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.get`  (lines 88–93)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a complete stored file from the filesystem. It turns a normal missing-file condition into BlobNotFound so callers see the same kind of error across backends.

**Data flow**: A key comes in → the key is resolved to a safe path and the file is read as bytes → the bytes are returned, or BlobNotFound is raised if the file is absent.

**Call relations**: This is the filesystem version of the BlobStore get operation. It depends on _resolve for safety and is used anywhere the configured backend is local disk.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.exists`  (lines 95–97)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a key points to an existing file inside the filesystem blob root. It gives callers a quick yes-or-no answer without reading the file.

**Data flow**: A key comes in → it is converted to a safe path → the filesystem is asked whether that path is a file → true or false is returned.

**Call relations**: This implements the BlobStore exists operation for local storage. Like other file operations, it goes through _resolve and runs the blocking filesystem check outside the main async path.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.delete`  (lines 99–101)

```
async def delete(self, key: str) -> None
```

**Purpose**: Removes a stored file from the filesystem if it exists. If it is already gone, the function still succeeds.

**Data flow**: A key comes in → it is resolved to a safe path → the file is unlinked with missing files ignored → nothing is returned.

**Call relations**: This is the local-disk version of BlobStore.delete. It uses _resolve before touching the filesystem so a key cannot delete outside the blob root.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.get_stream`  (lines 103–116)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads a stored file in bounded chunks. This lets callers send or process large blobs without holding the whole file in memory.

**Data flow**: A key comes in → the safe file path is opened for reading → chunks are read and yielded until the file ends → the file handle is closed afterward, even if reading stops early.

**Call relations**: This implements the streaming read part of the BlobStore contract for local files. It uses _resolve for containment and raises BlobNotFound when opening the file fails because it is missing.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.put_stream`  (lines 118–131)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed byte chunks to a local file safely. Like the whole-file writer, it writes to a temporary file first so a failed stream does not leave a broken final object.

**Data flow**: A key and a stream of chunks come in → the safe path and parent folders are prepared → chunks are written into a temporary file → on success the temporary file replaces the target, and on failure it is removed.

**Call relations**: This is the filesystem implementation of BlobStore.put_stream. It uses _resolve and temporary filenames, while the actual blocking file writes happen in worker threads.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.list`  (lines 133–136)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists local files whose keys begin with a required prefix. Requiring a prefix keeps callers from accidentally walking the whole blob store.

**Data flow**: A prefix comes in → empty prefixes are rejected → the slower directory walk is run away from the event loop → a tuple of matching BlobEntry records comes back.

**Call relations**: This is the filesystem version of BlobStore.list. It hands the real walking work to _walk so the async method stays small and non-blocking.

*Call graph*: 1 external calls (to_thread).


##### `FilesystemBlobStore._walk`  (lines 138–159)

```
def _walk(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Performs the actual filesystem scan for list. It turns matching files into BlobEntry records and ignores temporary write files.

**Data flow**: A prefix comes in → the store root and starting directory are found → files under that area are walked, filtered by prefix, measured, timestamped, sorted, and capped → a tuple of entries is returned.

**Call relations**: FilesystemBlobStore.list runs this in a background thread. It depends on _contained_root and _resolve to know the safe root and starting path, then produces the entries that list returns.

*Call graph*: calls 2 internal fn (_contained_root, _resolve); 4 external calls (__init__, fromtimestamp, walk, Path).


##### `FilesystemBlobStore._resolve`  (lines 161–166)

```
def _resolve(self, key: str) -> Path
```

**Purpose**: Turns a blob key into a real filesystem path while preventing path escape. This is the guardrail that stops keys like '../secret' from reaching outside the blob directory.

**Data flow**: A key comes in → it is joined to the canonical blob root and resolved → if the result is not inside the root, an error is raised → otherwise the safe path is returned.

**Call relations**: Every filesystem read, write, delete, stream, and walk uses this before touching disk. It calls _contained_root to get the trusted root for the current operation.

*Call graph*: calls 1 internal fn (_contained_root); called by 7 (_walk, delete, exists, get, get_stream, put, put_stream).


##### `FilesystemBlobStore._contained_root`  (lines 168–181)

```
def _contained_root(self) -> Path
```

**Purpose**: Finds the real storage root directory in a way that matches the project’s containment checks. It allows a not-yet-created root, but rejects configured paths that point somewhere invalid.

**Data flow**: The configured root path is read from the store → containment validation is attempted → a canonical directory path is returned, or a not-yet-existing root is resolved for first use.

**Call relations**: _resolve and _walk call this whenever they need the safe filesystem root. It delegates the main validation to the sandbox containment helper configured_root.

*Call graph*: called by 2 (_resolve, _walk); 1 external calls (configured_root).


##### `_is_missing_key`  (lines 184–185)

```
def _is_missing_key(error: ClientError) -> bool
```

**Purpose**: Recognizes the different error codes S3-like services use for a missing object. This lets the rest of the code treat those variants as one simple “not found” case.

**Data flow**: An S3 client error comes in → the error code is read from its response data → true is returned if it matches a known missing-object code, otherwise false.

**Call relations**: S3BlobStore.get, exists, and get_stream call this when the S3 client raises an error. It decides whether the store should return false, raise BlobNotFound, or pass the original error upward.

*Call graph*: called by 3 (exists, get, get_stream).


##### `S3BlobStore.put`  (lines 207–209)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves a complete byte string as one object in an S3 bucket. It is the cloud-storage equivalent of writing a whole local file.

**Data flow**: A key and bytes come in → an S3 client for the current event loop is obtained → the bytes are sent to S3 under the bucket and key → nothing is returned after S3 accepts the write.

**Call relations**: This implements BlobStore.put for S3. It relies on _client so callers do not pay the cost of building a new S3 client for every operation.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get`  (lines 211–221)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a complete object from S3 into memory. Missing objects are reported as BlobNotFound so callers do not need to understand S3’s error formats.

**Data flow**: A key comes in → an S3 client requests the object → if S3 says it is missing, BlobNotFound is raised → otherwise the response body is read fully and returned as bytes.

**Call relations**: This is the S3 implementation of BlobStore.get. It uses _client for the shared client and _is_missing_key to translate S3 missing-object errors.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.exists`  (lines 223–231)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether an object exists in S3 without downloading it. It uses S3’s metadata lookup as a lightweight presence test.

**Data flow**: A key comes in → an S3 client sends a head-object request → missing-object errors become false, other errors are re-raised, and success becomes true.

**Call relations**: This implements BlobStore.exists for S3. It uses _client to talk to S3 and _is_missing_key to recognize the normal absent-key case.

*Call graph*: calls 2 internal fn (_client, _is_missing_key).


##### `S3BlobStore.delete`  (lines 233–235)

```
async def delete(self, key: str) -> None
```

**Purpose**: Asks S3 to remove an object from the bucket. Like the blob-store contract expects, deleting an absent object is not treated as a special problem here.

**Data flow**: A key comes in → an S3 client is obtained → a delete request is sent for that bucket and key → nothing is returned.

**Call relations**: This is the S3 version of BlobStore.delete. It shares the event-loop-specific client supplied by _client.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get_stream`  (lines 237–248)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads an S3 object in chunks instead of all at once. This is important for large files because it keeps memory use bounded.

**Data flow**: A key comes in → S3 is asked for the object → missing-object errors become BlobNotFound → the response body yields chunks until complete, then the body is closed.

**Call relations**: This implements BlobStore.get_stream for S3. It uses _client for access and _is_missing_key for error translation.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.put_stream`  (lines 250–294)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed data to S3, using multipart upload for larger content. Multipart upload means S3 receives the object in numbered pieces and then assembles them at the end.

**Data flow**: A key and chunk stream come in → chunks are gathered into part-sized buffers → small data is uploaded as one object, while larger data starts a multipart upload, sends each part, and completes it → on failure any started multipart upload is aborted.

**Call relations**: This is the S3 implementation of BlobStore.put_stream. It gets the shared S3 client through _client and then chooses between simple upload and multipart upload based on how much data arrives.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_put`  (lines 296–320)

```
async def presigned_put(self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary URL that lets another process upload exactly one measured object to S3. The URL is tied to the key, expected byte length, checksum, and expiry time.

**Data flow**: A key, size, checksum, and time-to-live come in → the S3 client signs a put-object request with those constraints → a URL string is returned for the uploader to use.

**Call relations**: WorkspaceBlobStore.presigned_put delegates here when its backend is S3. This function uses _client so the signing settings match the S3 client that will accept the upload.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_put_unmeasured`  (lines 322–332)

```
async def presigned_put_unmeasured(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary upload URL for a fixed key when the final size and checksum are not known yet. It is less strict than presigned_put, but still only grants access to one key for a limited time.

**Data flow**: A key and expiry time come in → the S3 client signs a put-object request for that key only → a URL string is returned.

**Call relations**: WorkspaceBlobStore.presigned_put_unmeasured delegates here for S3-backed work. It uses _client for the signing operation.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_get`  (lines 334–341)

```
async def presigned_get(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary URL that lets a holder download one S3 object. This is useful when another component needs direct access without receiving storage credentials.

**Data flow**: A key and expiry time come in → the S3 client signs a get-object request → a URL string is returned.

**Call relations**: WorkspaceBlobStore.presigned_get delegates here when the backend is S3. The function uses _client so the generated URL follows the same endpoint and signing rules as other S3 operations.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.put_host`  (lines 343–354)

```
async def put_host(self) -> str
```

**Purpose**: Reports the hostname that presigned upload URLs will contact. The system can use this to allow a sandbox to reach exactly the storage host it needs.

**Data flow**: The store’s S3 client is obtained → its endpoint URL is parsed → the correct hostname is returned, including the bucket name for normal AWS virtual-hosted addressing when needed.

**Call relations**: Code that prepares network egress rules can call this after S3 signing is configured. It depends on _client and parses the client’s actual endpoint rather than rebuilding it by hand.

*Call graph*: calls 1 internal fn (_client); 1 external calls (urlsplit).


##### `S3BlobStore.list`  (lines 356–373)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists objects in S3 under a required prefix and returns their keys, sizes, and modification times. The fixed cap prevents very large listings from overwhelming callers.

**Data flow**: A prefix comes in → empty prefixes are rejected → S3 list pages are fetched for that prefix → each returned object becomes a BlobEntry → entries are capped and returned.

**Call relations**: This implements BlobStore.list for S3. It gets the shared client through _client and uses S3 pagination to walk matching objects page by page.

*Call graph*: calls 1 internal fn (_client); 1 external calls (__init__).


##### `S3BlobStore.close`  (lines 375–381)

```
async def close(self) -> None
```

**Purpose**: Closes the S3 client associated with the currently running async event loop. This releases network resources when that loop is done using the store.

**Data flow**: The current event loop is identified → any cached client and lock for that loop are removed from the store → if a client existed, it is closed.

**Call relations**: This is teardown support for S3BlobStore. It mirrors _client, which creates and caches one client per event loop.

*Call graph*: 1 external calls (get_running_loop).


##### `S3BlobStore._client`  (lines 383–409)

```
async def _client(self) -> AioBaseClient
```

**Purpose**: Returns the shared S3 client for the current async event loop, creating it if needed. Reusing clients avoids expensive setup on every blob operation and keeps network objects tied to the loop they belong to.

**Data flow**: The running event loop is checked → an existing client is returned if present → otherwise a per-loop lock prevents duplicate creation, a configured S3 client is built, cached, and returned.

**Call relations**: All S3 operations call this before talking to S3 or generating signed URLs. It is the central connection point between S3BlobStore’s public methods and aiobotocore, the asynchronous S3 library.

*Call graph*: called by 11 (delete, exists, get, get_stream, list, presigned_get, presigned_put, presigned_put_unmeasured, put, put_host (+1 more)); 3 external calls (get_session, Lock, get_running_loop).


##### `WorkspaceBlobStore.put`  (lines 422–423)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves bytes under the currently bound workspace’s storage prefix. Callers use workspace-relative keys, so they do not have to manually include the workspace ID.

**Data flow**: A workspace-relative key and bytes come in → _full adds the current workspace prefix → the backend store writes the bytes under that full key → nothing is returned.

**Call relations**: This wraps a filesystem or S3 backend. It calls _full first, then hands the actual write to the backend’s put method.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.get`  (lines 425–426)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a complete blob from the current workspace’s namespace. It prevents callers from accidentally reading another workspace by constructing the full key itself.

**Data flow**: A workspace-relative key comes in → _full adds the current workspace prefix → the backend reads that full key → the bytes are returned.

**Call relations**: This is the workspace-safe wrapper around backend get. It depends on _full for isolation and then delegates storage access to the configured backend.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.exists`  (lines 428–429)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a blob exists inside the current workspace. The caller asks with a local key, not a global storage path.

**Data flow**: A workspace-relative key comes in → _full turns it into a full workspace key → the backend checks whether that object exists → true or false is returned.

**Call relations**: This wraps backend exists with workspace prefixing. It calls _full before delegating so the check cannot wander into another namespace.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.delete`  (lines 431–432)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes a blob from the current workspace’s namespace. It keeps deletion scoped to the active workspace.

**Data flow**: A workspace-relative key comes in → _full adds the workspace prefix → the backend deletes the full key → nothing is returned.

**Call relations**: This is the workspace-safe wrapper for backend delete. It relies on _full for the namespace boundary.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.get_stream`  (lines 434–438)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Opens a streaming read for a blob in the current workspace. It resolves the workspace prefix immediately so the stream can keep working even after the workspace scope exits.

**Data flow**: A workspace-relative key comes in → _full immediately builds the full key → the backend stream for that full key is returned → later iteration yields byte chunks from storage.

**Call relations**: Context-building code calls this when it needs member blob text. This method fixes the workspace key first, then hands off to the backend’s get_stream method.

*Call graph*: calls 1 internal fn (_full); called by 1 (_member_blob_text).


##### `WorkspaceBlobStore.put_stream`  (lines 440–441)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed bytes into the current workspace’s namespace. It gives large uploads the same workspace isolation as whole-byte writes.

**Data flow**: A workspace-relative key and chunk stream come in → _full adds the current workspace prefix → the backend writes the stream under that full key → nothing is returned after completion.

**Call relations**: This wraps backend put_stream. It calls _full before delegating so the backend only sees the already-scoped key.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.list`  (lines 443–448)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists blobs under a prefix inside the current workspace, while returning keys back in workspace-relative form. This keeps callers from depending on the internal global prefix.

**Data flow**: A workspace-relative prefix comes in → empty prefixes are rejected → the current workspace root prefix is built → the backend lists full keys below it → each returned entry has the workspace root removed from its key.

**Call relations**: This wraps backend list. It uses _full to find the workspace root, delegates the real listing to the backend, then rewrites entries so callers see local keys.

*Call graph*: calls 1 internal fn (_full); 1 external calls (replace).


##### `WorkspaceBlobStore.presigned_put`  (lines 450–461)

```
async def presigned_put(self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a measured temporary upload URL for a blob inside the current workspace, but only when the backend is S3. This lets an uploader write directly to cloud storage without escaping the workspace prefix.

**Data flow**: A workspace-relative key, size, checksum, and expiry come in → the key is expanded with _full → if the backend is S3, S3BlobStore.presigned_put creates the URL → otherwise a type error is raised.

**Call relations**: This is a workspace-safe wrapper over S3BlobStore.presigned_put. It first scopes the key, then hands signing to the S3 backend.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.presigned_put_unmeasured`  (lines 463–469)

```
async def presigned_put_unmeasured(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary upload URL for a current-workspace object when the final body size is unknown. It works only with the S3 backend.

**Data flow**: A workspace-relative key and expiry come in → _full builds the full workspace key → the S3 backend signs an unmeasured PUT URL → the URL is returned, or a type error is raised for non-S3 storage.

**Call relations**: This wraps S3BlobStore.presigned_put_unmeasured with workspace prefixing. It keeps the direct upload limited to one key under the active workspace.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.presigned_get`  (lines 471–478)

```
async def presigned_get(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary download URL for an object in the current workspace, when storage is S3. The generated URL points only at the prefixed workspace key.

**Data flow**: A workspace-relative key and expiry come in → _full adds the workspace prefix → the S3 backend signs a GET URL → the URL is returned, or a type error is raised if the backend is not S3.

**Call relations**: This is the workspace wrapper around S3BlobStore.presigned_get. It scopes the key first, then delegates URL signing.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore._full`  (lines 480–483)

```
def _full(self, key: str) -> str
```

**Purpose**: Builds the real storage key for the currently active workspace. It also rejects keys that already include the workspace prefix, which prevents double-prefix mistakes.

**Data flow**: A caller’s workspace-relative key comes in → keys already starting with the global workspace prefix are rejected → the current workspace ID is read → the full key workspaces/<id>/<key> is returned.

**Call relations**: Every WorkspaceBlobStore operation calls this before touching the backend. It depends on ws_current, so calls must happen inside a bound workspace scope.

*Call graph*: called by 10 (delete, exists, get, get_stream, list, presigned_get, presigned_put, presigned_put_unmeasured, put, put_stream); 1 external calls (ws_current).


##### `FleetBlobStore.put`  (lines 494–495)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves deployment-owned bytes under one of the allowed fleet prefixes. This is for shared data, not workspace-private data.

**Data flow**: A key and bytes come in → _checked verifies the key starts with an allowed fleet prefix → the backend writes the bytes → nothing is returned.

**Call relations**: This wraps a filesystem or S3 backend for fleet-wide storage. It calls _checked before delegating so writes stay inside the approved shared namespaces.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.get`  (lines 497–498)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a complete deployment-owned blob from an allowed fleet namespace. It refuses keys outside those shared areas.

**Data flow**: A key comes in → _checked validates its prefix → the backend reads the object → bytes are returned.

**Call relations**: This is the fleet-safe wrapper around backend get. It uses _checked as the namespace gate before storage access.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.exists`  (lines 500–501)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a deployment-owned blob exists under an allowed fleet prefix. It blocks checks against workspace or unknown key areas.

**Data flow**: A key comes in → _checked confirms it is in an allowed fleet namespace → the backend checks existence → true or false is returned.

**Call relations**: This wraps backend exists. The prefix check happens first, so the backend is only asked about approved shared keys.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.delete`  (lines 503–504)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes a deployment-owned blob from an allowed fleet namespace. It protects other storage areas from accidental shared-store deletion.

**Data flow**: A key comes in → _checked validates the prefix → the backend deletes the object → nothing is returned.

**Call relations**: This wraps backend delete. It depends on _checked to enforce the fleet namespace boundary.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.get_stream`  (lines 506–507)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a deployment-owned blob from an allowed shared prefix. This supports large shared payloads without loading them all at once.

**Data flow**: A key comes in → _checked validates it → the backend streaming reader is returned → iterating that stream yields byte chunks.

**Call relations**: This wraps backend get_stream. It performs the fleet prefix check before handing the key to the underlying store.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.put_stream`  (lines 509–510)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed bytes into an allowed deployment-owned namespace. It is the large-file version of fleet put.

**Data flow**: A key and chunk stream come in → _checked validates the key prefix → the backend writes the stream → nothing is returned after the stream is saved.

**Call relations**: This wraps backend put_stream. It uses _checked first so streamed writes cannot target workspace-private or unknown areas.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.list`  (lines 512–513)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists deployment-owned blobs below an allowed fleet prefix. It refuses listing requests outside the shared namespaces.

**Data flow**: A prefix comes in → _checked validates that the prefix is allowed → the backend lists matching objects → BlobEntry records are returned.

**Call relations**: This wraps backend list. The prefix check ensures callers cannot use the fleet store to enumerate workspace data.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore._checked`  (lines 515–518)

```
def _checked(self, key: str) -> str
```

**Purpose**: Verifies that a fleet-store key starts with one of the approved shared prefixes. This is the main safety rule separating deployment-owned data from workspace data.

**Data flow**: A key comes in → its start is compared with the allowed prefixes → the key is returned unchanged if allowed, or a ValueError is raised if not.

**Call relations**: Every FleetBlobStore operation calls this before delegating to the backend. It is the guardrail that makes the unprefixed fleet handle safe to expose.

*Call graph*: called by 7 (delete, exists, get, get_stream, list, put, put_stream).


##### `blob_store_for`  (lines 521–533)

```
def blob_store_for(config: BlobConfig) -> FilesystemBlobStore | S3BlobStore
```

**Purpose**: Builds the actual blob storage backend described by configuration. It chooses local filesystem storage or S3 storage and checks that the needed settings are present.

**Data flow**: A BlobConfig comes in → its backend name is inspected → required fields such as root or bucket are checked → a FilesystemBlobStore or S3BlobStore instance is returned.

**Call relations**: Startup or configuration wiring code uses this to turn settings into a usable backend. WorkspaceBlobStore and FleetBlobStore can then wrap the returned store to add namespace rules.

*Call graph*: 2 external calls (__init__, __init__).


### Database access and durability
Provides workspace-scoped database entry points and version-tolerant object persistence for durable workflow replay.

### `core/src/ufo/db.py`

`io_transport` · `startup, request handling, background jobs, migrations, teardown`

This file protects the boundary between workspaces, which are separate customer or project areas sharing one running service. Its main job is to make database access safe by requiring normal code to use `workspace_tx`, a transaction helper that pins the current workspace onto that database transaction. In PostgreSQL, this supports row-level security, meaning the database itself refuses rows from the wrong workspace. If no workspace was set, access fails closed instead of accidentally showing shared data.

The file also keeps database connection pools. A pool is like a small taxi stand for database connections: callers borrow a connection, use it, and return it, instead of creating a new one every time. Because Python event loops cannot safely share async database connections, this file keeps one engine per event loop and database URL.

There is one deliberate exception to workspace-scoped access: `owner_tx`. Background sweeps use it to list work across all workspaces, then switch back into each workspace before reading its real contents.

The file also covers practical operations: checking at startup that the database is reachable, disposing connections during shutdown or tests, running Alembic migrations that update the database schema, and tuning SQLite so local/test databases behave predictably under concurrent writes.

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

`io_transport` · `cross-cutting: database persistence, replay, and recovery`

DBOS stores workflow inputs, step results, and errors in a system database so work can be replayed after a crash or deployment. The tricky part is that the code reading the saved data may not be exactly the same code that wrote it. A normal Python pickle, which is Python’s built-in object-freezing format, can restore a Pydantic model as raw internal data without running the model’s normal validation and default-filling logic. That means a field added later might simply be missing, causing a confusing failure much later during replay.

This file fixes that by creating a custom serializer called `ReplaySafeSerializer`. When it sees a Pydantic `BaseModel`, it saves two things: the model’s class and its current field values. When the object is loaded again, `_rebuild` reconstructs it through Pydantic’s `model_validate`, which is like sending the saved fields back through the model’s front door. New fields can get their current defaults, removed fields can be ignored, and truly required missing fields fail in a clearer place.

The file also provides `replay_safe_client`, the project’s intended way to create a `DBOSClient`. This matters because DBOS records the serializer name with stored rows. If a client is not built with this serializer, it may not decode the stored data correctly.

#### Function details

##### `replay_safe_client`  (lines 27–31)

```
def replay_safe_client(system_database_url: str) -> DBOSClient
```

**Purpose**: Creates a DBOS client that knows how to read and write this project’s replay-safe saved data. Someone uses this instead of constructing `DBOSClient` directly so database rows are recorded and decoded with the right serializer name.

**Data flow**: It takes a system database URL as input. It creates a new `ReplaySafeSerializer`, passes both the URL and serializer into `DBOSClient`, and returns the ready-to-use client. The database connection information stays the same, but the client is now equipped to encode and decode this project’s saved workflow data.

**Call relations**: This is the front door for DBOS client creation in this file. It calls `ReplaySafeSerializer.__init__` to make the serializer and then hands that serializer to the external `dbos.DBOSClient`, so later database reads and writes use the custom replay-safe format.

*Call graph*: 2 external calls (__init__, DBOSClient).


##### `_rebuild`  (lines 34–35)

```
def _rebuild(model_class: type[BaseModel], fields: dict[str, object]) -> BaseModel
```

**Purpose**: Recreates a saved Pydantic model from its class and saved field values. It exists so old stored model data is rebuilt using today’s model rules, including defaults and validation.

**Data flow**: It receives a Pydantic model class and a dictionary of field names to saved values. It asks the model class to validate those fields through `model_validate`, and returns the newly constructed model object. The saved raw field data becomes a normal current-version model instance.

**Call relations**: This function is used indirectly by pickle recordings made by `_ModelPickler.reducer_override`. The pickle stores a reference to `_rebuild` by module and function name, so during deserialization Python calls it to reconstruct Pydantic models safely.


##### `_ModelPickler.reducer_override`  (lines 39–42)

```
def reducer_override(self, obj: object) -> tuple[Callable[..., object], tuple[object, ...]]
```

**Purpose**: Changes how Pydantic models are written into pickle data. Instead of letting pickle freeze the model’s private internal state, it tells pickle to save enough information to rebuild the model cleanly later.

**Data flow**: It receives each object the pickler is about to serialize. If the object is a Pydantic `BaseModel`, it returns instructions saying: save the model’s class and a plain dictionary of its fields, then rebuild it later with `_rebuild`. If the object is not a Pydantic model, it returns `NotImplemented`, which tells pickle to use its normal behavior.

**Call relations**: This method is called by Python’s pickle machinery while `ReplaySafeSerializer.serialize` is dumping data. It hands Pydantic models off to `_rebuild` for future loading, while leaving non-model objects to ordinary pickle behavior.


##### `ReplaySafeSerializer.name`  (lines 48–49)

```
def name(self) -> str
```

**Purpose**: Returns the stable name DBOS uses to label data written with this serializer. This name is important because stored database rows need to be matched with the same decoding method later.

**Data flow**: It takes no outside data beyond the serializer object itself. It returns the constant string `ufo_pickle`. Nothing else is changed.

**Call relations**: DBOS calls this as part of its serializer interface when recording or reading serialized rows. The name connects database records to `ReplaySafeSerializer.serialize` and `ReplaySafeSerializer.deserialize` across future replays.


##### `ReplaySafeSerializer.serialize`  (lines 51–54)

```
def serialize(self, data: object) -> str
```

**Purpose**: Turns a Python object into a text string that can be stored in the database. It uses the custom pickler so Pydantic models are saved in a replay-safe way.

**Data flow**: It receives any Python object. It creates an in-memory byte buffer, uses `_ModelPickler` to pickle the object into that buffer, then base64-encodes the bytes into ordinary UTF-8 text. The result is a database-friendly string containing the saved object.

**Call relations**: DBOS uses this method when it needs to persist workflow data. Inside, it creates `_ModelPickler`, whose `reducer_override` supplies the special Pydantic-model behavior, and it uses `io.BytesIO` and `base64.b64encode` to turn binary pickle data into safe text.

*Call graph*: 3 external calls (__init__, b64encode, BytesIO).


##### `ReplaySafeSerializer.deserialize`  (lines 56–57)

```
def deserialize(self, serialized_data: str) -> object
```

**Purpose**: Turns a stored text string back into the original Python object. For Pydantic models saved by this serializer, loading triggers the safer rebuild path rather than restoring stale internal model state.

**Data flow**: It receives a base64 text string from storage. It decodes that text back into pickle bytes, then asks Python’s pickle loader to reconstruct the object. The output is the restored Python value, with Pydantic models rebuilt through `_rebuild` when the pickle data calls for it.

**Call relations**: DBOS uses this method when reading persisted workflow data during normal operation or replay. It relies on `base64.b64decode` to recover the stored bytes and `pickle.loads` to rebuild objects, which may call `_rebuild` for Pydantic models recorded by `ReplaySafeSerializer.serialize`.

*Call graph*: 2 external calls (b64decode, loads).


### Cursor pagination
Implements stable cursor-based paging for portal lists so rows can be browsed without skips or duplicates.

### `core/src/ufo/listings.py`

`domain_logic` · `request handling`

This file solves a common listing problem: how to show page after page of items while the underlying data may be changing. Instead of using offset paging, which says 'skip the first 20 rows' and can go wrong if a new row appears, it uses keyset paging. That means each page starts from a real item position, like placing a bookmark in a stack of papers.

Every listing is ordered the same way: newest first, using `created_at` and then the row id to break ties. The tie-breaker matters because two rows can have the same timestamp. A cursor stores both pieces, so the next page can start exactly before or after that row.

`ListingCursor` is the bookmark. It can be encoded into a string safe for a query parameter and decoded back. Bad cursor strings raise `MalformedCursor` instead of silently showing the wrong page.

`page_query` prepares a SQLAlchemy database query for one page. SQLAlchemy is the library used to build database queries in Python. It asks for one extra row beyond the requested limit so the code can tell whether another page exists.

`page_of` then turns those raw rows into a `ListingPage`: the visible rows plus optional cursors for moving older or newer. When walking toward newer rows, it temporarily queries in the opposite order for efficiency, then reverses the page back so users still see newest-first results.

#### Function details

##### `ListingCursor.encode`  (lines 42–45)

```
def encode(self) -> str
```

**Purpose**: This turns a cursor into a single text token that can be placed in a URL or request parameter. It records whether the cursor points toward newer or older items, plus the timestamp and item id that mark the page boundary.

**Data flow**: It starts with a `ListingCursor` object containing a creation time, an item id, and a newer-or-older direction. It chooses the word `newer` or `older`, joins that with the timestamp and id using `|`, and returns the resulting string. It does not change anything else.

**Call relations**: This is used when a listing response needs to give the client a navigation token. The matching `ListingCursor.decode` function later reads that token back when the client asks for another page.


##### `ListingCursor.decode`  (lines 48–60)

```
def decode(cls, token: str) -> 'ListingCursor'
```

**Purpose**: This reads a cursor token from a request and turns it back into a `ListingCursor`. It protects the listing from bad or invented tokens by raising `MalformedCursor` when the text does not describe a valid position.

**Data flow**: It receives a text token. It splits the token into direction, timestamp, and item id, checks that the direction is either `newer` or `older`, parses the timestamp, and verifies that the id is a valid UUID, which is a standard unique identifier format. If all parts are valid, it returns a cursor object; if not, it raises `MalformedCursor`.

**Call relations**: A web surface for workspace memory calls this when a user follows a listing link with a cursor. After decoding succeeds, that cursor can be passed into the paging flow; if decoding fails, the caller can report a client error instead of guessing what page was intended.

*Call graph*: called by 1 (workspace_memory); 3 external calls (__init__, fromisoformat, UUID).


##### `page_query`  (lines 74–96)

```
def page_query(query: sa.Select[Any], cursor: ListingCursor | None, limit: int, *, created_at: sa.ColumnElement[datetime], ident: sa.ColumnElement[Any]) -> sa.Select[Any]
```

**Purpose**: This prepares a database query so it returns exactly the slice of rows needed for one page of a listing. It applies the shared newest-first ordering and uses the cursor as a boundary when the user is moving through pages.

**Data flow**: It receives a SQLAlchemy query, an optional cursor, a page size limit, and the two database columns that define the listing position: creation time and id. It orders the query newest-first for normal paging, or oldest-first temporarily when walking toward newer rows. If there is a cursor, it adds a comparison so only rows beyond that cursor are returned. It asks for `limit + 1` rows, so later code can tell whether another page exists.

**Call relations**: Listing code calls this before talking to the database. Its output is still a query, not the final page; after the database returns rows, `page_of` uses the rows and the same cursor direction to build the user-facing page envelope.

*Call graph*: 2 external calls (tuple_, UUID).


##### `page_of`  (lines 99–128)

```
def page_of(rows: Sequence[SourceT], cursor: ListingCursor | None, limit: int, *, render: Callable[[SourceT], RowT], position: Callable[[SourceT], tuple[datetime, str]]) -> ListingPage[RowT]
```

**Purpose**: This turns database rows from `page_query` into a finished listing page. It renders each raw row into the caller's public row shape and adds optional cursors for moving to older or newer pages.

**Data flow**: It receives the rows returned from the database, the cursor that led here, the requested limit, a `render` function for converting raw rows into output rows, and a `position` function for reading each row's timestamp and id. It checks whether an extra row was returned, trims the page to the requested limit, reverses rows if the query had walked toward newer items, and creates boundary cursors from the first and last visible rows. It returns a `ListingPage` containing the rendered rows and whichever navigation cursors truly exist.

**Call relations**: This is the second half of the paging flow after `page_query`. It uses its helper `page_of.at` to create cursors for page boundaries, then returns a `ListingPage` for the calling listing endpoint or service to send onward.

*Call graph*: 1 external calls (__init__).


##### `page_of.at`  (lines 118–120)

```
def at(source: SourceT, *, newer: bool) -> ListingCursor
```

**Purpose**: This small helper creates a `ListingCursor` for one row in the page. It is used to mark the first or last visible row as the place where the next navigation step should begin.

**Data flow**: It receives one source row and a direction flag saying whether the cursor should point toward newer items. It calls the provided `position` function to extract the row's creation time and id, then returns a new `ListingCursor` with those values.

**Call relations**: This helper lives inside `page_of` because it depends on the caller-provided way to read a row's position. `page_of` calls it when building the `older` and `newer` cursors for the final `ListingPage`.

*Call graph*: 1 external calls (__init__).


### Durable transcript I/O
Reads and writes shared blob-backed conversation transcripts while preventing stale states from overwriting newer ones.

### `core/src/ufo/loop/transcript.py`

`io_transport` · `turn completion and repair republishing`

A conversation transcript is the long-lived record of what has happened in a conversation. This file wraps the low-level blob store, which is a place to save and load chunks of data by key, and gives the rest of the system a simple `Transcript` object for one conversation.

The important safety rule here is that each saved conversation has a `seq`, a sequence number that should move forward over time. Think of it like page numbers in a notebook: page 5 should not be replaced by page 4 after page 5 has already been written. Before saving a new transcript, this file reads the current one. If the current saved transcript is at the same or a later sequence number, the write is ignored. That means the first valid write for a sequence stays authoritative, and late or stale work cannot roll the transcript backward.

The file does not decide what a conversation means. It relies on `ufo.turns.transcript` to turn conversation objects into bytes for storage and back again. Its job is to connect that format to the shared storage system and enforce the monotonic sequence guard.

#### Function details

##### `Transcript.read`  (lines 17–22)

```
async def read(self) -> Conversation | None
```

**Purpose**: Reads the saved transcript for this conversation, if one exists. Someone uses this when they need the latest durable conversation record rather than an in-memory copy.

**Data flow**: It starts with the `Transcript` object's blob store and conversation ID. It turns the conversation ID into the storage key, asks the blob store for the saved bytes, and if nothing is found, returns `None`. If bytes are found, it decodes them into a `Conversation` object and returns that.

**Call relations**: This is the lookup step used by `Transcript.write` before saving, so the writer can compare sequence numbers and avoid overwriting newer data. It relies on `transcript_key` to find the right blob and `decode` to translate stored bytes back into a conversation.

*Call graph*: called by 1 (write); 2 external calls (decode, transcript_key).


##### `Transcript.write`  (lines 24–28)

```
async def write(self, conversation: Conversation) -> None
```

**Purpose**: Saves a conversation transcript only if it is newer than what is already stored. This protects the durable conversation history from being replaced by stale results.

**Data flow**: It receives a `Conversation` object to save. First it reads the currently stored transcript. If there is already a transcript with a sequence number greater than or equal to the incoming one, it stops without changing storage. Otherwise, it encodes the incoming conversation into bytes and writes those bytes to the blob store under this conversation's transcript key.

**Call relations**: This is called when the system is ready to publish a completed or repaired conversation state. It calls `Transcript.read` to check what is already durable, then uses `encode` and `transcript_key` to store the newer transcript in the shared blob store.

*Call graph*: calls 1 internal fn (read); 2 external calls (encode, transcript_key).


### Schema contracts
Declares the schema package, shared runtime record shapes, and the central database table blueprint used across storage, tools, jobs, and migrations.

### `core/src/ufo/schema/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can contain an `__init__.py` file to say, “treat this folder as an importable package.” That lets the rest of the project refer to modules inside this directory using names like `ufo.schema.something` instead of relying on raw file paths.

Because this file has no code, it does not create classes, run setup steps, or change data. Its value is structural: it helps organize the project’s schema code under one clear namespace. A schema usually means a description of the shape of data, such as what fields an object should have and what types those fields should be. This file is the front door to that area of the codebase, even though the door itself has no extra instructions written on it.

If it were removed, imports may still work in some modern Python setups, but behavior can vary depending on packaging tools and project layout. Keeping it makes the package boundary explicit and predictable.


### `core/src/ufo/schema/records.py`

`data_model` · `cross-cutting`

This file is like the project’s shared paperwork. When a member sends a message, the system turns it into a “turn”: one durable unit of work with an ID, a status, an incoming message, optional context, and eventually a final result. Different parts of the product need to read and write that same record, so this file defines the common shapes and rules.

Most classes are Pydantic models, meaning they are data records that also check their own values when they are created. For example, TurnContext cleans unsafe text from surface-provided fields before they are later rendered into an engine prompt, and it rejects unknown timezone names early. Turn checks that a finished turn always has a matching terminal frame, while a queued or running turn does not.

The file also defines stable ID helpers. These create repeatable UUIDs from workspace, conversation, turn, billing, and reply information. That matters because the system may replay work after a crash or resume a parked turn. Repeatable IDs stop duplicate rows and duplicate user-visible replies.

There is also small product logic for agent icons. New agents get an icon based on their name when possible, while avoiding icons already used in the workspace. Without these shared definitions, surfaces and workers could disagree about statuses, IDs, prompts, terminal results, or validation rules, causing duplicate work, broken rendering, or unsafe context text.

#### Function details

##### `auto_agent_icon`  (lines 176–195)

```
def auto_agent_icon(name: str, taken: Collection[str]) -> TablerIcon
```

**Purpose**: Chooses the starting icon for a new agent. It tries to pick an icon that matches a word in the agent’s name, and otherwise picks a stable-looking random choice from unused icons.

**Data flow**: It receives an agent name and a collection of icons already taken in the workspace. It hashes the name to get a repeatable number, scans the name for known keywords such as “support” or “billing,” and prefers that keyword icon if it is still free. If not, it chooses from the remaining unused icons using the hash; if every icon is taken, it allows a repeat. The output is one valid icon name.

**Call relations**: This function is used when creating or displaying a new agent that needs a default visual mark. It relies on the standard SHA-256 hash function so the same name tends to get the same icon, while the surrounding agent-creation flow supplies the list of icons already in use.

*Call graph*: 1 external calls (sha256).


##### `turn_id_for`  (lines 249–251)

```
def turn_id_for(workspace_id: UUID, conversation_id: UUID, seq: int) -> UUID
```

**Purpose**: Builds the stable ID for a turn. A turn is one unit of conversation work, and this ID is also used as the workflow ID so retries can recognize the same work instead of making a duplicate.

**Data flow**: It receives a workspace ID, conversation ID, and sequence number. It combines them into one namespace string and turns that string into a UUID using UUID version 5, which is deterministic: the same inputs always produce the same output. The result is the turn’s UUID.

**Call relations**: This helper is called when a new turn is admitted into the queue or workflow system. It hands back the identity that later storage, workers, and replay logic can all use to refer to exactly the same turn.

*Call graph*: 1 external calls (uuid5).


##### `ledger_id_for`  (lines 254–259)

```
def ledger_id_for(workspace_id: UUID, turn_id: UUID, dimension: str, attempt: str='') -> UUID
```

**Purpose**: Builds a stable billing ledger ID for one kind of cost within one turn attempt. This prevents billing replay from creating duplicate charges for the same recorded attempt.

**Data flow**: It receives the workspace ID, turn ID, billing dimension, and optionally a run attempt ID. It combines those values into a deterministic UUID string and returns the UUID version 5 result. The output names one billing write for that workspace, turn, dimension, and attempt.

**Call relations**: This helper fits into the billing flow after a model run spends tokens or money. The worker can write billing data with this ID, and if the same attempt is replayed, the same ID is produced; if a parked turn later resumes under a new attempt, the new attempt value produces a separate ledger entry.

*Call graph*: 1 external calls (uuid5).


##### `mid_turn_reply_id_for`  (lines 262–273)

```
def mid_turn_reply_id_for(turn_id: UUID, round_index: int, span_index: int, attempt: str='') -> UUID
```

**Purpose**: Builds a stable ID for a reply spoken before a turn fully ends. This keeps recovered workflows from delivering the same partial reply to the member twice.

**Data flow**: It receives a turn ID, round number, span number, and optionally a run attempt ID. It folds those values into a deterministic UUID version 5. The output identifies one mid-turn reply at one position in one attempt.

**Call relations**: This helper is used by the turn-running flow when it emits user-visible text before the terminal result. Replay of the same attempt creates the same reply ID and can collapse onto the already-delivered record, while a resumed attempt gets different IDs for its new words.

*Call graph*: 1 external calls (uuid5).


##### `TurnContext._tag_safe_line`  (lines 439–443)

```
def _tag_safe_line(cls, value: str | None) -> str | None
```

**Purpose**: Cleans member- or surface-provided context text so it can be safely inserted into prompt context. It removes angle brackets and flattens whitespace so the text cannot pretend to be markup tags.

**Data flow**: It receives an optional string from fields such as sender, question, or source. If the value is missing, it stays missing. Otherwise the function removes “<” and “>”, splits and rejoins whitespace into a single line, and returns the cleaned text; if nothing meaningful remains, it returns nothing.

**Call relations**: Pydantic calls this validator automatically when a TurnContext is created. It runs before the engine later renders that context into a prompt, so unsafe surface text is cleaned at the boundary rather than trusted deeper in the system.


##### `TurnContext._known_zone`  (lines 447–454)

```
def _known_zone(cls, value: str | None) -> str | None
```

**Purpose**: Checks that a timezone string is a real IANA timezone name, such as “America/New_York.” This catches bad surface input before a turn starts running.

**Data flow**: It receives an optional timezone string. If it is missing, it returns it unchanged. If present, it asks the system timezone database to load it; success means the original string is returned, while failure becomes a validation error that says the timezone is unknown.

**Call relations**: Pydantic calls this validator when building TurnContext. The surface that admits the turn gets the error early, instead of letting a bad timezone break prompt construction or date handling during the worker’s run.

*Call graph*: 1 external calls (ZoneInfo).


##### `Turn.spawned`  (lines 488–491)

```
def spawned(self) -> bool
```

**Purpose**: Tells whether this turn was created by another turn rather than directly by a member or scheduler. In plain terms, it answers: “Is this a child task?”

**Data flow**: It reads the turn’s parent_turn_id field. If that parent ID is present, it returns true; otherwise it returns false. It does not change the turn.

**Call relations**: Other code can read this property when it needs to treat spawned subagent work differently from ordinary turns. It depends on the convention that only the spawn path fills in parent_turn_id.


##### `Turn._nothing_created`  (lines 495–498)

```
def _nothing_created(cls, value: object) -> object
```

**Purpose**: Normalizes a missing created-objects value into an empty tuple. This lets the rest of the code treat “nothing was created” as an empty list-like value instead of a database null.

**Data flow**: It receives the raw value for created_refs before normal validation. If the database or caller supplied null, it changes that to an empty tuple. Any other value passes through unchanged.

**Call relations**: Pydantic calls this validator while creating a Turn. It protects later turn-resume and cancellation logic from having to special-case a nullable database column whenever no objects were created.


##### `Turn._aware_utc`  (lines 502–507)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Ensures turn timestamps know they are in UTC time. This avoids accidentally treating a database-returned timestamp as local time.

**Data flow**: It receives an optional datetime for created_at or updated_at. If the value is missing, it stays missing. If it already has timezone information, it is returned unchanged. If it lacks timezone information, the function marks it as UTC and returns the corrected datetime.

**Call relations**: Pydantic calls this validator when a Turn is loaded or built. It especially protects paths using database drivers that drop timezone markers, so later code can safely compare and display timestamps.

*Call graph*: 1 external calls (replace).


##### `Turn._terminal_matches_status`  (lines 510–515)

```
def _terminal_matches_status(self) -> 'Turn'
```

**Purpose**: Checks that a turn’s status and final result agree with each other. A running, queued, or parked turn must not have a terminal frame, and a done, failed, or cancelled turn must have one with the same status.

**Data flow**: It looks at the Turn after all fields have been parsed. If the status is not finished but terminal data exists, or if the status is finished but terminal data is missing, it raises a validation error. If a terminal frame exists but says a different status than the turn itself, it also raises an error. Otherwise it returns the valid Turn unchanged.

**Call relations**: Pydantic calls this model-level validator after constructing a Turn. It enforces the queue contract shared by surfaces, workers, and storage, preventing inconsistent records from entering the rest of the system.


### `core/src/ufo/schema/tables.py`

`data_model` · `database setup and all database access`

This file is like the floor plan for the system’s database. It does not store data itself. Instead, it tells SQLAlchemy, a Python library for describing and talking to databases, what rooms exist and what each room is allowed to contain. The shared `metadata` object is the binder that holds all these table definitions together.

The tables cover the main things the product needs to remember: workspaces, members, agents, conversations, turns in a conversation, incoming messages, billing ledger entries, spending caps, connected accounts, credentials, shared files, synced sources, pages, runtime workers, and delivery queues. The file also defines links between tables with foreign keys, which are rules like “this conversation must belong to a real workspace.”

Many columns include constraints, which are guardrails enforced by the database. For example, a turn status must be one of a known set, a ledger amount must be positive, and a main agent cannot be archived. Indexes are added where the system will often search, such as pending work, active conversations, or billing records.

Without this file, different parts of the system could disagree about what data exists or what shape it has. The result would be broken migrations, invalid records, slower queries, and bugs that only appear after bad data has already been saved.

#### Function details

##### `_conversation_audience`  (lines 12–13)

```
def _conversation_audience(context: DefaultExecutionContext) -> str
```

**Purpose**: This function chooses the default audience label for a new conversation when one is not supplied directly. In plain terms, it decides whether the conversation should be shared or tied to a specific member based on the `member_id` being inserted.

**Data flow**: It receives a database execution context, which is SQLAlchemy’s snapshot of the row currently being inserted. It reads the row’s current `member_id`, passes that value to `conversation_audience`, turns the result into text, and gives that text back as the value for the conversation’s `audience` column.

**Call relations**: This function is attached to the `conversation.audience` column as a Python-side default. When code inserts a conversation without explicitly setting `audience`, SQLAlchemy calls this function, which asks `ufo.turns.audience.conversation_audience` to produce the correct audience value from the row’s `member_id`.

*Call graph*: 2 external calls (get_current_parameters, conversation_audience).

## 📊 State Registers Touched

- `reg-effective-config` — The current trusted settings for how the service should run, including database, provider, deployment, and safety options.
- `reg-database-schema-version` — The record of which database upgrades have already been applied and what storage shape the system expects.
- `reg-database-session-workspace-scope` — The shared database access layer that keeps reads and writes inside the right workspace and transaction.
- `reg-workspace-directory` — The durable list of workspaces and their core ownership, admin, billing, and setup state.
- `reg-agent-profiles` — The saved assistant definitions, including each agent's model, tools, visibility, setup needs, internet access, and identity.
- `reg-surface-routing` — The mapping from outside places like web, Slack, terminal, and iMessage to the right workspace, conversation, member, and agent.
- `reg-conversation-records` — The durable conversation list, including titles, audience, surface labels, sandbox links, and visibility rules.
- `reg-inbound-admission-queue` — The saved queue of incoming messages or intents waiting to become safe conversation turns.
- `reg-turn-state` — The shared status record for each unit of agent work, including claiming, running, completion, failure, parent-child links, and billing markers.
- `reg-transcript-store` — The saved conversation history and compacted summaries that later turns, portals, and auditors read back.
- `reg-cancellation-flags` — The shared stop signals and cleanup markers used to cancel turns, child work, sandboxes, and stuck jobs safely.
- `reg-runtime-instance-fleet` — The record of which server processes are alive and which shared listeners or jobs they currently own.
- `reg-background-job-queue` — The shared pool of delayed or recurring work that workers claim, run, retry, and clean up.
- `reg-schedule-monitor-store` — The saved recurring prompts, pauses, and outside-world watches that can wake conversations later.
- `reg-skill-store` — The shared library of built-in and user-created skills that can be selected, checked, and loaded into a turn.
- `reg-billing-ledger` — The shared money and usage record for tokens, images, videos, sandbox use, egress, balances, caps, and exports.
- `reg-object-registry` — The shared object front desk that gives stable names, views, permissions, and change history for workspace records.
- `reg-artifact-blob-store` — The shared file storage for generated artifacts, downloads, document previews, screenshots, and other saved output bytes.
- `reg-source-sync-state` — The saved state of connected content sources, including pages, checkpoints, errors, ownership, and read grants.
- `reg-memory-store` — The long-term saved facts, profiles, notes, and summaries that can be recalled in later conversations.
- `reg-subagent-objectives` — The shared plan and delegation state for child agents, objectives, steps, evidence, attempts, and result delivery.
- `reg-observability-context` — The shared tracing, logging, metrics, health, and redaction context used to understand what happened safely.
- `reg-extension-data-store` — Durable extension-scoped key/value or JSON state used by installed extensions beyond their manifest capabilities and lockfile selection.
- `reg-reply-delivery-outbox` — Durable reply records for messages that must be delivered exactly once or retried safely, including mid-turn replies before final turn completion.
- `reg-workspace-change-store` — Saved per-conversation file-change summaries produced from workspace git state at turn teardown and later shown in portal slots or audits.
- `reg-research-reference-store` — Conversation-scoped research findings, cited links, fetched-page metadata, and source-panel references created by browser or research workflows.
- `reg-conversation-todo-store` — Durable conversation checklist/todo state exposed as workspace objects and updated by tools or agents across turns.
- `reg-report-digest-store` — Saved generated report digest results and related background-report state separate from the schedule that triggered them.
- `reg-runtime-connection-pools` — Live pooled connections and reusable clients for shared services such as the database, Redis/live hub, blob storage, model providers, connector APIs, and sandbox/browser providers.
- `reg-turn-created-reference-store` — Saved references created by a turn, linking its work to newly produced artifacts, objects, sources, sites, or other records for later display, replay, and cleanup.
