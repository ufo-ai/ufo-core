---
name: delegation
description: Load before spawning subagents or asking the member a blocking question. E.g. fan an investigation out in parallel; batch many similar operations; confirm before an irreversible action.
metadata:
  tools:
  - spawn_subagent
  - ask_user
  - load_skill
---
# Delegating and asking

## Delegate a subtask

`spawn_subagent` runs a named profile as a child working in the same `/workspace`, so it can pick
up files you leave and leave files you read back. Delegate when a task has a self-contained chunk —
a focused investigation, a batch of similar work — that a fresh context handles better than
crowding your own.

- Give the subagent a complete brief: it cannot ask you questions and will make reasonable
  assumptions from what you hand it.
- Foreground (the default) waits and returns the profile's structured result. Background returns a
  turn id at once — use it to fan out several subtasks, then collect their workspace files.
- A subagent does not delegate further; keep the tree one level deep.

## Ask the user — sparingly

`ask_user` is for the cases where proceeding would be guesswork: a missing detail that changes the
approach, or a confirmation before an irreversible, expensive, or high-impact action (sending a
message, a purchase, a deletion). Keep the question self-explanatory and any option list short. Do
not ask for things you can find yourself or decide with a reasonable default. Write only a brief
lead-in, then end your turn — the answer arrives as the next message.

Put the choices in exactly one place, never two. When you pass `options` they ride the structured
question, so do not also restate them as a prose list in your reply. And know your surface: on one
that renders `options` as clickable controls, the structured question stands on its own; on one that
does not — Slack shows them as collapsed, unclickable plain text — skip `options` and instead ask
plainly in prose, listing the choices in your reply for the user to answer in-thread. Either way the
choices appear once and the user can act on them.
