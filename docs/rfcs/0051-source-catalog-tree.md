---
rfc: 0051
title: "The source catalog is a tree — a stream's partitions are its parent's records"
status: proposed
date: 2026-09-12
---

# The source catalog is a tree — a stream's partitions are its parent's records

> Connectors declare streams as a flat list, so every collection a provider publishes only under a
> parent is reached by hand. Six different descent mechanisms have been invented across 28
> connectors to do it, one connector declares a phantom stream purely to be a parent, and the
> collections nobody got to — GitHub `timeline` and `reviews` among them — are simply absent. One
> declaration replaces all six: a stream names the parents it hangs under, and its partitions are
> those parents' landed records. Extends `spec.md` §Sources.

## The measured state, 2026-09-12

55 connectors, 691 declared streams. Every provider module scanned for templated paths and descent
helpers.

### Six named mechanisms, none aware of the others

| mechanism | connectors | shape |
|---|---|---|
| `PartitionWalk` + a private enumerator | github (`_repo_partitions`), slack | cursor map per partition |
| a walker per arity: `_paginate_two_level`, `_paginate_three_level` | freshdesk | depth is in the signature |
| `_Hop` chain + recursive `_walk_children` | zendesk | arbitrary depth, 7 streams |
| `{parent_path}/{parent_id}/{child_path}` + a phantom parent stream | recurly | 5 substreams |
| `per_list_child` dict + `stamp_parent_field` | mailchimp | 8 streams, 3 deep |
| `parent_external_id` stamp on the record | monday | 2 streams |
| bare per-stream `if` branches in `paginate` | ~20 others | one-off each |

**`recurly.py:74` is the sharpest artifact in the catalog:**

```python
_stream("unique_coupons_parent", source_object="coupons"),
```

A stream that exists only to be a parent. Its `paginate` branch walks `/coupons`, filters to
`coupon_type == "bulk"`, and lands the rows as a stream of their own so that
`unique_coupon_codes` has something to descend from. The catalog needs the parent relationship
badly enough to fake it with a phantom collection.

### The redundancy this costs, measured

GitHub's `_repo_partitions` (github.py:374) walks `/user/orgs`, then `/orgs/{org}/repos` — which is
`_PATHS["repositories"]` exactly, and `repositories` is canonical, so those pages are already
landed. Twenty-one repo-scoped streams each re-derive that enumeration, every 60-second tick,
forever.

Freshdesk's descent table (freshdesk.py:189) re-fetches `/api/v2/discussions/categories` to descend
into forums — the same path `discussion_categories` declares as a stream at freshdesk.py:95.

Two connectors, no shared code, the same defect invented twice.

### 133 of 691 streams are children

| connector | child streams | depth | connector | child streams | depth |
|---|---|---|---|---|---|
| github | 24 | 3 | typeform | 2 | 2 |
| stripe | 14 | 2 | attio | 1 | 3 |
| greenhouse | 9 | 3 | airtable | 2 | 3 |
| clickup | 8 | 5 | | | |
| freshdesk | 7 | 4 | jira | 2 | 3 |
| zendesk | 8 | 3 | monday | 2 | 2 |
| mailchimp | 8 | 3 | | | |
| hubspot | 7 | 2 | deel | 2 | 2 |
| recurly | 5 | 2 | intercom | 2 | 2 |
| chargebee | 4 | 2 | notion | 3 | recursive |
| sentry | 4 | 3 | googledrive | 3 | 2 |
| microsoft_teams | 3 | 3 | slack | 3 | 3 |
| instagram | 6 | 2 | calendly, mercury, pagerduty, bamboohr | 1 each | 2 |

Twenty-seven connectors. Depth reaches five (clickup: team → space → folder → list → task).

Every row is verified against a primary source in `0051-parent-map.json` (see *Verified parent
map*). A stream is a child when its path carries a placeholder filled from another stream's record;
tenant placeholders (`{cloud_id}`, `{account_id}`) and self-naming ones (`{stream.source_object}`)
are excluded — see *What the tree does not cover*. googlemeet, pandadoc and googlesheets, first
counted as children, assemble their descent inside one stream and contribute no row —
googlesheets' `sheets` is an array inside the spreadsheet response, reached by no request, which
`ParentEdge` refuses by construction.

### And the collections nobody reached

Because each descent is hand-rolled, the ones nobody wrote are invisible rather than missing:

| connector | absent stream | only endpoint | status |
|---|---|---|---|
| github | a PR's checks, reviews, mergeability | GraphQL `pullRequest { statusCheckRollup … }` | never fetched; REST `/timeline` carries no check event |
| github | `workflow_jobs` | `/actions/runs/{id}/jobs` | never declared |
| slack | `conversation_threads` (canonical) | `conversations.replies` | never called; thread rows carry no reply bodies |
| microsoft_teams | `channel_messages` (canonical) | `…/messages/{id}/replies` | never called; Graph omits replies by default |
| deel | `payslips` (canonical) | `/rest/gp/workers/{id}/payslips` | declared flat |
| freshdesk | `discussion_topics`, `discussion_comments` | only under a forum / a topic | ask a flat parent that does not exist |

The 2026-09-08 audit of all 56 connectors found the same class from the other end: eight streams
asking a wrong path or key, every one landing zero records **invisibly** —
`MAX_RECORDS_PER_RUN` counts landed records so it never trips, an emptied page yields no span so
`PartitionWalk` keeps descending, and the row just looks quiet.

## The design: three ideas

### 1. A stream's partitions are its parent stream's records

```python
_stream("organizations",  parent=None,            path="/user/orgs")
_stream("repositories",   parent="organizations", path="/orgs/{login}/repos",       canonical=True)
_stream("issues",         parent="repositories",  path="/repos/{full_name}/issues", canonical=True)
_stream("pull_requests",  parent="repositories",  path="/repos/{full_name}/pulls",  canonical=True)
_stream("timeline",       parent="issues",        path="{url}/timeline")
_stream("reviews",        parent="pull_requests", path="{url}/reviews")
_stream("workflow_jobs",  parent="workflow_runs", path="{jobs_url}")
```

A stream reached under more than one parent names each edge:

```python
_stream("lists",  parents=[("spaces", "/space/{id}/list"), ("folders", "/folder/{id}/list")])
_stream("blocks", parents=[("pages", "/blocks/{id}/children"), ("blocks", "/blocks/{id}/children")])
```

**Placeholders are dotted field paths read off the parent record.** No mapping table beside the
path, so there is no second place to look and nothing to drift. A placeholder naming a field the
parent record does not carry raises at fan-out — loud, where the current failure is silent.

Where a provider is HATEOAS the child path is one field of its parent (`{url}/timeline`,
`{jobs_url}`). Where it is not, the parent's own id fields compose it
(`/repos/{full_name}/issues`, `/api/v2/discussions/forums/{id}/topics`). Both are the same rule.

The walk keys a partition's cursor entry by **the parent page's ref**, which `page` already
stores. A child page's own identity keeps the `<stream>/` prefix the driver already writes and is
scoped under it by **the values its edge's placeholders read**, in path order:
`/repos/{full_name}/issues` → `issues/acme/ufo/3122`, `/orgs/{login}/repos` →
`repositories/acme/100` — **when the key is parent-local**. That is a fact about the provider, so
the stream declares it: `key_scope="local"` (the default: a branch `name`, a commit `sha`, a Teams
`chatMessage.id` — Graph's reference: "unique within a chat/channel… might be duplicated in other
chats/channels") scopes the identity; `key_scope="global"` (a Stripe `ch_…`, a Freshdesk ticket id,
a Typeform response id — Airbyte declares each an unscoped primary key; a Slack message, whose key
the connector already composes as `channel:ts`) keeps `<stream>/<key>`. The
partition scope keys the cursor map and the request path either way; the bit decides only whether
it is part of the page's identity.

For GitHub, `local` everywhere reproduces byte-for-byte the identity the old `flatten` computed by
hand from `repo_full_name`/`org_login` (verified against main for four fixtures), so converting the
connector re-lands nothing. Three wave-2 batches measured why that could not generalise: every other
connector keys its children flat on main, and `ParentEdge` requires a placeholder, so a converted
child always gains a scope segment — eleven canonical streams stopped on it. The bit is what lets a
flat-keyed conversion re-land nothing, and what makes a `local` restamp the fix for a measured
collision (two channels' message `1616990032035` on one page; a Drive permission id repeating on
every file) rather than churn. A `local` restamp of a canonical stream ships its own tombstone in
its unit — `delete_missing=True` where the stream is a true per-parent snapshot, a bounded migration
otherwise.

**Parity is a live question only for streams canonical on main**: `ConnectedSources._create`
registers canonical streams and nothing else, so a non-canonical stream has never landed a page in
any deployment and its identity is free to change.

An identity built from the parent's *page ref* instead would embed the whole ancestor chain in
every child key — the defect RFC 0046 removed from `page.id` — and would restamp every existing
page at deploy, the 2026-09-07 incident's shape at several times the size.

**A stream declares one or more `(parent, path)` edges, not exactly one.** Two connectors force it,
in the two possible ways:

| connector | stream | parents | paths |
|---|---|---|---|
| clickup | `lists` | `spaces`, `folders` | `/space/{id}/list`, `/folder/{id}/list` — distinct |
| notion | `blocks` | `pages`, `blocks` | `/blocks/{id}/children` — shared |

Notion's second edge is the self-edge: `_blocks` (notion.py:144) walks search results for pages and
calls `_block_children` on each, which then recurses on itself. Two parents, one path. So the
catalog is a directed graph that happens to be a tree in 28 of 30 connectors — the rule is unchanged
("partitions are the parents' records"), only the arity is ≥ 1.

An edge set is also what keeps the declaration honest: a stream reachable two ways declares both,
where today clickup writes two path templates and joins them by hand.

**An edge may carry a declarative predicate over the parent record** — `where` (allowed values) and
`unless` (excluded values), data and never a callable — so that a fan-out can skip a parent it has
nothing to ask: `unique_coupon_codes` hangs only under `coupon_type == "bulk"` coupons, and a Notion
`blocks` edge skips `child_page`/`child_database`/`ai_block` blocks and `has_children: false`
leaves, which otherwise cost one empty request each per pass. Predicate fields join the parent's
projection; a parent lacking one is a non-match, not a fault — only a *path* field the parent does
not carry raises. Wave 2E measured the gap: without it, notion's `_BLOCK_NO_DESCEND` and recurly's
bulk filter had nowhere to live but the hand-rolled walkers the tree deletes. The non-match rule has
a trap 2E also hit: a `where` on a field the parent *kind* never carries admits nothing — a Notion
page has no `has_children`, so that predicate belongs on the self-edge only, and a bare `pages` edge
beside it. An edge that admits zero of a non-empty parent set on a completed pass is the fetched-vs-
landed signal the 2026-09-08 audit asked for, at the edge; it is an open question below.

**An edge may carry parent fields onto the child record** — `carry={"channel_name": "name", …}`,
child field ← parent field path. Four connectors needed it: slack denormalised the channel's
`name`/`type`/`is_private` onto every message (canonical: without `carry` the whole corpus takes a
new revision at deploy and a message page loses its channel name); stripe's line items took the
parent's `created` as their own timestamp; hubspot `consent_states` builds its identity from the
parent's `id` while its path reads `{email}`; zendesk stamped `article_id` on each comment; clickup
stamped the list's `name` on every task (canonical — the fifth instance). A declared carry is data
where a hand-rolled stamp was code; a carried field the parent lacks writes nothing; a
target that collides with a provider field on the child is refused at declaration.

### 2. `canonical` selects which parent records to fan over

| `canonical` | partitions |
|---|---|
| True | every landed parent record |
| False | the parents a live `source_trigger`'s resource resolves to — every stream hanging under that resource's kind, not streams the caller names |

**The member's interface is a URL and nothing else.** "Watch this pull request" resolves to the
PR's own partition of `pull_requests`, visited every tick. On GitHub that stream is GraphQL-backed:
one `pullRequest` query returns the object with `statusCheckRollup`, `reviewDecision`, `mergeable`,
`totalCommentsCount`, reviews, review threads, files, a bounded slice of
`timelineItems` and the total of every clipped list, and the watched read then pages its threads,
checks and files to the end (100 a page, 1 point a page) — so the watched page carries every check
run and status, and the catalog's page, which keeps the clipped lists, still moves on any activity
past a clip because the totals and the rollup's counts by state move — on GraphQL's own 5,000-point
budget.
REST cannot express it: `/issues/{n}/timeline` carries review, comment and merge events and **no
check or status event at all**, so a REST fan-out over `timeline`/`reviews`/`files` would miss the
one thing a PR watch is usually for. Bulk incremental streams (`comments`, `review_comments`,
`commits`) stay REST, where `?since=` is one request per repo and GraphQL would need per-issue
traversal — the mixed shape Airbyte's connector and `gh` both use. The repo-scoped streams whose
records link to the PR still reach it through the existing body match (`workflow_runs`). `streams`
on the trigger survives only as a narrowing on a whole-feed watch.

No new field. `canonical` stops meaning sync/don't-sync and starts meaning all-parents/watched-
parents — and watched-parents is a subset of all-parents, so both are one code path with a
different partition filter.

This is what makes the tree affordable. Fanning `timeline` over every issue in every repo is
ruinous; fanning it over the one pull request a thread is watching is two requests a minute.
Remove this idea and the only options are sync-everything (the `workflow_runs` pathology: 10,000
pages per repo across every org repo, starving three sibling rows) or sync-nothing.

### 3. A pass has a partition budget and an interval; a watched partition is visited every tick

Without a bound the tree is quadratic: Notion with 5,000 landed blocks would issue 5,000 requests a
tick.

**The bound is not "has this parent changed".** That was the first formulation and it is wrong twice
over:

1. *A parent's revision is not a sound proxy for its children changing.* GitHub's repository record
   moves `pushed_at` on a push but not on a new issue comment, so gating `comments` on the repo
   page's revision would drop comments indefinitely. The signal the gate needs is not in the parent
   record.
2. *It cannot work for an `Ordering.none` child at all.* A `none` stream has no cursor, so a full
   re-walk **is** how it notices a changed row. Gate it on a parent that rarely moves and it
   freezes.

The real lever is not *which* partitions to visit but *how many per tick* and *how often a pass
repeats* — and `PartitionWalk` is already most of the way there. `_stream_unordered` returns early
for a partition already in the stored map and stamps it on completion, so one pass already spreads
across ticks. The only defect is that a completed pass dissolves its markers and restarts on the
very next tick.

So: a per-row **fetch budget per tick**, and a per-row **pass interval** that a completed pass waits
out before the next one opens. Per row, not per connection: rows are claimed and run independently,
so a connection-wide counter would be shared state the driver does not have, while a row already
owns its cursor map, its `Ordering` and its `next_sync_at` — the budget and the interval sit beside
them. A connection's spend is the sum of its rows' budgets, which an operator can read off the
declarations. Watched partitions go **first in the budget, not outside it**: a watched pull request is ~6 child requests a tick, so ten watched PRs at 60 s are
~3,600 requests an hour against GitHub's 5,000 — and they share that token with the whole canonical
catalog. An exemption would let watches starve the feed; priority lets them slow each other down
instead.

```
fetches per tick, per row  =  budget,  spent watched partitions first, then canonical ones round-robin
```

Bounded absolutely, identical across all three `Ordering` values, no per-partition revision lookup,
and it reuses the marker map that exists. Every tick reads the whole local parent catalog but
fetches only its budget slice; a completed pass keeps the cursor of every parent still in that
catalog and drops only absent parents. The trade is stated rather than hidden: a canonical child's
worst-case staleness is `|partitions| / (budget − watched)` ticks plus the interval; a watched
resource's is one tick until the watches alone exceed the budget.

**The seam has a bound by default.** No production stream declares `fetch_budget`, so a declaration
that is only a knob leaves every tree child unbounded in exactly the quadratic configuration. The
effective budget is resolved once, where `fanned_out` builds the walk: the declaration where one is
made, else `DEFAULT_FETCH_BUDGET` — 100 partitions a run, one request each per 60 s tick, so at most
100 requests a minute per row: under Zendesk's 200/min Team plan, HubSpot's 100 per 10 s per private
app and Intercom's 10,000/min, and over GitHub's 5,000 an hour (83/min) per installation, which a
github row must declare down from. A `delete_missing` stream is the one exception and it is not a
branch on the default: its declaration refuses `fetch_budget`, `pass_interval_seconds` and `refan`
outright, because a snapshot that visited some partitions, or none, would sweep the pages of every
parent it did not visit — a snapshot fan-out is unbounded and whole.

**This unit is a prerequisite for some conversions, not polish after them.** Wave 2F measured why:
freshdesk `conversations` (canonical, `Ordering.none`) used to descend only the tickets its ticket
watermark admitted — one request on a quiet tick; under the tree it fans over every landed ticket
on each completed pass, so its steady state goes from ~1 to N requests. That is what `none` means
and the fixture's 2 → 1 hides it. Wave 2C measured the second instance: intercom
`conversation_parts` narrowed by `POST /conversations/search` since the watermark — `1 + changed`
requests on main, `landed` under the tree, and the gap widens with the account. A canonical `none`
child under a large parent merges with the budget in place or not at all.

Both providers bump the parent when a child lands — Freshdesk updates the ticket on a reply, Intercom
the conversation on a part — so for exactly these edges the revision gate rejected as *the bound* is
sound as *a narrowing*: `ParentEdge(refan="on_parent_change")` re-fans a parent only when its page
revision moved since the last fan, restoring `1 + changed`. It is declared per edge because it is a
provider fact (GitHub does not bump a repository on a comment), it defaults off, and it sits inside
the budget rather than replacing it. A pass freezes the greatest parent revision when it opens and
stores that ceiling while it spans ticks. On completion that ceiling becomes the next pass's lower
bound, so a parent that changes after its slice remains above it and is fetched by the next pass.

**Declared in the catalog** — every syncing child, the provider fact behind each knob, and the plan
each budget is sized to. The connector cannot read an account's plan, so the lowest paid one sets
the number; a 429 storm is what the budget exists to prevent, and a Pro account merely completes a
pass sooner than it had to.

| connector | syncing child ← parent | `refan` | `fetch_budget` | why |
|---|---|---|---|---|
| intercom | `conversation_parts` ← `conversations` | `on_parent_change` | default | main narrowed by `POST /conversations/search` on `updated_at > cursor`, then one detail read per hit (intercom.py:317-324 on main); `updated_at` is "the last time the conversation was updated", and a part is one. 10,000 calls/min per app leaves the default right |
| freshdesk | `conversations` ← `tickets` | `on_parent_change` | 20 | main walked only the tickets `updated_since=<cursor>` admitted (freshdesk.py:252-263 on main), a filter Freshdesk documents as "any activity since". Growth: 100 calls/min, 40 list calls/min, account-wide |
| freshdesk | `solution_folders` ← `solution_categories`, `solution_articles` ← `solution_folders` | none — a folder's `updated_at` is not documented to move on an article | 5, 5 | the same 40 list calls/min: 20 + 5 + 5 beside ~9 root reads a tick |
| clickup | `spaces` ← `teams`, `folders` ← `spaces`, `lists` ← `folders`/`spaces`, `tasks` ← `lists` | none — no ClickUp parent carries a timestamp a child moves | 5, 10, 15, 15 | 100 requests/min per token on Free, Unlimited and Business; a list's tasks cost ≥ 2 requests a tick (`?page` runs to the first empty answer and takes no time bound), so the ceiling is 2 + 5 + 10 + 15 + 2·15 = 62 and a 300-task list (4 pages) still fits |
| slack | `messages`, `conversation_threads` ← `conversations` | none — a channel record carries no last-message field | 20, 20 | `conversations.history` is Tier 3, 50+/min per method per workspace per app, and both rows read it for the same channels: 20 + 20 + 2 `users.list` ≤ 50. An app created after 2025-05-29 and distributed outside the Marketplace gets 1/min and `limit` 15 — a fact about the app's listing that no budget answers |
| github | six REST children ← `repositories`; `pull_requests` (GraphQL) | none — a repository moves `pushed_at` on a push, not on a comment | 12 each; 20 | with the github owner: 5,000 requests/h per token is 83/min shared by every row, 6 · 12 + orgs + root ≤ 83; GraphQL 5,000 points/h at 3 points a page is 27 pages/min, and 20 leaves 23 points for 1-point watched reads |
| airtable `records`, monday `items`, sentry `issues`, typeform `responses`, zendesk `article_comments`, mailchimp `list_members`, microsoft_teams `channels`, `channel_messages` | | none — no parent timestamp is documented to move on a child (a board's `updated_at`, an article's `updated_at`, a form's `last_updated_at` name the parent's own edits) | default | 5 req/s per base; a complexity budget; 2 req/s per account (120/min — the one row within 20 of the default); 200/min in the Help Center bucket on Team. deel `tasks` is a snapshot and takes no knob |

### A child fans only over parents that landed with their projection

A child composes its path from fields of the parent record, so those fields must be on the parent's
page row — a projection written when the page lands (`page.parent_fields`: only the fields the
declared edges read, `NULL` for a page nothing hangs under). Two consequences, both deploy-shaped:

| parent's `ordering` | a page landed before the edge existed | reached by children |
|---|---|---|
| `none` | re-walked whole on the next completed pass; its unchanged body takes the metadata-only update, which writes the projection | after one pass |
| `ascending` / `newest_first` | never re-lands — history sits below the watermark | **never**, unless refetched |

So an edge declared under a `none` parent backfills itself on the first pass after deploy, and an
edge declared later under an ordered parent reaches only the parents landed after it. The unit
that adds such an edge ships the refetch of its parent row (`rewindow_sources(…, refetch=…)`
already does this shape) or it has declared a child that fans over nothing. GitHub's unit 1 hangs
every child off `organizations` and `repositories`, both `none`, so it needs no refetch; unit 2's
`timeline` hangs off `issues`, which is `ascending`, and its partitions come from the watch's
resource URL rather than from landed pages — which is why the watch path needs no backfill either.

A `delete_missing` child may sweep only against a whole enumeration, and a live parent page with no
projection is a hole in it: the children under that parent are live pages no partition of the pass
mentions, so the sweep would tombstone them for a run that observed nothing. The reader hands such
a page down as an `UnprojectedParent`; `fanned_out` raises on it for a `delete_missing` stream (the
run fails, commits nothing, and clears once the parent re-lands with its projection) and passes it
over for any other, which asserts nothing about deletions.

An empty parent catalog becomes authoritative only after its row completes a sync. `source.synced_at`
records that completion; an existing live page proves it during rollout. Before either exists the reader
hands down an `UnreadyParent`, which an incremental child passes over and a `delete_missing` child
refuses. This separates a valid empty result from a parent row that has not run without ordering
claims inside one connection.

### The cursor map lives beside the watermark, not in it

While the fleet rolls, the outgoing image and this one run the same `source` row. The outgoing one
reads a converted child's `cursor` as its own plain watermark — Intercom sends `updated_at > "{}"`,
Mailchimp sends the map as `since_*`, Teams compares timestamps against JSON — and this one would
read its bare-key map as an empty one. So a stream with `parents` keeps its map in
`source.partition_cursor` (migration `20260913044546`, nullable, nothing backfilled) and never
touches `cursor`; every other stream keeps `cursor` exactly as today. The driver decides the column
once, at the claim, from the backend's own declaration (`PartitionedBackend.partitioned`), and
`ClaimedSource.resume`/`advanced` are the only readers of the pair. A tree row therefore starts from
an empty map on this image and re-walks from its floor once — accepted. `Partition.key` is the bare
`ref` for a root partition (one a connector enumerates itself, under no edge) and `ref\npath` for a
fanned-out one, so a hand-walked stream's stored entries keep matching byte for byte and two edges
to one parent record still keep two entries.

### The derived rule, not a fourth idea

**A stream syncs if it is canonical, an ancestor of a canonical stream, or watched.**

GitHub's `organizations` is not canonical but is the root that `repositories` fans from, so it must
sync. That falls out as the transitive closure of `canonical` up the tree; nothing is declared for
it. Whether such an ancestor's pages reach memory is `indexed`, which already exists and already
answers exactly that. The two existing booleans cover the whole matrix:

| | `canonical` | `indexed` |
|---|---|---|
| is it content a member asked for? | yes | — |
| do its pages reach memory? | — | yes |
| does it sync? | canonical, or ancestor of canonical, or watched | — |

## Worked conversions

Every shape class in the catalog, not one.

### github — enumerator deleted

`_repo_partitions`, `_iter_user_repos`, `_iter_user_orgs`, `_ORG_SCOPE_GATE_STATUS`,
`_partition_field`, `REPO_PARTITION_FIELD`, `ORG_PARTITION_FIELD` all go. `flatten` keeps its
`stargazers`/`pull_requests` shaping and loses 36 lines of primary-key scoping: the collision it
patches (every repo's `main` landing on one page) exists *because* the partition is not part of the
page's address, and under the tree it is. `_repo_pages` keeps its `?since`/`?until`/`created>=`
param shaping and loses its partition plumbing.

GitHub's `_PATHS` is 21 repo-scoped paths, 3 org-scoped and one flat root (`organizations`) — so
24 of its 25 streams are children and the tree describes the whole connector.

Measured: 244 of 692 lines are enumeration, partition and key-scoping machinery; ~150 delete.

### freshdesk — a walker per depth, and a latent bug the tree makes impossible

Freshdesk has **two descent helpers, one per arity**: `_paginate_two_level(parent_path,
child_path_template)` and `_paginate_three_level(root_path, mid_path_template,
leaf_path_template)`. Depth is hard-coded into the helper's signature, so a fourth level needs a
third helper.

`discussion_categories → discussion_forums → discussion_topics → discussion_comments` is four deep.
It is walked as two independent two-level pairs, which requires flat `/api/v2/discussions/forums`
and `/api/v2/discussions/topics` parent collections.

**Those endpoints do not exist.** Airbyte's `source-freshdesk` manifest reaches both only through
chained `SubstreamPartitionRouter`s — `discussion_comments` ← `discussion_topics` ←
`discussion_forums` ← `discussion_categories` (manifest.yaml:446, 558, 502, 398) — and declares no
flat path for either; `grep -c 'path: discussions/forums$|path: discussions/topics$'` over that
manifest returns 0.

Both streams are non-canonical, so the bug is latent rather than live: it fires the moment either is
marked canonical. It is the zendesk `article_comments` shape exactly, in a connector the 2026-09-08
audit cleared — because that audit checked record *paths* (envelope keys) and never checked
endpoint *parents*.

`solution_articles` is correct today and stays correct: its three-level walk
(`solutions/categories` → `categories/{id}/folders` → `solutions/folders/{id}/articles`) matches
Airbyte line for line (1167, 1215, 1111).

Under the tree `discussion_categories` is the root and the three below it declare one `parent`
each. Both helpers delete, and a chain that skips a level cannot be expressed.

### zendesk — the `_Hop` chain is its own transitive closure

`_CHILD_COLLECTIONS` (zendesk.py:73) maps 7 streams to hop tuples — PR #3181's fix for canonical
`article_comments`, which until then requested a flat `help_center/article_comments.json` that
404s. It walks the right paths today and matches Airbyte line for line. Each hop becomes one
`parent`: `article_comments` → `articles`, `article_comment_votes` → `article_comments`. The chain
the recursion walks is the path up the tree, so `_Hop`, `_CHILD_COLLECTIONS` and `_walk_children`
delete and the declaration carries what they encoded.

### recurly — the phantom stream is deleted, not converted

`unique_coupons_parent` exists only to be a parent. Under the tree `unique_coupon_codes` declares
`parent="coupons"` and the phantom goes, along with its `paginate` branch. The `coupon_type ==
"bulk"` filter moves to the child's fan-out as a partition predicate.

### mailchimp — two parent sets, one rule

Eight children over two parent sets: `list_members`, `segments`, `tags` and `interest_categories`
under `lists` (the `per_list_child` dict), `email_activity` and `unsubscribes` under `reports`, then
`interests` under `interest_categories` and `segment_members` under `segments` — both 3 deep, and
both needing their own bespoke walker (`_paginate_interests`) because `per_list_child` reaches only
one level. All eight become one `parent=` each; `stamp_parent_field="list_id"` deletes, since the
parent ref is the partition.

### clickup — five levels, no special case

`team → space → folder → list → task`, plus `list_comments` and `list_custom_fields` beside
`tasks`, and `goals` under `team` — seven children over eight hand-rolled path templates.

`lists` has **two** parents (`/space/{id}/list` for folderless lists, `/folder/{id}/list` for the
rest) — the two-edge case from idea 1, declared rather than joined by hand.

Depth is free: the cursor map is per-stream-row keyed by parent ref, so nothing about depth 5
differs from depth 2.

### sentry — the root is a stream

Four of its six streams are children — `members` and `releases` under `organizations`, `issues` and
`events` under `projects` — but `projects` is a **root**, not a child: `/projects/` is flat and spans
every organization the grant reaches, so `organizations` hangs nothing canonical and does not
register. `syncing_streams` is `{issues, projects}`; the wave-2B conversion states that in a test.
`{org_slug}`/`{project_slug}` are the project record's own fields, so the path composes from the
parent exactly as GitHub's does.

### notion — recursion, and it gets better

Two edges: `parent="pages"` and `parent="blocks"`, both at `/blocks/{id}/children`. Partitions are
the pages plus the blocks landed so far, so each pass descends one level.

Google Drive is **not** this shape, though its path template suggests it: `files` is one flat
`q=trashed = false` listing (googledrive.py:126), and its three children hang off each file at
`/drive/v3/files/{id}/{source_object}` — depth 2, no folder recursion. Notion is the only
self-edge in the catalog.

This is strictly better than the in-run recursive walk it replaces. `_block_children`
(notion.py:156) calls itself at line 172 and is bounded by `MAX_BLOCK_DEPTH = 30` — a constant, not
the data — so it holds a recursion stack, cannot checkpoint mid-descent, and silently truncates a
tree deeper than 30. One level per tick is cursor-checkpointed, resumable, and bounded by the data:
a 10-deep tree reaches full depth in 10 ticks and a 40-deep one is not truncated, while idea 3's
budget holds the per-tick cost flat however wide the tree gets.

### stripe — ten children, no chain

`/v1/customers/{id}/payment_methods`, `/v1/invoices/{id}/lines`, and eight more. All depth 2, all
currently `if stream.name == …` branches inside `paginate`. All become one `parent=` each. The
~35 flat top-level streams beside them declare `parent=None` and are untouched.

### bamboohr, pandadoc, googlesheets — hydration is the degenerate case

`/v1/employees/{eid}` is a detail view: one child record per parent record. The tree expresses it
with no special case (`parent="employees"`, path `/v1/employees/{id}`), and it deletes a hand-rolled
list-then-get loop inside `paginate`.

### deel — a known-broken canonical stream

`payslips` is canonical and falls through to deel's default
`f"/rest/{stream.source_object}"` → `/rest/payslips`. The real endpoint is
`/rest/gp/workers/{id}/payslips`. `parent="gp_workers"` is the declaration that fixes it —
but deel is the one case the tree does not finish on its own: no reachable Global-Payroll worker
listing has been found, so the parent stream itself is unverified. The tree turns an unfixable
walker problem into a findable endpoint problem; it does not conjure the endpoint.

`tasks` is the clean half: the connector already enumerates contracts and reads each one's tasks,
stamping the contract id onto every row (deel.py module docstring). That hand-rolled walk and its
stamp become `parent="contracts"`.

### slack — already the tree, by hand

`channels → messages → replies` via `PartitionWalk` with a private enumerator, the same shape as
GitHub's. The enumerator deletes; `parent="channels"` replaces it.

## The canonical children that stopped, and what `key_scope` does with each

Every non-canonical child converted without incident — none had ever landed a page. Seventeen
canonical ones stopped because the tree's identity rule would move them. The measurements sort them:

| `key_scope` | streams | why |
|---|---|---|
| **`global`** — convert, identity byte-identical to main, nothing re-lands | typeform `responses`; freshdesk `conversations`, `solution_articles`; slack `messages`, `conversation_threads` (key already `channel:ts`); intercom `conversation_parts`; zendesk `article_comments`; monday `items`; sentry `issues`; airtable `records`; clickup `spaces`, `folders`, `lists`, `tasks` | Airbyte declares an unscoped primary key, or the connector already composes the parent in — the scope buys no disambiguation |
| **`local`** — the restamp is the fix; ship the tombstone with it. **Decided: all four convert**, ordered by blast radius — deel `tasks` (**landed on #3566**: `delete_missing=True` is its tombstone, the snapshot rule confirmed from `backend.py`; every landed deel task page re-derives once), mailchimp `list_members` (**landed on #3571**: `carry={"list_id": "id"}`, a bounded migration `20260913015909` as its tombstone — `delete_missing` would hold six figures of members in one uncapped run — two lists' `md5-of-ada` are two pages), microsoft_teams (**landed on #3565**: `teams → channels → channel_messages`, migration `20260913020953` joined on `backend` since slack also has a `channels` stream; nothing the old walk stamped ever reached a message body, so digests hold; two residues named — the roll window can re-land unscoped rows the outgoing image still writes, and the deleted pages' vector chunks stay in the index unreachable) — silent loss on a canonical stream against one re-derive was never close | microsoft_teams `channels`, `channel_messages` (Graph: "IDs… might be duplicated in other chats/channels"); mailchimp `list_members` (measured: two lists' `md5-of-ada` on one page); deel `tasks` (Deel's spec declares no uniqueness; no `GET /tasks/{id}`) | a collision main has today, documented or measured |
| neither — stays on its own walk | hubspot `consent_states` (identity built from a parent field the path does not read: under an edge every row is *dropped*, not re-keyed); hubspot `sequences` until 1d's `optional` | the seam cannot express it yet, and the failure mode is the silent one |

No canonical body moved. Five conversions would have — slack `messages`/`conversation_threads`
(channel name and type), zendesk `article_comments` (`article_id`), clickup `tasks` (`list_name`),
airtable `records` (`table_name`), hubspot `consent_states` (its identity) — and each is closed by a
`carry` whose proof is the rendered body's sha256 pinned as a literal against main's output, with the
carry emptied under mutation to show the literal executes. The parent-id stamps every hand-rolled
walk wrote (`repo_full_name`, `checkout_session_id`, `invoice_id`, `file_id`, …) are gone uniformly:
the address carries the parent now, and every stream that dropped one is non-canonical, so no landed
body changes. Four attio stamps on non-canonical `call_recordings` stay dropped because main
*computed* them (a `title` fallback, `start.datetime`-else-`start.date`, a duration) — a carry copies,
it does not derive, and a derived value belongs in `flatten` if it belongs anywhere.

Two conversions changed what the stream was, and that is the point: mercury `transactions` fans out no
more (`GET /api/v1/transactions` paginates flat; the per-account response carried only a `total`),
and stripe's two line-item streams lost the parent's `created` they were stamping as their own
timestamp — a non-path parent field does not survive the tree, and both ends moved together.

## What the holistic review found in the connectors, beyond the seam

Verified on the six branches merged onto `c2866b450` (clean merges, 570 provider tests green):

- **Ancestor rows reach memory by default, and nobody decided that.** `syncing_streams` registers
  airtable `bases`/`tables`, clickup `teams`, freshdesk `solution_categories`/`solution_folders`,
  sentry `projects`, github `organizations` — all `indexed=True` by default, all "a lookup list a
  join would want" in `canonical`'s own words. `indexed` exists for exactly this and no conversion
  touched it. Each owner states `indexed=` on its new ancestor rows, either way.
- **`carry` spelled as code.** sentry and clickup split `partition.scope` on `/` and index positions
  to stamp parent fields; intercom stamps `partition.scope` whole. The scope is an address, not a
  projection, and the declaration exists. Three `carry` maps replace ~40 lines.
- **Three 2A connectors drove `tree_partitions` directly** (mailchimp, recurly, monday) and so had no
  per-partition cursor — a run capped mid-fan-out re-issued every finished partition's requests on
  resume, and one stream-wide watermark filtered every partition. 1h's `TreeFanOut` removes the free
  function; they move onto the walk.
- **The digest pins are regression pins.** Four canonical conversions pin a sha256 literal against
  "main's output"; the literal was computed by hand on a branch where main's renderer is not
  reachable, and the mutation arm that proved each literal executes was run by hand, not committed.
  Either the tests carry the arm or the prose stops claiming provenance the code cannot check.
  zendesk `article_comments` pins a substring, not a digest.
- **Docstrings restate the seam.** 17 of 24 module docstrings carry a "partitions are … landed
  records" sentence that `ParentEdge` and `StreamSpec` state once; three are near-verbatim copies of
  each other; 22 carry "Widen the default seam by …", a restated signature that dies with 1i.
  hubspot says "Six streams hang under a parent" and lists seven. mercury's docstring has zero drift
  and states a measured alternative and its failure — the standard.
- **What the six got right without being told:** all seven `carry` maps translate a name, none is an
  identity map — one shape was enough; `key_scope` per stream is honest (only slack has every child
  `global`, one connector of 24 would use a connector default); stripe's `_partition_pages` is 38
  lines of Stripe-specific refusal logic that earns its name; mercury deleted its fan-out rather than
  converting it.

## What the holistic review found in the seam

Read at `1fbf02134` with unit 3's additions from `d9ef675fc`: `connector.py` 917 lines (526 on main),
`rest.py` 637, `backend.py` 507, `sdk/sources.py` 175. The seam grew by ~500 lines of code and
docstring for eleven mechanisms — the tree, `where`/`unless`, `optional`, `carry`, `key_scope`, the
encoding rule, the width gate, the fan-out tally, `fetch_budget`, `pass_interval_seconds`, `refan` —
each forced by a case one of 27 connectors measured, and together they delete six bespoke descent
mechanisms and, after round 4, ~800 lines of connector code. Docstrings are ~40% of it; every
paragraph states an invariant or a provider fact, in the register main's `PartitionWalk` already had.
The seam is the right place for all of it. What is not yet one shape:

0. **The whole stack, measured.** Seam at 1i plus the six batch PRs plus #3573 and #3574: five
   batches merge clean onto the stack and **2E conflicts** — 2E rewrote slack's `paginate_source` in
   round 4 and #3573 touched the same signature to thread `watched`; both are correct alone, and the
   merge order at landing decides who resolves it once. Without 2E the stack runs 733 + 306 tests green.
   github.py grows 840 → 983 for the GraphQL `pull_requests` stream, the watched partition and unit 3's
   declarations — a feature, not ceremony — so the whole change is roughly −215 lines of connector code
   with a materially larger catalog. **`from conftest import parents_reader` is a real collision:** the
   two test trees run in one pytest invocation fail collection with 20 errors, because
   `extensions/sources/tests/providers/conftest.py` and `core/tests/conftest.py` are both importable as
   `conftest` and the wrong one wins. CI is green only because no shard combines them today. The reader
   becomes a fixture, or moves to a named module; either way it leaves `conftest`. Unit 1j. 1j made it a fixture and github's test imports nothing from `conftest`; round 5's first
   return kept `from conftest import Landed` — the type alias — which is the same collision by another
   name (one argument order passes, the other errors at collection). The rule is not \"take the
   fixture\"; it is **no name is imported from `conftest`**, and the type is spelled inline.
1. **`paginate_source` exists only to drop two keyword arguments.** 1i made `parents` cross it
   always, because "the edges are the shape of the catalog"; `self_user_id` and `backfill_after`
   still drop, so five connectors (github, slack, gmail, outlook, datadog) override it to widen
   their own `paginate` — the 22-override ceremony at smaller scale. The one-shape answer is not
   to thread two more kwargs onto 55 signatures but to thread **one value object**:
   `paginate(client, stream, run)` where `run` is a frozen `Run(cursor, parents, self_user_id,
   backfill_after, pinned)` — five run-scoped values, the fifth being the watch's pinned partitions
   unit 3 had to add to `fanned_out` because a watch names a provider resource per run, not a stream
   constant. Then `paginate_source` is deleted, `fetch_page` calls `paginate` directly, the
   five overrides go, and a connector that reads the floor writes `run.backfill_after`. **Unit 1j — #3560 `949474ed3`**: `Run(cursor, parents, self_user_id, backfill_after)`,
   `paginate(client, stream, run)`, `paginate_source` deleted, the five overrides now plain `paginate`
   bodies; **66 files, +483 / −797 — the first unit in the series to shrink the tree**, while the seam
   itself moved five lines. The test reader is a fixture and both collection orders pass. `pinned` was
   left off `Run` on purpose: a field with no producer does not land, and `Run` is what makes adding
   it later free — the unit-2/3 owner adds it with no connector touched. Round 4 landed, and the decision was never open: five run-scoped
   values on 55 signatures against one object and two fewer methods is this RFC's own rule applied to
   its own seam. The all-connector pass is a cost, not a question. It also dissolves the one landing
   conflict on the stack — slack's `paginate_source` hunk between 2E and #3573 — because the method
   it lives in stops existing.
2. **`Partition.edge`'s docstring states a purpose it does not serve.** It says the template "tells
   two edges to one parent apart where the resolved paths cannot"; `Partition.key` uses `ref` and
   the resolved `path`, not `edge`. `edge` exists for the fan-out tally, which keys `fetched`/`landed`
   by template. Say that.
3. **`ParentEdge.address(parent, name)` returns one field's value**, not an address — the address
   is what `partition()` builds. `value(parent, name)`.
4. **Three single-caller helpers under `_PassMarks.take`** (`_marked`, `_pass_instant`,
   `_pass_count`) each wrap one parse and one raise; inline them.
5. **Five spellings of the projection**: `ParentEdge.reads` → the backend's `children` → `_parent_fields()`
   → `page.parent_fields` → `ParentRecord.fields`. One concept, and the names are consistent enough
   to follow — but `children` in the backend is "the fields this stream's children read", not the
   children; `read_by_children` says it.
6. **`no_parents` yields from a cast empty tuple** to be an async generator; `return` then `yield`
   is the idiom.

Accepted as they stand, with the reason: the leading-placeholder rule reads the template's shape
rather than a declaration, and it cannot be wrong for a valid REST address — a leading value that is
not a URL is a prefix of one, which no provider publishes; the fan-out tally is split across
`TreeFanOut` (`enumerated`/`admitted`, known only at fan-out) and `PartitionWalk` (`fetched`/`landed`,
known only at the walk) and joined by a callback `fanned_out` hides — the information lives in two
places, so the seam does too; `_PassMarks` keeps its three markers as reserved keys in the cursor
map rather than columns, because the map is the one durable object the walk owns, and
`Partition.key`'s newline makes collision impossible; `StreamSpec` at sixteen fields is the cost of
a declarative catalog, and every field is a connector constant.

## What can be seen, and what cannot yet

Everything below is a structured log record in the driver's `source_sync.*` family — Datadog ingests
the family already, and a log field costs no terraform where a metric would.

| record | when | carries | what it makes visible |
|---|---|---|---|
| `source_sync.fan_out` (1h) | per edge, per completed pass | `stream parent edge enumerated admitted fetched landed` | the audit's wrong-path class (`fetched > 0 ∧ landed = 0`), a predicate on a field the parent kind never carries (`enumerated > 0 ∧ admitted = 0`), and `refan` narrowing (`enumerated 3, fetched 1`) |
| `source_sync.collection_refused` (2C) | per refused asset type, hubspot `campaign_assets` | `connector stream campaign collection http_status` | a 26-way fan-out that used to `continue` past every 403/404 |
| `source_sync.failed` (pre-existing) | a run that raised | `provider_fault`, the row | every hard failure the tree added — a path field the parent lacks, a `carry` collision, mixed-width watermarks, a malformed pass marker, a child handed no reader — raises into it, so none is quiet |
| `source_sync.pass` (**1k**, with the unit-2/3 owner — every field it emits is produced by #3574's brake, so it lands in the same change, both ends) | once per run of a tree stream | `stream outcome∈{completed,truncated,held} spent budget watched resume_from enumerated` | a row whose budget is smaller than its partition count never completes a pass and so never emits `fan_out` — `outcome=truncated` on every run is that row; watched reads and held passes become countable |

**Not yet, and why:** GitHub's GraphQL answers carry `rateLimit { cost remaining resetAt }` on every
query and the connector does not read them at runtime — the budget arithmetic the RFC states has no
live signal; it lands with the unit-2 owner's round 5 as a per-query record. No Datadog monitor,
dashboard or log facet exists for any field above; the three monitors that fall out are
`fan_out` with `fetched > 0 ∧ landed = 0` sustained on a canonical stream, `pass` with
`outcome = truncated` on every run of a row, and GraphQL `remaining` under the next pass's projected
cost — and each needs a week of records before its threshold is a measurement rather than a guess,
which is why they are not written now.

## What the tree does not cover

Two placeholder kinds look like parents and are not. Excluding them is what keeps the mechanism
from becoming a general string-substitution feature.

| kind | examples | already handled by |
|---|---|---|
| **tenant identity** | jira/confluence `{cloud_id}`, googleads `{customer_id}`, facebook_ads/instagram `{account_id}` | the connection's `base_url` and config; the grant store's per-provider tenant rule |
| **the stream's own name** | salesforce `{sobject}`, zendesk `{slug}`, googledrive/asana/klaviyo/square/wrike/xero `{stream.source_object}` | the stream's own path template |

A `parent` that covered either would be expressing two unrelated things.

Nine connectors have only these and gain nothing: asana, confluence, klaviyo, recruitee,
salesforce, square, wrike, xero, facebook_ads. Sixteen more have no templated paths at all — flat
top-level collections, depth 1, `parent=None`, untouched. **The tree is free where it does not
help.**

## The watch path, end to end

Member says "watch PR 3122" in a shared conversation.

1. **Agent call.** `object_apply source_trigger` with the spec that already exists
   (`SourceTriggerSpec`, tools.py:179) — `connection`, `resource`, `streams`, `delivery`. Unchanged.
   The only difference: a resource now *drives* syncing of everything under it rather than filtering
   what the catalog already synced.

   ```yaml
   connection: github-acme
   resource: https://github.com/acme/ufo/pull/3122
   ```

   No `streams`. The resource names one `pull_requests` partition.

2. **Resolution.** `resource_url` (github.py:659) reads owner/repo/number. That pins one partition
   of `pull_requests` by (repository, number) and marks it watched; the page it lands is
   `pull_requests/acme/ufo/<databaseId>` — the stream's key is GitHub's numeric id, and GraphQL's
   `databaseId` is that same number (proven live: PR 3560's `databaseId` and REST `id` are both
   `4515114744`), so the watched fetch and the catalog fetch land one page. The repository's landed
   spelling is used, not the URL's — `resource_url` lower-cases it, and a watch on `Acme/Repo1` would
   otherwise have filed a second page (caught by a mutation check). No parent page need have landed.

3. **One request.** At `SOURCE_SYNC_INTERVAL_SECONDS` = 60:

   ```
   POST /graphql   { repository(owner:"acme", name:"ufo") { pullRequest(number: 3122) { … } } }
   ```

   Measured live against `metalcraftai/ufo` (3,560 PRs): the single-PR query costs **1 point**
   (14–22 KB, 1.7 s); the catalog's paged query at `first: 25` costs **14 points** (263 KB, 4.3 s);
   `first: 100` answers HTTP 504. And why REST could not do this: PR 3560's `/timeline` returns 19
   entries across `commented, committed, cross-referenced, head_ref_force_pushed, reviewed` — zero
   check events — while its head commit carries **44 check runs**.

   The paged cost is the deploy gate. A `newest_first` stream still reads one page per repository per
   tick to see `updatedAt` ≤ watermark. At 14 points a 20-repository connection spent ~16,800 points
   an hour against GraphQL's 5,000. Trimming the nested limits uniformly — `contexts(first: 30)`,
   `reviews(last: 5)`, `reviewThreads(first: 10)`, `files(first: 20)`, `timelineItems(last: 10)`,
   page size 20, one shared field set so a PR's body cannot depend on whether it is watched —
   measured **3 points a page** (the single read stays 1). That buys 4.7× and does not close the
   gap: `3 × repositories × 60` is 3,600 an hour at 20 repositories and spends the whole budget past
   ~27, taking every watched PR's 1-point read with it. `pull_requests` over GraphQL is not deployable
   at a 60 s catalog tick; unit 3's pass interval is what makes it so, and the connector's docstring
   says exactly that with the numbers. Neither endpoint takes a time filter, so each
   tick re-reads and the driver's digest-skip absorbs the repeats — the shape `issue_events`
   already has.

   The totals beside every clipped list and the rollup's counts by state add no points — paged 3,
   single 1, re-measured 2026-09-13 — and a watched pull request past the clips
   spends 1 more point per clipped connection paged to its end: 3560 (44 checks, 16 threads, 116
   files) is 4 a tick. Declared the same day: `fetch_budget=12` on each of the six canonical REST
   streams under a repository — GitHub's REST pool is 5,000 requests an hour, 83 a minute, apart
   from GraphQL's points (`/rate_limit` reports `core` and `graphql` separately), and a quiet stream
   spends one request per repository per tick, so `6 × 12` = 72 a minute beside the catalog rows'
   own reads — and 20 on `pull_requests`: 20 trimmed pages spend 60 of GraphQL's 83 points a minute
   and leave 23 for the 1-point watched reads that go first. A stream that registers no row carries
   none.

4. **Landing.** The PR is one page, `pull_requests/acme/ufo/3122`, re-rendered each tick; a new
   revision only where the digest moved. **A tick that finds nothing new emits no `PageChange` and
   therefore no notification.** A change wakes the trigger with one ref, and the agent reads the
   whole current state — checks, reviews, threads, files — from one object rather than reassembling
   it from five streams.

5. **Notification.** `on_page_change` (tools.py:427) filters in order: group by connection →
   `triggers.waking(feed.id)` → `subject == SHARED_SUBJECT` → `readable_source_ids(agent)` (**the
   authorization gate**) → `_about_resource` → `trigger.streams`. Then `_fire_trigger` writes the
   change log and calls `ext.invoke(..., standalone=True, holds_work_already_done=True,
   fired_by=FiredBy(kind="source_trigger", …))` under idempotency key
   `source-trigger:<connection>/<resource digest>:<conv>:<latest>`. The inbound leads with the
   object, not the streams: `github: Add retry to egress dial — 2 review comments, 1 review, CI run
   failed` — `_headline` already counts per stream; the resource's page title heads it.

6. **End.** The trigger row is deleted; the next completed pass of the `timeline` row enumerates
   zero partitions and `PartitionWalk.stream`'s existing prune drops the cursor entry. Zero REST
   calls after that, with no lifecycle code written for it. **Landed pages stay recallable** —
   they are content the member asked to be told about, and dropping them would erase the record of
   what they were told.

## Units

Each lands both ends with its proof.

| unit | content | proof |
|---|---|---|
| 1 | `parent` + `path` on `StreamSpec`; parent-ref partitions in `PartitionWalk`; ancestor closure; github converted, `_repo_partitions` deleted — **#3560, 70 → 25 requests per tick** | the redundant org/repo walk is gone — measured request count per tick |
| 1b | `key_scope` on `StreamSpec`; `_page` scopes the identity only for `local` — **#3560 `814cad684`** | a flat-keyed canonical child converts with identity byte-identical to main; a `local` one lands two parents' record `1` as two pages |
| 1c | `where`/`unless` on `ParentEdge`; predicate fields projected — **#3560 `9e21b3b09`**; two maps because an exclusion is open-ended (every block type Notion has not shipped yet) | a bulk-only coupon edge issues one request; notion leaf blocks cost zero requests on the pass after they land |
| 1e | `carry` on `ParentEdge`: parent fields written onto the child record; projected beside path and predicate fields — **#3560 `3f7996dea`**; the collision refusal is at the walk, since a declaration cannot know a provider record's keys |
| 1f | **#3560 `524294431`** — refuse a partition whose all-digit watermarks are not one width, because the walk orders every watermark as text and `"1000" < "999"`; checked together at the top of each ordered page, so a tripped run commits nothing | `"999"`→`"1000"` raises naming both; `"0999"`/`"1000"` and ISO walks checkpoint unchanged; mutation: the walk resumes from `"999"` |
| 1h | **#3560 `eb67a6eac`** — one `source_sync.fan_out` log record per edge on a completed pass: `enumerated`, `admitted`, `fetched`, `landed`; `TreeFanOut` replaces the free `tree_partitions` and tallies as it yields; a capped run emits nothing | 3 coupons / `where` admits 1 / 2 records → `3·1·1·2`; a `where` on an absent field → `3·0·0·0`; an envelope that drops every record → `3·3·3·0`; mutation: all three assert `[] == [{…}]` |
| — | **#3560 `7a4e9c54b`, merge of main.** Three seam pushes had run **no CI**: main's #3568 landed on `github.py`, the PR went `DIRTY`, and GitHub runs no `pull_request` workflow on an unmergeable PR — the checks list showed the last green run and looked verified. Merging surfaced a defect git had auto-merged cleanly: #3568's `_retire` removed every row whose stream is not canonical, which under the ancestor closure deletes `organizations` each tick and cascades the repository catalog away, leaving every child with zero partitions. `_retire` now keeps a row whose stream is in `syncing_streams`, with a test | verify a stacked branch by `mergeStateStatus` and by checks on the *current* head, never by the checks list alone |
| 1g | **#3560 `c2866b450`** — a value substituted into a path is percent-encoded as one segment (`quote(value, safe="")`) unless its placeholder begins the path, where it is an address the provider rendered and rides as it came; a query value is `quote_plus`, the inverse of the `parse_qsl` 1d decodes with. The scope stays raw — identities carry raw values on main. github's repo paths read `{owner.login}/{name}`, since `{full_name}` is two segments; the scope is still `acme/repo1`, so no page moves | `a b/c#d?e@f` reaches the wire as `a%20b%2Fc%23d%3Fe%40f` and lands identity `…/a b/c#d?e@f/r0`; mutation: `#` truncates the request at a fragment | slack `messages` body byte-identical to main; stripe line items regain their timestamps; hubspot `consent_states` converts |
| 1l | **#3560 `a40c9e953`** — `PartitionSkipped` on a `delete_missing` stream is a run failure, not a skip (measured first: the adapter returned an authoritative snapshot holding only the unrefused contract's page, so `_commit` would have tombstoned the other's) — the walk passes a skipped partition over and the pass still completes, so on a snapshot stream the driver would tombstone every page of a partition the provider merely refused. Wave 2D found it converting deel `tasks` and wrote its factory to raise nothing; the seam refuses it for the next author | a snapshot fixture whose factory skips one of two partitions raises and lands nothing; the same fixture without `delete_missing` passes the partition over as today |
| 1d | **#3560 `0573eb4d8`** — cursor entry keyed by ref *and* path (path alone collapses slack, whose channels share one endpoint and differ by a query param); an edge's `?query` merged with pagination params in the seam; `ParentEdge(optional=True)` | two edges under one parent both resume after a cap; `/balance_transactions?payout={id}` keeps its filter at the wire; an owner with no `userId` is skipped, not a fault |
| 4–5 | 27 connectors converted in six batches — **landed as #3565 #3566 #3567 #3569 #3570 #3571**: 60+ edges, every hand-rolled descent deleted, no request count rose; 17 canonical children stopped on identity (table below). **These units convert the catalog; they do not put it in service.** A child that is neither canonical nor an ancestor of one registers no row until a watch names its parent, and the watch path (idea 2) shipped for github alone — so ~100 of the declared children, freshdesk's four-level chain and notion's self-edge among them, are declarations with tests and no traffic until idea 2 generalises. That is the design, said out loud | each connector's parity pinned against main; each non-canonical conversion asserts the stream never landed |
| 1i | **#3560 `1fbf02134`.** The holistic review's finding. Merged, the six conversions grew 24 provider files by **+2,632 / −2,252 = net +380 lines; 17 of 24 got longer** — while every descent the RFC named was in fact deleted. ~410 lines are seam ceremony each agent re-invented because the seam made them: 22 byte-identical `paginate_source` overrides (272 lines) because `rest.py` dropped `parents` by default; 21 dead `if parents is None: raise` guards — `backend.py:262` raises before `fetch_page` is ever called, and two of the guards were `StreamSkipped`, the silent class; 13 `isinstance(walk, AsyncGenerator)` guards that are always true and 7 connectors that never close the walk the driver abandons at the record cap; 24 identical `_parents` test readers. Thread `parents` by default and non-optional, own the walk driver once, one `conftest.py` | **Round 4 landed on all six PRs.** Merged onto 1i: 25 provider files (27 converted + github) go from **10,199 lines on main to 9,841 — a net deletion of 358**, from +380 before this round; every descent mechanism the audit named greps to zero (`_Hop`, `_DESCENT_PATHS`, `per_list_child`, `_repo_partitions`, `unique_coupons_parent`, `_SUBSTREAM_*`, the `isinstance` guard); 576 provider tests green on the merged tree; `indexed=False` declared on every ancestor row (bases, tables, teams, projects, solution categories and folders). Landed: `parents` threads by default and is non-optional by type (`no_parents` for a root); one `fanned_out(stream, parents, pages, cursor, floor)` driver — github's walk block went 17 lines → 2; `PartitionWalk.stream` annotated as the `AsyncGenerator` it is, so the guard's cause is gone; one `parents_reader` in `extensions/sources/tests/providers/conftest.py`; `TreeFanOut` left the SDK, and `gates.py` forbids `ufo.runtime` in extensions, so partitions cannot be consumed outside the walk — open question 5 closed by type. The 22/21/13+7/24 deletions are the batch owners' on rebase |
| 2 | **#3573** — `pull_requests` GraphQL-backed with `statusCheckRollup` at `first: 25`; a watch pins one partition read first every tick with its own cursor entry; three mutation checks (watched-first order, `statusCheckRollup` present, repo spelling). The `streams` narrowing was **reverted**: a five-arm ablation ($18.37) that proved the suite sensitive (blanking the offer costs three cases) could not separate the wordings, and an unproven prompt edit does not ship | a watch on a PR URL alone lands one rich page whose revision moves when CI, a review or a thread changes; no watch → the PR syncs on the catalog's schedule; the offer names no stream; identity unchanged, so no page re-lands |
| 3 | **#3574 `d9ef675fc`**, stacked on #3573 — per-row `fetch_budget` (partitions *fetched*, watched first, ordered rotation so the tail is not starved), per-row `pass_interval_seconds` (a completed pass waits it out; a watched partition ignores it; a pass the budget cut short is not stalled by it), `ParentEdge(refan="on_parent_change")` with the parent page's `revision` riding down through `ParentPages` | budget forced off → `assert 40 == 10`; interval forced open → tick 2 walked `['/w','/q00','/q01','/q02'] == ['/w']`; revision ignored → fetched ticket 1 when only ticket 2 moved; freshdesk `conversations` and intercom `conversation_parts` back to `1 + changed` |
| 4 | freshdesk (`_DESCENT_PATHS` deleted), zendesk (`_Hop` deleted), recurly (phantom deleted), mailchimp | each connector's hand-rolled descent is gone and its fixtures still pass against the parent map's paths |
| 5 | the remaining 23 connectors, in provider batches | per-connector fixtures grounded in Airbyte's manifest, never in the connector's own assumption |

## Rejected alternatives

**Caller-supplied path + cursor.** The original proposal: let a subscription name an arbitrary API
path and cursor, synced for the life of the watch. Rejected on three counts.

1. *No gate exists for it.* Authority is per-connection. A caller-named path reads anything the
   token reaches — `/user/emails`, `/orgs/{org}/members`. The only gate that could check it is an
   allowlist, which is the connector's stream table with extra steps.
2. *It industrialises the silent-drop class.* A caller guessing `record_path`, `primary_key`,
   `cursor_field`, `ordering` and pagination reproduces the eight-finding audit on demand, and the
   failure is invisible. That audit's own conclusion was never to ground a path in the connector's
   assumption; a model's guess is strictly weaker.
3. *The cursor is the engine's resume invariant.* `PartitionWalk._decode` already raises on a
   malformed entry because it is "corruption the walk alone could have written."

**A `StreamScope` enum separating resource-scoped streams from catalog streams.** Rejected: it
needs its own registration path, its own lifecycle, and a workaround for
`SOURCE_EMPTY_IDLE_THRESHOLD` idling a row registered before anything watched it. All three are
scaffolding around a list that should have been a tree.

**Revision-gated re-fan** — skip a parent whose page revision has not moved. This was idea 3's first
form and it is unsound: a provider does not reliably bump a parent record when a child changes
(GitHub moves a repository's `pushed_at` on a push, not on an issue comment), and an
`Ordering.none` child has no cursor, so a full re-walk is the only way it notices change at all.
Gating it on a static parent freezes the stream. It survives only as a later optimization for
providers that genuinely do bump the parent, never as the bound.

**A `parent_fields` mapping beside the path.** Rejected: a second place to look, and it can drift
from the path it serves. Dotted placeholders name the fields directly, so a wrong field raises.

## Open questions

1. ~~**What sets the budget and the interval?**~~ — **decided**: the budget is one seam default
   (`DEFAULT_FETCH_BUDGET`, 100) overridden by a per-stream declaration only where a provider's rate
   limit demands it, since 30 connectors would otherwise each invent a number; the interval stays a
   per-stream declaration with no default, because a completed pass that waits is a provider fact
   (a pull request's checks settle in minutes) and most streams have none to state.
2. **The alert's headline.** A watched PR's wake should say what moved (`checks: pending →
   failure; 1 review`), but no prior body exists to diff against — `page` stores one revision, the
   change log holds no bodies — and `evals/harness/source_wake.py` stages `body=""`, so no eval can
   see a headline change. It is model-read text, so it stays as it is until the harness stages a
   real body and an arm measures the wording. Unit 2 shipped such a headline, was sent back, and
   reverted it with its producer (`resource_state` on the rules) — `tools.py` and `resources.py` are
   byte-identical to the seam. The follow-up is spelled: `evals/harness/source_wake.py::woken_inbound`
   takes a `body` carrying a rendered `pull_requests` page, and an arm restoring the per-stream-count
   headline measures the change. (The `streams` offer wording was ablated and found not load-bearing;
   the `<watch_offer>` manifest already named no stream.)
3. **An edge that admits nothing** — in build as unit 1h: one structured log event per edge on a
   completed pass carrying `enumerated`, `admitted`, `fetched`, `landed`. No row column, no gate, no
   threshold yet — `enumerated > 0 ∧ admitted = 0` on a `where` edge is the absent-field trap,
   `fetched > 0 ∧ landed = 0` across a pass is the audit's wrong-path class, and what "zero" means per
   connector is a decision for a week of real data, not a constant guessed now.
4. ~~Watermarks compare as strings~~ — **closed by 1f**: the gate, not normalisation (a value read one
   way and ordered another is two answers to what it is), enforced once at the top of each ordered
   page.
5. ~~`carry` is applied by `PartitionWalk`, not by `tree_partitions`~~ — **closed by 1i**: `TreeFanOut` is not
   in the SDK and extensions cannot import `ufo.runtime`, so a connector has no way to read partitions
   except through `PartitionWalk`. Answered in code, not review.
6. **Two answers to "how do we see CI" — both landed on 2026-09-12.** Main's #3568 added REST
   `check_runs` and `commit_statuses` under each open PR's head ref, and `_with_mergeability`: one
   `GET /pulls/{n}` per open pull request per pass at concurrency 8, merging `mergeable`/
   `mergeable_state` into the PR page. #3573's GraphQL `pull_requests` carries `mergeable`,
   `reviewDecision` and the whole `statusCheckRollup` in its 3-point page — the same facts with no
   per-PR read. `_with_mergeability` is therefore superseded outright and **went** when #3573 rebased onto the
   merge: deleted with `_MERGEABILITY_CONCURRENCY`; the fact it recorded is kept in GraphQL's own
   vocabulary — `mergeable` is `MERGEABLE`/`CONFLICTING`/`UNKNOWN`, and `UNKNOWN` (REST's `null`,
   "still being calculated") is dropped from the page for the reason #3568 dropped the null: storing
   it turns one background recompute into two page changes. Both of #3568's digest tests are ported
   to the GraphQL body, not deleted; a new test lands the rollup page and the per-run pages in one
   tick and proves neither addresses the other's page.
   `check_runs`/`commit_statuses` are a different thing — one page per run, history and recall —
   and stay if per-run pages are wanted; they are depth-3 (under a commit ref under a PR head), and
   the merge kept #3568's nested walk as a `_REF_COLLECTIONS` table beside the declarations so no
   page moves. Converting them under the tree needs the PR record to carry its repository, which
   `flatten` drops — a body change to every PR page — so it is the github owner's decision, not a
   merge's. **Decided: keep both, and convert `check_runs`/`commit_statuses` under the tree.** The
   rollup answers what a PR's state is now; a page per run answers which checks failed last week
   across repositories — two questions, so two answers, and `_with_mergeability` was the one
   duplicate. The conversion carries the repository onto the PR record and takes one revision of
   every PR page (~2,600 on testing), bounded and once, against a hand-rolled table living in the
   reference connector for good. **Measured before building: byte parity is impossible for a faithful
   conversion** — the check-runs collection is addressable only through a commit ref, so a truthful
   edge path reads the ref and the ref enters the scope; the only parity-preserving form declares one
   path and requests another, which breaks the edge's own contract and was refused. Decided:
   `check_runs` converts faithfully as `key_scope="global"` (a check-run id is globally unique;
   `check_runs/<id>`), the restamp accepted while the stream is a day old and its rows are the fewest
   they will ever be; `commit_statuses` **retires** — its content is the rollup's `StatusContext`, and
   as per-run pages it duplicated `check_runs` under a key a fork's shared SHAs collide on. #3568's own
   `_retire` takes its rows. **Amended 2026-09-13 by Alex's call on main (#3580, commit 1):
   `check_runs` is `canonical=False`** — declared, syncable on request, in no connection by default;
   the watched PR page's rollup carries every check and the catalog's page the counts of all of them,
   and a page per run was noise. The refetch
   migration that re-walked every `pull_requests` row so `check_runs` could enumerate pre-landed PRs
   goes with it: a stream nobody syncs by default earns no fleet-wide re-walk on deploy. The retire
   of `commit_statuses` stands against #3580's premise that it "tells us when AI review is complete":
   the `ufo review` verdict is a `StatusContext` in `statusCheckRollup`, on the watched page, at no
   extra read; nothing outside `github.py` names the stream. #3580's second commit — a wall-clock
   rotating window (`_REF_FAN_OUT_TICK=10`, 6 heads a pass) over the per-head REST walk — is
   superseded, not fixed: the gate found it aliases at the 60 s cadence (10 s buckets step by 6, so
   half the slices are never read) and stops at 60 heads, and this change deletes the walk it caps;
   `fetch_budget` and `pass_interval_seconds` are the same bound, cursor-marked, for every stream.
7. **`_REF_COLLECTIONS` puts a hand-rolled descent back in the reference connector.** github.py is
   the conversion every other connector copied; it now carries one table the tree does not express.
   Holistic-review item, with the conversion in 6 as its resolution.
6. ~~An edge path substitutes parent values raw~~ — **closed by 1g**. One consequence for every
   conversion: a placeholder is one segment, so a compound field (`{full_name}`) is spelled as its
   parts (`{owner.login}/{name}`) and a provider-rendered address (`{url}`, `{jobs_url}`, calendly's
   `{uri}`) leads the template. The scope is the values joined by `/`, so a re-spelling that reads the
   same values leaves every identity where it was. The parent map carried calendly as
   `/scheduled_events/{uri}/invitees`, which 1g renders as `/scheduled_events/https%3A%2F%2F…/invitees`
   — wave 2F measured both spellings against one record, and the map row now reads `{uri}/invitees`.
   A URL-valued field is never a mid-path placeholder.
8. **Grant granularity stays per-connection.** Decided as stated: a watch on a resource does not
   narrow authority; the connection grant is the gate.

**One PR, decided by Alex — phase 1 landed at `0d8f11dad`:** six merges onto the seam, the one conflict
the single-head guard on `versions/HEAD` (resolved as the brief authorised), chain `20260912192000 →
20260913015909 → 20260913020953`, `gates.py` passing, 751 + 305 tests green run separately, both
collection orders passing, **25 provider files 10,010 → 9,506 on main, −504** (hubspot −170, mailchimp
−129, github −113; clickup +38 for five levels of declaration where it had no descent to delete). The
stack combines into #3560, which already targets main and carries the
history and CI. Phase 1 merges the six connector branches into it (2A before 2B, and 2B's migration
repoints inside that merge: `20260913020953` → `down_revision = 20260913015909`, `versions/HEAD`
rewritten, stamp kept — the one hand edit of the combine); phase 2 merges #3573 and #3574 when their
owner's round 5 is done. The six and the two close as folded. #3561, this document, stays its own PR.
The merge of #3560 into main is the one act the repo reserves for a person.

## Verified parent map

`0051-parent-map.json`, beside this file, is the ground truth the conversion runs on: one row per
child stream, each backed by a primary source read at a named line or a vendor page fetched for the
purpose. No row is grounded in the connector's own assumption.

```json
{"connector", "stream", "parent", "path", "record_path", "canonical",
 "source": "airbyte:source-freshdesk/manifest.yaml:558" | "vendor:<url>",
 "agrees_with_us": true | false, "note": "<only where they disagree>"}
```

`path` is relative to the connector's base URL, each placeholder spelled as the dotted parent-record
field that fills it. `record_path` is the envelope key; `""` means the body is the array. A stream
reached two ways gets one row per edge (clickup `lists`, notion `blocks`). Tenant placeholders
(`{cloud_id}`, calendly's `{organization}`, stripe's config `account_id`) are excluded, and each
exclusion is named in the note of a row that touches it.

### Coverage

135 rows over 28 connectors. 103 cite an Airbyte manifest or `streams.py` line, 32 a vendor
reference fetched for the purpose. Every row is verified: no row carries `agrees_with_us: null`.
127 agree with what the connector requests today; 8 do not.

| connector | rows | connector | rows | connector | rows | connector | rows |
|---|---|---|---|---|---|---|---|
| `airtable` | 2 | `attio` | 1 | `bamboohr` | 1 | `calendly` | 1 |
| `chargebee` | 4 | `clickup` | 8 | `deel` | 2 | `freshdesk` | 7 |
| `github` | 24 | `googledrive` | 3 | `googlesheets` | 2 | `greenhouse` | 9 |
| `hubspot` | 7 | `instagram` | 6 | `intercom` | 2 | `jira` | 2 |
| `mailchimp` | 8 | `mercury` | 1 | `microsoft_teams` | 3 | `monday` | 2 |
| `notion` | 3 | `pagerduty` | 1 | `recurly` | 5 | `sentry` | 4 |
| `slack` | 3 | `stripe` | 14 | `typeform` | 2 | `zendesk` | 8 |

**Two of the thirty connectors contribute no row, and that is itself the finding.** `googlemeet`
declares one stream (`meeting_artifacts`) and assembles the whole `conferenceRecords → transcripts
/ smartNotes → entries` descent inside that one record (googlemeet.py:94-117), so there is no
second stream to hang. `pandadoc` declares no child either: `documents` widens its own rows at
`/public/v1/documents/{id}/details` (pandadoc.py:114).

### Where Airbyte gave nothing

| connector | why |
|---|---|
| attio, deel, mercury | no Airbyte connector exists |
| greenhouse | Airbyte is on the Harvest **v3** bulk API (`/v3/notes?candidate_ids=…`); ours is v1 per-parent, so its manifest verifies none of our nine paths |
| sentry | four streams, no `members`; organization and project come from config, not a parent |
| googledrive | a file-based reader (`stream_reader.py`), no per-file streams |
| pagerduty | declares `incident_logs` (`/log_entries`), not `notes` |
| microsoft_teams | reaches a channel's members and tabs, not its messages |
| clickup | declares the `folder → list` edge only, not folderless lists |
| github | `releases` is GraphQL there (streams.py:852), so it carries no REST path |
| zendesk | gets identities by sideload on the users export, not by a per-user endpoint |
| hubspot | five of our seven children are absent from its catalogue |

### Findings

Canonical streams sync, so a defect in one is live rather than latent.

1. **deel `payslips` (canonical) lands nothing, and the tree cannot finish it.** We ask flat
   `/rest/payslips` (deel.py:122); the endpoint is `GET /rest/gp/workers/{id}/payslips` → `data`.
   Deel publishes **no GP-worker listing** — every `/rest/gp/workers/...` path is keyed by an id,
   and the index of stable endpoints has no `GET /rest/gp/workers`. The only candidate enumeration
   is `GET /rest/contracts?types=["global_payroll"]` → `data[].worker.id`, and no Deel page states
   that id is the one `{id}` accepts; `/rest/people` demonstrably carries two different worker
   identifiers. **A live call settles it; documents do not.** EOR payslips are the enumerable
   parallel: `/rest/eor/workers/{worker_id}/payslips` with
   `/rest/people?hiring_types=["eor"]`. (Also: our base path is `/rest/`, not the `/rest/v2/` some
   Deel doc headers show — the spec's `servers` block, all eight code samples and every endpoint
   page use `/rest/`.)
2. **slack `conversation_threads` (canonical) never fetches a reply.** All three message streams
   derive from one `conversations.history` page per channel, and `conversations.replies` appears
   nowhere in slack.py. Thread rows are reconstructed from each parent message's own `thread_ts` /
   `reply_count` (slack.py:508-538), so reply bodies are never landed. Airbyte's `threads` stream
   is the missing edge: `conversations.replies`, parent `channel_messages`, parent_key `ts`
   (manifest.yaml:390).
3. **microsoft_teams `channel_messages` (canonical) lands no replies either.** Graph's
   `GET /teams/{team-id}/channels/{channel-id}/messages` returns messages "without the replies";
   replies need `$expand=replies` or the `/replies` child, and we send neither
   (microsoft_teams.py:93).
4. **mercury `transactions` (canonical) fans out where it need not, and pays for it.**
   `/api/v1/account/{id}/transactions` is correct, but `GET /api/v1/transactions` lists every
   account's with real cursor pagination (`page.nextPage`), where the per-account response carries
   only a `total` count.
5. **greenhouse `activity_feed` would land zero rows on every run** — latent, since it is not
   canonical and so never runs today. The Harvest v1 body is
   `{notes: [...], emails: [...], activities: [...]}` — three sibling arrays. `_paginate_per_parent`
   reads it through `_paginate_link_header`, which takes the body as the record list
   (`_response_list`, rest.py:178-181) and so returns `[]` for a dict (rest.py:63-67). This is the silent
   class exactly, and no single `record_path` can express three sibling arrays: the stream splits
   or the extractor unions.
6. **freshdesk `discussion_topics` and `discussion_comments` ask flat parents that do not exist.**
   Confirmed as stated above: `_special_pages` (freshdesk.py:201-208) walks
   `/api/v2/discussions/forums` and `/api/v2/discussions/topics`; Freshdesk publishes both only
   under a parent. Latent while both are non-canonical.
7. **hubspot's dated path segments are a rollout behind in four streams.** `campaign_assets`,
   `sequences`, `sequence_enrollments` and `consent_states` request `…/2026-03/…`; the fetched
   references document each at its `v3`/`v4` spelling and at `…/2026-09/…`, and name no 2026-03
   rollout of those APIs. `list_memberships` is the exception — HubSpot's lists rollout does ship
   2026-03, so that one agrees. Two record-shape faults ride along: `sequence_enrollments` returns
   a single object, not a `results` array, so only our whole-body fallback lands it
   (hubspot.py:2084-2102); and `consent_states` omits the `channel=EMAIL` query the v4 endpoint
   requires.
8. **clickup `goals` drops every goal held in a folder.** ClickUp's own 200 schema marks both
   `goals` and `folders` required; we read `goals` alone (clickup.py:218-220).
9. **chargebee `subscription_with_scheduled_changes` never lifts its envelope.** The body is
   `{"subscription": {…}}`; `flatten` (chargebee.py:148-160) lifts by `stream.source_object`, which
   defaults to the stream name, so the raw envelope ships as the record.
10. **zendesk `users_identities` is an N+1 where a sideload exists.** Our per-user
    `/api/v2/users/{id}/identities.json` → `identities` is correct, but Airbyte lands the same rows
    inside the users export with `?include=identities` (manifest.yaml:1425,1432) — one request
    instead of one per user, at the cost of partial user records.
11. **jira `issue_comments` re-walks every issue every run.** Its parent walk is called with
    `cursor=None` (jira.py:161), so the issues enumeration ignores its own incremental JQL filter
    and the comment cursor is applied client-side afterwards.
12. **deel `forms` asks an endpoint Deel does not publish.** There is no `GET /rest/forms`; the
    forms family is `POST /rest/forms/eor/create-contract/definitions` (contract-creation form
    *definitions*, body-driven, ten countries per request) plus two worker-scoped reads. It is
    schema metadata, not a record collection.

**Two known findings, re-checked.** deel `payslips` is confirmed and sharpened above. zendesk
`article_comments` is **corrected: it is already fixed.** PR #3181 (65ad42582, "reach zendesk's
help centre rows under the parent that holds them") landed `_CHILD_COLLECTIONS` and
`_walk_children`, so today it walks `help_center/articles` then `{id}/comments.json` and matches
Airbyte line for line. The flat `help_center/article_comments.json` is gone. *Where the tree does
not cover* and the unit-4 proof line above both still describe the pre-#3181 state.

### Corrections to this RFC's own table

The counted child totals were taken from the declared streams by hand; reading each connector
against its reference moves eleven of them.

| connector | counted | verified | why |
|---|---|---|---|
| stripe | 10 | 14 | nine path fan-outs, three query-param ones (`subscription_items`, `payout_balance_transactions`, `setup_attempts`), and the two `external_accounts` branches |
| hubspot | 5 | 7 | the `deal_contacts`-style junctions are `?associations=` sideloads over constant object types, not per-parent fan-outs; the real children are campaign_assets, list_memberships, consent_states, sequences, sequence_enrollments, form_submissions, conversation_messages |
| zendesk | 7 | 8 | `users_identities` also fans per user |
| googledrive | 1, recursive | 3, flat | `files` is one `q=trashed = false` listing across all drives (googledrive.py:126-146) — no folder recursion anywhere; the children are `permissions`, `comments`, `revisions` |
| sentry | 5 | 4 | `projects` is a flat `/projects/` walk (sentry.py:165-169), not `organizations/{slug}/projects/` |
| notion | 1 | 3 | `comments` hangs under `pages` via `?block_id=`, and `blocks` carries both its edges |
| googlesheets | 1 | 2 | `sheets` (from the spreadsheet record) and `sheet_values` (per tab) |
| attio | 2 | 1 | `notes` and `tasks` take their parent as an **optional** query filter, so they stay flat; only `call_recordings` fans |
| monday | 2 | 2 | the count holds, the composition does not: the second child is `activity_logs`, not `updates`, which is a root query (monday.py:298-305) |
| googlemeet | 2 | 0 | one declared stream; the descent is intra-record |
| pandadoc | 1 | 0 | `documents` hydrates itself |

Depth 5 holds (clickup `team → space → folder → list → task`), and so does the two-edge case:
clickup `lists` hangs under both `folders` and `spaces`, notion `blocks` under both `pages` and
itself.

## Rollout coexistence — decided 2026-09-13 on Alex's review of #3560

The jobs fleet rolls while old and new pods share `source` and `page`; the migrate Job runs first.
Every write the new image makes must be one the outgoing image never reads.

| concern | shape |
|---|---|
| a converted child's cursor is a map; the outgoing image reads `source.cursor` as a plain watermark (Intercom sends `updated_at > "{}"`) | a stream with `parents` keeps its map in a new nullable `source.partition_cursor`; `cursor` is untouched for it. Every other stream, Slack included, keeps `cursor`. The new image starts tree streams from an empty map — one re-walk from each row's floor, accepted |
| `Partition.key` moved from the bare partition id to `ref\npath`, orphaning every stored Slack and GitHub watermark | `key` is `ref` for a partition the connector enumerated itself (GitHub's pinned pull request is the one in production) and `ref\npath` under an edge; every tree row — Slack `messages` fans out over `conversations` here — keeps its map in `partition_cursor` and re-walks once from its floor |
| the Teams and Mailchimp identity reaps delete rows the outgoing image can re-land during the roll, leaving live residue nothing addresses | the two reap migrations (`20260913015909`, `20260913020953`) leave this change and ship in the release after it, once no outgoing pod exists; until then the new and old identities coexist as duplicates for one release, and deel `tasks` needs none — its snapshot sweeps the old identities on every pass |
| a budgeted or held pass on a `delete_missing` child returns `snapshot=True` over the partitions it did not visit | `StreamSpec` refuses `delete_missing` with `fetch_budget`, `pass_interval_seconds` or a non-default `refan`; snapshot fan-outs stay whole and unbounded |
| a snapshot child runs before its parent has produced an empty or non-empty catalog | `source.synced_at` marks a complete run; existing page rows also prove readiness. Until then the parent reader yields `UnreadyParent`, and the snapshot child fails without committing or sweeping |
| no production stream declared `fetch_budget`, so every tree child was unbounded | the seam bounds every non-snapshot tree child at `DEFAULT_FETCH_BUDGET` partitions per run; a provider declares its own where its rate limit says so; `refan="on_parent_change"` is declared where the provider bumps the parent (Intercom, Freshdesk, …) |
| budget resume relied on database order and an exact marker | `PartitionWalk` orders every enumerated partition by key; resume skips while `key <= marker`, so a deleted marker resumes at the next key |
| the watched PR page clips checks, threads and files with no count, so activity past the clip never moves the digest | both reads carry every count (`totalCommentsCount`, `reviewThreads.totalCount`, `files.totalCount`, rollup counts by state, `mergeStateStatus`); the watched read paginates its nested connections to completion |
