"""One attempt to reach one member, and what SES made of it.

The row is the claim. It is inserted before control is asked, on a key that can only be inserted
once — `(workspace, member, kind, reason)` — so a second pass, or a second process, sends
nothing. An attempt whose outcome the pass cannot decide is settled `failed` rather than left for
another pass to repeat: a duplicate lifecycle email is worse than a missing one, exactly as for a
campaign.

`reason` names the instance of the reason for the message — the credit a workspace had spent, the
enrollment a step belongs to — so a message is once-per-reason rather than once-ever.

Every statement filters `workspace_id` itself; `ExtensionContext.transaction` yields an unscoped
connection."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ufo.sdk.context import ExtensionContext
from ufo.sdk.email import EmailSends
from ufo.sdk.jobs import WorkspaceCandidates, owner_candidates
from ufo.sdk.o11y import log

ATTEMPTED = "attempted"
SENT = "sent"
FAILED = "failed"

FEEDBACK_WINDOW = timedelta(days=3)
"""How long a sent message is asked about. SES reports a delivery in seconds and a delayed bounce
in hours; past this nothing more is coming, and the workspace leaves the candidate set."""

ERROR_MAX_CHARS = 500

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


def unreported_workspaces() -> WorkspaceCandidates:
    """The workspaces holding a sent message SES has not reported on yet."""

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

    return owner_candidates(awaiting)


@dataclass(frozen=True)
class Sends:
    """The attempt ledger for the workspace the dispatcher bound."""

    ctx: ExtensionContext

    async def claim(self, member_id: UUID, kind: str, reason: str) -> bool:
        """Mark the attempt before making it, and answer whether this pass owns it."""
        async with self.ctx.transaction() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            claimed = (
                await connection.execute(
                    insert(lifecycle_send)
                    .values(
                        id=uuid4(),
                        workspace_id=self.ctx.workspace_id,
                        member_id=member_id,
                        kind=kind,
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

    async def sent(self, member_id: UUID, kind: str, reason: str, message_id: str) -> None:
        await self._settle(member_id, kind, reason, state=SENT, ses_message_id=message_id)

    async def failed(self, member_id: UUID, kind: str, reason: str, refusal: Exception) -> None:
        await self._settle(
            member_id, kind, reason, state=FAILED, last_error=str(refusal)[:ERROR_MAX_CHARS]
        )
        log("lifecycle_email.refused", kind=kind, error_class=type(refusal).__name__)

    async def record_deliveries(self, email: EmailSends) -> None:
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

    async def _settle(self, member_id: UUID, kind: str, reason: str, **values: str) -> None:
        async with self.ctx.transaction() as connection:
            await connection.execute(
                sa.update(lifecycle_send)
                .where(
                    lifecycle_send.c.workspace_id == self.ctx.workspace_id,
                    lifecycle_send.c.member_id == member_id,
                    lifecycle_send.c.kind == kind,
                    lifecycle_send.c.reason == reason,
                )
                .values(updated_at=sa.func.now(), **values)
            )
