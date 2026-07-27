from pathlib import Path

SKILL = Path(__file__).parents[2] / ".claude" / "skills" / "babysit-prs" / "SKILL.md"


def test_babysit_prs_skill_keeps_review_stage_updates_terse() -> None:
    skill = SKILL.read_text()

    assert (
        skill.index("never rerun an unchanged mechanism")
        < skill.index("Between current-head passes")
        < skill.index("## What you may and may not do")
    )
    assert "only material findings, blockers, decisions, or completed wait results" in skill
    assert "omit tool narration, routine checks, the act of waiting, and unchanged status" in skill
    assert "`Two valid issues. Fixing.`" in skill
