---
rfc: 0018
title: "Model spec — one record per model, the single source of truth"
status: accepted
date: 2026-07-23
---

# Model spec — one record per model, the single source of truth

> A model's facts (routing, request shape, price, knowledge cutoff, context window, reasoning
> defaults, image caps) live in **seven** independent places across six modules — five keyed by
> model id, two process-global constants — each able to be wrong, missing, or (for the globals)
> impossible to vary per model. This is the root cause behind the
> `gpt-5.6-terra` break (#568) and the reason every point-fix risks tripping the *next* table. One
> frozen `ModelSpec` per model becomes the only place a model is described; every seam reads its fact
> off `registry.spec(id)`; one fail-loud gate replaces failures at three layers.

## Current state

A single model's facts are scattered, with three incompatible keying schemes and two globals:

| Fact | Home | Structure / key | Failure when wrong/missing |
|---|---|---|---|
| Pinned id | `extensions/research/ufo_ext_research/subagent.py:20` | `str` literal (currently `"claude-sonnet-5"`) | unvalidated slug — a typo silently degrades |
| Routing | `models/registry.py:102-115` (`ModelProviderSpec.matches`) + `models/interface.py:132-133` | **prefix** match | terra & `gpt-5.5` indistinguishable |
| Request shape | `models/openai.py:160,162` | imperative — `reasoning_effort` + `tools` attached together, unconditionally | **400** (this is #568) |
| Price | `accounting.py:77` (`MODEL_TOKEN_PRICE`) | `dict[str,ModelPrice]`, exact id | warns (`accounting.py:128-130`), bills **$0** |
| Knowledge cutoff | `loop/prompts/render.py:38` (`MODEL_KNOWLEDGE_CUTOFF`) | `dict[str,str]`, exact id **+ `anthropic.`/`openai.` aliases** | **fails loud** every turn (`render.py:86-88`) |
| Context window | `loop/compaction.py:76` (`MODEL_CONTEXT_WINDOW`) | `dict[str,int]`, exact id, sparse | absent → silent 200k default (`compaction.py:50,126`) |
| Reasoning default / image caps | `schema/records.py:14-15`, `interface.py:53-54` | global consts | not per-model |

The extension contribution surface — `ModelProviderSpec` (`ext/manifest.py:188`, held by
`Manifest.models` at `:554`) — already bundles *some* of this (`matches`, `client`, `key_slot`,
`key_env`, `prices`) but not cutoff, capability, context window, or image caps. So a contributed
model is priced and routed from its spec yet its cutoff must *also* be hand-added to a core dict in
`render.py` — a cross-module edit an extension author cannot make and no gate catches.

Concretely, the #568 trigger: research *was* pinned to `gpt-5.6-terra`, which rejects `tools` +
`reasoning_effort` on `/v1/chat/completions` (#572 has since repointed research to
`claude-sonnet-4-6` — the point-patch this RFC supersedes; the scatter that let a bad pin fail this
way is what remains). The "obvious" swap to `gpt-5.5` would instead have crashed at the cutoff
table (its bare id was absent), and terra was also missing from the context-window map. Every table
is a separate landmine that fails at a different layer.

## Proposal

One frozen `ModelSpec` is the *only* place a model is described. It is an internal value object
carrying a live dep (the client builder) ⇒ `@dataclass(frozen=True)`, never a `BaseModel` (it holds
a `Callable`, is never serialized). New home: `core/src/ufo/models/spec.py`.

```python
@dataclass(frozen=True)
class ReasoningSupport:
    supported: bool                     # does the model reason at all; the client omits reasoning if not
    tools_with_reasoning: bool          # does reasoning compose with tools on the CHAT surface (#568)

@dataclass(frozen=True)
class ModelSpec:
    id: str                             # the exact slug — the registry key
    provider: str                       # "anthropic" | "openai" | contributed — read in PR1 by
                                        #   registry.model_key_env (onboarding's eager key check)
    client: Callable[[ModelSpec, str], ModelClient]   # builds the client from (spec, key)
    price: ModelPrice                   # input / output / cache_read / cache_write_5m / cache_write_1h
    knowledge_cutoff: str               # MACHINE date "YYYY-MM" — rendered to "February 2026" at the seam
    context_window: int
    reasoning: ReasoningSupport
    api_surface: Literal["chat", "responses"]   # REQUIRED — no default: a model's surface is a
                                                #   conscious per-model declaration, never a silent
                                                #   "chat" that would reproduce #568 on a
                                                #   Responses-only model that forgot to set it
    key_slot: str = ""
    key_env: str = ""

    def __post_init__(self) -> None:    # schema-as-enforcement: mis-specified model fails at construction
        if not KNOWLEDGE_CUTOFF_RE.match(self.knowledge_cutoff):
            raise ValueError(...)
        if self.reasoning.tools_with_reasoning and not self.reasoning.supported:
            raise ValueError(...)
```

Load-bearing facts (price, cutoff, window, reasoning, api_surface) have **no defaults** — omitting
one is a
construction error, so "a complete spec" is enforced by the type, not a checklist. The spec carries
exactly the facts that were scattered per-model and each of which a seam reads in this same unit —
so nothing lands as a dead declaration. Facts that are process-global and identical across every
model today (image caps applied to the canonical messages before client translation and the
reasoning-effort *default*) keep their single existing home; they fold onto the spec the first time
a model needs a non-global value. Per-model **output-cap enforcement** is
the RFC's designated later unit (see Resolved decisions), and its field is declared then, with its
consumer — not now.

The seams change from *owning a table* to *reading a spec*:

| Seam | Before | After |
|---|---|---|
| Routing | prefix match, `registry.py` | `registry.spec(id).client` — exact-id lookup |
| Request shape | unconditional `reasoning_effort` + `tools` | client reads `spec.api_surface` + `spec.reasoning` and renders the legal shape |
| Price | `MODEL_TOKEN_PRICE.get(model)` | `spec.price` |
| Cutoff | `MODEL_KNOWLEDGE_CUTOFF.get(model)` | `spec.knowledge_cutoff` |
| Context window | `MODEL_CONTEXT_WINDOW.get(model, DEFAULT)` | `spec.context_window` |

Registration mirrors the existing provider fold: core registers its Anthropic and OpenAI specs; each
manifest contributes a `tuple[ModelSpec, ...]`; the registry indexes them by `id`. The catch-all
router is **dropped** — every served model is enumerated as a complete spec, and a **duplicate id
raises at build** (core `claude-opus-4-8` and bedrock `anthropic.claude-opus-4-8` are distinct ids,
so they don't collide). An unknown id raises at `registry.spec(id)` — the single point every seam
funnels through — instead of three failures at three layers. Pricing's warn-and-zero
(`accounting.py:128-130`) survives *only* for historical ledger rows whose model was since removed;
a live turn can never select an unpriced model because selection and pricing read the same registry.

The #568/#575 fix falls out for free, and the two request-shape facts do distinct jobs: `gpt-5.6-terra`
sets `api_surface="responses"`, so the OpenAI client renders the legal Responses request instead of
the illegal chat one — that is terra's fix, no prefix special-case, no profile edit. The separate
`reasoning.tools_with_reasoning` flag governs a *chat*-surface model that rejects `tools` and
`reasoning_effort` together: the client drops the reasoning parameter for a tool round rather than
400. terra needs only the surface; the flag is the general chat-surface capability. Research pins a
**role** the registry resolves (`registry.role("research")`), not a raw slug, so a re-point is a
validated one-line change, never a slug swap that risks the fact tables.

The **model-catalog skill is generated from the same registry** at serve — its table (id, provider,
cutoff, window, price, reasoning, api surface) reads the records the runtime routes on, so docs
cannot drift from behaviour (both-ends applied to documentation). There is no such skill today.

### Prior art

Validated against the open-source registries/routers (LiteLLM, models.dev, OpenRouter, Helicone,
Portkey, aisuite, Vercel AI SDK). The load-bearing lesson every mature system converges on: **request
*shape* translation belongs in provider client code; the *facts* that gate and price belong in a
per-model data record.** Portkey normalizes shape in code yet still ships a separate pricing DB;
aisuite normalizes shape, has no table, and consequently *cannot* answer "does this model cost /
support vision". So client normalization eliminates only the shape half — the record is required.

- **Borrowed:** `knowledge_cutoff` as a machine date rendered at the seam (models.dev/OpenRouter
  store dates, never a free-form string that can't sort or validate); context-window as its own axis,
  distinct from any output budget, never a shared `max_tokens` (LiteLLM's documented legacy footgun);
  identity is the **exact slug**, which for a contributed model encodes its provider — bedrock's
  `anthropic.claude-opus-4-8` and core's `claude-opus-4-8` are two distinct ids, so the same weights
  under two providers are two rows that never collide (the registry keys on that slug; the dup gate
  catches an accidental clash). Capability facts as named typed fields (not OpenRouter's stringly-typed
  `supported_parameters` array); anti-drift by schema-as-enforcement (`__post_init__` refinements +
  a completeness test, our code-construction equivalent of models.dev's zod `.strict()`).
- **Rejected as scale-driven overbuild** (ufo has ~20 models, not LiteLLM's 2969): a base+overlay
  inheritance split (models.dev `base_model`, Helicone `ModelProviderConfig`/`EndpointConfig`) —
  each spec stays flat and self-contained, one home per fact.
- **Recorded as traps, deferred** (no current model needs them): tiered context pricing (Anthropic
  1M-context doubles past 200k) and dual-surface models. `api_surface` stays single-valued. Cache
  writes keep separate 5m and 1h rates because subagent and main-agent turns use both. If tiered
  context pricing is added, price becomes a tiered structure — noted, not built.

## Doctrine fit / implications

- **One-shape:** one record per model, one registry, one lookup. Every model fact has exactly one home.
- **Fail-loud:** an unknown or under-specified model fails at a single seam (`registry.spec`) at
  load/selection time, not across a mid-turn 400, a render crash, and a silent zero bill.
- **Both-ends:** the request builder, accounting, prompt renderer, and — in PR1 — `client_for`
  (`client`), `usage_priced_micro_usd` (`price`), the OpenAI client (`api_surface`, `reasoning`),
  compaction (`context_window`), render (`knowledge_cutoff`), `client_for`'s key resolution
  (`key_slot`/`key_env`), and `model_key_env` (`provider`) each read their field off the spec. No
  independent tables to drift, and no declared field lands without a same-unit consumer.
- **Core/extension boundary:** the `ModelSpec` type and the registry live in `core/` because they
  are the seam core's own hot path routes, prices, and prompts through — the turn loop selects a
  client, `accounting` prices the burn, and `render` slots the cutoff, all off `registry.spec(id)`;
  an extension cannot own the lookup core itself depends on every turn, and core ships two providers
  (Anthropic, OpenAI) directly whose specs must resolve with no extension installed. What extensions
  express is the *contents*: each contributes complete `ModelSpec`s through the manifest, and core
  keeps no per-model knowledge a contributed model would need appended. What stays out of core: any
  single model's facts beyond core's own two providers.

## Alternatives

- **Patch #568 only** (repoint research off terra and add any missing cutoff entry — what #572
  did). Fixes one instance, leaves
  the scatter and the next-missing-table trap. Not a solution to the class.
- **Extend `ModelProviderSpec` in place** (add cutoff + capability fields) without unifying to a
  per-model record. Keeps the matcher/prices split and the prefix router, so two models under one
  prefix still cannot differ in api surface — the exact #568 shape.
- **Config-file catalog (YAML)** instead of code specs. Loses the typed `client` builder and the
  gate's static checks; a contributed client is code, so the spec that carries it should be too.
- **Normalize the request shape at the client so no capability table is needed.** The metalcraft
  sibling repo's "auto, no knobs" bias. Rejected: the prior-art survey shows shape-normalization
  removes only half — price, cutoff, window, and *which* efforts/surfaces are legal remain per-model
  facts that must live somewhere. The spec is that somewhere; the client still normalizes shape off it.

## Resolved decisions

- **Registry key:** exact id only; the catch-all router is dropped and every served model enumerated.
- **Role indirection** (`registry.role("research")`): adopted now.
- **`context_window`:** on the spec now and read by compaction. A per-model **output cap** and
  pre-flight window enforcement (trim/raise) are a later unit — the field lands there, with its
  consumer, not declared idle now.
- **Catalog-skill generation:** rendered at serve from the live registry (not build-time codegen).

## Delivery

Three stacked PRs, each independently green. The `Manifest.models` type change from
`ModelProviderSpec` to `ModelSpec` forces every extension to convert in one unit (one concept, one
form), so PR1 is the atomic unification; PR2/PR3 are clean follow-ons.

1. **`ModelSpec` + unified registry** — the type, the id-keyed registry, core's ~12 specs, and the
   five per-model-fact seam migrations from the Seams table (routing, request shape — incl. the
   net-new `/v1/responses` code path in the OpenAI client — price, cutoff, context window), the
   manifest + SDK type flip, and the bedrock/openrouter/sample conversions. Closes #568; supersedes
   #575. (The other two Current-state facts move later: the pinned-id → role in PR2; the uniform
   globals — reasoning-effort default, image caps — stay at their existing home, per Doctrine fit.)
2. **Role indirection** — `registry.role`, research pins a role not a slug (migrates the pinned-id
   seam).
3. **Serve-time model-catalog skill** — generated from the registry.
