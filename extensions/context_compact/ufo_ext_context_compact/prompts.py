"""The words this strategy owns: what the agent is told its window does at a compaction, and the
instructions the one model call at that boundary runs under.

`CONTEXT_WINDOW_BLOCK` fills the system prompt's `{{context_window}}` slot for a deploy that selects
`compact`; it names no history file and no reset tool, because this boundary offers neither.
`COMPACTION_SYSTEM_PROMPT` is the summarizer's own system prompt — a rollover deploy never loads
this module, so it never ships either."""

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"
CONTEXT_WINDOW_BLOCK = (_PROMPTS_DIR / "context_window.md").read_text().strip()
COMPACTION_SYSTEM_PROMPT = (_PROMPTS_DIR / "compaction.md").read_text().strip()
