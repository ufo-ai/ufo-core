from dataclasses import dataclass
from pathlib import Path
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
    run_capability_case,
)
from evals.harness.harness import (
    WAIT_EXPIRED,
    infra_error,
    infra_owned_fault,
    is_transient_fault,
    provider_owned_error,
    provider_owned_fault,
)
from evals.harness.scorers import exact_scorer
from evals.harness.target import (
    CapabilityTarget,
    EvalConversations,
    InProcessTarget,
    TargetResult,
    TurnOutcome,
    capability_output,
)
from ufo.blob import WorkspaceBlobStore
from ufo.runtime.ext.context import ExtensionContext
from ufo.runtime.workspace import ws
from ufo.schema.records import TurnStatus
from ufo.sdk.models import Message


def test_the_harness_stopwatch_excludes_whether_or_not_the_model_had_spoken() -> None:
    """A wait the harness's own cancel ended is its measurement, not the model's answer. While
    prose or one of the turn's own calls kept such an expiry scored, the 2026-09-12 sweep charged
    24 cases for a budget the shard chose — every one of them ending at its wait to the tenth of a
    second. Only a turn that reached its own terminal without a transcript stays a failure."""

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

    call = ToolInvocation("object_list", {"kind": "member"}, "{}", True)
    assert harness_capability._unclean_verdict(unclean("running")).excluded
    assert harness_capability._unclean_verdict(unclean("queued")).excluded
    assert harness_capability._unclean_verdict(unclean("cancelled")).excluded
    assert harness_capability._unclean_verdict(
        unclean("cancelled", output=CapabilityOutput("Still checking.", ()))
    ).excluded
    assert harness_capability._unclean_verdict(
        unclean("cancelled", output=CapabilityOutput("", (call,), own_calls=(call,)))
    ).excluded
    assert not harness_capability._unclean_verdict(unclean("failed")).excluded
    assert not harness_capability._unclean_verdict(unclean("done")).excluded
    assert not harness_capability._unclean_verdict(unclean(None)).excluded
    assert not harness_capability._unclean_verdict(unclean("running", "turn row vanished")).excluded
    assert not harness_capability._unclean_verdict(unclean("cancelled", "no artifact")).excluded
    provider = TargetResult(
        CapabilityOutput("", ()),
        clean=False,
        failure_reason="model call failed",
        error_class="APIConnectionError",
    )
    assert harness_capability._unclean_verdict(provider).excluded
    assert harness_capability._unclean_verdict(provider).provider_fault
    assert infra_owned_fault(None, WAIT_EXPIRED, "parked")
    assert not infra_owned_fault(None, WAIT_EXPIRED, "done")


def test_every_exclusion_names_the_owner_the_cohort_gate_reads() -> None:
    """`excluded` and `provider_fault` come out of one decision. While two decisions drew them, a
    wait that expired with no transcript excluded a case that named no owner, and the nightly gate
    read that as cohort drift and reds the sweep over it. The harness cancels every overdue turn,
    so the record reads `cancelled` whatever the turn was doing, and only the status the wait
    expired on, with the turn's own first step beside it, names the owner. A turn that was working
    is a clock the gate accepts; a turn behind our own workers, a parked turn, a `running` turn the
    engine had not stepped yet — the dispatch claim writes that status before the sandbox has even
    booted — an expiry no status was read for, and a rejected eval credential are the sweep's own
    shape, and stay refused."""

    def unclean(
        expiry_status: TurnStatus | None,
        error_class: str | None = None,
        status: TurnStatus = "cancelled",
        work_started: bool = True,
    ) -> TargetResult:
        return TargetResult(
            CapabilityOutput("", ()),
            clean=False,
            failure_reason=WAIT_EXPIRED,
            error_class=error_class,
            trajectory=EvalTrajectory(
                conversation_id=uuid4(),
                turn_id=uuid4(),
                status=status,
                messages=(),
                error=WAIT_EXPIRED,
            ),
            expiry_status=expiry_status,
            work_started=work_started,
        )

    answering = harness_capability._unclean_verdict(unclean("running"))
    booting = harness_capability._unclean_verdict(unclean("running", work_started=False))
    queued = harness_capability._unclean_verdict(unclean("queued"))
    parked = harness_capability._unclean_verdict(unclean("parked"))
    unread = harness_capability._unclean_verdict(unclean(None))
    credential = harness_capability._unclean_verdict(
        unclean(None, "CredentialValueInvalid", "failed")
    )

    assert answering.excluded
    assert answering.provider_fault
    assert answering.reason.endswith("the harness's wait expired on a turn still working")
    assert booting.excluded
    assert not booting.provider_fault
    assert booting.reason.endswith("the harness's wait expired before the turn began its own work")
    assert queued.excluded
    assert not queued.provider_fault
    assert queued.reason.endswith("the harness's wait expired before the turn began its own work")
    assert parked.excluded
    assert not parked.provider_fault
    assert unread.excluded
    assert not unread.provider_fault
    assert credential.excluded
    assert not credential.provider_fault
    assert provider_owned_fault(None, WAIT_EXPIRED, "running", True)
    assert not provider_owned_fault(None, WAIT_EXPIRED, "running", False)
    assert not provider_owned_fault(None, WAIT_EXPIRED, "queued", True)
    assert not provider_owned_fault(None, WAIT_EXPIRED, "parked", True)
    assert not provider_owned_fault(None, WAIT_EXPIRED, "cancelled", True)
    assert not provider_owned_fault(None, WAIT_EXPIRED, None, True)
    assert not provider_owned_fault("CredentialValueInvalid", "boom", "failed", True)
    assert not provider_owned_fault(None, WAIT_EXPIRED, "done", True)
    assert provider_owned_fault("OverloadedError", "model call failed", None, False)
    assert infra_owned_fault(None, WAIT_EXPIRED, "cancelled")
    assert infra_owned_fault(None, WAIT_EXPIRED, "running")


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


async def test_a_seed_that_raises_fails_its_case_and_leaves_the_suite_standing(
    tmp_path: Path,
) -> None:
    """A seed raising out of the suite loses every case of it and the report with them: the nightly
    summary then reads a suite that never ran. `github_connections` refused a workspace another
    suite had already connected GitHub on, and produced no report on two 2026-09 nights."""
    target = InProcessTarget(
        ctx=cast(ExtensionContext, object()),
        agent_id=uuid4(),
        conversations=cast(EvalConversations, _RefusingConversations()),
        outcome=cast(TurnOutcome, object()),
        blob=cast(WorkspaceBlobStore, object()),
    )

    async def seed(workspace_id: UUID, agent_id: UUID, blob: WorkspaceBlobStore) -> None:
        raise RuntimeError("this workspace already holds an account")

    case = CapabilityCase("seeded", "hello", exact_scorer("never graded"), seed=seed)

    with ws(uuid4()):
        result = await run_capability_case(case, target)

    assert not result.passed
    assert not result.excluded
    assert "seed raised: RuntimeError: this workspace already holds an account" in result.reason


@dataclass(frozen=True)
class _RefusingConversations:
    async def open(self, *args: object, **kwargs: object) -> UUID:
        raise AssertionError("a case whose seed raised must never open a conversation")
