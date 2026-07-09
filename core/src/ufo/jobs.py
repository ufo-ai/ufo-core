"""The jobs role: discovered JobSpecs (core + extensions) become live DBOS work at boot.

Registration is dynamic — `DBOS.apply_schedules` for cron jobs, an enqueue for one-shots — not the
import-time `@DBOS.scheduled` decorator, because jobs are discovered from the installed extensions
at boot, not known when this module is imported; `apply_schedules` upserts, so re-registration on
every restart is idempotent. Registration is the synchronous DBOS API: the async variants repoint
the running loop's default executor at DBOS's shared pool, so a short-lived boot loop closing would
shut that pool down. One durable workflow fires each handler with the extension's scoped
ExtensionContext, so a core job and an extension job run the identical path."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOS, DBOSClient, EnqueueOptions, Queue, ScheduleInput

from ufo.accounting import ALLOW, SpendEvaluator
from ufo.blob import BlobStore
from ufo.credentials import CredentialStore
from ufo.db import owner_tx, workspace_tx
from ufo.ext.context import ExtensionContext, TurnInvoker, context_for
from ufo.ext.manifest import HookContext, HookSpec, JobSpec, Manifest, PageChangeBatch
from ufo.indexing import EmbedClient, IndexBackend
from ufo.models.registry import ModelRegistry
from ufo.o11y import log
from ufo.sandbox.session import Carrier, SandboxHandle, sandbox_handle_id
from ufo.schema import tables
from ufo.schema.records import (
    DBOS_APP_VERSION,
    NON_TERMINAL_STATUSES,
    PARKED,
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
)
from ufo.sources.sync import (
    SOURCE_SYNC_JOB,
    SOURCE_SYNC_SCHEDULE,
    PageFeed,
    SyncDriver,
)
from ufo.workspace import ws

JOB_QUEUE_NAME = "jobs"
JOB_WORKFLOW_NAME = "job"
CORE_EXTENSION = "core"
SPEND_RESUME_JOB = "spend_resume"
SPEND_RESUME_SCHEDULE = "0 * * * * *"
RESUME_ENQUEUE_GRACE_SECONDS = 300
SANDBOX_REAP_JOB = "sandbox_reap"
SANDBOX_REAP_SCHEDULE = "0 */10 * * * *"
SANDBOX_IDLE_TTL_SECONDS = 1800
PAGE_CHANGE_JOB = "page_change"
PAGE_CHANGE_SCHEDULE = "0 * * * * *"
PAGE_CHANGE_CURSOR_KEY = "page_change_cursor"
PAGE_CHANGE_BATCH = 50
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
    orphaning the turn.

    A cross-workspace sweep: it enumerates parked turns across every workspace through `owner_tx`
    (the one RLS-bypass read), then binds each turn's own workspace with `with ws(...)` before it
    decides the cap and enqueues — so the decision reads that workspace's spend and the resume is
    placed on that workspace's partition, exactly as a turn would. On a per-tenant deploy `owner_tx`
    enumerates the single workspace and the scope binds it, unchanged."""

    client: DBOSClient

    async def run(self) -> None:
        for turn in await self._parked_turns():
            with ws(turn.workspace_id):
                async with workspace_tx() as connection:
                    decision = await SpendEvaluator(
                        turn.workspace_id, turn.member_id, turn.agent_id
                    ).decide(connection, 0)
                if decision.outcome == ALLOW:
                    await self._enqueue(turn)

    async def _parked_turns(self) -> tuple[_ParkedTurn, ...]:
        cutoff = datetime.now(UTC) - timedelta(seconds=RESUME_ENQUEUE_GRACE_SECONDS)
        async with owner_tx() as connection:
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
        await self.client.enqueue_async(options, str(turn.workspace_id), str(turn.id))


@dataclass(frozen=True)
class SandboxReaper:
    """Reclaim the disposable container behind each idle conversation. The workspace is the truth
    and the container is cache (§Sandboxing): a conversation whose most recent turn settled longer
    ago than the idle TTL, with no turn still in flight, has its sandbox destroyed through the
    carrier seam — the next turn recreates it from the same durable workspace and notices only
    latency. A batch-at-interval sweep, never fired by a turn it reclaims; the carrier's destroy is
    idempotent, so a container already gone is a no-op, and a still-idle conversation re-selected on
    the next sweep costs one such no-op.

    The durable source of truth is the conversation's `sandbox_handle` (`<backend>:<id>`), not an
    in-process map, so the reaper reclaims a sandbox this or any PRIOR process created: it reaps by
    conversation identity plus the stored id, then clears the row so a reaped sandbox is never
    resumed into a dead (docker) or released (e2b pause) id — the next turn creates fresh. A handle
    another backend wrote (a deploy that switched carriers) is not this carrier's to reap and is
    skipped.

    A cross-workspace sweep: it enumerates idle conversations across every workspace through
    `owner_tx` (the one RLS-bypass read), then binds each conversation's own workspace with `with
    ws(...)` for the in-flight re-check and the row clear — so a reap is scoped exactly as that
    workspace's turn would scope it. On a per-tenant deploy `owner_tx` enumerates the single
    workspace, unchanged."""

    carrier: Carrier
    backend: str

    async def run(self) -> None:
        for workspace_id, conversation_id, stored in await self._idle_sandboxes():
            container_id = sandbox_handle_id(self.backend, stored)
            if container_id is None:
                continue
            with ws(workspace_id):
                if await self._now_active(conversation_id):
                    continue
                await self.carrier.destroy(
                    SandboxHandle(conversation_id=conversation_id, container_id=container_id)
                )
                await self._clear(conversation_id)

    async def _clear(self, conversation_id: UUID) -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.conversation)
                .values(sandbox_handle=None)
                .where(tables.conversation.c.id == conversation_id)
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

    async def _idle_sandboxes(self) -> tuple[tuple[UUID, UUID, str], ...]:
        """Conversations across every workspace carrying a persisted sandbox handle whose most
        recent turn settled past the idle TTL and which have no turn in flight — the durable handle,
        not an in-process map, is the set the reaper reclaims from, so a sandbox a prior process
        created is in scope. Enumerated through `owner_tx` (RLS bypass) so one sweep reclaims across
        the fleet; each row carries its `workspace_id` so `run` re-binds it before the reap."""
        cutoff = datetime.now(UTC) - timedelta(seconds=SANDBOX_IDLE_TTL_SECONDS)
        in_flight = (
            sa.select(tables.turn.c.conversation_id)
            .where(tables.turn.c.status.in_(NON_TERMINAL_STATUSES))
            .distinct()
        )
        async with owner_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.conversation.c.workspace_id,
                        tables.turn.c.conversation_id,
                        tables.conversation.c.sandbox_handle,
                    )
                    .select_from(tables.turn.join(tables.conversation))
                    .where(
                        tables.conversation.c.sandbox_handle.is_not(None),
                        tables.turn.c.conversation_id.notin_(in_flight),
                    )
                    .group_by(
                        tables.conversation.c.workspace_id,
                        tables.turn.c.conversation_id,
                        tables.conversation.c.sandbox_handle,
                    )
                    .having(sa.func.max(tables.turn.c.updated_at) < cutoff)
                )
            ).all()
        return tuple((row.workspace_id, row.conversation_id, row.sandbox_handle) for row in rows)


@dataclass(frozen=True)
class PageChangeConsumer:
    """One registered `page_change` hook and where its cursor and context are scoped: the declaring
    extension's name, the credential slots it declared, the hook spec, and the discriminator that
    keeps two hooks in one extension independent — the handler's own `__name__`, since page_change
    handlers are plain module-level functions. `core_jobs` names one JobSpec
    `page_change:{extension}:{discriminator}` per consumer and `drive` rides a
    `{PAGE_CHANGE_CURSOR_KEY}:{discriminator}` cursor, so each drives as its own DBOS workflow off
    its own cursor — a backlogged or wedged consumer delays only itself, and an extension that
    registers two page_change hooks (the memory indexer and its fact deriver) never collides on
    JobSpec name or cursor key."""

    extension: str
    declared: frozenset[str]
    spec: HookSpec
    discriminator: str


@dataclass(frozen=True)
class PageChangeRunner:
    """The core batched cursor-runner behind the data-plane `page_change` hook. `consumers` reads
    the registered page_change hooks out of the active manifests; `core_jobs` registers one JobSpec
    per consumer, so each `drive` runs as its own per-minute DBOS workflow — the memory page indexer
    and the knowledge-graph extractor are independent failure domains again, a backlog or a raise in
    one never delaying or blocking the other. `drive` is the one home for the cursor loop every page
    consumer shares: it replays each source page changed since that consumer's own cursor and hands
    the batch to its handler, then advances and persists the cursor. Each cursor lives in that
    extension's own ScopedStore under a per-consumer key
    (`{PAGE_CHANGE_CURSOR_KEY}:{discriminator}`), so two hooks in one extension keep independent
    cursors and a restart resumes each exactly where it left off; the handlers stay idempotent, so a
    replayed batch settles on the same state. Each handler runs with the extension's scoped
    ExtensionContext built the jobs way — the model wired — so a consumer like graph extraction's
    Tier-B pass reaches ctx.model. A
    handler that raises propagates out of `drive` (failing that one workflow) before its cursor
    advances, so the tick makes no progress and the next tick retries from the same place.
    Batch-at-interval and fed only by the source pipeline, so it can never fire on the derived rows
    a handler writes.

    A cross-workspace sweep: `drive` enumerates every workspace holding pages through `owner_tx`
    (the one RLS-bypass read), then binds each with `with ws(...)` and runs that consumer's cursor
    loop scoped to it — the page feed reads that workspace's pages, the cursor lives in that
    workspace's ScopedStore, so one consumer's per-minute workflow replays the fleet with each
    workspace resuming independently. On a per-tenant deploy `owner_tx` enumerates the single
    workspace, unchanged."""

    credential_store: CredentialStore
    manifests: tuple[Manifest, ...]
    pages: PageFeed
    index: IndexBackend | None = None
    embed: EmbedClient | None = None
    blob: BlobStore | None = None
    invoker: TurnInvoker | None = None
    registry: ModelRegistry | None = None

    def consumers(self) -> tuple[PageChangeConsumer, ...]:
        consumers: list[PageChangeConsumer] = []
        seen: set[tuple[str, str]] = set()
        for manifest in self.manifests:
            declared = frozenset(slot.name for slot in manifest.credentials)
            for spec in manifest.hooks:
                if spec.event != "page_change":
                    continue
                discriminator = spec.handler.__name__
                if (manifest.name, discriminator) in seen:
                    raise RuntimeError(
                        f"two page_change hooks in extension {manifest.name!r} share the "
                        f"discriminator {discriminator!r} (handler __name__); give the handlers "
                        "distinct function names so each keys its own JobSpec and cursor"
                    )
                seen.add((manifest.name, discriminator))
                consumers.append(
                    PageChangeConsumer(
                        extension=manifest.name,
                        declared=declared,
                        spec=spec,
                        discriminator=discriminator,
                    )
                )
        return tuple(consumers)

    async def drive(self, consumer: PageChangeConsumer) -> None:
        for workspace_id in await self._workspaces_with_pages():
            with ws(workspace_id):
                await self._drive_workspace(consumer)

    async def _workspaces_with_pages(self) -> tuple[UUID, ...]:
        """Every workspace holding at least one page — the set a consumer replays over. Enumerated
        through `owner_tx` (RLS bypass) so one workflow drives the fleet; a workspace with no pages
        has nothing to replay and is skipped."""
        async with owner_tx() as connection:
            rows = (
                await connection.execute(sa.select(tables.page.c.workspace_id).distinct())
            ).all()
        return tuple(row.workspace_id for row in rows)

    async def _drive_workspace(self, consumer: PageChangeConsumer) -> None:
        context = self._context_for(consumer.extension, consumer.declared)
        cursor_key = f"{PAGE_CHANGE_CURSOR_KEY}:{consumer.discriminator}"
        stored = await context.store.get(cursor_key)
        cursor = stored if isinstance(stored, str) else None
        while True:
            batch = await self.pages.pages_changed_since(cursor, PAGE_CHANGE_BATCH)
            if not batch.changes:
                return
            await consumer.spec.handler(
                HookContext(ext=context, payload=PageChangeBatch(changes=batch.changes))
            )
            cursor = batch.next_cursor
            await context.store.put(cursor_key, cursor)
            if len(batch.changes) < PAGE_CHANGE_BATCH:
                return

    def _context_for(self, extension: str, declared: frozenset[str]) -> ExtensionContext:
        return context_for(
            extension,
            declared,
            self.index,
            self.embed,
            self.pages,
            self.blob,
            self.invoker,
            self.registry,
        )


def core_jobs(
    sync_driver: SyncDriver,
    spend_resume: SpendResume,
    reaper: SandboxReaper,
    page_change_runner: PageChangeRunner,
) -> tuple[JobSpec, ...]:
    """The jobs a deploy always runs, before any extension's — all core because the source pipeline,
    spend enforcement, sandbox lifecycle, and the page-change fan-out are core. The sync driver
    polls each source and lands its pages; the page-change runner contributes one
    `page_change:<ext>:<hook>` job per registered consumer, each replaying those pages to that
    consumer's hook off its own cursor as its own workflow (the memory page indexer, the memory
    fact deriver, and the graph extractor among them); the spend-resume sweep re-admits parked
    turns their caps now allow; the sandbox
    reaper destroys the disposable container behind each idle conversation through the carrier seam.
    None fires on its own writes. (Memory-item indexing stays the memory extension's own job; page
    derivation is a page_change hook this runner drives.)"""

    async def _sync_sources(context: ExtensionContext) -> None:
        await sync_driver.run()

    async def _resume_spend(context: ExtensionContext) -> None:
        await spend_resume.run()

    async def _reap_sandboxes(context: ExtensionContext) -> None:
        await reaper.run()

    def _drive_consumer(
        consumer: PageChangeConsumer,
    ) -> Callable[[ExtensionContext], Awaitable[None]]:
        async def _handler(context: ExtensionContext) -> None:
            await page_change_runner.drive(consumer)

        return _handler

    page_change = tuple(
        JobSpec(
            name=f"{PAGE_CHANGE_JOB}:{consumer.extension}:{consumer.discriminator}",
            schedule=PAGE_CHANGE_SCHEDULE,
            handler=_drive_consumer(consumer),
        )
        for consumer in page_change_runner.consumers()
    )
    return (
        JobSpec(name=SOURCE_SYNC_JOB, schedule=SOURCE_SYNC_SCHEDULE, handler=_sync_sources),
        *page_change,
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
    registry: ModelRegistry | None = None

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
        with ws(self.workspace_id):
            context = context_for(
                binding.extension,
                binding.declared,
                self.index,
                self.embed,
                self.pages,
                self.blob,
                self.invoker,
                self.registry,
            )
            await binding.spec.handler(context)


_firing: JobRunner | None = None


@DBOS.workflow(name=JOB_WORKFLOW_NAME)
async def job_workflow(scheduled_time: datetime, key: str) -> None:
    runner = _firing
    if runner is None:
        raise RuntimeError("jobs not registered (JobRunner.launch runs in serve)")
    await runner.fire(key)
