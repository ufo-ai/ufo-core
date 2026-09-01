import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from ufo.runtime.indexing import Hit
from ufo.runtime.queue import (
    _member_skill_block,
    _prompt_skill_index,
    _shadow_skill_selection,
)
from ufo.runtime.skills.runtime import CORE_SKILL_REGISTRY, SkillCard
from ufo.runtime.skills.selection import (
    MEMBER_BLOCK_CLOSE,
    MEMBER_BLOCK_OPEN,
    SKILL_MEMBER_BLOCK_MAX_CHARS,
    SKILL_PROMPT_FOLD_MAX_CHARS,
    SKILL_TOP_K,
    MemberVisibility,
    catalog_fits,
    folds_into_prompt,
    lexical_score,
    member_block,
    member_visibility,
    prompt_index,
    select_top_k,
    skill_line,
)
from ufo.schema.records import (
    INTENT_ADMISSION,
    INTERNAL_ADMISSION,
    MEMBER_ADMISSION,
    SCHEDULED_ADMISSION,
    Turn,
    TurnAdmissionSource,
)


def _card(name: str, description: str, pinned: bool = False) -> SkillCard:
    return SkillCard(name=name, description=description, pinned=pinned)


def _corpus(count: int, stem: str = "topic") -> tuple[SkillCard, ...]:
    return tuple(
        _card(f"{stem}-{i:04d}", f"Load when a member asks about {stem} number {i:04d}. " * 3)
        for i in range(count)
    )


def _lines(block: str) -> list[str]:
    lines = block.splitlines()
    assert lines[0] == MEMBER_BLOCK_OPEN
    assert lines[-1] == MEMBER_BLOCK_CLOSE
    return lines[1:-1]


def test_lexical_score_counts_distinct_query_terms_case_folded() -> None:
    card = _card("invoice-review", "Load when a member asks to reconcile an invoice.")
    assert lexical_score("Reconcile INVOICE", card) == 2
    assert lexical_score("invoice invoice invoice", card) == 1
    assert lexical_score("kubernetes", card) == 0


def test_select_top_k_ranks_unpinned_only_and_keeps_ties_stable() -> None:
    pinned = _card("pinned-invoice", "Load when a member reconciles an invoice.", pinned=True)
    hit = _card("invoice-review", "Load when a member reconciles an invoice.")
    cards = (pinned, *_corpus(12), hit)

    selected = select_top_k("reconcile an invoice", cards)

    assert len(selected) == SKILL_TOP_K
    assert selected[0] is hit
    assert pinned not in selected
    assert [card.name for card in selected[1:]] == [card.name for card in _corpus(7)]


def test_lexical_score_orders_the_better_match_first() -> None:
    close = _card("expense-report", "Load when a member files an expense report.")
    far = _card("holiday-planner", "Load when a member plans time off.")
    query = "file an expense report"
    assert lexical_score(query, close) > lexical_score(query, far)


def _sized_cards(line_lengths: tuple[int, ...]) -> tuple[SkillCard, ...]:
    return tuple(
        _card(f"c{i:03d}", "x" * (length - len(f"- c{i:03d}: ")))
        for i, length in enumerate(line_lengths)
    )


def test_the_fold_boundary_is_exact_on_chars() -> None:
    at_budget = _sized_cards((*(200,) * 19, 181))
    assert len("\n".join(skill_line(card) for card in at_budget)) == SKILL_PROMPT_FOLD_MAX_CHARS
    assert folds_into_prompt(at_budget)

    one_over = _sized_cards((*(200,) * 19, 182))
    assert not folds_into_prompt(one_over)


def test_a_folded_tier_joins_the_prompt_index_and_renders_no_block() -> None:
    cards = (
        _card("alpha", "a"),
        _card("invoice-review", "Load when a member reconciles an invoice."),
    )
    registry = CORE_SKILL_REGISTRY.with_member(cards, _materialize_nothing)

    entries = prompt_index(registry)

    assert entries[: len(registry.index())] == registry.index()
    assert ("invoice-review", "Load when a member reconciles an invoice.") in entries
    assert member_block("reconcile an invoice", cards) == ""


def test_past_the_fold_the_prompt_index_is_deploy_only() -> None:
    registry = CORE_SKILL_REGISTRY.with_member(_corpus(50), _materialize_nothing)
    assert not folds_into_prompt(_corpus(50))
    assert prompt_index(registry) == registry.index()


def test_pins_render_first_whatever_order_they_arrive_in() -> None:
    cards = (*_corpus(25), _card("pinned-late", "p", pinned=True), *_corpus(25, stem="extra"))
    block = member_block("", cards)
    assert _lines(block)[0] == "- pinned-late: p"


def test_past_the_catalog_budget_top_k_keeps_full_lines_and_the_rest_bare_names() -> None:
    cards = (*_corpus(200), _card("invoice-review", "Load when a member reconciles an invoice."))
    assert not catalog_fits(cards)

    block = member_block("reconcile an invoice", cards)
    lines = _lines(block)

    full = [line for line in lines if ": " in line]
    bare = [line for line in lines if ": " not in line]
    assert full[0] == skill_line(cards[-1])
    assert len(full) == SKILL_TOP_K
    assert bare == [f"- {card.name}" for card in cards if skill_line(card) not in full]
    assert len(block) <= SKILL_MEMBER_BLOCK_MAX_CHARS


def test_pins_keep_full_lines_past_the_catalog_budget() -> None:
    pinned = _card("pinned-flow", "Load when the member asks for the pinned flow.", pinned=True)
    cards = (*_corpus(200), pinned)

    lines = _lines(member_block("something unrelated", cards))

    assert lines[0] == skill_line(pinned)
    assert f"- {pinned.name}" not in lines[1:]


def test_the_block_respects_the_budget_on_every_rung() -> None:
    for count in (1, 80, 120, 700, 3000):
        block = member_block("a query with several terms", _corpus(count))
        assert len(block) <= SKILL_MEMBER_BLOCK_MAX_CHARS


def _turn(
    speaker: UUID | None = None,
    parent_turn_id: UUID | None = None,
    admission_source: TurnAdmissionSource = MEMBER_ADMISSION,
) -> Turn:
    return Turn(
        id=uuid4(),
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=1,
        status="running",
        inbound="reconcile an invoice",
        created_at=datetime(2026, 8, 20, tzinfo=UTC),
        admission_source=admission_source,
        speaker_member_id=speaker,
        parent_turn_id=parent_turn_id,
    )


async def _materialize_nothing(name: str) -> None:
    return None


CARDS = (
    SkillCard(name="invoice-review", description="Load when reconciling an invoice."),
    *_corpus(30, stem="filler"),
)


def _view(turn: Turn) -> MemberVisibility:
    return member_visibility(turn.inbound, CARDS)


def test_prompt_skill_index_folds_only_while_enabled() -> None:
    folded = CORE_SKILL_REGISTRY.with_member(
        (SkillCard(name="invoice-review", description="Load when reconciling an invoice."),),
        _materialize_nothing,
    )
    heavy = CORE_SKILL_REGISTRY.with_member(_corpus(50), _materialize_nothing)

    assert (
        "invoice-review",
        "Load when reconciling an invoice.",
    ) in _prompt_skill_index(folded, enabled=True)
    assert _prompt_skill_index(folded, enabled=False) == folded.index()
    assert _prompt_skill_index(heavy, enabled=True) == heavy.index()


def test_a_member_turn_gets_the_block() -> None:
    turn = _turn(speaker=uuid4())
    block = _member_skill_block(turn, _view(turn), enabled=True)
    assert "- invoice-review: Load when reconciling an invoice." in block


def test_recall_parity_skips_a_speakerless_internal_root() -> None:
    turn = _turn(admission_source=INTERNAL_ADMISSION)
    assert _member_skill_block(turn, _view(turn), enabled=True) == ""


def test_recall_parity_keeps_the_block_on_a_scheduled_turn() -> None:
    turn = _turn(admission_source=SCHEDULED_ADMISSION)
    assert "- invoice-review:" in _member_skill_block(turn, _view(turn), enabled=True)


def test_an_intent_turn_gets_no_block() -> None:
    turn = _turn(speaker=uuid4(), admission_source=INTENT_ADMISSION)
    assert _member_skill_block(turn, _view(turn), enabled=True) == ""


def test_the_config_switch_ablates_the_block() -> None:
    turn = _turn(speaker=uuid4())
    assert _member_skill_block(turn, _view(turn), enabled=False) == ""


@dataclass(frozen=True)
class _StubIndex:
    hits: tuple[Hit, ...]

    async def vector(
        self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int
    ) -> tuple[Hit, ...]:
        return self.hits


async def test_shadow_selection_swallows_every_failure_with_a_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    @dataclass(frozen=True)
    class _BrokenEmbed:
        async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            raise RuntimeError("embed down")

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _shadow_skill_selection(_StubIndex(()), _BrokenEmbed(), _turn(speaker=uuid4()), CARDS)

    (record,) = [r.ufo for r in caplog.records if r.getMessage() == "skill.shadow_selection_failed"]
    assert record["error_class"] == "RuntimeError"
