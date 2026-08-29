# Search chunking and index backends  `stage-14.2`

This stage is the behind-the-scenes search memory for the system. Its job is to take large source files, break them into smaller pieces, store those pieces, and later find the most relevant ones when the system needs context. The shared indexing file sets the rules for this process. It decides how text is split into searchable chunks, how those chunks are sent to storage, and what “contracts” storage and embedding providers must follow. An embedding is a list of numbers that represents the meaning of text, so similar ideas can be compared even when the words differ.

The built-in index is the default storage engine. It saves chunks in the project database and can search either by normal word matching or by comparing embeddings. It works with PostgreSQL or SQLite, depending on what the system is using.

The Turbopuffer extension is an alternate backend. Instead of local database search, it sends chunks and embeddings to the Turbopuffer service, then asks that service to find matches by meaning, words, or both.

## Files in this stage

### Chunking contracts
Shared chunking and indexing interfaces define how source text becomes searchable chunks and how index and embedding providers plug into the core system.

### `core/src/ufo/indexing.py`

`domain_logic` · `indexing and retrieval preparation`

Search works best when large documents are broken into smaller, meaningful pieces. This file is the workshop where that happens. It does not store anything itself. Instead, it defines the shape of an index backend, the shape of an embedding client, and the shared process for preparing text before it is stored.

The main idea is: take one body of text, split it into chunks, turn each chunk into an embedding, then upsert it into an index. An embedding is a list of numbers that represents the meaning of text, so similar text ends up with similar number patterns. An upsert means “insert this if it is new, or replace it if it already exists.”

The `TextChunker` tries to split text in a human-friendly way. It prefers paragraph breaks, then line breaks, then sentence or punctuation boundaries, and only falls back to whitespace or raw character slicing when it must. It also adds a little overlap between neighboring chunks, like repeating the last few lines of a previous page at the top of the next one, so context is not lost.

The file also prevents stale search results. After new chunks are written for an owner, old chunks for that same owner are pruned away if they no longer match the current text.

#### Function details

##### `IndexBackend.upsert`  (lines 68–68)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This is the required method an index backend must provide to save chunks. It is used when prepared text chunks, usually with embeddings attached, need to become searchable.

**Data flow**: It receives a group of `Chunk` objects. A concrete backend is expected to write them into its storage, replacing existing entries with the same chunk identity when needed. It returns nothing, but the index should afterward contain those chunks.

**Call relations**: The shared indexing flow calls this after text has been split and embedded. This file only defines the promise; an extension supplies the real storage behavior.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.delete`  (lines 70–70)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This is the required method an index backend must provide to remove all indexed chunks for one owner. It is useful when the source item should no longer appear in search at all.

**Data flow**: It receives an `IndexScope`, which names the owner kind and owner id. A concrete backend is expected to delete matching indexed chunks. It returns nothing, but those chunks should no longer be searchable.

**Call relations**: A skill creation extension calls this when refreshing or replacing an indexed card. The method belongs to the shared contract so core code and extensions can agree on how deletion is requested.

*Call graph*: called by 1 (_index_card).


##### `IndexBackend.prune`  (lines 72–72)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This is the required method an index backend must provide to remove outdated chunks for one owner while keeping the current ones. It prevents edited text from leaving old search results behind.

**Data flow**: It receives an owner scope and a set of chunk digests to keep. A concrete backend is expected to remove that owner’s chunks whose digests are not in the keep set. It returns nothing, but the index should afterward match the owner’s current text.

**Call relations**: The shared `chunk_embed_upsert` flow calls this after writing the latest chunks. This makes indexing safe to rerun after edits, because old chunks are cleaned up.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.has_chunks`  (lines 74–74)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: This is the required method an index backend must provide to answer whether an owner already has indexed chunks. It lets callers avoid unnecessary work or decide whether indexing is needed.

**Data flow**: It receives an `IndexScope` naming one owner. A concrete backend checks its storage and returns `true` if chunks exist for that owner, otherwise `false`.

**Call relations**: No caller is shown in the provided graph, but it is part of the backend contract. It gives higher-level code a standard way to ask whether an item has already been indexed.


##### `IndexBackend.lexical`  (lines 76–78)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the required method an index backend must provide for word-based search. Lexical search means matching the actual words in the query, rather than comparing meanings as number vectors.

**Data flow**: It receives a query string, a set of allowed subjects, an owner kind, and a maximum number of results. A concrete backend searches stored chunk text under those filters and returns matching `Hit` objects with scores.

**Call relations**: No caller is shown in the provided graph, but this is part of the retrieval contract. Search code can ask any backend for text-match results without knowing how that backend stores or ranks them.


##### `IndexBackend.vector`  (lines 80–82)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the required method an index backend must provide for meaning-based search using embeddings. It finds chunks whose stored number vectors are close to the query vector.

**Data flow**: It receives an embedding, allowed subjects, an owner kind, and a result limit. A concrete backend compares the query embedding with stored chunk embeddings and returns the closest `Hit` objects.

**Call relations**: The queue’s shadow skill selection flow calls this after making an embedding for its query. This method is the bridge from “what does this mean?” to “which stored chunks are most similar?”

*Call graph*: called by 1 (_shadow_skill_selection).


##### `EmbedClient.embed`  (lines 86–86)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This is the required method an embedding client must provide to turn text into numeric meaning vectors. Those vectors are what make meaning-based search possible.

**Data flow**: It receives a group of text strings. A concrete embedding provider converts each string into a tuple of floating-point numbers and returns the vectors in the same order.

**Call relations**: The shared indexing flow calls this for chunks before storing them. The queue’s shadow skill selection also calls it to turn a search query into something that vector search can compare.

*Call graph*: called by 2 (chunk_embed_upsert, _shadow_skill_selection).


##### `chunk_embed_upsert`  (lines 89–114)

```
async def chunk_embed_upsert(index: IndexBackend, embed: EmbedClient, chunker: 'TextChunker', owner_kind: str, owner_id: str, subject: str, body: str) -> None
```

**Purpose**: This function is the shared “prepare and save this text for search” workflow. It chunks one body of text, embeds the chunks, writes them to the index, and removes old chunks that no longer belong.

**Data flow**: It receives an index backend, an embedding client, a chunker, owner information, a subject, and the body text. It first asks the chunker to split the body into chunks. If there are chunks, it embeds their text and writes copies of the chunks with embeddings attached. Finally, it prunes the owner’s index entries so only the newly produced chunk digests remain.

**Call relations**: This is the coordinating step used by indexers that need the same behavior. It calls the embedding client for vectors, calls the backend to upsert new chunks, then calls the backend again to prune stale ones.

*Call graph*: calls 3 internal fn (embed, prune, upsert); 2 external calls (__init__, replace).


##### `TextChunker.chunk`  (lines 123–134)

```
def chunk(self, text: str, owner_kind: str, owner_id: str, subject: str) -> tuple[Chunk, ...]
```

**Purpose**: This is the public entry point for turning one text body into `Chunk` objects. It adds identity information to each piece so the index can tell where it came from and whether it has changed.

**Data flow**: It receives raw text plus owner kind, owner id, and subject. It asks `_slices` for the actual text pieces, numbers them in order, computes a digest for each one, and returns immutable `Chunk` objects without embeddings.

**Call relations**: `chunk_embed_upsert` uses this before embedding and storing text. Inside the chunker, it relies on `_slices` to decide the boundaries and `_digest` to create stable chunk identities.

*Call graph*: calls 2 internal fn (_digest, _slices); 1 external calls (__init__).


##### `TextChunker._slices`  (lines 136–144)

```
def _slices(self, text: str) -> list[str]
```

**Purpose**: This function decides the actual text slices that will become chunks. It is the main chunking recipe: ignore empty text, keep short text together, split long text carefully, merge small pieces, add overlap, and enforce a character limit.

**Data flow**: It receives raw text. Empty text becomes an empty list. Short text is stripped and character-capped. Long text is recursively split, greedily merged into useful sizes, given overlap from neighboring chunks, and finally capped by maximum character length. It returns plain strings ready to become chunks.

**Call relations**: `TextChunker.chunk` calls this as its first real step. It delegates counting, recursive splitting, merging, overlap, and character capping to smaller helper functions.

*Call graph*: calls 5 internal fn (_apply_overlap, _cap_by_chars, _count_words, _greedy_merge, _recursive_split); called by 1 (chunk).


##### `TextChunker._count_words`  (lines 147–153)

```
def _count_words(text: str) -> int
```

**Purpose**: This function estimates how large a piece of text is. It works for ordinary whitespace-separated languages and also makes a practical adjustment for Chinese, Japanese, and Korean text, where words are often not separated by spaces.

**Data flow**: It receives a text string. It removes whitespace to see how much real content exists. If a large share of the content is CJK characters, it counts non-whitespace characters as the size; otherwise it counts runs of non-space text. It returns an integer size estimate.

**Call relations**: The chunking process calls this when deciding whether text is short enough, whether recursive pieces need further splitting, and whether merged chunks have grown too large.

*Call graph*: called by 3 (_greedy_merge, _recursive_split, _slices); 1 external calls (sub).


##### `TextChunker._cap_by_chars`  (lines 155–167)

```
def _cap_by_chars(self, text: str) -> list[str]
```

**Purpose**: This function enforces a hard maximum character length for chunks. It is a safety net for cases where word-based splitting still produces text that is too long.

**Data flow**: It receives one text string. If it fits within the maximum size, it returns that string as a one-item list. If it is too long, it cuts the text into overlapping character windows, strips empty edges, and returns the resulting list.

**Call relations**: `_slices` calls this both for short text and after the full split-and-overlap process. It protects downstream embedding and storage systems from oversized chunks.

*Call graph*: called by 1 (_slices).


##### `TextChunker._recursive_split`  (lines 169–181)

```
def _recursive_split(self, text: str, level: int) -> list[str]
```

**Purpose**: This function breaks long text into smaller pieces by trying nicer split points before rougher ones. It is like cutting a loaf first between sections, then between slices, and only using crumbs as a last resort.

**Data flow**: It receives text and a delimiter level. At each level, it tries delimiters such as paragraph breaks, line breaks, sentence endings, or punctuation. If a piece is still too large, it recurses to the next, finer level. If no delimiter level remains, it splits on whitespace. It returns a list of smaller strings.

**Call relations**: `_slices` calls this for long text. It uses `_split_at_delimiters` to cut at the current kind of boundary, `_count_words` to test piece size, and `_split_on_whitespace` as the fallback.

*Call graph*: calls 3 internal fn (_count_words, _split_at_delimiters, _split_on_whitespace); called by 1 (_slices).


##### `TextChunker._split_at_delimiters`  (lines 184–197)

```
def _split_at_delimiters(text: str, delimiters: tuple[str, ...]) -> list[str]
```

**Purpose**: This function cuts text at whichever configured delimiter appears earliest. It keeps the delimiter with the piece, so punctuation and line breaks stay attached to the text they ended.

**Data flow**: It receives a text string and a group of delimiters. It repeatedly finds the earliest next delimiter, takes text through that delimiter as one piece, and continues with the remaining text. It drops pieces that are only whitespace and returns the rest.

**Call relations**: `_recursive_split` uses this while trying each level of human-friendly split points. It does not decide whether pieces are the right size; it only performs the cut.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._split_on_whitespace`  (lines 199–215)

```
def _split_on_whitespace(self, text: str) -> list[str]
```

**Purpose**: This function is the fallback splitter when nicer punctuation or paragraph boundaries are not enough. It groups words into target-sized batches, or slices raw characters if there are no usable word runs.

**Data flow**: It receives text. If it can find word-like runs, it joins them into groups of about the target word count. If the text is empty, it returns nothing. If the text is one huge unbreakable run, it cuts by character count instead. It returns a list of strings.

**Call relations**: `_recursive_split` calls this only after delimiter-based splitting has been exhausted. It ensures the chunker can still make progress on difficult input.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._greedy_merge`  (lines 217–231)

```
def _greedy_merge(self, pieces: list[str]) -> list[str]
```

**Purpose**: This function combines neighboring pieces that are too small on their own. It tries to make chunks large enough to be useful without letting them grow too far beyond the target size.

**Data flow**: It receives a list of split text pieces. Starting from the first piece, it keeps adding the next piece while the combined size stays within a generous limit. When adding another piece would be too much, it saves the current chunk and starts a new one. It returns the merged list.

**Call relations**: `_slices` calls this after recursive splitting. It uses `_count_words` to judge whether a proposed merge is still reasonably sized.

*Call graph*: calls 1 internal fn (_count_words); called by 1 (_slices); 1 external calls (ceil).


##### `TextChunker._apply_overlap`  (lines 233–239)

```
def _apply_overlap(self, chunks: list[str]) -> list[str]
```

**Purpose**: This function adds a small amount of repeated context to chunks after the first one. The overlap helps a later search result make sense even if the best match starts right after a chunk boundary.

**Data flow**: It receives a list of chunk strings. If there is only one chunk or overlap is disabled, it returns the list unchanged. Otherwise, each chunk after the first is prefixed with trailing context from the previous chunk. It returns the context-enriched chunks.

**Call relations**: `_slices` calls this after merging pieces. It asks `_trailing_context` for the exact text to repeat from each previous chunk.

*Call graph*: calls 1 internal fn (_trailing_context); called by 1 (_slices); 1 external calls (pairwise).


##### `TextChunker._trailing_context`  (lines 241–251)

```
def _trailing_context(self, text: str) -> str
```

**Purpose**: This function chooses the repeated context to carry from one chunk into the next. It usually takes the last few words, but tries not to start awkwardly in the middle of a sentence when it can avoid it.

**Data flow**: It receives one chunk of text. If the chunk is not longer than the overlap size, it returns an empty string to avoid duplicating the whole thing. Otherwise it takes the last overlap-sized group of words, checks for a sentence boundary inside it, and may trim to the text after that boundary. It returns the chosen context string.

**Call relations**: `_apply_overlap` calls this for each previous chunk when building overlapped chunks. This helper keeps the overlap logic focused on readable context.

*Call graph*: called by 1 (_apply_overlap).


##### `TextChunker._digest`  (lines 254–256)

```
def _digest(owner_kind: str, owner_id: str, subject: str, ordinal: int, text: str) -> str
```

**Purpose**: This function creates a stable fingerprint for a chunk. The fingerprint lets the index recognize the same chunk on a later run and distinguish it from chunks with different owner, subject, position, or text.

**Data flow**: It receives owner kind, owner id, subject, ordinal number, and chunk text. It joins those values with a separator, hashes the result with SHA-256, and returns the hash as a string prefixed with `sha256:`.

**Call relations**: `TextChunker.chunk` calls this once for each text slice. Later, `chunk_embed_upsert` uses these digests to decide which chunks should be kept during pruning.

*Call graph*: called by 1 (chunk); 1 external calls (sha256).


### Index backends
Built-in and Turbopuffer-backed implementations store embedded chunks and retrieve them through lexical, vector, or hybrid search.

### `extensions/index_default/ufo_ext_index_default.py`

`domain_logic` · `indexing and search request handling`

This file is the project’s default memory search engine. The system stores pieces of text called chunks, then later needs to find the most relevant chunks for a question or subject. Without this file, a basic deployment would have no standard way to save searchable chunks or retrieve them.

It supports two kinds of search. Lexical search means “find chunks that share important words with the query.” Vector search means “find chunks whose numeric meaning-shape is close to the query’s meaning-shape.” A vector embedding is a list of numbers that represents text in a way a model can compare.

The file adapts to the database. With PostgreSQL, it uses database-native text search and pgvector, a PostgreSQL extension for vector comparison. With SQLite, it uses FTS5, SQLite’s full-text search feature, and does vector comparison in Python by scanning rows and calculating cosine similarity. That is slower, but simple and suitable for small local or development use.

The central class is `DefaultIndex`. It opens a workspace-scoped database transaction for each operation, then chooses the right SQL for PostgreSQL or SQLite. It can insert or update chunks, delete a whole owner’s chunks, check whether chunks exist, prune stale chunks after re-chunking, and run lexical or vector searches. The `manifest` function registers this backend under the name `default` so the rest of the system can discover it.

#### Function details

##### `pgvector_literal`  (lines 35–36)

```
def pgvector_literal(vector: tuple[float, ...]) -> str
```

**Purpose**: Turns a Python tuple of numbers into the bracketed text format expected by PostgreSQL’s pgvector extension. It is used when sending embeddings to PostgreSQL for storage or comparison.

**Data flow**: It receives a tuple such as `(0.1, 0.2)`, converts each value to a plain floating-point representation, joins them with commas, and returns a string like `[0.1,0.2]`. It does not change any outside state.

**Call relations**: When `DefaultIndex.upsert` stores a chunk in PostgreSQL, it asks this helper to format the chunk’s embedding. When `DefaultIndex.vector` searches PostgreSQL by meaning, it uses the same helper to format the query embedding before handing it to SQL.

*Call graph*: called by 2 (upsert, vector).


##### `cosine`  (lines 39–47)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Measures how similar two embeddings are using cosine similarity, a common way to compare the direction of two number lists. A result near 1 means the vectors point in a similar direction; 0 is returned if either vector has no length.

**Data flow**: It receives two equal-length tuples of numbers, calculates each vector’s length, and divides their dot product by those lengths. It returns one similarity score as a float and changes nothing outside itself.

**Call relations**: SQLite does not have pgvector built in here, so `DefaultIndex.vector` calls this function after reading candidate rows from the database. The function uses `math.sqrt` for the length calculation and gives `DefaultIndex.vector` the score used for sorting hits.

*Call graph*: called by 1 (vector); 1 external calls (sqrt).


##### `pack_embedding`  (lines 50–51)

```
def pack_embedding(vector: tuple[float, ...]) -> bytes
```

**Purpose**: Converts an embedding into compact binary bytes so SQLite can store it in a database column. This avoids inventing a text format for vectors in SQLite.

**Data flow**: It receives a tuple of floats, packs each number into a 32-bit binary float, and returns the resulting bytes. The original tuple is left unchanged.

**Call relations**: When `DefaultIndex.upsert` is writing to SQLite, it calls this helper before saving a chunk with an embedding. PostgreSQL does not use it because PostgreSQL gets embeddings through `pgvector_literal` instead.

*Call graph*: called by 1 (upsert); 1 external calls (pack).


##### `unpack_embedding`  (lines 54–55)

```
def unpack_embedding(blob: bytes) -> tuple[float, ...]
```

**Purpose**: Converts an embedding stored as SQLite binary data back into a Python tuple of floats. This makes it possible to compare stored vectors in Python.

**Data flow**: It receives bytes from the SQLite database, reads them four bytes at a time as 32-bit floats, and returns a tuple of numbers. It does not write anything back to the database.

**Call relations**: During SQLite vector search, `DefaultIndex.vector` reads stored embeddings as bytes, calls this function to recover the number tuple, and then passes that tuple to `cosine` for scoring.

*Call graph*: called by 1 (vector); 1 external calls (unpack).


##### `_hit`  (lines 58–67)

```
def _hit(row: sa.RowMapping, score: float) -> Hit
```

**Purpose**: Builds a standard `Hit` result object from a database row and a relevance score. This keeps PostgreSQL and SQLite search results shaped the same way for the rest of the system.

**Data flow**: It receives one row returned by SQL plus a score, copies fields such as chunk id, owner, subject, order, and text into a `Hit`, converts the score to a float, and returns that `Hit`. It does not alter the row or database.

**Call relations**: `DefaultIndex.lexical` and `DefaultIndex.vector` both call this after they have found and scored matching chunks. This helper is the small adapter that turns database output into the project’s database-neutral search result type.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `DefaultIndex.upsert`  (lines 177–214)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds new chunks to the index or replaces existing chunks with the same digest. This is used when content is indexed or re-indexed so searches see the latest text and embedding.

**Data flow**: It receives a tuple of `Chunk` objects. If the tuple is empty, it stops. Otherwise it opens a database transaction, checks whether the connection is PostgreSQL or SQLite, and writes each chunk using the matching SQL. For PostgreSQL it formats embeddings with `pgvector_literal`; for SQLite it stores embeddings with `pack_embedding` and refreshes the full-text search table. It returns nothing, but the database is updated.

**Call relations**: This is called by the wider indexing flow when chunks need to be saved. Inside the method, the work splits by database type: PostgreSQL can keep text and vector search data in the main table, while SQLite also needs its `chunk_fts` full-text table kept in step.

*Call graph*: calls 2 internal fn (pack_embedding, pgvector_literal).


##### `DefaultIndex.delete`  (lines 216–223)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Removes all indexed chunks that belong to one owner, such as one document or memory source. This is needed when an owner is deleted or when pruning decides nothing should remain.

**Data flow**: It receives an `IndexScope`, which identifies the owner kind and owner id. It opens a transaction, deletes matching rows directly in PostgreSQL, or in SQLite first removes matching full-text-search rows and then removes the main chunk rows. It returns nothing, but the stored index entries for that owner are gone.

**Call relations**: The broader system can use this to clear an owner’s index. `DefaultIndex.prune` also calls it when its keep-set is empty, because pruning everything is the same as deleting the whole scope.

*Call graph*: called by 1 (prune).


##### `DefaultIndex.has_chunks`  (lines 225–228)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether the index already contains any chunks for a given owner. This lets callers avoid unnecessary work or decide whether indexing is needed.

**Data flow**: It receives an `IndexScope`, opens a transaction, asks the database for a single matching row, and returns `true` if one exists or `false` if none does. It only reads from the database.

**Call relations**: This method is a small query point for orchestration code outside this file. It does not call other local helpers because the same existence check works for both supported databases.


##### `DefaultIndex.prune`  (lines 230–244)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Deletes stale chunks for one owner while keeping a named set of current chunk digests. This matters after content is split into chunks again: old chunks that no longer exist should not keep appearing in search results.

**Data flow**: It receives an `IndexScope` and a frozen set of chunk digests to keep. If the keep-set is empty, it delegates to `DefaultIndex.delete`. Otherwise it opens a transaction and deletes only rows in that owner’s scope whose digest is not in the keep-set. In SQLite it also removes matching rows from the full-text-search table. It returns nothing, but the database is cleaned up.

**Call relations**: This sits between re-indexing and search correctness. After newer chunks are written, callers can use this method to remove leftovers. When there is nothing to keep, it hands the job to `DefaultIndex.delete` rather than duplicating whole-scope deletion.

*Call graph*: calls 1 internal fn (delete).


##### `DefaultIndex.lexical`  (lines 246–282)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Finds chunks by word overlap with a text query. This is the search path for cases where shared words are useful evidence of relevance.

**Data flow**: It receives a query string, a set of allowed subjects, an owner kind, and a maximum number of results. If there are no subjects, or the query has no usable terms, it returns an empty tuple. Otherwise it opens a transaction, runs PostgreSQL text search or SQLite FTS5 search, and converts each scored row into a `Hit` with `_hit`. The returned tuple contains the best word-based matches.

**Call relations**: Search code calls this when it wants lexical results from the default index. The method hides the database differences: PostgreSQL builds a text-search query, while SQLite builds an OR query for FTS5. Both branches hand their rows to `_hit` so callers receive the same kind of result.

*Call graph*: calls 1 internal fn (_hit).


##### `DefaultIndex.vector`  (lines 284–317)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Finds chunks by embedding similarity, meaning it looks for stored text whose numeric representation is close to the query embedding. This supports semantic search, where wording can differ but meaning can still match.

**Data flow**: It receives a query embedding, allowed subjects, an owner kind, and a result limit. If the embedding or subject set is empty, it returns no hits. With PostgreSQL, it formats the query using `pgvector_literal` and lets the database score and order rows. With SQLite, it reads candidate rows, unpacks stored embeddings with `unpack_embedding`, scores them with `cosine`, sorts them in Python, and returns the top results as `Hit` objects via `_hit`.

**Call relations**: This is the vector-search half of the default backend. It relies on PostgreSQL for database-side vector comparison when available, but falls back to local Python scoring for SQLite. The helper functions do the format conversion and scoring so this method can focus on the retrieval flow.

*Call graph*: calls 4 internal fn (_hit, cosine, pgvector_literal, unpack_embedding).


##### `manifest`  (lines 320–330)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system and registers the default index backend. This is how the rest of the project learns that a backend named `default` exists and how to create it.

**Data flow**: It takes no input. It builds a `Manifest` containing this extension’s name and version, plus an `IndexBackendSpec` whose factory creates a `DefaultIndex` using the transaction opener supplied by the host context. It returns that manifest and does not touch the database.

**Call relations**: During extension discovery or startup, the host calls this function to collect available capabilities. The returned factory is later used to make a `DefaultIndex`, which then performs the actual indexing and search operations.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/turbopuffer/ufo_ext_turbopuffer.py`

`io_transport` · `jobs/serve indexing and search operations`

This extension is the bridge between UFO’s internal idea of searchable memory chunks and Turbopuffer’s HTTP API. Without it, choosing `memory.index_backend = "turbopuffer"` would have no effect, and UFO could not save or search memories in Turbopuffer.

A chunk is a small piece of text with metadata, such as who owns it, what subject it belongs to, its position, and sometimes an embedding, which is a list of numbers that represents the meaning of the text. This file turns those chunks into Turbopuffer documents. It also shortens normal SHA-256 chunk IDs into compact URL-safe IDs so they fit Turbopuffer’s limits, then turns them back when results return.

The search side has two paths. The lexical path uses BM25, a word-matching ranking method, for text queries. The vector path uses approximate nearest-neighbor search, meaning it asks for chunks whose embeddings are close to the query embedding. Both paths filter results to the right owner kind and subject set, so one area of memory does not leak into another.

The `TurbopufferIndex` class performs the real work: upload chunks, delete a whole scope, prune old chunks after re-chunking, check whether a scope has data, and run searches. Each request reads the Turbopuffer API key from UFO’s credential store and sends it as a Bearer token.

#### Function details

##### `turbopuffer_id`  (lines 48–54)

```
def turbopuffer_id(chunk_digest: str) -> str
```

**Purpose**: Converts UFO’s chunk digest into a document ID suitable for Turbopuffer. Standard SHA-256 digests are shortened, while unusual IDs are left alone.

**Data flow**: It receives a chunk digest string. If the string looks like `sha256:` followed by a 64-character hex digest, it decodes the hex bytes and re-encodes them as URL-safe base64 without padding. The result is a shorter ID; otherwise the original string comes back unchanged.

**Call relations**: When chunks are uploaded, `upsert_body` calls this so Turbopuffer receives acceptable document IDs. When `TurbopufferIndex.delete` and `TurbopufferIndex.prune` remove documents, they call it again so the IDs they send match the IDs used at upload time.

*Call graph*: called by 3 (delete, prune, upsert_body); 1 external calls (urlsafe_b64encode).


##### `chunk_digest_from_id`  (lines 57–66)

```
def chunk_digest_from_id(chunk_id: str) -> str
```

**Purpose**: Turns a Turbopuffer document ID back into UFO’s original chunk digest form when possible. This keeps search results and exported chunks speaking UFO’s normal ID language.

**Data flow**: It receives a document ID from Turbopuffer. If the ID has the expected shortened base64 length, it tries to decode it and rebuilds a `sha256:` hex digest. If decoding does not fit that pattern, it returns the ID unchanged.

**Call relations**: `hit_from_row` uses this when turning search rows into `Hit` results. `_scope_chunks` also uses it while listing chunks for deletion or pruning, so the rest of the code can compare against normal UFO chunk digests.

*Call graph*: called by 2 (_scope_chunks, hit_from_row); 1 external calls (urlsafe_b64decode).


##### `upsert_body`  (lines 69–85)

```
def upsert_body(chunks: tuple[Chunk, ...]) -> dict[str, Any]
```

**Purpose**: Builds the JSON body used to upload a batch of chunks into Turbopuffer. It lays the data out in the column-based shape Turbopuffer expects.

**Data flow**: It receives a tuple of chunks. It extracts each chunk’s ID, embedding, owner information, subject, order number, and text into parallel lists, then returns a JSON-ready dictionary that also tells Turbopuffer to use cosine distance for vectors and enable full-text search on the text field.

**Call relations**: `TurbopufferIndex.upsert` calls this for each batch before sending the HTTP request. Inside this helper, `turbopuffer_id` prepares each chunk ID so the uploaded IDs match the later delete and search flows.

*Call graph*: calls 1 internal fn (turbopuffer_id); called by 1 (upsert).


##### `bm25_query`  (lines 88–104)

```
def bm25_query(text: str) -> str
```

**Purpose**: Cleans and shortens a text query so Turbopuffer’s word-based BM25 search can rank it safely. It avoids sending meaningless or oversized full-text queries.

**Data flow**: It receives raw query text. It keeps only tokens that contain a run of at least two letters or digits, joins them back into a query, and checks the byte length. If the query is too long, it cuts it to Turbopuffer’s limit and tries not to leave a chopped-off final term. It returns the cleaned query, or an empty string if nothing useful remains.

**Call relations**: `TurbopufferIndex.lexical` calls this before asking Turbopuffer for word-based results. If this function returns an empty string, lexical search stops early so punctuation-only or one-letter queries do not cause the whole namespace to look equally relevant.

*Call graph*: called by 1 (lexical).


##### `query_filters`  (lines 107–111)

```
def query_filters(owner_kind: str, subjects: frozenset[str]) -> list[Any]
```

**Purpose**: Creates the filter used for normal searches so results stay inside the requested owner kind and subject set. This is the search equivalent of checking the correct shelf before picking books.

**Data flow**: It receives an owner kind and a frozen set of subjects. It returns a Turbopuffer filter expression saying the owner kind must match exactly and the subject must be one of the requested subjects.

**Call relations**: `TurbopufferIndex._query` calls this whenever lexical or vector search runs. The public search methods choose the search style, while this helper supplies the shared scoping rule.

*Call graph*: called by 1 (_query).


##### `scope_filters`  (lines 114–121)

```
def scope_filters(scope: IndexScope, after_id: str | None) -> list[Any]
```

**Purpose**: Creates the filter used when looking at all chunks for one owner scope. It can also continue after a previous document ID for page-by-page listing.

**Data flow**: It receives an `IndexScope`, which names an owner kind and owner ID, plus an optional `after_id`. It returns a Turbopuffer filter expression requiring that owner kind and owner ID, and if `after_id` is present, only IDs greater than that value.

**Call relations**: `TurbopufferIndex.has_chunks` uses this to check whether any document exists in a scope. `_scope_chunks` uses it repeatedly while walking through a scope page by page for delete and prune operations.

*Call graph*: called by 2 (_scope_chunks, has_chunks).


##### `hit_from_row`  (lines 124–133)

```
def hit_from_row(row: dict[str, Any], score: float) -> Hit
```

**Purpose**: Turns one row returned by Turbopuffer into UFO’s standard `Hit` object. A hit is the form the rest of UFO expects for a search result.

**Data flow**: It receives a Turbopuffer row and a score chosen by the caller. It converts the row’s ID back to a chunk digest, reads the owner, subject, ordinal, and text fields, attaches the score, and returns a `Hit`.

**Call relations**: `TurbopufferIndex.lexical` calls this after BM25 rows come back, and `TurbopufferIndex.vector` calls it after vector rows are scored. It relies on `chunk_digest_from_id` so returned IDs match the IDs originally written by UFO.

*Call graph*: calls 1 internal fn (chunk_digest_from_id); called by 2 (lexical, vector); 1 external calls (__init__).


##### `vector_score`  (lines 136–142)

```
def vector_score(row: dict[str, Any], position: int, total: int) -> float
```

**Purpose**: Computes a score for a vector search row where higher means a closer match. This gives the rest of the recall system a consistent direction for ranking.

**Data flow**: It receives a result row, that row’s position in the returned list, and the total number of rows. If Turbopuffer included a cosine distance, it returns `1 - distance`. If no distance is present, it falls back to a descending rank score based on position.

**Call relations**: `TurbopufferIndex.vector` calls this for every vector result before building `Hit` objects. It is the small adapter that turns Turbopuffer’s distance-style answer into UFO’s score-style answer.

*Call graph*: called by 1 (vector).


##### `TurbopufferIndex.upsert`  (lines 156–167)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Uploads new or updated chunks into Turbopuffer. This is how searchable memory gets written to the external index.

**Data flow**: It receives a tuple of chunks. It first ignores chunks without embeddings, because vector search needs those number lists. It reads authorization headers, splits the remaining chunks into batches, turns each batch into a Turbopuffer upload body, posts it to the namespace path, and raises an error if Turbopuffer rejects the request.

**Call relations**: Higher-level indexing code calls this when it wants chunks to become searchable. This method asks `_auth` for credentials, `_path` for the workspace namespace URL, and `upsert_body` for the correct JSON shape before handing the batch to Turbopuffer.

*Call graph*: calls 3 internal fn (_auth, _path, upsert_body).


##### `TurbopufferIndex.delete`  (lines 169–177)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Deletes every indexed chunk belonging to one scope. This is used when an owner’s indexed memory should be removed completely.

**Data flow**: It receives an `IndexScope`. It reads authorization headers, lists all chunks currently in that scope, converts their chunk digests to Turbopuffer document IDs, sends delete batches to the namespace path, and raises an error if any delete request fails.

**Call relations**: The broader index cleanup flow calls this when a whole scope must disappear. It uses `_scope_chunks` to discover what exists, `turbopuffer_id` to match stored IDs, `_auth` for the API key, and `_path` for the target namespace.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.prune`  (lines 179–189)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Deletes only the chunks in a scope that are no longer in a keep-set. This prevents old chunks from lingering after the same owner has been split into new chunks.

**Data flow**: It receives an `IndexScope` and a set of chunk digests to keep. It reads authorization headers, lists all chunks in the scope, filters out the ones whose digests are still wanted, converts the rest to Turbopuffer IDs, and sends delete batches.

**Call relations**: Re-indexing code calls this after refreshing an owner’s chunks. Like `delete`, it relies on `_scope_chunks`, `turbopuffer_id`, `_auth`, and `_path`, but it removes only the difference between what exists and what should remain.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.has_chunks`  (lines 191–197)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether Turbopuffer already contains at least one chunk for a scope. This is a quick existence check, not a full export.

**Data flow**: It receives an `IndexScope`. It builds a query asking for one ID in that scope, sends it to Turbopuffer, treats a missing namespace as empty, raises on other errors, and returns true if any row came back.

**Call relations**: Higher-level code can call this before deciding whether work is needed. It uses `scope_filters` to describe the scope, `_path` to reach the query endpoint, and `_auth` to authorize the request.

*Call graph*: calls 3 internal fn (_auth, _path, scope_filters).


##### `TurbopufferIndex.lexical`  (lines 199–208)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Runs word-based search over indexed chunk text. It is useful when the user’s wording matters, such as matching names, terms, or exact phrases.

**Data flow**: It receives raw query text, allowed subjects, an owner kind, and a limit. It cleans the query with `bm25_query`; if there is no useful text or no subjects, it returns no hits. Otherwise it asks `_query` to rank by BM25 on the text field, then converts rows into `Hit` objects with simple descending scores.

**Call relations**: Search orchestration calls this for the lexical leg of recall. It delegates the shared Turbopuffer request to `_query` and the row conversion to `hit_from_row`.

*Call graph*: calls 3 internal fn (_query, bm25_query, hit_from_row).


##### `TurbopufferIndex.vector`  (lines 210–219)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Runs meaning-based search using an embedding. It finds chunks whose stored embeddings are close to the query embedding, even if the words differ.

**Data flow**: It receives an embedding, allowed subjects, an owner kind, and a limit. If there is no embedding or no subjects, it returns no hits. Otherwise it asks `_query` to run approximate nearest-neighbor search on the vector field, computes a score for each row, drops non-positive scores, and returns `Hit` objects.

**Call relations**: Search orchestration calls this for the semantic, or meaning-based, leg of recall. It uses `_query` for the HTTP request, `vector_score` to translate distance into score, and `hit_from_row` to produce UFO’s standard result objects.

*Call graph*: calls 3 internal fn (_query, hit_from_row, vector_score).


##### `TurbopufferIndex._query`  (lines 221–234)

```
async def _query(self, rank_by: list[Any], owner_kind: str, subjects: frozenset[str], limit: int) -> list[dict[str, Any]]
```

**Purpose**: Sends the shared Turbopuffer query request used by both lexical and vector search. It centralizes the common pieces: ranking rule, limit, returned attributes, filters, path, and authentication.

**Data flow**: It receives a `rank_by` instruction, owner kind, subject set, and result limit. It builds the JSON query body, adds owner and subject filters, posts to the namespace query endpoint, treats a missing namespace as no results, raises on other failures, and returns the list of result rows.

**Call relations**: `TurbopufferIndex.lexical` and `TurbopufferIndex.vector` call this after deciding how results should be ranked. This method calls `_auth`, `_path`, and `query_filters`, then hands raw rows back for the caller-specific scoring and conversion.

*Call graph*: calls 3 internal fn (_auth, _path, query_filters); called by 2 (lexical, vector).


##### `TurbopufferIndex._scope_chunks`  (lines 236–264)

```
async def _scope_chunks(self, scope: IndexScope, headers: dict[str, str]) -> list[Chunk]
```

**Purpose**: Lists all chunks that belong to one owner scope. It is mainly used before deletion, because Turbopuffer deletes by document ID and this code first needs to discover those IDs.

**Data flow**: It receives an `IndexScope` and already-built authorization headers. It queries Turbopuffer in pages sorted by ID, converts each returned row into a lightweight `Chunk`, and keeps going until a page is smaller than the page size. If the namespace does not exist, it returns whatever has been collected, usually an empty list.

**Call relations**: `TurbopufferIndex.delete` and `TurbopufferIndex.prune` call this before deciding which IDs to delete. It uses `_path` to reach the query endpoint, `scope_filters` to stay inside the scope and continue after the last ID, and `chunk_digest_from_id` to restore UFO-style chunk digests.

*Call graph*: calls 3 internal fn (_path, chunk_digest_from_id, scope_filters); called by 2 (delete, prune); 1 external calls (__init__).


##### `TurbopufferIndex._auth`  (lines 266–268)

```
async def _auth(self) -> dict[str, str]
```

**Purpose**: Builds the HTTP authorization header for Turbopuffer requests. It reads the API key at request time from UFO’s credential access object.

**Data flow**: It asks the credential store for the `turbopuffer_api_key` value. It returns a headers dictionary containing `Authorization: Bearer <key>`.

**Call relations**: All public operations that talk directly to Turbopuffer call this, including upload, delete, prune, existence checks, and shared search queries. It keeps credential reading in one place so callers do not need to know how the key is stored.

*Call graph*: called by 5 (_query, delete, has_chunks, prune, upsert).


##### `TurbopufferIndex._path`  (lines 270–271)

```
def _path(self, suffix: str='') -> str
```

**Purpose**: Builds the Turbopuffer namespace path for this workspace. A namespace is like a separate folder in Turbopuffer where one workspace’s documents live.

**Data flow**: It receives an optional suffix such as `/query`. It combines the fixed namespace prefix, the current workspace ID from credentials, and the suffix, then returns the HTTP path string.

**Call relations**: Every method that sends a Turbopuffer request calls this so uploads, deletes, checks, exports, and searches all address the same workspace namespace.

*Call graph*: called by 6 (_query, _scope_chunks, delete, has_chunks, prune, upsert).


##### `manifest`  (lines 274–294)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to UFO so it can be discovered and selected as an index backend. It also declares the Turbopuffer API key credential that must be available.

**Data flow**: It creates and returns a `Manifest` containing the extension name and version, one credential slot for the API key, and one index backend specification named `turbopuffer`. The backend factory builds a `TurbopufferIndex` with the runtime credential access object and an HTTP client pointed at Turbopuffer’s API.

**Call relations**: UFO’s extension loading system calls this when it discovers the extension. The manifest it returns is how core startup learns that `memory.index_backend = "turbopuffer"` should create a `TurbopufferIndex` for later indexing and search work.

*Call graph*: 3 external calls (__init__, __init__, __init__).
