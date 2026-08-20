"""The start screen's per-member slate: the rows a member reads before they have asked anything.

A batch-at-interval job, fed only by its own clock, so it can never fire on the slate it wrote. It
ranks the unlock catalog against what this member's memory says the team works on and what
applications the workspace already has, and stores the ranking under the member's own subject. It
reads no connection state at all: whether a ranked row is an application the member can build now
or an unlock still short of an account is decided at read time by `workspace_starters`, against
live truth, so connecting an account and reloading is enough to move a row.
"""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, Field, ValidationError

from ufo.sdk.audience import audience_subjects, conversation_audience
from ufo.sdk.context import ExtensionContext
from ufo.sdk.models import Message, ModelRequest, ToolSchema, ToolUseBlock
from ufo.sdk.subjects import member_subject
from ufo_ext_web.panels import UNLOCKS, UNLOCKS_BY_NAME

STARTERS_JOB_NAME = "starters"
STARTERS_JOB_SCHEDULE = "0 */10 * * * *"
STARTERS_KEY_PREFIX = "starters"

MEMORY_LIMIT = 60
MEMORY_TEXT_CHARS = 400
RANKED_MAX = 8
TITLE_CHARS = 28
BODY_CHARS = 96
ASK_CHARS = 320
SLATE_MAX_TOKENS = 4096
SLATE_TOOL = "record_slate"
SLATE_TOOL_DESCRIPTION = "Record the ranked starters for this member."

member = sa.table(
    "member",
    sa.column("id", sa.Uuid),
    sa.column("workspace_id", sa.Uuid),
    sa.column("seated_at", sa.DateTime(timezone=True)),
)

SLATE_SYSTEM = """\
You rank the start screen of a work assistant for one member. The screen shows three rows they can \
press, and each row says one sentence on their behalf.

You are given what this member's memory says about their company and their work, the applications \
the workspace already has, and a catalog of applications the product knows how to build.

Rank the catalog rows that would help this member most. Rank a row for the work the memory shows \
them doing, never because its accounts look popular. Rank no row whose job an existing application \
already does. Rank at most eight, best first; rank fewer rather than padding with rows the memory \
gives you no reason for.

For each ranked row write:
- title: the application's name in the member's own words. Sentence case, at most 28 characters.
- body: what it does for this team, in one sentence of at most 96 characters. State the work, not \
the accounts it reads.
- ask: the sentence the member says by pressing the row, first person, asking for the application. \
Name the work concretely. Do not mention connecting an account: the assistant asks for what it \
needs once the work is agreed.

Then write one check_in: a question this member would actually ask about work in progress, drawn \
from something specific in their memory — a customer, a project, a decision that was pending. It \
asks about work; it does not ask to build anything. Write no check_in at all when the memory shows \
no work in progress; a made-up question is worse than a missing row.

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
    """What one member's start screen ranks, and the fingerprint of the state it was ranked from.

    The fingerprint is the whole regeneration rule: a tick that computes the same one skips the
    model call. There is no age floor — a workspace whose memory and applications have not moved
    has nothing new to say."""

    fingerprint: str
    ranked: tuple[RankedUnlock, ...] = ()
    check_in: CheckIn | None = None


class _SlateCall(BaseModel):
    ranked: tuple[RankedUnlock, ...] = Field(max_length=RANKED_MAX)
    check_in: CheckIn | None = None


def starters_key(member_id: UUID) -> str:
    return f"{STARTERS_KEY_PREFIX}:{member_subject(member_id)}"


async def rank_starters(ctx: ExtensionContext) -> None:
    """Rank every seated member's start screen — the job the manifest registers."""
    if ctx.model is None:
        raise RuntimeError("starters need model access; serve wires it")
    agents = tuple(sorted(agent.name for agent in await ctx.workspace_agents()))
    async with ctx.transaction() as connection:
        rows = (
            await connection.execute(
                sa.select(member.c.id).where(
                    member.c.workspace_id == ctx.workspace_id,
                    member.c.seated_at.is_not(None),
                )
            )
        ).all()
    for row in rows:
        await _MemberSlate(ctx=ctx, member_id=row.id, agents=agents).run()


@dataclass(frozen=True)
class _MemberSlate:
    ctx: ExtensionContext
    member_id: UUID
    agents: tuple[str, ...]

    async def run(self) -> None:
        recalled = await self._memory()
        fingerprint = self._fingerprint(recalled)
        key = starters_key(self.member_id)
        held = await self.ctx.store.get(key)
        if isinstance(held, dict) and held.get("fingerprint") == fingerprint:
            return
        slate = await self._rank(recalled, fingerprint)
        await self.ctx.store.put(key, slate.model_dump(mode="json"))

    async def _memory(self) -> tuple[str, ...]:
        subjects = audience_subjects(conversation_audience(self.member_id))
        page = await self.ctx.recent_memory(subjects, MEMORY_LIMIT)
        return tuple(match.text[:MEMORY_TEXT_CHARS] for match in page.rows)

    def _fingerprint(self, recalled: tuple[str, ...]) -> str:
        state = json.dumps([list(recalled), list(self.agents)], separators=(",", ":"))
        return hashlib.sha256(state.encode()).hexdigest()

    async def _rank(self, recalled: tuple[str, ...], fingerprint: str) -> Slate:
        if not recalled:
            return Slate(fingerprint=fingerprint)
        payload = {
            "memory": list(recalled),
            "applications": list(self.agents),
            "catalog": [{"name": row.name, "does": row.does} for row in UNLOCKS],
        }
        assert self.ctx.model is not None
        reply = await self.ctx.model.turn(
            ModelRequest(
                model=self.ctx.model.model,
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
        return settle_slate(reply, fingerprint)


def settle_slate(reply: Message, fingerprint: str) -> Slate:
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
    return Slate(fingerprint=fingerprint, ranked=tuple(ranked[:RANKED_MAX]), check_in=check_in)
