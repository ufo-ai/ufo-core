import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
_PATH = _ROOT / ".github" / "scripts" / "prior_findings.py"
_SPEC = importlib.util.spec_from_file_location("prior_findings", _PATH)
prior = importlib.util.module_from_spec(_SPEC)
sys.modules["prior_findings"] = prior
_SPEC.loader.exec_module(prior)

_GATE_PATH = _ROOT / ".github" / "scripts" / "ai_review_gate.py"
_GATE_SPEC = importlib.util.spec_from_file_location("ai_review_gate", _GATE_PATH)
gate = importlib.util.module_from_spec(_GATE_SPEC)
sys.modules["ai_review_gate"] = gate
_GATE_SPEC.loader.exec_module(gate)

HEAD = "9426a25a359f1f74eacc63f94637e92ece667a21"
OLDER = "1a409e751a409e751a409e751a409e751a409e75"
MARKER = f"<!-- claude-review-verdict head={HEAD} verdict=CHANGES_REQUESTED -->"


def payload(id, login="claude[bot]", commit_id=HEAD, in_reply_to_id=None, body="finding"):
    return {
        "id": id,
        "user": None if login is None else {"login": login},
        "path": "core/src/ufo/serve.py",
        "line": 42,
        "commit_id": commit_id,
        "in_reply_to_id": in_reply_to_id,
        "body": body,
    }


def verdict_payload(state="CHANGES_REQUESTED", login="claude[bot]", body="Four blocking findings."):
    return {"user": None if login is None else {"login": login}, "state": state, "body": body}


def note_payload(body=MARKER, login="claude[bot]"):
    return {"user": None if login is None else {"login": login}, "body": body}


def comments(*payloads):
    return tuple(prior.comment(one) for one in payloads)


def reviews(*payloads):
    return tuple(prior.review(one) for one in payloads)


def notes(*payloads):
    return tuple(prior.issue_comment(one) for one in payloads)


def run_main(monkeypatch, run):
    monkeypatch.setattr(prior.subprocess, "run", run)
    return prior.main(["prior_findings.py", "metalcraftai/ufo", "713"])


def responding(stdout, reviews_stdout="[]", notes_stdout="[]"):
    def run(argv, **_kwargs):
        page = {"reviews": reviews_stdout, "issues": notes_stdout}.get(_kind(argv[2]), stdout)
        return subprocess.CompletedProcess(argv, 0, stdout=page)

    return run


def _kind(path):
    if path.endswith("/reviews"):
        return "reviews"
    return "issues" if "/issues/" in path else "comments"


def raising(error):
    def run(*_args, **_kwargs):
        raise error

    return run


def raising_on(kind, error):
    def run(argv, **_kwargs):
        if _kind(argv[2]) == kind:
            raise error
        return subprocess.CompletedProcess(argv, 0, stdout="[]")

    return run


def test_no_claude_comments_is_a_first_review():
    assert prior.prior_round((), (), ()) == prior.PriorRound(
        rounds=0, anchor_sha=None, verdicts=(), findings=()
    )


def test_another_author_alone_does_not_make_it_a_follow_up():
    result = prior.prior_round(
        comments(
            payload(1, login="marshall-ufo", body="drive-by note"),
            payload(2, login="dependabot[bot]"),
        ),
        reviews(verdict_payload(login="marshall-ufo")),
        (),
    )

    assert result.rounds == 0
    assert result.findings == ()
    assert result.verdicts == ()
    assert result.anchor_sha is None


def test_a_claude_finding_makes_it_a_follow_up_and_anchors_the_range():
    result = prior.prior_round(
        comments(payload(1, commit_id=OLDER), payload(2, commit_id=HEAD)), (), ()
    )

    assert result.rounds == 1
    assert result.anchor_sha == HEAD
    assert [finding.commit_id for finding in result.findings] == [OLDER, HEAD]


def test_replies_pair_onto_their_finding_and_never_count_as_findings():
    result = prior.prior_round(
        comments(
            payload(1),
            payload(2, login="marshall-ufo", in_reply_to_id=1, body="fixed in e5dc40f6"),
        ),
        (),
        (),
    )

    assert len(result.findings) == 1
    assert result.findings[0].replies == (
        prior.Reply(author="marshall-ufo", body="fixed in e5dc40f6"),
    )


def test_a_claude_reply_is_a_reply_not_a_second_finding():
    result = prior.prior_round(
        comments(payload(1), payload(2, in_reply_to_id=1, body="still open")), (), ()
    )

    assert len(result.findings) == 1
    assert result.findings[0].replies[0].body == "still open"


def test_a_deleted_account_is_no_author():
    assert prior.prior_round(comments(payload(1, login=None)), (), ()).rounds == 0


@pytest.mark.parametrize("login", ["claude", "Claude[bot]", "CLAUDE[BOT]"])
def test_claude_is_recognized_regardless_of_case(login):
    assert prior.prior_round(comments(payload(1, login=login)), (), ()).rounds == 1


def test_each_decisive_claude_review_is_one_round():
    result = prior.prior_round(
        (),
        reviews(
            verdict_payload(body="Five blocking findings."),
            verdict_payload(body="Four blocking findings."),
        ),
        (),
    )

    assert result.rounds == 2
    assert list(result.verdicts) == ["Five blocking findings.", "Four blocking findings."]


def test_a_bodiless_verdict_still_counts_its_round():
    result = prior.prior_round((), reviews(verdict_payload(state="APPROVED", body=None)), ())

    assert result.rounds == 1
    assert result.verdicts == ("",)


def test_two_rounds_that_said_the_same_thing_are_two_rounds():
    result = prior.prior_round(
        (),
        reviews(verdict_payload(body="Two blocking."), verdict_payload(body="Two blocking.")),
        (),
    )

    assert result.rounds == 2
    assert result.verdicts == ("Two blocking.", "Two blocking.")


def test_an_approval_is_a_round_too():
    assert prior.prior_round((), reviews(verdict_payload(state="APPROVED")), ()).rounds == 1


def test_a_dismissed_verdict_still_counts_its_round():
    assert prior.prior_round((), reviews(verdict_payload(state="DISMISSED")), ()).rounds == 1


def test_the_inline_comment_reviews_are_not_rounds():
    result = prior.prior_round(
        (),
        reviews(
            verdict_payload(state="COMMENTED", body="one inline finding"),
            verdict_payload(state="COMMENTED", body="another inline finding"),
            verdict_payload(state="CHANGES_REQUESTED", body="Six blocking findings."),
        ),
        (),
    )

    assert result.rounds == 1
    assert list(result.verdicts) == ["Six blocking findings."]


def test_another_reviewer_verdict_is_not_a_claude_round():
    result = prior.prior_round(
        (), reviews(verdict_payload(login="marshall-ufo", state="APPROVED")), ()
    )

    assert result.rounds == 0
    assert result.verdicts == ()


def test_a_published_finding_is_a_round_even_with_no_decisive_review():
    result = prior.prior_round(
        comments(payload(1)), reviews(verdict_payload(state="COMMENTED")), ()
    )

    assert result.rounds == 1
    assert result.verdicts == ()


def test_a_marker_comment_is_the_round_github_refused_as_a_review():
    result = prior.prior_round((), (), notes(note_payload()))

    assert result.rounds == 1
    assert result.verdicts == ()


def test_marker_rounds_add_to_the_decisive_ones():
    result = prior.prior_round(
        (), reviews(verdict_payload()), notes(note_payload(), note_payload())
    )

    assert result.rounds == 3
    assert list(result.verdicts) == ["Four blocking findings."]


@pytest.mark.parametrize(
    "body",
    [
        f"**Claude finished the task** quoting {MARKER} in a progress note",
        f"{MARKER} plus a trailing sentence",
        f"Reviewed at head {HEAD}. GitHub does not allow this account to submit a review.",
        "no marker at all",
    ],
)
def test_a_comment_that_only_mentions_the_marker_is_not_a_round(body):
    assert prior.prior_round((), (), notes(note_payload(body=body))).rounds == 0


def test_a_marker_from_another_author_is_not_a_claude_round():
    assert prior.prior_round((), (), notes(note_payload(login="marshall-ufo"))).rounds == 0


def test_a_marker_survives_surrounding_whitespace():
    assert prior.prior_round((), (), notes(note_payload(body=f"\n  {MARKER}  \n"))).rounds == 1


def test_an_approved_marker_counts_its_round_too():
    approved = MARKER.replace("CHANGES_REQUESTED", "APPROVED")

    assert prior.prior_round((), (), notes(note_payload(body=approved))).rounds == 1


@pytest.mark.parametrize(
    "body",
    [
        f"<!-- claude-review-verdict head={HEAD} verdict=CHANGES_REQUESTED -->",
        f"<!-- claude-review-verdict head={HEAD} verdict=APPROVED -->",
        f"  <!-- claude-review-verdict head={HEAD} verdict=APPROVED -->  ",
        f"<!-- claude-review-verdict head={HEAD} verdict=APPROVED --> and more",
        f"<!-- claude-review-verdict head={HEAD[:8]} verdict=APPROVED -->",
        f"<!-- claude-review-verdict head={HEAD} verdict=MAYBE -->",
        "unrelated comment",
    ],
)
def test_the_marker_shape_this_module_counts_is_the_shape_the_gate_accepts(body):
    counted = prior.prior_round((), (), notes(note_payload(body=body))).rounds == 1
    gated = gate.marker_verdict(body, HEAD) is not None

    assert counted == gated


def test_an_unreadable_issue_comment_raises_unusable():
    with pytest.raises(prior.Unusable):
        prior.issue_comment({"user": "not-an-object", "body": ""})


def test_an_issue_comment_with_no_body_is_readable():
    assert prior.issue_comment({"user": {"login": "claude[bot]"}, "body": None}).body == ""


def test_a_verdict_state_is_read_case_insensitively():
    assert prior.review(verdict_payload(state="changes_requested")).state == "CHANGES_REQUESTED"


def test_a_verdict_from_a_deleted_account_is_no_round():
    assert prior.prior_round((), reviews(verdict_payload(login=None)), ()).rounds == 0


def test_an_empty_verdict_body_is_readable():
    assert prior.review(verdict_payload() | {"body": None}).body == ""


@pytest.mark.parametrize(
    "node",
    [
        {"user": {"login": "claude[bot]"}},
        {"user": "not-an-object", "state": "APPROVED"},
        {"user": {"login": "claude[bot]"}, "state": 7},
    ],
)
def test_an_unreadable_review_raises_unusable(node):
    with pytest.raises(prior.Unusable):
        prior.review(node)


def test_a_review_with_no_user_at_all_is_no_author():
    assert prior.review({"state": "APPROVED"}).author == ""


def test_a_line_falls_back_to_the_original_line():
    node = payload(1) | {"line": None, "original_line": 7}

    assert prior.comment(node).line == 7


def test_an_anchor_falls_back_to_the_original_commit_id():
    node = payload(1) | {"commit_id": None, "original_commit_id": OLDER}

    assert prior.comment(node).commit_id == OLDER


def test_a_live_commit_id_wins_over_the_original():
    node = payload(1, commit_id=HEAD) | {"original_commit_id": OLDER}

    assert prior.comment(node).commit_id == HEAD


def test_a_finding_with_no_anchor_at_all_leaves_the_range_unset():
    node = payload(1) | {"commit_id": None, "original_commit_id": None}
    result = prior.prior_round((prior.comment(node),), (), ())

    assert result.rounds == 1
    assert result.anchor_sha is None


def test_the_anchor_is_the_newest_finding_that_has_one():
    older = payload(1, commit_id=OLDER)
    unanchored = payload(2) | {"commit_id": None, "original_commit_id": None}

    assert prior.prior_round(comments(older, unanchored), (), ()).anchor_sha == OLDER


def test_a_non_string_anchor_is_unusable():
    node = payload(1) | {"commit_id": 12345}

    with pytest.raises(prior.Unusable):
        prior.comment(node)


@pytest.mark.parametrize(
    "node",
    [
        {"body": "no id here"},
        {"id": "not-an-int", "path": "a.py", "body": ""},
        {"id": True, "path": "a.py", "body": ""},
        {"id": 1, "user": "not-an-object", "path": "a.py", "body": ""},
        {"id": 1, "path": 7, "body": ""},
        {"id": 1, "path": "a.py", "body": "", "in_reply_to_id": "not-an-int"},
    ],
)
def test_an_unreadable_comment_raises_unusable(node):
    with pytest.raises(prior.Unusable):
        prior.comment(node)


def test_a_non_object_element_raises_unusable_rather_than_attribute_error():
    with pytest.raises(prior.Unusable):
        prior.json_object("not-a-dict", "comment")


@pytest.mark.parametrize(
    "error",
    [
        subprocess.CalledProcessError(1, "gh", stderr="HTTP 403"),
        subprocess.TimeoutExpired("gh", prior.FETCH_TIMEOUT_SECONDS),
        FileNotFoundError(2, "No such file or directory: 'gh'"),
        PermissionError(13, "Permission denied"),
        UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "invalid start byte"),
    ],
)
def test_a_failed_call_leaves_the_round_count_unknown_never_a_first_review(
    monkeypatch, capsys, error
):
    assert run_main(monkeypatch, raising(error)) == 0
    assert json.loads(capsys.readouterr().out)["rounds"] is None


@pytest.mark.parametrize("kind", ["comments", "reviews", "issues"])
def test_any_endpoint_failing_leaves_the_history_unknown(monkeypatch, capsys, kind):
    error = subprocess.CalledProcessError(1, "gh", stderr="HTTP 502")

    assert run_main(monkeypatch, raising_on(kind, error)) == 0
    assert json.loads(capsys.readouterr().out)["rounds"] is None


def test_undecodable_bytes_from_gh_leave_the_round_count_unknown(monkeypatch, capsys):
    real_run = subprocess.run

    def emit_invalid_bytes(*_args, **_kwargs):
        return real_run(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(bytes([0xff, 0xfe]))"],
            capture_output=True,
            text=True,
            check=True,
        )

    assert run_main(monkeypatch, emit_invalid_bytes) == 0
    assert json.loads(capsys.readouterr().out)["rounds"] is None


@pytest.mark.parametrize(
    "stdout",
    [
        '{"message": "Not Found"}',
        "not json at all",
        '["not-a-dict"]',
        '[{"body": "no id here"}]',
        '[{"id": 1, "user": "not-an-object", "path": "a.py", "body": ""}]',
    ],
)
def test_an_unusable_payload_is_undetermined_not_a_traceback(monkeypatch, capsys, stdout):
    assert run_main(monkeypatch, responding(stdout)) == 0
    assert json.loads(capsys.readouterr().out)["rounds"] is None


@pytest.mark.parametrize(
    "reviews_stdout",
    [
        '{"message": "Not Found"}',
        "not json at all",
        '["not-a-dict"]',
        '[{"state": 7}]',
        '[{"state": "APPROVED", "body": 7}]',
    ],
)
def test_an_unusable_reviews_payload_is_undetermined_not_a_traceback(
    monkeypatch, capsys, reviews_stdout
):
    assert run_main(monkeypatch, responding("[]", reviews_stdout)) == 0
    assert json.loads(capsys.readouterr().out)["rounds"] is None


@pytest.mark.parametrize(
    "notes_stdout",
    [
        '{"message": "Not Found"}',
        "not json at all",
        '["not-a-dict"]',
        '[{"user": "nope"}]',
        '[{"body": 7}]',
    ],
)
def test_an_unusable_issue_comments_payload_is_undetermined_not_a_traceback(
    monkeypatch, capsys, notes_stdout
):
    assert run_main(monkeypatch, responding("[]", "[]", notes_stdout)) == 0
    assert json.loads(capsys.readouterr().out)["rounds"] is None


def test_the_marker_rounds_reach_the_printed_count(monkeypatch, capsys):
    run = responding("[]", "[]", json.dumps([note_payload(), note_payload()]))

    assert run_main(monkeypatch, run) == 0
    printed = json.loads(capsys.readouterr().out)

    assert printed["rounds"] == 2
    assert printed["verdicts"] == []


def test_the_fetch_reads_all_three_endpoints_unfiltered_and_bounded(monkeypatch):
    seen = []

    def record(argv, **kwargs):
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="[]")

    monkeypatch.setattr(prior.subprocess, "run", record)
    assert prior.fetch("metalcraftai/ufo", "713") == ((), (), ())

    assert [argv[2] for argv, _ in seen] == [
        "repos/metalcraftai/ufo/pulls/713/comments",
        "repos/metalcraftai/ufo/pulls/713/reviews",
        "repos/metalcraftai/ufo/issues/713/comments",
    ]
    for argv, kwargs in seen:
        assert "--paginate" in argv
        assert not {"-q", "--jq", "--slurp"} & set(argv)
        assert kwargs["timeout"] == prior.FETCH_TIMEOUT_SECONDS
        assert kwargs["check"] is True


def test_a_successful_run_prints_the_findings_and_the_round_count(monkeypatch, capsys):
    run = responding(json.dumps([payload(1)]), json.dumps([verdict_payload()]))

    assert run_main(monkeypatch, run) == 0
    printed = json.loads(capsys.readouterr().out)

    assert printed["rounds"] == 1
    assert printed["anchor_sha"] == HEAD
    assert printed["findings"][0]["path"] == "core/src/ufo/serve.py"
    assert printed["verdicts"] == ["Four blocking findings."]


def test_usage_is_an_error_not_an_empty_round(capsys):
    assert prior.main(["prior_findings.py"]) == 2
    assert capsys.readouterr().out == ""
