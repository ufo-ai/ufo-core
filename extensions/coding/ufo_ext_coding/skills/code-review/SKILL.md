---
name: code-review
description: Load before reviewing any diff, pull request/PR, or proposed change.
---
# Code Review

Review a pull request's changes and report findings for the parent to act on. **Report findings — do not post them to GitHub yourself.**

## Get the PR

The objective names `{owner}/{repo}` and the PR number. Reach GitHub through the **connector** — call the `GITHUB_*` actions via `call_external_tool` with `source_id: "github"` unless the objective supplied a connected-account source id. It is authenticated and it is the review path — read the diff through it rather than cloning, so a review costs one call instead of a checkout. (`gh` is authenticated wherever the workspace holds a GitHub connector grant, and `git` wherever it holds a git credential; neither is needed here.) Call `describe_external_tools` once for the GitHub actions you use.

1. **Diff in one call** — `GITHUB_LIST_PULL_REQUESTS_FILES` (args `owner`, `repo`, `pull_number`) returns every changed file with its `patch`. This is your primary review input.
2. **Full-file context, sparingly** — only when a patch lacks the surrounding code a specific finding needs, fetch that one file with `GITHUB_GET_REPOSITORY_CONTENT` (args `owner`, `repo`, `path`, `ref` = the PR head SHA; content is base64). A couple at most, not every file.

## Review

Go change-by-change. Look for, in priority order:

1. **Correctness** — logic errors, off-by-one, wrong conditionals, unhandled cases, contracts broken with callers.
2. **Security** — injection, auth/authz gaps, leaked secrets, unsafe deserialization, SSRF, the OWASP top 10.
3. **Data & resource safety** — unbounded inputs, missing transactions, races, leaks.
4. **Behavior/API changes** — backward-incompatible changes, missing migrations.
5. **Tests** — does the change test the behavior it adds or fixes?

Judge each change from its patch and the context lines around it. Verify a claimed bug is reachable before reporting it — prefer fewer, high-confidence findings over a long speculative list.

## Report

Return a structured result the parent can post verbatim. For each finding:

- **Severity** — `[blocking]` (must fix before merge), `[important]` (should fix), or `[nit]` (optional).
- **Location** — `path:line` at the PR head, so the parent can map it onto a changed diff line.
- **Title** — one line.
- **Description** — what's wrong and why it matters.
- **Suggested fix** — concrete.

End with your **recommendation**: `approve` (no blocking findings), `request changes` (≥1 blocking finding), or `needs discussion`. A clean diff with no findings is a valid result — say so and recommend `approve`.
