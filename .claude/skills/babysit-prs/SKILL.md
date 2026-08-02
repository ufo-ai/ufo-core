---
name: babysit-prs
description: Shepherd open metalcraftai/ufo pull requests to merge-ready — watch CI, respond to review, resolve threads — without hacking around design gaps, working around uncertainty, or adding complexity to get green. Use to babysit a PR after pushing, or to sweep the PRs this session opened or pushed to. Never merges.
---

# Babysit CI and code review

Touch only the pull requests named in the invocation. With no argument, touch only the PRs this session
opened or pushed to; if this session has none, say so and stop — never widen to `gh pr list --author @me`,
and never commit, reply, or resolve a thread on a PR outside that set, however broken it looks (another
session is holding it).

Read root `README.md`, `CLAUDE.md`, and `spec.md` first — `spec.md` is the design source of truth you judge a "design gap" against; also read a nested `CLAUDE.md`/`AGENTS.md` that governs a changed path before editing it. Fix each PR on its own branch, one worktree per PR; never combine fixes across PRs. Keep a per-PR note of what you tried and why it failed, and never rerun an unchanged mechanism.

You speak only where this file names a statement you owe: a blocker or a design decision that is the user's
to make, why you believe a cause is external before you rerun, a push onto an approved head, a completed
wait this file tells you to report, and the merge-ready state or truthful red at the end. Everything else
is silent — a pass, a routine push, waiting itself, a check you are watching, a finding you fixed, an
unchanged status, a plan for the next round. One line when you do speak: `Two valid issues. Fixing.`

## What you may and may not do

You may diagnose, patch, test, commit, push, reply to reviewers, and resolve threads. You may **not** merge a PR or enable auto-merge without explicit human approval. Your job ends at merge-ready, not at merged.

## Fix only proven defects

A push must never change what the PR does to pass a check or satisfy a reviewer. Classify before you touch code:

- **Proven implementation defect** — code contradicts behavior the diff, tests, repo instructions, or an existing contract establish → make the **smallest contract-preserving root fix**, plus the proof (test) that would have caught it.
- **Ambiguous intent, a design gap, a fix that needs a new abstraction, or a contract change** → **stop and surface** (below). These are the three forbidden moves:
  1. No hacking around a design gap — fix the cause, not the symptom.
  2. No working around uncertainty — don't guess a contract, a fallback, or a branch because intent is unknown; find out.
  3. No new complexity — no abstraction, flag, alias, adapter, retry, or contract change to route around a failure. The fix stays in one shape.

Deletion, inlining, and simplification that restore the existing design are always fair.

## What earns a commit

Three things: **behavior**, **the proof of behavior the diff leaves unpinned**, and **any finding anchored
to a written rule** in `CLAUDE.md`, `AGENTS.md`, `spec.md`, or an existing contract — a rule-backed finding
is a defect whatever its category, naming and prose included, and no reply clears the gate it holds.

What is left is a preference with no rule behind it: wording, a name the repo does not govern, a log field,
a test's name, a reviewer's "consider". Answer those in a reply and resolve them; a commit spends a whole
review round, and a preference is never worth one.

A prose finding therefore has exactly two ends and no third, and both are decided in **this** round.

**The sentence goes**, by deletion, in this round's push: whenever a written rule backs the finding, whatever
verdict this head carries, and whenever the sentence is false at a head no `APPROVED` verdict has reached.
**Or the sentence stays** and one reply says why, and only where no written rule backs it: it is true, or this
head is already approved and the cut is worth less than the approval it would spend. Never a deletion promised
for a later push — a thread resolves only once the pushed head carries the fix, so a sentence left standing is
left standing for a stated reason, not owed.

A false sentence is not a wording problem, and "advisory" is not a licence to negotiate it. The code is the
documentation: the sentence had one job and does not do it, so it goes unless it is the one case above, an
unbacked sentence at an approved head.

When the sentence does go, it goes by **deletion** wherever the code reads without it (CLAUDE.md: no
comments; enforce, don't document), keeping only a public API's docstring, in the fewest words that are
true. A reworded claim is a new claim for the next round to re-litigate; the deletion, or a test that makes
the claim true, ends the class. Count the wordings you have shipped of one sentence: at two, the sentence
is not carrying its keep — cut it and let the code say it.

## One push per round, and it fixes the class

A round is: collect every finding at this head → address all of them → sweep → **one** push. Never push per
finding, and never push while a finding from the same head is **unaddressed**; each extra push re-runs a
full review of the whole diff. Addressed covers the reply you stand behind, not only the fix — a disputed
finding and a surfaced design gap are answered where they are and never hold the push for their round's
committed fixes.

The sweep is a review you run on yourself: read `git diff origin/main...HEAD` whole, as the reviewer will
read it at the head you are about to create, and hold it to `.claude/skills/review-pull-request/SKILL.md` —
the same rules, the same repo instructions, the same both-ends and new-function-new-test bars. A finding
you catch here costs nothing; the same finding caught after the push costs a full round.

Close the class rather than the line. The reviewer's line number is one example, not the inventory:

- the same defect at every sibling site in the diff;
- the surface your own fix just created — a new branch, parameter, state, event, or window is unproven
  behavior and carries its own test **in this push**;
- what the fix just made untrue elsewhere: a docstring, a sibling test's assumption, a gate's coverage.

A next round that says *the other half of your fix has no test*, *the injection moved the hole*, or *the
new drain stacks a second window* is the sweep you skipped, not a nitpick.

## A fix that needs a new mechanism is a scope call, not a push

Closing a finding sometimes takes something the diff does not have yet — a field on a shared type, a stamp
on a write path, a gate, a validator, a new call site on a seam. Pushing it here does not close the round.
Nothing has reviewed it, so it lands as a first review layered on a diff already under review: the next
round closes every lens on it, finds its own findings, and your fix earns a fix. One mechanism per round is
how a one-file change becomes fifteen files and eight rounds, with the blocking count flat the whole way.

Size the fix before you write it. A branch, a parameter, a state, or an assertion inside the shape the diff
already has is this round's push, and it carries its own test. A mechanism is a unit of its own: **surface
it** — name the finding, the mechanism it needs, and that reviewing it here costs a fresh full round.
Whether it stacks as its own pull request or this one grows is the human's call, and pushing it is making
that call silently.

## An approval is not spent on an advisory

Once a head carries an `APPROVED` verdict, no advisory earns a push of its own: the findings the approval
body names, and any thread still open on taste, are answered by reply and resolved, because a push to
satisfy them throws the approval away and buys a fresh round for nothing.

The approval body's advisory list is answered the same way, every entry: one reply, then resolve. That is the
second end above — an entry whose sentence merely stands says so, and an entry whose sentence is false says
that, because an approved head is the ground that end already names. No entry waits on a later push.

A push the rest of this file requires is still owed at an approved head — a red required check, a `dirty`
merge state, a rule-backed finding, a defect the approval missed. Take that round deliberately. A thread you
were told to leave open — a design gap you surfaced — stays open.

## Every new head resets the review

Re-read the head SHA at the start of every pass; a push invalidates prior CI and review conclusions, so discard findings from an older head. A push is progress, not completion. Every push resets the AI gate to pending and earns a fresh review + verdict — a per-push pending gate is expected, not a failure.

Claude's head-anchored verdict is the one that controls the **AI Review Gate**; but all valid review feedback matters regardless of who left it — act on every correct finding, push back with evidence on the rest.

## Watch and repair CI

- After any push, run `gh pr checks <n>` immediately. If checks are pending, start `gh pr checks <n> --watch` as a **background** command so the harness re-invokes you on completion, then report and act. Never end a turn with checks pending.
- Diagnose a failing check to its cause and fix it there. This is a realtime system: **never rerun, sleep, bump a timeout, or swallow an error to make an internal failure go away.** Rerunning CI on our own code is not a fix.
- Retry is legitimate only after you **prove** the cause is external and transient (model, OAuth, network egress) — never our DB, DBOS, queue, or code. Say why you believe it's external when you rerun.
- A failure that already reproduces on `main` (this PR didn't cause it) is an external blocker — record it, don't absorb the fix into this PR.

## Respond to review

- Triage from the ledger and the review **summary**, not the inline list. A finding is published once and carried by its row, so the inline list holds threads from every earlier head; the summary names this round's ids by bucket and the ledger says where each one stands.
- A summary opens with its round number, where the round had a summary to write — a marker-only verdict carries no body and so no count. Read the number as a measurement of your own loop: a count that climbs while the blocking count holds flat means each push is buying the next round rather than ending one. The causes are yours — a mechanism in every push, a class fixed at one site, a reply the code does not back. Name which one it is and surface it; another push is not the answer to it.
- Every finding carries an `F<n>` id and one row in the ledger — the single PR comment holding `<!-- claude-review-ledger -->`, edited in place every round. Read it before the inline list: a `todo` row is open and a `done` row is settled and asks nothing of you. Cite the id in every reply and in anything you surface — the row is where the human reads what is still open.
- A row moves on evidence the reviewer reads, never on your say-so: your fix moves it when the reviewer verifies it at the pushed head.
- Fix a valid finding at the root, reply on its thread beginning `F<n> — `, then resolve it — but only after the pushed head contains the fix (GraphQL `resolveReviewThread`; thread ids from `pullRequest.reviewThreads`).
- A reply is at most two sentences: what changed, and the test that holds it. Never restate the finding, thank the reviewer, narrate agreeing, or transcribe your reasoning — the evidence belongs in the code and the test, where the next reader is. Reply on the thread, never as a new PR-level comment.
- A reply claiming a fix names a check you **ran at the pushed head** and its result — the focused test with its counts, or the mutation you reverted to watch it fail. Never `Fixed` from the edit you believe you made: the reviewer settles it against the code, so a claim the head does not carry re-publishes the finding and burns the round that reply was meant to close. Unrun means unfixed — say what you changed and that it is unverified.
- A finding you disagree with, or a reviewer preference that is not a correctness issue or a repo rule: reply with the evidence and resolve — never churn the branch to satisfy taste, never silently leave it open.
- A finding that exposes a design gap is not patched shut to clear the thread. Surface it.

## Stop and surface

When clearing a check or a finding would require hacking a design gap, guessing through uncertainty, or adding complexity/changing a contract: stop. Leave the PR red, resolve nothing dishonestly, and tell the human plainly what the check or reviewer is actually asking for and why the clean fix is a design decision. A truthful red beats a green that hides a hack.

## Done = merge-ready

Complete a PR only when one snapshot of the **current head** proves all of:

- every required check green — `test`, `checks`, `rls`, `deployment`;
- the **AI Review Gate** green (a live `APPROVED` Claude verdict on this head);
- zero unresolved review threads;
- mergeable with no conflict; and
- nothing left uncommitted or unpushed.

Report that state; do not merge and do not arm auto-merge yourself. If auto-merge was already armed by an authorized human, wait for it and confirm the merge landed — never assume it.

## Gotchas

| Situation | Response |
| --- | --- |
| No PR named in the invocation | Only the PRs this session opened or pushed to. None → say so and stop; a wider sweep clobbers another session's branch mid-flight. |
| `APPROVED` head whose only open findings are advisory | Reply and resolve; no push. A push here would trade a green gate for another full round. |
| `APPROVED` head with a red check, a `dirty` merge state, or a rule-backed finding | Fix and push — the approval was never a bar to that. |
| A round raises only wording or docstrings with no written rule behind them, and the sentences are true | Reply that the sentence stands, and resolve. Nothing is owed, so nothing is promised for a later push. |
| The sentence the finding names is false at this head | Cut it in this round's push. Where no written rule backs it and this head is approved, reply that it is false and resolve instead — the cut is not worth the approval. Never reword it: a second wording is a second claim, and the class only closes on the cut. |
| You are about to reply `Fixed` | Name the check you ran at this pushed head and its result. A reply the code does not back costs the whole round. |
| Closing the finding needs a field, stamp, gate, validator, or call site the diff does not have | That is a unit, not a push. Surface the scope call; pushing it layers a first review onto a diff already under review. |
| A reply names a line instead of the finding's `F<n>` | Cite the id. The ledger row is what the human reads, and a reply that does not name it leaves the row unreadable. |
| A prior finding's thread is open but the newest summary does not name its id | The row says where it stands. A finding is published once, so an old thread is not this round's finding, and a `todo` row is open whether or not this round restated it. |
| The verdict's round number climbs while the blocking count holds flat | Your pushes are buying rounds. Find which cause it is — a mechanism per push, an instance fix, an unbacked reply — and surface it instead of pushing again. |
| A new finding lands while this head's fix is still unpushed | Fold it into that same push; one push per round. |
| The finding names one line, one call site, one test | That line is an example, not the inventory. Fix the cause that let it exist and every sibling it already reached; an instance fix comes back as the next round's finding. |
| Your fix adds a surface — a branch, parameter, state, event, window, column | Unproven behavior. Its test lands in the same push, or the next round files it as untested and you pay the round. |
| Checks pending at end of turn | Background `gh pr checks <n> --watch`; circle back before claiming done. |
| `Publish AI review status` passed but `AI Review Gate` is pending | The workflow ran fine but found no current-head Claude verdict. Inspect the reviews; never read the job result as the gate result. |
| PR is draft and the Claude job posted no verdict | Expected — the review skill stops on drafts. Report the draft state; never mark it ready without explicit approval. |
| Only the gate runs; `pull_request` workflows silent | `gh api .../pulls/<n> --jq .mergeable_state` = `dirty` → the branch conflicts; merge `origin/main` in (no force-push) and re-push. |
| All checks green + `APPROVED` but merge still `BLOCKED` | An inline thread is unresolved — reply + `resolveReviewThread`. |
| Gate stuck pending, review job green, zero reviews posted | The review agent (a model) ended its run without posting a head-anchored verdict — a stochastic model outcome, the external exception, not a fault in your PR. `gh run rerun <claude-review run id>` re-rolls the model and never touches the PR; if reruns keep coming back verdict-less, surface that rather than editing the PR to chase the gate. |
| A test is flaky | Root-cause the race/ordering; a rerun or `-p no:randomly` masks it and is not a fix. |
| History rewrite needed (rebase/squash a fixup) | `git fetch` immediately before `--force-with-lease`; never overwrite a head that advanced elsewhere (someone may be pushing to the same branch). |
