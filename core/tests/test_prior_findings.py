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

HEAD = "9426a25a359f1f74eacc63f94637e92ece667a21"
OLDER = "1a409e751a409e751a409e751a409e751a409e75"


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


def comments(*payloads):
    return tuple(prior.comment(one) for one in payloads)


def run_main(monkeypatch, run):
    monkeypatch.setattr(prior.subprocess, "run", run)
    return prior.main(["prior_findings.py", "metalcraftai/ufo", "713"])


def responding(stdout):
    def run(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, stdout=stdout)

    return run


def raising(error):
    def run(*_args, **_kwargs):
        raise error

    return run


def test_no_claude_comments_is_a_first_review():
    assert prior.prior_round(()) == prior.PriorRound(round="first", anchor_sha=None, findings=())


def test_another_author_alone_does_not_make_it_a_follow_up():
    result = prior.prior_round(
        comments(
            payload(1, login="marshall-ufo", body="drive-by note"),
            payload(2, login="dependabot[bot]"),
        )
    )

    assert result.round == "first"
    assert result.findings == ()
    assert result.anchor_sha is None


def test_a_claude_finding_makes_it_a_follow_up_and_anchors_the_range():
    result = prior.prior_round(comments(payload(1, commit_id=OLDER), payload(2, commit_id=HEAD)))

    assert result.round == "follow_up"
    assert result.anchor_sha == HEAD
    assert [finding.commit_id for finding in result.findings] == [OLDER, HEAD]


def test_replies_pair_onto_their_finding_and_never_count_as_findings():
    result = prior.prior_round(
        comments(
            payload(1),
            payload(2, login="marshall-ufo", in_reply_to_id=1, body="fixed in e5dc40f6"),
        )
    )

    assert len(result.findings) == 1
    assert result.findings[0].replies == (
        prior.Reply(author="marshall-ufo", body="fixed in e5dc40f6"),
    )


def test_a_claude_reply_is_a_reply_not_a_second_finding():
    result = prior.prior_round(
        comments(payload(1), payload(2, in_reply_to_id=1, body="still open"))
    )

    assert len(result.findings) == 1
    assert result.findings[0].replies[0].body == "still open"


def test_a_deleted_account_is_no_author():
    assert prior.prior_round(comments(payload(1, login=None))).round == "first"


@pytest.mark.parametrize("login", ["claude", "Claude[bot]", "CLAUDE[BOT]"])
def test_claude_is_recognized_regardless_of_case(login):
    assert prior.prior_round(comments(payload(1, login=login))).round == "follow_up"


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
    result = prior.prior_round((prior.comment(node),))

    assert result.round == "follow_up"
    assert result.anchor_sha is None


def test_the_anchor_is_the_newest_finding_that_has_one():
    older = payload(1, commit_id=OLDER)
    unanchored = payload(2) | {"commit_id": None, "original_commit_id": None}

    assert prior.prior_round(comments(older, unanchored)).anchor_sha == OLDER


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
def test_a_failed_call_is_undetermined_never_a_first_review(monkeypatch, capsys, error):
    assert run_main(monkeypatch, raising(error)) == 0
    assert json.loads(capsys.readouterr().out)["round"] == "undetermined"


def test_undecodable_bytes_from_gh_are_undetermined(monkeypatch, capsys):
    real_run = subprocess.run

    def emit_invalid_bytes(*_args, **_kwargs):
        return real_run(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(bytes([0xff, 0xfe]))"],
            capture_output=True,
            text=True,
            check=True,
        )

    assert run_main(monkeypatch, emit_invalid_bytes) == 0
    assert json.loads(capsys.readouterr().out)["round"] == "undetermined"


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
    assert json.loads(capsys.readouterr().out)["round"] == "undetermined"


def test_the_fetch_paginates_unfiltered_and_bounded(monkeypatch):
    seen = {}

    def record(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        return subprocess.CompletedProcess(argv, 0, stdout="[]")

    monkeypatch.setattr(prior.subprocess, "run", record)
    assert prior.fetch("metalcraftai/ufo", "713") == ()

    assert "--paginate" in seen["argv"]
    assert not {"-q", "--jq", "--slurp"} & set(seen["argv"])
    assert seen["kwargs"]["timeout"] == prior.FETCH_TIMEOUT_SECONDS
    assert seen["kwargs"]["check"] is True


def test_a_successful_run_prints_the_findings(monkeypatch, capsys):
    assert run_main(monkeypatch, responding(json.dumps([payload(1)]))) == 0
    printed = json.loads(capsys.readouterr().out)

    assert printed["round"] == "follow_up"
    assert printed["anchor_sha"] == HEAD
    assert printed["findings"][0]["path"] == "core/src/ufo/serve.py"


def test_usage_is_an_error_not_an_empty_round(capsys):
    assert prior.main(["prior_findings.py"]) == 2
    assert capsys.readouterr().out == ""
