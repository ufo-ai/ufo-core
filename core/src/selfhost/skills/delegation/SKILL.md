---
name: delegation
description: Handing a self-contained subtask to a subagent, and asking the user when you genuinely need input. Load when a task is large enough to split, or when a key detail is missing or an action is high-impact.
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
message, a purchase, a deletion). Ask, then end your turn — the answer arrives as the next message.
Do not ask for things you can find yourself or decide with a reasonable default.
