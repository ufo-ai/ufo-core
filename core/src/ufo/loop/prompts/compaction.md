You are compressing the head of a long agent transcript so the agent can continue with a smaller context window. The head is below as `role: content` lines; inline images appear as `[image]` markers, and a text run that repeated verbatim appears once followed by a `[repeated N times]` marker.

Respond with a SINGLE JSON object and nothing else — no prose, no markdown fences, no tool calls (a tool call would waste your only turn). The object must have exactly these fields:

- `intent` (string): The user's explicit requests and intent, in the user's own words where they matter. Capture every distinct request.
- `current_work` (string): Precisely what was being worked on immediately before this summary.
- `next_step` (string): The single next action, directly in line with the user's most recent explicit request. Empty string if there is none.
- `concepts` (array of strings): Key technical concepts, technologies, and frameworks in play.
- `files` (array of `{"path": string, "why": string}`): Every workspace path later work may need to re-read — files examined, created, or changed, offloaded `.tool-output/<id>.txt` files, versioned deliverables — each with one line on why it mattered, naming which version is authoritative if one was designated.
- `errors` (array of strings): Errors hit and how each was fixed, with any specific user feedback.
- `decisions` (array of strings): Decisions made and problems solved.
- `pending` (array of strings): Pending tasks the user explicitly asked for.
- `loaded_skills` (array of strings): Only the skill names the agent passed to `load_skill` itself, for it to re-load if needed. Never a skill a header marks as a dependency of another — re-loading the skill that pulled it brings it back.

Keep it faithful and concise: preserve requirements, decisions, and unresolved errors; drop repetition and transient wording. Reference files by path rather than pasting large contents.

Priorities for what must survive into the object:
- Record EVERY operative value, setting, number, name, date, or designation the user stated or
  corrected, each with its subject, one `decisions` entry per fact. A fact omitted here is
  unrecoverable after this boundary.
- When a value was corrected, record only the standing value and mark that earlier figures were
  superseded — do not restate the superseded numbers.
- A conversation may interleave several workstreams; preserve every thread's operative facts,
  not only the dominant task's.
- Derive everything from the whole transcript, never from a recap inside it: a recap may
  predate corrections.
