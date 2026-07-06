"""One capability case: a message the live agent answers, and a grader over its final answer and
the tool trajectory it took. The grader FAILS on bad output. A case runs against a `Target` that
drives a real turn and reconstructs the answer + tool calls from the durable transcript.

The trajectory is reconstructed from the transcript's structured tool-use / tool-result blocks —
the call names and inputs ride faithfully, but a tool's result is only its transcript text (the
transcript drops the structured result payload), so a result-payload grader cannot be scored from
it. Per-case tool/skill scoping is not expressible either: `invoke` sends a message to an agent
whose tool set is fixed by its config. Both want a structured trajectory-audit-trail core unit."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from selfhost_ext_eval_harness.harness import EvalCaseResult, JsonObject, infra_error

if TYPE_CHECKING:
    from selfhost_ext_eval_harness.target import CapabilityTarget


@dataclass(frozen=True)
class CapabilityVerdict:
    passed: bool
    reason: str


@dataclass(frozen=True)
class ToolInvocation:
    """One tool call the agent made: its name, its input, and the transcript text of its result."""

    name: str
    input: JsonObject
    result: str = ""


@dataclass(frozen=True)
class CapabilityOutput:
    """What a grader sees: the agent's final answer, the ordered tool invocations its turn made, and
    the text of any tool call that errored."""

    response: str
    calls: tuple[ToolInvocation, ...]
    tool_errors: tuple[str, ...] = ()

    @property
    def tools(self) -> tuple[str, ...]:
        return tuple(call.name for call in self.calls)


type Grader = Callable[[CapabilityOutput], Awaitable[CapabilityVerdict]]


@dataclass(frozen=True)
class CapabilityCase:
    """A message and a grader over the answer + trajectory it produces. `web_dependent` marks a case
    that reaches the live web, so a tool failure from an external outage is infra-excluded — neither
    pass nor fail, out of scoring — rather than counted a capability failure; `samples` re-runs the
    case and passes if any sample passes; `digest_tag` stabilizes the suite digest."""

    name: str
    message: str
    grader: Grader
    samples: int = 1
    web_dependent: bool = False
    digest_tag: str = ""

    def payload(self) -> JsonObject:
        return {
            "name": self.name,
            "message": self.message,
            "samples": self.samples,
            "webDependent": self.web_dependent,
            "grader": self.digest_tag or self.name,
        }


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
    return result.output, await case.grader(result.output)
