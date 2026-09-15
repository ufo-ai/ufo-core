"""The rows a settled thread ends on: the next turns a member takes by pressing one.

Written where they are read, for the turn they answer, and kept under that turn's id. A thread's
rows cannot go stale while the turn they answer is still its newest, and the next turn replaces
them — so there is no clock here and nothing generates on a turn's own end: a thread nobody is
looking at is never ranked, and a thread read twice is ranked once.

Which kinds of row this workspace can take is not kept with them. That is answered on every read
against the surfaces it holds right now, so a stored row never states a reach a teardown made
false.
"""

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from ufo.sdk.context import ScopedStore
from ufo.sdk.models import Message, ModelRequest, ToolSchema, ToolUseBlock
from ufo.sdk.o11y import warn
from ufo.sdk.surfaces import SurfaceModel
from ufo_ext_web.claim import CLAIM_LEASE, Claim, stamped

HOOK_CHARS = 80
PROMPT_CHARS = 200
OFFERS_MAX = 6
TAIL_MESSAGES = 6
TAIL_TEXT_CHARS = 1200
OFFERS_MAX_TOKENS = 1024
OFFERS_TOOL = "record_follow_ups"
OFFERS_TOOL_DESCRIPTION = "Record the rows this thread ends on."

FOLLOW_UPS = (
    (Path(__file__).parent / "prompts" / "follow_ups.md")
    .read_text()
    .strip()
    .replace("{{hook_chars}}", str(HOOK_CHARS))
    .replace("{{prompt_chars}}", str(PROMPT_CHARS))
)
FOLLOW_UPS_DIGEST = hashlib.sha256(FOLLOW_UPS.encode()).hexdigest()

OfferKind = Literal["ask", "keep", "share", "watch"]


class Offer(BaseModel):
    """One row under the thread, in two texts. `hook` is what the member reads — one sentence, at
    the measure a row reads in. `prompt` is what pressing it sends, which says the whole thing: a
    row has a line to read in and a turn has none, so the words that state the work in full are not
    the words that have to fit. The hook is a short form of the prompt and never a promise
    the prompt does not make — the turn is what happens, and the transcript records the prompt.

    `kind` is what the row would do, which the page draws as the row's mark and the read answers
    reach against."""

    kind: OfferKind
    hook: str = Field(min_length=1, max_length=HOOK_CHARS)
    prompt: str = Field(min_length=1, max_length=PROMPT_CHARS)


class Offers(BaseModel):
    """One thread's rows, the turn they answer, and the digest of the instructions that wrote them.

    Those two stamps are the whole cache rule. A turn later than `turn` moved the thread on, so its
    rows are about work already done; an edit to the instructions — a narrowed character budget,
    say, whose rows the read would otherwise refuse whole — rewrites them at once."""

    turn: str
    prompt: str
    offers: tuple[Offer, ...] = ()

    def answer(self, turn_id: UUID) -> bool:
        return self.turn == str(turn_id) and self.prompt == FOLLOW_UPS_DIGEST


@dataclass(frozen=True)
class Reading:
    """What one read answers: the rows to draw, and whether another read is about to have more.

    A thread gives two different empty answers — it supports no honest row, or its rows are being
    written right now by the read that found none — and a page that could not tell them apart would
    either ask forever or never ask again. `ranking` is the one that says ask again shortly."""

    offers: tuple[Offer, ...] = ()
    ranking: bool = False


class _OffersCall(BaseModel):
    offers: tuple[Offer, ...] = Field(max_length=OFFERS_MAX)


def offers_key(conversation_id: UUID) -> str:
    return f"follow-ups:{conversation_id}"


def claim_key(conversation_id: UUID) -> str:
    return f"follow-ups-claim:{conversation_id}"


def cooldown_key(conversation_id: UUID) -> str:
    return f"follow-ups-cooldown:{conversation_id}"


@dataclass(frozen=True)
class FollowUpCache:
    """The rows this thread's newest terminal turn ends on, written by the read that finds none for
    it.

    `read` is the whole flow. Rows written for an earlier turn are not answered: they offer work the
    thread has moved past, and a row that asks for what was just done is worse than an empty
    screen. The read that writes them answers them in the same breath, so a member waits once.

    The thread's own words arrive as `thread` rather than as a payload, because reading them is the
    expensive half of a read that is usually a cache hit: every open tab asks this on every mount,
    and projecting a transcript to answer rows already written would cost more than the ranking
    saved. The claim is taken before it is called, not after: a reader that loses the claim polls
    every two seconds until the winner lands, and reading the thread first made each of those polls
    project the whole transcript and throw it away.

    Nothing here may raise: a read that has never answered stops the pane asking for the rest of the
    session. Every failure answers nothing and stamps a cooldown, so the next read does not walk
    into the same wall."""

    store: ScopedStore
    conversation_id: UUID
    turn_id: UUID
    thread: Callable[[], Awaitable[tuple[tuple[str, str], ...]]]
    kinds: frozenset[str]
    model: SurfaceModel | None
    solvent: bool

    async def read(self) -> Reading:
        now = datetime.now(UTC)
        held = await self._held()
        if held is not None and held.answer(self.turn_id):
            return Reading(offers=held.offers)
        if self.model is None or not self.solvent:
            return Reading()
        if not await self._claim().take(now):
            return Reading(ranking=await self._standing(now))
        try:
            tail = await self.thread()
            if not tail:
                return Reading()
            made = await self._write(tail)
        except Exception:
            await self._claim().cool(now)
            warn("web.follow_ups_failed", conversation_id=str(self.conversation_id))
            return Reading()
        finally:
            await self._claim().release()
        await self.store.put(offers_key(self.conversation_id), made.model_dump(mode="json"))
        return Reading(offers=made.offers)

    async def _standing(self, now: datetime) -> bool:
        """Whether the claim this read lost is a live one. A reader that is writing the rows now is
        a reason to ask again in a moment; a cooldown from a failure is a reason not to, and both
        refuse the claim the same way."""
        claimed = stamped(await self.store.get(claim_key(self.conversation_id)), "claimed_at")
        return claimed is not None and now - claimed < CLAIM_LEASE

    async def _held(self) -> Offers | None:
        stored = await self.store.get(offers_key(self.conversation_id))
        if not isinstance(stored, dict):
            return None
        try:
            return Offers.model_validate(stored)
        except ValidationError:
            return None

    def _claim(self) -> Claim:
        return Claim(
            store=self.store,
            claim=claim_key(self.conversation_id),
            cooldown=cooldown_key(self.conversation_id),
        )

    async def _write(self, tail: tuple[tuple[str, str], ...]) -> Offers:
        assert self.model is not None
        payload = {
            "thread": [{"from": role, "said": said} for role, said in tail],
            "kinds": sorted(self.kinds),
        }
        reply = await self.model.turn(
            ModelRequest(
                model=self.model.model,
                system=FOLLOW_UPS,
                messages=(
                    Message(role="user", content=json.dumps(payload, separators=(",", ":"))),
                ),
                max_tokens=OFFERS_MAX_TOKENS,
                conversation_cache_ttl="5m",
                tools=(
                    ToolSchema(
                        name=OFFERS_TOOL,
                        description=OFFERS_TOOL_DESCRIPTION,
                        input_schema=_OffersCall.model_json_schema(),
                    ),
                ),
                tool_choice=OFFERS_TOOL,
                reasoning="off",
            )
        )
        return settle_offers(reply, self.turn_id)


def settle_offers(reply: Message, turn_id: UUID) -> Offers:
    """The rows a `record_follow_ups` reply carries, row by row: one the contract does not satisfy,
    and a repeat of a hook already taken, each drop without taking the rest with them, while a reply
    that recorded no call at all raises — that is not the same answer as a thread with nothing to
    offer, and the caller must store nothing for them.

    A repeat is judged on the hook, because the hook is what the member reads: two rows saying the
    same words are one row to them, whatever the prompts behind them differ by."""
    blocks = () if isinstance(reply.content, str) else reply.content
    recorded = next(
        (
            block
            for block in blocks
            if isinstance(block, ToolUseBlock) and block.name == OFFERS_TOOL
        ),
        None,
    )
    if recorded is None:
        raise ValueError(f"follow-up ranking recorded no {OFFERS_TOOL} call")
    written = recorded.input.get("offers")
    offers: list[Offer] = []
    for entry in written if isinstance(written, list) else ():
        try:
            candidate = Offer.model_validate(entry)
        except ValidationError:
            continue
        if all(candidate.hook != held.hook for held in offers):
            offers.append(candidate)
    return Offers(turn=str(turn_id), prompt=FOLLOW_UPS_DIGEST, offers=tuple(offers[:OFFERS_MAX]))
