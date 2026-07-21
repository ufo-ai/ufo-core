import re
from pathlib import Path

WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "claude-code-review.yml"
SKILL = Path(__file__).parents[2] / ".claude" / "skills" / "review-pull-request" / "SKILL.md"


def test_claude_code_review_uses_its_own_skill_with_permissive_bash() -> None:
    workflow = WORKFLOW.read_text()
    allowed_tools = re.search(r'--allowedTools "([^"]+)"', workflow)

    assert allowed_tools is not None
    assert {"Skill", "Bash"} <= set(allowed_tools.group(1).split(","))
    assert "--model sonnet" in workflow
    assert "Use the review-pull-request skill" in workflow
    assert "plugin_marketplaces" not in workflow
    assert "plugins:" not in workflow
    assert "fetch-depth: 0" in workflow


def test_claude_code_review_skill_requires_a_current_head_verdict() -> None:
    skill = SKILL.read_text()

    assert skill.index("root `README.md`") < skill.index("Read the pull request")
    assert "`AGENTS.md` when present; otherwise read `CLAUDE.md`" in skill
    assert "## Gotchas" in skill
    assert "pipe, redirect, or compound Bash command" in skill
    assert "Re-read `headRefOid` immediately before writing" in skill
    assert "--request-changes" in skill
    assert "--approve" in skill
