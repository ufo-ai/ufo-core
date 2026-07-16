---
rfc: 0008
title: "RFC — durable recovery without redoing completed work"
status: implemented
date: 2026-07-06
---

# RFC — durable recovery without redoing completed work

**Status:** implemented. **Scope:** crash recovery at three linked granularities — sub-turn
(per model-round / per tool-call), `wide_*` per-entity fan-out, and a browser task resuming on a new
runner via CDP reconnect. **Verdict:** today a crash mid-turn re-runs the *entire* turn from scratch
(every model round, every tool call, every fanned-out entity, every browser action); DBOS already
gives us the substrate to skip completed work, but the turn is deliberately collapsed into **one**
DBOS step, so the substrate is never used sub-turn. The fix is a turn-engine restructure resting on
one seam metalcraft had and selfhost dropped: a per-tool `idempotency_key`.

| Level | What crashes | Redone today | After this RFC |
|---|---|---|---|
| **1 sub-turn** | any turn mid-run | every model round + tool call re-executed | completed rounds/tools replay from DBOS; only the in-flight step onward re-runs |
| **2 `wide_*`** | parent fan-out turn | all N entities respawned; prior children orphaned + double-billed | completed entities skipped; only the remainder spawns |
| **3 browser** | browser task turn (new runner) | full session lost, task restarts blind | reconnect live CDP session + last-stable-page checkpoint; only a dead session restarts |

---

## 0. What DBOS actually gives (verified against the vendored SDK)

`dbos>=2.24` (`pyproject.toml`). The memoization contract, read from source, not docs:

- A `@DBOS.workflow` **may contain many `@DBOS.step`s**. On entering a workflow the context's
  `function_id` is set to 0 (`dbos/_context.py:235`); every step, transaction, or child-workflow
  call increments it (`_context.py:179`), so steps get sequential ids **in invocation order**.
- Each step's result is keyed by `(workflow_id, function_id, step_name)` in the system-DB
  `operation_outputs` table. First run: no record → the body executes, then `record_operation_result`
  persists the output (or the serialized exception) (`dbos/_core.py:1757-1787`). Replay/recovery:
  `check_operation_execution` finds the record and **returns the deserialized output / re-raises the
  recorded error without re-executing the body** (`_core.py:1789-1815`).
- **Recovery re-runs the workflow body from the top under the *same* `workflow_id`.** Each step it
  re-reaches replays from `operation_outputs`; only steps with no record execute for real. This is
  precisely "resume from a new runner" — DBOS re-dispatches the crashed workflow to a live peer
  (`spec.md:270`).
- **Determinism requirement:** the workflow body must re-issue the same sequence of step calls so
  `function_id`s line up. Anything non-deterministic (model output, network, clock, RNG, a changing
  DB read) must live **inside a step**, whose frozen output then drives the same branch on replay.
- **Serialization:** step *outputs* and workflow *inputs* must serialize to the system DB
  (`serialize_value`, `_core.py:1783`). Step *inputs* are **not** persisted — they are passed
  in-process by the re-executing workflow body — so a step may take live, unserializable deps
  (a sandbox handle, a model client) as arguments; only what it *returns* must serialize.
- **`preemptible=True` + cancellation:** on `DBOSWorkflowCancelledError` the step records **no**
  outcome and re-runs on resume (`_core.py:1771-1774`). A hard crash likewise leaves the in-flight
  step unrecorded → re-executed on recovery.

**The current turn.** `turn_workflow` (`@DBOS.workflow`, `queue.py:234`) awaits a single
`_execute_turn` (`@DBOS.step(preemptible=True)`, `queue.py:103`). That step is the whole turn — load
context, create the sandbox (`queue.py:150`), run `engine.run()` (`queue.py:192`) with all its model
rounds and tool dispatches. Being one step, it holds exactly one `function_id`. **DBOS therefore
memoizes only turn completion; a crash before that re-executes the entire turn.** Everything below
follows from moving that boundary inward.

## 1. Current durability model — precise

Durable state is keyed by `turn_id`, which doubles as the DBOS `workflow_id`
(`turn_id_for`, `records.py:27-29`). The ledger is keyed one level finer, by *run attempt*
(`ledger_id_for(workspace, turn, dimension, attempt)`, `records.py:32-37`; `attempt = DBOS.workflow_id
or turn_id`, `queue.py:188`), so a crash-recovery re-run (same `workflow_id` → same attempt) collapses
onto the same ledger rows while a park→resume (fresh `workflow_id`, `jobs.py:129`) bills each partial
burn separately. The run guard on `turn` — `running_attempt`, `dispatch_enqueued_at` (`tables.py:77-78`)
— lets exactly one execution claim a turn (`_mark_running`, `engine.py:247-275`).

| Concern | Survives a crash | Lost + redone on recovery |
|---|---|---|
| Turn lifecycle / terminal | yes — `_commit` retries until durable (`engine.py:579-604`) | — |
| Token ledger | yes — per-attempt, replay-idempotent upsert (`accounting.py:165-186`) | — |
| Parked→resume headroom | yes — advisory stamp + fresh attempt (`jobs.py:63-133`) | — |
| Single-owner claim | yes — `running_attempt` CAS (`engine.py:255-275`) | — |
| **Model rounds** | **no** — accumulated only in memory (`engine.py:316-320`) | **every round re-called** — real tokens re-spent; ledger under-counts the crashed partial |
| **Tool-call results** | **no** — collected in-memory per round (`engine.py:315`) | **every tool re-dispatched** — side-effecting tools (egress, writes) **re-applied** |
| **Transcript** | at turn end only (`_persist_transcript`, `engine.py:229,737`) | a crashed turn writes none; `_prior_messages` self-exclusion (`engine.py:280-286`) protects prior turns, not this one |
| **`wide_*` per-entity results** | **no** — `rows` in memory, file written after `gather` (`delegation.py:69-70`) | **all N entities respawned**; prior children orphaned (§3) |
| **Browser session / page** | **no** — lease + `BrowserSession` in-memory in `BuaSurface` (`backend.py:26-38`) | **session dropped**, task restarts from its prompt |

The through-line: **all intra-turn progress is in-memory inside the one step.** The durable layer is
excellent at the *boundaries* and blind *between* them.

## 2. The idempotency seam — the foundation for all three levels

metalcraft computed, per tool call, `idempotency_key = f"{turn_id}/{name}/{call_id}"` for any tool
flagged `idempotent`, and threaded it into the external side-effecting call as a dedup header
(producer `metalcraft_agent/turn_engine.py:435-441`; consumers
`tools/connector_tools.py:70-71`, `tools/mcp.py:94-95`, `tools/broker.py:98-99`). selfhost dropped
the seam with those tools. (selfhost's `turn.idempotency_key`, `tables.py:76`, is unrelated — it dedups
inbound *message identity* at admission, not tool calls.)

**Why it is the foundation, and why it must land first.** The key is stable only across executions
that see the **same** `call_id`. `call_id` is the model-assigned `ToolUseBlock.id` from the stream
(`engine.py:449-454`). On a whole-turn re-run the model is re-invoked and assigns *new* ids — so the
key is meaningless until the model round that produced it is itself memoized. Hence the strict order:

1. **Memoize model rounds as steps** → freezes `call_id`s and the round structure.
2. **Memoize tool dispatches as steps, keyed by the now-stable `call_id`** → per-tool replay; a
   side-effecting tool additionally carries the `idempotency_key` for external at-most-once.
3. Levels 2 and 3 are special cases of (2): a `wide_*` spawn and a browser navigation are both
   side-effecting tool work whose replay safety rests on this key.

**Producer (core).** `ToolDef` gains `side_effecting: bool` (default `False`). In `_dispatch`
(`engine.py:472`), before invoking a side-effecting tool, core folds
`idempotency_key = f"{self.turn.id}/{call.name}/{call.id}"` onto the `ToolContext` copy the handler
receives (`ctx.idempotency_key`). Deterministic reads (bash read, file read, search, `read_page`)
declare `side_effecting=False` and get none — they are safe to re-execute, so DBOS step memoization
alone suffices.

**Consumer (sdk).** Re-export `ctx.idempotency_key` on the `selfhost.sdk.tools.ToolContext`. A tool
performing egress or an external write passes it as the provider's dedup header (Composio/MCP/broker
all accept one, per the metalcraft consumers above). Both ends land together; the sample extension's
probe asserts a side-effecting tool receives a stable key across a simulated replay and a read tool
receives none.

| Tool class | Example | `side_effecting` | Recovery guarantee |
|---|---|---|---|
| Deterministic read | `bash` read, `read_page`, `search_web` | no | DBOS step memoizes result; re-execution (if unrecorded) is harmless |
| Idempotent-by-key external | connector POST, MCP call, `share_file` | yes | step memoizes; a crash before the record → re-run dedups at the provider via the key |
| Local durable write | `memory_update` | yes | step memoizes; the write's own `on_conflict` / content id dedups a re-run |

## 3. Level 1 — sub-turn checkpointing

**Restructure.** Promote the turn body from one step to a workflow with per-round and per-tool steps.
`turn_workflow` stays the `@DBOS.workflow`; `engine.run()` becomes the workflow body; the two
non-deterministic / side-effecting units become steps:

- `_stream_once` (`engine.py:396`) — one model round → `@DBOS.step`. Output `(text, tool_calls,
  usage)` is serializable. Memoizing it freezes the round and its `call_id`s.
- `_dispatch` (`engine.py:472`) — one tool call → `@DBOS.step`, `function_id` ordered after its round.
  Output `ToolResultBlock` is serializable.
- Compaction's model call (`compaction.maybe_compact`) — also a step (non-deterministic model output).

**What stays out of steps (re-run each recovery, must be idempotent):**

| Setup work | Why not a step | Why re-running is safe |
|---|---|---|
| `_load_turn` (`queue.py:239`) | returns live rows; cheap | deterministic read |
| `carrier.create` sandbox (`queue.py:150`) | returns a **live handle** — not serializable | create-or-attach is idempotent by `conversation_id` (`spec.md:280`, "workspace is truth, container is cache") |
| `_enforce_spend` (`engine.py:376`) | a **live** cap decision that must reflect *current* spend | re-deciding on recovery is desired, not a bug; it may park, which re-runs anyway |
| `_mark_running` claim (`engine.py:247`) | the single-owner CAS | idempotent under the same attempt |

Recovery then re-enters the workflow, replays every recorded round/tool step (skipping real work),
re-runs setup idempotently, and resumes at the first unrecorded step. **A 40-round browser turn that
crashes at round 39 replays 38 rounds from `operation_outputs` and continues.**

**Invariant to keep correct — replay self-exclusion.** The transcript's self-exclusion
(`_prior_messages`: `stored.seq >= self.turn.seq → ()`, `engine.py:280-286`) protects a replay from
reading its own prior write. Level 1 keeps the transcript written **once at turn end** — the in-memory
`messages` list is rebuilt deterministically from memoized round/tool step outputs on replay, so no
intermediate transcript row is needed and the invariant is untouched. Do **not** persist per-round
transcript rows: that would create a second answer to "what is this turn's transcript" mid-run and
break self-exclusion.

**Cancellation.** The turn body keeps its `preemptible` character: on `cancel_workflow` the in-flight
step records nothing (`_core.py:1771-1774`) and the engine's existing `CancelledError` path
(`engine.py:236-239`) still bills and preserves inbound. Cancellation now stops at a step boundary
rather than tearing down the whole turn's uncommitted work.

**Scope boundary — crash recovery, not park/resume.** Step replay is keyed to the `workflow_id`.
Crash recovery re-dispatches the *same* `workflow_id`, so steps replay — this is the win. Park→resume
deliberately mints a *fresh* `workflow_id` for per-attempt billing (`jobs.py:129`; `records.py:32-37`),
so it does **not** replay and re-executes from the top. That is acceptable (a parked turn re-decides
against caps) and is the reason the per-tool `idempotency_key` matters even post-Level-1: it is the
only guard against a resumed attempt re-applying an earlier attempt's external effects. Making resume
reuse the `workflow_id` (to also skip completed work) trades against per-attempt billing — an open
decision (§6).

**Effort / risk — high; this is the turn engine.** Concentrated risks: (a) step-output size — an
offloaded tool result or a screenshot block is large; the offload already caps text at
`MAX_TOOL_RESULT_CHARS` (`engine.py:81,525`), but image blocks (`engine.py:550-555`) must be
blob-referenced before the step returns, not serialized inline into the system DB; (b) determinism —
`_enforce_spend` and setup must not branch the step sequence differently on replay; (c) `app_version`
pinning (`DBOS_APP_VERSION`, `records.py:24`) already guarantees recovery runs the same code, so the
step graph is stable. The declared-but-unbuilt `turn_step` table (`spec.md:51`) is **not** needed —
DBOS's `operation_outputs` *is* the durable step log; materializing a second one would duplicate it.

## 4. Level 2 — `wide_*` per-entity recovery

**Today.** `_wide_research` / `_wide_browse` read an entities file, then `asyncio.gather` a
semaphore-bounded pool of `ctx.spawn` calls **in memory inside one tool call**, collecting `rows` and
writing the workspace JSON only after every child returns (`delegation.py:50-73` research,
`:95-118` browser; bound `DEFAULT_SUBAGENT_FANOUT=8`, `MAX_*_ENTITIES=128`).

**Are children durable / dedup'd today? Verified: durable individually, NOT dedup'd across a parent
re-run.** `Subagents.spawn` mints `conversation_id = uuid4()` (**random**, `subagents.py:105`),
derives `turn_id` from it, admits conversation+turn rows, and enqueues via `DBOSClient.enqueue_async`
with `workflow_id = str(turn_id)` (`subagents.py:106-108,241-249`). Each child is thus a durable
top-level DBOS workflow that recovers on its own `workflow_id` if *its* runner dies. But because the
`conversation_id` is freshly random every call, a **parent** re-run computes entirely new child ids —
so it respawns all N entities as fresh children while the previously-spawned children keep running,
unawaited, **billing twice** and never collected. (metalcraft used `DBOS.start_workflow` proper child
workflows, `dbos_turns.py:129`; selfhost's `DBOSClient` enqueue from inside a step is the regression.)

**Fix — deterministic child identity + idempotent admit.** Make the child's `conversation_id`
derive from the parent turn and the entity, not from `uuid4()`:

```
child_conversation_id = uuid5(NAMESPACE_URL, f"{parent_turn_id}/{entity}")   # deterministic
child_turn_id         = turn_id_for(workspace_id, child_conversation_id, 1)  # already deterministic
```

Then: (a) `_admit`'s inserts become `INSERT … ON CONFLICT DO NOTHING` (idempotent); (b) `_enqueue`
with the same `workflow_id` is deduplicated by DBOS (the workflow already exists); (c) `_await_terminal`
(`subagents.py:251-261`) polls the same `turn_id` — a child that already finished (via its own DBOS
recovery) returns **immediately**. So on a Level-1 replay of the `wide_*` tool step, the fan-out
re-runs but **reconnects to completed children instead of respawning them; only the remainder is
new work.** The children's own durable terminals *are* the per-`(turn, entity)` checkpoint — no
separate results table or file is required. `spawn` grows a `dedup_key: str | None` parameter so
`browser_task`/`spawn_subagent` (which *want* a fresh child each call) keep `uuid4()`, while `wide_*`
passes the deterministic key.

| Failure | Recovery |
|---|---|
| **Child crashes** | its own DBOS workflow recovers under its `turn_id` (Level-1 step replay inside the child); the parent's `_await_terminal` poll is unaffected |
| **Parent crashes** | the `wide_*` tool step re-runs; deterministic ids + idempotent admit + poll-existing → done entities skipped, in-flight children awaited, only unstarted entities spawned |

**Doctrine reconciliation.** "Event-fired work must not re-fire on events it caused" (CLAUDE.md) is
honored: the fan-out is driven by the tool call, not by a child-completion event, and the recovery is
a deterministic re-derivation, never an event cascade. The bound is preserved — the re-run
re-establishes the same semaphore (`delegation.py:56,101`) and the same `MAX_*_ENTITIES` cap; polling
an already-done child costs one DB read, not a spawn. A running child cap should also gate
concurrently-recovering parents, but at `concurrency=1` per conversation partition (`queue.py:55-60`)
a single parent re-runs at a time.

## 5. Level 3 — browser task resume via CDP reconnect

**Today.** The browser surface is per-turn and lazy: `BuaSurface._open` mints a `CdpLease` on first
use and connects a fresh `BrowserSession` (`backend.py:29-38`); the lease + session live **only in
memory** in a per-turn `WeakKeyDictionary` (`tools.py:25,94-107`); `aclose` releases both and is
drained at turn end (`engine.py:244-245`). The action loop (`BrowserComputer.run`, `computer.py:97-163`)
runs a batch of actions then screenshots — **no progress is checkpointed**. On crash the whole session
is gone and, worse, a hard crash skips the `finally` drain so a hosted (browserbase) session is
**orphaned** — never released, still paid.

**Two providers, two crash shapes** (seam: `CdpProvider`/`CdpLease`, `browser.py`; `CdpProviderSpec`,
`ext/manifest.py:201-213`):

| Provider | Endpoint lifetime | On crash |
|---|---|---|
| `sandbox-cdp` (core default, `SandboxCdpProvider`) | `BROWSER_CDP_URL` **outlives every turn**; `StaticCdpLease.aclose` is a no-op (`browser.py:70-84`) | endpoint still live → reconnect trivially; Chrome likely still holds the tabs |
| browserbase (extension, mints per-turn) | fresh hosted session per turn, released at turn end | session may be alive (unreleased) and reattachable within its TTL, or already reaped |

**The correctness coupling with Level 1.** A browser action mutates external page state; its result
(a screenshot + DOM) is meaningful *only against the session that produced it*. So memoizing browser
action steps (Level 1) is sound **only if the exact live session is reattached** on recovery. If the
session is dead, the memoized "clicked X, now at Y" results describe a page the fresh browser is not
on. Therefore browser tasks get a **coarser** recovery unit than pure compute:

1. **Reattach succeeds** → the memoized action steps stay valid; the turn resumes at the first
   unrecorded step against the same page. Fastest.
2. **Reattach fails** (session reaped / endpoint dead) → the browser action steps are **invalidated**
   (their memoized results discarded) and the task restarts from its **last stable-page checkpoint**,
   re-grounding the model with "you were at URL X, having done Y."

**Seam changes.**

- `CdpLease` gains `token() -> str` — a **serializable, durable** reattach handle (the browserbase
  session id; for `sandbox-cdp`, the static URL). `CdpProvider` gains
  `reattach(token: str) -> CdpLease` — reconnect if live, raise `SessionGone` if not.
- **Persist the token** keyed by the (browser subagent) `turn_id`/`conversation_id` on first lease,
  clear on release. The browser extension owns this via its scoped store — the existing seam, no core
  change; `BuaSurface` reads it on `_open` and calls `reattach` before minting fresh.
- **Progress checkpoint.** After each stable navigation, the browser extension writes
  `{url, step_index}` to that same durable record (batch, at a settle boundary — `computer.py:122`).
  The resumed model reads it as its re-grounding prompt. This is the browser analogue of the
  workspace-is-truth model (`spec.md:280`): the *page* is cache, the *checkpoint* is truth.

**Release discipline must change — the crux.** Release a hosted session **only** on a *terminal* turn
(done/failed/cancelled) or via a **TTL sweep for orphans**, never on a mere step re-run or a mid-run
teardown. A crashed non-terminal turn's session is *kept* for reattach within a TTL. This mirrors the
`SandboxReaper` (`jobs.py:136-176`) exactly: a batch-at-interval sweep, keyed by durable identity, that
reaps a hosted session whose turn is terminal or whose lease TTL has lapsed — with the same
"re-check not-active immediately before destroy" guard (`jobs.py:158-176`) so a resume in flight is
never reaped out from under itself. This is a new job the browser extension registers (`JobSpec`),
plus the durable token record; both ends land together.

## 6. Doctrine fit, sequence, and open decisions

**Doctrine.**

- **Root-cause, not retry-masking** (CLAUDE.md hot paths). None of this is a retry, sleep, or timeout
  bump. It is *finer durable checkpointing on the existing durable substrate* — moving the DBOS
  memoization boundary from the turn edge inward. The only retries touched are the legitimate external
  ones (model/egress).
- **DBOS is the substrate** (`spec.md:30`). We use exactly what DBOS provides (§0); we invent no
  parallel checkpoint store — `operation_outputs` is the step log, children's terminals are the
  per-entity log, the extension's scoped store is the browser log.
- **Both ends or neither.** Each seam ships producer + consumer together: `side_effecting` +
  `idempotency_key` (core folds it, sdk exposes it, a side-effecting tool consumes it); `spawn`'s
  `dedup_key` (producer `wide_*`, consumer `Subagents.spawn`); the browser token/checkpoint (written on
  lease, read on reattach, reaped by the sweep).
- **Stays out of core.** `wide_*` and the browser session/token/sweep are entirely extension-side over
  existing seams (`ctx.spawn`, `ctx.store`, `CdpProvider`, `JobSpec`). Core changes are confined to the
  turn engine (Level 1) and the two-field `ToolDef`/`ToolContext` idempotency seam — both genuinely
  core (the loop and its tool contract).

**Sequence** (the idempotency seam underpins all three, so it is first):

| Phase | Delivers | Depends on |
|---|---|---|
| **1a** | `side_effecting` on `ToolDef`; core folds `idempotency_key`; sdk exposes it; a side-effecting tool consumes it. Probe asserts stability. | — |
| **1b** | Model rounds + tool dispatch + compaction become DBOS steps; turn body becomes the workflow. Crash-recovery replay proof: a turn killed mid-round resumes, tokens not re-spent, no tool re-applied. | 1a (stable `call_id`) |
| **2** | Deterministic child identity + idempotent admit + `spawn(dedup_key=…)`. Proof: a `wide_*` turn killed after k/N entities resumes and completes only N−k. | 1b |
| **3** | `CdpLease.token`/`CdpProvider.reattach`; durable token + page checkpoint; terminal/TTL release sweep. Proof: a browser task killed mid-session reconnects and continues; a dead session restarts from checkpoint; no orphaned hosted session. | 1b |

**Open decisions.**

1. **Resume vs step-replay billing.** Should park→resume reuse the `workflow_id` (so it also skips
   completed steps) at the cost of per-attempt ledger granularity (`records.py:32-37`), or stay
   fresh-attempt (redo work, precise billing)? Default: stay fresh-attempt; Level 1 scoped to crash
   recovery.
2. **Cross-attempt external dedup.** The `{turn_id}/{name}/{call_id}` key is stable within an attempt
   but not across a fresh-attempt resume (new `call_id`s). Do side-effecting tools need a stronger key
   (arg-content hash) for cross-attempt at-most-once, or is "resume may re-apply" acceptable given
   parks happen at round boundaries (`engine.py:299`)?
3. **Screenshot/image step outputs.** Confirm large image blocks are blob-referenced before a tool
   step returns (never serialized into `operation_outputs`); pick the size threshold.
4. **Browser reattach TTL.** How long to hold a crashed turn's hosted session for reattach before the
   sweep reaps it — bounded against paid-session cost.
5. **`turn_step` table.** Delete the declared-but-unbuilt `turn_step` from `spec.md:51` (DBOS's
   `operation_outputs` is the step log), or keep it as a domain-level projection for operators?
