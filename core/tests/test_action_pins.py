from typing import cast

import pytest
import ufo_ext_sample as sample
from pydantic import BaseModel, ConfigDict

from ufo.runtime.object_views import frame_admissible_ids, presented_action_views
from ufo.runtime.objects import BoundAction, ObjectActionInput, ObjectVerbs
from ufo.runtime.tools.context import TextContent, ToolContext, ToolResult
from ufo.runtime.tools.registry import (
    ActionPresentation,
    ObjectBinding,
    ToolDef,
    validate_tool_declaration,
)


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _handler(ctx: ToolContext, args: _Input) -> ToolResult:
    return ToolResult(content=(TextContent(text="ok"),))


def _action(name: str, bound: ObjectBinding, **overrides: object) -> ToolDef:
    return ToolDef(
        name=name,
        description="d",
        input_model=_Input,
        handler=_handler,
        bound=bound,
        **overrides,  # type: ignore[arg-type]
    )


PINNED = _action(
    "connect",
    ObjectBinding(kind="surface", binding="instance", name="slack"),
    presentation=ActionPresentation(label="Connect Slack"),
)
OPEN = _action(
    "inspect",
    ObjectBinding(kind="surface", binding="instance"),
    presentation=ActionPresentation(label="Inspect"),
)


def _registry(*defs: ToolDef) -> dict[str, dict[str, BoundAction]]:
    return {
        "surface": {
            tool.name: BoundAction(action=tool, extension=None, context=None) for tool in defs
        }
    }


def test_a_pin_names_a_row_only_on_an_instance_binding() -> None:
    validate_tool_declaration(PINNED, "pinned")
    with pytest.raises(ValueError, match="only an instance action names its row"):
        validate_tool_declaration(
            _action("sweep", ObjectBinding(kind="surface", binding="collection", name="slack")),
            "collection pin",
        )


def test_a_pinned_action_projects_on_its_row_alone_and_an_open_one_on_every_row() -> None:
    registry = _registry(PINNED, OPEN)
    on_slack = [
        view.name for view in presented_action_views(registry, "surface", "instance", name="slack")
    ]
    on_imessage = [
        view.name
        for view in presented_action_views(registry, "surface", "instance", name="imessage")
    ]
    assert on_slack == ["connect", "inspect"]
    assert on_imessage == ["inspect"]


async def test_dispatch_refuses_a_pinned_action_aimed_at_another_row() -> None:
    verbs = ObjectVerbs(registry={}, actions=_registry(PINNED))
    with pytest.raises(ValueError, match="acts on surface/slack, not surface/imessage"):
        await verbs.action_target(
            cast(ToolContext, object()),
            PINNED,
            ObjectActionInput(kind="surface", action="connect", name="imessage"),
        )


def test_the_sample_keeps_an_unpinned_instance_action() -> None:
    engrave = next(tool for tool in sample.manifest().tools if tool.name == sample.ENGRAVE_ACTION)
    assert engrave.bound is not None and engrave.bound.name is None


def test_frame_admission_is_declared_and_carried_in_both_shapes() -> None:
    connect_account = ToolDef(
        name="connect_account",
        description="d",
        input_model=_Input,
        handler=_handler,
        presentation=ActionPresentation(label="Connect", frame=True),
    )
    framed = _action(
        "rebuild",
        ObjectBinding(kind="surface", binding="collection"),
        presentation=ActionPresentation(label="Rebuild", frame=True),
    )
    assert frame_admissible_ids((connect_account, PINNED), _registry(framed, OPEN)) == (
        "action:surface:rebuild",
        "connect_account",
    )
