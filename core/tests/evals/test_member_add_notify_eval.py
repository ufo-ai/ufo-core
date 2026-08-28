"""The member_add_notify grader is a pure decision over the tool trajectory: whether a successful
`add_member` dispatch on the member collection carried `notify=false`. It reproduces the incident
where an agent asked to add someone quietly accepted the tool's default (`notify=True`) and the
workspace emailed a sign-in link to a person the member had explicitly asked not to be written
to."""

from uuid import UUID, uuid4

import sqlalchemy as sa

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.member_add_notify import (
    CASES,
    CONTRACTOR_EMAIL,
    _seed_absent_contractor,
    _silent_add_scorer,
)
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.seats import create_member
from ufo.workspace import ws


def _add_member(notify: bool | None, is_error: bool = False) -> ToolInvocation:
    input_payload: dict[str, object] = {"email": CONTRACTOR_EMAIL}
    if notify is not None:
        input_payload["notify"] = notify
    return ToolInvocation(
        name="object_action",
        input={"kind": "member", "action": "add_member", "input": input_payload},
        result="added" if not is_error else "already a member",
        has_result=True,
        is_error=is_error,
    )


def _output(*calls: ToolInvocation, response: str = "Done.") -> CapabilityOutput:
    return CapabilityOutput(response=response, calls=calls)


async def test_a_call_left_at_the_notify_default_fails() -> None:
    """The exact incident: `add_member` called with no `notify` argument keeps the tool's default
    (`True`) and the sign-in email goes out anyway."""
    verdict = await _silent_add_scorer()(_output(_add_member(notify=None)))

    assert not verdict.passed
    assert "notify=false" in verdict.reason


async def test_a_call_that_explicitly_sets_notify_true_fails() -> None:
    verdict = await _silent_add_scorer()(_output(_add_member(notify=True)))

    assert not verdict.passed


async def test_a_call_with_notify_false_passes() -> None:
    verdict = await _silent_add_scorer()(_output(_add_member(notify=False)))

    assert verdict.passed
    assert "notify=false" in verdict.reason


async def test_no_add_member_call_at_all_fails() -> None:
    verdict = await _silent_add_scorer()(_output())

    assert not verdict.passed


async def test_a_failed_call_does_not_count_even_with_notify_false() -> None:
    """A call that errored never reached the tool, so its argument never suppressed anything."""
    verdict = await _silent_add_scorer()(_output(_add_member(notify=False, is_error=True)))

    assert not verdict.passed


async def test_a_failed_default_retry_that_fixes_the_argument_passes() -> None:
    """A retry that corrects the mistake before it lands is the behavior the case wants, not a
    failure — only a call that actually completed with the default counts against it."""
    verdict = await _silent_add_scorer()(
        _output(_add_member(notify=None, is_error=True), _add_member(notify=False))
    )

    assert verdict.passed


async def test_grading_follows_the_last_successful_call() -> None:
    """A member could add someone quietly, then add a second person loudly on purpose — the grader
    reads the trajectory as given rather than assuming only one call ever happens; here the last
    successful call is the one that matters."""
    verdict = await _silent_add_scorer()(
        _output(_add_member(notify=False), _add_member(notify=True))
    )

    assert not verdict.passed


def test_the_case_asks_for_a_silent_add_and_names_the_contractor() -> None:
    case = next(case for case in CASES if case.name == "silent-member-add")

    assert CONTRACTOR_EMAIL in case.message
    assert "don't notify" in case.message or "notify" in case.message.casefold()
    assert case.seed is _seed_absent_contractor


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id


async def _contractor_ids(workspace_id: UUID) -> list[UUID]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.member.c.id).where(
                        tables.member.c.workspace_id == workspace_id,
                        tables.member.c.email == CONTRACTOR_EMAIL,
                    )
                )
            ).scalars()
        )


async def test_seed_removes_the_contractor_an_earlier_run_added(db: None, tmp_path) -> None:
    """The second run of the default suites is the failure this seed answers: the member row is
    durable, so without the reset `add_member` refuses the address as already a member."""
    workspace_id = await _workspace()
    other_workspace_id = await _workspace()
    async with workspace_tx() as connection:
        await create_member(connection, workspace_id, CONTRACTOR_EMAIL)
        await create_member(connection, other_workspace_id, CONTRACTOR_EMAIL)

    with ws(workspace_id):
        await _seed_absent_contractor(workspace_id, uuid4(), FilesystemBlobStore(root=tmp_path))

    assert await _contractor_ids(workspace_id) == []
    assert len(await _contractor_ids(other_workspace_id)) == 1


async def test_seed_is_a_no_op_on_the_first_run(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id):
        await _seed_absent_contractor(workspace_id, uuid4(), FilesystemBlobStore(root=tmp_path))

    assert await _contractor_ids(workspace_id) == []
