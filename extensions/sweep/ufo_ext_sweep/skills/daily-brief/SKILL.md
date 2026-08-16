---
name: daily-brief
description: Load when the member asks to prepare, review, or approve items from a private daily brief. Do not load for general reports or recurring task schedules.
---
# Daily brief

## Scheduled edition

Call `sweep_newspaper` exactly once. Treat its result as the complete bounded input for this
edition. Do not use other private sources.

Rank items by member impact, urgency, and how much the new information changes the next action.
Prefer a specific unfinished commitment over general activity. Prefer a changed decision or stale
assumption over a routine update. Keep at most eight items and 6,000 characters.

Use only these headings, in this order, and omit empty sections:

- Work to finish
- Things you may have missed
- From your pages and files
- Outside context
- Drafts
- Coverage

Keep supplied internal references and public URLs with the item they support. Do not add an
unsupported fact or hide missing scout coverage.

Draft at most three tasks and three memories. A draft is a proposal, not approval. Never call
`update_todo_list` or `memory_update` during a scheduled edition.

## Scout roles

Return no more than five findings. Each finding needs a short title, why it matters, an information
date, one stable subject key, and no more than three supplied references.

- `work`: Select open objectives, task memories, commitments, unanswered requests, and overdue
  work that the member can act on.
- `missed-items`: Select decisions, unanswered questions, stale assumptions, and material changes.
  Do not repeat routine open work.
- `pages-artifacts`: Select important changed pages, files, reports, and produced results. Use only
  supplied references.
- `public-context`: Select outside context only from the supplied public records. Never infer,
  quote, or disclose private facts.

Use a record's stable subject key when the finding is about that record. When several records form
one finding, use a short key that will remain the same when only wording changes.

## Member approval

Apply only drafts the member explicitly approves in a later turn. Use `update_todo_list` for an
approved task and `memory_update` for an approved memory. If the approval does not identify a draft
unambiguously, ask which draft. Do not treat discussion, editing, or the scheduled turn as approval.
