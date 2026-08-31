---
rfc: 0019
title: "Shared brain — scope and audience"
status: proposed
date: 2026-07-26
---

# Shared brain — scope and audience

> A workspace is many principals — members, agents, automations — learning at once. Everything
> shipping today picks a pole: **partition and never unify** (nothing anyone learns compounds) or
> **append and let ranking arbitrate** (every mistake is instantly everyone's). The old shape in
> between — branch, then merge under review — is git's, and git is honest about its limit: it
> merges structure and hands meaning to a human; the field, verified vendor by vendor, ships fork
> and stops short of merge (arXiv:2603.10062 names multi-agent memory consistency the most
> pressing open challenge). Enterprise search solved the same problem for *documents* without
> merging anything: one index, the source's ACL enforced per reader at query time. What stopped
> that shape at documents is that a derived fact has no ACL — until provenance is typed. A fact
> with recorded sources has a computable audience: **the intersection of its sources'
> audiences**. ufo already writes at the addresses — `(workspace, agent, subject)` (#645) —
> **this RFC is the audience function**: knowledge compounds in one brain, sharing is automatic
> to exactly the audience a fact's sources permit, a reader is never served a fact whose sources
> they could not open, and nothing is ever copied, reconciled, or merged.
>
> Two organs, one per question no model capability answers: **scope** (where knowledge
> accumulates) and **audience** (who may read a derived fact — computed at read, never declared,
> never an act). Widening is not machinery: a member states the fact where the wider audience
> lives, the statement is a new source at that audience, and derivation does the rest. Core owns
> each organ's enforcement point — the address, the root's audience; the mechanics stay
> extension-owned. Extraction, ranking, and consolidation are guesses about how this year's
> models retrieve, and deleting one costs nothing.

## Current state

Three fragments of one idea, none composing with the others.

| Fragment | Has | Lacks | Where |
|---|---|---|---|
| memory ext | 3-leg recall (lexical + vector + unembedded-tail), RRF/cosine blend, per-kind decay, hourly consolidation, `memory_100` evals | write-time classify (identity is `uuid5` over exact body bytes — a reworded duplicate is a new row), typed provenance (`source_ref` is a nullable free-text column **the model itself writes**, `manifest.py:139-141`, and consolidation nulls it, `condenser.py:276`), forget path, index cleanup of superseded rows, durable refs in results | `extensions/memory/ufo_ext_memory/store.py:396-434` |
| knowledge_graph ext | 2-tier extraction, typed edges with page provenance, stub-on-reference | entity resolution — `entity_type` sits inside the `uuid5` identity (`store.py:433-436`), so an org chart in prose mints `topic` nodes a *person* lookup can never find; a working seed (`context_for` substring-scans the newest 500 entities); any eval | `extensions/knowledge_graph/ufo_ext_knowledge_graph/store.py:425-436,557-574` |
| objects (RFC 0017) | one address `(kind, name)`, five verbs, per-kind typed storage, `MemberOwnedObjects` | typed links between a memory and its page; durable refs in search results (#613) | `core/src/ufo/runtime/objects.py:505-565` |

Two gaps are worse than they read. **Recall silently shrinks** — consolidation supersedes rows
but never prunes their chunks (the only index delete in the repo is page-scoped,
`store.py:736`), so dead chunks keep winning result slots the read-back filter then drops. **The
knowledge surface is three tools that don't compose** — `memory_search`, `graph_search`, and the
object verbs; the graph tool can't see what the org chart created, search drops durable refs
(#613), and nothing links a memory to its page.

And one gap is invisible until you look for it: **`source.subject` is a binary** (`shared` or
`member:X`, `tables.py:374-385`), so a whole-drive sync registered as `shared` claims every
member as its readership regardless of what the upstream system's folder ACLs say. Today that
mis-scopes retrieval; under an audience function computed from roots it would mis-scope every
fact derived from those pages. §2 names it and closes it.

**In flight, assumed:** **#645** — agent as the operational security boundary; everything scoped
`(workspace, agent, subject)`; a new agent starts empty; copying is explicit. **#613** — typed
`links` on `object_get`; `memory_search` returns `ObjectRef`s; "search finds, `object_get`
opens."

**Adjacent, not here:** RFC 0016 owns the self-improvement loop, its gate, and the governance
spine that gives a behavior change an approver and an undo. This RFC gates no act and records no
decision — knowledge flows by computation, so it needs neither.

## Prior art

Full sourcing in PR #684's research record; the live engagements are in §Alternatives.

- **Nobody merges knowledge semantically.** Production conflict handling is compare-and-swap
  prevention, last-write-wins with documented lost updates, regenerate-and-adopt (Dreams,
  Dreaming — a new store from an immutable input, adopted wholesale), temporal invalidation that
  keeps both sides (Graphiti), and partition-as-refusal. The one literal three-way merge is
  Letta's git worktrees — textual, own-subagents only.
- **Query-time ACL enforcement is proven — on documents only.** Glean enforces the source's
  permissions per reader, per search, and never touches derived memory, because a derived item
  has no ACL to enforce. Typed provenance is the missing half: it *is* the derived fact's ACL,
  and it makes the audience computable at read. That is the whole bet of this RFC — an existing,
  validated enterprise-search mechanism extended one layer down, to facts.
- **ActiveGraph** (arXiv:2605.21997) is the nearest substrate prior art. Its blackboard —
  behaviors reacting to a shared graph — ufo already has (objects, typed links, `page_change`
  events, automations), and its `promote` is adoption, not merge: "no semantic merge; identical
  concurrent edits still conflict." Here that boundary is dissolved rather than solved — with no
  copies there is nothing to reconcile (§Alternatives, *cross by copying*). Not adoptable as a
  runtime: contractually single-process and single-writer, no tenancy, no members, no retrieval.

## Proposal

### 1. Scope — where knowledge accumulates, and one recall over it

**The lattice is the write address.** Every durable item is written at one address in
`(workspace, agent, subject)` — #645's model, this RFC's substrate. The address says where a
fact accumulated, and recall attributes it; **who may read it is §2's computed function**, never
the address alone. Members and agents accumulate knowledge locally, ungated, fast; writes never
leave their address — what crosses scopes is *readability*, computed (§2). #645's wall stands
where it was built — capability, credentials, egress, operations; for derived knowledge the gate
is the member audience, and the address survives in every reader's view as attribution.
Coordination *inside* a scope needs no new machinery — objects, typed links, page events, and
automations already are the blackboard.

**The substrate stays mediated, or every invariant here records a shadow.** A model-provider
memory endpoint the loop reads and writes directly would move the brain outside the lattice.
`ModelClient` therefore forbids provider-side memory and server-side conversation state — boot
fails loud on an endpoint that offers it (the `memory_search` single-provider gate's shape,
`serve.py:531-557`). Where a provider's memory protocol is the only path to a surface, ufo
mounts *its own* audience-filtered items through that protocol: the store is a carrier, ufo
remains the mediator, and Claude Code or Codex reads the brain with no second store.

**Provenance becomes typed, or the audience is fiction.** Today `memory_item.source_ref` is a
free-text column the model itself writes — the thing §2 computes over could forge its own
inputs. It is demoted to display. In its place: `source_page_id`/`source_turn_id` typed columns
writable only by pipeline code (`FactDeriver` stamps the page; the `memory_update` handler
stamps the acting turn; the tool can set neither), and a `memory_provenance(item_id, root_kind,
root_id)` set that consolidation must *union* from its cluster instead of nulling
(`condenser.py:276` today). A `body_digest` column (sha256, normalized) gives duplicate
detection and the classify cache one cheap join key. Typed provenance is the entire input to
§2's audience function — a model-writable root would be a model-writable audience.

The rest of the organ, each change with its cost:

- **`graph_search`, its seeder, and the `knowledge_graph` extension die whole.** One query surface
  remains: `memory_search`. Whether a graph arm is ever
  *rebuilt* — entity resolution, provider-structure extraction, a fusion leg inside recall — is
  a measured question, never a design default: the `graph_arm` eval set and a
  relational-query-share meter land first, and the arm stays deferred until measured share
  clears ~15% (below it HippoRAG 2's lift vanishes while extraction costs apply to every page).
- **Each workspace gets a derivation floor.** The same meter shape gives every workspace its
  measured stuff-vs-retrieve crossover (ConvoMem's 2025 point estimate was ~150 conversations;
  windows moved, so it is measured, not pinned): below it, pages index but derivation and
  consolidation stay off.
- **Classify-on-index, not write-time.** The write path derives nothing (by doctrine); the index
  job already claims batches and computes the embedding classify needs. In the same claim:
  `duplicate` when `body_digest` matches or cosine ≥ 0.95 with near-identical normalized text —
  no model call; the band between clear-duplicate and clear-independent takes one small-model
  call per claim batch, fail-toward-insert; the verdict is memoized content-addressed on
  `(body_digest, neighbor_id, neighbor_digest)` so replay is a no-op and the verdict is an
  auditable artifact. Similarity cannot tell a restatement from a negation — "we chose Postgres"
  and "we chose MySQL" embed close — so `supersede` past the duplicate band is the model call's
  verdict, never cosine's alone; a supersede that *reverses* meaning rather than refreshing it
  notifies the subject member — the human is the escalation path, not a contested-state machine.
  The ≤60s window before classification is served by the existing unembedded-tail leg.
  `supersede` marks the old row and **nulls its index claim so the same poll prunes its chunks**
  — closing the live defect where recall shrinks with every consolidation pass.
- **Recall stays one entry point.** Results carry durable `ObjectRef`s (#613) — search finds,
  `object_get` opens. Fusion, ranking, and any future arm live inside `MemoryStore.recall`,
  which nothing outside the extension can call.
- **Three lifecycle ops, and forgetting cascades.** `supersede` (leaves the index; row and
  record intact), `delete` (tombstone; record intact), `redact` (record payload scrubbed;
  skeleton intact; never on a live head). Collapsing these is how a system becomes unable to
  answer whether "forget this" meant *stop retrieving* or *erase the trace*. A derived item
  tombstones when no remaining provenance root supports it — a join over `memory_provenance` —
  so the existing page-deletion path is the eraser. **The ops are their own undo**: a superseded
  row stays in the store, so a bad consolidation is reversible by unmarking it; only `redact`
  destroys, and it refuses on a live head. `delete` takes effect behind a `FORGET_GRACE_DAYS`
  window before purge, and deletion past `BULK_DELETE_CONFIRM_THRESHOLD` within a rolling window
  — never per turn, which a patient deleter slices under — requires a **live speaker's inline
  confirm**, the `mutate_requires_speaker` gate that already hard-gates "share my Gmail"
  (`tools.py`): "clean up outdated facts" is a sentence an injected page can write, and an
  injected page has no speaker.

### 2. Audience — computed at read

Sharing is not an act. A fact is shared the moment it is derived, to exactly the audience its
provenance permits: the agent learns "Acme's renewal is May 1" from a page every member may
read, and every member's recall serves it within the indexing latency — no ask, no click, no
copy, no tool. The same sentence spoken in Alex's DM carries a turn root only Alex may read, so
it is Alex's alone. Memorability is the derivation pipeline's question (§1); viewability is the
audience function's; sharing is their product — computed, automatic, never a member action.

There is no widening verb. The organ's safety argument is one invariant — **a reader is never
served a derived fact whose sources they could not open** — and every root's audience is the
audience of a page or conversation that reader already holds, so the automatic flow discloses
nothing a reader could not already read at source; no guard machinery patrols a boundary the
function cannot cross. Widening past the bound works the way organizations already work: the
member states the fact where the wider audience lives; the statement is a new root at that
audience, attributed to its speaker, and derivation does the rest. What that costs is retyping,
and the cost is measured, not guessed — recall counts every withheld fact (below), and a
widening verb is built only if that number ever makes the tax real (deferred, with its trigger).

- **The function.** `audience(item) = ∩ audience(root)` over the item's typed provenance set. A
  turn root carries its conversation's audience — `member:X` in a DM is X alone; `shared` within
  agent A is every member with access to A (#645's access list). A page root carries its source
  binding's, subject to fidelity (below). The value is denormalized onto the item row and
  maintained by the same jobs that index — a source rebind enqueues recompute; derived state by
  jobs, never inline — and enforcement is one filter in `MemoryStore.recall`, applied **before
  ranking**, which nothing outside the extension can call: ranking never sees an unreadable
  item.
- **A binding's audience is only as true as its granularity.** `source.subject` is a binary
  (`shared | member:X`) while the upstream system is not: a whole-drive sync marked `shared`
  claims every member for pages some of them cannot open at source. A connector therefore
  declares `acl_fidelity ∈ {binding, upstream}` — `binding` when the sync's subject faithfully
  captures upstream readership (a member's own mailbox, a channel synced with its membership),
  `upstream` when the source enforces finer ACLs than `subject` captured. An `upstream`-fidelity
  page contributes **the writer's scope**, never `shared`, so nothing derived from it over-serves;
  binding narrower sources restores full flow, which is the right incentive. Pipeline-set from
  the connector's declaration, never model-writable — and it is the one place the audience
  function can be wrong about a root rather than about an item.
- **Automatic means: already theirs, and few enough roots.** An item reads beyond its write
  scope only when (a) every root resolves to something that audience may already read —
  resolved over §1's typed columns, since model-writable strings can launder anything — and (b)
  its distinct roots number ≤ `MAX_PROVENANCE_ROOTS` (start 3). The second conjunct keeps
  aggregation honest: a synthesis over a thousand individually-readable expense lines *is* a new
  disclosure — per-item authorization does not compose to aggregate authorization — so a
  synthesis reads at its write scope, full stop; a member who may read it and wants it wider
  says it where wider lives.
- **Currency is reader-relative, and that is the design.** `supersede` hides the elder head only
  from readers who may read the superseder; a reader outside the superseder's audience keeps the
  elder head — which is how organizations already read: what you know is what the documents you
  may see say, and a correction you may not see changes nothing for you. A reversal notifies the
  subject member either way (§1).
- **Attribution rides provenance.** Recall renders where a fact accumulated — another
  principal's items surface as attributed statements from their scope, never laundered into the
  reader's own voice. Two agents on one incident see each other's findings live the moment root
  audiences permit — and nothing was copied to make that true.
- **The withheld counter is the organ's meter.** Recall already computes the scope-eligible set;
  the audience filter's rejects are counted per workspace — the withheld-at-recall rate — and
  reported. It measures, exactly where the tax would be paid, whether a widening verb would ever
  earn its machinery.
- **Operator reads are audited.** The operator surfaces (debugger, memory explorer) reach every
  tenant's memory today with no record (`operator.py:53-64`) — the one read path the audience
  function does not govern, because an operator is not a member. A tenant-readable `access`
  record — who, which workspace, when, which surface — is written by those surfaces, boot-gated
  so an operator surface that doesn't write it fails to start.

## Threat model

| Attack | Defense | Residual |
|---|---|---|
| Injected page → false "policy" in private scope | a derived fact's audience never exceeds its page's own readership; recall renders it attributed to that page | influence on that page's readers' own turns |
| Provenance laundering (model-supplied refs) | provenance is typed columns written only by pipeline code; the audience function reads nothing else; `source_ref` has zero power | pipeline compromise |
| Over-broad source binding (whole-drive sync marked `shared`) | `acl_fidelity` — an `upstream`-fidelity root contributes the writer's scope, never the binding's | a connector that misdeclares its own fidelity |
| Aggregation disclosure (synthesis of readable items) | `MAX_PROVENANCE_ROOTS` holds syntheses at write scope, with no widening path | none by machinery — a utility ceiling, not an exposure; a member can still restate a synthesis by hand |
| Cross-scope inference via ranking | the audience filter runs before ranking inside `MemoryStore.recall` — ranking never sees an unreadable item; subject in every query | timing side channels |
| Unlogged operator access | tenant-readable `access` record, boot-gated | the operator's own infrastructure |
| Injection-driven forgetting | the `FORGET_GRACE_DAYS` delete window; bulk deletes past the rolling-window threshold need a live speaker's confirm, which an injected page cannot supply | deletion held under the windowed threshold stays ungated; the grace window is the backstop and needs a reader |
| A bad consolidation buries a true fact | supersede leaves the row in the store and is reversible by unmarking; only `redact` destroys, and it refuses on a live head | a redact a member asked for and later wanted back |

## Build order

Two units, each landing alone and proving a chain end-to-end. Nothing is declared before its
producer and consumer land together. A Proof cell is the unit's acceptance chain — the
end-to-end acts that demonstrate the seams compose — not its test matrix; every mechanism a
Lands cell names gets its focused tests with its implementation (AGENTS.md §Testing), whether
or not it appears in the chain.

| Unit | Lands | Proof |
|---|---|---|
| **1. Measure, delete the second surface** | `graph_arm` eval set + relational-share meter + per-workspace stuff-vs-retrieve crossover; `graph_search`, its seeder, and the `knowledge_graph` extension deleted whole | The meter's number decides whether the deferred graph arm is ever built and sets each workspace's derivation floor; `graph_search` no longer registers |
| **2. One recall, computed audience** | typed provenance columns + `memory_provenance` set + `body_digest`; the audience function — `∩` over root audiences — denormalized at index with its rebind recompute job; `acl_fidelity` declared by connectors and consumed by the function; `MAX_PROVENANCE_ROOTS`; classify-on-index with memoized verdicts and the reversal notification; superseded-chunk pruning; cascade forget; `FORGET_GRACE_DAYS` + `BULK_DELETE_CONFIRM_THRESHOLD` behind the live-speaker confirm; the `ModelClient` memory-endpoint boot gate; `ObjectRef`s in results (#613's search half); the `access` record on operator surfaces; the withheld-at-recall counter and its report | A reworded duplicate stops minting a second row; a page tombstone erases its derived facts; recall stops shrinking; within one agent computed audience matches the scope filter except where it is narrower — an item whose only root is a page bound narrower than its write scope stops over-serving — and across agents a member who may read every root of another agent's item recalls it, attributed, with no act; a fact derived from an `upstream`-fidelity page reads at the writer's scope only; a synthesis past `MAX_PROVENANCE_ROOTS` reads at write scope only; a reversal supersede notifies the subject member; a superseder the reader may not read leaves that reader's head unchanged; a bulk delete with no live speaker refuses; an unmarked supersede restores the elder head; results carry `ObjectRef`s; an operator read appears in the tenant's `access` list; boot refuses a model endpoint offering provider-side memory; the withheld-at-recall report renders |

**Deliberately deferred, each with its reason:**

- **The widening grant** — builds only when unit 2's withheld-at-recall rate says retyping is a
  real tax, and arrives as its own RFC (§Alternatives carries the full argument and the
  machinery it drags).
- **The graph arm** — entity resolution (`merged_into` pointers, alias conservatism),
  provider-structure extraction, a fusion leg inside recall. Behind unit 1's relational-share
  meter clearing ~15%; below it the lift measurably vanishes while extraction costs apply to
  every page.
- **Cross-workspace federation** — the lattice ends at the workspace until a second-workspace
  consumer exists.

## Doctrine fit

- **Core gains two gates and no module, which is the core test passing.** Root audiences are
  facts core already owns — a conversation's membership, a source binding's subject — and the
  intersection is recall policy inside the owning extension. What core does gain is exactly what
  extensions cannot be trusted to do: `ModelClient` refusing provider-side memory (the brain
  cannot leave the lattice) and the boot-gated `access` record (an operator surface that reads
  a tenant's memory without recording it fails to start). Ranking, extraction, classification,
  and consolidation stay in the extension.
- **Zero new member-facing tools.** Deleted: `graph_search`, its seeder, one whole extension.
  Added: nothing. Sharing has no tool at all — it is the audience function; the widening act
  that would have needed one is a member speaking in the room where the wider audience lives.
- **Every member action stays in chat**, and the only gate in this RFC is the one already there:
  a bulk forget needs a live speaker, the same `mutate_requires_speaker` shape that gates
  connecting an account. No proposal, no click, no endpoint.
- **One shape.** Every readability question in the system is the same intersection over the same
  typed provenance set — recall, attribution, currency, and aggregation are one function read
  four ways, not four mechanisms.
- **Fail loud:** a bulk delete without a speaker refuses, a redact on a live head refuses, boot
  refuses a provider memory endpoint, an operator surface without an `access` writer refuses to
  start. **Batch-at-interval stands**: the rebind recompute and the poll indexer are the
  sanctioned convergence paths with their latency written down (≤60s).
- **The allocation principle:** scope and audience are questions about who may know a thing and
  live at core's enforcement points; extraction, ranking, and consolidation are questions about
  retrieval and live in extensions — the mechanism layer is commoditizing on schedule, and none
  of it records an audience.

## Why this survives

| Bet | Holds because | Dies if |
|---|---|---|
| Scope | No model knows who may see a salary band; each platform governs only its own agents by design (Purview risk-scores no third-party agent) while enclosing data against the others (Slack's 2025 API terms cut Glean to query-by-query) — the cross-vendor seat is structurally unoccupiable by any one bundle | Hyperscalers fold tenant-faithful *knowledge* scoping into the identity plane |
| Substrate mediation | Every lab is pulling memory into the platform (managed stores, Dreams/Dreaming); a mediated carrier keeps the audience true while using their stores | A provider ships workspace-audience semantics — then ufo is a UI on it, and the bet was still right to take |
| Audience | Nobody merges, and nothing here needs to: with no copies there are no replicas to reconcile — the consistency challenge the field names most pressing is dissolved, not entered; Glean proved query-time ACL enforcement at document granularity, and typed provenance is the only missing input to do it one layer down | Provenance stops being resolvable — we lose the page substrate, not the model race |
| Graph as a measured arm | HippoRAG 2's multi-hop lift is real above ~15% relational share — and the sellers are the skeptics: Mem0 removed external graph stores after measuring ~2%, LightRAG deletes traversal, and the benchmark canon is demonstrably gameable (a named teardown caught test-set-engineered fixes and a retrieval config that bypassed retrieval) | The meter says share is under ~15% — the arm is never built; unit 1 measures before anything is |
| Derived knowledge at all | Most models fall below half their short-context baseline by 32K on non-literal tasks (NoLiMa); multi-turn agents drop 39% with no recovery; long context beats RAG by 8 points at 26× the tokens; test-time training memorizes metrics, not content (recall stays zero) | Weight-level continual learning ships with real recall — or a workspace sits under its measured crossover, where stuffing already wins |

Read the graveyard precisely: every 2025–26 casualty is an orchestration layer; no system that
stores, indexes, or governs an organization's knowledge died in the window. Scaffolds die into
thinner standard scaffolds; knowledge layers compound.

## Alternatives

- **Ship a widening grant now** — a `memory_share` verb granting named roots to a wider
  audience, speaker-confirmed, revocable, with syntheses widened by item grant. The residual it
  serves is real: a fact whose member would rather click than retype. But the verb drags the
  hardest machinery in the design — recompute-widening with secret detection inside it, revoke's
  closure over what consolidation made of granted items, retroactive reach demanding live counts
  and expiry at signature, origin-trust and fidelity eligibility wiring — and its own threat
  rows (exfiltration by grant, retroactive widening), all for a frequency nobody has measured
  against a fallback that costs nothing to build. The withheld-at-recall counter measures that
  frequency where the tax is paid; the grant is built when the number says so, not before.
- **Cross by copying — reconcile item sets into each destination scope** (ActiveGraph's
  `promote`, applied to knowledge). A curated set crosses atomically into the destination,
  `crossed_from` chains later corrections, per-destination supersede keeps every scope one
  internally consistent head. Rejected because it rebuilds replication: every crossed row is a
  replica whose consistency a classify cascade must then maintain per destination — the exact
  open problem the field names — and the machinery it drags in (staleness recompute at apply,
  crossing caps, self-corroboration exclusions) exists only to manage the copies. What copies do
  buy — one head per scope regardless of reader — is real, and §2 prices it: reader-relative
  currency is how organizations already read.
- **Model-chosen scope** — let the writer decide who may read what it learned. The model would
  be choosing an ACL from prose, with no ground truth about org membership and every incentive
  to be helpful; and it would make the audience a model-writable field, which §1 spends a whole
  migration to prevent.
- **One pile, ranking arbitrates.** The append pole: every derived fact readable by everyone,
  relevance sorts it out. It is the fastest thing to build and it is unshippable to any
  workspace where one member's DM is not everyone's — which is all of them.
- **Keep `graph_search` and fix its seeding.** Two query surfaces for one knowledge plane; zero
  usage evidence; the one dependent workflow is broken by the identity key it would need fixed
  anyway.
- **Dreams-style regenerate-and-adopt as the whole answer.** Input-immutability is right and
  consolidation keeps it; a synthesis pass has no per-item provenance and no audience — it
  cannot say *why* a reader may see an item. With the audience function, regenerate is just
  consolidation.
- **Files-in-git memory with PR review** (Letta's shape). The one validated merge mechanism —
  textual, and blind to audiences: git has no concept of "visible to Ana and not Ben," which is
  the property being sold. Revisit if the brain ever becomes single-tenant-per-repo.
- **CRDTs.** No shipped instance for knowledge, and for a reason: commutativity is exactly the
  property a contradiction lacks — and with no replicas there is nothing to converge.

## Open decisions

1. **Where an agent's own scope sits in the lattice for a member with access to two agents.**
   #645 makes the agent the operational wall; a member with access to A and B reads both agents'
   `shared` items by root default. Whether that is one brain per member or two attributed
   streams is a rendering question this RFC leaves to recall.
2. **Fidelity granularity beyond the binary.** `acl_fidelity` degrades an `upstream` source to
   the writer's scope rather than modelling upstream ACLs. Modelling them (per-folder subjects
   from the connector) is strictly better and strictly more connector work; it needs a connector
   that can actually report them.
3. **The withheld counter's threshold.** What withheld-at-recall rate justifies building the
   widening grant is a number nobody has, which is why the counter ships before the opinion.
