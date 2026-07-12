"""What the self-improvement extension declares: its scheduled evaluation tick.

The cron is a scheduled job (batch-at-interval), so it fires on the clock — never on the proposals
or store writes it makes. Its handler runs proposer, replay, and grader legs through the scoped
context's workspace-keyed, metered model access."""

from ufo.sdk.context import ExtensionContext, trajectory_workspaces
from ufo.sdk.jobs import JobSpec
from ufo.sdk.manifest import Manifest
from ufo_ext_self_improvement.cron import ImproveCron
from ufo_ext_self_improvement.evaluation import CandidateEvaluation
from ufo_ext_self_improvement.model import ModelAccessLeg
from ufo_ext_self_improvement.proposer import PromptProposer

NAME = "self_improvement"
VERSION = "0.1.0"
EVAL_JOB = "eval_cron"
EVAL_SCHEDULE = "0 0 * * * *"


async def _tick(ctx: ExtensionContext) -> None:
    if ctx.model is None:
        raise RuntimeError("self_improvement requires model access; none is wired")
    leg = ModelAccessLeg(ctx.model)
    await ImproveCron(
        ctx=ctx,
        proposer=PromptProposer(model=leg),
        evaluation=CandidateEvaluation(replay_model=leg, judge=leg),
    ).run()


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        jobs=(
            JobSpec(
                name=EVAL_JOB,
                schedule=EVAL_SCHEDULE,
                handler=_tick,
                candidates=trajectory_workspaces(),
            ),
        ),
    )
