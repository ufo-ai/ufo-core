---
name: babysit-prs
description: Shepherd open metalcraftai/ufo pull requests to merge-ready — watch CI, respond to review, resolve threads — without hacking around design gaps, working around uncertainty, or adding complexity to get green. Use to babysit a PR after pushing, or to sweep your open PRs. Never merges.
---

# Babysit CI and code review

With no argument, sweep the PRs you authored (`gh pr list --author @me --state open`); otherwise touch only the named PRs. Read root `README.md`, `CLAUDE.md`, and `spec.md` first — `spec.md` is the design source of truth you judge a "design gap" against; also read a nested `CLAUDE.md`/`AGENTS.md` that governs a changed path before editing it. Fix each PR on its own branch, one worktree per PR; never combine fixes across PRs. Keep a per-PR note of what you tried and why it failed, and never rerun an unchanged mechanism.

Between current-head passes, tell the user only material findings, blockers, decisions, or completed wait results, in the fewest words possible; omit tool narration, routine checks, the act of waiting, and unchanged status. Prefer `Two valid issues. Fixing.`

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

## Every new head resets the review

Re-read the head SHA at the start of every pass; a push invalidates prior CI and review conclusions, so discard findings from an older head. A push is progress, not completion. Every push resets the AI gate to pending and earns a fresh review + verdict — a per-push pending gate is expected, not a failure.

Claude's head-anchored verdict is the one that controls the **AI Review Gate**; but all valid review feedback matters regardless of who left it — act on every correct finding, push back with evidence on the rest.

## Watch and repair CI

- After any push, run `gh pr checks <n>` immediately. If checks are pending, start `gh pr checks <n> --watch` as a **background** command so the harness re-invokes you on completion, then report and act. Never end a turn with checks pending.
- Diagnose a failing check to its cause and fix it there. This is a realtime system: **never rerun, sleep, bump a timeout, or swallow an error to make an internal failure go away.** Rerunning CI on our own code is not a fix.
- Retry is legitimate only after you **prove** the cause is external and transient (model, OAuth, network egress) — never our DB, DBOS, queue, or code. Say why you believe it's external when you rerun.
- A failure that already reproduces on `main` (this PR didn't cause it) is an external blocker — record it, don't absorb the fix into this PR.

## Respond to review

- Triage from the review **summary**, not the inline list — the review re-anchors every prior finding to each new head, including ones the last push already fixed. The summary names the genuinely new findings.
- Fix a valid finding at the root, reply on its thread, then resolve it — but only after the pushed head contains the fix (GraphQL `resolveReviewThread`; thread ids from `pullRequest.reviewThreads`).
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
| Checks pending at end of turn | Background `gh pr checks <n> --watch`; circle back before claiming done. |
| `Publish AI review status` passed but `AI Review Gate` is pending | The workflow ran fine but found no current-head Claude verdict. Inspect the reviews; never read the job result as the gate result. |
| PR is draft and the Claude job posted no verdict | Expected — the review skill stops on drafts. Report the draft state; never mark it ready without explicit approval. |
| Only the gate runs; `pull_request` workflows silent | `gh api .../pulls/<n> --jq .mergeable_state` = `dirty` → the branch conflicts; merge `origin/main` in (no force-push) and re-push. |
| All checks green + `APPROVED` but merge still `BLOCKED` | An inline thread is unresolved — reply + `resolveReviewThread`. |
| Gate stuck pending, review job green, zero reviews posted | The review agent (a model) ended its run without posting a head-anchored verdict — a stochastic model outcome, the external exception, not a fault in your PR. `gh run rerun <claude-review run id>` re-rolls the model and never touches the PR; if reruns keep coming back verdict-less, surface that rather than editing the PR to chase the gate. |
| A test is flaky | Root-cause the race/ordering; a rerun or `-p no:randomly` masks it and is not a fix. |
| History rewrite needed (rebase/squash a fixup) | `git fetch` immediately before `--force-with-lease`; never overwrite a head that advanced elsewhere (someone may be pushing to the same branch). |
