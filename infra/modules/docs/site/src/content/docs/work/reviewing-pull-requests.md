---
title: Reviewing pull requests
description: Ask ufo for an evidence-based review and control where it is published.
---

Connect [GitHub](/connectors/github/) and give the pull request URL.

> Review this pull request for correctness, regressions, security problems, and missing tests. Run
> focused checks where useful. Publish findings on GitHub. Do not change the branch or merge it.

## Set the review scope

Name special risks such as permissions, billing, concurrency, migrations, or user-facing behavior.
Provide the related issue or specification when the diff does not contain the full contract.

State where the review must go:

- **Conversation only** keeps findings in ufo.
- **Publish on GitHub** creates the requested GitHub review.
- **Fix findings** allows branch changes after the review.

The agent does not publish or change the branch unless the request allows it.

## Read the result

A useful finding names the affected file and behavior, explains the failure case, and gives enough
evidence to reproduce it. Questions and style preferences are separate from correctness findings.

If no finding survives verification, the agent reports that result and the checks it performed.
An empty review must not invent a comment only to show activity.

See the complete [pull request review recipe](/recipes/pull-request-review/).
