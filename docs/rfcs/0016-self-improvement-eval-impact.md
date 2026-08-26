---
rfc: 0016
title: "Self-improvement — the loop's exit, its impact meter, and a human signal"
status: proposed
date: 2026-07-16
---

# Self-improvement — the loop's exit, its impact meter, and a human signal

> The self-improvement loop runs hourly, mines its corpus, rewrites the agent prompt, replays the
> change counterfactually, passes a two-stage gate — and writes into a table nothing can approve.
> `approve_proposal(proposal_id)` takes no member argument, checks nothing, and has zero
> production callers (#646); the HTTP endpoint that once clicked it was deleted 2026-07-23 as a
> doctrine violation, with no replacement. **The loop is a closed pipe**: it spends tokens to
> produce candidates that can never ship. Two further gaps sit behind that one — the only
> evidence a candidate carries is its own narrow replay (a prompt rewrite that fixes `tool:bash`
> while regressing `web_research` promotes with no one the wiser), and the only friction signal
> the corpus can see is a tool that errored (a member telling the agent it was wrong is
> invisible).
>
> Three slices, in dependency order: **the exit** — a decision record that rolls back, an
> approver who is a real seated member, and one gated ref that closes the pipe; **the impact
> meter** — a candidate-prompt arm so the eval harness measures cross-suite impact (built); **the
> feedback signal** — so the corpus and grader learn from members, not just tracebacks. Extends
> `spec.md` §Extension system (`trajectories.read` / `propose_change` / `invoke`, lines 166-171)
> and the "Service self-improvement" acceptance row (`spec.md:395`). RFC 0019 owns knowledge
> scope and audience and gates nothing; every gate in the system is here.

## Current state

**The self-improvement loop (extension).** `extensions/self_improvement` is the offline-replay
loop, whole:

| Leg | Where | What it does |
|---|---|---|
| Corpus | `corpus.py:46-78` | Mines trajectories; the **only** friction signal is a tool round that errored (`first_tool_error`); classes are `tool:<name>`, split mine / held-out. |
| Proposer | `proposer.py:33-43` | One model leg rewrites the agent prompt to handle a struggling class. |
| Evaluation | `evaluation.py:24-45`, `replay.py:129-150` | **Counterfactual, tool-aware replay**: re-runs each held-out task under both prompt arms, feeding *archived* tool results back (zero side effects), then an LLM judge accepts/rejects each answer. |
| Gate | `gate.py:157-174` | Two-stage Newcombe lift (local class lift ≥ floor with per-arm `N_FLOOR`, global non-regression), and `cron.py:108-110` requires `STABILITY_COUNT` consecutive passing ticks. |
| Promotion | `cron.py:112-117` | Governed `propose_change` (compare-and-swap on prompt digest). Never a direct write — and, today, never an approval either. |

The corpus docstring states its constraint plainly: *"the friction signal that survives in the
transcript (ufo has no self-report side-channel)"* (`corpus.py:1-5`).

**The governance half.** `proposal` is a row plus `propose_change` (`governance.py:28,57`;
`schema/tables.py:289-303`). It has no approver argument, no `approved_by`, no `expires_at`, and
no uniqueness among pending rows — a replayed propose mints a duplicate. RFC 0017 deferred
object history and concurrency verbatim: *"returns with governance, not before."*

**The eval harness (operator package).** `evals/` runs suites as **real turns against a persisted
agent resolved by name**:

- `resolve_workspace_and_agent` reads `agent.prompt` / `agent.model` from the row (`driver.py:42-59`),
  and the turn engine **re-reads the agent row at turn-load** (`queue.py:371-396`, rendered at
  `queue.py:217-218`). The persisted prompt is the sole source of the system prompt.
- Reports are digest-pinned to the case payloads (`harness.py:156-158`); `--share CURRENT [BASELINE]`
  renders a **two-run comparison** behind a private S3 URL (`__main__.py:253-291`).
- The nearest existing A/B is GDPval **pack treatments** — whole-config arms via `[pack] name`
  (`__main__.py:307-311`), not prompt arms.

**The gaps, precisely.**

1. **No exit.** Nothing can approve a passing candidate, so the loop cannot change anything. This
   blocks the other two: an impact meter measures evidence for a decision nobody can make, and a
   feedback signal improves candidates that cannot ship.
2. **No statistical power behind the gate that would substitute for a signature.** At
   `N_FLOOR = 4` the likelihood ratio is ~2.3:1 and the gate goes silent above a ~0.96 baseline
   (`gate.py:22-24`); the global stage *passes by default* under-floor (`gate.py:142-154`); and
   the corpus that would fix this is a 200-conversation sliding window that cannot accumulate
   (`ext/context.py:178`). A mechanically-verified promotion is the right long-run answer and is
   **not reachable today at any headcount** — which is what makes a human signature with an undo
   the honest interim, rather than a placeholder.
3. **No candidate-prompt arm.** The harness can only run what is persisted on an agent row. A
   pending proposal's `body.prompt` (`governance.py:49`) cannot be run without first approving it
   (mutating the live agent). So the loop's own replay gate is the *only* evidence a change ever
   gets, and it is narrow — held-out members of the one mined class, judged by one LLM leg.
4. **No human-feedback signal.** `corpus.py` flags a trajectory *only* when a tool errored. The
   large class of failures where the agent answered confidently, wrongly, with no tool error — and
   the member said so — never enters the corpus, and the grader has no human label to learn from.

## What the field ships

Verified July 2026; full sourcing in PR #684's research record.

- **Gates exist for skills and documents, never for facts or prompts.** OpenClaw's Skill Workshop
  writes `PROPOSAL.md`, stales on hash movement — then defaults `approvalPolicy` to `auto`; GRASP
  (arXiv:2605.29668) accepts a skill edit only on held-out gain within a regression budget and
  shows the *gate*, not the writer, makes results real; Glean verification is a freshness badge
  on a document.
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
  dialog showed the proposed path, not the resolved target) — while mechanical verification works
  where clicking doesn't (DeLM: admitting writes only when they verify against evidence costs 4.9
  points to remove). Entra's answer is standing scoped grants plus permissions "even an
  administrator can't consent to." The design consequence: gate **one** ref, not a taxonomy —
  every additional click is a discount on the ones that matter.
- **ActiveGraph and Regimes** (arXiv:2605.21997, arXiv:2606.10241) are the nearest prior art for
  the spine: patches as CAS'd proposals whose rejection is data; **authority ceilings** — act
  classes that are *unrepresentable as routine*, with "no inference, ever" mapping no fuzzy label
  onto a class; and an improvement loop gated static → sandbox → in-sample → held-out. Three
  Regimes results this RFC adopts: a promoted prompt is a *probe* whose lasting value is the
  guarded deterministic operator it reveals; loops over-promote at high baselines without a
  plateau rule; and **the measurement layer must sit outside the loop's own mutation reach**. Its
  own governance is stubbed (`approved_by` defaults to the string `"user"`), so the algebra ports
  and the mechanism does not.

## Proposal

Three slices. The exit is blocking and lands first; the impact meter is built; the feedback
signal is a signal design that lands in the extension.

### 1. The exit — a receipt, an approver, one gated ref

**The receipt comes first, because a signature without an undo is a one-way door.** The evidence
floor is unreachable (gap 2), so the interim bar is a signature *with rollback armed and a
post-apply regression watch* — and rollback is what makes that bar honest rather than
ceremonial.

- **`revision` (core, one table) records a decision, not a version:** `ref`, an opaque
  `principal` (internally a superset of RFC 8693 `act` chaining — the one standardized shape for
  actor chains, so exporting to whatever WIMSE normatizes is a projection, not a migration),
  originating turn and audience, decision basis, and the per-item op list for applies (ids only:
  inserted, superseded, tombstoned). It is written by a **core-private derived writer** that
  accepts no principal and no basis argument, reading both from turn authority and the recorded
  click, so extension code can never assert `basis = signed_decision`. That writer plus a CI
  import gate are the primary defense; DB grants are hosted-only hardening, labelled as such —
  boot fails loud if tamper-evidence is claimed without the two-role split.
- **It threads through the object verbs on day one, so it has a consumer before the gate
  exists.** `ObjectVerbs._apply`/`_delete` and the private handoffs journal every governed apply,
  which makes "why did my scheduled task change last Tuesday" answerable from the moment the
  table lands — history for `scheduled_task` and `source` is a real read today, not a rehearsal
  for the prompt gate.
- **Rollback rides the object surface: `revision` is a kind whose apply inverts.** Zero new verbs
  — `object_list revision` is history, `object_apply` of an inversion manifest is rollback,
  rendered and gated like any governed apply: it takes a *set* of revisions, newest-first,
  atomic, refuses over cap, recomputes against destination-now (a row changed since the target
  revision surfaces as a conflict, never a silent force).
- **Digests and refs only — never content.** Content stays in the owning kind's table under its
  erasure path. The journal is append-only **by policy, not cryptography** — a feature: the
  EDPB's blockchain guidelines (final, 2026-07-07) advise keeping personal data, even hashed, off
  technically immutable structures, so a Postgres table with purge and an erasable
  principal→member mapping stays Art. 17-operable where a hash chain would not. Retention:
  per-workspace `retention_days`, default 180 (the AI Act's "at least six months" floor, binding
  deployers 2026-08-02). Approver identity is retained through erasure as a legal-obligation
  exception (Art. 17(3)(b)) — an audit trail whose approvers become "someone" is not one. Purges
  run under a distinct principal and are themselves journaled as count-and-window records.

**The approver is a real member, or the signature means nothing.**

- `approve_proposal(proposal_id, approver_member_id)` — required positional;
  `required_approver_member_id` recorded at open; NOT NULL on decision; a wrong-member click
  refuses visibly.
- An approver must hold a **seat** (owner-granted, already in the model). A domain-verified email
  auto-joins members (`join_member`), and auto-provisioning must not be approval capability.
- This forces the owner explicit: a recorded, chat-assignable owner. Today's owner is the
  earliest member row, so erasing a departed founder silently promotes whoever joined next.
- `expires_at` with its sweep job, `MAX_PENDING_PER_ACTOR = 20`, `PROPOSAL_TTL_DAYS = 28`, and
  `UNIQUE(workspace_id, ref, to_digest)` among pending rows; related changes bundle into one
  decision. `proposal` becomes strictly a pending request, consumed by its decision; `revision`
  is the sole record of what was decided.

**One gated ref: the agent prompt.** Everything else stays ungated.

- **The model has no apply verb, and a click never becomes a turn.** A gated apply refuses with
  `{proposal, status: pending}`; approval is a surface interaction, `ConnectClick`-shaped (direct
  non-turn side effect), never `AnswerSubmit`-shaped (which would re-admit the model into the
  decision path). A gated act on a surface with no decision path refuses visibly; web parity
  ships with the ref.
- **The diff is the resolved effect** — the values the apply will actually use, never the
  proposer's description (GhostApproval is what the other choice costs). For a prompt change:
  the rendered body diff against the digest the CAS pins.
- **Signature now, evidence later.** A shared-scope prompt change takes a signature with rollback
  armed and a regression watch behind it; when a floor is clearable it becomes the
  promotion-to-default bar — and that enum member lands **only with its producer**, which is this
  RFC's other two slices (the accumulating corpus and the cross-suite impact arm). An under-floor
  decision journals its insufficient-evidence diagnostic beside it: an honest label rather than a
  silent degrade.
- **A closed set of refs is never proposal-reachable**, at any configuration: `gate.py`, the eval
  suites, and the regime taxonomy — Regimes' result that the measurement layer must sit outside
  the loop's own mutation reach, and ActiveGraph's ceiling in our types. An optimizer that can
  edit its own meter has no meter.
- **The improvement ladder is the point of the gate**, not the prompt: a prompt repair is a
  probe; a recurring procedure graduates to a skill; a deterministic skill graduates to a guarded
  automation firing on detected structure. What survives a probe is the operator it reveals.

**Not here, deliberately.** A computed tier function over `(act, ref, scope, actor, …)` is the
right shape at two or more gated refs and premature at one — with a single ref the "tier" is a
constant. The `ObjectStore.decide(ctx, name, spec, old) -> Effect` pre-pass is designed for the
first gated *object* kind (the prompt goes through the existing `propose_change` path, not the
object verbs) and lands with it: `scope` and `reversibility` are knowable only where the kind's
semantics live, and a handler re-run at approval time would resolve `ctx.speaker_member_id` to
the *approver*. Gating `skill`, `source`, `connector`, or `automation` is blocked on object
ownership — `SkillObjects` carries no owner, so scope-driven tiering would make every skill edit
gated and small workspaces unable to edit skills at all. RFC 0017's deferral resolves for the
prompt and stands for the kinds.

### 2. The impact meter — candidate-prompt arm (built)

The harness runs a suite against a **candidate prompt** resolved from a pending proposal, producing
an `EvalReport` **digest-comparable** to the baseline agent's run; the existing
`--share CURRENT BASELINE` renders the before/after impact.

The hard-to-vary fact: the turn engine reads the prompt from the agent **row** (`queue.py:371-396`),
so overriding `driver.agent_prompt` alone changes nothing the turn sees. The arm is therefore a
**real agent row** — a scratch agent (base agent's model, candidate prompt) seeded in the target
workspace, upserted by the stable `CANDIDATE_AGENT_NAME` so a re-run reseeds one row.

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
| `evals/driver.py:seed_candidate_agent` | Resolves the pending proposal's `body.prompt` and its base agent's model, upserts the `CANDIDATE_AGENT_NAME` scratch agent, returns `(workspace_id, name)`. Fails loud on a missing / non-pending proposal or an empty prompt body. |
| `evals/__main__.py:--candidate-from-proposal` | Seeds the scratch agent before resolving the target and runs the selected suites against it; the run is labelled with that agent's name and carries the candidate prompt. Rejected against corpus-backed evals (those pin their own workspace/agent semantics). |
| digest | Task digests are unchanged (they pin *cases*, `harness.py:156-158`), so after/baseline reports compare directly; `pin_runtime` already folds `agentPromptDigest` into the runtime digest, so the arms are legibly distinct where it matters. |

The loop then closes without new core (not yet built): the `self_improvement` extension already
holds `invoke` (`context.py:275-277`) and the spec already promises *"evaluate candidates via
`invoke`"* (`spec.md:170`). A later tick drives a bounded suite through `invoke` against a scratch
agent and attaches the **cross-suite delta** to the `propose_change` it opens, so the approver
reads "replay lift +0.31 on `tool:bash`, **eval impact: basics 0/0, tool_calling +2,
web_research −1**" instead of replay lift alone. Replay stays the cheap inner gate; the eval
harness is the outer confirmation that the rewrite did not regress the standardized suites — and
it is half the producer the `evidenced` bar waits on.

### 3. The feedback signal — human labels into corpus and grader

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

The corpus must also **persist flagged examples durably** rather than riding the 200-conversation
sliding window (`ext/context.py:178`) — without that, no evidence floor is ever reachable and the
`signed` interim is permanent. That persistence is this slice's other half.

## Threat model

| Attack | Defense | Residual |
|---|---|---|
| Poisoned prompt supply chain (ClawHavoc-shaped) | the prompt at shared scope is gated with rollback armed and a regression watch; the proposal binds its target's digest and goes stale if the target moves; no auto-apply, ever | skills and automations stay ungated until they gain owners — stated, not papered |
| Proposal flood → approval fatigue | caps, TTL, pending uniqueness, bundling; exactly one gated ref, so the tray stays small by construction; the only proposal producer is the replay-gated pipeline — there is no page-injected path to a proposal | a patient attacker at low rate still reaches a bored approver |
| Approval of something other than what applies | the rendered diff is the resolved effect the apply uses; the digest CAS makes a moved target stale | approver comprehension of a large but complete diff |
| Optimizer captures its own meter | `gate.py`, the eval suites, and the regime taxonomy are not proposal-reachable at any configuration | operator compromise |
| Over-promotion at a high baseline | the plateau stopping rule and the corrected floor (below); `STABILITY_COUNT` consecutive passing ticks | a genuinely drifting baseline nobody reads |
| Forged audit | core-private derived writer; CI import gate; hosted two-role grants | core or operator compromise |
| Compromised Slack account = approval capability | Not defended | step-up auth is an open decision |

## Build order

Five units, each landing alone and proving a chain end-to-end. Nothing is declared before its
producer and consumer land together.

| Unit | Lands | Proof |
|---|---|---|
| **1. Receipt** | `revision` (this unit's columns only) + the core-private derived writer threaded through `ObjectVerbs._apply`/`_delete` and the private handoffs; `revision` object kind (list/get + inverting apply); retention/purge under a journaled purge principal; CI import gate | Apply and delete a `scheduled_task` and a `source`; read history back; roll one back; a purge removes expired rows and journals a count-and-window record under the purge principal; an extension import of the journal writer fails CI |
| **2. Approver identity** | explicit owner (recorded, chat-assignable), seat requirement for approvers, `approve_proposal(proposal_id, approver_member_id)`, `required_approver_member_id`, `expires_at` + its sweep job, pending-row uniqueness, per-actor caps | A wrong-member click refuses visibly; an unseated auto-joined member cannot approve; the owner is an explicit record — erasing a member never reassigns ownership by inference; an expired proposal leaves the tray; a replayed propose no longer mints a duplicate |
| **3. One gated ref — the exit** | the agent prompt via the existing governance path (proposer, digest CAS, replay arm all exist); `TerminalFrame.proposal`; `ConnectClick`-shaped Slack `ProposalClick`; web renderer; refusal on surfaces with no decision path; the never-proposal-reachable ref set; the regression watch. **#646 closes here** | self_improvement's next passing candidate renders as a proposal with its evidence; the bound owner clicks; the prompt changes; the record names them; rollback restores the prior prompt; the web surface renders the same proposal; a gated act on a surface with no decision path refuses visibly; a proposal naming `gate.py` refuses to open |
| **4. Impact evidence on the proposal** | the `invoke`-driven bounded suite on a scratch agent, its cross-suite delta attached to `propose_change`; the gate's statistics rework — MDE-derived n, family-wise correction, the unreachable-ceiling diagnostic, plateau stopping, and the under-floor global stage no longer passing by default | A proposal renders replay lift *and* cross-suite impact; a candidate that improves its mined class while regressing a suite is visible before the click; the gate reports "unreachable at this baseline" instead of passing silently |
| **5. Feedback signal** | the `feedback` record with its explicit producer; durable persistence of flagged examples past the sliding window; corpus second reason; grader ground-truth override | A member's 👎 lands a record; the flagged trajectory enters the corpus with no tool error present and survives past 200 conversations; a labelled case is graded by the human label, not the judge; a candidate proposed from a `feedback:` class reaches the tray |

**Deliberately deferred, each with its reason:** the computed tier function and the
`decide() → Effect` seam (need a second gated ref / the first gated object kind);
`skill`/`source`/`connector`/`automation` as gated refs (blocked on object ownership);
`legal_hold` and the date-bounded audit export (the first compliance-driven buyer names the
shape; retention plus journaled purge is the floor that ships); the tenant SIEM/webhook feed
(export lands first); SCIM/IdP directory sync and approver liveness freshness (the
enterprise-sale unit; the seat requirement is the bridge); owner plurality and lifecycle (≥2
owners, `active | departed`, transfer ceremony — one explicit recorded owner suffices until a
workspace outgrows it); standing delegation (lands with its first kind); cross-tenant evidence
pooling (the portable bundle is built; pooling is a DPA conversation, not a default).

## Doctrine fit

- **Core gains three things, each one extensions cannot express:** the `revision` table with its
  derived writer (an extension that could assert its own `basis` is not a record), the approver
  binding on `approve_proposal`, and the decision surface (`TerminalFrame.proposal` +
  per-surface `ProposalClick`). Everything else stays where it is: the corpus, proposer, replay,
  and gate are extension code; the candidate arm is the operator `evals/` package, which already
  imports core and resolves agents by name.
- **Every member action stays in chat** — with the approval click argued, not assumed. The
  bespoke HTTP approval endpoint RFC 0017 named as the standing doctrine violation is already
  gone (deleted 2026-07-23 with no replacement — which is *why* `approve_proposal` has zero
  callers and #646 exists), so this RFC builds the first approval mechanism, not a retirement: a
  button **on the agent's rendered reply, in the conversation, on the chat surface** — the same
  interactivity seam `ConnectClick` and the answer buttons already ride. The click is
  deliberately not a *turn* (the model must never re-enter the decision path), but it is the chat
  transport, not a bespoke endpoint.
- **Both ends or neither.** Unit 1 ships the journal *and* a read that uses it (object history,
  rollback) before any gate exists. The `evidenced` bar does not land until units 4 and 5 build
  its producer. The `feedback` record's migration adds only the column its unit wires.
- **One shape.** The governed apply — resolved effect, journaled, invertible — is the single
  mechanism: a prompt promotion, an object mutation, and a rollback are the same act with
  different manifests. The candidate arm is likewise a real agent row, not a shadow prompt
  threaded through the turn path — no second source of "what prompt is this turn running."
- **Fail loud:** a wrong member refuses, an over-cap rollback refuses, a proposal naming a
  never-reachable ref refuses to open, a gated act on a surface with no decision path refuses,
  `seed_candidate_agent` refuses a non-pending proposal or an empty prompt body.

## Alternatives

| Option | Why not |
|---|---|
| **Leave the loop closed** (status quo) | It spends model tokens hourly to fill a table with no exit — the most expensive no-op in the repo. |
| **Auto-apply passing candidates** | The gate's power says no: LR ~2.3:1 at `N_FLOOR = 4`, silent above a ~0.96 baseline, global stage passing by default under-floor. OpenClaw shipped the proposal flow and then defaulted it to `auto`; that is the failure to avoid, not the precedent to follow. |
| **A tier taxonomy across many gated refs, now** | Every additional click discounts the ones that matter (fatigue is measured). One gated ref, then generalize when a second earns it. |
| **A/B by mutating the live agent prompt between runs** | Pollutes the production agent, races other turns, and breaks the compare-and-swap invariant. The scratch agent is disposable and isolated. |
| **Pack-treatment arms** (as GDPval does) for prompt A/B | Too coarse — a treatment is a whole `[pack]` config; we want to vary *only* the prompt body, holding pack/tools/model fixed. |
| **Keep the replay gate as the sole evidence** | Narrow by construction: one mined class, held-out members only, one judge. Misses cross-suite regression — the exact risk a whole-prompt rewrite carries. |
| **HF via a member-facing `/feedback` endpoint** | Violates "every member action happens in chat" — the signal must ride the surface (reaction / correction turn), never a bespoke endpoint. |
| **Version rows per kind instead of one `revision` table** | Gives history and no decision: who approved, on what basis, and what to invert are exactly the columns a per-kind version table lacks. |

## Open decisions

1. **HF signal source.** Explicit reaction, mined correction turn, or both? Explicit is a cleaner
   label but sparse; mined is dense but noisier. A portfolio: land explicit first (high
   precision), add mined as a second producer once the corpus consumer is proven.
2. **Gate composition.** Does the gate *require* an eval-harness pass before opening a proposal,
   or does replay stay the gate with eval impact riding along as advisory evidence? (Cost: a
   suite run per candidate is far more expensive than replay.) Recommend: replay is the gate; the
   eval impact is attached evidence the approver reads — until we have data that replay-passing
   candidates regress suites often enough to justify the spend.
3. **Scratch-agent cleanup.** A settled proposal's scratch agent has no further
   use; nothing removes it today beyond discarding the disposable workspace. Whether that stays
   the answer or a settle-time delete is worth wiring is open until candidate runs happen
   anywhere longer-lived.
4. **Hash-chained records?** No vendor ships tamper evidence for agent audit and no standard
   demands it — and the EDPB now advises against personal data on immutable structures even
   hashed. If chained, chain non-personal fields only. Deferred with that constraint recorded.
5. **Step-up authentication for the gated ref.** A compromised Slack account is currently full
   approval capability.
6. **The predetermined-change envelope** (AI Act Art. 43(4)): per-ref bounds as the artifact a
   standing delegation is measured against. Needs a consumer before it lands.
