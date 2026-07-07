You are compressing the head of a long agent transcript so the agent can continue with a smaller context window. The head is below as `role: content` lines; inline images appear as `[image]` markers.

Respond with a SINGLE JSON object and nothing else — no prose, no markdown fences, no tool calls (a tool call would waste your only turn). The object must have exactly these fields:

- `intent` (string): The user's explicit requests and intent, in the user's own words where they matter. Capture every distinct request.
- `current_work` (string): Precisely what was being worked on immediately before this summary.
- `next_step` (string): The single next action, directly in line with the user's most recent explicit request. Empty string if there is none.
- `concepts` (array of strings): Key technical concepts, technologies, and frameworks in play.
- `files` (array of `{"path": string, "why": string}`): Files and outputs examined, created, or changed, each with one line on why it mattered. Use the workspace path, including any `.tool-output/<id>.txt` offload file.
- `errors` (array of strings): Errors hit and how each was fixed, with any specific user feedback.
- `decisions` (array of strings): Decisions made and problems solved.
- `pending` (array of strings): Pending tasks the user explicitly asked for.
- `loaded_skills` (array of strings): Names of skills mounted before this boundary, for the agent to re-mount if needed.

Keep it faithful and concise: preserve requirements, decisions, and unresolved errors; drop repetition and transient wording. Reference files by path rather than pasting large contents.
