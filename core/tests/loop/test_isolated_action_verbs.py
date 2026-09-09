"""What an exact tool set holds when it also holds an action."""

from pathlib import Path

from ufo.runtime.queue import _with_action_verbs
from ufo.runtime.tools.registry import ACTION_READ_TOOLS, OBJECT_ACTION_TOOL, ToolDef

DEPLOY = "action:site:deploy_website"


def _tool(name: str) -> ToolDef:
    return ToolDef(name=name, description=name, input_model=None, handler=None)


ALL = (_tool("bash"), _tool(OBJECT_ACTION_TOOL), *(_tool(name) for name in ACTION_READ_TOOLS))


def test_a_discovering_turn_gets_the_reads_that_publish_an_action() -> None:
    held = _with_action_verbs((_tool("bash"),), ALL, frozenset({DEPLOY}))
    names = {tool.name for tool in held}
    assert OBJECT_ACTION_TOOL in names
    assert set(ACTION_READ_TOOLS) <= names


def test_an_exact_tool_set_keeps_the_dispatcher_and_nothing_else() -> None:
    """A profile declaring `isolated_tools` names every tool it holds and is handed its one call
    verbatim, so the reads are not a companion it needs. One recorded builder spent 93 rounds
    listing objects, and another ten consecutive `object_list` calls, hunting for a site while the
    design it was asked to build sat beside it."""

    held = _with_action_verbs((_tool("bash"),), ALL, frozenset({DEPLOY}), discovery=False)
    names = {tool.name for tool in held}
    assert OBJECT_ACTION_TOOL in names, "without the dispatcher the granted action cannot be called"
    assert not set(ACTION_READ_TOOLS) & names


def test_a_turn_holding_no_action_gets_no_verbs_either_way() -> None:
    for discovery in (True, False):
        held = _with_action_verbs((_tool("bash"),), ALL, frozenset(), discovery=discovery)
        assert {tool.name for tool in held} == {"bash"}


def test_the_subagent_path_takes_discovery_from_the_profile() -> None:
    """The narrowing is only worth anything if the caller passes it. The tests above drive the
    function directly, so this holds the one wiring that decides which profiles get the reads."""

    assemble = Path("core/src/ufo/host/assemble.py").read_text()
    assert "discovery=not profile.isolated_tools," in assemble
