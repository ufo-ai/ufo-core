---
rfc: 0025
title: "Deep work — control over what may proceed, and insight into whether it is converging"
status: rejected
date: 2026-08-02
---

# Deep work — control over what may proceed, and insight into whether it is converging

> **Rejected on its own evidence.** Every closure form this proposes that a person or an agent
> performs was measured against HANDBOOK.md and scored *below doing nothing*: an agent checking its
> own work, 62/73, and a second agent checking it, 67/73, against a 69/73 baseline. The reason is
> recorded in full below and is the reusable part — a second reader of the same kind misses what the
> first missed, so separating the interests does not separate the attention. The one form never
> tested is a check the extension evaluates rather than anyone reads. Nothing here is built.
>
> Half of all HANDBOOK.md failures are near misses — eleven of thirteen graded requirements met, the
> case lost on one. Reading those failures against the upstream rubrics, every one is a checkable
> condition on final state that the agent believed it had satisfied: eleven a required end state
> never reached, four a prohibition it violated. The work reports itself finished while a condition
> anyone could have checked is false. And nothing measures whether a unit is converging, so an agent
> buys round after round with no signal to split instead. Asking the agent to write and check its own
> conditions has been tried and measured here: it makes the work worse, because the author writes the
> condition it can pass. What follows is the design where the check is not the author's to soften. This proposes one extension answering both — **control**, a
> small set of refusals over what may start and what may be called done, and **insight**, one view
> of that state shared by every worker and every later turn. `todos` has neither: any worker ticks
> any box, and the board is invisible to every other worker.

## Current state

**The failure is measured.** The 07-30 HANDBOOK.md sweep scored 15/65 (23%). Among the 50 failures,
**15 lost on exactly one rubric and 25 on two or fewer**, against a median of 13 rubrics per case:

| Failed rubrics | Cases | Cumulative |
|---|---|---|
| 1 | 15 | 30% |
| 2 | 10 | 50% |
| 3 | 9 | 68% |
| ≥4 | 16 | 100% |

Grading is upstream's own 824 deterministic rubrics on a clean-sweep bar
(`evals/handbook/README.md`), so these are not judge artefacts. Reading the fifteen one-rubric
failures against their rubric text: **eleven are a required end state never reached** — a row absent
from a spreadsheet, a PDF never written, an email never sent — and **four are prohibitions the agent
violated**, such as "there must be no sent emails in `mailbox.json`". Every one is machine-checkable
against state, and none is a step that ran before its prerequisite.

**Completion is self-reported.** `extensions/todos` lets `update_todo_status` move any index to any
status on the caller's own assertion (`ufo_ext_todos.py:108-119`), keeps no record of what was
attempted, and models no dependency between tasks — its tasks are a flat tuple (`:52-53`). A board
is keyed `todo/{conversation_id}` (`:89`), so a parent and every child hold separate, invisible
copies. Nothing records what a task must be true for, and nothing can contradict a worker that says
it finished.

**The doctrine is customer-facing and reaches one agent.**
`core/src/ufo/loop/prompts/shell.md:67` carries the portfolio rules in full. `SHELL` renders into the
main agent's prompt alone (`core/src/ufo/loop/prompts/render.py:34`); a subagent is built from
`subagent_shell.md` (`core/src/ufo/loop/subagents.py:62-67`), which carries none of it.

**Workers share files and nothing else.** `spawn_subagent`
(`core/src/ufo/tools/builtins.py:600`) mints the child a fresh conversation
(`core/src/ufo/loop/subagents.py:163-170`) while inheriting the parent's sandbox
(`subagents.py:370`), so `/workspace` is the only shared channel — untyped bytes, no ids
(`core/src/ufo/skills/delegation/SKILL.md:14-23`).

**Nothing says whether a unit is converging.** A worker can iterate indefinitely with no reading of
whether the work is closing. The one loop in this repository that has such a reading —
`.claude/skills/babysit-prs/SKILL.md:133` — treats a round count that climbs while the blocking count
holds flat as the signal to stop pushing and split. Both roles carry the same reading, so either can
call it. A member's agent has no equivalent, and no state from which one could be computed.

**Where both halves are proven.** The same pair of skills. Control:
`.claude/skills/review-pull-request/SKILL.md:196-198` moves a finding to done on
`F<n> — verified fixed: <sha>`, written by the reviewing agent and never the author. Insight: the
ledger and the round count, which is what lets either side decide between iterating, splitting, and
escalating. They are Claude Code skills over this repository, so neither has ever reached a member's
agent.

## Proposal

**The design below was never built and never validated.** Code review raised defects in it that were
not fixed, because the measurement retired the approach before they mattered: the tables declare no
audience scope beyond `workspace_id` (F24), the plan tool can drop a task and orphan its events
(F34), the revision lock does not cover a unit's first write (F35), `recorded_by` as written cannot
tell two workers on one profile apart (F22), and the unit proofs do not fire the constraints they
claim (F25, F26). They are recorded here rather than corrected — a reader taking this schema as a
starting point should treat it as an unfinished sketch, not a specification.

One extension, `deep_work`, replacing `todos`, with tables of its own through its own migration —
the pattern `memory_item` establishes (`spec.md:62`) and
`extensions/sample/migrations/0001_sample_ext_note.py` shows, reached through
`ExtensionContext.transaction()` (`core/src/ufo/ext/context.py:649`).

### The record

Every table carries `workspace_id`: `bootstrap_policies` raises on a public table without one
(`servers/control/src/ufo_control/rls.py:187-191`), and `transaction()` enforces no scoping of its own
(`core/src/ufo/ext/context.py:655`), so the column is both the RLS requirement and the tenant fence.

| Table | Columns |
|---|---|
| `deep_work_unit` | `workspace_id`, `unit`, `title` |
| `deep_work_task` | `workspace_id`, `id`, `unit`, `title`, `accepts` |
| `deep_work_event` | `workspace_id`, `task_id`, `kind`, `actor_turn_id`, `evidence`, `created_at` |

`kind` is `did`, `confirmed`, `rejected`, or `blocked`. Events are append-only — never updated, never
deleted — so a plan revision cannot erase what happened, and **a task's state is derived, never
stored**:

| Derived state | From |
|---|---|
| `pending` | no events |
| `awaiting confirmation` | a `did` newer than any `confirmed` or `rejected` |
| `done` | a `confirmed`, newer than any `rejected`, whose `actor_turn_id` differs from the `did`'s |
| `rejected` | a `rejected` newer than the latest `did` — the work must be redone and confirmed afresh |
| `blocked` | latest event is `blocked` |

A unit is done when every task is `done`, so **any actor can hold a unit open indefinitely by
rejecting one task**. That is the point: refusing is the counterpart of confirming, and without it
"not confirmed" cannot be told from "nobody has looked". Rejection also removes any need for a
reopen verb — a rejection *is* the reopen.

`actor_turn_id` is the turn's own id (`core/src/ufo/schema/records.py:212-213`), stamped by the
extension and never supplied. It is per **worker**, not per profile: every subagent is its own turn, so two
workers running `general_purpose` are still distinguishable — which a profile name could not do.

### Control — three refusals

**A task closes only through a party with a different interest.** Free-text evidence accepted from
the worker that did the work is not such a party — it is what `todos` ships, and it is why nothing
today can contradict a worker that says it finished. A closure is admissible in exactly three forms:

| Form | Where the different interest comes from | Enforced by |
|---|---|---|
| `confirmed` by a different `actor_turn_id` | another worker, ideally under an adversarial brief | an equality check — **measured, and it did not hold**: see arm 2 below |
| An `accepts` check declared on the task **at plan time**, evaluated at closure | the earlier commitment — a check written before the work was known to be hard | the extension evaluates it |
| An in-band command declared on the task, whose exit status decides | the check itself | the sandbox runs it |

The second is the one that makes a single worker safe, and time is what separates the interests:
the acceptance test is fixed when the plan is written and cannot be softened once the work turns out
to be difficult. It is also what "the evidence must quote a read-back" becomes when made
enforceable — the check names the state to re-read, and the extension, not the worker, decides
whether it holds.

None of these asks the extension to judge whether evidence is *good*. Each is an equality, an
evaluation, or an exit status.

**There is no dependency gate, and the data is why.** An earlier draft refused to start a task whose
`blocked_by` was unfinished. Classifying the fifteen one-rubric HANDBOOK.md failures against the
upstream rubric text settled it: four are prohibitions the agent violated and eleven are a required
end state never reached. **None is a step that ran before its prerequisite.** A `blocked_by` column
would have been a field with a gate and no measured failure to catch, so neither ships.

**A task nothing depends on may close on its own worker's word, and that closure is bookkeeping
rather than control.** It is stated that way deliberately: such a closure carries no guarantee and
would measure exactly as `todos` measures today. It is admissible only because a false `done` does
damage by propagating, and nothing depends on this one — not because the worker's word is worth
anything. Anything with a dependent takes one of the three forms above, whatever it costs.

**Nothing wedges.** If no second actor is available, the task takes a `blocked` event whose evidence
is the open question, and the parent escalates with `ask_user` — a subagent cannot ask
(`core/src/ufo/loop/profiles.py:5-8`). A task never waits forever on a confirmer that cannot exist.

Confirmation is an act, not a task: it takes no row in `deep_work_task` and nothing confirms a
confirmation.

### Insight — one view, and a reading of whether it is closing

The same record answers the questions no worker can answer today, because every worker and every
later turn reads the one unit rather than its own copy: which conditions a task must satisfy, which
are unmet, what has been attempted against it and by which actor, and what is blocked on a member.

Two counts come off the events and neither is writable: **attempts**, the `did` events for the unit,
and **confirmed**, its tasks in `done`. Attempts climbing while confirmed holds flat is a unit
buying rounds rather than closing them — the reading `babysit-prs` uses to stop pushing and split.
The extension reports the two numbers; the skill decides what they mean, because whether to split,
escalate, or iterate once more is a judgment and not a refusal.

That division is the whole shape of the design: **control is what the extension refuses, insight is
what it shows, and every judgment sits with the skill and the actors.** A gate that needed to know
whether evidence was *good*, or whether an approach was *materially new*, would be on the wrong side
of it — which is why neither exists here.

### Tools

Each declares `subagent_default=True` (`core/src/ufo/tools/registry.py:39`) — the flag
`core/src/ufo/loop/queue.py:288` filters on, without which a child holds none of them.

| Tool | Does |
|---|---|
| `update_work_plan(unit, title, tasks)` | Creates or revises tasks and each task's `accepts`. Writes no events, so it cannot move state, and an `accepts` cannot be weakened once the task has a `did` |
| `record_work(task, kind, evidence)` | Appends one event as this turn: `did`, `confirmed`, or `blocked` |
| `read_work_unit(unit)` | Every task with its derived state, its `accepts` and which conditions are unmet, every event and its actor, and the attempts/confirmed counts |

### Two surfaces

A prompt section keeps simple work cheap — plan, do, confirm — since `todos`' always-on section
(`ufo_ext_todos.py:140`) is the only reason checklists happen at all today. The skill carries what no
column can: who should confirm what, when to split, and reading the events before re-attempting a
route. Its trigger:

> Load when work spans several dependent steps or more than one worker — a wedged bug, an open
> design, a multi-stage investigation. E.g. get a step confirmed by someone other than whoever did
> it; check the conditions a step must satisfy; resume what an earlier turn left blocked.

### Removing `todos`

`todos` owns no tables, so the removal drops the package, its section, its `ext_store` rows in the
same migration (`CLAUDE.md`: "Removing an extension drops its tables *and* its `ext_store` rows in
the same migration — nothing else ever will"), its four pack entries
(`packs/assistant_dev/ufo_pack_assistant.py:23`, `packs/assistant_hosted/…:32`,
`packs/chief_of_staff/…:32`, and the yc pack), its `pyproject.toml` entries, and the two skills that
instruct its tools (`packs/chief_of_staff/skills/sync/SKILL.md:37`,
`extensions/research/ufo_ext_research/skills/research-assistant/SKILL.md:41-53`).

## What was measured

HANDBOOK.md is the instrument. Two arms have run; the third has not.

**Arm 0 — the recorded baseline.** 15/65 (23%). Reproduced exactly on one case before running
anything (`medical_pathfinder_billing_and_coding_a25684b9`, 5/6 both times), so the harness is sound.

**Arm 1 — the acceptance discipline as instruction.** An agent identical to baseline but for a prompt
telling it to derive every condition that must hold of real state, write them down before acting, and
settle each at the end by re-reading the state it names rather than trusting its memory of acting. No
extension and no refusal, so the only variable is the discipline. Run against the 15 one-rubric
failures and, to measure damage, the 15 cases baseline already passed.

| | Cases passing | Rubrics |
|---|---|---|
| Near-miss 15 | 0 → 7 | 183 → 186 / 198 |
| Passing 15 | 15 → **11** | 180 → **169** / 180 |
| Combined | 15 → 18 | 363 → **355** / 378 |

The mechanism executed everywhere — all 30 runs wrote a list, and the lists name exact sheets, rows,
and values, including "must NOT" conditions derived unprompted. It still made the work worse: the
clean-sweep count rose only because the near-miss set was selected for being one rubric from flipping,
while the rubric total fell.

**Why it fell, root-caused.** The worst regression (`finance_meridian_partners_331accf1`, 19/19 → 11/19)
reproduced across three runs — 11, 11, 15 — with the same two rubrics failing every time, so it is a
defect and not variance. The agent declared the condition as *"W9_Whitfield_Consulting_Group.pdf
exists"*. The rubric requires that file **in `1099/2025/`**. It wrote the files to the workspace root,
never created the folder, checked "does the file exist?", found it, and marked the condition verified.

**The check ran, passed, and was wrong** — and baseline, with no discipline at all, had filed the same
files correctly. A condition authored by the party that will verify it can be underspecified in
exactly the way that makes it pass, and a check like that is worse than no check because it
manufactures confidence. That is this proposal's own thesis turned on the weakest of its three closure
forms, and the result retires that form: **prose conditions verified by their author do not ship.**

What the result does not touch is the executable form. `accepts` evaluated by the extension would have
required the path as data, so the omitted folder would have been a malformed check rather than a
vacuously true one — the failure is an argument for enforcement, not against it. Nor does it touch
closure by a second actor, which no arm has yet tested.

**Arm 2 — closure by a second actor.** An agent told to hand every deliverable to a
`general_purpose` subagent along with the *original request* — explicitly not its own checklist or
summary — and to have that worker derive for itself what must be true and read the state. Seven
cases, interleaving near-misses and cases the baseline already passed.

| Case | set | base | arm 1 | arm 2 |
|---|---|---|---|---|
| `331accf1` | passing | 19/19 | 11 | **15** |
| `cc2fc143` | near | 6/7 | 5 | **6** |
| `79786c46` | near | 20/21 | 21 | 21 |
| `d93daa01` | passing | 8/8 | 8 | 8 |
| `f6d19d30` | near | 6/7 | 7 | 7 |
| `9d274282` | passing | 5/5 | 5 | 5 |
| `a25684b9` | near | 5/6 | 5 | 5 |
| **total** | | **69/73** | **62/73** | **67/73** |

A second actor beats a self-authored check and still loses to doing nothing. It flips two near-misses
and repairs most of arm 1's damage, and it still breaks a case that worked.

**Why it breaks is the result.** `331accf1` failed the same way under both arms, on four rubrics of
one shape: *"In folder `1099/2025/`, file `W9_Whitfield_Consulting_Group.pdf` must exist."* The
verifier — handed the request rather than the doer's account, and asked to derive requirements
itself — checked that the files existed and never checked where. **The blind spot is shared.**
Separating the interests does not separate the attention: two readers of the same kind miss the same
thing, so a second actor is a weaker instrument than it appears and cannot be assumed to catch what
the first missed.

That refutes the reading this RFC carried after arm 1 — that the second-actor form stood untouched.
It fails the same way, less severely. **Both human-shaped closure forms are now measured and both
lose to no discipline at all.**

What survives is the form that needs no attention: an `accepts` check evaluated by the extension
catches a wrong folder because the path is *data*, compared literally. Neither reader compared it.
The remaining question in this proposal is therefore narrow and testable — whether an agent asked
for machine-checkable conditions writes better ones than an agent asked for prose — and nothing here
has answered it.

**Cost, measured.** A handoff is a loop, not a step: 2-4 verifier children per case, each a full
child turn, serialized behind the parent — 3-4x baseline. Nothing in the design bounds the rounds.

**One thing did work.** Across 18 spawn calls in arm 2 there were **zero** interface failures, against
40% of 45 calls before `spawn_subagent` began publishing the deploy's profiles and their payload
fields. Delegation was undiscoverable and is not any more.

## Doctrine fit

**Not core** — `tools`, `skills`, `prompt_sections`, and a migration shipping inside the extension
package (the loader finds `migrations/` by convention, `core/src/ufo/ext/loader.py:88`; it is not a `Manifest`
field).

**Both ends.** `unit`/`title` → the projection; `accepts` → the closure evaluation and the unmet-condition list;
`kind` → the derived state; `actor_turn_id` → the confirmation gate; `evidence` → the non-empty check
and what a later worker reads; `created_at` → event order.

**Fail loud** — every gate raises; none degrades to a permissive default.

**One shape** — `spec.md:706` forbids "a second representation of any fact", which is why `todos` is
removed rather than bounded against.

## Alternatives

| Option | Why not |
|---|---|
| Keep `todos`, add dependencies to it | Its completion model is the defect, and dependencies are not what the failures are made of — eleven of fifteen are a required end state never reached, four are prohibitions, none is an ordering error |
| Record who did it and let the model weigh it | Showing provenance is weaker than refusing the transition, and it was this RFC's earlier answer. An identity comparison is enforceable; asking a model to discount its own worker is not |
| An append-only record of approaches tried, and nothing else | This RFC's own earlier drafts, and the wrong axis: a history is neither a refusal nor a reading. Within one context it is also close to unmeasurable — `evals/suites/dead_route_repeat.py` staged a deterministic trap over four units and the agent inspected before acting, so it never fired (run 7b1ce2a1). The events here exist to serve the gates and the counts, not as the point |
| Gate on evidence quality or novelty | An extension can judge neither; every such gate reduces to a non-empty-string check |
| Have the agent write its own conditions in prose and check them itself | Measured and refuted, not argued: arm 1 above. The author underspecifies the condition in the way that makes it pass — "the file exists", omitting the folder the rubric requires — so the check verifies true against the wrong state. Worse than no check, because it manufactures confidence |
| A mutable graph in `ext_store` under compare-and-swap | Three earlier rounds of this RFC: a single JSON value forces an unbounded retry against our own database |
| beads or a git-backed DAG CLI | A member's workspace is not a repository, and a CLI holding state beside the extension's tables is a second source of truth |

## Open decisions

1. **Does an executable `accepts` survive what a prose one did not?** Arm 1 refuted the prose form and
   left the executable form untouched, but "the extension evaluates the check" only helps if the agent
   states the check in a form that can be evaluated — a path, a cell, a record — rather than a
   sentence. Whether an agent asked for machine-checkable conditions writes better ones than an agent
   asked for prose is the question arm 2 answers, and it is the load-bearing assumption of this
   proposal.
2. **What scope beyond `workspace_id`?** `memory_item` scopes by audience subject
   (`spec.md:62`), which a work unit may or may not need. Declaring an audience model this RFC has
   not shown a reader for would be a field ahead of its consumer.
3. **Does closure by a second actor earn its place at all?** Arm 2 measured it and the answer leans
   no: it scored below doing nothing and missed the same requirement the doer missed. Its cost is
   also now measured rather than estimated — 2-4 verifier turns per case. Keeping the form in the
   design needs a reason beyond "a different party checked", because a different party of the same
   kind demonstrably does not check differently.
