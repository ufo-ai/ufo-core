# Memory, search, and graph recall  `stage-9.1`

This stage is the system’s recall machinery. During the main conversation loop, it helps find useful facts from past memories, synced pages, and connected knowledge before a response is made. It also does background upkeep when pages or memories change.

The indexing rules in core indexing split text into searchable chunks and define a common interface so the rest of the system does not need to know which search engine is used. The default index stores those chunks locally and searches by matching words or by meaning. The OpenAI embedding extension turns text into number lists, called vectors, so “similar meaning” can be searched even when the same words are not used. The Turbopuffer extension swaps in an outside search service for the same job.

The memory store saves and searches long-term memories, and also indexes source pages. The condenser turns raw pages into cleaner remembered facts and merges related facts over time. The shared memory interface lets callers search memory in a consistent way. The knowledge graph store adds another path: it links entities and relationships, then retrieves nearby connected facts.

## Files in this stage

### Search index backends
Built-in and Turbopuffer-backed indexes store searchable chunks and rely on the shared indexing contract.

### `extensions/index_default/ufo_ext_index_default.py`

`domain_logic` · `indexing and search requests`

This file is the default search engine for stored “chunks,” which are small pieces of text with optional numeric embeddings. An embedding is a list of numbers that represents the meaning of text, so similar text should have similar numbers. Without this file, a basic deployment would have no default way to save searchable chunks or retrieve relevant ones.

The file supports two database worlds. In Postgres, it uses database-native full-text search and pgvector, a Postgres extension for vector comparison. In SQLite, it uses FTS5, SQLite’s built-in full-text search tool, and does vector comparison in Python by scanning rows and computing cosine similarity. The rest of the application does not need to know those details; it only sees neutral objects like Chunk and Hit.

The DefaultIndex class is the main piece. It opens a workspace-scoped database transaction for each operation, then chooses the right SQL based on whether the connection is Postgres or SQLite. It can add or update chunks, delete all chunks for an owner, prune old chunks after re-indexing, search by words, and search by embedding. The manifest function registers this backend under the name "default," so the larger system can discover and create it.

#### Function details

##### `pgvector_literal`  (lines 31–32)

```
def pgvector_literal(vector: tuple[float, ...]) -> str
```

**Purpose**: Turns a Python tuple of numbers into the text format expected by pgvector in Postgres. This lets Postgres receive an embedding as something like a vector value rather than as an ordinary Python object.

**Data flow**: It receives a tuple of floating-point numbers → converts each value to a plain float string and joins them inside square brackets → returns one string that can be passed into Postgres vector SQL.

**Call relations**: When DefaultIndex.upsert stores an embedding in Postgres, it uses this helper to prepare the value for the database. DefaultIndex.vector uses it the same way when sending a query embedding to Postgres for similarity search.

*Call graph*: called by 2 (upsert, vector).


##### `cosine`  (lines 35–43)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Compares two embeddings and returns how similar their directions are. This is used for SQLite vector search, where the database does not do the vector math for this backend.

**Data flow**: It receives two equal-length tuples of numbers → calculates the length of each vector and their dot product → returns a similarity score from the comparison, or 0.0 if either vector has no length.

**Call relations**: DefaultIndex.vector calls this after reading candidate SQLite rows. It uses the score to sort chunks from most similar to least similar before turning them into search hits.

*Call graph*: called by 1 (vector); 1 external calls (sqrt).


##### `pack_embedding`  (lines 46–47)

```
def pack_embedding(vector: tuple[float, ...]) -> bytes
```

**Purpose**: Converts an embedding from Python numbers into raw bytes for storage in SQLite. SQLite stores the embedding as a blob, which is a compact byte string.

**Data flow**: It receives a tuple of floats → packs those floats into a little-endian binary format, meaning a consistent byte order → returns bytes ready to be written into the SQLite chunk table.

**Call relations**: DefaultIndex.upsert calls this only on the SQLite path. It prepares the embedding before saving or updating a chunk.

*Call graph*: called by 1 (upsert); 1 external calls (pack).


##### `unpack_embedding`  (lines 50–51)

```
def unpack_embedding(blob: bytes) -> tuple[float, ...]
```

**Purpose**: Converts an embedding stored as SQLite bytes back into Python numbers. This is needed before Python can compare embeddings.

**Data flow**: It receives a byte blob from the database → interprets every four bytes as one floating-point number → returns a tuple of floats.

**Call relations**: DefaultIndex.vector calls this on SQLite search rows. The unpacked numbers are then passed to cosine to compute similarity against the query embedding.

*Call graph*: called by 1 (vector); 1 external calls (unpack).


##### `_hit`  (lines 54–63)

```
def _hit(row: sa.RowMapping, score: float) -> Hit
```

**Purpose**: Builds a Hit object, which is the standard search-result shape used outside this backend. It hides the database row format from the rest of the index code.

**Data flow**: It receives a database row and a score → copies fields such as chunk digest, owner, subject, ordinal, and text into a Hit → returns the finished Hit with the score converted to a float.

**Call relations**: DefaultIndex.lexical and DefaultIndex.vector both call this after finding matching rows. It is the final translation step from database results into application-level search results.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `DefaultIndex.upsert`  (lines 168–205)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds new chunks to the index or updates existing chunks with the same digest. This is how freshly chunked content becomes searchable.

**Data flow**: It receives a tuple of Chunk objects → if the tuple is empty, it does nothing; otherwise it opens a database transaction → for Postgres, it writes each chunk using pgvector_literal for embeddings; for SQLite, it writes each chunk using pack_embedding and refreshes the matching full-text-search row → it returns nothing, but the database now contains the latest chunk text and embedding.

**Call relations**: The wider indexing system calls this when content needs to be stored or refreshed. Inside the function, pgvector_literal prepares Postgres vector values, while pack_embedding prepares SQLite vector blobs.

*Call graph*: calls 2 internal fn (pack_embedding, pgvector_literal).


##### `DefaultIndex.delete`  (lines 207–214)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Removes all indexed chunks belonging to one owner. An owner is the thing the chunks came from, such as a document or record.

**Data flow**: It receives an IndexScope containing owner kind and owner id → opens a database transaction → in Postgres, deletes matching rows from the chunk table; in SQLite, deletes matching full-text rows first and then chunk rows → it returns nothing, but that owner’s indexed content is gone.

**Call relations**: This can be used directly when an owner should disappear from search. DefaultIndex.prune also calls it when the keep-set is empty, because pruning everything is the same as deleting the whole scope.

*Call graph*: called by 1 (prune).


##### `DefaultIndex.prune`  (lines 216–230)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Removes old chunks for an owner while keeping a known set of current chunk digests. This prevents stale search results after content is re-chunked.

**Data flow**: It receives an IndexScope and a frozenset of chunk digests to keep → if the keep-set is empty, it delegates to delete; otherwise it opens a transaction → deletes all matching chunks not in the keep-set, including SQLite full-text rows when needed → it returns nothing, but only the current chunks remain indexed for that owner.

**Call relations**: The indexing flow uses this after reprocessing an owner, like cleaning a shelf by keeping only the books on a new inventory list. If there is nothing to keep, it hands off to DefaultIndex.delete.

*Call graph*: calls 1 internal fn (delete).


##### `DefaultIndex.lexical`  (lines 232–268)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches chunks by ordinary words. This is the classic search-box path: a user provides text, and the backend finds chunks containing matching terms.

**Data flow**: It receives a query string, allowed subjects, an owner kind, and a result limit → if there are no subjects, or the query becomes empty, it returns no results → otherwise it opens a transaction and runs Postgres full-text search or SQLite FTS5 search → converts each matching row into a Hit with _hit → returns a tuple of ranked hits.

**Call relations**: Search code calls this when it wants word-based results. After the database ranks the matches, this function calls _hit so callers receive the same Hit shape no matter which database was used.

*Call graph*: calls 1 internal fn (_hit).


##### `DefaultIndex.vector`  (lines 270–303)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches chunks by meaning-like numeric similarity instead of exact words. This is useful when a query and a chunk may be related even if they do not share the same terms.

**Data flow**: It receives a query embedding, allowed subjects, an owner kind, and a limit → if the embedding or subject set is empty, it returns no results → in Postgres, it sends the query vector with pgvector_literal and lets the database score rows → in SQLite, it reads candidate rows, unpacks stored embeddings, scores them with cosine, sorts them, and keeps the top results → converts winners into Hit objects with _hit → returns a tuple of hits.

**Call relations**: Search code calls this for vector-based retrieval. On Postgres it relies on the database’s vector comparison; on SQLite it calls unpack_embedding and cosine to do the comparison in Python, then calls _hit to present the results in the common format.

*Call graph*: calls 4 internal fn (_hit, cosine, pgvector_literal, unpack_embedding).


##### `manifest`  (lines 306–316)

```
def manifest() -> Manifest
```

**Purpose**: Advertises this extension to the larger system. It says: this package provides an index backend named "default," and here is how to create it.

**Data flow**: It reads the module constants for name, version, and backend name → builds an IndexBackendSpec whose factory creates a DefaultIndex using the system-provided transaction opener → wraps that in a Manifest → returns the Manifest to the extension loader.

**Call relations**: The extension discovery process calls this when loading available features. It constructs the IndexBackendSpec and Manifest objects that let the core system instantiate DefaultIndex when the default index backend is needed.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/turbopuffer/ufo_ext_turbopuffer.py`

`io_transport` · `startup registration, then indexing and recall during jobs/serve operation`

This extension is the bridge between UFO's memory system and Turbopuffer's HTTP API. UFO breaks source material into chunks, gives each chunk a stable digest, and may attach an embedding, which is a list of numbers that represents the chunk's meaning. This file turns those chunks into Turbopuffer documents and puts them in a workspace-specific namespace, like giving each workspace its own labeled filing cabinet.

It supports two kinds of lookup. Vector search finds chunks that are close in meaning to a query embedding. Lexical search uses BM25, a keyword-ranking method, to find chunks whose text matches a query. Both searches are scoped by owner kind and subject, so recall does not accidentally pull unrelated material.

The file also keeps the remote index tidy. `delete` removes all chunks for one owner. `prune` removes only chunks that are no longer in a supplied keep-set, which matters after re-chunking a document: old pieces should not linger and appear in future search results.

Authentication is done directly in this process. Each request reads the Turbopuffer API key from the configured credential slot and sends it as a Bearer token. The `manifest` function registers this backend so the larger system can select it with the `turbopuffer` index backend setting.

#### Function details

##### `turbopuffer_id`  (lines 42–48)

```
def turbopuffer_id(chunk_digest: str) -> str
```

**Purpose**: This function converts UFO's chunk digest into a document ID that Turbopuffer will accept. Standard SHA-256 digests are shortened into a base64url form, while any non-standard ID is left alone.

**Data flow**: It receives a chunk digest string. If the string looks like `sha256:` followed by 64 hexadecimal characters, it turns the raw digest bytes into a shorter URL-safe text ID and removes padding; otherwise it returns the original string unchanged.

**Call relations**: When chunks are written, `upsert_body` calls this so each chunk has the right remote document ID. When chunks are removed, `TurbopufferIndex.delete` and `TurbopufferIndex.prune` call it so the IDs they send for deletion match the IDs that were originally stored.

*Call graph*: called by 3 (delete, prune, upsert_body); 1 external calls (urlsafe_b64encode).


##### `chunk_digest_from_id`  (lines 51–60)

```
def chunk_digest_from_id(chunk_id: str) -> str
```

**Purpose**: This function converts a Turbopuffer document ID back into UFO's normal chunk digest form. It makes search results look the same to the rest of UFO as the chunks that were originally written.

**Data flow**: It receives a document ID from Turbopuffer. If it is the expected 43-character shortened base64url ID, it tries to decode it back into raw bytes and returns a `sha256:` digest; if decoding fails or the ID has another shape, it returns the ID unchanged.

**Call relations**: `hit_from_row` uses this when turning search rows into `Hit` objects. `_scope_chunks` also uses it while listing chunks for deletion or pruning, so later code can compare normal UFO chunk digests rather than Turbopuffer's shortened IDs.

*Call graph*: called by 2 (_scope_chunks, hit_from_row); 1 external calls (urlsafe_b64decode).


##### `upsert_body`  (lines 63–79)

```
def upsert_body(chunks: tuple[Chunk, ...]) -> dict[str, Any]
```

**Purpose**: This function builds the JSON request body used to insert or update chunks in Turbopuffer. It arranges chunk data into the column-based format that Turbopuffer expects.

**Data flow**: It receives a tuple of `Chunk` objects. It extracts IDs, vectors, owner information, subject, order, and text into parallel lists, declares cosine distance for vector comparison, and marks the text field as searchable for full-text keyword search. It returns a dictionary ready to send as JSON.

**Call relations**: `TurbopufferIndex.upsert` calls this for each write batch before posting to Turbopuffer. Inside this conversion, it calls `turbopuffer_id` so the stored remote IDs use the shortened digest format when appropriate.

*Call graph*: calls 1 internal fn (turbopuffer_id); called by 1 (upsert).


##### `query_filters`  (lines 82–86)

```
def query_filters(owner_kind: str, subjects: frozenset[str]) -> list[Any]
```

**Purpose**: This function builds the filter used for normal search queries. The filter keeps results inside the requested owner kind and subject set.

**Data flow**: It receives an owner kind and a frozen set of subjects. It sorts the subjects for stable output and returns a Turbopuffer filter expression meaning: owner kind must match, and subject must be one of these subjects.

**Call relations**: `TurbopufferIndex._query` calls this whenever lexical or vector search is run. It supplies the scope guard that prevents a search from pulling chunks from the wrong part of memory.

*Call graph*: called by 1 (_query).


##### `scope_filters`  (lines 89–96)

```
def scope_filters(scope: IndexScope, after_id: str | None) -> list[Any]
```

**Purpose**: This function builds the filter used when listing every chunk belonging to one owner. It is mainly used before deleting or pruning remote documents.

**Data flow**: It receives an `IndexScope`, which names an owner kind and owner ID, plus an optional `after_id` cursor. It returns a filter that selects only that owner, and if `after_id` is present, only IDs greater than that value so paging can continue from where the last page ended.

**Call relations**: `TurbopufferIndex._scope_chunks` calls this each time it asks Turbopuffer for a page of chunks in a scope. That listing is then used by `delete` and `prune` to decide which document IDs to remove.

*Call graph*: called by 1 (_scope_chunks).


##### `hit_from_row`  (lines 99–108)

```
def hit_from_row(row: dict[str, Any], score: float) -> Hit
```

**Purpose**: This function turns one row returned by Turbopuffer into a UFO `Hit`, which is the system's normal search-result object. It also attaches the score chosen by the caller.

**Data flow**: It receives a row dictionary from Turbopuffer and a numeric score. It converts the row's ID back into a chunk digest, reads the owner, subject, order, and text fields, and returns a `Hit` containing all of that information.

**Call relations**: `TurbopufferIndex.lexical` and `TurbopufferIndex.vector` both call this after `_query` returns raw rows. It calls `chunk_digest_from_id` so callers outside this file see normal chunk digests rather than Turbopuffer-specific IDs.

*Call graph*: calls 1 internal fn (chunk_digest_from_id); called by 2 (lexical, vector); 1 external calls (__init__).


##### `vector_score`  (lines 111–117)

```
def vector_score(row: dict[str, Any], position: int, total: int) -> float
```

**Purpose**: This function gives a vector-search row a score where higher means better. It normalizes Turbopuffer's distance-style result into the score style expected by UFO recall.

**Data flow**: It receives a result row, the row's position in the result list, and the total number of rows. If Turbopuffer supplied `$dist`, it returns `1 - distance`; otherwise it falls back to a descending rank score based on position.

**Call relations**: `TurbopufferIndex.vector` calls this for each vector-search row before turning the row into a `Hit`. Its score is passed into `hit_from_row`, and rows with non-positive scores are dropped.

*Call graph*: called by 1 (vector).


##### `TurbopufferIndex.upsert`  (lines 131–142)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This method writes new or changed chunks into Turbopuffer. It is how UFO keeps the remote index up to date with the latest chunk text and embeddings.

**Data flow**: It receives a tuple of chunks. It keeps only chunks that have an embedding, gets authorization headers, splits the chunks into batches, converts each batch into a Turbopuffer JSON body, posts it to the workspace namespace, and raises an error if Turbopuffer rejects the request. It returns nothing after the remote index is updated.

**Call relations**: Indexing code calls this when chunks need to be stored for later recall. During the write, it calls `_auth` for the Bearer token, `_path` for the namespace URL, and `upsert_body` to shape the batch for Turbopuffer.

*Call graph*: calls 3 internal fn (_auth, _path, upsert_body).


##### `TurbopufferIndex.delete`  (lines 144–152)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This method removes all indexed chunks for a particular scope, such as all chunks belonging to one source owner. It prevents deleted or replaced material from still appearing in search results.

**Data flow**: It receives an `IndexScope`. It gets authorization headers, lists the chunks currently in that scope, converts their chunk digests into Turbopuffer document IDs, sends batched delete requests, and raises an error if any request fails. The remote namespace is changed by removing those documents.

**Call relations**: Cleanup or reindexing flows call this when an entire scoped set should disappear. It relies on `_scope_chunks` to discover what exists, `turbopuffer_id` to produce matching remote IDs, `_auth` for credentials, and `_path` for the Turbopuffer namespace endpoint.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.prune`  (lines 154–164)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This method removes only outdated chunks from a scope while keeping the chunks whose digests are still valid. It is useful after a document has been re-chunked, because only obsolete pieces should be deleted.

**Data flow**: It receives an `IndexScope` and a set of chunk digests to keep. It gets authorization headers, lists all chunks in the scope, filters out the keep-set, converts the remaining digests into Turbopuffer IDs, and sends batched delete requests. The result is that the remote index keeps only the desired chunks for that owner.

**Call relations**: Reindexing code calls this when it knows which chunks should survive. Like `delete`, it uses `_scope_chunks` to inspect current remote contents, `turbopuffer_id` to address documents for deletion, `_auth` to authorize the work, and `_path` to target the right namespace.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.lexical`  (lines 166–174)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This method searches indexed chunk text using keywords. It uses BM25, a common text-ranking method that rewards useful word matches, to return matching chunks.

**Data flow**: It receives a query string, subjects to search within, an owner kind, and a maximum result count. If the query is blank or there are no subjects, it returns no hits. Otherwise it asks `_query` to rank rows by BM25 text matching, then turns each row into a `Hit` with a simple descending score.

**Call relations**: Recall code calls this when it wants keyword-based results. It delegates the HTTP request details to `_query`, then uses `hit_from_row` to translate Turbopuffer rows into the system's standard result objects.

*Call graph*: calls 2 internal fn (_query, hit_from_row).


##### `TurbopufferIndex.vector`  (lines 176–185)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This method searches indexed chunks by meaning using an embedding. It asks Turbopuffer for approximate nearest neighbors, meaning chunks whose stored vectors are close to the query vector.

**Data flow**: It receives an embedding, subjects to search within, an owner kind, and a limit. If the embedding is empty or there are no subjects, it returns no hits. Otherwise it sends a vector query, computes a score for each returned row, keeps only positive scores, and returns standard `Hit` objects.

**Call relations**: Recall code calls this when it wants semantic results rather than plain keyword matches. It uses `_query` for the Turbopuffer call, `vector_score` to convert distances into useful scores, and `hit_from_row` to produce the final hits.

*Call graph*: calls 3 internal fn (_query, hit_from_row, vector_score).


##### `TurbopufferIndex._query`  (lines 187–200)

```
async def _query(self, rank_by: list[Any], owner_kind: str, subjects: frozenset[str], limit: int) -> list[dict[str, Any]]
```

**Purpose**: This helper performs the shared Turbopuffer query request used by both keyword and vector search. It hides the repeated details of filters, included fields, authorization, and response parsing.

**Data flow**: It receives a `rank_by` instruction, owner kind, subjects, and limit. It builds a JSON body with ranking, maximum result count, requested attributes, and scope filters, posts it to the namespace query endpoint, returns an empty list if the namespace does not exist, raises on other HTTP errors, and otherwise returns the response rows.

**Call relations**: `lexical` and `vector` both call this rather than each building their own HTTP request. It calls `query_filters` to keep results scoped, `_path` to find the namespace query URL, and `_auth` to attach the current API key.

*Call graph*: calls 3 internal fn (_auth, _path, query_filters); called by 2 (lexical, vector).


##### `TurbopufferIndex._scope_chunks`  (lines 202–230)

```
async def _scope_chunks(self, scope: IndexScope, headers: dict[str, str]) -> list[Chunk]
```

**Purpose**: This helper lists all chunks in one owner scope from Turbopuffer. It is used before deletion-style operations so the code knows exactly which remote documents exist.

**Data flow**: It receives an `IndexScope` and already-prepared authorization headers. It repeatedly queries Turbopuffer in pages ordered by ID, asking for chunk attributes and using `after_id` to move to the next page. It converts each row into a lightweight `Chunk` without an embedding and returns the full list; if the namespace is missing, it returns whatever has been collected, usually an empty list.

**Call relations**: `delete` and `prune` call this as their first step. It uses `scope_filters` to select the right owner and page forward, `_path` to reach the query endpoint, and `chunk_digest_from_id` while rebuilding chunks from Turbopuffer rows.

*Call graph*: calls 3 internal fn (_path, chunk_digest_from_id, scope_filters); called by 2 (delete, prune); 1 external calls (__init__).


##### `TurbopufferIndex._auth`  (lines 232–234)

```
async def _auth(self) -> dict[str, str]
```

**Purpose**: This helper creates the HTTP authorization header for Turbopuffer. It reads the API key from the configured credential slot each time it is needed.

**Data flow**: It reads the `turbopuffer_api_key` credential through the credential access object. It returns a header dictionary containing `Authorization: Bearer <key>`, which can be sent with an HTTP request.

**Call relations**: `upsert`, `delete`, `prune`, and `_query` call this before making Turbopuffer requests. It centralizes how the backend obtains and formats credentials so the rest of the methods do not duplicate that logic.

*Call graph*: called by 4 (_query, delete, prune, upsert).


##### `TurbopufferIndex._path`  (lines 236–237)

```
def _path(self, suffix: str='') -> str
```

**Purpose**: This helper builds the namespace path used for Turbopuffer requests. The namespace includes the workspace ID, so each workspace stores and searches its own documents.

**Data flow**: It receives an optional suffix such as `/query`. It reads the workspace ID from the credential access object, prefixes it with `ufo-`, appends the suffix, and returns the resulting API path.

**Call relations**: `upsert`, `delete`, `prune`, `_query`, and `_scope_chunks` call this whenever they need to address the correct Turbopuffer namespace. It is the small piece that keeps all HTTP operations pointed at the workspace-specific filing cabinet.

*Call graph*: called by 5 (_query, _scope_chunks, delete, prune, upsert).


##### `manifest`  (lines 240–260)

```
def manifest() -> Manifest
```

**Purpose**: This function declares this extension to the UFO plugin system. It tells UFO the extension's name, version, needed credential, and how to build the Turbopuffer index backend.

**Data flow**: It takes no input. It creates a manifest containing a credential slot named `turbopuffer_api_key` and an index backend spec named `turbopuffer`; the backend factory builds a `TurbopufferIndex` with the runtime credential access object and an HTTP client configured for Turbopuffer's base URL and timeout.

**Call relations**: The core system calls this during extension discovery or startup. The returned manifest lets configuration select the `turbopuffer` backend, after which the factory creates the live `TurbopufferIndex` used by indexing and recall operations.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `core/src/ufo/indexing.py`

`domain_logic` · `indexing and search preparation`

This file is the meeting point between ordinary text and the search index. When a memory item or page changes, the system cannot usually search one huge block of text well. It first cuts the text into smaller pieces, gives each piece a stable fingerprint, asks an embedding service to turn each piece into numbers that represent meaning, and then stores those pieces in an index. Without this step, search results could be stale, too large, duplicated, or tied too closely to one storage technology.

The file keeps two ideas separate. `TextChunker` knows how to split text into useful chunks. It tries larger natural breaks first, like paragraphs and sentences, then falls back to smaller breaks when needed. It also adds a little overlap between neighboring chunks, like repeating the last line of one page at the top of the next, so context is not lost. Very long chunks are capped by character length for safety.

`IndexBackend` and `EmbedClient` are protocols, meaning they describe what another component must provide. One component must store and search chunks; another must create embeddings, which are lists of numbers used for meaning-based search. The helper `chunk_embed_upsert` ties the pieces together: split the text, embed the chunks, save them, then remove old chunks for the same owner that no longer exist after an edit.

#### Function details

##### `IndexBackend.upsert`  (lines 68–68)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This is the promised operation for saving chunks into an index. “Upsert” means insert if new, or update if the same chunk already exists.

**Data flow**: It receives a group of `Chunk` objects, each containing text, owner information, and usually an embedding. The concrete index implementation writes those chunks into its storage. Nothing is returned; the index is changed so those chunks can be found later.

**Call relations**: `chunk_embed_upsert` calls this after text has been split and embedded. The protocol does not say how storage works; it lets database-specific extensions provide the actual saving behavior.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.delete`  (lines 70–70)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This is the promised operation for removing all indexed chunks that belong to one owner, such as one page or one memory item.

**Data flow**: It receives an `IndexScope`, which names the kind of owner and the owner’s ID. The concrete backend removes matching chunks from its index. It returns nothing, but the stored search data is reduced.

**Call relations**: This file defines the operation as part of the backend contract. Other parts of the system can call it when an owner is deleted, while the backend extension decides how the deletion is performed.


##### `IndexBackend.prune`  (lines 72–72)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This is the promised operation for deleting old chunks for an owner while keeping the chunks that still belong there. It prevents edited text from leaving stale search results behind.

**Data flow**: It receives an owner scope and a set of chunk fingerprints to keep. The backend compares what is stored for that owner against the keep set, then deletes anything outside it. It returns nothing, but the index is cleaned up.

**Call relations**: `chunk_embed_upsert` calls this every time it finishes preparing a body of text. If the new body is empty, the keep set is empty too, so pruning removes all chunks for that owner.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.lexical`  (lines 74–76)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the promised operation for text-match search, where the query is matched against words in indexed chunks. It is useful for finding exact or near-exact wording.

**Data flow**: It receives a query string, allowed subjects, an owner kind, and a maximum number of results. The backend searches stored chunk text under those filters and returns matching `Hit` objects with scores. The index is read, not changed.

**Call relations**: This file only defines the shape of the call. Search orchestration elsewhere can ask any backend for lexical results without knowing whether it uses full-text search, SQL, or another search tool underneath.


##### `IndexBackend.vector`  (lines 78–80)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the promised operation for meaning-based search using an embedding, which is a numeric summary of a text’s meaning. It helps find related text even when the exact words differ.

**Data flow**: It receives a query embedding, allowed subjects, an owner kind, and a result limit. The backend compares that embedding with stored chunk embeddings and returns the closest `Hit` objects with scores. It reads from the index and does not change it.

**Call relations**: This protocol method lets search code request semantic matches from any backend implementation. The backend is responsible for the actual vector search technique.


##### `EmbedClient.embed`  (lines 84–84)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This is the promised operation for turning text chunks into embeddings, which are lists of numbers used for meaning-based search.

**Data flow**: It receives a group of text strings. The concrete embedding client sends or computes them through an embedding model and returns one numeric vector for each input text. It does not decide where the vectors are stored.

**Call relations**: `chunk_embed_upsert` calls this after chunking text and before saving the chunks. This protocol keeps the indexing code independent from any particular embedding provider.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `chunk_embed_upsert`  (lines 87–112)

```
async def chunk_embed_upsert(index: IndexBackend, embed: EmbedClient, chunker: 'TextChunker', owner_kind: str, owner_id: str, subject: str, body: str) -> None
```

**Purpose**: This function is the shared indexing workflow for one body of text. It cuts the body into chunks, embeds each chunk, saves them, and removes stale chunks from previous versions of the same owner.

**Data flow**: It receives an index backend, an embedding client, a chunker, owner details, a subject, and the text body. First it asks the chunker for chunks. If there are chunks, it sends their text to the embedder, attaches the returned vectors to the chunks, and upserts them into the index. Finally it prunes the owner’s stored chunks so only the newly produced chunk fingerprints remain.

**Call relations**: This is the bridge between text preparation and storage. It calls `EmbedClient.embed` for numeric meaning vectors, `IndexBackend.upsert` to save fresh chunks, and `IndexBackend.prune` to remove old ones; it also creates an `IndexScope` to describe which owner is being cleaned up.

*Call graph*: calls 3 internal fn (embed, prune, upsert); 2 external calls (__init__, replace).


##### `TextChunker.chunk`  (lines 121–132)

```
def chunk(self, text: str, owner_kind: str, owner_id: str, subject: str) -> tuple[Chunk, ...]
```

**Purpose**: This is the public chunking method. It turns one text body into ordered `Chunk` records that carry ownership, subject, text, and a stable digest.

**Data flow**: It receives raw text plus the owner kind, owner ID, and subject. It asks `_slices` to produce the actual text pieces, then numbers them in order and creates a digest for each one. It returns a tuple of `Chunk` objects with no embeddings yet.

**Call relations**: `chunk_embed_upsert` uses this as the first step in indexing. Inside the chunker, this method relies on `_slices` for splitting and `_digest` for stable fingerprints.

*Call graph*: calls 2 internal fn (_digest, _slices); 1 external calls (__init__).


##### `TextChunker._slices`  (lines 134–142)

```
def _slices(self, text: str) -> list[str]
```

**Purpose**: This function decides the overall splitting plan for a text body. It returns clean text pieces that are small enough for indexing but still try to preserve readable context.

**Data flow**: It receives raw text. If the text is blank, it returns no pieces. If it is already short enough, it only applies the character cap. Otherwise it recursively splits the text, merges small pieces into useful-sized chunks, adds overlap between neighboring chunks, and caps each final piece by character length. The output is a list of strings.

**Call relations**: `TextChunker.chunk` calls this before wrapping pieces into `Chunk` records. It coordinates the helper methods `_count_words`, `_recursive_split`, `_greedy_merge`, `_apply_overlap`, and `_cap_by_chars`.

*Call graph*: calls 5 internal fn (_apply_overlap, _cap_by_chars, _count_words, _greedy_merge, _recursive_split); called by 1 (chunk).


##### `TextChunker._count_words`  (lines 145–151)

```
def _count_words(text: str) -> int
```

**Purpose**: This function estimates how large a piece of text is in word-like units. It has special behavior for Chinese, Japanese, and Korean text, where spaces are not always used between words.

**Data flow**: It receives a string and removes whitespace to see how much real text is present. If the text has enough CJK characters, it counts non-whitespace characters as the size. Otherwise it counts runs of non-space text as words. It returns an integer size estimate.

**Call relations**: The chunking helpers call this when deciding whether text is already small enough, whether a split piece is too large, and whether merged pieces are still within the target size.

*Call graph*: called by 3 (_greedy_merge, _recursive_split, _slices); 1 external calls (sub).


##### `TextChunker._cap_by_chars`  (lines 153–165)

```
def _cap_by_chars(self, text: str) -> list[str]
```

**Purpose**: This function enforces a hard maximum character length for chunks. It is a safety net for text that is too long even after word-based splitting.

**Data flow**: It receives one text string. If it is within the character limit, it returns it as one piece, unless it is empty. If it is too long, it slices the text into overlapping character windows and returns the non-empty pieces. The overlap reduces the chance of cutting away important context at a boundary.

**Call relations**: `_slices` calls this for short whole texts and again after the main chunking pipeline. It is the final guardrail before chunks are turned into index records.

*Call graph*: called by 1 (_slices).


##### `TextChunker._recursive_split`  (lines 167–179)

```
def _recursive_split(self, text: str, level: int) -> list[str]
```

**Purpose**: This function breaks large text apart by trying natural separators from broad to narrow. It starts with paragraph-like breaks, then lines, then sentence punctuation, then smaller punctuation, and finally whitespace.

**Data flow**: It receives text and a delimiter level. It tries to split the text using the delimiters for that level. If nothing useful splits, it moves to the next level. If a resulting piece is still too large, it recursively splits that piece more finely. It returns a list of smaller strings.

**Call relations**: `_slices` calls this when a text is too large. It uses `_split_at_delimiters` for punctuation-based cutting, `_count_words` to judge piece size, and `_split_on_whitespace` as the last fallback.

*Call graph*: calls 3 internal fn (_count_words, _split_at_delimiters, _split_on_whitespace); called by 1 (_slices).


##### `TextChunker._split_at_delimiters`  (lines 182–195)

```
def _split_at_delimiters(text: str, delimiters: tuple[str, ...]) -> list[str]
```

**Purpose**: This function cuts text at the earliest matching delimiter from a given set, while keeping the delimiter attached to the piece. That helps chunks keep their punctuation and read naturally.

**Data flow**: It receives text and a set of delimiter strings, such as paragraph breaks or punctuation marks. It repeatedly finds the next earliest delimiter, cuts there, and continues with the remaining text. It returns only pieces that contain non-whitespace content.

**Call relations**: `_recursive_split` calls this at each delimiter level. It does one small job: perform the cut requested by the recursive splitting strategy.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._split_on_whitespace`  (lines 197–213)

```
def _split_on_whitespace(self, text: str) -> list[str]
```

**Purpose**: This is the fallback splitter when punctuation and paragraph breaks are not enough. It divides text by runs of whitespace, or by raw characters when there are no usable word breaks.

**Data flow**: It receives text. If it can find word-like runs, it groups them into chunks of the target word count. If it cannot, or if there is one very long run, it slices the raw text by the target size. It returns non-empty string pieces.

**Call relations**: `_recursive_split` calls this only after all delimiter levels are exhausted. It ensures the chunker can still make progress on difficult input, such as minified text or a single long token.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._greedy_merge`  (lines 215–229)

```
def _greedy_merge(self, pieces: list[str]) -> list[str]
```

**Purpose**: This function puts small split pieces back together into fuller chunks. It prevents the index from being filled with tiny fragments when natural delimiters produce many short pieces.

**Data flow**: It receives a list of pieces. Starting with the first, it keeps appending the next piece as long as the combined text stays within a generous size limit. When adding another piece would make it too large, it saves the current chunk and starts a new one. It returns a list of merged chunks.

**Call relations**: `_slices` calls this after recursive splitting. It uses `_count_words` to decide whether a combined chunk is still small enough, with `math.ceil` used to compute the allowed size.

*Call graph*: calls 1 internal fn (_count_words); called by 1 (_slices); 1 external calls (ceil).


##### `TextChunker._apply_overlap`  (lines 231–237)

```
def _apply_overlap(self, chunks: list[str]) -> list[str]
```

**Purpose**: This function adds a little context from the end of each chunk to the start of the next one. The goal is to avoid losing meaning when an important sentence crosses a chunk boundary.

**Data flow**: It receives a list of chunks. If there is only one chunk or overlap is disabled, it returns the chunks unchanged. Otherwise it pairs each chunk with the one before it, prefixes trailing context from the previous chunk, and returns the expanded list.

**Call relations**: `_slices` calls this after merging chunks. It relies on `_trailing_context` to choose the repeated text and uses pairwise iteration to walk neighboring chunks.

*Call graph*: calls 1 internal fn (_trailing_context); called by 1 (_slices); 1 external calls (pairwise).


##### `TextChunker._trailing_context`  (lines 239–249)

```
def _trailing_context(self, text: str) -> str
```

**Purpose**: This function chooses what text from the end of one chunk should be repeated before the next chunk. It tries to return useful recent context without restarting too far back.

**Data flow**: It receives one chunk of text. It gathers the last configured number of word-like runs. If there are not enough words, it returns an empty string. If it finds a sentence boundary in the first half of that trailing text, it starts after that boundary so the overlap begins cleanly; otherwise it returns the whole trailing portion.

**Call relations**: `_apply_overlap` calls this for each previous chunk when building overlapped chunks. It is the part that makes overlap sentence-aware rather than blindly copying a fixed number of characters.

*Call graph*: called by 1 (_apply_overlap).


##### `TextChunker._digest`  (lines 252–254)

```
def _digest(owner_kind: str, owner_id: str, subject: str, ordinal: int, text: str) -> str
```

**Purpose**: This function creates a stable fingerprint for a chunk. The fingerprint lets the index recognize the same chunk again on a later run.

**Data flow**: It receives the owner kind, owner ID, subject, chunk number, and chunk text. It joins those fields with a separator that is unlikely to appear by accident, hashes the result with SHA-256, and returns the hash string with a `sha256:` prefix. It does not change any outside state.

**Call relations**: `TextChunker.chunk` calls this for every produced text piece. `chunk_embed_upsert` later uses these digests indirectly when saving chunks and when telling the backend which old chunks to keep during pruning.

*Call graph*: called by 1 (chunk); 1 external calls (sha256).


### Memory storage and recall
The memory extension condenses raw sources into durable facts, stores and indexes them, and exposes a common memory-search interface.

### `extensions/memory/ufo_ext_memory/condenser.py`

`domain_logic` · `page-change replay and periodic memory consolidation`

The memory system receives page changes, but a page is often too large and messy to remember directly. This file acts like a careful note-taker. First, FactDeriver reads changed pages and asks the language model to pull out short, standalone facts worth keeping. It ignores deleted pages and very tiny pages, limits how much text it sends to the model, checks the model’s answer before trusting it, and writes only usable facts into the memory store.

Later, MemoryConsolidator does housekeeping. It looks for older fact memories that have not already been replaced. It groups them by subject, turns their text into embeddings (number lists that represent meaning), and compares those embeddings to find facts that are probably about the same thing. When a cluster is large enough, it asks the model to write one concise summary. It then stores that summary as a semantic memory and marks the original facts as superseded, like filing several sticky notes under one clean index card.

An important safety feature is that model work happens before database write transactions whenever possible, so the system does not hold a database lock while waiting for a model. If no model is configured, this file mostly steps aside instead of breaking the run.

#### Function details

##### `FactDeriver.apply`  (lines 106–115)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: This is the entry point for turning a delivered batch of changed pages into fact memories. It filters out pages that are deleted or too short to be useful, and skips the whole step if no language model is available.

**Data flow**: It receives a tuple of page changes. It keeps only non-deleted pages whose body is long enough, splits them into small batches, and sends each batch onward for fact extraction. It returns nothing, but it may cause new memory facts to be written through later steps.

**Call relations**: The page-change runner calls this when it has a batch ready. This function is the gatekeeper: after basic filtering and batching, it hands each group to FactDeriver._derive, which does the actual extraction and writing.

*Call graph*: calls 1 internal fn (_derive); 1 external calls (batched).


##### `FactDeriver._derive`  (lines 117–132)

```
async def _derive(self, model: ModelAccess, pages: tuple[PageChange, ...]) -> None
```

**Purpose**: This takes a small group of source pages and turns the model’s extracted facts into memory-store writes. It also makes sure each extracted fact really points back to one of the pages in the current group and is notable enough to keep.

**Data flow**: It receives a model connection and a tuple of pages. It builds a lookup from page ID to page, asks FactDeriver._extract for candidate facts, drops candidates with an unknown page ID or low notability, and commits each accepted fact as a MemoryWrite with subject, body, kind, confidence, and source reference. The result is no return value, but the memory store may gain new fact items.

**Call relations**: FactDeriver.apply calls this for each bounded page group. It relies on FactDeriver._extract to talk to the model, then hands accepted facts to the memory store using MemoryWrite so they become durable memories.

*Call graph*: calls 1 internal fn (_extract); called by 1 (apply); 1 external calls (__init__).


##### `FactDeriver._extract`  (lines 134–150)

```
async def _extract(self, model: ModelAccess, pages: tuple[PageChange, ...]) -> tuple[ExtractedFact, ...]
```

**Purpose**: This asks the language model to read a bounded set of pages and return structured fact candidates. It is the only fact-derivation step that talks directly to the model.

**Data flow**: It receives a model connection and pages. It trims each page body to a maximum size, packages the pages as compact JSON, builds a model request with instructions for extracting notable facts, sends the request, and passes the model’s text response to _parse_facts. It returns a tuple of validated ExtractedFact objects, or an empty tuple if the response cannot be used.

**Call relations**: FactDeriver._derive calls this when it needs candidate facts. This function hands the raw model output to _parse_facts, keeping the rest of the derivation code from having to deal with messy or malformed model text.

*Call graph*: calls 2 internal fn (complete, _parse_facts); called by 1 (_derive); 3 external calls (__init__, __init__, dumps).


##### `MemoryConsolidator.run`  (lines 180–189)

```
async def run(self) -> None
```

**Purpose**: This is the periodic consolidation job. It finds old fact memories, groups related ones, and replaces each strong cluster with one cleaner semantic summary.

**Data flow**: It starts with no direct input besides the consolidator’s configured store, workspace, embedder, and optional model. If there is no model, it stops. Otherwise it reads aged facts, groups them by subject, embeds each eligible group, clusters facts by meaning, and consolidates clusters large enough to summarize. It returns nothing, but may insert semantic summary memories and mark old facts as superseded.

**Call relations**: A scheduler or background job calls this on an interval. It coordinates the whole consolidation pipeline by calling _aged_facts, _buckets, _embed, _clusters, and _consolidate in order.

*Call graph*: calls 5 internal fn (_aged_facts, _buckets, _clusters, _consolidate, _embed).


##### `MemoryConsolidator._aged_facts`  (lines 191–220)

```
async def _aged_facts(self) -> tuple[_AgedFact, ...]
```

**Purpose**: This fetches fact memories that are old enough to be considered for summarizing and have not already been superseded. It keeps the scan bounded so one run cannot grow without limit.

**Data flow**: It reads the current time, calculates an age cutoff, opens a database transaction, and selects recent-enough candidate rows for the configured workspace. It converts each database row into a small _AgedFact record containing ID, subject, body, confidence, and creation time. It returns those records as a tuple.

**Call relations**: MemoryConsolidator.run calls this first to get the raw candidates. The returned facts feed into _buckets, which starts organizing them for comparison.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryConsolidator._buckets`  (lines 222–231)

```
def _buckets(self, facts: tuple[_AgedFact, ...]) -> tuple[tuple[str, tuple[_AgedFact, ...]], ...]
```

**Purpose**: This groups candidate facts by subject so facts about different people, projects, or topics are not mixed together. It also caps each subject group to a maximum size.

**Data flow**: It receives aged facts. It builds groups keyed by each fact’s subject, sorts each group by recency, trims large groups to the configured limit, and returns subject-plus-facts pairs. It does not write anything.

**Call relations**: MemoryConsolidator.run calls this after reading aged facts. Only buckets with enough facts continue to _embed and _clusters, so this function helps avoid wasting model and embedding work on tiny groups.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._embed`  (lines 233–237)

```
async def _embed(self, facts: tuple[_AgedFact, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: This turns fact text into embeddings, which are number lists used to compare meaning. The consolidator uses these numbers to find facts that are similar without relying on exact matching words.

**Data flow**: It receives a tuple of aged facts. It trims each fact body to a safe length, sends all trimmed bodies to the embedding service, and pairs each returned vector back to the matching fact ID. It returns a dictionary from fact ID to embedding vector.

**Call relations**: MemoryConsolidator.run calls this for each subject bucket that is large enough to consolidate. The resulting vectors are passed to _clusters, where they are compared with _cosine.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._clusters`  (lines 239–258)

```
def _clusters(self, facts: tuple[_AgedFact, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_AgedFact, ...], ...]
```

**Purpose**: This groups facts that appear semantically close to each other. It uses a simple greedy method: each fact joins the first existing cluster whose lead fact is similar enough, otherwise it starts a new cluster.

**Data flow**: It receives facts and their embedding vectors. It sorts facts newest first, compares each fact’s vector with the first fact in existing clusters using _cosine, and builds clusters from those matches. It returns a tuple of fact clusters; later code decides which clusters are large enough to summarize.

**Call relations**: MemoryConsolidator.run calls this after embeddings are available. It calls _cosine for the actual similarity score, then hands its clusters back to run, which sends large enough clusters to _consolidate.

*Call graph*: calls 1 internal fn (_cosine); called by 1 (run).


##### `MemoryConsolidator._consolidate`  (lines 260–286)

```
async def _consolidate(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> None
```

**Purpose**: This replaces a cluster of related old facts with one new semantic summary. It writes the summary and marks the original facts as superseded in the same database transaction, so the memory index stays consistent.

**Data flow**: It receives a model connection and a fact cluster. It asks _summarize for a summary string; if the summary is empty, it does nothing. Otherwise it creates a new ID, inserts a semantic memory row with the cluster’s subject and highest confidence, then updates all original fact rows so their superseded_by field points to the new summary. It returns nothing, but changes the database.

**Call relations**: MemoryConsolidator.run calls this for each cluster that is large enough. It delegates the language-model wording to _summarize, then performs the database insert and update that make consolidation visible to recall.

*Call graph*: calls 1 internal fn (_summarize); called by 1 (run); 3 external calls (insert, update, uuid4).


##### `MemoryConsolidator._summarize`  (lines 288–297)

```
async def _summarize(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> str
```

**Purpose**: This asks the language model to write one concise statement that captures several related facts. It limits both the input facts and the returned summary size.

**Data flow**: It receives a model connection and a fact cluster. It trims each fact body, packages the list as compact JSON, builds a model request with summary instructions, sends it to the model, strips whitespace, and truncates the result to the maximum summary length. It returns the summary text.

**Call relations**: MemoryConsolidator._consolidate calls this before opening the write transaction. That separation keeps slow model waiting outside the database write section.

*Call graph*: calls 1 internal fn (complete); called by 1 (_consolidate); 3 external calls (__init__, __init__, dumps).


##### `_recency`  (lines 300–301)

```
def _recency(fact: _AgedFact) -> tuple[datetime, UUID]
```

**Purpose**: This provides a consistent sorting key for facts based on when they were created, with the ID as a tie-breaker. It helps the consolidator process newer facts first when ordering matters.

**Data flow**: It receives one _AgedFact. It reads the fact’s creation time and ID, then returns them as a pair that Python can use for sorting. It does not change anything.

**Call relations**: This helper supports the consolidator’s ordering decisions. It is used where facts need to be sorted by newest-first behavior, such as bucket trimming and clustering order.


##### `_cosine`  (lines 304–310)

```
def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: This measures how similar two embedding vectors are. A higher score means the two pieces of text are closer in meaning, which helps decide whether facts belong in the same cluster.

**Data flow**: It receives two equal-length tuples of numbers. It calculates the cosine similarity by comparing their direction rather than their size; if either vector has zero length, it safely returns 0.0. It returns a floating-point similarity score.

**Call relations**: MemoryConsolidator._clusters calls this while deciding whether a fact should join an existing cluster. The cluster threshold is applied to this score.

*Call graph*: called by 1 (_clusters); 1 external calls (sqrt).


##### `_parse_facts`  (lines 313–335)

```
def _parse_facts(text: str) -> tuple[ExtractedFact, ...]
```

**Purpose**: This safely reads the model’s fact-extraction response and keeps only valid fact objects. It is deliberately forgiving, so one bad item from the model does not ruin the whole page batch.

**Data flow**: It receives raw text from the model. It looks for the first JSON object, tries to decode it, checks that it contains a facts list, and validates each list item as an ExtractedFact. Invalid JSON, missing lists, non-object items, and validation failures are dropped. It returns a tuple of valid facts, possibly empty.

**Call relations**: FactDeriver._extract calls this after the model replies. This helper forms the trust boundary between unpredictable model text and the memory-writing code that follows.

*Call graph*: called by 1 (_extract); 1 external calls (JSONDecoder).


### `extensions/memory/ufo_ext_memory/store.py`

`domain_logic` · `request handling and background indexing`

This file solves a practical problem: an assistant needs to remember useful facts without slowing down every write. When something is saved, it is written as one row in the memory table, but the heavier work — splitting text into chunks and turning it into embeddings, which are number lists used for meaning-based search — is done later by an indexer job. That keeps saving fast and predictable.

For recall, the file combines several signals. It searches by words, searches by meaning, and also checks a small “tail” of new memories that have not been indexed yet. It then fuses those results, reads the matching memory rows back from the database, applies age and confidence decay for facts, limits how much one memory type can dominate the answer, and turns episodic memories into topic pointers instead of injecting their full text.

The file also supports an operator inventory view, showing stored memories plus their indexing state and live decay weight. Finally, it includes two background workers: MemoryIndexer indexes memory rows that are due, while PageIndexer keeps indexed source pages and a small page mirror table in sync. Without this file, the extension could store text, but it would not have reliable, scoped, searchable memory.

#### Function details

##### `recall_subjects`  (lines 107–112)

```
def recall_subjects(member_id: UUID | None) -> frozenset[str]
```

**Purpose**: Chooses which memory spaces are visible during recall. A conversation can see the shared memory space, and if it is linked to a member, that member’s private memory space too.

**Data flow**: It receives an optional member ID. If there is no member ID, it returns only the shared subject; if there is one, it converts that member ID into the member subject and returns both that and the shared subject.

**Call relations**: This is a small scoping helper used before recall-style operations. It relies on the source helper that formats a member-specific subject, so later searches can filter memory by the right visibility boundary.

*Call graph*: 1 external calls (member_subject).


##### `inventory`  (lines 143–193)

```
async def inventory(transaction: Transaction, workspace_id: UUID) -> tuple[MemoryInventoryItem, ...]
```

**Purpose**: Reads a bounded list of stored memories for an operator or explorer view. It shows not only the saved text, but also whether it has been indexed and how strongly it would currently count during recall.

**Data flow**: It receives a transaction opener and workspace ID. It reads the newest memory rows for that workspace from the database, then computes age, half-life, and decay weight using one shared current time, and returns MemoryInventoryItem objects.

**Call relations**: This function stands apart from normal recall: it does not search by a query or hide superseded rows. It calls the time-normalizing and decay helpers so the explorer reports the same aging behavior that recall uses.

*Call graph*: calls 3 internal fn (_aware, decay_multiplier, half_life_days); 3 external calls (__init__, now, select).


##### `_aware`  (lines 196–197)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Makes sure a datetime has a timezone. This avoids mistakes when comparing stored times with current UTC time.

**Data flow**: It receives a datetime. If the datetime already has timezone information, it returns it unchanged; otherwise it treats it as UTC and returns a timezone-aware copy.

**Call relations**: It is used by inventory and decay calculations before doing age math. That keeps time comparisons consistent even if a database driver returns a naive datetime, meaning one without timezone attached.

*Call graph*: called by 2 (decay_multiplier, inventory); 1 external calls (replace).


##### `_fuse`  (lines 235–258)

```
def _fuse(legs: tuple[tuple[Hit, ...], ...], cosine_leg: tuple[Hit, ...]) -> dict[str, tuple[float, float, str]]
```

**Purpose**: Combines search hits from multiple search methods into one best score per owning item or page. It is the shared ranking core behind memory recall and source-page search.

**Data flow**: It receives several ordered lists of index hits plus the vector-search list. For each chunk hit, it calculates reciprocal-rank fusion, which rewards items that appear high in one or more ranked lists, keeps the best chunk per owner, tracks the best vector similarity score, and returns a map from owner ID to score data and snippet text.

**Call relations**: This helper is called by fuse_hits and fuse_recall. It does not read the database; it only reshapes index results so higher-level functions can decide how to rank and display them.

*Call graph*: called by 2 (fuse_hits, fuse_recall); 1 external calls (from_iterable).


##### `fuse_hits`  (lines 261–266)

```
def fuse_hits(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks source-page search results by combining word-search and meaning-search hits. It keeps the highest fused results up to the requested limit.

**Data flow**: It receives lexical hits, vector hits, and a limit. It asks _fuse to combine the two hit lists, sorts owners by fused rank, and returns Fused records containing the owner ID, score, and matched snippet.

**Call relations**: MemoryStore.search_sources calls this after collecting both index legs. This function supplies the ranked page IDs and snippets that are later checked against the page mirror table.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (search_sources); 1 external calls (__init__).


##### `fuse_recall`  (lines 269–286)

```
def fuse_recall(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], tail: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks memory recall candidates using both fused rank and raw meaning similarity. This gives a boost to memories whose text is semantically close to the query, not just high in a ranked list.

**Data flow**: It receives lexical hits, vector hits, unindexed-tail hits, and a limit. It fuses all legs, normalizes the rank score, blends it with the best vector cosine score, sorts by that blended score, and returns Fused candidates.

**Call relations**: MemoryStore.recall calls this after gathering indexed hits and newest unindexed matches. Its output is then enriched from the database, aged, diversified, and possibly rewritten as topic pointers.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (recall); 1 external calls (__init__).


##### `half_life_days`  (lines 303–309)

```
def half_life_days(item_class: str, memory_kind: str) -> float | None
```

**Purpose**: Looks up how quickly a fact should fade with age. Non-fact memory classes do not decay here, so they return no half-life.

**Data flow**: It receives an item class and memory kind. If the item is not a fact, it returns None; otherwise it returns the configured half-life for that kind, falling back to the normal fact half-life.

**Call relations**: Inventory uses this to show the half-life to operators, and decay_multiplier uses it to compute the actual recall weight. It centralizes the decay policy so both views agree.

*Call graph*: called by 2 (decay_multiplier, inventory).


##### `decay_multiplier`  (lines 312–324)

```
def decay_multiplier(item_class: str, memory_kind: str, confidence: int, created_at: datetime | None, now: datetime) -> float
```

**Purpose**: Computes how much a memory’s relevance should be reduced because it is old or low-confidence. This is the main math behind time-based fading for facts.

**Data flow**: It receives the item class, memory kind, confidence, creation time, and current time. For facts with a creation time, it combines confidence with exponential age decay; for other items or missing creation time, it returns 1.0, meaning no reduction.

**Call relations**: decay_factor calls this for recalled items, and inventory calls it for display. It uses _aware and half_life_days so time math and memory-kind rules stay in one place.

*Call graph*: calls 2 internal fn (_aware, half_life_days); called by 2 (decay_factor, inventory).


##### `decay_factor`  (lines 327–330)

```
def decay_factor(item: Recalled, now: datetime) -> float
```

**Purpose**: Applies the shared decay calculation to a Recalled memory item. It is a convenience wrapper used during recall ranking.

**Data flow**: It receives a recalled item and the current time. It pulls the item’s class, kind, confidence, and creation time, passes them to decay_multiplier, and returns the resulting weight.

**Call relations**: MemoryStore.recall uses this after database enrichment. It turns a raw relevance score into a time- and confidence-adjusted score before final ordering.

*Call graph*: calls 1 internal fn (decay_multiplier); called by 1 (recall).


##### `enforce_type_diversity`  (lines 333–351)

```
def enforce_type_diversity(rows: tuple[Recalled, ...], limit: int) -> tuple[Recalled, ...]
```

**Purpose**: Prevents one memory class from crowding out all others in the final recall results. It is like making sure a shortlist is not filled entirely by one category.

**Data flow**: It receives already-ranked recalled rows and a limit. It admits only a capped number from each item class at first, saves extra rows for later, then backfills from those extras if the result is still short.

**Call relations**: MemoryStore.recall calls this after decay-adjusted ranking. It shapes the final answer set before episodic items are turned into topic pointers.

*Call graph*: called by 1 (recall).


##### `as_topic_pointer`  (lines 354–364)

```
def as_topic_pointer(item: Recalled, index: int) -> Recalled
```

**Purpose**: Changes episodic memory hits into lightweight topic pointers. This keeps broad episode-like memories from being inserted verbatim as context.

**Data flow**: It receives a recalled item and its position in the final list. If the item is not episodic, it returns it unchanged; if it is episodic, it returns a copy whose body is a short topic label and whose recall mode is marked as topic.

**Call relations**: MemoryStore.recall applies this at the very end. It uses dataclass replacement so the original recalled item is not mutated in place.

*Call graph*: called by 1 (recall); 1 external calls (replace).


##### `MemoryStore.commit`  (lines 386–422)

```
async def commit(self, write: MemoryWrite) -> None
```

**Purpose**: Saves one memory write into the database. It deliberately does not create chunks or embeddings immediately, so writing memory stays fast.

**Data flow**: It receives a MemoryWrite. It builds a stable ID from workspace, subject, item class, and body, inserts the row, or updates selected fields if the same memory already exists; the embedding digest remains empty unless it was already present from a previous identical body.

**Call relations**: This is the write path for MemoryStore. Later, MemoryIndexer notices rows without an embedding digest and performs the heavier indexing work.

*Call graph*: 1 external calls (uuid5).


##### `MemoryStore.recall`  (lines 424–450)

```
async def recall(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None) -> tuple[Recalled, ...]
```

**Purpose**: Finds the most useful saved memories for a query. It combines indexed search, new unindexed memories, database filtering, age decay, diversity rules, and episodic pointer rewriting.

**Data flow**: It receives a query, allowed subjects, a result limit, and optional start/end time bounds. It gathers lexical and vector hits, scans the unindexed tail, fuses those candidates, reads surviving memory rows, adjusts scores by decay, diversifies by item class, converts episodic memories to topic pointers, and returns final Recalled items.

**Call relations**: This is the main recall workflow. It calls _legs for index searches, _untail_leg for just-written rows, fuse_recall for ranking, _enrich for database rows, then decay_factor, enforce_type_diversity, and as_topic_pointer for final shaping.

*Call graph*: calls 7 internal fn (_enrich, _legs, _untail_leg, as_topic_pointer, decay_factor, enforce_type_diversity, fuse_recall); 2 external calls (replace, now).


##### `MemoryStore.search_sources`  (lines 452–494)

```
async def search_sources(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None) -> tuple[SourceMatch, ...]
```

**Purpose**: Searches indexed source pages, not saved memory facts. It lets the memory extension find relevant snippets from synced documents under the same subject visibility rules.

**Data flow**: It receives a query, allowed subjects, a limit, and optional time bounds. It gets lexical and vector page hits, fuses them, reads matching page metadata from the mem_page mirror table, filters by time if requested, and returns SourceMatch records with page ID, subject, snippet text, and score.

**Call relations**: This mirrors the recall search shape but uses page owners instead of memory-item owners. It depends on PageIndexer keeping the index and mem_page mirror table current.

*Call graph*: calls 2 internal fn (_legs, fuse_hits); 3 external calls (__init__, select, UUID).


##### `MemoryStore._legs`  (lines 496–504)

```
async def _legs(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[tuple[Hit, ...], tuple[Hit, ...]]
```

**Purpose**: Runs the two normal index searches for a query: word-based search and meaning-based vector search. It hides the repeated setup used by memory and page search.

**Data flow**: It receives a query, subject filter, owner kind, and limit. It embeds the query if possible, always asks the index for lexical hits, asks for vector hits only when an embedding exists, and returns both hit lists.

**Call relations**: MemoryStore.recall and MemoryStore.search_sources both call this. It delegates query embedding to _embed_query, then hands the results back to the caller for fusion.

*Call graph*: calls 1 internal fn (_embed_query); called by 2 (recall, search_sources).


##### `MemoryStore._untail_leg`  (lines 506–549)

```
async def _untail_leg(self, query: str, subjects: frozenset[str], limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches the newest memories that have not been indexed yet. This makes a freshly committed memory recallable before the background indexer has processed it.

**Data flow**: It receives a query, subject filter, and limit. It splits the query into terms, reads a bounded number of newest unembedded and non-superseded memory rows, counts term matches in each body, turns positive matches into Hit objects, sorts them, and returns the best ones.

**Call relations**: MemoryStore.recall calls this alongside the normal index legs. The returned hits go into fuse_recall as a third, lexical-only source of candidates.

*Call graph*: called by 1 (recall); 3 external calls (__init__, split, select).


##### `MemoryStore._embed_query`  (lines 551–559)

```
async def _embed_query(self, query: str) -> tuple[float, ...]
```

**Purpose**: Turns a search query into an embedding for meaning-based search. If embedding fails, recall can still continue using word search.

**Data flow**: It receives the query string. Blank queries return no vector; otherwise it asks the embed backend for one vector and returns it, logging and returning an empty result if the backend raises an error.

**Call relations**: MemoryStore._legs calls this before vector search. Its failure-safe behavior keeps recall and source search from breaking completely when the embedding service is unavailable.

*Call graph*: called by 1 (_legs).


##### `MemoryStore._enrich`  (lines 561–611)

```
async def _enrich(self, fused: tuple[Fused, ...], start: datetime | None, end: datetime | None) -> tuple[Recalled, ...]
```

**Purpose**: Turns fused index candidates back into real memory records from the database. It also removes superseded memories and memories outside the requested time window.

**Data flow**: It receives fused candidates and optional start/end times. It converts candidate owner IDs to UUIDs, queries matching non-superseded memory rows, builds a lookup table, and returns Recalled objects in the same order as the fused candidates that still exist.

**Call relations**: MemoryStore.recall calls this after fuse_recall. It is the point where index hits are verified against durable memory rows before final scoring and display.

*Call graph*: called by 1 (recall); 3 external calls (__init__, select, UUID).


##### `store_for`  (lines 614–624)

```
def store_for(ext: ExtensionContext) -> MemoryStore
```

**Purpose**: Builds a MemoryStore from an extension context. It also checks that the required index and embedding backends are actually available.

**Data flow**: It receives an ExtensionContext. If either the index backend or embedding backend is missing, it raises an error; otherwise it returns a MemoryStore wired to the context’s transaction opener and workspace ID.

**Call relations**: This is the small factory that connects the memory workflow to the host extension runtime. Other code can call it instead of manually assembling a MemoryStore.

*Call graph*: 1 external calls (__init__).


##### `MemoryIndexer.run`  (lines 640–642)

```
async def run(self) -> None
```

**Purpose**: Runs one indexing pass for due memory items. It claims a batch, then indexes each claimed item.

**Data flow**: It takes no external input beyond the indexer’s configured database, index, embedder, and chunker. It asks _claim_due for rows needing embeddings, then passes each one to _index_item; it returns nothing but changes the index and memory rows.

**Call relations**: This is the public entry method for the memory indexing job. It coordinates _claim_due and _index_item so overlapping runs do not do the same work twice.

*Call graph*: calls 2 internal fn (_claim_due, _index_item).


##### `MemoryIndexer._claim_due`  (lines 644–676)

```
async def _claim_due(self) -> tuple[MemoryItem, ...]
```

**Purpose**: Finds memory rows that need indexing and marks them as claimed. The claim is a lease, meaning another worker can retry later if this one gets stuck.

**Data flow**: It reads the current time, calculates a lease-expiry cutoff, selects rows whose embedding digest is missing and whose claim is absent or expired, optionally locks them for PostgreSQL, updates their claimed-at time, and returns them as MemoryItem objects.

**Call relations**: MemoryIndexer.run calls this before indexing. Its claim step protects _index_item from duplicate work when multiple indexer ticks overlap.

*Call graph*: called by 1 (run); 5 external calls (now, timedelta, or_, select, update).


##### `MemoryIndexer._index_item`  (lines 678–698)

```
async def _index_item(self, item: MemoryItem) -> None
```

**Purpose**: Creates searchable chunks and embeddings for one memory item, then marks that item as indexed. This is the heavy work intentionally kept off the commit path.

**Data flow**: It receives a MemoryItem. It sends the item body through chunk_embed_upsert, which splits text and stores indexed chunks, calculates a SHA-256 digest of the body, then updates the memory row with that digest and clears the claim.

**Call relations**: MemoryIndexer.run calls this for each claimed item. After it succeeds, normal recall can find the memory through the index instead of relying on the unindexed-tail scan.

*Call graph*: called by 1 (run); 3 external calls (sha256, update, chunk_embed_upsert).


##### `PageIndexer.apply`  (lines 716–718)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Applies a batch of source-page changes to the memory extension’s page index. It is the batch-level entry for page indexing work.

**Data flow**: It receives a tuple of PageChange records. It loops over them and passes each change to _apply; it returns nothing but updates the index and page mirror table.

**Call relations**: The broader page-change runner owns batching and delivery, then calls this method. This method simply hands each delivered change to the single-change logic.

*Call graph*: calls 1 internal fn (_apply).


##### `PageIndexer._apply`  (lines 720–751)

```
async def _apply(self, change: PageChange) -> None
```

**Purpose**: Indexes, updates, or removes one source page in response to a page change. It keeps the search index and mem_page mirror table aligned.

**Data flow**: It receives one PageChange. If the change is a tombstone, it deletes that page’s indexed chunks and mirror row; otherwise it chunks and embeds the page body, then updates or inserts the page’s subject and creation time in mem_page.

**Call relations**: PageIndexer.apply calls this for every change in a batch. MemoryStore.search_sources later depends on the chunks and mirror rows this function maintains.

*Call graph*: called by 1 (apply); 5 external calls (__init__, delete, insert, update, chunk_embed_upsert).


### `core/src/ufo/memory.py`

`domain_logic` · `request handling`

This file is the meeting point between code that wants to recall past information and extensions that know how to search for it. Think of it like a standard power socket: many devices can provide power in different ways behind the wall, but the thing plugging in only needs one predictable shape.

The file defines a provider-neutral result, `MemoryMatch`, which is one piece of remembered information with a type, called `kind`, and the actual text to inject or show. It also defines `MemorySearchProvider`, a protocol. A protocol is a promise about what methods an object must have. Here, any memory provider must offer an async `search` method that accepts search phrases, an optional member ID, and an optional time window.

The main working class is `MemorySearch`. Consumers give it a provider, then ask it to search for a specific conversation. Before passing the search to the provider, it opens a workspace-scoped database transaction, looks up which member owns that conversation, and then calls the provider with that member ID. This matters because memory may be private to a member as well as shared. Without this step, a search could be too broad, miss the right private memory, or risk mixing information between people or workspaces.

#### Function details

##### `MemorySearchProvider.search`  (lines 28–34)

```
async def search(self, queries: tuple[str, ...], member_id: UUID | None, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This is the required search shape that memory extensions must provide. It says: given one or more search phrases, an optional member, and an optional date range, return matching memory snippets in a common format.

**Data flow**: The caller supplies search queries, possibly a member ID to limit whose memory should be searched, and optional start and end times. A concrete provider uses those inputs to look through its own memory store and returns a tuple of `MemoryMatch` objects. This protocol method does not do the search itself; it describes what real providers must implement.

**Call relations**: MemorySearch.search relies on any object matching this protocol. After it has found the conversation’s member in the workspace database, it hands the queries, member ID, and time limits to the provider’s `search` method so the provider can do the actual recall work.


##### `MemorySearch.search`  (lines 43–60)

```
async def search(self, conversation_id: UUID, queries: tuple[str, ...], start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This searches memory for a conversation while first figuring out which member the conversation belongs to. That extra lookup keeps the provider search correctly scoped to the person or member connected to the conversation.

**Data flow**: The caller gives a conversation ID, search queries, and optional start and end times. The function opens a workspace database transaction, reads the current workspace ID, looks up the conversation row, and extracts its member ID. It then passes the queries, member ID, and time window to the configured memory provider and returns the provider’s memory matches unchanged.

**Call relations**: This is the practical entry point for consumers that know the conversation but not the member. During its work it calls `workspace_tx` to safely talk to the database, uses `ws_current` to stay inside the active workspace, builds a SQL query with `sqlalchemy.select`, and finally delegates the real searching to `MemorySearchProvider.search`.

*Call graph*: 3 external calls (select, workspace_tx, ws_current).


### Semantic embeddings
OpenAI embeddings convert text chunks into vectors for meaning-based memory and document search.

### `extensions/embed_openai/ufo_ext_embed_openai.py`

`io_transport` · `startup registration and background embedding/indexing work`

This extension is the system’s built-in bridge to OpenAI for embeddings. An embedding is a list of numbers that represents the meaning of text, a bit like giving each note card a coordinate on a giant map of ideas. Without this file, a deployment that relies on the default embedding backend would not know how to convert text into vectors for semantic search or indexing.

The file registers itself as the backend named `default`, so the core system can find it when no other embedding backend is configured. It does not require an OpenAI key at startup. Instead, it reads `OPENAI_API_KEY` from the environment only when embedding is actually requested. That means local development can start without a key, but a real embedding attempt fails clearly if the key is missing.

Before sending text to OpenAI, the file protects the provider call from being too large. `plan_embed_batches` cuts each text item to a maximum length and groups items into batches that stay under item-count and character-count limits. `OpenAIEmbedClient.embed` then sends each batch through OpenAI’s asynchronous client and returns the resulting vectors in the same intended order. The manifest at the bottom advertises this extension to the host system.

#### Function details

##### `plan_embed_batches`  (lines 32–48)

```
def plan_embed_batches(texts: tuple[str, ...]) -> tuple[tuple[str, ...], ...]
```

**Purpose**: This function prepares text for safe embedding requests. It trims overly long text items and groups the rest into batches small enough to send to OpenAI without exceeding the file’s built-in limits.

**Data flow**: It takes a tuple of text strings. For each string, it keeps only the allowed number of characters, then adds it to the current batch unless that batch would become too large by item count or total characters. It returns a tuple of batches, where each batch is a tuple of clipped text strings ready to send.

**Call relations**: When `OpenAIEmbedClient.embed` is about to call OpenAI, it first asks `plan_embed_batches` to split the incoming text into safe chunks. This keeps the actual provider calls predictable and bounded before any network request is made.

*Call graph*: called by 1 (embed).


##### `OpenAIEmbedClient.embed`  (lines 61–73)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This method sends text to OpenAI and returns embeddings, which are numeric vectors representing the text meaning. It is the main runtime path that turns stored or indexed text into searchable vector form.

**Data flow**: It receives a tuple of text strings. It reads `OPENAI_API_KEY` from the environment, refuses to continue if the key is missing, creates an asynchronous OpenAI client, splits the text with `plan_embed_batches`, sends each batch to OpenAI, sorts returned rows by their provider index, and collects each embedding as a tuple of floats. It returns all vectors as one tuple, matching the input order across the batches.

**Call relations**: This is the method the embedding core uses when it needs vectors. Inside, it depends on `plan_embed_batches` to keep requests within safe size limits, then hands each prepared batch to `openai.AsyncOpenAI` for the actual remote API call.

*Call graph*: calls 1 internal fn (plan_embed_batches); 1 external calls (AsyncOpenAI).


##### `build`  (lines 76–81)

```
def build(ctx: ExtensionContext) -> EmbedClient
```

**Purpose**: This function creates the embedding client object that the host system will use. It deliberately does not create the OpenAI API client yet, so startup can succeed even when no API key is present.

**Data flow**: It receives an `ExtensionContext`, which represents the surrounding workspace or extension environment, but this backend does not need to read anything from it. It returns a new `OpenAIEmbedClient` configured with the default model.

**Call relations**: The extension manifest points to `build` as the factory for the default embedding backend. During boot, the core calls this factory to get an `OpenAIEmbedClient`; later, actual embedding work happens through that client’s `embed` method.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 84–89)

```
def manifest() -> Manifest
```

**Purpose**: This function tells the host system what this extension is and what backend it provides. It names the extension, gives its version, and registers the embedding backend under the name `default`.

**Data flow**: It takes no input. It builds an `EmbedBackendSpec` that says the `default` embedding backend should be created with `build`, then wraps that in a `Manifest` containing the extension name and version. The returned manifest is what the extension loader reads.

**Call relations**: The host system calls `manifest` when discovering extensions. The manifest then connects the backend name `default` to the `build` function, so startup can construct the OpenAI embedding client when this backend is selected.

*Call graph*: 2 external calls (__init__, __init__).


### Knowledge graph recall
The knowledge graph extension converts changed pages into entities and relationships, then searches connected neighborhoods for relevant facts.

### `extensions/knowledge_graph/ufo_ext_knowledge_graph/store.py`

`domain_logic` · `page-change indexing and graph query handling`

This file is the heart of the knowledge-graph extension. Its job is to keep a separate graph-shaped index beside the normal page storage. Think of the pages as notebooks, and this file as the person who reads each notebook page and draws a relationship map from it: this page mentions that person, this person works at that company, this topic links to that URL, and so on.

It owns two database tables: one for entities, such as people, companies, organizations, or topics, and one for edges, meaning typed relationships between those entities. When a page changes, GraphExtractor reads the page. First it uses predictable text rules, with no artificial intelligence call, to find markdown links, @mentions, #tags, URLs, and typed links like [[works_at::Acme]]. If a model is available, it can also ask the model to extract extra typed relationships from normal prose. Those model results are checked before being saved, so only approved relationship types enter the graph.

The file is careful about repeat work. Each edge records the page digest, a fingerprint of the page content, so unchanged pages are skipped. If a page is deleted, its edges are soft-deleted rather than followed later. GraphStore is the read side: it finds matching entities and walks a small number of relationship steps, returning a bounded subgraph suitable for search results or conversational context.

#### Function details

##### `to_edge_type`  (lines 160–165)

```
def to_edge_type(raw: str) -> EdgeType
```

**Purpose**: Checks that a relationship type is one of the graph's allowed types. This prevents misspelled or invented relationship labels from silently entering searches or storage.

**Data flow**: It receives a raw text label such as "works_at". It compares that label with the fixed set of allowed edge types. If the label is known, it returns it as a valid edge type; if not, it raises an error saying the type is unknown.

**Call relations**: GraphExtractor._record_edge calls this just before saving an edge, so bad edge types cannot reach the database. GraphExtractor._tier_b also calls it on model-produced relationships, because model output is treated as untrusted until it passes this check.

*Call graph*: called by 2 (_record_edge, _tier_b); 2 external calls (__init__, cast).


##### `normalize_name`  (lines 192–195)

```
def normalize_name(name: str) -> str
```

**Purpose**: Turns an entity name into a stable lookup key. It makes names easier to match by ignoring extra spaces and letter casing.

**Data flow**: It receives a name like "Sam  Altman". It collapses repeated whitespace, trims the ends, and lowercases the result. The output is a normalized form such as "sam altman" that can be used for matching and deduplication.

**Call relations**: parse_page.record uses this while collecting references so the same name is not recorded twice with different spacing. GraphExtractor._upsert_entity uses it to give each entity a stable identity. GraphStore._resolve and GraphStore._seed_from_text use it when finding entities during queries.

*Call graph*: called by 4 (_upsert_entity, _resolve, _seed_from_text, record); 1 external calls (sub).


##### `graph_subjects`  (lines 198–203)

```
def graph_subjects(member_id: UUID | None) -> frozenset[str]
```

**Purpose**: Decides which graph areas a reader should search. It includes the shared graph, and when there is a specific member, that member's personal graph too.

**Data flow**: It receives either a member ID or no member ID. With a member ID, it builds the member-specific subject name and returns it together with the shared subject. Without one, it returns only the shared subject.

**Call relations**: This helper prepares the subject set that GraphStore methods expect. It calls the shared source helper that formats a member-specific subject name, so graph reads follow the same ownership rules as the rest of the page and memory system.

*Call graph*: 1 external calls (member_subject).


##### `parse_page`  (lines 248–284)

```
def parse_page(body: str) -> ParsedPage
```

**Purpose**: Reads a page's markdown text and extracts the obvious entity references without using a model. This gives the graph a reliable baseline even when artificial intelligence extraction is unavailable.

**Data flow**: It receives the page body as text. It looks for a first heading to use as the page's anchor title, then scans for wikilinks, typed wikilinks, @mentions, #tags, and URLs. It removes duplicate references and returns a ParsedPage containing the title and the list of references found.

**Call relations**: GraphExtractor._apply calls this before materializing a changed page into graph records. Inside the scan, parse_page.record does the repeated work of cleaning and deduplicating each reference before it becomes part of the parsed result.

*Call graph*: called by 1 (_apply); 1 external calls (__init__).


##### `parse_page.record`  (lines 260–267)

```
def record(edge_type: str, name: str, entity_type: str) -> None
```

**Purpose**: Adds one discovered reference to the page parse, unless it is empty or already seen. It keeps the parser from producing duplicate edges for the same target.

**Data flow**: It receives an edge type, a display name, and an entity type. It trims the name, normalizes it for comparison, checks whether that combination has already been recorded, and if not appends a Ref object to the parser's growing list.

**Call relations**: This is the small inner helper used by parse_page while it scans different markdown patterns. It relies on normalize_name so links that differ only by casing or spacing collapse to the same reference.

*Call graph*: calls 1 internal fn (normalize_name); 1 external calls (__init__).


##### `render_subgraph`  (lines 287–302)

```
def render_subgraph(subgraph: Subgraph) -> tuple[str, ...]
```

**Purpose**: Turns a subgraph into readable text lines. This is useful when graph results need to be shown to a tool, prompt, or human-facing context.

**Data flow**: It receives a Subgraph containing nodes and edges. It builds a quick lookup from node IDs to node details, then formats each edge as a line showing source name, relationship type, target name, target type, whether the target is still only a stub, and the source page citation. It returns those lines as a tuple of strings.

**Call relations**: This function sits after GraphStore has already found a subgraph. It does not fetch data itself; it only formats the nodes and edges that were handed to it.


##### `GraphExtractor.apply`  (lines 320–322)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Applies a batch of page changes to the graph index. It is the batch-facing entry method for updating graph records after pages change.

**Data flow**: It receives a tuple of page changes. It processes them one by one by passing each change to GraphExtractor._apply. It does not return a value; its effect is that the graph database is updated as needed.

**Call relations**: The page-change runner calls this with delivered changes from the page feed. This method then delegates the actual decision for each individual page to GraphExtractor._apply.

*Call graph*: calls 1 internal fn (_apply).


##### `GraphExtractor._apply`  (lines 324–338)

```
async def _apply(self, change: PageChange) -> None
```

**Purpose**: Decides what to do with one changed page. It either marks old edges as deleted, skips unchanged content, or parses and writes a fresh graph extraction.

**Data flow**: It receives one PageChange. If the page is tombstoned, meaning deleted or no longer active, it marks all edges from that page as tombstoned in the database. If the page is not deleted, it checks whether this exact digest was already extracted. If not, it parses the body and sends the result onward for materialization.

**Call relations**: GraphExtractor.apply calls this for each change in a batch. It calls GraphExtractor._already_extracted to avoid repeated work, parse_page to find deterministic references, and GraphExtractor._materialize to write nodes and edges.

*Call graph*: calls 3 internal fn (_already_extracted, _materialize, parse_page); called by 1 (apply); 1 external calls (update).


##### `GraphExtractor._already_extracted`  (lines 340–354)

```
async def _already_extracted(self, change: PageChange) -> bool
```

**Purpose**: Checks whether a page's current content has already been turned into graph edges. This saves work and avoids rewriting the same graph data unnecessarily.

**Data flow**: It receives a PageChange. It queries the edge table for a non-deleted edge from the same workspace and page with the same extracted digest. It returns true if such an edge exists, otherwise false.

**Call relations**: GraphExtractor._apply calls this before parsing and writing a non-deleted page. If this says the digest is already present, the extraction flow stops for that page.

*Call graph*: called by 1 (_apply); 1 external calls (select).


##### `GraphExtractor._materialize`  (lines 356–383)

```
async def _materialize(self, change: PageChange, parsed: ParsedPage) -> None
```

**Purpose**: Writes the graph records for one parsed page. It creates or updates the page's anchor entity, creates referenced entities as needed, writes relationship edges, and removes stale edges from older page versions.

**Data flow**: It receives a PageChange and a ParsedPage. If a model is configured, it first asks GraphExtractor._tier_b for extra prose-derived relations. Then, inside a database transaction, it upserts the page anchor entity, upserts each target entity, records deterministic edges, records model-derived edges, and deletes edges from older digests of the same page.

**Call relations**: GraphExtractor._apply calls this after it knows the page is not deleted and not already extracted. This method coordinates GraphExtractor._tier_b, GraphExtractor._upsert_entity, and GraphExtractor._record_edge so the graph tables match the latest page content.

*Call graph*: calls 3 internal fn (_record_edge, _tier_b, _upsert_entity); called by 1 (_apply); 1 external calls (delete).


##### `GraphExtractor._tier_b`  (lines 385–423)

```
async def _tier_b(self, model: ModelAccess, body: str) -> tuple[ExtractedRelation, ...]
```

**Purpose**: Asks the configured model to extract extra typed relationships from normal prose. This is an optional second pass that adds detail beyond markdown links, while failing safely if the model response is missing or invalid.

**Data flow**: It receives a model access object and the page body. It builds a constrained model request that requires a tool-shaped response, sends the shortened page text to the model, validates the returned structure, checks every edge type with to_edge_type, drops empty targets, and returns the accepted ExtractedRelation objects. If the model call or validation fails, it logs a warning and returns no relations.

**Call relations**: GraphExtractor._materialize calls this before opening the write transaction, so the database transaction is not held open while waiting for the model provider. It calls the model's turn method and uses to_edge_type to guard the graph's fixed vocabulary.

*Call graph*: calls 2 internal fn (turn, to_edge_type); called by 1 (_materialize); 2 external calls (__init__, __init__).


##### `GraphExtractor._upsert_entity`  (lines 425–464)

```
async def _upsert_entity(self, connection: AsyncConnection, subject: str, name: str, entity_type: str, fill: bool) -> UUID
```

**Purpose**: Finds or creates an entity node for a page anchor or reference. It also upgrades a placeholder stub when a later page actually defines that entity.

**Data flow**: It receives a database connection, subject, display name, entity type, and a fill flag. It normalizes the name, creates a deterministic UUID from workspace, subject, type, and normalized name, then inserts the entity if new. If fill is true, it updates an existing entity to be non-stub and refreshes its display name; if fill is false, it leaves existing entities alone. It returns the entity ID.

**Call relations**: GraphExtractor._materialize calls this for the page anchor and for every referenced target. It uses normalize_name for stable identity and writes through the active SQLAlchemy async connection.

*Call graph*: calls 1 internal fn (normalize_name); called by 1 (_materialize); 2 external calls (execute, uuid5).


##### `GraphExtractor._record_edge`  (lines 466–513)

```
async def _record_edge(self, connection: AsyncConnection, change: PageChange, from_entity: UUID, to_entity: UUID, raw_edge_type: str, confidence: float=DETERMINISTIC_CONFIDENCE) -> None
```

**Purpose**: Creates or updates one relationship edge between two entity nodes. It records which page produced the edge and what digest of the page it came from.

**Data flow**: It receives a database connection, the page change, source entity ID, target entity ID, raw edge type, and confidence score. It validates the edge type, builds a deterministic UUID from the relationship details, and inserts the edge. If the edge already exists, it refreshes the digest, confidence, tombstone flag, and update time. It does not return a value.

**Call relations**: GraphExtractor._materialize calls this after both endpoint entities exist. It calls to_edge_type to reject invalid relationships and writes through the same transaction that is materializing the page.

*Call graph*: calls 1 internal fn (to_edge_type); called by 1 (_materialize); 2 external calls (execute, uuid5).


##### `GraphStore.traverse`  (lines 525–533)

```
async def traverse(self, query: str, subjects: frozenset[str], hops: int, edge_types: frozenset[str]) -> Subgraph
```

**Purpose**: Runs an explicit graph search from a named entity. It finds the entity or entities matching the query, then walks nearby relationships.

**Data flow**: It receives a query string, allowed subjects, a hop count, and optional edge type filters. It resolves the query name to seed node IDs, expands outward through matching edges, and returns a Subgraph containing the visited nodes and edges.

**Call relations**: This is the read method used by graph-search style queries. It first calls GraphStore._resolve to find starting nodes, then GraphStore._expand to collect the surrounding neighborhood.

*Call graph*: calls 2 internal fn (_expand, _resolve).


##### `GraphStore.context_for`  (lines 535–539)

```
async def context_for(self, text: str, subjects: frozenset[str], hops: int) -> Subgraph
```

**Purpose**: Finds graph context relevant to a piece of text, such as an incoming conversation turn. Instead of requiring an exact query entity, it looks for known entity names appearing in the text.

**Data flow**: It receives text, allowed subjects, and a hop count. It chooses seed entities whose normalized names appear in the text, expands outward from them, and returns the resulting Subgraph.

**Call relations**: This is used when the system wants background graph context for arbitrary text. It calls GraphStore._seed_from_text to choose starting points and then GraphStore._expand to gather nearby relationships.

*Call graph*: calls 2 internal fn (_expand, _seed_from_text).


##### `GraphStore._resolve`  (lines 541–555)

```
async def _resolve(self, query: str, subjects: frozenset[str]) -> frozenset[UUID]
```

**Purpose**: Looks up graph entities by an exact normalized name. It is how a direct graph search turns a user query into starting node IDs.

**Data flow**: It receives a query and a set of subjects. It normalizes the query; if the query or subjects are empty, it returns no seeds. Otherwise it queries the entity table for matching normalized names in the workspace and subjects, and returns their IDs.

**Call relations**: GraphStore.traverse calls this before expanding the graph. It uses normalize_name so user input matches stored names despite casing or extra spaces.

*Call graph*: calls 1 internal fn (normalize_name); called by 1 (traverse); 1 external calls (select).


##### `GraphStore._seed_from_text`  (lines 557–574)

```
async def _seed_from_text(self, text: str, subjects: frozenset[str]) -> frozenset[UUID]
```

**Purpose**: Finds likely starting entities by checking whether known entity names appear inside a larger piece of text. This lets the graph provide context for a message without an explicit graph query.

**Data flow**: It receives text and allowed subjects. It normalizes the text, loads a capped list of recently updated candidate entities from the database, and returns the IDs of candidates whose normalized names appear as whole padded phrases in the text.

**Call relations**: GraphStore.context_for calls this to create seeds for automatic context lookup. It uses normalize_name for the incoming text and a database select to fetch possible entity names.

*Call graph*: calls 1 internal fn (normalize_name); called by 1 (context_for); 1 external calls (select).


##### `GraphStore._expand`  (lines 576–592)

```
async def _expand(self, seeds: frozenset[UUID], subjects: frozenset[str], hops: int, edge_types: frozenset[str]) -> Subgraph
```

**Purpose**: Walks outward from seed nodes for a limited number of steps. This turns a few starting entities into a small, bounded neighborhood of related facts.

**Data flow**: It receives seed IDs, subjects, hop count, and edge type filters. If there are no seeds or subjects, it returns an empty Subgraph. Otherwise it repeatedly asks GraphStore._hop for the next layer of connected edges and nodes until it reaches the hop limit or result cap, then fetches node details and returns the final Subgraph.

**Call relations**: GraphStore.traverse and GraphStore.context_for both call this after choosing seed nodes. It calls GraphStore._hop for each graph step and GraphStore._nodes at the end to turn visited IDs into readable node records.

*Call graph*: calls 2 internal fn (_hop, _nodes); called by 2 (context_for, traverse); 1 external calls (__init__).


##### `GraphStore._hop`  (lines 594–639)

```
async def _hop(self, frontier: set[UUID], subjects: frozenset[str], edge_types: frozenset[str], edges: dict[UUID, TraversedEdge], visited: set[UUID]) -> set[UUID]
```

**Purpose**: Performs one step of graph traversal. It finds edges connected to the current frontier of nodes and discovers the next set of nodes to visit.

**Data flow**: It receives the current frontier, allowed subjects, edge type filters, the accumulated edge map, and the visited node set. It limits the frontier size, queries non-tombstoned edges touching those nodes, optionally filters by edge type, adds each found edge to the accumulated results, marks newly seen endpoints as visited, and returns those new endpoints as the next frontier.

**Call relations**: GraphStore._expand calls this once per hop. This method is the piece that actually reads relationship rows from the edge table and feeds the next traversal layer back to the expansion loop.

*Call graph*: called by 1 (_expand); 3 external calls (__init__, or_, select).


##### `GraphStore._nodes`  (lines 641–663)

```
async def _nodes(self, ids: set[UUID]) -> tuple[EntityNode, ...]
```

**Purpose**: Loads the display details for a set of entity IDs. Traversal mostly works with IDs, and this function turns those IDs back into names and types for the final result.

**Data flow**: It receives a set of entity IDs. If the set is empty, it returns an empty tuple. Otherwise it queries the entity table for matching rows in the workspace and builds EntityNode objects containing ID, name, entity type, and stub status.

**Call relations**: GraphStore._expand calls this after traversal is complete. It supplies the node records that make the returned Subgraph understandable and renderable.

*Call graph*: called by 1 (_expand); 2 external calls (__init__, select).
