"""Daily Brief behavior eval criteria."""

from evals.daily_brief import CASES, _valid_sections


def test_daily_brief_eval_requires_only_present_sections_in_order() -> None:
    valid = """## Work to finish
One.
## Things you may have missed
Two.
## Drafts
Three.
## Coverage
Four.
"""
    assert _valid_sections(valid)
    assert not _valid_sections(valid.replace("## Drafts\nThree.\n", ""))
    assert not _valid_sections(valid + "## Outside context\nNone.\n")


def test_daily_brief_eval_has_a_semantic_rubric() -> None:
    assert CASES[0].name == "daily-brief-bounded-review"
    assert len(CASES[0].rubric) == 3
