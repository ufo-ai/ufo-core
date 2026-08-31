"""The house delivery register: the writing rules every word an agent hands to a member or another
agent answers to, the words of a schema-owned field included.

The block is one copy. The loop slots it into the shell and into every subagent prompt; an
extension assembling a direct model call reaches it through `ufo.sdk.delivery_register`."""

from pathlib import Path

DELIVERY_REGISTER_BLOCK = (Path(__file__).parent / "delivery_register.md").read_text().strip()
DIRECT_PROSE_RESULT_MAX_CHARS = 400
SUBAGENT_RESULT_MAX_WORDS = 20
SUBAGENT_RESULT_DESCRIPTION = (
    f"The one parent-visible delivery, at most {SUBAGENT_RESULT_MAX_WORDS} words. Choose its "
    "register from the shared delivery register. Call finish as soon as work is complete; never "
    "write this result as assistant prose first. For a required artifact, give the conclusion and "
    "absolute path without restating its body. For a result-only task, give the result directly "
    "and create no file."
)
