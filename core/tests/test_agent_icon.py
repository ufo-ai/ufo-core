"""The closed icon set and the assignment a new agent's row is stamped from."""

from typing import get_args

from ufo.schema.records import (
    AGENT_ICON_KEYWORDS,
    AGENT_ICONS,
    AUTO_AGENT_ICONS,
    DEFAULT_AGENT_ICON,
    MAIN_AGENT_ICON,
    AgentIcon,
    auto_agent_icon,
)

ICON_COUNT = 41


def test_the_set_and_the_type_hold_the_same_icons() -> None:
    assert AGENT_ICONS == get_args(AgentIcon)
    assert len(AGENT_ICONS) == ICON_COUNT
    assert len(set(AGENT_ICONS)) == ICON_COUNT
    assert AGENT_ICONS == (MAIN_AGENT_ICON, *AUTO_AGENT_ICONS)
    assert DEFAULT_AGENT_ICON in AUTO_AGENT_ICONS
    assert set(AGENT_ICON_KEYWORDS.values()) <= set(AUTO_AGENT_ICONS)


def test_the_first_keyword_token_of_the_name_wins() -> None:
    assert auto_agent_icon("Support Desk", ()) == "lifebuoy"
    assert auto_agent_icon("Desk Support", ()) == "headset"
    assert auto_agent_icon("data_ops", ()) == "database"
    assert auto_agent_icon("Q4 finance review", ()) == "receipt"


def test_a_name_no_keyword_names_lands_the_same_way_every_time() -> None:
    picked = auto_agent_icon("nightly digest", ())
    assert picked in AUTO_AGENT_ICONS
    assert picked == auto_agent_icon("nightly digest", ())
    assert auto_agent_icon("nightly digest", (picked,)) != picked


def test_a_workspace_fills_the_set_before_it_repeats() -> None:
    taken: list[str] = []
    for index in range(len(AUTO_AGENT_ICONS)):
        taken.append(auto_agent_icon(f"agent-{index}", taken))
    assert set(taken) == set(AUTO_AGENT_ICONS)
    assert auto_agent_icon("support", taken) == "lifebuoy"
    assert auto_agent_icon("nightly digest", taken) in AUTO_AGENT_ICONS


def test_the_main_agents_icon_is_never_dealt_out() -> None:
    names = [f"agent-{index}" for index in range(500)] + ["ufo", "the ufo", "support ufo"]
    assert all(auto_agent_icon(name, ()) != MAIN_AGENT_ICON for name in names)
    assert all(auto_agent_icon(name, AGENT_ICONS) != MAIN_AGENT_ICON for name in names)
