"""One scenario case: a simulated member holds a multi-turn conversation with the live agent on a
single eval conversation, and a grader scores the finished conversation — every reply, the full
tool trajectory, and whatever durable state the turns left behind. The simulator is an LLM leg
prompted tau2-bench style: it knows only its scenario, reveals details when asked rather than up
front, and ends the conversation with an in-band stop token once its goal is met."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from hashlib import sha256
from inspect import getmodule, getsource
from uuid import UUID

from evals.harness.capability import CapabilityOutput, CapabilityVerdict, grading_statement
from evals.harness.harness import EvalCaseResult, Json, JsonObject
from evals.harness.judge import JUDGE_REVISION, CriterionVerdict, JudgeLeg, rubric_pass
from evals.harness.target import CapabilityTarget, TargetResult
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

    @property
    def replies(self) -> tuple[str, ...]:
        return tuple(turn.reply for turn in self.turns)


type ScenarioGrader = Callable[[ScenarioOutcome], Awaitable[CapabilityVerdict]]
type ScenarioSeed = Callable[[UUID, UUID], Awaitable[None]]


def _seed_digest(seed: ScenarioSeed) -> str:
    module = getmodule(seed)
    if module is None:
        raise RuntimeError(f"seed {seed!r} has no source module to digest")
    return sha256(f"{getsource(seed)}\n{getsource(module)}".encode()).hexdigest()


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
    seed: ScenarioSeed | None = None
    trials: int = 1
    tier: int = 1
    rubric: tuple[str, ...] = ()

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
            "seed": None if self.seed is None else _seed_digest(self.seed),
            "trials": self.trials,
            "tier": self.tier,
            "simulatorRevision": SIMULATOR_REVISION,
            "rubric": list(self.rubric),
        }
        if self.rubric:
            payload["judgeRevision"] = JUDGE_REVISION
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


TRANSIENT_ERROR_CLASSES = frozenset(
    {
        "RateLimitError",
        "InternalServerError",
        "ServiceUnavailableError",
        "OverloadedError",
        "DeadlineExceededError",
        "APIConnectionError",
        "APITimeoutError",
        "ReadTimeout",
        "ConnectTimeout",
        "PoolTimeout",
        "WriteTimeout",
        "ReadError",
        "ConnectError",
        "WriteError",
        "RemoteProtocolError",
        "ProxyError",
    }
)


def _is_transient(error_class: str | None) -> bool:
    """A trial whose turn crashed on a model or transport fault the provider owns — a read/connect
    timeout, an overload, a 5xx — carried on the terminal's `error_class` (or the class of a
    simulator-leg model call that raised). These are external uncertainty, not a capability signal,
    so the trial is excluded from pass^k rather than counted as a failure (mirroring the capability
    harness's `web_dependent` infra exclusion). Matched by exact class name against the anthropic
    SDK / httpx transient set, never a substring: the terminal `error_class` also carries the class
    of an internal fault (a DB or DBOS wedge the backstop commits as `type(error).__name__`), and a
    builtin `TimeoutError` or `ConnectionError` there is an internal wedge that must surface as a
    failure, never be masked as external."""
    return error_class in TRANSIENT_ERROR_CLASSES


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
        for index in range(case.max_turns):
            try:
                message = await self.simulator.next_message(tuple(turns))
            except Exception as error:
                reason = f"simulator model call failed: {type(error).__name__}: {error}"
                if not _is_transient(type(error).__name__):
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
                    infra=_is_transient(result.error_class),
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
        verdict = await case.grader(ScenarioOutcome(tuple(turns), last.output, stopped))
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
        )

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
            "turns": [
                {"userMessage": turn.user_message, "reply": turn.reply} for turn in trial.turns
            ],
            "stopped": trial.stopped,
            "infra": trial.infra,
            "tokens": trial.tokens,
            "costMicroUsd": trial.cost_micro_usd,
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
        }
