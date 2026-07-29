---
name: review-pull-request
description: Review a metalcraftai/ufo pull request against its exact current head, leave only validated inline findings, and submit an approving or change-requesting verdict. Use for the repository's automated Claude code-review workflow.
---

# Review a pull request

The argument is the pull request URL. Do not modify its branch.

## Establish the review

1. Before any GitHub or diff command, read root `README.md` and the repository instructions. Read
   `AGENTS.md` when present; otherwise read `CLAUDE.md`. Fail if `README.md` or both instruction
   files are missing.
2. Read the pull request with one `gh pr view` call using explicit JSON fields: number, state,
   isDraft, title, body, baseRefName, headRefName, headRefOid, files, comments, and reviews.
3. Stop when the pull request is closed or draft. Stop when Claude already submitted `APPROVED`
   or `CHANGES_REQUESTED` on the exact head commit.
4. Record the head SHA. Capture `gh pr diff` once under `$RUNNER_TEMP` and reuse that file. Work in
   the existing full-history checkout; do not clone the repository or fetch an object already
   present.
5. Read any nested `AGENTS.md` or `CLAUDE.md` whose directory contains a changed file.
6. Save `python3 .github/scripts/prior_findings.py <owner/repo> <number>` under `$RUNNER_TEMP` and
   read it. It returns every finding Claude published on an earlier head with the replies on each
   thread, and a `round` of `first`, `follow_up`, or `undetermined`. `undetermined` means the fetch
   failed: treat it as a follow-up over every changed file, never as a first review.

Use Bash directly and deliberately. Combine related reads when that reduces calls, but never repeat
an equivalent command that failed without changing the mechanism.

## Review

Launch these three foreground agents together through `Task`:

- Audit runtime behavior, security boundaries, concurrency, and failure paths.
- Audit the diff against the applicable `CLAUDE.md` rules and existing local design seams.
- Audit tests and the full producer-to-consumer chain for every changed surface.

Give each agent the title, body, head SHA, captured diff path, and applicable instructions. Require
concrete findings with file, line, failure mechanism, and evidence. Reject status reports,
speculation, pre-existing defects, lint findings, and preferences not encoded in the repository.

Each agent finishes a changed file within its own lens before moving to the next one, including the
comments and docstrings in that file that describe what its lens covers. Closure across all three
lenses is yours to confirm when you synthesize: a file is finished when every lens has reported on
it, so a later review of this pull request never has to reopen it for a concern already visible at
this head. State the per-file rule in each agent's instructions.

### Write each finding to be the last one on its subject

Every round costs the author a full review cycle, so what one finding leaves unsaid the next round
bills for.

- **Name the class, not the instance.** A finding covers every case of itself visible at this head.
  When the code names several — a docstring's causes, an enum's members, a table's rows, two call
  sites of one helper — and the diff answers one, the finding names them all in the one comment.
- **Name what the fix must preserve.** A finding asking for something deleted, replaced, or moved
  states what the replacement still has to cover and where the surviving statement lives. A fix
  that satisfies the finding and breaks what the finding did not mention is the finding's defect.
- **Close new code the round it appears.** Code a follow-up commit introduces has never been
  reviewed: every lens reports on it now, to the same closure a first review gives. A mechanism
  audited one aspect per round is one finding spread over three.

On a `follow_up` round, give each agent the findings and replies from step 6 and bound its work to
two things: verify each earlier finding is actually fixed at this head, and audit what the new
commits changed. Step 6 returns `anchor_sha`, the head the newest earlier finding anchored to: when
it is set, capture `git diff <anchor_sha>..<head sha>` under `$RUNNER_TEMP` and give agents that
path alongside the cumulative diff, naming which is which. A null `anchor_sha` leaves no range, so
audit the cumulative diff instead and still verify each earlier finding. Re-derive a finding on
unchanged code only when an earlier finding's fix exposed it, and with a range in hand never on code
no commit since `anchor_sha` has touched.

The reply decides how much work verifying costs, never whether the finding holds. A reply naming a
commit is a claim to check against this head, and a finding whose reply disputes it or is missing is
checked the same way; only the code at this head settles it. Re-publish a finding the head does not
resolve, and never re-publish one it does.

Validate every returned finding against the exact diff and surrounding code. Keep the findings that
are grounded there; drop the rest. Run a focused test only when it is necessary to prove or disprove
a finding. Whether a kept finding blocks the merge is decided below, after validation, never here.

Two further drops on a follow-up round. With `anchor_sha` set, drop a new finding on a file no
commit in `<anchor_sha>..<head sha>` touched: the earlier round saw that content and published
nothing, and raising it now buys a round for code this pull request is done with. The range bounds
new work only — an earlier finding this head does not resolve is verified and re-published
wherever its file sits. Drop a finding that restores what an earlier round's finding removed, or
that reverses a disposition an earlier round settled — that is the review contradicting itself,
and it costs the author two rounds to arrive back where the diff already was. Publish it only by
stating which earlier finding was wrong and why.

### Blocking and advisory findings

Sort every kept finding into one of three buckets.

Blocking, because it changes what the code does: wrong behavior, an unhandled failure path, a
security or concurrency defect, or a changed surface with no test covering it.

Blocking, because the diff breaks a rule the repository writes down: a `CLAUDE.md`, `AGENTS.md`, or
`spec.md` requirement, or a contract an existing local seam already establishes. Structure and
design findings live here, and the written rule is what makes them blocking: quote it in the
comment. A structural finding you cannot anchor to a written rule is a preference, which the Review
section already rejects.

Advisory: a comment, docstring, or prose inaccuracy that does not change behavior. Publish it as an
inline comment and do not let it hold the verdict.
When every remaining finding is advisory, approve and name those findings in the approval body so
the author answers them in a reply, without a new head.

## Publish the verdict

Re-read `headRefOid` immediately before writing. If it changed, do not comment on or review the new
head from stale evidence.

For each validated issue, blocking or advisory, create one inline comment with
`mcp__github_inline_comment__create_inline_comment`. Then submit exactly one decisive review:

- Blocking findings of either kind: `gh pr review <number> --request-changes --body "<reason>"`
- No blocking findings: `gh pr review <number> --approve`, naming any advisory findings in the body

If GitHub refuses that command only because Claude authored the pull request, re-read the full
current head SHA. If it still matches the reviewed head, post one pull request comment containing
only `<!-- claude-review-verdict head=<full head SHA> verdict=APPROVED -->` for an attempted
approval or `<!-- claude-review-verdict head=<full head SHA> verdict=CHANGES_REQUESTED -->` for an
attempted changes request.

Finish only after GitHub records the verdict as a decisive review or the exact self-review marker.

## Gotchas

| Failure | Response |
| --- | --- |
| GitHub refuses a decisive review because Claude authored the pull request | Publish the exact-head marker matching the attempted verdict. Never use the marker for another review failure. |
| A pipe, redirect, or compound Bash command is rejected | Save command output under `$RUNNER_TEMP`, then inspect the file in a separate call. Do not retry an equivalent shell shape. |
| `gh pr view --comments` fails through an unnecessary GraphQL field | Request only the explicit JSON fields listed above. |
| Diff or log output is too large | Read the saved file in bounded chunks; do not rerun the producing command. |
| Git history or an object appears missing | Check the full-history checkout before fetching. Never clone the repository. |
| The head changes during review | Discard stale findings and stop without publishing them. |
| The action is green but no verdict exists | The review is incomplete until `gh pr review` succeeds. |
| Step 6 reports `undetermined` | The fetch failed; that is not evidence of a first review. Review every changed file rather than assume there is nothing to verify. |
| A `follow_up` round carries a null `anchor_sha` | No prior finding named a head, so there is no range. Audit the cumulative diff and verify the earlier findings anyway; never skip the round for want of a range. |
| Tempted to read prior findings with `gh pr view` or a filtered `gh api` | `gh pr view` returns no inline comments, and `gh api --paginate -q` applies the filter per page, keeping only page one. Run the script. |
| Only wording, comment, or docstring issues remain | Approve and list them as advisory. Requesting changes for prose costs the author a full review round. |
| A structural or design finding fits neither blocking bucket | It is blocking only with a written rule quoted from `CLAUDE.md`, `AGENTS.md`, `spec.md`, or an existing local contract. Without one it is a preference: drop it. |
| A follow-up review is about to re-audit a file an earlier round already flagged | Verify that earlier finding at this head instead. Audit the file afresh only where the new commits touched it. |
| The code names several cases and the diff answers one | Name every uncovered case in the one comment. A finding that covers the instance and not the class returns as its own round. |
| A finding asks for a deletion, a replacement, or a move | Say what the replacement must still cover. A rewrite that satisfies the wording and drops the cover is a round you bought. |
| A follow-up commit introduced a mechanism that did not exist before | Nothing has reviewed it. Close every lens on it this round rather than one aspect per round. |
| A new finding lands on a file no commit in the `anchor_sha` range touched | Drop it. It was visible to the earlier round, which published nothing on it. An earlier finding on that file is verified and re-published as usual. |
