"""The `monitor` action on the monitor kind: arm a durable watch and end the turn.

The probe runs once here under the monitor's connection scope. A command that fails fails this
tool call rather than dying unattended in a job an hour later, and the stdout it captured both
seeds the baseline and returns in the result, so the agent sees the state it is watching from.
Only then does the row persist; a refused arm leaves nothing behind."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from ufo.sdk.authority import authority_member_id
from ufo.sdk.context import CONNECTION_SCOPE_MAX, ExtensionContext
from ufo.sdk.tools import ObjectBinding, TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_monitors.monitors import (
    ARMED_MAX,
    DEADLINE_MAX_MINUTES,
    MONITOR_KIND,
    NAME_MAX,
    NAME_PATTERN,
    PROBE_TIMEOUT_SECONDS,
    MonitorStore,
    capped,
    qualified_name,
    stderr_tail,
)

MONITOR_TOOL_NAME = "monitor"
DEFAULT_INTERVAL_MINUTES = 5
MONITOR_DIRECTIVE = (
    "Reply with `ai_response`, then end your turn. The monitor fires exactly once — on a change in "
    "the probe's output, on three consecutive probe failures, or at the deadline — and retires; "
    "watching further is that turn calling `monitor` again."
)


class MonitorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(
        max_length=NAME_MAX,
        pattern=f"^{NAME_PATTERN}$",
        description=(
            "Slug naming this watch, unique among the monitors armed in this conversation. "
            "The `monitor` object it reads back as prefixes the slug with this conversation, "
            "so use that fuller name when listing or deleting."
        ),
    )
    command: str = Field(
        description=(
            "Shell command run in this conversation's sandbox each interval. Its stdout is "
            "compared byte-for-byte against the arming run's, so make the output deterministic — "
            "sort it and strip clocks, counters, and elapsed times, or every tick reads as a "
            "change."
        )
    )
    interval_minutes: int = Field(
        default=DEFAULT_INTERVAL_MINUTES,
        ge=1,
        description="Minimum minutes between probes.",
    )
    deadline_minutes: int = Field(
        ge=1,
        le=DEADLINE_MAX_MINUTES,
        description="Minutes after which the monitor fires whether or not anything changed.",
    )
    ai_response: str = Field(description="Message to show while the watch is armed.")
    reason: str = Field(description="What is being watched and why.")
    next_steps: str = Field(description="Instructions for the fired turn.")
    metadata: dict[str, JsonValue] | None = Field(
        default=None, description="State the fired turn needs."
    )
    connections: tuple[UUID, ...] = Field(
        max_length=CONNECTION_SCOPE_MAX,
    )

    @field_validator("connections")
    @classmethod
    def canonical_connections(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(set(value)) != len(value):
            raise ValueError("connections cannot contain duplicate ids")
        return tuple(sorted(value, key=str))


def _require_ext(ext: ExtensionContext | None) -> ExtensionContext:
    if ext is None:
        raise RuntimeError("the monitor action requires the monitors ExtensionContext")
    return ext


def _refusal(text: str) -> ToolResult:
    return ToolResult(content=(TextContent(text=text),), is_error=True)


async def monitor(ctx: ToolContext, args: MonitorInput) -> ToolResult:
    ext = _require_ext(ctx.ext)
    store = MonitorStore(ext)
    armed = await store.armed(ctx.turn.conversation_id)
    if len(armed) >= ARMED_MAX:
        return _refusal(
            f"{ARMED_MAX} monitors are already armed in this conversation, which is the cap. "
            "Delete one with the monitor object kind before arming another."
        )
    name = qualified_name(ctx.turn.conversation_id, args.slug)
    if any(row.name == name for row in armed):
        return _refusal(
            f"a monitor named {args.slug!r} is already armed in this conversation; delete it or "
            "choose another name"
        )
    available = frozenset(await ctx.connector_connection_ids())
    unavailable = tuple(
        connection_id for connection_id in args.connections if connection_id not in available
    )
    if unavailable:
        return _refusal(
            "connections are outside this turn's scope: "
            + ", ".join(str(connection_id) for connection_id in unavailable)
        )
    if ext.probes is None:
        raise RuntimeError("the monitor action requires the probes capability; none is wired")
    internet_access = (
        None if ctx.turn.runtime_config is None else ctx.turn.runtime_config.internet_access
    )
    probe = await ext.probes.run(
        ctx.turn.conversation_id,
        args.command,
        PROBE_TIMEOUT_SECONDS,
        authority=ctx.authority,
        connections=args.connections,
        internet_access=internet_access,
    )
    if probe.exit_code != 0:
        tail = stderr_tail(probe.stderr)
        return _refusal(
            f"the probe command exited {probe.exit_code}, so no monitor was armed"
            + (f"\n{tail}" if tail else "")
        )
    now = datetime.now(UTC)
    baseline = capped(probe.stdout)
    row = await store.arm(
        conversation_id=ctx.turn.conversation_id,
        agent_id=ctx.turn.agent_id,
        name=name,
        command=args.command,
        interval_minutes=args.interval_minutes,
        deadline_at=now + timedelta(minutes=args.deadline_minutes),
        reason=args.reason,
        next_steps=args.next_steps,
        metadata=args.metadata,
        created_by_member_id=authority_member_id(ctx.authority),
        baseline=baseline,
        next_probe_at=now + timedelta(minutes=args.interval_minutes),
        connections=args.connections,
        internet_access=internet_access,
    )
    payload: dict[str, JsonValue] = {
        "armed": row.name,
        "ai_response": args.ai_response,
        "baseline": row.baseline,
        "deadline_at": row.deadline_at.isoformat(),
        "interval_minutes": row.interval_minutes,
        "reason": row.reason,
        "next_steps": row.next_steps,
    }
    return ToolResult(content=(TextContent(text=f"{MONITOR_DIRECTIVE}\n{json.dumps(payload)}"),))


MONITOR_TOOL = ToolDef(
    name=MONITOR_TOOL_NAME,
    description=(
        "Watch external state and end this turn: a shell probe runs in this conversation's sandbox "
        "every `interval_minutes` until its output changes, it fails three times running, or "
        "`deadline_minutes` passes — then you are invoked once with the new output, `next_steps`, "
        "and `metadata`. Quiet intervals cost nothing and post nothing. Use it for a CI run, a "
        "deploy, a build log, or an inbox; use `pause_and_wait` instead when the thing you are "
        "waiting for is a member's reply. The command runs once now, so a broken probe fails here "
        "and the result shows the output the watch starts from. A probe cannot call a model. At "
        "most 5 monitors per conversation."
    ),
    input_model=MonitorInput,
    handler=monitor,
    bound=ObjectBinding(kind=MONITOR_KIND, binding="collection"),
    side_effecting=True,
)
