"""One reader pays for a ranking read where it is read; every other reader takes what stands.

A pane re-reads on its interval, a second tab is ordinary, and a reload asks again — so without a
claim a member pays several times over for one answer. The claim is the row that says a reader is
already on it; the cooldown is the row that says the last attempt failed, so the next read answers
what stands rather than walking into the same wall.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from ufo.sdk.context import JsonValue, ScopedStore

CLAIM_LEASE = timedelta(minutes=2)
COOLDOWN_AFTER_FAILURE = timedelta(minutes=15)


def stamped(held: object, key: str) -> datetime | None:
    """The moment a stamp records, or None where it records nothing readable. A value written by an
    older shape, or half-written, reads as absent rather than raising — the caller's answer to that
    is to do the work again, which is always safe here."""
    if not isinstance(held, dict) or not isinstance(held.get(key), str):
        return None
    try:
        return datetime.fromisoformat(held[key])
    except ValueError:
        return None


@dataclass(frozen=True)
class Claim:
    """The two rows that decide whether this read is the one that generates."""

    store: ScopedStore
    claim: str
    cooldown: str

    async def take(self, now: datetime) -> bool:
        """Whether this reader generates: not while a failure is still cooling off, and not while
        another reader holds the claim.

        `put_if` with `expected=None` inserts only where no claim stands. A claim left behind by a
        reader that died is taken over once it is older than `CLAIM_LEASE`, by comparing against the
        exact value read — there is no primitive that displaces a live row, so the stale value is
        the token."""
        cooled = await self.store.get(self.cooldown)
        failed = stamped(cooled, "failed_at")
        if failed is not None and now - failed < COOLDOWN_AFTER_FAILURE:
            return False
        mine: JsonValue = {"claimed_at": now.isoformat()}
        if await self.store.put_if(self.claim, mine, expected=None):
            return True
        standing = await self.store.get(self.claim)
        claimed = stamped(standing, "claimed_at")
        if claimed is not None and now - claimed < CLAIM_LEASE:
            return False
        return await self.store.put_if(self.claim, mine, expected=standing)

    async def release(self) -> None:
        await self.store.delete(self.claim)

    async def cool(self, now: datetime) -> None:
        stamp: JsonValue = {"failed_at": now.isoformat()}
        await self.store.put(self.cooldown, stamp)
