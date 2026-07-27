---
name: sync
description: The recurring review-and-route run — turn what accumulated (transcripts, channels, the state repo) into a proposed fan-out of observations, agenda items, todos, and watches, written only after approval. Load when a scheduled sync fires or the member asks to sync.
metadata:
  depends:
    - triage
---
# Sync — review what accumulated, route it, write on approval

Runs on its schedule in the inbox conversation (the member's private channel with just them and
the bot). The pipeline has already done the heavy lift off-turn — sources synced, pages distilled
into memory, watches classified. This run's job is judgment and routing.

## 1 — Establish the window

`memory_search` for the latest `sync run —` record. Everything since it is fair game; without one,
review what recall surfaces as recent and say the run is seeding.

## 2 — Gather

- The roster: `memory_search` the org chart and people files (the state repo syncs them).
- Per person who matters now: `memory_search` their name and where the org chart places them
  (triage rule 2). Note fresh facts, commitments, concerns.
- The inbox: what the member dropped in this channel since the last run is in the conversation.
- Standing loops: `list_page_watches`.

## 3 — Form insights

For each fresh signal, one insight: what happened, who it touches, which source said it, its
weight per `triage`, and destinations from this closed set — nothing else is a destination:

| Destination | Written as |
|---|---|
| Observation | `memory_update` — `observation — <person>: <what> (<source>)`, memory_kind `event` |
| 1:1 agenda | `memory_update` — `1:1 <person> — raise: <what>`, memory_kind `task` |
| Decision record | `memory_update` — `decision — <what>, because <why>`, memory_kind `decision` |
| Kudos | `memory_update` — `kudos — <person>: <what>`, memory_kind `event` |
| Todo | `update_todo_list` / `update_todo_status` on this conversation's board |
| Follow-through | `watch_pages` — a topic the page proving it closed will match — plus a todo naming the loop |

Memory stays private to the member (`shared` false) — this is one person's operating manual.

## 4 — Propose, then write

Present the whole fan-out in one message, ordered by `triage`, each line
`<insight> → <destination(s)>`. Then `ask_user`: approve all, adjust, or drop. Nothing
materializes before the answer; on adjust, apply the corrections and confirm once more. After
writing, record `memory_update` — `sync run — <date>: routed <n> insights`, memory_kind `event` —
the next run's window marker.

## 5 — Amend the manual

A correction in step 4 is a rule. Keep the workspace skill `sync-rules` current: author or update
its folder in the sandbox and apply the `skill` object (see `create-skill`); start every run with
`load_skill sync-rules` when it exists. Prompt-level improvements arrive separately as governed self-improvement
proposals the member approves in chat.

## Guardrails

- Propose-and-confirm every write. Nothing outward-facing ever sends — this pack grants no send
  tools; a draft stays a draft.
- Nothing accumulated → end the run silently (the scheduled-run convention).
