import importlib.util
import json
import re
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).parents[2]
_PATH = _ROOT / ".github" / "scripts" / "ai_review_gate.py"
_SPEC = importlib.util.spec_from_file_location("ai_review_gate", _PATH)
gate = importlib.util.module_from_spec(_SPEC)
sys.modules["ai_review_gate"] = gate
_SPEC.loader.exec_module(gate)

_WORKFLOW = _ROOT / ".github" / "workflows" / "ai-review-gate.yml"
_SKIP_CONDITION = re.compile(r"!contains\(github\.event\.review\.state, '(\w+)'\)")

HEAD = "9426a25a359f1f74eacc63f94637e92ece667a21"


def claude_review(state, commit_oid=HEAD, id=1):
    return gate.Review(id=id, reviewer="claude[bot]", state=state, commit_oid=commit_oid)


def verdict_comment(verdict="APPROVED", head=HEAD, author="claude[bot]"):
    return gate.IssueComment(
        author=author,
        body=f"<!-- claude-review-verdict head={head} verdict={verdict} -->",
    )


def test_verdict_requires_head_anchored_claude_verdict_review():
    assert gate.claude_verdict((), (), HEAD) is None
    assert gate.claude_verdict((claude_review("APPROVED", commit_oid="0" * 40),), (), HEAD) is None
    assert gate.claude_verdict((claude_review("COMMENTED"),), (), HEAD) is None
    assert gate.claude_verdict((claude_review("DISMISSED"),), (), HEAD) is None
    codex = gate.Review(id=2, reviewer="chatgpt-codex-connector", state="APPROVED", commit_oid=HEAD)
    assert gate.claude_verdict((codex,), (), HEAD) is None
    assert gate.claude_verdict((claude_review("APPROVED"),), (), HEAD) == "APPROVED"


def test_latest_verdict_wins():
    reviews = (claude_review("CHANGES_REQUESTED"), claude_review("APPROVED"))
    assert gate.claude_verdict(reviews, (), HEAD) == "APPROVED"
    assert gate.claude_verdict(reviews[::-1], (), HEAD) == "CHANGES_REQUESTED"


def test_dismissing_the_latest_verdict_voids_it():
    dismissed_last = (claude_review("APPROVED"), claude_review("DISMISSED"))
    assert gate.claude_verdict(dismissed_last, (), HEAD) is None
    dismissed_then_approved = (claude_review("DISMISSED"), claude_review("APPROVED"))
    assert gate.claude_verdict(dismissed_then_approved, (), HEAD) == "APPROVED"
    commented_after = (claude_review("APPROVED"), claude_review("COMMENTED"))
    assert gate.claude_verdict(commented_after, (), HEAD) == "APPROVED"


def test_deleted_account_author_is_no_reviewer():
    ghost = gate.review({"id": 7, "user": None, "state": "APPROVED", "commit_id": HEAD})
    assert ghost.reviewer == ""
    assert gate.claude_verdict((ghost,), (), HEAD) is None


def test_deleted_account_author_is_no_commenter():
    ghost = gate.issue_comment({"id": 8, "user": None, "body": verdict_comment().body})
    assert ghost.author == ""
    assert gate.claude_verdict((), (ghost,), HEAD) is None


def test_stale_reviews_are_claude_verdicts_off_head():
    old = "0" * 40
    stale_approval = claude_review("APPROVED", commit_oid=old, id=11)
    stale_changes = claude_review("CHANGES_REQUESTED", commit_oid=old, id=12)
    reviews = (
        stale_approval,
        stale_changes,
        claude_review("DISMISSED", commit_oid=old, id=13),
        claude_review("COMMENTED", commit_oid=old, id=14),
        claude_review("APPROVED", id=15),
        gate.Review(id=16, reviewer="chatgpt-codex-connector", state="APPROVED", commit_oid=old),
    )
    assert gate.stale_claude_reviews(reviews, HEAD) == (stale_approval, stale_changes)


def test_exact_head_marker_carries_a_self_authored_verdict():
    assert gate.claude_verdict((), (verdict_comment(),), HEAD) == "APPROVED"
    assert (
        gate.claude_verdict((), (verdict_comment("CHANGES_REQUESTED"),), HEAD)
        == "CHANGES_REQUESTED"
    )
    comments = (verdict_comment("CHANGES_REQUESTED"), verdict_comment())
    assert gate.claude_verdict((), comments, HEAD) == "APPROVED"


def test_marker_requires_claude_exact_head_and_exact_shape():
    stale = verdict_comment(head="0" * 40)
    other = verdict_comment(author="marshall-ufo")
    prose = gate.IssueComment(author="claude[bot]", body=f"Approved\n{verdict_comment().body}")
    abbreviated = verdict_comment(head=HEAD[:12])

    assert gate.claude_verdict((), (stale, other, prose, abbreviated), HEAD) is None


def test_formal_verdict_takes_precedence_over_marker():
    reviews = (claude_review("CHANGES_REQUESTED"),)
    assert gate.claude_verdict(reviews, (verdict_comment(),), HEAD) == "CHANGES_REQUESTED"


def test_issue_comment_targets_its_pull_request(monkeypatch, tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"issue": {"number": 635, "pull_request": {}}}))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "issue_comment")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))

    assert gate.target_pull_requests("metalcraftai", "ufo") == (635,)


def test_gate_state_maps_verdicts():
    assert gate.gate_state("APPROVED") == ("success", "Claude approved")
    assert gate.gate_state("CHANGES_REQUESTED") == ("failure", "Claude requested changes")
    assert gate.gate_state(None) == ("pending", "Awaiting Claude review of the head commit")


def test_the_workflow_skips_a_review_state_the_verdict_ignores():
    jobs = yaml.safe_load(_WORKFLOW.read_text())["jobs"]
    skipped = _SKIP_CONDITION.search(jobs["ai-review-gate"]["if"])
    assert skipped, "the job runs for every review state"
    assert skipped[1].upper() not in gate.DECISIVE_STATES
    assert gate.claude_verdict((claude_review(skipped[1].upper()),), (), HEAD) is None


def test_the_workflow_rechecks_verdict_comments():
    workflow = yaml.safe_load(_WORKFLOW.read_text())

    assert workflow[True]["issue_comment"]["types"] == ["created", "edited", "deleted"]
    assert workflow["permissions"]["issues"] == "read"
