---
name: task-scheduling
description: Load when a member asks to create, change, pause, resume, or cancel a recurring task, notification, reminder, watch, or periodic check, or asks for a one-time reminder.
---
# Task Scheduling

Recurring tasks are workspace objects of kind `scheduled_task`, managed with the generic object
tools:

- **object_apply** — create or update a recurring task from a YAML manifest (below); `paused: true`
  stops the fires and `paused: false` starts them again
- **object_list** with `kind: scheduled_task` — list tasks with their name and schedule
- **object_get** — one task's spec plus its live status (`next_run_at`, `last_run_at`)
- **object_delete** — cancel a task by name; the result echoes the deleted spec

One-shot scheduling is not supported. For a "run once at time T" ask — a single reminder, a
one-time job — create nothing and promise nothing: say one-off reminders are not supported and
only recurring tasks can be scheduled. Never emulate run-once with an `expires_at`-bounded
recurring task on your own; you may name that workaround as an option, and apply it only after the
member explicitly asks for it.

## Creating a recurring task

Apply one YAML manifest. The `name` is the task's durable address (lowercase, digits, hyphens);
`schedule` is a single 5-field cron expression in UTC; `prompt` is what the platform sends you on
each fire. Every run searches memory for relevant context before the task starts.

```yaml
kind: scheduled_task
name: competitor-price-digest
spec:
  schedule: "0 14 * * *"
  prompt: Report noteworthy competitor price changes.
  description: Daily competitor price digest
  expires_at: "2026-08-11T14:00:00Z"
```

Re-applying an existing name updates it in place and re-points reporting to the conversation you
applied it from. `object_explain` with `kind: scheduled_task` shows the spec schema.

### Time zones

`schedule` runs in UTC; members speak on their own clock. The `time:` line of the message's
`<context>` header shows the current moment in the member's zone — a bare clock time ("10am")
means that zone unless the member names another. Convert from the member's zone to UTC; never
assume a zone the conversation does not establish. Moving an existing task to a new clock time
with no zone named keeps its schedule's frame: `0 9 * * 1-5` moved "to 10am" becomes
`0 10 * * 1-5`. Ask only when no zone is discoverable at all.

### Bound frequent informational tasks

For a recurring task that fires daily or more often and can safely stop:

1. Honor the user's duration or run count; otherwise enumerate ten permitted occurrences starting
   with the first future fire.
2. Set `expires_at` to occurrence 11, not occurrence 10. Occurrence 10 completes its task and adds
   a check-in offering `Continue same cadence`, `Change cadence`, and `Stop`.
3. Re-apply the same name with a new bound only after explicit confirmation.

Keep `prompt` limited to the recurring work. Never embed an expiry date, occurrence count, or
continuation instruction; runtime adds the final-fire check-in.

Leave `expires_at` unset only when stopping could itself harm an external-service operation:
credential refresh, synchronization, keep-alive work, or monitoring where a gap loses required
coverage. Reading an external service for an informational digest is not an exception.

COMMUNICATION RULE: When talking to users, NEVER say "cron" or "cron job". Use friendly terms like
"recurring task", "scheduled task", or "automatic check".

**When to schedule:**

- Daily monitoring → "monitor competitor prices daily"
- Periodic reporting → "send weekly sales summaries every Monday"
- Regular checks → "check my inbox for investor replies every hour"

**Examples:**

Example: Daily Monitoring
User: "Keep an eye on competitor pricing and alert me whenever it changes"

- Use Python to convert user's preferred time to UTC
- Apply a daily `scheduled_task` whose prompt describes what to collect, compare, and when to alert
- System triggers daily at the specified UTC time
- You collect data, compare, and alert user if changes

Example: Weekly Reports
User: "Send me a weekly summary of our sales metrics every Monday at 9am"

- The member has said they are in US/Pacific, so 9am Pacific = 17:00 UTC (standard time)
- Apply a weekly `scheduled_task` (`0 17 * * 1`) whose prompt describes what to compile and report
- System triggers every Monday at 17:00 UTC
- You compile and send the report

Example: Periodic Inbox Check
User: "Watch my inbox for investor replies and notify me immediately"

- Apply an hourly `scheduled_task` whose prompt describes what to check and when to notify
- System triggers every hour
- You check inbox and notify if new replies

**KEY PRINCIPLES:**

- Confirm before creating a scheduled task or increasing/ambiguously changing run frequency; each
  run costs credits. After checking the current schedule (`object_get`), skip only updates that
  clearly keep or lower frequency. Treat an explicit "set it up" or "go ahead" as confirmation.
  When unsure, confirm.
- Recurring tasks use a cron `schedule` and persist until deleted or their optional UTC
  `expires_at`; the platform cancels an expired task before another run. A paused task persists
  too and fires nothing. One-shot `run_at` is not supported.
- When both day-of-month and day-of-week restrict a recurring task, both constraints must match.
  For example, `0 12 1-7 * 1` runs on the first Monday of each month.
- Do NOT delegate durable scheduled workflows to subagents — they don't hold the object tools
- The `schedule` must be a single 5-field cron expression. Comma-joined multi-expressions fail —
  use one task per disjoint cadence.
- **Never gate task execution on exact-minute wall-clock equality.** Scheduled runs have startup
  latency, so an exact-minute gate silently skips fires. Phrase any time-of-day gate as a
  tolerance window or compare against the `<scheduled_task>` header's `scheduled_fire`.

## Pausing a recurring task

Apply `paused: true` with `kind: scheduled_task` when the user asks to pause, hold, or stop a task
for now. The task keeps its name, schedule, prompt, and run history and fires nothing.
Apply `paused: false` to start it again from the next fire.

- Pass the exact task `name` — `object_list` returns the names and each task's `paused` state.
- Never delete a task the user asked you to pause. A delete cannot be undone.

## Cancelling a recurring task

Use `object_delete` with `kind: scheduled_task` when the user asks to cancel or delete a scheduled
task for good. Pass the exact task `name` — `object_list` returns the names.

- If the task name is ambiguous or missing, list the tasks or ask for the name.
- If the delete reports no such object, tell the user no matching scheduled task was active.
- If a recurring task is blocked by expired auth or missing permissions, pause it instead of
  letting it keep firing.

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

- Timer triggers → you check tweets → no new tweets → end the run silently
- Timer triggers → you check tweets → new tweet found → reply with the tweet details and a link

## Memory hygiene for scheduled runs

A scheduled run's per-run output is NOT durable memory. The reply you post to the conversation is
already the durable record of what this run found, so do not also save that run's digest, flagged
items, or snapshot summary as a `fact` — that piles up single-execution snapshots that crowd out
relevant context on unrelated later turns.

- Keep in-task working state (progress, an "already covered" ledger, intermediate results) in
  workspace files or todo items, not memory.
- Only durable, cross-task facts belong in memory: a genuine config change the run made (universe
  edits, an approve/reject decision, a posting change). Write those as a single canonical item and
  update it in place — never re-emit a cumulative note as a fresh near-duplicate each run.
- If a run must record a per-run snapshot at all, prefer `event` kind (short half-life) over `fact`,
  so it decays instead of accumulating.
