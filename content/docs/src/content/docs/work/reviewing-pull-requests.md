---
title: Reviewing pull requests
description: Ask ufo for an evidence-based review and control where it is published.
---

Connect [GitHub](/connectors/github/) and give the pull request URL.

> Review this pull request for correctness, regressions, security problems, and missing tests. Run
> focused checks where useful. Publish findings on GitHub. Do not change the branch or merge it.

## Set the review scope

Name risks such as permissions, billing, concurrency, migrations, or user-facing behavior. Provide
the related issue or specification when the diff does not contain the full contract.

State where the review must go:

- **Conversation only** keeps findings in ufo.
- **Publish on GitHub** creates the requested GitHub review.
- **Fix findings** allows branch changes after the review.

The agent does not publish or change the branch unless the request allows it.

## Read the result

A finding must name the affected file and behavior. It must explain the failure case and give enough
evidence to reproduce it. Questions and style preferences are not correctness findings.

If the agent verifies no findings, it reports that result and its checks. It does not add a comment
only to show activity.

See the complete [pull request review recipe](/recipes/pull-request-review/).
