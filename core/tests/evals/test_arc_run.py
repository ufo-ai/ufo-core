from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID

from evals.harness.arc import NO_TRAJECTORY, ArcCase, ArcObservation, ArcRun, ArcVerdict
from evals.harness.capability import CapabilityOutput, EvalTrajectory, WorkspaceFile
from evals.harness.harness import WAIT_EXPIRED, EvalCaseResult
from evals.harness.target import CapabilityTarget, TargetResult
from ufo.schema.records import TurnStatus

CONVERSATION_ID = UUID(int=1)
TURN_ID = UUID(int=2)


async def _grade(_: ArcObservation) -> ArcVerdict:
    raise AssertionError("an unclean opening must not reach the arc grader")


@dataclass(frozen=True)
class _Conversations:
    root: Path

    async def open(
        self,
        case_name: str,
        member_key: str | None,
        workspace_files: tuple[WorkspaceFile, ...],
    ) -> UUID:
        return CONVERSATION_ID

    def workspace_path(self, conversation_id: UUID, rel: str) -> Path:
        return self.root / rel


@dataclass(frozen=True)
class _OpeningTarget:
    conversations: _Conversations
    opening: TargetResult

    async def step(self, conversation_id: UUID, message: str, idempotency_key: str) -> TargetResult:
        return self.opening


async def _unclean_arc(
    tmp_path: Path, status: TurnStatus | None, error_class: str | None = None
) -> EvalCaseResult:
    opening = TargetResult(
        CapabilityOutput("", ()),
        clean=False,
        failure_reason=WAIT_EXPIRED,
        error_class=error_class,
        trajectory=None
        if status is None
        else EvalTrajectory(
            conversation_id=CONVERSATION_ID,
            turn_id=TURN_ID,
            status=status,
            messages=(),
            error=WAIT_EXPIRED,
        ),
    )
    target = cast(CapabilityTarget, _OpeningTarget(_Conversations(tmp_path), opening))
    case = ArcCase("unclean-opening", "start", _grade, "the arc settles")
    return await ArcRun(case, target).result()


async def test_arc_excludes_a_cancelled_opening_without_a_terminal_transcript(
    tmp_path: Path,
) -> None:
    result = await _unclean_arc(tmp_path, "cancelled")

    assert not result.passed
    assert result.excluded
    assert result.reason == f"opening turn did not settle cleanly: {WAIT_EXPIRED}"
    assert result.evidence["openingStatus"] == "cancelled"


async def test_arc_scores_a_terminal_opening_without_a_transcript_as_a_failure(
    tmp_path: Path,
) -> None:
    result = await _unclean_arc(tmp_path, "done")

    assert not result.passed
    assert not result.excluded
    assert result.reason == f"opening turn did not settle cleanly: {WAIT_EXPIRED}"
    assert result.evidence["openingStatus"] == "done"


async def test_arc_records_which_fault_one_reason_stood_for(tmp_path: Path) -> None:
    """`turn produced no terminal transcript` is one reason over several faults, and the reason
    alone cannot say which. The record carries the status the exclusion turned on, so an archived
    red case reads as a cancelled wait, a turn that terminalized empty, or a run with no
    trajectory at all — measured after a nightly `ab_reversal` red could not be told apart."""
    cancelled = await _unclean_arc(tmp_path, "cancelled")
    terminal = await _unclean_arc(tmp_path, "done")
    absent = await _unclean_arc(tmp_path, None, error_class="RuntimeError")

    assert cancelled.reason == terminal.reason == absent.reason
    statuses = {result.evidence["openingStatus"] for result in (cancelled, terminal, absent)}
    assert statuses == {"cancelled", "done", NO_TRAJECTORY}
    assert absent.evidence["openingErrorClass"] == "RuntimeError"
    assert not absent.excluded
