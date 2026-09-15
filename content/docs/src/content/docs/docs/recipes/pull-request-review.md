---
title: Review every ready pull request
description: Review each changed, non-draft pull request through a GitHub synced source and source trigger.
---

## Connect and sync GitHub

In the Code application, connect the GitHub account that will publish reviews. Share the connection
with the workspace, then register its `pull_requests` stream as a synced source.

Run one review by URL before you add the trigger. Confirm that your UFO can fetch the head, read
repository instructions, run focused checks, and publish a review.

## Create the source trigger

Ask in the Code application:

> Watch the shared GitHub connection's `pull_requests` stream. Use one stable conversation per pull
> request. When a pull request changes, review it only when it is open, not a draft, and the current
> head has not already been reviewed. Read the repository instructions and related code. Find
> defects that affect behavior, security, data, or operations. Verify each finding. Publish the
> review on GitHub. Do not change the branch or merge it. Do not post a result for drafts, closed
> pull requests, or heads already reviewed.

## Keep CI separate

A pull request record does not change when only a check run finishes. If your UFO must also watch
checks, create a scheduled task in the same application:

> Every 20 minutes, inspect pull requests this application reviewed that are still open. Report or
> fix only failures caused by their current head. Stop tracking a pull request when it merges or
> closes. Do not merge.

Open **Tasks**, then **Triggers** to manage the review trigger. Use **Scheduled** to manage the CI
task. Use **Runs** to inspect their conversations.
