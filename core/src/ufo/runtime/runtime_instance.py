"""The shared fleet's seat, its heartbeat, the executor-recovery sweep, the cancel reconciler,
and the stranded-turn reconciler — the fleet-wide background sweeps every serve process runs.

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
from dbos import DBOS, DBOSClient
from dbos import error as dbos_error

from ufo.db import owner_tx
from ufo.harness.o11y import log
from ufo.runtime.turns.cancellation import cancel_one_turn
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import CANCELLED, INTENT_ADMISSION, NON_TERMINAL_STATUSES, RUNNING

HEARTBEAT_INTERVAL_SECONDS = 2
STALE_AFTER_SECONDS = 10
EXECUTOR_RECOVERY_INTERVAL_SECONDS = 5
CANCEL_RECONCILE_INTERVAL_SECONDS = 5
STRANDED_RECONCILE_INTERVAL_SECONDS = 60
STRANDED_TURN_GRACE_SECONDS = 300
PENDING_WORKFLOW_SCAN_LIMIT = 1000
CLAIMED_TURN_SCAN_LIMIT = 1000
ADVANCING_WORKFLOW_STATUSES = ("PENDING", "ENQUEUED", "DELAYED")


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


@dataclass(frozen=True)
class CancelReconciler:
    """Cascade a cancel to the descendant turns it spawned — the sole mechanism that carries a
    cancel down the tree. Cancelling a turn (the eval driver's deadline, the `cancel_spawn` tool)
    is a local act through `cancel_one_turn`: it terminalizes just that turn. This sweep cancels
    everything beneath it. On an interval it finds every non-terminal turn with a cancelled
    ancestor — climbing the `parent_turn_id` chain, so a live grandchild beneath a child that
    already finished on its own (`done`) is still reached — and cancels each through the same
    `cancel_one_turn` primitive, cancelling its workflow before committing its terminal.

    Interval-driven rather than pushed from the canceller: the cost is paid only when a cancel
    actually happens, the sweep is inherently crash-safe (a descendant left live by a fault or
    re-dispatched by DBOS recovery is simply re-selected next tick and converges), and cancellation
    stays off the realtime turn loop. A running descendant therefore stops within one interval
    rather than instantly — bounded and, since a model round usually outlasts the interval, under a
    round of extra burn. Every serve process runs it, so any survivor reconciles a crashed peer's
    cancels; a failed tick is logged and the loop continues, mirroring the recovery sweep."""

    client: DBOSClient
    interval_seconds: float = CANCEL_RECONCILE_INTERVAL_SECONDS

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.sweep()
            except (sa.exc.SQLAlchemyError, dbos_error.DBOSException) as error:
                log("instance.cancel_reconcile_failed", error_class=type(error).__name__)

    async def sweep(self) -> None:
        async with owner_tx() as connection:
            orphans = (await connection.execute(self._orphans_query())).all()
        for orphan in orphans:
            with ws(orphan.workspace_id):
                cancelled = await cancel_one_turn(self.client, orphan.id)
            if cancelled is not None:
                log("instance.cancel_reconciled", turn_id=str(orphan.id))

    def _orphans_query(self) -> sa.Select:
        turn = tables.turn
        chain = (
            sa.select(
                turn.c.id.label("orphan"),
                turn.c.workspace_id.label("orphan_workspace"),
                self._dependent_parent(turn).label("ancestor_parent"),
                turn.c.status.label("ancestor_status"),
            )
            .where(turn.c.status.in_(NON_TERMINAL_STATUSES))
            .cte("cancel_orphan_chain", recursive=True)
        )
        ancestor = turn.alias("ancestor")
        chain = chain.union_all(
            sa.select(
                chain.c.orphan,
                chain.c.orphan_workspace,
                self._dependent_parent(ancestor),
                ancestor.c.status,
            )
            .select_from(chain.join(ancestor, ancestor.c.id == chain.c.ancestor_parent))
            .where(chain.c.ancestor_status != CANCELLED)
        )
        return (
            sa.select(
                chain.c.orphan.label("id"),
                chain.c.orphan_workspace.label("workspace_id"),
            )
            .where(chain.c.ancestor_status == CANCELLED)
            .distinct()
        )

    def _dependent_parent(self, turn: sa.Table | sa.FromClause) -> sa.ColumnElement:
        return sa.case(
            (
                sa.or_(
                    turn.c.subagent_profile.is_not(None),
                    turn.c.admission_source == INTENT_ADMISSION,
                ),
                turn.c.parent_turn_id,
            ),
            else_=sa.null(),
        )


@dataclass(frozen=True)
class StrandedTurnReconciler:
    """Terminalize a claimed turn whose workflow can no longer advance it. A turn goes RUNNING from
    inside its own workflow, stamping that workflow's id as `running_attempt`, and the claim admits
    only that same attempt — so once the attempt's workflow reaches a terminal DBOS status or
    leaves the store, no dispatch can ever reach the row again and it holds `running` forever. The
    conversation reads it as live work, and admission folds a member's next message into a turn
    nothing will run.

    The DBOS status of the claimed attempt is the only signal that separates a stranded row from
    live work. A turn's own family cannot: a spawned agent outlives its spawner by design, so a
    live child under a `done` parent is ordinary. Neither can row freshness: a turn stepping
    normally goes minutes between writes, so an idle row is not an absent one. PENDING, ENQUEUED
    and DELAYED all mean something still carries the turn; every other status, and absence, mean
    nothing does.

    Only RUNNING rows are candidates. A QUEUED or PARKED turn belongs to the dispatch sweep, which
    re-offers it under a fresh workflow id — its current id going missing is that sweep's ordinary
    path, not a strand. The grace window keeps a row that has just been written out of the scan,
    so a claim landing beside the sweep's own reads is never mistaken for one.

    Cancel, not recovery: DBOS recovery re-dispatches PENDING work whose executor died, which is
    the executor sweep's job and is already covered. A workflow that ended or vanished has nothing
    to resume, so the turn's only remaining terminal is cancelled. `cancel_one_turn` re-reads the
    row under its own transaction and no-ops on a turn that reached a terminal in the meantime, so
    a sweep racing a turn's own commit cannot disturb it."""

    client: DBOSClient
    interval_seconds: float = STRANDED_RECONCILE_INTERVAL_SECONDS
    grace_seconds: float = STRANDED_TURN_GRACE_SECONDS

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.sweep()
            except (sa.exc.SQLAlchemyError, dbos_error.DBOSException) as error:
                log("instance.stranded_reconcile_failed", error_class=type(error).__name__)

    async def sweep(self) -> None:
        async with owner_tx() as connection:
            claimed = (await connection.execute(self._claimed_query())).all()
        if len(claimed) == CLAIMED_TURN_SCAN_LIMIT:
            log("instance.claimed_scan_at_limit", limit=CLAIMED_TURN_SCAN_LIMIT)
        advancing = await self._advancing_attempts([row.running_attempt for row in claimed])
        for row in claimed:
            if row.running_attempt in advancing:
                continue
            with ws(row.workspace_id):
                cancelled = await cancel_one_turn(self.client, row.id)
            if cancelled is not None:
                log(
                    "instance.stranded_turn_reconciled",
                    turn_id=str(row.id),
                    attempt=row.running_attempt,
                )

    def _claimed_query(self) -> sa.Select:
        """Every RUNNING turn holding a claim the grace window has aged past, oldest first so a
        scan that hits the limit still reaches the longest-stranded row."""
        cutoff = datetime.now(UTC) - timedelta(seconds=self.grace_seconds)
        return (
            sa.select(
                tables.turn.c.id,
                tables.turn.c.workspace_id,
                tables.turn.c.running_attempt,
            )
            .where(
                tables.turn.c.status == RUNNING,
                tables.turn.c.running_attempt.is_not(None),
                tables.turn.c.updated_at < cutoff,
            )
            .order_by(tables.turn.c.updated_at)
            .limit(CLAIMED_TURN_SCAN_LIMIT)
        )

    async def _advancing_attempts(self, attempts: list[str]) -> set[str]:
        """The subset of `attempts` DBOS still carries. An empty scan asks nothing: a workflow id
        filter that is given no ids selects the whole store."""
        if not attempts:
            return set()
        carried = await self.client.list_workflows_async(
            workflow_ids=attempts,
            status=list(ADVANCING_WORKFLOW_STATUSES),
            load_input=False,
            load_output=False,
        )
        return {status.workflow_id for status in carried}
