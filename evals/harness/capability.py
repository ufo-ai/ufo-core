"""One capability case: a message the live agent answers, a deterministic grader over its answer
and tool trajectory, and an optional semantic rubric. Deterministic checks run first; a passing
rubric case then reaches the target's model judge. A case runs against a `Target` that drives a real
turn and reconstructs the answer + tool calls from the durable transcript. Transcript tool results
retain text, completion, and error state; the agent's configured tool set remains fixed per run."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from evals.harness.harness import EvalCaseResult, Json, JsonObject, infra_error
from evals.harness.judge import JUDGE_REVISION, rubric_pass
from ufo.schema.records import TurnStatus
from ufo.sdk.models import Message

if TYPE_CHECKING:
    from evals.harness.target import CapabilityTarget


@dataclass(frozen=True)
class CapabilityVerdict:
    passed: bool
    reason: str
    evidence: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class ToolInvocation:
    """One tool call and the completion state reconstructed from its transcript result."""

    name: str
    input: JsonObject
    result: str = ""
    has_result: bool = False
    is_error: bool = False

    @property
    def succeeded(self) -> bool:
        return self.has_result and not self.is_error


@dataclass(frozen=True)
class SharedArtifact:
    """One artifact durably attached to the evaluated turn."""

    name: str
    content: bytes


class TurnLog(BaseModel):
    """One allowlisted structured log exported by an evaluated turn."""

    event: str = Field(min_length=1)
    turn_id: UUID
    attributes: JsonObject


class EvalTrajectory(BaseModel):
    model_config = ConfigDict(frozen=True)

    conversation_id: UUID
    turn_id: UUID | None
    status: TurnStatus | None
    messages: tuple[Message, ...]
    error: str = ""


@dataclass(frozen=True)
class CapabilityOutput:
    """The answer, tool trajectory, artifacts, and allowlisted log visible to a grader."""

    response: str
    calls: tuple[ToolInvocation, ...]
    tool_errors: tuple[str, ...] = ()
    artifacts: tuple[SharedArtifact, ...] = ()
    artifact_error: str = ""
    log: TurnLog | None = None
    tokens: int = 0
    cost_micro_usd: int = 0

    @property
    def tools(self) -> tuple[str, ...]:
        return tuple(call.name for call in self.calls)


type Grader = Callable[[CapabilityOutput], Awaitable[CapabilityVerdict]]


@dataclass(frozen=True)
class CapabilitySample:
    output: CapabilityOutput
    verdict: CapabilityVerdict
    trajectory: EvalTrajectory | None


@dataclass(frozen=True)
class CapabilityCase:
    """A message and its deterministic and semantic criteria. `web_dependent` infra-excludes an
    external outage; `samples` re-runs the case and passes if any sample passes; `digest_tag`
    stabilizes the suite digest. `member_key`, when set, is the exact email of the workspace member
    whose private memory the eval conversation may recall."""

    name: str
    message: str
    grader: Grader
    samples: int = 1
    web_dependent: bool = False
    digest_tag: str = ""
    rubric: tuple[str, ...] = ()
    member_key: str | None = None

    def payload(self) -> JsonObject:
        payload: JsonObject = {
            "name": self.name,
            "message": self.message,
            "samples": self.samples,
            "webDependent": self.web_dependent,
            "grader": self.digest_tag or self.name,
            "rubric": list(self.rubric),
        }
        if self.rubric:
            payload["judgeRevision"] = JUDGE_REVISION
        if self.member_key is not None:
            payload["memberKey"] = self.member_key
        return payload


async def run_capability_case(case: CapabilityCase, target: CapabilityTarget) -> EvalCaseResult:
    samples = [await sample_capability(case, target) for _ in range(max(case.samples, 1))]
    winning_indexes = [index for index, sample in enumerate(samples) if sample.verdict.passed]
    selected_index = winning_indexes[0] if winning_indexes else len(samples) - 1
    verdict = samples[selected_index].verdict
    attempts: list[Json] = []
    for sample in samples:
        sample_output = sample.output
        sample_verdict = sample.verdict
        calls: list[Json] = []
        for call in sample_output.calls:
            calls.append(
                {
                    "name": call.name,
                    "input": call.input,
                    "result": call.result,
                    "hasResult": call.has_result,
                    "isError": call.is_error,
                }
            )
        attempts.append(
            {
                "passed": sample_verdict.passed,
                "reason": sample_verdict.reason,
                "response": sample_output.response,
                "calls": calls,
                "toolErrors": list(sample_output.tool_errors),
                "artifacts": [artifact.name for artifact in sample_output.artifacts],
                "artifactError": sample_output.artifact_error or None,
                "tokens": sample_output.tokens,
                "costMicroUsd": sample_output.cost_micro_usd,
                "log": (
                    None if sample_output.log is None else sample_output.log.model_dump(mode="json")
                ),
                "grader": sample_verdict.evidence or None,
                "trajectory": (
                    None if sample.trajectory is None else sample.trajectory.model_dump(mode="json")
                ),
            }
        )
    evidence: JsonObject = {
        "message": case.message,
        "rubric": list(case.rubric),
        "selectedAttempt": selected_index,
        "attempts": attempts,
    }
    if not winning_indexes and case.web_dependent:
        broke = infra_error(
            tuple(error for sample in samples for error in sample.output.tool_errors)
        )
        if broke:
            return EvalCaseResult(
                name=case.name,
                passed=False,
                reason=f"infra-excluded (web unavailable): {broke[:120]}",
                evidence=evidence,
                excluded=True,
            )
    passed = bool(winning_indexes)
    reason = (
        verdict.reason
        if passed
        else f"{len(winning_indexes)}/{len(samples)} samples passed: {verdict.reason}"
    )
    return EvalCaseResult(name=case.name, passed=passed, reason=reason, evidence=evidence)


async def sample_capability(case: CapabilityCase, target: CapabilityTarget) -> CapabilitySample:
    result = await target.run(case)
    if not result.clean:
        return CapabilitySample(
            result.output, CapabilityVerdict(False, result.failure_reason), result.trajectory
        )
    deterministic = await case.grader(result.output)
    if not deterministic.passed or not case.rubric:
        return CapabilitySample(result.output, deterministic, result.trajectory)
    if target.judge is None:
        return CapabilitySample(
            result.output,
            CapabilityVerdict(False, "semantic rubric requires a model judge"),
            result.trajectory,
        )
    passed, reason = await rubric_pass(
        case.message, result.output.response, case.rubric, target.judge
    )
    return CapabilitySample(
        result.output,
        CapabilityVerdict(passed, f"{deterministic.reason}; {reason}", deterministic.evidence),
        result.trajectory,
    )
