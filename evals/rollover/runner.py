"""The rollover suite: one digest-pinned task per leaf, graded by deterministic recovery fidelity.

Artifact leaves drive `ContextRollover.maybe_cross` directly over a snapshot window (chains
roll their own output plus fresh filler over again) and grade the boundary's two recovery surfaces:
the recovery record the fresh window opens with, and the journal range that record addresses. The
behavior leaf materializes a window as a live conversation's transcript and grades probe turns
through the real engine — answers, forbidden stale values, and whether an offloaded fact was
re-read by path.

Nothing at the boundary is model-authored, so the fidelity bars are 1.0: a fact the window held and
the boundary made unrecoverable is a defect, not a prioritization trade. What the leaves still
measure is the split — how much the record states outright, how much stays readable by line,
and what the boundary must never assert as current (a superseded value, a distractor)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from time import perf_counter
from typing import Protocol, cast
from uuid import UUID, uuid4

from ufo_ext_context_rollover.rollover import (
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    HISTORY_LINE,
    OBJECTIVE_HEADING,
    PENDING_HEADING,
    ROLLOVER_PREFIX,
    USER_INPUTS_HEADING,
)

from evals.harness.harness import (
    EvalCaseResult,
    EvalMetric,
    EvalReport,
    Json,
    JsonObject,
    digest_payload,
)
from evals.harness.registry import EvalTask, gather_cases
from evals.harness.target import CapabilityTarget, EvalConversations, TargetResult
from evals.rollover.models import (
    PlantedFact,
    RolloverCase,
    RolloverLeaf,
    RolloverProbe,
    estimate_tokens,
)
from evals.rollover.snapshot import load_snapshot
from evals.rollover.target import RolloverTarget
from ufo.runtime.turns.transcript import RecoveryRecord

ROLLOVER_GRADER_REVISION = "recovery-fidelity-1"
SEED_MESSAGE = "Reply with the single word: ready."
TRIGGER_FRACTION = 0.9
OVERLOAD_PASS_WEIGHTED_FIDELITY = 1.0
BURIED_PASS_WEIGHTED_FIDELITY = 1.0
SUPERSESSION_PASS_RECALL = 1.0
SUPERSESSION_MAX_STALE_RATE = 0.0
CHAIN_PASS_FINAL_FIDELITY = 1.0
CHAIN_CRITICAL_WEIGHT = 4
REFERENCE_PASS_WEIGHTED_COVERAGE = 1.0
LEAF_ORDER: tuple[RolloverLeaf, ...] = (
    "overload",
    "buried",
    "supersession",
    "chain",
    "reference",
    "image",
    "behavior",
    "real",
)


class RolloverRunTarget(Protocol):
    @property
    def conversations(self) -> EvalConversations: ...

    @property
    def rollover(self) -> RolloverTarget | None: ...

    async def step(
        self, conversation_id: UUID, message: str, idempotency_key: str
    ) -> TargetResult: ...


@dataclass(frozen=True)
class RolloverRun:
    tasks: tuple[EvalTask, ...]


def load_rollover(root: Path) -> RolloverRun:
    snapshot = load_snapshot(root)
    trigger_tokens = max(1, int(snapshot.manifest.target_tokens * TRIGGER_FRACTION))
    tasks: list[EvalTask] = []
    for leaf in LEAF_ORDER:
        cases = tuple(case for case in snapshot.cases if case.leaf == leaf)
        if not cases:
            continue
        name = f"rollover.{leaf}"
        suite = RolloverSuite(
            leaf=leaf,
            cases=cases,
            digest=digest_payload(
                {
                    "runner": "rollover-case",
                    "task": name,
                    "snapshot": snapshot.manifest.digest,
                    "grader": ROLLOVER_GRADER_REVISION,
                    "triggerTokens": trigger_tokens,
                    "cases": [case.id for case in cases],
                }
            ),
            trigger_tokens=trigger_tokens,
        )
        tasks.append(
            EvalTask(
                name=name,
                suite="rollover",
                digest=suite.digest,
                cases=tuple(case.id for case in cases),
                run=suite.run,
            )
        )
    return RolloverRun(tuple(tasks))


@dataclass(frozen=True)
class GenerationGrade:
    """One boundary's two recovery surfaces, kept apart. `carried` holds the facts the recovery
    record states outright; `journaled` holds those the history lines the record names still carry,
    which `read` reaches by line; `searchable` holds those anywhere in the history file, which
    `grep` reaches after any number of boundaries. `stale_carried` holds the superseded values the
    record asserts in its own voice — outside the member-words and unread-results sections, which
    quote history in order and assert nothing. `addressed` holds the reference facts whose offload
    path the named lines still carry, so the agent can read the file the path names."""

    generation: int
    before_tokens: int
    after_tokens: int
    carried: frozenset[str]
    journaled: frozenset[str]
    searchable: frozenset[str]
    stale_carried: frozenset[str]
    addressed: frozenset[str]

    @property
    def present(self) -> frozenset[str]:
        return self.carried | self.journaled


@dataclass(frozen=True)
class RolloverSuite:
    leaf: RolloverLeaf
    cases: tuple[RolloverCase, ...]
    digest: str
    trigger_tokens: int

    async def run(self, target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        run_target = cast(RolloverRunTarget, target)
        lab = run_target.rollover
        if lab is None:
            raise RuntimeError("the rollover suite requires the rollover eval target")
        grouped = await gather_cases(
            slots, tuple(partial(self._case_results, case, run_target, lab) for case in self.cases)
        )
        results = tuple(result for group in grouped for result in group)
        return EvalReport(
            name=f"rollover.{self.leaf}",
            suite="rollover",
            digest=self.digest,
            cases=results,
            metrics=self._metrics(results),
        )

    async def _case_results(
        self, case: RolloverCase, run_target: RolloverRunTarget, lab: RolloverTarget
    ) -> tuple[EvalCaseResult, ...]:
        if case.leaf in ("behavior", "real"):
            return tuple(await self._behavior_case(case, run_target, lab))
        return (await self._artifact_case(case, lab),)

    async def _artifact_case(self, case: RolloverCase, lab: RolloverTarget) -> EvalCaseResult:
        conversation_id = uuid4()
        rollover = lab.rollover(conversation_id, self.trigger_tokens)
        window = case.messages
        grades: list[GenerationGrade] = []
        for generation in range(1, case.generations + 1):
            if generation > 1:
                window = (*window, *case.extensions[generation - 2].messages)
            before_tokens = estimate_tokens(window)
            outcome = await rollover.maybe_cross(window)
            if not outcome.crossed:
                return EvalCaseResult(
                    name=case.id,
                    passed=False,
                    reason=f"generation {generation} window never crossed the rollover line",
                    evidence=self._evidence(case, grades),
                )
            rendered = str(outcome.messages[0].content)
            if not rendered.startswith(ROLLOVER_PREFIX):
                return EvalCaseResult(
                    name=case.id,
                    passed=False,
                    reason=f"generation {generation} produced no recovery-record message",
                    evidence=self._evidence(case, grades),
                )
            record = await rollover.read_record(generation)
            if record is None:
                return EvalCaseResult(
                    name=case.id,
                    passed=False,
                    reason=f"generation {generation} persisted no rollover record",
                    evidence=self._evidence(case, grades),
                )
            grades.append(
                await _grade_boundary(
                    case,
                    lab,
                    conversation_id,
                    record.recovery,
                    rendered,
                    generation=generation,
                    before_tokens=before_tokens,
                    after_tokens=estimate_tokens(outcome.messages),
                )
            )
            window = outcome.messages
        passed, reason = self._verdict(case, grades[-1])
        return EvalCaseResult(
            name=case.id, passed=passed, reason=reason, evidence=self._evidence(case, grades)
        )

    def _verdict(self, case: RolloverCase, final: GenerationGrade) -> tuple[bool, str]:
        match case.leaf:
            case "overload":
                fidelity = _weighted_recall(case.facts, final.present, frozenset({"decision"}))
                stated = _weighted_recall(case.facts, final.carried, frozenset({"decision"}))
                if fidelity is None or stated is None:
                    return False, "case plants no gradeable decision facts"
                return (
                    fidelity >= OVERLOAD_PASS_WEIGHTED_FIDELITY,
                    f"weighted recovery fidelity {fidelity:.0%} against bar "
                    f"{OVERLOAD_PASS_WEIGHTED_FIDELITY:.0%} — the record states {stated:.0%} and "
                    "the history lines it names hold the rest",
                )
            case "buried":
                buried = _weighted_recall(case.facts, final.present, frozenset({"buried"}))
                spoken = _weighted_recall(case.facts, final.present, frozenset({"decision"}))
                if buried is None:
                    return False, "case plants no buried facts"
                return (
                    buried >= BURIED_PASS_WEIGHTED_FIDELITY,
                    f"buried recovery fidelity {buried:.0%} against bar "
                    f"{BURIED_PASS_WEIGHTED_FIDELITY:.0%} "
                    f"(spoken comparator {spoken:.0%})"
                    if spoken is not None
                    else f"buried recovery fidelity {buried:.0%}",
                )
            case "supersession":
                corrected = [fact for fact in case.facts if fact.kind == "superseded"]
                recall = sum(fact.id in final.present for fact in corrected) / len(corrected)
                stale = sum(fact.id in final.stale_carried for fact in corrected) / len(corrected)
                passed = recall >= SUPERSESSION_PASS_RECALL and stale <= SUPERSESSION_MAX_STALE_RATE
                return passed, (
                    f"correction recall {recall:.0%} (bar {SUPERSESSION_PASS_RECALL:.0%}), "
                    f"stale rate {stale:.0%} (cap {SUPERSESSION_MAX_STALE_RATE:.0%}) — a stale "
                    "value stays readable in the journal, but the record may not state it"
                )
            case "reference":
                coverage = _weighted_reference_coverage(case.facts, final)
                if coverage is None:
                    return False, "case plants no reference facts"
                return (
                    coverage >= REFERENCE_PASS_WEIGHTED_COVERAGE,
                    f"weighted reference coverage {coverage:.0%} against bar "
                    f"{REFERENCE_PASS_WEIGHTED_COVERAGE:.0%} — an offload path clears the bar "
                    "only through the history lines the record names",
                )
            case "chain":
                fidelity = _weighted_recall(
                    case.facts,
                    final.searchable,
                    frozenset({"decision"}),
                    min_weight=CHAIN_CRITICAL_WEIGHT,
                )
                if fidelity is None:
                    return False, "chain case plants no critical-weight facts"
                return (
                    fidelity >= CHAIN_PASS_FINAL_FIDELITY,
                    f"after generation {final.generation} the critical facts are "
                    f"{fidelity:.0%} reachable with grep against bar "
                    f"{CHAIN_PASS_FINAL_FIDELITY:.0%}",
                )
            case "image":
                image = next(fact for fact in case.facts if fact.kind == "image")
                survived = image.id in final.searchable
                return survived, (
                    "image-borne fact survived the boundary"
                    if survived
                    else "image-borne fact died at the boundary (the journal keeps rendered text, "
                    "so an image becomes a marker)"
                )
            case _:
                return False, f"leaf {case.leaf!r} has no artifact verdict"

    def _grading(self, case: RolloverCase) -> str:
        match case.leaf:
            case "overload":
                return (
                    "every generation rolls over; weighted decision-fact recovery fidelity — the "
                    "recovery record plus the journal range it addresses — "
                    f"≥ {OVERLOAD_PASS_WEIGHTED_FIDELITY:.0%}"
                )
            case "buried":
                return (
                    "every generation rolls over; buried-fact weighted recovery fidelity "
                    f"≥ {BURIED_PASS_WEIGHTED_FIDELITY:.0%}"
                )
            case "supersession":
                return (
                    "every generation rolls over; correction recall "
                    f"≥ {SUPERSESSION_PASS_RECALL:.0%} with the recovery record stating a "
                    f"superseded value at a rate ≤ {SUPERSESSION_MAX_STALE_RATE:.0%}"
                )
            case "reference":
                return (
                    "every generation rolls over; weighted reference coverage "
                    f"≥ {REFERENCE_PASS_WEIGHTED_COVERAGE:.0%} — the history lines the record "
                    "names must carry every offload path"
                )
            case "chain":
                return (
                    f"every generation rolls over; critical-weight (≥{CHAIN_CRITICAL_WEIGHT}) "
                    f"facts stay reachable with grep after generation {case.generations} "
                    f"at ≥ {CHAIN_PASS_FINAL_FIDELITY:.0%}"
                )
            case "image":
                return "the image-borne fact survives the rollover boundary"
            case "real":
                return (
                    "sanity is the pass bar: the case passes when the window rolls over through "
                    "the live probe turn; probe verdicts and record-graded recovery fidelity are "
                    "recorded as observability metrics, like the chain leaf's generation rates"
                )
            case _:
                raise RuntimeError(f"leaf {case.leaf!r} has no artifact grading criteria")

    def _evidence(self, case: RolloverCase, grades: list[GenerationGrade]) -> JsonObject:
        final = grades[-1] if grades else None
        facts: list[Json] = [
            {
                "id": fact.id,
                "kind": fact.kind,
                "weight": fact.weight,
                "present": final is not None and fact.id in final.present,
                "carried": final is not None and fact.id in final.carried,
                "journaled": final is not None and fact.id in final.journaled,
                "searchable": final is not None and fact.id in final.searchable,
                "staleCarried": final is not None and fact.id in final.stale_carried,
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
                    grade.searchable,
                    frozenset({"decision"}),
                    min_weight=CHAIN_CRITICAL_WEIGHT,
                ),
            }
            for grade in grades
        ]
        evidence: JsonObject = {
            "grading": self._grading(case),
            "facts": facts,
            "generations": generations,
        }
        if final is None:
            return evidence
        evidence["weightedRecall"] = _weighted_recall(
            case.facts, final.present, frozenset({"decision"})
        )
        evidence["recordCarryRate"] = _weighted_recall(
            case.facts, final.carried, frozenset({"decision"})
        )
        evidence["distractorRate"] = _presence_rate(case.facts, final.carried, "distractor")
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
                fact.id in final.stale_carried for fact in superseded
            ) / len(superseded)
        references = [fact for fact in case.facts if fact.kind == "reference"]
        if references:
            evidence["referenceCoverage"] = sum(
                fact.id in final.addressed for fact in references
            ) / len(references)
            evidence["weightedReferenceCoverage"] = _weighted_reference_coverage(case.facts, final)
            evidence["addressedCount"] = len(final.addressed)
        image = next((fact for fact in case.facts if fact.kind == "image"), None)
        if image is not None:
            evidence["imageSurvival"] = 1.0 if image.id in final.searchable else 0.0
        return evidence

    async def _behavior_case(
        self, case: RolloverCase, target: RolloverRunTarget, lab: RolloverTarget
    ) -> list[EvalCaseResult]:
        window_tokens = estimate_tokens(case.messages)
        if window_tokens <= lab.live_trigger_tokens:
            return _unreachable_trigger(case, lab, window_tokens)
        conversation_id = await target.conversations.open(case.id)
        seed = await target.step(conversation_id, SEED_MESSAGE, f"{case.id}:{conversation_id}:seed")
        if not seed.clean:
            reason = f"seed turn failed: {seed.failure_reason}"
            return [
                EvalCaseResult(
                    name=f"{case.id}.{probe.id}",
                    passed=False,
                    reason=reason,
                    evidence={
                        "question": probe.question,
                        "grading": _probe_grading(probe),
                        "attempts": [_turn_attempt(seed, False, reason, None)],
                        "selectedAttempt": 0,
                    },
                )
                for probe in case.probes
            ]
        files = {fact.path: fact.body for fact in case.facts if fact.kind == "reference"}
        await lab.materialize(conversation_id, case.messages, files)
        results = [
            await _timed_probe(case, probe, target, conversation_id) for probe in case.probes
        ]
        record = await lab.rollover(conversation_id).read_record(1)
        if record is None:
            return [
                result.model_copy(
                    update={
                        "passed": False,
                        "reason": f"{result.reason}; the rollover never fired on the probe turns",
                    }
                )
                for result in results
            ]
        rollover_evidence: JsonObject = {
            "beforeTokens": estimate_tokens(record.before),
            "afterTokens": estimate_tokens(record.after),
            "firstEntryId": record.recovery.first_entry_id,
            "lastEntryId": record.recovery.last_entry_id,
        }
        results[0] = results[0].model_copy(
            update={"evidence": {**results[0].evidence, "rollover": rollover_evidence}}
        )
        if case.leaf == "real":
            grade = await _grade_boundary(
                case,
                lab,
                conversation_id,
                record.recovery,
                str(record.after[0].content),
                generation=1,
                before_tokens=estimate_tokens(record.before),
                after_tokens=estimate_tokens(record.after),
            )
            artifact = self._evidence(case, [grade])
            artifact["artifactGrading"] = artifact.pop("grading")
            results[0] = results[0].model_copy(
                update={"evidence": {**artifact, **results[0].evidence}}
            )
            results = [
                result.model_copy(
                    update={
                        "excluded": True,
                        "reason": (
                            f"{result.reason} "
                            "(recorded as observability, excluded from the pass bar)"
                        ),
                    }
                )
                for result in results
            ]
            results.append(
                EvalCaseResult(
                    name=f"{case.id}.rolled",
                    passed=True,
                    reason=(
                        "window rolled over through the live probe turn; probe verdicts and "
                        "recovery-fidelity metrics recorded as observability"
                    ),
                    evidence={"grading": self._grading(case), "rollover": rollover_evidence},
                )
            )
        return results

    def _metrics(self, results: tuple[EvalCaseResult, ...]) -> tuple[EvalMetric, ...]:
        match self.leaf:
            case "overload":
                return _rate_metrics(
                    results, ("weighted_recall", "record_carry_rate", "distractor_rate")
                )
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
            case "real":
                probes = [result for result in results if not result.name.endswith(".rolled")]
                if not probes:
                    return ()
                rate = sum(result.passed for result in probes) / len(probes)
                return (
                    EvalMetric(name="probe_pass_rate", value=rate),
                    *_rate_metrics(
                        results,
                        (
                            "weighted_recall",
                            "stale_rate",
                            "correction_recall",
                            "weighted_reference_coverage",
                        ),
                    ),
                )
            case _:
                return ()


def _unreachable_trigger(
    case: RolloverCase, lab: RolloverTarget, window_tokens: int
) -> list[EvalCaseResult]:
    """A live leaf whose window cannot reach the probe model's rollover line is a mis-sized run,
    not a model failure. The snapshot is built against `DEFAULT_CONTEXT_WINDOW_TOKENS`; a target
    agent on a longer-window model moves the line out of reach and every probe would otherwise
    report a rollover that never fired. Refuse before a turn is spent, and name both numbers."""
    reason = (
        f"snapshot window is {window_tokens:,} estimated tokens but {lab.serving.model} declares a "
        f"{lab.context_window:,}-token context window, so the rollover line sits at "
        f"{lab.live_trigger_tokens:,} — {lab.live_trigger_tokens - window_tokens:,} beyond the "
        f"window. Point --agent at an agent whose model declares a "
        f"{DEFAULT_CONTEXT_WINDOW_TOKENS:,}-token window"
    )
    return [
        EvalCaseResult(
            name=f"{case.id}.{probe.id}",
            passed=False,
            reason=reason,
            evidence={"question": probe.question, "grading": _probe_grading(probe)},
        )
        for probe in case.probes
    ]


def _probe_grading(probe: RolloverProbe) -> str:
    parts = [f"after the rollover, the probe answer carries {', '.join(probe.expect_literals)}"]
    if probe.forbid_literals:
        parts.append(f"omits the superseded {', '.join(probe.forbid_literals)}")
    if probe.expect_read_path:
        parts.append(f"and the trajectory re-reads {probe.expect_read_path}")
    return "; ".join(parts)


def _turn_attempt(
    outcome: TargetResult,
    passed: bool,
    reason: str,
    grader: JsonObject | None,
    wall_ms: int | None = None,
) -> Json:
    """One probe turn in the viewer's attempt shape: the answer, its tool calls, the deterministic
    recovery-fidelity grader evidence, the wall clock the probe turn took, and the stored transcript
    the trajectory link opens."""
    return {
        "passed": passed,
        "reason": reason,
        "wallMs": wall_ms,
        "response": outcome.output.response,
        "calls": [
            {
                "name": call.name,
                "input": call.input,
                "result": call.result,
                "hasResult": call.has_result,
                "isError": call.is_error,
            }
            for call in outcome.output.calls
        ],
        "toolErrors": list(outcome.output.tool_errors),
        "tokens": outcome.output.tokens,
        "costMicroUsd": outcome.output.cost_micro_usd,
        "rollovers": outcome.output.rollovers,
        "grader": grader,
        "trajectory": (
            None if outcome.trajectory is None else outcome.trajectory.model_dump(mode="json")
        ),
    }


async def _timed_probe(
    case: RolloverCase, probe: RolloverProbe, target: RolloverRunTarget, conversation_id: UUID
) -> EvalCaseResult:
    """One probe turn, graded, with the wall clock it took — the number a cheaper recovery path
    has to beat."""
    started = perf_counter()
    outcome = await target.step(
        conversation_id, probe.question, f"{case.id}:{conversation_id}:{probe.id}"
    )
    return _grade_probe(case, probe, outcome, round((perf_counter() - started) * 1_000))


def _grade_probe(
    case: RolloverCase, probe: RolloverProbe, outcome: TargetResult, wall_ms: int
) -> EvalCaseResult:
    name = f"{case.id}.{probe.id}"
    base: JsonObject = {"question": probe.question, "grading": _probe_grading(probe)}
    if not outcome.clean:
        return EvalCaseResult(
            name=name,
            passed=False,
            reason=outcome.failure_reason,
            evidence={
                **base,
                "attempts": [_turn_attempt(outcome, False, outcome.failure_reason, None, wall_ms)],
                "selectedAttempt": 0,
            },
        )
    answer = outcome.output.response
    missing = [literal for literal in probe.expect_literals if literal not in answer]
    forbidden = [literal for literal in probe.forbid_literals if literal in answer]
    read_touched = not probe.expect_read_path or any(
        probe.expect_read_path in json.dumps(call.input, sort_keys=True)
        for call in outcome.output.calls
    )
    if missing:
        passed, reason = False, f"answer omits the planted value(s): {', '.join(missing)}"
    elif forbidden:
        passed, reason = False, f"answer repeats the superseded value(s): {', '.join(forbidden)}"
    elif not read_touched:
        passed, reason = False, f"trajectory never re-read {probe.expect_read_path}"
    else:
        passed, reason = True, "answer carries the surviving value"
    grader: JsonObject = {
        "missingLiterals": cast(list[Json], list(missing)),
        "forbiddenLiterals": cast(list[Json], list(forbidden)),
        "readPathTouched": read_touched,
    }
    return EvalCaseResult(
        name=name,
        passed=passed,
        reason=reason,
        evidence={
            **base,
            "attempts": [_turn_attempt(outcome, passed, reason, grader, wall_ms)],
            "selectedAttempt": 0,
        },
    )


async def _grade_boundary(
    case: RolloverCase,
    lab: RolloverTarget,
    conversation_id: UUID,
    recovery: RecoveryRecord,
    rendered: str,
    generation: int,
    before_tokens: int,
    after_tokens: int,
) -> GenerationGrade:
    """Grade one boundary against what a recovering agent can actually reach: the recovery record
    it opens with, the history lines that record names, and the whole history file a grep walks.
    The file is the one the boundary appended to, so a fact graded recoverable is one the file
    tools return."""
    journal = lab.journal(conversation_id)
    addressed_text = await journal.text(recovery.first_entry_id, recovery.last_entry_id)
    journal_text = await journal.text(1, await journal.lines())
    stated = _without_history_line(rendered)
    asserted = stated
    for heading in (OBJECTIVE_HEADING, USER_INPUTS_HEADING, PENDING_HEADING):
        asserted = asserted.replace(_section(rendered, heading), "")
    return GenerationGrade(
        generation=generation,
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        carried=frozenset(fact.id for fact in case.facts if fact.literal in stated),
        journaled=frozenset(fact.id for fact in case.facts if fact.literal in addressed_text),
        searchable=frozenset(
            fact.id for fact in case.facts if fact.literal in stated or fact.literal in journal_text
        ),
        stale_carried=frozenset(
            fact.id for fact in case.facts if fact.stale_literal and fact.stale_literal in asserted
        ),
        addressed=frozenset(
            fact.id
            for fact in case.facts
            if fact.kind == "reference" and fact.path in addressed_text
        ),
    )


def _without_history_line(rendered: str) -> str:
    """The history line names a path and a line range. Digits inside a conversation id or a line
    count are not the record stating a value, so the line is not part of what it asserts."""
    prefix = HISTORY_LINE.split("{", 1)[0]
    return "\n".join(line for line in rendered.splitlines() if not line.startswith(prefix))


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
    return sum(fact.weight for fact in references if fact.id in final.addressed) / total


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
        "record_carry_rate": "recordCarryRate",
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
