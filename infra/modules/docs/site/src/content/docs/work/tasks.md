---
title: Scheduled work
description: Create recurring tasks, choose where results go, and inspect or change each run.
---

Ask the agent to repeat work on a schedule. Include what to do, when to run, the time zone, the
source to inspect, and where useful results should go.

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

Each run has its own conversation. A run posts only when it has something useful to report. A quiet
run does not send a message.

The **Runs** view on the **Tasks** screen lists task and source-trigger runs, newest first. Open a
run to read its conversation. You can stop a running web task there.

**Radar** opens scheduled reports with their files and conversation.

## Change a task

Ask the agent to change, pause, resume, or delete a task. You can also use the **Scheduled** view on
the **Tasks** screen. The **Triggers** view holds source triggers.

> Pause the weekday pull request report.

> Move the revenue report to Monday at 8:00 AM Eastern time.

When the workspace has no credit, a scheduled run is refused and the task tries again at its next
scheduled time.
