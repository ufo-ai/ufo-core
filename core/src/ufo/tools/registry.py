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

import re
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from ufo.models.interface import Message, ToolResultBlock, ToolSchema, ToolUseBlock
from ufo.schema.records import FINAL_ACT_FIELDS
from ufo.tools.context import TextContent, ToolContext, ToolResult

OBJECT_ACTION_TOOL = "object_action"
ACTION_ID_PREFIX = "action:"
REQUESTED_BY = "requested_by"
REQUESTED_BY_DESCRIPTION = (
    "Message ref that explicitly requested this call. Required for any member-specific authority "
    "or capability, including admin actions; omit only for conversation-common work."
)
TOOL_SEARCH = "tool_search"
DIRECT_TOOL_LIMIT = 24
TOOL_SEARCH_PER_QUERY = 5
TOOL_SEARCH_DESCRIPTION_CHARS = 240
TOOL_SEARCH_QUERY_MAX_CHARS = 240
TOOL_SEARCH_TERM = re.compile(r"[a-z0-9]+")
EAGER_TOOL_NAMES = frozenset(
    {
        "ask_user",
        "bash",
        "call_external_tool",
        "connect_account",
        "describe_external_tools",
        "edit",
        "list_external_tools",
        "load_skill",
        "memory_search",
        "memory_update",
        "object_apply",
        "object_action",
        "object_delete",
        "object_explain",
        "object_get",
        "object_list",
        "read",
        "request_credentials",
        "search_connector_tools",
        "share_file",
        "skill_search",
        "spawn",
        "write",
    }
)


class ToolSearchInput(BaseModel):
    queries: tuple[str, ...] = Field(min_length=1, max_length=4)


class ToolSearchMatch(BaseModel):
    name: str
    description: str


class ToolSearchOutput(BaseModel):
    tools: tuple[ToolSearchMatch, ...]


async def tool_search_handler(ctx: ToolContext, args: ToolSearchInput) -> ToolResult:
    if ctx.find_tools is None:
        raise RuntimeError("tool search is not available in this context")
    return ToolResult(content=(TextContent(text=ctx.find_tools(args.queries)),))


ActionBinding = Literal["collection", "instance"]


@dataclass(frozen=True)
class ObjectBinding:
    """What a bound `ToolDef` attaches to: a registered kind's collection, or one visible
    instance of it named per call."""

    kind: str
    binding: ActionBinding


@dataclass(frozen=True)
class ActionPresentation:
    """A callable's portal control: the label a schema-derived form renders and the confirmation
    it asks before submitting. Presence admits the callable through the prepared-intent lane; it
    is never an authority grant — the turn carries the submitting member and the handler
    decides."""

    label: str
    confirm: str | None = None


@dataclass(frozen=True)
class ToolDef[ModelT: BaseModel]:
    name: str
    description: str
    input_model: type[ModelT]
    handler: Callable[[ToolContext, ModelT], Awaitable[ToolResult]]
    untrusted: bool = False
    side_effecting: bool = False
    subagent_default: bool = False
    parallel_safe: bool = False
    profile_only: bool = False
    bound: ObjectBinding | None = None
    agent_targetable: bool = False
    final_act_model: type[BaseModel] | None = None
    presentation: ActionPresentation | None = None

    @property
    def canonical_id(self) -> str:
        """The deploy-wide identity allowlists, hooks, idempotency keys, and telemetry address:
        the name itself for a global tool, `action:<kind>:<name>` for a bound action."""
        if self.bound is None:
            return self.name
        return f"{ACTION_ID_PREFIX}{self.bound.kind}:{self.name}"

    def schema(self, *, include_requested_by: bool = True) -> ToolSchema:
        input_schema = self.input_model.model_json_schema()
        properties = input_schema.setdefault("properties", {})
        if include_requested_by:
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


TOOL_SEARCH_DEF = ToolDef(
    name=TOOL_SEARCH,
    description="Find tools.",
    input_model=ToolSearchInput,
    handler=tool_search_handler,
    parallel_safe=True,
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

    def schemas(self, *, include_requested_by: bool = True) -> tuple[ToolSchema, ...]:
        return tuple(tool.schema(include_requested_by=include_requested_by) for tool in self.tools)

    def with_catalog(self) -> "ToolRegistry":
        if len(self.tools) <= DIRECT_TOOL_LIMIT or any(
            tool.name == TOOL_SEARCH for tool in self.tools
        ):
            return self
        return ToolRegistry((*self.tools, TOOL_SEARCH_DEF))

    def model_schemas(
        self,
        messages: tuple[Message, ...],
        *,
        include_requested_by: bool = True,
    ) -> tuple[ToolSchema, ...]:
        searchable = tuple(tool for tool in self.tools if tool.name != TOOL_SEARCH)
        search = next((tool for tool in self.tools if tool.name == TOOL_SEARCH), None)
        if search is None or len(searchable) <= DIRECT_TOOL_LIMIT:
            return tuple(
                tool.schema(include_requested_by=include_requested_by) for tool in searchable
            )
        loaded = self.loaded_names(messages)
        offered = tuple(
            tool for tool in searchable if tool.name in EAGER_TOOL_NAMES or tool.name in loaded
        )
        return (
            *tuple(tool.schema(include_requested_by=include_requested_by) for tool in offered),
            search.schema(include_requested_by=include_requested_by),
        )

    def find_tools(self, queries: tuple[str, ...]) -> tuple[ToolDef[Any], ...]:
        candidates = tuple(
            (
                position,
                tool,
                frozenset(TOOL_SEARCH_TERM.findall(tool.name.casefold())),
                tool.description.casefold(),
                " ".join(
                    f"{name} {field.description or ''}"
                    for name, field in tool.input_model.model_fields.items()
                ).casefold(),
            )
            for position, tool in enumerate(self.tools)
            if tool.name != TOOL_SEARCH and tool.name not in EAGER_TOOL_NAMES
        )
        selected: dict[str, ToolDef[Any]] = {}
        for query in queries:
            terms = frozenset(
                TOOL_SEARCH_TERM.findall(query[:TOOL_SEARCH_QUERY_MAX_CHARS].casefold())
            )
            ranked = sorted(
                (
                    (
                        sum(
                            4
                            if term in name
                            else 2
                            if term in description
                            else 1
                            if term in inputs
                            else 0
                            for term in terms
                        ),
                        position,
                        tool,
                    )
                    for position, tool, name, description, inputs in candidates
                ),
                key=lambda row: (-row[0], row[1]),
            )
            for score, _, tool in ranked[:TOOL_SEARCH_PER_QUERY]:
                if score > 0:
                    selected.setdefault(tool.name, tool)
        return tuple(selected.values())

    def find_tools_json(self, queries: tuple[str, ...]) -> str:
        matches = self.find_tools(queries)
        return ToolSearchOutput(
            tools=tuple(
                ToolSearchMatch(
                    name=tool.name,
                    description=tool.description[:TOOL_SEARCH_DESCRIPTION_CHARS],
                )
                for tool in matches
            )
        ).model_dump_json()

    def loaded_names(self, messages: tuple[Message, ...]) -> frozenset[str]:
        registry_names = frozenset(tool.name for tool in self.tools)
        loaded: set[str] = set()
        for call, result in self._completed_calls(messages):
            if call.name == TOOL_SEARCH:
                if not isinstance(result.content, str):
                    continue
                try:
                    output = ToolSearchOutput.model_validate_json(result.content)
                except ValueError:
                    continue
                loaded.update(match.name for match in output.tools if match.name in registry_names)
            else:
                loaded.add(call.name)
        return frozenset(loaded)

    def _completed_calls(
        self, messages: tuple[Message, ...]
    ) -> Iterator[tuple[ToolUseBlock, ToolResultBlock]]:
        calls: dict[str, ToolUseBlock] = {}
        for message in messages:
            if isinstance(message.content, str):
                continue
            for block in message.content:
                match block:
                    case ToolUseBlock():
                        calls[block.id] = block
                    case ToolResultBlock(tool_use_id=call_id, is_error=False) if call_id in calls:
                        yield calls[call_id], block

    def get(self, name: str) -> ToolDef[Any]:
        for tool in self.tools:
            if tool.name == name:
                return tool
        raise KeyError(f"unknown tool: {name}")
