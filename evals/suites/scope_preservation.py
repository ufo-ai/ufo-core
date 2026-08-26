"""Exact-step probes for preserving a bug fix's narrow behavioral contract."""

from typing import Literal

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
)
from evals.harness.scorers import answer_text

ScopeChoice = Literal["narrow", "generalize"]


def scope_choice_scorer(expected: ScopeChoice) -> DescribedGrader[CapabilityOutput]:
    """Grade the implementation boundary chosen at the isolated reasoning step."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        choice = answer_text(output.response)
        if choice == expected:
            return CapabilityVerdict(True, f"selected {choice}")
        return CapabilityVerdict(False, f"selected {choice!r}, expected {expected!r}")

    return DescribedGrader(f"the final ANSWER selects the {expected!r} scope", grade)


CASES = (
    CapabilityCase(
        "regex-csv-quantifier",
        """You are choosing an implementation plan before changing an established parser.

The parser accepts a comma-separated list of regular expressions. A bug report demonstrates one
failure: the comma in the quantifier ``(foo{1,3})`` is mistaken for the separator. The report says
valid regular expressions should be expressible, while the current comma-separated list behavior
must continue to work.

Choose the plan supported by the demonstrated requirement:

NARROW: Do not split a comma while a ``{`` quantifier is open; retain separator behavior
everywhere else.
GENERALIZE: Build a general regex-list lexer that also stops splitting inside groups, character
classes, and after escapes.

Reply with exactly ``ANSWER: NARROW`` or ``ANSWER: GENERALIZE``.""",
        scope_choice_scorer("narrow"),
        digest_tag="scope-preservation:regex-csv-quantifier:v2",
    ),
    CapabilityCase(
        "regex-csv-explicit-generalization",
        """You are choosing an implementation plan before changing an established parser.

The parser accepts a comma-separated list of regular expressions. The requirement explicitly says
that commas inside quantifiers, groups, character classes, and after escapes must all remain part
of their regular expression, while commas outside those contexts remain separators.

Choose the plan that implements the requirement:

NARROW: Do not split a comma only while a ``{`` quantifier is open.
GENERALIZE: Build a regex-list lexer that also stops splitting inside groups, character classes,
and after escapes.

Reply with exactly ``ANSWER: NARROW`` or ``ANSWER: GENERALIZE``.""",
        scope_choice_scorer("generalize"),
        digest_tag="scope-preservation:regex-csv-explicit-generalization:v2",
    ),
)
