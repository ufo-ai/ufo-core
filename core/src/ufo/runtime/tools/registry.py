"""Named tool definitions, their wire schemas, and lookup.

A `ToolDef` binds a name and its input model to the handler that runs it; `schema()` renders the
pair the model client puts on the wire. A def whose `bound` names an `ObjectBinding` is an object
action: it attaches to a registered kind's collection or to one visible instance, is discovered
through the object verbs, and dispatches through the one `object_action` wire tool under its
canonical id `action:<kind>:<name>` — it never enters the wire registry, so `ToolRegistry` refuses
it and reserves the `action:` prefix against global names.
`untrusted` marks a tool whose result carries
attacker-controllable content (a fetched page, a search snippet, a connector API response) — the
engine walls such a result in a data-only span so the model never reads it as instructions.
`side_effecting` marks a tool whose handler performs an external write or egress (a connector POST,
an MCP call, a local durable write): the engine folds a per-call `idempotency_key` onto the context
such a handler receives, so a cross-attempt resume can dedup the external effect at the provider. A
deterministic read (a bash read, a search, a page read) leaves it `False` and gets no key — DBOS
step memoization already makes re-executing it harmless. The same declaration decides an adopted
turn's guidance preemption: a keyed re-execution dedups (a spawn reattaches to its child, a send
dedups at the provider) and must run, since skipping it strands the keyed work and a re-issued
call would duplicate it under a fresh call id; only an unkeyed redo yields to queued member
guidance. `parallel_safe` marks a tool whose same-round calls the engine may dispatch
concurrently: the model owns not aiming two calls at one target, so independence is the default
and a tool leaves it `False` for one of two reasons. A final act (a question, a connect, a
credential request) is read from the round's last call, so its position is the semantics; and a
file tool whose own guard or staging reads same-round state — write and edit consult the paths
the turn has read, share_file's preflight measures a file another call may still be producing —
would race the very sequence its description prescribes. `ToolRegistry` is the frozen set the engine
dispatches against — it rejects a duplicate name at construction and fails loud on an unknown
lookup."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from ufo.harness.models.interface import ToolSchema
from ufo.runtime.access.member_authorization import AuthorizationBinding, AuthorizationScope
from ufo.runtime.tools.context import (
    MemberHandoffContext,
    MemberHandoffResult,
    ToolContext,
    ToolResult,
)
from ufo.schema.records import FINAL_ACT_FIELDS

OBJECT_ACTION_TOOL = "object_action"
OBJECT_LIST_TOOL = "object_list"
OBJECT_GET_TOOL = "object_get"
ACTION_READ_TOOLS = (OBJECT_LIST_TOOL, OBJECT_GET_TOOL)
ACTION_ID_PREFIX = "action:"
TRUSTED_TOOL_INPUT = object()
REQUESTED_BY = "requested_by"
REQUESTED_BY_DESCRIPTION = (
    "Message ref that explicitly requested this call. Required for any member-specific authority "
    "or capability, including admin actions; omit only for conversation-common work."
)


ActionBinding = Literal["collection", "instance"]


@dataclass(frozen=True)
class ObjectBinding:
    """What a bound `ToolDef` attaches to: a registered kind's collection, or one visible
    instance of it named per call. `name` pins an instance action to one row of a kind whose rows
    differ in what they are — a surface's connect belongs to that surface alone — so dispatch
    refuses any other target and discovery shows the action on that row only."""

    kind: str
    binding: ActionBinding
    name: str | None = None


@dataclass(frozen=True)
class ActionPresentation:
    """A callable's portal control: the label a schema-derived form renders and the confirmation
    it asks before submitting. Presence projects the control but grants no authority: a prepared
    intent carries the submitting member to `handler`, while a declared `member_handoff` receives
    the authenticated member directly. `frame` admits the callable from an embedded app page as
    well: a page speaks with the viewer's whole session, so an act reaches either path only by
    saying so."""

    label: str
    confirm: str | None = None
    frame: bool = False


@dataclass(frozen=True)
class StandingAuthorization[ModelT: BaseModel]:
    """A callable's validated input, bound context, and reusable permission scope."""

    context: ToolContext
    input: ModelT
    scope: AuthorizationScope
    binding: AuthorizationBinding


@dataclass(frozen=True)
class ToolDef[ModelT: BaseModel]:
    """A callable a turn may hold. `flag` names the feature flag that offers it: where that flag
    reads off for the turn's workspace, or nothing answers, the tool is absent from the catalog the
    model sees and the action is absent from the grants the turn holds — withheld, never refused.
    `binds_member_authority` gives a connector-, credential-, or access-grant callable a static
    `requested_by` field and permits that ref to bind member authority; false rejects the field
    regardless of who is speaking. `standing_authorization` binds a code-defined reusable scope
    and the exact context and input its execution must retain; absent, Always Allow is
    unavailable. `member_handoff` is the callable's authenticated-surface adapter: it runs the
    same domain workflow as `handler` without constructing a turn. `activity` is the member-facing
    step label the call publishes as it starts, in place of the one the activity model writes."""

    name: str
    description: str
    input_model: type[ModelT]
    handler: Callable[[ToolContext, ModelT], Awaitable[ToolResult]]
    untrusted: bool = False
    side_effecting: bool = False
    subagent_default: bool = False
    parallel_safe: bool = False
    retains_sandbox_authority: bool = False
    profile_only: bool = False
    binds_member_authority: bool = True
    bound: ObjectBinding | None = None
    agent_targetable: bool = False
    final_act_model: type[BaseModel] | None = None
    presentation: ActionPresentation | None = None
    flag: str | None = None
    activity: str | None = None
    standing_authorization: (
        Callable[[ToolContext, ModelT], Awaitable[StandingAuthorization[ModelT]]] | None
    ) = None
    member_handoff: (
        Callable[[MemberHandoffContext, ModelT], Awaitable[MemberHandoffResult]] | None
    ) = None

    @property
    def canonical_id(self) -> str:
        """The deploy-wide identity allowlists, hooks, idempotency keys, and telemetry address:
        the name itself for a global tool, `action:<kind>:<name>` for a bound action."""
        if self.bound is None:
            return self.name
        return f"{ACTION_ID_PREFIX}{self.bound.kind}:{self.name}"

    def schema(self) -> ToolSchema:
        input_schema = self.input_model.model_json_schema()
        properties = input_schema.setdefault("properties", {})
        if self.binds_member_authority:
            properties[REQUESTED_BY] = {
                "type": "string",
                "format": "uuid",
                "description": REQUESTED_BY_DESCRIPTION,
            }
        return ToolSchema(
            name=self.name,
            description=self.description,
            input_schema=input_schema,
        )


def validate_tool_declaration(tool: ToolDef[Any], label: str) -> None:
    """The boot gate every callable passes, bound or global: a declared presentation must carry a
    usable label and confirmation and cannot sit on a `profile_only` callable prepared member
    intents never reach, and a declared final-act model must be one a terminal frame field
    carries."""
    if tool.presentation is not None:
        if not tool.presentation.label.strip():
            raise ValueError(f"{label} declares a presentation with an empty label")
        if tool.presentation.confirm is not None and not tool.presentation.confirm.strip():
            raise ValueError(f"{label} declares a presentation with an empty confirmation")
        if tool.profile_only:
            raise ValueError(
                f"{label} is profile_only and declares a presentation — prepared member "
                "intents never reach profile-only calls"
            )
    if tool.profile_only and tool.binds_member_authority:
        raise ValueError(
            f"{label} is profile_only and binds member authority — supporting actors have no "
            "member requester"
        )
    if tool.standing_authorization is not None and not tool.binds_member_authority:
        raise ValueError(
            f"{label} declares standing authorization without binding member authority"
        )
    if tool.member_handoff is not None and tool.presentation is None:
        raise ValueError(f"{label} declares a member handoff with no presentation")
    if tool.bound is not None and tool.bound.name is not None and tool.bound.binding != "instance":
        raise ValueError(
            f"{label} pins collection action to {tool.bound.kind}/{tool.bound.name} — only an "
            "instance action names its row"
        )
    if tool.final_act_model is not None and tool.final_act_model not in FINAL_ACT_FIELDS:
        raise ValueError(
            f"{label} declares final-act model {tool.final_act_model.__name__}, which no "
            "terminal frame field carries"
        )


@dataclass(frozen=True)
class ToolRegistry:
    tools: tuple[ToolDef[Any], ...]

    def __post_init__(self) -> None:
        names = [tool.name for tool in self.tools]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate tool names: {', '.join(duplicates)}")
        bound = sorted(tool.name for tool in self.tools if tool.bound is not None)
        if bound:
            raise ValueError(f"bound actions never enter the wire registry: {', '.join(bound)}")
        reserved = sorted(name for name in names if name.startswith(ACTION_ID_PREFIX))
        if reserved:
            raise ValueError(
                f"tool names reserve the {ACTION_ID_PREFIX!r} prefix for object actions: "
                f"{', '.join(reserved)}"
            )
        collisions = sorted(
            tool.name for tool in self.tools if REQUESTED_BY in tool.input_model.model_fields
        )
        if collisions:
            raise ValueError(f"tool inputs reserve {REQUESTED_BY!r}: {', '.join(collisions)}")
        for tool in self.tools:
            validate_tool_declaration(tool, f"tool {tool.name!r}")

    def schemas(self) -> tuple[ToolSchema, ...]:
        return tuple(tool.schema() for tool in self.tools)

    def get(self, name: str) -> ToolDef[Any]:
        for tool in self.tools:
            if tool.name == name:
                return tool
        raise KeyError(f"unknown tool: {name}")
