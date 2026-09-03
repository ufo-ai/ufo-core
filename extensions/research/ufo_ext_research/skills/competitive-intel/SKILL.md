---
name: competitive-intel
description: "Load when a member asks for recurring competitive analysis or intelligence: what competitors changed across pricing, launches, hiring, or press, or says \"Set up my first task: a daily competitive analysis.\" Not for a single page or price watch, a change to an existing task, or a one-off competitor question."
---
# Competitive intelligence, set up in chat

The turn writes the first brief, then asks once whether it should repeat. The brief is the product
and it lands in this turn; a recurring row exists only after the member says they want one, and the
member's corrections re-apply the same name.

## Read before asking

1. The opening line states the business, and its business sentence is already the enrichment's
   summary of the company — read it and never ask what the business does. `memory_search` for the
   market and any competitor already named; a first turn in a new workspace finds nothing, and
   finding nothing is silent, not a sentence.
2. Propose competitors before asking for them: name three to five from one web search on the
   business the opening line names, and ask the member to confirm, drop, or add. With no search
   backend, propose from what you know and say the list is unsearched. "Who are your
   competitors?" with nothing proposed is the trap — the member came to be told. Match the set to
   the member's size and market; the famous names of a field are rarely who a two-person studio
   loses work to.

## Ask only what blocks the brief

One message, one short numbered list: confirm the proposed competitors, and confirm the default
watch (pricing, product launches, hiring, press) as a yes/no — never a menu. Cadence is not asked
here. The first-run opening line, "set it up," and "go ahead" are confirmation of the brief; do not
ask again.

## Write the first brief

Load `research-assistant` and find what each confirmed competitor changed in pricing, product
launches, hiring, and press, bounding every query to the recent past. Load `research-report` and
write `competitive-intel-<date>.md`. Reply with the digest itself — a title, then one line per
change, with a source link and a date on each — never with a promise of one. Nothing is scheduled
yet, so this brief is the whole answer to the ask.

## Ask once whether it repeats

After the brief, and only after it, ask once with `ask_user`: keep this as a one-time brief, or run
it every day. Two options, named as the member reads them — `Just this one` and `Every day` — with
daily recommended in the title. Ask this once: an answer already given ("make it daily", "daily is
fine") is the answer, and asking again spends the member's turn on what they said.

Read the Slack connection just in time, in this same turn, before you write the question:
`object_get` `surface/slack` and read `bound` on its status. Do not carry an earlier turn's answer,
and do not read the offer off what the workspace looked like when the run started — an install
landing during onboarding is exactly the case this catches.

- `bound: true` — the same `ask_user` carries a second question: deliver the daily brief in Slack
  as well, or here only. A report lands in the conversation its task was created from, so a member
  who wants it in Slack sends `set up the competitive analysis here` in the Slack channel they want
  it in; say that one line, and apply the chat-delivered task regardless.
- `bound: false` or no such object — ask nothing about Slack and offer nothing. A member who has
  not connected Slack cannot act on the offer, and naming it states a connection this workspace
  does not hold.

A surface that renders options as unclickable text — Slack itself — takes no structured options:
ask the same thing plainly in prose there, and list the choices in exactly one place.

## Record what lasts

Before applying, `memory_update` one item each for the confirmed competitor list and for what the
business is. Every fire is injected with recalled memory before it starts — best effort, which is
why the prompt below still names the competitors itself. Per-run findings are the reply, never a
memory item.

## Apply the task, once they asked for a recurring one

A one-time answer ends here: say the brief is written, name where the file is, and offer nothing
else. Apply nothing — a row nobody asked for is a fire nobody asked for.

For a recurring answer, load `task-scheduling` and apply one manifest named
`competitive-intel-daily` (`-weekly` for a weekly ask). Fire at 8am in the member's zone, read from
the `<context>` header's `time:` line and converted to UTC; a `time:` line ending `UTC` is the
no-zone fallback, not a member in London — ask. The cadence is daily unless the member named
another one. Daily is an informational cadence, so bound it the way `task-scheduling` says:
`expires_at` at the eleventh fire, and the tenth fire's check-in offers to continue.

The manifest carries no `run_now`: the first brief was written in this turn, so an immediate fire
would research the same day twice and report it twice. The first scheduled fire is tomorrow's.

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
```

Every field is filled from this member's own answers. A competitor carried over from the example
is a fabricated competitor.

## Close

Four lines at most: what it watches, that the first brief is the one above, when the daily fire
happens from now on, and that every report lands in this conversation. A one-time brief closes on
the first two and the offer to make it daily whenever they want it. Name the Radar app only where
the workspace holds one a member can open.

## Traps

- Asking the member to pick a time or a format from a list — default, then confirm.
- Asking whether it repeats before the brief is written — the question is worth more once they
  have read what a brief is.
- Asking whether it repeats twice, or after the member already named a cadence.
- Applying a schedule on a one-time answer.
- Offering Slack delivery without reading `surface/slack` in this turn, or reading it and offering
  it anyway when nothing is bound.
- Applying before `memory_update` — the second fire then knows less than the first.
- Naming a competitor no search returned and no member confirmed.
- Carrying `run_now: true` — the brief already ran in this turn, so it would run twice.
- `expires_at` on the weekly variant — `task-scheduling` bounds daily-or-faster tasks, not weekly.
