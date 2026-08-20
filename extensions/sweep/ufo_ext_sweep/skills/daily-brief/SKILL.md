---
name: daily-brief
description: Load when the member asks to create, prepare, review, or approve a private Daily Brief application. Do not load for other reports or schedules.
---
# Daily brief

## Application

A Daily Brief is an ordinary member-created application. When creating it, load
`create-application` first. Put these duties in its prompt:

- Load `daily-brief` for setup, each scheduled edition, and later approval.
- Call `configure_daily_brief` in the application's private setup conversation before creating its
  recurring task.
- Call `sweep_newspaper` exactly once in each scheduled edition.
- Publish each useful edition as a Markdown file with `write` and `share_file`, and update the
  homepage.
- Keep task and memory suggestions as drafts until the member approves them in a later turn.

Keep `configure_daily_brief`, `sweep_newspaper`, `write`, `share_file`, `deploy_website`, and
`set_homepage` verbatim in the application prompt; do not replace a tool name with prose.

Create no schedule from the main agent's application-creation turn. Send the member to the new
application. In the creation reply, say setup creates one recurring task that reports each edition
into that application's same private conversation. Say it is the member's private application;
Sweep supplies the skill and scouts but does not own or provision it.

When the member asks the application to schedule the brief, load `task-scheduling`. In the current
private conversation, call `configure_daily_brief`, build the homepage, run `deploy_website`, and
bind it with `set_homepage`. Then create one recurring task in this same conversation. Its prompt
must name `daily-brief`, `sweep_newspaper`, the shared Markdown edition, and the homepage update.
The task keeps reporting into this conversation; do not open a conversation per edition.

## Editions

A scheduled edition collects first: call `sweep_newspaper` exactly once and treat its result as
the complete bounded input. When the member supplies the collected material, or says not to
collect, that material is the complete bounded input: format it and call no tools — no
`sweep_newspaper`, no `update_todo_list`, no `memory_update`. Either way, use no other private
sources.

Rank items by member impact, urgency, and how much the new information changes the next action.
Prefer a specific unfinished commitment over general activity. Prefer a changed decision or stale
assumption over a routine update. Keep at most eight items and 6,000 characters.

Use only these headings, each rendered as a markdown `##` line (`## Work to finish`), in this
order:

- Work to finish
- Things you may have missed
- From your pages and files
- Outside context
- Drafts
- Coverage

Write a heading only when it has at least one item. Drop an empty section entirely — no heading,
no placeholder, no "none".

Keep supplied internal references and public URLs with the item they support. Do not add an
unsupported fact or hide missing scout coverage.

Write the edition to a Markdown file and call `share_file`. The shared file is the published result
that Radar shows. If there are no useful findings, share nothing.

Update the homepage files in this conversation with the new edition, then run `deploy_website` with
the existing site name. Never call `set_homepage` from a scheduled turn. The member-facing setup
turn already bound the site.

Draft at most three tasks and three memories. A draft is a proposal, not approval. Preparing an
edition never calls `update_todo_list` or `memory_update`; only member approval in a later turn
does.

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
