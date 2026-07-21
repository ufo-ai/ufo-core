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

Validate every returned finding against the exact diff and surrounding code. Keep only issues that
must be fixed before merge. Run a focused test only when it is necessary to prove or disprove a
finding.

## Publish the verdict

Re-read `headRefOid` immediately before writing. If it changed, do not comment on or review the new
head from stale evidence.

For each validated issue, create one inline comment with
`mcp__github_inline_comment__create_inline_comment`. Then submit exactly one decisive review:

- Findings: `gh pr review <number> --request-changes --body "<one-line reason>"`
- No findings: `gh pr review <number> --approve`

Finish only after the decisive review command succeeds.

## Gotchas

| Failure | Response |
| --- | --- |
| A pipe, redirect, or compound Bash command is rejected | Save command output under `$RUNNER_TEMP`, then inspect the file in a separate call. Do not retry an equivalent shell shape. |
| `gh pr view --comments` fails through an unnecessary GraphQL field | Request only the explicit JSON fields listed above. |
| Diff or log output is too large | Read the saved file in bounded chunks; do not rerun the producing command. |
| Git history or an object appears missing | Check the full-history checkout before fetching. Never clone the repository. |
| The head changes during review | Discard stale findings and stop without publishing them. |
| The action is green but no verdict exists | The review is incomplete until `gh pr review` succeeds. |
