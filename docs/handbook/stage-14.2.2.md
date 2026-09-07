# Indexing, embeddings, and memory recall  `stage-14.2.2`

This stage is shared behind-the-scenes support for finding things again later. It turns large pieces of text into smaller chunks, gives those chunks searchable “embeddings” (lists of numbers that roughly capture meaning), and stores them in indexes or memory stores.

The core indexing file is the common doorway. It breaks long text into search-sized pieces, asks an embedding provider to describe them as numbers, and keeps the index updated when content changes. The default index is the built-in filing cabinet: it stores chunks and can search by exact words or by similar meaning. The OpenAI embedding extension is the translator that calls OpenAI’s service, handling API keys and batching so other code can simply ask for embeddings.

The memory store builds on this. It saves recallable memories, searches them when a user asks something, and keeps memories made from pages aligned with their source pages. The memory events file defines shared event names and size limits. The research observations file keeps a clean, capped list of web sources found or read in a conversation, so they can be shown later as reliable sources.

## Files in this stage

### Searchable storage engines
These files provide the concrete stores that keep text chunks and memories searchable for later recall.

### `extensions/index_default/ufo_ext_index_default.py`

`domain_logic` · `indexing and search requests`

This file is the default search engine for the project’s memory chunks. A chunk is a small piece of text with metadata saying what it belongs to. The file knows how to save chunks, remove stale chunks, and search them in two ways: lexical search, which looks for shared words, and vector search, which compares numeric embeddings that represent rough meaning.

It matters because every deployment needs some way to remember and retrieve stored text. Without this backend, the system could create chunks but would not have a standard way to search them later.

The file supports two database families. In PostgreSQL, it uses PostgreSQL’s native full-text search plus pgvector, a database extension for comparing vectors. In SQLite, it uses FTS5, SQLite’s built-in full-text search feature, and does vector comparison in Python by scanning rows and computing cosine similarity. This is like having a fast warehouse system in production, but a simpler tabletop version for local development.

The central class, DefaultIndex, opens a workspace-scoped database transaction for each operation. It chooses the right SQL based on the database dialect, while keeping the rest of the project insulated from database-specific details like PostgreSQL vector syntax or SQLite binary packing.

#### Function details

##### `pgvector_literal`  (lines 35–36)

```
def pgvector_literal(vector: tuple[float, ...]) -> str
```

**Purpose**: Turns a Python tuple of numbers into the text format PostgreSQL’s pgvector extension expects. This lets the database read an embedding as a vector value.

**Data flow**: It receives a tuple of floating-point numbers. It converts each number to a plain float string and joins them inside square brackets. It returns one string, such as a database-ready vector literal.

**Call relations**: When DefaultIndex saves chunks or runs PostgreSQL vector search, it calls this helper before handing the embedding to SQL. The helper is the small adapter between Python’s tuple format and PostgreSQL’s vector format.

*Call graph*: called by 2 (upsert, vector).


##### `cosine`  (lines 39–47)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Compares two embeddings and returns how similar their directions are. This is used for SQLite vector search, where the database does not provide the vector comparison itself.

**Data flow**: It receives two equal-length tuples of numbers. It computes the size of each vector, then compares their dot product against those sizes. It returns a similarity score, or 0.0 if either vector has no length.

**Call relations**: DefaultIndex.vector uses this only on the SQLite path. PostgreSQL can compare vectors inside the database, but SQLite rows are pulled into Python and scored with this function.

*Call graph*: called by 1 (vector); 1 external calls (sqrt).


##### `pack_embedding`  (lines 50–51)

```
def pack_embedding(vector: tuple[float, ...]) -> bytes
```

**Purpose**: Converts an embedding into compact bytes so SQLite can store it in a database column. SQLite does not have a native vector type here, so the numbers are packed manually.

**Data flow**: It receives a tuple of floating-point numbers. It writes them into a binary byte string using four bytes per number. It returns those bytes for storage.

**Call relations**: DefaultIndex.upsert calls this when saving chunks into SQLite. It is paired with unpack_embedding, which reverses the process during vector search.

*Call graph*: called by 1 (upsert); 1 external calls (pack).


##### `unpack_embedding`  (lines 54–55)

```
def unpack_embedding(blob: bytes) -> tuple[float, ...]
```

**Purpose**: Converts bytes read from SQLite back into a tuple of floating-point numbers. This makes stored embeddings usable again for similarity comparison.

**Data flow**: It receives a byte string from the database. It reads the bytes in groups of four as floating-point numbers. It returns a tuple of numbers.

**Call relations**: DefaultIndex.vector calls this on SQLite rows before passing the restored embedding to cosine. Together with pack_embedding, it forms the save-and-load path for SQLite embeddings.

*Call graph*: called by 1 (vector); 1 external calls (unpack).


##### `_hit`  (lines 58–67)

```
def _hit(row: sa.RowMapping, score: float) -> Hit
```

**Purpose**: Builds a Hit object from a database row and a search score. A Hit is the project’s neutral search-result shape, so callers do not need to know which database produced the row.

**Data flow**: It receives a row containing chunk fields and a numeric score. It copies the chunk identity, owner information, subject, position, text, and score into a Hit object. It returns that Hit.

**Call relations**: Both DefaultIndex.lexical and DefaultIndex.vector call this after database rows have been found and scored. It is the final translation step from raw database output into the index API’s result format.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `DefaultIndex.upsert`  (lines 177–214)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds new chunks or replaces existing chunks with the same digest. This keeps the index current when text is chunked or re-chunked.

**Data flow**: It receives a tuple of Chunk objects. If the tuple is empty, it does nothing. Otherwise it opens a transaction, checks whether the connection is PostgreSQL or SQLite, and writes each chunk using the right database format. PostgreSQL embeddings are turned into pgvector text; SQLite embeddings are packed into bytes, and the SQLite full-text table is refreshed for each chunk.

**Call relations**: This is called when the system wants searchable chunks stored in the default index. It uses pgvector_literal on PostgreSQL and pack_embedding on SQLite, then hands the prepared values to SQL statements.

*Call graph*: calls 2 internal fn (pack_embedding, pgvector_literal).


##### `DefaultIndex.delete`  (lines 216–223)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Removes all indexed chunks for one owner. This is used when an indexed object should no longer appear in search results.

**Data flow**: It receives an IndexScope, which identifies the owner kind and owner id. It opens a transaction and deletes matching rows. On SQLite it also deletes matching full-text-search entries before deleting the main chunk rows.

**Call relations**: Other code can use this to clear an owner’s index entries directly. DefaultIndex.prune also calls it when pruning with an empty keep-set, because in that case every chunk for the scope should be removed.

*Call graph*: called by 1 (prune).


##### `DefaultIndex.has_chunks`  (lines 225–228)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether an owner already has any chunks in the index. This is a quick yes-or-no test before deciding whether indexing work is needed.

**Data flow**: It receives an IndexScope with owner information. It opens a transaction and asks the database whether at least one matching chunk exists. It returns true if a row is found, otherwise false.

**Call relations**: This method stands as a small query in the index backend API. It does not call local helpers; it simply uses the shared transaction opener and the common chunk table.


##### `DefaultIndex.prune`  (lines 230–244)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Deletes old chunks for an owner while keeping a known set of current chunk digests. This prevents orphaned search results after content has been re-chunked.

**Data flow**: It receives an IndexScope and a frozen set of chunk digests to keep. If the keep-set is empty, it deletes the whole scope. Otherwise it opens a transaction and removes every chunk for that owner whose digest is not in the keep-set. On SQLite it also removes the matching full-text rows.

**Call relations**: This method is used after re-indexing when the system knows which chunks are still valid. If nothing should be kept, it delegates to DefaultIndex.delete; otherwise it runs database-specific prune SQL.

*Call graph*: calls 1 internal fn (delete).


##### `DefaultIndex.lexical`  (lines 246–282)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches chunks by words in the query. It is the plain text search path: chunks with overlapping terms are returned with a ranking score.

**Data flow**: It receives a query string, allowed subjects, an owner kind, and a result limit. If there are no subjects, or the query is empty after preparation, it returns no results. Otherwise it opens a transaction, runs PostgreSQL full-text search or SQLite FTS5 search, and converts each matching row into a Hit with its score.

**Call relations**: This is called when the index is asked for word-based matches. After the database returns rows, it calls _hit so the rest of the system receives normal Hit objects instead of database-specific rows.

*Call graph*: calls 1 internal fn (_hit).


##### `DefaultIndex.vector`  (lines 284–317)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches chunks by embedding similarity, which is useful when the same idea may be phrased with different words. It returns the closest chunks according to vector similarity.

**Data flow**: It receives a query embedding, allowed subjects, an owner kind, and a result limit. If there is no embedding or no subject filter, it returns no results. In PostgreSQL, it sends the query vector to the database and lets SQL score and order matches. In SQLite, it fetches candidate rows, unpacks each stored embedding, computes cosine similarity in Python, sorts the rows, and returns the best Hits.

**Call relations**: This is the meaning-based search path for the backend. It uses pgvector_literal for PostgreSQL, and on SQLite it uses unpack_embedding and cosine before turning the chosen rows into Hits through _hit.

*Call graph*: calls 4 internal fn (_hit, cosine, pgvector_literal, unpack_embedding).


##### `manifest`  (lines 320–330)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the host system and registers the index backend under the name "default". This is how the core system discovers and creates DefaultIndex.

**Data flow**: It takes no input. It builds a Manifest containing the extension name, version, and an IndexBackendSpec. The spec includes a factory that creates DefaultIndex using the transaction opener supplied by the host context. It returns the Manifest.

**Call relations**: The extension loader calls this during discovery. The returned backend spec tells the core that when it needs the default index, it should construct DefaultIndex with the workspace-scoped transaction function.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/memory/ufo_ext_memory/store.py`

`domain_logic` · `request handling and background indexing`

This file solves a practical problem: a system may learn many small facts from tools or synced pages, but those facts are only useful if they can be safely stored, searched, updated, and hidden when their source is no longer valid. Think of it like a library card catalog plus a cleanup crew. The database tables keep the official record of each memory and which source pages support it. The index backend is the fast search shelf, built from chunks of text and embeddings, which are numeric summaries used for meaning-based search.

Writing a memory is deliberately simple: `commit` stores the row and marks it as needing indexing. It does not split or embed the text immediately, so user-facing writes stay quick. Background indexers later claim due rows, check whether they are still allowed to be published, then create or remove search chunks.

Reading is more careful. `recall` searches both by words and by meaning, blends those results, checks the caller’s subject and source permissions, removes stale or replaced rows, applies recency decay for facts, drops near-duplicates, and returns a diverse shortlist. `search_sources` does the same kind of search over source pages. The file also contains the page indexer, which mirrors live pages into the memory search index and withdraws chunks when pages change or disappear.

#### Function details

##### `recall_subjects`  (lines 176–177)

```
def recall_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: Turns an audience description into the set of memory subjects that may be searched. A subject is the visibility label used to decide which memories belong to which audience.

**Data flow**: It receives an `Audience` object → asks the shared audience helper to expand it into subject strings → returns those subjects as a frozen set.

**Call relations**: This is a small adapter around the shared audience code. Callers use it before recall so the memory store searches only the subjects the current audience is allowed to see.

*Call graph*: 1 external calls (audience_subjects).


##### `clip_to_word`  (lines 180–189)

```
def clip_to_word(text: str, limit: int) -> str
```

**Purpose**: Shortens text without cutting a word in half. It is used when a caller wants to fit memory text into a character budget while keeping it readable.

**Data flow**: It receives text and a maximum length → if the text already fits, it returns it unchanged → otherwise it keeps as much as possible, backs up to the last whole word, trims loose punctuation, and adds an ellipsis within the limit.

**Call relations**: This helper sits near the write model because memory bodies have a strict size limit. The store itself rejects overlong text; callers can use this function before writing if they choose to trim.


##### `_granted_link`  (lines 192–200)

```
def _granted_link(source_ids: frozenset[UUID]) -> ColumnElement[bool]
```

**Purpose**: Builds a database permission test for page-derived memories. A memory learned from source pages should be readable if the reader has access to at least one of those source links.

**Data flow**: It receives a set of source IDs the reader may access → builds a SQL `EXISTS` condition, meaning “there is at least one matching source link for this memory” → returns that condition for larger queries to include.

**Call relations**: `MemoryStore._untail_leg` uses it when scanning not-yet-indexed memories, and `MemoryStore._enrich` uses it when reading final recalled rows. It is the common gate that keeps source-derived memory from leaking to readers without a grant.

*Call graph*: called by 2 (_enrich, _untail_leg); 1 external calls (exists).


##### `inventory`  (lines 240–309)

```
async def inventory(transaction: Transaction, workspace_id: UUID) -> tuple[MemoryInventoryItem, ...]
```

**Purpose**: Produces an operator-facing list of recent memory rows in a workspace. This is not search; it is an inspection view that shows what is stored and how recall would currently treat it.

**Data flow**: It receives a transaction opener and workspace ID → reads the newest memory rows and their source links from the database → computes age, half-life, and decay weight using one current time → returns `MemoryInventoryItem` objects.

**Call relations**: It calls `_aware`, `half_life_days`, and `decay_multiplier` so the inventory reports the same timing math recall uses. It is separate from `MemoryStore.recall` because operators need to inspect even superseded or not-yet-indexed rows.

*Call graph*: calls 3 internal fn (_aware, decay_multiplier, half_life_days); 3 external calls (__init__, now, select).


##### `_aware`  (lines 312–313)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Makes sure a timestamp has a timezone. This avoids mistakes when comparing stored times with current UTC time.

**Data flow**: It receives a `datetime` → if it already has timezone information, it returns it → otherwise it treats it as UTC and returns a timezone-aware copy.

**Call relations**: Inventory, recall enrichment, source search, and decay math all call this before time comparisons. It keeps age calculations consistent even if a database driver returns a timestamp without timezone data.

*Call graph*: called by 4 (_enrich, search_sources, decay_multiplier, inventory); 1 external calls (replace).


##### `MemoryWrite.body_is_within_budget`  (lines 340–346)

```
def body_is_within_budget(self) -> Self
```

**Purpose**: Rejects a memory body that is too long to be stored as a single memory. The system treats very long text as a page or document, not as one small recallable fact.

**Data flow**: It receives the proposed `MemoryWrite` model during validation → checks the body length against the maximum → returns the same object if it fits, or raises an error if it does not.

**Call relations**: This runs automatically when a caller creates a `MemoryWrite`. It protects `MemoryStore.commit` from accepting oversized memory bodies.


##### `MemoryWrite.page_origin_is_complete`  (lines 349–357)

```
def page_origin_is_complete(self) -> Self
```

**Purpose**: Ensures page-derived memories include all required source details. A memory from a page needs the page ID, page revision, and source ID together, or none of them.

**Data flow**: It receives the proposed `MemoryWrite` during validation → checks whether only some page-origin fields were supplied → returns the object if the origin is complete, or raises an error if it is partial.

**Call relations**: This runs before `MemoryStore.commit`. It prevents broken rows that could not later be checked against page state or source permissions.


##### `_fuse`  (lines 387–410)

```
def _fuse(legs: tuple[tuple[Hit, ...], ...], cosine_leg: tuple[Hit, ...]) -> dict[str, tuple[float, float, str]]
```

**Purpose**: Combines several search result lists into one score per owning row. It lets word-based and meaning-based search results vote together instead of trusting only one search style.

**Data flow**: It receives search “legs,” each a ranked list of chunk hits, plus the vector leg → assigns rank-based credit to chunks, keeps each owner’s best chunk text, records the owner’s best cosine similarity from the vector leg → returns a map from owner ID to fused score information.

**Call relations**: `fuse_hits` and `fuse_recall` both call this as their shared ranking core. It does the raw fusion, while the callers decide how to sort, floor, and package the results.

*Call graph*: called by 2 (fuse_hits, fuse_recall); 1 external calls (from_iterable).


##### `fuse_hits`  (lines 413–426)

```
def fuse_hits(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks source-page search results by combining word search and meaning search. It returns the best page owners and snippets for source search.

**Data flow**: It receives lexical hits, vector hits, and a limit → calls `_fuse` → applies a similarity floor only when word search found nothing → sorts by fused rank → returns `Fused` results up to the limit.

**Call relations**: `MemoryStore.search_sources` calls this after retrieving page-index hits. It prepares a ranked page candidate list before the store checks whether those pages are still readable and current.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (search_sources); 1 external calls (__init__).


##### `fuse_recall`  (lines 429–457)

```
def fuse_recall(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], tail: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks memory recall candidates by blending rank-based fusion with raw semantic closeness. This helps a memory that is meaningfully close to the query rise above one that only matched by position in a result list.

**Data flow**: It receives lexical hits, vector hits, a tail list of not-yet-indexed rows, and a limit → calls `_fuse` → normalizes the rank score, blends it with cosine similarity, applies a floor for wordless garbage queries, sorts, and returns `Fused` results.

**Call relations**: `MemoryStore.recall` calls this after collecting indexed hits and fresh unindexed tail hits. Its output is then passed to `_enrich`, which reads the actual memory rows and applies permission and freshness checks.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (recall); 1 external calls (__init__).


##### `half_life_days`  (lines 475–481)

```
def half_life_days(item_class: str, memory_kind: str) -> float | None
```

**Purpose**: Returns how quickly a fact should fade in recall ranking. Non-fact memory classes do not fade this way.

**Data flow**: It receives an item class and memory kind → if the item is not a fact, it returns `None` → otherwise it looks up the half-life for that kind, falling back to the default fact half-life.

**Call relations**: Inventory calls it to display decay settings, and `decay_multiplier` calls it to compute the actual recall weight.

*Call graph*: called by 2 (decay_multiplier, inventory).


##### `decay_multiplier`  (lines 484–496)

```
def decay_multiplier(item_class: str, memory_kind: str, confidence: int, as_of: datetime | None, now: datetime) -> float
```

**Purpose**: Computes the recency-and-confidence weight used for fact recall. Older facts gradually count less, and low-confidence facts start lower.

**Data flow**: It receives item class, memory kind, confidence, an as-of time, and the current time → gets the half-life → if the item does not decay, returns 1.0 → otherwise calculates age in days and returns the confidence-scaled decay factor.

**Call relations**: `decay_factor` uses this during recall shortlisting, and `inventory` uses it to show operators the same value recall would apply.

*Call graph*: calls 2 internal fn (_aware, half_life_days); called by 2 (decay_factor, inventory).


##### `decay_factor`  (lines 499–502)

```
def decay_factor(item: Recalled, now: datetime) -> float
```

**Purpose**: Applies the standard decay calculation to a recalled item. It is a convenience wrapper for ranking recall results.

**Data flow**: It receives a `Recalled` item and the current time → chooses the item’s `as_of` time or creation time → passes the relevant fields to `decay_multiplier` → returns the multiplier.

**Call relations**: `MemoryStore._shortlist` calls this while re-ranking enriched recall candidates. It keeps the shortlist logic readable and centralizes decay math in `decay_multiplier`.

*Call graph*: calls 1 internal fn (decay_multiplier); called by 1 (_shortlist).


##### `_body_shingles`  (lines 510–512)

```
def _body_shingles(body: str) -> frozenset[str]
```

**Purpose**: Turns a body of text into overlapping three-word phrases. These phrases are used to spot near-duplicate memories cheaply.

**Data flow**: It receives a body string → lowercases it, splits it into words, builds three-word shingles → returns them as a set.

**Call relations**: `drop_near_duplicates` calls this for each candidate body it considers. The shingle sets make similarity checks deterministic without using an AI model.

*Call graph*: called by 1 (drop_near_duplicates); 1 external calls (split).


##### `drop_near_duplicates`  (lines 515–541)

```
def drop_near_duplicates(items: tuple[Recalled, ...], keep: int) -> tuple[Recalled, ...]
```

**Purpose**: Removes recall candidates that say almost the same thing as a higher-ranked candidate. This avoids spending limited context space on repeated facts.

**Data flow**: It receives ranked recalled items and a keep count → walks them in order → shingles each body prefix → skips an item if it overlaps too much with something already kept → returns the distinct kept items.

**Call relations**: `MemoryStore._shortlist` calls this after applying decay. It works before type diversity so the final result has both less repetition and a better spread of memory classes.

*Call graph*: calls 1 internal fn (_body_shingles); called by 1 (_shortlist).


##### `enforce_type_diversity`  (lines 544–562)

```
def enforce_type_diversity(rows: tuple[Recalled, ...], limit: int) -> tuple[Recalled, ...]
```

**Purpose**: Prevents one memory class from taking over all recall slots. For example, facts should not crowd out every episodic or semantic item when a mixed set is useful.

**Data flow**: It receives recalled rows and a limit → admits rows in rank order up to a per-class cap → saves overflow rows → backfills from overflow if there are still open slots → returns at most the requested limit.

**Call relations**: `MemoryStore._shortlist` calls this after duplicate removal. It is the last shaping step before episodic items are turned into topic pointers.

*Call graph*: called by 1 (_shortlist).


##### `as_topic_pointer`  (lines 565–575)

```
def as_topic_pointer(item: Recalled, index: int) -> Recalled
```

**Purpose**: Turns episodic memory hits into pointers instead of injecting their full text. Episodic memory acts like a breadcrumb to browse, not automatic context to quote.

**Data flow**: It receives a recalled item and its position → if it is not episodic, returns it unchanged → if it is episodic, replaces the body with a short topic pointer and marks the recall mode as `topic`.

**Call relations**: `MemoryStore.recall` applies this to the final shortlist. This happens after ranking, so episodic items can still be found but are presented safely.

*Call graph*: called by 1 (recall); 1 external calls (replace).


##### `MemoryStore.commit`  (lines 602–728)

```
async def commit(self, write: MemoryWrite) -> UUID
```

**Purpose**: Stores one memory item in the database and marks it for later indexing. It intentionally does not create search chunks or embeddings during the write.

**Data flow**: It receives a validated `MemoryWrite` → computes a stable ID from workspace, subject, class, and body → inserts or updates the memory row → records a source-page link if the memory came from a page → returns the memory ID.

**Call relations**: This is the write entry point for memory facts. Later, `MemoryIndexer.run` notices rows with no embedding digest and performs the expensive indexing work. `supersede_page_facts` relies on the IDs returned from page derivation commits to retire page facts no longer kept.

*Call graph*: 3 external calls (case, or_, uuid5).


##### `MemoryStore.supersede_page_facts`  (lines 730–851)

```
async def supersede_page_facts(self, page_id: UUID, kept: frozenset[UUID] | None) -> None
```

**Purpose**: Removes a page’s support for old derived facts after that page has been re-read or removed. It deletes a memory row only when no source-page link still supports it.

**Data flow**: It receives a page ID and either the set of memory IDs still kept for that page or `None` for a gone page → deletes stale `memory_source` links → repoints surviving rows to another valid link when needed → deletes unsupported rows and removes their index chunks.

**Call relations**: The page fact derivation flow calls this after committing the facts it still believes. It coordinates with the index backend by deleting index scopes for rows that truly disappear.

*Call graph*: 4 external calls (__init__, delete, select, update).


##### `MemoryStore.recall`  (lines 853–892)

```
async def recall(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None, *, source_reader: SourceReader) -> tuple[Recalled, ...]
```

**Purpose**: Finds the best memories for a user’s query while respecting subjects, source permissions, freshness, recency, duplicates, and diversity. This is the main read path for memory recall.

**Data flow**: It receives a query, allowed subjects, limit, optional time window, and source reader → gets readable source IDs → collects lexical and vector index legs plus an unindexed tail leg → fuses hits → enriches them from the database → shortlists them in a worker thread → returns final recalled items with episodic items rewritten as topic pointers.

**Call relations**: It calls `_source_ids`, `_legs`, `_untail_leg`, `fuse_recall`, `_enrich`, `_shortlist`, and `as_topic_pointer` in sequence. It is the place where search results become safe, ranked memory context.

*Call graph*: calls 6 internal fn (_enrich, _legs, _source_ids, _untail_leg, as_topic_pointer, fuse_recall); 2 external calls (to_thread, now).


##### `MemoryStore._shortlist`  (lines 894–909)

```
def _shortlist(self, enriched: tuple[Recalled, ...], limit: int, now: datetime) -> tuple[Recalled, ...]
```

**Purpose**: Narrows an enriched recall candidate pool down to the final requested slots. It applies the human-facing ranking and cleanup rules.

**Data flow**: It receives recalled candidates, a limit, and current time → multiplies each score by its decay factor → sorts by the adjusted score → removes near-duplicates → enforces type diversity → returns the final shortlist.

**Call relations**: `MemoryStore.recall` runs this in a worker thread because it is CPU work with no awaits. It calls `decay_factor`, `drop_near_duplicates`, and `enforce_type_diversity`.

*Call graph*: calls 3 internal fn (decay_factor, drop_near_duplicates, enforce_type_diversity); 1 external calls (replace).


##### `MemoryStore.search_sources`  (lines 911–977)

```
async def search_sources(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None, *, source_reader: SourceReader) -> tuple[SourceMatch, ...]
```

**Purpose**: Searches synced source pages and returns matched snippets. It is like recall, but aimed at original pages rather than distilled memory items.

**Data flow**: It receives a query, subjects, limit, optional time window, and source reader → gets lexical and vector page hits through `_legs` → fuses them with `fuse_hits` → reads matching mirror rows from `mem_page` → checks current readable page state → returns `SourceMatch` snippets.

**Call relations**: This method calls `_legs`, `fuse_hits`, and `_readable_states`. It depends on `PageIndexer` keeping `mem_page` and page chunks current enough to avoid serving stale source text.

*Call graph*: calls 4 internal fn (_legs, _readable_states, _aware, fuse_hits); 3 external calls (__init__, select, UUID).


##### `MemoryStore._source_ids`  (lines 979–985)

```
async def _source_ids(self, source_reader: SourceReader) -> frozenset[UUID]
```

**Purpose**: Gets the set of source IDs the current reader is allowed to access. Source-derived memory cannot be recalled safely without this authority.

**Data flow**: It receives a `SourceReader` → checks that a readable-source provider was wired into the store → asks that provider for source IDs → returns them as a frozen set.

**Call relations**: `MemoryStore.recall` calls this before searching the unindexed tail or enriching results. If the store lacks a grant authority, it fails loudly instead of risking an unsafe read.

*Call graph*: called by 1 (recall).


##### `MemoryStore._legs`  (lines 987–999)

```
async def _legs(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[tuple[Hit, ...], tuple[Hit, ...]]
```

**Purpose**: Runs the two main search legs: word-based search and meaning-based vector search. A vector is a numeric representation of the query’s meaning.

**Data flow**: It receives a query, subjects, owner kind, and limit → embeds the query with `_embed_query` → asks the index for lexical hits → if embedding succeeded, asks for vector hits too → returns both hit lists.

**Call relations**: Both `MemoryStore.recall` and `MemoryStore.search_sources` call this. The owner kind tells the shared index whether to search memory items or source pages.

*Call graph*: calls 1 internal fn (_embed_query); called by 2 (recall, search_sources).


##### `MemoryStore._untail_leg`  (lines 1001–1057)

```
async def _untail_leg(self, query: str, subjects: frozenset[str], limit: int, source_ids: frozenset[UUID]) -> tuple[Hit, ...]
```

**Purpose**: Finds very new memory rows that have not yet been indexed. This makes a just-written fact recallable before the background indexer catches up.

**Data flow**: It receives a query, subjects, limit, and readable source IDs → splits the query into terms → scans the newest unindexed eligible rows in the database → counts term matches in each body → returns synthetic `Hit` objects sorted by match count.

**Call relations**: `MemoryStore.recall` adds this as a third search leg before fusion. It calls `_granted_link` so unindexed page-derived rows still obey source permissions.

*Call graph*: calls 1 internal fn (_granted_link); called by 1 (recall); 4 external calls (__init__, split, or_, select).


##### `MemoryStore._embed_query`  (lines 1059–1067)

```
async def _embed_query(self, query: str) -> tuple[float, ...]
```

**Purpose**: Converts a non-empty search query into an embedding vector. If embedding fails, recall can still continue with word search.

**Data flow**: It receives a query string → returns an empty tuple for blank text → otherwise asks the embed backend for one vector → returns that vector, or an empty tuple after logging a warning on failure.

**Call relations**: `MemoryStore._legs` calls this before vector search. Its failure-tolerant behavior keeps recall available even when the embedding service has a problem.

*Call graph*: called by 1 (_legs).


##### `MemoryStore._enrich`  (lines 1069–1149)

```
async def _enrich(self, fused: tuple[Fused, ...], subjects: frozenset[str], source_ids: frozenset[UUID], start: datetime | None, end: datetime | None) -> tuple[Recalled, ...]
```

**Purpose**: Turns fused hit IDs into full recalled memory rows, while applying the important safety filters. The index suggests candidates; this function decides which rows may actually be shown.

**Data flow**: It receives fused hits, subjects, readable source IDs, and an optional time window → reads matching live rows from the database → filters out superseded, retired, unauthorized, and out-of-window rows → checks page-derived rows against current page state → returns `Recalled` objects in fused order.

**Call relations**: `MemoryStore.recall` calls this immediately after `fuse_recall`. It calls `_granted_link` for source permissions and `_aware` for timestamps, and uses `page_states` to avoid serving rows tied to old page revisions.

*Call graph*: calls 2 internal fn (_aware, _granted_link); called by 1 (recall); 4 external calls (__init__, or_, select, UUID).


##### `MemoryStore._readable_states`  (lines 1151–1158)

```
async def _readable_states(self, page_ids: tuple[UUID, ...], source_reader: SourceReader) -> dict[UUID, PageState]
```

**Purpose**: Gets current page states only for pages the reader may access. It is the source-page equivalent of a permission-aware freshness check.

**Data flow**: It receives page IDs and a source reader → returns an empty dictionary if there are no page IDs → otherwise requires a readable-page provider and asks it for current states → returns a page ID to state mapping.

**Call relations**: `MemoryStore.search_sources` calls this after page candidates are found. It prevents source search from returning snippets for pages that are no longer current or readable.

*Call graph*: called by 1 (search_sources).


##### `store_for`  (lines 1161–1174)

```
def store_for(ext: ExtensionContext) -> MemoryStore
```

**Purpose**: Builds a `MemoryStore` from the extension context. It is the wiring point that connects memory logic to the workspace transaction, index, embedder, and page-state services.

**Data flow**: It receives an `ExtensionContext` → verifies index and embed backends exist → copies the needed handles and workspace ID into a new `MemoryStore` → returns that store.

**Call relations**: Higher-level extension code calls this when it needs the memory workflow. It fails early if required backend services were not provided.

*Call graph*: 1 external calls (__init__).


##### `MemoryIndexer.run`  (lines 1198–1200)

```
async def run(self) -> None
```

**Purpose**: Processes a batch of memory rows that are due for indexing. It is the background job entry point for turning stored memory text into searchable chunks.

**Data flow**: It starts with no direct input → claims due rows through `_claim_due` → passes each claimed `MemoryItem` to `_index_item` → produces index changes and database settlement as side effects.

**Call relations**: A scheduler or derivation runner calls this periodically. It coordinates `_claim_due` and `_index_item` so writes stay fast and embedding work happens off the write path.

*Call graph*: calls 2 internal fn (_claim_due, _index_item).


##### `MemoryIndexer._claim_due`  (lines 1202–1238)

```
async def _claim_due(self) -> tuple[MemoryItem, ...]
```

**Purpose**: Atomically reserves a limited batch of memory rows that need indexing. The claim prevents overlapping indexer runs from embedding the same row at the same time.

**Data flow**: It computes the current time and expired-lease cutoff → selects rows with no embedding digest and no active claim → locks them when the database supports it → stamps their claim time → returns them as `MemoryItem` objects.

**Call relations**: `MemoryIndexer.run` calls this first. The later `_settle` step clears the claim when a row reaches a terminal indexing decision.

*Call graph*: called by 1 (run); 5 external calls (now, timedelta, or_, select, update).


##### `MemoryIndexer._index_item`  (lines 1240–1277)

```
async def _index_item(self, item: MemoryItem) -> None
```

**Purpose**: Decides what to do with one claimed memory item: publish it to the index, reuse existing chunks, withhold it, or delete stale chunks. This is the core memory indexing decision.

**Data flow**: It receives a claimed `MemoryItem` → checks whether it is retired or not publishable → deletes its index chunks and settles if needed → otherwise creates chunks and embeddings if missing → rechecks the row’s current binding → settles only if the claimed binding is still valid.

**Call relations**: `MemoryIndexer.run` calls this for every claimed row. It calls `_publishable`, `chunk_embed_upsert`, and `_settle`, and uses the index backend to delete chunks when a row should not be searchable.

*Call graph*: calls 2 internal fn (_publishable, _settle); called by 1 (run); 3 external calls (__init__, select, chunk_embed_upsert).


##### `MemoryIndexer._publishable`  (lines 1279–1288)

```
async def _publishable(self, subject: str, page_id: UUID | None, revision: int | None) -> bool
```

**Purpose**: Checks whether a memory body is allowed to appear in the search index. Tool-written memories are publishable; page-derived memories are publishable only while their page still has the same subject and revision.

**Data flow**: It receives a subject, optional page ID, and optional revision → returns true immediately for non-page memories → otherwise reads current page state and compares subject and revision → returns true only on an exact match.

**Call relations**: `MemoryIndexer._index_item` calls this before and after indexing work. The double check avoids publishing chunks for a page revision that changed while embedding was in progress.

*Call graph*: called by 1 (_index_item).


##### `MemoryIndexer._settle`  (lines 1290–1314)

```
async def _settle(self, item: MemoryItem) -> None
```

**Purpose**: Marks a claimed memory row as decided by writing a digest of its body and clearing the claim. This removes it from the indexer’s due queue.

**Data flow**: It receives the claimed `MemoryItem` → computes a SHA-256 digest of the body → updates the row only if the row still matches the claimed subject, body, page binding, source, and claim state → clears `embedding_claimed_at` and stores the digest.

**Call relations**: `MemoryIndexer._index_item` calls this after either publishing or intentionally withholding a row. The guarded update prevents an old indexing decision from settling a row that has been rebound or changed.

*Call graph*: called by 1 (_index_item); 2 external calls (sha256, update).


##### `PageIndexer.apply`  (lines 1337–1339)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Applies a delivered batch of source-page changes to the memory extension’s page index. It is the batch-level entry point for page indexing.

**Data flow**: It receives a tuple of `PageChange` records → loops over them in order → passes each one to `_apply` → produces index and mirror-table updates as side effects.

**Call relations**: The core page-change runner owns batching and calls this with changes. This method delegates the real per-page decision to `_apply`.

*Call graph*: calls 1 internal fn (_apply).


##### `PageIndexer._apply`  (lines 1341–1399)

```
async def _apply(self, change: PageChange) -> None
```

**Purpose**: Updates the source-page search index and the `mem_page` mirror for one page change. It also makes old facts from abandoned page revisions due for withdrawal.

**Data flow**: It receives a `PageChange` → reads the page’s current state → clears indexing settlement for facts left behind by this page → if tombstoned or stale, deletes page chunks and mirror row → otherwise chunks and embeds the page body → rechecks the page state → upserts the mirror row if still current.

**Call relations**: `PageIndexer.apply` calls this for each change. It calls `_unsettle_left_behind_facts`, uses `chunk_embed_upsert` to publish page chunks, and deletes index scopes when a page is gone or stale.

*Call graph*: calls 1 internal fn (_unsettle_left_behind_facts); called by 1 (apply); 5 external calls (__init__, delete, insert, update, chunk_embed_upsert).


##### `PageIndexer._unsettle_left_behind_facts`  (lines 1401–1429)

```
async def _unsettle_left_behind_facts(self, page_id: UUID, state: PageState | None) -> None
```

**Purpose**: Marks page-derived memory facts from old page revisions as needing the memory indexer’s attention again. This causes stale chunks to be withdrawn even if no replacement facts are produced.

**Data flow**: It receives a page ID and the page’s current state, if any → builds a database condition for memories from that page that no longer match the live subject and revision, or all of them if the page is gone → clears their embedding digest and claim fields → leaves the rows themselves in place.

**Call relations**: `PageIndexer._apply` calls this before handling the page chunk update. The memory indexer later sees these rows as due and decides whether to delete their memory-item chunks.

*Call graph*: called by 1 (_apply); 2 external calls (or_, update).


### Indexing and embedding infrastructure
These files define the shared indexing doorway, chunking and synchronization logic, and the OpenAI embedding provider used to make text searchable by meaning.

### `core/src/ufo/runtime/indexing.py`

`domain_logic` · `cross-cutting during indexing and retrieval preparation`

Search works best when long documents are broken into smaller, meaningful pieces. This file provides that common recipe without tying it to any particular database or search engine. Think of it like a food-prep station: it cuts the text into portions, labels each portion, asks an embedding service to turn each portion into numbers that represent its meaning, then gives those portions to whatever storage backend has been plugged in.

The small data objects, such as Chunk, Hit, and IndexScope, are the shared language used across this boundary. Chunk is a piece of indexed text. Hit is a search result. IndexScope names all chunks belonging to one owner, such as one memory item or one page.

IndexBackend and EmbedClient are protocols, meaning they describe what another component must be able to do, without saying how it does it. A backend might use a database, full-text search, or vector search, but this file does not care.

TextChunker is the main local logic. It splits text first at natural breaks like paragraphs, lines, sentences, and punctuation, then falls back to word or character splitting. It also adds a little overlap between neighboring chunks so a search does not lose context at the cut line. Finally, chunk_embed_upsert coordinates the whole update: chunk, embed, save, and prune old chunks that no longer belong.

#### Function details

##### `IndexBackend.upsert`  (lines 68–68)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This is the required method for saving chunks into an index. “Upsert” means save this chunk if it is new, or replace the existing one if the same chunk is already there.

**Data flow**: A backend implementation receives a group of Chunk objects, usually already containing embeddings. It writes them into its own storage system. Nothing is returned, but the index should now contain those chunks.

**Call relations**: chunk_embed_upsert calls this after it has split text and received embeddings. The method is only a promise here; the real work is done by whichever extension implements IndexBackend.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.delete`  (lines 70–70)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This is the required method for removing all indexed chunks for one owner. It is used when something should disappear from search completely.

**Data flow**: The caller gives an IndexScope, which names an owner kind and owner id. The backend removes matching chunks from its storage. It returns nothing, but the index should no longer have those chunks.

**Call relations**: The skill creation manifest’s _index_card flow calls this when it needs to clear indexed material. This file defines the contract so that flow can work with any backend implementation.

*Call graph*: called by 1 (_index_card).


##### `IndexBackend.prune`  (lines 72–72)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This is the required method for deleting stale chunks while keeping a chosen set. It matters when edited text produces a different set of chunks and old search results must not linger.

**Data flow**: The caller gives an IndexScope and a set of chunk digests to keep. The backend removes chunks for that owner whose digests are not in the keep set. It returns nothing, but the stored index becomes aligned with the latest text.

**Call relations**: chunk_embed_upsert calls this after saving the current chunks. That makes the index self-cleaning after edits, including the case where the new body is empty.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.has_chunks`  (lines 74–74)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: This is the required method for asking whether an owner already has indexed chunks. It is useful for deciding whether indexing work is missing or already done.

**Data flow**: The caller gives an IndexScope. The backend checks its storage for any chunks belonging to that owner and returns true or false.

**Call relations**: No direct caller is shown in the provided graph, but this belongs to the same backend contract as upsert, delete, prune, and search. Implementations provide it so orchestration code can test index state without knowing storage details.


##### `IndexBackend.lexical`  (lines 76–78)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the required method for text-based search, where matches are found using the words in the query. It is the classic kind of search: find chunks that contain or strongly match the requested terms.

**Data flow**: The caller provides a query string, a set of allowed subjects, an owner kind, and a maximum number of results. The backend searches its stored text under those filters and returns Hit objects ordered by whatever score the backend assigns.

**Call relations**: No direct caller is shown in the provided graph, but this method is part of the retrieval contract. Search code can call it alongside vector search without caring which database or search engine is underneath.


##### `IndexBackend.vector`  (lines 80–82)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the required method for meaning-based search using an embedding, which is a list of numbers representing the sense of some text. It helps find relevant chunks even when they do not use the exact same words as the query.

**Data flow**: The caller gives an embedding, allowed subjects, an owner kind, and a result limit. The backend compares that embedding with stored chunk embeddings and returns the closest Hit objects with scores.

**Call relations**: The queue’s _shadow_skill_selection flow calls this after creating an embedding for what it wants to search. This method lets that flow ask for semantic matches while leaving the storage and nearest-neighbor search details to the backend.

*Call graph*: called by 1 (_shadow_skill_selection).


##### `EmbedClient.embed`  (lines 86–86)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This is the required method for turning text into embeddings, which are numeric fingerprints of meaning. Other code uses it before saving chunks or running semantic search.

**Data flow**: The caller sends a tuple of text strings. The implementation sends them to an embedding model or service and returns one numeric vector for each input text, in the same order.

**Call relations**: chunk_embed_upsert calls this for every chunk it is about to store. The queue’s _shadow_skill_selection flow also calls it to turn a search prompt into a vector before asking IndexBackend.vector for matches.

*Call graph*: called by 2 (chunk_embed_upsert, _shadow_skill_selection).


##### `chunk_embed_upsert`  (lines 89–114)

```
async def chunk_embed_upsert(index: IndexBackend, embed: EmbedClient, chunker: 'TextChunker', owner_kind: str, owner_id: str, subject: str, body: str) -> None
```

**Purpose**: This function performs the shared indexing update for one body of text. It chunks the text, embeds each chunk, saves the chunks, and removes old chunks that the current text no longer produces.

**Data flow**: It receives an index backend, an embedding client, a chunker, owner information, a subject, and raw body text. First it asks TextChunker to split the body into Chunk objects. If there are chunks, it asks EmbedClient.embed for vectors, copies those vectors into the chunks, and sends them to IndexBackend.upsert. Finally it calls IndexBackend.prune with the set of current chunk digests, so anything stale is removed. It returns nothing, but the index is updated to match the current body.

**Call relations**: This is the coordinator that ties the local chunking logic to external embedding and storage implementations. Memory and page indexers can share it so they all get the same edit-safe behavior: save current chunks, then prune leftovers.

*Call graph*: calls 3 internal fn (embed, prune, upsert); 2 external calls (__init__, replace).


##### `TextChunker.chunk`  (lines 123–134)

```
def chunk(self, text: str, owner_kind: str, owner_id: str, subject: str) -> tuple[Chunk, ...]
```

**Purpose**: This is the public method for turning one text body into labeled Chunk objects. Each chunk gets ownership information, a subject, an order number, and a stable digest.

**Data flow**: It receives text plus the owner kind, owner id, and subject. It asks _slices to produce plain text pieces, then wraps each piece in a Chunk and uses _digest to create its unique identifier. It returns all chunks as a tuple.

**Call relations**: chunk_embed_upsert calls this at the start of an indexing update. Internally it relies on _slices for the actual cutting and _digest for stable chunk ids.

*Call graph*: calls 2 internal fn (_digest, _slices); 1 external calls (__init__).


##### `TextChunker._slices`  (lines 136–144)

```
def _slices(self, text: str) -> list[str]
```

**Purpose**: This function turns raw text into plain text slices before they become Chunk objects. It chooses the right splitting path based on whether the text is empty, already small enough, or needs careful cutting.

**Data flow**: It receives raw text. If the text is blank, it returns an empty list. If the text is short enough, it trims it and applies the character cap. Otherwise it recursively splits on natural delimiters, merges small pieces, adds overlap for context, caps each result by character length, and returns the final list of slice strings.

**Call relations**: TextChunker.chunk calls this to get the text pieces it will label. _slices is the central pipeline for the chunker, handing work to _count_words, _recursive_split, _greedy_merge, _apply_overlap, and _cap_by_chars.

*Call graph*: calls 5 internal fn (_apply_overlap, _cap_by_chars, _count_words, _greedy_merge, _recursive_split); called by 1 (chunk).


##### `TextChunker._count_words`  (lines 147–153)

```
def _count_words(text: str) -> int
```

**Purpose**: This function estimates how large a piece of text is in word-like units. It includes special handling for Chinese, Japanese, and Korean text, where words are often not separated by spaces.

**Data flow**: It receives text and removes whitespace to see whether anything meaningful remains. If the text is mostly CJK characters, it counts non-whitespace characters as the size. Otherwise it counts runs of non-space text like words. It returns a number used to decide whether chunks are too big or small enough.

**Call relations**: _slices uses this to decide whether text needs splitting. _recursive_split uses it to decide whether a piece must be split more deeply. _greedy_merge uses it to decide whether neighboring pieces can safely be combined.

*Call graph*: called by 3 (_greedy_merge, _recursive_split, _slices); 1 external calls (sub).


##### `TextChunker._cap_by_chars`  (lines 155–167)

```
def _cap_by_chars(self, text: str) -> list[str]
```

**Purpose**: This function enforces the maximum character length for chunks. It is a final safety limit so a chunk never becomes too large for downstream storage or embedding services.

**Data flow**: It receives one text string. If it is within the maximum length, it returns it as a one-item list, unless it is empty. If it is too long, it cuts it into character windows with a small overlap between windows, trims empty edges, and returns the resulting list.

**Call relations**: _slices calls this for short text and again at the end of the full splitting pipeline. It protects the rest of the indexing flow from oversized chunks.

*Call graph*: called by 1 (_slices).


##### `TextChunker._recursive_split`  (lines 169–181)

```
def _recursive_split(self, text: str, level: int) -> list[str]
```

**Purpose**: This function breaks large text into smaller pieces using increasingly fine separators. It tries friendly cuts first, like paragraphs and sentences, before falling back to rougher cuts.

**Data flow**: It receives text and a delimiter level. At each level, it tries to split on the configured delimiters. If that does not split the text, it moves to the next level. If a piece is still too large, it recursively splits that piece further. When no delimiters remain, it splits on whitespace. It returns a list of smaller strings.

**Call relations**: _slices calls this when text is too large for one chunk. It delegates delimiter cutting to _split_at_delimiters, size checks to _count_words, and the last-resort word split to _split_on_whitespace.

*Call graph*: calls 3 internal fn (_count_words, _split_at_delimiters, _split_on_whitespace); called by 1 (_slices).


##### `TextChunker._split_at_delimiters`  (lines 184–197)

```
def _split_at_delimiters(text: str, delimiters: tuple[str, ...]) -> list[str]
```

**Purpose**: This function cuts text at the earliest matching delimiter from a given set. It preserves the delimiter at the end of each piece so punctuation or line breaks stay with the text they belong to.

**Data flow**: It receives text and a tuple of delimiters. It repeatedly finds the nearest delimiter, cuts through that delimiter, and continues with the remaining text. If no delimiter is found, it keeps the rest as one piece. It returns only non-blank pieces.

**Call relations**: _recursive_split calls this while trying each delimiter level. It is the low-level cutter used for paragraph, line, sentence, and punctuation splitting.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._split_on_whitespace`  (lines 199–215)

```
def _split_on_whitespace(self, text: str) -> list[str]
```

**Purpose**: This is the fallback splitter when natural delimiters are not enough. It cuts by word runs, or by raw characters if there are no usable word breaks.

**Data flow**: It receives text. If it finds normal word-like runs, it groups them into chunks of about target_words. If the text is blank, it returns nothing. If the text is effectively one very long run, it cuts by character count based on the target size. It returns non-blank pieces.

**Call relations**: _recursive_split calls this only after delimiter-based splitting has run out of options. It ensures the chunker can still make progress on unusual text, minified text, or text without spaces.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._greedy_merge`  (lines 217–231)

```
def _greedy_merge(self, pieces: list[str]) -> list[str]
```

**Purpose**: This function combines neighboring pieces when they are smaller than they need to be. It keeps chunks reasonably full without making them too large.

**Data flow**: It receives a list of pieces. Starting from the first piece, it tries to append the next piece if the combined size stays under a generous limit. If adding the next piece would be too much, it saves the current piece and starts a new one. It returns the merged list.

**Call relations**: _slices calls this after recursive splitting. It uses _count_words to judge size and math.ceil to compute the allowed upper limit.

*Call graph*: calls 1 internal fn (_count_words); called by 1 (_slices); 1 external calls (ceil).


##### `TextChunker._apply_overlap`  (lines 233–239)

```
def _apply_overlap(self, chunks: list[str]) -> list[str]
```

**Purpose**: This function adds a little context from the end of each chunk to the beginning of the next one. That helps search and embedding models understand text that crosses a chunk boundary.

**Data flow**: It receives a list of chunk strings. If there is only one chunk or overlap is disabled, it returns the list unchanged. Otherwise it keeps the first chunk as-is, then prefixes each later chunk with trailing context from the previous chunk. It returns the overlapped chunks.

**Call relations**: _slices calls this after merging. It uses _trailing_context to decide exactly what previous text should be carried forward, and it walks through neighboring pairs of chunks.

*Call graph*: calls 1 internal fn (_trailing_context); called by 1 (_slices); 1 external calls (pairwise).


##### `TextChunker._trailing_context`  (lines 241–251)

```
def _trailing_context(self, text: str) -> str
```

**Purpose**: This function chooses the piece of previous text that should be copied into the next chunk as context. It tries to avoid starting that copied context in the middle of a sentence when possible.

**Data flow**: It receives one chunk of text. If the chunk does not have more words than the configured overlap size, it returns an empty string. Otherwise it takes the last overlap_words worth of text. If it finds a sentence boundary early enough inside that trailing text, it starts after that boundary; otherwise it returns the whole trailing text.

**Call relations**: _apply_overlap calls this for each previous chunk when building overlapped chunks. It is the small helper that makes overlap more sentence-aware rather than blindly copying words.

*Call graph*: called by 1 (_apply_overlap).


##### `TextChunker._digest`  (lines 254–256)

```
def _digest(owner_kind: str, owner_id: str, subject: str, ordinal: int, text: str) -> str
```

**Purpose**: This function creates a stable unique id for a chunk. The id changes if the owner, subject, order, or text changes, which helps the index recognize edited content.

**Data flow**: It receives the owner kind, owner id, subject, ordinal number, and chunk text. It joins those fields with a separator, hashes the result with SHA-256, and returns the digest string with a sha256 prefix.

**Call relations**: TextChunker.chunk calls this once for each slice while building Chunk objects. chunk_embed_upsert later uses these digests as the keep set when pruning stale chunks.

*Call graph*: called by 1 (chunk); 1 external calls (sha256).


### `extensions/embed_openai/ufo_ext_embed_openai.py`

`io_transport` · `startup registration and background embedding work`

This extension is the project’s built-in OpenAI embedding backend. An embedding is a list of numbers that represents the meaning of a piece of text, so similar texts end up with similar number lists. The system can then use those numbers for search or memory lookup.

The file does three main things. First, it defines fixed settings: which OpenAI model to use, how large the vectors should be, how many texts can go in one request, and how large each request may be. Second, it includes a batching helper that trims very long texts and groups many texts into safe request-sized chunks. This is like packing boxes for shipping: each box has both an item limit and a weight limit, so the helper starts a new box before the old one gets too full. Third, it defines an `OpenAIEmbedClient` that looks up the deploy API key only when embedding is actually requested, then calls OpenAI’s async client and returns the vectors in the original order.

This matters because embedding can be expensive and remote-service dependent. Without this file, a default deployment would not know how to produce embeddings. Without the batching limits, large inputs could exceed provider limits or fail unpredictably.

#### Function details

##### `plan_embed_batches`  (lines 33–49)

```
def plan_embed_batches(texts: tuple[str, ...]) -> tuple[tuple[str, ...], ...]
```

**Purpose**: This function prepares text for OpenAI embedding requests by cutting each text to a maximum length and grouping texts into batches that stay under safe size limits. It prevents one oversized request from being sent to the provider.

**Data flow**: It receives a tuple of text strings. For each string, it keeps only the allowed number of characters, then adds it to the current batch unless that batch would have too many items or too many total characters. It returns a tuple of batches, where each batch is a tuple of clipped strings ready to send.

**Call relations**: When `OpenAIEmbedClient.embed` is about to call OpenAI, it first asks this function to split the input into provider-safe pieces. The embedding client then sends each planned batch one at a time.

*Call graph*: called by 1 (embed).


##### `OpenAIEmbedClient.embed`  (lines 63–75)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This method turns a group of texts into OpenAI embedding vectors. It is the main runtime bridge between the project’s embedding interface and OpenAI’s remote service.

**Data flow**: It receives a tuple of texts. It reads the deploy API key from the environment, fails clearly if no key is available, creates an async OpenAI client with timeout and retry limits, splits the texts into safe batches, sends each batch to OpenAI, sorts the returned rows back into input order, and returns a tuple of numeric vectors.

**Call relations**: The wider embedding system calls this method when it needs vectors for indexing or search memory. Inside, it relies on `deploy_env` to find the API key, `plan_embed_batches` to keep requests within limits, and `openai.AsyncOpenAI` to make the actual network calls.

*Call graph*: calls 1 internal fn (plan_embed_batches); 2 external calls (AsyncOpenAI, deploy_env).


##### `build`  (lines 78–83)

```
def build(ctx: ExtensionContext) -> EmbedClient
```

**Purpose**: This function creates the embedding client object that the core system will use for the OpenAI backend. It deliberately does not require an API key during startup, so a local development server can still boot before anyone tries to embed text.

**Data flow**: It receives an extension context, which represents the surrounding workspace setup, but this backend does not need to read anything from it. It returns a new `OpenAIEmbedClient` instance.

**Call relations**: The manifest points to this function as the factory for the default embedding backend. During backend setup, the core calls it to get a client; later, that client’s `embed` method does the actual OpenAI work.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 86–92)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the extension to the host system: its name, version, needed deploy key, and the embedding backend it provides. It is how the OpenAI embedding client becomes discoverable as the default backend.

**Data flow**: It takes no input. It creates an `EmbedBackendSpec` that names the backend and points to `build`, then wraps that in a `Manifest` with the extension metadata and required API key name. It returns that manifest to the extension loader.

**Call relations**: The extension loading system calls this function to learn what this file contributes. The returned manifest tells the core that, when it needs the default embedding backend, it should call `build` to construct it.

*Call graph*: 2 external calls (__init__, __init__).


### Memory and research records
These files preserve structured memory-event metadata and durable research source observations for later display or retrieval.

### `extensions/memory/ufo_ext_memory/events.py`

`config` · `cross-cutting`

The memory extension needs to report certain things it does, such as recalling saved memories before a response is produced. This file defines the small shared vocabulary for those reports. Think of it like a label sheet used by different parts of the system: if everyone uses the same printed label, logs and event listeners can recognize the event reliably.

The main event name here is `memory.pre_response_recall`, stored as `MEMORY_RECALL_EVENT`. That name likely identifies the moment when the memory system searches for relevant past information before the assistant answers.

The file also sets two safety limits. `MAX_RECALLED_MEMORY_IDS` caps how many recalled memory identifiers should be included in an event, so event records stay compact and readable. `MAX_RECALL_ERROR_CLASS_CHARS` limits how much of an error class name is recorded, which helps prevent unusually long error text from bloating logs or event payloads.

Without this file, the same event name and limits might be copied around by hand, increasing the chance of spelling mistakes or inconsistent behavior.


### `extensions/research/ufo_ext_research/observations.py`

`domain_logic` · `request handling and conversation display`

Research tools often find search results and fetch pages while answering a user. Without this file, those sources would be temporary: useful during the turn, but not reliably available later for the conversation’s source list. This file acts like a small notebook for retrieved sources.

It defines a database table for source observations. Each saved source belongs to a workspace and a conversation, and is identified by a hash of its URL. A hash is a fixed-length fingerprint, used here so the same URL can be updated instead of duplicated. The file records the URL, title, snippet, optional published date, where it appeared in the result order, and when it was last seen.

Before saving, source text is shortened to safe maximum lengths and validated as a conversation source. Invalid entries are skipped rather than breaking the whole recording step. Saving uses an “upsert,” meaning “insert this row, or update the existing row if this source URL is already known.” Afterward, old extra sources are deleted so each conversation keeps at most 100 current sources.

The file also exposes a conversation slot provider named `SOURCES_SLOT`. A slot is a structured piece of conversation side content. Here, it lets the rest of the system ask, “How many sources are available?” and “Read the sources to display.”

#### Function details

##### `_bounded`  (lines 53–54)

```
def _bounded(value: str, limit: int) -> str
```

**Purpose**: Shortens a string to a maximum allowed length. It is used as a safety guard before storing source titles, snippets, and dates.

**Data flow**: It receives a text value and a character limit. It keeps only the first part of the text up to that limit, then returns the shortened text. It does not change anything outside itself.

**Call relations**: When `record_sources` prepares source data for storage, it calls `_bounded` so long titles, snippets, or dates do not exceed the file’s chosen limits before validation and database saving.

*Call graph*: called by 1 (record_sources).


##### `record_sources`  (lines 57–130)

```
async def record_sources(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, sources: tuple[RetrievedSource, ...]) -> None
```

**Purpose**: Saves a batch of retrieved sources for a specific conversation turn. It updates existing source records when the same URL is seen again, and trims the conversation’s saved source list down to the most recent 100.

**Data flow**: It receives the extension context, a conversation ID, a turn ID, and source objects. If there are no sources, it stops. Otherwise it opens a database transaction, cleans and validates each source, hashes the URL into a stable identifier, then inserts or updates the matching database row. After saving, it finds the newest allowed source records for that conversation and deletes older extras.

**Call relations**: `record_search_hits` and `record_fetched_page` both hand their source information to this function after converting it into `RetrievedSource` form. Inside the flow, it uses the extension context to open a transaction, `_bounded` to shorten fields, `ConversationSource` to validate the public source shape, `datetime.now` to timestamp observations, `sha256` to create URL fingerprints, and SQLAlchemy select/delete helpers to keep only the capped set.

*Call graph*: calls 2 internal fn (transaction, _bounded); called by 2 (record_fetched_page, record_search_hits); 5 external calls (__init__, now, sha256, delete, select).


##### `record_search_hits`  (lines 133–152)

```
async def record_search_hits(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, hits: tuple[SearchHit, ...]) -> None
```

**Purpose**: Converts search result items into saved conversation sources. It is the bridge between raw search results and the durable source notebook kept by this file.

**Data flow**: It receives search hits for one conversation turn. For each hit, it copies the URL, title, result text, and published date into a `RetrievedSource`. It then passes the full tuple of converted sources to `record_sources`, which does the validation and database writing.

**Call relations**: This function is used when the research extension has search results to remember. It does not write to the database itself; instead, it prepares the search-hit data and hands it off to `record_sources` for the shared saving logic.

*Call graph*: calls 1 internal fn (record_sources); 1 external calls (__init__).


##### `record_fetched_page`  (lines 155–173)

```
async def record_fetched_page(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, page: FetchedPage) -> None
```

**Purpose**: Saves a single fetched web page as a conversation source. This lets pages that were directly opened or read also appear in the conversation’s source list.

**Data flow**: It receives a fetched page for one conversation turn. It builds one `RetrievedSource` using the page URL as both URL and title, and using the page summary if available, otherwise the page text, as the snippet. It leaves the published date empty, then sends that one source to `record_sources`.

**Call relations**: This function is used after a page has been fetched. Like `record_search_hits`, it does not duplicate the database rules; it converts the page into the common source shape and lets `record_sources` handle validation, upserting, timestamps, and cleanup.

*Call graph*: calls 1 internal fn (record_sources); 1 external calls (__init__).


##### `_source_count`  (lines 176–186)

```
async def _source_count(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Counts how many saved sources are available for a conversation, up to the display limit. It is used to summarize the Sources slot without loading all source details.

**Data flow**: It receives a conversation slot context, which includes the extension context and conversation ID. It opens a database transaction, counts matching source rows for the current workspace and conversation, then returns `None` if there are none. If there are sources, it returns the count capped at 100.

**Call relations**: `SOURCES_SLOT` uses this as its summary function. When the wider conversation UI or runtime wants a quick badge or summary for the Sources slot, this function reads only the count from the database instead of fetching every saved source.

*Call graph*: 1 external calls (select).


##### `_read_sources`  (lines 189–217)

```
async def _read_sources(ctx: ConversationSlotContext) -> SourcesSlotPayload
```

**Purpose**: Reads the saved sources for a conversation and packages them for display in the Sources slot. It also reports whether there were more sources than the slot is allowed to show.

**Data flow**: It receives a conversation slot context. It opens a database transaction, selects source rows for the current workspace and conversation, orders them by most recently updated and then by their saved rank, and fetches one more than the display limit. It turns up to 100 rows into `ConversationSource` objects and returns a `SourcesSlotPayload` containing those sources plus a `truncated` flag that says whether extra rows existed.

**Call relations**: `SOURCES_SLOT` uses this as its read function. When the rest of the system asks to display the Sources slot, `_read_sources` pulls the saved rows from the database and converts them into the structured payload the conversation system expects.

*Call graph*: 3 external calls (__init__, __init__, select).
