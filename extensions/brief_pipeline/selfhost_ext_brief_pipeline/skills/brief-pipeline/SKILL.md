---
name: brief-pipeline
description: Load when a member asks for a brief, one-pager, or short internal write-up — chains the outline, draft, and critic subagents into a reviewed brief.
---
# Brief pipeline — outline → draft → critic

Produce a reviewed brief by chaining three typed subagents. Each spawn is **foreground** — every
stage needs the previous stage's output, so never background them and never skip a stage.

1. **Outline** — `spawn_subagent` profile `brief_outline`, payload `{"topic": …, "audience": …}`.
   The result is `{"outline": …}`.
2. **Draft** — `spawn_subagent` profile `brief_draft`, payload `{"topic": …, "outline": …}` using
   the outline verbatim. The result is `{"draft": …}`.
3. **Critique** — `spawn_subagent` profile `brief_critic`, payload `{"draft": …}`. The result is
   `{"verdict": "ship" | "revise", "improvements": …}`.
4. **Deliver** — on `ship`, reply with the draft as-is. On `revise`, apply each numbered
   improvement to the draft yourself — do not re-spawn the pipeline — and reply with the revised
   brief, noting in one line what the review changed.

The stages are toolless writers: the payload is everything they see, so put the member's real
topic and audience in it, never a summary of a summary.
