---
name: prep
description: Assemble a 1:1 brief for a named person — open agenda items, commitments in flight, fresh observations, wins worth naming. Load when the member asks to prep for a 1:1 or a meeting with someone.
metadata:
  depends:
    - triage
---
# Prep — the 1:1 brief

Given a name:

1. **Place them.** `graph_search` the name (team, manager line, active topics) and `memory_search`
   their people file from the state repo.
2. **Pull the open threads.** Focused `memory_search` passes: `1:1 <name> — raise` (the agenda),
   `observation — <name>` (fresh context), `kudos — <name>` (wins not yet named), and the bare
   name (anything recent the conventions missed).
3. **Check the loops.** `list_page_watches` for watches naming them; this conversation's todo
   board for their items.
4. **Brief.** One message: the agenda ordered by `triage`, then commitments in flight (theirs and
   the member's), then wins, then watch-outs. Short enough to read at the meeting-room door.
5. **Close the loop after.** When the member says the 1:1 happened, offer to mark raised items:
   `memory_update` — `1:1 <name> held <date>: raised <items>`, kind `event` — and check off any
   todo the conversation resolved.
