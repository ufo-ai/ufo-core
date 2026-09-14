---
rfc: 0050
title: "Prefetch RAG — retrieve both corpora before the first model round"
status: implemented
date: 2026-09-19
---

# Prefetch RAG

> A member who asks a factual question pays two model rounds for the answer: one round calls a
> search tool, one round reads its result. The deploy already holds both retrieval seams at boot —
> the selected web backend and the workspace's own index — so this RFC spends them once, in
> parallel, on the `user_prompt_submit` hook, and folds what they found into the founding message.
> The router prefetches on every member message and decides only what is searched for, the two legs
> fuse by reciprocal rank, and the block is rendered inside the untrusted wall. Every constant that shapes a model's
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

The router prefetches on every member message (`route.py:72`). It decides what is searched for, not
whether to search.

Adaptive retrieval — decide per query whether to retrieve — is the published answer, and it is what
this router did first. [Self-RAG](https://arxiv.org/abs/2310.11511) trains the model to emit a
retrieve/no-retrieve reflection token; [Adaptive-RAG](https://arxiv.org/abs/2403.14403) routes by
question complexity; [FLARE](https://arxiv.org/abs/2305.06983) retrieves only when the generation's
own confidence drops. All three spend a model's own judgement on the decision. This hook cannot:
the prefetch exists to save a round trip, a classifier round trip spends exactly what it saves, and
the whole budget is a 5-second hook deadline and one shot before the first token. What was left was
a read of the text — a `?`, an interrogative opener, a 12-character floor — and that read refused
real questions. `s&p close` carries no question mark, no opener and nine characters, and reached no
provider at all; a member reported exactly that on the testing cluster.

The two costs are not symmetric. Refusing a real question costs the turn its grounding, and the
member reads a worse answer. Retrieving for an instruction costs one block the model steps past on
its own, inside a deadline that is bounded whatever the legs do. So the gate is gone, and no
preface clause stands in for it: with the sentence that told the model the block replaces no tool
removed, `R05-action-still-uses-tools` and `R06-request-phrased-as-a-question` both score 20/20,
the same as with it.

| inbound | decision |
|---|---|
| holds question segments | one query per question, at most 3 |
| holds none | one query, the message as it stands |
| no content after stripping | refuse |
| over 2,000 characters | refuse |

A message over `MAX_INBOUND_CHARS` is a pasted document. Sending it verbatim is not a search — the
provider is handed thousands of characters of the member's own text, which matches nothing and
spends the turn's retrieval budget — and finding the ask inside the paste needs the model call this
hook runs before. The decision is logged as `rag.route`, so a routing decision is legible per turn.

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
body, and the notice states they are not instructions. Inside the wall each selected passage is one
numbered entry, numbered from 1 in the order the passages render:

```
[1] https://northwind.example/pricing — Northwind pricing (2026-02-11)
Team plan is $30 a seat.

[2] page/2f1c… — Northwind order form
Northwind bills this workspace $24 per seat per month.
```

The number is the handle a reply cites, and the entry carries beside it the two things the prompt's
citation rules need: the reference a reader opens — a web address, or the workspace record ref for a
page — and the title, which is the descriptive name a citation is anchored on instead of a bare
address. A workspace passage therefore cites exactly as a web passage does. The numbering costs the
head of each entry and nothing else: `PASSAGE_MAX_CHARS`, `TOTAL_MAX_CHARS` and `MAX_PASSAGES` bound
the passage text as before.

The preface is two sentences: answer from these passages when they answer the question, and name in
the sentence that states it where every figure, date and name came from — a web passage by its
address, a workspace passage by the record it names. Each clause holds its place on a measurement,
and every other clause the preface once carried was removed on one.

The attribution sentence is what accuracy rests on. Removing it takes `R02-internal-fact` and
`R03-mixed-sources` from 19/20 to 8/20 (p=0.0004, Fisher exact, 10 samples a case); removing the
whole preface takes them to 0/20. It names the two source forms because the looser "name the source
you took each fact from" left both cases split: over 20 samples each it held R02 at 11/20 and R03
at 17/20, against 17/20 and 19/20 for the wording that names them.

The answer-from-the-passages sentence is what saves the round trip the hook exists to save.
Removing it holds accuracy at 158/170 against 155/170 (p=0.69) and sends 82% of turns back out to a
tool where 21% go with it, over 150 samples an arm.

Five clauses were removed because their own cases did not move. The searches-ran-unasked sentence
and the they-run-on-every-message sentence each left the four cases that grade what a reply claims
about the member's own work at 39/40 against 39/40 and 40/40. The numbered-entry sentence left R02
and R03 at 19/20 against 19/20: the block numbers its own entries, and replies cite the number
without being told to. The tool sentence left R05 and R06 at 20/20 and cost re-searching — 56% of
turns called a tool again with it, 19% without. The conflict sentence measured 53/80 with it against
59/80 without on the four conflict cases, and the shipped preface scores 67/80 on them (p=0.017
against the preface that carried every clause): the sentence wins the "name each side" criterion on
`R15-amendment-supersedes-the-order-form` and loses the "land on one value" criterion on
`R11-two-workspace-records-disagree`, and the two do not net out in its favour.

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

`evals/rag-prefetch-routing.toml` is the hill climb. Seven arms run beside the always-run control:
`no-prefetch`, `question-shaped-only`, `bare-preface`, `no-answer-clause`, `unnamed-source-clause`,
`external-only`, `internal-only`. The first two bound what the router is worth, the next three
measure each surviving preface sentence the AGENTS.md Prompts rule requires ablated —
`unnamed-source-clause` putting the looser attribution wording back — and the last two measure each
corpus on its own. A GEPA run ([Agrawal et al.](https://arxiv.org/abs/2507.19457)) can then
search the preface wording against the same suite, with the ablation deciding.

## Alternatives

| option | why not |
|---|---|
| A model call to route | Spends the round trip the prefetch exists to save. |
| Route by question shape | Refuses what members type: `s&p close` is a factual ask with no question mark, no interrogative opener and nine characters. `question-shaped-only` measures it. |
| Score normalization instead of RRF | Needs a shared scale across a third-party web rank and two index legs, which does not exist. |
| A tool the model calls | Already the current state, and the two-round cost is the problem. |
| Rerank the fused set with a cross-encoder | A second model call inside a 5-second deadline. |

## Open decisions

- Whether `RRF_K` should itself become an arm once the routing arms settle.
- Whether the internal leg should read memory items beside pages, which needs the memory-search
  seam on the hook context that this change deliberately does not add.
