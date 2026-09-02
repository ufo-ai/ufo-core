---
name: competitive-intel
description: "Load when a member asks for recurring competitive analysis or intelligence: what competitors changed across pricing, launches, hiring, or press, or says \"Set up my first task: a daily competitive analysis.\" Not for a single page or price watch, a change to an existing task, or a one-off competitor question."
---
# Competitive intelligence, set up in chat

The turn ends on an applied `scheduled_task`. Nothing exists until the row does, so apply it in
this turn from what you propose; the member's corrections re-apply the same name.

## Read before asking

1. The opening line states the business and the member's role, and its business sentence is
   already the enrichment's summary of the company — read it and never ask what the business
   does. `memory_search` for the market and any competitor already named; a first turn in a new
   workspace finds nothing, and finding nothing is silent, not a sentence.
2. Propose competitors before asking for them: name three to five from one web search on the
   business the opening line names, and ask the member to confirm, drop, or add. With no search
   backend, propose from what you know and say the list is unsearched. "Who are your
   competitors?" with nothing proposed is the trap — the member came to be told. Match the set to
   the member's size and market; the famous names of a field are rarely who a two-person studio
   loses work to.

## Ask only what blocks the task

One message, one short numbered list: confirm the proposed competitors, and confirm the default
watch (pricing, product launches, hiring, press) as a yes/no — never a menu. Apply the task in the
same turn as that message.

A task reports into the conversation it was created from, so where it reports is not a question:
say in one sentence that a Slack-delivered copy means sending "set up the competitive analysis
here" from the Slack channel, and finish the chat-delivered one regardless.

Cadence is daily unless the member names another. The first-run opening line, "set it up," and
"go ahead" are confirmation; do not ask again.

## Record what lasts

Before applying, `memory_update` one item each for the confirmed competitor list and for what the
business is. Every fire is injected with recalled memory before it starts — best effort, which is
why the prompt below still names the competitors itself. Per-run findings are the reply, never a
memory item.

## Apply the task

Load `task-scheduling` and apply one manifest named `competitive-intel-daily` (`-weekly` for a
weekly ask). Fire at 8am in the member's zone, read from the `<context>` header's `time:` line and
converted to UTC; a `time:` line ending `UTC` is the no-zone fallback, not a member in London —
ask. The cadence is daily unless the member named another one. Daily is an informational cadence,
so bound it the way `task-scheduling` says: `expires_at` at the eleventh fire, and the tenth fire's
check-in offers to continue.

The manifest also carries `run_now: true`, so the first report runs on the spot rather than at
tomorrow's fire: the member reads real findings in this conversation instead of a promise. The
schedule keeps every fire after it.

```yaml
kind: scheduled_task
name: competitive-intel-daily
spec:
  schedule: "0 14 * * *"
  description: Daily competitive analysis for Harbor Freight Analytics
  prompt: |
    Daily competitive analysis for Harbor Freight Analytics, a load-board tool for freight
    brokers. Competitors: Flexport, project44, FourKites. Load research-assistant and find what
    changed since the previous run in their pricing, product launches, hiring, and press — bound
    every query to the recent past. Load research-report and write competitive-intel-<date>.md.
    Reply with a digest: a title, then one line per change; when nothing moved, say so in one
    line.
  expires_at: "<the eleventh fire, in UTC>"
  run_now: true
```

Every field is filled from this member's own answers. A competitor carried over from the example
is a fabricated competitor.

## Close

Four lines at most: what it watches, that the first report is running now and lands here in
minutes, when the daily fire happens after that, and that every report lands in this conversation.
Name the Radar app only where the workspace holds one a member can open.

## Traps

- Asking the member to pick a cadence, a time, or a format from a list — default, then confirm.
- Ending the turn on the question — the row exists before the reply does.
- Applying before `memory_update` — the second fire then knows less than the first.
- Naming a competitor no search returned and no member confirmed.
- Applying without `run_now: true` — the member then waits a day for the first report.
- `expires_at` on the weekly variant — `task-scheduling` bounds daily-or-faster tasks, not weekly.
