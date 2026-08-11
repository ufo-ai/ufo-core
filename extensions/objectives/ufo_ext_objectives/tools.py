"""The three verbs an objective's turns share, and the evaluation that closes a step.

`record_step` is the one that matters: a `did` does not close anything. The extension re-reads the
state each condition names and decides, so "I finished it" and "it is true" stay different claims.
A step whose conditions do not hold comes back `unmet` with which ones failed, in the same result —
the worker learns it is not done at the moment it claims to be."""

import shlex

from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.context import ExtensionContext, agent_current
from ufo.sdk.o11y import emit_metric
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_objectives.store import (
    BLOCKED,
    CommandSucceeds,
    Condition,
    ConditionVerdict,
    FileContains,
    FileExists,
    Objectives,
    ObjectiveView,
    StepPlan,
    StepView,
    condition_summary,
)

CONDITION_TIMEOUT_SECONDS = 120
STEPS_MAX = 12


class PlanObjectiveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(
        min_length=1,
        max_length=80,
        description="Short stable handle for this objective, e.g. 'checkout-rollout'. Reusing a "
        "name revises that objective's plan rather than starting a second one.",
    )
    directive: str = Field(
        min_length=1,
        max_length=4_000,
        description="What this objective is for, in enough detail that a later turn holding none "
        "of your context could take it over.",
    )
    steps: tuple[StepPlan, ...] = Field(
        max_length=STEPS_MAX,
        description="The steps, in order. Size each one to a delegation or a genuinely hard "
        "stretch of your own work — something that can fail on its own terms — never a single "
        "file write or tool call. Give each one `accepts`: the conditions on real state that "
        "close it, naming state you did not create so the condition could pass. Write them now, "
        "while the work still looks easy; they are frozen once a step is attempted. A step with "
        "no accepts closes on your word alone and guarantees nothing.",
    )
    user_description: str = Field(
        description="What you are planning, in plain language for the activity timeline."
    )


class RecordStepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="The objective's name.")
    step: str = Field(description="The step's exact title.")
    kind: str = Field(
        pattern="^(did|blocked)$",
        description="'did' when you have attempted the step — its conditions are then evaluated, "
        "and it closes only if they hold. 'blocked' when it cannot proceed; say why in evidence.",
    )
    evidence: str = Field(
        max_length=2_000,
        description="What you actually did or what is blocking, naming the state you changed.",
    )
    user_description: str = Field(
        description="What you are recording, in plain language for the activity timeline."
    )


class ReadObjectiveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="The objective's name.")
    user_description: str = Field(
        description="What you are checking, in plain language for the activity timeline."
    )


def _require_ext(ctx: ToolContext) -> ExtensionContext:
    if ctx.ext is None:
        raise RuntimeError("the objectives tools require their extension context")
    return ctx.ext


def render(view: ObjectiveView) -> str:
    lines = [
        f"objective {view.name}: {view.directive}",
        f"attempts {view.attempts} · closed {view.confirmed}/{len(view.steps)}",
    ]
    for step in view.steps:
        checked = "" if step.checked_at is None else f" (checked {step.checked_at:%Y-%m-%d %H:%M})"
        lines.append(f"[{step.state}]{checked} {step.title}")
        for condition in step.accepts:
            lines.append(f"    needs: {condition_summary(condition)}")
        for verdict in step.verdicts:
            if not verdict.holds:
                lines.append(f"    UNMET: {verdict.detail}")
        for event in step.events[-2:]:
            lines.append(f"    {event.kind}: {event.evidence[:200]}")
    return "\n".join(lines)


async def evaluate(ctx: ToolContext, step: StepView) -> tuple[ConditionVerdict, ...]:
    verdicts: list[ConditionVerdict] = []
    for condition in step.accepts:
        verdicts.append(await _verdict(ctx, condition))
    return tuple(verdicts)


async def _verdict(ctx: ToolContext, condition: Condition) -> ConditionVerdict:
    """Each evaluation is counted by condition kind and whether it held. Whether the gate ever
    refuses is the question the offline arcs could not settle, and in production it is this
    counter: `objective_condition_total{holds="false"}` staying at zero means every step closed on
    the first claim and the check is decoration."""
    match condition:
        case FileExists():
            command = f"test -e {shlex.quote(condition.path)}"
        case FileContains():
            command = f"grep -qF -- {shlex.quote(condition.text)} {shlex.quote(condition.path)}"
        case CommandSucceeds():
            command = condition.command
    result = await ctx.sandbox.bash(command, timeout_s=CONDITION_TIMEOUT_SECONDS)
    holds = result.exit_code == 0
    emit_metric("objective_condition_total", kind=condition.kind, holds=str(holds).lower())
    detail = condition_summary(condition) if holds else f"{condition_summary(condition)} — false"
    return ConditionVerdict(condition=condition, holds=holds, detail=detail)


async def plan_objective(ctx: ToolContext, args: PlanObjectiveInput) -> ToolResult:
    ext = _require_ext(ctx)
    async with ext.transaction() as connection:
        objectives = Objectives(connection, agent_current().workspace_id)
        view = await objectives.plan(
            ctx.turn.conversation_id, args.name, args.directive, args.steps
        )
    return ToolResult(content=(TextContent(text=render(view)),))


async def record_step(ctx: ToolContext, args: RecordStepInput) -> ToolResult:
    ext = _require_ext(ctx)
    async with ext.transaction() as connection:
        objectives = Objectives(connection, agent_current().workspace_id)
        view = await objectives.named(ctx.turn.conversation_id, args.name)
        if view is None:
            return ToolResult(
                content=(TextContent(text=f"no objective named {args.name!r}"),), is_error=True
            )
        step = next((item for item in view.steps if item.title == args.step), None)
        if step is None:
            titles = ", ".join(item.title for item in view.steps)
            return ToolResult(
                content=(TextContent(text=f"no step {args.step!r}; steps are: {titles}"),),
                is_error=True,
            )
        wrote = await objectives.record(step, args.kind, ctx.turn.id, args.evidence)
    if args.kind == BLOCKED:
        emit_metric(
            "objective_step_recorded_total",
            kind=args.kind,
            state="repeated" if not wrote else "blocked",
        )
        if not wrote:
            return ToolResult(
                content=(
                    TextContent(
                        text=f"{args.step!r} is already blocked on this exact question and it is "
                        "already with the member. Do not raise it again; wait for their answer or "
                        "record a different block if something changed."
                    ),
                )
            )
        return ToolResult(content=(TextContent(text=f"recorded blocked on {args.step!r}"),))
    verdicts = await evaluate(ctx, step)
    async with ext.transaction() as connection:
        objectives = Objectives(connection, agent_current().workspace_id)
        await objectives.checked(step, verdicts, ctx.turn.id)
        refreshed = await objectives.named(ctx.turn.conversation_id, args.name)
    if refreshed is None:
        raise RuntimeError(f"objective {args.name!r} vanished while recording a step")
    graded = _with_verdicts(refreshed, args.step, verdicts)
    recorded = next((item for item in graded.steps if item.title == args.step), None)
    emit_metric(
        "objective_step_recorded_total",
        kind=args.kind,
        state=recorded.state if recorded is not None else "unknown",
    )
    return ToolResult(content=(TextContent(text=render(graded)),))


async def read_objective(ctx: ToolContext, args: ReadObjectiveInput) -> ToolResult:
    ext = _require_ext(ctx)
    async with ext.transaction() as connection:
        view = await Objectives(connection, agent_current().workspace_id).named(
            ctx.turn.conversation_id, args.name
        )
    if view is None:
        return ToolResult(
            content=(TextContent(text=f"no objective named {args.name!r}"),), is_error=True
        )
    graded = view
    for step in view.steps:
        if step.attempted and step.accepts:
            verdicts = await evaluate(ctx, step)
            async with ext.transaction() as connection:
                await Objectives(connection, agent_current().workspace_id).checked(
                    step, verdicts, ctx.turn.id
                )
            graded = _with_verdicts(graded, step.title, verdicts)
    return ToolResult(content=(TextContent(text=render(graded)),))


def _with_verdicts(
    view: ObjectiveView, title: str, verdicts: tuple[ConditionVerdict, ...]
) -> ObjectiveView:
    from dataclasses import replace

    return replace(
        view,
        steps=tuple(
            replace(step, verdicts=verdicts) if step.title == title else step for step in view.steps
        ),
    )


PLAN_OBJECTIVE_TOOL = ToolDef(
    name="plan_objective",
    description=(
        "Record what this objective is and the steps it takes, with the conditions on real state "
        "that close each one. Use it for work that outlives this turn: a later turn on this "
        "conversation opens holding the steps still outstanding, however little else it "
        "remembers. Reusing a name revises the plan."
    ),
    input_model=PlanObjectiveInput,
    handler=plan_objective,
    side_effecting=True,
    subagent_default=True,
)

RECORD_STEP_TOOL = ToolDef(
    name="record_step",
    description=(
        "Record an attempt on a step, or that it is blocked. A 'did' does not close the step — its "
        "conditions are re-read against real state and the result tells you whether it held."
    ),
    input_model=RecordStepInput,
    handler=record_step,
    side_effecting=True,
    subagent_default=True,
)

READ_OBJECTIVE_TOOL = ToolDef(
    name="read_objective",
    description=(
        "The objective's directive, every step with its state and unmet conditions, what has been "
        "attempted, and the attempts-against-closed counts. Read it before re-attempting a step."
    ),
    input_model=ReadObjectiveInput,
    handler=read_objective,
    subagent_default=True,
)
