"""The rows a settled thread ends on: what a model reply settles to, and which read pays for them.

The turn the rows answer is the whole cache rule, so the tests that matter are the ones about a
thread that moved: rows written for an earlier turn are never answered, and the read that writes
the next ones answers them itself.
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_web.claim import CLAIM_LEASE, Claim
from ufo_ext_web.followups import (
    FOLLOW_UPS_DIGEST,
    HOOK_CHARS,
    OFFERS_TOOL,
    FollowUpCache,
    Offer,
    Offers,
    claim_key,
    cooldown_key,
    offers_key,
    settle_offers,
)

from ufo.db import workspace_tx
from ufo.harness.models.catalog import CORE_PRICING
from ufo.harness.models.interface import (
    Message,
    ModelClient,
    ModelEvent,
    ModelRequest,
    TextBlock,
    ToolCallDelta,
    ToolCallStart,
    ToolUseBlock,
)
from ufo.harness.models.pricing import Pricing
from ufo.runtime.ext.context import ModelAccess, ScopedStore
from ufo.runtime.workspace import (
    PLATFORM_FUNDED,
    PLATFORM_PAYER,
    ResolvedModelClient,
    ws,
)
from ufo.schema import tables
from ufo.schema.records import Usage

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

AUTO_MODEL = "claude-opus-5"
PROVIDER_ANTHROPIC = "anthropic"

TAIL = (("user", "What did Acme say?"), ("assistant", "They asked for the revised terms."))
EVERY_KIND = frozenset({"ask", "keep", "share", "watch"})


def _row(kind: str, hook: str) -> dict[str, str]:
    """One recorded row. Its prompt names its own hook, so an assertion on what a row sends states
    which row sent it."""
    return {"kind": kind, "hook": hook, "prompt": f"{hook}, in full."}


def _reply(*blocks: TextBlock | ToolUseBlock) -> Message:
    return Message(role="assistant", content=blocks)


def _call(**payload: object) -> ToolUseBlock:
    return ToolUseBlock(id="call-1", name=OFFERS_TOOL, input=payload)


def test_a_reply_recording_no_call_raises_rather_than_settling_an_empty_thread() -> None:
    with pytest.raises(ValueError, match=OFFERS_TOOL):
        settle_offers(_reply(TextBlock(text="you could ask about Acme")), uuid4())


def test_one_unusable_row_drops_without_taking_the_rest_with_it() -> None:
    offers = settle_offers(
        _reply(
            _call(
                offers=[
                    _row("ask", "Draft the Acme reply"),
                    {"kind": "invented", "hook": "Do something", "prompt": "Do it."},
                    {"kind": "ask", "hook": "", "prompt": "Say nothing."},
                    {"kind": "ask", "hook": "Fits", "prompt": ""},
                    _row("ask", "x" * (HOOK_CHARS + 1)),
                    _row("share", "Post the terms to #sales"),
                ]
            )
        ),
        uuid4(),
    )
    assert [(row.kind, row.hook) for row in offers.offers] == [
        ("ask", "Draft the Acme reply"),
        ("share", "Post the terms to #sales"),
    ]


def test_a_hook_already_taken_is_never_drawn_twice() -> None:
    """A repeat is judged on what the member reads, not on the prompt behind it: two rows saying the
    same words are one row to them."""
    offers = settle_offers(
        _reply(
            _call(
                offers=[
                    {"kind": "ask", "hook": "Draft the Acme reply", "prompt": "Draft it short."},
                    {"kind": "keep", "hook": "Draft the Acme reply", "prompt": "Draft it long."},
                ]
            )
        ),
        uuid4(),
    )
    assert [(row.kind, row.prompt) for row in offers.offers] == [("ask", "Draft it short.")]


def test_rows_answer_their_own_turn_under_their_own_instructions() -> None:
    turn_id, moved_on = uuid4(), uuid4()
    held = Offers(turn=str(turn_id), prompt=FOLLOW_UPS_DIGEST, offers=())
    assert held.answer(turn_id)
    assert not held.answer(moved_on)
    assert not Offers(turn=str(turn_id), prompt="older instructions").answer(turn_id)


@dataclass
class _OffersClient:
    """Streams one canned `record_follow_ups` call, counting completions so a test can witness that
    rows already written cost nothing. `fails` makes the provider raise, which is the case the read
    must survive without ever answering an error."""

    arguments: str
    fails: bool = False
    calls: int = 0

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.calls += 1
        if self.fails:
            raise RuntimeError("provider is down")
        yield ToolCallStart(id=f"call-{self.calls}", name=OFFERS_TOOL)
        yield ToolCallDelta(id=f"call-{self.calls}", partial_json=self.arguments)
        yield Usage(input_tokens=10, output_tokens=5)


@dataclass
class _Resolver:
    client: ModelClient
    auto_model: str = AUTO_MODEL
    pricing: Pricing = CORE_PRICING

    async def client_for(self, model: str) -> ResolvedModelClient:
        return ResolvedModelClient(self.client, PLATFORM_FUNDED, PLATFORM_PAYER)

    def key_slot_for(self, model: str) -> str | None:
        return None

    def provider_for(self, model: str) -> str:
        return PROVIDER_ANTHROPIC


def _written(hook: str) -> str:
    return json.dumps({"offers": [_row("ask", hook)]})


async def _seed_workspace() -> UUID:
    workspace_id = uuid4()
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(id=workspace_id, created_at=now, updated_at=now)
        )
    return workspace_id


@dataclass
class _Thread:
    """The thread's words, counting the reads that ask for them — projecting a transcript is the
    expensive half of a read that usually answers from rows already written."""

    tail: tuple[tuple[str, str], ...] = TAIL
    reads: int = 0

    async def __call__(self) -> tuple[tuple[str, str], ...]:
        self.reads += 1
        return self.tail


def _cache(
    conversation_id: UUID,
    turn_id: UUID,
    client: _OffersClient | None = None,
    thread: _Thread | None = None,
    solvent: bool = True,
) -> FollowUpCache:
    model = None if client is None else ModelAccess(_Resolver(client), "surface:web")
    return FollowUpCache(
        store=ScopedStore(extension="web"),
        conversation_id=conversation_id,
        turn_id=turn_id,
        thread=thread or _Thread(),
        kinds=EVERY_KIND,
        model=model,
        solvent=solvent,
    )


async def test_the_rows_a_turn_ends_on_are_written_once_and_read_from_then_on(db: None) -> None:
    workspace_id = await _seed_workspace()
    conversation_id, turn_id = uuid4(), uuid4()
    client = _OffersClient(_written("Draft the Acme reply"))
    thread = _Thread()
    with ws(workspace_id):
        written = await _cache(conversation_id, turn_id, client, thread).read()
        assert [(row.hook, row.prompt) for row in written.offers] == [
            ("Draft the Acme reply", "Draft the Acme reply, in full.")
        ]
        assert not written.ranking

        again = await _cache(conversation_id, turn_id, client, thread).read()
        assert again.offers == written.offers
        assert client.calls == 1
        assert thread.reads == 1
        assert await ScopedStore(extension="web").get(claim_key(conversation_id)) is None


async def test_a_thread_that_moved_on_is_ranked_again_and_never_answers_the_old_rows(
    db: None,
) -> None:
    workspace_id = await _seed_workspace()
    conversation_id, spoke, spoke_again = uuid4(), uuid4(), uuid4()
    client = _OffersClient(_written("Draft the Acme reply"))
    with ws(workspace_id):
        await _cache(conversation_id, spoke, client).read()
        client.arguments = _written("Send the revised terms")

        moved = await _cache(conversation_id, spoke_again, client).read()

    assert client.calls == 2
    assert [row.hook for row in moved.offers] == ["Send the revised terms"]


async def test_a_second_reader_of_a_moved_thread_waits_rather_than_drawing_the_old_rows(
    db: None,
) -> None:
    """The reader holding the claim is writing the rows for the newest turn. A reader that arrives
    while it works draws nothing — the rows still stored answer a turn the thread has moved past,
    and a row asking for work already done is worse than an empty foot — and is told to ask again,
    since the rows it came for are seconds away."""
    workspace_id = await _seed_workspace()
    conversation_id, spoke, spoke_again = uuid4(), uuid4(), uuid4()
    client = _OffersClient(_written("Draft the Acme reply"))
    thread = _Thread()
    with ws(workspace_id):
        await _cache(conversation_id, spoke, client, thread).read()
        store = ScopedStore(extension="web")
        held = Claim(store=store, claim=claim_key(conversation_id), cooldown="unused")
        assert await held.take(datetime.now(UTC))

        waiting = await _cache(conversation_id, spoke_again, client, thread).read()
        assert (waiting.offers, waiting.ranking) == ((), True)
    assert client.calls == 1
    # The loser polls every two seconds until the winner lands; projecting the thread before the
    # claim made each of those polls read the whole transcript and drop it.
    assert thread.reads == 1


async def test_a_claim_older_than_its_lease_is_taken_over(db: None) -> None:
    workspace_id = await _seed_workspace()
    conversation_id, turn_id = uuid4(), uuid4()
    client = _OffersClient(_written("Draft the Acme reply"))
    store = ScopedStore(extension="web")
    with ws(workspace_id):
        stranded = datetime.now(UTC) - CLAIM_LEASE - timedelta(minutes=1)
        await store.put(claim_key(conversation_id), {"claimed_at": stranded.isoformat()})

        assert (await _cache(conversation_id, turn_id, client).read()).offers
    assert client.calls == 1


async def test_a_failed_ranking_answers_nothing_and_then_stands_down(db: None) -> None:
    workspace_id = await _seed_workspace()
    conversation_id, turn_id = uuid4(), uuid4()
    bad = _OffersClient(_written("Draft the Acme reply"), fails=True)
    store = ScopedStore(extension="web")
    with ws(workspace_id):
        failed = await _cache(conversation_id, turn_id, bad).read()
        assert (failed.offers, failed.ranking) == ((), False)
        assert await store.get(claim_key(conversation_id)) is None

        cooling = await _cache(conversation_id, turn_id, bad).read()
        assert (cooling.offers, cooling.ranking) == ((), False)
        assert bad.calls == 1
        assert await store.get(cooldown_key(conversation_id)) is not None


async def test_a_refusing_balance_reads_neither_the_thread_nor_a_model(db: None) -> None:
    workspace_id = await _seed_workspace()
    client = _OffersClient(_written("Draft the Acme reply"))
    thread = _Thread()
    with ws(workspace_id):
        refused = await _cache(uuid4(), uuid4(), client, thread, solvent=False).read()
        assert (refused.offers, refused.ranking) == ((), False)
    assert (client.calls, thread.reads) == (0, 0)


async def test_a_thread_with_nothing_said_in_it_is_never_ranked(db: None) -> None:
    workspace_id = await _seed_workspace()
    client = _OffersClient(_written("Draft the Acme reply"))
    with ws(workspace_id):
        assert (await _cache(uuid4(), uuid4(), client, _Thread(tail=())).read()).offers == ()
    assert client.calls == 0


async def test_a_deploy_with_no_model_answers_without_one(db: None) -> None:
    workspace_id = await _seed_workspace()
    with ws(workspace_id):
        assert (await _cache(uuid4(), uuid4(), None).read()).offers == ()


async def test_the_thread_and_the_kinds_it_can_take_are_what_the_ranking_reads(db: None) -> None:
    """The payload is the tail and the reach, and nothing else — a ranking that read a member's
    memory here would offer work this thread never mentioned."""
    workspace_id = await _seed_workspace()
    client = _OffersClient(_written("Draft the Acme reply"))
    seen: list[ModelRequest] = []
    original = client.complete

    async def watched(request: ModelRequest) -> AsyncIterator[ModelEvent]:
        seen.append(request)
        async for event in original(request):
            yield event

    client.complete = watched  # type: ignore[method-assign]
    with ws(workspace_id):
        await _cache(uuid4(), uuid4(), client).read()

    assert len(seen) == 1
    assert json.loads(str(seen[0].messages[0].content)) == {
        "thread": [
            {"from": "user", "said": "What did Acme say?"},
            {"from": "assistant", "said": "They asked for the revised terms."},
        ],
        "kinds": ["ask", "keep", "share", "watch"],
    }


async def test_rows_are_kept_under_the_conversation_they_answer(db: None) -> None:
    conversation_id = uuid4()
    assert offers_key(conversation_id).endswith(str(conversation_id))
    assert claim_key(conversation_id) != offers_key(conversation_id)
    assert cooldown_key(conversation_id) != claim_key(conversation_id)


def test_a_hook_longer_than_the_measure_it_reads_in_is_refused_at_construction() -> None:
    with pytest.raises(ValueError):
        Offer(kind="ask", hook="x" * (HOOK_CHARS + 1), prompt="Fits.")
