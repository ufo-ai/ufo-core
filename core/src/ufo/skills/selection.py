"""Member-tier skill visibility: where an agent's saved-skill cards reach the model, and how.

A small member tier — card lines within `SKILL_PROMPT_FOLD_MAX_CHARS` — folds into the system
prompt's `<available_skills>` beside the deploy tier (`prompt_index`), so the common case lists
exactly like a system skill; past the fold the tier renders into the turn message under a fixed
char budget instead. `member_visibility` is the turn's one pass over the cards — the fold and
catalog decisions and the rendered block together, each card's line rendered once and the budget
trimmed on a running total, because this runs on the event loop for every turn at up to thousands
of cards. The block fills a ladder in order — pinned cards as full lines, then the whole catalog
while it fits, then `select_top_k` as full lines with every remaining skill as its bare name, then
a count of what was dropped — so the agent always knows a skill exists even when its description
did not fit, and `skill_search` reaches the tail. `select_top_k` is the one retrieval selection:
the block renders it and the shadow log records it, so the promote-or-delete evidence reads the
block's own behavior. Everything here is a pure function over cards: no I/O, deterministic, unable
to fail a turn."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from ufo.skills.runtime import SkillCard, SkillRegistry

SKILL_PROMPT_FOLD_MAX_CHARS = 4_000
SKILL_MEMBER_BLOCK_MAX_CHARS = 16_000
SKILL_LINE_MAX_CHARS = 200
SKILL_TOP_K = 8
SKILL_QUERY_MAX_CHARS = 4_000
SKILL_QUERY_TERM_MIN_CHARS = 3
QUERY_TERM_SPLIT = re.compile(r"\W+")
MEMBER_BLOCK_OPEN = "<saved_skills>"
MEMBER_BLOCK_CLOSE = "</saved_skills>"
MEMBER_BLOCK_MORE = "{count} more skills; skill_search finds them."


def _query_terms(query: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            term
            for term in QUERY_TERM_SPLIT.split(query[:SKILL_QUERY_MAX_CHARS].casefold())
            if len(term) >= SKILL_QUERY_TERM_MIN_CHARS
        )
    )


def _term_hits(terms: Sequence[str], card: SkillCard) -> int:
    haystack = f"{card.name} {card.description}".casefold()
    return sum(1 for term in terms if term in haystack)


def lexical_score(query: str, card: SkillCard) -> int:
    """How many distinct query terms appear in the card's name or description — case-folded, split
    on non-alphanumeric runs so punctuation never hides a term, terms under
    `SKILL_QUERY_TERM_MIN_CHARS` dropped so stopwords match nothing, and the query capped at
    `SKILL_QUERY_MAX_CHARS` so a large paste costs a bounded scan."""
    return _term_hits(_query_terms(query), card)


def select_top_k(query: str, cards: Sequence[SkillCard]) -> tuple[SkillCard, ...]:
    """The unpinned top-K by lexical score against the capped query, ties keeping card order — the
    one retrieval selection: the member block renders exactly this, and the shadow log records
    exactly this, so the vector leg's promote gate compares against what the block actually did.
    The query tokenizes once for the whole ranking, never per card."""
    terms = _query_terms(query)
    unpinned = tuple(card for card in cards if not card.pinned)
    ranked = sorted(unpinned, key=lambda card: _term_hits(terms, card), reverse=True)
    return tuple(ranked[:SKILL_TOP_K])


def skill_line(card: SkillCard) -> str:
    """One card as a full index line, capped so no description can crowd out the rest."""
    return f"- {card.name}: {card.description}"[:SKILL_LINE_MAX_CHARS]


def folds_into_prompt(cards: Sequence[SkillCard]) -> bool:
    """Whether the member tier is small enough to list in the system prompt itself: every card's
    capped `skill_line` render, taken together, fits the fold budget. Chars rather than a count, so
    long descriptions cannot sneak a large tier past the fold."""
    return _joined_size(tuple(skill_line(card) for card in cards)) <= SKILL_PROMPT_FOLD_MAX_CHARS


def prompt_index(registry: SkillRegistry) -> tuple[tuple[str, str], ...]:
    """The `{{skill_index}}` entries for a turn: the deploy index, and — when the member tier folds
    — each member card in the same line format, its description capped exactly as the fold budget
    measured it. Past the fold the deploy index alone renders and the member tier moves to
    `member_block`; `SkillRegistry.index()` keeps its deploy-only meaning for callers that need the
    invariant set."""
    cards = tuple(registry.member_cards.values())
    if not folds_into_prompt(cards):
        return registry.index()
    return (
        *registry.index(),
        *((card.name, skill_line(card)[len(f"- {card.name}: ") :]) for card in cards),
    )


def catalog_fits(cards: Sequence[SkillCard]) -> bool:
    """Whether every card renders as a full line within the block budget — the rung above
    retrieval; while it holds, ranking chooses nothing."""
    return _block_size(tuple(skill_line(card) for card in cards)) <= SKILL_MEMBER_BLOCK_MAX_CHARS


@dataclass(frozen=True)
class MemberVisibility:
    """One turn's member-tier visibility, decided in a single pass: whether the tier folds into the
    system prompt, whether the whole catalog fits the block, and the block text (empty when folded
    or when there is nothing to show)."""

    folded: bool
    catalog_fits: bool
    block: str


def member_visibility(query: str, cards: Sequence[SkillCard]) -> MemberVisibility:
    """Decide the tier's placement and render its block in one pass over the cards: each line is
    rendered once and reused by the fold check, the catalog check, and the block; the budget trim
    subtracts from a running total instead of re-rendering per dropped line; the query tokenizes
    once inside `select_top_k`. The rendered block is byte-identical to composing the standalone
    predicates and ladder — this is the hot-path shape of the same decisions."""
    lines = tuple(skill_line(card) for card in cards)
    folded = _joined_size(lines) <= SKILL_PROMPT_FOLD_MAX_CHARS
    fits = _block_size(lines) <= SKILL_MEMBER_BLOCK_MAX_CHARS
    if not cards or folded:
        return MemberVisibility(folded=folded, catalog_fits=fits, block="")
    by_name = dict(zip((card.name for card in cards), lines, strict=True))
    pinned = tuple(card for card in cards if card.pinned)
    unpinned = tuple(card for card in cards if not card.pinned)
    if fits:
        ordered = tuple(by_name[card.name] for card in (*pinned, *unpinned))
        return MemberVisibility(folded=False, catalog_fits=True, block=_render(ordered))
    top = select_top_k(query, cards)
    top_names = frozenset(card.name for card in top)
    shown = [
        *(by_name[card.name] for card in pinned),
        *(by_name[card.name] for card in top),
        *(f"- {card.name}" for card in unpinned if card.name not in top_names),
    ]
    body = sum(len(line) + 1 for line in shown)
    dropped = 0
    while shown:
        closing_chars = 0 if dropped == 0 else len(MEMBER_BLOCK_MORE.format(count=dropped)) + 1
        if body + closing_chars + _BLOCK_WRAPPER_CHARS <= SKILL_MEMBER_BLOCK_MAX_CHARS:
            break
        body -= len(shown.pop()) + 1
        dropped += 1
    closing = () if dropped == 0 else (MEMBER_BLOCK_MORE.format(count=dropped),)
    return MemberVisibility(folded=False, catalog_fits=False, block=_render((*shown, *closing)))


def member_block(query: str, cards: Sequence[SkillCard]) -> str:
    """The saved-skills block a member turn's message carries, engaging only past the fold — a tier
    small enough for `prompt_index` renders no block at all. Filled ladder-rung by rung: pinned
    cards first as full lines, then every card as a full line while the whole set fits the budget;
    past that, `select_top_k` keeps full lines and every remaining card appears as its bare name —
    retrieval chooses which cards carry descriptions, never which skills are visible. Anything
    still over budget drops lines from the end, bare names first, and the block closes with a count
    naming `skill_search` as the way to the rest."""
    return member_visibility(query, cards).block


_BLOCK_WRAPPER_CHARS = len(MEMBER_BLOCK_OPEN) + len(MEMBER_BLOCK_CLOSE) + 1


def _joined_size(lines: Sequence[str]) -> int:
    return sum(len(line) + 1 for line in lines) - 1 if lines else 0


def _block_size(lines: Sequence[str]) -> int:
    return _joined_size(lines) + _BLOCK_WRAPPER_CHARS + (1 if lines else 0)


def _render(lines: tuple[str, ...]) -> str:
    return "\n".join((MEMBER_BLOCK_OPEN, *lines, MEMBER_BLOCK_CLOSE))
