"""Every `notify_triage` seed replaces the workspace member's whole inbox with its batch, and two
of the three batches share their routine rows. Two cases seeding at once collided on the shared
rows' ids and ended the shard before any case ran, so the task runs its cases one at a time.

The grader over that batch counts messages the member could read. A call the action refused
delivered nothing: on `z-ai/glm-5.3-flash` the app's first `deliver` names invented keys
(`subject`/`body`, `message`/`notification_refs`), takes a validation error, and repairs it on the
next call — one message reached the member, and counting the attempt failed a trajectory that did
the right thing. It also holds the delivery to the surface it landed on, so a push that took the
portal fallback because the member has no reach cannot pass as a push."""

from uuid import uuid4

import sqlalchemy as sa
from ufo_ext_app_notification.deliver import DELIVERED, DELIVERED_TO_PORTAL_ONLY
from ufo_ext_app_notification.notify_tool import NOTIFY_TOOL_NAME

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.registry import TASKS
from evals.suites.notify_triage import (
    CHURN,
    DEPLOY,
    INVESTOR,
    REACH_SURFACE,
    ROUTINE,
    _delivers_exactly,
    _give_the_member_reach,
    _ref,
)
from ufo.db import workspace_tx
from ufo.host.ext.loader import durable_surfaces, load_manifests
from ufo.runtime.ext.context import context_for
from ufo.runtime.surfaces.admission import Admission, AdmissionInvoker
from ufo.runtime.workspace import ws
from ufo.schema import tables

MERIT = (CHURN, DEPLOY, INVESTOR)
ON_SURFACE = DELIVERED.format(surface=REACH_SURFACE)
MALFORMED = "ValidationError: 2 validation errors for DeliverInput"


class _NoDbos:
    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        raise AssertionError("the reach projection admits nothing")


def _deliver(
    refs: tuple[str, ...], result: str = ON_SURFACE, is_error: bool = False
) -> ToolInvocation:
    return ToolInvocation(
        name="object_action",
        input={
            "kind": "notification",
            "action": "deliver",
            "input": {"refs": list(refs), "text": "three things moved overnight"},
        },
        result=result,
        has_result=True,
        is_error=is_error,
    )


def _malformed() -> ToolInvocation:
    """The first call as flash writes it: the kind's own field names, refused by the input model."""
    return ToolInvocation(
        name="object_action",
        input={
            "kind": "notification",
            "action": "deliver",
            "input": {"subject": "Overnight", "body": "three things moved overnight"},
        },
        result=MALFORMED,
        has_result=True,
        is_error=True,
    )


def _output(*calls: ToolInvocation) -> CapabilityOutput:
    return CapabilityOutput(response="Delivered the three that matter.", calls=calls)


def test_notify_triage_runs_its_cases_one_at_a_time() -> None:
    task = next(task for task in TASKS if task.name == "notify_triage")

    assert task.exclusive


def test_notify_triage_runs_only_where_a_surface_can_post() -> None:
    """The grader holds a delivery to the surface it landed on, so the deploy under test has to
    have one that posts. A surface is durable exactly when its extension declares a `post` handler,
    and a pack that ships none makes the reach seed inert: every delivery takes the portal fallback
    and the two cases that merit a push fail for every model however well it judged."""
    task = next(task for task in TASKS if task.name == "notify_triage")
    posting = {pack for pack in task.packs if durable_surfaces(load_manifests(pack))}

    assert task.packs
    assert posting == set(task.packs)
    assert REACH_SURFACE in {
        surface for pack in task.packs for surface in durable_surfaces(load_manifests(pack))
    }


async def test_one_message_naming_the_rows_that_merit_it_passes() -> None:
    verdict = await _delivers_exactly(MERIT)(_output(_deliver(tuple(_ref(e) for e in MERIT))))

    assert verdict.passed
    assert REACH_SURFACE in verdict.reason


async def test_a_refused_call_repaired_on_the_next_one_is_one_message() -> None:
    """The trajectory the ablation recorded: the member read one message, so the case passes."""
    verdict = await _delivers_exactly(MERIT)(
        _output(_malformed(), _deliver(tuple(_ref(e) for e in MERIT)))
    )

    assert verdict.passed


async def test_a_refused_call_alone_delivered_nothing() -> None:
    verdict = await _delivers_exactly(MERIT)(_output(_malformed()))

    assert not verdict.passed
    assert "delivered 0 messages" in verdict.reason


async def test_two_messages_that_both_landed_fail() -> None:
    """The bound the design holds: one message per batch, however many calls carry it."""
    verdict = await _delivers_exactly(MERIT)(
        _output(
            _deliver((_ref(CHURN),)),
            _deliver((_ref(DEPLOY), _ref(INVESTOR))),
        )
    )

    assert not verdict.passed
    assert "delivered 2 messages" in verdict.reason


async def test_a_message_that_took_the_portal_fallback_is_not_a_push() -> None:
    """A member with no durable conversation reads nothing; the rows are only marked. A case that
    means to prove the push must fail here rather than score the fallback."""
    verdict = await _delivers_exactly(MERIT)(
        _output(_deliver(tuple(_ref(e) for e in MERIT), result=DELIVERED_TO_PORTAL_ONLY))
    )

    assert not verdict.passed
    assert REACH_SURFACE in verdict.reason


async def test_the_wrong_rows_fail_however_they_landed() -> None:
    verdict = await _delivers_exactly(MERIT)(_output(_deliver((_ref(CHURN), _ref(ROUTINE[0])))))

    assert not verdict.passed
    assert "wanted" in verdict.reason


async def test_a_routine_batch_passes_only_with_nothing_delivered() -> None:
    quiet = await _delivers_exactly(())(_output())
    refused = await _delivers_exactly(())(_output(_malformed()))
    pushed = await _delivers_exactly(())(_output(_deliver((_ref(ROUTINE[0]),))))

    assert quiet.passed
    assert refused.passed
    assert not pushed.passed


async def test_the_app_raising_on_its_own_batch_fails_before_anything_else() -> None:
    verdict = await _delivers_exactly(MERIT)(
        _output(
            ToolInvocation(name=NOTIFY_TOOL_NAME, input={}, result="Queued.", has_result=True),
            _deliver(tuple(_ref(e) for e in MERIT)),
        )
    )

    assert not verdict.passed
    assert NOTIFY_TOOL_NAME in verdict.reason


async def test_the_seed_gives_the_member_a_reach_the_projection_answers(db: None) -> None:
    """The seed's own end: without a conversation `member_reach` returns, every delivery takes the
    portal fallback and the case grades a push nobody read."""
    workspace_id, member_id, agent_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="founder@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="be brief",
                model="claude-opus-4-8",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    invoker = AdmissionInvoker(
        admission=Admission(dbos=_NoDbos(), durable_surfaces=frozenset({REACH_SURFACE})),
        workspace_id=workspace_id,
    )
    ext = context_for("app_notification", frozenset(), invoker=invoker, member_context_read=True)
    with ws(workspace_id):
        await _give_the_member_reach(member_id)
        await _give_the_member_reach(member_id)
        reach = await ext.member_reach(member_id)

    assert [(one.surface, one.agent_id) for one in reach] == [(REACH_SURFACE, agent_id)]
