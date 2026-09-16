"""What the lifecycle-email extension declares: three per-minute jobs, and nothing else.

No hook and no tool. A lifecycle message is a reaction to a state, and every state one reacts to is
already a row — so a sweep reads it, which is also the standing rule that batch-at-interval is the
default. Nothing here reaches a member through a turn: the balance gate refuses the act that would
fix billing, so a notice about it must not need one.

Three jobs rather than one because they are three failure domains. A sweep that cannot read the
rows it enrolls from must not hold back a message already due, and a notice that fires off a state
is not the runner that fires off a clock.

One flag decides whether this deploy sends at all. Every job reads it and does nothing while it is
off — no rows are written, so turning it on starts from the fleet as it stands rather than sending
a backlog of what was missed."""

from ufo.sdk.context import ExtensionContext
from ufo.sdk.flags import flag_enabled
from ufo.sdk.jobs import JobSpec
from ufo.sdk.manifest import FlagSpec, Manifest
from ufo.sdk.seats import recently_invited_workspaces
from ufo_ext_lifecycle_email.balance_notice import BalanceNotice, notice_workspaces
from ufo_ext_lifecycle_email.enrollments import due_enrollment_workspaces
from ufo_ext_lifecycle_email.runner import Enrolling, SequenceRunner
from ufo_ext_lifecycle_email.sequences import INVITATION_WINDOW

NAME = "lifecycle_email"
VERSION = "0.2.0"
NOTICE_JOB = "balance_notice"
ENROLL_JOB = "lifecycle_enroll"
RUNNER_JOB = "lifecycle_runner"
SCHEDULE = "0 * * * * *"
SENDING_FLAG = "enable-lifecycle-email"


async def _notice(ctx: ExtensionContext) -> None:
    if not await flag_enabled(SENDING_FLAG, default=False):
        return
    await BalanceNotice(ctx=ctx).run()


async def _enroll(ctx: ExtensionContext) -> None:
    if not await flag_enabled(SENDING_FLAG, default=False):
        return
    await Enrolling(ctx=ctx).run()


async def _run(ctx: ExtensionContext) -> None:
    if not await flag_enabled(SENDING_FLAG, default=False):
        return
    await SequenceRunner(ctx=ctx).run()


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        flags=(
            FlagSpec(
                key=SENDING_FLAG,
                what="ufo sends lifecycle email — balance notices and sequences.",
            ),
        ),
        jobs=(
            JobSpec(
                name=NOTICE_JOB,
                schedule=SCHEDULE,
                handler=_notice,
                candidates=notice_workspaces(),
            ),
            JobSpec(
                name=ENROLL_JOB,
                schedule=SCHEDULE,
                handler=_enroll,
                candidates=recently_invited_workspaces(INVITATION_WINDOW),
            ),
            JobSpec(
                name=RUNNER_JOB,
                schedule=SCHEDULE,
                handler=_run,
                candidates=due_enrollment_workspaces(),
            ),
        ),
    )
