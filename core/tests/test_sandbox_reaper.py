from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa

from selfhost.db import workspace_tx
from selfhost.jobs import SANDBOX_IDLE_TTL_SECONDS, SandboxReaper
from selfhost.sandbox.local import LocalCarrier
from selfhost.sandbox.session import SandboxHandle, SandboxSpec
from selfhost.schema import tables
from selfhost.schema.records import NON_TERMINAL_STATUSES, TerminalFrame

IDLE_AGE_SECONDS = SANDBOX_IDLE_TTL_SECONDS + 3600
FRESH_AGE_SECONDS = 60


@dataclass
class RecordingCarrier:
    """Stands in for the carrier so the test asserts which conversations the reaper selects — the
    reaper drives the seam, this records the destroys it issues; the carriers' own destroy is proven
    against the real local carrier here and the real Docker container in the docker extension."""

    destroyed: list[UUID] = field(default_factory=list)

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError("the reaper never creates a sandbox")

    async def exec(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("the reaper never execs in a sandbox")

    async def export(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("the reaper never exports from a sandbox")

    async def destroy(self, handle: SandboxHandle) -> None:
        self.destroyed.append(handle.conversation_id)


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
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id


async def _conversation(
    workspace_id: UUID, agent_id: UUID, status: str, age_seconds: float
) -> UUID:
    """A conversation with one turn stamped `age_seconds` in the past — its `updated_at` is the
    last-activity the reaper measures idleness against, and `status` says whether it is live."""
    conversation_id, turn_id = uuid4(), uuid4()
    stamp = datetime.now(UTC) - timedelta(seconds=age_seconds)
    terminal = None if status in NON_TERMINAL_STATUSES else TerminalFrame(status=status)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface="cli",
                queue_key=uuid4().hex,
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
                terminal=None if terminal is None else terminal.model_dump(mode="json"),
                created_at=stamp,
                updated_at=stamp,
            )
        )
    return conversation_id


async def test_reaper_destroys_a_conversation_idle_past_ttl_but_not_a_fresh_one(db: None) -> None:
    workspace_id, agent_id = await _workspace_agent()
    idle = await _conversation(workspace_id, agent_id, "done", IDLE_AGE_SECONDS)
    await _conversation(workspace_id, agent_id, "done", FRESH_AGE_SECONDS)
    carrier = RecordingCarrier()
    await SandboxReaper(carrier=carrier).run()
    assert carrier.destroyed == [idle]


async def test_reaper_skips_a_conversation_with_an_in_flight_turn(db: None) -> None:
    """The idle measure is last-activity, but a long-running turn's row can be old while the turn is
    still executing — the status guard, not the timestamp, keeps the reaper off a live sandbox."""
    workspace_id, agent_id = await _workspace_agent()
    await _conversation(workspace_id, agent_id, "running", IDLE_AGE_SECONDS)
    idle = await _conversation(workspace_id, agent_id, "done", IDLE_AGE_SECONDS)
    carrier = RecordingCarrier()
    await SandboxReaper(carrier=carrier).run()
    assert carrier.destroyed == [idle]


async def test_reaper_rechecks_and_skips_a_conversation_that_went_active_after_the_snapshot(
    db: None,
) -> None:
    """The idle snapshot and the out-of-band carrier destroy don't share a transaction, so a
    conversation that admits a turn between them must not have its just-created sandbox reaped. The
    per-destroy re-check catches it — here a racing carrier admits an in-flight turn on the other
    idle conversation during the first destroy, and the reaper skips it (order-independent)."""
    workspace_id, agent_id = await _workspace_agent()
    a = await _conversation(workspace_id, agent_id, "done", IDLE_AGE_SECONDS)
    b = await _conversation(workspace_id, agent_id, "done", IDLE_AGE_SECONDS)

    async def _go_active(conversation_id: UUID) -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    seq=2,
                    status="running",
                    inbound="y",
                    terminal=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )

    class _RacingCarrier(RecordingCarrier):
        async def destroy(self, handle: SandboxHandle) -> None:
            first = not self.destroyed
            await super().destroy(handle)
            if first:
                await _go_active(b if handle.conversation_id == a else a)

    carrier = _RacingCarrier()
    await SandboxReaper(carrier=carrier).run()
    assert len(carrier.destroyed) == 1


async def test_reaper_destroy_on_a_never_created_sandbox_is_a_no_op(db: None) -> None:
    """The real local carrier holds nothing for a conversation whose turn never ran, so reaping it
    is a harmless cleanup that never raises — idempotency proven against the real carrier."""
    workspace_id, agent_id = await _workspace_agent()
    await _conversation(workspace_id, agent_id, "done", IDLE_AGE_SECONDS)
    await SandboxReaper(carrier=LocalCarrier()).run()
