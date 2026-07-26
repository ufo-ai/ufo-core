"""The `issue_recall` leaf: does default memory injection surface the repository's related issues
and pull requests when the turn's goal is filing a new one?

Each case's graded turn is one issue-filing message and nothing else — no reminder that prior issues
exist, no instruction to search — and a `mid-thread` case sends that ask as the next turn of a
conversation already spent on unrelated technical Q&A. The grade reads the recall event the memory
extension's `user_prompt_submit` hook exported for that exact turn, so it scores what injection
surfaced before the model ran: the rank of each related fixture page's derived facts, the coverage
of the related set, and whether the corpus's distractors outnumbered them in the injected context.
The answer is recorded, and the related issue numbers it cited with it, but the bar is the recall.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from ufo_ext_memory.events import MEMORY_RECALL_EVENT

from evals.harness.capability import CapabilityCase, CapabilityOutput, CapabilityVerdict
from evals.harness.harness import Json, JsonObject
from evals.harness.recall import MemoryRecallEvent, recall_graded
from evals.harness.registry import EvalTask, capability_task
from evals.issue_recall.corpus import (
    ABSENT_FILINGS,
    CASES,
    ISSUE_SHAPED,
    corpus_digest,
    corpus_issue_numbers,
    related_refs,
)
from evals.issue_recall.state import CorpusReadiness

ISSUE_RECALL_TASK = "issue_recall"
ISSUE_RECALL_GRADER_REVISION = "derived-fact-owners-1"
ISSUE_NUMBER_PATTERN = re.compile(r"#(\d{2,5})\b")
MEMORY_SEARCH_TOOL = "memory_search"


@dataclass(frozen=True)
class ExpectedPage:
    """One related fixture page and the derived facts recall can inject for it."""

    source_ref: str
    memory_ids: frozenset[UUID]


@dataclass(frozen=True)
class IssueRecallRun:
    tasks: tuple[EvalTask, ...]
    readiness: CorpusReadiness


def load_issue_recall(readiness_path: Path) -> IssueRecallRun:
    """Build the leaf against one materialized corpus, failing loud when the readiness belongs to a
    different fixture or omits a page a case grades."""
    readiness = CorpusReadiness.model_validate_json(readiness_path.read_bytes())
    if readiness.corpus_digest != corpus_digest():
        raise ValueError("issue_recall readiness belongs to a different fixture corpus")
    owners = {page.source_ref: frozenset(page.memory_ids) for page in readiness.pages}
    haystack = frozenset(memory.memory_id for memory in readiness.ambient)
    shaped = frozenset(
        memory.memory_id
        for memory in readiness.ambient
        if memory.ref in {record.ref for record in ISSUE_SHAPED}
    )
    cases: list[CapabilityCase] = []
    for case in CASES:
        related = frozenset(related_refs(case))
        missing = sorted(ref for ref in related if ref not in owners)
        if missing:
            raise ValueError(f"issue_recall readiness is missing pages: {', '.join(missing)}")
        expected = tuple(ExpectedPage(ref, owners[ref]) for ref in related_refs(case))
        expected_identity = json.dumps(
            [
                [page.source_ref, sorted(str(memory_id) for memory_id in page.memory_ids)]
                for page in expected
            ],
            separators=(",", ":"),
        )
        distractors = haystack | frozenset(
            memory_id
            for source_ref, memory_ids in owners.items()
            if source_ref not in related
            for memory_id in memory_ids
        )
        cases.append(
            CapabilityCase(
                name=case.name,
                message=case.message,
                prior_messages=case.prior_messages,
                grader=IssueRecallGrader(expected, distractors, shaped, case.min_coverage),
                digest_tag=(
                    f"{readiness.corpus_digest}:{ISSUE_RECALL_GRADER_REVISION}:{case.name}:"
                    f"{expected_identity}"
                ),
            )
        )
    cases += [
        CapabilityCase(
            name=f"{absent.slug}:absent-topic",
            message=absent.message,
            grader=AbsentTopicGrader(corpus_issue_numbers(), shaped),
            digest_tag=(
                f"{readiness.corpus_digest}:{ISSUE_RECALL_GRADER_REVISION}:{absent.slug}:absent"
            ),
        )
        for absent in ABSENT_FILINGS
    ]
    return IssueRecallRun(
        (recall_graded(capability_task(ISSUE_RECALL_TASK, tuple(cases))),), readiness
    )


@dataclass(frozen=True)
class AbsentTopicGrader:
    """Score a filing ask the corpus holds no match for: the reply must not attribute it to an
    issue that exists. Recall still injects its eight memories, most turns carrying an issue-shaped
    one, so a reply that answers "already filed" here is answering the shape of its context."""

    corpus_numbers: frozenset[int]
    issue_shaped: frozenset[UUID]

    @property
    def grading(self) -> str:
        return (
            "the turn exports a valid memory recall log and the reply cites no existing corpus "
            "issue or pull request number, since the corpus holds nothing about this topic — a "
            "citation here is form priming rather than recall"
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
        cited = sorted(set(ISSUE_NUMBER_PATTERN.findall(output.response)))
        attributed = sorted(number for number in cited if int(number) in self.corpus_numbers)
        evidence: JsonObject = {
            "recallError": recall.error_class,
            "selectedCount": len(recall.memory_ids),
            "unmappedEvidence": [],
            "issueShapedInjected": sum(
                memory_id in self.issue_shaped for memory_id in recall.memory_ids
            ),
            "citedIssues": cited,
            "attributedToCorpusIssues": attributed,
            "memorySearchCalls": [
                call.name for call in output.calls if call.name == MEMORY_SEARCH_TOOL
            ],
        }
        if recall.error_class is not None:
            return CapabilityVerdict(False, f"recall degraded ({recall.error_class})", evidence)
        if not output.response.strip():
            return CapabilityVerdict(False, "answer is empty", evidence)
        if attributed:
            return CapabilityVerdict(
                False,
                f"the reply attributed an absent topic to existing issues {attributed}",
                evidence,
            )
        return CapabilityVerdict(
            True,
            f"no existing issue was claimed for a topic the corpus lacks "
            f"({evidence['issueShapedInjected']} issue-shaped memories were injected)",
            evidence,
        )


@dataclass(frozen=True)
class IssueRecallGrader:
    """Score the turn's injected recall against the case's related pages."""

    expected: tuple[ExpectedPage, ...]
    distractors: frozenset[UUID]
    issue_shaped: frozenset[UUID]
    min_coverage: float

    @property
    def grading(self) -> str:
        refs = ", ".join(page.source_ref for page in self.expected)
        return (
            "the turn exports a valid memory recall log whose injected memories cover at least "
            f"{self.min_coverage:.0%} of the whole related set ({refs}) — a page that derived no "
            "fact counts against coverage rather than leaving the denominator — and the front of "
            "the injected context, its first slots as many as the related set has pages, is at "
            "least half related rather than distractors"
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
            page.source_ref: next(
                (
                    rank
                    for rank, memory_id in enumerate(selected, start=1)
                    if memory_id in page.memory_ids
                ),
                None,
            )
            for page in self.expected
        }
        mapped = tuple(page for page in self.expected if page.memory_ids)
        found = sum(ranks[page.source_ref] is not None for page in mapped)
        related_ids = frozenset(
            memory_id for page in self.expected for memory_id in page.memory_ids
        )
        related_injected = sum(memory_id in related_ids for memory_id in selected)
        distractor_injected = sum(memory_id in self.distractors for memory_id in selected)
        front = selected[: len(self.expected)]
        related_in_front = sum(memory_id in related_ids for memory_id in front)
        coverage = found / len(self.expected)
        evidence: JsonObject = {
            "recallError": recall.error_class,
            "selectedCount": len(selected),
            "expectedCount": len(self.expected),
            "mappedCount": len(mapped),
            "evidenceRanks": ranks,
            "unmappedEvidence": [page.source_ref for page in self.expected if not page.memory_ids],
            "coverage": coverage,
            "relatedInjected": related_injected,
            "distractorInjected": distractor_injected,
            "frontSlots": len(front),
            "relatedInFront": related_in_front,
            "memorySearchCalls": [
                call.name for call in output.calls if call.name == MEMORY_SEARCH_TOOL
            ],
            "citedIssues": sorted(set(ISSUE_NUMBER_PATTERN.findall(output.response))),
            "issueShapedInjected": sum(memory_id in self.issue_shaped for memory_id in selected),
        }
        if recall.error_class is not None:
            return CapabilityVerdict(False, f"recall degraded ({recall.error_class})", evidence)
        if not mapped:
            return CapabilityVerdict(
                False, "no related page derived a durable memory to recall", evidence
            )
        if coverage < self.min_coverage:
            unmapped = [page.source_ref for page in self.expected if not page.memory_ids]
            derivation = (
                f"; {len(unmapped)} derived no fact to recall: {unmapped}" if unmapped else ""
            )
            return CapabilityVerdict(
                False,
                f"injected recall covered {found}/{len(self.expected)} related pages, below "
                f"{self.min_coverage:.0%}{derivation}",
                evidence,
            )
        if related_in_front * 2 < len(front):
            return CapabilityVerdict(
                False,
                f"distractors led the injected context ({related_in_front} of the first "
                f"{len(front)} injected memories related)",
                evidence,
            )
        return CapabilityVerdict(
            True,
            f"injected recall covered {found}/{len(self.expected)} related pages, "
            f"{related_in_front} of the first {len(front)} injected memories related",
            evidence,
        )
