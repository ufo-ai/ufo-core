---
title: Review every ready pull request
description: Review each changed, non-draft pull request through a GitHub synced source and source trigger.
---

This loop gives each pull request its own review conversation and wakes it when the pull request
record changes.

## Connect and sync GitHub

In the Code application, connect the GitHub account that will publish reviews. Share the connection
with the workspace, then register its `pull_requests` stream as a synced source.

Run one review by URL before you add the trigger. Confirm that the agent can fetch the head, read
repository instructions, run focused checks, and publish a review.

## Create the source trigger

Ask in the Code application:

> Watch the shared GitHub connection's `pull_requests` stream. Use one stable conversation per pull
> request. When a pull request changes, review it only when it is open, not a draft, and the current
> head has not already been reviewed. Read the repository instructions and related code. Find
> defects that affect behavior, security, data, or operations. Verify each finding. Publish the
> review on GitHub. Do not change the branch or merge it. Stay quiet for drafts, closed pull
> requests, and heads already reviewed.

The trigger watches the `pull_requests` stream. The agent applies the ready-state and head checks
inside the stable pull request conversation.

## Keep CI separate

A pull request record does not change when only a check run finishes. If the agent must also watch
checks, create a scheduled task in the same application:

> Every 20 minutes, inspect pull requests this application reviewed that are still open. Report or
> fix only failures caused by their current head. Stop tracking a pull request when it merges or
> closes. Do not merge.

Open **Tasks**, then **Triggers** for the review trigger. Use **Scheduled** for the CI task and
**Runs** for their conversations.
