"""The shared fleet's seat, its heartbeat, and the executor-recovery sweep — the only
instance-aware code in core.

Every live serve process holds a bare `runtime_instance` seat it heartbeats — the shared fleet has
no single workspace to pin. The heartbeat is a per-process loop, not a shared job, so each instance
keeps only its own row fresh.

The instance id doubles as the process's DBOS executor id, which is what makes the row a liveness
signal for durable work: a queued workflow (a turn, a job) stays PENDING under the executor id that
dispatched it, so a workflow whose executor has no fresh row is stranded — its process is gone —
and the sweep re-dispatches it through DBOS recovery. An executor with a fresh row is alive and
mid-execution; recovering it would start a second concurrent execution of a live workflow, whose
loser parks forever in DBOS's duplicate-execution wait — the sweep never touches it."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from dbos import DBOS
from dbos import error as dbos_error

from ufo.db import owner_tx
from ufo.o11y import log
from ufo.schema import tables

HEARTBEAT_INTERVAL_SECONDS = 2
STALE_AFTER_SECONDS = 10
EXECUTOR_RECOVERY_INTERVAL_SECONDS = 5
PENDING_WORKFLOW_SCAN_LIMIT = 1000


async def record_fleet_seat(instance_id: UUID) -> None:
    """The shared fleet's seat: a bare `runtime_instance` row with no workspace, written before
    DBOS launches so the executor-recovery sweep never reads this process's own fresh work as
    stranded. The fleet takes no admission guard — its backends are shared by construction (an
    unshareable one has no fleet deploy) — so recording the seat is the whole flow."""
    async with owner_tx() as connection:
        await connection.execute(
            sa.insert(tables.runtime_instance).values(
                id=instance_id,
                workspace_id=None,
                heartbeat_at=sa.func.now(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    log("instance.fleet_seat_recorded", instance=str(instance_id))


@dataclass(frozen=True)
class Heartbeat:
    """Keep this instance's row fresh on an interval, and drop it on graceful shutdown so peers see
    the seat free at once rather than waiting out the stale window. A transient database error on
    one tick is logged and the loop continues — a single failed update must not kill the heartbeat
    and let a healthy instance's row go stale, which would wrongly free the seat to a peer; only a
    sustained outage lets the row age out, which is the correct signal that the instance is gone."""

    instance_id: UUID

    async def run(self) -> None:
        while True:
            try:
                await self.beat()
            except sa.exc.SQLAlchemyError as error:
                log(
                    "instance.heartbeat_failed",
                    instance=str(self.instance_id),
                    error_class=type(error).__name__,
                )
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    async def beat(self) -> None:
        """One liveness stamp, run first thing by the loop — which serve drives on a dedicated
        thread from the moment the seat exists, before DBOS launches: no boot step can outrun the
        heartbeat into a window where this process holds durable work while its seat reads stale,
        and an app-loop stall never makes a live process sweepable."""
        async with owner_tx() as connection:
            await connection.execute(
                sa.update(tables.runtime_instance)
                .values(heartbeat_at=sa.func.now(), updated_at=sa.func.now())
                .where(tables.runtime_instance.c.id == self.instance_id)
            )

    async def retire(self) -> None:
        async with owner_tx() as connection:
            await connection.execute(
                sa.delete(tables.runtime_instance).where(
                    tables.runtime_instance.c.id == self.instance_id
                )
            )


@dataclass(frozen=True)
class ExecutorRecovery:
    """Re-dispatch queued workflows stranded by dead processes: on an interval, read the executor
    ids still holding PENDING workflows in this deploy's DBOS store, subtract the executors with a
    fresh `runtime_instance` heartbeat, and run DBOS recovery for the rest — each stranded turn or
    job re-enters its queue and resumes from its last recorded step. Every serve process runs this
    loop, so any survivor reclaims a crashed peer's work; executor ids are unique per process, so a
    freshly re-dispatched workflow immediately carries a live executor's id and a concurrent sweep
    on another instance cannot re-steal it. An executor with no seat row at all is dead by
    definition — a seat outlives its process only until the sweep next runs, and a process that
    retired its seat on graceful shutdown left nothing PENDING or is recovered all the same. A
    failed tick is logged and the loop continues, mirroring the heartbeat: recovery must survive a
    transient database or DBOS error, and a sustained outage stalls every peer's sweep equally."""

    interval_seconds: float = EXECUTOR_RECOVERY_INTERVAL_SECONDS
    stale_after_seconds: float = STALE_AFTER_SECONDS

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.sweep()
            except (sa.exc.SQLAlchemyError, dbos_error.DBOSException) as error:
                log("instance.executor_recovery_failed", error_class=type(error).__name__)

    async def sweep(self) -> None:
        stranded = await self._pending_executors() - await self._live_executors()
        for executor in sorted(stranded):
            recovered = await asyncio.to_thread(DBOS._recover_pending_workflows, [executor])
            log(
                "instance.executor_recovered",
                executor=executor,
                workflows=len(recovered),
            )

    async def _pending_executors(self) -> set[str]:
        """Executor ids holding PENDING workflows, oldest first so a scan that hits the limit still
        reaches the longest-stranded work; a hit limit is logged, never silently truncated."""
        pending = await asyncio.to_thread(
            DBOS.list_workflows,
            status="PENDING",
            limit=PENDING_WORKFLOW_SCAN_LIMIT,
            load_input=False,
            load_output=False,
        )
        if len(pending) == PENDING_WORKFLOW_SCAN_LIMIT:
            log("instance.pending_scan_at_limit", limit=PENDING_WORKFLOW_SCAN_LIMIT)
        return {status.executor_id for status in pending if status.executor_id}

    async def _live_executors(self) -> set[str]:
        cutoff = datetime.now(UTC) - timedelta(seconds=self.stale_after_seconds)
        async with owner_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.runtime_instance.c.id).where(
                        tables.runtime_instance.c.heartbeat_at >= cutoff
                    )
                )
            ).all()
        return {str(row.id) for row in rows}
