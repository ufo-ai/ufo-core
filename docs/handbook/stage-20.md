# Cross-cutting configuration, provider adapters, and reusable utilities  `stage-20` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support. It is not one step in a single request path. Instead, many parts of the system consult it when they need common services, like talking to AI model providers, reading external data, or storing large files.

The model adapter files act like plug converters. `anthropic.py` translates UFO’s internal message format into Anthropic’s Messages API and streams back text, tool calls, and token counts. `openai.py` does the same for OpenAI-compatible servers, including both chat and newer response formats. `ufo_ext_openrouter.py` connects the same style of request to OpenRouter, which can route it to many models.

`blob.py` gives the system one simple way to read and write large byte objects, whether they are on disk or in S3-style cloud storage. `rest.py` gives data connectors a reusable HTTP client, retries, and pagination helpers for REST APIs, meaning web APIs that expose data through standard URLs.

`subjects.py` keeps shared labels consistent. The many `__init__.py` files are package signposts: they make folders importable and show where major areas like tools, models, prompts, schema, sandbox, and extensions live.

## Files in this stage

### Package roots and blob storage
Root package markers establish importable namespaces before the shared blob abstraction provides storage-independent byte-object access.

### `control/src/ufo_control/__init__.py`

`other` · `import time`

This is an empty Python package marker. In Python projects, a folder often needs an `__init__.py` file so Python treats that folder as a package: a named collection of modules that can be imported elsewhere. Think of it like a label on a drawer. The drawer may contain useful tools, but this label simply tells Python, “this drawer belongs together and can be opened by name.”

Because the file is empty, it does not run setup code, expose shortcut imports, or define shared constants. That is important in its own way: importing `ufo_control` has no side effects. Nothing starts, connects, reads configuration, or changes program state just because this package is imported.

Without this file, depending on the Python version and packaging setup, imports from `ufo_control` might fail or behave differently. With it present, the package structure is explicit and predictable.


### `core/src/ufo/__init__.py`

`other` · `import time`

In Python, an `__init__.py` file is like a label on a folder saying, “this folder is a package you can import from.” This particular file is empty, which means it does not run setup code, define shortcuts, or expose names for easier importing. Its main value is structural: it helps Python and developer tools recognize `core/src/ufo` as a package root. Without it, some import styles or packaging tools might not treat the folder the way the project expects, especially in environments that still rely on traditional package markers. Think of it like a blank cover page for a section in a binder: it does not contain instructions, but it tells readers and tools that the following pages belong together.


### `core/src/ufo/blob.py`

`io_transport` · `cross-cutting storage access during request handling, background work, and artifact transfer`

The system needs a place for files, attachments, artifacts, and saved records that may be too large to keep in memory or inside a database row. This file is the storage doorway for those bytes. Think of it like a mailroom counter: callers ask to put, fetch, delete, or list packages by a label, and the clerk decides whether the package goes into a local cabinet or a cloud warehouse.

The shared shape is `BlobStore`, an asynchronous protocol. “Asynchronous” means the program can wait for slow disk or network work without freezing other tasks. It supports whole-object reads and writes for small data, plus streaming reads and writes for large data so the whole file does not have to sit in memory at once.

`FilesystemBlobStore` is the local backend. It turns blob keys into files under a configured root folder. It is careful about safety: keys are checked so they cannot escape that root folder, and writes go through a temporary file before replacing the final file. That avoids leaving a half-written file behind as the real object.

`S3BlobStore` is the cloud backend. It talks to S3 or an S3-compatible service, uses multipart uploads for large files, and can create presigned upload URLs so a sandboxed worker can upload directly without receiving broad storage credentials. `blob_store_for` chooses the right backend from configuration.

#### Function details

##### `BlobStore.put`  (lines 51–51)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: This is the common promise that any blob store can save a complete byte string under a key. Code uses this when the data is already small enough to hold in memory.

**Data flow**: A caller provides a text key and bytes. A concrete backend saves those bytes under that key. Nothing is returned; the lasting result is that later reads can find the object.

**Call relations**: This method is part of the shared storage contract. The filesystem and S3 implementations provide the real behavior so callers do not need to know where the bytes are stored.


##### `BlobStore.put_file`  (lines 53–56)

```
async def put_file(self, key: str, source: Path) -> None
```

**Purpose**: This is the common promise that any blob store can save the contents of a local file without first loading the whole file into memory. It is meant for larger attachments or artifacts.

**Data flow**: A caller provides a key and a path to a file on disk. The backend reads the file and stores its contents under the key. The caller gets no value back, only the saved blob.

**Call relations**: This belongs to the shared storage interface. Each backend decides the best way to move the file: local copying for the filesystem backend, multipart upload for S3.


##### `BlobStore.get`  (lines 58–58)

```
async def get(self, key: str) -> bytes
```

**Purpose**: This is the common promise that any blob store can return all bytes for a stored key. It is useful for small or moderate-sized blobs that callers want as one byte string.

**Data flow**: A caller gives a key. The backend looks up the stored object and returns its bytes, or raises a not-found error if the key does not exist.

**Call relations**: Higher-level code uses this through the interface when reading stored records or identities, for example transcript compaction records and Slack identity data. The actual read is supplied by whichever backend was configured.

*Call graph*: called by 2 (read_compaction_record, read_identity).


##### `BlobStore.exists`  (lines 60–60)

```
async def exists(self, key: str) -> bool
```

**Purpose**: This is the common promise that any blob store can answer whether a key is present. It lets callers check before deciding whether to read or create stored data.

**Data flow**: A caller gives a key. The backend checks storage and returns `true` if an object exists there, or `false` if it does not.

**Call relations**: Callers such as the Slack surface code use this through the interface to decide whether saved identity information is available. Concrete backends turn that question into a file check or an S3 metadata check.

*Call graph*: called by 1 (read_identity).


##### `BlobStore.delete`  (lines 62–64)

```
async def delete(self, key: str) -> None
```

**Purpose**: This is the common promise that any blob store can remove a key. It is intentionally safe to call even if the object is already gone.

**Data flow**: A caller gives a key. The backend tries to remove the stored object. Nothing is returned, and a missing object is treated as already deleted.

**Call relations**: This is part of the shared contract, so cleanup code can delete blobs without caring whether they live on disk or in S3.


##### `BlobStore.get_stream`  (lines 66–66)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: This is the common promise that any blob store can read a blob piece by piece. It is for large files where loading everything at once would waste memory or risk running out of it.

**Data flow**: A caller gives a key. The backend opens the object and yields chunks of bytes over time. The stream ends when all bytes have been read, or raises a not-found error if the key is absent.

**Call relations**: This method gives higher-level code a backend-neutral way to move large blobs. The filesystem backend yields file chunks; the S3 backend yields chunks from the network response.


##### `BlobStore.put_stream`  (lines 68–68)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: This is the common promise that any blob store can write a blob from a stream of byte chunks. It lets the system accept large data without building one giant byte string.

**Data flow**: A caller provides a key and an asynchronous stream of byte chunks. The backend consumes the chunks in order and stores them as one object. Nothing is returned after the write completes.

**Call relations**: This is the shared large-write path. The filesystem backend writes chunks to a temporary file, while the S3 backend may switch to multipart upload when enough data has accumulated.


##### `BlobStore.list`  (lines 70–74)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: This is the common promise that any blob store can list objects below a key prefix. It is used when code needs to discover related stored objects without scanning the entire store.

**Data flow**: A caller provides a non-empty prefix. The backend finds matching objects and returns entries containing each key, size, and modification time, capped at a fixed maximum count.

**Call relations**: This interface lets read views or record walkers enumerate a bounded group of blobs. The filesystem and S3 versions implement the same result shape in different ways.


##### `FilesystemBlobStore.put`  (lines 83–88)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: This saves an in-memory byte string as a file under the store root. It writes through a temporary file first so readers do not see a half-written result.

**Data flow**: The key is turned into a safe path inside the root folder. Parent folders are created, bytes are written to a uniquely named temporary file, and that temporary file is then moved into the final location. The output is the completed file on disk.

**Call relations**: It relies on `_resolve` to prevent unsafe paths. It uses background thread calls for blocking filesystem work so the async event loop can keep serving other tasks.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.put_file`  (lines 90–95)

```
async def put_file(self, key: str, source: Path) -> None
```

**Purpose**: This stores an existing local file under a blob key. It copies the file without reading the whole thing into Python memory.

**Data flow**: The key becomes a safe destination path. The source file is copied to a temporary file beside the destination, then the temporary file replaces the final path. The stored blob becomes an exact copy of the source file.

**Call relations**: Like `put`, it uses `_resolve` for safety and a temporary file for an all-or-nothing write. It delegates slow file copying to a thread so other async work is not blocked.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.get`  (lines 97–102)

```
async def get(self, key: str) -> bytes
```

**Purpose**: This reads a stored filesystem blob into memory and returns its bytes. It translates a missing file into the blob-specific not-found error.

**Data flow**: The key is resolved to a safe path. The file bytes are read from disk and returned. If the file is absent, the function raises `BlobNotFound` instead of exposing a raw filesystem error.

**Call relations**: It uses `_resolve` before touching the disk. Callers using the common blob interface get the same missing-object behavior they would get from S3.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.exists`  (lines 104–106)

```
async def exists(self, key: str) -> bool
```

**Purpose**: This checks whether a blob key points to a real file in the local store. It lets callers ask a cheap yes-or-no question before reading.

**Data flow**: The key is resolved to a safe path. The filesystem is asked whether that path is a file. The function returns `true` or `false`.

**Call relations**: It depends on `_resolve` for root containment, then runs the file check in a thread so it does not block the event loop.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.delete`  (lines 108–110)

```
async def delete(self, key: str) -> None
```

**Purpose**: This removes a local blob file if it exists. It treats an already-missing file as a successful delete.

**Data flow**: The key is resolved to a safe path. The file at that path is unlinked, meaning removed from the filesystem, if present. No value is returned.

**Call relations**: It uses `_resolve` to make sure deletion cannot target a file outside the blob root. The actual filesystem operation is sent to a thread.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.get_stream`  (lines 112–125)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: This reads a local blob in fixed-size chunks. It is the memory-friendly read path for large files.

**Data flow**: The key is resolved to a safe path and opened for binary reading. The function repeatedly reads a chunk and yields it to the caller until no bytes remain. The file is closed afterward, even if reading is interrupted.

**Call relations**: It relies on `_resolve` for safety and raises `BlobNotFound` for absent files. Thread calls are used for opening, reading, and closing because normal file I/O can block.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.put_stream`  (lines 127–140)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: This writes a stream of byte chunks into a local blob file. It is designed for large incoming data and for safety if the write fails partway through.

**Data flow**: The key is resolved and parent folders are created. Chunks from the caller are written into a uniquely named temporary file. If all chunks arrive successfully, the temporary file replaces the final file; if anything goes wrong, the temporary file is removed.

**Call relations**: It combines `_resolve`, threaded file writes, and temporary-file replacement to match the blob interface’s promise of safe streaming writes.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.list`  (lines 142–145)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: This lists local blob files whose keys begin with a required prefix. Requiring a prefix keeps callers from accidentally walking the entire store.

**Data flow**: The caller gives a prefix. If it is empty, the function rejects it. Otherwise it runs the directory walk in a thread and returns the matching blob entries.

**Call relations**: It hands the actual scanning work to `_walk`. This keeps the async method small and prevents the event loop from being tied up by filesystem traversal.

*Call graph*: 1 external calls (to_thread).


##### `FilesystemBlobStore._walk`  (lines 147–168)

```
def _walk(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: This performs the actual filesystem scan for `list`. It turns matching files into `BlobEntry` records with key, size, and last-modified time.

**Data flow**: It starts from the part of the root folder relevant to the prefix. It walks files below that point, ignores temporary files and non-matching keys, gathers file metadata, sorts by key, and returns only up to the configured maximum count.

**Call relations**: `FilesystemBlobStore.list` calls this inside a worker thread. It uses `_resolve` to choose a safe starting directory and creates the entries returned through the public list method.

*Call graph*: calls 1 internal fn (_resolve); 4 external calls (__init__, fromtimestamp, walk, Path).


##### `FilesystemBlobStore._resolve`  (lines 170–175)

```
def _resolve(self, key: str) -> Path
```

**Purpose**: This converts a blob key into a filesystem path and makes sure it stays inside the store root. It is the main safety guard for the local backend.

**Data flow**: The root and key are combined and normalized into an absolute path. If the result is the root itself or escapes outside the root, the function raises an error. Otherwise it returns the safe path.

**Call relations**: Every filesystem operation calls this before touching disk, including reads, writes, deletes, streams, and listing. It prevents dangerous keys such as paths that try to climb out of the blob directory.

*Call graph*: called by 8 (_walk, delete, exists, get, get_stream, put, put_file, put_stream).


##### `_is_missing_key`  (lines 178–179)

```
def _is_missing_key(error: ClientError) -> bool
```

**Purpose**: This recognizes S3 error responses that mean “that object does not exist.” It lets the S3 backend turn several cloud-specific error codes into one simple missing-blob meaning.

**Data flow**: It receives a `ClientError` from the S3 library. It looks inside the response for the error code and checks whether it is one of the known missing-key codes. It returns `true` for missing-object errors and `false` otherwise.

**Call relations**: S3 reads, existence checks, and streaming reads call this after a failed S3 request. When it says the key is missing, those methods return `false` or raise `BlobNotFound`; other errors are allowed to bubble up.

*Call graph*: called by 3 (exists, get, get_stream).


##### `S3BlobStore.put`  (lines 198–200)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: This saves an in-memory byte string as one S3 object. It is the cloud version of a simple whole-object write.

**Data flow**: The caller gives a key and bytes. The function obtains an S3 client and sends a put-object request with the bucket, key, and body. Nothing is returned after S3 accepts the write.

**Call relations**: It uses `_client` to reuse the right S3 client for the current async event loop. Callers use it through the same blob interface as the filesystem `put`.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.put_file`  (lines 202–234)

```
async def put_file(self, key: str, source: Path) -> None
```

**Purpose**: This uploads a local file to S3, using multipart upload for non-empty files. Multipart upload means the file is sent in numbered pieces, which is safer and more practical for large files.

**Data flow**: The file size is read first. An empty file is uploaded as a simple object. Otherwise, the function starts a multipart upload, reads fixed-size slices from the source file, uploads each slice as a part, then tells S3 to assemble the parts into the final object. If an error happens, the unfinished upload is aborted.

**Call relations**: It gets its S3 connection from `_client` and uses thread calls for local file operations. This is the S3 counterpart to `FilesystemBlobStore.put_file`, optimized for cloud storage behavior.

*Call graph*: calls 1 internal fn (_client); 1 external calls (to_thread).


##### `S3BlobStore.get`  (lines 236–246)

```
async def get(self, key: str) -> bytes
```

**Purpose**: This reads an S3 object into memory and returns its bytes. It presents S3’s many possible missing-object responses as the project’s single `BlobNotFound` error.

**Data flow**: The caller gives a key. The function asks S3 for that object, reads the response body, and returns the bytes. If S3 says the key is missing, it raises `BlobNotFound`; other S3 errors are not hidden.

**Call relations**: It uses `_client` for the connection and `_is_missing_key` to interpret S3 errors. Higher-level code can call the blob interface without knowing S3’s error vocabulary.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.exists`  (lines 248–256)

```
async def exists(self, key: str) -> bool
```

**Purpose**: This checks whether an S3 object exists without downloading its contents. It is the cloud equivalent of asking whether a local file is present.

**Data flow**: The caller gives a key. The function sends a metadata-only request to S3. It returns `true` if S3 finds the object, `false` if S3 reports a missing key, and lets unexpected errors surface.

**Call relations**: It uses `_client` to reach S3 and `_is_missing_key` to separate a normal absent object from a real failure.

*Call graph*: calls 2 internal fn (_client, _is_missing_key).


##### `S3BlobStore.delete`  (lines 258–260)

```
async def delete(self, key: str) -> None
```

**Purpose**: This asks S3 to delete an object by key. S3 deletion is safe to retry, so callers do not need special handling for already-deleted objects.

**Data flow**: The key is sent to S3 with the configured bucket. S3 removes the object if it is present. The function returns nothing.

**Call relations**: It obtains the S3 client through `_client`. It fulfills the shared blob deletion contract for cloud-backed storage.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get_stream`  (lines 262–273)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: This reads an S3 object in chunks instead of loading it all at once. It is the cloud streaming read path for large blobs.

**Data flow**: The caller gives a key. The function opens the S3 response body and yields chunks of bytes until the object is fully read. If S3 says the key is missing, it raises `BlobNotFound`.

**Call relations**: It uses `_client` for the request and `_is_missing_key` for missing-object handling. This mirrors `FilesystemBlobStore.get_stream`, but the bytes arrive over the network.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.put_stream`  (lines 275–319)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: This writes an incoming stream of byte chunks to S3. Small streams are sent as one object, while larger streams are uploaded as multipart data.

**Data flow**: The function gathers incoming chunks into a buffer. If the total data stays below the multipart size, it sends one simple S3 put. Once enough data accumulates, it starts a multipart upload, sends each buffered part with a part number, uploads the final remainder, and completes the object. If anything fails after multipart upload starts, it aborts the unfinished upload.

**Call relations**: It uses `_client` for all S3 calls. It is the S3 implementation of the shared streaming write method and protects S3 from abandoned partial uploads when errors occur.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.presigned_put`  (lines 321–345)

```
async def presigned_put(self, key: str, size_bytes: int, checksum_sha256: str, ttl_seconds: int) -> str
```

**Purpose**: This creates a temporary URL that lets another process upload exactly one expected object directly to S3. It is useful for an untrusted sandbox because the sandbox gets permission for one measured upload, not broad storage access.

**Data flow**: The caller gives the key, expected byte size, expected SHA-256 checksum, and time-to-live. The function asks S3 to generate a signed URL whose signature includes the size and checksum requirements. It returns that URL as a string.

**Call relations**: It uses `_client` because the same S3 client configuration must be used for signing. Later, the sandbox can upload to the returned URL, and S3 itself rejects a body with the wrong size or checksum.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.put_host`  (lines 347–358)

```
async def put_host(self) -> str
```

**Purpose**: This reports the hostname that a presigned upload URL will contact. The system can use it to allow a sandbox’s network connection only to the exact S3 host it needs.

**Data flow**: The function gets the configured S3 client, reads the client endpoint URL, extracts the hostname, and returns the hostname that should be allowed. For AWS-style virtual-hosted addressing, it includes the bucket name at the front.

**Call relations**: It depends on `_client` so the hostname matches the same configuration used to create presigned URLs. It uses URL parsing to avoid hand-building a host that might disagree with the client.

*Call graph*: calls 1 internal fn (_client); 1 external calls (urlsplit).


##### `S3BlobStore.list`  (lines 360–377)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: This lists S3 objects whose keys begin with a required prefix. It returns the same simple entry records as the filesystem backend.

**Data flow**: The caller provides a non-empty prefix. The function pages through S3 list results, turns each object into a `BlobEntry` with key, size, and UTC modification time, stops once the configured maximum is reached, and returns the entries.

**Call relations**: It uses `_client` to get the S3 paginator, which is the S3 library’s way to read long result sets page by page. This implements the shared `list` behavior for cloud storage.

*Call graph*: calls 1 internal fn (_client); 1 external calls (__init__).


##### `S3BlobStore._client`  (lines 379–405)

```
async def _client(self) -> AioBaseClient
```

**Purpose**: This creates and reuses an S3 client for the current asynchronous event loop. Reusing clients avoids repeated setup cost and avoids using a network client on the wrong loop.

**Data flow**: The function checks the currently running event loop and looks for an existing client tied to it. If found, it returns that client. If not, it creates a new S3 client with fixed signing and addressing settings, stores it in the loop-specific cache, and returns it; if a race creates an extra client, it closes the unused one and logs if that close fails.

**Call relations**: Every S3 operation calls this before talking to S3. It is the quiet plumbing that keeps S3 access efficient and consistent for uploads, downloads, deletes, listings, presigned URLs, and host discovery.

*Call graph*: called by 10 (delete, exists, get, get_stream, list, presigned_put, put, put_file, put_host, put_stream); 3 external calls (get_session, get_running_loop, log).


##### `blob_store_for`  (lines 408–420)

```
def blob_store_for(config: BlobConfig) -> FilesystemBlobStore | S3BlobStore
```

**Purpose**: This builds the correct blob store from configuration. It is the small factory that turns a setting such as “filesystem” or “s3” into the object the rest of the system will use.

**Data flow**: The function receives a `BlobConfig`. If the backend is filesystem, it requires a root path and returns a `FilesystemBlobStore`. If the backend is S3, it requires a bucket and returns an `S3BlobStore` with the configured endpoint and region.

**Call relations**: Startup or setup code can call this once after loading configuration, then pass around the returned store through the shared `BlobStore` interface. That keeps the rest of the project independent from the chosen storage backend.

*Call graph*: 2 external calls (__init__, __init__).


### Extension and prompt namespaces
These package markers make extension, loop, and prompt modules importable for later cross-cutting components.

### `core/src/ufo/ext/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python projects, an `__init__.py` file tells Python that a folder should be treated as an importable package. Here, it makes the `core/src/ufo/ext` directory available as `ufo.ext`.

That matters because extension code often lives in separate modules under this folder, and other parts of the project may need to import them using normal Python import paths. Think of this file like a label on a drawer: the drawer may contain many useful tools, but the label is what lets the rest of the workshop find it by name.

Because the file is empty, it does not create objects, load settings, run startup code, or change behavior directly. Its value is structural: without it, depending on the Python version and packaging setup, imports involving `ufo.ext` could fail or behave differently.


### `core/src/ufo/loop/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, it makes the `ufo.loop` folder available to the rest of the project.

There is no code inside this file, so it does not start anything, configure anything, or change data. Its value is structural: it acts like a label on a drawer, letting other parts of the program say “look in `ufo.loop`” when they need loop-related code stored in nearby files.

Without this file, depending on the Python version and packaging setup, imports from `ufo.loop` might fail or behave differently. Keeping it present makes the package layout explicit and predictable.


### `core/src/ufo/loop/prompts/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the language, “treat this folder as an importable package.” Here, it makes `core/src/ufo/loop/prompts` available as a named place where prompt-related modules can live.

Think of it like a label on a drawer. The drawer may contain useful documents, but the label itself does not do the work. Without this file, depending on the Python version and packaging setup, code elsewhere in the project might not reliably import modules from the `prompts` folder using normal package paths.

Because the file is empty, importing this package has no side effects. It does not load settings, create objects, register prompts, or run setup code. Its value is structural: it helps organize the project and gives the prompt-related code a stable home in the package tree.


### Core model provider adapters
The model package signpost leads into adapters that translate UFO model requests and streaming responses for Anthropic and OpenAI-compatible APIs.

### `core/src/ufo/models/__init__.py`

`data_model` · `import time`

This is an empty package file. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Here, it makes the `core/src/ufo/models` folder available as `ufo.models`.

Think of it like a label on a filing cabinet drawer: the label does not contain the documents, but it lets people refer to the drawer by name. Other files can then import model definitions from this area of the project in a clean and predictable way.

Because the file is empty, it does not create classes, run setup code, or re-export symbols from other files. Its value is structural: without it, some Python environments or tooling might not recognize this directory as a package, which could make imports fail or behave inconsistently.


### `core/src/ufo/models/anthropic.py`

`io_transport` · `request handling`

This file lets the rest of the project talk to Anthropic models without having to know Anthropic's exact message format or streaming rules. It is like a translator at a service desk: UFO hands it a request in UFO's own shape, and this file rewrites it into the shape Anthropic expects; then it translates Anthropic's streamed replies back into UFO's common event types.

The small helper functions convert pieces of content. Plain text stays plain. Images are wrapped with their media type and base64 data. Tool calls and tool results are converted into Anthropic's names for those concepts. This matters because different model providers describe the same ideas in different formats.

The main class, AnthropicClient, owns the actual streaming request. It builds the API call, includes system instructions, messages, tools, reasoning settings, and caching hints, then reads Anthropic's stream event by event. As text arrives, it yields text deltas. When a tool call begins or receives partial JSON input, it yields tool-call events. At the end it yields exactly one Usage record with token counts.

It also protects the rest of the system from common provider problems. Timeouts and retryable server errors are retried before any output has been sent. Deterministic client errors, refusals, and truncated responses are surfaced as clear exceptions. Empty responses are retried a few times before being accepted so the outer conversation loop can recover.

#### Function details

##### `anthropic_sdk_client`  (lines 41–45)

```
def anthropic_sdk_client(api_key: str) -> anthropic.AsyncAnthropic
```

**Purpose**: Creates the Anthropic software development kit client used to make API calls. It deliberately turns off the SDK's built-in retries, because this file has its own retry rules that are aware of streaming behavior.

**Data flow**: It receives an API key string → builds an Anthropic asynchronous client with that key, a fixed timeout, and zero SDK retries → returns the ready-to-use client object.

**Call relations**: This is the setup doorway for Anthropic access. Later, AnthropicClient.complete uses the returned client to send message requests, while keeping retry decisions inside this file instead of hidden inside the external SDK.

*Call graph*: 1 external calls (AsyncAnthropic).


##### `_anthropic_image`  (lines 48–52)

```
def _anthropic_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Turns UFO's internal image description into the image format Anthropic expects. This is needed whenever a user message or tool result includes an image.

**Data flow**: It receives an ImageSource containing a media type and base64 image data → wraps those fields in Anthropic's required dictionary structure → returns that dictionary for inclusion in an API message.

**Call relations**: This helper is called while converting larger message content. anthropic_content uses it for image blocks in normal messages, and _anthropic_tool_result_part uses it for image parts inside tool results.

*Call graph*: called by 2 (_anthropic_tool_result_part, anthropic_content).


##### `_anthropic_tool_result_part`  (lines 55–60)

```
def _anthropic_tool_result_part(part: ToolResultContent) -> dict[str, object]
```

**Purpose**: Converts one piece of a tool result into Anthropic's format. A tool result can contain text or an image, and this function translates either kind.

**Data flow**: It receives one tool-result content part → if it is text, it produces an Anthropic text dictionary; if it is an image, it passes the image source to _anthropic_image → returns the converted dictionary.

**Call relations**: This function is used by anthropic_content when a message contains a structured tool result. It breaks the nested tool-result content into Anthropic-ready parts before the full request is sent.

*Call graph*: calls 1 internal fn (_anthropic_image); called by 1 (anthropic_content).


##### `anthropic_content`  (lines 63–88)

```
def anthropic_content(content: str | tuple[ContentBlock, ...]) -> str | list[dict[str, object]]
```

**Purpose**: Converts UFO message content into Anthropic message content. It supports simple strings as well as richer blocks such as text, images, tool-use requests, and tool results.

**Data flow**: It receives either a plain string or a tuple of UFO content blocks → leaves plain strings unchanged, or walks through each block and converts it to Anthropic's dictionary format → returns either the original string or a list of converted content dictionaries.

**Call relations**: AnthropicClient.complete calls this while building the messages that will be sent to Anthropic. When it encounters images or nested tool-result parts, it delegates to _anthropic_image and _anthropic_tool_result_part so each small piece is translated consistently.

*Call graph*: calls 2 internal fn (_anthropic_image, _anthropic_tool_result_part); called by 1 (complete).


##### `AnthropicClient.complete`  (lines 96–248)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Sends one model request to Anthropic and streams the answer back as UFO model events. It also applies the project's retry policy, detects refusals and truncation, and reports final token usage.

**Data flow**: It receives a ModelRequest with the model name, system prompt, conversation messages, tools, reasoning preference, token limit, and tool-choice preference → trims images where needed, converts messages into Anthropic format, adds tool and reasoning options, and opens a streamed Anthropic API call → as stream events arrive, it yields text pieces, tool-call starts, and tool-call JSON fragments; after the stream ends, it yields a Usage record. If Anthropic times out or returns a retryable error before any output is yielded, it waits and tries again. If the model refuses, runs out of token budget, or the stream is malformed, it raises a clear exception instead of pretending the response succeeded.

**Call relations**: This is the main runtime path in the file. It calls anthropic_content to prepare outgoing messages, calls the external Anthropic client to create the stream, then turns Anthropic stream events into UFO event objects such as TextDelta, ToolCallStart, ToolCallDelta, and Usage. Its retry sleeps use asyncio.sleep so the program can wait without blocking other asynchronous work.

*Call graph*: calls 1 internal fn (anthropic_content); 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, sleep, trim_images, log).


### `core/src/ufo/models/openai.py`

`io_transport` · `model request handling`

UFO wants the rest of the system to talk to “a model” in one consistent way, even when different providers expect different request formats. This file is the adapter for providers that speak OpenAI’s wire format, including OpenAI itself and compatible services using a custom base URL. It is like a travel plug adapter: the appliance stays the same, but the plug shape changes for the wall socket.

The file first converts UFO’s message blocks into provider-specific payloads. Text stays text. Images are turned into base64 data URLs. Tool requests become function calls. Tool results are carefully reshaped because the Chat Completions API only accepts text in tool-result messages, while image results must be “lifted” into a following user message.

The main class, OpenAIClient, chooses which OpenAI API surface to use from the model’s specification, not by guessing from the model name. That matters because some models reject certain combinations, such as tools plus reasoning settings, on one API but accept them on another.

When a model reply streams back, this client converts provider stream chunks into UFO’s standard events: text pieces, tool-call starts, tool-call argument pieces, refusal errors, truncation errors, and final token usage. It also retries temporary provider failures before any output has been produced, but once a response has started, later failures are allowed to surface immediately so the caller does not receive a mixed or duplicated answer.

#### Function details

##### `openai_sdk_client`  (lines 71–77)

```
def openai_sdk_client(api_key: str, base_url: str | None=None) -> openai.AsyncOpenAI
```

**Purpose**: Creates the low-level OpenAI software client used to contact OpenAI or an OpenAI-compatible service. It deliberately turns off the SDK’s own retries so this file’s retry rules are the only ones in charge.

**Data flow**: It receives an API key and, optionally, a custom base URL. It builds an asynchronous OpenAI client with a fixed timeout, no built-in retries, and the chosen endpoint. The result is a ready-to-use network client for later model calls.

**Call relations**: This is used when setting up an OpenAI-backed model connection. After it creates the SDK client, an OpenAIClient instance can use that client to send chat or responses requests.

*Call graph*: 1 external calls (AsyncOpenAI).


##### `_openai_image`  (lines 80–84)

```
def _openai_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Turns UFO’s internal image source into the image format expected by the Chat Completions API. It packages the image as a data URL, which is a text string containing the image type and base64 image data.

**Data flow**: It takes an ImageSource containing a media type, such as image/png, and base64 data. It combines those into an OpenAI-style image_url dictionary. The returned dictionary can be placed inside a message sent to the provider.

**Call relations**: This helper is called while converting normal image message blocks and image-containing tool results. It keeps the image formatting rule in one small place so openai_messages and _openai_tool_result do not each have to rebuild it by hand.

*Call graph*: called by 2 (_openai_tool_result, openai_messages).


##### `_openai_tool_result`  (lines 87–103)

```
def _openai_tool_result(result: str | tuple[ToolResultContent, ...]) -> tuple[str, list[dict[str, object]]]
```

**Purpose**: Splits a tool result into the parts the Chat Completions API can accept directly and the parts that need special treatment. This is needed because Chat Completions tool messages are text-only, while image results must be sent separately.

**Data flow**: It receives either a plain string result or a tuple of text and image blocks. Plain strings come out as text with no images. Mixed results are separated into joined text and a list of OpenAI-formatted image parts. Nothing is sent over the network here; it only reshapes the data.

**Call relations**: openai_messages calls this when it sees a ToolResultBlock. If images are found, _openai_tool_result relies on _openai_image to format them, and openai_messages later places those images into a trailing user message.

*Call graph*: calls 1 internal fn (_openai_image); called by 1 (openai_messages).


##### `openai_messages`  (lines 106–159)

```
def openai_messages(system: str, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: Converts UFO’s standard conversation history into the message list required by OpenAI’s Chat Completions API. This includes system text, user and assistant text, images, tool calls, and tool results.

**Data flow**: It starts with the system instruction and the tuple of UFO messages. It first passes messages through trim_images, which reduces image history according to project rules. It then walks through each message block: text is joined, images are converted, tool uses become function-call records, and tool results become tool messages. The output is a list of dictionaries ready to send in a Chat Completions request.

**Call relations**: OpenAIClient._chat_kwargs calls this while building the request body for the chat API. It delegates image formatting to _openai_image, tool-result splitting to _openai_tool_result, and JSON argument encoding to json.dumps.

*Call graph*: calls 2 internal fn (_openai_image, _openai_tool_result); called by 1 (_chat_kwargs); 2 external calls (dumps, trim_images).


##### `responses_input`  (lines 162–228)

```
def responses_input(messages: tuple[Message, ...]) -> list[ResponseInputItemParam]
```

**Purpose**: Converts UFO’s conversation history into input items for OpenAI’s Responses API. The Responses API uses a different shape from Chat Completions, so the same internal messages need a separate translation path.

**Data flow**: It receives UFO messages, trims image history, and then turns each message or block into typed Responses API objects. Text becomes input text, images become input images, tool-use blocks become function-call items, and tool-result blocks become function-call-output items. The result is an ordered list that the Responses API accepts as its input.

**Call relations**: responses_request calls this when preparing a Responses API request. It uses OpenAI’s typed request helper classes and json.dumps so tool arguments and outputs are encoded in the format the provider expects.

*Call graph*: called by 1 (responses_request); 9 external calls (dumps, EasyInputMessageParam, ResponseFunctionToolCallParam, ResponseInputImageContentParam, ResponseInputImageParam, FunctionCallOutput, ResponseInputTextContentParam, ResponseInputTextParam, trim_images).


##### `responses_request`  (lines 231–256)

```
def responses_request(request: ModelRequest) -> dict[str, Any]
```

**Purpose**: Builds the full request body for a streaming Responses API call. It combines the model name, instructions, converted input history, token limit, reasoning setting, and available tools.

**Data flow**: It takes a ModelRequest. It creates a dictionary containing the model, system instructions, converted input items, maximum output tokens, and streaming settings. If reasoning is enabled, it adds the requested reasoning effort. If tools are available, it adds tool definitions and tool-choice rules. The output is the keyword-argument dictionary passed to the OpenAI SDK.

**Call relations**: OpenAIClient._complete_responses calls this immediately before opening a Responses API stream. It depends on responses_input for the conversation conversion and uses OpenAI’s FunctionToolParam objects for tool definitions.

*Call graph*: calls 1 internal fn (responses_input); called by 1 (_complete_responses); 1 external calls (FunctionToolParam).


##### `OpenAIClient.complete`  (lines 268–271)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Chooses the correct OpenAI API style for one model request and starts the streaming completion. Callers use this as the public entry point for getting model events from this client.

**Data flow**: It receives a ModelRequest. It checks the model specification stored on the client to see whether the model should use the Responses API or the Chat Completions API. It returns an asynchronous stream of ModelEvent objects from the chosen private method.

**Call relations**: Higher-level model orchestration calls this when it wants a completion from an OpenAI-wire model. complete then hands the request to either _complete_responses or _complete_chat, hiding that provider-specific choice from the rest of the system.

*Call graph*: calls 2 internal fn (_complete_chat, _complete_responses).


##### `OpenAIClient._chat_kwargs`  (lines 273–302)

```
def _chat_kwargs(self, request: ModelRequest) -> dict[str, Any]
```

**Purpose**: Builds the request body for a Chat Completions streaming call. It also applies the model specification’s rules about whether reasoning settings are allowed, especially when tools are present.

**Data flow**: It takes a ModelRequest and creates a dictionary with the model, converted chat messages, token budget, streaming flags, and usage-reporting flag. It asks the model spec for the effective reasoning effort and includes it only when allowed. If tools are present, it adds their function schemas and any required tool choice. The result is a dictionary passed to the SDK’s chat completion call.

**Call relations**: _complete_chat calls this right before contacting the provider. It calls openai_messages to translate UFO’s internal message history into Chat Completions format.

*Call graph*: calls 1 internal fn (openai_messages); called by 1 (_complete_chat).


##### `OpenAIClient._complete_chat`  (lines 304–406)

```
async def _complete_chat(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Sends a streaming Chat Completions request and translates the provider’s stream into UFO model events. It also enforces retry, timeout, truncation, empty-response, and usage-reporting rules.

**Data flow**: It receives a ModelRequest. Before sending, it builds Chat Completions arguments with _chat_kwargs. As chunks arrive, text deltas become TextDelta events, tool-call starts become ToolCallStart events, tool-call argument fragments become ToolCallDelta events, and final provider usage becomes a Usage event. Temporary rate-limit, server, or timeout failures are retried only if no output has been yielded yet. If the model stops because it hit the token limit, it raises ModelResponseTruncated instead of pretending the answer is complete.

**Call relations**: OpenAIClient.complete calls this for models whose specification uses the Chat Completions surface. During the stream it creates standard UFO event objects for callers, logs provider timeouts, sleeps between retry attempts, and returns only after yielding final usage.

*Call graph*: calls 1 internal fn (_chat_kwargs); called by 1 (complete); 7 external calls (__init__, __init__, __init__, __init__, __init__, sleep, log).


##### `OpenAIClient._complete_responses`  (lines 408–516)

```
async def _complete_responses(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Sends a streaming Responses API request and translates its event stream into UFO’s standard model events. It is the Responses API counterpart to _complete_chat, with the same broad contract but different provider event shapes.

**Data flow**: It receives a ModelRequest, first adjusts the reasoning setting according to the model specification, then builds a Responses API request with responses_request. As provider events arrive, text events become TextDelta, function-call starts become ToolCallStart, argument fragments become ToolCallDelta, and completed usage becomes a final Usage event. Refusals, content filtering, failed responses, and max-token truncation are turned into explicit UFO exceptions. Temporary provider errors are retried only before any output has been yielded.

**Call relations**: OpenAIClient.complete calls this for models whose specification says to use the Responses API. It relies on responses_request for the outgoing payload, creates UFO events for the rest of the system, and uses sleep-based backoff when retrying temporary provider failures.

*Call graph*: calls 1 internal fn (responses_request); called by 1 (complete); 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, sleep, model_copy, log).


### Sandbox, schema, and skill namespaces
These import signposts prepare sandbox, proxy, schema, and skill areas for use by higher-level runtime features.

### `core/src/ufo/sandbox/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can act as an importable package when it has an `__init__.py` file. That means other parts of the project can refer to code inside `core/src/ufo/sandbox` using normal import paths, such as `ufo.sandbox.some_module`.

There is no setup code, no helper function, and no hidden configuration here. Its job is structural: it tells Python and project tools that the `sandbox` directory is a named part of the `ufo` code tree. An everyday analogy is a label on a filing cabinet drawer. The label does not contain documents itself, but it makes the drawer recognizable and usable by the filing system.

Without this file, depending on the Python version and packaging setup, imports from the `ufo.sandbox` area could become less predictable or fail in some environments. Keeping it present makes the package boundary explicit.


### `core/src/ufo/sandbox/proxy/__init__.py`

`other` · `import/package discovery`

This is an empty Python package file. In Python projects, an `__init__.py` file is commonly used to tell Python, tools, and readers that a folder is meant to be treated as an importable package. Here, it makes the `ufo.sandbox.proxy` folder available as part of the larger `ufo` code structure.

There are no functions, classes, settings, or side effects in this file. Nothing is computed, opened, started, or changed when it is loaded. Its value is organizational: it gives this directory a clear place in the project’s import tree, much like putting a label on a drawer so other parts of the system know where to find proxy-related sandbox code.

Without this file, depending on the Python version and tooling, imports from this folder could be less explicit or fail in environments that expect traditional packages. Keeping it present helps make the package layout predictable.


### `core/src/ufo/schema/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can write imports that refer to `ufo.schema` and to files inside that folder.

There is no code here, so nothing runs and no data is changed. Its value is structural: it gives the project a clear place for schema-related code, such as definitions of data shapes, validation rules, or shared formats, even though this particular file does not expose any of those directly.

An everyday analogy is a labeled folder in a filing cabinet. The label does not contain documents itself, but it tells people and tools that the folder is a meaningful section of the system.


### `core/src/ufo/skills/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, it makes the `core/src/ufo/skills` directory available as `ufo.skills` to the rest of the project.

Because the file is empty, it does not define any functions, classes, settings, or startup behavior. Its value is structural: without it, some Python tooling or older import rules might not recognize this folder as a proper package, and imports that expect `ufo.skills` to exist could fail or behave inconsistently.

A simple analogy is a label on a drawer. The label does not contain the tools, but it tells people and systems that this drawer is a named place where related tools belong.


### REST source connector foundations
The sources package marker introduces reusable REST connector infrastructure for safe clients, retries, and pagination.

### `core/src/ufo/sources/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. That matters because code elsewhere in the project may want to refer to things inside `ufo.sources` using normal import paths, such as importing source-related modules from this directory. Think of it like putting a label on a folder in a filing cabinet: the label does not hold documents itself, but it lets the rest of the system find and refer to that folder reliably. Since this file has no functions, classes, or setup code, importing `ufo.sources` does not run any custom behavior here. Its job is structural rather than active.


### `core/src/ufo/sources/rest.py`

`io_transport` · `request handling during source reads`

Many outside services expose data through REST APIs, where the program asks for one URL and gets back JSON data. Those APIs usually add chores: authentication, timeouts, temporary failures, and pagination, meaning results arrive in many pages instead of one big response. This file keeps every REST-based source connector from having to rewrite that same plumbing.

The main class, RestConnector, is a base class. A specific service connector supplies details such as its base URL and list of streams, where a stream is one kind of data to read, like users or tickets. RestConnector then builds an httpx asynchronous client, which can make network requests without blocking the rest of the sync run. It sends GET or read-style POST requests through one shared retry wrapper, so short outages, rate limits, and server errors are retried with increasing waits.

The other major job is pagination. APIs signal “there is another page” in different ways: a next cursor in the JSON body, a next link in an HTTP header, an offset and limit, page numbers, or Microsoft OData links. This file contains loops for those patterns and includes safety checks so a bad API response cannot trap the sync in an endless loop. Before pages leave this layer, records are checked, optionally flattened, and yielded to the rest of the source system.

#### Function details

##### `get_path`  (lines 42–51)

```
def get_path(data: Mapping[str, Any], path: str, default: Any=None) -> Any
```

**Purpose**: Reads a value from nested dictionary-like data using a dotted path such as "meta.next.cursor". It is useful when APIs wrap important fields several layers deep.

**Data flow**: It receives a mapping, a dotted path, and a fallback value. It walks through the mapping one path part at a time; if any step is missing or not dictionary-like, it returns the fallback. If the full path exists, it returns the value found there.

**Call relations**: Pagination helpers use this when they need to find records, cursor tokens, page-size values, or continuation flags inside an API response. records_at also relies on it to reach nested lists before cleaning them up.

*Call graph*: called by 3 (_get_cursor_pages, _get_offset_pages, records_at).


##### `list_or_empty`  (lines 54–58)

```
def list_or_empty(value: Any) -> list[dict[str, Any]]
```

**Purpose**: Turns uncertain API data into a safe list of record dictionaries. If the value is not a list, or if list items are not dictionaries, they are ignored.

**Data flow**: It receives any value. If the value is a list, it keeps only items that look like records, meaning dictionaries; otherwise it returns an empty list. The output is always a list of dictionaries.

**Call relations**: This is the basic cleanup step used by response parsing helpers. It supports records_at, _response_list, and the OData pagination loop so later code can assume it is working with record-shaped data.

*Call graph*: called by 3 (_get_odata_pages, _response_list, records_at).


##### `dict_or_empty`  (lines 61–64)

```
def dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: Safely treats a value as a single record-shaped dictionary. If the value is not a dictionary, it returns an empty dictionary instead.

**Data flow**: It receives any value. A dictionary passes through unchanged; anything else becomes {}. It does not change outside state.

**Call relations**: This helper is available for connector subclasses that need to pull a nested object out of an API response. It is not called inside this file, but it matches the safety style of list_or_empty.


##### `records_at`  (lines 67–72)

```
def records_at(data: Any, path: str | None) -> list[dict[str, Any]]
```

**Purpose**: Finds the list of records inside an API response, either at the top level or at a named nested path. It protects the rest of the code from malformed response shapes.

**Data flow**: It receives response data and an optional path. With no path, it treats the response itself as the list. With a path, it first checks that the response is mapping-like, uses get_path to find the nested value, then uses list_or_empty to return only dictionary records.

**Call relations**: The pagination loops call this after each API response to extract the actual records. The link-header strategy can also create a small parser that uses records_at when records are wrapped in an envelope object.

*Call graph*: calls 2 internal fn (get_path, list_or_empty); called by 4 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages, parse).


##### `_int_or_none`  (lines 75–80)

```
def _int_or_none(value: Any) -> int | None
```

**Purpose**: Converts a value to an integer only when it is already an integer or a simple decimal string. It avoids guessing with messy values.

**Data flow**: It receives any value. Integers pass through, strings like "25" become 25, and everything else becomes None. The result helps decide how far to advance an offset.

**Call relations**: The offset-based pagination loop uses this when an API tells the client how many records it actually returned or accepted as its limit.

*Call graph*: called by 1 (_get_offset_pages).


##### `with_context`  (lines 83–86)

```
def with_context(records: Iterable[dict[str, Any]], **context: Any) -> list[dict[str, Any]]
```

**Purpose**: Adds shared context fields to every record, such as a parent ID or cloud site ID. This preserves where a record came from when a connector fans out across parent objects.

**Data flow**: It receives an iterable of record dictionaries and named context values. It copies each record into a new dictionary and adds the context fields. The output is a new list; the original records are not modified.

**Call relations**: This helper is meant for connector subclasses that fetch child records under a parent. It lets downstream rendering and storage know the origin of each record.


##### `next_link`  (lines 89–94)

```
def next_link(headers: httpx.Headers) -> str | None
```

**Purpose**: Finds the URL for the next page in an HTTP Link header. Some APIs put pagination instructions in response headers instead of the JSON body.

**Data flow**: It receives response headers, looks for the "link" header, and searches for the part marked rel="next". It returns that URL string when present, otherwise None.

**Call relations**: The link-header pagination loop calls this after each response. If it finds a next URL, the loop follows it; if not, pagination ends.

*Call graph*: called by 1 (_get_link_header_pages); 1 external calls (get).


##### `_is_retryable`  (lines 97–102)

```
def _is_retryable(error: BaseException) -> bool
```

**Purpose**: Decides whether a failed request is worth trying again. It treats network transport failures and common temporary HTTP statuses, such as rate limits and server errors, as retryable.

**Data flow**: It receives an exception. If it is a network-level httpx transport error, it returns true. If it is an HTTP status error, it checks whether the status code is one of the configured temporary statuses. Other errors return false.

**Call relations**: The shared _send method calls this after a request fails. Its answer decides whether _send sleeps and retries or lets the error stop the sync attempt.

*Call graph*: called by 1 (_send).


##### `_raise_for_status`  (lines 105–116)

```
def _raise_for_status(response: httpx.Response) -> None
```

**Purpose**: Raises a clear error when an HTTP response is not successful, including a short copy of the response body. That body often contains the real reason the API rejected the request.

**Data flow**: It receives an httpx response. Successful responses return quietly. Failed responses produce an HTTPStatusError whose message includes the status, request method, URL, and a capped amount of body text.

**Call relations**: _send calls this immediately after receiving a response. It turns bad HTTP statuses into exceptions that the retry logic can either retry or surface.

*Call graph*: called by 1 (_send); 1 external calls (HTTPStatusError).


##### `_json_or_empty`  (lines 119–123)

```
def _json_or_empty(response: httpx.Response) -> dict[str, Any]
```

**Purpose**: Reads a JSON object from a response, but treats empty responses as an empty dictionary. This keeps callers from crashing on valid empty API replies.

**Data flow**: It receives an HTTP response. If the status is 204 or there is no content, it returns {}. Otherwise it parses the response body as JSON and returns it as a dictionary.

**Call relations**: _get and _post use this after _send has already checked for request failure. It is the normal path for API endpoints that return object-shaped JSON.

*Call graph*: called by 2 (_get, _post); 1 external calls (json).


##### `_response_list`  (lines 126–129)

```
def _response_list(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Reads a top-level JSON list of records from a response. Empty or non-list responses become an empty list.

**Data flow**: It receives an HTTP response. Empty responses return []; otherwise it parses JSON and passes the result through list_or_empty, keeping only dictionary records.

**Call relations**: The link-header pagination loop uses this when the API returns records directly as a top-level array rather than inside a named field.

*Call graph*: calls 1 internal fn (list_or_empty); called by 1 (_get_link_header_pages); 1 external calls (json).


##### `_bound_pages`  (lines 132–138)

```
def _bound_pages(who: str, pages: int) -> None
```

**Purpose**: Stops a pagination loop that runs for too many pages. This is a safety brake against broken or hostile APIs that never signal the end.

**Data flow**: It receives a label describing the loop and the current page count. If the count is within the maximum, nothing happens. If it exceeds the maximum, it raises an error so the sync fails safely instead of spinning forever.

**Call relations**: Every built-in pagination loop calls this once per page. It protects the broader sync driver from being held hostage by a never-ending fetch.

*Call graph*: called by 5 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages, _get_offset_pages, _get_page_number_pages).


##### `_bound_cursor`  (lines 141–147)

```
def _bound_cursor(who: str, token: str, seen: set[str]) -> None
```

**Purpose**: Stops cursor-based pagination when the API repeats a cursor or next-link that was already used. A repeated token means the server is not advancing to new data.

**Data flow**: It receives a label, the current token or URL, and a set of tokens already seen. If the token is new, it adds it to the set. If it was seen before, it raises an error.

**Call relations**: Cursor, link-header, and OData pagination loops call this before following the next page signal. It catches an infinite loop earlier and more clearly than the page-count limit.

*Call graph*: called by 3 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages).


##### `RestConnector.streams`  (lines 156–157)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns the list of streams this connector knows how to read. A stream is a named collection of records from the provider.

**Data flow**: It reads the class-level streams_list and returns a new list copy. The copy means callers can inspect or modify their local list without changing the connector class definition.

**Call relations**: The wider source system asks connectors for their streams before fetching data. Subclasses usually provide streams_list, and this method exposes it in the common Connector interface.


##### `RestConnector._make_client`  (lines 159–176)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the asynchronous HTTP client used for one account and one base API URL. It applies timeouts, JSON headers, and the authentication method supplied by the resolved credential.

**Data flow**: It receives a base URL and a credential. It normalizes the URL, prepares standard JSON headers, then chooses one auth path: a proxy transport, a bearer token, or explicit headers. It returns an httpx AsyncClient, or raises an error if no authentication is available.

**Call relations**: fetch_page calls this before starting pagination. All later GET and POST helpers use the client it creates, so authentication and timeout behavior stay consistent.

*Call graph*: called by 1 (fetch_page); 2 external calls (AsyncClient, Timeout).


##### `RestConnector._get`  (lines 178–181)

```
async def _get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Makes a retried GET request and returns the response as a JSON object. It is the usual helper for read endpoints that return object-shaped JSON.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It asks _get_raw to perform the request with retry behavior, then passes the response to _json_or_empty. The result is a dictionary, with empty responses represented as {}.

**Call relations**: Cursor, offset, and page-number pagination loops call this for each page. It delegates the network work to _get_raw so all GET calls share the same retry wrapper.

*Call graph*: calls 2 internal fn (_get_raw, _json_or_empty); called by 3 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages).


##### `RestConnector._get_raw`  (lines 183–187)

```
async def _get_raw(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: Makes a retried GET request and returns the full HTTP response. This is needed when pagination information lives in headers or when callers need more than just the JSON body.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It wraps client.get in _send, which performs the request, checks status, and retries temporary failures. It returns the successful httpx response.

**Call relations**: _get builds on this for ordinary JSON object responses. Link-header and OData pagination use it directly because they need response headers or full response handling.

*Call graph*: calls 1 internal fn (_send); called by 3 (_get, _get_link_header_pages, _get_odata_pages).


##### `RestConnector._post`  (lines 189–194)

```
async def _post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Makes a retried POST request for read-only API endpoints that require POST, such as search endpoints. It does not add a write feature; it is still used to read data.

**Data flow**: It receives an HTTP client, a path, and an optional JSON body. It sends the POST through _send, then parses the successful response with _json_or_empty. The output is a dictionary.

**Call relations**: Connector subclasses can call this when a provider exposes a read operation over POST. It shares the same retry and error behavior as GET requests.

*Call graph*: calls 2 internal fn (_send, _json_or_empty).


##### `RestConnector._post_raw`  (lines 196–202)

```
async def _post_raw(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: Makes a retried POST request and returns the raw response. This supports read endpoints whose response is not a normal JSON object, such as a top-level array.

**Data flow**: It receives an HTTP client, a path, and an optional JSON body. It sends client.post through _send and returns the successful response unchanged. The caller decides how to parse the body.

**Call relations**: Connector subclasses use this for unusual read-style POST responses. It hands off to _send so temporary failures are retried the same way as other requests.

*Call graph*: calls 1 internal fn (_send).


##### `RestConnector._send`  (lines 204–218)

```
async def _send(self, request: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response
```

**Purpose**: Provides the shared retry wrapper for HTTP requests. It retries temporary network problems, rate limits, and server errors with exponential backoff, meaning each wait gets longer.

**Data flow**: It receives a zero-argument async request function. It calls the function, checks the response status, and returns the response when successful. If a retryable error occurs, it sleeps and tries again until the attempt limit is reached; non-retryable or exhausted errors are raised.

**Call relations**: _get_raw, _post, and _post_raw all route requests through this method. This centralizes retry behavior so individual connectors do not implement their own inconsistent network handling.

*Call graph*: calls 2 internal fn (_is_retryable, _raise_for_status); called by 3 (_get_raw, _post, _post_raw); 1 external calls (sleep).


##### `RestConnector.fetch_page`  (lines 220–250)

```
async def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main read entry for a REST connector page stream. It opens the HTTP client, runs pagination, validates each page, flattens records if needed, and yields clean pages onward.

**Data flow**: It receives a stream, optional cursor, credential, base URL, and optional current user ID. It chooses the final base URL, creates a client, asks paginate_source for pages, skips empty pages, validates records, applies flatten to each record, and yields either plain record lists or StreamPage objects with delete and cursor metadata preserved. It also closes an async generator source if needed.

**Call relations**: The core sync driver calls this to read provider data. It is the bridge between connector-specific pagination and the rest of the system that expects validated, flattened records.

*Call graph*: calls 4 internal fn (_make_client, _validate_page, flatten, paginate_source); 1 external calls (__init__).


##### `RestConnector.paginate_source`  (lines 252–260)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Adapts the generic pagination call to include extra source context, such as the current user ID. The default version simply calls paginate.

**Data flow**: It receives the HTTP client, stream, cursor, and self_user_id. By default it ignores self_user_id and returns the async iterator from paginate. No records are changed here.

**Call relations**: fetch_page calls this rather than paginate directly so subclasses can override it when they need extra context while paging. The default path hands control to paginate.

*Call graph*: calls 1 internal fn (paginate); called by 1 (fetch_page).


##### `RestConnector.paginate`  (lines 262–274)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses how to produce raw pages for a stream. The default only works for streams that declare one of the built-in pagination strategies.

**Data flow**: It receives an HTTP client, stream, and cursor. It checks the stream pagination setting; if none is declared, it raises NotImplementedError so the subclass must provide custom logic. Otherwise it yields pages from paginate_from_strategy.

**Call relations**: paginate_source calls this in the default flow. Subclasses can override it for APIs with shapes that do not fit the shared strategies.

*Call graph*: calls 1 internal fn (paginate_from_strategy); called by 1 (paginate_source).


##### `RestConnector.paginate_from_strategy`  (lines 276–338)

```
async def paginate_from_strategy(self, stream: StreamSpec, *, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs the built-in pagination loop described by a stream's Pagination setting. It turns a declarative stream configuration into actual page-by-page API requests.

**Data flow**: It receives a stream and HTTP client. It reads the strategy, path, record path, cursor names, page size, and extra parameters from the stream specification. Depending on the strategy, it delegates to the cursor, link-header, or offset pagination helper and yields each list of records.

**Call relations**: paginate calls this for streams that use shared pagination. It is the dispatcher that sends each stream to the right pagination machine.

*Call graph*: calls 4 internal fn (_get_cursor_pages, _get_link_header_pages, _get_offset_pages, _strategy_path); called by 1 (paginate).


##### `RestConnector.paginate_from_strategy.parse`  (lines 308–310)

```
def parse(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Extracts records from a link-header paginated response when those records are nested inside the JSON body. It is a small local parser created only when the stream declares a record path.

**Data flow**: It receives an HTTP response. It parses the body as JSON when present, then calls records_at with the configured record path. It returns a list of dictionary records.

**Call relations**: paginate_from_strategy passes this parser into _get_link_header_pages for link-header APIs with enveloped responses. The pagination loop calls it for each response instead of assuming the response body is a top-level list.

*Call graph*: calls 1 internal fn (records_at); 1 external calls (json).


##### `RestConnector._get_link_header_pages`  (lines 340–368)

```
async def _get_link_header_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, page_size_param: str | None='per_page', page_size: int | None=None, parse_records: C
```

**Purpose**: Fetches pages for APIs that point to the next page in the HTTP Link header. This is common in REST APIs that follow web linking conventions.

**Data flow**: It receives a client, starting path, query parameters, optional page-size settings, and an optional response parser. It requests the first page, extracts records, yields non-empty pages, reads the next-link header, checks for repeated links and page limits, then follows the next URL until there is no next link.

**Call relations**: paginate_from_strategy calls this for the next_link strategy. It relies on _get_raw for network requests, next_link for header parsing, and the bound checks for safety.

*Call graph*: calls 5 internal fn (_get_raw, _bound_cursor, _bound_pages, _response_list, next_link); called by 1 (paginate_from_strategy).


##### `RestConnector._get_cursor_pages`  (lines 370–402)

```
async def _get_cursor_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, next_cursor_path: str, params: dict[str, Any] | None=None, cursor_param: str='cursor', page_size_pa
```

**Purpose**: Fetches pages for APIs that return a next cursor token in the JSON body. A cursor is like a bookmark the API gives back so the next request can continue where the last one stopped.

**Data flow**: It receives a client, path, record path, cursor path, cursor parameter name, optional page-size settings, and extra query parameters. It sends a request, extracts records, yields them, reads the next cursor from the response, checks that it is new, and repeats until no valid cursor is returned.

**Call relations**: paginate_from_strategy calls this for the next_cursor strategy. It uses _get for requests, records_at for records, get_path for cursor lookup, and safety guards to prevent endless loops.

*Call graph*: calls 5 internal fn (_get, _bound_cursor, _bound_pages, get_path, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._get_odata_pages`  (lines 404–430)

```
async def _get_odata_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches pages from Microsoft Graph or other OData-style APIs. In this style, records are under "value" and the next page URL is in "@odata.nextLink".

**Data flow**: It receives a client, path, and optional parameters. It requests the current path, extracts dictionary records from the "value" field, yields them, reads the next-link URL, checks for repeats, and continues. Only the first request uses the original parameters because OData next links already include their own query details.

**Call relations**: This helper is available to subclasses with OData APIs. It uses _get_raw for requests, list_or_empty for safe record extraction, and the shared bound checks for loop safety.

*Call graph*: calls 4 internal fn (_get_raw, _bound_cursor, _bound_pages, list_or_empty).


##### `RestConnector._get_offset_pages`  (lines 432–472)

```
async def _get_offset_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, limit: int, params: dict[str, Any] | None=None, limit_param: str='limit', offset_param: str='offset
```

**Purpose**: Fetches pages for APIs that use offset and limit parameters. Offset means “start at item number N,” and limit means “return up to this many items.”

**Data flow**: It receives a client, path, record path, limit size, parameter names, optional extra parameters, and optional response fields that describe continuation. It requests a page at the current offset, extracts records, yields them, decides whether another page exists, then advances the offset by either the configured limit or a server-reported step.

**Call relations**: paginate_from_strategy calls this for the offset_limit strategy. It uses _get for requests, records_at and get_path to read the response, _int_or_none for server-reported page sizes, and _bound_pages for safety.

*Call graph*: calls 5 internal fn (_get, _bound_pages, _int_or_none, get_path, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._get_page_number_pages`  (lines 474–503)

```
async def _get_page_number_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, page_size: int, params: dict[str, Any] | None=None, page_param: str='page', page_size_param: s
```

**Purpose**: Fetches pages for APIs that use page numbers, such as page=1, page=2, and so on. It stops when a page comes back shorter than the requested page size.

**Data flow**: It receives a client, path, record path, page size, optional parameters, parameter names, and a starting page number. It requests each page number in order, extracts records, yields them when present, and stops once fewer records than the page size are returned.

**Call relations**: This helper is available for subclasses whose APIs use page numbers. It shares _get, records_at, and the page-count guard with the other pagination helpers.

*Call graph*: calls 3 internal fn (_get, _bound_pages, records_at).


##### `RestConnector._strategy_path`  (lines 505–511)

```
def _strategy_path(self, stream: StreamSpec) -> str
```

**Purpose**: Resolves the request path for a stream when the stream's Pagination setting did not include one. The default raises an error because the base class does not know provider-specific paths.

**Data flow**: It receives a stream. Instead of returning a path, the base implementation raises NotImplementedError with a message explaining that a subclass must provide the missing path logic.

**Call relations**: paginate_from_strategy calls this only when a pagination spec has no path. Connectors with their own per-stream path table can override it to keep stream declarations shorter.

*Call graph*: called by 1 (paginate_from_strategy).


##### `RestConnector.flatten`  (lines 513–516)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Converts one API record into the flat dictionary shape the sync writer expects. The default leaves the record unchanged.

**Data flow**: It receives a record dictionary and its stream. It returns the same record by default. Subclasses can override it to lift nested fields or reshape provider-specific payloads before storage.

**Call relations**: fetch_page calls this on every validated record just before yielding pages to the rest of the sync system. It is the final shaping hook for subclasses.

*Call graph*: called by 1 (fetch_page).


##### `RestConnector._validate_page`  (lines 518–529)

```
def _validate_page(self, page: Any, stream: StreamSpec) -> None
```

**Purpose**: Checks that a pagination result is actually a list of dictionary records. It fails early with a clear error if a connector yields the wrong shape.

**Data flow**: It receives a page value and stream. It first confirms the page is a list, then checks that every item is a dictionary. If the shape is wrong, it raises TypeError; otherwise it returns without changing anything.

**Call relations**: fetch_page calls this before flattening or yielding data. It protects downstream sync code from confusing malformed connector output.

*Call graph*: called by 1 (fetch_page).


### Shared labels and tool signposts
Shared subject labels and package markers keep conversation ownership, surface code, and tool-related imports consistent.

### `core/src/ufo/subjects.py`

`util` · `cross-cutting`

This file is a small but important naming rulebook. In this project, a “subject” is a short text value that marks the audience or owner of something, such as a source or a conversation audience. There is one fixed subject for things everyone shares: "shared". There is also a pattern for things tied to one specific member: the word "member:" followed by that member’s unique ID.

Without this file, different parts of the system might invent slightly different labels, such as "user:", "member-", or "members/". That would make matching and filtering unreliable, like filing papers under several different spellings of the same name. By putting the shared label and member prefix in one place, the codebase has one agreed format.

The main helper, member_subject, takes a member’s UUID, which is a unique identifier, and turns it into the exact subject string the rest of the system expects. The file does not store data or talk to a database. It simply provides shared constants and a safe way to build a member subject consistently.

#### Function details

##### `member_subject`  (lines 9–10)

```
def member_subject(member_id: UUID) -> str
```

**Purpose**: Builds the standard subject label for one specific member. Someone would use it when they need to tag or look up information that belongs to that member.

**Data flow**: It receives a member_id, which is a UUID, meaning a unique identifier for a member. It places that ID after the fixed text prefix "member:". It returns the finished string, such as "member:<id>", without changing anything else.

**Call relations**: This function is the shared doorway for creating member-specific subject names. Other code can call it whenever it needs the official member subject format instead of rebuilding the text by hand.


### `core/src/ufo/surfaces/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, that means code elsewhere can refer to modules under `ufo.surfaces` in a clean, organized way.

There is no executable logic, no classes, and no functions in this file. Its value is structural: it acts like a label on a drawer in a filing cabinet. The drawer may contain important surface-related code in neighboring files, but this file itself only helps Python recognize the drawer as part of the project’s module layout.

Without this file, some import styles or tooling may not recognize `ufo.surfaces` as a regular package, especially in environments that still rely on traditional Python package markers. Keeping it present makes the package boundary explicit and helps the codebase stay predictable.


### `core/src/ufo/tools/__init__.py`

`other` · `import time`

This is a small package marker file. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package. Here, that package is `ufo.tools`, which appears to be the home for the project’s “tools”: pieces of functionality that can be registered, given context, and then used by the rest of the system.

The file itself does not define any functions, classes, or setup code. Its only content is a plain docstring, which acts like a label on a drawer: it tells future readers what kinds of things belong in this part of the codebase. The three named ideas are the registry, which is likely where available tools are listed; the handler context, which likely carries information a tool needs while running; and the built-in tool set, which likely contains tools provided by the project itself.

Without this file, depending on the Python version and packaging setup, importing `ufo.tools` could be less explicit or could fail in some environments. More importantly, this file gives the package a clear identity and a bit of documentation right where newcomers will look first.


### External extension adapters
Extension package markers and the OpenRouter adapter connect optional external capabilities into the shared provider ecosystem.

### `extensions/browser/ufo_ext_browser/bua/__init__.py`

`other` · `import/package discovery`

This is an empty Python `__init__.py` file. Its main job is structural: it tells Python that the `bua` directory should be treated as an importable package. Think of it like a label on a folder in a filing cabinet. The label does not contain the documents, but it lets people refer to the folder by name and find what is inside.

Without this file, some Python environments or import styles might not recognize `extensions.browser.ufo_ext_browser.bua` as a package, which could make imports fail or behave inconsistently. Because the file is empty, it does not set up configuration, define shared names, or run startup code. Any real behavior for this package lives in other files inside the same directory.


### `extensions/openrouter/ufo_ext_openrouter.py`

`io_transport` · `extension discovery and model request handling`

The core system already knows how to talk to some model providers directly. This file adds OpenRouter as an extension, because OpenRouter is not one model company: it is more like a switchboard that forwards a request to another provider behind the scenes. The file translates the system’s normal model request into the OpenAI Chat Completions format that OpenRouter understands, opens a streaming connection, and turns the streamed reply back into the system’s standard events: pieces of text, tool-call starts, tool-call argument chunks, and final token usage.

It also adds OpenRouter-specific behavior. If a model name is written as a plain OpenAI or Anthropic name, it adds the right provider prefix so OpenRouter can recognize it. If the requested model supports reasoning, it sends a reasoning effort setting. If OpenRouter returns an empty successful answer from one upstream provider, the client treats that provider as possibly broken and retries while asking OpenRouter to avoid it, like asking a taxi dispatcher not to send the same stalled car again.

The file also defines the OpenRouter models this extension offers, including their prices, context window size, and API key settings. Without this file, the system would not know that these OpenRouter-hosted models exist or how to stream responses from them safely.

#### Function details

##### `openrouter_slug`  (lines 53–63)

```
def openrouter_slug(model: str) -> str
```

**Purpose**: This function turns a model name into the provider/model format OpenRouter expects. It lets users write familiar names like OpenAI or Claude model IDs while still sending OpenRouter a clear routing name.

**Data flow**: It receives a model string. If the string already contains a slash, it is treated as already complete and returned unchanged. If it looks like an OpenAI or Anthropic model name, the function adds the matching provider prefix; otherwise it leaves the name as-is for OpenRouter to interpret.

**Call relations**: When OpenRouterModelClient._create_kwargs builds the outgoing API request, it calls this function so the model field is in a shape OpenRouter can route correctly.

*Call graph*: called by 1 (_create_kwargs).


##### `_chunk_provider`  (lines 66–71)

```
def _chunk_provider(chunk: ChatCompletionChunk) -> str | None
```

**Purpose**: This function reads which upstream provider OpenRouter used for a streamed response chunk, if OpenRouter included that detail. That matters because an empty response can be retried while excluding the provider that produced it.

**Data flow**: It receives one streamed chat chunk from the OpenAI-style API. It looks inside the chunk’s extra OpenRouter metadata for a provider name and returns that name as text, or returns nothing if the provider is not present.

**Call relations**: OpenRouterModelClient.complete calls this while reading the stream. The returned provider name can later be added to the ignore list if the stream finishes without producing any useful text or tool call.

*Call graph*: called by 1 (complete).


##### `_usage_of`  (lines 74–83)

```
def _usage_of(usage: CompletionUsage) -> Usage
```

**Purpose**: This function converts OpenAI-style token usage into the system’s own Usage object. It separates normal input tokens from cached input tokens so cost and accounting stay accurate.

**Data flow**: It receives usage data from the API, including prompt tokens, completion tokens, and optional details about cached prompt tokens. It checks that cached tokens are not larger than total prompt tokens, then creates a Usage value with input, output, and cache-read token counts.

**Call relations**: OpenRouterModelClient.complete calls this when the streaming API sends final usage information. The resulting Usage object is yielded as the last event in a successful model response.

*Call graph*: called by 1 (complete); 1 external calls (__init__).


##### `OpenRouterModelClient.complete`  (lines 100–168)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the main streaming call for OpenRouter. It sends a model request, yields reply events as they arrive, retries certain provider failures, and reports final token usage.

**Data flow**: It receives a ModelRequest containing the model name, messages, tools, token limit, and reasoning preference. It builds OpenRouter API arguments, opens a streaming chat completion, and turns each incoming chunk into text or tool-call events. It watches for final usage, truncation, API errors, and empty successful responses. On success, it yields all streamed events followed by one Usage object; on truncation or unrecoverable failure, it raises an error.

**Call relations**: This method is the file’s central request path. It asks _create_kwargs to prepare the outgoing API call, uses _chunk_provider to remember which upstream answered, uses _usage_of to convert billing data, and yields standard model events such as TextDelta, ToolCallStart, and ToolCallDelta. If OpenRouter or an upstream returns retryable errors before any output is produced, it waits with asyncio.sleep and tries again.

*Call graph*: calls 3 internal fn (_create_kwargs, _chunk_provider, _usage_of); 5 external calls (__init__, __init__, __init__, __init__, sleep).


##### `OpenRouterModelClient._create_kwargs`  (lines 170–199)

```
def _create_kwargs(self, request: ModelRequest, ignore_providers: frozenset[str]) -> dict[str, Any]
```

**Purpose**: This function prepares the exact keyword arguments sent to OpenRouter’s OpenAI-compatible chat API. It is the bridge between the system’s internal request shape and OpenRouter’s wire format.

**Data flow**: It receives a ModelRequest and a set of providers to avoid. It converts the model name with openrouter_slug, converts messages into OpenAI-style messages, adds token limits, streaming settings, usage reporting, optional reasoning effort, optional provider ignore rules, and optional tool definitions. It returns a dictionary ready to pass to the OpenAI SDK client.

**Call relations**: OpenRouterModelClient.complete calls this right before creating the streaming API request. It delegates message conversion to the shared openai_messages helper so this extension uses the same message format rules as the rest of the OpenAI-compatible code.

*Call graph*: calls 1 internal fn (openrouter_slug); called by 1 (complete); 1 external calls (openai_messages).


##### `_model_client`  (lines 202–206)

```
def _model_client(spec: ModelSpec, key: str) -> OpenRouterModelClient
```

**Purpose**: This function creates an OpenRouterModelClient for one model specification and API key. It connects the model registry’s abstract model entry to a real network client.

**Data flow**: It receives a ModelSpec and an API key string. It builds an OpenAI SDK client pointed at OpenRouter’s base URL, then wraps that SDK client and the spec in an OpenRouterModelClient.

**Call relations**: Each ModelSpec created by _openrouter stores this function as its client factory. Later, when the system needs to call one of these models, the registry can use this factory to create the actual client.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_openrouter`  (lines 209–226)

```
def _openrouter(id: str, price: ModelPrice, cutoff: str, context_window: int=OPENROUTER_CONTEXT_WINDOW) -> ModelSpec
```

**Purpose**: This helper builds a complete ModelSpec for an OpenRouter model. A ModelSpec is the system’s description of a model: its ID, provider, price, context size, reasoning support, and API key location.

**Data flow**: It receives a model ID, price, knowledge cutoff date, and optionally a context window size. It combines those with OpenRouter defaults, such as the provider name, API surface, key slot, key environment variable, reasoning support, and client factory, then returns a ModelSpec.

**Call relations**: The module uses this helper to define OPENROUTER_MODEL_SPECS. Those specs are then included in manifest, which exposes the extension’s available models to the rest of the system.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 249–250)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the extension manifest, which is the package label the host system reads to discover what the extension provides. Here, it announces the OpenRouter extension name, version, and model list.

**Data flow**: It takes no input. It packages the fixed extension name, version, and predefined OpenRouter model specs into a Manifest object and returns it.

**Call relations**: The extension loader calls this during discovery. The returned Manifest lets the wider system register these OpenRouter models just like models supplied by built-in providers.

*Call graph*: 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-effective-configuration` — The chosen runtime settings that tell the service how this deployment should behave.
- `reg-model-catalog` — The shared list of available AI models, their limits, features, provider names, and calling rules.
- `reg-model-provider-adapters` — The shared provider clients that translate internal model requests into Anthropic, OpenAI, OpenRouter, or similar APIs.
- `reg-pricing-table` — The shared price list used to turn model and service usage into cost records.
- `reg-credential-store` — The encrypted store of API keys, service secrets, and owner-provided credentials.
- `reg-sandbox-session` — The saved or live sandbox workspace where an agent can run commands and keep files across tool calls.
- `reg-search-index` — The shared searchable index and chunk store built from synced or fetched content.
- `reg-artifact-storage` — The shared file and blob storage for generated artifacts, plus the signed download state used to protect them.
- `reg-accounting-ledger` — The shared usage ledger that records tokens, egress, sandbox usage, billing exports, and spend-limit checks.
- `reg-data-backend-catalog` — The resolved registry of non-model service backends such as search, indexing, memory, source, browser, sandbox, and blob providers made available by configuration and extensions.
- `reg-outbound-http-client-pool` — Shared outbound HTTP client/session pool state used for connection reuse, retries, and provider/API calls across model adapters, connectors, search, tools, and billing jobs.
- `reg-model-token-budget` — The per-turn model budget state derived from model context/output limits and spend policy, used while assembling prompts, truncating or summarizing context, and tracking remaining usage during model calls.
