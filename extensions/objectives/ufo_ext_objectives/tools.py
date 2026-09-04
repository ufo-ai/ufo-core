"""The three verbs an objective's turns share, and the evaluation that closes a step.

`record_step` is the one that matters: a `did` does not close anything. The extension re-reads the
state each condition names and decides, so "I finished it" and "it is true" stay different claims.
A step whose conditions do not hold comes back `unmet` with which ones failed, in the same result —
the worker learns it is not done at the moment it claims to be."""

import shlex

from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.context import ExtensionContext, agent_current
from ufo.sdk.o11y import emit_metric
from ufo.sdk.sandbox import ExecResult
from ufo.sdk.tools import (
    AppliedEffect,
    TextContent,
    ToolContext,
    ToolDef,
    ToolFailure,
    ToolResult,
)
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
CONDITION_OUTPUT_MAX_CHARS = 800
PLAN_PHASE = "plan"
RECORD_PHASE = "record"
PRODUCED_STATE_KINDS = frozenset({"file_exists", "file_contains"})
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
        "while the work still looks easy; they are frozen once a step is attempted. A "
        "`file_exists` or `file_contains` condition already true when you plan it is refused: it "
        "names an artifact the work is supposed to produce, so holding before the work starts "
        "makes it close the step on nothing. A `command_succeeds` condition may already hold — a "
        "suite that has to pass when you are finished, not only when your change lands, is green "
        "before you start, and it is the check worth having, because it is the one that can go "
        "back to failing when someone else's work lands. A step with no accepts closes on your "
        "word alone and guarantees nothing.",
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


class RunIndependentStepsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="The objective's name.")
    profile: str = Field(
        description="The subagent profile each step is delegated to, e.g. 'coding'."
    )


class ReadObjectiveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="The objective's name.")


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
        verdicts.append(await _verdict(ctx, condition, RECORD_PHASE))
    return tuple(verdicts)


async def _verdict(ctx: ToolContext, condition: Condition, phase: str) -> ConditionVerdict:
    """Each evaluation is counted by condition kind, whether it held, and which gate asked. Whether
    either gate ever refuses is the question the offline arcs could not settle, and in production it
    is this counter: `objective_condition_total{phase="record",holds="false"}` staying at zero means
    every step closed on the first claim and the closure check is decoration, while
    `{phase="plan",holds="true"}` counts the plans refused for naming state that was already there.
    The phases have to be read apart — a plan is admitted only when its gated conditions are false,
    so plan-phase `holds="false"` is the ordinary case and says nothing about closure."""
    match condition:
        case FileExists():
            command = f"test -e {shlex.quote(condition.path)}"
        case FileContains():
            command = f"grep -qF -- {shlex.quote(condition.text)} {shlex.quote(condition.path)}"
        case CommandSucceeds():
            command = condition.command
    result = await ctx.sandbox.bash(command, timeout_s=CONDITION_TIMEOUT_SECONDS)
    holds = result.exit_code == 0
    emit_metric(
        "objective_condition_total",
        kind=condition.kind,
        holds=str(holds).lower(),
        phase=phase,
    )
    detail = condition_summary(condition) if holds else _unmet(condition, result)
    return ConditionVerdict(condition=condition, holds=holds, detail=detail)


def _unmet(condition: Condition, result: ExecResult) -> str:
    """Why the check said no. "false" alone is the whole of what a step gets told today, and a
    `command_succeeds` gate is a whole test suite behind that word — the agent cannot tell a
    failing assertion from a missing binary from a budget that ran out, and each wants a different
    next move. The exit code cannot say which either, since a command running `timeout` exits 124
    exactly as a carrier-stopped one does, so the expiry is named separately. The tail is what is
    kept: a failing run puts its reason at the end, and every verdict here is rendered into a view
    that lists all of them."""
    ended = (
        f"exit {result.exit_code}"
        if result.timed_out_after_s is None
        else f"stopped after {result.timed_out_after_s}s"
    )
    said = (result.stderr.strip() or result.stdout.strip())[-CONDITION_OUTPUT_MAX_CHARS:]
    unmet = f"{condition_summary(condition)} — false ({ended})"
    return f"{unmet}: {said}" if said else f"{unmet}, and the command said nothing"


async def plan_objective(ctx: ToolContext, args: PlanObjectiveInput) -> ToolResult:
    """Refuse a condition that names produced state and already has it, then store the plan.

    `file_exists` and `file_contains` name an artifact, so the whole of what they prove is that the
    work made them true; one already true when the step is written closes it on nothing. Production
    bore that out — they refused 0 of 45 claims, because a path the worker names is one the worker
    is about to create. Those 45 were not working, they were manufacturing confidence, which the
    design holds to be worse than no check at all.

    `command_succeeds` is never gated, and that asymmetry is the design rather than an exemption.
    A suite that has to pass when the work is finished is green before it starts, and it is the one
    condition that can go back to `unmet` when someone else's commit lands — refusing it for being
    green would leave only conditions that cannot fail, which is the failure this whole gate exists
    to prevent. It refused 4 of 108 claims in production, so it is also the kind already working.

    A condition already on the record is not re-checked. It was gated when it was written, and an
    attempted step's `accepts` are frozen against revision anyway, so re-running either would refuse
    a plan revision for having made progress — losing the steps it meant to add."""
    ext = _require_ext(ctx)
    async with ext.transaction() as connection:
        existing = await Objectives(connection, agent_current().workspace_id).named(
            ctx.turn.conversation_id, args.name
        )
    recorded: dict[str, StepView] = (
        {} if existing is None else {step.title: step for step in existing.steps}
    )
    vacuous: list[str] = []
    for step in args.steps:
        stored = recorded.get(step.title)
        if stored is not None and stored.attempted:
            continue
        held = () if stored is None else stored.accepts
        for condition in step.accepts:
            if condition.kind not in PRODUCED_STATE_KINDS or condition in held:
                continue
            if (await _verdict(ctx, condition, PLAN_PHASE)).holds:
                vacuous.append(f"{step.title!r}: {condition_summary(condition)}")
    if vacuous:
        listed = "\n".join(f"  {item}" for item in vacuous)
        return ToolResult(
            content=(
                TextContent(
                    text="these conditions name state that is already there, so they cannot close "
                    f"anything — the work they stand for has not happened yet:\n{listed}\nName the "
                    "state this step will actually produce, or gate the step on a command whose "
                    "success depends on the work, and plan again."
                ),
            ),
            is_error=True,
        )
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


async def run_independent_steps(ctx: ToolContext, args: RunIndependentStepsInput) -> ToolResult:
    """Dispatch every step the plan declared independent, at once.

    The plan already answered which steps need nothing from each other, so the shape of the fan-out
    is read from the record instead of decided again by whichever turn is awake — no tokens spent
    re-deriving it and the same answer every time. Each child is keyed on the step it is taking, so
    a step re-executed inside this turn reconnects to the child it already started rather than
    paying for a second one. Children deliver their own results, so this turn ends rather than
    holding its conversation's partition open while they run."""
    ext = _require_ext(ctx)
    async with ext.transaction() as connection:
        view = await Objectives(connection, agent_current().workspace_id).named(
            ctx.turn.conversation_id, args.name
        )
    if view is None:
        return ToolResult(
            content=(TextContent(text=f"no objective named {args.name!r}"),), is_error=True
        )
    runnable = view.runnable
    if not runnable:
        return ToolResult(
            content=(
                TextContent(
                    text="no step is ready to run on its own: every independent step is "
                    "already attempted or blocked. Read the objective and take the dependent "
                    "steps in order."
                ),
            )
        )
    dispatched: list[tuple[str, str]] = []
    for step in runnable:
        try:
            result = await ctx.spawn(
                args.profile,
                {"task": f"{view.directive}\n\nYour step: {step.title}"},
                background=True,
                dedup_key=f"{args.name}/{step.title}",
                delivers_result=True,
            )
        except Exception as error:
            return _dispatch_stopped(step.title, dispatched, error).result()
        dispatched.append((step.title, str(result.turn_id)))
        emit_metric("objective_step_dispatched_total", profile=args.profile)
    lines = [f"dispatched {len(dispatched)} independent step(s) to {args.profile!r}:"]
    lines.extend(f"  {title} -> subagent {turn_id}" for title, turn_id in dispatched)
    lines.append(
        "Each hands its result back to this conversation when it finishes. End your turn; you will "
        "be woken with their answers. Record each step when its result arrives."
    )
    return ToolResult(content=(TextContent(text="\n".join(lines)),))


def _dispatch_stopped(
    step: str, dispatched: list[tuple[str, str]], error: Exception
) -> ToolFailure:
    """A fan-out that stopped part-way names the children already running.

    Each of them is a real background turn that will deliver its result into this conversation
    whatever this call returns, so a failure reporting only the step that would not start reads as
    though none of them exist — and the turn ends without the record it needs to match arriving
    results to steps. Re-issuing the tool is safe and is the way to take the rest: every child is
    keyed on its step, so the ones listed here reconnect rather than run twice."""
    detail = str(error).strip() or type(error).__name__
    return ToolFailure(
        operation="run_independent_steps",
        summary=(
            f"step {step!r} would not start: {detail}. "
            f"{len(dispatched)} step(s) were already dispatched and are running — their results "
            "still arrive in this conversation, so record each one when it does. Re-issue this "
            "tool to take the remaining steps; the dispatched ones reconnect rather than repeat."
        ),
        applied=tuple(
            AppliedEffect(kind="step", identity=turn_id, state=f"running: {title}")
            for title, turn_id in dispatched
        ),
    )


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

RUN_INDEPENDENT_STEPS_TOOL = ToolDef(
    name="run_independent_steps",
    description=(
        "Delegate every step the plan marked independent to a subagent at once, then end your "
        "turn. "
        "Use it instead of taking those steps yourself or spawning them one at a time — the plan "
        "already says which steps need nothing from each other."
    ),
    input_model=RunIndependentStepsInput,
    handler=run_independent_steps,
    side_effecting=True,
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
