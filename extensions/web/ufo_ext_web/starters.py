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
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from ufo.sdk.context import JsonValue, ScopedStore
from ufo.sdk.models import Message, ModelRequest, ToolSchema, ToolUseBlock
from ufo.sdk.o11y import warn
from ufo.sdk.subjects import member_subject
from ufo.sdk.surfaces import SurfaceModel
from ufo_ext_web.panels import UNLOCKS, UNLOCKS_BY_NAME

STARTERS_TTL = timedelta(minutes=30)
CLAIM_LEASE = timedelta(minutes=2)
COOLDOWN_AFTER_FAILURE = timedelta(minutes=15)

MEMORY_LIMIT = 60
MEMORY_TEXT_CHARS = 400
RANKED_MAX = 8
TITLE_CHARS = 20
LINE_CHARS = 64
ASK_CHARS = 320
SLATE_MAX_TOKENS = 4096
SLATE_TOOL = "record_slate"
SLATE_TOOL_DESCRIPTION = "Record the ranked starters for this member."


@dataclass(frozen=True)
class SlatePrompt:
    """One ranking's instructions and the store keys its answers live under. The start screen and
    the automations screen ask the same question of the same memory under different instructions, so
    each holds its own slate: a member reads both screens, and one cache would hand the second
    screen the first one's rows."""

    key: str
    system: str
    digest: str


def slate_prompt(key: str, text: str) -> SlatePrompt:
    system = (
        text.strip()
        .replace("{{title_chars}}", str(TITLE_CHARS))
        .replace("{{line_chars}}", str(LINE_CHARS))
    )
    return SlatePrompt(key=key, system=system, digest=hashlib.sha256(system.encode()).hexdigest())


STARTERS_SLATE = slate_prompt(
    "starters", (Path(__file__).parent / "prompts" / "starters_slate.md").read_text()
)
AUTOMATIONS_SLATE = slate_prompt(
    "automations", (Path(__file__).parent / "prompts" / "automations_slate.md").read_text()
)


class RankedUnlock(BaseModel):
    unlock: str
    title: str = Field(min_length=1, max_length=TITLE_CHARS)
    line: str = Field(min_length=1, max_length=LINE_CHARS)
    ask: str = Field(min_length=1, max_length=ASK_CHARS)


class CheckIn(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_CHARS)
    line: str = Field(min_length=1, max_length=LINE_CHARS)
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

    def fresh(self, now: datetime, prompt: SlatePrompt) -> bool:
        return now - self.generated_at < STARTERS_TTL and self.prompt == prompt.digest


class _SlateCall(BaseModel):
    ranked: tuple[RankedUnlock, ...] = Field(max_length=RANKED_MAX)
    check_in: CheckIn | None = None


def starters_key(prompt: SlatePrompt, member_id: UUID) -> str:
    return f"{prompt.key}:{member_subject(member_id)}"


def claim_key(prompt: SlatePrompt, member_id: UUID) -> str:
    return f"{prompt.key}-claim:{member_subject(member_id)}"


def cooldown_key(prompt: SlatePrompt, member_id: UUID) -> str:
    return f"{prompt.key}-cooldown:{member_subject(member_id)}"


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
    prompt: SlatePrompt = STARTERS_SLATE

    async def read(self) -> Slate | None:
        now = datetime.now(UTC)
        held = await self._held()
        if held is not None and held.fresh(now, self.prompt):
            return held
        if not await self._may_generate(now):
            return held
        try:
            made = await self._rank(now)
        except Exception:
            cooled: JsonValue = {"failed_at": now.isoformat()}
            await self.store.put(cooldown_key(self.prompt, self.member_id), cooled)
            warn("web.starters_failed", member_id=str(self.member_id), slate=self.prompt.key)
            return held
        finally:
            await self.store.delete(claim_key(self.prompt, self.member_id))
        await self.store.put(
            starters_key(self.prompt, self.member_id), made.model_dump(mode="json")
        )
        return made

    async def _held(self) -> Slate | None:
        stored = await self.store.get(starters_key(self.prompt, self.member_id))
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
        cooled = await self.store.get(cooldown_key(self.prompt, self.member_id))
        failed = _stamped(cooled, "failed_at")
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
        key = claim_key(self.prompt, self.member_id)
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
                system=self.prompt.system,
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
        return settle_slate(reply, now, self.prompt)


def settle_slate(reply: Message, generated_at: datetime, prompt: SlatePrompt) -> Slate:
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
        prompt=prompt.digest,
        ranked=tuple(ranked[:RANKED_MAX]),
        check_in=check_in,
    )
