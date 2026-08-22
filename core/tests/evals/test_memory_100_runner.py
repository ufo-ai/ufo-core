import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from ufo_ext_memory.events import MEMORY_RECALL_EVENT

import evals.memory_100.runner as memory_100_runner
from evals.__main__ import _tasks as selected_eval_tasks
from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    ToolInvocation,
    TurnLog,
)
from evals.harness.harness import EvalCaseResult, EvalReport
from evals.harness.judge import MAX_CRITERIA, MAX_CRITERION_CHARS
from evals.harness.recall import MemoryRecallEvent, with_recall_aggregates
from evals.harness.target import TargetResult
from evals.harness.viewer import EvalRun, render_viewer
from evals.memory_100.materialize import ASKER_EMAIL
from evals.memory_100.models import SnapshotCase, SnapshotMemory
from evals.memory_100.runner import (
    ALIAS_MIN_MAPPED_EVIDENCE_COVERAGE,
    MEMORY_100_LEAVES,
    MEMORY_JUDGE_MODEL,
    ExpectedEvidence,
    Memory100Grader,
    _answer_rubric,
    load_memory_100,
)
from evals.memory_100.snapshot import content_digest, write_snapshot
from evals.memory_100.state import AudienceBinding, CorpusReadiness, EvidenceOwner

DIGEST = "sha256:" + "0" * 64
MEMORY_100_CASE_GROUPS = (
    ("enterprise", "basic", 4),
    ("enterprise", "semantic", 8),
    ("enterprise", "intra_document_reasoning", 6),
    ("enterprise", "project_related", 8),
    ("enterprise", "constrained", 7),
    ("enterprise", "conflicting_info", 7),
    ("enterprise", "completeness", 7),
    ("enterprise", "miscellaneous", 4),
    ("enterprise", "high_level", 4),
    ("enterprise", "info_not_found", 5),
    ("longmem", "information_extraction", 6),
    ("longmem", "multi_session", 6),
    ("longmem", "knowledge_update", 6),
    ("longmem", "temporal_reasoning", 6),
    ("longmem", "abstention", 6),
    ("ufo", "shared-page", 1),
    ("ufo", "multi-page", 1),
    ("ufo", "conflicting-evidence", 1),
    ("ufo", "private-memory", 1),
    ("ufo", "decision-memory", 1),
    ("ufo", "preference-memory", 1),
    ("ufo", "event-memory", 1),
    ("ufo", "member-isolation", 1),
    ("ufo", "information-not-found", 1),
    ("ufo", "mixed-scope", 1),
    ("ufo", "alias-initialism", 1),
    ("ufo", "alias-handle", 1),
)
MEMORY_100_LEAF_COUNTS = (
    ("memory_100.enterprise.basic", 4),
    ("memory_100.enterprise.semantic", 8),
    ("memory_100.enterprise.intra_document_reasoning", 6),
    ("memory_100.enterprise.project_related", 8),
    ("memory_100.enterprise.constrained", 7),
    ("memory_100.enterprise.conflicting_info", 7),
    ("memory_100.enterprise.completeness", 7),
    ("memory_100.enterprise.miscellaneous", 4),
    ("memory_100.enterprise.high_level", 4),
    ("memory_100.enterprise.info_not_found", 5),
    ("memory_100.longmem.information_extraction", 6),
    ("memory_100.longmem.multi_session", 6),
    ("memory_100.longmem.knowledge_update", 6),
    ("memory_100.longmem.temporal_reasoning", 6),
    ("memory_100.longmem.abstention", 6),
    ("memory_100.ufo.pages", 3),
    ("memory_100.ufo.memories", 4),
    ("memory_100.ufo.boundaries", 3),
    ("memory_100.ufo.alias_identity", 2),
)
ALIAS_LEAF = "memory_100.ufo.alias_identity"


@dataclass(frozen=True)
class PassingJudge:
    async def complete(self, system: str, messages: object) -> str:
        return '{"items":[{"passed":true,"reason":"supported by the answer"}]}'


@dataclass(frozen=True)
class RecallTarget:
    memory_id: UUID
    judge: PassingJudge = PassingJudge()

    async def run(self, case: CapabilityCase) -> TargetResult:
        return TargetResult(
            CapabilityOutput(
                "The answer is forty two.",
                (),
                log=TurnLog(
                    event=MEMORY_RECALL_EVENT,
                    turn_id=uuid4(),
                    attributes={"memory_ids": [str(self.memory_id)]},
                ),
            ),
            clean=True,
        )


@pytest.mark.parametrize(
    "attributes",
    (
        {"memory_ids": [], "path": "hook"},
        {"memory_ids": [str(uuid4())] * 2},
        {"memory_ids": [str(uuid4()) for _ in range(9)]},
        {"memory_ids": [], "error_class": ""},
        {"memory_ids": [], "error_class": "x" * 129},
        {"memory_ids": [str(uuid4())], "error_class": "TimeoutError"},
        {"memory_ids": [], "skipped": ""},
        {"memory_ids": [], "skipped": "x" * 65},
    ),
)
def test_memory_recall_event_rejects_invalid_attributes(attributes: object) -> None:
    with pytest.raises(ValidationError):
        MemoryRecallEvent.model_validate(attributes)


def test_memory_recall_event_accepts_a_skipped_internal_admission() -> None:
    event = MemoryRecallEvent.model_validate({"memory_ids": [], "skipped": "internal_admission"})
    assert event.memory_ids == ()
    assert event.skipped == "internal_admission"


def _cases() -> tuple[SnapshotCase, ...]:
    return tuple(
        SnapshotCase(
            id=f"{corpus}/{category}/{index}",
            corpus=corpus,
            category=category,
            audience="shared" if corpus == "enterprise" else "owner",
            question=f"question {corpus} {category} {index}",
            expected_answer="The answer is forty two.",
            evidence_refs=("memory/answer",),
        )
        for corpus, category, count in MEMORY_100_CASE_GROUPS
        for index in range(count)
    )


def _memory_100_paths(tmp_path: Path) -> tuple[Path, Path]:
    snapshot_root = tmp_path / "snapshot"
    manifest = write_snapshot(
        snapshot_root,
        upstreams=(),
        builder_digest=DIGEST,
        cases=tuple(case.model_copy(update={"evidence_refs": ()}) for case in _cases()),
        pages=(),
        memories=(),
    )
    readiness = CorpusReadiness(
        snapshot_digest=manifest.digest,
        corpus_digest=DIGEST,
        workspace_id=uuid4(),
        source_id=uuid4(),
        pages_root=tmp_path / "pages",
        page_count=0,
        memory_count=0,
        chunk_count=0,
        asker_email=ASKER_EMAIL,
        audiences=(
            AudienceBinding(alias="shared", email=None, member_id=None),
            AudienceBinding(alias="owner", email="owner@eval.invalid", member_id=uuid4()),
        ),
        evidence=(),
    )
    readiness_path = tmp_path / "readiness.json"
    readiness_path.write_text(readiness.model_dump_json())
    return snapshot_root, readiness_path


async def test_memory_100_grader_reports_observed_evidence_without_gating_the_answer() -> None:
    expected_id = uuid4()
    alternate_expected_id = uuid4()
    wrong_id = uuid4()
    turn_id = uuid4()
    grader = Memory100Grader(
        (
            ExpectedEvidence(
                "memory/answer", frozenset({expected_id, alternate_expected_id}), frozenset()
            ),
        )
    )
    output = CapabilityOutput(
        "answer",
        (),
        log=TurnLog(
            event=MEMORY_RECALL_EVENT,
            turn_id=turn_id,
            attributes={"memory_ids": [str(wrong_id), str(expected_id)]},
        ),
    )

    verdict = await grader(output)

    assert verdict.passed
    assert "memory recall log" in grader.grading
    assert "semantic rubric" in grader.grading
    assert verdict.evidence["coverage"] == 1.0
    assert verdict.evidence["evidenceRanks"] == {"memory/answer": 2}
    assert verdict.evidence["recallError"] is None
    assert verdict.evidence["unmappedEvidence"] == []
    no_answer = await Memory100Grader(())(output)
    assert no_answer.passed
    assert no_answer.evidence["coverage"] is None
    unmapped = await Memory100Grader(
        (ExpectedEvidence("page/handbook", frozenset(), frozenset()),)
    )(output)
    assert unmapped.evidence["coverage"] is None
    assert unmapped.evidence["expectedCount"] == 0
    assert unmapped.evidence["evidenceRanks"] == {"page/handbook": None}
    assert unmapped.evidence["unmappedEvidence"] == ["page/handbook"]
    assert not (await grader(CapabilityOutput("", (), log=output.log))).passed


async def test_memory_100_grader_scores_page_evidence_the_turn_retrieved_itself() -> None:
    retrieved = uuid4()
    missed = uuid4()
    grader = Memory100Grader(
        (
            ExpectedEvidence("enterprise/found", frozenset(), frozenset({retrieved})),
            ExpectedEvidence("enterprise/missed", frozenset(), frozenset({missed})),
        )
    )
    log = TurnLog(event=MEMORY_RECALL_EVENT, turn_id=uuid4(), attributes={"memory_ids": []})
    searched = CapabilityOutput(
        "answer",
        (
            ToolInvocation(
                name="memory_search",
                input={},
                result=f"- [doc] the clause (page/{retrieved}, 2026-08-10)",
                has_result=True,
            ),
        ),
        log=log,
    )

    verdict = await grader(searched)

    assert verdict.evidence["pageEvidenceExpected"] == 2
    assert verdict.evidence["pageEvidenceFound"] == 1
    assert verdict.evidence["pageCoverage"] == 0.5
    assert verdict.evidence["missingPageEvidence"] == ["enterprise/missed"]
    assert verdict.evidence["unmappedEvidence"] == []
    assert verdict.evidence["coverage"] is None

    blind = await grader(CapabilityOutput("answer", (), log=log))
    assert blind.evidence["pageEvidenceFound"] == 0
    assert blind.evidence["pageCoverage"] == 0.0

    failed_call = replace(searched.calls[0], is_error=True)
    errored = await grader(CapabilityOutput("answer", (failed_call,), log=log))
    assert errored.evidence["pageEvidenceFound"] == 0


def _recalled(*memory_ids: UUID) -> CapabilityOutput:
    return CapabilityOutput(
        "answer",
        (),
        log=TurnLog(
            event=MEMORY_RECALL_EVENT,
            turn_id=uuid4(),
            attributes={"memory_ids": [str(memory_id) for memory_id in memory_ids]},
        ),
    )


async def test_memory_100_alias_leaf_gates_the_verdict_on_mapped_evidence_coverage() -> None:
    full_name = uuid4()
    handle = uuid4()
    unrelated = uuid4()
    expected = (
        ExpectedEvidence("ufo/page/halyard-charter", frozenset(), frozenset()),
        ExpectedEvidence("ufo/memory/halyard-cutover-window", frozenset({full_name}), frozenset()),
        ExpectedEvidence("ufo/memory/halyard-deputy", frozenset({handle}), frozenset()),
    )
    grader = Memory100Grader(expected, ALIAS_MIN_MAPPED_EVIDENCE_COVERAGE)

    partial = await grader(_recalled(unrelated, full_name))
    covered = await grader(_recalled(handle, unrelated, full_name))
    degraded = await grader(
        CapabilityOutput(
            "answer",
            (),
            log=TurnLog(
                event=MEMORY_RECALL_EVENT,
                turn_id=uuid4(),
                attributes={"memory_ids": [], "error_class": "TimeoutError"},
            ),
        )
    )
    unmapped_only = await Memory100Grader((expected[0],), ALIAS_MIN_MAPPED_EVIDENCE_COVERAGE)(
        _recalled(full_name)
    )

    assert not partial.passed
    assert partial.reason == "injected recall covered 1/2 mapped evidence rows, below 100%"
    assert partial.evidence["coverage"] == 0.5
    assert partial.evidence["unmappedEvidence"] == ["ufo/page/halyard-charter"]
    assert (await Memory100Grader(expected)(_recalled(unrelated, full_name))).passed
    assert covered.passed
    assert covered.evidence["coverage"] == 1.0
    assert not degraded.passed
    assert degraded.reason == (
        "injected recall covered 0/2 mapped evidence rows, below 100% "
        "(recall degraded: TimeoutError)"
    )
    assert not unmapped_only.passed
    assert unmapped_only.reason == "no expected evidence row owns a memory item to recall"
    assert "100% of the evidence rows that own a memory item" in grader.grading
    assert MEMORY_100_LEAVES[-1].name == ALIAS_LEAF
    assert MEMORY_100_LEAVES[-1].min_mapped_evidence_coverage == 1.0
    assert all(leaf.min_mapped_evidence_coverage is None for leaf in MEMORY_100_LEAVES[:-1])


async def test_memory_100_grader_case_fails_invalid_or_missing_log() -> None:
    grader = Memory100Grader(())

    missing = await grader(CapabilityOutput("answer", ()))
    wrong = await grader(
        CapabilityOutput(
            "answer",
            (),
            log=TurnLog(event="other.event", turn_id=uuid4(), attributes={}),
        )
    )
    malformed = await grader(
        CapabilityOutput(
            "answer",
            (),
            log=TurnLog(
                event=MEMORY_RECALL_EVENT,
                turn_id=uuid4(),
                attributes={"memory_ids": [], "unexpected": True},
            ),
        )
    )

    assert not missing.passed
    assert missing.reason == "memory recall log is missing"
    assert not wrong.passed
    assert wrong.reason == "memory recall log has the wrong event"
    assert not malformed.passed
    assert malformed.reason == "memory recall log is invalid"


def test_memory_100_report_surfaces_recall_observations_without_gating_pass() -> None:
    cases = (
        EvalCaseResult(
            name="recalled",
            passed=True,
            reason="answer is present",
            evidence={
                "selectedAttempt": 0,
                "attempts": [
                    {
                        "grader": {
                            "coverage": 1.0,
                            "recallError": None,
                            "unmappedEvidence": [],
                        }
                    }
                ],
            },
        ),
        EvalCaseResult(
            name="degraded",
            passed=True,
            reason="answer is present",
            evidence={
                "selectedAttempt": 1,
                "attempts": [
                    {"grader": None},
                    {
                        "grader": {
                            "coverage": 0.0,
                            "recallError": "TimeoutError",
                            "unmappedEvidence": ["page/handbook", "page/runbook"],
                        }
                    },
                ],
            },
        ),
        EvalCaseResult(
            name="unmapped",
            passed=True,
            reason="answer is present",
            evidence={
                "selectedAttempt": 0,
                "attempts": [
                    {
                        "grader": {
                            "coverage": None,
                            "recallError": None,
                            "unmappedEvidence": ["page/policy"],
                        }
                    }
                ],
            },
        ),
    )
    report = with_recall_aggregates(
        EvalReport(name="memory_100", suite="capability", digest=DIGEST, cases=cases)
    )

    assert report.passed
    assert report.mean_mapped_evidence_coverage == 0.5
    assert report.min_mapped_evidence_coverage == 0.0
    assert report.degraded_recall_count == 1
    assert report.unmapped_evidence_count == 3
    assert report.to_json()["meanMappedEvidenceCoverage"] == 0.5
    assert report.to_json()["minMappedEvidenceCoverage"] == 0.0
    assert report.to_json()["degradedRecallCount"] == 1
    assert report.to_json()["unmappedEvidenceCount"] == 3
    assert "mapped evidence coverage mean 50%, min 0%" in report.console_summary
    viewer = render_viewer(
        (
            EvalRun(
                id=uuid4(),
                created_at=datetime(2026, 7, 14, tzinfo=UTC),
                label="memory recall",
                agent="assistant",
                ufo_version="0.1.0",
                revision="abc123",
                reports=(report,),
            ),
        )
    ).decode()
    assert '"meanMappedEvidenceCoverage":0.5' in viewer
    assert '"minMappedEvidenceCoverage":0.0' in viewer
    assert '"degradedRecallCount":1' in viewer
    assert '"unmappedEvidenceCount":3' in viewer
    assert "mapped evidence coverage" in viewer


def test_non_memory_report_omits_recall_observations() -> None:
    report = EvalReport(name="tool_calling", suite="capability", digest=DIGEST, cases=())
    run = EvalRun(
        id=uuid4(),
        created_at=datetime(2026, 7, 14, tzinfo=UTC),
        label="tools",
        agent="assistant",
        ufo_version="0.1.0",
        revision="abc123",
        reports=(report,),
    )

    assert "meanMappedEvidenceCoverage" not in report.to_json()
    assert report.console_summary == (f"tool_calling 0/0 passed, 0 excluded (rate 0%) {DIGEST}")
    recorded_report = run.model_dump(mode="json", by_alias=True, exclude_none=True)["reports"][0]
    assert isinstance(recorded_report, dict)
    assert "meanMappedEvidenceCoverage" not in recorded_report


async def test_memory_100_leaves_pin_all_memory_owners_and_bind_member(
    tmp_path: Path,
) -> None:
    snapshot_root = tmp_path / "snapshot"
    body = "The answer is forty two."
    manifest = write_snapshot(
        snapshot_root,
        upstreams=(),
        builder_digest=DIGEST,
        cases=_cases(),
        pages=(),
        memories=(
            SnapshotMemory(
                source_ref="memory/answer",
                audience="owner",
                body=body,
                digest=content_digest(body),
            ),
        ),
    )
    workspace_id = uuid4()
    first_memory_id = uuid4()
    second_memory_id = uuid4()
    readiness = CorpusReadiness(
        snapshot_digest=manifest.digest,
        corpus_digest=DIGEST,
        workspace_id=workspace_id,
        source_id=uuid4(),
        pages_root=tmp_path / "pages",
        page_count=0,
        memory_count=1,
        chunk_count=1,
        asker_email=ASKER_EMAIL,
        audiences=(
            AudienceBinding(alias="shared", email=None, member_id=None),
            AudienceBinding(alias="owner", email="owner@eval.invalid", member_id=uuid4()),
        ),
        evidence=(
            EvidenceOwner(
                source_ref="memory/answer",
                owner_kind="memory_item",
                owner_id=first_memory_id,
                subject="shared",
            ),
            EvidenceOwner(
                source_ref="memory/answer",
                owner_kind="memory_item",
                owner_id=second_memory_id,
                subject="shared",
            ),
            EvidenceOwner(
                source_ref="memory/answer",
                owner_kind="page",
                owner_id=uuid4(),
                subject="shared",
            ),
        ),
    )
    readiness_path = tmp_path / "readiness.json"
    readiness_path.write_text(readiness.model_dump_json())

    run = load_memory_100(snapshot_root, readiness_path)
    readiness_path.write_text(
        readiness.model_copy(
            update={"evidence": tuple(reversed(readiness.evidence))}
        ).model_dump_json()
    )
    reordered = load_memory_100(snapshot_root, readiness_path)
    changed_evidence = list(readiness.evidence)
    changed_evidence[1] = changed_evidence[1].model_copy(update={"owner_id": uuid4()})
    readiness_path.write_text(
        readiness.model_copy(update={"evidence": tuple(changed_evidence)}).model_dump_json()
    )
    changed = load_memory_100(snapshot_root, readiness_path)
    reports = tuple(
        [await task.run(RecallTarget(first_memory_id), asyncio.Semaphore(1)) for task in run.tasks]
    )

    assert tuple((task.name, len(task.cases)) for task in run.tasks) == MEMORY_100_LEAF_COUNTS
    assert {task.judge_model for task in run.tasks} == {MEMORY_JUDGE_MODEL}
    leaf_cases = tuple(case for task in run.tasks for case in task.cases)
    assert len(leaf_cases) == len(set(leaf_cases)) == 102
    assert set(leaf_cases) == {case.id for case in _cases()}
    assert run.readiness.workspace_id == workspace_id
    assert tuple(task.digest for task in reordered.tasks) == tuple(
        task.digest for task in run.tasks
    )
    assert all(
        changed_task.digest != task.digest
        for changed_task, task in zip(changed.tasks, run.tasks, strict=True)
    )
    assert selected_eval_tasks((), run) == run.tasks
    assert tuple(task.name for task in selected_eval_tasks((run.tasks[0].name,), run)) == (
        run.tasks[0].name,
    )
    assert all(report.mean_mapped_evidence_coverage == 1.0 for report in reports)
    assert all(report.min_mapped_evidence_coverage == 1.0 for report in reports)
    assert all(report.degraded_recall_count == 0 for report in reports)
    assert all(report.unmapped_evidence_count == 0 for report in reports)
    assert all(report.passed for report in reports)


def test_memory_100_alias_leaf_pins_its_coverage_bar_into_the_suite_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_root, readiness_path = _memory_100_paths(tmp_path)
    run = load_memory_100(snapshot_root, readiness_path)
    leaves = memory_100_runner.MEMORY_100_LEAVES
    monkeypatch.setattr(
        memory_100_runner,
        "MEMORY_100_LEAVES",
        (*leaves[:-1], replace(leaves[-1], min_mapped_evidence_coverage=0.5)),
    )

    relaxed = load_memory_100(snapshot_root, readiness_path)

    by_name = {task.name: task.digest for task in run.tasks}
    relaxed_by_name = {task.name: task.digest for task in relaxed.tasks}
    assert by_name.keys() == relaxed_by_name.keys()
    assert by_name[ALIAS_LEAF] != relaxed_by_name[ALIAS_LEAF]
    assert all(by_name[name] == relaxed_by_name[name] for name in by_name if name != ALIAS_LEAF)


def test_memory_100_rejects_unlabeled_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot_root, readiness_path = _memory_100_paths(tmp_path)
    leaves = memory_100_runner.MEMORY_100_LEAVES
    monkeypatch.setattr(
        memory_100_runner,
        "MEMORY_100_LEAVES",
        (replace(leaves[0], categories=("unknown",)), *leaves[1:]),
    )

    with pytest.raises(ValueError, match="cases have no leaf: enterprise/basic/0"):
        load_memory_100(snapshot_root, readiness_path)


def test_memory_100_rejects_overlapping_leaves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_root, readiness_path = _memory_100_paths(tmp_path)
    leaves = memory_100_runner.MEMORY_100_LEAVES
    monkeypatch.setattr(
        memory_100_runner,
        "MEMORY_100_LEAVES",
        (
            leaves[0],
            replace(leaves[1], categories=(*leaves[1].categories, "basic")),
            *leaves[2:],
        ),
    )

    with pytest.raises(ValueError, match="cases belong to multiple leaves: enterprise/basic/0"):
        load_memory_100(snapshot_root, readiness_path)


def test_memory_100_rejects_incorrect_leaf_case_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_root, readiness_path = _memory_100_paths(tmp_path)
    leaves = memory_100_runner.MEMORY_100_LEAVES
    monkeypatch.setattr(
        memory_100_runner,
        "MEMORY_100_LEAVES",
        (replace(leaves[0], expected_cases=5), *leaves[1:]),
    )

    with pytest.raises(
        ValueError,
        match=r"leaf 'memory_100\.enterprise\.basic' requires 5 cases, found 4",
    ):
        load_memory_100(snapshot_root, readiness_path)


def test_memory_100_task_groups_long_enterprise_answer_rubric(tmp_path: Path) -> None:
    snapshot_root = tmp_path / "snapshot"
    body = "The answer is forty two."
    reference_paragraphs = (
        "Preparation and rollback requirements. " * 40,
        "Approval and technical readiness requirements. " * 35,
        "Customer and internal communication requirements. " * 30,
    )
    facts = tuple(
        f"Required validation {index}: " + "substantive evidence " * 7 for index in range(45)
    )
    cases = list(_cases())
    cases[0] = cases[0].model_copy(
        update={
            "expected_answer": "\n\n".join(reference_paragraphs),
            "answer_facts": facts,
        }
    )
    manifest = write_snapshot(
        snapshot_root,
        upstreams=(),
        builder_digest=DIGEST,
        cases=tuple(cases),
        pages=(),
        memories=(
            SnapshotMemory(
                source_ref="memory/answer",
                audience="owner",
                body=body,
                digest=content_digest(body),
            ),
        ),
    )
    readiness = CorpusReadiness(
        snapshot_digest=manifest.digest,
        corpus_digest=DIGEST,
        workspace_id=uuid4(),
        source_id=uuid4(),
        pages_root=tmp_path / "pages",
        page_count=0,
        memory_count=1,
        chunk_count=1,
        asker_email=ASKER_EMAIL,
        audiences=(
            AudienceBinding(alias="shared", email=None, member_id=None),
            AudienceBinding(alias="owner", email="owner@eval.invalid", member_id=uuid4()),
        ),
        evidence=(
            EvidenceOwner(
                source_ref="memory/answer",
                owner_kind="memory_item",
                owner_id=uuid4(),
                subject="shared",
            ),
        ),
    )
    readiness_path = tmp_path / "readiness.json"
    readiness_path.write_text(readiness.model_dump_json())

    load_memory_100(snapshot_root, readiness_path)

    rubric = _answer_rubric(cases[0])
    assert 1 < len(rubric) <= MAX_CRITERIA
    assert all(len(criterion) <= MAX_CRITERION_CHARS for criterion in rubric)
    assert all(paragraph in "\n".join(rubric) for paragraph in reference_paragraphs)
    assert all(fact in "\n".join(rubric) for fact in facts)


def test_memory_100_task_rejects_a_different_snapshot(tmp_path: Path) -> None:
    snapshot_root = tmp_path / "snapshot"
    body = "The answer is forty two."
    write_snapshot(
        snapshot_root,
        upstreams=(),
        builder_digest=DIGEST,
        cases=_cases(),
        pages=(),
        memories=(
            SnapshotMemory(
                source_ref="memory/answer",
                audience="owner",
                body=body,
                digest=content_digest(body),
            ),
        ),
    )
    readiness = CorpusReadiness(
        snapshot_digest="sha256:" + "1" * 64,
        corpus_digest=DIGEST,
        workspace_id=uuid4(),
        source_id=uuid4(),
        pages_root=tmp_path / "pages",
        page_count=0,
        memory_count=0,
        chunk_count=0,
        asker_email=ASKER_EMAIL,
        audiences=(),
        evidence=(),
    )
    path = tmp_path / "readiness.json"
    path.write_text(readiness.model_dump_json())

    with pytest.raises(ValueError, match="different snapshot"):
        load_memory_100(snapshot_root, path)
