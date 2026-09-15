"""Tell a workspace's admins, once per exhaustion, that its credit has run out.

The shape every lifecycle message takes: one person, because of a state they are in, reached by
email through the one seam core holds. No new hook event stands behind it — a spent balance is a
row, so a per-minute sweep reads the same truth an event would carry, and batch-at-interval is the
standing rule.

Recovery must never need a turn. The balance gate refuses the very act that would fix billing, so
this message exists to reach an admin off the turn path, and it carries the billing screen's own
link. A deploy with no such screen sends nothing: a notice nobody can act on is a line that does
not ship.

Once per exhaustion, not once per minute: the row this writes is keyed by the total credit the
workspace has ever been granted, so an admin is told once for the balance they spent and again only
after a further grant has been spent too. The total is the key rather than the last credit's stamp
because two credits inside one second carry one stamp and would read as one exhaustion.

What decides whether the agent has stopped is the gate, not the balance line. A workspace holding
its own key for a model keeps running every turn that model serves however little prepaid credit is
left, so the pass asks `spend_admitted` — the question `BalanceGate.admits` answers — and tells such
a workspace nothing."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ufo.sdk.balance import (
    BILLING_SCREEN_FRAGMENT,
    read_balance,
    spend_admitted,
    unfunded_balances,
)
from ufo.sdk.context import ExtensionContext
from ufo.sdk.email import EmailRefused, EmailSends, EmailUnanswered
from ufo.sdk.jobs import WorkspaceCandidates, owner_candidates
from ufo.sdk.o11y import log
from ufo.sdk.seats import Seats, workspace_domain

BALANCE_EXHAUSTED = "balance_exhausted"

ATTEMPTED = "attempted"
SENT = "sent"
FAILED = "failed"

FEEDBACK_WINDOW = timedelta(days=3)
"""How long a sent message is asked about. SES reports a delivery in seconds and a delayed bounce
in hours; past this nothing more is coming, and the workspace leaves the candidate set."""

ERROR_MAX_CHARS = 500
UNNAMED_WORKSPACE = "This workspace"

SUBJECT = "{workspace} is out of credit"
BODY = (
    "{workspace} has no credit left, so the agent has stopped answering. It starts again as soon "
    "as credit is added."
)
ACTION_LABEL = "Add credit"

_metadata = sa.MetaData()
lifecycle_send = sa.Table(
    "lifecycle_send",
    _metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("member_id", sa.Uuid, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("ses_message_id", sa.Text, nullable=True),
    sa.Column("delivery", sa.Text, nullable=True),
    sa.Column("last_error", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)


def notice_workspaces() -> WorkspaceCandidates:
    """Where this job has work: a workspace whose balance has reached the line and whose admins
    have not been told about that exhaustion, and a workspace holding a sent message SES has not
    reported on yet. The two sets are read as one, so a topped-up workspace still has its last
    notice's delivery recorded.

    The ledger is what takes the first set back down. A workspace that has spent its credit and
    never tops up stays below the line for as long as it exists, so without the join on the grant
    total that names the exhaustion it would be bound and read four times a minute forever, and the
    per-minute fan-out would grow with every workspace that ever spent out. One row for this
    exhaustion is the whole of it: an attempt this pass cannot decide is settled rather than left
    for another pass, so a claim already made is never work this job still owes.

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

    def awaiting() -> sa.Select[tuple[UUID]]:
        return (
            sa.select(lifecycle_send.c.workspace_id)
            .where(
                lifecycle_send.c.state == SENT,
                lifecycle_send.c.delivery.is_(None),
                lifecycle_send.c.created_at > datetime.now(UTC) - FEEDBACK_WINDOW,
            )
            .distinct()
        )

    unreported = owner_candidates(awaiting)

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
        await self._record_deliveries(email)

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
        for member in snapshot.members:
            if not (member.seated and member.admin):
                continue
            if not await self._claim(member.id, reason):
                continue
            await self._send(
                email, member.id, member.email, reason, workspace or UNNAMED_WORKSPACE, url
            )

    async def _claim(self, member_id: UUID, reason: str) -> bool:
        """Mark the attempt before making it, and answer whether this pass owns it. The unique key
        is the claim: a second pass, or a second process, inserts nothing and sends nothing. A
        message whose outcome this pass cannot decide stays `attempted` and is never repeated — a
        duplicate lifecycle email is worse than a missing one."""
        async with self.ctx.transaction() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            claimed = (
                await connection.execute(
                    insert(lifecycle_send)
                    .values(
                        id=uuid4(),
                        workspace_id=self.ctx.workspace_id,
                        member_id=member_id,
                        kind=BALANCE_EXHAUSTED,
                        reason=reason,
                        state=ATTEMPTED,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                    .on_conflict_do_nothing(
                        index_elements=(
                            lifecycle_send.c.workspace_id,
                            lifecycle_send.c.member_id,
                            lifecycle_send.c.kind,
                            lifecycle_send.c.reason,
                        )
                    )
                    .returning(lifecycle_send.c.id)
                )
            ).one_or_none()
        return claimed is not None

    async def _send(
        self,
        email: EmailSends,
        member_id: UUID,
        address: str,
        reason: str,
        workspace: str,
        url: str,
    ) -> None:
        try:
            message_id = await email.send(
                address=address,
                kind=BALANCE_EXHAUSTED,
                subject=SUBJECT.format(workspace=workspace),
                body=BODY.format(workspace=workspace),
                action_label=ACTION_LABEL,
                action_url=url,
            )
        except (EmailRefused, EmailUnanswered) as refusal:
            await self._settle(
                member_id, reason, state=FAILED, last_error=str(refusal)[:ERROR_MAX_CHARS]
            )
            log(
                "lifecycle_email.refused",
                kind=BALANCE_EXHAUSTED,
                error_class=type(refusal).__name__,
            )
            return
        await self._settle(member_id, reason, state=SENT, ses_message_id=message_id)

    async def _settle(self, member_id: UUID, reason: str, **values: str) -> None:
        async with self.ctx.transaction() as connection:
            await connection.execute(
                sa.update(lifecycle_send)
                .where(
                    lifecycle_send.c.workspace_id == self.ctx.workspace_id,
                    lifecycle_send.c.member_id == member_id,
                    lifecycle_send.c.kind == BALANCE_EXHAUSTED,
                    lifecycle_send.c.reason == reason,
                )
                .values(updated_at=sa.func.now(), **values)
            )

    async def _record_deliveries(self, email: EmailSends) -> None:
        """What SES has reported about this workspace's sent messages, read back through the
        consumer that reports a campaign's. A message nothing has been reported about is left
        alone: feedback arrives on its own clock, and silence is not a failure."""
        async with self.ctx.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(lifecycle_send.c.id, lifecycle_send.c.ses_message_id).where(
                        lifecycle_send.c.workspace_id == self.ctx.workspace_id,
                        lifecycle_send.c.state == SENT,
                        lifecycle_send.c.delivery.is_(None),
                        lifecycle_send.c.created_at > datetime.now(UTC) - FEEDBACK_WINDOW,
                    )
                )
            ).all()
        for row in rows:
            delivery = await email.delivery(row.ses_message_id)
            if delivery is None:
                continue
            async with self.ctx.transaction() as connection:
                await connection.execute(
                    sa.update(lifecycle_send)
                    .where(lifecycle_send.c.id == row.id)
                    .values(delivery=delivery, updated_at=sa.func.now())
                )
