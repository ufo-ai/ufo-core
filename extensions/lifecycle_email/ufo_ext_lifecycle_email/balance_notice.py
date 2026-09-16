"""Tell a workspace's admins, once per exhaustion, that its credit has run out.

No new hook event stands behind it — a spent balance is a row, so a per-minute sweep reads the same
truth an event would carry, and batch-at-interval is the standing rule.

Recovery must never need a turn. The balance gate refuses the very act that would fix billing, so
this message exists to reach an admin off the turn path, and it carries the billing screen's own
link. A deploy with no such screen sends nothing: a notice nobody can act on is a line that does
not ship.

Once per exhaustion, not once per minute: the attempt is keyed by the total credit the workspace
has ever been granted, so an admin is told once for the balance they spent and again only after a
further grant has been spent too. The total is the key rather than the last credit's stamp because
two credits inside one second carry one stamp and would read as one exhaustion.

What decides whether the agent has stopped is the gate, not the balance line. A workspace holding
its own key for a model keeps running every turn that model serves however little prepaid credit is
left, so the pass asks `spend_admitted` — the question `BalanceGate.admits` answers — and tells such
a workspace nothing."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa

from ufo.sdk.balance import (
    BILLING_SCREEN_FRAGMENT,
    read_balance,
    spend_admitted,
    unfunded_balances,
)
from ufo.sdk.context import ExtensionContext
from ufo.sdk.email import TRANSACTIONAL, EmailRefused, EmailSends, EmailUnanswered
from ufo.sdk.jobs import WorkspaceCandidates, owner_candidates
from ufo.sdk.seats import Seats, workspace_domain
from ufo_ext_lifecycle_email.sends import Sends, lifecycle_send, unreported_workspaces

BALANCE_EXHAUSTED = "balance_exhausted"

UNNAMED_WORKSPACE = "This workspace"
SUBJECT = "{workspace} is out of credit"
BODY = (
    "{workspace} has no credit left, so the agent has stopped answering. It starts again as soon "
    "as credit is added."
)
ACTION_LABEL = "Add credit"


def notice_workspaces() -> WorkspaceCandidates:
    """Where this job has work: a workspace whose balance has reached the line and whose admins
    have not been told about that exhaustion, and a workspace holding a sent message SES has not
    reported on yet. The two sets are read as one, so a topped-up workspace still has its last
    notice's delivery recorded.

    The ledger is what takes the first set back down. A workspace that has spent its credit and
    never tops up stays below the line for as long as it exists, so without the join on the grant
    total that names the exhaustion it would be bound and read four times a minute forever, and the
    per-minute fan-out would grow with every workspace that ever spent out. One row for this
    exhaustion is the whole of it: the ledger settles an attempt it cannot decide rather than
    leaving it for another pass, so a claim already made is never work this job still owes.

    A workspace paying its own way for a model is still here — the slots that exemption tests are
    the process's, not a query's. `BalanceNotice._notify` answers the gate before it reads anything
    else, so such a workspace costs one statement a tick and is told nothing."""

    def untold() -> sa.Select[tuple[UUID]]:
        unfunded = unfunded_balances().subquery()
        told = sa.select(sa.literal(1)).where(
            lifecycle_send.c.workspace_id == unfunded.c.workspace_id,
            lifecycle_send.c.kind == BALANCE_EXHAUSTED,
            lifecycle_send.c.reason == sa.cast(unfunded.c.granted_micro_usd, sa.Text),
        )
        return sa.select(unfunded.c.workspace_id).where(~sa.exists(told))

    spent = owner_candidates(untold)
    unreported = unreported_workspaces()

    async def candidates() -> tuple[UUID, ...]:
        return tuple({*await spent(), *await unreported()})

    return candidates


@dataclass(frozen=True)
class BalanceNotice:
    """One pass over the bound workspace: tell the admins who have not been told, then record what
    SES has since reported about the messages already sent."""

    ctx: ExtensionContext

    async def run(self) -> None:
        email = self.ctx.email
        if email is None:
            raise RuntimeError("lifecycle_email needs the deploy's send seam and holds none")
        url = self.ctx.home_url(BILLING_SCREEN_FRAGMENT)
        if url is not None:
            await self._notify(email, url)
        await Sends(ctx=self.ctx).record_deliveries(email)

    async def _notify(self, email: EmailSends, url: str) -> None:
        async with self.ctx.transaction() as connection:
            if await spend_admitted(connection, self.ctx.workspace_id, self.ctx.own_key_slots):
                return
            balance = await read_balance(connection, self.ctx.workspace_id)
            if balance is None:
                return
            reason = str(balance.granted_micro_usd)
            workspace = await workspace_domain(connection, self.ctx.workspace_id)
            snapshot = await Seats(self.ctx.workspace_id).snapshot(connection)
        named = workspace or UNNAMED_WORKSPACE
        sends = Sends(ctx=self.ctx)
        for member in snapshot.members:
            if not (member.seated and member.admin):
                continue
            if not await sends.claim(member.id, BALANCE_EXHAUSTED, reason):
                continue
            try:
                message_id = await email.send(
                    address=member.email,
                    kind=BALANCE_EXHAUSTED,
                    topic=TRANSACTIONAL,
                    subject=SUBJECT.format(workspace=named),
                    body=BODY.format(workspace=named),
                    action_label=ACTION_LABEL,
                    action_url=url,
                )
            except (EmailRefused, EmailUnanswered) as refusal:
                await sends.failed(member.id, BALANCE_EXHAUSTED, reason, refusal)
                continue
            await sends.sent(member.id, BALANCE_EXHAUSTED, reason, message_id)
