---
rfc: 0038
title: "Skill retrieval — routing cards, tiered visibility, and retrieval proven in shadow"
status: implemented
date: 2026-08-19
---

# Skill retrieval — routing cards, tiered visibility, and retrieval proven in shadow

> Every skill's name and description renders into the system prompt on every turn, and every saved
> skill's file bytes are decoded on every turn to produce that list. At the current cap (100 per
> agent) both are affordable; at 5,000 they are fatal — a 182k-token system prompt that exceeds the
> window of every model but one, and 58 MB of Postgres reads plus 0.39 s of event-loop CPU per
> turn. This RFC splits skills into two tiers with different cache lives: the **deploy tier**
> (core, pack, generated) stays in the system prompt — and a member tier below a 4,000-char fold
> lists there too — while past the fold the prompt is byte-identical across workspaces, so at the
> sizes where invalidation matters a skill save never invalidates a cached prefix; the **member tier** (an agent's saved skills) renders
> into the turn message under a char budget — pins first, a full catalog while it fits, bare
> names past that, and retrieval choosing which cards keep full descriptions — all over **routing
> cards** (name + description + depends, projected to columns so no turn ever decodes file bytes). `skill_search` is the escape hatch for the tail; `preload_skills`, which
> already exists, is how a parent hands a member skill to a subagent. The vector retrieval leg
> ships **shadow-first** — logged against the turn's actual `load_skill` calls, promoted only when
> the eval shows the always-fresh in-memory lexical leg missing. The cap rises to 5,000 last,
> as the assertion that no surface still grows with it.

## Current state

One flat index, one full-corpus read, both per turn.

| Site | What it does |
|---|---|
| `SkillRegistry.index()` (`core/src/ufo/skills/runtime.py:281`) | Every top-level skill's `(name, description)`, no cap, no ranking |
| `render_skill_index` (`core/src/ufo/loop/prompts/render.py:98`) | Renders them as `<available_skills>` into the **system prompt** |
| `UserSkillStore.load_all` (`extensions/skill_create/ufo_ext_skill_create/store.py:128`) | `SELECT name, content` for every row of the bound agent — `content` is base64 JSON of every file — then base64 + YAML + pydantic per skill |
| `turn_runtime_skills` → `merged_with` (`core/src/ufo/loop/queue.py:434`) | Calls `load_all` on **every turn** and merges the parsed result |
| `SkillObjects._rows` (`extensions/skill_create/ufo_ext_skill_create/manifest.py:172`) | Calls `load_all`, then `object_page` paginates in Python afterwards |

Measured against a synthetic 5,000-skill corpus (two files, 11.7 KB/skill) through the real code:

| Cost | At 5,000 skills |
|---|---|
| `<available_skills>` block | 727,111 chars ≈ **182k tokens**, every turn |
| `load_all` read | **58.7 MB** per turn (no projection, no limit; per-skill ceiling is `MAX_SKILL_TOTAL_BYTES` = 1 MiB, so the worst case is ~6.8 GB) |
| `load_all` parse | **0.39 s** of sync base64 + YAML + pydantic on the one event loop, per turn — stalling every other stream and turn |
| Cache tax (opus-5: `cache_read` $0.50/Mtok, `cache_write_1h` $10/Mtok) | ~**$0.09/turn** to re-read the index; ~**$1.82** per skill save, which rewrites the block and invalidates the cached prefix of every conversation of that agent |

The window math is the hard failure. `ANTHROPIC_CONTEXT_WINDOW = 200_000` for every Anthropic model
but `claude-opus-5` (`core/src/ufo/models/catalog.py:17`); compaction fires at 150k and
`Compaction._tokens` counts **messages only, never the system prompt** — so the compactor believes
it has room while the request is already 182k. Every turn ends in an unrecoverable overflow after
one wasted forced summarize (`core/src/ufo/loop/engine.py:1896`). On opus-5's 1M window it runs,
but the index alone puts every request past the 200k long-context threshold, doubling input price
on the whole turn.

Today's registry is 25 skills / 4,070 description chars (mean 163). The observed heavy member
corpus is ~500 skills — call it the 99th percentile — which already sits 12× past a
full-description catalog budget, so ranked visibility is the daily path for the heaviest real
agents, not a stress case. At 500 the failure is a tax rather than an overflow: ~18k tokens of
system prompt, ~5.9 MB and ~39 ms of decode per turn, and a cache invalidation per save.
`MAX_USER_SKILLS_PER_AGENT = 100` is the only thing holding any of it, and it holds by preventing
the feature, not by bounding it; 5,000 stays as the stress bound the proofs run at.

## What the field does

Four checked-out implementations, all of which hit this wall; none dumps a flat list.

| Repo | Mechanism | What we take |
|---|---|---|
| `codex` (`codex-rs/ext/skills/src/render.rs`) | Catalog rendered under a budget — `min(2% of context window, 10k tokens)`, 8k-char fallback — with a degradation ladder: full lines → name-only plus fair-share description chars → omit and warn. Paginated `list`/`read` tools (20/page, 512 KB cap). `SkillScope::{User,Repo,System,Admin}`. Six cheap selectors — all **lexical**, no embeddings (weighted lexical, fielded BM25, char n-gram, multi-query, RRF fusion, routing-card) — run in **shadow mode** on every turn, scored against real usage before any ships (`dynamic_skill_selector/`, `shadow_selection_experiment/`) | The budget as a first-class constant; scope as the pinning axis; shadow-first promotion; the hint that lexical alone may suffice for routing cards |
| `gbrain` (`skills/RESOLVER.md`, 81 skills) | Frontmatter `triggers:` is the authoritative routing signal. At 200+ skills, `functional-area-resolver` collapses skill-per-row tables into two-level dispatchers — A/B-proven over naive compression | The empirical wall: flat routing dies around 200 rows / 25 KB, and hierarchy beats compression |
| `openclaw` (`src/skills/loading/skill-prompt-limits.ts`) | 150 skills / 18,000 chars, binary-searched degradation (full → shortened → omitted) with a member-visible note; per-skill exposure flags | The bounded-catalog-with-note shape for a member-controlled corpus — full visibility while it fits, honest truncation when it doesn't |
| `qm` (`src/skills/`) | Skill packs materialized into the sandbox by index hash and tree hash | Nothing for retrieval; its concern is delivery, which `load_skill` already covers |

And the in-repo precedents, which carry more weight than any of the above because they ran on this
stack against this failure class:

| Precedent | Lesson taken |
|---|---|
| Memory recall (`extensions/memory/ufo_ext_memory/store.py`) | Fused legs + a **query-level** cosine floor (the per-row floor failed cross-corpus and was replaced); a candidate pool with near-dup shingle drop and diversity caps; ranking arithmetic in a worker thread |
| Memory's `_untail_leg` (`store.py:946`) | An index-backed leg is stale within the indexer's tick; a just-committed row needs a fresh leg or it is unfindable at the moment the member most expects it |
| Memory's index job (`manifest.py:617`) | Derivation is a `JobSpec` interval batch with `candidates=owner_candidates(_items_awaiting_index)` — rows whose settle marker is unset — never work fired inline on the write |
| `recall_hook` (`manifest.py:365`) | `user_prompt_submit` hooks run **serially** and gating hooks fail closed (`ext/loader.py:786`); anything on that path owns a soft timeout and swallows everything, and each addition stacks wall-clock on every member turn |
| The memory-poisons-routing finding (PR #1977 review) | Injected recall content measurably biased skill routing. An injected "skills you can load" block is a stronger version of the same hazard — the model over-loads what the block suggests. Suggestion bias is a first-class eval axis, not a hypothetical |

Deliberately not taken:

- **A runtime degradation ladder for the deploy tier.** codex and openclaw need one because users
  install arbitrary plugins into the prompt. Our deploy tier is CI-bounded; a breach is a failing
  gate, not a truncated prompt. The member tier gets the ladder, because that corpus is not ours.
- **A hand-maintained dispatch map.** gbrain's `RESOLVER.md` is a second answer to "what routes
  here". Our description already *is* the routing trigger — the skill gate requires a ≤50-word
  description beginning `Load when`, naming member intent. That is the routing card, already
  enforced, already the thing scored. No new frontmatter.
- **`$skill` mention syntax.** Members here express intent in conversation and the agent calls
  `load_skill`; a keyword syntax is not a member action in this product.
- **Two-level dispatchers.** Retrieval replaces them. Kept in the registry of approaches as the
  fallback if shadow scoring shows retrieval underperforming a hierarchy at 5,000.

## Decisions

| Fork | Decision |
|---|---|
| The two tiers | **System prompt = deploy-controlled. Turn message = member-controlled.** The deploy tier is everything that reaches the registry outside the user-skill merge: core, pack, boot-generated (`model-catalog`), and per-turn generated (`spawn_catalog_skill`, `setup_skill`) — the defining property is that no member action changes it. A member tier below the fold joins the same `<available_skills>`; past the fold the rendered system prompt is byte-identical across every workspace and every save, and the member tier renders into the turn message, exactly where `recall_hook` puts memory. |
| The routing card | A skill's routing datum: `name`, `description`, `depends` — projected to columns on `user_skill` at save, where the parse already happens. A card and a `RuntimeSkill` are **two concepts, not two forms of one**: the card is what routing, search, and closure walk; the `RuntimeSkill` is the mounted workflow, and for a member skill it exists only inside a load. This is memory's own split (index chunk vs memory row). |
| What a turn reads | The card projection — one SELECT of three short columns (~1.2 MB at 5,000 rows, vs 58.7 MB) — because closure resolution needs every name and `depends` reachable. File bytes are read for exactly the rows a `load_skill` names, one row each. `load_all` is deleted. |
| Closure and the hot path | `closure()` walks cards. `_reseed_loaded_skills` (`engine.py:618`), which re-expands every load on every round, resolves against the in-memory cards — no DB read, no body parse. Bodies are materialized once per `load_skill`, after the walk. |
| The member block | Rendered into the member-turn message under `SKILL_MEMBER_BLOCK_MAX_CHARS = 16_000` (≈4k tokens — 2% of the 200k window, codex's norm) with `SKILL_LINE_MAX_CHARS = 200`. A ladder, filled in order: **pins** (full lines; `user_skill.pinned`, `MAX_PINNED_USER_SKILLS = 10` = 12.5% of the block, the cap's derivation) → **full catalog** while every card fits (~75 skills) → **retrieved top-k as full lines, every remaining skill as its bare name** (~600 skills stay fully visible at ~21 chars a name — the whole observed 99th percentile) → past that, a count line: `N more skills; skill_search finds them.` The names rung is the openclaw rung the ladder was missing: the agent always knows a skill exists, so retrieval chooses which cards carry descriptions, never which skills are visible — lower stakes, and bare names are the least suggestible form the block can take. Below `SKILL_PROMPT_FOLD_MAX_CHARS = 4_000` of card lines (~20 typical skills) the member tier folds into `<available_skills>` itself and no block renders: the common case lists exactly like a deploy skill, and per-turn repetition in the message costs more than the occasional save's cache invalidation at that size — codex and openclaw both list small sets in the prompt under a budget. The ladder engages only past the fold. |
| Retrieval legs | The lexical leg scores the **in-memory cards** — always fresh (a card saved this turn is in the projection), no embed call, no new await on the serial gating-hook path; scoring arithmetic off-loop like memory's `_shortlist`. The vector leg (index-backed, `owner_kind = "skill"` over the existing `IndexBackend` seam, one chunk per card) ships **shadow-only** and is promoted per the shadow gate below. If promoted, it takes memory's query-level cosine floor and the fresh lexical leg doubles as the tail. |
| Shadow gate | On member turns whose corpus exceeds the catalog budget, run both selectors, log what each would have injected, and score against the turn's actual `load_skill` calls — ground-truth labels for free (codex's design). The vector leg goes live only if it beats lexical-only on would-have-hit rate over the eval corpus; otherwise it is deleted, not parked. |
| Suggestion bias | An eval axis from day one: ablation cases run with and without the member block where correct behavior is loading *nothing* or loading a *deploy* skill the block did not suggest. A block that causes loads fails the gate regardless of its recall. |
| Index derivation | A `JobSpec` interval batch, `candidates` = rows where `indexed_digest IS NULL OR indexed_digest != digest`; `chunk_embed_upsert` one chunk per card; delete prunes the owner scope. Memory's exact pattern. Never inline on the save. |
| Subagents | The child's `{{skill_index}}` renders the same fold-aware index the parent's prompt carries: the deploy tier, plus a member tier small enough to fold. Past the fold, member skills reach a child the way they already do: `preload_skills` in the spawn payload (`queue.py:473`) — a model-facing field on skill-capable profiles (`ufo_ext_documents/subagent.py:42`), resolved through the merged closure, bodies rendered into the child's system prompt. The parent, who saw the member block, passes what the child needs. `skill_search` joins profiles that grant `load_skill`, via the existing tool-set mechanics. |
| Failure mode | Best-effort, like recall: soft timeout below the hook deadline, every error swallowed. Retrieval that fails costs the turn a suggestion, never the turn. |
| The cap | `MAX_USER_SKILLS_PER_AGENT` 100 → 5,000, last, once nothing grows with it. |
| The extension seam | `runtime_skills` (parse-everything) is replaced by a provider with two verbs: cards for the turn, one materialized skill by name. `skill_create` is the real consumer proving the seam, per the sample-extension doctrine. |

## Architecture

```
save (object apply) ──▶ user_skill row: content + card columns (name, description, depends, pinned, digest)
                                          │                                    │
                              interval JobSpec (indexed_digest ≠ digest)       │
                                          ▼                                    │
                              index chunks (owner_kind "skill")                │
                                          │                                    │
turn admission                            │                                    │
  ├─ card projection (one SELECT) ────────┼────────────────────────────────────┘
  │        │                              │
  ├─ system prompt ◀── deploy tier (core + pack + generated) + member tier below the fold — byte-identical above it, cached
  │        │                              │
  └─ member-turn message ◀── member block (≤ 8,000 chars):
           │                   pins ▸ full catalog while it fits
           │                   ▸ else: lexical leg (in-memory cards, fresh)
           │                           vector leg (index, SHADOW until promoted)
           │                           near-dup drop ─▶ top-k full lines
           │                           ▸ remaining as bare names, then "N more; skill_search"
           │
load_skill(name) ──▶ closure over cards ──▶ one row per member skill ──▶ parse ──▶ mount + inject
skill_search(query, limit ≤ 8) ──▶ same legs over cards ──▶ rows back, never bodies
spawn(…, preload_skills=[name]) ──▶ closure ──▶ bodies into the child's system prompt  (exists today)
```

The tiers answer different questions: the deploy tier is what the agent can always do; the member
block is what this turn is probably about. A skill in neither is reachable by `skill_search` then
`load_skill`, which is why bounding the tiers costs no capability — and why a retrieval miss
degrades to a search instead of a dead end.

## Evals — extending `evals/skill_loading/`

The existing rig is already the right instrument: each case sends one natural member message,
watches the conversation workspace for the `$UFO_HOME/skills/<name>/SKILL.md` mounts `load_skill` produces,
and settles at the first observed mount with an expected-present verdict over a `forbidden` set
(`evals/skill_loading/runner.py`) — recall@1 with distractors, cheap because the turn cancels at
the first mount. Today it only exercises pack skills against the deploy index. Five rig changes
make it cover the member tier; each is small because the verdict machinery stays untouched.

**Rig changes**

| Change | What and why |
|---|---|
| Member-skill seeding | `SkillLoadCase` gains `member_skills: tuple[SkillFixture, ...]` — (name, description, body) triples saved to the case's agent through the real object-apply path before `admit`, torn down after (the skill sibling of `forget_workspace_memory()`; skills are agent-scoped, so an un-deleted fixture poisons the next case). The runner's exclusion gate becomes pack set ∪ seeded names — today `case.expected not in loadable` would exclude every member case. |
| Corpus regimes | A cancelled turn has no transcript, so the route cannot be read from calls — it is proven by construction. Each case declares its regime via the seeded corpus: **catalog** (≤~75, every card full), **names** (~500 — the observed 99th percentile; target visible only as a bare name outside top-k), **retrieval** (thousands, target scoring into top-k), **tail** (thousands, target constructed to miss both top-k and the names fill — reachable only through `skill_search`). A mount in a given regime is proof the corresponding path routed. Corpora anchor at 500 (real percentile, descriptions seeded from real saved skills where available) and 5,000 (stress). |
| No-load verdict | An `expects_no_load` case form: pass = the turn reaches its own terminal without mounting any watched skill. Today no-mount is always a failure; the bias family needs the inversion. |
| Block ablation knob | A runner switch rendering the member block on/off for the same case set, so bias is measured as a pass-rate delta on identical inputs rather than argued. |
| Distractor generator | Deterministic corpus builders: N topically-spread cards, plus a near-duplicate crowd (k+ paraphrases of one description) for the crowding proof. Seeded corpus digests join the case payload so the run digest moves when the corpus does. |

**Case families**

| Family | Shape | Pass | Gates |
|---|---|---|---|
| Member catalog | Corpus of ~20 seeded skills, query names one member intent, plausible siblings forbidden | Expected mount first | U2 |
| Member vs deploy | Query where a pack skill is correct and a similar-sounding seeded skill is the forbidden trap — and the mirror case | Right tier wins both directions | U2 |
| Bias ablation | `expects_no_load` cases (member asks something no skill covers) and deploy-correct cases, run block-on and block-off | No pass-rate regression block-on; a block that causes loads fails the gate regardless of recall elsewhere | U2, re-run U4 |
| Freshness | Turn 1 saves a skill in chat; the next member turn's query needs it | Mount without waiting on the index job — the in-memory lexical leg is the thing under test | U2 |
| Pins | Pinned target beside a near-duplicate unpinned distractor, corpus past the catalog budget | Pinned mount first | U2 |
| Subagent handoff | Query whose task routes through a skill-capable profile; the member skill reaches the child only via `preload_skills` | Child's mount observed in the shared conversation workspace | U2 |
| Names regime | Corpus of ~500; target outside top-k, visible only as a bare name | Expected mount first — the name alone routed | U2 |
| Retrieval regime | Corpus of thousands (past the names fill), target in top-k | Expected mount first | U4 promote gate |
| Tail regime | Same corpus, target constructed to miss top-k | Mount proves the agent read the truncation note and searched | U2 (search), U4 |
| Crowding | Near-duplicate crowd seeded around the target at 5,000 | Top-k not saturated by clones; expected still mounts | U5 |

Two to three cases per family, the existing rule. Queries are written from the seeded descriptions
the way the pack cases were written from real ones — naming member intent, never quoting the
description back (a query that echoes the card tests string matching, not routing).

**The offline selector harness** — the U4 gate needs selector comparison at corpus sizes and case
counts a live-agent eval cannot afford. A fixture corpus (cards + labeled queries, including the
near-duplicate crowd) is scored directly against both selectors — in-memory lexical, and fused
lexical + vector — as plain pytest over recall@k, no agent, no sandbox. The live half of the gate
is the U3 shadow log: would-have-injected vs the turn's actual `load_skill` calls on real member
turns. Promotion requires the vector leg to win **both** — the offline corpus and the shadow
would-have-hit rate — so a selector cannot be promoted on synthetic queries alone, and the
mid-turn-need question in Open is answered by the same shadow data.

## Units

Each lands with both ends and its own proof; ordered so the riskiest component is measured before
it is live.

| Unit | Change | Proof |
|---|---|---|
| **U1 — routing cards** | Card columns on `user_skill` (backfilled by one migration that parses existing rows); save writes them in the same transaction; the provider seam (cards + materialize-by-name) replaces `runtime_skills`; `closure()` walks cards; `load_skill` materializes per name; `SkillObjects._rows` and `member_detail` read the projection; `load_all` deleted | A 5,000-row test asserting one turn reads no `content` bytes; a drift test asserting a save cannot leave card columns disagreeing with the frontmatter; the reseed path exercised over cards |
| **U2 — tiers, block, search** | `index()` narrows to the deploy tier; member block rendered into the member-turn message (pins → catalog → top-k + bare names → count note); `skill_search` over in-memory cards (lexical, off-loop); `skill_search` granted to `load_skill`-capable profiles; the eval rig changes (seeding, regimes, no-load verdict, ablation knob) | Cache-invariance above the fold: past 4,000 chars of cards, saving, editing, pinning, and deleting a member skill leaves the rendered system prompt byte-identical; below it, two different small tiers render two different prompts by design. Eval families: member catalog, member vs deploy, bias ablation, freshness, pins, subagent handoff, tail regime |
| **U3 — index + shadow** | Index `JobSpec` with `indexed_digest` candidates; vector leg wired shadow-only, logging would-have-injected vs actual `load_skill` calls per turn | The job proven memory-style (save → tick → chunks; delete → prune); shadow metrics visible; the offline selector harness lands here; zero change to any rendered prompt |
| **U4 — promote or delete** | If shadow beats lexical-only on the eval corpus: the vector leg joins the block's fill with the query-level floor and near-dup drop. If not: the leg and the job are torn out whole | The promote gate: offline selector harness AND shadow would-have-hit rate; the retrieval-regime and tail-regime families live; bias ablation re-run |
| **U5 — the cap** | `MAX_USER_SKILLS_PER_AGENT` 100 → 5,000 | U1/U2 proofs re-run at 5,000; block and prompt within budget; the crowding family |

U1 before everything: no tier is affordable while routing decodes the corpus. U2 is the value ship
and is complete without retrieval — the catalog covers every corpus the current cap admits. U4 is
where the design's one open bet is settled by measurement, in either direction.

## Open

- **Does inbound text predict skill need?** Retrieval keys on the member's message, but a skill's
  relevance can emerge mid-turn from what the agent decides to do. The names rung caps the damage —
  up to ~600 skills every name is visible whatever retrieval ranks — and the shadow logs answer the
  question directly, scoring the inbound-keyed selection against loads made at any point in the
  turn. If mid-turn need dominates, the block stays names-plus-search and U4 resolves to *delete*.
- **Cross-agent scale.** A skill belongs to one agent; 50 agents × 5,000 skills is 250,000 rows of
  cards and chunks in one workspace's backend. A scale check before U5, not a design change.
- **Card-load ceiling.** The per-turn projection is ~1.2 MB at 5,000 rows. If a future cap makes
  that read the new hot-path cost, the projection gains its own bound and the closure walk moves
  to targeted lookups — noted so the ceiling is a decision, not a discovery.
