"""The `monitor` action on the monitor kind: arm a durable watch and end the turn.

The probe runs once here, inline in the arming turn — the turn is live, so the sandbox and its
egress are the `bash` tool's own machinery. A command that fails fails this tool call rather than
dying unattended in a job an hour later, and the stdout it captured both seeds the baseline and
returns in the result, so the agent sees the state it is watching from. Only then does the row
persist; a refused arm leaves nothing behind."""

import json
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ufo.sdk.context import ExtensionContext
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


def _require_ext(ext: ExtensionContext | None) -> ExtensionContext:
    if ext is None:
        raise RuntimeError("the monitor action requires the monitors ExtensionContext")
    return ext


def _refusal(text: str) -> ToolResult:
    return ToolResult(content=(TextContent(text=text),), is_error=True)


async def monitor(ctx: ToolContext, args: MonitorInput) -> ToolResult:
    store = MonitorStore(_require_ext(ctx.ext))
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
    probe = await ctx.sandbox.bash(args.command, timeout_s=PROBE_TIMEOUT_SECONDS)
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
        audience=str(ctx.audience),
        command=args.command,
        interval_minutes=args.interval_minutes,
        deadline_at=now + timedelta(minutes=args.deadline_minutes),
        reason=args.reason,
        next_steps=args.next_steps,
        metadata=args.metadata,
        created_by_member_id=ctx.acting_member_id,
        baseline=baseline,
        next_probe_at=now + timedelta(minutes=args.interval_minutes),
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
        "and the result shows the output the watch starts from. Later probes reach your connected "
        "accounts as you reach them now, but never a model: a probe command that calls one "
        "succeeds in this turn and then fails every probe after it. At most 5 monitors per "
        "conversation."
    ),
    input_model=MonitorInput,
    handler=monitor,
    bound=ObjectBinding(kind=MONITOR_KIND, binding="collection"),
    side_effecting=True,
)
