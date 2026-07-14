from dataclasses import dataclass
from pathlib import Path

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
)
from evals.harness.judge import MAX_CRITERIA, MAX_CRITERION_CHARS
from evals.harness.registry import EvalTask, capability_task
from evals.memory_100.models import SnapshotCase
from evals.memory_100.snapshot import load_snapshot
from evals.memory_100.state import CorpusReadiness


@dataclass(frozen=True)
class Memory100Run:
    task: EvalTask
    readiness: CorpusReadiness


def load_memory_100(snapshot_root: Path, readiness_path: Path) -> Memory100Run:
    snapshot = load_snapshot(snapshot_root)
    readiness = CorpusReadiness.model_validate_json(readiness_path.read_bytes())
    if readiness.snapshot_digest != snapshot.manifest.digest:
        raise ValueError("memory_100 readiness belongs to a different snapshot")
    evidence = {owner.source_ref for owner in readiness.evidence}
    missing = sorted({ref for case in snapshot.cases for ref in case.evidence_refs} - evidence)
    if missing:
        raise ValueError(f"memory_100 readiness is missing evidence: {', '.join(missing)}")
    audiences = {binding.alias: binding.email for binding in readiness.audiences}
    unknown = sorted({case.audience for case in snapshot.cases} - audiences.keys())
    if unknown:
        raise ValueError(f"memory_100 readiness is missing audiences: {', '.join(unknown)}")
    cases = tuple(
        CapabilityCase(
            name=case.id,
            message=case.question,
            grader=_answer_grader,
            digest_tag=(f"{snapshot.manifest.digest}:{readiness.corpus_digest}:{case.id}"),
            rubric=_answer_rubric(case),
            member_key=audiences[case.audience],
        )
        for case in snapshot.cases
    )
    return Memory100Run(capability_task("memory_100", cases), readiness)


def _answer_rubric(case: SnapshotCase) -> tuple[str, ...]:
    reference_prefix = "Match this reference answer paragraph in substance: "
    rubric = tuple(
        reference_prefix + paragraph
        for paragraph in case.expected_answer.split("\n\n")
        if paragraph.strip()
    )
    if any(len(criterion) > MAX_CRITERION_CHARS for criterion in rubric):
        raise ValueError(f"memory_100 case {case.id!r} has an oversized reference paragraph")

    fact_prefix = "Cover every answer fact in this group: "
    separator = " | "
    fact_rubric: list[str] = []
    criterion = fact_prefix
    for fact in case.answer_facts:
        if len(fact_prefix + fact) > MAX_CRITERION_CHARS:
            raise ValueError(f"memory_100 case {case.id!r} has an oversized answer fact")
        candidate = criterion + (separator if criterion != fact_prefix else "") + fact
        if len(candidate) > MAX_CRITERION_CHARS:
            fact_rubric.append(criterion)
            criterion = fact_prefix + fact
        else:
            criterion = candidate
    if case.answer_facts:
        fact_rubric.append(criterion)
    rubric += tuple(fact_rubric)
    if len(rubric) > MAX_CRITERIA:
        raise ValueError(f"memory_100 case {case.id!r} has too many answer criteria")
    return rubric


async def _answer_grader(output: CapabilityOutput) -> CapabilityVerdict:
    if not output.response.strip():
        return CapabilityVerdict(False, "answer is empty")
    return CapabilityVerdict(True, "answer is present")
