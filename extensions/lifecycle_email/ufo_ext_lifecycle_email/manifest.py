"""What the lifecycle-email extension declares: one per-minute job that tells a workspace's admins
their credit has run out, and records what SES made of each message.

It declares no hook and no tool. A lifecycle message is a reaction to a state, and every state one
reacts to is already a row — so a sweep reads it, which is also the standing rule that
batch-at-interval is the default. Nothing here reaches a member through a turn: the balance gate
refuses the act that would fix billing, so a notice about it must not need one.

One flag decides whether this deploy sends at all. Every job reads it and does nothing while it is
off — no rows are written, so turning it on starts from the fleet as it stands rather than sending
a backlog of what was missed."""

from ufo.sdk.context import ExtensionContext
from ufo.sdk.flags import flag_enabled
from ufo.sdk.jobs import JobSpec
from ufo.sdk.manifest import FlagSpec, Manifest
from ufo_ext_lifecycle_email.balance_notice import BalanceNotice, notice_workspaces

NAME = "lifecycle_email"
VERSION = "0.1.0"
NOTICE_JOB = "balance_notice"
NOTICE_SCHEDULE = "0 * * * * *"
SENDING_FLAG = "enable-lifecycle-email"


async def _notice(ctx: ExtensionContext) -> None:
    if not await flag_enabled(SENDING_FLAG, default=False):
        return
    await BalanceNotice(ctx=ctx).run()


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
                schedule=NOTICE_SCHEDULE,
                handler=_notice,
                candidates=notice_workspaces(),
            ),
        ),
    )
