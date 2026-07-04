---
name: task-scheduling
description: Load before scheduling durable recurring tasks and notifications.
---
# Task Scheduling

## Which tool to use

- **schedule_task** — schedule a durable **recurring** task: a 5-field cron `schedule` plus a `prompt`
- **list_scheduled_tasks** — list this workspace's recurring tasks with their name, schedule, and last run
- **cancel_scheduled_task** — cancel a recurring task by the scheduled task `name`

One-shot scheduling is not supported. For a "run once at time T" request, say that recurring tasks are
supported and single future runs are not.

## schedule_task — Durable recurring tasks

Use `schedule_task` for recurring tasks that should survive beyond the active workflow. Pass a 5-field
cron `schedule`, a `prompt`, and optionally `name`/`description`. Save the returned `name`; cancellation
requires it.

COMMUNICATION RULE: When talking to users, NEVER say "cron" or "cron job". Use friendly terms like "recurring task", "scheduled task", or "automatic check".

**When to use schedule_task:**

- Daily monitoring → "monitor competitor prices daily"
- Periodic reporting → "send weekly sales summaries every Monday"
- Regular checks → "check my inbox for investor replies every hour"

**Examples:**

Example: Daily Monitoring
User: "Keep an eye on competitor pricing and alert me whenever it changes"

- Use Python to convert user's preferred time to UTC
- Schedule a daily recurring task with a prompt describing what to collect, compare, and when to alert
- System triggers daily at the specified UTC time
- You collect data, compare, and alert user if changes

Example: Weekly Reports
User: "Send me a weekly summary of our sales metrics every Monday at 9am"

- User is in US/Pacific, so 9am Pacific = 17:00 UTC (standard time)
- Schedule a weekly recurring task (Mondays at 17:00 UTC) with a prompt describing what to compile and report
- System triggers every Monday at 17:00 UTC
- You compile and send the report

Example: Periodic Inbox Check
User: "Watch my inbox for investor replies and notify me immediately"

- Schedule an hourly recurring task with a prompt describing what to check and when to notify
- System triggers every hour
- You check inbox and notify if new replies

**KEY PRINCIPLES:**

- Confirm before creating a scheduled task or increasing/ambiguously changing run frequency; each run costs credits. After checking the current schedule, skip only updates that clearly keep or lower frequency. When unsure, confirm.
- Recurring tasks use a cron `schedule` and persist until deleted (one-shot `run_at` is not supported)
- When both day-of-month and day-of-week restrict a recurring task, both constraints must match. For example, `0 12 1-7 * 1` runs on the first Monday of each month.
- Do NOT delegate durable scheduled workflows to subagents - they don't have schedule_task
- The `schedule` must be a single 5-field cron expression. Comma-joined multi-expressions fail — use one schedule per disjoint cadence.
- **Never gate task execution on exact-minute wall-clock equality.** Scheduled runs have startup latency, so an exact-minute gate silently skips fires. Phrase any time-of-day gate as a tolerance window or as a comparison against the scheduled fire time from the task header.

## cancel_scheduled_task — Stop a recurring task

Use `cancel_scheduled_task` when the user asks to pause, stop, cancel, or delete a scheduled task. There
is no pause state. Pass the exact scheduled task `name` — `list_scheduled_tasks` returns the names.

- If the task name is ambiguous or missing, list the tasks or ask for the name.
- If `cancel_scheduled_task` returns `canceled: false`, tell the user no matching scheduled task was active.
- If a recurring task is blocked by expired auth or missing permissions, cancel it instead of letting it keep firing.

Do not recreate a missing scheduled task unless the user explicitly asks.

## Alerting from scheduled runs

A scheduled task re-invokes you in the same conversation, so your run's reply *is* the alert — it
posts to the conversation the task was scheduled in. Reply with the update only when the run
discovers genuinely new or noteworthy information.

**When to reply:**

- A scheduled-task run found new data the user cares about (new tweet, price alert hit, new search result)
- The information is actionable or time-sensitive

**When to stay silent:**

- Nothing new happened since the last check — end the run without posting a substantive message
- The update is trivial or redundant
- You're in the initial (non-scheduled) run — just respond normally

**Behavior:**

- The scheduled task remains active; the next trigger starts a fresh run
- Include enough detail in the reply that the user understands the update without opening the app

Example: "Check @potus's tweets every hour"

- Cron triggers → you check tweets → no new tweets → end the run silently
- Cron triggers → you check tweets → new tweet found → reply with the tweet details and a link
