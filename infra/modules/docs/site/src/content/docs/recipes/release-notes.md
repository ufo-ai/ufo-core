---
title: Draft weekly release notes
description: Turn verified production deployments into a factual changelog draft every Thursday.
---

## Connect the release record

Connect GitHub, the production deployment system, and the issue tracker. Add product plans or
customer notes as synced sources only when they explain the member problem behind a shipped change.

Run the report once. Confirm that the deployment record, not merge time, decides what shipped.

## Create the scheduled task

> Every Thursday at 2:00 PM Pacific time, draft release notes for production changes since the
> previous successful run. Start from deployment records, then trace each deployed head to its
> merged pull request and issue. Include only member-visible behavior. For each item, state what
> changed, who it affects, and what the member can do now. Link the deployment and pull request.
> Exclude internal refactors, tests, operational repairs with no member effect, and merged work that
> is not deployed. Return the draft in this conversation. If no member-visible change shipped, stay
> quiet. Do not edit or publish the changelog.

Each run records its own reporting boundary. The next run starts after the last successful result,
so a failed run does not silently skip a release.

## Publish after review

Review the draft, then authorize the write separately:

> Publish the approved items to the changelog. Keep their wording, order, dates, and links. Build
> the docs and check every internal link. Open a pull request. Do not merge it.

Use **Tasks**, then **Scheduled** to pause or change the cadence. Use **Runs** to inspect the source
chain for an earlier draft.
