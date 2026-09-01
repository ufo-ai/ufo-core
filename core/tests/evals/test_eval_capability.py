from typing import cast
from uuid import UUID, uuid4

from evals.harness import capability as harness_capability
from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    EvalTrajectory,
    ToolInvocation,
)
from evals.harness.harness import WAIT_EXPIRED, infra_owned_fault
from evals.harness.target import CapabilityTarget, TargetResult
from ufo.schema.records import TurnStatus


def test_a_wait_expired_before_model_output_is_excluded_a_started_loop_is_not() -> None:
    def unclean(
        status: TurnStatus | None,
        reason: str = WAIT_EXPIRED,
        output: CapabilityOutput | None = None,
    ) -> TargetResult:
        return TargetResult(
            output or CapabilityOutput("", ()),
            clean=False,
            failure_reason=reason,
            trajectory=EvalTrajectory(
                conversation_id=uuid4(),
                turn_id=uuid4(),
                status=status,
                messages=(),
                error=reason,
            ),
        )

    assert harness_capability._unclean_verdict(unclean("running")).excluded
    assert harness_capability._unclean_verdict(unclean("queued")).excluded
    assert not harness_capability._unclean_verdict(unclean("failed")).excluded
    assert not harness_capability._unclean_verdict(unclean(None)).excluded
    assert not harness_capability._unclean_verdict(unclean("running", "turn row vanished")).excluded
    assert harness_capability._unclean_verdict(unclean("cancelled")).excluded
    narrated = harness_capability._unclean_verdict(
        unclean("cancelled", output=CapabilityOutput("Still checking.", ()))
    )
    call = ToolInvocation("object_list", {"kind": "member"}, "{}", True)
    called = harness_capability._unclean_verdict(
        unclean(
            "cancelled",
            output=CapabilityOutput("", (call,), own_calls=(call,)),
        )
    )
    inherited = harness_capability._unclean_verdict(
        unclean("cancelled", output=CapabilityOutput("", (call,)))
    )
    assert not narrated.excluded
    assert narrated.reason == WAIT_EXPIRED
    assert not called.excluded
    assert called.reason == WAIT_EXPIRED
    assert inherited.excluded
    assert not harness_capability._unclean_verdict(unclean("cancelled", "no artifact")).excluded
    provider = TargetResult(
        CapabilityOutput("", ()),
        clean=False,
        failure_reason="model call failed",
        error_class="APIConnectionError",
    )
    assert harness_capability._unclean_verdict(provider).excluded
    assert infra_owned_fault(None, WAIT_EXPIRED, "parked")
    assert not infra_owned_fault(None, WAIT_EXPIRED, "done")


async def test_a_followup_wait_ignores_tool_calls_from_the_completed_first_turn() -> None:
    conversation_id = uuid4()
    first_call = ToolInvocation("object_list", {"kind": "member"}, "{}", True)
    trajectory = EvalTrajectory(
        conversation_id=conversation_id,
        turn_id=uuid4(),
        status="done",
        messages=(),
    )

    class Target:
        async def run(self, _case: CapabilityCase) -> TargetResult:
            return TargetResult(
                CapabilityOutput("", (first_call,), own_calls=(first_call,)),
                clean=True,
                trajectory=trajectory,
            )

        async def step(
            self, _conversation_id: UUID, _message: str, _idempotency_key: str
        ) -> TargetResult:
            return TargetResult(
                CapabilityOutput("", ()),
                clean=False,
                failure_reason=WAIT_EXPIRED,
                trajectory=trajectory.model_copy(update={"status": "cancelled"}),
            )

    async def followup(_output: CapabilityOutput) -> str:
        return "Continue."

    async def grader(_output: CapabilityOutput) -> CapabilityVerdict:
        return CapabilityVerdict(True, "passed")

    sample = await harness_capability.sample_capability(
        CapabilityCase("followup", "Start.", grader, followup=followup),
        cast(CapabilityTarget, Target()),
    )

    assert sample.verdict.excluded
