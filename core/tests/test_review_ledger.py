import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]


def _load(name):
    path = _ROOT / ".github" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


prior = _load("prior_findings")
ledger = _load("review_ledger")

HEAD = "9426a25a359f1f74eacc63f94637e92ece667a21"
QUEUE = "core/src/ufo/loop/queue.py"
LEDGER_BODY = f"{ledger.LEDGER_MARKER}\n\n{ledger.LEDGER_HEADER}"


def finding(
    id,
    marker="F1",
    body="the drain stacks a second window on every reseed",
    login="claude[bot]",
    line=210,
):
    return {
        "id": id,
        "user": None if login is None else {"login": login},
        "path": QUEUE,
        "line": line,
        "body": f"{marker} — {body}\n\n<!-- claude-finding id={marker} -->" if marker else body,
        "in_reply_to_id": None,
    }


def reply(id, in_reply_to_id, body, login="marshall-ufo"):
    return finding(id, marker="", body=body, login=login) | {"in_reply_to_id": in_reply_to_id}


def note(id, body, login="marshall-ufo"):
    return {
        "id": id,
        "user": None if login is None else {"login": login},
        "body": body,
    }


def comments(*payloads):
    return tuple(ledger.comment(one) for one in payloads)


def notes(*payloads):
    return tuple(ledger.note(one) for one in payloads)


def build(inline=(), pr_comments=()):
    return ledger.ledger(comments(*inline), notes(*pr_comments))


def states(result):
    return {row.id: (row.state, row.evidence) for row in result.rows}


def run_main(monkeypatch, run):
    monkeypatch.setattr(ledger.subprocess, "run", run)
    return ledger.main(["review_ledger.py", "metalcraftai/ufo", "960"])


def responding(stdout, notes_stdout="[]"):
    def run(argv, **_kwargs):
        page = notes_stdout if _kind(argv[2]) == "issues" else stdout
        return subprocess.CompletedProcess(argv, 0, stdout=page)

    return run


def _kind(path):
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


def test_a_pull_request_with_no_findings_has_an_empty_ledger_and_the_first_id():
    result = build()

    assert result.rows == ()
    assert result.next_id == "F1"
    assert result.comment_id is None
    assert result.body == LEDGER_BODY


def test_a_finding_keeps_the_id_its_comment_carries():
    result = build(inline=(finding(1, marker="F7"),))

    assert [row.id for row in result.rows] == ["F7"]
    assert result.next_id == "F8"


def test_a_quoted_marker_cannot_claim_the_trailing_finding_id():
    result = build(
        inline=(
            finding(1, marker="F7"),
            finding(
                2,
                marker="F8",
                body="the message quotes <!-- claude-finding id=F7 --> before its own marker",
            ),
        )
    )

    assert [row.id for row in result.rows] == ["F7", "F8"]
    assert result.next_id == "F9"


def test_ids_hold_across_rounds_as_findings_are_added():
    first = build(inline=(finding(1, marker="F1"), finding(2, marker="F2")))
    later = build(
        inline=(finding(1, marker="F1"), finding(2, marker="F2"), finding(3, marker="F3"))
    )

    assert [row.id for row in first.rows] == ["F1", "F2"]
    assert [row.id for row in later.rows] == ["F1", "F2", "F3"]
    assert later.next_id == "F4"


def test_an_id_is_never_renumbered_when_an_earlier_finding_is_unmarked():
    result = build(inline=(finding(1, marker=""), finding(2, marker="F1"), finding(3, marker="")))

    assert [row.id for row in result.rows] == ["F2", "F1", "F3"]
    assert result.next_id == "F4"


def test_ids_recover_from_thread_creation_order_with_the_ledger_comment_deleted():
    result = build(inline=(finding(1, marker=""), finding(2, marker=""), finding(3, marker="")))

    assert result.comment_id is None
    assert [row.id for row in result.rows] == ["F1", "F2", "F3"]


def test_the_ledger_comment_is_the_one_carrying_the_marker():
    result = build(
        inline=(finding(1),),
        pr_comments=(note(11, "looks close"), note(12, LEDGER_BODY, login="claude[bot]")),
    )

    assert result.comment_id == 12


def test_a_ledger_shaped_comment_from_another_author_is_not_the_ledger():
    assert build(pr_comments=(note(11, LEDGER_BODY),)).comment_id is None


@pytest.mark.parametrize(
    "body",
    [
        f"> {ledger.LEDGER_MARKER}\n>\n> {ledger.LEDGER_HEADER}",
        f"progress\n\n{LEDGER_BODY}",
        ledger.LEDGER_MARKER,
        f"{ledger.LEDGER_MARKER}\n\n| ID | subject |\n| --- | --- |",
    ],
)
def test_a_quoted_or_planted_marker_is_not_the_patch_target(body):
    assert build(pr_comments=(note(11, body, login="claude[bot]"),)).comment_id is None


def test_the_earliest_qualifying_ledger_comment_is_the_one_edited():
    result = build(
        pr_comments=(
            note(11, LEDGER_BODY, login="claude[bot]"),
            note(12, LEDGER_BODY, login="claude[bot]"),
        )
    )

    assert result.comment_id == 11


def test_one_id_published_twice_folds_into_one_row_anchored_at_the_first():
    result = build(
        inline=(
            finding(1, marker="F1", body="the drain stacks a second window"),
            finding(2, marker="F1", body="still stacking a window", line=240),
        )
    )

    assert [(row.id, row.subject, row.location) for row in result.rows] == [
        ("F1", "the drain stacks a second window", f"{QUEUE}:210")
    ]
    assert result.next_id == "F2"


def test_an_act_on_a_later_thread_settles_the_one_row_that_id_has():
    result = build(
        inline=(
            finding(1, marker="F1"),
            finding(2, marker="F1", line=240),
            reply(3, 2, f"verified fixed: {HEAD}", login="claude[bot]"),
        )
    )

    assert states(result) == {"F1": ("done", HEAD)}


def test_only_claudes_thread_openers_are_findings():
    result = build(
        inline=(
            finding(1, login="marshall-ufo", marker=""),
            finding(2),
            reply(3, 2, "fixed in e5dc40f6"),
        )
    )

    assert [row.id for row in result.rows] == ["F1"]
    assert result.rows[0].location == f"{QUEUE}:210"


def test_a_row_with_no_act_on_it_is_todo_with_no_evidence():
    assert states(build(inline=(finding(1),)))["F1"] == ("todo", "")


def test_the_reviewers_verification_moves_the_row_to_done_with_the_commit():
    result = build(inline=(finding(1), reply(2, 1, f"verified fixed: {HEAD}", login="claude[bot]")))

    assert states(result)["F1"] == ("done", HEAD)


def test_a_verification_note_without_a_target_moves_no_row():
    result = build(
        inline=(finding(1, marker="F1"), finding(2, marker="F2")),
        pr_comments=(note(11, f"verified fixed: {HEAD} for F1; F2 is still open", login="claude"),),
    )

    assert states(result) == {"F1": ("todo", ""), "F2": ("todo", "")}


def test_a_pull_request_note_cannot_settle_a_thread_even_with_its_id():
    result = build(
        inline=(finding(1, marker="F1"),),
        pr_comments=(note(11, f"F1 — verified fixed: {HEAD}", login="claude[bot]"),),
    )

    assert states(result)["F1"] == ("todo", "")


def test_a_reply_citing_another_id_does_not_verify_its_thread():
    result = build(
        inline=(
            finding(1, marker="F1"),
            reply(2, 1, f"F2 — verified fixed: {HEAD}", login="claude"),
        )
    )

    assert states(result)["F1"] == ("todo", "")


def test_an_authors_claim_of_a_fix_is_not_a_verification():
    result = build(inline=(finding(1), reply(2, 1, f"verified fixed: {HEAD}")))

    assert states(result)["F1"] == ("todo", "")


@pytest.mark.parametrize(
    "body",
    [
        f"F1 — verified fixed: {HEAD}",
        f"F1 - verified fixed: {HEAD}",
        f"verified fixed: {HEAD}",
    ],
)
def test_a_token_opens_the_comment_after_the_id_a_reply_cites(body):
    result = build(inline=(finding(1), reply(2, 1, body, login="claude[bot]")))

    assert states(result)["F1"] == ("done", HEAD)


@pytest.mark.parametrize(
    "body",
    [
        f"Confirmed. verified fixed: {HEAD}",
        f"Round 3 verified fixed: {HEAD}",
    ],
)
def test_a_token_the_comment_does_not_open_with_moves_nothing(body):
    result = build(inline=(finding(1), reply(2, 1, body, login="claude[bot]")))

    assert states(result)["F1"] == ("todo", "")


def test_a_subject_is_cut_to_eight_words_and_escapes_the_cell_separator():
    result = build(inline=(finding(1, body="one two three four five six seven eight nine ten"),))

    assert result.rows[0].subject == "one two three four five six seven eight"
    assert build(inline=(finding(1, body="a | b"),)).rows[0].subject == r"a \| b"


def test_every_quoted_finding_marker_is_absent_from_the_subject():
    body = "the message quotes <!-- claude-finding id=F7 --> before its own finding"

    assert build(inline=(finding(1, marker="F8", body=body),)).rows[0].subject == (
        "the message quotes before its own finding"
    )


def test_a_finding_on_a_line_the_head_dropped_locates_by_its_original_line():
    node = finding(1) | {"line": None, "original_line": 7}

    assert ledger.comment(node).line == 7
    assert build(inline=(node,)).rows[0].location == f"{QUEUE}:7"


def test_a_finding_with_no_line_locates_by_its_file():
    node = finding(1) | {"line": None, "original_line": None}

    assert build(inline=(node,)).rows[0].location == QUEUE


def test_the_rendered_ledger_is_one_table_under_the_marker():
    result = build(
        inline=(
            finding(1, marker="F1", body="the drain stacks a second window"),
            reply(2, 1, f"verified fixed: {HEAD}", login="claude[bot]"),
        )
    )

    assert result.body.splitlines()[0] == ledger.LEDGER_MARKER
    assert result.body.splitlines()[2] == "| ID | subject | file:line | state | evidence |"
    assert result.body.splitlines()[4] == (
        f"| F1 | the drain stacks a second window | {QUEUE}:210 | done | {HEAD} |"
    )


@pytest.mark.parametrize(
    "node",
    [
        {"body": "no id here"},
        {"id": "not-an-int", "path": "a.py", "body": ""},
        {"id": 1, "user": "not-an-object", "path": "a.py", "body": ""},
        {"id": 1, "path": 7, "body": ""},
        {"id": 1, "path": "a.py", "body": "", "in_reply_to_id": "not-an-int"},
    ],
)
def test_an_unreadable_comment_raises_unusable(node):
    with pytest.raises(ledger.Unusable):
        ledger.comment(node)


@pytest.mark.parametrize(
    "node",
    [{"body": "no id"}, {"id": True, "body": ""}, {"id": 1, "body": 7}, {"id": 1, "user": "nope"}],
)
def test_an_unreadable_pull_request_comment_raises_unusable(node):
    with pytest.raises(ledger.Unusable):
        ledger.note(node)


@pytest.mark.parametrize(
    "error",
    [
        subprocess.CalledProcessError(1, "gh", stderr="HTTP 403"),
        subprocess.TimeoutExpired("gh", 120),
        FileNotFoundError(2, "No such file or directory: 'gh'"),
        PermissionError(13, "Permission denied"),
        UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "invalid start byte"),
    ],
)
def test_a_failed_fetch_leaves_the_rows_unknown_never_a_clean_slate(monkeypatch, capsys, error):
    assert run_main(monkeypatch, raising(error)) == 0
    printed = json.loads(capsys.readouterr().out)

    assert printed["rows"] is None
    assert printed["body"] is None
    assert printed["next_id"] is None


@pytest.mark.parametrize("kind", ["comments", "issues"])
def test_any_endpoint_failing_leaves_the_ledger_unknown(monkeypatch, capsys, kind):
    error = subprocess.CalledProcessError(1, "gh", stderr="HTTP 502")

    assert run_main(monkeypatch, raising_on(kind, error)) == 0
    assert json.loads(capsys.readouterr().out)["rows"] is None


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
def test_an_unusable_comments_payload_is_undetermined_not_a_traceback(monkeypatch, capsys, stdout):
    assert run_main(monkeypatch, responding(stdout)) == 0
    assert json.loads(capsys.readouterr().out)["rows"] is None


@pytest.mark.parametrize(
    "notes_stdout", ['{"message": "Not Found"}', '["not-a-dict"]', '[{"body": "no id"}]']
)
def test_an_unusable_pull_request_comments_payload_is_undetermined(
    monkeypatch, capsys, notes_stdout
):
    assert run_main(monkeypatch, responding("[]", notes_stdout)) == 0
    assert json.loads(capsys.readouterr().out)["rows"] is None


def test_the_fetch_reads_both_endpoints_unfiltered_and_bounded(monkeypatch):
    seen = []

    def record(argv, **kwargs):
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="[]")

    monkeypatch.setattr(ledger.subprocess, "run", record)
    assert ledger.fetch("metalcraftai/ufo", "960") == ((), ())

    assert [argv[2] for argv, _ in seen] == [
        "repos/metalcraftai/ufo/pulls/960/comments",
        "repos/metalcraftai/ufo/issues/960/comments",
    ]
    for argv, kwargs in seen:
        assert "--paginate" in argv
        assert not {"-q", "--jq", "--slurp"} & set(argv)
        assert kwargs["timeout"] == prior.FETCH_TIMEOUT_SECONDS


def test_a_successful_run_prints_the_rows_and_the_ledger(monkeypatch, capsys):
    run = responding(
        json.dumps(
            [
                finding(1, marker="F1"),
                reply(2, 1, f"verified fixed: {HEAD}", login="claude[bot]"),
            ]
        ),
        json.dumps([note(11, LEDGER_BODY, login="claude[bot]")]),
    )

    assert run_main(monkeypatch, run) == 0
    printed = json.loads(capsys.readouterr().out)

    assert printed["comment_id"] == 11
    assert printed["next_id"] == "F2"
    assert printed["rows"][0]["state"] == "done"
    assert printed["body"].startswith(ledger.LEDGER_MARKER)


def test_usage_is_an_error_not_an_empty_ledger(capsys):
    assert ledger.main(["review_ledger.py"]) == 2
    assert capsys.readouterr().out == ""
