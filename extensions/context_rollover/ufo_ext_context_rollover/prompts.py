"""The words this strategy owns: what the agent is told its window does at a rollover.

The block fills the system prompt's `{{context_window}}` slot for a deploy that selects `rollover`,
so the prose and the tools it names ship with the behavior instead of with the host shell."""

from pathlib import Path

CONTEXT_WINDOW_BLOCK = (Path(__file__).parent / "prompts" / "context_window.md").read_text().strip()
