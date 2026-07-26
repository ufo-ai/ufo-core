---
rfc: 0019
title: "Shared brain — scope, merge, decision, receipt"
status: proposed
date: 2026-07-26
---

# Shared brain — scope, merge, decision, receipt

> A workspace is many principals — members, agents, automations — learning at once. Everything
> shipping today picks a pole: **partition and never unify** (nothing anyone learns compounds) or
> **append and let ranking arbitrate** (every mistake is instantly everyone's). The old shape in
> between — branch, then merge under review — is honest about its limit: git merges structure and
> hands meaning to a human. Facts with recorded sources can go further: duplicates are
> detectable, newer-versus-older is datable, and true contradictions are rare enough to show to a
> person. ufo already has the branches — `(workspace, agent, subject)` is a lattice of long-lived
> branches of one company brain (#645). **This RFC is the merge**, and the field, verified vendor
> by vendor, ships fork and stops short of merge — the closest thing is git-textual, blind to
> meaning and audience (arXiv:2603.10062 names multi-agent memory consistency the most pressing
> open challenge).
>
> Four organs, one per question no model capability answers: **scope** (what may be known
> where), **merge** (what may cross, how it reconciles), **decision** (who must agree, computed
> — *a gate you can compute is worth more than a gate you must click*), **receipt** (what
> happened, reversibly). Core owns each organ's enforcement point — the address, the crossing
> workflow, the tier, the record; every organ's mechanics stay extension-owned. Extraction,
> ranking, and consolidation are guesses about how this year's models retrieve, and deleting
> one costs nothing.

## Current state

Five fragments of one idea, none composing with the others.

| Fragment | Has | Lacks | Where |
|---|---|---|---|
| memory ext | 3-leg recall (lexical + vector + unembedded-tail), RRF/cosine blend, per-kind decay, hourly consolidation, `memory_100` evals | write-time classify (identity is `uuid5` over exact body bytes — a reworded duplicate is a new row), typed provenance (`source_ref` is a nullable free-text column **the model itself writes**, `manifest.py:139-141`, and consolidation nulls it, `condenser.py:276`), forget path, index cleanup of superseded rows, durable refs in results | `extensions/memory/ufo_ext_memory/store.py:396-434` |
| knowledge_graph ext | 2-tier extraction, typed edges with page provenance, stub-on-reference | entity resolution — `entity_type` sits inside the `uuid5` identity (`store.py:433-436`), so an org chart in prose mints `topic` nodes a *person* lookup can never find, breaking the "place a person" workflow four `chief_of_staff` skills teach; a working seed (`context_for` substring-scans the newest 500 entities); any eval | `extensions/knowledge_graph/ufo_ext_knowledge_graph/store.py:425-436,557-574` |
| governance | `proposal` row + `propose_change` (agent prompt only, digest CAS) | **an approver** — `approve_proposal(proposal_id)` takes no member argument, checks nothing, has zero production callers (#646); no `approved_by`, no `expires_at`, no pending-row uniqueness (a replayed propose mints a duplicate) | `core/src/ufo/governance.py:28,57`; `schema/tables.py:289-303` |
| self_improvement ext | corpus → proposer → counterfactual replay → two-stage gate + `STABILITY_COUNT = 2` | statistical power (at `N_FLOOR = 4` the likelihood ratio is ~2.3:1 and the gate goes silent above a ~0.96 baseline, `gate.py:22-24`; the global stage *passes by default* under-floor, `gate.py:142-154`); a corpus that can accumulate (it is a 200-conversation sliding window, `ext/context.py:178`); a ref beyond the agent prompt; an exit — every passing candidate lands in a table nothing can approve | `extensions/self_improvement/`; RFC 0016 owns the rework |
| objects (RFC 0017) | one address `(kind, name)`, five verbs, per-kind typed storage, `MemberOwnedObjects` | history (last write wins), concurrency — deferred verbatim: "returns with governance, not before" | `core/src/ufo/objects.py:505-565` |

Three gaps are worse than they read. **The improvement loop is a closed pipe** — it runs hourly,
spends tokens, passes its gate, and writes into a table with no exit. **Recall silently shrinks**
— consolidation supersedes rows but never prunes their chunks (the only index delete in the repo
is page-scoped, `store.py:736`), so dead chunks keep winning result slots the read-back filter
then drops. **The knowledge surface is three tools that don't compose** — `memory_search`,
`graph_search`, and the object verbs; the graph tool can't see what the org chart created,
search drops durable refs (#613), and nothing links a memory to its page.

**In flight, assumed:** **#645** — agent as the operational security boundary; everything scoped
`(workspace, agent, subject)`; a new agent starts empty; copying is explicit. **#624** — the
portal as read-projections; every mutation a chat turn. **#613** — typed `links` on
`object_get`; `memory_search` returns `ObjectRef`s; "search finds, `object_get` opens."

## What the field ships

Verified July 2026; full sourcing in PR #684's research record.

- **Nobody merges knowledge semantically.** Production conflict handling is compare-and-swap
  prevention
  (Anthropic's `content_sha256`, Letta's `memory_replace`), last-write-wins with documented lost
  updates (`memory_rethink`), regenerate-and-adopt (Anthropic Dreams and OpenAI Dreaming both
  write a *new* store from an immutable input; a human adopts or discards wholesale), temporal
  invalidation that keeps both sides (Graphiti `invalid_at`), and partition-as-refusal (Graphiti:
  no cross-namespace merge, by doc). The one literal three-way merge is Letta's git worktrees —
  textual, own-subagents only.
- **Gates exist for skills and documents, never for facts.** OpenClaw's Skill Workshop writes
  `PROPOSAL.md`, stales on hash movement — then defaults `approvalPolicy` to `auto`; GRASP
  (arXiv:2605.29668) accepts a skill edit only on held-out gain within a regression budget and
  shows the *gate*, not the writer, makes results real; Glean verification is a freshness badge
  on a document. No shipping system gates a learned memory claim.
- **The registry and the log are commoditizing; the undo is not.** Forrester's Agent Control
  Plane category (2025-12) names *agent registry* and *audit trail* as capabilities; AWS shipped
  a registry preview (2026-04); Anthropic ships immutable memory versions with CAS — and no
  restore endpoint. Forrester's 2026 verdict asks for what none of them name: "bounded tasks
  behind approval gates and **rollback paths**," every agent "a governed identity [with] a named
  owner." The Five Eyes agencies' agentic guidance (2026-05) prescribes human sign-off on
  high-impact acts *decided in advance by designers* — and concedes today's agent logs are "hard
  to parse."
- **Human-click gating fails measurably** — fatigue (per-action approval is
  "security-equivalent to no approval" under habituation), misrepresentation (GhostApproval: the
  dialog showed the proposed path, not the resolved target) — while mechanical verification
  works where clicking doesn't (DeLM: admitting writes only when they verify against evidence
  costs 4.9 points to remove). Entra's answer is standing scoped grants plus permissions "even
  an administrator can't consent to."
- **The graph engine is being deleted by the people who sold it.** Mem0 removed external graph
  stores after measuring ~2%; LightRAG deletes traversal; the memory-benchmark canon is
  demonstrably gameable (a named teardown caught test-set-engineered fixes, a retrieval config
  that bypassed retrieval, 0.4% sampling); the durable pro-graph result is HippoRAG 2's +7 F1 on
  multi-hop, lift that vanishes below ~15% relational query share.

The two shapes worth combining have never been combined: Dreams' adoption posture (reconcile
into a reviewable artifact, input immutable, adopt or discard whole) and GRASP's gated
acceptance — and neither has ever been applied to *facts*, on a permissioned lattice, with an
audience recorded per decision. That combination is this RFC.

## What ActiveGraph teaches

Yohei Nakajima's ActiveGraph (arXiv:2605.21997, "The Log is the Agent") and Regimes
(arXiv:2606.10241) are the nearest prior art. The runtime: an append-only event log as source of
truth; the graph a deterministic projection; behaviors react to shared state — the blackboard
lineage, named in the paper — with total provenance on every mutation, patches as CAS'd
proposals whose rejection is data, **fork** (branch at any event, prefix served from a
content-addressed response cache), **trial** (candidate runs against replayed history in
isolation), **diff**, and **promote** (shipped v1.3): a fork's net delta adopted into its parent,
three-way against the fork point, atomic, quiescent, audited — and fail-closed: "no semantic
merge; identical concurrent edits still conflict; the escape hatch is re-fork." v1.9 adds
authority ceilings — five act classes where outward and governance acts are *unrepresentable as
routine*, and "no inference, ever" maps no fuzzy label onto a class. Regimes then runs the
improvement loop on that substrate — diagnose failures into a regime histogram, route the
dominant regime to the one seam that can address it, gate candidates through static → sandbox →
in-sample → held-out, record every promote and discard as history — and lands three results this
RFC adopts: a promoted prompt is a *probe* whose lasting value is the guarded deterministic
operator it reveals; loops over-promote at high baselines without a plateau rule; and the
measurement layer must sit outside the loop's own mutation reach.

The map, organ by organ — what ActiveGraph ships single-writer, ufo ships multi-tenant:

| ActiveGraph | ufo organ |
|---|---|
| shared graph, behaviors react (blackboard) | workspace objects + typed links (#613) + `page_change` events + automations — the coordination plane, already built |
| fork (branch at any event) | the scope lattice `(workspace, agent, subject)` — branches by construction, no machinery |
| trial (recorded-segment replay) | counterfactual replay (archived tool results fed back — the same content-addressed-cache move) + the eval-harness candidate arm (RFC 0016) |
| structural diff | the resolved-effect diff every governed apply renders (§3) |
| promote (adoption: atomic, quiescent, audited, fail-closed) | the merge's shape (§2) — plus the semantic classify layer promote refused, affordable here because the items are facts with recorded sources |
| patches (CAS, rejection-as-data) | proposals (digest CAS, pending rows) |
| authority ceilings; R3/R4 never routine | the computed tier + closed never-routine set (§3) |
| the log | turn lineage + typed page provenance + the `revision` journal (§4) |

The map's price belongs here, where the question lives: promote is free in a single-writer
runtime; in a multi-tenant one, the first crossing between two audiences costs a human signature
(§2) — after which that decision is a provenance root and the flow computes. Two rows lean on
RFC 0016 (status: proposed) — the candidate-arm trial and the gate rework are its to land; the
dependency is stated, not hidden.

**Should we just use ActiveGraph? No** — architecture (contractually single-process,
single-writer; ufo is multi-process with live concurrent writers under DBOS), category (its
graph is coordination working-state: no tenancy, no members, no retrieval — its only embedding
implementation is a test double the runtime never calls — no decay, no consolidation), and its
own deferrals (the governance gate "is downstream's to implement"; `approved_by` defaults to the
string `"user"`). Everything portable is design, and the table above absorbs it.

## Proposal

### 1. Scope — the lattice, and one recall over it

**The lattice is the branch structure.** Every durable item lives at one address in
`(workspace, agent, subject)` — #645's model, this RFC's substrate. A scope's **audience** is
the set of members who may read it: `member:X` within agent A is X alone; `shared` within agent
A is every member with access to A (#645's access list). Every admission question in §2 is a
question about the destination's audience. Members and agents accumulate knowledge and
capability locally, ungated, fast. Writes never cross an edge; reads adopt nothing across an
edge; **adoption crosses only by merge (§2)**. Coordination *inside* a scope needs no new
machinery — objects, typed links, page events, and automations already are the blackboard; this
RFC governs what crosses *between* scopes.

**The substrate stays mediated, or every invariant here records a shadow.** A model-provider
memory endpoint the loop reads and writes directly would move the brain outside the lattice.
`ModelClient` therefore forbids provider-side memory and server-side conversation state — boot
fails loud on an endpoint that offers it (the `memory_search` single-provider gate's shape,
`serve.py:531-557`). Where a provider's memory protocol is the only path to a surface, ufo
mounts *its own* scope-partitioned items through that protocol: the store is a carrier, ufo
remains the mediator, and Claude Code or Codex reads the brain with no second store.

**Provenance becomes typed, or verification is fiction.** Today `memory_item.source_ref` is a
free-text column the model itself writes — the thing §2 verifies could forge its own
verification. It is demoted to display. In its place: `source_page_id`/`source_turn_id` typed
columns writable only by pipeline code (`FactDeriver` stamps the page; the `memory_update`
handler stamps the acting turn; the tool can set neither), and a `memory_provenance(item_id,
root_kind, root_id)` set that consolidation must *union* from its cluster instead of nulling
(`condenser.py:276` today) — the same core-private-derived-writer move §4 makes for the journal,
applied to what the journal's guarantees rest on. A `body_digest` column (sha256, normalized)
gives duplicate detection, corroboration, and the classify cache one cheap join key.

The rest of the organ, each change with its cost:

- **`graph_search` and its seeder die; the graph folds into memory.** Four `chief_of_staff`
  skills call the tool (`triage:13`, `prep:12`, `sync:22`, `setup:52`) — those edits and their
  routing evals land in the same unit. The graph's consumers become a measured ranking arm
  inside `memory_search` and `related` links on `object_get` — **edges and #613 links are one
  graph**; the reviewed relation vocabulary is the wire form. Fusion lives inside
  `MemoryStore.recall`, which nothing outside the extension can call, and the `memory_search`
  seam admits exactly one provider — a graph leg inside it cannot stay a separate optional
  extension. Cost: migration re-parent, four pack manifests.
- **Measure before building.** The `graph_arm` eval set and a relational-query-share meter land
  first and double as unit 3's acceptance harness; the fusion leg ships only if measured share
  clears ~15% (below it HippoRAG 2's lift vanishes while extraction costs apply to every page).
  The same meter shape gives each workspace its own derivation floor: below the measured
  stuff-vs-retrieve crossover (ConvoMem's 2025 point estimate was ~150 conversations; windows
  moved, so it is measured, not pinned), pages index but Tier-B extraction and consolidation
  stay off.
- **Entity identity: embedding on the row, `entity_type` out of the key, merge as a pointer.**
  Resolution at index time: nearest neighbor within the writer's scope above `ENTITY_MATCH` →
  same entity, surface form recorded as an alias; miss → new entity; **ambiguity → alias-link
  without merging** (gbrain raised its threshold 0.4 → 0.7 after a real misattribution; its
  auto-redirect aborts on >1 candidate; we adopt the conservatism). A merge **never rewrites
  edges** — it sets `merged_into` on the losing row, resolution follows the pointer at read
  time, and re-extraction stays stable because edge identity never moved. Rollback clears the
  pointer and writes a merge-block row the resolver consults. Every merge stamps its basis
  `(embedding_model_id, threshold, neighbor_id, cosine)`. Batched: one NN query per index claim,
  never per reference inside the page drain.
- **Tier A extraction reads provider structure, not wikilinks.** gbrain's typed graph is free
  because its corpus is authored markdown; ufo's corpus is synced provider records that never
  contain wikilinks — today's Tier A fires on syntax that doesn't occur, so every typed edge
  costs a model call. The deterministic tier that matches our corpus maps **declared connector
  record fields** to the closed edge vocabulary — calendar attendees → `attended`, CRM contact →
  `works_at`, message headers → `person` — zero model calls. Tier B (one bounded call) runs only
  on prose bodies, only past the workspace's measured floor.
- **Classify-on-index, not write-time.** The write path derives nothing (by doctrine); the
  index job already claims batches and computes the embedding classify needs. In the same
  claim: `duplicate` when `body_digest` matches or cosine ≥ 0.95 with near-identical normalized
  text — no model call; the band between clear-duplicate and clear-independent takes one
  small-model call per claim batch, fail-toward-insert;
  the verdict is memoized content-addressed on `(body_digest, neighbor_id, neighbor_digest)` so
  replay is a no-op and the verdict is an auditable artifact. Similarity cannot tell a
  restatement from a negation — "we chose Postgres" and "we chose MySQL" embed close — so
  `supersede` versus `conflict` is never decided by cosine alone. The ≤60s window before
  classification is served by the existing unembedded-tail leg. `supersede` marks the old row
  and **nulls its index claim so the same poll prunes its chunks** — closing the live defect
  where recall shrinks with every consolidation pass.
- **`conflict` holds both, contested.** Most disagreement is stale-versus-current and lands in
  `supersede`; a genuine contradiction stores both sides marked contested, renders at recall as
  disagreement-with-sources (the shape untrusted content already uses), notifies the subject
  member, and escalates only on repeated contested recall. Nothing blocks; nothing silently
  picks a winner.
- **Recall stays one entry point with stamped honesty.** Results gain an `evidence` stamp
  (`alias_hit | exact | high_vector | keyword | weak`) and durable `ObjectRef`s (#613) — "Acme
  vs Acme's" is answered by *showing the resolution*. A bounded **corroboration boost** ranks
  items whose `body_digest` groups across ≥2 **distinct provenance roots** the reader may
  already see — independent attestation, not merge activity (crossed copies are excluded via
  their link, §2). Zero-model entity pointers (gbrain's *reflex*: names matched in the turn →
  ≤3 durable refs with synopses, never bodies) ride the recall hook from day one. gbrain's
  remaining inventory is dispositioned, not ignored: its classify cascade, alias conservatism,
  and reflex are adopted here; its takes/bets, markdown system-of-record, and `think` synthesis
  are deliberately not wanted — the source systems are the record, and the agent is the
  synthesis layer.
- **Three lifecycle ops, and forgetting cascades.** `supersede` (leaves the index; row and
  record intact), `delete` (tombstone; record intact), `redact` (record payload scrubbed;
  skeleton intact; never on a live head). Collapsing these is how a system becomes unable to
  answer whether "forget this" meant *stop retrieving* or *erase the receipt*. A derived item
  tombstones when no remaining provenance root supports it — a join over `memory_provenance` —
  so the existing page-deletion path is the eraser. `delete` takes effect behind a
  `FORGET_GRACE_DAYS` window before purge, and deletion past `BULK_DELETE_SIGNED_THRESHOLD`
  within a rolling window — never per turn, which a patient deleter slices under — is `signed`:
  "clean up outdated facts" is a sentence an injected page can write.

### 2. Merge — how knowledge crosses, walked end to end

A member — Alex — asks: "share what you learned about Acme with the team." The agent selects
items the way it would
answer *what do we know about Acme* — a recall over its own scope; the model curates, and the
workflow verifies. It calls **`memory_share`** (the memory extension's tool over core's crossing
workflow — `graph_search` dies, this arrives). Core resolves every item's **provenance roots** —
pages, member turns, and prior journaled decisions — against the *destination audience*;
verified items reconcile through the classify cascade against destination-now (`duplicate`
drops, `supersede` chains, `independent` inserts, `conflict` lands contested); each crossed row
carries `crossed_from` its source id, so a later correction on either side chains to the other
and the pair never self-corroborates. The subject member is notified; one journal record (§4)
carries the item ops and resolved roots. And because Alex asked in chat, **the speaker is the
signature**: verified items apply with `basis = speaker`, and a residual item — a synthesis past
the root cap — renders inline for the same speaker to confirm in the same conversation, a
sentence and a click in the flow of the ask. The cap's purpose for own-scope crossings is
*informed* consent — the synthesis is shown as one thing before its owner consents to it. For
any other source scope, the residual goes to the bound approver, **who is never the proposer**;
the pending tray exists only for those speakerless crossings — an automation's, another
agent's. That is the whole mechanism; everything below is its rules.

- **Admissible means: already theirs, and few enough roots.** An item crosses as `routine` only
  when (a) every provenance root resolves to something the receiving audience may already read
  — resolved against the receiver, never the proposer, over the typed columns of §1, since
  model-writable strings can launder anything — and (b) its distinct roots number ≤
  `MAX_PROVENANCE_ROOTS` (start 3). The second conjunct is what keeps aggregation honest:
  a synthesis over a thousand individually-readable expense lines *is* a new disclosure —
  per-item authorization does not compose to aggregate authorization — so syntheses are
  residual by construction.
- **Decisions are roots, keyed by audience pair.** A signed merge journals against
  `(source scope → destination audience)`, and later items whose roots resolve to that decision
  cross mechanically. This is what makes the multi-agent case honest under #645: the *first*
  agent→agent crossing is a genuine new disclosure (the receiver's audience cannot read the
  source agent's pages) and takes a signature — **once per audience pair**, not once per agent
  pair or per spawn. An approved **agent profile** (the typed-subagent registry) may declare its
  scope inheritance; approving the profile is the signature, and instantiation journals against
  it — O(profiles), not O(spawns), and it repairs today's asymmetry where capability inherits at
  spawn (`on_behalf_of_member_id` copies forward) while knowledge would not.
- **Reading is not adopting.** Where routine admission would pass, a cross-scope *read* is free:
  items surface in the reader's recall as attributed quotes from the source scope — never the
  reader's own facts, never re-crossable, never corroborating. Sharing becomes synchronous
  (two agents on one incident see each other's findings live once their audience pair is
  granted); adoption stays batched, reconciled, journaled, reversible. One admission function,
  two verbs.
- **The merge has promote's shape and Dreams' posture.** The result is computed as one complete
  resolved diff against destination-now; the source scope is never mutated; the verified set
  applies as one atomic unit or not at all, and the residual is named and held as its own unit
  — partial between the two sets, never within one; staleness recomputes at apply (a
  destination that moved re-verifies as if no diff had rendered). Over `MAX_CROSSING_ITEMS =
  50` or `MAX_CROSSING_BYTES` it **refuses, never truncates**. Apply is quiescent about *item-creating* work — no `page_change` fan-out, no
  automation admission, no event-fired work — while the idempotent index poll picks crossed
  rows up on its next claim (≤60s to searchable, the number written down; "no derivation ever"
  would leave crossed items permanently unsearchable). Consolidation treats crossed items like
  any other only *after* their provenance set is unioned in, so rollback's closure (§4) still
  reaches what they became.
- **Four guards, all pipeline-set.** A secret detector (provider key patterns, entropy, the
  workspace's credential sentinels as a literal denylist) blocks any body crossing a scope
  boundary regardless of tier. `origin_trust` records the arrival pipe — the model can never
  write it; untrusted-origin items are never admissible and render as quoted evidence
  (machine-authored content is the 2027 norm, so the durable form of trust is the provenance
  chain itself; the flag is the cheap first cut). `acl_fidelity ∈ {document, source}` marks
  whether the upstream system enforced finer ACLs than the source binding captured —
  source-fidelity items (a whole-drive sync) are ineligible for `routine` crossing, because
  "the audience may already read the source" is only as true as the source's own granularity;
  binding narrower sources restores routine flow, which is the right incentive. And per-actor
  throughput is capped (§3).

### 3. Decision — who must agree, computed

| Tier | When | Requires |
|---|---|---|
| `routine` | reversible, no live-speaker gate bypassed, discloses nothing new (§2 verified) | applies; journaled |
| `signed` | irreversible, a residual disclosure, a capability grant with no present speaker — or a behavior-bearing ref at shared scope without evidence | a bound approver's signature; behavior refs additionally arm rollback and a post-apply regression watch |
| `evidenced` | a behavior-bearing ref at shared scope **with** an evidence bundle that clears its floor | the bundle and a signature |

**Tier is computed, never declared per kind** — one core function of
`(act, ref, scope, actor, reversibility, speaker_present, verified, evidence)` — `verified` is
admission's verdict on a crossing, `evidence` the bundle-and-floor verdict a proposal carries,
each supplied by its own producer and never by the model. Core journals the tier
**and its inputs**, so a policy change cannot reinterpret old records. A **closed set of acts
can never compute to `routine` under any configuration** — ActiveGraph's ceiling, Entra's
"no admin can consent" list, in our types: owner and member-lifecycle changes, edits to the
gate, the eval suites, or the regime taxonomy (the measurement layer must sit outside the
loop's own reach), credential mutation, journal or evidence deletion, retention and
`legal_hold` changes.

- **The seam is `ObjectStore.decide(ctx, name, spec, old) -> Effect`** — side-effect-free,
  called before `apply`. The proposal stores the **resolved Effect** (subject, account, item
  set): the same values the rendered diff shows and a private applier (no `ToolContext`) later
  applies. It must be a pre-pass, not a raise, for two reasons found in the code: `scope` and
  `reversibility` are knowable only where the kind's semantics live (one `sources` handler
  spans a create that lands directly at `shared` and a private→shared flip,
  `tools.py:395-417`), and a handler re-run at approval time would resolve
  `ctx.speaker_member_id` to the *approver* — registering a source under the approver's subject
  against the approver's grants. The seam lands with the first gated object kind, not before.
  Core owns the tier function and the record; the kind owns *when*. RFC 0017 said governance
  would gate the verbs before the handler runs; half reverses here — only the handler can
  resolve the effect — and the other half (kinds untouched) stands.
- **A present speaker is already a signature.** `mutate_requires_speaker` already hard-gates
  "share my Gmail" on a live speaker; routing that through a proposal a different member clicks
  tomorrow is strictly weaker. `speaker_present` is a tier input; signatures are spent only
  where no live-speaker gate exists.
- **Evidence unlocks; its absence never blocks.** The `signed` row for behavior refs is the
  small-workspace path stated as design, not failure: the corpus is today a 200-conversation
  sliding window that cannot accumulate — n≈50/arm is unreachable at *any* headcount until the
  corpus persists flagged examples durably (a stated prerequisite) or the operator pools
  portable bundles. Until a floor clears, a shared behavior change takes a signature with
  rollback armed and a regression watch behind it; when evidence exists, `evidenced` is the
  promotion-to-default bar — and the enum member lands only with its producer (the accumulating
  corpus or the pooled bundle, RFC 0016's first deliverable). The gate's statistics — a relative
  floor, MDE-derived n, family-wise correction, the unreachable-ceiling diagnostic, plateau
  stopping — are RFC 0016's to fix; it owns `gate.py` and the eval arm, and this RFC's only
  requirements on it are those five and the improvement ladder: a prompt repair is a probe; a
  recurring procedure graduates to a skill; a deterministic skill graduates to a guarded
  automation firing on detected structure.
- **Skills stay `routine` until they have owners.** `SkillObjects` carries no owner — any member
  may mutate any skill — so scope-driven tiering would make every skill edit `signed`-or-worse
  and small workspaces unable to edit skills at all. The `evidenced` row keys on scope, never on
  a ref's name.

Five invariants make a signature a boundary rather than a policy:

1. **The approver is bound at open, required at approve, and seated.**
   `approve_proposal(proposal_id, approver_member_id)` — required positional;
   `required_approver_member_id` recorded at open; NOT NULL on decision; a wrong-member click
   refuses visibly. An approver must hold a seat (owner-granted, already in the model) and a
   liveness assertion no older than `APPROVER_FRESHNESS_DAYS` — a domain-verified email
   auto-joins members (`join_member`), and auto-provisioning must not be approval capability;
   the Slack `users.info` deleted flag is the cheap first freshness input, directory sync
   (SCIM) the named enterprise unit. This forces the owner model explicit: assignable, ≥2
   owners, chat-driven transfer, `active | departed` lifecycle — today's owner is the earliest
   member row, so erasing a departed founder silently promotes whoever joined next.
2. **The model has no apply verb, and a click never becomes a turn.** `object_apply` on a gated
   ref refuses with `{proposal, status: pending}`; approval is a signed surface interaction,
   `ConnectClick`-shaped (direct non-turn side effect), never `AnswerClick`-shaped (which would
   re-admit the model into the decision path). A `signed` act on a surface with no decision
   path refuses visibly; web parity ships with the first gated ref.
3. **The diff is the resolved effect** — the stored `Effect`, the same values apply will use,
   never the proposer's description (GhostApproval is what the other choice costs).
4. **Throughput is bounded, and the monitor watches the routine path.**
   `MAX_PENDING_PER_ACTOR = 20`, `PROPOSAL_TTL_DAYS = 28`, `UNIQUE(workspace_id, ref,
   to_digest)` among pending rows; related items bundle into one decision. A proposal whose
   turn lineage carries **untrusted-origin evidence** gets no button — it parks as "the agent
   wants to propose X; ask it to" (keyed on `origin_trust` in the lineage, a defense, not a
   speaker proxy — a cron-born candidate with clean evidence keeps its button; the rule lands
   in unit 4 with its `origin_trust` producer, and needs nothing earlier: through 1c the only
   proposal producer is the replay-gated pipeline, with no page-injected path to a proposal).
   The rate-anomaly signal computes over **journaled routine crossings**, not the pending tray
   — verification-first keeps the tray empty, so the tray is the wrong place to look.
5. **Standing delegation ships with the click.** The grant is the signed decision, journaled
   once; applies journal against it; delegations are listed, expiring, revocable, journal their
   use, and expire on the delegator's lifecycle change. Audience-pair roots and approved agent
   profiles (§2) are this mechanism, not new ones.

### 4. Receipt — a decision record that rolls back

- **`revision` (core, one table) records a decision, not a version:** `ref`, an opaque
  `principal` (internally a superset of RFC 8693 `act` chaining — the one standardized shape
  for actor chains, so exporting to whatever WIMSE normatizes is a projection, not a
  migration), originating turn and audience, computed tier **and its inputs**, decision basis,
  the per-item op list (ids only: inserted, superseded, contested), and the **resolved
  admitting-root ids** — which is what makes the blast-radius query answerable: "every revision
  whose roots transit page P," the first question an incident asks (the closure over derived
  items needs unit 3's provenance set, and until then the receipt cannot answer it — stated,
  not implied). Unit 1a lands only what unit 1a produces: `basis = 'speaker'`, applied tier
  `routine`, plus the meter's `shadow_tier` — the one 1a column whose consumer is the
  residual-rate report in the same unit; each later unit brings its own columns and enum
  members.
- **`proposal` becomes strictly a pending request**, consumed by its decision; `revision` is the
  sole record of what was decided.
- **Digests and refs only — never content.** Content stays in the owning kind's table under its
  erasure path. The journal is append-only **by policy, not cryptography** — a feature: the
  EDPB's blockchain guidelines (final, 2026-07-07) advise keeping personal data, even hashed,
  off technically immutable structures, so a Postgres table with purge, `redact`, and an
  erasable principal→member mapping stays Art. 17-operable where a hash chain would not.
  Retention: per-workspace `retention_days`, default 180 (the AI Act's "at least six months"
  floor, binding deployers 2026-08-02). Approver identity is retained through erasure as a
  legal-obligation exception (Art. 17(3)(b)) — an audit trail whose approvers become "someone"
  is not one. Purges run under a distinct principal and are themselves journaled as
  count-and-window records.
- **`legal_hold` is a workspace mode in the never-routine set**: purge suspends (unit 1a, with
  the purge job it gates); when the lifecycle ops land (unit 3), `delete` collapses to
  `supersede` and `redact` refuses under hold. The three-op distinction is why hold is a mode,
  not a subsystem — and each of its behaviors lands with the op it constrains.
- **Operator reads are part of the receipt.** The operator surfaces (debugger, memory explorer)
  reach every tenant's memory today with no record (`operator.py:53-64`). A tenant-readable
  `access` record — who, which workspace, when, which surface — is written by those surfaces,
  boot-gated so an operator surface that doesn't write it fails to start.
- **A core-private writer derives what it records** — it accepts no principal and no basis
  argument, reading both from turn authority and the recorded click, so extension code can
  never assert `basis = signed_decision`. That writer plus the CI import gate are the primary
  defense; DB grants are hosted-only hardening, labelled as such — boot fails loud if
  tamper-evidence is claimed without the two-role split.
- **Rollback rides the object surface: `revision` is a kind whose apply inverts.** Zero new
  verbs — `object_list revision` is history, `object_apply` of an inversion manifest is
  rollback, rendered and gated like any governed apply: it takes a *set* of revisions,
  newest-first, atomic, refuses over cap, recomputes against destination-now (a row superseded
  since the target revision surfaces as a conflict, never a silent force), and re-inserting
  what a merge superseded / tombstoning what it inserted follows the op list, with the
  provenance closure pulling in what crossed items became. Every workspace the evidence floor
  excludes gets its safety from this verb instead — and an under-floor `signed` decision
  journals its insufficient-evidence diagnostic beside it, an honest label rather than a
  silent degrade.
- **Readers are named.** The portal's journal tab (#624) is a projection and carries the
  governance view the journal already knows: decisions per approver per week, rejection rate,
  median time-to-decision, expirations unread — an approver with a 0% rejection rate and a
  four-second median is rubber-stamping, and the journal can say so. A date-bounded export
  (JSONL + digest manifest) ships in unit 1a — an auditor's evidence request is an export, not
  a tab.

## Threat model

| Attack | Defense | Residual |
|---|---|---|
| Injected page → false "policy" in private scope | `origin_trust` pipeline-set; renders as quoted evidence; never admissible | Influence on that member's own turns |
| Injected page → proposal flood → fatigue | §3.4 caps/TTL/dedup/bundling; untrusted-evidence lineage → no button; verification keeps the queue small | A patient attacker at low rate still reaches a bored approver |
| Provenance laundering (model-supplied refs) | provenance is typed columns written only by pipeline code; `source_ref` has zero admission power | Pipeline compromise |
| Aggregation disclosure (synthesis of readable items) | `MAX_PROVENANCE_ROOTS` makes synthesis residual by construction | An approver can still consent to a synthesis they don't recognize as one |
| Approval of something other than what applies | the stored resolved `Effect` is the diff and the apply; staleness recomputes | Approver comprehension of a large but complete diff |
| Exfiltration by crossing | caps; audience-side typed provenance; `acl_fidelity`; secret scan; subject notification | A member consenting to disclose their own data |
| Cross-scope inference via ranking | subject in every query; corroboration only over reader-visible distinct roots; crossed pairs excluded | Timing side channels |
| Poisoned skill supply chain (ClawHavoc-shaped) | the agent prompt at shared scope is `signed` with rollback armed (unit 1c), `evidenced` once RFC 0016's producer lands; a proposal binds its target's digest and goes stale if the target moves; no auto-apply, ever | Skills and automations stay `routine` until they gain owners (§3, deferred) — until then the residual is any member, stated, not papered |
| Optimizer captures its own meter | gate, evals, taxonomy in the never-routine set, outside proposal-reachable refs | Operator compromise |
| Forged audit | core-private derived writer; CI gate; hosted two-role grants | Core or operator compromise |
| Unlogged operator access | tenant-readable `access` record, boot-gated | The operator's own infrastructure |
| Injection-driven forgetting | the `FORGET_GRACE_DAYS` delete window; deletes past the rolling-window threshold signed; `legal_hold` freezes | Deletion held under the windowed threshold stays routine; the grace window is the backstop and needs a reader |
| Compromised Slack account = approval capability | Not defended | Step-up auth is an open decision |

## Build order

Six units, each landing alone and proving a chain end-to-end. Nothing is declared before its
producer and consumer land together. A Proof cell is the unit's acceptance chain — the
end-to-end acts that demonstrate the seams compose — not its test matrix; every mechanism a
Lands cell names gets its focused tests with its implementation (CLAUDE.md §Testing), whether
or not it appears in the chain.

| Unit | Lands | Proof |
|---|---|---|
| **1a. Receipt, no gates** | `revision` (1a columns only) + core-private derived writer threaded through `ObjectVerbs._apply`/`_delete` and the private handoffs; `revision` object kind (list/get + inverting apply); `access` record on operator surfaces; retention/purge under a journaled purge principal; `legal_hold` as purge suspension; export verb; CI import gate; the **residual-rate meter** — shadow-compute the tier while every ref stays `routine`, journal it, publish the would-be-residual fraction | Apply and delete a `scheduled_task` and a `source`; read history back; roll one back; export a date-bounded JSONL; an operator read appears in the tenant's `access` list; an unheld purge removes expired rows and journals a count-and-window record under the purge principal, and a held workspace's purge is a no-op; an extension import of the journal writer fails CI; the residual-rate report renders from the shadow tiers. The number it reports decides unit 4's shape |
| **1b. Approver identity** | owner/member lifecycle (`active \| departed`, transfer, ≥2 owners), seat + freshness requirement, `approve_proposal(proposal_id, approver_member_id)`, `required_approver_member_id`, `expires_at` + its sweep job, pending-row uniqueness | A wrong-member click refuses visibly; an unseated auto-joined member cannot approve; erasing a departed owner does not promote the next-earliest row; an expired proposal leaves the tray; a replayed propose no longer mints a duplicate; a chat-driven transfer moves ownership; dropping below two owners refuses; a seated-but-stale approver's click refuses until freshness re-asserts |
| **1c. One gated ref** | the agent prompt via the existing governance path (proposer, digest CAS, replay arm all exist); `TerminalFrame.proposal`; `ConnectClick`-shaped Slack `ProposalClick`; web renderer; refusal on surfaces without a decision path. **#646 closes here** | self_improvement's next passing candidate renders with clean evidence and keeps its button; the bound owner clicks; the prompt changes; the record names them; rollback restores; the web surface renders the same proposal; a `signed` act on a surface with no decision path refuses visibly |
| **2. Measure** | `graph_arm` eval set + relational-share meter (doubles as unit 3's acceptance harness) + per-workspace stuff-vs-retrieve crossover; `graph_search` + seeder deleted, the four skills re-routed to `memory_search` in the same unit so no live workflow loses capability between units | Numbers that decide unit 3's fusion leg and each workspace's derivation floor; `graph_search` no longer registers and the four skills' routing evals pass against `memory_search` |
| **3. One recall** | typed provenance columns + `memory_provenance` set + `body_digest`; knowledge_graph folds into memory (unconditional; the fusion leg alone waits on unit 2's number); entity re-key + `merged_into` + merge-block; provider-structure Tier A; classify-on-index with memoized verdicts; contested pairs; corroboration boost; superseded-chunk pruning; cascade forget; `legal_hold` over the lifecycle ops (`delete` → `supersede`, `redact` refused); the `ModelClient` memory-endpoint boot gate; evidence stamps + `ObjectRef`s (#613's search half); reflex pointers | Multi-hop lift with no simple-recall regression; the org-chart person is findable; a reworded duplicate stops minting a second row; a page tombstone erases its derived facts; recall stops shrinking; a re-extracted page does not undo an entity merge, and a rolled-back merge stays blocked from re-merging; under `legal_hold` a delete lands as supersede and redact refuses; a genuine contradiction stores contested and renders as disagreement at recall; a fact attested from two roots outranks its single-root twin; results carry evidence stamps and `ObjectRef`s; a name in the turn injects its reflex pointer; a calendar event yields its `attended` edge with zero model calls; boot refuses a model endpoint offering provider-side memory |
| **4. Crossing** | `memory_share` over the core crossing workflow with its caps (`MAX_CROSSING_ITEMS`/`_BYTES`, refuse-never-truncate); audience-pair decision roots; `MAX_PROVENANCE_ROOTS`; `acl_fidelity`; `crossed_from` linkage; the secret detector; subject notification; cross-scope read (quotes, zero adoption); profile scope-inheritance grants; `origin_trust` end to end + the untrusted-lineage button rule it keys | Two agents, one workspace: A's research reaches `shared` with no click when provenance verifies and cannot when it doesn't; the subject member is notified when their items cross; a source-fidelity item and a body carrying a credential sentinel both refuse `routine` crossing; a granted audience pair reads live quotes that adopt nothing and never re-cross; a crossed pair never corroborates itself and a correction on either end chains to the other; instantiating an approved profile crosses its declared inheritance with no click; an untrusted-lineage proposal renders with no button; a contested fact renders as disagreement; the whole merge rolls back as one unit — including what consolidation made of it; a synthesis over many roots renders for its owner's confirm when the speaker is present and parks for the bound approver when not |

**Deliberately deferred, each with its reason:** SCIM/IdP directory sync (the enterprise-sale
unit; seat+freshness is the bridge); standing-delegation surfaces beyond audience pairs and
profiles (land with their kind); generalizing `decide()` across kinds (needs enough handlers to
show the shape); `skill`/`source`/`connector` as gated refs (blocked on owners and the
present-speaker rule, and `automation` with them — a scheduled task is the same owner question);
cross-tenant evidence pooling (the portable bundle is built; pooling is
a DPA conversation, not a default); the tenant SIEM/webhook feed (export lands first);
cross-workspace federation (the lattice ends at the workspace until a second-workspace consumer
exists).

## Doctrine fit

- **Core gains four things in this plan, and a fifth by design:** the `revision` table with its
  derived writer (+ the `access` record beside it), the computed-tier function with its closed
  never-routine set, the decision surface (`TerminalFrame.proposal` + per-surface
  `ProposalClick`), and the **crossing workflow** — the only path by which a scope-bearing row
  changes address, the `MemberOwnedObjects` precedent applied to scope (a kind cannot ship
  crossing without it). The fifth, the `decide() → Effect` seam, is designed in §3 and lands
  with the first gated object kind, which is deferred. Scope itself adds no core module: its core pieces already exist (the
  `(workspace, agent, subject)` address on every row, the `MemberOwnedObjects` visibility base)
  or arrive as one gate (`ModelClient` refusing provider-side memory); its mechanics — typed
  provenance, entity resolution, classify — are extension policy, which is the core test
  passing, not failing. Reconciliation policy stays in the owning extension; extensions cannot
  express a trustworthy record and core cannot express the ranking arm.
- **Zero new member-facing tools; one new affordance, owned.** Deleted: `graph_search`, its
  seeder, one extension. Added: `memory_share` (extension tool) — and the approval button on a
  rendered reply (`TerminalFrame.proposal` + per-surface click), which is new member-visible
  surface and is the point. Rollback and history ride the existing object verbs.
- **Every member action stays in chat** — with the approval click argued, not assumed. The
  bespoke HTTP approval endpoint RFC 0017 named as the standing doctrine violation is already
  gone (deleted 2026-07-23 with no replacement — which is *why* `approve_proposal` has zero
  callers and #646 exists), so this RFC builds the first approval mechanism, not a retirement:
  a button **on the agent's rendered reply, in the conversation, on the chat surface** — the
  same interactivity seam `ConnectClick` and the answer buttons already ride. The click is
  deliberately not a *turn* (the model must never re-enter the decision path), but it is the
  chat transport, not a bespoke endpoint. Everything else is a sentence.
- **One shape.** The shared mechanism is the **governed apply** — resolved effect, computed
  tier, atomic, journaled. A *merge* is the governed apply of a knowledge crossing (the only
  one with a reconciler); an agent copy is a governed apply of a bulk act (the 50-item cap
  binds merges, not copies); a rollback is a governed apply of an inversion.
- **Fail loud:** over-cap refuses, unverified refuses, a wrong member refuses, staleness
  recomputes, contested renders. **Batch-at-interval stands**: crossings are quiescent for
  item-creating work; the poll indexer is the sanctioned convergence path with its latency
  written down.
- **The allocation principle:** the four organs are questions about authority and live in core;
  extraction, ranking, and consolidation are questions about retrieval and live in extensions —
  both labs shipped regenerate-style consolidation this year (Dreams, Dreaming), which is the
  mechanism layer commoditizing on schedule, and neither records a decision, an audience, or an
  undo.

## Why this survives

| Bet | Holds because | Dies if |
|---|---|---|
| Scope | No model knows who may see a salary band; each platform governs only its own agents by design (Purview risk-scores no third-party agent) while enclosing data against the others (Slack's 2025 API terms cut Glean to query-by-query) — the cross-vendor seat is structurally unoccupiable by any one bundle | Hyperscalers fold tenant-faithful *knowledge* scoping into the identity plane |
| Substrate mediation | Every lab is pulling memory into the platform (managed stores, Dreams/Dreaming); a mediated carrier keeps the audit true while using their stores | A provider ships workspace-audience semantics *and* a decision journal — then ufo is a UI on it, and the bet was still right to take |
| Merge | Partition-or-pile is what everyone ships; the field names multi-agent consistency its most pressing open challenge; Five Eyes prescribes pre-declared human sign-off on high-impact acts — the computed tier is that, mechanized | Provenance stops being resolvable — we lose the page substrate, not the model race |
| Decision | Mechanical admission beats clicking on cost and coverage (DeLM −4.9); fatigue is measured; Entra/ActiveGraph converge on ceilings no consent can lift | The residual-rate meter reads high — above ~25–30% would-be-residual, per-change gating converges on rubber-stamping and the organ pivots to blast-radius + monitored rollback (the meter ships in 1a precisely to decide this before unit 4) |
| Receipt | Registry and audit trail are now named category capabilities (Forrester's control plane; AWS registry preview) — the log commoditizes; **rollback and audience-per-decision are named nowhere** (Forrester asks for "rollback paths"; Anthropic ships no restore endpoint); only 53% of finance leaders can defend an agent's actions to an auditor today | Nothing. It commoditizes rather than dies — which is why it records decisions and audiences, not versions |
| Graph as a measured arm | HippoRAG 2's multi-hop lift is real above ~15% relational share; gbrain ships the same bounded-arm shape in production | The meter says share is under ~15% — the leg never ships; unit 2 precedes unit 3 for exactly this |
| Derived knowledge at all | 1M-token windows went GA and the gap moved rather than closed: most models fall below half their short-context baseline by 32K on non-literal tasks (NoLiMa); multi-turn agents drop 39% with no recovery; long context beats RAG by 8 points at 26× the tokens; test-time training memorizes metrics, not content (recall stays zero) | Weight-level continual learning ships with real recall — or a workspace sits under its measured crossover, where stuffing already wins |

Read the graveyard precisely: every 2025–26 casualty is an orchestration layer; no system that
stores, indexes, or governs an organization's knowledge died in the window. Scaffolds die into
thinner standard scaffolds; authority layers compound.

## Alternatives

- **Adopt ActiveGraph.** No — single-process single-writer by contract, no tenancy or members,
  no retrieval or memory lifecycle, approvals stubbed. Its algebra is absorbed as design (the
  map above); its published contribution exceeds its code.
- **Event-source ufo.** What the log buys — total provenance, replay, fork, trial — ufo holds in
  domain form: DBOS step memoization, transcripts, the ledger, counterfactual replay, candidate
  agent rows, this RFC's journal. A substrate rewrite re-litigates RFC 0011 to obtain properties
  already delivered, and single-log event sourcing is what forced ActiveGraph's single-writer
  contract.
- **Keep `graph_search` and fix its seeding.** Two query surfaces for one knowledge plane; zero
  usage evidence; the one dependent workflow is broken by the identity key it would need fixed
  anyway.
- **Human-gate every crossing.** At agent volume it converges on
  rubber-stamping, and the receipt then records a rubber stamp. Verification-first is cheaper
  and stricter — and the residual meter now measures instead of assumes.
- **Dreams-style regenerate-and-adopt as the whole answer.** The posture is right and §2 adopts
  it; a synthesis pass has no per-item provenance check, no audience, no per-item receipt — it
  cannot say *why* an item may cross or roll one item back. Keep the posture, add the ledger.
- **Files-in-git memory with PR review** (Letta's shape). The one validated merge mechanism —
  textual, and blind to audiences: git has no concept of "visible to Ana and not Ben," which is
  the property being sold. Revisit if the brain ever becomes single-tenant-per-repo.
- **CRDTs.** No shipped instance for knowledge, and for a reason: commutativity is exactly the
  property a contradiction lacks.
- **Wait for the platform control planes** (Agent 365, Fabric IQ). Authored ontologies and
  outside-in inventories; an external registry cannot enforce which member may see which fact.
  Interop, not substitution.
- **The RFC 0013 shape** (everything at once). Died of seven programs. Four organs, six units,
  two meters that can each cancel a unit.

## Open decisions

1. **Hash-chained records?** No vendor ships tamper evidence for agent audit and no standard
   demands it — and the EDPB now advises against personal data on immutable structures even
   hashed. If chained, chain non-personal fields only. Deferred with that constraint recorded.
2. **Step-up authentication for high-impact tiers.** A compromised Slack account is currently
   full approval capability.
3. **Cross-agent merge initiation** — who may propose an audience-pair grant: any member with
   access to both scopes, or owners only?
4. **The predetermined-change envelope** (AI Act Art. 43(4)): per-ref bounds as the artifact a
   standing delegation is measured against. Needs a consumer before it lands.
5. **Cross-tenant evidence pooling** — the bundle stays portable and workspace-anonymous by
   construction; pooling itself is a per-tenant DPA conversation, never a default.
