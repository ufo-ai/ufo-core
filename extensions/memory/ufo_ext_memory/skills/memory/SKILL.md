---
name: memory
description: Load when a member asks to recall prior context or preserve a durable fact across conversations.
metadata:
  tools:
  - memory_search
  - memory_update
---
# Memory

## Recall

Search before answering a request that depends on a past preference, decision, name, or synced
source. Use focused queries; do not guess at context the team may already have provided.

## Write

Record stable preferences, commitments, corrections, decisions and their reasons. Capture
“moved to Postgres for row-level isolation,” not merely “moved to Postgres.”

The write inherits the conversation audience. A private conversation writes member-private memory;
a shared conversation writes shared memory. Never claim or attempt to widen a private write for the
team.

## Exclude

Memory is durable cross-task context, not a task scratchpad.

- Keep progress, checklists, intermediate results, and “already covered” ledgers in workspace files
  or todos.
- Per-run or scheduled-run output — today's digest, the items flagged this run, a snapshot summary
  — belongs in the delivered post or artifact.
- Never re-emit a growing cumulative note. Update one canonical memory only when a standing fact
  genuinely changes.
