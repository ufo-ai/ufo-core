import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).parents[2] / ".github" / "scripts" / "ai_review_gate.py"
_SPEC = importlib.util.spec_from_file_location("ai_review_gate", _PATH)
gate = importlib.util.module_from_spec(_SPEC)
sys.modules["ai_review_gate"] = gate
_SPEC.loader.exec_module(gate)

HEAD = "9426a25a359f1f74eacc63f94637e92ece667a21"


def codex_review(commit_oid, state="COMMENTED"):
    return gate.Review(
        reviewer="chatgpt-codex-connector", body="", url="", state=state, commit_oid=commit_oid
    )


def clean_codex_comment(prefix):
    return gate.IssueComment(
        author="chatgpt-codex-connector[bot]",
        body=f"Codex Review: Didn't find any major issues. :+1:\n\n**Reviewed commit:** `{prefix}`",
    )


def test_deleted_account_author_is_no_reviewer():
    ghost = gate.issue_comment({"author": None, "body": f"**Reviewed commit:** `{HEAD}`"})
    assert ghost.author == ""
    assert not gate.codex_reviewed((), (ghost,), HEAD)


def test_claude_awaiting_until_review_run_succeeds():
    assert gate.claude_awaiting(()) == "claude-review has not started"
    running = (gate.CheckRun(status="in_progress", conclusion=None),)
    assert gate.claude_awaiting(running) == "claude-review is running"
    failed = (gate.CheckRun(status="completed", conclusion="failure"),)
    assert gate.claude_awaiting(failed) == "claude-review did not succeed"
    succeeded = (*failed, gate.CheckRun(status="completed", conclusion="success"))
    assert gate.claude_awaiting(succeeded) is None


def test_codex_reviewed_by_head_anchored_review():
    assert gate.codex_reviewed((codex_review(HEAD),), (), HEAD)
    assert not gate.codex_reviewed((codex_review("0" * 40),), (), HEAD)
    assert not gate.codex_reviewed((codex_review(HEAD, state="DISMISSED"),), (), HEAD)
    assert not gate.codex_reviewed((), (), HEAD)


def test_codex_reviewed_by_clean_pass_comment():
    assert gate.codex_reviewed((), (clean_codex_comment(HEAD[:10]),), HEAD)
    assert not gate.codex_reviewed((), (clean_codex_comment("0123456789"),), HEAD)
    other_author = gate.IssueComment(author="marshall-ufo", body=f"**Reviewed commit:** `{HEAD}`")
    assert not gate.codex_reviewed((), (other_author,), HEAD)
    findings = gate.IssueComment(
        author="chatgpt-codex-connector[bot]",
        body=f"Codex Review: two P1 findings below.\n\n**Reviewed commit:** `{HEAD[:10]}`",
    )
    assert not gate.codex_reviewed((), (findings,), HEAD)


def test_changes_requested_blocks_only_on_head_reviews():
    stale = codex_review("0" * 40, state="CHANGES_REQUESTED")
    assert gate.changes_requested_blockers((stale,), HEAD) == []
    current = codex_review(HEAD, state="CHANGES_REQUESTED")
    blockers = gate.changes_requested_blockers((current,), HEAD)
    assert [item.reason for item in blockers] == ["latest review requests changes"]


def test_awaiting_reasons_names_each_missing_reviewer():
    done = (gate.CheckRun(status="completed", conclusion="success"),)
    assert gate.awaiting_reasons((), (), (), HEAD) == (
        "claude-review has not started",
        f"codex has not reviewed {HEAD[:10]}",
    )
    assert gate.awaiting_reasons(done, (codex_review(HEAD),), (), HEAD) == ()


def test_gate_state_blockers_beat_awaiting():
    blocker = gate.Blocker(reviewer="claude", reason="unresolved review thread", url="", detail="")
    state, description = gate.gate_state([blocker], ("claude-review is running",))
    assert state == "failure"
    assert "1 unresolved" in description


def test_gate_state_pending_until_reviewed():
    assert gate.gate_state(
        [], ("claude-review has not started", "codex has not reviewed 9426")
    ) == (
        "pending",
        "Awaiting AI review: claude-review has not started; codex has not reviewed 9426",
    )


def test_gate_state_success_after_clean_reviews():
    state, description = gate.gate_state([], ())
    assert state == "success"
    assert description == "AI review complete; no unresolved feedback"
