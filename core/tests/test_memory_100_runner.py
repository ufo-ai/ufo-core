from pathlib import Path
from uuid import uuid4

import pytest

from evals.harness.capability import CapabilityOutput
from evals.harness.judge import MAX_CRITERIA, MAX_CRITERION_CHARS
from evals.memory_100.models import SnapshotCase, SnapshotMemory
from evals.memory_100.runner import _answer_grader, _answer_rubric, load_memory_100
from evals.memory_100.snapshot import content_digest, write_snapshot
from evals.memory_100.state import AudienceBinding, CorpusReadiness, EvidenceOwner

DIGEST = "sha256:" + "0" * 64


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


async def test_answer_grader_requires_a_response_before_semantic_judging() -> None:
    assert not (await _answer_grader(CapabilityOutput("", ()))).passed
    assert (await _answer_grader(CapabilityOutput("answer", ()))).passed


def test_memory_100_task_binds_private_cases_to_materialized_member(
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
                owner_id="1",
                subject="shared",
            ),
        ),
    )
    readiness_path = tmp_path / "readiness.json"
    readiness_path.write_text(readiness.model_dump_json())

    run = load_memory_100(snapshot_root, readiness_path)

    assert run.task.name == "memory_100"
    assert len(run.task.cases) == 100
    assert run.readiness.workspace_id == workspace_id


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
                owner_id="1",
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
