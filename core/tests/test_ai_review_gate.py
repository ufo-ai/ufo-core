import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).parents[2] / ".github" / "scripts" / "ai_review_gate.py"
_SPEC = importlib.util.spec_from_file_location("ai_review_gate", _PATH)
gate = importlib.util.module_from_spec(_SPEC)
sys.modules["ai_review_gate"] = gate
_SPEC.loader.exec_module(gate)

HEAD = "9426a25a359f1f74eacc63f94637e92ece667a21"


def claude_review(state, commit_oid=HEAD):
    return gate.Review(reviewer="claude[bot]", state=state, commit_oid=commit_oid)


def test_verdict_requires_head_anchored_claude_verdict_review():
    assert gate.claude_verdict((), HEAD) is None
    assert gate.claude_verdict((claude_review("APPROVED", commit_oid="0" * 40),), HEAD) is None
    assert gate.claude_verdict((claude_review("COMMENTED"),), HEAD) is None
    assert gate.claude_verdict((claude_review("DISMISSED"),), HEAD) is None
    codex = gate.Review(reviewer="chatgpt-codex-connector", state="APPROVED", commit_oid=HEAD)
    assert gate.claude_verdict((codex,), HEAD) is None
    assert gate.claude_verdict((claude_review("APPROVED"),), HEAD) == "APPROVED"


def test_latest_verdict_wins():
    reviews = (claude_review("CHANGES_REQUESTED"), claude_review("APPROVED"))
    assert gate.claude_verdict(reviews, HEAD) == "APPROVED"
    assert gate.claude_verdict(reviews[::-1], HEAD) == "CHANGES_REQUESTED"


def test_dismissing_the_latest_verdict_voids_it():
    dismissed_last = (claude_review("APPROVED"), claude_review("DISMISSED"))
    assert gate.claude_verdict(dismissed_last, HEAD) is None
    dismissed_then_approved = (claude_review("DISMISSED"), claude_review("APPROVED"))
    assert gate.claude_verdict(dismissed_then_approved, HEAD) == "APPROVED"
    commented_after = (claude_review("APPROVED"), claude_review("COMMENTED"))
    assert gate.claude_verdict(commented_after, HEAD) == "APPROVED"


def test_deleted_account_author_is_no_reviewer():
    ghost = gate.review({"user": None, "state": "APPROVED", "commit_id": HEAD})
    assert ghost.reviewer == ""
    assert gate.claude_verdict((ghost,), HEAD) is None


def test_gate_state_maps_verdicts():
    assert gate.gate_state("APPROVED") == ("success", "Claude approved")
    assert gate.gate_state("CHANGES_REQUESTED") == ("failure", "Claude requested changes")
    assert gate.gate_state(None) == ("pending", "Awaiting Claude review of the head commit")
