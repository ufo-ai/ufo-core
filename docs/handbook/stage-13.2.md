# Recall, indexing, alerts, memory consolidation, and graph extraction  `stage-13.2`

This stage is shared behind-the-scenes support for remembering, searching, and reacting to changed pages. When text changes, the indexing rules split it into chunks, create embeddings, which are number lists that capture meaning, and store them for later search. The OpenAI embedding extension makes those vectors. The default index keeps them locally, while the Turbopuffer extension can send them to an external search service.

On top of that search base, the memory extension turns useful page content into longer-lasting facts. Its manifest plugs in recall hooks, tools, and background jobs. Its store saves and searches memories, subjects label who each memory belongs to, and the core memory doorway gives all providers the same search shape. The condenser turns raw pages into memories and later merges older memories into summaries. Memory objects let callers read saved memories safely.

The knowledge-graph extension adds another view: it extracts entities and links from pages, then lets queries follow those connections. Finally, page alerts watch for chosen topics and notify the conversation when matching changed pages appear.

## Files in this stage

### Search indexing substrate
Shared chunking, embedding, and index-provider code makes changed text searchable locally or through hosted vector search.

### `core/src/ufo/indexing.py`

`domain_logic` · `indexing and content update`

Search systems work best when long text is broken into smaller pieces. This file is the place where that happens. It defines simple data shapes for a text chunk, a search result, and the scope of one indexed owner, such as a memory item or a page. It also defines two contracts, called protocols: one for an index backend that stores and searches chunks, and one for an embedding client that turns text into numeric vectors. A vector is a list of numbers that represents meaning, so similar text ends up with similar numbers.

The main workflow is `chunk_embed_upsert`. Given one body of text, it asks `TextChunker` to split it into useful pieces, asks the embedding service to create a vector for each piece, stores those enriched chunks in the index, and then removes any older chunks for the same owner that no longer belong. This matters after edits: without pruning, deleted or changed text could still appear in search results like an old note stuck in a filing cabinet.

`TextChunker` tries to split text in human-friendly places first: paragraphs, lines, sentences, then smaller punctuation. If needed, it falls back to whitespace or character cuts. It also adds a little overlap between neighboring chunks, like repeating the last few words on the next page, so context is not lost at the boundary.

#### Function details

##### `IndexBackend.upsert`  (lines 68–68)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This is the storage contract for adding or replacing indexed chunks. A backend implements it so the core code can save chunks without knowing which database or search engine is underneath.

**Data flow**: It receives a group of `Chunk` objects, each containing owner information, text, and usually an embedding vector. The implementation stores them, replacing existing rows or records with the same chunk identity. It returns nothing, but the index is changed so those chunks can be searched later.

**Call relations**: `chunk_embed_upsert` calls this after text has been split and embedded. The function is a handoff point: core indexing prepares the chunks, then the backend takes responsibility for durable storage and search indexing.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.delete`  (lines 70–70)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This is the storage contract for removing all indexed chunks for one owner. It is used when a memory item, page, or similar source should disappear from search entirely.

**Data flow**: It receives an `IndexScope`, which names the kind of owner and its id. The backend removes every indexed chunk matching that scope. It returns nothing, but the search index no longer contains those chunks.

**Call relations**: This file only defines the contract; callers elsewhere can use it when an indexed object is deleted. It belongs beside `upsert` and `prune` as one of the ways core code asks a backend to keep the index in sync.


##### `IndexBackend.prune`  (lines 72–72)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This is the storage contract for cleaning up stale chunks for one owner while keeping a known good set. It prevents old chunks from continuing to appear in search after text has changed.

**Data flow**: It receives an `IndexScope` for the owner and a set of chunk digests that should remain. The backend deletes indexed chunks for that owner whose digests are not in the keep set. It returns nothing, but the owner’s indexed content is made current.

**Call relations**: `chunk_embed_upsert` calls this at the end of every indexing pass. After new chunks are saved, pruning removes any chunks produced by previous versions of the text.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.lexical`  (lines 74–76)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the search contract for plain text matching. Lexical search means matching words or terms in the query, rather than comparing meaning vectors.

**Data flow**: It receives a query string, a set of allowed subjects, an owner kind, and a maximum number of results. The backend searches stored chunk text under those filters and returns matching `Hit` objects with scores. It does not change the index.

**Call relations**: This file defines the shape of the request and result so callers can ask for word-based search without knowing the backend details. A concrete backend supplies the actual search behavior.


##### `IndexBackend.vector`  (lines 78–80)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the search contract for meaning-based matching. It searches by comparing an input embedding vector against stored chunk vectors.

**Data flow**: It receives a numeric embedding, allowed subjects, an owner kind, and a result limit. The backend finds nearby stored vectors and returns `Hit` objects ordered or scored by similarity. It reads from the index but does not change it.

**Call relations**: Search orchestration elsewhere can call this after embedding a user query. The backend performs the specialized vector lookup, while this protocol keeps the core code independent from any particular vector database.


##### `EmbedClient.embed`  (lines 84–84)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This is the contract for turning text into embeddings, which are numeric fingerprints of meaning. It lets indexing code ask for vectors without caring whether they come from a local model or an outside service.

**Data flow**: It receives a tuple of text strings. The implementation converts each string into a tuple of floating-point numbers, preserving the same order as the input. It returns those vectors and does not directly change the index.

**Call relations**: `chunk_embed_upsert` calls this after chunking the body text. The returned vectors are attached to the chunks before `IndexBackend.upsert` stores them.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `chunk_embed_upsert`  (lines 87–112)

```
async def chunk_embed_upsert(index: IndexBackend, embed: EmbedClient, chunker: 'TextChunker', owner_kind: str, owner_id: str, subject: str, body: str) -> None
```

**Purpose**: This is the shared indexing recipe for one piece of source text. It splits the text, embeds each piece, saves the result, and removes stale leftovers from earlier versions.

**Data flow**: It takes an index backend, an embedding client, a text chunker, owner details, a subject, and the body text. First it turns the body into chunks. If there are chunks, it sends their text to the embedding client, copies each returned vector into the matching chunk, and upserts them into the index. Finally it prunes the owner’s index entries so only the newly produced chunk digests remain. It returns nothing, but the search index is updated to match the current body.

**Call relations**: Indexers for pages or memory items can call this instead of repeating the same steps. Inside, it calls `TextChunker.chunk`, then `EmbedClient.embed`, then `IndexBackend.upsert`, and always finishes with `IndexBackend.prune` so empty or edited bodies do not leave stale searchable content behind.

*Call graph*: calls 3 internal fn (embed, prune, upsert); 2 external calls (__init__, replace).


##### `TextChunker.chunk`  (lines 121–132)

```
def chunk(self, text: str, owner_kind: str, owner_id: str, subject: str) -> tuple[Chunk, ...]
```

**Purpose**: This is the public method that turns one full text into `Chunk` records ready for embedding. It adds identity information so each piece can be tied back to its owner and ordered correctly.

**Data flow**: It receives raw text plus owner kind, owner id, and subject. It asks `_slices` to produce the actual text pieces. For each piece, it assigns an ordinal number, creates a stable digest with `_digest`, and builds a `Chunk` without an embedding yet. It returns all chunks as a tuple.

**Call relations**: `chunk_embed_upsert` uses this as the first step in indexing. `chunk` hides the lower-level splitting details and hands back clean value objects for embedding and storage.

*Call graph*: calls 2 internal fn (_digest, _slices); 1 external calls (__init__).


##### `TextChunker._slices`  (lines 134–142)

```
def _slices(self, text: str) -> list[str]
```

**Purpose**: This is the main splitting pipeline inside `TextChunker`. It decides whether text is already small enough or needs to be broken down, merged, overlapped, and character-capped.

**Data flow**: It receives one text string. Blank text becomes an empty list. Short text is trimmed and only checked against the character limit. Longer text is recursively split at natural boundaries, merged into reasonable sizes, given overlap between neighboring chunks, and finally capped by maximum character length. It returns a list of plain text slices.

**Call relations**: `TextChunker.chunk` calls this before wrapping pieces as `Chunk` objects. `_slices` coordinates the helper methods `_count_words`, `_recursive_split`, `_greedy_merge`, `_apply_overlap`, and `_cap_by_chars`.

*Call graph*: calls 5 internal fn (_apply_overlap, _cap_by_chars, _count_words, _greedy_merge, _recursive_split); called by 1 (chunk).


##### `TextChunker._count_words`  (lines 145–151)

```
def _count_words(text: str) -> int
```

**Purpose**: This estimates how large a text is for chunking purposes. It treats languages written without spaces, such as Chinese, Japanese, and Korean, differently from space-separated text.

**Data flow**: It receives text and removes whitespace to see how much real content exists. If there is no content, it returns zero. If enough characters are CJK characters, it counts non-whitespace characters as the size; otherwise it counts runs of non-whitespace text like words. It returns an integer size estimate.

**Call relations**: `_slices`, `_recursive_split`, and `_greedy_merge` call this whenever they need to decide whether a piece is small enough. It is the measuring tape used throughout the chunking process.

*Call graph*: called by 3 (_greedy_merge, _recursive_split, _slices); 1 external calls (sub).


##### `TextChunker._cap_by_chars`  (lines 153–165)

```
def _cap_by_chars(self, text: str) -> list[str]
```

**Purpose**: This enforces a hard maximum character length for chunks. It is a safety net for very long text pieces that may still be too large after word-based splitting.

**Data flow**: It receives one text string. If the text fits within `max_chars`, it returns it as a one-item list, unless it is empty. If it is too long, it cuts it into character windows with a small overlap so the boundary is not too abrupt. It returns a list of capped text pieces.

**Call relations**: `_slices` calls this for short text and again at the end of the full splitting pipeline. It is the final size guard before pieces become chunks.

*Call graph*: called by 1 (_slices).


##### `TextChunker._recursive_split`  (lines 167–179)

```
def _recursive_split(self, text: str, level: int) -> list[str]
```

**Purpose**: This breaks large text into smaller pieces by trying increasingly fine natural separators. It prefers paragraph breaks before line breaks, sentence endings, punctuation, and finally whitespace.

**Data flow**: It receives text and a delimiter level. At the current level, it tries to split the text using that level’s separators. If no useful split is found, it moves to the next level. If a resulting piece is still too large, it recursively splits that piece at a finer level. It returns a list of smaller text pieces.

**Call relations**: `_slices` calls this when the full text is too large. It calls `_split_at_delimiters` for natural separators, `_count_words` to test piece size, and `_split_on_whitespace` as the last fallback.

*Call graph*: calls 3 internal fn (_count_words, _split_at_delimiters, _split_on_whitespace); called by 1 (_slices).


##### `TextChunker._split_at_delimiters`  (lines 182–195)

```
def _split_at_delimiters(text: str, delimiters: tuple[str, ...]) -> list[str]
```

**Purpose**: This splits text at the earliest occurrence of any separator in a given set. It keeps the separator attached to the piece before it, which helps preserve readable punctuation and line endings.

**Data flow**: It receives text and a tuple of delimiters such as paragraph breaks or sentence endings. It repeatedly finds the next earliest delimiter, cuts through it, and continues with the remaining text. Empty-looking pieces are removed. It returns a list of non-blank pieces.

**Call relations**: `_recursive_split` uses this while trying each delimiter level. It supplies the first pass of human-friendly cuts before later steps merge or overlap the pieces.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._split_on_whitespace`  (lines 197–213)

```
def _split_on_whitespace(self, text: str) -> list[str]
```

**Purpose**: This is the fallback splitter when punctuation and paragraph boundaries are not enough. It breaks text into groups based on whitespace, or into raw character slices if there are no useful words.

**Data flow**: It receives text. If normal word runs are found, it groups them by the target word count and joins each group back into a string. If there are no words, or one extremely long run, it slices the raw text by the target size. It returns non-blank pieces.

**Call relations**: `_recursive_split` calls this only after all delimiter levels are exhausted. It makes sure chunking still finishes even for unusual input like minified text, long identifiers, or text without spaces.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._greedy_merge`  (lines 215–229)

```
def _greedy_merge(self, pieces: list[str]) -> list[str]
```

**Purpose**: This combines small neighboring pieces so the final chunks are not unnecessarily tiny. It is called greedy because it keeps adding the next piece as long as the combined text stays within a reasonable size.

**Data flow**: It receives a list of pieces. Starting with the first piece, it tries to append each next piece. If the combined size is no more than about one and a half times the target word count, it keeps them together; otherwise it starts a new chunk. It returns a list of merged pieces.

**Call relations**: `_slices` calls this after recursive splitting. It uses `_count_words` to decide whether combining two pieces is still safe.

*Call graph*: calls 1 internal fn (_count_words); called by 1 (_slices); 1 external calls (ceil).


##### `TextChunker._apply_overlap`  (lines 231–237)

```
def _apply_overlap(self, chunks: list[str]) -> list[str]
```

**Purpose**: This adds a small amount of previous context to each chunk after the first. The goal is to make search results and embeddings less fragile when important meaning crosses a chunk boundary.

**Data flow**: It receives a list of chunks. If there is only one chunk or overlap is disabled, it returns the list unchanged. Otherwise, for each neighboring pair, it prefixes the later chunk with selected trailing text from the previous chunk. It returns the overlapped list.

**Call relations**: `_slices` calls this after merging. It uses `_trailing_context` to choose the repeated text and `itertools.pairwise` to walk through neighboring chunks.

*Call graph*: calls 1 internal fn (_trailing_context); called by 1 (_slices); 1 external calls (pairwise).


##### `TextChunker._trailing_context`  (lines 239–249)

```
def _trailing_context(self, text: str) -> str
```

**Purpose**: This chooses the overlap text to carry from one chunk into the next. It tries to include useful recent words while avoiding awkwardly starting in the middle of an earlier sentence when possible.

**Data flow**: It receives one chunk of text. If the chunk is not longer than the configured overlap size, it returns an empty string, avoiding a full duplicate. Otherwise it takes the last overlap-sized group of words. If there is a sentence boundary early enough inside that trailing text, it drops the earlier sentence fragment and returns the cleaner later part. Otherwise it returns the full trailing words.

**Call relations**: `_apply_overlap` calls this for each previous chunk when building overlapped chunks. It is the small cleanup step that makes overlap more sentence-aware.

*Call graph*: called by 1 (_apply_overlap).


##### `TextChunker._digest`  (lines 252–254)

```
def _digest(owner_kind: str, owner_id: str, subject: str, ordinal: int, text: str) -> str
```

**Purpose**: This creates a stable unique identifier for a chunk. The identifier changes if the owner, subject, position, or text changes.

**Data flow**: It receives owner kind, owner id, subject, ordinal number, and chunk text. It joins those values with a separator that is unlikely to appear accidentally, hashes the result with SHA-256, and prefixes it with `sha256:`. It returns the digest string.

**Call relations**: `TextChunker.chunk` calls this for every slice it turns into a `Chunk`. Later, `chunk_embed_upsert` uses these digests as the keep set for pruning, so the index can tell current chunks from stale ones.

*Call graph*: called by 1 (chunk); 1 external calls (sha256).


### `extensions/embed_openai/ufo_ext_embed_openai.py`

`io_transport` · `startup registration, then background embedding/indexing`

This extension is the bridge between the project and OpenAI’s text embedding API. An embedding is a list of numbers that represents the meaning of text, a bit like turning a sentence into coordinates on a map so similar sentences land near each other. The rest of the system needs these vectors for indexing and memory search, and this file supplies the default way to create them.

The file registers a backend named "default", so if no other embedding provider is chosen, the core system can find and use this one. It deliberately does not require an OpenAI API key when the app starts. Instead, it checks for OPENAI_API_KEY only when an embedding request is actually made. That lets local development start without configuration, while still failing clearly if someone tries to embed text without a key.

Before sending text to OpenAI, the file protects the request size. It clips very long individual texts and groups the remaining text into batches that stay under item-count and character-count limits. Then OpenAIEmbedClient sends each batch to the async OpenAI client, keeps the returned vectors in the same order as the input texts, and returns them as plain tuples of floats.

#### Function details

##### `plan_embed_batches`  (lines 32–48)

```
def plan_embed_batches(texts: tuple[str, ...]) -> tuple[tuple[str, ...], ...]
```

**Purpose**: This function prepares text for safe submission to the embedding provider. It trims texts that are too long and groups them into batches small enough to stay within the file’s configured request limits.

**Data flow**: It receives a tuple of text strings. For each string, it keeps only the allowed number of characters, then adds it to the current batch unless doing so would make the batch too large by item count or total characters. It returns a tuple of batches, where each batch is a tuple of clipped text strings ready to send.

**Call relations**: OpenAIEmbedClient.embed calls this just before talking to OpenAI. It acts like a packing step before shipping: the embed client gives it all requested texts, and it hands back provider-sized bundles so the later network calls are less likely to be rejected for being too large.

*Call graph*: called by 1 (embed).


##### `OpenAIEmbedClient.embed`  (lines 61–73)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This is the main workhorse that turns text into OpenAI embedding vectors. Someone uses it when the system needs numeric representations of text for indexing or semantic search.

**Data flow**: It receives a tuple of text strings. It reads OPENAI_API_KEY from the environment, and if the key is missing it raises a clear error. With the key, it creates an async OpenAI client, asks plan_embed_batches to split the input safely, sends each batch to OpenAI’s embeddings endpoint, sorts the response rows back into input order, converts each embedding value to a float, and returns a tuple of vectors.

**Call relations**: The embedding backend returned by build is this client. When the wider indexing or memory system asks for embeddings, this method is where the request becomes actual OpenAI API calls. It hands batching to plan_embed_batches and hands provider communication to openai.AsyncOpenAI.

*Call graph*: calls 1 internal fn (plan_embed_batches); 1 external calls (AsyncOpenAI).


##### `build`  (lines 76–81)

```
def build(ctx: ExtensionContext) -> EmbedClient
```

**Purpose**: This function creates the embedding client that the core system will use for this backend. It is intentionally lightweight and does not contact OpenAI or require an API key at startup.

**Data flow**: It receives an ExtensionContext, which represents the surrounding workspace or extension environment, but this backend does not need to read anything from it. It creates and returns an OpenAIEmbedClient with the default model settings.

**Call relations**: manifest points to this function as the factory for the "default" embedding backend. During system startup or extension loading, the core can call build to get an EmbedClient, and later that client’s embed method performs the real embedding work.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 84–89)

```
def manifest() -> Manifest
```

**Purpose**: This function tells the host application what this extension provides. In this case, it announces an embedding backend named "default" and says that build should be used to create it.

**Data flow**: It takes no input. It creates an EmbedBackendSpec containing the backend name and factory function, wraps that in a Manifest with the extension name and version, and returns the Manifest to the extension loader.

**Call relations**: The extension system calls manifest when discovering available extensions. The returned Manifest is the signpost that lets the core system resolve the default embedding backend and later call build when it needs an actual client.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/index_default/ufo_ext_index_default.py`

`domain_logic` · `indexing and search`

This file is the default memory search engine for the project. It solves a practical problem: once documents or other objects are split into searchable chunks, the system needs a reliable place to save those chunks and later find the most relevant ones. Without this file, a basic deployment would have no default way to index or search memory.

The file supports two database worlds. In PostgreSQL, it uses database-native full-text search for words and pgvector for vector similarity. In SQLite, it uses FTS5, SQLite’s built-in full-text search feature, and calculates vector similarity in Python by scanning stored rows. That makes SQLite simple and useful for development, while PostgreSQL is the stronger production path.

The main class, `DefaultIndex`, is given a transaction opener. Each operation opens a database transaction, checks which database dialect it is talking to, and runs the matching SQL. It can insert or update chunks, delete all chunks for an owner, prune old chunks after re-indexing, search by words, and search by vector meaning.

A useful analogy is a library catalog with two lookup systems: one finds books by exact words in the title or text, and the other finds books that are “close in meaning” to a query. This file keeps both systems in sync.

#### Function details

##### `pgvector_literal`  (lines 31–32)

```
def pgvector_literal(vector: tuple[float, ...]) -> str
```

**Purpose**: This helper turns a Python tuple of numbers into the text format expected by PostgreSQL’s pgvector extension. It is used when saving or searching vector embeddings in PostgreSQL.

**Data flow**: It receives a tuple of floating-point numbers. It converts each value to a plain float representation, joins them with commas, wraps them in square brackets, and returns that string for SQL parameters.

**Call relations**: When `DefaultIndex.upsert` stores a chunk in PostgreSQL, it calls this to prepare the chunk’s embedding. When `DefaultIndex.vector` searches by similarity in PostgreSQL, it calls this to prepare the query embedding.

*Call graph*: called by 2 (upsert, vector).


##### `cosine`  (lines 35–43)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: This computes cosine similarity, a common way to measure how close two vectors point in the same direction. SQLite uses it because this backend does not rely on a database vector index there.

**Data flow**: It receives two equal-length tuples of numbers. It calculates the size of each vector, returns 0 if either has no usable length, otherwise returns a similarity score based on their dot product divided by their lengths.

**Call relations**: During SQLite vector search, `DefaultIndex.vector` loads candidate embeddings from the database, unpacks them, and calls `cosine` to score each one before sorting the best matches.

*Call graph*: called by 1 (vector); 1 external calls (sqrt).


##### `pack_embedding`  (lines 46–47)

```
def pack_embedding(vector: tuple[float, ...]) -> bytes
```

**Purpose**: This helper turns a vector embedding into compact bytes so SQLite can store it in a database column. It is needed because SQLite does not have the same native vector type used by PostgreSQL here.

**Data flow**: It receives a tuple of floating-point numbers. It packs those numbers as little-endian 32-bit floats and returns the resulting byte string for storage.

**Call relations**: When `DefaultIndex.upsert` stores chunks in SQLite, it calls `pack_embedding` before writing the embedding to the `chunk` table.

*Call graph*: called by 1 (upsert); 1 external calls (pack).


##### `unpack_embedding`  (lines 50–51)

```
def unpack_embedding(blob: bytes) -> tuple[float, ...]
```

**Purpose**: This reverses `pack_embedding`: it turns stored SQLite bytes back into a tuple of numbers. It is used before comparing a stored embedding with a query embedding.

**Data flow**: It receives a byte string from the database. It reads the bytes as 32-bit floats and returns them as a tuple of numbers.

**Call relations**: When `DefaultIndex.vector` performs SQLite vector search, it calls `unpack_embedding` for each stored embedding before passing the result to `cosine`.

*Call graph*: called by 1 (vector); 1 external calls (unpack).


##### `_hit`  (lines 54–63)

```
def _hit(row: sa.RowMapping, score: float) -> Hit
```

**Purpose**: This helper turns a database result row into a `Hit`, which is the project’s neutral search-result object. It keeps PostgreSQL and SQLite result formatting from leaking into the rest of the system.

**Data flow**: It receives a database row and a numeric score. It copies the chunk identity, owner information, subject, position, text, and score into a new `Hit` object and returns it.

**Call relations**: Both `DefaultIndex.lexical` and `DefaultIndex.vector` use `_hit` after the database or Python scoring step has found matching rows. It is the final translation step before results leave this backend.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `DefaultIndex.upsert`  (lines 168–205)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This saves chunks into the index, replacing any older version of the same chunk. It is used when content has been chunked or re-chunked and needs to become searchable.

**Data flow**: It receives a tuple of `Chunk` objects. If there are none, it does nothing. Otherwise it opens a transaction, checks whether the connection is PostgreSQL or SQLite, writes each chunk into the main chunk table, converts embeddings into the right storage format, and for SQLite refreshes the separate full-text-search table as well. It returns nothing, but the database is updated.

**Call relations**: This is called by higher-level indexing code when new searchable chunks are ready. Inside, it uses `pgvector_literal` for PostgreSQL embeddings and `pack_embedding` for SQLite embeddings so each database gets data in the form it understands.

*Call graph*: calls 2 internal fn (pack_embedding, pgvector_literal).


##### `DefaultIndex.delete`  (lines 207–214)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This removes all indexed chunks belonging to one owner, such as one document or one stored object. It is used when that owner should no longer appear in search results.

**Data flow**: It receives an `IndexScope`, which identifies an owner by kind and ID. It opens a transaction, then deletes matching rows from PostgreSQL’s chunk table or, in SQLite, first deletes matching full-text rows and then deletes the main chunk rows. It returns nothing, but matching index data is removed.

**Call relations**: Higher-level code can call this directly when an owner is deleted. `DefaultIndex.prune` also calls it when the keep-set is empty, because pruning everything is the same as deleting the whole scope.

*Call graph*: called by 1 (prune).


##### `DefaultIndex.prune`  (lines 216–230)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This removes stale chunks for an owner while keeping a specified set of current chunks. It matters when an object is re-indexed and old chunk versions should not linger in search results.

**Data flow**: It receives an `IndexScope` and a set of chunk digests to keep. If the keep-set is empty, it deletes the whole scope. Otherwise it opens a transaction and removes every matching chunk whose digest is not in the keep-set, also cleaning SQLite’s full-text table when needed. It returns nothing, but old database rows disappear.

**Call relations**: This sits between full deletion and normal upsert. Re-indexing code can upsert the new chunks and then call `prune` so only the latest chunk set remains. When pruning means keeping nothing, it hands off to `DefaultIndex.delete`.

*Call graph*: calls 1 internal fn (delete).


##### `DefaultIndex.lexical`  (lines 232–268)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This searches chunks by words in their text. It is the keyword-search side of the index, useful when the user’s query contains terms that should appear in the stored text.

**Data flow**: It receives a query string, allowed subjects, an owner kind, and a result limit. If there are no subjects, or the query is empty after cleanup, it returns an empty tuple. Otherwise it opens a transaction, runs PostgreSQL full-text search or SQLite FTS5 search, converts each matching row into a `Hit`, and returns the hits ordered by the database’s relevance score.

**Call relations**: Search orchestration code calls this when it wants keyword matches. After the database returns rows, `DefaultIndex.lexical` uses `_hit` to produce the common result shape expected outside this backend.

*Call graph*: calls 1 internal fn (_hit).


##### `DefaultIndex.vector`  (lines 270–303)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This searches chunks by vector similarity, meaning it finds stored chunks whose embeddings are numerically close to the query embedding. It is the “similar meaning” side of the index.

**Data flow**: It receives a query embedding, allowed subjects, an owner kind, and a result limit. If the embedding or subjects are missing, it returns an empty tuple. In PostgreSQL, it asks the database to rank rows by vector distance and converts them into hits. In SQLite, it loads candidate rows, unpacks each stored embedding, scores it with cosine similarity in Python, sorts by score, and returns the top hits.

**Call relations**: Search orchestration code calls this when semantic search is needed. It uses `pgvector_literal` on PostgreSQL, and on SQLite it combines `unpack_embedding`, `cosine`, and `_hit` to recreate the vector-search behavior without a native vector index.

*Call graph*: calls 4 internal fn (_hit, cosine, pgvector_literal, unpack_embedding).


##### `manifest`  (lines 306–316)

```
def manifest() -> Manifest
```

**Purpose**: This tells the extension system that this file provides an index backend named `default`. It is how the wider application discovers and creates `DefaultIndex`.

**Data flow**: It takes no input. It builds a `Manifest` containing the extension name, version, and an `IndexBackendSpec`; that spec includes a factory that creates `DefaultIndex` using the transaction opener supplied by the host context. It returns the manifest object.

**Call relations**: During extension loading, the core system calls `manifest` to learn what this extension offers. Later, when the default index backend is selected, the registered factory creates a `DefaultIndex` connected to the workspace-scoped transaction system.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/turbopuffer/ufo_ext_turbopuffer.py`

`io_transport` · `startup and index request handling`

UFO needs a way to remember and find chunks of text. This file is an adapter between UFO’s own index interface and Turbopuffer’s HTTP API. Think of it like a plug adapter: UFO speaks in terms of chunks, owners, subjects, and hits; Turbopuffer expects namespaces, document ids, vectors, attributes, and query filters.

At startup, the file registers a backend named "turbopuffer" through a manifest. When selected, UFO builds a TurbopufferIndex with a shared HTTP client and a credential reader. Each request reads the Turbopuffer API key and sends it as a Bearer token, which is a standard HTTP authorization header.

When chunks are added, the file turns them into Turbopuffer documents. The chunk digest becomes the document id, the embedding becomes the vector for meaning-based search, and fields like owner kind, owner id, subject, order, and text become searchable attributes. Searches can be lexical, meaning word-based BM25 search, or vector-based, meaning approximate nearest-neighbor search over embeddings. Both are scoped so a query only sees the right owner kind and subjects.

Deletion is careful: Turbopuffer deletes by id, so the file first lists all chunk ids in a scope, then deletes them in batches. Pruning does the same but keeps ids that are still current after re-chunking.

#### Function details

##### `turbopuffer_id`  (lines 42–48)

```
def turbopuffer_id(chunk_digest: str) -> str
```

**Purpose**: This turns UFO’s chunk digest into a document id that Turbopuffer will accept. Normal SHA-256 digests are shortened with base64url encoding so they stay compact, while unusual ids are left alone.

**Data flow**: It receives a chunk digest string. If the string looks like a SHA-256 digest, it removes the prefix, converts the hex text into raw bytes, encodes those bytes into a URL-safe short string, and returns that. If the input is not in the expected SHA-256 format, it returns the original string unchanged.

**Call relations**: When chunks are written, upsert_body uses this to choose Turbopuffer document ids. When chunks are deleted or pruned, TurbopufferIndex.delete and TurbopufferIndex.prune use it again so the delete request names the exact same ids that were written earlier.

*Call graph*: called by 3 (delete, prune, upsert_body); 1 external calls (urlsafe_b64encode).


##### `chunk_digest_from_id`  (lines 51–60)

```
def chunk_digest_from_id(chunk_id: str) -> str
```

**Purpose**: This reverses Turbopuffer’s shortened document id back into UFO’s normal chunk digest form. It lets search results and exported rows look like the chunks UFO originally stored.

**Data flow**: It receives a Turbopuffer document id. If it has the expected shortened length, it tries to decode it as base64url bytes and returns a "sha256:" digest string. If decoding fails, or if the id is not the shortened form, it returns the original id unchanged.

**Call relations**: Search-result conversion uses this through hit_from_row so callers get familiar chunk digests. Scope listing also uses it in TurbopufferIndex._scope_chunks before delete and prune decide which chunks to remove.

*Call graph*: called by 2 (_scope_chunks, hit_from_row); 1 external calls (urlsafe_b64decode).


##### `upsert_body`  (lines 63–79)

```
def upsert_body(chunks: tuple[Chunk, ...]) -> dict[str, Any]
```

**Purpose**: This builds the JSON request body used to add or replace chunks in Turbopuffer. It arranges chunk data into the column-style format Turbopuffer expects.

**Data flow**: It receives a tuple of Chunk objects. It turns each chunk into parallel columns: ids, vectors, owner fields, subject, order, and text. It also declares cosine distance for vector search and marks the text field as usable for full-text search. The output is a dictionary ready to send as JSON.

**Call relations**: TurbopufferIndex.upsert calls this for each batch of embeddable chunks. Inside, it calls turbopuffer_id so document ids match the shortened format used later by delete and prune.

*Call graph*: calls 1 internal fn (turbopuffer_id); called by 1 (upsert).


##### `query_filters`  (lines 82–86)

```
def query_filters(owner_kind: str, subjects: frozenset[str]) -> list[Any]
```

**Purpose**: This builds the filter that keeps search results inside the right slice of memory. It limits results to one owner kind and a chosen set of subjects.

**Data flow**: It receives an owner kind and a frozen set of subjects. It sorts the subjects for stable output and returns a Turbopuffer filter expression saying: owner_kind must match, and subject must be one of these values.

**Call relations**: TurbopufferIndex._query uses this for both lexical and vector searches. That means TurbopufferIndex.lexical and TurbopufferIndex.vector both get scoped results instead of searching the whole namespace.

*Call graph*: called by 1 (_query).


##### `scope_filters`  (lines 89–96)

```
def scope_filters(scope: IndexScope, after_id: str | None) -> list[Any]
```

**Purpose**: This builds the filter used when listing all chunks that belong to one owner. It can also continue after a previous id, which allows paging through large scopes.

**Data flow**: It receives an IndexScope and an optional last-seen id. It returns a filter requiring the matching owner kind and owner id. If an after_id is provided, it also requires ids greater than that value so the next page starts after the previous one.

**Call relations**: TurbopufferIndex._scope_chunks calls this while exporting chunks for a delete or prune operation. The filter is what keeps that export focused on only the owner being cleaned up.

*Call graph*: called by 1 (_scope_chunks).


##### `hit_from_row`  (lines 99–108)

```
def hit_from_row(row: dict[str, Any], score: float) -> Hit
```

**Purpose**: This converts one Turbopuffer result row into UFO’s Hit object. A Hit is the shape UFO uses to represent a found chunk and its score.

**Data flow**: It receives a row dictionary from Turbopuffer and a score chosen by the caller. It converts the id back to a chunk digest, reads the stored attributes, casts values into the expected types, and returns a Hit with the text and score.

**Call relations**: TurbopufferIndex.lexical and TurbopufferIndex.vector call this after receiving rows from _query. It calls chunk_digest_from_id so the rest of UFO sees the original digest format rather than Turbopuffer’s shortened id.

*Call graph*: calls 1 internal fn (chunk_digest_from_id); called by 2 (lexical, vector); 1 external calls (__init__).


##### `vector_score`  (lines 111–117)

```
def vector_score(row: dict[str, Any], position: int, total: int) -> float
```

**Purpose**: This gives a vector search result a score where higher means better. It normalizes Turbopuffer’s distance-style answer into the score-style answer UFO expects.

**Data flow**: It receives a result row, the row’s position in the result list, and the total number of rows. If Turbopuffer supplied a cosine distance, it returns one minus that distance. If not, it falls back to a descending rank score based on position.

**Call relations**: TurbopufferIndex.vector calls this before turning rows into Hit objects. The score it produces is passed into hit_from_row so later ranking code can combine vector results with other result types.

*Call graph*: called by 1 (vector).


##### `TurbopufferIndex.upsert`  (lines 131–142)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This writes new or updated chunks into Turbopuffer. It skips chunks without embeddings because they cannot be used for vector search in this backend.

**Data flow**: It receives a tuple of Chunk objects. It filters to chunks that have an embedding, gets authorization headers, splits the chunks into safe-sized batches, builds a JSON body for each batch, sends each batch to the namespace endpoint, and raises an error if Turbopuffer rejects the request. It returns nothing after the remote index is updated.

**Call relations**: Core indexing code calls this when memory chunks need to be stored or refreshed. It asks _auth for the API key header, _path for the workspace namespace URL, and upsert_body for the request body.

*Call graph*: calls 3 internal fn (_auth, _path, upsert_body).


##### `TurbopufferIndex.delete`  (lines 144–152)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This removes all indexed chunks for a given owner scope. It is used when a whole document, memory owner, or similar scope should disappear from the search index.

**Data flow**: It receives an IndexScope. It gets authorization headers, lists all chunks currently in that scope, converts their digests back into Turbopuffer ids, sends batched delete requests, and raises an error if any delete request fails. The remote namespace is changed by removing those documents.

**Call relations**: Higher-level cleanup code calls this when a scope should be fully removed. It relies on _scope_chunks to discover what exists, turbopuffer_id to name the remote documents, _auth for authorization, and _path for the namespace endpoint.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.prune`  (lines 154–164)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This removes only the stale chunks in a scope while keeping the chunks whose digests are still current. It is useful after content is re-split into chunks, so old leftovers do not remain searchable.

**Data flow**: It receives an IndexScope and a keep set of chunk digests. It gets authorization headers, lists all chunks in the scope, filters out any chunk whose digest is in the keep set, converts the remaining digests to Turbopuffer ids, and sends batched delete requests. The result is that only unwanted remote documents are removed.

**Call relations**: Re-indexing flows call this when an owner has been refreshed and only obsolete chunks should be deleted. It uses _scope_chunks to inspect the scope, turbopuffer_id to address remote ids, _auth to authorize requests, and _path to reach the namespace.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.lexical`  (lines 166–174)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This searches chunks by words using BM25, a common full-text ranking method that favors text matching the query terms well. It returns UFO Hit objects instead of raw Turbopuffer rows.

**Data flow**: It receives a query string, subjects, owner kind, and result limit. If the query is blank or there are no subjects, it immediately returns an empty tuple. Otherwise it asks _query to rank by text BM25, gives each row a rank-based score, converts rows into Hit objects, and returns them.

**Call relations**: Recall or search code calls this when it wants word-based matches. It delegates the HTTP query to _query and then uses hit_from_row to translate each Turbopuffer row into UFO’s standard result shape.

*Call graph*: calls 2 internal fn (_query, hit_from_row).


##### `TurbopufferIndex.vector`  (lines 176–185)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This searches chunks by semantic similarity using an embedding vector. It finds chunks whose stored vectors are near the query vector, which helps match meaning even when the exact words differ.

**Data flow**: It receives an embedding, subjects, owner kind, and result limit. If the embedding is empty or there are no subjects, it returns an empty tuple. Otherwise it asks _query to run an approximate nearest-neighbor search, computes a positive score for each row, converts scored rows into Hit objects, and returns only hits with scores above zero.

**Call relations**: Recall or search code calls this when it wants meaning-based matches. It uses _query for the remote Turbopuffer request, vector_score to turn distance or rank into a usable score, and hit_from_row to produce standard UFO hits.

*Call graph*: calls 3 internal fn (_query, hit_from_row, vector_score).


##### `TurbopufferIndex._query`  (lines 187–200)

```
async def _query(self, rank_by: list[Any], owner_kind: str, subjects: frozenset[str], limit: int) -> list[dict[str, Any]]
```

**Purpose**: This is the shared remote query helper for both word search and vector search. It builds the Turbopuffer query request, sends it, and returns raw rows.

**Data flow**: It receives a rank_by instruction, owner kind, subject set, and limit. It builds a JSON body with ranking, result count, attributes to include, and filters. It sends the body to the namespace query endpoint with authorization headers. If the namespace does not exist yet, it returns an empty list; otherwise it raises on HTTP errors and returns the response rows.

**Call relations**: TurbopufferIndex.lexical and TurbopufferIndex.vector both call this instead of duplicating HTTP query logic. It uses query_filters for scoping, _auth for the Bearer token, and _path for the correct workspace namespace.

*Call graph*: calls 3 internal fn (_auth, _path, query_filters); called by 2 (lexical, vector).


##### `TurbopufferIndex._scope_chunks`  (lines 202–230)

```
async def _scope_chunks(self, scope: IndexScope, headers: dict[str, str]) -> list[Chunk]
```

**Purpose**: This lists all indexed chunks for one owner scope. It is mainly a preparation step for deleting or pruning, because Turbopuffer delete requests need document ids.

**Data flow**: It receives an IndexScope and already-built authorization headers. It repeatedly asks Turbopuffer for a page of rows sorted by id, using after_id to move through pages. Each row is converted into a lightweight Chunk with digest and attributes. It returns the full list, or the chunks found so far if the namespace does not exist.

**Call relations**: TurbopufferIndex.delete and TurbopufferIndex.prune call this before deciding what ids to delete. It uses scope_filters to stay inside the requested owner scope, _path to reach the namespace query endpoint, and chunk_digest_from_id to restore UFO-style digests.

*Call graph*: calls 3 internal fn (_path, chunk_digest_from_id, scope_filters); called by 2 (delete, prune); 1 external calls (__init__).


##### `TurbopufferIndex._auth`  (lines 232–234)

```
async def _auth(self) -> dict[str, str]
```

**Purpose**: This prepares the HTTP authorization header for Turbopuffer. It reads the API key from UFO’s credential system each time, so requests use the current workspace credential.

**Data flow**: It reads the Turbopuffer API key from the credential access object using the configured slot name. It returns a dictionary containing an Authorization header in Bearer-token form.

**Call relations**: Upsert, delete, prune, and the shared _query helper all call this before making HTTP requests. It is the small bridge between UFO’s credential storage and Turbopuffer’s expected request authentication.

*Call graph*: called by 4 (_query, delete, prune, upsert).


##### `TurbopufferIndex._path`  (lines 236–237)

```
def _path(self, suffix: str='') -> str
```

**Purpose**: This builds the Turbopuffer API path for the current workspace namespace. A namespace is Turbopuffer’s separate container for one group of documents.

**Data flow**: It receives an optional suffix such as "/query". It combines the fixed namespace prefix, the workspace id from credentials, and the suffix, then returns the path string used by the HTTP client.

**Call relations**: Every remote operation uses this to point at the right namespace: upsert writes there, delete and prune delete there, _query searches there, and _scope_chunks lists from there.

*Call graph*: called by 5 (_query, _scope_chunks, delete, prune, upsert).


##### `manifest`  (lines 240–260)

```
def manifest() -> Manifest
```

**Purpose**: This tells UFO that the Turbopuffer extension exists and how to create it. It declares the needed credential slot and registers the index backend name.

**Data flow**: It takes no input. It creates a Manifest containing the extension name, version, required Turbopuffer API key credential, and an index backend factory. That factory builds a TurbopufferIndex with the runtime credential access object and an async HTTP client pointed at Turbopuffer’s base URL.

**Call relations**: UFO’s extension loading process calls this at startup. The returned manifest lets core code select the backend when memory.index_backend is set to "turbopuffer" and then construct TurbopufferIndex for later indexing and search work.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Memory recall wiring
The memory extension package, event limits, manifest hooks, and shared scope helpers define how prompt-time recall is registered and addressed.

### `extensions/memory/ufo_ext_memory/__init__.py`

`other` · `cross-cutting`

This file does not contain executable logic. Its main value is as the front door for the memory extension package. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package. Here, it also carries a short summary of the extension’s purpose.

The memory extension appears to support a few related jobs. It keeps durable facts, meaning information meant to last beyond a single interaction. It can recall stored information when a user submits a prompt, using a `user_prompt_submit` hook. A hook is a named moment in the system where extra behavior can be plugged in, like adding a reminder note to a form before it is sent. It can also derive memory-related pages when a page changes, through a `page_change` hook. Finally, it includes a memory-index job, which likely prepares stored memories so they can be searched or retrieved efficiently.

Without this file, the package may not be recognized consistently as a Python module, and newcomers would lose this concise map of what the memory extension is for.


### `extensions/memory/ufo_ext_memory/events.py`

`config` · `cross-cutting`

The memory extension needs a consistent way to report what it did when it tried to bring back relevant memories before the system answers. This file is the small shared label sheet for that reporting. The event name, `memory.pre_response_recall`, is a stable string that other parts of the system can listen for or record, much like a standard form title that tells everyone what kind of report they are reading. The two limits keep event data compact and predictable: only up to eight recalled memory identifiers should be included, and an error class name should be shortened if it is too long. Without these constants, different parts of the extension might spell the event name differently or include too much data, making logs harder to search and event consumers easier to break. The file contains no active code. It simply defines shared facts that help the memory feature speak in a clear, consistent format.


### `extensions/memory/ufo_ext_memory/manifest.py`

`orchestration` · `startup registration, request handling, hooks, and scheduled background jobs`

This file is the front desk and schedule board for the memory feature. It tells the larger UFO system: “Here are the memory tools, here is when to run them, and here is the work that should happen in the background.” The extension lets an agent store durable facts, search stored facts and synced source pages, and automatically place useful memories into a conversation before the model answers.

The main flow has three parts. First, the agent-facing tools let the model search memory or write a new memory item. A search can use several focused queries at once, then combines the results fairly so one query does not drown out the others. A write stores a persistent fact either for a specific member or for shared workspace use.

Second, hooks react to events from the host system. When a user prompt arrives, the recall hook tries to fetch relevant memories and inject them into the turn’s context. It is deliberately “fail-soft”: if search is slow or broken, it logs the problem and lets the conversation continue. Page-change hooks also watch changed source pages, indexing them for search and deriving durable facts from them.

Third, scheduled jobs keep memory healthy. One indexes newly written memory items. Another consolidates older related facts into summaries, like periodically tidying a notebook so it stays useful.

#### Function details

##### `_date_bound`  (lines 150–161)

```
def _date_bound(value: str | None, *, end: bool) -> datetime | None
```

**Purpose**: This helper turns an optional date or date-time string into a timezone-aware UTC time boundary for memory searches. It makes bare end dates behave the way people expect: searching through a date includes that whole day.

**Data flow**: It receives a string such as "2026-01-31" or a full ISO date-time, plus a flag saying whether it is an end boundary. If the input is missing, it returns nothing. Otherwise it parses the text, adds UTC if no timezone was provided, and for a bare end date moves the boundary to the next midnight. The result is a datetime object used to filter search results.

**Call relations**: The memory search tool calls this before searching, so user-supplied start_date and end_date values become real time limits. If the text is malformed, the parsing error is allowed to surface as a recoverable tool error instead of silently changing the search.

*Call graph*: called by 1 (memory_search_handler); 2 external calls (fromisoformat, timedelta).


##### `MemorySearchService.search`  (lines 170–219)

```
async def search(self, queries: tuple[str, ...], member_id: UUID | None, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This is the shared search workflow for memory. It searches both stored memory facts and indexed source-page snippets, using up to three focused queries, then returns a clean combined list of matches.

**Data flow**: It receives search queries, an optional member ID, and optional start and end times. It chooses the subjects to search, such as the current member plus shared memory, asks the memory store to recall matching memory items, and also asks it to search source pages. It runs those searches in parallel, interleaves results from the different queries so each query gets a fair chance, removes duplicates, limits the result count, and returns MemoryMatch objects with readable text, kind, object reference, and creation time.

**Call relations**: The memory_search tool builds this service and calls it when the agent asks to look up memory. The manifest also registers this class as the default memory search provider, so other extensions can depend on the same search behavior instead of reimplementing it.

*Call graph*: 6 external calls (__init__, __init__, gather, zip_longest, recall_subjects, store_for).


##### `match_line`  (lines 222–229)

```
def match_line(match: MemoryMatch) -> str
```

**Purpose**: This formats one memory search result into a simple line of text for the agent to read. It includes the snippet, the kind of result, and when possible a reference that can be opened later.

**Data flow**: It receives one MemoryMatch. It builds a line beginning with the match kind and text. If the match has an object reference, it appends that reference and, if available, the creation date. The output is a single human-readable string.

**Call relations**: The memory_search tool uses this after it receives matches from MemorySearchService.search. It turns structured search results into the plain text shown in the tool response.

*Call graph*: called by 1 (memory_search_handler).


##### `memory_search_handler`  (lines 232–249)

```
async def memory_search_handler(ctx: ToolContext, args: MemorySearchInput) -> ToolResult
```

**Purpose**: This is the actual handler behind the agent’s memory_search tool. It lets the agent look up remembered facts and source snippets using focused queries and optional date limits.

**Data flow**: It receives the tool context and validated search arguments. It checks that the extension context exists, converts optional date strings into time boundaries, calls MemorySearchService.search with the current audience member, and then turns the matches into text lines. It returns a ToolResult saying either that no memory matched or listing the matching memories and source snippets.

**Call relations**: The manifest registers this as the handler for the memory_search ToolDef. When the model calls that tool, this function coordinates date parsing, search, formatting, and the final response.

*Call graph*: calls 2 internal fn (_date_bound, match_line); 3 external calls (__init__, __init__, __init__).


##### `memory_update_handler`  (lines 252–270)

```
async def memory_update_handler(ctx: ToolContext, args: MemoryUpdateInput) -> ToolResult
```

**Purpose**: This is the actual handler behind the agent’s memory_update tool. It records a durable fact so later conversations can recall it.

**Data flow**: It receives the tool context and the memory details to write. It decides whether the memory belongs to shared workspace memory or the current member’s private memory, builds a MemoryWrite record with the body, class, kind, confidence, and optional source reference, and commits it to the memory store. It returns a short confirmation showing where the memory was stored.

**Call relations**: The manifest registers this as the handler for the memory_update ToolDef. The agent calls it when it learns something persistent, and the function hands the write to the storage layer.

*Call graph*: 5 external calls (__init__, __init__, __init__, member_subject, store_for).


##### `recall_hook`  (lines 273–303)

```
async def recall_hook(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook automatically adds relevant memories to a user turn before the model answers. Its most important rule is that memory recall must never block or break the conversation.

**Data flow**: It receives a hook context. If the event is not a user prompt submission, it does nothing. For a real prompt, it finds the right recall subjects, searches memory using the prompt text, and filters out topic-only matches before building an InjectContext containing bullet-point memories. If recall fails or takes too long, it logs the error and returns nothing, so the user’s turn continues without injected memory.

**Call relations**: The manifest registers this for user_prompt_submit events. The host calls it before model execution; it calls the memory store for recall and the observability logger for a recall event, then optionally hands injected context back to the host.

*Call graph*: 5 external calls (__init__, timeout, log, recall_subjects, store_for).


##### `index_memory`  (lines 306–311)

```
async def index_memory(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job indexes newly committed memory items so they can be searched efficiently. Indexing means turning text into searchable chunks and embeddings, which are numeric representations used for meaning-based search.

**Data flow**: It receives the extension context. It checks that the required index and embedding backends are available, builds a MemoryIndexer with those services, the database transaction, and a text chunker, then runs the indexer. It does not return data; it updates indexing state through the indexer’s work.

**Call relations**: The manifest registers this as the memory_index job. The scheduler calls it for workspaces that have unindexed memory items, and it delegates the actual indexing steps to MemoryIndexer.

*Call graph*: 2 external calls (__init__, __init__).


##### `index_pages`  (lines 314–329)

```
async def index_pages(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook reacts to changed source pages and makes them searchable through memory search. It creates index chunks and a mirror record for each changed page delivered by the core runner.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it does nothing. Otherwise it checks that indexing and embedding services are available, builds a PageIndexer with the workspace and text chunker, and applies it to the changed pages. It returns no special hook outcome after the indexing work.

**Call relations**: The manifest registers this as one of the page_change hooks. When the host replays source-page changes, this function receives each batch and passes it to PageIndexer so source documents can later appear in memory search results.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_facts`  (lines 332–340)

```
async def derive_facts(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook turns changed source pages into durable memory facts. It is the learning-from-documents side of the memory extension.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it does nothing. For real page changes, it builds a FactDeriver with the memory store and available model, then applies it to the changed pages. The output is not returned directly; any useful derived facts are written into memory storage.

**Call relations**: The manifest registers this as a second page_change hook, separate from page indexing. The host calls it on changed pages, and it hands the batch to FactDeriver so facts can be distilled and stored.

*Call graph*: 2 external calls (__init__, store_for).


##### `consolidate_memory`  (lines 343–351)

```
async def consolidate_memory(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job tidies older memory facts by grouping related facts into broader summaries. This helps keep long-term memory useful instead of filling it with many small overlapping notes.

**Data flow**: It receives the extension context. It checks that the embedding backend is available, builds a MemoryConsolidator with embedding, transaction, workspace ID, and model access, then runs it. The job updates memory records by creating summary memories and superseding originals where appropriate.

**Call relations**: The manifest registers this as the memory_consolidate job. The scheduler calls it for workspaces that appear to have enough older facts to consolidate, and it delegates the actual clustering and summary writing to MemoryConsolidator.

*Call graph*: 1 external calls (__init__).


##### `_items_awaiting_index`  (lines 354–359)

```
def _items_awaiting_index() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the database query used to find workspaces with memory items that still need indexing. It is a filter for deciding where the memory_index job has real work to do.

**Data flow**: It reads the memory item table definition and constructs a SQL query. The query selects distinct workspace IDs where the embedding digest is missing, which means an item has not yet been indexed. It returns the query object, not the query results.

**Call relations**: The manifest passes this query builder to owner_candidates for the memory_index job. The job scheduler uses it to choose which workspace owners should run indexing work.

*Call graph*: 1 external calls (select).


##### `_consolidatable_workspaces`  (lines 362–377)

```
def _consolidatable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the database query used to find workspaces that are worth running memory consolidation on. It avoids scheduling the consolidation job for places with too few, too new, or already superseded facts.

**Data flow**: It calculates an age cutoff based on the current UTC time and the minimum fact age. Then it constructs a SQL query selecting workspace IDs with enough live fact memories older than that cutoff. It returns the query object for the scheduler to use.

**Call relations**: The manifest passes this query builder to owner_candidates for the memory_consolidate job. The scheduler uses it to call consolidation only where a meaningful cluster of older facts might exist.

*Call graph*: 2 external calls (now, select).


##### `manifest`  (lines 380–455)

```
def manifest() -> Manifest
```

**Purpose**: This function is the extension’s public declaration to the host system. It lists everything the memory extension contributes: tools, object types, hooks, jobs, skills, search provider, and web surface routes.

**Data flow**: It takes no input. It builds ToolDef objects for memory search and update, HookSpec objects for prompt recall and page changes, JobSpec objects for indexing and consolidation, plus object, skill, provider, and surface declarations. It returns one Manifest object containing all of that registration information.

**Call relations**: The host calls this when loading the extension. The returned Manifest tells the rest of the system which handlers to call for agent tools, user prompt hooks, page-change hooks, scheduled jobs, memory search provider access, and the memory user interface surface.

*Call graph*: 8 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, owner_candidates).


### `core/src/ufo/memory.py`

`domain_logic` · `request handling`

This file is a small but important contract between two sides of the memory feature: extensions that know how to search stored memories, and callers that want to use those memories in a conversation. Without this shared contract, each memory provider could return different-looking results, and consumers would have to understand every provider’s private details.

The central result type is `MemoryMatch`, which represents one search hit. It carries the kind of memory, the text snippet to show or inject, and, when available, a durable object reference that can be opened later. It may also include when the memory was created, so newer and older hits can be judged without opening them.

`MemorySearchProvider` is a protocol, meaning a “shape” that other classes promise to follow. Any provider can plug in as long as it offers the expected async `search` method.

`MemorySearch` is the small coordinator. A caller gives it a conversation ID and search queries. Before searching, it looks up which member belongs to that conversation inside the current workspace. It then passes the queries, member ID, and optional time window to the provider. This prevents a search from accidentally using the wrong person’s memory, like checking the name on a mailbox before opening it.

#### Function details

##### `MemorySearchProvider.search`  (lines 34–40)

```
async def search(self, queries: tuple[str, ...], member_id: UUID | None, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This is the promised search method that every memory provider must offer. It describes how a provider should accept search text, an optional member identity, and an optional time range, then return matching memories in the common `MemoryMatch` format.

**Data flow**: The inputs are one or more query strings, an optional member ID, and optional start and end times. A concrete provider uses that information to search whatever memory store it owns, then returns a tuple of matching memory records. This protocol method itself does not perform the search; it defines the expected before-and-after shape.

**Call relations**: Other code, especially `MemorySearch.search`, relies on this method being present. Once `MemorySearch.search` has found the correct member for a conversation, it hands the actual searching off to the provider through this method.


##### `MemorySearch.search`  (lines 49–66)

```
async def search(self, conversation_id: UUID, queries: tuple[str, ...], start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This method searches memory for a specific conversation, but first translates the conversation into the member whose memory should be searched. That extra lookup keeps memory recall scoped to the right person and workspace.

**Data flow**: It receives a conversation ID, search queries, and optional start and end times. It opens a workspace database transaction, reads the current workspace ID, and looks up the member ID for that conversation. After the lookup, it calls the configured provider with the queries, member ID, and time window, then returns the provider’s memory matches unchanged.

**Call relations**: This is the public coordinator around a provider search. It uses `workspace_tx` to safely read from the workspace database, `ws_current` to make sure the lookup is limited to the active workspace, and SQLAlchemy’s `select` to ask for the conversation’s member. After that preparation, it delegates the real recall work to `MemorySearchProvider.search`.

*Call graph*: 3 external calls (select, workspace_tx, ws_current).


### `core/src/ufo/subjects.py`

`data_model` · `cross-cutting`

This file is a small but important shared vocabulary for the memory system. A “subject” is the scope of a memory item: either something everyone in a conversation can recall, or something tied to one specific member. Think of it like labeling notes in a filing cabinet: one drawer is marked “shared,” while each person also has their own drawer marked with their member ID.

The file defines two basic pieces of text. `SHARED_SUBJECT` is the fixed label for memories visible to everyone. `MEMBER_SUBJECT_PREFIX` is the prefix used when building a label for one member’s private memory. The helper function `member_subject` takes a member’s unique ID and turns it into the exact subject string used elsewhere.

This matters because several parts of the system need to agree on these labels. The core sync pipeline, source interfaces, and memory extension all refer to subjects when deciding what memory should be stored, searched, or recalled. Without this shared definition, different parts of the project might spell or format subjects differently, causing memory to be hidden, mixed up, or missed entirely.

#### Function details

##### `member_subject`  (lines 15–16)

```
def member_subject(member_id: UUID) -> str
```

**Purpose**: This function builds the standard subject label for one member’s private memory. Someone uses it when they have a member’s unique ID and need the exact text key that the rest of the memory system expects.

**Data flow**: It receives a `UUID`, which is a unique identifier for a member. It adds that ID after the fixed `member:` prefix, producing a string like `member:<id>`. It does not change anything else; it simply returns the finished subject label.

**Call relations**: This function is the shared shortcut for creating member-specific subject names. Other parts of the system can call it whenever they need to store or look up memory for a particular member, instead of rebuilding the string by hand and risking a different format.


### Memory storage and consolidation
The memory store, condenser, and read-only object views create durable memories, merge stale related facts, and expose stored results for later inspection.

### `extensions/memory/ufo_ext_memory/store.py`

`domain_logic` · `request handling and background indexing`

This file gives the system a durable “memory notebook.” A memory can be a fact, preference, decision, event, or task, and it can belong to a shared space or to one member’s private space. Writing a memory is deliberately quick: the row is saved, but the heavier work of cutting text into chunks and making embeddings (number lists that capture meaning for semantic search) is left to a background indexer. That keeps normal writes from slowing down.

When something asks to recall memories, the store searches in two ways: by words that match the query, and by meaning through vector search. It blends those results so that items found by either route can rise to the top. It also checks newly written memories that have not been indexed yet, so a fresh fact can still be recalled right away. After finding candidate rows, it reads the real database records back, drops replaced items, applies time decay for facts, limits over-representation by memory type, and turns episodic memories into “topic pointers” instead of injecting their full text.

The file also mirrors source pages into the search index. If a page is deleted, its searchable chunks and mirror row are removed. Without this file, the extension could store text, but it would not have reliable, scoped, ranked, freshness-aware memory recall.

#### Function details

##### `recall_subjects`  (lines 109–114)

```
def recall_subjects(member_id: UUID | None) -> frozenset[str]
```

**Purpose**: Decides which visibility spaces should be searched for a conversation. If there is a linked member, recall includes that member’s private subject plus the shared subject; otherwise it searches only shared memory.

**Data flow**: It receives an optional member ID. If the ID is present, it turns it into that member’s subject name and combines it with the shared subject; if not, it returns only the shared subject. The result is a frozen set of subject strings used as a search filter.

**Call relations**: This is a small helper used before recall. It relies on `ufo.sdk.sources.member_subject` to format a member-specific subject, so the rest of the memory store can simply receive a ready-made subject filter.

*Call graph*: 1 external calls (member_subject).


##### `inventory`  (lines 148–203)

```
async def inventory(transaction: Transaction, workspace_id: UUID) -> tuple[MemoryInventoryItem, ...]
```

**Purpose**: Returns a bounded, newest-first listing of stored memories for an operator or explorer view. Unlike recall, it does not search by a query; it shows the stored rows plus useful live signals such as age and decay.

**Data flow**: It receives a transaction opener and a workspace ID. It reads recent `memory_item` rows for that workspace, computes one shared current time, then builds `MemoryInventoryItem` objects with raw row fields plus age, half-life, and decay factor. The output is a tuple of inventory items.

**Call relations**: This function is separate from normal recall because it is for inspection rather than answering a user query. It calls `_aware`, `half_life_days`, and `decay_multiplier` so the explorer reports the same freshness math that recall uses.

*Call graph*: calls 3 internal fn (_aware, decay_multiplier, half_life_days); 3 external calls (__init__, now, select).


##### `_aware`  (lines 206–207)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Makes sure a date-time value has timezone information. This prevents age calculations from mixing timezone-aware and timezone-naive values.

**Data flow**: It receives a `datetime`. If it already has timezone information, it returns it unchanged; otherwise it marks it as UTC. The returned value can safely be compared with other UTC times.

**Call relations**: This helper is used by `inventory` and `decay_multiplier` whenever they compute how old a memory is. It is a guardrail around date math.

*Call graph*: called by 2 (decay_multiplier, inventory); 1 external calls (replace).


##### `_fuse`  (lines 250–273)

```
def _fuse(legs: tuple[tuple[Hit, ...], ...], cosine_leg: tuple[Hit, ...]) -> dict[str, tuple[float, float, str]]
```

**Purpose**: Combines several search result lists into one best score per owning item or page. It lets word-based search and meaning-based search reinforce each other instead of choosing only one source.

**Data flow**: It receives search “legs,” where each leg is an ordered list of chunk hits, plus the vector leg used for raw semantic closeness. It gives each chunk credit based on how highly it ranked in each leg, keeps the best chunk for each owner, and records that owner’s best vector score. It returns a dictionary from owner ID to fused score, vector score, and matched text.

**Call relations**: `fuse_hits` and `fuse_recall` both call this shared combiner. It is the common ranking workshop where separate search routes are turned into one comparable signal.

*Call graph*: called by 2 (fuse_hits, fuse_recall); 1 external calls (from_iterable).


##### `fuse_hits`  (lines 276–281)

```
def fuse_hits(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks source-page search results by combining lexical and vector hits into one fused score. It is used where the fused rank itself is the final relevance signal.

**Data flow**: It receives word-search hits, vector-search hits, and a limit. It asks `_fuse` to combine the two result lists, sorts owners by fused score, cuts the list to the requested limit, and returns `Fused` records containing owner ID, score, and snippet text.

**Call relations**: `MemoryStore.search_sources` calls this after asking the index for source-page hits. It hands back a compact ranked list that can then be checked against the page mirror table.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (search_sources); 1 external calls (__init__).


##### `fuse_recall`  (lines 284–301)

```
def fuse_recall(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], tail: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks memory recall candidates by blending fused rank with raw semantic closeness. This gives a memory a boost when it is not only well-ranked but also meaningfully close to the query.

**Data flow**: It receives lexical hits, vector hits, an unindexed-tail hit list, and a limit. It fuses all three legs, normalizes the fused rank, mixes it with the best vector score, sorts by that blended score, and returns the top `Fused` records.

**Call relations**: `MemoryStore.recall` calls this before reading full memory rows. It builds the first recall ranking, including fresh unindexed memories from the tail leg.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (recall); 1 external calls (__init__).


##### `half_life_days`  (lines 319–325)

```
def half_life_days(item_class: str, memory_kind: str) -> float | None
```

**Purpose**: Returns how quickly a memory should fade in ranking, measured as a half-life in days. Only fact-class memories decay; episodic and semantic memories keep full relevance.

**Data flow**: It receives an item class and memory kind. If the class is not `fact`, it returns `None`; otherwise it looks up the kind’s configured half-life, falling back to the default fact half-life. The result tells later code whether and how to apply recency decay.

**Call relations**: `inventory` uses this to display decay settings, and `decay_multiplier` uses it to calculate the actual ranking multiplier.

*Call graph*: called by 2 (decay_multiplier, inventory).


##### `decay_multiplier`  (lines 328–340)

```
def decay_multiplier(item_class: str, memory_kind: str, confidence: int, as_of: datetime | None, now: datetime) -> float
```

**Purpose**: Calculates how much a memory’s relevance should be reduced because of age and confidence. It is the shared formula used by both recall and inventory reporting.

**Data flow**: It receives item class, memory kind, confidence, the time the memory is about, and the current time. If the item does not decay or has no time to age from, it returns `1.0`; otherwise it combines confidence with age-based half-life decay. The output is a multiplier applied to relevance.

**Call relations**: `decay_factor` calls this for recalled items, and `inventory` calls it for operator display. It uses `_aware` for safe time comparison and `half_life_days` for the configured decay speed.

*Call graph*: calls 2 internal fn (_aware, half_life_days); called by 2 (decay_factor, inventory).


##### `decay_factor`  (lines 343–346)

```
def decay_factor(item: Recalled, now: datetime) -> float
```

**Purpose**: Applies the shared decay formula to a recalled memory item. It is a convenience wrapper for recall ranking.

**Data flow**: It receives a `Recalled` item and the current time. It chooses the best timestamp to age from, preferring `as_of` and falling back to `created_at`, then calls `decay_multiplier`. The result is a single number used to adjust the item’s score.

**Call relations**: `MemoryStore.recall` calls this after enriching fused hits with database rows. It connects the general decay formula to the recalled-item data shape.

*Call graph*: calls 1 internal fn (decay_multiplier); called by 1 (recall).


##### `enforce_type_diversity`  (lines 349–367)

```
def enforce_type_diversity(rows: tuple[Recalled, ...], limit: int) -> tuple[Recalled, ...]
```

**Purpose**: Prevents one class of memory from filling the whole recall result list. This helps the final answer include a healthier mix when many similar items score highly.

**Data flow**: It receives ranked recalled rows and a limit. It walks the rows in order, keeps only a capped number per item class at first, saves overflow for later, and backfills from overflow if there is still room. The output is a tuple no longer than the requested limit.

**Call relations**: `MemoryStore.recall` calls this after score decay. It is one of the final shaping steps before episodic memories are converted into topic pointers.

*Call graph*: called by 1 (recall).


##### `as_topic_pointer`  (lines 370–380)

```
def as_topic_pointer(item: Recalled, index: int) -> Recalled
```

**Purpose**: Turns an episodic memory into a short pointer instead of returning its full body. This treats episodes as browseable breadcrumbs rather than automatic context to inject verbatim.

**Data flow**: It receives a recalled item and its position in the final list. If the item is not episodic, it returns it unchanged; if it is episodic, it replaces the body with a short “Memory topic” label and marks the recall mode as `topic`. The output is a `Recalled` item ready for callers.

**Call relations**: `MemoryStore.recall` calls this as the final transformation on diversified results. It uses `dataclasses.replace` to keep the original item data except for the topic-style fields.

*Call graph*: called by 1 (recall); 1 external calls (replace).


##### `MemoryStore.commit`  (lines 403–445)

```
async def commit(self, write: MemoryWrite) -> None
```

**Purpose**: Saves one memory item to the database without doing indexing work inline. This keeps memory writes fast and marks the item as due for later chunking and embedding.

**Data flow**: It receives a `MemoryWrite` object with subject, body, type, source, confidence, and timing information. It creates a stable ID from workspace, subject, class, and body, then inserts the row or updates selected fields if the same memory already exists. It changes the database and returns nothing.

**Call relations**: This is the write entry into `MemoryStore`. The background `MemoryIndexer` later notices rows whose embedding digest is still empty and builds their searchable chunks.

*Call graph*: 1 external calls (uuid5).


##### `MemoryStore.recall`  (lines 447–473)

```
async def recall(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None) -> tuple[Recalled, ...]
```

**Purpose**: Finds the most relevant memories for a query under a subject filter. It combines search ranking, freshly written unindexed rows, database validation, time decay, diversity, and episodic topic handling.

**Data flow**: It receives a query, allowed subjects, a result limit, and optional start/end date bounds. It gets lexical and vector index legs through `_legs`, gets unindexed matches through `_untail_leg`, fuses them with `fuse_recall`, reads full rows through `_enrich`, applies decay with `decay_factor`, diversifies by type, and converts episodic rows with `as_topic_pointer`. The output is a tuple of `Recalled` items.

**Call relations**: This is the main read path for memory recall. It coordinates the lower-level helpers in this file and returns the final memory set to whatever feature is asking for remembered context.

*Call graph*: calls 7 internal fn (_enrich, _legs, _untail_leg, as_topic_pointer, decay_factor, enforce_type_diversity, fuse_recall); 2 external calls (replace, now).


##### `MemoryStore.search_sources`  (lines 475–520)

```
async def search_sources(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None) -> tuple[SourceMatch, ...]
```

**Purpose**: Searches synced source pages, such as imported documents, using the same word-and-meaning approach as memory recall. It returns matching snippets from indexed page chunks plus page metadata from the mirror table.

**Data flow**: It receives a query, allowed subjects, a limit, and optional date bounds. It asks `_legs` for lexical and vector page hits, fuses them with `fuse_hits`, reads matching page rows from `mem_page`, filters by date if needed, and returns `SourceMatch` objects with page ID, subject, snippet, score, and creation time.

**Call relations**: This method is the page-search companion to `recall`. It depends on `PageIndexer` having kept page chunks and the `mem_page` mirror in sync.

*Call graph*: calls 2 internal fn (_legs, fuse_hits); 3 external calls (__init__, select, UUID).


##### `MemoryStore._legs`  (lines 522–530)

```
async def _legs(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[tuple[Hit, ...], tuple[Hit, ...]]
```

**Purpose**: Runs the two standard index searches for a query: word matching and vector meaning matching. It centralizes this repeated search setup for both memories and source pages.

**Data flow**: It receives a query, subject filter, owner kind, and limit. It first tries to embed the query through `_embed_query`, always performs lexical search, and performs vector search only if an embedding was produced. It returns a pair: lexical hits and vector hits.

**Call relations**: `MemoryStore.recall` and `MemoryStore.search_sources` both call this. It hides the details of query embedding and index calls so the higher-level methods can focus on ranking and row lookup.

*Call graph*: calls 1 internal fn (_embed_query); called by 2 (recall, search_sources).


##### `MemoryStore._untail_leg`  (lines 532–575)

```
async def _untail_leg(self, query: str, subjects: frozenset[str], limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches very recent memory rows that have not yet been indexed. This makes a just-committed memory recallable before the background indexer has processed it.

**Data flow**: It receives a query, subject filter, and limit. It splits the query into simple lowercase terms, reads a bounded set of newest unembedded, non-superseded memory rows for those subjects, counts term matches in each body, creates `Hit` objects for rows with matches, sorts them by match count, and returns the top hits.

**Call relations**: `MemoryStore.recall` calls this as a third search leg. Its results are later passed into `fuse_recall`, where they can compete with normal index hits without being double-counted after indexing finishes.

*Call graph*: called by 1 (recall); 3 external calls (__init__, split, select).


##### `MemoryStore._embed_query`  (lines 577–585)

```
async def _embed_query(self, query: str) -> tuple[float, ...]
```

**Purpose**: Turns a text query into an embedding for vector search, while failing safely if embedding is unavailable. An embedding is a numeric representation of meaning used to find semantically similar text.

**Data flow**: It receives the query string. Blank queries immediately produce an empty tuple; otherwise it asks the embed backend for one vector. If embedding fails, it logs a warning and returns an empty tuple instead of breaking recall.

**Call relations**: `MemoryStore._legs` calls this before vector search. If it returns no vector, the system still falls back to lexical search.

*Call graph*: called by 1 (_legs).


##### `MemoryStore._enrich`  (lines 587–639)

```
async def _enrich(self, fused: tuple[Fused, ...], start: datetime | None, end: datetime | None) -> tuple[Recalled, ...]
```

**Purpose**: Loads full memory rows for the fused search winners and removes items that should no longer be served. Search hits alone are not trusted as final answers.

**Data flow**: It receives fused hits and optional date bounds. It converts hit owner IDs to UUIDs, queries `memory_item` for matching rows that are not superseded and are inside the date window, then rebuilds results in fused-hit order as `Recalled` objects. Hits whose rows are missing or filtered out disappear.

**Call relations**: `MemoryStore.recall` calls this after `fuse_recall`. It bridges from index results back to authoritative database rows before decay and final shaping happen.

*Call graph*: called by 1 (recall); 3 external calls (__init__, select, UUID).


##### `store_for`  (lines 642–652)

```
def store_for(ext: ExtensionContext) -> MemoryStore
```

**Purpose**: Builds a `MemoryStore` from an extension context. It makes sure the needed index and embedding backends are available before memory operations begin.

**Data flow**: It receives an `ExtensionContext`. If the context lacks either the index backend or embed backend, it raises an error; otherwise it copies the index, embedder, transaction opener, and workspace ID into a new `MemoryStore`. The output is a ready-to-use store object.

**Call relations**: This is the construction helper used by callers that have an extension context. It wires the domain logic in this file to the workspace-scoped services provided by the wider system.

*Call graph*: 1 external calls (__init__).


##### `MemoryIndexer.run`  (lines 668–670)

```
async def run(self) -> None
```

**Purpose**: Processes a batch of memory rows that still need indexing. It is the background job entry point for turning saved memory text into searchable chunks.

**Data flow**: It takes no direct input beyond the indexer’s configured database transaction, index backend, embedder, and chunker. It claims due rows with `_claim_due`, then sends each claimed item to `_index_item`. It returns nothing, but updates the index and database as it works.

**Call relations**: This method orchestrates the memory indexing tick. `_claim_due` prevents overlapping workers from doing the same work, and `_index_item` performs the actual chunking, embedding, and completion stamp.

*Call graph*: calls 2 internal fn (_claim_due, _index_item).


##### `MemoryIndexer._claim_due`  (lines 672–704)

```
async def _claim_due(self) -> tuple[MemoryItem, ...]
```

**Purpose**: Atomically reserves a limited batch of memory items that need embeddings. The reservation acts like a short lease, so another indexer tick should skip those rows unless the lease expires.

**Data flow**: It reads the current time, computes a lease cutoff, selects rows whose embedding digest is empty and whose claim is missing or old, and marks selected rows as claimed. It returns those rows as `MemoryItem` objects.

**Call relations**: `MemoryIndexer.run` calls this before indexing. On PostgreSQL it uses locked selection to avoid two workers claiming the same rows; the claimed items then flow to `_index_item`.

*Call graph*: called by 1 (run); 5 external calls (now, timedelta, or_, select, update).


##### `MemoryIndexer._index_item`  (lines 706–726)

```
async def _index_item(self, item: MemoryItem) -> None
```

**Purpose**: Indexes one memory item and marks it as no longer due. This is where a saved memory becomes searchable by both words and meaning.

**Data flow**: It receives a `MemoryItem`. It sends the item body through `chunk_embed_upsert`, which chunks the text, embeds it, and upserts those chunks into the index; then it computes a SHA-256 digest of the body and updates the database row with that digest while clearing the claim. It returns nothing.

**Call relations**: `MemoryIndexer.run` calls this for each claimed row. It hands the heavy text-processing work to `ufo.sdk.index.chunk_embed_upsert`, then records completion in `memory_item`.

*Call graph*: called by 1 (run); 3 external calls (sha256, update, chunk_embed_upsert).


##### `PageIndexer.apply`  (lines 744–746)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Applies a delivered batch of source-page changes to the memory extension’s page index. It is the batch-level entry point used by the page-change hook.

**Data flow**: It receives a tuple of `PageChange` objects. It loops through them and passes each one to `_apply`. Its effects are the combined index and mirror-table updates for the batch.

**Call relations**: The wider page-change runner owns the cursor and batch delivery. This method simply fans the delivered changes into `_apply`, which performs the per-page work.

*Call graph*: calls 1 internal fn (_apply).


##### `PageIndexer._apply`  (lines 748–779)

```
async def _apply(self, change: PageChange) -> None
```

**Purpose**: Updates the searchable page index and page mirror for one source-page change. It also removes all traces of a page when the change says the page was tombstoned, meaning deleted.

**Data flow**: It receives one `PageChange`. If it is a tombstone, it deletes the page’s index scope and removes its `mem_page` row. Otherwise it chunks and embeds the page body into the index, then updates or inserts the mirror row with page ID, workspace, subject, and creation time. It returns nothing.

**Call relations**: `PageIndexer.apply` calls this for each page change. Its mirror rows are later read by `MemoryStore.search_sources`, while its index chunks are searched through `MemoryStore._legs`.

*Call graph*: called by 1 (apply); 5 external calls (__init__, delete, insert, update, chunk_embed_upsert).


### `extensions/memory/ufo_ext_memory/condenser.py`

`domain_logic` · `page-change processing and periodic background consolidation`

This file is like a two-stage notebook assistant. First, FactDeriver reads changed source pages and asks the language model to pull out standalone facts: short claims that can be remembered without rereading the whole page. It skips deleted pages and pages that are too short to be meaningful. It also works in small batches so one huge input cannot overwhelm the model. The model’s answer is treated as untrusted: the code checks that each returned fact has the expected shape before writing it to the memory store.

Second, MemoryConsolidator runs later as a periodic cleanup and summarizing job. It looks for fact memories that are old enough, not already replaced, and in the current workspace. It groups them by subject, embeds their text into numeric vectors, and clusters facts whose vectors are close together. An embedding is a machine-readable “meaning fingerprint” of text. For each cluster, it asks the model to write one combined semantic summary, then writes that summary and marks the original facts as superseded by it.

A key safety rule is that model calls happen before database write transactions where practical, so the database is not held open while waiting on the model. The file is also fail-soft: if no model is configured, it quietly skips model-based work instead of breaking the rest of memory processing.

#### Function details

##### `FactDeriver.apply`  (lines 106–115)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: This is the public entry point for turning a batch of changed pages into memory facts. It filters out deleted pages and pages with too little text, then sends the remaining pages onward in small groups.

**Data flow**: It receives a tuple of page changes. It keeps only changes that are not tombstones and whose body is long enough, checks that a model is available, splits the pages into fixed-size batches, and passes each batch to the fact-derivation step. It returns nothing, but may cause new memory facts to be written through later calls.

**Call relations**: The page-change runner calls this when it delivers changed pages to the memory extension. If there is useful work to do, this function calls FactDeriver._derive for each small group; otherwise it stops early so the larger pipeline can continue.

*Call graph*: calls 1 internal fn (_derive); 1 external calls (batched).


##### `FactDeriver._derive`  (lines 117–133)

```
async def _derive(self, model: ModelAccess, pages: tuple[PageChange, ...]) -> None
```

**Purpose**: This takes model-extracted facts and writes the acceptable ones into the memory store. It connects each extracted fact back to the page it came from, so the stored memory keeps its subject and source timing.

**Data flow**: It receives a model object and a group of page changes. It builds a lookup from page ID to page, asks FactDeriver._extract for candidate facts, drops facts that point to an unknown page or have low notability, and commits each remaining fact as a MemoryWrite. The output is no direct return value; the important change is that new fact memory items may appear in the store.

**Call relations**: FactDeriver.apply hands page groups to this function. This function relies on FactDeriver._extract to talk to the model, then hands accepted facts to the memory store by creating MemoryWrite records.

*Call graph*: calls 1 internal fn (_extract); called by 1 (apply); 1 external calls (__init__).


##### `FactDeriver._extract`  (lines 135–151)

```
async def _extract(self, model: ModelAccess, pages: tuple[PageChange, ...]) -> tuple[ExtractedFact, ...]
```

**Purpose**: This asks the language model to read a small group of pages and return durable facts in JSON form. It keeps the request bounded by trimming page bodies and setting limits on the model response.

**Data flow**: It receives a model object and page changes. It builds a compact JSON payload containing page IDs and trimmed page text, wraps that payload in a ModelRequest with instructions, sends it to the model, and passes the model’s text response to _parse_facts. It returns a tuple of validated ExtractedFact objects.

**Call relations**: FactDeriver._derive calls this when it needs candidate facts. This function is the bridge to ModelAccess.complete, then delegates cleanup and validation of the model’s reply to _parse_facts.

*Call graph*: calls 2 internal fn (complete, _parse_facts); called by 1 (_derive); 3 external calls (__init__, __init__, dumps).


##### `MemoryConsolidator.run`  (lines 181–190)

```
async def run(self) -> None
```

**Purpose**: This is the main periodic job for merging old, related facts into compact semantic summaries. It prevents memory from becoming a pile of repeated small facts by replacing clusters with one stronger summary.

**Data flow**: It starts with no input besides the consolidator’s configured store, workspace, embedder, and optional model. If no model is available, it exits. Otherwise it loads old facts, groups them by subject, skips groups that are too small, embeds the fact text, clusters similar facts, and consolidates clusters that are large enough. It returns nothing, but may write summary memories and mark old facts as superseded.

**Call relations**: A scheduler or periodic background runner calls this function. It coordinates the whole consolidation flow by calling MemoryConsolidator._aged_facts, _buckets, _embed, _clusters, and _consolidate in order.

*Call graph*: calls 5 internal fn (_aged_facts, _buckets, _clusters, _consolidate, _embed).


##### `MemoryConsolidator._aged_facts`  (lines 192–221)

```
async def _aged_facts(self) -> tuple[_AgedFact, ...]
```

**Purpose**: This reads the database for fact memories that are old enough to be candidates for consolidation. It deliberately ignores facts that have already been superseded, so completed work is not repeated.

**Data flow**: It uses the current time to compute an age cutoff, opens a database transaction, and selects a limited number of memory rows for the current workspace where the item is a fact, not superseded, and older than the cutoff. It converts each row into an _AgedFact object and returns them as a tuple.

**Call relations**: MemoryConsolidator.run calls this at the start of a consolidation pass. The returned facts become the raw material for grouping, embedding, clustering, and summarizing.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryConsolidator._buckets`  (lines 223–232)

```
def _buckets(self, facts: tuple[_AgedFact, ...]) -> tuple[tuple[str, tuple[_AgedFact, ...]], ...]
```

**Purpose**: This groups candidate facts by their subject, so only facts about the same thing are compared and merged. It also caps each subject group to a safe maximum size.

**Data flow**: It receives a tuple of aged facts. It builds groups keyed by subject, sorts each group by recency with newest first, trims oversized groups, and returns subject-and-facts pairs. It does not change the database or the facts themselves.

**Call relations**: MemoryConsolidator.run calls this after loading aged facts. The buckets it returns decide which facts are embedded and clustered together.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._embed`  (lines 234–238)

```
async def _embed(self, facts: tuple[_AgedFact, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: This turns fact text into embedding vectors, which are lists of numbers that represent meaning for similarity comparison. These vectors let the consolidator find facts that are about roughly the same idea.

**Data flow**: It receives a tuple of aged facts. It trims each fact body to a safe length, sends the texts to the embedding client, then pairs each returned vector back with the fact’s ID. It returns a dictionary from fact ID to embedding vector.

**Call relations**: MemoryConsolidator.run calls this for each subject bucket that is large enough. MemoryConsolidator._clusters then uses the returned vectors to decide which facts belong together.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._clusters`  (lines 240–259)

```
def _clusters(self, facts: tuple[_AgedFact, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_AgedFact, ...], ...]
```

**Purpose**: This groups similar facts together using their embedding vectors. It uses a simple newest-first approach: each fact joins the first existing cluster whose leading fact is similar enough, or starts a new cluster.

**Data flow**: It receives aged facts and a dictionary of embeddings. It sorts facts by recency, compares each fact’s vector with the first fact in existing clusters using _cosine, and builds clusters of related facts. It returns a tuple of fact clusters, without writing anything.

**Call relations**: MemoryConsolidator.run calls this after embeddings are available. When this function finds clusters large enough, run sends those clusters to MemoryConsolidator._consolidate.

*Call graph*: calls 1 internal fn (_cosine); called by 1 (run).


##### `MemoryConsolidator._consolidate`  (lines 261–288)

```
async def _consolidate(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> None
```

**Purpose**: This replaces one cluster of related facts with a single semantic summary. It writes the new summary and marks the original facts as superseded by that summary in the same database transaction.

**Data flow**: It receives a model and a cluster of aged facts. It asks MemoryConsolidator._summarize for a combined statement; if the summary is empty, it stops. Otherwise it creates a new ID, inserts a semantic memory item with the summary, and updates the original fact rows so their superseded_by field points to the new summary. It returns nothing, but changes the memory database.

**Call relations**: MemoryConsolidator.run calls this for each cluster that meets the minimum size. This function depends on MemoryConsolidator._summarize for the model-written text, then uses database insert and update operations to make the consolidation durable.

*Call graph*: calls 1 internal fn (_summarize); called by 1 (run); 3 external calls (insert, update, uuid4).


##### `MemoryConsolidator._summarize`  (lines 290–299)

```
async def _summarize(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> str
```

**Purpose**: This asks the language model to write one concise statement that captures several related facts. It trims the input facts and caps the returned summary so the model call stays bounded.

**Data flow**: It receives a model and a cluster of aged facts. It builds a JSON payload containing trimmed fact bodies, creates a ModelRequest with consolidation instructions, sends it to the model, strips whitespace from the response, trims it to the maximum summary length, and returns the resulting string.

**Call relations**: MemoryConsolidator._consolidate calls this before opening the write transaction. The text returned here becomes the body of the new semantic memory item.

*Call graph*: calls 1 internal fn (complete); called by 1 (_consolidate); 3 external calls (__init__, __init__, dumps).


##### `_recency`  (lines 302–303)

```
def _recency(fact: _AgedFact) -> tuple[datetime, UUID]
```

**Purpose**: This small helper gives a consistent sort key for ordering facts by when they were created, with the fact ID as a tie-breaker. It helps the code decide what is newest in a stable way.

**Data flow**: It receives one _AgedFact. It reads the fact’s created_at timestamp and ID, then returns them as a pair that sorting code can use. It does not change anything.

**Call relations**: This helper supports the consolidator’s ordering choices, especially when grouping and clustering facts newest-first.


##### `_cosine`  (lines 306–312)

```
def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: This compares two embedding vectors and returns how similar their directions are. In plain terms, it answers: do these two pieces of text point to the same meaning?

**Data flow**: It receives two tuples of numbers. It computes the length of each vector, returns 0 if either length is zero, otherwise divides their dot product by the combined lengths. The result is a similarity score, where higher means more alike.

**Call relations**: MemoryConsolidator._clusters calls this while deciding whether a fact should join an existing cluster. The cluster threshold is applied to this score.

*Call graph*: called by 1 (_clusters); 1 external calls (sqrt).


##### `_parse_facts`  (lines 315–337)

```
def _parse_facts(text: str) -> tuple[ExtractedFact, ...]
```

**Purpose**: This safely extracts validated facts from the language model’s text response. It is intentionally forgiving: bad JSON or malformed individual facts are ignored rather than stopping the whole page-change cursor.

**Data flow**: It receives raw text from the model. It looks for the first JSON object, tries to decode it, checks that it contains a facts list, validates each dictionary-like item as an ExtractedFact, skips invalid items, and returns the valid facts as a tuple. It does not write anything.

**Call relations**: FactDeriver._extract calls this immediately after ModelAccess.complete returns. This function forms the safety boundary between unpredictable model output and the stricter memory-writing code.

*Call graph*: called by 1 (_extract); 1 external calls (JSONDecoder).


### `extensions/memory/ufo_ext_memory/objects.py`

`domain_logic` · `request handling`

This file is the bridge between the memory database and the system’s general object interface. A memory item is a saved piece of information, such as a fact, preference, decision, event, or task. Other tools can find memory references through search, and this object kind is what lets those references be opened and read.

The main rule here is that memory objects are read-only. New or changed memories must go through the separate `memory_update` path. Old memories are not deleted either; if a better version replaces one, the old row points to the new one with a `superseded_by` link. This is like keeping an old index card in a filing cabinet with a note saying “see the newer card,” instead of throwing it away.

The file also protects visibility. Reads are limited to the caller’s allowed memory subjects: shared memory plus, when relevant, that conversation member’s private memory space. Listing only returns live memories, meaning ones that have not been superseded. Getting a memory by id can still return a superseded one, so an old reference does not go dead; instead, the caller can follow the replacement link.

At the bottom, `MEMORY_OBJECT` registers this behavior with the wider object system, including its readable fields, description, guidance, data shape, and read-only store.

#### Function details

##### `_require_ext`  (lines 58–61)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure a memory object request has the extension context it needs. The extension context is the bundle of memory-specific services, such as database access and workspace information.

**Data flow**: It receives a tool context. If the context contains an extension context, it returns it. If not, it stops immediately with an error, because the memory object code cannot safely read the memory store without knowing which extension and workspace it belongs to.

**Call relations**: The list and get paths call this before touching the database. It acts as a small guard at the doorway, making sure those read operations have the memory extension information they depend on.

*Call graph*: called by 2 (get, list).


##### `MemoryObjects.list`  (lines 70–109)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a page of current memory items that the caller is allowed to see. It is meant for browsing live memory records, not for semantic recall; search is still the main way to find relevant memories.

**Data flow**: It receives the caller’s tool context and a list query. It uses the extension context to open a database transaction, asks which memory subjects are visible to this caller, then selects up to 500 non-superseded memory rows in the current workspace, newest first. Each row is turned into a short object row with the memory id as its name, the first part of the body as a summary, and a few filterable fields such as subject and memory kind. Those rows are then wrapped into an object page that respects the incoming query.

**Call relations**: When the object system needs to list objects of kind `memory`, it comes here. This method relies on `_require_ext` for the memory extension context, `recall_subjects` to enforce who may see what, SQLAlchemy to read the database, `ObjectRow` to shape each visible item, and `object_page` to return the final paged response.

*Call graph*: calls 1 internal fn (_require_ext); 4 external calls (__init__, select, object_page, recall_subjects).


##### `MemoryObjects.get`  (lines 111–161)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: This opens one memory item by id and returns its full stored details, if the caller is allowed to see it. It can return both current memories and older superseded memories, so stale references can still be followed.

**Data flow**: It receives the caller’s tool context and a memory name, which is expected to be a UUID-style id. If the name is not a valid id, it returns nothing. Otherwise, it reads the matching memory row from the current workspace, limited to the caller’s visible subjects. If no row is found, it returns nothing. If a row is found, it builds a detailed memory object containing the body, subject, class, kind, confidence, source note, and date information. It also adds links when available: one to the source page the memory was created from, and one to the newer memory that superseded it.

**Call relations**: When a caller opens a memory reference, the object system uses this method. It first uses `_require_ext` and `recall_subjects` for safe, scoped database access. It uses `UUID` to reject malformed ids, SQLAlchemy to fetch the row, `MemorySpec` to describe the memory contents, and object-link types to connect the memory to its source page or replacement memory.

*Call graph*: calls 1 internal fn (_require_ext); 7 external calls (__init__, __init__, __init__, __init__, select, recall_subjects, UUID).


##### `MemoryObjects.status`  (lines 163–164)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: This reports no separate status for memory objects. Memory items are simple readable records here, not long-running jobs or resources with changing state exposed through this method.

**Data flow**: It receives the caller context and object name, but does not inspect them. It always returns nothing, meaning there is no status payload to show.

**Call relations**: The wider object interface may ask an object store for status, but this memory store has nothing extra to provide. It therefore ends the status path locally without handing work to any other helper.


##### `MemoryObjects.apply`  (lines 166–169)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemorySpec, old: MemorySpec | None) -> None
```

**Purpose**: This refuses attempts to create or update memory objects through the generic object apply operation. Memories must be written through `memory_update`, which is the controlled write path for this extension.

**Data flow**: It receives the caller context, object name, proposed memory spec, and optional previous spec. Instead of saving anything, it raises a clear “verb not supported” error explaining that memories are recorded through `memory_update`, not applied directly.

**Call relations**: If the generic object system tries to apply changes to a `memory` object, this method blocks that route. It hands back a `VerbNotSupported` error so callers know to use the proper memory-writing tool instead.

*Call graph*: 1 external calls (__init__).


##### `MemoryObjects.delete`  (lines 171–172)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: This refuses attempts to delete memory objects. The memory system keeps history by superseding old items rather than removing them directly.

**Data flow**: It receives the caller context and object name. It does not look up or remove anything. It raises a “verb not supported” error explaining that memories cannot be deleted and that consolidation replaces old items instead.

**Call relations**: If the generic object system tries to delete a `memory` object, this method stops the request. It returns a clear refusal through `VerbNotSupported`, preserving the extension’s rule that memory endings happen by supersession, not deletion.

*Call graph*: 1 external calls (__init__).


### Knowledge graph extraction
The knowledge-graph extension registers graph hooks and tools, then stores extracted entities and typed links for contextual graph queries.

### `extensions/knowledge_graph/ufo_ext_knowledge_graph/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder usually needs an `__init__.py` file to be treated as an importable package. Think of it like a label on a drawer: the label does not contain the tools, but it tells Python that the drawer belongs to the system and can be opened by name. Without this file, depending on the Python setup and import style, code elsewhere might not be able to import modules from `ufo_ext_knowledge_graph` reliably. Because the file is empty, it does not run startup code, expose shortcuts, or change any settings. Its value is structural: it makes the knowledge graph extension package visible and organized for the rest of the application.


### `extensions/knowledge_graph/ufo_ext_knowledge_graph/manifest.py`

`orchestration` · `startup registration, then active during prompt submission, tool calls, and page-change processing`

The knowledge graph turns information from source pages into named things, such as people or companies, and relationships between them. This file declares how the rest of the system can use that graph. Without it, the extension would not be visible to the host application: the model could not call the graph search tool, prompt messages would not get relevant graph context, and changed pages would not be converted into graph facts.

There are three main jobs here. First, it defines the input shape for the `graph_search` tool, including the starting entity, how many relationship steps to follow, and optional relationship-type filters. Second, it provides the tool handler that reads from `GraphStore`, walks the graph, and returns readable lines with source-page citations. Third, it registers two hooks. A hook is code the host runs at a specific moment. One hook runs when a user prompt is submitted and quietly injects relevant graph facts into the model’s context. It is deliberately best-effort: if it is slow or fails, the user’s turn still continues. The other hook runs after page changes and asks `GraphExtractor` to update the graph from those changed pages.

The `manifest()` function gathers all of this into a `Manifest`, which is like a sign-up sheet telling the host, “Here are my tools and here is when to call me.”

#### Function details

##### `graph_search_handler`  (lines 71–88)

```
async def graph_search_handler(ctx: ToolContext, args: GraphSearchInput) -> ToolResult
```

**Purpose**: This is the implementation of the `graph_search` tool. Given a named entity, it looks outward through the knowledge graph and returns the relationships it finds in a form the model or user can read.

**Data flow**: It receives a tool context, which includes extension access such as the current transaction and workspace, plus the user’s requested search arguments. It checks that extension context is present, converts any requested relationship-type filters into the graph’s approved internal names, opens a `GraphStore`, and asks it to traverse from the requested entity for the requested number of hops. The resulting subgraph is turned into text lines. If nothing is found, it returns a tool result saying so; otherwise it returns a heading followed by the discovered relationship lines.

**Call relations**: The host calls this when the model chooses the `graph_search` tool registered by `manifest`. This function relies on helper code from the graph store layer to identify which graph subjects belong to the current audience member, validate edge types, traverse the graph, and render the result. It then wraps the readable answer in tool response objects so the host can pass it back to the model.

*Call graph*: 6 external calls (__init__, __init__, __init__, graph_subjects, render_subgraph, to_edge_type).


##### `graph_context_hook`  (lines 91–110)

```
async def graph_context_hook(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook adds relevant graph relationships to a user prompt before the model sees it. It helps the model answer with graph facts even when it did not explicitly call the graph search tool.

**Data flow**: It receives a hook context and first checks whether the event payload is actually a user prompt submission. If not, it does nothing. For a real prompt, it starts a short timeout, opens a `GraphStore`, and asks for graph context related to the prompt text and the current audience member. It converts the returned subgraph into readable lines. If there are lines, it returns injected context prefixed with a clear label; if there are no lines, or if anything goes wrong, it returns nothing.

**Call relations**: The host calls this because `manifest` registers it for the `user_prompt_submit` event. It sits in front of the model’s response generation, but it is intentionally cautious: because prompt submission is a gating moment, the hook catches failures and uses a timeout so graph trouble does not block the conversation. It hands successful graph text back through `InjectContext`, which the host can add to the model’s context.

*Call graph*: 5 external calls (__init__, __init__, timeout, graph_subjects, render_subgraph).


##### `extract_graph`  (lines 113–125)

```
async def extract_graph(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook updates the knowledge graph when source pages change. It turns delivered page changes into graph nodes and typed relationships, but does that after the write path rather than during the page write itself.

**Data flow**: It receives a hook context and checks whether the payload is a batch of page changes. If the payload is unrelated, it returns without doing anything. For a page-change batch, it creates a `GraphExtractor` using the current transaction, workspace, and model access, then asks it to apply the batch’s changes. The function does not return new content; its effect is updating the graph data behind the scenes.

**Call relations**: The host calls this because `manifest` registers it for the `page_change` event. The core page-change runner owns the larger loop and cursor, meaning it decides which changed pages are delivered and when. This function is the extension’s per-batch worker: it hands the changed pages to `GraphExtractor`, which performs the actual extraction work.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 128–152)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the extension to the host application. It names the extension, declares the `graph_search` tool, and registers the two moments when the host should call the extension’s hooks.

**Data flow**: It takes no input. It builds a `Manifest` object containing the extension name and version, one `ToolDef` for graph searching, and two `HookSpec` entries: one for user prompt submission and one for page changes. The completed manifest is returned to the host so the host can discover and wire up the extension.

**Call relations**: This is typically called when extensions are loaded at startup. The objects it returns are what connect later runtime events to the functions in this file: tool calls are routed to `graph_search_handler`, prompt submissions are routed to `graph_context_hook`, and page-change batches are routed to `extract_graph`.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/knowledge_graph/ufo_ext_knowledge_graph/store.py`

`domain_logic` · `page indexing and query handling`

This file is the heart of the knowledge-graph extension. It keeps two database tables: one for graph entities, such as people, companies, topics, or page anchors, and one for graph edges, which are named relationships between those entities. Without it, pages could still exist as text, but the system would not have a structured map of “who is connected to what.”

The write side is `GraphExtractor`. When a page changes, it checks whether the page content has already been processed. If not, it parses simple markdown patterns like `[[Acme]]`, `[[works_at::Acme]]`, `@sam`, `#ai`, and URLs. These become graph edges without calling an AI model. If a model is available, it can also read relationships from ordinary prose, but that extra step is optional. The deterministic parsing always runs first.

Entities are created as stable database records. A name mentioned before it has its own page becomes a “stub,” like a placeholder card in an address book. If a page later defines that entity, the placeholder is filled in. Edges are stamped with the page digest they came from, so unchanged pages are skipped and old edges are removed when content changes.

The read side is `GraphStore`. It finds matching entities and expands outward through nearby edges for a small number of steps. This produces a compact subgraph that other tools can render or use as context.

#### Function details

##### `to_edge_type`  (lines 160–165)

```
def to_edge_type(raw: str) -> EdgeType
```

**Purpose**: This function checks that a relationship name is one of the graph’s allowed edge types. It prevents misspelled or invented relationship types from being saved or used silently.

**Data flow**: It receives a raw string such as `works_at`. It compares that string with the fixed list of accepted edge types. If it is valid, the same value comes out as an approved edge type; if not, the function raises an error.

**Call relations**: Both model-extracted relationships and database writes pass through this gate. `GraphExtractor._tier_b` uses it to reject bad model output, and `GraphExtractor._record_edge` uses it before persisting an edge.

*Call graph*: called by 2 (_record_edge, _tier_b); 2 external calls (__init__, cast).


##### `normalize_name`  (lines 192–195)

```
def normalize_name(name: str) -> str
```

**Purpose**: This function turns a human-written name into a consistent lookup key. It makes names easier to match even if users vary capitalization or spacing.

**Data flow**: It receives a name string. It collapses repeated whitespace, trims the ends, and lowercases the result. The output is the cleaned name used for matching, while the original display name can still be stored separately.

**Call relations**: It is used wherever the graph needs to decide whether two references point to the same entity. The parser uses it while deduplicating references, the extractor uses it when creating entity IDs, and the store uses it when resolving queries or scanning text for known entities.

*Call graph*: called by 4 (_upsert_entity, _resolve, _seed_from_text, record); 1 external calls (sub).


##### `graph_subjects`  (lines 198–203)

```
def graph_subjects(member_id: UUID | None) -> frozenset[str]
```

**Purpose**: This function decides which graph areas a user should search. It includes the shared graph for everyone, and adds a member-specific graph when there is a linked member.

**Data flow**: It receives either a member ID or nothing. If there is no member, it returns only the shared subject. If there is a member, it returns both the member’s subject and the shared subject.

**Call relations**: Other parts of the extension can call this before reading the graph, so `GraphStore` gets the correct subject set for a query. It relies on the SDK helper that turns a member ID into the member’s subject name.

*Call graph*: 1 external calls (member_subject).


##### `parse_page`  (lines 248–284)

```
def parse_page(body: str) -> ParsedPage
```

**Purpose**: This function reads a page’s markdown and extracts graph references without using an AI model. It finds the page title and the obvious links, mentions, tags, and URLs that should become graph edges.

**Data flow**: It receives the page body as text. It looks for the first heading to use as the page’s anchor title, scans wikilinks first, then scans the remaining text for mentions, tags, and URLs. It returns a `ParsedPage` containing the title and a deduplicated list of references.

**Call relations**: `GraphExtractor._apply` calls this before materializing a changed page. The references it produces are later turned into entity rows and edge rows by `GraphExtractor._materialize`.

*Call graph*: called by 1 (_apply); 1 external calls (__init__).


##### `parse_page.record`  (lines 260–267)

```
def record(edge_type: str, name: str, entity_type: str) -> None
```

**Purpose**: This small inner helper adds one parsed reference to the page result, while avoiding duplicates. It keeps repeated links in the same page from creating repeated graph edges.

**Data flow**: It receives an edge type, a target name, and an entity type. It trims the name, normalizes it for comparison, checks whether that combination was already seen, and if not adds a new `Ref` to the list being built.

**Call relations**: It is used only inside `parse_page`. Each markdown pattern found by `parse_page` passes through this helper before becoming part of the parsed output.

*Call graph*: calls 1 internal fn (normalize_name); 1 external calls (__init__).


##### `render_subgraph`  (lines 287–302)

```
def render_subgraph(subgraph: Subgraph) -> tuple[str, ...]
```

**Purpose**: This function turns a graph result into readable text lines. It is useful when a query tool or hook needs to show relationships in a simple citation-friendly form.

**Data flow**: It receives a `Subgraph` containing nodes and edges. It matches each edge to its source and target nodes, formats a line like `A -works_at-> B`, marks targets that are still stubs, and includes the source page ID. It returns the formatted lines.

**Call relations**: It sits after graph traversal. Once `GraphStore` has produced a subgraph, callers can use this function to present the result to people or to another system component.


##### `GraphExtractor.apply`  (lines 320–322)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: This is the batch entry point for writing graph updates from page changes. It applies each changed page one by one.

**Data flow**: It receives a tuple of page changes. For each change, it passes that single item to the lower-level `_apply` method. It returns nothing, but the database may be updated as each change is processed.

**Call relations**: The page-change runner calls this method when it has delivered a batch. `apply` then delegates the real decision-making to `GraphExtractor._apply` for each page.

*Call graph*: calls 1 internal fn (_apply).


##### `GraphExtractor._apply`  (lines 324–338)

```
async def _apply(self, change: PageChange) -> None
```

**Purpose**: This function decides what to do with one changed page. It soft-deletes edges for deleted pages, skips unchanged content, and processes new or changed content.

**Data flow**: It receives a single page change. If the page is tombstoned, it marks all edges sourced from that page as tombstoned in the database. Otherwise it checks whether the same digest was already extracted; if not, it parses the page and materializes the graph rows.

**Call relations**: `GraphExtractor.apply` calls this for each delivered change. It calls `_already_extracted` to avoid repeat work, `parse_page` to read deterministic references, and `_materialize` to write entities and edges.

*Call graph*: calls 3 internal fn (_already_extracted, _materialize, parse_page); called by 1 (apply); 1 external calls (update).


##### `GraphExtractor._already_extracted`  (lines 340–354)

```
async def _already_extracted(self, change: PageChange) -> bool
```

**Purpose**: This function checks whether a page version has already been turned into graph edges. It saves work when the same page change is replayed or delivered again.

**Data flow**: It receives a page change. It queries the edge table for a non-tombstoned edge from the same workspace, page, and digest. It returns true if such an edge exists, otherwise false.

**Call relations**: `GraphExtractor._apply` calls this before doing parsing and writes. If it returns true, extraction stops early for that page.

*Call graph*: called by 1 (_apply); 1 external calls (select).


##### `GraphExtractor._materialize`  (lines 356–383)

```
async def _materialize(self, change: PageChange, parsed: ParsedPage) -> None
```

**Purpose**: This function turns a parsed page into actual graph database rows. It creates or updates the page’s anchor entity, creates placeholder entities for references, writes edges, and removes stale edges from older versions of the page.

**Data flow**: It receives the page change and the deterministic parse result. If a model is available, it first asks `_tier_b` for extra prose-based relations. Then, inside a database transaction, it upserts the anchor entity, upserts each target entity, records deterministic and model-derived edges, and deletes edges from older digests of the same page.

**Call relations**: `GraphExtractor._apply` calls this after confirming the page needs extraction. It coordinates `_tier_b`, `_upsert_entity`, and `_record_edge`, keeping the model call outside the database transaction so the database is not held open while waiting on the model provider.

*Call graph*: calls 3 internal fn (_record_edge, _tier_b, _upsert_entity); called by 1 (_apply); 1 external calls (delete).


##### `GraphExtractor._tier_b`  (lines 385–423)

```
async def _tier_b(self, model: ModelAccess, body: str) -> tuple[ExtractedRelation, ...]
```

**Purpose**: This optional function asks the configured AI model to extract typed relationships from ordinary prose. It adds extra graph edges beyond the deterministic markdown links, but safely returns no relations if the model response is missing or malformed.

**Data flow**: It receives model access and page text. It builds a constrained model request that forces a tool-shaped response, using only a bounded amount of page text and a fixed edge vocabulary. It validates the returned relations, rejects unknown edge types, drops empty targets, and returns a tuple of accepted relations.

**Call relations**: `GraphExtractor._materialize` calls this only when a model is configured. It calls the model through `ModelAccess.turn`, then uses `to_edge_type` as the safety check before the extracted relations are written by `_materialize`.

*Call graph*: calls 2 internal fn (turn, to_edge_type); called by 1 (_materialize); 2 external calls (__init__, __init__).


##### `GraphExtractor._upsert_entity`  (lines 425–464)

```
async def _upsert_entity(self, connection: AsyncConnection, subject: str, name: str, entity_type: str, fill: bool) -> UUID
```

**Purpose**: This function creates an entity if it does not exist, or updates it when a real page defines a previously stubbed entity. It gives each entity a stable ID based on its workspace, subject, type, and normalized name.

**Data flow**: It receives a database connection, subject, display name, entity type, and a `fill` flag. It normalizes the name, derives a repeatable UUID, and inserts the entity. If `fill` is true, an existing row is updated and marked as not a stub; if false, an existing filled row is left alone. It returns the entity ID.

**Call relations**: `GraphExtractor._materialize` calls this for the page anchor and for every referenced target. The returned IDs are then handed to `_record_edge` so relationships can connect the right nodes.

*Call graph*: calls 1 internal fn (normalize_name); called by 1 (_materialize); 2 external calls (execute, uuid5).


##### `GraphExtractor._record_edge`  (lines 466–513)

```
async def _record_edge(self, connection: AsyncConnection, change: PageChange, from_entity: UUID, to_entity: UUID, raw_edge_type: str, confidence: float=DETERMINISTIC_CONFIDENCE) -> None
```

**Purpose**: This function writes one relationship between two entities. It validates the edge type, gives the edge a stable ID, and records where the relationship came from.

**Data flow**: It receives a database connection, the page change, source entity ID, target entity ID, raw edge type, and confidence score. It validates the edge type, derives a repeatable UUID from the edge details, and inserts or updates the edge row with the page ID, digest, confidence, and tombstone status. It returns nothing, but the edge table is changed.

**Call relations**: `GraphExtractor._materialize` calls this for each deterministic reference and each accepted model relation. It uses `to_edge_type` as the final guard before anything reaches the database.

*Call graph*: calls 1 internal fn (to_edge_type); called by 1 (_materialize); 2 external calls (execute, uuid5).


##### `GraphStore.traverse`  (lines 525–533)

```
async def traverse(self, query: str, subjects: frozenset[str], hops: int, edge_types: frozenset[str]) -> Subgraph
```

**Purpose**: This is the main search-style read method. It starts from an exact entity name and returns a nearby slice of the graph.

**Data flow**: It receives a query string, allowed subjects, hop count, and optional edge-type filter. It resolves the query to seed entity IDs, expands outward through graph edges, and returns a `Subgraph` of visited nodes and edges.

**Call relations**: Query tools call this when a user asks for graph relationships around a named thing. It delegates name lookup to `_resolve` and graph walking to `_expand`.

*Call graph*: calls 2 internal fn (_expand, _resolve).


##### `GraphStore.context_for`  (lines 535–539)

```
async def context_for(self, text: str, subjects: frozenset[str], hops: int) -> Subgraph
```

**Purpose**: This method finds graph context relevant to a block of text. Instead of requiring an exact query, it seeds traversal from known entity names that appear in the text.

**Data flow**: It receives text, allowed subjects, and a hop count. It finds matching entity IDs inside the text, expands outward from those seeds, and returns the resulting `Subgraph`.

**Call relations**: Inbound hooks can call this when they want background graph context for a conversation turn. It delegates text-based seeding to `_seed_from_text` and graph walking to `_expand`.

*Call graph*: calls 2 internal fn (_expand, _seed_from_text).


##### `GraphStore._resolve`  (lines 541–555)

```
async def _resolve(self, query: str, subjects: frozenset[str]) -> frozenset[UUID]
```

**Purpose**: This function finds graph entities whose normalized name exactly matches a query. It is the starting point for explicit graph searches.

**Data flow**: It receives a query and subject set. It normalizes the query, returns no IDs if the query or subjects are empty, otherwise queries the entity table for matching rows in the current workspace and subjects. It returns the matching entity IDs.

**Call relations**: `GraphStore.traverse` calls this before expanding the graph. The IDs it returns become the seed nodes passed into `_expand`.

*Call graph*: calls 1 internal fn (normalize_name); called by 1 (traverse); 1 external calls (select).


##### `GraphStore._seed_from_text`  (lines 557–574)

```
async def _seed_from_text(self, text: str, subjects: frozenset[str]) -> frozenset[UUID]
```

**Purpose**: This function finds known entities whose names appear inside a larger text. It gives the system a way to attach graph context to ordinary conversation text.

**Data flow**: It receives text and subject set. It normalizes the text, loads a bounded set of recent candidate entities from the database, and checks whether each normalized entity name appears as a whole phrase inside the text. It returns the IDs that match.

**Call relations**: `GraphStore.context_for` calls this to choose starting points for contextual graph lookup. The matched IDs are then passed to `_expand`.

*Call graph*: calls 1 internal fn (normalize_name); called by 1 (context_for); 1 external calls (select).


##### `GraphStore._expand`  (lines 576–592)

```
async def _expand(self, seeds: frozenset[UUID], subjects: frozenset[str], hops: int, edge_types: frozenset[str]) -> Subgraph
```

**Purpose**: This function walks outward from seed nodes through the graph for a limited number of steps. It keeps the result small so graph queries do not grow without control.

**Data flow**: It receives seed IDs, allowed subjects, hop count, and optional edge-type filter. It repeatedly asks `_hop` for the next ring of connected nodes, stopping when there is no frontier left or the edge cap is reached. Finally it loads node details with `_nodes` and returns a `Subgraph`.

**Call relations**: Both `GraphStore.traverse` and `GraphStore.context_for` call this after choosing seed nodes. It coordinates the repeated edge lookup in `_hop` and the final node lookup in `_nodes`.

*Call graph*: calls 2 internal fn (_hop, _nodes); called by 2 (context_for, traverse); 1 external calls (__init__).


##### `GraphStore._hop`  (lines 594–639)

```
async def _hop(self, frontier: set[UUID], subjects: frozenset[str], edge_types: frozenset[str], edges: dict[UUID, TraversedEdge], visited: set[UUID]) -> set[UUID]
```

**Purpose**: This function performs one step of graph walking. It finds edges touching the current frontier and discovers the next set of neighboring nodes.

**Data flow**: It receives the current frontier, subject set, optional edge-type filter, the edge collection built so far, and the visited node set. It queries non-tombstoned edges touching the frontier, records each edge, adds newly found endpoints to `visited`, and returns the next frontier.

**Call relations**: `GraphStore._expand` calls this once per hop. `_hop` updates the shared edge and visited collections so `_expand` can continue walking or stop with the accumulated result.

*Call graph*: called by 1 (_expand); 3 external calls (__init__, or_, select).


##### `GraphStore._nodes`  (lines 641–663)

```
async def _nodes(self, ids: set[UUID]) -> tuple[EntityNode, ...]
```

**Purpose**: This function loads display details for a set of entity IDs. It turns raw IDs discovered during traversal into readable graph nodes.

**Data flow**: It receives a set of entity IDs. If the set is empty, it returns an empty tuple. Otherwise it queries the entity table for each ID in the current workspace and returns `EntityNode` objects with name, type, and stub status.

**Call relations**: `GraphStore._expand` calls this after the edge walk is complete. The returned nodes are paired with the traversed edges to form the final `Subgraph`.

*Call graph*: called by 1 (_expand); 2 external calls (__init__, select).


### Page alerts
The page-alerts extension package and alert logic let conversations watch synced pages for matching changes and fire notifications.

### `extensions/page_alerts/ufo_ext_page_alerts/__init__.py`

`other` · `startup/import time`

This is the smallest possible starting point for the page alerts extension. In Python, an `__init__.py` file tells the language that a folder should be treated as an importable package. That matters because other parts of the project can then refer to this extension by its package name and load code from inside it. Think of it like a sign on a folder that says, “this folder belongs together and can be opened as one module.” This file does not define any functions, classes, or behavior by itself. Its main value is organizational: it gives the extension a clear package boundary and a brief description. Without it, depending on the Python version and packaging setup, the extension might be harder or impossible to import in the expected way.


### `extensions/page_alerts/ufo_ext_page_alerts/alerts.py`

`domain_logic` · `tool calls and page-change hook handling`

This file solves a simple problem: a person may not want to keep checking every synced page by hand, but they do want to know when something relevant changes. It acts like a saved search with a messenger attached. A user creates a “watch” from a conversation by naming a topic. The watch is stored with the topic, the conversation where it was requested, and the agent that should later speak there.

When synced pages change, the page-change hook reads all saved watches. For each changed page and each watch, it asks the configured language model a tightly limited yes-or-no question: does this page concern the watch topic? Only a small excerpt of the page is sent, so the check is bounded and predictable. If the answer contains “MATCH,” the extension invokes a new turn in the original conversation, asking the agent to explain the change and why it matters.

The file also includes tools for listing current watches and canceling one by name. Watch names are normalized into simple lowercase “slugs,” so names can safely become storage keys. The alert invocation uses an idempotency key based on the watch and page digest, which helps prevent duplicate alerts if the same change batch is replayed.

#### Function details

##### `_slug`  (lines 38–42)

```
def _slug(raw: str) -> str
```

**Purpose**: Turns a user-provided watch name into a safe, simple storage name. It makes the name lowercase, replaces runs of non-letter-or-number characters with dashes, and rejects names that contain no usable letters or digits.

**Data flow**: It receives raw text from a user or topic. It cleans that text into a lowercase dash-separated name. It returns the cleaned name, or raises an error if the result would be empty.

**Call relations**: When a watch is created, watch_pages uses this to decide the saved watch key. When a watch is canceled, cancel_page_watch uses the same cleanup so the user can refer to the watch by a human-friendly name and still reach the stored entry.

*Call graph*: called by 2 (cancel_page_watch, watch_pages); 1 external calls (sub).


##### `watch_pages`  (lines 45–66)

```
async def watch_pages(ctx: ToolContext, args: WatchPagesInput) -> ToolResult
```

**Purpose**: Creates a new page watch from inside a conversation. A user gives a topic, optionally a name, and this function records that future matching page changes should alert the current conversation.

**Data flow**: It receives the tool context, which includes the current conversation, current agent, and extension storage, plus the requested topic and optional name. It checks that extension context is available, creates a safe watch name, stores the topic and conversation details under that name, then returns a short confirmation message to the user.

**Call relations**: This is called when the user asks the tool to start watching pages. It relies on _slug to make a safe watch key, writes the watch into the extension store, and returns its answer as tool text content.

*Call graph*: calls 1 internal fn (_slug); 2 external calls (__init__, __init__).


##### `list_page_watches`  (lines 69–79)

```
async def list_page_watches(ctx: ToolContext, args: ListPageWatchesInput) -> ToolResult
```

**Purpose**: Shows the user the page watches that are currently saved. It helps people remember what topics are being monitored and under which names.

**Data flow**: It receives the tool context and reads all stored entries whose keys start with the watch prefix. If none exist, it returns “No page watches.” Otherwise it extracts each watch’s topic and returns a plain text list of watch names and topics.

**Call relations**: This is called when a user asks to see existing watches. It uses _watch_fields to safely read each stored watch record before formatting the response.

*Call graph*: calls 1 internal fn (_watch_fields); 2 external calls (__init__, __init__).


##### `cancel_page_watch`  (lines 82–89)

```
async def cancel_page_watch(ctx: ToolContext, args: CancelPageWatchInput) -> ToolResult
```

**Purpose**: Deletes an existing page watch by name. This stops future page changes from producing alerts for that watch.

**Data flow**: It receives the tool context and the requested watch name. It converts the name into the same safe form used when the watch was created, checks whether that stored watch exists, deletes it if found, and returns a confirmation message. If no matching watch exists, it raises a clear error.

**Call relations**: This is called when a user asks to stop watching a topic. It uses _slug to find the correct storage key, then removes that key from the extension store.

*Call graph*: calls 1 internal fn (_slug); 2 external calls (__init__, __init__).


##### `on_page_change`  (lines 92–146)

```
async def on_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Runs when synced pages change and decides whether any saved watch should produce an alert. It compares each changed page with each watch topic and, on a match, starts an alerting turn in the original conversation.

**Data flow**: It receives a hook context containing a page-change batch, extension storage, a model, and the ability to invoke an agent turn. It ignores deleted pages, clips each changed page body to a limited excerpt, asks the model whether the excerpt matches each watch topic, and for positive matches invokes the saved conversation and agent with an alert prompt. It returns no visible hook result, but it may cause alert messages to be generated elsewhere.

**Call relations**: The system calls this after the source page-sync pipeline reports changes. It reads watches created by watch_pages, uses _watch_fields to unpack their saved data, asks the configured model for a MATCH or NO verdict, and then hands matching cases to the extension invocation path so the alert is delivered through the same conversation surface where the watch was created.

*Call graph*: calls 1 internal fn (_watch_fields); 3 external calls (__init__, __init__, UUID).


##### `_watch_fields`  (lines 149–154)

```
def _watch_fields(key: str, value: object) -> tuple[str, str, str]
```

**Purpose**: Checks and unpacks a stored watch record. It makes sure the saved value has the expected topic, conversation ID, and agent ID fields before other code trusts it.

**Data flow**: It receives a storage key and the stored value found under that key. If the value has the expected shape, it returns the topic, conversation ID, and agent ID as text. If the stored data is malformed, it raises an error naming the broken watch.

**Call relations**: list_page_watches uses this before showing saved watches to a user. on_page_change uses it before deciding where to send an alert. In both cases it acts as a small safety gate between raw stored data and the rest of the alert flow.

*Call graph*: called by 2 (list_page_watches, on_page_change).
