"""The start screen's per-member slate: the rows a member reads before they have asked anything.

Generated where it is read, for the member who is there, and cached for `STARTERS_TTL`. Nothing
generates on a clock: a slate nobody opens is never made, and memory housekeeping rewriting rows
behind the scenes costs nothing. The cache turns over on time alone, because time and the ranking
instructions are the only things that can make a stored slate wrong — whether a ranked row is an
application the member can build now or an unlock still short of an account is decided at read time
by `fill_starters` against live connections, so connecting an account moves a row with no new slate.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from ufo.sdk.context import JsonValue, ScopedStore
from ufo.sdk.models import Message, ModelRequest, ToolSchema, ToolUseBlock
from ufo.sdk.o11y import warn
from ufo.sdk.subjects import member_subject
from ufo.sdk.surfaces import SurfaceModel
from ufo_ext_web.panels import UNLOCKS, UNLOCKS_BY_NAME

STARTERS_KEY_PREFIX = "starters"
CLAIM_KEY_PREFIX = "starters-claim"
COOLDOWN_KEY_PREFIX = "starters-cooldown"

STARTERS_TTL = timedelta(minutes=30)
CLAIM_LEASE = timedelta(minutes=2)
COOLDOWN_AFTER_FAILURE = timedelta(minutes=15)

MEMORY_LIMIT = 60
MEMORY_TEXT_CHARS = 400
RANKED_MAX = 8
TITLE_CHARS = 20
BODY_CHARS = 68
ASK_CHARS = 320
SLATE_MAX_TOKENS = 4096
SLATE_TOOL = "record_slate"
SLATE_TOOL_DESCRIPTION = "Record the ranked starters for this member."

SLATE_SYSTEM = f"""\
You rank the start screen of a work assistant for one member. The screen shows three rows they can \
press, and each row says one sentence on their behalf.

You are given what this member's memory says about their company and their work, the applications \
the workspace already has, and a catalog of applications the product knows how to build.

Rank the catalog rows that would help this member most. Rank a row for the work the memory shows \
them doing, never because its accounts look popular. Rank no row whose job an existing application \
already does. Rank six to eight, best first. The screen draws only a few of \
them and drops any whose accounts are missing, so a short list leaves it with nothing to show.

For each ranked row write:
- title: the application's name in the member's own words. Sentence case, at most \
{TITLE_CHARS} characters.
- body: what it does for this team, in one sentence of at most {BODY_CHARS} characters. State \
the work, not the accounts it reads. Name what is actually theirs — their product, their customer, \
their repository, the thing itself. A body that would read the same for any company is too \
general to be worth a row.
- ask: the sentence the member says by pressing the row, first person, asking for the application. \
Name the work concretely. Do not mention connecting an account: the assistant asks for what it \
needs once the work is agreed.

Then consider one check_in, and only write it if it clearly earns a place.

A check_in asks about work this member or their team owns. Memory also records other people's \
work — a post someone wrote, an article, another company, something a colleague reported about a \
third party. A fact being specific, and being in this member's memory, does not make the work \
theirs. Where the memory names someone else as the owner, or does not make the owner plain, write \
no check_in. Never restate another party's situation in the first person.

The bar is high. Write no check_in at all unless the memory shows work of their own that is under \
way and unsettled. Most windows should produce none. A missing row costs nothing; a row that hands \
the member someone else's problem costs their trust in every other row.

Write plainly. No greeting, no exclamation, no restatement of what the member has done. State what \
is true or what the row does, and nothing else.
"""


class RankedUnlock(BaseModel):
    unlock: str
    title: str = Field(min_length=1, max_length=TITLE_CHARS)
    body: str = Field(min_length=1, max_length=BODY_CHARS)
    ask: str = Field(min_length=1, max_length=ASK_CHARS)


class CheckIn(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_CHARS)
    body: str = Field(min_length=1, max_length=BODY_CHARS)
    ask: str = Field(min_length=1, max_length=ASK_CHARS)


class Slate(BaseModel):
    """What one member's start screen ranks, when it was ranked, and the instructions it was ranked
    under.

    Those two stamps are the whole cache rule. `generated_at` turns the slate over on time alone,
    because nothing else can make a ranking wrong: access is answered live at read time. `prompt` is
    the digest of the instructions that produced it, so an edit to those instructions — a narrowed
    character budget, say, whose rows the read would otherwise refuse whole — re-ranks at once
    rather than waiting out the clock."""

    generated_at: datetime
    prompt: str
    ranked: tuple[RankedUnlock, ...] = ()
    check_in: CheckIn | None = None

    def fresh(self, now: datetime) -> bool:
        return now - self.generated_at < STARTERS_TTL and self.prompt == SLATE_DIGEST


class _SlateCall(BaseModel):
    ranked: tuple[RankedUnlock, ...] = Field(max_length=RANKED_MAX)
    check_in: CheckIn | None = None


SLATE_DIGEST = hashlib.sha256(SLATE_SYSTEM.encode()).hexdigest()


def starters_key(member_id: UUID) -> str:
    return f"{STARTERS_KEY_PREFIX}:{member_subject(member_id)}"


def claim_key(member_id: UUID) -> str:
    return f"{CLAIM_KEY_PREFIX}:{member_subject(member_id)}"


def cooldown_key(member_id: UUID) -> str:
    return f"{COOLDOWN_KEY_PREFIX}:{member_subject(member_id)}"


def _stamped(held: object, key: str) -> datetime | None:
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
class StarterCache:
    """What the start screen reads, generated where it is read and cached for `STARTERS_TTL`.

    `read` is the whole flow. A fresh slate is answered as it stands. A stale one is answered too —
    the member is waiting, and last half-hour's ranking beats an empty screen — and only then is a
    new one made, so the answer this member gets is never worse for the regeneration happening.

    Nothing here may raise: a read that has never answered stops the pane polling for the rest of
    the session, so a provider failure would freeze the start screen rather than degrade it. Every
    failure answers what is stored and stamps a cooldown so the next read does not walk into the
    same wall."""

    store: ScopedStore
    member_id: UUID
    agents: tuple[str, ...]
    recalled: tuple[str, ...]
    model: SurfaceModel | None
    solvent: bool

    async def read(self) -> Slate | None:
        now = datetime.now(UTC)
        held = await self._held()
        if held is not None and held.fresh(now):
            return held
        if not await self._may_generate(now):
            return held
        try:
            made = await self._rank(now)
        except Exception:
            cooled: JsonValue = {"failed_at": now.isoformat()}
            await self.store.put(cooldown_key(self.member_id), cooled)
            warn("web.starters_failed", member_id=str(self.member_id))
            return held
        finally:
            await self.store.delete(claim_key(self.member_id))
        await self.store.put(starters_key(self.member_id), made.model_dump(mode="json"))
        return made

    async def _held(self) -> Slate | None:
        stored = await self.store.get(starters_key(self.member_id))
        if not isinstance(stored, dict):
            return None
        try:
            return Slate.model_validate(stored)
        except ValidationError:
            return None

    async def _may_generate(self, now: datetime) -> bool:
        """Whether this read is the one that regenerates. It is not when there is no model or no
        memory to rank, when the balance is refusing — a workspace that cannot run turns must not
        be spending on suggestions it cannot act on — when a failure is still cooling off, or when
        another read already holds the claim."""
        if self.model is None or not self.recalled or not self.solvent:
            return False
        failed = _stamped(await self.store.get(cooldown_key(self.member_id)), "failed_at")
        if failed is not None and now - failed < COOLDOWN_AFTER_FAILURE:
            return False
        return await self._claim(now)

    async def _claim(self, now: datetime) -> bool:
        """One reader generates. The pane re-reads on its interval and again whenever the tab is
        looked at, and two tabs are ordinary, so without this a member could pay for the same slate
        several times over.

        `put_if` with `expected=None` inserts only where no claim stands. A claim left behind by a
        reader that died is taken over once it is older than `CLAIM_LEASE`, by comparing against the
        exact value read — there is no primitive that displaces a live row, so the stale value is
        the token."""
        key = claim_key(self.member_id)
        mine: JsonValue = {"claimed_at": now.isoformat()}
        if await self.store.put_if(key, mine, expected=None):
            return True
        standing = await self.store.get(key)
        claimed = _stamped(standing, "claimed_at")
        if claimed is not None and now - claimed < CLAIM_LEASE:
            return False
        return await self.store.put_if(key, mine, expected=standing)

    async def _rank(self, now: datetime) -> Slate:
        assert self.model is not None
        payload = {
            "memory": list(self.recalled),
            "applications": list(self.agents),
            "catalog": [{"name": row.name, "does": row.does} for row in UNLOCKS],
        }
        reply = await self.model.turn(
            ModelRequest(
                model=self.model.model,
                system=SLATE_SYSTEM,
                messages=(
                    Message(role="user", content=json.dumps(payload, separators=(",", ":"))),
                ),
                max_tokens=SLATE_MAX_TOKENS,
                conversation_cache_ttl="5m",
                tools=(
                    ToolSchema(
                        name=SLATE_TOOL,
                        description=SLATE_TOOL_DESCRIPTION,
                        input_schema=_SlateCall.model_json_schema(),
                    ),
                ),
                tool_choice=SLATE_TOOL,
                reasoning="off",
            )
        )
        return settle_slate(reply, now)


def settle_slate(reply: Message, generated_at: datetime) -> Slate:
    """The slate a `record_slate` reply carries, entry by entry: a row the contract does not
    satisfy, one naming no catalog row, and a repeat of a row already taken each drop without
    taking the rest of the slate with them, while a reply that recorded no call at all raises —
    that is not the same answer as a member with nothing to rank, and the caller must settle
    nothing for them."""
    blocks = () if isinstance(reply.content, str) else reply.content
    recorded = next(
        (block for block in blocks if isinstance(block, ToolUseBlock) and block.name == SLATE_TOOL),
        None,
    )
    if recorded is None:
        raise ValueError(f"starter ranking recorded no {SLATE_TOOL} call")
    entries = recorded.input.get("ranked")
    ranked: list[RankedUnlock] = []
    for entry in entries if isinstance(entries, list) else ():
        try:
            candidate = RankedUnlock.model_validate(entry)
        except ValidationError:
            continue
        if candidate.unlock in UNLOCKS_BY_NAME and all(
            candidate.unlock != held.unlock for held in ranked
        ):
            ranked.append(candidate)
    try:
        check_in = CheckIn.model_validate(recorded.input.get("check_in"))
    except ValidationError:
        check_in = None
    return Slate(
        generated_at=generated_at,
        prompt=SLATE_DIGEST,
        ranked=tuple(ranked[:RANKED_MAX]),
        check_in=check_in,
    )
