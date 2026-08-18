import re
from pathlib import Path

from ufo.schema.records import AGENT_ICONS

ICON_COMPONENTS = (
    Path(__file__).resolve().parents[3] / "extensions/web/frontend/src/lib/agentIcon.tsx"
)
COMPONENT_MAP = re.compile(r"\bAGENT_ICONS\b.*?=\s*\{\n(.*?)\n\}", re.DOTALL)
MAPPED_SLUG = re.compile(r'^\s+"?([a-z0-9][a-z0-9-]*)"?:', re.MULTILINE)


def test_the_portal_draws_exactly_the_icons_an_agent_may_carry() -> None:
    """An agent's icon is one slug from the closed set core assigns and validates against, and the
    portal turns that slug into a drawn mark through one hand-written component map. Nothing else
    holds the two together: a slug core admits that the map omits reaches a member as an empty
    square, and a component the set never names is a picker entry no agent can hold. Equality in
    order keeps them one list rather than two that happen to share members, and makes the failure
    name the position that drifted."""
    source = ICON_COMPONENTS.read_text()
    literal = COMPONENT_MAP.search(source)
    assert literal is not None, f"{ICON_COMPONENTS}: no AGENT_ICONS object literal to read"
    drawn = MAPPED_SLUG.findall(literal.group(1))
    named = list(AGENT_ICONS)
    assert drawn == named, (
        f"{ICON_COMPONENTS} drifted from AGENT_ICONS: "
        f"undrawn {[slug for slug in named if slug not in drawn]}, "
        f"unnamed {[slug for slug in drawn if slug not in named]}, "
        f"reordered {[(a, b) for a, b in zip(named, drawn, strict=False) if a != b]}"
    )
