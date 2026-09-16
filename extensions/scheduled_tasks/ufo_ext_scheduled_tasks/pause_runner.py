"""The pause runner: fires every due pause once, and settles it either way.

It runs as a recurring job, so it fires on the clock. Each tick claims the due pauses under a lease
(an overlapping tick never fires one twice) and invokes each one's stored resume body as a scheduled
turn under the arming turn's internet ceiling — guarded by the two watermarks the arm recorded,
which ask admission under the conversation lock whether a member has spoken since the wait began.

That guard is the whole convergence. Answered with a turn, the timer resumed the workflow. Answered
`None`, a member already did, and there is nothing left to resume — a member's message and the timer
were always two ways for one wait to end, and this is where they meet. The row retires on either
answer, because either answer ends the wait. An archived app is the one answer that ends nothing:
it admits no turn at all, so the pause stays where it is and the restore serves it.

Invoke first, retire second: a crash between the two re-fires under the same idempotency key, which
admits the turn already admitted rather than a second one. A tick with failures raises their names.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from ufo.sdk.context import AgentArchived, ExtensionContext, TurnRuntimeConfig
from ufo_ext_scheduled_tasks.pauses import Pause, PauseStore

CLAIM_LEASE_SECONDS = 300
FIRE_KEY_PREFIX = "pause-fired:"


@dataclass(frozen=True)
class PauseRunner:
    ctx: ExtensionContext
    lease_seconds: int = CLAIM_LEASE_SECONDS

    async def run(self) -> None:
        store = PauseStore(self.ctx)
        failures: list[str] = []
        for row in await store.claim_due(datetime.now(UTC), self.lease_seconds):
            try:
                await self._fire(store, row)
            except Exception as raised:
                failures.append(f"{row.conversation_id} ({type(raised).__name__})")
        if failures:
            raise RuntimeError("pause fires failed: " + ", ".join(failures))

    async def _fire(self, store: PauseStore, row: Pause) -> None:
        if not await store.claim_holds(row):
            return
        try:
            await self.ctx.invoke(
                row.conversation_id,
                row.agent_id,
                row.prompt,
                f"{FIRE_KEY_PREFIX}{row.id}",
                as_scheduled=True,
                unless_member_since=row.origin_seq,
                unless_member_arrival_since=row.origin_arrival_seq,
                runtime_config=TurnRuntimeConfig(internet_access=row.internet_access),
                acting_member_id=row.created_by_member_id,
            )
        except AgentArchived:
            return
        await store.retire(row)
