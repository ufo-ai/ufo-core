"""What the lifecycle-email extension declares: four per-minute jobs.

A lifecycle message is a reaction to a state, and every state it reacts to is already a row — core's
members, core's connections, a workspace balance. So a job reads them on a clock, which is the
standing rule that batch-at-interval is the default. No hook and no new event: nothing here has to
be caught as it happens.

Four jobs because they are four failure domains, and each names the workspaces it has work in:

  lifecycle_events     writes events — a member was invited, a member has connected nothing
  lifecycle_reconcile  reads events nothing has looked at yet, and starts the sequences that match
  lifecycle_runner     sends the steps that are due
  balance_notice       reads the balance row and sends; no events, no sequences

A producer only writes an instant; one job decides what measures from it. That is what lets a
sequence an operator approved this morning reach the events written since the last pass, and what
keeps a producer from knowing whether a sequence lives in the tree or in a row.

One flag decides whether this deploy sends at all. Every job reads it and does nothing while it is
off — no rows are written, so turning it on starts from the fleet as it stands rather than sending
a backlog of what was missed."""

from ufo.sdk.context import ExtensionContext
from ufo.sdk.flags import flag_enabled
from ufo.sdk.jobs import JobSpec
from ufo.sdk.manifest import FlagSpec, Manifest
from ufo_ext_lifecycle_email.balance_notice import BalanceNotice, notice_workspaces
from ufo_ext_lifecycle_email.enrollments import (
    due_enrollment_workspaces,
    unread_event_workspaces,
)
from ufo_ext_lifecycle_email.events import WriteEvents, event_workspaces
from ufo_ext_lifecycle_email.runner import Reconciling, SequenceRunner

NAME = "lifecycle_email"
VERSION = "0.4.0"
NOTICE_JOB = "balance_notice"
EVENTS_JOB = "lifecycle_events"
RECONCILE_JOB = "lifecycle_reconcile"
RUNNER_JOB = "lifecycle_runner"
SCHEDULE = "0 * * * * *"
SENDING_FLAG = "enable-lifecycle-email"


async def _notice(ctx: ExtensionContext) -> None:
    if not await flag_enabled(SENDING_FLAG, default=False):
        return
    await BalanceNotice(ctx=ctx).run()


async def _events(ctx: ExtensionContext) -> None:
    if not await flag_enabled(SENDING_FLAG, default=False):
        return
    await WriteEvents(ctx=ctx).run()


async def _reconcile(ctx: ExtensionContext) -> None:
    if not await flag_enabled(SENDING_FLAG, default=False):
        return
    await Reconciling(ctx=ctx).run()


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
                name=EVENTS_JOB,
                schedule=SCHEDULE,
                handler=_events,
                candidates=event_workspaces(),
            ),
            JobSpec(
                name=RECONCILE_JOB,
                schedule=SCHEDULE,
                handler=_reconcile,
                candidates=unread_event_workspaces(),
            ),
            JobSpec(
                name=RUNNER_JOB,
                schedule=SCHEDULE,
                handler=_run,
                candidates=due_enrollment_workspaces(),
            ),
        ),
    )
