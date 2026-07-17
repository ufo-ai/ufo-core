---
rfc: 0016
title: "Self-improvement impact — human feedback in, eval harness as the impact meter"
status: proposed
date: 2026-07-16
---

# Self-improvement impact — human feedback in, eval harness as the impact meter

> Two halves of the self-improvement loop already exist and never touch: the `self_improvement`
> extension proposes prompt changes gated by a *counterfactual replay* over its own mined
> tool-error set, and `evals/` runs the standardized suites against a persisted agent and can share
> a two-run A/B. A promoted change therefore ships evidence of "lift on `tool:bash`" but no answer
> to "does it help or hurt `basics` / `tool_calling` / `web_research`?" — and the only friction
> signal the loop can see is a tool that errored; a member telling the agent it was wrong is
> invisible. This proposes (A) a **candidate-prompt arm** so the eval harness measures a proposal's
> cross-suite impact, and (B) a **human-feedback signal** so the corpus and grader learn from
> members, not just tracebacks. Extends `spec.md` §Extension system (`trajectories.read` /
> `propose_change` / `invoke`, lines 166-171) and the "Service self-improvement" acceptance row
> (`spec.md:395`).

## Current state

**The self-improvement loop (extension).** `extensions/self_improvement` is the offline-replay
loop, whole:

| Leg | Where | What it does |
|---|---|---|
| Corpus | `corpus.py:46-78` | Mines trajectories; the **only** friction signal is a tool round that errored (`first_tool_error`); classes are `tool:<name>`, split mine / held-out. |
| Proposer | `proposer.py:33-43` | One model leg rewrites the agent prompt to handle a struggling class. |
| Evaluation | `evaluation.py:24-45`, `replay.py:129-150` | **Counterfactual, tool-aware replay**: re-runs each held-out task under both prompt arms, feeding *archived* tool results back (zero side effects), then an LLM judge accepts/rejects each answer. |
| Gate | `gate.py:157-174` | Two-stage Newcombe lift (local class lift ≥ floor with per-arm `N_FLOOR`, global non-regression), and `cron.py:108-110` requires `STABILITY_COUNT` consecutive passing ticks. |
| Promotion | `cron.py:112-117` | Governed `propose_change` (compare-and-swap on prompt digest; a member approves in chat — `governance.py:58-104`). Never a direct write. |

The corpus docstring states the constraint plainly: *"the friction signal that survives in the
transcript (ufo has no self-report side-channel)"* (`corpus.py:1-5`).

**The eval harness (operator package).** `evals/` runs suites as **real turns against a persisted
agent resolved by name**:

- `resolve_workspace_and_agent` reads `agent.prompt` / `agent.model` from the row (`driver.py:42-59`),
  and the turn engine **re-reads the agent row at turn-load** (`queue.py:371-396`, rendered at
  `queue.py:217-218`). The persisted prompt is the sole source of the system prompt.
- Reports are digest-pinned to the case payloads (`harness.py:156-158`); `--share CURRENT [BASELINE]`
  renders a **two-run comparison** behind a private S3 URL (`__main__.py:253-291`).
- The nearest existing A/B is GDPval **pack treatments** — whole-config arms via `[pack] name`
  (`__main__.py:307-311`), not prompt arms.

**The gap, precisely.** The two halves share no seam:

1. **No candidate-prompt arm.** The harness can only run what is persisted on an agent row. A
   pending proposal's `body.prompt` (`governance.py:49`) cannot be run without first approving it
   (mutating the live agent). So the loop's own replay gate is the *only* evidence a change ever
   gets, and it is narrow — held-out members of the one mined class, judged by one LLM leg. A prompt
   rewrite that fixes `tool:bash` retries but quietly regresses `web_research` promotes with no one
   the wiser.
2. **No human-feedback signal.** `corpus.py` flags a trajectory *only* when a tool errored. The
   large class of failures where the agent answered confidently, wrongly, with no tool error — and
   the member said so — never enters the corpus, and the grader has no human label to learn from.

## Proposal

Two independently landable slices. (A) is bounded and lands in the operator package; (B) is a
signal design that lands in the extension.

### A. Candidate-prompt arm → eval harness as the impact meter (built)

The harness runs a suite against a **candidate prompt** resolved from a pending proposal, producing
an `EvalReport` **digest-comparable** to the baseline agent's run; the existing
`--share CURRENT BASELINE` renders the before/after impact.

The hard-to-vary fact: the turn engine reads the prompt from the agent **row** (`queue.py:371-396`),
so overriding `driver.agent_prompt` alone changes nothing the turn sees. The arm is therefore a
**real agent row** — a scratch agent (base agent's model, candidate prompt) seeded in the target
workspace, upserted by a stable `candidate:<proposal>` name so a re-run reseeds one row.

```
python -m evals --only basics tool_calling web_research \
    --workspace <disposable> --candidate-from-proposal <proposal_id>   # after arm
python -m evals --only basics tool_calling web_research \
    --workspace <disposable> --agent assistant                          # baseline arm
python -m evals --share <after_run> <baseline_run>                       # impact diff
```

Seams, all in `evals/` (operator package — imports core directly, is not an extension):

| Seam | What it does |
|---|---|
| `evals/driver.py:seed_candidate_agent` | Resolves the pending proposal's `body.prompt` and its base agent's model, upserts the `candidate:<proposal>` scratch agent, returns `(workspace_id, name)`. Fails loud on a missing / non-pending proposal or an empty prompt body. |
| `evals/__main__.py:--candidate-from-proposal` | Seeds the scratch agent before resolving the target and runs the selected suites against it; the run is labelled `candidate:<proposal>` and carries the candidate prompt. Rejected against corpus-backed evals (those pin their own workspace/agent semantics). |
| digest | Task digests are unchanged (they pin *cases*, `harness.py:156-158`), so after/baseline reports compare directly; `pin_runtime` already folds `agentPromptDigest` into the runtime digest, so the arms are legibly distinct where it matters. |

The loop then closes without new core (the second slice, not yet built): the `self_improvement`
extension already holds `invoke`
(`context.py:275-277`) and the spec already promises *"evaluate candidates via `invoke`"*
(`spec.md:170`). A later tick can drive a bounded suite through `invoke` against a scratch agent and
attach the **cross-suite delta** to the `propose_change` it opens, so a member approving in chat sees
"replay lift +0.31 on `tool:bash`, **eval impact: basics 0/0, tool_calling +2, web_research −1**"
instead of replay lift alone. Replay stays the cheap inner gate; the eval harness is the outer
confirmation that the rewrite did not regress the standardized suites.

### B. Human feedback as a corpus + grader signal

Give the loop a second friction axis: a durable **feedback record** per turn, produced when a member
expresses (dis)satisfaction, consumed by the corpus (which trajectories are "bad") and by the grader
(a human label is ground truth where present, displacing the LLM judge for that case).

Every member action happens in chat (`AGENTS.md`), so the signal is **not** a new end-user endpoint.
Two producers, both already-shaped seams:

| Producer | Mechanism | Notes |
|---|---|---|
| Explicit | a `stop`-hook or a durable-surface reaction the surface admits (Slack 👎 / "that was wrong") lands a `feedback` row keyed to the turn | The Slack surface already handles reactions; the record is the new part. |
| Implicit | a `post_tool_use`/`stop` hook that flags a **correction turn** — a member's next inbound that contradicts or redoes the prior answer | Mined, not asked; weaker signal, larger reach. |

Consumers:

- **Corpus** — `bad_trajectory` (`corpus.py:68-78`) gains a second reason: a negative feedback record,
  not only a tool error. Class name becomes `feedback:<agent>` (or stays `tool:<name>` when both
  fire). This is the change that lets the loop learn from wrong-but-no-error answers.
- **Grader** — where a human label exists, `_accepts` (`evaluation.py:47-59`) uses it as ground truth
  instead of the LLM judge; the judge only fills the unlabeled held-out arms. Human labels anchor the
  lift measurement to real dissatisfaction, not a judge's opinion of it.

## Doctrine fit / implications

- **Nothing enters core.** (A) is the operator `evals/` package, which already imports core and
  resolves agents by name; it adds a scratch-agent insert and a proposal read, no new core module.
  (B) is expressible on the existing extension seams — `hooks` (`stop` / `post_tool_use`), the
  extension's own scoped store / table for the feedback record, `trajectories.read` for the corpus.
  The "Service self-improvement" acceptance row (`spec.md:395`) already claims both.
- **Both ends or neither.** (A) ships the arm (producer: scratch agent + candidate resolve) and the
  consumer (the impact diff via `--share`) in one change, proven by a suite whose after-arm carries a
  known-different prompt and shows a measured delta. (B) does not land until a producer *and* the
  corpus/grader consumer land together — the feedback record's migration adds only the column its
  unit wires.
- **Promotion stays governed.** Neither slice writes an agent prompt; the eval impact is *evidence
  attached to* a `propose_change`, and a member still approves in chat (`governance.py:58`).
- **One shape.** The candidate arm is a real agent row, not a shadow prompt threaded through the
  turn path — no second source of "what prompt is this turn running."

## Alternatives

| Option | Why not |
|---|---|
| A/B by **mutating the live agent** prompt between runs | Pollutes the production agent, races other turns, and breaks the compare-and-swap invariant. The scratch agent is disposable and isolated. |
| **Pack-treatment arms** (as GDPval does) for prompt A/B | Too coarse — a treatment is a whole `[pack]` config; we want to vary *only* the prompt body, holding pack/tools/model fixed. |
| Keep the **replay gate as the sole evidence** (status quo) | Narrow by construction: one mined class, held-out members only, one judge. Misses cross-suite regression — the exact risk a whole-prompt rewrite carries. |
| HF via a **member-facing `/feedback` endpoint** | Violates "every member action happens in chat" — the signal must ride the surface (reaction / correction turn), never a bespoke endpoint. |

## Open decisions

1. **HF signal source (B).** Explicit reaction, mined correction turn, or both? Explicit is a
   cleaner label but sparse; mined is dense but noisier. A portfolio: land explicit first (high
   precision), add mined as a second producer once the corpus consumer is proven.
2. **Gate composition.** Does the self_improvement gate *require* an eval-harness pass before
   promoting, or does replay stay the gate and the eval impact ride along as advisory evidence on
   the proposal? (Cost: a suite run per candidate is far more expensive than replay.) Recommend:
   replay is the gate; the eval impact is attached advisory evidence a member reads before approving
   — until we have data that replay-passing candidates regress suites often enough to justify the
   spend.
3. **Scratch-agent cleanup.** A settled proposal's `candidate:<proposal>` agent has no further use;
   nothing removes it today beyond discarding the disposable workspace. Whether that stays the
   answer or a settle-time delete is worth wiring is open until candidate runs happen anywhere
   longer-lived.
