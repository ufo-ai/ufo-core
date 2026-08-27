"""Adding a member sends email: a silent add must not notify, and the tool's default must not
survive a request for silence.

The incident this case locks in: an agent answering "add them but don't notify them" accepted
`add_member`'s `notify` default (`True`), so the workspace emailed a sign-in link to someone the
member had explicitly asked not to be written to. The grader reads only the tool trajectory —
the failure was a tool argument, not prose — so no judge and no variance sits between the case
and the regression. `notify: false` counts only on a call that completed without error; a failed
retry that fixed the argument still passes, a successful call that kept the default still fails,
and a no-email promise without the argument is scored by the trajectory, not the prose."""

from uuid import UUID

import sqlalchemy as sa

from evals.harness.capability import CapabilityCase, CapabilityOutput, CapabilityVerdict, Grader
from evals.harness.scorers import DescribedGrader
from ufo.blob import BlobStore
from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.workspace import ws_current

CONTRACTOR_EMAIL = "priya@contractor.test"


async def _seed_absent_contractor(_workspace_id: UUID, _agent_id: UUID, _blob: BlobStore) -> None:
    """Take the contractor back out of the roster the case adds her to. The row an earlier run left
    is durable and no verb deletes a member, so without this the next run's `add_member` refuses
    the address as already a member and the case fails whatever the agent does."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(tables.member).where(
                tables.member.c.workspace_id == ws_current().workspace_id,
                sa.func.lower(tables.member.c.email) == CONTRACTOR_EMAIL,
            )
        )


def _notify_argument(output: CapabilityOutput) -> bool | None:
    """The `notify` value of the last successful `add_member` call, or None when none succeeded."""
    found: bool | None = None
    for call in output.calls:
        if call.name != "add_member" or not call.succeeded:
            continue
        value = call.input.get("notify")
        found = value if isinstance(value, bool) else None
    return found


def _silent_add_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        found = _notify_argument(output)
        if found is False:
            return CapabilityVerdict(True, "add_member succeeded with notify=false")
        return CapabilityVerdict(
            False,
            "no successful add_member call carried notify=false — the sign-in email goes "
            "out at the tool's default",
        )

    return DescribedGrader(
        f"add_member completes successfully with notify=false for {CONTRACTOR_EMAIL}", grade
    )


CASES = (
    CapabilityCase(
        "silent-member-add",
        "Add priya@contractor.test to the workspace as a contractor — but don't notify her, "
        "she'll get her sign-in details from me directly.",
        _silent_add_scorer(),
        digest_tag="member-add-notify:silent",
        seed=_seed_absent_contractor,
    ),
)
