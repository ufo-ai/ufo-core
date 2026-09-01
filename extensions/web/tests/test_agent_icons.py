import re
from pathlib import Path

from ufo.schema.records import AGENT_ICONS

ICON_COMPONENTS = (
    Path(__file__).resolve().parents[3] / "extensions/web/frontend/src/lib/agentIcon.tsx"
)
COMPONENT_MAP = re.compile(r"^export const AGENT_ICONS =\s*\{\n(.*?)\n\}", re.DOTALL | re.MULTILINE)
RESERVED_SLUG = re.compile(r'^const RESERVED_MARK = "([a-z0-9-]+)";$', re.MULTILINE)
BRAND_IMPORT = re.compile(r'^import brandMark from "@brand/(\S+)";$', re.MULTILINE)
BRAND_SOURCE = Path(__file__).resolve().parents[3] / "assets/brand"
MAPPED_SLUG = re.compile(r'^\s+"?([a-z0-9][a-z0-9-]*)"?:', re.MULTILINE)


def _offered() -> list[str]:
    """The slugs the portal's picker map holds, in its own order."""
    literal = COMPONENT_MAP.search(ICON_COMPONENTS.read_text())
    assert literal is not None, f"{ICON_COMPONENTS}: no AGENT_ICONS object literal to read"
    return MAPPED_SLUG.findall(literal.group(1))


def test_the_portal_bundles_exactly_the_marks_its_picker_offers() -> None:
    """An agent may carry any tabler mark, and any of them draws — but the marks the picker offers
    are the ones the bundle holds, so those are drawn without a fetch and in the order the picker
    shows them. Core names that shortlist and the portal bundles it; nothing else holds the two
    together, and a slug in one and not the other is either a picker entry with no mark or a mark
    the picker never offers. Equality in order keeps them one list rather than two that happen to
    share members, and makes the failure name the position that drifted."""
    drawn = _offered()
    named = list(AGENT_ICONS)
    assert drawn == named, (
        f"{ICON_COMPONENTS} drifted from AGENT_ICONS: "
        f"undrawn {[slug for slug in named if slug not in drawn]}, "
        f"unnamed {[slug for slug in drawn if slug not in named]}, "
        f"reordered {[(a, b) for a, b in zip(named, drawn, strict=False) if a != b]}"
    )


def test_the_reserved_mark_is_drawn_from_the_brands_own_file() -> None:
    """The reserved mark is the brand's three-dot mark, and the portal wears the brand file itself
    rather than a copy of its geometry: the mark the main agent's row draws is the mark the tab icon
    and the wordmark are cut from, so it cannot drift from them."""
    imported = BRAND_IMPORT.search(ICON_COMPONENTS.read_text())
    assert imported is not None, f"{ICON_COMPONENTS}: no brand mark is imported"
    drawn = BRAND_SOURCE / imported.group(1)
    assert drawn.is_file(), f"{drawn}: the brand keeps no such mark"
