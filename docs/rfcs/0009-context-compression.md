---
rfc: 0009
title: "RFC — structured context compression, a pipeline not a summarize call"
status: implemented
date: 2026-07-06
---

# RFC — structured context compression, a pipeline not a summarize call

**Status:** implemented. **Scope:** the turn loop's window compaction
(`core/src/selfhost/loop/compaction.py`) — how the head of an over-window transcript is compressed
before the next model round. **Verdict:** selfhost today does compaction with **one freeform
summarize call** — walk back to an assistant boundary, render the head as `role: text` lines, ask
the model to "compress the conversation," splice the blob back as a `user` message. It works and it
is durably recorded, but it is a single stage where Claude Code runs a **pipeline** — threshold →
group-aware selection → structured multi-section summary → verbatim preservation + file/skill
re-injection → prompt-too-long recovery → deterministic reconstruction. The proposal replaces the
one call with a bounded, deterministic pipeline of the same shape, reusing seams selfhost already
has (the tool-result offload files, the before/after records, the usage metering).

Not in scope: the memory **condenser** (memory-item summarization, deferred — a different subject),
and durable sub-turn recovery (separate RFC, `0008-durable-recovery.md`).

---

## 0. Reference — Claude Code's compaction pipeline

Read from the reconstructed source at `tanbiralam/claude-code`, `src/services/compact/*`. The
`/compact` flow is a sequence of stages, not a single call.

**Stage 1 — threshold detection** (`autoCompact.ts`). The trigger is computed *relative to the
model's real context window*, not a flat constant, and reserves output room for the summary itself:

```
effectiveContextWindow = contextWindow - reservedTokensForSummary   // MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000
autocompactThreshold   = effectiveContextWindow - AUTOCOMPACT_BUFFER_TOKENS   // = 13_000
// fires when tokenUsage >= autocompactThreshold  → ~93% of the effective window
```

Other buffers: `WARNING_THRESHOLD_BUFFER_TOKENS = 20_000`, `MANUAL_COMPACT_BUFFER_TOKENS = 3_000`.
A circuit breaker (`MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3`) stops retrying after repeated failures
so an irrecoverable over-limit session does not burn API calls forever.

**Stage 2 — message preparation** (`compact.ts`). Three filters run before the summarize call:
image/document blocks are replaced with `[image]`/`[document]` markers (so the *compaction request*
does not itself overflow); `skill_discovery`/`skill_listing` attachments are stripped (re-injected
post-compact); `getMessagesAfterCompactBoundary()` excludes any prior compaction boundary.

**Stage 3 — group-aware selection** (`grouping.ts`). `groupMessagesByApiRound` returns
`Message[][]` — each inner array is one API round. "A boundary fires when a NEW assistant response
begins (different `message.id` from the prior assistant)"; because "every `tool_use` is resolved
before the next assistant turn," a round's tool_use/tool_result pairs never split across the
summarize/keep line. Selection operates on whole groups.

**Stage 4 — structured summary** (`prompt.ts`). `BASE_COMPACT_PROMPT` asks for **nine named
sections**, wrapped in `<analysis>`/`<summary>` tags, preceded by "Respond with TEXT ONLY. Do NOT
call any tools." (a tool call "would waste your only turn"). `formatCompactSummary()` strips the
`<analysis>` scratchpad before the summary re-enters context. The sections, quoted:

| # | Section | Instruction (verbatim) |
|---|---|---|
| 1 | **Primary Request and Intent** | "Capture all of the user's explicit requests and intents in detail" |
| 2 | **Key Technical Concepts** | "List all important technical concepts, technologies, and frameworks discussed." |
| 3 | **Files and Code Sections** | "Enumerate specific files and code sections examined, modified, or created… include full code snippets where applicable" |
| 4 | **Errors and fixes** | "List all errors that you ran into, and how you fixed them. Pay special attention to specific user feedback" |
| 5 | **Problem Solving** | "Document problems solved and any ongoing troubleshooting efforts." |
| 6 | **All user messages** | "List ALL user messages that are not tool results. These are critical for understanding the users' feedback" |
| 7 | **Pending Tasks** | "Outline any pending tasks that you have explicitly been asked to work on." |
| 8 | **Current Work** | "Describe in detail precisely what was being worked on immediately before this summary request…" |
| 9 | **Optional Next Step** | "List the next step… ensure that this step is DIRECTLY in line with the user's most recent explicit requests" |

Variants: `PARTIAL_COMPACT_PROMPT` (summarize only a sub-range), `PARTIAL_COMPACT_UP_TO_PROMPT`
(prefix summary for a continuation session — replaces "Optional Next Step" with "Context for
Continuing Work").

**Stage 5 — prompt-too-long recovery** (`compact.ts`, `MAX_PTL_RETRIES = 3`). If the summarize
request *itself* overflows, `truncateHeadForPTLRetry(...)` drops the oldest API-round groups until
the token gap is covered (falling back to dropping 20% of groups if the gap is unparseable) and
retries. Streaming has its own `MAX_COMPACT_STREAMING_RETRIES = 2`.

**Stage 6 — verbatim preservation + re-injection** (`compact.ts`, `postCompactCleanup.ts`). What
survives the boundary uncompressed: recent messages after the boundary; thinking blocks from recent
assistant turns; **recently-read files re-injected by content** (`POST_COMPACT_MAX_FILES_TO_RESTORE
= 5`, `POST_COMPACT_MAX_TOKENS_PER_FILE = 5_000`, `POST_COMPACT_TOKEN_BUDGET = 50_000`), excluding
files still visible in the preserved tail (`collectReadToolFilePaths()` + `FILE_UNCHANGED_STUB`
dedup — "don't re-inject content the model can already see"); **invoked skills re-injected**
(`POST_COMPACT_MAX_TOKENS_PER_SKILL = 5_000`, `POST_COMPACT_SKILLS_TOKEN_BUDGET = 25_000`, head
preserved on truncation); tool-discovery / agent-listing / MCP-instruction deltas.

**Stage 7 — reconstruction** (`compact.ts`, `buildPostCompactMessages`):

```
[boundaryMarker, ...summaryMessages, ...messagesToKeep, ...attachments, ...hookResults]
```

The `boundaryMarker` carries metadata — `preCompactTokenCount`, `preCompactDiscoveredTools`,
(partial only) `preservedSegment` UUIDs for message-chain relinking.

**Adjacent — micro-compaction** (`microCompact.ts`, `apiMicrocompact.ts`). A *lighter* pressure
response that clears **only tool-result content**, never whole messages. Two paths: API cache-edit
strategies (`clear_tool_uses_20250919`, `clear_thinking_20251015`; default max input `180_000`,
target `40_000`) that drop old tool results without invalidating the server cache prefix; and a
time-based fallback that rewrites cleared results to `"[Old tool result content cleared]"`.
Clearable tools: `FILE_READ, GREP, GLOB, WEB_SEARCH, WEB_FETCH, FILE_EDIT, FILE_WRITE, SHELL`;
`keepRecent = Math.max(1, config.keepRecent)` always keeps the most recent result.

**The through-line:** compaction is *staged* — cheap in-place tool-result clearing first, a
structured multi-section summary with explicit preservation/re-injection second, and a recovery path
if the summarize request overflows. selfhost collapses all of it into one call.

---

## 1. selfhost today — precise

One frozen-dataclass workflow, `Compaction` (`compaction.py:53-93`), constructed per turn in
`queue.py:167-172` with `client=model, model=resolved.model` (the **agent's own model**, not a
cheaper summarizer), the blob store, and the conversation id. The turn loop calls it in three places,
all `maybe_compact`:

| Call site | When | `force` |
|---|---|---|
| `engine.py:300-301` | before **every** model round | no |
| `engine.py:339-340` | before the forced-final round | no |
| `engine.py:367` | after a provider **context-overflow**, then retry once (`_stream_recovering_overflow`) | yes |

**Trigger** (`compaction.py:65-76`). Flat `COMPACTION_TRIGGER_TOKENS = 120_000` (`compaction.py:32`),
estimated at `CHARS_PER_TOKEN = 4` plus `IMAGE_TOKEN_ESTIMATE = 1_600` per inline image
(`_tokens`, `compaction.py:148-156`). Guards: a window `<= keep_messages` (`COMPACTION_KEEP_MESSAGES
= 8`, `compaction.py:33`) or with no assistant boundary to split on returns unchanged, so a `force`
call still no-ops safely.

**Selection** (`compaction.py:81-90`). `split = len - keep_messages`, then walk *backward* to the
nearest `assistant` message (`compaction.py:82-83`) so a tool_use/tool_result pair is not severed —
a crude, one-message-at-a-time version of Claude Code's round grouping. `head = messages[:split]`
is summarized; `tail = messages[split:]` is kept verbatim.

**Summary** (`_summarize`, `compaction.py:95-117`). The head is flattened to `"role: text"` lines
(`compaction.py:96`) via `_text` (`compaction.py:170-184`) — which renders `TextBlock`, tool-result
text, and `tool_use` as `name(args_json)`, and **drops all image content from the summarizer input**.
One model call: `system = COMPACTION_SYSTEM_PROMPT`, `max_tokens = 8_192`
(`COMPACTION_SUMMARY_MAX_TOKENS`), `reasoning = "off"`. The prompt is a **single sentence**
(`compaction.md:1`, via `render.py:31`):

> "Compress the conversation for continuation. Keep user requirements, decisions, tool results, open
> tasks, and unresolved errors verbatim where they matter. Drop repetition and transient wording.
> Return only the compacted context."

Empty output raises (`compaction.py:113`). The result is a freeform blob.

**Reconstruction** (`compaction.py:90`). `after = (Message(role="user", content=f"{COMPACTED_CONTEXT_PREFIX}{summary}"), *tail)`
— one user message (`"Compacted context:\n…"`) followed by the verbatim tail.

**Durability — a genuine strength.** The whole pre-compaction window (`before`) *and* the
summarized window (`after`) persist under
`conversations/<cid>/compactions/<n>/{before,after}.json.lz4` (`_write`, `compaction.py:137-146`;
`read_record`, `compaction.py:125-135`), so a pre-compaction fact survives verbatim even after the
live window is swapped. The summarize call's `Usage` is returned and folded onto the turn's
`usage_events` (`engine.py:301,340,370`), so it is **metered and billed** at terminal like any model
round.

**Related, already-built — the tool-result offload** (`engine.py:525-530`). At dispatch time a
non-error tool result over `MAX_TOOL_RESULT_CHARS = 1_048_576` (`engine.py:81`) is written to
`{WORKSPACE_DIR}/.tool-output/<call_id>.txt` and replaced in-context by a
`TOOL_RESULT_PREVIEW_CHARS = 2_000` preview + an `OFFLOAD_NOTICE` path (`engine.py:83-84,525-530`).
This is selfhost's structural analogue of Claude Code's micro-compaction — but it fires **at write
time on one result**, not as a response to window pressure, and it offloads *to a durable workspace
file* rather than clearing in-context. The compaction pipeline should build on it, not duplicate it.

**Limits (what the single call costs):**

1. **No structure.** One freeform blob. No enforced intent/decisions/files/errors/pending/next-step
   sections; quality varies call to call, and nothing downstream can read a field.
2. **No re-injection.** Files read and skills loaded before the boundary vanish unless they happen
   to sit in the 8-message tail. The offloaded `.tool-output` paths — already durable, re-readable
   references — are lost from context on the summarize side.
3. **Lossy summarizer input.** Images are dropped (`_text`), block structure and tool_use/result
   pairing are flattened to `role: text` — the summarizer sees a degraded transcript.
4. **No PTL recovery.** If the *summarize request itself* overflows, there is no head-drop retry; on
   the overflow-recovery path the forced compaction simply cannot shrink and the turn re-raises
   (`_stream_recovering_overflow`, `engine.py:368`).
5. **Flat, model-blind trigger.** `120_000` regardless of the agent model's real window; no reserve
   for the summary's own output; no per-model derivation.
6. **No micro tier.** The only response to pressure is a full summarize; there is no cheap
   "clear old tool-result previews first" step.

---

## 2. Gap table — Claude Code stage → selfhost

| Stage (Claude Code) | selfhost has? | Proposed |
|---|---|---|
| **Threshold detection** — per-model window − summary reserve − buffer (`autoCompact.ts`) | Partial — flat `120_000`, no reserve, no per-model (`compaction.py:32`) | Derive from the model's real window; reserve the summary's `max_tokens`; keep `force` |
| **Circuit breaker** on repeated compaction failure | No | Bound retries; a repeatedly-failing compaction fails the turn loud, never loops |
| **Pre-compact hook** (`compact.ts`) | Yes — `pre_compact`/`post_compact` observe hooks fire when compaction occurs (`compaction.py`); `HookChain` is a generalized per-event map over the CC hook taxonomy (`loader.py`) | Done — landed with the generalized hook taxonomy |
| **Message prep** — image→marker, attachment strip, boundary exclude (`compact.ts`) | Partial — `_text` drops images from input; single before/after boundary (`compaction.py:170-184`) | Explicit prep step: image markers, block-structure-preserving render |
| **Group-aware selection** by API round (`grouping.ts`) | Partial — walk back to one assistant msg (`compaction.py:82-83`) | Group by assistant-id boundary; select/keep whole rounds |
| **Structured multi-section summary** (`prompt.ts`, 9 sections) | No — one freeform sentence (`compaction.md:1`) | Typed `CompactionSummary` schema + sectioned prompt (§3) |
| **Verbatim preservation** of recent tail | Yes — `keep_messages` tail (`compaction.py:88-90`) | Keep; express as whole kept rounds |
| **File re-injection** (`compact.ts`, 5 files / 50k budget) | No | Re-inject **references** to recent `.tool-output` files + recently-read paths (workspace-is-truth: reference, don't re-inline) |
| **Skill re-injection** (`postCompactCleanup.ts`) | No | Carry loaded-skill names in the summary; re-mount is the model's next act |
| **PTL recovery** — drop oldest groups, retry (`compact.ts`, `MAX_PTL_RETRIES = 3`) | No — summarize overflow re-raises (`engine.py:368`) | Bounded head-group-drop retry before giving up |
| **Reconstruction** with boundary metadata (`compact.ts`) | Partial — summary msg + tail (`compaction.py:90`) | Render `CompactionSummary` + preserved rounds + reference block; keep before/after records |
| **Micro-compaction** — in-place tool-result clear (`microCompact.ts`) | Partial — write-time offload to file (`engine.py:525-530`) | Optional cheap tier: clear old offloaded previews before a full summarize (§4, phase 4) |
| **Durable before/after windows** | **Yes — a selfhost strength** (`compaction.py:137-146`) | Keep; also persist the typed `CompactionSummary` |
| **Metered summary call** | **Yes** (`engine.py:301`) | Keep; retries meter too |

---

## 3. Proposal — the compression pipeline

Keep `Compaction` a single frozen dataclass with one public entry (`maybe_compact`) and private steps
**in execution order** beneath it (house rule: one workflow, one file, read downward once). The
call sites in `engine.py` do not change — still `maybe_compact(...) -> (window, usages)`. Internally
`_compact` becomes the pipeline:

```
maybe_compact
 └─ _compact
     1. _select        group into API rounds; split into (head_rounds, kept_rounds)   [deterministic]
     2. _prepare       render head to summarizer input: block-structured, images→markers  [deterministic]
     3. _summarize     ONE metered model call → validated CompactionSummary            [external, bounded]
        └─ _recover_ptl on summarize overflow: drop oldest head rounds, retry ≤ N       [bounded]
     4. _references    collect recent .tool-output paths + read files to re-reference   [deterministic]
     5. _reconstruct   after = [summary render] + [kept_rounds] + [reference block]     [deterministic]
     6. _persist       before, after, AND the CompactionSummary                         [durable]
```

Only **step 3** is non-deterministic; everything around it is a pure function of its input. The one
model call's output is *validated into a typed schema* and then rendered deterministically — so the
same `CompactionSummary` always yields the same `after` window.

### The structured-summary schema

The summary **crosses a boundary** — it re-enters the model context and it persists in the `after`
record — so it is a `BaseModel`, validated at construction, fully serializable (house rule: BaseModel
= data that crosses a boundary; never `dict[str, Any]` across a module edge). Sections adapted from
Claude Code's nine, trimmed to what a headless multi-surface agent needs (no `<analysis>` scratchpad
survives into context; "All user messages" folds into intent):

```python
class FileRef(BaseModel):
    path: str            # workspace path — a .tool-output/<id>.txt or a read source
    why: str             # one line: what it is / why it mattered

class CompactionSummary(BaseModel):
    intent: str                      # 1. Primary request and intent, incl. user's own words
    concepts: tuple[str, ...]        # 2. Key technical concepts in play
    files: tuple[FileRef, ...]       # 3. Files/outputs examined or changed, + why
    errors: tuple[str, ...]          # 4. Errors hit and how each was fixed
    decisions: tuple[str, ...]       # 5. Decisions made / problems solved
    pending: tuple[str, ...]         # 6. Pending tasks explicitly requested
    current_work: str                # 7. Exactly what was in flight at the boundary
    next_step: str                   # 8. The single next action, in line with the latest request
    loaded_skills: tuple[str, ...]   # skills mounted pre-boundary, for the model to re-mount
```

Two ways to produce it (open decision, §5): a **structured/tool-output** model call validated
straight into `CompactionSummary` (enforces the shape, deterministic reconstruction, fits selfhost's
explicit-data doctrine); or a **tagged-text** prompt (Claude Code style) parsed into the schema
(model-agnostic, no structured-output dependency). Either way the *stored and re-injected* object is
the typed `CompactionSummary`, and `_reconstruct` renders it deterministically — the freeform blob
is gone.

### Preserved verbatim vs summarized

| Content | Fate | Why |
|---|---|---|
| Recent **kept rounds** (tail, whole API rounds) | verbatim | recency + intact tool_use/result pairs; today's `keep_messages` tail, expressed as rounds |
| **Last image / screenshot** block in the tail | verbatim | vision context is lossy to summarize; keep the freshest |
| **`.tool-output/<id>.txt` references** for recent offloaded results | **reference, not content** | the file is already durable in the workspace (`engine.py:526`); a path costs ~1 line, the model re-reads on demand (workspace-is-truth, `spec.md`) |
| Recently **read file paths** | reference | same — re-read beats re-inline; cheaper than Claude Code's 50k re-inject budget |
| **Head rounds** (everything older) | summarized into `CompactionSummary` | the whole point |
| **Images in the head** | dropped to `[image]` markers in summarizer input | they carry no text; already dropped by `_text` — make it explicit |

This is the one deliberate divergence from Claude Code: where CC **re-inlines** up to 5 files / 50k
tokens of content, selfhost **re-references** the durable workspace files. It is cheaper and fits
workspace-is-truth; the cost is one extra read round if the model needs the body. (§5 open decision.)

### Composition with what exists

- **Tool-result offload (`engine.py:525-530`).** The pipeline does not re-offload — it *harvests*
  the already-written `.tool-output/<id>.txt` paths for the reference block. The offload's preview +
  path line already in the tail rides through verbatim.
- **before/after records (`compaction.py:137-146`).** Unchanged contract; `_persist` additionally
  writes `CompactionSummary` (a third blob or a field on the `after` record) so an eval /
  trajectory reader gets the structured object, not just the rendered text.
- **Metering (`engine.py:301`).** `_summarize` still returns `Usage`; a PTL retry appends another
  `Usage`. All fold onto `usage_events` → billed at terminal. No new billing path.
- **Overflow recovery (`_stream_recovering_overflow`, `engine.py:361-374`).** Unchanged: it still
  calls `maybe_compact(force=True)`; the pipeline just shrinks harder (PTL recovery) before
  declaring the window unshrinkable and re-raising (`engine.py:368`).

---

## 4. Phasing

| Phase | Delivers | Risk |
|---|---|---|
| **1** | `CompactionSummary` schema + sectioned prompt (replaces `compaction.md:1`); `_summarize` returns the typed object; `_reconstruct` renders it; `_persist` stores it. Trigger/selection unchanged. Proof: a forced compaction yields a validated `CompactionSummary` with populated intent/next_step; before/after + summary round-trip through `read_record`. | Low — swaps the prompt + adds a typed parse; call sites untouched |
| **2** | Group-aware `_select` (assistant-id round boundaries) + `_prepare` (structured render, image markers) + `_references` (harvest recent `.tool-output` + read paths into the reference block). Proof: a window with a mid-round tail keeps whole rounds; an offloaded output stays re-readable by path after compaction. | Medium — touches selection; assert no tool_use/result split |
| **3** | Per-model threshold with summary reserve (replaces flat `120_000`); bounded `_recover_ptl` head-group-drop on summarize overflow; circuit-breaker so a repeatedly-failing compaction fails the turn loud. Proof: a summarize request forced to overflow drops oldest rounds and completes; an unshrinkable window fails loud, not silently. | Medium — must preserve the `force` no-op guard and metering |
| **4** *(optional)* | Micro tier: before a full summarize, clear old offloaded tool-result previews in place to their reference line (selfhost's `microCompact.ts` analogue, pressure-driven). Only if phase 1–3 leaves a real gap. | Low value if offload already covers it — gate on evidence |

---

## 5. Doctrine fit + open decisions

**Doctrine.**

- **Deterministic pipeline.** Six steps, fixed order; the sole non-determinism is one model call
  whose output is validated into `CompactionSummary` and rendered deterministically. Same summary →
  same `after` window. No hidden branching, no clock/RNG in the reconstruction.
- **Bounded, everywhere.** Summary output capped by `max_tokens`; PTL retries capped (≤ N,
  Claude Code's `MAX_PTL_RETRIES = 3`); selection bounded by kept-round count; the reference block
  bounded by a max-paths cap (references, not content — no per-file token budget needed). Every
  payload to the summarize call is bounded next to the call (house hot-path rule).
- **The summarize call is a real external model call** — already metered and billed
  (`engine.py:301`); the proposal keeps every `Usage` (including retries) folded onto the turn.
  Retry/backoff here is legitimate: it is against a *model* call's proven external uncertainty
  (overflow), not our own DB or code.
- **Fail loud.** Empty summary already raises (`compaction.py:113`); PTL exhaustion re-raises up the
  existing overflow path (`engine.py:368`); the circuit breaker turns a wedged compaction into a
  loud turn failure, never a silent loop.
- **Both ends or neither.** `CompactionSummary` ships producer (`_summarize`) and consumer
  (`_reconstruct` render + `read_record` for eval) in the same change; the reference block ships its
  writer (`_references`) and its reader (the model re-reading `.tool-output` via the file tools,
  already wired).
- **Stays in the loop, not new core surface.** Everything lives in `loop/compaction.py` (already
  core — it *is* the turn loop). No new manifest point, no extension seam, no config knob beyond the
  existing constants. The schema is internal to the loop.

**Open decisions.**

1. **Summary production mechanism.** Structured/tool-output call validated straight into
   `CompactionSummary` (enforced shape, deterministic render, fits explicit-data doctrine) **vs**
   tagged-text prompt parsed into the schema (model-agnostic, no structured-output dependency).
   Leaning structured, with a tagged-text fallback for models lacking it.
2. **Trigger derivation.** Keep flat `120_000`, or derive `window − summary_reserve − buffer`
   per-model like `autoCompact.ts`? Per-model is more correct but needs the model's window surfaced
   into `Compaction`. Leaning per-model in phase 3.
3. **Summary output budget.** Keep `8_192`, or raise toward Claude Code's `20_000`
   (`MAX_OUTPUT_TOKENS_FOR_SUMMARY`)? A richer structured summary wants more; it also costs more per
   compaction. Pick against measured summary truncation.
4. **Reference vs re-inline.** Confirm reference-only (workspace paths) over Claude Code's content
   re-inline (5 files / 50k). Reference is cheaper and fits workspace-is-truth; the cost is a re-read
   round when the body is actually needed. Re-inline only if evals show the extra round hurts.
5. **Micro tier (phase 4).** Is the write-time offload (`engine.py:525-530`) already enough, or is a
   pressure-driven in-place tool-result clear worth its complexity? Gate on evidence that windows go
   over-trigger *because of* stale tool-result previews the offload missed.
6. **Cheaper summarizer model.** Today compaction uses the **agent's own model**
   (`queue.py:169`). A dedicated small model would cut cost, but adds a second model dependency and a
   quality risk on the most information-dense call of the turn. Default: keep the agent model; revisit
   if compaction cost is material on the ledger.
