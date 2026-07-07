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
