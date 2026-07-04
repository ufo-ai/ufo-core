"""The jobs role: discovered JobSpecs (core + extensions) become live DBOS work at boot.

Registration is dynamic — `DBOS.apply_schedules` for cron jobs, an enqueue for one-shots — not the
import-time `@DBOS.scheduled` decorator, because jobs are discovered from the installed extensions
at boot, not known when this module is imported; `apply_schedules` upserts, so re-registration on
every restart is idempotent. Registration is the synchronous DBOS API: the async variants repoint
the running loop's default executor at DBOS's shared pool, so a short-lived boot loop closing would
shut that pool down. One durable workflow fires each handler with the extension's scoped
ExtensionContext, so a core job and an extension job run the identical path."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOS, DBOSClient, EnqueueOptions, Queue, ScheduleInput

from selfhost.accounting import ALLOW, SpendEvaluator
from selfhost.blob import BlobStore
from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.ext.context import ExtensionContext, TurnInvoker, context_for
from selfhost.ext.manifest import JobSpec, Manifest
from selfhost.indexing import EmbedClient, IndexBackend
from selfhost.o11y import log
from selfhost.sandbox.session import Carrier, SandboxHandle
from selfhost.schema import tables
from selfhost.schema.records import (
    DBOS_APP_VERSION,
    NON_TERMINAL_STATUSES,
    PARKED,
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
)
from selfhost.sources.sync import (
    SOURCE_SYNC_JOB,
    SOURCE_SYNC_SCHEDULE,
    PageFeed,
    SyncDriver,
)

JOB_QUEUE_NAME = "jobs"
JOB_WORKFLOW_NAME = "job"
CORE_EXTENSION = "core"
SPEND_RESUME_JOB = "spend_resume"
SPEND_RESUME_SCHEDULE = "0 * * * * *"
RESUME_ENQUEUE_GRACE_SECONDS = 300
SANDBOX_REAP_JOB = "sandbox_reap"
SANDBOX_REAP_SCHEDULE = "0 */10 * * * *"
SANDBOX_IDLE_TTL_SECONDS = 1800
JOB_QUEUE = Queue(JOB_QUEUE_NAME)


@dataclass(frozen=True, slots=True)
class _ParkedTurn:
    id: UUID
    workspace_id: UUID
    conversation_id: UUID
    agent_id: UUID
    member_id: UUID | None


@dataclass(frozen=True)
class SpendResume:
    """Re-admit parked turns whose caps now have headroom — the resume half of parking. A
    batch-at-interval job, never fired by the spend_cap write it reacts to, so raising a cap frees
    its parked turns on the next sweep. It only ENQUEUES; the turn stays PARKED until its own
    execution atomically claims it (parked → running), so a crash between decide and enqueue leaves
    it re-enqueueable rather than orphaned. Each run is a fresh DBOS workflow id (the original was
    consumed by the run that parked it); the transcript and per-attempt ledger stay keyed by the
    turn id, so the re-run is idempotent at the durable layer and each attempt's real spend is
    billed.

    A parked turn's resume can linger unclaimed while its conversation partition is busy, so the
    enqueue stamps an advisory `resume_enqueued_at`: a sweep skips a turn stamped within the grace
    window, bounding a lingering turn to one in-flight resume instead of one per sweep. The stamp is
    advisory, not a status flip — set before the enqueue and cleared by the claim, so a crash
    between stamp and enqueue merely delays re-admission to the end of the grace window rather than
    orphaning the turn."""

    client: DBOSClient

    async def run(self) -> None:
        for turn in await self._parked_turns():
            async with workspace_tx() as connection:
                decision = await SpendEvaluator(
                    turn.workspace_id, turn.member_id, turn.agent_id
                ).decide(connection, 0)
            if decision.outcome == ALLOW:
                await self._enqueue(turn)

    async def _parked_turns(self) -> tuple[_ParkedTurn, ...]:
        cutoff = datetime.now(UTC) - timedelta(seconds=RESUME_ENQUEUE_GRACE_SECONDS)
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.id,
                        tables.turn.c.workspace_id,
                        tables.turn.c.conversation_id,
                        tables.turn.c.agent_id,
                        tables.conversation.c.member_id,
                    )
                    .select_from(tables.turn.join(tables.conversation))
                    .where(
                        tables.turn.c.status == PARKED,
                        sa.or_(
                            tables.turn.c.resume_enqueued_at.is_(None),
                            tables.turn.c.resume_enqueued_at < cutoff,
                        ),
                    )
                )
            ).all()
        return tuple(
            _ParkedTurn(r.id, r.workspace_id, r.conversation_id, r.agent_id, r.member_id)
            for r in rows
        )

    async def _enqueue(self, turn: _ParkedTurn) -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.turn)
                .values(resume_enqueued_at=sa.func.now())
                .where(tables.turn.c.id == turn.id, tables.turn.c.status == PARKED)
            )
        options: EnqueueOptions = {
            "queue_name": TURN_QUEUE_NAME,
            "workflow_name": TURN_WORKFLOW_NAME,
            "workflow_id": uuid4().hex,
            "queue_partition_key": str(turn.conversation_id),
            "app_version": DBOS_APP_VERSION,
        }
        await self.client.enqueue_async(options, str(turn.id))


@dataclass(frozen=True)
class SandboxReaper:
    """Reclaim the disposable container behind each idle conversation. The workspace is the truth
    and the container is cache (§Sandboxing): a conversation whose most recent turn settled longer
    ago than the idle TTL, with no turn still in flight, has its sandbox destroyed through the
    carrier seam — the next turn recreates it from the same durable workspace and notices only
    latency. A batch-at-interval sweep, never fired by a turn it reclaims; the carrier's destroy is
    idempotent, so a container already gone (or one the local carrier never held) is a no-op, and a
    still-idle conversation re-selected on the next sweep costs one such no-op. The reaper holds
    only conversation identity — the durable key create-or-attach reuses — so it reaps by
    conversation, never by the ephemeral container id no durable row carries."""

    carrier: Carrier

    async def run(self) -> None:
        for conversation_id in await self._idle_conversations():
            if await self._now_active(conversation_id):
                continue
            await self.carrier.destroy(
                SandboxHandle(conversation_id=conversation_id, container_id="")
            )

    async def _now_active(self, conversation_id: UUID) -> bool:
        """A fresh point check immediately before destroy: has the conversation admitted an
        in-flight turn since the idle snapshot? The snapshot and the out-of-band carrier `destroy`
        don't share a transaction, so a turn admitted between them would otherwise have its
        just-created sandbox reaped mid-run, degrading that turn's tool calls. This re-check shrinks
        the window to the check-to-destroy gap; the negligible residual self-heals — the next turn's
        create-or-attach rebuilds the container from the durable workspace."""
        async with workspace_tx() as connection:
            found = (
                await connection.execute(
                    sa.select(tables.turn.c.id)
                    .where(
                        tables.turn.c.conversation_id == conversation_id,
                        tables.turn.c.status.in_(NON_TERMINAL_STATUSES),
                    )
                    .limit(1)
                )
            ).first()
        return found is not None

    async def _idle_conversations(self) -> tuple[UUID, ...]:
        cutoff = datetime.now(UTC) - timedelta(seconds=SANDBOX_IDLE_TTL_SECONDS)
        in_flight = (
            sa.select(tables.turn.c.conversation_id)
            .where(tables.turn.c.status.in_(NON_TERMINAL_STATUSES))
            .distinct()
        )
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.turn.c.conversation_id)
                    .where(tables.turn.c.conversation_id.notin_(in_flight))
                    .group_by(tables.turn.c.conversation_id)
                    .having(sa.func.max(tables.turn.c.updated_at) < cutoff)
                )
            ).all()
        return tuple(row.conversation_id for row in rows)


def core_jobs(
    sync_driver: SyncDriver,
    spend_resume: SpendResume,
    reaper: SandboxReaper,
) -> tuple[JobSpec, ...]:
    """The jobs a deploy always runs, before any extension's — all core because the source pipeline,
    spend enforcement, and sandbox lifecycle are core. The sync driver polls each source and lands
    its pages (which the memory extension's page-index job then reads through the PageFeed); the
    spend-resume sweep re-admits parked turns their caps now allow; the sandbox reaper destroys the
    disposable container behind each idle conversation through the carrier seam. None fires on its
    own writes. (Both memory-item and page indexing are the memory extension's jobs.)"""

    async def _sync_sources(context: ExtensionContext) -> None:
        await sync_driver.run()

    async def _resume_spend(context: ExtensionContext) -> None:
        await spend_resume.run()

    async def _reap_sandboxes(context: ExtensionContext) -> None:
        await reaper.run()

    return (
        JobSpec(name=SOURCE_SYNC_JOB, schedule=SOURCE_SYNC_SCHEDULE, handler=_sync_sources),
        JobSpec(name=SPEND_RESUME_JOB, schedule=SPEND_RESUME_SCHEDULE, handler=_resume_spend),
        JobSpec(name=SANDBOX_REAP_JOB, schedule=SANDBOX_REAP_SCHEDULE, handler=_reap_sandboxes),
    )


@dataclass(frozen=True)
class _Binding:
    key: str
    extension: str
    declared: frozenset[str]
    spec: JobSpec


def bindings_from(
    manifests: tuple[Manifest, ...], core_jobs: tuple[JobSpec, ...]
) -> tuple[_Binding, ...]:
    """Core jobs live in the `core` namespace with no declared slots; each extension's jobs live in
    its own namespace with exactly the credential slots that extension declared."""
    bindings = [
        _Binding(
            key=f"{CORE_EXTENSION}:{spec.name}",
            extension=CORE_EXTENSION,
            declared=frozenset(),
            spec=spec,
        )
        for spec in core_jobs
    ]
    for manifest in manifests:
        declared = frozenset(slot.name for slot in manifest.credentials)
        bindings.extend(
            _Binding(
                key=f"{manifest.name}:{spec.name}",
                extension=manifest.name,
                declared=declared,
                spec=spec,
            )
            for spec in manifest.jobs
        )
    return tuple(bindings)


@dataclass(frozen=True)
class JobRunner:
    """Boot registration for one workspace: publish the firing table, then register each cron job
    and enqueue each one-shot. `fire` is the per-execution dispatch the durable workflow calls."""

    workspace_id: UUID
    credential_store: CredentialStore
    bindings: tuple[_Binding, ...]
    index: IndexBackend | None = None
    embed: EmbedClient | None = None
    pages: PageFeed | None = None
    blob: BlobStore | None = None
    invoker: TurnInvoker | None = None

    def launch(self) -> None:
        global _firing
        _firing = self
        schedules: list[ScheduleInput] = []
        for binding in self.bindings:
            if binding.spec.schedule is None:
                JOB_QUEUE.enqueue(job_workflow, datetime.now(UTC), binding.key)
                log("jobs.enqueued", key=binding.key)
            else:
                schedules.append(
                    ScheduleInput(
                        schedule_name=binding.key,
                        workflow_fn=job_workflow,
                        schedule=binding.spec.schedule,
                        context=binding.key,
                        queue_name=JOB_QUEUE_NAME,
                    )
                )
                log("jobs.scheduled", key=binding.key, schedule=binding.spec.schedule)
        if schedules:
            DBOS.apply_schedules(schedules)

    async def fire(self, key: str) -> None:
        binding = next((b for b in self.bindings if b.key == key), None)
        if binding is None:
            raise RuntimeError(f"no job registered for key {key!r}")
        context = context_for(
            self.workspace_id,
            binding.extension,
            binding.declared,
            self.credential_store,
            self.index,
            self.embed,
            self.pages,
            self.blob,
            self.invoker,
        )
        await binding.spec.handler(context)


_firing: JobRunner | None = None


@DBOS.workflow(name=JOB_WORKFLOW_NAME)
async def job_workflow(scheduled_time: datetime, key: str) -> None:
    runner = _firing
    if runner is None:
        raise RuntimeError("jobs not registered (JobRunner.launch runs in serve)")
    await runner.fire(key)
