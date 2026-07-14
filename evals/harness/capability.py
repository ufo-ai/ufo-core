"""One capability case: a message the live agent answers, a deterministic grader over its answer
and tool trajectory, and an optional semantic rubric. Deterministic checks run first; a passing
rubric case then reaches the target's model judge. A case runs against a `Target` that drives a real
turn and reconstructs the answer + tool calls from the durable transcript. Transcript tool results
retain text, completion, and error state; the agent's configured tool set remains fixed per run."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from evals.harness.harness import EvalCaseResult, Json, JsonObject, infra_error
from evals.harness.judge import JUDGE_REVISION, rubric_pass

if TYPE_CHECKING:
    from evals.harness.target import CapabilityTarget


@dataclass(frozen=True)
class CapabilityVerdict:
    passed: bool
    reason: str


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


@dataclass(frozen=True)
class CapabilityOutput:
    """What a grader sees: the agent's final answer, the ordered tool invocations its turn made, and
    the text of any tool call that errored."""

    response: str
    calls: tuple[ToolInvocation, ...]
    tool_errors: tuple[str, ...] = ()
    artifacts: tuple[SharedArtifact, ...] = ()
    artifact_error: str = ""

    @property
    def tools(self) -> tuple[str, ...]:
        return tuple(call.name for call in self.calls)


type Grader = Callable[[CapabilityOutput], Awaitable[CapabilityVerdict]]


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
    winning_indexes = [index for index, sample in enumerate(samples) if sample[1].passed]
    selected_index = winning_indexes[0] if winning_indexes else len(samples) - 1
    _, verdict = samples[selected_index]
    attempts: list[Json] = []
    for sample_output, sample_verdict in samples:
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
            }
        )
    evidence: JsonObject = {
        "message": case.message,
        "rubric": list(case.rubric),
        "selectedAttempt": selected_index,
        "attempts": attempts,
    }
    if not winning_indexes and case.web_dependent:
        broke = infra_error(tuple(error for sample, _ in samples for error in sample.tool_errors))
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


async def sample_capability(
    case: CapabilityCase, target: CapabilityTarget
) -> tuple[CapabilityOutput, CapabilityVerdict]:
    result = await target.run(case)
    if not result.clean:
        return result.output, CapabilityVerdict(False, result.failure_reason)
    deterministic = await case.grader(result.output)
    if not deterministic.passed or not case.rubric:
        return result.output, deterministic
    if target.judge is None:
        return result.output, CapabilityVerdict(False, "semantic rubric requires a model judge")
    passed, reason = await rubric_pass(
        case.message, result.output.response, case.rubric, target.judge
    )
    return result.output, CapabilityVerdict(passed, f"{deterministic.reason}; {reason}")
