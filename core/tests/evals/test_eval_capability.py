from typing import cast
from uuid import UUID, uuid4

import pytest

from evals.harness import capability as harness_capability
from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    EvalTrajectory,
    ToolInvocation,
)
from evals.harness.harness import (
    WAIT_EXPIRED,
    infra_error,
    infra_owned_fault,
    is_transient_fault,
    provider_owned_error,
)
from evals.harness.target import CapabilityTarget, TargetResult, capability_output
from ufo.schema.records import TurnStatus
from ufo.sdk.models import Message


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
    assert harness_capability._unclean_verdict(provider).provider_fault
    assert not harness_capability._unclean_verdict(unclean("running")).provider_fault
    assert infra_owned_fault(None, WAIT_EXPIRED, "parked")
    assert not infra_owned_fault(None, WAIT_EXPIRED, "done")


def test_an_external_service_status_is_read_as_a_whole_number() -> None:
    """The three digits of a status also sit inside run directories, digests, and ids. Reading one
    out of an identifier excludes a case the model genuinely failed and drops it from the cohort."""
    identifier = (
        "Traceback (most recent call last):\n"
        '  File "/x/.local/evals/20260903-85c3f35735ca40299ab436/make_poster.py", line 70\n'
        "AttributeError: 'tuple' object has no attribute 'load'"
    )

    assert infra_error((identifier,)) == ""
    assert infra_error(("fetch failed with status 429 from the upstream",))
    assert infra_error(("HTTP 402: payment required",))
    assert infra_error(("upstream returned 503",))


def test_a_provider_rate_limit_is_the_providers_fault_under_either_name() -> None:
    """A 429 the client's retries could not outlast reaches the terminal as the repo's own typed
    class, where the SDK's status class used to surface. Both name the same external limit, so a
    run that met one is excluded from scoring rather than counted against the model."""
    for error_class in ("ModelAccountRateLimited", "RateLimitError"):
        assert is_transient_fault(error_class)
        assert infra_owned_fault(error_class, "model call failed", None)
        limited = TargetResult(
            CapabilityOutput("", ()),
            clean=False,
            failure_reason="model call failed",
            error_class=error_class,
        )
        assert harness_capability._unclean_verdict(limited).excluded


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


async def test_a_web_dependent_exclusion_records_who_owns_the_fault() -> None:
    """The nightly cohort gate accepts an exclusion the provider owns and refuses every other one,
    so the archived record has to name the owner. A web tool answered 429 is provider weather; an
    eval account out of credit is ours and stays unexpected."""

    def target(tool_error: str) -> CapabilityTarget:
        call = ToolInvocation("fetch_url", {"url": "https://example.com"}, tool_error, True, True)

        class Target:
            async def run(self, _case: CapabilityCase) -> TargetResult:
                return TargetResult(
                    CapabilityOutput("", (call,), tool_errors=(tool_error,)),
                    clean=True,
                    trajectory=EvalTrajectory(
                        conversation_id=uuid4(),
                        turn_id=uuid4(),
                        status="done",
                        messages=(),
                    ),
                )

        return cast(CapabilityTarget, Target())

    async def grader(_output: CapabilityOutput) -> CapabilityVerdict:
        return CapabilityVerdict(False, "the answer names no source")

    case = CapabilityCase("web-research", "Look it up.", grader, web_dependent=True)

    weather = await harness_capability.run_capability_case(
        case, target("fetch_url failed: 429 Too Many Requests from the upstream")
    )
    dry = await harness_capability.run_capability_case(
        case, target("fetch_url failed: HTTP 402: payment required")
    )

    assert weather.excluded
    assert weather.provider_fault
    assert dry.excluded
    assert not dry.provider_fault
    assert provider_owned_error("upstream returned 503")
    assert provider_owned_error("read timed out")
    assert not provider_owned_error("no credits remaining")
    assert not provider_owned_error("the poster script raised AttributeError")


class APIConnectionError(Exception):
    """The transient class the OpenAI client raises when the judge's transport drops."""


def _judged_case(rubric_target_judge: object) -> tuple[CapabilityCase, CapabilityTarget]:
    class Target:
        judge = rubric_target_judge

        async def run(self, _case: CapabilityCase) -> TargetResult:
            return TargetResult(
                CapabilityOutput("The total is 223.", ()),
                clean=True,
                trajectory=EvalTrajectory(
                    conversation_id=uuid4(), turn_id=uuid4(), status="done", messages=()
                ),
            )

    async def grader(_output: CapabilityOutput) -> CapabilityVerdict:
        return CapabilityVerdict(True, "answered")

    case = CapabilityCase("judged", "Total them.", grader, rubric=("States the total.",))
    return case, cast(CapabilityTarget, Target())


async def test_a_judge_transient_excludes_the_sample_as_the_providers_fault() -> None:
    """The target answered and the judge's transport dropped. Raising out of the sample used to end
    the whole shard, taking every suite still queued behind it out of the night's cohort; the sample
    now leaves the denominator, owned by the provider so the nightly gate accepts it."""

    class Judge:
        async def complete(self, _system: str, _messages: tuple[Message, ...]) -> str:
            raise APIConnectionError("Connection error.")

    case, target = _judged_case(Judge())

    sample = await harness_capability.sample_capability(case, target)

    assert sample.verdict.excluded
    assert sample.verdict.provider_fault
    assert "APIConnectionError" in sample.verdict.reason


async def test_a_judge_fault_of_our_own_still_raises() -> None:
    class Judge:
        async def complete(self, _system: str, _messages: tuple[Message, ...]) -> str:
            raise ValueError("the judge prompt is malformed")

    case, target = _judged_case(Judge())

    with pytest.raises(ValueError, match="malformed"):
        await harness_capability.sample_capability(case, target)


def test_the_rebuilt_answer_projects_the_workspace_link_the_window_keeps() -> None:
    """The transcript keeps the workspace link for later turns while the member reads its label."""
    messages = (
        Message(role="user", content="should we move?"),
        Message(
            role="assistant",
            content="Move the jobs onto a queue.\n\n[plan.md](/workspace/plan.md)\n",
        ),
    )

    output = capability_output(messages)

    assert output.response == "Move the jobs onto a queue.\n\nplan.md\n"
    assert output.calls == ()
