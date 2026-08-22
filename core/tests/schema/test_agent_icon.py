"""The icons a picker offers, the slugs the type admits, and the assignment a new agent's row is
stamped from."""

import pytest
from pydantic import TypeAdapter, ValidationError

from ufo.schema.records import (
    AGENT_ICON_KEYWORDS,
    AGENT_ICONS,
    DEFAULT_AGENT_ICON,
    MAIN_AGENT_ICON,
    TablerIcon,
    auto_agent_icon,
)

ICON_COUNT = 40


def test_the_picker_offers_a_shortlist_of_the_marks_the_type_admits() -> None:
    admits = TypeAdapter(TablerIcon)
    for icon in AGENT_ICONS:
        assert admits.validate_python(icon) == icon
    assert admits.validate_python("anchor") == "anchor"
    assert len(AGENT_ICONS) == ICON_COUNT
    assert len(set(AGENT_ICONS)) == ICON_COUNT
    assert DEFAULT_AGENT_ICON in AGENT_ICONS
    assert set(AGENT_ICON_KEYWORDS.values()) <= set(AGENT_ICONS)


def test_the_products_own_mark_is_reserved_and_never_offered() -> None:
    """The picker offers the set core names, so the product's own mark stays out of it. The type
    still admits the slug: the main agent's row holds it, every keyword rule names another mark, and
    a row holding it keeps drawing it."""
    assert MAIN_AGENT_ICON not in AGENT_ICONS
    assert TypeAdapter(TablerIcon).validate_python(MAIN_AGENT_ICON) == MAIN_AGENT_ICON
    assert MAIN_AGENT_ICON not in AGENT_ICON_KEYWORDS.values()
    assert DEFAULT_AGENT_ICON != MAIN_AGENT_ICON


@pytest.mark.parametrize(
    "refused", ["Rocket", "", "rocket ship", "rocket-", "-rocket", "2fa", "a" * 65]
)
def test_a_slug_no_mark_could_be_named_by_is_refused(refused: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(TablerIcon).validate_python(refused)


def test_the_first_keyword_token_of_the_name_wins() -> None:
    assert auto_agent_icon("Support Desk", ()) == "kardia"
    assert auto_agent_icon("Desk Support", ()) == "kalyx"
    assert auto_agent_icon("data_ops", ()) == "ziggurat"
    assert auto_agent_icon("Q4 finance review", ()) == "ashnan"


def test_a_name_no_keyword_names_lands_the_same_way_every_time() -> None:
    picked = auto_agent_icon("nightly digest", ())
    assert picked in AGENT_ICONS
    assert picked == auto_agent_icon("nightly digest", ())
    assert auto_agent_icon("nightly digest", (picked,)) != picked


def test_a_workspace_fills_the_set_before_it_repeats() -> None:
    taken: list[str] = []
    for index in range(len(AGENT_ICONS)):
        taken.append(auto_agent_icon(f"agent-{index}", taken))
    assert set(taken) == set(AGENT_ICONS)
    assert auto_agent_icon("support", taken) == "kardia"
    assert auto_agent_icon("nightly digest", taken) in AGENT_ICONS


def test_the_main_agents_icon_is_never_dealt_out() -> None:
    names = [f"agent-{index}" for index in range(500)] + ["ufo", "the ufo", "support ufo"]
    assert all(auto_agent_icon(name, ()) != MAIN_AGENT_ICON for name in names)
    assert all(auto_agent_icon(name, AGENT_ICONS) != MAIN_AGENT_ICON for name in names)
