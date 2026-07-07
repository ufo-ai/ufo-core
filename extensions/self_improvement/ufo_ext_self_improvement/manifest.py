"""What the self-improvement extension declares: the eval cron and the model-key slot it reads.

The cron is a scheduled job (batch-at-interval), so it fires on the clock — never on the proposals
or store writes it makes. Its handler builds the Anthropic model leg from the declared credential
slot (read in-process, not injected onto a sandbox wire) and runs one tick of the loop with the
extension's scoped context."""

import anthropic

from ufo.sdk.context import ExtensionContext
from ufo.sdk.jobs import JobSpec
from ufo.sdk.manifest import CredentialSlot, Manifest
from ufo_ext_self_improvement.cron import ImproveCron
from ufo_ext_self_improvement.evaluation import CandidateEvaluation
from ufo_ext_self_improvement.model import PROVIDER_TIMEOUT_SECONDS, AnthropicModelLeg
from ufo_ext_self_improvement.proposer import PromptProposer

NAME = "self_improvement"
VERSION = "0.1.0"
EVAL_JOB = "eval_cron"
EVAL_SCHEDULE = "0 0 * * * *"
MODEL_KEY_SLOT = "self_improvement_model_key"
MODEL = "claude-opus-4-8"


async def _tick(ctx: ExtensionContext) -> None:
    leg = AnthropicModelLeg(
        client=anthropic.AsyncAnthropic(
            api_key=await ctx.credentials.get(MODEL_KEY_SLOT),
            max_retries=0,
            timeout=PROVIDER_TIMEOUT_SECONDS,
        ),
        model=MODEL,
    )
    await ImproveCron(
        ctx=ctx,
        proposer=PromptProposer(model=leg),
        evaluation=CandidateEvaluation(replay_model=leg, judge=leg),
    ).run()


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        jobs=(JobSpec(name=EVAL_JOB, schedule=EVAL_SCHEDULE, handler=_tick),),
        credentials=(
            CredentialSlot(
                name=MODEL_KEY_SLOT,
                description="Model API key the self-improvement loop reads in-process to run its "
                "proposer, replay, and grader legs.",
            ),
        ),
    )
