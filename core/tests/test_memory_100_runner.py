from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from ufo_ext_memory.events import MEMORY_RECALL_EVENT

from evals.harness.capability import CapabilityCase, CapabilityOutput, TurnLog
from evals.harness.harness import EvalCaseResult, EvalReport
from evals.harness.judge import MAX_CRITERIA, MAX_CRITERION_CHARS
from evals.harness.target import TargetResult
from evals.harness.viewer import EvalRun, render_viewer
from evals.memory_100.models import SnapshotCase, SnapshotMemory
from evals.memory_100.runner import (
    ExpectedEvidence,
    Memory100Grader,
    MemoryRecallEvent,
    _answer_rubric,
    _with_memory_recall_aggregates,
    load_memory_100,
)
from evals.memory_100.snapshot import content_digest, write_snapshot
from evals.memory_100.state import AudienceBinding, CorpusReadiness, EvidenceOwner

DIGEST = "sha256:" + "0" * 64


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
    ),
)
def test_memory_recall_event_rejects_invalid_attributes(attributes: object) -> None:
    with pytest.raises(ValidationError):
        MemoryRecallEvent.model_validate(attributes)


def _cases() -> tuple[SnapshotCase, ...]:
    counts = (("enterprise", 60), ("longmem", 30), ("ufo", 10))
    return tuple(
        SnapshotCase(
            id=f"{corpus}/{index}",
            corpus=corpus,
            category="recall",
            audience="shared" if corpus == "enterprise" else "owner",
            question=f"question {corpus} {index}",
            expected_answer="The answer is forty two.",
            evidence_refs=("memory/answer",),
        )
        for corpus, count in counts
        for index in range(count)
    )


async def test_memory_100_grader_reports_observed_evidence_without_gating_the_answer() -> None:
    expected_id = uuid4()
    alternate_expected_id = uuid4()
    wrong_id = uuid4()
    turn_id = uuid4()
    grader = Memory100Grader(
        (ExpectedEvidence("memory/answer", frozenset({expected_id, alternate_expected_id})),)
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
    assert verdict.evidence["coverage"] == 1.0
    assert verdict.evidence["evidenceRanks"] == {"memory/answer": 2}
    assert verdict.evidence["recallError"] is None
    assert verdict.evidence["unmappedEvidence"] == []
    no_answer = await Memory100Grader(())(output)
    assert no_answer.passed
    assert no_answer.evidence["coverage"] is None
    unmapped = await Memory100Grader((ExpectedEvidence("page/handbook", frozenset()),))(output)
    assert unmapped.evidence["coverage"] is None
    assert unmapped.evidence["expectedCount"] == 0
    assert unmapped.evidence["evidenceRanks"] == {"page/handbook": None}
    assert unmapped.evidence["unmappedEvidence"] == ["page/handbook"]
    assert not (await grader(CapabilityOutput("", (), log=output.log))).passed


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
    report = _with_memory_recall_aggregates(
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


async def test_memory_100_task_pins_all_memory_owners_and_binds_member(
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
    report = await run.task.run(RecallTarget(first_memory_id))

    assert run.task.name == "memory_100"
    assert len(run.task.cases) == 100
    assert run.readiness.workspace_id == workspace_id
    assert reordered.task.digest == run.task.digest
    assert changed.task.digest != run.task.digest
    assert report.mean_mapped_evidence_coverage == 1.0
    assert report.min_mapped_evidence_coverage == 1.0
    assert report.degraded_recall_count == 0
    assert report.unmapped_evidence_count == 0


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
        audiences=(),
        evidence=(),
    )
    path = tmp_path / "readiness.json"
    path.write_text(readiness.model_dump_json())

    with pytest.raises(ValueError, match="different snapshot"):
        load_memory_100(snapshot_root, path)
