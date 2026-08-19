# Indexing, retrieval, and memory extraction  `stage-14.2`

This stage is the system’s library and research desk. It works behind the scenes before and during answers: saving useful text, finding it again, keeping memories clean, and reaching out to the web when needed. The core indexing code cuts long text into smaller searchable pieces, adds embeddings, which are number patterns that roughly represent meaning, and sends them to a chosen search store. The default index keeps this locally with word and meaning search, while the Turbopuffer extension does the same through an external service and can delete outdated chunks.

Memory has a shared interface so the rest of the system can ask for memories without caring where they live. The memory store saves, searches, and re-indexes them. The condenser turns changed pages into clean facts, merges related facts, and removes near-duplicates. Memory events record when memories were recalled, with size limits.

For outside research, Perplexity provides web search and page fetching. Research tools wrap search, fetch, image, or academic queries into safe JSON results. Source observations save links for later display, and wide_research splits a large research task into parallel smaller ones and writes the combined result.

## Files in this stage

### Search index backends
Built-in and external index implementations store searchable chunks and rely on the shared indexing boundary for chunking and embeddings.

### `extensions/index_default/ufo_ext_index_default.py`

`domain_logic` · `cross-cutting`

This file is the project’s default memory search engine. Every deployment needs some way to store searchable pieces of text, called chunks, and later find the most relevant ones. This backend does that using the database the project is already connected to.

It supports two kinds of search. Lexical search means “find chunks that share words with the question,” like using a book index. Vector search means “find chunks whose numeric embedding is close to the query embedding,” where an embedding is a list of numbers representing the rough meaning of text. The file hides database-specific details so the rest of the system can work with simple project types like Chunk and Hit.

The same class, DefaultIndex, works differently depending on the database. With PostgreSQL, it uses PostgreSQL’s native text search and pgvector, an extension for vector similarity search. With SQLite, it uses FTS5, SQLite’s full-text search feature, and calculates vector similarity in Python by scanning stored embeddings. That SQLite path is simpler and suited to smaller local or development use.

The backend can insert or update chunks, delete all chunks for an owner, check whether an owner has chunks, remove stale chunks after re-indexing, and run both word-based and vector-based searches. Finally, manifest() registers this backend under the name "default" so the extension system can discover and create it.

#### Function details

##### `pgvector_literal`  (lines 35–36)

```
def pgvector_literal(vector: tuple[float, ...]) -> str
```

**Purpose**: Turns a Python tuple of numbers into the bracketed text format PostgreSQL’s pgvector extension expects. It is used when saving or searching embeddings in PostgreSQL.

**Data flow**: It receives a tuple such as numbers from an embedding → converts each value to a floating-point text form → returns one string like "[0.1,0.2,0.3]" that can be passed into SQL.

**Call relations**: DefaultIndex.upsert calls this before storing a chunk embedding in PostgreSQL, and DefaultIndex.vector calls it before asking PostgreSQL to compare a query embedding against stored embeddings.

*Call graph*: called by 2 (upsert, vector).


##### `cosine`  (lines 39–47)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Measures how similar two embeddings are using cosine similarity, a common score for comparing the direction of two lists of numbers. A higher score means the two embeddings point in a more similar direction.

**Data flow**: It receives two equal-length tuples of numbers → calculates the length of each tuple and their dot product → returns a similarity score. If either tuple has zero length in the mathematical sense, it returns 0.0 to avoid dividing by zero.

**Call relations**: DefaultIndex.vector uses this only on the SQLite path, where the database does not do vector comparison for this backend. The function relies on math.sqrt to calculate vector lengths.

*Call graph*: called by 1 (vector); 1 external calls (sqrt).


##### `pack_embedding`  (lines 50–51)

```
def pack_embedding(vector: tuple[float, ...]) -> bytes
```

**Purpose**: Converts an embedding from Python numbers into compact bytes for storage in SQLite. SQLite stores the vector as a blob, which is a raw chunk of bytes.

**Data flow**: It receives a tuple of floats → packs them into a little-endian binary format, meaning a predictable byte order → returns bytes that can be stored in the SQLite chunk table.

**Call relations**: DefaultIndex.upsert calls this when saving a chunk with an embedding to SQLite. It uses Python’s struct.pack to perform the binary conversion.

*Call graph*: called by 1 (upsert); 1 external calls (pack).


##### `unpack_embedding`  (lines 54–55)

```
def unpack_embedding(blob: bytes) -> tuple[float, ...]
```

**Purpose**: Converts an embedding stored as SQLite bytes back into Python numbers. This is needed before Python can compare vectors.

**Data flow**: It receives a byte blob from the database → interprets every four bytes as one float → returns a tuple of floats matching the original stored embedding.

**Call relations**: DefaultIndex.vector calls this on SQLite search results before passing the numbers to cosine for similarity scoring. It uses Python’s struct.unpack to decode the bytes.

*Call graph*: called by 1 (vector); 1 external calls (unpack).


##### `_hit`  (lines 58–67)

```
def _hit(row: sa.RowMapping, score: float) -> Hit
```

**Purpose**: Builds a Hit object, which is the project’s standard search-result shape, from a database row and a score. This keeps PostgreSQL and SQLite query results flowing back to the rest of the system in the same format.

**Data flow**: It receives a database row containing chunk fields plus a separate score → copies the chunk digest, owner information, subject, position, text, and score → returns a Hit object.

**Call relations**: DefaultIndex.lexical and DefaultIndex.vector both call this after database rows have been found and scored. It hands search results back in a database-neutral form.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `DefaultIndex.upsert`  (lines 177–214)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds new chunks to the index or updates existing chunks with the same digest. This is how freshly chunked text becomes searchable.

**Data flow**: It receives a tuple of Chunk objects → if the tuple is empty, it does nothing → otherwise it opens a transaction, checks whether the connection is PostgreSQL or SQLite, and writes each chunk using the matching SQL. For PostgreSQL it formats embeddings with pgvector_literal; for SQLite it stores embeddings with pack_embedding and refreshes the full-text-search table.

**Call relations**: This is called when the system wants indexed content to be present or refreshed. It calls pgvector_literal on the PostgreSQL path and pack_embedding on the SQLite path, then leaves the database with chunk rows ready for later lexical or vector searches.

*Call graph*: calls 2 internal fn (pack_embedding, pgvector_literal).


##### `DefaultIndex.delete`  (lines 216–223)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Removes all indexed chunks belonging to one owner, such as one document or other indexed item. This prevents old content from continuing to appear in search results after it has been removed.

**Data flow**: It receives an IndexScope containing owner kind and owner id → opens a transaction → deletes matching rows. In SQLite it also deletes matching rows from the separate full-text-search table before deleting the main chunk rows.

**Call relations**: Other parts of the indexing flow can call this when an owner should disappear from the index. DefaultIndex.prune also calls it when asked to keep nothing for a scope, because deleting everything is simpler and clearer than pruning item by item.

*Call graph*: called by 1 (prune).


##### `DefaultIndex.has_chunks`  (lines 225–228)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether a given owner already has any chunks in the index. This lets callers decide whether indexing work is needed or whether stored chunks already exist.

**Data flow**: It receives an IndexScope → opens a transaction → asks the chunk table for one matching row → returns true if a row exists and false if none exists.

**Call relations**: This method is a small read-side helper for the wider indexing process. It does not call local helper functions; it simply uses the shared transaction opener and the common chunk table.


##### `DefaultIndex.prune`  (lines 230–244)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Removes stale chunks for one owner while keeping a specified set of current chunk digests. This matters after re-chunking, because old pieces that no longer match the source should not remain searchable.

**Data flow**: It receives an IndexScope and a keep set of chunk digests → if the keep set is empty, it deletes the whole scope → otherwise it opens a transaction and deletes all chunks for that owner whose digest is not in the keep set. In SQLite it also removes the matching full-text-search rows.

**Call relations**: This fits into the refresh path after new chunks have been written or calculated. When there is nothing to keep, it hands off to DefaultIndex.delete; otherwise it runs dialect-specific pruning SQL directly.

*Call graph*: calls 1 internal fn (delete).


##### `DefaultIndex.lexical`  (lines 246–282)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches for chunks by word overlap. This is useful when the exact words in a question or memory cue should pull back chunks containing similar words.

**Data flow**: It receives a query string, a set of allowed subjects, an owner kind, and a result limit → if there are no subjects, or if the query has no usable terms, it returns no results → otherwise it opens a transaction and runs PostgreSQL text search or SQLite FTS5 search. It converts each matching row into a Hit with its word-match score.

**Call relations**: Search orchestration calls this when it wants word-based evidence. After the database ranks matching rows, this method calls _hit so callers receive normal Hit objects rather than raw database rows.

*Call graph*: calls 1 internal fn (_hit).


##### `DefaultIndex.vector`  (lines 284–317)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches for chunks by embedding similarity, meaning it finds text whose numeric representation is close to the query’s numeric representation. This helps find related content even when the exact same words are not used.

**Data flow**: It receives a query embedding, allowed subjects, an owner kind, and a result limit → if the embedding or subjects are empty, it returns no results → otherwise it opens a transaction. PostgreSQL compares vectors in SQL using pgvector_literal; SQLite loads candidate rows, unpacks stored embeddings, scores them in Python with cosine, sorts them, and returns the top hits.

**Call relations**: Search orchestration calls this when it wants meaning-based retrieval. It uses pgvector_literal on PostgreSQL, and on SQLite it uses unpack_embedding and cosine before turning the best rows into Hit objects with _hit.

*Call graph*: calls 4 internal fn (_hit, cosine, pgvector_literal, unpack_embedding).


##### `manifest`  (lines 320–330)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the project’s plugin system and registers the index backend name "default". Without this, the core system would not know how to create DefaultIndex as the built-in backend.

**Data flow**: It takes no input → creates a Manifest containing the extension name, version, and one IndexBackendSpec → returns that manifest. The backend spec includes a factory that builds DefaultIndex using the transaction opener supplied by the core runtime.

**Call relations**: The extension loader calls this during discovery or startup. It constructs Manifest and IndexBackendSpec objects so the broader system can later instantiate DefaultIndex when the default index backend is requested.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/turbopuffer/ufo_ext_turbopuffer.py`

`io_transport` · `startup and indexing/search request handling`

UFO needs a place to put searchable memory chunks. This file provides one option: Turbopuffer, an external hosted index service reached over HTTP. Think of it like hiring a specialist librarian: UFO sends the librarian small text cards with labels and embeddings, and later asks for the cards that best match a question.

The file registers itself through a manifest so the core system can choose it when `memory.index_backend` is set to `turbopuffer`. At runtime it builds a `TurbopufferIndex`, which keeps an HTTP client and reads the user’s Turbopuffer API key from the credential store before each request.

Chunks are stored in one Turbopuffer namespace per workspace. Each chunk has an id, its vector embedding, and plain attributes such as owner type, owner id, subject, order, and text. Searching has two paths. The vector path finds chunks close in meaning using approximate nearest-neighbor search. The lexical path uses BM25, a word-matching ranking method that favors documents containing important query terms. Both paths filter results so searches only see the right owner kind and subject set.

The file also knows how to remove all chunks for a scope, or prune only old chunks not in a keep-set after content is re-chunked. Without this file, UFO could not use Turbopuffer for memory indexing, search, or cleanup.

#### Function details

##### `turbopuffer_id`  (lines 48–54)

```
def turbopuffer_id(chunk_digest: str) -> str
```

**Purpose**: Converts UFO’s chunk digest into a document id that Turbopuffer can store safely. Standard SHA-256 digests are shortened into base64url text so they stay within Turbopuffer’s id length limits; other ids are left alone.

**Data flow**: It receives a chunk digest string. If the string looks like `sha256:` followed by 64 hex characters, it turns the raw bytes into a shorter URL-safe base64 id and removes padding; otherwise it returns the original string unchanged.

**Call relations**: When chunks are written, `upsert_body` uses this to prepare document ids for Turbopuffer. When chunks are removed, `TurbopufferIndex.delete` and `TurbopufferIndex.prune` use the same conversion so they delete the exact ids that were stored.

*Call graph*: called by 3 (delete, prune, upsert_body); 1 external calls (urlsafe_b64encode).


##### `chunk_digest_from_id`  (lines 57–66)

```
def chunk_digest_from_id(chunk_id: str) -> str
```

**Purpose**: Converts a Turbopuffer document id back into UFO’s original chunk digest format when possible. This keeps search results and exported rows speaking the same id language as the rest of UFO.

**Data flow**: It receives an id string from Turbopuffer. If it has the expected shortened base64url length, it tries to decode it into bytes and returns a `sha256:` hex digest; if decoding fails or the length does not match, it returns the id unchanged.

**Call relations**: `hit_from_row` uses this when turning search rows into `Hit` objects. `_scope_chunks` uses it when listing stored chunks for delete or prune operations, so cleanup decisions can compare against normal UFO chunk digests.

*Call graph*: called by 2 (_scope_chunks, hit_from_row); 1 external calls (urlsafe_b64decode).


##### `upsert_body`  (lines 69–85)

```
def upsert_body(chunks: tuple[Chunk, ...]) -> dict[str, Any]
```

**Purpose**: Builds the HTTP request body used to add or update a batch of chunks in Turbopuffer. It arranges chunk data in the column-based format Turbopuffer expects.

**Data flow**: It receives a tuple of `Chunk` objects. It extracts ids, embeddings, owner labels, subjects, order numbers, and text into parallel lists, sets cosine distance as the vector comparison method, and marks the text field as searchable for full-text matching.

**Call relations**: `TurbopufferIndex.upsert` calls this for each write batch. Inside, it calls `turbopuffer_id` so the ids in the write request match Turbopuffer’s constraints and later delete requests.

*Call graph*: calls 1 internal fn (turbopuffer_id); called by 1 (upsert).


##### `bm25_query`  (lines 88–104)

```
def bm25_query(text: str) -> str
```

**Purpose**: Cleans and shortens a text query before sending it to Turbopuffer’s BM25 word search. This prevents meaningless punctuation-only queries from matching everything and prevents over-long queries from being rejected.

**Data flow**: It receives raw query text. It keeps only tokens containing a run of at least two letters or digits, joins them back into a query, and if the encoded query is over Turbopuffer’s byte limit, clips it while trying not to leave a half term at the end.

**Call relations**: `TurbopufferIndex.lexical` calls this before running a word-based search. If this function returns an empty string, lexical search stops and the broader search can rely on the vector path instead.

*Call graph*: called by 1 (lexical).


##### `query_filters`  (lines 107–111)

```
def query_filters(owner_kind: str, subjects: frozenset[str]) -> list[Any]
```

**Purpose**: Builds the Turbopuffer filter used during search so results come only from the intended kind of owner and subject set. This is the guardrail that keeps one category of memory from leaking into another search.

**Data flow**: It receives an owner kind and a frozen set of subjects. It returns a Turbopuffer filter expression requiring the owner kind to match exactly and the subject to be one of the requested subjects.

**Call relations**: `TurbopufferIndex._query` calls this whenever lexical or vector search is run. The resulting filter is sent along with the Turbopuffer query request.

*Call graph*: called by 1 (_query).


##### `scope_filters`  (lines 114–121)

```
def scope_filters(scope: IndexScope, after_id: str | None) -> list[Any]
```

**Purpose**: Builds the Turbopuffer filter used when looking at all chunks that belong to one source scope. A scope means a specific owner kind plus owner id, such as all chunks from one document-like source.

**Data flow**: It receives an `IndexScope` and optionally an `after_id` cursor. It returns a filter requiring the matching owner kind and owner id, and if `after_id` is present, only ids greater than that value for paging through results.

**Call relations**: `TurbopufferIndex.has_chunks` uses this to ask whether a scope has at least one stored chunk. `_scope_chunks` uses it repeatedly to page through all chunks in that scope for deletion or pruning.

*Call graph*: called by 2 (_scope_chunks, has_chunks).


##### `hit_from_row`  (lines 124–133)

```
def hit_from_row(row: dict[str, Any], score: float) -> Hit
```

**Purpose**: Turns one Turbopuffer result row into UFO’s standard `Hit` object. A hit is the search result shape the rest of the system understands.

**Data flow**: It receives a row dictionary from Turbopuffer and a score chosen by the caller. It converts the id back into a chunk digest, reads the stored attributes, casts them to the expected types, and returns a `Hit` containing text, ownership data, order, and score.

**Call relations**: `TurbopufferIndex.lexical` uses this for BM25 results, and `TurbopufferIndex.vector` uses it for vector results. It calls `chunk_digest_from_id` so returned hits carry the original UFO-style digest.

*Call graph*: calls 1 internal fn (chunk_digest_from_id); called by 2 (lexical, vector); 1 external calls (__init__).


##### `vector_score`  (lines 136–142)

```
def vector_score(row: dict[str, Any], position: int, total: int) -> float
```

**Purpose**: Computes a useful score for a vector search result. It turns Turbopuffer’s distance value into a higher-is-better score, which is easier for the recall system to combine with other rankings.

**Data flow**: It receives a result row, that row’s position in the returned list, and the total number of rows. If Turbopuffer supplied a cosine distance, it returns `1 - distance`; otherwise it falls back to a descending rank score based on position.

**Call relations**: `TurbopufferIndex.vector` calls this before converting rows into hits. The score it produces is passed into `hit_from_row` so each returned hit has a relevance value.

*Call graph*: called by 1 (vector).


##### `TurbopufferIndex.upsert`  (lines 156–167)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds or updates chunks in Turbopuffer. This is used when UFO has new searchable memory content or refreshed chunk content to store.

**Data flow**: It receives a tuple of chunks. It ignores chunks without embeddings, gets authorization headers, splits the rest into safe write-sized batches, builds each request body, posts it to the workspace namespace, and raises an error if Turbopuffer rejects the request.

**Call relations**: The indexing flow calls this when content should be written to the backend. It relies on `_auth` for the Bearer token, `_path` for the namespace URL, and `upsert_body` for Turbopuffer’s expected payload format.

*Call graph*: calls 3 internal fn (_auth, _path, upsert_body).


##### `TurbopufferIndex.delete`  (lines 169–177)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Deletes every stored chunk in a given scope. This is used when a source owner should no longer have any indexed memory.

**Data flow**: It receives an `IndexScope`. It gets authorization headers, lists all chunks currently stored for that scope, converts their digests into Turbopuffer ids, sends delete requests in batches, and raises an error on failed HTTP responses.

**Call relations**: Cleanup code calls this when an owner’s indexed content must be removed. It uses `_scope_chunks` to discover what exists, `turbopuffer_id` to address the stored documents, plus `_auth` and `_path` to send the delete requests.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.prune`  (lines 179–189)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Deletes only the chunks in a scope that are no longer in a supplied keep-set. This is useful after re-chunking content, where old chunks should disappear but unchanged chunks can remain.

**Data flow**: It receives an `IndexScope` and a set of chunk digests to keep. It lists existing chunks for the scope, filters out the ones whose digests are still wanted, converts the rest into Turbopuffer ids, and sends batched delete requests.

**Call relations**: Re-indexing flows call this after deciding which chunks should survive. It uses `_scope_chunks` to inspect current storage, `turbopuffer_id` to form delete ids, and `_auth` and `_path` to talk to Turbopuffer.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.has_chunks`  (lines 191–197)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether a given scope already has any chunks stored in Turbopuffer. This lets callers make quick decisions without exporting the whole scope.

**Data flow**: It receives an `IndexScope`. It sends a tiny query asking for at most one id in that scope; if the namespace does not exist it returns false, otherwise it returns true when the response contains at least one row.

**Call relations**: Indexing or cleanup logic can call this before doing heavier work. It uses `scope_filters` to limit the check, `_auth` for credentials, and `_path` for the workspace namespace query URL.

*Call graph*: calls 3 internal fn (_auth, _path, scope_filters).


##### `TurbopufferIndex.lexical`  (lines 199–208)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Runs a word-based search over stored chunk text. It is meant for cases where exact or important terms in the query should help find relevant memory.

**Data flow**: It receives raw query text, subjects, owner kind, and a result limit. It cleans the query with `bm25_query`, stops early if there is no useful text or no subjects, runs a BM25 query, then converts each row into a `Hit` with a rank-based score.

**Call relations**: The recall/search layer calls this for the lexical leg of search. It delegates the actual HTTP query to `_query` and uses `hit_from_row` to return results in UFO’s standard format.

*Call graph*: calls 3 internal fn (_query, bm25_query, hit_from_row).


##### `TurbopufferIndex.vector`  (lines 210–219)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Runs a meaning-based search using an embedding vector. This finds chunks whose stored embeddings are close to the query embedding, even if the words differ.

**Data flow**: It receives an embedding, subjects, owner kind, and a result limit. If the embedding or subjects are empty it returns no hits; otherwise it asks Turbopuffer for approximate nearest neighbors, scores each returned row, drops non-positive scores, and returns `Hit` objects.

**Call relations**: The recall/search layer calls this for the vector leg of search. It uses `_query` to send the ANN request, `vector_score` to make scores higher-is-better, and `hit_from_row` to shape rows for the rest of UFO.

*Call graph*: calls 3 internal fn (_query, hit_from_row, vector_score).


##### `TurbopufferIndex._query`  (lines 221–234)

```
async def _query(self, rank_by: list[Any], owner_kind: str, subjects: frozenset[str], limit: int) -> list[dict[str, Any]]
```

**Purpose**: Sends the shared Turbopuffer query request used by both lexical and vector search. It centralizes the common pieces: ranking rule, filters, attributes to return, and missing-namespace behavior.

**Data flow**: It receives a `rank_by` instruction, owner kind, subject set, and limit. It builds a request body, adds the owner/subject filters, posts to the namespace query endpoint, returns an empty list if the namespace is missing, or returns the response rows after checking for HTTP errors.

**Call relations**: `TurbopufferIndex.lexical` and `TurbopufferIndex.vector` both call this rather than building separate HTTP requests. It uses `query_filters`, `_auth`, and `_path` to assemble a correctly scoped authenticated request.

*Call graph*: calls 3 internal fn (_auth, _path, query_filters); called by 2 (lexical, vector).


##### `TurbopufferIndex._scope_chunks`  (lines 236–264)

```
async def _scope_chunks(self, scope: IndexScope, headers: dict[str, str]) -> list[Chunk]
```

**Purpose**: Lists all chunks stored in Turbopuffer for one scope. It is mainly a helper for safe deletion and pruning, where the code must know exactly which stored ids belong to that owner.

**Data flow**: It receives an `IndexScope` and already-built authorization headers. It repeatedly queries Turbopuffer in id order, converts each row into a lightweight `Chunk` without an embedding, appends it to a list, and continues until a page has fewer rows than the page size.

**Call relations**: `TurbopufferIndex.delete` and `TurbopufferIndex.prune` call this before deciding what to remove. It uses `scope_filters` for paging, `_path` for the query endpoint, and `chunk_digest_from_id` so returned chunks use UFO’s normal digest format.

*Call graph*: calls 3 internal fn (_path, chunk_digest_from_id, scope_filters); called by 2 (delete, prune); 1 external calls (__init__).


##### `TurbopufferIndex._auth`  (lines 266–268)

```
async def _auth(self) -> dict[str, str]
```

**Purpose**: Builds the HTTP authorization header for Turbopuffer requests. It reads the API key at request time from UFO’s credential access layer.

**Data flow**: It reads the `turbopuffer_api_key` credential slot asynchronously. It returns a dictionary containing an `Authorization` header with a Bearer token.

**Call relations**: Every method that talks directly to Turbopuffer calls this: writes, deletes, existence checks, and shared search queries. This keeps credential lookup in one place instead of scattering API-key handling across the file.

*Call graph*: called by 5 (_query, delete, has_chunks, prune, upsert).


##### `TurbopufferIndex._path`  (lines 270–271)

```
def _path(self, suffix: str='') -> str
```

**Purpose**: Builds the Turbopuffer URL path for the current workspace namespace. Namespacing keeps one workspace’s indexed chunks separate from another’s.

**Data flow**: It receives an optional suffix such as `/query`. It combines the fixed namespace prefix, the workspace id from credentials, and the suffix into a path like `/namespaces/ufo-<workspace>/query`.

**Call relations**: All HTTP operations call this when they need the right namespace endpoint. Search helpers, writes, deletes, pruning, scope export, and existence checks all use it so they address the same workspace-specific index.

*Call graph*: called by 6 (_query, _scope_chunks, delete, has_chunks, prune, upsert).


##### `manifest`  (lines 274–294)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to UFO so the core system can discover and build the Turbopuffer index backend. It also declares the credential slot the user must provide.

**Data flow**: It creates and returns a `Manifest` containing the extension name, version, required Turbopuffer API-key credential, and an index backend specification. The backend factory builds a `TurbopufferIndex` with the current credential context and an async HTTP client pointed at Turbopuffer’s base URL.

**Call relations**: UFO calls this during extension discovery or startup. The returned manifest is how the `turbopuffer` backend becomes selectable, and the factory is what creates the live index object used later for upsert, search, delete, and prune operations.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `core/src/ufo/indexing.py`

`domain_logic` · `cross-cutting indexing and retrieval preparation`

Search works better when long documents are broken into smaller, meaningful pieces. This file provides that shared chopping and indexing workflow. It does not store anything itself. Instead, it defines simple data shapes like Chunk, Hit, and IndexScope, plus two promises that outside components must fulfill: IndexBackend for saving and searching chunks, and EmbedClient for turning text into numeric vectors that represent meaning.

The main worker is TextChunker. It takes a body of text and splits it into pieces near a target size. It tries natural breaks first, such as paragraphs, lines, and sentences, before falling back to whitespace or character-based cutting. It also adds a little overlap between neighboring chunks, like repeating the last few lines of one page at the top of the next, so a search does not lose context at a boundary.

The shared indexing workflow is chunk_embed_upsert. It chunks one owner’s text, asks the embedding service for vectors, writes the finished chunks through the backend, and then removes old chunks for that owner that are no longer produced. That last pruning step matters: without it, edited or emptied text could leave stale search results behind.

#### Function details

##### `IndexBackend.upsert`  (lines 68–68)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This is the promise that a search backend can save or replace a group of chunks. It is used when fresh text chunks and their embeddings are ready to become searchable.

**Data flow**: It receives a tuple of Chunk objects, each carrying text, ownership details, and usually an embedding. The backend implementation is expected to write them into its storage so the same chunk digest updates the same stored record rather than creating a duplicate. It returns nothing, but the index is changed.

**Call relations**: chunk_embed_upsert calls this after it has split text and received embeddings. This file only defines the method shape; a real backend elsewhere supplies the storage behavior.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.delete`  (lines 70–70)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This is the promise that a search backend can remove all chunks belonging to one owner. It is useful when an indexed item or page is deleted entirely.

**Data flow**: It receives an IndexScope, which names the owner kind and owner id. The backend implementation uses that scope to find matching chunks and delete them. It returns nothing, but stored index data is removed.

**Call relations**: No function in this file calls it directly. It exists as part of the backend contract so higher-level code can ask any backend to clean out one owner in the same way.


##### `IndexBackend.prune`  (lines 72–72)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This is the promise that a backend can remove old chunks for one owner while keeping a known set of current chunks. It prevents edited text from leaving outdated search results behind.

**Data flow**: It receives an IndexScope naming the owner and a set of chunk digests to keep. The backend compares its stored chunks for that owner against the keep set, deletes anything not in the set, and returns nothing. If the keep set is empty, all chunks for that owner should be removed.

**Call relations**: chunk_embed_upsert calls this at the end of every indexing pass. After new chunks are written, prune is the cleanup step that discards chunks from older versions of the same owner.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.has_chunks`  (lines 74–74)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: This is the promise that a backend can answer whether an owner already has indexed chunks. It lets other code avoid unnecessary work or decide whether indexing is missing.

**Data flow**: It receives an IndexScope naming the owner to check. The backend looks in its storage for chunks under that owner and returns a true-or-false answer.

**Call relations**: No function in this file calls it directly. It is part of the common backend interface used by indexing orchestration elsewhere.


##### `IndexBackend.lexical`  (lines 76–78)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the promise that a backend can search by the actual words in a query. Lexical search is the traditional kind of search where matching terms in the text matter.

**Data flow**: It receives the query text, a set of allowed subjects, an owner kind to search within, and a maximum number of results. The backend searches matching indexed chunks and returns Hit objects with text and scores.

**Call relations**: No function here calls it directly. It belongs to the retrieval contract so callers can use any backend for word-based search without knowing how that backend stores or ranks text.


##### `IndexBackend.vector`  (lines 80–82)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the promise that a backend can search by meaning using an embedding, which is a list of numbers representing the sense of some text. It supports semantic search, where results can match the idea of a query even if the exact words differ.

**Data flow**: It receives a query embedding, allowed subjects, an owner kind, and a result limit. The backend compares that vector with stored chunk vectors and returns the closest Hit objects with scores.

**Call relations**: No function in this file calls it directly. It is part of the retrieval seam used by code that has already embedded a query and wants meaning-based results.


##### `EmbedClient.embed`  (lines 86–86)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This is the promise that an embedding provider can turn text strings into numeric vectors. Those vectors are what make meaning-based search possible.

**Data flow**: It receives a tuple of text snippets. The implementation sends or computes those snippets through an embedding model and returns one vector for each input text, in the same order.

**Call relations**: chunk_embed_upsert calls this after TextChunker has produced chunks. This file defines the expected behavior, while a real embedding client elsewhere performs the model call.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `chunk_embed_upsert`  (lines 89–114)

```
async def chunk_embed_upsert(index: IndexBackend, embed: EmbedClient, chunker: 'TextChunker', owner_kind: str, owner_id: str, subject: str, body: str) -> None
```

**Purpose**: This is the shared indexing recipe for one piece of text. It breaks the text into chunks, embeds each chunk, saves the embedded chunks, and removes stale chunks from earlier versions.

**Data flow**: It receives an index backend, an embedding client, a chunker, owner information, a subject, and the body text. First it asks the chunker for Chunk objects without embeddings. If any chunks exist, it sends their text to the embedding client, pairs each returned vector with the matching chunk, and upserts them into the backend. Finally it builds an IndexScope for the owner and asks the backend to keep only the chunk digests from this run.

**Call relations**: This function ties the file’s main pieces together. It calls EmbedClient.embed for vectors, IndexBackend.upsert to store current chunks, and IndexBackend.prune to remove leftovers; it relies on TextChunker.chunk to decide what the current chunks are.

*Call graph*: calls 3 internal fn (embed, prune, upsert); 2 external calls (__init__, replace).


##### `TextChunker.chunk`  (lines 123–134)

```
def chunk(self, text: str, owner_kind: str, owner_id: str, subject: str) -> tuple[Chunk, ...]
```

**Purpose**: This turns raw text into Chunk records that can later be embedded and indexed. It adds ownership information and a stable digest to each piece so the search backend can identify it.

**Data flow**: It receives text plus owner kind, owner id, and subject. It asks _slices to split the text into plain string pieces, numbers those pieces in order, computes a digest for each one, and returns a tuple of Chunk objects with empty embeddings.

**Call relations**: chunk_embed_upsert uses this as the first step in indexing. Internally it delegates splitting to _slices and identity creation to _digest, then constructs Chunk values for the rest of the indexing flow.

*Call graph*: calls 2 internal fn (_digest, _slices); 1 external calls (__init__).


##### `TextChunker._slices`  (lines 136–144)

```
def _slices(self, text: str) -> list[str]
```

**Purpose**: This decides the overall cutting plan for a body of text. It keeps short text as one piece, but breaks long text into readable chunks with size limits and overlap.

**Data flow**: It receives raw text. Empty or whitespace-only text becomes an empty list. Short enough text is just trimmed and character-capped. Longer text is recursively split at natural boundaries, merged back into useful-sized chunks, given overlap for context, then capped by maximum character length. It returns a list of text pieces.

**Call relations**: TextChunker.chunk calls this before wrapping pieces as Chunk objects. It coordinates the helper methods _count_words, _recursive_split, _greedy_merge, _apply_overlap, and _cap_by_chars in that order.

*Call graph*: calls 5 internal fn (_apply_overlap, _cap_by_chars, _count_words, _greedy_merge, _recursive_split); called by 1 (chunk).


##### `TextChunker._count_words`  (lines 147–153)

```
def _count_words(text: str) -> int
```

**Purpose**: This estimates how large a piece of text is for chunking purposes. It treats dense Chinese, Japanese, or Korean text differently because those languages may not use spaces between words.

**Data flow**: It receives text and removes whitespace to see how much visible content exists. If there is no content, it returns 0. If enough characters are from CJK scripts, it counts non-whitespace characters as the size; otherwise it counts runs of non-space text like ordinary words.

**Call relations**: _slices uses it to decide whether text is already small enough. _recursive_split uses it to decide whether a piece still needs more splitting, and _greedy_merge uses it to decide whether neighboring pieces can safely be joined.

*Call graph*: called by 3 (_greedy_merge, _recursive_split, _slices); 1 external calls (sub).


##### `TextChunker._cap_by_chars`  (lines 155–167)

```
def _cap_by_chars(self, text: str) -> list[str]
```

**Purpose**: This enforces a hard maximum character length for each chunk. It is a safety net for very long text pieces, even if word counting thought they were acceptable.

**Data flow**: It receives one text piece. If it is within the maximum character count, it returns that piece as a one-item list, unless it is empty. If it is too long, it cuts it into character windows with a small overlap so nearby context is not completely lost. It returns the resulting list of non-empty pieces.

**Call relations**: _slices calls this for short text and again after overlap is added to longer text. It is the last guard that keeps chunks from becoming too large for downstream embedding or storage.

*Call graph*: called by 1 (_slices).


##### `TextChunker._recursive_split`  (lines 169–181)

```
def _recursive_split(self, text: str, level: int) -> list[str]
```

**Purpose**: This breaks long text using increasingly smaller natural separators. It tries to preserve human-readable boundaries before resorting to blunt word-based splitting.

**Data flow**: It receives text and a delimiter level. At each level it tries separators such as paragraph breaks, line breaks, sentence punctuation, or commas. If a split produces pieces, it keeps pieces that are small enough and recursively splits pieces that are still too large. If no delimiter level remains, it falls back to _split_on_whitespace.

**Call relations**: _slices calls this for text that exceeds the target size. It calls _split_at_delimiters to cut by punctuation or spacing, _count_words to test each piece, and _split_on_whitespace when natural separators are not enough.

*Call graph*: calls 3 internal fn (_count_words, _split_at_delimiters, _split_on_whitespace); called by 1 (_slices).


##### `TextChunker._split_at_delimiters`  (lines 184–197)

```
def _split_at_delimiters(text: str, delimiters: tuple[str, ...]) -> list[str]
```

**Purpose**: This cuts text at the earliest matching delimiter from a given set, keeping the delimiter with the piece before it. That helps preserve punctuation and paragraph markers with the text they belong to.

**Data flow**: It receives text and a tuple of delimiters. It repeatedly searches the remaining text for the earliest delimiter occurrence, emits everything through that delimiter as one piece, then continues with the rest. If no delimiter is found, it emits the remaining text. Blank pieces are filtered out.

**Call relations**: _recursive_split calls this at each delimiter level. It is the mechanical cutter used when the chunker is trying natural boundaries first.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._split_on_whitespace`  (lines 199–215)

```
def _split_on_whitespace(self, text: str) -> list[str]
```

**Purpose**: This is the fallback splitter when punctuation and paragraph boundaries are not enough. It cuts text into groups based on whitespace-separated words, or by characters if there are no usable word breaks.

**Data flow**: It receives text. If normal word runs are found, it joins them into groups of about the target word count. If there are no words, or there is one extremely long run, it cuts by character count based on the target size. It returns only non-empty pieces.

**Call relations**: _recursive_split calls this when all delimiter levels have been exhausted. It is the chunker’s last attempt to create manageable pieces from difficult input.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._greedy_merge`  (lines 217–231)

```
def _greedy_merge(self, pieces: list[str]) -> list[str]
```

**Purpose**: This joins small neighboring pieces back together so the final chunks are not too tiny. It is called “greedy” because it keeps adding the next piece as long as the combined result stays under a generous size limit.

**Data flow**: It receives a list of pieces from the recursive splitter. Starting with the first piece, it tests whether adding the next piece would keep the combined text within about one and a half times the target word count. If so, it joins them; otherwise it saves the current chunk and starts a new one. It returns the merged list.

**Call relations**: _slices calls this after _recursive_split. It uses _count_words to judge combined size and math.ceil to calculate the allowed upper bound.

*Call graph*: calls 1 internal fn (_count_words); called by 1 (_slices); 1 external calls (ceil).


##### `TextChunker._apply_overlap`  (lines 233–239)

```
def _apply_overlap(self, chunks: list[str]) -> list[str]
```

**Purpose**: This adds a little context from the end of each chunk to the start of the next chunk. The goal is to keep ideas that cross a boundary searchable together.

**Data flow**: It receives a list of chunks. If there is only one chunk or overlap is disabled, it returns the list unchanged. Otherwise it keeps the first chunk as-is, then prefixes each later chunk with trailing context taken from the previous chunk. It returns the overlapped chunks.

**Call relations**: _slices calls this after merging pieces. It uses _trailing_context to choose the repeated context and itertools.pairwise to walk through previous-and-current chunk pairs.

*Call graph*: calls 1 internal fn (_trailing_context); called by 1 (_slices); 1 external calls (pairwise).


##### `TextChunker._trailing_context`  (lines 241–251)

```
def _trailing_context(self, text: str) -> str
```

**Purpose**: This chooses the text to repeat from the end of a previous chunk. It tries to give the next chunk enough background without repeating more than needed.

**Data flow**: It receives one chunk of text and finds its word-like runs. If there are not enough words to exceed the overlap size, it returns an empty string because repeating the whole chunk would be wasteful. Otherwise it takes the last overlap-sized group of words and, when possible, trims it to start after an early sentence boundary. It returns that context string.

**Call relations**: _apply_overlap calls this for each previous chunk while building overlapped chunks. It is the small helper that makes overlap more sentence-aware instead of blindly copying words.

*Call graph*: called by 1 (_apply_overlap).


##### `TextChunker._digest`  (lines 254–256)

```
def _digest(owner_kind: str, owner_id: str, subject: str, ordinal: int, text: str) -> str
```

**Purpose**: This creates a stable fingerprint for a chunk. The fingerprint lets the backend recognize the same chunk on repeated indexing runs.

**Data flow**: It receives owner kind, owner id, subject, ordinal position, and chunk text. It joins those fields with a separator that is unlikely to appear by accident, hashes the result with SHA-256, and returns the hash string with a sha256 prefix.

**Call relations**: TextChunker.chunk calls this once for each text slice. chunk_embed_upsert later uses these digests indirectly when it asks the backend to upsert current chunks and prune anything not produced in the latest run.

*Call graph*: called by 1 (chunk); 1 external calls (sha256).


### Memory extraction and recall
The memory extension extracts and cleans facts from changing pages, stores and searches them, and exposes shared recall contracts and events.

### `extensions/memory/ufo_ext_memory/condenser.py`

`domain_logic` · `page-change processing and periodic background cleanup`

The memory system can collect many small pieces of information over time: page contents, repeated statements, and separate facts that really belong together. This file is the cleanup and distillation workshop for that material. Without it, synced pages would not become durable facts, old facts would pile up instead of turning into broader summaries, and repeated statements would keep appearing as separate live memories.

There are three main workers. FactDeriver reacts to source page changes. It asks a language model to read page text and record only useful standalone facts, then stores those facts and retires older facts from the same page revision when replacements were successfully written. MemoryConsolidator is a periodic background job. It looks for older, live fact memories, groups ones about the same subject, compares their embeddings (number lists that represent meaning), and asks a model to combine related facts into one semantic summary. MemoryDeduper is another periodic job. It finds live tool-written memories that say nearly the same thing, keeps the newest copy, and marks the older copies as superseded.

A key safety pattern runs through the file: model calls and embedding work happen before database write transactions whenever possible, and every write re-checks that the rows are still current. This avoids replacing memories based on stale information.

#### Function details

##### `FactDeriver.apply`  (lines 138–152)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: This is the entry point for turning changed source pages into memory facts. It decides which changed pages should be examined, retires facts for pages that disappeared, and sends suitable live pages through fact extraction in small batches.

**Data flow**: It receives a group of page changes. It asks the memory store which pages are still live, immediately retires page-derived facts for pages no longer present, filters out tombstones and pages with very short bodies, then processes the remaining pages in fixed-size batches. For each batch, it receives back the pages where new facts were actually committed and only then retires older facts from those pages.

**Call relations**: A page-change runner calls this when it delivers changed pages. This function breaks the work into safe batches and hands each batch to FactDeriver._derive, then uses the result to tell the store exactly which page facts can be superseded.

*Call graph*: calls 1 internal fn (_derive); 1 external calls (batched).


##### `FactDeriver._derive`  (lines 154–192)

```
async def _derive(self, pages: tuple[PageChange, ...]) -> tuple[PageChange, ...]
```

**Purpose**: This commits extracted facts for a batch of pages, but only if the pages are still exactly the same versions that were originally delivered. It protects the system from writing facts for stale page text.

**Data flow**: It receives page changes that looked eligible. It re-reads the current page states, keeps only pages whose subject and revision still match, asks FactDeriver._extract to pull facts from those pages, filters out low-notability or mismatched facts, checks each page again just before writing, then commits each accepted fact as a MemoryWrite. It returns only the pages for which at least one fact was successfully stored.

**Call relations**: FactDeriver.apply calls this for each bounded batch. This function calls FactDeriver._extract for the model pass, then hands accepted facts to the memory store through MemoryWrite objects so apply can later retire replaced page facts.

*Call graph*: calls 1 internal fn (_extract); called by 1 (apply); 1 external calls (__init__).


##### `FactDeriver._extract`  (lines 194–248)

```
async def _extract(self, pages: tuple[PageChange, ...]) -> tuple[ExtractedFact, ...]
```

**Purpose**: This asks the configured language model to read a small group of pages and return structured facts. It uses a tool-style response so facts come back as validated data rather than text that must be guessed or parsed from prose.

**Data flow**: It receives current pages. It builds a compact JSON payload containing page IDs and clipped page bodies, creates a model request with a forced record_facts tool, sends it to the model, finds the tool call in the reply, and validates each returned fact. It returns the valid extracted facts and raises an error if the model did not make the expected tool call or did not provide a facts list.

**Call relations**: FactDeriver._derive calls this when it needs facts for a page batch. It constructs Message, ToolSchema, and ModelRequest objects for the model API, and its returned facts are later committed by _derive.

*Call graph*: called by 1 (_derive); 4 external calls (__init__, __init__, __init__, dumps).


##### `cosine`  (lines 251–259)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: This measures how close two embeddings are in meaning. An embedding is a list of numbers representing text; cosine similarity compares the direction of those number lists, where higher means more alike.

**Data flow**: It receives two number tuples. It computes their dot product and their lengths, returns 0.0 if either vector has no length, otherwise returns the cosine similarity score. It does not change any outside state.

**Call relations**: Both MemoryConsolidator._clusters and MemoryDeduper._clusters use this as their basic similarity test. It is the shared ruler that decides whether facts or copies are close enough to group together.

*Call graph*: called by 2 (_clusters, _clusters); 2 external calls (sqrt, sumprod).


##### `MemoryConsolidator.run`  (lines 289–298)

```
async def run(self) -> None
```

**Purpose**: This is the periodic job that turns older related facts into compact semantic summaries. It keeps the memory store from becoming a long list of overlapping facts when a smaller summary would serve recall better.

**Data flow**: It starts with no direct input beyond the consolidator's configured store, embedder, workspace, and optional model. If there is no model, it exits. Otherwise it loads aged fact candidates, groups them by subject, embeds each large enough group, clusters similar facts in a worker thread, and consolidates each cluster that has enough members.

**Call relations**: A scheduler calls this periodically. It coordinates the whole consolidation flow by calling _aged_facts, _buckets, _embed, _clusters, and _consolidate in order.

*Call graph*: calls 4 internal fn (_aged_facts, _buckets, _consolidate, _embed); 1 external calls (to_thread).


##### `MemoryConsolidator._aged_facts`  (lines 300–330)

```
async def _aged_facts(self) -> tuple[_AgedFact, ...]
```

**Purpose**: This finds old live fact memories that are eligible to be merged into summaries. It deliberately ignores page-derived facts and already-superseded facts.

**Data flow**: It reads the current time, calculates an age cutoff, queries the memory_item table for this workspace's old live fact rows with no source page, and limits the scan size. It turns each database row into an _AgedFact record and returns them as a tuple.

**Call relations**: MemoryConsolidator.run calls this at the start of a consolidation pass. The returned facts are then grouped by _buckets and later embedded and clustered.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryConsolidator._buckets`  (lines 332–341)

```
def _buckets(self, facts: tuple[_AgedFact, ...]) -> tuple[tuple[str, tuple[_AgedFact, ...]], ...]
```

**Purpose**: This groups aged facts by subject so only facts about the same thing are compared and summarized together. It also caps each subject bucket so one run cannot grow without bound.

**Data flow**: It receives aged fact records. It builds subject-based groups, sorts members by recency, keeps only the newest allowed facts per subject, and returns ordered subject buckets.

**Call relations**: MemoryConsolidator.run calls this after loading aged facts. Its buckets decide which facts move on to embedding and clustering.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._embed`  (lines 343–347)

```
async def _embed(self, facts: tuple[_AgedFact, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: This converts fact text into embeddings so the consolidator can compare facts by meaning rather than exact words.

**Data flow**: It receives aged facts. It clips each fact body to a safe size, sends the text batch to the embedding client, and returns a dictionary from fact ID to embedding vector.

**Call relations**: MemoryConsolidator.run calls this for each subject bucket that is large enough. The resulting vectors are passed into _clusters so similar facts can be grouped.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._clusters`  (lines 349–370)

```
def _clusters(self, facts: tuple[_AgedFact, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_AgedFact, ...], ...]
```

**Purpose**: This groups related facts by embedding similarity. It uses a simple newest-first approach: each fact joins the first existing cluster whose leading fact is close enough in meaning.

**Data flow**: It receives facts and their embeddings. It sorts facts by recency, compares each fact's vector with the head of existing clusters using cosine, adds it to a matching cluster when the similarity passes the threshold, or starts a new cluster otherwise. It returns clusters of facts.

**Call relations**: MemoryConsolidator.run runs this in a worker thread so CPU-heavy comparison work does not block the main async event loop. It relies on cosine to score similarity, and its clusters are later passed to _consolidate.

*Call graph*: calls 1 internal fn (cosine).


##### `MemoryConsolidator._consolidate`  (lines 372–434)

```
async def _consolidate(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> None
```

**Purpose**: This replaces a cluster of related facts with one new semantic summary, while marking the original facts as superseded. It does the final database write for consolidation.

**Data flow**: It receives a model and a cluster of aged facts. It first asks _summarize for a summary; if the summary is empty, it stops. It then opens a transaction, locks or re-reads the donor facts, checks that they still match the facts that were summarized, inserts a new semantic memory row, and updates the original fact rows so they point to the summary.

**Call relations**: MemoryConsolidator.run calls this for each cluster large enough to merge. This function calls _summarize before writing, then uses database insert and update operations to make the summary live and retire the donors atomically.

*Call graph*: calls 1 internal fn (_summarize); called by 1 (run); 4 external calls (insert, select, update, uuid4).


##### `MemoryConsolidator._summarize`  (lines 436–446)

```
async def _summarize(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> str
```

**Purpose**: This asks the language model to write one concise standalone summary for a group of related facts.

**Data flow**: It receives a model and a fact cluster. It clips each fact body, serializes the facts into JSON, builds a model request with consolidation instructions, sends the request through the model's complete method, trims the reply, and returns a summary capped to the maximum allowed length.

**Call relations**: MemoryConsolidator._consolidate calls this before opening the database transaction. Its returned text becomes the body of the new semantic memory row.

*Call graph*: calls 1 internal fn (complete); called by 1 (_consolidate); 3 external calls (__init__, __init__, dumps).


##### `_recency`  (lines 449–450)

```
def _recency(fact: _AgedFact) -> tuple[datetime, UUID]
```

**Purpose**: This provides a consistent sorting key for facts by creation time, with the ID as a tie-breaker. It lets the code say which fact is newer in a stable way.

**Data flow**: It receives an aged fact and returns a pair containing its created_at time and ID. It does not read or change anything else.

**Call relations**: The consolidator uses this helper when ordering facts inside buckets and clusters. That ordering matters because newer facts become the first candidates for cluster heads.


##### `_Group.key`  (lines 470–471)

```
def key(self) -> tuple[str, str]
```

**Purpose**: This gives a duplicate-check group its ordering identity: subject plus item class. The deduper uses it like a bookmark position while walking through groups over time.

**Data flow**: It reads the group's subject and item_class fields and returns them as a tuple. Nothing outside the group changes.

**Call relations**: MemoryDeduper.run uses this property when comparing groups against the stored cursor. It helps decide which group should be swept next.


##### `_Group.fingerprint`  (lines 474–475)

```
def fingerprint(self) -> list[JsonValue]
```

**Purpose**: This creates a small snapshot of a group's current duplicate state. If the snapshot has not changed since the last completed sweep, the deduper can skip expensive embedding work.

**Data flow**: It reads the group's live copy count and latest update time, converts the time to text, and returns both values in a JSON-friendly list.

**Call relations**: MemoryDeduper.run compares this property with a stored fingerprint before deduplicating a group, and writes it back after a successful sweep.


##### `MemoryDeduper.run`  (lines 515–527)

```
async def run(self) -> None
```

**Purpose**: This is the periodic job that removes near-duplicate live memories by keeping the newest copy and marking older copies as superseded. It works one group at a time so cleanup is steady and bounded.

**Data flow**: It loads groups that have enough live copies, reads the cursor from scoped storage, chooses the next group after that cursor, stores the new cursor, checks whether the group's fingerprint has changed, and skips or deduplicates accordingly. After a successful deduplication, it stores the current fingerprint.

**Call relations**: A scheduler calls this periodically. It coordinates the dedupe pass by calling _groups, _cursor, and _dedup_group, while using scoped key-value storage to remember progress and avoid repeated work.

*Call graph*: calls 3 internal fn (_cursor, _dedup_group, _groups).


##### `MemoryDeduper._cursor`  (lines 529–541)

```
def _cursor(self, stored: JsonValue | None) -> tuple[str, ...]
```

**Purpose**: This reads and validates the saved bookmark that tells the deduper where its last group scan stopped. It treats malformed saved data as an error rather than quietly starting over.

**Data flow**: It receives a stored JSON value or nothing. If there is no value, it returns an empty tuple meaning no prior cursor. If the value is a two-item list of strings, it returns those strings as the cursor. Otherwise it raises an error because the stored cursor is corrupted.

**Call relations**: MemoryDeduper.run calls this after reading the cursor key from scoped storage. The returned cursor is used to choose the next duplicate group to sweep.

*Call graph*: called by 1 (run).


##### `MemoryDeduper._groups`  (lines 543–567)

```
async def _groups(self) -> tuple[_Group, ...]
```

**Purpose**: This finds subject-and-class groups that contain enough old live tool-written rows to be worth checking for duplicates.

**Data flow**: It calculates an age cutoff, queries the memory table for live non-page-derived rows in the workspace that are old enough, groups them by subject and item class, counts copies, records the latest update time, and returns _Group records ordered by subject and class.

**Call relations**: MemoryDeduper.run calls this at the start of each tick. The resulting groups form the walk that the cursor advances through.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryDeduper._dedup_group`  (lines 569–574)

```
async def _dedup_group(self, group: _Group) -> None
```

**Purpose**: This performs the full duplicate cleanup for one selected group. It gathers the live copies, compares their meanings, and collapses each duplicate cluster.

**Data flow**: It receives a group. It loads that group's live copies, embeds their text, clusters near-duplicates in a worker thread, and for every cluster with enough copies, calls _collapse to mark older copies as superseded.

**Call relations**: MemoryDeduper.run calls this only when a group's fingerprint says the group has changed since its last completed sweep. This function calls _live_copies, _embed, _clusters, and _collapse in sequence.

*Call graph*: calls 3 internal fn (_collapse, _embed, _live_copies); called by 1 (run); 1 external calls (to_thread).


##### `MemoryDeduper._live_copies`  (lines 576–591)

```
async def _live_copies(self, group: _Group) -> tuple[_LiveCopy, ...]
```

**Purpose**: This loads the live memory rows for one duplicate group, newest first. Newest-first order matters because the deduper keeps the first item in each duplicate cluster.

**Data flow**: It receives a group. It builds the live-row filters with _live_group, queries the memory table for matching rows ordered by newest creation time and ID, limits the number of rows read, and returns _LiveCopy records containing IDs and bodies.

**Call relations**: MemoryDeduper._dedup_group calls this before embedding. It uses _live_group so the read matches the same definition of live, old-enough, tool-written rows used later during collapse.

*Call graph*: calls 1 internal fn (_live_group); called by 1 (_dedup_group); 2 external calls (__init__, select).


##### `MemoryDeduper._embed`  (lines 593–600)

```
async def _embed(self, copies: tuple[_LiveCopy, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: This turns each live copy's text into an embedding so duplicates can be detected by meaning rather than exact text.

**Data flow**: It receives live copies. It processes them in fixed-size batches, clips each body to a safe length, asks the embedding client for vectors, and returns a dictionary from copy ID to embedding vector.

**Call relations**: MemoryDeduper._dedup_group calls this after loading copies. The embeddings are then passed to _clusters for similarity comparison.

*Call graph*: called by 1 (_dedup_group); 1 external calls (batched).


##### `MemoryDeduper._clusters`  (lines 602–631)

```
def _clusters(self, copies: tuple[_LiveCopy, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_LiveCopy, ...], ...]
```

**Purpose**: This groups near-duplicate memory copies together. It uses a stricter similarity threshold than consolidation because it is meant to catch restatements, not merely related ideas.

**Data flow**: It receives copies in newest-first order and their embeddings. For each copy, it compares the copy's vector to the head of each existing cluster using cosine. If it is close enough, it joins that cluster; otherwise it starts a new one. It returns all clusters.

**Call relations**: MemoryDeduper._dedup_group runs this in a worker thread to avoid blocking the async loop with many numeric comparisons. It relies on cosine, and its duplicate-sized clusters are passed to _collapse.

*Call graph*: calls 1 internal fn (cosine).


##### `MemoryDeduper._collapse`  (lines 633–661)

```
async def _collapse(self, group: _Group, cluster: tuple[_LiveCopy, ...]) -> None
```

**Purpose**: This marks all older copies in a duplicate cluster as superseded by the newest copy. It is the database step that actually removes duplicate rows from live recall without deleting history.

**Data flow**: It receives a group and a cluster ordered with the winner first. It records the expected IDs and bodies, opens a transaction, locks or re-reads the matching live rows, confirms they are still exactly the same, and updates donor rows so their superseded_by field points to the head row. If the rows changed underneath it, it safely does nothing or raises if the locked update count is inconsistent.

**Call relations**: MemoryDeduper._dedup_group calls this for each duplicate cluster. It uses _live_group for both checking and updating so it never stamps rows that are no longer live or no longer part of the same group.

*Call graph*: calls 1 internal fn (_live_group); called by 1 (_dedup_group); 2 external calls (select, update).


##### `MemoryDeduper._live_group`  (lines 663–671)

```
def _live_group(self, group: _Group) -> tuple[ColumnElement[bool], ...]
```

**Purpose**: This builds the shared database conditions that define which rows count as live members of a dedupe group. It keeps reads and writes using the same rule.

**Data flow**: It receives a group and reads the workspace ID and current time. It returns conditions requiring the same workspace, subject, and item class, no source page, no superseded_by value, and an age older than the dedupe minimum.

**Call relations**: MemoryDeduper._live_copies uses this to read candidate rows, and MemoryDeduper._collapse uses it again when locking and updating rows. That shared filter prevents the deduper from touching page-derived, too-new, or already-retired memories.

*Call graph*: called by 2 (_collapse, _live_copies); 1 external calls (now).


### `extensions/memory/ufo_ext_memory/store.py`

`domain_logic` · `request handling and background indexing`

This file gives the project a durable “memory” system. A memory is a short stored note, such as a fact, preference, decision, event, or task. The file records those notes in database tables, decides who is allowed to read them, ranks them when someone asks a question, and feeds them into the search index used for fast recall.

The write path is deliberately simple. `MemoryStore.commit` saves a memory row but does not immediately split it into chunks or create embeddings. An embedding is a numeric representation of text used for meaning-based search. That slower work is done later by `MemoryIndexer`, like a mailroom that processes letters after they are dropped in the box.

Recall combines two search styles: word matching and vector search, which finds text with similar meaning. It blends the results, filters out memories the reader should not see, applies recency decay for facts, removes near-duplicates, and limits how much one memory type can dominate the answer.

The file also indexes source pages through `PageIndexer`. It mirrors live page state, removes stale page chunks, and rechecks page-derived memories when a page changes. Without this file, memories could not be safely stored, recalled, ranked, indexed, or protected from stale and unauthorized source content.

#### Function details

##### `recall_subjects`  (lines 165–166)

```
def recall_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: Turns an audience into the exact subject labels that memory recall should search. A subject is the visibility bucket for a memory, such as who or what the memory belongs to.

**Data flow**: It receives an `Audience` value, passes it to the shared audience helper, and returns the frozen set of subject strings that are allowed for recall.

**Call relations**: This is a small adapter around the SDK audience logic. Other memory code can use it before calling recall so the store searches only the subjects that match the caller’s audience.

*Call graph*: 1 external calls (audience_subjects).


##### `_granted_link`  (lines 169–177)

```
def _granted_link(source_ids: frozenset[UUID]) -> ColumnElement[bool]
```

**Purpose**: Builds the database permission check for page-derived memories. A memory learned from a source page is readable only if the reader has access to at least one source linked to that memory.

**Data flow**: It receives a set of source IDs, creates an SQL `EXISTS` condition against the memory-source link table, and returns that condition for a larger database query to use.

**Call relations**: Recall uses this helper inside `_enrich`, and the unindexed-tail search uses it inside `_untail_leg`. In both places it acts like a gate: rows without a readable source link are filtered out.

*Call graph*: called by 2 (_enrich, _untail_leg); 1 external calls (exists).


##### `inventory`  (lines 217–286)

```
async def inventory(transaction: Transaction, workspace_id: UUID) -> tuple[MemoryInventoryItem, ...]
```

**Purpose**: Lists the newest stored memories in a workspace for an operator or explorer view. This is not a search result; it is a snapshot of what is stored and how each item would currently decay in ranking.

**Data flow**: It receives a transaction opener and workspace ID, reads recent memory rows and their source links from the database, computes age, half-life, and decay for each row, and returns `MemoryInventoryItem` objects.

**Call relations**: It calls `_aware`, `half_life_days`, and `decay_multiplier` to report the same time-based weighting that recall uses. It stands apart from recall because it shows stored rows directly, including superseded and not-yet-indexed state.

*Call graph*: calls 3 internal fn (_aware, decay_multiplier, half_life_days); 3 external calls (__init__, now, select).


##### `_aware`  (lines 289–290)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Makes sure a timestamp has timezone information. This prevents age calculations from mixing timezone-aware and timezone-naive dates.

**Data flow**: It receives a `datetime`; if it already has a timezone it returns it unchanged, otherwise it marks it as UTC and returns the adjusted value.

**Call relations**: Inventory, recall enrichment, source search, and decay calculation all call this before comparing times. It keeps time handling consistent across database rows that may arrive with or without timezone metadata.

*Call graph*: called by 4 (_enrich, search_sources, decay_multiplier, inventory); 1 external calls (replace).


##### `MemoryWrite.page_origin_is_complete`  (lines 313–321)

```
def page_origin_is_complete(self) -> Self
```

**Purpose**: Validates that page-derived memories include all required page origin details. A memory cannot claim to come from a page unless it names the page, the page revision, and the source.

**Data flow**: It checks the three page-origin fields on a `MemoryWrite`. If some are present but not all, it raises a validation error; otherwise it returns the write object unchanged.

**Call relations**: Pydantic runs this validator when a `MemoryWrite` is created. It protects `MemoryStore.commit` from receiving half-described page provenance that later permission and freshness checks could not trust.


##### `_fuse`  (lines 349–372)

```
def _fuse(legs: tuple[tuple[Hit, ...], ...], cosine_leg: tuple[Hit, ...]) -> dict[str, tuple[float, float, str]]
```

**Purpose**: Combines several ranked search-result lists into one score per owning row. It lets the system blend word search and meaning search without letting duplicate chunks from the same memory overwhelm the result.

**Data flow**: It receives search “legs” made of chunk hits plus the vector leg, calculates reciprocal-rank fusion scores for chunks, keeps the best chunk per owner, attaches the best vector similarity for that owner, and returns a mapping by owner ID.

**Call relations**: `fuse_hits` and `fuse_recall` both call this as their shared ranking core. They then apply different final scoring rules depending on whether the caller is searching source pages or recalling memories.

*Call graph*: called by 2 (fuse_hits, fuse_recall); 1 external calls (from_iterable).


##### `fuse_hits`  (lines 375–388)

```
def fuse_hits(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks source-page search hits by combining word-match and vector-search results. It also blocks meaningless vector-only matches unless they are similar enough.

**Data flow**: It receives lexical hits, vector hits, and a limit, fuses the two result lists, applies a similarity floor when there were no word matches, sorts by fused rank, and returns `Fused` results up to the limit.

**Call relations**: `MemoryStore.search_sources` calls this after collecting both index legs. It hands back page IDs and snippets that source search can then verify against live page state.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (search_sources); 1 external calls (__init__).


##### `fuse_recall`  (lines 391–419)

```
def fuse_recall(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], tail: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks memory recall candidates by blending rank-based search evidence with raw semantic similarity. It also includes not-yet-indexed memories through a temporary lexical “tail” leg.

**Data flow**: It receives lexical hits, vector hits, tail hits, and a limit, fuses all legs, normalizes the rank score, blends it with cosine similarity, filters weak vector-only matches, sorts, and returns `Fused` candidates.

**Call relations**: `MemoryStore.recall` calls this before reading full memory rows. It is the bridge between the search index’s chunk hits and the later database filtering and final ranking steps.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (recall); 1 external calls (__init__).


##### `half_life_days`  (lines 437–443)

```
def half_life_days(item_class: str, memory_kind: str) -> float | None
```

**Purpose**: Returns how quickly a memory should lose influence because of age. Only fact-like memories decay; episodic and semantic memories keep their relevance score unchanged.

**Data flow**: It receives a memory class and kind, checks whether the item is a fact, and returns the configured half-life in days or `None` if no decay should apply.

**Call relations**: `decay_multiplier` uses this to compute actual score reduction, and `inventory` uses it to show operators the configured decay behavior for each row.

*Call graph*: called by 2 (decay_multiplier, inventory).


##### `decay_multiplier`  (lines 446–458)

```
def decay_multiplier(item_class: str, memory_kind: str, confidence: int, as_of: datetime | None, now: datetime) -> float
```

**Purpose**: Calculates the time-based multiplier applied to a memory’s recall score. This makes older, lower-confidence facts matter less over time.

**Data flow**: It receives item class, memory kind, confidence, an as-of time, and the current time. It finds the half-life, computes age in days, combines age with confidence, and returns a numeric multiplier.

**Call relations**: `decay_factor` calls this for recalled items, and `inventory` calls it for display. It is the single place where recall and operator views share the same decay math.

*Call graph*: calls 2 internal fn (_aware, half_life_days); called by 2 (decay_factor, inventory).


##### `decay_factor`  (lines 461–464)

```
def decay_factor(item: Recalled, now: datetime) -> float
```

**Purpose**: Applies the standard decay calculation to a recalled memory object. It is a convenience wrapper for final recall ranking.

**Data flow**: It receives a `Recalled` item and the current time, chooses the best timestamp from `as_of` or `created_at`, passes the fields into `decay_multiplier`, and returns the multiplier.

**Call relations**: `MemoryStore._shortlist` calls this while turning a candidate pool into the final list. It keeps the shortlist step focused on ranking rather than field unpacking.

*Call graph*: calls 1 internal fn (decay_multiplier); called by 1 (_shortlist).


##### `_body_shingles`  (lines 472–474)

```
def _body_shingles(body: str) -> frozenset[str]
```

**Purpose**: Turns a memory body into small three-word fingerprints used for duplicate detection. This gives a cheap way to notice nearly repeated facts.

**Data flow**: It receives text, lowercases and splits it into words, groups neighboring words into three-word phrases, and returns those phrases as a set.

**Call relations**: `drop_near_duplicates` calls this for each candidate body. The resulting sets are compared with Jaccard overlap, a simple measure of how much two sets share.

*Call graph*: called by 1 (drop_near_duplicates); 1 external calls (split).


##### `drop_near_duplicates`  (lines 477–503)

```
def drop_near_duplicates(items: tuple[Recalled, ...], keep: int) -> tuple[Recalled, ...]
```

**Purpose**: Removes memories that say almost the same thing as an earlier, higher-ranked memory. This prevents recall from wasting limited context space on repeated facts.

**Data flow**: It receives ranked recalled items and a keep count, builds word-shingle sets for candidate bodies, skips items whose overlap with a kept item is too high, and returns distinct-enough items.

**Call relations**: `MemoryStore._shortlist` calls this after applying decay-based ranking. It acts as a safety net until a deeper deduplication process retires duplicate rows permanently.

*Call graph*: calls 1 internal fn (_body_shingles); called by 1 (_shortlist).


##### `enforce_type_diversity`  (lines 506–524)

```
def enforce_type_diversity(rows: tuple[Recalled, ...], limit: int) -> tuple[Recalled, ...]
```

**Purpose**: Prevents one memory class from filling nearly all recall slots. This helps a result set include a mix, such as facts and topics, when available.

**Data flow**: It receives ranked rows and a limit, admits only a capped number from each item class at first, saves overflow, then backfills from overflow if there are still open slots.

**Call relations**: `MemoryStore._shortlist` calls this after duplicate removal. It is the final shaping step before recall returns items to the caller.

*Call graph*: called by 1 (_shortlist).


##### `as_topic_pointer`  (lines 527–537)

```
def as_topic_pointer(item: Recalled, index: int) -> Recalled
```

**Purpose**: Rewrites episodic memories into browseable topic pointers instead of injecting their full text. Episodic memory is treated like a breadcrumb, not direct background context.

**Data flow**: It receives a recalled item and its position. If the item is episodic, it returns a copy with a short “Memory topic” body and topic recall mode; otherwise it returns the original item.

**Call relations**: `MemoryStore.recall` applies this to final shortlist items. It ensures episodic hits are visible as navigation hints while ordinary facts remain directly usable.

*Call graph*: called by 1 (recall); 1 external calls (replace).


##### `MemoryStore.commit`  (lines 564–678)

```
async def commit(self, write: MemoryWrite) -> None
```

**Purpose**: Saves or updates one memory in the database without indexing it immediately. It also records the source-page link when the memory came from a synced page.

**Data flow**: It receives a `MemoryWrite`, creates a stable content-based memory ID, upserts the memory row, clears index state if the page binding changed, revives superseded rows when reasserted, and upserts the source link if present.

**Call relations**: This is the main write entry on `MemoryStore`. It prepares rows for `MemoryIndexer` by leaving or clearing `embedding_digest`, and it supplies the link data later used by recall permission checks and page-fact retirement.

*Call graph*: 3 external calls (case, or_, uuid5).


##### `MemoryStore.supersede_page_facts`  (lines 680–796)

```
async def supersede_page_facts(self, page_id: UUID, revision: int | None) -> None
```

**Purpose**: Retires stale facts that were derived from an older version of a page. If the same fact was learned from other pages too, it removes only the stale page link and keeps the memory alive.

**Data flow**: It receives a page ID and optional current revision, finds stale memory-source links, locks affected memory rows, deletes stale links, either deletes rows with no remaining links or rebinds them to a surviving link, and removes index chunks for deleted rows.

**Call relations**: This is called by the fact-derivation flow outside this file. It coordinates with the index backend when rows disappear, while `MemoryIndexer` later rechecks rebound rows whose digest was cleared.

*Call graph*: 4 external calls (__init__, delete, select, update).


##### `MemoryStore.recall`  (lines 798–837)

```
async def recall(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None, *, source_reader: SourceReader) -> tuple[Recalled, ...]
```

**Purpose**: Answers a memory search query for a reader. It gathers candidates from the index and from fresh unindexed rows, filters them for permissions and freshness, then ranks and shapes the final list.

**Data flow**: It receives a query, allowed subjects, a limit, optional date window, and source reader. It reads readable source IDs, collects lexical and vector hits, adds tail hits, fuses them, enriches them from the database, shortlists them in a worker thread, rewrites episodic items as topic pointers, and returns recalled memories.

**Call relations**: This is the central read path. It calls `_source_ids`, `_legs`, `_untail_leg`, `fuse_recall`, `_enrich`, `_shortlist`, and `as_topic_pointer`, with each helper handling one stage of the recall pipeline.

*Call graph*: calls 6 internal fn (_enrich, _legs, _source_ids, _untail_leg, as_topic_pointer, fuse_recall); 2 external calls (to_thread, now).


##### `MemoryStore._shortlist`  (lines 839–854)

```
def _shortlist(self, enriched: tuple[Recalled, ...], limit: int, now: datetime) -> tuple[Recalled, ...]
```

**Purpose**: Turns a larger candidate pool into the final number of memories requested. It applies time decay, duplicate suppression, and type diversity.

**Data flow**: It receives enriched recalled items, a limit, and the current time. It multiplies each score by its decay factor, sorts by the new score, drops near-duplicates, enforces type diversity, and returns the final tuple.

**Call relations**: `MemoryStore.recall` runs this in a worker thread because it is CPU work with no waiting points. It calls `decay_factor`, `drop_near_duplicates`, and `enforce_type_diversity` in sequence.

*Call graph*: calls 3 internal fn (decay_factor, drop_near_duplicates, enforce_type_diversity); 1 external calls (replace).


##### `MemoryStore.search_sources`  (lines 856–922)

```
async def search_sources(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None, *, source_reader: SourceReader) -> tuple[SourceMatch, ...]
```

**Purpose**: Searches synced source pages, not stored memory facts. It returns matching page snippets only if the page is still live, current, and readable by the caller.

**Data flow**: It receives a query, subjects, limit, optional date window, and source reader. It gathers index legs, fuses them, reads mirror rows from `mem_page`, checks readable current page states, and returns `SourceMatch` objects.

**Call relations**: This parallels `recall` but uses page owner IDs and `fuse_hits`. It calls `_legs` for index searching and `_readable_states` to make sure returned page snippets are still allowed and current.

*Call graph*: calls 4 internal fn (_legs, _readable_states, _aware, fuse_hits); 3 external calls (__init__, select, UUID).


##### `MemoryStore._source_ids`  (lines 924–930)

```
async def _source_ids(self, source_reader: SourceReader) -> frozenset[UUID]
```

**Purpose**: Asks the configured permission authority which source IDs the reader may access. Source-derived memories cannot be safely read without this authority.

**Data flow**: It receives a `SourceReader`, verifies that a readable-source callback is available, calls it, and returns the readable source IDs. If the callback is missing, it raises an error.

**Call relations**: `MemoryStore.recall` calls this before filtering page-derived memories. The result is later used by `_untail_leg` and `_enrich` to enforce source grants.

*Call graph*: called by 1 (recall).


##### `MemoryStore._legs`  (lines 932–944)

```
async def _legs(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[tuple[Hit, ...], tuple[Hit, ...]]
```

**Purpose**: Runs the two normal search-index paths for a query: word search and vector search. These are called “legs” because they are separate routes that later get fused.

**Data flow**: It receives a query, subject set, owner kind, and limit. It embeds the query if possible, asks the index for lexical hits, asks for vector hits when an embedding exists, and returns both hit lists.

**Call relations**: Both `MemoryStore.recall` and `MemoryStore.search_sources` call this. It delegates embedding to `_embed_query` and leaves result blending to `fuse_recall` or `fuse_hits`.

*Call graph*: calls 1 internal fn (_embed_query); called by 2 (recall, search_sources).


##### `MemoryStore._untail_leg`  (lines 946–1001)

```
async def _untail_leg(self, query: str, subjects: frozenset[str], limit: int, source_ids: frozenset[UUID]) -> tuple[Hit, ...]
```

**Purpose**: Finds fresh memories that have been saved but not indexed yet. This makes a just-written fact searchable before the background indexer has processed it.

**Data flow**: It receives a query, subjects, limit, and readable source IDs. It splits the query into terms, scans a bounded set of newest unindexed memory rows that are visible to the reader, counts term matches in each body, and returns synthetic `Hit` objects sorted by score.

**Call relations**: `MemoryStore.recall` calls this as a third recall leg. It uses `_granted_link` for source permissions and feeds its hits into `fuse_recall` beside real index hits.

*Call graph*: calls 1 internal fn (_granted_link); called by 1 (recall); 4 external calls (__init__, split, or_, select).


##### `MemoryStore._embed_query`  (lines 1003–1011)

```
async def _embed_query(self, query: str) -> tuple[float, ...]
```

**Purpose**: Converts a query into a vector for meaning-based search. If embedding fails, recall still works using word search instead of failing the whole request.

**Data flow**: It receives query text, returns an empty vector for blank text, otherwise asks the embed client for one embedding. On errors it logs a warning and returns an empty tuple.

**Call relations**: `MemoryStore._legs` calls this before vector search. Its graceful failure path lets both recall and source search continue with lexical results only.

*Call graph*: called by 1 (_legs).


##### `MemoryStore._enrich`  (lines 1013–1092)

```
async def _enrich(self, fused: tuple[Fused, ...], subjects: frozenset[str], source_ids: frozenset[UUID], start: datetime | None, end: datetime | None) -> tuple[Recalled, ...]
```

**Purpose**: Loads full memory rows for fused search candidates and applies database-level safety filters. This is where candidate IDs become actual recalled memories.

**Data flow**: It receives fused hits, allowed subjects, readable source IDs, and an optional time window. It reads matching non-superseded rows, filters by workspace, subject, source grant, and date, checks page-derived rows against current page state, and returns `Recalled` objects in fused order.

**Call relations**: `MemoryStore.recall` calls this after `fuse_recall`. It uses `_granted_link` for permissions and `_aware` for timestamps, and it calls the page-state provider to prevent stale page-derived memories from leaking.

*Call graph*: calls 2 internal fn (_aware, _granted_link); called by 1 (recall); 4 external calls (__init__, or_, select, UUID).


##### `MemoryStore._readable_states`  (lines 1094–1101)

```
async def _readable_states(self, page_ids: tuple[UUID, ...], source_reader: SourceReader) -> dict[UUID, PageState]
```

**Purpose**: Reads current page states only through a permission-aware page-state callback. This is used when source page search needs to confirm the caller may see a page.

**Data flow**: It receives page IDs and a source reader. If there are no IDs it returns an empty dictionary; if the permission-aware callback is missing it raises an error; otherwise it returns the readable current states.

**Call relations**: `MemoryStore.search_sources` calls this after index results and mirror rows are known. It is the final live-page and access check before source snippets are returned.

*Call graph*: called by 1 (search_sources).


##### `store_for`  (lines 1104–1117)

```
def store_for(ext: ExtensionContext) -> MemoryStore
```

**Purpose**: Builds a `MemoryStore` from the extension context. It fails early if the required index or embedding backends were not wired into the context.

**Data flow**: It receives an `ExtensionContext`, checks that index and embed clients exist, then passes the workspace transaction, workspace ID, page-state readers, and grant readers into a new `MemoryStore`.

**Call relations**: Other extension code uses this factory instead of constructing `MemoryStore` by hand. It connects the memory workflow to the surrounding workspace-scoped infrastructure.

*Call graph*: 1 external calls (__init__).


##### `MemoryIndexer.run`  (lines 1139–1141)

```
async def run(self) -> None
```

**Purpose**: Runs one indexing tick for stored memories that are due to be processed. It claims a batch and indexes each item.

**Data flow**: It asks `_claim_due` for due memory rows, then passes each claimed `MemoryItem` to `_index_item`. It returns nothing; its effects are in the index and database stamps.

**Call relations**: A background job calls this periodically. It is the simple outer loop that connects batch claiming with per-item indexing.

*Call graph*: calls 2 internal fn (_claim_due, _index_item).


##### `MemoryIndexer._claim_due`  (lines 1143–1178)

```
async def _claim_due(self) -> tuple[MemoryItem, ...]
```

**Purpose**: Claims memory rows whose embeddings are missing, so overlapping indexer runs do not work on the same rows. The claim is leased so stuck work can be retried later.

**Data flow**: It computes the lease cutoff time, selects due rows with no digest and no active claim, locks them when the database supports it, stamps `embedding_claimed_at`, and returns them as `MemoryItem` objects.

**Call relations**: `MemoryIndexer.run` calls this at the start of a tick. The returned rows are then handed to `_index_item`, which either publishes, withholds, or settles each row.

*Call graph*: called by 1 (run); 5 external calls (now, timedelta, or_, select, update).


##### `MemoryIndexer._index_item`  (lines 1180–1217)

```
async def _index_item(self, item: MemoryItem) -> None
```

**Purpose**: Processes one claimed memory row for the search index. It publishes chunks only if the memory is still allowed to appear in recall.

**Data flow**: It receives a `MemoryItem`, checks whether its page binding is publishable, deletes old chunks and settles if not, otherwise chunks and embeds the body if needed, rechecks the current binding after embedding, deletes chunks if the row became invalid, and settles only if the binding still matches.

**Call relations**: `MemoryIndexer.run` calls this for each claimed row. It relies on `_publishable` for freshness checks, `chunk_embed_upsert` for index writes, and `_settle` to mark the database row no longer due.

*Call graph*: calls 2 internal fn (_publishable, _settle); called by 1 (run); 3 external calls (__init__, select, chunk_embed_upsert).


##### `MemoryIndexer._publishable`  (lines 1219–1228)

```
async def _publishable(self, subject: str, page_id: UUID | None, revision: int | None) -> bool
```

**Purpose**: Decides whether a memory body may be present in the index. Member-written memories are always publishable; page-derived memories must still match the live page subject and revision.

**Data flow**: It receives a subject, optional page ID, and optional revision. If there is no page ID it returns true; otherwise it reads current page state and returns true only when subject and revision match.

**Call relations**: `MemoryIndexer._index_item` calls this before and after possible embedding work. It prevents stale page-derived memories from occupying index candidate slots.

*Call graph*: called by 1 (_index_item).


##### `MemoryIndexer._settle`  (lines 1230–1254)

```
async def _settle(self, item: MemoryItem) -> None
```

**Purpose**: Marks a claimed memory row as decided by the indexer. This removes it from the due queue whether it was published or deliberately withheld.

**Data flow**: It receives the claimed `MemoryItem`, computes a SHA-256 digest of the body, and updates the row to store that digest and clear the claim, but only if the row still matches the claimed subject, body, page binding, source, and active claim.

**Call relations**: `MemoryIndexer._index_item` calls this after a safe terminal decision. The guarded update avoids stamping a row that was changed or rebound while indexing was in progress.

*Call graph*: called by 1 (_index_item); 2 external calls (sha256, update).


##### `PageIndexer.apply`  (lines 1277–1279)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Applies a batch of source-page changes to the memory extension’s page index. It processes each delivered change one by one.

**Data flow**: It receives a tuple of `PageChange` objects, calls `_apply` for each change, and returns after all changes have been attempted.

**Call relations**: The core page-change runner owns batching and cursor progress, then calls this method for a delivered batch. This method is the batch wrapper around the per-change logic in `_apply`.

*Call graph*: calls 1 internal fn (_apply).


##### `PageIndexer._apply`  (lines 1281–1339)

```
async def _apply(self, change: PageChange) -> None
```

**Purpose**: Updates the page search index and page mirror for one source-page change. It removes stale page chunks, indexes current page text, or deletes mirror/index data when the page is gone or outdated.

**Data flow**: It receives a page change, reads current page state, clears digest state for facts left behind by the page, handles tombstones by deleting index and mirror rows, rejects mismatched stale changes, embeds and upserts current page chunks, rechecks the page did not change during embedding, and upserts the `mem_page` mirror row.

**Call relations**: `PageIndexer.apply` calls this for each change. It calls `_unsettle_left_behind_facts` first so memory rows tied to old page revisions become due for `MemoryIndexer` to withdraw or republish.

*Call graph*: calls 1 internal fn (_unsettle_left_behind_facts); called by 1 (apply); 5 external calls (__init__, delete, insert, update, chunk_embed_upsert).


##### `PageIndexer._unsettle_left_behind_facts`  (lines 1341–1369)

```
async def _unsettle_left_behind_facts(self, page_id: UUID, state: PageState | None) -> None
```

**Purpose**: Marks page-derived memory rows as needing a fresh index decision when their source page has moved on. It does not delete the memories; it asks the memory indexer to re-evaluate their chunks.

**Data flow**: It receives a page ID and current page state. It builds a database filter for memories from that page that no longer match the live subject and revision, or all such memories if the page is gone, then clears their embedding digest and claim fields.

**Call relations**: `PageIndexer._apply` calls this before handling each page change. The cleared rows later appear in `MemoryIndexer._claim_due`, where stale chunks can be removed and valid chunks can be settled again.

*Call graph*: called by 1 (_apply); 2 external calls (or_, update).


### `core/src/ufo/memory.py`

`data_model` · `cross-cutting; used whenever memory search or recent-memory browsing is requested`

This file is a small but important meeting point between code that wants to recall past information and code that knows where that information is stored. Think of it like a standard plug shape: many different memory systems can exist behind the wall, but callers only need one kind of socket.

The `MemoryMatch` data class describes one search result in a provider-neutral way. It carries the kind of memory, the text snippet to show, and, when available, a durable object reference that can be opened later. It can also include when the item was created and what subject it belongs to, so users can judge relevance before opening it.

`MemorySearchProvider` is a protocol, meaning it is a promise about what methods a real provider must offer. A provider must support text search, browsing recent readable items, and reporting which item kinds can be listed. The comments make one important design choice clear: recent browsing uses a cursor rather than a numbered offset, so newly arriving items do not cause the reader to skip or repeat results.

`MemorySearch` is a thin dispatcher. It holds the chosen provider and forwards calls to it. That keeps the consumer-facing API stable while allowing different memory backends to do the real work.

#### Function details

##### `MemorySearchProvider.search`  (lines 37–43)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This defines the search operation that every memory provider must implement. It asks a provider to find memory matches for one or more query strings, limited to what the given source reader is allowed to read and optionally bounded by time.

**Data flow**: The caller supplies query text, a `SourceReader` that represents readable sources, and optional start and end times. A real provider uses those inputs to look through its memory store and returns a tuple of `MemoryMatch` results. This method is only the contract; the actual searching happens in whichever provider implements it.

**Call relations**: Consumers call this through the `MemorySearch` wrapper, which passes the request on unchanged. Provider implementations fulfill this promise so callers do not need to know which memory extension is behind the search.


##### `MemorySearchProvider.list_recent`  (lines 45–51)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: This defines how a memory provider must list recent memory items that a set of subjects may read. It is for browsing current memory by recency, not for doing a text search.

**Data flow**: The caller provides readable subject names, a maximum number of results, optional item kinds to include, and an optional cursor showing where the previous page ended. A provider returns a `ListingPage` of `MemoryMatch` items, usually with information needed to continue paging. The cursor-based approach lets the list stay stable even if new items are added while someone is browsing.

**Call relations**: Consumers normally reach this through `MemorySearch.list_recent`, which delegates directly to the selected provider. The provider then supplies a page of recent items in the agreed format.


##### `MemorySearchProvider.listable_kinds`  (lines 53–53)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: This defines how a provider reports the memory item categories that can be shown in recent-memory browsing filters. It lets a consumer offer only choices that the provider actually knows how to list.

**Data flow**: It takes no input beyond the provider itself. A real provider returns a tuple of kind names, such as the classes or categories of memory entries it can expose in browse mode. Nothing is changed; it is a read-only capability check.

**Call relations**: The `MemorySearch` wrapper forwards this call to the selected provider. User interfaces or other consumers can call it before listing recent items so their filters match the provider’s real data.


##### `MemorySearch.search`  (lines 62–69)

```
async def search(self, reader: SourceReader, queries: tuple[str, ...], start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This is the consumer-friendly search method. It keeps callers from talking to a provider directly and forwards the search request to the provider chosen for this `MemorySearch` instance.

**Data flow**: The caller gives a `SourceReader`, query strings, and optional time bounds. `MemorySearch` passes those values straight to its provider’s `search` method and returns the provider’s tuple of `MemoryMatch` results. It does not alter the query or interpret the results.

**Call relations**: This method sits between consumers and `MemorySearchProvider.search`. When code asks to search memory, this wrapper hands the request to the configured provider, allowing the rest of the system to stay independent of the provider’s implementation.


##### `MemorySearch.list_recent`  (lines 71–78)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: This is the consumer-friendly method for browsing recent memory items. It forwards the request to the configured provider while keeping the public calling pattern consistent.

**Data flow**: The caller supplies readable subjects, a result limit, optional kind filters, and an optional paging cursor. `MemorySearch` passes them directly to the provider’s `list_recent` method. The returned `ListingPage` comes back to the caller unchanged.

**Call relations**: This method is the bridge from consumer code to `MemorySearchProvider.list_recent`. It is used when something wants a page of recent memory without needing to know which backend stores or orders those items.


##### `MemorySearch.listable_kinds`  (lines 80–81)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: This asks the selected provider which memory kinds can be browsed. It gives callers a simple way to build valid filters or choices.

**Data flow**: The caller provides no extra data. `MemorySearch` calls the provider’s `listable_kinds` method and returns the tuple of kind names it receives. It does not add, remove, or rename any kinds.

**Call relations**: This method delegates to `MemorySearchProvider.listable_kinds`. It is typically used before recent-memory listing, so a consumer can offer only provider-supported categories.


### `extensions/memory/ufo_ext_memory/events.py`

`config` · `cross-cutting`

This file is a tiny shared vocabulary for the memory extension’s event logging. The memory feature can look up relevant saved memories before the system writes a reply. When that happens, other parts of the system may want a clear signal that says, “memory recall happened here.” The constant `MEMORY_RECALL_EVENT` provides that exact event name, so every sender and listener uses the same spelling.

The file also defines two limits. `MAX_RECALLED_MEMORY_IDS` caps how many recalled memory identifiers should be included in an event. This keeps event records short and avoids flooding logs or telemetry with too much detail. `MAX_RECALL_ERROR_CLASS_CHARS` limits the length of an error class name recorded when recall fails, which helps keep error information tidy and predictable.

Without this file, the event name and size limits might be repeated in several places. That would make it easier for small differences to creep in, like two components using slightly different event names and silently missing each other. Think of it like a label maker for the memory system: everyone uses the same label, and the labels are kept to a sensible size.


### Research retrieval tools
Perplexity-backed search, wide research orchestration, durable source records, and agent-facing research tools provide web retrieval for conversations.

### `extensions/perplexity/ufo_ext_perplexity.py`

`io_transport` · `extension registration and search/fetch request handling`

This file is the adapter between UFO’s search interface and Perplexity’s hosted search API. Without it, the rest of the system could ask for “web search” in a general way, but it would not know how to talk to Perplexity, where to send requests, how to authenticate, or how to interpret the response.

The main class, PerplexitySearchProvider, has two public jobs. Its search method sends a normal web search query and returns a list of search hits. Its fetch method asks Perplexity for content from one exact web page and returns the text snippet as a fetched page. Think of it like a bilingual clerk: UFO speaks in its own SearchQuery and FetchRequest forms, while Perplexity expects a particular JSON message over HTTPS. This file translates both directions.

The file also protects the outside service and the caller from bad requests. It checks limits such as query length, URL length, result counts, prompt length, and character limits before sending anything. It also checks that Perplexity replies in the expected shape. If Perplexity refuses the request, sends broken JSON, or does not return the requested page, the code raises PerplexityError with a clear message.

Finally, manifest tells the host how to register this extension: it declares the needed Perplexity API key credential and exposes a provider named “perplexity”.

#### Function details

##### `PerplexitySearchProvider.search`  (lines 72–85)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Runs a web search through Perplexity and returns the results in UFO’s standard search-result format. Someone would use this when the system needs search hits, not full page extraction.

**Data flow**: It receives a SearchQuery containing the text to search for, result count, optional vertical such as people or academic, optional domain filters, and optional date filters. It first asks _search_body to turn that into Perplexity’s JSON request, sends it with _post, validates the returned data with _response, and then converts each Perplexity result into a SearchHit. The output is a SearchResults object containing those hits.

**Call relations**: This is one of the provider’s main entry points. When the host asks this extension to search, this method coordinates the helper steps: _search_body prepares the request, _post talks to Perplexity over the network, and _response checks that the answer can be trusted before SearchResults is returned.

*Call graph*: calls 3 internal fn (_post, _response, _search_body); 2 external calls (__init__, __init__).


##### `PerplexitySearchProvider.fetch`  (lines 87–131)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Extracts text for one requested web page using Perplexity’s search API. It is used when the system wants content from a specific URL rather than a general list of search results.

**Data flow**: It receives a FetchRequest with a URL, an optional extraction prompt, and an optional maximum character count. It checks that the URL is a reasonable absolute HTTP or HTTPS URL, that the prompt and limits are allowed, and that the requested output size stays within this provider’s caps. It sends Perplexity a domain-limited request, validates the response, looks for a returned result whose canonical page matches the requested URL, trims the text to the allowed size, and returns a FetchedPage. If the exact page is not found, it raises PerplexityError instead of silently returning the wrong page.

**Call relations**: This is the provider’s second main entry point. It calls _post to contact Perplexity and _response to parse the answer, then uses _canonical_page to compare URLs in a forgiving but careful way. It finishes by creating the FetchedPage object that the rest of the system expects.

*Call graph*: calls 3 internal fn (_post, _response, _canonical_page); 3 external calls (__init__, __init__, urlsplit).


##### `PerplexitySearchProvider._search_body`  (lines 134–159)

```
def _search_body(query: SearchQuery) -> dict[str, Json]
```

**Purpose**: Builds the JSON request body that Perplexity expects for a search. It also enforces local safety limits before any network call is made.

**Data flow**: It receives a SearchQuery. It checks that at least one result was requested, caps the requested result count to Perplexity’s maximum, adds a plain-language qualifier for some vertical searches, checks the final query length, and adds optional domain and date filters. It returns a dictionary ready to be sent as JSON to Perplexity.

**Call relations**: The search method calls this before contacting Perplexity. When date filters are present, it hands those dates to _api_date so they are formatted in the style the Perplexity API expects. If the request is outside allowed bounds, it raises PerplexityError and prevents _post from sending a bad request.

*Call graph*: calls 1 internal fn (_api_date); called by 1 (search); 1 external calls (__init__).


##### `PerplexitySearchProvider._response`  (lines 162–166)

```
def _response(payload: object) -> _PerplexitySearchResponse
```

**Purpose**: Checks that Perplexity’s reply has the structure this adapter needs. It protects the rest of the code from confusing or incomplete API responses.

**Data flow**: It receives a raw decoded JSON value from Perplexity. It validates that the value contains a results list whose items have the expected fields such as URL, title, and snippet. If validation succeeds, it returns a typed _PerplexitySearchResponse object; if not, it raises PerplexityError.

**Call relations**: Both search and fetch call this immediately after _post returns. It is the gate between untrusted outside data and the provider’s normal result-building code.

*Call graph*: called by 2 (fetch, search); 1 external calls (__init__).


##### `PerplexitySearchProvider._post`  (lines 168–188)

```
async def _post(self, body: dict[str, Json]) -> object
```

**Purpose**: Sends one authenticated HTTP POST request to Perplexity’s /search endpoint and returns the decoded JSON response. This is the only method in the provider that directly talks over the network.

**Data flow**: It receives a JSON-ready request body. It reads the Perplexity API key from the credential store, opens an asynchronous HTTP client, sends the body to Perplexity with a bearer authorization header, and waits for the response. If Perplexity returns an error status or invalid JSON, it raises PerplexityError; otherwise it returns the decoded response object.

**Call relations**: The search and fetch methods both rely on this to perform the actual API call. They prepare the meaning of the request, while _post is responsible for transport details such as the host name, timeout, authorization header, and response decoding.

*Call graph*: called by 2 (fetch, search); 2 external calls (__init__, AsyncClient).


##### `_api_date`  (lines 191–192)

```
def _api_date(value: date) -> str
```

**Purpose**: Formats a Python date into the date string format expected by the Perplexity API. It is used for search filters like “after this date” or “before this date.”

**Data flow**: It receives a date object. It converts that date into a string in month/day/year form. The output is a simple text value that can be placed into the Perplexity JSON request.

**Call relations**: PerplexitySearchProvider._search_body calls this when a search query includes start or end publication dates. It keeps the API-specific date formatting in one small helper instead of mixing it into the larger request-building code.

*Call graph*: called by 1 (_search_body); 1 external calls (strftime).


##### `_canonical_page`  (lines 195–202)

```
def _canonical_page(value: str) -> tuple[str | None, str, str]
```

**Purpose**: Normalizes a URL enough to compare whether two links point to the same page. It helps fetch avoid accepting a nearby or similar-looking URL when the caller asked for a specific one.

**Data flow**: It receives a URL string. It splits the URL into parts, removes a trailing slash from the path, and strips common page suffixes such as .html, .htm, and .txt. It returns a tuple containing the host name, cleaned path, and query string, which can be compared with another normalized URL.

**Call relations**: PerplexitySearchProvider.fetch uses this on both the requested URL and each Perplexity result URL. That comparison decides whether Perplexity actually returned the page the caller asked for.

*Call graph*: called by 1 (fetch); 1 external calls (urlsplit).


##### `manifest`  (lines 205–225)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the host system. It tells the host that this file provides a Perplexity search backend and that it needs a Perplexity API key credential.

**Data flow**: It takes no input. It creates a Manifest containing the extension name and version, a credential slot named for the Perplexity API key, and a search provider specification that knows how to build a PerplexitySearchProvider when credentials are available. The output is that Manifest object.

**Call relations**: The host calls this during extension discovery or startup. The manifest connects the outside registration system to PerplexitySearchProvider by supplying a build function, so later search and fetch requests can be routed to this provider.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/research/ufo_ext_research/delegation.py`

`orchestration` · `request handling`

This file exists to solve a practical research problem: if a user has many companies, topics, or entities to investigate, doing them one by one would be slow and repetitive. `wide_research` acts like a team lead handing out the same kind of assignment to several researchers at once, then collecting everyone’s notes into one report.

The tool starts by reading an input file from the sandbox, where each non-empty line is treated as one entity to research. It removes duplicates while keeping the original order, so the same entity is not researched twice. It also enforces a safety limit of 128 entities, which prevents one request from accidentally launching an enormous amount of work.

For each entity, it fills a prompt template by replacing `{entity}` with the actual name. If the caller provided an output schema file, meaning a description of the shape the result should follow, that schema is appended to the research instructions. The file then starts several research subagents in parallel, but uses a semaphore, which is a simple concurrency gate, to keep the number of simultaneous child jobs to 8.

Each child job gets a deterministic deduplication key based on the parent tool call and the entity. That matters for crash recovery: if the parent is retried, already-started or completed child research can be reused instead of duplicated. Finally, the collected rows are written to `wide_research.json`, and the tool returns both the rows and the output filename.

#### Function details

##### `_read_lines`  (lines 42–55)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: This helper reads the entities file and turns it into a clean list of unique names. It is used so the main research tool can work from a simple list instead of raw file text.

**Data flow**: It receives a tool context and a file path. It asks the sandbox to run `cat` on that path, using shell quoting so unusual characters in the filename are treated safely. If the read fails, it raises an error with the sandbox’s message. If it succeeds, it splits the file into lines, trims extra spaces, skips blank lines, removes duplicates, and returns the cleaned list of entities.

**Call relations**: `_wide_research` calls this first, before it can launch any research jobs. `_read_lines` hands back the list of entities that drives the rest of the fan-out work.

*Call graph*: called by 1 (_wide_research); 1 external calls (quote).


##### `_wide_research`  (lines 58–85)

```
async def _wide_research(ctx: ToolContext, args: WideResearchInput) -> ToolResult
```

**Purpose**: This is the main implementation of the `wide_research` tool. It reads the batch input, launches one research subagent per entity with controlled parallelism, saves the combined results, and returns a summary to the caller.

**Data flow**: It receives the tool context and structured user inputs: the entities file, a prompt template, an optional schema file path, and a user description field. It reads and deduplicates the entities, rejects the request if there are more than 128, reads the schema file if available, then creates a concurrency limit of 8. For each entity, it runs the nested `visit` task, waits for all tasks to finish, writes the resulting list of rows to `wide_research.json`, and returns a `ToolResult` containing JSON text with the rows and output filename.

**Call relations**: This function is registered as the handler for `WIDE_RESEARCH_TOOL`, so it runs when the tool is invoked. It calls `_read_lines` to prepare the entity list, uses `asyncio.gather` to run all per-entity visits as a batch, and uses `TextContent` and `ToolResult` to package the final answer in the form the tool system expects.

*Call graph*: calls 1 internal fn (_read_lines); 6 external calls (__init__, __init__, Semaphore, gather, dumps, quote).


##### `_wide_research.visit`  (lines 66–79)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: This nested helper performs the research work for one entity. It builds that entity’s prompt, starts the research subagent, and formats the returned result as one row in the final output file.

**Data flow**: It receives a single entity name. It waits for permission from the semaphore so no more than 8 child jobs run at once, substitutes the entity into the prompt template, appends the output schema text if one was read successfully, and calls `ctx.spawn` to start the research profile. The spawned child’s output is converted to JSON text when present, and the function returns a dictionary with the entity name and its result string.

**Call relations**: `_wide_research` creates one `visit` task for each entity and passes all of them to `asyncio.gather`. Each `visit` call hands the actual research assignment to `ctx.spawn`, using the research profile and a stable deduplication key so retries can reconnect to previous child work instead of starting it again.


### `extensions/research/ufo_ext_research/observations.py`

`domain_logic` · `research result recording and conversation source display`

When the research extension searches the web or fetches a page, the useful source details should not disappear after that single step finishes. This file is the notebook for those source observations. It saves each source under the workspace and conversation that asked for it, so later the system can answer: “What sources were used in this conversation?”

The file defines a database table for saved sources. Each row records the conversation, the source URL, a short title, a snippet of text, an optional publication date, the source’s rank in the result list, and timestamps. Instead of using the full URL as the database key, it stores a SHA-256 digest, which is a fixed-length fingerprint of the URL. That keeps the key predictable while still identifying the URL.

Before saving, the file trims long titles, snippets, and dates to safe limits and validates them as conversation sources. Invalid entries are skipped rather than breaking the whole save. If the same source appears again in the same conversation, the row is updated instead of duplicated. The file also keeps only the newest 100 sources per conversation, like keeping a tidy stack of the most recent reference cards.

At the end, it exposes a conversation slot provider named `SOURCES_SLOT`. This lets the wider system count and read the saved sources when it needs to display them.

#### Function details

##### `_bounded`  (lines 53–54)

```
def _bounded(value: str, limit: int) -> str
```

**Purpose**: This small helper cuts a piece of text down to a maximum length. It is used to keep saved source fields from becoming too large.

**Data flow**: It receives a text value and a character limit. It returns the same text if it is already short enough, or the beginning of the text up to that limit if it is too long. It does not change anything outside itself.

**Call relations**: When `record_sources` prepares a source for saving, it calls `_bounded` on titles, snippets, and publication dates. This keeps the stored data within the file’s chosen safety limits before validation and database writing happen.

*Call graph*: called by 1 (record_sources).


##### `record_sources`  (lines 57–130)

```
async def record_sources(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, sources: tuple[RetrievedSource, ...]) -> None
```

**Purpose**: This is the main saving function for retrieved sources. It writes source information into the database for a specific workspace conversation and turn, updates existing entries for repeated URLs, and trims the stored list to the newest 100 sources.

**Data flow**: It receives an extension context, a conversation ID, a turn ID, and a tuple of retrieved sources. If there are no sources, it stops immediately. Otherwise it opens a database transaction, trims and validates each source, fingerprints the URL, and inserts or updates a database row. After saving, it deletes older extra rows so only the configured source limit is retained for that conversation.

**Call relations**: `record_search_hits` and `record_fetched_page` both turn their own input types into `RetrievedSource` records and then hand them to `record_sources`. Inside, `record_sources` relies on the extension context for the database transaction, uses `_bounded` to shorten fields, uses `ConversationSource` to validate what will later be shown to users, and uses SQL queries to insert, update, and prune rows.

*Call graph*: calls 2 internal fn (transaction, _bounded); called by 2 (record_fetched_page, record_search_hits); 5 external calls (__init__, now, sha256, delete, select).


##### `record_search_hits`  (lines 133–152)

```
async def record_search_hits(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, hits: tuple[SearchHit, ...]) -> None
```

**Purpose**: This function saves a batch of search results as conversation sources. It is used when the research extension has received normal search hits and wants them to appear later in the conversation’s source list.

**Data flow**: It receives the extension context, conversation ID, turn ID, and search hits. For each hit, it copies the URL, title, hit text, and publication date into a `RetrievedSource`. It then passes the whole converted tuple to `record_sources`, which does the actual validation and database writing.

**Call relations**: `record_search_hits` is an adapter: it sits between search-result data and the general source-saving machinery. Rather than writing to the database itself, it reshapes `SearchHit` objects into the common `RetrievedSource` form and hands them off to `record_sources`.

*Call graph*: calls 1 internal fn (record_sources); 1 external calls (__init__).


##### `record_fetched_page`  (lines 155–173)

```
async def record_fetched_page(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, page: FetchedPage) -> None
```

**Purpose**: This function saves a single fetched web page as a conversation source. It is used when the research extension has directly opened or fetched a page, not just seen it as a search result.

**Data flow**: It receives the extension context, conversation ID, turn ID, and fetched page. It builds one `RetrievedSource` using the page URL as both the URL and title, and uses the page summary if available, otherwise the full text. It then sends that one-source tuple to `record_sources` for storage.

**Call relations**: `record_fetched_page` is another adapter around `record_sources`. It translates a `FetchedPage` into the shared source shape, then lets `record_sources` handle trimming, validation, upserting, and cleanup.

*Call graph*: calls 1 internal fn (record_sources); 1 external calls (__init__).


##### `_source_count`  (lines 176–186)

```
async def _source_count(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function tells the conversation UI how many saved sources exist for the current conversation, up to the display limit. It returns no count when there are none.

**Data flow**: It receives a conversation slot context, which includes the extension context and conversation ID. It opens a database transaction, counts rows in the source observation table for that workspace and conversation, and returns either `None` for zero sources or the count capped at 100.

**Call relations**: `_source_count` is connected to `SOURCES_SLOT` as the summary function. When the wider conversation system wants a small summary for the Sources slot, it calls this function to know whether there are sources and how many should be advertised.

*Call graph*: 1 external calls (select).


##### `_read_sources`  (lines 189–217)

```
async def _read_sources(ctx: ConversationSlotContext) -> SourcesSlotPayload
```

**Purpose**: This function reads the saved sources for a conversation and packages them for display. It returns the source list plus a flag saying whether there were more sources than the normal limit.

**Data flow**: It receives a conversation slot context. It queries the database for rows belonging to the current workspace and conversation, ordered with the most recently updated sources first and then by rank. It reads one more than the display limit so it can detect truncation, converts the first 100 rows into `ConversationSource` objects, and returns them inside a `SourcesSlotPayload`.

**Call relations**: `_read_sources` is connected to `SOURCES_SLOT` as the full read function. When the conversation system opens or renders the Sources slot, this function fetches the stored rows, turns raw database data back into validated source objects, and hands the finished payload to the caller.

*Call graph*: 3 external calls (__init__, __init__, select).


### `extensions/research/ufo_ext_research/tools.py`

`domain_logic` · `tool invocation during a turn`

This file is the bridge between an agent asking for outside information and the search service that can provide it. Without it, the agent would not have a clear, validated way to search the web or read public URLs, and failures like “no search backend is configured” could be silent or confusing.

The file defines three tool inputs using Pydantic models, which are schemas that check incoming arguments before the tool runs. `SearchWebInput` limits how many searches can be sent and keeps filters like date ranges and allowed domains separate from the query text. `FetchUrlInput` describes how to fetch one public web page, optionally asking for a summary or extraction. `SearchVerticalInput` supports focused searches for images, people, academic papers, videos, or shopping results.

The actual tool functions then look up the search provider for the current turn, ask it to search or fetch, and package the answer for the model as text containing JSON. Search results can also be recorded into the extension’s observation log, so the system has a trace of what the agent saw. A particularly important safety note is that fetched pages come through the provider’s crawler session, not the user’s workspace. The returned content may reflect the crawler’s identity or login state, so the file always includes provenance text warning about that.

#### Function details

##### `_provider`  (lines 145–148)

```
def _provider(ctx: ToolContext) -> SearchProvider
```

**Purpose**: This function retrieves the search provider for the current tool call. It fails loudly if no provider has been configured, so the agent does not pretend it searched when it had no search service available.

**Data flow**: It receives the current `ToolContext`, which contains turn-level services and state. It checks `ctx.search_provider`; if one is present, it returns that provider. If the value is missing, it raises an error saying there is no configured search provider for this turn.

**Call relations**: The three tool handlers call this first, before doing any web search or URL fetch. It acts like checking that the library is open before sending someone to look for a book: `_search_web`, `_fetch_url`, and `_search_vertical` all depend on it to get the backend they will ask for information.

*Call graph*: called by 3 (_fetch_url, _search_vertical, _search_web).


##### `_results_json`  (lines 151–165)

```
def _results_json(hits: list[SearchHit], answer: str | None) -> str
```

**Purpose**: This function turns search results into a JSON string the model can read consistently. It keeps only the useful public-facing fields: URL, title, snippet text, publication date, highlights, and an optional direct answer.

**Data flow**: It receives a list of `SearchHit` objects and possibly an answer string. It builds a plain dictionary containing a `results` list, adds `answer` when one exists, and converts the whole structure into JSON text. The output is a string ready to be placed inside a tool result.

**Call relations**: Both search tools use this after the search provider returns results. `_search_web` uses it after merging hits from one or more queries, and `_search_vertical` uses it after a specialized search, so both produce the same response format.

*Call graph*: called by 2 (_search_vertical, _search_web); 1 external calls (dumps).


##### `_search_web`  (lines 168–186)

```
async def _search_web(ctx: ToolContext, args: SearchWebInput) -> ToolResult
```

**Purpose**: This is the handler behind the general `search_web` tool. It lets the agent ask one or more natural-language web search questions, applies optional filters like dates or allowed domains, and returns a combined list of results.

**Data flow**: It receives the tool context and validated `SearchWebInput`. First it gets the configured provider. Then, for each query, it creates a `SearchQuery` with the requested result count and filters, asks the provider to search, adds the returned hits to one shared list, and keeps the first direct answer if the provider supplies one. If observation recording is available, it records the hits for the current conversation and turn. Finally it wraps the JSON result text in a `ToolResult`.

**Call relations**: This function is called when the agent invokes the `search_web` tool registered in `RESEARCH_TOOLS`. It relies on `_provider` to find the backend, sends each request to the provider, optionally hands the hits to `record_search_hits` for auditing or timeline use, and uses `_results_json` to shape the final reply.

*Call graph*: calls 2 internal fn (_provider, _results_json); 4 external calls (__init__, __init__, __init__, record_search_hits).


##### `_fetch_url`  (lines 189–210)

```
async def _fetch_url(ctx: ToolContext, args: FetchUrlInput) -> ToolResult
```

**Purpose**: This is the handler behind the `fetch_url` tool. It reads a public HTTP or HTTPS URL through the search provider’s crawler and returns the page text, with an optional summary and a warning about where the content came from.

**Data flow**: It receives the tool context and validated `FetchUrlInput`. It gets the provider, checks whether that provider supports fetching pages, and returns an error message if it does not. If fetching is supported, it builds a `FetchRequest` from the URL, optional prompt, length limit, and cache-bypass flag, then asks the provider to fetch the page. It may record the fetched page in observations. It returns JSON containing the final URL, page text, crawler provenance warning, and summary if one exists.

**Call relations**: This function is called when the agent invokes the `fetch_url` tool registered in `RESEARCH_TOOLS`. It starts with `_provider`, then either stops early with a helpful error if the backend cannot fetch, or hands a fetch request to the provider. Afterward it may call `record_fetched_page` so the system remembers what page content was shown.

*Call graph*: calls 1 internal fn (_provider); 5 external calls (__init__, __init__, __init__, dumps, record_fetched_page).


##### `_search_vertical`  (lines 213–222)

```
async def _search_vertical(ctx: ToolContext, args: SearchVerticalInput) -> ToolResult
```

**Purpose**: This is the handler behind the `search_vertical` tool. It performs a focused search in one content category, such as images, videos, academic papers, shopping listings, or professional profiles.

**Data flow**: It receives the tool context and validated `SearchVerticalInput`. It gets the configured provider, builds a `SearchQuery` using the user’s query plus the chosen vertical, and asks for the default number of results. If observation recording is available, it records the hits. It then turns the hits and optional answer into JSON text and returns that inside a `ToolResult`.

**Call relations**: This function is called when the agent invokes the `search_vertical` tool registered in `RESEARCH_TOOLS`. It follows the same overall path as `_search_web`: get the provider through `_provider`, ask it to search, optionally record what was found with `record_search_hits`, and format the response with `_results_json`.

*Call graph*: calls 2 internal fn (_provider, _results_json); 4 external calls (__init__, __init__, __init__, record_search_hits).
