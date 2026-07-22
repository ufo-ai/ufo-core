---
name: memory
description: Recalling what the team already knows and recording durable facts across conversations. Load when a task depends on prior context, member preferences, or anything worth remembering for next time.
metadata:
  tools:
  - memory_search
  - memory_update
---
# Remembering and recalling

Memory spans conversations, so a fact learned once is available to every later turn. It has two
subjects: the speaking member (personal) and `shared` (the whole team). Recall reads both; a write
lands on one.

## Recall before you answer

When a request leans on earlier context — a preference, a past decision, a name, a synced document
— run `memory_search` first with a focused query. It returns matching facts and snippets from
synced sources. Do not guess at something the team may have already told you.

## Record what will matter later

After learning something durable — a stable preference, a commitment, a correction, a decision and
its reason — write it with `memory_update`. Capture the reasoning, not just the outcome: "moved to
Postgres for row-level isolation" is worth keeping; "moved to Postgres" is not.

- Personal to the speaker by default; set it shared when it is true for the whole team.
- Record durable facts, never transient chatter or anything you can recompute on demand.

## Not memory: task-execution state and per-run output

Memory is for durable, useful cross-task context — identity, preferences, decisions and their
reasons, key people and projects. It is not a scratchpad for the task you are running right now.

- Working state for the current task (progress, a checklist of what is done, intermediate results,
  a ledger of "already covered" items) belongs in workspace files or todo items, not memory. Reach
  for those to track in-task state; they stay with the task instead of bloating cross-task recall.
- Per-run or scheduled-run output — today's digest, the items flagged this run, a snapshot summary
  — is not a durable fact. It belongs in the delivered post or artifact, which is already the
  durable record. Saving it as memory piles up single-execution snapshots that crowd out relevant
  context on unrelated later turns.
- Do not re-emit a cumulative note as a fresh memory item each time it grows. If some standing fact
  genuinely changed (a preference, a decision, a durable config), update the one canonical item in
  place rather than writing a new near-duplicate copy.
