# Shared utilities, telemetry-like reporting, accounting, and diagnostics  `stage-22` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support. It does not belong to just startup, request handling, or shutdown. Instead, other parts of the system call into it whenever they need common services.

The blob module is the shared storage drawer for raw bytes, such as uploaded files or generated artifacts. It hides whether the data is kept on the local disk or in S3-style cloud storage, and it keeps workspace data separate from deployment-wide data to avoid accidental mixing.

The listings module gives portal pages a consistent way to move through long lists. Its paging rules keep “next” and “previous” results stable even if new items appear while someone is browsing.

The o11y module is the system’s observability center. Observability means traces, metrics, and logs that help operators understand what happened. It records useful diagnostic information while avoiding leaks of large prompts, tokens, or sensitive text.

The seed module builds a safe demo conversation showing the portal’s features, and cleans up only its own older demo data.

## Files in this stage

### Shared data helpers
Common storage and paging utilities provide consistent behavior for binary assets and portal list navigation.

### `core/src/ufo/blob.py`

`io_transport` · `cross-cutting`

A “blob” here means a bag of bytes, such as an uploaded file, a transcript record, a generated preview, or a web asset. This file is the storage doorway for those blobs. Code elsewhere can ask to put, get, stream, delete, or list blobs without caring whether the real storage is a folder on disk during development or an S3 bucket in production.

The main idea is like using the same mailbox rules for both a home mailbox and a post office box. The caller uses a key, which is a slash-separated name like a path. The backend decides how that key becomes either a file path or an S3 object name.

`FilesystemBlobStore` stores blobs as files under a configured root directory. It writes through a temporary file and then swaps it into place, so readers do not see half-written data. It also checks that keys cannot escape the storage root.

`S3BlobStore` talks to S3 through an async client. It can stream large files in chunks, use multipart uploads for big writes, and create temporary signed URLs so another process can upload or download directly without receiving broad bucket access.

`WorkspaceBlobStore` automatically prefixes keys with the current workspace id. `FleetBlobStore` only allows a small set of deployment-wide prefixes. These wrappers are important safety rails: without them, data from one workspace or subsystem could be mixed with another.

#### Function details

##### `BlobStore.put`  (lines 55–55)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Defines the common promise for saving a whole blob from bytes in one call. Code can depend on this method without knowing whether storage is local files or S3.

**Data flow**: A caller gives a key and a byte string. The concrete store writes those bytes under that key, changing the stored data, and returns nothing when the write is complete.

**Call relations**: This is the interface used by asset-publishing code when it wants to store a finished file. The actual work is supplied by implementations such as the filesystem, S3, workspace, or fleet stores.

*Call graph*: called by 1 (_publish_assets).


##### `BlobStore.get`  (lines 57–57)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Defines the common promise for reading a whole blob into memory. It is meant for data small enough to comfortably load all at once.

**Data flow**: A caller gives a key. The concrete store looks up that key and returns the stored bytes, or raises `BlobNotFound` if there is no such object.

**Call relations**: Transcript readers and extension surfaces call through this interface when they need stored records, identities, or web assets. The protocol lets those callers stay independent of the storage backend.

*Call graph*: called by 4 (read_compaction_after, read_compaction_record, read_identity, _stored_asset).


##### `BlobStore.exists`  (lines 59–59)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Defines the common promise for checking whether a blob is present. It lets callers avoid unnecessary reads or decide whether something needs publishing.

**Data flow**: A caller gives a key. The concrete store checks storage and returns `true` if a blob exists there, or `false` if it does not.

**Call relations**: Slack and web extension code use this interface before reading or publishing stored data. Each backend supplies the matching disk or S3 check.

*Call graph*: called by 3 (read_identity, _publish_assets, _stored_asset).


##### `BlobStore.delete`  (lines 61–63)

```
async def delete(self, key: str) -> None
```

**Purpose**: Defines the common promise for removing a blob. Deleting something that is already gone is treated as success, which makes retries safe.

**Data flow**: A caller gives a key. The concrete store removes any stored object at that key and returns nothing, without treating a missing key as an error.

**Call relations**: This method is part of the shared storage contract. Backend implementations provide the real delete behavior for files or S3 objects.


##### `BlobStore.get_stream`  (lines 65–65)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines the common promise for reading a blob piece by piece. This is used when a blob may be large and loading it all at once would waste memory.

**Data flow**: A caller gives a key. The concrete store opens the blob and yields chunks of bytes until the content is finished, or raises `BlobNotFound` if it is missing.

**Call relations**: Higher-level code can use this protocol for downloads or text extraction without knowing where the bytes are stored. Filesystem and S3 implementations both produce the same kind of async byte stream.


##### `BlobStore.put_stream`  (lines 67–67)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Defines the common promise for writing a blob from incoming chunks. It is the write-side partner to streaming reads.

**Data flow**: A caller gives a key and an async sequence of byte chunks. The concrete store consumes the chunks, writes them as one stored object, and returns when the final object is in place.

**Call relations**: This allows upload paths and generated output paths to save large data without building one huge byte string first. Each backend chooses the safest way to assemble the final object.


##### `BlobStore.list`  (lines 69–73)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Defines the common promise for listing stored blobs under a required key prefix. It returns a bounded list so callers cannot accidentally scan the entire store.

**Data flow**: A caller gives a prefix. The concrete store finds matching objects, packages each as a `BlobEntry` with key, size, and modification time, sorts or preserves a predictable order, and returns a tuple.

**Call relations**: Read views such as transcript compaction browsing can ask for related blobs under a prefix. Backends implement the listing differently, but callers receive the same entry shape.


##### `FilesystemBlobStore.put`  (lines 82–87)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Writes a complete blob to local disk safely. It uses a temporary file first so a crash or interruption is less likely to leave a half-written final file.

**Data flow**: It receives a key and bytes, turns the key into a safe path under the blob root, creates parent folders, writes the bytes to a uniquely named temporary file, then replaces the final path with that file.

**Call relations**: When the configured backend is the filesystem, calls made through the blob interface end here for whole-object writes. It relies on `_resolve` to keep the key inside the configured storage root.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.get`  (lines 89–94)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a complete blob from local disk. It converts normal missing-file errors into the project’s `BlobNotFound` error so all backends behave alike.

**Data flow**: It receives a key, resolves it to a safe path, reads all bytes from that file, and returns them. If the file is absent, it raises `BlobNotFound` instead of exposing the raw filesystem error.

**Call relations**: This is the filesystem version of the shared `get` operation. It depends on `_resolve` for path safety and is used wherever the chosen backend is local storage.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.exists`  (lines 96–98)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a local blob exists as a regular file. It is a lightweight way to test presence without reading the content.

**Data flow**: It receives a key, resolves the key to a safe path, asks the filesystem whether that path is a file, and returns the boolean answer.

**Call relations**: This backs the common `exists` operation for local development or filesystem deployments. `_resolve` is used first so even existence checks cannot probe outside the blob root.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.delete`  (lines 100–102)

```
async def delete(self, key: str) -> None
```

**Purpose**: Removes a local blob if it is present. Missing files are ignored so repeated deletes remain safe.

**Data flow**: It receives a key, resolves it to a safe path, asks the filesystem to unlink that file with `missing_ok`, and returns nothing.

**Call relations**: This is the filesystem implementation of the shared delete operation. It uses `_resolve` before touching disk to enforce the store boundary.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.get_stream`  (lines 104–117)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads a local blob in fixed-size chunks instead of all at once. This protects memory when serving or processing large files.

**Data flow**: It receives a key, resolves and opens the file, repeatedly reads up to the configured chunk size, yields each non-empty chunk, and closes the file afterward. If opening fails because the file is missing, it raises `BlobNotFound`.

**Call relations**: This backs streaming reads for the filesystem backend. Callers see an async stream even though the real file operations are run in worker threads.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.put_stream`  (lines 119–132)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes a local blob from a stream of chunks. Like the whole-file write, it uses a temporary file so the final key appears only after the complete content is written.

**Data flow**: It receives a key and incoming byte chunks, resolves the final path, opens a temporary file, writes each chunk to it, closes it, and replaces the final path. If anything fails, it closes the file, removes the temporary file, and re-raises the error.

**Call relations**: This is the filesystem implementation of streamed writes. It depends on `_resolve` for safety and on a temporary-file pattern to avoid leaving broken final blobs.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.list`  (lines 134–137)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists local blobs under a required prefix. Requiring a prefix prevents accidental whole-store walks.

**Data flow**: It receives a prefix, rejects an empty one, then runs `_walk` in a worker thread. The result is a tuple of matching `BlobEntry` records.

**Call relations**: This is the public list method for the filesystem backend. It hands the detailed directory traversal to `_walk` so the async event loop is not blocked by filesystem scanning.

*Call graph*: 1 external calls (to_thread).


##### `FilesystemBlobStore._walk`  (lines 139–160)

```
def _walk(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Performs the actual directory walk for filesystem listing. It gathers only real stored blobs that match the requested prefix and skips temporary write files.

**Data flow**: It receives a prefix, finds the safe root and starting directory, walks files below that point, converts each matching file path back into a blob key, reads its size and modification time, and returns a sorted, capped tuple of entries.

**Call relations**: `FilesystemBlobStore.list` calls this in a background thread. It uses `_contained_root` and `_resolve` to keep the walk tied to the configured blob area.

*Call graph*: calls 2 internal fn (_contained_root, _resolve); 4 external calls (__init__, fromtimestamp, walk, Path).


##### `FilesystemBlobStore._resolve`  (lines 162–167)

```
def _resolve(self, key: str) -> Path
```

**Purpose**: Turns a blob key into a safe filesystem path. Its main job is to stop keys like `../secret` from escaping the blob directory.

**Data flow**: It reads the configured root, combines it with the key, canonicalizes the result, checks that the final path remains inside the root and is not the root itself, then returns the path or raises `ValueError`.

**Call relations**: Every filesystem operation calls this before reading, writing, deleting, or listing. It delegates root validation to `_contained_root`.

*Call graph*: calls 1 internal fn (_contained_root); called by 7 (_walk, delete, exists, get, get_stream, put, put_stream).


##### `FilesystemBlobStore._contained_root`  (lines 169–182)

```
def _contained_root(self) -> Path
```

**Purpose**: Finds the real storage root directory in a way that is safe but practical for deployment. It allows a not-yet-created root and follows configured symlinks, which are common in container or mounted-volume setups.

**Data flow**: It reads the store’s configured root path and asks containment code to validate it as the `blob.root` setting. If the path does not exist yet, it returns the resolved path so the first write can create it.

**Call relations**: `_resolve` and `_walk` call this whenever they need the canonical root. It is the point where deployment configuration and filesystem safety meet.

*Call graph*: called by 2 (_resolve, _walk); 1 external calls (configured_root).


##### `_is_missing_key`  (lines 185–186)

```
def _is_missing_key(error: ClientError) -> bool
```

**Purpose**: Recognizes S3 error responses that mean “that object does not exist.” Different S3 services may use different codes for the same situation.

**Data flow**: It receives a `ClientError`, looks inside the response for the error code, and returns whether that code is one of the known missing-object codes.

**Call relations**: S3 reads, existence checks, and streaming reads call this when S3 reports an error. It lets them translate missing objects into `false` or `BlobNotFound` while re-raising other failures.

*Call graph*: called by 3 (exists, get, get_stream).


##### `S3BlobStore.put`  (lines 205–207)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Writes a complete blob to an S3 bucket in one request. It is best for data already available as one byte string.

**Data flow**: It receives a key and bytes, gets or creates the async S3 client for the current event loop, sends a `put_object` request to the configured bucket, and returns when S3 accepts it.

**Call relations**: This is the S3 implementation of the shared whole-object write. It relies on `_client` for connection setup and reuse.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get`  (lines 209–219)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a complete blob from S3 into memory. It normalizes S3’s missing-object errors into `BlobNotFound`.

**Data flow**: It receives a key, gets the S3 client, requests the object, reads the response body fully, and returns the bytes. If S3 says the key is missing, it raises `BlobNotFound`; other S3 errors are left unchanged.

**Call relations**: This is the S3 version of the common `get` operation. It calls `_is_missing_key` to decide which S3 errors mean normal absence.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.exists`  (lines 221–229)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether an object exists in S3 without downloading it. It uses S3 metadata lookup for a cheap presence test.

**Data flow**: It receives a key, gets the S3 client, sends a `head_object` request, and returns `true` if it succeeds. If S3 reports a missing key, it returns `false`; other errors are raised.

**Call relations**: This backs the shared `exists` operation for S3. `_is_missing_key` keeps missing objects from being treated as unexpected failures.

*Call graph*: calls 2 internal fn (_client, _is_missing_key).


##### `S3BlobStore.delete`  (lines 231–233)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes an object from S3. S3 delete calls are naturally safe for absent keys, matching the project’s delete contract.

**Data flow**: It receives a key, gets the S3 client, sends a delete request for that bucket and key, and returns when the request completes.

**Call relations**: This is the S3 implementation of the common delete operation. It depends on `_client` for the actual S3 connection.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get_stream`  (lines 235–246)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams an S3 object in chunks. This is used for large blobs where reading the full body into memory would be expensive.

**Data flow**: It receives a key, asks S3 for the object, then yields chunks from the response body up to the configured chunk size. If S3 says the object is missing, it raises `BlobNotFound`.

**Call relations**: This is the S3 partner to filesystem streaming reads. It calls `_client` to reach S3 and `_is_missing_key` to translate absence consistently.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.put_stream`  (lines 248–292)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes an incoming byte stream to S3. Small streams are sent as one object; larger streams use S3 multipart upload, which uploads pieces and then joins them on S3’s side.

**Data flow**: It receives a key and byte chunks, buffers data until it reaches the multipart part size, starts a multipart upload if needed, uploads parts, and completes the upload at the end. If an error happens after multipart upload starts, it aborts the upload so stray unfinished parts are not left behind.

**Call relations**: This is the S3 implementation of streamed writes. It gets its S3 client from `_client` and hides multipart details from callers using the simple blob interface.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_put`  (lines 294–318)

```
async def presigned_put(self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary upload URL for exactly one measured file. The URL is signed with the expected size and checksum, so someone holding it can upload only those bytes to that key before it expires.

**Data flow**: It receives a key, byte size, SHA-256 checksum, and time-to-live. It asks the S3 client to generate a signed `put_object` URL with those constraints and returns the URL string.

**Call relations**: Workspace storage calls this when S3 is the backend and an untrusted sandbox needs to upload directly. `_client` supplies the signer and S3 configuration.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_put_unmeasured`  (lines 320–330)

```
async def presigned_put_unmeasured(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary upload URL for a fixed key without fixing the body size or checksum. This is for trusted producers that do not know the final length before generating the content.

**Data flow**: It receives a key and time-to-live, asks S3 to generate a signed PUT URL for that bucket and key, and returns the URL.

**Call relations**: Workspace storage exposes this only when backed by S3. It still limits the holder to one key and one expiry window, but delegates signing to `_client`.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_get`  (lines 332–339)

```
async def presigned_get(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary download URL for an S3 object. Anyone with the URL can read that one object until the URL expires.

**Data flow**: It receives a key and time-to-live, asks S3 to generate a signed GET URL, and returns the URL string.

**Call relations**: Workspace storage uses this when callers need direct, time-limited access to a stored object. `_client` provides the S3 signer.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.put_host`  (lines 341–352)

```
async def put_host(self) -> str
```

**Purpose**: Finds the hostname that a presigned upload URL will contact. This is used so an egress proxy can allow the sandbox to reach exactly the S3 host needed for uploads.

**Data flow**: It gets the S3 client, reads the client endpoint URL, extracts its hostname, and returns either that hostname or the bucket-prefixed AWS hostname depending on whether a custom endpoint is configured.

**Call relations**: This ties S3 signing behavior to network allow rules. By reading from the actual client, it avoids mismatches between the generated URL and what the sandbox is allowed to contact.

*Call graph*: calls 1 internal fn (_client); 1 external calls (urlsplit).


##### `S3BlobStore.list`  (lines 354–371)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists S3 objects under a required prefix. It returns a capped set of entries so callers do not accidentally enumerate an entire bucket.

**Data flow**: It receives a prefix, rejects an empty one, gets an S3 paginator, reads pages of matching objects, converts each object into a `BlobEntry`, stops once the cap is reached, and returns the tuple.

**Call relations**: This is the S3 implementation of the shared list operation. It depends on `_client` for S3 access and produces the same entry shape as the filesystem backend.

*Call graph*: calls 1 internal fn (_client); 1 external calls (__init__).


##### `S3BlobStore._client`  (lines 373–399)

```
async def _client(self) -> AioBaseClient
```

**Purpose**: Creates and reuses one async S3 client per event loop. This avoids expensive client construction on every blob operation and respects that the underlying HTTP client is tied to the event loop it was created on.

**Data flow**: It reads the current event loop, checks the store’s client cache, and returns the cached client if present. Otherwise it creates an S3 client with pinned signing and addressing settings, stores it in the cache, closes any redundant racing client, and returns the chosen client.

**Call relations**: Every S3 operation calls this before talking to S3. If a duplicate client is created during a race, it logs a close failure through the observability logger rather than disrupting normal storage work.

*Call graph*: called by 11 (delete, exists, get, get_stream, list, presigned_get, presigned_put, presigned_put_unmeasured, put, put_host (+1 more)); 3 external calls (get_session, get_running_loop, log).


##### `WorkspaceBlobStore.put`  (lines 412–413)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Writes bytes under the current workspace’s private blob prefix. Callers use workspace-relative keys and do not need to pass the workspace id.

**Data flow**: It receives a relative key and bytes, expands the key with `_full`, then passes the full key and bytes to the backend store.

**Call relations**: This wrapper sits between workspace-aware code and the raw backend. `_full` supplies the current workspace prefix before the filesystem or S3 store writes the data.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.get`  (lines 415–416)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a blob from the current workspace. It prevents callers from accidentally reaching another workspace by always adding the active workspace prefix.

**Data flow**: It receives a relative key, turns it into a full workspace key with `_full`, asks the backend for the bytes, and returns them.

**Call relations**: Workspace-level callers use this instead of the raw backend. The actual read is handed off to either the filesystem or S3 store after prefixing.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.exists`  (lines 418–419)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a blob exists in the current workspace. The caller only names the file within the workspace.

**Data flow**: It receives a relative key, prefixes it with the current workspace through `_full`, asks the backend whether that full key exists, and returns the answer.

**Call relations**: This keeps presence checks inside the workspace boundary. `_full` is the safety gate before the backend is contacted.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.delete`  (lines 421–422)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes a blob from the current workspace. It follows the same workspace-prefix rule as reads and writes.

**Data flow**: It receives a relative key, builds the full workspace key, asks the backend to delete it, and returns nothing.

**Call relations**: Workspace code calls this when removing workspace-owned data. The backend performs the actual disk or S3 delete after `_full` chooses the namespace.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.get_stream`  (lines 424–428)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Opens a streaming read for a blob in the current workspace. It resolves the workspace prefix immediately, so the stream can continue even if the surrounding workspace scope ends later.

**Data flow**: It receives a relative key, converts it right away to a full workspace key, and returns the backend’s async byte stream for that full key.

**Call relations**: Context-building code uses this to read member blob text. The important handoff is early: `_full` captures the workspace before the backend starts yielding chunks.

*Call graph*: calls 1 internal fn (_full); called by 1 (_member_blob_text).


##### `WorkspaceBlobStore.put_stream`  (lines 430–431)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes a streamed blob into the current workspace. It is useful for large workspace files that arrive in pieces.

**Data flow**: It receives a relative key and an async stream of byte chunks, expands the key with `_full`, and passes both to the backend streamed writer.

**Call relations**: This is the workspace-safe wrapper around backend streaming writes. The backend decides whether that means a temporary file or S3 multipart upload.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.list`  (lines 433–438)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists blobs under a prefix within the current workspace and returns keys relative to that workspace. Callers do not see the internal `workspaces/<id>/` prefix.

**Data flow**: It receives a relative prefix, rejects an empty one, builds the workspace root prefix, asks the backend to list under that full prefix, then strips the workspace root from each returned key.

**Call relations**: This wraps backend listing for workspace callers. It uses `_full` to form the backend prefix and `replace` to return entries with cleaner, workspace-relative keys.

*Call graph*: calls 1 internal fn (_full); 1 external calls (replace).


##### `WorkspaceBlobStore.presigned_put`  (lines 440–451)

```
async def presigned_put(self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a measured temporary upload URL for a blob in the current workspace. This only works when the backend is S3, because local files cannot provide S3-style signed URLs.

**Data flow**: It receives a relative key, size, checksum, and expiry, turns the key into a full workspace key, and asks the S3 backend to create the constrained upload URL. If the backend is not S3, it raises `TypeError`.

**Call relations**: Workspace upload flows call this after they have established that S3 is in use. It delegates the signing details to `S3BlobStore.presigned_put`.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.presigned_put_unmeasured`  (lines 453–459)

```
async def presigned_put_unmeasured(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates an unconstrained-by-size temporary upload URL for a fixed key in the current workspace. It is intended for trusted producers whose output size is unknown beforehand.

**Data flow**: It receives a relative key and expiry, prefixes the key for the current workspace, and asks the S3 backend for an unmeasured signed PUT URL. If storage is not S3, it raises `TypeError`.

**Call relations**: This is the workspace wrapper over `S3BlobStore.presigned_put_unmeasured`. `_full` keeps the URL limited to the active workspace’s namespace.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore.presigned_get`  (lines 461–468)

```
async def presigned_get(self, key: str, ttl_seconds: int) -> str
```

**Purpose**: Creates a temporary download URL for a blob in the current workspace. It is only available with the S3 backend.

**Data flow**: It receives a relative key and expiry, builds the full workspace key, and asks the S3 backend for a signed GET URL. If the backend is not S3, it raises `TypeError`.

**Call relations**: Workspace-facing download flows use this to grant short-lived direct access. The method hands signing to `S3BlobStore.presigned_get` after applying the workspace boundary.

*Call graph*: calls 1 internal fn (_full).


##### `WorkspaceBlobStore._full`  (lines 470–473)

```
def _full(self, key: str) -> str
```

**Purpose**: Builds the real storage key for the current workspace. It is the central guardrail that keeps workspace-relative keys from crossing into other workspaces.

**Data flow**: It receives a caller-supplied key, rejects it if it is already prefixed with `workspaces/`, reads the current workspace id from the ambient workspace scope, and returns `workspaces/<id>/<key>`.

**Call relations**: Every workspace store operation calls this before using the backend. If there is no current workspace scope, the workspace lookup fails rather than silently using the wrong namespace.

*Call graph*: called by 10 (delete, exists, get, get_stream, list, presigned_get, presigned_put, presigned_put_unmeasured, put, put_stream); 1 external calls (ws_current).


##### `FleetBlobStore.put`  (lines 484–485)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Writes deployment-wide data, such as static assets or terminal spill data, to the backend. It only accepts keys in approved fleet namespaces.

**Data flow**: It receives a key and bytes, checks the key with `_checked`, then writes the bytes to the backend under that same key.

**Call relations**: This is the fleet-safe wrapper for whole-object writes. `_checked` prevents callers from using this unprefixed path to bypass workspace storage.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.get`  (lines 487–488)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads deployment-wide data from an approved fleet namespace. It is not for workspace-owned blobs.

**Data flow**: It receives a key, validates that the key starts with an allowed fleet prefix, asks the backend for the bytes, and returns them.

**Call relations**: Fleet-level callers use this wrapper instead of the raw backend. The backend performs the actual filesystem or S3 read after `_checked` approves the namespace.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.exists`  (lines 490–491)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether an approved fleet blob is present. It keeps the check limited to deployment-wide namespaces.

**Data flow**: It receives a key, validates the prefix, asks the backend whether that key exists, and returns the boolean result.

**Call relations**: This supports safe presence checks for fleet data. `_checked` is called first so existence checks cannot probe workspace or arbitrary keys.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.delete`  (lines 493–494)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes a deployment-wide blob from an approved namespace. It refuses keys outside the fleet areas.

**Data flow**: It receives a key, checks that the prefix is allowed, asks the backend to delete it, and returns nothing.

**Call relations**: This is the fleet wrapper around backend deletion. `_checked` keeps deletion from reaching workspace-owned data.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.get_stream`  (lines 496–497)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a deployment-wide blob from an approved namespace. It is useful for large fleet-owned payloads.

**Data flow**: It receives a key, validates the prefix, and returns the backend’s async stream for that key.

**Call relations**: This mirrors the shared streaming read behavior while enforcing fleet namespace rules through `_checked` before the backend is touched.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.put_stream`  (lines 499–500)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed deployment-wide data to an approved namespace. It allows large fleet blobs to be saved without buffering them all in memory.

**Data flow**: It receives a key and incoming byte chunks, validates the key, then passes the stream to the backend’s streamed writer.

**Call relations**: This wraps backend streaming writes for fleet data. `_checked` decides whether the key is allowed before filesystem or S3 upload begins.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore.list`  (lines 502–503)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists deployment-wide blobs under an approved fleet prefix. It refuses prefixes outside the fleet namespaces.

**Data flow**: It receives a prefix, checks that it starts with an allowed fleet prefix, asks the backend to list matching keys, and returns the backend’s entries.

**Call relations**: This is the fleet-safe version of listing. It delegates enumeration to the backend only after `_checked` confirms the prefix cannot expose workspace data.

*Call graph*: calls 1 internal fn (_checked).


##### `FleetBlobStore._checked`  (lines 505–508)

```
def _checked(self, key: str) -> str
```

**Purpose**: Validates that a key belongs to one of the allowed fleet namespaces. It is the simple gate that separates deployment-wide blobs from workspace blobs.

**Data flow**: It receives a key, checks whether it starts with one of the allowed prefixes, returns the key unchanged if valid, or raises `ValueError` if not.

**Call relations**: Every fleet store operation calls this before using the backend. That makes the otherwise unprefixed fleet store safe to expose to code that should not touch arbitrary blob keys.

*Call graph*: called by 7 (delete, exists, get, get_stream, list, put, put_stream).


##### `blob_store_for`  (lines 511–523)

```
def blob_store_for(config: BlobConfig) -> FilesystemBlobStore | S3BlobStore
```

**Purpose**: Builds the low-level blob backend from configuration. It chooses local filesystem storage or S3 storage based on the configured backend name.

**Data flow**: It receives a `BlobConfig`, checks which backend is requested, verifies the required fields are present, then returns a `FilesystemBlobStore` or `S3BlobStore` with the configured settings.

**Call relations**: Startup or setup code uses this to turn configuration into a working storage object. Workspace and fleet wrappers can then be layered on top of the returned backend.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/listings.py`

`domain_logic` · `request handling`

This file solves a common listing problem: if a user is reading page 2 while new items arrive, simple page numbers can repeat or skip items. Instead, it uses keyset paging, which means each page starts from a real row position, not from a count like “skip 20 rows.” Think of it like using a bookmark in a stack of papers rather than saying “go to the 21st paper,” because the stack may change while you read.

All listings here use the same order: newest first, using `created_at` as the main sort value and the row id as a tie-breaker when two rows have the same time. A `ListingCursor` is that bookmark. It stores the row time, the row id, and whether the user is asking for newer or older rows.

`page_query` prepares a database query so it fetches the right slice of rows, plus one extra row. That extra row is not shown; it only tells the code whether another page exists. `page_of` then turns the raw rows into a `ListingPage`, creates the older/newer cursor links, and reverses rows when needed so the user always sees them newest-first. If a cursor token is malformed, the file raises `MalformedCursor` instead of silently showing the wrong page.

#### Function details

##### `ListingCursor.encode`  (lines 42–45)

```
def encode(self) -> str
```

**Purpose**: Turns a cursor into a compact text token that can travel in a web link or query string. This is how the system gives the user a bookmark for the next or previous page.

**Data flow**: It starts with a `ListingCursor` containing a timestamp, an item id, and a direction flag. It writes those three pieces into one string separated by `|`, using `newer` or `older` to describe the direction. The result is a single cursor token that can be sent back by a client later.

**Call relations**: This is the counterpart to `ListingCursor.decode`: pages create cursor objects, and `encode` makes them safe to place in navigation controls. Other listing code can then hand those encoded tokens to clients without exposing separate timestamp and id fields.


##### `ListingCursor.decode`  (lines 48–60)

```
def decode(cls, token: str) -> 'ListingCursor'
```

**Purpose**: Reads a cursor token from a client and turns it back into a safe, structured cursor. It rejects tokens that do not describe a real listing position.

**Data flow**: It receives one string token. It splits the token into direction, timestamp, and item id; checks that the direction is either `newer` or `older`; parses the timestamp; and verifies that the id is a valid UUID, which is a standard unique identifier format. If anything is missing or invalid, it raises `MalformedCursor`; otherwise it returns a `ListingCursor` ready for paging.

**Call relations**: The web surface functions for workspace artifacts, memory, and radar call this when a request includes a cursor. After decoding, the resulting cursor can be passed into the paging flow so the database query starts from the exact row the link named.

*Call graph*: called by 3 (workspace_artifacts, workspace_memory, workspace_radar); 3 external calls (__init__, fromisoformat, UUID).


##### `page_query`  (lines 74–96)

```
def page_query(query: sa.Select[Any], cursor: ListingCursor | None, limit: int, *, created_at: sa.ColumnElement[datetime], ident: sa.ColumnElement[Any]) -> sa.Select[Any]
```

**Purpose**: Takes a caller’s database query and adds the ordering, boundary, and limit needed to fetch one stable page. It makes sure paging is based on row positions instead of fragile page offsets.

**Data flow**: It receives a SQLAlchemy `Select` query, an optional cursor, a page size, and the two database columns that define position: creation time and id. If there is no cursor, it asks for the newest rows. If the cursor asks for newer rows, it temporarily sorts oldest-to-newest so it can walk in that direction; otherwise it sorts newest-to-oldest. It also adds a limit of `limit + 1`, so the extra row can prove whether another page exists. When a cursor is present, it adds a comparison against the cursor’s timestamp and id so only rows on the requested side are fetched. The output is a modified query ready to run.

**Call relations**: Listing endpoints or providers use this before talking to the database. Its output feeds into the later shaping step, usually `page_of`, which interprets the fetched rows and builds the page envelope with navigation cursors.

*Call graph*: 2 external calls (tuple_, UUID).


##### `page_of`  (lines 99–128)

```
def page_of(rows: Sequence[SourceT], cursor: ListingCursor | None, limit: int, *, render: Callable[[SourceT], RowT], position: Callable[[SourceT], tuple[datetime, str]]) -> ListingPage[RowT]
```

**Purpose**: Turns fetched rows into a finished listing page: visible rows plus optional older and newer cursors. It is the step that decides which navigation buttons should exist.

**Data flow**: It receives the rows returned from `page_query`, the cursor that led here, the requested limit, a `render` function that converts raw rows into response rows, and a `position` function that extracts each row’s timestamp and id. It checks whether there is one extra row beyond the limit, trims that extra row away, and reverses the page if the query had to walk toward newer rows. It then builds cursor bookmarks from the first and last visible rows when there are more rows in those directions. The result is a `ListingPage` containing rendered rows and optional `older` and `newer` cursors.

**Call relations**: This usually runs after a database query prepared by `page_query` has returned rows. It hands the final page object back to listing surfaces or providers, which can then show the rows and expose navigation links to clients.

*Call graph*: 1 external calls (__init__).


##### `page_of.at`  (lines 118–120)

```
def at(source: SourceT, *, newer: bool) -> ListingCursor
```

**Purpose**: Creates a cursor for one boundary row in a page. It is a small helper used so `page_of` can build the older and newer navigation bookmarks consistently.

**Data flow**: It receives one source row and a direction flag. It calls the supplied `position` function to pull out that row’s creation time and id, then creates a `ListingCursor` with those values and the requested direction. The output is a cursor pointing exactly at that row boundary.

**Call relations**: This helper is used only inside `page_of`. When `page_of` has decided that an older or newer page exists, it calls `page_of.at` on the last or first visible row to produce the cursor that will lead to that next page.

*Call graph*: 1 external calls (__init__).


### Observability reporting
The observability hub records traces, metrics, and structured logs while guarding against sensitive or oversized log content.

### `core/src/ufo/o11y.py`

`io_transport` · `startup and cross-cutting runtime`

This file makes the system visible without forcing every caller to repeat the same logging and tracing setup. Observability means the clues operators use after the fact: logs for events, metrics for counts and timings, and traces for a timeline of work across steps. At startup, init_o11y can connect these signals to an OpenTelemetry OTLP collector, which is a standard service that receives telemetry over HTTP. If no collector is configured, the project still installs its log-size guard so noisy third-party libraries cannot dump huge prompt-like messages to stderr. During normal work, callers use helpers such as span, turn_span, log, warn, log_error, emit_metric, and emit_histogram. These helpers automatically add the current workspace, hide sensitive fields, and keep metric labels controlled so dashboards do not explode into endless unique series. A useful analogy is a shipping label system: each package of telemetry gets a safe, standardized label before it leaves the app. The file also includes careful exception stack formatting that keeps class names and frames but not exception messages, because messages can contain user or sandbox text. Without this file, failures would be harder to connect across queues and turns, dashboards could become noisy or expensive, and private content could leak through logs.

#### Function details

##### `init_o11y`  (lines 252–278)

```
def init_o11y(otlp_endpoint: str | None) -> None
```

**Purpose**: Sets up observability for the process. It installs the log message guard every time, and when an OTLP endpoint is provided, it connects traces, metrics, and logs to the OpenTelemetry collector.

**Data flow**: It receives an optional collector base URL. First it installs the oversized-log guard; if there is no endpoint, it stops there. If there is an endpoint, it builds separate trace, metric, and log URLs, creates OpenTelemetry providers and exporters, stores them as the process-wide defaults, and finally bridges standard warning logs into the log pipeline.

**Call relations**: This is the startup doorway for the rest of the file. It calls _guard_log_messages before anything else, uses _otlp_signal_urls to build the export destinations, and then calls _bridge_warning_logs so warnings from ordinary Python loggers can also reach the collector.

*Call graph*: calls 3 internal fn (_bridge_warning_logs, _guard_log_messages, _otlp_signal_urls); 13 external calls (set_logger_provider, OTLPLogExporter, OTLPMetricExporter, OTLPSpanExporter, set_meter_provider, LoggerProvider, BatchLogRecordProcessor, MeterProvider, PeriodicExportingMetricReader, create (+3 more)).


##### `_bridge_warning_logs`  (lines 281–293)

```
def _bridge_warning_logs(logger_provider: LoggerProvider) -> None
```

**Purpose**: Sends ordinary Python warning-and-error logs into the OpenTelemetry log pipeline. This catches important warnings from libraries or other modules that are not using this file's structured log helpers.

**Data flow**: It receives an OpenTelemetry logger provider. It creates a standard logging handler that exports warning-level and higher records, filters out this project's own structured logger and OpenTelemetry's own exporter logs, and attaches the handler to Python's root logger.

**Call relations**: init_o11y calls this after the log provider is ready. It acts as a bridge from the wider Python logging world into the configured collector, while avoiding loops or duplicate logs from ufo and OpenTelemetry itself.

*Call graph*: called by 1 (init_o11y); 2 external calls (getLogger, LoggingHandler).


##### `_GuardedRecordFactory.__call__`  (lines 323–335)

```
def __call__(self, *args: object, **kwargs: object) -> logging.LogRecord
```

**Purpose**: Inspects every newly created standard Python log record and replaces dangerously large messages with a short safe summary. This prevents third-party libraries from printing huge prompt-like data or other sensitive blobs.

**Data flow**: It receives the raw arguments used to create a log record. It asks the original factory to create the record, renders the message safely through _rendered_message, and if the message is too long, replaces it with a short note naming the logger, level, dropped length, and size limit. It returns the original or modified record.

**Call relations**: _guard_log_messages installs this factory so it runs before any handler or exporter sees a record. It relies on _rendered_message to avoid crashing on malformed logging templates.

*Call graph*: calls 1 internal fn (_rendered_message).


##### `_guard_log_messages`  (lines 338–342)

```
def _guard_log_messages() -> None
```

**Purpose**: Installs the global oversized-log guard if it is not already installed. This makes sure every standard Python log record gets checked once.

**Data flow**: It reads Python's current log record factory. If that factory is already a _GuardedRecordFactory, it does nothing; otherwise it wraps the current factory and sets the wrapper as the new global factory.

**Call relations**: init_o11y calls this during startup, even when telemetry export is disabled. After installation, _GuardedRecordFactory.__call__ is invoked by Python logging whenever any logger creates a record.

*Call graph*: called by 1 (init_o11y); 3 external calls (__init__, getLogRecordFactory, setLogRecordFactory).


##### `_rendered_message`  (lines 345–355)

```
def _rendered_message(record: logging.LogRecord) -> str | None
```

**Purpose**: Safely gets the final text of a log record so the size guard can judge it. It avoids turning a broken logging template into a new application error.

**Data flow**: It receives a logging record. If the record is already a plain string with no arguments, it returns that string. Otherwise it asks the record to format itself; if formatting raises an exception, it returns None instead of raising.

**Call relations**: _GuardedRecordFactory.__call__ uses this helper before deciding whether a record is too large. It isolates the risky part of message formatting from the global logging guard.

*Call graph*: called by 1 (__call__); 1 external calls (getMessage).


##### `_otlp_signal_urls`  (lines 358–364)

```
def _otlp_signal_urls(otlp_endpoint: str) -> tuple[str, str, str]
```

**Purpose**: Builds the exact HTTP URLs used to send traces, metrics, and logs to an OTLP collector. This matters because the exporter does not add these paths by itself.

**Data flow**: It receives a collector base URL, removes any trailing slash, and returns three URLs ending in v1/traces, v1/metrics, and v1/logs.

**Call relations**: init_o11y calls this before constructing the OpenTelemetry exporters. The returned URLs tell each exporter where to send its kind of telemetry.

*Call graph*: called by 1 (init_o11y).


##### `_ambient_scope`  (lines 367–372)

```
def _ambient_scope() -> dict[str, str]
```

**Purpose**: Finds the current workspace and turns it into metadata for logs and spans. This lets code inside a workspace scope be tagged automatically without passing the workspace ID through every call.

**Data flow**: It reads current_workspace from the database context. If no workspace is active, it returns an empty dictionary; otherwise it returns a dictionary containing the workspace ID as text.

**Call relations**: _emit_log, span, and turn_span call this when building telemetry attributes. It is the shared source of workspace tagging across logs and traces.

*Call graph*: called by 3 (_emit_log, span, turn_span); 1 external calls (get).


##### `current_traceparent`  (lines 375–381)

```
def current_traceparent() -> str | None
```

**Purpose**: Captures the current trace identity as a standard W3C traceparent header. This is useful when work crosses a queue or storage boundary and later needs to reconnect to the same trace.

**Data flow**: It starts with an empty carrier dictionary, asks the trace context propagator to inject the current trace into it, and returns the traceparent value if one was produced. If there is no valid current trace, it returns None.

**Call relations**: No direct caller is shown in this file's graph. It is a public helper for code that needs to save or pass along trace context so a later turn can continue the same trace.


##### `turn_profile`  (lines 384–392)

```
def turn_profile(subagent_profile: str | None, spawned: bool=False) -> str
```

**Purpose**: Chooses the small, safe profile label used for a turn. It separates main user-facing work, spawned agent work, and configured subagent profiles without using high-cardinality IDs.

**Data flow**: It receives an optional subagent profile and a flag saying whether the turn was spawned. If a subagent profile is present, it returns that; otherwise it returns agent for spawned turns or main for ordinary turns.

**Call relations**: turn_span calls this while preparing trace attributes. The result matches the profile dimension used by metrics, so traces and metrics can be compared consistently.

*Call graph*: called by 1 (turn_span).


##### `turn_span`  (lines 396–432)

```
def turn_span(turn_id: UUID, conversation_id: UUID, traceparent: str | None, subagent_profile: str | None, parent_turn_id: UUID | None) -> Iterator[Span]
```

**Purpose**: Creates the top-level trace span for one durable turn. A span is a timed block in a trace, and this one represents the whole turn so queue waits and child work can be connected.

**Data flow**: It receives turn and conversation IDs, an optional saved traceparent, an optional subagent profile, and an optional parent turn ID. It builds safe attributes, adds ambient workspace data, extracts a parent trace context when a traceparent is present, opens an OpenTelemetry SERVER span named turn, yields it to the caller's block, and closes it when the block exits.

**Call relations**: This is used by turn-running code to wrap a turn's work. Inside, it calls turn_profile, _ambient_scope, and redact_payload before asking OpenTelemetry for a tracer and starting the span.

*Call graph*: calls 3 internal fn (_ambient_scope, redact_payload, turn_profile); 2 external calls (get_tracer, cast).


##### `span`  (lines 436–448)

```
def span(name: str, kind: SpanKind=SpanKind.INTERNAL, **attributes: object) -> Iterator[Span]
```

**Purpose**: Creates a trace span for a smaller stage of work, such as a model call or tool dispatch. It gives operators a waterfall-style timeline inside the larger trace.

**Data flow**: It receives a span name, an optional span kind, and arbitrary attributes. It adds the ambient workspace, redacts sensitive values, flattens values into span-safe attributes with ufo-prefixed names, opens the span, yields it to the caller's block, and closes it afterward.

**Call relations**: Callers use this around meaningful chunks of runtime work. It shares _ambient_scope and redact_payload with turn_span so nested spans use the same safety and workspace rules.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); 1 external calls (get_tracer).


##### `redact_payload`  (lines 451–457)

```
def redact_payload(fields: Mapping[str, object]) -> dict[str, JsonValue]
```

**Purpose**: Removes fields with sensitive names and redacts nested values into safe JSON-like data. This is the main privacy filter before logs or trace attributes leave the process.

**Data flow**: It receives a mapping of field names to values. For each field, it normalizes the key by removing underscores and dashes and lowercasing it; sensitive keys are dropped, while all other values are passed to redact_value. It returns a new dictionary of safe values.

**Call relations**: _emit_log, span, and turn_span call this before sending data to logging or tracing. redact_value also calls it recursively when it finds a nested dictionary.

*Call graph*: calls 1 internal fn (redact_value); called by 4 (_emit_log, redact_value, span, turn_span).


##### `redact_value`  (lines 460–470)

```
def redact_value(value: object) -> JsonValue
```

**Purpose**: Converts one value into a JSON-like safe form, recursing through lists and dictionaries. It keeps simple values as they are and stringifies unusual objects.

**Data flow**: It receives any Python object. None, booleans, numbers, and strings pass through; mappings are converted through redact_payload; non-string sequences become lists of redacted items; everything else becomes its string form.

**Call relations**: redact_payload calls this for each non-sensitive field. When this function sees a nested mapping, it hands that nested data back to redact_payload so key-based redaction still applies.

*Call graph*: calls 1 internal fn (redact_payload); called by 1 (redact_payload).


##### `log`  (lines 473–478)

```
def log(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured informational event. Use it for normal noteworthy events that should appear in logs and correlate with the current trace.

**Data flow**: It receives an event name and optional fields. It passes them to _emit_log with INFO severity, which adds scope, redacts data, and emits the record through both standard logging and OpenTelemetry logs.

**Call relations**: This is one of the public logging helpers. It delegates the shared work to _emit_log so info, warning, and error logs behave the same way apart from severity.

*Call graph*: calls 1 internal fn (_emit_log).


##### `log_error`  (lines 481–483)

```
def log_error(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured error event. Use it when something failed and operators should see it as an error.

**Data flow**: It receives an event name and fields describing the failure. It passes them to _emit_log with ERROR severity, which adds workspace context, removes sensitive data, and emits the record.

**Call relations**: This public helper shares the same path as log and warn through _emit_log. The only difference is the severity it supplies.

*Call graph*: calls 1 internal fn (_emit_log).


##### `warn`  (lines 486–488)

```
def warn(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured warning event. Use it for expected but important conditions that are not full errors.

**Data flow**: It receives an event name and optional fields. It calls _emit_log with warning severity so the event is recorded with the same redaction and workspace tagging as other structured logs.

**Call relations**: This public helper sits beside log and log_error. It hands all common work to _emit_log and only chooses the warning level.

*Call graph*: calls 1 internal fn (_emit_log).


##### `formatted_stack`  (lines 491–520)

```
def formatted_stack(error: BaseException) -> str
```

**Purpose**: Formats an exception's class names and stack frames for safe logging without including exception messages. This helps diagnose failures while avoiding accidental leakage of user, prompt, or sandbox text.

**Data flow**: It receives an exception. It walks through the exception and its cause or context chain, avoiding loops, records each exception class name and traceback frames, and joins them into one string. If the result is too long, it keeps the beginning and end and replaces the middle with an elision marker.

**Call relations**: No direct caller is shown in this file's graph, but it is designed for log fields passed to the logging helpers. It uses traceback formatting for frames while deliberately excluding message text.

*Call graph*: 1 external calls (format_tb).


##### `_emit_log`  (lines 523–537)

```
def _emit_log(event: str, severity_number: SeverityNumber, severity_text: str, level: int, fields: Mapping[str, object]) -> None
```

**Purpose**: Performs the common work behind info, warning, and error structured logs. It emits one safe event through both Python logging and OpenTelemetry logs.

**Data flow**: It receives an event name, OpenTelemetry severity details, a standard logging level, and fields. It adds ambient workspace fields, redacts the combined payload, writes to the ufo standard logger with the payload in extra data, and emits an OpenTelemetry log record with the same attributes.

**Call relations**: log, warn, and log_error all call this. It calls _ambient_scope and redact_payload before handing the finished record to Python logging and the OpenTelemetry logger.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); called by 3 (log, log_error, warn); 2 external calls (getLogger, get_logger).


##### `_bounded_error_class`  (lines 540–553)

```
def _bounded_error_class(dimensions: dict[str, str]) -> dict[str, str]
```

**Purpose**: Keeps the error_class metric label within a known list. This prevents one unusual exception class from creating a new permanent metric series.

**Data flow**: It receives a dictionary of metric dimensions. If error_class is missing or already one of the approved values, it returns the dimensions unchanged. If error_class is present but unknown, it returns a copy with error_class changed to other.

**Call relations**: emit_metric and emit_histogram call this just before recording measurements. It is the safety gate that keeps error labels predictable across counter and histogram metrics.

*Call graph*: called by 2 (emit_histogram, emit_metric).


##### `emit_metric`  (lines 556–566)

```
def emit_metric(name: str, amount: int=1, /, **dimensions: str) -> None
```

**Purpose**: Increments one registered counter metric. A counter is a number that only goes up, such as how many turns started or how many retries happened.

**Data flow**: It receives a metric name, an amount that defaults to 1, and string dimensions. It rejects unknown metric names, creates and caches the OpenTelemetry counter on first use, bounds the error_class dimension, and adds the amount with those attributes.

**Call relations**: Runtime code calls this when an event should be counted. It uses _bounded_error_class before sending the increment to the OpenTelemetry meter.

*Call graph*: calls 1 internal fn (_bounded_error_class); 1 external calls (get_meter).


##### `emit_histogram`  (lines 569–591)

```
def emit_histogram(name: str, value: int, /, **dimensions: str) -> None
```

**Purpose**: Records one timing or size-like observation for a registered histogram, usually in milliseconds. Histograms let operators ask questions like p50 or p95 latency.

**Data flow**: It receives a histogram name, a numeric value, and string dimensions. It rejects unknown histogram names and dimensions that were not declared for that histogram, creates and caches the OpenTelemetry histogram on first use, bounds the error_class dimension, and records the value.

**Call relations**: Runtime code calls this around measured operations such as model rounds or tool calls. It uses _bounded_error_class and then hands the observation to the OpenTelemetry meter.

*Call graph*: calls 1 internal fn (_bounded_error_class); 1 external calls (get_meter).


##### `emit_up_down_metric`  (lines 594–606)

```
def emit_up_down_metric(name: str, amount: int, /, **dimensions: str) -> None
```

**Purpose**: Changes a registered current-state metric by a positive or negative amount. This is used for gauges-like counts, such as active work in progress.

**Data flow**: It receives a metric name, a signed amount, and string dimensions. It rejects unknown metric names and undeclared dimensions, creates and caches an OpenTelemetry up-down counter on first use, and adds the signed amount with the given attributes.

**Call relations**: Runtime code calls this when something starts or stops and the current count should move up or down. Unlike counters and histograms, it does not call _bounded_error_class because the registered up-down metrics do not declare error_class.

*Call graph*: 1 external calls (get_meter).


### Demo conversation seeding
Demo data generation creates and refreshes a comprehensive sample conversation that exercises the portal display surface.

### `core/src/ufo/seed.py`

`domain_logic` · `on-demand seeding / demo setup`

This file is a purpose-built seed tool for the web chat surface. Its job is to write a believable finished conversation directly into the system’s durable storage: the database for conversations and turns, and the blob store for transcripts and attached files. This matters because outside extensions are not allowed to forge completed conversation history. That history is audit-like data, so only engine-side code should create it.

The main type, `KitchenSink`, acts like a demo scene builder. When asked to write, it first finds older kitchen-sink runs. It only deletes runs that are clearly marked as seed-created and have not been touched by a real member or disclosed through transcript access. Then it opens a new web conversation, adds three finished turns, stores terminal summaries such as model cost and a still-open user question, creates nested subagent conversations, attaches two example files, and writes the transcript blob that the UI reads.

A key safety idea is marking. Seed-created conversations use a special queue-key prefix, and seed-created turns use a matching idempotency-key prefix. The code treats anything outside those marks as real workspace history and leaves it alone. In plain terms, it cleans up its own props after the demo, but it will not throw away someone else’s belongings.

#### Function details

##### `_framed`  (lines 74–75)

```
def _framed(turn_id: UUID, said: str) -> Message
```

**Purpose**: Builds a user message that includes a hidden reference to the turn it belongs to. This lets the transcript text stay connected to the database turn that committed it.

**Data flow**: It receives a turn ID and the words the user supposedly said. It wraps the turn ID in a small context block, appends the visible message text, and returns a `Message` object marked as coming from the user.

**Call relations**: The transcript builder `KitchenSink._said` calls this whenever it needs a user message tied to one of the seeded turns. The returned message is later encoded into the stored transcript.

*Call graph*: called by 1 (_said); 1 external calls (__init__).


##### `KitchenSink.write`  (lines 106–116)

```
async def write(self) -> UUID
```

**Purpose**: Creates one fresh kitchen-sink demo conversation from start to finish. This is the top-level action someone uses when they want to seed the workspace with the full demo.

**Data flow**: It starts with the `KitchenSink` object’s stored workspace, agent, member, email, and blob store. It generates new IDs, clears previous safe-to-delete demo data, writes the main conversation and turns, creates subagent runs, attaches files, stores the transcript in the blob store, and returns the new conversation ID.

**Call relations**: This method is the conductor for the whole file. It calls `_clear` before creating anything, then delegates to `_open`, `_runs`, `_files`, and `_said`, and finally uses transcript encoding and a transcript key so the finished conversation can be read by the rest of the system.

*Call graph*: calls 5 internal fn (_clear, _files, _open, _runs, _said); 4 external calls (__init__, encode, transcript_key, uuid4).


##### `KitchenSink._clear`  (lines 118–124)

```
async def _clear(self) -> None
```

**Purpose**: Removes older kitchen-sink demo runs that this seeding tool created and that are still safe to delete. This keeps repeated demo runs from piling up duplicate conversations and files.

**Data flow**: It asks `_prior` for earlier seed-created runs. For each one, it asks `_drop` to remove database rows, deletes the web chat row from the extension store, and deletes transcript and artifact blobs from the blob store.

**Call relations**: `KitchenSink.write` calls this first, before making the new demo conversation. It relies on `_prior` to decide what belongs to this seed tool and on `_drop` to remove the database part of each old run.

*Call graph*: calls 2 internal fn (_drop, _prior); called by 1 (write); 2 external calls (__init__, transcript_key).


##### `KitchenSink._prior`  (lines 126–204)

```
async def _prior(self) -> tuple[_PriorRun, ...]
```

**Purpose**: Finds older kitchen-sink runs that are safe for the seed tool to destroy. Its main job is to protect real workspace history from accidental deletion.

**Data flow**: It reads the database inside a workspace transaction, looking for web conversations in this workspace whose queue key has the kitchen-sink prefix. For each root conversation, it follows child subagent conversations through their turn links. It then checks whether any turn looks user-created or whether any transcript access row exists. Only runs with no such signs are returned as `_PriorRun` records.

**Call relations**: `_clear` calls this before deleting anything. The returned `_PriorRun` objects tell `_clear` and `_drop` exactly which conversations and turns belong to an old seed run.

*Call graph*: called by 1 (_clear); 5 external calls (__init__, not_, or_, select, workspace_tx).


##### `KitchenSink._drop`  (lines 206–235)

```
async def _drop(self, run: _PriorRun) -> tuple[str, ...]
```

**Purpose**: Deletes the database rows for one old seed run and reports which attached blobs also need deleting. It handles the database cleanup part, but leaves blob deletion to its caller.

**Data flow**: It receives a `_PriorRun` containing conversation IDs and turn IDs. It looks up shared artifact blob keys, deletes shared artifact rows, conversation change rows, turn rows, and conversation rows, then returns the artifact blob keys it found.

**Call relations**: `_clear` calls this after `_prior` has identified a safe old run. `_drop` removes database records, and `_clear` uses the returned blob keys to remove the corresponding files from the blob store.

*Call graph*: called by 1 (_clear); 3 external calls (delete, select, workspace_tx).


##### `KitchenSink._open`  (lines 237–275)

```
async def _open(self, conversation_id: UUID, turns: tuple[UUID, ...]) -> None
```

**Purpose**: Creates the main web conversation and its three completed turns. It also gives the conversation its visible title and adds the web extension’s chat row so the portal can show it.

**Data flow**: It receives the new conversation ID and the three turn IDs. It inserts a conversation row, inserts one finished turn for each terminal frame from `_terminals`, retitles the conversation to “Kitchen sink,” and writes a small chat record containing the agent ID and member email into the extension store.

**Call relations**: `KitchenSink.write` calls this after cleanup. `_open` calls `_terminals` to get the finished-turn summaries, writes the core database rows, then hands off to `retitle_conversation` and `ScopedStore` so the web surface can discover and display the seeded chat.

*Call graph*: calls 1 internal fn (_terminals); called by 1 (write); 5 external calls (__init__, insert, workspace_tx, retitle_conversation, uuid4).


##### `KitchenSink._terminals`  (lines 277–333)

```
def _terminals(self) -> tuple[TerminalFrame, ...]
```

**Purpose**: Builds the terminal summaries for the three seeded turns. A terminal frame is the final state of a turn: done status, model name, token count, cost, and optionally a question waiting for the user.

**Data flow**: It takes no outside input beyond the `KitchenSink` instance. It returns three `TerminalFrame` objects: two plain completed turns and one completed turn that includes a structured user-input request with choices and a free-text prompt.

**Call relations**: `_open` calls this while inserting the main turns. The returned frames become the saved terminal data that the portal can render as costs, model information, completion status, and an outstanding question.

*Call graph*: called by 1 (_open); 4 external calls (__init__, __init__, __init__, __init__).


##### `KitchenSink._runs`  (lines 335–368)

```
async def _runs(self, conversation_id: UUID, parent: UUID) -> None
```

**Purpose**: Creates nested subagent demo activity and writes a transcript for one spawned subagent conversation. This gives the UI example data for showing an agent that launched another agent.

**Data flow**: It receives the main conversation ID and the parent turn ID that should appear to have spawned subagents. It creates one child subagent run and one grandchild run through `_run`, then stores a short transcript for the child run in the blob store.

**Call relations**: `KitchenSink.write` calls this after opening the main conversation. It uses `_run` to insert the subagent conversation and turn rows, then uses transcript encoding and blob storage so the subagent surface has conversation text to display.

*Call graph*: calls 1 internal fn (_run); called by 1 (write); 8 external calls (__init__, __init__, __init__, __init__, __init__, encode, transcript_key, uuid4).


##### `KitchenSink._run`  (lines 370–405)

```
async def _run(self, turn_id: UUID, parent: UUID, profile: str, answered: str) -> UUID
```

**Purpose**: Creates one completed subagent conversation with one completed turn. It is the small building block used to make the demo’s child and grandchild agent runs.

**Data flow**: It receives a turn ID, a parent turn ID, a subagent profile name, and the result text the subagent should report. It generates a new conversation ID, inserts a subagent conversation linked through the parent queue key, inserts one completed turn with a terminal result, and returns the new subagent conversation ID.

**Call relations**: `_runs` calls this once for the child subagent and once for the grandchild. The returned conversation ID lets `_runs` write a transcript for the spawned subagent when needed.

*Call graph*: called by 1 (_runs); 4 external calls (__init__, insert, workspace_tx, uuid4).


##### `KitchenSink._files`  (lines 407–428)

```
async def _files(self, turn_id: UUID) -> None
```

**Purpose**: Adds two example shared files to the demo conversation: a Markdown audit and a CSV metrics file. This gives the portal real attachment data to render.

**Data flow**: It receives the turn ID that should own the attachments. For each built-in file body, it encodes the text, writes the bytes to the blob store under a fresh artifact key, and inserts a shared-artifact database row with filename, media type, size, and workspace information.

**Call relations**: `KitchenSink.write` calls this after creating the main conversation and subagent activity. The blob writes store the file contents, while the database rows make those files visible as artifacts attached to the selected turn.

*Call graph*: called by 1 (write); 3 external calls (insert, workspace_tx, uuid4).


##### `KitchenSink._said`  (lines 430–484)

```
def _said(self, turns: tuple[UUID, ...]) -> tuple[Message, ...]
```

**Purpose**: Builds the main transcript messages for the seeded web conversation. The transcript is what a reader sees as the back-and-forth chat log.

**Data flow**: It receives the three turn IDs. It creates a sequence of user, assistant, tool-use, and tool-result messages, using `_framed` for user messages that need a turn reference, and returns the complete message tuple.

**Call relations**: `KitchenSink.write` calls this when it is ready to store the transcript blob for the main conversation. `_said` supplies the human-readable chat content, and `write` wraps it in a `Conversation` and encodes it for storage.

*Call graph*: calls 1 internal fn (_framed); called by 1 (write); 3 external calls (__init__, __init__, __init__).

## 📊 State Registers Touched

- `reg-effective-config` — The merged deployment settings that tell the process how to run, which services to use, and which safety options are enabled.
- `reg-database-store` — The shared database connection and tables where workspaces, users, agents, turns, files, jobs, costs, and extension data are saved.
- `reg-turn-run-state` — The shared state of each unit of agent work, including queued, claimed, running, parked, canceled, recovered, or finished.
- `reg-live-stream-state` — The live update stream that broadcasts turn progress, tool activity, subagent activity, cancellations, and final replies to connected clients.
- `reg-model-usage-accounting` — The recorded token, image, video, embedding, sandbox, egress, and cost usage used for billing and audit trails.
- `reg-file-blob-store` — The shared byte storage for uploads, generated files, previews, media, and other raw data, separated by workspace or deployment scope.
- `reg-artifact-registry` — The saved list of files deliberately shared with users, including ownership, access checks, preview metadata, and download links.
- `reg-source-sync-catalog` — The saved catalog of external sources, pages, sync cursors, deletion marks, retry backoff, and indexing needs.
- `reg-search-index` — The shared keyword and embedding indexes that let conversations, tools, and background jobs find relevant stored documents.
- `reg-scheduled-jobs` — The durable background job and scheduled task state used for recurring work, wakeups, retries, monitors, billing, and offline evaluation.
- `reg-subagent-delegation` — The shared state for spawned helper agents, including their catalog entries, parent-child turn links, required results, names, and cancellation state.
- `reg-workspace-change-log` — The saved record of file changes made during a conversation, used to explain later what the agent changed in the workspace.
- `reg-observability-traces` — The shared trace, metric, log, and traceparent information that lets operators connect startup, turns, tools, subagents, and billing events.
- `reg-provider-runtime-controls` — The in-process model-provider client pools, retry/backoff state, rate-limit buckets, and request-budget guards shared by model calls and background work.
- `reg-prompt-render-audit` — Rendered-prompt fingerprints, template provenance, and compaction/prompt hashes used to trace or reproduce the exact context sent to models.
