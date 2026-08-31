"""Object-action views shared by model discovery and portal controls."""

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue

if TYPE_CHECKING:
    from ufo.runtime.objects import BoundAction
    from ufo.runtime.tools.registry import ActionBinding, ToolDef


class ActionView(BaseModel):
    """An action declaration and pre-bound invocation template."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    input_schema: dict[str, JsonValue]
    call: dict[str, JsonValue]
    label: str | None = None
    confirm: str | None = None


def action_view(
    kind: str,
    bound: "BoundAction",
    *,
    name: str | None = None,
    agent: str | None = None,
    generation: UUID | None = None,
    presented: bool = False,
) -> ActionView:
    """Build a model or portal view pre-bound to an object read."""
    call: dict[str, JsonValue] = {"kind": kind, "action": bound.action.name}
    if name is not None:
        call["name"] = name
    if agent is not None:
        call["agent"] = agent
    if generation is not None:
        call["generation"] = str(generation)
    call["input"] = {}
    presentation = bound.action.presentation if presented else None
    return ActionView(
        name=bound.action.name,
        description=bound.action.description,
        input_schema=bound.action.input_model.model_json_schema(),
        call=call,
        label=None if presentation is None else presentation.label,
        confirm=None if presentation is None else presentation.confirm,
    )


def presented(bound: "BoundAction") -> bool:
    """Whether an action has a portal control."""
    return bound.action.presentation is not None and not bound.action.profile_only


def presented_action_views(
    actions: "Mapping[str, Mapping[str, BoundAction]]",
    kind: str,
    binding: "ActionBinding",
    *,
    name: str | None = None,
    generation: UUID | None = None,
) -> tuple[ActionView, ...]:
    """Project presented actions for one target, ordered by short name."""
    return tuple(
        action_view(kind, bound, name=name, generation=generation, presented=True)
        for _short_name, bound in sorted(actions.get(kind, {}).items())
        if bound.action.bound is not None
        and bound.action.bound.binding == binding
        and (bound.action.bound.name is None or bound.action.bound.name == name)
        and presented(bound)
    )


def frame_admissible_ids(
    tools: "Iterable[ToolDef]", actions: "Mapping[str, Mapping[str, BoundAction]]"
) -> tuple[str, ...]:
    """Return the canonical ids callable from an embedded app page."""
    globals_ = (
        tool.name
        for tool in tools
        if tool.bound is None and tool.presentation is not None and tool.presentation.frame
    )
    bound_ids = (
        bound.action.canonical_id
        for held in actions.values()
        for bound in held.values()
        if presented(bound)
        and bound.action.presentation is not None
        and bound.action.presentation.frame
    )
    return tuple(sorted({*globals_, *bound_ids}))
