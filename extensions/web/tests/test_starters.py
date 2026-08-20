"""The start screen's per-member slate: the catalog's contract, what a model reply settles to, and
which rows a member reads once live access is applied to the ranking.

The two decisions are separated on purpose and tested that way. The job ranks relevance and stores
it; the read answers access. So a connector landing moves a row with no job tick in between, and a
stored ranking never states a claim about access that a connect made stale.
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from ufo_ext_web.panels import FIRST_RUN_PROVIDER_NAMES, UNLOCKS, UNLOCKS_BY_NAME, Unlock
from ufo_ext_web.starters import (
    SLATE_TOOL,
    CheckIn,
    RankedUnlock,
    Slate,
    rank_starters,
    settle_slate,
    starters_key,
)
from ufo_ext_web.surface import STARTER_APP_SLOTS, fill_starters

from ufo.audience import audience_subjects, conversation_audience
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.listings import ListingPage
from ufo.memory import MemoryMatch, MemorySearch
from ufo.models.catalog import CORE_PRICING
from ufo.models.interface import (
    Message,
    ModelClient,
    ModelEvent,
    ModelRequest,
    TextBlock,
    ToolCallDelta,
    ToolCallStart,
    ToolUseBlock,
)
from ufo.models.pricing import Pricing
from ufo.schema import tables
from ufo.schema.records import Usage
from ufo.subjects import SHARED_SUBJECT, member_subject
from ufo.workspace import ws

AUTO_MODEL = "claude-opus-5"
PROVIDER_ANTHROPIC = "anthropic"

FINGERPRINT = "f" * 64


def _ranked(unlock: str, title: str) -> RankedUnlock:
    return RankedUnlock(unlock=unlock, title=title, body="Does the job.", ask="Build me the thing.")


def _reply(*blocks: TextBlock | ToolUseBlock) -> Message:
    return Message(role="assistant", content=blocks)


def _call(**payload: object) -> ToolUseBlock:
    return ToolUseBlock(id="call-1", name="record_slate", input=payload)


def test_every_catalog_row_names_offered_tiles_and_a_drawn_mark() -> None:
    for row in UNLOCKS:
        for group in row.needs:
            assert set(group) <= FIRST_RUN_PROVIDER_NAMES
    assert len(UNLOCKS_BY_NAME) == len(UNLOCKS)


def test_a_catalog_row_naming_no_tile_is_refused_at_construction() -> None:
    with pytest.raises(ValidationError):
        Unlock(name="ghost", mark="gnomon", does="Nothing.", needs=(("photoshop",),))


def test_a_catalog_row_wearing_an_undrawn_mark_is_refused_at_construction() -> None:
    with pytest.raises(ValidationError):
        Unlock(name="ghost", mark="not-a-mark", does="Nothing.", needs=(("github",),))


def test_a_catalog_row_stating_an_empty_alternative_is_refused() -> None:
    with pytest.raises(ValidationError):
        Unlock(name="ghost", mark="gnomon", does="Nothing.", needs=((),))


def test_a_group_is_met_by_any_one_name_and_every_group_must_be_met() -> None:
    row = Unlock(
        name="pair",
        mark="gnomon",
        does="Needs a tracker and GitHub.",
        needs=(("github",), ("linear", "jira", "asana")),
    )
    assert row.missing(frozenset({"github", "jira"})) == ()
    assert row.missing(frozenset({"github"})) == ("linear",)
    assert row.missing(frozenset()) == ("github", "linear")


def test_a_row_needing_nothing_is_ready_in_an_empty_workspace() -> None:
    row = Unlock(name="research", mark="wedjat", does="Reads the open web.")
    assert row.missing(frozenset()) == ()


def test_the_slate_is_stored_under_the_members_own_subject() -> None:
    member_id = uuid4()
    assert starters_key(member_id).endswith(member_subject(member_id))


def test_a_reply_recording_no_call_raises_rather_than_settling_an_empty_slate() -> None:
    with pytest.raises(ValueError, match="record_slate"):
        settle_slate(_reply(TextBlock(text="here are some ideas")), FINGERPRINT)


def test_one_unusable_entry_drops_without_taking_the_slate_with_it() -> None:
    slate = settle_slate(
        _reply(
            _call(
                ranked=[
                    {"unlock": "pr-babysitter", "title": "PR watch", "body": "b", "ask": "a"},
                    {"unlock": "pr-babysitter", "title": "", "body": "b", "ask": "a"},
                    {"unlock": "runway-report", "title": "Runway", "body": "b", "ask": "a"},
                ]
            )
        ),
        FINGERPRINT,
    )
    assert [entry.unlock for entry in slate.ranked] == ["pr-babysitter", "runway-report"]


def test_an_entry_naming_no_catalog_row_drops() -> None:
    slate = settle_slate(
        _reply(
            _call(
                ranked=[
                    {"unlock": "invented", "title": "Invented", "body": "b", "ask": "a"},
                    {"unlock": "inbox-triage", "title": "Inbox", "body": "b", "ask": "a"},
                ]
            )
        ),
        FINGERPRINT,
    )
    assert [entry.unlock for entry in slate.ranked] == ["inbox-triage"]


def test_a_row_ranked_twice_is_taken_once() -> None:
    slate = settle_slate(
        _reply(
            _call(
                ranked=[
                    {"unlock": "inbox-triage", "title": "Inbox", "body": "b", "ask": "a"},
                    {"unlock": "inbox-triage", "title": "Mail", "body": "b", "ask": "a"},
                ]
            )
        ),
        FINGERPRINT,
    )
    assert [entry.unlock for entry in slate.ranked] == ["inbox-triage"]


def test_an_unusable_check_in_leaves_the_slate_without_one() -> None:
    slate = settle_slate(_reply(_call(ranked=[], check_in={"title": "x"})), FINGERPRINT)
    assert slate.check_in is None


def test_a_held_row_is_an_application_and_a_short_row_is_the_unlock() -> None:
    slate = Slate(
        fingerprint=FINGERPRINT,
        ranked=(_ranked("pr-babysitter", "PR watch"), _ranked("runway-report", "Runway")),
    )
    rows, unlock = fill_starters(slate, frozenset({"github"}), frozenset())
    assert [row.title for row in rows] == ["PR watch"]
    assert unlock is not None
    assert unlock.title == "Runway"
    assert [tile.name for tile in unlock.providers] == ["stripe", "quickbooks"]


def test_a_connected_provider_is_never_offered_as_an_unlock() -> None:
    slate = Slate(fingerprint=FINGERPRINT, ranked=(_ranked("runway-report", "Runway"),))
    _rows, still_short = fill_starters(slate, frozenset({"stripe"}), frozenset())
    assert still_short is not None
    assert [tile.name for tile in still_short.providers] == ["quickbooks"]

    rows, none_left = fill_starters(slate, frozenset({"stripe", "quickbooks"}), frozenset())
    assert none_left is None
    assert [row.title for row in rows] == ["Runway"]


def test_a_row_more_than_two_accounts_short_is_passed_over() -> None:
    row = Unlock(
        name="three-way",
        mark="gnomon",
        does="Needs three.",
        needs=(("github",), ("stripe",), ("notion",)),
    )
    UNLOCKS_BY_NAME[row.name] = row
    try:
        slate = Slate(fingerprint=FINGERPRINT, ranked=(_ranked("three-way", "Three"),))
        rows, unlock = fill_starters(slate, frozenset(), frozenset())
        assert rows == ()
        assert unlock is None
    finally:
        del UNLOCKS_BY_NAME[row.name]


def test_an_application_the_workspace_already_has_is_never_offered_again() -> None:
    slate = Slate(
        fingerprint=FINGERPRINT,
        ranked=(_ranked("pr-babysitter", "PR watch"), _ranked("inbox-triage", "Inbox")),
    )
    rows, _unlock = fill_starters(
        slate, frozenset({"github", "gmail"}), frozenset({"pr-babysitter"})
    )
    assert [row.title for row in rows] == ["Inbox"]


def test_a_title_an_application_already_carries_is_never_offered_again() -> None:
    slate = Slate(fingerprint=FINGERPRINT, ranked=(_ranked("pr-babysitter", "PR watch"),))
    rows, _unlock = fill_starters(slate, frozenset({"github"}), frozenset({"pr watch"}))
    assert rows == ()


def test_the_screen_takes_no_more_applications_than_it_draws() -> None:
    slate = Slate(
        fingerprint=FINGERPRINT,
        ranked=tuple(
            _ranked(name, name)
            for name in ("competitor-watch", "market-researcher", "writing-desk")
        ),
    )
    rows, _unlock = fill_starters(slate, frozenset(), frozenset())
    assert len(rows) == STARTER_APP_SLOTS


def test_the_check_in_closes_the_list_and_founds_no_application() -> None:
    slate = Slate(
        fingerprint=FINGERPRINT,
        ranked=(_ranked("competitor-watch", "Rivals"),),
        check_in=CheckIn(title="Acme renewal", body="Waiting on legal.", ask="Where did it land?"),
    )
    rows, _unlock = fill_starters(slate, frozenset(), frozenset())
    assert [(row.kind, row.mark) for row in rows] == [("app", "wedjat"), ("check_in", None)]


@dataclass
class _SlateClient:
    """Streams one canned `record_slate` call, counting completions so a test can witness that a
    workspace whose state has not moved costs no model call at all."""

    arguments: str
    calls: int = 0

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.calls += 1
        yield ToolCallStart(id=f"call-{self.calls}", name=SLATE_TOOL)
        yield ToolCallDelta(id=f"call-{self.calls}", partial_json=self.arguments)
        yield Usage(input_tokens=10, output_tokens=5)


@dataclass
class _Resolver:
    client: ModelClient
    auto_model: str = AUTO_MODEL
    pricing: Pricing = CORE_PRICING

    async def client_for(self, model: str) -> ModelClient:
        return self.client

    def key_slot_for(self, model: str) -> str | None:
        return None

    def provider_for(self, model: str) -> str:
        return PROVIDER_ANTHROPIC


@dataclass
class _Recall:
    """A memory-search provider standing in for the extension's: `list_recent` is the only half the
    starters job reads, and the subjects it is asked for are recorded so a test can hold that a
    member's slate is ranked from their own audience and nobody else's."""

    texts: tuple[str, ...]
    asked: list[frozenset[str]] = field(default_factory=list)

    async def search(self, queries, reader, start=None, end=None):
        raise AssertionError("the starters job browses memory; it never searches it")

    def listable_kinds(self) -> tuple[str, ...]:
        return ("fact",)

    async def list_recent(self, subjects, limit, kinds=None, cursor=None):
        self.asked.append(frozenset(subjects))
        return ListingPage(
            rows=tuple(
                MemoryMatch(
                    kind="fact",
                    text=text,
                    ref=None,
                    created_at=datetime.now(UTC),
                    subject=SHARED_SUBJECT,
                )
                for text in self.texts
            )
        )


async def _seed_member() -> tuple[UUID, UUID]:
    workspace_id, member_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(id=workspace_id, created_at=now, updated_at=now)
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="member@example.com",
                seated_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    return workspace_id, member_id


RANKED_ARGUMENTS = json.dumps(
    {
        "ranked": [
            {
                "unlock": "pr-babysitter",
                "title": "PR watch",
                "body": "Reports what each pull request waits on.",
                "ask": "Build me a pull request watcher.",
            }
        ],
        "check_in": None,
    }
)


async def test_a_ranked_slate_lands_under_the_members_subject_from_their_own_audience(
    db: None,
) -> None:
    workspace_id, member_id = await _seed_member()
    client = _SlateClient(RANKED_ARGUMENTS)
    recall = _Recall(("We ship a payments product.",))
    with ws(workspace_id):
        ctx = context_for(
            "web",
            frozenset(),
            model_resolver=_Resolver(client),
            model_job="web:starters",
            member_context_read=True,
            memory=MemorySearch(provider=recall),
        )
        await rank_starters(ctx)
        stored = await ctx.store.get(starters_key(member_id))

    assert client.calls == 1
    assert recall.asked == [audience_subjects(conversation_audience(member_id))]
    assert [entry.unlock for entry in Slate.model_validate(stored).ranked] == ["pr-babysitter"]


async def test_a_workspace_that_has_not_moved_costs_no_model_call(db: None) -> None:
    workspace_id, _member_id = await _seed_member()
    client = _SlateClient(RANKED_ARGUMENTS)
    recall = _Recall(("We ship a payments product.",))
    with ws(workspace_id):
        ctx = context_for(
            "web",
            frozenset(),
            model_resolver=_Resolver(client),
            model_job="web:starters",
            member_context_read=True,
            memory=MemorySearch(provider=recall),
        )
        await rank_starters(ctx)
        await rank_starters(ctx)
        assert client.calls == 1

        recall.texts = ("We ship a payments product.", "We moved to annual billing.")
        await rank_starters(ctx)
        assert client.calls == 2


async def test_a_member_with_no_memory_is_ranked_without_asking_the_model(db: None) -> None:
    workspace_id, member_id = await _seed_member()
    client = _SlateClient(RANKED_ARGUMENTS)
    with ws(workspace_id):
        ctx = context_for(
            "web",
            frozenset(),
            model_resolver=_Resolver(client),
            model_job="web:starters",
            member_context_read=True,
            memory=MemorySearch(provider=_Recall(())),
        )
        await rank_starters(ctx)
        stored = await ctx.store.get(starters_key(member_id))

    assert client.calls == 0
    assert Slate.model_validate(stored).ranked == ()
