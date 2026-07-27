# Derived indexing, memory, graph extraction, and page alerts  `stage-15`

This stage runs behind the scenes after pages or saved artifacts change. Its job is to turn raw text into things the system can find, remember, and act on later. The core indexing file defines the common shape of text “chunks,” search results, and index backends. The OpenAI embedding extension turns text into numeric meaning fingerprints, splitting large text safely. The default index stores chunks and embeddings locally, while the Turbopuffer extension can store and search them in an external search service.

The memory extension builds on this. Its manifest wires in tools, hooks, jobs, and screens. Its store saves memories, searches them, and keeps indexes current. The condenser turns changed pages into durable facts and later merges related facts into clearer summaries. The objects file lets memories be opened and inspected read-only, while events gives shared names for recall activity.

The knowledge graph extension adds another recall path: its manifest connects hooks and lookup tools, and its store extracts entities and relationships from pages. Page alerts let users watch topics, then send a follow-up conversation message when a changed page matches.

## Files in this stage

### Indexing substrate
Shared chunking rules, embedding generation, and pluggable search backends turn changed text into searchable vectors and keyword records.

### `core/src/ufo/indexing.py`

`domain_logic` · `indexing or content update`

Search works better when long text is broken into smaller, meaningful pieces. This file provides that shared breaking-up step, plus the small data shapes and contracts used to store and retrieve those pieces. Think of it like a library card system: the text is cut into cards, each card gets a stable ID, the cards are filed in an index, and old cards are removed when the source text changes.

The file does not talk directly to a database or a search engine. Instead, it defines Protocols, which are promises about what another object must be able to do. `IndexBackend` promises it can save chunks, delete or prune them, and search them by words or by vector similarity. Vector similarity means comparing numeric representations of text so related meanings can match even when the exact words differ. `EmbedClient` promises it can turn text into those numeric representations, called embeddings.

`TextChunker` is the main local worker. It splits text by paragraphs, lines, sentences, punctuation, and finally whitespace if needed. It aims for readable chunk sizes, adds a little overlap so context is not lost between neighboring chunks, and caps very long chunks by character count. `chunk_embed_upsert` ties the workflow together: chunk the text, embed the chunks, save them, then prune anything from the same owner that no longer belongs. That final prune is important because edited or emptied text should not leave stale search results behind.

#### Function details

##### `IndexBackend.upsert`  (lines 68–68)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This is the promised method an index backend must provide to save chunks into the search index. “Upsert” means save this chunk whether it is new or already exists, replacing the old version if needed.

**Data flow**: It receives a group of `Chunk` objects, including their text and embeddings. The concrete backend is expected to write those chunks into its storage system. Nothing is returned; the visible result is that the index now contains those chunks.

**Call relations**: `chunk_embed_upsert` calls this after the text has been split and embedded. The core code does not know how storage works; it simply hands completed chunks to whatever backend implements this promise.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.delete`  (lines 70–70)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This is the promised method an index backend must provide to remove all indexed chunks for one source item. It is used when a whole indexed owner, such as a page or memory item, should disappear from search.

**Data flow**: It receives an `IndexScope`, which identifies the owner kind and owner ID. The concrete backend removes matching chunks from its storage. Nothing is returned; the index is changed by deletion.

**Call relations**: This file only defines the contract. Other parts of the system can call it when they need to fully remove an owner’s indexed text.


##### `IndexBackend.prune`  (lines 72–72)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This is the promised method an index backend must provide to remove stale chunks while keeping a specific current set. It matters after an edit, because old chunks from the previous text should not keep appearing in search.

**Data flow**: It receives an `IndexScope` naming the owner and a set of chunk digests to keep. The backend deletes indexed chunks for that owner whose digests are not in the keep set. Nothing is returned; the index is cleaned up.

**Call relations**: `chunk_embed_upsert` calls this every time after preparing the current chunks. If the new body is empty, the keep set is empty too, so pruning removes all old chunks for that owner.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.lexical`  (lines 74–76)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the promised method an index backend must provide for word-based search. It looks for chunks whose stored text matches the query words, usually like a traditional search box.

**Data flow**: It receives a query string, a set of allowed subjects, an owner kind, and a maximum number of results. The backend searches its stored text under those filters and returns `Hit` objects, each describing a matching chunk and its score.

**Call relations**: This file defines the shared shape of the request and response. Retrieval code elsewhere can call this on a backend without needing to know which database or search engine is underneath.


##### `IndexBackend.vector`  (lines 78–80)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the promised method an index backend must provide for meaning-based search using embeddings. It finds chunks whose numeric text representation is close to the query embedding.

**Data flow**: It receives an embedding, a set of allowed subjects, an owner kind, and a result limit. The backend compares the embedding with stored chunk embeddings, applies the filters, and returns scored `Hit` objects.

**Call relations**: This is the retrieval counterpart to embedding chunks during indexing. Search orchestration elsewhere can ask the backend for semantically similar chunks through this common method.


##### `EmbedClient.embed`  (lines 84–84)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This is the promised method an embedding client must provide to turn text into numeric vectors. Those vectors let the system compare pieces of text by meaning, not only by exact words.

**Data flow**: It receives a tuple of text strings. The concrete embedding client sends or computes them wherever appropriate and returns one numeric vector for each input text, in the same order.

**Call relations**: `chunk_embed_upsert` calls this after chunking text and before saving chunks. The returned vectors are attached to the chunks before they are handed to `IndexBackend.upsert`.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `chunk_embed_upsert`  (lines 87–112)

```
async def chunk_embed_upsert(index: IndexBackend, embed: EmbedClient, chunker: 'TextChunker', owner_kind: str, owner_id: str, subject: str, body: str) -> None
```

**Purpose**: This function performs the shared indexing update for one body of text. It chunks the body, gets embeddings for each chunk, saves the finished chunks, and removes stale chunks from the same owner.

**Data flow**: It receives an index backend, an embedding client, a chunker, owner details, a subject, and the body text. First it asks the chunker to create chunks. If there are chunks, it embeds their text, copies each chunk with its embedding filled in, and upserts them into the index. Finally it prunes the owner’s indexed chunks so only the newly produced chunk digests remain.

**Call relations**: This is the file’s main workflow function. It calls `EmbedClient.embed` to get vectors, `IndexBackend.upsert` to save current chunks, and `IndexBackend.prune` to remove old ones, using `IndexScope` to name the owner being cleaned.

*Call graph*: calls 3 internal fn (embed, prune, upsert); 2 external calls (__init__, replace).


##### `TextChunker.chunk`  (lines 121–132)

```
def chunk(self, text: str, owner_kind: str, owner_id: str, subject: str) -> tuple[Chunk, ...]
```

**Purpose**: This method turns one text body into ordered `Chunk` objects ready for embedding. It adds ownership information and a stable digest so each chunk can be identified later.

**Data flow**: It receives raw text plus owner kind, owner ID, and subject. It asks `_slices` for clean text pieces, numbers those pieces in order, computes a digest for each one, and returns the resulting chunks. The chunks do not yet have embeddings.

**Call relations**: `chunk_embed_upsert` uses this as the first step of indexing. Inside the method, `_slices` decides how to split the text, `_digest` gives each piece a stable ID, and `Chunk` objects carry the result forward.

*Call graph*: calls 2 internal fn (_digest, _slices); 1 external calls (__init__).


##### `TextChunker._slices`  (lines 134–142)

```
def _slices(self, text: str) -> list[str]
```

**Purpose**: This method decides the overall splitting plan for raw text. It turns empty text into no chunks, short text into one or more character-capped pieces, and long text into readable overlapping sections.

**Data flow**: It receives raw text. It checks whether the text is blank, estimates its word count, and then either caps it directly or splits it recursively, merges small pieces, adds overlap, and caps final pieces by character length. It returns a list of text slices.

**Call relations**: `TextChunker.chunk` calls this before wrapping slices into `Chunk` objects. `_slices` coordinates the lower-level helpers: `_count_words`, `_recursive_split`, `_greedy_merge`, `_apply_overlap`, and `_cap_by_chars`.

*Call graph*: calls 5 internal fn (_apply_overlap, _cap_by_chars, _count_words, _greedy_merge, _recursive_split); called by 1 (chunk).


##### `TextChunker._count_words`  (lines 145–151)

```
def _count_words(text: str) -> int
```

**Purpose**: This helper estimates how large a piece of text is. It treats languages without spaces between words, such as Chinese, Japanese, and Korean text, differently from space-separated text.

**Data flow**: It receives text and removes whitespace to count non-blank characters. If enough of the text is CJK characters, it uses character count as the size estimate. Otherwise it counts runs of non-whitespace text like ordinary words. It returns that number.

**Call relations**: `_slices`, `_recursive_split`, and `_greedy_merge` use this to decide whether text is too large, small enough, or safe to combine. It is the chunker’s measuring tape.

*Call graph*: called by 3 (_greedy_merge, _recursive_split, _slices); 1 external calls (sub).


##### `TextChunker._cap_by_chars`  (lines 153–165)

```
def _cap_by_chars(self, text: str) -> list[str]
```

**Purpose**: This helper enforces a hard maximum character size for chunks. It is a safety net for text that is too long even after word-based splitting.

**Data flow**: It receives one text piece. If it fits within the maximum character length, it returns it as a one-item list. If it is too long, it cuts it into overlapping character windows so no piece exceeds the cap, then returns the non-empty pieces.

**Call relations**: `_slices` calls this both for short text and after the full chunking process. It ensures downstream embedding and indexing code never receives extremely large text pieces.

*Call graph*: called by 1 (_slices).


##### `TextChunker._recursive_split`  (lines 167–179)

```
def _recursive_split(self, text: str, level: int) -> list[str]
```

**Purpose**: This helper breaks large text using increasingly fine separators. It tries natural breaks first, like paragraphs and sentences, before falling back to whitespace.

**Data flow**: It receives text and a delimiter level. At each level, it tries to split on the separators for that level. If splitting does not help, it moves to the next level. Pieces still too large are split again more finely. It returns a list of smaller text pieces.

**Call relations**: `_slices` calls this when text is bigger than the target size. `_recursive_split` uses `_split_at_delimiters` for natural punctuation breaks, `_count_words` to test piece size, and `_split_on_whitespace` as the final fallback.

*Call graph*: calls 3 internal fn (_count_words, _split_at_delimiters, _split_on_whitespace); called by 1 (_slices).


##### `TextChunker._split_at_delimiters`  (lines 182–195)

```
def _split_at_delimiters(text: str, delimiters: tuple[str, ...]) -> list[str]
```

**Purpose**: This helper cuts text at the earliest occurrence of any supplied delimiter, while keeping the delimiter with the piece before it. This helps preserve punctuation and line breaks in the resulting chunks.

**Data flow**: It receives text and a group of delimiter strings. It repeatedly finds the earliest delimiter in the remaining text, cuts there, stores that piece, and continues with the rest. It returns all non-blank pieces.

**Call relations**: `_recursive_split` calls this at each delimiter level. It supplies the first, natural-looking cuts before the chunker decides whether pieces need further splitting.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._split_on_whitespace`  (lines 197–213)

```
def _split_on_whitespace(self, text: str) -> list[str]
```

**Purpose**: This helper is the last-resort splitter when punctuation and line breaks are not enough. It splits by runs of non-whitespace text, roughly meaning words.

**Data flow**: It receives text. If normal word runs are available, it groups them into batches of the target size. If there are no words, or one very long run, it cuts by character count based on the target. It returns non-empty text pieces.

**Call relations**: `_recursive_split` calls this when it has run out of delimiter levels. It prevents the chunker from getting stuck on text with no useful punctuation or spacing.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._greedy_merge`  (lines 215–229)

```
def _greedy_merge(self, pieces: list[str]) -> list[str]
```

**Purpose**: This helper combines neighboring pieces when they are small enough together. It avoids creating many tiny chunks that would be less useful for search.

**Data flow**: It receives a list of pieces from the splitting step. Starting from the first piece, it keeps adding the next piece if the combined text stays within a generous size limit. When adding would make it too large, it stores the current combined chunk and starts a new one. It returns the merged list.

**Call relations**: `_slices` calls this after `_recursive_split`. It uses `_count_words` and a rounded size limit to balance two goals: keep chunks readable, but not too small.

*Call graph*: calls 1 internal fn (_count_words); called by 1 (_slices); 1 external calls (ceil).


##### `TextChunker._apply_overlap`  (lines 231–237)

```
def _apply_overlap(self, chunks: list[str]) -> list[str]
```

**Purpose**: This helper adds a little context from the end of each chunk to the start of the next one. The goal is to keep meaning from being lost exactly at a chunk boundary.

**Data flow**: It receives a list of chunks. If there is only one chunk or overlap is disabled, it returns them unchanged. Otherwise, for each chunk after the first, it prefixes trailing context from the previous chunk and returns the expanded list.

**Call relations**: `_slices` calls this after merging. It uses `_trailing_context` to choose what context to copy, and `itertools.pairwise` to walk through neighboring chunk pairs.

*Call graph*: calls 1 internal fn (_trailing_context); called by 1 (_slices); 1 external calls (pairwise).


##### `TextChunker._trailing_context`  (lines 239–249)

```
def _trailing_context(self, text: str) -> str
```

**Purpose**: This helper chooses the bit of text from the end of one chunk that should be repeated before the next chunk. It tries to make that overlap start at a sensible sentence boundary when possible.

**Data flow**: It receives one chunk of text. It extracts the last configured number of word runs. If that trailing text contains an early sentence boundary, it drops the earlier sentence fragment and returns the later part. Otherwise it returns the trailing words as-is. If the chunk is already short, it returns no overlap.

**Call relations**: `_apply_overlap` calls this while building overlapped chunks. It is the small piece that makes overlap more readable instead of blindly copying text from the middle of a sentence whenever it can avoid it.

*Call graph*: called by 1 (_apply_overlap).


##### `TextChunker._digest`  (lines 252–254)

```
def _digest(owner_kind: str, owner_id: str, subject: str, ordinal: int, text: str) -> str
```

**Purpose**: This helper creates a stable unique ID for a chunk. The ID changes if the owner, subject, order, or text changes, which helps the index know which chunks are current.

**Data flow**: It receives owner kind, owner ID, subject, ordinal number, and chunk text. It joins those values with a separator, hashes them with SHA-256, and returns the digest string with a `sha256:` prefix.

**Call relations**: `TextChunker.chunk` calls this for every slice it turns into a `Chunk`. Later, `chunk_embed_upsert` uses those digests as the keep set when pruning stale indexed chunks.

*Call graph*: called by 1 (chunk); 1 external calls (sha256).


### `extensions/embed_openai/ufo_ext_embed_openai.py`

`io_transport` · `startup registration and embedding jobs`

This extension is the project's built-in OpenAI embedding backend. An embedding is a list of numbers that represents the meaning of a piece of text, so similar text ends up with similar numbers. The system can then search or compare memories by meaning, not just by exact words.

The file registers a default backend named "default". If no other embedding backend is chosen, the core system can discover this one through the extension manifest. The actual OpenAI client is not created when the server starts. Instead, it is created only when an embed call happens. That matters because a developer can start the system without an OpenAI key, but the first real embedding attempt will fail clearly if `OPENAI_API_KEY` is missing.

Before sending text to OpenAI, the file trims each item to a maximum size and groups items into batches. This is like packing boxes before shipping: each box has limits on both item count and total weight. Here the limits are number of texts and total characters. The `OpenAIEmbedClient` then sends each batch to OpenAI's async API, keeps the returned vectors in the same order as the input, and returns them as plain tuples of floats.

#### Function details

##### `plan_embed_batches`  (lines 32–48)

```
def plan_embed_batches(texts: tuple[str, ...]) -> tuple[tuple[str, ...], ...]
```

**Purpose**: This function prepares text for the OpenAI embedding request by trimming overly long entries and splitting the work into safe-sized batches. It prevents one request from becoming too large for the provider or for the system's own limits.

**Data flow**: It receives a tuple of text strings. For each string, it cuts the text down to the configured maximum length, then adds it to the current batch unless that batch would exceed the maximum number of items or total characters. It returns a tuple of batches, where each batch is a tuple of clipped text strings.

**Call relations**: The embedding client calls this just before talking to OpenAI. It acts as the packing step: `OpenAIEmbedClient.embed` gives it all requested texts, then sends each returned batch as a separate OpenAI request.

*Call graph*: called by 1 (embed).


##### `OpenAIEmbedClient.embed`  (lines 61–73)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This async method turns one or more text strings into OpenAI embedding vectors. It is the main runtime bridge between this project and OpenAI's embedding API.

**Data flow**: It receives a tuple of texts. It reads `OPENAI_API_KEY` from the environment; if the key is missing, it raises a clear error. It creates an async OpenAI client, uses `plan_embed_batches` to split the input safely, sends each batch to OpenAI, sorts the response rows back into input order, converts the returned numbers to floats, and returns a tuple of embedding vectors.

**Call relations**: This method is used when the core indexing or memory system needs embeddings from this backend. It calls `plan_embed_batches` to shape the payload, then hands each batch to `openai.AsyncOpenAI` so the external provider can produce vectors.

*Call graph*: calls 1 internal fn (plan_embed_batches); 1 external calls (AsyncOpenAI).


##### `build`  (lines 76–81)

```
def build(ctx: ExtensionContext) -> EmbedClient
```

**Purpose**: This function creates the embedding client that the core system will use for this backend. It deliberately does not require an OpenAI key at construction time.

**Data flow**: It receives an extension context, which represents the workspace or runtime scope, but this backend does not need to read anything from it. It returns a new `OpenAIEmbedClient` instance configured with the default model.

**Call relations**: The manifest points to this function as the factory for the default embedding backend. During startup, the core extension system can call it to get a client, while the actual OpenAI API setup is delayed until `OpenAIEmbedClient.embed` is called.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 84–89)

```
def manifest() -> Manifest
```

**Purpose**: This function tells the host application what this extension provides. In this case, it announces an embedding backend named `default` and says that `build` should be used to create it.

**Data flow**: It takes no input. It constructs an `EmbedBackendSpec` describing the backend name and factory function, wraps that in a `Manifest` with the extension name and version, and returns the manifest to the extension loader.

**Call relations**: The extension loading system calls this to discover the extension. The returned manifest connects the backend name `default` to `build`, so later startup code can construct the OpenAI embedding client when this backend is selected.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/index_default/ufo_ext_index_default.py`

`domain_logic` · `cross-cutting during indexing and search`

This file is the project’s default “index,” meaning the place where pieces of content are saved so they can be found again later. Without it, a fresh deployment would have no standard way to store chunks of text, remove stale chunks, or search memory by keywords and embeddings. An embedding is a list of numbers that represents the meaning of text, so similar text ends up with similar number lists.

The file supports two database styles. In PostgreSQL, it uses PostgreSQL’s built-in full-text search for word matching and pgvector for vector similarity. In SQLite, it uses FTS5, SQLite’s full-text search feature, and does vector comparison in Python by scanning rows and calculating cosine similarity. The public data types stay the same either way, so the rest of the system does not need to know which database is underneath.

The main class, DefaultIndex, opens a workspace-scoped database transaction for each operation. It can insert or update chunks, delete all chunks for one owner, prune old chunks after re-chunking, search by words, and search by vector similarity. Think of it like a library catalog that stores both the words on each page and a rough “meaning fingerprint” for each page, then can look up either exact words or nearby ideas.

#### Function details

##### `pgvector_literal`  (lines 31–32)

```
def pgvector_literal(vector: tuple[float, ...]) -> str
```

**Purpose**: Turns a Python tuple of numbers into the text format expected by PostgreSQL’s pgvector extension. This is needed when saving or querying vector embeddings in PostgreSQL.

**Data flow**: It receives a tuple such as several floating-point numbers → converts each value to a plain float representation and joins them inside square brackets → returns a string that PostgreSQL can cast into its vector type.

**Call relations**: When DefaultIndex.upsert saves an embedding to PostgreSQL, it uses this helper to format the stored value. When DefaultIndex.vector searches by embedding in PostgreSQL, it uses the same helper to format the query vector before handing it to SQL.

*Call graph*: called by 2 (upsert, vector).


##### `cosine`  (lines 35–43)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Measures how similar two embeddings are using cosine similarity, a common way to compare the direction of two number lists. SQLite uses this because it does not have the same vector-search support as PostgreSQL here.

**Data flow**: It receives two equal-length tuples of numbers → calculates the size of each vector, multiplies matching positions together, and divides by the combined sizes → returns a similarity score, or 0.0 if either vector has no size.

**Call relations**: DefaultIndex.vector calls this on SQLite after reading candidate rows from the database. It supplies the scores that are then used to sort the best vector matches.

*Call graph*: called by 1 (vector); 1 external calls (sqrt).


##### `pack_embedding`  (lines 46–47)

```
def pack_embedding(vector: tuple[float, ...]) -> bytes
```

**Purpose**: Converts an embedding into compact bytes so SQLite can store it in a database column. SQLite does not have the same native vector type used by PostgreSQL, so the numbers are packed by hand.

**Data flow**: It receives a tuple of floating-point numbers → writes them into a binary blob using four bytes per number → returns those bytes for storage in SQLite.

**Call relations**: DefaultIndex.upsert calls this only on the SQLite path, just before writing a chunk and its embedding to the database.

*Call graph*: called by 1 (upsert); 1 external calls (pack).


##### `unpack_embedding`  (lines 50–51)

```
def unpack_embedding(blob: bytes) -> tuple[float, ...]
```

**Purpose**: Converts an embedding stored as bytes in SQLite back into a tuple of numbers. This is the reverse of pack_embedding.

**Data flow**: It receives a binary blob from the database → reads it as a sequence of four-byte floating-point numbers → returns a tuple that Python can compare with another embedding.

**Call relations**: DefaultIndex.vector calls this on SQLite rows before passing the restored embedding to cosine for scoring.

*Call graph*: called by 1 (vector); 1 external calls (unpack).


##### `_hit`  (lines 54–63)

```
def _hit(row: sa.RowMapping, score: float) -> Hit
```

**Purpose**: Builds a standard search result object from a database row and a score. This keeps PostgreSQL and SQLite search results shaped the same for the rest of the system.

**Data flow**: It receives a row containing chunk details plus a separate score → copies fields such as chunk ID, owner, subject, order, and text into a Hit object → returns that Hit with the score converted to a normal float.

**Call relations**: DefaultIndex.lexical uses this after word-search queries, and DefaultIndex.vector uses it after vector-search queries. It is the final adapter between raw database results and the index API’s Hit objects.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `DefaultIndex.upsert`  (lines 168–205)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds new chunks to the index or updates existing chunks with the same digest. This is used when content is indexed or re-indexed so search sees the latest text and embedding.

**Data flow**: It receives a tuple of Chunk objects → if there are none, it does nothing; otherwise it opens a database transaction, checks whether the connection is PostgreSQL or SQLite, and writes each chunk using the right SQL → in SQLite it also refreshes the full-text-search table entry for each chunk.

**Call relations**: This is called by the wider indexing flow when chunks are ready to be stored. On PostgreSQL it uses pgvector_literal for embeddings; on SQLite it uses pack_embedding and also updates the separate FTS table so later lexical searches can find the text.

*Call graph*: calls 2 internal fn (pack_embedding, pgvector_literal).


##### `DefaultIndex.delete`  (lines 207–214)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Removes every indexed chunk that belongs to one owner. This is used when a document, memory item, or other indexed owner should no longer be searchable.

**Data flow**: It receives an IndexScope, which identifies an owner kind and owner ID → opens a transaction and deletes matching rows → on SQLite it deletes from the full-text-search table first, then from the main chunk table; on PostgreSQL one delete from the chunk table is enough.

**Call relations**: Other parts of the system can call this to clear an indexed owner. DefaultIndex.prune also calls it when the keep-set is empty, because pruning everything is the same as deleting the whole scope.

*Call graph*: called by 1 (prune).


##### `DefaultIndex.prune`  (lines 216–230)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Removes old chunks for an owner while keeping a known set of current chunk digests. This matters after content is split into chunks again, because old chunks may otherwise stay searchable even though they no longer exist in the source.

**Data flow**: It receives an IndexScope and a set of chunk digests to keep → if the keep-set is empty, it delegates to delete; otherwise it opens a transaction and deletes only chunks for that owner whose digest is not in the keep-set → on SQLite it also removes matching rows from the full-text-search table.

**Call relations**: The re-indexing flow uses this after writing the current chunks for an owner. It calls DefaultIndex.delete for the simple “keep nothing” case; otherwise it runs dialect-specific pruning SQL.

*Call graph*: calls 1 internal fn (delete).


##### `DefaultIndex.lexical`  (lines 232–268)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches for chunks whose text matches a word query. This is the keyword-search half of the index, useful when the caller has exact terms or phrases to look for.

**Data flow**: It receives a query string, allowed subjects, an owner kind, and a maximum result count → if there are no subjects, or the cleaned query is empty, it returns no results; otherwise it runs PostgreSQL full-text search or SQLite FTS5 search → converts each matching row into a Hit and returns them ordered by database-provided relevance.

**Call relations**: The search layer calls this when it wants lexical, meaning word-based, retrieval. After the database returns rows, this function hands each row to _hit so callers receive the same Hit shape regardless of database.

*Call graph*: calls 1 internal fn (_hit).


##### `DefaultIndex.vector`  (lines 270–303)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches for chunks whose embeddings are closest to a query embedding. This is the meaning-based search path, used when similar ideas should match even if the exact words differ.

**Data flow**: It receives a query embedding, allowed subjects, an owner kind, and a maximum result count → if the embedding or subject set is empty, it returns no results; otherwise it opens a transaction → PostgreSQL scores and orders rows in SQL, while SQLite reads candidate embeddings, unpacks them, scores them in Python with cosine similarity, sorts them, and returns the top hits.

**Call relations**: The search layer calls this for vector retrieval. On PostgreSQL it uses pgvector_literal before sending the query to SQL; on SQLite it uses unpack_embedding and cosine to score rows locally. In both cases it uses _hit to turn scored rows into standard Hit objects.

*Call graph*: calls 4 internal fn (_hit, cosine, pgvector_literal, unpack_embedding).


##### `manifest`  (lines 306–316)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system and registers the default index backend. This is how the core system discovers that the backend named "default" can be created from this file.

**Data flow**: It takes no input → creates a Manifest containing the extension name, version, and an IndexBackendSpec → the spec includes a factory that builds DefaultIndex using the transaction opener supplied by the host context → returns the Manifest.

**Call relations**: The extension-loading system calls this during setup. The returned manifest tells the core that when it needs the default index backend, it should create a DefaultIndex wired to the workspace-scoped transaction function.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/turbopuffer/ufo_ext_turbopuffer.py`

`io_transport` · `startup registration, then index reads/writes during jobs and serving`

This extension is the bridge between UFO’s own indexing interface and Turbopuffer, an external hosted search service. Without it, a workspace configured to use the “turbopuffer” backend could not save searchable memory chunks or retrieve them during recall.

The file treats every text chunk like a document in a Turbopuffer namespace, which is a named container for one workspace’s indexed data. Each document stores the chunk text, its owner information, its subject, its position, and optionally an embedding, which is a list of numbers that represents the meaning of the text. Turbopuffer can then search these documents in two ways: vector search, which finds text with similar meaning, and BM25 keyword search, which is a common full-text ranking method for matching words.

A small set of helper functions translates between UFO’s chunk IDs and Turbopuffer’s document IDs, builds request bodies, turns returned rows into UFO `Hit` objects, and creates filters so searches only look inside the right owner kind and subject set. The `TurbopufferIndex` class performs the actual HTTP calls. It reads the API key from UFO’s credential store, writes chunks in batches, queries for matches, and deletes old chunks by first listing the documents in a scope. The `manifest` function registers this backend so the rest of the system can discover and build it at startup.

#### Function details

##### `turbopuffer_id`  (lines 42–48)

```
def turbopuffer_id(chunk_digest: str) -> str
```

**Purpose**: Converts UFO chunk digests into document IDs that are safe and short enough for Turbopuffer. Standard SHA-256 digests are shortened using URL-safe Base64; any non-standard ID is left alone.

**Data flow**: It receives a chunk digest string. If the string looks like `sha256:` followed by a 64-character hex value, it turns the raw bytes into a shorter URL-safe text ID and removes the padding characters. If it does not match that shape, it returns the original string unchanged.

**Call relations**: When chunks are written, `upsert_body` uses this to choose the Turbopuffer document IDs. When chunks are removed, `TurbopufferIndex.delete` and `TurbopufferIndex.prune` use the same conversion so they delete the exact documents that were written.

*Call graph*: called by 3 (delete, prune, upsert_body); 1 external calls (urlsafe_b64encode).


##### `chunk_digest_from_id`  (lines 51–60)

```
def chunk_digest_from_id(chunk_id: str) -> str
```

**Purpose**: Converts a Turbopuffer document ID back into UFO’s normal chunk digest form when possible. This keeps search results using the same kind of ID that the rest of UFO expects.

**Data flow**: It receives a document ID from Turbopuffer. If the ID has the shortened Base64 length, it tries to decode it and rebuilds a `sha256:` digest from the bytes. If decoding fails, or if the ID does not have that length, it returns the original ID.

**Call relations**: Search and listing responses come back as Turbopuffer rows. `hit_from_row` uses this when making recall hits, and `_scope_chunks` uses it when rebuilding chunk records before delete or prune work.

*Call graph*: called by 2 (_scope_chunks, hit_from_row); 1 external calls (urlsafe_b64decode).


##### `upsert_body`  (lines 63–79)

```
def upsert_body(chunks: tuple[Chunk, ...]) -> dict[str, Any]
```

**Purpose**: Builds the JSON body sent to Turbopuffer when saving or replacing a batch of chunks. It also tells Turbopuffer to use cosine distance for vector search and to enable full-text search on the text field.

**Data flow**: It receives a tuple of UFO `Chunk` objects. It turns them into parallel columns: IDs, vectors, owner fields, subjects, ordinals, and text. The result is a dictionary ready to send as the HTTP request body.

**Call relations**: `TurbopufferIndex.upsert` calls this for each write batch. Inside the body-building step, it calls `turbopuffer_id` so the stored document IDs match Turbopuffer’s limits and later delete calls.

*Call graph*: calls 1 internal fn (turbopuffer_id); called by 1 (upsert).


##### `query_filters`  (lines 82–86)

```
def query_filters(owner_kind: str, subjects: frozenset[str]) -> list[Any]
```

**Purpose**: Builds the filter used for normal searches. It narrows results to one owner kind and to the allowed set of recall subjects, so a query does not search unrelated memory.

**Data flow**: It receives an owner kind and a frozen set of subjects. It returns a Turbopuffer filter expression saying: owner kind must equal this value, and subject must be one of these values.

**Call relations**: `TurbopufferIndex._query` calls this whenever lexical or vector search is run. It is the small gatekeeper that keeps broad search requests inside the caller’s intended scope.

*Call graph*: called by 1 (_query).


##### `scope_filters`  (lines 89–96)

```
def scope_filters(scope: IndexScope, after_id: str | None) -> list[Any]
```

**Purpose**: Builds the filter used when listing all chunks for one owner scope. This is needed before deleting or pruning, because Turbopuffer deletes by document ID.

**Data flow**: It receives an `IndexScope`, which identifies an owner kind and owner ID, plus an optional `after_id` for paging. It returns a filter expression for that owner, and if `after_id` is present, only IDs greater than that value are included.

**Call relations**: `TurbopufferIndex._scope_chunks` calls this while walking through all documents in a scope page by page. That listing then feeds `delete` and `prune`.

*Call graph*: called by 1 (_scope_chunks).


##### `hit_from_row`  (lines 99–108)

```
def hit_from_row(row: dict[str, Any], score: float) -> Hit
```

**Purpose**: Turns one row returned by Turbopuffer into UFO’s standard `Hit` object. A `Hit` is the record the rest of the system uses to represent a found chunk and its score.

**Data flow**: It receives a row dictionary from Turbopuffer and a score chosen by the caller. It reads the ID, owner fields, subject, ordinal, and text, converts the ID back to a chunk digest, and returns a `Hit` containing all of that information.

**Call relations**: `TurbopufferIndex.lexical` and `TurbopufferIndex.vector` call this after `_query` returns raw rows. It is the translation point between Turbopuffer’s response shape and UFO’s recall result shape.

*Call graph*: calls 1 internal fn (chunk_digest_from_id); called by 2 (lexical, vector); 1 external calls (__init__).


##### `vector_score`  (lines 111–117)

```
def vector_score(row: dict[str, Any], position: int, total: int) -> float
```

**Purpose**: Computes a useful score for one vector-search result. UFO wants higher scores to mean better matches, while Turbopuffer may return distance, where lower means closer.

**Data flow**: It receives a result row, the row’s position in the returned list, and the total number of rows. If Turbopuffer included a `$dist` distance, it turns that into `1 - distance`. If no distance is present, it falls back to a descending rank score based on position.

**Call relations**: `TurbopufferIndex.vector` calls this before converting rows into hits. This keeps vector results comparable in the simple “bigger is better” direction expected by later recall fusion.

*Call graph*: called by 1 (vector).


##### `TurbopufferIndex.upsert`  (lines 131–142)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Saves new or updated chunks into Turbopuffer. It ignores chunks without embeddings because this backend’s stored vector column needs embeddable content.

**Data flow**: It receives a tuple of `Chunk` objects. It keeps only chunks that have an embedding, asks `_auth` for an authorization header, splits the work into batches, builds each request body with `upsert_body`, posts it to the namespace path from `_path`, and raises an error if Turbopuffer rejects the request. It returns nothing, but the remote index is updated.

**Call relations**: The core indexing flow calls this when content needs to become searchable. It relies on `_auth` for the API key, `_path` for the workspace namespace, and `upsert_body` for the exact Turbopuffer payload.

*Call graph*: calls 3 internal fn (_auth, _path, upsert_body).


##### `TurbopufferIndex.delete`  (lines 144–152)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Deletes every indexed chunk belonging to one owner scope. This is used when an owner’s indexed content should be removed entirely.

**Data flow**: It receives an `IndexScope`. It gets authorization, asks `_scope_chunks` to list all chunks in that scope, converts their digests to Turbopuffer document IDs, sends delete requests in batches, and raises an error if any remote request fails. The result is that those documents disappear from Turbopuffer.

**Call relations**: The wider indexing system calls this when a full scope is being removed. Since Turbopuffer deletes by ID, this method first delegates to `_scope_chunks` to discover those IDs, then uses `_path` and `turbopuffer_id` to delete the right remote records.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.prune`  (lines 154–164)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Deletes only the chunks in a scope that are no longer supposed to exist. This is useful after content is re-chunked, so old leftover chunks do not remain searchable.

**Data flow**: It receives an `IndexScope` and a set of chunk digests to keep. It lists the current chunks in that scope, filters out the ones present in the keep set, converts the remaining digests to Turbopuffer IDs, and sends batched delete requests. It returns nothing, but stale remote documents are removed.

**Call relations**: The indexing flow calls this during cleanup after rewriting a scope. Like `delete`, it depends on `_auth`, `_scope_chunks`, `_path`, and `turbopuffer_id`, but it removes only records outside the keep set.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.lexical`  (lines 166–174)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Runs a keyword-style search over chunk text. It uses BM25, a ranking method that scores documents by how well their words match the query.

**Data flow**: It receives a query string, a set of allowed subjects, an owner kind, and a result limit. If the query is blank or there are no subjects, it returns an empty tuple. Otherwise it asks `_query` to rank by text BM25, then turns each returned row into a `Hit` with a simple rank-based score.

**Call relations**: The recall system uses this when it wants word-based matches. This method delegates the HTTP request to `_query` and then uses `hit_from_row` so callers receive normal UFO hits instead of raw Turbopuffer rows.

*Call graph*: calls 2 internal fn (_query, hit_from_row).


##### `TurbopufferIndex.vector`  (lines 176–185)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Runs meaning-based search using an embedding vector. It finds chunks whose stored vectors are close to the query vector.

**Data flow**: It receives an embedding, allowed subjects, an owner kind, and a result limit. If the embedding is empty or there are no subjects, it returns no results. Otherwise it asks `_query` to run approximate nearest-neighbor search, scores each row with `vector_score`, drops non-positive scores, and returns the remaining rows as `Hit` objects.

**Call relations**: The recall system uses this when it wants semantic matches rather than exact word matches. It hands the remote search to `_query`, uses `vector_score` to make scores point in the expected direction, and uses `hit_from_row` to return standard UFO results.

*Call graph*: calls 3 internal fn (_query, hit_from_row, vector_score).


##### `TurbopufferIndex._query`  (lines 187–200)

```
async def _query(self, rank_by: list[Any], owner_kind: str, subjects: frozenset[str], limit: int) -> list[dict[str, Any]]
```

**Purpose**: Sends one search request to Turbopuffer and returns the raw result rows. It is shared by both keyword search and vector search.

**Data flow**: It receives a Turbopuffer `rank_by` instruction, owner kind, subject set, and limit. It builds a request body with the ranking rule, maximum result count, returned attributes, and filters from `query_filters`. It posts that body to the namespace query path with authorization. If the namespace does not exist, it returns an empty list; otherwise it checks for errors and returns the response rows.

**Call relations**: `TurbopufferIndex.lexical` and `TurbopufferIndex.vector` both call this instead of duplicating HTTP request logic. It calls `_auth` for credentials, `_path` for the endpoint, and `query_filters` to keep the search scoped.

*Call graph*: calls 3 internal fn (_auth, _path, query_filters); called by 2 (lexical, vector).


##### `TurbopufferIndex._scope_chunks`  (lines 202–230)

```
async def _scope_chunks(self, scope: IndexScope, headers: dict[str, str]) -> list[Chunk]
```

**Purpose**: Lists all chunks currently stored in Turbopuffer for one owner scope. It is mainly a support step for deletion and pruning.

**Data flow**: It receives an `IndexScope` and already-built authorization headers. It repeatedly queries Turbopuffer for pages of rows ordered by ID, using `scope_filters` and an `after_id` marker to continue where the previous page ended. Each row is converted into a lightweight `Chunk` with its digest and attributes. It returns the full list found so far, or an empty/partial list if the namespace does not exist.

**Call relations**: `TurbopufferIndex.delete` and `TurbopufferIndex.prune` call this before deciding which document IDs to delete. It uses `_path` to reach the namespace, `scope_filters` to stay inside the owner scope, and `chunk_digest_from_id` to restore UFO-style chunk digests.

*Call graph*: calls 3 internal fn (_path, chunk_digest_from_id, scope_filters); called by 2 (delete, prune); 1 external calls (__init__).


##### `TurbopufferIndex._auth`  (lines 232–234)

```
async def _auth(self) -> dict[str, str]
```

**Purpose**: Builds the HTTP authorization header for Turbopuffer requests. It reads the API key from UFO’s credential access object each time it is needed.

**Data flow**: It asks the credential store for the `turbopuffer_api_key` value. It then returns a dictionary containing an `Authorization` header in Bearer-token form. It does not change local state.

**Call relations**: `upsert`, `delete`, `prune`, and `_query` call this before making HTTP requests. It is the shared doorway between UFO’s credential system and Turbopuffer’s API authentication.

*Call graph*: called by 4 (_query, delete, prune, upsert).


##### `TurbopufferIndex._path`  (lines 236–237)

```
def _path(self, suffix: str='') -> str
```

**Purpose**: Builds the URL path for this workspace’s Turbopuffer namespace. This keeps all indexed data separated by workspace.

**Data flow**: It receives an optional suffix such as `/query`. It combines the fixed namespace prefix, the credential object’s workspace ID, and the suffix into a path like `/namespaces/ufo-<workspace>...`.

**Call relations**: `upsert`, `delete`, `prune`, `_query`, and `_scope_chunks` call this whenever they need to post to the correct Turbopuffer endpoint. It centralizes the namespace naming rule so every operation uses the same workspace container.

*Call graph*: called by 5 (_query, _scope_chunks, delete, prune, upsert).


##### `manifest`  (lines 240–260)

```
def manifest() -> Manifest
```

**Purpose**: Declares this file as a UFO extension and registers the Turbopuffer index backend. This is how the core system discovers that `memory.index_backend = "turbopuffer"` is available.

**Data flow**: It creates and returns a `Manifest` containing the extension name and version, one credential slot for the Turbopuffer API key, and one index backend specification. The backend factory builds a `TurbopufferIndex` with the runtime credential access object and an `httpx` asynchronous HTTP client pointed at Turbopuffer’s base URL.

**Call relations**: The extension loading path calls this during startup. After that, the core can use the registered factory to create a `TurbopufferIndex`, and all later index operations flow through that object.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Memory extension surface
The memory package exposes durable recall through its manifest, event vocabulary, and read-only memory object interface.

### `extensions/memory/ufo_ext_memory/__init__.py`

`other` · `package import`

This is the package entry file for the memory extension, but it does not contain any executable logic itself. Its main job is to label the package and explain its scope in a short docstring. In Python, an `__init__.py` file marks a directory as an importable package, meaning other parts of the project can refer to `ufo_ext_memory` as a module.

The text in the file tells a newcomer what this package is meant to provide. The extension is concerned with “durable facts,” meaning information that should survive beyond a single interaction. It also mentions recall through a `user_prompt_submit` hook, which means the extension can react when a user sends a prompt and bring relevant memory back into the conversation. It mentions page derivation through a `page_change` hook, meaning it can respond when some page-like content changes and derive memory-related output from it. Finally, it points to a memory-index job, which likely prepares or organizes stored memory so it can be searched or recalled efficiently.

Without this file, the package may not be importable in some Python setups, and readers would lose this small but useful signpost explaining what the memory extension is for.


### `extensions/memory/ufo_ext_memory/manifest.py`

`orchestration` · `startup registration, then active during tool calls, prompt submission hooks, page-change processing, and scheduled jobs`

This file tells the host system how the memory feature plugs in. Without it, the agent would not know that memory_search and memory_update exist, prompts would not get relevant remembered facts added before the model answers, and background jobs would not index or consolidate memory.

The file has three main jobs. First, it defines the input shapes for the two tools: one for searching memory with up to three focused queries, and one for writing a lasting fact or preference. Second, it provides the small workflows behind those tools and hooks. Searching fans out across both stored memory items and synced source pages, then merges the results so one query cannot crowd out the others. Updating chooses whether a memory belongs to one member or to the shared workspace, then stores it. The recall hook runs before a user prompt reaches the model and quietly injects relevant memory into the context; importantly, it is “best effort,” meaning it times out and swallows errors rather than blocking the user’s turn.

Third, the file registers background work. One job indexes memory for search. Page-change hooks index changed source pages and derive new facts from them. Another job consolidates older facts into broader summaries. The manifest at the end is like a plugboard: it connects all these pieces to the system.

#### Function details

##### `_date_bound`  (lines 150–161)

```
def _date_bound(value: str | None, *, end: bool) -> datetime | None
```

**Purpose**: Turns an optional date or date-time string into a timezone-aware UTC boundary for searching memory by creation date. It also makes an end date like 2026-01-31 include that whole day by moving the boundary to the next midnight.

**Data flow**: It receives a string or nothing, plus a flag saying whether this is the end of a range. If there is no string, it returns nothing. If there is a string, it parses it as an ISO-style date or date-time, adds UTC when no timezone was written, optionally advances a bare end date by one day, and returns the resulting datetime. Bad date text is allowed to raise an error so the tool call can report a recoverable problem.

**Call relations**: The memory search tool calls this before searching. The parsed start and end values are then passed into the search workflow so only memory created within that window is considered.

*Call graph*: called by 1 (memory_search_handler); 2 external calls (fromisoformat, timedelta).


##### `MemorySearchService.search`  (lines 170–219)

```
async def search(self, queries: tuple[str, ...], member_id: UUID | None, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Runs the shared memory-search workflow used by the tool and by other extension code. It searches both durable memory items and synced source-page snippets, then returns a single set of readable matches.

**Data flow**: It receives one to three search queries, the current member if there is one, and optional start and end times. It finds the right subjects to search, such as the member’s private memory plus shared memory, asks the store to search memories and source pages for every query in parallel, then merges the per-query results in rounds. Duplicate memory IDs and page IDs are removed. It outputs MemoryMatch objects that include the kind of hit, the text snippet, an object reference that can be opened later, and the creation date.

**Call relations**: The memory_search_handler creates this service when the agent calls memory_search. The manifest also registers it as the default memory search provider, so other parts of the system can use the same search behavior instead of reimplementing it.

*Call graph*: 6 external calls (__init__, __init__, gather, zip_longest, recall_subjects, store_for).


##### `match_line`  (lines 222–229)

```
def match_line(match: MemoryMatch) -> str
```

**Purpose**: Formats one memory search result into a single line of text that an agent or user can read. It includes the snippet first, then the reference and date when available.

**Data flow**: It receives one MemoryMatch. It builds a line such as a bullet with the match kind and text. If the match has an object reference, it appends that reference and, when present, the creation date. It returns the finished text line.

**Call relations**: After memory_search_handler gets matches from MemorySearchService.search, it calls this for each match to make the final tool response easy to scan.

*Call graph*: called by 1 (memory_search_handler).


##### `memory_search_handler`  (lines 232–249)

```
async def memory_search_handler(ctx: ToolContext, args: MemorySearchInput) -> ToolResult
```

**Purpose**: Implements the memory_search tool that the agent can call before answering. It turns tool arguments into a real search and returns either matching memory lines or a clear “No matching memory” message.

**Data flow**: It receives the tool context and the search arguments. It checks that the extension context exists, parses optional start and end dates, searches using MemorySearchService, and formats the matches. It returns a ToolResult containing text for the agent. If nothing matches, the returned text says so.

**Call relations**: The manifest registers this as the handler for the memory_search tool. During a model turn, when the agent invokes that tool, the tool system calls this function; it then delegates date parsing to _date_bound, searching to MemorySearchService.search, and display formatting to match_line.

*Call graph*: calls 2 internal fn (_date_bound, match_line); 3 external calls (__init__, __init__, __init__).


##### `memory_update_handler`  (lines 252–270)

```
async def memory_update_handler(ctx: ToolContext, args: MemoryUpdateInput) -> ToolResult
```

**Purpose**: Implements the memory_update tool that lets the agent store a durable fact, preference, decision, event, or task-like memory. It decides whether the new memory is private to the current member or shared with the workspace.

**Data flow**: It receives the tool context and the memory text plus metadata such as kind, confidence, sharing choice, and source reference. It checks that the extension context exists. If the memory is marked shared, or there is no current member, it writes to the shared subject; otherwise it writes to the member’s subject. It commits a MemoryWrite to the store and returns a short confirmation message naming where it was remembered.

**Call relations**: The manifest registers this as the handler for the memory_update tool. When the agent learns something worth keeping, the tool system calls this function, which hands the actual write to the memory store.

*Call graph*: 5 external calls (__init__, __init__, __init__, member_subject, store_for).


##### `recall_hook`  (lines 273–303)

```
async def recall_hook(ctx: HookContext) -> HookOutcome
```

**Purpose**: Automatically adds relevant remembered facts to the model’s context before it answers a user prompt. It is deliberately fail-soft: memory recall should help the answer, but it must not block the conversation if search is slow or broken.

**Data flow**: It receives a hook context and first checks that the event is a user prompt submission. It builds the set of subjects to search, tries to recall matching memory under a short timeout, and records the error class if something fails. It filters out topic-only recalls, logs which memory IDs were injected when there is a turn, and returns injected context text only if recall succeeded and found usable lines. On timeout or error, it returns nothing.

**Call relations**: The manifest registers this for the user_prompt_submit event. The host calls it just before the model runs. It uses the memory store to recall facts and InjectContext to hand extra text back to the prompt-building system.

*Call graph*: 5 external calls (__init__, timeout, log, recall_subjects, store_for).


##### `index_memory`  (lines 306–311)

```
async def index_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled job that turns committed memory items into searchable index chunks. This is what makes newly stored memories findable by semantic search.

**Data flow**: It receives the extension context. It verifies that both the index backend and embedding backend are available; embeddings are numeric representations of text used for similarity search. It creates a MemoryIndexer with the index, embedder, database transaction, and text chunker, then runs it. It does not return a value; its effect is updating indexing data.

**Call relations**: The manifest registers this as the memory_index background job. The job scheduler calls it for workspaces that have memory items awaiting indexing, as selected by _items_awaiting_index through owner_candidates.

*Call graph*: 2 external calls (__init__, __init__).


##### `index_pages`  (lines 314–329)

```
async def index_pages(ctx: HookContext) -> HookOutcome
```

**Purpose**: Processes changed source pages so their content can be searched alongside memories. It also keeps a memory-side mirror row for each page change.

**Data flow**: It receives a hook context and only acts when the payload is a batch of page changes. It checks that indexing and embedding services are wired. Then it creates a PageIndexer with the needed services, the workspace ID, and a text chunker, and applies the incoming changes. It returns nothing to the hook system.

**Call relations**: The manifest registers this as one consumer of page_change events. When the core runner replays source-page changes, this hook indexes the delivered batch while the runner owns the cursor and batching.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_facts`  (lines 332–340)

```
async def derive_facts(ctx: HookContext) -> HookOutcome
```

**Purpose**: Looks at changed source pages and tries to distill durable facts from them. This lets synced documents gradually become remembered facts the agent can recall later.

**Data flow**: It receives a hook context and only acts for page-change batches. It builds a FactDeriver from the memory store and the optional model, then applies it to the changed pages. The output is not returned directly; the effect is that new fact memory items may be written to the store.

**Call relations**: The manifest registers this as a second page_change consumer, separate from page indexing. When the runner delivers a page-change batch, this function hands the batch to FactDeriver, which performs the model-based extraction work.

*Call graph*: 2 external calls (__init__, store_for).


##### `consolidate_memory`  (lines 343–351)

```
async def consolidate_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled consolidation job that groups older related facts into broader semantic summaries. This keeps memory useful as it grows, like turning many scattered notes into a concise summary page.

**Data flow**: It receives the extension context. It checks that the embedding backend is present, then creates a MemoryConsolidator with embeddings, database transaction access, workspace ID, and the model. It runs the consolidator. It returns nothing; its effect is writing summary memories and marking originals as superseded where appropriate.

**Call relations**: The manifest registers this as the memory_consolidate background job. The scheduler calls it for workspaces selected by _consolidatable_workspaces, so it only runs where there are enough old live facts to make consolidation worthwhile.

*Call graph*: 1 external calls (__init__).


##### `_items_awaiting_index`  (lines 354–359)

```
def _items_awaiting_index() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query that finds workspaces with memory items not yet indexed. It is used to decide where the memory indexing job should run.

**Data flow**: It takes no runtime input. It creates a SQL query selecting distinct workspace IDs from memory items whose embedding digest is missing, which means they still need indexing. It returns the query object rather than executing it.

**Call relations**: The manifest passes this query builder to owner_candidates for the memory_index job. The job system uses it during scheduling to find candidate workspace owners that have real indexing work waiting.

*Call graph*: 1 external calls (select).


##### `_consolidatable_workspaces`  (lines 362–377)

```
def _consolidatable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query that finds workspaces where memory consolidation could actually produce a useful cluster. It avoids scheduling consolidation for workspaces with too few facts, only young facts, or already-superseded facts.

**Data flow**: It takes no direct input. It calculates an age cutoff based on the current UTC time and the minimum age rule. Then it creates a SQL query for workspace IDs that have enough live fact items older than that cutoff. It returns the query object without running it.

**Call relations**: The manifest gives this query builder to owner_candidates for the memory_consolidate job. The scheduler uses it to pick only workspaces where a consolidation pass has a reasonable chance of doing useful work.

*Call graph*: 2 external calls (now, select).


##### `manifest`  (lines 380–455)

```
def manifest() -> Manifest
```

**Purpose**: Declares the complete memory extension to the host system. It names the extension and registers its tools, hooks, jobs, object kind, skill files, memory search provider, and web surface.

**Data flow**: It takes no input. It constructs a Manifest containing two tool definitions, the memory object, three hook registrations, two scheduled jobs with candidate selectors, a skill directory, the default memory search provider, and a surface route group. It returns that Manifest for the host to load.

**Call relations**: This is the file’s main registration point. At startup, the extension loader calls it, and the returned manifest tells the rest of the system when to call memory_search_handler, memory_update_handler, recall_hook, index_pages, derive_facts, index_memory, and consolidate_memory.

*Call graph*: 8 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, owner_candidates).


### `extensions/memory/ufo_ext_memory/events.py`

`data_model` · `cross-cutting`

The memory extension can emit structured events, which are machine-readable messages about something that happened. This file defines the small set of fixed values used for those events. The main event name, `MEMORY_RECALL_EVENT`, marks the moment when the system tries to recall stored memories before preparing a response. The two size limits act like guardrails: one caps how many recalled memory IDs may be included in an event, and the other caps how much of an error class name is recorded if recall fails. Without this file, different parts of the extension might use slightly different event names or include overly large event details, making logs and monitoring harder to trust. Think of it like a label maker and a few packing rules: every package gets the same label, and no package is allowed to contain more detail than expected.


### `extensions/memory/ufo_ext_memory/objects.py`

`domain_logic` · `request handling`

This file is the doorway from the general object system into the memory extension’s stored memory records. A memory item is a saved piece of information, such as a fact, preference, decision, event, or task. Other tools can find memory references through search, and this object type is what lets those references be opened to see the full text and details.

The main rule is that memory is read-only here. Listing shows only current, non-superseded memories, newest first. Getting a specific memory by id can still return an older superseded memory if the caller has a stale reference. In that case, the response includes a link to the newer memory that replaced it. This is like keeping an old filing-card number working, but adding a note that says “see the newer card instead.”

Access is also scoped. A caller can read shared memory, and, when the conversation has a member identity, that member’s private memory. The file uses the extension context to open a database transaction, reads from the memory table, turns rows into object-system results, and adds helpful links back to source pages or replacement memories. Attempts to apply changes or delete a memory are refused with clear messages.

#### Function details

##### `_require_ext`  (lines 58–61)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure the object request has the memory extension context attached. The extension context is needed because it knows how to reach the memory store and database transaction machinery.

**Data flow**: It receives a tool context. If that context contains an extension context, it returns it unchanged. If not, it stops the request with a runtime error, because the memory object code cannot safely read memory without knowing which extension store it belongs to.

**Call relations**: Both `MemoryObjects.list` and `MemoryObjects.get` call this at the start of their work. It acts like a gatekeeper before either function opens a database transaction or reads memory rows.

*Call graph*: called by 2 (get, list).


##### `MemoryObjects.list`  (lines 70–109)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a page of visible, current memory items for the caller. It is meant for browsing live memories, not for recalling memories by relevance; search is the main recall path.

**Data flow**: It receives the tool context and a list query. It first gets the extension context, then opens a database transaction and reads memory rows from the current workspace. It only includes subjects the caller is allowed to see, and it excludes rows that have been superseded. Each row becomes a short object listing with the memory id as its name, a trimmed text summary, and fields such as subject, item class, and memory kind. Those rows are wrapped into an object page and returned.

**Call relations**: When the object system asks to list `memory` objects, this method does the work. It calls `_require_ext` to get the extension state, uses `recall_subjects` to decide which shared or member-private memories are visible, builds `ObjectRow` entries for the object system, and hands them to `object_page` so the result follows the standard paging shape.

*Call graph*: calls 1 internal fn (_require_ext); 4 external calls (__init__, select, object_page, recall_subjects).


##### `MemoryObjects.get`  (lines 111–161)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: This opens one memory item by id and returns its full stored details. It can also reveal important provenance links, such as the source page it came from or the newer memory that replaced it.

**Data flow**: It receives the tool context and a name string. It first tries to treat the name as a UUID, which is the durable id format used for memory items. If the name is not a valid UUID, it returns nothing. Otherwise it gets the extension context, reads the matching row from the current workspace, and checks that the memory subject is visible to the caller. If no visible row exists, it returns nothing. If a row is found, it builds links for `created_from` and `superseded_by` when those values exist, packages the memory fields into a `MemorySpec`, and returns an `ObjectDetail` with timestamps and links.

**Call relations**: When a caller opens a memory reference, the object system calls this method. It relies on `_require_ext` for extension state, `recall_subjects` for visibility rules, and `uuid.UUID` to validate the requested id. It creates `ObjectRef` and `ObjectLink` values so readers can move from a memory to its source page or to the memory that superseded it.

*Call graph*: calls 1 internal fn (_require_ext); 7 external calls (__init__, __init__, __init__, __init__, select, recall_subjects, UUID).


##### `MemoryObjects.status`  (lines 163–164)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: This reports no separate status for memory objects. A memory item is either readable through `get` or not visible, so there is no extra progress or health state to show here.

**Data flow**: It receives the tool context and object name, but does not read anything or change anything. It always returns `None`, meaning there is no status payload for this object.

**Call relations**: The object system may ask an object kind for status information. For memory objects, this method intentionally ends that path immediately because the file only supports reading item contents and listing live items.


##### `MemoryObjects.apply`  (lines 166–169)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemorySpec, old: MemorySpec | None) -> None
```

**Purpose**: This refuses attempts to create or update memory through the generic object apply path. Memories must be recorded through `memory_update`, which keeps writes on the intended, controlled route.

**Data flow**: It receives the tool context, memory name, proposed memory spec, and optional old spec. Instead of using those values to change storage, it raises a `VerbNotSupported` error with a message explaining that memories are not applied here.

**Call relations**: If the object system tries to write a `memory` object, this method is called. It deliberately does not hand off to database code or update helpers; it stops the flow and points users toward the memory update mechanism.

*Call graph*: 1 external calls (__init__).


##### `MemoryObjects.delete`  (lines 171–172)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: This refuses attempts to delete a memory item. In this system, old memories are ended by being superseded during consolidation, not by direct deletion.

**Data flow**: It receives the tool context and memory name. It does not look up the item or remove anything. It raises a `VerbNotSupported` error explaining that memories cannot be deleted through this object interface.

**Call relations**: If the object system tries to delete a `memory` object, this method is called. It stops the request immediately, preserving the system’s rule that memory cleanup happens through superseding and recall filtering rather than direct removal.

*Call graph*: 1 external calls (__init__).


### Memory derivation and recall
Saved pages are condensed into long-term memories, merged into summaries, stored, searched, ranked, and kept aligned with their source pages.

### `extensions/memory/ufo_ext_memory/condenser.py`

`domain_logic` · `page-change processing and periodic background consolidation`

This file is the memory system’s “condenser.” Like reducing a long set of notes into index cards and then later merging similar index cards into one clearer card, it has two main jobs.

First, `FactDeriver` watches batches of changed source pages. It ignores deleted pages and pages that are too short to be useful. For the rest, it asks the configured language model to extract standalone facts. A language model is software that can read text and generate text. The file keeps the model request bounded by cutting long page bodies and limiting the response size, so one large page cannot make the job run away. The model’s answer is treated as untrusted: it must parse as JSON, and each fact must pass validation before it is written to the memory store.

Second, `MemoryConsolidator` runs periodically on older facts. It finds facts that have not already been replaced, groups them by subject, embeds their text into number vectors, and clusters facts that appear semantically similar. An embedding is a numeric fingerprint of meaning. Each cluster is summarized by the model into one `semantic` memory item, and the original facts are marked as superseded by that summary. This keeps recall from being clogged with repeated facts while preserving their combined meaning.

Both parts are fail-soft. If no model is configured, they skip model work instead of breaking the system.

#### Function details

##### `FactDeriver.apply`  (lines 106–115)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: This is the entry point for turning a delivered batch of page changes into extracted memory facts. It filters out deleted pages and pages that are too short, then processes the remaining pages in small groups.

**Data flow**: It receives a tuple of page changes. It keeps only pages that still exist and have enough body text, then, if a model is available, splits them into bounded batches and sends each batch onward for fact extraction. It returns nothing; its effect is that eligible pages may lead to new memory items being written later in the flow.

**Call relations**: The page-change runner calls this when it has a batch of source page updates to deliver. `apply` does the first safety check and batching step, then hands each group to `FactDeriver._derive` so the actual model reading and memory writing can happen.

*Call graph*: calls 1 internal fn (_derive); 1 external calls (batched).


##### `FactDeriver._derive`  (lines 117–133)

```
async def _derive(self, model: ModelAccess, pages: tuple[PageChange, ...]) -> None
```

**Purpose**: This function connects extracted facts back to the pages they came from and writes the useful ones into the memory store. It is where model output becomes durable memory.

**Data flow**: It takes a model and a batch of pages. It builds a lookup from page ID to page, asks `_extract` for candidate facts, drops facts that point to an unknown page or are not notable enough, and writes the remaining facts with their subject, confidence, source page ID, and timestamp. It returns nothing, but it may add fact records to storage.

**Call relations**: `FactDeriver.apply` calls this after filtering and batching pages. `_derive` depends on `FactDeriver._extract` to read the pages with the model, then wraps each accepted result in a `MemoryWrite` so the memory store can commit it.

*Call graph*: calls 1 internal fn (_extract); called by 1 (apply); 1 external calls (__init__).


##### `FactDeriver._extract`  (lines 135–151)

```
async def _extract(self, model: ModelAccess, pages: tuple[PageChange, ...]) -> tuple[ExtractedFact, ...]
```

**Purpose**: This function asks the language model to read a small batch of page bodies and return structured candidate facts. It also converts the model’s raw text answer into validated `ExtractedFact` objects.

**Data flow**: It receives a model and pages. It builds a compact JSON payload containing page IDs and trimmed page text, creates a model request with instructions for fact extraction, sends it to the model, and passes the model’s reply to `_parse_facts`. It returns a tuple of validated facts, or an empty tuple if nothing usable came back.

**Call relations**: `FactDeriver._derive` calls this when it needs candidate facts for a page group. `_extract` is the only part of the fact-derivation path that talks directly to the model, and it hands parsing and validation off to `_parse_facts`.

*Call graph*: calls 2 internal fn (complete, _parse_facts); called by 1 (_derive); 3 external calls (__init__, __init__, dumps).


##### `MemoryConsolidator.run`  (lines 181–190)

```
async def run(self) -> None
```

**Purpose**: This is the main periodic consolidation job. It finds older facts, groups related ones, and replaces repeated clusters with a single summary memory.

**Data flow**: It starts with no direct input other than the consolidator’s configured store, embedding client, workspace, and optional model. If no model is configured, it does nothing. Otherwise it reads old facts, groups them by subject, embeds each group, clusters similar facts, and consolidates clusters that are large enough. It returns nothing, but it may create semantic summary records and mark older facts as superseded.

**Call relations**: A scheduler or background worker calls `run` from time to time. `run` coordinates the full consolidation pipeline by calling `_aged_facts`, `_buckets`, `_embed`, `_clusters`, and `_consolidate` in order.

*Call graph*: calls 5 internal fn (_aged_facts, _buckets, _clusters, _consolidate, _embed).


##### `MemoryConsolidator._aged_facts`  (lines 192–221)

```
async def _aged_facts(self) -> tuple[_AgedFact, ...]
```

**Purpose**: This function fetches candidate facts that are old enough to be safely summarized. It deliberately skips facts that were already superseded, so the same facts are not consolidated again.

**Data flow**: It reads the current time, computes a cutoff age, opens a database transaction, and selects fact records in the current workspace that are older than the cutoff and have no `superseded_by` marker. It returns those rows as `_AgedFact` objects containing only the fields needed for consolidation.

**Call relations**: `MemoryConsolidator.run` calls this first to decide what material is available. The rest of the consolidation flow works only with the compact `_AgedFact` records returned here.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryConsolidator._buckets`  (lines 223–232)

```
def _buckets(self, facts: tuple[_AgedFact, ...]) -> tuple[tuple[str, tuple[_AgedFact, ...]], ...]
```

**Purpose**: This function groups old facts by subject so unrelated people, projects, or topics are not summarized together. It also caps each group so one busy subject cannot dominate the job.

**Data flow**: It receives a tuple of aged facts. It builds groups keyed by each fact’s subject, sorts each group by recency, keeps only the newest allowed number of facts per subject, and returns subject-and-facts pairs. It does not write anything.

**Call relations**: `MemoryConsolidator.run` calls this after loading aged facts. The returned buckets are then considered one at a time for embedding and clustering.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._embed`  (lines 234–238)

```
async def _embed(self, facts: tuple[_AgedFact, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: This function turns fact text into embedding vectors, which are number lists that help compare meaning. These vectors let the consolidator find facts that say similar things even if their wording differs.

**Data flow**: It receives facts, trims each fact body to a safe length, and sends the text list to the embedding client. It pairs each returned vector back to the fact ID and returns a dictionary from fact ID to vector.

**Call relations**: `MemoryConsolidator.run` calls this for each subject bucket that has enough facts. The vectors it returns are passed to `_clusters`, which uses them to group similar facts.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._clusters`  (lines 240–259)

```
def _clusters(self, facts: tuple[_AgedFact, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_AgedFact, ...], ...]
```

**Purpose**: This function groups facts that appear close in meaning. It uses a simple greedy method: each fact joins the first existing cluster whose leading fact is similar enough, or starts a new cluster.

**Data flow**: It receives facts and their embedding vectors. It sorts facts newest first, compares each fact’s vector with the first fact in existing clusters using cosine similarity, and builds clusters from those comparisons. It returns a tuple of fact clusters without changing storage.

**Call relations**: `MemoryConsolidator.run` calls this after embeddings are available. `_clusters` relies on `_cosine` for the similarity score, then `run` sends clusters that are large enough to `_consolidate`.

*Call graph*: calls 1 internal fn (_cosine); called by 1 (run).


##### `MemoryConsolidator._consolidate`  (lines 261–288)

```
async def _consolidate(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> None
```

**Purpose**: This function replaces one cluster of related facts with a single semantic summary. It creates the summary and marks the original facts as superseded in the same database transaction.

**Data flow**: It receives a model and a cluster of facts. It asks `_summarize` for a concise summary; if the summary is empty, it stops. Otherwise it creates a new summary ID, inserts a semantic memory item with the summary text and best confidence from the cluster, then updates all original facts so they point to the new summary. It returns nothing, but it changes the database.

**Call relations**: `MemoryConsolidator.run` calls this for each cluster that passes the minimum-size rule. `_consolidate` calls `_summarize` before opening the write transaction, then uses database insert and update operations to make the replacement durable.

*Call graph*: calls 1 internal fn (_summarize); called by 1 (run); 3 external calls (insert, update, uuid4).


##### `MemoryConsolidator._summarize`  (lines 290–299)

```
async def _summarize(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> str
```

**Purpose**: This function asks the language model to compress several related facts into one clear standalone statement. It is the text-writing step of consolidation.

**Data flow**: It receives a model and a cluster of facts. It trims each fact body, packs the facts into compact JSON, creates a model request with summarization instructions, sends it to the model, strips whitespace from the answer, cuts it to the maximum allowed length, and returns the summary text.

**Call relations**: `MemoryConsolidator._consolidate` calls this before writing anything to the database. It is the only model call in the consolidation write path, and its result becomes the body of the new semantic memory item.

*Call graph*: calls 1 internal fn (complete); called by 1 (_consolidate); 3 external calls (__init__, __init__, dumps).


##### `_recency`  (lines 302–303)

```
def _recency(fact: _AgedFact) -> tuple[datetime, UUID]
```

**Purpose**: This small helper gives the code a consistent way to sort facts from older to newer or newer to older. It uses both creation time and ID so ordering stays stable when times match.

**Data flow**: It receives an aged fact and returns a pair made from that fact’s creation time and unique ID. Callers use that pair as a sorting key; the function does not modify anything.

**Call relations**: The bucket and clustering logic use this helper when they need newest-first ordering. It keeps the ordering rule in one place instead of repeating it.


##### `_cosine`  (lines 306–312)

```
def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: This helper measures how similar two embedding vectors are. A higher score means the two pieces of text are closer in meaning according to their embeddings.

**Data flow**: It receives two equal-length tuples of numbers. It computes the cosine similarity by comparing their dot product against their lengths; if either vector has zero length in the mathematical sense, it returns 0.0. The output is a single floating-point similarity score.

**Call relations**: `MemoryConsolidator._clusters` calls this while deciding whether a fact should join an existing cluster. The score is compared with the clustering threshold to decide whether two facts are similar enough.

*Call graph*: called by 1 (_clusters); 1 external calls (sqrt).


##### `_parse_facts`  (lines 315–337)

```
def _parse_facts(text: str) -> tuple[ExtractedFact, ...]
```

**Purpose**: This function safely reads the model’s fact-extraction response. It accepts only a JSON object with a usable facts list and drops malformed entries instead of failing the whole batch.

**Data flow**: It receives raw text from the model. It looks for the first JSON object, tries to decode it, checks that it contains a list named `facts`, and validates each dictionary in that list as an `ExtractedFact`. It returns the valid facts as a tuple, or an empty tuple if the response cannot be used.

**Call relations**: `FactDeriver._extract` calls this right after the model replies. This helper is the safety gate between unpredictable model text and the memory-writing path, so bad model output does not block page-change processing.

*Call graph*: called by 1 (_extract); 1 external calls (JSONDecoder).


### `extensions/memory/ufo_ext_memory/store.py`

`domain_logic` · `request handling and background indexing`

This file gives the memory extension its durable “notebook” and its search machinery. A memory is saved as a row in the `memory_item` table. Saving is deliberately quick: it writes the memory text and metadata, but does not immediately split it into chunks or create embeddings. An embedding is a numeric version of text used for meaning-based search. That slower work is done later by `MemoryIndexer`, like a librarian shelving books after the front desk has accepted them.

When asked to recall something, `MemoryStore` searches in two ways: ordinary word matching and meaning-based vector search. It combines those results with reciprocal-rank fusion, a method that rewards items that appear high in more than one result list. It also checks newly written memories that have not been indexed yet, so fresh facts can still be found. After candidate memories are found, the file reads the real rows back from the database, drops replaced items, applies recency and confidence decay for facts, limits one memory type from crowding out the rest, and turns episodic memories into topic pointers rather than injecting their full text.

The same pattern is used for source pages. `PageIndexer` turns page changes into searchable chunks and keeps a small `mem_page` mirror table so source search can return page subject and date information. Tombstoned pages are removed from both the search index and the mirror.

#### Function details

##### `recall_subjects`  (lines 109–114)

```
def recall_subjects(member_id: UUID | None) -> frozenset[str]
```

**Purpose**: Builds the set of visibility areas that a recall request is allowed to search. If there is a known member, it includes that member’s private memory space plus the shared space; otherwise it only includes shared memory.

**Data flow**: It receives an optional member ID. If the ID exists, it turns it into that member’s subject label and combines it with the shared subject; if not, it returns only the shared subject. The result is a frozen set that later search code can safely use as a filter.

**Call relations**: This is a small helper used before recall or source search begins. It relies on `ufo.sdk.sources.member_subject` to format a member-specific subject, then hands the allowed subjects to memory lookup code.

*Call graph*: 1 external calls (member_subject).


##### `inventory`  (lines 148–203)

```
async def inventory(transaction: Transaction, workspace_id: UUID) -> tuple[MemoryInventoryItem, ...]
```

**Purpose**: Returns a bounded, newest-first listing of stored memories for an operator or explorer view. This is not a search; it shows what is in the memory store and includes useful live signals like age and decay.

**Data flow**: It receives a transaction opener and workspace ID. It reads recent `memory_item` rows for that workspace, takes one current timestamp, and for each row calculates age, half-life, and decay multiplier. It returns `MemoryInventoryItem` objects that combine the stored row fields with those derived values.

**Call relations**: This function is used when someone wants to inspect memory contents directly. While building the listing, it calls `_aware` to normalize dates, `half_life_days` to know whether a memory decays, and `decay_multiplier` so the explorer reports the same weighting that recall would use.

*Call graph*: calls 3 internal fn (_aware, decay_multiplier, half_life_days); 3 external calls (__init__, now, select).


##### `_aware`  (lines 206–207)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Makes sure a datetime has timezone information. This avoids incorrect age calculations when some stored dates are missing an explicit timezone.

**Data flow**: It receives a datetime. If the datetime already has timezone information, it returns it unchanged; otherwise it treats it as UTC and returns a timezone-aware version.

**Call relations**: It is a low-level helper for time math. `inventory` and `decay_multiplier` call it before subtracting dates, so recency calculations are consistent.

*Call graph*: called by 2 (decay_multiplier, inventory); 1 external calls (replace).


##### `_fuse`  (lines 250–273)

```
def _fuse(legs: tuple[tuple[Hit, ...], ...], cosine_leg: tuple[Hit, ...]) -> dict[str, tuple[float, float, str]]
```

**Purpose**: Combines several search result lists into one best score per owning row. It is the shared scoring core for both memory recall and source-page search.

**Data flow**: It receives multiple result lists, called legs, plus the vector-search leg used for cosine similarity. It ranks chunks within each leg, gives chunks credit for appearing high in those lists, keeps the best chunk per owner, and records the best semantic similarity score for that owner. It returns a dictionary from owner ID to fused rank score, cosine score, and matched text snippet.

**Call relations**: `fuse_hits` and `fuse_recall` call this so they do not duplicate the same rank-combining work. It is the place where raw index hits are collapsed from many chunks into one candidate per memory item or source page.

*Call graph*: called by 2 (fuse_hits, fuse_recall); 1 external calls (from_iterable).


##### `fuse_hits`  (lines 276–281)

```
def fuse_hits(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks source-page search hits by combining word-based and meaning-based search results. It produces a short list of page-level matches.

**Data flow**: It receives lexical hits, vector hits, and a limit. It sends both hit lists through `_fuse`, sorts owners by the fused rank score, trims to the requested limit, and returns `Fused` records with owner ID, score, and snippet text.

**Call relations**: `MemoryStore.search_sources` calls this after asking the index for page hits. It hands back ranked page candidates that `search_sources` then checks against the `mem_page` table.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (search_sources); 1 external calls (__init__).


##### `fuse_recall`  (lines 284–301)

```
def fuse_recall(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], tail: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks memory candidates by blending fused search rank with semantic closeness. It also includes a special “tail” list for memories that were saved but not indexed yet.

**Data flow**: It receives lexical hits, vector hits, tail hits, and a limit. It fuses all three lists, normalizes the fused rank score, adds a weighted cosine similarity score from vector search, sorts by the combined score, and returns `Fused` candidates.

**Call relations**: `MemoryStore.recall` calls this after collecting all recall search legs. Its output is not the final answer yet; recall still reads rows from the database, applies decay, enforces type diversity, and rewrites episodic items.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (recall); 1 external calls (__init__).


##### `half_life_days`  (lines 319–325)

```
def half_life_days(item_class: str, memory_kind: str) -> float | None
```

**Purpose**: Says how quickly a memory should lose ranking strength because it is old. Only fact-style memories decay; episodic and semantic memories do not.

**Data flow**: It receives the memory’s class and kind. If the class is not `fact`, it returns `None`; otherwise it looks up the configured half-life for that kind, falling back to the default fact half-life.

**Call relations**: `decay_multiplier` uses this to decide the decay curve for recall scoring, and `inventory` uses it to show operators which half-life applies to each stored memory.

*Call graph*: called by 2 (decay_multiplier, inventory).


##### `decay_multiplier`  (lines 328–340)

```
def decay_multiplier(item_class: str, memory_kind: str, confidence: int, as_of: datetime | None, now: datetime) -> float
```

**Purpose**: Calculates the multiplier that reduces a fact’s relevance as it ages. This keeps old low-confidence facts from ranking as strongly as newer or more trusted facts.

**Data flow**: It receives memory class, kind, confidence, the date the information was current, and the current time. It finds the half-life, normalizes the timestamp, computes age in days, and returns a multiplier based on confidence and age. For non-decaying memories or missing dates, it returns 1.0.

**Call relations**: `decay_factor` calls this during recall, and `inventory` calls it for display. It calls `_aware` for safe time math and `half_life_days` for the correct decay schedule.

*Call graph*: calls 2 internal fn (_aware, half_life_days); called by 2 (decay_factor, inventory).


##### `decay_factor`  (lines 343–346)

```
def decay_factor(item: Recalled, now: datetime) -> float
```

**Purpose**: Applies the standard decay calculation to a recalled memory item. It is a convenience wrapper used while ranking recall results.

**Data flow**: It receives a `Recalled` item and the current time. It chooses the item’s `as_of` date when available, otherwise its creation date, then passes the item’s class, kind, confidence, and date into `decay_multiplier`. It returns the multiplier used to adjust the item’s score.

**Call relations**: `MemoryStore.recall` calls this after candidate memories have been enriched from the database. It connects the stored memory fields to the shared decay formula.

*Call graph*: calls 1 internal fn (decay_multiplier); called by 1 (recall).


##### `enforce_type_diversity`  (lines 349–367)

```
def enforce_type_diversity(rows: tuple[Recalled, ...], limit: int) -> tuple[Recalled, ...]
```

**Purpose**: Prevents one class of memory from filling the whole recall result list. This helps a user see a more balanced set of facts, episodic pointers, and semantic items when possible.

**Data flow**: It receives already-ranked recalled rows and a result limit. It walks the rows in order, keeps only a capped number per item class at first, saves overflow items for later, then backfills from the overflow if there is still room. It returns the final trimmed tuple.

**Call relations**: `MemoryStore.recall` calls this after applying score decay. It is one of the last shaping steps before episodic items are rewritten and returned.

*Call graph*: called by 1 (recall).


##### `as_topic_pointer`  (lines 370–380)

```
def as_topic_pointer(item: Recalled, index: int) -> Recalled
```

**Purpose**: Turns an episodic memory result into a short topic pointer instead of returning its full body text. This makes episodic memory act like a breadcrumb to explore, not automatic context to inject verbatim.

**Data flow**: It receives a recalled item and its position in the final list. If the item is not episodic, it returns it unchanged. If it is episodic, it creates a copy with a short body such as “Memory topic 1...” and marks its recall mode as `topic`.

**Call relations**: `MemoryStore.recall` calls this as the final transformation on diversified results. It uses `dataclasses.replace` to copy the item without mutating the original.

*Call graph*: called by 1 (recall); 1 external calls (replace).


##### `MemoryStore.commit`  (lines 403–445)

```
async def commit(self, write: MemoryWrite) -> None
```

**Purpose**: Saves one memory item to the database without doing expensive indexing work immediately. This keeps writes fast and leaves indexing to the background job.

**Data flow**: It receives a `MemoryWrite` object. It creates a stable ID from workspace, subject, item class, and body, then inserts the row. If the same memory already exists, it updates metadata such as confidence, source, and date instead of creating a duplicate. The row remains due for indexing if it has no embedding digest.

**Call relations**: External memory-writing flows call this on a `MemoryStore`. It does not call the indexer; instead, `MemoryIndexer.run` later finds rows with missing embedding digests and indexes them.

*Call graph*: 1 external calls (uuid5).


##### `MemoryStore.recall`  (lines 447–473)

```
async def recall(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None) -> tuple[Recalled, ...]
```

**Purpose**: Finds memories relevant to a query and returns them in a useful order. It combines search relevance, freshness, confidence, memory-type balance, and special episodic handling.

**Data flow**: It receives query text, allowed subjects, a limit, and optional date bounds. It gets lexical and vector search legs from `_legs`, gets unindexed fresh-memory matches from `_untail_leg`, fuses those with `fuse_recall`, and reads real database rows through `_enrich`. It then multiplies scores by decay, sorts, enforces type diversity, converts episodic items to topic pointers, and returns recalled memories.

**Call relations**: This is the main read path for memory recall. It orchestrates helper functions in this file: `_legs`, `_untail_leg`, `fuse_recall`, `_enrich`, `decay_factor`, `enforce_type_diversity`, and `as_topic_pointer`.

*Call graph*: calls 7 internal fn (_enrich, _legs, _untail_leg, as_topic_pointer, decay_factor, enforce_type_diversity, fuse_recall); 2 external calls (replace, now).


##### `MemoryStore.search_sources`  (lines 475–520)

```
async def search_sources(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None) -> tuple[SourceMatch, ...]
```

**Purpose**: Searches synced source pages and returns matching snippets with page metadata. It lets the memory extension search source documents as well as stored memory facts.

**Data flow**: It receives a query, allowed subjects, a limit, and optional date bounds. It asks `_legs` for lexical and vector page hits, combines them with `fuse_hits`, then reads matching page rows from `mem_page` to get subject and creation date. It returns `SourceMatch` objects for candidates that still exist and fit the date window.

**Call relations**: This is the source-page counterpart to `recall`. It calls `_legs` to talk to the index and `fuse_hits` to rank hits, then relies on the `mem_page` mirror kept current by `PageIndexer._apply`.

*Call graph*: calls 2 internal fn (_legs, fuse_hits); 3 external calls (__init__, select, UUID).


##### `MemoryStore._legs`  (lines 522–530)

```
async def _legs(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[tuple[Hit, ...], tuple[Hit, ...]]
```

**Purpose**: Runs the two normal search methods for a query: word matching and meaning-based vector matching. It is shared by memory recall and source-page search.

**Data flow**: It receives query text, allowed subjects, an owner kind, and a limit. It first tries to embed the query with `_embed_query`, then asks the index backend for lexical hits. If an embedding exists, it also asks for vector hits; if not, the vector leg is empty. It returns both hit tuples.

**Call relations**: `MemoryStore.recall` and `MemoryStore.search_sources` both call this before fusing results. It delegates embedding failure handling to `_embed_query` so callers can still get lexical results when embedding is unavailable.

*Call graph*: calls 1 internal fn (_embed_query); called by 2 (recall, search_sources).


##### `MemoryStore._untail_leg`  (lines 532–575)

```
async def _untail_leg(self, query: str, subjects: frozenset[str], limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches the newest unindexed memory rows directly in the database. This makes a freshly committed memory recallable before the background indexer has processed it.

**Data flow**: It receives query text, allowed subjects, and a limit. It splits the query into lowercase terms, reads recent memory rows whose embedding digest is still missing and that are not superseded, counts term matches in each body, creates index-like `Hit` objects for rows with matches, sorts them by match count, and returns the best ones.

**Call relations**: `MemoryStore.recall` calls this alongside the normal index legs. Once `MemoryIndexer._index_item` stamps an embedding digest on a row, that row no longer appears in this tail search and is served by the real index instead.

*Call graph*: called by 1 (recall); 3 external calls (__init__, split, select).


##### `MemoryStore._embed_query`  (lines 577–585)

```
async def _embed_query(self, query: str) -> tuple[float, ...]
```

**Purpose**: Turns a recall or search query into an embedding vector when possible. If embedding fails, it safely falls back so word search can still work.

**Data flow**: It receives query text. If the query is blank, it returns an empty tuple. Otherwise it asks the embed backend for one vector; if that call raises an error, it logs a warning and returns an empty tuple. On success, it returns the first vector.

**Call relations**: `MemoryStore._legs` calls this before vector search. Its failure-tolerant behavior means `recall` and `search_sources` can continue with lexical search even if the embedding service is down.

*Call graph*: called by 1 (_legs).


##### `MemoryStore._enrich`  (lines 587–639)

```
async def _enrich(self, fused: tuple[Fused, ...], start: datetime | None, end: datetime | None) -> tuple[Recalled, ...]
```

**Purpose**: Turns fused candidate IDs into full recalled memory records from the database. It also removes candidates that should not be served anymore.

**Data flow**: It receives fused candidates and optional date bounds. If there are no candidates, it returns nothing. Otherwise it loads matching `memory_item` rows, excludes superseded rows and rows outside the date window, then rebuilds results in fused order as `Recalled` objects with body, subject, source, confidence, and dates.

**Call relations**: `MemoryStore.recall` calls this after `fuse_recall`. It is the gate where search-index candidates are checked against the durable database before final ranking and return.

*Call graph*: called by 1 (recall); 3 external calls (__init__, select, UUID).


##### `store_for`  (lines 642–652)

```
def store_for(ext: ExtensionContext) -> MemoryStore
```

**Purpose**: Builds a ready-to-use `MemoryStore` from the extension context. It also fails early if the required index or embedding backends were not wired in.

**Data flow**: It receives an `ExtensionContext`. It checks for an index backend and an embedding backend, raises an error if either is missing, and otherwise creates a `MemoryStore` with those backends, the scoped transaction opener, and the current workspace ID.

**Call relations**: Setup code uses this to obtain the main memory workflow object. It connects the broader extension context to the methods that commit, recall, and search sources.

*Call graph*: 1 external calls (__init__).


##### `MemoryIndexer.run`  (lines 668–670)

```
async def run(self) -> None
```

**Purpose**: Processes a batch of memory items that still need indexing. This is the background path that turns saved text into searchable chunks and embeddings.

**Data flow**: It asks `_claim_due` for rows that are due and safely claimed. For each claimed item, it calls `_index_item`, which writes chunks to the index and stamps the row as indexed. It does not return a value; it changes index contents and database row state.

**Call relations**: A scheduled or worker loop calls this periodically. It coordinates `_claim_due` and `_index_item` so memory writes stay fast while indexing catches up asynchronously.

*Call graph*: calls 2 internal fn (_claim_due, _index_item).


##### `MemoryIndexer._claim_due`  (lines 672–704)

```
async def _claim_due(self) -> tuple[MemoryItem, ...]
```

**Purpose**: Finds and claims memory rows that need embedding work. The claim prevents overlapping indexer runs from doing the same work at the same time.

**Data flow**: It computes a lease cutoff time, selects rows whose embedding digest is missing and whose claim is absent or expired, and limits the batch size. Inside one transaction, it uses database locking where available, stamps `embedding_claimed_at` on selected rows, and returns them as `MemoryItem` objects.

**Call relations**: `MemoryIndexer.run` calls this before indexing. The rows it returns are then passed to `_index_item`; rows already claimed by another worker are skipped until their lease expires.

*Call graph*: called by 1 (run); 5 external calls (now, timedelta, or_, select, update).


##### `MemoryIndexer._index_item`  (lines 706–726)

```
async def _index_item(self, item: MemoryItem) -> None
```

**Purpose**: Indexes one memory item and marks it as no longer due. This is where the memory body is split, embedded, and written to the search backend.

**Data flow**: It receives a claimed `MemoryItem`. It calls `chunk_embed_upsert` with the item’s ID, subject, and body so the index backend stores searchable chunks. It then computes a SHA-256 digest of the body and updates the database row with that digest, clears the claim timestamp, and refreshes `updated_at`.

**Call relations**: `MemoryIndexer.run` calls this for each row claimed by `_claim_due`. Its database update is what removes the item from future due batches and from the unindexed tail search used by recall.

*Call graph*: called by 1 (run); 3 external calls (sha256, update, chunk_embed_upsert).


##### `PageIndexer.apply`  (lines 744–746)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Applies a delivered batch of source-page changes to the memory extension’s page index. It is the batch-level entry for page indexing work.

**Data flow**: It receives a tuple of `PageChange` objects. It loops through them in order and passes each one to `_apply`. It returns nothing, but each change may update index chunks and the `mem_page` mirror table.

**Call relations**: The core page-change runner calls this after it has gathered changes. This method keeps batching simple and delegates the actual per-page behavior to `_apply`.

*Call graph*: calls 1 internal fn (_apply).


##### `PageIndexer._apply`  (lines 748–779)

```
async def _apply(self, change: PageChange) -> None
```

**Purpose**: Applies one source-page change: either remove a deleted page or index an active page. This keeps source search aligned with the latest synced pages.

**Data flow**: It receives one `PageChange`. If the change is a tombstone, it deletes that page’s chunks from the index and removes its `mem_page` row. Otherwise it chunks and embeds the page body, upserts those chunks into the index, then updates or inserts the mirror row with page ID, workspace, subject, and creation date.

**Call relations**: `PageIndexer.apply` calls this for each change in a batch. `MemoryStore.search_sources` later depends on the chunks and `mem_page` rows created here, and tombstone handling ensures deleted pages do not appear in search results.

*Call graph*: called by 1 (apply); 5 external calls (__init__, delete, insert, update, chunk_embed_upsert).


### Knowledge graph extraction
The knowledge graph extension registers graph search and hooks, then extracts entities and relationships from pages for later traversal.

### `extensions/knowledge_graph/ufo_ext_knowledge_graph/__init__.py`

`other` · `import/package discovery`

This is an empty Python package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a folder: it does not add any documents itself, but it makes the folder recognizable to the filing system.

For this extension, the package name is `ufo_ext_knowledge_graph`. Other parts of the project can import modules from this folder because this file exists. Without it, depending on the Python version and packaging setup, imports might fail or the extension might not be discovered in the expected way.

There are no functions, classes, settings, or startup actions here. Its value is structural rather than behavioral: it helps define the shape of the codebase and makes the knowledge graph extension available as a normal Python package.


### `extensions/knowledge_graph/ufo_ext_knowledge_graph/manifest.py`

`orchestration` · `startup registration, then prompt submission and page-change processing`

This file is like the sign-up sheet for the knowledge-graph extension. Without it, the rest of UFO would not know that the extension has a `graph_search` tool, that it can add relevant graph facts before a user message is sent to the model, or that it should rebuild graph data when source pages change.

The graph is a structured map of entities and relationships, such as people, companies, topics, and typed links between them. The search tool starts from a named entity and walks a limited number of relationship steps, then returns readable lines with citations to the source pages the facts came from. This helps answer “who is connected to whom?” questions that ordinary text search may miss.

The prompt hook runs when a user submits a message. It tries to find graph relations relevant to that message and inject them as extra context for the model. Importantly, it is deliberately best-effort: it has a short timeout and swallows errors, because a slow graph lookup should not block the user’s turn.

The page-change hook is the background intake path. When the core system reports changed pages, this file hands them to the graph extractor, which turns page content into graph nodes and edges outside the normal write path.

#### Function details

##### `graph_search_handler`  (lines 71–88)

```
async def graph_search_handler(ctx: ToolContext, args: GraphSearchInput) -> ToolResult
```

**Purpose**: This is the actual worker behind the `graph_search` tool. Given an entity name, it looks up nearby graph relationships, optionally limited to certain relationship types, and returns them as readable text with source citations.

**Data flow**: It receives the tool context and a `GraphSearchInput` object containing the entity name, hop count, optional edge-type filter, and optional user-facing description. It reads the extension’s database transaction and workspace id from the context, converts requested edge-type names into the graph’s allowed internal types, asks `GraphStore` to traverse outward from the entity for the current audience member, and formats the returned subgraph. It outputs a `ToolResult` containing either the formatted graph relations or a friendly message saying none were found.

**Call relations**: The UFO tool system calls this when the model or runtime invokes `graph_search`. It builds a `GraphStore`, uses `graph_subjects` to scope the search to the relevant audience member, uses `to_edge_type` to validate and normalize filters, asks the store for the traversal, and passes the result through `render_subgraph` before wrapping it in `TextContent` and `ToolResult`.

*Call graph*: 6 external calls (__init__, __init__, __init__, graph_subjects, render_subgraph, to_edge_type).


##### `graph_context_hook`  (lines 91–110)

```
async def graph_context_hook(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook tries to add useful graph facts to a user’s prompt before the model responds. It is designed not to get in the way: if lookup is slow, fails, or finds nothing, it simply adds nothing.

**Data flow**: It receives a hook context and first checks that the event payload is a user prompt. If so, it starts a short timeout, creates a `GraphStore` from the extension transaction and workspace id, asks for graph context related to the prompt text and audience member, and formats the returned subgraph. It outputs an `InjectContext` containing the graph relations when there are lines to add; otherwise, or after any error, it returns `None` and changes nothing.

**Call relations**: The core hook system calls this during the `user_prompt_submit` event. Inside that moment, it uses `asyncio.timeout` to stay below the hook deadline, calls `graph_subjects` to keep the lookup scoped to the audience member, calls the graph store for relevant context, and uses `render_subgraph` to turn the result into text that `InjectContext` can place into the model’s prompt.

*Call graph*: 5 external calls (__init__, __init__, timeout, graph_subjects, render_subgraph).


##### `extract_graph`  (lines 113–125)

```
async def extract_graph(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook turns changed source pages into graph data. It lets the extension update its nodes and relationships after page changes, instead of doing that work inline while pages are being written.

**Data flow**: It receives a hook context and checks whether the payload is a batch of page changes. If it is, it creates a `GraphExtractor` using the current transaction, workspace id, and model, then applies the extractor to the delivered page changes. It returns `None`; its effect is the updated graph data written through the extractor.

**Call relations**: The core page-change runner calls this for the `page_change` event when it has a batch ready for this extension. The function does not own the batch loop or cursor; it only takes the batch it was given, constructs `GraphExtractor`, and hands the page changes off so extraction can produce graph nodes and typed edges.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 128–152)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the extension declaration that UFO reads to discover what the knowledge-graph extension can do. It registers the graph search tool and the two hooks that run on prompt submission and page changes.

**Data flow**: It takes no input. It packages the extension name, version, one `ToolDef` for `graph_search`, and two `HookSpec` entries into a `Manifest` object. The returned manifest is what the host system uses to wire this file’s handlers into the wider application.

**Call relations**: The extension loading process calls this at registration time. It creates the `ToolDef` that points to `graph_search_handler`, creates hook specifications that point to `graph_context_hook` and `extract_graph`, and returns a `Manifest` so the core UFO runtime knows when and how to call them.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/knowledge_graph/ufo_ext_knowledge_graph/store.py`

`domain_logic` · `page change indexing and query handling`

This file is the heart of the knowledge-graph extension. Its job is to turn ordinary page text into a map of things and how they relate, like “Alice works_at Acme” or “Project X mentions roadmap.” Without it, the extension would have no durable graph to search, and page changes would not become reusable knowledge.

It owns two database tables: one for entities, such as people, companies, topics, and organizations, and one for edges, which are typed relationships between entities. The `GraphExtractor` is the write side. When a page changes, it checks whether that exact version was already processed. If not, it parses simple markdown patterns such as `[[links]]`, `[[works_at::Acme]]`, `@mentions`, `#tags`, and URLs. If a model is available, it also asks the model to extract extra typed relationships from prose. It then creates or updates entity rows and writes edge rows tied to the page digest, so old edges can be replaced when the page changes.

The `GraphStore` is the read side. It finds matching entity nodes, then walks a limited number of relationship steps, much like following roads on a map. It deliberately caps the search size so one query cannot pull back the whole graph.

#### Function details

##### `to_edge_type`  (lines 160–165)

```
def to_edge_type(raw: str) -> EdgeType
```

**Purpose**: Checks that a relationship type is one of the graph’s approved types, such as `mentions` or `works_at`. This prevents misspelled or invented relationship names from being saved or queried as if they were valid.

**Data flow**: It receives a raw text value for an edge type. It compares that value with the fixed set of allowed edge types. If the value is allowed, it returns it as a trusted edge type; if not, it raises an error so the bad value is stopped immediately.

**Call relations**: Both model-extracted relations and database edge writes pass through this gate. `GraphExtractor._tier_b` uses it to validate model output, and `GraphExtractor._record_edge` uses it before persisting any edge.

*Call graph*: called by 2 (_record_edge, _tier_b); 2 external calls (__init__, cast).


##### `normalize_name`  (lines 192–195)

```
def normalize_name(name: str) -> str
```

**Purpose**: Turns a name into a stable lookup key by trimming it, lowercasing it, and collapsing repeated spaces. This lets names like “Sam  Altman” and “sam altman” point to the same graph node.

**Data flow**: It receives a display name as text. It cleans the spacing and casing. It returns the normalized version used for matching and deduplication, while the original display name can still be stored separately.

**Call relations**: The parser uses it to avoid recording the same reference twice. The extractor uses it when creating entity IDs, and the store uses it when resolving a user query or finding entities mentioned in incoming text.

*Call graph*: called by 4 (_upsert_entity, _resolve, _seed_from_text, record); 1 external calls (sub).


##### `graph_subjects`  (lines 198–203)

```
def graph_subjects(member_id: UUID | None) -> frozenset[str]
```

**Purpose**: Chooses which subject areas a graph query should read from. A subject is a scope, like a member’s private space or the shared workspace.

**Data flow**: It receives an optional member ID. If there is a member, it returns both that member’s subject and the shared subject; if not, it returns only the shared subject.

**Call relations**: This helper prepares the subject set used by graph reads. It calls the shared source helper that turns a member ID into that member’s subject name.

*Call graph*: 1 external calls (member_subject).


##### `parse_page`  (lines 248–284)

```
def parse_page(body: str) -> ParsedPage
```

**Purpose**: Extracts obvious entity references from markdown without using an AI model. It gives the graph a reliable baseline from links, mentions, tags, and URLs.

**Data flow**: It receives the full page body. It looks for a first-level heading to use as the page’s title, scans wikilinks first, then scans the remaining text for `@mentions`, `#tags`, and URLs. It returns a parsed page containing the title and a deduplicated list of references.

**Call relations**: When `GraphExtractor._apply` sees a changed page that needs processing, it calls this function before materializing database rows. The helper `parse_page.record` does the repeated work of cleaning and deduplicating each found reference.

*Call graph*: called by 1 (_apply); 1 external calls (__init__).


##### `parse_page.record`  (lines 260–267)

```
def record(edge_type: str, name: str, entity_type: str) -> None
```

**Purpose**: Adds one found reference to the page parse result, but only if it is meaningful and not already seen. It is the small checkpoint that keeps duplicate links from becoming duplicate edges.

**Data flow**: It receives an edge type, a target name, and an entity type. It trims the name, ignores empty names, normalizes the name for comparison, and appends a `Ref` only when that same kind of reference has not already been recorded.

**Call relations**: This nested helper is used inside `parse_page` each time the parser finds a wikilink, mention, tag, or URL. It relies on `normalize_name` so duplicates with different spacing or casing still collapse to one reference.

*Call graph*: calls 1 internal fn (normalize_name); 1 external calls (__init__).


##### `render_subgraph`  (lines 287–302)

```
def render_subgraph(subgraph: Subgraph) -> tuple[str, ...]
```

**Purpose**: Turns a returned subgraph into readable text lines. Each line describes one relationship and cites the page it came from.

**Data flow**: It receives a `Subgraph` containing nodes and edges. It matches each edge’s start and end IDs back to node details, skips edges whose nodes are missing, and returns text lines like `Source -edge_type-> Target [page ...]`.

**Call relations**: This is a presentation helper for graph results. Query tools and context hooks can use the same rendering so graph output is shown consistently.


##### `GraphExtractor.apply`  (lines 320–322)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Processes a batch of page changes for graph indexing. It is the public entry point for the write side of this file.

**Data flow**: It receives a tuple of page changes. It walks through them one at a time and passes each change to `_apply`. It does not return graph data; its effect is to update the graph tables.

**Call relations**: The page-change runner calls this when it has delivered a batch to the knowledge-graph extension. This method then delegates each individual change to `GraphExtractor._apply`.

*Call graph*: calls 1 internal fn (_apply).


##### `GraphExtractor._apply`  (lines 324–338)

```
async def _apply(self, change: PageChange) -> None
```

**Purpose**: Decides what to do with one changed page. It either soft-deletes that page’s edges, skips unchanged content, or rebuilds the graph facts from the page.

**Data flow**: It receives one `PageChange`. If the page is tombstoned, it marks all edges from that page as tombstoned in the database. Otherwise, it checks whether the same digest was already extracted; if not, it parses the page and materializes the results.

**Call relations**: `GraphExtractor.apply` calls this for each change. It calls `_already_extracted` to avoid duplicate work, `parse_page` to read deterministic references, and `_materialize` to write entities and edges.

*Call graph*: calls 3 internal fn (_already_extracted, _materialize, parse_page); called by 1 (apply); 1 external calls (update).


##### `GraphExtractor._already_extracted`  (lines 340–354)

```
async def _already_extracted(self, change: PageChange) -> bool
```

**Purpose**: Checks whether this exact page version has already produced graph edges. This keeps repeated delivery of the same page change from doing unnecessary database writes.

**Data flow**: It receives a page change with a page ID and digest. It queries the edge table for a non-tombstoned edge from that page with the same digest. It returns `true` if such an edge exists, otherwise `false`.

**Call relations**: `GraphExtractor._apply` calls this before doing parsing and writes. If it returns true, the extractor stops early for that page.

*Call graph*: called by 1 (_apply); 1 external calls (select).


##### `GraphExtractor._materialize`  (lines 356–383)

```
async def _materialize(self, change: PageChange, parsed: ParsedPage) -> None
```

**Purpose**: Writes the graph representation of one live page into the database. It creates the page’s anchor node, creates or reuses target nodes, records edges, and removes stale edges from older page versions.

**Data flow**: It receives a page change and the deterministic parse of the page. If a model is configured, it first asks `_tier_b` for extra relations. Then, inside one database transaction, it upserts the page anchor entity, upserts each referenced target entity, records deterministic and model-derived edges, and deletes edges from older digests for the same page.

**Call relations**: `GraphExtractor._apply` calls this after deciding a page needs extraction. It coordinates `_tier_b`, `_upsert_entity`, and `_record_edge`, making it the main write-orchestration step inside the extractor.

*Call graph*: calls 3 internal fn (_record_edge, _tier_b, _upsert_entity); called by 1 (_apply); 1 external calls (delete).


##### `GraphExtractor._tier_b`  (lines 385–423)

```
async def _tier_b(self, model: ModelAccess, body: str) -> tuple[ExtractedRelation, ...]
```

**Purpose**: Asks the configured AI model to extract extra typed relationships from the page’s prose. This is optional and only adds to the deterministic parser’s results.

**Data flow**: It receives a model access object and the page body. It sends a bounded model request that forces a structured tool response, validates that response, rejects unknown edge types, drops empty targets, and returns the accepted extracted relations. If the model call or validation fails, it logs a warning and returns no relations.

**Call relations**: `GraphExtractor._materialize` calls this before opening the database write transaction, so the database is not held open while waiting for the model. It uses `to_edge_type` to make sure model output cannot invent relationship types.

*Call graph*: calls 2 internal fn (turn, to_edge_type); called by 1 (_materialize); 2 external calls (__init__, __init__).


##### `GraphExtractor._upsert_entity`  (lines 425–464)

```
async def _upsert_entity(self, connection: AsyncConnection, subject: str, name: str, entity_type: str, fill: bool) -> UUID
```

**Purpose**: Creates or updates one entity node and returns its stable ID. It is used both for page anchor nodes and for referenced target nodes.

**Data flow**: It receives a database connection, subject, display name, entity type, and a flag saying whether this reference fills in a real page-defined entity. It normalizes the name, builds a deterministic UUID from the workspace, subject, type, and normalized name, and inserts the row. If `fill` is true, an existing stub is updated into a filled entity; if false, existing entities are left alone.

**Call relations**: `GraphExtractor._materialize` calls this for the page itself and for every referenced target. It calls `normalize_name` so repeated references resolve to the same node and uses the active SQL dialect to choose the right upsert statement.

*Call graph*: calls 1 internal fn (normalize_name); called by 1 (_materialize); 2 external calls (execute, uuid5).


##### `GraphExtractor._record_edge`  (lines 466–513)

```
async def _record_edge(self, connection: AsyncConnection, change: PageChange, from_entity: UUID, to_entity: UUID, raw_edge_type: str, confidence: float=DETERMINISTIC_CONFIDENCE) -> None
```

**Purpose**: Creates or updates one relationship edge between two entities. It ties that edge to the source page and the page digest that produced it.

**Data flow**: It receives a database connection, the page change, the source entity ID, target entity ID, raw edge type, and confidence score. It validates the edge type, builds a deterministic edge ID, and upserts the edge row with the current digest, confidence, and tombstone set to false.

**Call relations**: `GraphExtractor._materialize` calls this after both endpoint entities exist. It uses `to_edge_type` as the final validation gate before any relationship reaches the graph table.

*Call graph*: calls 1 internal fn (to_edge_type); called by 1 (_materialize); 2 external calls (execute, uuid5).


##### `GraphStore.traverse`  (lines 525–533)

```
async def traverse(self, query: str, subjects: frozenset[str], hops: int, edge_types: frozenset[str]) -> Subgraph
```

**Purpose**: Runs a direct graph search from a user-provided name. It finds matching entity nodes and then walks nearby relationships.

**Data flow**: It receives a query string, allowed subjects, hop count, and optional edge-type filter. It resolves the query text to seed node IDs, expands outward from those seeds, and returns a `Subgraph` containing visited nodes and edges.

**Call relations**: This is the main read method for a graph search tool. It first calls `_resolve` to find starting nodes, then calls `_expand` to collect the neighborhood around them.

*Call graph*: calls 2 internal fn (_expand, _resolve).


##### `GraphStore.context_for`  (lines 535–539)

```
async def context_for(self, text: str, subjects: frozenset[str], hops: int) -> Subgraph
```

**Purpose**: Finds graph context relevant to a piece of text, such as an incoming conversation turn. Instead of searching one exact name, it seeds from any known entity name that appears in the text.

**Data flow**: It receives text, allowed subjects, and a hop count. It finds entity IDs mentioned in the text, expands outward from them, and returns the resulting subgraph.

**Call relations**: Inbound context-building code can call this before responding to a user. It delegates seed finding to `_seed_from_text` and graph walking to `_expand`.

*Call graph*: calls 2 internal fn (_expand, _seed_from_text).


##### `GraphStore._resolve`  (lines 541–555)

```
async def _resolve(self, query: str, subjects: frozenset[str]) -> frozenset[UUID]
```

**Purpose**: Looks up graph entities whose normalized name exactly matches a query. This turns a human search term into starting node IDs.

**Data flow**: It receives a query and subject set. It normalizes the query, returns nothing if the query or subjects are empty, otherwise queries the entity table for matching names in the current workspace and subjects. It returns the matching IDs.

**Call relations**: `GraphStore.traverse` calls this before expanding the graph. It uses `normalize_name` so search terms match the same cleanup rules used during extraction.

*Call graph*: calls 1 internal fn (normalize_name); called by 1 (traverse); 1 external calls (select).


##### `GraphStore._seed_from_text`  (lines 557–574)

```
async def _seed_from_text(self, text: str, subjects: frozenset[str]) -> frozenset[UUID]
```

**Purpose**: Finds known entities whose names appear inside a larger text. This lets the system attach graph context to a message without the user naming a formal search query.

**Data flow**: It receives text and subject set. It normalizes the text, fetches a capped list of recently updated candidate entities, and returns the IDs of candidates whose normalized names appear as whole padded phrases in the text.

**Call relations**: `GraphStore.context_for` calls this to choose starting points for context expansion. It uses `normalize_name` for the same matching behavior used elsewhere.

*Call graph*: calls 1 internal fn (normalize_name); called by 1 (context_for); 1 external calls (select).


##### `GraphStore._expand`  (lines 576–592)

```
async def _expand(self, seeds: frozenset[UUID], subjects: frozenset[str], hops: int, edge_types: frozenset[str]) -> Subgraph
```

**Purpose**: Walks outward from starting nodes for a limited number of steps. This is the shared graph-walking engine behind both direct search and context lookup.

**Data flow**: It receives seed IDs, subjects, a hop count, and optional edge-type filters. It keeps track of visited nodes and collected edges, repeatedly asks `_hop` for the next ring of neighbors, stops at configured limits, then fetches node details and returns a `Subgraph`.

**Call relations**: Both `GraphStore.traverse` and `GraphStore.context_for` call this after they have seed nodes. It calls `_hop` for each expansion step and `_nodes` at the end to turn IDs into readable node records.

*Call graph*: calls 2 internal fn (_hop, _nodes); called by 2 (context_for, traverse); 1 external calls (__init__).


##### `GraphStore._hop`  (lines 594–639)

```
async def _hop(self, frontier: set[UUID], subjects: frozenset[str], edge_types: frozenset[str], edges: dict[UUID, TraversedEdge], visited: set[UUID]) -> set[UUID]
```

**Purpose**: Performs one step of graph expansion from the current frontier of nodes. A frontier is the current outer edge of the search, like the next set of intersections to inspect on a map.

**Data flow**: It receives the current frontier, subjects, edge-type filters, the accumulated edge dictionary, and the visited node set. It queries non-tombstoned edges touching the frontier, records each edge, adds newly discovered endpoint nodes to `visited`, and returns the next frontier.

**Call relations**: `GraphStore._expand` calls this once per hop until it reaches the hop limit or result caps. It creates `TraversedEdge` records from database rows so the final subgraph can cite each relationship.

*Call graph*: called by 1 (_expand); 3 external calls (__init__, or_, select).


##### `GraphStore._nodes`  (lines 641–663)

```
async def _nodes(self, ids: set[UUID]) -> tuple[EntityNode, ...]
```

**Purpose**: Fetches readable details for a set of entity IDs. It turns the graph walk’s internal IDs back into names, types, and stub status.

**Data flow**: It receives a set of UUIDs. If the set is empty, it returns no nodes; otherwise it queries the entity table for those IDs in the current workspace and builds `EntityNode` objects from the rows.

**Call relations**: `GraphStore._expand` calls this after all hops are complete. The returned nodes are paired with the collected edges to form the final `Subgraph`.

*Call graph*: called by 1 (_expand); 2 external calls (__init__, select).


### Page-change alerts
Page alert watches connect changed workspace pages to follow-up messages in the conversations that asked to monitor them.

### `extensions/page_alerts/ufo_ext_page_alerts/__init__.py`

`other` · `import/package discovery`

This is a very small package marker file. In Python, an `__init__.py` file tells the interpreter that a folder should be treated as an importable package, meaning other parts of the project can refer to it by name. Here, the package is for a “page alerts” extension, which likely adds alert-related behavior to pages elsewhere in the system.

The file does not define any functions, classes, settings, or startup behavior. Its only content is a docstring: a short text note saying “Page alerts extension.” Think of it like a label on a folder. The real work of the extension is expected to live in other files inside this package, but this file makes the folder recognizable and importable as a package.

Without this file, depending on the Python version and packaging setup, imports or extension discovery could become less clear or fail in environments that expect a traditional package layout.


### `extensions/page_alerts/ufo_ext_page_alerts/alerts.py`

`domain_logic` · `tool calls and page-change hook`

This file solves a practical problem: people may want to know when a synced document changes in a way that matters to them, without manually checking every page. A user can ask the assistant to watch for a topic, such as “pricing changes” or “security policy updates.” The file saves that watch with the current conversation and agent, so future alerts know exactly where to go.

When the system later receives a batch of changed pages, this file checks each live page change against each saved watch. It does not simply search for exact words. Instead, it asks the configured language model a very small yes-or-no question: does this document concern the watch topic? The page text is clipped to a safe length, and the model is told to answer only “MATCH” or “NO,” keeping the check bounded and cheap.

If the model says there is a match, the file asks the extension system to start an alerting turn in the original conversation. An idempotency key, like a receipt number, is based on the watch and page digest so the same replayed page-change batch does not create duplicate alerts. It also ignores deleted pages, called tombstones, because there is no page body to classify.

#### Function details

##### `_slug`  (lines 38–42)

```
def _slug(raw: str) -> str
```

**Purpose**: Turns a user-provided watch name into a safe storage name. It makes names lowercase, replaces non-letter-or-number runs with dashes, and rejects names that contain no usable letters or digits.

**Data flow**: It receives raw text from a watch name or topic. It uses a regular expression to clean that text into a simple dash-separated identifier. It returns the cleaned name, or raises an error if nothing meaningful remains.

**Call relations**: When a user creates a watch, watch_pages calls this so the watch can be stored under a predictable key. When a user cancels a watch, cancel_page_watch calls it so the same kind of key can be found again.

*Call graph*: called by 2 (cancel_page_watch, watch_pages); 1 external calls (sub).


##### `watch_pages`  (lines 45–66)

```
async def watch_pages(ctx: ToolContext, args: WatchPagesInput) -> ToolResult
```

**Purpose**: Creates a new page watch from a chat tool call. It records what topic to watch for and binds the watch to the current conversation and agent so later alerts return to the right place.

**Data flow**: It receives the tool context, which includes the current turn and extension storage, plus the requested topic and optional name. It checks that extension context is available, cleans the watch name, saves the topic, conversation ID, and agent ID in the extension store, then returns a short confirmation message to the user.

**Call relations**: This is called when a member asks the assistant to watch synced pages. It uses _slug to create the storage key, then writes the watch record that on_page_change will later read when page updates arrive.

*Call graph*: calls 1 internal fn (_slug); 2 external calls (__init__, __init__).


##### `list_page_watches`  (lines 69–79)

```
async def list_page_watches(ctx: ToolContext, args: ListPageWatchesInput) -> ToolResult
```

**Purpose**: Shows the user the page watches that are currently saved. It gives a simple list of watch names and their topics, or says there are none.

**Data flow**: It receives the tool context and an empty input object. It checks for extension context, reads all stored records whose keys start with the watch prefix, turns each valid record into a display line, and returns those lines as text.

**Call relations**: This is called when a member asks what page watches exist. It relies on _watch_fields to verify and unpack each stored watch before showing it.

*Call graph*: calls 1 internal fn (_watch_fields); 2 external calls (__init__, __init__).


##### `cancel_page_watch`  (lines 82–89)

```
async def cancel_page_watch(ctx: ToolContext, args: CancelPageWatchInput) -> ToolResult
```

**Purpose**: Deletes a saved page watch by name. It protects users from thinking they cancelled something that did not exist by raising an error when the named watch cannot be found.

**Data flow**: It receives the tool context and the watch name to cancel. It checks that extension context is available, cleans the name into the same storage form used when creating the watch, looks up that stored record, deletes it if present, and returns a confirmation message.

**Call relations**: This is called when a member asks to stop watching a topic. It uses _slug to find the same key that watch_pages created, then removes the record so on_page_change will no longer consider it.

*Call graph*: calls 1 internal fn (_slug); 2 external calls (__init__, __init__).


##### `on_page_change`  (lines 92–146)

```
async def on_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Responds to synced-page change events by comparing changed pages with saved watches and triggering alerts for matches. This is the core alerting path.

**Data flow**: It receives a hook context containing a page-change batch and extension services. It confirms the payload really is a page-change batch, loads all saved watches, skips work if there are none, and requires an off-turn model to be available. For each non-deleted page, it takes a clipped excerpt, asks the model whether the page matches each watch topic, and if the answer includes MATCH, it invokes an alerting turn in the stored conversation and agent using a duplicate-prevention key. It returns no special outcome.

**Call relations**: The source page-sync pipeline calls this hook when pages change. It uses _watch_fields to unpack saved watch records, builds a small model request with Message and ModelRequest, and converts stored conversation and agent strings back into UUID objects before asking the extension system to send the alert.

*Call graph*: calls 1 internal fn (_watch_fields); 3 external calls (__init__, __init__, UUID).


##### `_watch_fields`  (lines 149–154)

```
def _watch_fields(key: str, value: object) -> tuple[str, str, str]
```

**Purpose**: Checks that a stored watch record has the expected shape and extracts the important fields. It prevents later code from silently using broken or incomplete watch data.

**Data flow**: It receives a storage key and the value read from the store. If the value is a dictionary with string fields for topic, conversation ID, and agent ID, it returns those three strings. Otherwise, it raises an error naming the malformed watch.

**Call relations**: list_page_watches calls this before displaying saved watches, and on_page_change calls it before using a watch to classify pages and send alerts. In both cases it acts like a gatekeeper for stored watch data.

*Call graph*: called by 2 (list_page_watches, on_page_change).

## 📊 State Registers Touched

- `reg-extension-set` — The saved and loaded set of extensions, packs, manifests, and contributed capabilities available to the runtime.
- `reg-workspace-storage` — The shared file, blob, artifact, and mount state that stores workspace bytes and files shared back to users.
- `reg-source-pages` — The source connections, sync cursors, imported pages, removal markers, and page-change records from outside systems.
- `reg-search-index` — The searchable text chunks, embeddings, and selected index backend used to find relevant stored content.
- `reg-memory-store` — The durable memories, memory pages, recall events, and consolidation state used for long-term recall.
- `reg-knowledge-graph` — The stored entities and relationships extracted from pages so the system can look up connected facts.
- `reg-extension-store` — The per-workspace extension-owned storage where plugins keep their own durable records without private tables.
- `reg-page-alert-subscriptions` — The stored page-change watch rules, subscribed conversations, topic alerts, and pending alert notifications triggered by synced content changes.
