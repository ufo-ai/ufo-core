"""One capability case: a message the live agent answers, a deterministic grader over its answer
and tool trajectory, and an optional semantic rubric. Deterministic checks run first; a passing
rubric case then reaches the target's model judge. A case runs against a `Target` that drives a real
turn and reconstructs the answer + tool calls from the durable transcript. Transcript tool results
retain text, completion, and error state; the agent's configured tool set remains fixed per run."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from evals.harness.harness import EvalCaseResult, JsonObject, infra_error
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
    stabilizes the suite digest."""

    name: str
    message: str
    grader: Grader
    samples: int = 1
    web_dependent: bool = False
    digest_tag: str = ""
    rubric: tuple[str, ...] = ()

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
        return payload


async def run_capability_case(case: CapabilityCase, target: CapabilityTarget) -> EvalCaseResult:
    samples = [await sample_capability(case, target) for _ in range(max(case.samples, 1))]
    won = [sample for sample in samples if sample[1].passed]
    output, verdict = won[0] if won else samples[-1]
    if not won and case.web_dependent:
        broke = infra_error(tuple(error for sample, _ in samples for error in sample.tool_errors))
        if broke:
            return EvalCaseResult(
                case.name,
                False,
                f"infra-excluded (web unavailable): {broke[:120]}",
                {"response": output.response, "tools": list(output.tools), "infraExcluded": True},
                excluded=True,
            )
    passed = bool(won)
    reason = (
        verdict.reason if passed else f"{len(won)}/{len(samples)} samples passed: {verdict.reason}"
    )
    return EvalCaseResult(
        case.name,
        passed,
        reason,
        {
            "response": output.response,
            "tools": list(output.tools),
            "artifacts": [artifact.name for artifact in output.artifacts],
            "artifactError": output.artifact_error or None,
            "samples": len(samples),
            "samplesPassed": len(won),
        },
    )


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
