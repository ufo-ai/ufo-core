import asyncio
import json
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from ufo_ext_memory.events import (
    MAX_RECALL_ERROR_CLASS_CHARS,
    MAX_RECALLED_MEMORY_IDS,
    MEMORY_RECALL_EVENT,
)

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
)
from evals.harness.harness import EvalReport, Json, JsonObject
from evals.harness.judge import MAX_CRITERIA, MAX_CRITERION_CHARS
from evals.harness.registry import EvalTask, capability_task
from evals.harness.target import CapabilityTarget
from evals.memory_100.models import Corpus, SnapshotCase
from evals.memory_100.snapshot import load_snapshot
from evals.memory_100.state import CorpusReadiness

MEMORY_100_GRADER_REVISION = "memory-item-ids-1"
MEMORY_JUDGE_MODEL = "gpt-5.4"


@dataclass(frozen=True)
class Memory100Leaf:
    name: str
    corpus: Corpus
    categories: tuple[str, ...]
    expected_cases: int


MEMORY_100_LEAVES = (
    Memory100Leaf("memory_100.enterprise.basic", "enterprise", ("basic",), 4),
    Memory100Leaf("memory_100.enterprise.semantic", "enterprise", ("semantic",), 8),
    Memory100Leaf(
        "memory_100.enterprise.intra_document_reasoning",
        "enterprise",
        ("intra_document_reasoning",),
        6,
    ),
    Memory100Leaf("memory_100.enterprise.project_related", "enterprise", ("project_related",), 8),
    Memory100Leaf("memory_100.enterprise.constrained", "enterprise", ("constrained",), 7),
    Memory100Leaf("memory_100.enterprise.conflicting_info", "enterprise", ("conflicting_info",), 7),
    Memory100Leaf("memory_100.enterprise.completeness", "enterprise", ("completeness",), 7),
    Memory100Leaf("memory_100.enterprise.miscellaneous", "enterprise", ("miscellaneous",), 4),
    Memory100Leaf("memory_100.enterprise.high_level", "enterprise", ("high_level",), 4),
    Memory100Leaf("memory_100.enterprise.info_not_found", "enterprise", ("info_not_found",), 5),
    Memory100Leaf(
        "memory_100.longmem.information_extraction",
        "longmem",
        ("information_extraction",),
        6,
    ),
    Memory100Leaf("memory_100.longmem.multi_session", "longmem", ("multi_session",), 6),
    Memory100Leaf("memory_100.longmem.knowledge_update", "longmem", ("knowledge_update",), 6),
    Memory100Leaf("memory_100.longmem.temporal_reasoning", "longmem", ("temporal_reasoning",), 6),
    Memory100Leaf("memory_100.longmem.abstention", "longmem", ("abstention",), 6),
    Memory100Leaf(
        "memory_100.ufo.pages",
        "ufo",
        ("shared-page", "multi-page", "conflicting-evidence"),
        3,
    ),
    Memory100Leaf(
        "memory_100.ufo.memories",
        "ufo",
        ("private-memory", "decision-memory", "preference-memory", "event-memory"),
        4,
    ),
    Memory100Leaf(
        "memory_100.ufo.boundaries",
        "ufo",
        ("member-isolation", "information-not-found", "mixed-scope"),
        3,
    ),
)


class MemoryRecallEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_ids: tuple[UUID, ...] = Field(max_length=MAX_RECALLED_MEMORY_IDS)
    error_class: str | None = Field(
        default=None, min_length=1, max_length=MAX_RECALL_ERROR_CLASS_CHARS
    )

    @model_validator(mode="after")
    def _valid_outcome(self) -> "MemoryRecallEvent":
        if len(set(self.memory_ids)) != len(self.memory_ids):
            raise ValueError("memory recall IDs must be unique")
        if self.error_class is not None and self.memory_ids:
            raise ValueError("failed memory recall cannot contain IDs")
        return self


@dataclass(frozen=True)
class ExpectedEvidence:
    source_ref: str
    memory_ids: frozenset[UUID]


@dataclass(frozen=True)
class Memory100Run:
    tasks: tuple[EvalTask, ...]
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
    cases: dict[str, CapabilityCase] = {}
    for case in snapshot.cases:
        expected = tuple(
            ExpectedEvidence(
                source_ref,
                frozenset(
                    owner.owner_id
                    for owner in readiness.evidence
                    if owner.source_ref == source_ref and owner.owner_kind == "memory_item"
                ),
            )
            for source_ref in case.evidence_refs
        )
        evidence_identity = json.dumps(
            [
                [item.source_ref, sorted(str(memory_id) for memory_id in item.memory_ids)]
                for item in expected
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        cases[case.id] = CapabilityCase(
            name=case.id,
            message=case.question,
            grader=Memory100Grader(expected),
            digest_tag=(
                f"{snapshot.manifest.digest}:{readiness.corpus_digest}:"
                f"{MEMORY_100_GRADER_REVISION}:{case.id}:{evidence_identity}"
            ),
            rubric=_answer_rubric(case),
            member_key=audiences[case.audience],
        )
    memberships = {
        case.id: tuple(
            leaf
            for leaf in MEMORY_100_LEAVES
            if leaf.corpus == case.corpus and case.category in leaf.categories
        )
        for case in snapshot.cases
    }
    unlabeled = sorted(case_id for case_id, leaves in memberships.items() if not leaves)
    if unlabeled:
        raise ValueError(f"memory_100 cases have no leaf: {', '.join(unlabeled)}")
    repeated = sorted(case_id for case_id, leaves in memberships.items() if len(leaves) > 1)
    if repeated:
        raise ValueError(f"memory_100 cases belong to multiple leaves: {', '.join(repeated)}")
    tasks: list[EvalTask] = []
    for leaf in MEMORY_100_LEAVES:
        leaf_cases = tuple(
            cases[case.id] for case in snapshot.cases if memberships[case.id] == (leaf,)
        )
        if len(leaf_cases) != leaf.expected_cases:
            raise ValueError(
                f"memory_100 leaf {leaf.name!r} requires {leaf.expected_cases} cases, "
                f"found {len(leaf_cases)}"
            )
        tasks.append(_memory_task(leaf.name, leaf_cases))
    return Memory100Run(tuple(tasks), readiness)


def _memory_task(name: str, cases: tuple[CapabilityCase, ...]) -> EvalTask:
    task = capability_task(name, cases, judge_model=MEMORY_JUDGE_MODEL)

    async def run(target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        report = await task.run(target, slots)
        return _with_memory_recall_aggregates(report)

    return replace(task, run=run)


def _with_memory_recall_aggregates(report: EvalReport) -> EvalReport:
    coverages: list[float] = []
    degraded_recall_count = 0
    unmapped_evidence_count = 0
    for case in report.cases:
        selected_attempt = case.evidence.get("selectedAttempt")
        attempts = case.evidence.get("attempts")
        if (
            isinstance(selected_attempt, bool)
            or not isinstance(selected_attempt, int)
            or not isinstance(attempts, list)
            or not 0 <= selected_attempt < len(attempts)
        ):
            raise TypeError("memory_100 selected attempt evidence is invalid")
        attempt = attempts[selected_attempt]
        if not isinstance(attempt, dict):
            raise TypeError("memory_100 selected attempt must be an object")
        grader = attempt.get("grader")
        if grader is None:
            continue
        if not isinstance(grader, dict):
            raise TypeError("memory_100 grader evidence must be an object")
        coverage = grader.get("coverage")
        if coverage is not None:
            if isinstance(coverage, bool) or not isinstance(coverage, (int, float)):
                raise TypeError("memory_100 coverage must be a number or null")
            coverages.append(float(coverage))
        recall_error = grader.get("recallError")
        if recall_error is not None:
            if not isinstance(recall_error, str):
                raise TypeError("memory_100 recall error must be a string or null")
            degraded_recall_count += 1
        unmapped = grader.get("unmappedEvidence")
        if not isinstance(unmapped, list) or not all(isinstance(item, str) for item in unmapped):
            raise TypeError("memory_100 unmapped evidence must be a list of source refs")
        unmapped_evidence_count += len(unmapped)
    mean_coverage = sum(coverages) / len(coverages) if coverages else None
    return report.model_copy(
        update={
            "mean_mapped_evidence_coverage": mean_coverage,
            "min_mapped_evidence_coverage": min(coverages) if coverages else None,
            "degraded_recall_count": degraded_recall_count,
            "unmapped_evidence_count": unmapped_evidence_count,
        }
    )


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


@dataclass(frozen=True)
class Memory100Grader:
    expected: tuple[ExpectedEvidence, ...]

    grading = (
        "the turn exports a valid memory recall log whose selected memories are ranked "
        "against each expected evidence owner and the answer is non-empty; answer substance "
        "is judged against the semantic rubric"
    )

    async def __call__(self, output: CapabilityOutput) -> CapabilityVerdict:
        if output.log is None:
            return CapabilityVerdict(False, "memory recall log is missing")
        if output.log.event != MEMORY_RECALL_EVENT:
            return CapabilityVerdict(False, "memory recall log has the wrong event")
        try:
            recall = MemoryRecallEvent.model_validate(output.log.attributes)
        except ValidationError:
            return CapabilityVerdict(False, "memory recall log is invalid")
        selected = recall.memory_ids
        ranks: dict[str, Json] = {
            owner.source_ref: next(
                (
                    rank
                    for rank, memory_id in enumerate(selected, start=1)
                    if memory_id in owner.memory_ids
                ),
                None,
            )
            for owner in self.expected
        }
        found = sum(rank is not None for rank in ranks.values())
        expected = sum(bool(owner.memory_ids) for owner in self.expected)
        evidence: JsonObject = {
            "recallError": recall.error_class,
            "selectedCount": len(selected),
            "expectedCount": expected,
            "evidenceRanks": ranks,
            "unmappedEvidence": [
                owner.source_ref for owner in self.expected if not owner.memory_ids
            ],
            "coverage": found / expected if expected else None,
        }
        if not output.response.strip():
            return CapabilityVerdict(False, "answer is empty", evidence)
        return CapabilityVerdict(
            True,
            f"answer is present; recall found {found}/{expected} mapped evidence",
            evidence,
        )
