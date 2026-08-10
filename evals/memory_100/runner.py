import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from ufo_ext_memory.events import MEMORY_RECALL_EVENT

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
)
from evals.harness.harness import Json, JsonObject
from evals.harness.judge import MAX_CRITERIA, MAX_CRITERION_CHARS
from evals.harness.recall import MemoryRecallEvent, recall_graded
from evals.harness.registry import EvalTask, capability_task
from evals.memory_100.models import Corpus, SnapshotCase
from evals.memory_100.snapshot import load_snapshot
from evals.memory_100.state import CorpusReadiness
from ufo.subjects import SHARED_SUBJECT

MEMORY_100_GRADER_REVISION = "evidence-coverage-gate-1"
MEMORY_JUDGE_MODEL = "gpt-5.4"
ALIAS_MIN_MAPPED_EVIDENCE_COVERAGE = 1.0


@dataclass(frozen=True)
class Memory100Leaf:
    """One report leaf over one corpus. `min_mapped_evidence_coverage`, when set, makes the leaf's
    bar the recall coverage of its cases' mapped evidence instead of the answer rubric: a case whose
    injected recall misses one of those rows fails however well the judge reads its answer."""

    name: str
    corpus: Corpus
    categories: tuple[str, ...]
    expected_cases: int
    min_mapped_evidence_coverage: float | None = None


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
    Memory100Leaf(
        "memory_100.ufo.alias_identity",
        "ufo",
        ("alias-initialism", "alias-handle"),
        2,
        min_mapped_evidence_coverage=ALIAS_MIN_MAPPED_EVIDENCE_COVERAGE,
    ),
)


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
    cases: dict[str, CapabilityCase] = {}
    for case in snapshot.cases:
        (leaf,) = memberships[case.id]
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
            grader=Memory100Grader(expected, leaf.min_mapped_evidence_coverage),
            digest_tag=(
                f"{snapshot.manifest.digest}:{readiness.corpus_digest}:"
                f"{MEMORY_100_GRADER_REVISION}:{case.id}:{evidence_identity}:"
                f"{leaf.min_mapped_evidence_coverage}"
            ),
            rubric=_answer_rubric(case),
            member_key=audiences[case.audience] or readiness.asker_email,
            shared_audience=case.audience == SHARED_SUBJECT,
        )
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
        tasks.append(
            recall_graded(capability_task(leaf.name, leaf_cases, judge_model=MEMORY_JUDGE_MODEL))
        )
    return Memory100Run(tuple(tasks), readiness)


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
    min_mapped_evidence_coverage: float | None = None

    @property
    def grading(self) -> str:
        statement = (
            "the turn exports a valid memory recall log whose selected memories are ranked "
            "against each expected evidence owner and the answer is non-empty; answer substance "
            "is judged against the semantic rubric"
        )
        if self.min_mapped_evidence_coverage is None:
            return statement
        refs = ", ".join(owner.source_ref for owner in self.expected)
        return (
            f"{statement}; injected recall must additionally cover at least "
            f"{self.min_mapped_evidence_coverage:.0%} of the evidence rows that own a memory item "
            f"({refs}), so the verdict answers to retrieval rather than to the answer text"
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
        coverage = found / expected if expected else None
        evidence: JsonObject = {
            "recallError": recall.error_class,
            "selectedCount": len(selected),
            "expectedCount": expected,
            "evidenceRanks": ranks,
            "unmappedEvidence": [
                owner.source_ref for owner in self.expected if not owner.memory_ids
            ],
            "coverage": coverage,
        }
        if not output.response.strip():
            return CapabilityVerdict(False, "answer is empty", evidence)
        if self.min_mapped_evidence_coverage is not None:
            if coverage is None:
                return CapabilityVerdict(
                    False, "no expected evidence row owns a memory item to recall", evidence
                )
            if coverage < self.min_mapped_evidence_coverage:
                degraded = f" (recall degraded: {recall.error_class})" if recall.error_class else ""
                return CapabilityVerdict(
                    False,
                    f"injected recall covered {found}/{expected} mapped evidence rows, below "
                    f"{self.min_mapped_evidence_coverage:.0%}{degraded}",
                    evidence,
                )
        return CapabilityVerdict(
            True,
            f"answer is present; recall found {found}/{expected} mapped evidence",
            evidence,
        )
