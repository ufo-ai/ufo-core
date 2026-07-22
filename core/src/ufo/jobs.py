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
from typing import Protocol
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOS, DBOSClient, EnqueueOptions, Queue, ScheduleInput

from ufo.accounting import ALLOW, SpendEvaluator
from ufo.blob import BlobStore
from ufo.candidates import WorkspaceCandidates
from ufo.db import owner_tx, workspace_tx
from ufo.ext.context import ExtensionContext, TurnInvoker, context_for
from ufo.ext.manifest import HookContext, HookSpec, JobSpec, Manifest, PageChangeBatch
from ufo.indexing import EmbedClient, IndexBackend
from ufo.models.registry import ModelRegistry
from ufo.o11y import log, log_error
from ufo.sandbox.session import Carrier, SandboxHandle, sandbox_handle_id
from ufo.scheduling import ScheduleInvoker
from ufo.schema import tables
from ufo.schema.records import (
    DBOS_APP_VERSION,
    NON_TERMINAL_STATUSES,
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
)
from ufo.workspace import ws, ws_current


class JobInvoker(TurnInvoker, ScheduleInvoker, Protocol):
    pass


InvokerFactory = Callable[[UUID], JobInvoker]

JOB_QUEUE_NAME = "jobs"
JOB_WORKFLOW_NAME = "job"
CORE_EXTENSION = "core"
TURN_DISPATCH_JOB = "turn_dispatch"
TURN_DISPATCH_SCHEDULE = "0 * * * * *"
TURN_DISPATCH_GRACE_SECONDS = 300
TURN_DISPATCH_BATCH_TURNS = 100
SANDBOX_REAP_JOB = "sandbox_reap"
SANDBOX_REAP_SCHEDULE = "0 */10 * * * *"
SANDBOX_IDLE_TTL_SECONDS = 1800
PAGE_CHANGE_JOB = "page_change"
PAGE_CHANGE_SCHEDULE = "0 * * * * *"
PAGE_CHANGE_CURSOR_KEY = "page_change_cursor"
PAGE_CHANGE_BATCH = 50
JOB_QUEUE = Queue(JOB_QUEUE_NAME)
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

    PARKED rows share the same scanner and advisory dispatch stamp, but remain spend- and
    seat-gated: a parked turn stays held while its gate member — the speaker, or the member a
    scheduled fire acts on behalf of (its schedule's creator) — holds no seat, and resumes when
    one is granted again. A row
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

    async def run(self) -> None:
        for turn in await self._dispatchable_turns():
            if turn.status == PARKED:
                async with workspace_tx() as connection:
                    gate = gate_member(
                        turn.speaker_member_id,
                        turn.admission_source,
                        turn.on_behalf_of_member_id,
                    )
                    seated = gate is None or await Seats(turn.workspace_id).admits(connection, gate)
                    decision = await SpendEvaluator(
                        turn.workspace_id, turn.member_id, turn.agent_id
                    ).decide(connection, 0)
                if not seated or decision.outcome != ALLOW:
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

    `run` operates on the bound workspace alone: the dispatcher names the candidate workspaces
    through `candidate_workspaces` and binds each, so the in-flight re-check and the row clear are
    scoped exactly as that workspace's turn would scope them. `candidate_workspaces` is the one
    `owner_tx` read (the RLS-bypass path) naming only the workspaces holding an idle sandbox, so a
    workspace with none is never bound. On a per-tenant deploy `owner_tx` resolves to the single
    workspace, unchanged."""

    carrier: Carrier
    backend: str

    async def run(self) -> None:
        for conversation_id, stored in await self._idle_sandboxes():
            container_id = sandbox_handle_id(self.backend, stored)
            if container_id is None:
                continue
            if await self._now_active(conversation_id):
                continue
            await self.carrier.destroy(
                SandboxHandle(conversation_id=conversation_id, container_id=container_id)
            )
            await self._clear(conversation_id)

    async def candidate_workspaces(self) -> tuple[UUID, ...]:
        """The workspaces holding a conversation whose sandbox is idle past the TTL — one distinct
        `workspace_id` per such workspace, read in one `owner_tx` (RLS bypass). Anti-joins driven
        from the handle-carrying conversations (a partial index names them), each probing that
        conversation's turns by index — the sweep's cost follows the live sandboxes, never the
        settled turn history. A workspace with no idle sandbox is never bound, so no transaction
        runs against it on the sweep."""
        cutoff = datetime.now(UTC) - timedelta(seconds=SANDBOX_IDLE_TTL_SECONDS)
        async with owner_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.conversation.c.workspace_id)
                    .where(
                        tables.conversation.c.sandbox_handle.is_not(None),
                        ~self._active_since(cutoff),
                    )
                    .distinct()
                )
            ).all()
        return tuple(row.workspace_id for row in rows)

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

    async def _idle_sandboxes(self) -> tuple[tuple[UUID, str], ...]:
        """The bound workspace's conversations carrying a persisted sandbox handle with no turn in
        flight and none touched within the TTL — the durable handle, not an in-process map, is the
        set the reaper reclaims from, so a sandbox a prior process created is in scope, and a
        handle whose turns are all gone is an orphan reclaimed the same way. Read through RLS, so
        the sweep sees only the workspace the dispatcher bound."""
        cutoff = datetime.now(UTC) - timedelta(seconds=SANDBOX_IDLE_TTL_SECONDS)
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.conversation.c.id,
                        tables.conversation.c.sandbox_handle,
                    ).where(
                        tables.conversation.c.sandbox_handle.is_not(None),
                        ~self._active_since(cutoff),
                    )
                )
            ).all()
        return tuple((row.id, row.sandbox_handle) for row in rows)

    def _active_since(self, cutoff: datetime) -> sa.ColumnElement[bool]:
        """The conversation has a turn in flight or one touched at or after `cutoff` — the busy
        signal both the fleet-wide candidate read and the bound workspace's sweep negate, probing
        each candidate conversation's turns through the `(conversation_id, updated_at)` index."""
        return sa.exists(
            sa.select(tables.turn.c.id).where(
                tables.turn.c.conversation_id == tables.conversation.c.id,
                sa.or_(
                    tables.turn.c.status.in_(NON_TERMINAL_STATUSES),
                    tables.turn.c.updated_at >= cutoff,
                ),
            )
        )


def _page_beyond_cursor(updated_at: datetime, page_id: UUID, cursor: object) -> bool:
    """Whether a page at `(updated_at, page_id)` lies past a `page_change` cursor — the same total
    order (`{changed_at.isoformat()}|{page_id}`) `CorePageFeed` replays in, so the candidate query
    and the feed agree on what "changed since" means. A cursor that is not a stored string (the
    consumer never drained this workspace) leaves every page pending. Both moments are read as UTC —
    a naive timestamp (SQLite) is the UTC wall-clock it was written as."""
    if not isinstance(cursor, str):
        return True
    stamp_str, boundary_str = cursor.split("|", 1)
    stamp = datetime.fromisoformat(stamp_str)
    stamp = stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)
    changed_at = updated_at if updated_at.tzinfo is not None else updated_at.replace(tzinfo=UTC)
    return changed_at > stamp or (changed_at == stamp and page_id > UUID(boundary_str))


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
    blob: BlobStore | None = None
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

    async def workspaces_with_changes(self, consumer: PageChangeConsumer) -> tuple[UUID, ...]:
        """The workspaces this consumer has actual pending work in — those whose newest page lies
        beyond the consumer's own stored cursor. One `owner_tx` read (RLS bypass) takes each
        workspace's high-water page as a pair of correlated probes down the `page_feed` index (the
        maximum in the feed's `(updated_at, id)` order — one probe per workspace, never a scan of
        the page table) and each workspace's cursor for this consumer from `ext_store`; a workspace
        whose high-water page is at or before its cursor has nothing changed since it last drained
        and is never opened, while a workspace with no cursor yet (never driven) has every page
        pending. So a page-holding but change-free workspace runs no per-tick transaction. On a
        per-tenant deploy `owner_tx` resolves to the single workspace, unchanged."""
        cursor_key = f"{PAGE_CHANGE_CURSOR_KEY}:{consumer.discriminator}"
        of_workspace = tables.page.c.workspace_id == tables.workspace.c.id
        newest_first = (tables.page.c.updated_at.desc(), tables.page.c.id.desc())
        newest_at = (
            sa.select(tables.page.c.updated_at)
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
                        newest_at.label("updated_at"),
                        newest_id.label("id"),
                    )
                )
            ).all()
        cursors = {row.workspace_id: row.value for row in cursor_rows}
        pending: list[UUID] = []
        for row in page_rows:
            if row.updated_at is None:
                continue
            if _page_beyond_cursor(row.updated_at, row.id, cursors.get(row.workspace_id)):
                pending.append(row.workspace_id)
        return tuple(pending)

    async def drive(self, consumer: PageChangeConsumer) -> None:
        """Replay the workspace's pages changed since this consumer's cursor to its handler and
        advance the cursor — the dispatcher binds the workspace, so this runs scoped to it and never
        enumerates the fleet itself."""
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
        invoker = (
            None
            if self.invoker_factory is None
            else self.invoker_factory(ws_current().workspace_id)
        )
        return context_for(
            extension,
            declared,
            self.index,
            self.embed,
            self.pages,
            self.blob,
            invoker,
            self.registry,
        )


def core_jobs(
    sync_driver: SyncDriver,
    turn_dispatcher: TurnDispatcher,
    reaper: SandboxReaper,
    page_change_runner: PageChangeRunner,
) -> tuple[JobSpec, ...]:
    """The jobs a deploy always runs, before any extension's — all core because the source pipeline,
    spend enforcement, sandbox lifecycle, and the page-change fan-out are core. The sync driver
    polls each source and lands its pages; the page-change runner contributes one
    `page_change:<ext>:<hook>` job per registered consumer, each replaying those pages to that
    consumer's hook off its own cursor as its own workflow (the memory page indexer, the memory
    fact deriver, and the graph extractor among them); the turn dispatcher recovers queued outbox
    rows and re-admits parked turns their caps now allow; the sandbox
    reaper destroys the disposable container behind each idle conversation through the carrier seam.
    None fires on its own writes. (Memory-item indexing stays the memory extension's own job; page
    derivation is a page_change hook this runner drives.)"""

    async def _sync_sources(context: ExtensionContext) -> None:
        await sync_driver.run()

    async def _dispatch_turns(context: ExtensionContext) -> None:
        await turn_dispatcher.run()

    async def _reap_sandboxes(context: ExtensionContext) -> None:
        await reaper.run()

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
            name=f"{PAGE_CHANGE_JOB}:{consumer.extension}:{consumer.discriminator}",
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
            name=SANDBOX_REAP_JOB,
            schedule=SANDBOX_REAP_SCHEDULE,
            handler=_reap_sandboxes,
            candidates=reaper.candidate_workspaces,
        ),
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
    """Boot registration for the deploy: publish the firing table, then register each cron job and
    enqueue each one-shot. `fire` is the per-execution dispatch the durable workflow calls; it is
    the sole path a handler runs on: it names the job's candidate workspaces (one `owner_tx` read
    returning only the ids with work) and, for each, opens `with ws(id)` and runs the handler
    scoped to it. There is no branch that runs a handler outside a bound workspace — an empty
    candidate set runs the handler zero times, and every core and extension job rides the identical
    fan-and-bind, so an unbound handler call cannot exist. On a per-tenant deploy the candidate read
    resolves to the single workspace, unchanged."""

    bindings: tuple[_Binding, ...]
    invoker_factory: InvokerFactory | None = None
    index: IndexBackend | None = None
    embed: EmbedClient | None = None
    pages: PageFeed | None = None
    blob: BlobStore | None = None
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
        for workspace_id in await binding.spec.candidates():
            with ws(workspace_id):
                invoker = (
                    None if self.invoker_factory is None else self.invoker_factory(workspace_id)
                )
                context = context_for(
                    binding.extension,
                    binding.declared,
                    self.index,
                    self.embed,
                    self.pages,
                    self.blob,
                    invoker,
                    self.registry,
                    schedule_invoker=invoker,
                )
                try:
                    await binding.spec.handler(context)
                except Exception as error:
                    log_error("jobs.failed", job=key, error_class=type(error).__name__)
                    raise


_firing: JobRunner | None = None


@DBOS.workflow(name=JOB_WORKFLOW_NAME)
async def job_workflow(scheduled_time: datetime, key: str) -> None:
    runner = _firing
    if runner is None:
        raise RuntimeError("jobs not registered (JobRunner.launch runs in serve)")
    await runner.fire(key)
