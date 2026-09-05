# Shared safety, storage, configuration, and utility infrastructure  `stage-20` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support used by many parts of the system. It is not one step in the main work loop. Instead, it provides common guardrails and “plumbing” that other stages rely on.

The storage and database files are the main service pipes. blob.py stores and reads raw bytes, either from local files or cloud storage, while keeping workspace files separate from deploy-wide files. db.py opens safe database connections, runs migrations, wraps work in transactions, and cleans up connection pools. flags.py reads feature flags, which are controlled on/off switches, and falls back safely if the flag service fails.

The harness support adds safety walls around file paths, untrusted text, saved workflow data, logging, health checks, and sandbox network access. The runtime context files track which workspace, authority, and agent are currently allowed to act. The many package marker stages are mostly labels on folders, telling Python where runtime, extension, integration, document, developer, and sample code can be imported from. Together, these pieces keep shared resources organized, separated, and safer to use.

## Sub-stages

- [Harness safety, serialization, observability, and sandbox network settings](stage-20.1.md) `stage-20.1` — 7 files
- [Runtime workspace, authority, agent scope, and shared path limits](stage-20.2.md) `stage-20.2` — 5 files
- [Core non-runtime package markers](stage-20.3.md) `stage-20.3` — 11 files
- [Runtime package markers](stage-20.4.md) `stage-20.4` — 13 files
- [Application extension package markers](stage-20.5.md) `stage-20.5` — 11 files
- [External integration and web extension package markers](stage-20.6.md) `stage-20.6` — 14 files
- [Knowledge, document, monitoring, and workflow extension package markers](stage-20.7.md) `stage-20.7` — 13 files
- [Developer, debugging, skill-creation, and sample extension markers](stage-20.8.md) `stage-20.8` — 5 files

## Files in this stage

### Blob Storage
Provides shared byte storage across local and S3-style backends while keeping workspace and deploy-owned data separated.

### `core/src/ufo/blob.py`

`io_transport` · `cross-cutting`

A “blob” here means a stored chunk of bytes, such as an attachment, a generated asset, a transcript record, or a file belonging to a workspace. This file hides the storage location behind one asynchronous interface, meaning callers can ask to put, get, stream, delete, or list blobs without caring whether the bytes are on disk or in S3, Amazon’s object storage service.

There are two real backends. `FilesystemBlobStore` turns blob keys into files under a configured root folder. It writes through a temporary file and then replaces the final file, like writing a letter as a draft before putting it in the mailbox, so readers do not see half-written content. `S3BlobStore` talks to S3 using reusable async clients and supports large streamed uploads, temporary signed upload/download links, and listing by prefix.

On top of those backends are two safety wrappers. `WorkspaceBlobStore` automatically adds the current workspace’s prefix, so workspace code cannot casually read or write another workspace’s data. `FleetBlobStore` allows only a small set of deploy-wide prefixes, such as static assets and terminal payloads. Without this file, storage code would be duplicated, local and production behavior would drift, and data isolation would be much easier to break.

#### Function details

##### `BlobStore.put`  (lines 54–54)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Defines the common promise that any blob store must be able to save a whole byte string under a key. Callers use this when the full content is already in memory.

**Data flow**: A caller provides a text key and bytes → the chosen backend stores those bytes at that key → nothing is returned, but the stored object should be available for later reads.

**Call relations**: This is part of the shared storage contract. Web asset publishing calls through this shape so it can save assets without knowing whether the backend is local disk or S3.

*Call graph*: called by 1 (_publish_assets).


##### `BlobStore.get`  (lines 56–56)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Defines the common promise that any blob store must be able to read a whole stored object as bytes. It is used when the caller expects the object to fit comfortably in memory.

**Data flow**: A caller provides a key → the backend looks up that stored object → the bytes come back, or a missing-object error is raised by concrete implementations.

**Call relations**: Transcript compaction readers, Slack identity loading, and stored web asset lookup depend on this contract so they can fetch saved data through one interface.

*Call graph*: called by 4 (read_compaction_after, read_compaction_record, read_identity, _stored_asset).


##### `BlobStore.exists`  (lines 58–58)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Defines the common promise that any blob store can answer whether a key currently points to a stored object. This lets callers avoid unnecessary uploads or decide whether cached data is available.

**Data flow**: A key goes in → the backend checks storage metadata or the filesystem → a true or false answer comes out.

**Call relations**: Slack identity lookup and web asset publishing use this before deciding whether to read or write stored content.

*Call graph*: called by 3 (read_identity, _publish_assets, _stored_asset).


##### `BlobStore.delete`  (lines 60–62)

```
async def delete(self, key: str) -> None
```

**Purpose**: Defines the common promise that any blob store can remove an object. Deleting something that is already absent is intentionally treated as harmless.

**Data flow**: A key goes in → the backend attempts to remove that object → nothing is returned, and a missing key is not considered a failure.

**Call relations**: This belongs to the common storage contract so cleanup code can delete safely even if a previous attempt partly succeeded.


##### `BlobStore.get_stream`  (lines 64–64)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines the common promise that a blob store can read an object in smaller byte chunks. This matters for large files that should not be loaded all at once.

**Data flow**: A key goes in → the backend opens the object → chunks of bytes are yielded one by one until the object is fully read.

**Call relations**: Concrete backends implement this so download-style code can move large data through bounded memory instead of one giant buffer.


##### `BlobStore.put_stream`  (lines 66–66)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Defines the common promise that a blob store can write an object from a stream of chunks. This is used for large incoming content or content produced gradually.

**Data flow**: A key and an async stream of byte chunks go in → the backend writes each chunk in order → nothing is returned once the final object is stored.

**Call relations**: Concrete backends use this contract to support large writes consistently on both local disk and S3.


##### `BlobStore.list`  (lines 68–72)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Defines the common promise that a blob store can list stored objects below a required key prefix. The prefix requirement prevents accidental whole-store scans.

**Data flow**: A prefix goes in → the backend finds matching stored objects → a sorted, capped collection of entries comes out, each describing key, size, and modification time.

**Call relations**: Web asset publishing uses this shape when it needs to inspect already-published blobs under a known namespace.

*Call graph*: called by 1 (_publish_assets).


##### `FilesystemBlobStore.put`  (lines 81–86)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves a complete byte string as a file under the blob root. It uses an atomic-style write so readers do not see a partly written file.

**Data flow**: A blob key and bytes go in → the key is turned into a safe path, parent folders are created, bytes are written to a unique temporary file, and that file replaces the final path → the stored file now contains the new bytes.

**Call relations**: This is the local-disk implementation of `BlobStore.put`. It relies on `_resolve` to keep the path inside the store root and uses a temporary name before the final replace.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.get`  (lines 88–93)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a complete stored file from the filesystem backend. It turns normal filesystem “file not found” errors into the project’s `BlobNotFound` error.

**Data flow**: A key goes in → `_resolve` maps it to a safe path → the file’s bytes are read in a worker thread → bytes come back, or `BlobNotFound` is raised if no file exists.

**Call relations**: This is the local-disk implementation of `BlobStore.get`. It uses `_resolve` first so callers cannot use a blob key to escape the configured storage directory.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.exists`  (lines 95–97)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a filesystem-backed blob exists as a regular file. It is a lightweight way to ask whether a key is already stored.

**Data flow**: A key goes in → `_resolve` turns it into a safe path → the filesystem is asked whether that path is a file → true or false comes back.

**Call relations**: This is the local-disk implementation of `BlobStore.exists`, with `_resolve` providing the same containment check used by reads and writes.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.delete`  (lines 99–101)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes a filesystem-backed blob if it is present. Missing files are ignored so repeated cleanup attempts are safe.

**Data flow**: A key goes in → `_resolve` produces a safe path → the file is unlinked if it exists → no value is returned.

**Call relations**: This is the local-disk implementation of `BlobStore.delete`. It uses `_resolve` before touching the filesystem.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.get_stream`  (lines 103–116)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads a filesystem-backed blob in fixed-size chunks. This is useful for large files because only one chunk needs to be in memory at a time.

**Data flow**: A key goes in → `_resolve` finds the safe file path → the file is opened and read chunk by chunk → each chunk is yielded, and the file is closed afterward.

**Call relations**: This is the local-disk implementation of streaming reads. If opening the file fails because it is absent, it raises `BlobNotFound` just like the whole-file read.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.put_stream`  (lines 118–131)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes a filesystem-backed blob from incoming chunks. It protects readers from partial writes by writing to a temporary file first.

**Data flow**: A key and chunk stream go in → `_resolve` chooses a safe path, folders are created, chunks are written to a temporary file → on success the temporary file replaces the final file; on failure the temporary file is removed.

**Call relations**: This is the local-disk implementation of streaming writes. It shares the same safety pattern as `FilesystemBlobStore.put`, but accepts data gradually.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.list`  (lines 133–136)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists filesystem-backed blobs under a required prefix. It refuses an empty prefix to avoid accidentally walking the whole store.

**Data flow**: A prefix goes in → if the prefix is non-empty, the blocking directory walk is run in a worker thread → a tuple of matching `BlobEntry` records comes back.

**Call relations**: This is the public listing method for the filesystem backend. It delegates the actual directory traversal to `_walk` so the async event loop is not blocked.

*Call graph*: 1 external calls (to_thread).


##### `FilesystemBlobStore._walk`  (lines 138–159)

```
def _walk(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Does the actual filesystem scan for `list`. It finds files under the relevant directory, filters them by prefix, skips temporary files, and turns them into blob entries.

**Data flow**: A prefix goes in → the root is canonicalized, a starting directory is chosen, files are walked, matching files are measured → sorted `BlobEntry` records come out, capped at the maximum list size.

**Call relations**: `FilesystemBlobStore.list` calls this inside a worker thread. It uses `_contained_root` and `_resolve` so the scan stays anchored inside the configured blob root.

*Call graph*: calls 2 internal fn (_contained_root, _resolve); 4 external calls (__init__, fromtimestamp, walk, Path).


##### `FilesystemBlobStore._resolve`  (lines 161–166)

```
def _resolve(self, key: str) -> Path
```

**Purpose**: Converts a blob key into a safe filesystem path. Its main job is to stop keys like `../secret` from escaping the blob storage folder.

**Data flow**: A key goes in → the configured root is canonicalized, the key is joined and resolved → a path comes out if it stays under the root; otherwise a `ValueError` is raised.

**Call relations**: All filesystem read, write, delete, stream, and walk operations call this before touching disk. It is the filesystem backend’s main safety gate.

*Call graph*: calls 1 internal fn (_contained_root); called by 7 (_walk, delete, exists, get, get_stream, put, put_stream).


##### `FilesystemBlobStore._contained_root`  (lines 168–181)

```
def _contained_root(self) -> Path
```

**Purpose**: Finds the real blob root directory in a way that works with configured paths and symlinked deployment layouts. If the root does not exist yet, it returns the path where the first write should create it.

**Data flow**: The store’s configured root path is read → `configured_root` validates and canonicalizes it when possible → a resolved root path comes back, or a not-yet-existing root is resolved directly.

**Call relations**: `_resolve` and `_walk` call this whenever they need the trustworthy root path used for containment checks.

*Call graph*: called by 2 (_resolve, _walk); 1 external calls (configured_root).


##### `_is_missing_key`  (lines 184–185)

```
def _is_missing_key(error: ClientError) -> bool
```

**Purpose**: Recognizes the different S3 error codes that all mean “this object is not there.” S3-compatible services do not always use the exact same code.

**Data flow**: A `ClientError` from S3 goes in → the nested error code is inspected → true comes out for known missing-object codes, otherwise false.

**Call relations**: S3 reads, existence checks, and streaming reads call this so they can translate missing objects into `BlobNotFound` or false instead of treating them as unexpected cloud errors.

*Call graph*: called by 3 (exists, get, get_stream).


##### `S3BlobStore.put`  (lines 207–209)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves a complete byte string as an S3 object. It is the cloud-storage version of whole-object writing.

**Data flow**: A key and bytes go in → `_client` provides the S3 client for the current async loop → S3 receives a `put_object` request → the object is stored in the configured bucket.

**Call relations**: This implements `BlobStore.put` for S3. It depends on `_client` so client creation is reused rather than repeated for every write.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get`  (lines 211–221)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a complete S3 object into memory. Missing S3 objects are translated into the project’s `BlobNotFound` error.

**Data flow**: A key goes in → `_client` gets an S3 client → S3 is asked for the object → the response body is read fully and returned as bytes, or a missing-key error becomes `BlobNotFound`.

**Call relations**: This implements `BlobStore.get` for S3. It uses `_is_missing_key` to distinguish normal absence from real S3 failures.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.exists`  (lines 223–231)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether an S3 object exists without downloading it. It uses object metadata lookup, which is cheaper than reading the full body.

**Data flow**: A key goes in → `_client` gets an S3 client → S3 is asked for object headers → true comes back if present, false if S3 reports a missing key, and other errors are re-raised.

**Call relations**: This implements `BlobStore.exists` for S3 and uses `_is_missing_key` to turn S3’s missing-object response into a simple false.

*Call graph*: calls 2 internal fn (_client, _is_missing_key).


##### `S3BlobStore.delete`  (lines 233–235)

```
async def delete(self, key: str) -> None
```

**Purpose**: Removes an object from the configured S3 bucket. S3 delete operations are naturally safe to repeat for absent keys.

**Data flow**: A key goes in → `_client` provides the S3 client → S3 receives a delete request → no value is returned.

**Call relations**: This implements `BlobStore.delete` for S3. It relies on `_client` for the reusable connection and signing setup.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get_stream`  (lines 237–248)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads an S3 object in chunks instead of loading it all at once. This keeps memory use bounded for large downloads.

**Data flow**: A key goes in → `_client` gets an S3 client → S3 returns a streaming body → chunks are yielded until the object is fully read, or `BlobNotFound` is raised for a missing key.

**Call relations**: This implements streaming reads for the S3 backend. It uses `_is_missing_key` for consistent missing-object behavior.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.put_stream`  (lines 250–294)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed data to S3, using S3 multipart upload for larger content. Multipart upload means a large object is sent as numbered pieces and then assembled by S3.

**Data flow**: A key and chunk stream go in → chunks are buffered until they reach the multipart part size → small content is sent as one object, while large content is uploaded in parts and completed → on failure, any unfinished multipart upload is aborted.

**Call relations**: This is the S3 implementation of streamed writes. It calls `_client` once and then coordinates the S3 upload calls needed for either small or large data.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_put`  (lines 296–320)

```
async def presigned_put(self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary upload URL for one exact object, including its expected size and SHA-256 checksum. This lets an untrusted sandbox upload directly to S3 without receiving broad write permission.

**Data flow**: A key, size, checksum, and expiry time go in → `_client` signs an S3 `put_object` request with those restrictions → a URL string comes out that only works until it expires and only for matching bytes.

**Call relations**: Workspace-level presigned upload methods call this when the backend is S3. The URL can then be handed to sandboxed code while keeping the destination and content tightly limited.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_put_unmeasured`  (lines 322–332)

```
async def presigned_put_unmeasured(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary upload URL for a fixed S3 key without signing the body size or checksum. It is for trusted producers whose output size is not known before they generate it.

**Data flow**: A key and expiry time go in → `_client` signs a `put_object` request for that key only → a temporary URL comes out that can upload any bytes to that one key.

**Call relations**: Workspace-level code can call this for S3-only flows such as preview rendering, where the writer controls the content but cannot report its length ahead of time.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_get`  (lines 334–341)

```
async def presigned_get(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary download URL for an S3 object. Anyone holding the URL can read that object until the URL expires.

**Data flow**: A key and expiry time go in → `_client` signs an S3 `get_object` request → a temporary URL string comes out.

**Call relations**: Workspace-level presigned download methods call this when the backend is S3, allowing controlled direct reads without proxying the bytes through the main service.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.put_host`  (lines 343–354)

```
async def put_host(self) -> str
```

**Purpose**: Finds the hostname that presigned upload URLs will use. This is needed so a sandbox egress proxy can allow exactly the host needed for the upload.

**Data flow**: The configured S3 client is read → its endpoint URL is parsed → the hostname comes out, with the bucket added for normal AWS virtual-hosted addressing when appropriate.

**Call relations**: This calls `_client` so the hostname matches the actual client configuration used to create signed URLs, avoiding mismatches between proxy rules and real upload URLs.

*Call graph*: calls 1 internal fn (_client); 1 external calls (urlsplit).


##### `S3BlobStore.list`  (lines 356–373)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists S3 objects under a required prefix and turns them into the project’s standard blob entry records. It caps the result so a listing cannot grow without bound.

**Data flow**: A non-empty prefix goes in → `_client` gets an S3 client and pages through S3 list results → matching object keys, sizes, and modification times become `BlobEntry` records → a capped tuple comes back.

**Call relations**: This implements `BlobStore.list` for S3. It uses S3 pagination because S3 may return large listings in pages rather than one response.

*Call graph*: calls 1 internal fn (_client); 1 external calls (__init__).


##### `S3BlobStore.close`  (lines 375–381)

```
async def close(self) -> None
```

**Purpose**: Closes and forgets the S3 client associated with the currently running async event loop. This is cleanup for long-lived reusable clients.

**Data flow**: The current event loop is identified → any cached client and lock for that loop are removed from the store → if a client existed, it is closed.

**Call relations**: This pairs with `_client`, which caches one client per event loop. It is used during cleanup so network resources do not remain open unnecessarily.

*Call graph*: 1 external calls (get_running_loop).


##### `S3BlobStore._client`  (lines 383–409)

```
async def _client(self) -> AioBaseClient
```

**Purpose**: Creates or reuses the S3 client for the current async event loop. Reusing the client avoids expensive setup on every operation and respects that the underlying HTTP client belongs to one event loop.

**Data flow**: The running event loop is read → if a client already exists for it, that client is returned → otherwise a per-loop lock prevents duplicate creation, a new configured S3 client is opened, cached, and returned.

**Call relations**: Every S3 operation calls this before talking to S3. It is the central place that chooses signing style and addressing style so presigned URLs and normal S3 calls behave consistently.

*Call graph*: called by 11 (delete, exists, get, get_stream, list, presigned_get, presigned_put, presigned_put_unmeasured, put, put_host (+1 more)); 3 external calls (get_session, Lock, get_running_loop).


##### `WorkspaceBlobStore.put`  (lines 422–423)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves bytes under the currently bound workspace’s storage prefix. Callers provide only a workspace-relative key, not the full global key.

**Data flow**: A relative key and bytes go in → `_full` adds `workspaces/<workspace id>/` → the backend stores the bytes at that full key → nothing is returned.

**Call relations**: Environment document and file storage call this to save workspace-owned data. It delegates the actual write to the filesystem or S3 backend after adding the workspace boundary.

*Call graph*: calls 1 internal fn (_full); called by 2 (store_environment_document, store_environment_file).


##### `WorkspaceBlobStore.get`  (lines 425–426)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads bytes from the currently bound workspace’s storage area. It prevents callers from manually choosing another workspace’s prefix.

**Data flow**: A relative key goes in → `_full` builds the full workspace key → the backend reads that object → bytes come back.

**Call relations**: Environment loading code calls this to retrieve workspace-owned documents and files. The wrapper supplies the workspace prefix before handing off to the backend.

*Call graph*: calls 1 internal fn (_full); called by 2 (load_environment_document, load_environment_file).


##### `WorkspaceBlobStore.exists`  (lines 428–429)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a workspace-relative blob exists in the current workspace. It gives callers a simple yes-or-no answer without exposing global keys.

**Data flow**: A relative key goes in → `_full` attaches the current workspace prefix → the backend checks existence → true or false comes back.

**Call relations**: This follows the same workspace-prefixing path as reads and writes, using `_full` before asking the backend.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.delete`  (lines 431–432)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes a blob from the currently bound workspace. It keeps deletion scoped to that workspace by constructing the full key internally.

**Data flow**: A relative key goes in → `_full` adds the current workspace prefix → the backend deletes that full key → no value is returned.

**Call relations**: This uses `_full` as the safety step before delegating deletion to the underlying filesystem or S3 store.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.get_stream`  (lines 434–438)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Opens a workspace blob for chunked reading. The workspace prefix is resolved immediately so the stream can keep working even if the surrounding workspace scope exits later.

**Data flow**: A relative key goes in → `_full` captures the full workspace key right away → the backend returns a stream for that full key → chunks can later be read from it.

**Call relations**: Runtime extension context code uses this to read member blob text. It hands off to the backend’s streaming read after locking in the correct workspace key.

*Call graph*: calls 1 internal fn (_full); called by 1 (_member_blob_text).


##### `WorkspaceBlobStore.put_stream`  (lines 440–441)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed bytes into the current workspace’s blob area. It supports large or gradually produced content while preserving workspace isolation.

**Data flow**: A relative key and chunk stream go in → `_full` adds the workspace prefix → the backend writes the stream at that full key → no value is returned on success.

**Call relations**: This is the workspace wrapper around backend streaming writes, with `_full` enforcing the namespace boundary first.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.list`  (lines 443–448)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists blobs under a prefix inside the current workspace and returns keys relative to the workspace. Callers do not see the internal `workspaces/<id>/` prefix.

**Data flow**: A relative prefix goes in → `_full` builds the workspace root and backend list prefix → backend entries are fetched → each returned key has the workspace root removed before being returned.

**Call relations**: This delegates listing to the backend but reshapes the results for workspace callers, using `replace` to keep size and modification time while changing the visible key.

*Call graph*: calls 1 internal fn (_full); 1 external calls (replace).


##### `WorkspaceBlobStore.presigned_put`  (lines 450–461)

```
async def presigned_put(self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a restricted S3 upload URL for a blob in the current workspace. It only works when the underlying backend is S3.

**Data flow**: A relative key, size, checksum, and expiry go in → `_full` adds the workspace prefix → if the backend is S3, it creates a measured presigned upload URL → the URL comes back; otherwise a type error is raised.

**Call relations**: This is the workspace-scoped wrapper around `S3BlobStore.presigned_put`, ensuring direct uploads still land under the current workspace.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.presigned_put_unmeasured`  (lines 463–469)

```
async def presigned_put_unmeasured(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary S3 upload URL for a current-workspace key without restricting body size or checksum. It is only available with the S3 backend.

**Data flow**: A relative key and expiry go in → `_full` adds the workspace prefix → the S3 backend signs an unmeasured upload URL → the URL comes back, or a type error is raised for non-S3 storage.

**Call relations**: This wraps `S3BlobStore.presigned_put_unmeasured` while preserving workspace scoping.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.presigned_get`  (lines 471–478)

```
async def presigned_get(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary S3 download URL for a blob in the current workspace. It is only meaningful when blobs are stored in S3.

**Data flow**: A relative key and expiry go in → `_full` constructs the full workspace key → the S3 backend signs a download URL → the URL comes back, or a type error is raised for the filesystem backend.

**Call relations**: This wraps `S3BlobStore.presigned_get` so direct downloads cannot point outside the current workspace’s prefix.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore._full`  (lines 480–483)

```
def _full(self, key: str) -> str
```

**Purpose**: Builds the real storage key for a workspace-relative key. It also refuses keys that are already manually workspace-prefixed, which helps prevent confused or double-prefixed paths.

**Data flow**: A relative key goes in → the current workspace id is read from the active workspace context → `workspaces/<id>/` is prepended → the full key comes out, or a `ValueError` is raised for an already-prefixed key.

**Call relations**: Every workspace store operation calls this before touching the backend. It is the single point that ties blob access to `ws_current()` and enforces workspace isolation.

*Call graph*: called by 10 (delete, exists, get, get_stream, list, presigned_get, presigned_put, presigned_put_unmeasured, put, put_stream); 1 external calls (ws_current).


##### `FleetBlobStore.put`  (lines 494–495)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves deploy-wide bytes under an allowed fleet namespace. This is for data that belongs to the deployment rather than to a workspace.

**Data flow**: A key and bytes go in → `_checked` verifies the key starts with an allowed fleet prefix → the backend stores the bytes → no value is returned.

**Call relations**: This wraps backend writing with fleet namespace validation so deploy-wide storage cannot be used as a back door into workspace data.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.get`  (lines 497–498)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a deploy-wide blob from an allowed fleet namespace. It rejects keys outside the approved fleet areas.

**Data flow**: A key goes in → `_checked` validates the prefix → the backend reads the object → bytes come back.

**Call relations**: This is the fleet-scoped wrapper around backend reads, relying on `_checked` before delegation.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.exists`  (lines 500–501)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether an allowed deploy-wide blob exists. It keeps existence checks inside the fleet namespaces.

**Data flow**: A key goes in → `_checked` confirms it starts with an approved prefix → the backend checks storage → true or false comes back.

**Call relations**: This follows the same validation path as other fleet operations, using `_checked` before asking the backend.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.delete`  (lines 503–504)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes a deploy-wide blob, but only from approved fleet namespaces. This prevents cleanup code from deleting arbitrary blob keys.

**Data flow**: A key goes in → `_checked` validates it → the backend deletes that key → no value is returned.

**Call relations**: This delegates deletion to the backend after `_checked` enforces the fleet namespace boundary.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.get_stream`  (lines 506–507)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads an allowed deploy-wide blob in chunks. This is useful for larger fleet-owned files such as terminal payload spill or static content.

**Data flow**: A key goes in → `_checked` validates its namespace → the backend opens a stream → byte chunks are yielded by the backend.

**Call relations**: This is the fleet wrapper around backend streaming reads, with `_checked` acting as the guard before handoff.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.put_stream`  (lines 509–510)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed bytes into an allowed deploy-wide namespace. It supports large fleet-owned content without loading all bytes at once.

**Data flow**: A key and chunk stream go in → `_checked` verifies the key prefix → the backend writes the stream → no value is returned on success.

**Call relations**: This delegates to the backend’s streaming write only after `_checked` confirms the key belongs to the fleet area.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.list`  (lines 512–513)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists deploy-wide blobs under an allowed fleet prefix. It refuses prefixes outside the closed fleet namespace set.

**Data flow**: A prefix goes in → `_checked` validates that the prefix is allowed → the backend lists matching entries → blob entries come back.

**Call relations**: This wraps backend listing with fleet namespace validation, using `_checked` before the backend scan or S3 list.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore._checked`  (lines 515–518)

```
def _checked(self, key: str) -> str
```

**Purpose**: Verifies that a fleet blob key belongs to one of the approved deploy-wide namespaces. It is the safety gate for `FleetBlobStore`.

**Data flow**: A key goes in → its prefix is compared with the allowed fleet prefixes → the same key comes back if allowed, or a `ValueError` is raised if not.

**Call relations**: Every fleet store method calls this before delegating to the backend. It prevents fleet storage from being used to access arbitrary or workspace-prefixed blobs.

*Call graph*: called by 7 (delete, exists, get, get_stream, list, put, put_stream).


##### `blob_store_for`  (lines 521–533)

```
def blob_store_for(config: BlobConfig) -> FilesystemBlobStore | S3BlobStore
```

**Purpose**: Builds the concrete blob backend described by configuration. It chooses local filesystem storage or S3 storage and checks that the required settings are present.

**Data flow**: A `BlobConfig` object goes in → the backend name is inspected → a `FilesystemBlobStore` is returned with a root path, or an `S3BlobStore` is returned with bucket and endpoint settings; missing required fields raise `ValueError`.

**Call relations**: Startup or setup code can call this once configuration is loaded. The returned backend is then wrapped or used by the rest of the system through the common blob store behavior.

*Call graph*: 2 external calls (__init__, __init__).


### Database Access
Centralizes database connections, workspace-scoped transactions, migrations, and connection pool cleanup.

### `core/src/ufo/db.py`

`io_transport` · `startup, request handling, background jobs, migrations, teardown`

This file protects one of the most important boundaries in the system: one running service can serve many workspaces, but a database transaction must only see the workspace it is meant to see. It does that by keeping database engines private inside this module and asking callers to use `workspace_tx` for normal work. A `ContextVar` named `current_workspace` stores the workspace for the current request or job, and `workspace_tx` pins that value into PostgreSQL for just one transaction. That is like writing the room number on a visitor badge that expires when the visit ends.

The file also has an `owner_tx` escape hatch for background sweeps that must first list work across all workspaces. That path is intentionally narrow: it should find identifiers, then re-enter the proper workspace before reading real workspace data.

Connections are pooled, meaning the service reuses open database connections instead of opening a fresh one every time. Because asynchronous database connections belong to the event loop that created them, this file keeps a separate engine per event loop and database URL. It also supports SQLite for local or test use, including special settings so SQLite’s single-writer behavior queues cleanly. Finally, it runs Alembic migrations, checks database reachability at startup, records transaction timing metrics, and disposes engines safely during shutdown.

#### Function details

##### `_build_engine`  (lines 108–118)

```
def _build_engine(url: str, pool: _Pool) -> AsyncEngine
```

**Purpose**: Creates a SQLAlchemy asynchronous database engine for one database URL and one pool definition. It also adds SQLite-specific setup hooks when the database is SQLite.

**Data flow**: It receives a database URL and a `_Pool` object that describes pool size and naming. It asks `_pool_kwargs` for the right connection-pool settings, builds an async engine, attaches SQLite connection and transaction setup if needed, and returns the ready engine.

**Call relations**: When `_engine_for` needs an engine that does not yet exist, it calls `_build_engine`. `verify_db_reachable` also calls it to make a temporary engine just to test whether the database can be reached.

*Call graph*: calls 1 internal fn (_pool_kwargs); called by 2 (_engine_for, verify_db_reachable); 1 external calls (create_async_engine).


##### `_pool_kwargs`  (lines 121–141)

```
def _pool_kwargs(url: str, pool: _Pool) -> dict[str, Any]
```

**Purpose**: Chooses the right connection-pool options for the database type. PostgreSQL and SQLite need different settings, so this function keeps those differences in one place.

**Data flow**: It receives a URL and pool description, parses the URL, and decides whether it points to SQLite or another database. For SQLite it returns local-file-friendly pool settings; for PostgreSQL-style databases it returns bounded pool settings, connection recycling, pre-ping checks, and driver-specific options from `_driver_kwargs`.

**Call relations**: `_build_engine` calls this before creating an engine. If the URL uses a non-SQLite backend, `_pool_kwargs` hands off to `_driver_kwargs` to fill in details that depend on the database driver.

*Call graph*: calls 1 internal fn (_driver_kwargs); called by 1 (_build_engine); 1 external calls (make_url).


##### `_driver_kwargs`  (lines 144–171)

```
def _driver_kwargs(driver: str, pool: _Pool) -> dict[str, Any]
```

**Purpose**: Provides low-level connection options for the selected PostgreSQL driver. It sets timeouts, names the connection pool for database observability, and disables prepared-statement caching to avoid stale query plans after migrations.

**Data flow**: It receives the driver name and pool description. If the driver is asyncpg, it returns asyncpg-shaped connection arguments; otherwise it returns psycopg-shaped connection arguments. The output is a dictionary later passed into SQLAlchemy engine creation.

**Call relations**: `_pool_kwargs` calls this only for non-SQLite URLs. It is the final step before `_build_engine` creates a database engine.

*Call graph*: called by 1 (_pool_kwargs).


##### `_engine_for`  (lines 174–190)

```
def _engine_for(url: str, pool: _Pool) -> AsyncEngine
```

**Purpose**: Finds or creates the database engine for the current asynchronous event loop and URL. This matters because async database connections are tied to the event loop that opened them.

**Data flow**: It reads the currently running event loop and combines it with the URL as a lookup key. It removes registry entries for loops that have closed, returns an existing engine if one is registered, or builds and stores a new one with `_build_engine`.

**Call relations**: `workspace_tx` and `owner_tx` call this whenever they need a transaction. If it cannot find an engine for this loop and URL, it creates one through `_build_engine`.

*Call graph*: calls 1 internal fn (_build_engine); called by 2 (owner_tx, workspace_tx); 1 external calls (get_running_loop).


##### `init_db`  (lines 193–197)

```
def init_db(url: str) -> None
```

**Purpose**: Registers the main application database URL. This is the setup step that must happen before normal workspace transactions can run.

**Data flow**: It receives a database URL and stores it in the module-level `_app_url`. If a URL is already registered, it raises an error instead of silently switching databases.

**Call relations**: Composition roots, such as service startup code or command setup code, call this before using `workspace_tx` or `owner_tx`. Those transaction functions later read the stored URL.


##### `init_owner_db`  (lines 200–214)

```
def init_owner_db(url: str) -> None
```

**Purpose**: Registers the special owner database URL used for cross-workspace enumeration. It also normalizes plain PostgreSQL URLs into the async driver form SQLAlchemy needs here.

**Data flow**: It receives an owner database URL, refuses to overwrite an existing one, rewrites a leading `postgresql://` into `postgresql+asyncpg://`, and stores the result in `_owner_url`.

**Call relations**: Service startup calls this when the process has an owner-role database secret. Later, `owner_tx` chooses this owner URL when it is available; otherwise it falls back to the regular app URL.


##### `verify_db_reachable`  (lines 217–237)

```
async def verify_db_reachable() -> None
```

**Purpose**: Checks at startup that every configured database can actually be reached. This prevents a service from appearing ready while all later requests would fail on database access.

**Data flow**: It reads the registered app and owner URLs. For each one, it builds a temporary engine with `_build_engine`, opens and closes a connection, and then disposes the engine so the check leaves no pooled connections behind. If no database has been initialized, it raises an error.

**Call relations**: Startup code can await this after `init_db` and possibly `init_owner_db`. It uses `_build_engine` directly rather than publishing an engine into the normal per-loop registry.

*Call graph*: calls 1 internal fn (_build_engine).


##### `dispose_db`  (lines 240–263)

```
async def dispose_db() -> None
```

**Purpose**: Shuts down all registered database engines and clears the stored database URLs. It is used by command cleanup, tests, and process teardown.

**Data flow**: It clears `_app_url` and `_owner_url`, then walks through both engine registries. Engines owned by the current event loop are disposed immediately; engines owned by other still-running loops are removed from the registry and handed off with `_hand_off`. Closed-loop entries are simply dropped.

**Call relations**: Teardown code calls this when the whole database layer should be reset. It calls `_hand_off` for engines that must be closed on a different event loop.

*Call graph*: calls 1 internal fn (_hand_off); 1 external calls (get_running_loop).


##### `_hand_off`  (lines 266–273)

```
def _hand_off(loop: asyncio.AbstractEventLoop, engine: AsyncEngine) -> None
```

**Purpose**: Asks another event loop to dispose an engine that belongs to that loop. This avoids closing async database connections from the wrong thread or loop.

**Data flow**: It receives an event loop and engine. It schedules `_dispose_on_this_loop` on that loop with the engine as an argument. If the loop closes during the handoff, it catches the runtime error and gives up because the loop can no longer do any cleanup work.

**Call relations**: `dispose_db` calls this for engines owned by loops other than the current one. The handoff causes `_dispose_on_this_loop` to run later on the owning loop.

*Call graph*: called by 1 (dispose_db); 1 external calls (call_soon_threadsafe).


##### `_dispose_on_this_loop`  (lines 276–300)

```
def _dispose_on_this_loop(engine: AsyncEngine) -> None
```

**Purpose**: Runs engine disposal on the event loop that owns the engine’s database connections. It keeps the disposal task alive until it finishes.

**Data flow**: It receives an engine, reads the current event loop, removes bookkeeping for loops that have already closed, starts `engine.dispose()` as an asynchronous task, and stores that task in `_disposing`. When the task finishes, a callback removes it from the pending set.

**Call relations**: This function is scheduled by `_hand_off` onto the correct event loop. It creates the disposal task and relies on its nested `finished` callback to clean up bookkeeping.

*Call graph*: 3 external calls (ensure_future, get_running_loop, dispose).


##### `_dispose_on_this_loop.finished`  (lines 295–298)

```
def finished(done: asyncio.Task[None]) -> None
```

**Purpose**: Cleans up the record of one engine-disposal task after it completes. It removes the loop’s entry entirely when no disposal tasks remain for that loop.

**Data flow**: It receives the completed task, removes it from the pending set for the loop, and deletes the loop’s pending-task set if it is now empty.

**Call relations**: _dispose_on_this_loop attaches this as a completion callback to each disposal task. It runs automatically when that task finishes.


##### `dispose_loop_engines`  (lines 303–313)

```
async def dispose_loop_engines() -> None
```

**Purpose**: Disposes only the engines owned by the currently running event loop, without forgetting the configured database URLs. This is useful for temporary event loops that are about to close.

**Data flow**: It gets the current event loop, finds engines in the app and owner registries whose key uses that loop, removes each from its registry, and awaits its disposal.

**Call relations**: Code that creates short-lived event loops can call this before closing the loop. Unlike `dispose_db`, it does not reset `init_db` state and does not touch engines belonging to other loops.

*Call graph*: 1 external calls (get_running_loop).


##### `_stopping`  (lines 316–323)

```
def _stopping() -> bool
```

**Purpose**: Detects whether the current task is being cancelled. This helps the transaction wrapper treat shutdown as shutdown, not as an ordinary database error to retry or ignore.

**Data flow**: It reads the current asyncio task. If there is a task and it has pending cancellation requests, it returns true; otherwise it returns false.

**Call relations**: `_opened` calls this when database opening or transaction work fails. If cancellation is in progress, `_opened` converts disguised database errors back into cancellation.

*Call graph*: called by 1 (_opened); 1 external calls (current_task).


##### `_await_opening`  (lines 326–337)

```
async def _await_opening(opening: asyncio.Future[AsyncConnection]) -> tuple[AsyncConnection, asyncio.CancelledError | None]
```

**Purpose**: Waits for a transaction-opening future to finish while preserving cancellation information. It lets the database open complete cleanly even if the surrounding task is asked to cancel.

**Data flow**: It receives a future that should produce an `AsyncConnection`. It repeatedly awaits it through `asyncio.shield`, records any cancellation request, and stops once the opening future is done or another exception breaks the wait. It returns the opened connection plus any cancellation that arrived while waiting.

**Call relations**: `_opened` uses this while entering `engine.begin()`. The result tells `_opened` both whether the connection opened and whether cancellation needs to be re-raised later.

*Call graph*: called by 1 (_opened); 1 external calls (shield).


##### `_await_close`  (lines 340–348)

```
async def _await_close(close: asyncio.Future[bool | None]) -> asyncio.CancelledError | None
```

**Purpose**: Waits for transaction cleanup to finish while remembering whether cancellation happened during cleanup. This protects commit or rollback from being interrupted halfway through.

**Data flow**: It receives a future for closing the transaction context. It shields that close operation, records any cancellation requests, waits until the close is done, checks the result for errors, and returns the recorded cancellation if there was one.

**Call relations**: `_opened` uses this after the caller’s transaction body finishes or fails. It lets `_opened` finish the database cleanup before deciding whether to re-raise cancellation.

*Call graph*: called by 1 (_opened); 1 external calls (shield).


##### `_opened`  (lines 352–416)

```
async def _opened(engine: AsyncEngine, path: str) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens a database transaction safely, measures how long acquisition took, reports failures, and guarantees the transaction closes correctly. It is the shared transaction wrapper used by both workspace and owner transactions.

**Data flow**: It receives an engine and a path label such as `workspace` or `owner`. For SQLite it may acquire a per-engine lock first, then starts `engine.begin()`, waits for it through `_await_opening`, records metrics, yields the connection to the caller, and finally closes the transaction through `_await_close`. It reports pool exhaustion and unavailable transactions, handles cancellation carefully, and releases the SQLite lock at the end.

**Call relations**: `workspace_tx` and `owner_tx` call this to do the common work of opening and closing a transaction. Inside, it calls `_await_opening`, `_await_close`, and `_stopping`, and it uses SQLAlchemy and asyncio tools to run the transaction context.

*Call graph*: calls 3 internal fn (_await_close, _await_opening, _stopping); called by 2 (owner_tx, workspace_tx); 7 external calls (Lock, ensure_future, AsyncExitStack, begin, monotonic, emit_histogram, emit_metric).


##### `workspace_tx`  (lines 420–430)

```
async def workspace_tx() -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens a normal application transaction scoped to the current workspace. This is the safe path callers should use when reading or writing workspace data.

**Data flow**: It checks that the app database URL was initialized, gets the right engine with `_engine_for`, and opens a transaction with `_opened`. It reads `current_workspace`; if a workspace is set and the database is PostgreSQL, it runs `set_config` so PostgreSQL row-level security can restrict this transaction to that workspace. It then yields the database connection to the caller.

**Call relations**: Application request, job, or turn code uses this after setting `current_workspace`. It relies on `_engine_for` for the per-loop engine and `_opened` for transaction lifetime, then hands the scoped connection to the caller’s database queries.

*Call graph*: calls 2 internal fn (_engine_for, _opened); 1 external calls (text).


##### `failed_statement`  (lines 433–453)

```
def failed_statement(error: BaseException) -> dict[str, str]
```

**Purpose**: Extracts safe logging fields from a database error: the SQL statement and SQLSTATE code. It avoids logging bound values or database messages that might contain workspace data.

**Data flow**: It receives any exception. If it is not a SQLAlchemy database API error, it returns an empty dictionary. If it is a database error, it copies a shortened statement when available and reads the SQLSTATE or PostgreSQL code from the original driver error, then returns those fields.

**Call relations**: Error logging code can call this when reporting a database failure. It does not call other local functions; it translates driver-specific error details into safe structured log fields.


##### `owner_tx`  (lines 457–470)

```
async def owner_tx() -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens the special cross-workspace transaction used to enumerate work across all workspaces. It deliberately does not set a workspace value, so callers must not use it for normal workspace-scoped reads.

**Data flow**: It chooses the owner URL and owner pool if an owner URL was initialized; otherwise it uses the app URL and app pool. It raises if no usable URL exists, gets the per-loop engine with `_engine_for`, opens a transaction with `_opened`, and yields the connection without setting the workspace GUC.

**Call relations**: Background sweeps use this to find rows that identify work across workspaces. It shares `_engine_for` and `_opened` with `workspace_tx`, but it intentionally skips the workspace pinning step.

*Call graph*: calls 2 internal fn (_engine_for, _opened).


##### `apply_migrations`  (lines 473–511)

```
def apply_migrations(url: str, pack: str | None=None) -> None
```

**Purpose**: Runs database schema migrations so the database tables match the code. It combines core migrations with active extension migrations and upgrades all migration heads.

**Data flow**: It receives a database URL and optionally an extension pack name. It builds an Alembic configuration, gathers migration locations, validates that revision IDs are not duplicated and each location has only one head, then runs `upgrade heads`. If the URL is SQLite, it calls `_seal_sqlite_journal` afterward.

**Call relations**: Command-line tools, startup jobs, or test fixtures call this outside the main async transaction flow. It asks `ufo.host.ext.loader.migration_locations` for extension migration folders and calls `_seal_sqlite_journal` for SQLite cleanup.

*Call graph*: calls 1 internal fn (_seal_sqlite_journal); 7 external calls (__init__, upgrade, from_config, Path, migration_locations, catch_warnings, simplefilter).


##### `_seal_sqlite_journal`  (lines 514–530)

```
def _seal_sqlite_journal(url: str) -> None
```

**Purpose**: Puts a migrated SQLite file into write-ahead logging mode before normal engines open it. This prevents later connections from racing to change the journal mode and hitting `database is locked`.

**Data flow**: It receives a SQLite URL, parses out the database file path, opens that file with the standard SQLite library, runs `pragma journal_mode=wal`, and closes the connection. If the URL does not name a file, it raises an error.

**Call relations**: `apply_migrations` calls this after running migrations on SQLite. It is not part of normal request transactions; it prepares the file for later use by engines created through `_build_engine`.

*Call graph*: called by 1 (apply_migrations); 2 external calls (make_url, connect).


##### `core_migration_head`  (lines 533–541)

```
def core_migration_head() -> str
```

**Purpose**: Returns the current head revision of the core migration graph. Developers use this when creating a new core migration that must chain onto the latest one.

**Data flow**: It builds an Alembic configuration pointing only at the core migrations, asks Alembic for the current head, raises if there is none, and returns the revision string.

**Call relations**: Migration tooling calls this when it needs the core schema’s latest revision. It intentionally ignores extension migration heads.

*Call graph*: 2 external calls (__init__, from_config).


##### `_sqlite_on_connect`  (lines 544–550)

```
def _sqlite_on_connect(dbapi_connection: Any, _connection_record: Any) -> None
```

**Purpose**: Applies required SQLite settings whenever a SQLite database connection is opened. These settings make local database behavior closer to what the rest of the code expects.

**Data flow**: It receives a raw SQLite database connection from SQLAlchemy’s event system. It disables the driver’s automatic transaction behavior, enables write-ahead logging, turns on foreign-key enforcement, sets a busy timeout, and closes the temporary cursor.

**Call relations**: `_build_engine` registers this as a SQLite `connect` listener. SQLAlchemy calls it automatically for each new SQLite connection.


##### `_sqlite_begin_immediate`  (lines 553–555)

```
def _sqlite_begin_immediate(connection: sa.Connection) -> None
```

**Purpose**: Starts SQLite transactions with `begin immediate` so writer conflicts queue at the start instead of deadlocking later. This is important because SQLite only allows one writer at a time.

**Data flow**: It receives a SQLAlchemy connection and sends the raw SQL command `begin immediate` to SQLite. The result is that the transaction claims the writer slot up front.

**Call relations**: `_build_engine` registers this as a SQLite `begin` listener. SQLAlchemy calls it automatically when a SQLite transaction begins.

*Call graph*: 1 external calls (exec_driver_sql).


### Feature Flags
Supplies safe workspace-aware feature flag checks with startup integration and default fallbacks.

### `core/src/ufo/flags.py`

`domain_logic` · `startup and cross-cutting feature checks`

Feature flags let a deployment turn features on or off without changing the code. This file is the project’s single doorway to that system. Without it, different parts of the code might talk to the flag service in different ways, wait too long for network calls, or accidentally turn on a feature when the flag service is unavailable.

At startup, the deployment may provide an OpenFeature provider. OpenFeature is a standard library interface for feature flag systems, like a universal plug adapter. If no provider is supplied, the built-in no-op provider stays in place, so every flag simply resolves to the default chosen by the code.

When code asks whether a flag is enabled, this file looks up the current workspace and uses that as the targeting key. That means a backend can say, for example, “turn this on only for workspace 123.” The flag value is read as a string, not a true boolean, because the supported flag backends store the served values as the strings "true" and "false". If the service is slow, errors, refuses the request, or returns some unusable value, the helper warns and returns the safe default. In practice, this means the system “fails closed”: a broken flag check does not crash a turn or accidentally offer a feature.

#### Function details

##### `init_flags`  (lines 37–42)

```
def init_flags(provider: FeatureProvider | None) -> None
```

**Purpose**: This function connects the deployment’s chosen feature-flag provider to the process-wide OpenFeature API. If no provider is given, it deliberately does nothing, leaving OpenFeature’s no-op provider in place so flag reads use their code defaults.

**Data flow**: It receives either a feature-flag provider object or None. If it receives None, nothing changes. If it receives a provider, it gives that provider to OpenFeature, which makes it the shared backend used by later flag lookups.

**Call relations**: This is meant to be called during startup after the deployment has chosen or loaded its flag backend. Its only handoff is to OpenFeature’s set_provider call, which stores the provider for later use by flag_enabled.

*Call graph*: 1 external calls (set_provider).


##### `flag_enabled`  (lines 45–75)

```
async def flag_enabled(flag: str, *, default: bool) -> bool
```

**Purpose**: This asynchronous function answers the practical question, “Is this feature flag on for the current workspace?” It protects callers from slow, broken, or confusing flag-service responses by returning the caller’s default when the flag cannot be read cleanly.

**Data flow**: It takes a flag name and a required default boolean. It reads the current workspace ID, builds an OpenFeature evaluation context from it, converts the default into the served string form "true" or "false", and asks the OpenFeature client for the flag value with a two-second limit. If the lookup raises an error, times out, reports an error code, or returns anything other than "true" or "false", it writes a warning and returns the default. If the returned value is usable, it converts the string back into a boolean and returns that.

**Call relations**: Application code calls this whenever it needs to decide whether to offer a gated feature. The function gathers workspace information from ws_current, talks to OpenFeature through get_client, uses asyncio.timeout so the check cannot hang forever, and reports unresolved or unreadable results through warn before handing a safe boolean back to the caller.

*Call graph*: 5 external calls (timeout, get_client, EvaluationContext, warn, ws_current).

## 📊 State Registers Touched

- `reg-database-schema-version` — The current shape and migration level of the database, so old stored data can be upgraded and all code agrees on table layouts.
- `reg-effective-config` — The merged deployment settings that tell the service how to start, where storage is, and which runtime options are enabled.
- `reg-feature-flags` — The shared on/off switches used to safely change product behavior without changing code.
- `reg-prompt-skill-environment` — The saved instructions, skills, environment documents, and fingerprints that shape what an agent sees for a turn.
- `reg-auth-tokens-sessions` — The login, surface, sandbox, and signing tokens that prove who a request belongs to and what it may access.
- `reg-authority-context` — The current acting identity for runtime work, saying which workspace, member, and agent are allowed to act.
- `reg-sandbox-handles` — The durable handles and leases that let conversations reconnect to their sandbox, files, ports, hosted previews, and work directories.
- `reg-execution-environment-policy` — The shared rules for where commands and tools may run, such as local execution, Docker, cloud sandboxes, terminals, and browser sessions.
- `reg-egress-proxy-policy` — The network access rules and proxy state that decide which outside hosts can be reached and when secrets may be attached.
- `reg-object-artifact-site-store` — The shared store of workspace objects, files, artifacts, previews, reports, websites, todos, and objective records.
- `reg-observability-trace` — The tracing, health, logging, and traceparent state used to connect work across turns, subagents, workers, and cleanup.
- `reg-database-connection-pools` — Process-global database engines, sessions, transaction handles, and connection pools shared by serving, workers, migrations, and cleanup code.
- `reg-blob-storage-state` — The raw byte/blob storage namespaces and content-addressed stored files that back artifacts, previews, environment files, workspace files, and deploy-wide assets.
- `reg-durable-workflow-checkpoints` — Saved workflow execution/checkpoint state used to resume, repair, cancel, or finalize long-running workflows after pauses, crashes, or worker handoff.
- `reg-external-client-connection-pools` — Process-global HTTP/gRPC client sessions, proxy clients, DNS/TLS state, and connection pools used for model providers, connectors, cloud storage, and sandbox services.
- `reg-user-feedback-buffer` — Collected user/operator feedback events, ratings, comments, and review signals used by telemetry, diagnostics, and offline improvement loops.
- `reg-update-check-state` — Cached software/version update-check results, last-check timestamps, retry timing, and dismissed or shown update notices for CLI and service maintenance flows.
- `reg-service-worker-lifecycle-state` — Process-local supervisor state for background loops and workers, including async task handles, startup readiness, shutdown signals, and drain status not represented by durable job tables.
