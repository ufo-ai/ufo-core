"""The house delivery register: the writing rules every word an agent hands to a member or another
agent answers to, the words of a schema-owned field included.

The block is one copy. The loop slots it into the shell and into every subagent prompt; an
extension assembling a direct model call reaches it through `ufo.sdk.delivery_register`."""

from pathlib import Path

DELIVERY_REGISTER_BLOCK = (Path(__file__).parent / "delivery_register.md").read_text().strip()
