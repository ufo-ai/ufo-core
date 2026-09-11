---
title: Audit documentation after shipped changes
description: Use GitHub source changes and deployment evidence to find product documentation drift.
---

## Connect the sources

Connect GitHub and the production deployment system. Share the GitHub connection and register its
`pull_requests` stream as a source. Register the product documentation repository as a shared
knowledge source.

Run the audit against one known release. Confirm that it excludes internal refactors, unmerged work,
and changes that are not deployed.

## Create the source trigger

Ask in the documentation audit conversation:

> Watch the shared GitHub connection's `pull_requests` stream in this conversation. When a pull
> request changes, continue only if it merged and its head is present in a production deployment.
> Compare its member-visible behavior with the product documentation source. Report each false,
> incomplete, or missing claim. Link the pull request, deployment, affected page, and source path.
> State the current behavior and the smallest documentation correction. Do not post a result for
> internal changes, unshipped work, or changes with no documentation effect. Do not edit or publish
> docs.

Deliver each run to this conversation. Inspect each run under **Tasks**, then **Runs**.

## Draft corrections separately

Review the findings, then ask:

> Update the confirmed pages. Keep the current structure and voice. Build the site, check internal
> links, and open a draft pull request. Do not publish or merge it.
