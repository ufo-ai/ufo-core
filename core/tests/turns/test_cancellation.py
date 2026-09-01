from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from ufo.db import owner_tx, workspace_tx
from ufo.harness import o11y
from ufo.runtime.object_name import ObjectRef
from ufo.runtime.turns.cancellation import cancel_one_turn
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import TerminalFrame


@dataclass
class _RecordingClient:
    """Records each turn id a cancel targets, so a test reads back what the primitive asked DBOS to
    cancel — never asserting DBOS itself. cancel_workflow_async is a no-op here, matching the real
    client's conditional-update behavior for an absent or already-complete workflow."""

    cancelled: list[str] = field(default_factory=list)

    async def cancel_workflow_async(self, workflow_id: str) -> None:
        self.cancelled.append(workflow_id)


async def _workspace_agent() -> tuple[UUID, UUID]:
    workspace_id, agent_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="a",
                prompt="p",
                model="m",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id


async def _turn(
    workspace_id: UUID, agent_id: UUID, status: str, subagent_profile: str | None = None
) -> UUID:
    conversation_id, turn_id = uuid4(), uuid4()
    terminal = None if status in ("queued", "running", "parked") else TerminalFrame(status=status)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="subagent",
                queue_key=str(turn_id),
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status=status,
                inbound="x",
                subagent_profile=subagent_profile,
                terminal=None if terminal is None else terminal.model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn_id


async def _status(turn_id: UUID) -> str:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.turn.c.status).where(tables.turn.c.id == turn_id)
            )
        ).scalar_one()


async def test_cancel_one_turn_cancels_the_workflow_then_commits_the_terminal(db: None) -> None:
    """A live turn: cancel_one_turn asks DBOS to cancel the workflow and commits a cancelled
    terminal, returning True."""
    workspace_id, agent_id = await _workspace_agent()
    turn_id = await _turn(workspace_id, agent_id, "running")
    client = _RecordingClient()
    assert await cancel_one_turn(client, turn_id) is not None
    assert client.cancelled == [str(turn_id)]
    assert await _status(turn_id) == "cancelled"
    async with workspace_tx() as connection:
        terminal = (
            await connection.execute(
                sa.select(tables.turn.c.terminal).where(tables.turn.c.id == turn_id)
            )
        ).scalar_one()
    assert TerminalFrame.model_validate(terminal).status == "cancelled"


async def test_cancel_one_turn_targets_the_live_redispatch_attempt(db: None) -> None:
    workspace_id, agent_id = await _workspace_agent()
    turn_id = await _turn(workspace_id, agent_id, "running")
    attempt = uuid4().hex
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(running_attempt=attempt)
            .where(tables.turn.c.id == turn_id)
        )
    client = _RecordingClient()

    assert await cancel_one_turn(client, turn_id) is not None

    assert client.cancelled == [attempt]
    assert await _status(turn_id) == "cancelled"


async def test_cancel_one_turn_follows_a_claim_that_races_the_cancel(db: None) -> None:
    workspace_id, agent_id = await _workspace_agent()
    turn_id = await _turn(workspace_id, agent_id, "queued")
    claimed_attempt = uuid4().hex

    @dataclass
    class _ClaimingClient:
        cancelled: list[str] = field(default_factory=list)

        async def cancel_workflow_async(self, workflow_id: str) -> None:
            self.cancelled.append(workflow_id)
            if len(self.cancelled) != 1:
                return
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.turn)
                    .values(status="running", running_attempt=claimed_attempt)
                    .where(tables.turn.c.id == turn_id)
                )

    client = _ClaimingClient()

    assert await cancel_one_turn(client, turn_id) is not None

    assert client.cancelled == [str(turn_id), claimed_attempt]
    assert await _status(turn_id) == "cancelled"


async def test_cancel_one_turn_names_what_the_turn_already_created(db: None) -> None:
    """The turn row records its creations the round they happen, so the cancelled terminal — the
    one frame the turn's own execution never writes — still names them, on the committed row and
    on the frame the stopper publishes."""
    workspace_id, agent_id = await _workspace_agent()
    turn_id = await _turn(workspace_id, agent_id, "running")
    made = ObjectRef(kind="widget", name="anvil")
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.turn)
                .values(created_refs=[made.model_dump(mode="json")])
                .where(tables.turn.c.id == turn_id)
            )
        frame = await cancel_one_turn(_RecordingClient(), turn_id)
        assert frame is not None
        assert frame.created == (made,)
        async with workspace_tx() as connection:
            terminal = (
                await connection.execute(
                    sa.select(tables.turn.c.terminal).where(tables.turn.c.id == turn_id)
                )
            ).scalar_one()
    assert TerminalFrame.model_validate(terminal).created == (made,)


async def test_cancel_one_turn_names_work_committed_while_the_workflow_stops(db: None) -> None:
    workspace_id, agent_id = await _workspace_agent()
    turn_id = await _turn(workspace_id, agent_id, "running")
    made = ObjectRef(kind="widget", name="anvil")

    @dataclass
    class _StoppingClient:
        async def cancel_workflow_async(self, workflow_id: str) -> None:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.turn)
                    .values(created_refs=[made.model_dump(mode="json")])
                    .where(tables.turn.c.id == turn_id)
                )

    with ws(workspace_id):
        frame = await cancel_one_turn(_StoppingClient(), turn_id)

    assert frame is not None
    assert frame.created == (made,)


async def test_cancel_one_turn_cancels_a_queued_turn_with_no_live_workflow(db: None) -> None:
    """A queued turn never enqueued has no live workflow — the cancel is a no-op UPDATE, yet the row
    is still committed cancelled, so nothing downstream re-dispatches it."""
    workspace_id, agent_id = await _workspace_agent()
    turn_id = await _turn(workspace_id, agent_id, "queued")
    assert await cancel_one_turn(_RecordingClient(), turn_id) is not None
    assert await _status(turn_id) == "cancelled"


async def test_cancel_one_turn_leaves_a_terminal_turn_untouched(db: None) -> None:
    """A turn that already reached its own terminal (a cancel racing its done commit) is not
    disturbed: no workflow cancel is requested and it returns False."""
    workspace_id, agent_id = await _workspace_agent()
    done = await _turn(workspace_id, agent_id, "done")
    client = _RecordingClient()
    assert await cancel_one_turn(client, done) is None
    assert client.cancelled == []
    assert await _status(done) == "done"


async def test_cancel_one_turn_reports_nothing_cancelled_for_a_turn_that_does_not_exist(
    db: None,
) -> None:
    """A turn id with no row — a reconciler pass racing a workspace wipe, or a stale id from a
    caller — returns False and asks DBOS to cancel nothing, rather than faulting on the read the
    profile now rides along on."""
    await _workspace_agent()
    client = _RecordingClient()
    assert await cancel_one_turn(client, uuid4()) is None
    assert client.cancelled == []


async def test_cancel_one_turn_leaves_the_row_live_when_the_workflow_cancel_faults(
    db: None,
) -> None:
    """Cancel-before-commit: a DBOS/DB fault cancelling the workflow propagates and the row is NOT
    committed cancelled — it stays live for a retry, never a cancelled row whose workflow was never
    told to stop. This is the guard that fails if the commit is moved before the cancel."""
    workspace_id, agent_id = await _workspace_agent()
    turn_id = await _turn(workspace_id, agent_id, "running")

    @dataclass
    class _FaultyClient:
        async def cancel_workflow_async(self, workflow_id: str) -> None:
            raise sa.exc.SQLAlchemyError("cancel failed")

    with pytest.raises(sa.exc.SQLAlchemyError):
        await cancel_one_turn(_FaultyClient(), turn_id)
    assert await _status(turn_id) == "running"


def _metric_reader(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    """Route the counter onto a reader this test reads back, installing no global meter provider.
    The instrument cache holds one bound to the provider it was created against, so it is emptied
    alongside it."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(o11y.metrics, "get_meter", provider.get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    return reader


def _terminal_counts(reader: InMemoryMetricReader) -> list[tuple[int, str, str, str]]:
    """Every terminal counted so far, as (count, status, error class, profile). The reader yields no
    data at all until an instrument exists, which is the same answer as counting nothing."""
    data = reader.get_metrics_data()
    if data is None:
        return []
    return [
        (
            point.value,
            point.attributes["status"],
            point.attributes["error_class"],
            point.attributes["profile"],
        )
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == "ufo.turn_terminal_total"
        for point in metric.data.data_points
    ]


async def test_the_cancelled_terminal_is_counted_once_by_the_call_that_wrote_it(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The turn's own execution never writes a cancelled row, and a turn cancelled before one
    started has no execution at all, so this is where a cancelled turn joins the terminal counter —
    once, from the call that made the transition. The count carries the cancelled turn's own
    profile: `cancel_spawn` reaches this primitive with a subagent's turn, so a cancel storm
    inside one profile stays readable as that profile's rather than the main agent's."""
    reader = _metric_reader(monkeypatch)
    workspace_id, agent_id = await _workspace_agent()
    turn_id = await _turn(workspace_id, agent_id, "queued", "coding")
    assert await cancel_one_turn(_RecordingClient(), turn_id) is not None
    assert await cancel_one_turn(_RecordingClient(), turn_id) is None
    assert _terminal_counts(reader) == [(1, "cancelled", "", "coding")]


async def test_a_cancel_that_transitioned_nothing_counts_no_terminal(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The turn commits its own terminal inside the window between the status read and the update,
    so the update matches nothing and the call returns False. A terminal it did not write is not
    its to count — the row guard, not the pre-check, is what the counter follows."""
    reader = _metric_reader(monkeypatch)
    workspace_id, agent_id = await _workspace_agent()
    turn_id = await _turn(workspace_id, agent_id, "running")

    @dataclass
    class _RacingClient:
        async def cancel_workflow_async(self, workflow_id: str) -> None:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.turn)
                    .values(
                        status="done",
                        terminal=TerminalFrame(status="done").model_dump(mode="json"),
                        updated_at=sa.func.now(),
                    )
                    .where(tables.turn.c.id == turn_id)
                )

    assert await cancel_one_turn(_RacingClient(), turn_id) is None
    assert await _status(turn_id) == "done"
    assert _terminal_counts(reader) == []


async def test_the_operator_verb_cancels_a_wedged_turn_in_its_own_workspace(db: None) -> None:
    """`ufoctl turn cancel` is the only end an operator has for a turn no member can end — one
    waiting in-turn on work that will not finish, or one a rollout keeps recovering. The verb finds
    which workspace owns the id through the cross-workspace path, then cancels bound to that
    workspace, so a deploy serving many tenants can still be given one turn id and act on it.
    """
    workspace_id, agent_id = await _workspace_agent()
    other_workspace, other_agent = await _workspace_agent()
    wedged = await _turn(workspace_id, agent_id, "running")
    untouched = await _turn(other_workspace, other_agent, "running")
    client = _RecordingClient()

    async with owner_tx() as connection:
        found = (
            await connection.execute(
                sa.select(tables.turn.c.workspace_id).where(tables.turn.c.id == wedged)
            )
        ).scalar_one()
    assert found == workspace_id

    with ws(found):
        assert await cancel_one_turn(client, wedged) is not None
    assert client.cancelled == [str(wedged)]

    async with workspace_tx() as connection:
        statuses = dict(
            (
                await connection.execute(
                    sa.select(tables.turn.c.id, tables.turn.c.status).where(
                        tables.turn.c.id.in_([wedged, untouched])
                    )
                )
            ).all()
        )
    assert statuses[wedged] == "cancelled"
    assert statuses[untouched] == "running"
