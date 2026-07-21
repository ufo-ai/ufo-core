import re
from pathlib import Path

WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "claude-code-review.yml"


def test_claude_code_review_permits_its_skill() -> None:
    allowed_tools = re.search(r'--allowedTools "([^"]+)"', WORKFLOW.read_text())

    assert allowed_tools is not None
    assert "Skill" in allowed_tools.group(1).split(",")
