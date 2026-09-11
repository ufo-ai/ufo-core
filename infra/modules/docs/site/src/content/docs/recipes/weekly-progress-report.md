---
title: Prepare a weekly progress report
description: Combine plans, shipped work, and blockers into a checked report every Friday.
---

## Connect and sync the work

Connect GitHub, Linear, Slack, and the source that holds product plans. Share the delivery records
and plans that the whole workspace may use. Register them as synced sources.

Run one report before you schedule it. Check the reporting period, owners, links, and classification
of shipped, moving, blocked, and unowned work.

## Create the scheduled task

> Every Friday at 3:00 PM Pacific time, prepare the engineering progress report for the current
> week. Use production deployments as the source of truth for shipped work, GitHub for pull request
> state, Linear for plans and ownership, and the product-plan source for commitments. Include
> shipped outcomes, work still moving, blocked work with its blocker and owner, unowned work, and
> decisions needed next week. Link every item. Do not include activity with no customer or delivery
> effect. Compare with the previous report and call out material changes. Post in the engineering
> Slack channel. Do not post a result if nothing material changed. Do not change any source system.

## Inspect the loop

If a report is wrong, correct its source or classification rule before the next run. Do not correct
only the posted message.

Use **Scheduled** to change the time, destination, or pause state:

> Move the report to Monday at 9:00 AM Pacific time and cover the previous calendar week.
