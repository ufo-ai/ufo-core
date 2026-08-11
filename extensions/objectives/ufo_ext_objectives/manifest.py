"""The objectives extension: three verbs, and the injection that makes a wake self-orienting.

The hook is the load-bearing half. A tool the agent must remember to call is no use to a turn that
does not remember there is anything to call it about — a heartbeat fire and a hand-back wake both
arrive with fresh context. So every turn on a conversation carrying an objective gets its frontier
injected: the directive, what is unclosed, and which conditions are unmet."""

from ufo.sdk.context import agent_current
from ufo.sdk.manifest import (
    HookContext,
    HookOutcome,
    HookSpec,
    InjectContext,
    Manifest,
    PromptSection,
)
from ufo.sdk.o11y import emit_metric
from ufo_ext_objectives.store import ATTEMPTED_STATE, Objectives, condition_summary
from ufo_ext_objectives.tools import (
    PLAN_OBJECTIVE_TOOL,
    READ_OBJECTIVE_TOOL,
    RECORD_STEP_TOOL,
)

NAME = "objectives"
VERSION = "0.1.0"
SECTION_NAME = "objectives"
SECTION_BODY = (
    "Work that outlives this turn goes in an objective. A heartbeat or a subagent hand-back wakes "
    "you in a NEW turn holding none of your current working memory — only the durable record. "
    "Before ending a turn that delegated or scheduled anything, call plan_objective so that "
    "decision survives.\n"
    "A step is a unit you would hand to a specialist worker, or one that will take you several "
    "rounds and can fail on its own terms — ship the migration, get the PR through review, "
    "confirm the rollout held. Not a file write, not a tool call, not a note to yourself. If a "
    "step cannot fail in an interesting way, it is not a step: fold it into the one it serves. "
    "Most objectives are three to seven steps.\n"
    "Each step's accepts are the conditions that close it, and they must name state you did not "
    "invent for the purpose — a path the member named, a command whose meaning is not yours to "
    "define, a check someone else would run. A marker file you create so the condition can pass "
    "proves nothing; a condition that cannot fail is worse than none, because it manufactures "
    "confidence. Record attempts with record_step, which re-reads those conditions rather than "
    "taking your word, and returns 'unmet' when you believed you were done and the state "
    "disagrees. Attempts climbing while closed holds flat means split the work or ask, not "
    "another round."
)
FRONTIER_MAX_STEPS = 12


async def _inject_frontier(ctx: HookContext) -> HookOutcome:
    """Every turn on a conversation that carries an objective opens holding its frontier."""
    if ctx.turn is None:
        return None
    async with ctx.ext.transaction() as connection:
        view = await Objectives(connection, agent_current().workspace_id).on_conversation(
            ctx.turn.conversation_id
        )
    if view is None:
        return None
    emit_metric(
        "objective_frontier_injected_total",
        blocked=str(any(step.open_block is not None for step in view.frontier)).lower(),
    )
    lines = [
        f"<objective name={view.name!r}>",
        view.directive,
        f"attempts {view.attempts} · closed {view.confirmed}/{len(view.steps)}",
    ]
    for step in view.frontier[:FRONTIER_MAX_STEPS]:
        lines.append(f"[{step.state}] {step.title}")
        for condition in step.accepts:
            lines.append(f"    needs: {condition_summary(condition)}")
        standing = step.open_block
        if standing is not None:
            lines.append(
                f"    already raised with the member: {standing.evidence[:200]} "
                "(do not ask again; wait for their answer)"
            )
        if step.state == ATTEMPTED_STATE:
            lines.append(
                "    recorded as attempted; its conditions have not been run. read_objective "
                "checks them."
            )
    if not view.frontier:
        lines.append("every step is closed; confirm the objective is finished before saying so.")
    lines.append("</objective>")
    return InjectContext(text="\n".join(lines))


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(PLAN_OBJECTIVE_TOOL, RECORD_STEP_TOOL, READ_OBJECTIVE_TOOL),
        hooks=(HookSpec(event="user_prompt_submit", handler=_inject_frontier),),
        prompt_sections=(PromptSection(name=SECTION_NAME, body=SECTION_BODY),),
    )
