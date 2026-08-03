"""The shared fleet's seat, its heartbeat, the executor-recovery sweep, and the cancel reconciler —
the fleet-wide background sweeps every serve process runs.

Every live serve process holds a bare `runtime_instance` seat it heartbeats — the shared fleet has
no single workspace to pin. The heartbeat is a per-process loop, not a shared job, so each instance
keeps only its own row fresh.

The instance id doubles as the process's DBOS executor id, which is what makes the row a liveness
signal for durable work: a queued workflow (a turn, a job) stays PENDING under the executor id that
dispatched it, so a workflow whose executor has no fresh row is stranded — its process is gone —
and the sweep re-dispatches it through DBOS recovery. A fresh row says the process is alive, which
is not the same as executing: only the process holding a claim can tell whether a task is still
running that workflow, so each process answers for its own claims and never for a peer's —
recovering a peer's live workflow would start a second concurrent execution, whose loser parks
forever in DBOS's duplicate-execution wait."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from dbos import DBOS, DBOSClient, WorkflowStatus
from dbos import error as dbos_error

from ufo.cancellation import cancel_one_turn
from ufo.db import owner_tx
from ufo.o11y import log
from ufo.schema import tables
from ufo.schema.records import CANCELLED, NON_TERMINAL_STATUSES
from ufo.workspace import ws

HEARTBEAT_INTERVAL_SECONDS = 2
STALE_AFTER_SECONDS = 10
EXECUTOR_RECOVERY_INTERVAL_SECONDS = 5
CANCEL_RECONCILE_INTERVAL_SECONDS = 5
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
    """Re-dispatch queued workflows whose execution is gone: on an interval, read the PENDING
    workflows in this deploy's DBOS store and return the claimed-but-unexecuted ones to their queue,
    where each resumes from its last recorded step under the same workflow id. Two kinds of claim
    qualify, and a claim is only ever judged by the process that took it or by the absence of that
    process:

    A dead peer's — its executor id holds PENDING rows and has no fresh `runtime_instance`
    heartbeat, so DBOS recovery runs for that executor. An executor with no seat row at all is dead
    by definition; a seat outlives its process only until the sweep next runs, and a process that
    retired its seat on graceful shutdown left nothing PENDING or is recovered all the same.

    This process's own — the row is PENDING under this process's executor id while nothing here is
    executing it. Every serve process runs this loop, so any survivor reclaims a crashed peer's work
    and each reclaims the claims it abandoned; executor ids are unique per process, so a freshly
    re-dispatched workflow immediately carries a live executor's id and a concurrent sweep on
    another instance cannot re-steal it. A failed tick is logged and the loop continues, mirroring
    the heartbeat: recovery must survive a transient database or DBOS error, and a sustained outage
    stalls every peer's sweep equally."""

    dbos: DBOS
    interval_seconds: float = EXECUTOR_RECOVERY_INTERVAL_SECONDS
    stale_after_seconds: float = STALE_AFTER_SECONDS
    unexecuted: set[str] = field(default_factory=set)

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.sweep()
            except (sa.exc.SQLAlchemyError, dbos_error.DBOSException) as error:
                log("instance.executor_recovery_failed", error_class=type(error).__name__)

    async def sweep(self) -> None:
        pending = await self._pending_workflows()
        claiming = {status.executor_id for status in pending if status.executor_id}
        for executor in sorted(claiming - await self._live_executors()):
            recovered = await asyncio.to_thread(DBOS._recover_pending_workflows, [executor])
            log(
                "instance.executor_recovered",
                executor=executor,
                workflows=len(recovered),
            )
        await self._release_unexecuted_claims(pending)

    async def _pending_workflows(self) -> list[WorkflowStatus]:
        """Every PENDING workflow, oldest first so a scan that hits the limit still reaches the
        longest-stranded work; a hit limit is logged, never silently truncated."""
        pending = await asyncio.to_thread(
            DBOS.list_workflows,
            status="PENDING",
            limit=PENDING_WORKFLOW_SCAN_LIMIT,
            load_input=False,
            load_output=False,
        )
        if len(pending) == PENDING_WORKFLOW_SCAN_LIMIT:
            log("instance.pending_scan_at_limit", limit=PENDING_WORKFLOW_SCAN_LIMIT)
        return pending

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

    async def _release_unexecuted_claims(self, pending: list[WorkflowStatus]) -> None:
        """Return this process's claims that nothing here is executing to their queue. A PENDING row
        is this process's word that a task is running that workflow, and a task that dies without
        writing an outcome — cancelled, or destroyed while the process lives on — leaves that word
        standing: no peer may act on a heartbeating executor's claim, and nothing else revisits it.
        On the conversation-partitioned turns queue the dead claim also holds the partition's only
        slot, so the queue has no room to start the very workflow holding it.

        DBOS's active-workflow set is what this process can read to tell whether a task is running
        a claimed workflow, and one absence from it settles nothing: a workflow dequeued moments ago
        has not reached its first step, and one that just finished released its entry before writing
        its outcome. So a claim is released only where two sweeps in a row find it absent, and a
        sighting is spent the moment this sweep reaches it — the set is read again at the release,
        and whether that read leaves the claim alone, the release lands, or the release raises, the
        next release needs a fresh pair of sightings. The count is written before any release runs,
        so no path out of the loop can leave a spent sighting standing.

        Releasing is DBOS's own recovery action for a queued workflow: the row returns to ENQUEUED
        under the same workflow id, freeing its partition slot and re-dispatching it with its
        recorded steps intact, so the resumed execution replays completed work instead of redoing
        it. Every workflow here is queued — a turn on the turns queue, a job fan-out on the jobs
        queue, a job tick on DBOS's internal one — so returning the row to its queue is the whole
        release."""
        abandoned = {
            status.workflow_id for status in pending if status.executor_id == DBOS.executor_id
        } - self._executing()
        sighted_twice = sorted(abandoned & self.unexecuted)
        self.unexecuted.clear()
        self.unexecuted.update(abandoned - set(sighted_twice))
        for workflow_id in sighted_twice:
            if workflow_id in self._executing():
                continue
            await asyncio.to_thread(self.dbos._sys_db.clear_queue_assignment, workflow_id)
            log("instance.claim_released", workflow=workflow_id)

    def _executing(self) -> set[str]:
        return set(self.dbos._active_workflows_set.activeList())


@dataclass(frozen=True)
class CancelReconciler:
    """Cascade a cancel to the descendant turns it spawned — the sole mechanism that carries a
    cancel down the tree. Cancelling a turn (the eval driver's deadline, the `cancel_subagent` tool)
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
            if cancelled:
                log("instance.cancel_reconciled", turn_id=str(orphan.id))

    def _orphans_query(self) -> sa.Select:
        """Every non-terminal turn that has a cancelled ancestor, with its workspace. Climbs the
        `parent_turn_id` chain from each live turn: a turn is an orphan the moment any turn above it
        is cancelled, whatever the statuses in between — so a live grandchild under an intermediate
        that finished `done` is caught, not only a direct child of the cancelled turn. Bounded by
        the live-turn count and depth; it stops climbing once a cancelled ancestor is hit."""
        turn = tables.turn
        chain = (
            sa.select(
                turn.c.id.label("orphan"),
                turn.c.workspace_id.label("orphan_workspace"),
                turn.c.parent_turn_id.label("ancestor_parent"),
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
                ancestor.c.parent_turn_id,
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
