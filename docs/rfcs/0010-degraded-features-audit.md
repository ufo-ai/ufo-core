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
| **2** | **Non-streaming in-sandbox model calls are entirely unmetered** | `sandbox/proxy/server.py:298-311,365-398` | Invariant #3: *every* in-sandbox model call hits the ledger → meters only by teeing the SSE stream; a single-JSON completion (or a stream without `include_usage`) is free | **NEW — billing hole** |
| **3** | **Memory condenser: specced + planned, never built** | no `pipeline.py`; `Manifest` has no `condenser` field; `salvage.md:38`, `plan.md:64/66` | curation loop (inbox→fact extraction + cosine-cluster consolidation → `MemorySummary` + supersede) → absent; memory accretes raw agent-authored facts forever | *in flight* (#25→#26) |
| **4** | **`auto` model policy is a static alias, not task-class routing** | `models/registry.py:47-51` | spec §Model abstraction "routes by task class" → returns one deploy-wide id; no task classification exists | **NEW — spec reduction** |
| **5** | **gbrain Tier-B (LLM prose extraction) shipped disabled → graph empty on real prose/JSON** | `extensions/knowledge_graph/.../store.py:14-25,209` | typed relations from prose → only wiki/`[[a::b]]`/`@`/`#` syntax parsed; `confidence` always `1.0` | *in flight* (#25) |
| **6** | **`spend_cap.dimension` axis does not exist — caps are dollar-only** | `accounting.py:311-322`, `tables.py:114-135` | spec: cap by scope, **dimension**, window → no dimension column; can't cap "egress ≤ N" or "tokens only" | **NEW — spec reduction** |
| **7** | **Per-tool-call / per-step metering does not exist** | `accounting.py` (no `record_tool_call`); `loop/engine.py` dispatch | spec §Accounting "every model **and tool** call meters in the same commit as the step" → one aggregated row per turn-attempt (mitigant: a live `CostTick` frame per model call) | **NEW — spec reduction** |
| **8** | **`agents.propose_change` promotes only the prompt** | `governance.py:28-56`; `records.py:82-88` | spec: proposal of **(prompt, skills, tool grants)** → `AgentChange` carries only `new_prompt`; self-improvement can't propose skills/grants | **NEW — spec reduction** |
| **9** | **`pause_and_wait` timer / cooldown auto-resume is fiction** | `tools/builtins.py:858-865,652-663` | description promises "until a timer expires… API cooldowns" → no scheduler reads `wait_minutes`; resumes on next inbound exactly like `ask_user`; `metadata` dropped | **NEW — desc overstates** |
| **10** | **mcp is a bare JSON-RPC POST, not the Streamable-HTTP transport its source used** | `extensions/mcp/selfhost_ext_mcp.py:115-234` | initialize handshake + SSE + `Mcp-Session-Id` + tools pagination → none; spec-compliant/FastMCP servers error or under-expose | **NEW** |
| **11** | **sites `deploy_website`/`publish_website` return an unreachable `localhost` URL** | `extensions/sites/.../tools.py:130,152,159` | "a route the user can reach" → hardcoded `http://localhost:<port>` (sandbox-internal); the `serve_url` carrier seam was deleted (even E2B lost its public path) | **NEW** |
| **12** | **In-sandbox model calls priced with the core-only table + core digest** | `accounting.py:261-308` | merged/overridden provider prices → `record_sandbox_tokens` omits the `pricing` arg, mis-prices + mis-attributes any non-core rate | **NEW — silent bug** |
| **13** | **coding subagent's PR-review path is dead** | `extensions/coding/.../manifest.py:23-36` | `code-review` skill calls `call_external_tool` → not granted to the profile, not `subagent_default` | **NEW** |
| **14** | **eval_harness green-washes real failures + its LLM-judge is unwired** | `capability.py:83-91`, `harness.py:68-91`, `judge.py:29-35` | a failed `web_dependent` case flips to `passed=True` on a substring match (`"timeout"`,`"429"`,`" 500"`); the LLM-judge is never invoked, its fallback auto-passes | **NEW — undermines eval evidence** |

## FLAG — real gaps needing an owner call

- **`triggers` Manifest point does not exist** (`ext/manifest.py`); `spec.md:131/317`, `plan.md:48/66` list it as a live seam. Data-plane half ≈ `sources`+`jobs`+`PageFeed`; the trigger→invocation (platform event fires a turn) half has no equivalent. Build it or delete the prose.
- **`superseded_by` + `episodic`/`semantic` memory tiers are consumers with no producer** — recall filters/decays on them (`extensions/memory/.../store.py:426,470,223,253`), nothing ever writes them (the condenser would). Dead until #26.
- **Durable attachment delivery is best-effort-once, not at-least-once** — `ext/surface.py:483-495`: `attach` sits inside the `reply_ref is None` guard, so a retry after `post` succeeds skips `attach` and marks delivered; shared files drop permanently on first attach failure.
- **Writeback registration is not atomic with turn enqueue** — `ext/surface.py:302-320` (same class as the admission-orphan hardening item).
- **OpenAI provider likely broken for its default `gpt-5`** — `models/openai.py:152` sends `max_tokens` on Chat Completions; modern reasoning models require `max_completion_tokens` and 400 otherwise. **Verify against the live API.**
- **Egress is priced $0 and un-cappable** (`accounting.py:229-258`) — with #6, a runaway granted-API caller can't be gated.
- **Sandbox wire-metering is fire-and-forget + dimension-collapsing** (`sandbox/proxy/server.py:379-406`); a `credential` slot with `dimension=None` puts the real key on the wire unmetered (`rules.py:119-126`); an unknown/mispriced model bills $0 and escapes caps (`accounting.py:126-129`).
- **`reasoning_effort` is deploy-wide, not per-agent** (`queue.py:187`); core model keys come only from env, never a `credential` slot (`registry.py:82-91`); `requires` resolves only `cdp_providers`/`search_providers` (`serve.py:453`) — all narrower than the prose.
- **exa** `search_vertical` silently falls back to web for `image`/`video`/`shopping` (`selfhost_ext_exa.py:35,86`); `SearchResults.answer` is never populated. **skill_create** has no in-chat remove path (can't add past the 100 cap). **slack** drops an under-cap attachment whose upload throws (`surface.py:423`). **browser** `browser_task.timeout_minutes` is a phantom arg (`delegation.py:45`); **sites** subagent `preload_skills`/`extended_context` are inert. **e2b exec** drops the salvage-mandated dead-pooled-connection handling (`salvage.md:64`).

## Doc integrity — stale prose to correct (both-ends)

`salvage.md:38` (condenser "becomes the core Condenser"), `:44` (pause_and_wait "deferred to extensions"), `:62/64` (serve_url, e2b dropped-connection); `plan.md:48/64/66` (pipeline/Condenser + trigger seam + sample condenser/trigger); `spec.md:131/317` (`triggers`), §Model abstraction (`auto` routing, per-agent reasoning, keys-from-slots), §Accounting (per-tool metering), §Workspace (spend_cap dimension), §Extension (propose_change skills/grants). Also the inverse drift: `BUILTIN_TOOLS` ships 17 tools vs the spec's 8 — `glob`/`grep`/`list_skills`/`load_sessions`/`connect_account`/`pause_and_wait` each contradict a `salvage.md` drop/fold/defer decision.

## Verified SOUND (checked, not cripples — so we don't re-flag)

Compaction (real summarize, fail-loud), background subagents (real child turns), result-bound/offload, grant scoping (default-deny at CONNECT + confused-deputy guard), park/`SpendResume`, caps at inbound+per-round+resume across all scopes, Fernet credentials, every hub frame has a producer, the sandbox reaper, sentinel swap + workspace-mount guard, S3 blob, folder source + `CorePageFeed`, hooks firing, **recall richness** (full gbrain port — multi-query interleave, RRF+cosine, decay, diversity, tail leg), **sources/sync** (cursor/claim/commit, snapshot-vs-delta, loud skips), `self_improvement`, and the fail-loud backend extensions (docker/redis_hub/index_default/turbopuffer/embed_openai/openrouter).

## Recommended order

1. **Now / safety + money:** #1 (e2b — fail-loud-disable immediately, wire later), #2 + #12 + the metering FLAGs (sandbox billing holes), #14 (eval green-washing — our evidence is overstated until fixed).
2. **In flight:** the metered `ModelClient` (#25) → the condenser (#3/#26) → confirm gbrain Tier-B (#5) works on prose.
3. **Spec-vs-impl reductions (build or amend spec):** #4 auto-routing, #6 spend_cap dimension, #7 per-tool metering, #8 propose_change, #9 pause_and_wait, the `triggers` seam.
4. **Extension fixes:** #10 mcp transport, #11 sites URLs, #13 coding PR-review grant, and the Tier-2 flags.
5. **Doc corrections** (small, do alongside each fix, or upfront to stop the bleeding).
</content>
