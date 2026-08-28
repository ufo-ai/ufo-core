import ufo_ext_sample as sample

from ufo.loop.engine import _intent_admits
from ufo.objects import ObjectVerbs
from ufo.tools.builtins import BUILTIN_TOOLS
from ufo.tools.registry import ToolDef


def _named(name: str, tools: tuple[ToolDef, ...]) -> ToolDef:
    return next(tool for tool in tools if tool.name == name)


def test_a_presented_bound_action_is_admitted() -> None:
    assert _intent_admits(_named(sample.ENGRAVE_ACTION, sample.manifest().tools))


def test_a_bound_action_without_presentation_is_refused() -> None:
    assert not _intent_admits(_named(sample.POLISH_ACTION, sample.manifest().tools))


def test_the_presented_retained_global_is_admitted() -> None:
    assert _intent_admits(_named("connect_account", BUILTIN_TOOLS))


def test_a_global_without_presentation_is_refused() -> None:
    assert not _intent_admits(_named("bash", BUILTIN_TOOLS))


def test_the_object_verbs_keep_their_own_lane() -> None:
    verbs = ObjectVerbs(registry={}).tools()
    assert _intent_admits(_named("object_apply", verbs))
    assert _intent_admits(_named("object_delete", verbs))
    assert not _intent_admits(_named("object_get", verbs))
