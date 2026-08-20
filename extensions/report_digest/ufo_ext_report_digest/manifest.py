"""What the report-digest extension declares: the skill that holds the writing standard, and the
job that applies it to every report a scheduled run publishes.

The skill is the one copy of the standard. A member's agent reaches it with `load_skill` and writes
an entry in a reply; the job reads the same words as its system prompt and writes the stored entry
the portal's digest draws. Two callers, one set of rules."""

from ufo.sdk.context import ExtensionContext
from ufo.sdk.jobs import JobSpec, owner_candidates
from ufo.sdk.manifest import Manifest, SkillSpec
from ufo_ext_report_digest.digest import SKILL_DIR
from ufo_ext_report_digest.writer import DigestWriter, undigested_workspaces

NAME = "report_digest"
VERSION = "0.1.0"
JOB_NAME = "report_digest"

"""Every ten minutes. A report is read by a member the next time they open the page rather than the
second it lands, and a tighter interval buys nothing but a burstier spend."""
JOB_SCHEDULE = "0 */10 * * * *"


async def write_digests(ctx: ExtensionContext) -> None:
    if ctx.model is None:
        raise RuntimeError("report_digest needs the background model; serve wires it")
    if ctx.member_context_blob is None:
        raise RuntimeError("report_digest needs blob access to read a published report")
    await DigestWriter(ctx=ctx, model=ctx.model, blob=ctx.member_context_blob).run()


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        skills=(SkillSpec(path=SKILL_DIR),),
        jobs=(
            JobSpec(
                name=JOB_NAME,
                schedule=JOB_SCHEDULE,
                handler=write_digests,
                candidates=owner_candidates(undigested_workspaces),
            ),
        ),
        member_context_read=True,
    )
