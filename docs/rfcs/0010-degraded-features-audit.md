---
rfc: 0010
title: "Degraded-features audit — what was silently crippled"
status: accepted
date: 2026-07-06
---

# Degraded-features audit — what was silently crippled

Status: **audit, 2026-07-06.** A three-front sweep (core runtime, all 26 extensions, the
memory/derivation pipeline), cross-checked against `spec.md`, `docs/salvage.md`, `docs/plan.md`, and
`~/src/metalcraft`. Verdicts: **CRIPPLED** = an intended capability is silently absent/degraded and a
reader assumes it works; **FLAG** = a real gap needing an owner call; items marked *in flight* are
already being fixed.

## The pattern (and the honest part)

One root cause dominates: **the "an extension job/tool reaches a model" seam was never landed**, so
every capability needing an in-job model pass shipped deferred — the condenser, the graph extractor's
prose tier, and by extension the whole page→fact/curation half of memory. The metered `ModelClient`
unit now in flight (#25) fixes the seam; the condenser (#26) and gbrain Tier-B follow.

The rest are two kinds: **spec-vs-impl reductions** (prose in `spec.md` promises a capability the code
renamed or thinned instead of building) and **silent bugs** (mostly in sandbox-side metering). Several
were recorded (condenser #11, gbrain Tier-B, hardening-backlog items) — but they accumulated, and the
`spec.md`/`salvage.md`/`plan.md` prose still advertises things that don't exist. Those docs are fixed
as part of closing this out.

## CRIPPLED — ranked by severity

| # | Finding | Where | Intended → Actual | Status |
|---|---|---|---|---|
| **1** | **e2b carrier runs agent egress unconfined, yet is a selectable backend** | `extensions/e2b/selfhost_ext_e2b.py:116-139` | Carrier applies proxy + CA + sentinel→key swap (as Docker does) → `create()` ignores `spec.proxy`/`run_token`; raw model key in-sandbox, egress unmetered + un-allowlisted, and it does **not** fail loud | **NEW — security. Fix or fail-loud-disable now** |
| **2** | **Non-streaming in-sandbox model calls were entirely unmetered** | `sandbox/proxy/server.py` | Invariant #3: *every* in-sandbox model call hits the ledger → meters only by teeing the SSE stream; a single-JSON completion (or a stream without `include_usage`) is free | **PARTIALLY FIXED** — `SseTokenUsage` now recovers a non-streaming single-JSON body's top-level `usage` and meters it under `sandbox_tokens`; an OpenAI stream sent without `stream_options.include_usage` stays unmetered (recovering it needs a request-body rewrite or rejecting the call — **owner design decision**, untouched) |
| **3** | **Memory condenser: specced + planned, never built** | now `extensions/memory/.../condenser.py` | curation loop (page→fact extraction + cosine-cluster consolidation → `semantic` summary + supersede) → **BUILT**: `FactDeriver` (2nd memory `page_change` consumer) + `MemoryConsolidator` (hourly), both metered via `ctx.model`, feeding recall's `superseded_by`/`semantic` machinery | **RESOLVED** (#26, main 3028765) |
| **4** | **`auto` model policy is a static alias, not task-class routing** | `models/registry.py:47-51` | spec §Model abstraction "routes by task class" → returns one deploy-wide id; no task classification exists | **NEW — spec reduction** |
| **5** | **gbrain Tier-B (LLM prose extraction) shipped disabled → graph empty on real prose/JSON** | `extensions/knowledge_graph/.../store.py` | typed relations from prose → **wired**: Tier-B sends bounded page body through `ctx.model`, validates edges against the bounded vocabulary (fail-loud on out-of-vocab), writes with model confidence; Tier-A untouched, Tier-B skipped when no model | **RESOLVED** (#25) |
| **6** | **`spend_cap.dimension` axis does not exist — caps are dollar-only** | `accounting.py:311-322`, `tables.py:114-135` | spec: cap by scope, **dimension**, window → no dimension column; can't cap "egress ≤ N" or "tokens only" | **NEW — spec reduction** |
| **7** | **Per-tool-call / per-step metering does not exist** | `accounting.py` (no `record_tool_call`); `loop/engine.py` dispatch | spec §Accounting "every model **and tool** call meters in the same commit as the step" → one aggregated row per turn-attempt (mitigant: a live `CostTick` frame per model call) | **NEW — spec reduction** |
| **8** | **`agents.propose_change` promotes only the prompt** | `governance.py:28-56`; `records.py:82-88` | spec: proposal of **(prompt, skills, tool grants)** → `AgentChange` carries only `new_prompt`; self-improvement can't propose skills/grants | **NEW — spec reduction** |
| **9** | **`pause_and_wait` timer / cooldown auto-resume is fiction** | `tools/builtins.py:858-865,652-663` | description promises "until a timer expires… API cooldowns" → no scheduler reads `wait_minutes`; resumes on next inbound exactly like `ask_user`; `metadata` dropped | **NEW — desc overstates** |
| **10** | **mcp was a bare JSON-RPC POST, not the Streamable-HTTP transport its source used** | `extensions/mcp/selfhost_ext_mcp.py` | initialize handshake + SSE + `Mcp-Session-Id` + tools pagination → **wired**: swapped to the fastmcp `StreamableHttpTransport` client the connectors pack already ships, so the handshake, session id, SSE framing, and `tools/list` `nextCursor` pagination are fastmcp's; spec-compliant + FastMCP servers now work and large catalogs paginate; `arguments`/result re-bounded to 1 MiB next to the call | **RESOLVED** |
| **11** | **sites `deploy_website`/`publish_website` return an unreachable `localhost` URL** | `extensions/sites/.../tools.py:130,152,159` | "a route the user can reach" → hardcoded `http://localhost:<port>` (sandbox-internal); the `serve_url` carrier seam was deleted (even E2B lost its public path) | **NEW** |
| **12** | **In-sandbox model calls priced with the core-only table + core digest** | `accounting.py`, `sandbox/proxy/server.py`, `serve.py` | merged/overridden provider prices → `record_sandbox_tokens` omits the `pricing` arg, mis-prices + mis-attributes any non-core rate | **FIXED** — `record_sandbox_tokens` takes `pricing: Pricing = CORE_PRICING`; `EgressProxy.pricing` is wired from `registry.pricing` at serve, so a sandbox call is priced by the merged table and stamped with its digest, exactly as the turn path |
| **13** | ~~coding subagent's PR-review path is dead~~ **MISDIAGNOSED — path works** | `extensions/coding/.../manifest.py`, `coding/SKILL.md:41`, `loop/queue.py:131` | `code-review` is a **main-agent** skill by design (`coding/SKILL.md:41`: "do not spawn a coding subagent to review a PR … load `code-review` and follow it directly"); the main agent holds all tools incl. the connector trio, gated only by a runtime GitHub connector grant (normal auth). Granting the trio to the coding profile would be wrong. Real gap was only: untested + a wrong read of the skill's agent-agnostic "report to parent" wording. Pinned by a test; no code fix | **NOT A BUG** |
| **14** | **eval_harness green-washing FIXED; its LLM-judge confirmed unwired (owner call)** | `capability.py`, `harness.py`, `judge.py:29-79` | **Green-wash fixed**: a failed `web_dependent` case behind an infra substring (`"timeout"`,`"429"`,`" 500"`) is now a distinct `excluded=True, passed=False` state — out of both the suite verdict and the pass rate (`pass_rate = passed / scorable`), never counted a pass; a false-positive match now UNDER-counts (drops a real failure from scoring) instead of green-washing it, and every excluded case is visible in the report for audit. **LLM-judge still dead**: `rubric_pass`/`JudgeLeg` has ZERO production callers (only `tests/test_eval_harness.py`) — no capability case or scorer wires it; its scripted fallback keyword-matches each criterion's quoted terms and FAILS on unmet criteria (it does not auto-pass) — the real defect is that no grader ever invokes it. | **FIXED (green-wash) + FLAG (judge: owner wire-or-delete)** |

## FLAG — real gaps needing an owner call

- **RESOLVED — `triggers` deleted, replaced by the `page_change` hook.** The prose seam never existed as a Manifest point. Its data-plane half is now the `page_change` hook event, fired by a core batched cursor-runner that replays each changed source page to a consumer's hook off that extension's own cursor (the memory indexer and knowledge-graph extractor ride it). The trigger→invocation half (a platform event that fires a turn) stays a non-goal — that is `invoke` from a route or job, not a distinct seam. The `triggers` prose is gone from `spec.md`, `plan.md`, and `contracts.md`.
- **RESOLVED — `superseded_by` + `semantic` tier now have a producer.** The condenser's `MemoryConsolidator` (#26, main 3028765) writes `semantic` summaries and stamps `superseded_by` on the clustered originals, so recall's `superseded_by IS NULL` drop and its `semantic` decay-exemption (`store.py:426,470,223,253`) now fire. (The `episodic` class still has no producer — no consumer needs one today; add with its first writer.)
- **Durable attachment delivery is best-effort-once, not at-least-once** — `ext/surface.py:483-495`: `attach` sits inside the `reply_ref is None` guard, so a retry after `post` succeeds skips `attach` and marks delivered; shared files drop permanently on first attach failure.
- **Writeback registration is not atomic with turn enqueue** — `ext/surface.py:302-320` (same class as the admission-orphan hardening item).
- **OpenAI provider likely broken for its default `gpt-5`** — `models/openai.py:152` sends `max_tokens` on Chat Completions; modern reasoning models require `max_completion_tokens` and 400 otherwise. **Verify against the live API.**
- **Egress is priced $0 and un-cappable** (`accounting.py:229-258`) — with #6, a runaway granted-API caller can't be gated.
- **Sandbox wire-metering is fire-and-forget + dimension-collapsing** (`sandbox/proxy/server.py`); **(a)** a `credential` slot with `dimension=None` puts the real key on the wire unmetered (`rules.py:119-126`) — documented-intended, **open owner decision**; **(b)** the $0-escapes-caps case is now moot for any *contributed/overridden* slug — with #12 the sandbox path prices against the merged table, so a provider-priced slug bills at its real rate; only a *truly unknown* model (in no provider's table) still bills $0, the deliberate never-wedge invariant in `usage_priced_micro_usd` (`accounting.py:126-129`). Also unchanged: a `sandbox_tokens` row is keyed per turn (not per model), so several distinct in-sandbox models in one turn accumulate the correct dollar total but attribute the `model` label to the first — a reporting quirk, not a mis-bill; splitting it needs a keying/migration change out of this unit.
- **`reasoning_effort` is deploy-wide, not per-agent** (`queue.py:187`); core model keys come only from env, never a `credential` slot (`registry.py:82-91`); `requires` resolves only `cdp_providers`/`search_providers` (`serve.py:453`) — all narrower than the prose.
- **exa** `search_vertical` silently falls back to web for `image`/`video`/`shopping` (`selfhost_ext_exa.py:35,86`); `SearchResults.answer` is never populated. **skill_create** has no in-chat remove path (can't add past the 100 cap). **slack** drops an under-cap attachment whose upload throws (`surface.py:423`). **browser** `browser_task.timeout_minutes` is a phantom arg (`delegation.py:45`); **sites** subagent `preload_skills`/`extended_context` are inert. **e2b exec** drops the salvage-mandated dead-pooled-connection handling (`salvage.md:64`).

## Doc integrity — stale prose to correct (both-ends)

`salvage.md:38` (condenser "becomes the core Condenser"), `:44` (pause_and_wait "deferred to extensions"), `:62/64` (serve_url, e2b dropped-connection); `plan.md:48/64/66` (pipeline/Condenser + sample condenser); `spec.md` §Model abstraction (`auto` routing, per-agent reasoning, keys-from-slots), §Accounting (per-tool metering), §Workspace (spend_cap dimension), §Extension (propose_change skills/grants). Also the inverse drift: `BUILTIN_TOOLS` ships 17 tools vs the spec's 8 — `glob`/`grep`/`list_skills`/`load_sessions`/`connect_account`/`pause_and_wait` each contradict a `salvage.md` drop/fold/defer decision.

## Verified SOUND (checked, not cripples — so we don't re-flag)

Compaction (real summarize, fail-loud), background subagents (real child turns), result-bound/offload, grant scoping (default-deny at CONNECT + confused-deputy guard), park/`SpendResume`, caps at inbound+per-round+resume across all scopes, Fernet credentials, every hub frame has a producer, the sandbox reaper, sentinel swap + workspace-mount guard, S3 blob, folder source + `CorePageFeed`, hooks firing, **recall richness** (full gbrain port — multi-query interleave, RRF+cosine, decay, diversity, tail leg), **sources/sync** (cursor/claim/commit, snapshot-vs-delta, loud skips), `self_improvement`, and the fail-loud backend extensions (docker/redis_hub/index_default/turbopuffer/embed_openai/openrouter).

## Recommended order

1. **Now / safety + money:** #1 (e2b — fail-loud-disable immediately, wire later). #12 is **FIXED** and #2 **PARTIALLY FIXED** (single-JSON body now metered; OpenAI-no-`include_usage` is an owner call), closing the priced-wrong and single-JSON sandbox billing holes; the remaining metering FLAGs (dimension=None, per-model attribution) stay owner decisions. #14 eval green-washing is **FIXED** (infra-excluded is now a non-passing state out of the pass rate); its unwired LLM-judge remains an owner wire-or-delete **FLAG**.
2. **DONE:** the metered `ModelClient` (#25) → the condenser (#3/#26) → gbrain Tier-B (#5) — all landed on main.
3. **Spec-vs-impl reductions (build or amend spec):** #4 auto-routing, #6 spend_cap dimension, #7 per-tool metering, #8 propose_change, #9 pause_and_wait. (The `triggers` seam is RESOLVED — deleted for the `page_change` hook.)
4. **Extension fixes:** #10 mcp transport is **RESOLVED** (fastmcp Streamable-HTTP client, reusing the connectors pack's); #11 sites URLs and the Tier-2 flags remain. (#13 was misdiagnosed — no fix; pinned by a test.)
5. **Doc corrections** (small, do alongside each fix, or upfront to stop the bleeding).
</content>
