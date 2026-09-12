---
rfc: 0050
title: "Adaptive prefetch RAG — retrieve both corpora before the first model round"
status: implemented
date: 2026-09-19
---

# Adaptive prefetch RAG

> A member who asks a factual question pays two model rounds for the answer: one round calls a
> search tool, one round reads its result. The deploy already holds both retrieval seams at boot —
> the selected web backend and the workspace's own index — so this RFC spends them once, in
> parallel, on the `user_prompt_submit` hook, and folds what they found into the founding message.
> The router decides per inbound whether retrieval is worth it, the two legs fuse by reciprocal
> rank, and the block is rendered inside the untrusted wall. Every constant that shapes a model's
> reading of the block is an arm in `evals/rag-prefetch-routing.toml`.

## Current state

- The engine folds every `InjectContext` a hook returns into the founding user message
  (`INJECTED_CONTEXT`, `core/src/ufo/runtime/engine.py:311`), so a `user_prompt_submit` hook is the
  one seam that reaches the model before its first round. The chain deadline is
  `HOOK_TIMEOUT_SECONDS = 5.0` (`core/src/ufo/runtime/ext/hooks.py:28`).
- The deploy builds one web-search backend at boot from `[research] search_provider`
  (`_select_search_provider`, `core/src/ufo/serve.py:923`). Before this change it reached tools
  only; no hook could read it.
- A workspace's synced pages are already chunked and embedded in the selected index backend. No
  extension searched them: the memory provider answers memory items, not pages.
- `wall` (`core/src/ufo/harness/untrusted.py:23`) is the one definition of how content from outside
  the workspace's trust reaches a model as data.
- The gap: retrieval happened only when a model chose to call a tool, one corpus at a time, and
  paid a round trip for the choice.

## Proposal

### The seam

`ExtensionContext.search` (`core/src/ufo/runtime/ext/context.py:1236`) carries the deploy's web
backend to every handler, threaded `context_for` (`:2696`) → `turn_hooks`
(`core/src/ufo/host/ext/loader.py:1033`) → `HostEnvironment` (`core/src/ufo/host/assemble.py:111`)
→ `serve.py`. That is the whole core change: one field on an existing dataclass and the four call
sites that fill it. The index and the embedder already reached hooks, so the internal leg needs no
core change at all.

Everything else is the `rag` extension (`extensions/rag/ufo_ext_rag/`), registered in the dev and
hosted packs.

### The router

Retrieving on every turn is the failure this design starts from: an instruction to send an email is
answered by tools, and a retrieved block in front of it only competes with the work. Adaptive
retrieval — decide per query whether to retrieve — is the published answer.
[Self-RAG](https://arxiv.org/abs/2310.11511) trains the model to emit a retrieve/no-retrieve
reflection token; [Adaptive-RAG](https://arxiv.org/abs/2403.14403) routes by question complexity;
[FLARE](https://arxiv.org/abs/2305.06983) retrieves only when the generation's own confidence
drops. All three report the same thing: the decision is what keeps the quality while cutting the
retrieval cost.

This router (`route.py:121`) is a read of the text, not a model call. The prefetch exists to save a
round trip, and a classifier round trip spends exactly what it saves. The published decision
procedures assume a retrieval budget spread across a generation; this one has a 5-second hook
deadline and one shot before the first token.

| inbound | decision |
|---|---|
| ends in `?`, or opens with a question word | prefetch, one query per question, at most 3 |
| second-person request (`can you`, `please`) whose next two words hold an action verb | refuse |
| no question segment | refuse |
| under 12 or over 2,000 characters | refuse |

The refusal reason is logged as `rag.route`, so a routing decision is legible per turn.

### Fusion

Two legs run for every query: the web backend, and the page store read over the index
(`pages.py:56`). The page store is itself two legs — lexical for the terms the member used
verbatim, vector for the passage that says it in other words. Hybrid lexical-plus-dense retrieval is
the configuration [BEIR](https://arxiv.org/abs/2104.08663) found robust across domains where either
leg alone is not.

Nothing here compares scores. A BM25 score, a cosine distance, and a third-party web rank share no
scale, and a score is not comparable across queries either. Every ranked list contributes
`1 / (RRF_K + rank)` to the key it holds (`fusion.py:19`).
`RRF_K = 60` is the constant [Cormack, Clarke and Buettcher](https://dl.acm.org/doi/10.1145/1571941.1572114)
report ([PDF](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf)), where RRF beat Condorcet Fuse,
CombMNZ, and the best individual system it combined, with no tuning. The constant damps the head,
so one leg's top hit cannot carry a document the other leg never ranked.

Selection then fills a 4,000-character budget, at most 8 passages, after seating the top passage of
each leg that returned one: a question needing the web and the workspace together is exactly the
case a pure score order starves. What is selected renders best-first, because a model reads the
head of its context more reliably than its middle
([Liu et al.](https://arxiv.org/abs/2307.03172)).

Each leg is bounded at 3 seconds and the handler at 4 seconds, under the chain's 5. The hook is
`best_effort`, so a retrieval fault costs the injection and never the turn.

### Walled rendering

The block is text a web page controls, injected into the founding message with no member asking for
it — the exact shape [indirect prompt injection](https://arxiv.org/abs/2302.12173) exploits. It
renders through `wall`, so the passages arrive as data, the closing delimiter is escaped inside the
body, and the notice states they are not instructions. The preface adds what the wall cannot say:
nobody asked for this search, every figure, date and name carries its source in the sentence that
states it — a web passage by its address, a workspace passage by the record it names — the model
must still call tools for an action or for live state, and disagreeing passages are reported rather
than merged.

The attribution clause names those two forms because the looser "name the source you took each fact
from" left two cases split: over 20 samples each it held R02-internal-fact at 11/20 and
R03-mixed-sources at 17/20, against 17/20 and 19/20 for the wording that names them. Adding "state
no figure the passages do not state" or "answer only what it asks" on top measured no better and
cost R05-action-still-uses-tools and R02 respectively.

## Doctrine fit / implications

- Core gains one optional field and its four call sites. The router, the fusion, the budget, the
  preface, and the page store are all extension code, because an extension can express them.
- Both ends ship together: `ExtensionContext.search` has its producer in `serve.py` and its
  consumer in the `rag` hook, in this change.
- No new config knob. The prefetch runs where the pack lists `rag`, and reads whatever backend the
  deploy already selected.
- Latency is bounded by the hook chain, never added to it: a leg that hangs is dropped and the turn
  proceeds.

## Measuring it

`evals/suites/rag_prefetch.py` holds ten cases. Each seeds one web corpus, served by the
`eval_search` backend the eval environment registers
(`extensions/eval_env/ufo_ext_eval_env/manifest.py:734`), and one workspace corpus staged as files
a real folder source syncs and the memory extension's own page-change consumer indexes. The suite
grades five behaviours: the answer comes from the right corpus, a factual question is answered
rather than searched again, an action still reaches for tools, disagreeing sources are reported and
not merged, and several questions in one message are all answered.

`evals/rag-prefetch-routing.toml` is the hill climb. Eight arms run beside the always-run control:
`no-prefetch`, `prefetch-every-turn`, `bare-preface`, `no-tool-clause`, `no-conflict-clause`,
`unnamed-source-clause`, `external-only`, `internal-only`. The first two bound what the router is
worth, the next four measure each preface clause the AGENTS.md Prompts rule requires ablated —
`unnamed-source-clause` putting the looser attribution wording back — and the last two measure each
corpus on its own. A GEPA run ([Agrawal et al.](https://arxiv.org/abs/2507.19457)) can then
search the preface wording against the same suite, with the ablation deciding.

## Alternatives

| option | why not |
|---|---|
| A model call to route | Spends the round trip the prefetch exists to save. |
| Retrieve on every turn | Costs latency and context on action turns; `prefetch-every-turn` measures it. |
| Score normalization instead of RRF | Needs a shared scale across a third-party web rank and two index legs, which does not exist. |
| A tool the model calls | Already the current state, and the two-round cost is the problem. |
| Rerank the fused set with a cross-encoder | A second model call inside a 5-second deadline. |

## Open decisions

- Whether the router should admit a statement that implies a question (`I need Northwind's price`),
  which the current reading refuses.
- Whether `RRF_K` should itself become an arm once the routing arms settle.
- Whether the internal leg should read memory items beside pages, which needs the memory-search
  seam on the hook context that this change deliberately does not add.
