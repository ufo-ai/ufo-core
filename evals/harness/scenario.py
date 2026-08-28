"""One scenario case: a simulated member holds a multi-turn conversation with the live agent on a
single eval conversation, and a grader scores the finished conversation — every reply, the full
tool trajectory, and whatever durable state the turns left behind. The simulator is an LLM leg
prompted tau2-bench style: it knows only its scenario, reveals details when asked rather than up
front, and ends the conversation with an in-band stop token once its goal is met."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from evals.harness.capability import (
    ArtifactProbe,
    CapabilityOutput,
    CapabilityVerdict,
    EvalSeed,
    grading_statement,
    linked_artifacts,
    merge_tool_calls,
    source_digest,
)
from evals.harness.handoff import SubagentHandoff
from evals.harness.harness import (
    EvalCaseResult,
    Json,
    JsonObject,
    infra_owned_fault,
    is_transient_fault,
)
from evals.harness.judge import JUDGE_REVISION, CriterionVerdict, JudgeLeg, rubric_pass
from evals.harness.target import CapabilityTarget, TargetResult
from evals.harness.timing import CaseTiming, case_timing
from ufo.sdk.models import Message
from ufo.workspace import ws_current

STOP_TOKEN = "###STOP###"
OPENING_NUDGE = "[The conversation is starting. Send your first message.]"
MAX_SIMULATOR_REPLY_CHARS = 6_000
SIMULATOR_REVISION = "2026-07-15-progressive-disclosure"
SIMULATOR_SYSTEM = f"""You are role-playing one specific human member of a workspace chatting \
with their assistant. Follow the scenario exactly:
- Send one short, natural chat message per turn.
- Pursue only the scenario's goal. Anything the scenario does not tell you is something you do \
not know — never invent facts, names, or extra requests.
- Reveal details progressively: answer what the assistant asks; do not volunteer everything at \
once.
- Stay in character; never mention being simulated and never repeat the scenario text verbatim.
- When your goal is fully satisfied, or the scenario tells you to give up, reply with exactly \
{STOP_TOKEN} and nothing else."""


@dataclass(frozen=True)
class ScenarioUser:
    """What the simulated member knows and wants — tau2-bench's structured user instructions."""

    reason_for_call: str
    known_info: str = ""
    unknown_info: str = ""
    task_instructions: str = ""
    persona: str = ""

    def scenario_block(self) -> str:
        lines = [f"Why you are contacting the assistant: {self.reason_for_call}"]
        if self.known_info:
            lines.append(f"What you know: {self.known_info}")
        if self.unknown_info:
            lines.append(f"What you do not know: {self.unknown_info}")
        if self.task_instructions:
            lines.append(f"How to behave: {self.task_instructions}")
        if self.persona:
            lines.append(f"Who you are: {self.persona}")
        return "<scenario>\n" + "\n".join(lines) + "\n</scenario>"

    def payload(self) -> JsonObject:
        return {
            "reasonForCall": self.reason_for_call,
            "knownInfo": self.known_info,
            "unknownInfo": self.unknown_info,
            "taskInstructions": self.task_instructions,
            "persona": self.persona,
        }


@dataclass(frozen=True)
class ScenarioTurn:
    user_message: str
    reply: str


@dataclass(frozen=True)
class ScenarioOutcome:
    """The finished conversation a grader scores: each exchange, the output reconstructed from the
    full durable transcript (every turn's tool calls, the final reply), and whether the simulator
    ended satisfied rather than hitting the turn cap."""

    turns: tuple[ScenarioTurn, ...]
    output: CapabilityOutput
    stopped: bool
    followups: tuple[CapabilityOutput, ...] = ()

    @property
    def replies(self) -> tuple[str, ...]:
        return tuple(turn.reply for turn in self.turns)

    @property
    def followup(self) -> CapabilityOutput | None:
        return self.followups[-1] if self.followups else None


type ScenarioGrader = Callable[[ScenarioOutcome], Awaitable[CapabilityVerdict]]
type ScenarioFollowup = Callable[
    [ScenarioOutcome, CapabilityTarget],
    Awaitable[TargetResult | tuple[TargetResult, ...]],
]


@dataclass(frozen=True)
class ScenarioCase:
    """`max_turns` caps the member's messages; hitting the cap is not itself a failure — the
    grader decides what a finished conversation must show. `member_key`, when set, is the exact
    email of the workspace member the simulator speaks as. `seed`, when set, receives
    (workspace_id, agent_id) before each trial's conversation opens and establishes the trial's
    starting state — resetting whatever it owns, so no trial inherits another's rows. The payload
    hashes the seed's qualified name plus its defining module's source, so editing a fixture — or
    a helper the fixtures share — moves the suite digest by itself. `trials` reruns the whole
    conversation independently; the case passes only if every trial passes (tau2-bench's pass^k
    consistency bar, with k = trials)."""

    name: str
    user: ScenarioUser
    grader: ScenarioGrader
    max_turns: int = 8
    member_key: str | None = None
    digest_tag: str = ""
    seed: EvalSeed | None = None
    trials: int = 1
    tier: int = 1
    rubric: tuple[str, ...] = ()
    followup: ScenarioFollowup | None = None
    artifact_probe: ArtifactProbe | None = None

    def __post_init__(self) -> None:
        if self.trials < 1:
            raise ValueError(f"case {self.name!r} needs at least one trial, got {self.trials}")
        if self.tier < 1:
            raise ValueError(f"case {self.name!r} tier must be at least 1, got {self.tier}")

    def payload(self) -> JsonObject:
        payload: JsonObject = {
            "name": self.name,
            "user": self.user.payload(),
            "maxTurns": self.max_turns,
            "grader": self.digest_tag or self.name,
            "seed": None if self.seed is None else source_digest(self.seed),
            "trials": self.trials,
            "tier": self.tier,
            "simulatorRevision": SIMULATOR_REVISION,
            "rubric": list(self.rubric),
        }
        if self.rubric:
            payload["judgeRevision"] = JUDGE_REVISION
        if self.followup is not None:
            payload["followup"] = source_digest(self.followup)
        if self.artifact_probe is not None:
            payload["artifactProbe"] = source_digest(self.artifact_probe)
        if self.member_key is not None:
            payload["memberKey"] = self.member_key
        return payload


@dataclass(frozen=True)
class UserSimulator:
    """The scenario's member: an LLM leg whose history flips roles — its own messages are the
    assistant side of its view, the agent's replies the user side — so completing the chat yields
    the member's next message."""

    leg: JudgeLeg
    user: ScenarioUser

    async def next_message(self, turns: tuple[ScenarioTurn, ...]) -> str:
        history: list[Message] = [Message(role="user", content=OPENING_NUDGE)]
        for turn in turns:
            history.append(Message(role="assistant", content=turn.user_message))
            history.append(Message(role="user", content=_bounded(turn.reply)))
        system = f"{SIMULATOR_SYSTEM}\n\n{self.user.scenario_block()}"
        return (await self.leg.complete(system, tuple(history))).strip()


def _bounded(reply: str) -> str:
    if len(reply) <= MAX_SIMULATOR_REPLY_CHARS:
        return reply
    return reply[:MAX_SIMULATOR_REPLY_CHARS] + "\n[reply truncated for the simulator]"


def _scenario_evidence(
    result: TargetResult,
    timings: tuple[CaseTiming, ...],
    handoffs: tuple[SubagentHandoff, ...],
) -> TargetResult:
    errors = tuple(dict.fromkeys(timing.error for timing in timings if timing.error))
    timing = (
        None
        if not timings
        else case_timing(
            sum(item.wall_ms for item in timings),
            tuple(turn for item in timings for turn in item.turns),
            "; ".join(errors),
        )
    )
    return replace(result, output=replace(result.output, timing=timing, handoffs=handoffs))


@dataclass(frozen=True)
class _Trial:
    """One independent run of the case's conversation and its verdict. `infra` marks a trial whose
    turn crashed on a transient provider fault — excluded from scoring, not counted as a failure."""

    turns: tuple[ScenarioTurn, ...]
    stopped: bool
    last: TargetResult | None
    passed: bool
    reason: str
    tokens: int = 0
    cost_micro_usd: int = 0
    grader_evidence: JsonObject | None = None
    infra: bool = False
    judge: tuple[CriterionVerdict, ...] = ()
    followups: tuple[TargetResult, ...] = ()


async def run_scenario_case(case: ScenarioCase, target: CapabilityTarget) -> EvalCaseResult:
    if target.simulator is None:
        raise RuntimeError("a scenario case requires a model leg to simulate its member")
    run = _ScenarioRun(case, target, UserSimulator(target.simulator, case.user))
    return await run.result()


@dataclass(frozen=True)
class _ScenarioRun:
    """One case's execution against a target: every trial in order — each a freshly seeded
    conversation the simulator drives to its stop — folded into one all-trials-must-pass
    result."""

    case: ScenarioCase
    target: CapabilityTarget
    simulator: UserSimulator

    async def result(self) -> EvalCaseResult:
        trials = [await self._trial(index) for index in range(self.case.trials)]
        scored = [trial for trial in trials if not trial.infra]
        excluded = len(trials) - len(scored)
        first_failure = next((trial for trial in scored if not trial.passed), None)
        selected = trials.index(first_failure) if first_failure is not None else 0
        evidence: JsonObject = {
            "user": self.case.user.payload(),
            "grading": grading_statement(self.case.grader) or None,
            "memberKey": self.case.member_key,
            "maxTurns": self.case.max_turns,
            "selectedAttempt": selected,
            "excludedTrials": excluded,
            "attempts": [self._attempt(trial) for trial in trials],
        }
        if not scored:
            return EvalCaseResult(
                name=self.case.name,
                passed=False,
                reason=f"all {len(trials)} trial(s) infra-excluded (transient model faults)",
                evidence=evidence,
                excluded=True,
                tier=self.case.tier,
            )
        passes = sum(1 for trial in scored if trial.passed)
        passed = passes == len(scored)
        note = f" ({excluded} infra-excluded)" if excluded else ""
        if len(scored) == 1 and not excluded:
            reason = scored[0].reason
        elif first_failure is None:
            reason = f"{passes}/{len(scored)} scored trials passed{note}"
        else:
            reason = (
                f"{passes}/{len(scored)} scored trials passed{note}; "
                f"first failure: {first_failure.reason}"
            )
        return EvalCaseResult(
            name=self.case.name,
            passed=passed,
            reason=reason,
            evidence=evidence,
            tier=self.case.tier,
        )

    async def _trial(self, trial: int) -> _Trial:
        case = self.case
        if case.seed is not None:
            await case.seed(ws_current().workspace_id, self.target.agent_id)
        conversation_id = await self.target.conversations.open(
            f"{case.name}:{trial}", case.member_key
        )
        turns: list[ScenarioTurn] = []
        last: TargetResult | None = None
        stopped = False
        tokens = 0
        cost_micro_usd = 0
        timings: list[CaseTiming] = []
        handoffs: list[SubagentHandoff] = []
        for index in range(case.max_turns):
            try:
                message = await self.simulator.next_message(tuple(turns))
            except Exception as error:
                reason = f"simulator model call failed: {type(error).__name__}: {error}"
                if not is_transient_fault(type(error).__name__):
                    raise
                return _Trial(
                    tuple(turns), stopped, last, False, reason, tokens, cost_micro_usd, infra=True
                )
            if STOP_TOKEN in message:
                stopped = True
                break
            if not message:
                return _Trial(
                    tuple(turns),
                    stopped,
                    last,
                    False,
                    "simulator sent an empty message",
                    tokens,
                    cost_micro_usd,
                )
            result = await self.target.step(
                conversation_id, message, f"{case.name}:{conversation_id}:{index}"
            )
            turns.append(ScenarioTurn(message, result.output.response))
            if result.output.timing is not None:
                timings.append(result.output.timing)
            handoffs.extend(result.output.handoffs)
            result = _scenario_evidence(result, tuple(timings), tuple(handoffs))
            last = result
            tokens += result.output.tokens
            cost_micro_usd += result.output.cost_micro_usd
            if not result.clean:
                return _Trial(
                    tuple(turns),
                    stopped,
                    last,
                    False,
                    result.failure_reason,
                    tokens,
                    cost_micro_usd,
                    infra=infra_owned_fault(
                        result.error_class,
                        result.failure_reason,
                        result.trajectory.status if result.trajectory is not None else None,
                    ),
                )
        if last is None:
            return _Trial(
                tuple(turns),
                stopped,
                last,
                False,
                "simulator ended the conversation before it began",
                tokens,
                cost_micro_usd,
            )
        outcome = ScenarioOutcome(tuple(turns), last.output, stopped)
        followups: tuple[TargetResult, ...] = ()
        if case.followup is not None:
            try:
                returned = await case.followup(outcome, self.target)
            except Exception as error:
                reason = f"followup raised: {type(error).__name__}: {error}"
                return _Trial(
                    tuple(turns),
                    stopped,
                    last,
                    False,
                    reason,
                    tokens,
                    cost_micro_usd,
                    infra=infra_owned_fault(type(error).__name__, reason, None),
                )
            followups = returned if isinstance(returned, tuple) else (returned,)
            for index, followup in enumerate(followups):
                tokens += followup.output.tokens
                cost_micro_usd += followup.output.cost_micro_usd
                if followup.output.timing is not None:
                    timings.append(followup.output.timing)
                handoffs.extend(followup.output.handoffs)
                calls = merge_tool_calls(last.output.calls, followup.output.calls)
                own_calls = merge_tool_calls(last.output.own_calls, followup.output.own_calls)
                merged = replace(
                    last.output,
                    calls=calls,
                    own_tools=tuple(call.call for call in own_calls),
                    own_calls=own_calls,
                    tool_errors=(*last.output.tool_errors, *followup.output.tool_errors),
                    tokens=tokens,
                    cost_micro_usd=cost_micro_usd,
                )
                last = replace(
                    last,
                    output=_scenario_evidence(
                        replace(last, output=merged), tuple(timings), tuple(handoffs)
                    ).output,
                )
                if not followup.clean:
                    last = await self._capture_artifacts(last, followup)
                    return _Trial(
                        tuple(turns),
                        stopped,
                        last,
                        False,
                        followup.failure_reason,
                        tokens,
                        cost_micro_usd,
                        infra=infra_owned_fault(
                            followup.error_class,
                            followup.failure_reason,
                            (
                                followup.trajectory.status
                                if followup.trajectory is not None
                                else None
                            ),
                        ),
                        followups=followups[: index + 1],
                    )
            last = await self._capture_artifacts(last, followups[-1])
            outcome = ScenarioOutcome(
                tuple(turns),
                last.output,
                stopped,
                tuple(item.output for item in followups),
            )
        verdict = await case.grader(outcome)
        judged: tuple[CriterionVerdict, ...] = ()
        if verdict.passed and case.rubric:
            if self.target.judge is None:
                verdict = CapabilityVerdict(False, "semantic rubric requires a model judge")
            else:
                transcript = "\n".join(
                    f"member: {turn.user_message}\nassistant: {turn.reply}" for turn in turns
                )
                rubric = await rubric_pass(
                    case.user.reason_for_call, transcript, case.rubric, self.target.judge
                )
                judged = rubric.criteria
                verdict = CapabilityVerdict(
                    rubric.passed, f"{verdict.reason}; {rubric.reason}", verdict.evidence
                )
        return _Trial(
            tuple(turns),
            stopped,
            last,
            verdict.passed,
            verdict.reason,
            tokens,
            cost_micro_usd,
            verdict.evidence,
            judge=judged,
            followups=followups,
        )

    async def _capture_artifacts(self, result: TargetResult, source: TargetResult) -> TargetResult:
        if self.case.artifact_probe is None:
            return result
        trajectory = source.trajectory
        if trajectory is None:
            return replace(
                result,
                output=replace(
                    result.output,
                    artifact_error="application artifact capture has no followup trajectory",
                ),
            )
        output = replace(
            result.output,
            workspace_dir=self.target.conversations.workspace_path(trajectory.conversation_id, ""),
        )
        captured = await self.target.capture_artifacts(
            trajectory.conversation_id, output, self.case.artifact_probe
        )
        return replace(result, output=captured)

    def _attempt(self, trial: _Trial) -> Json:
        output = trial.last.output if trial.last is not None else CapabilityOutput("", ())
        calls: list[Json] = [
            {
                "name": call.name,
                "input": call.input,
                "result": call.result,
                "hasResult": call.has_result,
                "isError": call.is_error,
            }
            for call in output.calls
        ]
        trajectory = trial.last.trajectory if trial.last is not None else None
        return {
            "passed": trial.passed,
            "reason": trial.reason,
            "response": output.response,
            "calls": calls,
            "toolErrors": list(output.tool_errors),
            "artifacts": [artifact.name for artifact in output.artifacts],
            "artifactContents": linked_artifacts(output.artifacts, output.artifact_references),
            "artifactReferences": [
                {
                    "name": artifact.name,
                    "blobKey": artifact.blob_key,
                    "digest": artifact.digest,
                    "sizeBytes": artifact.size_bytes,
                }
                for artifact in output.artifact_references
            ],
            "artifactError": output.artifact_error or None,
            "turns": [
                {"userMessage": turn.user_message, "reply": turn.reply} for turn in trial.turns
            ],
            "stopped": trial.stopped,
            "infra": trial.infra,
            "tokens": trial.tokens,
            "costMicroUsd": trial.cost_micro_usd,
            "timing": None if output.timing is None else output.timing.model_dump(mode="json"),
            "handoffs": [handoff.model_dump(mode="json") for handoff in output.handoffs],
            "grader": trial.grader_evidence or None,
            "judge": (
                [
                    {"criterion": item.criterion, "passed": item.passed, "reason": item.reason}
                    for item in trial.judge
                ]
                if trial.judge
                else None
            ),
            "trajectory": None if trajectory is None else trajectory.model_dump(mode="json"),
            "followupTrajectory": (
                None
                if not trial.followups or trial.followups[-1].trajectory is None
                else trial.followups[-1].trajectory.model_dump(mode="json")
            ),
            "followupTrajectories": [
                None if followup.trajectory is None else followup.trajectory.model_dump(mode="json")
                for followup in trial.followups
            ],
        }
