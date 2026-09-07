# Persistence, database schema, and durable stores  `stage-18` (cross-cutting infrastructure)

This stage is the system’s long-term memory. It is shared behind-the-scenes support used during startup, normal work, and recovery after changes or crashes. The database holds structured records such as workspaces, conversations, schedules, notifications, and monitors. Blob storage holds large files that do not fit neatly in database rows.

The core database doorway is core/src/ufo/db.py. It opens safe database sessions, runs migrations that update the schema, and keeps each workspace’s data separated. core/src/ufo/schema/tables.py is the main blueprint for the core tables, while schema/__init__.py simply makes those definitions importable. core/src/ufo/blob.py is the file cabinet for large byte data, using local disk in development or S3 in production. core/src/ufo/harness/durability.py helps old saved workflow records remain readable after code moves.

The extension stores add specialized shelves to the same memory system: notifications manage inbox rows, enrichment stores profile and permission data, monitors store repeating watch jobs, pauses store conversations waiting to resume, and schedules store recurring tasks and safely hand due work to background runners.

## Files in this stage

### Core persistence foundations
Shared storage, database access, workflow durability, and core schema definitions provide the persistence base used across the system.

### `core/src/ufo/blob.py`

`io_transport` · `cross-cutting: used whenever blobs are stored, streamed, listed, or served`

A “blob” here means an opaque bundle of bytes: an uploaded file, generated artifact, static asset, transcript record, or preview image. This file is the storage adapter for those blobs. It hides the difference between a local folder and S3, which is Amazon-style object storage, so the rest of the system can simply say “put these bytes under this key” or “stream this key back.”

The main idea is like a mailroom with two possible buildings. In development, `FilesystemBlobStore` turns each key into a file under a configured root folder. It writes through a temporary file and then swaps it into place, so readers do not see half-written files. In deployment, `S3BlobStore` talks to S3 asynchronously and can upload or download in chunks so large files do not need to fit in memory at once.

On top of those raw backends are safety wrappers. `WorkspaceBlobStore` automatically adds `workspaces/<workspace id>/` to every key, using the currently active workspace. This prevents one workspace from reading or writing another workspace’s blobs by accident. `FleetBlobStore` is the opposite: it is for deploy-wide files only, and only allows known prefixes such as static assets and terminal payloads.

The file also creates presigned URLs for S3. These are temporary URLs that let a sandbox upload or download exactly one object without receiving broad storage credentials.

#### Function details

##### `BlobStore.put`  (lines 54–54)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Defines the common promise that any blob store can save a complete byte string under a key. Code can depend on this promise without caring whether the bytes go to disk or S3.

**Data flow**: A key and a block of bytes go in. The concrete store writes those bytes at that key. Nothing is returned, but the stored object should exist afterward.

**Call relations**: Web asset publishing calls this through the shared interface, so the publishing code can work with any backend that follows the blob-store contract.

*Call graph*: called by 1 (_publish_assets).


##### `BlobStore.get`  (lines 56–56)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Defines the common promise that any blob store can read a complete object back as bytes. It is the simple whole-file read form.

**Data flow**: A key goes in. The concrete store finds the stored object and returns its bytes, or raises a not-found error if it is missing.

**Call relations**: Transcript readers, Slack identity loading, and web asset serving rely on this interface when they need the whole stored object at once.

*Call graph*: called by 4 (read_compaction_after, read_compaction_record, read_identity, _stored_asset).


##### `BlobStore.exists`  (lines 58–58)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Defines the common promise that any blob store can answer whether a key currently names a stored object. This lets callers avoid unnecessary reads or writes.

**Data flow**: A key goes in. The concrete store checks its storage area. A true or false answer comes out.

**Call relations**: Slack and web surfaces use this contract to decide whether stored identities or assets are already present before reading or publishing them.

*Call graph*: called by 3 (read_identity, _publish_assets, _stored_asset).


##### `BlobStore.delete`  (lines 60–62)

```
async def delete(self, key: str) -> None
```

**Purpose**: Defines the common promise that any blob store can remove an object. Deleting a missing object is intentionally harmless, which makes retries safe.

**Data flow**: A key goes in. The concrete store removes the matching object if it exists. Nothing is returned, and no error is expected just because the key was absent.

**Call relations**: This is part of the shared storage interface. Concrete filesystem, S3, workspace, and fleet stores provide the actual deletion behavior.


##### `BlobStore.get_stream`  (lines 64–64)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines the common promise that any blob store can read an object in pieces. This is used for large files so the program does not need to load everything into memory.

**Data flow**: A key goes in. The concrete store opens the stored object and yields byte chunks one at a time. The caller receives a stream of chunks until the object ends.

**Call relations**: This is the streaming read half of the shared interface. Concrete stores implement it using either file reads or S3 response chunks.


##### `BlobStore.put_stream`  (lines 66–66)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Defines the common promise that any blob store can write an object from a stream of byte chunks. It is the large-file write form.

**Data flow**: A key and an async stream of chunks go in. The concrete store consumes the chunks and writes them as one stored object. Nothing is returned when the write completes.

**Call relations**: Concrete stores use this interface when callers, such as artifact storage code, produce bytes gradually instead of all at once.


##### `BlobStore.list`  (lines 68–72)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Defines the common promise that any blob store can list objects under a required key prefix. It deliberately avoids whole-store scans by requiring callers to name the area they want.

**Data flow**: A prefix goes in. The concrete store returns sorted `BlobEntry` records describing matching keys, sizes, and modification times, capped at a fixed maximum.

**Call relations**: Web asset publishing uses this interface to inspect already stored assets under a known prefix without knowing which backend is underneath.

*Call graph*: called by 1 (_publish_assets).


##### `FilesystemBlobStore.put`  (lines 81–86)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Writes a complete blob to the local filesystem. It uses a temporary file first so a crash or interrupted write does not leave a half-finished final file.

**Data flow**: A key and bytes go in. The key is converted to a safe path under the store root, parent folders are created, bytes are written to a uniquely named temporary file, and that file replaces the final path. Nothing is returned.

**Call relations**: This is the filesystem implementation of the shared `put` operation. It depends on `_resolve` to keep the path inside the configured blob root.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.get`  (lines 88–93)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a complete blob from the local filesystem. It translates a missing file into the project’s own `BlobNotFound` error.

**Data flow**: A key goes in. The key is resolved to a safe file path, the file is read in a worker thread, and its bytes are returned. If the file is absent, `BlobNotFound` comes out instead of a raw filesystem error.

**Call relations**: This is the filesystem implementation of the shared `get` operation. It uses `_resolve` before touching disk.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.exists`  (lines 95–97)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a local-file blob exists. It answers only whether the resolved key is a file.

**Data flow**: A key goes in. The key is safely resolved under the blob root, the filesystem is checked, and a boolean answer comes out.

**Call relations**: This is the filesystem implementation of the shared `exists` operation. `_resolve` provides the safety boundary before the disk check happens.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.delete`  (lines 99–101)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes a local-file blob if it exists. It is safe to call even when the file is already gone.

**Data flow**: A key goes in. The key is safely resolved to a file path, and that file is unlinked with missing files ignored. Nothing is returned.

**Call relations**: This is the filesystem implementation of the shared `delete` operation. It relies on `_resolve` so deletion cannot escape the blob directory.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.get_stream`  (lines 103–116)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads a local blob in fixed-size chunks. This is for large files that should not be loaded into memory all at once.

**Data flow**: A key goes in. The matching file is opened, read chunk by chunk, and each chunk is yielded to the caller. The file handle is closed at the end or after an error.

**Call relations**: This is the filesystem implementation of streaming reads. It uses `_resolve` for path safety and raises `BlobNotFound` if the file cannot be opened.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.put_stream`  (lines 118–131)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes a local blob from incoming chunks. Like the whole-byte write, it writes to a temporary file first and then atomically swaps it into place.

**Data flow**: A key and a stream of byte chunks go in. The key becomes a safe path, chunks are written to a temporary file, and the temporary file replaces the final file when all chunks arrive. If anything fails, the temporary file is cleaned up.

**Call relations**: This is the filesystem implementation of streaming writes. It calls `_resolve` and uses temporary-file cleanup so callers do not leave corrupt blobs behind.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.list`  (lines 133–136)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists local blobs under a required prefix. Requiring a prefix prevents accidental scans of the entire storage tree.

**Data flow**: A non-empty prefix goes in. The actual directory walk is run in a worker thread, and a tuple of matching `BlobEntry` records comes out. An empty prefix raises an error.

**Call relations**: This is the filesystem implementation of the shared `list` operation. It hands the real walking work to `_walk` so disk traversal does not block the async event loop.

*Call graph*: 1 external calls (to_thread).


##### `FilesystemBlobStore._walk`  (lines 138–159)

```
def _walk(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Performs the actual filesystem search for `list`. It walks the relevant directory, ignores temporary files, and builds the public listing records.

**Data flow**: A prefix goes in. The method finds the contained root, chooses the directory to inspect, walks files below it, keeps only keys that match the prefix, records size and modification time, sorts by key, and returns up to the maximum allowed entries.

**Call relations**: `FilesystemBlobStore.list` calls this inside a worker thread. It uses `_contained_root` and `_resolve` to keep the walk tied to the configured store.

*Call graph*: calls 2 internal fn (_contained_root, _resolve); 4 external calls (__init__, fromtimestamp, walk, Path).


##### `FilesystemBlobStore._resolve`  (lines 161–166)

```
def _resolve(self, key: str) -> Path
```

**Purpose**: Turns a blob key into a safe filesystem path. Its main job is to stop keys like `../secret` from escaping the blob root.

**Data flow**: A key goes in. The configured root is canonicalized, the key is joined to it and resolved, and the resulting path is returned only if it stays under the root. Unsafe keys raise an error.

**Call relations**: Every filesystem read, write, delete, stream, and walk uses this before touching disk. It depends on `_contained_root` to know the real root path.

*Call graph*: calls 1 internal fn (_contained_root); called by 7 (_walk, delete, exists, get, get_stream, put, put_stream).


##### `FilesystemBlobStore._contained_root`  (lines 168–181)

```
def _contained_root(self) -> Path
```

**Purpose**: Finds the real filesystem root used by the blob store. It allows the root itself to be a symlink, which is common in deployments, but still checks that the configured location is suitable.

**Data flow**: The configured root path is read from the store. The containment helper validates and canonicalizes it when it exists; if it has not been created yet, the resolved intended path is returned. The caller receives a root path to compare other paths against.

**Call relations**: `_resolve` and `_walk` call this whenever they need the authoritative blob root. It is the safety anchor for all filesystem blob paths.

*Call graph*: called by 2 (_resolve, _walk); 1 external calls (configured_root).


##### `_is_missing_key`  (lines 184–185)

```
def _is_missing_key(error: ClientError) -> bool
```

**Purpose**: Recognizes S3 errors that mean “this object does not exist.” Different S3-compatible services use slightly different error codes, so this helper centralizes the check.

**Data flow**: An S3 `ClientError` goes in. The function reads the error code from the response and returns true if it matches one of the known missing-object codes.

**Call relations**: S3 `get`, `exists`, and streaming `get_stream` call this when S3 returns an error, so they can turn missing objects into `BlobNotFound` or `False` while letting real failures pass through.

*Call graph*: called by 3 (exists, get, get_stream).


##### `S3BlobStore.put`  (lines 207–209)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Writes a complete blob to an S3 bucket in one request. This is the S3 version of the simple whole-byte save operation.

**Data flow**: A key and bytes go in. The method gets or creates the async S3 client, sends a `put_object` request to the configured bucket, and returns nothing after S3 accepts it.

**Call relations**: This implements the shared `put` operation for S3. It relies on `_client` so client creation is reused instead of repeated for every call.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get`  (lines 211–221)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a complete blob from S3. It returns all bytes at once and translates S3’s missing-object response into `BlobNotFound`.

**Data flow**: A key goes in. The method obtains the S3 client, requests the object, reads the response body fully, and returns the bytes. If S3 says the key is missing, `BlobNotFound` is raised.

**Call relations**: This implements the shared `get` operation for S3. It uses `_client` for transport and `_is_missing_key` to classify S3 errors.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.exists`  (lines 223–231)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether an object exists in S3 without downloading it. It uses S3’s metadata check rather than reading the body.

**Data flow**: A key goes in. The method asks S3 for the object header. A successful response becomes `True`; a known missing-key error becomes `False`; other errors are raised.

**Call relations**: This implements the shared `exists` operation for S3. It uses `_client` to talk to S3 and `_is_missing_key` to separate absence from real failures.

*Call graph*: calls 2 internal fn (_client, _is_missing_key).


##### `S3BlobStore.delete`  (lines 233–235)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes an object from S3. S3 deletion is naturally tolerant of missing keys, matching the blob-store contract.

**Data flow**: A key goes in. The method gets the S3 client, sends a delete request for that bucket and key, and returns nothing.

**Call relations**: This implements the shared `delete` operation for S3 and uses `_client` for the reusable S3 connection.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get_stream`  (lines 237–248)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams an S3 object in chunks. This lets callers serve or process large files without first holding the whole object in memory.

**Data flow**: A key goes in. The method opens the S3 object body and yields chunks of bytes until S3 has no more data. If the object is missing, `BlobNotFound` is raised.

**Call relations**: This implements the shared streaming-read operation for S3. It uses `_client` for the request and `_is_missing_key` to translate missing-object errors.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.put_stream`  (lines 250–294)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Uploads streamed bytes to S3, switching to S3 multipart upload for larger content. Multipart upload means the object is sent as numbered pieces and then finalized as one object.

**Data flow**: A key and a stream of chunks go in. Small total content is buffered and sent with one `put_object` request. Once enough data accumulates, the method starts a multipart upload, sends each part, records S3’s part tags, and completes the upload. If anything fails after multipart starts, it aborts the upload.

**Call relations**: This implements the shared streaming-write operation for S3. It relies on `_client`, and its cleanup path prevents abandoned partial uploads when a caller or network fails.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_put`  (lines 296–320)

```
async def presigned_put(self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary upload URL for exactly one measured object. The URL is signed for a specific key, byte length, checksum, and expiry time.

**Data flow**: A key, expected size, SHA-256 checksum, and lifetime go in. The S3 client signs a PUT URL that S3 will accept only if the request matches those details. The URL string comes out.

**Call relations**: Workspace artifact storage reaches this through `WorkspaceBlobStore.presigned_put` when an untrusted sandbox should upload directly to S3 without receiving full credentials.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_put_unmeasured`  (lines 322–332)

```
async def presigned_put_unmeasured(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary upload URL for a fixed key when the final byte length is not known yet. It still limits the holder to that one key and expiry time.

**Data flow**: A key and lifetime go in. The S3 client signs a PUT URL without size or checksum restrictions. The URL string comes out.

**Call relations**: Preview rendering reaches this through `WorkspaceBlobStore.presigned_put_unmeasured` when generated output size is only known after rendering.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_get`  (lines 334–341)

```
async def presigned_get(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary download URL for one S3 object. Anyone holding the URL can read that object until the URL expires.

**Data flow**: A key and lifetime go in. The S3 client signs a GET URL for that bucket and key. The URL string comes out.

**Call relations**: Preview rendering reaches this through `WorkspaceBlobStore.presigned_get` when a renderer or browser needs short-lived access to a stored object.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.put_host`  (lines 343–354)

```
async def put_host(self) -> str
```

**Purpose**: Reports the hostname used by presigned S3 upload URLs. The sandbox egress proxy uses this to know which host it should allow.

**Data flow**: The method reads the endpoint URL from the actual S3 client, extracts its hostname, and adjusts it for AWS virtual-hosted bucket addressing when needed. The bare hostname comes out.

**Call relations**: This depends on `_client` so the allowed proxy host is derived from the same client that signs URLs, avoiding mismatches between configuration and generated URLs.

*Call graph*: calls 1 internal fn (_client); 1 external calls (urlsplit).


##### `S3BlobStore.list`  (lines 356–373)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists S3 objects under a required prefix. It gathers object key, size, and modification time while respecting the project’s maximum listing size.

**Data flow**: A non-empty prefix goes in. The method pages through S3 `list_objects_v2` results, converts each object into a `BlobEntry`, stops once enough entries are collected, and returns the capped tuple.

**Call relations**: This implements the shared `list` operation for S3. It uses `_client` for the paginator and matches the filesystem backend’s prefix-required behavior.

*Call graph*: calls 1 internal fn (_client); 1 external calls (__init__).


##### `S3BlobStore.close`  (lines 375–381)

```
async def close(self) -> None
```

**Purpose**: Closes the cached S3 client for the currently running async event loop. This releases network resources when that loop is done with the store.

**Data flow**: No explicit input is passed. The method finds the current event loop, removes its cached client and lock from the store, and closes the client if one existed. Nothing is returned.

**Call relations**: This is the cleanup companion to `_client`, which caches one S3 client per event loop.

*Call graph*: 1 external calls (get_running_loop).


##### `S3BlobStore._client`  (lines 383–409)

```
async def _client(self) -> AioBaseClient
```

**Purpose**: Returns the reusable S3 client for the current async event loop, creating it if needed. Reuse matters because building S3 clients is relatively expensive and each async client belongs to the loop it was created on.

**Data flow**: The current event loop is read. If a client is already cached for that loop, it is returned. Otherwise a lock prevents duplicate creation, a new aiobotocore S3 client is configured with the correct signing and addressing style, cached, and returned.

**Call relations**: Nearly every S3 operation calls this before talking to S3. It is the shared gateway that keeps blob operations efficient and makes presigned URLs use consistent S3 settings.

*Call graph*: called by 11 (delete, exists, get, get_stream, list, presigned_get, presigned_put, presigned_put_unmeasured, put, put_host (+1 more)); 3 external calls (get_session, Lock, get_running_loop).


##### `WorkspaceBlobStore.put`  (lines 422–423)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Stores bytes under the currently active workspace. Callers pass a workspace-relative key, and this method prevents them from choosing another workspace’s prefix themselves.

**Data flow**: A relative key and bytes go in. `_full` adds the current workspace prefix, then the backend stores the bytes at that full key. Nothing is returned.

**Call relations**: Environment document and file storage use this when saving workspace-owned data. The method delegates actual storage to either the filesystem or S3 backend.

*Call graph*: calls 1 internal fn (_full); called by 2 (store_environment_document, store_environment_file).


##### `WorkspaceBlobStore.get`  (lines 425–426)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads bytes from the currently active workspace. It hides the full storage prefix from callers.

**Data flow**: A relative key goes in. `_full` expands it to `workspaces/<id>/...`, the backend reads that object, and the bytes are returned.

**Call relations**: Environment loading uses this to retrieve workspace-owned documents and files. The backend supplies the actual disk or S3 read.

*Call graph*: calls 1 internal fn (_full); called by 2 (load_environment_document, load_environment_file).


##### `WorkspaceBlobStore.exists`  (lines 428–429)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a workspace-relative blob exists in the active workspace.

**Data flow**: A relative key goes in. `_full` adds the active workspace prefix, the backend checks that full key, and a boolean answer comes out.

**Call relations**: This is the workspace-scoped version of the shared existence check. It sits between callers and the raw backend to enforce workspace boundaries.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.delete`  (lines 431–432)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes a blob from the active workspace. It cannot be used to delete another workspace’s object because the prefix is supplied automatically.

**Data flow**: A relative key goes in. `_full` turns it into the full workspace key, the backend deletes that object if present, and nothing is returned.

**Call relations**: This is the workspace-scoped version of deletion. It delegates to the configured backend after applying the workspace prefix.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.get_stream`  (lines 434–438)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Opens a streamed read for a blob in the active workspace. The workspace prefix is resolved immediately so the stream can keep working after the workspace scope exits.

**Data flow**: A relative key goes in. `_full` captures the full workspace key right away, and the backend returns a stream of byte chunks for that full key.

**Call relations**: Runtime context code uses this to read member blob text. This method wraps the backend stream with workspace safety.

*Call graph*: calls 1 internal fn (_full); called by 1 (_member_blob_text).


##### `WorkspaceBlobStore.put_stream`  (lines 440–441)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed bytes into the active workspace. It is the workspace-safe path for large artifact uploads.

**Data flow**: A relative key and chunk stream go in. `_full` adds the workspace prefix, and the backend consumes the chunks into the full key. Nothing is returned.

**Call relations**: Artifact storage code calls this when the backend should receive streamed bytes through the application instead of direct S3 upload.

*Call graph*: calls 1 internal fn (_full); called by 1 (store_artifact).


##### `WorkspaceBlobStore.list`  (lines 443–448)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists blobs under a prefix inside the active workspace, but returns keys relative to that workspace. This keeps callers from seeing or depending on the internal `workspaces/<id>/` prefix.

**Data flow**: A non-empty relative prefix goes in. `_full` builds the workspace root and search prefix, the backend lists full keys, and each result is rewritten so the key no longer includes the workspace root.

**Call relations**: This is the workspace-scoped version of listing. It uses the backend list operation, then adapts the returned `BlobEntry` records for workspace-relative callers.

*Call graph*: calls 1 internal fn (_full); 1 external calls (replace).


##### `WorkspaceBlobStore.presigned_put`  (lines 450–461)

```
async def presigned_put(self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a measured S3 upload URL for a blob in the active workspace. It is only valid when the underlying backend is S3.

**Data flow**: A relative key, expected size, checksum, and lifetime go in. `_full` adds the workspace prefix, and the S3 backend signs a URL for that full key. If the backend is not S3, an error is raised.

**Call relations**: Artifact storage calls this when a sandbox should upload directly to S3. This wrapper ensures the direct upload still lands inside the current workspace.

*Call graph*: calls 1 internal fn (_full); called by 1 (store_artifact).


##### `WorkspaceBlobStore.presigned_put_unmeasured`  (lines 463–469)

```
async def presigned_put_unmeasured(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates an unmeasured S3 upload URL for a blob in the active workspace. It is used when the writer controls the content but does not know its final size yet.

**Data flow**: A relative key and lifetime go in. `_full` adds the workspace prefix, and the S3 backend signs a PUT URL for that full key. Non-S3 backends cause an error.

**Call relations**: The document preview renderer calls this for generated covers whose output size is not known before rendering.

*Call graph*: calls 1 internal fn (_full); called by 1 (render_document_cover).


##### `WorkspaceBlobStore.presigned_get`  (lines 471–478)

```
async def presigned_get(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary S3 download URL for a blob in the active workspace. It is only available with the S3 backend.

**Data flow**: A relative key and lifetime go in. `_full` adds the workspace prefix, and the S3 backend signs a GET URL for that full key. Non-S3 backends cause an error.

**Call relations**: The document preview renderer calls this when it needs short-lived read access to a workspace blob.

*Call graph*: calls 1 internal fn (_full); called by 1 (render_document_cover).


##### `WorkspaceBlobStore._full`  (lines 480–483)

```
def _full(self, key: str) -> str
```

**Purpose**: Builds the real storage key for a workspace-relative key. It is the guardrail that keeps callers inside the currently bound workspace.

**Data flow**: A caller-provided key goes in. If it already starts with the reserved workspace prefix, the method rejects it. Otherwise it reads the current workspace id and returns `workspaces/<id>/<key>`.

**Call relations**: Every workspace store operation calls this before touching the backend. It depends on the current workspace context, so unscoped calls fail instead of silently using the wrong workspace.

*Call graph*: called by 10 (delete, exists, get, get_stream, list, presigned_get, presigned_put, presigned_put_unmeasured, put, put_stream); 1 external calls (ws_current).


##### `FleetBlobStore.put`  (lines 494–495)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Stores deploy-wide bytes under an approved fleet prefix. This is for data that belongs to the whole installation, not to one workspace.

**Data flow**: A key and bytes go in. `_checked` confirms the key starts with an allowed fleet prefix, then the backend writes the bytes. Nothing is returned.

**Call relations**: This is the fleet-scoped wrapper around the backend `put` operation. It prevents fleet storage from being used as a back door into workspace namespaces.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.get`  (lines 497–498)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a deploy-wide blob from an approved fleet namespace.

**Data flow**: A key goes in. `_checked` verifies the prefix, the backend reads the object, and the bytes are returned.

**Call relations**: This is the fleet-scoped wrapper around backend reads. The prefix check happens before the filesystem or S3 backend is reached.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.exists`  (lines 500–501)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether an approved deploy-wide blob exists.

**Data flow**: A key goes in. `_checked` confirms it belongs to a fleet namespace, the backend checks the full key, and a boolean comes out.

**Call relations**: This wraps the backend existence check with the fleet namespace rule.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.delete`  (lines 503–504)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes a deploy-wide blob, but only from allowed fleet namespaces.

**Data flow**: A key goes in. `_checked` validates the prefix, the backend deletes the object if present, and nothing is returned.

**Call relations**: This wraps backend deletion and applies the same fleet-only boundary used by all fleet operations.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.get_stream`  (lines 506–507)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a deploy-wide blob from an approved fleet namespace.

**Data flow**: A key goes in. `_checked` validates it, and the backend returns a stream of byte chunks for that key.

**Call relations**: This is the fleet-scoped streaming read wrapper. It delegates chunk delivery to the configured backend.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.put_stream`  (lines 509–510)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed bytes to a deploy-wide blob under an approved fleet prefix.

**Data flow**: A key and chunk stream go in. `_checked` validates the key, and the backend consumes the chunks into that object. Nothing is returned.

**Call relations**: This is the fleet-scoped streaming write wrapper. It keeps streamed fleet data within the allowed namespace before handing it to disk or S3.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.list`  (lines 512–513)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists deploy-wide blobs under an approved fleet prefix.

**Data flow**: A prefix goes in. `_checked` confirms it belongs to one of the allowed fleet namespaces, and the backend returns matching `BlobEntry` records.

**Call relations**: This wraps backend listing with fleet namespace validation, so callers cannot list workspace data through the fleet store.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore._checked`  (lines 515–518)

```
def _checked(self, key: str) -> str
```

**Purpose**: Verifies that a fleet key is in one of the allowed deploy-wide namespaces. It is a simple but important boundary check.

**Data flow**: A key goes in. If it starts with an allowed prefix such as `static/`, `term/`, or `apps/`, the same key is returned. Otherwise an error is raised.

**Call relations**: Every fleet store operation calls this before delegating to the backend. It is the single gate that keeps fleet and workspace storage separate.

*Call graph*: called by 7 (delete, exists, get, get_stream, list, put, put_stream).


##### `blob_store_for`  (lines 521–533)

```
def blob_store_for(config: BlobConfig) -> FilesystemBlobStore | S3BlobStore
```

**Purpose**: Builds the raw blob backend described by configuration. It chooses local filesystem storage or S3 storage and checks that the required settings are present.

**Data flow**: A `BlobConfig` goes in. If the backend is `filesystem`, the configured root becomes a `FilesystemBlobStore`; if it is `s3`, the bucket and optional endpoint or region become an `S3BlobStore`. Missing required fields raise clear errors.

**Call relations**: Startup or setup code uses this factory to create the storage backend that workspace and fleet wrappers can then sit on top of.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/db.py`

`io_transport` · `startup, request handling, background jobs, migrations, teardown`

This file protects the project’s main tenancy boundary: many workspaces share one running service, but each database transaction must be tied to the right workspace. For PostgreSQL, it uses row-level security, meaning database rules decide which rows are visible. Before a normal workspace transaction runs, this file sets a short-lived database setting named app.workspace_id, so the database can filter rows for that workspace. The setting is local to the transaction, so it disappears when the transaction ends and cannot leak through a reused pooled connection.

The file also builds and remembers SQLAlchemy async engines. An engine is the object that owns a pool of database connections. Because async database connections belong to the event loop that created them, this file keeps a separate engine per event loop and database URL. That avoids using a connection on the wrong loop.

There are two main transaction doors. workspace_tx is the normal, workspace-scoped door. owner_tx is the special cross-workspace door used by background sweeps to list work that must later be re-opened under the right workspace. The file also includes startup checks, shutdown cleanup, SQLite-specific safety settings, and Alembic migration support so the database schema is at the right version before the app uses it.

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


##### `_dispose_on_this_loop`  (lines 276–300)

```
def _dispose_on_this_loop(engine: AsyncEngine) -> None
```

*Call graph*: 3 external calls (ensure_future, get_running_loop, dispose).


##### `_dispose_on_this_loop.finished`  (lines 295–298)

```
def finished(done: asyncio.Task[None]) -> None
```


##### `dispose_loop_engines`  (lines 303–313)

```
async def dispose_loop_engines() -> None
```

*Call graph*: 1 external calls (get_running_loop).


##### `_stopping`  (lines 316–323)

```
def _stopping() -> bool
```

*Call graph*: called by 1 (_opened); 1 external calls (current_task).


##### `_await_opening`  (lines 326–337)

```
async def _await_opening(opening: asyncio.Future[AsyncConnection]) -> tuple[AsyncConnection, asyncio.CancelledError | None]
```

*Call graph*: called by 1 (_opened); 1 external calls (shield).


##### `_await_close`  (lines 340–348)

```
async def _await_close(close: asyncio.Future[bool | None]) -> asyncio.CancelledError | None
```

*Call graph*: called by 1 (_opened); 1 external calls (shield).


##### `_opened`  (lines 352–416)

```
async def _opened(engine: AsyncEngine, path: str) -> AsyncIterator[AsyncConnection]
```

*Call graph*: calls 3 internal fn (_await_close, _await_opening, _stopping); called by 2 (owner_tx, workspace_tx); 7 external calls (Lock, ensure_future, AsyncExitStack, begin, monotonic, emit_histogram, emit_metric).


##### `workspace_tx`  (lines 420–430)

```
async def workspace_tx() -> AsyncIterator[AsyncConnection]
```

*Call graph*: calls 2 internal fn (_engine_for, _opened); 1 external calls (text).


##### `failed_statement`  (lines 433–453)

```
def failed_statement(error: BaseException) -> dict[str, str]
```


##### `owner_tx`  (lines 457–470)

```
async def owner_tx() -> AsyncIterator[AsyncConnection]
```

*Call graph*: calls 2 internal fn (_engine_for, _opened).


##### `apply_migrations`  (lines 473–511)

```
def apply_migrations(url: str, pack: str | None=None) -> None
```

*Call graph*: calls 1 internal fn (_seal_sqlite_journal); 7 external calls (__init__, upgrade, from_config, Path, migration_locations, catch_warnings, simplefilter).


##### `_seal_sqlite_journal`  (lines 514–530)

```
def _seal_sqlite_journal(url: str) -> None
```

*Call graph*: called by 1 (apply_migrations); 2 external calls (make_url, connect).


##### `core_migration_head`  (lines 533–541)

```
def core_migration_head() -> str
```

*Call graph*: 2 external calls (__init__, from_config).


##### `_sqlite_on_connect`  (lines 544–550)

```
def _sqlite_on_connect(dbapi_connection: Any, _connection_record: Any) -> None
```


##### `_sqlite_begin_immediate`  (lines 553–555)

```
def _sqlite_begin_immediate(connection: sa.Connection) -> None
```

*Call graph*: 1 external calls (exec_driver_sql).


### `core/src/ufo/harness/durability.py`

`io_transport` · `cross-cutting during DBOS persistence, replay, and crash recovery`

DBOS stores workflow inputs, step results, and errors in a database so work can resume after a crash. The problem is that this stored data may be read by a later version of the code, not the exact version that wrote it. A normal Python pickle is brittle here: it can recreate an object without running its normal validation, so if a Pydantic model gained or lost fields between releases, replay can fail in confusing ways. Pydantic is a data-model library that validates fields and applies defaults.

This file solves that by wrapping pickle with safer rules. When it sees a Pydantic BaseModel, it saves the model's class and its field values, then rebuilds it through Pydantic validation when loading. That means new default fields are filled in, removed fields are ignored, and truly missing required fields fail in a clear place.

It also protects against code moves. Pickled data stores the old module path for each class, like an address on an envelope. If the class moved, the MOVED_MODULES table acts like mail forwarding, sending the loader from the old address to the new one. Without this file, recovered workflows could return raw unreadable strings, fail after deploys, or break when packages are reorganized.

#### Function details

##### `replay_safe_client`  (lines 176–180)

```
def replay_safe_client(system_database_url: str) -> DBOSClient
```

**Purpose**: This is the approved way to create a DBOSClient for this project. It makes sure the client knows how to read and write the project's replay-safe saved data instead of using DBOS's default serializer.

**Data flow**: It receives the system database URL. It creates a ReplaySafeSerializer, gives both the URL and serializer to DBOSClient, and returns the ready-to-use client. The database is not changed by this function directly; it prepares the client that will later read and write rows.

**Call relations**: Startup or setup code calls this when it needs a DBOS client. The function hands off construction to ReplaySafeSerializer and DBOSClient so every later DBOS read and write uses the same named format.

*Call graph*: 2 external calls (__init__, DBOSClient).


##### `_rebuild`  (lines 183–184)

```
def _rebuild(model_class: type[BaseModel], fields: dict[str, object]) -> BaseModel
```

**Purpose**: This rebuilds a saved Pydantic model using the current version of its class. It exists so old stored model data can be validated again and can pick up current defaults.

**Data flow**: It receives a model class and a dictionary of saved field values. It asks that class to validate the fields, which creates a proper model object according to today's class definition. The result is the rebuilt model.

**Call relations**: The custom pickler records this function as the recipe for rebuilding Pydantic models. Later, when deserialization replays that recipe, this function turns the stored class-and-fields pair back into a real model object.


##### `_ModelPickler.reducer_override`  (lines 188–191)

```
def reducer_override(self, obj: object) -> tuple[Callable[..., object], tuple[object, ...]]
```

**Purpose**: This tells pickle to save Pydantic models in a safer custom shape. Instead of freezing the model's internal state exactly as-is, it records enough information to rebuild the model through validation later.

**Data flow**: It receives each object that pickle is about to save. If the object is a Pydantic BaseModel, it returns a recipe: call _rebuild with the object's class and current field dictionary. If the object is not a Pydantic model, it tells pickle to use its normal behavior.

**Call relations**: ReplaySafeSerializer.serialize uses _ModelPickler to write data. During that write, pickle consults this method whenever it needs to decide how an object should be represented.


##### `_CompatUnpickler.find_class`  (lines 195–196)

```
def find_class(self, module: str, name: str) -> object
```

**Purpose**: This lets old saved data still find classes after modules have been renamed or moved. It is the compatibility bridge for historic module paths.

**Data flow**: It receives the module name and class or function name recorded in the saved data. It looks up the module name in MOVED_MODULES; if there is a newer location, it substitutes that. It then asks Python's normal unpickler to load the named item from the resolved module.

**Call relations**: ReplaySafeSerializer.deserialize uses _CompatUnpickler when reading saved data. As objects are reconstructed, this method is called whenever pickle needs to locate a class or function by name.


##### `ReplaySafeSerializer.name`  (lines 202–203)

```
def name(self) -> str
```

**Purpose**: This returns the stable name DBOS uses to label data written with this serializer. That label tells future DBOS clients which decoding rules to use.

**Data flow**: It takes no outside input beyond the serializer instance. It returns the constant serializer name used by this project.

**Call relations**: DBOS calls this as part of its serializer interface. The name ties database rows to ReplaySafeSerializer so later reads know which serializer should decode them.


##### `ReplaySafeSerializer.serialize`  (lines 205–208)

```
def serialize(self, data: object) -> str
```

**Purpose**: This turns a Python object into a database-friendly text string using the replay-safe pickle rules. It is used when DBOS needs to persist workflow data.

**Data flow**: It receives any Python object. It creates an in-memory byte buffer, uses _ModelPickler to pickle the object into bytes, converts those bytes to base64 text, and returns that text. Base64 is a common way to represent arbitrary bytes using safe printable characters.

**Call relations**: DBOS calls this serializer method when recording data. The method hands the actual object traversal to _ModelPickler, which applies the special Pydantic model rule before the bytes are encoded for storage.

*Call graph*: 3 external calls (__init__, b64encode, BytesIO).


##### `ReplaySafeSerializer.deserialize`  (lines 210–211)

```
def deserialize(self, serialized_data: str) -> object
```

**Purpose**: This turns stored text back into a Python object using compatibility rules for moved modules and safely rebuilt models. It is used when DBOS replays or recovers saved workflow data.

**Data flow**: It receives the base64 text stored in the database. It decodes the text back into bytes, wraps those bytes in an in-memory stream, and uses _CompatUnpickler to reconstruct the original object graph. The returned value is the usable Python object.

**Call relations**: DBOS calls this serializer method when loading recorded data. The method delegates object reconstruction to _CompatUnpickler, which can redirect old module names while pickle rebuilds the saved objects.

*Call graph*: 3 external calls (__init__, b64decode, BytesIO).


### `core/src/ufo/schema/__init__.py`

`other` · `cross-cutting`

This is an empty Python package marker file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, that means code elsewhere can refer to modules under `core/src/ufo/schema` using normal Python import paths.

There is no runtime logic in this file: no functions, classes, constants, or setup steps. Its value is structural rather than behavioral. A useful analogy is a label on a drawer: the label does not contain the tools, but it tells the rest of the system that this drawer exists and can be opened in an organized way.

Without this file, depending on the Python version and packaging setup, imports involving `ufo.schema` could become less explicit or fail in some environments. Keeping it present makes the package layout clear and stable.


### `core/src/ufo/schema/tables.py`

`data_model` · `database setup, migrations, and runtime database access`

Think of this file as the blueprint for the project’s main filing cabinet. It does not store data itself. Instead, it tells SQLAlchemy, the Python database toolkit, exactly what drawers exist, what labels are allowed, and which records must point to other records.

The file creates one shared `metadata` object, then fills it with table definitions. These tables cover the system’s main concepts: workspaces, members, agents, conversations, turns in a conversation, incoming messages, billing ledger entries, credentials, external connections, source documents, shared files, runtime workers, and access records. The same definitions can be used against SQLite, a lightweight local database, and Postgres, a production database server, so developers and deployed systems follow the same rules.

A lot of the value here is in the guardrails. Foreign keys keep related records connected, like making sure a conversation belongs to a real workspace. Unique constraints stop duplicates, such as two members with the same email in one workspace. Check constraints prevent impossible states, such as a turn being marked finished while still missing its final result. Indexes are added where the system is likely to search often, so common lookups stay fast.

Without this file, different parts of the system could disagree about what the database should look like, causing broken inserts, inconsistent data, or slow queries.

#### Function details

##### `_conversation_audience`  (lines 12–13)

```
def _conversation_audience(context: DefaultExecutionContext) -> str
```

**Purpose**: This function chooses the default audience value for a new conversation when one is not provided directly. In plain terms, it decides whether a conversation should be treated as shared or tied to a specific member, based on the row being inserted.

**Data flow**: It receives a SQLAlchemy execution context, which is an object describing the current database insert or update. It reads the current row’s parameters, pulls out `member_id`, passes that value to `conversation_audience`, and turns the result into text. That text becomes the stored default value for the conversation’s `audience` column.

**Call relations**: This function is attached to the `conversation` table as a Python-side default for the `audience` column. When SQLAlchemy prepares a new conversation row and no audience was supplied, it calls this helper. The helper asks the shared audience helper `conversation_audience` to apply the project’s audience rules, then hands SQLAlchemy the final string to write into the database.

*Call graph*: 2 external calls (get_current_parameters, conversation_audience).


### Member-facing extension stores
Extension-specific database layers persist notification inbox state and member enrichment records.

### `extensions/app_notification/ufo_ext_app_notification/store.py`

`domain_logic` · `cross-cutting: posting notifications, background drain ticks, delivery, and cleanup`

The notification app needs a safe shared inbox so agents can be told about important changes without being spammed by hundreds of separate messages. This file provides that inbox. A notification is stored as one database row for one subject, one receiving agent, and one member. If the same subject is raised again while still open, the file updates the existing row, replaces the latest message body, and increases an occurrence count instead of adding another row. This is like keeping one sticky note per topic and tallying how many times it came up.

The file also protects background drain jobs from stepping on each other. A drain job looks for “lanes,” meaning one inbox for one agent-member pair, then claims a limited batch of open rows with a temporary lease. If another drain tick overlaps, the lease stops both jobs from sending the same notification. If the job fails, the lease expires and the rows can be retried.

The store also records when a notification was triaged, meaning read into a drain turn, and when it was delivered to a surface such as a conversation or UI. Every query filters by workspace because the database connection is not automatically scoped to one workspace. Without this file, the notification app would not have a reliable memory of what still needs attention, what was already read, or what has already been delivered.

#### Function details

##### `Notification.name`  (lines 103–104)

```
def name(self) -> str
```

**Purpose**: Gives a notification a stable object-style name based on its unique id. This lets other parts of the app refer to a notification by a safe text name instead of passing around a raw database id.

**Data flow**: It reads the notification’s UUID id → converts it to its compact hexadecimal text form → returns that text as the notification name. It does not change anything.

**Call relations**: This is used wherever a Notification object needs to be matched against names supplied by a caller, especially when deciding which named notifications are deliverable.


##### `Notification.lane`  (lines 107–108)

```
def lane(self) -> Lane
```

**Purpose**: Builds the lane that this notification belongs to. A lane is the inbox for one receiving agent and one member.

**Data flow**: It reads the notification’s receiving agent id and member id → packages them into a Lane value → returns that Lane. The notification itself is unchanged.

**Call relations**: This property helps code move from an individual notification to the inbox bucket it belongs to. It creates a Lane object so drain logic can group work by agent-member pair.

*Call graph*: 1 external calls (__init__).


##### `_aware`  (lines 121–122)

```
def _aware(value: datetime) -> datetime
```

**Purpose**: Makes sure a date and time value has timezone information. This prevents confusing comparisons between times that know their timezone and times that do not.

**Data flow**: It receives a datetime value → checks whether it already has a timezone → returns it unchanged if it does, or returns a copy marked as UTC if it does not.

**Call relations**: _row calls this while turning database rows into Notification objects, so timestamps read from different database engines are normalized before the rest of the app uses them.

*Call graph*: called by 1 (_row); 1 external calls (replace).


##### `_row`  (lines 125–144)

```
def _row(row: sa.RowMapping) -> Notification
```

**Purpose**: Turns one raw database result row into a Notification object that application code can use comfortably. It is the translation step between SQL results and the app’s plain Python data model.

**Data flow**: It receives a database row mapping with column names and values → pulls out each notification field → normalizes timestamp fields with _aware → returns a populated Notification object.

**Call relations**: NotificationStore.rows and NotificationStore.claim call this after fetching rows from the database. It hands them clean Notification objects instead of database-specific row objects.

*Call graph*: calls 1 internal fn (_aware); called by 2 (claim, rows); 1 external calls (__init__).


##### `_claim_available`  (lines 147–148)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the rule for whether a notification can currently be claimed by a drain job. A row is available if it has no lease or its lease has expired.

**Data flow**: It receives the current time → creates a database condition checking for a missing claim expiry or an expiry earlier than now → returns that condition for use in SQL queries.

**Call relations**: The workspace candidate query, lane finder, and claim operation all use this same rule. That keeps the definition of “free to claim” consistent across the drain flow.

*Call graph*: called by 3 (claim, lanes_with_untriaged, due); 1 external calls (or_).


##### `inbox_agent_id`  (lines 151–164)

```
async def inbox_agent_id(ctx: ExtensionContext) -> UUID | None
```

**Purpose**: Finds the live agent that belongs to the notification app in the current workspace. This matters because the app should only write to and wake the agent it provisioned, not another agent that happens to have a similar name.

**Data flow**: It asks the extension context for all workspace agents → scans for the first non-archived agent provisioned by the notification extension → returns that agent’s id, or None if no suitable agent exists.

**Call relations**: Other notification code can call this before addressing inbox work. It relies on ExtensionContext.workspace_agents to read the workspace’s agent list.

*Call graph*: calls 1 internal fn (workspace_agents).


##### `untriaged_workspaces`  (lines 167–183)

```
def untriaged_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates the background-job candidate source for workspaces that have notification drain work waiting. It tells the job system, “these workspaces may need a drain tick.”

**Data flow**: It defines a database query-producing helper for due workspaces → passes that helper to the job candidate machinery → returns a WorkspaceCandidates object the scheduler can use.

**Call relations**: The scheduler uses this as the seam between notification storage and background jobs. Inside it, the nested due query checks for open, claimable rows whose target inbox agent is live.

*Call graph*: 1 external calls (owner_candidates).


##### `untriaged_workspaces.due`  (lines 171–181)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the actual database query for workspaces that currently have open notification rows ready to be drained. It only includes rows whose lease is free and whose receiving agent is live.

**Data flow**: It gets the current UTC time → builds a SELECT query over notification workspace ids → filters to open rows, available claims, and live target agents → returns the SQL query for the job system to run.

**Call relations**: This helper is handed to owner_candidates by untriaged_workspaces. It uses _claim_available for lease logic and agent_is_live so the scheduler does not wake workspaces for dead inbox agents.

*Call graph*: calls 1 internal fn (_claim_available); 3 external calls (now, select, agent_is_live).


##### `NotificationStore.post`  (lines 192–299)

```
async def post(self, *, to_agent_id: UUID, member_id: UUID, subject: str, body: str, agent_id: UUID, agent_name: str, turn_id: UUID, conversation_id: UUID) -> Posted | Refused
```

**Purpose**: Adds or updates a notification for one subject in one lane. It folds repeated posts about the same open subject into one row, so the inbox stays compact instead of growing without limit.

**Data flow**: It receives the destination agent, member, subject, body, producing agent, turn, and conversation → checks how many notification subjects this producer turn has already opened → refuses a new subject if the per-turn limit is reached and there is no existing open row → otherwise inserts a new row or updates the existing subject row → returns Posted with the new occurrence count, or Refused with a reason.

**Call relations**: This is the main write path for raising notifications. It uses database upsert behavior, meaning “insert if new, update if already present,” and returns a small result object so callers know whether their notification was accepted.

*Call graph*: 6 external calls (__init__, __init__, case, null, select, uuid4).


##### `NotificationStore.rows`  (lines 301–314)

```
async def rows(self) -> tuple[Notification, ...]
```

**Purpose**: Reads all notification rows for the current workspace, newest first. This is the broad listing method used when code needs a workspace-level view of the notification table.

**Data flow**: It opens a transaction → selects all notification columns for the store’s workspace → orders by most recently raised notification → converts each database row through _row → returns a tuple of Notification objects.

**Call relations**: NotificationStore.deliverable calls this and then filters the returned objects by name, member, and delivery state. Other callers can use it as the general read-all view.

*Call graph*: calls 1 internal fn (_row); called by 1 (deliverable); 1 external calls (select).


##### `NotificationStore.dismiss`  (lines 316–324)

```
async def dismiss(self, row_id: UUID) -> bool
```

**Purpose**: Deletes one notification row from the current workspace. This is used when a notification should be removed entirely rather than triaged or delivered.

**Data flow**: It receives a notification id → deletes the matching row only within the current workspace → returns true if exactly one row was deleted, otherwise false.

**Call relations**: This is a direct cleanup action. It does not call the higher-level row conversion helpers because it only needs to remove a row and report whether anything changed.

*Call graph*: 1 external calls (delete).


##### `NotificationStore.lanes_with_untriaged`  (lines 326–361)

```
async def lanes_with_untriaged(self, cooldown_seconds: int) -> tuple[Lane, ...]
```

**Purpose**: Finds inbox lanes that have open notifications ready for a drain job, while respecting a cooldown period. The cooldown prevents the same member-agent inbox from being woken again immediately after it was just read.

**Data flow**: It receives a cooldown length in seconds → reads distinct lanes with open, claimable rows → reads lanes triaged recently within the cooldown window → removes the cooling lanes from the open-lane list → returns Lane objects for the lanes that are ready now.

**Call relations**: The drain uses this before claiming work. It shares lease availability logic with the claim path through _claim_available, so lanes are only offered when their rows are actually claimable.

*Call graph*: calls 1 internal fn (_claim_available); 4 external calls (__init__, now, timedelta, select).


##### `NotificationStore.claim`  (lines 363–401)

```
async def claim(self, lane: Lane, limit: int, lease_seconds: int) -> tuple[Notification, ...]
```

**Purpose**: Temporarily reserves a limited number of open notifications from one lane for a drain job. The lease is what prevents two overlapping drain jobs from reading and sending the same rows.

**Data flow**: It receives a Lane, a maximum number of rows, and a lease duration → selects the oldest open, claimable rows in that lane → updates them with a claim expiry timestamp → converts the returned rows into Notification objects → returns them sorted oldest first.

**Call relations**: This is the handoff from “there is work” to “this drain turn owns these rows for now.” It uses _claim_available to avoid already leased rows and _row to return application-friendly Notification objects.

*Call graph*: calls 2 internal fn (_claim_available, _row); 5 external calls (now, timedelta, and_, select, update).


##### `NotificationStore.mark_triaged`  (lines 403–430)

```
async def mark_triaged(self, batch: tuple[Notification, ...], turn_id: UUID) -> None
```

**Purpose**: Closes the exact notification rows that a drain turn successfully read. It only closes a row if its occurrence count is still the same as when it was claimed, so new folded-in activity is not accidentally hidden.

**Data flow**: It receives the batch of Notifications that were read and the drain turn id → updates rows in the current workspace whose ids and occurrence counts still match → stamps them with the triage turn and time, clears the lease, and updates their timestamp → returns nothing.

**Call relations**: InboxDrain._wake calls this after waking or preparing the inbox turn. The occurrence-count check is important: if another post arrived after the claim, the row stays open under its lease and can be retried after the lease expires.

*Call graph*: called by 1 (_wake); 3 external calls (and_, or_, update).


##### `NotificationStore.is_delivery_turn`  (lines 432–445)

```
async def is_delivery_turn(self, turn_id: UUID) -> bool
```

**Purpose**: Checks whether a given turn was created as part of delivering a notification. This acts as a loop guard so notification delivery does not recursively trigger more notification delivery.

**Data flow**: It receives a turn id → searches the current workspace for any notification whose delivered_turn_id matches it → returns true if one exists, otherwise false.

**Call relations**: Other notification logic can ask this before reacting to a turn. It reads only a single matching id because it only needs a yes-or-no answer.

*Call graph*: 1 external calls (select).


##### `NotificationStore.deliverable`  (lines 447–457)

```
async def deliverable(self, member_id: UUID, names: tuple[str, ...]) -> tuple[Notification, ...]
```

**Purpose**: Finds the named notifications for a member that have not yet been delivered. This lets delivery code validate a requested set of notification names before sending them somewhere.

**Data flow**: It receives a member id and a tuple of notification names → reads all workspace rows through NotificationStore.rows → keeps only rows whose name is requested, whose member matches, and whose delivery surface is still empty → returns those Notification objects.

**Call relations**: This function builds on the general rows reader instead of writing its own SQL. It relies on Notification.name to compare caller-provided names with stored notification ids.

*Call graph*: calls 1 internal fn (rows).


##### `NotificationStore.mark_delivered`  (lines 459–472)

```
async def mark_delivered(self, rows: tuple[Notification, ...], *, turn_id: UUID | None, surface: str) -> None
```

**Purpose**: Records that a set of notifications has been delivered to a particular surface. This prevents the same notification from being delivered again through the same flow.

**Data flow**: It receives Notification objects, an optional delivery turn id, and a surface name → updates those rows in the current workspace → stores the delivery turn, delivery surface, and updated timestamp → returns nothing.

**Call relations**: Delivery code calls this after it has handed notifications off to their destination. Later, deliverable filters out rows with a delivered surface, and is_delivery_turn can recognize turns created by delivery.

*Call graph*: 1 external calls (update).


### `extensions/enrichment/ufo_ext_enrichment/store.py`

`io_transport` · `background enrichment jobs and request handling`

This file keeps the enrichment feature grounded in the database. Enrichment means looking up extra public information about a member or their company, but this code is careful about consent: a member is only looked up if there is a saved consent row saying they agreed. Think of it like a filing cabinet with three drawers. One drawer stores each member’s enriched profile. Another stores whether each member said yes or no, plus the website they confirmed. A third stores a temporary “come back later” note when the outside data provider refuses or rate-limits requests.

The file defines the shape of those records with SQLAlchemy tables, which describe database tables in Python, and Pydantic models, which check that JSON data has the expected fields. `Person`, `Company`, and `Profile` describe the information that can be saved and later shown. The `Profiles`, `Consents`, and `Backoff` classes are small database helpers used inside an existing database transaction. They find members ready for enrichment, write or delete profile rows, save consent decisions, and pause or resume work after provider failures.

An important behavior is that missing consent is treated the same as refusal for enrichment purposes: no saved “yes” means no lookup. Another important behavior is backoff: repeated provider trouble makes the system wait longer, up to an hour, instead of hammering the provider every minute.

#### Function details

##### `due_workspaces`  (lines 161–176)

```
def due_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds a database query for workspaces that have at least one member ready to be enriched. A workspace is ready only if a seated member gave consent, has no profile yet, and the workspace is not currently paused after a provider refusal.

**Data flow**: It reads the current time and the member, consent, profile, and backoff table definitions. From that, it creates a SQL query that will return workspace IDs matching the rules. It does not run the query itself; it hands the query back to the caller.

**Call relations**: This is used when the enrichment job is deciding which workspaces deserve attention next. It relies on `_has_profile` to exclude members who already have stored enrichment data, and it uses database query-building helpers to express the consent and pause checks.

*Call graph*: calls 1 internal fn (_has_profile); 3 external calls (now, exists, select).


##### `_has_profile`  (lines 179–180)

```
def _has_profile() -> sa.ColumnElement[bool]
```

**Purpose**: Creates a small reusable database condition that answers: “does this member already have an enrichment profile?” It helps prevent duplicate enrichment work.

**Data flow**: It looks at the profile table definition and the current member row being considered in a larger query. It returns a SQL existence check, meaning a condition the database can evaluate later.

**Call relations**: This helper is called by `due_workspaces` and `Profiles.due` whenever they need to filter out members who already have profile rows. It is not a standalone operation; it is a building block inside bigger database queries.

*Call graph*: called by 2 (due, due_workspaces); 1 external calls (exists).


##### `Profiles.due`  (lines 190–212)

```
async def due(self, limit: int) -> tuple[SeatedMember, ...]
```

**Purpose**: Finds the next seated members in one workspace who gave consent and still need enrichment. It returns their member ID, email address, and confirmed website.

**Data flow**: The caller gives a limit. The method reads members and consent rows for this workspace, keeps only seated members with granted consent and no profile, orders them oldest-first, and returns a tuple of `SeatedMember` objects. The database is only read, not changed.

**Call relations**: A workspace-level enrichment job calls this after choosing a workspace to work on. It uses `_has_profile` for the “not enriched yet” check, then packages each result as a `SeatedMember` so later code can ask the provider for data.

*Call graph*: calls 1 internal fn (_has_profile); 2 external calls (__init__, select).


##### `Profiles.seated`  (lines 214–224)

```
async def seated(self, member_id: UUID) -> SeatedMember | None
```

**Purpose**: Checks whether a particular member belongs to this workspace and is currently seated. This is useful before doing work that only makes sense for active, seated members.

**Data flow**: It receives a member ID. It queries the member table for that ID within the current workspace and requires `seated_at` to be present. If found, it returns a `SeatedMember` with the ID and email; otherwise it returns `None`.

**Call relations**: Other enrichment or portal code can call this before recording or displaying profile information for a specific member. It hands back a simple member object that later steps can use without needing the raw database row.

*Call graph*: 2 external calls (__init__, select).


##### `Profiles.write`  (lines 226–249)

```
async def write(self, member_id: UUID, profile: Profile) -> None
```

**Purpose**: Saves an enrichment profile for a member, replacing an existing row if one is already there. This is how provider results or recorded profile data become durable database state.

**Data flow**: It receives a member ID and a `Profile` object. It turns nested person and company models into JSON-friendly dictionaries, then tries to update the existing profile row for this workspace and member. If no row was updated, it inserts a new row. Nothing is returned.

**Call relations**: After an enrichment lookup succeeds or a profile is otherwise produced, higher-level code calls this method to persist the result. It hands the actual database writing to SQL update and insert operations.

*Call graph*: 2 external calls (insert, update).


##### `Profiles.forget`  (lines 251–257)

```
async def forget(self, member_id: UUID) -> None
```

**Purpose**: Deletes a stored enrichment profile for one member in this workspace. This supports removing data when it should no longer be kept or shown.

**Data flow**: It receives a member ID and sends a database delete for the matching workspace and member profile row. It returns nothing and does not complain if there was no matching row.

**Call relations**: Higher-level privacy, cleanup, or member-change flows can call this when enrichment data should be removed. It delegates the removal to the database delete operation.

*Call graph*: 1 external calls (delete).


##### `Profiles.rows`  (lines 259–268)

```
async def rows(self, limit: int) -> tuple[StoredProfile, ...]
```

**Purpose**: Reads a batch of stored enrichment profiles for this workspace. This is useful for listing or exporting profile data in a stable order.

**Data flow**: The caller supplies a maximum number of rows. The method selects profile columns for this workspace, orders them by fetch time and member ID, limits the result, and converts each raw database row into a `StoredProfile`. The database is not changed.

**Call relations**: Code that needs many saved profiles calls this method. For each row, it hands conversion to `_stored`, which rebuilds the typed `Profile` object from database values and JSON fields.

*Call graph*: calls 1 internal fn (_stored); 1 external calls (select).


##### `Profiles.one`  (lines 270–279)

```
async def one(self, member_id: UUID) -> StoredProfile | None
```

**Purpose**: Reads the stored enrichment profile for one specific member, if it exists. This supports member-specific display or decision-making.

**Data flow**: It receives a member ID and queries the profile table for that member in the current workspace. If a row exists, it converts it into a `StoredProfile`; if not, it returns `None`.

**Call relations**: Portal or workflow code can call this when it needs exactly one member’s enrichment. Like the batch reader, it relies on `_stored` to turn the raw database row into the typed shape used by the rest of the feature.

*Call graph*: calls 1 internal fn (_stored); 1 external calls (select).


##### `Profiles.by_email`  (lines 281–290)

```
async def by_email(self, email: str) -> StoredProfile | None
```

**Purpose**: Finds a stored enrichment profile by email address within this workspace. The match ignores letter case and trims extra spaces from the input email.

**Data flow**: It receives an email string, strips surrounding whitespace, lowercases it, and compares it to lowercased stored emails in the profile table. If one row is found, it converts it to `StoredProfile`; otherwise it returns `None`.

**Call relations**: Code that starts from an email address rather than a member ID can use this lookup. Once the database returns a row, `_stored` performs the same safe conversion used by the other profile readers.

*Call graph*: calls 1 internal fn (_stored); 1 external calls (select).


##### `Consents.record`  (lines 301–316)

```
async def record(self, member_id: UUID, *, granted: bool, website: str | None=None) -> None
```

**Purpose**: Saves a member’s enrichment consent decision: whether they granted permission and, optionally, the website they confirmed. This is the gatekeeper record that decides whether enrichment may happen.

**Data flow**: It receives a member ID, a granted-or-not value, and an optional website. It stamps the decision with the current time, updates the existing consent row if present, or inserts a new one if not. It returns nothing.

**Call relations**: Consent UI or member onboarding code calls this when a member answers the enrichment question. Later, `due_workspaces` and `Profiles.due` read these rows so only members with a saved granted decision are looked up.

*Call graph*: 3 external calls (now, insert, update).


##### `Backoff.pause`  (lines 328–354)

```
async def pause(self, retry_after: float | None) -> float
```

**Purpose**: Pauses enrichment work for this workspace after the provider refuses or asks the system to wait. It calculates how long to wait and saves that future retry time.

**Data flow**: It reads the current number of failed attempts for the workspace. If the provider supplied a wait time, it uses that up to a maximum of one hour; otherwise it doubles the delay each time, starting at one minute and also capped at one hour. It writes the new attempt count and retry time to the backoff table, then returns the number of seconds chosen.

**Call relations**: The enrichment job calls this when a provider response says “not now” or otherwise fails in a way that should slow future attempts. `due_workspaces` later reads the backoff table and skips the workspace until the saved retry time has passed.

*Call graph*: 5 external calls (now, timedelta, insert, select, update).


##### `Backoff.clear`  (lines 356–361)

```
async def clear(self) -> None
```

**Purpose**: Removes the pause record for this workspace. This lets enrichment resume normally after successful work.

**Data flow**: It deletes the backoff row for the current workspace, if one exists. It returns nothing and leaves other workspaces untouched.

**Call relations**: A job tick that successfully enriches somebody can call this to show the workspace is healthy again. After it clears the row, `due_workspaces` will no longer skip that workspace because of backoff.

*Call graph*: 1 external calls (delete).


##### `agent_is_main`  (lines 364–375)

```
async def agent_is_main(connection: AsyncConnection, workspace_id: UUID, agent_id: UUID) -> bool
```

**Purpose**: Checks whether a given agent is the main agent for a workspace. This mirrors a core member-page rule so enrichment can apply the same narrowing.

**Data flow**: It receives a database connection, workspace ID, and agent ID. It queries the agent table for the matching row and reads its `is_main` flag. It returns `True` if the flag is present and true, otherwise `False`.

**Call relations**: Higher-level code calls this when it needs to know whether an agent has the main-agent role before showing or using enrichment information. It performs one direct database read and returns a simple yes-or-no answer.

*Call graph*: 2 external calls (execute, select).


##### `_stored`  (lines 378–392)

```
def _stored(row: sa.Row) -> StoredProfile
```

**Purpose**: Turns a raw database profile row into the typed object used by the rest of the enrichment code. It is the translation step between stored JSON and normal Python models.

**Data flow**: It receives a database row containing member ID, profile fields, optional person JSON, optional company JSON, and fetch time. It validates the JSON into `Person` and `Company` models when present, ensures the fetch time has a timezone, wraps everything in a `Profile`, and returns a `StoredProfile`.

**Call relations**: `Profiles.rows`, `Profiles.one`, and `Profiles.by_email` call this after reading from the database. This keeps all profile-reading paths consistent, so the rest of the system receives the same clean shape no matter how the row was found.

*Call graph*: called by 3 (by_email, one, rows); 2 external calls (__init__, __init__).


### Automation scheduling stores
Operational stores track monitors, delayed conversation wakeups, and recurring scheduled work for background automation.

### `extensions/monitors/ufo_ext_monitors/monitors.py`

`domain_logic` · `request handling and scheduled monitor runner`

This file is the monitor extension’s storage layer and rulebook for monitor state. Think of each monitor as a scheduled alarm with a clipboard: it remembers which conversation and agent to return to, what command to run, what output counts as “normal,” when to check again, and whether a background worker has temporarily borrowed it to do the check. Without this file, monitors could not be armed, workers could double-run the same monitor, stopped monitors might still fire, and output could grow without limit.

The file first defines constants, such as maximum output size and lease batch size, then declares the database table used only by this extension. Every query includes the workspace id because the database connection is not automatically limited to one workspace.

A small `Monitor` value object represents one row in ordinary Python form. Helper functions turn raw database rows into this object and normalize timestamps so time comparisons stay reliable.

The `MonitorStore` class is the main doorway. It can list armed monitors, insert a new one, lease due monitors for a runner, record the result of a probe, check whether a lease still owns a monitor, and delete a monitor after it fires or when a user disarms it. The lease acts like a library checkout slip: while one worker holds it, another worker should not process the same monitor.

#### Function details

##### `qualified_name`  (lines 73–84)

```
def qualified_name(conversation_id: UUID, slug: str) -> str
```

**Purpose**: Builds the stored monitor name from a conversation id and a human-chosen slug. This prevents two conversations in the same workspace from accidentally using the same monitor object name.

**Data flow**: It receives a conversation UUID and a short slug → takes the first few hex characters of the conversation id and places them before the slug → returns one workspace-unique name string.

**Call relations**: This is a naming helper used when a monitor is created or referred to by object name. It does not call other project functions; it simply enforces the same naming shape expected by the monitor table’s uniqueness rule.


##### `capped`  (lines 87–97)

```
def capped(output: str) -> str
```

**Purpose**: Shrinks probe output to a safe maximum size while keeping the beginning and end. This matters because monitor output can be large, and a later fire should not carry an unlimited amount of text.

**Data flow**: It receives an output string → converts it to bytes and checks its size → if it is small enough, returns it unchanged; otherwise returns the first half, an omission marker saying how many bytes were removed, and the last half.

**Call relations**: This helper is used around probe output before it is compared or reported. It stands alone and does not call other project functions.


##### `stderr_tail`  (lines 100–105)

```
def stderr_tail(stderr: str) -> str
```

**Purpose**: Keeps only the end of a failed command’s error output. The end of stderr is usually where shells and tools explain what went wrong.

**Data flow**: It receives a stderr string → checks its byte length → returns the whole string if it is short, or only the final allowed bytes if it is too long.

**Call relations**: This helper is meant for failed probe reporting. It has no project-level callees; it is a simple text-size guard.


##### `_aware`  (lines 137–138)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Ensures a datetime has timezone information. This avoids mixing timezone-aware and timezone-less times, which can cause wrong comparisons or runtime errors.

**Data flow**: It receives a datetime → if it already has a timezone, returns it as-is; if not, marks it as UTC → returns the normalized datetime.

**Call relations**: `_row` calls this whenever it builds a `Monitor` from database data. The need comes from SQLite sometimes returning plain datetimes without timezone labels.

*Call graph*: called by 1 (_row); 1 external calls (replace).


##### `_row`  (lines 141–168)

```
def _row(row: sa.RowMapping) -> Monitor
```

**Purpose**: Converts one database row into a `Monitor` object that the rest of the extension can use safely. It also normalizes all stored times to UTC-aware datetimes.

**Data flow**: It receives a SQLAlchemy row mapping from the monitor table → reads each column, fixes timestamp fields through `_aware`, and fills a `Monitor` dataclass → returns that `Monitor` object.

**Call relations**: `MonitorStore.armed`, `MonitorStore.arm`, and `MonitorStore.claim_due` all call `_row` after reading rows from the database. It is the shared doorway that keeps every retrieved monitor shaped consistently.

*Call graph*: calls 1 internal fn (_aware); called by 3 (arm, armed, claim_due); 1 external calls (__init__).


##### `_claim_available`  (lines 171–172)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for “this monitor is not currently leased, or its lease has expired.” This is how workers avoid stepping on each other.

**Data flow**: It receives the current time → creates a SQL condition checking for no claim or an expired claim time → returns that condition for use inside larger database queries.

**Call relations**: `due_monitor_workspaces.due` uses it to find workspaces with runnable monitor work, and `MonitorStore.claim_due` uses it again when actually leasing monitor rows.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `_due`  (lines 175–176)

```
def _due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for “this monitor needs attention now.” A monitor is due if its next probe time has arrived or its deadline has arrived.

**Data flow**: It receives the current time → creates a SQL condition comparing that time against `next_probe_at` and `deadline_at` → returns the condition for larger queries.

**Call relations**: `due_monitor_workspaces.due` uses it to find candidate workspaces, and `MonitorStore.claim_due` uses it when selecting the specific monitor rows to lease.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `due_monitor_workspaces`  (lines 179–196)

```
def due_monitor_workspaces() -> WorkspaceCandidates
```

**Purpose**: Provides the job system with a way to find workspaces that have monitor work ready to run. It returns candidates only when a due monitor is also free to be claimed and its agent is live.

**Data flow**: It defines an inner query builder → gives that query builder to the job helper `owner_candidates` → returns a `WorkspaceCandidates` object that the scheduler can ask for workspaces.

**Call relations**: The background monitor runner relies on this as its scheduling seam. It hands the inner `due` function to `owner_candidates`, which is responsible for turning the SQL query into workspace candidates for the job system.

*Call graph*: 1 external calls (owner_candidates).


##### `due_monitor_workspaces.due`  (lines 184–194)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the actual database query for workspaces that currently have due monitor work. It filters out monitors that are leased and monitors whose agent is not live.

**Data flow**: It reads the current UTC time → creates a SQL select over monitor workspace ids → applies the “claim is free,” “monitor is due,” and “agent is live” conditions → returns a distinct workspace-id query.

**Call relations**: This inner function is passed to `owner_candidates` by `due_monitor_workspaces`. It calls `_claim_available`, `_due`, and `agent_is_live` so the scheduler opens only workspaces where work can really proceed.

*Call graph*: calls 2 internal fn (_claim_available, _due); 3 external calls (now, select, agent_is_live).


##### `MonitorStore.armed`  (lines 205–211)

```
async def armed(self, conversation_id: UUID | None=None) -> tuple[Monitor, ...]
```

**Purpose**: Lists the monitors currently armed in this store’s workspace. It can list all monitors or only those belonging to one conversation.

**Data flow**: It receives an optional conversation id → builds a database query scoped to the store’s workspace, with an extra conversation filter if provided → reads matching rows ordered by name → converts each row through `_row` → returns a tuple of `Monitor` objects.

**Call relations**: This is the read path for callers that need to show or inspect active monitors. It relies on `_row` so every returned monitor has normalized timestamps and the same Python shape.

*Call graph*: calls 1 internal fn (_row); 1 external calls (select).


##### `MonitorStore.arm`  (lines 213–268)

```
async def arm(self, *, conversation_id: UUID, agent_id: UUID, name: str, audience: str, command: str, interval_minutes: int, deadline_at: datetime, reason: str, next_steps: str, metadata: dict[str, Js
```

**Purpose**: Creates a new armed monitor row. This is used when an agent or member asks the system to start watching something.

**Data flow**: It receives all monitor setup details, including conversation, agent, command, interval, deadline, explanation text, metadata, baseline output, and first probe time → inserts a new row with fresh ids, zeroed counters, no current claim, and timestamps → converts the returned database row through `_row` → returns the newly created `Monitor`.

**Call relations**: This is the write path that turns a monitor request into durable database state. It calls `uuid4` for the monitor id and `_row` to return the inserted row in the same form as other reads.

*Call graph*: calls 1 internal fn (_row); 2 external calls (insert, uuid4).


##### `MonitorStore.claim_due`  (lines 270–313)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_MONITORS) -> tuple[Monitor, ...]
```

**Purpose**: Leases a batch of due monitors for a worker to process. The lease prevents overlapping runners from probing and firing the same monitor at the same time.

**Data flow**: It receives the current time, a lease length in seconds, and a maximum number of monitors → generates a claim id → selects due, unclaimed-or-expired monitors in this workspace whose agents are live → updates those rows with the claim id and claim expiry → returns the claimed rows as `Monitor` objects.

**Call relations**: The monitor runner calls this when it is ready to do work. It uses `_claim_available` and `_due` to find eligible rows, `agent_is_live` to skip dead agents, and `_row` to hand claimed monitors back to the runner.

*Call graph*: calls 3 internal fn (_claim_available, _due, _row); 5 external calls (timedelta, select, update, agent_is_live, uuid4).


##### `MonitorStore.quiet_tick`  (lines 315–325)

```
async def quiet_tick(self, row: Monitor, probed_at: datetime, next_probe_at: datetime) -> None
```

**Purpose**: Records a successful probe whose output still matches the baseline. This means nothing needs to be posted, but the monitor should remember the quiet streak and schedule the next check.

**Data flow**: It receives the claimed monitor row, the time the probe ran, and the next probe time → increases the probe count and quiet streak, resets the failure streak, keeps the skipped count → passes the new values to `_tick` → no value is returned.

**Call relations**: `MonitorRunner._tick` calls this after a normal, unchanged probe. This function does not write directly; it hands the actual database update to `MonitorStore._tick`.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore.failed_tick`  (lines 327–339)

```
async def failed_tick(self, row: Monitor, probed_at: datetime, next_probe_at: datetime) -> None
```

**Purpose**: Records a probe that ran but exited with an error, when that error is not yet enough to fire the monitor. It advances the failure streak and breaks the quiet streak.

**Data flow**: It receives the claimed monitor row, the probe time, and the next probe time → increases the probe count and failure streak, resets the quiet streak, keeps skipped unchanged → sends those values to `_tick` → no value is returned.

**Call relations**: `MonitorRunner._tick` calls this when a command failure should be remembered but not yet delivered as a fire. `MonitorStore._tick` performs the guarded database update.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore.skipped_tick`  (lines 341–352)

```
async def skipped_tick(self, row: Monitor, next_probe_at: datetime) -> None
```

**Purpose**: Records that a probe could not run, for example because the client sandbox was unreachable. A skipped probe is counted separately from a failed command.

**Data flow**: It receives the claimed monitor row and the next probe time → keeps the probe count, quiet streak, failure streak, and last probe time unchanged → increases the skipped count → passes the update to `_tick` → no value is returned.

**Call relations**: `MonitorRunner._tick` calls this when the runner could not actually execute the probe. It delegates the database write to `MonitorStore._tick` like the other tick-result methods.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore._tick`  (lines 354–386)

```
async def _tick(self, row: Monitor, *, probes_run: int, quiet_streak: int, failure_streak: int, skipped: int, last_probe_at: datetime | None, next_probe_at: datetime) -> None
```

**Purpose**: Writes the new counters and schedule after a claimed monitor has been processed. It also releases the lease so the monitor can be claimed again later.

**Data flow**: It receives a claimed monitor plus the updated counters, last probe time, and next probe time → refuses to proceed if the monitor was not claimed → updates only the row with the matching workspace, monitor id, and claim id → clears the claim and claim expiry → returns nothing.

**Call relations**: `quiet_tick`, `failed_tick`, and `skipped_tick` all funnel into this method. The claim check is important: it makes sure a worker only updates the monitor it actually leased.

*Call graph*: called by 3 (failed_tick, quiet_tick, skipped_tick); 1 external calls (update).


##### `MonitorStore.claim_holds`  (lines 388–415)

```
async def claim_holds(self, row: Monitor) -> bool
```

**Purpose**: Checks whether a worker still owns the monitor lease immediately before firing. This prevents a monitor that was just disarmed from still delivering a fire.

**Data flow**: It receives a claimed monitor → refuses to proceed if there is no claim id → looks for the row with the same workspace, monitor id, and claim id, locking it while checking → returns true if it still exists under that claim, otherwise false.

**Call relations**: `MonitorRunner._fire` calls this right before delivering a fire. If the row was deleted by `disarm`, this returns false and the runner can avoid firing something the user stopped.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `MonitorStore.retire`  (lines 417–429)

```
async def retire(self, row: Monitor) -> None
```

**Purpose**: Deletes a monitor after its fire has been delivered. This matches the rule that one armed monitor ends in exactly one fire.

**Data flow**: It receives a claimed monitor → refuses if there is no claim id → deletes only the row with the matching workspace, monitor id, and claim id → returns nothing.

**Call relations**: `MonitorRunner._fire` calls this after a fire is successfully delivered. The claim guard means an expired or lost lease cannot delete a row that another runner may now own.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


##### `MonitorStore.disarm`  (lines 431–439)

```
async def disarm(self, row: Monitor) -> bool
```

**Purpose**: Stops watching by deleting a monitor row, regardless of whether it is currently claimed. It reports whether a row was actually removed.

**Data flow**: It receives a monitor row → deletes the row with the same workspace and monitor id → checks the database’s deleted-row count → returns true if one row was deleted, false otherwise.

**Call relations**: This is the user-driven stop path. It can race with a runner, so `claim_holds` exists on the fire path to notice when `disarm` removed the row before a fire is sent.

*Call graph*: 1 external calls (delete).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/pauses.py`

`io_transport` · `scheduled task polling and pause resume handling`

A pause means: “this workflow is sleeping until a certain time, then resume this conversation with this prompt.” This file defines the pause table and the small set of safe operations used to add, inspect, claim, and remove those pauses.

The important rule is that each conversation can have only one active pause. If the same conversation is paused again, the old wait is replaced. That prevents one workflow from waiting for two different timers at once, like replacing an alarm rather than setting a second alarm beside it.

The file also protects against multiple workers trying to wake the same pause. A worker must first “claim” a due pause, which is a short lease saying “I am working on this one.” Other workers skip pauses with a live claim. Before firing, the worker checks that the claim still belongs to the same pause, because the conversation may have been re-armed in the meantime. After the wake-up is fired or skipped, the worker retires the pause, but only if its claim still matches.

The table belongs to this extension, not the core system schema. Every query filters by workspace, because the database connection is not automatically scoped to one workspace.

#### Function details

##### `_aware`  (lines 76–77)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: This helper makes sure a date and time value clearly says it is in UTC time. It prevents later code from accidentally comparing a timezone-less time with a timezone-aware one.

**Data flow**: It receives a datetime value. If the value already has timezone information, it returns it unchanged; if not, it adds UTC as the timezone. The output is always safe for the rest of this file to treat as UTC.

**Call relations**: Rows from the database are converted through _row, and _row calls this helper for each stored timestamp. This matters especially for SQLite, which may return times without timezone information.

*Call graph*: called by 1 (_row); 1 external calls (replace).


##### `_row`  (lines 80–95)

```
def _row(row: sa.RowMapping) -> Pause
```

**Purpose**: This turns a raw database row into a Pause object that the rest of the extension can use. It is the one place where stored pause data is cleaned up after reading.

**Data flow**: It receives a row mapping from the database. It pulls out the pause id, conversation, agent, wake-up time, recorded sequence numbers, prompt, creator, claim, and timestamps; it also normalizes all timestamps through _aware. It returns a Pause value object.

**Call relations**: All read paths in PauseStore funnel their database results through this builder. arm uses it after inserting or updating a pause, armed uses it when listing stored pauses, and claim_due uses it after leasing due pauses.

*Call graph*: calls 1 internal fn (_aware); called by 3 (arm, armed, claim_due); 1 external calls (__init__).


##### `_claim_available`  (lines 98–99)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition that says a pause is free to be claimed. A pause is available if nobody has claimed it, or if the previous claim has expired.

**Data flow**: It receives the current time. It produces a SQL condition comparing that time with the pause row’s claim fields. Nothing is changed directly; the condition is used inside larger database queries.

**Call relations**: The workspace scanner and the claim operation both rely on the same availability rule. due_pause_workspaces.due uses it to find workspaces worth opening, and PauseStore.claim_due uses it to actually lease pauses in one workspace.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `due_pause_workspaces`  (lines 102–118)

```
def due_pause_workspaces() -> WorkspaceCandidates
```

**Purpose**: This tells the background job system which workspaces may have pauses ready to wake up. It is a quick first filter before doing detailed work inside each workspace.

**Data flow**: It defines a query-producing helper that looks for distinct workspace ids with due, claimable pauses. It passes that helper to the job ownership system, which turns it into workspace candidates for workers.

**Call relations**: The pause runner uses this as its candidate seam: instead of scanning every workspace blindly, the job system asks this file which workspaces have visible due work. The inner due query does the actual database selection.

*Call graph*: 1 external calls (owner_candidates).


##### `due_pause_workspaces.due`  (lines 107–116)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: This inner helper builds the database query for workspaces that currently have at least one due pause whose claim is free. It keeps the scheduler from waking workers for pauses that are already leased by someone else.

**Data flow**: It reads the current UTC time, creates the claim-availability condition, and builds a SQL query for distinct workspace ids where resume_at is in the past or present. It returns the query, not the final rows.

**Call relations**: due_pause_workspaces hands this query builder to owner_candidates. That outside job helper can then run the query when it needs to decide which workspaces should be assigned to workers.

*Call graph*: calls 1 internal fn (_claim_available); 2 external calls (now, select).


##### `PauseStore.arm`  (lines 127–183)

```
async def arm(self, *, conversation_id: UUID, agent_id: UUID, resume_at: datetime, origin_seq: int, origin_arrival_seq: int, prompt: str, created_by_member_id: UUID | None) -> Pause
```

**Purpose**: This creates or replaces the pause for one conversation. Someone uses it when a workflow says, “wait until this time, then resume with this prompt.”

**Data flow**: It receives the conversation, agent, wake-up time, sequence watermarks, prompt, and optional member who created the pause. It opens the extension transaction, inserts a new row, or updates the existing row for that workspace and conversation. It gives the wait a fresh id and clears any old claim, then returns the stored pause as a Pause object.

**Call relations**: This is the write path for arming a wait. It hands the returned database row to _row so callers receive normalized data. Its fresh id behavior is important because PauseRunner later uses pause identity to avoid confusing an old fired wait with a newly re-armed one.

*Call graph*: calls 1 internal fn (_row); 1 external calls (uuid4).


##### `PauseStore.armed`  (lines 185–191)

```
async def armed(self, conversation_id: UUID | None=None) -> tuple[Pause, ...]
```

**Purpose**: This lists the currently armed pauses in the workspace, optionally for just one conversation. It is useful for inspection, tests, or code that needs to know what is waiting.

**Data flow**: It receives an optional conversation id. It builds a workspace-scoped query, adds the conversation filter if one was given, orders results by wake-up time, reads the rows, and returns them as Pause objects.

**Call relations**: This is a read-only view over the pause table. After the database returns rows, it sends each one through _row so the rest of the system sees the same clean Pause shape used by other operations.

*Call graph*: calls 1 internal fn (_row); 1 external calls (select).


##### `PauseStore.claim_due`  (lines 193–234)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_PAUSES) -> tuple[Pause, ...]
```

**Purpose**: This leases a batch of pauses that are ready to fire. Leasing is how the system prevents two workers from waking the same conversation at the same time.

**Data flow**: It receives the current time, a lease length in seconds, and a maximum number of pauses to take. It creates a unique claim id, selects the oldest due and available rows for this workspace, updates those rows with the claim and expiry time, and returns the claimed pauses. The database update and return happen together so overlapping workers divide the work instead of duplicating it.

**Call relations**: A pause runner calls this when polling a workspace for due work. It uses _claim_available for the shared availability rule and _row to turn leased rows into Pause objects that can later be checked and retired.

*Call graph*: calls 2 internal fn (_claim_available, _row); 4 external calls (timedelta, select, update, uuid4).


##### `PauseStore.claim_holds`  (lines 236–261)

```
async def claim_holds(self, row: Pause) -> bool
```

**Purpose**: This checks whether a worker still owns the pause it is about to fire. It is a last safety check before doing something that cannot be undone: sending the resume turn.

**Data flow**: It receives a Pause that should already have a claim id. If there is no claim id, it raises an error because an unclaimed pause must not fire. Otherwise it looks up the same row in the current workspace with the same claim and returns true if it still exists, false if it was replaced, cleared, or taken out from under this worker.

**Call relations**: PauseRunner._fire calls this immediately before firing a pause. If a workflow re-armed the same conversation during the lease window, this check helps avoid firing an abandoned wait.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `PauseStore.retire`  (lines 263–276)

```
async def retire(self, row: Pause) -> None
```

**Purpose**: This removes a pause after the worker is done with it, but only if the worker still owns the matching claim. It is the cleanup step after a pause has fired or has been deliberately settled.

**Data flow**: It receives a claimed Pause. If the pause has no claim id, it raises an error. Otherwise it deletes the row with the same id, workspace, and claim id. If the claim expired or the row was replaced, the delete matches nothing, leaving the newer or differently-owned pause safe.

**Call relations**: PauseRunner._fire calls this after handling a pause. The claim check in the delete statement pairs with claim_due and claim_holds so one worker cannot accidentally remove a pause that another worker or a re-arm now owns.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/schedules.py`

`domain_logic` · `request handling and background scheduled-task sweeps`

Scheduled tasks need to survive process restarts and be safe when more than one worker is looking for work. This file gives them a durable home in the database and wraps that table in `ScheduleStore`, a small service that knows the rules for creating, editing, listing, claiming, and rescheduling tasks.

A task row stores the task name, cron-like schedule text, prompt, next run time, optional expiry time, the conversation it reports into, and the agent that must execute it. The file is careful about boundaries: every database statement filters by workspace, and member-facing operations also stay inside the current object agent’s namespace. That prevents one workspace or agent from accidentally seeing or changing another’s tasks.

The most important behavior is leasing. When the runner asks for due tasks, `claim_due` marks a small batch with a temporary claim, like putting a sticky note on library books so two librarians do not both process the same ones. Before firing, the runner can check that the claim still holds, because a user may have edited or cancelled the task in the meantime. After a successful fire, the task is advanced to its next run time and the claim is cleared. Expired tasks are removed instead of fired.

#### Function details

##### `_utc`  (lines 124–125)

```
def _utc(value: datetime) -> datetime
```

**Purpose**: Makes sure a datetime has a UTC timezone attached. This keeps time comparisons consistent even when the database returns a time without timezone information.

**Data flow**: It receives one datetime. If the datetime already says what timezone it is in, it is returned as-is; otherwise the function labels it as UTC. The output is always a datetime that can be treated as UTC-aware.

**Call relations**: Rows turned into `ScheduledTask` objects pass their time fields through this helper, and status inspection does the same. `_utc_opt` also uses it for optional time fields.

*Call graph*: called by 3 (inspect_many, _task, _utc_opt); 1 external calls (replace).


##### `_utc_opt`  (lines 128–129)

```
def _utc_opt(value: datetime | None) -> datetime | None
```

**Purpose**: Does the same UTC cleanup as `_utc`, but for a time value that may be missing. It avoids forcing callers to repeat the same `None` check.

**Data flow**: It receives either a datetime or `None`. If the value is missing, it returns `None`; otherwise it sends the datetime to `_utc` and returns the normalized result.

**Call relations**: It is used when building task objects and inspection results for fields such as `last_run_at` and `expires_at`, where a task may not have run yet or may not expire.

*Call graph*: calls 1 internal fn (_utc); called by 2 (inspect_many, _task).


##### `_claim_available`  (lines 132–136)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for deciding whether a task can be claimed by a worker. A task is available if nobody has claimed it, or if its old claim has timed out.

**Data flow**: It receives the current time. It produces a SQL condition that matches rows with no `claimed_by` value or with a `claim_expires_at` earlier than that time.

**Call relations**: The workspace candidate query and `ScheduleStore.claim_due` both use this exact condition, so the system agrees on which tasks are worth waking a worker for and which tasks can actually be leased.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `_expired`  (lines 139–143)

```
def _expired(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for deciding whether a task has reached its expiry time. Expired tasks should be removed rather than fired again.

**Data flow**: It receives the current time. It produces a SQL condition matching rows that have an expiry time and whose expiry time is now or in the past.

**Call relations**: The due-workspace finder uses it to wake a sweep for cleanup, and `ScheduleStore.claim_due` uses it to delete expired, claim-available tasks before leasing runnable ones.

*Call graph*: called by 2 (claim_due, due); 1 external calls (and_).


##### `_task`  (lines 146–165)

```
def _task(row: sa.RowMapping) -> ScheduledTask
```

**Purpose**: Turns a raw database row into a `ScheduledTask` value that the rest of the extension can safely use. It is the single place that normalizes task timestamps from the database.

**Data flow**: It receives a row mapping from a SQL query. It copies the row’s identifiers, text fields, claim marker, pause flag, and timestamps into a `ScheduledTask`, converting date fields to UTC-aware values on the way. The output is an in-memory task object.

**Call relations**: Create, update, list, and claim operations all funnel returned rows through this builder. That means callers get the same shape of task no matter which database operation produced it.

*Call graph*: calls 2 internal fn (_utc, _utc_opt); called by 4 (claim_due, create, list, update); 1 external calls (__init__).


##### `due_task_workspaces`  (lines 168–191)

```
def due_task_workspaces() -> WorkspaceCandidates
```

**Purpose**: Provides the job system with a way to find workspaces that may have scheduled-task work to do. It looks for workspaces containing either runnable tasks or expired tasks needing cleanup.

**Data flow**: It creates an inner query function that selects distinct workspace IDs with available due or expired tasks. It hands that query function to the job helper that turns workspace IDs into job candidates.

**Call relations**: This is the scheduled-task runner’s doorway into the job scheduling system. It delegates the actual candidate wrapping to `owner_candidates`, while the nested `due` query describes what counts as interesting work.

*Call graph*: 1 external calls (owner_candidates).


##### `due_task_workspaces.due`  (lines 174–189)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the actual SQL query used to find workspaces with due scheduled-task activity. It includes expired tasks because cleanup should happen even if their next run time is not due.

**Data flow**: It reads the current UTC time, then constructs a query for distinct workspace IDs where a task can be claimed and is either expired or due to run while not paused. The output is a selectable database query, not the rows themselves.

**Call relations**: This nested function is supplied by `due_task_workspaces` to the job-candidate helper. It uses the same `_claim_available` and `_expired` rules as `claim_due`, keeping wake-up decisions aligned with the actual claiming step.

*Call graph*: calls 2 internal fn (_claim_available, _expired); 5 external calls (now, and_, not_, or_, select).


##### `ScheduleStore.workspace_id`  (lines 205–206)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace ID from the extension context. This is the basic safety boundary used by the store’s database operations.

**Data flow**: It reads `ctx.workspace_id` from the store’s context and returns that UUID. It does not change anything.

**Call relations**: The store’s methods use this value when building their database filters, so every create, read, update, delete, claim, and inspection stays inside the current workspace.


##### `ScheduleStore.create`  (lines 208–267)

```
async def create(self, conversation_id: UUID, name: str, schedule: str, prompt: str, description: str, next_run_at: datetime, created_by_member_id: UUID | None=None, expires_at: datetime | None=None,
```

**Purpose**: Creates a new recurring task for the current object agent. It refuses to create a task that reports into a conversation owned by a different agent, because the task must re-enter the right agent when it fires.

**Data flow**: It receives the conversation, name, schedule text, prompt, description, first run time, optional creator, optional expiry, and paused flag. It checks the conversation’s agent, inserts a row with a new ID and timestamps, and returns the new `ScheduledTask`. If another task with the same workspace, agent, and name already exists, it raises an error.

**Call relations**: This is called when a member-facing operation wants to add a scheduled task. It uses `object_agent_id` to bind the task to the current agent, chooses the right database upsert style, and sends the returned row through `_task`.

*Call graph*: calls 1 internal fn (_task); 2 external calls (object_agent_id, uuid4).


##### `ScheduleStore.update`  (lines 269–323)

```
async def update(self, expected: ScheduledTask, schedule: str, prompt: str, description: str, next_run_at: datetime, expires_at: datetime | None=None, *, paused: bool) -> ScheduledTask
```

**Purpose**: Edits an existing task’s schedule, prompt, description, next run time, expiry, and paused state without rewriting its run history. It uses the caller’s expected task as a guard so an edit does not silently overwrite a task that changed meanwhile.

**Data flow**: It receives the task version the caller believes exists plus the new editable fields. It checks that the current agent still matches, updates only the exact row matching the old identity and creator, clears any active claim, and returns the updated task. If no row matches, it raises an error saying the task changed while editing.

**Call relations**: Member-facing edit flows use this after reading a task. It relies on `_creator_matches` to handle creator ownership correctly, uses SQL update to change the row, and turns the result back into a `ScheduledTask` with `_task`.

*Call graph*: calls 2 internal fn (_creator_matches, _task); 2 external calls (update, object_agent_id).


##### `ScheduleStore.cancel`  (lines 325–341)

```
async def cancel(self, expected: ScheduledTask) -> None
```

**Purpose**: Deletes an existing scheduled task, but only if it is still the same task version the caller expected. This protects against cancelling the wrong row after a concurrent edit or ownership change.

**Data flow**: It receives the expected task. It checks that the current object agent is still the task’s executor, deletes the row matching workspace, ID, agent, conversation, name, and creator, and returns nothing. If no row was deleted, it raises an error.

**Call relations**: Cancel operations call this to remove a member’s task. It shares `_creator_matches` with `update`, so creatorless tasks and member-created tasks are matched safely.

*Call graph*: calls 1 internal fn (_creator_matches); 2 external calls (delete, object_agent_id).


##### `ScheduleStore._creator_matches`  (lines 343–348)

```
def _creator_matches(self, expected: ScheduledTask) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition that says whether a stored task has the same creator as the expected task. It treats “no creator” carefully, because database `NULL` values need special matching.

**Data flow**: It receives an expected `ScheduledTask`. If the expected task has no creator, it returns a SQL `IS NULL` condition; otherwise it returns a SQL equality condition for that creator ID.

**Call relations**: Both `update` and `cancel` use this helper when protecting mutations. It prevents an operation authorized for one creator’s task from accidentally landing on a creatorless task, or the other way around.

*Call graph*: called by 2 (cancel, update).


##### `ScheduleStore._listing`  (lines 350–371)

```
def _listing(self, selected: tuple[sa.ColumnElement[Any], ...], *, conversation_id: UUID | None, names: tuple[str, ...] | None, visible_to_member_id: UUID | None, include_all_owners: bool, limit: int
```

**Purpose**: Builds a filtered and ordered query for task listings. It centralizes the common rules for workspace, agent, conversation, name, owner visibility, and limit.

**Data flow**: It receives the columns to select and optional filters such as conversation ID, task names, visible member, owner scope, and limit. It creates a SQL select for the current workspace and current object agent, adds any requested filters, orders by task name, and optionally caps the number of rows.

**Call relations**: `ScheduleStore.list` calls this to avoid duplicating listing rules. It uses `object_agent_id` so member-facing reads only see tasks belonging to the selected object agent.

*Call graph*: called by 1 (list); 2 external calls (select, object_agent_id).


##### `ScheduleStore.list`  (lines 373–392)

```
async def list(self, *, conversation_id: UUID | None=None, names: tuple[str, ...] | None=None, visible_to_member_id: UUID | None=None, include_all_owners: bool=True, limit: int | None=None) -> tuple[S
```

**Purpose**: Returns scheduled tasks visible under the requested filters. It is the basic read operation for listing task definitions.

**Data flow**: It receives optional filters for conversation, names, visible member, owner scope, and limit. It asks `_listing` to build the query, executes it in a transaction, converts each returned row with `_task`, and returns a tuple of `ScheduledTask` objects.

**Call relations**: Member-facing listing code can call this directly when it only needs task rows. `list_reported` builds on it when it also needs conversation audience and surface-label information.

*Call graph*: calls 2 internal fn (_listing, _task); called by 1 (list_reported).


##### `ScheduleStore.list_reported`  (lines 394–428)

```
async def list_reported(self, *, conversation_id: UUID | None=None, names: tuple[str, ...] | None=None, visible_to_member_id: UUID | None=None, include_all_owners: bool=True, limit: int | None=None) -
```

**Purpose**: Lists tasks together with the live conversation details needed to decide how they should be shown to a member. This matters because a task’s visibility comes from the conversation it reports into.

**Data flow**: It receives the same filters as `list`. It first gets the matching tasks, then asks the context for facts about their conversations, and returns `ListedTask` objects containing each task plus audience and surface label. If a task’s conversation is gone, that task is left out.

**Call relations**: This is the richer listing path used by member-facing surfaces. It reuses `list` for task selection, then combines those results with conversation facts before constructing `ListedTask` values.

*Call graph*: calls 1 internal fn (list); 1 external calls (__init__).


##### `ScheduleStore.claim_due`  (lines 430–486)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_TASKS) -> tuple[ScheduledTask, ...]
```

**Purpose**: Lets a background worker lease a small batch of due tasks so it can fire them without another worker firing the same rows at the same time. It also cleans up expired tasks that are safe to remove.

**Data flow**: It receives the current time, lease length in seconds, and a maximum batch size. It creates a fresh claim ID, deletes expired claim-available rows, finds the oldest due unpaused rows that are available, stamps them with the claim and claim expiry time, and returns those claimed tasks.

**Call relations**: The scheduled-task runner calls this during a sweep to get work. It uses `_expired` and `_claim_available` to match the workspace-candidate logic, uses SQL delete for cleanup, SQL update for atomic leasing, and `_task` to return usable task objects.

*Call graph*: calls 3 internal fn (_claim_available, _expired, _task); 6 external calls (timedelta, delete, not_, select, update, uuid4).


##### `ScheduleStore.claim_holds`  (lines 488–519)

```
async def claim_holds(self, task: ScheduledTask) -> bool
```

**Purpose**: Checks whether a claimed task is still exactly the same task before the runner fires it. This narrows the chance of firing something that was edited, cancelled, or reclaimed after it was leased.

**Data flow**: It receives a claimed `ScheduledTask`. If the task has no claim ID, it raises an error. Otherwise it locks and rereads the row by workspace, ID, claim, conversation, agent, name, and schedule, then returns `true` if that exact row still exists and `false` if it does not.

**Call relations**: `ScheduledTaskRunner._fire` calls this immediately before invoking a task. The function does not hand off to other local helpers; it directly asks the database for proof that the lease still owns the same task version.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `ScheduleStore.retire_if_expired`  (lines 521–535)

```
async def retire_if_expired(self, task: ScheduledTask, now: datetime) -> bool
```

**Purpose**: Removes a claimed task if its expiry time has passed before it is invoked. This prevents the runner from firing a task that should already be dead.

**Data flow**: It receives a claimed task and the current time. It raises an error if there is no claim, returns `false` if the task has no expiry or has not expired yet, and otherwise deletes the claimed row and returns `true`.

**Call relations**: `ScheduledTaskRunner._fire` calls this during the firing flow. If it returns `true`, the runner can stop because the task was retired instead of invoked.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


##### `ScheduleStore.reschedule`  (lines 537–567)

```
async def reschedule(self, task: ScheduledTask, next_run_at: datetime, last_run_at: datetime, last_turn_id: UUID | None=None) -> bool
```

**Purpose**: Advances a claimed task after it has fired. It records the run time, optionally records the turn that was created, clears the claim, and sets the next run time.

**Data flow**: It receives a claimed task, the next run time, the last run time, and optionally the last turn ID. It raises an error if the task was not claimed, updates the matching claimed row with the new timing and cleared claim, and returns whether a row was actually updated.

**Call relations**: `ScheduledTaskRunner._fire` calls this after a fire has been accepted. The stored `last_turn_id` later lets inspection show the latest outcome and response text.

*Call graph*: called by 1 (_fire); 1 external calls (update).


##### `ScheduleStore.inspect`  (lines 569–573)

```
async def inspect(self, expected: ScheduledTask) -> TaskInspection | None
```

**Purpose**: Returns the live status picture for one scheduled task. It is a convenience wrapper around the batch inspection path.

**Data flow**: It receives one expected task, calls `inspect_many` with a one-item tuple, and returns the inspection for that task ID if present. If the task no longer matches or is gone, it returns `None`.

**Call relations**: Status-rendering code can call this for one task without dealing with a dictionary. The real work is delegated to `inspect_many`, which keeps single-task and multi-task inspection consistent.

*Call graph*: calls 1 internal fn (inspect_many).


##### `ScheduleStore.inspect_many`  (lines 575–611)

```
async def inspect_many(self, expected: tuple[ScheduledTask, ...]) -> dict[UUID, TaskInspection]
```

**Purpose**: Returns status information for several expected tasks, including their timing marks and the outcome of their latest fired turn. This is what lets a user see not just the schedule, but what happened last time.

**Data flow**: It receives expected `ScheduledTask` objects. It queries matching rows in the current workspace and agent, gathers any recorded last turn IDs, asks the context for those turn outcomes, and builds a dictionary from task ID to `TaskInspection`. Rows whose name or conversation no longer match the expected task are skipped.

**Call relations**: `inspect` calls this for the one-task case, and multi-task status pages can call it directly. It uses `object_agent_id` for the agent boundary, `_utc` and `_utc_opt` for time cleanup, and `TaskInspection` to package the result.

*Call graph*: calls 2 internal fn (_utc, _utc_opt); called by 1 (inspect); 3 external calls (__init__, select, object_agent_id).

## 📊 State Registers Touched

- `reg-schema-version` — The database upgrade position that says which schema changes have already been applied.
- `reg-persistence-handles` — The shared database and blob-storage connections used to read and save durable system data.
- `reg-skill-library` — The stored and packaged reusable skill instructions and files available to agents.
- `reg-workspace-directory` — The shared record of workspaces, members, seats, admins, invitations, and onboarding status.
- `reg-credential-connections` — The encrypted outside-account credentials, reusable connections, and grants that let agents use them.
- `reg-agent-registry` — The saved agents, their owners, visibility, model choices, tool policies, and sandbox settings.
- `reg-conversation-transcripts` — The durable conversation history, compacted records, audiences, and readable timeline data.
- `reg-turn-queue-state` — The durable state of conversation turns, including pending, running, paused, cancelled, and finished work.
- `reg-live-update-streams` — The shared live progress channels that stream text, status, costs, and completion events to clients.
- `reg-surface-routing-state` — The saved routing state for web, Slack, iMessage, terminal, and other public conversation surfaces.
- `reg-inbound-delivery-ledger` — The durable deduplication and delivery records for inbound messages, writebacks, and mid-turn replies.
- `reg-environment-documents` — The saved per-agent run environment describing prompts, tools, skills, files, and model overrides.
- `reg-sandbox-handles` — The remembered sandbox workspaces and conversation sandbox handles used to resume or clean up execution.
- `reg-subagent-delivery-state` — The parent-child task links and owed-result records used when agents spawn helper agents.
- `reg-object-store-and-journal` — The shared workspace object records and change history for agents, tasks, memories, sites, and related items.
- `reg-source-index` — The stored external sources, synced pages, permissions, indexing status, and retry/backoff state.
- `reg-memory-store` — The remembered facts and searchable memory chunks that can be retrieved or condensed later.
- `reg-artifact-blob-store` — The shared files, media blobs, previews, metadata, and signed-download records created by agent work.
- `reg-hosted-site-registry` — The saved hosted-site names, owners, visibility, ports, files, previews, and ingress routing state.
- `reg-scheduled-work-store` — The durable records for recurring tasks, delayed resumes, scheduled fires, and background job claims.
- `reg-notification-inbox` — The stored pending notifications and delivery state used to batch notices and wake conversations.
- `reg-monitor-objective-state` — The saved monitors, objectives, plans, steps, evidence, and blocks that survive across turns.
- `reg-runtime-fleet-liveness` — The shared record of running service and worker instances, heartbeats, listener claims, and stuck work.
- `reg-usage-ledger-balance` — The money and usage ledger that tracks costs, prepaid balances, limits, exports, and billing status.
- `reg-extension-state-store` — Generic per-workspace extension-owned durable key/value or configuration state not covered by a named core store.
- `reg-credential-request-state` — Pending and fulfilled credential-connection requests, OAuth/device-code callback context, and idempotency markers for credential fulfillment.
- `reg-workspace-change-log` — Durable per-conversation sandbox file-change snapshots and summaries used after tool execution and shown in workspace-change slots.
- `reg-enrichment-profile-store` — Cached or recorded person/company enrichment data together with permissions controlling who may use it.
- `reg-transcript-access-audit` — Durable audit records of privileged/admin reads of private member transcripts for compliance and safety review.
- `reg-egress-policy-cache-state` — Per-workspace egress-rule generation and cache-freshness state used by proxies to detect stale sandbox network-access rules.
- `reg-turn-billing-snapshot` — Per-turn frozen billing identity and BYOK attempt state captured before execution and consumed later for stable accounting.
- `reg-workflow-checkpoints` — Durable per-turn workflow checkpoints, serialized runner state, and step/tool-output idempotency records used to resume, cancel, or recover work without rerunning completed actions.
- `reg-proposal-review-state` — Durable reviewable-change proposals with source/target digests, creator, approval state, and publication lifecycle outside the self-improvement prompt-promotion loop.
