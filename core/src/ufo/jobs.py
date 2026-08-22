"""The jobs role: discovered JobSpecs (core + extensions) become live DBOS work at boot.

Registration is dynamic — `DBOS.apply_schedules` for cron jobs, an enqueue for one-shots — not the
import-time `@DBOS.scheduled` decorator, because jobs are discovered from the installed extensions
at boot, not known when this module is imported; `apply_schedules` upserts, so re-registration on
every restart is idempotent, and a schedule outliving the job that wrote it is skipped by `tick`
rather than deleted — one schedule table serves every process, and none of them can tell which
peer's code version owns a key. Registration is the synchronous DBOS API: the async variants repoint
the running loop's default executor at DBOS's shared pool, so a short-lived boot loop closing would
shut that pool down. Two durable workflows carry every fire: `job_tick` fans out one queued
`job_workflow` per candidate workspace, deduplicated on (job key, workspace) held from enqueue to
terminal — a workspace still running its previous execution absorbs the tick alone, never stacked,
never stalling its neighbors, and twin replica boots start a one-shot once — and each
`job_workflow` runs exactly one workspace's handler through the extension's scoped
ExtensionContext, so a core job and an extension job ride the identical path. `JOB_QUEUE` runs at
most `JOB_WORKER_CONCURRENCY` `job_workflow` executions per process, bounded backpressure in
Postgres, never a thread bloom; the tick itself rides DBOS's internal queue — one durable insert
per candidate workspace, milliseconds — so a tick never waits behind a slow job for a worker
slot."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOS, DBOSClient, EnqueueOptions, Queue, ScheduleInput, SetEnqueueOptions
from dbos import error as dbos_error

from ufo.accounting import ALLOW, BalanceGate, SpendEvaluator
from ufo.blob import WorkspaceBlobStore
from ufo.candidates import WorkspaceCandidates
from ufo.db import owner_tx, workspace_tx
from ufo.ext.context import ConversationProbes, ExtensionContext, TurnInvoker, context_for
from ufo.ext.manifest import (
    PAGE_CHANGE_CURSOR_KEY,
    HookContext,
    HookSpec,
    JobSpec,
    Manifest,
    PageChangeBatch,
)
from ufo.indexing import EmbedClient, IndexBackend
from ufo.models.registry import ModelRegistry
from ufo.o11y import emit_metric, formatted_stack, log, log_error, warn
from ufo.preview_renderer import PreviewRenderer
from ufo.provisioning import AgentProvisioning
from ufo.sandbox.conversation import ConversationSandbox
from ufo.schema import tables
from ufo.schema.records import (
    DBOS_APP_VERSION,
    PARKED,
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
    TurnAdmissionSource,
    TurnStatus,
)
from ufo.seats import Seats, gate_member
from ufo.sources.sync import (
    SOURCE_SYNC_JOB,
    SOURCE_SYNC_SCHEDULE,
    PageFeed,
    SyncDriver,
    page_cursor,
)
from ufo.workspace import ws, ws_current


class ResultDeliverer(Protocol):
    """The subagent hand-back sweep as this role sees it. The work is the turn loop's — it reads a
    finished child and posts its arrival — so it lives there, and the jobs role names and schedules
    it through this structural type rather than importing across the boundary."""

    async def run(self) -> None: ...

    async def candidate_workspaces(self) -> tuple[UUID, ...]: ...


InvokerFactory = Callable[[UUID], TurnInvoker]

JOB_QUEUE_NAME = "jobs"
JOB_WORKFLOW_NAME = "job"
JOB_TICK_WORKFLOW_NAME = "job_tick"
CORE_EXTENSION = "core"
TURN_DISPATCH_JOB = "turn_dispatch"
TURN_DISPATCH_SCHEDULE = "0 * * * * *"
TURN_DISPATCH_GRACE_SECONDS = 300
TURN_DISPATCH_BATCH_TURNS = 100
RESULT_DELIVERY_JOB = "result_delivery"
RESULT_DELIVERY_SCHEDULE = "0 * * * * *"
PAGE_CHANGE_JOB = "page_change"
PAGE_CHANGE_SCHEDULE = "0 * * * * *"
RENDER_PREVIEWS_JOB = "render_previews"
RENDER_PREVIEWS_SCHEDULE = "0 * * * * *"
PAGE_CHANGE_BATCH = 50
JOB_WORKER_CONCURRENCY = 8
JOB_QUEUE = Queue(JOB_QUEUE_NAME, worker_concurrency=JOB_WORKER_CONCURRENCY)
QUEUED: TurnStatus = "queued"


@dataclass(frozen=True, slots=True)
class _DispatchTurn:
    id: UUID
    workspace_id: UUID
    conversation_id: UUID
    agent_id: UUID
    member_id: UUID | None
    speaker_member_id: UUID | None
    on_behalf_of_member_id: UUID | None
    admission_source: TurnAdmissionSource
    status: TurnStatus


@dataclass(frozen=True)
class TurnDispatcher:
    """Dispatch durable turn rows onto the conversation-partitioned worker queue. QUEUED is an
    outbox state: admission stamps and offers the first turn immediately, while this bounded sweep
    recovers an unstamped or stale offer. Only the lowest-sequence QUEUED turn in a conversation is
    eligible, so a later turn cannot overtake an earlier offer that has not started. A never-claimed
    QUEUED turn's DBOS workflow id is the turn id, making an ambiguous duplicate offer safe.

    PARKED rows share the same scanner and advisory dispatch stamp, but remain spend-, balance-
    and seat-gated: a parked turn stays held while its founder, scheduled creator, or any pending
    absorbed speaker holds no seat, and resumes when every one is seated. A row
    that has ever been claimed — a PARKED one, or a QUEUED one a fold resumed from park — needs a
    fresh DBOS workflow id because the run that claimed it consumed its original id; the choice
    reads `running_attempt` from the stamping update itself, so a claim-park-requeue racing the
    sweep's scan cannot ride a spent id. The worker claim keeps a duplicate fresh-id offer safe,
    and clears the stamp for either state. A stamp
    set before an external enqueue and left behind by a process failure becomes eligible again
    after the grace window.

    `candidate_workspaces` is the only fleet-wide owner read. `run` executes inside each returned
    workspace through RLS, claims at most `dispatch_batch` rows, and offers only rows whose stale
    stamp it atomically replaces. Concurrent sweepers therefore cannot both make a fresh offer."""

    client: DBOSClient
    dispatch_batch: int = TURN_DISPATCH_BATCH_TURNS
    key_slot_for: Callable[[str], str | None] | None = None

    async def run(self) -> None:
        for turn in await self._dispatchable_turns():
            if turn.status == PARKED:
                async with workspace_tx() as connection:
                    gate = gate_member(turn.speaker_member_id, turn.on_behalf_of_member_id)
                    members = {gate} if gate is not None else set()
                    members.update(
                        (
                            await connection.execute(
                                sa.select(tables.inbound_message.c.speaker_member_id)
                                .where(
                                    tables.inbound_message.c.workspace_id == turn.workspace_id,
                                    tables.inbound_message.c.conversation_id
                                    == turn.conversation_id,
                                    tables.inbound_message.c.consumed_turn_id.is_(None),
                                    tables.inbound_message.c.speaker_member_id.is_not(None),
                                )
                                .distinct()
                            )
                        )
                        .scalars()
                        .all()
                    )
                    seated = await Seats(turn.workspace_id).all_seated(
                        connection, [member for member in members if member is not None]
                    )
                    decision = await SpendEvaluator(
                        turn.workspace_id, turn.member_id, turn.agent_id
                    ).decide(connection, 0)
                    balance = await BalanceGate(turn.workspace_id).admits(
                        connection, turn.agent_id, self.key_slot_for, turn.id
                    )
                if not seated or decision.outcome != ALLOW or balance.outcome != ALLOW:
                    continue
            await self._enqueue(turn)

    async def candidate_workspaces(self) -> tuple[UUID, ...]:
        cutoff = datetime.now(UTC) - timedelta(seconds=TURN_DISPATCH_GRACE_SECONDS)
        async with owner_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.turn.c.workspace_id).where(self._eligible(cutoff)).distinct()
                )
            ).all()
        return tuple(row.workspace_id for row in rows)

    async def _dispatchable_turns(self) -> tuple[_DispatchTurn, ...]:
        cutoff = datetime.now(UTC) - timedelta(seconds=TURN_DISPATCH_GRACE_SECONDS)
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.id,
                        tables.turn.c.workspace_id,
                        tables.turn.c.conversation_id,
                        tables.turn.c.agent_id,
                        tables.conversation.c.member_id,
                        tables.turn.c.speaker_member_id,
                        tables.turn.c.on_behalf_of_member_id,
                        tables.turn.c.admission_source,
                        tables.turn.c.status,
                    )
                    .select_from(tables.turn.join(tables.conversation))
                    .where(self._eligible(cutoff))
                    .order_by(
                        sa.case((tables.turn.c.status == QUEUED, 0), else_=1),
                        tables.turn.c.created_at,
                        tables.turn.c.seq,
                    )
                    .limit(self.dispatch_batch)
                )
            ).all()
        return tuple(
            _DispatchTurn(
                r.id,
                r.workspace_id,
                r.conversation_id,
                r.agent_id,
                r.member_id,
                r.speaker_member_id,
                r.on_behalf_of_member_id,
                r.admission_source,
                r.status,
            )
            for r in rows
        )

    async def _enqueue(self, turn: _DispatchTurn) -> None:
        cutoff = datetime.now(UTC) - timedelta(seconds=TURN_DISPATCH_GRACE_SECONDS)
        order_guard = self._first_in_status(turn.status)
        async with workspace_tx() as connection:
            claimed = (
                await connection.execute(
                    sa.update(tables.turn)
                    .values(dispatch_enqueued_at=sa.func.now(), updated_at=sa.func.now())
                    .where(
                        tables.turn.c.id == turn.id,
                        tables.turn.c.status == turn.status,
                        self._stale(cutoff),
                        order_guard,
                    )
                    .returning(tables.turn.c.id, tables.turn.c.running_attempt)
                )
            ).one_or_none()
        if claimed is None:
            return
        options: EnqueueOptions = {
            "queue_name": TURN_QUEUE_NAME,
            "workflow_name": TURN_WORKFLOW_NAME,
            "workflow_id": (
                str(turn.id)
                if turn.status == QUEUED and claimed.running_attempt is None
                else uuid4().hex
            ),
            "queue_partition_key": str(turn.conversation_id),
            "app_version": DBOS_APP_VERSION,
        }
        await self.client.enqueue_async(options, str(turn.workspace_id), str(turn.id))

    def _eligible(self, cutoff: datetime) -> sa.ColumnElement[bool]:
        return sa.and_(
            tables.turn.c.status.in_((QUEUED, PARKED)),
            self._stale(cutoff),
            sa.or_(
                sa.and_(tables.turn.c.status == QUEUED, self._first_in_status(QUEUED)),
                sa.and_(tables.turn.c.status == PARKED, self._first_in_status(PARKED)),
            ),
        )

    def _stale(self, cutoff: datetime) -> sa.ColumnElement[bool]:
        return sa.or_(
            tables.turn.c.dispatch_enqueued_at.is_(None),
            tables.turn.c.dispatch_enqueued_at < cutoff,
        )

    def _first_in_status(self, status: TurnStatus) -> sa.ColumnElement[bool]:
        earlier = tables.turn.alias(f"earlier_{status}_turn")
        return ~sa.exists(
            sa.select(earlier.c.id).where(
                earlier.c.workspace_id == tables.turn.c.workspace_id,
                earlier.c.conversation_id == tables.turn.c.conversation_id,
                earlier.c.status == status,
                earlier.c.seq < tables.turn.c.seq,
            )
        )


def _page_beyond_cursor(revision: int, page_id: UUID, cursor: object) -> bool:
    """Whether a page at `(revision, page_id)` lies past a `page_change` cursor."""
    if cursor is None:
        return True
    boundary_revision, boundary_id = page_cursor(cursor)
    return revision > boundary_revision or (revision == boundary_revision and page_id > boundary_id)


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

    @property
    def spec_name(self) -> str:
        """The name `core_jobs` gives this consumer's own JobSpec."""
        return f"{PAGE_CHANGE_JOB}:{self.extension}:{self.discriminator}"

    @property
    def job(self) -> str:
        """That spec's binding key — in the `core` namespace, because the runner driving it is a
        core job whatever extension declared the hook. What this consumer's model spend and latency
        are attributed to on the `ufo.model_*` series, so the fact deriver's distillation reads
        apart from the page indexer beside it."""
        return f"{CORE_EXTENSION}:{self.spec_name}"


@dataclass(frozen=True)
class PageChangeRunner:
    """The core batched cursor-runner behind the data-plane `page_change` hook. `consumers` reads
    the registered page_change hooks out of the active manifests; `core_jobs` registers one JobSpec
    per consumer, so each `drive` runs as its own per-minute DBOS workflow — the memory page indexer
    and the fact deriver are independent failure domains, a backlog or a raise in one never delaying
    or blocking the other. `drive` is the one home for the cursor loop every page
    consumer shares: it replays each source page changed since that consumer's own cursor and hands
    the batch to its handler, then advances and persists the cursor. Each cursor lives in that
    extension's own ScopedStore under a per-consumer key
    (`{PAGE_CHANGE_CURSOR_KEY}:{discriminator}`), so two hooks in one extension keep independent
    cursors and a restart resumes each exactly where it left off; the handlers stay idempotent, so a
    replayed batch settles on the same state. Each handler runs with the extension's scoped
    ExtensionContext built the jobs way — the model wired, on `background_model` — so a consumer
    like the fact deriver's distillation pass reaches ctx.model. A
    handler that raises propagates out of `drive` (failing that one workflow) before its cursor
    advances, so the tick makes no progress and the next tick retries from the same place.
    Batch-at-interval and fed only by the source pipeline, so it can never fire on the derived rows
    a handler writes.

    A selective cross-workspace sweep: the dispatcher names the candidate workspaces through
    `workspaces_with_changes` (one `owner_tx` read, the RLS-bypass path, taking only those whose
    high-water page lies beyond this consumer's cursor) and binds each, so `drive` runs that
    consumer's cursor loop scoped to the bound workspace — the page feed reads that workspace's
    pages, the cursor lives in that workspace's ScopedStore, so one consumer's per-minute workflow
    replays the fleet with each workspace resuming independently. A workspace with nothing changed
    since its cursor is never bound. On a per-tenant deploy `owner_tx` resolves to the single
    workspace, unchanged."""

    manifests: tuple[Manifest, ...]
    pages: PageFeed
    invoker_factory: InvokerFactory | None = None
    index: IndexBackend | None = None
    embed: EmbedClient | None = None
    blob: WorkspaceBlobStore | None = None
    sandboxes: ConversationSandbox | None = None
    registry: ModelRegistry | None = None
    probes: ConversationProbes | None = None
    background_model: str | None = None

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

    async def workspaces_with_changes(self, consumer: PageChangeConsumer) -> tuple[UUID, ...]:
        """The workspaces this consumer has actual pending work in — those whose newest page lies
        beyond the consumer's own stored cursor. One `owner_tx` read (RLS bypass) takes each
        workspace's high-water page as a pair of correlated probes down the `page_feed` index (the
        maximum in the feed's `(revision, id)` order — one probe per workspace, never a scan of
        the page table) and each workspace's cursor for this consumer from `ext_store`; a workspace
        whose high-water page is at or before its cursor has nothing changed since it last drained
        and is never opened, while a workspace with no cursor yet (never driven) has every page
        pending. So a page-holding but change-free workspace runs no per-tick transaction. A
        workspace whose stored cursor fails to parse counts as pending rather than aborting this
        fleet-wide read — one workspace's unparseable cursor stays that workspace's `drive` failure,
        never blocking every other workspace's tick. On a per-tenant deploy `owner_tx` resolves to
        the single workspace, unchanged."""
        cursor_key = f"{PAGE_CHANGE_CURSOR_KEY}:{consumer.discriminator}"
        of_workspace = tables.page.c.workspace_id == tables.workspace.c.id
        newest_first = (tables.page.c.revision.desc(), tables.page.c.id.desc())
        newest_revision = (
            sa.select(tables.page.c.revision)
            .where(of_workspace)
            .order_by(*newest_first)
            .limit(1)
            .scalar_subquery()
        )
        newest_id = (
            sa.select(tables.page.c.id)
            .where(of_workspace)
            .order_by(*newest_first)
            .limit(1)
            .scalar_subquery()
        )
        async with owner_tx() as connection:
            cursor_rows = (
                await connection.execute(
                    sa.select(tables.ext_store.c.workspace_id, tables.ext_store.c.value).where(
                        tables.ext_store.c.extension == consumer.extension,
                        tables.ext_store.c.key == cursor_key,
                    )
                )
            ).all()
            page_rows = (
                await connection.execute(
                    sa.select(
                        tables.workspace.c.id.label("workspace_id"),
                        newest_revision.label("revision"),
                        newest_id.label("id"),
                    )
                )
            ).all()
        cursors = {row.workspace_id: row.value for row in cursor_rows}
        pending: list[UUID] = []
        for row in page_rows:
            if row.revision is None:
                continue
            try:
                beyond = _page_beyond_cursor(row.revision, row.id, cursors.get(row.workspace_id))
            except ValueError:
                warn(
                    "jobs.page_change_cursor_invalid",
                    workspace_id=str(row.workspace_id),
                    extension=consumer.extension,
                    discriminator=consumer.discriminator,
                )
                beyond = True
            if beyond:
                pending.append(row.workspace_id)
        return tuple(pending)

    async def drive(self, consumer: PageChangeConsumer) -> None:
        """Replay the workspace's pages changed since this consumer's cursor to its handler and
        advance the cursor — the dispatcher binds the workspace, so this runs scoped to it and never
        enumerates the fleet itself. The cursor advances by compare-and-set against the value this
        tick read, so an overlapping tick or an external writer that already moved it on is never
        rewound to an older place: losing that write means another writer owns the cursor, and this
        tick stops having only redone work a handler is idempotent under.

        A handler that raises leaves the cursor where it was, so the same batch is replayed on the
        next tick. That is right for a fault that passes and wrong for one that does not: a batch
        the handler can never accept holds every later page in the workspace behind it, and the
        replay is silent — one `jobs.failed` a minute reads exactly like a stream of unrelated
        blips. So each failure states which consumer stopped and where, and counts, and the counter
        is what a monitor reads: a fault that passes shows up once or twice, and one that does not
        keeps the count at the tick rate until somebody looks."""
        context = self._context_for(consumer)
        cursor_key = f"{PAGE_CHANGE_CURSOR_KEY}:{consumer.discriminator}"
        stored = await context.store.get(cursor_key)
        if stored is not None and not isinstance(stored, str):
            raise ValueError("page cursor must be a string")
        cursor = stored
        while True:
            batch = await self.pages.pages_changed_since(cursor, PAGE_CHANGE_BATCH)
            if not batch.changes:
                return
            try:
                await consumer.spec.handler(
                    HookContext(ext=context, payload=PageChangeBatch(changes=batch.changes))
                )
            except Exception as error:
                emit_metric(
                    "page_change_stalled_total",
                    extension=consumer.extension,
                    discriminator=consumer.discriminator,
                )
                log_error(
                    "jobs.page_change_stalled",
                    workspace_id=str(ws_current().workspace_id),
                    extension=consumer.extension,
                    discriminator=consumer.discriminator,
                    cursor=cursor or "",
                    pages=len(batch.changes),
                    error_class=type(error).__name__,
                    stack=formatted_stack(error),
                )
                raise
            if not await context.store.put_if(cursor_key, batch.next_cursor, expected=cursor):
                return
            cursor = batch.next_cursor
            if len(batch.changes) < PAGE_CHANGE_BATCH:
                return

    def _context_for(self, consumer: PageChangeConsumer) -> ExtensionContext:
        invoker = (
            None
            if self.invoker_factory is None
            else self.invoker_factory(ws_current().workspace_id)
        )
        return context_for(
            consumer.extension,
            consumer.declared,
            self.index,
            self.embed,
            self.pages,
            self.blob,
            self.sandboxes,
            invoker,
            _background_registry(self.registry, self.background_model),
            consumer.job,
            probes=self.probes,
        )


def _background_registry(
    registry: ModelRegistry | None, background_model: str | None
) -> ModelRegistry | None:
    """The registry a job's model seam resolves through: the deploy's own, with its default replaced
    by the configured background-jobs model — the same replacement core makes for the ambient-reply
    gate, and for the same reason. A job is one bounded one-shot over a payload nothing re-reads, so
    it reads no cache and pays full price per token; a member turn keeps the deploy default. The
    price table and every spec stay whole, so the model called is the model priced. No background
    model configured leaves the registry as it is."""
    if registry is None or background_model is None:
        return registry
    return replace(registry, auto_model=background_model)


def core_jobs(
    sync_driver: SyncDriver,
    turn_dispatcher: TurnDispatcher,
    page_change_runner: PageChangeRunner,
    delivery_sweep: ResultDeliverer,
    preview_renderer: PreviewRenderer | None,
) -> tuple[JobSpec, ...]:
    """The jobs a deploy always runs, before any extension's — all core because the source pipeline,
    spend enforcement, the page-change fan-out, and the subagent loop are core. The sync driver
    polls each source and lands its pages; the page-change runner contributes one
    `page_change:<ext>:<hook>` job per registered consumer, each replaying those pages to that
    consumer's hook off its own cursor as its own workflow (the memory page indexer and fact deriver
    among them); the turn dispatcher recovers queued outbox rows and re-admits parked turns their
    caps now allow; the delivery sweep hands back the delegated children whose own execution could
    not — a cancelled one above all, whose terminal is committed from outside it. None fires on its
    own writes. (Memory-item indexing stays the memory extension's own job; page derivation is a
    page_change hook this runner drives. Reclaiming a container is the carrier's own business, never
    core's — the workspace lives inside the sandbox, so only a carrier knows whether dropping its
    container takes the workspace with it.)"""

    async def _sync_sources(context: ExtensionContext) -> None:
        await sync_driver.run()

    async def _dispatch_turns(context: ExtensionContext) -> None:
        await turn_dispatcher.run()

    async def _deliver_results(context: ExtensionContext) -> None:
        await delivery_sweep.run()

    async def _render_previews(context: ExtensionContext) -> None:
        assert preview_renderer is not None
        await preview_renderer.run()

    async def _preview_candidates() -> tuple[UUID, ...]:
        assert preview_renderer is not None
        return await preview_renderer.candidate_workspaces()

    def _drive_consumer(
        consumer: PageChangeConsumer,
    ) -> Callable[[ExtensionContext], Awaitable[None]]:
        async def _handler(context: ExtensionContext) -> None:
            await page_change_runner.drive(consumer)

        return _handler

    def _consumer_candidates(consumer: PageChangeConsumer) -> WorkspaceCandidates:
        async def _candidates() -> tuple[UUID, ...]:
            return await page_change_runner.workspaces_with_changes(consumer)

        return _candidates

    page_change = tuple(
        JobSpec(
            name=consumer.spec_name,
            schedule=PAGE_CHANGE_SCHEDULE,
            handler=_drive_consumer(consumer),
            candidates=_consumer_candidates(consumer),
        )
        for consumer in page_change_runner.consumers()
    )
    return (
        JobSpec(
            name=SOURCE_SYNC_JOB,
            schedule=SOURCE_SYNC_SCHEDULE,
            handler=_sync_sources,
            candidates=sync_driver.candidate_workspaces,
        ),
        *page_change,
        JobSpec(
            name=TURN_DISPATCH_JOB,
            schedule=TURN_DISPATCH_SCHEDULE,
            handler=_dispatch_turns,
            candidates=turn_dispatcher.candidate_workspaces,
        ),
        JobSpec(
            name=RESULT_DELIVERY_JOB,
            schedule=RESULT_DELIVERY_SCHEDULE,
            handler=_deliver_results,
            candidates=delivery_sweep.candidate_workspaces,
        ),
        *(
            (
                JobSpec(
                    name=RENDER_PREVIEWS_JOB,
                    schedule=RENDER_PREVIEWS_SCHEDULE,
                    handler=_render_previews,
                    candidates=_preview_candidates,
                ),
            )
            if preview_renderer is not None
            else ()
        ),
    )


@dataclass(frozen=True)
class _Binding:
    key: str
    extension: str
    declared: frozenset[str]
    spec: JobSpec
    member_context_read: bool = False
    manifest: Manifest | None = None


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
                member_context_read=manifest.member_context_read,
                manifest=manifest,
            )
            for spec in manifest.jobs
        )
    return tuple(bindings)


@dataclass(frozen=True)
class JobRunner:
    """Boot registration for the deploy: publish the firing table, then register each cron job and
    enqueue each one-shot tick. `candidates` names the workspaces holding work (one `owner_tx`
    read); the tick fans them out and `fire` — the per-execution dispatch the durable workflow
    calls, the sole path a handler runs on — opens `with ws(id)` for its one workspace and runs the
    handler scoped to it. There is no branch that runs a handler outside a bound workspace — an
    empty candidate set enqueues zero executions, and every core and extension job rides the
    identical fan-and-bind, so an unbound handler call cannot exist. On a per-tenant deploy the
    candidate read resolves to the single workspace, unchanged.

    A handler's `ctx.model` runs on `background_model`, the deploy's background-jobs model — except
    for a job that declares `needs_deploy_model`, which keeps `auto_model`."""

    bindings: tuple[_Binding, ...]
    invoker_factory: InvokerFactory | None = None
    index: IndexBackend | None = None
    embed: EmbedClient | None = None
    pages: PageFeed | None = None
    blob: WorkspaceBlobStore | None = None
    sandboxes: ConversationSandbox | None = None
    registry: ModelRegistry | None = None
    probes: ConversationProbes | None = None
    background_model: str | None = None

    def launch(self) -> None:
        global _firing
        _firing = self
        schedules: list[ScheduleInput] = []
        for binding in self.bindings:
            if binding.spec.schedule is None:
                with SetEnqueueOptions(deduplication_id=binding.key):
                    try:
                        JOB_QUEUE.enqueue(job_tick, datetime.now(UTC), binding.key)
                    except dbos_error.DBOSQueueDeduplicatedError:
                        warn("jobs.enqueue_skipped", key=binding.key)
                        continue
                log("jobs.enqueued", key=binding.key)
            else:
                schedules.append(
                    ScheduleInput(
                        schedule_name=binding.key,
                        workflow_fn=job_tick,
                        schedule=binding.spec.schedule,
                        context=binding.key,
                    )
                )
                log("jobs.scheduled", key=binding.key, schedule=binding.spec.schedule)
        if schedules:
            DBOS.apply_schedules(schedules)

    async def tick(self, scheduled_time: datetime, key: str) -> None:
        """One fire of a job: fan out to the workspaces holding work, one queued execution per
        workspace under a (job, workspace) deduplication id held from enqueue to terminal — a
        workspace still running its previous execution absorbs the tick alone, never stalling its
        neighbors, and the first tick after it completes starts its next one.

        A key this process holds no binding for is skipped, not raised. `apply_schedules` upserts
        and never deletes, and the binding set is discovered per process at boot, so a schedule can
        name a job this process does not run — an extension uninstalled since the schedule was
        written, or a job only a newer peer registers. Deleting the schedule instead would let an
        older peer silently drop one a newer peer owns, since every process shares one schedule
        table and none can tell the versions apart."""
        if self._registered(key) is None:
            warn("jobs.tick_unregistered", key=key)
            return
        for workspace_id in await self.candidates(key):
            with SetEnqueueOptions(deduplication_id=f"{key}:{workspace_id}"):
                try:
                    await JOB_QUEUE.enqueue_async(
                        job_workflow, scheduled_time, key, str(workspace_id)
                    )
                except dbos_error.DBOSQueueDeduplicatedError:
                    warn("jobs.tick_skipped", key=key, workspace_id=str(workspace_id))

    async def candidates(self, key: str) -> tuple[UUID, ...]:
        return await self._binding(key).spec.candidates()

    async def fire(self, key: str, workspace_id: UUID) -> None:
        binding = self._binding(key)
        with ws(workspace_id):
            if binding.manifest is not None and binding.manifest.agents:
                await AgentProvisioning((binding.manifest,)).apply(workspace_id)
            invoker = None if self.invoker_factory is None else self.invoker_factory(workspace_id)
            context = context_for(
                binding.extension,
                binding.declared,
                self.index,
                self.embed,
                self.pages,
                self.blob,
                self.sandboxes,
                invoker,
                self.registry
                if binding.spec.needs_deploy_model
                else _background_registry(self.registry, self.background_model),
                key,
                probes=self.probes,
                member_context_read=binding.member_context_read,
                member_context_blob=self.blob,
            )
            try:
                await binding.spec.handler(context)
            except Exception as error:
                log_error("jobs.failed", job=key, error_class=type(error).__name__)
                raise

    def _registered(self, key: str) -> _Binding | None:
        return next((binding for binding in self.bindings if binding.key == key), None)

    def _binding(self, key: str) -> _Binding:
        """The binding for a key this process is running. A tick asks `_registered` first, because a
        schedule outliving its job is expected; reaching here without one means work was handed to a
        process that cannot do it, which is a fault."""
        binding = self._registered(key)
        if binding is None:
            raise RuntimeError(f"no job registered for key {key!r}")
        return binding


_firing: JobRunner | None = None


@DBOS.workflow(name=JOB_TICK_WORKFLOW_NAME)
async def job_tick(scheduled_time: datetime, key: str) -> None:
    runner = _firing
    if runner is None:
        raise RuntimeError("jobs not registered (JobRunner.launch runs in serve)")
    await runner.tick(scheduled_time, key)


@DBOS.workflow(name=JOB_WORKFLOW_NAME)
async def job_workflow(scheduled_time: datetime, key: str, workspace_id: str) -> None:
    runner = _firing
    if runner is None:
        raise RuntimeError("jobs not registered (JobRunner.launch runs in serve)")
    await runner.fire(key, UUID(workspace_id))
