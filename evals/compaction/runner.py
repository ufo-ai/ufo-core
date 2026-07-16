"""The compaction suite: one digest-pinned task per leaf, graded by deterministic literal survival.

Artifact leaves drive `Compaction.maybe_compact` directly over a snapshot window (chains re-compact
their own output plus fresh filler) and grade the rendered replacement message. The behavior leaf
materializes a window as a live conversation's transcript and grades probe turns through the real
engine — answers, forbidden stale values, and whether an offloaded fact was re-read by path.

The pass bars are deliberately below 1.0-equivalents: the windows overload the summary budget, so
the leaves measure prioritization under loss, and a perfect score is structurally out of reach."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID, uuid4

from evals.compaction.models import (
    CompactionCase,
    CompactionLeaf,
    CompactionProbe,
    PlantedFact,
    estimate_tokens,
)
from evals.compaction.snapshot import load_snapshot
from evals.compaction.target import CompactionTarget
from evals.harness.harness import (
    EvalCaseResult,
    EvalMetric,
    EvalReport,
    Json,
    JsonObject,
    digest_payload,
)
from evals.harness.registry import EvalTask
from evals.harness.target import CapabilityTarget, EvalConversations, TargetResult
from ufo.loop.compaction import COMPACTED_CONTEXT_PREFIX, MAX_REFERENCE_PATHS

COMPACTION_GRADER_REVISION = "literal-survival-2"
SEED_MESSAGE = "Reply with the single word: ready."
TRIGGER_FRACTION = 0.9
OVERLOAD_PASS_WEIGHTED_RECALL = 0.30
BURIED_PASS_WEIGHTED_RECALL = 0.20
SUPERSESSION_PASS_RECALL = 0.60
SUPERSESSION_MAX_STALE_RATE = 0.20
CHAIN_PASS_FINAL_SURVIVAL = 0.25
CHAIN_CRITICAL_WEIGHT = 4
REFERENCE_PASS_WEIGHTED_COVERAGE = 0.5
FILES_HEADING = "## Files and outputs"
REFERENCES_HEADING = "## Durable references"
LEAF_ORDER: tuple[CompactionLeaf, ...] = (
    "overload",
    "buried",
    "supersession",
    "chain",
    "reference",
    "image",
    "behavior",
)


class CompactionRunTarget(Protocol):
    @property
    def conversations(self) -> EvalConversations: ...

    @property
    def compaction(self) -> CompactionTarget | None: ...

    async def step(
        self, conversation_id: UUID, message: str, idempotency_key: str
    ) -> TargetResult: ...


@dataclass(frozen=True)
class CompactionRun:
    tasks: tuple[EvalTask, ...]


def load_compaction(root: Path) -> CompactionRun:
    snapshot = load_snapshot(root)
    trigger_tokens = max(1, int(snapshot.manifest.target_tokens * TRIGGER_FRACTION))
    tasks: list[EvalTask] = []
    for leaf in LEAF_ORDER:
        cases = tuple(case for case in snapshot.cases if case.leaf == leaf)
        if not cases:
            continue
        name = f"compaction.{leaf}"
        suite = CompactionSuite(
            leaf=leaf,
            cases=cases,
            digest=digest_payload(
                {
                    "runner": "compaction-case",
                    "task": name,
                    "snapshot": snapshot.manifest.digest,
                    "grader": COMPACTION_GRADER_REVISION,
                    "triggerTokens": trigger_tokens,
                    "cases": [case.id for case in cases],
                }
            ),
            trigger_tokens=trigger_tokens,
        )
        tasks.append(
            EvalTask(
                name=name,
                suite="compaction",
                digest=suite.digest,
                cases=tuple(case.id for case in cases),
                run=suite.run,
            )
        )
    return CompactionRun(tuple(tasks))


@dataclass(frozen=True)
class GenerationGrade:
    """`harvested` holds reference facts whose path the pipeline's durable-reference block kept
    (recency-capped at MAX_REFERENCE_PATHS); `carried` holds those the summary's own files field
    preserved — the split is what the reference leaf exists to expose."""

    generation: int
    before_tokens: int
    after_tokens: int
    present: frozenset[str]
    stale_present: frozenset[str]
    harvested: frozenset[str]
    carried: frozenset[str]

    @property
    def referenced(self) -> frozenset[str]:
        return self.harvested | self.carried


@dataclass(frozen=True)
class CompactionSuite:
    leaf: CompactionLeaf
    cases: tuple[CompactionCase, ...]
    digest: str
    trigger_tokens: int

    async def run(self, target: CapabilityTarget) -> EvalReport:
        run_target = cast(CompactionRunTarget, target)
        lab = run_target.compaction
        if lab is None:
            raise RuntimeError("the compaction suite requires the compaction eval target")
        results: list[EvalCaseResult] = []
        for case in self.cases:
            if case.leaf == "behavior":
                results.extend(await self._behavior_case(case, run_target, lab))
            else:
                results.append(await self._artifact_case(case, lab))
        return EvalReport(
            name=f"compaction.{self.leaf}",
            suite="compaction",
            digest=self.digest,
            cases=tuple(results),
            metrics=self._metrics(tuple(results)),
        )

    async def _artifact_case(self, case: CompactionCase, lab: CompactionTarget) -> EvalCaseResult:
        compactor = lab.compactor(uuid4(), self.trigger_tokens)
        window = case.messages
        grades: list[GenerationGrade] = []
        tokens_spent = 0
        for generation in range(1, case.generations + 1):
            if generation > 1:
                window = (*window, *case.extensions[generation - 2].messages)
            before_tokens = estimate_tokens(window)
            after, usages = await compactor.maybe_compact(window)
            if not usages:
                return EvalCaseResult(
                    name=case.id,
                    passed=False,
                    reason=f"generation {generation} window never crossed the compaction trigger",
                    evidence=self._evidence(case, grades, tokens_spent),
                )
            rendered = str(after[0].content)
            if not rendered.startswith(COMPACTED_CONTEXT_PREFIX):
                return EvalCaseResult(
                    name=case.id,
                    passed=False,
                    reason=f"generation {generation} produced no compacted-context message",
                    evidence=self._evidence(case, grades, tokens_spent),
                )
            files_block = _section(rendered, FILES_HEADING)
            references_block = _section(rendered, REFERENCES_HEADING)
            grades.append(
                GenerationGrade(
                    generation=generation,
                    before_tokens=before_tokens,
                    after_tokens=estimate_tokens(after),
                    present=frozenset(fact.id for fact in case.facts if fact.literal in rendered),
                    stale_present=frozenset(
                        fact.id
                        for fact in case.facts
                        if fact.stale_literal and fact.stale_literal in rendered
                    ),
                    harvested=frozenset(
                        fact.id
                        for fact in case.facts
                        if fact.kind == "reference" and fact.path in references_block
                    ),
                    carried=frozenset(
                        fact.id
                        for fact in case.facts
                        if fact.kind == "reference" and fact.path in files_block
                    ),
                )
            )
            tokens_spent += sum(usage.input_tokens + usage.output_tokens for usage in usages)
            window = after
        passed, reason = self._verdict(case, grades[-1])
        return EvalCaseResult(
            name=case.id,
            passed=passed,
            reason=reason,
            evidence=self._evidence(case, grades, tokens_spent),
        )

    def _verdict(self, case: CompactionCase, final: GenerationGrade) -> tuple[bool, str]:
        match case.leaf:
            case "overload":
                recall = _weighted_recall(case.facts, final.present, frozenset({"decision"}))
                if recall is None:
                    return False, "case plants no gradeable decision facts"
                return (
                    recall >= OVERLOAD_PASS_WEIGHTED_RECALL,
                    f"weighted recall {recall:.0%} against bar "
                    f"{OVERLOAD_PASS_WEIGHTED_RECALL:.0%} under summary-budget overload",
                )
            case "buried":
                buried = _weighted_recall(case.facts, final.present, frozenset({"buried"}))
                spoken = _weighted_recall(case.facts, final.present, frozenset({"decision"}))
                if buried is None:
                    return False, "case plants no buried facts"
                return (
                    buried >= BURIED_PASS_WEIGHTED_RECALL,
                    f"buried recall {buried:.0%} against bar "
                    f"{BURIED_PASS_WEIGHTED_RECALL:.0%} "
                    f"(spoken comparator {spoken:.0%})"
                    if spoken is not None
                    else f"buried recall {buried:.0%}",
                )
            case "supersession":
                corrected = [fact for fact in case.facts if fact.kind == "superseded"]
                recall = sum(fact.id in final.present for fact in corrected) / len(corrected)
                stale = sum(fact.id in final.stale_present for fact in corrected) / len(corrected)
                passed = recall >= SUPERSESSION_PASS_RECALL and stale <= SUPERSESSION_MAX_STALE_RATE
                return passed, (
                    f"correction recall {recall:.0%} (bar {SUPERSESSION_PASS_RECALL:.0%}), "
                    f"stale rate {stale:.0%} (cap {SUPERSESSION_MAX_STALE_RATE:.0%})"
                )
            case "reference":
                coverage = _weighted_reference_coverage(case.facts, final)
                if coverage is None:
                    return False, "case plants no reference facts"
                return (
                    coverage >= REFERENCE_PASS_WEIGHTED_COVERAGE,
                    f"weighted reference coverage {coverage:.0%} against bar "
                    f"{REFERENCE_PASS_WEIGHTED_COVERAGE:.0%} — the durable-reference block "
                    f"keeps at most the {MAX_REFERENCE_PATHS} most recent paths, so clearing "
                    "the bar requires the summary's own files field to carry the heavy early "
                    "references",
                )
            case "chain":
                survival = _weighted_recall(
                    case.facts,
                    final.present,
                    frozenset({"decision"}),
                    min_weight=CHAIN_CRITICAL_WEIGHT,
                )
                if survival is None:
                    return False, "chain case plants no critical-weight facts"
                return (
                    survival >= CHAIN_PASS_FINAL_SURVIVAL,
                    f"generation {final.generation} critical survival {survival:.0%} against "
                    f"bar {CHAIN_PASS_FINAL_SURVIVAL:.0%}",
                )
            case "image":
                image = next(fact for fact in case.facts if fact.kind == "image")
                survived = image.id in final.present
                return survived, (
                    "image-borne fact survived the boundary"
                    if survived
                    else "image-borne fact died at the boundary (the head image becomes a marker)"
                )
            case _:
                return False, f"leaf {case.leaf!r} has no artifact verdict"

    def _evidence(
        self, case: CompactionCase, grades: list[GenerationGrade], tokens_spent: int
    ) -> JsonObject:
        final = grades[-1] if grades else None
        facts: list[Json] = [
            {
                "id": fact.id,
                "kind": fact.kind,
                "weight": fact.weight,
                "present": final is not None and fact.id in final.present,
                "stalePresent": final is not None and fact.id in final.stale_present,
                "harvested": final is not None and fact.id in final.harvested,
                "carried": final is not None and fact.id in final.carried,
            }
            for fact in case.facts
        ]
        generations: list[Json] = [
            {
                "generation": grade.generation,
                "beforeTokens": grade.before_tokens,
                "afterTokens": grade.after_tokens,
                "weightedRecall": _weighted_recall(
                    case.facts, grade.present, frozenset({"decision"})
                ),
                "criticalSurvival": _weighted_recall(
                    case.facts,
                    grade.present,
                    frozenset({"decision"}),
                    min_weight=CHAIN_CRITICAL_WEIGHT,
                ),
            }
            for grade in grades
        ]
        evidence: JsonObject = {
            "facts": facts,
            "generations": generations,
            "tokensSpent": tokens_spent,
        }
        if final is None:
            return evidence
        evidence["weightedRecall"] = _weighted_recall(
            case.facts, final.present, frozenset({"decision"})
        )
        evidence["distractorRate"] = _presence_rate(case.facts, final.present, "distractor")
        if any(fact.kind == "buried" for fact in case.facts):
            evidence["buriedRecall"] = _weighted_recall(
                case.facts, final.present, frozenset({"buried"})
            )
        superseded = [fact for fact in case.facts if fact.kind == "superseded"]
        if superseded:
            evidence["correctionRecall"] = sum(
                fact.id in final.present for fact in superseded
            ) / len(superseded)
            evidence["staleRate"] = sum(
                fact.id in final.stale_present for fact in superseded
            ) / len(superseded)
        references = [fact for fact in case.facts if fact.kind == "reference"]
        if references:
            evidence["referenceCoverage"] = sum(
                fact.id in final.referenced for fact in references
            ) / len(references)
            evidence["weightedReferenceCoverage"] = _weighted_reference_coverage(case.facts, final)
            evidence["harvestedCount"] = len(final.harvested)
            evidence["carriedCount"] = len(final.carried)
            evidence["harvesterCap"] = MAX_REFERENCE_PATHS
        image = next((fact for fact in case.facts if fact.kind == "image"), None)
        if image is not None:
            evidence["imageSurvival"] = 1.0 if image.id in final.present else 0.0
        return evidence

    async def _behavior_case(
        self, case: CompactionCase, target: CompactionRunTarget, lab: CompactionTarget
    ) -> list[EvalCaseResult]:
        conversation_id = await target.conversations.open(case.id)
        seed = await target.step(conversation_id, SEED_MESSAGE, f"{case.id}:{conversation_id}:seed")
        if not seed.clean:
            return [
                EvalCaseResult(
                    name=f"{case.id}.{probe.id}",
                    passed=False,
                    reason=f"seed turn failed: {seed.failure_reason}",
                    evidence={"question": probe.question},
                )
                for probe in case.probes
            ]
        files = {fact.path: fact.body for fact in case.facts if fact.kind == "reference"}
        await lab.materialize(conversation_id, case.messages, files)
        results = [
            _grade_probe(
                case,
                probe,
                await target.step(
                    conversation_id, probe.question, f"{case.id}:{conversation_id}:{probe.id}"
                ),
            )
            for probe in case.probes
        ]
        record = await lab.compactor(conversation_id).read_record(1)
        if record is None:
            return [
                result.model_copy(
                    update={
                        "passed": False,
                        "reason": f"{result.reason}; compaction never fired on the probe turns",
                    }
                )
                for result in results
            ]
        compaction_evidence: JsonObject = {
            "beforeTokens": estimate_tokens(record.before),
            "afterTokens": estimate_tokens(record.after),
        }
        results[0] = results[0].model_copy(
            update={"evidence": {**results[0].evidence, "compaction": compaction_evidence}}
        )
        return results

    def _metrics(self, results: tuple[EvalCaseResult, ...]) -> tuple[EvalMetric, ...]:
        match self.leaf:
            case "overload":
                return _rate_metrics(results, ("weighted_recall", "distractor_rate"))
            case "buried":
                return _rate_metrics(results, ("buried_recall", "weighted_recall"))
            case "supersession":
                return _rate_metrics(results, ("correction_recall", "stale_rate"))
            case "reference":
                return _rate_metrics(results, ("reference_coverage", "weighted_reference_coverage"))
            case "chain":
                return _chain_metrics(results)
            case "image":
                return _rate_metrics(results, ("image_survival",))
            case "behavior":
                scored = [result for result in results if not result.excluded]
                if not scored:
                    return ()
                rate = sum(result.passed for result in scored) / len(scored)
                return (EvalMetric(name="probe_pass_rate", value=rate),)
            case _:
                return ()


def _grade_probe(
    case: CompactionCase, probe: CompactionProbe, outcome: TargetResult
) -> EvalCaseResult:
    name = f"{case.id}.{probe.id}"
    calls: list[Json] = [call.name for call in outcome.output.calls]
    evidence: JsonObject = {
        "question": probe.question,
        "response": outcome.output.response,
        "calls": calls,
    }
    if not outcome.clean:
        return EvalCaseResult(
            name=name, passed=False, reason=outcome.failure_reason, evidence=evidence
        )
    answer = outcome.output.response
    missing = [literal for literal in probe.expect_literals if literal not in answer]
    forbidden = [literal for literal in probe.forbid_literals if literal in answer]
    read_touched = not probe.expect_read_path or any(
        probe.expect_read_path in json.dumps(call.input, sort_keys=True)
        for call in outcome.output.calls
    )
    evidence["missingLiterals"] = cast(list[Json], list(missing))
    evidence["forbiddenLiterals"] = cast(list[Json], list(forbidden))
    evidence["readPathTouched"] = read_touched
    if missing:
        return EvalCaseResult(
            name=name,
            passed=False,
            reason=f"answer omits the planted value(s): {', '.join(missing)}",
            evidence=evidence,
        )
    if forbidden:
        return EvalCaseResult(
            name=name,
            passed=False,
            reason=f"answer repeats the superseded value(s): {', '.join(forbidden)}",
            evidence=evidence,
        )
    if not read_touched:
        return EvalCaseResult(
            name=name,
            passed=False,
            reason=f"trajectory never re-read {probe.expect_read_path}",
            evidence=evidence,
        )
    return EvalCaseResult(
        name=name, passed=True, reason="answer carries the surviving value", evidence=evidence
    )


def _section(rendered: str, heading: str) -> str:
    start = rendered.find(heading)
    if start == -1:
        return ""
    end = rendered.find("\n## ", start + len(heading))
    return rendered[start:end] if end != -1 else rendered[start:]


def _weighted_reference_coverage(
    facts: tuple[PlantedFact, ...], final: GenerationGrade
) -> float | None:
    references = [fact for fact in facts if fact.kind == "reference"]
    if not references:
        return None
    total = sum(fact.weight for fact in references)
    return sum(fact.weight for fact in references if fact.id in final.referenced) / total


def _weighted_recall(
    facts: tuple[PlantedFact, ...],
    present: frozenset[str],
    kinds: frozenset[str],
    min_weight: int = 1,
) -> float | None:
    scored = [fact for fact in facts if fact.kind in kinds and fact.weight >= min_weight]
    if not scored:
        return None
    total = sum(fact.weight for fact in scored)
    return sum(fact.weight for fact in scored if fact.id in present) / total


def _presence_rate(
    facts: tuple[PlantedFact, ...], present: frozenset[str], kind: str
) -> float | None:
    scored = [fact for fact in facts if fact.kind == kind]
    if not scored:
        return None
    return sum(fact.id in present for fact in scored) / len(scored)


def _rate_metrics(
    results: tuple[EvalCaseResult, ...], names: tuple[str, ...]
) -> tuple[EvalMetric, ...]:
    key_by_name = {
        "weighted_recall": "weightedRecall",
        "buried_recall": "buriedRecall",
        "distractor_rate": "distractorRate",
        "correction_recall": "correctionRecall",
        "stale_rate": "staleRate",
        "reference_coverage": "referenceCoverage",
        "weighted_reference_coverage": "weightedReferenceCoverage",
        "image_survival": "imageSurvival",
    }
    metrics: list[EvalMetric] = []
    for name in names:
        values = [
            value
            for result in results
            if isinstance(value := result.evidence.get(key_by_name[name]), (int, float))
            and not isinstance(value, bool)
        ]
        if values:
            metrics.append(EvalMetric(name=name, value=sum(values) / len(values)))
    return tuple(metrics)


def _chain_metrics(results: tuple[EvalCaseResult, ...]) -> tuple[EvalMetric, ...]:
    by_generation: dict[int, list[float]] = {}
    for result in results:
        generations = result.evidence.get("generations")
        if not isinstance(generations, list):
            continue
        for entry in generations:
            if not isinstance(entry, dict):
                continue
            generation = entry.get("generation")
            survival = entry.get("criticalSurvival")
            if isinstance(generation, int) and not isinstance(generation, bool):
                if isinstance(survival, (int, float)) and not isinstance(survival, bool):
                    by_generation.setdefault(generation, []).append(float(survival))
    return tuple(
        EvalMetric(name=f"survival_gen{generation}", value=sum(values) / len(values))
        for generation, values in sorted(by_generation.items())
    )
