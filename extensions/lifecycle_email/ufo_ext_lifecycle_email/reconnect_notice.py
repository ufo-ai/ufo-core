"""Tell someone, once per break, that a provider has stopped answering an account.

A stream parks when the provider refuses it. The connection row is untouched, so the workspace
looks healthy while it syncs nothing, and nobody finds out until they ask the agent for something it
can no longer read. That is the gap this closes.

Only a park a member can repair is one to tell them about. A throttle, a rate-limited org and a plan
gate park too and clear themselves on the next read — `parked_connections` answers with the first
kind alone, because a message naming a cause that is not real asks for an act that fixes nothing.

It reaches the member who owns the connection, because re-granting is theirs to do. A shared
connection nobody owns reaches the seated admins instead, who are the only people left who can.

Once per break, not once per hour: the row this writes is keyed by `parked_since`, the first
refusal still standing. A refused stream re-parks hourly and `parked_at` moves with it, so keying on
that would mail the owner every hour until they fixed it. An account fixed and broken again is a new
break and a new message."""

from dataclasses import dataclass
from datetime import UTC
from uuid import UUID

import sqlalchemy as sa

from ufo.sdk.context import ExtensionContext
from ufo.sdk.email import TRANSACTIONAL, EmailRefused, EmailSends, EmailUnanswered
from ufo.sdk.grants import (
    CREDENTIALS_SCREEN_FRAGMENT,
    ParkedConnection,
    parked_breaks,
    parked_connections,
)
from ufo.sdk.jobs import WorkspaceCandidates, owner_candidates
from ufo.sdk.seats import SeatEntry, Seats, workspace_domain
from ufo_ext_lifecycle_email.sends import Sends, lifecycle_send, unreported_workspaces

ACCOUNT_PARKED = "account_parked"

UNNAMED_WORKSPACE = "This workspace"

SUBJECT = "{workspace} cannot reach {account}"
BODY = (
    "{account} has stopped syncing to {workspace}. The account is still connected; the access it "
    "was granted is not.\n\n"
    "Open the workspace and ask the agent to connect it again."
)
ACTION_LABEL = "Open the workspace"


def reconnect_workspaces() -> WorkspaceCandidates:
    """Where this job has work: a workspace holding a parked stream nobody has been told about, and
    a workspace holding a sent message SES has not reported on yet.

    The ledger is what takes the first set back down. Only a granted account clears
    `parked_awaits_grant`, and an account nobody repairs never grants one, so a workspace with a
    broken stream would otherwise be bound and read four times a minute for as long as it stayed
    broken — and the per-minute fan-out would grow with every break that goes unfixed. A send made
    for this connection at or after the break began is the whole of the test: the reason a claim is
    keyed by opens with the connection and closes with the break, so a row carrying that connection
    and younger than the break was written for this one. A workspace holds several connections and
    they break separately, so a test that read the workspace alone would take the message sent for
    one account as proof that the other was told. The connection is matched as `UUID.hex` spells
    it, because Postgres renders a uuid hyphenated and SQLite renders the same column as the 32 hex
    characters it stores."""

    def untold() -> sa.Select[tuple[UUID]]:
        parked = parked_breaks().subquery()
        told = sa.select(sa.literal(1)).where(
            lifecycle_send.c.workspace_id == parked.c.workspace_id,
            lifecycle_send.c.kind == ACCOUNT_PARKED,
            lifecycle_send.c.reason.startswith(
                sa.func.replace(sa.cast(parked.c.connection_id, sa.Text), "-", "")
            ),
            lifecycle_send.c.created_at >= parked.c.parked_since,
        )
        return sa.select(parked.c.workspace_id).where(~sa.exists(told)).distinct()

    parked = owner_candidates(untold)
    unreported = unreported_workspaces()

    async def candidates() -> tuple[UUID, ...]:
        return tuple({*await parked(), *await unreported()})

    return candidates


@dataclass(frozen=True)
class ReconnectNotice:
    """One pass over the bound workspace: tell whoever can fix each broken account, then record
    what SES has since reported about the messages already sent."""

    ctx: ExtensionContext

    async def run(self) -> None:
        email = self.ctx.email
        if email is None:
            raise RuntimeError("lifecycle_email needs the deploy's send seam and holds none")
        url = self.ctx.home_url(CREDENTIALS_SCREEN_FRAGMENT)
        if url is not None:
            await self._notify(email, url)
        await Sends(ctx=self.ctx).record_deliveries(email)

    async def _notify(self, email: EmailSends, url: str) -> None:
        async with self.ctx.transaction() as connection:
            broken = await parked_connections(connection, self.ctx.workspace_id)
            if not broken:
                return
            workspace = await workspace_domain(connection, self.ctx.workspace_id)
            snapshot = await Seats(self.ctx.workspace_id).snapshot(connection)
        seated = {member.id: member for member in snapshot.members if member.seated}
        admins = tuple(member for member in seated.values() if member.admin)
        sends = Sends(ctx=self.ctx)
        for account in broken:
            for member in _told(account, seated, admins):
                await self._send(email, sends, member, account, workspace or UNNAMED_WORKSPACE, url)

    async def _send(
        self,
        email: EmailSends,
        sends: Sends,
        member: SeatEntry,
        account: ParkedConnection,
        workspace: str,
        url: str,
    ) -> None:
        reason = f"{account.connection_id.hex}:{account.parked_since.astimezone(UTC).isoformat()}"
        if not await sends.claim(member.id, ACCOUNT_PARKED, reason):
            return
        named = account.account_id or account.provider
        try:
            message_id = await email.send(
                address=member.email,
                kind=ACCOUNT_PARKED,
                topic=TRANSACTIONAL,
                subject=SUBJECT.format(workspace=workspace, account=named),
                body=BODY.format(workspace=workspace, account=named),
                action_label=ACTION_LABEL,
                action_url=url,
            )
        except (EmailRefused, EmailUnanswered) as refusal:
            await sends.failed(member.id, ACCOUNT_PARKED, reason, refusal)
            return
        await sends.sent(member.id, ACCOUNT_PARKED, reason, message_id)


def _told(
    account: ParkedConnection,
    seated: dict[UUID, SeatEntry],
    admins: tuple[SeatEntry, ...],
) -> tuple[SeatEntry, ...]:
    """The owner, or the seated admins where nobody owns the connection. An owner who has left the
    workspace is nobody, so their broken account reaches the admins too."""
    owner = seated.get(account.owner_member_id) if account.owner_member_id else None
    return (owner,) if owner is not None else admins
