# Indexing, embeddings, and recall  `stage-15.2`

This stage is shared behind-the-scenes support for memory and search. Its job is to turn saved text into pieces that can be found later, either by matching words or by matching meaning. “Embeddings” are the meaning fingerprints made from text, so similar ideas can be found even when the same words are not used.

The shared indexing code defines the common recipe for splitting long text into smaller chunks and preparing them for storage. The default index is the built-in option: it stores chunks locally with optional embeddings and searches them. The Turbopuffer extension does the same job through an outside search service.

The memory extension builds on these indexes. Its store records durable memories, searches them, and keeps indexed facts and synced pages up to date. The condenser acts like an editor, turning raw notes into cleaner facts, summaries, page sections, profiles, and curated pages. The runtime memory interface gives the rest of the system one simple way to search or browse recent memories, no matter how they are stored. The package file describes this extension’s role, and the events file defines the safe, limited recall event used before a reply.

## Files in this stage

### Search indexes
Local and hosted index implementations store chunks, embeddings, and keyword data while relying on the shared indexing contract.

### `extensions/index_default/ufo_ext_index_default.py`

`domain_logic` · `cross-cutting search and indexing operations`

This extension is the project’s default search engine for stored “chunks,” which are small pieces of text tied to an owner, a subject, and sometimes an embedding, meaning a list of numbers that represents the text’s meaning. Without this file, a deployment with no custom index backend would have nowhere standard to save searchable chunks or retrieve them later.

The file supports two database worlds. In PostgreSQL, it uses PostgreSQL’s native full-text search for word matching and pgvector for vector similarity. In SQLite, it uses FTS5, SQLite’s full-text search feature, and calculates vector similarity in Python by scanning rows. That makes SQLite useful for local or smaller setups, while PostgreSQL is the stronger production path.

The main class, DefaultIndex, opens a workspace-scoped database transaction for each operation. It can insert or update chunks, delete all chunks for an owner, check whether an owner already has chunks, prune old chunks after re-chunking, and run two kinds of search. Lexical search finds chunks sharing query words. Vector search finds chunks whose embeddings point in a similar direction. A small helper turns database rows into neutral Hit objects, so the rest of the system does not need to know whether PostgreSQL or SQLite was used underneath.

#### Function details

##### `pgvector_literal`  (lines 35–36)

```
def pgvector_literal(vector: tuple[float, ...]) -> str
```

**Purpose**: Turns a Python tuple of numbers into the bracketed text format expected by PostgreSQL’s pgvector extension. This is needed when saving or querying vector embeddings in PostgreSQL.

**Data flow**: It receives a tuple of floating-point numbers, converts each value to a plain float representation, joins them with commas, wraps them in square brackets, and returns that string. It does not change anything outside itself.

**Call relations**: DefaultIndex.upsert uses it before storing a chunk embedding in PostgreSQL. DefaultIndex.vector uses it before sending a query embedding to PostgreSQL for similarity search.

*Call graph*: called by 2 (upsert, vector).


##### `cosine`  (lines 39–47)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Measures how similar two embeddings are by calculating cosine similarity, which compares the direction of two number lists rather than their size. This is the SQLite fallback for vector search.

**Data flow**: It receives two equal-length tuples of numbers, computes each vector’s length, and divides their dot product by those lengths. If either vector has zero length, it returns 0.0 because there is no meaningful direction to compare.

**Call relations**: DefaultIndex.vector calls this after reading SQLite rows with stored embeddings. It is the local scoring step that replaces PostgreSQL’s pgvector comparison when SQLite is being used.

*Call graph*: called by 1 (vector); 1 external calls (sqrt).


##### `pack_embedding`  (lines 50–51)

```
def pack_embedding(vector: tuple[float, ...]) -> bytes
```

**Purpose**: Compresses an embedding into raw bytes so SQLite can store it in a database field. SQLite does not have the same vector type as PostgreSQL, so the numbers are packed manually.

**Data flow**: It receives a tuple of floating-point numbers and uses binary packing to turn them into a byte string. The returned bytes can be saved in SQLite and later unpacked back into numbers.

**Call relations**: DefaultIndex.upsert calls this when it is saving chunks through a SQLite connection. PostgreSQL does not use it because PostgreSQL gets embeddings as pgvector text literals instead.

*Call graph*: called by 1 (upsert); 1 external calls (pack).


##### `unpack_embedding`  (lines 54–55)

```
def unpack_embedding(blob: bytes) -> tuple[float, ...]
```

**Purpose**: Restores an embedding that was previously stored as raw bytes in SQLite. It reverses pack_embedding so the vector can be compared in Python.

**Data flow**: It receives a byte string from the database, treats every four bytes as one floating-point number, and returns the full tuple of numbers. It does not modify the database.

**Call relations**: DefaultIndex.vector calls this for each SQLite row before passing the restored embedding to cosine for scoring.

*Call graph*: called by 1 (vector); 1 external calls (unpack).


##### `_hit`  (lines 58–67)

```
def _hit(row: sa.RowMapping, score: float) -> Hit
```

**Purpose**: Builds a Hit object, which is the project’s neutral search-result shape, from a database row and a score. This keeps the rest of the system from depending on raw SQL row details.

**Data flow**: It receives a database row containing chunk fields plus a separate score, copies the chunk identity, owner, subject, order, and text into a Hit, converts the score to a float, and returns the Hit.

**Call relations**: DefaultIndex.lexical calls it to turn word-search rows into results. DefaultIndex.vector calls it to turn vector-search rows into the same result format.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `DefaultIndex.upsert`  (lines 177–214)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds new chunks to the index or replaces existing chunks with the same digest. This is how the searchable store stays current when content is created or reprocessed.

**Data flow**: It receives a tuple of Chunk objects. If the tuple is empty, it does nothing. Otherwise it opens a database transaction, checks whether the connection is PostgreSQL or SQLite, writes each chunk into the chunk table, and stores the embedding in the format that database expects. For SQLite, it also refreshes the full-text-search table entry for each chunk.

**Call relations**: This method is part of the DefaultIndex backend created by manifest. It calls pgvector_literal for PostgreSQL embeddings and pack_embedding for SQLite embeddings, then hands the prepared values to SQL statements that insert or update rows.

*Call graph*: calls 2 internal fn (pack_embedding, pgvector_literal).


##### `DefaultIndex.delete`  (lines 216–223)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Removes all indexed chunks for one owner. This is used when an owner’s content should no longer appear in search results.

**Data flow**: It receives an IndexScope, which identifies an owner by kind and id. It opens a transaction, deletes matching rows from PostgreSQL directly, or in SQLite deletes the matching full-text-search rows first and then the chunk rows.

**Call relations**: DefaultIndex.prune calls this when there is no keep-set, meaning every chunk in the scope should be removed. Other index users can also call it as the backend’s delete operation.

*Call graph*: called by 1 (prune).


##### `DefaultIndex.has_chunks`  (lines 225–228)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether the index already contains any chunks for a given owner. This lets callers avoid unnecessary work or decide whether indexing is needed.

**Data flow**: It receives an IndexScope, opens a transaction, asks the database for one matching chunk row, and returns true if a row exists or false if none is found.

**Call relations**: No internal caller is shown in this file’s call facts. It is provided as part of the DefaultIndex backend so higher-level indexing code can ask whether an owner has already been indexed.


##### `DefaultIndex.prune`  (lines 230–244)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Deletes outdated chunks for one owner while keeping a supplied set of current chunk digests. This matters after re-chunking, because old chunk rows would otherwise remain searchable as stale results.

**Data flow**: It receives an IndexScope and a frozen set of chunk digests to keep. If the keep-set is empty, it delegates to delete and removes the whole scope. Otherwise it opens a transaction and deletes every matching chunk whose digest is not in the keep-set, also cleaning SQLite’s full-text-search table when needed.

**Call relations**: It calls DefaultIndex.delete for the all-gone case. In the normal partial-cleanup case, it talks directly to the database using PostgreSQL- or SQLite-specific prune statements.

*Call graph*: calls 1 internal fn (delete).


##### `DefaultIndex.lexical`  (lines 246–282)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches chunks by matching words in a text query. It returns chunks whose stored text shares terms with the query, ranked by the database’s text-search score.

**Data flow**: It receives a query string, a set of subjects to search within, an owner kind, and a result limit. If there are no subjects, or the query has no usable words, it returns an empty tuple. Otherwise it opens a transaction, runs PostgreSQL full-text search or SQLite FTS5 search, converts each matching row into a Hit, and returns the tuple of hits.

**Call relations**: This is the word-search path of the DefaultIndex backend. It calls _hit to package database rows into standard Hit objects before handing results back to the caller.

*Call graph*: calls 1 internal fn (_hit).


##### `DefaultIndex.vector`  (lines 284–317)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches chunks by embedding similarity, which is a way to find text with a similar meaning rather than just the same words. It returns the closest matching chunks within the requested subjects and owner kind.

**Data flow**: It receives a query embedding, a set of subjects, an owner kind, and a result limit. If the embedding or subjects are empty, it returns no results. With PostgreSQL, it sends the embedding to pgvector and lets the database score rows. With SQLite, it loads candidate rows, unpacks each stored embedding, scores it with cosine similarity in Python, sorts the rows by score, and returns the top hits.

**Call relations**: It calls pgvector_literal on the PostgreSQL path. On the SQLite path, it calls unpack_embedding and cosine for each stored vector. Both paths call _hit at the end to return standard search-result objects.

*Call graph*: calls 4 internal fn (_hit, cosine, pgvector_literal, unpack_embedding).


##### `manifest`  (lines 320–330)

```
def manifest() -> Manifest
```

**Purpose**: Advertises this extension to the host system as an index backend named "default". This is how the project discovers and constructs DefaultIndex when no custom backend is chosen.

**Data flow**: It creates a Manifest containing the extension name, version, and one IndexBackendSpec. That spec says the backend is called "default" and provides a factory that builds DefaultIndex using the transaction opener supplied by the host context.

**Call relations**: The extension loading system calls this to learn what the file provides. The manifest then hands off a factory that creates DefaultIndex instances connected to the workspace-scoped transaction system.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/turbopuffer/ufo_ext_turbopuffer.py`

`io_transport` · `indexing and search request handling`

UFO needs a way to remember and find relevant text chunks. This file provides that by connecting UFO’s standard index interface to Turbopuffer’s HTTP API. Think of it as an adapter plug: UFO speaks in terms of chunks, owners, subjects, and hits; Turbopuffer expects namespaces, document ids, vectors, attributes, and query bodies.

When chunks are written, the file turns each chunk into Turbopuffer’s column-style upload format. The chunk text is stored for word-based search, and its embedding is stored for vector search, which means “find text with similar meaning.” Each workspace gets its own Turbopuffer namespace, so different workspaces do not share the same search space.

For searching, the file supports two paths. The lexical path uses BM25, a common word-ranking method, but first cleans and shortens the query so Turbopuffer will accept it and meaningless punctuation-only searches do not match everything. The vector path sends an embedding for approximate nearest-neighbor search, then converts Turbopuffer distances into UFO scores where higher means better.

It also deletes or prunes chunks for one owner by first listing the matching stored ids, then deleting them in batches. Authentication is done per request with a Turbopuffer API key read from UFO credentials.

#### Function details

##### `turbopuffer_id`  (lines 49–55)

```
def turbopuffer_id(chunk_digest: str) -> str
```

**Purpose**: This converts UFO’s chunk digest into a document id that Turbopuffer can store safely. SHA-256 digests are shortened with URL-safe base64 so they stay within Turbopuffer’s id limits; other ids are left alone.

**Data flow**: It receives a chunk digest string. If the string looks like a full `sha256:` digest, it removes the prefix, turns the hex bytes into base64 text, strips padding, and returns the shorter id. If it does not match that format, the original string comes back unchanged.

**Call relations**: The upload path uses this while building Turbopuffer documents, and delete/prune use it again when turning UFO chunk digests back into the ids Turbopuffer knows.

*Call graph*: called by 3 (delete, prune, upsert_body); 1 external calls (urlsafe_b64encode).


##### `chunk_digest_from_id`  (lines 58–67)

```
def chunk_digest_from_id(chunk_id: str) -> str
```

**Purpose**: This reverses the id shortening done for Turbopuffer, so search results can return UFO’s original chunk digest format. If an id does not look like one of the shortened SHA-256 ids, it is kept as-is.

**Data flow**: It receives a Turbopuffer document id. If the id has the expected shortened length, it tries to base64-decode it and rebuilds a `sha256:` hex digest. If decoding fails, or the length is different, it returns the input unchanged.

**Call relations**: Search-result conversion and scope listing call this after reading rows from Turbopuffer, so the rest of UFO sees normal chunk digests rather than service-specific ids.

*Call graph*: called by 2 (_scope_chunks, hit_from_row); 1 external calls (urlsafe_b64decode).


##### `upsert_body`  (lines 70–86)

```
def upsert_body(chunks: tuple[Chunk, ...]) -> dict[str, Any]
```

**Purpose**: This builds the JSON body used to insert or update chunks in Turbopuffer. It translates UFO chunk objects into the column-based layout Turbopuffer expects.

**Data flow**: It receives a tuple of chunks. It creates parallel lists for ids, vectors, owner fields, subject, order number, and text, then wraps them with Turbopuffer settings for cosine vector distance and full-text search on the text field. The output is a dictionary ready to send as JSON.

**Call relations**: The `TurbopufferIndex.upsert` method calls this for each write batch before sending the HTTP request. It relies on `turbopuffer_id` so stored document ids match the delete and prune paths.

*Call graph*: calls 1 internal fn (turbopuffer_id); called by 1 (upsert).


##### `bm25_query`  (lines 89–105)

```
def bm25_query(text: str) -> str
```

**Purpose**: This prepares a word-search query for Turbopuffer’s BM25 search. It removes terms too small to be useful and keeps the query within Turbopuffer’s byte limit.

**Data flow**: It receives free-form text from a user or model. It keeps only tokens that contain at least one run of two or more letters or digits, joins them into a query, and checks the encoded byte length. If the query is too long, it clips it and avoids ending in the middle of a term when possible. The output is a safe query string, or an empty string when there are no meaningful terms.

**Call relations**: The lexical search method calls this before making a Turbopuffer BM25 request. If this returns empty text, lexical search stops early and leaves relevance to the vector search path instead of asking Turbopuffer to match everything.

*Call graph*: called by 1 (lexical).


##### `query_filters`  (lines 108–112)

```
def query_filters(owner_kind: str, subjects: frozenset[str]) -> list[Any]
```

**Purpose**: This creates the filter used for normal searches. It limits results to one owner kind and to the allowed recall subjects.

**Data flow**: It receives an owner kind and a set of subjects. It sorts the subjects and returns a Turbopuffer filter expression meaning: owner kind must match, and subject must be one of these values.

**Call relations**: `TurbopufferIndex._query` uses this whenever lexical or vector search asks Turbopuffer for rows. It keeps search results inside the same scope rules UFO’s other index backends use.

*Call graph*: called by 1 (_query).


##### `scope_filters`  (lines 115–122)

```
def scope_filters(scope: IndexScope, after_id: str | None) -> list[Any]
```

**Purpose**: This creates the filter used when looking through all chunks for one stored owner. It is mainly used for checking, deleting, or pruning that owner’s indexed chunks.

**Data flow**: It receives an `IndexScope`, which names an owner kind and owner id, plus an optional id to start after. It returns a Turbopuffer filter expression for that owner, and adds an id-greater-than condition when paging through results.

**Call relations**: `has_chunks` uses this for a quick existence check. `_scope_chunks` uses it repeatedly while walking through a scope page by page.

*Call graph*: called by 2 (_scope_chunks, has_chunks).


##### `hit_from_row`  (lines 125–134)

```
def hit_from_row(row: dict[str, Any], score: float) -> Hit
```

**Purpose**: This turns a raw Turbopuffer result row into UFO’s `Hit` object. A hit is the standard shape UFO uses for a search result.

**Data flow**: It receives one row dictionary from Turbopuffer and a score chosen by the caller. It converts the stored id back into a chunk digest, copies owner and text fields, converts the ordinal to a number, attaches the score, and returns a `Hit`.

**Call relations**: Both lexical and vector search call this after `_query` returns rows. It is the final translation step from Turbopuffer’s response format back into UFO’s index format.

*Call graph*: calls 1 internal fn (chunk_digest_from_id); called by 2 (lexical, vector); 1 external calls (__init__).


##### `vector_score`  (lines 137–143)

```
def vector_score(row: dict[str, Any], position: int, total: int) -> float
```

**Purpose**: This gives a vector-search row a score where larger means more relevant. It hides Turbopuffer’s distance-style result, where smaller distance means closer.

**Data flow**: It receives a row, its position in the result list, and the total number of rows. If Turbopuffer supplied a `$dist` distance, it returns `1 - distance`. If no distance is present, it falls back to a descending score based on result order.

**Call relations**: `TurbopufferIndex.vector` calls this before turning rows into `Hit` objects. The score it returns is passed into `hit_from_row` and later used by UFO’s result-combining logic.

*Call graph*: called by 1 (vector).


##### `TurbopufferIndex._api`  (lines 160–181)

```
def _api(self) -> httpx.AsyncClient
```

**Purpose**: This provides the right reusable HTTP client for the currently running async event loop. An event loop is the scheduler that runs asynchronous tasks; sharing one client across different loops can break connection pooling.

**Data flow**: It checks which event loop is running now, removes records for loops that have closed, and looks for an existing `httpx.AsyncClient` for this loop. If none exists, it creates one with Turbopuffer’s base URL, timeout, and optional test transport. It returns the client.

**Call relations**: All methods that talk to Turbopuffer call this before sending HTTP requests. It sits underneath writes, deletes, existence checks, scope listing, and both search paths.

*Call graph*: called by 6 (_query, _scope_chunks, delete, has_chunks, prune, upsert); 2 external calls (get_running_loop, AsyncClient).


##### `TurbopufferIndex.upsert`  (lines 183–194)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This writes chunks into Turbopuffer, creating or replacing their stored documents. It is used when UFO indexes new or updated memory chunks.

**Data flow**: It receives a tuple of chunks, keeps only chunks that actually have embeddings, and returns immediately if there are none. It reads authorization headers, splits the chunks into batches, turns each batch into a Turbopuffer JSON body, posts it to the workspace namespace, and raises an error if Turbopuffer rejects the request.

**Call relations**: This is one of the main public index-backend operations. It uses `_auth` for the API key, `_path` for the namespace URL, `_api` for the HTTP client, and `upsert_body` for the request shape.

*Call graph*: calls 4 internal fn (_api, _auth, _path, upsert_body).


##### `TurbopufferIndex.delete`  (lines 196–204)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This removes every indexed chunk belonging to one owner scope. It is used when UFO no longer wants that owner’s chunks to be searchable.

**Data flow**: It receives an `IndexScope`. It gets authorization headers, lists all chunks currently stored for that scope, converts their digests into Turbopuffer ids, then sends batched delete requests. If any request fails, it raises an error.

**Call relations**: This public backend operation first delegates to `_scope_chunks` to discover what needs deleting. It then uses `turbopuffer_id`, `_path`, `_api`, and `_auth` to send the delete commands to Turbopuffer.

*Call graph*: calls 5 internal fn (_api, _auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.prune`  (lines 206–216)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This removes stale chunks for one owner while keeping a specified set. It is useful after re-chunking content, where some old chunks should disappear but current chunks should remain.

**Data flow**: It receives an `IndexScope` and a set of chunk digests to keep. It reads all chunks in that scope, selects only those not in the keep set, converts them to Turbopuffer ids, and sends batched delete requests. The kept chunks are left untouched.

**Call relations**: Like `delete`, this public operation uses `_scope_chunks` to list the stored scope. It then combines `turbopuffer_id`, `_path`, `_api`, and `_auth` to delete only the unwanted ids.

*Call graph*: calls 5 internal fn (_api, _auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.has_chunks`  (lines 218–226)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: This answers whether Turbopuffer currently has any chunks for a given owner scope. It is a quick existence check rather than a full export.

**Data flow**: It receives an `IndexScope`, builds a query that asks for just one id in that scope, and posts it to the namespace. If the namespace does not exist, it returns false. Otherwise it checks whether the response has any rows and returns true or false.

**Call relations**: This public backend operation uses `scope_filters` to limit the check, then `_path`, `_api`, and `_auth` to make the HTTP request.

*Call graph*: calls 4 internal fn (_api, _auth, _path, scope_filters).


##### `TurbopufferIndex.lexical`  (lines 228–237)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This searches indexed chunks by words using BM25, a ranking method that favors text containing important query terms. It returns UFO `Hit` results.

**Data flow**: It receives a query string, allowed subjects, an owner kind, and a limit. It cleans the query with `bm25_query`; if there is no useful text or no subjects, it returns an empty tuple. Otherwise it asks `_query` to rank by text BM25, then converts each returned row into a `Hit` with a rank-based score.

**Call relations**: This is the public word-search path. It prepares the query, delegates the HTTP search to `_query`, and uses `hit_from_row` to translate Turbopuffer rows into UFO search hits.

*Call graph*: calls 3 internal fn (_query, bm25_query, hit_from_row).


##### `TurbopufferIndex.vector`  (lines 239–248)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This searches indexed chunks by semantic similarity using an embedding. An embedding is a list of numbers that represents meaning, so this can find related text even when the words differ.

**Data flow**: It receives an embedding, allowed subjects, an owner kind, and a limit. If the embedding or subject set is empty, it returns no hits. Otherwise it asks `_query` to run an approximate nearest-neighbor vector search, scores each row with `vector_score`, drops non-positive scores, and returns `Hit` objects.

**Call relations**: This is the public meaning-based search path. It delegates the Turbopuffer request to `_query`, uses `vector_score` to normalize relevance, and uses `hit_from_row` to produce UFO results.

*Call graph*: calls 3 internal fn (_query, hit_from_row, vector_score).


##### `TurbopufferIndex._query`  (lines 250–265)

```
async def _query(self, rank_by: list[Any], owner_kind: str, subjects: frozenset[str], limit: int) -> list[dict[str, Any]]
```

**Purpose**: This is the shared helper for lexical and vector searches. It sends a scoped Turbopuffer query and returns the raw rows.

**Data flow**: It receives a Turbopuffer ranking instruction, owner kind, subjects, and result limit. It builds a request body with ranking, top-k limit, requested attributes, and filters. It posts that body to the namespace query endpoint with authorization. A missing namespace becomes an empty list; other errors are raised; successful responses return their rows as a list.

**Call relations**: `lexical` and `vector` both call this instead of duplicating HTTP request logic. It uses `query_filters`, `_path`, `_api`, and `_auth` to turn a UFO search request into a Turbopuffer request.

*Call graph*: calls 4 internal fn (_api, _auth, _path, query_filters); called by 2 (lexical, vector).


##### `TurbopufferIndex._scope_chunks`  (lines 267–295)

```
async def _scope_chunks(self, scope: IndexScope, headers: dict[str, str]) -> list[Chunk]
```

**Purpose**: This lists all stored chunks for one owner scope, page by page. It is used before deleting or pruning because Turbopuffer deletes by document id.

**Data flow**: It receives an `IndexScope` and already-built authorization headers. It repeatedly queries Turbopuffer for a page of rows sorted by id, converts each row into a lightweight `Chunk`, and remembers the last id so the next request starts after it. It stops when the namespace is missing or a page is smaller than the page size, then returns the collected chunks.

**Call relations**: `delete` and `prune` call this to discover which ids exist in Turbopuffer. It uses `scope_filters` for each page, `_path` and `_api` for the request, and `chunk_digest_from_id` to restore UFO digest values.

*Call graph*: calls 4 internal fn (_api, _path, chunk_digest_from_id, scope_filters); called by 2 (delete, prune); 1 external calls (__init__).


##### `TurbopufferIndex._auth`  (lines 297–299)

```
async def _auth(self) -> dict[str, str]
```

**Purpose**: This builds the HTTP authorization header Turbopuffer requires. It reads the API key from UFO’s credential storage at request time.

**Data flow**: It asks the credential reader for the `turbopuffer_api_key` value. It then returns a dictionary with an `Authorization` header in Bearer-token form.

**Call relations**: Most Turbopuffer requests call this shortly before sending HTTP. It supplies the headers used by upsert, delete, prune, existence checks, and shared query searches.

*Call graph*: called by 5 (_query, delete, has_chunks, prune, upsert).


##### `TurbopufferIndex._path`  (lines 301–302)

```
def _path(self, suffix: str='') -> str
```

**Purpose**: This builds the Turbopuffer namespace URL path for the current workspace. A namespace is Turbopuffer’s separate storage area, like a labeled drawer for one workspace’s index.

**Data flow**: It receives an optional suffix such as `/query`. It combines the fixed namespace prefix, the workspace id from credentials, and the suffix into a path string.

**Call relations**: Every method that sends a Turbopuffer request uses this to address the correct workspace namespace. Query helpers add `/query`; write and delete requests use the namespace path directly.

*Call graph*: called by 6 (_query, _scope_chunks, delete, has_chunks, prune, upsert).


##### `manifest`  (lines 305–322)

```
def manifest() -> Manifest
```

**Purpose**: This declares the extension to UFO so the system can discover and build the Turbopuffer index backend. Without this, setting the index backend to `turbopuffer` would not know what code or credentials to use.

**Data flow**: It creates a manifest containing the extension name and version, declares the needed credential slot for the Turbopuffer API key, and registers an index backend spec whose factory builds a `TurbopufferIndex` from the runtime context. The output is a `Manifest` object.

**Call relations**: UFO’s extension loading calls this during setup. The manifest it returns tells core configuration how to construct the backend and which credential must be available.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `core/src/ufo/runtime/indexing.py`

`domain_logic` · `cross-cutting indexing and retrieval`

Search works best when long documents are broken into smaller, meaningful pieces. This file is the project’s common “indexing seam”: it says what an index backend must be able to do, what an embedding service must provide, and what shape indexed chunks and search hits have. An index backend is the storage/search part supplied elsewhere; an embedding service turns text into number lists that represent meaning. This file does not talk to a database itself.

The main workflow is `chunk_embed_upsert`. It takes one body of text, splits it into chunks, asks an embedder to create a vector for each chunk, stores those chunks through the backend, and then removes any old chunks for the same owner that are no longer present. That last prune step matters after edits: without it, deleted or rewritten text could still appear in search results.

`TextChunker` is the text-splitting machine. It first tries natural breaks, like paragraphs, lines, sentences, and punctuation. If that is not enough, it falls back to whitespace or character-sized pieces. It adds some overlap between neighboring chunks, like repeating the last few lines of a recipe on the next page, so later search has enough context. It also caps chunk size by characters to avoid oversized entries.

#### Function details

##### `IndexBackend.upsert`  (lines 68–68)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This is part of the storage contract for an index backend. It means “store these chunks, or update them if they are already there.”

**Data flow**: It receives a group of `Chunk` objects, each with text, ownership details, and usually an embedding. A concrete backend writes or replaces those records in its search store. Nothing is returned; the lasting effect is that the index now contains those chunks.

**Call relations**: `chunk_embed_upsert` calls this after text has been split and embedded. The backend implementation does the actual saving, while this file only defines that such a saving step must exist.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.delete`  (lines 70–70)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This is the contract method for removing all indexed chunks that belong to one owner. Someone uses it when an indexed item should disappear completely.

**Data flow**: It receives an `IndexScope`, which identifies an owner by kind and id. A concrete backend uses that scope to delete matching chunks from storage. It returns nothing; the index is changed by removing those entries.

**Call relations**: The skill creation extension’s manifest indexing flow calls this when it needs to clear an indexed card. This protocol method lets that extension ask any compatible backend to perform the deletion without knowing how the backend stores data.

*Call graph*: called by 1 (_index_card).


##### `IndexBackend.prune`  (lines 72–72)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This is the contract method for deleting stale chunks while keeping the chunks that are still current. It is used after re-indexing edited text.

**Data flow**: It receives an owner scope and a set of chunk digests to keep. A concrete backend removes all chunks for that owner whose digest is not in the keep set. It returns nothing; the index is cleaned so old chunks do not remain searchable.

**Call relations**: `chunk_embed_upsert` calls this after upserting the newly generated chunks. Together, upsert and prune make re-indexing safe: new content is added, unchanged content stays, and old content is removed.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.has_chunks`  (lines 74–74)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: This contract method asks whether an owner already has indexed chunks. It is useful for deciding whether something needs indexing or has already been indexed.

**Data flow**: It receives an `IndexScope` naming the owner to check. A concrete backend looks in its storage and answers with `true` or `false`. It does not change the index.

**Call relations**: No caller is shown in the provided graph, but it belongs to the same backend contract as insert, delete, and search. Other parts of the system can use it as a quick existence check before doing more work.


##### `IndexBackend.lexical`  (lines 76–78)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This contract method performs text-based search, sometimes called lexical search. In plain terms, it looks for chunks whose words match the query words.

**Data flow**: It receives a query string, a set of allowed subjects, an owner kind, and a maximum number of results. A concrete backend searches its stored text and returns matching `Hit` objects with scores. It does not change stored data.

**Call relations**: No caller is shown in the provided graph, but this is one half of the retrieval contract. It lets the rest of the system ask for word-match results without caring which search engine or database is underneath.


##### `IndexBackend.vector`  (lines 80–82)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This contract method performs meaning-based search using an embedding, which is a list of numbers representing the meaning of some text. It finds chunks whose embeddings are close to the input embedding.

**Data flow**: It receives an embedding vector, allowed subjects, an owner kind, and a result limit. A concrete backend compares that vector against stored chunk vectors and returns the best matching `Hit` objects. It reads from the index but does not modify it.

**Call relations**: The queue’s shadow skill selection flow calls this when it wants to find relevant indexed material by meaning. The actual nearest-match search happens in the backend implementation.

*Call graph*: called by 1 (_shadow_skill_selection).


##### `EmbedClient.embed`  (lines 86–86)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This is the contract for an embedding service. It turns pieces of text into numeric vectors that can be used for meaning-based search.

**Data flow**: It receives a tuple of text strings. A concrete embedder sends them to a model or local embedding system and returns one vector per input text, preserving the same order. It does not store anything by itself.

**Call relations**: `chunk_embed_upsert` calls this while building the index, and the queue’s shadow skill selection flow calls it when preparing a search query. This keeps the rest of the code independent from any particular embedding provider.

*Call graph*: called by 2 (chunk_embed_upsert, _shadow_skill_selection).


##### `chunk_embed_upsert`  (lines 89–114)

```
async def chunk_embed_upsert(index: IndexBackend, embed: EmbedClient, chunker: 'TextChunker', owner_kind: str, owner_id: str, subject: str, body: str) -> None
```

**Purpose**: This is the shared indexing workflow for one text body. It splits the text, embeds the chunks, stores them, and removes old chunks that no longer match the current text.

**Data flow**: It receives an index backend, an embedder, a chunker, owner details, a subject, and the body text. First it asks the chunker for chunks. If there are chunks, it embeds their text and copies each chunk with its matching embedding attached, then upserts them into the backend. Finally it asks the backend to prune everything for that owner except the digests just produced. It returns nothing, but the index is updated to match the current body.

**Call relations**: Indexers call this as the common derivation step from source text to searchable chunks. Inside, it relies on `TextChunker.chunk` for splitting, `EmbedClient.embed` for vectors, `IndexBackend.upsert` for saving, and `IndexBackend.prune` for cleanup.

*Call graph*: calls 3 internal fn (embed, prune, upsert); 2 external calls (__init__, replace).


##### `TextChunker.chunk`  (lines 123–134)

```
def chunk(self, text: str, owner_kind: str, owner_id: str, subject: str) -> tuple[Chunk, ...]
```

**Purpose**: This public method turns one text body into a sequence of `Chunk` records ready to be embedded. Each chunk includes ownership information so search results can be traced back to the original item.

**Data flow**: It receives raw text plus owner kind, owner id, and subject. It asks `_slices` to split the text into plain text pieces. For each piece, it assigns an ordinal number, computes a stable digest, and creates a `Chunk` without an embedding yet. It returns all chunks as a tuple.

**Call relations**: `chunk_embed_upsert` uses this at the start of indexing. This method coordinates the private splitting helpers and `_digest`, then hands structured chunks back to the embedding-and-storage workflow.

*Call graph*: calls 2 internal fn (_digest, _slices); 1 external calls (__init__).


##### `TextChunker._slices`  (lines 136–144)

```
def _slices(self, text: str) -> list[str]
```

**Purpose**: This method decides the overall splitting plan for a piece of text. It produces clean text slices that are near the target size and not too large.

**Data flow**: It receives raw text. Empty or whitespace-only text becomes an empty list. Short text is simply trimmed and character-capped. Longer text is recursively split at natural boundaries, merged back into useful-sized chunks, given overlap, and finally capped by character length. It returns a list of text slices.

**Call relations**: `TextChunker.chunk` calls this before creating `Chunk` objects. It is the central coordinator for `_count_words`, `_recursive_split`, `_greedy_merge`, `_apply_overlap`, and `_cap_by_chars`.

*Call graph*: calls 5 internal fn (_apply_overlap, _cap_by_chars, _count_words, _greedy_merge, _recursive_split); called by 1 (chunk).


##### `TextChunker._count_words`  (lines 147–153)

```
def _count_words(text: str) -> int
```

**Purpose**: This helper estimates how large a text is in word-like units. It has special behavior for Chinese, Japanese, and Korean text, where words are often not separated by spaces.

**Data flow**: It receives a text string. It removes whitespace to count non-space characters, then checks how much of the text is CJK writing. If the CJK share is high, it treats characters as the size measure; otherwise it counts runs of non-whitespace text like ordinary words. It returns an integer size estimate.

**Call relations**: `_slices`, `_recursive_split`, and `_greedy_merge` call this whenever they need to decide whether a piece is too big or can be combined. It is the chunker’s measuring tape.

*Call graph*: called by 3 (_greedy_merge, _recursive_split, _slices); 1 external calls (sub).


##### `TextChunker._cap_by_chars`  (lines 155–167)

```
def _cap_by_chars(self, text: str) -> list[str]
```

**Purpose**: This helper enforces an absolute character limit on chunks. It is a safety net for very long text pieces that are still too large after word-based splitting.

**Data flow**: It receives one text string. If it is already within the maximum character length, it returns that text as a one-item list, unless it is empty. If it is too long, it cuts it into overlapping character windows, trims each piece, and returns the non-empty pieces.

**Call relations**: `_slices` calls this for short text and again after overlap has been applied to longer text. It prevents oversized chunks from being sent to embedding or storage.

*Call graph*: called by 1 (_slices).


##### `TextChunker._recursive_split`  (lines 169–181)

```
def _recursive_split(self, text: str, level: int) -> list[str]
```

**Purpose**: This helper breaks long text into smaller pieces by trying increasingly fine separators. It prefers human-friendly breaks, such as paragraphs and sentences, before falling back to rougher cuts.

**Data flow**: It receives text and a delimiter level. At each level, it tries the delimiters for that level. If they do not split the text, it moves to the next level. If a resulting piece is still too large, it recursively splits that piece further. If no delimiter level remains, it splits on whitespace. It returns a list of smaller text pieces.

**Call relations**: `_slices` calls this when text is too large for one chunk. It uses `_split_at_delimiters`, `_count_words`, and `_split_on_whitespace` to move from natural divisions to fallback divisions.

*Call graph*: calls 3 internal fn (_count_words, _split_at_delimiters, _split_on_whitespace); called by 1 (_slices).


##### `TextChunker._split_at_delimiters`  (lines 184–197)

```
def _split_at_delimiters(text: str, delimiters: tuple[str, ...]) -> list[str]
```

**Purpose**: This helper cuts text at the earliest matching delimiter from a given set. It keeps the delimiter with the preceding piece, so punctuation and line breaks stay with the text they belong to.

**Data flow**: It receives text and a tuple of delimiters. It repeatedly searches the remaining text for the next earliest delimiter, cuts there, and continues with the rest. Empty-looking pieces are removed. It returns a list of non-empty pieces.

**Call relations**: `_recursive_split` calls this at each delimiter level. It supplies the raw pieces that `_recursive_split` then checks for size and may split further.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._split_on_whitespace`  (lines 199–215)

```
def _split_on_whitespace(self, text: str) -> list[str]
```

**Purpose**: This is the fallback splitter when natural punctuation or paragraph breaks are not enough. It divides text into groups of words, or into character-sized pieces when words cannot be found.

**Data flow**: It receives text. If normal word runs are found, it groups them by the target word count and joins each group back into a string. If there are no usable words, or one extremely long run, it slices the raw text by a target-sized character count. It returns non-empty pieces.

**Call relations**: `_recursive_split` calls this at the final delimiter level. It ensures the chunker can still make progress even on unusual text with no spaces or punctuation.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._greedy_merge`  (lines 217–231)

```
def _greedy_merge(self, pieces: list[str]) -> list[str]
```

**Purpose**: This helper combines small neighboring pieces into larger, more useful chunks. It avoids producing many tiny fragments when natural splitting created pieces that are too small.

**Data flow**: It receives a list of text pieces. Starting with the first piece, it tries to append each next piece. If the combined text stays within about one and a half times the target word count, it keeps merging; otherwise it saves the current chunk and starts a new one. It returns the merged list.

**Call relations**: `_slices` calls this after recursive splitting. It uses `_count_words` to decide whether a merge is still a reasonable size before overlap and character caps are applied.

*Call graph*: calls 1 internal fn (_count_words); called by 1 (_slices); 1 external calls (ceil).


##### `TextChunker._apply_overlap`  (lines 233–239)

```
def _apply_overlap(self, chunks: list[str]) -> list[str]
```

**Purpose**: This helper adds a little context from the previous chunk to the start of each later chunk. The point is to keep ideas from being cut too sharply at chunk boundaries.

**Data flow**: It receives a list of chunks as strings. If there is only one chunk, or overlap is disabled, it returns the list unchanged. Otherwise, it keeps the first chunk as-is and prefixes every later chunk with trailing context taken from the previous one. It returns the overlapped chunk list.

**Call relations**: `_slices` calls this after merging. It uses `_trailing_context` to choose the repeated text and `itertools.pairwise` to walk through previous/current chunk pairs.

*Call graph*: calls 1 internal fn (_trailing_context); called by 1 (_slices); 1 external calls (pairwise).


##### `TextChunker._trailing_context`  (lines 241–251)

```
def _trailing_context(self, text: str) -> str
```

**Purpose**: This helper chooses the bit of text to repeat at the start of the next chunk. It tries to make that repeated context start at a sensible sentence point when possible.

**Data flow**: It receives one chunk of text. It finds its word runs and, if there are more words than the overlap size, takes the last overlap-sized group. If there is a sentence boundary early enough inside that trailing text, it starts after that boundary so the overlap is cleaner. It returns the context string, or an empty string when overlap would simply repeat the whole chunk.

**Call relations**: `_apply_overlap` calls this for each previous chunk when building overlapped chunks. It keeps chunk boundaries more readable and useful for later search.

*Call graph*: called by 1 (_apply_overlap).


##### `TextChunker._digest`  (lines 254–256)

```
def _digest(owner_kind: str, owner_id: str, subject: str, ordinal: int, text: str) -> str
```

**Purpose**: This helper creates a stable unique id for a chunk. The id changes if the owner, subject, position, or text changes.

**Data flow**: It receives owner kind, owner id, subject, ordinal number, and chunk text. It joins those values with a separator that is unlikely to appear naturally, hashes the result with SHA-256, and prefixes it with `sha256:`. It returns the digest string.

**Call relations**: `TextChunker.chunk` calls this once for every text slice. `chunk_embed_upsert` later uses these digests to upsert current chunks and prune old ones reliably.

*Call graph*: called by 1 (chunk); 1 external calls (sha256).


### Memory storage and consolidation
The memory extension cleans raw records into useful knowledge, stores and indexes them, and exposes a common runtime search shape.

### `extensions/memory/ufo_ext_memory/condenser.py`

`domain_logic` · `page-change handling and periodic background maintenance`

The memory system receives many small pieces of information from synced pages, tools, and user activity. Left alone, those pieces would pile up like an unedited notebook: old versions would remain, repeated statements would crowd out better ones, and long pages would have no plain summary. This file supplies the workers that keep that notebook readable.

It has several jobs. FactDeriver reads changed source pages and asks a model to extract concrete facts from them. MemoryConsolidator groups older related facts and asks a model to summarize them into one broader memory. MemoryDeduper finds near-duplicate tool-written memories and marks older copies as replaced by the newest one. SectionWriter and OverviewWriter write short paragraphs that sit above wiki-like memory rows, so a reader sees the meaning of a section or whole workspace at a glance. ProfileWriter creates a People view: each member’s role and current focus. PagePass is the cautious final editor: it reads a whole subject page and retires page-derived rows that duplicate other rows.

A recurring theme is safety. Model calls happen before database write transactions, so locks are not held while waiting. Payloads are bounded, so a huge workspace cannot create unlimited model costs. When rows are replaced, the old rows are not deleted; they are marked as superseded or retired, like crossing out an index card while keeping its audit trail.

#### Function details

##### `section_headings`  (lines 176–182)

```
def section_headings(subject: str) -> dict[MemoryKind, str]
```

**Purpose**: Chooses the human-readable section titles for a memory page. Workspace-wide pages use team wording, while personal pages use wording addressed to the individual.

**Data flow**: It receives a subject string, checks whether that subject is the shared workspace subject, and returns the matching dictionary of memory kinds to headings.

**Call relations**: SectionWriter uses it when deciding which bands can be summarized and when telling the model what heading a paragraph will sit under. PagePass also uses it when building the full page that the model will curate.

*Call graph*: called by 3 (_page, _sections, _summarize); 1 external calls (subject_shared).


##### `live_page_link`  (lines 244–257)

```
def live_page_link() -> ColumnElement[bool]
```

**Purpose**: Builds the database condition that says a page-derived memory row still belongs to the current version of its source page. This prevents summaries and curation from using facts from old page revisions.

**Data flow**: It reads no rows itself. It returns a SQL condition that matches a memory item to a page mirror only when page id, workspace, subject, and revision all line up.

**Call relations**: member_servable builds on this condition for broad read safety. PagePass uses it directly because it only wants rows still backed by live source pages.

*Call graph*: called by 2 (_page, member_servable); 1 external calls (and_).


##### `member_servable`  (lines 260–272)

```
def member_servable() -> ColumnElement[bool]
```

**Purpose**: Builds the database condition for rows a member could actually be shown. Tool-written rows always count, while page-derived rows count only if their source page revision is still current.

**Data flow**: It returns a SQL condition combining two cases: rows with no source page, or rows whose source page still matches through live_page_link.

**Call relations**: SectionWriter, OverviewWriter, and ProfileWriter use this before summarizing facts, so the prose they write is based on the same rows users can read.

*Call graph*: calls 1 internal fn (live_page_link); called by 4 (_facts, _facts, _facts, _sections); 2 external calls (or_, select).


##### `ExtractedFact.within_row_budget`  (lines 312–313)

```
def within_row_budget(cls, body: str) -> str
```

**Purpose**: Keeps a model-extracted fact short enough to fit in one memory row. It cuts long text at a word boundary instead of leaving a broken word.

**Data flow**: It receives the fact body text, trims it with the shared word-safe clipping helper, and returns the clipped text for validation.

**Call relations**: This runs automatically when ExtractedFact validates model output inside FactDeriver._extract, before any extracted fact can be committed to the store.

*Call graph*: 1 external calls (clip_to_word).


##### `FactDeriver.apply`  (lines 349–364)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Processes a delivered batch of changed source pages and turns eligible pages into stored facts. It also retires facts from pages that disappeared or are known machine-status streams.

**Data flow**: It receives page changes, asks the store which pages are still live, immediately supersedes facts for removed or skipped streams, filters usable pages, batches them, and asks _derive to create replacement facts. For pages where replacements land, it retires older page facts.

**Call relations**: This is the public entry for the page-change consumer. It hands each eligible batch to _derive, which does the model extraction and commits the new facts.

*Call graph*: calls 1 internal fn (_derive); 1 external calls (batched).


##### `FactDeriver._derive`  (lines 366–405)

```
async def _derive(self, pages: tuple[PageChange, ...]) -> dict[UUID, frozenset[UUID]]
```

**Purpose**: Safely commits facts extracted from one small group of source pages. It double-checks that each page is still at the same revision before and during the write.

**Data flow**: It receives page changes, rereads current page state, drops pages that changed meanwhile, asks _extract for facts, then commits each valid fact as a MemoryWrite. It returns the ids of rows that were successfully landed for each page.

**Call relations**: FactDeriver.apply calls this for each batch. It calls _extract for the model pass and then uses the MemoryStore to write the accepted facts.

*Call graph*: calls 1 internal fn (_extract); called by 1 (apply); 1 external calls (__init__).


##### `FactDeriver._extract`  (lines 407–484)

```
async def _extract(self, pages: tuple[PageChange, ...]) -> tuple[ExtractedFact, ...]
```

**Purpose**: Asks the model to read a bounded set of source pages and record concrete facts through a structured tool call. It filters bad model entries and collapses repeated facts from the same page.

**Data flow**: It receives page changes, builds a compact JSON payload with page ids, titles, streams, and clipped bodies, sends a forced tool request to the model, validates each returned fact, removes restatements, and returns accepted ExtractedFact objects.

**Call relations**: _derive calls this before any database write. It uses _restates to avoid storing two model entries that say the same thing.

*Call graph*: calls 1 internal fn (_restates); called by 1 (_derive); 4 external calls (__init__, __init__, __init__, dumps).


##### `_content_words`  (lines 487–488)

```
def _content_words(body: str) -> frozenset[str]
```

**Purpose**: Extracts the meaningful words from a sentence-like memory body. Common filler words such as “the” and “and” are removed so similarity checks focus on content.

**Data flow**: It receives text, splits it into lowercase words, removes empty words and filler words, and returns a set of remaining words.

**Call relations**: _restates uses this helper to compare two extracted facts by their important words.

*Call graph*: called by 1 (_restates); 1 external calls (split).


##### `_restates`  (lines 491–503)

```
def _restates(kept: str, candidate: str) -> bool
```

**Purpose**: Decides whether two extracted fact texts are really the same claim in slightly different words. This keeps extraction from storing a short version and a longer version of one claim.

**Data flow**: It receives two fact bodies, converts each to content words, ignores very short comparisons, and checks whether most of the shorter fact’s content appears in the other.

**Call relations**: FactDeriver._extract calls this while reading model output. If a new fact restates an existing one, the longer version is kept.

*Call graph*: calls 1 internal fn (_content_words); called by 1 (_extract).


##### `cosine`  (lines 506–514)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Measures how close two embedding vectors are. An embedding is a list of numbers representing meaning, and cosine similarity tells whether two texts point in a similar semantic direction.

**Data flow**: It receives two numeric vectors, computes their dot product divided by their magnitudes, and returns a similarity score. If either vector has no magnitude, it returns 0.0.

**Call relations**: MemoryConsolidator._clusters uses this to group related facts. MemoryDeduper._clusters uses it to find near-duplicate copies.

*Call graph*: called by 2 (_clusters, _clusters); 2 external calls (sqrt, sumprod).


##### `MemoryConsolidator.run`  (lines 544–553)

```
async def run(self) -> None
```

**Purpose**: Runs the periodic job that turns clusters of old, related user/tool-written facts into one semantic summary. It skips entirely if no model is configured.

**Data flow**: It reads aged facts, groups them by subject, embeds each group, clusters the embeddings in a worker thread, and consolidates clusters large enough to be worth summarizing.

**Call relations**: This is the coordinator for MemoryConsolidator. It calls _aged_facts, _buckets, _embed, _clusters, and _consolidate in order.

*Call graph*: calls 4 internal fn (_aged_facts, _buckets, _consolidate, _embed); 1 external calls (to_thread).


##### `MemoryConsolidator._aged_facts`  (lines 555–586)

```
async def _aged_facts(self) -> tuple[_AgedFact, ...]
```

**Purpose**: Finds old live facts that are eligible to be merged into higher-level summaries. It deliberately ignores page-derived facts and already superseded or retired rows.

**Data flow**: It opens a transaction, queries the memory table for old live fact rows in this workspace, limits the scan, and returns lightweight _AgedFact records.

**Call relations**: MemoryConsolidator.run calls this first to decide what material exists for consolidation.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryConsolidator._buckets`  (lines 588–597)

```
def _buckets(self, facts: tuple[_AgedFact, ...]) -> tuple[tuple[str, tuple[_AgedFact, ...]], ...]
```

**Purpose**: Groups consolidation candidates by subject, so facts about different people or pages are not summarized together.

**Data flow**: It receives aged facts, collects them into subject groups, sorts each group newest first, trims each group to a maximum size, and returns ordered buckets.

**Call relations**: MemoryConsolidator.run uses these buckets before embedding and clustering each subject’s facts.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._embed`  (lines 599–603)

```
async def _embed(self, facts: tuple[_AgedFact, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: Turns each candidate fact into an embedding vector for similarity comparison.

**Data flow**: It receives aged facts, sends clipped fact bodies to the embedding service, and returns a map from fact id to vector.

**Call relations**: MemoryConsolidator.run calls this before sending the facts and vectors to _clusters.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._clusters`  (lines 605–626)

```
def _clusters(self, facts: tuple[_AgedFact, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_AgedFact, ...], ...]
```

**Purpose**: Groups related facts by meaning using their embeddings. It uses a simple newest-first greedy method, like placing each card onto the first matching pile.

**Data flow**: It receives facts and their embedding vectors, compares each fact to the head of existing clusters with cosine, and returns clusters of similar facts.

**Call relations**: MemoryConsolidator.run runs this in a worker thread so CPU-heavy comparisons do not block the async event loop.

*Call graph*: calls 1 internal fn (cosine).


##### `MemoryConsolidator._consolidate`  (lines 628–692)

```
async def _consolidate(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> None
```

**Purpose**: Writes one semantic summary for a cluster and marks the original facts as superseded by that summary. It verifies the original rows did not change while the model was summarizing.

**Data flow**: It receives a model and a fact cluster, asks _summarize for a paragraph, creates a new summary id, locks and rereads the donor rows, inserts the summary, and updates the donors to point at it.

**Call relations**: MemoryConsolidator.run calls this for each cluster large enough to merge. It calls _summarize before opening the write transaction.

*Call graph*: calls 1 internal fn (_summarize); called by 1 (run); 4 external calls (insert, select, update, uuid4).


##### `MemoryConsolidator._summarize`  (lines 694–704)

```
async def _summarize(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> str
```

**Purpose**: Asks the model to turn a cluster of related facts into one short summary paragraph.

**Data flow**: It receives a model and fact cluster, sends clipped fact bodies in a JSON payload, gets model text back, trims it to the overview-style paragraph budget, and returns the summary.

**Call relations**: _consolidate calls this before inserting the semantic memory row.

*Call graph*: calls 2 internal fn (complete, _to_overview_budget); called by 1 (_consolidate); 3 external calls (__init__, __init__, dumps).


##### `_to_overview_budget`  (lines 707–719)

```
def _to_overview_budget(summary: str) -> str
```

**Purpose**: Cuts a generated paragraph down to the allowed size while trying to preserve whole sentences. This keeps summaries readable and within storage limits.

**Data flow**: It receives summary text, keeps sentences until the word, sentence, or character budget would be exceeded, and falls back to word-safe clipping if even the first sentence is too long.

**Call relations**: MemoryConsolidator, SectionWriter, and OverviewWriter all use this after model output and before storing generated prose.

*Call graph*: called by 3 (_summarize, _write, _summarize); 1 external calls (clip_to_word).


##### `_recency`  (lines 722–723)

```
def _recency(fact: _AgedFact) -> tuple[datetime, UUID]
```

**Purpose**: Provides a stable sorting key for facts based on when they were created, with id as a tie-breaker.

**Data flow**: It receives an aged fact and returns a pair of created time and id.

**Call relations**: MemoryConsolidator uses this key when ordering facts inside buckets and clusters.


##### `_Group.key`  (lines 743–744)

```
def key(self) -> tuple[str, str]
```

**Purpose**: Gives the deduper a stable identity for a duplicate-search group: subject plus item class.

**Data flow**: It reads the group’s subject and item_class fields and returns them as a tuple.

**Call relations**: MemoryDeduper.run uses this key to walk through groups in order and remember where the last tick stopped.


##### `_Group.fingerprint`  (lines 747–748)

```
def fingerprint(self) -> list[JsonValue]
```

**Purpose**: Summarizes whether a dedupe group has changed since the last sweep. It uses the number of live copies and the latest update time.

**Data flow**: It reads the group’s copy count and latest timestamp and returns a small JSON-friendly list.

**Call relations**: MemoryDeduper.run compares this value with stored state to skip groups that have not changed.


##### `MemoryDeduper.run`  (lines 792–804)

```
async def run(self) -> None
```

**Purpose**: Runs one step of the periodic duplicate cleanup job. It examines one group per tick, so work is spread over time.

**Data flow**: It reads duplicate groups, loads the saved cursor, picks the next group after that cursor, stores the new cursor, checks the group fingerprint, and deduplicates only if the group changed.

**Call relations**: This is the coordinator for MemoryDeduper. It calls _groups, _cursor, and _dedup_group, while using ScopedStore keys as its memory between ticks.

*Call graph*: calls 3 internal fn (_cursor, _dedup_group, _groups).


##### `MemoryDeduper._cursor`  (lines 806–818)

```
def _cursor(self, stored: JsonValue | None) -> tuple[str, ...]
```

**Purpose**: Reads and validates the saved place in the dedupe group walk. It treats malformed stored data as an error instead of silently restarting.

**Data flow**: It receives a stored JSON value, returns an empty tuple for no cursor, returns the subject/item_class tuple for a valid cursor, or raises if the shape is wrong.

**Call relations**: MemoryDeduper.run calls this before choosing which group to sweep next.

*Call graph*: called by 1 (run).


##### `MemoryDeduper._groups`  (lines 820–846)

```
async def _groups(self) -> tuple[_Group, ...]
```

**Purpose**: Finds groups that contain at least two old enough live tool-written rows and might have duplicates.

**Data flow**: It queries memory items by workspace, subject, and item class, excluding page-derived, section, superseded, retired, and too-new rows. It returns _Group objects with copy counts and latest update times.

**Call relations**: MemoryDeduper.run uses this list to choose one group for the current tick.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryDeduper._dedup_group`  (lines 848–853)

```
async def _dedup_group(self, group: _Group) -> None
```

**Purpose**: Deduplicates one selected group by embedding all its live copies, clustering near matches, and collapsing each duplicate cluster.

**Data flow**: It reads the group’s live copies, embeds their bodies, clusters them in a worker thread, and sends clusters with enough copies to _collapse.

**Call relations**: MemoryDeduper.run calls this after cursor and fingerprint checks say the group is due.

*Call graph*: calls 3 internal fn (_collapse, _embed, _live_copies); called by 1 (run); 1 external calls (to_thread).


##### `MemoryDeduper._live_copies`  (lines 855–870)

```
async def _live_copies(self, group: _Group) -> tuple[_LiveCopy, ...]
```

**Purpose**: Loads the live rows in one dedupe group, newest first. Newest first matters because the newest row becomes the keeper.

**Data flow**: It receives a _Group, queries rows matching _live_group conditions, orders them by creation time and id descending, limits the result, and returns _LiveCopy records.

**Call relations**: _dedup_group calls this before embedding and clustering the group.

*Call graph*: calls 1 internal fn (_live_group); called by 1 (_dedup_group); 2 external calls (__init__, select).


##### `MemoryDeduper._embed`  (lines 872–879)

```
async def _embed(self, copies: tuple[_LiveCopy, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: Creates embedding vectors for the duplicate candidates in manageable batches.

**Data flow**: It receives live copies, sends clipped bodies to the embedding service in fixed-size batches, and returns a map from row id to vector.

**Call relations**: _dedup_group calls this before _clusters compares the copies by meaning.

*Call graph*: called by 1 (_dedup_group); 1 external calls (batched).


##### `MemoryDeduper._clusters`  (lines 881–910)

```
def _clusters(self, copies: tuple[_LiveCopy, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_LiveCopy, ...], ...]
```

**Purpose**: Groups near-verbatim duplicate rows by embedding similarity. It is stricter than general consolidation because it should collapse restatements, not merely related ideas.

**Data flow**: It receives newest-first copies and embeddings, compares each copy to existing cluster heads with cosine, and returns clusters where the first row is the intended keeper.

**Call relations**: _dedup_group runs this in a worker thread and then asks _collapse to stamp duplicate donors onto their cluster head.

*Call graph*: calls 1 internal fn (cosine).


##### `MemoryDeduper._collapse`  (lines 912–940)

```
async def _collapse(self, group: _Group, cluster: tuple[_LiveCopy, ...]) -> None
```

**Purpose**: Marks all duplicate rows in a cluster, except the newest head, as superseded by that head. It locks and verifies rows first to avoid pointing at a row that changed meanwhile.

**Data flow**: It receives a group and cluster, separates the head from donors, locks matching live rows, checks their bodies match what was embedded, updates donors to superseded_by=head.id, and raises if the locked set changes unexpectedly.

**Call relations**: _dedup_group calls this for each duplicate cluster. It relies on _live_group for the exact database safety conditions.

*Call graph*: calls 1 internal fn (_live_group); called by 1 (_dedup_group); 2 external calls (select, update).


##### `MemoryDeduper._live_group`  (lines 942–951)

```
def _live_group(self, group: _Group) -> tuple[ColumnElement[bool], ...]
```

**Purpose**: Builds the shared database conditions for rows that are eligible within one dedupe group.

**Data flow**: It receives a _Group and returns SQL conditions for workspace, subject, item class, no source page, not superseded, not retired, and older than the dedupe age floor.

**Call relations**: _live_copies uses it to read candidates, and _collapse uses it again to make sure it only updates rows that are still eligible.

*Call graph*: called by 2 (_collapse, _live_copies); 1 external calls (now).


##### `_rewrite_in_place`  (lines 972–1022)

```
async def _rewrite_in_place(connection: AsyncConnection, workspace_id: UUID, standing: _Standing, paragraph: str, confidence: int) -> None
```

**Purpose**: Writes a new standing paragraph, such as a section summary or overview, and supersedes the old paragraph in the same location. This enforces “one live paragraph here.”

**Data flow**: It receives a database connection, workspace id, standing location, paragraph text, and confidence. It locks existing live paragraphs for that location, inserts the new paragraph, and updates the old ones to point to it.

**Call relations**: SectionWriter.run and OverviewWriter.run call this after their model-written paragraph is ready.

*Call graph*: called by 2 (run, run); 5 external calls (execute, insert, select, update, uuid4).


##### `_retire_standing`  (lines 1025–1053)

```
async def _retire_standing(connection: AsyncConnection, workspace_id: UUID, standing: _Standing) -> None
```

**Purpose**: Removes a standing paragraph when its page or section no longer has enough live facts to justify it. This prevents stale summaries from sitting above rows that no longer support them.

**Data flow**: It receives a connection, workspace id, and standing location, then marks matching live paragraphs retired and clears indexing fields so they leave recall search.

**Call relations**: SectionWriter.run calls this for section paragraphs that no longer qualify. OverviewWriter.run calls it when the shared workspace overview falls below its fact floor.

*Call graph*: called by 2 (run, run); 2 external calls (execute, update).


##### `SectionWriter.run`  (lines 1079–1102)

```
async def run(self) -> None
```

**Purpose**: Runs the periodic section-summary pass. It rewrites short paragraphs above each memory section that has enough live facts, and retires paragraphs for sections that no longer do.

**Data flow**: It skips if no model exists, reads qualifying sections and currently standing sections, retires stale standings, reads facts for each qualifying section, asks the model for a paragraph, and rewrites that paragraph in place.

**Call relations**: This coordinates SectionWriter’s helpers and uses _retire_standing and _rewrite_in_place for the actual database changes.

*Call graph*: calls 6 internal fn (_facts, _sections, _standing, _summarize, _retire_standing, _rewrite_in_place).


##### `SectionWriter._sections`  (lines 1104–1137)

```
async def _sections(self) -> tuple[_Standing, ...]
```

**Purpose**: Finds the subject/kind bands that currently have enough live facts to deserve a section paragraph.

**Data flow**: It queries servable live facts, groups by subject and memory kind, keeps groups above the minimum count, verifies each memory kind has a visible heading, and returns _Standing locations.

**Call relations**: SectionWriter.run uses this as the target list for sections to write. It depends on member_servable and section_headings to match what users can actually read.

*Call graph*: calls 2 internal fn (member_servable, section_headings); called by 1 (run); 2 external calls (__init__, select).


##### `SectionWriter._standing`  (lines 1139–1157)

```
async def _standing(self) -> tuple[_Standing, ...]
```

**Purpose**: Finds section paragraphs that are currently live, whether or not their underlying facts still qualify.

**Data flow**: It queries live section memory items, groups them by subject and memory kind, and returns their _Standing locations.

**Call relations**: SectionWriter.run compares this with _sections to retire paragraphs that have fallen below the fact floor.

*Call graph*: called by 1 (run); 2 external calls (__init__, select).


##### `SectionWriter._facts`  (lines 1159–1180)

```
async def _facts(self, section: _Standing) -> tuple[_SectionFact, ...]
```

**Purpose**: Reads the newest live facts under one section so the model can summarize the section as it exists now.

**Data flow**: It receives a section standing, queries servable live facts for that subject and memory kind, orders newest first, limits the result, and returns body/confidence pairs.

**Call relations**: SectionWriter.run calls this before _summarize for each section that qualifies.

*Call graph*: calls 1 internal fn (member_servable); called by 1 (run); 2 external calls (__init__, select).


##### `SectionWriter._summarize`  (lines 1182–1201)

```
async def _summarize(self, model: ModelAccess, section: _Standing, facts: tuple[_SectionFact, ...]) -> str
```

**Purpose**: Asks the model to write one short paragraph for a section under its actual heading.

**Data flow**: It receives a model, section location, and facts, builds a payload with the heading and clipped fact bodies, gets model text, trims it to the paragraph budget, and returns it.

**Call relations**: SectionWriter.run calls this before writing the paragraph through _rewrite_in_place.

*Call graph*: calls 3 internal fn (complete, _to_overview_budget, section_headings); called by 1 (run); 3 external calls (__init__, __init__, dumps).


##### `OverviewWriter.run`  (lines 1225–1246)

```
async def run(self) -> None
```

**Purpose**: Runs the periodic workspace overview pass. It keeps exactly one opening paragraph for the shared workspace memory page when enough live facts exist.

**Data flow**: It skips if no model exists, reads shared workspace facts, retires the overview if there are too few, otherwise reads the workspace domain, asks the model for an overview, and rewrites the standing overview in place.

**Call relations**: This coordinates OverviewWriter._facts and _write, then uses _retire_standing or _rewrite_in_place for the database update.

*Call graph*: calls 4 internal fn (_facts, _write, _retire_standing, _rewrite_in_place); 2 external calls (__init__, workspace_domain).


##### `OverviewWriter._facts`  (lines 1248–1269)

```
async def _facts(self) -> tuple[_SectionFact, ...]
```

**Purpose**: Reads the newest live shared-workspace facts across all memory kinds for the overview paragraph.

**Data flow**: It queries servable live fact rows for the shared subject, orders newest first, limits the result, and returns body/confidence pairs.

**Call relations**: OverviewWriter.run calls this to decide whether an overview should exist and what facts the model should see.

*Call graph*: calls 1 internal fn (member_servable); called by 1 (run); 2 external calls (__init__, select).


##### `OverviewWriter._write`  (lines 1271–1291)

```
async def _write(self, model: ModelAccess, domain: str | None, facts: tuple[_SectionFact, ...]) -> str
```

**Purpose**: Asks the model to write the workspace’s opening overview paragraph.

**Data flow**: It receives a model, optional workspace domain, and facts, builds a compact JSON payload, gets model text, trims it to the paragraph budget, and returns it.

**Call relations**: OverviewWriter.run calls this before committing the resulting overview with _rewrite_in_place.

*Call graph*: calls 2 internal fn (complete, _to_overview_budget); called by 1 (run); 3 external calls (__init__, __init__, dumps).


##### `WrittenProfile.within_role_budget`  (lines 1328–1329)

```
def within_role_budget(cls, role: str) -> str
```

**Purpose**: Keeps a generated people-profile role short enough to read like a phrase, not a paragraph.

**Data flow**: It receives the role text, clips it at a word boundary to the role character limit, and returns the clipped role.

**Call relations**: This validator runs when ProfileWriter._write validates each model-produced WrittenProfile.

*Call graph*: 1 external calls (clip_to_word).


##### `WrittenProfile.within_row_budget`  (lines 1333–1334)

```
def within_row_budget(cls, focus: str) -> str
```

**Purpose**: Keeps a generated people-profile focus sentence within the normal memory row size.

**Data flow**: It receives the focus text, clips it safely at a word boundary, and returns the clipped focus.

**Call relations**: This validator runs during WrittenProfile validation inside ProfileWriter._write.

*Call graph*: 1 external calls (clip_to_word).


##### `ProfileWriter.run`  (lines 1372–1387)

```
async def run(self) -> None
```

**Purpose**: Runs the periodic People pass, writing each roster member’s role and current focus into the memory_profile table.

**Data flow**: It skips if no model exists, loads the roster, reads shared facts, asks the model for people entries, matches entries back to roster names, and stores matched profiles.

**Call relations**: This coordinates ProfileWriter._roster, _facts, _write, and _store. It only writes model entries that name known roster members.

*Call graph*: calls 4 internal fn (_facts, _roster, _store, _write).


##### `ProfileWriter._roster`  (lines 1389–1408)

```
async def _roster(self) -> tuple[_Rostered, ...]
```

**Purpose**: Reads the workspace roster in the same way the seating system understands it. It records each member’s email and standing, such as admin/member and seated/unseated.

**Data flow**: It opens a transaction, asks Seats for a snapshot, takes a bounded number of members, and returns _Rostered records.

**Call relations**: ProfileWriter.run calls this before asking the model to write people profiles.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `ProfileWriter._facts`  (lines 1410–1427)

```
async def _facts(self) -> tuple[str, ...]
```

**Purpose**: Reads shared workspace facts that everyone on the roster could already see. This avoids using a person’s private memory to write a public profile.

**Data flow**: It queries servable live facts for the shared subject, orders newest first, limits the result, and returns fact bodies.

**Call relations**: ProfileWriter.run sends these facts to _write along with the roster.

*Call graph*: calls 1 internal fn (member_servable); called by 1 (run); 1 external calls (select).


##### `ProfileWriter._write`  (lines 1429–1477)

```
async def _write(self, model: ModelAccess, roster: tuple[_Rostered, ...], facts: tuple[str, ...]) -> tuple[WrittenProfile, ...]
```

**Purpose**: Asks the model to produce one role and focus entry per roster member through a structured tool call.

**Data flow**: It receives the model, roster, and facts, builds a payload with member names, emails, standing, and clipped facts, forces the write_people tool call, validates each returned entry, and returns accepted WrittenProfile objects.

**Call relations**: ProfileWriter.run calls this before matching entries to member ids and storing them.

*Call graph*: calls 1 internal fn (turn); called by 1 (run); 4 external calls (__init__, __init__, __init__, dumps).


##### `ProfileWriter._store`  (lines 1479–1508)

```
async def _store(self, entries: tuple[tuple[UUID, WrittenProfile], ...]) -> None
```

**Purpose**: Writes people profiles into the memory_profile table, replacing each member’s previous row if one exists.

**Data flow**: It receives member/profile pairs, opens one transaction, and performs database upserts keyed by workspace and member id with the new role, focus, and timestamp.

**Call relations**: ProfileWriter.run calls this after model output has been validated and matched to roster members.

*Call graph*: called by 1 (run); 3 external calls (now, insert, insert).


##### `admitted_curation`  (lines 1562–1597)

```
def admitted_curation(bands: tuple[tuple[int, ...], ...], retire: tuple[RetiredRow, ...]) -> AdmittedCuration
```

**Purpose**: Checks whether the page-curation model’s proposed row retirements are safe enough to apply. It is a guardrail before any destructive edit.

**Data flow**: It receives the row indexes sent for each band and the model’s retirement requests. It drops unknown rows, requires each retired row to point to a surviving duplicate, refuses pages that would retire too much of one band, and returns accepted indexes or a refusal reason.

**Call relations**: PagePass._retiring calls this after model curation and before converting row indexes into database ids.

*Call graph*: called by 1 (_retiring); 1 external calls (__init__).


##### `PagePass.run`  (lines 1645–1658)

```
async def run(self) -> None
```

**Purpose**: Runs the periodic full-page curation pass. For each long enough subject page, it asks the model which page-derived rows duplicate others and retires only admitted rows.

**Data flow**: It skips if no model exists, reads candidate subjects, builds each page, asks the model to curate it, filters the model’s retirements through _retiring, and applies accepted retirements.

**Call relations**: This coordinates PagePass._subjects, _page, _curate, _retiring, and _apply.

*Call graph*: calls 5 internal fn (_apply, _curate, _page, _retiring, _subjects).


##### `PagePass._subjects`  (lines 1660–1676)

```
async def _subjects(self) -> tuple[str, ...]
```

**Purpose**: Finds subjects whose memory pages have enough live fact rows to be worth full-page curation.

**Data flow**: It queries fact rows by subject, excludes superseded and retired rows, keeps subjects above the minimum count, orders them, and returns subject strings.

**Call relations**: PagePass.run uses this list to decide which pages to inspect.

*Call graph*: called by 1 (run); 1 external calls (select).


##### `PagePass._page`  (lines 1678–1724)

```
async def _page(self, subject: str) -> tuple[_Band, ...]
```

**Purpose**: Builds the page payload for one subject, including each section heading, its current summary paragraph, and its live page-derived rows.

**Data flow**: It receives a subject, loops through that subject’s section headings, reads live page-linked fact rows for each memory kind, reads the standing section summary if present, assigns small numeric row indexes, and returns bands.

**Call relations**: PagePass.run calls this before _curate. It uses live_page_link to stay on current source revisions and section_headings to shape the page as users see it.

*Call graph*: calls 2 internal fn (live_page_link, section_headings); called by 1 (run); 3 external calls (__init__, __init__, select).


##### `PagePass._curate`  (lines 1726–1775)

```
async def _curate(self, model: ModelAccess, bands: tuple[_Band, ...]) -> CuratedPage
```

**Purpose**: Asks the model to read a whole memory page and name rows the page would read better without.

**Data flow**: It receives a model and bands, builds a compact payload with headings, summaries, and clipped row bodies, forces the curate_page tool call, validates returned retire entries, and returns a CuratedPage.

**Call relations**: PagePass.run calls this after _page and before _retiring checks the proposed retirements.

*Call graph*: calls 1 internal fn (turn); called by 1 (run); 5 external calls (__init__, __init__, __init__, __init__, dumps).


##### `PagePass._retiring`  (lines 1777–1788)

```
def _retiring(self, subject: str, bands: tuple[_Band, ...], retire: tuple[RetiredRow, ...]) -> frozenset[UUID]
```

**Purpose**: Turns the model’s row-index retirements into real database row ids, but only after safety checks pass.

**Data flow**: It receives a subject, page bands, and proposed retirements, calls admitted_curation, logs a warning if the whole page is refused, and returns the ids of rows whose indexes were admitted.

**Call relations**: PagePass.run calls this between _curate and _apply. It delegates the safety decision to admitted_curation.

*Call graph*: calls 1 internal fn (admitted_curation); called by 1 (run); 1 external calls (warn).


##### `PagePass._apply`  (lines 1790–1806)

```
async def _apply(self, retiring: frozenset[UUID]) -> None
```

**Purpose**: Marks accepted page rows as retired and removes their indexing claim so search will stop returning them.

**Data flow**: It receives row ids, opens a transaction, updates matching live rows in this workspace with retired_at, clears embedding fields, and updates timestamps.

**Call relations**: PagePass.run calls this once it has a non-empty set of safe retirements.

*Call graph*: called by 1 (run); 1 external calls (update).


### `extensions/memory/ufo_ext_memory/store.py`

`domain_logic` · `request handling and background indexing`

This file is the memory extension’s workshop. It owns the database tables for remembered items, their links back to source pages, and a small mirror of source pages that have been indexed. A write is deliberately simple: `commit` stores one memory row and marks it as needing later indexing. It does not split text into chunks or call the embedding service inline, so saving a memory stays fast and reliable.

Recall works like asking a librarian for the best matching notes. It searches both by words and by meaning, fuses those two ranked lists, then reads the matching rows back from the database. At that point it applies safety fences: the reader must be allowed to see the subject and any linked source, the page revision must still be current, and retired or superseded rows are hidden. It also lowers old facts by a time-decay rule, removes near-duplicates, and prevents one memory type from crowding out the rest.

Two background indexers keep derived search data fresh. `MemoryIndexer` claims unindexed memory rows, checks whether they are still publishable, chunks and embeds them, or removes their chunks if they should no longer be searchable. `PageIndexer` does the same for synced source pages. Without this file, memories could be saved but not reliably recalled, stale page-derived facts could leak into search, and indexing work could race or duplicate itself.

#### Function details

##### `recall_subjects`  (lines 176–177)

```
def recall_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: Turns an audience description into the exact subject labels that memory recall should search under. This keeps recall scoped to the people or groups the caller is allowed to address.

**Data flow**: It receives an `Audience` object, asks the shared audience helper to expand it into subject strings, and returns those strings as an immutable set.

**Call relations**: This is a small bridge from the memory extension to the shared audience code. It delegates the real audience expansion to `ufo.sdk.audience.audience_subjects` so the memory system uses the same subject rules as the rest of the project.

*Call graph*: 1 external calls (audience_subjects).


##### `clip_to_word`  (lines 180–189)

```
def clip_to_word(text: str, limit: int) -> str
```

**Purpose**: Shortens text to a character limit without cutting a word in half. It is useful when a caller wants to trim an overlong memory body while keeping it readable.

**Data flow**: It takes text and a maximum length. If the text already fits, it returns it unchanged; otherwise it keeps as much as will fit, backs up to the previous word boundary when possible, tidies trailing punctuation, and adds an ellipsis within the limit.

**Call relations**: This helper is available to callers that choose to trim text before creating a `MemoryWrite`. The validator on `MemoryWrite` rejects over-budget bodies rather than trimming them automatically.


##### `_granted_link`  (lines 192–200)

```
def _granted_link(source_ids: frozenset[UUID]) -> ColumnElement[bool]
```

**Purpose**: Builds a database condition that says a page-derived memory is readable if the reader has access to at least one source that produced it. This matters because the same fact can come from more than one source.

**Data flow**: It receives a set of source IDs. It creates an SQL `EXISTS` test that looks for a matching row in `memory_source` tied to the current `memory_item` row and one of those sources.

**Call relations**: `MemoryStore._untail_leg` uses this while scanning not-yet-indexed memories, and `MemoryStore._enrich` uses it while reading final recall rows. In both places it is the source-permission fence.

*Call graph*: called by 2 (_enrich, _untail_leg); 1 external calls (exists).


##### `inventory`  (lines 240–309)

```
async def inventory(transaction: Transaction, workspace_id: UUID) -> tuple[MemoryInventoryItem, ...]
```

**Purpose**: Returns a bounded, newest-first listing of stored memories for an operator or explorer view. It is not a search; it shows what is in the store and enough extra information to understand each row’s indexing and recall state.

**Data flow**: It receives a transaction opener and workspace ID, reads up to the inventory limit of memory rows, reads their source links, computes age, half-life, and decay using one current timestamp, and returns `MemoryInventoryItem` objects.

**Call relations**: It calls `_aware`, `half_life_days`, and `decay_multiplier` so the explorer reports the same time-decay numbers recall would use. It talks directly to the database through SQLAlchemy selects.

*Call graph*: calls 3 internal fn (_aware, decay_multiplier, half_life_days); 3 external calls (__init__, now, select).


##### `_aware`  (lines 312–313)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Ensures a timestamp has timezone information. This avoids mixing timezone-aware and timezone-naive dates in age calculations.

**Data flow**: It takes a `datetime`. If it already has a timezone, it returns it; otherwise it treats it as UTC and returns a copy marked that way.

**Call relations**: `inventory`, `decay_multiplier`, `MemoryStore._enrich`, and `MemoryStore.search_sources` use this before comparing or returning stored times. It is the small guardrail that keeps date math consistent.

*Call graph*: called by 4 (_enrich, search_sources, decay_multiplier, inventory); 1 external calls (replace).


##### `MemoryWrite.body_is_within_budget`  (lines 340–346)

```
def body_is_within_budget(self) -> Self
```

**Purpose**: Rejects memory writes whose body is too long to be a single memory item. This keeps memories as concise, self-contained statements rather than turning them into documents.

**Data flow**: During model validation, it reads the proposed body length. If the body is within the configured limit, validation continues; if not, it raises a clear error.

**Call relations**: This runs automatically when a `MemoryWrite` is created. It protects `MemoryStore.commit` from receiving a body that the memory table and recall display rules are not meant to carry.


##### `MemoryWrite.page_origin_is_complete`  (lines 349–357)

```
def page_origin_is_complete(self) -> Self
```

**Purpose**: Makes sure a page-derived memory names its full origin: page ID, page revision, and source ID. A partial origin would make permission checks and stale-page checks unreliable.

**Data flow**: During validation, it checks the three origin fields. If none are set, the memory is treated as not page-derived; if some are set but not all, it raises an error; if all are set, validation passes.

**Call relations**: This runs before `MemoryStore.commit` stores the write. Later recall and indexing code rely on these fields being all-present or all-absent.


##### `_fuse`  (lines 387–410)

```
def _fuse(legs: tuple[tuple[Hit, ...], ...], cosine_leg: tuple[Hit, ...]) -> dict[str, tuple[float, float, str]]
```

**Purpose**: Combines several search result lists into one score per owning item or page. It uses reciprocal-rank fusion, a method that rewards results that appear near the top of more than one list.

**Data flow**: It receives one or more result lists plus the vector-search list. It ranks chunks inside each list, sums rank-based points for each chunk, keeps the best chunk per owner, and attaches the best raw vector similarity for that owner.

**Call relations**: `fuse_hits` and `fuse_recall` call this as their shared core. It works on generic index `Hit` objects, so it can support both memory recall and source-page search.

*Call graph*: called by 2 (fuse_hits, fuse_recall); 1 external calls (from_iterable).


##### `fuse_hits`  (lines 413–426)

```
def fuse_hits(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks source-page search hits by combining word search and meaning search. It also blocks meaningless queries from returning arbitrary nearest-neighbor results when no words matched.

**Data flow**: It receives lexical hits, vector hits, and a limit. It fuses the lists, applies a cosine-similarity floor if the word search found nothing, sorts by fused rank, and returns `Fused` results up to the limit.

**Call relations**: `MemoryStore.search_sources` calls this after getting the two index legs for pages. It uses `_fuse` to do the common ranking work.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (search_sources); 1 external calls (__init__).


##### `fuse_recall`  (lines 429–457)

```
def fuse_recall(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], tail: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks memory recall candidates by blending rank-fusion with raw semantic closeness. It also includes a temporary word-search leg for memories that were just saved but not indexed yet.

**Data flow**: It receives lexical hits, vector hits, tail hits, and a limit. It fuses all three, normalizes the rank score, blends it with vector similarity, applies a floor only when there were no word matches anywhere, sorts, and returns `Fused` results.

**Call relations**: `MemoryStore.recall` calls this after gathering index hits and the unindexed tail. It builds on `_fuse`, then adds recall-specific scoring.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (recall); 1 external calls (__init__).


##### `half_life_days`  (lines 475–481)

```
def half_life_days(item_class: str, memory_kind: str) -> float | None
```

**Purpose**: Chooses how quickly a fact should fade in recall ranking. Non-fact memory classes do not decay and therefore return no half-life.

**Data flow**: It reads an item class and memory kind. If the item is a fact, it returns the configured half-life for that kind, falling back to the default fact value; otherwise it returns `None`.

**Call relations**: `decay_multiplier` uses this for recall scoring, and `inventory` uses it to show operators the same decay setting.

*Call graph*: called by 2 (decay_multiplier, inventory).


##### `decay_multiplier`  (lines 484–496)

```
def decay_multiplier(item_class: str, memory_kind: str, confidence: int, as_of: datetime | None, now: datetime) -> float
```

**Purpose**: Calculates how much time and confidence should reduce a memory’s relevance. For example, a low-confidence old task fades much faster than a high-confidence stable fact.

**Data flow**: It takes item type, memory kind, confidence, an `as_of` time, and the current time. For facts with a date, it computes confidence divided by 10 times an exponential age decay; for other items or missing dates, it returns 1.0.

**Call relations**: `decay_factor` calls this during recall ranking, and `inventory` calls it for display. It uses `half_life_days` and `_aware` to keep the calculation centralized.

*Call graph*: calls 2 internal fn (_aware, half_life_days); called by 2 (decay_factor, inventory).


##### `decay_factor`  (lines 499–502)

```
def decay_factor(item: Recalled, now: datetime) -> float
```

**Purpose**: Gets the live decay multiplier for one recalled memory. It is a convenience wrapper around the shared decay calculation.

**Data flow**: It receives a `Recalled` item and the current time, chooses the item’s `as_of` date or creation date, and returns the multiplier from `decay_multiplier`.

**Call relations**: `MemoryStore._shortlist` calls this while turning a broad candidate pool into the final recall list.

*Call graph*: calls 1 internal fn (decay_multiplier); called by 1 (_shortlist).


##### `_body_shingles`  (lines 510–512)

```
def _body_shingles(body: str) -> frozenset[str]
```

**Purpose**: Turns a body of text into small three-word fingerprints used to spot near-duplicates. This is a cheap way to notice that two memories say almost the same thing.

**Data flow**: It lowercases the body, splits it into words, slides a three-word window across them, and returns the set of those short phrases.

**Call relations**: `drop_near_duplicates` calls this for each candidate it considers. It uses regular-expression splitting to ignore punctuation differences.

*Call graph*: called by 1 (drop_near_duplicates); 1 external calls (split).


##### `drop_near_duplicates`  (lines 515–541)

```
def drop_near_duplicates(items: tuple[Recalled, ...], keep: int) -> tuple[Recalled, ...]
```

**Purpose**: Removes recall candidates whose visible text is very similar to something already kept. This saves limited context space from being wasted on repeated versions of the same fact.

**Data flow**: It receives ranked `Recalled` items and a keep count. It walks the list in order, builds word-shingle sets for candidates, skips any that overlap too much with an already kept item, and returns the kept tuple.

**Call relations**: `MemoryStore._shortlist` calls this after decay-adjusted ranking and before type diversity. It relies on `_body_shingles` for the similarity fingerprints.

*Call graph*: calls 1 internal fn (_body_shingles); called by 1 (_shortlist).


##### `enforce_type_diversity`  (lines 544–562)

```
def enforce_type_diversity(rows: tuple[Recalled, ...], limit: int) -> tuple[Recalled, ...]
```

**Purpose**: Prevents one class of memory from filling all recall slots. It keeps the final context more balanced when, for example, many facts outrank all preferences or topics.

**Data flow**: It receives ranked rows and a limit. It admits only a capped number per item class on the first pass, saves overflow rows, then backfills from overflow if there is still room.

**Call relations**: `MemoryStore._shortlist` calls this as the last narrowing step before recall results are returned.

*Call graph*: called by 1 (_shortlist).


##### `as_topic_pointer`  (lines 565–575)

```
def as_topic_pointer(item: Recalled, index: int) -> Recalled
```

**Purpose**: Turns episodic memories into pointers instead of injecting their full bodies. Episodic memory acts like a breadcrumb to browse, not text to automatically paste into context.

**Data flow**: It receives a recalled item and its position. If the item is episodic, it returns a copy whose body is a short topic pointer and whose recall mode is `topic`; otherwise it returns the item unchanged.

**Call relations**: `MemoryStore.recall` applies this to the final shortlist. It uses dataclass replacement so the original recalled object is not mutated.

*Call graph*: called by 1 (recall); 1 external calls (replace).


##### `MemoryStore.commit`  (lines 602–728)

```
async def commit(self, write: MemoryWrite) -> UUID
```

**Purpose**: Stores one memory item and records where it came from, without doing any indexing work immediately. This keeps writes quick and leaves chunking and embedding to the background indexer.

**Data flow**: It receives a validated `MemoryWrite`. It creates a stable ID from workspace, subject, class, and body; upserts the memory row; clears indexing state when the page binding changed; revives superseded rows when restated; records a source-page link when present; and returns the memory ID.

**Call relations**: Callers that derive or write memories use this as the durable write path. Later, `MemoryIndexer` sees rows whose embedding digest is missing and turns them into searchable chunks.

*Call graph*: 3 external calls (case, or_, uuid5).


##### `MemoryStore.supersede_page_facts`  (lines 730–851)

```
async def supersede_page_facts(self, page_id: UUID, kept: frozenset[UUID] | None) -> None
```

**Purpose**: Retires page-derived memory links that a page no longer supports. It is careful not to delete a fact if another source page still supports the same stored row.

**Data flow**: It receives a page ID and either the set of memory IDs the latest derivation kept or `None` for a gone page. It deletes stale links, repoints rows to surviving links when possible, deletes rows with no links left, clears indexing state for rebound rows, and removes deleted rows from the index.

**Call relations**: The page fact derivation flow calls this after committing the facts a page still stands behind. It hands deleted memory IDs off to the index backend with `IndexScope` so their chunks are removed.

*Call graph*: 4 external calls (__init__, delete, select, update).


##### `MemoryStore.recall`  (lines 853–892)

```
async def recall(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None, *, source_reader: SourceReader) -> tuple[Recalled, ...]
```

**Purpose**: Finds the best memories for a user query while respecting subjects, source permissions, page freshness, duplicate suppression, and time decay. This is the main read path used when the system wants relevant memory.

**Data flow**: It receives a query, allowed subjects, a limit, optional time bounds, and a source reader. It gets readable source IDs, asks the index for word and vector hits, scans the unindexed tail, fuses the hits, enriches them from the database, shortlists them in a worker thread, rewrites episodic items as topic pointers, and returns the final tuple.

**Call relations**: This orchestrates `_source_ids`, `_legs`, `_untail_leg`, `fuse_recall`, `_enrich`, `_shortlist`, and `as_topic_pointer`. It is the place where search ranking and database safety checks meet.

*Call graph*: calls 6 internal fn (_enrich, _legs, _source_ids, _untail_leg, as_topic_pointer, fuse_recall); 2 external calls (to_thread, now).


##### `MemoryStore._shortlist`  (lines 894–909)

```
def _shortlist(self, enriched: tuple[Recalled, ...], limit: int, now: datetime) -> tuple[Recalled, ...]
```

**Purpose**: Turns a broad set of recall candidates into the final number requested. It applies decay, removes near-duplicates, and enforces type variety.

**Data flow**: It receives enriched candidates, a limit, and the current time. It replaces each score with a decay-adjusted score, sorts by that score, drops near-duplicate bodies, applies the type-diversity cap, and returns the shortened tuple.

**Call relations**: `MemoryStore.recall` runs this through `asyncio.to_thread` so the CPU-heavy comparison work does not block the async request loop. It calls `decay_factor`, `drop_near_duplicates`, and `enforce_type_diversity`.

*Call graph*: calls 3 internal fn (decay_factor, drop_near_duplicates, enforce_type_diversity); 1 external calls (replace).


##### `MemoryStore.search_sources`  (lines 911–977)

```
async def search_sources(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None, *, source_reader: SourceReader) -> tuple[SourceMatch, ...]
```

**Purpose**: Searches indexed source pages, not distilled memory facts. It lets callers find matching page snippets while still checking that the page is current and readable.

**Data flow**: It receives a query, subjects, limit, optional time bounds, and a source reader. It gets lexical and vector page hits, fuses them, reads matching `mem_page` mirror rows, asks for readable current page states, filters stale or unauthorized pages, and returns `SourceMatch` snippets.

**Call relations**: This is the source-page counterpart to `MemoryStore.recall`. It calls `_legs`, `fuse_hits`, `_readable_states`, and `_aware`, then builds `SourceMatch` results.

*Call graph*: calls 4 internal fn (_legs, _readable_states, _aware, fuse_hits); 3 external calls (__init__, select, UUID).


##### `MemoryStore._source_ids`  (lines 979–985)

```
async def _source_ids(self, source_reader: SourceReader) -> frozenset[UUID]
```

**Purpose**: Asks the configured permission authority which source IDs this reader may access. Source-derived memory reads cannot proceed safely without this authority.

**Data flow**: It receives a `SourceReader`. If no readable-source callback is configured, it raises an error; otherwise it calls the callback and returns the readable source ID set.

**Call relations**: `MemoryStore.recall` calls this before searching the unindexed tail and enriching results. The returned IDs feed source-grant filters.

*Call graph*: called by 1 (recall).


##### `MemoryStore._legs`  (lines 987–999)

```
async def _legs(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[tuple[Hit, ...], tuple[Hit, ...]]
```

**Purpose**: Runs the two main search legs: word-based search and vector-based meaning search. Keeping this shared avoids duplicating search setup for memory recall and page search.

**Data flow**: It receives a query, subjects, owner kind, and limit. It embeds the query if possible, asks the index for lexical hits, asks for vector hits only when an embedding exists, and returns both hit tuples.

**Call relations**: `MemoryStore.recall` uses this for memory-item hits, and `MemoryStore.search_sources` uses it for page hits. It calls `_embed_query` first so vector search can be skipped cleanly if embedding fails.

*Call graph*: calls 1 internal fn (_embed_query); called by 2 (recall, search_sources).


##### `MemoryStore._untail_leg`  (lines 1001–1057)

```
async def _untail_leg(self, query: str, subjects: frozenset[str], limit: int, source_ids: frozenset[UUID]) -> tuple[Hit, ...]
```

**Purpose**: Searches recently saved memories that have not been indexed yet. This makes a new fact recallable during the short gap before the background index job processes it.

**Data flow**: It receives a query, subjects, limit, and readable source IDs. It splits the query into terms, scans a bounded number of newest unindexed rows that pass subject and source permissions, scores each by term counts in the body, and returns synthetic `Hit` objects sorted by score.

**Call relations**: `MemoryStore.recall` calls this as a third recall leg. It uses `_granted_link` to enforce source grants and creates `Hit` objects shaped like index results so `fuse_recall` can combine them.

*Call graph*: calls 1 internal fn (_granted_link); called by 1 (recall); 4 external calls (__init__, split, or_, select).


##### `MemoryStore._embed_query`  (lines 1059–1067)

```
async def _embed_query(self, query: str) -> tuple[float, ...]
```

**Purpose**: Gets an embedding vector for a query, while treating blank queries or embedding failures as no-vector-search cases. This keeps recall working even if the embedding service is temporarily unavailable.

**Data flow**: It receives the query string. Blank text returns an empty tuple; otherwise it calls the embed client, returns the first vector if present, and logs a warning plus returns empty on error.

**Call relations**: `MemoryStore._legs` calls this before deciding whether to run vector search. A failure here degrades recall to lexical search rather than failing the whole request.

*Call graph*: called by 1 (_legs).


##### `MemoryStore._enrich`  (lines 1069–1149)

```
async def _enrich(self, fused: tuple[Fused, ...], subjects: frozenset[str], source_ids: frozenset[UUID], start: datetime | None, end: datetime | None) -> tuple[Recalled, ...]
```

**Purpose**: Loads fused memory hits from the database and applies the final visibility and freshness fences. Index hits are only candidates; this function decides which rows may actually be shown.

**Data flow**: It receives fused hits, allowed subjects, readable source IDs, and optional time bounds. It reads matching memory rows that are not superseded or retired and pass source grants, checks current page state for page-derived rows, then returns `Recalled` objects in fused order.

**Call relations**: `MemoryStore.recall` calls this after `fuse_recall`. It uses `_granted_link` for source permissions, `_aware` for timestamps, and `page_states` to reject rows tied to old page revisions.

*Call graph*: calls 2 internal fn (_aware, _granted_link); called by 1 (recall); 4 external calls (__init__, or_, select, UUID).


##### `MemoryStore._readable_states`  (lines 1151–1158)

```
async def _readable_states(self, page_ids: tuple[UUID, ...], source_reader: SourceReader) -> dict[UUID, PageState]
```

**Purpose**: Fetches current page states only for pages the reader is allowed to inspect. This is the page-search equivalent of source permission checking.

**Data flow**: It receives page IDs and a source reader. Empty input returns an empty dictionary; otherwise it requires a readable-page callback and returns that callback’s page-state mapping.

**Call relations**: `MemoryStore.search_sources` calls this after finding candidate page IDs. The result lets source search drop stale or unauthorized page snippets.

*Call graph*: called by 1 (search_sources).


##### `store_for`  (lines 1161–1174)

```
def store_for(ext: ExtensionContext) -> MemoryStore
```

**Purpose**: Builds a `MemoryStore` from the extension context. It fails early if the required index or embedding backends were not wired in.

**Data flow**: It receives an `ExtensionContext`, checks that index and embed clients exist, pulls the transaction opener, workspace ID, page-state callbacks, and permission callbacks from the context, and returns a configured `MemoryStore`.

**Call relations**: Startup or extension wiring code uses this to create the memory workflow object. It centralizes the dependency handoff from the host extension context into this file’s store class.

*Call graph*: 1 external calls (__init__).


##### `MemoryIndexer.run`  (lines 1198–1200)

```
async def run(self) -> None
```

**Purpose**: Processes a batch of memory rows that need indexing. It is the simple outer loop for the background memory indexing job.

**Data flow**: It claims due items, then sends each claimed item through the indexing decision path one at a time.

**Call relations**: The scheduler calls this job periodically. It delegates claiming to `_claim_due` and per-row work to `_index_item`.

*Call graph*: calls 2 internal fn (_claim_due, _index_item).


##### `MemoryIndexer._claim_due`  (lines 1202–1238)

```
async def _claim_due(self) -> tuple[MemoryItem, ...]
```

**Purpose**: Atomically reserves a bounded batch of memory rows whose embeddings are missing or whose previous claim expired. This prevents two indexer runs from embedding the same row at the same time.

**Data flow**: It computes a lease cutoff time, selects due rows, uses row locking where supported, stamps `embedding_claimed_at` on the selected rows, and returns them as `MemoryItem` objects.

**Call relations**: `MemoryIndexer.run` calls this first. The claim it writes is later checked and cleared by `_settle` when `_index_item` reaches a terminal decision.

*Call graph*: called by 1 (run); 5 external calls (now, timedelta, or_, select, update).


##### `MemoryIndexer._index_item`  (lines 1240–1277)

```
async def _index_item(self, item: MemoryItem) -> None
```

**Purpose**: Decides whether one claimed memory should be published to the search index, removed from it, or left for a later run. It protects recall from stale page-derived chunks.

**Data flow**: It receives a claimed `MemoryItem`. If the item is retired or no longer publishable, it deletes its index chunks and settles the row; otherwise it upserts chunks if missing, rereads the current binding, rechecks publishability, and settles only if the binding has not changed under it.

**Call relations**: `MemoryIndexer.run` calls this for every claimed row. It uses `_publishable` for freshness checks, `chunk_embed_upsert` for chunking and embedding, `IndexScope` for deletes, and `_settle` to mark completion.

*Call graph*: calls 2 internal fn (_publishable, _settle); called by 1 (run); 3 external calls (__init__, select, chunk_embed_upsert).


##### `MemoryIndexer._publishable`  (lines 1279–1288)

```
async def _publishable(self, subject: str, page_id: UUID | None, revision: int | None) -> bool
```

**Purpose**: Checks whether a memory item may appear in the index. Tool-written memories are publishable, but page-derived memories must still match the live page subject and revision.

**Data flow**: It receives a subject, optional page ID, and optional revision. If there is no page ID it returns true; otherwise it reads the current page state and returns true only when subject and revision match.

**Call relations**: `MemoryIndexer._index_item` calls this before and after indexing work. The double check prevents stale page-derived memory from occupying search candidate slots.

*Call graph*: called by 1 (_index_item).


##### `MemoryIndexer._settle`  (lines 1290–1314)

```
async def _settle(self, item: MemoryItem) -> None
```

**Purpose**: Marks a claimed memory row as decided by the indexer. The decision may be either published chunks or deliberately withheld chunks.

**Data flow**: It receives the item that was claimed. It writes a SHA-256 digest of the body, clears the claim timestamp, and updates the row only if the important fields still match the claimed version and the claim is still present.

**Call relations**: `MemoryIndexer._index_item` calls this after it has made a safe terminal decision. The guarded update avoids settling a row that another page derivation changed meanwhile.

*Call graph*: called by 1 (_index_item); 2 external calls (sha256, update).


##### `PageIndexer.apply`  (lines 1337–1339)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Applies a delivered batch of source-page changes to the memory extension’s page index. It is the batch-level entry used by the page-change hook.

**Data flow**: It receives a tuple of page changes and processes each one in order by calling `_apply`.

**Call relations**: The core page-change runner owns batching and cursor progress, then calls this method. This method keeps the file’s page-indexing work idempotent by delegating each change to `_apply`.

*Call graph*: calls 1 internal fn (_apply).


##### `PageIndexer._apply`  (lines 1341–1399)

```
async def _apply(self, change: PageChange) -> None
```

**Purpose**: Indexes, refreshes, or removes one source page’s searchable chunks and mirror row. It also marks facts from old revisions as needing re-check by the memory indexer.

**Data flow**: It receives a `PageChange`, reads the current page state, clears digest state for facts left behind by the page’s current state, deletes chunks and mirror rows for tombstones or stale changes, otherwise chunks and embeds the page body, rechecks that the page did not change during embedding, and upserts the `mem_page` mirror row.

**Call relations**: `PageIndexer.apply` calls this for each change. It calls `_unsettle_left_behind_facts`, uses `chunk_embed_upsert` to publish page chunks, and uses `IndexScope` plus database delete/update/insert operations to keep the index and mirror aligned.

*Call graph*: calls 1 internal fn (_unsettle_left_behind_facts); called by 1 (apply); 5 external calls (__init__, delete, insert, update, chunk_embed_upsert).


##### `PageIndexer._unsettle_left_behind_facts`  (lines 1401–1429)

```
async def _unsettle_left_behind_facts(self, page_id: UUID, state: PageState | None) -> None
```

**Purpose**: Marks page-derived memory rows from no-longer-current page revisions as due for the memory indexer again. This lets the memory indexer withdraw stale chunks even if no replacement facts were derived.

**Data flow**: It receives a page ID and the page’s current state, builds a database filter for memory rows created from that page but no longer matching the live subject and revision, or all such rows if the page is gone, then clears their embedding digest and claim timestamp.

**Call relations**: `PageIndexer._apply` calls this before handling each page change. It does not delete memory rows itself; it asks `MemoryIndexer` to revisit their chunks by making them due.

*Call graph*: called by 1 (_apply); 2 external calls (or_, update).


### `core/src/ufo/runtime/memory.py`

`data_model` · `cross-cutting memory search and browsing`

This file is a small contract between two sides of the system: code that wants to recall memories, and extensions that know how to find them. Without this shared contract, every memory provider would need its own special calling style, and consumers could not swap one provider for another cleanly.

The main result type is `MemoryMatch`, which represents one memory hit in a provider-neutral way. It carries the text snippet to show, the kind of memory it is, and, when available, a durable object reference that can later be opened. It may also include when the memory was created and what subject it belongs to, so callers can judge whether a hit is relevant without opening everything.

`MemorySearchProvider` is a protocol, meaning “anything with these methods counts.” It says a provider must be able to search by query text, list recent memories a set of subjects is allowed to read, and report which memory kinds can be listed. The recent-listing method uses a cursor, like a bookmark in a changing book, so new memories arriving during reading do not make the caller skip or repeat items.

`MemorySearch` is a thin wrapper around one selected provider. It gives the rest of the runtime a stable object to call, while simply passing each request to the chosen provider.

#### Function details

##### `MemorySearchProvider.search`  (lines 37–43)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Defines how a memory provider must search its stored memories using one or more query strings. A caller uses this when it wants relevant memories rather than simply the newest ones.

**Data flow**: The caller provides query text, a `SourceReader` that represents what sources the caller is allowed to read, and optional start and end times. The provider is expected to search only within those boundaries and permissions, then return a tuple of `MemoryMatch` results ready for the caller to display or inject elsewhere.

**Call relations**: This is the provider-side contract that `MemorySearch.search` relies on. When the runtime asks `MemorySearch` to recall memories, that wrapper passes the request here so the selected extension can do the actual lookup.


##### `MemorySearchProvider.list_recent`  (lines 45–51)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Defines how a memory provider must return the most recent readable memory items. This is for browsing recent memories, not searching by similarity or query text.

**Data flow**: The caller gives a set of readable subjects, a maximum number of items, an optional filter for memory kinds, and an optional cursor that marks where the previous page ended. The provider returns a `ListingPage` of `MemoryMatch` items, along with paging information so the caller can continue from the right place later.

**Call relations**: This is the provider-side contract behind `MemorySearch.list_recent`. The wrapper receives a recent-list request from runtime code and hands it to the selected provider, which knows how to page through its own stored memories.


##### `MemorySearchProvider.listable_kinds`  (lines 53–53)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Defines how a provider reports the kinds of memory items it can list. This helps callers show accurate filters, rather than guessing which categories exist.

**Data flow**: It takes no extra input beyond the provider itself. The provider returns a tuple of kind names, such as the provider’s own categories of stored memory items.

**Call relations**: This contract is used through `MemorySearch.listable_kinds`. When a consumer needs to offer listing filters, the wrapper asks the provider for its available kinds and returns them unchanged.


##### `MemorySearch.search`  (lines 62–69)

```
async def search(self, reader: SourceReader, queries: tuple[str, ...], start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Searches memories through the currently selected provider. It exists so callers can use one stable runtime object instead of talking directly to a specific extension.

**Data flow**: The caller passes a `SourceReader`, query strings, and optional time limits. `MemorySearch` forwards those values to its provider’s `search` method, waits for the provider’s answer, and returns the resulting `MemoryMatch` tuple without changing it.

**Call relations**: This is the consumer-facing path into provider search. Runtime code calls `MemorySearch.search`; it delegates to `MemorySearchProvider.search`, where the actual extension-specific searching happens.


##### `MemorySearch.list_recent`  (lines 71–78)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Lists recent memory items through the currently selected provider. It gives callers a single way to browse recent memories, no matter which extension supplies them.

**Data flow**: The caller provides readable subjects, a page size limit, optional kind filters, and an optional cursor. `MemorySearch` passes all of that to the provider’s `list_recent` method and returns the provider’s `ListingPage` directly.

**Call relations**: This is the runtime’s front door for recent-memory browsing. It is called by consumers that need a page of recent items, and it hands the work to `MemorySearchProvider.list_recent` so the chosen provider can read from its own storage.


##### `MemorySearch.listable_kinds`  (lines 80–81)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Returns the memory kinds that the selected provider says can be listed. Callers use this to build filters or menus that match the provider’s real capabilities.

**Data flow**: It reads the provider stored inside `MemorySearch`, asks that provider for its listable kinds, and returns the tuple of kind names unchanged.

**Call relations**: This is the consumer-facing wrapper around `MemorySearchProvider.listable_kinds`. When UI or runtime code needs to know what categories can be browsed, it asks `MemorySearch`, which passes the question to the provider.


### Recall extension surface
Package metadata and event definitions describe the memory extension’s prompt-time recall behavior and its safety limits.

### `extensions/memory/ufo_ext_memory/__init__.py`

`other` · `cross-cutting`

This file does not contain working code. Its job is to label this folder as the memory extension package and document, in one sentence, the main responsibilities of that extension. In plain terms, this extension is about helping the system remember useful information over time. It can store facts so they survive beyond a single interaction, bring relevant memories back when a user submits a prompt, learn or update information when a page changes, and run a background indexing job so stored memories can be searched or retrieved more effectively. Think of it like the title card on a toolbox: it does not use the tools itself, but it tells a reader what kinds of tools they should expect to find inside. Without this file, the package would have less immediate context for newcomers, and depending on the Python packaging setup, imports of this directory as a package could also be affected.


### `extensions/memory/ufo_ext_memory/events.py`

`config` · `cross-cutting during memory recall and event reporting`

The memory extension likely reports important moments as structured events, which are machine-readable messages other parts of the system can log, display, or react to. This file is the shared label sheet for one of those moments: when the system tries to recall relevant memory before forming a response.

The main constant, `MEMORY_RECALL_EVENT`, gives that event a stable name: `memory.pre_response_recall`. Using one shared name matters because event producers and event readers must agree exactly, like using the same tracking number on both sides of a delivery.

The other constants are guardrails. `MAX_RECALLED_MEMORY_IDS` limits how many recalled memory identifiers should be attached to the event, so logs or event payloads do not become too large. `MAX_RECALL_ERROR_CLASS_CHARS` limits the length of an error class name recorded when recall fails, which helps keep event data tidy and predictable.

Nothing runs in this file by itself. It exists so the rest of the memory extension can refer to the same event name and size limits without copying strings and numbers around, reducing mistakes and keeping event output consistent.
