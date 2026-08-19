import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from ufo_ext_memory.events import MEMORY_RECALL_EVENT
from ufo_ext_memory.objects import MEMORY_KIND

from evals.harness.capability import CapabilityCase, CapabilityOutput, CapabilityVerdict
from evals.harness.harness import Json, JsonObject
from evals.harness.judge import MAX_CRITERION_CHARS
from evals.harness.recall import MemoryRecallEvent, recall_graded
from evals.harness.registry import EvalTask, capability_task
from evals.memory_ingestion.materialize import (
    DERIVATION_MODEL,
    IngestionReadiness,
)
from evals.memory_ingestion.models import IngestionCase, load_snapshot

MEMORY_INGESTION_GRADER_REVISION = "derived-evidence-1"
MEMORY_JUDGE_MODEL = "gpt-5.4"
WORKFLOW_WAIT_SECONDS = 1200.0
MEMORY_REF_PATTERN = re.compile(
    rf"\b{MEMORY_KIND}/([0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}})"
)


@dataclass(frozen=True)
class ExpectedDerivedEvidence:
    source_ref: str
    memory_ids: frozenset[UUID]


@dataclass(frozen=True)
class MemoryIngestionRun:
    tasks: tuple[EvalTask, ...]
    readiness: IngestionReadiness


def load_memory_ingestion(snapshot_root: Path, readiness_path: Path) -> MemoryIngestionRun:
    """Load cases whose only recallable corpus is Luna-derived memory."""
    snapshot = load_snapshot(snapshot_root)
    readiness = IngestionReadiness.model_validate_json(readiness_path.read_bytes())
    if readiness.snapshot_digest != snapshot.manifest.digest:
        raise ValueError("memory_ingestion readiness belongs to a different snapshot")
    if readiness.derivation_model != DERIVATION_MODEL:
        raise ValueError(
            f"memory_ingestion readiness used {readiness.derivation_model!r}, "
            f"expected {DERIVATION_MODEL!r}"
        )
    evidence = {item.source_ref: frozenset(item.memory_ids) for item in readiness.evidence}
    if len(evidence) != len(readiness.evidence):
        raise ValueError("memory_ingestion readiness contains duplicate evidence refs")
    missing = sorted(
        {ref for case in snapshot.cases for ref in case.evidence_refs} - evidence.keys()
    )
    if missing:
        raise ValueError(f"memory_ingestion readiness is missing evidence: {', '.join(missing)}")
    grouped: defaultdict[tuple[str, str], list[CapabilityCase]] = defaultdict(list)
    for case in snapshot.cases:
        expected = tuple(
            ExpectedDerivedEvidence(source_ref, evidence[source_ref])
            for source_ref in case.evidence_refs
        )
        identity = json.dumps(
            [
                [item.source_ref, sorted(str(memory_id) for memory_id in item.memory_ids)]
                for item in expected
            ],
            separators=(",", ":"),
        )
        grouped[case.corpus, case.category].append(
            CapabilityCase(
                name=case.id,
                message=case.question,
                grader=MemoryIngestionGrader(expected),
                digest_tag=(
                    f"{snapshot.manifest.digest}:{readiness.corpus_digest}:"
                    f"{DERIVATION_MODEL}:{MEMORY_INGESTION_GRADER_REVISION}:{case.id}:{identity}"
                ),
                rubric=_answer_rubric(case),
                answer_spans_artifacts=True,
                member_key=readiness.asker_email,
                shared_audience=True,
            )
        )
    tasks = tuple(
        recall_graded(
            capability_task(
                f"memory_ingestion.{corpus}.{category}",
                tuple(grouped[corpus, category]),
                judge_model=MEMORY_JUDGE_MODEL,
            )
        )
        for corpus, category in sorted(grouped)
    )
    return MemoryIngestionRun(tasks, readiness)


def _answer_rubric(case: IngestionCase) -> tuple[str, ...]:
    criterion = f"Match this reference answer in substance: {case.expected_answer}"
    if len(criterion) > MAX_CRITERION_CHARS:
        raise ValueError(f"memory_ingestion case {case.id!r} has an oversized reference answer")
    return (criterion,)


@dataclass(frozen=True)
class MemoryIngestionGrader:
    expected: tuple[ExpectedDerivedEvidence, ...]

    @property
    def grading(self) -> str:
        return (
            "the answer is non-empty and every expected evidence turn has a "
            f"{DERIVATION_MODEL}-derived memory item in injected recall or a successful "
            "memory_search result"
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
        searched = {
            UUID(memory_id)
            for call in output.calls
            if call.name == "memory_search" and call.succeeded
            for memory_id in MEMORY_REF_PATTERN.findall(call.result)
        }
        selected = frozenset(recall.memory_ids)
        observed = selected | searched
        hits = {item.source_ref: bool(item.memory_ids & observed) for item in self.expected}
        found = sum(hits.values())
        expected = len(hits)
        ranks: dict[str, Json] = {
            item.source_ref: next(
                (
                    rank
                    for rank, memory_id in enumerate(recall.memory_ids, start=1)
                    if memory_id in item.memory_ids
                ),
                None,
            )
            for item in self.expected
        }
        evidence: JsonObject = {
            "derivationModel": DERIVATION_MODEL,
            "recallError": recall.error_class,
            "selectedCount": len(selected),
            "searchedCount": len(searched),
            "expectedCount": expected,
            "evidenceRanks": ranks,
            "coverage": found / expected if expected else None,
            "unmappedEvidence": [],
            "missingEvidence": [source_ref for source_ref, hit in hits.items() if not hit],
        }
        if not output.response.strip():
            return CapabilityVerdict(False, "answer is empty", evidence)
        if found != expected:
            return CapabilityVerdict(
                False,
                f"derived memory covered {found}/{expected} evidence turns",
                evidence,
            )
        return CapabilityVerdict(
            True,
            f"derived memory covered {found}/{expected} evidence turns",
            evidence,
        )
