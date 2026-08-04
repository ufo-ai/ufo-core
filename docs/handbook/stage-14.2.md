# Memory and Search Backends  `stage-14.2`

This stage is shared behind-the-scenes support for remembering and finding information. It is like a library system: some parts decide how to split books into useful pages, some parts store the pages, and some parts help people browse the shelves.

The shared indexing code defines how long text is cut into smaller searchable chunks. It also sets the common “contract” that every search backend must follow, so the rest of the system can search without knowing where the index lives. The default index is the built-in option. It stores chunks locally and can search by matching words or by comparing vector embeddings, which are number patterns that represent meaning. The Turbopuffer extension does the same kind of work using an outside search service.

The memory store saves explicit facts and source-page text, then queues background indexing so saving stays quick. The condenser cleans up raw pages and older facts into more durable memories. Events define shared names and limits for memory activity. Objects make memories readable by ID, while the surface provides a read-only web view and API for authorized operators.

## Files in this stage

### Indexing contract and chunks
Shared indexing rules define how text is chunked and how index backends plug into memory search.

### `core/src/ufo/indexing.py`

`domain_logic` · `content indexing and re-indexing`

This file is the “front desk” for indexing text. Other parts of the system may have pages or memory items, but search works better when long bodies are split into smaller, meaningful pieces. This file describes those pieces, creates them, asks an embedding service to turn each piece into numbers that capture meaning, and sends them to an index backend for storage.

The key value objects are Chunk, Hit, and IndexScope. A Chunk is one searchable slice of text. A Hit is one search result. An IndexScope identifies all chunks belonging to one owner, such as one page or one memory item.

IndexBackend and EmbedClient are protocols, meaning they are promises about what another implementation must provide. The backend does the actual storage and searching. The embed client turns text into vectors, which are lists of numbers used for meaning-based search.

TextChunker is the main in-process logic. It splits text first at natural boundaries like paragraphs, lines, sentences, and punctuation, then falls back to whitespace or character slicing when needed. It also adds a little overlap between chunks, like repeating the last few words of one page at the top of the next, so search does not lose context at a cut point.

chunk_embed_upsert ties the steps together: chunk, embed, save, then prune old chunks that no longer match the current text.

#### Function details

##### `IndexBackend.upsert`  (lines 68–68)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This is the storage contract for adding or replacing indexed chunks. A backend implementation uses it to save chunks so they can later be found by search.

**Data flow**: It receives a group of Chunk objects, each containing owner information, text, and usually an embedding. The backend stores them, replacing matching existing chunks when needed. It returns nothing, but the index is changed.

**Call relations**: chunk_embed_upsert calls this after text has been split and embedded. The protocol itself only states the promise; a concrete backend supplies the real database or search-index work.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.delete`  (lines 70–70)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This is the storage contract for removing all indexed chunks for one owner. It is used when a page or memory item should no longer appear in search.

**Data flow**: It receives an IndexScope, which names an owner kind and owner id. The backend deletes all chunks in that scope. It returns nothing, but the stored index loses those chunks.

**Call relations**: No direct caller is shown in the provided call facts. It belongs to the same backend contract as upsert and prune, so other indexing flows can remove an owner’s search data completely.


##### `IndexBackend.prune`  (lines 72–72)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This is the storage contract for cleaning up stale chunks after content changes. It keeps the chunks that still belong to the current version and removes the rest.

**Data flow**: It receives an IndexScope for one owner and a set of chunk digests that should remain. The backend compares stored chunks for that owner against the keep set, deletes anything outside it, and returns nothing.

**Call relations**: chunk_embed_upsert calls this after saving the newly produced chunks. That final cleanup step prevents old text from still appearing in search after an edit.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.has_chunks`  (lines 74–74)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: This is the storage contract for asking whether an owner already has indexed chunks. It lets callers decide whether indexing work is needed.

**Data flow**: It receives an IndexScope naming one owner. The backend checks its stored index and returns true if chunks exist for that owner, otherwise false.

**Call relations**: No direct caller is shown in the provided call facts. It is part of the backend seam so higher-level indexing code can inspect index state without knowing the storage details.


##### `IndexBackend.lexical`  (lines 76–78)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the search contract for ordinary word-based search. It finds chunks whose text matches the query words.

**Data flow**: It receives a query string, allowed subjects, an owner kind, and a maximum number of results. The backend searches text for matching words within those limits and returns Hit objects with scores.

**Call relations**: No direct caller is shown in the provided call facts. Concrete backends implement it so retrieval code can ask for keyword-style results through the shared interface.


##### `IndexBackend.vector`  (lines 80–82)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the search contract for meaning-based search using an embedding. It finds chunks that are close in meaning, not just chunks that share exact words.

**Data flow**: It receives an embedding, allowed subjects, an owner kind, and a result limit. The backend compares that embedding with stored chunk embeddings and returns the best matching Hit objects.

**Call relations**: No direct caller is shown in the provided call facts. It pairs with EmbedClient.embed: text is turned into numbers elsewhere, then a backend can search for nearby stored vectors.


##### `EmbedClient.embed`  (lines 86–86)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This is the contract for turning text into embeddings, which are number lists that represent meaning. An implementation might call a local model or an external service.

**Data flow**: It receives a group of text strings. It returns one embedding for each input string, in the same order, so each chunk can be paired with its vector.

**Call relations**: chunk_embed_upsert calls this after TextChunker has produced chunk text. The returned vectors are then placed back onto the chunks before IndexBackend.upsert is called.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `chunk_embed_upsert`  (lines 89–114)

```
async def chunk_embed_upsert(index: IndexBackend, embed: EmbedClient, chunker: 'TextChunker', owner_kind: str, owner_id: str, subject: str, body: str) -> None
```

**Purpose**: This function performs the shared indexing recipe for one body of text. It splits the body into chunks, embeds those chunks, saves them, and removes stale old chunks.

**Data flow**: It receives an index backend, an embedding client, a chunker, owner details, a subject, and the text body. It turns the body into chunks, asks the embed client for vectors, copies those vectors into the chunks, saves them through the backend, then prunes any stored chunks for that owner whose digests were not produced this time. It returns nothing, but the index is updated to match the current body.

**Call relations**: This is the main coordinator in the file. It calls TextChunker.chunk indirectly through the chunker object, calls EmbedClient.embed for vectors, calls IndexBackend.upsert to save current chunks, builds an IndexScope for the owner, and calls IndexBackend.prune so edits do not leave old search results behind.

*Call graph*: calls 3 internal fn (embed, prune, upsert); 2 external calls (__init__, replace).


##### `TextChunker.chunk`  (lines 123–134)

```
def chunk(self, text: str, owner_kind: str, owner_id: str, subject: str) -> tuple[Chunk, ...]
```

**Purpose**: This is the public chunking method. It turns one text body into ordered Chunk objects that carry owner information and stable digests.

**Data flow**: It receives raw text plus owner kind, owner id, and subject. It asks _slices to split the text into pieces, gives each piece an ordinal number, computes a digest for each one, and returns the resulting Chunk objects. It does not add embeddings; that happens later.

**Call relations**: chunk_embed_upsert uses this as the first step of indexing. Inside the chunker, it relies on _slices for the actual splitting and _digest for each chunk’s stable identity.

*Call graph*: calls 2 internal fn (_digest, _slices); 1 external calls (__init__).


##### `TextChunker._slices`  (lines 136–144)

```
def _slices(self, text: str) -> list[str]
```

**Purpose**: This is the main internal text-splitting workflow. It decides whether text is already small enough, otherwise it splits, merges, overlaps, and caps the result.

**Data flow**: It receives raw text. Empty text becomes no slices. Short text is only trimmed and capped by character length. Longer text is recursively split at natural delimiters, greedily merged into useful sizes, given overlap for context, and finally capped by maximum character length. It returns a list of text slices.

**Call relations**: TextChunker.chunk calls this before creating Chunk objects. It coordinates the helper methods _count_words, _recursive_split, _greedy_merge, _apply_overlap, and _cap_by_chars.

*Call graph*: calls 5 internal fn (_apply_overlap, _cap_by_chars, _count_words, _greedy_merge, _recursive_split); called by 1 (chunk).


##### `TextChunker._count_words`  (lines 147–153)

```
def _count_words(text: str) -> int
```

**Purpose**: This estimates how large a piece of text is for chunking purposes. It treats dense Chinese, Japanese, or Korean text differently because those languages often do not use spaces between words.

**Data flow**: It receives text and removes whitespace to see how much real content exists. If there is no content, it returns zero. If enough characters are from Chinese, Japanese, or Korean ranges, it counts non-whitespace characters; otherwise it counts runs of non-whitespace text like normal words.

**Call relations**: _slices uses it to decide whether a body is already small enough. _recursive_split uses it to decide whether a piece still needs deeper splitting, and _greedy_merge uses it to decide whether combined pieces are still within a reasonable size.

*Call graph*: called by 3 (_greedy_merge, _recursive_split, _slices); 1 external calls (sub).


##### `TextChunker._cap_by_chars`  (lines 155–167)

```
def _cap_by_chars(self, text: str) -> list[str]
```

**Purpose**: This enforces a hard maximum character length for each slice. It is a safety net for very long text pieces that still exceed the size limit.

**Data flow**: It receives one text piece. If it is within the character limit, it returns that piece. If it is too long, it cuts it into overlapping character windows so no output piece is larger than the limit. Empty trimmed pieces are dropped.

**Call relations**: _slices calls this both for short whole texts and after the main splitting and overlap process. It is the final guardrail before slices become chunks.

*Call graph*: called by 1 (_slices).


##### `TextChunker._recursive_split`  (lines 169–181)

```
def _recursive_split(self, text: str, level: int) -> list[str]
```

**Purpose**: This breaks large text into smaller pieces by trying increasingly fine natural boundaries. It is like first cutting a document by paragraphs, then lines, then sentences, then punctuation, and only then by words.

**Data flow**: It receives text and a delimiter level. At each level, it tries to split on the delimiters for that level. If splitting does not help, it moves to the next level. Pieces still too large are split more deeply. When no delimiter levels remain, it falls back to whitespace splitting.

**Call relations**: _slices calls this when the text is too large. It uses _split_at_delimiters to cut at the current boundary type, _count_words to test piece size, and _split_on_whitespace as the last fallback.

*Call graph*: calls 3 internal fn (_count_words, _split_at_delimiters, _split_on_whitespace); called by 1 (_slices).


##### `TextChunker._split_at_delimiters`  (lines 184–197)

```
def _split_at_delimiters(text: str, delimiters: tuple[str, ...]) -> list[str]
```

**Purpose**: This splits text at the earliest matching delimiter from a given set. It preserves the delimiter at the end of each piece so punctuation and line breaks stay with the text they belong to.

**Data flow**: It receives text and a group of delimiter strings. It repeatedly finds the next earliest delimiter, cuts through it, and continues with the remaining text. It returns only non-empty pieces.

**Call relations**: _recursive_split calls this while trying each delimiter level. It does the mechanical cutting, while _recursive_split decides whether those cuts are good enough.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._split_on_whitespace`  (lines 199–215)

```
def _split_on_whitespace(self, text: str) -> list[str]
```

**Purpose**: This is the fallback splitter when natural delimiters are not enough. It cuts text into word-sized groups based on the target chunk size.

**Data flow**: It receives text. If normal word runs are found, it groups them into chunks of about target_words words. If there are no usable word runs or there is one extremely long run, it cuts by raw characters using the target size. It returns non-empty pieces.

**Call relations**: _recursive_split calls this only after delimiter-based splitting has run out. It ensures the chunker can still make progress on unusual text, such as a long unbroken string.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._greedy_merge`  (lines 217–231)

```
def _greedy_merge(self, pieces: list[str]) -> list[str]
```

**Purpose**: This combines small neighboring pieces so the final chunks are not too tiny. It keeps adding the next piece while the combined result stays within a reasonable size.

**Data flow**: It receives a list of split pieces. Starting from the first piece, it tries to append each next piece. If the combined text is no more than one and a half times the target word count, it keeps the combined text; otherwise it saves the current chunk and starts a new one. It returns the merged list.

**Call relations**: _slices calls this after _recursive_split. It uses _count_words to judge whether a merge is still small enough, producing better-sized chunks before overlap is added.

*Call graph*: calls 1 internal fn (_count_words); called by 1 (_slices); 1 external calls (ceil).


##### `TextChunker._apply_overlap`  (lines 233–239)

```
def _apply_overlap(self, chunks: list[str]) -> list[str]
```

**Purpose**: This adds a small amount of previous context to each chunk after the first. The goal is to avoid losing meaning when an important phrase is split across a boundary.

**Data flow**: It receives a list of chunks. If there is only one chunk or overlap is disabled, it returns them unchanged. Otherwise, for each neighboring pair, it prefixes the later chunk with trailing context from the previous chunk and returns the adjusted list.

**Call relations**: _slices calls this after merging. It uses _trailing_context to choose the repeated context and uses pairwise iteration to walk through neighboring chunks.

*Call graph*: calls 1 internal fn (_trailing_context); called by 1 (_slices); 1 external calls (pairwise).


##### `TextChunker._trailing_context`  (lines 241–251)

```
def _trailing_context(self, text: str) -> str
```

**Purpose**: This chooses the text to repeat from the end of a previous chunk. It tries to keep the repeated context useful and, when possible, starts after a sentence boundary.

**Data flow**: It receives one chunk of text. If the chunk has too few words, it returns no overlap. Otherwise it takes the last overlap_words words, looks for a sentence boundary in the first half of that trailing text, and may drop the earlier part so the overlap starts cleanly after a sentence end. It returns the chosen context string.

**Call relations**: _apply_overlap calls this for each previous chunk when building overlapped chunks. It is the helper that decides exactly what context gets copied forward.

*Call graph*: called by 1 (_apply_overlap).


##### `TextChunker._digest`  (lines 254–256)

```
def _digest(owner_kind: str, owner_id: str, subject: str, ordinal: int, text: str) -> str
```

**Purpose**: This creates a stable identity string for a chunk. The digest changes if the owner, subject, position, or text changes.

**Data flow**: It receives owner kind, owner id, subject, ordinal, and chunk text. It joins those fields with a separator, hashes the result with SHA-256, and returns the hash with a sha256 prefix. The original inputs are not changed.

**Call relations**: TextChunker.chunk calls this for every slice it turns into a Chunk. chunk_embed_upsert later uses these digests as the keep set for pruning, so unchanged chunks can stay while outdated chunks are removed.

*Call graph*: called by 1 (chunk); 1 external calls (sha256).


### Search index backends
Built-in and external index implementations store chunks, embeddings, and keyword data for later retrieval.

### `extensions/index_default/ufo_ext_index_default.py`

`domain_logic` · `indexing and search operations`

This file is the project’s default memory search engine. The system breaks larger things into small text chunks, stores those chunks, and later asks, “Which chunks best match this query?” Without this file, a normal deployment would have no default way to save searchable chunks or retrieve them.

It supports two database worlds. In PostgreSQL, it uses PostgreSQL’s native full-text search for word matching and pgvector for vector similarity. A vector is a list of numbers that represents the meaning of text, so similar meanings end up close together. In SQLite, it uses FTS5 for word search and stores vectors as raw bytes, then compares them in Python. That SQLite path is simpler and good for small local or development setups.

The main class, DefaultIndex, receives a transaction opener from the host system. Each operation opens a database transaction, checks which database dialect it is talking to, and runs the matching SQL. It can insert or update chunks, delete all chunks for one owner, check whether an owner has chunks, prune old chunks after re-indexing, and search by either words or vectors.

A key idea is that the rest of the project stays database-neutral. Public objects like Chunk, Hit, and IndexScope do not need to know whether PostgreSQL or SQLite is underneath. This file is the adapter that hides those differences.

#### Function details

##### `pgvector_literal`  (lines 31–32)

```
def pgvector_literal(vector: tuple[float, ...]) -> str
```

**Purpose**: This turns a Python tuple of numbers into the text format PostgreSQL’s vector extension expects. It is used when storing or searching vector embeddings in PostgreSQL.

**Data flow**: It receives a tuple like several floating-point numbers → converts each value into a plain float representation and joins them inside square brackets → returns a string such as a database-ready vector literal.

**Call relations**: When DefaultIndex.upsert saves chunks into PostgreSQL, it calls this to format each chunk embedding before sending it to the database. When DefaultIndex.vector searches PostgreSQL by meaning, it calls this again to format the query embedding.

*Call graph*: called by 2 (upsert, vector).


##### `cosine`  (lines 35–43)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: This measures how similar two vectors are using cosine similarity, which compares their direction rather than their size. It is the fallback similarity calculator for SQLite, where the database is not doing vector search itself.

**Data flow**: It receives two equal-length tuples of numbers → computes each vector’s length, checks for a zero-length vector, and compares matching positions → returns a similarity score, where larger means more alike.

**Call relations**: DefaultIndex.vector uses this after reading candidate SQLite rows from the database. The function relies on math.sqrt to calculate vector lengths, then hands the score back so the caller can sort the best matches first.

*Call graph*: called by 1 (vector); 1 external calls (sqrt).


##### `pack_embedding`  (lines 46–47)

```
def pack_embedding(vector: tuple[float, ...]) -> bytes
```

**Purpose**: This converts a vector embedding into compact bytes so SQLite can store it in a database column. SQLite does not have the same native vector type used by PostgreSQL, so the numbers are packed manually.

**Data flow**: It receives a tuple of floating-point numbers → writes them into a binary byte sequence using a fixed float layout → returns bytes ready to store in SQLite.

**Call relations**: DefaultIndex.upsert calls this only on the SQLite path when saving a chunk with an embedding. It delegates the actual binary packing to struct.pack.

*Call graph*: called by 1 (upsert); 1 external calls (pack).


##### `unpack_embedding`  (lines 50–51)

```
def unpack_embedding(blob: bytes) -> tuple[float, ...]
```

**Purpose**: This reverses pack_embedding by turning stored SQLite bytes back into a tuple of floating-point numbers. It is needed before Python can compare stored vectors with a query vector.

**Data flow**: It receives a byte string from SQLite → reads it as a sequence of four-byte floating-point numbers → returns the original-style tuple of numbers.

**Call relations**: DefaultIndex.vector calls this on SQLite search results before passing the restored vector to cosine for scoring. It delegates the binary reading to struct.unpack.

*Call graph*: called by 1 (vector); 1 external calls (unpack).


##### `_hit`  (lines 54–63)

```
def _hit(row: sa.RowMapping, score: float) -> Hit
```

**Purpose**: This builds a Hit object, which is the standard search-result shape returned by this index. It keeps the rest of the code from caring about raw database row formats.

**Data flow**: It receives a database row and a score → copies the chunk identity, owner information, subject, order, text, and score into a Hit → returns that Hit to the search method.

**Call relations**: DefaultIndex.lexical uses this to turn word-search rows into results. DefaultIndex.vector uses it to turn vector-search rows into results. Internally it creates the shared Hit data object.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `DefaultIndex.upsert`  (lines 171–208)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This saves chunks into the index, replacing existing records with the same chunk identity. Someone uses it after text has been split into searchable pieces and possibly given embeddings.

**Data flow**: It receives a tuple of Chunk objects → if there are none, it does nothing; otherwise it opens a transaction, detects PostgreSQL or SQLite, and writes each chunk to the right table format → the database ends up containing the newest version of those chunks. In SQLite, it also refreshes the full-text-search table for each chunk.

**Call relations**: This is one of the main entry points of DefaultIndex. On PostgreSQL it calls pgvector_literal to format embeddings for pgvector. On SQLite it calls pack_embedding to store embeddings as bytes, then updates the FTS table so later lexical searches can find the text.

*Call graph*: calls 2 internal fn (pack_embedding, pgvector_literal).


##### `DefaultIndex.delete`  (lines 210–217)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This removes every indexed chunk that belongs to one owner. It is used when an item is removed or when pruning decides nothing should remain for that owner.

**Data flow**: It receives an IndexScope containing an owner kind and owner id → opens a transaction and chooses the right database commands → deletes all matching chunks. In SQLite, it deletes matching full-text-search rows first so the side table does not point at removed chunks.

**Call relations**: DefaultIndex.prune calls this when the keep-set is empty, meaning no chunks should survive for that scope. Otherwise it stands as the direct delete operation for callers that want to clear an owner’s index entries.

*Call graph*: called by 1 (prune).


##### `DefaultIndex.has_chunks`  (lines 219–222)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: This answers a simple yes-or-no question: does this owner already have any chunks in the index? It can be used to avoid unnecessary indexing work or to check whether indexing has happened.

**Data flow**: It receives an IndexScope → opens a transaction and asks the chunk table for one matching row → returns true if a row exists and false if none exists.

**Call relations**: This method is a small query on the DefaultIndex. It does not call other local helper functions; it talks directly to the database through the transaction supplied to the index.


##### `DefaultIndex.prune`  (lines 224–238)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This removes old chunks for an owner while keeping a specific set of current chunk digests. It matters after re-chunking, because otherwise stale chunks could keep appearing in search results.

**Data flow**: It receives an IndexScope and a set of chunk digests to keep → if the set is empty, it deletes the whole scope; otherwise it opens a transaction and deletes only chunks not in the keep list → the database keeps current chunks and drops obsolete ones. In SQLite, it also prunes the full-text-search table.

**Call relations**: When there is nothing to keep, this method hands off to DefaultIndex.delete. When there is a keep-set, it performs dialect-specific pruning itself so both PostgreSQL and SQLite leave the index clean.

*Call graph*: calls 1 internal fn (delete).


##### `DefaultIndex.lexical`  (lines 240–276)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This searches chunks by ordinary words. It is useful when a user query should match exact or related terms in the stored text.

**Data flow**: It receives a text query, allowed subjects, an owner kind, and a result limit → if there are no subjects or no usable query text, it returns no results; otherwise it opens a transaction, runs the database’s word-search feature, and ranks the matches → returns a tuple of Hit objects.

**Call relations**: This is the word-search path of DefaultIndex. After the database returns rows, it calls _hit to turn each row into the project’s standard Hit result. PostgreSQL uses its full-text search query form, while SQLite builds a safer literal FTS query from the input words.

*Call graph*: calls 1 internal fn (_hit).


##### `DefaultIndex.vector`  (lines 278–311)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This searches chunks by meaning using vector embeddings. It is useful when the best answer may not share the same exact words as the query but has a similar semantic shape.

**Data flow**: It receives a query embedding, allowed subjects, an owner kind, and a result limit → if the embedding or subjects are empty, it returns no results; otherwise it opens a transaction and chooses a PostgreSQL or SQLite strategy → returns the highest-scoring Hit objects. PostgreSQL scores in the database; SQLite reads candidate embeddings, unpacks them, scores them in Python, sorts them, and keeps the top results.

**Call relations**: On PostgreSQL, this method calls pgvector_literal so the query embedding can be sent to pgvector. On SQLite, it calls unpack_embedding to restore stored vectors and cosine to score them. In both paths it calls _hit to produce the final search results.

*Call graph*: calls 4 internal fn (_hit, cosine, pgvector_literal, unpack_embedding).


##### `manifest`  (lines 314–324)

```
def manifest() -> Manifest
```

**Purpose**: This tells the host application that this extension provides an index backend named "default". It is how the default index becomes discoverable and usable by the rest of the system.

**Data flow**: It reads the module’s name, version, and backend name constants → builds an IndexBackendSpec whose factory creates a DefaultIndex from the host context’s transaction opener → returns a Manifest describing this extension.

**Call relations**: The extension-loading system calls this function to learn what the file contributes. It creates a Manifest containing an IndexBackendSpec, and that spec later lets the core construct DefaultIndex when the default backend is selected.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/turbopuffer/ufo_ext_turbopuffer.py`

`io_transport` · `startup registration, then active during indexing and search requests`

This extension is the bridge between UFO’s internal memory chunk system and Turbopuffer’s HTTP API. UFO works with chunks: small pieces of text, each with metadata such as who owns it, what subject it belongs to, and sometimes an embedding, which is a list of numbers that represents the text’s meaning. Turbopuffer stores those chunks as documents in a namespace for the current workspace.

The file solves two main problems. First, it translates UFO’s chunk objects into the column-shaped request format Turbopuffer expects. Second, it translates Turbopuffer search results back into UFO Hit objects, so the rest of the system does not need to know anything about Turbopuffer.

It supports two kinds of search. Vector search finds text that is close in meaning to a query embedding. Lexical search uses BM25, a keyword-ranking method, to find text that matches query words. Both searches are filtered by owner kind and subject, so results stay inside the right recall scope.

The TurbopufferIndex class is the working backend. It reads the API key from UFO credentials, builds authenticated HTTP requests, writes chunks in batches, deletes whole scopes, prunes stale chunks after re-chunking, and checks whether a scope already has indexed data. Think of it as a careful warehouse clerk: it labels every box, files it in the right workspace aisle, and removes old boxes when the source changes.

#### Function details

##### `turbopuffer_id`  (lines 42–48)

```
def turbopuffer_id(chunk_digest: str) -> str
```

**Purpose**: Converts UFO’s chunk digest into a document ID safe for Turbopuffer. Standard sha256 digests are shortened into base64url text so they fit comfortably within Turbopuffer’s ID limits; other IDs are left alone.

**Data flow**: It receives a chunk digest string. If the string looks like a sha256 digest, it removes the prefix, turns the hex bytes into compact base64url text, and drops padding characters. It returns that shortened ID; if the input is not a recognized sha256 digest, it returns the original string unchanged.

**Call relations**: When chunks are written, upsert_body uses this to name each Turbopuffer document. When delete or prune removes remote documents, they also use it so the IDs sent for deletion match the IDs that were originally written.

*Call graph*: called by 3 (delete, prune, upsert_body); 1 external calls (urlsafe_b64encode).


##### `chunk_digest_from_id`  (lines 51–60)

```
def chunk_digest_from_id(chunk_id: str) -> str
```

**Purpose**: Turns a Turbopuffer document ID back into UFO’s original chunk digest when possible. This keeps search results and exported rows speaking UFO’s normal chunk language.

**Data flow**: It receives a document ID from Turbopuffer. If the ID has the expected shortened base64url length, it tries to decode it back into raw bytes and formats those bytes as a sha256 digest. If decoding fails or the ID is not the shortened form, it returns the ID as-is.

**Call relations**: Search-result conversion uses this through hit_from_row. Scope export also uses it in _scope_chunks, so delete and prune can compare remote chunks against UFO’s keep or delete decisions using the original digest form.

*Call graph*: called by 2 (_scope_chunks, hit_from_row); 1 external calls (urlsafe_b64decode).


##### `upsert_body`  (lines 63–79)

```
def upsert_body(chunks: tuple[Chunk, ...]) -> dict[str, Any]
```

**Purpose**: Builds the request body used to write a batch of chunks into Turbopuffer. It packages IDs, vectors, metadata, and text in the format Turbopuffer expects.

**Data flow**: It receives a tuple of Chunk objects. It turns each chunk into parallel columns: document ID, embedding vector, owner details, subject, order number, and text. It returns a dictionary that also tells Turbopuffer to use cosine distance for vector search and to enable full-text search on the text field.

**Call relations**: TurbopufferIndex.upsert calls this for each write batch. Inside the body-building step, it calls turbopuffer_id so the remote document IDs match the file’s compact ID scheme.

*Call graph*: calls 1 internal fn (turbopuffer_id); called by 1 (upsert).


##### `query_filters`  (lines 82–86)

```
def query_filters(owner_kind: str, subjects: frozenset[str]) -> list[Any]
```

**Purpose**: Builds the Turbopuffer filter used for normal searches. It limits results to one owner kind and a chosen set of subjects, so recall does not pull unrelated chunks.

**Data flow**: It receives an owner kind and a frozen set of subjects. It sorts the subjects for stable output and returns a filter expression that means: owner_kind must match, and subject must be one of these values.

**Call relations**: TurbopufferIndex._query calls this whenever lexical or vector search needs to ask Turbopuffer for ranked rows. It is the shared guardrail that keeps both search styles scoped the same way.

*Call graph*: called by 1 (_query).


##### `scope_filters`  (lines 89–96)

```
def scope_filters(scope: IndexScope, after_id: str | None) -> list[Any]
```

**Purpose**: Builds the Turbopuffer filter for operations on one exact indexed scope. A scope means one owner kind plus one owner ID, such as all chunks for a particular source item.

**Data flow**: It receives an IndexScope and optionally an after_id marker. It returns a filter requiring the matching owner kind and owner ID. If after_id is present, it also asks for IDs greater than that marker, which allows paging through large scopes.

**Call relations**: TurbopufferIndex.has_chunks uses this to check whether any row exists in a scope. TurbopufferIndex._scope_chunks uses it repeatedly while walking through all chunks in a scope for delete and prune.

*Call graph*: called by 2 (_scope_chunks, has_chunks).


##### `hit_from_row`  (lines 99–108)

```
def hit_from_row(row: dict[str, Any], score: float) -> Hit
```

**Purpose**: Converts one Turbopuffer result row into UFO’s standard Hit object. A Hit is the internal search result shape used by the rest of the recall system.

**Data flow**: It receives a row dictionary from Turbopuffer and a score chosen by the caller. It converts the remote ID back into a chunk digest, reads the stored owner, subject, ordinal, and text fields, attaches the score, and returns a Hit.

**Call relations**: TurbopufferIndex.lexical calls this after keyword search assigns rank-based scores. TurbopufferIndex.vector calls it after vector search computes similarity-style scores. It relies on chunk_digest_from_id to undo the compact ID encoding.

*Call graph*: calls 1 internal fn (chunk_digest_from_id); called by 2 (lexical, vector); 1 external calls (__init__).


##### `vector_score`  (lines 111–117)

```
def vector_score(row: dict[str, Any], position: int, total: int) -> float
```

**Purpose**: Turns Turbopuffer’s vector-search ranking information into a score where higher means better. This gives UFO a consistent scoring direction for later result merging.

**Data flow**: It receives a result row, the row’s position in the returned list, and the total number of rows. If Turbopuffer returned a cosine distance, it converts that distance to similarity by subtracting it from 1. If no distance is present, it falls back to a descending rank score based on position.

**Call relations**: TurbopufferIndex.vector calls this for each returned vector row before turning the row into a Hit. The score it produces is then passed to hit_from_row.

*Call graph*: called by 1 (vector).


##### `TurbopufferIndex.upsert`  (lines 131–142)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Writes new or updated chunks to Turbopuffer. It skips chunks that do not have embeddings, because this backend is built around vector-capable indexed documents.

**Data flow**: It receives a tuple of Chunk objects. It filters out chunks without embeddings, gets authorization headers, splits the remaining chunks into write-sized batches, builds a Turbopuffer request body for each batch, posts it to the workspace namespace, and raises an error if Turbopuffer rejects the request. It returns nothing after the remote index is updated.

**Call relations**: The indexing flow calls this when UFO has chunks ready to store. It asks _auth for the Bearer token, _path for the namespace URL, and upsert_body to translate each batch into Turbopuffer’s format.

*Call graph*: calls 3 internal fn (_auth, _path, upsert_body).


##### `TurbopufferIndex.delete`  (lines 144–152)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Deletes every indexed chunk belonging to a given scope. This is used when a whole source or owner should no longer appear in search results.

**Data flow**: It receives an IndexScope. It gets authorization headers, fetches all remote chunks in that scope, converts their chunk digests into Turbopuffer document IDs, sends delete requests in batches, and raises an error if any delete request fails. The remote namespace is changed by removing those documents.

**Call relations**: Higher-level cleanup code calls this when an indexed scope must be removed. It uses _scope_chunks to discover what currently exists, turbopuffer_id to get the remote IDs, and _path plus _auth to send authenticated delete requests.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.prune`  (lines 154–164)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Removes stale chunks from a scope while keeping a specified set. This matters after re-chunking, when some old chunks may no longer exist but others should remain.

**Data flow**: It receives an IndexScope and a set of chunk digests to keep. It gets authorization headers, loads all chunks currently stored for the scope, selects only those whose digest is not in the keep set, converts them to Turbopuffer IDs, and sends batched delete requests. The result is that only unwanted remote chunks are removed.

**Call relations**: The indexing refresh flow calls this after deciding which chunks are still valid. It relies on _scope_chunks to list the current remote state, turbopuffer_id to address documents by their remote IDs, and _path plus _auth for the HTTP calls.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.has_chunks`  (lines 166–172)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether Turbopuffer already has at least one chunk for a given scope. This can help the system decide whether indexing work is needed.

**Data flow**: It receives an IndexScope. It builds a tiny query asking for just one row in that scope, sends it with authorization, treats a missing namespace as empty, and otherwise returns true if any rows came back.

**Call relations**: Index orchestration code can call this before doing heavier work. Internally it uses scope_filters to express the scope, _path to target the namespace query endpoint, and _auth to authorize the request.

*Call graph*: calls 3 internal fn (_auth, _path, scope_filters).


##### `TurbopufferIndex.lexical`  (lines 174–182)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Runs a keyword-style search over indexed text. It uses BM25, a common ranking method that rewards documents whose words match the query well.

**Data flow**: It receives a text query, a set of subjects, an owner kind, and a result limit. If the query is blank or there are no subjects, it returns no hits. Otherwise it asks Turbopuffer to rank by text match, then converts each returned row into a Hit with a simple descending rank score.

**Call relations**: Recall code calls this when it wants word-based matches. It delegates the HTTP query to _query and then uses hit_from_row to turn each returned row into UFO’s standard search result.

*Call graph*: calls 2 internal fn (_query, hit_from_row).


##### `TurbopufferIndex.vector`  (lines 184–193)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Runs a meaning-based search using an embedding vector. This finds chunks whose stored vectors are close to the query vector, even if the exact words differ.

**Data flow**: It receives an embedding, a set of subjects, an owner kind, and a limit. If the embedding is empty or there are no subjects, it returns no hits. Otherwise it asks Turbopuffer for approximate nearest-neighbor results, computes a positive score for each row, filters out non-positive scores, and returns Hits.

**Call relations**: Recall code calls this when it wants semantic matches. It uses _query to contact Turbopuffer, vector_score to convert ranking details into UFO-friendly scores, and hit_from_row to build the final Hit objects.

*Call graph*: calls 3 internal fn (_query, hit_from_row, vector_score).


##### `TurbopufferIndex._query`  (lines 195–208)

```
async def _query(self, rank_by: list[Any], owner_kind: str, subjects: frozenset[str], limit: int) -> list[dict[str, Any]]
```

**Purpose**: Performs the shared Turbopuffer query request used by both lexical and vector search. It centralizes the common request shape and error handling.

**Data flow**: It receives a rank_by instruction, owner kind, subjects, and a limit. It builds a request body with ranking, result count, requested attributes, and scope filters; sends it to the namespace query endpoint with authorization; returns an empty list if the namespace does not exist; otherwise returns the rows from the JSON response.

**Call relations**: TurbopufferIndex.lexical and TurbopufferIndex.vector both call this so they do not duplicate the HTTP request code. It gets authorization through _auth, builds the URL through _path, and uses query_filters to keep search results inside the allowed owner kind and subjects.

*Call graph*: calls 3 internal fn (_auth, _path, query_filters); called by 2 (lexical, vector).


##### `TurbopufferIndex._scope_chunks`  (lines 210–238)

```
async def _scope_chunks(self, scope: IndexScope, headers: dict[str, str]) -> list[Chunk]
```

**Purpose**: Lists all chunks currently stored in Turbopuffer for one scope. It is used before deleting or pruning so the code knows exactly which remote documents exist.

**Data flow**: It receives an IndexScope and already-prepared authorization headers. It repeatedly queries Turbopuffer for pages of rows ordered by ID, converts each row into a lightweight Chunk without an embedding, and keeps going until a short page means there are no more rows. If the namespace does not exist, it returns whatever has been collected, usually an empty list.

**Call relations**: TurbopufferIndex.delete and TurbopufferIndex.prune call this before deciding which IDs to delete. It uses scope_filters to page through one owner scope, _path to reach the query endpoint, and chunk_digest_from_id to restore UFO’s original digest form.

*Call graph*: calls 3 internal fn (_path, chunk_digest_from_id, scope_filters); called by 2 (delete, prune); 1 external calls (__init__).


##### `TurbopufferIndex._auth`  (lines 240–242)

```
async def _auth(self) -> dict[str, str]
```

**Purpose**: Builds the HTTP authorization header for Turbopuffer requests. It reads the API key from UFO’s credential store at the moment it is needed.

**Data flow**: It reads the configured Turbopuffer API key credential. It returns a dictionary containing an Authorization header in Bearer-token form, which is the standard way many HTTP APIs receive secret access tokens.

**Call relations**: Every operation that talks to Turbopuffer calls this directly or indirectly: upsert, delete, prune, has_chunks, and _query. It keeps credential lookup in one place so request methods can focus on their own work.

*Call graph*: called by 5 (_query, delete, has_chunks, prune, upsert).


##### `TurbopufferIndex._path`  (lines 244–245)

```
def _path(self, suffix: str='') -> str
```

**Purpose**: Builds the workspace-specific Turbopuffer namespace path. The namespace keeps one workspace’s indexed chunks separate from another’s.

**Data flow**: It receives an optional suffix such as '/query'. It reads the workspace ID from the credential context, prefixes it with the UFO namespace prefix, appends the suffix, and returns the URL path used by the HTTP client.

**Call relations**: All Turbopuffer HTTP operations call this when they need to know where to send a request. Upsert, delete, prune, has_chunks, _query, and _scope_chunks use it so they all point at the same workspace namespace.

*Call graph*: called by 6 (_query, _scope_chunks, delete, has_chunks, prune, upsert).


##### `manifest`  (lines 248–268)

```
def manifest() -> Manifest
```

**Purpose**: Declares this file as a UFO extension and tells UFO how to create the Turbopuffer index backend. Without this manifest, the backend would not be discoverable by name.

**Data flow**: It creates a Manifest containing the extension name and version, declares the required Turbopuffer API key credential slot, and registers an index backend named 'turbopuffer'. The backend factory builds a TurbopufferIndex with the runtime credential access object and an async HTTP client pointed at Turbopuffer’s base URL.

**Call relations**: UFO’s extension loading process calls this during startup. The returned Manifest wires the configured backend name to the TurbopufferIndex class, so later indexing and search flows can call the backend methods described above.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Memory storage and consolidation
The memory extension records facts, indexes source text, defines event limits, and condenses raw material into durable memories.

### `extensions/memory/ufo_ext_memory/events.py`

`config` · `cross-cutting`

The memory extension appears to emit structured events, which are machine-readable messages about something that happened. This file defines the shared labels and limits for those events. The main event name, `MEMORY_RECALL_EVENT`, marks the moment when the system recalls stored memories before preparing a response. The two maximum values act like guardrails: one limits how many recalled memory IDs can be included in an event, and the other limits how long an error class name can be when reported. These limits matter because event data is often logged, sent to monitoring tools, or displayed elsewhere. Without shared caps, one part of the system might send overly large or inconsistent event fields. Think of this file like a small style guide for memory-related event messages: it does not send the messages itself, but it tells other code what labels and boundaries to use.


### `extensions/memory/ufo_ext_memory/store.py`

`domain_logic` · `request handling and background indexing`

This file is where the memory extension turns plain text into durable, searchable memory. A memory can be written directly, or derived from a synced source page. The file stores those memories in database tables, records where page-derived memories came from, and keeps a small mirror of source pages so search results can be checked against the current page state. The key idea is separation of work: writing a memory only saves the row, while a later indexing job splits the text into chunks and embeds it. An embedding is a numeric representation of text used for meaning-based search. This keeps user-facing writes quick and prevents expensive indexing work from happening inline. When recalling memories, the store searches two ways: exact-ish word matching and meaning-based vector matching. It then blends the ranked results, reads the real database rows back, checks permissions and page freshness, applies recency decay for facts, limits over-representation by memory type, and turns episodic memories into topic pointers instead of injecting their full text. The file also handles page changes. If a source page changes or disappears, its old chunks are withdrawn and any memories tied to stale revisions are made due for re-checking. Without this file, memories could be duplicated, stale, leaked across source permissions, or impossible to recall reliably.

#### Function details

##### `recall_subjects`  (lines 149–150)

```
def recall_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: Turns an audience into the set of subject labels that memory recall is allowed to search. A subject is the visibility bucket for a memory, such as who or what the memory is about.

**Data flow**: It receives an audience object → asks the shared audience helper to expand it into subject strings → returns those subjects as a frozen set.

**Call relations**: This is a small doorway from the audience system into memory search. It delegates the real audience expansion to `audience_subjects` so this file uses the same visibility rules as the rest of the system.

*Call graph*: 1 external calls (audience_subjects).


##### `_granted_link`  (lines 153–161)

```
def _granted_link(source_ids: frozenset[UUID]) -> ColumnElement[bool]
```

**Purpose**: Builds a database permission test for page-derived memories. A memory learned from source pages is readable if the reader has access to at least one source that produced it.

**Data flow**: It receives a set of readable source IDs → builds an SQL `EXISTS` condition that looks for a matching link in `memory_source` for the current memory row → returns that condition for other queries to include.

**Call relations**: `MemoryStore._untail_leg` and `MemoryStore._enrich` use this condition when reading memory rows. It is the fence that stops a page-derived memory from being shown just because it matched search.

*Call graph*: called by 2 (_enrich, _untail_leg); 1 external calls (exists).


##### `inventory`  (lines 201–270)

```
async def inventory(transaction: Transaction, workspace_id: UUID) -> tuple[MemoryInventoryItem, ...]
```

**Purpose**: Returns a bounded, newest-first listing of stored memories for an operator or explorer view. This is not search; it is a snapshot of what the memory table currently contains.

**Data flow**: It receives a transaction opener and workspace ID → reads the newest memory rows and their source links from the database → computes age, half-life, and decay multiplier using one shared current time → returns `MemoryInventoryItem` objects.

**Call relations**: This function is separate from recall so an operator can inspect stored memory even if it would not currently rank in search. It calls `_aware`, `half_life_days`, and `decay_multiplier` to show the same decay signals recall uses.

*Call graph*: calls 3 internal fn (_aware, decay_multiplier, half_life_days); 3 external calls (__init__, now, select).


##### `_aware`  (lines 273–274)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Makes sure a date-time value has a timezone. If a stored timestamp has no timezone, it treats it as UTC.

**Data flow**: It receives a `datetime` → checks whether timezone information is present → returns it unchanged or with UTC attached.

**Call relations**: Inventory, recall enrichment, source search, and decay math call this before comparing times. It prevents subtle age calculations from mixing timezone-aware and timezone-unaware values.

*Call graph*: called by 4 (_enrich, search_sources, decay_multiplier, inventory); 1 external calls (replace).


##### `MemoryWrite.page_origin_is_complete`  (lines 297–305)

```
def page_origin_is_complete(self) -> Self
```

**Purpose**: Validates that a memory derived from a page has all of its origin information. A page-derived memory needs the page ID, page revision, and source ID together, not just one or two of them.

**Data flow**: It reads the fields already placed on a `MemoryWrite` object → checks whether any page-origin field is set without the others → returns the same object if complete, or raises an error if incomplete.

**Call relations**: This runs during `MemoryWrite` validation before `MemoryStore.commit` saves anything. It protects later indexing and permission checks from half-described provenance.


##### `_fuse`  (lines 333–356)

```
def _fuse(legs: tuple[tuple[Hit, ...], ...], cosine_leg: tuple[Hit, ...]) -> dict[str, tuple[float, float, str]]
```

**Purpose**: Combines ranked search hits from different search methods into one score per owning row. It uses reciprocal-rank fusion, a method that rewards results that appear near the top of several lists.

**Data flow**: It receives one or more lists of chunk hits plus the vector-search list → ranks chunks inside each list → gives each owner its best fused chunk score and best vector similarity → returns a map from owner ID to fused score, cosine score, and matched text.

**Call relations**: `fuse_hits` and `fuse_recall` both use this as their shared ranking engine. It is the common mixer that turns many chunk-level matches into row-level candidates.

*Call graph*: called by 2 (fuse_hits, fuse_recall); 1 external calls (from_iterable).


##### `fuse_hits`  (lines 359–364)

```
def fuse_hits(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks source-page search results by combining word-based and meaning-based hits. It returns the best owners up to the requested limit.

**Data flow**: It receives lexical hits, vector hits, and a limit → calls `_fuse` to combine the two hit lists → sorts by fused rank score → returns `Fused` results with owner ID, score, and snippet text.

**Call relations**: `MemoryStore.search_sources` calls this after getting index results. Unlike memory recall, it uses only the fused rank score, because source search does not apply memory-specific decay.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (search_sources); 1 external calls (__init__).


##### `fuse_recall`  (lines 367–384)

```
def fuse_recall(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], tail: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks memory recall candidates by blending fused rank with semantic closeness. It also includes a temporary word-search leg for memories not indexed yet.

**Data flow**: It receives lexical hits, vector hits, tail hits, and a limit → fuses all legs with `_fuse` → normalizes the top rank score → blends normalized rank with raw vector similarity → sorts and returns `Fused` candidates.

**Call relations**: `MemoryStore.recall` calls this before reading the database rows back. It lets freshly committed but not-yet-indexed memories participate through the tail leg.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (recall); 1 external calls (__init__).


##### `half_life_days`  (lines 402–408)

```
def half_life_days(item_class: str, memory_kind: str) -> float | None
```

**Purpose**: Chooses how quickly a fact should fade in recall ranking. Only factual memories decay; episodic and semantic memories do not.

**Data flow**: It receives an item class and memory kind → if the item is not a fact, returns no half-life → otherwise looks up the kind-specific half-life, falling back to the default fact half-life.

**Call relations**: `decay_multiplier` uses this for ranking, and `inventory` uses it to show operators the same decay rule. It is the single lookup point for recency half-life values.

*Call graph*: called by 2 (decay_multiplier, inventory).


##### `decay_multiplier`  (lines 411–423)

```
def decay_multiplier(item_class: str, memory_kind: str, confidence: int, as_of: datetime | None, now: datetime) -> float
```

**Purpose**: Calculates how much recency and confidence should reduce a memory’s relevance. Think of it like freshness weighting: old low-confidence facts count less.

**Data flow**: It receives class, kind, confidence, source time, and current time → finds the half-life → computes age in days and applies the decay formula for facts → returns a multiplier, or 1.0 for non-decaying items.

**Call relations**: `decay_factor` wraps this for recalled rows, and `inventory` calls it directly for display. It calls `_aware` so time comparisons are safe.

*Call graph*: calls 2 internal fn (_aware, half_life_days); called by 2 (decay_factor, inventory).


##### `decay_factor`  (lines 426–429)

```
def decay_factor(item: Recalled, now: datetime) -> float
```

**Purpose**: Calculates the decay multiplier for one recalled memory item. It chooses the best available timestamp from the item before applying the standard decay math.

**Data flow**: It receives a `Recalled` item and current time → uses the item’s `as_of` time or creation time → passes its class, kind, and confidence into `decay_multiplier` → returns the multiplier.

**Call relations**: `MemoryStore.recall` calls this after database enrichment. It is the bridge between recalled row objects and the shared decay formula.

*Call graph*: calls 1 internal fn (decay_multiplier); called by 1 (recall).


##### `enforce_type_diversity`  (lines 432–450)

```
def enforce_type_diversity(rows: tuple[Recalled, ...], limit: int) -> tuple[Recalled, ...]
```

**Purpose**: Prevents one memory class from taking over all recall results. It keeps results varied when possible, while still filling the requested limit.

**Data flow**: It receives ranked recalled rows and a limit → accepts only a capped number per item class on the first pass → keeps overflow aside → backfills from overflow if there is still room → returns the final limited tuple.

**Call relations**: `MemoryStore.recall` applies this after scoring and decay. It is a final shaping step before episodic items are turned into topic pointers.

*Call graph*: called by 1 (recall).


##### `as_topic_pointer`  (lines 453–463)

```
def as_topic_pointer(item: Recalled, index: int) -> Recalled
```

**Purpose**: Changes episodic memories into short topic pointers instead of full recalled text. This makes episodic memories act like breadcrumbs to browse, not automatic context to inject verbatim.

**Data flow**: It receives a recalled item and its result index → if the item is not episodic, returns it unchanged → if episodic, copies it with a pointer-style body and recall mode set to `topic`.

**Call relations**: `MemoryStore.recall` calls this on the diversified result list. It uses `dataclasses.replace` so the original recalled object is not modified in place.

*Call graph*: called by 1 (recall); 1 external calls (replace).


##### `MemoryStore.commit`  (lines 490–579)

```
async def commit(self, write: MemoryWrite) -> None
```

**Purpose**: Saves one memory item without doing indexing work. It records the memory text, visibility subject, confidence, kind, and optional page origin, then marks it as needing the background indexer if needed.

**Data flow**: It receives a validated `MemoryWrite` → creates a stable content-based ID from workspace, subject, class, and body → inserts or updates the `memory_item` row → if the memory came from a source page, inserts or updates the page/source link in `memory_source`.

**Call relations**: This is the write path used when new memories are created. It deliberately does not call embedding or chunking code; `MemoryIndexer.run` later finds rows whose digest is empty and indexes them.

*Call graph*: 3 external calls (case, or_, uuid5).


##### `MemoryStore.supersede_page_facts`  (lines 581–697)

```
async def supersede_page_facts(self, page_id: UUID, revision: int | None) -> None
```

**Purpose**: Retires facts tied to an old version of a source page. If the same fact was learned from other pages too, it removes only the stale page link and keeps the fact alive.

**Data flow**: It receives a page ID and the page’s current revision, or no revision for a deleted page → finds memory-source links from that page that are now stale → deletes those links → deletes memory rows with no remaining links, or re-points rows to a surviving link and clears their index digest → deletes index chunks for fully removed memories.

**Call relations**: The fact-derivation flow calls this when a page’s derived facts are replaced. It uses the index backend only after rows are deleted, while surviving rows are left for the memory indexer to re-check.

*Call graph*: 4 external calls (__init__, delete, select, update).


##### `MemoryStore.recall`  (lines 699–739)

```
async def recall(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None, *, source_reader: SourceReader) -> tuple[Recalled, ...]
```

**Purpose**: Searches stored memories for a user query and returns the best readable results. It combines search relevance, permission checks, freshness checks, recency decay, and diversity rules.

**Data flow**: It receives a query, allowed subjects, limit, optional time window, and source reader → gets readable source IDs → asks the index for lexical and vector candidates → adds a bounded scan of not-yet-indexed rows → fuses scores → reads real memory rows back with permission and page-state filters → applies decay and diversity → returns recalled items.

**Call relations**: This is the main read path for memory recall. It coordinates `_source_ids`, `_legs`, `_untail_leg`, `fuse_recall`, `_enrich`, `decay_factor`, `enforce_type_diversity`, and `as_topic_pointer`.

*Call graph*: calls 8 internal fn (_enrich, _legs, _source_ids, _untail_leg, as_topic_pointer, decay_factor, enforce_type_diversity, fuse_recall); 2 external calls (replace, now).


##### `MemoryStore.search_sources`  (lines 741–807)

```
async def search_sources(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None, *, source_reader: SourceReader) -> tuple[SourceMatch, ...]
```

**Purpose**: Searches synced source pages and returns matching snippets. It is like memory recall, but for page chunks rather than distilled memory facts.

**Data flow**: It receives query, subjects, limit, optional time window, and source reader → gets lexical and vector page hits from `_legs` → fuses them with `fuse_hits` → reads matching mirror rows from `mem_page` → asks for readable current page states → keeps only pages that are still current and readable → returns `SourceMatch` snippets.

**Call relations**: This supports source-page search. It depends on `PageIndexer` keeping `mem_page` and page chunks current, and it calls `_readable_states` to enforce source access.

*Call graph*: calls 4 internal fn (_legs, _readable_states, _aware, fuse_hits); 3 external calls (__init__, select, UUID).


##### `MemoryStore._source_ids`  (lines 809–815)

```
async def _source_ids(self, source_reader: SourceReader) -> frozenset[UUID]
```

**Purpose**: Gets the set of source IDs the current reader is allowed to use for memory reads. Without this authority, source-derived memories cannot be safely shown.

**Data flow**: It receives a source reader token or object → checks that a readable-source-ID provider was wired into the store → asks that provider for allowed source IDs → returns them.

**Call relations**: `MemoryStore.recall` calls this before any source-derived memory filtering. If the store was created without grant authority, it raises a clear runtime error instead of risking an unsafe read.

*Call graph*: called by 1 (recall).


##### `MemoryStore._legs`  (lines 817–829)

```
async def _legs(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[tuple[Hit, ...], tuple[Hit, ...]]
```

**Purpose**: Runs the two normal search legs: word-based search and vector-based meaning search. The vector leg is skipped if the query cannot be embedded.

**Data flow**: It receives a query, subjects, owner kind, and limit → embeds the query through `_embed_query` → asks the index for lexical hits → asks the index for vector hits if an embedding exists → returns both hit lists.

**Call relations**: Both `MemoryStore.recall` and `MemoryStore.search_sources` use this helper. It centralizes how this file talks to the index backend for memory items and pages.

*Call graph*: calls 1 internal fn (_embed_query); called by 2 (recall, search_sources).


##### `MemoryStore._untail_leg`  (lines 831–886)

```
async def _untail_leg(self, query: str, subjects: frozenset[str], limit: int, source_ids: frozenset[UUID]) -> tuple[Hit, ...]
```

**Purpose**: Searches very recent memories that have not been indexed yet. This makes a just-written memory recallable before the background embedding job catches up.

**Data flow**: It receives a query, subjects, limit, and readable source IDs → splits the query into terms → reads a bounded newest-first set of unindexed, non-superseded memory rows that the reader may access → counts term matches in each body → returns synthetic `Hit` objects sorted by score.

**Call relations**: `MemoryStore.recall` adds this as a third search leg before `fuse_recall`. It uses `_granted_link` to apply the same source permission fence as normal recall.

*Call graph*: calls 1 internal fn (_granted_link); called by 1 (recall); 4 external calls (__init__, split, or_, select).


##### `MemoryStore._embed_query`  (lines 888–896)

```
async def _embed_query(self, query: str) -> tuple[float, ...]
```

**Purpose**: Turns a recall or source-search query into an embedding vector. If embedding fails, it logs a warning and lets search continue with word matching only.

**Data flow**: It receives query text → returns an empty tuple for blank text → otherwise asks the embedding client for one vector → returns that vector, or an empty tuple if there was an error or no result.

**Call relations**: `MemoryStore._legs` calls this before using vector search. Its failure-tolerant behavior keeps recall available even when the embedding service is temporarily broken.

*Call graph*: called by 1 (_legs).


##### `MemoryStore._enrich`  (lines 898–977)

```
async def _enrich(self, fused: tuple[Fused, ...], subjects: frozenset[str], source_ids: frozenset[UUID], start: datetime | None, end: datetime | None) -> tuple[Recalled, ...]
```

**Purpose**: Turns fused search candidates into full recalled memory rows, while applying database-level filters. It is where matching candidates become safe, current, user-visible results.

**Data flow**: It receives fused candidates, subjects, readable source IDs, and optional time bounds → reads matching memory rows that are not superseded and pass source grants → checks page-derived rows against current page state and revision → returns `Recalled` objects in the fused order.

**Call relations**: `MemoryStore.recall` calls this after fusion. It uses `_granted_link` for source permissions and `_aware` for timestamp normalization.

*Call graph*: calls 2 internal fn (_aware, _granted_link); called by 1 (recall); 4 external calls (__init__, or_, select, UUID).


##### `MemoryStore._readable_states`  (lines 979–986)

```
async def _readable_states(self, page_ids: tuple[UUID, ...], source_reader: SourceReader) -> dict[UUID, PageState]
```

**Purpose**: Fetches current page states that the reader is allowed to see. It is used to verify source-page search results before returning snippets.

**Data flow**: It receives page IDs and a source reader → returns an empty dictionary if there are no page IDs → otherwise checks that readable-page-state authority exists → asks that provider for the readable states → returns them by page ID.

**Call relations**: `MemoryStore.search_sources` calls this after index fusion. It prevents stale or unauthorized page chunks from being returned just because they still exist in the index.

*Call graph*: called by 1 (search_sources).


##### `store_for`  (lines 989–1002)

```
def store_for(ext: ExtensionContext) -> MemoryStore
```

**Purpose**: Builds a `MemoryStore` from the extension context. It makes sure the required index and embedding backends are available.

**Data flow**: It receives an extension context → checks that index and embed clients are present → copies the scoped transaction opener, workspace ID, page-state readers, and grant helpers into a new `MemoryStore` → returns that store.

**Call relations**: Other extension code uses this as the setup helper before committing, recalling, or searching memory. It fails loudly at setup time if memory cannot work because key services were not wired.

*Call graph*: 1 external calls (__init__).


##### `MemoryIndexer.run`  (lines 1024–1026)

```
async def run(self) -> None
```

**Purpose**: Runs one indexing pass for memory items that are due. It claims a batch and processes each item one by one.

**Data flow**: It takes no explicit input beyond the indexer’s configured database, index, embedder, and chunker → asks `_claim_due` for rows needing indexing → sends each claimed item to `_index_item` → returns nothing after the batch is processed.

**Call relations**: This is the background job entry for memory indexing. It keeps expensive chunking and embedding off the write path used by `MemoryStore.commit`.

*Call graph*: calls 2 internal fn (_claim_due, _index_item).


##### `MemoryIndexer._claim_due`  (lines 1028–1063)

```
async def _claim_due(self) -> tuple[MemoryItem, ...]
```

**Purpose**: Finds memory rows that need indexing and marks them as claimed for this run. The claim acts like a short lease so overlapping workers do not do the same embedding work.

**Data flow**: It reads the current time → selects rows whose embedding digest is empty and whose claim is absent or expired → locks or relies on single-writer behavior depending on the database → stamps their claim time → returns them as `MemoryItem` objects.

**Call relations**: `MemoryIndexer.run` calls this at the start of a batch. `_index_item` then works only on the claimed items.

*Call graph*: called by 1 (run); 5 external calls (now, timedelta, or_, select, update).


##### `MemoryIndexer._index_item`  (lines 1065–1102)

```
async def _index_item(self, item: MemoryItem) -> None
```

**Purpose**: Indexes one claimed memory item if it is safe to publish. If the item is tied to an old page revision, it removes its chunks instead of publishing stale text.

**Data flow**: It receives a claimed `MemoryItem` → checks `_publishable` against current page state → deletes index chunks and settles the row if not publishable → otherwise creates chunks and embeddings if missing → rechecks the row’s binding and page state → settles only if the binding is still the same.

**Call relations**: `MemoryIndexer.run` calls this for each claimed row. It hands chunk creation to `chunk_embed_upsert`, deletion to the index backend, and final database stamping to `_settle`.

*Call graph*: calls 2 internal fn (_publishable, _settle); called by 1 (run); 3 external calls (__init__, select, chunk_embed_upsert).


##### `MemoryIndexer._publishable`  (lines 1104–1113)

```
async def _publishable(self, subject: str, page_id: UUID | None, revision: int | None) -> bool
```

**Purpose**: Decides whether a memory item is allowed to appear in the search index. Directly written memories are publishable; page-derived memories are publishable only while their source page is still at the same subject and revision.

**Data flow**: It receives a subject, optional page ID, and optional revision → returns true immediately for non-page memories → otherwise reads the current page state → returns true only if the page exists and exactly matches the expected subject and revision.

**Call relations**: `MemoryIndexer._index_item` calls this before indexing and again after chunking. The double-check closes the race where a page changes while embedding is in progress.

*Call graph*: called by 1 (_index_item).


##### `MemoryIndexer._settle`  (lines 1115–1139)

```
async def _settle(self, item: MemoryItem) -> None
```

**Purpose**: Marks a claimed memory row as decided by the indexer. This removes it from the due queue whether the body was published or deliberately withheld.

**Data flow**: It receives the item that was claimed → computes a SHA-256 digest of its body → updates the row with that digest, clears the claim time, and touches `updated_at`, but only if the row still matches the claimed binding and still has a claim → returns nothing.

**Call relations**: `MemoryIndexer._index_item` calls this at the end of a safe decision. Its guarded update prevents an old indexing decision from overwriting a newer page rebind.

*Call graph*: called by 1 (_index_item); 2 external calls (sha256, update).


##### `PageIndexer.apply`  (lines 1162–1164)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Applies a batch of source-page changes to the memory extension’s page index. It processes each change independently and in order.

**Data flow**: It receives a tuple of page changes → loops through them → calls `_apply` for each change → returns after the batch is complete.

**Call relations**: The core page-change runner calls this with delivered changes. `_apply` contains the actual per-page decision logic.

*Call graph*: calls 1 internal fn (_apply).


##### `PageIndexer._apply`  (lines 1166–1224)

```
async def _apply(self, change: PageChange) -> None
```

**Purpose**: Updates indexed source-page chunks and the local `mem_page` mirror for one page change. It also withdraws stale page chunks when a page is deleted or no longer matches the delivered change.

**Data flow**: It receives one `PageChange` → reads the current page state → clears digest claims for facts left behind by the page change → if tombstoned or stale, deletes page chunks and mirror row as needed → otherwise chunks and embeds the page body → rechecks page state → writes or updates the mirror row if still current.

**Call relations**: `PageIndexer.apply` calls this for each change. It uses `_unsettle_left_behind_facts` so the memory indexer will later remove or re-check memories tied to old page revisions.

*Call graph*: calls 1 internal fn (_unsettle_left_behind_facts); called by 1 (apply); 5 external calls (__init__, delete, insert, update, chunk_embed_upsert).


##### `PageIndexer._unsettle_left_behind_facts`  (lines 1226–1254)

```
async def _unsettle_left_behind_facts(self, page_id: UUID, state: PageState | None) -> None
```

**Purpose**: Marks page-derived memory rows as needing re-indexing when their source page has moved on. This is how stale facts are pushed out of the index even if no replacement fact has been derived yet.

**Data flow**: It receives a page ID and the page’s current state, if any → builds a condition for memory rows created from that page that no longer match the live subject and revision, or all rows if the page is gone → clears their embedding digest and claim time → returns nothing.

**Call relations**: `PageIndexer._apply` calls this before handling the page’s own chunks. The memory indexer later sees these rows as due and decides whether to delete their chunks or republish them.

*Call graph*: called by 1 (_apply); 2 external calls (or_, update).


### `extensions/memory/ufo_ext_memory/condenser.py`

`domain_logic` · `page-change processing and periodic memory cleanup`

The memory system has two different jobs here, both aimed at making recall better. First, `FactDeriver` watches page changes that have already been captured elsewhere. For each live page with enough text, it asks the language model to extract short, standalone facts. If those facts are successfully saved, it marks older facts from the same page revision as replaced. This is careful: it does not delete or retire old facts just because a page changed. It only does so when replacement facts actually land, so a bad model reply does not accidentally erase useful memory.

Second, `MemoryConsolidator` runs later as a periodic cleanup job. It looks for older fact memories that are still active, groups them by subject, compares their embeddings (number lists that represent meaning), and clusters facts that appear semantically close. For each cluster, it asks the model to write one concise summary and then marks the original facts as superseded by that summary. Think of it like turning a pile of sticky notes into one clean notebook entry.

The file is deliberately cautious. Model calls happen before database write transactions, so the database is not held open while waiting. Inputs and outputs are size-limited. Unreadable model output is treated as “do not change anything,” not as “there were no facts.”

#### Function details

##### `FactDeriver.apply`  (lines 113–127)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: This is the main entry point for turning a batch of changed pages into fact memories. It decides which pages still exist, which are worth sending to the model, and which page-derived facts can safely be retired.

**Data flow**: It receives a group of page-change records. It asks the memory store which pages are still current, immediately retires facts for pages that no longer exist, filters out tombstones and very short bodies, then processes the remaining pages in small groups. After each group produces saved replacement facts, it tells the store to supersede older facts for those exact page revisions.

**Call relations**: The page-change runner calls this when it delivers a batch of changed pages. `apply` splits the work into bounded groups with `itertools.batched`, then hands each group to `FactDeriver._derive`; only pages returned by `_derive` are treated as safely replaced.

*Call graph*: calls 1 internal fn (_derive); 1 external calls (batched).


##### `FactDeriver._derive`  (lines 129–179)

```
async def _derive(self, pages: tuple[PageChange, ...]) -> tuple[PageChange, ...]
```

**Purpose**: This function performs one protected extraction-and-save pass for a small group of pages. It makes sure the pages have not changed since the batch was delivered before committing any facts from them.

**Data flow**: It receives page-change records, rereads the current page state, and keeps only pages whose subject and revision still match. It asks `_extract` for the model’s raw answer, parses that answer with `_parse_facts`, ignores low-notability or invalid facts, and writes accepted facts to the memory store as `MemoryWrite` records. It returns only the pages for which at least one fact was actually saved.

**Call relations**: `FactDeriver.apply` calls this for each eligible group. `_derive` calls `_extract` to talk to the model and `_parse_facts` to turn the model reply into checked fact objects. Its return value tells `apply` which old page facts may now be superseded.

*Call graph*: calls 2 internal fn (_extract, _parse_facts); called by 1 (apply); 1 external calls (__init__).


##### `FactDeriver._extract`  (lines 181–198)

```
async def _extract(self, pages: tuple[PageChange, ...]) -> str
```

**Purpose**: This function packages a small group of pages into a language-model request that asks for durable facts. It returns the model’s raw text so the caller can decide whether it is readable.

**Data flow**: It receives page records, trims each page body to a safe maximum length, builds a compact JSON payload, wraps that payload in a `Message` and `ModelRequest`, and sends it through the configured model access object. It returns the model completion as plain text.

**Call relations**: `FactDeriver._derive` calls this when it needs the model to read page text. `_extract` does not save anything itself; it only prepares and sends the request, then hands the raw reply back for parsing.

*Call graph*: called by 1 (_derive); 3 external calls (__init__, __init__, dumps).


##### `MemoryConsolidator.run`  (lines 228–237)

```
async def run(self) -> None
```

**Purpose**: This is the main periodic cleanup pass that turns clusters of older related facts into broader semantic summaries. If no model is available, it quietly does nothing.

**Data flow**: It starts by checking whether a model is configured. If so, it reads eligible old facts, groups them by subject, skips groups that are too small, embeds each group, builds similarity clusters, and consolidates clusters large enough to summarize. Its visible result is new summary memories and older fact rows marked as superseded.

**Call relations**: A scheduler or periodic job calls `run`. It coordinates the full consolidation pipeline by calling `_aged_facts`, `_buckets`, `_embed`, `_clusters`, and `_consolidate` in order.

*Call graph*: calls 5 internal fn (_aged_facts, _buckets, _clusters, _consolidate, _embed).


##### `MemoryConsolidator._aged_facts`  (lines 239–269)

```
async def _aged_facts(self) -> tuple[_AgedFact, ...]
```

**Purpose**: This function finds old fact memories that are candidates for consolidation. It only selects active, non-page-derived facts, because page-derived facts have their own replacement rules.

**Data flow**: It calculates a cutoff time, opens a database transaction, and queries the memory table for fact rows in the current workspace that are old enough, not superseded, and not tied to a source page. It turns each database row into an `_AgedFact` value and returns them as a tuple.

**Call relations**: `MemoryConsolidator.run` calls this at the start of a consolidation cycle. The facts it returns become the raw material for bucketing, embedding, clustering, and eventual summarizing.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryConsolidator._buckets`  (lines 271–280)

```
def _buckets(self, facts: tuple[_AgedFact, ...]) -> tuple[tuple[str, tuple[_AgedFact, ...]], ...]
```

**Purpose**: This function groups candidate facts by their subject, so facts about different people or topics are not accidentally merged. It also caps each group to a safe size.

**Data flow**: It receives aged facts, collects them into lists keyed by subject, sorts each subject’s facts by recency, trims each group to the maximum bucket size, and returns subject-and-facts pairs.

**Call relations**: `MemoryConsolidator.run` calls this after reading aged facts. The resulting subject buckets are then processed one at a time for embedding and clustering.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._embed`  (lines 282–286)

```
async def _embed(self, facts: tuple[_AgedFact, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: This function converts fact text into embeddings, which are numeric fingerprints of meaning. Those fingerprints let the consolidator compare facts by meaning rather than exact wording.

**Data flow**: It receives facts, trims each fact body to a bounded length, sends the text batch to the embedding client, and pairs each returned vector with the matching fact id. It returns a dictionary from fact id to embedding vector.

**Call relations**: `MemoryConsolidator.run` calls this for each large-enough subject bucket. The returned vectors are passed to `_clusters`, which uses them to decide which facts belong together.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._clusters`  (lines 288–307)

```
def _clusters(self, facts: tuple[_AgedFact, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_AgedFact, ...], ...]
```

**Purpose**: This function groups facts that appear meaningfully similar. It uses a simple “join the first close-enough group” approach, starting with the newest facts first.

**Data flow**: It receives facts and their embedding vectors. For each fact, it compares that fact’s vector with the first fact in each existing cluster using `_cosine`; if the similarity is high enough, it joins that cluster, otherwise it starts a new one. It returns the completed clusters.

**Call relations**: `MemoryConsolidator.run` calls this after embedding a bucket. `_clusters` relies on `_cosine` for the similarity score, and `run` later sends clusters that are large enough to `_consolidate`.

*Call graph*: calls 1 internal fn (_cosine); called by 1 (run).


##### `MemoryConsolidator._consolidate`  (lines 309–367)

```
async def _consolidate(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> None
```

**Purpose**: This function replaces a cluster of related facts with one semantic summary, but only if the original facts are still exactly as expected. That safety check prevents overwriting or superseding memories that changed while consolidation was in progress.

**Data flow**: It receives a model and a cluster of aged facts. It asks `_summarize` for a combined statement, creates a new summary id, opens a database transaction, rereads and optionally locks the donor facts, checks that their bodies and confidence values still match, inserts the new semantic memory, and updates the old facts so they point to the summary as their replacement. If anything changed unexpectedly, it stops without writing, or raises an error if the locked update count is inconsistent.

**Call relations**: `MemoryConsolidator.run` calls this for each cluster that is big enough to collapse. `_consolidate` calls `_summarize` before opening the write transaction, then uses SQL insert and update operations to make the summary and retire the donors together.

*Call graph*: calls 1 internal fn (_summarize); called by 1 (run); 4 external calls (insert, select, update, uuid4).


##### `MemoryConsolidator._summarize`  (lines 369–378)

```
async def _summarize(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> str
```

**Purpose**: This function asks the language model to turn several related facts into one concise standalone summary. It is the text-generation step of consolidation.

**Data flow**: It receives a model and a cluster of facts, trims each fact body to a safe size, builds a compact JSON payload, creates a `ModelRequest`, and sends it to the model. It strips whitespace from the reply, limits the summary length, and returns that final summary text.

**Call relations**: `MemoryConsolidator._consolidate` calls this before doing any database writes. The returned summary becomes the body of the new semantic memory if it is not empty.

*Call graph*: calls 1 internal fn (complete); called by 1 (_consolidate); 3 external calls (__init__, __init__, dumps).


##### `_recency`  (lines 381–382)

```
def _recency(fact: _AgedFact) -> tuple[datetime, UUID]
```

**Purpose**: This small helper gives a stable sorting key for facts based on when they were created. It includes the fact id as a tie-breaker so ordering is predictable even when timestamps match.

**Data flow**: It receives one `_AgedFact` and returns a pair containing its creation time and id. Callers use that pair for sorting from newest to oldest or oldest to newest.

**Call relations**: The consolidation code uses this helper when ordering facts inside buckets and clusters. It keeps the recency logic in one place instead of repeating the same tuple-building code.


##### `_cosine`  (lines 385–391)

```
def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: This helper measures how similar two embedding vectors are. A result near 1 means the vectors point in a similar direction, which usually means the texts are close in meaning.

**Data flow**: It receives two number tuples, computes the length of each vector, returns 0 if either vector has no length, and otherwise divides their dot product by the product of their lengths. The output is a single similarity score.

**Call relations**: `MemoryConsolidator._clusters` calls this whenever it needs to decide whether a fact is close enough to a cluster head to join that cluster.

*Call graph*: called by 1 (_clusters); 1 external calls (sqrt).


##### `_parse_facts`  (lines 394–418)

```
def _parse_facts(text: str) -> tuple[ExtractedFact, ...]
```

**Purpose**: This function turns the model’s fact-extraction reply into validated `ExtractedFact` objects. It is intentionally forgiving of one bad fact item, but strict when the whole reply is not a readable facts response.

**Data flow**: It receives raw model text, finds the first JSON object inside it, decodes that object, and looks for a `facts` list. For each dictionary in that list, it validates the fields with `ExtractedFact`; invalid individual items are skipped. It returns the valid facts, or raises an error if there is no usable JSON object or no facts list.

**Call relations**: `FactDeriver._derive` calls this after `_extract` returns the model reply. If `_parse_facts` raises an error, `_derive` saves nothing for that group, which protects existing memories from being retired based on an unreadable model answer.

*Call graph*: called by 1 (_derive); 1 external calls (JSONDecoder).


### Memory explorer surfaces
Read-only object and web surfaces let operators and callers browse stored memory records safely.

### `extensions/memory/ufo_ext_memory/objects.py`

`domain_logic` · `request handling`

The memory extension stores remembered facts or notes in a database table. This file is the read-only doorway from the general object system into those stored memories. In everyday terms, `memory_search` can hand back a reference like a library catalog card, and this file is what lets someone use that card to open the full memory record.

A memory has text, an audience it is visible to, a type, confidence, and optional source information. Some memories are distilled from synced pages, so the file also checks that the caller is still allowed to read the source page revision before showing the memory. This prevents a memory from leaking information from a private or no-longer-visible room.

Listing shows only live memories: items that have not been replaced by newer consolidated memories. Opening a single memory by id is more forgiving: it can return a superseded memory too, but it includes a `superseded_by` link so the caller can follow the trail to the current version. Like a note with a forwarding address, the old item is still findable, but it tells you where to look next.

The file deliberately blocks `apply` and `delete`. Memories are written through the separate `memory_update` tool, and old memories end by being superseded, not erased here.

#### Function details

##### `_require_ext`  (lines 57–60)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure the memory object code was given its extension context. The extension context is the object that knows how to open the memory extension's database and workspace state.

**Data flow**: It receives a tool context. If that context contains an extension context, it returns it. If not, it stops immediately with an error, because the rest of this file cannot safely read memory data without it.

**Call relations**: `MemoryObjects.list` and `MemoryObjects.get` call this before touching the database. It is a guard at the front door: those flows need the extension context before they can query memory rows or check page visibility.

*Call graph*: called by 2 (get, list).


##### `MemoryObjects.list`  (lines 69–122)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a page of current, visible memory items for the caller. It is used when someone wants to browse live memories, not recall them by search ranking.

**Data flow**: It starts with the caller's tool context and list query. It reads the caller's allowed subjects, queries the memory table for non-superseded rows in the current workspace, and limits the result to a safe maximum. For memories that came from pages, it then checks that the source page is still readable, has the expected audience, and is still at the revision the memory came from. The output is an object page containing short rows with each memory id, a trimmed summary of the body, and basic fields such as subject, item class, and memory kind.

**Call relations**: When the object system asks to list objects of kind `memory`, this method is the store operation that does the work. It calls `_require_ext` to get database access, uses the tool context's source reader to check page visibility, builds `ObjectRow` entries for memories that pass the checks, and hands them to `object_page` so the result matches the shared object-list format.

*Call graph*: calls 2 internal fn (source_reader, _require_ext); 3 external calls (__init__, select, object_page).


##### `MemoryObjects.get`  (lines 124–189)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: This opens one memory item by its id and returns its full readable record. It is the path used when a caller has a memory reference and wants the complete text and metadata.

**Data flow**: It receives a name string and first tries to treat it as a UUID, which is the durable id format for memories. If the name is not a valid UUID, it returns nothing. If it is valid, it queries the memory table for a row in the current workspace and in one of the caller's readable subjects. If the memory was created from a page, it checks that the page source is still readable and still matches the recorded subject and revision. It then builds a detailed object containing the memory body, classification fields, confidence, source note, timestamp information, and links to the source page or replacement memory when those exist.

**Call relations**: The object system calls this when someone opens a `memory` object reference, such as one returned by memory search. It uses `_require_ext` for extension state, `UUID` parsing to validate the id, SQL selection to fetch the database row, the tool context's source reader to protect source-page visibility, and then wraps the result in `MemorySpec`, `ObjectLink`, `ObjectRef`, and `ObjectDetail` objects for the shared object API.

*Call graph*: calls 2 internal fn (source_reader, _require_ext); 6 external calls (__init__, __init__, __init__, __init__, select, UUID).


##### `MemoryObjects.status`  (lines 191–198)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports no special status for memory objects. It exists because the object store interface expects a status method, but memory items do not expose an apply-progress or sync status here.

**Data flow**: It receives the context, object name, and optional expected generation value. It does not inspect or change anything and always returns `None`, meaning there is no status record to show.

**Call relations**: If the wider object system asks a memory object for status, this method answers directly with no handoff. It is a placeholder implementation that keeps memory objects compatible with the common object interface.


##### `MemoryObjects.apply`  (lines 200–209)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemorySpec, old: MemorySpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses direct creation or editing of memory objects through the object API. Memories must be recorded through `memory_update`, which keeps writes on the intended controlled path.

**Data flow**: It receives the proposed memory name, new specification, optional old specification, and optional expected generation. Instead of using any of that data to write a row, it raises a clear 'verb not supported' error explaining that memories are not applied this way.

**Call relations**: When the wider object system tries to apply a change to a `memory` object, this method is invoked and immediately stops the request. It creates a `VerbNotSupported` error so callers know to use the memory-specific update tool instead.

*Call graph*: 1 external calls (__init__).


##### `MemoryObjects.delete`  (lines 211–218)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses direct deletion of memory objects. In this system, old memories are replaced by consolidation and hidden from recall, rather than deleted through the object API.

**Data flow**: It receives the context, memory name, and optional expected generation. It does not look up or remove the memory. It raises a 'verb not supported' error with a message explaining that memories cannot be deleted here.

**Call relations**: When the object system asks the memory store to delete a memory object, this method blocks that path. It hands back a `VerbNotSupported` error, preserving the design that memories end by being superseded, not manually removed.

*Call graph*: 1 external calls (__init__).


### `extensions/memory/ufo_ext_memory/surface.py`

`io_transport` · `request handling`

This file is the front door for the memory explorer: a small web surface that shows what the Memory extension has stored for a workspace. In plain terms, it serves two things: the HTML page the operator sees, and a JSON feed of memory records that the page can display. Without this file, an operator would not have a built-in way to inspect the memory items that recall is using.

The file expects authorization to have already tied the request to a workspace. That workspace boundary matters because memory is private to a workspace; reads must not accidentally show another workspace’s data. When the browser asks for the page, `app_page` returns a static `memory.html` file bundled with the extension. When the page asks for data, `memories` creates an extension-scoped context so it can read the Memory extension’s own `memory_item` table. It then asks the store layer for the inventory of memory items in the current workspace and returns them as JSON.

The route list at the bottom connects URL paths to these actions. It also includes a POST route for binding the operator web session, which is the login/session step used before the read-only explorer can be used.

#### Function details

##### `app_page`  (lines 27–30)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns the memory explorer web page to the operator’s browser. It exists so the operator interface can be served as one static HTML file from the extension.

**Data flow**: It receives the surface context and the incoming web request, but it does not need to inspect them. It checks whether the bundled `memory.html` file was successfully loaded when the module started. If the file is present, it wraps that HTML text in an HTTP response; if it is missing, it raises an error so the broken installation is visible instead of showing a blank page.

**Call relations**: This is used when a browser makes a GET request to the surface’s base path. It hands the already-loaded HTML to the HTTP response helper, which turns the text into a proper web response for the browser.

*Call graph*: 1 external calls (HTMLResponse).


##### `memories`  (lines 33–42)

```
async def memories(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns the workspace’s memory records as JSON for the explorer page. It is read-only: it shows what is stored, rather than changing memory.

**Data flow**: It receives the surface context, which includes the workspace ID chosen by the authorized operator session, and the incoming request. It builds an extension context for the Memory extension’s own storage area, opens the correct scoped transaction through that context, asks the store inventory function for memory items in the current workspace, converts each item into JSON-friendly data, and returns the list as a JSON HTTP response.

**Call relations**: This is used when the explorer page calls the `api/memories` endpoint. It creates the small amount of extension storage context needed for the read, delegates the actual database lookup to `ufo_ext_memory.store.inventory`, then hands the result to the JSON response helper so the browser can render it.

*Call graph*: 5 external calls (__init__, __init__, __init__, JSONResponse, inventory).
