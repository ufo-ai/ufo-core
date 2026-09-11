---
title: Scheduled work
description: Create recurring tasks, choose where results go, and inspect or change each run.
---

Ask the agent to repeat work on a schedule. State the action, schedule, time zone, source, and result
destination.

> Every weekday at 9:00 AM Pacific time, check open pull requests in acme/web. Report failed checks,
> conflicts, and reviews that need an owner. Post the report in the engineering Slack channel.

## What a task needs

- A repeated action.
- A cadence and time zone.
- The accounts or sources it can use.
- A destination for results, when the result must be delivered outside the web portal.
- An expiry, when the task must stop after a date.

Every task repeats. One-time reminders are not available. Use an expiry for a short-lived schedule.

## Runs

Each run has its own conversation. It sends no message when it finds nothing to report.

The **Runs** view on the **Tasks** screen lists task and source-trigger runs, newest first. Open a
run to read its conversation. You can stop a running web task there.

**Radar** shows scheduled reports, files, and conversations.

## Change a task

Ask the agent to change, pause, resume, or delete a task. You can also use the **Scheduled** view on
the **Tasks** screen. The **Triggers** view holds source triggers.

> Pause the weekday pull request report.

> Move the revenue report to Monday at 8:00 AM Eastern time.

When the workspace has no credit, a scheduled run is refused and the task tries again at its next
scheduled time.
