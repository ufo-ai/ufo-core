import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError

from ufo.db import workspace_tx
from ufo.harness import o11y
from ufo.runtime.engine import _claim_turn
from ufo.runtime.ext.context import AgentArchived
from ufo.runtime.ext.surface import Admitted, conversation_name, fence_member_message, mint_marker
from ufo.runtime.hub import Absorbed, ArrivalQueued, InProcessHub, Reply
from ufo.runtime.seats import SEAT_REFUSAL_MESSAGE, UNRESOLVED_SPEAKER_MESSAGE
from ufo.runtime.surfaces.admission import (
    ADMITTED_TURN_METRIC,
    ARCHIVED_REFUSAL_MESSAGE,
    Admission,
)
from ufo.runtime.turns.audience import conversation_audience
from ufo.schema import tables
from ufo.schema.records import (
    REPLY_REACHES_NOBODY,
    SURFACE_COMMENT_ROUND_INDEX,
    ModelAccountCapability,
    TerminalFrame,
    TurnContext,
    TurnRuntimeConfig,
)

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]


@dataclass
class StubDbos:
    """Stands in for the DBOS client at the admission seam: enqueue records the workflow id so a
    test reads back which turns were placed on the queue, never asserting DBOS itself."""

    enqueued: list[str] = field(default_factory=list)
    workflow_ids: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: dict[str, str], workspace_id: str, turn_id: str) -> None:
        self.enqueued.append(turn_id)
        self.workflow_ids.append(options["workflow_id"])


async def _invoke(
    admission: Admission,
    workspace_id: UUID,
    conversation_id: UUID,
    agent_id: UUID,
    body: str,
    idempotency_key: str | None = None,
    context: TurnContext | None = None,
    *,
    holds_work_already_done: bool = False,
    as_scheduled: bool = False,
    standalone: bool = False,
    unless_member_since: int | None = None,
    unless_member_arrival_since: int | None = None,
    runtime_config: TurnRuntimeConfig | None = None,
    acting_member_id: UUID | None = None,
) -> UUID | None:
    return await admission.invoke(
        workspace_id,
        conversation_id,
        agent_id,
        body,
        idempotency_key,
        context,
        holds_work_already_done=holds_work_already_done,
        as_scheduled=as_scheduled,
        standalone=standalone,
        unless_member_since=unless_member_since,
        unless_member_arrival_since=unless_member_arrival_since,
        runtime_config=runtime_config,
        acting_member_id=acting_member_id,
    )


class _AcceptedThenErroredDbos:
    async def enqueue_async(self, options: dict[str, str], workspace_id: str, turn_id: str) -> None:
        await _claim_turn(UUID(turn_id), turn_id)
        raise RuntimeError("ambiguous enqueue response")


class _FailedDbos:
    async def enqueue_async(self, options: dict[str, str], workspace_id: str, turn_id: str) -> None:
        raise RuntimeError("enqueue failed")


async def _seed() -> tuple[UUID, UUID, UUID, UUID]:
    workspace_id, member_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4(), uuid4()
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
                email="who@example.com",
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
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key="session",
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, member_id, agent_id, conversation_id


async def _turn_count(conversation_id: UUID) -> int:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.turn)
                .where(tables.turn.c.conversation_id == conversation_id)
            )
        ).scalar_one()


async def test_member_admission_persists_the_latest_valid_timezone(db: None) -> None:
    workspace_id, member_id, _agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    await admission.admit_member(
        workspace_id,
        conversation_id,
        "hello",
        member_id,
        context=TurnContext(timezone="America/Los_Angeles"),
    )
    async with workspace_tx() as connection:
        timezone = (
            await connection.execute(
                sa.select(tables.member.c.timezone).where(tables.member.c.id == member_id)
            )
        ).scalar_one()
    assert timezone == "America/Los_Angeles"


async def test_member_admission_refuses_a_causal_request_override(db: None) -> None:
    workspace_id, member_id, _agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())

    with pytest.raises(ValueError, match="cannot carry another requesting message"):
        await admission.admit_member(
            workspace_id,
            conversation_id,
            "hello",
            member_id,
            context=TurnContext(requesting_message_ref=uuid4()),
        )


async def test_each_opening_turn_persists_its_own_runtime_config(db: None) -> None:
    workspace_id, member_id, _agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first_config = TurnRuntimeConfig(model="model-one")
    second_config = TurnRuntimeConfig(model="model-two")

    first = await admission.admit_member(
        workspace_id,
        conversation_id,
        "first",
        member_id,
        runtime_config=first_config,
    )
    await _finish(first.turn_id)
    await admission.admit_member(
        workspace_id,
        conversation_id,
        "second",
        member_id,
        runtime_config=second_config,
    )

    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.turn.c.runtime_config)
                .where(tables.turn.c.conversation_id == conversation_id)
                .order_by(tables.turn.c.seq)
            )
        ).scalars()
    assert tuple(TurnRuntimeConfig.model_validate(value) for value in rows) == (
        first_config,
        second_config,
    )


@pytest.mark.parametrize(
    "runtime_config",
    [None, TurnRuntimeConfig(internet_access=False)],
    ids=["unpinned", "pinned"],
)
async def test_paid_work_under_the_live_turns_runtime_config_folds_into_it(
    db: None, runtime_config: TurnRuntimeConfig | None
) -> None:
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    live = await admission.admit_member(
        workspace_id, conversation_id, "dequeue", member_id, runtime_config=runtime_config
    )
    assert await _claim_turn(live.turn_id, str(live.turn_id))

    delivered = await _invoke(
        admission,
        workspace_id,
        conversation_id,
        agent_id,
        "<spawn_result>done</spawn_result>",
        "subagent-result:child",
        holds_work_already_done=True,
        runtime_config=runtime_config,
    )

    assert delivered == live.turn_id
    assert await _turn_count(conversation_id) == 1
    assert await _queued_bodies(conversation_id) == ["<spawn_result>done</spawn_result>"]
    assert dbos.enqueued == [str(live.turn_id)]


@pytest.mark.parametrize(
    "delivered_config",
    [TurnRuntimeConfig(environment="sha256:" + "1" * 64), None],
    ids=["pinned-elsewhere", "unpinned"],
)
async def test_paid_work_under_another_runtime_config_waits_in_its_own_turn(
    db: None, delivered_config: TurnRuntimeConfig | None
) -> None:
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    live_config = TurnRuntimeConfig(internet_access=False)
    live = await admission.admit_member(
        workspace_id,
        conversation_id,
        "new request",
        member_id,
        runtime_config=live_config,
    )

    delivered = await _invoke(
        admission,
        workspace_id,
        conversation_id,
        agent_id,
        "<spawn_result>done</spawn_result>",
        "subagent-result:old-config",
        holds_work_already_done=True,
        runtime_config=delivered_config,
    )

    assert delivered is not None
    assert delivered != live.turn_id
    assert await _queued_bodies(conversation_id) == []
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.runtime_config).where(
                    tables.turn.c.id == delivered
                )
            )
        ).one()
    assert row.status == "queued"
    assert (
        None if row.runtime_config is None else TurnRuntimeConfig.model_validate(row.runtime_config)
    ) == delivered_config
    assert (
        await _invoke(
            admission,
            workspace_id,
            conversation_id,
            agent_id,
            "<spawn_result>done</spawn_result>",
            "subagent-result:old-config",
            holds_work_already_done=True,
            runtime_config=delivered_config,
        )
        == delivered
    )
    with pytest.raises(ValueError, match="already has a different runtime config"):
        await _invoke(
            admission,
            workspace_id,
            conversation_id,
            agent_id,
            "<spawn_result>done</spawn_result>",
            "subagent-result:old-config",
            holds_work_already_done=True,
            runtime_config=live_config if delivered_config is None else None,
        )
    assert dbos.enqueued == [str(live.turn_id)]


async def test_paid_work_acting_for_another_member_waits_in_its_own_turn(db: None) -> None:
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    other = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=other,
                workspace_id=workspace_id,
                email="other@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    live = await admission.admit_member(workspace_id, conversation_id, "new request", member_id)

    waiting = await _invoke(
        admission,
        workspace_id,
        conversation_id,
        agent_id,
        "<spawn_result>theirs</spawn_result>",
        "subagent-result:other-member",
        holds_work_already_done=True,
        acting_member_id=other,
    )
    folded = await _invoke(
        admission,
        workspace_id,
        conversation_id,
        agent_id,
        "<spawn_result>ours</spawn_result>",
        "subagent-result:same-member",
        holds_work_already_done=True,
        acting_member_id=member_id,
    )

    assert waiting is not None and waiting != live.turn_id
    assert folded == live.turn_id
    assert await _queued_bodies(conversation_id) == ["<spawn_result>ours</spawn_result>"]
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.member_id).where(
                    tables.turn.c.id == waiting
                )
            )
        ).one()
    assert (row.status, row.member_id) == ("queued", other)


async def test_member_admission_cannot_mint_a_model_account_capability(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    with pytest.raises(TypeError, match="unexpected keyword argument 'model_accounts'"):
        await cast(Any, Admission(dbos=StubDbos(), durable_surfaces=frozenset()).admit_member)(
            workspace_id,
            conversation_id,
            "use this account",
            member_id,
            model_accounts=(
                ModelAccountCapability(
                    provider="openai", slot=f"openai_api_key:member:{member_id}"
                ),
            ),
        )


def test_model_account_capability_accepts_only_canonical_member_slots() -> None:
    with pytest.raises(ValidationError, match="does not match its provider"):
        ModelAccountCapability(provider="openai", slot="connector:github")

    with pytest.raises(ValidationError, match="must name a member credential"):
        ModelAccountCapability(provider="openai", slot="openai_api_key:member:not-a-uuid")


async def _queued_bodies(conversation_id: UUID) -> list[str]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.inbound_message.c.body)
                    .where(
                        tables.inbound_message.c.conversation_id == conversation_id,
                        tables.inbound_message.c.consumed_turn_id.is_(None),
                    )
                    .order_by(tables.inbound_message.c.seq)
                )
            ).scalars()
        )


async def _queued_ids(conversation_id: UUID) -> list[UUID]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.inbound_message.c.id)
                    .where(
                        tables.inbound_message.c.conversation_id == conversation_id,
                        tables.inbound_message.c.consumed_turn_id.is_(None),
                    )
                    .order_by(tables.inbound_message.c.seq)
                )
            ).scalars()
        )


async def _finish(turn_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(
                status="done",
                terminal=TerminalFrame(status="done", text="ok").model_dump(mode="json"),
                updated_at=sa.func.now(),
            )
            .where(tables.turn.c.id == turn_id)
        )


async def test_repeated_delivery_dedups_to_one_turn(db: None) -> None:
    workspace_id, _member_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await _invoke(admission, workspace_id, conversation_id, agent_id, "hi", "C0000001:1.5")
    second = await _invoke(admission, workspace_id, conversation_id, agent_id, "hi", "C0000001:1.5")
    assert first == second
    assert await _turn_count(conversation_id) == 1
    assert dbos.enqueued == [str(first), str(first)]
    assert dbos.workflow_ids == [str(first), str(first)]


async def test_the_opening_turn_names_the_conversation_and_no_later_one_renames_it(
    db: None,
) -> None:
    """A conversation is called what its first message said — the member's own words out of the
    fence, never the ambient digest a channel surface renders around them. Every later message
    leaves that name standing: a rail row that renamed itself on each reply would name a
    conversation something its member never chose."""
    workspace_id, _member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    marker = mint_marker()
    opened = await _invoke(
        admission,
        workspace_id,
        conversation_id,
        agent_id,
        fence_member_message(
            marker,
            "<ambient>a bystander said something</ambient>\n",
            "roll the warehouse plan forward",
            "",
        ),
        "C0000001:1.5",
    )
    await _finish(opened)
    await _invoke(
        admission, workspace_id, conversation_id, agent_id, "and one more thing", "C0000001:2.0"
    )

    async with workspace_tx() as connection:
        title = (
            await connection.execute(
                sa.select(tables.conversation.c.title).where(
                    tables.conversation.c.id == conversation_id
                )
            )
        ).scalar_one()

    assert title == "roll the warehouse plan forward"


async def test_idempotency_key_keeps_the_first_body(db: None) -> None:
    workspace_id, _member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await _invoke(admission, workspace_id, conversation_id, agent_id, "hi", "C0000001:1.5")
    second = await _invoke(
        admission, workspace_id, conversation_id, agent_id, "different", "C0000001:1.5"
    )
    assert second == first
    async with workspace_tx() as connection:
        inbound = (
            await connection.execute(
                sa.select(tables.turn.c.inbound).where(tables.turn.c.id == first)
            )
        ).scalar_one()
    assert inbound == "hi"


async def test_a_fold_logs_the_turn_and_the_row_it_landed_on(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    """The fold is where a message stops being a turn of its own: an operator tracing what a turn
    was asked, and whether the turn answered it, starts from this line."""
    workspace_id, member_id, _, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "one", member_id, "C:1")
    with caplog.at_level(logging.INFO, logger="ufo"):
        folded = await admission.admit_member(
            workspace_id, conversation_id, "two", member_id, "C:2"
        )
    (queued,) = [record.ufo for record in caplog.records if record.getMessage() == "arrival.queued"]
    assert (
        queued["turn_id"],
        queued["arrival_id"],
        queued["admission_source"],
        queued["turn_status"],
    ) == (str(first.turn_id), str(folded.arrival_id), "member", "queued")


async def test_message_while_a_turn_runs_joins_its_inbound_queue(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "one", member_id, "C:1")
    assert await _claim_turn(first.turn_id, str(first.turn_id))
    second = await admission.admit_member(workspace_id, conversation_id, "two", member_id, "C:2")
    assert second == Admitted(
        first.turn_id, opened_run=False, arrival_id=(await _queued_ids(conversation_id))[0]
    )
    assert await _turn_count(conversation_id) == 1
    assert await _queued_bodies(conversation_id) == ["two"]


async def test_every_admission_source_joins_the_live_turn(db: None) -> None:
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await _invoke(admission, workspace_id, conversation_id, agent_id, "job prompt", "job:1")
    second = await admission.admit_member(workspace_id, conversation_id, "hello", member_id, "C:2")
    third = await _invoke(admission, workspace_id, conversation_id, agent_id, "another job")
    assert second == Admitted(
        first, opened_run=False, arrival_id=(await _queued_ids(conversation_id))[0]
    )
    assert third == first
    assert await _turn_count(conversation_id) == 1
    assert await _queued_bodies(conversation_id) == ["hello", "another job"]


async def test_a_reject_cap_stops_arrivals_at_the_boundary(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "one", member_id, "C:1")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.spend_cap).values(
                id=uuid4(),
                workspace_id=workspace_id,
                scope="workspace",
                subject_id=None,
                window_seconds=3600,
                limit_micro_usd=1,
                on_breach="reject",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.ledger).values(
                id=uuid4(),
                workspace_id=workspace_id,
                turn_id=first.turn_id,
                dimension="tokens",
                amount=10,
                prompt_tokens=10,
                input_tokens=10,
                priced_micro_usd=100,
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    second = await admission.admit_member(workspace_id, conversation_id, "two", member_id, "C:2")
    assert second.turn_id != first.turn_id
    assert not second.opened_run
    assert await _queued_bodies(conversation_id) == []
    async with workspace_tx() as connection:
        status = (
            await connection.execute(
                sa.select(tables.turn.c.status).where(tables.turn.c.id == second.turn_id)
            )
        ).scalar_one()
    assert status == "cancelled"


async def test_message_after_a_terminal_turn_starts_a_new_turn(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "one", member_id, "C:1")
    await _finish(first.turn_id)
    second = await admission.admit_member(workspace_id, conversation_id, "two", member_id, "C:2")
    assert second.turn_id != first.turn_id
    assert second.opened_run
    assert await _turn_count(conversation_id) == 2
    assert await _queued_bodies(conversation_id) == []


async def test_enqueue_failure_keeps_the_turn_queued_for_dispatch(db: None) -> None:
    workspace_id, _member_id, agent_id, conversation_id = await _seed()
    turn_id = await _invoke(
        Admission(dbos=_FailedDbos(), durable_surfaces=frozenset()),
        workspace_id,
        conversation_id,
        agent_id,
        "hi",
    )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.turn.c.status,
                    tables.turn.c.terminal,
                    tables.turn.c.dispatch_enqueued_at,
                ).where(tables.turn.c.id == turn_id)
            )
        ).one()
    assert tuple(row) == ("queued", None, None)


async def test_enqueue_error_does_not_fail_a_turn_already_claimed_by_the_worker(db: None) -> None:
    workspace_id, _member_id, agent_id, conversation_id = await _seed()
    turn_id = await _invoke(
        Admission(dbos=_AcceptedThenErroredDbos(), durable_surfaces=frozenset()),
        workspace_id,
        conversation_id,
        agent_id,
        "hi",
    )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.terminal).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).one()
    assert tuple(row) == ("running", None)


async def test_redelivery_refounds_a_row_whose_turn_died_undrained(db: None) -> None:
    """A provider retry of a message whose live turn failed before draining it must not point at
    the dead turn: the row re-founds a fresh queued turn carrying the same idempotency key."""
    workspace_id, member_id, _, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "one", member_id, "C:1")
    second = await admission.admit_member(workspace_id, conversation_id, "two", member_id, "C:2")
    assert second == Admitted(
        first.turn_id, opened_run=False, arrival_id=(await _queued_ids(conversation_id))[0]
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(
                status="failed",
                terminal=TerminalFrame(status="failed", error_class="Boom").model_dump(mode="json"),
                updated_at=sa.func.now(),
            )
            .where(tables.turn.c.id == first.turn_id)
        )
    redelivered = await admission.admit_member(
        workspace_id, conversation_id, "two", member_id, "C:2"
    )
    assert redelivered.turn_id != first.turn_id
    assert redelivered.opened_run
    async with workspace_tx() as connection:
        refounded = (
            await connection.execute(
                sa.select(
                    tables.turn.c.inbound,
                    tables.turn.c.status,
                    tables.turn.c.idempotency_key,
                ).where(tables.turn.c.id == redelivered.turn_id)
            )
        ).one()
        rows = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.inbound_message))
        ).scalar_one()
    assert (refounded.inbound, refounded.status, refounded.idempotency_key) == (
        "two",
        "queued",
        "C:2",
    )
    assert rows == 0
    assert str(redelivered.turn_id) in dbos.enqueued


async def test_a_refounded_internal_arrival_remains_speakerless(db: None) -> None:
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    live = await admission.admit_member(workspace_id, conversation_id, "start", member_id, "C:1")
    folded = await _invoke(
        admission,
        workspace_id,
        conversation_id,
        agent_id,
        "<spawn_result …>",
        "subagent-result:1",
        holds_work_already_done=True,
    )
    assert folded == live.turn_id
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(
                status="failed",
                terminal=TerminalFrame(status="failed", error_class="Boom").model_dump(mode="json"),
                updated_at=sa.func.now(),
            )
            .where(tables.turn.c.id == live.turn_id)
        )

    refounded = await _invoke(
        admission,
        workspace_id,
        conversation_id,
        agent_id,
        "<spawn_result …>",
        "subagent-result:1",
        holds_work_already_done=True,
    )

    assert refounded is not None
    assert refounded != live.turn_id
    assert await _admission_stamp(refounded) == ("internal", None)
    assert await _turn_row(refounded) == ("queued", None)


async def test_fold_onto_a_parked_turn_dispatches_its_resume(db: None) -> None:
    """Caps were raised since the park: a fresh message folds onto the parked turn AND resumes it
    now, under a fresh workflow id, instead of leaving the member staring at the park notice."""
    workspace_id, member_id, _, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "one", member_id, "C:1")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(status="parked", updated_at=sa.func.now())
            .where(tables.turn.c.id == first.turn_id)
        )
    second = await admission.admit_member(workspace_id, conversation_id, "two", member_id, "C:2")
    assert second == Admitted(
        first.turn_id, opened_run=True, arrival_id=(await _queued_ids(conversation_id))[0]
    )
    assert await _queued_bodies(conversation_id) == ["two"]
    async with workspace_tx() as connection:
        resumed_status = (
            await connection.execute(
                sa.select(tables.turn.c.status).where(tables.turn.c.id == first.turn_id)
            )
        ).scalar_one()
    assert resumed_status == "queued"
    assert dbos.enqueued == [str(first.turn_id), str(first.turn_id)]
    assert dbos.workflow_ids[0] == str(first.turn_id)
    assert dbos.workflow_ids[1] != str(first.turn_id)


async def test_fold_onto_a_provider_park_waits_for_retry_at(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "one", member_id, "C:1")
    retry_at = datetime.now(UTC) + timedelta(hours=1)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(status="parked", retry_at=retry_at, updated_at=sa.func.now())
            .where(tables.turn.c.id == first.turn_id)
        )

    second = await admission.admit_member(workspace_id, conversation_id, "two", member_id, "C:2")

    assert second == Admitted(
        first.turn_id, opened_run=False, arrival_id=(await _queued_ids(conversation_id))[0]
    )
    assert await _queued_bodies(conversation_id) == ["two"]
    async with workspace_tx() as connection:
        status, stored_retry_at = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.retry_at).where(
                    tables.turn.c.id == first.turn_id
                )
            )
        ).one()
    assert status == "parked"
    assert stored_retry_at.replace(tzinfo=UTC) == retry_at
    assert dbos.enqueued == [str(first.turn_id)]


async def test_redelivery_of_a_fold_resumed_turn_retries_under_a_fresh_workflow_id(
    db: None,
) -> None:
    """The fold's own enqueue was deferred, so the redelivered message must retry the dispatch —
    but the run that parked the turn consumed its own workflow id, so a retry riding it would
    dedup against the completed workflow while re-stamping the offer, holding the sweep off a
    grace window at a time."""
    workspace_id, member_id, _, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "one", member_id, "C:1")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(
                running_attempt=uuid4().hex,
                dispatch_enqueued_at=None,
                updated_at=sa.func.now(),
            )
            .where(tables.turn.c.id == first.turn_id)
        )
    redelivered = await admission.admit_member(
        workspace_id, conversation_id, "one", member_id, "C:1"
    )
    assert redelivered == Admitted(first.turn_id, opened_run=False)
    assert dbos.enqueued == [str(first.turn_id), str(first.turn_id)]
    assert dbos.workflow_ids[0] == str(first.turn_id)
    assert dbos.workflow_ids[1] != str(first.turn_id)


async def _unseat(member_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member)
            .values(seated_at=None, updated_at=sa.func.now())
            .where(tables.member.c.id == member_id)
        )


async def _seat(member_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member)
            .values(seated_at=sa.func.now(), updated_at=sa.func.now())
            .where(tables.member.c.id == member_id)
        )


async def _turn_row(turn_id: UUID) -> tuple[str, str | None]:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.terminal).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).one()
    text = None if row.terminal is None else TerminalFrame.model_validate(row.terminal).text
    return row.status, text


async def test_unseated_speaker_is_refused_with_the_seat_message(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    await _unseat(member_id)
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset({"cli"}))
    turn_id = (await admission.admit_member(workspace_id, conversation_id, "hi", member_id)).turn_id
    assert dbos.enqueued == []
    status, text = await _turn_row(turn_id)
    assert status == "cancelled"
    assert text == SEAT_REFUSAL_MESSAGE
    async with workspace_tx() as connection:
        writebacks = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.writeback)
                .where(tables.writeback.c.turn_id == turn_id)
            )
        ).scalar_one()
    assert writebacks == 1


async def test_a_surface_comment_is_recorded_once_and_published_to_the_live_thread(
    db: None,
) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    hub = InProcessHub()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset(), hub=hub)
    opened = await admission.admit_member(workspace_id, conversation_id, "first", member_id)
    comment = "You [commented](https://ufo.test/surface/web#/c/thread): follow up"

    admitted = await admission.admit_member(
        workspace_id,
        conversation_id,
        "follow up",
        member_id,
        idempotency_key="web-comment-1",
        comment=comment,
    )
    redelivered = await admission.admit_member(
        workspace_id,
        conversation_id,
        "follow up",
        member_id,
        idempotency_key="web-comment-1",
        comment=comment,
    )

    assert admitted.turn_id == opened.turn_id
    assert admitted.arrival_id is not None
    assert admitted.comment_id is not None
    assert redelivered.comment_id is None
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.mid_turn_reply.c.id,
                    tables.mid_turn_reply.c.round_index,
                    tables.mid_turn_reply.c.message_ref,
                    tables.mid_turn_reply.c.text,
                ).where(tables.mid_turn_reply.c.turn_id == opened.turn_id)
            )
        ).all()
    assert [(row.id, row.round_index, row.message_ref, row.text) for row in rows] == [
        (admitted.comment_id, SURFACE_COMMENT_ROUND_INDEX, admitted.arrival_id, comment)
    ]
    stream = hub.subscribe(opened.turn_id)
    _arrival_cursor, arrival = await anext(stream)
    _reply_cursor, frame = await anext(stream)
    await stream.aclose()
    assert arrival == ArrivalQueued(arrival_id=admitted.arrival_id)
    assert frame == Reply(
        id=admitted.comment_id,
        message_ref=admitted.arrival_id,
        text=comment,
        is_comment=True,
    )


async def test_unseated_speakers_message_does_not_fold_into_a_live_turn(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "first", member_id)
    await _unseat(member_id)
    second = await admission.admit_member(workspace_id, conversation_id, "second", member_id)
    assert second.turn_id != first.turn_id
    status, text = await _turn_row(second.turn_id)
    assert status == "cancelled"
    assert text == SEAT_REFUSAL_MESSAGE
    first_status, _ = await _turn_row(first.turn_id)
    assert first_status == "queued"
    assert await _queued_bodies(conversation_id) == []


async def test_seated_reply_does_not_resume_a_seat_blocked_parked_turn(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    second_member = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=second_member,
                workspace_id=workspace_id,
                email="second@example.com",
                seated_at=sa.func.now(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "first", member_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(status="parked", updated_at=sa.func.now())
            .where(tables.turn.c.id == first.turn_id)
        )
        await connection.execute(
            sa.update(tables.member)
            .values(seated_at=None, updated_at=sa.func.now())
            .where(tables.member.c.id == member_id)
        )
    second = await admission.admit_member(workspace_id, conversation_id, "second", second_member)
    assert second.turn_id != first.turn_id
    first_status, _ = await _turn_row(first.turn_id)
    assert first_status == "parked"
    second_status, _ = await _turn_row(second.turn_id)
    assert second_status == "queued"
    assert await _queued_bodies(conversation_id) == []


async def test_seated_reply_does_not_resume_an_aggregate_with_an_unseated_speaker(
    db: None,
) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    unseated = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=unseated,
                workspace_id=workspace_id,
                email="unseated@example.com",
                seated_at=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "first", member_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(status="parked", updated_at=sa.func.now())
            .where(tables.turn.c.id == first.turn_id)
        )
        await connection.execute(
            sa.insert(tables.inbound_message).values(
                id=uuid4(),
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                seq=1,
                body="pending",
                admission_source="member",
                speaker_member_id=unseated,
                admitted_turn_id=first.turn_id,
                created_at=sa.func.now(),
            )
        )

    second = await admission.admit_member(workspace_id, conversation_id, "resume", member_id)

    assert second.turn_id != first.turn_id
    first_status, _ = await _turn_row(first.turn_id)
    second_status, _ = await _turn_row(second.turn_id)
    assert first_status == "parked"
    assert second_status == "queued"
    assert await _queued_bodies(conversation_id) == ["pending"]


async def test_speakerless_member_message_is_refused(db: None) -> None:
    """A member surface that could not name its speaker is answering a stranger, whatever else is
    true of the workspace: there is no seat bound left to make this conditional, and there never
    was a deploy where a speaker the surface failed to resolve was somebody the agent should
    answer in a conversation carrying the workspace's own audience."""
    workspace_id, _member_id, _, conversation_id = await _seed()
    dbos = StubDbos()
    turn_id = (
        await Admission(dbos=dbos, durable_surfaces=frozenset()).admit_member(
            workspace_id, conversation_id, "who am i", None
        )
    ).turn_id
    assert dbos.enqueued == []
    status, text = await _turn_row(turn_id)
    assert status == "cancelled"
    assert text == UNRESOLVED_SPEAKER_MESSAGE


async def test_speakerless_member_message_does_not_fold(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "first", member_id)
    ghost = await admission.admit_member(workspace_id, conversation_id, "psst", None)
    assert ghost.turn_id != first.turn_id
    status, text = await _turn_row(ghost.turn_id)
    assert status == "cancelled"
    assert text == UNRESOLVED_SPEAKER_MESSAGE
    assert await _queued_bodies(conversation_id) == []


async def test_a_reject_cap_holds_a_turn_carrying_work_already_paid_for(db: None) -> None:
    """A member's next message can be turned away — they read why and decide what to do. A turn
    carrying a finished subagent's result holds work the ledger already booked, and cancelling it
    discards that output with nobody to tell: the member paid for the run and would never hear it.
    So the same reject cap that cancels an ordinary invoke parks this one, for the dispatcher to
    release when the cap is raised or its window rolls."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "one", member_id, "C:1")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.spend_cap).values(
                id=uuid4(),
                workspace_id=workspace_id,
                scope="workspace",
                subject_id=None,
                window_seconds=3600,
                limit_micro_usd=1,
                on_breach="reject",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.ledger).values(
                id=uuid4(),
                workspace_id=workspace_id,
                turn_id=first.turn_id,
                dimension="tokens",
                amount=10,
                prompt_tokens=10,
                input_tokens=10,
                priced_micro_usd=100,
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )

    turned_away = await _invoke(
        admission,
        workspace_id,
        conversation_id,
        agent_id,
        "ordinary",
    )
    delivered = await _invoke(
        admission,
        workspace_id,
        conversation_id,
        agent_id,
        "<spawn_result …>",
        "subagent-result:held",
        holds_work_already_done=True,
    )

    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.turn.c.id, tables.turn.c.status, tables.turn.c.terminal).where(
                    tables.turn.c.id.in_([turned_away, delivered])
                )
            )
        ).all()
    state = {row.id: (row.status, row.terminal) for row in rows}
    assert state[turned_away][0] == "cancelled"
    assert state[delivered] == ("parked", None)


async def _dispatch_stamp(turn_id: UUID) -> datetime | None:
    """When the turn was placed on the queue, if it has been — an unstamped queued turn is one the
    next offer picks up."""
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.turn.c.dispatch_enqueued_at).where(tables.turn.c.id == turn_id)
            )
        ).scalar_one()


async def _admission_stamp(turn_id: UUID) -> tuple[str, UUID | None]:
    """What the turn says about who admitted it: its source and speaker."""
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.turn.c.admission_source,
                    tables.turn.c.speaker_member_id,
                ).where(tables.turn.c.id == turn_id)
            )
        ).one()
    return row.admission_source, row.speaker_member_id


async def test_an_as_scheduled_fire_founds_its_own_turn_beside_a_live_one(db: None) -> None:
    """A fire asserting the scheduled meaning gets it without a scheduled row: its own turn while
    another runs, stamped scheduled and speakerless."""
    workspace_id, _member_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    live = await _invoke(admission, workspace_id, conversation_id, agent_id, "background job")
    assert live is not None
    assert await _claim_turn(live, str(live))

    fired = await _invoke(
        admission,
        workspace_id,
        conversation_id,
        agent_id,
        "run the report",
        "task:fire-1",
        as_scheduled=True,
    )

    assert fired is not None
    assert fired != live
    assert await _turn_count(conversation_id) == 2
    assert await _queued_bodies(conversation_id) == []
    assert await _admission_stamp(fired) == ("scheduled", None)
    status, _ = await _turn_row(fired)
    assert status == "queued"
    assert str(fired) in dbos.enqueued


async def test_an_internal_arrival_folds_without_a_principal_partition(db: None) -> None:
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    live = (
        await admission.admit_member(workspace_id, conversation_id, "start", member_id, "C:1")
    ).turn_id
    assert await _claim_turn(live, str(live))

    arrived = await _invoke(
        admission,
        workspace_id,
        conversation_id,
        agent_id,
        "<result …>",
        "arrival:1",
        holds_work_already_done=True,
    )

    assert arrived == live
    assert await _turn_count(conversation_id) == 1
    assert await _queued_bodies(conversation_id) == ["<result …>"]
    assert dbos.enqueued == [str(live)]


async def test_automatic_turns_do_not_execute_as_a_member(db: None) -> None:
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    await _unseat(member_id)
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())

    admitted = await _invoke(
        admission,
        workspace_id,
        conversation_id,
        agent_id,
        "run the report",
        "task:fire-1",
        as_scheduled=True,
    )
    assert admitted is not None
    assert await _turn_row(admitted) == ("queued", None)
    assert await _admission_stamp(admitted) == ("scheduled", None)
    assert dbos.enqueued == [str(admitted)]


async def test_a_member_turn_past_the_armed_seq_supersedes_a_fire(db: None) -> None:
    """The member got there first, so the fire is refused and writes nothing — no turn to answer
    and no arrival waiting for one."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    spoke = await admission.admit_member(workspace_id, conversation_id, "never mind", member_id)

    superseded = await _invoke(
        admission,
        workspace_id,
        conversation_id,
        agent_id,
        "resume the plan",
        "pause:1",
        unless_member_since=0,
        unless_member_arrival_since=0,
    )

    assert superseded is None
    assert await _turn_count(conversation_id) == 1
    assert await _queued_bodies(conversation_id) == []
    assert dbos.enqueued == [str(spoke.turn_id)]


async def test_a_refused_message_does_not_supersede_a_fire(db: None) -> None:
    """A turn this gate refused never joined the conversation: nothing will answer it, so it cannot
    end the wait the fire belongs to. Otherwise a member whose seat an admin removed could kill a
    paused workflow by typing once, and a stranger the surface could not resolve could do it too —
    the work would hang with nobody left to resume it."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    await _unseat(member_id)
    refused = await admission.admit_member(workspace_id, conversation_id, "let me in", member_id)
    stranger = await admission.admit_member(workspace_id, conversation_id, "who am i", None)
    assert await _turn_row(refused.turn_id) == ("cancelled", SEAT_REFUSAL_MESSAGE)
    assert await _turn_row(stranger.turn_id) == ("cancelled", UNRESOLVED_SPEAKER_MESSAGE)

    fired = await _invoke(
        admission,
        workspace_id,
        conversation_id,
        agent_id,
        "resume the plan",
        "pause:1",
        unless_member_since=0,
        unless_member_arrival_since=0,
    )

    assert fired is not None
    status, _ = await _turn_row(fired)
    assert status == "queued"


async def _drain_arrivals(turn_id: UUID) -> None:
    """What the engine's drain leaves behind: the pending rows stamped consumed by the turn that
    absorbed them."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.inbound_message)
            .values(consumed_turn_id=turn_id)
            .where(tables.inbound_message.c.consumed_turn_id.is_(None))
        )


async def test_a_member_message_the_live_turn_absorbed_supersedes_a_fire(db: None) -> None:
    """The fold window. A member message arriving while the arming turn is still live founds no turn
    of its own — it lands on that turn's queue — and stops being queued the moment the engine drains
    it. Read only as a turn or as a pending row, the member would have spoken and left no trace by
    the time the timer came due, and the agent would resume a workflow the member already resumed.
    The trace is the turn the message folded into."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    arming = await _invoke(admission, workspace_id, conversation_id, agent_id, "the arming work")
    assert arming is not None
    folded = await admission.admit_member(
        workspace_id, conversation_id, "actually, do this instead", member_id
    )
    assert folded.turn_id == arming
    await _drain_arrivals(arming)

    superseded = await _invoke(
        admission,
        workspace_id,
        conversation_id,
        agent_id,
        "resume the plan",
        "pause:1",
        unless_member_since=1,
        unless_member_arrival_since=0,
    )

    assert superseded is None
    assert await _turn_count(conversation_id) == 1


async def test_a_queued_member_message_supersedes_a_fire(db: None) -> None:
    """A member message already waiting for the live turn is a resume in flight, whatever the turn
    sequence says."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    live = await _invoke(admission, workspace_id, conversation_id, agent_id, "internal work")
    assert live is not None
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.inbound_message).values(
                id=uuid4(),
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                seq=1,
                body="waiting on you",
                admission_source="member",
                speaker_member_id=member_id,
                admitted_turn_id=live,
                created_at=sa.func.now(),
            )
        )

    superseded = await _invoke(
        admission,
        workspace_id,
        conversation_id,
        agent_id,
        "resume the plan",
        "pause:1",
        unless_member_since=1,
        unless_member_arrival_since=0,
    )

    assert superseded is None
    assert await _turn_count(conversation_id) == 1
    assert await _queued_bodies(conversation_id) == ["waiting on you"]


async def test_a_quiet_conversation_admits_a_guarded_fire(db: None) -> None:
    workspace_id, _member_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    fired = await _invoke(
        Admission(dbos=dbos, durable_surfaces=frozenset()),
        workspace_id,
        conversation_id,
        agent_id,
        "resume the plan",
        "pause:1",
        unless_member_since=0,
        unless_member_arrival_since=0,
    )

    assert fired is not None
    status, _ = await _turn_row(fired)
    assert status == "queued"
    assert dbos.enqueued == [str(fired)]


async def _cancel_turn_row(turn_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(
                status="cancelled",
                terminal=TerminalFrame(status="cancelled").model_dump(mode="json"),
                updated_at=sa.func.now(),
            )
            .where(tables.turn.c.id == turn_id)
        )


async def test_redispatch_founds_a_run_on_the_oldest_pending_member_arrival(db: None) -> None:
    """The founded run keeps the pin the ended turn ran under: a fold already required the
    follow-up to match it, and nothing else could restore it once core founds the turn."""
    workspace_id, member_id, _, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    pinned = TurnRuntimeConfig(model="claude-opus-4-8", internet_access=False)
    first = await admission.admit_member(
        workspace_id, conversation_id, "start", member_id, runtime_config=pinned
    )
    followup = await admission.admit_member(
        workspace_id,
        conversation_id,
        "follow up",
        member_id,
        idempotency_key="send-1",
        runtime_config=pinned,
    )
    assert not followup.opened_run
    assert followup.arrival_id is not None
    await _cancel_turn_row(first.turn_id)

    new_turn_id = await admission.redispatch(workspace_id, conversation_id, first.turn_id)

    assert new_turn_id is not None
    assert new_turn_id != first.turn_id
    status, _ = await _turn_row(new_turn_id)
    assert status == "queued"
    assert str(new_turn_id) in dbos.enqueued
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.turn.c.inbound,
                    tables.turn.c.speaker_member_id,
                    tables.turn.c.runtime_config,
                ).where(tables.turn.c.id == new_turn_id)
            )
        ).one()
    assert row.inbound == "follow up"
    assert row.speaker_member_id == member_id
    assert TurnRuntimeConfig.model_validate(row.runtime_config) == pinned
    assert await _queued_bodies(conversation_id) == []
    assert await admission.redispatch(workspace_id, conversation_id, first.turn_id) is None


async def test_redispatch_with_nothing_pending_is_none(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "start", member_id)
    await _cancel_turn_row(first.turn_id)
    assert await admission.redispatch(workspace_id, conversation_id, first.turn_id) is None


@dataclass
class _RecordingHub:
    frames: list[tuple[UUID, object]] = field(default_factory=list)

    async def publish(self, turn_id: UUID, frame: object) -> str:
        self.frames.append((turn_id, frame))
        return str(len(self.frames))


async def test_redispatch_leaves_a_root_turns_internal_arrival_for_the_next_turn(
    db: None,
) -> None:
    """A child's result folded into a root turn is nobody's pending message: re-founding a turn on
    it after a stop would resume the work the member just stopped, so it waits for the
    conversation's next turn, whose first drain claims it."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    dbos, hub = StubDbos(), _RecordingHub()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset(), hub=hub)
    first = await admission.admit_member(workspace_id, conversation_id, "start", member_id)
    await _invoke(
        admission,
        workspace_id,
        conversation_id,
        agent_id,
        "child result",
        "subagent-result:child",
    )
    await _cancel_turn_row(first.turn_id)

    assert await admission.redispatch(workspace_id, conversation_id, first.turn_id) is None

    assert await _queued_bodies(conversation_id) == ["child result"]
    assert dbos.enqueued == [str(first.turn_id)]
    assert [(turn_id, type(frame)) for turn_id, frame in hub.frames] == [
        (first.turn_id, ArrivalQueued)
    ]


async def _spawned_conversation(
    workspace_id: UUID, member_id: UUID, agent_id: UUID, status: str
) -> tuple[UUID, UUID, UUID, TurnRuntimeConfig]:
    """A child conversation whose founding turn ended `status` with a parent's follow-up still
    pending on it, as a fold followed by a failure or a cancel leaves things."""
    child_conversation, child_id, parent_id = uuid4(), uuid4(), uuid4()
    pinned = TurnRuntimeConfig(model="claude-opus-4-8", internet_access=False)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=child_conversation,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="subagent",
                queue_key=str(child_id),
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=child_id,
                workspace_id=workspace_id,
                conversation_id=child_conversation,
                agent_id=agent_id,
                seq=1,
                status=status,
                inbound="{}",
                terminal=TerminalFrame(status=status, text="over").model_dump(mode="json"),
                parent_turn_id=parent_id,
                subagent_profile="general-purpose",
                runtime_config=pinned.model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.inbound_message).values(
                id=uuid4(),
                workspace_id=workspace_id,
                conversation_id=child_conversation,
                seq=1,
                body="narrow it to staging",
                admission_source="internal",
                speaker_member_id=None,
                idempotency_key="turn-1/message_spawn/call-1",
                admitted_turn_id=child_id,
                created_at=sa.func.now(),
            )
        )
    return child_conversation, child_id, parent_id, pinned


async def test_redispatch_readmits_a_failed_spawned_turns_followup_under_its_own_identity(
    db: None,
) -> None:
    """A parent's follow-up folded into a child whose turn then failed is re-admitted as the
    child's next turn: the row records no speaker, inherits the runtime config and spawn identity
    every founded turn on that conversation carries,
    and announces no `Absorbed` frame, which names a member's own message."""
    workspace_id, member_id, agent_id, _ = await _seed()
    child_conversation, child_id, parent_id, pinned = await _spawned_conversation(
        workspace_id, member_id, agent_id, "failed"
    )
    dbos, hub = StubDbos(), _RecordingHub()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset(), hub=hub)

    founded = await admission.redispatch(workspace_id, child_conversation, child_id)

    assert founded is not None
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.turn.c.seq,
                    tables.turn.c.status,
                    tables.turn.c.inbound,
                    tables.turn.c.idempotency_key,
                    tables.turn.c.admission_source,
                    tables.turn.c.subagent_profile,
                    tables.turn.c.parent_turn_id,
                    tables.turn.c.speaker_member_id,
                    tables.turn.c.result_delivery,
                    tables.turn.c.runtime_config,
                ).where(tables.turn.c.id == founded)
            )
        ).one()
    assert (row.seq, row.status, row.inbound) == (2, "queued", "narrow it to staging")
    assert (row.idempotency_key, row.admission_source) == (
        "turn-1/message_spawn/call-1",
        "internal",
    )
    assert (row.subagent_profile, row.parent_turn_id) == ("general-purpose", parent_id)
    assert row.speaker_member_id is None
    assert row.result_delivery == "pending"
    assert TurnRuntimeConfig.model_validate(row.runtime_config) == pinned
    assert dbos.enqueued == [str(founded)]
    assert await _queued_bodies(child_conversation) == []
    assert hub.frames == []


async def test_redispatch_leaves_a_cancelled_spawned_turns_followup_alone(db: None) -> None:
    """A cancel ends the child's work: the parent that sent the follow-up cancelled it, or was
    cancelled with it, so re-founding the child on the follow-up would run cancelled work."""
    workspace_id, member_id, agent_id, _ = await _seed()
    child_conversation, child_id, _, _ = await _spawned_conversation(
        workspace_id, member_id, agent_id, "cancelled"
    )
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())

    assert await admission.redispatch(workspace_id, child_conversation, child_id) is None

    assert await _turn_count(child_conversation) == 1
    assert await _queued_bodies(child_conversation) == ["narrow it to staging"]
    assert dbos.enqueued == []


async def test_member_admission_stores_its_trace_for_the_turn_span(db: None, monkeypatch) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(o11y.trace, "get_tracer", provider.get_tracer)
    workspace_id, member_id, _, conversation_id = await _seed()
    admitted = await Admission(dbos=StubDbos(), durable_surfaces=frozenset()).admit_member(
        workspace_id, conversation_id, "hi", member_id
    )
    async with workspace_tx() as connection:
        stored = (
            await connection.execute(
                sa.select(tables.turn.c.traceparent).where(tables.turn.c.id == admitted.turn_id)
            )
        ).scalar_one()
    admission = next(s for s in exporter.get_finished_spans() if s.name == "admission")
    assert stored.split("-")[1] == format(admission.context.trace_id, "032x")


async def test_an_archived_app_refuses_a_member_and_raises_for_work_fired_by_a_clock(
    db: None,
) -> None:
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .values(
                name=f"~archived-{agent_id}",
                archived_name=tables.agent.c.name,
                archived_at=sa.func.now(),
            )
            .where(tables.agent.c.id == agent_id)
        )
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset({"cli"}))

    admitted = await admission.admit_member(workspace_id, conversation_id, "hi", member_id)

    status, text = await _turn_row(admitted.turn_id)
    assert (status, text) == ("cancelled", ARCHIVED_REFUSAL_MESSAGE)
    assert dbos.enqueued == []
    async with workspace_tx() as connection:
        writebacks = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.writeback)
                .where(tables.writeback.c.turn_id == admitted.turn_id)
            )
        ).scalar_one()
    assert writebacks == 1

    with pytest.raises(AgentArchived):
        await _invoke(admission, workspace_id, conversation_id, agent_id, "scheduled fire")
    assert dbos.enqueued == []
    assert await _turn_count(conversation_id) == 1


async def test_an_admitted_internal_turn_replays_after_its_agent_is_archived(db: None) -> None:
    workspace_id, _, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    admitted = await _invoke(
        admission,
        workspace_id,
        conversation_id,
        agent_id,
        "scheduled fire",
        idempotency_key="fire-1",
        as_scheduled=True,
    )
    assert admitted is not None
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .values(
                name=f"~archived-{agent_id}",
                archived_name=tables.agent.c.name,
                archived_at=sa.func.now(),
            )
            .where(tables.agent.c.id == agent_id)
        )

    replayed = await _invoke(
        admission,
        workspace_id,
        conversation_id,
        agent_id,
        "scheduled fire",
        idempotency_key="fire-1",
        as_scheduled=True,
    )

    assert replayed == admitted
    assert await _turn_count(conversation_id) == 1


async def test_a_message_left_by_a_stop_is_refused_by_an_app_archived_under_it(db: None) -> None:
    """A re-admission holds every refusal a return can release, but an archive releases nothing:
    a turn held under it would run on the archived app at the next sweep, so this one cancels."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    running = await admission.admit_member(workspace_id, conversation_id, "do the thing", member_id)
    await admission.admit_member(
        workspace_id, conversation_id, "and this too", member_id, idempotency_key="send-1"
    )
    await _cancel_turn_row(running.turn_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .values(
                name=f"~archived-{agent_id}",
                archived_name=tables.agent.c.name,
                archived_at=sa.func.now(),
            )
            .where(tables.agent.c.id == agent_id)
        )

    founded = await admission.redispatch(workspace_id, conversation_id, running.turn_id)

    assert founded is None
    async with workspace_tx() as connection:
        newest = (
            await connection.execute(
                sa.select(tables.turn.c.id)
                .where(tables.turn.c.conversation_id == conversation_id)
                .order_by(tables.turn.c.seq.desc())
                .limit(1)
            )
        ).scalar_one()
    assert await _turn_row(newest) == ("cancelled", ARCHIVED_REFUSAL_MESSAGE)


async def test_redispatch_keeps_a_pinned_followup_separate_from_a_live_sibling(
    db: None,
) -> None:
    """A stop with a scheduled turn standing beside the stopped one keeps the member's pinned
    follow-up queued under its own runtime config."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    dbos, hub = StubDbos(), _RecordingHub()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset(), hub=hub)
    pinned = TurnRuntimeConfig(model="claude-opus-4-8", internet_access=False)
    first = await admission.admit_member(
        workspace_id, conversation_id, "start", member_id, runtime_config=pinned
    )
    followup = await admission.admit_member(
        workspace_id,
        conversation_id,
        "follow up",
        member_id,
        idempotency_key="send-1",
        runtime_config=pinned,
    )
    scheduled = await _invoke(
        admission,
        workspace_id,
        conversation_id,
        agent_id,
        "scheduled fire",
        "fire-1",
        as_scheduled=True,
    )
    await _cancel_turn_row(first.turn_id)

    founded = await admission.redispatch(workspace_id, conversation_id, first.turn_id)

    assert founded is not None
    assert founded not in (first.turn_id, scheduled)
    assert await _turn_count(conversation_id) == 3
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.runtime_config).where(
                    tables.turn.c.id == founded
                )
            )
        ).one()
    assert row.status == "queued"
    assert TurnRuntimeConfig.model_validate(row.runtime_config) == pinned
    assert await _queued_bodies(conversation_id) == []
    assert [turn_id for turn_id, frame in hub.frames if isinstance(frame, ArrivalQueued)] == [
        first.turn_id
    ]
    assert [
        (turn_id, frame.arrivals) for turn_id, frame in hub.frames if isinstance(frame, Absorbed)
    ] == [(founded, (followup.arrival_id,))]


async def test_a_message_to_an_archived_app_is_refused_beside_a_live_turn_rather_than_folded(
    db: None,
) -> None:
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    live = await admission.admit_member(workspace_id, conversation_id, "do the thing", member_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .values(
                name=f"~archived-{agent_id}",
                archived_name=tables.agent.c.name,
                archived_at=sa.func.now(),
            )
            .where(tables.agent.c.id == agent_id)
        )

    second = await admission.admit_member(workspace_id, conversation_id, "and this too", member_id)

    assert second.turn_id != live.turn_id
    assert second.arrival_id is None
    assert await _turn_row(second.turn_id) == ("cancelled", ARCHIVED_REFUSAL_MESSAGE)
    assert await _turn_row(live.turn_id) == ("queued", None)
    assert dbos.enqueued == [str(live.turn_id)]


class _TitleFailed(RuntimeError):
    """Stands in for any statement after the turn insert, or the commit itself, failing."""


def _refuse_title(body: str) -> str:
    raise _TitleFailed(body)


def _in_memory_metrics(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    monkeypatch.setattr(o11y.metrics, "get_meter", MeterProvider(metric_readers=[reader]).get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    monkeypatch.setattr(o11y, "_histograms", {})
    return reader


def _admitted_points(reader: InMemoryMetricReader) -> list[tuple[str, str, int]]:
    """A reader that collected nothing at all answers None rather than an empty reading."""
    data = reader.get_metrics_data()
    if data is None:
        return []
    return [
        (point.attributes["surface"], point.attributes["admission_source"], point.value)
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == f"ufo.{ADMITTED_TURN_METRIC}"
        for point in metric.data.data_points
    ]


async def test_an_admitted_turn_is_counted_under_its_surface_and_source(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The product board reads chat volume off this counter, so it counts the turn row rather than
    the call: a message that folds into a turn already running adds no row and must add no count,
    or every mid-turn interjection reads as another conversation."""
    workspace_id, member_id, _agent_id, conversation_id = await _seed()
    reader = _in_memory_metrics(monkeypatch)
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())

    await admission.admit_member(workspace_id, conversation_id, "first", member_id)
    await admission.admit_member(workspace_id, conversation_id, "folded", member_id)

    assert _admitted_points(reader) == [("cli", "member", 1)]
    assert await _turn_count(conversation_id) == 1


async def test_an_admission_that_rolls_back_counts_nothing_and_its_retry_counts_once(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The count is taken off the committed row, not beside the insert. A statement after the insert
    or the commit itself can fail, and the surface then retries under the same idempotency key with
    no committed turn to dedupe against: counted inside the transaction, the failed attempt would
    add a turn that never existed and the retry would add the same turn again."""
    workspace_id, member_id, _agent_id, conversation_id = await _seed()
    reader = _in_memory_metrics(monkeypatch)
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    monkeypatch.setattr("ufo.runtime.surfaces.admission.conversation_name", _refuse_title)

    with pytest.raises(_TitleFailed):
        await admission.admit_member(
            workspace_id, conversation_id, "first", member_id, idempotency_key="send-1"
        )

    assert await _turn_count(conversation_id) == 0
    assert _admitted_points(reader) == []

    monkeypatch.setattr("ufo.runtime.surfaces.admission.conversation_name", conversation_name)
    await admission.admit_member(
        workspace_id, conversation_id, "first", member_id, idempotency_key="send-1"
    )

    assert _admitted_points(reader) == [("cli", "member", 1)]
    assert await _turn_count(conversation_id) == 1


async def _reply_reaches(turn_id: UUID) -> str | None:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.context).where(tables.turn.c.id == turn_id)
            )
        ).scalar_one()
    return None if row is None else row.get("reply_reaches")


async def test_admission_stamps_where_a_turn_reply_lands(db: None) -> None:
    """A turn cannot work out for itself whether its answer reaches anyone: a background turn on an
    extension's own conversation writes into a room nobody watches, and reads exactly like one a
    member is waiting on. Admission knows, so it stamps where the reply lands — the surface, when a
    member spoke there or the surface posts, and `nobody` otherwise."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    posts = Admission(dbos=StubDbos(), durable_surfaces=frozenset({"slack"}))
    spoken = await posts.admit_member(workspace_id, conversation_id, "hello", member_id)

    quiet_conversation = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=quiet_conversation,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="sources",
                queue_key="source-trigger:probe",
                member_id=member_id,
                audience=str(conversation_audience(member_id)),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    quiet = Admission(dbos=StubDbos(), durable_surfaces=frozenset({"slack"}))
    background = await quiet.invoke(
        workspace_id,
        quiet_conversation,
        agent_id,
        "the review found two blocking defects",
        "probe-key",
    )

    assert await _reply_reaches(spoken.turn_id) == "cli"
    assert background is not None
    assert await _reply_reaches(background) == REPLY_REACHES_NOBODY


async def test_a_slack_arrival_clears_the_archive_mark(db: None) -> None:
    """Archiving is broken by the message, not by the screen that drew the archive: a member who
    filed a conversation away and then answers it in Slack finds it back in the list. Admission
    holds the conversation row locked for every surface, so the mark is cleared there and every
    surface's arrival breaks it."""
    workspace_id, member_id, agent_id, _conversation_id = await _seed()
    slack_conversation = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=slack_conversation,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="slack",
                queue_key="slack/C1/thread",
                member_id=member_id,
                audience=str(conversation_audience(member_id)),
                archived_at=sa.func.now(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset({"slack"}))

    await admission.admit_member(workspace_id, slack_conversation, "any news?", member_id)

    async with workspace_tx() as connection:
        archived_at = (
            await connection.execute(
                sa.select(tables.conversation.c.archived_at).where(
                    tables.conversation.c.id == slack_conversation
                )
            )
        ).scalar_one()
    assert archived_at is None
    assert await _turn_count(slack_conversation) == 1


async def test_an_internal_turn_leaves_the_archive_mark(db: None) -> None:
    """The contract is a member's message, not any arrival: a scheduled fire reports into the
    conversation it files its lane under, and that report must not unfile it — the member put the
    automation lane away, and only their own word brings it back."""
    workspace_id, member_id, agent_id, _conversation_id = await _seed()
    task_lane = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=task_lane,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key="scheduled/task-lane",
                member_id=member_id,
                audience=str(conversation_audience(member_id)),
                archived_at=sa.func.now(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset({"web"}))

    await admission.invoke(
        workspace_id,
        task_lane,
        agent_id,
        "the 09:00 sweep found nothing",
        as_scheduled=True,
    )

    async with workspace_tx() as connection:
        archived_at = (
            await connection.execute(
                sa.select(tables.conversation.c.archived_at).where(
                    tables.conversation.c.id == task_lane
                )
            )
        ).scalar_one()
    assert archived_at is not None
    assert await _turn_count(task_lane) == 1


async def test_a_turn_records_the_member_it_acts_for(db: None) -> None:
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    spoken = await admission.admit_member(workspace_id, conversation_id, "hi", member_id)
    fired = await admission.invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "fire",
        "task:1",
        as_scheduled=True,
        acting_member_id=member_id,
    )
    unattributed = await admission.invoke(
        workspace_id, conversation_id, agent_id, "notice", "notice:1", as_scheduled=True
    )
    async with workspace_tx() as connection:
        rows = {
            row.id: row
            for row in (
                await connection.execute(
                    sa.select(
                        tables.turn.c.id, tables.turn.c.member_id, tables.turn.c.speaker_member_id
                    ).where(tables.turn.c.conversation_id == conversation_id)
                )
            ).all()
        }
    assert fired is not None and unattributed is not None
    assert (rows[fired].member_id, rows[fired].speaker_member_id) == (member_id, None)
    assert (rows[unattributed].member_id, rows[unattributed].speaker_member_id) == (None, None)
    assert (rows[spoken.turn_id].member_id, rows[spoken.turn_id].speaker_member_id) == (
        member_id,
        member_id,
    )
