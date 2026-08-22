"""Reading a run archive's recorded handoffs: which children finished terse, which restated their
report, and which moved it into a file the case never asked for. The archive is validated as an
`EvalRun` and every row as the recorder's own dump, so a field either of them renames stops this
reader rather than defaulting a check off."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from evals.coding_repo.cases import CASES
from evals.coding_repo.handoff import (
    MAX_CLOSING_CHARS,
    MAX_DUPLICATION,
    METRIC_NAME,
    handoffs_in,
)
from evals.coding_repo.handoff import main as handoff_main
from evals.coding_repo.runner import ANSWERS_TASK, DELIVERABLES_TASK
from evals.harness.handoff import handoff_record
from evals.harness.harness import EvalCaseResult, EvalReport, Json
from evals.harness.viewer import EvalRun
from ufo.sdk.models import Message, TextBlock, ToolUseBlock

CONVERSATION = uuid4()
REPLY_CASE = next(case for case in CASES if case.deliverable == "reply")
PATCH_CASE = next(case for case in CASES if case.deliverable == "patch")
SURVEY_CASE = next(case for case in CASES if case.deliverable == "document")
REPORT = (
    "I fixed the journal mode by sealing the file at the end of apply_migrations, because alembic "
    "builds its own engine and the connect hook never runs against a migrated file. I added a test "
    "that holds a write transaction open on a byte copy and asks a second connection for the mode."
)
NARRATION = "Running the focused db tests now."
LONG_REPORT = f"{REPORT} {REPORT}"


def document(
    path: str,
    chars: int = 9000,
    reads: int = 2,
    errors: int = 0,
    duplication: float = 0.9,
) -> dict[str, object]:
    return {
        "path": path,
        "chars": chars,
        "reads": reads,
        "errors": errors,
        "duplication": duplication,
    }


def row(
    closing: int, result: int, duplication: float, *documents: dict[str, object]
) -> dict[str, object]:
    return {
        "conversation_id": str(CONVERSATION),
        "closing_chars": closing,
        "result_chars": result,
        "duplication": duplication,
        "documents": list(documents),
    }


def archive(
    tmp_path: Path,
    *handoffs: dict[str, object],
    case: str = REPLY_CASE.name,
    task: str = ANSWERS_TASK,
    name: str = "run-1",
    attempts: Json | None = None,
) -> Path:
    """One run archive built from the harness's own record types, so a renamed `EvalRun` field
    breaks this fixture instead of leaving the reader silently reporting nothing."""
    recorded = [{"handoffs": list(handoffs)}] if attempts is None else attempts
    run = EvalRun(
        id=uuid4(),
        created_at=datetime.now(UTC),
        label=name,
        agent="assistant",
        ufo_version="0",
        revision="0",
        reports=(
            EvalReport(
                name=task,
                suite="capability",
                digest="d",
                cases=(
                    EvalCaseResult(
                        name=case, passed=True, reason="", evidence={"attempts": recorded}
                    ),
                ),
            ),
        ),
    )
    path = tmp_path / f"{name}.json"
    path.write_text(run.model_dump_json(indent=2, by_alias=True, exclude_none=True))
    return path


def test_the_reporter_reads_the_recorders_own_dump(tmp_path: Path) -> None:
    """The row the recorder writes is the row this reads: a child that left one narration line,
    wrote its report to a file, read it back, and returned that report as its payload comes through
    with every number intact — terse by the closing numbers alone, rerouted by all of them."""
    path = "/workspace/findings.md"
    messages = (
        Message(
            role="assistant",
            content=(
                ToolUseBlock(
                    id="w1", name="write", input={"file_path": path, "content": LONG_REPORT}
                ),
            ),
        ),
        Message(
            role="assistant",
            content=(ToolUseBlock(id="r1", name="read", input={"file_path": path}),),
        ),
        Message(role="assistant", content=(TextBlock(text=NARRATION),)),
    )
    recorded = handoff_record(CONVERSATION, messages, LONG_REPORT).model_dump(mode="json")
    (child,) = handoffs_in(archive(tmp_path, recorded))
    assert child.record.closing_chars == len(NARRATION)
    (written,) = child.record.documents
    assert written.path == path
    assert written.chars == len(LONG_REPORT)
    assert written.reads == 1
    assert written.duplication == 1.0
    assert not child.verbose and not child.duplicated
    assert child.rerouted and not child.compliant
    assert child.handoff_chars == len(NARRATION) + 2 * len(LONG_REPORT)


def test_a_renamed_recorder_field_stops_the_reader(tmp_path: Path) -> None:
    """A silently defaulted document row would turn the rerouting check off for every archive while
    every test still passed, so an unknown key is a loud failure."""
    renamed = row(20, 900, 0.0, {"path": "/workspace/x.md", "size": 9000})
    with pytest.raises(ValueError, match="the recorder's own shape"):
        handoffs_in(archive(tmp_path, renamed))
    missing = row(20, 900, 0.0)
    del missing["duplication"]
    with pytest.raises(ValueError, match="the recorder's own shape"):
        handoffs_in(archive(tmp_path, missing))


def test_an_archive_that_is_not_an_eval_run_stops_the_reader(tmp_path: Path) -> None:
    """A run whose fields this reader guessed at would report `no delegated handoff` and exit 3 — a
    false diagnosis of the run rather than of the reader."""
    path = tmp_path / "renamed.json"
    path.write_text(json.dumps({"id": str(uuid4()), "results": []}))
    with pytest.raises(ValueError, match="not an eval run archive"):
        handoffs_in(path)


def test_evidence_without_an_attempt_list_stops_the_reader(tmp_path: Path) -> None:
    """Every capability case records `attempts`; anything else is a recorder this reader no longer
    matches, and reporting no handoff for it would read as a run that never delegated."""
    with pytest.raises(ValueError, match="no attempt list"):
        handoffs_in(archive(tmp_path, attempts="none"))
    with pytest.raises(ValueError, match="not a record"):
        handoffs_in(archive(tmp_path, attempts=["one"]))
    with pytest.raises(ValueError, match="handoffs are not a list"):
        handoffs_in(archive(tmp_path, attempts=[{"handoffs": 3}]))


def test_the_reporter_reads_every_recorded_handoff(tmp_path: Path) -> None:
    run = archive(tmp_path, row(7443, 7349, 0.779), row(120, 900, 0.02))
    found = handoffs_in(run)
    assert [item.record.closing_chars for item in found] == [7443, 120]
    assert [item.case.name for item in found] == [REPLY_CASE.name] * 2


def test_a_verbose_or_duplicating_child_is_not_compliant(tmp_path: Path) -> None:
    verbose, duplicating, terse = handoffs_in(
        archive(
            tmp_path,
            row(MAX_CLOSING_CHARS + 1, 50, 0.0),
            row(100, 4_000, MAX_DUPLICATION + 0.01),
            row(MAX_CLOSING_CHARS, 4_000, MAX_DUPLICATION),
        )
    )
    assert verbose.verbose and not verbose.duplicated and not verbose.compliant
    assert duplicating.duplicated and not duplicating.verbose and not duplicating.compliant
    assert terse.compliant, "a child exactly at both bounds is within them"
    assert verbose.wasted_chars == 1
    assert terse.wasted_chars == 0


def test_a_run_with_no_delegation_records_no_handoff(tmp_path: Path) -> None:
    """The recorder writes `handoffs` as null for an attempt that delegated nothing, so that is the
    one absent shape this reader accepts."""
    assert handoffs_in(archive(tmp_path)) == ()
    assert handoffs_in(archive(tmp_path, attempts=[{"handoffs": None}])) == ()
    assert handoffs_in(archive(tmp_path, attempts=[{"passed": True}])) == ()


def test_only_a_file_the_case_asked_for_escapes_the_rerouting_check(tmp_path: Path) -> None:
    """A patch case asks for its diff and its note and a document case asks for the path in its
    brief, while a reply case asks for no file at all — so `<case>.patch` under a reply case is
    scaffolding."""
    scaffold = document("/workspace/findings.md")
    asked = document(f"/workspace/{PATCH_CASE.notes_name}")
    rerouted, deliverable = handoffs_in(
        archive(
            tmp_path,
            row(20, 900, 0.0, scaffold),
            row(20, 900, 0.0, asked),
            case=PATCH_CASE.name,
            task=DELIVERABLES_TASK,
        )
    )
    assert rerouted.rerouted and not rerouted.compliant
    assert not deliverable.rerouted and deliverable.compliant
    (as_reply,) = handoffs_in(
        archive(tmp_path, row(20, 900, 0.0, document(f"/workspace/{PATCH_CASE.name}.patch")))
    )
    assert as_reply.rerouted, "a reply case asks for no file"
    (survey,) = handoffs_in(
        archive(
            tmp_path,
            row(20, 900, 0.0, document(SURVEY_CASE.document_path)),
            case=SURVEY_CASE.name,
            task=DELIVERABLES_TASK,
        )
    )
    assert survey.compliant, "a document case asks for the path in its brief"


def test_the_note_a_patch_case_asked_for_does_not_hide_the_scaffold_beside_it(
    tmp_path: Path,
) -> None:
    """The regression the check exists to catch, on the six cases that can produce it: a patch
    case's own note is asked-for prose and the largest thing the child wrote, so reading one
    document would report every rerouted patch case as compliant."""
    note = document(f"/workspace/{PATCH_CASE.notes_name}", chars=40_000)
    scaffold = document("/workspace/handoff-summary.md", chars=9_000)
    (child,) = handoffs_in(
        archive(
            tmp_path,
            row(20, 900, 0.0, note, scaffold),
            case=PATCH_CASE.name,
            task=DELIVERABLES_TASK,
        )
    )
    assert [item.path for item in child.rerouted_documents] == ["/workspace/handoff-summary.md"]
    assert child.rerouted and not child.compliant


def test_handoff_chars_bills_the_rerouted_copies_and_not_the_deliverable(tmp_path: Path) -> None:
    """A contract that only forbids the final message moves bytes into a document, so every copy of
    the report counts. The deliverable the case asked for is the work, not the cost of handing it
    over, so its bytes are not billed."""
    note = document(f"/workspace/{PATCH_CASE.notes_name}", chars=40_000)
    scaffold = document("/workspace/handoff-summary.md", chars=9_000)
    (child,) = handoffs_in(
        archive(
            tmp_path,
            row(3781, 3742, 0.88, note, scaffold),
            case=PATCH_CASE.name,
            task=DELIVERABLES_TASK,
        )
    )
    assert child.handoff_chars == 3781 + 3742 + 9_000
    assert child.verbose and child.duplicated
    (pointed_at,) = handoffs_in(
        archive(
            tmp_path,
            row(3781, 3742, 0.88, document("/workspace/findings-b.md", duplication=0.0, errors=1)),
        )
    )
    assert pointed_at.handoff_chars == 3781 + 3742
    assert not pointed_at.rerouted, "the payload points at the document rather than restating it"


def test_the_reporter_reads_only_this_suites_reports(tmp_path: Path) -> None:
    run = archive(tmp_path, row(9000, 9000, 0.9), case="some-other-case", task="web_research")
    assert handoffs_in(run) == ()


def test_a_case_the_corpus_does_not_name_fails_loud(tmp_path: Path) -> None:
    """A silent True would disable the rerouted check for every child of an unknown case."""
    run = archive(
        tmp_path,
        row(9000, 900, 0.9, document("/workspace/x.md")),
        case="not-a-case",
        task=DELIVERABLES_TASK,
    )
    with pytest.raises(ValueError, match="not a coding_repo case"):
        handoffs_in(run)


def test_the_reporter_prints_a_verdict_and_the_metric(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    archive(
        run_dir,
        row(20, 900, 0.0, document(f"/workspace/{PATCH_CASE.notes_name}")),
        row(20, 900, 0.0, document("/workspace/handoff-summary.md", chars=7_000)),
        case=PATCH_CASE.name,
        task=DELIVERABLES_TASK,
        name="run-3",
    )
    handoff_main(["--runs", str(run_dir), "--metric-stdout"])
    printed = capsys.readouterr().out
    assert "compliant\n" in printed
    assert "rerouted-to-file [handoff-summary.md 7000c reads=2 errs=0 dup=90%]" in printed
    assert f"{METRIC_NAME}: 0.5000" in printed


def test_a_run_with_no_recorded_handoff_reports_no_metric(tmp_path: Path) -> None:
    """Absent data is not compliance, so it exits non-zero rather than printing a perfect score."""
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    archive(run_dir, case=REPLY_CASE.name, name="run-4")
    with pytest.raises(SystemExit) as exit_info:
        handoff_main(["--runs", str(run_dir), "--metric-stdout"])
    assert exit_info.value.code == 3


def test_the_reporter_selects_a_named_run_or_the_newest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--run` names one archive and a missing one is a startup error; without it the newest wins,
    so a stale archive never reports as this run."""
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    archive(run_dir, row(20, 900, 0.0), name="older")
    archive(run_dir, row(9_000, 900, 0.0), name="newer")
    (run_dir / "newer.json").touch()
    handoff_main(["--runs", str(run_dir)])
    assert "verbose" in capsys.readouterr().out, "the newest archive is the one reported"

    handoff_main(["--runs", str(run_dir), "--run", "older"])
    assert "compliant" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        handoff_main(["--runs", str(run_dir), "--run", "absent"])
