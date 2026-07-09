from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext
from ufo.ext.manifest import JobSpec
from ufo.jobs import (
    CORE_EXTENSION,
    SANDBOX_IDLE_TTL_SECONDS,
    SANDBOX_REAP_JOB,
    JobRunner,
    SandboxReaper,
    bindings_from,
)
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import SandboxHandle, SandboxSpec, format_sandbox_handle
from ufo.schema import tables
from ufo.schema.records import NON_TERMINAL_STATUSES, TerminalFrame

IDLE_AGE_SECONDS = SANDBOX_IDLE_TTL_SECONDS + 3600
FRESH_AGE_SECONDS = 60
LOCAL_BACKEND = "local"


async def _reap(carrier: object) -> None:
    """Drive the reaper through the real dispatch: the sandbox-reap JobSpec's candidate names the
    workspaces holding an idle sandbox, and `fire` binds each before running it — so the reap
    runs exactly as the fleet fires it, scoped per workspace and never unbound, and an idle-free
    workspace (absent from the candidates) is never opened."""
    reaper = SandboxReaper(carrier=carrier, backend=LOCAL_BACKEND)

    async def _handler(context: ExtensionContext) -> None:
        await reaper.run()

    spec = JobSpec(
        name=SANDBOX_REAP_JOB,
        schedule=None,
        handler=_handler,
        candidates=reaper.candidate_workspaces,
    )
    runner = JobRunner(bindings=bindings_from((), (spec,)))
    await runner.fire(f"{CORE_EXTENSION}:{SANDBOX_REAP_JOB}")


@dataclass
class RecordingCarrier:
    """Stands in for the carrier so the test asserts which conversations the reaper selects and the
    id it reaps them by — the reaper drives the seam, this records the destroys it issues; the
    carriers' own destroy is proven against the real local carrier here and the real Docker
    container in the docker extension."""

    reaped: list[SandboxHandle] = field(default_factory=list)

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError("the reaper never creates a sandbox")

    async def exec(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("the reaper never execs in a sandbox")

    async def export(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("the reaper never exports from a sandbox")

    async def destroy(self, handle: SandboxHandle) -> None:
        self.reaped.append(handle)

    @property
    def destroyed(self) -> list[UUID]:
        return [handle.conversation_id for handle in self.reaped]


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
    workspace_id: UUID,
    agent_id: UUID,
    status: str,
    age_seconds: float,
    handle: str = "local:sbx",
) -> UUID:
    """A conversation carrying a persisted sandbox `handle`, with one turn stamped `age_seconds` in
    the past — its `updated_at` is the last-activity the reaper measures idleness against, and
    `status` says whether it is live. The reaper reaps only conversations whose row carries a
    handle, so a test conversation must persist one to be in scope."""
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
                sandbox_handle=handle,
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


async def _stored_handle(conversation_id: UUID) -> str | None:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.conversation.c.sandbox_handle).where(
                    tables.conversation.c.id == conversation_id
                )
            )
        ).scalar_one()


async def test_reaper_destroys_a_conversation_idle_past_ttl_but_not_a_fresh_one(db: None) -> None:
    workspace_id, agent_id = await _workspace_agent()
    idle = await _conversation(workspace_id, agent_id, "done", IDLE_AGE_SECONDS)
    await _conversation(workspace_id, agent_id, "done", FRESH_AGE_SECONDS)
    carrier = RecordingCarrier()
    await _reap(carrier)
    assert carrier.destroyed == [idle]


async def test_reaper_enumerates_and_scopes_across_workspaces(db: None) -> None:
    """One sweep finds idle conversations across every workspace through the single owner_tx read
    and reaps each scoped to its own workspace, clearing its row — no caller binds a workspace, the
    sweep binds each candidate itself. The cross-workspace reaper, scope-per-row."""
    ws_a, agent_a = await _workspace_agent()
    idle_a = await _conversation(ws_a, agent_a, "done", IDLE_AGE_SECONDS)
    ws_b, agent_b = await _workspace_agent()
    idle_b = await _conversation(ws_b, agent_b, "done", IDLE_AGE_SECONDS)
    carrier = RecordingCarrier()
    await _reap(carrier)
    assert set(carrier.destroyed) == {idle_a, idle_b}
    assert await _stored_handle(idle_a) is None
    assert await _stored_handle(idle_b) is None


async def test_reaper_touches_only_the_workspace_with_an_idle_sandbox(db: None) -> None:
    """Selectivity: two workspaces exist but only ws_a's sandbox is idle past the TTL — ws_b's
    conversation carries a handle but its turn is fresh, so it is not a candidate. One `run` finds
    ws_a's idle conversation through the single owner_tx read and reaps it; ws_b is never opened, so
    its handle stands. The idle-free workspace runs no per-workspace transaction on the sweep."""
    ws_a, agent_a = await _workspace_agent()
    idle_a = await _conversation(ws_a, agent_a, "done", IDLE_AGE_SECONDS)
    ws_b, agent_b = await _workspace_agent()
    fresh_b = await _conversation(ws_b, agent_b, "done", FRESH_AGE_SECONDS)
    carrier = RecordingCarrier()
    await _reap(carrier)
    assert carrier.destroyed == [idle_a]
    assert await _stored_handle(idle_a) is None
    assert await _stored_handle(fresh_b) == "local:sbx"


async def test_reaper_reaps_by_the_stored_id_and_clears_the_row(db: None) -> None:
    """The durable handle is the source of truth: the reaper strips the backend prefix and reaps by
    the stored id — the id a prior process persisted — then clears the row so a reaped sandbox is
    never resumed into a released id; the next turn creates fresh."""
    workspace_id, agent_id = await _workspace_agent()
    conversation = await _conversation(
        workspace_id,
        agent_id,
        "done",
        IDLE_AGE_SECONDS,
        handle=format_sandbox_handle("local", "sbx-42"),
    )
    carrier = RecordingCarrier()
    await _reap(carrier)
    assert [(h.conversation_id, h.container_id) for h in carrier.reaped] == [
        (conversation, "sbx-42")
    ]
    assert await _stored_handle(conversation) is None


async def test_reaper_skips_a_conversation_with_no_persisted_handle(db: None) -> None:
    """A conversation whose sandbox was already reaped (or that never opened one) carries no handle,
    so it is out of the reaper's scope — the reaper reclaims from the durable column, not every idle
    conversation."""
    workspace_id, agent_id = await _workspace_agent()
    async with workspace_tx() as connection:
        conversation_id, turn_id = uuid4(), uuid4()
        stamp = datetime.now(UTC) - timedelta(seconds=IDLE_AGE_SECONDS)
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=None,
                sandbox_handle=None,
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
                status="done",
                inbound="x",
                terminal=TerminalFrame(status="done").model_dump(mode="json"),
                created_at=stamp,
                updated_at=stamp,
            )
        )
    carrier = RecordingCarrier()
    await _reap(carrier)
    assert carrier.reaped == []


async def test_reaper_skips_a_handle_another_backend_wrote(db: None) -> None:
    """A deploy that switched carriers leaves rows another backend wrote; the running carrier
    neither reaps nor clears them — the id is not its to reclaim, so the prefix gates the reap."""
    workspace_id, agent_id = await _workspace_agent()
    conversation = await _conversation(
        workspace_id, agent_id, "done", IDLE_AGE_SECONDS, handle="docker:cid-1"
    )
    carrier = RecordingCarrier()
    await _reap(carrier)
    assert carrier.reaped == []
    assert await _stored_handle(conversation) == "docker:cid-1"


async def test_reaper_skips_a_conversation_with_an_in_flight_turn(db: None) -> None:
    """The idle measure is last-activity, but a long-running turn's row can be old while the turn is
    still executing — the status guard, not the timestamp, keeps the reaper off a live sandbox."""
    workspace_id, agent_id = await _workspace_agent()
    await _conversation(workspace_id, agent_id, "running", IDLE_AGE_SECONDS)
    idle = await _conversation(workspace_id, agent_id, "done", IDLE_AGE_SECONDS)
    carrier = RecordingCarrier()
    await _reap(carrier)
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
    await _reap(carrier)
    assert len(carrier.destroyed) == 1


async def test_reaper_destroy_on_a_never_created_sandbox_is_a_no_op(db: None) -> None:
    """The real local carrier holds nothing for a conversation whose turn never ran, so reaping it
    is a harmless cleanup that never raises — idempotency proven against the real carrier — and the
    row is cleared all the same."""
    workspace_id, agent_id = await _workspace_agent()
    conversation = await _conversation(workspace_id, agent_id, "done", IDLE_AGE_SECONDS)
    await _reap(LocalCarrier())
    assert await _stored_handle(conversation) is None
