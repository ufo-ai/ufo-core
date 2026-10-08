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
`job_workflow` records exactly one workspace's handler as a DBOS step through the extension's
scoped ExtensionContext, so recovery replays a completed handler instead of running it again and a
core job and an extension job ride the identical path. The jobs queue runs at
most `JOB_WORKER_CONCURRENCY` `job_workflow` executions per process, bounded backpressure in
Postgres, never a thread bloom; a recurring tick rides DBOS's internal queue — one candidate read
and one durable insert per candidate workspace, milliseconds — so it never waits behind a slow job
for a slot. A one-shot tick rides the jobs queue, as does the execution it fans out. The expensive
handler work is therefore confined to `ufo.serve.JOBS_FLEET`, while every image can drain every
application queue across a rollout or rollback."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOS, DBOSClient, EnqueueOptions, ScheduleInput, SetEnqueueOptions
from dbos import error as dbos_error
from pydantic import BaseModel

from ufo.blob import WorkspaceBlobStore
from ufo.db import failed_statement, owner_tx, workspace_tx
from ufo.harness.models.registry import ModelRegistry
from ufo.harness.o11y import emit_histogram, emit_metric, formatted_stack, log, log_error, warn
from ufo.harness.sandbox.conversation import ConversationSandbox
from ufo.product import PRODUCT_CENSUS_JOB, PRODUCT_CENSUS_SCHEDULE, ProductCensus
from ufo.runtime.access.grants import INDEX_REAP_EXTENSION, INDEX_REAP_KEY_PREFIX
from ufo.runtime.background_tasks import BACKGROUND_TASKS_JOB, BACKGROUND_TASKS_SCHEDULE
from ufo.runtime.billing.accounting import (
    JOB_DAY_ROLLUP_JOB,
    JOB_DAY_ROLLUP_SCHEDULE,
    LEDGER_SERVICE_BACKFILL_JOB,
    LEDGER_SERVICE_BACKFILL_SCHEDULE,
    UNGATED_LEDGER,
    JobDayRollup,
    Ledger,
    OffTurnSpendRefused,
    ServiceBackfill,
    SpendEvaluator,
    job_day_candidates,
    ledger_service_backfill_candidates,
)
from ufo.runtime.billing.spend import (
    ALLOW,
    NO_SPEND_GATES,
    RESUME_MOMENT,
    SpendGates,
    composed,
)
from ufo.runtime.candidates import WorkspaceCandidates, owner_candidates
from ufo.runtime.cloud import CloudApis
from ufo.runtime.ext.context import (
    CORE_EXTENSION,
    SPEND_REFUSAL_NOTICE_KEY,
    ConversationProbes,
    ExtensionContext,
    ScopedStore,
    TurnInvoker,
    agent_is_live,
    context_for,
    seated_member_workspaces,
    spend_refusal_notice_key,
)
from ufo.runtime.ext.manifest import (
    JOB_FAULT_MAX_CHARS,
    PAGE_CHANGE_CURSOR_KEY,
    HookContext,
    HookSpec,
    JobFault,
    JobSpec,
    Manifest,
    PageChangeBatch,
    minted_slots,
)
from ufo.runtime.gravatar import (
    GRAVATAR_JOB,
    GRAVATAR_SCHEDULE,
    GravatarPrefill,
    unpictured_member_workspaces,
)
from ufo.runtime.indexing import OWNER_KIND_PAGE, EmbedClient, IndexBackend, IndexScope
from ufo.runtime.kinds.provisioning import AgentProvisioning
from ufo.runtime.media.preview_renderer import PreviewRenderer
from ufo.runtime.seats import Seats
from ufo.runtime.signin_photo import (
    SIGNIN_PHOTO_JOB,
    SIGNIN_PHOTO_SCHEDULE,
    SigninPhotos,
    unfetched_signin_photo_workspaces,
)
from ufo.runtime.sources.sync import (
    SOURCE_SYNC_JOB,
    SOURCE_SYNC_SCHEDULE,
    PageChange,
    PageFeed,
    SyncDriver,
    page_cursor,
)
from ufo.runtime.turns.audience import SHARED_AUDIENCE
from ufo.runtime.turns.dispatch import dispatch_wait_ms
from ufo.runtime.turns.record import subagent_activity
from ufo.runtime.turns.subjects import MEMBER_SUBJECT_PREFIX
from ufo.runtime.workspace import ws, ws_current
from ufo.schema import tables
from ufo.schema.records import (
    DBOS_APP_VERSION,
    MEMBER_ADMISSION,
    PARKED,
    RUNNING,
    TURN_WORKFLOW_NAME,
    TerminalFrame,
    TurnAdmissionSource,
    TurnRuntimeConfig,
    TurnStatus,
    turn_queue_for,
)


class ResultDeliverer(Protocol):
    """The subagent hand-back sweep as this role sees it. The work is the turn loop's — it reads a
    finished child and posts its arrival — so it lives there, and the jobs role names and schedules
    it through this structural type rather than importing across the boundary."""

    async def run(self) -> None: ...

    async def candidate_workspaces(self) -> tuple[UUID, ...]: ...


class BackgroundTaskFollower(Protocol):
    """The detached-command sweep as this role sees it: it probes each conversation's task journal
    and posts what ended, so it lives with the turn loop and is named here structurally."""

    async def run(self) -> None: ...

    async def candidate_workspaces(self) -> tuple[UUID, ...]: ...


InvokerFactory = Callable[[UUID], TurnInvoker]

JOB_QUEUE_NAME = "jobs"
JOB_WORKFLOW_NAME = "job"
JOB_FAILED_METRIC = "job_failed_total"
JOB_TICK_WORKFLOW_NAME = "job_tick"
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
INDEX_REAP_JOB = "index_reap"
INDEX_REAP_SCHEDULE = "0 * * * * *"
SETTLED_ACTIVITY_JOB = "settled_activity"
SETTLED_ACTIVITY_SCHEDULE = "0 * * * * *"
SETTLED_ACTIVITY_BATCH = 200
CHANGE_LOG_PRUNE_JOB = "conversation_change_prune"
CHANGE_LOG_PRUNE_SCHEDULE = "0 * * * * *"
CHANGE_LOG_RETENTION = timedelta(hours=24)
INDEX_REAP_BATCH = 50
PAGE_CHANGE_BATCH = 50
PAGE_CHANGE_PARKED_KEY = "page_change_parked"
PAGE_CHANGE_REFUSED_KEY = "page_change_refused"
PAGE_CHANGE_BATCH_REFUSED_KEY = "page_change_batch_refused"
PAGE_CHANGE_PARK_STRIKES = 3
PAGE_CHANGE_PARK_MAX = 20
PAGE_CHANGE_PARK_RETRY_SECONDS = 3600
PAGE_CHANGE_NARROWED_METRIC = "page_change_narrowed_total"
PAGE_CHANGE_PARKED_METRIC = "page_change_parked_total"
PAGE_CHANGE_STALLED_METRIC = "page_change_stalled_total"
JOB_WORKER_CONCURRENCY = 8
QUEUED: TurnStatus = "queued"


def register_job_queue() -> None:
    """Declare the jobs queue in the system database, after `DBOS.launch` and off the event loop.
    Queue settings live in that table, and a tick enqueued on a name no process has declared stays
    ENQUEUED."""
    DBOS.register_queue(JOB_QUEUE_NAME, worker_concurrency=JOB_WORKER_CONCURRENCY)


@dataclass(frozen=True, slots=True)
class _DispatchTurn:
    id: UUID
    workspace_id: UUID
    conversation_id: UUID
    agent_id: UUID
    speaker_member_id: UUID | None
    admission_source: TurnAdmissionSource
    status: TurnStatus
    parent_turn_id: UUID | None
    created_at: datetime


@dataclass(frozen=True)
class TurnDispatcher:
    """Dispatch durable turn rows onto the capacity-claimed queues. QUEUED is an outbox state:
    admission stamps and offers the first turn immediately, the exit handoff offers each
    successor, and this bounded sweep recovers an unstamped or stale offer. Only the
    lowest-sequence QUEUED turn in a conversation with no running sibling is eligible, so one
    conversation runs one turn at a time and a later turn cannot overtake an earlier offer.
    A never-claimed
    QUEUED turn's DBOS workflow id is the turn id, making an ambiguous duplicate offer safe.

    PARKED rows share the same scanner and advisory dispatch stamp, but remain retry-time-, cap-,
    spend-gate-, and seat-gated: a timed provider park is invisible until `retry_at`, and a parked
    member turn stays held while its founder or any pending absorbed speaker holds no seat. A row
    that has ever been claimed — a PARKED one, or a QUEUED one a fold resumed from park — needs a
    fresh DBOS workflow id because the run that claimed it consumed its original id; the choice
    reads `running_attempt` from the stamping update itself, so a claim-park-requeue racing the
    sweep's scan cannot ride a spent id. The worker claim keeps a duplicate fresh-id offer safe,
    and clears the stamp for either state. A stamp
    set before an external enqueue and left behind by a process failure becomes eligible again
    after the grace window.

    `candidate_workspaces` is the only fleet-wide owner read. `run` reads and stamps only the bound
    workspace's rows, claims at most `dispatch_batch` rows, and offers only rows whose stale stamp
    it atomically replaces. Concurrent sweepers therefore cannot both make a fresh offer."""

    client: DBOSClient
    dispatch_batch: int = TURN_DISPATCH_BATCH_TURNS
    spend: SpendGates = NO_SPEND_GATES

    async def run(self) -> None:
        for turn in await self._dispatchable_turns():
            if turn.status == PARKED:
                async with workspace_tx() as connection:
                    member_id = turn.speaker_member_id
                    members = {member_id} if member_id is not None else set()
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
                    decision = composed(
                        await SpendEvaluator(turn.workspace_id, member_id, turn.agent_id).decide(
                            connection, 0
                        ),
                        await self.spend.admit(
                            connection,
                            RESUME_MOMENT,
                            turn.workspace_id,
                            agent_id=turn.agent_id,
                            turn_id=turn.id,
                        ),
                    )
                if not seated or decision.outcome != ALLOW:
                    continue
            await self._enqueue(turn)

    async def candidate_workspaces(self) -> tuple[UUID, ...]:
        now = datetime.now(UTC)
        async with owner_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.turn.c.workspace_id).where(self._eligible(now)).distinct()
                )
            ).all()
        return tuple(row.workspace_id for row in rows)

    async def _dispatchable_turns(self) -> tuple[_DispatchTurn, ...]:
        now = datetime.now(UTC)
        workspace_id = ws_current().workspace_id
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.id,
                        tables.turn.c.workspace_id,
                        tables.turn.c.conversation_id,
                        tables.turn.c.agent_id,
                        tables.turn.c.speaker_member_id,
                        tables.turn.c.admission_source,
                        tables.turn.c.status,
                        tables.turn.c.parent_turn_id,
                        tables.turn.c.created_at,
                    )
                    .where(tables.turn.c.workspace_id == workspace_id, self._eligible(now))
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
                r.speaker_member_id,
                r.admission_source,
                r.status,
                r.parent_turn_id,
                r.created_at,
            )
            for r in rows
        )

    async def _enqueue(self, turn: _DispatchTurn) -> None:
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=TURN_DISPATCH_GRACE_SECONDS)
        order_guard = self._first_in_status(turn.status)
        async with workspace_tx() as connection:
            claimed = (
                await connection.execute(
                    sa.update(tables.turn)
                    .values(dispatch_enqueued_at=sa.func.now(), updated_at=sa.func.now())
                    .where(
                        tables.turn.c.workspace_id == turn.workspace_id,
                        tables.turn.c.id == turn.id,
                        tables.turn.c.status == turn.status,
                        self._stale(cutoff),
                        self._retry_due(now),
                        order_guard,
                    )
                    .returning(tables.turn.c.id, tables.turn.c.running_attempt)
                )
            ).one_or_none()
        if claimed is None:
            return
        if turn.status == QUEUED:
            emit_histogram("turn_dispatch_wait_ms", dispatch_wait_ms(turn.created_at))
        options: EnqueueOptions = {
            "queue_name": turn_queue_for(turn.parent_turn_id, turn.admission_source),
            "workflow_name": TURN_WORKFLOW_NAME,
            "workflow_id": (
                str(turn.id)
                if turn.status == QUEUED and claimed.running_attempt is None
                else uuid4().hex
            ),
            "app_version": DBOS_APP_VERSION,
        }
        await self.client.enqueue_async(options, str(turn.workspace_id), str(turn.id))

    def _eligible(self, now: datetime) -> sa.ColumnElement[bool]:
        """The plain queue does not hold one turn per conversation; the exit handoff or a later
        sweep offers it once the sibling ends."""
        running = tables.turn.alias("running_sibling_turn")
        no_running_sibling = ~sa.exists(
            sa.select(running.c.id).where(
                running.c.workspace_id == tables.turn.c.workspace_id,
                running.c.conversation_id == tables.turn.c.conversation_id,
                running.c.status == RUNNING,
            )
        )
        return sa.and_(
            tables.turn.c.status.in_((QUEUED, PARKED)),
            self._stale(now - timedelta(seconds=TURN_DISPATCH_GRACE_SECONDS)),
            self._retry_due(now),
            no_running_sibling,
            sa.or_(
                sa.and_(tables.turn.c.status == QUEUED, self._first_in_status(QUEUED)),
                sa.and_(tables.turn.c.status == PARKED, self._first_in_status(PARKED)),
            ),
        )

    def _retry_due(self, now: datetime) -> sa.ColumnElement[bool]:
        return sa.or_(tables.turn.c.retry_at.is_(None), tables.turn.c.retry_at <= now)

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
    extension's name, the credential slots it declared and those it mints, the hook spec, and the
    discriminator that keeps two hooks in one extension independent — the handler's own `__name__`,
    since page_change handlers are plain module-level functions. `core_jobs` names one JobSpec
    `page_change:{extension}:{discriminator}` per consumer and `drive` rides a
    `{PAGE_CHANGE_CURSOR_KEY}:{discriminator}` cursor, so each drives as its own DBOS workflow off
    its own cursor — a backlogged or wedged consumer delays only itself, and an extension that
    registers two page_change hooks (the memory indexer and its fact deriver) never collides on
    JobSpec name or cursor key."""

    extension: str
    declared: frozenset[str]
    minted: frozenset[str]
    spec: HookSpec
    discriminator: str
    cloud_client: bool = False

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


class RefusedPage(BaseModel):
    """The page a consumer's handler last refused on its own and how many ticks running it has. A
    model that answers badly once, a provider that refuses for a minute — an external fault that
    passes — is gone by the next tick and the page derives from where it stands, so a refusal holds
    the cursor first and only a page that reaches PAGE_CHANGE_PARK_STRIKES is set aside."""

    cursor: str | None
    page_id: UUID
    strikes: int


class RefusedBatch(BaseModel):
    cursor: str | None
    strikes: int


class ParkedPage(BaseModel):
    """One page a `page_change` handler refused on its own, set aside so the pages behind it keep
    moving. `cursor` is the feed position it sits at — the page before it — and `page_id` the page a
    one-page read from there must return for that page to still be this one: a page changed since is
    further down the feed now, where the main line reaches it. `tried_at` paces the retry."""

    cursor: str | None
    page_id: UUID
    tried_at: datetime


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
    like the fact deriver's distillation pass reaches ctx.model. A batch a handler raises on is
    retried whole when the hook requires one delivery, then retried a page at a time; a page it
    refuses alone is parked rather than left in front of the cursor, so no one page holds a
    workspace's consumer.
    Batch-at-interval and fed only by the source pipeline, so it can never fire on the derived rows
    a handler writes.

    A selective cross-workspace sweep: the dispatcher names the candidate workspaces through
    `workspaces_with_changes` (one `owner_tx` read, pinned to no workspace and run as the owner role
    RLS policies exempt, taking only those whose high-water page lies beyond this consumer's cursor)
    and binds each, so `drive` runs that consumer's cursor loop scoped to the bound workspace — the
    page feed reads that workspace's pages, the cursor lives in that workspace's ScopedStore, so one
    consumer's per-minute workflow replays the fleet with each workspace resuming independently. A
    workspace with nothing changed since its cursor is never bound. On a per-tenant deploy
    `owner_tx` resolves to the single workspace, unchanged."""

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
    spend: SpendGates = NO_SPEND_GATES
    ledger: Ledger = UNGATED_LEDGER
    cloud: CloudApis | None = None

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
                        minted=minted_slots(manifest),
                        spec=spec,
                        discriminator=discriminator,
                        cloud_client=manifest.cloud_client,
                    )
                )
        return tuple(consumers)

    async def workspaces_with_changes(self, consumer: PageChangeConsumer) -> tuple[UUID, ...]:
        """The workspaces this consumer has actual pending work in — those whose newest page lies
        beyond the consumer's own stored cursor. One `owner_tx` read, pinned to no workspace and run
        as the owner role RLS policies exempt, takes each workspace's high-water page as a pair of
        correlated probes down the `page_feed` index (the maximum in the feed's `(revision, id)`
        order — one probe per workspace, never a scan of the page table) and each workspace's cursor
        for this consumer from `ext_store`; a workspace whose high-water page is at or before its
        cursor has nothing changed since it last drained and is never opened, while a workspace with
        no cursor yet (never driven) has every page pending. So a page-holding but change-free
        workspace runs no per-tick transaction. A workspace whose stored cursor fails to parse
        counts as pending rather than aborting this fleet-wide read — one workspace's unparseable
        cursor stays that workspace's `drive` failure, never blocking every other workspace's tick.
        A workspace the spend gates hold (`SpendGates.admitting`) is not a candidate however far its
        pages run past the cursor: the gates would refuse every call a consumer makes, and the
        cursor would stay put through a stall logged every tick. On a per-tenant deploy `owner_tx`
        resolves to the single workspace, unchanged."""
        cursor_key = f"{PAGE_CHANGE_CURSOR_KEY}:{consumer.discriminator}"
        of_workspace = tables.page.c.workspace_id == tables.workspace.c.id
        newest_first = (tables.page.c.revision.desc(), tables.page.c.uid.desc())
        newest_revision = (
            sa.select(tables.page.c.revision)
            .where(of_workspace)
            .order_by(*newest_first)
            .limit(1)
            .scalar_subquery()
        )
        newest_id = (
            sa.select(tables.page.c.uid)
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
                    ).where(self.spend.admitting(tables.workspace.c.id))
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

        A handler that requires one batch delivery gets bounded whole-batch retries before the
        runner narrows it. Other handlers narrow at once. A refusal the batch earns as a whole — a
        request over a provider's size cap — is gone once the pages arrive singly, and one the
        handler makes against a single page names the page. That page holds the cursor while it
        might be a fault that passes — a model
        answering badly, a provider refusing for a minute — and is parked once it has refused
        PAGE_CHANGE_PARK_STRIKES ticks running: recorded with the position it sits at, stepped over,
        and re-delivered on its own every hour until it lands, so the pages behind it move and the
        page itself is set aside rather than dropped — whatever refused it, fixed, indexes it on the
        next hour without anyone rewinding a cursor. Parking is bounded: with
        PAGE_CHANGE_PARK_MAX pages already aside the refusal is the consumer's rather than any
        page's, so the drive stops at its cursor and says which consumer stopped and where, and
        counts. The counter is what a monitor reads: a fault that passes shows up once or twice, and
        one that does not keeps the count at the tick rate until somebody looks."""
        context = self._context_for(consumer)
        parked = await self._retry_parked(consumer, context)
        cursor_key = f"{PAGE_CHANGE_CURSOR_KEY}:{consumer.discriminator}"
        stored = await context.store.get(cursor_key)
        if stored is not None and not isinstance(stored, str):
            raise ValueError("page cursor must be a string")
        cursor = stored
        narrowed_through: str | None = None
        while True:
            batch = await self.pages.pages_changed_since(
                cursor, 1 if narrowed_through is not None else PAGE_CHANGE_BATCH
            )
            if not batch.changes:
                return
            try:
                await consumer.spec.handler(
                    HookContext(ext=context, payload=PageChangeBatch(changes=batch.changes))
                )
            except Exception as error:
                if len(batch.changes) > 1:
                    if (
                        consumer.spec.page_change_failure_scope == "batch"
                        and await self._hold_batch(consumer, context, cursor, len(parked), error)
                    ):
                        raise
                    emit_metric(
                        PAGE_CHANGE_NARROWED_METRIC,
                        extension=consumer.extension,
                        discriminator=consumer.discriminator,
                    )
                    warn(
                        "jobs.page_change_narrowed",
                        workspace_id=str(ws_current().workspace_id),
                        extension=consumer.extension,
                        discriminator=consumer.discriminator,
                        cursor=cursor or "",
                        pages=len(batch.changes),
                        error_class=type(error).__name__,
                        stack=formatted_stack(error),
                    )
                    narrowed_through = batch.next_cursor
                    continue
                parked = await self._refuse(
                    consumer, context, cursor, batch.changes[0], parked, error
                )
            if not await context.store.put_if(cursor_key, batch.next_cursor, expected=cursor):
                return
            cursor = batch.next_cursor
            if cursor == narrowed_through:
                narrowed_through = None
            elif narrowed_through is None and len(batch.changes) < PAGE_CHANGE_BATCH:
                return

    async def _hold_batch(
        self,
        consumer: PageChangeConsumer,
        context: ExtensionContext,
        cursor: str | None,
        parked: int,
        error: Exception,
    ) -> bool:
        key = f"{PAGE_CHANGE_BATCH_REFUSED_KEY}:{consumer.discriminator}"
        stored = await context.store.get(key)
        held = None if stored is None else RefusedBatch.model_validate(stored)
        strikes = held.strikes + 1 if held is not None and held.cursor == cursor else 1
        if strikes >= PAGE_CHANGE_PARK_STRIKES:
            await context.store.delete(key)
            return False
        await context.store.put(
            key,
            RefusedBatch(cursor=cursor, strikes=strikes).model_dump(mode="json"),
        )
        self._report_stall(consumer, cursor, parked, strikes, error)
        return True

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
            minted=consumer.minted,
            spend=self.spend,
            ledger=self.ledger,
            cloud_client=consumer.cloud_client,
            cloud=self.cloud,
        )

    async def _retry_parked(
        self, consumer: PageChangeConsumer, context: ExtensionContext
    ) -> list[ParkedPage]:
        key = f"{PAGE_CHANGE_PARKED_KEY}:{consumer.discriminator}"
        stored = await context.store.get(key)
        if stored is None:
            return []
        if not isinstance(stored, list):
            raise ValueError("parked pages must be a list")
        parked = [ParkedPage.model_validate(entry) for entry in stored]
        due = datetime.now(UTC) - timedelta(seconds=PAGE_CHANGE_PARK_RETRY_SECONDS)
        kept: list[ParkedPage] = []
        for page in parked:
            if page.tried_at > due:
                kept.append(page)
                continue
            batch = await self.pages.pages_changed_since(page.cursor, 1)
            if not batch.changes or batch.changes[0].page_id != page.page_id:
                continue
            try:
                await consumer.spec.handler(
                    HookContext(ext=context, payload=PageChangeBatch(changes=batch.changes))
                )
            except Exception as error:
                self._report_park(consumer, batch.changes[0], error, len(parked))
                kept.append(page.model_copy(update={"tried_at": datetime.now(UTC)}))
        if kept != parked:
            await context.store.put(key, [page.model_dump(mode="json") for page in kept])
        return kept

    async def _refuse(
        self,
        consumer: PageChangeConsumer,
        context: ExtensionContext,
        cursor: str | None,
        change: PageChange,
        parked: list[ParkedPage],
        error: Exception,
    ) -> list[ParkedPage]:
        refused_key = f"{PAGE_CHANGE_REFUSED_KEY}:{consumer.discriminator}"
        last = await context.store.get(refused_key)
        held = None if last is None else RefusedPage.model_validate(last)
        strikes = (
            held.strikes + 1
            if held is not None and held.page_id == change.page_id and held.cursor == cursor
            else 1
        )
        already_parked = any(page.page_id == change.page_id for page in parked)
        if strikes < PAGE_CHANGE_PARK_STRIKES and not already_parked:
            await context.store.put(
                refused_key,
                RefusedPage(cursor=cursor, page_id=change.page_id, strikes=strikes).model_dump(
                    mode="json"
                ),
            )
            self._report_stall(consumer, cursor, len(parked), strikes, error)
            raise error
        if already_parked:
            return parked
        if len(parked) >= PAGE_CHANGE_PARK_MAX:
            self._report_stall(consumer, cursor, len(parked), strikes, error)
            raise error
        self._report_park(consumer, change, error, len(parked) + 1)
        parked = [
            *parked,
            ParkedPage(cursor=cursor, page_id=change.page_id, tried_at=datetime.now(UTC)),
        ]
        await context.store.put(
            f"{PAGE_CHANGE_PARKED_KEY}:{consumer.discriminator}",
            [page.model_dump(mode="json") for page in parked],
        )
        return parked

    def _report_stall(
        self,
        consumer: PageChangeConsumer,
        cursor: str | None,
        parked: int,
        strikes: int,
        error: Exception,
    ) -> None:
        emit_metric(
            PAGE_CHANGE_STALLED_METRIC,
            extension=consumer.extension,
            discriminator=consumer.discriminator,
        )
        log_error(
            "jobs.page_change_stalled",
            workspace_id=str(ws_current().workspace_id),
            extension=consumer.extension,
            discriminator=consumer.discriminator,
            cursor=cursor or "",
            parked=parked,
            strikes=strikes,
            error_class=type(error).__name__,
            stack=formatted_stack(error),
        )

    def _report_park(
        self, consumer: PageChangeConsumer, change: PageChange, error: Exception, parked: int
    ) -> None:
        emit_metric(
            PAGE_CHANGE_PARKED_METRIC,
            extension=consumer.extension,
            discriminator=consumer.discriminator,
        )
        log_error(
            "jobs.page_change_parked",
            workspace_id=str(ws_current().workspace_id),
            extension=consumer.extension,
            discriminator=consumer.discriminator,
            page_id=str(change.page_id),
            revision=change.revision,
            parked=parked,
            error_class=type(error).__name__,
            stack=formatted_stack(error),
        )


def _background_registry(
    registry: ModelRegistry | None, background_model: str | None
) -> ModelRegistry | None:
    """A job reads no cache and pays full price per token; a member turn keeps the deploy
    default."""
    if registry is None or background_model is None:
        return registry
    return replace(registry, auto_model=background_model)


def model_key_slots(registry: ModelRegistry | None) -> tuple[str, ...]:
    """Every key slot a model of the deploy keys from — what `SpendGates.admitting` hands each gate
    as `self_funded`, so a workspace holding its own key for any of them can be left out of a hold.
    A gate may admit, on that key, every turn the model serves: the agents' turns, and the
    source-trigger turns a page-change consumer opens. So the pages its sources feed stay work the
    gates let happen, whichever model the workspace chose to pay for itself."""
    if registry is None:
        return ()
    return tuple(sorted({spec.key_slot for spec in registry.specs.values() if spec.key_slot}))


async def reap_index_queue(store: ScopedStore, index: IndexBackend | None) -> None:
    """Drop the index chunks of the pages a disconnect deleted. Each queue row holds the page uids
    one disconnect left behind; the drain takes them `INDEX_REAP_BATCH` at a time and writes back
    what is left after each batch, so a tick that faults costs only the batch it was in — the row
    keeps the rest, the candidates read leaves the workspace due while any row remains, and the
    next tick resumes there. A scope delete is idempotent, so a batch replayed after a fault
    re-deletes scopes already empty. A row goes once its last uid does."""
    if index is None:
        return
    for key, value in await store.list(INDEX_REAP_KEY_PREFIX):
        queued = value if isinstance(value, list) else []
        pending = [uid for uid in queued if isinstance(uid, str)]
        while pending:
            batch, pending = pending[:INDEX_REAP_BATCH], pending[INDEX_REAP_BATCH:]
            for page_uid in batch:
                await index.delete(IndexScope(OWNER_KIND_PAGE, page_uid))
            if pending:
                await store.put(key, list(pending))
        await store.delete(key)


UNSETTLED_ACTIVITY = (
    tables.turn.c.parent_turn_id.is_not(None),
    tables.turn.c.terminal.is_not(None),
    tables.turn.c.terminal["activity"].as_string().is_(None),
)


def unsettled_activity_workspaces() -> WorkspaceCandidates:
    return owner_candidates(
        lambda: sa.select(tables.turn.c.workspace_id.distinct()).where(*UNSETTLED_ACTIVITY)
    )


async def settle_subagent_activity(context: ExtensionContext) -> None:
    """Write onto each settled spawned turn the work its transcript records, for the turns that
    settled before the engine wrote it at commit. A batch at a time, oldest first; a child whose
    transcript is gone takes an empty record, so the key is always written and the row never
    comes back."""
    assert context.corpus is not None
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.turn.c.id, tables.turn.c.conversation_id, tables.turn.c.terminal)
                .where(tables.turn.c.workspace_id == ws_current().workspace_id, *UNSETTLED_ACTIVITY)
                .order_by(tables.turn.c.updated_at, tables.turn.c.id)
                .limit(SETTLED_ACTIVITY_BATCH)
            )
        ).all()
    if not rows:
        return
    held = {
        trajectory.conversation_id: trajectory.messages
        for trajectory in await context.corpus.conversations(
            tuple({row.conversation_id for row in rows})
        )
    }
    async with workspace_tx() as connection:
        for row in rows:
            frame = TerminalFrame.model_validate(row.terminal)
            settled = frame.model_copy(
                update={"activity": subagent_activity(held.get(row.conversation_id, ()))}
            )
            await connection.execute(
                sa.update(tables.turn)
                .values(terminal=settled.model_dump(mode="json"))
                .where(tables.turn.c.id == row.id)
            )


def _stale_changes() -> sa.ColumnElement[bool]:
    return tables.conversation_change_log.c.created_at < datetime.now(UTC) - CHANGE_LOG_RETENTION


def stale_change_workspaces() -> WorkspaceCandidates:
    return owner_candidates(
        lambda: sa.select(tables.conversation_change_log.c.workspace_id.distinct()).where(
            _stale_changes()
        )
    )


async def prune_conversation_changes(context: ExtensionContext) -> None:
    """Drop change-log rows older than the retention a reader's cursor can still continue from.
    A cursor older than the oldest row left is refused, and that reader reads page one again."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(tables.conversation_change_log).where(
                tables.conversation_change_log.c.workspace_id == ws_current().workspace_id,
                _stale_changes(),
            )
        )


async def _backfill_ledger_service(context: ExtensionContext) -> None:
    async with workspace_tx() as connection:
        await ServiceBackfill(ws_current().workspace_id).roll(connection)


def core_jobs(
    sync_driver: SyncDriver,
    turn_dispatcher: TurnDispatcher,
    page_change_runner: PageChangeRunner,
    delivery_sweep: ResultDeliverer,
    background_tasks: BackgroundTaskFollower,
    preview_renderer: PreviewRenderer | None,
    census: ProductCensus,
) -> tuple[JobSpec, ...]:
    """The jobs a deploy always runs, before any extension's — all core because the source pipeline,
    spend enforcement, the page-change fan-out, and the subagent loop are core. The sync driver
    polls each source and lands its pages; the page-change runner contributes one
    `page_change:<ext>:<hook>` job per registered consumer, each replaying those pages to that
    consumer's hook off its own cursor as its own workflow (the memory page indexer and fact deriver
    among them); the turn dispatcher recovers queued outbox rows and re-admits parked turns their
    caps now allow; the delivery sweep hands back the delegated children whose own execution could
    not — a cancelled one above all, whose terminal is committed from outside it; the background
    task sweep follows the commands a turn detached, keeping their sandbox awake and posting each
    one's end into its conversation; the product census
    counts each workspace's place in the funnel, and is core because it reads nearly the whole core
    schema to do it — an SDK seam wide enough to count `member`, `agent`, `connector_grant`,
    `connection`, `credential`, `surface_installation`, `surface_address` and `turn` would be a
    wider public surface than the one consumer counting them is worth, and a stage an extension's
    own rows mark arrives as its `Manifest.census` contribution.
    None fires on its own writes. (Memory-item indexing stays the memory extension's own job; page
    derivation is a page_change hook this runner drives. Reclaiming a container is the carrier's
    own business, never core's — the workspace lives inside the sandbox, so only a carrier knows
    whether dropping its container takes the workspace with it.)"""

    async def _sync_sources(context: ExtensionContext) -> None:
        await sync_driver.run()

    async def _dispatch_turns(context: ExtensionContext) -> None:
        await turn_dispatcher.run()

    async def _deliver_results(context: ExtensionContext) -> None:
        await delivery_sweep.run()

    async def _follow_background_tasks(context: ExtensionContext) -> None:
        await background_tasks.run()

    async def _census_product(context: ExtensionContext) -> None:
        await census.count()

    async def _roll_job_days(context: ExtensionContext) -> None:
        async with workspace_tx() as connection:
            await JobDayRollup(ws_current().workspace_id).roll(connection, datetime.now(UTC))

    async def _reap_index(context: ExtensionContext) -> None:
        await reap_index_queue(context.store, context.index)

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

    def _reap_candidates() -> WorkspaceCandidates:
        due = sa.select(tables.ext_store.c.workspace_id.distinct()).where(
            tables.ext_store.c.extension == INDEX_REAP_EXTENSION,
            tables.ext_store.c.key.startswith(INDEX_REAP_KEY_PREFIX, autoescape=True),
        )
        return owner_candidates(lambda: due)

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
        JobSpec(
            name=BACKGROUND_TASKS_JOB,
            schedule=BACKGROUND_TASKS_SCHEDULE,
            handler=_follow_background_tasks,
            candidates=background_tasks.candidate_workspaces,
        ),
        JobSpec(
            name=INDEX_REAP_JOB,
            schedule=INDEX_REAP_SCHEDULE,
            handler=_reap_index,
            candidates=_reap_candidates(),
        ),
        JobSpec(
            name=SETTLED_ACTIVITY_JOB,
            schedule=SETTLED_ACTIVITY_SCHEDULE,
            handler=settle_subagent_activity,
            candidates=unsettled_activity_workspaces(),
        ),
        JobSpec(
            name=CHANGE_LOG_PRUNE_JOB,
            schedule=CHANGE_LOG_PRUNE_SCHEDULE,
            handler=prune_conversation_changes,
            candidates=stale_change_workspaces(),
        ),
        JobSpec(
            name=JOB_DAY_ROLLUP_JOB,
            schedule=JOB_DAY_ROLLUP_SCHEDULE,
            handler=_roll_job_days,
            candidates=job_day_candidates(),
        ),
        JobSpec(
            name=LEDGER_SERVICE_BACKFILL_JOB,
            schedule=LEDGER_SERVICE_BACKFILL_SCHEDULE,
            handler=_backfill_ledger_service,
            candidates=ledger_service_backfill_candidates(),
        ),
        JobSpec(
            name=PRODUCT_CENSUS_JOB,
            schedule=PRODUCT_CENSUS_SCHEDULE,
            handler=_census_product,
            candidates=seated_member_workspaces(),
        ),
        JobSpec(
            name=GRAVATAR_JOB,
            schedule=GRAVATAR_SCHEDULE,
            handler=GravatarPrefill().run,
            candidates=unpictured_member_workspaces(),
        ),
        JobSpec(
            name=SIGNIN_PHOTO_JOB,
            schedule=SIGNIN_PHOTO_SCHEDULE,
            handler=SigninPhotos().run,
            candidates=unfetched_signin_photo_workspaces(),
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
    minted: frozenset[str]
    spec: JobSpec
    member_context_read: bool = False
    cloud_client: bool = False
    surfaces: frozenset[str] = frozenset()
    addressed_surfaces: frozenset[str] = frozenset()


def bindings_from(
    manifests: tuple[Manifest, ...],
    core_jobs: tuple[JobSpec, ...],
    disabled: frozenset[str] = frozenset(),
) -> tuple[_Binding, ...]:
    """Core jobs live in the `core` namespace with no declared slots; each extension's jobs live in
    its own namespace with exactly the credential slots and surfaces that extension declared."""
    bindings = [
        _Binding(
            key=f"{CORE_EXTENSION}:{spec.name}",
            extension=CORE_EXTENSION,
            declared=frozenset(),
            minted=frozenset(),
            spec=spec,
        )
        for spec in core_jobs
    ]
    for manifest in manifests:
        declared = frozenset(slot.name for slot in manifest.credentials)
        surfaces = frozenset(surface.name for surface in manifest.surfaces)
        addressed = frozenset(surface.name for surface in manifest.surfaces if surface.addressed)
        bindings.extend(
            _Binding(
                key=f"{manifest.name}:{spec.name}",
                extension=manifest.name,
                declared=declared,
                minted=minted_slots(manifest),
                spec=spec,
                member_context_read=manifest.member_context_read,
                cloud_client=manifest.cloud_client,
                surfaces=surfaces,
                addressed_surfaces=addressed,
            )
            for spec in manifest.jobs
        )
    known = frozenset(binding.key for binding in bindings)
    if unknown := sorted(disabled - known):
        raise ValueError(f"disabled jobs are not registered: {', '.join(unknown)}")
    return tuple(binding for binding in bindings if binding.key not in disabled)


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
    manifests: tuple[Manifest, ...]
    invoker_factory: InvokerFactory | None = None
    index: IndexBackend | None = None
    embed: EmbedClient | None = None
    pages: PageFeed | None = None
    blob: WorkspaceBlobStore | None = None
    sandboxes: ConversationSandbox | None = None
    registry: ModelRegistry | None = None
    probes: ConversationProbes | None = None
    background_model: str | None = None
    spend: SpendGates = NO_SPEND_GATES
    ledger: Ledger = UNGATED_LEDGER
    cloud: CloudApis | None = None
    public_base_url: str | None = None
    home_surface: str | None = None
    provisioned_workspaces: set[UUID] = field(default_factory=set, compare=False, repr=False)

    def launch(self) -> None:
        global _firing
        _firing = self
        schedules: list[ScheduleInput] = []
        for binding in self.bindings:
            if binding.spec.schedule is None:
                with SetEnqueueOptions(deduplication_id=binding.key):
                    try:
                        DBOS.enqueue_workflow(
                            JOB_QUEUE_NAME, job_tick, datetime.now(UTC), binding.key
                        )
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
                    await DBOS.enqueue_workflow_async(
                        JOB_QUEUE_NAME, job_workflow, scheduled_time, key, str(workspace_id)
                    )
                except dbos_error.DBOSQueueDeduplicatedError:
                    warn("jobs.tick_skipped", key=key, workspace_id=str(workspace_id))

    async def candidates(self, key: str) -> tuple[UUID, ...]:
        spec = self._binding(key).spec
        named = await spec.candidates()
        if not spec.spends or not named or not self.spend.gates:
            return named
        async with owner_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.workspace.c.id).where(
                        tables.workspace.c.id.in_(named),
                        self.spend.admitting(tables.workspace.c.id),
                    )
                )
            ).all()
        solvent = {row.id for row in rows}
        return tuple(workspace_id for workspace_id in named if workspace_id in solvent)

    async def fire(self, key: str, workspace_id: UUID) -> None:
        binding = self._binding(key)
        with ws(workspace_id):
            if workspace_id not in self.provisioned_workspaces:
                await AgentProvisioning(self.manifests).apply(workspace_id)
                self.provisioned_workspaces.add(workspace_id)
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
                surfaces=binding.surfaces,
                addressed_surfaces=binding.addressed_surfaces,
                probes=self.probes,
                member_context_read=binding.member_context_read,
                member_context_blob=self.blob,
                public_base_url=self.public_base_url,
                home_surface=self.home_surface,
                minted=binding.minted,
                spend=self.spend,
                ledger=self.ledger,
                cloud_client=binding.cloud_client,
                cloud=self.cloud,
            )
            try:
                await binding.spec.handler(context)
            except OffTurnSpendRefused as refusal:
                await self._deferred_on_spend(key, workspace_id, refusal)
            except Exception as error:
                error_class = type(error).__name__
                match error:
                    case JobFault():
                        fault: str | None = error.reason[:JOB_FAULT_MAX_CHARS]
                    case _:
                        fault = None
                log_error(
                    "jobs.failed",
                    job=key,
                    error_class=error_class,
                    fault=fault,
                    stack=formatted_stack(error),
                    **failed_statement(error),
                )
                emit_metric(JOB_FAILED_METRIC, job=key, error_class=error_class)
                raise

    async def _deferred_on_spend(
        self, key: str, workspace_id: UUID, refusal: OffTurnSpendRefused
    ) -> None:
        """The off-turn model seam deletes this model's mark on the first pass its gates allow; the
        mark's compare-and-swap makes jobs refused in one pass open one notice."""
        log("jobs.deferred_on_spend", job=key, outcome=refusal.outcome, model=refusal.model)
        store = ScopedStore(extension=CORE_EXTENSION)
        mark = spend_refusal_notice_key(refusal.model)
        told = await store.get(mark)
        if told == refusal.outcome:
            return
        if not await store.put_if(mark, refusal.outcome, expected=told):
            return
        if await self._tell_the_member(key, workspace_id, str(refusal)) is None:
            await store.delete(mark)

    async def _tell_the_member(self, key: str, workspace_id: UUID, refusal: str) -> UUID | None:
        """The schema sets `conversation.member_id` for a member's own audience alone. A fixed
        idempotency key would collapse every later notice onto the first turn."""
        if self.invoker_factory is None:
            return None
        async with workspace_tx() as connection:
            told = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.conversation_id,
                        tables.turn.c.agent_id,
                        tables.turn.c.speaker_member_id,
                    )
                    .select_from(
                        tables.turn.join(
                            tables.member,
                            tables.member.c.id == tables.turn.c.speaker_member_id,
                        ).join(
                            tables.conversation,
                            tables.conversation.c.id == tables.turn.c.conversation_id,
                        )
                    )
                    .where(
                        tables.turn.c.workspace_id == workspace_id,
                        tables.turn.c.admission_source == MEMBER_ADMISSION,
                        tables.member.c.workspace_id == workspace_id,
                        tables.member.c.seated_at.is_not(None),
                        tables.conversation.c.workspace_id == workspace_id,
                        sa.or_(
                            tables.conversation.c.audience == str(SHARED_AUDIENCE),
                            sa.and_(
                                tables.conversation.c.audience.startswith(MEMBER_SUBJECT_PREFIX),
                                tables.conversation.c.member_id == tables.turn.c.speaker_member_id,
                            ),
                        ),
                        agent_is_live(tables.turn.c.workspace_id, tables.turn.c.agent_id),
                    )
                    .order_by(tables.turn.c.created_at.desc(), tables.turn.c.id.desc())
                    .limit(1)
                )
            ).one_or_none()
        if told is None:
            warn("jobs.spend_refusal_untold", job=key, workspace_id=str(workspace_id))
            return None
        return await self.invoker_factory(workspace_id).invoke(
            told.conversation_id,
            told.agent_id,
            f"The {key} background job asked for a model call and this workspace's spend gates "
            f"refused it: {refusal}\n\nTell the member, once, that this work is paused, what the "
            "refusal above says to do about it, and that the job writes again on its next pass "
            "once the spend is allowed. Do nothing else.",
            f"{SPEND_REFUSAL_NOTICE_KEY}:{uuid4().hex}",
            as_scheduled=True,
            runtime_config=TurnRuntimeConfig(internet_access=False),
        )

    def _registered(self, key: str) -> _Binding | None:
        return next((binding for binding in self.bindings if binding.key == key), None)

    def _binding(self, key: str) -> _Binding:
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
    await job_fire(key, workspace_id)


@DBOS.step(preemptible=True)
async def job_fire(key: str, workspace_id: str) -> None:
    runner = _firing
    if runner is None:
        raise RuntimeError("jobs not registered (JobRunner.launch runs in serve)")
    await runner.fire(key, UUID(workspace_id))
