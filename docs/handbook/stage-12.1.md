# Search indexing and default index backend  `stage-12.1`

This stage is shared behind-the-scenes support for search. It is used when the system needs to make text searchable, keep the search data up to date, or answer a user’s search later. The core indexing file sets the common rules. It takes long text and cuts it into smaller chunks, like slicing a book into searchable paragraphs. It also defines how those chunks are handed off to whatever search storage is being used, so the rest of the app does not need to know the details of each backend.

The default index extension is the built-in backend used when no custom one is configured. It saves those chunks, updates them when the original content changes, and deletes old chunks that no longer match the source. When searching, it can match ordinary words and also use vector similarity, which means comparing numeric “meaning fingerprints” of text to find related content even when the exact words differ. Together, these parts turn raw text into a maintained, searchable index.

## Files in this stage

### Indexing rules and backend
Shared chunking and indexing rules lead into the built-in backend that stores, updates, deletes, and searches indexed chunks.

### `core/src/ufo/indexing.py`

`domain_logic` · `cross-cutting indexing and retrieval preparation`

This file solves a practical search problem: pages and memory items can be too long to search well as one giant block, so they need to be cut into smaller pieces, turned into numeric meaning-vectors, and stored in a search index. Think of it like cutting a book into labeled index cards so a librarian can find the right passage later.

The file does not talk directly to a database or search engine. Instead, it defines small shared shapes, such as `Chunk` for a piece of text and `Hit` for a search result, plus two contracts: `IndexBackend`, which promises storage and search operations, and `EmbedClient`, which promises to turn text into embeddings. An embedding is a list of numbers that represents the rough meaning of text so similar ideas can be found even when the words differ.

The main workflow is `chunk_embed_upsert`. It takes one body of text, asks `TextChunker` to split it into readable chunks, asks the embedder for vectors, writes the chunks into the index, and then removes old chunks for the same owner that no longer exist. That last cleanup matters: without it, edited or deleted text could still appear in future search results as stale leftovers.

`TextChunker` tries to split text gently: paragraphs first, then lines, then sentence-like punctuation, then smaller punctuation, and finally whitespace or character limits. It also overlaps nearby chunks a little so a sentence crossing a boundary is less likely to lose its context.

#### Function details

##### `IndexBackend.upsert`  (lines 68–68)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This is the contract for saving chunks into whatever search index an extension provides. “Upsert” means insert if new, or update if already present.

**Data flow**: It receives a group of `Chunk` objects, each containing text, ownership labels, and usually an embedding. The concrete backend stores them so they can be found later. It returns no value, but the index is changed.

**Call relations**: `chunk_embed_upsert` calls this after text has been split and embedded. The actual work is done by an extension that implements this protocol, because this file only defines the promise.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.delete`  (lines 70–70)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This is the contract for removing all indexed chunks that belong to one owner, such as one page or one memory item. Someone would use it when the original item is deleted or should no longer be searchable.

**Data flow**: It receives an `IndexScope`, which names the owner kind and owner id. The backend uses that scope to delete matching chunks from its storage. It returns no value, but the index loses those records.

**Call relations**: This method is part of the backend contract for cleanup flows. It is not called inside this file, but other indexing code can call it when an entire indexed object goes away.


##### `IndexBackend.prune`  (lines 72–72)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This is the contract for deleting old chunks for one owner while keeping a known set of current chunks. It prevents edited text from leaving behind stale search results.

**Data flow**: It receives an `IndexScope` naming the owner and a set of chunk digests to keep. The backend removes any indexed chunks for that owner whose digest is not in the keep set. It returns nothing, but the stored index is cleaned up.

**Call relations**: `chunk_embed_upsert` calls this every time it re-indexes a body of text. After any new chunks are saved, pruning removes chunks that belonged to older versions of that same body.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.has_chunks`  (lines 74–74)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: This is the contract for asking whether an owner already has indexed chunks. It can be used to decide whether indexing work is needed.

**Data flow**: It receives an `IndexScope` naming one owner. The backend checks its storage for matching chunks and returns true or false. It does not change the index.

**Call relations**: This method belongs to the shared indexing contract. It is not used by this file’s workflow directly, but other code can call it before deciding to build or refresh an index.


##### `IndexBackend.lexical`  (lines 76–78)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the contract for keyword-style search, where matching is based on the actual words in the query. It is useful when exact terms, names, or phrases matter.

**Data flow**: It receives a query string, a set of allowed subjects, an owner kind, and a maximum number of results. The backend searches stored chunks that match those filters and returns `Hit` objects with text and scores. It does not change stored data.

**Call relations**: Search orchestration code can call this on a backend implementation when it wants word-based matches. This file defines the shape of the call and result, while the extension decides how to search.


##### `IndexBackend.vector`  (lines 80–82)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the contract for meaning-based search using an embedding. It helps find text with similar meaning even if it does not share the same exact words.

**Data flow**: It receives a numeric embedding, a set of allowed subjects, an owner kind, and a result limit. The backend compares that embedding to stored chunk embeddings and returns the closest `Hit` results. It does not change the index.

**Call relations**: Search code can call this after turning a user query into an embedding. This file only defines the agreement; the backend extension performs the actual nearest-match search.


##### `EmbedClient.embed`  (lines 86–86)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This is the contract for turning text strings into embeddings, which are number lists that capture rough meaning. Indexing needs this so chunks can later be found by semantic, or meaning-based, search.

**Data flow**: It receives a tuple of text strings. The embedder returns a tuple of numeric vectors in the same order, one vector per text. It does not store anything by itself.

**Call relations**: `chunk_embed_upsert` calls this after chunking text and before saving chunks. A concrete embedding provider implements the method outside this file.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `chunk_embed_upsert`  (lines 89–114)

```
async def chunk_embed_upsert(index: IndexBackend, embed: EmbedClient, chunker: 'TextChunker', owner_kind: str, owner_id: str, subject: str, body: str) -> None
```

**Purpose**: This is the shared indexing recipe for one piece of source text. It splits the text, embeds each chunk, saves the current chunks, and deletes old chunks that no longer match the source.

**Data flow**: It receives an index backend, an embedding client, a chunker, owner labels, a subject, and the text body. First it makes chunks from the body. If there are chunks, it asks the embedder for vectors, copies each chunk with its vector attached, and upserts them into the index. Finally it prunes the owner’s index records to keep only the digests from this run, even if the body produced no chunks.

**Call relations**: This function ties the seam together. It calls `EmbedClient.embed` to get vectors, `IndexBackend.upsert` to store current chunks, and `IndexBackend.prune` to clean stale ones. It relies on `TextChunker.chunk` before those calls, although that call is through the passed chunker object.

*Call graph*: calls 3 internal fn (embed, prune, upsert); 2 external calls (__init__, replace).


##### `TextChunker.chunk`  (lines 123–134)

```
def chunk(self, text: str, owner_kind: str, owner_id: str, subject: str) -> tuple[Chunk, ...]
```

**Purpose**: This is the public way to turn one text body into labeled `Chunk` objects ready for embedding. It gives each piece its owner information, order number, and stable digest.

**Data flow**: It receives raw text plus owner kind, owner id, and subject. It asks `_slices` to produce text pieces, then wraps each piece in a `Chunk` and computes a digest with `_digest`. It returns a tuple of chunks with no embeddings yet.

**Call relations**: `chunk_embed_upsert` uses this as the first step in indexing. Inside the chunker, it delegates the actual splitting to `_slices` and the stable identifier creation to `_digest`.

*Call graph*: calls 2 internal fn (_digest, _slices); 1 external calls (__init__).


##### `TextChunker._slices`  (lines 136–144)

```
def _slices(self, text: str) -> list[str]
```

**Purpose**: This decides how to cut raw text into final chunk strings. It keeps small text as-is, and for larger text it splits, merges, overlaps, and size-caps the pieces.

**Data flow**: It receives one text string. Empty or whitespace-only text becomes an empty list. Short text is trimmed and capped by character length. Longer text is recursively split, merged into useful sizes, given overlap for context, and finally capped by maximum characters. It returns a list of final text slices.

**Call relations**: `TextChunker.chunk` calls this before creating `Chunk` objects. `_slices` coordinates the helper methods: `_count_words`, `_recursive_split`, `_greedy_merge`, `_apply_overlap`, and `_cap_by_chars`.

*Call graph*: calls 5 internal fn (_apply_overlap, _cap_by_chars, _count_words, _greedy_merge, _recursive_split); called by 1 (chunk).


##### `TextChunker._count_words`  (lines 147–153)

```
def _count_words(text: str) -> int
```

**Purpose**: This estimates how large a text is for chunking purposes. It treats mostly Chinese, Japanese, or Korean text differently because those languages often do not use spaces between words.

**Data flow**: It receives a text string. It removes whitespace to see how much real text exists, then checks how much of it is CJK text. For dense CJK text it counts non-whitespace characters; otherwise it counts runs of non-space text. It returns a number used as the chunk size estimate.

**Call relations**: `_slices`, `_recursive_split`, and `_greedy_merge` call this whenever they need to decide whether text is small enough, too large, or safe to combine.

*Call graph*: called by 3 (_greedy_merge, _recursive_split, _slices); 1 external calls (sub).


##### `TextChunker._cap_by_chars`  (lines 155–167)

```
def _cap_by_chars(self, text: str) -> list[str]
```

**Purpose**: This enforces a hard maximum character length for chunks. It is a safety net for unusually long text that still exceeds the desired size after word-based splitting.

**Data flow**: It receives one text string. If it is already short enough, it returns it as a one-item list. If it is too long, it cuts it into character windows with a small overlap so context is not lost completely. It returns the non-empty pieces.

**Call relations**: `_slices` calls this for short text and again at the end of the full splitting process. It is the final guardrail before chunks are turned into `Chunk` objects.

*Call graph*: called by 1 (_slices).


##### `TextChunker._recursive_split`  (lines 169–181)

```
def _recursive_split(self, text: str, level: int) -> list[str]
```

**Purpose**: This breaks large text using increasingly smaller natural boundaries. It tries paragraphs first, then lines, then sentence punctuation, then smaller punctuation, before falling back to whitespace.

**Data flow**: It receives text and a delimiter level. At each level, it tries to split using the delimiters for that level. Pieces that are still too large are split again at the next level. If no delimiter level remains, it uses `_split_on_whitespace`. It returns a list of smaller pieces.

**Call relations**: `_slices` calls this for text that is too large. It calls `_split_at_delimiters`, checks size with `_count_words`, and may eventually call `_split_on_whitespace` as the fallback.

*Call graph*: calls 3 internal fn (_count_words, _split_at_delimiters, _split_on_whitespace); called by 1 (_slices).


##### `TextChunker._split_at_delimiters`  (lines 184–197)

```
def _split_at_delimiters(text: str, delimiters: tuple[str, ...]) -> list[str]
```

**Purpose**: This splits text at the earliest matching delimiter from a given set, while keeping the delimiter with the piece before it. That helps chunks preserve punctuation and line breaks.

**Data flow**: It receives text and a tuple of delimiters such as paragraph breaks or punctuation marks. It repeatedly finds the earliest next delimiter, cuts there, and continues with the remaining text. It returns only pieces that contain non-whitespace content.

**Call relations**: `_recursive_split` calls this while trying each delimiter level. It does the low-level cutting, and `_recursive_split` decides whether the resulting pieces are small enough.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._split_on_whitespace`  (lines 199–215)

```
def _split_on_whitespace(self, text: str) -> list[str]
```

**Purpose**: This is the fallback splitter when natural punctuation or line boundaries are not enough. It cuts text by word runs, or by raw characters when there are no usable words.

**Data flow**: It receives one text string. If normal word runs are available, it groups them into batches around the target word count. If there is one huge unbroken run, it cuts by character count instead. It returns non-empty pieces.

**Call relations**: `_recursive_split` calls this only after delimiter-based splitting has run out of options. It makes sure even awkward text, like a very long unbroken string, can still be chunked.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._greedy_merge`  (lines 217–231)

```
def _greedy_merge(self, pieces: list[str]) -> list[str]
```

**Purpose**: This joins small neighboring pieces back together so the index does not fill with tiny fragments. It aims for chunks that are large enough to be useful but not too large.

**Data flow**: It receives a list of pieces. Starting from the first piece, it keeps adding the next piece if the combined size stays within a generous limit. When adding would make the chunk too large, it saves the current chunk and starts a new one. It returns the merged list.

**Call relations**: `_slices` calls this after recursive splitting. It uses `_count_words` and `math.ceil` to decide whether a possible merge is still within the allowed size.

*Call graph*: calls 1 internal fn (_count_words); called by 1 (_slices); 1 external calls (ceil).


##### `TextChunker._apply_overlap`  (lines 233–239)

```
def _apply_overlap(self, chunks: list[str]) -> list[str]
```

**Purpose**: This adds a little context from the end of each chunk to the start of the next one. That helps search work when the important meaning sits right across a chunk boundary.

**Data flow**: It receives a list of chunk strings. If there is only one chunk or overlap is disabled, it returns the chunks unchanged. Otherwise, it prefixes each chunk after the first with trailing context from the previous chunk. It returns the overlapped chunk list.

**Call relations**: `_slices` calls this after merging. It uses `_trailing_context` to choose the text to carry forward and `itertools.pairwise` to walk through neighboring chunks.

*Call graph*: calls 1 internal fn (_trailing_context); called by 1 (_slices); 1 external calls (pairwise).


##### `TextChunker._trailing_context`  (lines 241–251)

```
def _trailing_context(self, text: str) -> str
```

**Purpose**: This chooses the overlap text to copy from the end of one chunk into the next. It prefers recent words, and if possible starts after a sentence boundary so the copied context reads more naturally.

**Data flow**: It receives one chunk of text. If the chunk is not longer than the configured overlap amount, it returns an empty string. Otherwise it takes the last overlap-sized group of word runs, optionally trims it to start after a sentence-ending mark, and returns that context string.

**Call relations**: `_apply_overlap` calls this for each previous chunk when building the next overlapped chunk. It supplies the context that keeps neighboring chunks connected.

*Call graph*: called by 1 (_apply_overlap).


##### `TextChunker._digest`  (lines 254–256)

```
def _digest(owner_kind: str, owner_id: str, subject: str, ordinal: int, text: str) -> str
```

**Purpose**: This creates a stable unique identifier for a chunk. The identifier changes if the owner, subject, order, or text changes, which lets the index recognize stale chunks after edits.

**Data flow**: It receives owner kind, owner id, subject, ordinal, and chunk text. It joins them with a separator, hashes that payload using SHA-256, and returns the hash with a `sha256:` prefix. It does not change any outside state.

**Call relations**: `TextChunker.chunk` calls this for every produced slice. Later, `chunk_embed_upsert` uses these digests to tell the backend which chunks to keep during pruning.

*Call graph*: called by 1 (chunk); 1 external calls (sha256).


### `extensions/index_default/ufo_ext_index_default.py`

`domain_logic` · `indexing and search operations`

This file is the project’s default memory search engine. The rest of the system can talk to it using neutral objects like Chunk, Hit, and IndexScope, without caring whether the database underneath is PostgreSQL or SQLite. That matters because deployments may use PostgreSQL for production-scale search, while local development may use SQLite, and both should look the same to the rest of the app.

The file stores pieces of text called chunks. Each chunk belongs to an owner, has a subject, text, an order number, and optionally an embedding, which is a list of numbers that represents the meaning of the text for similarity search. For PostgreSQL, it writes embeddings in pgvector’s text format and uses PostgreSQL’s native full-text search. For SQLite, it packs embeddings into raw bytes and uses FTS5, SQLite’s built-in full-text search table. Think of this file as a translator: the app asks for “save these chunks” or “find matching chunks,” and this file speaks the exact dialect the chosen database understands.

It also cleans up after re-indexing. If an owner is re-chunked and some old chunks are no longer present, prune removes the leftovers so search results do not point to stale text. Finally, manifest registers this backend under the name "default", so the extension system can discover and create it.

#### Function details

##### `pgvector_literal`  (lines 31–32)

```
def pgvector_literal(vector: tuple[float, ...]) -> str
```

**Purpose**: Turns a Python tuple of numbers into the bracketed text form PostgreSQL’s pgvector extension expects. This is needed before sending an embedding to PostgreSQL for storage or vector search.

**Data flow**: It receives a tuple such as numbers representing an embedding. It converts each value to a float, joins them with commas, wraps them in square brackets, and returns one string that PostgreSQL can cast into its vector type.

**Call relations**: When DefaultIndex.upsert stores chunks in PostgreSQL, it uses this function to format each chunk’s embedding. When DefaultIndex.vector searches PostgreSQL by meaning, it uses the same formatter for the query embedding.

*Call graph*: called by 2 (upsert, vector).


##### `cosine`  (lines 35–43)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Measures how similar two embeddings are using cosine similarity, which compares the direction of two number lists rather than their size. SQLite uses this because it does not have the same vector-search machinery as PostgreSQL here.

**Data flow**: It receives two equal-length tuples of numbers. It computes the size of each vector, returns 0 if either one has no usable length, otherwise calculates a similarity score where higher means more alike.

**Call relations**: DefaultIndex.vector calls this during SQLite searches after reading stored embeddings from the database. It is the in-Python fallback that scores every candidate row before the best matches are returned.

*Call graph*: called by 1 (vector); 1 external calls (sqrt).


##### `pack_embedding`  (lines 46–47)

```
def pack_embedding(vector: tuple[float, ...]) -> bytes
```

**Purpose**: Converts an embedding from Python numbers into compact bytes for SQLite storage. SQLite stores the vector as a blob because it does not have the PostgreSQL vector type used elsewhere.

**Data flow**: It receives a tuple of floats. It writes them into a binary byte string using 32-bit floating point numbers and returns those bytes for database insertion.

**Call relations**: DefaultIndex.upsert calls this only on the SQLite path, right before saving a chunk with an embedding.

*Call graph*: called by 1 (upsert); 1 external calls (pack).


##### `unpack_embedding`  (lines 50–51)

```
def unpack_embedding(blob: bytes) -> tuple[float, ...]
```

**Purpose**: Converts a stored SQLite embedding blob back into Python numbers. This makes it possible to compare saved embeddings with a new query embedding.

**Data flow**: It receives bytes read from SQLite. It treats every four bytes as one floating point number and returns a tuple of floats.

**Call relations**: DefaultIndex.vector calls this on the SQLite path before passing the restored numbers into cosine for scoring.

*Call graph*: called by 1 (vector); 1 external calls (unpack).


##### `_hit`  (lines 54–63)

```
def _hit(row: sa.RowMapping, score: float) -> Hit
```

**Purpose**: Builds a Hit object, which is the standard search-result shape used outside this file. It hides the database row format from the rest of the index code.

**Data flow**: It receives a database row and a score. It copies the chunk identity, owner information, subject, order, text, and score into a new Hit object and returns it.

**Call relations**: DefaultIndex.lexical and DefaultIndex.vector both use this after the database or local scoring has found matches. It is the final conversion step from raw search rows into normal application results.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `DefaultIndex.upsert`  (lines 171–208)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds or updates chunks in the index. Someone uses this when text has been chunked and should become searchable, or when an existing chunk’s text or embedding has changed.

**Data flow**: It receives a tuple of Chunk objects. If the tuple is empty, it does nothing. Otherwise it opens one database transaction, checks whether the connection is PostgreSQL or SQLite, and writes each chunk using the right SQL for that database. PostgreSQL embeddings are converted with pgvector_literal; SQLite embeddings are packed with pack_embedding, and the SQLite full-text table is refreshed for each chunk.

**Call relations**: This is called by higher-level indexing code when content needs to be saved into the default index. Inside, it hands embedding formatting to pgvector_literal or pack_embedding depending on the database, then writes the rows through the provided transaction connection.

*Call graph*: calls 2 internal fn (pack_embedding, pgvector_literal).


##### `DefaultIndex.delete`  (lines 210–217)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Removes all chunks that belong to one owner scope. This is used when an indexed object is deleted or its whole indexed footprint needs to be cleared.

**Data flow**: It receives an IndexScope containing an owner kind and owner id. It opens a transaction, then deletes matching rows. In PostgreSQL it deletes from the main chunk table; in SQLite it first removes matching full-text rows and then removes the chunk rows.

**Call relations**: Higher-level cleanup can call this directly. DefaultIndex.prune also calls it when the keep-set is empty, because keeping nothing is the same as deleting the whole scope.

*Call graph*: called by 1 (prune).


##### `DefaultIndex.has_chunks`  (lines 219–222)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether a given owner already has any chunks in the index. This lets callers decide whether indexing work already exists for that scope.

**Data flow**: It receives an IndexScope. It opens a transaction, asks the database for one matching chunk, and returns true if a row exists or false if none exists.

**Call relations**: This is a small query method used by outside index orchestration code when it needs a yes-or-no answer before deciding what to do next.


##### `DefaultIndex.prune`  (lines 224–238)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Deletes old chunks for an owner while keeping a specified set of current chunk digests. This prevents stale search results after content is split into chunks again.

**Data flow**: It receives an IndexScope and a frozen set of chunk digest strings to keep. If the keep set is empty, it delegates to delete and removes the whole scope. Otherwise it opens a transaction and removes every matching chunk whose digest is not in the keep list, including the SQLite full-text rows when SQLite is used.

**Call relations**: This is used after re-indexing an owner, when the caller knows which chunk digests are still valid. If there is nothing to preserve, it calls DefaultIndex.delete; otherwise it performs database-specific pruning itself.

*Call graph*: calls 1 internal fn (delete).


##### `DefaultIndex.lexical`  (lines 240–276)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches chunks by words in the text. This is the classic “find documents containing these terms” search path.

**Data flow**: It receives a query string, allowed subjects, an owner kind, and a maximum result count. If there are no subjects, or the query is blank after database-specific cleanup, it returns no results. Otherwise it opens a transaction, runs PostgreSQL full-text search or SQLite FTS5 search, then turns each returned row into a Hit with _hit.

**Call relations**: Search orchestration calls this when it wants text-based matches. The method relies on the database to rank matching rows, then uses _hit to hand results back in the common Hit format.

*Call graph*: calls 1 internal fn (_hit).


##### `DefaultIndex.vector`  (lines 278–311)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches chunks by meaning using embeddings instead of literal words. This helps find text that is semantically similar even when it does not share the same exact wording.

**Data flow**: It receives a query embedding, allowed subjects, an owner kind, and a result limit. If the embedding or subjects are empty, it returns no results. With PostgreSQL, it formats the query embedding with pgvector_literal and lets the database score nearby vectors. With SQLite, it loads candidate embeddings, unpacks each one, scores it with cosine similarity in Python, sorts best to worst, and returns the top hits through _hit.

**Call relations**: Search orchestration calls this for vector-based retrieval. It uses PostgreSQL’s vector features when available, while on SQLite it combines unpack_embedding, cosine, sorting, and _hit to produce the same kind of result.

*Call graph*: calls 4 internal fn (_hit, cosine, pgvector_literal, unpack_embedding).


##### `manifest`  (lines 314–324)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system and registers the index backend name "default". Without this, the extension system would not know how to create DefaultIndex.

**Data flow**: It takes no input. It creates a Manifest containing the extension name, version, and an IndexBackendSpec whose factory builds a DefaultIndex using the transaction opener supplied by the host context. It returns that Manifest.

**Call relations**: The extension loader calls this during discovery. The returned Manifest tells the core system that when it asks for the "default" index backend, it should construct DefaultIndex with the workspace-scoped transaction function.

*Call graph*: 2 external calls (__init__, __init__).
