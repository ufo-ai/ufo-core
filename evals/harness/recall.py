"""The observed-recall grading contract the memory leaves share: the validated shape of the memory
extension's one recall event, and the suite rollup a recall-graded report carries.

The event is content-free — the ordered memory-item ids the `user_prompt_submit` hook actually
injected into the turn, and the error class when recall degraded — so a grader scores what default
injection surfaced without touching the turn path. A recall-graded case records `coverage`,
`recallError`, and `unmappedEvidence` in its grader evidence; `recall_graded` rolls those onto the
report so a coverage regression shows on stdout and in the viewer, not only inside one case's JSON.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from ufo_ext_memory.events import MAX_RECALL_ERROR_CLASS_CHARS, MAX_RECALLED_MEMORY_IDS

from evals.harness.harness import EvalReport
from evals.harness.registry import EvalTask, rewrapped
from evals.harness.target import CapabilityTarget

MAX_RECALL_SKIP_REASON_CHARS = 64


class MemoryRecallEvent(BaseModel):
    """One turn's injected recall, as the extension exported it."""

    model_config = ConfigDict(extra="forbid")

    memory_ids: tuple[UUID, ...] = Field(max_length=MAX_RECALLED_MEMORY_IDS)
    error_class: str | None = Field(
        default=None, min_length=1, max_length=MAX_RECALL_ERROR_CLASS_CHARS
    )
    skipped: str | None = Field(default=None, min_length=1, max_length=MAX_RECALL_SKIP_REASON_CHARS)

    @model_validator(mode="after")
    def _valid_outcome(self) -> MemoryRecallEvent:
        if len(set(self.memory_ids)) != len(self.memory_ids):
            raise ValueError("memory recall IDs must be unique")
        if self.error_class is not None and self.memory_ids:
            raise ValueError("failed memory recall cannot contain IDs")
        return self


def recall_graded(task: EvalTask) -> EvalTask:
    """The task with the recall aggregates folded onto its report."""
    return rewrapped(task, _with_aggregates)


def _with_aggregates(task: EvalTask) -> EvalTask:
    async def run(target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        return with_recall_aggregates(await task.run(target, slots))

    return replace(task, run=run)


def with_recall_aggregates(report: EvalReport) -> EvalReport:
    coverages: list[float] = []
    degraded_recall_count = 0
    unmapped_evidence_count = 0
    page_evidence_found = 0
    page_evidence_expected = 0
    for case in report.cases:
        selected_attempt = case.evidence.get("selectedAttempt")
        attempts = case.evidence.get("attempts")
        if (
            isinstance(selected_attempt, bool)
            or not isinstance(selected_attempt, int)
            or not isinstance(attempts, list)
            or not 0 <= selected_attempt < len(attempts)
        ):
            raise TypeError("recall-graded selected attempt evidence is invalid")
        attempt = attempts[selected_attempt]
        if not isinstance(attempt, dict):
            raise TypeError("recall-graded selected attempt must be an object")
        grader = attempt.get("grader")
        if grader is None:
            continue
        if not isinstance(grader, dict):
            raise TypeError("recall-graded grader evidence must be an object")
        coverage = grader.get("coverage")
        if coverage is not None:
            if isinstance(coverage, bool) or not isinstance(coverage, (int, float)):
                raise TypeError("recall coverage must be a number or null")
            coverages.append(float(coverage))
        recall_error = grader.get("recallError")
        if recall_error is not None:
            if not isinstance(recall_error, str):
                raise TypeError("recall error must be a string or null")
            degraded_recall_count += 1
        unmapped = grader.get("unmappedEvidence")
        if not isinstance(unmapped, list) or not all(isinstance(item, str) for item in unmapped):
            raise TypeError("unmapped recall evidence must be a list of source refs")
        unmapped_evidence_count += len(unmapped)
        found = grader.get("pageEvidenceFound")
        total = grader.get("pageEvidenceExpected")
        if isinstance(found, bool) or isinstance(total, bool):
            raise TypeError("page evidence counts must be integers")
        if isinstance(found, int) and isinstance(total, int):
            page_evidence_found += found
            page_evidence_expected += total
    mean_coverage = sum(coverages) / len(coverages) if coverages else None
    return report.model_copy(
        update={
            "mean_mapped_evidence_coverage": mean_coverage,
            "min_mapped_evidence_coverage": min(coverages) if coverages else None,
            "degraded_recall_count": degraded_recall_count,
            "unmapped_evidence_count": unmapped_evidence_count,
            "page_evidence_coverage": (
                page_evidence_found / page_evidence_expected if page_evidence_expected else None
            ),
        }
    )
