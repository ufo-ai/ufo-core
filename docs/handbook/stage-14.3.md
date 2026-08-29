# Memory storage, recall, and consolidation  `stage-14.3`

This stage is the system’s long-term notebook. It works mostly behind the scenes during the main conversation loop: before answering, it can recall useful memories; after syncing or learning, it can clean and reshape what was stored. The events file defines a small shared “event language” and limits, so other code can consistently report when memory was recalled before a response. The store is the central cabinet and librarian. It saves memory rows, searches for the ones that match a user’s question, ranks them, hides or retires ones that should not be used, and keeps the search index up to date. The condenser is the editor. It reads raw pages and older memory rows, pulls out clear facts, merges duplicates, writes summaries and people profiles, and retires clutter that no longer helps. The research observations file keeps track of web sources found during research, safely trims their text, stores them, and shows them later in a “Sources” panel tied to the conversation.

## Files in this stage

### Recall event vocabulary
Shared event names and limits establish the small language used when memory is recalled before a response.

### `extensions/memory/ufo_ext_memory/events.py`

`config` · `cross-cutting`

The memory extension can emit structured events, which are machine-readable notes about something that happened. This file defines the small set of fixed values used for one of those notes: recalling memories before the system writes a response.

The main event name is `memory.pre_response_recall`. Other code can use that exact string when it records, sends, or listens for the memory-recall event. Keeping the name in one place avoids mistakes like two files using slightly different spellings for the same event.

The file also sets two safety limits. `MAX_RECALLED_MEMORY_IDS` says that at most 8 recalled memory identifiers should be included in the event. This keeps event records short and avoids flooding logs or telemetry with too much detail. `MAX_RECALL_ERROR_CLASS_CHARS` limits an error class name to 128 characters, which protects event data from becoming unexpectedly large if something goes wrong.

In everyday terms, this file is like a label sheet and a few packing rules for memory-related reports. Without it, callers might invent inconsistent event names or attach oversized details, making the system harder to observe and debug.


### Memory consolidation and store
Raw pages and memory rows are condensed into durable facts, summaries, profiles, and indexed records that can later be recalled, searched, hidden, or retired.

### `extensions/memory/ufo_ext_memory/condenser.py`

`domain_logic` · `background page-change consumption and periodic memory cleanup`

The memory system stores many small rows of knowledge. Without this file, synced documents would stay as search chunks or pile up as repeated, stale, hard-to-read statements. This file is the cleanup and distillation layer. It is like an editor for a growing notebook: it pulls out facts from source pages, groups related facts into summaries, removes duplicate restatements, rewrites section and overview paragraphs, builds a People/profile band, and occasionally reads a whole page to retire rows that repeat other rows. Most of these jobs are periodic background jobs. Some use a model, meaning an AI text service, but they do the model work before opening database write transactions so the database is not held open during slow calls. The file is careful about safety: it only retires page-derived facts when replacements were actually written, ignores machine-generated status streams that would clutter memory, checks that source pages are still at the revision being processed, validates model tool output before storing it, bounds every payload size, and uses locks when replacing live paragraphs or collapsing duplicates. The result is that members see a wiki-like memory page that reflects what is currently true, rather than a ledger of every old statement ever written.

#### Function details

##### `section_headings`  (lines 178–184)

```
def section_headings(subject: str) -> dict[MemoryKind, str]
```

**Purpose**: Chooses the human-readable section titles for a memory page. Shared workspace pages and individual member pages use different wording, so the summaries speak to the right audience.

**Data flow**: It receives a subject string, checks whether that subject is the shared workspace subject, and returns the matching dictionary of memory kinds to headings. Nothing is stored or changed.

**Call relations**: Section and page writers call this when they need to know what headings exist for a subject. It relies on the subject helper that tells shared workspace subjects apart from personal ones.

*Call graph*: called by 3 (_page, _sections, _summarize); 1 external calls (subject_shared).


##### `live_page_link`  (lines 246–259)

```
def live_page_link() -> ColumnElement[bool]
```

**Purpose**: Builds the database condition that says a fact still matches the current version of the source page it came from. This prevents summaries from using facts from old page revisions.

**Data flow**: It reads no rows by itself. It returns a SQL condition tying a memory item to a page with the same page id, workspace, subject, and revision.

**Call relations**: The page pass uses this directly when reading page-derived rows, and member_servable uses it to decide whether a page-derived memory row should still be visible.

*Call graph*: called by 2 (_page, member_servable); 1 external calls (and_).


##### `member_servable`  (lines 262–274)

```
def member_servable() -> ColumnElement[bool]
```

**Purpose**: Builds the database condition for rows a member is allowed to see. Tool-written rows are visible, while page-derived rows are visible only if their source page is still current.

**Data flow**: It produces a SQL condition: either the row has no source page, or a matching live page exists. It does not execute the query itself.

**Call relations**: Section, overview, and profile readers use this so their model-written prose is based only on rows that would actually be served to a member.

*Call graph*: calls 1 internal fn (live_page_link); called by 4 (_facts, _facts, _facts, _sections); 2 external calls (or_, select).


##### `ExtractedFact.within_row_budget`  (lines 314–315)

```
def within_row_budget(cls, body: str) -> str
```

**Purpose**: Keeps a model-extracted fact short enough to fit a memory row. It trims at a word boundary so readers do not see chopped-off words.

**Data flow**: A fact body string comes in. The function clips it to the configured memory-row length and returns the clipped string.

**Call relations**: Pydantic, the validation library, calls this while validating ExtractedFact objects created from the model's extraction output.

*Call graph*: 1 external calls (clip_to_word).


##### `FactDeriver.apply`  (lines 351–366)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Processes a batch of changed source pages and turns eligible pages into stored memory facts. It also retires facts from pages that disappeared or are machine-status streams.

**Data flow**: A tuple of page changes comes in. The function checks current page states, retires facts for deleted or skipped machine pages, filters usable live pages, processes them in small batches, and asks the store to supersede older facts after replacements land.

**Call relations**: This is the public entry for page-change consumption. It hands eligible page batches to FactDeriver._derive, then gives the store the ids of facts that should remain for each page.

*Call graph*: calls 1 internal fn (_derive); 1 external calls (batched).


##### `FactDeriver._derive`  (lines 368–407)

```
async def _derive(self, pages: tuple[PageChange, ...]) -> dict[UUID, frozenset[UUID]]
```

**Purpose**: Safely derives facts for one bounded group of pages and commits them only if the pages are still at the expected revision. This avoids writing facts from stale content.

**Data flow**: It receives page changes, rechecks their current page state, asks _extract for facts, validates each fact's page id, commits each fact as a MemoryWrite, and returns a map from page id to the committed fact ids for that page.

**Call relations**: FactDeriver.apply calls this for each batch. It calls FactDeriver._extract for model extraction and then uses the memory store to commit facts before the caller retires older page facts.

*Call graph*: calls 1 internal fn (_extract); called by 1 (apply); 1 external calls (__init__).


##### `FactDeriver._extract`  (lines 409–486)

```
async def _extract(self, pages: tuple[PageChange, ...]) -> tuple[ExtractedFact, ...]
```

**Purpose**: Asks the model to extract concrete facts from a small group of source pages. It validates the tool output and removes near-duplicate facts from the same page.

**Data flow**: It builds a compact JSON payload from page ids, titles, streams, and clipped bodies. The model must call a structured tool named record_facts. The returned entries are validated as ExtractedFact objects; invalid entries are dropped; same-page restatements are collapsed; a tuple of facts comes out.

**Call relations**: FactDeriver._derive calls this before committing anything. It uses _restates to avoid storing two extracted facts that say the same thing.

*Call graph*: calls 1 internal fn (_restates); called by 1 (_derive); 4 external calls (__init__, __init__, __init__, dumps).


##### `_content_words`  (lines 489–490)

```
def _content_words(body: str) -> frozenset[str]
```

**Purpose**: Reduces text to the meaningful words used for restatement checks. Common filler words are removed so overlap is based on content, not grammar glue.

**Data flow**: A text string comes in. It is lowercased, split into words, stripped of filler words, and returned as a set.

**Call relations**: _restates calls this for both pieces of text it compares.

*Call graph*: called by 1 (_restates); 1 external calls (split).


##### `_restates`  (lines 493–505)

```
def _restates(kept: str, candidate: str) -> bool
```

**Purpose**: Decides whether two fact sentences are basically the same claim. It looks for high overlap in the shorter sentence's meaningful words.

**Data flow**: Two strings come in. The function extracts content words, rejects very short comparisons, measures overlap, and returns true or false.

**Call relations**: FactDeriver._extract uses this while cleaning a model response, keeping the longer version when two entries restate the same page claim.

*Call graph*: calls 1 internal fn (_content_words); called by 1 (_extract).


##### `cosine`  (lines 508–516)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Measures how close two embedding vectors are. An embedding is a list of numbers representing text meaning, and cosine similarity compares their direction.

**Data flow**: Two numeric vectors come in. The function computes their dot product divided by their lengths and returns a score; if either vector has no length, it returns 0.0.

**Call relations**: Both MemoryConsolidator._clusters and MemoryDeduper._clusters use this score to decide whether rows are similar enough to group.

*Call graph*: called by 2 (_clusters, _clusters); 2 external calls (sqrt, sumprod).


##### `MemoryConsolidator.run`  (lines 546–555)

```
async def run(self) -> None
```

**Purpose**: Runs the periodic job that turns clusters of old related facts into one semantic summary. It does nothing if no model is configured.

**Data flow**: It reads old eligible facts, groups them by subject, embeds each group, clusters similar facts in a worker thread, and consolidates clusters large enough to matter.

**Call relations**: This is the public job method. It coordinates _aged_facts, _buckets, _embed, _clusters, and _consolidate in that order.

*Call graph*: calls 4 internal fn (_aged_facts, _buckets, _consolidate, _embed); 1 external calls (to_thread).


##### `MemoryConsolidator._aged_facts`  (lines 557–588)

```
async def _aged_facts(self) -> tuple[_AgedFact, ...]
```

**Purpose**: Finds old, live, tool-written fact rows that are candidates for consolidation. It excludes page-derived rows and anything already retired or superseded.

**Data flow**: It computes an age cutoff, queries the memory table for matching fact rows in the workspace, and returns them as _AgedFact records.

**Call relations**: MemoryConsolidator.run calls this first to get the raw material for consolidation.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryConsolidator._buckets`  (lines 590–599)

```
def _buckets(self, facts: tuple[_AgedFact, ...]) -> tuple[tuple[str, tuple[_AgedFact, ...]], ...]
```

**Purpose**: Groups candidate facts by subject so only facts about the same page or person are compared. It also keeps each group bounded and newest-first.

**Data flow**: A tuple of aged facts comes in. The function builds subject groups, sorts each group by recency, limits each group, and returns subject-to-facts pairs.

**Call relations**: MemoryConsolidator.run uses these buckets before embedding and clustering.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._embed`  (lines 601–605)

```
async def _embed(self, facts: tuple[_AgedFact, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: Gets meaning vectors for fact text so related facts can be clustered. Long bodies are clipped before embedding to keep the request bounded.

**Data flow**: A tuple of aged facts comes in. Their bodies are sent to the embedding client, and the returned vectors are mapped back to fact ids.

**Call relations**: MemoryConsolidator.run calls this before sending the facts and vectors to _clusters.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._clusters`  (lines 607–628)

```
def _clusters(self, facts: tuple[_AgedFact, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_AgedFact, ...], ...]
```

**Purpose**: Groups related facts using embedding similarity. It starts with the newest facts and adds each fact to the first close-enough cluster.

**Data flow**: Facts and their embedding vectors come in. The function compares each fact with cluster heads using cosine similarity and returns clusters of facts.

**Call relations**: MemoryConsolidator.run runs this in a worker thread so CPU-heavy comparisons do not block the async event loop.

*Call graph*: calls 1 internal fn (cosine).


##### `MemoryConsolidator._consolidate`  (lines 630–694)

```
async def _consolidate(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> None
```

**Purpose**: Writes one summary row for a cluster and marks the original fact rows as superseded by it. It checks that the source rows have not changed since clustering.

**Data flow**: A model and a fact cluster come in. The function asks _summarize for text, locks and verifies the donor rows, inserts a semantic summary row, and updates donors to point to that summary.

**Call relations**: MemoryConsolidator.run calls this for each large enough cluster. It calls _summarize before opening the write transaction.

*Call graph*: calls 1 internal fn (_summarize); called by 1 (run); 4 external calls (insert, select, update, uuid4).


##### `MemoryConsolidator._summarize`  (lines 696–706)

```
async def _summarize(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> str
```

**Purpose**: Asks the model to turn a cluster of related facts into a short summary paragraph. The result is trimmed to the overview-style paragraph budget.

**Data flow**: A model and fact cluster come in. The fact bodies are clipped and sent as JSON to the model, the returned text is stripped, budgeted by _to_overview_budget, and returned.

**Call relations**: MemoryConsolidator._consolidate calls this to get the semantic row body it will insert.

*Call graph*: calls 2 internal fn (complete, _to_overview_budget); called by 1 (_consolidate); 3 external calls (__init__, __init__, dumps).


##### `_to_overview_budget`  (lines 709–721)

```
def _to_overview_budget(summary: str) -> str
```

**Purpose**: Cuts a generated paragraph down to the allowed size while preserving whole sentences when possible. If even the first sentence is too long, it falls back to word-boundary clipping.

**Data flow**: A summary string comes in. The function keeps sentences until sentence, word, or character limits would be exceeded, then returns the kept text or a clipped fallback.

**Call relations**: Consolidation, section writing, and overview writing all use this to enforce the same readable paragraph limits.

*Call graph*: called by 3 (_summarize, _write, _summarize); 1 external calls (clip_to_word).


##### `_recency`  (lines 724–725)

```
def _recency(fact: _AgedFact) -> tuple[datetime, UUID]
```

**Purpose**: Provides a stable sorting key for facts by creation time and id. This lets newer facts be considered first while breaking ties consistently.

**Data flow**: An aged fact comes in. The function returns its created_at timestamp and id as a tuple.

**Call relations**: MemoryConsolidator uses this helper when ordering facts inside buckets and clusters.


##### `_Group.key`  (lines 745–746)

```
def key(self) -> tuple[str, str]
```

**Purpose**: Returns the ordering identity for a deduplication group. The cursor uses this to walk groups in a stable order.

**Data flow**: It reads the group's subject and item class and returns them as a tuple.

**Call relations**: MemoryDeduper.run uses this property to decide which group comes after the last swept group.


##### `_Group.fingerprint`  (lines 749–750)

```
def fingerprint(self) -> list[JsonValue]
```

**Purpose**: Returns a compact snapshot of a deduplication group's current state. If this snapshot has not changed, the deduper can skip expensive embedding work.

**Data flow**: It reads the group's live-copy count and latest update time, converts the time to text, and returns both as a JSON-friendly list.

**Call relations**: MemoryDeduper.run compares this value with a stored fingerprint before and after sweeping a group.


##### `MemoryDeduper.run`  (lines 794–806)

```
async def run(self) -> None
```

**Purpose**: Runs one step of the duplicate-cleanup sweep. It visits one eligible group per tick and collapses near-duplicate tool-written rows onto the newest copy.

**Data flow**: It reads eligible groups, reads the saved cursor, picks the next group, stores the new cursor, checks the group's fingerprint, deduplicates if needed, and stores the fingerprint after success.

**Call relations**: This public job method coordinates _groups, _cursor, and _dedup_group, using the scoped key-value store for cursor and fingerprint state.

*Call graph*: calls 3 internal fn (_cursor, _dedup_group, _groups).


##### `MemoryDeduper._cursor`  (lines 808–820)

```
def _cursor(self, stored: JsonValue | None) -> tuple[str, ...]
```

**Purpose**: Parses the saved deduplication cursor. It treats missing data as first run and malformed data as an error.

**Data flow**: A stored JSON value comes in. The function returns an empty tuple for no cursor, a subject/item-class tuple for a valid cursor, or raises for corrupted data.

**Call relations**: MemoryDeduper.run calls this before choosing which group to sweep next.

*Call graph*: called by 1 (run).


##### `MemoryDeduper._groups`  (lines 822–848)

```
async def _groups(self) -> tuple[_Group, ...]
```

**Purpose**: Finds groups of live tool-written rows that have enough copies to be worth deduplicating. It ignores sections and young rows.

**Data flow**: It queries memory rows grouped by subject and item class, counts live copies, records the latest update time, and returns _Group records sorted by cursor order.

**Call relations**: MemoryDeduper.run calls this to know what groups exist and whether there is any dedupe work.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryDeduper._dedup_group`  (lines 850–855)

```
async def _dedup_group(self, group: _Group) -> None
```

**Purpose**: Deduplicates one subject/class group. It reads live copies, embeds them, clusters similar rows, and collapses clusters with enough copies.

**Data flow**: A group comes in. The function fetches its rows, gets embeddings, clusters them in a worker thread, and calls _collapse for duplicate clusters.

**Call relations**: MemoryDeduper.run calls this after cursor and fingerprint checks. It coordinates _live_copies, _embed, _clusters, and _collapse.

*Call graph*: calls 3 internal fn (_collapse, _embed, _live_copies); called by 1 (run); 1 external calls (to_thread).


##### `MemoryDeduper._live_copies`  (lines 857–872)

```
async def _live_copies(self, group: _Group) -> tuple[_LiveCopy, ...]
```

**Purpose**: Reads the current live rows for one deduplication group, newest first. The newest order matters because the first row in a duplicate cluster is kept.

**Data flow**: A group comes in. The function builds the live-group filter, queries ids and bodies, applies the group size limit, and returns _LiveCopy records.

**Call relations**: MemoryDeduper._dedup_group calls this before embedding and clustering.

*Call graph*: calls 1 internal fn (_live_group); called by 1 (_dedup_group); 2 external calls (__init__, select).


##### `MemoryDeduper._embed`  (lines 874–881)

```
async def _embed(self, copies: tuple[_LiveCopy, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: Gets embedding vectors for candidate duplicate rows. It batches requests so large groups do not become one oversized embedding call.

**Data flow**: A tuple of live copies comes in. Their clipped bodies are sent to the embedding client in batches, and a map from row id to vector comes out.

**Call relations**: MemoryDeduper._dedup_group calls this before _clusters compares rows by meaning.

*Call graph*: called by 1 (_dedup_group); 1 external calls (batched).


##### `MemoryDeduper._clusters`  (lines 883–912)

```
def _clusters(self, copies: tuple[_LiveCopy, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_LiveCopy, ...], ...]
```

**Purpose**: Groups near-duplicate rows by embedding similarity. It keeps the newest copy as each cluster head because input rows are newest first.

**Data flow**: Copies and embeddings come in. Each copy is compared with existing cluster heads using cosine similarity and either joins a close cluster or starts a new one.

**Call relations**: MemoryDeduper._dedup_group runs this in a worker thread, then collapses clusters large enough to count as duplicates.

*Call graph*: calls 1 internal fn (cosine).


##### `MemoryDeduper._collapse`  (lines 914–942)

```
async def _collapse(self, group: _Group, cluster: tuple[_LiveCopy, ...]) -> None
```

**Purpose**: Marks all duplicate copies except the newest one as superseded by the newest copy. It locks and verifies rows first so it does not point donors at a row that changed.

**Data flow**: A group and duplicate cluster come in. The function picks the head, locks all cluster rows that are still live, checks their bodies match expectations, and updates donor rows to point to the head.

**Call relations**: MemoryDeduper._dedup_group calls this for each duplicate cluster. It uses _live_group to ensure it only touches rows still eligible for deduplication.

*Call graph*: calls 1 internal fn (_live_group); called by 1 (_dedup_group); 2 external calls (select, update).


##### `MemoryDeduper._live_group`  (lines 944–953)

```
def _live_group(self, group: _Group) -> tuple[ColumnElement[bool], ...]
```

**Purpose**: Builds the database filters for rows that count as live members of a deduplication group. This keeps all reads and writes using the same definition.

**Data flow**: A group comes in. The function returns SQL conditions for workspace, subject, class, no source page, not superseded, not retired, and old enough.

**Call relations**: MemoryDeduper._live_copies uses it to read rows, and _collapse uses it to lock and update the same population.

*Call graph*: called by 2 (_collapse, _live_copies); 1 external calls (now).


##### `_rewrite_in_place`  (lines 974–1024)

```
async def _rewrite_in_place(connection: AsyncConnection, workspace_id: UUID, standing: _Standing, paragraph: str, confidence: int) -> None
```

**Purpose**: Writes a new standing paragraph and supersedes the old one at the same position. This keeps exactly one live section or overview paragraph for a place.

**Data flow**: A database connection, workspace id, standing identity, paragraph text, and confidence come in. The function locks existing live paragraph rows, inserts the new paragraph, and updates old ones to point to it.

**Call relations**: SectionWriter.run and OverviewWriter.run both call this so their replacement rules stay identical.

*Call graph*: called by 2 (run, run); 5 external calls (execute, insert, select, update, uuid4).


##### `_retire_standing`  (lines 1027–1055)

```
async def _retire_standing(connection: AsyncConnection, workspace_id: UUID, standing: _Standing) -> None
```

**Purpose**: Retires a standing paragraph when its page or section no longer has enough facts to justify it. This prevents old prose from floating above rows that no longer support it.

**Data flow**: A connection, workspace id, and standing identity come in. Matching live paragraph rows are marked retired, their embedding digest is cleared, and their update time is refreshed.

**Call relations**: SectionWriter.run calls this for bands that fell below the section floor, and OverviewWriter.run calls it when the shared page falls below the overview floor.

*Call graph*: called by 2 (run, run); 2 external calls (execute, update).


##### `SectionWriter.run`  (lines 1081–1104)

```
async def run(self) -> None
```

**Purpose**: Runs the periodic pass that rewrites section paragraphs for bands with enough live facts. It also retires section paragraphs whose bands no longer qualify.

**Data flow**: It exits if there is no model, reads qualifying sections and currently standing sections, retires stale standings, fetches facts for each qualifying band, asks the model for a paragraph, and rewrites that paragraph in place.

**Call relations**: This public job method coordinates _sections, _standing, _facts, _summarize, _retire_standing, and _rewrite_in_place.

*Call graph*: calls 6 internal fn (_facts, _sections, _standing, _summarize, _retire_standing, _rewrite_in_place).


##### `SectionWriter._sections`  (lines 1106–1139)

```
async def _sections(self) -> tuple[_Standing, ...]
```

**Purpose**: Finds subject-and-kind bands that have enough live facts to deserve a section summary. It also checks that each memory kind has a real page heading.

**Data flow**: It queries live servable fact rows grouped by subject and memory kind, filters by minimum count, validates headings, and returns _Standing identities for section paragraphs.

**Call relations**: SectionWriter.run calls this first to know which bands should be written. It uses member_servable and section_headings to match what readers can see.

*Call graph*: calls 2 internal fn (member_servable, section_headings); called by 1 (run); 2 external calls (__init__, select).


##### `SectionWriter._standing`  (lines 1141–1159)

```
async def _standing(self) -> tuple[_Standing, ...]
```

**Purpose**: Finds section paragraphs that are currently live, whether or not their underlying facts still qualify. This lets the writer retire stale paragraphs.

**Data flow**: It queries live section rows grouped by subject and memory kind and returns them as _Standing identities.

**Call relations**: SectionWriter.run compares this result with _sections to find paragraphs that should be retired.

*Call graph*: called by 1 (run); 2 external calls (__init__, select).


##### `SectionWriter._facts`  (lines 1161–1182)

```
async def _facts(self, section: _Standing) -> tuple[_SectionFact, ...]
```

**Purpose**: Reads the live facts for one section band, newest first and bounded. These are the facts the model will summarize.

**Data flow**: A section identity comes in. The function queries servable, live fact rows for that subject and memory kind, limits the result, and returns body/confidence records.

**Call relations**: SectionWriter.run calls this before asking _summarize to write the band paragraph.

*Call graph*: calls 1 internal fn (member_servable); called by 1 (run); 2 external calls (__init__, select).


##### `SectionWriter._summarize`  (lines 1184–1203)

```
async def _summarize(self, model: ModelAccess, section: _Standing, facts: tuple[_SectionFact, ...]) -> str
```

**Purpose**: Asks the model to write one paragraph for a section band. It includes the section heading so the paragraph fits where it will be displayed.

**Data flow**: A model, section identity, and facts come in. The function sends a JSON payload with the heading and clipped fact bodies, receives text, trims it to budget, and returns it.

**Call relations**: SectionWriter.run calls this after reading facts and before rewriting the paragraph with _rewrite_in_place.

*Call graph*: calls 3 internal fn (complete, _to_overview_budget, section_headings); called by 1 (run); 3 external calls (__init__, __init__, dumps).


##### `OverviewWriter.run`  (lines 1227–1248)

```
async def run(self) -> None
```

**Purpose**: Runs the periodic pass that rewrites the shared workspace overview paragraph. If there are too few facts, it retires the overview instead.

**Data flow**: It exits if no model is configured, reads shared live facts, retires the overview if the count is too low, otherwise reads the workspace domain, asks the model for a paragraph, and rewrites the overview in place.

**Call relations**: This public job method coordinates _facts, workspace_domain, _write, _retire_standing, and _rewrite_in_place.

*Call graph*: calls 4 internal fn (_facts, _write, _retire_standing, _rewrite_in_place); 2 external calls (__init__, workspace_domain).


##### `OverviewWriter._facts`  (lines 1250–1271)

```
async def _facts(self) -> tuple[_SectionFact, ...]
```

**Purpose**: Reads the shared workspace facts used to write the opening overview paragraph. It spans all fact bands, not just one section.

**Data flow**: It queries servable, live, shared-subject fact rows, ordered newest first and capped at the overview limit, and returns body/confidence records.

**Call relations**: OverviewWriter.run calls this to decide whether an overview should exist and what the model should write from.

*Call graph*: calls 1 internal fn (member_servable); called by 1 (run); 2 external calls (__init__, select).


##### `OverviewWriter._write`  (lines 1273–1293)

```
async def _write(self, model: ModelAccess, domain: str | None, facts: tuple[_SectionFact, ...]) -> str
```

**Purpose**: Asks the model to write the workspace overview paragraph. The workspace domain is included when available so the model can name the company context.

**Data flow**: A model, optional domain, and facts come in. The function sends clipped fact bodies as JSON, receives text from the model, trims it to budget, and returns it.

**Call relations**: OverviewWriter.run calls this after reading facts and the domain, then passes the result to _rewrite_in_place.

*Call graph*: calls 2 internal fn (complete, _to_overview_budget); called by 1 (run); 3 external calls (__init__, __init__, dumps).


##### `WrittenProfile.within_role_budget`  (lines 1330–1331)

```
def within_role_budget(cls, role: str) -> str
```

**Purpose**: Keeps a generated role phrase short. A role should be a phrase, not a sentence.

**Data flow**: A role string comes in. It is clipped at a word boundary to the role length limit and returned.

**Call relations**: Pydantic calls this while validating WrittenProfile entries from the People model pass.

*Call graph*: 1 external calls (clip_to_word).


##### `WrittenProfile.within_row_budget`  (lines 1335–1336)

```
def within_row_budget(cls, focus: str) -> str
```

**Purpose**: Keeps a generated focus sentence within the normal memory-row length. This prevents oversized profile text from being stored.

**Data flow**: A focus string comes in. It is clipped at a word boundary to the memory body limit and returned.

**Call relations**: Pydantic calls this while validating WrittenProfile entries from the People model pass.

*Call graph*: 1 external calls (clip_to_word).


##### `ProfileWriter.run`  (lines 1374–1389)

```
async def run(self) -> None
```

**Purpose**: Runs the periodic People pass that writes each roster member's role and current focus. It stores profiles separately from normal memory rows.

**Data flow**: It exits if no model is configured, reads the roster, reads shared workspace facts, asks the model for profile entries, matches entries back to roster names, and stores valid matches.

**Call relations**: This public job method coordinates _roster, _facts, _write, and _store.

*Call graph*: calls 4 internal fn (_facts, _roster, _store, _write).


##### `ProfileWriter._roster`  (lines 1391–1410)

```
async def _roster(self) -> tuple[_Rostered, ...]
```

**Purpose**: Reads the workspace member roster in the same way the seat system sees it. It records each member's email/name and whether they are admin/member and seated/unseated.

**Data flow**: It opens a transaction, asks Seats for a snapshot, limits the roster, and converts members into _Rostered records.

**Call relations**: ProfileWriter.run calls this before building the model payload for profile writing.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `ProfileWriter._facts`  (lines 1412–1429)

```
async def _facts(self) -> tuple[str, ...]
```

**Purpose**: Reads shared workspace facts that everyone on the roster could already see. This avoids using one person's private memory to describe them to colleagues.

**Data flow**: It queries servable, live, shared-subject fact bodies, ordered newest first and capped at the profile fact limit, and returns text strings.

**Call relations**: ProfileWriter.run calls this before asking _write to infer roles and focus.

*Call graph*: calls 1 internal fn (member_servable); called by 1 (run); 1 external calls (select).


##### `ProfileWriter._write`  (lines 1431–1479)

```
async def _write(self, model: ModelAccess, roster: tuple[_Rostered, ...], facts: tuple[str, ...]) -> tuple[WrittenProfile, ...]
```

**Purpose**: Asks the model to write People entries as structured tool output. Each returned profile is validated independently so one bad entry does not spoil the rest.

**Data flow**: A model, roster, and facts come in. The function sends member info and clipped facts, requires a write_people tool call, validates each listed person as WrittenProfile, and returns valid profiles.

**Call relations**: ProfileWriter.run calls this after reading roster and facts, then matches returned names to roster members before storage.

*Call graph*: calls 1 internal fn (turn); called by 1 (run); 4 external calls (__init__, __init__, __init__, dumps).


##### `ProfileWriter._store`  (lines 1481–1510)

```
async def _store(self, entries: tuple[tuple[UUID, WrittenProfile], ...]) -> None
```

**Purpose**: Writes profile entries to the memory_profile table, replacing any existing profile for the same workspace member. It uses an upsert, meaning insert-or-update.

**Data flow**: A tuple of member ids and profile entries comes in. The function records a timestamp and, inside one transaction, inserts each row or updates the existing row's role, focus, and written time.

**Call relations**: ProfileWriter.run calls this after validating and matching model-written profiles.

*Call graph*: called by 1 (run); 3 external calls (now, insert, insert).


##### `admitted_curation`  (lines 1564–1599)

```
def admitted_curation(bands: tuple[tuple[int, ...], ...], retire: tuple[RetiredRow, ...]) -> AdmittedCuration
```

**Purpose**: Checks whether the page curation model's requested retirements are safe enough to apply. It is a guardrail before destructive changes.

**Data flow**: The sent row ids per band and requested retirements come in. The function drops invalid ids, requires each retired row to name a surviving duplicate, rejects over-large band losses, and returns admitted row indexes or a refusal reason.

**Call relations**: PagePass._retiring calls this before converting model-requested row indexes into database ids.

*Call graph*: called by 1 (_retiring); 1 external calls (__init__).


##### `PagePass.run`  (lines 1647–1660)

```
async def run(self) -> None
```

**Purpose**: Runs the periodic whole-page curation pass. It asks the deploy model to find rows a subject page reads better without, then retires admitted rows.

**Data flow**: It exits if no model is configured, reads subjects with enough rows, loads each page, asks the model to curate it, filters retirements through safety checks, and applies accepted retirements.

**Call relations**: This public job method coordinates _subjects, _page, _curate, _retiring, and _apply.

*Call graph*: calls 5 internal fn (_apply, _curate, _page, _retiring, _subjects).


##### `PagePass._subjects`  (lines 1662–1678)

```
async def _subjects(self) -> tuple[str, ...]
```

**Purpose**: Finds subjects whose pages are large enough to be worth whole-page curation. Small pages are skipped because a reader can scan them easily.

**Data flow**: It queries live fact rows grouped by subject, keeps groups meeting the minimum row count, sorts them, and returns subject strings.

**Call relations**: PagePass.run calls this to decide which pages to inspect.

*Call graph*: called by 1 (run); 1 external calls (select).


##### `PagePass._page`  (lines 1680–1726)

```
async def _page(self, subject: str) -> tuple[_Band, ...]
```

**Purpose**: Builds the model-readable version of one subject's page: section headings, current section summaries, and page-derived fact rows. It only includes rows tied to live source page revisions.

**Data flow**: A subject comes in. For each heading, it queries live page-derived fact rows, assigns small numeric indexes, reads the standing section summary if any, and returns bands of rows.

**Call relations**: PagePass.run calls this before asking _curate. It uses section_headings and live_page_link so the model sees the same page structure readers see.

*Call graph*: calls 2 internal fn (live_page_link, section_headings); called by 1 (run); 3 external calls (__init__, __init__, select).


##### `PagePass._curate`  (lines 1728–1777)

```
async def _curate(self, model: ModelAccess, bands: tuple[_Band, ...]) -> CuratedPage
```

**Purpose**: Asks the model to identify rows that should be retired from a whole page. The model must answer through a structured curate_page tool call.

**Data flow**: Page bands come in. The function sends section summaries and clipped row bodies with small row indexes, validates returned retirement entries, and returns a CuratedPage object.

**Call relations**: PagePass.run calls this after loading a page, then sends its requested retirements to _retiring for safety filtering.

*Call graph*: calls 1 internal fn (turn); called by 1 (run); 5 external calls (__init__, __init__, __init__, __init__, dumps).


##### `PagePass._retiring`  (lines 1779–1790)

```
def _retiring(self, subject: str, bands: tuple[_Band, ...], retire: tuple[RetiredRow, ...]) -> frozenset[UUID]
```

**Purpose**: Turns model-requested row indexes into actual memory row ids, but only after safety checks. If the whole curation is refused, it logs a warning and retires nothing.

**Data flow**: A subject, bands, and requested RetiredRow entries come in. The function calls admitted_curation, logs refusal details if needed, and returns the ids of rows whose indexes were admitted.

**Call relations**: PagePass.run calls this between _curate and _apply.

*Call graph*: calls 1 internal fn (admitted_curation); called by 1 (run); 1 external calls (warn).


##### `PagePass._apply`  (lines 1792–1808)

```
async def _apply(self, retiring: frozenset[UUID]) -> None
```

**Purpose**: Retires accepted memory rows and removes their embedding digest so search indexing can withdraw them. It does not supersede them with another row.

**Data flow**: A set of memory row ids comes in. The function updates matching live rows in the workspace with retired_at, clears embedding fields, and refreshes updated_at.

**Call relations**: PagePass.run calls this after _retiring chooses safe row ids to remove from the page.

*Call graph*: called by 1 (run); 1 external calls (update).


### `extensions/memory/ufo_ext_memory/store.py`

`domain_logic` · `request handling and background indexing`

This file is the heart of the memory feature. It owns the database tables for stored memories, links those memories back to source pages, and mirrors source pages that can be searched. A memory can come from a direct write or from a synced page. The write path deliberately stays simple: it stores the row and marks it as needing indexing, but it does not split text into chunks or create embeddings, which are numeric fingerprints used for meaning-based search. That heavier work is done later by background indexers.

Recall works like asking several librarians at once. One search looks for matching words, another looks for similar meaning, and a small emergency search checks very new rows that have not been indexed yet. The results are fused, filtered by subject and source permissions, adjusted for age and confidence, deduplicated, and balanced so one type of memory does not crowd out all others.

The file also protects against stale source data. Page-derived memories and page search chunks are only shown when the underlying page still has the same subject and revision. When pages move on or disappear, indexers withdraw old chunks and mark affected memories for re-checking.

#### Function details

##### `recall_subjects`  (lines 190–191)

```
def recall_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: Returns the set of memory subjects that an audience is allowed to recall. A subject is the visibility label used to decide which memories belong in a search.

**Data flow**: It receives an Audience object, asks the shared audience helper to turn it into subject strings, and returns those strings as a frozen set.

**Call relations**: This is a small adapter around the SDK audience logic. Other memory code can use it before calling recall so recall searches only the subjects the audience should see.

*Call graph*: 1 external calls (audience_subjects).


##### `clip_to_word`  (lines 194–203)

```
def clip_to_word(text: str, limit: int) -> str
```

**Purpose**: Shortens text to a maximum number of characters without cutting through the middle of a word. It adds an ellipsis when trimming is needed.

**Data flow**: It takes text and a character limit. If the text already fits, it returns it unchanged; otherwise it backs up to the last space that fits, trims stray punctuation, adds an ellipsis, and returns the shortened string.

**Call relations**: This helper is used by callers that want to fit memory text into the same size limits enforced by MemoryWrite. It is separate from validation because committing overlong text is treated as an error, not silently fixed.


##### `_granted_link`  (lines 206–214)

```
def _granted_link(source_ids: frozenset[UUID]) -> ColumnElement[bool]
```

**Purpose**: Builds a database permission check for page-derived memories. A memory is readable if the reader has access to at least one source page that produced it.

**Data flow**: It receives source IDs the reader may access and creates a SQL EXISTS condition that checks the memory_source link table for any matching source tied to the current memory row.

**Call relations**: MemoryStore._untail_leg uses it when searching unindexed memories, and MemoryStore._enrich uses it when reading back recalled rows. It keeps source-derived facts from leaking to readers who lack the right source grants.

*Call graph*: called by 2 (_enrich, _untail_leg); 1 external calls (exists).


##### `inventory`  (lines 254–323)

```
async def inventory(transaction: Transaction, workspace_id: UUID) -> tuple[MemoryInventoryItem, ...]
```

**Purpose**: Returns a bounded, newest-first inventory of stored memories for an operator or explorer view. It includes both the raw stored fields and computed recall signals such as age and decay.

**Data flow**: It takes a transaction opener and workspace ID, reads recent memory rows, reads their source links, computes age, half-life, and decay using one current timestamp, and returns MemoryInventoryItem objects.

**Call relations**: It calls _aware, half_life_days, and decay_multiplier so the explorer reports the same aging math that recall uses. Unlike recall, it does not search or hide superseded rows; it shows the durable store for inspection.

*Call graph*: calls 3 internal fn (_aware, decay_multiplier, half_life_days); 3 external calls (__init__, now, select).


##### `_aware`  (lines 326–327)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Ensures a datetime has timezone information. If a timestamp is missing a timezone, it treats it as UTC.

**Data flow**: It receives a datetime and returns it unchanged if it already has a timezone; otherwise it returns a copy marked as UTC.

**Call relations**: Inventory, decay_multiplier, MemoryStore._enrich, and MemoryStore.search_sources use it before comparing or returning times. This avoids subtle mistakes when mixing timezone-aware and timezone-naive timestamps.

*Call graph*: called by 4 (_enrich, search_sources, decay_multiplier, inventory); 1 external calls (replace).


##### `MemoryWrite.body_is_within_its_class_budget`  (lines 355–361)

```
def body_is_within_its_class_budget(self) -> Self
```

**Purpose**: Validates that a memory body is not longer than the limit for its class. Short list-like facts and longer overview paragraphs have different budgets.

**Data flow**: It reads the MemoryWrite item_class and body length. If the body is too long, it raises a validation error; otherwise it returns the same MemoryWrite object.

**Call relations**: Pydantic runs this validator when a MemoryWrite is created. MemoryStore.commit can then trust that stored bodies already fit the intended reading format.


##### `MemoryWrite.page_origin_is_complete`  (lines 364–372)

```
def page_origin_is_complete(self) -> Self
```

**Purpose**: Validates that page-derived memories include all required page-origin fields. A memory cannot be partly linked to a page.

**Data flow**: It checks whether page ID, page revision, and source ID are either all present or all absent. If only some are present, it raises a validation error; otherwise it returns the MemoryWrite object.

**Call relations**: Pydantic runs this before MemoryStore.commit receives the write. This protects later retirement, permission, and freshness checks, all of which need the full page origin.


##### `_fuse`  (lines 402–425)

```
def _fuse(legs: tuple[tuple[Hit, ...], ...], cosine_leg: tuple[Hit, ...]) -> dict[str, tuple[float, float, str]]
```

**Purpose**: Combines multiple search result lists into one score per owning row. It uses reciprocal-rank fusion, a method that rewards results that rank well across different searches.

**Data flow**: It receives several legs of Hit objects plus the vector leg. It ranks chunks inside each leg, sums rank-based credit per chunk, keeps the best chunk per owner, records the owner’s best cosine similarity from the vector leg, and returns a dictionary of owner ID to score details.

**Call relations**: fuse_hits and fuse_recall both call this shared combiner. It is the common ranking base for source-page search and memory recall.

*Call graph*: called by 2 (fuse_hits, fuse_recall); 1 external calls (from_iterable).


##### `fuse_hits`  (lines 428–441)

```
def fuse_hits(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks source-page search hits by combining word-based and meaning-based search results. It returns the best owners and snippets up to the requested limit.

**Data flow**: It receives lexical hits, vector hits, and a limit. It fuses the two hit lists, applies a similarity floor when there were no word matches, sorts by fused score, and returns Fused results.

**Call relations**: MemoryStore.search_sources calls this after asking the index for page hits. It hands back page IDs and snippets that search_sources later verifies against the page mirror and permissions.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (search_sources); 1 external calls (__init__).


##### `fuse_recall`  (lines 444–472)

```
def fuse_recall(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], tail: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks memory recall candidates by blending rank-fusion with raw meaning similarity. It also includes a tail of very new, not-yet-indexed memories.

**Data flow**: It receives lexical hits, vector hits, tail hits, and a limit. It fuses all legs, normalizes the rank score, blends it with cosine similarity, applies a floor only when there were no word matches, sorts, and returns Fused results.

**Call relations**: MemoryStore.recall calls this before reading full memory rows. It gives recall a fair candidate order that can include both indexed memories and just-committed rows.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (recall); 1 external calls (__init__).


##### `half_life_days`  (lines 490–496)

```
def half_life_days(item_class: str, memory_kind: str) -> float | None
```

**Purpose**: Chooses how quickly a fact should fade in recall ranking. Non-fact memory classes do not decay.

**Data flow**: It receives an item class and memory kind. If the item is not a fact, it returns None; otherwise it returns the configured half-life for that kind, falling back to the fact default.

**Call relations**: decay_multiplier uses it for ranking, and inventory uses it for reporting. This keeps the visible inventory and actual recall behavior aligned.

*Call graph*: called by 2 (decay_multiplier, inventory).


##### `decay_multiplier`  (lines 499–511)

```
def decay_multiplier(item_class: str, memory_kind: str, confidence: int, as_of: datetime | None, now: datetime) -> float
```

**Purpose**: Computes how much age and confidence should reduce a memory’s recall score. Recent, high-confidence facts keep more of their score; old or low-confidence facts keep less.

**Data flow**: It receives item class, kind, confidence, an as-of timestamp, and the current time. It finds the half-life, computes age in days, applies confidence and exponential decay for facts, and returns 1.0 for items that do not decay.

**Call relations**: decay_factor wraps this for Recalled items, and inventory calls it directly for display. It is the single place where recall decay math lives.

*Call graph*: calls 2 internal fn (_aware, half_life_days); called by 2 (decay_factor, inventory).


##### `decay_factor`  (lines 514–517)

```
def decay_factor(item: Recalled, now: datetime) -> float
```

**Purpose**: Gets the decay multiplier for a recalled memory item. It adapts a Recalled object to the lower-level decay function.

**Data flow**: It receives a Recalled item and current time, chooses the item’s as_of time or created_at time, passes those fields to decay_multiplier, and returns the multiplier.

**Call relations**: MemoryStore._shortlist calls this while reranking the candidate pool. It lets recall apply the same decay rule to every candidate before deduplication and diversity limits.

*Call graph*: calls 1 internal fn (decay_multiplier); called by 1 (_shortlist).


##### `_body_shingles`  (lines 525–527)

```
def _body_shingles(body: str) -> frozenset[str]
```

**Purpose**: Turns a body of text into small three-word fingerprints for duplicate detection. This is a cheap way to compare whether two memories say nearly the same thing.

**Data flow**: It receives text, lowercases it, splits it into words, builds overlapping three-word phrases, and returns them as a frozen set.

**Call relations**: drop_near_duplicates calls this for each candidate body. The shingles are then compared with Jaccard overlap, a measure of how much two sets share.

*Call graph*: called by 1 (drop_near_duplicates); 1 external calls (split).


##### `drop_near_duplicates`  (lines 530–556)

```
def drop_near_duplicates(items: tuple[Recalled, ...], keep: int) -> tuple[Recalled, ...]
```

**Purpose**: Removes recall candidates whose bodies are very similar to earlier, higher-ranked candidates. This keeps limited recall slots from repeating the same fact.

**Data flow**: It receives ranked Recalled items and a keep count. It walks in rank order, builds word-shingle sets for each body prefix, skips items whose overlap with a kept item is too high, and returns the kept tuple.

**Call relations**: MemoryStore._shortlist calls this after applying decay. It is a runtime guard against duplicate facts that have not yet been cleaned up by deeper deduplication jobs.

*Call graph*: calls 1 internal fn (_body_shingles); called by 1 (_shortlist).


##### `enforce_type_diversity`  (lines 559–577)

```
def enforce_type_diversity(rows: tuple[Recalled, ...], limit: int) -> tuple[Recalled, ...]
```

**Purpose**: Prevents one class of memory from filling all recall slots. It first caps each class, then backfills if there is still room.

**Data flow**: It receives ranked Recalled rows and a limit. It admits rows while each class is under its cap, holds overflow aside, fills remaining slots from overflow if needed, and returns at most the limit.

**Call relations**: MemoryStore._shortlist calls this after near-duplicate removal. It is the final shaping step before recall results are returned.

*Call graph*: called by 1 (_shortlist).


##### `as_topic_pointer`  (lines 580–590)

```
def as_topic_pointer(item: Recalled, index: int) -> Recalled
```

**Purpose**: Converts episodic memory hits into topic pointers instead of injecting their full text. Episodic memories are treated as breadcrumbs to browse, not direct context.

**Data flow**: It receives a Recalled item and its index in the final list. Non-episodic items are returned unchanged; episodic items are copied with a short pointer body and recall_mode set to topic.

**Call relations**: MemoryStore.recall applies this to the final shortlist. It changes how episodic results appear to the caller after all ranking and filtering is done.

*Call graph*: called by 1 (recall); 1 external calls (replace).


##### `MemoryStore.commit`  (lines 617–743)

```
async def commit(self, write: MemoryWrite) -> UUID
```

**Purpose**: Stores one memory item and records its source link if it came from a page. It does not index the body immediately; it marks the row as due for the background indexer.

**Data flow**: It receives a MemoryWrite, builds a stable UUID from workspace, subject, class, and body, then inserts or updates the memory row. If the page binding changed, it clears the embedding digest and claim so indexing will re-check it; if a source ID is present, it upserts the memory_source link. It returns the memory ID.

**Call relations**: Callers that derive or write memories use this as the durable write path. MemoryIndexer later sees rows with no embedding digest and publishes or withholds their chunks; supersede_page_facts uses the returned IDs to know which page-derived facts should remain.

*Call graph*: 3 external calls (case, or_, uuid5).


##### `MemoryStore.supersede_page_facts`  (lines 745–866)

```
async def supersede_page_facts(self, page_id: UUID, kept: frozenset[UUID] | None) -> None
```

**Purpose**: Removes stale links between a page and memories it no longer supports. It deletes a memory row only when no source-page links remain.

**Data flow**: It receives a page ID and either a set of kept memory IDs or None for a gone page. It deletes stale memory_source links, locks affected memory rows, repoints rows to surviving links when possible, clears indexing state for rebound rows, deletes rows with no links, and removes their index chunks.

**Call relations**: The page fact derivation flow calls this after committing the facts a page still supports. It works with MemoryStore.commit’s source links and MemoryIndexer’s digest flags so stale page-derived memories stop being searchable without harming the same fact learned from another page.

*Call graph*: 4 external calls (__init__, delete, select, update).


##### `MemoryStore.recall`  (lines 868–907)

```
async def recall(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None, *, source_reader: SourceReader) -> tuple[Recalled, ...]
```

**Purpose**: Answers a memory search query for a reader. It combines indexed search, not-yet-indexed tail search, permission checks, freshness checks, decay, deduplication, and diversity shaping.

**Data flow**: It receives a query, allowed subjects, a limit, optional time window, and source reader. It gets readable source IDs, asks _legs for lexical and vector hits, asks _untail_leg for new unindexed matches, fuses them, enriches them with database rows and permission checks, shortlists them in a worker thread, converts episodic items to topic pointers, and returns Recalled items.

**Call relations**: This is the main read path for memory recall. It coordinates _source_ids, _legs, _untail_leg, fuse_recall, _enrich, _shortlist, and as_topic_pointer in that order.

*Call graph*: calls 6 internal fn (_enrich, _legs, _source_ids, _untail_leg, as_topic_pointer, fuse_recall); 2 external calls (to_thread, now).


##### `MemoryStore._shortlist`  (lines 909–924)

```
def _shortlist(self, enriched: tuple[Recalled, ...], limit: int, now: datetime) -> tuple[Recalled, ...]
```

**Purpose**: Turns a larger recall candidate pool into the final number of slots requested. It applies time decay, duplicate removal, and type diversity.

**Data flow**: It receives enriched Recalled items, a limit, and the current time. It copies each item with a decay-adjusted score, sorts by that score, drops near duplicates, enforces type diversity, and returns the final tuple.

**Call relations**: MemoryStore.recall runs this in a worker thread because it is CPU work with no awaits. It calls decay_factor, drop_near_duplicates, and enforce_type_diversity.

*Call graph*: calls 3 internal fn (decay_factor, drop_near_duplicates, enforce_type_diversity); 1 external calls (replace).


##### `MemoryStore.search_sources`  (lines 926–992)

```
async def search_sources(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None, *, source_reader: SourceReader) -> tuple[SourceMatch, ...]
```

**Purpose**: Searches synced source pages rather than stored memory facts. It returns matching page snippets only if the page is still current and readable.

**Data flow**: It receives a query, subjects, limit, optional time window, and source reader. It gets lexical and vector page hits, fuses them, reads matching mem_page mirror rows, asks for readable current page states, filters out stale or unauthorized pages, and returns SourceMatch objects.

**Call relations**: This is the source-page counterpart to recall. It calls _legs for index hits, fuse_hits for ranking, and _readable_states for permission-aware freshness checks.

*Call graph*: calls 4 internal fn (_legs, _readable_states, _aware, fuse_hits); 3 external calls (__init__, select, UUID).


##### `MemoryStore._source_ids`  (lines 994–1000)

```
async def _source_ids(self, source_reader: SourceReader) -> frozenset[UUID]
```

**Purpose**: Gets the set of source IDs the current reader may use for source-derived memory. If no grant authority was wired in, it fails loudly.

**Data flow**: It receives a SourceReader. It checks that readable_source_ids exists, calls it, and returns the resulting frozen set of UUIDs.

**Call relations**: MemoryStore.recall calls this before tail search and row enrichment. The returned source IDs feed _granted_link-based filters.

*Call graph*: called by 1 (recall).


##### `MemoryStore._legs`  (lines 1002–1014)

```
async def _legs(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[tuple[Hit, ...], tuple[Hit, ...]]
```

**Purpose**: Runs the two main index searches for a query: word-based search and meaning-based vector search. Vector search is skipped if the query cannot be embedded.

**Data flow**: It receives query text, subjects, owner kind, and limit. It embeds the query, asks the index for lexical hits, asks the index for vector hits when an embedding exists, and returns both hit tuples.

**Call relations**: MemoryStore.recall uses it for memory items, and MemoryStore.search_sources uses it for pages. It delegates embedding failure handling to _embed_query.

*Call graph*: calls 1 internal fn (_embed_query); called by 2 (recall, search_sources).


##### `MemoryStore._untail_leg`  (lines 1016–1072)

```
async def _untail_leg(self, query: str, subjects: frozenset[str], limit: int, source_ids: frozenset[UUID]) -> tuple[Hit, ...]
```

**Purpose**: Searches the newest unindexed memory rows with a simple word-count score. This lets a newly committed fact be recalled before the background indexer catches up.

**Data flow**: It receives query, subjects, limit, and readable source IDs. It splits the query into terms, reads recent memory rows with no embedding digest that pass subject, retirement, supersession, and source-grant filters, scores each by term occurrences in the body, and returns sorted Hit objects.

**Call relations**: MemoryStore.recall calls this as a third search leg before fuse_recall. It uses _granted_link for source permissions and stops once rows are indexed because indexed rows no longer have a null embedding digest.

*Call graph*: calls 1 internal fn (_granted_link); called by 1 (recall); 4 external calls (__init__, split, or_, select).


##### `MemoryStore._embed_query`  (lines 1074–1082)

```
async def _embed_query(self, query: str) -> tuple[float, ...]
```

**Purpose**: Turns a query into an embedding vector for meaning-based search. If the query is blank or embedding fails, it returns no vector instead of breaking recall.

**Data flow**: It receives query text. Blank text returns an empty tuple; otherwise it asks the embed backend for a vector, logs and returns empty on failure, and returns the first vector when available.

**Call relations**: MemoryStore._legs calls this before vector search. By swallowing embedding failures with a warning, lexical recall and source search can still work.

*Call graph*: called by 1 (_legs).


##### `MemoryStore._enrich`  (lines 1084–1164)

```
async def _enrich(self, fused: tuple[Fused, ...], subjects: frozenset[str], source_ids: frozenset[UUID], start: datetime | None, end: datetime | None) -> tuple[Recalled, ...]
```

**Purpose**: Reads full memory rows for fused candidate IDs and applies the final safety filters. It removes superseded, retired, unauthorized, out-of-window, or stale page-derived memories.

**Data flow**: It receives fused hits, subjects, readable source IDs, and optional time bounds. It queries matching memory rows with workspace, subject, lifecycle, and grant conditions, reads current page states for page-derived rows, preserves fused order, and returns Recalled objects for rows still valid.

**Call relations**: MemoryStore.recall calls this after fuse_recall. It uses _granted_link and _aware, and it calls page_states so index candidates cannot bypass database and page freshness checks.

*Call graph*: calls 2 internal fn (_aware, _granted_link); called by 1 (recall); 4 external calls (__init__, or_, select, UUID).


##### `MemoryStore._readable_states`  (lines 1166–1173)

```
async def _readable_states(self, page_ids: tuple[UUID, ...], source_reader: SourceReader) -> dict[UUID, PageState]
```

**Purpose**: Gets current page states for pages the reader is allowed to read. It is the permission-aware page-state lookup used by source search.

**Data flow**: It receives page IDs and a SourceReader. Empty input returns an empty dictionary; otherwise it requires readable_page_states to be wired and calls it.

**Call relations**: MemoryStore.search_sources calls this after reading mem_page rows. It confirms that returned snippets correspond to pages still readable and still at the mirrored revision.

*Call graph*: called by 1 (search_sources).


##### `store_for`  (lines 1176–1189)

```
def store_for(ext: ExtensionContext) -> MemoryStore
```

**Purpose**: Builds a MemoryStore from an extension context. It checks that required index and embedding backends are available.

**Data flow**: It receives an ExtensionContext. If index or embed is missing, it raises an error; otherwise it copies the context’s backends, transaction opener, workspace ID, and page/source lookup functions into a MemoryStore.

**Call relations**: Startup or extension wiring code uses this to get the memory workflow object. It is the bridge from the broader extension context into this file’s store methods.

*Call graph*: 1 external calls (__init__).


##### `MemoryIndexer.run`  (lines 1213–1215)

```
async def run(self) -> None
```

**Purpose**: Runs one indexing pass for due memory items. It claims a batch and processes each claimed item.

**Data flow**: It calls _claim_due to get MemoryItem rows needing indexing, then passes each item to _index_item. It returns nothing but may update the index and database.

**Call relations**: A background job calls this periodically. It is the top-level driver for memory-item chunking, embedding, withholding, and settlement.

*Call graph*: calls 2 internal fn (_claim_due, _index_item).


##### `MemoryIndexer._claim_due`  (lines 1217–1253)

```
async def _claim_due(self) -> tuple[MemoryItem, ...]
```

**Purpose**: Atomically claims memory rows that need indexing. The claim prevents overlapping indexer runs from doing the same embedding work.

**Data flow**: It computes a lease cutoff time, selects rows with no embedding digest and no active claim, optionally uses database row locking, stamps embedding_claimed_at for selected rows, and returns them as MemoryItem objects.

**Call relations**: MemoryIndexer.run calls this at the start of a pass. _index_item later settles each claimed row or leaves it for a future pass if its binding changes.

*Call graph*: called by 1 (run); 5 external calls (now, timedelta, or_, select, update).


##### `MemoryIndexer._index_item`  (lines 1255–1292)

```
async def _index_item(self, item: MemoryItem) -> None
```

**Purpose**: Decides whether one claimed memory should be published to the index, withdrawn, or left for a later run. It is careful not to publish stale page-derived bodies.

**Data flow**: It receives a MemoryItem. If retired or not publishable, it deletes its index chunks and settles the row. Otherwise it checks whether chunks already exist, chunks and embeds the body if needed, re-reads the row binding, verifies publishability again, and settles only if the binding is still the same.

**Call relations**: MemoryIndexer.run calls this for each claimed row. It calls _publishable before and after indexing, uses chunk_embed_upsert for real indexing work, and calls _settle to mark a final decision.

*Call graph*: calls 2 internal fn (_publishable, _settle); called by 1 (run); 3 external calls (__init__, select, chunk_embed_upsert).


##### `MemoryIndexer._publishable`  (lines 1294–1303)

```
async def _publishable(self, subject: str, page_id: UUID | None, revision: int | None) -> bool
```

**Purpose**: Checks whether a memory body is allowed to appear in the index. Direct memories are always publishable; page-derived memories must still match the current page subject and revision.

**Data flow**: It receives subject, page ID, and revision. If there is no page ID, it returns true; otherwise it reads current page state and returns true only when subject and revision match.

**Call relations**: MemoryIndexer._index_item calls this before and after chunking. This prevents stale page-derived rows from taking up index candidate slots.

*Call graph*: called by 1 (_index_item).


##### `MemoryIndexer._settle`  (lines 1305–1329)

```
async def _settle(self, item: MemoryItem) -> None
```

**Purpose**: Marks a claimed memory row as decided by writing a digest of its body and clearing the claim. Settled rows leave the due-for-indexing queue.

**Data flow**: It receives the MemoryItem that was claimed. It computes a SHA-256 digest of the body and updates the row only if the row still has the same subject, body, page binding, source, and an active claim.

**Call relations**: MemoryIndexer._index_item calls this after publishing or deliberately withholding a row. The guarded update prevents an old indexing decision from overwriting a newer page rebinding.

*Call graph*: called by 1 (_index_item); 2 external calls (sha256, update).


##### `PageIndexer.apply`  (lines 1352–1354)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Applies a batch of source-page changes to the page search index and page mirror. It processes each change one at a time.

**Data flow**: It receives a tuple of PageChange objects and calls _apply for each. It returns nothing but may update page chunks, mem_page rows, and memory indexing state.

**Call relations**: The broader page-change runner calls this with delivered changes. PageIndexer._apply contains the per-page safety checks and write logic.

*Call graph*: calls 1 internal fn (_apply).


##### `PageIndexer._apply`  (lines 1356–1414)

```
async def _apply(self, change: PageChange) -> None
```

**Purpose**: Applies one source-page change safely. It indexes live page bodies, removes tombstoned or stale pages, and updates the mem_page mirror.

**Data flow**: It receives a PageChange, reads current page state, marks facts from left-behind revisions as due for re-checking, then handles tombstones, stale changes, or live changes. For live changes it chunks and embeds the page body, verifies the page did not change during embedding, and upserts the mem_page row.

**Call relations**: PageIndexer.apply calls this for every change. It calls _unsettle_left_behind_facts first, uses chunk_embed_upsert for page chunks, and deletes index/mirror data when the change is no longer current.

*Call graph*: calls 1 internal fn (_unsettle_left_behind_facts); called by 1 (apply); 5 external calls (__init__, delete, insert, update, chunk_embed_upsert).


##### `PageIndexer._unsettle_left_behind_facts`  (lines 1416–1444)

```
async def _unsettle_left_behind_facts(self, page_id: UUID, state: PageState | None) -> None
```

**Purpose**: Marks memories derived from old page revisions as needing the memory indexer to re-check them. This causes stale chunks to be withdrawn even if no replacement fact is derived.

**Data flow**: It receives a page ID and the page’s current state, if any. It builds a database condition for memory rows from that page that no longer match the live subject and revision, or all such rows if the page is gone, then clears their embedding digest and claim.

**Call relations**: PageIndexer._apply calls this before handling each page change. MemoryIndexer later sees those rows as due and deletes or withholds chunks that should no longer be searchable.

*Call graph*: called by 1 (_apply); 2 external calls (or_, update).


### Research source recall
Research observations preserve web sources from conversations and expose safe, trimmed source records for later display.

### `extensions/research/ufo_ext_research/observations.py`

`io_transport` · `request handling and conversation display`

When the research extension searches the web or fetches a page, the system needs a durable record of what sources were used. Without this file, useful citations could disappear after the immediate task finishes, and the conversation would lose the trail of evidence behind an answer.

The file defines a database table for source observations. Each saved source belongs to a workspace and a conversation, and is keyed by a digest of its URL. A digest is a fixed-length fingerprint made from the URL, like writing a short label on a long address so it can be compared reliably. The stored record includes the URL, title, snippet, optional publication date, source rank, and timestamps.

Before saving, the code trims titles, snippets, and dates to reasonable lengths, then checks that each item fits the expected conversation-source shape. Invalid sources are skipped instead of breaking the whole save. Saving uses an “upsert”: if the same URL has already been seen for that conversation, the old row is updated rather than duplicated.

The file also limits each conversation to the most recent 100 sources. Finally, it defines a conversation slot provider called `SOURCES_SLOT`, which lets the rest of the app ask, “How many sources are there?” and “Please read the source list.”

#### Function details

##### `_bounded`  (lines 53–54)

```
def _bounded(value: str, limit: int) -> str
```

**Purpose**: This small helper cuts a string down to a maximum length. It is used to keep saved source fields from becoming too large for display or storage expectations.

**Data flow**: It receives a piece of text and a character limit. It returns the same text if it is already short enough, or the beginning of the text up to that limit if it is too long. It does not change anything outside itself.

**Call relations**: When `record_sources` prepares a source for storage, it calls `_bounded` on fields such as title, snippet, and publication date before validating and saving them.

*Call graph*: called by 1 (record_sources).


##### `record_sources`  (lines 57–130)

```
async def record_sources(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, sources: tuple[RetrievedSource, ...]) -> None
```

**Purpose**: This is the main saving routine for retrieved sources. It validates source information, writes it into the database, updates existing entries for repeated URLs, and prunes old entries so a conversation does not grow an unlimited source list.

**Data flow**: It receives the extension context, a conversation ID, a turn ID, and a tuple of retrieved sources. If there are no sources, it stops. Otherwise it opens a database transaction, trims and validates each source, creates a URL fingerprint, then inserts or updates one database row per valid source. After saving, it deletes older excess rows so only the newest allowed set remains.

**Call relations**: `record_search_hits` and `record_fetched_page` both convert their own input formats into `RetrievedSource` objects and hand them to this function. Inside the save flow, `record_sources` uses `_bounded` to shorten fields, `ConversationSource` to validate the shape of the data, database insert/delete/select operations to persist it, and the extension context transaction to make the database work happen safely as one unit.

*Call graph*: calls 2 internal fn (transaction, _bounded); called by 2 (record_fetched_page, record_search_hits); 5 external calls (__init__, now, sha256, delete, select).


##### `record_search_hits`  (lines 133–152)

```
async def record_search_hits(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, hits: tuple[SearchHit, ...]) -> None
```

**Purpose**: This function saves sources that came from search results. It is a translator from the search system’s result format into the generic source format used by this file.

**Data flow**: It receives the extension context, conversation ID, turn ID, and search hits. For each hit, it copies the URL, title, text snippet, and publication date into a `RetrievedSource`. It then passes the full converted tuple to `record_sources`, which does the actual validation and database writing.

**Call relations**: This sits between the search feature and the shared storage routine. Search code can call it with `SearchHit` objects, and this function hands normalized source records onward to `record_sources`.

*Call graph*: calls 1 internal fn (record_sources); 1 external calls (__init__).


##### `record_fetched_page`  (lines 155–173)

```
async def record_fetched_page(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, page: FetchedPage) -> None
```

**Purpose**: This function saves a source when the system has fetched a specific web page, not just a search result. It records that page as a conversation source so it can appear later in the source list.

**Data flow**: It receives the extension context, conversation ID, turn ID, and fetched page. It builds one `RetrievedSource` using the page URL as both URL and title, and uses the page summary if available, otherwise the full page text. It then sends that single source to `record_sources` for validation and storage.

**Call relations**: This is the fetched-page counterpart to `record_search_hits`. It adapts a `FetchedPage` into the common `RetrievedSource` shape, then relies on `record_sources` to perform the actual database update.

*Call graph*: calls 1 internal fn (record_sources); 1 external calls (__init__).


##### `_source_count`  (lines 176–186)

```
async def _source_count(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function answers the lightweight question, “How many saved sources does this conversation have?” It is used when the conversation UI or manifest system wants a summary without loading every source.

**Data flow**: It receives a conversation slot context, opens a database transaction, and counts source rows for the current workspace and conversation. If there are no rows, it returns `None`; otherwise it returns the count capped at the source limit.

**Call relations**: `SOURCES_SLOT` uses this function as its summary callback. When the app wants to summarize the Sources slot, this function reads only the count from the database instead of loading the full source payload.

*Call graph*: 1 external calls (select).


##### `_read_sources`  (lines 189–217)

```
async def _read_sources(ctx: ConversationSlotContext) -> SourcesSlotPayload
```

**Purpose**: This function loads the saved source list for a conversation so it can be shown to the user. It returns both the visible sources and a flag saying whether there were more than the display limit.

**Data flow**: It receives a conversation slot context, opens a database transaction, and selects source rows for the current workspace and conversation. Rows are ordered with the newest observations first, then by their saved rank. It converts the rows back into validated `ConversationSource` objects and wraps them in a `SourcesSlotPayload`, marking the result as truncated if more than the source limit was found.

**Call relations**: `SOURCES_SLOT` uses this function as its read callback. When the app needs the actual contents of the Sources slot, this function reads from the database and hands back the structured payload expected by the conversation slot system.

*Call graph*: 3 external calls (__init__, __init__, select).
