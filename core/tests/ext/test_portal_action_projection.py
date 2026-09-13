from uuid import uuid4

import ufo_ext_sample as sample
from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict

from ufo.host.ext.loader import MemberObjectRegistry, load_manifests, member_object_registry
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.object_views import action_view, presented_action_views
from ufo.runtime.objects import BoundAction
from ufo.runtime.tools.context import TextContent, ToolContext, ToolResult
from ufo.runtime.tools.registry import ActionPresentation, ObjectBinding, ToolDef


def _sample_registry() -> MemberObjectRegistry:
    manifest = next((m for m in load_manifests() if m.name == sample.NAME), None)
    assert manifest is not None, "sample extension not discovered via entry points — run `uv sync`"
    return member_object_registry(
        (manifest,), CredentialStore(fernet=Fernet(Fernet.generate_key()))
    )


def test_the_portal_registry_carries_the_deploys_actions_beside_its_kinds() -> None:
    registry = _sample_registry()
    assert sample.WIDGET_KIND in registry.kinds
    assert {sample.ENGRAVE_ACTION, sample.POLISH_ACTION, sample.CALIBRATE_ACTION} <= set(
        registry.actions[sample.WIDGET_KIND]
    )


def test_only_presented_actions_project_pre_bound_to_the_row_the_read_answered() -> None:
    registry = _sample_registry()
    generation = uuid4()
    views = presented_action_views(
        registry.actions, sample.WIDGET_KIND, "instance", name="w1", generation=generation
    )
    assert [view.name for view in views] == [sample.ENGRAVE_ACTION]
    (engrave,) = views
    assert engrave.label == sample.ENGRAVE_PRESENTATION_LABEL
    assert engrave.confirm == sample.ENGRAVE_PRESENTATION_CONFIRM
    assert engrave.call == {
        "kind": sample.WIDGET_KIND,
        "action": sample.ENGRAVE_ACTION,
        "name": "w1",
        "generation": str(generation),
        "input": {},
    }
    assert presented_action_views(registry.actions, sample.WIDGET_KIND, "collection") == ()
    assert presented_action_views(registry.actions, "no_such_kind", "collection") == ()


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _handler(ctx: ToolContext, args: _Input) -> ToolResult:
    return ToolResult(content=(TextContent(text="ok"),))


def test_the_profile_only_wall_holds_on_the_projection() -> None:
    held = ToolDef(
        name="held",
        description="d",
        input_model=_Input,
        handler=_handler,
        bound=ObjectBinding(kind="widget", binding="collection"),
        profile_only=True,
        binds_member_authority=False,
        presentation=ActionPresentation(label="Hold"),
    )
    registry = {"widget": {"held": BoundAction(action=held, extension=None, context=None)}}
    assert presented_action_views(registry, "widget", "collection") == ()


def test_a_portal_view_is_the_model_view_plus_the_presentations_words() -> None:
    registry = _sample_registry()
    bound = registry.actions[sample.WIDGET_KIND][sample.ENGRAVE_ACTION]
    shown = action_view(sample.WIDGET_KIND, bound, name="w1", presented=True).model_dump(
        exclude_none=True
    )
    discovered = action_view(sample.WIDGET_KIND, bound, name="w1").model_dump(exclude_none=True)
    assert "label" not in discovered
    assert {
        key: value for key, value in shown.items() if key not in {"label", "confirm"}
    } == discovered
