"""The start screen's per-member slate: the catalog's contract, what a model reply settles to, and
which rows a member reads once live access is applied to the ranking.

The two decisions are separated on purpose and tested that way. The job ranks relevance and stores
it; the read answers access. So a connector landing moves a row with no job tick in between, and a
stored ranking never states a claim about access that a connect made stale.
"""

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from ufo_ext_web.panels import (
    APP_UNLOCKS,
    FIRST_RUN_PROVIDER_NAMES,
    UNLOCKS,
    UNLOCKS_BY_NAME,
    Unlock,
)
from ufo_ext_web.starters import (
    LINE_CHARS,
    SLATE_DIGEST,
    SLATE_SYSTEM,
    SLATE_TOOL,
    TITLE_CHARS,
    CheckIn,
    RankedUnlock,
    Slate,
    StarterCache,
    claim_key,
    settle_slate,
    starters_key,
)
from ufo_ext_web.surface import (
    DEFAULT_APP_SETUP_ASK,
    STARTER_APP_SLOTS,
    StarterApp,
    fill_starters,
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
from ufo.runtime.turns.subjects import member_subject
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Usage

AUTO_MODEL = "claude-opus-5"
PROVIDER_ANTHROPIC = "anthropic"

STAMP = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
CODE_ID = UUID("11111111-1111-4111-8111-111111111111")
MEETINGS_ID = UUID("22222222-2222-4222-8222-222222222222")


def _slate(**kw: object) -> Slate:
    return Slate(generated_at=STAMP, prompt=SLATE_DIGEST, **kw)  # type: ignore[arg-type]


def _ranked(unlock: str, title: str) -> RankedUnlock:
    """One ranked row. Its line names its own title, so an assertion on what a row says states
    which row said it — a fixture whose rows share one sentence cannot tell them apart."""
    return RankedUnlock(
        unlock=unlock, title=title, line=f"Does {title}.", ask="Build me the thing."
    )


def _reply(*blocks: TextBlock | ToolUseBlock) -> Message:
    return Message(role="assistant", content=blocks)


def _call(**payload: object) -> ToolUseBlock:
    return ToolUseBlock(id="call-1", name="record_slate", input=payload)


def test_every_catalog_row_names_offered_tiles_and_a_drawn_mark() -> None:
    for row in UNLOCKS:
        for group in row.needs:
            assert set(group) <= FIRST_RUN_PROVIDER_NAMES
    assert len(UNLOCKS_BY_NAME) == len(UNLOCKS)


def test_overlapping_starters_name_the_default_app_their_account_unlocks() -> None:
    mapped = {row.name: (row.extension, row.needs) for row in APP_UNLOCKS}

    assert mapped == {
        "pr-babysitter": ("app_code", (("github",),)),
        "issue-assigner": ("app_issues", (("github",),)),
        "meeting-to-issues": ("app_meetings", (("googlecalendar",),)),
        "day-ahead": ("app_meetings", (("googlecalendar",),)),
    }


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


def test_the_prompt_states_the_character_budget_the_schema_enforces() -> None:
    stated = [int(cap) for cap in re.findall(r"at most (\d+) characters", SLATE_SYSTEM)]
    assert stated == [TITLE_CHARS, LINE_CHARS]


def test_a_reply_recording_no_call_raises_rather_than_settling_an_empty_slate() -> None:
    with pytest.raises(ValueError, match="record_slate"):
        settle_slate(_reply(TextBlock(text="here are some ideas")), STAMP)


def test_one_unusable_entry_drops_without_taking_the_slate_with_it() -> None:
    slate = settle_slate(
        _reply(
            _call(
                ranked=[
                    {"unlock": "pr-babysitter", "title": "PR watch", "line": "b", "ask": "a"},
                    {"unlock": "pr-babysitter", "title": "", "line": "b", "ask": "a"},
                    {"unlock": "runway-report", "title": "Runway", "line": "b", "ask": "a"},
                ]
            )
        ),
        STAMP,
    )
    assert [entry.unlock for entry in slate.ranked] == ["pr-babysitter", "runway-report"]


def test_an_entry_naming_no_catalog_row_drops() -> None:
    slate = settle_slate(
        _reply(
            _call(
                ranked=[
                    {"unlock": "invented", "title": "Invented", "line": "b", "ask": "a"},
                    {"unlock": "inbox-triage", "title": "Inbox", "line": "b", "ask": "a"},
                ]
            )
        ),
        STAMP,
    )
    assert [entry.unlock for entry in slate.ranked] == ["inbox-triage"]


def test_a_row_ranked_twice_is_taken_once() -> None:
    slate = settle_slate(
        _reply(
            _call(
                ranked=[
                    {"unlock": "inbox-triage", "title": "Inbox", "line": "b", "ask": "a"},
                    {"unlock": "inbox-triage", "title": "Mail", "line": "b", "ask": "a"},
                ]
            )
        ),
        STAMP,
    )
    assert [entry.unlock for entry in slate.ranked] == ["inbox-triage"]


def test_an_unusable_check_in_leaves_the_slate_without_one() -> None:
    slate = settle_slate(_reply(_call(ranked=[], check_in={"title": "x"})), STAMP)
    assert slate.check_in is None


def test_a_held_row_is_an_application_and_a_short_row_is_the_unlock() -> None:
    slate = _slate(
        ranked=(_ranked("inbox-triage", "Inbox"), _ranked("runway-report", "Runway")),
    )
    rows, unlock = fill_starters(slate, frozenset({"gmail"}), frozenset())
    assert [row.line for row in rows] == ["Does Inbox."]
    assert unlock is not None
    assert unlock.line == "Does Runway."
    assert [tile.name for tile in unlock.providers] == ["stripe", "quickbooks"]


def test_a_connected_provider_is_never_offered_as_an_unlock() -> None:
    slate = _slate(ranked=(_ranked("runway-report", "Runway"),))
    _rows, still_short = fill_starters(slate, frozenset({"stripe"}), frozenset())
    assert still_short is not None
    assert [tile.name for tile in still_short.providers] == ["quickbooks"]

    rows, none_left = fill_starters(slate, frozenset({"stripe", "quickbooks"}), frozenset())
    assert none_left is None
    assert [row.line for row in rows] == ["Does Runway."]


def test_unlocks_take_the_slots_no_ready_application_filled() -> None:
    """A workspace that has connected nothing has no ready application by definition. The screen
    fills with named work rather than standing empty. A row standing in an application's slot says
    its work and nothing about its accounts — it carries `providers` so the row can wear the brand
    of what it needs, and the row closing the list is the one that states a price."""
    slate = _slate(
        ranked=(
            _ranked("inbox-triage", "Inbox"),
            _ranked("runway-report", "Runway"),
            _ranked("release-notes", "Release notes"),
        )
    )
    rows, unlock = fill_starters(slate, frozenset(), frozenset())
    assert unlock is not None and unlock.line == "Does Inbox."
    assert [(r.kind, r.line) for r in rows] == [
        ("unlock", "Does Runway."),
        ("unlock", "Does Release notes."),
    ]
    assert rows[0].line == "Does Runway."
    assert [t.name for t in rows[0].providers] == ["stripe", "quickbooks"]


def test_promotion_stops_at_the_slots_the_screen_draws() -> None:
    """A long ranking of unreachable rows still yields one screen, not a list of everything the
    workspace could connect."""
    slate = _slate(
        ranked=(
            _ranked("inbox-triage", "Inbox"),
            _ranked("runway-report", "Runway"),
            _ranked("release-notes", "Release notes"),
            _ranked("payment-watch", "Payments"),
            _ranked("ticket-themes", "Tickets"),
        )
    )
    rows, unlock = fill_starters(slate, frozenset(), frozenset())
    assert unlock is not None
    assert len([r for r in rows if r.kind == "unlock"]) == STARTER_APP_SLOTS


def test_a_row_short_of_accounts_is_never_drawn_twice() -> None:
    """The connector row takes the first short row, so promotion starts at the second. A screen
    that offered one row in two places would spend a slot saying the same thing."""
    slate = _slate(ranked=(_ranked("runway-report", "Runway"), _ranked("inbox-triage", "Inbox")))
    rows, unlock = fill_starters(slate, frozenset({"gmail"}), frozenset())
    assert unlock is not None and unlock.line == "Does Runway."
    assert [(r.kind, r.line) for r in rows] == [("app", "Does Inbox.")]


def test_two_ready_applications_leave_no_slot_to_promote_into() -> None:
    slate = _slate(
        ranked=(
            _ranked("inbox-triage", "Inbox"),
            _ranked("thread-summarizer", "Threads"),
            _ranked("runway-report", "Runway"),
        )
    )
    rows, unlock = fill_starters(slate, frozenset({"gmail", "slack"}), frozenset())
    assert [r.kind for r in rows] == ["app", "app"]
    assert unlock is not None and unlock.line == "Does Runway."


def test_a_row_more_than_two_accounts_short_is_passed_over() -> None:
    row = Unlock(
        name="three-way",
        mark="gnomon",
        does="Needs three.",
        needs=(("github",), ("stripe",), ("notion",)),
    )
    UNLOCKS_BY_NAME[row.name] = row
    try:
        slate = _slate(ranked=(_ranked("three-way", "Three"),))
        rows, unlock = fill_starters(slate, frozenset(), frozenset())
        assert rows == ()
        assert unlock is None
    finally:
        del UNLOCKS_BY_NAME[row.name]


def test_an_application_the_workspace_already_has_is_never_offered_again() -> None:
    slate = _slate(
        ranked=(_ranked("release-notes", "Releases"), _ranked("inbox-triage", "Inbox")),
    )
    rows, _unlock = fill_starters(
        slate, frozenset({"github", "slack", "gmail"}), frozenset({"release-notes"})
    )
    assert [row.line for row in rows] == ["Does Inbox."]


def test_a_title_an_application_already_carries_is_never_offered_again() -> None:
    slate = _slate(ranked=(_ranked("release-notes", "Release notes"),))
    rows, _unlock = fill_starters(
        slate, frozenset({"github", "slack"}), frozenset({"release notes"})
    )
    assert rows == ()


def test_a_default_app_is_not_suggested_before_its_account_is_known() -> None:
    slate = _slate(ranked=(_ranked("pr-babysitter", "PR watch"),))
    installed = (StarterApp(id=CODE_ID, extension="app_code", ready=False),)

    rows, unlock = fill_starters(slate, frozenset(), frozenset(), installed)

    assert rows == ()
    assert unlock is None


def test_a_known_account_opens_the_default_app_that_still_needs_setup() -> None:
    slate = _slate(ranked=(_ranked("pr-babysitter", "PR watch"),))
    installed = (StarterApp(id=CODE_ID, extension="app_code", ready=False),)

    rows, unlock = fill_starters(slate, frozenset({"github"}), frozenset(), installed)

    assert unlock is None
    assert [(row.line, row.agent_id, row.ask) for row in rows] == [
        ("Does PR watch.", CODE_ID, DEFAULT_APP_SETUP_ASK)
    ]


def test_a_ready_default_app_is_not_suggested_again() -> None:
    slate = _slate(ranked=(_ranked("pr-babysitter", "PR watch"),))
    installed = (StarterApp(id=CODE_ID, extension="app_code", ready=True),)

    rows, unlock = fill_starters(slate, frozenset({"github"}), frozenset(), installed)

    assert rows == ()
    assert unlock is None


def test_two_starters_for_one_default_app_produce_one_offer() -> None:
    slate = _slate(
        ranked=(
            _ranked("day-ahead", "Day ahead"),
            _ranked("meeting-to-issues", "Meeting follow-ups"),
        )
    )
    installed = (StarterApp(id=MEETINGS_ID, extension="app_meetings", ready=False),)

    rows, unlock = fill_starters(slate, frozenset({"googlecalendar"}), frozenset(), installed)

    assert unlock is None
    assert [(row.line, row.agent_id) for row in rows] == [("Does Day ahead.", MEETINGS_ID)]


def test_the_screen_takes_no_more_applications_than_it_draws() -> None:
    slate = _slate(
        ranked=tuple(
            _ranked(name, name)
            for name in ("competitor-watch", "market-researcher", "writing-desk")
        ),
    )
    rows, _unlock = fill_starters(slate, frozenset(), frozenset())
    assert len(rows) == STARTER_APP_SLOTS


def test_the_check_in_closes_the_list_and_founds_no_application() -> None:
    slate = _slate(
        ranked=(_ranked("competitor-watch", "Rivals"),),
        check_in=CheckIn(title="Acme renewal", line="Waiting on legal.", ask="Where did it land?"),
    )
    rows, _unlock = fill_starters(slate, frozenset(), frozenset())
    assert [(row.kind, row.mark) for row in rows] == [("app", "wedjat"), ("check_in", None)]


@dataclass
class _SlateClient:
    """Streams one canned `record_slate` call, counting completions so a test can witness that a
    cached slate costs nothing. `fails` makes the provider raise, which is the case the read must
    survive without ever answering an error."""

    arguments: str
    fails: bool = False
    calls: int = 0

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.calls += 1
        if self.fails:
            raise RuntimeError("provider is down")
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


RANKED_ARGUMENTS = json.dumps(
    {
        "ranked": [
            {
                "unlock": "pr-babysitter",
                "title": "PR watch",
                "line": "Reports what each pull request waits on.",
                "ask": "Build me a pull request watcher.",
            }
        ],
        "check_in": None,
    }
)

RECALLED = ("We ship a payments product.",)


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


def _cache(
    member_id: UUID,
    client: _SlateClient | None = None,
    recalled: tuple[str, ...] = RECALLED,
    solvent: bool = True,
) -> StarterCache:
    model = None if client is None else ModelAccess(_Resolver(client), "surface:web")
    return StarterCache(
        store=ScopedStore(extension="web"),
        member_id=member_id,
        agents=(),
        recalled=recalled,
        model=model,
        solvent=solvent,
    )


async def _backdate(store: ScopedStore, key: str, field_name: str, minutes: float) -> None:
    """Age a stamp the way the house tests a TTL — no test in this repo freezes the clock."""
    held = await store.get(key)
    assert isinstance(held, dict)
    stamped = datetime.fromisoformat(str(held[field_name])) - timedelta(minutes=minutes)
    await store.put(key, {**held, field_name: stamped.isoformat()})


async def test_the_first_read_generates_and_stores_the_slate(db: None) -> None:
    workspace_id, member_id = await _seed_member()
    client = _SlateClient(RANKED_ARGUMENTS)
    with ws(workspace_id):
        slate = await _cache(member_id, client).read()
        stored = await ScopedStore(extension="web").get(starters_key(member_id))

    assert client.calls == 1
    assert slate is not None
    assert [entry.unlock for entry in slate.ranked] == ["pr-babysitter"]
    assert Slate.model_validate(stored).prompt == SLATE_DIGEST


async def test_a_slate_inside_its_ttl_costs_no_model_call(db: None) -> None:
    workspace_id, member_id = await _seed_member()
    client = _SlateClient(RANKED_ARGUMENTS)
    with ws(workspace_id):
        await _cache(member_id, client).read()
        await _cache(member_id, client).read()
        assert client.calls == 1

        await _backdate(ScopedStore(extension="web"), starters_key(member_id), "generated_at", 31)
        await _cache(member_id, client).read()
        assert client.calls == 2


async def test_changed_ranking_instructions_re_rank_inside_the_ttl(db: None) -> None:
    workspace_id, member_id = await _seed_member()
    client = _SlateClient(RANKED_ARGUMENTS)
    store = ScopedStore(extension="web")
    with ws(workspace_id):
        await _cache(member_id, client).read()
        held = await store.get(starters_key(member_id))
        assert isinstance(held, dict)
        await store.put(starters_key(member_id), {**held, "prompt": "a different digest"})
        await _cache(member_id, client).read()

    assert client.calls == 2


async def test_a_failed_generation_answers_what_is_held_and_then_stands_down(db: None) -> None:
    workspace_id, member_id = await _seed_member()
    good = _SlateClient(RANKED_ARGUMENTS)
    bad = _SlateClient(RANKED_ARGUMENTS, fails=True)
    store = ScopedStore(extension="web")
    with ws(workspace_id):
        first = await _cache(member_id, good).read()
        assert first is not None
        await _backdate(store, starters_key(member_id), "generated_at", 31)
        aged = await store.get(starters_key(member_id))

        answered = await _cache(member_id, bad).read()
        assert bad.calls == 1
        # The member reads the ranking they already had rather than an error or an empty screen,
        # and nothing overwrote it.
        assert answered is not None
        assert answered.ranked == first.ranked
        assert await store.get(starters_key(member_id)) == aged

        # The cooldown holds the next read back rather than walking into the same wall.
        await _cache(member_id, bad).read()
        assert bad.calls == 1
        # And the claim was released, so a later read is free to try again.
        assert await store.get(claim_key(member_id)) is None


async def test_a_held_claim_leaves_the_second_reader_with_what_is_stored(db: None) -> None:
    workspace_id, member_id = await _seed_member()
    client = _SlateClient(RANKED_ARGUMENTS)
    store = ScopedStore(extension="web")
    with ws(workspace_id):
        await store.put(claim_key(member_id), {"claimed_at": datetime.now(UTC).isoformat()})
        assert await _cache(member_id, client).read() is None
        assert client.calls == 0

        await _backdate(store, claim_key(member_id), "claimed_at", 3)
        assert await _cache(member_id, client).read() is not None
        assert client.calls == 1


async def test_a_refusing_balance_generates_nothing(db: None) -> None:
    workspace_id, member_id = await _seed_member()
    client = _SlateClient(RANKED_ARGUMENTS)
    with ws(workspace_id):
        assert await _cache(member_id, client, solvent=False).read() is None
    assert client.calls == 0


async def test_a_member_with_no_memory_is_never_ranked(db: None) -> None:
    workspace_id, member_id = await _seed_member()
    client = _SlateClient(RANKED_ARGUMENTS)
    with ws(workspace_id):
        assert await _cache(member_id, client, recalled=()).read() is None
    assert client.calls == 0


async def test_a_deploy_with_no_model_answers_without_one(db: None) -> None:
    workspace_id, member_id = await _seed_member()
    with ws(workspace_id):
        assert await _cache(member_id, None).read() is None
