---
title: Writing code
description: Give ufo a code change and receive a tested pull request.
---

Connect [GitHub](/connectors/github/) and name the repository. Include the problem, the required
behavior, and the proof that must pass.

> In `acme/web`, make the invoice table sortable by due date. Preserve the current mobile layout.
> Add the test that proves the sort order, run the focused checks, and open a pull request. Do not
> merge it.

## Define the boundary

State:

- The repository and base branch.
- The behavior that must change.
- Constraints, affected environments, and files that must not change.
- The tests or manual checks required.
- Whether the agent may commit, push, open a pull request, or merge.

The agent reads repository instructions before it changes code. If the repository contains
`AGENTS.md`, those instructions apply to files in its scope.

## Follow the work

The agent reports important decisions and blockers in the conversation. A final result should name
the changed behavior, tests, and pull request. A local passing test does not prove that CI passed or
that a pull request merged.

Ask the agent to keep watching when the required outcome includes CI or review:

> Watch this pull request until all required checks and reviews finish. Fix failures caused by this
> branch. Do not merge it.

## Review the result

Check the product behavior, not only the diff. For a visual change, ask for browser proof at the
important widths. For a data change, ask for realistic input through durable state to a result a
member can use.

See the complete [code change recipe](/recipes/code-change/).
