---
rfc: 0046
title: "Tenant-scale schema — surrogate keys over workspace-leading partitions"
status: proposed
date: 2026-09-09
---

# Tenant-scale schema — surrogate keys over workspace-leading partitions

> Two changes to the page/memory model so it holds thousands of tenants: primary keys stop being
> content, and every key leads with `workspace_id` over a hash partition. Sequenced so no step takes
> the site down and no table is rebuilt twice. Two further simplifications were proposed and
> withdrawn — both tables earn their rows. Extends `spec.md` §Workspace model.

## Current state, measured on production 2026-09-09

| table | rows | on disk | primary key |
|---|---|---|---|
| `memory_source` | 692,507 | 210 MB | `(memory_item_id, page_id)` |
| `memory_item` | 635,465 | 651 MB | `id` = uuid5 over workspace + subject + class + body |
| `page` | 510,538 | 991 MB | `id` = uuid5 over **`source_id`** + `source_ref` |
| `mem_page` | 487,712 | 82 MB | `page_id` |
| `source` | 230 | — | `id` = uuid5 over workspace + backend + feed identity + **`connection_id`** |

23 tenants: ~22k pages and ~28k memories each. Nothing is partitioned. No primary key leads with
`workspace_id`. At 5,000 tenants the same shape is ~500M rows and ~400 GB.

**Keys that embed a parent's id are the load-bearing defect.** `source.id` embeds `connection_id`
and `page.id` embeds `source_id`, so re-addressing a connection rewrites every source under it, every
page under those, and every row citing them — which is why `20260907150257` re-keyed in Python with
the fleet stopped and cleared `claimed_by`. `memory_item.id` is content-addressed too, but over its
own body: no update ever sets `body=` (rows are superseded, never edited) and no parent id is in the
hash, so it cannot cascade. That one is dedup by design and stays.

**Neither `source` nor `page` persists its natural key.** `page.source_ref` and the source's feed
identity exist only inside the uuid5; `page` has no unique constraint on anything but `id`. Sync
matches an existing page by `source_identity` (stored coalesced from the connector's identity or its
ref — 272 legacy rows of 510,593 are NULL and self-heal on their next sync) and falls back to the
computed id. So the surrogate key's prerequisite is a stored natural key with a unique index, and
that step is additive.

Content addressing also scatters inserts across the whole B-tree. At 460M rows that is page
splitting and WAL amplification on every write, for an identity nobody dereferences by content.

## The four changes

1. **Surrogate keys on `source` and `page`.** UUIDv7 `uid`; the natural key becomes a stored
   `UNIQUE` — `(workspace_id, connection_id, backend, feed_identity)` on `source`,
   `(workspace_id, source_id, source_identity)` on `page`, already unique where non-null.
   Re-addressing becomes an `UPDATE` of one column. Time-ordered ids insert at the right edge.
   What a rekey must repoint, measured: `memory_item.source_id` and `created_from_page_id`,
   `memory_source.source_id` and `page_id`, `mem_page.page_id`, and — outside Postgres — the
   `owner_id` attribute on every page-owned chunk in Turbopuffer, where it is a filter and delete
   key. That last one is a corpus-wide attribute rewrite and needs counting before unit A.
2. **`workspace_id` leads every key, hash-partitioned on it.** `PRIMARY KEY (workspace_id, uid)`,
   `PARTITION BY HASH (workspace_id)`, the shard key on every table so joins stay in-partition.
   Buys per-partition vacuum, index builds and backfills. Offboarding a tenant is a bounded delete
   inside one partition, not a `DETACH` — hash partitions hold many tenants each.
Two further changes were proposed here and **withdrawn on inspection**. Both were argued from row
counts and table names; reading the code that uses them showed each table carries an invariant the
simpler shape cannot express.

3. ~~`mem_page` collapses to a `page.derived_revision` watermark.~~ **Withdrawn.** `mem_page.subject`
   is not a copy of `page.subject`; it is the subject *as of the revision the pass indexed*.
   `condenser.py:255` joins on `mem_page.subject == memory_item.subject` and
   `mem_page.revision == memory_item.created_from_page_revision` to ask whether a memory's binding
   is still live. `page.subject` is the current value and moves; the mirror's must not. A watermark
   column loses the snapshot and breaks that check.
4. ~~`memory_source` collapses to `memory_item.source_ids UUID[]`.~~ **Withdrawn.** The table is the
   retraction ledger, not a link cache. Per `supersede_page_facts`, it is keyed one row per page so
   that retiring a page drops only that page's contribution, the memory survives while any other
   page links it, and the row is deleted "only when its last link is gone — this is the only path
   that removes a page-derived memory". It also re-points a dropped primary binding to a surviving
   link and fences recall by that link's revision. An array can express none of it. The 2.4%
   multi-source figure was the wrong measure: every memory needs its per-page edge for invalidation
   to work at all, which is why there are 1.09 links per memory rather than a sparse side table.

## The split

Two rules set the order.

**One rebuild, not two.** Changing a primary key is a table rebuild. Partitioning is a table
rebuild; Postgres has no `ALTER TABLE … PARTITION BY`. Landing them separately rebuilds every table
twice. They go in one shadow-table cutover.

**Sooner is cheaper, and the gradient is steep.** Every unit costs in proportion to the rows it
moves, and the rows arrive with the tenants. This is the cheapest week to do it that there will
ever be.

| unit | what | rows touched today | downtime | status |
|---|---|---|---|---|
| **0** | Persist `source`'s natural key; `page` already holds one | 317 | none | merged #3245 → `4a2f545fb`; on testing, migrate Job 24s |
| **A** | Surrogate `uid` on `source` and `page`; nullable `uid` twins on every citer, dual-written | 2.33M | none | merged #3254 → `08f8defa5`; on testing, migrate Job 48s, twins 0 null / 0 wrong |
| **B** | Reads and the Turbopuffer `owner_id` attribute move onto `uid`; twins become required | 1.9M (no corpus rewrite — the `owner_id` patch is per page) | none | merged #3310 → `70c0f7806` |
| **C** | Shadow partitioned tables with the final shape, cut over | 2.33M | none | two revisions: shadows + mirrors merged #3311 → `35d7bc3d1`, the swap merged #3314 → `ca74e0619`; on testing, migrate Jobs 43 s and 22 s |
| **D** | Content ids stop being written, then go | — | none | two revisions: unwritten PR #3321, dropped PR #3339 stacked on it |

Unit 0 is small and is the whole prerequisite: without a stored natural key there is nothing for a
surrogate to be unique against, and sync keeps relying on the computed id. `page` needed nothing —
`page_source_identity` is already unique on `(source_id, source_identity)` — so the unit is `source`
alone: 317 rows on production, every one reproducing its id from its stored config.

### Do it now because the tables are small

The whole of it backfills 2.33M rows across 1.9 GB. That fits the migrate Job as it now stands, and a batched job is an optimization rather than a prerequisite.

That is the argument for doing this immediately rather than when it hurts. The same programme
against 460M rows is a year: unit C alone would be a multi-day logical-replication cutover per
table instead of a backfill that finishes inside one deploy. The window where this is cheap is the
one we are in, and it closes as tenants arrive.

**Resumable batched backfills are still owed** — the 2026-09-08 outage asked for them, and unit C
wants them if the tenant count moves before it lands. They are simply not the gate they would be
at scale, and building them first would delay the units that are cheap today.

### Unit A — the surrogate key, additively (as built, #3254)

`source` and `page` each gain `uid`: minted UUIDv7 by the writers (`ufo.schema.ids.uuid7`, since
Python 3.12 ships none — a 12-bit in-millisecond sequence keeps back-to-back ids ordered), and for
existing rows one `UPDATE … SET uid = gen_random_uuid()` — the backfill needs distinct values, not
order. `NOT NULL` lands through a validated `CHECK`, so the scan runs under `SHARE UPDATE EXCLUSIVE`
rather than the exclusive lock. `UNIQUE (workspace_id, uid)` is built **in-transaction, not
`CONCURRENTLY`**: `CREATE INDEX CONCURRENTLY` waits for every open transaction in the database to
end, and one idle session holds it indefinitely; on 317 and 510k rows a plain build is milliseconds
to seconds under a lock that blocks writes only. (An earlier draft said CONCURRENTLY, and I briefly
blamed it for a 36-minute testing deploy of unit 0 — the apply log put that Job at 24 seconds. The
choice stands on the wait semantics, not on that scare.) The content hash stays `UNIQUE`, so dedup
is unchanged.

Every citer — `memory_item.created_from_page_id`/`source_id`, `memory_source.page_id`/`source_id`,
`mem_page.page_id` — gains a `_uid` twin, backfilled by join on the id it already names. **The twins
stay nullable through this unit.** It is the expand phase: a writer that does not yet carry one may
still land its row, and unit B, which moves the joins onto the twins, is where they become
required. (The first cut made them NOT NULL and had `commit` read page state to fill them; that
added a query to every memory write and shifted a call-counting test fake.)

**The seam is `PageState`.** The memory extension may import only `ufo.sdk`, so it cannot read
`tables.page.c.uid`. `PageState` — which the store already reads for every page it mirrors — carries
`uid` and `source_uid`, filled by core from one `page ⨝ source`. The deriver builds each
`MemoryWrite` while holding that state and puts the twins on the write; the survivor re-point in
`supersede_page_facts` copies them from the link row. `commit` reads nothing new. `PageChange` was
the other candidate and was rejected: an SDK type, six test constructors, three `MemoryWrite`
sites and a join on the feed select, for a dual-write nothing reads until unit B.

Two dialect facts the migrations carry: a SQLite batch recreate drops the table's triggers with the
table, so `page`'s two revision triggers are dropped and recreated around its batch op, as
`20260907150257` does; and alembic's SQLite batch `create_unique_constraint` silently no-ops, so the
key is a unique index there.

### Unit B — reads move onto `uid` (as built)

The seam decides the size. The SDK's `page_id` and `source_id` keep their names and change which
column they are: `CorePageFeed` emits `page.uid` and `source.uid`, ordered and cursored by
`(revision, uid)`; `page_states`, `readable_page_states`, `readable_source_ids`, `sources`,
`source_pages`, `forget_page`, `rewindow_sources`, `schedule_source_sync` and the value
`register_source` returns all speak uid; member-context refs are `page/<uid>`. Core's own
`page ⨝ source` joins stay on the content ids until unit D. `ExtensionContext.source_id` — the
predictor of a row's content id — went with its one caller; the sources extension asks whether a
connection holds a stream by `(connection_id, stream)`, the natural key unit 0 stored.

The memory extension joins, fences, writes and conflicts on the `_uid` twins and stops writing the
`_id` columns. `memory_0019` re-syncs the twins from the ids (the replaced release re-points ids on
re-derivation without touching twins), settles rows whose page or source is gone (a link to nothing
is dropped; a fact whose page is gone is retired with its whole binding cleared, ids and twins, so
it satisfies the check in either form), then moves the keys: `memory_source` PK
`(memory_item_id, page_uid)`, `mem_page` PK `page_uid` with `(workspace_id, page_uid) → page`
cascade, the `memory_item` check over the twins. The id-keyed keys survive as unique indexes because
the release being replaced names them as `ON CONFLICT` arbiters during the roll. SQLite rebuilds the
two tables (alembic's batch mode drops an unnamed primary key).

The Turbopuffer rewrite is an attribute patch, not a copy: `IndexBackend.reattribute(scope,
owner_id)` is `patch_by_filter` there and one `UPDATE` on the default backend. `mem_page.page_id`
is the marker for chunks still filed under a content id; `PageIndexer._apply` adopts a page before
indexing it and `memory_adopt_page_chunks` drains the rest 500 a minute per workspace. Search
resolves a hit through either key while the corpus moves. Chunk digests embed the owner id, so a
page's first re-index after adoption re-embeds it once — the cost of a body edit, paid only by pages
that change. The corpus-wide count this section once asked for is moot: the patch is per page and
bounded by the mirror table, not by chunk volume.

Measured surface, final: 8 core seam methods, the feed, 1 job-runner cursor read; 31 store sites,
8 condenser, 2 manifest, 5 objects; 3 backends; 44 test seeds re-keyed so that a lookup by the
wrong key finds nothing.

### Unit C — the cutover (as built, two revisions)

**C-a, expand.** `page` gains `source_uid` — the sync driver writes it (the claim carries the
source's uid) and the feed reads it, falling back to the join for rows the replaced release landed.
`source_new` and `page_new` are the live tables' columns, defaults and checks, `PARTITION BY HASH
(workspace_id)` in sixteen partitions, primary key `(workspace_id, uid)`, every unique index led by
the partition column, `page_new → source_new` by `(workspace_id, source_uid)` with the cascade,
plain `(id)` and `(source_id)` indexes for the content-id reads unit D removes (a partitioned unique
index must lead with the partition column, so `(workspace_id, id)` alone would leave `WHERE id = …`
scanning every partition), and the live table's `ufo_workspace_rls` policy cloned onto each parent.
Row triggers on the live tables mirror every insert, update and delete into the shadow, committed
in an `autocommit_block` before the backfill so a write racing the copy lands in both; the copy's
`ON CONFLICT DO NOTHING` keeps the newer row; a sweep drops copies whose original went; one
statement — one snapshot — checks that neither side has a row the other lacks, and raises. The two
`ON CONFLICT (source.id)` sites name `(workspace_id, id)`, which the live table already arbitrates and
a partitioned table can. Sixteen is a decision, not a default: with one large tenant the skew is
total whatever the count, and the count only has to stay cheap to plan until the tenant
distribution says otherwise. Logical replication was costed and declined: the tables are 1 GB, the
trigger mirror is ~60 lines the migration verifies itself, and a slot would have moved the risk to
an operator surface nobody watches.

**C-b, contract.** One transaction under `ACCESS EXCLUSIVE` locks: the mirror triggers go; every
foreign key another table holds on the two live tables is dropped with its definition kept
(`pg_constraint`, `coninhcount = 0` — a partition's inherited copy is its parent's); the live tables'
indexes and constraints take `*_old` names and the tables become `source_old` and `page_old`; the
shadows and their partitions take the live names and the canonical index and constraint names; the
revision trigger is recreated on the new `page` (its function names no key); the captured foreign
keys come back against the new tables. A rename is a catalog write, so the fleet resumes without a
beat. The old tables stay, frozen, for unit D to drop. The downgrade is the cutover in reverse:
rows written or removed since the swap are carried back first, so the old tables are the live
tables' equal when they take the names again. SQLite carries no partitions and rebuilds the two
tables in the same logical shape, both ways.

**What moved to unit B.** `mem_page.page_id → page (id)` names the content id alone, which no
partitioned `page` can hold; it is dropped in `memory_0019`, where the last release that wrote the
column left, and its downgrade restores it workspace-qualified once `page` is partitioned.

### Unit D — the content ids go (as built, two revisions)

**D-a, unwritten (`20260909204543`, `memory_0020`).** `source.id`, `page.id` and `page.source_id`
become nullable and no writer names them: sync, context, surfaces, grants and the memory extension
key by `uid` and by the natural keys unit 0 stored — a page within its source is `source_identity`,
a stream within its connection is `(connection_id, stream)`. Core's own `page ⨝ source` joins move
onto `(workspace_id, source_uid)`. The adopt job and `IndexBackend.reattribute` go with the last
chunk filed under a content id: `memory_0020` refuses to land while any `mem_page.page_id` marker
remains, so the deploy waits for the drain rather than orphaning a page's chunks. A page synced
before `source_identity` existed carries none and is never matched by a fetch again; it stays as
uid-keyed history, and a snapshot source tombstones it on its next pass and lands the ref again
under a new uid.

**D-b, dropped (`20260909233938`, `memory_0021`).** `source_old` and `page_old` go, then the three
columns with the five indexes that served them (`source_workspace_identity`, `source_id`,
`page_workspace_identity`, `page_source_id`, `page_id` — a drop on the partitioned parent reaches
every partition); the memory tables lose `created_from_page_id`, both `source_id`s, both `page_id`s
and the two id-keyed unique indexes `memory_0019` kept as `ON CONFLICT` arbiters. The downgrade is
the release being replaced's shape: the columns return nullable with their indexes, and
`source_old`/`page_old` return empty in the pre-swap shape under the `_old` names the swap's
downgrade strips — content ids NOT NULL as they were, since D-a's downgrade runs before the copy and
refuses a live row that lacks one. A test compares the migrated `source`, `page` and memory tables
against `tables.py` and the memory store's metadata with alembic's `compare_metadata` and holds them
equal. Alembic runs core to its head before any extension branch on a fresh database, so every
extension revision that ever named `page.id` or `source.id` (memory 0004, 0012, 0017, 0018, 0019;
coding_0001) skips that statement where the column is gone — there is no row for it to touch.

**No renames.** `uid`, `source_uid`, `page_uid` and `created_from_page_uid` are the permanent
names. Renaming them to `id` is a catalog write no statement of the outgoing image survives, so it
would need a quiesced fleet, and it buys nothing a reader needs.

## Risks

- **Unit C is the one that can lose writes.** Dual-write bugs are silent. The verification step is
  not optional, and the swap must be reversible for one release.
- **RLS follows partitions by name.** `rls.rs` bootstraps every `pg_tables` row, so each partition
  gets its own policy; harmless, but the partitioned parent must carry the policy too or a query
  through the parent is unfenced. Verify on the shadow table before cutover.
- **Partition count is a decision, not a default.** Too few and a large tenant dominates a
  partition; too many and planning cost rises. Pick from the tenant size distribution, not a
  round number.
- **A new read on the write path is a behaviour change, not plumbing.** Unit A's first cut had
  `commit` read page state to fill the twins; eleven tests whose page-state fake counts calls shifted
  by one. Data a writer needs rides the write.
- Both changes touch nearly every query in core and the memory extension. Units A and B are
  where the churn actually lands, and they are large regardless of sequencing.
