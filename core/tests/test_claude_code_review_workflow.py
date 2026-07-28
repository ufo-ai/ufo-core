import importlib.util
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[2]
WORKFLOW = _ROOT / ".github" / "workflows" / "claude-code-review.yml"
SKILL = _ROOT / ".claude" / "skills" / "review-pull-request" / "SKILL.md"
SCRIPT = _ROOT / ".github" / "scripts" / "prior_findings.py"

_SPEC = importlib.util.spec_from_file_location("prior_findings", SCRIPT)
prior = importlib.util.module_from_spec(_SPEC)
sys.modules["prior_findings"] = prior
_SPEC.loader.exec_module(prior)


def test_claude_code_review_uses_its_own_skill_with_permissive_bash() -> None:
    workflow = WORKFLOW.read_text()
    allowed_tools = re.search(r'--allowedTools "([^"]+)"', workflow)

    assert allowed_tools is not None
    assert {"Skill", "Bash"} <= set(allowed_tools.group(1).split(","))
    assert "--model claude-opus-5" in workflow
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
    assert "only because Claude authored the pull request" in skill
    assert "<!-- claude-review-verdict head=<full head SHA> verdict=APPROVED -->" in skill
    assert "<!-- claude-review-verdict head=<full head SHA> verdict=CHANGES_REQUESTED -->" in skill
    assert "Never use the marker for another review failure" in skill


def test_claude_code_review_skill_keeps_prose_findings_out_of_the_gate() -> None:
    skill = SKILL.read_text()

    assert "### Blocking and advisory findings" in skill
    assert "Sort every kept finding into one of three buckets" in skill
    assert "Blocking, because it changes what the code does" in skill
    assert "Blocking, because the diff breaks a rule the repository writes down" in skill
    assert "design findings live here" in skill
    assert "cannot anchor to a written rule is a preference" in skill
    assert "A structural or design finding fits neither blocking bucket" in skill
    assert "prose inaccuracy that does not change behavior" in skill
    assert "do not let it hold the verdict" in skill
    assert "When every remaining finding is advisory" in skill
    assert "For each validated issue, blocking or advisory" in skill
    assert "- Blocking findings of either kind: `gh pr review <number> --request-changes" in skill
    assert "- No blocking findings: `gh pr review <number> --approve`" in skill
    assert "must be fixed before merge" not in skill
    assert skill.index("Validate every returned finding") < skill.index(
        "### Blocking and advisory findings"
    )


def test_claude_code_review_skill_verifies_prior_rounds_instead_of_re_deriving() -> None:
    skill = SKILL.read_text()

    assert "python3 .github/scripts/prior_findings.py" in skill
    assert "`first`, `follow_up`, or `undetermined`" in skill
    assert "never as a first review" in skill
    assert "Step 6 reports `undetermined`" in skill
    assert "Tempted to read prior findings with `gh pr view` or a filtered `gh api`" in skill
    assert "Each agent finishes a changed file within its own lens" in skill
    assert "Closure across all three" in skill
    assert "On a `follow_up` round" in skill
    assert "verify each earlier finding is actually fixed at this head" in skill
    assert "`git diff <anchor_sha>..<head sha>`" in skill
    assert "A null `anchor_sha` leaves no range" in skill
    assert "A `follow_up` round carries a null `anchor_sha`" in skill
    assert "never on code\nno commit since `anchor_sha` has touched" in skill
    assert (
        "The reply decides how much work verifying costs, never whether the finding holds" in skill
    )
    assert "only the code at this head settles it" in skill
    assert skill.index("prior_findings.py") < skill.index("## Review")


def test_every_round_the_script_emits_has_an_instruction_in_the_skill() -> None:
    skill = SKILL.read_text()
    claude = prior.comment({"id": 1, "user": {"login": "claude[bot]"}, "path": "a.py", "body": ""})
    unanchored = prior.prior_round((claude,))
    rounds = {
        prior.prior_round(()).round,
        unanchored.round,
        prior.UNDETERMINED.round,
    }

    assert rounds == {"first", "follow_up", "undetermined"}
    for name in rounds:
        assert f"`{name}`" in skill

    assert unanchored.anchor_sha is None
    assert "A null `anchor_sha` leaves no range" in skill
    assert "A `follow_up` round carries a null `anchor_sha`" in skill
