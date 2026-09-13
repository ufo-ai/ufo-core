"""The inbox drain: each lane's open notifications, folded into one turn on that lane.

It runs as the extension's recurring job, so it fires on the clock and never on the rows it writes.
Each tick claims a lane's open rows under a lease and admits one turn on the lane's own conversation
— opened on the first batch, the way a source trigger's `current` delivery wakes the conversation
that watches it, and reused for every batch after — carrying the whole batch inline as data. A lane
a drain turn read inside the cooldown is skipped, its rows left open for the next tick: that is the
bound on a woken turn's inbox waking it again.

The invoke comes first and the triage mark second: a crash between the two re-posts under the same
idempotency key, which admits nothing, and a lease that lapses hands the rows to the next tick. The
key is the batch as read — every row id with its occurrence count — so a row that folded since it
was read is a different batch and its retry admits a turn carrying the folded body, rather than
spending a key already spent and closing the row unread. The
turn carries the member's own authority, so an unseated member's batch parks rather than becoming
workspace work, and `holds_work_already_done` parks it on a spend breach rather than discarding the
notifications already raised. Only the lanes of the agent this extension provisioned are woken: a
row addressed to any other agent is nobody's to run a turn on under a member's authority, so it
stays where it is."""

import hashlib
from contextlib import suppress
from dataclasses import dataclass

from ufo.sdk.authority import authority_from_member_id
from ufo.sdk.context import AgentArchived, ExtensionContext
from ufo.sdk.flags import flag_enabled
from ufo.sdk.untrusted import wall
from ufo_ext_app_notification.store import (
    NOTIFICATION_FLAG,
    NOTIFICATION_KIND,
    Lane,
    Notification,
    NotificationStore,
    inbox_agent_id,
)

DRAIN_COOLDOWN_SECONDS = 300
DRAIN_CLAIM_LEASE_SECONDS = 300
DRAIN_BATCH = 25
LANE_KEY = "member:{member}"
DRAIN_KEY = "notify-drain:{agent}:{member}:{batch}"
BATCH_OPEN = '<notifications count="{count}">'
BATCH_CLOSE = "</notifications>"
BATCH_CLOSE_ESCAPE = "&lt;/notifications&gt;"
BODY_SOURCE = "notification:{name}"


@dataclass(frozen=True)
class InboxDrain:
    ctx: ExtensionContext

    async def run(self) -> None:
        """One tick over the bound workspace. The flag that offers `notify` and `deliver` is read
        here too, for the one actor that holds no tool — the clock — so an environment that
        withholds the feature wakes nobody; it reads closed where nothing answers, and a stack that
        wants the feature without a flag service selects the `open` backend."""
        if not await flag_enabled(NOTIFICATION_FLAG, default=False):
            return
        inbox = await inbox_agent_id(self.ctx)
        if inbox is None:
            return
        store = NotificationStore(self.ctx)
        for lane in await store.lanes_with_untriaged(DRAIN_COOLDOWN_SECONDS):
            if lane.agent_id != inbox:
                continue
            batch = await store.claim(lane, DRAIN_BATCH, DRAIN_CLAIM_LEASE_SECONDS)
            if not batch:
                continue
            await self._wake(store, lane, batch)

    async def _wake(
        self, store: NotificationStore, lane: Lane, batch: tuple[Notification, ...]
    ) -> None:
        conversation_id = await self.ctx.open_conversation(
            lane.agent_id, LANE_KEY.format(member=lane.member_id.hex), member_id=lane.member_id
        )
        read = hashlib.sha256()
        for row in batch:
            read.update(f"{row.id.hex}:{row.occurrences}\n".encode())
        with suppress(AgentArchived):
            turn_id = await self.ctx.invoke(
                conversation_id,
                lane.agent_id,
                drain_message(batch),
                DRAIN_KEY.format(
                    agent=lane.agent_id.hex,
                    member=lane.member_id.hex,
                    batch=read.hexdigest()[:32],
                ),
                authority=authority_from_member_id(lane.member_id),
                holds_work_already_done=True,
                standalone=True,
                runtime_config=lane.runtime_config,
            )
            if turn_id is not None:
                await store.mark_triaged(batch, turn_id)


def drain_message(batch: tuple[Notification, ...]) -> str:
    """The batch as the triage turn reads it: one block per row naming its ref, subject, count and
    producer, with the body walled as data — it is another agent's words about content it read, so
    nothing in it can close the block it is held in and continue as instructions."""
    lines = [BATCH_OPEN.format(count=len(batch))]
    for row in batch:
        lines.append(
            f"- ref: {NOTIFICATION_KIND}/{row.name}\n"
            f"  subject: {row.subject}\n"
            f"  raised: {row.occurrences} time(s) by {row.produced_by_agent_name}, "
            f"first {row.created_at.isoformat()}, last {row.last_raised_at.isoformat()}\n"
            + wall(BODY_SOURCE.format(name=row.name), row.body)
        )
    return "\n".join(lines).replace(BATCH_CLOSE, BATCH_CLOSE_ESCAPE) + f"\n{BATCH_CLOSE}"
