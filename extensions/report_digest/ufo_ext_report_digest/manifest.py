"""What the report-digest extension declares: the skill that holds the writing standard, the job
that applies it to every report a scheduled run publishes, and the tool that sends the job back over
what it has already written.

The skill is the one copy of the standard. A member's agent reaches it with `load_skill` and writes
an entry in a reply; the job reads the same words as its system prompt and writes the stored entry
the portal's digest draws. Two callers, one set of rules."""

from pydantic import BaseModel, ConfigDict

from ufo.sdk.context import ExtensionContext
from ufo.sdk.jobs import JobSpec, owner_candidates
from ufo.sdk.manifest import Manifest, SkillSpec
from ufo.sdk.tools import (
    ActionPresentation,
    ObjectBinding,
    TextContent,
    ToolContext,
    ToolDef,
    ToolResult,
)
from ufo_ext_report_digest.digest import SKILL_DIR
from ufo_ext_report_digest.objects import REPORT_KIND, REPORT_OBJECT
from ufo_ext_report_digest.writer import DigestRebuild, DigestWriter, undigested_workspaces

NAME = "report_digest"
VERSION = "0.1.0"
JOB_NAME = "report_digest"

"""Every ten minutes. A report is read by a member the next time they open the page rather than the
second it lands, and a tighter interval buys nothing but a burstier spend."""
JOB_SCHEDULE = "0 */10 * * * *"

REBUILD_ADMIN_ONLY = "Only a workspace admin can write the radar entries again."
NOTHING_TO_REBUILD = "No report inside the last seven days carries an entry to write again."


async def write_digests(ctx: ExtensionContext) -> None:
    if ctx.model is None:
        raise RuntimeError("report_digest needs the background model; serve wires it")
    if ctx.member_context_blob is None:
        raise RuntimeError("report_digest needs blob access to read a published report")
    await DigestWriter(ctx=ctx, model=ctx.model, blob=ctx.member_context_blob).run()


class RebuildReportDigestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def rebuild_report_digest_handler(
    ctx: ToolContext, args: RebuildReportDigestInput
) -> ToolResult:
    """Make the reports inside the window due again and return. Nothing is written here: the job
    that owns this derived state reads the reports and writes each entry on its own interval, eight
    reports a tick."""
    if ctx.ext is None:
        raise RuntimeError("rebuild_report_digest dispatched without its ExtensionContext")
    if not await ctx.speaker_is_admin():
        raise ValueError(REBUILD_ADMIN_ONLY)
    due = await DigestRebuild(ctx=ctx.ext).run()
    if not due:
        return ToolResult(content=(TextContent(text=NOTHING_TO_REBUILD),))
    return ToolResult(
        content=(
            TextContent(
                text=(
                    f"The last seven days' entries are written again — {due} "
                    f"report{'' if due == 1 else 's'}. Each stands on its task's name until the "
                    "digest job reaches it."
                )
            ),
        )
    )


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        skills=(SkillSpec(path=SKILL_DIR),),
        tools=(
            ToolDef(
                name="rebuild_report_digest",
                description=(
                    "Write the radar's entries again, for a workspace admin who says the entries "
                    "under the reports read badly. It marks every report published in the last "
                    "seven days due and returns; the digest job reads each report again and writes "
                    "its entry, eight reports every ten minutes, and a report stands on its task's "
                    "name until its turn comes. A report older than seven days keeps the entry it "
                    "has — the job does not read that far back. Use it for the whole feed, never "
                    "to fix one entry."
                ),
                input_model=RebuildReportDigestInput,
                handler=rebuild_report_digest_handler,
                side_effecting=True,
                bound=ObjectBinding(kind=REPORT_KIND, binding="collection"),
                presentation=ActionPresentation(
                    label="Rebuild entries",
                    confirm="Every report of the last seven days has its entry written again.",
                    frame=True,
                ),
            ),
        ),
        jobs=(
            JobSpec(
                name=JOB_NAME,
                schedule=JOB_SCHEDULE,
                handler=write_digests,
                candidates=owner_candidates(undigested_workspaces),
            ),
        ),
        objects=(REPORT_OBJECT,),
        member_context_read=True,
    )
