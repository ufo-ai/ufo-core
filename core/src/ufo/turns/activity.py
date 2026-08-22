"""The member-facing projection of a bound tool call."""

import json

from ufo.hub import SkillLoad, ToolCall
from ufo.models.interface import ToolUseBlock

SKILL_LOAD_TOOL = "load_skill"
SKILL_SEARCH_TOOL = "skill_search"
TOOL_CALL_PREVIEW_CHARS = 200


def tool_activity(call: ToolUseBlock) -> ToolCall | SkillLoad:
    """Render the one member-facing activity frame for a bound tool call."""
    if call.name == SKILL_LOAD_TOOL:
        name = call.input.get("name")
        return SkillLoad(skill=name if isinstance(name, str) else "")
    description = call.input.get("user_description")
    preview = json.dumps(call.input, separators=(",", ":"))
    if len(preview) > TOOL_CALL_PREVIEW_CHARS:
        preview = preview[:TOOL_CALL_PREVIEW_CHARS] + "…"
    return ToolCall(
        tool=call.name,
        preview=preview,
        description=description if isinstance(description, str) else "",
    )
