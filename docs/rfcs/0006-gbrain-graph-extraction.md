---
rfc: 0006
title: "RFC — gbrain graph extraction from pages"
status: implemented
date: 2026-07-06
---

# RFC — gbrain graph extraction from pages

**Status:** proposal. **Scope:** a source-page → knowledge-graph derivation, delivered as an
extension. **Verdict:** gbrain ships it; metalcraft shipped a reduced heuristic slice; selfhost
ships none. It is buildable today on existing seams as a pure extension — no core change.

| System | Entity nodes | Typed relations | Graph store | Traversal query | Overall |
|---|---|---|---|---|---|
| **gbrain** (target) | yes (auto-stub on ref) | bounded typed vocab, zero-LLM on wikilinks | yes | yes (+ synthesis/gap analysis) | the reference |
| **metalcraft** | never built | 2 heuristic types (`mentions`,`derived_from`), targets are opaque strings | `graph_edge` (edges only) | never built (edges = a ranking boost) | **partial** |
| **selfhost** | absent | absent | absent | absent | **none — RAG only** |

---

## 1. What gbrain graph extraction is (and why)

From `~/src/metalcraft/docs/research/gbrain-gstack.md`:

- **Self-wiring typed-edge graph** (§3, lines 71-81): *"Every `put_page` extracts entity refs from
  markdown/wikilinks/typed-link syntax and writes edges with zero LLM calls. Typed edges
  (`attended`, `works_at`, `invested_in`, `founded`, `advises`, `mentions`…)."* Auto-link fires on
  every write; **a new entity ref auto-creates a node page-stub → the graph grows** (lines 79-81).
  The edge set is a **bounded vocabulary** (line 79).
- **Purpose** (lines 28-34): gbrain is positioned *against* "RAG-in-a-box" — it adds two things
  keyword+grep systems lack: (1) a **synthesis layer** returning a cited answer + gap analysis, and
  (2) the **self-wiring graph** enabling **graph traversal**. Benchmarked lift: *"+31.4 points P@5
  over its graph-disabled variant"* (line 81).
- **git+markdown is the system of record; the DB is a derived index** (lines 55-60);
  **git delete → DB soft-delete** (lines 62-69). Every claim traces to append-only evidence
  (lines 98-99). Cron enrichment dedups/fixes/consolidates overnight (lines 122-132).

The load-bearing properties for us: **deterministic materialization on a write signal, a bounded
typed vocabulary, entity refs resolved to nodes (stub-on-reference), provenance to source, and a
traversable graph** — not merely edges used as a scoring nudge.

## 2. Metalcraft — PARTIAL (edges-only, heuristic, no traversal)

Metalcraft built the graph *substrate* and a *deterministic distiller*, then wired edges only into
retrieval ranking. Evidence (`~/src/metalcraft`):

| Piece | Status | Evidence |
|---|---|---|
| `graph_edge` table (RLS) | built | `src/metalcraft_store/migrations/001_product_tables.sql:294-309` — `edge_type, from_ref, to_ref, confidence(0..1), observed_at`, PK `(namespace,store_uid,edge_type,from_ref,to_ref)` |
| `GraphEdge` + writer | built | `store_work.py:34-51`; `store_writes.py:141-170` `write_graph_edge` (upsert) |
| distiller job (batch, level-triggered) | built | `store_distill.py:121-196` `StoreDistillScan`; CronJob `store-distill-scan` (`jobs.py:109-113`); re-derives only when page digest changed (`:190`) |
| edge extraction | **heuristic stub** | `store_distill.py:66-81` `proposed_edges`: `mentions` per `@`/`#`/URL token (`MENTION_RE`, `:29`), `derived_from` → `source:{root}`. **Only 2 types** (`DEFAULT_EDGE_TYPES`, `:30`); other configured types are silent no-ops; **zero-LLM**; fixed `HEURISTIC_CONFIDENCE = 1` (`:32`) |
| entity nodes | **never built** | no node/entity table; no wikilink/stub logic; `to_ref` is a **raw string** (`@bob`, a URL), never joined to a page (`store_distill.py:74`). "entity" exists only as a fact-page slug string (`memory.py:294,302`) |
| graph traversal | **never built** | edges loaded flat (`store_service.py:496-525`) and used only as a lexical **ranking boost** — a page scores higher when an edge's `type`/`dstPath`/`srcPath` text contains a query term (`fusion.py:213-227`). Never followed to a target. |

So metalcraft realized gbrain's *"deterministic, on a change signal, bounded vocabulary"* shape
(`docs/brain.md:97`) but in a **reduced form**: opaque string targets (no node resolution), a
2-type vocabulary, and edges consumed as an RRF signal rather than a traversable graph. The
`GraphError` readiness reason even lacks a producer (`docs/brain.md:298-301`).

## 3. Selfhost — NONE (page → embeddings only)

Grep for `graph_edge|distill|entity|relation|edge|node|triple|wikilink|traverse` across
`core/` + `extensions/` returns **zero** hits (only "identity" / "Microsoft Graph" false
positives). The page→memory pipeline is pure chunk+embed RAG.

**Tables**

| Table | Owner | Columns | Ref |
|---|---|---|---|
| `page` | core | `id, workspace_id, source_id, digest, body_ref, subject, tombstone, created/updated_at` — thin substrate, body in blob store | `core/src/selfhost/schema/tables.py:277-292` |
| `memory_item` | memory ext | `id, workspace_id, subject, body, item_class, memory_kind, confidence, source_ref, embedding_digest, embedding_claimed_at, superseded_by, …` | `extensions/memory/selfhost_ext_memory/store.py:80-96` |
| `mem_page` | memory ext | `page_id, subject, created_at` — the **only** per-page derived artifact (a mirror for the date-window filter) | `store.py:98-104` |

**Derivation** — two batch jobs, both `chunk → embed → upsert` and nothing else:

- `PageIndexer` (`store.py:600-661`): reads `ctx.pages.pages_changed_since(cursor)` off the core
  `PageFeed`, advances its own cursor in `ScopedStore`, and for each change calls
  `chunk_embed_upsert(...)` + upserts a `mem_page` row. Tombstone → delete chunks + mirror.
- `MemoryIndexer` (`store.py:526-597`): `memory_item` bodies → chunks+embeddings.
- `chunk_embed_upsert` (`core/src/selfhost/indexing.py:89-114`): recursive-delimiter chunk → embed
  → index upsert → prune. **No LLM, no extraction of any kind.**

**Facts are not derived from pages at all** — `memory_item` rows are written only by the
`memory_update` tool the agent calls (`manifest.py:180-196`). (The metalcraft inbox→fact curation
loop that `salvage.md:38` planned as a "Condenser" was **not** ported either — a separate gap.)

**Recall/search** (`store.py:323-393`): lexical + vector RRF fusion, recency decay, diversity cap,
episodic→topic pointer — gbrain's *recall richness* (spec.md:75-85) but no graph. `IndexBackend`
(`indexing.py:67-82`) exposes only `upsert/delete/prune/lexical/vector/reindex` — **no adjacency or
traversal method**.

**Pipeline shape:** `source sync → page row + blob body → PageIndexer → chunks+embeddings + mem_page
→ hybrid similarity recall`. Pure RAG. No entities, no edges, no graph, at any stage.

## 4. The gap

Selfhost inherited metalcraft's *retrieval* (embeddings + fact recall + decay) but **not** its
graph substrate, and neither system built the parts of gbrain that make a graph worth having:

1. **No graph store** in selfhost (metalcraft had `graph_edge`).
2. **No entity resolution** in either — gbrain resolves refs to **nodes** with stub-on-reference;
   metalcraft's edge targets are opaque strings; selfhost has neither.
3. **No typed-relation extraction** — gbrain's bounded vocab (`works_at`, `founded`, …) exists
   nowhere; metalcraft has 2 heuristic types; selfhost has 0.
4. **No traversal / gap-analysis** — the query shape gbrain sells; metalcraft used edges only as a
   ranking nudge; selfhost has no edges to nudge with.

spec.md already names the target as an **acceptance-test extension**: line 317 — *"gbrain-style
memory (source → condense to markdown + graph) | memory, sources, triggers"*. It is declared, not
built.

## 5. Proposed implementation — a `knowledge-graph` extension

### 5.1 Placement (doctrine)

- **Extension, not core.** Core doctrine: if a capability can be an extension, it is not core; and
  spec.md:317 lists it as an example extension. Every need it has is already on `ExtensionContext`
  (§5.6) — **no new core seam, no core change**.
- **Standalone package `selfhost_ext_graph`**, not folded into memory. It owns distinct tables and a
  distinct query shape (traversal), and it must consume the `PageFeed` on its **own cursor**
  independent of `PageIndexer`. It rides *beside* memory over the one page substrate — the clean
  fan-out `pages → {PageIndexer(embeddings), GraphExtractor(entities+edges)}` (spec.md:85). (If a
  recall-time graph *boost* is later wanted — metalcraft's `fusion.py` model — that needs a memory
  ranking-contribution seam and is out of scope here; gbrain's value is **traversal**, a query the
  extension owns directly.)
- **Batch-at-interval, never inline.** Derived state is produced by jobs, never on a write
  (CLAUDE.md hot-paths; "event-fired work must be unable to fire on events it caused"). The job
  polls the `PageFeed` cursor exactly as `PageIndexer` does — it never fires on page writes. This is
  what spec.md's `triggers` ("pages → derive") means operationally; selfhost has **no** trigger
  abstraction (it is `jobs` + `PageFeed`), so we use that concrete seam, not invent one.

### 5.2 Tables (extension-owned migration, all `workspace_id`-scoped, `subject ∈ {shared, member:*}`)

| Table | Key | Columns | Purpose |
|---|---|---|---|
| `graph_entity` | `id` (content-addressed on `(subject, entity_type, normalized_name)`) | `workspace_id, subject, name, entity_type, aliases, digest, is_stub, created/updated_at` | resolved node; **stub-on-reference** (gbrain lines 79-81) — a referenced-but-unseen entity gets `is_stub=true`, filled when a page defines it |
| `graph_edge` | unique `(workspace_id, edge_type, from_entity, to_entity, source_page_id)` | `id, workspace_id, subject, edge_type, from_entity(fk), to_entity(fk), source_page_id(fk page), confidence, observed_at, extracted_digest, superseded_by, created/updated_at` | typed relation **node→node** (fixes metalcraft's opaque-string target); `source_page_id` = provenance/citation (gbrain "claims trace to evidence") |

**Level-trigger:** `graph_edge.extracted_digest` records the `page.digest` the edge was derived
from; a page is re-extracted only when its digest changes → steady state does nothing, upserts are
idempotent (mirrors `memory_item.embedding_digest`, `store.py`, and metalcraft
`derivation_checkpoint`). **Page tombstone → soft-delete** its entities'/edges' rows (gbrain
git-delete→soft-delete, lines 62-69; mirror `PageIndexer._apply` tombstone handling, `store.py:631`).

### 5.3 Producer — the extraction job

A `JobSpec` (`ext/manifest.py:60`) `graph_extract`, cron batch-at-interval (e.g. `"0 */5 * * * *"`),
handler receiving the scoped `ExtensionContext`. Structure mirrors `PageIndexer` (`store.py:600-661`)
— a workflow dataclass, `run()` is the flow, private steps in execution order:

1. `cursor = ctx.store.get(PAGE_CURSOR_KEY)`; `batch = await ctx.pages.pages_changed_since(cursor, LIMIT)`.
2. For each `PageChange`: tombstone → soft-delete rows; else if `page.digest != stored extracted_digest` → **extract** (§5.4).
3. Upsert entities (resolve/dedupe first) then edges in one `ctx.transaction()`; stamp `extracted_digest = page.digest`.
4. Advance the cursor in `ctx.store`.

**Bound every external payload next to the call** (CLAUDE.md): cap pages/batch (`LIMIT`), cap
body chars per page, cap model output tokens.

### 5.4 Extraction contract (deterministic backbone + LLM tier)

| Tier | Mechanism | Cost | Output |
|---|---|---|---|
| **A — deterministic** (always) | pattern-match `@`/`#`/URL mentions + `derived_from source` (metalcraft `MENTION_RE`, `store_distill.py:29-81`) | zero-LLM | `mentions`/`derived_from` edges to normalized string entities — the cheap backbone / fallback |
| **B — typed** (default on) | one bounded LLM pass per page via `ctx.invoker` / a `ModelClient` | 1 model call/changed page, batched | `(entity, entity_type)` + `(from, edge_type, to)` tuples, resolved to nodes |

- **Bounded edge vocabulary — gated, fail-loud.** `EdgeType = Literal["mentions","derived_from",
  "works_at","founded","invested_in","advises","attended","reports_to"]` (adopt gbrain's set,
  lines 74). Extraction output is validated against it; an unknown type is **rejected**, not a silent
  no-op (fixes metalcraft, `store_distill.py:62-63`). Same closed enum for `entity_type`
  (`person, company, project, topic, …`).
- **Entity resolution** (fixes both prior systems): normalize name → match existing node by
  `(subject, entity_type, normalized_name)` + alias table; miss → create (stub if only referenced).
  Phase-2 nicety: embed entity names via `ctx.embed`+`ctx.index` (owner-kind `entity`) for
  fuzzy-dedup — reuses the existing index seam, no new backend.
- **Provenance:** every Tier-B edge carries `source_page_id` and `confidence` from the model
  (bounded 0..1); Tier-A edges carry fixed confidence.

### 5.5 Consumer — query surface (ships in the same unit; both-ends)

- **Tool `graph_search`** (tools manifest point): entity lookup + **bounded k-hop neighbor
  expansion** over `graph_edge` via an iterative/recursive-CTE SQL query with a **hop cap and result
  cap** (bound the payload). This is the traversal gbrain has and metalcraft lacked — kept in SQL,
  **no graph-DB backend seam** (doctrine: generic only where a real matrix demands it; add a
  `graph_backends` seam only when a second backend exists, not before). Returns nodes+edges with
  `source_page_id` citations.
- **`on_inbound` hook** (hooks manifest point, spec.md:132) optionally injects the query-relevant
  subgraph into the turn — mirroring memory's `recall_hook` (`manifest.py:199-214`) — so graph
  context reaches the agent automatically, the memory extension's recall path untouched.

**Both-ends discipline:** the migration adds only what this unit wires; the extract job (producer)
and `graph_search`/hook (consumer) land together; the sample-extension-style proof exercises both —
realistic source pages → extracted nodes+edges (durable) → `graph_search` returns a cited
multi-hop answer, and an unknown edge type raises.

### 5.6 Seams touched — all existing (no core change)

| Need | Existing seam | Ref |
|---|---|---|
| Replay changed source pages on own cursor | `ctx.pages: PageFeed.pages_changed_since` | `sources/sync.py:486-544`; `context.py:207` |
| Durable extraction cursor | `ctx.store: ScopedStore` (`get`/`put`) | `context.py:203` |
| Own graph tables | `ctx.transaction()` + extension migration | `context.py:213-222` |
| LLM extraction pass | `ctx.invoker` (or a `ModelClient`) | `context.py:210, 224-231` |
| Batch scheduling | `Manifest.jobs = [JobSpec(name, schedule, handler)]` → DBOS cron | `ext/manifest.py:60`; `jobs.py:275-295` |
| Entity-embedding dedup (phase 2) | `ctx.index` / `ctx.embed` | `context.py:205-206` |
| Query tool + auto-inject | `tools` + `hooks` (`on_inbound`) manifest points | `spec.md:125,132` |

Conclusion: the whole feature is a manifest + a migration + one job + one tool + one hook. It
proves the extension seam (spec.md:317) rather than extending core.

### 5.7 Phasing

| Phase | Delivers |
|---|---|
| **1** | tables + `graph_extract` job (Tier A + Tier B) + `graph_search` tool + tombstone soft-delete + level-trigger. End-to-end proof. |
| **2** | `on_inbound` subgraph injection; embedding-based entity dedup over `ctx.index`. |
| **3** | cron enrichment job (entity merge, alias consolidation, stale-edge supersession) — gbrain's overnight loop (research lines 122-132), batch-at-interval. |

## 6. Open questions

1. **Subject scoping of the graph** — one shared workspace graph vs per-member subgraphs (memory
   uses `subject ∈ {shared, member:*}`). Default: mirror memory's subject model on both tables.
2. **Extraction model & cost cap** — a cheap dedicated model vs the agent's model via `ctx.invoker`;
   needs a per-workspace spend guard (ledger already meters model calls).
3. **Recall integration** — do we ever want graph edges to boost memory recall (metalcraft
   `fusion.py:213-227`)? That requires a memory ranking-contribution seam; deferred — traversal via
   `graph_search` covers the primary use.
4. **Conflict/versioning of edges** — adopt gbrain's append+supersede (`superseded_by`) vs
   overwrite; the column is provisioned for the append model but phase-1 can overwrite by digest.
